"""Tests for the full-page header fallback used when the table image has no bank name."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macau_tb_ocr as m  # noqa: E402


PAGE_17_LINES = [
    "N.º 45 — 11-11-2026 BOLETIM OFICIAL DA REGIÃO ADMINISTRATIVA "
    "ESPECIAL DE MACAU — II SÉRIE 127",
    "郵政儲金局",
    "CAIXA ECONÓMICA POSTAL",
    "澳門郵電 CTT",
    "試算表於二零二六年九月三十日",
    "Balancete do razão em 30 de Setembro de 2026",
]


class HeaderLineExtractionTests(unittest.TestCase):
    def test_recovers_bank_name_before_balancete_title(self):
        name, used = m.extract_bank_name_from_header_lines(PAGE_17_LINES)
        self.assertEqual(name, "郵政儲金局 | CAIXA ECONÓMICA POSTAL")
        self.assertEqual(used, ["郵政儲金局", "CAIXA ECONÓMICA POSTAL"])

    def test_ignores_gazette_running_header_and_page_number(self):
        lines = [
            "BOLETIM OFICIAL DA REGIÃO ADMINISTRATIVA ESPECIAL DE MACAU — II SÉRIE",
            "N.º 45 — 11-11-2026",
            "127",
            "BANCO TESTE, S.A.",
            "試算表於二零二六年九月三十日",
        ]
        name, used = m.extract_bank_name_from_header_lines(lines)
        self.assertEqual(name, "BANCO TESTE, S.A.")
        self.assertEqual(used, ["BANCO TESTE, S.A."])

    def test_returns_empty_when_only_noise_is_present(self):
        lines = [
            "N.º 45 — 11-11-2026",
            "BOLETIM OFICIAL DA REGIÃO ADMINISTRATIVA ESPECIAL DE MACAU — II SÉRIE",
            "127",
            "試算表於二零二六年九月三十日",
        ]
        name, used = m.extract_bank_name_from_header_lines(lines)
        self.assertEqual(name, "")
        self.assertEqual(used, [])

    def test_without_title_uses_first_candidate_lines(self):
        name, used = m.extract_bank_name_from_header_lines([
            "BANCO A, S.A.",
            "CAIXA ECONÓMICA",
            "extra line",
        ])
        self.assertEqual(name, "BANCO A, S.A. | CAIXA ECONÓMICA")
        self.assertEqual(len(used), 2)


class HeaderRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=595, height=842)
        self.page.insert_text((60, 80), "TEST BANK, S.A.", fontsize=18)
        self.page.insert_text((60, 130), "TRIAL BALANCE", fontsize=12)

    def tearDown(self) -> None:
        self.doc.close()
        self.tmp.cleanup()

    def test_render_header_crop_keeps_audit_image(self):
        out = self.root / "header.png"
        width, height = m.render_page_header_image(self.page, out, zoom=2.0)
        self.assertTrue(out.exists())
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertLess(height, 842 * 2)

    def test_recover_header_metadata_uses_full_page_header(self):
        boxes = [
            m.OCRBox("郵政儲金局", 0.99, 200, 100, 600, 140, "zh-Hant", "header"),
            m.OCRBox("CAIXA ECONÓMICA POSTAL", 0.98, 200, 160, 900, 200,
                     "pt-BR", "header"),
            m.OCRBox("試算表於二零二六年九月三十日", 0.98, 200, 300, 900, 340,
                     "zh-Hant", "header"),
        ]
        with mock.patch.object(
            m, "run_vision_header_ocr", return_value=(boxes, {
                "vision_elapsed_seconds": 0.25,
                "vision_observation_count": 3,
            })
        ):
            diagnostics, returned = m.recover_header_metadata(
                self.page, self.root, 17, ["chinese_cht", "pt"],
                engine="vision", level="accurate",
                minimum_text_height=0.004, preprocess=False,
                language_correction="labels", engines={},
                device="cpu", ocr_version="PP-OCRv5",
            )
        self.assertEqual(diagnostics["header_fallback_status"], "recovered")
        self.assertEqual(
            diagnostics["header_fallback_bank_name"],
            "郵政儲金局 | CAIXA ECONÓMICA POSTAL",
        )
        self.assertEqual(
            diagnostics["header_fallback_statement_date"], "2026-09-30"
        )
        self.assertEqual(len(returned), 3)
        self.assertTrue(Path(diagnostics["header_fallback_image"]).exists())

    def test_cross_language_duplicate_header_line_is_deduped(self):
        boxes = [
            m.OCRBox("郵政儲金局", 0.9, 200, 50, 500, 90, "zh-Hant", "header"),
            m.OCRBox("CAIXA ECONOMICA POSTAL", 0.5, 200, 100, 900, 140,
                     "zh-Hant", "header"),
            m.OCRBox("CAIXA ECONÓMICA POSTAL", 1.0, 200, 100, 900, 140,
                     "pt-BR", "header"),
            m.OCRBox("試算表於二零二六年九月三十日", 0.9, 200, 300, 900, 340,
                     "zh-Hant", "header"),
        ]
        with mock.patch.object(
            m, "run_vision_header_ocr", return_value=(boxes, {})
        ):
            diagnostics, returned = m.recover_header_metadata(
                self.page, self.root, 17, ["chinese_cht", "pt"],
                engine="vision", level="accurate",
                minimum_text_height=0.004, preprocess=False,
                language_correction="labels", engines={},
                device="cpu", ocr_version="PP-OCRv5",
            )
        self.assertEqual(
            diagnostics["header_fallback_bank_name_lines"],
            "郵政儲金局 | CAIXA ECONÓMICA POSTAL",
        )
        self.assertEqual(len(returned), 3)

    def test_recover_header_metadata_failure_is_non_fatal(self):
        with mock.patch.object(
            m, "run_vision_header_ocr", side_effect=RuntimeError("vision failed")
        ):
            diagnostics, returned = m.recover_header_metadata(
                self.page, self.root, 17, ["chinese_cht"],
                engine="vision", level="accurate",
                minimum_text_height=0.004, preprocess=False,
                language_correction="labels", engines={},
                device="cpu", ocr_version="PP-OCRv5",
            )
        self.assertEqual(diagnostics["header_fallback_status"], "failed")
        self.assertIn("vision failed", diagnostics["header_fallback_error"])
        self.assertEqual(returned, [])


if __name__ == "__main__":
    unittest.main()
