"""Read models and controlled write operations for the Workbench scaffold."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.config import SNAPSHOT_MANIFEST, V3_DB
from workbench.db import connect_v3

NAMESPACE = uuid.UUID("f2a3b4c5-d6e7-5f80-9a1b-2c3d4e5f6a77")
VERSION = "workbench-v1"


class StatementReviewedError(ValueError):
    """Raised when a reviewed statement is edited without reopening."""


class StatementAdoptedError(ValueError):
    """Raised when an adopted statement is edited without returning it to review."""


class InactiveStandardItemError(ValueError):
    """Raised when a mapping targets a draft, merged or retired standard item."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _normalize_places(places: int | None) -> int:
    if places is None:
        return 2
    return max(0, min(6, int(places)))


def _current_extraction_sql(alias: str) -> str:
    """SQL expression selecting the extraction a reader must use.

    Order of precedence:
    1. the adopted extraction (active statements are immutable);
    2. the active reviewer binding (what was actually signed off);
    3. the latest completed extraction (pre-review workbench view).
    """
    return f"""COALESCE(
        (SELECT st.adopted_statement_extraction_id FROM statements st
          WHERE st.statement_id={alias}.statement_id),
        (SELECT b.statement_extraction_id FROM statement_review_bindings b
          WHERE b.statement_id={alias}.statement_id
            AND b.binding_status='active'
          ORDER BY b.bound_at DESC, b.binding_id DESC LIMIT 1),
        (SELECT se.statement_extraction_id FROM statement_extractions se
          WHERE se.statement_id={alias}.statement_id
            AND se.extraction_status='completed'
          ORDER BY se.created_at DESC, se.statement_extraction_id DESC LIMIT 1)
    )"""


def _current_extraction_id(conn: sqlite3.Connection, statement_id: str) -> str | None:
    row = conn.execute(
        f"SELECT {_current_extraction_sql('s')} AS extraction_id "
        "FROM statements s WHERE s.statement_id=?",
        (statement_id,),
    ).fetchone()
    return row["extraction_id"] if row is not None else None


def _latest_completed_extraction_id(
    conn: sqlite3.Connection, statement_id: str
) -> str | None:
    row = conn.execute(
        "SELECT statement_extraction_id FROM statement_extractions "
        "WHERE statement_id=? AND extraction_status='completed' "
        "ORDER BY created_at DESC, statement_extraction_id DESC LIMIT 1",
        (statement_id,),
    ).fetchone()
    return row["statement_extraction_id"] if row is not None else None


def _active_review_binding(
    conn: sqlite3.Connection, statement_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM statement_review_bindings "
        "WHERE statement_id=? AND binding_status='active' "
        "ORDER BY bound_at DESC, binding_id DESC LIMIT 1",
        (statement_id,),
    ).fetchone()


def _bind_review_extraction(
    conn: sqlite3.Connection,
    statement_id: str,
    statement_extraction_id: str,
    *,
    actor: str,
    note: str,
) -> str:
    conn.execute(
        "UPDATE statement_review_bindings SET binding_status='void', "
        "voided_by=?, voided_at=? WHERE statement_id=? AND binding_status='active'",
        (actor, now(), statement_id),
    )
    binding_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO statement_review_bindings("
        "binding_id, statement_id, statement_extraction_id, binding_status, "
        "bound_by, bound_at, note"
        ") VALUES (?, ?, ?, 'active', ?, ?, ?)",
        (binding_id, statement_id, statement_extraction_id, actor, now(), note),
    )
    return binding_id


def _check_standard_item_usable(conn: sqlite3.Connection, standard_item_id: str) -> sqlite3.Row:
    item = conn.execute(
        "SELECT standard_item_id, item_key, item_status, merged_into_standard_item_id "
        "FROM standard_items WHERE standard_item_id=?",
        (standard_item_id,),
    ).fetchone()
    if item is None:
        raise LookupError("standard item not found")
    if item["item_status"] == "merged":
        target = conn.execute(
            "SELECT item_key FROM standard_items WHERE standard_item_id=?",
            (item["merged_into_standard_item_id"],),
        ).fetchone()
        target_key = target["item_key"] if target is not None else "another item"
        raise InactiveStandardItemError(
            f"standard item {item['item_key']} is merged into {target_key}; "
            "map the merged target instead"
        )
    if item["item_status"] != "approved":
        raise InactiveStandardItemError(
            f"standard item {item['item_key']} is {item['item_status']}; "
            "only approved catalogue items can be mapped"
        )
    return item


