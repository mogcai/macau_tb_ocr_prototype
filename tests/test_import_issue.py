"""Tests for the PDF URL -> OCR -> SQLite import wrapper."""
from __future__ import annotations

import http.server
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
