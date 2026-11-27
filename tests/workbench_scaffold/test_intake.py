"""Tests for Workbench import intake, page decisions and statement creation."""
from __future__ import annotations

import csv
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
from workbench.main import app  # noqa: E402
from workbench.permissions import can_classify  # noqa: E402

NOW = "2026-11-21T00:00:00+00:00"
DOC_ID = "doc-intake-fixture"


def _approved_entities(db: Path) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row) for row in conn.execute(
                "SELECT entity_id, entity_key, canonical_zh, canonical_pt "
                "FROM reporting_entities WHERE registry_status = 'approved' "
                "AND (canonical_zh IS NOT NULL OR canonical_pt IS NOT NULL) "
                "ORDER BY entity_id"
            )
        ]
    finally:
        conn.close()


def _entity_name(entity: dict) -> str:
    return entity.get("canonical_zh") or entity.get("canonical_pt")


def _insert_fixture(db: Path, run_dir: Path) -> tuple[dict, dict]:
    """Create a small unclassified import: 1 skipped page + 2 statement pages."""
    entities = _approved_entities(db)
    if len(entities) < 2:
        raise unittest.SkipTest("need two approved entities")
    first, second = entities[0], entities[1]
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "documents.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "pdf_page", "bank_name", "statement_date", "ocr_status",
                "skip_reason", "label_column_x0",
            ],
        )
        writer.writeheader()
        writer.writerow({"pdf_page": 1, "bank_name": "", "statement_date": "",
                         "ocr_status": "skipped", "skip_reason": "not_tb_page",
                         "label_column_x0": ""})
        writer.writerow({"pdf_page": 2, "bank_name": _entity_name(first),
                         "statement_date": "2099-12-31", "ocr_status": "completed",
                         "skip_reason": "", "label_column_x0": "0.005"})
        writer.writerow({"pdf_page": 3, "bank_name": _entity_name(second),
                         "statement_date": "2099-12-31", "ocr_status": "completed",
                         "skip_reason": "", "label_column_x0": "0.005"})
    with (run_dir / "tb_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pdf_page", "label_raw", "debit_raw", "credit_raw", "review_reason"],
        )
        writer.writeheader()
        writer.writerow({"pdf_page": 2, "label_raw": "利息收入", "debit_raw": "1.00",
                         "credit_raw": "", "review_reason": ""})
        writer.writerow({"pdf_page": 2, "label_raw": "現金", "debit_raw": "2.00",
                         "credit_raw": "", "review_reason": "low_ocr_confidence"})
        writer.writerow({"pdf_page": 3, "label_raw": "總額", "debit_raw": "3.00",
                         "credit_raw": "3.00", "review_reason": ""})
    (run_dir / "run_report.json").write_text(
        '{"tb_rows_extracted": 3, "rows_for_review": 1, "tb_pages_detected": 2, '
        '"ocr_engine": "vision", "vision_level": "accurate"}',
        encoding="utf-8",
    )
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO gazette_issues(gazette_issue_id, issue_key, publication_date, "
            "discovered_at) VALUES('issue-intake-fixture', '2099-01', '2099-01-01', ?)",
            (NOW,),
        )
        conn.execute(
            "INSERT INTO source_documents(source_document_id, gazette_issue_id, "
            "document_key, original_filename, storage_uri, sha256, page_count, acquired_at) "
            "VALUES(?, 'issue-intake-fixture', '2099-01:intakefixture', "
            "'anotariais-1-2099.pdf', ?, ?, 3, ?)",
            (DOC_ID, str(run_dir / "fixture.pdf"), "a" * 64, NOW),
        )
        for page in (1, 2, 3):
            conn.execute(
                "INSERT INTO document_pages(document_page_id, source_document_id, pdf_page) "
                "VALUES(?, ?, ?)",
                (f"page-fixture-{page}", DOC_ID, page),
            )
        conn.execute(
            "INSERT INTO extraction_runs(run_id, source_document_id, run_status, "
            "run_report_uri, completed_at) VALUES('run-intake-fixture', ?, 'completed', ?, ?)",
            (DOC_ID, str(run_dir / "run_report.json"), NOW),
        )
        rows = {
            1: [],
            2: [("利息收入", 1), ("現金", 2)],
            3: [("總額", 1)],
        }
        for page, labels in rows.items():
            conn.execute(
                "INSERT INTO page_extractions(page_extraction_id, run_id, document_page_id, "
                "source_document_id, ocr_status, created_at) VALUES(?, 'run-intake-fixture', ?, ?, ?, ?)",
                (
                    f"pe-fixture-{page}", f"page-fixture-{page}", DOC_ID,
                    "skipped" if page == 1 else "completed", NOW,
                ),
            )
            for label, order in labels:
                conn.execute(
                    "INSERT INTO extracted_rows(extracted_row_id, page_extraction_id, "
                    "document_page_id, run_id, row_disposition, page_row_order, label_raw, "
                    "row_type_proposed, debit_value_state, credit_value_state, created_at) "
                    "VALUES(?, ?, ?, 'run-intake-fixture', 'unassigned', ?, ?, 'item', "
                    "'blank', 'blank', ?)",
                    (
                        f"row-fixture-{page}-{order}", f"pe-fixture-{page}",
                        f"page-fixture-{page}", order, label, NOW,
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    return first, second


def _bootstrap_v3_fixture_db(dest: Path) -> None:
    """Build a hermetic schema + two approved entities.

    Never copy the operational database: tests must not inherit its schema
    state (a broken operational DB must not make a fixture pass or fail).
    """
    from tools.build_v3_db import apply_schema, seed_metadata

    conn = sqlite3.connect(dest)
    try:
        apply_schema(conn)
        seed_metadata(conn, baseline=None, artifact_manifest=None)
        for index, (key, zh, pt) in enumerate(
            (
                ("bank_fixture_a", "測試銀行甲", "Banco Fixture A"),
                ("bank_fixture_b", "測試銀行乙", "Banco Fixture B"),
            ),
            start=1,
        ):
            conn.execute(
                "INSERT INTO reporting_entities("
                "entity_id, entity_key, entity_type, registry_status, "
                "canonical_zh, canonical_pt, created_at) "
                "VALUES (?, ?, 'bank', 'approved', ?, ?, ?)",
                (f"entity-fixture-{index}", key, zh, pt, NOW),
            )
        conn.commit()
    finally:
        conn.close()


SINGLE_COLUMN_FK = (
    "FOREIGN KEY (extracted_row_id)\n"
    "        REFERENCES extracted_rows(extracted_row_id)"
)
COMPOSITE_FK = (
    "FOREIGN KEY (extracted_row_id, statement_extraction_id)\n"
    "        REFERENCES extracted_rows(extracted_row_id, statement_extraction_id)"
)


def _downgrade_statement_rows_to_composite_fk(db: Path) -> None:
    """Restore the pre-discard composite FK so migration tests start from it."""
    conn = sqlite3.connect(db)
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='statement_rows'"
        ).fetchone()[0]
        downgraded = sql.replace(SINGLE_COLUMN_FK, COMPOSITE_FK)
        if downgraded == sql:
            raise AssertionError("statement_rows does not carry the expected FK")
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE name='statement_rows'", (downgraded,)
        )
        version = conn.execute("PRAGMA schema_version").fetchone()[0]
        conn.execute(f"PRAGMA schema_version={version + 1}")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()
    finally:
        conn.close()


