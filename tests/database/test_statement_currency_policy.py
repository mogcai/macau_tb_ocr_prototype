"""Tests for the statement-level MOP / unit-scale policy."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.build_v3_db import apply_schema  # noqa: E402
from tools.set_statement_currency_policy import apply_policy  # noqa: E402


class StatementCurrencyPolicyTests(unittest.TestCase):
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
            "INSERT INTO statements("
            "statement_id, source_document_id, reporting_entity_id, statement_type, "
            "reporting_scope, identity_key, identity_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            ("S1", "D1", "E1", "balance_sheet", "bank", "k1", "proposed", "2026-11-19T00:00:00Z"),
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_dry_run_does_not_write(self) -> None:
        result = apply_policy(
            self.db, currency="MOP", unit_scale=1, reviewer="tester", apply=False
        )
        self.assertEqual(result["would_update"], 1)
        conn = sqlite3.connect(self.db)
        self.assertIsNone(conn.execute("SELECT currency FROM statements").fetchone()[0])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 0)
        conn.close()

    def test_apply_and_idempotent(self) -> None:
        first = apply_policy(
            self.db, currency="MOP", unit_scale=1, reviewer="tester", apply=True
        )
        self.assertEqual(first["would_update"], 1)
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT currency, currency_source, unit_scale, unit_scale_source "
            "FROM statements WHERE statement_id='S1'"
        ).fetchone()
        self.assertEqual(row, ("MOP", "manual", 1, "manual"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1)
        conn.close()

        second = apply_policy(
            self.db, currency="MOP", unit_scale=1, reviewer="tester", apply=True
        )
        self.assertEqual(second["would_update"], 0)
        self.assertEqual(second["would_add_audit_events"], 0)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1)
        conn.close()

    def test_invalid_policy_rejected(self) -> None:
        with self.assertRaises(ValueError):
            apply_policy(self.db, currency="MOP", unit_scale=2, reviewer="tester", apply=False)
        with self.assertRaises(ValueError):
            apply_policy(self.db, currency="MO", unit_scale=1, reviewer="tester", apply=False)


if __name__ == "__main__":
    unittest.main()
