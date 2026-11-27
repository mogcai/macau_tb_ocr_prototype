"""Tests for applying the curated standard-item classification CSV."""
from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import import_standard_classification as imp  # noqa: E402
from tools.build_v3_db import apply_schema  # noqa: E402

NOW = "2026-11-27T00:00:00Z"


class StandardItemClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "v3.sqlite"
        self.csv = self.root / "classification.csv"
        conn = sqlite3.connect(self.db)
        apply_schema(conn)
        for item in (
            ("si-cash", "user:現金", "現金", "CAIXA", None),
            ("si-cash-fx", "user:現金>外幣", "外幣", "MOEDAS EXTERNAS", "si-cash"),
            ("si-time", "user:定期存款", "定期存款", "DEPÓSITOS A PRAZO", None),
            ("si-time-fx", "user:定期存款>外幣", "外幣", "MOEDAS EXTERNAS", "si-time"),
            ("si-total", "user:總計", "總計", "TOTAIS", None),
            ("si-treasury", "user:pt:TESOURO", None,
             "TESOURO PÚBLICO - CONTA CORRENTE", None),
            ("si-due", "user:總公司之欠款", "總公司之欠款", None, None),
        ):
            conn.execute(
                "INSERT INTO standard_items("
                "standard_item_id, item_key, canonical_zh, canonical_zh_status, "
                "canonical_pt, canonical_pt_status, parent_standard_item_id, "
                "item_status, created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    item[0], item[1], item[2],
                    "approved" if item[2] else "missing",
                    item[3],
                    "approved" if item[3] else "missing",
                    item[4], "approved", NOW,
                ),
            )
        conn.commit()
        conn.close()
        self.write_csv([
            ["現金", "資產負債-資產", "classified"],
            ["現金>外幣", "資產負債-資產", "classified"],
            ["定期存款", "資產負債-負債", "classified"],
            ["定期存款>外幣", "資產負債-負債", "classified"],
            ["總計", "排除", "excluded"],
            ["pt:TESOURO PÚBLICO - CONTA CORRENTE", "資產負債-資產", "classified"],
        ])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_csv(self, rows: list[list[str]]) -> None:
        with self.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["account_name", "category", "classification_status"])
            writer.writerows(rows)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_dry_run_matches_paths_and_pt_prefix_without_writing(self):
        result = imp.classify(self.db, self.csv, actor="tester")
        self.assertEqual(result["counts"], {"set": 6})
        self.assertEqual(result["unmatched"], [])
        self.assertEqual(result["ambiguous"], [])
        self.assertEqual(
            [item["item_key"] for item in result["unclassified_standard_items"]],
            ["user:總公司之欠款"],
        )
        rows = self.query("SELECT category FROM standard_items")
        self.assertTrue(all(row["category"] is None for row in rows))

    def test_apply_writes_category_status_source_and_audit(self):
        result = imp.classify(self.db, self.csv, actor="tester", apply=True)
        self.assertEqual(result["counts"], {"set": 6})
        rows = {
            row["item_key"]: row for row in self.query(
                "SELECT item_key, category, classification_status, "
                "classification_source FROM standard_items"
            )
        }
        self.assertEqual(rows["user:現金"]["category"], "資產負債-資產")
        self.assertEqual(rows["user:現金"]["classification_status"], "classified")
        self.assertIn("classification.csv", rows["user:現金"]["classification_source"])
        self.assertEqual(rows["user:總計"]["category"], "排除")
        self.assertEqual(rows["user:總計"]["classification_status"], "excluded")
        self.assertEqual(
            rows["user:pt:TESOURO"]["category"], "資產負債-資產"
        )
        self.assertIsNone(rows["user:總公司之欠款"]["category"])
        self.assertEqual(
            rows["user:總公司之欠款"]["classification_status"], "unclassified"
        )
        self.assertEqual(
            self.query("SELECT COUNT(*) AS n FROM audit_events "
                       "WHERE event_type='standard_item_classified'")[0]["n"],
            6,
        )

    def test_reapply_is_idempotent(self):
        imp.classify(self.db, self.csv, actor="tester", apply=True)
        result = imp.classify(self.db, self.csv, actor="tester", apply=True)
        self.assertEqual(result["counts"], {"noop": 6})
        self.assertEqual(
            self.query("SELECT COUNT(*) AS n FROM audit_events "
                       "WHERE event_type='standard_item_classified'")[0]["n"],
            6,
        )

    def test_existing_category_is_a_conflict_unless_overwrite(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "UPDATE standard_items SET category='人手分類' WHERE item_key='user:現金'"
        )
        conn.commit()
        conn.close()
        result = imp.classify(self.db, self.csv, actor="tester", apply=True)
        self.assertEqual(result["counts"]["conflict"], 1)
        row = self.query(
            "SELECT category FROM standard_items WHERE item_key='user:現金'"
        )[0]
        self.assertEqual(row["category"], "人手分類")

        result = imp.classify(
            self.db, self.csv, actor="tester", apply=True, overwrite=True,
        )
        self.assertEqual(result["counts"]["update"], 1)
        row = self.query(
            "SELECT category, classification_status FROM standard_items "
            "WHERE item_key='user:現金'"
        )[0]
        self.assertEqual(row["category"], "資產負債-資產")
        self.assertEqual(row["classification_status"], "classified")

    def test_unmatched_and_ambiguous_rows_are_reported(self):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO standard_items("
            "standard_item_id, item_key, canonical_zh, canonical_zh_status, "
            "item_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("si-dup", "user:重複", "重複", "approved", "approved", NOW),
        )
        conn.execute(
            "INSERT INTO standard_items("
            "standard_item_id, item_key, canonical_zh, canonical_zh_status, "
            "item_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("si-dup2", "user:重複2", "重複", "approved", "approved", NOW),
        )
        conn.commit()
        conn.close()
        self.write_csv([
            ["現金", "資產負債-資產", "classified"],
            ["重複", "資產負債-資產", "classified"],
            ["唔存在", "資產負債-資產", "classified"],
        ])
        result = imp.classify(self.db, self.csv, actor="tester", apply=True)
        self.assertEqual([row["account_name"] for row in result["ambiguous"]], ["重複"])
        self.assertEqual([row["account_name"] for row in result["unmatched"]], ["唔存在"])
        self.assertEqual(result["counts"], {"set": 1})

    def test_schema_defaults_to_unclassified_and_rejects_unknown_status(self):
        row = self.query(
            "SELECT classification_status FROM standard_items "
            "WHERE item_key='user:現金'"
        )[0]
        self.assertEqual(row["classification_status"], "unclassified")
        conn = sqlite3.connect(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE standard_items SET classification_status='unknown' "
                "WHERE item_key='user:現金'"
            )
        conn.close()

    def test_csv_errors_block_every_write(self):
        self.write_csv([
            ["現金", "資產負債-資產", "classified"],
            ["定期存款", "", "classified"],
        ])
        result = imp.classify(self.db, self.csv, actor="tester", apply=True)
        self.assertTrue(result["errors"])
        self.assertFalse(result["applied"])
        rows = self.query("SELECT category FROM standard_items")
        self.assertTrue(all(row["category"] is None for row in rows))


if __name__ == "__main__":
    unittest.main()
