#!/usr/bin/env python3
"""Export and apply a standard line-item chart of accounts.

The standard master is deliberately separate from OCR. One row equals one
standard account; bilingual / combined OCR strings are aliases.

Examples::

    python tools/standard_line_items.py export \
        --db data/macau_balancete.sqlite \
        --csv labels/line_item_standard.csv

    # edit status to approved, then apply
    python tools/standard_line_items.py apply \
        --db data/macau_balancete.sqlite \
        --csv labels/line_item_standard.csv --by tony
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_database import _connect, _schema, normalize_text  # noqa: E402
from tools.name_registry import (  # noqa: E402
    _merge_concepts,
    _next_registry_version,
    _upsert_line_alias,
    apply_line_item_mappings,
)

MASTER_FIELDS = [
    "standard_key", "canonical_zh", "canonical_pt", "canonical_en",
    "level", "parent_key", "category", "status",
    "aliases_zh", "aliases_pt", "aliases_other",
    "merge_keys", "source", "note",
]


def _split(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def export_master(
    db_path: Path,
    out_path: Path,
    *,
    pt_zh_map_path: Path | None = None,
) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        _schema(conn)
        concepts = conn.execute(
            "SELECT concept_id, concept_key, canonical_zh, canonical_pt, "
            "canonical_en, parent_concept_id, level, category, status, "
            "source, note FROM line_item_concepts "
            "WHERE status <> 'merged' "
            "  AND (concept_key NOT LIKE 'auto:%' OR status = 'approved') "
            "ORDER BY concept_key"
        ).fetchall()
        aliases: dict[int, dict[str, list[str]]] = defaultdict(
            lambda: {"zh-Hant": [], "pt": [], "other": []}
        )
        for row in conn.execute(
            "SELECT concept_id, raw_name, language FROM line_item_aliases "
            "WHERE status = 'approved' ORDER BY alias_id"
        ):
            group = aliases[int(row["concept_id"])]
            language = normalize_text(row["language"])
            if language == "zh-Hant":
                group["zh-Hant"].append(normalize_text(row["raw_name"]))
            elif language == "pt":
                group["pt"].append(normalize_text(row["raw_name"]))
            else:
                group["other"].append(normalize_text(row["raw_name"]))
        by_id = {int(row["concept_id"]): row for row in concepts}
        # The bilingual pair candidates are evidence for a zh/pt master merge.
        pair_sources: dict[int, list[int]] = defaultdict(list)
        for row in conn.execute(
            "SELECT current_entity_id, suggested_entity_id "
            "FROM name_mapping_review "
            "WHERE entity_type = 'line_item' AND match_method = 'bilingual_pair'"
        ):
            if row["current_entity_id"] and row["suggested_entity_id"]:
                pair_sources[int(row["suggested_entity_id"])].append(
                    int(row["current_entity_id"])
                )
        key_to_id = {
            normalize_text(row["concept_key"]): int(row["concept_id"])
            for row in concepts
        }
        if pt_zh_map_path and pt_zh_map_path.exists():
            with pt_zh_map_path.open(newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    pt_key = normalize_text(row.get("pt_key"))
                    zh_key = normalize_text(row.get("zh_key"))
                    source = key_to_id.get(pt_key)
                    target = key_to_id.get(zh_key)
                    if source and target and source != target:
                        pair_sources[target].append(source)
        merged_sources = {
            source for sources in pair_sources.values() for source in sources
        }
        rows: list[dict[str, str]] = []
        for concept in concepts:
            concept_id = int(concept["concept_id"])
            if concept_id in merged_sources:
                continue
            source_ids = [concept_id] + pair_sources.get(concept_id, [])
            canonical_zh = normalize_text(concept["canonical_zh"])
            canonical_pt = normalize_text(concept["canonical_pt"])
            aliases_zh = list(aliases[concept_id]["zh-Hant"])
            aliases_pt = list(aliases[concept_id]["pt"])
            merge_keys: list[str] = []
            for source_id in source_ids[1:]:
                source = by_id.get(source_id)
                if source is None:
                    continue
                merge_keys.append(normalize_text(source["concept_key"]))
                canonical_pt = canonical_pt or normalize_text(source["canonical_pt"])
                aliases_pt.extend(aliases[source_id]["pt"])
            parent_key = ""
            if concept["parent_concept_id"]:
                parent = by_id.get(int(concept["parent_concept_id"]))
                parent_key = normalize_text(parent["concept_key"]) if parent else ""
            status = normalize_text(concept["status"])
            if source_ids[1:] or not canonical_zh:
                # A cross-language pair or a Chinese-less row needs review.
                status = "provisional"
            rows.append({
                "standard_key": normalize_text(concept["concept_key"]),
                "canonical_zh": canonical_zh,
                "canonical_pt": canonical_pt,
                "canonical_en": normalize_text(concept["canonical_en"]),
                "level": normalize_text(concept["level"]) or "parent",
                "parent_key": parent_key,
                "category": normalize_text(concept["category"]),
                "status": status or "provisional",
                "aliases_zh": "|".join(dict.fromkeys(aliases_zh)),
                "aliases_pt": "|".join(dict.fromkeys(aliases_pt)),
                "aliases_other": "|".join(dict.fromkeys(aliases[concept_id]["other"])),
                "merge_keys": "|".join(dict.fromkeys(merge_keys)),
                "source": normalize_text(concept["source"]),
                "note": normalize_text(concept["note"]),
            })
        rows.sort(key=lambda row: (row["level"] != "parent",
                                   row["canonical_zh"] or row["canonical_pt"]))
    finally:
        conn.close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=MASTER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "csv": str(out_path.resolve()),
        "rows": len(rows),
        "with_zh": sum(1 for row in rows if row["canonical_zh"]),
        "with_pt": sum(1 for row in rows if row["canonical_pt"]),
        "paired_merge_keys": sum(
            1 for row in rows if row["merge_keys"]
        ),
    }


def apply_master(
    db_path: Path,
    csv_path: Path,
    *,
    actor: str,
    include_provisional: bool = False,
) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        _schema(conn)
        with conn:
            version = _next_registry_version(conn)
            with csv_path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            applied = Counter()
            # First pass: create/update every standard concept so parent keys
            # and merge keys can be resolved.
            for row in rows:
                key = normalize_text(row.get("standard_key"))
                status = normalize_text(row.get("status")).lower() or "provisional"
                if not key:
                    continue
                if status != "approved" and not include_provisional:
                    applied["skipped_provisional"] += 1
                    continue
                canonical_zh = normalize_text(row.get("canonical_zh"))
                canonical_pt = normalize_text(row.get("canonical_pt"))
                canonical_en = normalize_text(row.get("canonical_en"))
                if not canonical_zh and not canonical_pt and not canonical_en:
                    applied["skipped_empty"] += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO line_item_concepts (
                        concept_key, canonical_zh, canonical_pt, canonical_en,
                        level, category, status, source, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'standard_master', ?)
                    ON CONFLICT(concept_key) DO UPDATE SET
                        canonical_zh = COALESCE(NULLIF(excluded.canonical_zh, ''),
                                                line_item_concepts.canonical_zh),
                        canonical_pt = COALESCE(NULLIF(excluded.canonical_pt, ''),
                                                line_item_concepts.canonical_pt),
                        canonical_en = COALESCE(NULLIF(excluded.canonical_en, ''),
                                                line_item_concepts.canonical_en),
                        level = excluded.level,
                        category = COALESCE(NULLIF(excluded.category, ''),
                                            line_item_concepts.category),
                        status = excluded.status,
                        note = COALESCE(NULLIF(excluded.note, ''),
                                       line_item_concepts.note)
                    """,
                    (key, canonical_zh or None, canonical_pt or None,
                     canonical_en or None, normalize_text(row.get("level"))
                     or "parent", normalize_text(row.get("category")) or None,
                     status, normalize_text(row.get("note")) or None),
                )
                applied["concepts"] += 1
            # Second pass: parents, merges and aliases.
            key_to_id = {
                normalize_text(row["concept_key"]): int(row["concept_id"])
                for row in conn.execute(
                    "SELECT concept_id, concept_key FROM line_item_concepts"
                )
            }
            for row in rows:
                key = normalize_text(row.get("standard_key"))
                status = normalize_text(row.get("status")).lower() or "provisional"
                if status != "approved" and not include_provisional:
                    continue
                target = key_to_id.get(key)
                if target is None:
                    continue
                parent_key = normalize_text(row.get("parent_key"))
                parent_id = key_to_id.get(parent_key) if parent_key else None
                if parent_id:
                    conn.execute(
                        "UPDATE line_item_concepts SET parent_concept_id = ? "
                        "WHERE concept_id = ?",
                        (parent_id, target),
                    )
                for merge_key in _split(row.get("merge_keys", "")):
                    source = key_to_id.get(merge_key)
                    if source and source != target:
                        _merge_concepts(
                            conn, source, target, actor,
                            normalize_text(row.get("canonical_zh"))
                            or normalize_text(row.get("canonical_pt")),
                        )
                        applied["merged_concepts"] += 1
                for language, field in (
                    ("zh-Hant", "aliases_zh"), ("pt", "aliases_pt"),
                    ("", "aliases_other"),
                ):
                    for alias in _split(row.get(field, "")):
                        _upsert_line_alias(
                            conn, target, alias, language or "other",
                            alias_type="official", status="approved",
                            source="standard_master",
                        )
                        applied["aliases"] += 1
            mapping = apply_line_item_mappings(conn, version)
            if applied.get("concepts", 0) > 0:
                conn.execute(
                    "INSERT INTO schema_meta (key, value) "
                    "VALUES ('standard_master_loaded', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                )
        pending = conn.execute(
            "SELECT COUNT(*) FROM name_mapping_review WHERE status = 'pending'"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "database": str(db_path.resolve()),
        "csv": str(csv_path.resolve()),
        "registry_version": version,
        "applied": dict(applied),
        "mapping": dict(mapping),
        "pending_reviews": int(pending),
        "generated_at": _now(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export/apply the standard line-item chart of accounts"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="export the current standard draft")
    export.add_argument("--db", type=Path,
                        default=Path("data/macau_balancete.sqlite"))
    export.add_argument("--csv", type=Path,
                        default=Path("labels/line_item_standard.csv"))
    export.add_argument("--pt-zh-map", type=Path,
                        default=Path("labels/line_item_pt_zh_map.csv"))

    apply = sub.add_parser("apply", help="apply an edited standard master")
    apply.add_argument("--db", type=Path,
                       default=Path("data/macau_balancete.sqlite"))
    apply.add_argument("--csv", type=Path,
                       default=Path("labels/line_item_standard.csv"))
    apply.add_argument("--by", required=True)
    apply.add_argument("--include-provisional", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_master(
                args.db, args.csv, pt_zh_map_path=args.pt_zh_map
            )
        else:
            result = apply_master(
                args.db, args.csv, actor=args.by,
                include_provisional=args.include_provisional,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
