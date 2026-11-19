"""Read models and controlled write operations for the Workbench scaffold."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.config import SNAPSHOT_MANIFEST, V3_DB
from workbench.db import connect_v3

NAMESPACE = uuid.UUID("f2a3b4c5-d6e7-5f80-9a1b-2c3d4e5f6a77")
VERSION = "workbench-v1"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def summary() -> dict[str, Any]:
    conn = connect_v3()
    try:
        data = {
            "db_path": str(V3_DB),
            "statements_active": _count(conn, "SELECT COUNT(*) FROM statements WHERE statement_status='active'"),
            "statements_held": _count(conn, "SELECT COUNT(*) FROM statements WHERE statement_status<>'active'"),
            "current_rows": _count(conn, "SELECT COUNT(*) FROM v_current_statement_rows"),
            "held_rows": _count(
                conn,
                "SELECT COUNT(*) FROM statement_rows sr JOIN statements s ON s.statement_id=sr.statement_id "
                "WHERE s.statement_status<>'active'",
            ),
            "field_verifications": _count(conn, "SELECT COUNT(*) FROM field_verifications"),
            "active_field_verifications": _count(
                conn,
                "SELECT COUNT(*) FROM field_verifications fv "
                "JOIN statement_rows sr ON sr.statement_row_id=fv.statement_row_id "
                "JOIN statements s ON s.statement_id=sr.statement_id "
                "WHERE s.statement_status='active'",
            ),
            "publications": _count(conn, "SELECT COUNT(*) FROM publication_records"),
        }
    finally:
        conn.close()
    manifest_path = Path(SNAPSHOT_MANIFEST)
    data["snapshot_manifest_exists"] = manifest_path.is_file()
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["snapshot"] = {
                "path": manifest.get("snapshot_path"),
                "sha256": manifest.get("snapshot_sha256"),
                "adopted_statements": manifest.get("adopted_statements"),
                "current_rows": manifest.get("current_rows"),
                "held_statements_excluded": manifest.get("held_statements_excluded"),
                "read_only": manifest.get("read_only"),
            }
        except (OSError, json.JSONDecodeError):
            data["snapshot"] = {"error": "manifest unreadable"}
    return data


def list_statements(status: str = "all", limit: int = 200) -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        where = ""
        params: list[Any] = []
        if status == "active":
            where = "WHERE s.statement_status='active'"
        elif status == "held":
            where = "WHERE s.statement_status<>'active'"
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT s.statement_id, s.statement_date, s.publication_date,
                   s.reporting_scope, s.statement_status, s.identity_status,
                   s.adopted_statement_extraction_id,
                   e.canonical_zh AS entity_name, e.entity_key,
                   (SELECT COUNT(*) FROM statement_rows sr WHERE sr.statement_id=s.statement_id) AS row_count,
                   (SELECT COUNT(*) FROM statement_pages sp WHERE sp.statement_id=s.statement_id) AS page_count
            FROM statements s
            JOIN reporting_entities e ON e.entity_id=s.reporting_entity_id
            {where}
            ORDER BY s.statement_date DESC, e.canonical_zh
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def statement_detail(statement_id: str) -> dict[str, Any] | None:
    conn = connect_v3()
    try:
        row = conn.execute(
            """
            SELECT s.*, e.canonical_zh AS entity_name, e.entity_key,
                   se.extraction_status, se.statement_extraction_id
            FROM statements s
            JOIN reporting_entities e ON e.entity_id=s.reporting_entity_id
            LEFT JOIN statement_extractions se ON se.statement_id=s.statement_id
            WHERE s.statement_id=?
            """,
            (statement_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["pages"] = [
            dict(page)
            for page in conn.execute(
                "SELECT sp.document_page_id, sp.page_order, sp.is_primary, "
                "dp.pdf_page, dp.printed_page, dp.page_role, dp.classification_status, "
                "dp.classification_source, dp.notes "
                "FROM statement_pages sp JOIN document_pages dp "
                "ON dp.document_page_id=sp.document_page_id "
                "WHERE sp.statement_id=? ORDER BY sp.page_order",
                (statement_id,),
            )
        ]
        result["findings"] = [
            dict(finding)
            for finding in conn.execute(
                "SELECT finding_id, rule_key, severity, finding_status, details_json, created_at "
                "FROM validation_findings WHERE statement_id=? ORDER BY created_at",
                (statement_id,),
            )
        ]
        result["tasks"] = [
            dict(task)
            for task in conn.execute(
                "SELECT review_task_id, task_type, priority, task_status, assigned_to, created_at "
                "FROM review_tasks WHERE scope_statement_id=? ORDER BY created_at",
                (statement_id,),
            )
        ]
        return result
    finally:
        conn.close()


def statement_rows(statement_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        rows = conn.execute(
            """
            SELECT sr.statement_row_id, sr.statement_order, sr.row_type,
                   er.label_raw, sr.label_normalized, sr.label_language,
                   er.debit_raw, er.credit_raw,
                   sr.debit_minor, sr.debit_value_state,
                   sr.credit_minor, sr.credit_value_state,
                   sr.parent_context_status, sr.row_fingerprint,
                   dp.pdf_page
            FROM statement_rows sr
            JOIN extracted_rows er ON er.extracted_row_id=sr.extracted_row_id
            JOIN document_pages dp ON dp.document_page_id=er.document_page_id
            WHERE sr.statement_id=?
            ORDER BY sr.statement_order, sr.statement_row_id
            LIMIT ?
            """,
            (statement_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def adopt_statement(statement_id: str, actor: str) -> dict[str, Any]:
    conn = connect_v3(write=True)
    try:
        statement = conn.execute(
            "SELECT statement_id, statement_status, adopted_statement_extraction_id "
            "FROM statements WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if statement is None:
            raise LookupError("statement not found")
        if statement["statement_status"] == "active" or statement["adopted_statement_extraction_id"]:
            raise ValueError("statement is already adopted")
        extraction = conn.execute(
            "SELECT statement_extraction_id, extraction_status FROM statement_extractions "
            "WHERE statement_id=? ORDER BY created_at DESC LIMIT 1",
            (statement_id,),
        ).fetchone()
        if extraction is None or extraction["extraction_status"] != "completed":
            raise ValueError("statement has no completed extraction")
        if conn.execute(
            "SELECT 1 FROM publication_records WHERE statement_id=? AND publication_status='published'",
            (statement_id,),
        ).fetchone():
            raise ValueError("statement has a current publication")
        conn.execute(
            "UPDATE statements SET adopted_statement_extraction_id=?, statement_status='active' "
            "WHERE statement_id=?",
            (extraction["statement_extraction_id"], statement_id),
        )
        event_id = stable_id("workbench_adopt", statement_id, extraction["statement_extraction_id"], VERSION)
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, actor_id, before_json, after_json, "
            "details_json, reason, created_at"
            ") VALUES (?, 'workbench_adopted', ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, statement_id, actor,
                json.dumps({"statement_status": statement["statement_status"]}, ensure_ascii=False),
                json.dumps({
                    "statement_status": "active",
                    "adopted_statement_extraction_id": extraction["statement_extraction_id"],
                }, ensure_ascii=False),
                json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                "Adopted via Workbench",
                now(),
            ),
        )
        conn.commit()
        return {
            "statement_id": statement_id,
            "adopted_statement_extraction_id": extraction["statement_extraction_id"],
            "statement_status": "active",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def publish_statement(statement_id: str, actor: str) -> dict[str, Any]:
    """Publication gate scaffold.

    Deliberately separate from adoption.  The v1 Workbench does not yet define
    the statement quality gate, so publish is blocked rather than silently
    creating a publication record.
    """
    raise NotImplementedError(
        "Publish gate is not implemented in Workbench v1; adoption is available separately"
    )
