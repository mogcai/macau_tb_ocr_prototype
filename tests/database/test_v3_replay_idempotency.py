"""Idempotency tests for the raw run-output replay stage."""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.build_v3_db import apply_schema  # noqa: E402
from tools.replay_runs_to_v3 import replay_run  # noqa: E402


class V3ReplayIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "v3.sqlite"
        self.run_dir = self.root / "issue99_2026"
        self.run_dir.mkdir()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        apply_schema(self.conn)
        self._write_run_fixture()

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def _write_run_fixture(self) -> None:
        report = {
            "app_version": "0.7.0",
            "source_pdf": "/tmp/issue99.pdf",
            "source_pdf_sha256": "d" * 64,
            "pdf_pages": 1,
            "ocr_engine": "vision",
            "ocr_version": "test",
        }
        (self.run_dir / "run_report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        with (self.run_dir / "documents.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "pdf_page", "printed_page", "ocr_status", "image_path",
                    "image_width", "image_height", "number_format_detected",
                    "number_format_confidence", "ocr_languages",
                ],
            )
            writer.writeheader()
            writer.writerow({
                "pdf_page": "1",
                "printed_page": "10",
                "ocr_status": "completed",
                "image_path": "/tmp/page1.png",
                "image_width": "1000",
                "image_height": "1400",
                "number_format_detected": "comma_dot",
                "number_format_confidence": "1.0",
                "ocr_languages": "zh-Hant|pt",
            })
        with (self.run_dir / "tb_rows.csv").open("w", newline="", encoding="utf-8") as handle:
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
            writer.writerow({
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
            })

    def _replay(self) -> dict:
        result = replay_run(self.conn, self.run_dir, legacy_runs={})
        self.conn.commit()
        return result

    def test_replay_is_idempotent(self) -> None:
        first = self._replay()
        self.assertEqual(first["rows_inserted"], 1)
        self.assertEqual(first["rows_skipped"], 0)
        second = self._replay()
        self.assertEqual(second["rows_inserted"], 0)
        self.assertEqual(second["rows_skipped"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM extracted_rows").fetchone()[0], 1
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM document_pages").fetchone()[0], 1
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0], 1
        )

    def test_replay_preserves_unassigned_raw_evidence(self) -> None:
        self._replay()
        row = self.conn.execute(
            "SELECT * FROM extracted_rows"
        ).fetchone()
        self.assertEqual(row["row_disposition"], "unassigned")
        self.assertIsNone(row["statement_id"])
        self.assertIsNone(row["statement_extraction_id"])
        self.assertEqual(row["debit_parsed_minor"], 123450)
        self.assertEqual(row["debit_value_state"], "amount")
        self.assertEqual(row["credit_value_state"], "blank")
        self.assertEqual(row["label_language"], "zh-Hant")
        self.assertEqual(row["page_row_order"], 1)

    def test_changed_row_set_fails_instead_of_overwriting(self) -> None:
        self._replay()
        with (self.run_dir / "tb_rows.csv").open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "1", "外幣", "外幣", "child", "現金", "", "1.00",
                "", "1.00", "blank", "parsed", "0.8",
            ])
        with self.assertRaises(ValueError):
            self._replay()

    def test_second_pass_amount_without_raw_text_is_preserved(self) -> None:
        with (self.run_dir / "tb_rows.csv").open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "1", "第二項", "第二項", "item", "", "", "",
                "1.00", "", "recovered_second_pass", "blank", "0.8",
            ])
        self._replay()
        row = self.conn.execute(
            "SELECT debit_raw, debit_parsed_minor, debit_value_state, row_payload_json "
            "FROM extracted_rows WHERE page_row_order = 2"
        ).fetchone()
        self.assertEqual(row["debit_value_state"], "amount")
        self.assertEqual(row["debit_parsed_minor"], 100)
        self.assertIn('"debit_amount": "1.00"', row["row_payload_json"])


if __name__ == "__main__":
    unittest.main()
