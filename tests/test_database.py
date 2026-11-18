"""Tests for the SQLite integration importer."""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_database import (  # noqa: E402
    bank_key_for,
    build_database,
    load_bank_aliases,
    resolve_bank,
)


DOC_FIELDS = [
    "pdf_page", "printed_page", "bank_name", "statement_date",
    "publication_date", "bank_name_source", "metadata_status",
]
ROW_FIELDS = [
    "pdf_page", "printed_page", "bank_name", "statement_date",
    "publication_date", "label_raw", "label_standard",
    "label_standard_id", "label_standard_status", "label_role",
    "parent_label_standard", "label_path", "debit_raw", "credit_raw",
    "debit_amount", "credit_amount", "review_reason", "review_status",
]


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def make_run(
    root: Path,
    pages: list[dict],
    app_version: str = "0.4.0-test",
    source_pdf: str = "test.pdf",
) -> Path:
    """Create a minimal OCR run directory.

    Each page dict accepts metadata plus ``rows`` (a list of row dicts).
    """
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    docs: list[dict] = []
    rows: list[dict] = []
    for page in pages:
        page_number = page["pdf_page"]
        docs.append({
            "pdf_page": page_number,
            "printed_page": page.get("printed_page", str(100 + page_number)),
            "bank_name": page.get("bank_name", "TEST BANK"),
            "statement_date": page.get("statement_date", "2026-09-30"),
            "publication_date": page.get("publication_date", "2026-11-11"),
            "bank_name_source": page.get("bank_name_source", "pdf_text"),
            "metadata_status": page.get("metadata_status", "metadata_extracted"),
        })
        for row in page.get("rows", []):
            rows.append({
                "pdf_page": page_number,
                "printed_page": str(100 + page_number),
                "bank_name": page.get("bank_name", "TEST BANK"),
                "statement_date": page.get("statement_date", "2026-09-30"),
                "publication_date": page.get("publication_date", "2026-11-11"),
                **row,
            })
    write_csv(run_dir / "documents.csv", DOC_FIELDS, docs)
    write_csv(run_dir / "tb_rows.csv", ROW_FIELDS, rows)
    (run_dir / "run_report.json").write_text(
        json.dumps({
            "source_pdf": source_pdf,
            "app_version": app_version,
            "ocr_engine": "vision",
        }),
        encoding="utf-8",
    )
    return run_dir


