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


# SQLite's own default busy timeout is 5 seconds. The operational database
# uses delete-journal mode, so a Workbench write can briefly lock readers.
DEFAULT_SQLITE_TIMEOUT_SECONDS = 5.0


def is_v5_analysis_path(path: Path) -> bool:
    """True for the archived V5 database, whatever directory it sits in."""
    return Path(path).name == V5_FILENAME


def friendly_db_error(exc: BaseException) -> str:
    """Turn a SQLite failure into a short read-only message."""
    text = str(exc).lower()
    if "locked" in text:
        return "資料庫正被其他程式鎖住。本檢視只讀，請稍後再打開。"
    if "not a database" in text or "malformed" in text or "disk image" in text:
        return "這不是可讀的 SQLite 檔案。"
    return "無法以唯讀方式開啟資料庫。"


def connect_readonly(
    path: Path | str,
    *,
    timeout: float = DEFAULT_SQLITE_TIMEOUT_SECONDS,
) -> sqlite3.Connection:
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
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        assert_v3_readable(connection)
    except DashboardDataError:
        if connection is not None:
            connection.close()
        raise
    except sqlite3.DatabaseError as exc:
        if connection is not None:
            connection.close()
        raise DashboardDataError(friendly_db_error(exc)) from exc
    return connection


@contextmanager
def open_readonly(
    path: Path | str,
    *,
    timeout: float = DEFAULT_SQLITE_TIMEOUT_SECONDS,
) -> Iterator[sqlite3.Connection]:
    """Open a read-only connection and close it. Does not commit."""
    connection = connect_readonly(path, timeout=timeout)
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
