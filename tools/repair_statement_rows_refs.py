#!/usr/bin/env python3
"""Repair v3 databases damaged by the 2026-11-22 statement_rows FK migration.

That migration rebuilt ``statement_rows`` with ``ALTER TABLE ... RENAME TO
statement_rows__pre_discard_fk``.  SQLite rewrites every incoming reference
when a parent table is renamed, so the rename silently retargeted:

* the foreign keys of ``statement_row_mappings``, ``field_verifications``,
  ``statement_row_adjustments``, ``validation_findings``, ``review_tasks``
  and ``audit_events``;
* four triggers (mapping / adjustment uniqueness);
* ``v_current_statement_rows`` and the other statement-row views.

The temporary table was then dropped, leaving those objects pointing at a
table that no longer exists.  Reads through the affected views fail with
``no such table: main.statement_rows__pre_discard_fk`` and every write that
fires an affected trigger fails the same way.

This tool is a one-off repair: it rebuilds each affected child table with the
correct ``REFERENCES statement_rows(...)`` clause, rewrites the views and
triggers, keeps every row, index and trigger, and refuses to commit unless
``PRAGMA foreign_key_check`` is clean.

Examples::

    # show what would be repaired (default, no writes)
    python tools/repair_statement_rows_refs.py --db data/macau_balancete_v3.sqlite

    # back up the DB and repair it
    python tools/repair_statement_rows_refs.py --db data/macau_balancete_v3.sqlite --apply

The repair is idempotent: a clean database reports ``"status": "clean"``.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DROPPED_TABLE = "statement_rows__pre_discard_fk"
PARENT_TABLE = "statement_rows"
_CREATE_TABLE_RE = re.compile(
    r"(?P<prefix>CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)"
    r'(?P<quoted>"[^"]+"|`[^`]+`|\[[^\]]+\]|\w+)',
    re.IGNORECASE | re.DOTALL,
)


class RepairError(RuntimeError):
    """The database cannot be repaired safely."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _table_name(token: str) -> str:
    if token.startswith('"') or token.startswith("`"):
        return token[1:-1]
    if token.startswith("["):
        return token[1:-1]
    return token


def _rename_create_table(sql: str, old: str, new: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if _table_name(match.group("quoted")) != old:
            return match.group(0)
        return f'{match.group("prefix")}"{new}"'

    renamed, count = _CREATE_TABLE_RE.subn(replace, sql, count=1)
    if count != 1 or f'"{new}"' not in renamed:
        raise RepairError(f"could not rewrite CREATE TABLE for {old}")
    return renamed


def dangling_objects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND sql LIKE ? ORDER BY type, name",
        (f"%{DROPPED_TABLE}%",),
    ).fetchall()
    return [dict(row) for row in rows]


def _objects(conn: sqlite3.Connection, kind: str) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = ? AND sql IS NOT NULL ORDER BY name",
        (kind,),
    ).fetchall()
    return [{"name": row["name"], "sql": row["sql"]} for row in rows]


def _indexes(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
        "AND sql IS NOT NULL ORDER BY name",
        (table,),
    ).fetchall()
    return [row["sql"] for row in rows]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def repair(conn: sqlite3.Connection, *, apply: bool) -> dict[str, Any]:
    """Repair dangling ``statement_rows`` references, optionally writing."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (PARENT_TABLE,)
    ).fetchone():
        raise RepairError(f"table {PARENT_TABLE} is missing; restore a backup instead")

    dangling = dangling_objects(conn)
    tables = [item for item in dangling if item["type"] == "table"]
    summary: dict[str, Any] = {
        "status": "dirty" if dangling else "clean",
        "dropped_table": DROPPED_TABLE,
        "dangling_tables": [item["name"] for item in tables],
        "dangling_views": [i["name"] for i in dangling if i["type"] == "view"],
        "dangling_triggers": [i["name"] for i in dangling if i["type"] == "trigger"],
        "repaired": False,
    }
    if not dangling:
        summary["foreign_key_violations"] = len(
            conn.execute("PRAGMA foreign_key_check").fetchall()
        )
        return summary
    if not apply:
        return summary

    views = _objects(conn, "view")
    triggers = _objects(conn, "trigger")
    counts = {item["name"]: _count(conn, item["name"]) for item in tables}
    index_sql = {item["name"]: _indexes(conn, item["name"]) for item in tables}

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Views and triggers can reference the tables being rebuilt, so drop
        # every one of them inside the transaction and recreate them after.
        for item in triggers:
            conn.execute(f'DROP TRIGGER "{item["name"]}"')
        for item in views:
            conn.execute(f'DROP VIEW "{item["name"]}"')

        for item in tables:
            name = item["name"]
            new_name = f"{name}__refs_repair"
            sql = item["sql"].replace(DROPPED_TABLE, PARENT_TABLE)
            sql = _rename_create_table(sql, name, new_name)
            conn.execute(sql)
            conn.execute(f'INSERT INTO "{new_name}" SELECT * FROM "{name}"')
            conn.execute(f'DROP TABLE "{name}"')
            conn.execute(f'ALTER TABLE "{new_name}" RENAME TO "{name}"')
            for statement in index_sql[name]:
                conn.execute(statement)

        for item in triggers:
            conn.execute(item["sql"].replace(DROPPED_TABLE, PARENT_TABLE))
        for item in views:
            conn.execute(item["sql"].replace(DROPPED_TABLE, PARENT_TABLE))

        after = {name: _count(conn, name) for name in counts}
        if after != counts:
            raise RepairError(f"row counts changed during repair: {counts} -> {after}")

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            sample = [tuple(row) for row in violations[:5]]
            raise RepairError(
                f"foreign_key_check still reports {len(violations)} violation(s): {sample}"
            )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    remaining = dangling_objects(conn)
    if remaining:
        raise RepairError(
            "repair committed but references remain: "
            + ", ".join(f"{item['type']}:{item['name']}" for item in remaining)
        )
    summary.update(
        {
            "status": "repaired",
            "repaired": True,
            "repaired_tables": [item["name"] for item in tables],
            "recreated_views": len(views),
            "recreated_triggers": len(triggers),
            "foreign_key_violations": len(
                conn.execute("PRAGMA foreign_key_check").fetchall()
            ),
        }
    )
    return summary


def _backup(path: Path) -> Path:
    target = path.with_name(f"{path.name}.bak-{_now()}")
    shutil.copyfile(path, target)
    check = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        result = check.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise RepairError(f"backup failed integrity check: {target} ({result})")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/macau_balancete_v3.sqlite"),
        help="v3 SQLite database to inspect or repair",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the repair (default is a read-only inspection)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="skip the automatic <db>.bak-<timestamp> copy",
    )
    args = parser.parse_args()

    path = args.db.expanduser().resolve()
    if not path.is_file():
        parser.error(f"database not found: {path}")

    if not args.apply:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            print(json.dumps(repair(conn, apply=False), indent=2))
        finally:
            conn.close()
        return 0

    backup = None if args.no_backup else _backup(path)
    conn = sqlite3.connect(path)
    try:
        summary = repair(conn, apply=True)
    finally:
        conn.close()

    check = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        violations = len(check.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        check.close()
    summary["integrity_check"] = integrity
    summary["foreign_key_violations"] = violations
    summary["backup"] = str(backup) if backup else None
    summary["database"] = str(path)
    print(json.dumps(summary, indent=2))
    if integrity != "ok" or violations:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
