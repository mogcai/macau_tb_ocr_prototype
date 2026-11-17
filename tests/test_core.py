import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macau_tb_ocr import OCRBox, parse_amount, parse_statement_date, detect_languages, parse_page_range, group_boxes_into_rows


class CoreTests(unittest.TestCase):
    def test_chinese_date(self):
        self.assertEqual(parse_statement_date("試算表於二零二六年九月三十日"), "2026-09-30")

    def test_numeric_date(self):
        self.assertEqual(parse_statement_date("於2026年09月30日"), "2026-09-30")

    def test_portuguese_date(self):
        self.assertEqual(parse_statement_date("Balancete do razão em 30 de Setembro de 2026"), "2026-09-30")

    def test_amount_us_style(self):
        self.assertEqual(parse_amount("1,234,567.89")[0], "1234567.89")

    def test_amount_european_style(self):
        self.assertEqual(parse_amount("1.234.567,89")[0], "1234567.89")

    def test_ambiguous_zero_flagged(self):
        value, issue = parse_amount("1O,5O0.00")
        self.assertEqual(value, "10500.00")
        self.assertEqual(issue, "ambiguous_ocr_character")

    def test_language_chinese(self):
        self.assertEqual(detect_languages("澳門銀行 試算表於2026年9月30日"), ["chinese_cht"])

    def test_language_portuguese(self):
        self.assertEqual(detect_languages("HANG SENG BANK LIMITED BALANCETE DO RAZAO"), ["pt"])

    def test_language_bilingual(self):
        self.assertEqual(detect_languages("澳門銀行 試算表 Balancete do razão"), ["chinese_cht", "pt"])

    def test_page_range(self):
        self.assertEqual(parse_page_range("7-32", 32), (6, 32))

    def test_chinese_footer_does_not_force_portuguese_model(self):
        text = "澳門銀行 試算表於2026年9月30日\n(Custo desta publicação $ 1,250.00)\n澳門特別行政區公報"
        self.assertEqual(detect_languages(text), ["chinese_cht"])

    def test_group_boxes_into_three_columns(self):
        boxes = [
            OCRBox("現金", 0.98, 20, 100, 140, 125, "chinese_cht"),
            OCRBox("100.00", 0.96, 500, 100, 620, 125, "chinese_cht"),
            OCRBox("-", 0.95, 820, 100, 830, 125, "chinese_cht"),
            OCRBox("存款", 0.97, 20, 200, 140, 225, "chinese_cht"),
            OCRBox("200.00", 0.96, 820, 200, 940, 225, "chinese_cht"),
        ]
        rows = group_boxes_into_rows(boxes, image_width=1000, image_height=1000, boundaries=(0.45, 0.75))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["label_raw"], "現金")
        self.assertEqual(rows[0]["debit_raw"], "100.00")
        self.assertEqual(rows[0]["credit_raw"], "-")
        self.assertEqual(rows[1]["credit_raw"], "200.00")


if __name__ == "__main__":
    unittest.main()
