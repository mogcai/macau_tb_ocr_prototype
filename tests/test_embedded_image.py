"""Regression tests for extracting stencil (ImageMask) table images."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macau_tb_ocr as ocr  # noqa: E402


class _FakeColorspace:
    n = 3


class _FakeMaskPixmap:
    """Stand-in for a CCITT ImageMask pixmap: no colorspace, inverted samples."""

    def __init__(self) -> None:
        self.width = 2000
        self.height = 3000
        self.colorspace = None
        self.alpha = 1

    def save(self, path: str) -> None:  # pragma: no cover - must not be called
        raise AssertionError("stencil pixmap must not be saved directly")


class _FakeRenderedPixmap:
    def __init__(self) -> None:
        self.width = 2001
        self.height = 3001
        self.colorspace = _FakeColorspace()
        self.alpha = False

    def save(self, path: str) -> None:
        Path(path).write_bytes(b"rendered")


class _FakePage:
    rect = fitz.Rect(0, 0, 600, 850)

    def get_images(self, full: bool = True):  # noqa: ARG002
        return [(7, 0, 2000, 3000, 1, "", "", "Im0", "CCITTFaxDecode", 0)]

    def get_image_rects(self, xref: int):
        self.rendered_xref = xref
        return [fitz.Rect(0, 0, 600, 850)]

    def get_pixmap(self, matrix=None, clip=None, alpha=False):  # noqa: ARG002
        self.render_matrix = matrix
        return _FakeRenderedPixmap()


class EmbeddedImageTests(unittest.TestCase):
    def test_stencil_image_is_rendered_instead_of_aborting(self):
        page = _FakePage()
        pdf = fitz.open()
        mask = _FakeMaskPixmap()

        def fake_pixmap(*args):
            if len(args) == 2 and args[0] is pdf:
                return mask
            raise AssertionError(f"unexpected Pixmap call: {args!r}")

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "page_0031_table.png"
            with mock.patch.object(ocr.fitz, "Pixmap", side_effect=fake_pixmap):
                table = ocr.extract_best_embedded_image(pdf, page, 31, out_path)

            self.assertTrue(out_path.exists())
            self.assertEqual(table.xref, 7)
            self.assertFalse(table.extracted_from_embedded_image)
            self.assertEqual((table.width, table.height), (2001, 3001))
            self.assertEqual(page.rendered_xref, 7)
            # Render at the embedded image's native pixel resolution.
            self.assertAlmostEqual(page.render_matrix.a, max(2000 / 600, 3000 / 850))
        pdf.close()


if __name__ == "__main__":
    unittest.main()
