"""Tests for applying reviewed statement adoption decisions."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.apply_statement_adoption_decisions import apply_rows  # noqa: E402
from tools.build_v3_db import apply_schema  # noqa: E402


class StatementAdoptionTests(unittest.TestCase):
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
            "INSERT INTO reporting_entities("
            "entity_id, entity_key, entity_type, canonical_zh, registry_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("E1", "bank:test", "bank", "測試銀行", "approved", "2026-11-19T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO extraction_runs(run_id, source_document_id, run_status) VALUES(?,?,?)",
            ("R1", "D1", "completed"),
        )
        for statement_id in ("S1", "S2"):
            conn.execute(
                "INSERT INTO statements("
                "statement_id, source_document_id, reporting_entity_id, statement_type, "
                "reporting_scope, identity_key, identity_status, created_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (statement_id, "D1", "E1", "balance_sheet", "bank", f"k:{statement_id}", "proposed", "2026-11-19T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO statement_extractions("
                "statement_extraction_id, statement_id, run_id, source_document_id, "
                "extraction_status, created_at"
                ") VALUES(?,?,?,?,?,?)",
                (f"SE{statement_id[-1]}", statement_id, "R1", "D1", "completed", "2026-11-19T00:00:00Z"),
            )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _rows(self) -> list[dict[str, str]]:
        return [
            {
                "statement_id": "S1",
                "entity_name": "測試銀行",
                "reviewer_decision": "adopt",
                "statement_extraction_id": "SE1",
                "reviewer_note": "",
            },
            {
                "statement_id": "S2",
                "entity_name": "測試銀行",
                "reviewer_decision": "needs_further_review",
                "statement_extraction_id": "SE2",
                "reviewer_note": "manual review",
            },
        ]

    def test_adopt_only_selected_statement(self) -> None:
        result = apply_rows(self.db, self._rows(), "tester", apply=True)
        self.assertEqual(result["adopted_now"], 1)
        self.assertEqual(result["held"], 1)
        conn = sqlite3.connect(self.db)
        adopted = conn.execute(
            "SELECT adopted_statement_extraction_id, statement_status "
            "FROM statements WHERE statement_id='S1'"
        ).fetchone()
        held = conn.execute(
            "SELECT adopted_statement_extraction_id, statement_status "
            "FROM statements WHERE statement_id='S2'"
        ).fetchone()
        self.assertEqual(adopted, ("SE1", "active"))
        self.assertEqual(held, (None, "proposed"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1)
        conn.close()

        second = apply_rows(self.db, self._rows(), "tester", apply=True)
        self.assertEqual(second["adopted_now"], 0)
        self.assertEqual(second["already_adopted"], 1)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
