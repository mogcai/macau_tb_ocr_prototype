#!/usr/bin/env python3
"""Validate and apply the reviewed P2-B entity reconciliation worksheet.

The input CSV is the human-reviewed entity worksheet.  The script creates v3
``reporting_entities`` and approved aliases only when ``--apply`` is supplied.
Merged legacy entities are preserved with ``registry_status='merged'`` and a
``merged_into_entity_id``; excluded OCR junk is not inserted as an entity.

Default mode is a dry run and writes an applied-plan CSV.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "docs" / "audit" / "reporting_entity_reconciliation.csv"
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_PLAN = ROOT / "docs" / "audit" / "entity_reconciliation_applied.csv"
NAMESPACE = uuid.UUID("b6b2e8e1-6e31-5a73-8f4c-9f6f1c1d2e44")
VERSION = "P2-B-1"

ALLOWED_DECISIONS = {"approve_entity", "approve_merge", "exclude_entity"}
CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", text(value))
    ascii_text = "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", ascii_text)


def language_of(value: str) -> str:
    if CJK_RE.search(value):
        return "zh-Hant"
    if LATIN_RE.search(value):
        return "pt"
    return "unknown"


def entity_type_for(row: dict[str, str]) -> str:
    name = " ".join([
        text(row.get("display_name")),
        text(row.get("canonical_zh")),
        text(row.get("canonical_pt")),
    ])
    if re.search(r"證券|證劵|securities", name, re.I):
        return "non_bank"
    value = text(row.get("entity_type"))
    return value if value in ("bank", "branch", "banking_group", "non_bank") else "bank"


def alias_type_for(row: dict[str, str]) -> str:
    name = text(row.get("display_name"))
    note = text(row.get("reviewer_note"))
    if re.search(r"前稱|國泰君安|historical|former", name + " " + note, re.I):
        return "historical"
    if re.search(r"Shanghal|證劵|ocr", name + " " + note, re.I):
        return "ocr_variant"
    return "historical"


def names_for(row: dict[str, str]) -> list[str]:
    names: list[str] = []
    for field in ("display_name", "canonical_zh", "canonical_pt"):
        value = text(row.get(field))
        if value:
            names.append(value)
    for value in text(row.get("reviewed_entity_names")).split("|"):
        value = value.strip()
        if value:
            names.append(value)
    # Split bilingual combined names into searchable parts.
    expanded: list[str] = []
    for name in names:
        for part in re.split(r"\s*\|\s*", name):
            part = part.strip()
            if part:
                expanded.append(part)
    return list(dict.fromkeys(expanded))


def validate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(rows) != 48:
        raise ValueError(f"expected 48 entities, got {len(rows)}")
    ids = [text(row.get("entity_id")) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate entity_id in worksheet")
    active_ids = {
        text(row["entity_id"])
        for row in rows
        if text(row.get("reviewer_decision")) == "approve_entity"
    }
    for row in rows:
        decision = text(row.get("reviewer_decision"))
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"unknown reviewer_decision {decision!r}")
        if decision == "approve_merge":
            survivor = text(row.get("reviewer_survivor_entity_id"))
            if not survivor:
                raise ValueError("approve_merge requires reviewer_survivor_entity_id")
            if survivor not in active_ids:
                raise ValueError(f"merge survivor {survivor} is not an approved entity")
            proposed = text(row.get("proposed_survivor_entity_id"))
            if proposed and proposed != survivor:
                raise ValueError(
                    f"entity {row['entity_id']} survivor {survivor} differs from proposal {proposed}"
                )
        if decision == "exclude_entity" and not text(row.get("reviewer_note")):
            raise ValueError("exclude_entity requires reviewer_note")
    return rows


def entity_values(row: dict[str, str], *, status: str, merged_into: str | None) -> dict[str, Any]:
    return {
        "entity_key": text(row.get("entity_key")) or f"legacy:{row['entity_id']}",
        "entity_type": entity_type_for(row),
        "canonical_zh": text(row.get("canonical_zh")) or None,
        "canonical_pt": text(row.get("canonical_pt")) or None,
        "canonical_en": None,
        "parent_entity_id": None,
        "registry_status": status,
        "merged_into_entity_id": merged_into,
        "merged_at": now() if merged_into else None,
        "notes": "; ".join(part for part in (
            f"P2-B decision={text(row.get('reviewer_decision'))}",
            f"legacy_bank_id={text(row.get('entity_id'))}",
            f"legacy_key={text(row.get('entity_key'))}",
            f"note={text(row.get('reviewer_note'))}",
        ) if part),
        "created_at": now(),
    }


def ensure_entity(conn: sqlite3.Connection, row: dict[str, str], *, status: str,
                  merged_into: str | None) -> str:
    entity_key = text(row.get("entity_key")) or f"legacy:{row['entity_id']}"
    existing = conn.execute(
        "SELECT entity_id FROM reporting_entities WHERE entity_key = ?", (entity_key,)
    ).fetchone()
    if existing:
        entity_id = str(existing[0])
        values = entity_values(row, status=status, merged_into=merged_into)
        conn.execute(
            """
            UPDATE reporting_entities
            SET entity_type=?, canonical_zh=?, canonical_pt=?, registry_status=?,
                merged_into_entity_id=?, merged_at=?, notes=?
            WHERE entity_id=?
            """,
            (
                values["entity_type"], values["canonical_zh"], values["canonical_pt"],
                values["registry_status"], values["merged_into_entity_id"],
                values["merged_at"], values["notes"], entity_id,
            ),
        )
        return entity_id
    entity_id = stable_id("entity", entity_key)
    values = entity_values(row, status=status, merged_into=merged_into)
    conn.execute(
        """
        INSERT INTO reporting_entities(
            entity_id, entity_key, entity_type, canonical_zh, canonical_pt,
            canonical_en, parent_entity_id, registry_status,
            merged_into_entity_id, merged_at, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id, values["entity_key"], values["entity_type"],
            values["canonical_zh"], values["canonical_pt"], values["canonical_en"],
            values["parent_entity_id"], values["registry_status"],
            values["merged_into_entity_id"], values["merged_at"],
            values["notes"], values["created_at"],
        ),
    )
    return entity_id


