"""Tests for the experimental row-projection structure and amount verifier."""
from __future__ import annotations

import unittest
from pathlib import Path

from macau_tb_ocr import (
    OCRBox,
    _band_for_box,
    _cell_ink_fraction,
    _parse_tesseract_tsv,
    amount_digit_signature,
    amount_verifier_status,
    dedupe_boxes,
    detect_column_layout,
    detect_row_bands,
    group_boxes_into_rows,
    validate_rows,
)


def make_box(text: str, x0: float, y0: float, x1: float, y1: float,
             score: float = 0.95, language: str = "en-US") -> OCRBox:
    return OCRBox(text=text, score=score, x0=x0, y0=y0, x1=x1, y1=y1,
                  language=language, region="table")


class AmountSignatureTests(unittest.TestCase):
    def test_separators_and_spaces_are_ignored(self):
        self.assertEqual(amount_digit_signature("1,234.56"), "123456")
        self.assertEqual(amount_digit_signature("1.234,56"), "123456")
        self.assertEqual(amount_digit_signature("- 1 234,56"), "123456")

    def test_letters_are_kept_so_confusables_do_not_match(self):
        self.assertNotEqual(
            amount_digit_signature("1,00O.00"), amount_digit_signature("1,000.00")
        )

    def test_empty(self):
        self.assertEqual(amount_digit_signature(""), "")


class VerifierStatusTests(unittest.TestCase):
    def test_agree_ignores_separator_style(self):
        self.assertEqual(amount_verifier_status("405.228,717.57", "405,228,717.57"), "agree")

    def test_digit_mismatch(self):
        self.assertEqual(amount_verifier_status("1,000,000.00", "1,000,000.06"), "digit_mismatch")

    def test_one_sided(self):
        self.assertEqual(amount_verifier_status("", "595,846.01"), "verifier_only")
        self.assertEqual(amount_verifier_status("595,846.01", ""), "vision_only")
        self.assertEqual(amount_verifier_status("", ""), "blank")


class TesseractTsvTests(unittest.TestCase):
    PAYLOAD = "\n".join([
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "1\t1\t0\t0\t0\t0\t0\t0\t500\t400\t-1\t",
        "5\t1\t1\t1\t1\t1\t10\t100\t40\t20\t95\t1,234",
        "5\t1\t1\t1\t1\t2\t60\t100\t40\t20\t95\t.56",
        "5\t1\t1\t1\t2\t1\t10\t200\t60\t20\t95\t8.90",
        "5\t1\t1\t1\t2\t2\t80\t200\t10\t20\t95\t",
    ])

    def test_groups_words_into_lines_and_offsets_y(self):
        lines = _parse_tesseract_tsv(self.PAYLOAD, y_offset=1000)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["text"], "1,234 .56")
        self.assertAlmostEqual(lines[0]["y_center"], 1110.0)
        self.assertEqual(lines[1]["text"], "8.90")
        self.assertAlmostEqual(lines[1]["y_center"], 1210.0)


class BandAssignmentTests(unittest.TestCase):
    def test_band_for_box_requires_half_overlap(self):
        bands = [(100.0, 150.0), (160.0, 210.0)]
        inside = make_box("A", 10, 105, 50, 145)
        straddling = make_box("B", 10, 140, 50, 180)
        self.assertEqual(_band_for_box(inside, bands), 0)
        # 10px of the 40px box is in band 0, 20px in band 1 -> band 1 wins.
        self.assertEqual(_band_for_box(straddling, bands), 1)

    def test_rows_follow_bands_and_orphans_cluster(self):
        bands = [(100.0, 150.0), (160.0, 210.0)]
        boxes = [
            make_box("現金", 10, 105, 90, 145),
            make_box("1,000.00", 500, 105, 600, 145),
            make_box("貸款", 10, 165, 90, 205),
            make_box("2,000.00", 500, 165, 600, 205),
            make_box("footer-like orphan", 10, 400, 200, 440),
        ]
        rows = group_boxes_into_rows(boxes, 1000, 1000, (0.4, 0.7), row_bands=bands)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["label_raw"], "現金")
        self.assertEqual(rows[0]["debit_raw"], "1,000.00")
        self.assertEqual(rows[1]["label_raw"], "貸款")
        self.assertEqual(rows[2]["label_raw"], "footer-like orphan")
        self.assertEqual(rows[0]["row_structure_source"], "projection")
        self.assertEqual(rows[2]["row_structure_source"], "clustered")

    def test_without_bands_clusters_by_y(self):
        boxes = [
            make_box("現金", 10, 105, 90, 145),
            make_box("1,000.00", 500, 105, 600, 145),
        ]
        rows = group_boxes_into_rows(boxes, 1000, 1000, (0.4, 0.7))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["row_structure_source"], "clustered")


