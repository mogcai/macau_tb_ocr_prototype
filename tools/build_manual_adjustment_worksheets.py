#!/usr/bin/env python3
"""Build manual-review and row-adjustment worksheets for held statements.

Reads the reviewed statement validation CSV, selects
``reviewer_decision = needs_further_review``, and writes:

* ``statement_manual_review.csv`` — one row per held statement;
* ``statement_row_adjustments.csv`` — only rows with known missing/ambiguous/
  unreadable evidence.

No database writes.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_REVIEWED = ROOT / "docs" / "audit" / "statement_validation_decisions.csv"
DEFAULT_OUT = ROOT / "docs" / "audit"


def connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def build(db: Path, reviewed_csv: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reviewed = read_csv(reviewed_csv)
    held = [row for row in reviewed if text(row.get("reviewer_decision")) == "needs_further_review"]
    conn = connect_ro(db)
    try:
        statement_plan: list[dict[str, Any]] = []
        row_plan: list[dict[str, Any]] = []
        for row in held:
            statement_id = text(row["statement_id"])
            metrics = conn.execute(
                """
                SELECT COUNT(*) AS row_count,
                       SUM(CASE WHEN label_normalized IS NULL OR label_normalized = '' THEN 1 ELSE 0 END) AS missing_label,
                       SUM(CASE WHEN debit_value_state IN ('ambiguous', 'unreadable') THEN 1 ELSE 0 END) AS debit_issue,
                       SUM(CASE WHEN credit_value_state IN ('ambiguous', 'unreadable') THEN 1 ELSE 0 END) AS credit_issue
                FROM statement_rows WHERE statement_id = ?
                """,
                (statement_id,),
            ).fetchone()
            flagged = conn.execute(
                """
                SELECT sr.*, er.label_raw, er.debit_raw, er.credit_raw,
                       dp.pdf_page
                FROM statement_rows sr
                JOIN extracted_rows er
                  ON er.extracted_row_id = sr.extracted_row_id
                JOIN document_pages dp
                  ON dp.document_page_id = er.document_page_id
                WHERE sr.statement_id = ?
                  AND (
                    sr.label_normalized IS NULL OR sr.label_normalized = ''
                    OR sr.debit_value_state IN ('ambiguous', 'unreadable')
                    OR sr.credit_value_state IN ('ambiguous', 'unreadable')
                  )
                ORDER BY sr.statement_order, sr.statement_row_id
                """,
                (statement_id,),
            ).fetchall()
            reasons: Counter[str] = Counter()
            for flagged_row in flagged:
                if not text(flagged_row["label_normalized"]):
                    reasons["missing_label"] += 1
                if flagged_row["debit_value_state"] in ("ambiguous", "unreadable"):
                    reasons["debit_" + flagged_row["debit_value_state"]] += 1
                if flagged_row["credit_value_state"] in ("ambiguous", "unreadable"):
                    reasons["credit_" + flagged_row["credit_value_state"]] += 1

            note = text(row.get("reviewer_note"))
            if "ocr" in note.lower() or "bad" in note.lower():
                recommended = "rerun_ocr"
            elif flagged:
                recommended = "row_adjustment"
            elif row.get("debit_credit_equal") == "no":
                recommended = "rerun_ocr"
            else:
                recommended = "manual_verify_pdf"

            statement_plan.append({
                "statement_id": statement_id,
                "issue_number": row.get("issue_number", ""),
                "entity_name": row.get("entity_name", ""),
                "statement_date": row.get("statement_date", ""),
                "publication_date": row.get("publication_date", ""),
                "reporting_scope": row.get("reporting_scope", ""),
                "pdf_pages": row.get("pdf_pages", ""),
                "row_count": metrics["row_count"],
                "debit_total_display": row.get("debit_total_display", ""),
                "credit_total_display": row.get("credit_total_display", ""),
                "debit_credit_equal": row.get("debit_credit_equal", ""),
                "debit_credit_difference_minor": row.get("debit_credit_difference_minor", ""),
                "validation_flags": row.get("validation_flags", ""),
                "flagged_row_count": len(flagged),
                "flagged_reasons": ";".join(f"{key}={value}" for key, value in sorted(reasons.items())),
                "reviewer_note": note,
                "recommended_action": recommended,
                "review_action": "",
                "manual_note": "",
            })

            for flagged_row in flagged:
                row_reasons = []
                if not text(flagged_row["label_normalized"]):
                    row_reasons.append("missing_label")
                if flagged_row["debit_value_state"] in ("ambiguous", "unreadable"):
                    row_reasons.append("debit_" + flagged_row["debit_value_state"])
                if flagged_row["credit_value_state"] in ("ambiguous", "unreadable"):
                    row_reasons.append("credit_" + flagged_row["credit_value_state"])
                row_plan.append({
                    "statement_id": statement_id,
                    "issue_number": row.get("issue_number", ""),
                    "entity_name": row.get("entity_name", ""),
                    "pdf_page": flagged_row["pdf_page"],
                    "statement_row_id": flagged_row["statement_row_id"],
                    "extracted_row_id": flagged_row["extracted_row_id"],
                    "statement_order": flagged_row["statement_order"],
                    "label_raw": flagged_row["label_raw"] or "",
                    "label_normalized": flagged_row["label_normalized"] or "",
                    "label_language": flagged_row["label_language"] or "",
                    "debit_raw": flagged_row["debit_raw"] or "",
                    "debit_minor": flagged_row["debit_minor"] if flagged_row["debit_minor"] is not None else "",
                    "debit_value_state": flagged_row["debit_value_state"],
                    "credit_raw": flagged_row["credit_raw"] or "",
                    "credit_minor": flagged_row["credit_minor"] if flagged_row["credit_minor"] is not None else "",
                    "credit_value_state": flagged_row["credit_value_state"],
                    "parent_context_status": flagged_row["parent_context_status"],
                    "row_issue": ";".join(row_reasons),
                    "corrected_label": "",
                    "corrected_debit": "",
                    "corrected_credit": "",
                    "corrected_debit_value_state": "",
                    "corrected_credit_value_state": "",
                    "reviewer_note": "",
                    "adjustment_status": "",
                })
    finally:
        conn.close()
    return statement_plan, row_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--reviewed-csv", type=Path, default=DEFAULT_REVIEWED)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    statement_plan, row_plan = build(args.db, args.reviewed_csv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    statement_fields = [
        "statement_id", "issue_number", "entity_name", "statement_date",
        "publication_date", "reporting_scope", "pdf_pages", "row_count",
        "debit_total_display", "credit_total_display", "debit_credit_equal",
        "debit_credit_difference_minor", "validation_flags", "flagged_row_count",
        "flagged_reasons", "reviewer_note", "recommended_action", "review_action",
        "manual_note",
    ]
    row_fields = [
        "statement_id", "issue_number", "entity_name", "pdf_page", "statement_row_id",
        "extracted_row_id", "statement_order", "label_raw", "label_normalized",
        "label_language", "debit_raw", "debit_minor", "debit_value_state",
        "credit_raw", "credit_minor", "credit_value_state", "parent_context_status",
        "row_issue", "corrected_label", "corrected_debit", "corrected_credit",
        "corrected_debit_value_state", "corrected_credit_value_state",
        "reviewer_note", "adjustment_status",
    ]
    statement_path = args.out_dir / "statement_manual_review.csv"
    row_path = args.out_dir / "statement_row_adjustments.csv"
    with statement_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=statement_fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(statement_plan)
    with row_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=row_fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(row_plan)
    print(f"held statements: {len(statement_plan)} -> {statement_path}")
    print(f"flagged rows: {len(row_plan)} -> {row_path}")
    print(Counter(row["recommended_action"] for row in statement_plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
