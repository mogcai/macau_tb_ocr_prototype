#!/usr/bin/env python3
"""Build a reproducible, read-only MVP snapshot of the current v3 dataset.

The snapshot contains only adopted/current statements and their current rows,
plus the reference tables needed to interpret them.  Held/proposed statements
and their rows stay in the operational v3 database.  The snapshot is a delivery
artifact, never a writable authority.

Usage::

    python tools/build_mvp_snapshot.py \
        --source data/macau_balancete_v3.sqlite \
        --out data/baseline/macau_balancete_v3_mvp_20261119.sqlite \
        --manifest docs/audit/MVP_SNAPSHOT_MANIFEST.json --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_v3_db import apply_schema  # noqa: E402
DEFAULT_SOURCE = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_OUT = ROOT / "data" / "baseline" / "macau_balancete_v3_mvp_20261119.sqlite"
DEFAULT_MANIFEST = ROOT / "docs" / "audit" / "MVP_SNAPSHOT_MANIFEST.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def copy_rows(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
    *,
    where: str = "",
    params: tuple[Any, ...] = (),
    transform: Any = None,
) -> int:
    columns = table_columns(src, table)
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    rows = src.execute(sql, params).fetchall()
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    insert = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    values = []
    for row in rows:
        record = dict(zip(columns, row))
        if transform is not None:
            record = transform(record)
        values.append(tuple(record[column] for column in columns))
    dst.executemany(insert, values)
    return len(values)


def build_snapshot(source: Path, out: Path, manifest_path: Path) -> dict[str, Any]:
    if out == source:
        raise ValueError("refusing to overwrite the operational source DB")
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source)
    created_at = now()

    src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(out)
    dst.row_factory = sqlite3.Row
    dst.execute("PRAGMA foreign_keys = ON")
    try:
        apply_schema(dst)
        dst.execute("DELETE FROM schema_meta")

        active = [
            row["statement_id"]
            for row in src.execute(
                "SELECT statement_id FROM statements "
                "WHERE statement_status='active' AND adopted_statement_extraction_id IS NOT NULL"
            )
        ]
        active_set = set(active)
        active_extractions = {
            row["statement_extraction_id"]
            for row in src.execute(
                "SELECT statement_extraction_id FROM statement_extractions "
                "WHERE statement_id IN (%s)" % ",".join("?" * len(active)),
                tuple(active),
            )
        }
        active_rows = {
            row["statement_row_id"]
            for row in src.execute(
                "SELECT statement_row_id FROM statement_rows "
                "WHERE statement_id IN (%s)" % ",".join("?" * len(active)),
                tuple(active),
            )
        }

        # Reference data.
        for table in (
            "gazette_issues", "source_documents", "document_pages",
            "reporting_entities", "reporting_entity_aliases",
            "extraction_runs", "page_extractions",
        ):
            copy_rows(src, dst, table)

        # Statements are inserted with NULL adoption first, then adopted after
        # their extraction exists (the insert trigger enforces this).
        def strip_adoption(record: dict[str, Any]) -> dict[str, Any]:
            record["adopted_statement_extraction_id"] = None
            return record

        copy_rows(
            src, dst, "statements",
            where="statement_id IN (%s)" % ",".join("?" * len(active)),
            params=tuple(active),
            transform=strip_adoption,
        )
        copy_rows(
            src, dst, "statement_extractions",
            where="statement_id IN (%s)" % ",".join("?" * len(active)),
            params=tuple(active),
        )
        for row in src.execute(
            "SELECT statement_id, adopted_statement_extraction_id FROM statements "
            "WHERE statement_id IN (%s)" % ",".join("?" * len(active)),
            tuple(active),
        ):
            dst.execute(
                "UPDATE statements SET adopted_statement_extraction_id=? WHERE statement_id=?",
                (row["adopted_statement_extraction_id"], row["statement_id"]),
            )
        copy_rows(
            src, dst, "statement_pages",
            where="statement_id IN (%s)" % ",".join("?" * len(active)),
            params=tuple(active),
        )
        copy_rows(
            src, dst, "extracted_rows",
            where="statement_extraction_id IN (%s)" % ",".join("?" * len(active_extractions)),
            params=tuple(active_extractions),
        )
        copy_rows(
            src, dst, "statement_rows",
            where="statement_id IN (%s)" % ",".join("?" * len(active)),
            params=tuple(active),
        )

        # Catalogue/mapping tables are deliberately empty in the MVP snapshot.
        for table in ("standard_items", "standard_item_aliases", "statement_row_mappings"):
            copy_rows(src, dst, table)

        copy_rows(
            src, dst, "field_verifications",
            where="statement_row_id IN (%s)" % ",".join("?" * len(active_rows)),
            params=tuple(active_rows),
        )
        copy_rows(src, dst, "validation_findings")
        copy_rows(src, dst, "review_tasks")
        copy_rows(
            src, dst, "publication_records",
            where="statement_id IN (%s)" % ",".join("?" * len(active)),
            params=tuple(active),
        )

        # Audit events: keep only events whose non-null targets exist in the
        # snapshot.  This prevents dangling FKs from held rows/statements.
        target_sets = {
            "source_document_id": {row[0] for row in dst.execute("SELECT source_document_id FROM source_documents")},
            "document_page_id": {row[0] for row in dst.execute("SELECT document_page_id FROM document_pages")},
            "entity_id": {row[0] for row in dst.execute("SELECT entity_id FROM reporting_entities")},
            "statement_id": {row[0] for row in dst.execute("SELECT statement_id FROM statements")},
            "statement_extraction_id": {row[0] for row in dst.execute("SELECT statement_extraction_id FROM statement_extractions")},
            "statement_row_id": {row[0] for row in dst.execute("SELECT statement_row_id FROM statement_rows")},
            "standard_item_id": {row[0] for row in dst.execute("SELECT standard_item_id FROM standard_items")},
            "mapping_id": {row[0] for row in dst.execute("SELECT mapping_id FROM statement_row_mappings")},
            "field_verification_id": {row[0] for row in dst.execute("SELECT field_verification_id FROM field_verifications")},
            "review_task_id": {row[0] for row in dst.execute("SELECT review_task_id FROM review_tasks")},
            "publication_id": {row[0] for row in dst.execute("SELECT publication_id FROM publication_records")},
        }
        audit_rows = []
        audit_columns = table_columns(src, "audit_events")
        for row in src.execute("SELECT * FROM audit_events"):
            record = dict(zip(audit_columns, row))
            keep = True
            for column, values in target_sets.items():
                value = record.get(column)
                if value is not None and value not in values:
                    keep = False
                    break
            if keep:
                audit_rows.append(tuple(record[column] for column in audit_columns))
        if audit_rows:
            placeholders = ", ".join("?" for _ in audit_columns)
            dst.executemany(
                f"INSERT INTO audit_events ({', '.join(audit_columns)}) VALUES ({placeholders})",
                audit_rows,
            )

        # Metadata.
        for row in src.execute("SELECT key, value FROM schema_meta"):
            dst.execute(
                "INSERT INTO schema_meta (key, value, updated_at) VALUES (?, ?, ?)",
                (row["key"], row["value"], created_at),
            )
        meta = {
            "snapshot_scope": "mvp_current",
            "snapshot_created_at": created_at,
            "snapshot_source_db": str(source),
            "snapshot_source_sha256": source_hash,
            "snapshot_adopted_statements": str(len(active)),
            "snapshot_current_rows": "0",  # filled after count
            "snapshot_held_statements": str(
                src.execute("SELECT COUNT(*) FROM statements WHERE statement_status <> 'active'").fetchone()[0]
            ),
            "snapshot_held_rows": str(
                src.execute(
                    "SELECT COUNT(*) FROM statement_rows sr JOIN statements s "
                    "ON s.statement_id=sr.statement_id WHERE s.statement_status <> 'active'"
                ).fetchone()[0]
            ),
            "snapshot_active_field_verifications": "0",
            "snapshot_read_only": "true",
            "snapshot_manifest": str(manifest_path),
        }
        current_rows = dst.execute("SELECT COUNT(*) FROM v_current_statement_rows").fetchone()[0]
        meta["snapshot_current_rows"] = str(current_rows)
        meta["snapshot_active_field_verifications"] = str(
            dst.execute(
                "SELECT COUNT(*) FROM field_verifications fv "
                "JOIN statement_rows sr ON sr.statement_row_id=fv.statement_row_id "
                "JOIN statements s ON s.statement_id=sr.statement_id "
                "WHERE s.statement_status='active'"
            ).fetchone()[0]
        )
        for key, value in meta.items():
            dst.execute(
                "INSERT INTO schema_meta (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, created_at),
            )
        dst.commit()
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = dst.execute("PRAGMA foreign_key_check").fetchall()
        table_counts = {
            table: dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "statements", "statement_pages", "statement_extractions",
                "statement_rows", "extracted_rows", "field_verifications",
                "audit_events",
            )
        }
    finally:
        src.close()
        dst.close()

    os.chmod(out, 0o444)
    manifest = {
        "snapshot_path": str(out),
        "snapshot_sha256": sha256_file(out),
        "snapshot_size": out.stat().st_size,
        "created_at": created_at,
        "source_db": str(source),
        "source_sha256": source_hash,
        "schema_version": "3.0",
        "scope": "mvp_current",
        "read_only": True,
        "integrity_check": integrity,
        "foreign_key_errors": len(fk_errors),
        "adopted_statements": table_counts["statements"],
        "current_rows": table_counts["statement_rows"],
        "held_statements_excluded": int(meta["snapshot_held_statements"]),
        "held_rows_excluded": int(meta["snapshot_held_rows"]),
        "active_field_verifications": int(meta["snapshot_active_field_verifications"]),
        "active_financially_verified": False,
        "table_counts": table_counts,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.out.exists() and not args.force:
        parser.error(f"snapshot already exists: {args.out}")
    manifest = build_snapshot(
        args.source.expanduser().resolve(),
        args.out.expanduser().resolve(),
        args.manifest.expanduser().resolve(),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
