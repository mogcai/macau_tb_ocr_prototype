"""Tests for bulk approval of unambiguous auto-mapped proposals."""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from workbench import service  # noqa: E402
from workbench.config import V3_DB  # noqa: E402
import workbench.db as wdb  # noqa: E402
from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from workbench.main import app  # noqa: E402


class BulkApproveTests(unittest.TestCase):
    def setUp(self) -> None:
        if not V3_DB.is_file():
            self.skipTest("operational v3 database not available")
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        shutil.copyfile(V3_DB, self.db)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _counts(self) -> dict[str, int]:
        conn = sqlite3.connect(self.db)
        try:
            return {
                status: int(count)
                for status, count in conn.execute(
                    "SELECT mapping_status, COUNT(*) FROM statement_row_mappings "
                    "GROUP BY mapping_status"
                )
            }
        finally:
            conn.close()

    def _amount_total(self) -> int:
        conn = sqlite3.connect(self.db)
        try:
            return int(conn.execute(
                "SELECT COALESCE(SUM(debit_minor), 0) + COALESCE(SUM(credit_minor), 0) "
                "FROM statement_rows"
            ).fetchone()[0])
        finally:
            conn.close()

    def _dry_run(self) -> dict:
        return service.bulk_approve_proposed_mappings(
            actor="tester", scope="reviewed", allow_locked=True,
            apply=False, db_path=self.db,
        )

    def test_locked_statements_require_the_flag(self) -> None:
        result = service.bulk_approve_proposed_mappings(
            actor="tester", scope="reviewed", allow_locked=False,
            apply=False, db_path=self.db,
        )
        self.assertEqual(result["approved"], 0)
        self.assertGreater(result["locked_statements_skipped"], 0)
        self.assertEqual(result["statements_in_scope"], 0)

    def _prepare_proposals(self, count: int = 3) -> tuple[str, int]:
        """Flip a few approved mappings back to proposed on the copy.

        The operational database may already be fully approved, so the tests
        create their own candidates instead of relying on live state.
        """
        conn = sqlite3.connect(self.db)
        try:
            found = conn.execute(
                "SELECT sr.statement_id FROM statement_row_mappings m "
                "JOIN statement_rows sr ON sr.statement_row_id=m.statement_row_id "
                "WHERE m.mapping_status='approved' GROUP BY sr.statement_id "
                "HAVING COUNT(*) >= ? ORDER BY sr.statement_id LIMIT 1",
                (count,),
            ).fetchone()
            if found is None:
                self.skipTest("no statement with enough approved mappings")
            statement_id = found[0]
            mapping_ids = [
                row[0] for row in conn.execute(
                    "SELECT m.mapping_id FROM statement_row_mappings m "
                    "JOIN statement_rows sr ON sr.statement_row_id=m.statement_row_id "
                    "WHERE sr.statement_id=? AND m.mapping_status='approved' "
                    "ORDER BY sr.statement_order LIMIT ?",
                    (statement_id, count),
                )
            ]
            conn.executemany(
                "UPDATE statement_row_mappings SET mapping_status='proposed', "
                "reviewed_by=NULL, reviewed_at=NULL WHERE mapping_id=?",
                [(mapping_id,) for mapping_id in mapping_ids],
            )
            conn.commit()
        finally:
            conn.close()
        return statement_id, len(mapping_ids)

    def test_dry_run_reports_but_does_not_write(self) -> None:
        statement_id, prepared = self._prepare_proposals()
        self.assertGreater(prepared, 0)
        before = self._counts()
        result = service.bulk_approve_proposed_mappings(
            actor="tester", statement_ids=[statement_id],
            scope="reviewed", allow_locked=True, apply=False, db_path=self.db,
        )
        self.assertGreater(result["approved"], 0)
        self.assertEqual(result["dry_run"], True)
        self.assertEqual(self._counts(), before)

    def test_apply_is_audited_idempotent_and_keeps_amounts(self) -> None:
        statement_id, prepared = self._prepare_proposals()
        plan = service.bulk_approve_proposed_mappings(
            actor="tester", statement_ids=[statement_id],
            scope="reviewed", allow_locked=True, apply=False, db_path=self.db,
        )
        expected = plan["approved"]
        self.assertGreater(expected, 0)
        self.assertLessEqual(expected, prepared)
        before_amounts = self._amount_total()
        before_counts = self._counts()

        applied = service.bulk_approve_proposed_mappings(
            actor="tester", note="bulk approval test", statement_ids=[statement_id],
            scope="reviewed", allow_locked=True, apply=True, db_path=self.db,
        )
        self.assertEqual(applied["approved"], expected)
        after_counts = self._counts()
        self.assertEqual(
            after_counts.get("approved", 0) - before_counts.get("approved", 0),
            expected,
        )
        self.assertEqual(
            before_counts.get("proposed", 0) - after_counts.get("proposed", 0),
            expected,
        )
        self.assertEqual(self._amount_total(), before_amounts)

        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM statement_row_mappings m "
                "JOIN statement_rows sr ON sr.statement_row_id=m.statement_row_id "
                "WHERE sr.statement_id=? AND m.reviewed_by='tester' "
                "AND m.mapping_status='approved'",
                (statement_id,),
            ).fetchone()[0]
            events = conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE event_type='workbench_mapping_bulk_approved' "
                "AND statement_id=? AND actor_id='tester'",
                (statement_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(row, expected)
        self.assertEqual(events, 1)

        again = service.bulk_approve_proposed_mappings(
            actor="tester", statement_ids=[statement_id],
            scope="reviewed", allow_locked=True, apply=False, db_path=self.db,
        )
        self.assertEqual(again["approved"], 0)
        self.assertGreater(again["skip_counts"].get("already_approved", 0), 0)

    def test_duplicate_item_rows_are_skipped_not_guessed(self) -> None:
        plan = self._dry_run()
        self.assertGreater(
            plan["skip_counts"].get("duplicate_item_in_statement", 0), 0
        )
        examples = [
            item for item in plan["skipped_examples"]
            if item["reason"] == "duplicate_item_in_statement"
        ]
        self.assertTrue(examples)
        statement_id = examples[0]["statement_id"]
        item_id = examples[0]["standard_item_id"]
        approved = service.bulk_approve_proposed_mappings(
            actor="tester", statement_ids=[statement_id],
            scope="reviewed", allow_locked=True, apply=True, db_path=self.db,
        )
        conn = sqlite3.connect(self.db)
        try:
            duplicates = conn.execute(
                "SELECT COUNT(*) FROM statement_row_mappings m "
                "JOIN statement_rows sr ON sr.statement_row_id=m.statement_row_id "
                "WHERE sr.statement_id=? AND m.mapping_status='approved' "
                "AND m.standard_item_id=?",
                (statement_id, item_id),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(duplicates, 0)
        self.assertGreater(
            approved["skip_counts"].get("duplicate_item_in_statement", 0), 0
        )


class BulkApproveApiTests(unittest.TestCase):
    def setUp(self) -> None:
        if not V3_DB.is_file():
            self.skipTest("operational v3 database not available")
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        shutil.copyfile(V3_DB, self.db)
        self.patcher = mock.patch.object(wdb, "V3_DB", self.db)
        self.patcher.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tempdir.cleanup()

    def _pending_with_proposals(self) -> dict:
        for item in service.review_queue():
            if item["proposed_mapping_rows"]:
                return item
        self.skipTest("no pending statement with proposed mappings")

    def test_viewer_cannot_bulk_approve(self) -> None:
        response = self.client.post(
            "/api/review/bulk-approve-proposed",
            json={"statement_ids": None, "scope": "editable"},
            headers={"X-Workbench-Role": "viewer"},
        )
        self.assertEqual(response.status_code, 403)

    def test_reviewer_bulk_approves_one_pending_statement(self) -> None:
        item = self._pending_with_proposals()
        response = self.client.post(
            "/api/review/bulk-approve-proposed",
            json={"statement_ids": [item["statement_id"]], "scope": "editable"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "bulk-tester"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        if body["approved"] == 0:
            self.skipTest("no unambiguous proposals in the selected statement")
        self.assertGreater(body["approved"], 0)
        self.assertFalse(body["dry_run"])

        conn = sqlite3.connect(self.db)
        try:
            approved = conn.execute(
                "SELECT COUNT(*) FROM statement_row_mappings m "
                "JOIN statement_rows sr ON sr.statement_row_id=m.statement_row_id "
                "WHERE sr.statement_id=? AND m.mapping_status='approved' "
                "AND m.reviewed_by='bulk-tester'",
                (item["statement_id"],),
            ).fetchone()[0]
            events = conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE event_type='workbench_mapping_bulk_approved' "
                "AND statement_id=? AND actor_id='bulk-tester'",
                (item["statement_id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(approved, body["approved"])
        self.assertEqual(events, 1)

    def test_review_page_shows_bulk_approve_control(self) -> None:
        page = self.client.get("/review?role=reviewer")
        self.assertEqual(page.status_code, 200)
        self.assertIn("bulk-approve-all", page.text)
        self.assertIn("/api/review/bulk-approve-proposed", page.text)


if __name__ == "__main__":
    unittest.main()