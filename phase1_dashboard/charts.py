"""Chart view for the phase-1 landing page.

A bar or trend point is drawn only when one adopted balance-sheet row has
exactly one distinct, currently valid catalogue candidate, MOP, and
unit_scale 1, and exactly one amount side. Anything else stays a gap.
Bars follow the adopted name order. Nothing is sorted, summed, or rescaled.
"""
from __future__ import annotations

import html
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


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _one_sided_amount(row: dict[str, Any]) -> tuple[str, int] | None:
    debit_state = row.get("debit_state")
    credit_state = row.get("credit_state")
    debit_minor = (row.get("debit") or {}).get("minor")
    credit_minor = (row.get("credit") or {}).get("minor")
    debit_ok = debit_state == "amount" and debit_minor is not None
    credit_ok = credit_state == "amount" and credit_minor is not None
    if debit_ok and credit_ok:
        return None
    if debit_ok:
        return "debit", int(debit_minor)
    if credit_ok:
        return "credit", int(credit_minor)
    return None


def _slot(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    slot = {
        "status": status,
        "reason": reason,
        "point": None,
        "included_in_chart": False,
    }
    slot.update(extra)
    return slot


def chart_slot(
    cell: dict[str, Any],
    rows_by_statement: dict[str, list[dict[str, Any]]],
    item_key: str,
) -> dict[str, Any]:
    """Decide whether one entity-date may become one chart point."""
    if cell.get("kind") == "gap":
        return _slot("gap", cell.get("reason") or "沒有報表")
    balance_rows = _balance_rows(cell, rows_by_statement)
    has_balance = any(
        statement.get("statement_type") == BALANCE_SHEET
        for statement in cell.get("statements") or []
    )
    if not has_balance:
        return _slot("no_balance_sheet", "這期沒有 adopted 的 balance_sheet。")
    single = [row for row in balance_rows if candidate_keys(row) == [item_key]]
    if len(single) > 1:
        return _slot(
            "multiple_rows",
            f"同一報表有 {len(single)} 行此候選，未選其中一行。",
        )
    if len(single) == 1:
        row = single[0]
        if not row.get("catalogue_current"):
            return _slot("inactive_candidate", "候選科目不是現行有效項，沒有畫入圖。")
        if not project_metadata_compliant(row.get("currency"), row.get("unit_scale")):
            return _slot(
                "metadata",
                "currency / unit_scale 不是 MOP、1。沒有換算，沒有畫入圖。",
            )
        sided = _one_sided_amount(row)
        if sided is None:
            return _slot("amount_side", "借貸兩欄不能確定單一金額，沒有選邊。")
        side, minor = sided
        return {
            "status": "chart",
            "reason": None,
            "included_in_chart": True,
            "point": {
                "side": side,
                "minor": minor,
                "item_key": item_key,
                "mapping_status": row.get("mapping_status"),
                "label": row.get("label"),
                "row_type": row.get("row_type"),
                "statement_id": row.get("statement_id"),
                "decimal_places": row.get("amount_decimal_places"),
                "financially_validated": False,
                "presented_as_approved_scale": False,
            },
        }
    if any(item_key in candidate_keys(row) and len(candidate_keys(row)) != 1 for row in balance_rows):
        return _slot("ambiguous", "同一行有多於一個不同候選，沒有排入圖。")
    return _slot("absent", "此期沒有此候選。這不是 0。")


def _bucket_balance_rows(
    entities: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity in entities:
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
        cell = entity["periods"][statement_date]
        slot = chart_slot(cell, rows_by_statement, item_key)
        if slot["included_in_chart"]:
            count += 1
    return count


def _choose_item_and_date(
    entities: list[dict[str, Any]],
    rows_by_statement: dict[str, list[dict[str, Any]]],
    catalogue: dict[str, str | None],
) -> tuple[str | None, str]:
    best_key = None
    best_count = -1
    for key in sorted(catalogue):
        count = sum(
            _chartable_count(entities, rows_by_statement, key, date)
            for date in COMPARISON_DATES
        )
        if count > best_count:
            best_key = key
            best_count = count
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


def _choose_entity(entities: list[dict[str, Any]], trend_ready: dict[str, bool]) -> dict[str, Any] | None:
    resolved = [entity for entity in entities if entity.get("entity_id")]
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
) -> dict[str, Any]:
    if bucket_id is None:
        bucket_id = "legal"
    bucket = bucket_by_id(bucket_id)
    if bucket is None:
        raise ScopeError("圖表只可以選擇一個分組：本地法人、關聯報表，或外地銀行分行。")
    if statement_date is not None and statement_date not in COMPARISON_DATES:
        raise ScopeError("比較日必須是 2026-03-31、2026-06-30 或 2026-09-30。")
    coverage = build_coverage(
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
            slot = chart_slot(entity["periods"][statement_date], rows_by_statement, item_key)
            series.append({
                "canonical_zh": entity["canonical_zh"],
                "entity_id": entity.get("entity_id"),
                **slot,
            })
    cross = _cross_bank(series)
    trend_by_entity: dict[str, list[dict[str, Any]]] = {}
    trend_ready: dict[str, bool] = {}
    if item_key:
        for entity in bucket_entities:
            if not entity.get("entity_id"):
                continue
            quarters = [
                {
                    "statement_date": date,
                    **chart_slot(entity["periods"][date], rows_by_statement, item_key),
                }
                for date in COMPARISON_DATES
            ]
            trend_by_entity[entity["entity_id"]] = quarters
            trend_ready[entity["entity_id"]] = any(item["included_in_chart"] for item in quarters)
    if entity_id and entity_id not in {entity.get("entity_id") for entity in bucket_entities}:
        raise ScopeError("所選機構不在這個分組。")
    selected = None
    if entity_id:
        selected = next(entity for entity in bucket_entities if entity.get("entity_id") == entity_id)
    else:
        selected = _choose_entity(bucket_entities, trend_ready)
    trend = _trend(trend_by_entity.get(selected["entity_id"], []) if selected else [])
    item_zh = catalogue.get(item_key) if item_key else None
    warnings = draft_warnings_for([item_key] if item_key else [])
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
            for key in sorted(catalogue)
        ],
        "entities": [
            {
                "entity_id": entity.get("entity_id"),
                "canonical_zh": entity["canonical_zh"],
            }
            for entity in bucket_entities
            if entity.get("entity_id")
        ],
        "selected_entity_id": selected.get("entity_id") if selected else None,
        "selected_entity_name": selected["canonical_zh"] if selected else None,
        "cross_bank": cross,
        "trend": trend,
        "warnings": warnings,
        "financially_validated": False,
        "presented_as_approved_scale": False,
        "is_total_assets": False,
        "summed": False,
        "ranked": False,
        "caption": _caption(item_key, item_zh, statement_date, bucket["title"]),
    }


