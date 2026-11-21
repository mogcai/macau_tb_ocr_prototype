"""Contract tests for manifest identity, page coverage and replay idempotency."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid

import fitz
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.build_v3_db import apply_schema  # noqa: E402
from tools import replay_runs_to_v3 as replay_module  # noqa: E402
from tools.replay_runs_to_v3 import (  # noqa: E402
    EvidenceChangedError,
    PageCoverageError,
    replay_run,
    resolve_issue_key,
)


class V3ReplayContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "v3.sqlite"
        self.run_dir = self.root / "issue99_2026__job_test"
        self.run_dir.mkdir()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        apply_schema(self.conn)
        self.pdf_sha = ""

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def write_run(
        self,
        *,
        pdf_pages: int = 2,
        document_statuses: dict[int, str] | list[tuple[int, str]] | None = None,
        include_manifest: bool = True,
        issue_key: str = "2026-99",
        tb_rows: list[dict[str, str]] | None = None,
    ) -> None:
        statuses = document_statuses or {1: "completed", 2: "skipped"}
        status_items = (
            list(statuses.items()) if isinstance(statuses, dict) else list(statuses)
        )
        pdf_path = self.root / f"source_{uuid.uuid4().hex}.pdf"
        pdf = fitz.open()
        for _ in range(pdf_pages):
            pdf.new_page(width=200, height=200)
        pdf.save(pdf_path)
        pdf.close()
        self.pdf_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        report = {
            "app_version": "0.7.0",
            "source_pdf": str(pdf_path),
            "source_pdf_sha256": self.pdf_sha,
            "source_pdf_size": pdf_path.stat().st_size,
            "pdf_pages": pdf_pages,
            "ocr_engine": "vision",
            "ocr_version": "test",
        }
        (self.run_dir / "run_report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        with (self.run_dir / "documents.csv").open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["pdf_page", "printed_page", "ocr_status"],
            )
            writer.writeheader()
            for page, status in status_items:
                writer.writerow({
                    "pdf_page": str(page),
                    "printed_page": str(page + 100),
                    "ocr_status": status,
                })
        rows = tb_rows if tb_rows is not None else [{
            "pdf_page": "1",
            "label_raw": "現金",
            "label_normalized": "現金",
            "label_role": "parent",
            "parent_label_raw": "",
            "debit_raw": "1,234.50",
            "credit_raw": "",
            "debit_amount": "1234.50",
            "credit_amount": "",
            "debit_parse_status": "parsed",
            "credit_parse_status": "blank",
            "row_confidence": "0.9",
        }]
        with (self.run_dir / "tb_rows.csv").open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "pdf_page", "label_raw", "label_normalized", "label_role",
                    "parent_label_raw", "debit_raw", "credit_raw",
                    "debit_amount", "credit_amount", "debit_parse_status",
                    "credit_parse_status", "row_confidence",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
        if include_manifest:
            (self.run_dir / "import_manifest.json").write_text(
                json.dumps({
                    "manifest_version": 2,
                    "manifest_origin": "written",
                    "target": "v3",
                    "operation": "full_import",
                    "issue_key": issue_key,
                    "source_url": "https://images.bo.dsaj.gov.mo/issue99_2026.pdf",
                    "pdf_path": str(pdf_path),
                    "pdf_sha256": self.pdf_sha,
                    "pdf_bytes": pdf_path.stat().st_size,
                    "run_dir": str(self.run_dir),
                    "v3_db": str(self.db_path),
                }),
                encoding="utf-8",
            )

    def replay(self) -> dict:
        result = replay_run(self.conn, self.run_dir, legacy_runs={})
        self.conn.commit()
        return result

    def test_strict_replay_records_coverage_provenance_and_status(self) -> None:
        self.write_run()
        result = self.replay()
        self.assertEqual(result["pdf_pages"], 2)
        self.assertEqual(result["ocr_pages_observed"], 2)
        self.assertEqual(result["ocr_skipped_pages"], 1)
        self.assertEqual(result["ocr_failed_pages"], 0)
        self.assertEqual(result["run_status"], "completed")
        document = self.conn.execute(
            "SELECT page_count, source_url FROM source_documents"
        ).fetchone()
        self.assertEqual(document["page_count"], 2)
        self.assertEqual(
            document["source_url"],
            "https://images.bo.dsaj.gov.mo/issue99_2026.pdf",
        )
        page_count = self.conn.execute(
            "SELECT COUNT(*) FROM document_pages"
        ).fetchone()[0]
        page_extractions = self.conn.execute(
            "SELECT COUNT(*) FROM page_extractions"
        ).fetchone()[0]
        self.assertEqual(page_count, 2)
        self.assertEqual(page_extractions, 2)
        config = json.loads(self.conn.execute(
            "SELECT config_json FROM extraction_runs"
        ).fetchone()[0])
        self.assertEqual(config["ocr_page_coverage"], "full")
        self.assertIn("evidence_fingerprint", config)

    def test_failed_page_makes_run_partial_but_skipped_does_not(self) -> None:
        self.write_run(document_statuses={1: "completed", 2: "failed"})
        result = self.replay()
        self.assertEqual(result["run_status"], "partial")
        self.assertEqual(result["ocr_failed_pages"], 1)
        status = self.conn.execute(
            "SELECT run_status FROM extraction_runs"
        ).fetchone()[0]
        self.assertEqual(status, "partial")

    def test_missing_duplicate_or_out_of_range_page_is_rejected(self) -> None:
        cases = [
            ({1: "completed"}, 2),
            ([(1, "completed"), (1, "completed"), (2, "skipped")], 2),
            ({1: "completed", 2: "skipped", 3: "skipped"}, 2),
        ]
        for statuses, pdf_pages in cases:
            with self.subTest(statuses=statuses):
                with tempfile.TemporaryDirectory() as temp:
                    self.run_dir = Path(temp) / "issue99_2026__job_bad"
                    self.run_dir.mkdir()
                    self.write_run(pdf_pages=pdf_pages, document_statuses=statuses)
                    with self.assertRaises(PageCoverageError):
                        replay_run(self.conn, self.run_dir, legacy_runs={})
                    self.conn.rollback()

    def test_same_row_count_but_changed_evidence_fails_and_rolls_back(self) -> None:
        self.write_run()
        self.replay()
        rows_path = self.run_dir / "tb_rows.csv"
        content = rows_path.read_text(encoding="utf-8-sig").replace(
            "1,234.50", "9,999.50"
        )
        rows_path.write_text(content, encoding="utf-8-sig")
        with self.assertRaises(EvidenceChangedError):
            replay_run(self.conn, self.run_dir, legacy_runs={})
        self.conn.rollback()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM extracted_rows"
        ).fetchone()[0]
        self.assertEqual(count, 1)
        value = self.conn.execute(
            "SELECT debit_raw FROM extracted_rows"
        ).fetchone()[0]
        self.assertEqual(value, "1,234.50")

    def test_same_run_reuses_run_identity(self) -> None:
        self.write_run()
        first = self.replay()
        second = self.replay()
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertTrue(second["already_present"])
        self.assertEqual(second["rows_inserted"], 0)

    def test_legacy_run_gets_page_count_and_coverage_without_status_change(self) -> None:
        self.write_run(
            pdf_pages=3,
            document_statuses={1: "completed"},
            include_manifest=False,
        )
        result = self.replay()
        document = self.conn.execute(
            "SELECT page_count FROM source_documents"
        ).fetchone()
        self.assertEqual(document["page_count"], 3)
        config = json.loads(self.conn.execute(
            "SELECT config_json FROM extraction_runs"
        ).fetchone()[0])
        self.assertEqual(config["ocr_page_coverage"], "legacy_partial")
        self.assertEqual(config["manifest_origin"], "reconstructed")
        self.assertEqual(result["run_status"], "completed")

    def test_no_v5_lookup_never_opens_v5(self) -> None:
        self.write_run()
        argv = [
            "replay_runs_to_v3.py",
            "--v3-db", str(self.db_path),
            "--run-dir", str(self.run_dir),
            "--no-v5-lookup",
        ]
        with mock.patch.object(
            replay_module, "load_legacy_runs",
            side_effect=AssertionError("V5 lookup must not run"),
        ), mock.patch.object(sys, "argv", argv):
            with redirect_stdout(io.StringIO()):
                code = replay_module.main()
        self.assertEqual(code, 0)

    def test_legacy_equal_page_count_is_still_legacy_partial(self) -> None:
        self.write_run(
            pdf_pages=2,
            document_statuses={1: "completed", 2: "skipped"},
            include_manifest=False,
        )
        result = self.replay()
        self.assertEqual(result["ocr_page_coverage"], "legacy_partial")
        self.assertNotEqual(result["ocr_page_coverage"], "full")

    def test_invalid_pdf_pages_does_not_overwrite_existing_page_count(self) -> None:
        self.write_run(pdf_pages=2, include_manifest=False)
        report_path = self.run_dir / "run_report.json"
        report = json.loads(report_path.read_text())
        report["pdf_pages"] = "2.5"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        sha = self.pdf_sha
        self.conn.execute(
            "INSERT INTO source_documents("
            "source_document_id,gazette_issue_id,document_key,storage_uri,sha256,"
            "page_count,acquired_at) VALUES('existing',NULL,?,?,?,5,?)",
            (f"2026-99:{sha[:16]}", str(self.root / "old.pdf"), sha,
             "2026-11-21T00:00:00+00:00"),
        )
        self.conn.commit()
        result = self.replay()
        page_count = self.conn.execute(
            "SELECT page_count FROM source_documents WHERE source_document_id='existing'"
        ).fetchone()[0]
        self.assertEqual(page_count, 5)
        self.assertEqual(result["ocr_page_coverage"], "unknown")
        self.assertIsNone(result["pdf_pages"])

    def test_corrupt_manifest_is_rejected_not_downgraded(self) -> None:
        self.write_run()
        (self.run_dir / "import_manifest.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(replay_module.ManifestError):
            replay_run(self.conn, self.run_dir, legacy_runs={})

    def test_strict_invalid_tb_row_page_is_rejected(self) -> None:
        self.write_run(tb_rows=[{
            "pdf_page": "2.5", "label_raw": "x", "label_normalized": "x",
            "label_role": "item", "parent_label_raw": "", "debit_raw": "",
            "credit_raw": "", "debit_amount": "", "credit_amount": "",
            "debit_parse_status": "blank", "credit_parse_status": "blank",
            "row_confidence": "0.9",
        }])
        with self.assertRaises(PageCoverageError):
            replay_run(self.conn, self.run_dir, legacy_runs={})
        self.conn.rollback()

    def test_pdf_hash_mismatch_is_rejected(self) -> None:
        self.write_run()
        pdf_path = Path(json.loads(
            (self.run_dir / "run_report.json").read_text()
        )["source_pdf"])
        pdf_path.write_bytes(b"%PDF-1.4\nchanged\n")
        with self.assertRaises(EvidenceChangedError):
            replay_run(self.conn, self.run_dir, legacy_runs={})
        self.conn.rollback()

    def test_actual_pdf_page_count_mismatch_is_rejected(self) -> None:
        self.write_run(pdf_pages=2)
        report_path = self.run_dir / "run_report.json"
        report = json.loads(report_path.read_text())
        report["pdf_pages"] = 3
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaises(PageCoverageError):
            replay_run(self.conn, self.run_dir, legacy_runs={})
        self.conn.rollback()

    def test_explicit_issue_key_must_match_manifest_and_directory(self) -> None:
        manifest = {"issue_key": "2026-99"}
        self.assertEqual(
            resolve_issue_key(self.run_dir, manifest, "2026-99"), "2026-99"
        )
        with self.assertRaises(ValueError):
            resolve_issue_key(self.run_dir, manifest, "2026-98")
        with self.assertRaises(ValueError):
            resolve_issue_key(self.run_dir, {"issue_key": "2026-98"}, None)


if __name__ == "__main__":
    unittest.main()
