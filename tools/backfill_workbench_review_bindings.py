#!/usr/bin/env python3
"""Backfill review-extraction bindings for pre-binding reviewed statements.

Before ``015_review_extraction_binding.sql`` the Workbench did not record which
statement extraction a reviewer signed off.  This one-off migration binds only
statements that:

* are currently ``reviewed``;
* have no active review binding; and
* have exactly one completed extraction (so the reviewed attempt is unambiguous).

Statements with zero or several completed extractions are reported and skipped;
they must be reopened and reviewed explicitly.  The command is a dry run unless
``--apply`` is passed, and every inserted binding gets an audit event.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
NAMESPACE = uuid.UUID("f2a3b4c5-d6e7-5f80-9a1b-2c3d4e5f6a77")
ACTOR = "system:review-binding-backfill"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write bindings and audit events (default is a dry run)",
    )
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    report: dict[str, object] = {
        "database": str(db_path),
        "apply": args.apply,
        "bound": [],
        "skipped": [],
    }
    try:
        candidates = conn.execute(
            """
            SELECT rv.statement_id, rv.reviewed_by, rv.reviewed_at, rv.updated_at
            FROM statement_reviews rv
            WHERE rv.review_status='reviewed'
              AND NOT EXISTS (
                  SELECT 1 FROM statement_review_bindings b
                  WHERE b.statement_id=rv.statement_id AND b.binding_status='active'
              )
            ORDER BY rv.statement_id
            """
        ).fetchall()
        for candidate in candidates:
            statement_id = candidate["statement_id"]
            extractions = conn.execute(
                "SELECT statement_extraction_id FROM statement_extractions "
                "WHERE statement_id=? AND extraction_status='completed' "
                "ORDER BY created_at DESC, statement_extraction_id DESC",
                (statement_id,),
            ).fetchall()
            if len(extractions) != 1:
                report["skipped"].append({
                    "statement_id": statement_id,
                    "reason": f"completed_extractions={len(extractions)}",
                })
                continue
            extraction_id = extractions[0]["statement_extraction_id"]
            binding_id = str(uuid.uuid4())
            bound_at = candidate["reviewed_at"] or candidate["updated_at"] or now()
            bound_by = candidate["reviewed_by"] or ACTOR
            conn.execute(
                "INSERT INTO statement_review_bindings("
                "binding_id, statement_id, statement_extraction_id, binding_status, "
                "bound_by, bound_at, note"
                ") VALUES (?, ?, ?, 'active', ?, ?, ?)",
                (
                    binding_id, statement_id, extraction_id, bound_by, bound_at,
                    "Backfilled from a pre-binding reviewed record",
                ),
            )
            conn.execute(
                "INSERT INTO audit_events("
                "audit_event_id, event_type, statement_id, statement_extraction_id, "
                "actor_id, after_json, details_json, reason, created_at"
                ") VALUES (?, 'workbench_review_binding_backfilled', ?, ?, ?, ?, ?, ?, ?)",
                (
                    stable_id("legacy_review_binding_event", binding_id),
                    statement_id, extraction_id, ACTOR,
                    json.dumps({
                        "binding_id": binding_id,
                        "binding_status": "active",
                        "bound_by": bound_by,
                        "bound_at": bound_at,
                    }, ensure_ascii=False),
                    json.dumps({"source": "migration", "version": "015"}, ensure_ascii=False),
                    "Backfilled review extraction binding for a pre-binding reviewed record",
                    now(),
                ),
            )
            report["bound"].append({
                "statement_id": statement_id,
                "statement_extraction_id": extraction_id,
                "binding_id": binding_id,
            })
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
