"""Tests for the V3 importer orchestration and duplicate gate."""
from __future__ import annotations

import argparse
import csv
import errno
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import macau_tb_ocr as ocr  # noqa: E402
from tools import import_issue as imp  # noqa: E402
from tools import v3_ingest as v3  # noqa: E402
from tools.build_v3_db import apply_schema  # noqa: E402


class V3IngestHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _args(self, *argv: str):
        return imp.build_parser().parse_args([
            "--target", "v3", *argv,
        ])

    def test_issue_key_derivation(self) -> None:
        self.assertEqual(
            v3.derive_issue_key("https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf"),
            "2026-33",
        )
        self.assertIsNone(v3.derive_issue_key("https://images.bo.dsaj.gov.mo/other.pdf"))
        args = self._args("anotariais-33-2026.pdf", "--issue-key", "2026-34")
        with self.assertRaises(v3.V3IngestError):
            v3.resolve_issue_key(args, args.source)

    def test_url_policy(self) -> None:
        with self.assertRaises(v3.V3IngestError):
            v3.validate_v3_url("http://images.bo.dsaj.gov.mo/a.pdf")
        with self.assertRaises(v3.V3IngestError):
            v3.validate_v3_url("https://evil.test/a.pdf")
        with self.assertRaises(v3.V3IngestError):
            v3.validate_v3_url("https://images.bo.dsaj.gov.mo/a.pdf?token=1")
        with mock.patch.object(v3.socket, "getaddrinfo", return_value=[
            (v3.socket.AF_INET, v3.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]):
            v3.validate_v3_url("https://images.bo.dsaj.gov.mo/a.pdf")

    def test_duplicate_decision_table(self) -> None:
        def args(**values):
            base = argparse.Namespace(reprocess=False, new_version=False, reason=None)
            base.__dict__.update(values)
            return base

        normal = {"issue_id": None, "same_hash": [], "same_issue_hash": [], "issue_docs": []}
        self.assertEqual(v3.decide_duplicate(normal, args()), "normal")

        same_issue = {
            "issue_id": "i", "same_hash": [{"source_document_id": "d"}],
            "same_issue_hash": [{"source_document_id": "d"}],
            "issue_docs": [{"source_document_id": "d"}],
        }
        with self.assertRaises(v3.OperatorDecision) as ctx:
            v3.decide_duplicate(same_issue, args())
        self.assertEqual(ctx.exception.status, "already_imported")
        self.assertEqual(
            v3.decide_duplicate(same_issue, args(reprocess=True, reason="retest")),
            "reprocess_existing",
        )
        with self.assertRaises(v3.V3IngestError):
            v3.decide_duplicate(same_issue, args(new_version=True, reason="new"))

        newer = {
            "issue_id": "i", "same_hash": [], "same_issue_hash": [],
            "issue_docs": [{"source_document_id": "old"}],
        }
        with self.assertRaises(v3.OperatorDecision) as ctx:
            v3.decide_duplicate(newer, args())
        self.assertEqual(ctx.exception.status, "new_source_version_required")
        self.assertEqual(
            v3.decide_duplicate(newer, args(new_version=True, reason="new")),
            "new_version",
        )
        with self.assertRaises(v3.V3IngestError):
            v3.decide_duplicate(newer, args(reprocess=True, reason="bad"))

        cross = {
            "issue_id": None, "same_hash": [{"source_document_id": "other"}],
            "same_issue_hash": [], "issue_docs": [],
        }
        with self.assertRaises(v3.OperatorDecision) as ctx:
            v3.decide_duplicate(cross, args())
        self.assertEqual(ctx.exception.status, "already_imported_content_conflict")
        self.assertEqual(
            v3.decide_duplicate(cross, args(reprocess=True, reason="cross")),
            "cross_issue_reprocess",
        )

    def test_promotion_no_clobber_and_fallback(self) -> None:
        source = self.root / "temp.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        dest = self.root / "input" / "anotariais-33-2026.pdf"
        final = v3.promote_no_clobber(source, dest, sha)
        self.assertEqual(final, dest)
        self.assertTrue(dest.is_file())
        self.assertFalse(source.exists())

        same = self.root / "same.tmp"
        same.write_bytes(dest.read_bytes())
        self.assertEqual(v3.promote_no_clobber(same, dest, sha), dest)
        self.assertFalse(same.exists())

        other = self.root / "other.tmp"
        other.write_bytes(b"%PDF-1.4\nother\n")
        other_sha = hashlib.sha256(other.read_bytes()).hexdigest()
        versioned = v3.promote_no_clobber(other, dest, other_sha)
        self.assertNotEqual(versioned, dest)
        self.assertEqual(dest.read_bytes(), b"%PDF-1.4\n")
        self.assertEqual(versioned.read_bytes(), b"%PDF-1.4\nother\n")

    def test_promotion_uses_fallback_when_link_unsupported(self) -> None:
        source = self.root / "temp2.pdf"
        source.write_bytes(b"%PDF-1.4\nfallback\n")
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        dest = self.root / "input2" / "x.pdf"
        with mock.patch.object(v3.os, "link", side_effect=OSError(errno.ENOTSUP, "no link")):
            final = v3.promote_no_clobber(source, dest, sha)
        self.assertEqual(final, dest)
        self.assertEqual(dest.read_bytes(), b"%PDF-1.4\nfallback\n")
        self.assertFalse(source.exists())

    def test_cli_rejects_v5_only_flags(self) -> None:
        args = self._args("https://images.bo.dsaj.gov.mo/a.pdf", "--db", "/tmp/v5.sqlite")
        with self.assertRaises(v3.V3IngestError):
            v3.validate_cli(args)
        args = self._args("https://images.bo.dsaj.gov.mo/a.pdf", "--pages", "1-2")
        with self.assertRaises(v3.V3IngestError):
            v3.validate_cli(args)


class V3IngestOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "v3.sqlite"
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        apply_schema(conn)
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _args(self, *argv: str):
        return imp.build_parser().parse_args([
            "--target", "v3", "--v3-db", str(self.db),
            "--allow-outside-output", "--json", *argv,
        ])

    def _make_pdf(self, pages: int = 1) -> Path:
        path = self.root / f"source_{pages}.pdf"
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page(width=200, height=200)
        doc.save(path)
        doc.close()
        return path

    def _write_ocr_artifacts(self, out_dir: Path, sha: str) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "app_version": "0.7.0",
            "source_pdf": str(self.root / "source.pdf"),
            "source_pdf_sha256": sha,
            "source_pdf_size": 10,
            "pdf_pages": 1,
            "ocr_engine": "vision",
            "ocr_version": "test",
        }
        (out_dir / "run_report.json").write_text(json.dumps(report), encoding="utf-8")
        with (out_dir / "documents.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["pdf_page", "ocr_status"])
            writer.writeheader()
            writer.writerow({"pdf_page": "1", "ocr_status": "completed"})
        with (out_dir / "tb_rows.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["pdf_page", "label_raw"])
            writer.writeheader()
            writer.writerow({"pdf_page": "1", "label_raw": "現金"})

    def test_full_import_orchestration(self) -> None:
        source = self._make_pdf(1)
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        out_dir = self.root / "out"
        args = self._args(
            "https://images.bo.dsaj.gov.mo/bo/ii/2026/99/anotariais-99-2026.pdf",
            "--outdir", str(out_dir),
        )

        def fake_download(args, input_dir, job_id):
            temp = self.root / "download.tmp"
            temp.write_bytes(source.read_bytes())
            return {
                "temp": temp,
                "source_url": args.source,
                "source_kind": "url",
                "source_filename": "anotariais-99-2026.pdf",
                "bytes": len(source.read_bytes()),
                "sha256": sha,
            }

        def fake_capture(command, log_path):
            self._write_ocr_artifacts(log_path.parent, sha)
            return 0, "", ""

        replay_result = {
            "status": "ok", "run_id": "run_1", "gazette_issue_id": "issue_1",
            "source_document_id": "doc_1", "already_present": False,
            "evidence_fingerprint": "f" * 64, "pages": 1,
            "page_extractions": 1, "rows_inserted": 1, "rows_skipped": 0,
        }
        with mock.patch.object(v3, "resolve_v3_input", return_value=self.root), \
                mock.patch.object(v3, "download_source", side_effect=fake_download), \
                mock.patch.object(v3, "run_capture", side_effect=fake_capture), \
                mock.patch.object(v3, "run_replay", return_value=replay_result):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = v3.run(args)
        self.assertEqual(code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["issue_key"], "2026-99")
        self.assertTrue((out_dir / "import_manifest.json").is_file())
        manifest = json.loads((out_dir / "import_manifest.json").read_text())
        self.assertEqual(manifest["v3_replay_status"], "completed")
        self.assertEqual(manifest["pdf_sha256"], sha)

    def test_run_replay_subprocess_contract(self) -> None:
        run_dir = self.root / "issue99_2026__job_contract"
        run_dir.mkdir()
        pdf = self._make_pdf(1)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        self._write_ocr_artifacts(run_dir, sha)
        (run_dir / "import_manifest.json").write_text(json.dumps({
            "manifest_version": 2, "manifest_origin": "written",
            "target": "v3", "operation": "full_import", "issue_key": "2026-99",
            "pdf_path": str(pdf), "pdf_sha256": sha,
            "run_dir": str(run_dir), "status": "running",
        }), encoding="utf-8")
        result = v3.run_replay(
            self.db, run_dir, "2026-99", run_dir / "replay.log"
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["issue_key"], "2026-99")
        self.assertEqual(result["pdf_pages"], 1)
        self.assertEqual(result["run_status"], "completed")

    def test_duplicate_gate_stops_before_promotion_and_ocr(self) -> None:
        source = self.root / "source.pdf"
        source.write_bytes(b"%PDF-1.4\nfixture\n")
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO gazette_issues(gazette_issue_id, issue_key, discovered_at) "
            "VALUES('g1','2026-99','2026-11-21T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO source_documents("
            "source_document_id,gazette_issue_id,document_key,storage_uri,sha256,"
            "page_count,acquired_at) VALUES('d1','g1',?,?,?,1,?)",
            (f"2026-99:{sha[:16]}", str(source), sha, "2026-11-21T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()
        out_dir = self.root / "duplicate-out"
        args = self._args(
            "https://images.bo.dsaj.gov.mo/bo/ii/2026/99/anotariais-99-2026.pdf",
            "--outdir", str(out_dir),
        )

        def fake_download(args, input_dir, job_id):
            temp = self.root / "duplicate.tmp"
            temp.write_bytes(source.read_bytes())
            return {
                "temp": temp, "source_url": args.source, "source_kind": "url",
                "source_filename": "anotariais-99-2026.pdf",
                "bytes": len(source.read_bytes()), "sha256": sha,
            }

        result_path = self.root / "result-manifest.json"
        with mock.patch.object(v3, "resolve_v3_input", return_value=self.root), \
                mock.patch.object(v3, "download_source", side_effect=fake_download), \
                mock.patch.object(v3, "run_capture") as capture, \
                mock.patch.object(v3, "run_replay") as replay_run, \
                mock.patch.object(v3, "result_manifest_path", return_value=result_path):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = v3.run(args)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "already_imported")
        self.assertFalse((self.root / "duplicate.tmp").exists())
        self.assertFalse(out_dir.exists())
        capture.assert_not_called()
        replay_run.assert_not_called()

    def _replay_fixture(
        self, *, issue_key: str = "2026-99", decision_type: str | None = None
    ) -> Path:
        run_dir = self.root / f"run_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir()
        pdf = self.root / f"pdf_{uuid.uuid4().hex[:8]}.pdf"
        pdf_doc = fitz.open()
        pdf_doc.new_page(width=200, height=200)
        pdf_doc.save(pdf)
        pdf_doc.close()
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        self._write_ocr_artifacts(run_dir, sha)
        (run_dir / "import_manifest.json").write_text(json.dumps({
            "manifest_version": 2, "manifest_origin": "written",
            "target": "v3", "operation": "full_import",
            "issue_key": issue_key, "decision_type": decision_type,
            "pdf_path": str(pdf), "pdf_sha256": sha, "run_dir": str(run_dir),
            "status": "failed",
        }), encoding="utf-8")
        return run_dir

    def _insert_source_document(self, issue_key: str, run_dir: Path) -> None:
        pdf = Path(json.loads(
            (run_dir / "import_manifest.json").read_text()
        )["pdf_path"])
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT OR IGNORE INTO gazette_issues("
            "gazette_issue_id,issue_key,discovered_at) VALUES(?,?,?)",
            (f"issue-{issue_key}", issue_key, "2026-11-21T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO source_documents("
            "source_document_id,gazette_issue_id,document_key,storage_uri,sha256,"
            "page_count,acquired_at) VALUES(?,?,?,?,?,1,?)",
            (f"doc-{issue_key}", f"issue-{issue_key}",
             f"{issue_key}:{sha[:16]}", str(pdf), sha,
             "2026-11-21T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

    def test_replay_only_cross_issue_conflict(self) -> None:
        run_dir = self._replay_fixture(issue_key="2026-99")
        self._insert_source_document("2026-98", run_dir)
        args = self._args("--replay-only", "--outdir", str(run_dir))
        result_path = self.root / "conflict-result.json"
        with mock.patch.object(v3, "run_replay") as replay_call, \
                mock.patch.object(v3, "result_manifest_path", return_value=result_path):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = v3.run(args)
        self.assertEqual(code, 3)
        self.assertEqual(
            json.loads(stdout.getvalue())["status"],
            "already_imported_content_conflict",
        )
        replay_call.assert_not_called()

    def test_replay_only_same_issue_retry_allowed(self) -> None:
        run_dir = self._replay_fixture(issue_key="2026-99")
        self._insert_source_document("2026-99", run_dir)
        args = self._args("--replay-only", "--outdir", str(run_dir))
        replay_result = {
            "status": "already_present", "run_id": "run_1",
            "gazette_issue_id": "g", "source_document_id": "d",
            "evidence_fingerprint": "f" * 64, "pdf_pages": 1,
            "ocr_pages_observed": 1, "ocr_page_coverage": "full",
            "run_status": "completed", "already_present": True,
        }
        with mock.patch.object(v3, "run_replay", return_value=replay_result):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = v3.run(args)
        self.assertEqual(code, 0)

    def test_replay_only_dry_run_does_not_lock_or_replay(self) -> None:
        run_dir = self._replay_fixture(issue_key="2026-99")
        args = self._args(
            "--replay-only", "--outdir", str(run_dir), "--dry-run",
        )
        with mock.patch.object(v3, "run_replay") as replay_call:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = v3.run(args)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "planned")
        replay_call.assert_not_called()

    def test_replay_only_rejects_symlink_artifact(self) -> None:
        run_dir = self._replay_fixture(issue_key="2026-99")
        documents = run_dir / "documents.csv"
        target = self.root / "outside-documents.csv"
        target.write_text(documents.read_text(), encoding="utf-8")
        documents.unlink()
        os.symlink(target, documents)
        args = self._args("--replay-only", "--outdir", str(run_dir))
        with mock.patch.object(v3, "run_replay") as replay_call:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = v3.run(args)
        self.assertEqual(code, 1)
        replay_call.assert_not_called()

    def test_replay_result_missing_fields_is_rejected(self) -> None:
        out_dir = self.root / "replay-result"
        out_dir.mkdir()
        stdout = json.dumps({
            "status": "ok", "run_id": "run_1",
        })
        with mock.patch.object(v3, "run_capture", return_value=(0, stdout, "")):
            with self.assertRaises(v3.V3IngestError):
                v3.run_replay(self.db, out_dir, "2026-99", out_dir / "log")

    def test_temp_names_are_unique_for_same_job_id(self) -> None:
        source = self.root / "unique.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(source)
        doc.close()
        args = self._args(
            str(source), "--outdir", str(self.root / "out"),
            "--allow-local-source",
        )
        first = v3.download_source(args, self.root, "same-job")
        second = v3.download_source(args, self.root, "same-job")
        self.assertNotEqual(first["temp"], second["temp"])
        first["temp"].unlink(missing_ok=True)
        second["temp"].unlink(missing_ok=True)

    def test_local_source_signature_and_size_validation(self) -> None:
        bad = self.root / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        args = self._args(
            str(bad), "--outdir", str(self.root / "out"),
            "--allow-local-source",
        )
        with self.assertRaises(v3.V3IngestError):
            v3.download_source(args, self.root, "job")
        big = self.root / "big.pdf"
        big.write_bytes(b"%PDF-" + b"x" * 100)
        with mock.patch.object(v3, "MAX_PDF_BYTES", 20):
            with self.assertRaises(v3.V3IngestError):
                v3.download_source(args, self.root, "job2")

    def test_url_policy_rejects_private_address_and_port(self) -> None:
        with mock.patch.object(v3.socket, "getaddrinfo", return_value=[
            (v3.socket.AF_INET, v3.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]):
            with self.assertRaises(v3.V3IngestError):
                v3.validate_v3_url("https://images.bo.dsaj.gov.mo/a.pdf")
        with self.assertRaises(v3.V3IngestError):
            v3.validate_v3_url("https://images.bo.dsaj.gov.mo:8443/a.pdf")

    def test_replay_only_orchestration(self) -> None:
        run_dir = self.root / "run"
        run_dir.mkdir()
        pdf = self._make_pdf(1)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        self._write_ocr_artifacts(run_dir, sha)
        (run_dir / "import_manifest.json").write_text(json.dumps({
            "manifest_version": 2, "manifest_origin": "written",
            "target": "v3", "operation": "full_import",
            "issue_key": "2026-99", "pdf_path": str(pdf),
            "pdf_sha256": sha, "run_dir": str(run_dir),
            "status": "failed",
        }), encoding="utf-8")
        args = self._args("--replay-only", "--outdir", str(run_dir))
        with mock.patch.object(v3, "run_replay", return_value={
            "status": "ok", "run_id": "run_1", "already_present": True,
            "evidence_fingerprint": "f" * 64,
        }):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = v3.run(args)
        self.assertEqual(code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["operation"], "replay_only")

    def test_import_lock_is_exclusive(self) -> None:
        with v3.import_lock(self.db):
            with self.assertRaises(v3.ImportLocked):
                with v3.import_lock(self.db):
                    pass

    def test_import_lock_resolves_symlink_alias(self) -> None:
        alias = self.root / "v3-alias.sqlite"
        os.symlink(self.db, alias)
        with v3.import_lock(self.db):
            with self.assertRaises(v3.ImportLocked):
                with v3.import_lock(alias):
                    pass


if __name__ == "__main__":
    unittest.main()
