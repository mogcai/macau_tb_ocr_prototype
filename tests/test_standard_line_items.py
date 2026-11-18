"""Tests for the standard line-item master export/apply workflow."""
from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_database import make_run  # noqa: E402
from tests.test_name_registry import billing_rows, write_lexicon  # noqa: E402
from tools import name_registry as reg  # noqa: E402
from tools import standard_line_items as std  # noqa: E402
from tools.build_database import build_database  # noqa: E402


class StandardLineItemTests(unittest.TestCase):
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
        reg.build_registry(
            self.db, lexicon_path=self.lexicon, bank_aliases_path=None
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_export_master_contains_zh_pt_and_merge_keys(self):
        out = self.root / "standard.csv"
        result = std.export_master(self.db, out)
        self.assertEqual(result["rows"], 2)  # 現金 + Caixa, until merged
        with out.open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        cash = next(row for row in rows if row["canonical_zh"] == "現金")
        self.assertEqual(cash["canonical_pt"], "Caixa")
        self.assertIn("lex:pt_cash", cash["merge_keys"])
        self.assertEqual(cash["status"], "provisional")

    def test_apply_approved_master_merges_and_maps(self):
        out = self.root / "standard.csv"
        std.export_master(self.db, out)
        with out.open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            if row["canonical_zh"] == "現金":
                row["status"] = "approved"
        with out.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=std.MASTER_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        result = std.apply_master(self.db, out, actor="reviewer")
        self.assertGreaterEqual(result["applied"].get("merged_concepts", 0), 1)
        cash = self.query(
            "SELECT concept_id, canonical_zh, canonical_pt, status "
            "FROM line_item_concepts WHERE canonical_zh = '現金'"
        )[0]
        self.assertEqual(cash["canonical_pt"], "Caixa")
        mapped = self.query(
            "SELECT COUNT(*) FROM line_items "
            "WHERE label_standard IN ('現金', 'Caixa') "
            "AND line_item_concept_id = ?",
            (cash["concept_id"],),
        )[0][0]
        self.assertEqual(mapped, 2)
        merged = self.query(
            "SELECT status FROM line_item_concepts WHERE concept_key = 'lex:pt_cash'"
        )[0]["status"]
        self.assertEqual(merged, "merged")

    def test_standard_mode_does_not_auto_create_concepts(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("DELETE FROM name_mapping_review WHERE raw_name = '新科目'")
            conn.execute("DELETE FROM line_item_concepts WHERE concept_key LIKE 'auto:%'")
            conn.execute(
                "INSERT INTO schema_meta (key, value) "
                "VALUES ('standard_master_loaded', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'"
            )
            conn.commit()
            reg.generate_line_item_reviews(conn)
            conn.commit()
            review = conn.execute(
                "SELECT suggested_entity_id, match_method "
                "FROM name_mapping_review WHERE raw_name = '新科目'"
            ).fetchone()
            self.assertEqual(review["match_method"], "needs_new_standard")
            self.assertEqual(review["suggested_entity_id"], 0)
            auto = conn.execute(
                "SELECT COUNT(*) FROM line_item_concepts "
                "WHERE concept_key LIKE 'auto:%'"
            ).fetchone()[0]
            self.assertEqual(auto, 0)
        finally:
            conn.close()

    def test_apply_skips_provisional_rows_without_flag(self):
        out = self.root / "standard.csv"
        std.export_master(self.db, out)
        result = std.apply_master(self.db, out, actor="reviewer")
        self.assertGreater(result["applied"].get("skipped_provisional", 0), 0)


if __name__ == "__main__":
    unittest.main()
