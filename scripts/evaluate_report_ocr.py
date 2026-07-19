from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from rag_assistant.report_extractor import JOURNAL_COLUMNS, extract_batch_pdf


def normalized(value):
    if isinstance(value, (date, datetime)):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, str):
        return value.strip().upper().replace(",", ".")
    return value


def equal(actual, expected) -> bool:
    actual = normalized(actual)
    expected = normalized(expected)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(expected)) <= 1e-3
    return actual == expected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("expected", type=Path)
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()
    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    reports, journal, warnings = extract_batch_pdf(args.pdf.read_bytes(), args.pdf.name, dpi=args.dpi)
    report = reports.iloc[0].to_dict()
    checks = []
    errors = []
    for field, target in expected["report"].items():
        ok = equal(report.get(field), target)
        checks.append(ok)
        if not ok:
            errors.append({"field": field, "expected": target, "actual": report.get(field)})
    actual_journal = journal[JOURNAL_COLUMNS[2:]].values.tolist()
    for row_index, target_row in enumerate(expected["journal"]):
        actual_row = actual_journal[row_index] if row_index < len(actual_journal) else [None] * len(target_row)
        for column_index, target in enumerate(target_row):
            actual = actual_row[column_index] if column_index < len(actual_row) else None
            ok = equal(actual, target)
            checks.append(ok)
            if not ok:
                errors.append({
                    "field": f"journal[{row_index}].{JOURNAL_COLUMNS[column_index + 2]}",
                    "expected": target,
                    "actual": actual,
                })
    score = sum(checks) / max(1, len(checks))
    print(json.dumps({
        "dpi": args.dpi,
        "correct": sum(checks),
        "total": len(checks),
        "accuracy": round(score, 4),
        "errors": errors,
        "warnings": warnings.to_dict(orient="records"),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
