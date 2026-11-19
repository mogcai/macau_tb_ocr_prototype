#!/usr/bin/env python3
"""Apply reviewed statement adoption decisions.

Only ``reviewer_decision = adopt`` rows are adopted.  ``needs_further_review``,
``hold`` and ``reject`` remain unchanged.  Adoption sets the statement's
``adopted_statement_extraction_id`` and writes an append-only audit event.

Default mode is a dry run; use ``--apply`` to write.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "docs" / "audit" / "statement_validation_decisions.csv"
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_PLAN = ROOT / "docs" / "audit" / "statement_adoption_applied.csv"
NAMESPACE = uuid.UUID("c4e5d6f7-8a9b-5c0d-9e1f-2a3b4c5d6e77")
VERSION = "statement-adoption-1"
ALLOWED = {"adopt", "needs_further_review", "hold", "reject"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def validate(rows: list[dict[str, str]]) -> None:
    if len(rows) != 113:
        raise ValueError(f"expected 113 statements, got {len(rows)}")
    ids = [text(row.get("statement_id")) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate statement_id")
    for row in rows:
        decision = text(row.get("reviewer_decision"))
        if decision not in ALLOWED:
            raise ValueError(f"unknown reviewer_decision {decision!r}")
        if decision == "adopt" and not text(row.get("statement_extraction_id")):
            raise ValueError(f"adopt row {row['statement_id']} has no extraction id")


def apply_rows(db: Path, rows: list[dict[str, str]], reviewer: str, apply: bool) -> dict[str, Any]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    adopted = 0
    already = 0
    held = 0
    rejected = 0
    events = 0
    plan: list[dict[str, Any]] = []
    try:
        for row in rows:
            statement_id = text(row["statement_id"])
            decision = text(row["reviewer_decision"])
            if decision != "adopt":
                held += decision == "needs_further_review" or decision == "hold"
                rejected += decision == "reject"
                plan.append({
                    "statement_id": statement_id,
                    "entity_name": text(row.get("entity_name")),
                    "decision": decision,
                    "adoption_status": "held" if decision != "reject" else "rejected",
                    "statement_extraction_id": "",
                    "current_rows": "",
                })
                continue

            extraction_id = text(row["statement_extraction_id"])
            statement = conn.execute(
                "SELECT adopted_statement_extraction_id, statement_status "
                "FROM statements WHERE statement_id = ?", (statement_id,)
            ).fetchone()
            if statement is None:
                raise ValueError(f"missing statement {statement_id}")
            extraction = conn.execute(
                "SELECT statement_id, extraction_status FROM statement_extractions "
                "WHERE statement_extraction_id = ?", (extraction_id,)
            ).fetchone()
            if extraction is None or extraction["statement_id"] != statement_id:
                raise ValueError(f"extraction {extraction_id} does not belong to {statement_id}")
            if extraction["extraction_status"] != "completed":
                raise ValueError(f"extraction {extraction_id} is not completed")
            if statement["adopted_statement_extraction_id"] == extraction_id:
                already += 1
                status = "already_adopted"
            elif statement["adopted_statement_extraction_id"] is not None:
                raise ValueError(
                    f"statement {statement_id} already adopted {statement['adopted_statement_extraction_id']}"
                )
            else:
                adopted += 1
                status = "adopted"
                if apply:
                    conn.execute(
                        "UPDATE statements SET adopted_statement_extraction_id=?, "
                        "statement_status='active' WHERE statement_id=?",
                        (extraction_id, statement_id),
                    )
            event_id = stable_id("statement_adopted", statement_id, extraction_id, VERSION)
            if not conn.execute(
                "SELECT 1 FROM audit_events WHERE audit_event_id = ?", (event_id,)
            ).fetchone():
                events += 1
                if apply:
                    conn.execute(
                        """
                        INSERT INTO audit_events(
                            audit_event_id, event_type, statement_id, actor_id,
                            before_json, after_json, details_json, reason, created_at
                        ) VALUES (?, 'statement_adopted', ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id,
                            statement_id,
                            reviewer,
                            json.dumps({
                                "adopted_statement_extraction_id": statement["adopted_statement_extraction_id"],
                                "statement_status": statement["statement_status"],
                            }, ensure_ascii=False),
                            json.dumps({
                                "adopted_statement_extraction_id": extraction_id,
                                "statement_status": "active",
                            }, ensure_ascii=False),
                            json.dumps({
                                "review_note": text(row.get("reviewer_note")),
                                "version": VERSION,
                            }, ensure_ascii=False),
                            text(row.get("reviewer_note")) or "Statement adoption approved",
                            now(),
                        ),
                    )
            current_rows = conn.execute(
                "SELECT COUNT(*) FROM statement_rows WHERE statement_extraction_id = ?",
                (extraction_id,),
            ).fetchone()[0]
            plan.append({
                "statement_id": statement_id,
                "entity_name": text(row.get("entity_name")),
                "decision": decision,
                "adoption_status": status,
                "statement_extraction_id": extraction_id,
                "current_rows": current_rows,
            })
        if apply:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "statements": len(rows),
        "adopted_now": adopted,
        "already_adopted": already,
        "held": held,
        "rejected": rejected,
        "audit_events_added": events,
        "applied": apply,
        "plan": plan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--plan-out", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--reviewer", default="reviewer")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    rows = read_csv(args.csv)
    validate(rows)
    result = apply_rows(args.db, rows, args.reviewer, args.apply)
    plan = result.pop("plan")
    args.plan_out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "statement_id", "entity_name", "decision", "adoption_status",
        "statement_extraction_id", "current_rows",
    ]
    with args.plan_out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(plan)
    result["plan_out"] = str(args.plan_out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
