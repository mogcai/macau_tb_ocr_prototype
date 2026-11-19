"""Tests for P2-C statement segmentation and row assignment."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.build_statement_segmentation import apply_plan  # noqa: E402
from tools.build_v3_db import apply_schema  # noqa: E402


class StatementSegmentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = ON")
        apply_schema(conn)
        conn.execute(
            "INSERT INTO gazette_issues(gazette_issue_id, issue_key, discovered_at) VALUES(?,?,?)",
            ("G1", "2026-99", "2026-11-19T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO source_documents("
            "source_document_id, gazette_issue_id, document_key, storage_uri, sha256, acquired_at"
            ") VALUES(?,?,?,?,?,?)",
            ("D1", "G1", "doc:99", "/tmp/99.pdf", "a" * 64, "2026-11-19T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO document_pages("
            "document_page_id, source_document_id, pdf_page, page_role, "
            "classification_status, classification_source"
            ") VALUES(?,?,?,?,?,?)",
            ("P1", "D1", 1, "financial_statement", "approved", "human"),
        )
        conn.execute(
            "INSERT INTO reporting_entities("
            "entity_id, entity_key, entity_type, canonical_zh, registry_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("E1", "bank:test", "bank", "測試銀行", "approved", "2026-11-19T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO extraction_runs(run_id, source_document_id, run_status) VALUES(?,?,?)",
            ("R1", "D1", "completed"),
        )
        conn.execute(
            "INSERT INTO page_extractions("
            "page_extraction_id, run_id, document_page_id, source_document_id, "
            "ocr_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("PE1", "R1", "P1", "D1", "completed", "2026-11-19T00:00:00Z"),
        )
        for row_id, order, label, role in (
            ("ER1", 1, "現金", "heading"),
            ("ER2", 2, "澳門元", "item"),
        ):
            conn.execute(
                "INSERT INTO extracted_rows("
                "extracted_row_id, page_extraction_id, document_page_id, run_id, "
                "row_disposition, page_row_order, label_raw, label_language, "
                "debit_raw, debit_parsed_minor, debit_value_state, credit_value_state, "
                "row_type_proposed, parent_context_status, created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row_id, "PE1", "P1", "R1", "unassigned", order,
                    label, "zh-Hant", "1.00", 100, "amount", "blank",
                    role, "unknown", "2026-11-19T00:00:00Z",
                ),
            )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _plan(self) -> list[dict]:
        return [{
            "statement_id": "S1",
            "identity_key": "bank:test|2026-03-31|balance_sheet|bank",
            "entity": {
                "entity_id": "E1",
                "entity_key": "bank:test",
                "entity_type": "bank",
            },
            "legacy_statement_id": "legacy:S1",
            "legacy_statement": {
                "publication_date": "2026-04-01",
                "statement_date": "2026-03-31",
                "bank_name_raw": "測試銀行",
            },
            "issue_key": "2026-99",
            "source_document_id": "D1",
            "run_id": "R1",
            "entity_name_raw": "測試銀行",
            "pages": [{"document_page_id": "P1", "pdf_page": 1}],
            "statement_extraction_id": "SE1",
            "row_count": 2,
        }]

    def test_apply_creates_statement_rows_and_assigns_raw_rows(self) -> None:
        apply_plan(self.db, self._plan(), "tester")
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM statement_pages").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM statement_extractions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM statement_rows").fetchone()[0], 2)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM extracted_rows WHERE row_disposition='included'").fetchone()[0],
            2,
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1)
        statement = conn.execute(
            "SELECT reporting_entity_id, statement_type, adopted_statement_extraction_id "
            "FROM statements WHERE statement_id='S1'"
        ).fetchone()
        self.assertEqual(statement[0], "E1")
        self.assertEqual(statement[1], "balance_sheet")
        self.assertIsNone(statement[2])
        conn.close()

        # Idempotent re-run.
        apply_plan(self.db, self._plan(), "tester")
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM statement_rows").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
