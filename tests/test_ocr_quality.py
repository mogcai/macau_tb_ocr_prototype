"""Tests for the OCR quality gate and table-body filters."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macau_tb_ocr as ocr  # noqa: E402


class TableBodyFilterTests(unittest.TestCase):
    def _box(self, text: str, y: float, x0: float = 10, x1: float = 200) -> ocr.OCRBox:
        return ocr.OCRBox(text, 1.0, x0, y, x1, y + 20, "zh-Hant")

    def test_header_and_signature_boxes_are_dropped(self):
        boxes = [
            self._box("澳門銀行地址", 40),
            self._box("帳戶名稱", 100),
            self._box("現金", 200),
            self._box("存款", 400),
            self._box("總額", 900),
            self._box("總經理 陳大文", 960),
        ]
        kept, top, bottom, dropped = ocr.filter_table_body_boxes(boxes, 1000)
        self.assertEqual([b.text for b in kept], ["現金", "存款", "總額"])
        self.assertEqual(dropped, 3)
        self.assertIsNotNone(top)
        self.assertIsNotNone(bottom)

    def test_no_header_keyword_keeps_all_boxes(self):
        boxes = [self._box("現金", 200), self._box("存款", 400)]
        kept, top, bottom, dropped = ocr.filter_table_body_boxes(boxes, 1000)
        self.assertEqual(len(kept), 2)
        self.assertIsNone(top)
        self.assertIsNone(bottom)
        self.assertEqual(dropped, 0)

    def test_column_header_row_is_not_an_account_row(self):
        rows = [
            {"y_center": 100, "label_raw": "帳戶名稱"},
            {"y_center": 200, "label_raw": "現金"},
            {"y_center": 300, "label_raw": "存款"},
        ]
        dropped = ocr.drop_column_header_rows(rows, 1000)
        self.assertEqual(dropped, 1)
        self.assertEqual([r["label_raw"] for r in rows], ["現金", "存款"])

    def test_amount_absorbed_into_label_is_flagged(self):
        rows = [
            {"label_raw": "・澳門元20,805,200.00", "debit_raw": "", "credit_raw": ""},
            {"label_raw": "外幣", "debit_raw": "", "credit_raw": ""},
            {"label_raw": "總額", "debit_raw": "1,234.56", "credit_raw": "1,234.56"},
        ]
        flagged = ocr.mark_geometry_suspect_rows(rows)
        self.assertEqual(flagged, 1)
        self.assertTrue(rows[0]["geometry_suspect"])
        self.assertFalse(rows[1].get("geometry_suspect"))
        self.assertFalse(rows[2].get("geometry_suspect"))

    def test_inset_frames_do_not_become_amount_separators(self):
        merged = [(438.0, 0.241), (411.0, 0.533), (421.0, 0.659), (486.0, 0.787)]
        selected = ocr._select_amount_column_rules(merged, allow_code_column=True)
        self.assertIsNotNone(selected)
        sep1, sep2, _label_x0 = selected
        self.assertAlmostEqual(sep1, 0.533)
        self.assertAlmostEqual(sep2, 0.659)

    def test_header_box_is_recovered_only_when_missing_from_table(self):
        table = [self._box("現金", 200)]
        header = [
            self._box("現金", 200),
            self._box("1,234.56", 200, x0=500, x1=650),
        ]
        merged = ocr.recover_header_region_table_boxes(table, header, body_top=100)
        self.assertEqual([b.text for b in merged], ["現金", "1,234.56"])

    def test_header_account_code_is_not_recovered_into_label(self):
        header = [
            self._box("10", 200, x0=300, x1=340),
            self._box("1,234.56", 200, x0=900, x1=1050),
        ]
        merged = ocr.recover_header_region_table_boxes(
            [], header, body_top=100, min_x=800
        )
        self.assertEqual([b.text for b in merged], ["1,234.56"])

    def test_page_number_is_not_an_account_row(self):
        rows = [{"y_center": 40, "label_raw": "", "debit_raw": "", "credit_raw": "185"}]
        dropped = ocr.drop_column_header_rows(rows, 1000)
        self.assertEqual(dropped, 1)
        self.assertEqual(rows, [])

    def test_narrow_rule_character_is_not_an_amount(self):
        self.assertTrue(ocr._is_column_rule_artifact(self._box("D", 200, 500, 506), 1940))
        self.assertFalse(ocr._is_column_rule_artifact(self._box("-", 200, 500, 506), 1940))
        self.assertFalse(ocr._is_column_rule_artifact(self._box("D", 200, 500, 560), 1940))


class ConfidenceReviewGateTests(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            "label_raw": "現金",
            "review_reason": "low_ocr_confidence",
            "review_status": "needs_review",
            "label_standard_status": "exact",
            "label_standard_score": 1.0,
            "debit_raw": "1,234.56",
            "credit_raw": "",
            "debit_confidence": 1.0,
            "credit_confidence": "",
            "debit_parse_status": "parsed",
            "credit_parse_status": "blank",
        }
        row.update(overrides)
        return row

    def test_exact_label_with_clean_amount_is_auto_passed(self):
        row = self._row()
        waived = ocr.apply_confidence_review_gate([row], 0.88)
        self.assertEqual(waived, 1)
        self.assertEqual(row["review_status"], "auto_check_pass")
        self.assertEqual(row["review_reason"], "")
        self.assertEqual(row["review_confidence_waived"], "label_exact")

    def test_fuzzy_label_stays_in_review(self):
        row = self._row(label_standard_status="fuzzy", label_standard_score=0.8)
        self.assertEqual(ocr.apply_confidence_review_gate([row], 0.88), 0)
        self.assertEqual(row["review_status"], "needs_review")

    def test_low_confidence_amount_stays_in_review(self):
        row = self._row(debit_confidence=0.5)
        self.assertEqual(ocr.apply_confidence_review_gate([row], 0.88), 0)
        self.assertEqual(row["review_status"], "needs_review")

    def test_malformed_amount_stays_in_review(self):
        row = self._row(debit_raw="00'00000051")
        self.assertEqual(ocr.apply_confidence_review_gate([row], 0.88), 0)
        self.assertEqual(row["review_status"], "needs_review")

    def test_other_review_reason_stays_in_review(self):
        row = self._row(review_reason="low_ocr_confidence;geometry_suspect")
        self.assertEqual(ocr.apply_confidence_review_gate([row], 0.88), 0)
        self.assertEqual(row["review_status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
