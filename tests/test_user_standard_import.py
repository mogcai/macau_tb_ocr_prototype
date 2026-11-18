"""Tests for importing the user's standard parent/child label CSV."""
from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_database import make_run  # noqa: E402
from tests.test_name_registry import billing_rows  # noqa: E402
from tools import import_user_standard as imp  # noqa: E402
from tools.build_database import build_database  # noqa: E402


def write_user_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["", "parent_zh", "child_zh", "parent_pt", "child_pt", "", ""])
        writer.writerows(rows)


class UserStandardImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "test.sqlite"
        self.run = make_run(self.root / "run", [{
            "pdf_page": 1, "bank_name": "BANK A", "rows": billing_rows(),
        }])
        build_database(self.db, [self.run], publish=True)
        self.csv = self.root / "user.csv"
        write_user_csv(self.csv, [
            ["", "現金", "", "CAIXA", "", "", ""],
            ["", "現金", "澳門元", "CAIXA", "PATACAS", "", ""],
        ])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_read_raw_csv_corrects_shifted_reserve_block(self):
        csv_path = self.root / "shifted.csv"
        write_user_csv(csv_path, [
            ["", "現金", "", "CAIXA", "", "", ""],
            ["", "股本", "", "FUNDO DE ΜΑΝΕΙΟ", "", "", ""],
            ["", "營運資金", "", "RESERVA LEGAL", "", "", ""],
            ["", "其他儲備", "", "RESERVA DA REAVALIAÇÃO", "", "", ""],
            ["", "總收人", "", "CUSTOS POR NATUREZA", "", "", ""],
        ])
        rows = imp.read_raw_csv(csv_path)
        by_name = {row["parent_zh"]: row for row in rows}
        self.assertEqual(by_name["股本"]["parent_pt"], "CAPITAL")
        self.assertEqual(by_name["營運資金"]["parent_pt"], "FUNDO DE MANEIO")
        self.assertEqual(by_name["總收入"]["parent_pt"], "PROVEITOS POR NATUREZA")
        self.assertIn("重估儲備", by_name)
        self.assertEqual(by_name["重估儲備"]["parent_pt"], "RESERVA DA REAVALIAÇÃO")
        self.assertEqual(by_name["股本"]["status"], "provisional")

    def test_import_maps_parent_and_child_paths(self):
        result = imp.import_standards(self.db, self.csv)
        self.assertEqual(result["standards"], 2)
        self.assertGreater(result["mapping"]["parent_exact"], 0)
        self.assertGreater(result["mapping"]["child_path_exact"], 0)
        mapped = self.query(
            "SELECT label_standard, parent_label_standard, standard_zh, "
            "standard_pt, user_standard_match_status "
            "FROM v_line_item_user_standard "
            "WHERE label_standard IN ('現金', 'Caixa', '澳門元', 'Patacas')"
        )
        rows = {row["label_standard"]: row for row in mapped}
        self.assertEqual(rows["現金"]["standard_zh"], "現金")
        self.assertEqual(rows["Caixa"]["standard_pt"], "CAIXA")
        self.assertEqual(rows["澳門元"]["standard_zh"], "澳門元")
        self.assertEqual(rows["Patacas"]["standard_pt"], "PATACAS")
        self.assertEqual(rows["澳門元"]["user_standard_match_status"],
                         "child_path_exact")
        unmapped = self.query(
            "SELECT * FROM v_line_item_user_standard "
            "WHERE label_raw = '新科目'"
        )[0]
        self.assertEqual(unmapped["user_standard_match_status"], "unmapped")

    def test_cleaned_csv_import_preserves_user_status_and_new_rows(self):
        cleaned = self.root / "cleaned.csv"
        with cleaned.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "source_row", "parent_zh", "child_zh", "parent_pt",
                "child_pt", "status", "note",
            ])
            writer.writeheader()
            writer.writerows([
                {"source_row": "2", "parent_zh": "現金", "child_zh": "",
                 "parent_pt": "CAIXA", "child_pt": "", "status": "approved",
                 "note": ""},
                {"source_row": "3", "parent_zh": "現金", "child_zh": "澳門元",
                 "parent_pt": "CAIXA", "child_pt": "PATACAS",
                 "status": "approved", "note": ""},
                {"source_row": "67", "parent_zh": "銀行貸款", "child_zh": "",
                 "parent_pt": "", "child_pt": "", "status": "approved",
                 "note": "葡文之後再補"},
            ])
        result = imp.import_standards(
            self.db, cleaned, from_cleaned=True
        )
        self.assertEqual(result["standards"], 3)
        statuses = {
            row["canonical_zh"]: row["status"]
            for row in self.query(
                "SELECT canonical_zh, status FROM standard_labels"
            )
        }
        self.assertEqual(statuses["銀行貸款"], "approved")

    def test_repair_alias_is_added_without_new_standard(self):
        imp.import_standards(self.db, self.csv)
        repairs = self.root / "repairs.csv"
        with repairs.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "raw_name", "standard_key", "language", "note",
            ])
            writer.writeheader()
            writer.writerow({
                "raw_name": "CAIXA現金",
                "standard_key": "user:現金",
                "language": "zh-Hant",
                "note": "merged string",
            })
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            stats = imp.apply_repairs(conn, repairs)
            conn.commit()
            self.assertEqual(stats["aliases"], 1)
            alias = conn.execute(
                "SELECT a.raw_name, s.standard_key "
                "FROM standard_label_aliases a "
                "JOIN standard_labels s ON s.standard_id = a.standard_id "
                "WHERE a.raw_name = 'CAIXA現金'"
            ).fetchone()
            self.assertEqual(alias["standard_key"], "user:現金")
            count = conn.execute(
                "SELECT COUNT(*) FROM standard_labels"
            ).fetchone()[0]
            self.assertEqual(count, 2)
        finally:
            conn.close()

    def test_report_lists_unmapped_names(self):
        imp.import_standards(self.db, self.csv)
        out = self.root / "report.csv"
        result = imp.write_report(self.db, out)
        self.assertGreater(result["unmapped"], 0)
        with out.open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(any(row["raw_name"] == "新科目" for row in rows))


if __name__ == "__main__":
    unittest.main()
