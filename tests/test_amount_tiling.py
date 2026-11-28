"""Tests for row-gap-aligned amount-column tiling (Vision ROI fix)."""
from __future__ import annotations

import unittest

from macau_tb_ocr import detect_amount_tiles, detect_label_tiles


def _synthetic_image():
    import cv2
    import numpy as np

    image = np.full((800, 1000), 255, dtype=np.uint8)
    # Three vertical rules: label|debit, debit|credit, right table frame.
    for x in (400, 700, 900):
        cv2.line(image, (x, 30), (x, 790), 0, 2)
    # Amount-like blobs, right-aligned in each column, in three separated rows.
    for top in (100, 400, 700):
        image[top:top + 30, 640:695] = 0    # debit
        image[top:top + 30, 850:895] = 0    # credit
    return image


class AmountTilingTests(unittest.TestCase):
    def test_tiles_split_at_blank_rows_and_keep_every_amount_intact(self):
        image = _synthetic_image()
        tiles, masked = detect_amount_tiles(image, 0.4, 0.7, max_tile_height=150)

        self.assertIn("debit", tiles)
        self.assertIn("credit", tiles)
        for column in ("debit", "credit"):
            self.assertGreaterEqual(len(tiles[column]), 3, column)
            for top in (100, 400, 700):
                containing = [
                    (y0, y1) for y0, y1 in tiles[column]
                    if y0 <= top and y1 >= top + 30
                ]
                self.assertEqual(
                    len(containing), 1,
                    f"{column} amount at y={top} was split: {tiles[column]}",
                )
            for y0, y1 in tiles[column]:
                self.assertGreaterEqual(y0, 0)
                self.assertLessEqual(y1, 800)

    def test_vertical_rules_are_painted_white(self):
        image = _synthetic_image()
        _tiles, masked = detect_amount_tiles(image, 0.4, 0.7)
        for x in (400, 700, 900):
            self.assertTrue((masked[300, x - 1:x + 3] == 255).all(), x)

    def test_blank_page_falls_back_to_one_full_height_tile(self):
        import numpy as np

        image = np.full((600, 900), 255, dtype=np.uint8)
        tiles, _masked = detect_amount_tiles(image, 0.4, 0.7)
        self.assertEqual(len(tiles["debit"]), 1)
        self.assertEqual(len(tiles["credit"]), 1)
        self.assertLess(tiles["debit"][0][0], 30)
        self.assertGreater(tiles["debit"][0][1], 590)

    def test_label_column_is_tiled_at_blank_rows(self):
        image = _synthetic_image()
        # Put label-like blobs in the label column at the same row groups.
        for top in (100, 400, 700):
            image[top:top + 30, 80:360] = 0
        tiles = detect_label_tiles(image, 0.4, 0.005, max_tile_height=150)
        self.assertGreaterEqual(len(tiles), 3)
        for top in (100, 400, 700):
            containing = [
                (y0, y1) for y0, y1 in tiles if y0 <= top and y1 >= top + 30
            ]
            self.assertEqual(len(containing), 1, f"label at y={top} was split")


if __name__ == "__main__":
    unittest.main()
