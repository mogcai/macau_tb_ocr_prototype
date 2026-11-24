"""Presentation rules for the adopted window.

Amounts stay attached to one statement and one mapping status. This module
does not add banks together, does not rank them, and does not compute shares.
"""
from __future__ import annotations

from typing import Any

from phase1_dashboard.scope import (
    ADOPTED_NAMES,
    BALANCE_SHEET,
    BUCKETS,
    COMPARISON_DATES,
    MAPPING_STATUSES,
    NOT_SAFELY_COMPARABLE,
    PERIMETER_REASON,
    SHARE_BLOCK_REASON,
    bucket_by_id,
    draft_warnings_for,
    empty_mapping_counts,
)


class ScopeError(ValueError):
    """The requested slice is outside the adopted perimeter."""


def is_usable_statement(statement: dict[str, Any]) -> bool:
    """Adopted, active, in the adopted name list, and inside the three-quarter window.

    ``identity_status`` and ``metadata_status`` are not a second rejection.
    A proposed identity on an adopted statement stays in the usable set.
    """
    return (
        statement.get("canonical_zh") in ADOPTED_NAMES
        and statement.get("statement_date") in COMPARISON_DATES
        and statement.get("statement_status") == "active"
        and bool(statement.get("adopted_statement_extraction_id"))
    )


def format_minor(minor: int | None, decimal_places: int | None) -> dict[str, Any]:
    """Show the stored minor integer. A reading uses only amount_decimal_places.

    unit_scale is never applied here. A missing decimal-place record stays a
    raw integer instead of assuming two decimal places.
    """
    if minor is None:
        return {"minor": None, "text": None, "reading": None, "note": None}
    value = int(minor)
    text = f"{value:,}"
    reading = None
    note = "只顯示庫內 minor 整數。小數位未記載，沒有換算。"
    if isinstance(decimal_places, int) and not isinstance(decimal_places, bool) and 0 <= decimal_places <= 6:
        scale = 10 ** decimal_places
        sign = "-" if value < 0 else ""
        whole, frac = divmod(abs(value), scale)
        if decimal_places == 0:
            reading = f"{sign}{whole:,}"
        else:
            reading = f"{sign}{whole:,}.{frac:0{decimal_places}d}"
        note = "旁註只按報表 amount_decimal_places 換算，不是財務核實，亦未乘 unit_scale。"
    return {"minor": value, "text": text, "reading": reading, "note": note}


def share_placeholder() -> dict[str, Any]:
    return {"available": False, "value": None, "reason": SHARE_BLOCK_REASON}


def aggregate_placeholder() -> dict[str, Any]:
    return {
        "goal": "加總後睇澳門銀行整體情況",
        "enabled": False,
        "computed": False,
        "amounts": None,
        "legal_bank_sum": None,
        "all_fifteen_sum": None,
        "column_total": None,
        "reason": PERIMETER_REASON,
    }


def classify_row(
    row_id: str,
    mappings: list[dict[str, Any]],
    insert: dict[str, Any] | None,
) -> dict[str, Any]:
    """Map one effective row to approved / proposed / insert-direct / unmapped.

    Rejected and superseded mappings are ignored. An approved row stays
    approved when a proposed mapping is also present. insert-direct is only
    the manual insert that carries a standard item and has no proposed or
    approved mapping row. ``v_unmapped = 0`` is not treated as safe.
    """
    live = [
        item
        for item in mappings
        if item.get("statement_row_id") == row_id
        and item.get("mapping_status") in {"approved", "proposed"}
    ]
    approved = [item for item in live if item["mapping_status"] == "approved"]
    proposed = [item for item in live if item["mapping_status"] == "proposed"]
    if approved:
        status = "approved"
        shown = approved
        also_proposed = bool(proposed)
        proposed_methods = sorted({item.get("match_method") for item in proposed if item.get("match_method")})
    elif proposed:
        status = "proposed"
        shown = proposed
        also_proposed = False
        proposed_methods = []
    elif insert and insert.get("standard_item_id"):
        status = "insert-direct"
        shown = [
            {
                "statement_row_id": row_id,
                "mapping_status": "insert-direct",
                "match_method": None,
                "standard_item_id": insert.get("standard_item_id"),
                "item_key": insert.get("item_key"),
                "item_zh": insert.get("item_zh"),
                "item_pt": insert.get("item_pt"),
                "item_status": insert.get("item_status"),
                "merged_into_standard_item_id": insert.get("merged_into_standard_item_id"),
            }
        ]
        also_proposed = False
        proposed_methods = []
    else:
        status = "unmapped"
        shown = []
        also_proposed = False
        proposed_methods = []
    candidates = [_candidate(item) for item in shown]
    return {
        "mapping_status": status,
        "also_proposed": also_proposed,
        "proposed_methods": proposed_methods,
        "candidates": candidates,
        "match_methods": [item["match_method"] for item in candidates if item.get("match_method")],
        "catalogue_current": _catalogue_current(candidates),
        "presented_as_approved_scale": False,
        "rank": None,
        "draft_warnings": draft_warnings_for(
            [item["item_key"] for item in candidates if item.get("item_key")]
        ),
    }


