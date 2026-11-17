import importlib.util
import sys
import unittest
from unittest.mock import patch
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macau_tb_ocr import (
    OCRBox,
    infer_number_format,
    normalize_chinese_label,
    parse_amount,
    validate_rows,
    build_parser,
)


class NumberFormatTests(unittest.TestCase):
    def setUp(self):
        self.cn_page_ocr_values = [
            "119.366,280.10",
            "2.496,722,065.08",
            "405.228,717.57",
            "65.045.622.601.52",
            "114,442,609.02",
            "595,846.01",
            "0.00",
        ]

    def test_infers_comma_dot_from_page_evidence_not_language(self):
        profile = infer_number_format(self.cn_page_ocr_values)
        self.assertEqual(profile["number_format"], "comma_dot")
        self.assertGreaterEqual(profile["format_confidence"], 0.9)

    def test_repairs_ocr_separator_mix_but_flags_it(self):
        expected = {
            "119.366,280.10": "119366280.10",
            "2.496,722,065.08": "2496722065.08",
            "405.228,717.57": "405228717.57",
            "65.045.622.601.52": "65045622601.52",
        }
        for raw, canonical in expected.items():
            with self.subTest(raw=raw):
                value, status = parse_amount(raw, "comma_dot")
                self.assertEqual(value, canonical)
                self.assertEqual(status, "recovered_ocr_separator")

    def test_us_style_value(self):
        self.assertEqual(parse_amount("1,234,567.89", "comma_dot"), ("1234567.89", ""))

    def test_european_style_value(self):
        self.assertEqual(parse_amount("1.234.567,89", "dot_comma"), ("1234567.89", ""))

    def test_grouping_only_values(self):
        self.assertEqual(parse_amount("1,234,567", "comma_dot"), ("1234567", ""))
        self.assertEqual(parse_amount("1.234.567", "dot_comma"), ("1234567", ""))

    def test_zero_with_decimal_separator(self):
        self.assertEqual(parse_amount("0.00", "comma_dot"), ("0.00", ""))
        self.assertEqual(parse_amount("0,00", "dot_comma"), ("0.00", ""))

    def test_invalid_grouping_is_not_guessed(self):
        value, status = parse_amount("12,34.56", "comma_dot")
        self.assertEqual(value, "")
        self.assertIn(status, {"invalid_grouping", "ambiguous_format"})

    def test_ocr_letter_confusion_is_flagged(self):
        value, status = parse_amount("1O,5O0.00", "comma_dot")
        self.assertEqual(value, "10500.00")
        self.assertEqual(status, "ambiguous_ocr_character")

    def test_negative_parentheses(self):
        self.assertEqual(parse_amount("(1,234.50)", "comma_dot"), ("-1234.50", ""))

    def test_mixed_page_is_ambiguous(self):
        profile = infer_number_format(["1,234.56", "1.234,56"])
        self.assertEqual(profile["number_format"], "ambiguous")

    def test_row_validation_adds_parse_status_and_review(self):
        rows = [{
            "label_raw": "現金",
            "debit_raw": "119.366,280.10",
            "credit_raw": "",
            "row_confidence": 0.99,
        }]
        out = validate_rows(rows, 0.88, number_format_override="comma_dot",
                            chinese_label_normalization="off")
        self.assertEqual(out[0]["debit_amount"], "119366280.10")
        self.assertEqual(out[0]["debit_parse_status"], "recovered_ocr_separator")
        self.assertEqual(out[0]["review_status"], "needs_review")
        self.assertIn("debit:recovered_ocr_separator", out[0]["review_reason"])

    def test_label_raw_is_not_mutated_by_normalization(self):
        raw = "现金"
        normalized, status = normalize_chinese_label(raw, "off")
        self.assertEqual(raw, "现金")
        self.assertEqual(normalized, "现金")
        self.assertEqual(status, "unchanged")

    @unittest.skipUnless(importlib.util.find_spec("opencc"), "OpenCC is installed through requirements.txt")
    def test_opencc_simplified_to_hk_traditional(self):
        normalized, status = normalize_chinese_label("现金", "s2hk")
        self.assertEqual(normalized, "現金")
        self.assertEqual(status, "converted")

    def test_default_engine_is_vision_on_macos(self):
        with patch("macau_tb_ocr.sys.platform", "darwin"):
            args = build_parser().parse_args(["dummy.pdf"])
        self.assertEqual(args.engine, "vision")

    def test_default_engine_is_paddle_off_macos(self):
        with patch("macau_tb_ocr.sys.platform", "linux"):
            args = build_parser().parse_args(["dummy.pdf"])
        self.assertEqual(args.engine, "paddle")


if __name__ == "__main__":
    unittest.main()
