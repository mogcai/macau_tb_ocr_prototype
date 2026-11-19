#!/usr/bin/env python3
"""Build the P2-D statement validation / adoption worksheet.

Read-only: reads v3 statements, statement rows and the v5 legacy statement
metadata, then writes a CSV for human review.  It does not adopt or publish a
statement.

Usage::

    python tools/build_statement_validation_worksheet.py \
        --db data/macau_balancete_v3.sqlite \
        --v5-db data/baseline/baseline_v5_20261118.sqlite \
        --out docs/audit/statement_validation_decisions.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_V5 = ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite"
DEFAULT_PLAN = ROOT / "docs" / "audit" / "statement_segmentation_plan.csv"
DEFAULT_OUT = ROOT / "docs" / "audit" / "statement_validation_decisions.csv"


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


def money(minor: int | None, places: int | None) -> str:
    if minor is None:
        return ""
    scale = places if places is not None else 2
    value = Decimal(minor) / (Decimal(10) ** scale)
    return format(value, ",.2f")


def decide_validation(
    debit_total_minor: int,
    credit_total_minor: int,
    flags: list[str],
    row_count: int,
    page_count: int,
) -> tuple[str, str]:
    """Return (validation_status, adoption_action).

    Project rule: equal debit/credit totals suggest adoption; unequal totals
    hold for review.  Critical structural failures still block adoption.
    """
    if row_count == 0 or page_count == 0:
        return "reject", "reject"
    critical = {
        "row_count_mismatch", "extraction_not_completed",
        "entity_not_approved", "duplicate_identity",
    }
    if set(flags) & critical:
        return "needs_review", "hold"
    if debit_total_minor == credit_total_minor:
        return "ready_for_adoption", "adopt"
    return "needs_review", "hold"


def build(db: Path, v5_db: Path, plan_csv: Path) -> list[dict[str, Any]]:
    plan = {row["statement_id"]: row for row in read_csv(plan_csv)}
    v3 = connect_ro(db)
    v5 = connect_ro(v5_db)
    try:
        identity_counts = Counter(
            row["identity_key"] for row in v3.execute("SELECT identity_key FROM statements")
        )
        legacy_row_counts = {
            row["statement_id"]: row["row_count"]
            for row in v5.execute("SELECT statement_id, row_count FROM statements")
        }
        rows: list[dict[str, Any]] = []
        for statement in v3.execute(
            """
            SELECT s.statement_id, s.identity_key, s.statement_date, s.publication_date,
                   s.reporting_scope, s.statement_status, s.identity_status,
                   s.metadata_status, s.currency, s.unit_scale, s.amount_decimal_places,
                   s.adopted_statement_extraction_id, s.source_document_id,
                   e.entity_id, e.entity_key, e.canonical_zh, e.registry_status,
                   se.statement_extraction_id, se.extraction_status
            FROM statements s
            JOIN reporting_entities e ON e.entity_id = s.reporting_entity_id
            LEFT JOIN statement_extractions se ON se.statement_id = s.statement_id
            ORDER BY s.statement_date, e.canonical_zh, s.statement_id
            """
        ).fetchall():
            plan_row = plan.get(statement["statement_id"], {})
            legacy_statement_id = text(plan_row.get("legacy_statement_id"))
            legacy_row_count = legacy_row_counts.get(legacy_statement_id)
            page_count = v3.execute(
                "SELECT COUNT(*) FROM statement_pages WHERE statement_id = ?",
                (statement["statement_id"],),
            ).fetchone()[0]
            metrics = v3.execute(
                """
                SELECT COUNT(*) AS row_count,
                       SUM(CASE WHEN label_normalized IS NULL OR label_normalized = '' THEN 1 ELSE 0 END) AS missing_labels,
                       SUM(CASE WHEN debit_value_state = 'ambiguous' OR credit_value_state = 'ambiguous' THEN 1 ELSE 0 END) AS ambiguous,
                       SUM(CASE WHEN debit_value_state = 'unreadable' OR credit_value_state = 'unreadable' THEN 1 ELSE 0 END) AS unreadable,
                       SUM(CASE WHEN parent_context_status = 'unknown' THEN 1 ELSE 0 END) AS parent_unknown,
                       SUM(CASE WHEN debit_value_state = 'amount' THEN debit_minor ELSE 0 END) AS debit_total_minor,
                       SUM(CASE WHEN credit_value_state = 'amount' THEN credit_minor ELSE 0 END) AS credit_total_minor,
                       SUM(CASE WHEN debit_value_state = 'amount' THEN 1 ELSE 0 END) AS debit_amount_rows,
                       SUM(CASE WHEN credit_value_state = 'amount' THEN 1 ELSE 0 END) AS credit_amount_rows
                FROM statement_rows WHERE statement_id = ?
                """,
                (statement["statement_id"],),
            ).fetchone()
            debit_total_minor = int(metrics["debit_total_minor"] or 0)
            credit_total_minor = int(metrics["credit_total_minor"] or 0)
            debit_credit_difference = debit_total_minor - credit_total_minor
            debit_credit_equal = debit_credit_difference == 0

            flags: list[str] = []
            if not debit_credit_equal:
                flags.append("debit_credit_mismatch")
            if int(metrics["row_count"] or 0) == 0:
                flags.append("no_statement_rows")
            if page_count == 0:
                flags.append("no_statement_pages")
            if legacy_row_count is not None and int(metrics["row_count"] or 0) != int(legacy_row_count):
                flags.append("row_count_mismatch")
            if int(metrics["missing_labels"] or 0):
                flags.append("missing_labels")
            if int(metrics["ambiguous"] or 0):
                flags.append("ambiguous_amounts")
            if int(metrics["unreadable"] or 0):
                flags.append("unreadable_amounts")
            if int(metrics["parent_unknown"] or 0):
                flags.append("parent_context_unknown")
            if statement["currency"] is None:
                flags.append("currency_unconfirmed")
            if statement["unit_scale"] is None:
                flags.append("unit_scale_unconfirmed")
            if statement["extraction_status"] != "completed":
                flags.append("extraction_not_completed")
            if statement["registry_status"] != "approved":
                flags.append("entity_not_approved")
            if identity_counts[statement["identity_key"]] > 1:
                flags.append("duplicate_identity")
            if statement["adopted_statement_extraction_id"] is not None:
                flags.append("already_adopted")

            validation, action = decide_validation(
                debit_total_minor,
                credit_total_minor,
                flags,
                int(metrics["row_count"] or 0),
                page_count,
            )

            places = statement["amount_decimal_places"]
            reason_parts = [
                "debit_credit_equal" if debit_credit_equal else "debit_credit_mismatch"
            ]
            reason_parts.extend(flags)
            rows.append({
                "statement_id": statement["statement_id"],
                "issue_number": plan_row.get("issue_key", ""),
                "entity_name": text(statement["canonical_zh"]),
                "entity_key": text(statement["entity_key"]),
                "statement_date": statement["statement_date"] or "",
                "publication_date": statement["publication_date"] or "",
                "reporting_scope": statement["reporting_scope"],
                "legacy_statement_id": legacy_statement_id,
                "statement_extraction_id": statement["statement_extraction_id"] or "",
                "page_count": page_count,
                "pdf_pages": plan_row.get("pages", ""),
                "row_count": metrics["row_count"] or 0,
                "legacy_row_count": legacy_row_count if legacy_row_count is not None else "",
                "row_count_match": (
                    "" if legacy_row_count is None
                    else "yes" if int(metrics["row_count"] or 0) == int(legacy_row_count) else "no"
                ),
                "label_missing_count": metrics["missing_labels"] or 0,
                "ambiguous_count": metrics["ambiguous"] or 0,
                "unreadable_count": metrics["unreadable"] or 0,
                "parent_unknown_count": metrics["parent_unknown"] or 0,
                "debit_amount_rows": metrics["debit_amount_rows"] or 0,
                "credit_amount_rows": metrics["credit_amount_rows"] or 0,
                "debit_total_minor": debit_total_minor,
                "credit_total_minor": credit_total_minor,
                "debit_credit_difference_minor": debit_credit_difference,
                "debit_credit_equal": "yes" if debit_credit_equal else "no",
                "debit_total_display": money(debit_total_minor, places),
                "credit_total_display": money(credit_total_minor, places),
                "currency": statement["currency"] or "",
                "unit_scale": statement["unit_scale"] if statement["unit_scale"] is not None else "",
                "amount_decimal_places": places if places is not None else "",
                "statement_status": statement["statement_status"],
                "identity_status": statement["identity_status"],
                "metadata_status": statement["metadata_status"],
                "extraction_status": statement["extraction_status"] or "",
                "adopted_statement_extraction_id": statement["adopted_statement_extraction_id"] or "",
                "proposed_validation_status": validation,
                "proposed_adoption_action": action,
                "validation_flags": ";".join(flags),
                "validation_reason": ";".join(reason_parts),
                "reviewer_decision": "",
                "reviewer_note": "",
            })
    finally:
        v3.close()
        v5.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--v5-db", type=Path, default=DEFAULT_V5)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rows = build(args.db, args.v5_db, args.plan)
    fields = [
        "statement_id", "issue_number", "entity_name", "entity_key",
        "statement_date", "publication_date", "reporting_scope",
        "legacy_statement_id", "statement_extraction_id", "page_count",
        "pdf_pages", "row_count", "legacy_row_count", "row_count_match",
        "label_missing_count", "ambiguous_count", "unreadable_count",
        "parent_unknown_count", "debit_amount_rows", "credit_amount_rows",
        "debit_total_minor", "credit_total_minor", "debit_credit_difference_minor",
        "debit_credit_equal", "debit_total_display", "credit_total_display",
        "currency", "unit_scale",
        "amount_decimal_places", "statement_status", "identity_status",
        "metadata_status", "extraction_status", "adopted_statement_extraction_id",
        "proposed_validation_status", "proposed_adoption_action",
        "validation_flags", "validation_reason", "reviewer_decision",
        "reviewer_note",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} statements to {args.out}")
    print(Counter(row["proposed_validation_status"] for row in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