def add_alias(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    raw_name: str,
    alias_type: str,
    source_reference: str,
    notes: str,
) -> bool:
    raw = text(raw_name)
    normalized = normalize_name(raw)
    if not raw or not normalized:
        return False
    language = language_of(raw)
    alias_id = stable_id("entity_alias", entity_id, normalized, language)
    try:
        conn.execute(
            """
            INSERT INTO reporting_entity_aliases(
                entity_alias_id, entity_id, raw_name, normalized_name,
                language, alias_type, status, source_reference, notes
            ) VALUES (?, ?, ?, ?, ?, ?, 'approved', ?, ?)
            ON CONFLICT(entity_id, normalized_name, language) DO UPDATE SET
                raw_name = excluded.raw_name,
                alias_type = excluded.alias_type,
                status = excluded.status,
                source_reference = excluded.source_reference,
                notes = excluded.notes
            """,
            (alias_id, entity_id, raw, normalized, language, alias_type, source_reference, notes),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def audit_id(row: dict[str, str]) -> str:
    return stable_id(
        "entity_decision",
        text(row.get("entity_id")),
        text(row.get("reviewer_decision")),
        text(row.get("reviewer_survivor_entity_id")),
        VERSION,
    )


def apply_rows(db: Path, rows: list[dict[str, str]], reviewer: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    plan: list[dict[str, Any]] = []
    try:
        # Create approved entities first so merge targets exist.
        approved_rows = [row for row in rows if text(row["reviewer_decision"]) == "approve_entity"]
        merge_rows = [row for row in rows if text(row["reviewer_decision"]) == "approve_merge"]
        excluded_rows = [row for row in rows if text(row["reviewer_decision"]) == "exclude_entity"]

        id_by_legacy: dict[str, str] = {}
        for row in approved_rows:
            entity_id = ensure_entity(conn, row, status="approved", merged_into=None)
            id_by_legacy[text(row["entity_id"])] = entity_id
            alias_count = 0
            for name in names_for(row):
                if add_alias(
                    conn,
                    entity_id=entity_id,
                    raw_name=name,
                    alias_type="official",
                    source_reference=f"P2-B approved; legacy_bank_id={text(row['entity_id'])}",
                    notes=text(row.get("reviewer_note")),
                ):
                    alias_count += 1
            plan.append({
                "legacy_entity_id": row["entity_id"],
                "v3_entity_id": entity_id,
                "decision": "approve_entity",
                "v3_entity_status": "approved",
                "v3_survivor_entity_id": "",
                "alias_count": alias_count,
            })

        for row in merge_rows:
            survivor_legacy = text(row["reviewer_survivor_entity_id"])
            survivor_id = id_by_legacy.get(survivor_legacy)
            if not survivor_id:
                raise ValueError(f"survivor {survivor_legacy} was not created")
            entity_id = ensure_entity(conn, row, status="merged", merged_into=survivor_id)
            id_by_legacy[text(row["entity_id"])] = entity_id
            alias_count = 0
            for name in names_for(row):
                if add_alias(
                    conn,
                    entity_id=survivor_id,
                    raw_name=name,
                    alias_type=alias_type_for(row),
                    source_reference=f"P2-B merged; legacy_bank_id={text(row['entity_id'])}",
                    notes=text(row.get("reviewer_note")),
                ):
                    alias_count += 1
            plan.append({
                "legacy_entity_id": row["entity_id"],
                "v3_entity_id": entity_id,
                "decision": "approve_merge",
                "v3_entity_status": "merged",
                "v3_survivor_entity_id": survivor_id,
                "alias_count": alias_count,
            })

        for row in excluded_rows:
            plan.append({
                "legacy_entity_id": row["entity_id"],
                "v3_entity_id": "",
                "decision": "exclude_entity",
                "v3_entity_status": "excluded",
                "v3_survivor_entity_id": "",
                "alias_count": 0,
            })

        for row in rows:
            legacy_id = text(row["entity_id"])
            decision = text(row["reviewer_decision"])
            entity_id = id_by_legacy.get(legacy_id)
            event_id = audit_id(row)
            exists = conn.execute(
                "SELECT 1 FROM audit_events WHERE audit_event_id = ?", (event_id,)
            ).fetchone()
            if exists:
                continue
            event_type = {
                "approve_entity": "entity_approved",
                "approve_merge": "entity_merged",
                "exclude_entity": "entity_excluded",
            }[decision]
            details = {
                "legacy_entity_id": legacy_id,
                "legacy_entity_key": text(row.get("entity_key")),
                "display_name": text(row.get("display_name")),
                "decision": decision,
                "survivor_legacy_entity_id": text(row.get("reviewer_survivor_entity_id")),
                "survivor_v3_entity_id": id_by_legacy.get(text(row.get("reviewer_survivor_entity_id")), ""),
                "reviewer_note": text(row.get("reviewer_note")),
                "version": VERSION,
            }
            conn.execute(
                """
                INSERT INTO audit_events(
                    audit_event_id, event_type, entity_id, actor_id,
                    after_json, details_json, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, event_type, entity_id, reviewer,
                    json.dumps({"v3_entity_id": entity_id}, ensure_ascii=False),
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    text(row.get("reviewer_note")),
                    now(),
                ),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--plan-out", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--reviewer", default="reviewer")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with args.csv.open(newline="", encoding="utf-8-sig") as handle:
        rows = validate_rows([dict(row) for row in csv.DictReader(handle)])

    plan = apply_rows(args.db, rows, args.reviewer) if args.apply else []
    if not args.apply:
        # Dry-run plan with deterministic IDs, without touching the DB.
        active = {text(row["entity_id"]) for row in rows if text(row["reviewer_decision"]) == "approve_entity"}
        for row in rows:
            decision = text(row["reviewer_decision"])
            legacy_id = text(row["entity_id"])
            entity_id = stable_id("entity", text(row.get("entity_key")) or f"legacy:{legacy_id}")
            survivor_legacy = text(row.get("reviewer_survivor_entity_id"))
            plan.append({
                "legacy_entity_id": legacy_id,
                "v3_entity_id": entity_id if decision != "exclude_entity" else "",
                "decision": decision,
                "v3_entity_status": {
                    "approve_entity": "approved",
                    "approve_merge": "merged",
                    "exclude_entity": "excluded",
                }[decision],
                "v3_survivor_entity_id": (
                    stable_id("entity", text(next(
                        (item.get("entity_key") for item in rows
                         if text(item.get("entity_id")) == survivor_legacy), ""
                    )) or f"legacy:{survivor_legacy}")
                    if decision == "approve_merge" else ""
                ),
                "alias_count": 0,
            })

    args.plan_out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "legacy_entity_id", "v3_entity_id", "decision", "v3_entity_status",
        "v3_survivor_entity_id", "alias_count",
    ]
    with args.plan_out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(plan)
    print(json.dumps({
        "rows": len(rows),
        "approved": sum(1 for row in rows if row["reviewer_decision"] == "approve_entity"),
        "merged": sum(1 for row in rows if row["reviewer_decision"] == "approve_merge"),
        "excluded": sum(1 for row in rows if row["reviewer_decision"] == "exclude_entity"),
        "applied": bool(args.apply),
        "plan_out": str(args.plan_out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
