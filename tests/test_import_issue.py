"""Tests for the PDF URL -> OCR -> SQLite import wrapper."""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import import_issue as imp  # noqa: E402


class ImportIssueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf_bytes = b"%PDF-1.4\nfixture\n"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_filename_and_output_dir(self):
        self.assertEqual(
            imp.filename_from_url(
                "https://images.bo.dsaj.gov.mo//bo/ii/2026/33/"
                "anotariais-33-2026.pdf"
            ),
            "anotariais-33-2026.pdf",
        )
        self.assertEqual(
            imp.output_dir_for(Path("input/anotariais-33-2026.pdf")),
            imp.ROOT / "outputs" / "runs" / "issue33_2026",
        )
        self.assertEqual(
            imp.output_dir_for(Path("input/custom.pdf")),
            imp.ROOT / "outputs" / "runs" / "custom",
        )
        with self.assertRaises(ValueError):
            imp.filename_from_url("https://example.test/not-a-pdf.txt")

    def test_local_pdf_reuse_and_overwrite(self):
        source = self.root / "source.pdf"
        source.write_bytes(self.pdf_bytes)
        input_dir = self.root / "input"
        first = imp.fetch_pdf(str(source), input_dir)
        self.assertTrue(Path(first["path"]).exists())
        self.assertEqual(first["sha256"], imp.sha256_file(source))

        second = imp.fetch_pdf(str(source), input_dir)
        self.assertTrue(second["reused"])

        source.write_bytes(b"%PDF-1.4\nchanged\n")
        with self.assertRaises(FileExistsError):
            imp.fetch_pdf(str(source), input_dir)
        third = imp.fetch_pdf(str(source), input_dir, overwrite_pdf=True)
        self.assertEqual(
            imp.sha256_file(Path(third["path"])),
            imp.sha256_file(source),
        )

    def test_http_download(self):
        serve_dir = self.root / "www"
        serve_dir.mkdir()
        (serve_dir / "anotariais-33-2026.pdf").write_bytes(self.pdf_bytes)
        handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
            *args, directory=str(serve_dir), **kwargs
        )
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = imp.fetch_pdf(
                    f"http://127.0.0.1:{port}/anotariais-33-2026.pdf",
                    self.root / "input",
                )
            finally:
                server.shutdown()
                thread.join(timeout=2)
        path = Path(result["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), self.pdf_bytes)
        self.assertEqual(result["sha256"], imp.sha256_file(path))

    def test_non_pdf_download_is_rejected(self):
        source = self.root / "bad.pdf"
        source.write_bytes(b"not a pdf")
        with self.assertRaises(ValueError):
            imp.fetch_pdf(str(source), self.root / "input")

    def test_v5_path_semantics_are_preserved(self):
        original_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(
                imp.resolve_v5_db_path(Path("data/macau_balancete.sqlite")),
                (self.root / "data" / "macau_balancete.sqlite").resolve(),
            )
            args = argparse.Namespace(outdir=Path("custom_out"))
            self.assertEqual(
                imp.resolve_run_outdir(args, Path("input/anotariais-33-2026.pdf")),
                (self.root / "custom_out").resolve(),
            )
        finally:
            os.chdir(original_cwd)

        args = argparse.Namespace(outdir=None)
        self.assertEqual(
            imp.resolve_run_outdir(args, Path("input/anotariais-33-2026.pdf")),
            imp.ROOT / "outputs" / "runs" / "issue33_2026",
        )

    def test_build_ocr_command_preserves_options(self):
        args = argparse.Namespace(
            pages="7-32",
            ocr_langs="auto",
            engine="vision",
            vision_level="fast",
            inspect_only=True,
        )
        command = imp.build_ocr_command(
            args, Path("/tmp/input.pdf"), Path("/tmp/out")
        )
        self.assertEqual(command, [
            sys.executable,
            str(imp.ROOT / "macau_tb_ocr.py"),
            "/tmp/input.pdf",
            "--outdir",
            "/tmp/out",
            "--pages",
            "7-32",
            "--ocr-langs",
            "auto",
            "--engine",
            "vision",
            "--vision-level",
            "fast",
            "--inspect-only",
        ])

    def test_build_v5_import_command_preserves_publish_flags(self):
        args = argparse.Namespace(
            publish=True,
            published_by="tester",
            force_publish=True,
        )
        command = imp.build_v5_import_command(
            args, Path("/tmp/out"), Path("/tmp/v5.sqlite")
        )
        self.assertEqual(command, [
            sys.executable,
            str(imp.ROOT / "tools" / "build_database.py"),
            "/tmp/out",
            "--db",
            "/tmp/v5.sqlite",
            "--publish",
            "--published-by",
            "tester",
            "--force-publish",
        ])

    def test_v5_database_stage_order_and_skip_flags(self):
        args = argparse.Namespace(
            db=Path("/tmp/v5.sqlite"),
            publish=False,
            published_by="operator",
            force_publish=False,
            no_bank_refresh=False,
            no_name_map=False,
        )
        manifest = {"steps": {}}
        with mock.patch.object(imp, "run_command") as run_command, \
                mock.patch.object(imp, "_db_exists", return_value=True):
            imp.run_v5_database_stage(
                args, Path("/tmp/out"), Path("/tmp/import.log"), manifest,
                dry_run=True,
            )
        self.assertEqual(run_command.call_count, 3)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertIn("build_database.py", commands[0][1])
        self.assertIn("name_registry.py", commands[1][1])
        self.assertIn("import_user_standard.py", commands[2][1])
        self.assertEqual(
            set(manifest["steps"]),
            {"db_import", "bank_refresh", "name_map"},
        )
        for call in run_command.call_args_list:
            self.assertTrue(call.kwargs["dry_run"])

        skip_args = argparse.Namespace(
            db=Path("/tmp/v5.sqlite"),
            publish=False,
            published_by="operator",
            force_publish=False,
            no_bank_refresh=True,
            no_name_map=True,
        )
        skip_manifest = {"steps": {}}
        with mock.patch.object(imp, "run_command") as run_command, \
                mock.patch.object(imp, "_db_exists", return_value=True):
            imp.run_v5_database_stage(
                skip_args, Path("/tmp/out"), Path("/tmp/import.log"),
                skip_manifest,
            )
        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(set(skip_manifest["steps"]), {"db_import"})

    def test_write_manifest_is_atomic_and_format_is_unchanged(self):
        manifest = {
            "source": "https://example.test/anotariais-33-2026.pdf",
            "status": "completed",
            "steps": {"download": {"status": "downloaded"}},
        }
        imp.write_manifest(self.root, manifest)
        path = self.root / "import_manifest.json"
        path.chmod(0o600)
        imp.write_manifest(self.root, manifest)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        self.assertEqual(path.stat().st_mode & 0o7777, 0o600)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_write_manifest_preserves_existing_file_on_replace_failure(self):
        imp.write_manifest(self.root, {"status": "running"})
        path = self.root / "import_manifest.json"
        original = path.read_text(encoding="utf-8")
        with mock.patch.object(imp.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                imp.write_manifest(self.root, {"status": "completed"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
