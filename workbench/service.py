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


class StatementReviewedError(ValueError):
    """Raised when a reviewed statement is edited without reopening."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


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
            "standard_items": _count(conn, "SELECT COUNT(*) FROM standard_items"),
            "proposed_mappings": _count(conn, "SELECT COUNT(*) FROM statement_row_mappings WHERE mapping_status='proposed'"),
            "approved_mappings": _count(conn, "SELECT COUNT(*) FROM statement_row_mappings WHERE mapping_status='approved'"),
            "mapping_conflict_rows": _count(
                conn,
                "SELECT COUNT(DISTINCT statement_row_id) FROM statement_row_mappings "
                "WHERE conflict_group_id IS NOT NULL "
                "AND mapping_status IN ('proposed','approved')",
            ),
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
                   (SELECT COUNT(*) FROM statement_pages sp WHERE sp.statement_id=s.statement_id) AS page_count,
                   COALESCE((SELECT rv.review_status FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id), 'pending') AS review_status
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
                   se.extraction_status, se.statement_extraction_id,
                   COALESCE((SELECT rv.review_status FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id), 'pending') AS review_status,
                   (SELECT rv.reviewed_by FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id) AS reviewed_by,
                   (SELECT rv.reviewed_at FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id) AS reviewed_at
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
    """Return rows with effective (verified) values for the statement view."""
    return review_rows(statement_id, limit=limit)


def mark_statement_reviewed(
    statement_id: str,
    actor: str,
    *,
    note: str,
    review_status: str = "reviewed",
) -> dict[str, Any]:
    if review_status not in {"reviewed", "needs_rework", "pending"}:
        raise ValueError("invalid review status")
    conn = connect_v3(write=True)
    try:
        statement = conn.execute(
            "SELECT statement_id, statement_status FROM statements WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if statement is None:
            raise LookupError("statement not found")
        if statement["statement_status"] == "active":
            raise ValueError("active statements are already adopted; review state is not applicable")
        readiness = next((item for item in review_queue() if item["statement_id"] == statement_id), None)
        if readiness is not None and not readiness["mapping_ready"]:
            raise ValueError(
                "resolve mapping conflict/unmatched before marking reviewed: "
                f"{readiness['mapping_blockers']}"
            )
        conn.execute(
            """
            INSERT INTO statement_reviews(
                statement_id, review_status, reviewed_by, reviewed_at, note, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(statement_id) DO UPDATE SET
                review_status=excluded.review_status,
                reviewed_by=excluded.reviewed_by,
                reviewed_at=excluded.reviewed_at,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            (statement_id, review_status, actor, now(), note, now()),
        )
        event_id = stable_id("workbench_statement_review", statement_id, review_status, now())
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, actor_id, after_json, "
            "details_json, reason, created_at"
            ") VALUES (?, 'workbench_statement_reviewed', ?, ?, ?, ?, ?, ?)",
            (
                event_id, statement_id, actor,
                json.dumps({"review_status": review_status, "note": note}, ensure_ascii=False),
                json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                note or "Statement reviewed in Workbench",
                now(),
            ),
        )
        conn.commit()
        return {"statement_id": statement_id, "review_status": review_status, "reviewed_by": actor}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def adopt_statement(statement_id: str, actor: str) -> dict[str, Any]:
    readiness = next((item for item in review_queue() if item["statement_id"] == statement_id), None)
    if readiness is not None and readiness["review_bucket"] != "ready_for_adoption":
        raise ValueError(f"statement is not adoption-ready: {readiness['review_bucket']}")
    if readiness is not None and readiness.get("review_status") != "reviewed":
        raise ValueError("statement must be marked reviewed before adoption")
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


def _money(minor: int | None, places: int | None) -> str:
    if minor is None:
        return ""
    from decimal import Decimal
    scale = places if places is not None else 2
    value = Decimal(minor) / (Decimal(10) ** scale)
    return format(value, ",.2f")


def _parse_money(value: str | None) -> int:
    if value is None or str(value).strip() == "":
        return 0
    raw = str(value).strip().replace(",", "")
    if raw in {"-", "–", "—", "−"}:
        return 0
    try:
        from decimal import Decimal
        amount = Decimal(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid amount: {value!r}") from exc
    scaled = amount * 100
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"amount has more than two decimal places: {value!r}")
    return int(integral)


