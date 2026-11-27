"""Database connection helpers with snapshot read-only enforcement."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from workbench.config import SNAPSHOT_DB, V3_DB

STANDARD_ITEM_CLASSIFICATION_COLUMNS = (
    (
        "classification_status",
        "TEXT NOT NULL DEFAULT 'unclassified' "
        "CHECK (classification_status IN ('unclassified', 'classified', 'excluded'))",
    ),
    ("classification_source", "TEXT"),
)


def ensure_standard_item_classification_columns(
    connection: sqlite3.Connection,
) -> None:
    """Add the classification columns to an older v3 database in place.

    The columns are part of the current ``standard_items`` DDL, so a database
    built from ``database/v3`` already has them.  Operational databases built
    before that change need the additive migration; it is safe to call on every
    start because each column is added only when missing.
    """
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='standard_items'"
    ).fetchone()
    if exists is None:
        return
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(standard_items)")
    }
    for name, ddl in STANDARD_ITEM_CLASSIFICATION_COLUMNS:
        if name not in columns:
            connection.execute(f"ALTER TABLE standard_items ADD COLUMN {name} {ddl}")


def is_snapshot(path: Path) -> bool:
    try:
        return Path(path).resolve() == SNAPSHOT_DB.resolve()
    except OSError:
        return False


def connect_v3(path: Path | None = None, *, write: bool = False) -> sqlite3.Connection:
    target = Path(path or V3_DB)
    if write and is_snapshot(target):
        raise PermissionError("MVP snapshot is read-only; write operations must target the operational v3 DB")
    if write:
        connection = sqlite3.connect(target)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    connection = sqlite3.connect(f"file:{target.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def connect_snapshot() -> sqlite3.Connection:
    return connect_v3(SNAPSHOT_DB, write=False)
