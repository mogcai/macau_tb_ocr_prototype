#!/usr/bin/env python3
"""Confirm statement-level currency and unit-scale policy.

This is a controlled metadata update: it sets ``currency`` and ``unit_scale``
on statements and records an append-only audit event.  It does not touch rows,
amounts or publication status.

Default mode is a dry run; use ``--apply`` to write.

Example::

    python tools/set_statement_currency_policy.py \
        --db data/macau_balancete_v3.sqlite \
        --currency MOP --unit-scale 1 --reviewer tony --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
NAMESPACE = uuid.UUID("a7c3d2e1-4f5b-5c6d-8e9f-0a1b2c3d4e55")
VERSION = "currency-policy-1"
ALLOWED_UNIT_SCALES = (1, 1000, 1000000)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def apply_policy(
    db: Path,
    *,
    currency: str,
    unit_scale: int,
    reviewer: str,
    apply: bool,
) -> dict[str, Any]:
    currency = currency.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a three-letter code")
    if unit_scale not in ALLOWED_UNIT_SCALES:
        raise ValueError(f"unit_scale must be one of {ALLOWED_UNIT_SCALES}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    changed = 0
    audit_added = 0
    try:
        statements = conn.execute(
            "SELECT statement_id, currency, currency_source, unit_scale, "
            "unit_scale_source FROM statements ORDER BY statement_id"
        ).fetchall()
        for statement in statements:
            needs_update = (
                statement["currency"] != currency
                or statement["currency_source"] != "manual"
                or statement["unit_scale"] != unit_scale
                or statement["unit_scale_source"] != "manual"
            )
            if needs_update:
                changed += 1
                if apply:
                    conn.execute(
                        "UPDATE statements SET currency=?, currency_source='manual', "
                        "unit_scale=?, unit_scale_source='manual' WHERE statement_id=?",
                        (currency, unit_scale, statement["statement_id"]),
                    )
            event_id = stable_id(
                "statement_currency_policy", statement["statement_id"],
                currency, unit_scale, VERSION,
            )
            if not conn.execute(
                "SELECT 1 FROM audit_events WHERE audit_event_id = ?", (event_id,)
            ).fetchone():
                audit_added += 1
                if apply:
                    conn.execute(
                        """
                        INSERT INTO audit_events(
                            audit_event_id, event_type, statement_id, actor_id,
                            before_json, after_json, details_json, reason, created_at
                        ) VALUES (?, 'statement_currency_unit_confirmed', ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id,
                            statement["statement_id"],
                            reviewer,
                            json.dumps({
                                "currency": statement["currency"],
                                "currency_source": statement["currency_source"],
                                "unit_scale": statement["unit_scale"],
                                "unit_scale_source": statement["unit_scale_source"],
                            }, ensure_ascii=False),
                            json.dumps({
                                "currency": currency,
                                "currency_source": "manual",
                                "unit_scale": unit_scale,
                                "unit_scale_source": "manual",
                            }, ensure_ascii=False),
                            json.dumps({
                                "policy": "all statements use MOP and unit_scale=1",
                                "version": VERSION,
                            }, ensure_ascii=False),
                            "Regulatory currency/unit policy confirmed by project owner",
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
    return {
        "statements": len(statements),
        "would_update": changed,
        "would_add_audit_events": audit_added,
        "currency": currency,
        "unit_scale": unit_scale,
        "applied": apply,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--currency", default="MOP")
    parser.add_argument("--unit-scale", type=int, default=1)
    parser.add_argument("--reviewer", default="reviewer")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = apply_policy(
        args.db,
        currency=args.currency,
        unit_scale=args.unit_scale,
        reviewer=args.reviewer,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