EFFECTIVE_ROW_SQL = """
SELECT
    sr.statement_id,
    sr.statement_row_id,
    sr.statement_order,
    sr.row_type,
    sr.parent_context_status,
    er.label_raw,
    er.debit_raw,
    er.credit_raw,
    dp.pdf_page,
    sr.label_normalized AS base_label,
    sr.debit_minor AS base_debit_minor,
    sr.debit_value_state AS base_debit_state,
    sr.credit_minor AS base_credit_minor,
    sr.credit_value_state AS base_credit_state,
    COALESCE((SELECT fv.verified_value_text FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='label_normalized'
        AND fv.verification_status='verified'), sr.label_normalized) AS effective_label,
    COALESCE((SELECT CAST(fv.verified_value_text AS INTEGER) FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='debit_minor'
        AND fv.verification_status='verified'), sr.debit_minor) AS effective_debit_minor,
    COALESCE((SELECT fv.verified_value_text FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='debit_value_state'
        AND fv.verification_status='verified'), sr.debit_value_state) AS effective_debit_state,
    COALESCE((SELECT CAST(fv.verified_value_text AS INTEGER) FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='credit_minor'
        AND fv.verification_status='verified'), sr.credit_minor) AS effective_credit_minor,
    COALESCE((SELECT fv.verified_value_text FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='credit_value_state'
        AND fv.verification_status='verified'), sr.credit_value_state) AS effective_credit_state
FROM statement_rows sr
JOIN extracted_rows er ON er.extracted_row_id=sr.extracted_row_id
JOIN document_pages dp ON dp.document_page_id=er.document_page_id
"""


def standard_items() -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT standard_item_id, item_key, canonical_zh, canonical_pt, "
            "parent_standard_item_id, item_status FROM standard_items "
            "ORDER BY item_key"
        )]
    finally:
        conn.close()
    by_id = {row["standard_item_id"]: row for row in rows}

    def path_for(row: dict[str, Any]) -> str:
        parts = []
        current = row
        seen = set()
        while current and current["standard_item_id"] not in seen:
            seen.add(current["standard_item_id"])
            label = text(current.get("canonical_zh")) or text(current.get("canonical_pt")) or current["item_key"]
            parts.append(label)
            current = by_id.get(current.get("parent_standard_item_id"))
        return " > ".join(reversed(parts))

    for row in rows:
        row["path"] = path_for(row)
    return rows


def _mapping_info(conn: sqlite3.Connection, statement_row_id: str) -> dict[str, Any]:
    mappings = [dict(row) for row in conn.execute(
        """
        SELECT m.mapping_id, m.statement_row_id, m.standard_item_id,
               m.mapping_status, m.conflict_group_id, m.match_method,
               m.match_score, m.mapping_version,
               si.item_key, si.canonical_zh, si.canonical_pt
        FROM statement_row_mappings m
        JOIN standard_items si ON si.standard_item_id=m.standard_item_id
        WHERE m.statement_row_id=?
          AND m.mapping_status IN ('proposed','approved')
        ORDER BY CASE m.mapping_status WHEN 'approved' THEN 0 ELSE 1 END,
                 m.match_score DESC, si.item_key
        """,
        (statement_row_id,),
    )]
    approved = next((item for item in mappings if item["mapping_status"] == "approved"), None)
    proposed = [item for item in mappings if item["mapping_status"] == "proposed"]
    if approved is not None:
        status = "approved"
        selected = approved
    elif len(proposed) == 1:
        status = "unique"
        selected = proposed[0]
    elif len(proposed) > 1:
        status = "conflict"
        selected = None
    else:
        status = "unmatched"
        selected = None
    return {
        "mapping_status": status,
        "standard_item_id": selected["standard_item_id"] if selected else "",
        "standard_item_key": selected["item_key"] if selected else "",
        "standardised_label": (
            text(selected.get("canonical_zh")) or text(selected.get("canonical_pt"))
            if selected else ""
        ),
        "mapping_candidates": mappings,
    }


