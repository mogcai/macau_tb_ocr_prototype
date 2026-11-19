#!/usr/bin/env python3
"""Validate and apply the reviewed P2-A page classification worksheet.

The input CSV is the human-reviewed worksheet.  The script keeps the original
proposal columns untouched, derives a ``reviewed_entity_name`` for P2-B, and
only writes ``document_pages`` when ``--apply`` is supplied.

Default mode is a dry run: it writes an enriched reviewed CSV and prints a
summary, but does not update the database.

Usage::

    python tools/apply_page_classification_decisions.py \
        --csv docs/audit/page_classification_decisions.csv \
        --db data/macau_balancete_v3.sqlite

    python tools/apply_page_classification_decisions.py \
        --csv docs/audit/page_classification_decisions.csv \
        --db data/macau_balancete_v3.sqlite --apply --reviewer tony
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "docs" / "audit" / "page_classification_decisions.csv"
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_REVIEWED = ROOT / "docs" / "audit" / "page_classification_reviewed.csv"
NAMESPACE = uuid.UUID("e9b6f1f4-0d1b-5a8b-8f2a-5c0f9d7a3b21")

ALLOWED_DECISIONS = {
    "approve_proposed",
    "change_proposed_name",
    "reject_page",
}
ALLOWED_ROLES = {
    "financial_statement",
    "audit_report",
    "shareholder_list",
    "cover",
    "contents",
    "other",
    "unknown",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def corrected_entity_name(note: str) -> str:
    """Extract a corrected entity name from a reviewer note.

    Most notes are the corrected name.  A few explain the correction, e.g.
    ``應該用郵政儲金局，OCR識別錯``.  In that case take the text after ``應該用``.
    """
    value = text(note)
    match = re.search(r"應該用\s*([^，,。;；]+)", value)
    if match:
        return match.group(1).strip()
    return value


def resolve_decision(row: dict[str, str]) -> dict[str, str]:
    decision = text(row.get("reviewer_decision"))
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"unknown reviewer_decision {decision!r}")
    proposed_role = text(row.get("proposed_v3_page_role"))
    if proposed_role not in ALLOWED_ROLES:
        raise ValueError(f"unknown proposed_v3_page_role {proposed_role!r}")
    note = text(row.get("reviewer_note"))
    if decision in ("change_proposed_name", "reject_page") and not note:
        raise ValueError("reviewer_note is required for this decision")

    if decision == "approve_proposed":
        return {
            "reviewed_entity_name": text(row.get("proposed_entity_name")),
            "applied_page_role": proposed_role,
            "applied_classification_status": "approved",
        }
    if decision == "change_proposed_name":
        if proposed_role != "financial_statement":
            raise ValueError("change_proposed_name only applies to financial_statement pages")
        return {
            "reviewed_entity_name": corrected_entity_name(note),
            "applied_page_role": proposed_role,
            "applied_classification_status": "approved",
        }
    # reject_page: never let a rejected financial candidate remain financial.
    applied_role = "other" if proposed_role == "financial_statement" else proposed_role
    return {
        "reviewed_entity_name": "",
        "applied_page_role": applied_role,
        "applied_classification_status": "rejected",
    }


def validate_rows(rows: list[dict[str, str]], db_path: Path) -> list[dict[str, str]]:
    if len(rows) != 118:
        raise ValueError(f"expected 118 pages, got {len(rows)}")
    ids = [text(row.get("document_page_id")) for row in rows]
    if any(not value for value in ids):
        raise ValueError("document_page_id must not be empty")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate document_page_id in worksheet")

    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        existing = {
            row[0]
            for row in conn.execute("SELECT document_page_id FROM document_pages")
        }
    finally:
        conn.close()
    missing = set(ids) - existing
    if missing:
        raise ValueError(f"worksheet contains unknown document_page_id: {sorted(missing)[:3]}")

    reviewed: list[dict[str, str]] = []
    for row in rows:
        resolved = resolve_decision(row)
        reviewed.append({**row, **resolved})
    return reviewed


def write_reviewed(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(rows[0].keys())
    for extra in ("reviewed_entity_name", "applied_page_role", "applied_classification_status"):
        if extra not in fields:
            fields.append(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def audit_event_id(row: dict[str, str], version: str) -> str:
    return str(uuid.uuid5(
        NAMESPACE,
        "|".join([
            text(row["document_page_id"]),
            text(row["reviewer_decision"]),
            text(row["reviewed_entity_name"]),
            text(row["applied_page_role"]),
            text(row["applied_classification_status"]),
            version,
        ]),
    ))


def apply_rows(db_path: Path, rows: list[dict[str, str]], reviewer: str) -> dict[str, Any]:
    version = "P2-A-1"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    updated = 0
    unchanged = 0
    events = 0
    try:
        for row in rows:
            page_id = text(row["document_page_id"])
            old = conn.execute(
                "SELECT page_role, classification_status, classification_source, notes "
                "FROM document_pages WHERE document_page_id = ?",
                (page_id,),
            ).fetchone()
            if old is None:
                raise ValueError(f"missing document page {page_id}")
            new_role = text(row["applied_page_role"])
            new_status = text(row["applied_classification_status"])
            base_note = re.split(r"(?:^|;\s*)P2-A decision=", text(old[3]), maxsplit=1)[0].strip()
            note_parts = [
                base_note,
                f"P2-A decision={text(row['reviewer_decision'])}",
                f"note={text(row['reviewer_note'])}",
                f"proposed_role={text(row['proposed_page_role'])}",
            ]
            new_notes = "; ".join(part for part in note_parts if part)
            changed = (
                old[0] != new_role
                or old[1] != new_status
                or old[2] != "human"
                or text(old[3]) != new_notes
            )
            if changed:
                conn.execute(
                    """
                    UPDATE document_pages
                    SET page_role = ?,
                        classification_status = ?,
                        classification_source = 'human',
                        classified_by = ?,
                        classified_at = ?,
                        classification_version = ?,
                        notes = ?
                    WHERE document_page_id = ?
                    """,
                    (new_role, new_status, reviewer, now(), version, new_notes, page_id),
                )
                updated += 1
            else:
                unchanged += 1

            event_id = audit_event_id(row, version)
            exists = conn.execute(
                "SELECT 1 FROM audit_events WHERE audit_event_id = ?", (event_id,)
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO audit_events(
                        audit_event_id, event_type, document_page_id, actor_id,
                        before_json, after_json, details_json, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        f"page_classification_{new_status}",
                        page_id,
                        reviewer,
                        json.dumps(
                            {"page_role": old[0], "classification_status": old[1]},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "page_role": new_role,
                                "classification_status": new_status,
                                "reviewed_entity_name": text(row["reviewed_entity_name"]),
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "decision": text(row["reviewer_decision"]),
                                "review_note": text(row["reviewer_note"]),
                                "proposed_page_role": text(row["proposed_page_role"]),
                                "proposed_entity_name": text(row["proposed_entity_name"]),
                                "classification_version": version,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        text(row["reviewer_note"]),
                        now(),
                    ),
                )
                events += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"updated": updated, "unchanged": unchanged, "audit_events": events}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--reviewed-out", type=Path, default=DEFAULT_REVIEWED)
    parser.add_argument("--reviewer", default="reviewer")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    rows = read_csv(args.csv)
    reviewed = validate_rows(rows, args.db)
    write_reviewed(args.reviewed_out, reviewed)
    summary = {
        "pages": len(reviewed),
        "approved": sum(1 for row in reviewed if row["applied_classification_status"] == "approved"),
        "rejected": sum(1 for row in reviewed if row["applied_classification_status"] == "rejected"),
        "name_changes": sum(1 for row in reviewed if row["reviewer_decision"] == "change_proposed_name"),
        "reviewed_out": str(args.reviewed_out),
        "applied": False,
    }
    if args.apply:
        summary.update(apply_rows(args.db, reviewed, args.reviewer))
        summary["applied"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