def _caption(item_key: str | None, item_zh: str | None, statement_date: str, bucket_title: str) -> str:
    label = item_zh or item_key or "未選候選"
    text = (
        f"{label} · {statement_date} · {bucket_title}。"
        "圖上是試算表候選行的儲存 minor，不是已核定指標，也不是財務核實。"
    )
    if item_key == "user:總計":
        text += " user:總計 不是總資產。"
    return text


def _cross_bank(series: list[dict[str, Any]]) -> dict[str, Any]:
    bars = [slot for slot in series if slot["included_in_chart"] and slot["point"]]
    sides = {slot["point"]["side"] for slot in bars}
    drawn = len(bars) >= 2 and len(sides) == 1
    reason = None
    if not drawn:
        if len(bars) == 0:
            reason = "這一組沒有兩間以上符合並排條件的機構。缺口留在下面，不畫成 0。"
        elif len(bars) == 1:
            reason = "只有一間符合並排條件，不畫成跨行比較。"
        else:
            reason = "可畫的行借貸方向不一致，沒有放在同一條軸上。"
    svg = _bar_svg(series) if drawn else None
    return {
        "drawn": drawn,
        "reason": reason,
        "side": next(iter(sides)) if drawn else None,
        "slots": [
            {
                "canonical_zh": slot["canonical_zh"],
                "entity_id": slot.get("entity_id"),
                "status": slot["status"],
                "reason": slot["reason"],
                "minor": slot["point"]["minor"] if slot["point"] else None,
                "mapping_status": slot["point"]["mapping_status"] if slot["point"] else None,
                "included_in_chart": slot["included_in_chart"] and drawn,
            }
            for slot in series
        ],
        "svg": svg,
        "summed": False,
        "ranked": False,
        "order": [slot["canonical_zh"] for slot in series],
    }


