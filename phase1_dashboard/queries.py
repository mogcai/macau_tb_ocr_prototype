"""Read-only queries against official V3 tables and views.

Usable rows come from ``v_effective_statement_rows``. Inserted manual rows that
exist only in that view are kept. This module does not write.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from phase1_dashboard.scope import ADOPTED_NAMES


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _in_clause(values: list[Any] | tuple[Any, ...]) -> tuple[str, tuple[Any, ...]]:
    if not values:
        raise ValueError("empty IN clause")
    return ",".join("?" for _ in values), tuple(values)


def fetch_entities(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    placeholders, params = _in_clause(tuple(sorted(ADOPTED_NAMES)))
    sql = f"""
        SELECT entity_id, entity_key, entity_type, canonical_zh, canonical_pt,
               canonical_en, parent_entity_id, registry_status, merged_into_entity_id
        FROM reporting_entities
        WHERE canonical_zh IN ({placeholders})
        ORDER BY canonical_zh, entity_id
    """
    return _rows(connection.execute(sql, params))


def fetch_statements(
    connection: sqlite3.Connection, entity_ids: list[str]
) -> list[dict[str, Any]]:
    if not entity_ids:
        return []
    placeholders, params = _in_clause(entity_ids)
    sql = f"""
        SELECT s.statement_id,
               s.reporting_entity_id AS entity_id,
               e.canonical_zh,
               e.entity_type,
               s.statement_date,
               s.statement_type,
               s.statement_status,
               s.reporting_scope,
               s.currency,
               s.unit_scale,
               s.amount_decimal_places,
               s.identity_status,
               s.metadata_status,
               s.adopted_statement_extraction_id,
               s.entity_name_raw,
               r.review_status
        FROM statements s
        JOIN reporting_entities e ON e.entity_id = s.reporting_entity_id
        LEFT JOIN statement_reviews r ON r.statement_id = s.statement_id
        WHERE s.reporting_entity_id IN ({placeholders})
        ORDER BY e.canonical_zh, s.statement_date, s.statement_type, s.statement_id
    """
    return _rows(connection.execute(sql, params))


def fetch_statement(
    connection: sqlite3.Connection, statement_id: str
) -> dict[str, Any] | None:
    sql = """
        SELECT s.statement_id,
               s.reporting_entity_id AS entity_id,
               e.canonical_zh,
               e.entity_type,
               s.statement_date,
               s.statement_type,
               s.statement_status,
               s.reporting_scope,
               s.currency,
               s.unit_scale,
               s.amount_decimal_places,
               s.identity_status,
               s.metadata_status,
               s.adopted_statement_extraction_id,
               s.entity_name_raw,
               r.review_status
        FROM statements s
        JOIN reporting_entities e ON e.entity_id = s.reporting_entity_id
        LEFT JOIN statement_reviews r ON r.statement_id = s.statement_id
        WHERE s.statement_id = ?
    """
    found = _rows(connection.execute(sql, (statement_id,)))
    return found[0] if found else None


def fetch_rows(
    connection: sqlite3.Connection, statement_ids: list[str]
) -> list[dict[str, Any]]:
    """Effective rows only. An empty id list reads nothing."""
    if not statement_ids:
        return []
    placeholders, params = _in_clause(statement_ids)
    sql = f"""
        SELECT sr.statement_row_id,
               sr.statement_id,
               sr.statement_order,
               sr.extracted_row_id,
               sr.row_type,
               sr.label_normalized,
               sr.effective_label_normalized,
               sr.effective_debit_minor,
               sr.effective_credit_minor,
               sr.effective_debit_value_state,
               sr.effective_credit_value_state,
               sr.effective_row_type,
               sr.effective_parent_row_id,
               er.label_raw,
               er.debit_raw,
               er.credit_raw
        FROM v_effective_statement_rows sr
        LEFT JOIN extracted_rows er ON er.extracted_row_id = sr.extracted_row_id
        WHERE sr.statement_id IN ({placeholders})
    """
    rows = _rows(connection.execute(sql, params))
    rows.sort(key=lambda row: (
        row["statement_id"],
        row["statement_order"] is None,
        row["statement_order"] if row["statement_order"] is not None else 0,
        row["statement_row_id"],
    ))
    return rows


def fetch_mappings(
    connection: sqlite3.Connection, row_ids: list[str]
) -> list[dict[str, Any]]:
    if not row_ids:
        return []
    placeholders, params = _in_clause(row_ids)
    sql = f"""
        SELECT m.statement_row_id,
               m.mapping_status,
               m.match_method,
               m.standard_item_id,
               si.item_key,
               si.canonical_zh AS item_zh,
               si.canonical_pt AS item_pt,
               si.item_status,
               si.merged_into_standard_item_id
        FROM statement_row_mappings m
        JOIN standard_items si ON si.standard_item_id = m.standard_item_id
        WHERE m.statement_row_id IN ({placeholders})
    """
    return _rows(connection.execute(sql, params))


def fetch_inserts(
    connection: sqlite3.Connection, statement_ids: list[str]
) -> list[dict[str, Any]]:
    if not statement_ids:
        return []
    placeholders, params = _in_clause(statement_ids)
    sql = f"""
        SELECT a.adjustment_id,
               a.statement_id,
               a.standard_item_id,
               a.label_normalized,
               si.item_key,
               si.canonical_zh AS item_zh,
               si.canonical_pt AS item_pt,
               si.item_status,
               si.merged_into_standard_item_id
        FROM statement_row_adjustments a
        LEFT JOIN standard_items si ON si.standard_item_id = a.standard_item_id
        WHERE a.adjustment_type = 'insert'
          AND a.adjustment_status = 'active'
          AND a.statement_id IN ({placeholders})
    """
    return _rows(connection.execute(sql, params))
