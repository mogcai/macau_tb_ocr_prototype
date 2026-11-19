"""DDL and integrity tests for the v3 database foundation."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.build_v3_db import apply_schema  # noqa: E402


NOW = "2026-11-19T00:00:00Z"
SHA = "a" * 64
FINGERPRINT = "b" * 64


class V3ConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "v3.sqlite"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        apply_schema(self.conn)
        self._seed_base()

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    def _seed_base(self) -> None:
        c = self.conn
        c.execute(
            "INSERT INTO gazette_issues(gazette_issue_id, issue_key, publication_date, discovered_at) "
            "VALUES(?,?,?,?)",
            ("G1", "2026-46", "2026-11-18", NOW),
        )
        c.execute(
            "INSERT INTO source_documents("
            "source_document_id, gazette_issue_id, document_key, storage_uri, sha256, page_count, acquired_at"
            ") VALUES(?,?,?,?,?,?,?)",
            ("D1", "G1", "doc:2026-46", "/tmp/issue46.pdf", SHA, 18, NOW),
        )
        c.execute(
            "INSERT INTO document_pages("
            "document_page_id, source_document_id, pdf_page, printed_page, page_role, "
            "classification_status, classification_source"
            ") VALUES(?,?,?,?,?,?,?)",
            ("P1", "D1", 1, "9", "financial_statement", "approved", "human"),
        )
        c.execute(
            "INSERT INTO reporting_entities("
            "entity_id, entity_key, entity_type, canonical_zh, canonical_pt, registry_status, created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            ("E1", "bank:test", "bank", "測試銀行", "BANCO TESTE", "approved", NOW),
        )
        c.execute(
            "INSERT INTO reporting_entity_aliases("
            "entity_alias_id, entity_id, raw_name, normalized_name, language, alias_type, status"
            ") VALUES(?,?,?,?,?,?,?)",
            ("EA1", "E1", "測試銀行", "測試銀行", "zh-Hant", "official", "approved"),
        )
        for statement_id, identity in (("S1", "identity:1"), ("S2", "identity:2")):
            c.execute(
                "INSERT INTO statements("
                "statement_id, source_document_id, reporting_entity_id, statement_type, "
                "reporting_scope, identity_key, identity_status, created_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (statement_id, "D1", "E1", "balance_sheet", "bank", identity, "proposed", NOW),
            )
        c.execute(
            "INSERT INTO extraction_runs(run_id, source_document_id, run_status) VALUES(?,?,?)",
            ("R1", "D1", "completed"),
        )
        c.execute(
            "INSERT INTO statement_extractions("
            "statement_extraction_id, statement_id, run_id, source_document_id, extraction_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("SE1", "S1", "R1", "D1", "completed", NOW),
        )
        c.execute(
            "INSERT INTO statement_extractions("
            "statement_extraction_id, statement_id, run_id, source_document_id, extraction_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("SE2", "S2", "R1", "D1", "completed", NOW),
        )
        c.execute(
            "INSERT INTO statement_pages("
            "statement_id, source_document_id, document_page_id, page_order, is_primary, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("S1", "D1", "P1", 1, 1, NOW),
        )
        c.execute(
            "INSERT INTO page_extractions("
            "page_extraction_id, run_id, document_page_id, source_document_id, ocr_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("PE1", "R1", "P1", "D1", "completed", NOW),
        )
        c.execute(
            "INSERT INTO extracted_rows("
            "extracted_row_id, page_extraction_id, document_page_id, run_id, row_disposition, "
            "statement_id, statement_extraction_id, page_row_order, label_raw, label_language, "
            "debit_raw, debit_parsed_minor, debit_value_state, credit_raw, credit_value_state, "
            "row_type_proposed, parent_context_status, row_confidence, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ER1", "PE1", "P1", "R1", "included", "S1", "SE1", 1,
                "現金", "zh-Hant", "1,234.50", 123450, "amount",
                None, "blank", "item", "root", 0.9, NOW,
            ),
        )
        c.execute(
            "INSERT INTO statement_rows("
            "statement_row_id, statement_id, statement_extraction_id, extracted_row_id, "
            "row_type, parent_context_status, label_normalized, label_language, "
            "debit_minor, debit_value_state, credit_value_state, "
            "row_fingerprint, fingerprint_version, carry_forward_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "SR1", "S1", "SE1", "ER1", "item", "root", "現金", "zh-Hant",
                123450, "amount", "blank", FINGERPRINT, 1, "none", NOW,
            ),
        )
        c.execute(
            "INSERT INTO standard_items("
            "standard_item_id, item_key, canonical_zh, canonical_zh_status, "
            "canonical_pt, canonical_pt_status, item_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            ("SI1", "CASH", "現金", "approved", "CAIXA", "approved", "approved", NOW),
        )
        c.execute(
            "INSERT INTO statement_row_mappings("
            "mapping_id, statement_row_id, standard_item_id, mapping_status, created_at"
            ") VALUES(?,?,?,?,?)",
            ("M1", "SR1", "SI1", "proposed", NOW),
        )
        c.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def assert_sqlite_error(self, sql: str, params: tuple = ()) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(sql, params)

    def adopt(self, statement_id: str = "S1", extraction_id: str = "SE1") -> None:
        self.conn.execute(
            "UPDATE statements SET adopted_statement_extraction_id=? WHERE statement_id=?",
            (extraction_id, statement_id),
        )
        self.conn.commit()

    def publish(self, publication_id: str = "PUB1", statement_id: str = "S1",
                extraction_id: str = "SE1", status: str = "published") -> None:
        self.conn.execute(
            "INSERT INTO publication_records("
            "publication_id, statement_id, statement_extraction_id, quality_level, "
            "publication_status, published_at"
            ") VALUES(?,?,?,?,?,?)",
            (publication_id, statement_id, extraction_id, "quality_checked", status, NOW),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Schema / metadata
    # ------------------------------------------------------------------

    def test_schema_objects_and_metadata(self) -> None:
        tables = self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        views = self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='view'"
        ).fetchone()[0]
        self.assertEqual(tables, 21)
        self.assertEqual(views, 7)
        self.assertEqual(
            self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
        )
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(
            self.conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0],
            "3.0",
        )

    # ------------------------------------------------------------------
    # Adoption / publication
    # ------------------------------------------------------------------

    def test_cross_statement_adoption_rejected(self) -> None:
        self.assert_sqlite_error(
            "UPDATE statements SET adopted_statement_extraction_id='SE2' WHERE statement_id='S1'"
        )

    def test_non_completed_adoption_rejected(self) -> None:
        self.conn.execute(
            "INSERT INTO extraction_runs(run_id, source_document_id, run_status) VALUES(?,?,?)",
            ("R2", "D1", "completed"),
        )
        self.conn.execute(
            "INSERT INTO statement_extractions("
            "statement_extraction_id, statement_id, run_id, source_document_id, extraction_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("SE3", "S2", "R2", "D1", "proposed", NOW),
        )
        self.conn.commit()
        self.assert_sqlite_error(
            "UPDATE statements SET adopted_statement_extraction_id='SE3' WHERE statement_id='S2'"
        )

    def test_new_statement_must_start_without_adoption(self) -> None:
        self.assert_sqlite_error(
            "INSERT INTO statements("
            "statement_id, source_document_id, reporting_entity_id, statement_type, "
            "reporting_scope, identity_key, identity_status, created_at, adopted_statement_extraction_id"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            ("S3", "D1", "E1", "balance_sheet", "bank", "identity:3", "proposed", NOW, "SE1"),
        )

    def test_adoption_blocked_by_current_publication(self) -> None:
        self.adopt()
        self.publish()
        self.conn.execute(
            "INSERT INTO extraction_runs(run_id, source_document_id, run_status) VALUES(?,?,?)",
            ("R2", "D1", "completed"),
        )
        self.conn.execute(
            "INSERT INTO statement_extractions("
            "statement_extraction_id, statement_id, run_id, source_document_id, extraction_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("SE4", "S1", "R2", "D1", "completed", NOW),
        )
        self.conn.commit()
        self.assert_sqlite_error(
            "UPDATE statements SET adopted_statement_extraction_id='SE4' WHERE statement_id='S1'"
        )

    def test_publication_requires_adopted_extraction(self) -> None:
        self.adopt()
        self.assert_sqlite_error(
            "INSERT INTO publication_records("
            "publication_id, statement_id, statement_extraction_id, quality_level, publication_status"
            ") VALUES(?,?,?,?,?)",
            ("BADPUB", "S1", "SE2", "quality_checked", "published"),
        )

    def test_only_one_current_publication_per_statement(self) -> None:
        self.adopt()
        self.publish("PUB1")
        self.conn.execute(
            "UPDATE publication_records SET publication_status='revoked' WHERE publication_id='PUB1'"
        )
        self.conn.commit()
        self.publish("PUB2")
        self.assert_sqlite_error(
            "INSERT INTO publication_records("
            "publication_id, statement_id, statement_extraction_id, quality_level, publication_status"
            ") VALUES(?,?,?,?,?)",
            ("PUB3", "S1", "SE1", "quality_checked", "published"),
        )

    def test_supersedes_publication_must_belong_to_same_statement(self) -> None:
        self.adopt("S1", "SE1")
        self.publish("PUB1", "S1", "SE1")
        self.adopt("S2", "SE2")
        self.assert_sqlite_error(
            "INSERT INTO publication_records("
            "publication_id, statement_id, statement_extraction_id, supersedes_publication_id, "
            "quality_level, publication_status"
            ") VALUES(?,?,?,?,?,?)",
            ("PUBX", "S2", "SE2", "PUB1", "quality_checked", "published"),
        )

    # ------------------------------------------------------------------
    # Rows / mappings
    # ------------------------------------------------------------------

    def test_duplicate_approved_mapping_rejected(self) -> None:
        self.conn.execute(
            "UPDATE statement_row_mappings SET mapping_status='approved', "
            "reviewed_by='reviewer', reviewed_at=? WHERE mapping_id='M1'",
            (NOW,),
        )
        self.conn.commit()
        self.assert_sqlite_error(
            "INSERT INTO statement_row_mappings("
            "mapping_id, statement_row_id, standard_item_id, mapping_status, "
            "reviewed_by, reviewed_at, created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            ("M2", "SR1", "SI1", "approved", "reviewer", NOW, NOW),
        )

    def test_append_only_rows_reject_update_and_delete(self) -> None:
        self.conn.execute(
            "INSERT INTO audit_events(audit_event_id, event_type, statement_id, created_at) "
            "VALUES(?,?,?,?)",
            ("A1", "test", "S1", NOW),
        )
        self.conn.commit()
        for table, key, new_value in (
            ("extracted_rows", "ER1", "x"),
            ("statement_rows", "SR1", "x"),
            ("audit_events", "A1", "changed"),
        ):
            with self.subTest(table=table, action="update"):
                column = {
                    "extracted_rows": "label_raw",
                    "statement_rows": "label_normalized",
                    "audit_events": "event_type",
                }[table]
                key_col = {
                    "extracted_rows": "extracted_row_id",
                    "statement_rows": "statement_row_id",
                    "audit_events": "audit_event_id",
                }[table]
                self.assert_sqlite_error(
                    f"UPDATE {table} SET {column}=? WHERE {key_col}=?", (new_value, key)
                )
            with self.subTest(table=table, action="delete"):
                key_col = {
                    "extracted_rows": "extracted_row_id",
                    "statement_rows": "statement_row_id",
                    "audit_events": "audit_event_id",
                }[table]
                self.assert_sqlite_error(f"DELETE FROM {table} WHERE {key_col}=?", (key,))

    def test_excluded_page_row_can_have_no_statement(self) -> None:
        self.conn.execute(
            "INSERT INTO extracted_rows("
            "extracted_row_id, page_extraction_id, document_page_id, run_id, row_disposition, "
            "page_row_order, label_raw, label_language, debit_value_state, credit_value_state, "
            "row_type_proposed, parent_context_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ER2", "PE1", "P1", "R1", "excluded_page_role", 2,
                "核數師報告", "zh-Hant", "blank", "blank", "note", "not_applicable", NOW,
            ),
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM extracted_rows WHERE row_disposition='excluded_page_role'"
            ).fetchone()[0],
            1,
        )

    def test_amount_state_requires_matching_minor(self) -> None:
        self.assert_sqlite_error(
            "INSERT INTO extracted_rows("
            "extracted_row_id, page_extraction_id, document_page_id, run_id, row_disposition, "
            "statement_id, statement_extraction_id, page_row_order, label_language, "
            "debit_value_state, credit_value_state, row_type_proposed, parent_context_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ERBAD", "PE1", "P1", "R1", "included", "S1", "SE1", 3,
                "zh-Hant", "amount", "blank", "item", "root", NOW,
            ),
        )

    def test_statement_page_source_mismatch_rejected(self) -> None:
        self.conn.execute(
            "INSERT INTO source_documents("
            "source_document_id, document_key, storage_uri, sha256, page_count, acquired_at"
            ") VALUES(?,?,?,?,?,?)",
            ("D2", "doc:other", "/tmp/other.pdf", "c" * 64, 1, NOW),
        )
        self.conn.execute(
            "INSERT INTO document_pages("
            "document_page_id, source_document_id, pdf_page, page_role, "
            "classification_status, classification_source"
            ") VALUES(?,?,?,?,?,?)",
            ("P2", "D2", 1, "financial_statement", "approved", "human"),
        )
        self.conn.commit()
        self.assert_sqlite_error(
            "INSERT INTO statement_pages("
            "statement_id, source_document_id, document_page_id, page_order, is_primary, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("S1", "D2", "P2", 2, 0, NOW),
        )

    # ------------------------------------------------------------------
    # Alias / review / findings
    # ------------------------------------------------------------------

    def test_alias_language_is_not_null_and_unique(self) -> None:
        self.assert_sqlite_error(
            "INSERT INTO standard_item_aliases("
            "standard_item_alias_id, standard_item_id, raw_name, normalized_name, "
            "language, alias_type, alias_status"
            ") VALUES(?,?,?,?,?,?,?)",
            ("A1", "SI1", "現金", "現金", None, "translation", "proposed"),
        )
        self.conn.execute(
            "INSERT INTO standard_item_aliases("
            "standard_item_alias_id, standard_item_id, raw_name, normalized_name, "
            "language, alias_type, alias_status"
            ") VALUES(?,?,?,?,?,?,?)",
            ("A1", "SI1", "現金", "現金", "zh-Hant", "translation", "proposed"),
        )
        self.conn.commit()
        self.assert_sqlite_error(
            "INSERT INTO standard_item_aliases("
            "standard_item_alias_id, standard_item_id, raw_name, normalized_name, "
            "language, alias_type, alias_status"
            ") VALUES(?,?,?,?,?,?,?)",
            ("A2", "SI1", "現金", "現金", "zh-Hant", "translation", "proposed"),
        )

    def test_validation_finding_requires_exactly_one_target(self) -> None:
        self.assert_sqlite_error(
            "INSERT INTO validation_findings("
            "finding_id, statement_id, document_page_id, rule_key, severity, finding_status, created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            ("F1", "S1", "P1", "test", "warning", "open", NOW),
        )
        self.assert_sqlite_error(
            "INSERT INTO validation_findings("
            "finding_id, rule_key, severity, finding_status, created_at"
            ") VALUES(?,?,?,?,?)",
            ("F2", "test", "warning", "open", NOW),
        )
        self.conn.execute(
            "INSERT INTO validation_findings("
            "finding_id, document_page_id, rule_key, severity, finding_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("F3", "P1", "test", "warning", "open", NOW),
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM validation_findings").fetchone()[0], 1
        )

    def test_review_task_requires_exactly_one_target(self) -> None:
        self.assert_sqlite_error(
            "INSERT INTO review_tasks("
            "review_task_id, target_statement_id, document_page_id, task_type, priority, task_status, created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            ("T1", "S1", "P1", "page_classification", "normal", "open", NOW),
        )
        self.assert_sqlite_error(
            "INSERT INTO review_tasks("
            "review_task_id, task_type, priority, task_status, created_at"
            ") VALUES(?,?,?,?,?)",
            ("T2", "page_classification", "normal", "open", NOW),
        )
        self.conn.execute(
            "INSERT INTO review_tasks("
            "review_task_id, document_page_id, task_type, priority, task_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            ("T3", "P1", "page_classification", "normal", "open", NOW),
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM review_tasks").fetchone()[0], 1
        )


if __name__ == "__main__":
    unittest.main()
