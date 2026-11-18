"""Tests for standard-label matching, hierarchy and wrapped-label detection."""
from __future__ import annotations

import unittest

import macau_tb_ocr as m


def entry(label_id: str, std: str, language: str = "pt", level: str = "parent",
          aliases: tuple[str, ...] = ()) -> dict:
    keys = {m.normalize_label_key(std, language)}
    keys.update(m.normalize_label_key(alias, language) for alias in aliases)
    return {"id": label_id, "std": std, "language": language,
            "level": level, "keys": frozenset(keys)}


LEXICON = [
    entry("pt_full", "Depósitos à ordem noutras instituições de crédito no Território"),
    entry("pt_label", "Depósitos à ordem noutras instituições de crédito"),
    entry("pt_other", "Crédito concedido"),
    entry("pt_child", "Patacas", "pt", "child"),
    entry("zh_cash", "現金", "zh-Hant"),
    entry("zh_asset", "其他流動資產", "zh-Hant"),
    entry("zh_mop", "澳門元", "zh-Hant", "child"),
    entry("zh_amb1", "科目甲", "zh-Hant"),
    entry("zh_amb2", "科目乙", "zh-Hant"),
]


def row(label: str, debit: str = "", credit: str = "", **extra) -> dict:
    return {"label_raw": label, "debit_amount": debit, "credit_amount": credit,
            "review_reason": "", "review_status": "auto_check_pass", **extra}


class StandardizationTests(unittest.TestCase):
    def standardize(self, rows):
        m.standardize_labels(rows, LEXICON)
        m.build_label_hierarchy(rows)
        m.detect_label_continuations(rows, LEXICON)
        m.apply_label_review(rows)
        return rows

    def test_exact_and_fuzzy_chinese(self):
        rows = self.standardize([
            row("現金"),
            row("其他流動貫全", debit="1.00"),
        ])
        self.assertEqual(rows[0]["label_standard"], "現金")
        self.assertEqual(rows[0]["label_standard_status"], "exact")
        self.assertEqual(rows[1]["label_standard"], "其他流動資產")
        self.assertEqual(rows[1]["label_standard_status"], "fuzzy")
        self.assertLess(rows[1]["label_standard_score"], 0.70)
        self.assertIn("label_standard_low_similarity", rows[1]["review_reason"])

    def test_dash_child_gets_parent_path(self):
        rows = self.standardize([
            row("現金"),
            row("-澳門元", debit="10.00"),
        ])
        self.assertEqual(rows[0]["label_role"], "parent")
        self.assertEqual(rows[1]["label_role"], "child")
        self.assertEqual(rows[1]["label_standard"], "澳門元")
        self.assertEqual(rows[1]["parent_label_standard"], "現金")
        self.assertEqual(rows[1]["label_path"], "現金 > 澳門元")

    def test_dashless_child_is_reclassified(self):
        rows = self.standardize([
            row("Depósitos à ordem"),
            row("PATACAS", debit="5.00"),
        ])
        self.assertEqual(rows[1]["label_role"], "child")
        self.assertEqual(rows[1]["label_standard"], "Patacas")

    def test_wrapped_label_is_merged_and_reviewed(self):
        rows = self.standardize([
            row("Depósitos à ordem noutras instituições de crédito"),
            row("no Território", debit="100.00"),
        ])
        self.assertEqual(
            rows[1]["label_standard"],
            "Depósitos à ordem noutras instituições de crédito no Território",
        )
        self.assertEqual(rows[1]["label_standard_status"], "continuation")
        self.assertEqual(rows[1]["label_continuation_raw"],
                         "Depósitos à ordem noutras instituições de crédito")
        self.assertIn("label_continuation_merged", rows[1]["review_reason"])
        self.assertEqual(rows[0]["label_role"], "continuation_line")

    def test_separate_concepts_are_not_merged(self):
        rows = self.standardize([
            row("Crédito concedido"),
            row("Depósitos à ordem noutras instituições de crédito", debit="1.00"),
        ])
        self.assertEqual(rows[1]["label_standard_status"], "exact")
        self.assertFalse(rows[1].get("label_continuation_raw"))

    def test_unmatched_near_miss_is_reviewed(self):
        rows = self.standardize([
            row("科目丙", debit="1.00"),
        ])
        self.assertEqual(rows[0]["label_standard"], "")
        self.assertEqual(rows[0]["label_standard_status"], "unmatched")
        self.assertGreaterEqual(rows[0]["label_standard_score"], 0.55)
        self.assertIn("label_standard_uncertain", rows[0]["review_reason"])


if __name__ == "__main__":
    unittest.main()
