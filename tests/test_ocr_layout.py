import unittest

import cv2
import numpy as np

from rag_assistant.parsers import _detect_table_cells, _group_text_lines


class OcrLayoutTests(unittest.TestCase):
    def test_groups_text_and_preserves_wide_column_gap(self):
        items = [
            {"text": "Параметр", "x1": 10, "x2": 90, "cy": 20, "height": 18},
            {"text": "Значение", "x1": 180, "x2": 250, "cy": 21, "height": 18},
            {"text": "Давление", "x1": 10, "x2": 90, "cy": 55, "height": 18},
            {"text": "5 МПа", "x1": 180, "x2": 235, "cy": 56, "height": 18},
        ]
        lines = _group_text_lines(items)
        self.assertEqual(["Параметр | Значение", "Давление | 5 МПа"], lines)

    def test_detects_grid_as_table_rows(self):
        image = np.full((260, 500, 3), 255, dtype=np.uint8)
        for x in (20, 220, 480):
            cv2.line(image, (x, 30), (x, 230), (0, 0, 0), 3)
        for y in (30, 95, 160, 230):
            cv2.line(image, (20, y), (480, y), (0, 0, 0), 3)
        rows = _detect_table_cells(image)
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(all(len(row) >= 2 for row in rows))


if __name__ == "__main__":
    unittest.main()
