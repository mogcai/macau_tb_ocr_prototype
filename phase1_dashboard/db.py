"""Read-only V3 connections.

The operational database stays on Tony's machine. This module never writes,
never migrates, and refuses the archived V5 file.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

V5_FILENAME = "macau_balancete.sqlite"

REQUIRED_OBJECTS: frozenset[str] = frozenset(
    {
        "reporting_entities",
        "statements",
        "statement_reviews",
        "statement_rows",
        "statement_row_mappings",
        "statement_row_adjustments",
        "standard_items",
        "extracted_rows",
        "v_effective_statement_rows",
    }
)


class DashboardDataError(RuntimeError):
    """The file cannot be used as the phase-1 V3 source."""


def is_v5_analysis_path(path: Path) -> bool:
    """True for the archived V5 database, whatever directory it sits in."""
    return Path(path).name == V5_FILENAME


def connect_readonly(path: Path | str) -> sqlite3.Connection:
    """Open a V3 SQLite file with mode=ro and query_only."""
    target = Path(path).expanduser()
    if is_v5_analysis_path(target):
        raise DashboardDataError(
            "拒絕開啟 macau_balancete.sqlite。V5／archive 資料庫不是分析來源。"
        )
    if not target.is_file():
        raise DashboardDataError(f"找不到資料庫檔案：{target}")
    resolved = target.resolve()
    uri = "file:" + quote(resolved.as_posix(), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        assert_v3_readable(connection)
    except Exception:
        connection.close()
        raise
    return connection


@contextmanager
def open_readonly(path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open a read-only connection and close it. Does not commit."""
    connection = connect_readonly(path)
    try:
        yield connection
    finally:
        connection.close()


def assert_v3_readable(connection: sqlite3.Connection) -> None:
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    if "line_items" in names and "reporting_entities" not in names:
        raise DashboardDataError("這是 V5 結構，不可用作分析資料。")
    missing = sorted(REQUIRED_OBJECTS - names)
    if missing:
        raise DashboardDataError(
            "這不是可用的 V3 資料庫，缺少：" + ", ".join(missing)
        )
