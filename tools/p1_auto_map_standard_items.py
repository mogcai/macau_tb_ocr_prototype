#!/usr/bin/env python3
"""Seed the v3 Standard Item Catalogue from the user's 89-item list and
auto-map statement rows as PROPOSED mappings.

This is a first-pass proposal generator.  It never creates approved mappings.

Source catalogue:
    v5 standard_labels (89 user-curated items)
    v5 standard_label_aliases

Mapping targets:
    v3 statement_rows, using effective labels (field_verifications first).

Outputs:
    - v3 standard_items / standard_item_aliases seed
    - v3 statement_row_mappings with mapping_status='proposed'
    - docs/audit/statement_row_mapping_review.csv
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sqlite3
import sys
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_V5 = ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite"
DEFAULT_V3 = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_OUT = ROOT / "docs" / "audit" / "statement_row_mapping_review.csv"
NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-5a7b-8c9d-0e1f2a3b4c55")
VERSION = "p1-auto-1"

CJK_RE = re.compile(r"[\u3400-\u9fff]")
CHILD_RE = re.compile(r"^[\-–—•·]\s*")
ZH_VARIANTS = str.maketrans({
    "賬": "帳", "帐": "帳", "劵": "券", "産": "產", "価": "價",
    "歴": "歷", "査": "查", "扺": "抵", "资": "資", "総": "總", "収": "收",
})


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def language_of(value: str, fallback: str = "") -> str:
    if CJK_RE.search(value):
        return "zh-Hant"
    if re.search(r"[A-Za-z]", value):
        return "pt"
    return fallback


def normalize(value: str, language: str = "") -> str:
    value = CHILD_RE.sub("", text(value))
    if language == "zh-Hant" or (not language and CJK_RE.search(value)):
        value = value.translate(ZH_VARIANTS)
        value = re.sub(r"[()（）〔〕【】\[\]、,，:：;；]", "", value)
        return re.sub(r"\s+", "", value)
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^0-9A-Za-z\s]", " ", ascii_text)
    return re.sub(r"\s+", "", ascii_text).casefold()


def connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def connect_rw(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def load_seed(v5: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    standards = [dict(row) for row in v5.execute("SELECT * FROM standard_labels ORDER BY standard_id")]
    aliases = [dict(row) for row in v5.execute("SELECT * FROM standard_label_aliases ORDER BY alias_id")]
    return standards, aliases


def seed_catalogue(v3: sqlite3.Connection, standards: list[dict[str, Any]],
                   aliases: list[dict[str, Any]]) -> tuple[dict[int, str], int]:
    """Seed legacy catalogue rows, leaving existing rows untouched.

    The legacy V5 list is a bootstrap source only.  Once a ``standard_item``
    exists in v3 it is curated in the Workbench (names, language status and
    lifecycle), so re-running this tool on the next intake build must never
    reset ``item_status`` or a language status back to ``draft`` (that is what
    silently reverted approved items).  Existing names, statuses and notes win;
    only missing values are filled from the seed.
    """
    id_by_legacy: dict[int, str] = {}
    preserved = 0
    for row in standards:
        item_id = stable_id("standard_item", row["standard_key"])
        id_by_legacy[int(row["standard_id"])] = item_id
        if v3.execute(
            "SELECT 1 FROM standard_items WHERE standard_item_id=?", (item_id,)
        ).fetchone():
            preserved += 1
        zh = text(row.get("canonical_zh"))
        pt = text(row.get("canonical_pt"))
        en = text(row.get("canonical_en"))
        source_approved = text(row.get("status")) == "approved"
        lang_status = "approved" if source_approved else "draft"
        v3.execute(
            """
            INSERT INTO standard_items(
                standard_item_id, item_key, canonical_zh, canonical_zh_status,
                canonical_pt, canonical_pt_status, canonical_en,
                canonical_en_status, parent_standard_item_id, category,
                item_status, created_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            ON CONFLICT(standard_item_id) DO UPDATE SET
                canonical_zh=CASE
                    WHEN standard_items.canonical_zh IS NULL
                    THEN excluded.canonical_zh
                    ELSE standard_items.canonical_zh END,
                canonical_zh_status=CASE
                    WHEN standard_items.canonical_zh IS NULL
                    THEN excluded.canonical_zh_status
                    ELSE standard_items.canonical_zh_status END,
                canonical_pt=CASE
                    WHEN standard_items.canonical_pt IS NULL
                    THEN excluded.canonical_pt
                    ELSE standard_items.canonical_pt END,
                canonical_pt_status=CASE
                    WHEN standard_items.canonical_pt IS NULL
                    THEN excluded.canonical_pt_status
                    ELSE standard_items.canonical_pt_status END,
                canonical_en=CASE
                    WHEN standard_items.canonical_en IS NULL
                    THEN excluded.canonical_en
                    ELSE standard_items.canonical_en END,
                canonical_en_status=CASE
                    WHEN standard_items.canonical_en IS NULL
                    THEN excluded.canonical_en_status
                    ELSE standard_items.canonical_en_status END,
                notes=COALESCE(standard_items.notes, excluded.notes)
            """,
            (
                item_id, row["standard_key"], zh or None,
                lang_status if zh else "missing",
                pt or None, lang_status if pt else "missing",
                en or None, lang_status if en else "missing",
                "approved" if source_approved else "draft",
                now(),
                f"legacy_standard_id={row['standard_id']}; source=standard_label_by_user",
            ),
        )
    # Parent links after all rows exist.
    for row in standards:
        if row.get("parent_standard_id") is None:
            continue
        parent_id = id_by_legacy.get(int(row["parent_standard_id"]))
        if parent_id:
            v3.execute(
                "UPDATE standard_items SET parent_standard_item_id=? "
                "WHERE standard_item_id=? AND parent_standard_item_id IS NULL",
                (parent_id, id_by_legacy[int(row["standard_id"])]),
            )

    # Aliases: approved aliases are usable for matching; provisional aliases are
    # still saved as proposed evidence.
    for alias in aliases:
        item_id = id_by_legacy.get(int(alias["standard_id"]))
        if not item_id:
            continue
        raw = text(alias.get("raw_name"))
        language = text(alias.get("language")) or language_of(raw)
        normalized = text(alias.get("normalized_name")) or normalize(raw, language)
        status = "approved" if text(alias.get("status")) == "approved" else "proposed"
        alias_id = stable_id("standard_item_alias", item_id, normalized, language)
        v3.execute(
            """
            INSERT OR IGNORE INTO standard_item_aliases(
                standard_item_alias_id, standard_item_id, scope_entity_id,
                raw_name, normalized_name, language, alias_type,
                source_registry, source_legacy_id, alias_status, notes
            ) VALUES (?, ?, NULL, ?, ?, ?, 'bank_label', 'standard_labels', ?, ?, ?)
            """,
            (
                alias_id, item_id, raw, normalized, language,
                str(alias["alias_id"]), status, text(alias.get("note")),
            ),
        )
    return id_by_legacy, preserved


def index_catalogue(v3: sqlite3.Connection) -> tuple[
    list[dict[str, Any]], dict[tuple[str, str], list[int]], dict[tuple[str, str], list[int]],
    dict[str, list[int]]
]:
    items = [dict(row) for row in v3.execute(
        "SELECT standard_item_id, item_key, canonical_zh, canonical_pt, "
        "parent_standard_item_id FROM standard_items ORDER BY standard_item_id"
    )]
    by_id = {item["standard_item_id"]: index for index, item in enumerate(items)}
    canonical: dict[tuple[str, str], list[int]] = defaultdict(list)
    aliases: dict[tuple[str, str], list[int]] = defaultdict(list)
    children: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        for value, language in ((item["canonical_zh"], "zh-Hant"), (item["canonical_pt"], "pt")):
            key = normalize(value or "", language)
            if key:
                canonical[(language, key)].append(index)
        if item["parent_standard_item_id"]:
            children[item["parent_standard_item_id"]].append(index)
    for row in v3.execute(
        "SELECT standard_item_id, normalized_name, language FROM standard_item_aliases "
        "WHERE alias_status='approved'"
    ):
        index = by_id.get(row["standard_item_id"])
        if index is not None and row["normalized_name"]:
            aliases[(row["language"], row["normalized_name"])].append(index)
    return items, canonical, aliases, children


def effective_rows(v3: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = v3.execute(
        """
        SELECT sr.statement_id, sr.statement_row_id, sr.statement_order, sr.row_type,
               sr.parent_context_status, sr.label_language,
               er.label_raw, er.label_normalized AS base_label,
               er.row_payload_json,
               dp.pdf_page,
               s.statement_date,
               e.canonical_zh AS entity_name,
               gi.issue_key,
               COALESCE((SELECT fv.verified_value_text FROM field_verifications fv
                 WHERE fv.statement_row_id=sr.statement_row_id
                   AND fv.field_name='label_normalized'
                   AND fv.verification_status='verified'), sr.label_normalized) AS effective_label
        FROM statement_rows sr
        JOIN extracted_rows er ON er.extracted_row_id=sr.extracted_row_id
        JOIN document_pages dp ON dp.document_page_id=er.document_page_id
        JOIN statements s ON s.statement_id=sr.statement_id
        LEFT JOIN reporting_entities e ON e.entity_id=s.reporting_entity_id
        LEFT JOIN source_documents sd ON sd.source_document_id=s.source_document_id
        LEFT JOIN gazette_issues gi ON gi.gazette_issue_id=sd.gazette_issue_id
        ORDER BY sr.statement_id, sr.statement_order, sr.statement_row_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def candidate_texts(row: dict[str, Any]) -> list[tuple[str, str]]:
    label = text(row["effective_label"]) or text(row["base_label"])
    result: list[tuple[str, str]] = []

    def add(value: str, fallback: str = "") -> None:
        value = text(value)
        if not value:
            return
        language = language_of(value, fallback)
        result.append((value, language))
        # Combined OCR labels such as ``PATACAS澳門元`` or
        # ``DEPOSITOSAORDEM活期存款`` are common in the source.  Add each
        # script segment as an independent matching candidate.
        if CJK_RE.search(value) and re.search(r"[A-Za-z]", value):
            for part in re.findall(r"[\u3400-\u9fff]+|[A-Za-z][A-Za-z\s]*", value):
                part = part.strip()
                if len(part) >= 2:
                    result.append((part, language_of(part)))

    add(label, row["label_language"] or "")
    try:
        payload = json.loads(row["row_payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    add(text(payload.get("label_standard")))
    return list(dict.fromkeys(result))


def parent_text(row: dict[str, Any]) -> str:
    try:
        payload = json.loads(row["row_payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    path = text(payload.get("label_path"))
    if ">" in path:
        return text(path.split(">")[-2])
    return text(payload.get("parent_label_raw"))


def find_items(
    row: dict[str, Any],
    items: list[dict[str, Any]],
    canonical: dict[tuple[str, str], list[int]],
    aliases: dict[tuple[str, str], list[int]],
    children: dict[str, list[int]],
) -> tuple[list[int], str, float, str]:
    texts = candidate_texts(row)
    candidates: dict[int, tuple[str, float]] = {}
    for value, language in texts:
        key = normalize(value, language)
        if not key:
            continue
        for index in canonical.get((language, key), []):
            candidates[index] = ("bilingual_exact", 1.0)
        for index in aliases.get((language, key), []):
            candidates.setdefault(index, ("alias_exact", 0.92))
    method = "bilingual_exact"
    score = 1.0

    parent = parent_text(row)
    if parent:
        parent_language = language_of(parent)
        parent_key = normalize(parent, parent_language)
        parent_candidates = set(canonical.get((parent_language, parent_key), []))
        parent_candidates.update(aliases.get((parent_language, parent_key), []))
        if len(parent_candidates) == 1:
            parent_index = next(iter(parent_candidates))
            parent_id = items[parent_index]["standard_item_id"]
            allowed = set(children.get(parent_id, []))
            filtered = {index: info for index, info in candidates.items() if index in allowed}
            if filtered:
                candidates = filtered
                method, score = "child_path_exact", 0.96

    if not candidates:
        # Conservative fuzzy fallback against canonical names in the same language.
        best: tuple[float, int, str] | None = None
        for value, language in texts:
            key = normalize(value, language)
            if not key:
                continue
            matches: list[tuple[float, int]] = []
            for (lang, candidate_key), indexes in canonical.items():
                if lang != language:
                    continue
                ratio = difflib.SequenceMatcher(None, key, candidate_key).ratio()
                if ratio >= 0.86:
                    for index in indexes:
                        matches.append((ratio, index))
            matches.sort(reverse=True)
            if matches and (len(matches) == 1 or matches[0][0] - matches[1][0] >= 0.03):
                best = (matches[0][0], matches[0][1], "fuzzy")
        if best is not None:
            candidates = {best[1]: ("fuzzy", best[0])}
            method, score = "fuzzy", best[0]

    if not candidates:
        return [], "unmatched", 0.0, "no_candidate"
    ordered = sorted(candidates.items(), key=lambda pair: (-pair[1][1], items[pair[0]]["item_key"]))
    if len(ordered) == 1:
        index, (method, score) = ordered[0]
        return [index], method, score, "unique"
    return [index for index, _ in ordered], ordered[0][1][0], ordered[0][1][1], "conflict"


def write_mappings(
    v3: sqlite3.Connection,
    rows: list[dict[str, Any]],
    items: list[dict[str, Any]],
    canonical: dict[tuple[str, str], list[int]],
    aliases: dict[tuple[str, str], list[int]],
    children: dict[str, list[int]],
    *,
    apply: bool,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    result: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row in rows:
        indexes, method, score, status = find_items(row, items, canonical, aliases, children)
        counts[status] += 1
        if not indexes:
            result.append({
                **row,
                "mapping_status": "unmatched",
                "candidate_indexes": [],
                "method": method,
                "score": score,
                "conflict_group_id": "",
            })
            continue
        conflict_group = (
            stable_id("conflict_group", row["statement_row_id"], VERSION)
            if len(indexes) > 1 else None
        )
        for index in indexes:
            item = items[index]
            mapping_id = stable_id(
                "statement_row_mapping", row["statement_row_id"],
                item["standard_item_id"], VERSION,
            )
            if apply:
                v3.execute(
                    """
                    INSERT INTO statement_row_mappings(
                        mapping_id, statement_row_id, standard_item_id,
                        conflict_group_id, mapping_status, match_method, match_score,
                        mapping_version, evidence_source, context_json, created_at
                    ) VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, 'standard_labels_seed', ?, ?)
                    ON CONFLICT(mapping_id) DO NOTHING
                    """,
                    (
                        mapping_id, row["statement_row_id"], item["standard_item_id"],
                        conflict_group, method, score, VERSION,
                        json.dumps({
                            "label": text(row["effective_label"]),
                            "language": row["label_language"],
                            "parent": parent_text(row),
                            "candidate_count": len(indexes),
                        }, ensure_ascii=False, sort_keys=True),
                        now(),
                    ),
                )
        result.append({
            **row,
            "mapping_status": status,
            "candidate_indexes": indexes,
            "method": method,
            "score": score,
            "conflict_group_id": conflict_group or "",
        })
    return result, counts


def write_review(path: Path, rows: list[dict[str, Any]], items: list[dict[str, Any]]) -> None:
    fields = [
        "statement_id", "issue_key", "entity_name", "statement_date",
        "statement_row_id", "pdf_page", "statement_order", "row_type",
        "label_raw", "effective_label", "label_language",
        "parent_context_status", "parent_label",
        "mapping_status", "match_method", "match_score",
        "candidate_count", "conflict_group_id", "candidate_keys",
        "candidate_names", "reviewer_decision", "reviewer_note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            candidates = [items[index] for index in row["candidate_indexes"]]
            writer.writerow({
                "statement_id": row["statement_id"],
                "issue_key": row["issue_key"] or "",
                "entity_name": row["entity_name"] or "",
                "statement_date": row["statement_date"] or "",
                "statement_row_id": row["statement_row_id"],
                "pdf_page": row["pdf_page"],
                "statement_order": row["statement_order"],
                "row_type": row["row_type"],
                "label_raw": row["label_raw"] or "",
                "effective_label": row["effective_label"] or "",
                "label_language": row["label_language"] or "",
                "parent_context_status": row["parent_context_status"],
                "parent_label": parent_text(row),
                "mapping_status": row["mapping_status"],
                "match_method": row["method"],
                "match_score": row["score"],
                "candidate_count": len(row["candidate_indexes"]),
                "conflict_group_id": row["conflict_group_id"],
                "candidate_keys": " | ".join(item["item_key"] for item in candidates),
                "candidate_names": " | ".join(
                    " / ".join(part for part in (item["canonical_zh"], item["canonical_pt"]) if part)
                    for item in candidates
                ),
                "reviewer_decision": "",
                "reviewer_note": "",
            })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v5-db", type=Path, default=DEFAULT_V5)
    parser.add_argument("--v3-db", type=Path, default=DEFAULT_V3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    v5 = connect_ro(args.v5_db.expanduser().resolve())
    v3 = connect_rw(args.v3_db.expanduser().resolve())
    try:
        standards, aliases = load_seed(v5)
        preserved = 0
        if args.apply:
            v3.execute("BEGIN")
            _, preserved = seed_catalogue(v3, standards, aliases)
            v3.commit()
        items, canonical, alias_index, children = index_catalogue(v3)
        rows = effective_rows(v3)
        mapped, counts = write_mappings(v3, rows, items, canonical, alias_index, children, apply=args.apply)
        if args.apply:
            v3.commit()
            v3.execute(
                "INSERT INTO audit_events("
                "audit_event_id, event_type, actor_id, details_json, reason, created_at"
                ") VALUES (?, 'standard_item_auto_map', 'p1-auto-map', ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    json.dumps({
                        "version": VERSION,
                        "rows": len(rows),
                        "status_counts": dict(counts),
                        "standard_items": len(items),
                        "preserved_catalogue_rows": preserved,
                        "approved_mappings": 0,
                    }, ensure_ascii=False, sort_keys=True),
                    "P1 first-pass proposed mappings; no approvals; "
                    "existing catalogue lifecycle preserved",
                    now(),
                ),
            )
            v3.commit()
        write_review(args.out.expanduser().resolve(), mapped, items)
        print(json.dumps({
            "standard_items": len(items),
            "preserved_catalogue_rows": preserved,
            "rows": len(rows),
            "status_counts": dict(counts),
            "applied": args.apply,
            "review_out": str(args.out),
        }, ensure_ascii=False, indent=2))
    finally:
        v5.close()
        v3.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
