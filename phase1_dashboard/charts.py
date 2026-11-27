"""Candidate Explorer chart view for the phase-1 dashboard.

This module draws *candidate row observations* from adopted balance sheets.
It is not an approved indicator engine: no totals, no ranking, no share, no
sector aggregation.

Phase-0 correctness rules (MACAU_BANKING_DASHBOARD_DESIGN v0.2 §16):

* An exact candidate row beside another row that also mentions the candidate
  makes the whole entity-period ambiguous; the exact row does not hide it.
* Every chart point carries a comparison signature (currency, unit_scale,
  amount_decimal_places, reporting_scope, statement_type, row_type, side).
  Points with different signatures are never placed on one chart.
* Chart values are the human reading derived from ``amount_decimal_places``.
  The raw minor integer stays in the point for provenance.
* ``row_type`` and mapping status travel with each point.
* merged / excluded / retired entities are not drawn as current banks.
* a competing non-adopted balance sheet in the same period and scope is a gap,
  not a silently ignored alternative.
* one valid date is an observation, not a trend.
"""
from __future__ import annotations

import html
from decimal import Decimal
from typing import Any

from phase1_dashboard.present import (
    ScopeError,
    _balance_rows,
    build_coverage,
    candidate_keys,
    project_metadata_compliant,
)
from phase1_dashboard.scope import (
    BALANCE_SHEET,
    BUCKETS,
    COMPARISON_DATES,
    bucket_by_id,
    draft_warnings_for,
)

SIGNATURE_FIELDS: tuple[str, ...] = (
    "currency",
    "unit_scale",
    "amount_decimal_places",
    "reporting_scope",
    "statement_type",
    "row_type",
    "side",
)

SIGNATURE_LABELS: dict[str, str] = {
    "currency": "幣別",
    "unit_scale": "unit_scale",
    "amount_decimal_places": "小數位",
    "reporting_scope": "reporting_scope",
    "statement_type": "報表類型",
    "row_type": "row_type",
    "side": "借貸方向",
}

LIFECYCLE_BLOCKED = frozenset({"merged", "excluded", "retired"})


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _amount_sides(
    row: dict[str, Any],
) -> tuple[bool, bool, int | None, int | None]:
    """Return (debit_ok, credit_ok, debit_minor, credit_minor)."""
    debit_state = row.get("debit_state")
    credit_state = row.get("credit_state")
    debit_minor = (row.get("debit") or {}).get("minor")
    credit_minor = (row.get("credit") or {}).get("minor")
    debit_ok = debit_state == "amount" and debit_minor is not None
    credit_ok = credit_state == "amount" and credit_minor is not None
    return (
        debit_ok,
        credit_ok,
        int(debit_minor) if debit_ok else None,
        int(credit_minor) if credit_ok else None,
    )


def _one_sided_amount(row: dict[str, Any]) -> tuple[str, int, bool] | None:
    """Pick the sole amount side, with a narrow zero-counterpart rule.

    Exactly one side must be an amount. When both sides are recorded amounts
    and exactly one of them is zero, the zero is the printed counterpart of the
    other side, so the non-zero side is selected. The returned flag marks the
    point for review. Two non-zero sides, or two zero sides, stay unselected.
    """
    debit_ok, credit_ok, debit_minor, credit_minor = _amount_sides(row)
    if debit_ok and credit_ok:
        if debit_minor == 0 and credit_minor != 0:
            return "credit", int(credit_minor), True
        if credit_minor == 0 and debit_minor != 0:
            return "debit", int(debit_minor), True
        return None
    if debit_ok and debit_minor is not None:
        return "debit", debit_minor, False
    if credit_ok and credit_minor is not None:
        return "credit", credit_minor, False
    return None


