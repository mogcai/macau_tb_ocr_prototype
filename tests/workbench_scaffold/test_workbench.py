"""Tests for the Workbench scaffold permissions and DB guard."""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from workbench.config import SNAPSHOT_DB, V3_DB  # noqa: E402
import workbench.db as wdb  # noqa: E402
from workbench.db import connect_v3, is_snapshot  # noqa: E402
from workbench.main import app  # noqa: E402
from workbench.permissions import can_adopt, can_publish, can_verify  # noqa: E402


class PermissionTests(unittest.TestCase):
    def test_adoption_and_publish_are_separate(self) -> None:
        self.assertTrue(can_adopt("adopter"))
        self.assertFalse(can_publish("adopter"))
        self.assertTrue(can_publish("publisher"))
        self.assertFalse(can_adopt("publisher"))
        self.assertTrue(can_verify("reviewer"))
        self.assertFalse(can_adopt("reviewer"))


class SnapshotGuardTests(unittest.TestCase):
    def test_snapshot_write_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "snapshot.sqlite"
            with mock.patch("workbench.db.SNAPSHOT_DB", snapshot):
                snapshot.touch()
                self.assertTrue(is_snapshot(snapshot))
                with self.assertRaises(PermissionError):
                    connect_v3(snapshot, write=True)

    def test_actual_snapshot_is_read_only(self) -> None:
        if not SNAPSHOT_DB.is_file():
            self.skipTest("MVP snapshot not built")
        self.assertFalse(SNAPSHOT_DB.stat().st_mode & 0o222)


