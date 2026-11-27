"""Wire read-only queries to the presentation rules."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from phase1_dashboard import queries
from phase1_dashboard.charts import build_chart_view
from phase1_dashboard.indicators import build_indicator_view
from phase1_dashboard.matrix import build_matrix_view
from phase1_dashboard.overview import build_bank_directory
from phase1_dashboard.present import (
    build_compare,
    build_coverage,
    build_entity_detail,
    build_statement_view,
    decorate_row,
    is_usable_statement,
)
from phase1_dashboard.scope import PAGE_WARNINGS


def load_scope(connection: sqlite3.Connection) -> dict[str, Any]:
    entities = queries.fetch_entities(connection)
    statements = queries.fetch_statements(
        connection, [entity["entity_id"] for entity in entities]
    )
    usable_ids = [
        statement["statement_id"]
        for statement in statements
        if is_usable_statement(statement)
    ]
    raw_rows = queries.fetch_rows(connection, usable_ids)
    mappings = queries.fetch_mappings(
        connection, [row["statement_row_id"] for row in raw_rows]
    )
    inserts = {
        row["adjustment_id"]: row
        for row in queries.fetch_inserts(connection, usable_ids)
    }
    mappings_by_row: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mapping in mappings:
        mappings_by_row[mapping["statement_row_id"]].append(mapping)
    statements_by_id = {statement["statement_id"]: statement for statement in statements}
    rows_by_statement: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in raw_rows:
        statement = statements_by_id[raw["statement_id"]]
        rows_by_statement[raw["statement_id"]].append(
            decorate_row(
                raw,
                mappings_by_row.get(raw["statement_row_id"], []),
                inserts.get(raw["statement_row_id"]),
                statement,
            )
        )
    return {
        "entities": entities,
        "statements": statements,
        "rows_by_statement": dict(rows_by_statement),
    }


def chart_home(
    connection: sqlite3.Connection,
    *,
    bucket_id: str | None,
    statement_date: str | None,
    item_key: str | None,
    entity_id: str | None,
) -> dict[str, Any]:
    scope = load_scope(connection)
    publications = queries.fetch_publication_summary(connection)
    coverage = build_coverage(**scope)
    model = build_chart_view(
        bucket_id=bucket_id,
        statement_date=statement_date,
        item_key=item_key or None,
        entity_id=entity_id or None,
        coverage=coverage,
        publication_count=publications["current"],
        publication_total=publications["total"],
        **scope,
    )
    model["page_warnings"] = list(PAGE_WARNINGS)
    return model


def bank_directory(connection: sqlite3.Connection) -> dict[str, Any]:
    model = build_bank_directory(coverage(connection))
    model["page_warnings"] = list(PAGE_WARNINGS)
    return model


def indicators(connection: sqlite3.Connection) -> dict[str, Any]:
    coverage_model = coverage(connection)
    publications = queries.fetch_publication_summary(connection)
    model = build_indicator_view(
        publication_count=publications["current"],
        publication_total=publications["total"],
        approved_rows=coverage_model["mapping"]["approved"],
        proposed_rows=coverage_model["mapping"]["proposed"],
    )
    model["page_warnings"] = list(PAGE_WARNINGS)
    return model


def coverage(connection: sqlite3.Connection) -> dict[str, Any]:
    model = build_coverage(**load_scope(connection))
    model["page_warnings"] = list(PAGE_WARNINGS)
    return model


def matrix_view(
    connection: sqlite3.Connection,
    *,
    bucket_id: str | None,
    entity_ids: list[str] | None,
    item_keys: list[str] | None,
    statement_dates: list[str] | None,
    signed: bool = False,
) -> dict[str, Any]:
    scope = load_scope(connection)
    model = build_matrix_view(
        bucket_id=bucket_id,
        entity_ids=entity_ids,
        item_keys=item_keys,
        statement_dates=statement_dates,
        signed=signed,
        coverage=build_coverage(**scope),
        **scope,
    )
    model["page_warnings"] = list(PAGE_WARNINGS)
    return model


def entity_detail(
    connection: sqlite3.Connection,
    entity_id: str,
    selected_date: str | None = None,
) -> dict[str, Any] | None:
    model = build_entity_detail(
        entity_id=entity_id,
        selected_date=selected_date,
        **load_scope(connection),
    )
    if model is not None:
        model["page_warnings"] = list(PAGE_WARNINGS)
    return model


def statement_detail(
    connection: sqlite3.Connection, statement_id: str
) -> dict[str, Any] | None:
    statement = queries.fetch_statement(connection, statement_id)
    if statement is None:
        return None
    rows: list[dict[str, Any]] = []
    if is_usable_statement(statement):
        scope = load_scope(connection)
        rows = scope["rows_by_statement"].get(statement_id, [])
    model = build_statement_view(statement, rows)
    if model is not None:
        model["page_warnings"] = list(PAGE_WARNINGS)
    return model


def compare(
    connection: sqlite3.Connection,
    *,
    bucket_id: str,
    statement_date: str,
    item_key: str | None,
    display: str | None,
) -> dict[str, Any]:
    model = build_compare(
        bucket_id=bucket_id,
        statement_date=statement_date,
        item_key=item_key or None,
        display=display,
        **load_scope(connection),
    )
    model["page_warnings"] = list(PAGE_WARNINGS)
    return model
