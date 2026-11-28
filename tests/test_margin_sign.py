"""Tests for negative signs printed at the far-left edge of an amount cell."""
from __future__ import annotations

import unittest
from pathlib import Path

from macau_tb_ocr import apply_margin_signs


def _image_with_sign(with_sign: bool = True, dotted: bool = False):
    import cv2
    import numpy as np

    image = np.full((400, 900), 255, dtype=np.uint8)
    cv2.line(image, (400, 20), (400, 380), 0, 2)   # debit|credit rule
    # Right-aligned amount cells.
    image[140:170, 700:780] = 0
    image[240:270, 700:780] = 0
    if with_sign:
        # 9x2 minus rule hugging the left edge of the credit cell.
        image[144:146, 406:415] = 0
    if dotted:
        # Dotted row separator: several 3px fragments, must not be a minus.
        for x in range(406, 470, 8):
            image[244:245, x:x + 3] = 0
    return image


def _row(top: int, amount: str, status: str = "parsed") -> dict:
    return {
        "label_raw": "歷年營業結果",
        "bbox_y0": top - 5,
        "bbox_y1": top + 35,
        "credit_raw": "6, 158, 864. 75",
        "credit_amount": amount,
        "credit_parse_status": status,
        "review_reason": "",
        "review_status": "auto_check_pass",
    }


class MarginSignTests(unittest.TestCase):
    def _run(self, image, rows):
        import cv2
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        cv2.imwrite(str(path), image)
        self.addCleanup(path.unlink, missing_ok=True)
        return apply_margin_signs(rows, path, image.shape[1], image.shape[0], (0.2, 400 / 900))

    def test_left_margin_minus_negates_the_amount_and_enters_review(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed")
        row = _row(144, "6158864.75")
        diag = self._run(_image_with_sign(), [row])
        self.assertEqual(diag["margin_sign_applied"], 1)
        self.assertEqual(row["credit_amount"], "-6158864.75")
        self.assertEqual(row["credit_margin_sign"], "negative")
        self.assertEqual(row["credit_parse_status"], "recovered_negative_margin")
        self.assertEqual(row["review_status"], "needs_review")
        self.assertIn("credit:recovered_negative_margin", row["review_reason"])
        # Raw OCR text is never rewritten.
        self.assertEqual(row["credit_raw"], "6, 158, 864. 75")

    def test_cell_without_a_minus_stays_positive(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed")
        row = _row(244, "84101224.04")
        diag = self._run(_image_with_sign(), [row])
        self.assertEqual(diag["margin_sign_applied"], 0)
        self.assertEqual(row["credit_amount"], "84101224.04")
        self.assertEqual(row["review_status"], "auto_check_pass")

    def test_dotted_separator_is_not_a_minus(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed")
        row = _row(244, "84101224.04")
        diag = self._run(_image_with_sign(with_sign=False, dotted=True), [row])
        self.assertEqual(diag["margin_sign_applied"], 0)
        self.assertEqual(row["credit_amount"], "84101224.04")

    def test_blank_cell_with_a_dash_is_not_negative(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed")
        row = _row(144, "")
        row["credit_parse_status"] = "blank"
        diag = self._run(_image_with_sign(), [row])
        self.assertEqual(diag["margin_sign_applied"], 0)
        self.assertEqual(row["credit_amount"], "")


if __name__ == "__main__":
    unittest.main()
