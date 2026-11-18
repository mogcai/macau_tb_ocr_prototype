"""Tests for OCR-time source PDF hashing."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macau_tb_ocr as m  # noqa: E402


class SourceFingerprintTests(unittest.TestCase):
    def test_sha256_file_matches_hashlib_and_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            payload = b"%PDF-1.4\n" + b"x" * (3 * 1024 * 1024)
            path.write_bytes(payload)
            expected = hashlib.sha256(payload).hexdigest()
            self.assertEqual(m.sha256_file(path), expected)
            self.assertEqual(len(m.sha256_file(path)), 64)


if __name__ == "__main__":
    unittest.main()