def review_queue() -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        rows = conn.execute(
            f"""
            WITH effective AS (
                {EFFECTIVE_ROW_SQL}
            )
            SELECT
                s.statement_id, s.statement_date, s.publication_date,
                s.reporting_scope, s.statement_status,
                e.canonical_zh AS entity_name, e.entity_key,
                s.currency, s.unit_scale, s.amount_decimal_places,
                COUNT(er.statement_row_id) AS row_count,
                SUM(CASE WHEN er.effective_label IS NULL OR er.effective_label = '' THEN 1 ELSE 0 END) AS missing_labels,
                SUM(CASE WHEN er.effective_debit_state IN ('ambiguous','unreadable')
                          OR er.effective_credit_state IN ('ambiguous','unreadable') THEN 1 ELSE 0 END) AS issue_rows,
                SUM(CASE WHEN er.effective_debit_state='amount' THEN er.effective_debit_minor ELSE 0 END) AS debit_total_minor,
                SUM(CASE WHEN er.effective_credit_state='amount' THEN er.effective_credit_minor ELSE 0 END) AS credit_total_minor,
                (SELECT COUNT(*) FROM field_verifications fv
                  JOIN statement_rows sr2 ON sr2.statement_row_id=fv.statement_row_id
                  WHERE sr2.statement_id=s.statement_id) AS field_verifications,
                COALESCE((SELECT rv.review_status FROM statement_reviews rv
                  WHERE rv.statement_id=s.statement_id), 'pending') AS review_status,
                (SELECT rv.reviewed_by FROM statement_reviews rv
                  WHERE rv.statement_id=s.statement_id) AS reviewed_by,
                (SELECT rv.reviewed_at FROM statement_reviews rv
                  WHERE rv.statement_id=s.statement_id) AS reviewed_at,
                (SELECT COUNT(DISTINCT m.statement_row_id)
                   FROM statement_row_mappings m
                   JOIN statement_rows sr2 ON sr2.statement_row_id=m.statement_row_id
                  WHERE sr2.statement_id=s.statement_id
                    AND m.mapping_status IN ('proposed','approved')) AS mapped_rows,
                (SELECT COUNT(DISTINCT m.statement_row_id)
                   FROM statement_row_mappings m
                   JOIN statement_rows sr2 ON sr2.statement_row_id=m.statement_row_id
                  WHERE sr2.statement_id=s.statement_id
                    AND m.mapping_status='approved') AS approved_mapping_rows,
                (SELECT COUNT(DISTINCT m.statement_row_id)
                   FROM statement_row_mappings m
                   JOIN statement_rows sr2 ON sr2.statement_row_id=m.statement_row_id
                  WHERE sr2.statement_id=s.statement_id
                    AND m.conflict_group_id IS NOT NULL
                    AND m.mapping_status IN ('proposed','approved')) AS mapping_conflict_rows
            FROM statements s
            JOIN reporting_entities e ON e.entity_id=s.reporting_entity_id
            LEFT JOIN effective er ON er.statement_id=s.statement_id
            WHERE s.statement_status <> 'active'
            GROUP BY s.statement_id, s.statement_date, s.publication_date,
                     s.reporting_scope, s.statement_status, e.canonical_zh,
                     e.entity_key, s.currency, s.unit_scale, s.amount_decimal_places
            """,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            difference = int(data["debit_total_minor"] or 0) - int(data["credit_total_minor"] or 0)
            data["debit_credit_difference_minor"] = difference
            data["debit_credit_equal"] = difference == 0
            data["unmapped_rows"] = max(0, int(data["row_count"] or 0) - int(data["mapped_rows"] or 0))
            data["mapping_ready"] = (
                int(data["row_count"] or 0) > 0
                and int(data["mapping_conflict_rows"] or 0) == 0
                and data["unmapped_rows"] == 0
            )
            data["mapping_blockers"] = ";".join(
                part for part in (
                    f"mapping_conflict={data['mapping_conflict_rows']}" if data["mapping_conflict_rows"] else "",
                    f"mapping_unmatched={data['unmapped_rows']}" if data["unmapped_rows"] else "",
                ) if part
            )
            if not data["row_count"]:
                bucket = "no_rows"
            elif data["mapping_conflict_rows"]:
                bucket = "mapping_conflict"
            elif data["unmapped_rows"]:
                bucket = "mapping_unmatched"
            elif difference != 0:
                bucket = "unbalanced"
            elif data["missing_labels"] or data["issue_rows"]:
                bucket = "row_issues"
            else:
                bucket = "ready_for_adoption"
            data["review_bucket"] = bucket
            data["priority"] = {
                "mapping_conflict": 1, "mapping_unmatched": 1,
                "unbalanced": 1, "no_rows": 1, "row_issues": 2,
            }.get(bucket, 3)
            data["debit_total_display"] = _money(data["debit_total_minor"], data["amount_decimal_places"])
            data["credit_total_display"] = _money(data["credit_total_minor"], data["amount_decimal_places"])
            result.append(data)
        return sorted(result, key=lambda item: (item["priority"], -int(item["issue_rows"] or 0), item["entity_name"]))
    finally:
        conn.close()


def review_rows(statement_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        statement = conn.execute(
            "SELECT amount_decimal_places FROM statements WHERE statement_id=?", (statement_id,)
        ).fetchone()
        if statement is None:
            raise LookupError("statement not found")
        places = statement["amount_decimal_places"]
        rows = conn.execute(
            f"{EFFECTIVE_ROW_SQL} WHERE sr.statement_id=? ORDER BY sr.statement_order, sr.statement_row_id LIMIT ?",
            (statement_id, limit),
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["effective_debit_display"] = _money(data["effective_debit_minor"], places)
            data["effective_credit_display"] = _money(data["effective_credit_minor"], places)
            issues = []
            if not text(data["effective_label"]):
                issues.append("missing_label")
            if data["effective_debit_state"] in ("ambiguous", "unreadable"):
                issues.append("debit_" + data["effective_debit_state"])
            if data["effective_credit_state"] in ("ambiguous", "unreadable"):
                issues.append("credit_" + data["effective_credit_state"])
            data["row_issues"] = ";".join(issues)
            data.update(_mapping_info(conn, data["statement_row_id"]))
            result.append(data)
        return result
    finally:
        conn.close()


def verify_row(
    statement_id: str,
    statement_row_id: str,
    *,
    label: str | None,
    debit: str | None,
    credit: str | None,
    debit_state: str,
    credit_state: str,
    note: str,
    actor: str,
) -> dict[str, Any]:
    if debit_state not in {"amount", "dash", "blank", "unreadable", "not_applicable", "ambiguous"}:
        raise ValueError("invalid debit state")
    if credit_state not in {"amount", "dash", "blank", "unreadable", "not_applicable", "ambiguous"}:
        raise ValueError("invalid credit state")
    debit_minor = _parse_money(debit) if debit_state == "amount" else 0
    credit_minor = _parse_money(credit) if credit_state == "amount" else 0
    conn = connect_v3(write=True)
    try:
        row = conn.execute(
            "SELECT statement_id, label_normalized, debit_minor, debit_value_state, "
            "credit_minor, credit_value_state FROM statement_rows WHERE statement_row_id=?",
            (statement_row_id,),
        ).fetchone()
        if row is None or row["statement_id"] != statement_id:
            raise LookupError("statement row not found")
        review = conn.execute(
            "SELECT review_status FROM statement_reviews WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if review is not None and review["review_status"] == "reviewed":
            raise StatementReviewedError("statement is reviewed; reopen it before editing")
        before = dict(row)
        fields = [
            ("label_normalized", before["label_normalized"], "" if label is None else label),
            ("debit_minor", before["debit_minor"], str(debit_minor)),
            ("debit_value_state", before["debit_value_state"], debit_state),
            ("credit_minor", before["credit_minor"], str(credit_minor)),
            ("credit_value_state", before["credit_value_state"], credit_state),
        ]
        for field_name, original, proposed in fields:
            verification_id = stable_id("workbench_verification", statement_row_id, field_name, VERSION)
            conn.execute(
                """
                INSERT INTO field_verifications(
                    field_verification_id, statement_row_id, field_name,
                    original_value_text, verified_value_text, verification_status,
                    verified_by, verified_at, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, 'verified', ?, ?, ?, ?)
                ON CONFLICT(statement_row_id, field_name) DO UPDATE SET
                    original_value_text=excluded.original_value_text,
                    verified_value_text=excluded.verified_value_text,
                    verification_status=excluded.verification_status,
                    verified_by=excluded.verified_by,
                    verified_at=excluded.verified_at,
                    reason=excluded.reason
                """,
                (
                    verification_id, statement_row_id, field_name,
                    str(original) if original is not None else None,
                    proposed, actor, now(), note or "Workbench row verification", now(),
                ),
            )
        event_id = stable_id("workbench_verify_event", statement_row_id, VERSION, now())
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, statement_row_id, actor_id, "
            "before_json, after_json, details_json, reason, created_at"
            ") VALUES (?, 'workbench_row_verified', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, statement_id, statement_row_id, actor,
                json.dumps(before, ensure_ascii=False, default=str),
                json.dumps({
                    "label": label, "debit_minor": debit_minor, "debit_state": debit_state,
                    "credit_minor": credit_minor, "credit_state": credit_state,
                }, ensure_ascii=False),
                json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                note or "Workbench row verification",
                now(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    rows = review_rows(statement_id)
    for item in rows:
        if item["statement_row_id"] == statement_row_id:
            return item
    raise LookupError("statement row not found after verification")


def approve_mapping(
    statement_id: str,
    statement_row_id: str,
    standard_item_id: str,
    actor: str,
    note: str = "",
) -> dict[str, Any]:
    conn = connect_v3(write=True)
    try:
        row = conn.execute(
            "SELECT statement_id FROM statement_rows WHERE statement_row_id=?",
            (statement_row_id,),
        ).fetchone()
        if row is None or row["statement_id"] != statement_id:
            raise LookupError("statement row not found")
        item = conn.execute(
            "SELECT standard_item_id, item_key FROM standard_items WHERE standard_item_id=?",
            (standard_item_id,),
        ).fetchone()
        if item is None:
            raise LookupError("standard item not found")
        review = conn.execute(
            "SELECT review_status FROM statement_reviews WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if review is not None and review["review_status"] == "reviewed":
            raise ValueError("statement is reviewed; reopen it before changing mappings")

        before = [
            dict(mapping)
            for mapping in conn.execute(
                "SELECT mapping_id, standard_item_id, mapping_status FROM statement_row_mappings "
                "WHERE statement_row_id=? AND mapping_status IN ('proposed','approved')",
                (statement_row_id,),
            )
        ]
        conn.execute(
            "UPDATE statement_row_mappings SET mapping_status='rejected', "
            "conflict_group_id=NULL "
            "WHERE statement_row_id=? AND standard_item_id<>? "
            "AND mapping_status IN ('proposed','approved')",
            (statement_row_id, standard_item_id),
        )
        existing = conn.execute(
            "SELECT mapping_id FROM statement_row_mappings "
            "WHERE statement_row_id=? AND standard_item_id=?",
            (statement_row_id, standard_item_id),
        ).fetchone()
        if existing:
            mapping_id = existing["mapping_id"]
            conn.execute(
                """
                UPDATE statement_row_mappings
                SET mapping_status='approved', conflict_group_id=NULL,
                    match_method='manual', match_score=1.0,
                    mapping_version='p1-review-1',
                    evidence_source='workbench_manual',
                    reviewed_by=?, reviewed_at=?, notes=?
                WHERE mapping_id=?
                """,
                (actor, now(), note, mapping_id),
            )
        else:
            mapping_id = stable_id("workbench_mapping", statement_row_id, standard_item_id, VERSION)
            conn.execute(
                """
                INSERT INTO statement_row_mappings(
                    mapping_id, statement_row_id, standard_item_id,
                    conflict_group_id, mapping_status, match_method, match_score,
                    mapping_version, evidence_source, context_json,
                    reviewed_by, reviewed_at, created_at, notes
                ) VALUES (?, ?, ?, NULL, 'approved', 'manual', 1.0,
                          'p1-review-1', 'workbench_manual', ?, ?, ?, ?)
                """,
                (
                    mapping_id, statement_row_id, standard_item_id,
                    json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                    actor, now(), now(), note,
                ),
            )
        event_id = stable_id("workbench_mapping_event", mapping_id, VERSION, now())
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, statement_row_id, mapping_id, "
            "actor_id, before_json, after_json, details_json, reason, created_at"
            ") VALUES (?, 'workbench_mapping_approved', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, statement_id, statement_row_id, mapping_id, actor,
                json.dumps(before, ensure_ascii=False, default=str),
                json.dumps({
                    "standard_item_id": standard_item_id,
                    "standard_item_key": item["item_key"],
                    "mapping_status": "approved",
                }, ensure_ascii=False),
                json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                note or "Standard item mapping approved in Workbench",
                now(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    rows = review_rows(statement_id)
    for item_row in rows:
        if item_row["statement_row_id"] == statement_row_id:
            return item_row
    raise LookupError("statement row not found after mapping")