@unittest.skipUnless(V3_DB.is_file(), "operational v3 DB not available")
class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_and_summary(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertGreaterEqual(body["statements_active"], 0)
        summary = self.client.get("/api/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["db_path"], str(V3_DB))

    def test_adopt_requires_adopter_role(self) -> None:
        held = self.client.get("/api/statements?status=held&limit=1")
        self.assertEqual(held.status_code, 200)
        if not held.json():
            self.skipTest("no held statement")
        statement_id = held.json()[0]["statement_id"]
        response = self.client.post(
            f"/api/statements/{statement_id}/adopt",
            headers={"X-Workbench-Role": "viewer"},
        )
        self.assertEqual(response.status_code, 403)
        not_ready = self.client.post(
            f"/api/statements/{statement_id}/adopt",
            headers={"X-Workbench-Role": "adopter"},
        )
        self.assertIn(not_ready.status_code, (409, 404))

    def test_publish_is_separate_and_not_implemented(self) -> None:
        held = self.client.get("/api/statements?status=held&limit=1")
        if not held.json():
            self.skipTest("no held statement")
        statement_id = held.json()[0]["statement_id"]
        forbidden = self.client.post(
            f"/api/statements/{statement_id}/publish",
            headers={"X-Workbench-Role": "adopter"},
        )
        self.assertEqual(forbidden.status_code, 403)
        not_implemented = self.client.post(
            f"/api/statements/{statement_id}/publish",
            headers={"X-Workbench-Role": "publisher"},
        )
        self.assertEqual(not_implemented.status_code, 501)

    def test_role_switch_cookie(self) -> None:
        switch = self.client.get("/set-role/reviewer?next=/review", follow_redirects=False)
        self.assertEqual(switch.status_code, 303)
        page = self.client.get("/review")
        self.assertEqual(page.status_code, 200)
        self.assertIn("reviewer", page.text)
        bad = self.client.get("/set-role/nope", follow_redirects=False)
        self.assertEqual(bad.status_code, 400)

    def test_review_queue_and_rows(self) -> None:
        queue = self.client.get("/api/review/queue")
        self.assertEqual(queue.status_code, 200)
        self.assertGreater(len(queue.json()), 0)
        statement_id = queue.json()[0]["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows")
        self.assertEqual(rows.status_code, 200)
        self.assertGreater(len(rows.json()), 0)
        detail = self.client.get(f"/review/{statement_id}")
        self.assertEqual(detail.status_code, 200)


@unittest.skipUnless(V3_DB.is_file(), "operational v3 DB not available")
class ReviewWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        shutil.copyfile(V3_DB, self.db)
        self.patcher = mock.patch.object(wdb, "V3_DB", self.db)
        self.patcher.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tempdir.cleanup()

    def test_verify_requires_reviewer_role(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        statement_id = queue[0]["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        response = self.client.post(
            f"/api/review/{statement_id}/rows/{rows[0]['statement_row_id']}/verify",
            json={
                "label": rows[0]["effective_label"],
                "debit": "1.00",
                "credit": "",
                "debit_state": "amount",
                "credit_state": "blank",
                "note": "test",
            },
            headers={"X-Workbench-Role": "viewer"},
        )
        self.assertEqual(response.status_code, 403)

    def test_verify_writes_field_verification_and_audit(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        statement_id = queue[0]["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        row = rows[0]
        conn = sqlite3.connect(self.db)
        before_events = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type='workbench_row_verified'"
        ).fetchone()[0]
        conn.close()
        response = self.client.post(
            f"/api/review/{statement_id}/rows/{row['statement_row_id']}/verify",
            json={
                "label": row["effective_label"],
                "debit": "1.00",
                "credit": "",
                "debit_state": "amount",
                "credit_state": "blank",
                "note": "test correction",
            },
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM field_verifications WHERE verified_by='tester'").fetchone()[0],
            5,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='workbench_row_verified'").fetchone()[0],
            before_events + 1,
        )
        conn.close()

    def test_mark_reviewed_then_adopt(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        ready = [
            item for item in queue
            if item["review_bucket"] == "ready_for_adoption" and item["mapping_ready"]
        ]
        if not ready:
            self.skipTest("no ready held statement")
        statement_id = ready[0]["statement_id"]
        reviewed = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "reviewed", "note": "reviewed in test"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(reviewed.status_code, 200)
        adopt = self.client.post(
            f"/api/statements/{statement_id}/adopt",
            headers={"X-Workbench-Role": "adopter", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(adopt.status_code, 200)
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT review_status FROM statement_reviews WHERE statement_id=?", (statement_id,)).fetchone()[0],
            "reviewed",
        )
        self.assertEqual(
            conn.execute("SELECT statement_status FROM statements WHERE statement_id=?", (statement_id,)).fetchone()[0],
            "active",
        )
        conn.close()

    def test_reviewed_statement_is_read_only_until_reopened(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        ready = next((item for item in queue if item["mapping_ready"]), None)
        if ready is None:
            self.skipTest("no mapping-ready held statement")
        statement_id = ready["statement_id"]
        reviewed = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "reviewed", "note": "reviewed"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(reviewed.status_code, 200)
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        payload = {
            "label": rows[0]["effective_label"],
            "debit": "1.00",
            "credit": "",
            "debit_state": "amount",
            "credit_state": "blank",
            "note": "correction",
        }
        blocked = self.client.post(
            f"/api/review/{statement_id}/rows/{rows[0]['statement_row_id']}/verify",
            json=payload,
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(blocked.status_code, 409)
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT review_status FROM statement_reviews WHERE statement_id=?", (statement_id,)).fetchone()[0],
            "reviewed",
        )
        conn.close()

        reopened = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "needs_rework", "note": "reopen"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(reopened.status_code, 200)
        allowed = self.client.post(
            f"/api/review/{statement_id}/rows/{rows[0]['statement_row_id']}/verify",
            json=payload,
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(allowed.status_code, 200)
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT review_status FROM statement_reviews WHERE statement_id=?", (statement_id,)).fetchone()[0],
            "needs_rework",
        )
        conn.close()

    def test_saved_values_are_returned_by_detail_rows(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        statement_id = queue[0]["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        row = rows[0]
        saved = self.client.post(
            f"/api/review/{statement_id}/rows/{row['statement_row_id']}/verify",
            json={
                "label": "測試標籤",
                "debit": "1.00",
                "credit": "",
                "debit_state": "amount",
                "credit_state": "blank",
                "note": "fresh value test",
            },
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(saved.status_code, 200)
        refreshed = self.client.get(f"/api/review/{statement_id}/rows").json()
        updated = next(item for item in refreshed if item["statement_row_id"] == row["statement_row_id"])
        self.assertEqual(updated["effective_label"], "測試標籤")
        self.assertEqual(updated["effective_debit_minor"], 100)
        self.assertEqual(updated["effective_debit_state"], "amount")
        # The held/statement view must use the same effective values.
        statement_rows = self.client.get(f"/api/statements/{statement_id}/rows").json()
        statement_row = next(item for item in statement_rows if item["statement_row_id"] == row["statement_row_id"])
        self.assertEqual(statement_row["effective_label"], "測試標籤")
        self.assertEqual(statement_row["effective_debit_minor"], 100)

    def test_standard_item_mapping_is_visible_and_approvable(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        statement_id = queue[0]["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        row = next(item for item in rows if item["mapping_candidates"])
        self.assertIn(row["mapping_status"], ("unique", "conflict"))
        self.assertTrue(row["standard_item_key"])
        item_id = row["mapping_candidates"][0]["standard_item_id"]

        forbidden = self.client.post(
            f"/api/review/{statement_id}/rows/{row['statement_row_id']}/map",
            json={"standard_item_id": item_id, "note": "test mapping"},
            headers={"X-Workbench-Role": "viewer"},
        )
        self.assertEqual(forbidden.status_code, 403)

        approved = self.client.post(
            f"/api/review/{statement_id}/rows/{row['statement_row_id']}/map",
            json={"standard_item_id": item_id, "note": "test mapping"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["mapping_status"], "approved")

        refreshed = self.client.get(f"/api/review/{statement_id}/rows").json()
        updated = next(item for item in refreshed if item["statement_row_id"] == row["statement_row_id"])
        self.assertEqual(updated["mapping_status"], "approved")
        self.assertEqual(updated["standard_item_id"], item_id)

        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM statement_row_mappings "
                "WHERE statement_row_id=? AND mapping_status='approved'",
                (row["statement_row_id"],),
            ).fetchone()[0],
            1,
        )
        conn.close()

    def test_mapping_conflict_or_unmatched_blocks_review(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        blocked = next((item for item in queue if not item["mapping_ready"]), None)
        if blocked is None:
            self.skipTest("no mapping-blocked statement")
        response = self.client.post(
            f"/api/review/{blocked['statement_id']}/review",
            json={"review_status": "reviewed", "note": "should be blocked"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("mapping", response.json()["detail"].lower())
        conn = sqlite3.connect(self.db)
        self.assertIsNone(
            conn.execute(
                "SELECT 1 FROM statement_reviews WHERE statement_id=? AND review_status='reviewed'",
                (blocked["statement_id"],),
            ).fetchone()
        )
        conn.close()

    def test_approving_a_conflict_clears_conflict_state(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        blocked = next(
            (item for item in queue if item["mapping_conflict_rows"]), None
        )
        if blocked is None:
            self.skipTest("no mapping-conflict statement")
        statement_id = blocked["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        conflict_row = next(row for row in rows if row["mapping_status"] == "conflict")
        candidate = conflict_row["mapping_candidates"][0]

        response = self.client.post(
            f"/api/review/{statement_id}/rows/{conflict_row['statement_row_id']}/map",
            json={"standard_item_id": candidate["standard_item_id"], "note": "resolve conflict"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mapping_status"], "approved")

        refreshed = next(
            item for item in self.client.get("/api/review/queue").json()
            if item["statement_id"] == statement_id
        )
        self.assertEqual(
            refreshed["mapping_conflict_rows"],
            blocked["mapping_conflict_rows"] - 1,
        )
        self.assertEqual(refreshed["mapped_rows"], blocked["mapped_rows"])

    def _insert_row(self, statement_id: str, *, after_row_id: str | None = None,
                    standard_item_id: str | None = None) -> dict:
        payload = {
            "after_row_id": after_row_id,
            "row_type": "item",
            "label": "測試插入行",
            "debit": "123.45",
            "debit_state": "amount",
            "credit_state": "blank",
            "standard_item_id": standard_item_id,
            "note": "test insert",
        }
        response = self.client.post(
            f"/api/review/{statement_id}/rows",
            json=payload,
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_inserted_row_appears_and_counts_as_mapped(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        ready = next(item for item in queue if item["mapping_ready"])
        statement_id = ready["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        item = self.client.get("/api/standard-items").json()[0]

        inserted = self._insert_row(
            statement_id, after_row_id=rows[0]["statement_row_id"],
            standard_item_id=item["standard_item_id"],
        )
        self.assertEqual(inserted["row_origin"], "manual")
        self.assertEqual(inserted["mapping_status"], "approved")
        self.assertEqual(inserted["effective_debit_display"], "123.45")

        refreshed = self.client.get(f"/api/review/{statement_id}/rows").json()
        self.assertEqual(len(refreshed), len(rows) + 1)
        self.assertEqual(
            next(row for row in refreshed if row["statement_row_id"] == inserted["statement_row_id"])[
                "standard_item_key"
            ],
            item["item_key"],
        )
        queue_row = next(
            item2 for item2 in self.client.get("/api/review/queue").json()
            if item2["statement_id"] == statement_id
        )
        self.assertEqual(queue_row["row_count"], ready["row_count"] + 1)
        self.assertEqual(queue_row["mapped_rows"], ready["mapped_rows"] + 1)

        removed = self.client.post(
            f"/api/review/{statement_id}/rows/{inserted['statement_row_id']}/exclude",
            json={"note": "remove manual row"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(removed.status_code, 200)
        final = self.client.get(f"/api/review/{statement_id}/rows").json()
        self.assertEqual(len(final), len(rows))
        self.assertNotIn(
            inserted["statement_row_id"],
            {row["statement_row_id"] for row in final},
        )

    def test_inserted_row_without_mapping_blocks_review(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        ready = next(item for item in queue if item["mapping_ready"])
        statement_id = ready["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        inserted = self._insert_row(statement_id, after_row_id=rows[0]["statement_row_id"])

        blocked = next(
            item for item in self.client.get("/api/review/queue").json()
            if item["statement_id"] == statement_id
        )
        self.assertFalse(blocked["mapping_ready"])
        self.assertEqual(blocked["unmapped_rows"], 1)
        review = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "reviewed"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(review.status_code, 409)

        item = self.client.get("/api/standard-items").json()[0]
        mapped = self.client.post(
            f"/api/review/{statement_id}/rows/{inserted['statement_row_id']}/map",
            json={"standard_item_id": item["standard_item_id"]},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(mapped.status_code, 200)
        self.assertEqual(mapped.json()["mapping_status"], "approved")
        unblocked = next(
            item2 for item2 in self.client.get("/api/review/queue").json()
            if item2["statement_id"] == statement_id
        )
        self.assertTrue(unblocked["mapping_ready"])

    def test_excluded_ocr_row_can_be_restored(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        ready = next(item for item in queue if item["mapping_ready"])
        statement_id = ready["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        target = rows[0]

        excluded = self.client.post(
            f"/api/review/{statement_id}/rows/{target['statement_row_id']}/exclude",
            json={"note": "OCR over-read"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(excluded.status_code, 200)
        adjustment_id = excluded.json()["adjustment_id"]
        self.assertNotIn(
            target["statement_row_id"],
            {row["statement_row_id"] for row in self.client.get(f"/api/review/{statement_id}/rows").json()},
        )
        adjustments = self.client.get(f"/api/review/{statement_id}/adjustments").json()
        active = next(item for item in adjustments if item["adjustment_id"] == adjustment_id)
        self.assertEqual(active["adjustment_type"], "exclude")

        restored = self.client.post(
            f"/api/review/{statement_id}/adjustments/{adjustment_id}/restore",
            json={"note": "restore"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertIn(
            target["statement_row_id"],
            {row["statement_row_id"] for row in self.client.get(f"/api/review/{statement_id}/rows").json()},
        )

    def test_row_adjustments_require_unreviewed_statement(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        ready = next(item for item in queue if item["mapping_ready"])
        statement_id = ready["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        reviewed = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "reviewed"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(reviewed.status_code, 200)
        insert = self.client.post(
            f"/api/review/{statement_id}/rows",
            json={"label": "blocked", "debit_state": "blank", "credit_state": "blank"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(insert.status_code, 409)
        exclude = self.client.post(
            f"/api/review/{statement_id}/rows/{rows[0]['statement_row_id']}/exclude",
            json={"note": "blocked"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(exclude.status_code, 409)

    def test_unmatched_row_can_be_mapped(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        blocked = next((item for item in queue if item["unmapped_rows"]), None)
        if blocked is None:
            self.skipTest("no unmatched row")
        statement_id = blocked["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        row = next(item for item in rows if item["mapping_status"] == "unmatched")
        item = self.client.get("/api/standard-items").json()[0]
        response = self.client.post(
            f"/api/review/{statement_id}/rows/{row['statement_row_id']}/map",
            json={"standard_item_id": item["standard_item_id"], "note": "match unmatched"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["mapping_status"], "approved")
        self.assertEqual(response.json()["standard_item_key"], item["item_key"])
        refreshed = self.client.get(f"/api/review/{statement_id}/rows").json()
        updated = next(
            item2 for item2 in refreshed
            if item2["statement_row_id"] == row["statement_row_id"]
        )
        self.assertEqual(updated["mapping_status"], "approved")
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM statement_row_mappings "
                "WHERE statement_row_id=? AND mapping_status='approved'",
                (row["statement_row_id"],),
            ).fetchone()[0],
            1,
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
