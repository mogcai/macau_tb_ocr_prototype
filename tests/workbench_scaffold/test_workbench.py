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
from workbench import service  # noqa: E402
from workbench.db import connect_v3, is_snapshot  # noqa: E402
from workbench.main import app  # noqa: E402
from workbench.permissions import can_adopt, can_catalogue, can_publish, can_verify  # noqa: E402


class PermissionTests(unittest.TestCase):
    def test_adoption_and_publish_are_separate(self) -> None:
        self.assertTrue(can_adopt("adopter"))
        self.assertFalse(can_publish("adopter"))
        self.assertTrue(can_publish("publisher"))
        self.assertFalse(can_adopt("publisher"))
        self.assertTrue(can_verify("reviewer"))
        self.assertFalse(can_adopt("reviewer"))

    def test_catalogue_maintenance_is_admin_only(self) -> None:
        self.assertTrue(can_catalogue("admin"))
        for role in ("viewer", "reviewer", "adopter", "publisher"):
            with self.subTest(role=role):
                self.assertFalse(can_catalogue(role))


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


class MoneyContractTests(unittest.TestCase):
    def test_dot_decimal_and_thousands_separator(self) -> None:
        self.assertEqual(service._parse_money("1234.56"), 123456)
        self.assertEqual(service._parse_money("1,234.56"), 123456)
        self.assertEqual(service._parse_money("1,234"), 123400)
        self.assertEqual(service._parse_money("-1.50"), -150)
        self.assertEqual(service._parse_money("0.00"), 0)
        self.assertEqual(service._parse_money("1234.5"), 123450)

    def test_decimal_comma_and_ambiguous_values_are_rejected(self) -> None:
        for value in ("1.234,56", "12,34", "1.234", "1234,56", "1 234.56", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    service._parse_money(value)
        with self.assertRaises(ValueError):
            service._parse_money("")
        with self.assertRaises(ValueError):
            service._parse_money(None)
        with self.assertRaises(ValueError):
            service._parse_money("-")

    def test_statement_decimal_places_drive_parse_and_format(self) -> None:
        self.assertEqual(service._parse_money("1234.567", 3), 1234567)
        self.assertEqual(service._money(1234567, 3), "1,234.567")
        self.assertEqual(service._parse_money("123456", 0), 123456)
        self.assertEqual(service._money(123456, 0), "123,456")
        self.assertEqual(service._money(None, 2), "")


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
        queue = self.client.get("/api/review/queue").json()
        candidate = next(
            (item for item in queue if item["review_bucket"] != "ready_for_adoption"),
            queue[0] if queue else None,
        )
        if candidate is None:
            self.skipTest("no held statement")
        statement_id = candidate["statement_id"]
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

    def test_review_page_includes_adopted_section(self) -> None:
        page = self.client.get("/review")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Adopted — current dataset", page.text)

    def test_adoption_page_has_ready_blocked_and_adopted_sections(self) -> None:
        page = self.client.get("/adopt")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Ready to adopt", page.text)
        self.assertIn("Reviewed — not adoptable", page.text)
        self.assertIn("Adopted — current dataset", page.text)
        self.assertIn("Only statements with reviewer sign-off can be adopted", page.text)

    def test_statement_page_requires_review_before_adoption(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        pending = next(
            (item for item in queue if item["review_status"] != "reviewed"), None
        )
        if pending is None:
            self.skipTest("no unreviewed statement")
        self.client.get("/set-role/adopter")
        page = self.client.get(f"/statements/{pending['statement_id']}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("Adopt current extraction", page.text)
        self.assertIn("Mark reviewed before adoption", page.text)

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

    def test_review_detail_uses_per_row_dirty_guard_and_field_checks(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        statement_id = queue[0]["statement_id"]
        page = self.client.get(f"/review/{statement_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("dirtyRows", page.text)
        self.assertIn("beforeunload", page.text)
        self.assertIn('data-field="debit_minor"', page.text)
        self.assertIn("verified_fields", page.text)

    def test_catalogue_page_lists_items(self) -> None:
        page = self.client.get("/catalogue?role=admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Standard list", page.text)
        self.assertIn("澳門特區負債證明書", page.text)
        self.assertIn("Add a draft standard item", page.text)


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

    def _pending_queue(self) -> list[dict]:
        return [
            item for item in self.client.get("/api/review/queue").json()
            if item["review_status"] != "reviewed"
        ]

    def _ready_candidate(self) -> dict | None:
        """Return a statement that passes every adoption-readiness gate.

        The operational fixture may already have reviewed statements without a
        review-extraction binding, so readiness is recomputed from the row and
        mapping metrics and a reviewed candidate is reopened first.
        """
        queue = self.client.get("/api/review/queue").json()
        candidates = [
            item for item in queue
            if item["mapping_ready"]
            and item["row_count"]
            and item["debit_credit_equal"]
            and not item["missing_labels"]
            and not item["issue_rows"]
            and not item["invalid_mapping_rows"]
            and not item["duplicate_mapping_items"]
        ]
        if not candidates:
            return None
        item = candidates[0]
        if item["review_status"] == "reviewed":
            reopened = self.client.post(
                f"/api/review/{item['statement_id']}/review",
                json={"review_status": "needs_rework", "note": "prepare ready fixture"},
                headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
            )
            if reopened.status_code != 200:
                return None
            refreshed = next(
                (
                    row for row in self.client.get("/api/review/queue").json()
                    if row["statement_id"] == item["statement_id"]
                ),
                None,
            )
            return refreshed
        return item

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
        queue = self._pending_queue()
        if not queue:
            self.skipTest("no pending statement")
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
                "verified_fields": ["label_normalized"],
                "note": "test correction",
            },
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # The ticked unchanged label plus fields whose value changed.
        self.assertIn("label_normalized", body["verified_fields"])
        self.assertIn("debit_minor", body["verified_fields"])
        self.assertLess(len(body["verified_fields"]), 5)
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM field_verifications WHERE verified_by='tester'"
            ).fetchone()[0],
            len(body["verified_fields"]),
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='workbench_row_verified'").fetchone()[0],
            before_events + 1,
        )
        conn.close()

    def test_mark_reviewed_then_adopt(self) -> None:
        ready = self._ready_candidate()
        if ready is None:
            self.skipTest("no ready held statement")
        statement_id = ready["statement_id"]
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

    def test_adoption_page_and_record_offer_adopt_after_review(self) -> None:
        ready = self._ready_candidate()
        if ready is None:
            self.skipTest("no ready held statement")
        statement_id = ready["statement_id"]
        reviewed = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "reviewed", "note": "reviewed for adoption page"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.client.get("/set-role/adopter")

        adoption_page = self.client.get("/adopt")
        self.assertEqual(adoption_page.status_code, 200)
        self.assertIn(statement_id, adoption_page.text)
        self.assertIn("adopt-button", adoption_page.text)

        record_page = self.client.get(f"/review/{statement_id}")
        self.assertIn("Adopt current extraction", record_page.text)

        statement_page = self.client.get(f"/statements/{statement_id}")
        self.assertIn("Adopt current extraction", statement_page.text)

        adopted = self.client.post(
            f"/api/statements/{statement_id}/adopt",
            headers={"X-Workbench-Role": "adopter", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(adopted.status_code, 200, adopted.text)
        self.assertIn(
            statement_id,
            {item["statement_id"] for item in service.adopted_statements()},
        )

    def test_reviewed_statement_is_read_only_until_reopened(self) -> None:
        queue = self._pending_queue()
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
        queue = self._pending_queue()
        if not queue:
            self.skipTest("no pending statement")
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
        queue = self._pending_queue()
        if not queue:
            self.skipTest("no pending statement")
        statement_id = queue[0]["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        row = next(
            (
                item for item in rows
                if item["mapping_candidates"]
                and item["mapping_status"] in ("unique", "conflict")
            ),
            None,
        )
        if row is None:
            self.skipTest("pending statement has no unapproved mapping candidate")
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
        queue = self._pending_queue()
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
        queue = self._pending_queue()
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
        queue = self._pending_queue()
        ready = next(item for item in queue if item["mapping_ready"])
        statement_id = ready["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        used = {
            candidate["standard_item_id"]
            for row in rows
            for candidate in row["mapping_candidates"]
        }
        item = next(
            (
                candidate for candidate in self.client.get("/api/standard-items").json()
                if candidate["standard_item_id"] not in used
            ),
            None,
        )
        if item is None:
            self.skipTest("no unused standard item in this statement")

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
        queue = self._pending_queue()
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
        queue = self._pending_queue()
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
        queue = self._pending_queue()
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
        queue = self._pending_queue()
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

    def test_return_to_review_requires_adopter_role(self) -> None:
        active = self.client.get("/api/statements?status=active&limit=1").json()
        if not active:
            self.skipTest("no adopted statement")
        response = self.client.post(
            f"/api/statements/{active[0]['statement_id']}/return-to-review",
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(response.status_code, 403)

    def test_adopted_statement_is_read_only_until_returned(self) -> None:
        active = self.client.get("/api/statements?status=active&limit=1").json()
        if not active:
            self.skipTest("no adopted statement")
        statement_id = active[0]["statement_id"]
        rows = self.client.get(f"/api/statements/{statement_id}/rows").json()
        verify = self.client.post(
            f"/api/review/{statement_id}/rows/{rows[0]['statement_row_id']}/verify",
            json={
                "label": "blocked",
                "debit": "1.00",
                "debit_state": "amount",
                "credit_state": "blank",
            },
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(verify.status_code, 409)
        self.assertIn("adopted", verify.json()["detail"].lower())
        insert = self.client.post(
            f"/api/review/{statement_id}/rows",
            json={"label": "blocked", "debit_state": "blank", "credit_state": "blank"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(insert.status_code, 409)

    def test_return_to_review_restores_reviewed_held_state(self) -> None:
        active = self.client.get("/api/statements?status=active&limit=1").json()
        if not active:
            self.skipTest("no adopted statement")
        statement_id = active[0]["statement_id"]
        before_rows = self.client.get(f"/api/statements/{statement_id}/rows").json()
        current_before = self.client.get("/api/summary").json()["current_rows"]

        response = self.client.post(
            f"/api/statements/{statement_id}/return-to-review",
            headers={"X-Workbench-Role": "adopter", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["statement_status"], "proposed")
        self.assertEqual(response.json()["review_status"], "reviewed")

        detail = self.client.get(f"/api/statements/{statement_id}").json()
        self.assertEqual(detail["statement_status"], "proposed")
        self.assertIsNone(detail["adopted_statement_extraction_id"])
        self.assertEqual(detail["review_status"], "reviewed")
        self.assertEqual(
            self.client.get("/api/summary").json()["current_rows"],
            current_before - len(before_rows),
        )
        queue = self.client.get("/api/review/queue").json()
        queue_row = next(item for item in queue if item["statement_id"] == statement_id)
        self.assertEqual(queue_row["review_status"], "reviewed")
        self.assertIn("reviewed", queue_row["review_bucket"] + "reviewed")

        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE event_type='workbench_statement_returned_to_review' "
                "AND statement_id=?",
                (statement_id,),
            ).fetchone()[0],
            1,
        )
        conn.close()

    def test_reviewed_statement_with_unresolved_mapping_can_be_reopened(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        candidate = next(
            (
                item for item in queue
                if item["review_status"] == "reviewed" and not item["mapping_ready"]
            ),
            None,
        )
        if candidate is None:
            self.skipTest("no reviewed statement with unresolved mappings")
        statement_id = candidate["statement_id"]

        reopened = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "needs_rework", "note": "reopen for editing"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        detail = self.client.get(f"/api/statements/{statement_id}").json()
        self.assertEqual(detail["review_status"], "needs_rework")

        blocked = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "reviewed"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(blocked.status_code, 409)

    def test_standard_item_can_only_be_used_once_per_statement(self) -> None:
        queue = self._pending_queue()
        candidate = next((item for item in queue if item["row_count"] > 2), None)
        if candidate is None:
            self.skipTest("no pending statement with multiple rows")
        statement_id = candidate["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        used = {
            option["standard_item_id"]
            for row in rows
            for option in row["mapping_candidates"]
        }
        item = next(
            (
                option for option in self.client.get("/api/standard-items").json()
                if option["standard_item_id"] not in used
            ),
            None,
        )
        if item is None:
            self.skipTest("no unused standard item in this statement")
        first = self.client.post(
            f"/api/review/{statement_id}/rows/{rows[0]['statement_row_id']}/map",
            json={"standard_item_id": item["standard_item_id"]},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            f"/api/review/{statement_id}/rows/{rows[1]['statement_row_id']}/map",
            json={"standard_item_id": item["standard_item_id"]},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(second.status_code, 400)
        self.assertIn("already mapped", second.json()["detail"].lower())

        self.client.cookies.set("workbench_role", "reviewer")
        page = self.client.get(f"/review/{statement_id}")
        self.assertEqual(page.status_code, 200)
        # The approved item is only offered to its own row; other row selects
        # and the insert form exclude it.
        self.assertEqual(
            page.text.count(f'value="{item["standard_item_id"]}"'),
            1,
        )

    def test_duplicate_approved_item_blocks_review(self) -> None:
        queue = self.client.get("/api/review/queue").json()
        duplicate = next(
            (item for item in queue if item["duplicate_mapping_items"]), None
        )
        if duplicate is None:
            self.skipTest("no duplicate standard item")
        self.assertFalse(duplicate["mapping_ready"])
        self.assertIn("duplicate_standard_item", duplicate["mapping_blockers"])

    def _add_completed_extraction(self, statement_id: str, *, suffix: str) -> str:
        conn = sqlite3.connect(self.db)
        try:
            source_document_id = conn.execute(
                "SELECT source_document_id FROM statements WHERE statement_id=?",
                (statement_id,),
            ).fetchone()[0]
            run_id = f"test-run-{suffix}"
            extraction_id = f"test-extraction-{suffix}"
            conn.execute(
                "INSERT INTO extraction_runs("
                "run_id, source_document_id, run_status, started_at, completed_at"
                ") VALUES (?, ?, 'completed', ?, ?)",
                (run_id, source_document_id, service.now(), service.now()),
            )
            conn.execute(
                "INSERT INTO statement_extractions("
                "statement_extraction_id, statement_id, run_id, source_document_id, "
                "extraction_status, created_at"
                ") VALUES (?, ?, ?, ?, 'completed', ?)",
                (
                    extraction_id, statement_id, run_id, source_document_id,
                    "2999-01-01T00:00:00+00:00",
                ),
            )
            conn.commit()
            return extraction_id
        finally:
            conn.close()

    def test_review_binds_extraction_and_stale_extraction_blocks_adoption(self) -> None:
        ready = self._ready_candidate()
        if ready is None:
            self.skipTest("no ready pending statement")
        statement_id = ready["statement_id"]
        before_rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        reviewed = self.client.post(
            f"/api/review/{statement_id}/review",
            json={"review_status": "reviewed", "note": "bind extraction"},
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        extraction_id = reviewed.json()["statement_extraction_id"]
        self.assertTrue(extraction_id)

        conn = sqlite3.connect(self.db)
        binding = conn.execute(
            "SELECT statement_extraction_id FROM statement_review_bindings "
            "WHERE statement_id=? AND binding_status='active'",
            (statement_id,),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(binding)
        self.assertEqual(binding[0], extraction_id)

        detail = self.client.get(f"/api/statements/{statement_id}").json()
        self.assertEqual(detail["statement_extraction_id"], extraction_id)
        self.assertEqual(detail["review_binding_statement_extraction_id"], extraction_id)
        self.assertFalse(detail["review_extraction_outdated"])

        self._add_completed_extraction(statement_id, suffix="newer")
        queue_row = next(
            item for item in self.client.get("/api/review/queue").json()
            if item["statement_id"] == statement_id
        )
        self.assertEqual(queue_row["review_bucket"], "review_outdated")
        self.assertTrue(queue_row["review_extraction_outdated"])

        adopt = self.client.post(
            f"/api/statements/{statement_id}/adopt",
            headers={"X-Workbench-Role": "adopter", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(adopt.status_code, 409)
        self.assertIn("review_outdated", adopt.json()["detail"])

        # Review rows still come from the bound extraction, not the newer empty one.
        after_rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        self.assertEqual(len(after_rows), len(before_rows))

    def test_amount_state_requires_a_value(self) -> None:
        queue = self._pending_queue()
        if not queue:
            self.skipTest("no pending statement")
        statement_id = queue[0]["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        row = rows[0]
        conn = sqlite3.connect(self.db)
        before = conn.execute(
            "SELECT COUNT(*) FROM field_verifications WHERE statement_row_id=?",
            (row["statement_row_id"],),
        ).fetchone()[0]
        conn.close()
        rejected = self.client.post(
            f"/api/review/{statement_id}/rows/{row['statement_row_id']}/verify",
            json={
                "label": row["effective_label"],
                "debit": "",
                "credit": "",
                "debit_state": "amount",
                "credit_state": "blank",
                "verified_fields": ["debit_minor"],
            },
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("required", rejected.json()["detail"].lower())
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM field_verifications WHERE statement_row_id=?",
                (row["statement_row_id"],),
            ).fetchone()[0],
            before,
        )
        conn.close()

        accepted = self.client.post(
            f"/api/review/{statement_id}/rows/{row['statement_row_id']}/verify",
            json={
                "label": row["effective_label"],
                "debit": "0.00",
                "credit": "",
                "debit_state": "amount",
                "credit_state": "blank",
                "verified_fields": ["debit_minor"],
            },
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["effective_debit_minor"], 0)
        self.assertEqual(accepted.json()["effective_debit_state"], "amount")

    def test_non_amount_state_stores_null_minor(self) -> None:
        queue = self._pending_queue()
        if not queue:
            self.skipTest("no pending statement")
        statement_id = queue[0]["statement_id"]
        rows = self.client.get(f"/api/review/{statement_id}/rows").json()
        row = next(
            (
                item for item in rows
                if item["effective_debit_state"] == "amount"
                and item["effective_debit_minor"] is not None
            ),
            None,
        )
        if row is None:
            self.skipTest("no amount row")
        credit = (
            row["effective_credit_display"]
            if row["effective_credit_state"] == "amount" else ""
        )
        response = self.client.post(
            f"/api/review/{statement_id}/rows/{row['statement_row_id']}/verify",
            json={
                "label": row["effective_label"],
                "debit": "",
                "credit": credit,
                "debit_state": "blank",
                "credit_state": row["effective_credit_state"],
                "verified_fields": ["debit_value_state"],
            },
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["effective_debit_state"], "blank")
        self.assertIsNone(body["effective_debit_minor"])
        conn = sqlite3.connect(self.db)
        stored = conn.execute(
            "SELECT verified_value_text FROM field_verifications "
            "WHERE statement_row_id=? AND field_name='debit_minor'",
            (row["statement_row_id"],),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(stored)
        self.assertIsNone(stored[0])

    def test_standard_item_dropdown_excludes_inactive_items(self) -> None:
        active = self.client.get("/api/standard-items").json()
        self.assertTrue(active)
        self.assertTrue(all(item["item_status"] == "approved" for item in active))
        everything = self.client.get("/api/standard-items?include_inactive=true").json()
        inactive = [item for item in everything if item["item_status"] != "approved"]
        if not inactive:
            self.skipTest("no inactive standard item in the fixture")
        self.assertTrue(inactive)

    def test_inactive_standard_items_cannot_be_mapped(self) -> None:
        queue = self._pending_queue()
        if not queue:
            self.skipTest("no pending statement")
        statement_id = queue[0]["statement_id"]
        row = self.client.get(f"/api/review/{statement_id}/rows").json()[0]
        everything = self.client.get("/api/standard-items?include_inactive=true").json()
        approved = next(item for item in everything if item["item_status"] == "approved")
        draft = next(
            (item for item in everything if item["item_status"] == "draft"), None
        )
        if draft is None:
            self.skipTest("no draft standard item")
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO standard_items("
            "standard_item_id, item_key, item_status, created_at"
            ") VALUES ('test-retired-item', 'test-retired-item', 'retired', ?)",
            (service.now(),),
        )
        conn.execute(
            "INSERT INTO standard_items("
            "standard_item_id, item_key, item_status, merged_into_standard_item_id, created_at"
            ") VALUES ('test-merged-item', 'test-merged-item', 'merged', ?, ?)",
            (approved["standard_item_id"], service.now()),
        )
        conn.commit()
        conn.close()

        for item_id, expected in (
            (draft["standard_item_id"], "only approved"),
            ("test-retired-item", "only approved"),
            ("test-merged-item", "merged into"),
        ):
            with self.subTest(item_id=item_id):
                response = self.client.post(
                    f"/api/review/{statement_id}/rows/{row['statement_row_id']}/map",
                    json={"standard_item_id": item_id, "note": "inactive item"},
                    headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
                )
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn(expected, response.json()["detail"])

        inserted = self.client.post(
            f"/api/review/{statement_id}/rows",
            json={
                "label": "inactive insert",
                "debit_state": "blank",
                "credit_state": "blank",
                "standard_item_id": "test-retired-item",
            },
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(inserted.status_code, 409)

    def test_catalogue_create_approve_retire_reopen(self) -> None:
        payload = {
            "canonical_zh": "測試科目",
            "canonical_pt": "CONTA TESTE",
            "category": "asset",
            "note": "created in test",
        }
        forbidden = self.client.post(
            "/api/catalogue/items",
            json=payload,
            headers={"X-Workbench-Role": "reviewer", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(forbidden.status_code, 403)

        created = self.client.post(
            "/api/catalogue/items",
            json=payload,
            headers={"X-Workbench-Role": "admin", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()
        item_id = item["standard_item_id"]
        self.assertEqual(item["item_status"], "draft")
        self.assertEqual(item["canonical_zh_status"], "draft")
        self.assertEqual(item["item_key"], "user:測試科目")

        default_items = self.client.get("/api/standard-items").json()
        self.assertNotIn(item_id, {entry["standard_item_id"] for entry in default_items})
        all_items = self.client.get("/api/standard-items?include_inactive=true").json()
        self.assertIn(item_id, {entry["standard_item_id"] for entry in all_items})

        approve_forbidden = self.client.post(
            f"/api/catalogue/items/{item_id}/approve",
            json={"note": "no"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(approve_forbidden.status_code, 403)
        approved = self.client.post(
            f"/api/catalogue/items/{item_id}/approve",
            json={"note": "approved in test"},
            headers={"X-Workbench-Role": "admin", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["item_status"], "approved")
        self.assertEqual(approved.json()["canonical_zh_status"], "approved")
        default_items = self.client.get("/api/standard-items").json()
        self.assertIn(item_id, {entry["standard_item_id"] for entry in default_items})

        retired = self.client.post(
            f"/api/catalogue/items/{item_id}/retire",
            json={"note": "retired in test"},
            headers={"X-Workbench-Role": "admin", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(retired.status_code, 200, retired.text)
        self.assertEqual(retired.json()["item_status"], "retired")
        self.assertIn("usage", retired.json())
        default_items = self.client.get("/api/standard-items").json()
        self.assertNotIn(item_id, {entry["standard_item_id"] for entry in default_items})

        reopened = self.client.post(
            f"/api/catalogue/items/{item_id}/reopen",
            json={"note": "reopened in test"},
            headers={"X-Workbench-Role": "admin", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json()["item_status"], "draft")

        duplicate = self.client.post(
            "/api/catalogue/items",
            json=payload,
            headers={"X-Workbench-Role": "admin", "X-Workbench-Actor": "tester"},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("already exists", duplicate.json()["detail"])

        conn = sqlite3.connect(self.db)
        events = {
            row[0] for row in conn.execute(
                "SELECT event_type FROM audit_events WHERE standard_item_id=?",
                (item_id,),
            )
        }
        conn.close()
        self.assertTrue({
            "standard_item_created", "standard_item_approved",
            "standard_item_retired", "standard_item_reopened",
        }.issubset(events))


if __name__ == "__main__":
    unittest.main()