def _simulate_old_discard_fk_migration(db: Path) -> None:
    """Reproduce the 2026-11-22 rename-based migration that damaged child FKs."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS trg_statement_rows_no_update;
            DROP TRIGGER IF EXISTS trg_statement_rows_no_delete;
            ALTER TABLE statement_rows RENAME TO statement_rows__pre_discard_fk;
            """
        )
        conn.executescript(intake._STATEMENT_ROWS_DISCARD_DDL)
        conn.executescript(
            """
            INSERT INTO statement_rows SELECT * FROM statement_rows__pre_discard_fk;
            DROP TABLE statement_rows__pre_discard_fk;
            CREATE INDEX IF NOT EXISTS idx_statement_rows_extraction_order
                ON statement_rows(statement_extraction_id, statement_order);
            CREATE INDEX IF NOT EXISTS idx_statement_rows_statement_extraction
                ON statement_rows(statement_id, statement_extraction_id);
            CREATE INDEX IF NOT EXISTS idx_statement_rows_fingerprint
                ON statement_rows(row_fingerprint);
            CREATE TRIGGER IF NOT EXISTS trg_statement_rows_no_update
            BEFORE UPDATE ON statement_rows
            BEGIN
                SELECT RAISE(ABORT, 'statement_rows is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_statement_rows_no_delete
            BEFORE DELETE ON statement_rows
            BEGIN
                SELECT RAISE(ABORT, 'statement_rows is append-only');
            END;
            """
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()


def _append_ocr_run(db: Path, run_dir: Path, run_id: str, created_at: str) -> None:
    """Copy the fixture's page rows into a second OCR run (all unassigned)."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO extraction_runs(run_id, source_document_id, run_status, "
            "run_report_uri, completed_at) VALUES(?, ?, 'completed', ?, ?)",
            (run_id, DOC_ID, str(run_dir / "run_report.json"), created_at),
        )
        for table, key in (
            ("page_extractions", "page_extraction_id"),
            ("extracted_rows", "extracted_row_id"),
        ):
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for row in rows:
                row[key] = f"{row[key]}--{run_id}"
                row["run_id"] = run_id
                if table == "extracted_rows":
                    row["page_extraction_id"] = f"{row['page_extraction_id']}--{run_id}"
                    row["row_disposition"] = "unassigned"
                    row["statement_id"] = None
                    row["statement_extraction_id"] = None
                    row["statement_order"] = None
                conn.execute(
                    f"INSERT INTO {table}({','.join(columns)}) "
                    f"VALUES({','.join('?' * len(columns))})",
                    [row[column] for column in columns],
                )
        conn.commit()
    finally:
        conn.close()


class _IntakeTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        _bootstrap_v3_fixture_db(self.db)
        self.patchers = [
            mock.patch.object(wdb, "V3_DB", self.db),
            mock.patch.object(intake, "_auto_map", return_value={"status": "stubbed"}),
        ]
        for patcher in self.patchers:
            patcher.start()
        intake._initialised.clear()
        intake.ensure_schema()
        self.run_dir = Path(self.tempdir.name) / "run"
        self.first, self.second = _insert_fixture(self.db, self.run_dir)
        self.detail = intake.batch_detail(DOC_ID)

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tempdir.cleanup()

    def _save(self, page_number: int, **kwargs) -> None:
        page = next(
            item for item in self.detail["pages"] if item["pdf_page"] == page_number
        )
        intake.save_page_decision(
            DOC_ID, page["document_page_id"], actor="tester", **kwargs
        )

    def _classify_all(self, *, reject_page: int | None = None) -> None:
        for page in self.detail["pages"]:
            if reject_page is not None and page["pdf_page"] == reject_page:
                self._save(
                    page["pdf_page"], page_role="other", decision="reject",
                    note="not a financial statement",
                )
                continue
            entity = self.first if page["pdf_page"] == 2 else self.second
            self._save(
                page["pdf_page"],
                page_role="financial_statement",
                decision="approve",
                entity_id=entity["entity_id"],
                statement_date="2099-12-31",
                statement_type="balance_sheet",
            )

    def _rows(self, sql: str, params: tuple = ()) -> int:
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()


class IntakeTests(_IntakeTestBase):
    def test_batch_detail_lists_pages_entities_and_pdf_suggestions(self) -> None:
        self.assertEqual(len(self.detail["pages"]), 3)
        self.assertTrue(self.detail["entities"])
        self.assertEqual(self.detail["pending_pages"], 3)
        self.assertFalse(self.detail["can_build"])
        page_two = self.detail["pages"][1]
        self.assertEqual(page_two["suggested_entity_id"], self.first["entity_id"])
        self.assertEqual(page_two["suggested_method"], "exact")
        self.assertEqual(page_two["pdf_statement_date"], "2099-12-31")
        self.assertEqual(self.detail["suggestion_summary"]["suggested_pages"], 2)

    def test_financial_approval_requires_entity_and_date(self) -> None:
        with self.assertRaises(intake.IntakeError):
            self._save(2, page_role="financial_statement", decision="approve")
        with self.assertRaises(intake.IntakeError):
            self._save(
                2, page_role="financial_statement", decision="approve",
                entity_id=self.first["entity_id"], statement_date="2099-13-01",
            )

    def test_reject_requires_note(self) -> None:
        with self.assertRaises(intake.IntakeError):
            self._save(2, page_role="other", decision="reject", note=" ")

    def test_bulk_reject_rejects_multiple_pages_with_one_reason(self) -> None:
        page_ids = [page["document_page_id"] for page in self.detail["pages"]]
        result = intake.reject_pages(
            DOC_ID,
            page_ids + [page_ids[0]],
            actor="tester",
            note="not a financial statement",
        )
        self.assertEqual(result["rejected_count"], 3)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["pending_pages"], 0)
        self.assertEqual(
            len({item["document_page_id"] for item in result["rejected"]}), 3
        )
        conn = sqlite3.connect(self.db)
        try:
            states = conn.execute(
                "SELECT page_role, classification_status, classified_by "
                "FROM document_pages WHERE source_document_id = ? "
                "ORDER BY pdf_page",
                (DOC_ID,),
            ).fetchall()
            audit = conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE source_document_id = ? AND event_type = 'page_classified' "
                "AND reason = 'not a financial statement'",
                (DOC_ID,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(
            states,
            [("other", "rejected", "tester")] * 3,
        )
        self.assertEqual(audit, 3)

    def test_bulk_reject_requires_a_note_and_a_selection(self) -> None:
        page_ids = [page["document_page_id"] for page in self.detail["pages"]]
        with self.assertRaises(intake.IntakeError):
            intake.reject_pages(DOC_ID, [], actor="tester", note="x")
        with self.assertRaises(intake.IntakeError):
            intake.reject_pages(DOC_ID, page_ids, actor="tester", note=" ")

    def test_bulk_reject_skips_pages_locked_to_statements(self) -> None:
        self._classify_all()
        intake.build_statements(DOC_ID, "tester")
        locked_ids = [
            page["document_page_id"]
            for page in self.detail["pages"]
            if page["pdf_page"] in (2, 3)
        ]
        result = intake.reject_pages(
            DOC_ID,
            locked_ids,
            actor="tester",
            note="late bulk reject",
        )
        self.assertEqual(result["rejected_count"], 0)
        self.assertEqual(result["skipped_count"], 2)
        self.assertTrue(
            all("Discard" in item["reason"] for item in result["skipped"])
        )

    def test_decision_writes_page_state_decision_and_audit(self) -> None:
        self._save(
            2, page_role="financial_statement", decision="approve",
            entity_id=self.first["entity_id"], statement_date="2099-12-31",
            note="reviewed",
        )
        conn = sqlite3.connect(self.db)
        try:
            state = conn.execute(
                "SELECT page_role, classification_status, classification_source, "
                "classified_by FROM document_pages WHERE document_page_id = 'page-fixture-2'"
            ).fetchone()
            decision = conn.execute(
                "SELECT reporting_entity_id, statement_date, statement_type "
                "FROM page_statement_decisions WHERE document_page_id = 'page-fixture-2'"
            ).fetchone()
            audit = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE document_page_id = 'page-fixture-2' "
                "AND event_type = 'page_classified'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(state[:3], ("financial_statement", "approved", "human"))
        self.assertEqual(state[3], "tester")
        self.assertEqual(
            decision, (self.first["entity_id"], "2099-12-31", "balance_sheet")
        )
        self.assertEqual(audit, 1)

    def test_build_requires_every_page_classified(self) -> None:
        with self.assertRaises(intake.IntakeError):
            intake.build_statements(DOC_ID, "tester")

    def test_auto_classify_uses_pdf_text_and_builds_one_statement_per_bank(self) -> None:
        result = intake.auto_classify(DOC_ID, actor="tester")
        self.assertEqual(result["financial"], 2)
        self.assertEqual(result["empty"], 1)
        self.assertEqual(result["unmatched"], [])
        self.assertEqual(result["detail"]["pending_pages"], 0)
        self.assertTrue(result["detail"]["can_build"])

        summary = intake.build_statements(DOC_ID, "tester")
        self.assertEqual(summary["statements_created"], 2)
        self.assertEqual(summary["rows_assigned"], 3)
        self.assertEqual(
            {statement["entity_name"] for statement in summary["statements"]},
            {_entity_name(self.first), _entity_name(self.second)},
        )
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM extracted_rows WHERE document_page_id = 'page-fixture-1' "
            "AND row_disposition = 'excluded_page_role'"
        ), 0)
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM extracted_rows WHERE document_page_id = 'page-fixture-1' "
            "AND row_disposition = 'unassigned'"
        ), 0)

        queue_ids = {item["statement_id"] for item in service.review_queue()}
        for statement in summary["statements"]:
            self.assertIn(statement["statement_id"], queue_ids)

        again = intake.build_statements(DOC_ID, "tester")
        self.assertEqual(again["statements_created"], 0)
        self.assertEqual(again["statements_existing"], 2)
        self.assertEqual(again["rows_assigned"], 0)

    def test_rejected_page_rows_are_excluded(self) -> None:
        self._classify_all(reject_page=2)
        summary = intake.build_statements(DOC_ID, "tester")
        self.assertEqual(summary["rows_excluded"], 2)
        self.assertEqual(summary["statements_created"], 1)
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM extracted_rows WHERE document_page_id = 'page-fixture-2' "
            "AND row_disposition = 'excluded_page_role'"
        ), 2)

    def test_classification_is_locked_after_assignment(self) -> None:
        self._classify_all()
        intake.build_statements(DOC_ID, "tester")
        with self.assertRaises(intake.IntakeConflict):
            self._save(
                2, page_role="other", decision="reject", note="late change",
            )

    def test_locked_page_error_names_statement_and_remedy(self) -> None:
        self._classify_all()
        intake.build_statements(DOC_ID, "tester")
        with self.assertRaises(intake.IntakeConflict) as raised:
            self._save(
                2,
                page_role="financial_statement",
                decision="approve",
                entity_id=self.second["entity_id"],
                statement_date="2099-12-31",
            )
        message = str(raised.exception)
        self.assertIn(_entity_name(self.first), message)
        self.assertIn("Discard", message)

    def test_older_run_assignment_still_locks_the_page(self) -> None:
        self._classify_all()
        intake.build_statements(DOC_ID, "tester")
        _append_ocr_run(
            self.db, self.run_dir, "run-intake-fixture-2", "2026-11-22T00:00:00+00:00"
        )
        detail = intake.batch_detail(DOC_ID)
        page_two = next(item for item in detail["pages"] if item["pdf_page"] == 2)
        self.assertEqual(page_two["included_rows"], 0)
        self.assertGreater(page_two["locked_rows"], 0)
        self.assertEqual(page_two["assigned_entity_name"], _entity_name(self.first))
        with self.assertRaises(intake.IntakeConflict) as raised:
            self._save(
                2,
                page_role="financial_statement",
                decision="approve",
                entity_id=self.second["entity_id"],
                statement_date="2099-12-31",
            )
        self.assertIn("Discard", str(raised.exception))

    def test_batch_detail_shows_the_assigned_statement(self) -> None:
        self._classify_all()
        intake.build_statements(DOC_ID, "tester")
        detail = intake.batch_detail(DOC_ID)
        page_two = next(item for item in detail["pages"] if item["pdf_page"] == 2)
        self.assertTrue(page_two["assigned_statement_id"])
        self.assertEqual(page_two["assigned_entity_name"], _entity_name(self.first))
        self.assertEqual(page_two["assigned_statement_status"], "proposed")

    def test_ocr_quality_preview_is_attached(self) -> None:
        quality = self.detail["ocr_quality"]
        self.assertIsNotNone(quality)
        self.assertEqual(quality["rows"], 3)
        self.assertEqual(quality["review_rows"], 1)
        self.assertEqual(quality["empty_labels"], 0)
        self.assertEqual(quality["amount_in_label"], 0)
        self.assertFalse(quality["warning"])
        self.assertEqual(self.detail["latest_run_id"], "run-intake-fixture")

    def test_discard_pending_statement_unassigns_rows(self) -> None:
        self._classify_all()
        summary = intake.build_statements(DOC_ID, "tester")
        statement_id = summary["statements"][0]["statement_id"]
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM extracted_rows WHERE statement_id = ?",
            (statement_id,),
        ), summary["statements"][0]["rows"])

        snapshot_rows = self._rows(
            "SELECT COUNT(*) FROM statement_rows WHERE statement_id = ?",
            (statement_id,),
        )
        self.assertGreater(snapshot_rows, 0)

        result = intake.discard_pending_statement(
            statement_id, "tester", reason="Bad OCR — discard for reprocess"
        )
        self.assertEqual(result["statement_status"], "excluded")
        self.assertGreater(result["rows_unassigned"], 0)
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM extracted_rows WHERE statement_id = ?",
            (statement_id,),
        ), 0)
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM extracted_rows WHERE row_disposition = 'unassigned' "
            "AND extracted_row_id IN ("
            "SELECT extracted_row_id FROM statement_rows WHERE statement_id = ?)",
            (statement_id,),
        ), snapshot_rows)
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM statement_rows WHERE statement_id = ?",
            (statement_id,),
        ), snapshot_rows)
        self.assertEqual(self._rows(
            "SELECT COUNT(*) FROM statements WHERE statement_id = ? "
            "AND statement_status = 'excluded'",
            (statement_id,),
        ), 1)
        queue_ids = {item["statement_id"] for item in service.review_queue()}
        self.assertNotIn(statement_id, queue_ids)

        detail = intake.batch_detail(DOC_ID)
        unlocked = [
            item for item in detail["pages"]
            if item["included_rows"] == 0 and item["total_rows"] > 0
        ]
        self.assertTrue(unlocked)

    def test_discard_refuses_reviewed_statement(self) -> None:
        self._classify_all()
        summary = intake.build_statements(DOC_ID, "tester")
        statement_id = summary["statements"][0]["statement_id"]
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO statement_reviews(statement_id, review_status, "
                "reviewed_by, reviewed_at, updated_at) "
                "VALUES (?, 'reviewed', 'tester', ?, ?)",
                (statement_id, NOW, NOW),
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(intake.IntakeConflict):
            intake.discard_pending_statement(
                statement_id, "tester", reason="should fail"
            )

    def test_discard_then_rebuild_reopens_statement_for_review(self) -> None:
        self._classify_all()
        first = intake.build_statements(DOC_ID, "tester")
        statement_id = first["statements"][0]["statement_id"]
        intake.discard_pending_statement(
            statement_id, "tester", reason="OCR columns misaligned"
        )
        self.assertNotIn(
            statement_id, {item["statement_id"] for item in service.review_queue()}
        )

        _append_ocr_run(
            self.db, self.run_dir, "run-intake-fixture-2", "2026-11-22T00:00:00+00:00"
        )
        rebuilt = intake.build_statements(DOC_ID, "tester")
        self.assertEqual(rebuilt["statements_existing"], 2)
        self.assertEqual(rebuilt["statements_reopened"], 1)
        self.assertEqual(
            self._rows(
                "SELECT statement_status FROM statements WHERE statement_id = ?",
                (statement_id,),
            ),
            "proposed",
        )
        self.assertIn(
            statement_id, {item["statement_id"] for item in service.review_queue()}
        )
        self.assertGreater(
            self._rows(
                "SELECT COUNT(*) FROM extracted_rows "
                "WHERE statement_id = ? AND run_id = 'run-intake-fixture-2'",
                (statement_id,),
            ),
            0,
        )
        self.assertEqual(
            self._rows(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE statement_id = ? AND event_type = 'statement_reopened'",
                (statement_id,),
            ),
            1,
        )

    def test_rebuild_without_new_run_revives_rejected_extraction(self) -> None:
        self._classify_all()
        first = intake.build_statements(DOC_ID, "tester")
        statement_id = first["statements"][0]["statement_id"]
        intake.discard_pending_statement(
            statement_id, "tester", reason="discard before a same-run rebuild"
        )
        rebuilt = intake.build_statements(DOC_ID, "tester")
        self.assertEqual(rebuilt["statements_reopened"], 1)
        self.assertEqual(
            self._rows(
                "SELECT extraction_status FROM statement_extractions "
                "WHERE statement_id = ?",
                (statement_id,),
            ),
            "completed",
        )
        self.assertIn(
            statement_id, {item["statement_id"] for item in service.review_queue()}
        )

    def test_rebuild_after_moving_a_page_to_another_statement(self) -> None:
        self._classify_all()
        intake.build_statements(DOC_ID, "tester")
        intake.discard_pending_for_document(
            DOC_ID, "tester", reason="reclassify page 2"
        )
        self._save(
            2,
            page_role="financial_statement",
            decision="approve",
            entity_id=self.second["entity_id"],
            statement_date="2099-12-31",
        )
        rebuilt = intake.build_statements(DOC_ID, "tester")
        target = next(
            item for item in rebuilt["statements"]
            if item["entity_name"] == _entity_name(self.second)
        )
        self.assertEqual(target["pages"], [1, 2, 3])
        conn = sqlite3.connect(self.db)
        try:
            links = conn.execute(
                "SELECT page_order, is_primary FROM statement_pages "
                "WHERE statement_id = ? ORDER BY page_order",
                (target["statement_id"],),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in links], [1, 2, 3])
        self.assertEqual(sum(row[1] for row in links), 1)

    def test_rebuild_refuses_discarded_statement_with_review(self) -> None:
        self._classify_all()
        first = intake.build_statements(DOC_ID, "tester")
        statement_id = first["statements"][0]["statement_id"]
        intake.discard_pending_statement(
            statement_id, "tester", reason="discard before review arrives"
        )
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO statement_reviews(statement_id, review_status, "
                "reviewed_by, reviewed_at, updated_at) VALUES (?, 'reviewed', 'tester', ?, ?)",
                (statement_id, NOW, NOW),
            )
            conn.commit()
        finally:
            conn.close()
        _append_ocr_run(
            self.db, self.run_dir, "run-intake-fixture-3", "2026-11-22T00:00:00+00:00"
        )
        with self.assertRaises(intake.IntakeConflict):
            intake.build_statements(DOC_ID, "tester")

    def test_failed_reprocess_keeps_pending_statement(self) -> None:
        self._classify_all()
        first = intake.build_statements(DOC_ID, "tester")
        statement_id = first["statements"][0]["statement_id"]
        # The fixture has no completed import job, so reprocess must fail
        # before anything is discarded.
        with self.assertRaises(intake.IntakeError):
            intake.reprocess_ocr(
                DOC_ID, "tester", reason="re-run OCR", discard_pending=True
            )
        self.assertEqual(
            self._rows(
                "SELECT statement_status FROM statements WHERE statement_id = ?",
                (statement_id,),
            ),
            "proposed",
        )
        self.assertGreater(
            self._rows(
                "SELECT COUNT(*) FROM extracted_rows WHERE statement_id = ?",
                (statement_id,),
            ),
            0,
        )
        self.assertEqual(
            self._rows(
                "SELECT COUNT(*) FROM statement_extractions "
                "WHERE statement_id = ? AND extraction_status = 'rejected'",
                (statement_id,),
            ),
            0,
        )


class StatementRowsSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "v3.sqlite"
        from tools.build_v3_db import apply_schema

        conn = sqlite3.connect(self.db)
        try:
            apply_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _assert_child_fks_point_at_statement_rows(self, conn: sqlite3.Connection) -> None:
        for table in (
            "statement_row_mappings",
            "field_verifications",
            "statement_row_adjustments",
            "validation_findings",
            "review_tasks",
            "audit_events",
        ):
            targets = {
                row[2] for row in conn.execute(f"PRAGMA foreign_key_list({table})")
            }
            self.assertIn("statement_rows", targets, table)

    def test_migration_from_composite_fk_keeps_schema_references(self) -> None:
        _downgrade_statement_rows_to_composite_fk(self.db)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            intake._migrate_statement_rows_discard_fk(conn)
            conn.commit()
            dangling = conn.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE sql LIKE '%statement_rows__pre_discard_fk%'"
            ).fetchall()
            self.assertEqual(dangling, [])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self._assert_child_fks_point_at_statement_rows(conn)
            for (view,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall():
                conn.execute(f'SELECT COUNT(*) FROM "{view}"').fetchone()
            self.assertTrue(
                "FOREIGN KEY (extracted_row_id, statement_extraction_id)"
                not in conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='statement_rows'"
                ).fetchone()[0]
            )
        finally:
            conn.close()

    def test_repair_tool_restores_renamed_references(self) -> None:
        from tools.repair_statement_rows_refs import repair

        _downgrade_statement_rows_to_composite_fk(self.db)
        _simulate_old_discard_fk_migration(self.db)

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            with self.assertRaises(RuntimeError):
                intake._assert_no_dangling_statement_rows_refs(conn)
            self.assertEqual(repair(conn, apply=False)["status"], "dirty")
            summary = repair(conn, apply=True)
            self.assertTrue(summary["repaired"])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            dangling = conn.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE sql LIKE '%statement_rows__pre_discard_fk%'"
            ).fetchall()
            self.assertEqual(dangling, [])
            self._assert_child_fks_point_at_statement_rows(conn)
            for (view,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall():
                conn.execute(f'SELECT COUNT(*) FROM "{view}"').fetchone()
            intake._assert_no_dangling_statement_rows_refs(conn)
            self.assertEqual(repair(conn, apply=True)["status"], "clean")
        finally:
            conn.close()


class IntakeMatcherTests(unittest.TestCase):
    def test_exact_and_split_matches(self) -> None:
        index = {
            intake._norm_name("澳門國際銀行股份有限公司"): {"e1"},
            intake._norm_name("HANG SENG BANK LIMITED"): {"e2"},
        }
        entities = {"e1": {}, "e2": {}}
        self.assertEqual(
            intake._match_entity("澳門國際銀行股份有限公司", index, entities),
            ("e1", 1.0, "exact"),
        )
        self.assertEqual(
            intake._match_entity(
                "HANG SENG BANK LIMITED | SUCURSAL DE MACAU", index, entities
            ),
            ("e2", 1.0, "exact"),
        )

    def test_ambiguous_exact_is_not_guessed(self) -> None:
        key = intake._norm_name("同名銀行")
        self.assertIsNone(
            intake._match_entity("同名銀行", {key: {"e1", "e2"}}, {"e1": {}, "e2": {}})
        )

    def test_fuzzy_variant_matches_with_score(self) -> None:
        index = {intake._norm_name("國泰海通證劵(澳門)一人有限公司"): {"e1"}}
        match = intake._match_entity("國泰君安證劵(澳門)一人有限公司", index, {"e1": {}})
        self.assertIsNotNone(match)
        self.assertEqual(match[0], "e1")
        self.assertGreaterEqual(match[1], 0.85)


class IntakePermissionTests(unittest.TestCase):
    def test_classify_capability(self) -> None:
        for role in ("reviewer", "adopter", "admin"):
            with self.subTest(role=role):
                self.assertTrue(can_classify(role))
        for role in ("viewer", "publisher"):
            with self.subTest(role=role):
                self.assertFalse(can_classify(role))


class IntakeApiTests(_IntakeTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app)

    def test_viewer_cannot_classify(self) -> None:
        response = self.client.post(
            f"/api/intake/{DOC_ID}/pages/page-fixture-2",
            json={"page_role": "other", "decision": "approve"},
            headers={"X-Workbench-Role": "viewer"},
        )
        self.assertEqual(response.status_code, 403)
        page = self.client.get(f"/intake/{DOC_ID}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('class="page-select"', page.text)
        self.assertNotIn("Reject selected pages", page.text)

    def test_auto_classify_endpoint_and_build(self) -> None:
        response = self.client.post(
            f"/api/intake/{DOC_ID}/auto-classify",
            json={"classify_empty_pages": True},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["financial"], 2)
        self.assertEqual(result["detail"]["pending_pages"], 0)
        built = self.client.post(
            f"/api/intake/{DOC_ID}/build",
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(built.status_code, 200, built.text)
        self.assertEqual(built.json()["statements_created"], 2)

    def test_bulk_reject_endpoint(self) -> None:
        page_ids = [page["document_page_id"] for page in self.detail["pages"]]
        viewer = self.client.post(
            f"/api/intake/{DOC_ID}/reject-pages",
            json={"page_ids": page_ids, "note": "not a financial statement"},
            headers={"X-Workbench-Role": "viewer"},
        )
        self.assertEqual(viewer.status_code, 403)
        missing_note = self.client.post(
            f"/api/intake/{DOC_ID}/reject-pages",
            json={"page_ids": page_ids, "note": " "},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(missing_note.status_code, 400)
        response = self.client.post(
            f"/api/intake/{DOC_ID}/reject-pages",
            json={"page_ids": page_ids, "note": "cover / contents / audit"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["rejected_count"], 3)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["pending_pages"], 0)
        detail = self.client.get(f"/api/intake/{DOC_ID}").json()
        self.assertTrue(
            all(
                page["page_role"] == "other"
                and page["classification_status"] == "rejected"
                for page in detail["pages"]
            )
        )

    def test_pages_and_build_endpoint(self) -> None:
        for page in self.detail["pages"]:
            entity = self.first if page["pdf_page"] == 2 else self.second
            response = self.client.post(
                f"/api/intake/{DOC_ID}/pages/{page['document_page_id']}",
                json={
                    "page_role": "financial_statement",
                    "decision": "approve",
                    "entity_id": entity["entity_id"],
                    "statement_date": "2099-12-31",
                    "statement_type": "balance_sheet",
                },
                headers={"X-Workbench-Role": "reviewer"},
            )
            self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get(f"/api/intake/{DOC_ID}")
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.json()["can_build"])
        built = self.client.post(
            f"/api/intake/{DOC_ID}/build",
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(built.status_code, 200, built.text)
        self.assertEqual(built.json()["statements_created"], 2)
        page = self.client.get("/intake")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Import intake", page.text)
        self.client.get("/set-role/reviewer")
        batch_page = self.client.get(f"/intake/{DOC_ID}")
        self.assertEqual(batch_page.status_code, 200)
        self.assertIn("OCR quality preview", batch_page.text)
        self.assertIn("Pending statements", batch_page.text)
        self.assertIn("discard-statement", batch_page.text)
        self.assertIn("Reject selected pages", batch_page.text)
        self.assertIn("page-select", batch_page.text)
        self.assertIn("Select all proposed", batch_page.text)

    def test_discard_endpoint_and_reprocess_requires_reason(self) -> None:
        self.client.post(
            f"/api/intake/{DOC_ID}/auto-classify",
            json={"classify_empty_pages": True},
            headers={"X-Workbench-Role": "reviewer"},
        )
        built = self.client.post(
            f"/api/intake/{DOC_ID}/build",
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(built.status_code, 200, built.text)
        statement_id = built.json()["statements"][0]["statement_id"]

        refused = self.client.post(
            f"/api/review/{statement_id}/discard",
            json={"reason": ""},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(refused.status_code, 400)

        discarded = self.client.post(
            f"/api/review/{statement_id}/discard",
            json={"reason": "OCR misaligned columns"},
            headers={"X-Workbench-Role": "reviewer"},
        )
        self.assertEqual(discarded.status_code, 200, discarded.text)
        self.assertEqual(discarded.json()["statement_status"], "excluded")

        with mock.patch.object(
            intake, "_find_reprocess_job",
            return_value={
                "job_id": "job-fixture",
                "status": "completed",
                "source_kind": "batch",
                "source": "batch-fixture",
            },
        ), mock.patch("workbench.import_jobs.decision_job") as decide:
            decide.return_value = {"job_id": "job-new", "status": "queued"}
            response = self.client.post(
                f"/api/intake/{DOC_ID}/reprocess",
                json={"reason": "column layout fix", "discard_pending": True},
                headers={"X-Workbench-Role": "reviewer"},
            )
        self.assertEqual(response.status_code, 202, response.text)
        decide.assert_called_once()
        self.assertEqual(decide.call_args.kwargs["decision"], "reprocess")
        self.assertEqual(decide.call_args.kwargs["reason"], "column layout fix")


class OcrQualityHelperTests(unittest.TestCase):
    def test_summarize_run_dir_flags_amount_in_label(self) -> None:
        from workbench.ocr_quality import summarize_run_dir

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (root / "tb_rows.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["pdf_page", "label_raw", "debit_raw", "credit_raw",
                                "review_reason"],
                )
                writer.writeheader()
                writer.writerow({
                    "pdf_page": 1, "label_raw": "140,191,384.20",
                    "debit_raw": "", "credit_raw": "", "review_reason": "",
                })
                writer.writerow({
                    "pdf_page": 1, "label_raw": "",
                    "debit_raw": "1.00", "credit_raw": "",
                    "review_reason": "amount_without_account_label",
                })
            summary = summarize_run_dir(root)
            self.assertEqual(summary["rows"], 2)
            self.assertEqual(summary["amount_in_label"], 1)
            self.assertEqual(summary["empty_labels"], 1)
            self.assertTrue(summary["warning"])


if __name__ == "__main__":
    unittest.main()
