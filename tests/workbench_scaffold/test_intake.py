"""Tests for Workbench import intake, page decisions and statement creation."""
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

from workbench import intake, service  # noqa: E402
import workbench.db as wdb  # noqa: E402
from workbench.config import V3_DB  # noqa: E402
from workbench.main import app  # noqa: E402
from workbench.permissions import can_classify  # noqa: E402


def _snapshot_batch(batches):
    for batch in batches:
        if batch["unassigned_rows"]:
            return batch
    return batches[0] if batches else None


@unittest.skipUnless(V3_DB.is_file(), "operational v3 DB not available")
class IntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        shutil.copyfile(V3_DB, self.db)
        self.patchers = [
            mock.patch.object(wdb, "V3_DB", self.db),
            mock.patch.object(intake, "_auto_map", return_value={"status": "stubbed"}),
        ]
        for patcher in self.patchers:
            patcher.start()
        intake.ensure_schema()
        self.batch = _snapshot_batch(intake.list_batches())
        if self.batch is None:
            self.skipTest("no imported batch awaiting intake")
        self.detail = intake.batch_detail(self.batch["source_document_id"])

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tempdir.cleanup()

    def _classify_all(self, *, reject_page: int | None = None) -> None:
        entity = self.detail["entities"][0]
        for page in self.detail["pages"]:
            if reject_page is not None and page["pdf_page"] == reject_page:
                intake.save_page_decision(
                    self.batch["source_document_id"],
                    page["document_page_id"],
                    page_role="other",
                    decision="reject",
                    actor="tester",
                    note="not a financial statement",
                )
                continue
            intake.save_page_decision(
                self.batch["source_document_id"],
                page["document_page_id"],
                page_role="financial_statement",
                decision="approve",
                actor="tester",
                entity_id=entity["entity_id"],
                statement_date="2025-12-31",
                statement_type="balance_sheet",
            )

    def test_batch_detail_lists_pages_and_entities(self) -> None:
        self.assertTrue(self.detail["pages"])
        self.assertTrue(self.detail["entities"])
        self.assertEqual(
            self.detail["pending_pages"], len(self.detail["pages"])
        )
        self.assertFalse(self.detail["can_build"])

    def test_financial_approval_requires_entity_and_date(self) -> None:
        page = self.detail["pages"][0]
        with self.assertRaises(intake.IntakeError):
            intake.save_page_decision(
                self.batch["source_document_id"], page["document_page_id"],
                page_role="financial_statement", decision="approve", actor="tester",
            )
        with self.assertRaises(intake.IntakeError):
            intake.save_page_decision(
                self.batch["source_document_id"], page["document_page_id"],
                page_role="financial_statement", decision="approve", actor="tester",
                entity_id=self.detail["entities"][0]["entity_id"],
                statement_date="2025-13-01",
            )

    def test_reject_requires_note(self) -> None:
        page = self.detail["pages"][0]
        with self.assertRaises(intake.IntakeError):
            intake.save_page_decision(
                self.batch["source_document_id"], page["document_page_id"],
                page_role="other", decision="reject", actor="tester", note=" ",
            )

    def test_decision_writes_page_state_decision_and_audit(self) -> None:
        entity = self.detail["entities"][0]
        page = self.detail["pages"][0]
        intake.save_page_decision(
            self.batch["source_document_id"], page["document_page_id"],
            page_role="financial_statement", decision="approve", actor="tester",
            entity_id=entity["entity_id"], statement_date="2025-12-31",
            statement_type="balance_sheet", note="reviewed",
        )
        conn = sqlite3.connect(self.db)
        try:
            state = conn.execute(
                "SELECT page_role, classification_status, classification_source, "
                "classified_by FROM document_pages WHERE document_page_id = ?",
                (page["document_page_id"],),
            ).fetchone()
            decision = conn.execute(
                "SELECT reporting_entity_id, statement_date, statement_type "
                "FROM page_statement_decisions WHERE document_page_id = ?",
                (page["document_page_id"],),
            ).fetchone()
            audit = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE document_page_id = ? "
                "AND event_type = 'page_classified'",
                (page["document_page_id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(state[:3], ("financial_statement", "approved", "human"))
        self.assertEqual(state[3], "tester")
        self.assertEqual(decision, (entity["entity_id"], "2025-12-31", "balance_sheet"))
        self.assertEqual(audit, 1)

    def test_build_requires_every_page_classified(self) -> None:
        with self.assertRaises(intake.IntakeError):
            intake.build_statements(self.batch["source_document_id"], "tester")

    def test_build_creates_statement_rows_and_review_queue_entry(self) -> None:
        self._classify_all()
        detail = intake.batch_detail(self.batch["source_document_id"])
        self.assertTrue(detail["can_build"], detail["preview_error"])

        summary = intake.build_statements(self.batch["source_document_id"], "tester")
        self.assertEqual(summary["statements_created"], 1)
        self.assertEqual(summary["rows_assigned"], self.batch["unassigned_rows"])
        self.assertEqual(summary["mapping"]["status"], "stubbed")

        conn = sqlite3.connect(self.db)
        try:
            included = conn.execute(
                "SELECT COUNT(*) FROM extracted_rows er "
                "JOIN document_pages dp ON dp.document_page_id = er.document_page_id "
                "WHERE dp.source_document_id = ? AND er.row_disposition = 'included'",
                (self.batch["source_document_id"],),
            ).fetchone()[0]
            unassigned = conn.execute(
                "SELECT COUNT(*) FROM extracted_rows er "
                "JOIN document_pages dp ON dp.document_page_id = er.document_page_id "
                "WHERE dp.source_document_id = ? AND er.row_disposition = 'unassigned'",
                (self.batch["source_document_id"],),
            ).fetchone()[0]
            statement_rows = conn.execute(
                "SELECT COUNT(*) FROM statement_rows sr "
                "JOIN statements s ON s.statement_id = sr.statement_id "
                "WHERE s.source_document_id = ?",
                (self.batch["source_document_id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(unassigned, 0)
        self.assertEqual(included, self.batch["unassigned_rows"])
        self.assertEqual(statement_rows, included)

        queue_ids = {item["statement_id"] for item in service.review_queue()}
        self.assertIn(summary["statements"][0]["statement_id"], queue_ids)

        again = intake.build_statements(self.batch["source_document_id"], "tester")
        self.assertEqual(again["statements_created"], 0)
        self.assertEqual(again["statements_existing"], 1)
        self.assertEqual(again["rows_assigned"], 0)

    def test_rejected_page_rows_are_excluded(self) -> None:
        page_with_rows = next(
            page for page in self.detail["pages"] if page["total_rows"]
        )
        self._classify_all(reject_page=page_with_rows["pdf_page"])
        summary = intake.build_statements(self.batch["source_document_id"], "tester")
        self.assertGreater(summary["rows_excluded"], 0)
        conn = sqlite3.connect(self.db)
        try:
            excluded = conn.execute(
                "SELECT COUNT(*) FROM extracted_rows WHERE document_page_id = ? "
                "AND row_disposition = 'excluded_page_role'",
                (page_with_rows["document_page_id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(excluded, page_with_rows["total_rows"])


class IntakePermissionTests(unittest.TestCase):
    def test_classify_capability(self) -> None:
        for role in ("reviewer", "adopter", "admin"):
            with self.subTest(role=role):
                self.assertTrue(can_classify(role))
        for role in ("viewer", "publisher"):
            with self.subTest(role=role):
                self.assertFalse(can_classify(role))


@unittest.skipUnless(V3_DB.is_file(), "operational v3 DB not available")
class IntakeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        shutil.copyfile(V3_DB, self.db)
        self.patchers = [
            mock.patch.object(wdb, "V3_DB", self.db),
            mock.patch.object(intake, "_auto_map", return_value={"status": "stubbed"}),
        ]
        for patcher in self.patchers:
            patcher.start()
        intake.ensure_schema()
        self.client = TestClient(app)
        self.batch = _snapshot_batch(intake.list_batches())
        if self.batch is None:
            self.skipTest("no imported batch awaiting intake")
        self.detail = intake.batch_detail(self.batch["source_document_id"])

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tempdir.cleanup()

    def test_viewer_cannot_classify(self) -> None:
        page = self.detail["pages"][0]
        response = self.client.post(
            f"/api/intake/{self.batch['source_document_id']}/pages/"
            f"{page['document_page_id']}",
            json={"page_role": "other", "decision": "approve"},
            headers={"X-Workbench-Role": "viewer"},
        )
        self.assertEqual(response.status_code, 403)

    def test_pages_and_build_endpoint(self) -> None:
        entity = self.detail["entities"][0]
        for page in self.detail["pages"]:
            response = self.client.post(
                f"/api/intake/{self.batch['source_document_id']}/pages/"
                f"{page['document_page_id']}",
                json={
                    "page_role": "financial_statement",
                    "decision": "approve",
                    "entity_id": entity["entity_id"],
                    "statement_date": "2025-12-31",
                    "statement_type": "balance_sheet",
                },
                headers={"X-Workbench-Role": "reviewer"},
            )
            self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get(
            f"/api/intake/{self.batch['source_document_id']}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.json()["can_build"])
        built = self.client.post(
            f"/api/intake/{self.batch['source_document_id']}/build",
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(built.status_code, 200, built.text)
        self.assertEqual(built.json()["statements_created"], 1)
        page = self.client.get("/intake")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Import intake", page.text)
        batch_page = self.client.get(
            f"/intake/{self.batch['source_document_id']}"
        )
        self.assertEqual(batch_page.status_code, 200)


if __name__ == "__main__":
    unittest.main()
