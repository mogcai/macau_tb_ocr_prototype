"""Tests for applying reviewed page classification decisions."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.apply_page_classification_decisions import (  # noqa: E402
    apply_rows,
    corrected_entity_name,
    resolve_decision,
)
from tools.build_v3_db import apply_schema  # noqa: E402


class PageClassificationDecisionTests(unittest.TestCase):
    def test_approve_proposed(self) -> None:
        result = resolve_decision({
            "reviewer_decision": "approve_proposed",
            "reviewer_note": "",
            "proposed_page_role": "balance_sheet",
            "proposed_v3_page_role": "financial_statement",
            "proposed_entity_name": "BANCO TESTE",
        })
        self.assertEqual(result["applied_page_role"], "financial_statement")
        self.assertEqual(result["applied_classification_status"], "approved")
        self.assertEqual(result["reviewed_entity_name"], "BANCO TESTE")

    def test_change_name_with_explanatory_note(self) -> None:
        self.assertEqual(
            corrected_entity_name("應該用郵政儲金局，OCR識別錯"),
            "郵政儲金局",
        )
        result = resolve_decision({
            "reviewer_decision": "change_proposed_name",
            "reviewer_note": "應該用郵政儲金局，OCR識別錯",
            "proposed_page_role": "balance_sheet",
            "proposed_v3_page_role": "financial_statement",
            "proposed_entity_name": "CAIXA ECONÓMICA POSTAL",
        })
        self.assertEqual(result["reviewed_entity_name"], "郵政儲金局")
        self.assertEqual(result["applied_classification_status"], "approved")

    def test_reject_financial_page_becomes_other(self) -> None:
        result = resolve_decision({
            "reviewer_decision": "reject_page",
            "reviewer_note": "銀行年報內容，不是試算表",
            "proposed_page_role": "balance_sheet",
            "proposed_v3_page_role": "financial_statement",
            "proposed_entity_name": "BANCO TESTE",
        })
        self.assertEqual(result["applied_page_role"], "other")
        self.assertEqual(result["applied_classification_status"], "rejected")
        self.assertEqual(result["reviewed_entity_name"], "")

    def test_reject_non_financial_page_keeps_role(self) -> None:
        result = resolve_decision({
            "reviewer_decision": "reject_page",
            "reviewer_note": "not target",
            "proposed_page_role": "shareholder_list",
            "proposed_v3_page_role": "shareholder_list",
            "proposed_entity_name": "",
        })
        self.assertEqual(result["applied_page_role"], "shareholder_list")
        self.assertEqual(result["applied_classification_status"], "rejected")

    def test_missing_note_fails(self) -> None:
        with self.assertRaises(ValueError):
            resolve_decision({
                "reviewer_decision": "reject_page",
                "reviewer_note": "",
                "proposed_page_role": "other",
                "proposed_v3_page_role": "other",
                "proposed_entity_name": "",
            })


class ApplyPageClassificationTests(unittest.TestCase):
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
            ("P1", "D1", 1, "unknown", "proposed", "automatic"),
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_apply_is_idempotent(self) -> None:
        row = {
            "document_page_id": "P1",
            "reviewer_decision": "approve_proposed",
            "reviewer_note": "",
            "proposed_page_role": "balance_sheet",
            "proposed_v3_page_role": "financial_statement",
            "proposed_entity_name": "BANCO TESTE",
            "reviewed_entity_name": "BANCO TESTE",
            "applied_page_role": "financial_statement",
            "applied_classification_status": "approved",
        }
        first = apply_rows(self.db, [row], "tester")
        self.assertEqual(first["updated"], 1)
        self.assertEqual(first["audit_events"], 1)
        second = apply_rows(self.db, [row], "tester")
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(second["audit_events"], 0)

        conn = sqlite3.connect(self.db)
        row_db = conn.execute(
            "SELECT page_role, classification_status, classification_source, notes "
            "FROM document_pages WHERE document_page_id='P1'"
        ).fetchone()
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        conn.close()
        self.assertEqual(row_db[0], "financial_statement")
        self.assertEqual(row_db[1], "approved")
        self.assertEqual(row_db[2], "human")
        self.assertEqual(row_db[3].count("P2-A decision="), 1)
        self.assertEqual(audit_count, 1)


if __name__ == "__main__":
    unittest.main()