class CrossLanguageDedupeTests(unittest.TestCase):
    def test_overlapping_chinese_line_prefers_chinese_reading(self):
        zh = make_box("AMCM存款", 100, 100, 320, 140, score=0.80, language="zh-Hant")
        pt = make_box("AMCM73:5X", 100, 100, 320, 140, score=0.90, language="pt-BR")
        kept = dedupe_boxes([zh, pt])
        self.assertEqual([box.text for box in kept], ["AMCM存款"])

    def test_overlapping_latin_line_prefers_portuguese_reading(self):
        zh = make_box("Depositos na AMCM", 100, 100, 320, 140, score=0.95, language="zh-Hant")
        pt = make_box("Depósitos na AMCM", 100, 100, 320, 140, score=0.80, language="pt-BR")
        kept = dedupe_boxes([zh, pt])
        self.assertEqual([box.text for box in kept], ["Depósitos na AMCM"])

    def test_separate_bilingual_lines_are_both_kept(self):
        zh = make_box("現金", 100, 100, 320, 140, language="zh-Hant")
        pt = make_box("Caixa", 100, 160, 320, 200, language="pt-BR")
        kept = dedupe_boxes([zh, pt])
        self.assertEqual(len(kept), 2)

    def test_same_language_overlap_keeps_higher_score(self):
        a = make_box("現金", 100, 100, 320, 140, score=0.95, language="zh-Hant")
        b = make_box("现金", 100, 100, 320, 140, score=0.70, language="zh-Hant")
        kept = dedupe_boxes([a, b])
        self.assertEqual([box.text for box in kept], ["現金"])


