import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from rag_assistant.parsers import parse_csv, parse_json, parse_xlsx


class StructuredParserTests(unittest.TestCase):
    def test_csv_adds_full_dataset_summary_and_named_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            path.write_text(
                "Узел;Температура;Состояние\nA;100;норма\nB;120;тревога\nC;;норма\n",
                encoding="cp1251",
            )

            blocks = parse_csv(path)

            self.assertEqual("data_summary", blocks[0].block_type)
            self.assertIn("Строк данных: 3; столбцов: 3", blocks[0].text)
            self.assertIn("мин. 100", blocks[0].text)
            self.assertIn("макс. 120", blocks[0].text)
            self.assertIn("Температура=100", blocks[1].text)

    def test_json_record_list_is_parsed_as_structured_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(
                json.dumps({"rows": [{"tag": "TIR_1", "value": 10}, {"tag": "TIR_2", "value": 20}]}),
                encoding="utf-8",
            )

            blocks = parse_json(path)

            self.assertIn("Строк данных: 2; столбцов: 2", blocks[0].text)
            self.assertIn("tag=TIR_1", blocks[1].text)

    def test_json_nested_values_do_not_break_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested.json"
            path.write_text(
                json.dumps([{"tag": "TIR_1", "limits": [10, 20]}]),
                encoding="utf-8",
            )

            blocks = parse_json(path)

            self.assertIn("limits=[10, 20]", blocks[1].text)

    def test_xlsx_summary_covers_all_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.xlsx"
            pd.DataFrame(
                {"Параметр": ["A", "B", "C"], "Значение": [1, 3, 5]}
            ).to_excel(path, index=False)

            blocks = parse_xlsx(path)

            self.assertIn("Строк данных: 3; столбцов: 2", blocks[0].text)
            self.assertIn("среднее 3.0", blocks[0].text)


if __name__ == "__main__":
    unittest.main()