def _decimal_places(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6:
        return value
    return None


def _display_amount(minor: int, decimal_places: int) -> str:
    """Format a stored minor integer using only amount_decimal_places.

    unit_scale is a statement-wide multiplier and is not applied here, exactly
    like ``present.format_minor``. Project statements are MOP / unit_scale 1.
    """
    value = Decimal(int(minor)).scaleb(-decimal_places)
    return f"{value:,.{decimal_places}f}"


def _side_label(side: str | None) -> str:
    return {"debit": "借方", "credit": "貸方"}.get(side or "", "—")


def _slot(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    slot = {
        "status": status,
        "reason": reason,
        "point": None,
        "included_in_chart": False,
    }
    slot.update(extra)
    return slot


def _is_lifecycle_blocked(entity: dict[str, Any]) -> bool:
    return bool(entity.get("merged")) or entity.get("registry_status") in LIFECYCLE_BLOCKED


def _lifecycle_slot(entity: dict[str, Any]) -> dict[str, Any]:
    status = entity.get("registry_status") or "merged"
    target = entity.get("merged_into_entity_id")
    reason = f"實體生命週期是 {status}"
    if target:
        reason += f"，併入 {target}"
    return _slot("lifecycle", reason + "。沒有當成現行銀行畫入圖。")


def _same_scope_competitors(
    cell: dict[str, Any], balance_summaries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Non-adopted balance sheets that compete with the adopted one."""
    adopted_scopes = {
        summary.get("reporting_scope")
        for summary in balance_summaries
        if summary.get("statement_type") == BALANCE_SHEET
    }
    competitors: list[dict[str, Any]] = []
    for candidate in cell.get("not_adopted") or []:
        if candidate.get("statement_type") != BALANCE_SHEET:
            continue
        scope = candidate.get("reporting_scope")
        if scope is None or scope in adopted_scopes:
            competitors.append(candidate)
    return competitors


def chart_slot(
    cell: dict[str, Any],
    rows_by_statement: dict[str, list[dict[str, Any]]],
    item_key: str,
) -> dict[str, Any]:
    """Decide whether one entity-date may become one chart point."""
    if cell.get("kind") == "gap":
        return _slot("gap", cell.get("reason") or "沒有報表")
    balance_summaries = [
        statement
        for statement in cell.get("statements") or []
        if statement.get("statement_type") == BALANCE_SHEET
    ]
    if not balance_summaries:
        return _slot("no_balance_sheet", "這期沒有 adopted 的 balance_sheet。")
    competitors = _same_scope_competitors(cell, balance_summaries)
    if competitors:
        names = "、".join(
            sorted({str(item.get("statement_id") or "未記 id") for item in competitors})
        )
        return _slot(
            "competing_statement",
            "同一結算日、同一 reporting_scope 另有未 adopted 的 balance_sheet"
            f"（{names}），未選其中一份。",
        )
    balance_rows = _balance_rows(cell, rows_by_statement)
    mentioning = [
        row for row in balance_rows if item_key in candidate_keys(row)
    ]
    # P1-1: any row that mentions the candidate together with another distinct
    # candidate makes the period ambiguous. A single exact row cannot hide it.
    ambiguous = [row for row in mentioning if len(candidate_keys(row)) != 1]
    if ambiguous:
        return _slot(
            "ambiguous",
            "同一行有多於一個不同候選（"
            + "、".join(
                sorted({key for row in ambiguous for key in candidate_keys(row)})
            )
            + "），沒有排入圖。",
        )
    exact = [row for row in mentioning if candidate_keys(row) == [item_key]]
    if len(exact) > 1:
        return _slot(
            "multiple_rows",
            f"同一報表有 {len(exact)} 行此候選，未選其中一行，亦未相加。",
        )
    if not exact:
        return _slot("absent", "此期沒有此候選。這不是 0。")
    row = exact[0]
    if not row.get("catalogue_current"):
        return _slot("inactive_candidate", "候選科目不是現行有效項，沒有畫入圖。")
    if not project_metadata_compliant(row.get("currency"), row.get("unit_scale")):
        return _slot(
            "metadata",
            "currency / unit_scale 不是 MOP、1。沒有換算，沒有畫入圖。",
        )
    decimal_places = _decimal_places(row.get("amount_decimal_places"))
    if decimal_places is None:
        return _slot(
            "decimal_places",
            "amount_decimal_places 未記載或不合法。沒有把小數位當成 2，亦沒有畫入圖。",
        )
    sided = _one_sided_amount(row)
    if sided is None:
        debit_ok, credit_ok, debit_minor, credit_minor = _amount_sides(row)
        if debit_ok and credit_ok and debit_minor == 0 and credit_minor == 0:
            reason = (
                "借貸兩欄都是 0.00"
                f"（借方 {_display_amount(0, decimal_places)}；"
                f"貸方 {_display_amount(0, decimal_places)}），"
                "沒有非零單邊金額可選，沒有畫入圖。"
            )
        elif debit_ok and credit_ok:
            reason = (
                "借貸兩欄都是非零值"
                f"（借方 {_display_amount(debit_minor, decimal_places)}；"
                f"貸方 {_display_amount(credit_minor, decimal_places)}），"
                "不能確定單一金額，沒有選邊。"
            )
        else:
            reason = "借貸兩欄不能確定單一金額，沒有選邊。"
        return _slot("amount_side", reason)
    side, minor, zero_counterpart = sided
    source = next(
        (candidate for candidate in row.get("candidates") or []
         if candidate.get("item_key") == item_key),
        None,
    )
    return {
        "status": "chart",
        "reason": None,
        "included_in_chart": True,
        "point": {
            "side": side,
            "minor": minor,
            "display": _display_amount(minor, decimal_places),
            "item_key": item_key,
            "item_zh": source.get("item_zh") if source else None,
            "mapping_status": row.get("mapping_status"),
            "match_methods": list(row.get("match_methods") or []),
            "label": row.get("label"),
            "row_type": row.get("row_type"),
            "statement_id": row.get("statement_id"),
            "statement_row_id": row.get("statement_row_id"),
            "statement_type": row.get("statement_type"),
            "reporting_scope": row.get("reporting_scope"),
            "currency": row.get("currency"),
            "unit_scale": row.get("unit_scale"),
            "amount_decimal_places": decimal_places,
            "zero_counterpart": zero_counterpart,
            "financially_validated": False,
            "presented_as_approved_scale": False,
        },
    }


def _signature(point: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(point.get(field) for field in SIGNATURE_FIELDS)


def _signature_text(point: dict[str, Any]) -> str:
    return (
        f"{point.get('currency') or '幣別未記載'} · unit_scale "
        f"{point.get('unit_scale') if point.get('unit_scale') is not None else '未記載'} · "
        f"{point.get('amount_decimal_places')} 位小數 · "
        f"{point.get('reporting_scope') or 'scope 未記載'} · "
        f"{point.get('row_type') or 'row_type 未記載'} · "
        f"{_side_label(point.get('side'))}"
    )


def _mismatch_note(
    points: list[dict[str, Any]], ignore: tuple[str, ...] = ()
) -> tuple[str, list[str]]:
    signatures = [_signature(point) for point in points]
    differing = [
        SIGNATURE_LABELS[field]
        for index, field in enumerate(SIGNATURE_FIELDS)
        if field not in ignore
        and len({signature[index] for signature in signatures}) > 1
    ]
    note = (
        "比較簽章不一致（" + "、".join(differing) + "）。"
        "不同幣別、單位、小數位、報表範圍或 row_type 不放在同一張圖；"
        "逐點原因見下面，金額仍可在對照表追溯。"
    )
    return note, differing


def _bucket_balance_rows(
    entities: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity in entities:
        if _is_lifecycle_blocked(entity):
            continue
        for date in COMPARISON_DATES:
            rows.extend(_balance_rows(entity["periods"][date], rows_by_statement))
    return rows


def _item_catalogue(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for row in rows:
        for candidate in row.get("candidates") or []:
            key = candidate.get("item_key")
            if key and key not in found:
                found[key] = candidate.get("item_zh")
    return found


def _chartable_count(
    entities: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
    item_key: str,
    statement_date: str,
) -> int:
    count = 0
    for entity in entities:
        if _is_lifecycle_blocked(entity):
            continue
        cell = entity["periods"][statement_date]
        slot = chart_slot(cell, rows_by_statement, item_key)
        if slot["included_in_chart"]:
            count += 1
    return count


_DISPLAY_PRIORITY: dict[str, int] = {
    "user:放款": 0,
    "user:現金": 1,
    "user:定期存款": 2,
    "user:活期存款": 3,
    "user:通知存款": 4,
    "user:總收入": 10,
    "user:總支出": 11,
    "user:股本": 20,
}


def _item_preference(item_key: str) -> tuple[int, int, str]:
    """Prefer a top-level candidate over a child row or the balance total.

    This is a display default only; it does not make any candidate an approved
    indicator. ``user:總計`` is deliberately last because it is not total assets.
    """
    penalty = _DISPLAY_PRIORITY.get(item_key, 30)
    if item_key == "user:總計":
        penalty = 1000
    elif ">" in item_key:
        penalty += 100
    return (penalty, 0, item_key)


def _choose_item_and_date(
    entities: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
    catalogue: dict[str, str | None],
) -> tuple[str | None, str]:
    best_key = None
    best_score: tuple[int, tuple[int, int, str]] | None = None
    for key in sorted(catalogue):
        count = sum(
            _chartable_count(entities, rows_by_statement, key, date)
            for date in COMPARISON_DATES
        )
        # Higher count first; then the display preference penalty.
        score = (-count, _item_preference(key))
        if best_score is None or score < best_score:
            best_key = key
            best_score = score
    if best_key is None:
        return None, COMPARISON_DATES[-1]
    best_date = COMPARISON_DATES[-1]
    best_date_count = -1
    for date in COMPARISON_DATES:
        count = _chartable_count(entities, rows_by_statement, best_key, date)
        if count > best_date_count or (count == best_date_count and date >= best_date):
            best_date = date
            best_date_count = count
    return best_key, best_date


def _choose_entity(
    entities: list[dict[str, Any]], trend_ready: dict[str, bool]
) -> dict[str, Any] | None:
    resolved = [
        entity
        for entity in entities
        if entity.get("entity_id") and not _is_lifecycle_blocked(entity)
    ]
    for entity in resolved:
        if trend_ready.get(entity["entity_id"]):
            return entity
    return resolved[0] if resolved else None


def build_chart_view(
    *,
    bucket_id: str | None,
    statement_date: str | None,
    item_key: str | None,
    entity_id: str | None,
    entities: list[dict[str, Any]],
    statements: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
    coverage: dict[str, Any] | None = None,
    publication_count: int = 0,
    publication_total: int = 0,
) -> dict[str, Any]:
    if bucket_id is None:
        bucket_id = "legal"
    bucket = bucket_by_id(bucket_id)
    if bucket is None:
        raise ScopeError("圖表只可以選擇一個分組：本地法人、關聯報表，或外地銀行分行。")
    if statement_date is not None and statement_date not in COMPARISON_DATES:
        raise ScopeError("比較日必須是 2026-03-31、2026-06-30 或 2026-09-30。")
    coverage = coverage or build_coverage(
        entities=entities,
        statements=statements,
        rows_by_statement=rows_by_statement,
    )
    bucket_model = next(item for item in coverage["buckets"] if item["id"] == bucket_id)
    bucket_entities = bucket_model["entities"]
    catalogue = _item_catalogue(_bucket_balance_rows(bucket_entities, rows_by_statement))
    chosen_item, chosen_date = _choose_item_and_date(
        bucket_entities, rows_by_statement, catalogue
    )
    explicit_item = bool(item_key)
    explicit_date = statement_date is not None
    item_key = item_key or chosen_item
    statement_date = statement_date or chosen_date
    if item_key is None:
        series = [
            {
                "canonical_zh": entity["canonical_zh"],
                "entity_id": entity.get("entity_id"),
                **_slot("absent", "資料庫裡沒有可選的單一候選。"),
            }
            for entity in bucket_entities
        ]
    else:
        series = []
        for entity in bucket_entities:
            if _is_lifecycle_blocked(entity):
                slot = _lifecycle_slot(entity)
            else:
                slot = chart_slot(
                    entity["periods"][statement_date], rows_by_statement, item_key
                )
            series.append({
                "canonical_zh": entity["canonical_zh"],
                "entity_id": entity.get("entity_id"),
                "registry_status": entity.get("registry_status"),
                "merged_into_entity_id": entity.get("merged_into_entity_id"),
                **slot,
            })
    cross = _cross_bank(series)
    trend_by_entity: dict[str, list[dict[str, Any]]] = {}
    trend_ready: dict[str, bool] = {}
    if item_key:
        for entity in bucket_entities:
            if not entity.get("entity_id") or _is_lifecycle_blocked(entity):
                continue
            quarters = [
                {
                    "statement_date": date,
                    **chart_slot(entity["periods"][date], rows_by_statement, item_key),
                }
                for date in COMPARISON_DATES
            ]
            trend_by_entity[entity["entity_id"]] = quarters
            trend_ready[entity["entity_id"]] = any(
                item["included_in_chart"] for item in quarters
            )
    selectable = [
        entity
        for entity in bucket_entities
        if entity.get("entity_id") and not _is_lifecycle_blocked(entity)
    ]
    if entity_id and entity_id not in {entity.get("entity_id") for entity in selectable}:
        raise ScopeError("所選機構不在這個分組，或已不在現行生命週期。")
    if entity_id:
        selected = next(
            entity for entity in selectable if entity.get("entity_id") == entity_id
        )
    else:
        selected = _choose_entity(bucket_entities, trend_ready)
    trend = _trend(
        trend_by_entity.get(selected["entity_id"], []) if selected else []
    )
    item_zh = catalogue.get(item_key) if item_key else None
    kpis = _kpis(
        coverage=coverage,
        chartable_points=len(
            [slot for slot in cross["slots"] if slot.get("included_in_chart")]
        ),
        publication_count=publication_count,
        publication_total=publication_total,
    )
    return {
        "bucket": bucket_id,
        "bucket_title": bucket["title"],
        "bucket_rule": bucket["rule"],
        "buckets": [
            {"id": item["id"], "title": item["title"]}
            for item in BUCKETS
        ],
        "statement_date": statement_date,
        "window": list(COMPARISON_DATES),
        "item_key": item_key,
        "item_zh": item_zh,
        "explicit_item": explicit_item,
        "explicit_date": explicit_date,
        "available_items": [
            {
                "item_key": key,
                "item_zh": catalogue[key],
                "chartable_on_date": _chartable_count(
                    bucket_entities, rows_by_statement, key, statement_date
                ),
            }
            for key in sorted(
                catalogue,
                key=lambda key: (
                    -_chartable_count(
                        bucket_entities, rows_by_statement, key, statement_date
                    ),
                    _item_preference(key),
                ),
            )
        ],
        "entities": [
            {
                "entity_id": entity.get("entity_id"),
                "canonical_zh": entity["canonical_zh"],
            }
            for entity in selectable
        ],
        "selected_entity_id": selected.get("entity_id") if selected else None,
        "selected_entity_name": selected["canonical_zh"] if selected else None,
        "cross_bank": cross,
        "trend": trend,
        "coverage": coverage,
        "coverage_matrix": build_coverage_matrix(coverage),
        "kpis": kpis,
        "publication": {
            "current": publication_count,
            "total": publication_total,
            "formal_indicators_enabled": publication_count > 0,
        },
        "warnings": draft_warnings_for([item_key] if item_key else []),
        "financially_validated": False,
        "presented_as_approved_scale": False,
        "is_total_assets": False,
        "summed": False,
        "ranked": False,
        "caption": _caption(
            item_key, item_zh, statement_date, bucket["title"], explicit_item
        ),
    }


def _kpis(
    *,
    coverage: dict[str, Any],
    chartable_points: int,
    publication_count: int,
    publication_total: int,
) -> dict[str, Any]:
    total_cells = 0
    usable_cells = 0
    for bucket in coverage["buckets"]:
        for entity in bucket["entities"]:
            for date in COMPARISON_DATES:
                total_cells += 1
                if entity["periods"][date]["kind"] == "usable":
                    usable_cells += 1
    mapping = coverage["mapping"]
    return {
        "usable_statement_count": coverage["usable_statement_count"],
        "usable_cells": usable_cells,
        "total_cells": total_cells,
        "gap_count": coverage["gap_count"],
        "chartable_points": chartable_points,
        "approved_rows": mapping["approved"],
        "proposed_rows": mapping["proposed"],
        "insert_direct_rows": mapping["insert-direct"],
        "unmapped_rows": mapping["unmapped"],
        "publication_count": publication_count,
        "publication_total": publication_total,
        "formal_indicators_enabled": False,
    }


def build_coverage_matrix(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    """Compact bucket x date coverage table for the overview page."""
    rows: list[dict[str, Any]] = []
    for bucket in coverage["buckets"]:
        cells = []
        for date in COMPARISON_DATES:
            usable = sum(
                1 for entity in bucket["entities"]
                if entity["periods"][date]["kind"] == "usable"
            )
            gaps = sum(
                1 for entity in bucket["entities"]
                if entity["periods"][date]["kind"] == "gap"
            )
            cells.append({
                "statement_date": date,
                "usable": usable,
                "gaps": gaps,
                "total": usable + gaps,
            })
        rows.append({
            "id": bucket["id"],
            "title": bucket["title"],
            "rule": bucket["rule"],
            "cells": cells,
        })
    return rows


def _caption(
    item_key: str | None,
    item_zh: str | None,
    statement_date: str,
    bucket_title: str,
    explicit_item: bool = False,
) -> str:
    label = item_zh or item_key or "未選候選"
    text = (
        f"{label} · {statement_date} · {bucket_title}。"
        "圖上是已採納報表內的候選行觀察，不是已核定金融指標，也不是財務核實。"
    )
    if not explicit_item:
        text += " 預設選了覆蓋最多的候選，不代表指標重要性。"
    if item_key == "user:總計":
        text += " user:總計 不是總資產。"
    return text


def _cross_bank(series: list[dict[str, Any]]) -> dict[str, Any]:
    bars = [slot for slot in series if slot["included_in_chart"] and slot["point"]]
    points = [slot["point"] for slot in bars]
    signatures = {_signature(point) for point in points}
    drawn = False
    reason = None
    signature_note = "未畫入圖。"
    if len(bars) == 0:
        reason = "這一組沒有符合並排條件的機構。缺口留在下面，不畫成 0。"
    elif len(bars) == 1:
        reason = "只有一間符合並排條件，不畫成跨行比較。"
    elif len(signatures) > 1:
        signature_note, _ = _mismatch_note(points)
        reason = signature_note
        # Do not leave a half-drawn comparison: every point is excluded.
        for slot in series:
            if slot["included_in_chart"]:
                slot["included_in_chart"] = False
                slot["signature_mismatch"] = True
                slot["reason"] = signature_note
    else:
        drawn = True
        signature_note = "全部點共用同一比較簽章：" + _signature_text(points[0]) + "。"
    svg = _bar_svg(series) if drawn else None
    side = points[0].get("side") if drawn and points else None
    return {
        "drawn": drawn,
        "mode": "comparison" if drawn else None,
        "reason": reason,
        "side": side,
        "signature_note": signature_note,
        "signature_fields": list(SIGNATURE_FIELDS),
        "slots": [
            {
                "canonical_zh": slot["canonical_zh"],
                "entity_id": slot.get("entity_id"),
                "registry_status": slot.get("registry_status"),
                "status": slot["status"],
                "reason": slot["reason"],
                "minor": (
                    slot["point"]["minor"]
                    if slot["point"] and slot["included_in_chart"] and drawn
                    else None
                ),
                "display": (
                    slot["point"]["display"]
                    if slot["point"] and slot["included_in_chart"] and drawn
                    else None
                ),
                "row_type": (
                    slot["point"]["row_type"] if slot["point"] else None
                ),
                "zero_counterpart": (
                    slot["point"].get("zero_counterpart")
                    if slot["point"]
                    else None
                ),
                "mapping_status": (
                    slot["point"]["mapping_status"] if slot["point"] else None
                ),
                "statement_id": (
                    slot["point"]["statement_id"]
                    if slot["point"] and slot["included_in_chart"] and drawn else None
                ),
                "statement_row_id": (
                    slot["point"]["statement_row_id"]
                    if slot["point"] and slot["included_in_chart"] and drawn else None
                ),
                "included_in_chart": bool(
                    slot["included_in_chart"] and drawn
                ),
            }
            for slot in series
        ],
        "svg": svg,
        "summed": False,
        "ranked": False,
        "order": [slot["canonical_zh"] for slot in series],
    }


def _trend(quarters: list[dict[str, Any]]) -> dict[str, Any]:
    charted = [
        slot for slot in quarters
        if slot.get("included_in_chart") and slot.get("point")
    ]
    points = [slot["point"] for slot in charted]
    signatures = {_signature(point) for point in points}
    drawn = False
    is_trend = False
    mode = None
    reason = None
    signature_note = "未畫入圖。"
    if not quarters:
        reason = "未選機構。"
    elif not points:
        reason = "這間機構三季都沒有可畫的單一候選。缺季不是 0。"
    elif len(signatures) > 1:
        signature_note, _ = _mismatch_note(points)
        reason = signature_note
        for slot in quarters:
            if slot.get("included_in_chart"):
                slot["included_in_chart"] = False
                slot["signature_mismatch"] = True
                slot["reason"] = signature_note
    else:
        drawn = True
        is_trend = len(points) >= 2
        mode = "trend" if is_trend else "observation"
        signature_note = "全部點共用同一比較簽章：" + _signature_text(points[0]) + "。"
        if not is_trend:
            reason = "只有一個有效日期，這是單期觀察，不是趨勢。"
    return {
        "drawn": drawn,
        "is_trend": is_trend,
        "mode": mode,
        "reason": reason,
        "side": points[0].get("side") if drawn and points else None,
        "signature_note": signature_note,
        "slots": [
            {
                "statement_date": slot["statement_date"],
                "status": slot["status"],
                "reason": slot["reason"],
                "minor": (
                    slot["point"]["minor"]
                    if slot.get("included_in_chart") and slot.get("point")
                    else None
                ),
                "display": (
                    slot["point"]["display"]
                    if slot.get("included_in_chart") and slot.get("point")
                    else None
                ),
                "row_type": slot["point"]["row_type"] if slot.get("point") else None,
                "zero_counterpart": (
                    slot["point"].get("zero_counterpart")
                    if slot.get("point")
                    else None
                ),
                "mapping_status": (
                    slot["point"]["mapping_status"] if slot.get("point") else None
                ),
                "statement_id": (
                    slot["point"]["statement_id"]
                    if slot.get("included_in_chart") and slot.get("point") and drawn
                    else None
                ),
                "statement_row_id": (
                    slot["point"]["statement_row_id"]
                    if slot.get("included_in_chart") and slot.get("point") and drawn
                    else None
                ),
                "included_in_chart": bool(slot.get("included_in_chart") and drawn),
            }
            for slot in quarters
        ],
        "svg": _trend_svg(quarters, mode) if drawn else None,
        "summed": False,
        "ranked": False,
    }


def _bar_svg(series: list[dict[str, Any]]) -> str:
    bars = [slot for slot in series if slot["included_in_chart"]]
    values = [Decimal(slot["point"]["display"].replace(",", "")) for slot in bars]
    max_abs = max((abs(value) for value in values), default=Decimal(0)) or Decimal(1)
    side = bars[0]["point"]["side"]
    side_label = _side_label(side)
    row_h = 52
    label_w = 308
    plot_w = 430
    pad_top = 34
    width = label_w + plot_w + 190
    height = pad_top + len(series) * row_h + 42
    decimal_places = bars[0]["point"]["amount_decimal_places"]
    parts = [
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="跨行候選觀察，順序依已採納名單，不是排名">',
        f'<text x="{label_w}" y="18" class="axis-title">'
        f'顯示金額（MOP · {decimal_places} 位小數）· {side_label} · 候選行</text>',
    ]
    zero_x = label_w
    for index, slot in enumerate(series):
        y = pad_top + index * row_h
        parts.append(
            f'<text x="8" y="{y + 28}" class="bar-label">{_esc(slot["canonical_zh"])}</text>'
        )
        if not slot["included_in_chart"]:
            parts.append(
                f'<text x="{label_w}" y="{y + 28}" class="gap-label">'
                f'缺口 · {_esc(slot["reason"])}</text>'
            )
            continue
        point = slot["point"]
        value = Decimal(point["display"].replace(",", ""))
        span = int(abs(value) / max_abs * (plot_w - 8))
        span = max(span, 2)
        x = zero_x - span if value < 0 else zero_x
        parts.append(
            f'<rect x="{x}" y="{y + 10}" width="{span}" height="22" class="bar" '
            f'data-name="{_esc(slot["canonical_zh"])}" data-minor="{point["minor"]}" '
            f'data-display="{_esc(point["display"])}" data-order="{index}" '
            f'data-zero-counterpart="{1 if point.get("zero_counterpart") else 0}" '
            f'data-row-type="{_esc(point["row_type"])}"></rect>'
        )
        parts.append(
            f'<text x="{x + span + 8}" y="{y + 20}" class="bar-value">'
            f'{_esc(point["display"])}</text>'
        )
        parts.append(
            f'<text x="{x + span + 8}" y="{y + 34}" class="axis-note">'
            f'{_esc(point["mapping_status"])} · '
            f'{_esc(point["row_type"] or "row_type 未記載")}</text>'
        )
    parts.append(
        f'<text x="{label_w}" y="{height - 12}" class="axis-note">'
        f'由上至下是名單順序，不是按金額排序。沒有合計，沒有排名。</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _trend_svg(quarters: list[dict[str, Any]], mode: str | None) -> str:
    points = [slot for slot in quarters if slot.get("included_in_chart")]
    values = [
        Decimal(slot["point"]["display"].replace(",", "")) for slot in points
    ]
    side = points[0]["point"]["side"]
    side_label = _side_label(side)
    decimal_places = points[0]["point"]["amount_decimal_places"]
    width = 640
    height = 280
    left = 76
    right = 28
    top = 36
    bottom = 52
    plot_w = width - left - right
    plot_h = height - top - bottom
    xs = [left + int(index * plot_w / 2) for index in range(3)]
    value_min = min(values)
    value_max = max(values)
    spread = value_max - value_min
    if spread == 0:
        spread = abs(value_max) or Decimal(1)
        spread *= Decimal("0.1")
    lower = value_min - spread * Decimal("0.15")
    upper = value_max + spread * Decimal("0.15")
    if lower > 0 and value_min >= 0:
        lower = max(Decimal(0), lower)

    def y_of(display: str) -> int:
        value = Decimal(display.replace(",", ""))
        ratio = (value - lower) / (upper - lower)
        return top + int(plot_h - ratio * plot_h)

    def axis_amount(value: Decimal) -> str:
        magnitude = abs(value)
        if magnitude >= Decimal("1000000000"):
            return f"{value / Decimal('1000000000'):,.2f}B"
        if magnitude >= Decimal("1000000"):
            return f"{value / Decimal('1000000'):,.2f}M"
        return f"{value:,.{decimal_places}f}"

    parts = [
        f'<svg class="chart-svg trend" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="同一機構候選觀察，缺期不連線、不是 0">',
        f'<text x="{left}" y="18" class="axis-title">'
        f'顯示金額（MOP · {decimal_places} 位小數）· {side_label} · '
        f'{"趨勢" if mode == "trend" else "單期觀察"}</text>',
        f'<text x="{width - right}" y="18" text-anchor="end" class="axis-note">'
        f'B = 十億 · M = 百萬</text>',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{width - right}" '
        f'y2="{top + plot_h}" class="axis-line"></line>',
        f'<text x="{left - 8}" y="{top + 4}" text-anchor="end" class="axis-note">'
        f'{_esc(axis_amount(upper))}</text>',
        f'<text x="{left - 8}" y="{top + plot_h}" text-anchor="end" class="axis-note">'
        f'{_esc(axis_amount(lower))}</text>',
    ]
    previous = None
    for index, slot in enumerate(quarters):
        x = xs[index]
        date_anchor = "start" if index == 0 else "end" if index == len(quarters) - 1 else "middle"
        date_x = x + (4 if index == 0 else -4 if index == len(quarters) - 1 else 0)
        parts.append(
            f'<text x="{date_x}" y="{height - 16}" text-anchor="{date_anchor}" '
            f'class="bar-label">{_esc(slot["statement_date"])}</text>'
        )
        if not slot.get("included_in_chart"):
            parts.append(
                f'<text x="{x}" y="{top + plot_h - 12}" text-anchor="middle" '
                f'class="gap-label">缺口</text>'
            )
            previous = None
            continue
        point = slot["point"]
        y = y_of(point["display"])
        if previous is not None:
            parts.append(
                f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{x}" y2="{y}" '
                f'class="trend-line"></line>'
            )
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="5" class="bar" '
            f'data-date="{_esc(slot["statement_date"])}" data-minor="{point["minor"]}" '
            f'data-display="{_esc(point["display"])}" '
            f'data-zero-counterpart="{1 if point.get("zero_counterpart") else 0}" '
            f'data-row-type="{_esc(point["row_type"])}"></circle>'
        )
        value_anchor = "start" if index == 0 else "end" if index == len(quarters) - 1 else "middle"
        value_x = x + (4 if index == 0 else -4 if index == len(quarters) - 1 else 0)
        parts.append(
            f'<text x="{value_x}" y="{y - 10}" text-anchor="{value_anchor}" '
            f'class="bar-value">{_esc(point["display"])}</text>'
        )
        previous = (x, y)
    if mode == "observation":
        parts.append(
            f'<text x="{width - right}" y="{height - 34}" text-anchor="end" '
            f'class="axis-note">只有一個有效日期，不畫成趨勢。</text>'
        )
    parts.append("</svg>")
    return "".join(parts)