def simple_rows(prefix: str = "", count: int = 2) -> list[dict]:
    return [
        {
            "label_raw": f"{prefix}現金",
            "label_standard": "現金",
            "label_standard_id": "zh_046",
            "label_standard_status": "exact",
            "label_role": "parent",
            "debit_amount": "1.00",
            "credit_amount": "",
            "review_status": "auto_check_pass",
        },
        {
            "label_raw": f"{prefix}-澳門元",
            "label_standard": "澳門元",
            "label_standard_id": "zh_044",
            "label_standard_status": "exact",
            "label_role": "child",
            "parent_label_standard": "現金",
            "label_path": "現金 > 澳門元",
            "debit_amount": "1.00",
            "credit_amount": "",
            "review_status": "auto_check_pass",
        },
    ][:count]


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "test.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_missing_publication_date_is_derived_within_run(self):
        run = make_run(self.root, [
            {"pdf_page": 1, "bank_name": "BANK A", "publication_date": "",
             "rows": simple_rows("A")},
            {"pdf_page": 2, "bank_name": "BANK B",
             "publication_date": "2026-11-11", "rows": simple_rows("B")},
        ])
        report = build_database(self.db, [run])
        rows = self.query(
            "SELECT bank_name_raw, publication_date, publication_date_source "
            "FROM statements ORDER BY bank_name_raw"
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["publication_date"] == "2026-11-11" for row in rows))
        self.assertEqual(rows[0]["publication_date_source"], "derived_from_run")
        self.assertEqual(rows[1]["publication_date_source"], "documents.csv")
        self.assertEqual(report["totals"]["statements"], 2)

    def test_conflicting_publication_dates_are_not_derived(self):
        run = make_run(self.root, [
            {"pdf_page": 1, "bank_name": "BANK A",
             "publication_date": "2026-11-11", "rows": simple_rows("A")},
            {"pdf_page": 2, "bank_name": "BANK B",
             "publication_date": "2026-11-12", "rows": simple_rows("B")},
            {"pdf_page": 3, "bank_name": "BANK C",
             "publication_date": "", "rows": simple_rows("C")},
        ])
        report = build_database(self.db, [run])
        rows = self.query(
            "SELECT bank_name_raw, publication_date, identity_status "
            "FROM statements ORDER BY bank_name_raw"
        )
        self.assertEqual(rows[2]["publication_date"], "")
        self.assertEqual(rows[2]["identity_status"], "missing_publication_date")
        self.assertTrue(any("conflicting publication_date" in warning
                            for warning in report["warnings"]))

    def test_duplicate_labels_get_occurrence_and_duplicate_status(self):
        run = make_run(self.root, [{
            "pdf_page": 1,
            "bank_name": "BANK A",
            "rows": [
                {"label_raw": "代收帳", "label_standard": "代收帳",
                 "label_standard_status": "exact", "debit_amount": "1.00"},
                {"label_raw": "代收帳", "label_standard": "代收帳",
                 "label_standard_status": "exact", "debit_amount": "2.00"},
            ],
        }])
        build_database(self.db, [run])
        rows = self.query(
            "SELECT label_standard, occurrence, natural_key_status, natural_key "
            "FROM line_items ORDER BY occurrence"
        )
        self.assertEqual([row["occurrence"] for row in rows], [1, 2])
        self.assertEqual([row["natural_key_status"] for row in rows],
                         ["complete", "duplicate"])
        self.assertEqual(len({row["natural_key"] for row in rows}), 2)
        duplicates = self.query(
            "SELECT label_standard, row_count FROM v_natural_key_duplicates"
        )
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["row_count"], 2)

    def test_blank_label_and_unknown_bank_are_flagged(self):
        run = make_run(self.root, [{
            "pdf_page": 1,
            "bank_name": "",
            "rows": [{
                "label_raw": "", "label_standard": "",
                "debit_amount": "5.00", "credit_amount": "",
            }],
        }])
        report = build_database(self.db, [run])
        statement = self.query(
            "SELECT bank_id, bank_key, identity_status FROM statements"
        )[0]
        self.assertIsNone(statement["bank_id"])
        self.assertIsNone(statement["bank_key"])
        self.assertIn("missing_bank_name", statement["identity_status"])
        line = self.query(
            "SELECT natural_key_status, label_path_key, occurrence FROM line_items"
        )[0]
        self.assertEqual(line["natural_key_status"], "missing_label")
        self.assertEqual(line["label_path_key"], "")
        self.assertEqual(line["occurrence"], 1)
        self.assertEqual(report["natural_key_status"]["missing_label"], 1)

    def test_bank_alias_merges_names_across_pages(self):
        run = make_run(self.root, [
            {"pdf_page": 1, "bank_name": "BANCO A, S.A.", "rows": simple_rows("A")},
            {"pdf_page": 2, "bank_name": "BANK A, S.A.", "rows": simple_rows("B")},
        ])
        no_alias = build_database(self.db, [run])
        self.assertEqual(no_alias["totals"]["statements"], 2)

        alias_path = self.root / "aliases.csv"
        write_csv(alias_path, ["alias", "canonical", "bank_key", "note"], [{
            "alias": "BANK A, S.A.",
            "canonical": "BANCO A, S.A.",
            "bank_key": "bank:a",
            "note": "test",
        }])
        aliases = load_bank_aliases(alias_path)
        self.assertIn(bank_key_for("bank a sa"), aliases)
        db2 = self.root / "aliased.sqlite"
        report = build_database(db2, [run], aliases_path=alias_path)
        self.assertEqual(report["totals"]["statements"], 1)
        conn = sqlite3.connect(db2)
        try:
            row = conn.execute(
                "SELECT bank_key, bank_name_raw, page_count FROM statements"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "bank:a")
        self.assertEqual(row[1], "BANCO A, S.A.")
        self.assertEqual(row[2], 2)

    def test_combined_bilingual_name_uses_alias(self):
        alias_path = self.root / "aliases.csv"
        write_csv(alias_path, ["alias", "canonical", "bank_key", "note"], [
            {"alias": "郵政儲金局", "canonical": "郵政儲金局", "bank_key": "",
             "note": ""},
            {"alias": "CAIXA ECONÓMICA POSTAL", "canonical": "郵政儲金局",
             "bank_key": "", "note": ""},
        ])
        aliases = load_bank_aliases(alias_path)
        bank_key, display = resolve_bank(
            "郵政儲金局 | CAIXA ECONÓMICA POSTAL", aliases
        )
        self.assertEqual(bank_key, bank_key_for("郵政儲金局"))
        self.assertEqual(display, "郵政儲金局")

    def test_bank_name_with_comma_is_not_truncated(self):
        run = make_run(self.root, [{
            "pdf_page": 1,
            "bank_name": "BANK X, S.A. | SUCURSAL DE MACAU",
            "rows": simple_rows(),
        }])
        build_database(self.db, [run])
        row = self.query("SELECT bank_name_raw FROM statements")[0]
        self.assertEqual(row["bank_name_raw"], "BANK X, S.A. | SUCURSAL DE MACAU")

    def test_reimport_is_idempotent_and_replace(self):
        run = make_run(self.root, [{
            "pdf_page": 1, "bank_name": "BANK A", "rows": simple_rows(),
        }])
        first = build_database(self.db, [run])
        self.assertEqual(first["runs"][0]["status"], "imported")
        second = build_database(self.db, [run])
        self.assertEqual(second["runs"][0]["status"], "skipped_existing")
        self.assertEqual(second["totals"]["line_items"], 2)

        replaced = build_database(self.db, [run], replace=True)
        self.assertEqual(replaced["runs"][0]["status"], "replaced")
        self.assertEqual(replaced["totals"]["runs"], 1)
        self.assertEqual(replaced["totals"]["line_items"], 2)

    def test_latest_import_wins_for_same_paper_key(self):
        run_one = make_run(self.root / "one", [{
            "pdf_page": 1, "bank_name": "BANK A", "rows": [{
                "label_raw": "現金", "label_standard": "現金",
                "label_standard_status": "exact", "debit_amount": "1.00",
            }],
        }])
        run_two = make_run(self.root / "two", [{
            "pdf_page": 1, "bank_name": "BANK A", "rows": [{
                "label_raw": "現金", "label_standard": "現金",
                "label_standard_status": "exact", "debit_amount": "2.00",
            }],
        }])
        report = build_database(self.db, [run_one, run_two])
        self.assertEqual(report["totals"]["runs"], 2)
        self.assertEqual(report["totals"]["statements"], 2)
        current = self.query(
            "SELECT COUNT(*) AS statements FROM v_current_statements"
        )[0]["statements"]
        self.assertEqual(current, 1)
        amount = self.query(
            "SELECT debit_amount FROM v_current_line_items"
        )[0]["debit_amount"]
        self.assertEqual(amount, "2.00")
        self.assertTrue(any("more than one run" in warning
                            for warning in report["warnings"]))

    def test_documents_csv_is_optional(self):
        run = make_run(self.root, [{
            "pdf_page": 1,
            "bank_name": "BANK A",
            "publication_date": "2026-11-11",
            "rows": simple_rows(),
        }])
        (run / "documents.csv").unlink()
        report = build_database(self.db, [run])
        self.assertEqual(report["totals"]["pages"], 1)
        self.assertEqual(report["totals"]["statements"], 1)
        self.assertTrue(any("documents.csv missing" in warning
                            for warning in report["warnings"]))
        row = self.query(
            "SELECT bank_name_raw, publication_date, publication_date_source "
            "FROM statements"
        )[0]
        self.assertEqual(row["bank_name_raw"], "BANK A")
        self.assertEqual(row["publication_date"], "2026-11-11")
        self.assertEqual(row["publication_date_source"], "tb_rows.csv")

    def test_views_expose_current_rows(self):
        run = make_run(self.root, [{
            "pdf_page": 1, "bank_name": "BANK A", "rows": simple_rows(),
        }])
        report = build_database(self.db, [run])
        self.assertEqual(report["totals"]["pages"], 1)
        self.assertEqual(
            self.query("SELECT COUNT(*) FROM v_balance_sheet")[0][0], 2
        )
        page = self.query(
            "SELECT pdf_page, bank_name, statement_id FROM v_page_index"
        )[0]
        self.assertEqual(page["pdf_page"], 1)
        self.assertEqual(page["bank_name"], "BANK A")
        self.assertTrue(page["statement_id"])
        index = self.query(
            "SELECT row_count, missing_label_rows, duplicate_key_rows, "
            "debit_total_cents FROM v_statement_index"
        )[0]
        self.assertEqual(tuple(index), (2, 0, 0, 200))
        current = self.query("SELECT COUNT(*) FROM v_current_line_items")[0][0]
        all_rows = self.query("SELECT COUNT(*) FROM v_all_line_items")[0][0]
        self.assertEqual(current, 2)
        self.assertEqual(all_rows, 2)

    def test_empty_label_path_gets_parent_path(self):
        run = make_run(self.root, [{
            "pdf_page": 1,
            "bank_name": "BANK A",
            "rows": [{
                "label_raw": "-澳門元", "label_standard": "澳門元",
                "label_standard_status": "exact", "label_role": "child",
                "parent_label_standard": "現金", "label_path": "",
                "debit_amount": "1.00",
            }],
        }])
        build_database(self.db, [run])
        row = self.query("SELECT label_path, label_path_key FROM line_items")[0]
        self.assertEqual(row["label_path"], "現金 > 澳門元")
        self.assertEqual(row["label_path_key"], "現金 > 澳門元")


if __name__ == "__main__":
    unittest.main()
