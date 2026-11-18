"""Tests for the ground-truth build/score helper logic."""
from __future__ import annotations

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gt_score = load_module("gt_score", "tools/gt_score.py")
gt_build = load_module("gt_build_sheet", "tools/gt_build_sheet.py")


class TextNormalizationTests(unittest.TestCase):
    def test_norm_label_collapses_whitespace_and_width(self):
        self.assertEqual(gt_score.norm_label("  現金　存款 "), "現金存款")

    def test_norm_label_ignores_spacing_inside_chinese_labels(self):
        self.assertEqual(gt_score.norm_label("AMCM 存款"), gt_score.norm_label("AMCM存款"))

    def test_norm_label_keeps_word_spacing_for_latin_labels(self):
        self.assertEqual(
            gt_score.norm_label("DEPÓSITOS  À   ORDEM"), "DEPÓSITOS À ORDEM"
        )

    def test_to_decimal_accepts_canonical_values(self):
        self.assertEqual(gt_score.to_decimal("1234.56"), Decimal("1234.56"))
        self.assertEqual(gt_score.to_decimal("1,234.56"), Decimal("1234.56"))
        self.assertIsNone(gt_score.to_decimal("-"))
        self.assertIsNone(gt_score.to_decimal(""))

    def test_edit_distance(self):
        self.assertEqual(gt_score.edit_distance("現金", "現金"), 0)
        self.assertEqual(gt_score.edit_distance("abc", "abd"), 1)
        self.assertEqual(gt_score.edit_distance("", "abc"), 3)


class RowMatchingTests(unittest.TestCase):
    def test_matches_by_fraction_when_scales_differ(self):
        gt_rows = [
            {"bbox_y0": "100", "bbox_y1": "150", "y_frac": "0.1"},
            {"bbox_y0": "300", "bbox_y1": "350", "y_frac": "0.3"},
        ]
        run_rows = [{"bbox_y0": "200"}, {"bbox_y0": "600"}]
        match = gt_score.match_rows(gt_rows, run_rows, gt_height=1000.0, run_height=2000.0)
        self.assertEqual(match, {0: 0, 1: 1})

    def test_falls_back_to_pixels_without_fractions(self):
        gt_rows = [
            {"bbox_y0": "100", "bbox_y1": "150"},
            {"bbox_y0": "300", "bbox_y1": "350"},
        ]
        run_rows = [{"bbox_y0": "102"}, {"bbox_y0": "305"}]
        match = gt_score.match_rows(gt_rows, run_rows, gt_height=0.0, run_height=0.0)
        self.assertEqual(match, {0: 0, 1: 1})


class DashFlagTests(unittest.TestCase):
    def test_builder_marks_only_printed_dash(self):
        for raw in ("-", "—", "–", "－", " - "):
            self.assertTrue(gt_build.is_dash_cell(raw))
        for raw in ("", " ", "0.00", "abc"):
            self.assertFalse(gt_build.is_dash_cell(raw))


class SyncMissingRowsTests(unittest.TestCase):
    def gt(self, top: float, row_id: str) -> dict[str, str]:
        return {"pdf_page": "29", "gt_row_id": row_id,
                "bbox_y0": str(top), "bbox_y1": str(top + 50), "y_frac": str(top / 1000)}

    def run_row(self, top: float, label: str) -> dict[str, str]:
        return {"pdf_page": "29", "label_raw": label, "bbox_y0": str(top),
                "bbox_y1": str(top + 50), "debit_raw": "", "credit_raw": "",
                "debit_amount": "", "credit_amount": "",
                "debit_parse_status": "blank",
                "credit_parse_status": "blank"}

    def test_adds_only_unmatched_run_rows(self):
        gt_rows = [self.gt(100, "p0029_r001"), self.gt(300, "p0029_r002")]
        run_rows = [self.run_row(100, "現金"), self.run_row(300, "-澳門元"), self.run_row(500, "金、銀")]
        added = gt_build.sync_missing_rows(29, gt_rows, run_rows, 1000)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["gt_row_id"], "p0029_r003")
        self.assertEqual(added[0]["label_gt"], "金、銀")
        self.assertEqual(added[0]["verified"], 0)
        self.assertEqual(added[0]["gt_note"], "sync_added_unverified")

    def test_nothing_added_when_aligned(self):
        gt_rows = [self.gt(100, "p0029_r001")]
        run_rows = [self.run_row(102, "現金")]
        self.assertEqual(gt_build.sync_missing_rows(29, gt_rows, run_rows, 1000), [])