def catalogue_item_current(item: dict[str, Any] | None) -> bool:
    """A current catalogue item is item_status approved and not merged away.

    This is independent of statement_row_mappings.mapping_status. An approved
    mapping onto a retired or merged item stays visible and is not current.
    """
    if not item:
        return False
    return item.get("item_status") == "approved" and not item.get("merged_into_standard_item_id")


def _catalogue_current(candidates: list[dict[str, Any]]) -> bool | None:
    if not candidates:
        return None
    return all(catalogue_item_current(item) for item in candidates)


def _candidate(item: dict[str, Any]) -> dict[str, Any]:
    merged_into = item.get("merged_into_standard_item_id")
    return {
        "item_key": item.get("item_key"),
        "item_zh": item.get("item_zh"),
        "item_pt": item.get("item_pt"),
        "standard_item_id": item.get("standard_item_id"),
        "item_status": item.get("item_status"),
        "merged_into_standard_item_id": merged_into,
        "catalogue_current": catalogue_item_current(item),
        "mapping_status": item.get("mapping_status"),
        "match_method": item.get("match_method"),
    }


def decorate_row(
    raw: dict[str, Any],
    mappings: list[dict[str, Any]],
    insert: dict[str, Any] | None,
    statement: dict[str, Any],
) -> dict[str, Any]:
    classified = classify_row(raw["statement_row_id"], mappings, insert)
    label = raw.get("effective_label_normalized") or raw.get("label_normalized")
    if label is None and insert is not None:
        label = insert.get("label_normalized")
    decimal_places = statement.get("amount_decimal_places")
    parent = raw.get("effective_parent_row_id")
    return {
        **classified,
        "statement_row_id": raw["statement_row_id"],
        "statement_id": raw["statement_id"],
        "statement_order": raw.get("statement_order"),
        "statement_date": statement.get("statement_date"),
        "statement_type": statement.get("statement_type"),
        "canonical_zh": statement.get("canonical_zh"),
        "entity_id": statement.get("entity_id"),
        "entity_type": statement.get("entity_type"),
        "reporting_scope": statement.get("reporting_scope"),
        "currency": statement.get("currency"),
        "unit_scale": statement.get("unit_scale"),
        "amount_decimal_places": decimal_places,
        "identity_status": statement.get("identity_status"),
        "metadata_status": statement.get("metadata_status"),
        "adopted": True,
        "row_type": raw.get("effective_row_type") or raw.get("row_type"),
        "label": label,
        "label_raw": raw.get("label_raw"),
        "debit_raw": raw.get("debit_raw"),
        "credit_raw": raw.get("credit_raw"),
        "debit": format_minor(raw.get("effective_debit_minor"), decimal_places),
        "credit": format_minor(raw.get("effective_credit_minor"), decimal_places),
        "debit_state": raw.get("effective_debit_value_state"),
        "credit_state": raw.get("effective_credit_value_state"),
        "parent_row_id": parent,
        "has_parent": bool(parent),
        "insert_without_ocr_label": raw.get("label_raw") is None and classified["mapping_status"] == "insert-direct",
        "share": share_placeholder(),
        "entity_total": None,
    }


