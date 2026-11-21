"""Tests for Workbench background import jobs."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from workbench import import_jobs  # noqa: E402
import workbench.db as wdb  # noqa: E402
from workbench.config import V3_DB  # noqa: E402
from workbench.main import app  # noqa: E402
from workbench.permissions import can_import  # noqa: E402

PDF_BYTES = b"%PDF-1.4\nfixture\n"
SOURCE = "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf"


@unittest.skipUnless(V3_DB.is_file(), "operational v3 DB not available")
class ImportJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.db = root / "v3.sqlite"
        shutil.copyfile(V3_DB, self.db)
        self.patchers = [
            mock.patch.object(wdb, "V3_DB", self.db),
            mock.patch.object(import_jobs, "UPLOAD_DIR", root / "uploads"),
            mock.patch.object(import_jobs, "_validate_url"),
            mock.patch.object(import_jobs, "_execute_job"),
        ]
        for patcher in self.patchers:
            patcher.start()
        import_jobs.ensure_schema()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tempdir.cleanup()

    def _insert_failed_job(self) -> str:
        job = import_jobs.create_url_job(SOURCE, issue_key="2026-33")
        import_jobs._update(job["job_id"], status="failed")
        return job["job_id"]

    def test_schema_creates_import_jobs_table(self) -> None:
        conn = sqlite3.connect(self.db)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        self.assertIn("import_jobs", names)

    def test_create_url_job_launches_v3_import_only(self) -> None:
        job = import_jobs.create_url_job(SOURCE, issue_key="2026-33")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["operation"], "full_import")
        self.assertEqual(job["issue_key"], "2026-33")
        self.assertIn("--target", job["argv"])
        self.assertEqual(job["argv"][job["argv"].index("--target") + 1], "v3")
        self.assertIn("--json", job["argv"])
        self.assertNotIn("--publish", job["argv"])
        self.assertNotIn("--db", job["argv"])

    def test_dry_run_job_records_operation(self) -> None:
        job = import_jobs.create_url_job(SOURCE, dry_run=True)
        self.assertEqual(job["operation"], "dry_run")
        self.assertTrue(job["dry_run"])
        self.assertIn("--dry-run", job["argv"])

    def test_issue_key_must_match_source_filename(self) -> None:
        with self.assertRaises(import_jobs.ImportJobError):
            import_jobs.create_url_job(SOURCE, issue_key="2026-34")
        with self.assertRaises(import_jobs.ImportJobError):
            import_jobs.create_url_job(SOURCE, issue_key="33-2026")

    def test_duplicate_active_source_is_rejected(self) -> None:
        import_jobs.create_url_job(SOURCE)
        with self.assertRaises(import_jobs.ImportJobConflict):
            import_jobs.create_url_job(SOURCE)

    def test_retry_reuses_argv_and_links_parent(self) -> None:
        job_id = self._insert_failed_job()
        retry = import_jobs.retry_job(job_id)
        self.assertEqual(retry["retry_of"], job_id)
        self.assertEqual(retry["status"], "queued")
        self.assertEqual(retry["argv"], import_jobs.get_job(job_id)["argv"])

    def test_retry_rejects_active_job(self) -> None:
        job = import_jobs.create_url_job(SOURCE)
        with self.assertRaises(import_jobs.ImportJobConflict):
            import_jobs.retry_job(job["job_id"])

    def test_decision_requires_reason_and_replaces_flags(self) -> None:
        job_id = self._insert_failed_job()
        with self.assertRaises(import_jobs.ImportJobError):
            import_jobs.decision_job(job_id, decision="reprocess", reason="  ")
        with self.assertRaises(import_jobs.ImportJobError):
            import_jobs.decision_job(job_id, decision="sideways", reason="x")
        job = import_jobs.decision_job(
            job_id, decision="reprocess", reason="OCR engine retest"
        )
        self.assertIn("--reprocess", job["argv"])
        self.assertIn("OCR engine retest", job["argv"])
        self.assertNotIn("--new-version", job["argv"])
        self.assertEqual(job["retry_of"], job_id)

    def test_result_mapping(self) -> None:
        cases = {
            "completed": "completed",
            "already_imported": "completed",
            "planned": "completed",
            "failed": "failed",
            "usage_error": "failed",
            "new_source_version_required": "needs_decision",
            "already_imported_content_conflict": "needs_decision",
        }
        for result_status, expected in cases.items():
            with self.subTest(result_status=result_status):
                job = import_jobs.create_url_job(SOURCE)
                import_jobs._apply_result(
                    job["job_id"],
                    code=0,
                    stdout=json.dumps({"status": result_status}),
                    stderr="",
                )
                stored = import_jobs.get_job(job["job_id"])
                self.assertEqual(stored["status"], expected)
                self.assertEqual(stored["result_status"], result_status)

    def test_missing_json_result_is_failed(self) -> None:
        job = import_jobs.create_url_job(SOURCE)
        import_jobs._apply_result(job["job_id"], code=1, stdout="", stderr="boom")
        stored = import_jobs.get_job(job["job_id"])
        self.assertEqual(stored["status"], "failed")
        self.assertIn("boom", stored["error_message"])

    def test_recover_stale_jobs_marks_interrupted(self) -> None:
        job = import_jobs.create_url_job(SOURCE)
        count = import_jobs.recover_stale_jobs()
        self.assertGreaterEqual(count, 1)
        stored = import_jobs.get_job(job["job_id"])
        self.assertEqual(stored["status"], "interrupted")
        self.assertEqual(stored["error_type"], "Interrupted")

    def test_upload_roundtrip(self) -> None:
        path = import_jobs.store_upload("anotariais-33-2026.pdf", PDF_BYTES)
        self.assertTrue(path.is_file())
        job = import_jobs.create_upload_job(path)
        self.assertEqual(job["source_kind"], "upload")
        self.assertEqual(job["issue_key"], "2026-33")
        self.assertIn("--allow-local-source", job["argv"])
        self.assertNotIn("--publish", job["argv"])

    def test_upload_rejects_non_pdf(self) -> None:
        with self.assertRaises(import_jobs.ImportJobError):
            import_jobs.store_upload("notes.txt", PDF_BYTES)
        with self.assertRaises(import_jobs.ImportJobError):
            import_jobs.store_upload("fake.pdf", b"not a pdf")

    def test_importer_url_policy_errors_are_validation_errors(self) -> None:
        # IP literals are rejected before any DNS lookup, so this is stable
        # offline.
        with self.assertRaises(import_jobs.ImportJobError):
            import_jobs._validate_url_policy(
                "https://127.0.0.1/anotariais-33-2026.pdf"
            )

    def test_job_detail_reads_manifest_and_log(self) -> None:
        job = import_jobs.create_url_job(SOURCE)
        run_dir = Path(self.tempdir.name) / "run"
        run_dir.mkdir()
        manifest = {
            "status": "completed",
            "download_status": "completed",
            "ocr_status": "completed",
            "v3_replay_status": "completed",
            "pdf_pages": 11,
            "v3": {"rows_inserted": 64},
        }
        manifest_path = run_dir / "import_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        log_path = Path(import_jobs.get_job(job["job_id"])["log_path"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("stage output\n", encoding="utf-8")
        import_jobs._update(
            job["job_id"],
            status="completed",
            manifest_path=str(manifest_path),
            run_dir=str(run_dir),
        )
        detail = import_jobs.job_detail(job["job_id"])
        self.assertEqual(detail["manifest"]["pdf_pages"], 11)
        self.assertIn("stage output", detail["log_tail"])
        stages = {step["name"]: step["status"] for step in detail["steps"]}
        self.assertEqual(stages["ocr"], "completed")
        self.assertEqual(stages["v3_replay"], "completed")


class ImportPermissionTests(unittest.TestCase):
    def test_import_capability(self) -> None:
        for role in ("reviewer", "adopter", "admin"):
            with self.subTest(role=role):
                self.assertTrue(can_import(role))
        for role in ("viewer", "publisher"):
            with self.subTest(role=role):
                self.assertFalse(can_import(role))


@unittest.skipUnless(V3_DB.is_file(), "operational v3 DB not available")
class ImportApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.db = root / "v3.sqlite"
        shutil.copyfile(V3_DB, self.db)
        self.patchers = [
            mock.patch.object(wdb, "V3_DB", self.db),
            mock.patch.object(import_jobs, "UPLOAD_DIR", root / "uploads"),
            mock.patch.object(import_jobs, "_validate_url"),
            mock.patch.object(import_jobs, "_execute_job"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tempdir.cleanup()

    def test_viewer_cannot_import(self) -> None:
        response = self.client.post(
            "/api/imports",
            json={"source": SOURCE},
            headers={"X-Workbench-Role": "viewer"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_create_and_read_job(self) -> None:
        created = self.client.post(
            "/api/imports",
            json={"source": SOURCE, "issue_key": "2026-33"},
            headers={"X-Workbench-Role": "admin"},
        )
        self.assertEqual(created.status_code, 202, created.text)
        job = created.json()
        self.assertEqual(job["status"], "queued")
        listing = self.client.get("/api/imports")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["job_id"], job["job_id"])
        detail = self.client.get(f"/api/imports/{job['job_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("steps", detail.json())
        log = self.client.get(f"/api/imports/{job['job_id']}/log")
        self.assertEqual(log.status_code, 200)
        page = self.client.get("/imports")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Import issue", page.text)
        job_page = self.client.get(f"/imports/{job['job_id']}")
        self.assertEqual(job_page.status_code, 200)

    def test_http_source_is_rejected(self) -> None:
        with mock.patch.object(
            import_jobs, "_validate_url", side_effect=import_jobs.ImportJobError(
                "V3 source URL must use HTTPS"
            )
        ):
            response = self.client.post(
                "/api/imports",
                json={"source": "http://example.test/a.pdf"},
                headers={"X-Workbench-Role": "admin"},
            )
        self.assertEqual(response.status_code, 400)

    def test_upload_endpoint(self) -> None:
        response = self.client.post(
            "/api/imports/upload",
            content=PDF_BYTES,
            headers={
                "X-Workbench-Role": "reviewer",
                "X-Filename": "anotariais-34-2026.pdf",
                "Content-Type": "application/pdf",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["issue_key"], "2026-34")

    def test_retry_conflict_and_not_found(self) -> None:
        job = self.client.post(
            "/api/imports",
            json={"source": SOURCE},
            headers={"X-Workbench-Role": "admin"},
        ).json()
        retry = self.client.post(
            f"/api/imports/{job['job_id']}/retry",
            headers={"X-Workbench-Role": "admin"},
        )
        self.assertEqual(retry.status_code, 409)
        missing = self.client.get("/api/imports/does-not-exist")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
