#!/usr/bin/env python3
"""Apply reviewed row corrections as field_verifications.

Reads ``statement_manual_review.csv`` and ``statement_row_adjustments.csv``.
Only statements whose manual review action is ``row_adjustment`` are applied;
``rerun_ocr`` statements are skipped and reported.

Default mode is a dry run and writes a plan CSV.  ``--apply`` writes
``field_verifications`` and append-only audit events; raw OCR fields and
statement row snapshots are never changed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_REVIEW = ROOT / "docs" / "audit" / "statement_manual_review.csv"
DEFAULT_ROWS = ROOT / "docs" / "audit" / "statement_row_adjustments.csv"
DEFAULT_PLAN = ROOT / "docs" / "audit" / "manual_adjustment_plan.csv"
NAMESPACE = uuid.UUID("e1f2a3b4-c5d6-5e7f-8a9b-0c1d2e3f4a66")
VERSION = "manual-adjustment-1"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def normalize_status(value: str) -> str:
    key = re.sub(r"[^a-z]", "", text(value).lower())
    mapping = {
        "correctedlabel": "label",
        "correcteddebit": "debit",
        "correctedcredit": "credit",
        "correctedlabelandcredit": "label_credit",
        "irrelvantrow": "irrelevant",
        "irrelevantrow": "irrelevant",
        "rerunocr": "rerun_ocr",
        "emtpycorrectedcredit": "empty_credit",
        "emptycorrectedcredit": "empty_credit",
        "emtpycorrecteddebit": "empty_debit",
        "emptycorrecteddebit": "empty_debit",
    }
    if key not in mapping:
        raise ValueError(f"unknown adjustment_status {value!r}")
    return mapping[key]


def parse_amount(value: str) -> tuple[str, int | None]:
    raw = text(value)
    if not raw:
        return "blank", None
    if raw in {"-", "–", "—", "−"}:
        return "dash", None
    cleaned = re.sub(r"[^0-9,.\-]", "", raw).replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"cannot parse amount {value!r}") from exc
    scaled = amount * 100
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"amount has more than two decimal places: {value!r}")
    return "amount", int(integral)


def statement_actions(review_csv: Path) -> dict[str, str]:
    return {
        text(row["statement_id"]): text(row.get("review_action"))
        for row in read_csv(review_csv)
    }


def build_plan(db: Path, review_csv: Path, rows_csv: Path) -> list[dict[str, Any]]:
    actions = statement_actions(review_csv)
    rows = read_csv(rows_csv)
    plan: list[dict[str, Any]] = []
    for row in rows:
        statement_id = text(row["statement_id"])
        status_raw = text(row["adjustment_status"])
        action = normalize_status(status_raw)
        statement_action = actions.get(statement_id, "")
        base = {
            "statement_id": statement_id,
            "statement_row_id": text(row["statement_row_id"]),
            "status_raw": status_raw,
            "statement_action": statement_action,
            "row_issue": text(row.get("row_issue")),
            "note": text(row.get("reviewer_note")),
        }
        if statement_action == "rerun_ocr" or action == "rerun_ocr":
            plan.append({**base, "field": "", "original": "", "proposed": "", "message": "skipped_rerun_ocr"})
            continue
        if action == "irrelevant":
            plan.extend([
                {**base, "field": "debit_minor", "original": "", "proposed": "0", "message": "irrelevant_row_zero"},
                {**base, "field": "debit_value_state", "original": "", "proposed": "not_applicable", "message": "irrelevant_row_zero"},
                {**base, "field": "credit_minor", "original": "", "proposed": "0", "message": "irrelevant_row_zero"},
                {**base, "field": "credit_value_state", "original": "", "proposed": "not_applicable", "message": "irrelevant_row_zero"},
            ])
            continue
        if action in ("label", "label_credit"):
            label = text(row.get("corrected_label"))
            if not label:
                raise ValueError(f"{statement_id} {row['statement_row_id']}: missing corrected_label")
            plan.append({**base, "field": "label_normalized", "original": text(row.get("label_normalized")), "proposed": label, "message": "label_corrected"})
        if action in ("debit", "empty_debit"):
            if action == "empty_debit":
                state, minor = "blank", 0
            else:
                state, minor = parse_amount(row.get("corrected_debit"))
            plan.append({**base, "field": "debit_minor", "original": text(row.get("debit_minor")), "proposed": str(minor or 0), "message": f"debit_{state}"})
            plan.append({**base, "field": "debit_value_state", "original": text(row.get("debit_value_state")), "proposed": state, "message": f"debit_{state}"})
        if action in ("credit", "empty_credit", "label_credit"):
            if action == "empty_credit":
                state, minor = "blank", 0
            else:
                state, minor = parse_amount(row.get("corrected_credit"))
            plan.append({**base, "field": "credit_minor", "original": text(row.get("credit_minor")), "proposed": str(minor or 0), "message": f"credit_{state}"})
            plan.append({**base, "field": "credit_value_state", "original": text(row.get("credit_value_state")), "proposed": state, "message": f"credit_{state}"})
    return plan


def apply_plan(db: Path, plan: list[dict[str, Any]], reviewer: str, apply: bool) -> dict[str, Any]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    applied = 0
    skipped = 0
    events = 0
    try:
        for item in plan:
            if item["message"] == "skipped_rerun_ocr":
                skipped += 1
                continue
            statement_row_id = item["statement_row_id"]
            # Ensure the row exists and belongs to the statement.
            row = conn.execute(
                "SELECT statement_id FROM statement_rows WHERE statement_row_id = ?",
                (statement_row_id,),
            ).fetchone()
            if row is None or row["statement_id"] != item["statement_id"]:
                raise ValueError(f"statement row {statement_row_id} does not belong to {item['statement_id']}")
            field_name = item["field"]
            proposed = item["proposed"]
            verification_id = stable_id("field_verification", statement_row_id, field_name, VERSION)
            if apply:
                conn.execute(
                    """
                    INSERT INTO field_verifications(
                        field_verification_id, statement_row_id, field_name,
                        original_value_text, verified_value_text, verification_status,
                        verified_by, verified_at, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'verified', ?, ?, ?, ?)
                    ON CONFLICT(statement_row_id, field_name) DO UPDATE SET
                        original_value_text = excluded.original_value_text,
                        verified_value_text = excluded.verified_value_text,
                        verification_status = excluded.verification_status,
                        verified_by = excluded.verified_by,
                        verified_at = excluded.verified_at,
                        reason = excluded.reason
                    """,
                    (
                        verification_id, statement_row_id, field_name,
                        item["original"] or None, proposed, reviewer, now(),
                        item["message"] + ("; " + item["note"] if item["note"] else ""),
                        now(),
                    ),
                )
            applied += 1
            event_id = stable_id("manual_adjustment_event", statement_row_id, field_name, VERSION)
            if not conn.execute(
                "SELECT 1 FROM audit_events WHERE audit_event_id = ?", (event_id,)
            ).fetchone():
                events += 1
                if apply:
                    conn.execute(
                        """
                        INSERT INTO audit_events(
                            audit_event_id, event_type, field_verification_id, statement_id,
                            actor_id, before_json, after_json, details_json, reason, created_at
                        ) VALUES (?, 'field_verification_applied', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id, verification_id, item["statement_id"], reviewer,
                            json.dumps({"field": field_name, "value": item["original"]}, ensure_ascii=False),
                            json.dumps({"field": field_name, "value": proposed}, ensure_ascii=False),
                            json.dumps({
                                "statement_row_id": statement_row_id,
                                "status_raw": item["status_raw"],
                                "message": item["message"],
                                "version": VERSION,
                            }, ensure_ascii=False, sort_keys=True),
                            item["note"] or "Manual row adjustment",
                            now(),
                        ),
                    )
        if apply:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"applied_fields": applied, "skipped_rerun_ocr": skipped, "audit_events": events}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    parser.add_argument("--plan-out", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--reviewer", default="reviewer")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    plan = build_plan(args.db, args.review, args.rows)
    result = apply_plan(args.db, plan, args.reviewer, args.apply)
    args.plan_out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "statement_id", "statement_row_id", "status_raw", "statement_action",
        "row_issue", "field", "original", "proposed", "message", "note",
    ]
    with args.plan_out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(plan)
    result.update({"plan_rows": len(plan), "applied": args.apply, "plan_out": str(args.plan_out)})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