class RowMatchingGapsTests(unittest.TestCase):
    def gt_row(self, top: float, row_id: str) -> dict[str, str]:
        return {"pdf_page": "7", "gt_row_id": row_id,
                "bbox_y0": str(top), "bbox_y1": str(top + 50),
                "label_gt": "x", "debit_gt": "", "credit_gt": "",
                "debit_dash_gt": "1", "credit_dash_gt": "1"}

    def run_row(self, top, label="x") -> dict[str, str]:
        return {"pdf_page": "7", "bbox_y0": str(top), "label_raw": label,
                "debit_raw": "", "debit_amount": "", "credit_raw": "",
                "credit_amount": "", "debit_parse_status": "blank",
                "credit_parse_status": "blank", "review_status": "needs_review"}

    def test_out_of_tolerance_rows_are_not_paired(self):
        gt = [self.gt_row(100, "r1"), self.gt_row(300, "r2")]
        run = [self.run_row(100), self.run_row(500)]
        self.assertEqual(gt_score.match_rows(gt, run, 0, 0), {0: 0})

    def test_alignment_f1_drops_for_missing_plus_spurious(self):
        gt = [self.gt_row(100, "r1"), self.gt_row(300, "r2")]
        run = [self.run_row(100), self.run_row(500)]
        metrics, _ = gt_score.score_page(7, gt, run, 0, 0)
        self.assertEqual(metrics["matched_rows"], 1)
        self.assertEqual(metrics["gt_missing_rows"], 1)
        self.assertEqual(metrics["run_spurious_rows"], 1)
        self.assertEqual(metrics["alignment_f1"], 0.5)

    def test_rows_without_coordinates_pair_by_order(self):
        gt = [{"pdf_page": "7", "gt_row_id": "r1", "bbox_y0": "", "bbox_y1": ""},
              {"pdf_page": "7", "gt_row_id": "r2", "bbox_y0": "", "bbox_y1": ""}]
        run = [{"pdf_page": "7", "bbox_y0": ""}, {"pdf_page": "7", "bbox_y0": ""}]
        self.assertEqual(gt_score.match_rows(gt, run, 0, 0), {0: 0, 1: 1})

    def test_one_sided_coordinates_do_not_fallback(self):
        gt = [{"pdf_page": "7", "gt_row_id": "r1", "bbox_y0": "", "bbox_y1": ""}]
        run = [{"pdf_page": "7", "bbox_y0": "100"}]
        self.assertEqual(gt_score.match_rows(gt, run, 0, 0), {})