def _trend(quarters: list[dict[str, Any]]) -> dict[str, Any]:
    points = [slot for slot in quarters if slot.get("included_in_chart") and slot.get("point")]
    sides = {slot["point"]["side"] for slot in points}
    drawn = len(points) >= 1 and len(sides) == 1
    reason = None
    if not quarters:
        reason = "未選機構。"
        drawn = False
    elif not drawn:
        if not points:
            reason = "這間機構三季都沒有可畫的單一候選。缺季不是 0。"
        else:
            reason = "三季的可畫金額不在同一借貸方向，沒有連成一條趨勢。"
    return {
        "drawn": drawn,
        "reason": reason,
        "side": next(iter(sides)) if drawn else None,
        "slots": [
            {
                "statement_date": slot["statement_date"],
                "status": slot["status"],
                "reason": slot["reason"],
                "minor": slot["point"]["minor"] if slot.get("point") else None,
                "mapping_status": slot["point"]["mapping_status"] if slot.get("point") else None,
                "included_in_chart": bool(slot.get("included_in_chart") and drawn and slot.get("point") and slot["point"]["side"] == next(iter(sides), None)),
            }
            for slot in quarters
        ],
        "svg": _trend_svg(quarters) if drawn else None,
        "summed": False,
        "ranked": False,
    }


def _bar_svg(series: list[dict[str, Any]]) -> str:
    bars = [slot for slot in series if slot["included_in_chart"]]
    max_abs = max(abs(slot["point"]["minor"]) for slot in bars) or 1
    side = bars[0]["point"]["side"]
    side_label = "借方" if side == "debit" else "貸方"
    row_h = 46
    label_w = 308
    plot_w = 430
    pad_top = 28
    width = label_w + plot_w + 108
    height = pad_top + len(series) * row_h + 36
    parts = [
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="跨行橫條，順序依已採納名單，不是排名">',
        f'<text x="{label_w}" y="18" class="axis-title">儲存 minor · MOP · unit_scale 1 · {side_label}</text>',
    ]
    zero_x = label_w
    for index, slot in enumerate(series):
        y = pad_top + index * row_h
        parts.append(
            f'<text x="8" y="{y + 28}" class="bar-label">{_esc(slot["canonical_zh"])}</text>'
        )
        if not slot["included_in_chart"]:
            parts.append(
                f'<text x="{label_w}" y="{y + 28}" class="gap-label">缺口 · {_esc(slot["reason"])}</text>'
            )
            continue
        minor = slot["point"]["minor"]
        span = int(abs(minor) / max_abs * (plot_w - 8))
        span = max(span, 2)
        if minor < 0:
            x = zero_x - span
        else:
            x = zero_x
        parts.append(
            f'<rect x="{x}" y="{y + 10}" width="{span}" height="22" class="bar" '
            f'data-name="{_esc(slot["canonical_zh"])}" data-minor="{minor}" '
            f'data-order="{index}"></rect>'
        )
        parts.append(
            f'<text x="{x + span + 8}" y="{y + 26}" class="bar-value">'
            f'{minor:,} · {_esc(slot["point"]["mapping_status"])}</text>'
        )
    parts.append(
        f'<text x="{label_w}" y="{height - 12}" class="axis-note">由上至下是名單順序，不是按金額排序。</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _trend_svg(quarters: list[dict[str, Any]]) -> str:
    points = [slot for slot in quarters if slot.get("included_in_chart")]
    max_abs = max(abs(slot["point"]["minor"]) for slot in points) or 1
    side = points[0]["point"]["side"]
    side_label = "借方" if side == "debit" else "貸方"
    width = 640
    height = 280
    left = 56
    right = 24
    top = 28
    bottom = 48
    plot_w = width - left - right
    plot_h = height - top - bottom
    xs = [left + int(index * plot_w / 2) for index in range(3)]

    def y_of(minor: int) -> int:
        return top + int(plot_h - (abs(minor) / max_abs) * (plot_h - 16))

    parts = [
        f'<svg class="chart-svg trend" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="同一機構三季，缺季不連線、不是 0">',
        f'<text x="{left}" y="18" class="axis-title">儲存 minor · MOP · unit_scale 1 · {side_label}</text>',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{width - right}" y2="{top + plot_h}" class="axis-line"></line>',
    ]
    previous = None
    for index, slot in enumerate(quarters):
        x = xs[index]
        parts.append(
            f'<text x="{x}" y="{height - 16}" text-anchor="middle" class="bar-label">{_esc(slot["statement_date"])}</text>'
        )
        if not slot.get("included_in_chart"):
            parts.append(
                f'<text x="{x}" y="{top + plot_h - 12}" text-anchor="middle" class="gap-label">缺口</text>'
            )
            previous = None
            continue
        minor = slot["point"]["minor"]
        y = y_of(minor)
        if previous is not None:
            parts.append(
                f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{x}" y2="{y}" class="trend-line"></line>'
            )
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="5" class="bar" data-date="{_esc(slot["statement_date"])}" '
            f'data-minor="{minor}"></circle>'
        )
        parts.append(
            f'<text x="{x}" y="{y - 10}" text-anchor="middle" class="bar-value">{minor:,}</text>'
        )
        previous = (x, y)
    parts.append("</svg>")
    return "".join(parts)
