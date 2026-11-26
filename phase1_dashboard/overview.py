"""Overview helpers that are not financial indicators.

The functions here only rearrange coverage/mapping facts that the V3 read-only
queries already returned. They do not add amounts, rank banks or calculate a
ratio.
"""
from __future__ import annotations

from typing import Any

from phase1_dashboard.scope import COMPARISON_DATES, empty_mapping_counts


def build_bank_directory(coverage: dict[str, Any]) -> dict[str, Any]:
    """One row per adopted-perimeter entity, with data status per quarter."""
    buckets: list[dict[str, Any]] = []
    total_mapping = empty_mapping_counts()
    total_rows = 0
    total_statements = 0
    for bucket in coverage["buckets"]:
        rows: list[dict[str, Any]] = []
        for entity in bucket["entities"]:
            usable_dates = [
                date for date in COMPARISON_DATES
                if entity["periods"][date]["kind"] == "usable"
            ]
            gap_dates = [
                date for date in COMPARISON_DATES
                if entity["periods"][date]["kind"] == "gap"
            ]
            not_adopted: list[dict[str, Any]] = []
            mapping = empty_mapping_counts()
            statement_count = 0
            for date in COMPARISON_DATES:
                cell = entity["periods"][date]
                if cell["kind"] != "usable":
                    continue
                for statement in cell.get("statements") or []:
                    statement_count += 1
                    for status, count in statement.get("mapping_counts", {}).items():
                        if status in mapping:
                            mapping[status] += count
                for item in cell.get("not_adopted") or []:
                    not_adopted.append({
                        "statement_date": date,
                        "statement_type": item.get("statement_type"),
                        "statement_status": item.get("statement_status"),
                        "statement_id": item.get("statement_id"),
                        "reason": cell.get("not_adopted_reason"),
                    })
            row_total = sum(mapping.values())
            rows.append({
                "canonical_zh": entity["canonical_zh"],
                "entity_id": entity.get("entity_id"),
                "entity_type": entity.get("entity_type"),
                "registry_status": entity.get("registry_status"),
                "merged": entity.get("merged", False),
                "merged_into_entity_id": entity.get("merged_into_entity_id"),
                "lifecycle_note": entity.get("lifecycle_note"),
                "resolved": entity.get("resolved", True),
                "duplicate_name": entity.get("duplicate_name", False),
                "usable_dates": usable_dates,
                "gap_dates": gap_dates,
                "not_adopted": not_adopted,
                "latest_date": usable_dates[-1] if usable_dates else None,
                "statement_count": statement_count,
                "mapping": mapping,
                "row_total": row_total,
                "approved_share": (
                    100.0 * mapping["approved"] / row_total if row_total else None
                ),
            })
            total_rows += row_total
            total_statements += statement_count
            for status, count in mapping.items():
                total_mapping[status] += count
        buckets.append({
            "id": bucket["id"],
            "title": bucket["title"],
            "rule": bucket["rule"],
            "entities": rows,
        })
    return {
        "buckets": buckets,
        "totals": {
            "entity_count": sum(len(bucket["entities"]) for bucket in buckets),
            "resolved_count": sum(
                1 for bucket in buckets for entity in bucket["entities"]
                if entity["entity_id"]
            ),
            "statement_count": total_statements,
            "row_total": total_rows,
            "mapping": total_mapping,
            "approved_share": (
                100.0 * total_mapping["approved"] / total_rows if total_rows else None
            ),
        },
        "window": list(COMPARISON_DATES),
    }
