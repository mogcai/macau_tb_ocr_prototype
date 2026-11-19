"""Tests for applying reviewed P2-B entity reconciliation decisions."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.apply_entity_reconciliation_decisions import (  # noqa: E402
    apply_rows,
    entity_type_for,
    normalize_name,
)
from tools.build_v3_db import apply_schema  # noqa: E402


class EntityReconciliationDecisionTests(unittest.TestCase):
    def test_normalize_name(self) -> None:
        self.assertEqual(normalize_name("BANCO  TESTE, S.A."), "bancotestesa")
        self.assertEqual(normalize_name("郵政儲金局"), "郵政儲金局")

    def test_securities_entity_is_non_bank(self) -> None:
        self.assertEqual(
            entity_type_for({"display_name": "海通國際證券有限公司(澳門分行)"}),
            "non_bank",
        )
        self.assertEqual(
            entity_type_for({"display_name": "澳門發展銀行股份有限公司"}),
            "bank",
        )


class ApplyEntityReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = ON")
        apply_schema(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _rows(self) -> list[dict[str, str]]:
        return [
            {
                "entity_id": "1",
                "entity_key": "bank:test",
                "display_name": "測試銀行",
                "canonical_zh": "測試銀行",
                "canonical_pt": "",
                "entity_type": "bank",
                "registry_status": "provisional",
                "reviewer_decision": "approve_entity",
                "reviewer_survivor_entity_id": "",
                "reviewer_note": "",
                "proposed_survivor_entity_id": "",
            },
            {
                "entity_id": "2",
                "entity_key": "bank:test-old",
                "display_name": "測試銀行舊名",
                "canonical_zh": "測試銀行舊名",
                "canonical_pt": "",
                "entity_type": "bank",
                "registry_status": "provisional",
                "reviewer_decision": "approve_merge",
                "reviewer_survivor_entity_id": "1",
                "reviewer_note": "historical name",
                "proposed_survivor_entity_id": "1",
            },
            {
                "entity_id": "3",
                "entity_key": "bank:junk",
                "display_name": "junk header",
                "canonical_zh": "",
                "canonical_pt": "",
                "entity_type": "bank",
                "registry_status": "provisional",
                "reviewer_decision": "exclude_entity",
                "reviewer_survivor_entity_id": "",
                "reviewer_note": "not an entity",
                "proposed_survivor_entity_id": "",
            },
        ]

    def test_apply_entity_merge_and_exclusion(self) -> None:
        plan = apply_rows(self.db, self._rows(), "tester")
        self.assertEqual(len(plan), 3)
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM reporting_entities").fetchone()[0], 2
        )
        merged = conn.execute(
            "SELECT registry_status, merged_into_entity_id FROM reporting_entities "
            "WHERE entity_key='bank:test-old'"
        ).fetchone()
        self.assertEqual(merged[0], "merged")
        self.assertIsNotNone(merged[1])
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM reporting_entity_aliases").fetchone()[0],
            2,
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 3)
        conn.close()

        # Re-run must not duplicate entities, aliases or audit events.
        second = apply_rows(self.db, self._rows(), "tester")
        self.assertEqual(len(second), 3)
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM reporting_entities").fetchone()[0], 2
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM reporting_entity_aliases").fetchone()[0], 2
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 3)
        conn.close()


if __name__ == "__main__":
    unittest.main()
