"""Tests for the bank / line-item name standardization registry."""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_database import make_run  # noqa: E402
from tools import name_registry as reg  # noqa: E402
from tools.build_database import build_database  # noqa: E402


def write_lexicon(path: Path) -> None:
    rows = [
        {"label_id": "zh_cash", "label_std": "現金", "language": "zh-Hant",
         "level": "parent", "aliases": "", "note": ""},
        {"label_id": "pt_cash", "label_std": "Caixa", "language": "pt",
         "level": "parent", "aliases": "CAIXA", "note": ""},
        {"label_id": "zh_mop", "label_std": "澳門元", "language": "zh-Hant",
         "level": "child", "aliases": "", "note": ""},
        {"label_id": "pt_mop", "label_std": "Patacas", "language": "pt",
         "level": "child", "aliases": "", "note": ""},
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def billing_rows() -> list[dict]:
    return [
        {"label_raw": "現金", "label_standard": "現金",
         "label_standard_id": "zh_cash", "label_standard_status": "exact",
         "label_language": "zh-Hant", "label_role": "parent",
         "debit_amount": "1.00"},
        {"label_raw": "Caixa", "label_standard": "Caixa",
         "label_standard_id": "pt_cash", "label_standard_status": "exact",
         "label_language": "pt", "label_role": "parent"},
        {"label_raw": "-澳門元", "label_standard": "澳門元",
         "label_standard_id": "zh_mop", "label_standard_status": "exact",
         "label_language": "zh-Hant", "label_role": "child",
         "parent_label_standard": "現金", "debit_amount": "1.00"},
        {"label_raw": "Patacas", "label_standard": "Patacas",
         "label_standard_id": "pt_mop", "label_standard_status": "exact",
         "label_language": "pt", "label_role": "child",
         "parent_label_standard": "Caixa"},
        {"label_raw": "新科目", "label_standard": "",
         "label_standard_status": "unmatched",
         "label_language": "zh-Hant", "label_role": "parent"},
    ]


class NameRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "test.sqlite"
        self.lexicon = self.root / "lexicon.csv"
        write_lexicon(self.lexicon)
        self.run = make_run(self.root / "run", [{
            "pdf_page": 1, "bank_name": "BANK A", "rows": billing_rows(),
        }])
        build_database(self.db, [self.run], publish=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_build_seeds_concepts_and_maps_same_language_aliases(self):
        report = reg.build_registry(
            self.db, lexicon_path=self.lexicon, bank_aliases_path=None
        )
        self.assertEqual(report["counts"]["banks"], 1)
        self.assertEqual(report["counts"]["pending_reviews"].get("line_item", 0), 3)
        concept = self.query(
            "SELECT concept_key, canonical_zh, canonical_pt, status "
            "FROM line_item_concepts WHERE concept_key = 'lex:zh_cash'"
        )[0]
        self.assertEqual(concept["canonical_zh"], "現金")
        self.assertEqual(concept["status"], "approved")
        mapped = self.query(
            "SELECT label_standard, line_item_concept_id, mapping_status "
            "FROM line_items WHERE label_standard IN ('現金', 'Caixa') "
            "ORDER BY row_order"
        )
        self.assertTrue(all(row["line_item_concept_id"] for row in mapped))
        self.assertTrue(all(row["mapping_status"] == "concept_exact"
                            for row in mapped))

    def test_bilingual_pair_candidate_can_be_approved_and_merges_concepts(self):
        reg.build_registry(
            self.db, lexicon_path=self.lexicon, bank_aliases_path=None
        )
        review = self.query(
            "SELECT review_id, current_entity_id, suggested_entity_id "
            "FROM name_mapping_review WHERE match_method = 'bilingual_pair' "
            "AND raw_name = 'Caixa'"
        )[0]
        result = reg.approve_review(
            self.db, review["review_id"], "reviewer"
        )
        self.assertEqual(result["status"], "approved")
        old = self.query(
            "SELECT status, merged_into_concept_id FROM line_item_concepts "
            "WHERE concept_id = ?",
            (review["current_entity_id"],),
        )[0]
        self.assertEqual(old["status"], "merged")
        self.assertEqual(old["merged_into_concept_id"], review["suggested_entity_id"])
        target = self.query(
            "SELECT canonical_pt FROM line_item_concepts WHERE concept_id = ?",
            (review["suggested_entity_id"],),
        )[0]
        self.assertEqual(target["canonical_pt"], "Caixa")
        mapped = self.query(
            "SELECT line_item_concept_id FROM line_items "
            "WHERE label_standard = 'Caixa'"
        )[0]
        self.assertEqual(mapped["line_item_concept_id"],
                         review["suggested_entity_id"])
        audit = self.query(
            "SELECT action FROM name_mapping_audit "
            "WHERE action = 'merge_concept'"
        )
        self.assertEqual(len(audit), 1)

    def test_reject_marks_review_without_creating_alias(self):
        reg.build_registry(
            self.db, lexicon_path=self.lexicon, bank_aliases_path=None
        )
        review = self.query(
            "SELECT review_id FROM name_mapping_review "
            "WHERE match_method = 'bilingual_pair' AND raw_name = 'Patacas'"
        )[0]
        reg.reject_review(self.db, review["review_id"], "reviewer", "not a match")
        row = self.query(
            "SELECT status FROM name_mapping_review WHERE review_id = ?",
            (review["review_id"],),
        )[0]
        self.assertEqual(row["status"], "rejected")
        aliases = self.query(
            "SELECT COUNT(*) FROM line_item_aliases "
            "WHERE raw_name = 'Patacas' AND source = 'review'"
        )[0][0]
        self.assertEqual(aliases, 0)

    def test_new_raw_name_creates_pending_candidate_not_approved_mapping(self):
        reg.build_registry(
            self.db, lexicon_path=self.lexicon, bank_aliases_path=None
        )
        review = self.query(
            "SELECT review_id, suggested_entity_id, match_method FROM name_mapping_review "
            "WHERE raw_name = '新科目'"
        )[0]
        self.assertEqual(review["match_method"], "new_candidate")
        concept = self.query(
            "SELECT status FROM line_item_concepts WHERE concept_id = ?",
            (review["suggested_entity_id"],),
        )[0]
        self.assertEqual(concept["status"], "provisional")
        mapped = self.query(
            "SELECT line_item_concept_id, mapping_status FROM line_items "
            "WHERE label_raw = '新科目'"
        )[0]
        self.assertIsNone(mapped["line_item_concept_id"])
        self.assertEqual(mapped["mapping_status"], "concept_unmapped")

        reg.approve_review(
            self.db, review["review_id"], "reviewer"
        )
        promoted = self.query(
            "SELECT status FROM line_item_concepts WHERE concept_id = ?",
            (review["suggested_entity_id"],),
        )[0]
        self.assertEqual(promoted["status"], "approved")
        mapped = self.query(
            "SELECT line_item_concept_id, mapping_status FROM line_items "
            "WHERE label_raw = '新科目'"
        )[0]
        self.assertEqual(mapped["line_item_concept_id"],
                         review["suggested_entity_id"])
        self.assertEqual(mapped["mapping_status"], "concept_alias")

    def test_build_is_idempotent(self):
        first = reg.build_registry(
            self.db, lexicon_path=self.lexicon, bank_aliases_path=None
        )
        second = reg.build_registry(
            self.db, lexicon_path=self.lexicon, bank_aliases_path=None
        )
        self.assertEqual(
            first["counts"]["concepts"], second["counts"]["concepts"]
        )
        self.assertEqual(
            first["counts"]["approved_line_item_aliases"],
            second["counts"]["approved_line_item_aliases"],
        )
        self.assertEqual(
            first["counts"]["pending_reviews"],
            second["counts"]["pending_reviews"],
        )


if __name__ == "__main__":
    unittest.main()
