#!/usr/bin/env python3
"""Create a new v3 SQLite database from the checked-in DDL.

This tool never writes to the v5 database or its frozen baseline.  It applies
``database/v3/*.sql`` in filename order and records rebuild provenance in
``schema_meta``.

Examples::

    python tools/build_v3_db.py --db data/macau_balancete_v3.sqlite

    # Test / disposable database
    python tools/build_v3_db.py --db /tmp/v3.sqlite --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "database" / "v3"
BASELINE = ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite"
ARTIFACT_MANIFEST = ROOT / "docs" / "audit" / "artifact_manifest.json"
VERSION_FILE = ROOT / "VERSION"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upsert_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO schema_meta (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, now()),
    )


def apply_schema(conn: sqlite3.Connection, schema_dir: Path = SCHEMA_DIR) -> list[str]:
    files = sorted(schema_dir.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"no SQL files found in {schema_dir}")
    applied: list[str] = []
    for path in files:
        conn.executescript(path.read_text(encoding="utf-8"))
        applied.append(path.name)
    return applied


def seed_metadata(
    conn: sqlite3.Connection,
    *,
    baseline: Path | None,
    artifact_manifest: Path | None,
) -> None:
    version = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "0.7.0"
    upsert_meta(conn, "schema_version", "3.0")
    upsert_meta(conn, "app_version", version)
    upsert_meta(conn, "id_generation_version", "1")
    upsert_meta(conn, "last_successful_replay_at", "not_run")
    if baseline is not None and baseline.is_file():
        upsert_meta(conn, "baseline_sha256", sha256_file(baseline))
    if artifact_manifest is not None and artifact_manifest.is_file():
        upsert_meta(conn, "artifact_manifest_sha256", sha256_file(artifact_manifest))


def existing_table_count(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "macau_balancete_v3.sqlite")
    parser.add_argument("--schema-dir", type=Path, default=SCHEMA_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--artifact-manifest", type=Path, default=ARTIFACT_MANIFEST)
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete an existing non-empty target DB before building (never the v5 baseline)",
    )
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if db_path == BASELINE.resolve():
        parser.error("refusing to overwrite the frozen v5 baseline")
    if db_path.exists() and existing_table_count(db_path) > 0:
        if not args.force:
            parser.error(
                f"target database already contains tables: {db_path}\n"
                "use --force only for a disposable v3 target"
            )
        db_path.unlink()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        applied = apply_schema(conn, args.schema_dir)
        seed_metadata(
            conn,
            baseline=args.baseline if args.baseline.exists() else None,
            artifact_manifest=args.artifact_manifest if args.artifact_manifest.exists() else None,
        )
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        views = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'"
        ).fetchone()[0]
    finally:
        conn.close()

    summary = {
        "database": str(db_path),
        "schema_files": applied,
        "tables": tables,
        "views": views,
        "integrity_check": integrity,
        "foreign_key_errors": len(foreign_key_errors),
        "sha256": sha256_file(db_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if integrity != "ok" or foreign_key_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
