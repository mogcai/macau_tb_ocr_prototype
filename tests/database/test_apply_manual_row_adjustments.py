"""Tests for normalizing manual row-adjustment input."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.apply_manual_row_adjustments import normalize_status, parse_amount  # noqa: E402


class ManualAdjustmentInputTests(unittest.TestCase):
    def test_status_variants(self) -> None:
        self.assertEqual(normalize_status("Corrected_label"), "label")
        self.assertEqual(normalize_status("corrected_credit"), "credit")
        self.assertEqual(normalize_status("Irrelvant row"), "irrelevant")
        self.assertEqual(normalize_status("Emtpy corrected_credit"), "empty_credit")
        self.assertEqual(normalize_status("Corrected_label and credit"), "label_credit")

    def test_display_amount_to_minor(self) -> None:
        self.assertEqual(parse_amount("1,234.50"), ("amount", 123450))
        self.assertEqual(parse_amount("35651521101"), ("amount", 3565152110100))
        self.assertEqual(parse_amount("-"), ("dash", None))
        self.assertEqual(parse_amount(""), ("blank", None))

    def test_more_than_two_decimals_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_amount("1.234")


if __name__ == "__main__":
    unittest.main()
