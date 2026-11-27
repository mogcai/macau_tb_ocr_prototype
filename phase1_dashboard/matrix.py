"""Multi-entity × multi-item × multi-period candidate comparison.

This is the grouped-bar view inspired by the scratch notebook pivot: one group
per entity × candidate item, one bar per statement date. It stays inside the
Candidate Explorer rules:

* every bar is one adopted balance-sheet row observation; nothing is summed,
  ranked, or netted from debit minus credit;
* a missing point stays a gap, never 0;
* the full comparison signature must match across every drawn point;
* groups keep the perimeter-list order, not amount order;
* the optional signed display negates credit-side amounts (× -1) so debit and
  credit items can share a chart; it is a sign convention, not a net amount.

The notebook computed ``abs(debit - credit) / 100`` and summed rows with
``pivot(aggregate_function="sum")``. Neither is reproduced here: the dashboard
shows the one-sided stored amount, formatted with ``amount_decimal_places``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from phase1_dashboard.charts import (
    SIGNATURE_FIELDS,
    SIGNATURE_LABELS,
    _bucket_balance_rows,
    _esc,
    _is_lifecycle_blocked,
    _item_catalogue,
    _item_preference,
    _mismatch_note,
    _signature_text,
    _side_label,
    chart_slot,
)
from phase1_dashboard.present import ScopeError, build_coverage
from phase1_dashboard.scope import BUCKETS, COMPARISON_DATES, bucket_by_id

MAX_ENTITIES = 8
MAX_ITEMS = 6
MAX_GROUPS = 24
DATE_STYLES = ("bar-date-0", "bar-date-1", "bar-date-2")


def _chartable_on(
    entities: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
    item_key: str,
    dates: tuple[str, ...],
) -> int:
    count = 0
    for entity in entities:
        if _is_lifecycle_blocked(entity):
            continue
        for date in dates:
            if chart_slot(
                entity["periods"][date], rows_by_statement, item_key
            )["included_in_chart"]:
                count += 1
    return count


def _selectable(bucket_entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        entity
        for entity in bucket_entities
        if entity.get("entity_id") and not _is_lifecycle_blocked(entity)
    ]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _resolve_entities(
    requested: list[str] | None,
    options: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {option["entity_id"]: option for option in options}
    if requested is None:
        return options, []
    wanted = set(_dedupe(requested))
    ignored = sorted(wanted - set(by_id))
    selected = [option for option in options if option["entity_id"] in wanted]
    return selected, ignored


def _resolve_items(
    requested: list[str] | None,
    catalogue: dict[str, str | None],
    chartable: dict[str, int],
) -> tuple[list[str], list[str]]:
    ordered = sorted(
        catalogue,
        key=lambda key: (-chartable.get(key, 0), _item_preference(key)),
    )
    if requested is None:
        return ordered[:1], []
    wanted = _dedupe(requested)
    wanted_set = set(wanted)
    ignored = sorted(key for key in wanted if key not in catalogue)
    selected = [key for key in ordered if key in wanted_set]
    return selected, ignored


def _resolve_dates(requested: list[str] | None) -> list[str]:
    if requested is None:
        return list(COMPARISON_DATES)
    wanted = set(_dedupe(requested))
    unknown = sorted(wanted - set(COMPARISON_DATES))
    if unknown:
        raise ScopeError(
            "比較日必須是 2026-03-31、2026-06-30 或 2026-09-30。"
            f"不認識：{'、'.join(unknown)}。"
        )
    return [date for date in COMPARISON_DATES if date in wanted]


def build_matrix_view(
    *,
    bucket_id: str | None,
    entity_ids: list[str] | None,
    item_keys: list[str] | None,
    statement_dates: list[str] | None,
    signed: bool = False,
    entities: list[dict[str, Any]],
    statements: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if bucket_id is None:
        bucket_id = "legal"
    bucket = bucket_by_id(bucket_id)
    if bucket is None:
        raise ScopeError(
            "圖表只可以選擇一個分組：本地法人、關聯報表，或外地銀行分行。"
        )
    dates = _resolve_dates(statement_dates)
    coverage = coverage or build_coverage(
        entities=entities,
        statements=statements,
        rows_by_statement=rows_by_statement,
    )
    bucket_model = next(item for item in coverage["buckets"] if item["id"] == bucket_id)
    bucket_entities = bucket_model["entities"]
    selectable = _selectable(bucket_entities)
    catalogue = _item_catalogue(
        _bucket_balance_rows(bucket_entities, rows_by_statement)
    )
    window = tuple(COMPARISON_DATES)
    chartable = {
        key: _chartable_on(bucket_entities, rows_by_statement, key, window)
        for key in catalogue
    }
    selected_entities, ignored_entities = _resolve_entities(entity_ids, selectable)
    selected_items, ignored_items = _resolve_items(item_keys, catalogue, chartable)
    entity_options = [
        {
            "entity_id": entity.get("entity_id"),
            "entity_key": entity.get("entity_key"),
            "canonical_zh": entity["canonical_zh"],
            "available_periods": sum(
                1
                for date in COMPARISON_DATES
                if entity["periods"][date]["kind"] == "usable"
            ),
        }
        for entity in selectable
    ]
    item_options = [
        {
            "item_key": key,
            "item_zh": catalogue[key],
            "chartable": chartable[key],
        }
        for key in sorted(
            catalogue,
            key=lambda key: (-chartable[key], _item_preference(key)),
        )
    ]

    groups: list[dict[str, Any]] = []
    over_limit_reason = None
    if len(selected_items) > MAX_ITEMS:
        over_limit_reason = (
            f"選了 {len(selected_items)} 個候選，超過上限 {MAX_ITEMS} 個。"
            "請收窄選取，令同一張圖仍然可讀。"
        )
    elif len(selected_entities) * len(selected_items) > MAX_GROUPS:
        over_limit_reason = (
            f"選了 {len(selected_entities)} 間機構 × {len(selected_items)} 個候選 = "
            f"{len(selected_entities) * len(selected_items)} 組，"
            f"超過上限 {MAX_GROUPS} 組。請收窄選取。"
        )

    if not selected_entities:
        reason = "未選機構。缺口不是 0；請選擇至少一間機構。"
    elif not selected_items:
        reason = "未選候選標準項。請選擇至少一個候選。"
    elif not dates:
        reason = "未選結算日。請選擇至少一個結算日。"
    elif over_limit_reason:
        reason = over_limit_reason
    else:
        reason = None
        for entity in selected_entities:
            for key in selected_items:
                cells = {
                    date: chart_slot(
                        entity["periods"][date], rows_by_statement, key
                    )
                    for date in dates
                }
                groups.append({
                    "entity_id": entity["entity_id"],
                    "entity_key": entity.get("entity_key"),
                    "canonical_zh": entity["canonical_zh"],
                    "item_key": key,
                    "item_zh": catalogue.get(key),
                    "cells": cells,
                })

    points = [
        slot["point"]
        for group in groups
        for slot in group["cells"].values()
        if slot["included_in_chart"] and slot["point"]
    ]
    signature_fields = tuple(
        field for field in SIGNATURE_FIELDS if not (signed and field == "side")
    )
    signatures = {
        tuple(point.get(field) for field in signature_fields) for point in points
    }
    drawn = False
    mode = None
    signature_note = "未畫入圖。"
    if reason is None:
        if not points:
            reason = (
                "選取的機構、候選及日期沒有任何可畫的單一候選。"
                "缺口留在下面，不畫成 0。"
            )
        elif len(signatures) > 1:
            signature_note, differing = _mismatch_note(
                points, ignore=("side",) if signed else ()
            )
            reason = signature_note
            if differing == [SIGNATURE_LABELS["side"]] and not signed:
                hint = (
                    " 可勾選「貸方 × -1」再更新，將貸方以負數顯示，"
                    "資產與負債就可以同圖。"
                )
                reason += hint
            for group in groups:
                for slot in group["cells"].values():
                    if slot["included_in_chart"]:
                        slot["included_in_chart"] = False
                        slot["signature_mismatch"] = True
                        slot["reason"] = reason
        else:
            drawn = True
            mode = "comparison" if len(points) > 1 else "observation"
            if signed:
                signature_note = (
                    "全部柱共用同一比較簽章（貸方已 × -1，借貸方向不比較）："
                    + _signed_signature_text(points[0])
                    + "。"
                )
            else:
                signature_note = (
                    "全部柱共用同一比較簽章：" + _signature_text(points[0]) + "。"
                )
            if mode == "observation":
                reason = "只有一個有效點，這是單期觀察，不是趨勢或排名。"
    if signed:
        for group in groups:
            for slot in group["cells"].values():
                if slot.get("point"):
                    slot["point"] = _signed_point(slot["point"])

    slots = [
        _trace_slot(group, date)
        for group in groups
        for date in group["cells"]
    ]
    gap_count = sum(1 for row in slots if not row["included_in_chart"])
    svg = _matrix_svg(groups, dates, signed) if drawn else None
    return {
        "bucket": bucket_id,
        "bucket_title": bucket["title"],
        "bucket_rule": bucket["rule"],
        "buckets": [{"id": item["id"], "title": item["title"]} for item in BUCKETS],
        "window": list(COMPARISON_DATES),
        "selected_dates": dates,
        "selected_entities": [
            entity["entity_id"] for entity in selected_entities
        ],
        "selected_items": selected_items,
        "entities": entity_options,
        "available_items": item_options,
        "entity_select_size": min(max(len(entity_options), 4), MAX_ENTITIES),
        "item_select_size": min(max(len(item_options), 6), 10),
        "ignored_entities": ignored_entities,
        "ignored_items": ignored_items,
        "signed": signed,
        "sign_rule": (
            "貸方（credit）× -1；借方（debit）不變。這是顯示約定，不是淨額。"
            if signed
            else None
        ),
        "groups": groups,
        "slots": slots,
        "drawn": drawn,
        "mode": mode,
        "reason": reason,
        "svg": svg,
        "signature_note": signature_note,
        "signature_fields": list(SIGNATURE_FIELDS),
        "group_count": len(groups),
        "bar_count": sum(1 for group in groups for slot in group["cells"].values()
                         if slot.get("included_in_chart")),
        "gap_count": gap_count,
        "limits": {
            "max_entities": MAX_ENTITIES,
            "max_items": MAX_ITEMS,
            "max_groups": MAX_GROUPS,
        },
        "caption": _caption(
            bucket["title"], selected_entities, selected_items, dates, signed
        ),
        "note": (
            "分組順序依機構名單及候選排序，不是按金額。"
            "同一機構、同一候選、同一結算日如有兩行同候選，標為缺口，不相加。"
            "借貸其中一邊剛好 0.00 時，按 0 對邊規則選非零一邊，該點標示需人手覆核。"
            "本頁沒有把借方減貸方，也沒有套用 unit_scale。"
            + (
                " 貸方 × -1 只改變圖上正負號，不是借貸相減，也不是合計。"
                if signed
                else ""
            )
        ),
        "summed": False,
        "ranked": False,
        "financially_validated": False,
        "presented_as_approved_scale": False,
    }


def _trace_slot(group: dict[str, Any], date: str) -> dict[str, Any]:
    slot = group["cells"][date]
    point = slot.get("point")
    return {
        "entity_id": group["entity_id"],
        "entity_key": group.get("entity_key"),
        "canonical_zh": group["canonical_zh"],
        "item_key": group["item_key"],
        "item_zh": group.get("item_zh"),
        "statement_date": date,
        "status": slot["status"],
        "reason": slot.get("reason"),
        "display": point.get("display") if point else None,
        "raw_display": (
            (point.get("raw_display") or point.get("display")) if point else None
        ),
        "signed_for_display": bool(point.get("signed_for_display")) if point else False,
        "minor": point.get("minor") if point else None,
        "mapping_status": point.get("mapping_status") if point else None,
        "row_type": point.get("row_type") if point else None,
        "side": point.get("side") if point else None,
        "zero_counterpart": bool(point.get("zero_counterpart")) if point else False,
        "statement_id": point.get("statement_id") if point else None,
        "statement_row_id": point.get("statement_row_id") if point else None,
        "included_in_chart": bool(slot.get("included_in_chart")),
        "signature_mismatch": bool(slot.get("signature_mismatch")),
    }


def _caption(
    bucket_title: str,
    entities: list[dict[str, Any]],
    items: list[str],
    dates: list[str],
    signed: bool = False,
) -> str:
    text = (
        f"{bucket_title} · {len(entities)} 間機構 × {len(items)} 個候選 × "
        f"{len(dates)} 個結算日。每條柱是一份已採納資產負債表內、"
        "恰好一個現行候選的一行觀察，不是已核定金融指標，也不是財務核實。"
        "沒有合計、沒有排名，亦沒有把借方減貸方；缺口不是 0。"
    )
    if signed:
        text += (
            " 貸方（credit）金額 × -1、借方不變，只為資產與負債同圖閱讀；"
            "原始正數、side 及 minor 仍保留在逐點追溯表。"
        )
    return text


def _signed_point(point: dict[str, Any]) -> dict[str, Any]:
    """Return a display copy with credit-side amounts negated.

    The raw minor and the original display stay on the point. This is a display
    convention, not an arithmetic net amount.
    """
    value = Decimal(str(point["display"]).replace(",", ""))
    signed_value = -value if point.get("side") == "credit" else value
    return {
        **point,
        "display": f"{signed_value:,.{point['amount_decimal_places']}f}",
        "raw_display": point["display"],
        "signed_for_display": point.get("side") == "credit",
    }


def _signed_signature_text(point: dict[str, Any]) -> str:
    return (
        f"{point.get('currency') or '幣別未記載'} · unit_scale "
        f"{point.get('unit_scale') if point.get('unit_scale') is not None else '未記載'} · "
        f"{point.get('amount_decimal_places')} 位小數 · "
        f"{point.get('reporting_scope') or 'scope 未記載'} · "
        f"row_type {point.get('row_type') or 'row_type 未記載'} · 借貸方向不比較"
    )


def _short(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _axis_amount(value: Decimal, decimal_places: int) -> str:
    magnitude = abs(value)
    if magnitude >= Decimal("1000000000"):
        return f"{value / Decimal('1000000000'):,.2f}B"
    if magnitude >= Decimal("1000000"):
        return f"{value / Decimal('1000000'):,.2f}M"
    if magnitude >= Decimal("1000"):
        return f"{value / Decimal('1000'):,.2f}K"
    return f"{value:,.{decimal_places}f}"


def _matrix_svg(
    groups: list[dict[str, Any]], dates: list[str], signed: bool = False
) -> str:
    """Grouped vertical bars: x = entity × item, one bar per selected date."""
    if not groups or not dates:
        return ""
    displayed = [
        (group, date, slot["point"])
        for group in groups
        for date in dates
        for slot in [group["cells"].get(date)]
        if slot and slot.get("included_in_chart") and slot.get("point")
    ]
    if not displayed:
        return ""
    decimal_places = displayed[0][2]["amount_decimal_places"]
    side = displayed[0][2]["side"]
    side_text = (
        "貸方 × -1（借方不變）" if signed else _side_label(side)
    )
    values = [
        Decimal(point["display"].replace(",", "")) for _, _, point in displayed
    ]
    low = min([Decimal(0), *values])
    high = max([Decimal(0), *values])
    if high == low:
        high = low + Decimal(1)
    span = high - low
    if low < 0:
        low -= span * Decimal("0.05")
    high += span * Decimal("0.12")
    if high == low:
        high = low + Decimal(1)

    group_w = 94
    left = 76
    right = 24
    top = 74
    plot_h = 244
    bottom = 88
    width = max(720, left + group_w * len(groups) + right)
    height = top + plot_h + bottom

    def y_of(value: Decimal) -> int:
        ratio = (value - low) / (high - low)
        return top + int(plot_h - ratio * plot_h)

    baseline_y = y_of(Decimal(0))
    parts = [
        f'<svg class="chart-svg matrix" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="多機構、多候選、多期候選觀察，缺口不是 0，沒有合計">',
        f'<text x="{left}" y="18" class="axis-title">'
        f'顯示金額（MOP · {decimal_places} 位小數）· {side_text} · 候選行</text>',
    ]
    legend_x = left
    for index, date in enumerate(dates):
        style = DATE_STYLES[index % len(DATE_STYLES)]
        parts.append(
            f'<rect x="{legend_x}" y="28" width="11" height="11" rx="2" '
            f'class="{style}"></rect>'
        )
        parts.append(
            f'<text x="{legend_x + 16}" y="38" class="axis-note">{_esc(date)}</text>'
        )
        legend_x += 16 + 10 * len(date) + 18
    parts.append(
        f'<rect x="{legend_x}" y="28" width="11" height="11" rx="2" '
        f'class="bar-gap"></rect>'
    )
    parts.append(
        f'<text x="{legend_x + 16}" y="38" class="axis-note">缺口</text>'
    )

    for step in range(5):
        value = low + (high - low) * Decimal(step) / Decimal(4)
        gy = y_of(value)
        parts.append(
            f'<line x1="{left}" y1="{gy}" x2="{width - right}" y2="{gy}" '
            f'class="grid-line"></line>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{gy + 4}" text-anchor="end" '
            f'class="axis-note">{_esc(_axis_amount(value, decimal_places))}</text>'
        )
    parts.append(
        f'<line x1="{left}" y1="{baseline_y}" x2="{width - right}" '
        f'y2="{baseline_y}" class="axis-line"></line>'
    )

    bar_span = (group_w - 20) / len(dates)
    bar_w = max(7, min(18, bar_span - 4))
    for index, group in enumerate(groups):
        group_x = left + index * group_w
        center = group_x + group_w / 2
        if index:
            parts.append(
                f'<line x1="{group_x}" y1="{top}" x2="{group_x}" '
                f'y2="{top + plot_h}" class="grid-line"></line>'
            )
        total_w = len(dates) * bar_w + (len(dates) - 1) * 4
        start = center - total_w / 2
        full_label = (
            f'{group["canonical_zh"]} · '
            f'{str(group["item_key"]).replace("user:", "")}'
        )
        parts.append(f'<g><title>{_esc(full_label)}</title>')
        for date_index, date in enumerate(dates):
            slot = group["cells"].get(date)
            bx = start + date_index * (bar_w + 4)
            if not slot or not slot.get("included_in_chart") or not slot.get("point"):
                reason = (slot or {}).get("reason") or "缺口"
                parts.append(
                    f'<rect x="{bx:.1f}" y="{baseline_y - 14}" width="{bar_w:.1f}" '
                    f'height="14" rx="2" class="bar-gap">'
                    f'<title>{_esc(date)} · 缺口 · {_esc(reason)}</title></rect>'
                )
                continue
            point = slot["point"]
            value = Decimal(point["display"].replace(",", ""))
            bar_y = y_of(value)
            rect_y = min(bar_y, baseline_y)
            rect_h = max(2, abs(baseline_y - bar_y))
            style = DATE_STYLES[date_index % len(DATE_STYLES)]
            parts.append(
                f'<rect x="{bx:.1f}" y="{rect_y}" width="{bar_w:.1f}" '
                f'height="{rect_h}" rx="2" class="{style}" '
                f'data-date="{_esc(date)}" data-minor="{point["minor"]}" '
                f'data-display="{_esc(point["display"])}" '
                f'data-row-type="{_esc(point.get("row_type"))}">'
                f'<title>{_esc(date)} · {_esc(point["display"])} · '
                f'{_esc(point.get("mapping_status"))} · row_type '
                f'{_esc(point.get("row_type") or "—")}'
                f'{" · 對邊 0.00" if point.get("zero_counterpart") else ""}'
                f'</title></rect>'
            )
        parts.append(
            f'<text x="{center:.1f}" y="{height - 50}" text-anchor="middle" '
            f'class="bar-label"><tspan x="{center:.1f}">'
            f'{_esc(_short(group["canonical_zh"], 7))}</tspan>'
            f'<tspan x="{center:.1f}" dy="14" class="axis-note">'
            f'{_esc(_short(str(group["item_key"]).replace("user:", ""), 12))}'
            f'</tspan></text>'
        )
        parts.append("</g>")
    parts.append(
        f'<text x="{left}" y="{height - 12}" class="axis-note">'
        f'分組依機構名單及候選排序，不是按金額。缺口不是 0；沒有合計，沒有排名。'
        f'</text>'
    )
    parts.append("</svg>")
    return "".join(parts)