def summary() -> dict[str, Any]:
    conn = connect_v3()
    try:
        data = {
            "db_path": str(V3_DB),
            "statements_active": _count(conn, "SELECT COUNT(*) FROM statements WHERE statement_status='active'"),
            "statements_held": _count(
                conn,
                "SELECT COUNT(*) FROM statements "
                "WHERE statement_status NOT IN ('active', 'excluded')",
            ),
            "current_rows": _count(conn, "SELECT COUNT(*) FROM v_current_statement_rows"),
            "held_rows": _count(
                conn,
                "SELECT COUNT(*) FROM statement_rows sr JOIN statements s ON s.statement_id=sr.statement_id "
                "WHERE s.statement_status NOT IN ('active', 'excluded') "
                f"AND sr.statement_extraction_id = {_current_extraction_sql('sr')}",
            ),
            "field_verifications": _count(conn, "SELECT COUNT(*) FROM field_verifications"),
            "active_field_verifications": _count(
                conn,
                "SELECT COUNT(*) FROM field_verifications fv "
                "JOIN v_current_statement_rows sr ON sr.statement_row_id=fv.statement_row_id",
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
            where = "WHERE s.statement_status NOT IN ('active', 'excluded')"
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT s.statement_id, s.statement_date, s.publication_date,
                   s.reporting_scope, s.statement_status, s.identity_status,
                   s.adopted_statement_extraction_id,
                   e.canonical_zh AS entity_name, e.entity_key,
                   (SELECT COUNT(*) FROM statement_rows sr
                     WHERE sr.statement_id=s.statement_id
                       AND sr.statement_extraction_id = {_current_extraction_sql('sr')}) AS row_count,
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
                   COALESCE((SELECT rv.review_status FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id), 'pending') AS review_status,
                   (SELECT rv.reviewed_by FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id) AS reviewed_by,
                   (SELECT rv.reviewed_at FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id) AS reviewed_at
            FROM statements s
            JOIN reporting_entities e ON e.entity_id=s.reporting_entity_id
            WHERE s.statement_id=?
            """,
            (statement_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        extraction_id = _current_extraction_id(conn, statement_id)
        extraction = None
        if extraction_id is not None:
            extraction = conn.execute(
                "SELECT statement_extraction_id, extraction_status, run_id, created_at "
                "FROM statement_extractions WHERE statement_id=? AND statement_extraction_id=?",
                (statement_id, extraction_id),
            ).fetchone()
        result["statement_extraction_id"] = extraction_id
        result["extraction_status"] = extraction["extraction_status"] if extraction else None
        result["extraction_run_id"] = extraction["run_id"] if extraction else None
        result["extraction_created_at"] = extraction["created_at"] if extraction else None
        binding = _active_review_binding(conn, statement_id)
        latest_completed = _latest_completed_extraction_id(conn, statement_id)
        result["review_binding_statement_extraction_id"] = (
            binding["statement_extraction_id"] if binding is not None else None
        )
        result["latest_completed_statement_extraction_id"] = latest_completed
        result["review_extraction_outdated"] = bool(
            binding is not None
            and latest_completed is not None
            and binding["statement_extraction_id"] != latest_completed
        )
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
        current_review = conn.execute(
            "SELECT review_status FROM statement_reviews WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        extraction_id: str | None = None
        binding_id: str | None = None
        voided_binding_id: str | None = None
        if review_status == "reviewed":
            if current_review is not None and current_review["review_status"] == "reviewed":
                raise ValueError(
                    "statement is already reviewed; reopen it before signing off extraction data"
                )
            if (
                readiness is not None
                and not readiness["mapping_ready"]
            ):
                raise ValueError(
                    "resolve mapping conflict/unmatched before marking reviewed: "
                    f"{readiness['mapping_blockers']}"
                )
            extraction_id = _latest_completed_extraction_id(conn, statement_id)
            if extraction_id is None:
                raise ValueError("statement has no completed extraction to review")
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
        if review_status == "reviewed" and extraction_id is not None:
            binding_id = _bind_review_extraction(
                conn, statement_id, extraction_id, actor=actor, note=note
            )
        else:
            previous_binding = _active_review_binding(conn, statement_id)
            voided_binding_id = (
                previous_binding["binding_id"] if previous_binding is not None else None
            )
            conn.execute(
                "UPDATE statement_review_bindings SET binding_status='void', "
                "voided_by=?, voided_at=? WHERE statement_id=? AND binding_status='active'",
                (actor, now(), statement_id),
            )
        event_id = stable_id("workbench_statement_review", statement_id, review_status, now())
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, statement_extraction_id, actor_id, "
            "after_json, details_json, reason, created_at"
            ") VALUES (?, 'workbench_statement_reviewed', ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, statement_id, extraction_id, actor,
                json.dumps({
                    "review_status": review_status,
                    "note": note,
                    "statement_extraction_id": extraction_id,
                    "review_binding_id": binding_id,
                    "voided_review_binding_id": voided_binding_id,
                }, ensure_ascii=False),
                json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                note or "Statement reviewed in Workbench",
                now(),
            ),
        )
        conn.commit()
        return {
            "statement_id": statement_id,
            "review_status": review_status,
            "reviewed_by": actor,
            "statement_extraction_id": extraction_id,
            "review_binding_id": binding_id,
        }
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
        binding = _active_review_binding(conn, statement_id)
        if binding is None:
            raise ValueError(
                "reviewer sign-off is not bound to an extraction; reopen and mark reviewed again"
            )
        extraction_id = binding["statement_extraction_id"]
        latest_completed = _latest_completed_extraction_id(conn, statement_id)
        if latest_completed is not None and latest_completed != extraction_id:
            raise ValueError(
                "a newer completed extraction exists since this sign-off; "
                "reopen and mark the current extraction reviewed before adoption"
            )
        extraction = conn.execute(
            "SELECT statement_extraction_id, extraction_status FROM statement_extractions "
            "WHERE statement_id=? AND statement_extraction_id=?",
            (statement_id, extraction_id),
        ).fetchone()
        if extraction is None or extraction["extraction_status"] != "completed":
            raise ValueError("the reviewed extraction is no longer completed")
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
        event_id = stable_id(
            "workbench_adopt",
            statement_id,
            extraction["statement_extraction_id"],
            binding["binding_id"],
            VERSION,
        )
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
                    "review_binding_id": binding["binding_id"],
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
            "review_binding_id": binding["binding_id"],
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


_MONEY_RE = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.(\d+))?$")


def _money(minor: int | None, places: int | None) -> str:
    if minor is None:
        return ""
    from decimal import Decimal
    decimal_places = _normalize_places(places)
    value = Decimal(minor) / (Decimal(10) ** decimal_places)
    return format(value, f",.{decimal_places}f")


def _parse_money(value: str | None, places: int | None = 2) -> int:
    """Parse a canonical Workbench amount into minor units.

    Input contract: optional sign, digits or comma thousands groups, and an
    optional dot decimal point (for example ``1234.56`` or ``1,234.56``).
    Decimal commas are deliberately rejected because they are ambiguous
    without page-level evidence; use the value-state fields for dash/blank.
    """
    if value is None or str(value).strip() == "":
        raise ValueError("amount is required when state is 'amount'")
    raw = str(value).strip()
    if raw in {"-", "–", "—", "−"}:
        raise ValueError("dash is not an amount; use state 'dash' instead")
    match = _MONEY_RE.fullmatch(raw)
    if match is None:
        raise ValueError(
            f"invalid amount {value!r}; use dot decimals such as 1234.56 "
            "(comma is a thousands separator, not a decimal point)"
        )
    from decimal import Decimal
    amount = Decimal(raw.replace(",", ""))
    decimal_places = _normalize_places(places)
    scaled = amount * (Decimal(10) ** decimal_places)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(
            f"amount has more than {decimal_places} decimal places: {value!r}"
        )
    return int(integral)


EFFECTIVE_ROW_SQL = f"""
SELECT
    sr.statement_id,
    sr.statement_row_id,
    sr.statement_order,
    sr.row_type,
    sr.parent_context_status,
    sr.statement_extraction_id,
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
    CASE WHEN EXISTS (SELECT 1 FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='debit_minor'
        AND fv.verification_status='verified')
      THEN (SELECT CAST(fv.verified_value_text AS INTEGER) FROM field_verifications fv
             WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='debit_minor'
               AND fv.verification_status='verified')
      ELSE sr.debit_minor END AS effective_debit_minor,
    COALESCE((SELECT fv.verified_value_text FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='debit_value_state'
        AND fv.verification_status='verified'), sr.debit_value_state) AS effective_debit_state,
    CASE WHEN EXISTS (SELECT 1 FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='credit_minor'
        AND fv.verification_status='verified')
      THEN (SELECT CAST(fv.verified_value_text AS INTEGER) FROM field_verifications fv
             WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='credit_minor'
               AND fv.verification_status='verified')
      ELSE sr.credit_minor END AS effective_credit_minor,
    COALESCE((SELECT fv.verified_value_text FROM field_verifications fv
      WHERE fv.statement_row_id=sr.statement_row_id AND fv.field_name='credit_value_state'
        AND fv.verification_status='verified'), sr.credit_value_state) AS effective_credit_state,
    'ocr' AS row_origin,
    NULL AS standard_item_id
FROM statement_rows sr
JOIN extracted_rows er ON er.extracted_row_id=sr.extracted_row_id
JOIN document_pages dp ON dp.document_page_id=er.document_page_id
WHERE sr.statement_extraction_id = {_current_extraction_sql('sr')}
  AND NOT EXISTS (
    SELECT 1 FROM statement_row_adjustments a
    WHERE a.adjustment_type='exclude'
      AND a.adjustment_status='active'
      AND a.target_statement_row_id=sr.statement_row_id
)
UNION ALL
SELECT
    a.statement_id,
    a.adjustment_id AS statement_row_id,
    a.display_order AS statement_order,
    a.row_type,
    'unknown' AS parent_context_status,
    NULL AS statement_extraction_id,
    NULL AS label_raw,
    NULL AS debit_raw,
    NULL AS credit_raw,
    a.pdf_page,
    a.label_normalized AS base_label,
    a.debit_minor AS base_debit_minor,
    a.debit_value_state AS base_debit_state,
    a.credit_minor AS base_credit_minor,
    a.credit_value_state AS base_credit_state,
    a.label_normalized AS effective_label,
    a.debit_minor AS effective_debit_minor,
    a.debit_value_state AS effective_debit_state,
    a.credit_minor AS effective_credit_minor,
    a.credit_value_state AS effective_credit_state,
    'manual' AS row_origin,
    a.standard_item_id
FROM statement_row_adjustments a
WHERE a.adjustment_type='insert'
  AND a.adjustment_status='active'
"""


def _standard_item_paths(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["standard_item_id"]: row for row in rows}

    def path_for(row: dict[str, Any]) -> str:
        parts = []
        current = row
        seen = set()
        while current and current["standard_item_id"] not in seen:
            seen.add(current["standard_item_id"])
            label = (
                text(current.get("canonical_zh"))
                or text(current.get("canonical_pt"))
                or current["item_key"]
            )
            parts.append(label)
            current = by_id.get(current.get("parent_standard_item_id"))
        return " > ".join(reversed(parts))

    for row in rows:
        row["path"] = path_for(row)
    return rows


def standard_items(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT standard_item_id, item_key, canonical_zh, canonical_pt, "
            "parent_standard_item_id, category, classification_status, "
            "item_status, merged_into_standard_item_id "
            "FROM standard_items "
            "ORDER BY item_key"
        )]
    finally:
        conn.close()
    rows = _standard_item_paths(rows)
    if include_inactive:
        return rows
    return [row for row in rows if row["item_status"] == "approved"]


def standard_item_admin_rows(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    """All catalogue items with lifecycle and usage counts for maintenance."""
    conn = connect_v3(db_path)
    try:
        rows = [dict(row) for row in conn.execute(
            """
            SELECT si.standard_item_id, si.item_key, si.canonical_zh,
                   si.canonical_zh_status, si.canonical_pt,
                   si.canonical_pt_status, si.canonical_en,
                   si.canonical_en_status, si.parent_standard_item_id,
                   si.category, si.classification_status, si.classification_source,
                   si.item_status, si.merged_into_standard_item_id,
                   si.created_at, si.notes,
                   parent.item_key AS merged_into_item_key,
                   (SELECT COUNT(*) FROM statement_row_mappings m
                     WHERE m.standard_item_id=si.standard_item_id
                       AND m.mapping_status='approved') AS approved_mapping_rows,
                   (SELECT COUNT(*) FROM statement_row_mappings m
                     WHERE m.standard_item_id=si.standard_item_id
                       AND m.mapping_status='proposed') AS proposed_mapping_rows,
                   (SELECT COUNT(*) FROM statement_row_adjustments a
                     WHERE a.standard_item_id=si.standard_item_id
                       AND a.adjustment_type='insert'
                       AND a.adjustment_status='active') AS manual_rows
            FROM standard_items si
            LEFT JOIN standard_items parent
              ON parent.standard_item_id=si.merged_into_standard_item_id
            ORDER BY si.item_key
            """
        )]
    finally:
        conn.close()
    return _standard_item_paths(rows)


def _get_standard_item(
    conn: sqlite3.Connection, identifier: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM standard_items WHERE standard_item_id=?",
        (identifier,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM standard_items WHERE item_key=?",
            (identifier,),
        ).fetchone()
    if row is None:
        raise LookupError("standard item not found")
    return row


def _standard_item_usage(conn: sqlite3.Connection, standard_item_id: str) -> dict[str, int]:
    return {
        "approved_mapping_rows": _count(
            conn,
            "SELECT COUNT(*) FROM statement_row_mappings "
            "WHERE standard_item_id=? AND mapping_status='approved'",
            (standard_item_id,),
        ),
        "proposed_mapping_rows": _count(
            conn,
            "SELECT COUNT(*) FROM statement_row_mappings "
            "WHERE standard_item_id=? AND mapping_status='proposed'",
            (standard_item_id,),
        ),
        "manual_rows": _count(
            conn,
            "SELECT COUNT(*) FROM statement_row_adjustments "
            "WHERE standard_item_id=? AND adjustment_type='insert' "
            "AND adjustment_status='active'",
            (standard_item_id,),
        ),
    }


def _standard_item_audit(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    actor: str,
    note: str,
    source: str,
) -> None:
    conn.execute(
        "INSERT INTO audit_events("
        "audit_event_id, event_type, standard_item_id, actor_id, "
        "before_json, after_json, details_json, reason, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()), event_type, after["standard_item_id"], actor,
            json.dumps(before, ensure_ascii=False, default=str) if before else None,
            json.dumps(after, ensure_ascii=False, default=str),
            json.dumps({"source": source, "version": VERSION}, ensure_ascii=False),
            note,
            now(),
        ),
    )


def approve_standard_item(
    identifier: str,
    *,
    actor: str,
    note: str = "",
    source: str = "workbench",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Approve a draft catalogue item; merged/retired items are refused."""
    conn = connect_v3(db_path, write=True)
    try:
        row = _get_standard_item(conn, identifier)
        if row["item_status"] == "approved":
            return {**dict(row), "changed": False}
        if row["item_status"] == "merged":
            target = conn.execute(
                "SELECT item_key FROM standard_items WHERE standard_item_id=?",
                (row["merged_into_standard_item_id"],),
            ).fetchone()
            raise InactiveStandardItemError(
                "standard item is merged into "
                f"{target['item_key'] if target else 'another item'}; "
                "approve the target instead"
            )
        if row["item_status"] == "retired":
            raise ValueError("retired standard item must be reopened before approval")
        if not text(row["canonical_zh"]) and not text(row["canonical_pt"]):
            raise ValueError(
                "at least one canonical Chinese or Portuguese name is required"
            )
        for name_field, status_field in (
            ("canonical_zh", "canonical_zh_status"),
            ("canonical_pt", "canonical_pt_status"),
            ("canonical_en", "canonical_en_status"),
        ):
            if text(row[name_field]) and row[status_field] == "rejected":
                raise ValueError(
                    f"{name_field} is rejected; clear or correct it before approval"
                )
        before = dict(row)
        zh_status = "approved" if text(row["canonical_zh"]) else row["canonical_zh_status"]
        pt_status = "approved" if text(row["canonical_pt"]) else row["canonical_pt_status"]
        en_status = "approved" if text(row["canonical_en"]) else row["canonical_en_status"]
        conn.execute(
            "UPDATE standard_items SET item_status='approved', "
            "canonical_zh_status=?, canonical_pt_status=?, canonical_en_status=? "
            "WHERE standard_item_id=?",
            (zh_status, pt_status, en_status, row["standard_item_id"]),
        )
        after = dict(_get_standard_item(conn, row["standard_item_id"]))
        _standard_item_audit(
            conn, event_type="standard_item_approved", before=before, after=after,
            actor=actor, note=note or "Standard item approved", source=source,
        )
        conn.commit()
        return {
            **after,
            "changed": True,
            "usage": _standard_item_usage(conn, row["standard_item_id"]),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def retire_standard_item(
    identifier: str,
    *,
    actor: str,
    note: str = "",
    source: str = "workbench",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Retire an item. Existing mappings stay as evidence but become invalid."""
    conn = connect_v3(db_path, write=True)
    try:
        row = _get_standard_item(conn, identifier)
        if row["item_status"] == "merged":
            raise ValueError("merged standard items cannot be retired")
        if row["item_status"] == "retired":
            return {**dict(row), "changed": False}
        before = dict(row)
        conn.execute(
            "UPDATE standard_items SET item_status='retired' WHERE standard_item_id=?",
            (row["standard_item_id"],),
        )
        after = dict(_get_standard_item(conn, row["standard_item_id"]))
        _standard_item_audit(
            conn, event_type="standard_item_retired", before=before, after=after,
            actor=actor, note=note or "Standard item retired", source=source,
        )
        conn.commit()
        return {
            **after,
            "changed": True,
            "usage": _standard_item_usage(conn, row["standard_item_id"]),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reopen_standard_item(
    identifier: str,
    *,
    actor: str,
    note: str = "",
    source: str = "workbench",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Move a retired item back to draft so it can be corrected and approved."""
    conn = connect_v3(db_path, write=True)
    try:
        row = _get_standard_item(conn, identifier)
        if row["item_status"] != "retired":
            if row["item_status"] == "draft":
                return {**dict(row), "changed": False}
            raise ValueError("only retired standard items can be reopened")
        before = dict(row)
        conn.execute(
            "UPDATE standard_items SET item_status='draft' WHERE standard_item_id=?",
            (row["standard_item_id"],),
        )
        after = dict(_get_standard_item(conn, row["standard_item_id"]))
        _standard_item_audit(
            conn, event_type="standard_item_reopened", before=before, after=after,
            actor=actor, note=note or "Standard item reopened", source=source,
        )
        conn.commit()
        return {**after, "changed": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_standard_item(
    *,
    actor: str,
    canonical_zh: str | None = None,
    canonical_pt: str | None = None,
    canonical_en: str | None = None,
    item_key: str | None = None,
    parent_standard_item_id: str | None = None,
    category: str | None = None,
    note: str = "",
    source: str = "workbench",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Create a draft catalogue item. Approval is a separate explicit action."""
    zh = text(canonical_zh)
    pt = text(canonical_pt)
    en = text(canonical_en)
    if not zh and not pt and not en:
        raise ValueError("at least one canonical name (zh, pt or en) is required")
    category_value = text(category)
    if not category_value:
        raise ValueError("classification/category is required for a new standard item")
    classification_status = (
        "excluded" if category_value.casefold() in {"排除", "excluded"} else "classified"
    )
    key = text(item_key) or f"user:{zh or pt or en}"
    key = re.sub(r"\s+", "", key)
    conn = connect_v3(db_path, write=True)
    try:
        if conn.execute(
            "SELECT 1 FROM standard_items WHERE item_key=?", (key,)
        ).fetchone():
            raise ValueError(f"standard item key already exists: {key}")
        if parent_standard_item_id:
            parent = conn.execute(
                "SELECT standard_item_id, item_status FROM standard_items "
                "WHERE standard_item_id=?",
                (parent_standard_item_id,),
            ).fetchone()
            if parent is None:
                raise LookupError("parent standard item not found")
            if parent["item_status"] in {"merged", "retired"}:
                raise ValueError("parent standard item is not active; choose an approved or draft parent")
        item_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO standard_items(
                standard_item_id, item_key, canonical_zh, canonical_zh_status,
                canonical_pt, canonical_pt_status, canonical_en,
                canonical_en_status, parent_standard_item_id, category,
                classification_status, classification_source,
                item_status, created_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual:workbench', 'draft', ?, ?)
            """,
            (
                item_id, key,
                zh or None, "draft" if zh else "missing",
                pt or None, "draft" if pt else "missing",
                en or None, "draft" if en else "missing",
                parent_standard_item_id or None, category_value,
                classification_status,
                now(), note or None,
            ),
        )
        after = dict(_get_standard_item(conn, item_id))
        _standard_item_audit(
            conn, event_type="standard_item_created", before=None, after=after,
            actor=actor, note=note or "Standard item created as draft", source=source,
        )
        conn.commit()
        return {**after, "created": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def _mapping_info(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    statement_row_id = row["statement_row_id"]
    if row.get("row_origin") == "manual":
        item_id = row.get("standard_item_id") or ""
        if item_id:
            item = conn.execute(
                "SELECT standard_item_id, item_key, canonical_zh, canonical_pt, item_status "
                "FROM standard_items WHERE standard_item_id=?",
                (item_id,),
            ).fetchone()
            if item is not None:
                candidate = {
                    "mapping_id": "",
                    "statement_row_id": statement_row_id,
                    "standard_item_id": item["standard_item_id"],
                    "mapping_status": "approved",
                    "conflict_group_id": None,
                    "match_method": "manual",
                    "match_score": 1.0,
                    "mapping_version": "manual-row",
                    "item_key": item["item_key"],
                    "canonical_zh": item["canonical_zh"],
                    "canonical_pt": item["canonical_pt"],
                    "item_status": item["item_status"],
                }
                return {
                    "mapping_status": "approved",
                    "standard_item_id": item["standard_item_id"],
                    "standard_item_key": item["item_key"],
                    "standard_item_status": item["item_status"],
                    "standardised_label": text(item["canonical_zh"]) or text(item["canonical_pt"]),
                    "mapping_candidates": [candidate],
                    "mapping_conflict": False,
                    "mapping_conflict_count": 0,
                    "row_origin": "manual",
                }
        return {
            "mapping_status": "unmatched",
            "standard_item_id": "",
            "standard_item_key": "",
            "standard_item_status": "",
            "standardised_label": "",
            "mapping_candidates": [],
            "mapping_conflict": False,
            "mapping_conflict_count": 0,
            "row_origin": "manual",
        }

    mappings = [dict(row) for row in conn.execute(
        """
        SELECT m.mapping_id, m.statement_row_id, m.standard_item_id,
               m.mapping_status, m.conflict_group_id, m.match_method,
               m.match_score, m.mapping_version,
               si.item_key, si.canonical_zh, si.canonical_pt, si.item_status
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
    conflicted = [item for item in mappings if item["conflict_group_id"]]
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
        "standard_item_status": selected["item_status"] if selected else "",
        "standardised_label": (
            text(selected.get("canonical_zh")) or text(selected.get("canonical_pt"))
            if selected else ""
        ),
        "mapping_candidates": mappings,
        "mapping_conflict": bool(conflicted),
        "mapping_conflict_count": len(conflicted),
        "row_origin": "ocr",
    }


def review_queue() -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        rows = conn.execute(
            f"""
            WITH effective AS (
                {EFFECTIVE_ROW_SQL}
            ),
            approved_items AS (
                SELECT er.statement_id, er.statement_row_id,
                       COALESCE(
                           er.standard_item_id,
                           (SELECT m.standard_item_id FROM statement_row_mappings m
                             WHERE m.statement_row_id=er.statement_row_id
                               AND m.mapping_status='approved'
                             LIMIT 1)
                       ) AS standard_item_id
                FROM effective er
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
                  JOIN effective er3 ON er3.statement_row_id=fv.statement_row_id
                  WHERE er3.statement_id=s.statement_id) AS field_verifications,
                {_current_extraction_sql('s')} AS review_statement_extraction_id,
                (SELECT b.statement_extraction_id FROM statement_review_bindings b
                  WHERE b.statement_id=s.statement_id AND b.binding_status='active'
                  ORDER BY b.bound_at DESC, b.binding_id DESC LIMIT 1) AS bound_statement_extraction_id,
                (SELECT se.extraction_status FROM statement_extractions se
                  WHERE se.statement_extraction_id = {_current_extraction_sql('s')}) AS extraction_status,
                (SELECT se.statement_extraction_id FROM statement_extractions se
                  WHERE se.statement_id=s.statement_id AND se.extraction_status='completed'
                  ORDER BY se.created_at DESC, se.statement_extraction_id DESC LIMIT 1)
                  AS latest_completed_statement_extraction_id,
                COALESCE((SELECT rv.review_status FROM statement_reviews rv
                  WHERE rv.statement_id=s.statement_id), 'pending') AS review_status,
                (SELECT rv.reviewed_by FROM statement_reviews rv
                  WHERE rv.statement_id=s.statement_id) AS reviewed_by,
                (SELECT rv.reviewed_at FROM statement_reviews rv
                  WHERE rv.statement_id=s.statement_id) AS reviewed_at,
                (SELECT COUNT(*) FROM effective er2
                  WHERE er2.statement_id=s.statement_id
                    AND (er2.standard_item_id IS NOT NULL
                         OR EXISTS (SELECT 1 FROM statement_row_mappings m
                                     WHERE m.statement_row_id=er2.statement_row_id
                                       AND m.mapping_status IN ('proposed','approved')))) AS mapped_rows,
                (SELECT COUNT(*) FROM effective er2
                  WHERE er2.statement_id=s.statement_id
                    AND (er2.standard_item_id IS NOT NULL
                         OR EXISTS (SELECT 1 FROM statement_row_mappings m
                                     WHERE m.statement_row_id=er2.statement_row_id
                                       AND m.mapping_status='approved'))) AS approved_mapping_rows,
                (SELECT COUNT(*) FROM effective er2
                  WHERE er2.statement_id=s.statement_id
                    AND er2.standard_item_id IS NULL
                    AND EXISTS (SELECT 1 FROM statement_row_mappings m
                                 WHERE m.statement_row_id=er2.statement_row_id
                                   AND m.mapping_status='proposed')
                    AND NOT EXISTS (SELECT 1 FROM statement_row_mappings m3
                                     WHERE m3.statement_row_id=er2.statement_row_id
                                       AND m3.mapping_status='approved')) AS proposed_mapping_rows,
                (SELECT COUNT(*) FROM effective er2
                  WHERE er2.statement_id=s.statement_id
                    AND EXISTS (SELECT 1 FROM statement_row_mappings m
                                 WHERE m.statement_row_id=er2.statement_row_id
                                   AND m.conflict_group_id IS NOT NULL
                                   AND m.mapping_status IN ('proposed','approved'))) AS mapping_conflict_rows,
                (SELECT COUNT(*) FROM effective er2
                  WHERE er2.statement_id=s.statement_id
                    AND (
                      (er2.standard_item_id IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM standard_items si
                                       WHERE si.standard_item_id=er2.standard_item_id
                                         AND si.item_status='approved'))
                      OR
                      (er2.standard_item_id IS NULL
                       AND EXISTS (
                         SELECT 1 FROM statement_row_mappings m
                         JOIN standard_items si ON si.standard_item_id=m.standard_item_id
                         WHERE m.statement_row_id=er2.statement_row_id
                           AND m.mapping_status IN ('proposed','approved')
                           AND si.item_status <> 'approved'
                           AND (m.mapping_status='approved'
                                OR NOT EXISTS (
                                  SELECT 1 FROM statement_row_mappings m2
                                  WHERE m2.statement_row_id=er2.statement_row_id
                                    AND m2.mapping_status='approved'
                                ))
                       ))
                    )) AS invalid_mapping_rows,
                (SELECT COUNT(*) FROM (
                    SELECT ai2.standard_item_id
                    FROM approved_items ai2
                    WHERE ai2.statement_id=s.statement_id
                      AND ai2.standard_item_id IS NOT NULL
                    GROUP BY ai2.standard_item_id
                    HAVING COUNT(*) > 1
                )) AS duplicate_mapping_items
            FROM statements s
            JOIN reporting_entities e ON e.entity_id=s.reporting_entity_id
            LEFT JOIN effective er ON er.statement_id=s.statement_id
            WHERE s.statement_status NOT IN ('active', 'excluded')
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
                and int(data["duplicate_mapping_items"] or 0) == 0
                and int(data["invalid_mapping_rows"] or 0) == 0
                and data["unmapped_rows"] == 0
            )
            data["can_discard"] = data.get("review_status") in (None, "pending")
            data["mapping_blockers"] = ";".join(
                part for part in (
                    f"mapping_conflict={data['mapping_conflict_rows']}" if data["mapping_conflict_rows"] else "",
                    f"mapping_unmatched={data['unmapped_rows']}" if data["unmapped_rows"] else "",
                    f"duplicate_standard_item={data['duplicate_mapping_items']}" if data["duplicate_mapping_items"] else "",
                    f"invalid_standard_item={data['invalid_mapping_rows']}" if data["invalid_mapping_rows"] else "",
                ) if part
            )
            if not data["row_count"]:
                bucket = "no_rows"
            elif data["mapping_conflict_rows"]:
                bucket = "mapping_conflict"
            elif data["invalid_mapping_rows"]:
                bucket = "invalid_mapping"
            elif data["unmapped_rows"]:
                bucket = "mapping_unmatched"
            elif difference != 0:
                bucket = "unbalanced"
            elif data["missing_labels"] or data["issue_rows"]:
                bucket = "row_issues"
            else:
                bucket = "ready_for_adoption"
            data["review_extraction_outdated"] = bool(
                data["bound_statement_extraction_id"]
                and data["latest_completed_statement_extraction_id"]
                and data["bound_statement_extraction_id"]
                != data["latest_completed_statement_extraction_id"]
            )
            if data["review_status"] == "reviewed":
                if not data["bound_statement_extraction_id"]:
                    bucket = "review_binding_missing"
                elif data["review_extraction_outdated"]:
                    bucket = "review_outdated"
            data["review_bucket"] = bucket
            data["priority"] = {
                "mapping_conflict": 1, "mapping_unmatched": 1, "invalid_mapping": 1,
                "unbalanced": 1, "no_rows": 1, "review_binding_missing": 1,
                "review_outdated": 1, "row_issues": 2,
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
            f"SELECT * FROM ({EFFECTIVE_ROW_SQL}) "
            "WHERE statement_id=? ORDER BY statement_order, statement_row_id LIMIT ?",
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
            data["verified_fields"] = [
                verified["field_name"]
                for verified in conn.execute(
                    "SELECT field_name FROM field_verifications "
                    "WHERE statement_row_id=? AND verification_status='verified' "
                    "ORDER BY field_name",
                    (data["statement_row_id"],),
                )
            ]
            data.update(_mapping_info(conn, data))
            result.append(data)
        return result
    finally:
        conn.close()


VERIFICATION_FIELDS = {
    "label_normalized", "debit_minor", "debit_value_state",
    "credit_minor", "credit_value_state",
}


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
    verified_fields: list[str] | None = None,
) -> dict[str, Any]:
    if debit_state not in {"amount", "dash", "blank", "unreadable", "not_applicable", "ambiguous"}:
        raise ValueError("invalid debit state")
    if credit_state not in {"amount", "dash", "blank", "unreadable", "not_applicable", "ambiguous"}:
        raise ValueError("invalid credit state")
    requested_fields: set[str] | None = None
    if verified_fields is not None:
        requested_fields = set(verified_fields)
        unknown = requested_fields - VERIFICATION_FIELDS
        if unknown:
            raise ValueError(f"unknown verification field(s): {', '.join(sorted(unknown))}")
    conn = connect_v3(write=True)
    try:
        _check_editable(conn, statement_id)
        statement = conn.execute(
            "SELECT amount_decimal_places FROM statements WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if statement is None:
            raise LookupError("statement not found")
        places = _normalize_places(statement["amount_decimal_places"])
        debit_minor = _parse_money(debit, places) if debit_state == "amount" else None
        credit_minor = _parse_money(credit, places) if credit_state == "amount" else None

        adjustment = conn.execute(
            "SELECT * FROM statement_row_adjustments "
            "WHERE adjustment_id=? AND statement_id=? AND adjustment_type='insert' "
            "AND adjustment_status='active'",
            (statement_row_id, statement_id),
        ).fetchone()
        if adjustment is not None:
            before = dict(adjustment)
            conn.execute(
                "UPDATE statement_row_adjustments SET label_normalized=?, "
                "debit_minor=?, debit_value_state=?, credit_minor=?, credit_value_state=? "
                "WHERE adjustment_id=?",
                (
                    "" if label is None else text(label),
                    debit_minor, debit_state,
                    credit_minor, credit_state,
                    statement_row_id,
                ),
            )
            event_id = stable_id(
                "workbench_manual_row_edit", statement_row_id, VERSION, now()
            )
            conn.execute(
                "INSERT INTO audit_events("
                "audit_event_id, event_type, statement_id, actor_id, "
                "before_json, after_json, details_json, reason, created_at"
                ") VALUES (?, 'workbench_manual_row_edited', ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id, statement_id, actor,
                    json.dumps(before, ensure_ascii=False, default=str),
                    json.dumps({
                        "label": label, "debit_minor": debit_minor,
                        "debit_state": debit_state, "credit_minor": credit_minor,
                        "credit_state": credit_state,
                        "verified_fields": sorted(requested_fields or []),
                        "row_origin": "manual",
                    }, ensure_ascii=False),
                    json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                    note or "Workbench manual row edit",
                    now(),
                ),
            )
            conn.commit()
            rows = review_rows(statement_id)
            for item in rows:
                if item["statement_row_id"] == statement_row_id:
                    return item
            raise LookupError("statement row not found after verification")

        row = conn.execute(
            "SELECT statement_id, label_normalized, debit_minor, debit_value_state, "
            "credit_minor, credit_value_state FROM statement_rows WHERE statement_row_id=?",
            (statement_row_id,),
        ).fetchone()
        if row is None or row["statement_id"] != statement_id:
            raise LookupError("statement row not found")
        before = dict(row)
        effective = conn.execute(
            f"SELECT * FROM ({EFFECTIVE_ROW_SQL}) WHERE statement_row_id=?",
            (statement_row_id,),
        ).fetchone()
        if effective is None:
            raise LookupError("statement row is not part of the current extraction")
        proposed: dict[str, Any] = {
            "label_normalized": "" if label is None else text(label),
            "debit_minor": debit_minor,
            "debit_value_state": debit_state,
            "credit_minor": credit_minor,
            "credit_value_state": credit_state,
        }
        current: dict[str, Any] = {
            "label_normalized": effective["effective_label"],
            "debit_minor": effective["effective_debit_minor"],
            "debit_value_state": effective["effective_debit_state"],
            "credit_minor": effective["effective_credit_minor"],
            "credit_value_state": effective["effective_credit_state"],
        }
        # A changed value is an explicit human edit and must become effective.
        # Untouched fields are only recorded when the reviewer ticks them.
        changed_fields = {
            field_name for field_name in VERIFICATION_FIELDS
            if proposed[field_name] != current[field_name]
        }
        selected_fields = (
            changed_fields
            if requested_fields is None
            else requested_fields | changed_fields
        )
        for field_name in sorted(selected_fields):
            original = before[field_name]
            value = proposed[field_name]
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
                    None if value is None else str(value),
                    actor, now(), note or "Workbench row verification", now(),
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
                    "verified_fields": sorted(selected_fields),
                    "changed_fields": sorted(changed_fields),
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


def _standard_item_conflict(
    conn: sqlite3.Connection,
    statement_id: str,
    standard_item_id: str,
    *,
    exclude_row_id: str | None = None,
    exclude_adjustment_id: str | None = None,
) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT sr.statement_order, sr.label_normalized AS label
        FROM statement_row_mappings m
        JOIN statement_rows sr ON sr.statement_row_id=m.statement_row_id
        WHERE sr.statement_id=?
          AND sr.statement_extraction_id={_current_extraction_sql('sr')}
          AND m.standard_item_id=?
          AND m.mapping_status='approved'
          AND (? IS NULL OR m.statement_row_id<>?)
        UNION ALL
        SELECT a.display_order AS statement_order, a.label_normalized AS label
        FROM statement_row_adjustments a
        WHERE a.statement_id=?
          AND a.adjustment_type='insert'
          AND a.adjustment_status='active'
          AND a.standard_item_id=?
          AND (? IS NULL OR a.adjustment_id<>?)
        LIMIT 1
        """,
        (
            statement_id, standard_item_id, exclude_row_id, exclude_row_id,
            statement_id, standard_item_id, exclude_adjustment_id, exclude_adjustment_id,
        ),
    ).fetchone()
    return dict(row) if row is not None else None


def statement_used_standard_items(statement_id: str) -> list[str]:
    """Approved standard items already used in one statement."""
    conn = connect_v3()
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT m.standard_item_id
            FROM statement_row_mappings m
            JOIN statement_rows sr ON sr.statement_row_id=m.statement_row_id
            WHERE sr.statement_id=?
              AND sr.statement_extraction_id={_current_extraction_sql('sr')}
              AND m.mapping_status='approved'
            UNION
            SELECT DISTINCT a.standard_item_id
            FROM statement_row_adjustments a
            WHERE a.statement_id=?
              AND a.adjustment_type='insert'
              AND a.adjustment_status='active'
              AND a.standard_item_id IS NOT NULL
            """,
            (statement_id, statement_id),
        ).fetchall()
        return [row["standard_item_id"] for row in rows]
    finally:
        conn.close()


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
        if row is not None and row["statement_id"] != statement_id:
            raise LookupError("statement row not found")
        item = _check_standard_item_usable(conn, standard_item_id)
        _check_editable(conn, statement_id)
        conflict = _standard_item_conflict(
            conn, statement_id, standard_item_id,
            exclude_row_id=statement_row_id,
            exclude_adjustment_id=statement_row_id,
        )
        if conflict is not None:
            raise ValueError(
                "standard item is already mapped in this statement "
                f"(row {conflict.get('statement_order')}: {conflict.get('label') or ''})"
            )

        adjustment = conn.execute(
            "SELECT * FROM statement_row_adjustments "
            "WHERE adjustment_id=? AND statement_id=? AND adjustment_type='insert' "
            "AND adjustment_status='active'",
            (statement_row_id, statement_id),
        ).fetchone()
        if adjustment is not None:
            conn.execute(
                "UPDATE statement_row_adjustments SET standard_item_id=? "
                "WHERE adjustment_id=?",
                (standard_item_id, statement_row_id),
            )
            event_id = stable_id(
                "workbench_manual_row_mapping", statement_row_id, standard_item_id,
                VERSION, now(),
            )
            conn.execute(
                "INSERT INTO audit_events("
                "audit_event_id, event_type, statement_id, "
                "standard_item_id, actor_id, before_json, after_json, "
                "details_json, reason, created_at"
                ") VALUES (?, 'workbench_manual_row_mapped', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id, statement_id, standard_item_id, actor,
                    json.dumps(
                        {"standard_item_id": adjustment["standard_item_id"]},
                        ensure_ascii=False,
                    ),
                    json.dumps({"standard_item_id": standard_item_id}, ensure_ascii=False),
                    json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                    note or "Manual row standard item selected",
                    now(),
                ),
            )
            conn.commit()
            rows = review_rows(statement_id)
            for item_row in rows:
                if item_row["statement_row_id"] == statement_row_id:
                    return item_row
            raise LookupError("statement row not found after mapping")

        if row is None:
            raise LookupError("statement row not found")
        before = [
            dict(mapping)
            for mapping in conn.execute(
                "SELECT mapping_id, standard_item_id, mapping_status FROM statement_row_mappings "
                "WHERE statement_row_id=? AND mapping_status IN ('proposed','approved') "
                "ORDER BY CASE mapping_status WHEN 'approved' THEN 0 ELSE 1 END, "
                "created_at, mapping_id",
                (statement_row_id,),
            )
        ]
        existing = next(
            (
                mapping for mapping in before
                if mapping["standard_item_id"] == standard_item_id
            ),
            None,
        )
        # Reject every other candidate, including a duplicate proposal for the
        # approved standard item left behind by an earlier approval.
        conn.execute(
            "UPDATE statement_row_mappings SET mapping_status='rejected', "
            "conflict_group_id=NULL "
            "WHERE statement_row_id=? AND mapping_id<>? "
            "AND mapping_status IN ('proposed','approved')",
            (statement_row_id, existing["mapping_id"] if existing else ""),
        )
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
                          'p1-review-1', 'workbench_manual', ?, ?, ?, ?, ?)
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


BULK_APPROVAL_SCOPES = ("reviewed", "editable", "all")


def bulk_approve_proposed_mappings(
    *,
    actor: str,
    note: str = "",
    statement_ids: list[str] | None = None,
    scope: str = "reviewed",
    allow_locked: bool = False,
    apply: bool = False,
    collect_skipped: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Approve unambiguous auto-mapped proposals in one audited pass.

    This is the "I already reviewed the statement" shortcut: it writes a real
    ``mapping_status='approved'`` with ``reviewed_by``/``reviewed_at`` and an
    audit event, so queries and dashboards keep the proposed/approved
    distinction. It never changes amounts, extractions or review state, and it
    never marks a row financially verified.

    Only the current effective rows are considered. A row is skipped when:

    * it already has an approved mapping;
    * it has no proposal or more than one proposal (conflict);
    * the same standard item is proposed on another row of the same statement
      (one item may only be approved once per statement);
    * the item is already approved or inserted on another row;
    * the item is missing, not approved, merged or retired;
    * it is a manual insert whose item is stored on the adjustment.

    ``scope``: ``reviewed`` = reviewed or adopted statements (default),
    ``editable`` = statements still under review, ``all`` = every non-excluded
    statement. Reviewed/adopted statements are skipped unless ``allow_locked``
    is set, because they are normally read-only.
    """
    if scope not in BULK_APPROVAL_SCOPES:
        raise ValueError(f"invalid scope: {scope}")
    if not text(actor):
        raise ValueError("actor is required")

    conn = connect_v3(db_path, write=apply)
    try:
        params: list[Any] = []
        scope_sql = (
            "SELECT s.statement_id, s.statement_status, "
            "COALESCE(r.review_status, 'pending') AS review_status "
            "FROM statements s "
            "LEFT JOIN statement_reviews r ON r.statement_id = s.statement_id "
            "WHERE s.statement_status NOT IN ('excluded', 'merged', 'retired')"
        )
        if statement_ids:
            placeholders = ",".join("?" for _ in statement_ids)
            scope_sql += f" AND s.statement_id IN ({placeholders})"
            params.extend(statement_ids)
        if scope == "reviewed":
            scope_sql += " AND (s.statement_status = 'active' OR r.review_status = 'reviewed')"
        elif scope == "editable":
            scope_sql += (
                " AND s.statement_status <> 'active' "
                " AND COALESCE(r.review_status, 'pending') <> 'reviewed'"
            )
        scope_sql += " ORDER BY s.statement_id"
        statements = [dict(row) for row in conn.execute(scope_sql, params)]

        summary: dict[str, Any] = {
            "dry_run": not apply,
            "scope": scope,
            "allow_locked": allow_locked,
            "statements_in_scope": len(statements),
            "locked_statements_skipped": 0,
            "rows_in_scope": 0,
            "approved": 0,
            "approved_by_statement": {},
            "skip_counts": {},
            "skipped_examples": [],
            "skipped": [],
        }
        if not statements:
            return summary

        allowed: list[dict[str, Any]] = []
        for statement in statements:
            locked = (
                statement["statement_status"] == "active"
                or statement["review_status"] == "reviewed"
            )
            if locked and not allow_locked:
                summary["locked_statements_skipped"] += 1
                continue
            allowed.append(statement)
        summary["statements_in_scope"] = len(allowed)
        if not allowed:
            return summary

        scope_ids = [statement["statement_id"] for statement in allowed]
        placeholders = ",".join("?" for _ in scope_ids)
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT statement_row_id, statement_id, statement_order, row_origin, "
                f"effective_label, standard_item_id FROM ({EFFECTIVE_ROW_SQL}) "
                f"WHERE statement_id IN ({placeholders}) "
                "ORDER BY statement_id, statement_order, statement_row_id",
                scope_ids,
            )
        ]
        summary["rows_in_scope"] = len(rows)
        if not rows:
            return summary

        effective_row_ids = {row["statement_row_id"] for row in rows}
        candidates: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute(
            "SELECT m.statement_row_id, m.mapping_id, m.standard_item_id, "
            "m.mapping_status FROM statement_row_mappings m "
            "JOIN statement_rows sr ON sr.statement_row_id = m.statement_row_id "
            f"WHERE sr.statement_id IN ({placeholders}) "
            "AND m.mapping_status IN ('proposed', 'approved')",
            scope_ids,
        ):
            if row["statement_row_id"] not in effective_row_ids:
                continue
            candidates.setdefault(row["statement_row_id"], []).append(dict(row))

        items = {
            row["standard_item_id"]: dict(row)
            for row in conn.execute(
                "SELECT standard_item_id, item_key, item_status, "
                "merged_into_standard_item_id FROM standard_items"
            )
        }

        skip_counts: Counter[str] = Counter()
        examples: list[dict[str, Any]] = []
        all_skipped: list[dict[str, Any]] = []
        rows_by_statement: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            rows_by_statement.setdefault(row["statement_id"], []).append(row)

        def skip(reason: str, row: dict[str, Any], item_id: str | None = None) -> None:
            skip_counts[reason] += 1
            entry = {
                "statement_id": row["statement_id"],
                "statement_row_id": row["statement_row_id"],
                "statement_order": row["statement_order"],
                "label": row.get("effective_label") or row.get("label") or "",
                "standard_item_id": item_id,
                "standard_item_key": (
                    items[item_id]["item_key"] if item_id and item_id in items else None
                ),
                "reason": reason,
            }
            if collect_skipped:
                all_skipped.append(entry)
            if len(examples) < 25:
                examples.append(entry)

        approvals: list[dict[str, Any]] = []
        for statement_id, statement_rows in rows_by_statement.items():
            used_items = {
                row["standard_item_id"]
                for row in statement_rows
                if row["row_origin"] == "manual" and row["standard_item_id"]
            }
            for row in statement_rows:
                for candidate in candidates.get(row["statement_row_id"], []):
                    if candidate["mapping_status"] == "approved" and candidate["standard_item_id"]:
                        used_items.add(candidate["standard_item_id"])

            eligible: dict[str, list[dict[str, Any]]] = {}
            for row in statement_rows:
                row_candidates = candidates.get(row["statement_row_id"], [])
                if any(candidate["mapping_status"] == "approved" for candidate in row_candidates):
                    skip_counts["already_approved"] += 1
                    continue
                if row["row_origin"] == "manual":
                    skip("manual_row", row)
                    continue
                if not row_candidates:
                    skip("no_candidate", row)
                    continue
                if len(row_candidates) > 1:
                    skip("multiple_candidates", row)
                    continue
                candidate = row_candidates[0]
                item = items.get(candidate["standard_item_id"])
                if item is None:
                    skip("item_missing", row, candidate["standard_item_id"])
                    continue
                if item["item_status"] != "approved" or item["merged_into_standard_item_id"]:
                    skip("item_not_active", row, candidate["standard_item_id"])
                    continue
                eligible.setdefault(candidate["standard_item_id"], []).append(
                    {**candidate, "statement_id": statement_id,
                     "label": row.get("effective_label") or "",
                     "statement_order": row["statement_order"]}
                )

            for item_id, entries in eligible.items():
                if len(entries) > 1:
                    for entry in entries:
                        skip("duplicate_item_in_statement", entry, item_id)
                    continue
                if item_id in used_items:
                    skip("item_already_used", entries[0], item_id)
                    continue
                entry = entries[0]
                approvals.append({
                    "statement_id": statement_id,
                    "statement_row_id": entry["statement_row_id"],
                    "mapping_id": entry["mapping_id"],
                    "standard_item_id": item_id,
                })

        summary["skip_counts"] = dict(sorted(skip_counts.items()))
        summary["skipped_examples"] = examples
        summary["skipped"] = all_skipped
        summary["approved"] = len(approvals)
        if not approvals:
            return summary
        by_statement: Counter[str] = Counter(
            approval["statement_id"] for approval in approvals
        )
        summary["approved_by_statement"] = {
            key: by_statement[key] for key in sorted(by_statement)
        }
        if not apply:
            return summary

        approved_at = now()
        try:
            for approval in approvals:
                cursor = conn.execute(
                    "UPDATE statement_row_mappings "
                    "SET mapping_status='approved', conflict_group_id=NULL, "
                    "reviewed_by=?, reviewed_at=?, "
                    "notes=CASE WHEN notes IS NULL OR TRIM(notes)='' THEN ? ELSE notes END "
                    "WHERE mapping_id=? AND mapping_status='proposed'",
                    (actor, approved_at, note, approval["mapping_id"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError(
                        "mapping changed while bulk approving: "
                        f"{approval['mapping_id']}"
                    )
            for statement_id in summary["approved_by_statement"]:
                mapping_ids = [
                    approval["mapping_id"]
                    for approval in approvals
                    if approval["statement_id"] == statement_id
                ]
                conn.execute(
                    "INSERT INTO audit_events("
                    "audit_event_id, event_type, statement_id, actor_id, "
                    "after_json, details_json, reason, created_at"
                    ") VALUES (?, 'workbench_mapping_bulk_approved', ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), statement_id, actor,
                        json.dumps(
                            {"approved": len(mapping_ids)}, ensure_ascii=False
                        ),
                        json.dumps({
                            "mapping_ids": mapping_ids,
                            "scope": scope,
                            "allow_locked": allow_locked,
                            "skip_counts": summary["skip_counts"],
                            "source": "workbench",
                            "version": VERSION,
                        }, ensure_ascii=False, sort_keys=True),
                        note or "Bulk approval of auto-mapped standard items",
                        approved_at,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return summary
    finally:
        conn.close()



def _language_of(value: str) -> str:
    if any("\u3400" <= char <= "\u9fff" for char in value):
        return "zh-Hant"
    if any(char.isalpha() for char in value):
        return "pt"
    return "unknown"


def statement_adjustments(statement_id: str) -> list[dict[str, Any]]:
    """Return active row adjustments with enough detail for review."""
    conn = connect_v3()
    try:
        statement = conn.execute(
            "SELECT amount_decimal_places FROM statements WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if statement is None:
            raise LookupError("statement not found")
        places = statement["amount_decimal_places"]
        rows = conn.execute(
            """
            SELECT a.*,
                   sr.statement_order AS target_order,
                   sr.label_normalized AS target_label,
                   er.label_raw AS target_label_raw,
                   er.debit_raw AS target_debit_raw,
                   er.credit_raw AS target_credit_raw,
                   dp.pdf_page AS target_page,
                   si.item_key AS standard_item_key,
                   si.canonical_zh AS standard_item_zh,
                   si.canonical_pt AS standard_item_pt
            FROM statement_row_adjustments a
            LEFT JOIN statement_rows sr
              ON sr.statement_row_id=a.target_statement_row_id
            LEFT JOIN extracted_rows er
              ON er.extracted_row_id=sr.extracted_row_id
            LEFT JOIN document_pages dp
              ON dp.document_page_id=er.document_page_id
            LEFT JOIN standard_items si
              ON si.standard_item_id=a.standard_item_id
            WHERE a.statement_id=?
              AND a.adjustment_status='active'
            ORDER BY a.adjustment_type DESC, a.display_order, a.created_at
            """,
            (statement_id,),
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            if data["adjustment_type"] == "insert":
                data["display_label"] = data.get("label_normalized") or ""
                data["display_order_value"] = data.get("display_order")
                data["display_page"] = data.get("pdf_page")
                data["debit_display"] = _money(data.get("debit_minor"), places)
                data["credit_display"] = _money(data.get("credit_minor"), places)
            else:
                data["display_label"] = data.get("target_label") or data.get("target_label_raw") or ""
                data["display_order_value"] = data.get("target_order")
                data["display_page"] = data.get("target_page")
                data["debit_display"] = data.get("target_debit_raw") or ""
                data["credit_display"] = data.get("target_credit_raw") or ""
            data["standardised_label"] = (
                text(data.get("standard_item_zh")) or text(data.get("standard_item_pt"))
            )
            result.append(data)
        return result
    finally:
        conn.close()


def _effective_orders(conn: sqlite3.Connection, statement_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT statement_row_id, statement_order, pdf_page FROM ({EFFECTIVE_ROW_SQL}) "
            "WHERE statement_id=? ORDER BY statement_order, statement_row_id",
            (statement_id,),
        )
    ]


def _check_editable(conn: sqlite3.Connection, statement_id: str) -> None:
    statement = conn.execute(
        "SELECT statement_status FROM statements WHERE statement_id=?",
        (statement_id,),
    ).fetchone()
    if statement is None:
        raise LookupError("statement not found")
    if statement["statement_status"] == "active":
        raise StatementAdoptedError(
            "statement is adopted; return it to reviewed before editing"
        )
    review = conn.execute(
        "SELECT review_status FROM statement_reviews WHERE statement_id=?",
        (statement_id,),
    ).fetchone()
    if review is not None and review["review_status"] == "reviewed":
        raise StatementReviewedError("statement is reviewed; reopen it before editing")


def insert_row(
    statement_id: str,
    *,
    actor: str,
    after_row_id: str | None,
    at_start: bool,
    pdf_page: int | None,
    row_type: str,
    label: str | None,
    debit: str | None,
    credit: str | None,
    debit_state: str,
    credit_state: str,
    standard_item_id: str | None,
    note: str,
) -> dict[str, Any]:
    """Insert a manually verified row that OCR missed."""
    if row_type not in {
        "heading", "item", "subtotal", "total", "continuation", "note", "header", "unknown",
    }:
        raise ValueError("invalid row type")
    if debit_state not in {"amount", "dash", "blank", "unreadable", "not_applicable", "ambiguous"}:
        raise ValueError("invalid debit state")
    if credit_state not in {"amount", "dash", "blank", "unreadable", "not_applicable", "ambiguous"}:
        raise ValueError("invalid credit state")
    if not text(label):
        raise ValueError("label is required for an inserted row")

    conn = connect_v3(write=True)
    try:
        statement = conn.execute(
            "SELECT statement_id, statement_status, amount_decimal_places "
            "FROM statements WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if statement is None:
            raise LookupError("statement not found")
        _check_editable(conn, statement_id)
        places = _normalize_places(statement["amount_decimal_places"])
        debit_minor = _parse_money(debit, places) if debit_state == "amount" else None
        credit_minor = _parse_money(credit, places) if credit_state == "amount" else None
        if standard_item_id:
            _check_standard_item_usable(conn, standard_item_id)
            conflict = _standard_item_conflict(conn, statement_id, standard_item_id)
            if conflict is not None:
                raise ValueError(
                    "standard item is already mapped in this statement "
                    f"(row {conflict.get('statement_order')}: {conflict.get('label') or ''})"
                )

        orders = _effective_orders(conn, statement_id)
        page = pdf_page
        if at_start:
            order = (orders[0]["statement_order"] / 2) if orders else 1.0
        elif after_row_id:
            index = next(
                (
                    position for position, item in enumerate(orders)
                    if item["statement_row_id"] == after_row_id
                ),
                None,
            )
            if index is None:
                raise LookupError("insert position row not found")
            current = orders[index]["statement_order"]
            following = (
                orders[index + 1]["statement_order"]
                if index + 1 < len(orders) else None
            )
            order = current + 1 if following is None else (current + following) / 2
            if page is None:
                page = orders[index].get("pdf_page")
        else:
            order = (orders[-1]["statement_order"] + 1) if orders else 1.0
            if page is None and orders:
                page = orders[-1].get("pdf_page")

        adjustment_id = stable_id(
            "manual_row", statement_id, order, text(label), VERSION, now()
        )
        conn.execute(
            """
            INSERT INTO statement_row_adjustments(
                adjustment_id, statement_id, adjustment_type,
                target_statement_row_id, display_order, pdf_page, row_type,
                label_normalized, label_language,
                debit_minor, debit_value_state,
                credit_minor, credit_value_state,
                standard_item_id, reason, adjusted_by, adjusted_at,
                adjustment_status, created_at
            ) VALUES (?, ?, 'insert', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'active', ?)
            """,
            (
                adjustment_id, statement_id, order, page, row_type,
                text(label), _language_of(text(label)),
                debit_minor, debit_state, credit_minor, credit_state,
                standard_item_id or None, note, actor, now(), now(),
            ),
        )
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, "
            "standard_item_id, actor_id, after_json, details_json, reason, created_at"
            ") VALUES (?, 'workbench_row_inserted', ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_id("workbench_row_inserted", adjustment_id, VERSION),
                statement_id, standard_item_id or None, actor,
                json.dumps({
                    "adjustment_id": adjustment_id, "display_order": order,
                    "pdf_page": page, "row_type": row_type, "label": text(label),
                    "debit_minor": debit_minor, "debit_state": debit_state,
                    "credit_minor": credit_minor, "credit_state": credit_state,
                    "standard_item_id": standard_item_id or None,
                }, ensure_ascii=False),
                json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                note or "OCR missed this row; inserted manually",
                now(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    for item in review_rows(statement_id):
        if item["statement_row_id"] == adjustment_id:
            return item
    raise LookupError("inserted row not found")


def exclude_row(
    statement_id: str,
    statement_row_id: str,
    *,
    actor: str,
    note: str,
) -> dict[str, Any]:
    """Hide an OCR row that was read by mistake; raw OCR stays untouched."""
    conn = connect_v3(write=True)
    try:
        statement = conn.execute(
            "SELECT statement_id FROM statements WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if statement is None:
            raise LookupError("statement not found")
        _check_editable(conn, statement_id)

        manual = conn.execute(
            "SELECT adjustment_id FROM statement_row_adjustments "
            "WHERE adjustment_id=? AND statement_id=? AND adjustment_type='insert' "
            "AND adjustment_status='active'",
            (statement_row_id, statement_id),
        ).fetchone()
        if manual is not None:
            adjustment_id = manual["adjustment_id"]
            conn.execute(
                "UPDATE statement_row_adjustments SET adjustment_status='void', "
                "voided_by=?, voided_at=?, void_reason=? WHERE adjustment_id=?",
                (actor, now(), note or "Manual row removed", adjustment_id),
            )
            conn.execute(
                "INSERT INTO audit_events("
                "audit_event_id, event_type, statement_id, "
                "actor_id, details_json, reason, created_at"
                ") VALUES (?, 'workbench_adjustment_voided', ?, ?, ?, ?, ?)",
                (
                    stable_id("workbench_adjustment_voided", adjustment_id, VERSION, now()),
                    statement_id, actor,
                    json.dumps({
                        "adjustment_id": adjustment_id,
                        "adjustment_type": "insert",
                    }, ensure_ascii=False),
                    note or "Manual row removed", now(),
                ),
            )
            conn.commit()
            return {"statement_id": statement_id, "voided_adjustment_id": adjustment_id}

        row = conn.execute(
            "SELECT statement_id FROM statement_rows WHERE statement_row_id=?",
            (statement_row_id,),
        ).fetchone()
        if row is None or row["statement_id"] != statement_id:
            raise LookupError("statement row not found")
        existing = conn.execute(
            "SELECT adjustment_id FROM statement_row_adjustments "
            "WHERE target_statement_row_id=? AND adjustment_type='exclude' "
            "AND adjustment_status='active'",
            (statement_row_id,),
        ).fetchone()
        if existing is not None:
            return {"statement_id": statement_id, "adjustment_id": existing["adjustment_id"]}

        adjustment_id = stable_id(
            "excluded_row", statement_id, statement_row_id, VERSION, now()
        )
        conn.execute(
            """
            INSERT INTO statement_row_adjustments(
                adjustment_id, statement_id, adjustment_type,
                target_statement_row_id, display_order, row_type,
                label_normalized, label_language,
                debit_value_state, credit_value_state,
                reason, adjusted_by, adjusted_at, adjustment_status, created_at
            ) VALUES (?, ?, 'exclude', ?, NULL, NULL, NULL, 'unknown',
                      'blank', 'blank', ?, ?, ?, 'active', ?)
            """,
            (
                adjustment_id, statement_id, statement_row_id, note, actor, now(), now(),
            ),
        )
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, statement_row_id, "
            "actor_id, details_json, reason, created_at"
            ") VALUES (?, 'workbench_row_excluded', ?, ?, ?, ?, ?, ?)",
            (
                stable_id("workbench_row_excluded", adjustment_id, VERSION),
                statement_id, statement_row_id, actor,
                json.dumps({"adjustment_id": adjustment_id}, ensure_ascii=False),
                note or "OCR read this row by mistake; excluded from the statement",
                now(),
            ),
        )
        conn.commit()
        return {"statement_id": statement_id, "adjustment_id": adjustment_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def restore_adjustment(
    statement_id: str,
    adjustment_id: str,
    *,
    actor: str,
    note: str,
) -> dict[str, Any]:
    """Void an adjustment so the original OCR row or manual row is restored."""
    conn = connect_v3(write=True)
    try:
        adjustment = conn.execute(
            "SELECT * FROM statement_row_adjustments "
            "WHERE adjustment_id=? AND statement_id=? AND adjustment_status='active'",
            (adjustment_id, statement_id),
        ).fetchone()
        if adjustment is None:
            raise LookupError("active adjustment not found")
        _check_editable(conn, statement_id)
        conn.execute(
            "UPDATE statement_row_adjustments SET adjustment_status='void', "
            "voided_by=?, voided_at=?, void_reason=? WHERE adjustment_id=?",
            (actor, now(), note or "Adjustment restored", adjustment_id),
        )
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, statement_row_id, "
            "actor_id, details_json, reason, created_at"
            ") VALUES (?, 'workbench_adjustment_voided', ?, ?, ?, ?, ?, ?)",
            (
                stable_id("workbench_adjustment_voided", adjustment_id, VERSION, now()),
                statement_id,
                adjustment["target_statement_row_id"],
                actor,
                json.dumps({
                    "adjustment_id": adjustment_id,
                    "adjustment_type": adjustment["adjustment_type"],
                }, ensure_ascii=False),
                note or "Adjustment restored", now(),
            ),
        )
        conn.commit()
        return {"statement_id": statement_id, "voided_adjustment_id": adjustment_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def adopted_statements() -> list[dict[str, Any]]:
    """Current adopted statements, shown with the same review metrics."""
    conn = connect_v3()
    try:
        rows = conn.execute(
            """
            SELECT s.statement_id, s.statement_date, s.publication_date,
                   s.reporting_scope, s.statement_status,
                   s.currency, s.unit_scale, s.amount_decimal_places,
                   s.adopted_statement_extraction_id,
                   e.canonical_zh AS entity_name, e.entity_key,
                   COALESCE((SELECT rv.review_status FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id), 'pending') AS review_status,
                   (SELECT rv.reviewed_by FROM statement_reviews rv
                     WHERE rv.statement_id=s.statement_id) AS reviewed_by,
                   (SELECT COUNT(*) FROM v_current_statement_rows sr
                     WHERE sr.statement_id=s.statement_id) AS row_count,
                   (SELECT COUNT(*) FROM field_verifications fv
                     JOIN v_current_statement_rows sr ON sr.statement_row_id=fv.statement_row_id
                     WHERE sr.statement_id=s.statement_id) AS field_verifications,
                   (SELECT COUNT(*) FROM v_current_statement_rows sr
                     WHERE sr.statement_id=s.statement_id
                       AND (EXISTS (SELECT 1 FROM statement_row_mappings m
                                     WHERE m.statement_row_id=sr.statement_row_id
                                       AND m.mapping_status IN ('proposed','approved'))
                            OR EXISTS (SELECT 1 FROM statement_row_adjustments a
                                        WHERE a.adjustment_id=sr.statement_row_id
                                          AND a.adjustment_type='insert'
                                          AND a.adjustment_status='active'
                                          AND a.standard_item_id IS NOT NULL))) AS mapped_rows,
                   (SELECT COUNT(*) FROM v_current_statement_rows sr
                     WHERE sr.statement_id=s.statement_id
                       AND EXISTS (SELECT 1 FROM statement_row_mappings m
                                    WHERE m.statement_row_id=sr.statement_row_id
                                      AND m.conflict_group_id IS NOT NULL
                                      AND m.mapping_status IN ('proposed','approved'))) AS mapping_conflict_rows,
                   (SELECT COUNT(*) FROM (
                       SELECT item FROM (
                           SELECT m.standard_item_id AS item
                           FROM statement_row_mappings m
                           JOIN statement_rows sr ON sr.statement_row_id=m.statement_row_id
                           WHERE sr.statement_id=s.statement_id
                             AND m.mapping_status='approved'
                           UNION ALL
                           SELECT a.standard_item_id AS item
                           FROM statement_row_adjustments a
                           WHERE a.statement_id=s.statement_id
                             AND a.adjustment_type='insert'
                             AND a.adjustment_status='active'
                             AND a.standard_item_id IS NOT NULL
                       ) WHERE item IS NOT NULL
                       GROUP BY item HAVING COUNT(*) > 1
                   )) AS duplicate_mapping_items,
                   (SELECT COUNT(*) FROM v_current_statement_rows sr
                     WHERE sr.statement_id=s.statement_id
                       AND (
                         EXISTS (
                           SELECT 1 FROM statement_row_adjustments a
                           WHERE a.adjustment_id=sr.statement_row_id
                             AND a.adjustment_type='insert'
                             AND a.adjustment_status='active'
                             AND a.standard_item_id IS NOT NULL
                             AND NOT EXISTS (
                               SELECT 1 FROM standard_items si
                               WHERE si.standard_item_id=a.standard_item_id
                                 AND si.item_status='approved'
                             )
                         )
                         OR EXISTS (
                           SELECT 1 FROM statement_row_mappings m
                           JOIN standard_items si ON si.standard_item_id=m.standard_item_id
                           WHERE m.statement_row_id=sr.statement_row_id
                             AND m.mapping_status IN ('proposed','approved')
                             AND si.item_status <> 'approved'
                             AND (m.mapping_status='approved'
                                  OR NOT EXISTS (
                                    SELECT 1 FROM statement_row_mappings m2
                                    WHERE m2.statement_row_id=sr.statement_row_id
                                      AND m2.mapping_status='approved'
                                  ))
                         )
                       )) AS invalid_mapping_rows,
                   (SELECT COUNT(*) FROM v_effective_statement_rows er
                     WHERE er.statement_id=s.statement_id
                       AND (er.effective_label_normalized IS NULL
                            OR er.effective_label_normalized = '')) AS missing_labels,
                   (SELECT COUNT(*) FROM v_effective_statement_rows er
                     WHERE er.statement_id=s.statement_id
                       AND (er.effective_debit_value_state IN ('ambiguous','unreadable')
                            OR er.effective_credit_value_state IN ('ambiguous','unreadable'))) AS issue_rows,
                   (SELECT SUM(CASE WHEN er.effective_debit_value_state='amount'
                                    THEN er.effective_debit_minor ELSE 0 END)
                      FROM v_effective_statement_rows er
                     WHERE er.statement_id=s.statement_id) AS debit_total_minor,
                   (SELECT SUM(CASE WHEN er.effective_credit_value_state='amount'
                                    THEN er.effective_credit_minor ELSE 0 END)
                      FROM v_effective_statement_rows er
                     WHERE er.statement_id=s.statement_id) AS credit_total_minor
            FROM statements s
            JOIN reporting_entities e ON e.entity_id=s.reporting_entity_id
            WHERE s.statement_status='active'
            ORDER BY s.statement_date DESC, e.canonical_zh
            """
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            difference = int(data["debit_total_minor"] or 0) - int(data["credit_total_minor"] or 0)
            data["debit_credit_difference_minor"] = difference
            data["debit_credit_equal"] = difference == 0
            data["unmapped_rows"] = max(
                0, int(data["row_count"] or 0) - int(data["mapped_rows"] or 0)
            )
            data["mapping_ready"] = (
                int(data["row_count"] or 0) > 0
                and int(data["mapping_conflict_rows"] or 0) == 0
                and int(data["duplicate_mapping_items"] or 0) == 0
                and int(data["invalid_mapping_rows"] or 0) == 0
                and data["unmapped_rows"] == 0
            )
            data["mapping_blockers"] = ";".join(
                part for part in (
                    f"mapping_conflict={data['mapping_conflict_rows']}" if data["mapping_conflict_rows"] else "",
                    f"mapping_unmatched={data['unmapped_rows']}" if data["unmapped_rows"] else "",
                    f"duplicate_standard_item={data['duplicate_mapping_items']}" if data["duplicate_mapping_items"] else "",
                    f"invalid_standard_item={data['invalid_mapping_rows']}" if data["invalid_mapping_rows"] else "",
                ) if part
            )
            data["review_bucket"] = "adopted"
            data["debit_total_display"] = _money(
                data["debit_total_minor"], data["amount_decimal_places"]
            )
            data["credit_total_display"] = _money(
                data["credit_total_minor"], data["amount_decimal_places"]
            )
            result.append(data)
        return result
    finally:
        conn.close()


def return_to_review(
    statement_id: str,
    actor: str,
    note: str = "",
) -> dict[str, Any]:
    """Revoke adoption and put the statement back into reviewed/held state."""
    conn = connect_v3(write=True)
    try:
        statement = conn.execute(
            "SELECT statement_id, statement_status, adopted_statement_extraction_id "
            "FROM statements WHERE statement_id=?",
            (statement_id,),
        ).fetchone()
        if statement is None:
            raise LookupError("statement not found")
        if (
            statement["statement_status"] != "active"
            or not statement["adopted_statement_extraction_id"]
        ):
            raise ValueError("statement is not adopted")
        if conn.execute(
            "SELECT 1 FROM publication_records "
            "WHERE statement_id=? AND publication_status='published'",
            (statement_id,),
        ).fetchone():
            raise ValueError("revoke the current publication before returning to review")

        extraction_id = statement["adopted_statement_extraction_id"]
        conn.execute(
            "UPDATE statements SET statement_status='proposed', "
            "adopted_statement_extraction_id=NULL WHERE statement_id=?",
            (statement_id,),
        )
        conn.execute(
            """
            INSERT INTO statement_reviews(
                statement_id, review_status, reviewed_by, reviewed_at, note, updated_at
            ) VALUES (?, 'reviewed', ?, ?, ?, ?)
            ON CONFLICT(statement_id) DO UPDATE SET
                review_status='reviewed',
                reviewed_by=excluded.reviewed_by,
                reviewed_at=excluded.reviewed_at,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            (
                statement_id, actor, now(),
                note or "Returned from adoption to reviewed status", now(),
            ),
        )
        _bind_review_extraction(
            conn, statement_id, extraction_id, actor=actor,
            note=note or "Returned from adoption to reviewed status",
        )
        conn.execute(
            "INSERT INTO audit_events("
            "audit_event_id, event_type, statement_id, statement_extraction_id, "
            "actor_id, before_json, after_json, details_json, reason, created_at"
            ") VALUES (?, 'workbench_statement_returned_to_review', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_id(
                    "workbench_return_to_review", statement_id, extraction_id, VERSION, now()
                ),
                statement_id, extraction_id, actor,
                json.dumps({
                    "statement_status": "active",
                    "adopted_statement_extraction_id": extraction_id,
                }, ensure_ascii=False),
                json.dumps({
                    "statement_status": "proposed",
                    "adopted_statement_extraction_id": None,
                    "review_status": "reviewed",
                    "review_binding_statement_extraction_id": extraction_id,
                }, ensure_ascii=False),
                json.dumps({"source": "workbench", "version": VERSION}, ensure_ascii=False),
                note or "Returned from adoption to reviewed status",
                now(),
            ),
        )
        conn.commit()
        return {
            "statement_id": statement_id,
            "statement_status": "proposed",
            "review_status": "reviewed",
            "released_statement_extraction_id": extraction_id,
            "review_binding_statement_extraction_id": extraction_id,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