class DashStateTests(unittest.TestCase):
    def gt_rows(self, dash: str = "1", value: str = "") -> list[dict[str, str]]:
        return [{
            "pdf_page": "7", "gt_row_id": "r1", "bbox_y0": "100", "bbox_y1": "150",
            "label_gt": "x", "debit_gt": value, "credit_gt": "",
            "debit_dash_gt": dash, "credit_dash_gt": "0",
        }]

    def run_rows(self, raw: str = "", status: str = "blank", value: str = "") -> list[dict[str, str]]:
        return [{
            "pdf_page": "7", "bbox_y0": "100", "label_raw": "x",
            "debit_raw": raw, "debit_amount": value,
            "credit_raw": "", "credit_amount": "",
            "debit_parse_status": status, "credit_parse_status": "blank",
            "review_status": "needs_review",
        }]

    def test_dash_matches_dash(self):
        metrics, errors = gt_score.score_page(
            7, self.gt_rows(), self.run_rows("-", "dash"), 0, 0
        )
        self.assertEqual(metrics["dash_mismatch_rows"], 0)
        self.assertEqual(errors, [])

    def test_dash_versus_blank_is_a_mismatch(self):
        metrics, errors = gt_score.score_page(
            7, self.gt_rows(), self.run_rows("", "blank"), 0, 0
        )
        self.assertEqual(metrics["dash_mismatch_rows"], 1)
        self.assertTrue(any(e["field"] == "debit_state" for e in errors))

    def test_blank_versus_dash_is_a_mismatch(self):
        metrics, _ = gt_score.score_page(
            7, self.gt_rows(dash="0"), self.run_rows("-", "dash"), 0, 0
        )
        self.assertEqual(metrics["dash_mismatch_rows"], 1)

    def test_dash_versus_value_is_spurious_not_dash_mismatch(self):
        metrics, errors = gt_score.score_page(
            7, self.gt_rows(), self.run_rows("1.00", "parsed", "1.00"), 0, 0
        )
        self.assertEqual(metrics["wrong_amount_rows"], 1)
        self.assertEqual(metrics["dash_mismatch_rows"], 0)
        self.assertTrue(any(e["field"] == "debit_spurious" for e in errors))

    def test_is_dash_cell(self):
        for raw in ("-", "—", "–", "－", " - "):
            self.assertTrue(gt_score.is_dash_cell(raw))
        for raw in ("", " ", "0.00", "abc"):
            self.assertFalse(gt_score.is_dash_cell(raw))


class ScoreTests(unittest.TestCase):
    def gt(self, debit: str = "1000.00", label: str = "現金") -> list[dict[str, str]]:
        return [{
            "pdf_page": "7", "gt_row_id": "p0007_r001",
            "bbox_y0": "100", "bbox_y1": "150", "y_frac": "0.1",
            "label_gt": label, "debit_gt": debit, "credit_gt": "",
            "debit_dash_gt": "0", "credit_dash_gt": "0", "verified": "1",
        }]

    def run_row(self, amount: str = "1000.00", status: str = "auto_check_pass") -> list[dict[str, str]]:
        return [{
            "pdf_page": "7", "bbox_y0": "100", "label_raw": "現金",
            "debit_amount": amount, "credit_amount": "", "review_status": status,
            "review_reason": "", "debit_raw": amount,
        }]

    def test_perfect_row(self):
        metrics, errors = gt_score.score_page(7, self.gt(), self.run_row(), 1000, 1000)
        self.assertEqual(metrics["matched_rows"], 1)
        self.assertEqual(metrics["debit_exact_rate"], 1.0)
        self.assertEqual(metrics["label_exact_rate"], 1.0)
        self.assertEqual(errors, [])

    def test_auto_pass_wrong_amount_is_flagged(self):
        metrics, errors = gt_score.score_page(7, self.gt(), self.run_row("1000.01"), 1000, 1000)
        self.assertEqual(metrics["wrong_amount_rows"], 1)
        self.assertEqual(metrics["auto_pass_wrong_amount_rows"], 1)
        self.assertEqual(metrics["debit_exact_rate"], 0.0)
        self.assertTrue(any(e["field"] == "debit_value" for e in errors))

    def test_spurious_amount_against_empty_gt(self):
        gt = self.gt(debit="")
        gt[0]["debit_dash_gt"] = "1"
        metrics, errors = gt_score.score_page(7, gt, self.run_row("5.00"), 1000, 1000)
        self.assertEqual(metrics["wrong_amount_rows"], 1)
        self.assertTrue(any(e["field"] == "debit_spurious" for e in errors))

    def test_unmatched_rows(self):
        metrics, _ = gt_score.score_page(7, self.gt(), [], 1000, 1000)
        self.assertEqual(metrics["gt_missing_rows"], 1)
        self.assertEqual(metrics["matched_rows"], 0)


if __name__ == "__main__":
    unittest.main()