def candidate_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for candidate in row.get("candidates") or []:
        key = candidate.get("item_key")
        if key and key not in keys:
            keys.append(key)
    return keys


def build_coverage(
    *,
    entities: list[dict[str, Any]],
    statements: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        if entity.get("canonical_zh") in ADOPTED_NAMES:
            by_name.setdefault(entity["canonical_zh"], []).append(entity)
    for matches in by_name.values():
        matches.sort(key=lambda entity: entity.get("entity_id") or "")

    in_window: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    for statement in statements:
        if statement.get("canonical_zh") not in ADOPTED_NAMES:
            continue
        if statement.get("statement_date") in COMPARISON_DATES:
            in_window.append(statement)
        else:
            outside.append(_excluded_identity(statement))

    usable = [statement for statement in in_window if is_usable_statement(statement)]
    counts_by_statement = {
        statement["statement_id"]: _count_rows(rows_by_statement.get(statement["statement_id"], []))
        for statement in usable
    }
    mapping_totals = empty_mapping_counts()
    for counts in counts_by_statement.values():
        for status in MAPPING_STATUSES:
            mapping_totals[status] += counts[status]
    zero_approved = [
        statement["statement_id"]
        for statement in usable
        if counts_by_statement[statement["statement_id"]]["approved"] == 0
    ]

    buckets: list[dict[str, Any]] = []
    gap_count = 0
    for bucket in BUCKETS:
        entity_rows: list[dict[str, Any]] = []
        for name in bucket["names"]:
            matches = by_name.get(name, [])
            if not matches:
                periods = {
                    date: _gap("庫內未找到此 canonical_zh")
                    for date in COMPARISON_DATES
                }
                gap_count += len(COMPARISON_DATES)
                entity_rows.append({
                    "canonical_zh": name,
                    "resolved": False,
                    "entity_id": None,
                    "entity_ids": [],
                    "entity_type": None,
                    "entity_key": None,
                    "parent_entity_id": None,
                    "registry_status": None,
                    "duplicate_name": False,
                    "merged": False,
                    "periods": periods,
                })
                continue
            for entity in matches:
                owned = [
                    statement for statement in in_window
                    if statement.get("entity_id") == entity.get("entity_id")
                ]
                periods = {}
                for date in COMPARISON_DATES:
                    cell = _period_cell(
                        [statement for statement in owned if statement.get("statement_date") == date],
                        counts_by_statement,
                    )
                    periods[date] = cell
                    if cell["kind"] == "gap":
                        gap_count += 1
                entity_rows.append({
                    "canonical_zh": name,
                    "resolved": True,
                    "entity_id": entity.get("entity_id"),
                    "entity_ids": [entity.get("entity_id")],
                    "entity_type": entity.get("entity_type"),
                    "entity_key": entity.get("entity_key"),
                    "parent_entity_id": entity.get("parent_entity_id"),
                    "registry_status": entity.get("registry_status"),
                    "duplicate_name": len(matches) > 1,
                    "merged": False,
                    "periods": periods,
                })
        buckets.append({
            "id": bucket["id"],
            "title": bucket["title"],
            "rule": bucket["rule"],
            "summed": False,
            "entity_total": None,
            "entities": entity_rows,
        })

    unmapped = mapping_totals["unmapped"]
    return {
        "window": list(COMPARISON_DATES),
        "usable_statement_count": len(usable),
        "gap_count": gap_count,
        "buckets": buckets,
        "mapping": {
            **mapping_totals,
            "unmapped_zero_means_safely_mapped": False,
            "safely_cross_comparable": False,
            "unmapped_is_zero": unmapped == 0,
            "note": NOT_SAFELY_COMPARABLE,
        },
        "zero_approved_statement_count": len(zero_approved),
        "aggregate": aggregate_placeholder(),
        "outside_window": outside,
        "read_only": True,
        "amounts_are_financially_verified": False,
        "summed": False,
    }


def build_compare(
    *,
    bucket_id: str,
    statement_date: str,
    item_key: str | None,
    display: str | None,
    entities: list[dict[str, Any]],
    statements: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    bucket = bucket_by_id(bucket_id)
    if bucket is None:
        raise ScopeError("比較只可以選擇一個分組：本地法人、關聯報表，或外地銀行分行。")
    if statement_date not in COMPARISON_DATES:
        raise ScopeError("比較日必須是 2026-03-31、2026-06-30 或 2026-09-30。")
    chosen_display = display if display in {"amount", "share"} else "amount"
    coverage = build_coverage(
        entities=entities,
        statements=statements,
        rows_by_statement=rows_by_statement,
    )
    bucket_model = next(item for item in coverage["buckets"] if item["id"] == bucket_id)
    catalogue: dict[str, dict[str, Any]] = {}
    entity_cells: list[dict[str, Any]] = []
    for entity in bucket_model["entities"]:
        cell = entity["periods"][statement_date]
        balance_rows = _balance_rows(cell, rows_by_statement)
        _accumulate_catalogue(catalogue, balance_rows)
        kind, note, matched, adopted_types = _compare_cell(
            cell, balance_rows, item_key
        )
        entity_cells.append({
            "canonical_zh": entity["canonical_zh"],
            "entity_id": entity.get("entity_id"),
            "entity_type": entity.get("entity_type"),
            "resolved": entity["resolved"],
            "kind": kind,
            "gap_reason": cell["reason"] if cell["kind"] == "gap" else None,
            "note": note,
            "adopted_statement_types": adopted_types,
            "rows": matched,
            "entity_total": None,
            "mixed_statement_types": cell.get("mixed_statement_types", False),
        })
    displayed = [row for cell in entity_cells for row in cell["rows"]]
    signatures = {
        (row.get("currency"), row.get("unit_scale"), row.get("amount_decimal_places"))
        for row in displayed
    }
    if not displayed:
        scale_mismatch = False
        scale_note = "未選項目，未並排金額。"
    elif len(signatures) > 1:
        scale_mismatch = True
        scale_note = "這些儲存格的幣別、unit_scale 或小數位不一致。數值保持各報表原記載，沒有換成同一單位。"
    else:
        scale_mismatch = False
        scale_note = "同一欄的幣別、unit_scale 與小數位記載一致。這仍然不是財務核實。"
    return {
        "bucket": bucket_id,
        "bucket_title": bucket["title"],
        "bucket_rule": bucket["rule"],
        "statement_date": statement_date,
        "statement_type": BALANCE_SHEET,
        "item_key": item_key or None,
        "needs_item": not item_key,
        "display": chosen_display,
        "share": share_placeholder(),
        "available_items": sorted(catalogue.values(), key=lambda item: item["item_key"]),
        "entities": entity_cells,
        "summed": False,
        "column_total": None,
        "legal_bank_sum": None,
        "all_fifteen_sum": None,
        "entity_total": None,
        "ranking": None,
        "scale_mismatch": scale_mismatch,
        "scale_note": scale_note,
        "safely_cross_comparable": False,
        "presented_as_approved_scale": False,
        "amounts_are_financially_verified": False,
        "note": NOT_SAFELY_COMPARABLE,
        "draft_warnings": draft_warnings_for([item_key] if item_key else []),
    }


def build_entity_detail(
    *,
    entity_id: str,
    selected_date: str | None,
    entities: list[dict[str, Any]],
    statements: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    coverage = build_coverage(
        entities=entities,
        statements=statements,
        rows_by_statement=rows_by_statement,
    )
    found: dict[str, Any] | None = None
    bucket_meta: dict[str, Any] | None = None
    for bucket in coverage["buckets"]:
        for entity in bucket["entities"]:
            if entity.get("entity_id") == entity_id:
                found = entity
                bucket_meta = bucket
                break
    if found is None or bucket_meta is None:
        return None
    if selected_date is not None and selected_date not in COMPARISON_DATES:
        raise ScopeError("期間必須是 2026-03-31、2026-06-30 或 2026-09-30。")
    date = selected_date if selected_date in COMPARISON_DATES else _default_date(found)
    pivot_items: dict[str, dict[str, Any]] = {}
    unaligned: dict[str, list[dict[str, Any]]] = {item: [] for item in COMPARISON_DATES}
    for period in COMPARISON_DATES:
        cell = found["periods"][period]
        for row in _balance_rows(cell, rows_by_statement):
            keys = candidate_keys(row)
            if len(keys) != 1:
                unaligned[period].append(row)
                continue
            key = keys[0]
            source = next(
                (candidate for candidate in row["candidates"] if candidate.get("item_key") == key),
                None,
            )
            slot = pivot_items.setdefault(key, {
                "item_key": key,
                "item_zh": source.get("item_zh") if source else None,
                "item_status": source.get("item_status") if source else None,
                "merged_into_standard_item_id": source.get("merged_into_standard_item_id") if source else None,
                "catalogue_current": catalogue_item_current(source),
                "draft_warnings": draft_warnings_for([key]),
                "cells": {},
            })
            if source and not catalogue_item_current(source):
                slot["item_status"] = source.get("item_status")
                slot["merged_into_standard_item_id"] = source.get("merged_into_standard_item_id")
                slot["catalogue_current"] = False
            slot["cells"].setdefault(period, []).append(row)
    pivot = []
    for key in sorted(pivot_items):
        item = pivot_items[key]
        cells = {}
        for period in COMPARISON_DATES:
            period_cell = found["periods"][period]
            if period_cell["kind"] == "gap":
                cells[period] = {"kind": "gap", "reason": period_cell["reason"], "rows": [], "total": None}
            elif period in item["cells"]:
                cells[period] = {"kind": "rows", "reason": None, "rows": item["cells"][period], "total": None}
            else:
                cells[period] = {
                    "kind": "absent",
                    "reason": "此期沒有此候選。這不是 0。",
                    "rows": [],
                    "total": None,
                }
        pivot.append({**item, "cells": cells, "total": None})
    selected = found["periods"][date]
    return {
        "canonical_zh": found["canonical_zh"],
        "bucket_id": bucket_meta["id"],
        "bucket_title": bucket_meta["title"],
        "bucket_rule": bucket_meta["rule"],
        "entity_id": found.get("entity_id"),
        "entity_type": found.get("entity_type"),
        "entity_key": found.get("entity_key"),
        "parent_entity_id": found.get("parent_entity_id"),
        "registry_status": found.get("registry_status"),
        "duplicate_name": found.get("duplicate_name"),
        "merged": False,
        "periods": found["periods"],
        "selected_date": date,
        "selected_statements": selected.get("statements") or [],
        "selected_rows_by_statement": _selected_statement_rows(selected, rows_by_statement),
        "pivot": pivot,
        "unaligned": unaligned,
        "window": list(COMPARISON_DATES),
        "summed": False,
        "safely_cross_comparable": False,
        "amounts_are_financially_verified": False,
        "note": NOT_SAFELY_COMPARABLE,
    }


def build_statement_view(
    statement: dict[str, Any] | None,
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if statement is None or statement.get("canonical_zh") not in ADOPTED_NAMES:
        return None
    usable = is_usable_statement(statement)
    return {
        "statement_id": statement["statement_id"],
        "canonical_zh": statement.get("canonical_zh"),
        "entity_id": statement.get("entity_id"),
        "entity_type": statement.get("entity_type"),
        "statement_date": statement.get("statement_date"),
        "statement_type": statement.get("statement_type"),
        "statement_status": statement.get("statement_status"),
        "review_status": statement.get("review_status"),
        "reporting_scope": statement.get("reporting_scope"),
        "currency": statement.get("currency"),
        "unit_scale": statement.get("unit_scale"),
        "amount_decimal_places": statement.get("amount_decimal_places"),
        "identity_status": statement.get("identity_status"),
        "metadata_status": statement.get("metadata_status"),
        "entity_name_raw": statement.get("entity_name_raw"),
        "in_window": statement.get("statement_date") in COMPARISON_DATES,
        "usable": usable,
        "rows": rows if usable else [],
        "amounts": None,
        "summed": False,
        "mapping_counts": _count_rows(rows or []) if usable else empty_mapping_counts(),
        "reason": None if usable else _blocked_statement_reason(statement),
        "amounts_are_financially_verified": False,
        "presented_as_approved_scale": False,
    }


def _blocked_statement_reason(statement: dict[str, Any]) -> str:
    if statement.get("statement_date") not in COMPARISON_DATES:
        return "這份報表不在比較窗（2026-03-31、2026-06-30、2026-09-30）。不計入可用集，不顯示金額。"
    return "這份報表在比較窗內但未 adopted。只記為缺口，不顯示金額。"


def _count_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = empty_mapping_counts()
    for row in rows:
        status = row.get("mapping_status")
        if status in counts:
            counts[status] += 1
    return counts


def _gap(reason: str) -> dict[str, Any]:
    return {
        "kind": "gap",
        "reason": reason,
        "amounts": None,
        "statements": [],
        "mixed_statement_types": False,
    }


def _period_cell(
    statements: list[dict[str, Any]],
    counts_by_statement: dict[str, dict[str, int]],
) -> dict[str, Any]:
    usable = [statement for statement in statements if is_usable_statement(statement)]
    if not statements:
        return _gap("沒有報表")
    if not usable:
        return {
            "kind": "gap",
            "reason": _gap_reason(statements),
            "amounts": None,
            "statements": [_gap_identity(statement) for statement in statements],
            "mixed_statement_types": False,
        }
    summaries = [_usable_summary(statement, counts_by_statement) for statement in usable]
    return {
        "kind": "usable",
        "reason": None,
        "amounts": None,
        "statements": summaries,
        "mixed_statement_types": len({item["statement_type"] for item in summaries}) > 1,
    }


def _gap_reason(statements: list[dict[str, Any]]) -> str:
    if any(statement.get("review_status") == "reviewed" for statement in statements):
        return "在窗內，reviewed，未 adopted"
    statuses = sorted({str(statement.get("statement_status") or "unknown") for statement in statements})
    return "在窗內，未 adopted（statement_status=" + ",".join(statuses) + "）"


def _gap_identity(statement: dict[str, Any]) -> dict[str, Any]:
    return {
        "statement_id": statement["statement_id"],
        "statement_type": statement.get("statement_type"),
        "statement_status": statement.get("statement_status"),
        "review_status": statement.get("review_status"),
        "statement_date": statement.get("statement_date"),
        "amounts": None,
    }


def _usable_summary(
    statement: dict[str, Any],
    counts_by_statement: dict[str, dict[str, int]],
) -> dict[str, Any]:
    counts = counts_by_statement.get(statement["statement_id"], empty_mapping_counts())
    notes: list[str] = []
    if statement.get("currency") != "MOP":
        notes.append("幣別不是 MOP 或未記載。")
    if statement.get("unit_scale") not in (1, 1000, 1000000):
        notes.append("unit_scale 未記載或不在 1／1000／1000000。沒有換算。")
    return {
        "statement_id": statement["statement_id"],
        "statement_type": statement.get("statement_type"),
        "statement_date": statement.get("statement_date"),
        "currency": statement.get("currency"),
        "unit_scale": statement.get("unit_scale"),
        "amount_decimal_places": statement.get("amount_decimal_places"),
        "reporting_scope": statement.get("reporting_scope"),
        "entity_type": statement.get("entity_type"),
        "identity_status": statement.get("identity_status"),
        "metadata_status": statement.get("metadata_status"),
        "adopted": True,
        "mapping_counts": counts,
        "zero_approved": counts["approved"] == 0,
        "notes": notes,
        "amounts": None,
    }


def _excluded_identity(statement: dict[str, Any]) -> dict[str, Any]:
    adopted = (
        statement.get("statement_status") == "active"
        and bool(statement.get("adopted_statement_extraction_id"))
    )
    return {
        "canonical_zh": statement.get("canonical_zh"),
        "entity_id": statement.get("entity_id"),
        "statement_id": statement["statement_id"],
        "statement_date": statement.get("statement_date"),
        "statement_status": statement.get("statement_status"),
        "statement_type": statement.get("statement_type"),
        "adopted": adopted,
        "amounts": None,
        "included_in_usable_set": False,
    }


def _compare_cell(
    cell: dict[str, Any],
    balance_rows: list[dict[str, Any]],
    item_key: str | None,
) -> tuple[str, str | None, list[dict[str, Any]], list[str]]:
    """Classify one entity-date for cross-bank comparison.

    (a) gap: no adopted statement in the window.
    (b) no_balance_sheet: adopted statement(s), none of them a balance sheet.
    (c) absent: a balance sheet exists and the selected candidate is not on it.
    (d) values: the candidate is on an adopted balance sheet.

    Income-statement rows are never treated as the balance-sheet candidate.
    """
    adopted_types = _adopted_statement_types(cell)
    balance_sheets = [
        statement_type for statement_type in adopted_types
        if statement_type == BALANCE_SHEET
    ]
    if cell.get("kind") == "gap":
        return "gap", cell.get("reason"), [], adopted_types
    if not balance_sheets:
        listed = "、".join(adopted_types) if adopted_types else "沒有記載"
        return (
            "no_balance_sheet",
            "這期有 adopted 報表，但沒有 balance_sheet。"
            f"已有 statement_type：{listed}。"
            "沒有把其他報表類型當成所選候選。",
            [],
            adopted_types,
        )
    if not item_key:
        return "needs_item", None, [], adopted_types
    matched = [row for row in balance_rows if item_key in candidate_keys(row)]
    if matched:
        return "values", None, matched, adopted_types
    return "absent", "此期沒有此候選。這不是 0，亦不是缺口補數。", [], adopted_types


def _adopted_statement_types(cell: dict[str, Any]) -> list[str]:
    if cell.get("kind") != "usable":
        return []
    return sorted({
        statement["statement_type"]
        for statement in cell.get("statements") or []
        if statement.get("statement_type")
    })


def _balance_rows(
    cell: dict[str, Any],
    rows_by_statement: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if cell.get("kind") != "usable":
        return []
    rows: list[dict[str, Any]] = []
    for summary in cell.get("statements") or []:
        if summary.get("statement_type") != BALANCE_SHEET:
            continue
        rows.extend(rows_by_statement.get(summary["statement_id"], []))
    return rows


def _accumulate_catalogue(catalogue: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    for row in rows:
        keys = candidate_keys(row)
        targets = keys or [None]
        for key in targets:
            if not key:
                continue
            warnings = draft_warnings_for([key])
            source = next(
                (candidate for candidate in row["candidates"] if candidate.get("item_key") == key),
                None,
            )
            slot = catalogue.setdefault(key, {
                "item_key": key,
                "item_zh": source.get("item_zh") if source else None,
                "item_status": source.get("item_status") if source else None,
                "merged_into_standard_item_id": source.get("merged_into_standard_item_id") if source else None,
                "catalogue_current": catalogue_item_current(source),
                "row_count": 0,
                "mapping_counts": empty_mapping_counts(),
                "draft_warning": warnings[0] if warnings else None,
            })
            if source and not catalogue_item_current(source):
                slot["item_status"] = source.get("item_status")
                slot["merged_into_standard_item_id"] = source.get("merged_into_standard_item_id")
                slot["catalogue_current"] = False
            slot["row_count"] += 1
            status = row.get("mapping_status")
            if status in slot["mapping_counts"]:
                slot["mapping_counts"][status] += 1


def _default_date(entity: dict[str, Any]) -> str:
    for date in reversed(COMPARISON_DATES):
        if entity["periods"][date]["kind"] == "usable":
            return date
    return COMPARISON_DATES[-1]


def _selected_statement_rows(
    cell: dict[str, Any],
    rows_by_statement: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if cell.get("kind") != "usable":
        return []
    blocks = []
    for summary in cell.get("statements") or []:
        blocks.append({
            "statement": summary,
            "rows": rows_by_statement.get(summary["statement_id"], []),
            "total": None,
        })
    return blocks