class SecondPassTests(unittest.TestCase):
    def test_cell_ink_fraction_separates_text_from_blank(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")
        blank = np.full((60, 200), 255, dtype=np.uint8)
        inked = blank.copy()
        inked[20:40, 60:140] = 0
        self.assertLess(_cell_ink_fraction(blank, 0, 200, 0, 60), 0.01)
        self.assertGreater(_cell_ink_fraction(inked, 0, 200, 0, 60), 0.1)

    def row(self, **overrides) -> dict:
        base = {
            "label_raw": "Outros valores", "debit_raw": "", "credit_raw": "",
            "debit_confidence": 0.95, "credit_confidence": 0.95, "row_confidence": 0.95,
        }
        base.update(overrides)
        return base

    def test_empty_primary_uses_second_pass_and_reviews(self):
        row = self.row(debit_second_pass_raw="143,376.00")
        result = validate_rows([row], 0.88, amount_second_pass=True)[0]
        self.assertEqual(result["debit_amount"], "143376.00")
        self.assertEqual(result["debit_parse_status"], "recovered_second_pass")
        self.assertEqual(result["debit_second_pass_status"], "used")
        self.assertIn("debit:recovered_second_pass", result["review_reason"])

    def test_low_confidence_primary_is_replaced(self):
        row = self.row(debit_raw="0928186901", debit_confidence=0.4,
                       debit_second_pass_raw="104,698,182.60")
        result = validate_rows([row], 0.88, number_format_override="comma_dot",
                               amount_second_pass=True)[0]
        self.assertEqual(result["debit_amount"], "104698182.60")
        self.assertEqual(result["debit_second_pass_status"], "replaced_low_confidence")
        self.assertIn("debit:recovered_second_pass", result["review_reason"])

    def test_confident_primary_is_kept_when_second_pass_differs(self):
        row = self.row(debit_raw="1,000.00", debit_confidence=0.99,
                       debit_second_pass_raw="1,000.06")
        result = validate_rows([row], 0.88, number_format_override="comma_dot",
                               amount_second_pass=True)[0]
        self.assertEqual(result["debit_amount"], "1000.00")
        self.assertEqual(result["debit_second_pass_status"], "differs")
        self.assertIn("debit:second_pass_differs", result["review_reason"])

    def test_agreement_adds_no_review_reason(self):
        row = self.row(debit_raw="1,000.00", debit_confidence=0.99,
                       debit_second_pass_raw="1,000.00")
        result = validate_rows([row], 0.88, number_format_override="comma_dot",
                               amount_second_pass=True)[0]
        self.assertEqual(result["debit_second_pass_status"], "agree")
        self.assertNotIn("second_pass", result["review_reason"])


class ColumnLayoutTests(unittest.TestCase):
    def _image(self, lines: list[int]) -> Path:
        import tempfile
        import cv2
        import numpy as np
        image = np.full((600, 800), 255, dtype=np.uint8)
        for x in lines:
            image[:, x:x + 4] = 0
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        cv2.imwrite(str(path), image)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_two_rules_are_the_amount_columns(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed")
        path = self._image([400, 600])
        sep1, sep2, label_x0, source = detect_column_layout(path)
        self.assertAlmostEqual(sep1, 0.5, places=2)
        self.assertAlmostEqual(sep2, 0.75, places=2)
        self.assertEqual(label_x0, 0.005)
        self.assertEqual(source, "detected_morphology")

    def test_leading_code_column_is_excluded_from_label_roi(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")
        image = np.full((600, 800), 255, dtype=np.uint8)
        for x in (120, 520, 660):
            image[:, x:x + 4] = 0
        # Account codes left of the code|label rule, on most rows. A blank code
        # column would be indistinguishable from a table margin. Amount-like
        # marks in both amount columns keep their rules from looking like a
        # frame (a frame has no content on its outside).
        for y in range(30, 570, 20):
            image[y:y + 14, 20:60] = 0
            image[y:y + 14, 440:495] = 0
            image[y:y + 14, 680:720] = 0
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        cv2.imwrite(str(path), image)
        self.addCleanup(path.unlink, missing_ok=True)
        sep1, sep2, label_x0, _source = detect_column_layout(path)
        self.assertAlmostEqual(sep1, 0.65, places=2)
        self.assertAlmostEqual(sep2, 0.825, places=2)
        self.assertGreater(label_x0, 0.15)
        self.assertLess(label_x0, 0.16)

    def test_outer_table_borders_are_not_amount_separators(self):
        """Issue 5-style frames: left | label/debit | debit/credit | right.

        The right (and left) outer frame must not become sep2/sep1, or the true
        label|debit rule is misclassified as an account-code trim and every
        amount lands in the wrong ROI.
        """
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed")
        # 800px wide: ~0.10, ~0.46, ~0.66, ~0.90 (4px-wide rules shift centres slightly)
        path = self._image([80, 370, 530, 720])
        sep1, sep2, label_x0, source = detect_column_layout(path)
        self.assertAlmostEqual(sep1, 0.46, delta=0.02)
        self.assertAlmostEqual(sep2, 0.66, delta=0.02)
        self.assertEqual(label_x0, 0.005)
        self.assertEqual(source, "detected_morphology")

    def test_inset_table_frames_are_dropped_before_amount_separators(self):
        """Frames inside the interior band (whitespace outside the printed table)."""
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed")
        # 800px: left frame 0.20, label|debit 0.47, debit|credit 0.63, right frame 0.80
        path = self._image([160, 376, 504, 640])
        sep1, sep2, label_x0, source = detect_column_layout(path)
        self.assertAlmostEqual(sep1, 0.47, delta=0.02)
        self.assertAlmostEqual(sep2, 0.63, delta=0.02)
        self.assertEqual(label_x0, 0.005)
        self.assertEqual(source, "detected_morphology")

    def test_inset_left_frame_with_edge_right_frame(self):
        """Issue 20/2025 page 35: the right frame is excluded by the edge cut.

        The left frame at 0.157 used to become sep1, moving every debit amount
        into the credit column.
        """
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed")
        # 800px: left frame 0.157, label|debit 0.472, debit|credit 0.678,
        # right frame 0.883 (inside the 0.88 edge cut-off).
        path = self._image([126, 378, 542, 707])
        sep1, sep2, _label_x0, source = detect_column_layout(path)
        self.assertAlmostEqual(sep1, 0.472, delta=0.02)
        self.assertAlmostEqual(sep2, 0.678, delta=0.02)
        self.assertEqual(source, "detected_morphology")

    def test_stamp_in_the_margin_does_not_keep_a_table_frame(self):
        """A seal left of the frame must not make the frame look like a column."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")
        image = np.full((600, 800), 255, dtype=np.uint8)
        for x in (126, 378, 542):
            image[:, x:x + 4] = 0
        # Seal-like block in the left margin covering ~25% of the table height.
        image[240:340, 20:110] = 0
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        cv2.imwrite(str(path), image)
        self.addCleanup(path.unlink, missing_ok=True)
        sep1, sep2, _label_x0, _source = detect_column_layout(path)
        self.assertAlmostEqual(sep1, 0.472, delta=0.02)
        self.assertAlmostEqual(sep2, 0.678, delta=0.02)


class RowBandDetectionTests(unittest.TestCase):
    def test_blank_image_has_no_bands(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")
        import tempfile
        image = np.full((600, 800), 255, dtype=np.uint8)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        try:
            cv2.imwrite(str(path), image)
            bands, source = detect_row_bands(path, (0.4, 0.7))
            self.assertEqual((bands, source), ([], "clustered"))
        finally:
            path.unlink(missing_ok=True)

    def test_text_lines_produce_bands(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")
        import tempfile
        image = np.full((600, 800), 255, dtype=np.uint8)
        for top in (120, 200, 280, 360, 440):
            image[top:top + 40, 400:760] = 0
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        try:
            cv2.imwrite(str(path), image)
            bands, source = detect_row_bands(path, (0.4, 0.7))
            self.assertEqual(source, "projection")
            self.assertGreaterEqual(len(bands), 4)
            self.assertTrue(all(b > a for a, b in bands))
        finally:
            path.unlink(missing_ok=True)


class VerifierReviewPolicyTests(unittest.TestCase):
    def test_clean_vision_value_is_not_escalated(self):
        rows = [{
            "label_raw": "現金", "debit_raw": "1,000,000.00", "credit_raw": "",
            "debit_verifier_raw": "1,000,000.06", "credit_verifier_raw": "",
            "row_confidence": 0.99,
        }]
        result = validate_rows(rows, 0.88, amount_verifier=True)[0]
        self.assertEqual(result["debit_verifier_status"], "digit_mismatch")
        self.assertNotIn("verifier_digit_mismatch", result["review_reason"])
        self.assertEqual(
            result["debit_verifier_note"], "digit_mismatch_vision_parsed"
        )

    def test_unparsed_vision_value_is_escalated(self):
        rows = [{
            "label_raw": "現金", "debit_raw": "1,00O.00", "credit_raw": "",
            "debit_verifier_raw": "1,000.00", "credit_verifier_raw": "",
            "row_confidence": 0.99,
        }]
        result = validate_rows(rows, 0.88, amount_verifier=True)[0]
        self.assertEqual(result["debit_verifier_status"], "digit_mismatch")
        self.assertIn("debit:verifier_digit_mismatch", result["review_reason"])
        self.assertEqual(result["review_status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
