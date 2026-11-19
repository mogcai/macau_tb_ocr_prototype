"""Database connection helpers with snapshot read-only enforcement."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from workbench.config import SNAPSHOT_DB, V3_DB


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
