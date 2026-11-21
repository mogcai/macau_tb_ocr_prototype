"""Tests for the additive full-page-record OCR mode used by V3 ingestion."""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macau_tb_ocr as ocr  # noqa: E402


class FullPageRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_pdf(self, pages: int = 3, texts: list[str] | None = None) -> Path:
        path = self.root / f"fixture_{pages}.pdf"
        doc = fitz.open()
        for index in range(pages):
            page = doc.new_page(width=600, height=800)
            if texts and index < len(texts):
                page.insert_text((50, 50), texts[index])
        doc.save(path)
        doc.close()
        return path

    def _args(self, pdf: Path, outdir: Path, *extra: str) -> object:
        return ocr.build_parser().parse_args([
            str(pdf),
            "--outdir", str(outdir),
            "--engine", "vision",
            "--header-fallback", "off",
            "--amount-second-pass", "off",
            "--no-label-standardization",
            *extra,
        ])

    def _fake_image(self, doc, page, page_index: int, out_path: Path) -> ocr.TableImage:
        return ocr.TableImage(page_index, out_path, 1000, 1400, None, False)

    def _fake_vision(self, image_path, *args, **kwargs):
        if "page_0002" in str(image_path):
            raise RuntimeError("fixture vision failure")
        return [], {}

    def _read_docs(self, outdir: Path) -> list[dict[str, str]]:
        path = outdir / "documents.csv"
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def _read_report(self, outdir: Path) -> dict:
        import json
        return json.loads((outdir / "run_report.json").read_text(encoding="utf-8"))

    def test_all_non_tb_pages_are_explicitly_skipped(self) -> None:
        pdf = self._make_pdf(3)
        outdir = self.root / "non_tb"
        args = self._args(pdf, outdir, "--full-page-records")
        with mock.patch.object(ocr, "write_excel"):
            report = ocr.process_pdf(args)

        rows = self._read_docs(outdir)
        self.assertEqual([row["pdf_page"] for row in rows], ["1", "2", "3"])
        self.assertTrue(all(row["ocr_status"] == "skipped" for row in rows))
        self.assertTrue(all(row["skip_reason"] == "not_tb_page" for row in rows))
        self.assertTrue(all(row["is_tb_page"] == "False" for row in rows))
        self.assertEqual(report["pdf_pages"], 3)
        self.assertEqual(report["page_records"], 3)
        self.assertEqual(report["tb_pages_detected"], 0)
        self.assertEqual(report["ocr_skipped_pages"], 3)
        self.assertEqual(report["ocr_failed_pages"], 0)

    def test_first_middle_last_vision_failure(self) -> None:
        for fail_page in (1, 2, 3):
            with self.subTest(fail_page=fail_page):
                pdf = self._make_pdf(3)
                outdir = self.root / f"fail_{fail_page}"
                args = self._args(pdf, outdir, "--full-page-records")
                vision = self._fake_vision
                if fail_page != 2:
                    def vision(image_path, *a, **kw):
                        if f"page_{fail_page:04d}" in str(image_path):
                            raise RuntimeError("fixture vision failure")
                        return [], {}
                with mock.patch.object(ocr, "is_tb_page", return_value=True), \
                        mock.patch.object(ocr, "extract_best_embedded_image", side_effect=self._fake_image), \
                        mock.patch.object(ocr, "detect_column_layout", return_value=(0.5, 0.8, 0.0, "test")), \
                        mock.patch.object(ocr, "run_vision_ocr", side_effect=vision), \
                        mock.patch.object(ocr, "write_excel"):
                    report = ocr.process_pdf(args)

                rows = self._read_docs(outdir)
                self.assertEqual(len(rows), 3)
                by_page = {int(row["pdf_page"]): row for row in rows}
                for page in range(1, 4):
                    if page < fail_page:
                        self.assertEqual(by_page[page]["ocr_status"], "completed")
                    elif page == fail_page:
                        self.assertEqual(by_page[page]["ocr_status"], "failed")
                        self.assertIn("fixture vision failure", by_page[page]["error_message"])
                    else:
                        self.assertEqual(by_page[page]["ocr_status"], "skipped")
                        self.assertEqual(
                            by_page[page]["skip_reason"],
                            "not_processed_after_failure",
                        )
                self.assertEqual(report["ocr_completed_pages"], fail_page - 1)
                self.assertEqual(report["ocr_failed_pages"], 1)
                self.assertEqual(report["ocr_skipped_pages"], 3 - fail_page)

    def test_failed_page_partial_evidence_is_discarded(self) -> None:
        pdf = self._make_pdf(3)
        outdir = self.root / "partial_evidence"
        args = self._args(pdf, outdir, "--full-page-records")
        validation_calls = {"count": 0}

        def vision(image_path, *a, **kw):
            return [
                ocr.OCRBox(
                    "1,234.50", 0.9, 10.0, 10.0, 80.0, 24.0, "en", "table"
                )
            ], {}

        def validate(rows, *a, **kw):
            validation_calls["count"] += 1
            if validation_calls["count"] == 2:
                raise RuntimeError("page 2 late failure")
            return []

        with mock.patch.object(ocr, "is_tb_page", return_value=True), \
                mock.patch.object(ocr, "extract_best_embedded_image", side_effect=self._fake_image), \
                mock.patch.object(ocr, "detect_column_layout", return_value=(0.5, 0.8, 0.0, "test")), \
                mock.patch.object(ocr, "run_vision_ocr", side_effect=vision), \
                mock.patch.object(ocr, "validate_rows", side_effect=validate), \
                mock.patch.object(ocr, "write_excel"):
            report = ocr.process_pdf(args)

        docs = self._read_docs(outdir)
        by_page = {int(row["pdf_page"]): row for row in docs}
        self.assertEqual(by_page[1]["ocr_status"], "completed")
        self.assertEqual(by_page[2]["ocr_status"], "failed")
        self.assertIn("page 2 late failure", by_page[2]["error_message"])
        self.assertEqual(by_page[3]["ocr_status"], "skipped")
        self.assertEqual(
            by_page[3]["skip_reason"], "not_processed_after_failure"
        )

        with (outdir / "ocr_raw.csv").open(newline="", encoding="utf-8-sig") as handle:
            raw = [dict(row) for row in csv.DictReader(handle)]
        raw_pages = [int(row["pdf_page"]) for row in raw]
        self.assertIn(1, raw_pages)
        self.assertNotIn(2, raw_pages)

        with (outdir / "tb_rows.csv").open(newline="", encoding="utf-8-sig") as handle:
            tb = [dict(row) for row in csv.DictReader(handle)]
        self.assertNotIn(2, [int(row["pdf_page"]) for row in tb])
        self.assertEqual(report["ocr_completed_pages"], 1)
        self.assertEqual(report["ocr_failed_pages"], 1)
        self.assertEqual(report["ocr_skipped_pages"], 1)

    def test_v5_default_mode_skips_non_tb_pages(self) -> None:
        pdf = self._make_pdf(3, ["TB PAGE 1", "PLAIN PAGE 2", "TB PAGE 3"])
        outdir = self.root / "v5_non_tb"

        def is_tb(text, has_large_image):
            return "TB PAGE" in text and "PLAIN" not in text

        args = self._args(pdf, outdir)
        with mock.patch.object(ocr, "is_tb_page", side_effect=is_tb), \
                mock.patch.object(ocr, "extract_best_embedded_image", side_effect=self._fake_image), \
                mock.patch.object(ocr, "detect_column_layout", return_value=(0.5, 0.8, 0.0, "test")), \
                mock.patch.object(ocr, "run_vision_ocr", return_value=([], {})), \
                mock.patch.object(ocr, "write_excel"):
            report = ocr.process_pdf(args)

        docs = self._read_docs(outdir)
        self.assertEqual([int(row["pdf_page"]) for row in docs], [1, 3])
        self.assertTrue(all(row["ocr_status"] == "completed" for row in docs))
        for forbidden in ("skip_reason", "error_message", "is_tb_page"):
            self.assertNotIn(forbidden, docs[0])
        for forbidden in (
            "full_page_records", "page_records", "ocr_page_coverage",
            "ocr_completed_pages", "ocr_skipped_pages", "ocr_failed_pages",
        ):
            self.assertNotIn(forbidden, report)
        self.assertEqual(report["tb_pages_detected"], 2)

    def test_completed_skipped_and_failed_fixture(self) -> None:
        pdf = self._make_pdf(
            3, ["TB PAGE 1", "PLAIN PAGE 2", "TB PAGE 3"]
        )
        outdir = self.root / "mixed"
        args = self._args(pdf, outdir, "--full-page-records")

        def is_tb(text, has_large_image):
            return "TB PAGE" in text and "PLAIN" not in text

        def vision(image_path, *a, **kw):
            if "page_0003" in str(image_path):
                raise RuntimeError("fixture vision failure")
            return [], {}

        with mock.patch.object(ocr, "is_tb_page", side_effect=is_tb), \
                mock.patch.object(ocr, "extract_best_embedded_image", side_effect=self._fake_image), \
                mock.patch.object(ocr, "detect_column_layout", return_value=(0.5, 0.8, 0.0, "test")), \
                mock.patch.object(ocr, "run_vision_ocr", side_effect=vision), \
                mock.patch.object(ocr, "write_excel"):
            report = ocr.process_pdf(args)

        rows = self._read_docs(outdir)
        self.assertEqual([row["pdf_page"] for row in rows], ["1", "2", "3"])
        self.assertEqual(rows[0]["ocr_status"], "completed")
        self.assertEqual(rows[1]["ocr_status"], "skipped")
        self.assertEqual(rows[1]["skip_reason"], "not_tb_page")
        self.assertEqual(rows[2]["ocr_status"], "failed")
        self.assertIn("fixture vision failure", rows[2]["error_message"])
        self.assertEqual(report["ocr_completed_pages"], 1)
        self.assertEqual(report["ocr_skipped_pages"], 1)
        self.assertEqual(report["ocr_failed_pages"], 1)
        self.assertEqual(report["tb_pages_detected"], 2)

    def test_v5_default_documents_columns_are_unchanged(self) -> None:
        pdf = self._make_pdf(1)
        outdir = self.root / "v5_default"
        args = self._args(pdf, outdir)
        with mock.patch.object(ocr, "is_tb_page", return_value=True), \
                mock.patch.object(ocr, "extract_best_embedded_image", side_effect=self._fake_image), \
                mock.patch.object(ocr, "detect_column_layout", return_value=(0.5, 0.8, 0.0, "test")), \
                mock.patch.object(ocr, "run_vision_ocr", return_value=([], {})), \
                mock.patch.object(ocr, "write_excel"):
            report = ocr.process_pdf(args)

        rows = self._read_docs(outdir)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ocr_status"], "completed")
        for forbidden in ("skip_reason", "error_message", "is_tb_page"):
            self.assertNotIn(forbidden, rows[0])
        for forbidden in ("full_page_records", "page_records", "ocr_page_coverage"):
            self.assertNotIn(forbidden, report)

    def test_full_page_mode_requires_pages_all(self) -> None:
        pdf = self._make_pdf(3)
        args = self._args(
            pdf, self.root / "bad_range",
            "--full-page-records", "--pages", "1-2",
        )
        with self.assertRaises(ValueError):
            ocr.process_pdf(args)

    def test_full_page_mode_rejects_inspect_only(self) -> None:
        pdf = self._make_pdf(1)
        args = self._args(
            pdf, self.root / "inspect",
            "--full-page-records", "--inspect-only",
        )
        with self.assertRaises(ValueError):
            ocr.process_pdf(args)

    def test_full_page_mode_rejects_empty_pdf(self) -> None:
        pdf = self._make_pdf(1)
        outdir = self.root / "empty_pdf"

        class FakeDoc:
            def __len__(self):
                return 0

            def close(self):
                pass

        args = self._args(pdf, outdir, "--full-page-records")
        with mock.patch.object(ocr.fitz, "open", return_value=FakeDoc()):
            with self.assertRaises(ValueError):
                ocr.process_pdf(args)

    def test_unreadable_pdf_is_rejected(self) -> None:
        pdf = self.root / "not_really.pdf"
        pdf.write_text("not a pdf", encoding="utf-8")
        args = self._args(pdf, self.root / "unreadable", "--full-page-records")
        with self.assertRaises(Exception):
            ocr.process_pdf(args)

    def test_page_record_validation_rejects_duplicates_and_gaps(self) -> None:
        with self.assertRaises(ocr.PageCoverageError):
            ocr.validate_page_records(
                [{"pdf_page": "1"}, {"pdf_page": "1"}], 2
            )
        with self.assertRaises(ocr.PageCoverageError):
            ocr.validate_page_records([{"pdf_page": "1"}], 2)
        with self.assertRaises(ocr.PageCoverageError):
            ocr.validate_page_records(
                [{"pdf_page": "1"}, {"pdf_page": "3"}], 2
            )
        ocr.validate_page_records(
            [{"pdf_page": "1"}, {"pdf_page": "2"}], 2
        )

    def test_full_page_flag_is_parsed(self) -> None:
        pdf = self._make_pdf(1)
        args = ocr.build_parser().parse_args([
            str(pdf), "--outdir", str(self.root / "flag"),
            "--full-page-records",
        ])
        self.assertTrue(args.full_page_records)


if __name__ == "__main__":
    unittest.main()
