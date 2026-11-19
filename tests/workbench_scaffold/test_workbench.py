"""Tests for the Workbench scaffold permissions and DB guard."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from workbench.config import SNAPSHOT_DB, V3_DB  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
