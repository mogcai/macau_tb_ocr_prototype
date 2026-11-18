#!/usr/bin/env python3
"""Build and review the bank / line-item name standardization registry.

Examples::

    python tools/name_registry.py build --db data/macau_balancete.sqlite
    python tools/name_registry.py list --db data/macau_balancete.sqlite
    python tools/name_registry.py approve --db data/macau_balancete.sqlite \
        --id 12 --by reviewer
    python tools/name_registry.py reject --db data/macau_balancete.sqlite \
        --id 13 --by reviewer --note "different account"

Approved rules are applied to ``line_items.line_item_concept_id``. The OCR
fields (``label_raw``, ``label_standard``, ``bank_name_raw``) are never changed.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from macau_tb_ocr import normalize_label_key  # noqa: E402
from tools.build_database import (  # noqa: E402
    SCHEMA_VERSION,
    _connect,
    _schema,
    bank_key_for,
    normalize_text,
)

CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
DATE_LIKE_RE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _language_of(text: str, fallback: str = "") -> str:
    value = normalize_text(text)
    if CJK_RE.search(value):
        return "zh-Hant"
    if LATIN_RE.search(value):
        return "pt"
    return fallback


def _alias_key(text: str, language: str) -> str:
    return normalize_label_key(text, language or _language_of(text) or "pt")


def _bank_alias_key(text: str) -> str:
    return bank_key_for(text)


def _read_lexicon(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [
            row for row in csv.DictReader(handle)
            if normalize_text(row.get("label_std"))
        ]


def _next_registry_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'name_registry_version'"
    ).fetchone()
    try:
        current = int(row[0]) if row else 0
    except (TypeError, ValueError):
        current = 0
    version = current + 1
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('name_registry_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )
    return version


def _upsert_concept(
    conn: sqlite3.Connection,
    concept_key: str,
    *,
    canonical_zh: str = "",
    canonical_pt: str = "",
    canonical_en: str = "",
    level: str = "parent",
    status: str = "provisional",
    source: str = "",
    note: str = "",
) -> int:
    conn.execute(
        """
        INSERT INTO line_item_concepts (
            concept_key, canonical_zh, canonical_pt, canonical_en,
            level, status, source, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(concept_key) DO UPDATE SET
            canonical_zh = COALESCE(NULLIF(line_item_concepts.canonical_zh, ''),
                                    excluded.canonical_zh),
            canonical_pt = COALESCE(NULLIF(line_item_concepts.canonical_pt, ''),
                                    excluded.canonical_pt),
            canonical_en = COALESCE(NULLIF(line_item_concepts.canonical_en, ''),
                                    excluded.canonical_en),
            level = excluded.level,
            source = COALESCE(NULLIF(line_item_concepts.source, ''),
                              excluded.source)
        """,
        (concept_key, canonical_zh or None, canonical_pt or None,
         canonical_en or None, level, status, source or None, note or None),
    )
    row = conn.execute(
        "SELECT concept_id FROM line_item_concepts WHERE concept_key = ?",
        (concept_key,),
    ).fetchone()
    return int(row[0])


def _upsert_line_alias(
    conn: sqlite3.Connection,
    concept_id: int,
    raw_name: str,
    language: str,
    *,
    alias_type: str = "official",
    status: str = "approved",
    source: str = "lexicon",
    note: str = "",
) -> None:
    raw = normalize_text(raw_name)
    if not raw:
        return
    key = _alias_key(raw, language)
    if not key:
        return
    try:
        conn.execute(
            """
            INSERT INTO line_item_aliases (
                concept_id, raw_name, normalized_name, language,
                alias_type, status, source, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(concept_id, normalized_name) DO UPDATE SET
                raw_name = excluded.raw_name,
                language = excluded.language,
                alias_type = excluded.alias_type,
                status = excluded.status,
                source = excluded.source
            """,
            (concept_id, raw, key, language, alias_type, status,
             source, note or None),
        )
    except sqlite3.IntegrityError:
        # Another approved alias already owns this key. Keep the existing rule;
        # the collision is visible through v_alias_conflicts.
        pass


def _upsert_bank_alias(
    conn: sqlite3.Connection,
    bank_id: int,
    raw_name: str,
    language: str,
    *,
    alias_type: str = "official",
    status: str = "approved",
    source: str = "canonical",
    note: str = "",
) -> None:
    raw = normalize_text(raw_name)
    if not raw:
        return
    key = _bank_alias_key(raw)
    try:
        conn.execute(
            """
            INSERT INTO bank_name_aliases (
                bank_id, raw_name, normalized_name, language,
                alias_type, status, source, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bank_id, normalized_name) DO UPDATE SET
                raw_name = excluded.raw_name,
                language = excluded.language,
                alias_type = excluded.alias_type,
                status = excluded.status,
                source = excluded.source
            """,
            (bank_id, raw, key, language, alias_type, status,
             source, note or None),
        )
    except sqlite3.IntegrityError:
        pass


def _concept_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    lookup: dict[tuple[str, str], int] = {}
    for row in conn.execute(
        """
        SELECT a.normalized_name, COALESCE(a.language, ''), a.concept_id
        FROM line_item_aliases a
        JOIN line_item_concepts c ON c.concept_id = a.concept_id
        WHERE a.status = 'approved' AND c.status <> 'merged'
        ORDER BY a.alias_id
        """
    ):
        lookup.setdefault((row[0], row[1]), int(row[2]))
    return lookup


def _resolve_concept(
    conn: sqlite3.Connection, concept_id: int | None
) -> int | None:
    current = concept_id
    seen: set[int] = set()
    while current:
        if current in seen:
            return None
        seen.add(current)
        row = conn.execute(
            "SELECT merged_into_concept_id FROM line_item_concepts "
            "WHERE concept_id = ?",
            (current,),
        ).fetchone()
        if row is None:
            return current
        if row[0] is None:
            return current
        current = int(row[0])
    return None


def _line_stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT COALESCE(NULLIF(label_standard, ''), label_raw) AS raw_name,
               COALESCE(label_language, '') AS language,
               label_standard,
               COUNT(*) AS occurrence_count,
               GROUP_CONCAT(DISTINCT publication_date) AS source_issues,
               GROUP_CONCAT(DISTINCT pdf_page) AS source_pages
        FROM v_current_line_items
        WHERE COALESCE(NULLIF(label_standard, ''), label_raw) <> ''
        GROUP BY raw_name, language, label_standard
        ORDER BY occurrence_count DESC, raw_name
        """
    ).fetchall()


def _bank_stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT bank_name_raw AS raw_name,
               COUNT(*) AS occurrence_count,
               GROUP_CONCAT(DISTINCT publication_date) AS source_issues,
               GROUP_CONCAT(DISTINCT pdf_pages) AS source_pages
        FROM v_current_statements
        WHERE COALESCE(bank_name_raw, '') <> ''
        GROUP BY bank_name_raw
        ORDER BY occurrence_count DESC, bank_name_raw
        """
    ).fetchall()


def _add_review(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    raw_name: str,
    normalized_name: str,
    language: str,
    current_entity_id: int | None,
    suggested_entity_id: int | None,
    suggested_canonical: str,
    match_method: str,
    match_score: float,
    occurrence_count: int,
    source_issues: str,
    source_pages: str,
) -> None:
    if not raw_name or suggested_entity_id is None:
        return
    conn.execute(
        """
        INSERT INTO name_mapping_review (
            entity_type, raw_name, normalized_name, language,
            current_entity_id, suggested_entity_id, suggested_canonical,
            match_method, match_score, occurrence_count,
            source_issues, source_pages, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        ON CONFLICT(entity_type, normalized_name, suggested_entity_id)
        DO UPDATE SET
            occurrence_count = MAX(name_mapping_review.occurrence_count,
                                   excluded.occurrence_count),
            source_issues = excluded.source_issues,
            source_pages = excluded.source_pages,
            match_score = MAX(name_mapping_review.match_score,
                              excluded.match_score)
        """,
        (entity_type, raw_name, normalized_name, language or None,
         current_entity_id, suggested_entity_id, suggested_canonical,
         match_method, match_score, occurrence_count,
         source_issues or None, source_pages or None),
    )


def seed_lexicon(conn: sqlite3.Connection, lexicon_path: Path) -> Counter[str]:
    stats: Counter[str] = Counter()
    for row in _read_lexicon(lexicon_path):
        label_id = normalize_text(row.get("label_id")) or (
            f"lex_{stats['rows'] + 1}"
        )
        standard = normalize_text(row.get("label_std"))
        language = normalize_text(row.get("language")) or _language_of(standard)
        level = normalize_text(row.get("level")) or "parent"
        concept_key = f"lex:{label_id}"
        canonical_zh = standard if language == "zh-Hant" else ""
        canonical_pt = standard if language == "pt" else ""
        concept_id = _upsert_concept(
            conn, concept_key,
            canonical_zh=canonical_zh,
            canonical_pt=canonical_pt,
            level=level,
            status="approved",
            source="labels/label_lexicon.csv",
        )
        _upsert_line_alias(
            conn, concept_id, standard, language,
            alias_type="official", status="approved", source="lexicon",
        )
        for alias in (row.get("aliases") or "").split("|"):
            alias = normalize_text(alias)
            if alias:
                _upsert_line_alias(
                    conn, concept_id, alias, language,
                    alias_type="ocr_variant", status="approved",
                    source="lexicon",
                )
        stats["concepts"] += 1
    return stats


def seed_banks(conn: sqlite3.Connection, aliases_path: Path | None) -> Counter[str]:
    stats: Counter[str] = Counter()
    alias_map: dict[str, int] = {}
    if aliases_path and aliases_path.exists():
        with aliases_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                alias = normalize_text(row.get("alias"))
                canonical = normalize_text(row.get("canonical"))
                if not alias or not canonical:
                    continue
                bank_key = bank_key_for(canonical)
                bank = conn.execute(
                    "SELECT bank_id FROM banks WHERE bank_key = ?", (bank_key,)
                ).fetchone()
                if bank is None:
                    continue
                alias_map[alias.casefold()] = int(bank["bank_id"])

    for row in conn.execute(
        "SELECT bank_id, bank_key, display_name, notes FROM banks"
    ).fetchall():
        bank_id = int(row["bank_id"])
        display = normalize_text(row["display_name"])
        text = display or row["bank_key"].removeprefix("bank:")
        parts = [part.strip() for part in re.split(r"[|｜]", text) if part.strip()]
        zh = next((part for part in parts if CJK_RE.search(part)), "")
        pt = next((part for part in parts if LATIN_RE.search(part)), "")
        entity_type = "bank"
        upper = text.upper()
        if "GRUPO" in upper or "GROUP" in upper:
            entity_type = "group"
        elif "分行" in text or "SUCURSAL" in upper or "BRANCH" in upper:
            entity_type = "branch"
        conn.execute(
            """
            UPDATE banks SET canonical_zh = ?, canonical_pt = ?,
                             entity_type = ?,
                             registry_status = CASE
                                 WHEN registry_status = 'approved' THEN 'approved'
                                 ELSE 'provisional'
                             END
            WHERE bank_id = ?
            """,
            (zh or None, pt or None, entity_type, bank_id),
        )
        _upsert_bank_alias(
            conn, bank_id, text, _language_of(text),
            alias_type="official", status="approved", source="canonical",
        )
        for part in parts:
            _upsert_bank_alias(
                conn, bank_id, part, _language_of(part),
                alias_type="official", status="approved", source="canonical",
            )
        stats["banks"] += 1

    for row in _bank_stats(conn):
        raw = normalize_text(row["raw_name"])
        key = _bank_alias_key(raw)
        bank = conn.execute(
            "SELECT bank_id FROM banks WHERE bank_key = ?", (key,)
        ).fetchone()
        if bank is None and raw.casefold() in alias_map:
            bank_id = alias_map[raw.casefold()]
        elif bank is not None:
            bank_id = int(bank["bank_id"])
        else:
            # No exact entity: suggest the closest bank name, if reasonably close.
            candidates = conn.execute(
                "SELECT bank_id, display_name FROM banks"
            ).fetchall()
            best = None
            best_score = 0.0
            for candidate in candidates:
                score = difflib.SequenceMatcher(
                    None, key, _bank_alias_key(candidate["display_name"])
                ).ratio()
                if score > best_score:
                    best, best_score = candidate, score
            if best is None or best_score < 0.75:
                continue
            bank_id = int(best["bank_id"])
            _add_review(
                conn, entity_type="bank", raw_name=raw,
                normalized_name=key, language=_language_of(raw),
                current_entity_id=None, suggested_entity_id=bank_id,
                suggested_canonical=normalize_text(best["display_name"]),
                match_method="fuzzy", match_score=best_score,
                occurrence_count=int(row["occurrence_count"]),
                source_issues=normalize_text(row["source_issues"]),
                source_pages=normalize_text(row["source_pages"]),
            )
            stats["bank_reviews"] += 1
            continue
        approved = conn.execute(
            "SELECT 1 FROM bank_name_aliases "
            "WHERE bank_id = ? AND normalized_name = ? AND status = 'approved'",
            (bank_id, key),
        ).fetchone()
        if not approved:
            _add_review(
                conn, entity_type="bank", raw_name=raw,
                normalized_name=key, language=_language_of(raw),
                current_entity_id=None, suggested_entity_id=bank_id,
                suggested_canonical=normalize_text(
                    conn.execute(
                        "SELECT display_name FROM banks WHERE bank_id = ?",
                        (bank_id,),
                    ).fetchone()[0]
                ),
                match_method="exact", match_score=1.0,
                occurrence_count=int(row["occurrence_count"]),
                source_issues=normalize_text(row["source_issues"]),
                source_pages=normalize_text(row["source_pages"]),
            )
            stats["bank_reviews"] += 1
    return stats


def _nearest_concept(
    conn: sqlite3.Connection,
    raw_name: str,
    language: str,
    exclude: set[int] | None = None,
) -> tuple[int | None, str, float]:
    key = _alias_key(raw_name, language)
    if not key:
        return None, "", 0.0
    candidates = conn.execute(
        """
        SELECT c.concept_id, COALESCE(c.canonical_zh, c.canonical_pt,
                                      c.canonical_en, '') AS canonical,
               a.normalized_name
        FROM line_item_concepts c
        JOIN line_item_aliases a ON a.concept_id = c.concept_id
        WHERE c.status <> 'merged' AND a.status = 'approved'
        """
    ).fetchall()
    best_id = None
    best_name = ""
    best_score = 0.0
    exclude = exclude or set()
    for row in candidates:
        if int(row["concept_id"]) in exclude:
            continue
        score = difflib.SequenceMatcher(None, key, row["normalized_name"]).ratio()
        if score > best_score:
            best_id = int(row["concept_id"])
            best_name = normalize_text(row["canonical"])
            best_score = score
    return best_id, best_name, best_score


def generate_line_item_reviews(conn: sqlite3.Connection) -> Counter[str]:
    stats: Counter[str] = Counter()
    approved = _concept_lookup(conn)
    for row in _line_stats(conn):
        raw = normalize_text(row["raw_name"])
        language = normalize_text(row["language"]) or _language_of(raw)
        key = _alias_key(raw, language)
        if not key:
            continue
        if (key, language) in approved:
            continue
        exact = approved.get((key, language))
        if exact:
            continue
        suggested, canonical, score = _nearest_concept(
            conn, raw, language
        )
        if suggested is None or score < 0.55:
            # New name: create a provisional concept so it can be reviewed
            # without inventing a mapping to an existing account.
            concept_key = f"auto:{language}:{key}"
            suggested = _upsert_concept(
                conn, concept_key,
                canonical_zh=raw if language == "zh-Hant" else "",
                canonical_pt=raw if language == "pt" else "",
                level="parent",
                status="provisional",
                source="auto_discovery",
            )
            _upsert_line_alias(
                conn, suggested, raw, language,
                alias_type="official", status="pending",
                source="auto_discovery",
            )
            canonical = raw
            score = 0.0
            method = "new_candidate"
        else:
            method = "fuzzy" if score < 0.999 else "exact"
        _add_review(
            conn, entity_type="line_item", raw_name=raw,
            normalized_name=key, language=language,
            current_entity_id=None, suggested_entity_id=suggested,
            suggested_canonical=canonical, match_method=method,
            match_score=score, occurrence_count=int(row["occurrence_count"]),
            source_issues=normalize_text(row["source_issues"]),
            source_pages=normalize_text(row["source_pages"]),
        )
        stats["line_item_reviews"] += 1
    return stats


def generate_bilingual_reviews(conn: sqlite3.Connection) -> Counter[str]:
    """Pair adjacent Chinese/Portuguese lines in bilingual statements.

    The pair is evidence for review, not an approved translation. A page such as
    issue-45 page 17 prints 現金 / Caixa, then -澳門元 / Patacas, etc.
    """
    stats: Counter[str] = Counter()
    approved = _concept_lookup(conn)
    statements = [
        row[0] for row in conn.execute(
            "SELECT DISTINCT statement_id FROM line_items "
            "WHERE label_language IN ('zh-Hant', 'pt') "
            "GROUP BY statement_id "
            "HAVING COUNT(DISTINCT label_language) > 1"
        )
    ]
    for statement_id in statements:
        rows = conn.execute(
            """
            SELECT row_order, label_standard, label_raw, label_language,
                   parent_label_standard
            FROM line_items
            WHERE statement_id = ? AND COALESCE(label_standard, '') <> ''
            ORDER BY row_order
            """,
            (statement_id,),
        ).fetchall()
        used: set[int] = set()
        for index in range(len(rows) - 1):
            left, right = rows[index], rows[index + 1]
            if left["row_order"] in used or right["row_order"] in used:
                continue
            if left["label_language"] == right["label_language"]:
                continue
            # Pair a zh line with the immediately following pt line (or the
            # reverse on pt-first forms). Marking both rows used prevents the
            # boundary pt/zh pair from creating a second, wrong candidate.
            zh_row = left if left["label_language"] == "zh-Hant" else right
            pt_row = right if left["label_language"] == "zh-Hant" else left
            used.add(left["row_order"])
            used.add(right["row_order"])
            zh_name = normalize_text(zh_row["label_standard"])
            pt_name = normalize_text(pt_row["label_standard"])
            if not zh_name or not pt_name or zh_name == pt_name:
                continue
            zh_concept = approved.get((_alias_key(zh_name, "zh-Hant"), "zh-Hant"))
            pt_concept = approved.get((_alias_key(pt_name, "pt"), "pt"))
            if not zh_concept:
                continue
            target = zh_concept
            current = pt_concept
            if current == target:
                continue
            _add_review(
                conn, entity_type="line_item", raw_name=pt_name,
                normalized_name=_alias_key(pt_name, "pt"), language="pt",
                current_entity_id=current, suggested_entity_id=target,
                suggested_canonical=zh_name,
                match_method="bilingual_pair", match_score=0.90,
                occurrence_count=1, source_issues="", source_pages="",
            )
            stats["bilingual_reviews"] += 1
    return stats


def apply_line_item_mappings(
    conn: sqlite3.Connection, mapping_version: int
) -> Counter[str]:
    approved = _concept_lookup(conn)
    stats: Counter[str] = Counter()
    rows = conn.execute(
        """
        SELECT line_item_id, label_standard, label_raw, label_language,
               line_item_concept_id
        FROM line_items
        """
    ).fetchall()
    for row in rows:
        language = normalize_text(row["label_language"])
        standard = normalize_text(row["label_standard"])
        raw = normalize_text(row["label_raw"])
        concept_id = None
        status = "concept_unmapped"
        if standard:
            concept_id = approved.get((_alias_key(standard, language), language))
            if concept_id:
                status = "concept_exact"
        if concept_id is None and raw:
            concept_id = approved.get((_alias_key(raw, language), language))
            if concept_id:
                status = "concept_alias"
        concept_id = _resolve_concept(conn, concept_id)
        if concept_id is None:
            status = "concept_unmapped" if (standard or raw) else "concept_missing_label"
        conn.execute(
            "UPDATE line_items SET line_item_concept_id = ?, "
            "mapping_status = ?, mapping_version = ? WHERE line_item_id = ?",
            (concept_id, status, mapping_version, row["line_item_id"]),
        )
        stats[status] += 1
    return stats


def build_registry(
    db_path: Path,
    *,
    lexicon_path: Path,
    bank_aliases_path: Path | None,
) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        _schema(conn)
        with conn:
            version = _next_registry_version(conn)
            stats: Counter[str] = Counter()
            stats.update(seed_lexicon(conn, lexicon_path))
            stats.update(seed_banks(conn, bank_aliases_path))
            stats.update(generate_line_item_reviews(conn))
            stats.update(generate_bilingual_reviews(conn))
            mapping = apply_line_item_mappings(conn, version)
        pending = conn.execute(
            "SELECT entity_type, COUNT(*) FROM name_mapping_review "
            "WHERE status = 'pending' GROUP BY entity_type"
        ).fetchall()
        concepts = conn.execute(
            "SELECT COUNT(*) FROM line_item_concepts WHERE status <> 'merged'"
        ).fetchone()[0]
        aliases = conn.execute(
            "SELECT COUNT(*) FROM line_item_aliases WHERE status = 'approved'"
        ).fetchone()[0]
        banks = conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0]
    finally:
        conn.close()
    return {
        "database": str(db_path.resolve()),
        "schema_version": SCHEMA_VERSION,
        "registry_version": version,
        "counts": {
            "banks": banks,
            "concepts": concepts,
            "approved_line_item_aliases": aliases,
            "line_item_mapping": dict(mapping),
            "pending_reviews": {
                str(row[0]): int(row[1]) for row in pending
            },
        },
        "seed": dict(stats),
        "generated_at": _now(),
    }


def list_reviews(
    db_path: Path,
    entity_type: str | None,
    limit: int,
    match_method: str | None = None,
) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        _schema(conn)
        sql = (
            "SELECT review_id, entity_type, raw_name, language, "
            "current_entity_id, suggested_entity_id, suggested_canonical, "
            "match_method, match_score, occurrence_count, status "
            "FROM name_mapping_review WHERE status = 'pending'"
        )
        params: list[Any] = []
        if entity_type:
            sql += " AND entity_type = ?"
            params.append(entity_type)
        if match_method:
            sql += " AND match_method = ?"
            params.append(match_method)
        sql += " ORDER BY occurrence_count DESC, review_id LIMIT ?"
        params.append(limit)
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _audit(
    conn: sqlite3.Connection,
    entity_type: str,
    raw_name: str,
    action: str,
    old_entity_id: int | None,
    new_entity_id: int | None,
    actor: str,
    note: str,
) -> None:
    conn.execute(
        "INSERT INTO name_mapping_audit "
        "(entity_type, raw_name, action, old_entity_id, new_entity_id, "
        " actor, at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (entity_type, raw_name, action, old_entity_id, new_entity_id,
         actor, _now(), note or None),
    )


def _merge_concepts(
    conn: sqlite3.Connection,
    old_id: int,
    new_id: int,
    actor: str,
    raw_name: str,
) -> None:
    if old_id == new_id:
        return
    old = conn.execute(
        "SELECT canonical_zh, canonical_pt, canonical_en FROM "
        "line_item_concepts WHERE concept_id = ?",
        (old_id,),
    ).fetchone()
    new = conn.execute(
        "SELECT canonical_zh, canonical_pt, canonical_en FROM "
        "line_item_concepts WHERE concept_id = ?",
        (new_id,),
    ).fetchone()
    if old is None or new is None:
        return
    conn.execute(
        "UPDATE line_item_concepts SET "
        "canonical_zh = COALESCE(NULLIF(canonical_zh, ''), ?), "
        "canonical_pt = COALESCE(NULLIF(canonical_pt, ''), ?), "
        "canonical_en = COALESCE(NULLIF(canonical_en, ''), ?) "
        "WHERE concept_id = ?",
        (old["canonical_zh"], old["canonical_pt"], old["canonical_en"], new_id),
    )
    for alias in conn.execute(
        "SELECT alias_id, normalized_name FROM line_item_aliases "
        "WHERE concept_id = ?", (old_id,)
    ).fetchall():
        try:
            conn.execute(
                "UPDATE line_item_aliases SET concept_id = ? WHERE alias_id = ?",
                (new_id, alias["alias_id"]),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "DELETE FROM line_item_aliases WHERE alias_id = ?",
                (alias["alias_id"],),
            )
    conn.execute(
        "UPDATE line_items SET line_item_concept_id = ? "
        "WHERE line_item_concept_id = ?",
        (new_id, old_id),
    )
    conn.execute(
        "UPDATE line_item_concepts SET status = 'merged', "
        "merged_into_concept_id = ? WHERE concept_id = ?",
        (new_id, old_id),
    )
    _audit(conn, "line_item", raw_name, "merge_concept", old_id, new_id,
           actor, "")


def approve_review(
    db_path: Path,
    review_id: int,
    actor: str,
    *,
    concept_id: int | None = None,
    bank_id: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        _schema(conn)
        with conn:
            review = conn.execute(
                "SELECT * FROM name_mapping_review WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if review is None:
                raise RuntimeError(f"review {review_id} not found")
            if review["status"] != "pending":
                raise RuntimeError(
                    f"review {review_id} is already {review['status']}"
                )
            entity_type = review["entity_type"]
            target = concept_id or review["suggested_entity_id"]
            if target is None:
                raise RuntimeError("review has no suggested entity")
            if entity_type == "line_item":
                target = _resolve_concept(conn, int(target)) or int(target)
                current = review["current_entity_id"]
                current = (
                    _resolve_concept(conn, int(current)) if current else None
                )
                if current and int(current) != target:
                    _merge_concepts(
                        conn, int(current), target, actor, review["raw_name"]
                    )
                _upsert_line_alias(
                    conn, target, review["raw_name"],
                    review["language"] or _language_of(review["raw_name"]),
                    alias_type="translation" if review["current_entity_id"]
                    else "official",
                    status="approved", source="review", note=note,
                )
                language = review["language"] or _language_of(review["raw_name"])
                key = _alias_key(review["raw_name"], language)
                added = conn.execute(
                    "SELECT 1 FROM line_item_aliases WHERE concept_id = ? "
                    "AND normalized_name = ? AND status = 'approved'",
                    (target, key),
                ).fetchone()
                if not added:
                    raise RuntimeError(
                        f"cannot add alias {review['raw_name']!r}: it is "
                        "already approved for another concept"
                    )
                conn.execute(
                    "UPDATE line_item_concepts SET status = 'approved' "
                    "WHERE concept_id = ? AND status = 'provisional'",
                    (target,),
                )
                conn.execute(
                    "UPDATE name_mapping_review SET status = 'approved', "
                    "reviewed_by = ?, reviewed_at = ?, note = ? "
                    "WHERE review_id = ?",
                    (actor, _now(), note or None, review_id),
                )
                _audit(conn, entity_type, review["raw_name"], "approve",
                       review["current_entity_id"], target, actor, note)
                version = _next_registry_version(conn)
                mapping = apply_line_item_mappings(conn, version)
                result = {
                    "review_id": review_id,
                    "status": "approved",
                    "entity_type": entity_type,
                    "target_id": target,
                    "mapping_version": version,
                    "mapping": dict(mapping),
                }
            elif entity_type == "bank":
                target = int(bank_id or target)
                _upsert_bank_alias(
                    conn, target, review["raw_name"],
                    review["language"] or _language_of(review["raw_name"]),
                    alias_type="official", status="approved",
                    source="review", note=note,
                )
                key = _bank_alias_key(review["raw_name"])
                added = conn.execute(
                    "SELECT 1 FROM bank_name_aliases WHERE bank_id = ? "
                    "AND normalized_name = ? AND status = 'approved'",
                    (target, key),
                ).fetchone()
                if not added:
                    raise RuntimeError(
                        f"cannot add bank alias {review['raw_name']!r}: it is "
                        "already approved for another bank"
                    )
                conn.execute(
                    "UPDATE banks SET registry_status = 'approved' "
                    "WHERE bank_id = ? AND registry_status = 'provisional'",
                    (target,),
                )
                conn.execute(
                    "UPDATE name_mapping_review SET status = 'approved', "
                    "reviewed_by = ?, reviewed_at = ?, note = ? "
                    "WHERE review_id = ?",
                    (actor, _now(), note or None, review_id),
                )
                _audit(conn, entity_type, review["raw_name"], "approve",
                       review["current_entity_id"], target, actor, note)
                result = {
                    "review_id": review_id,
                    "status": "approved",
                    "entity_type": entity_type,
                    "target_id": target,
                }
            else:
                raise RuntimeError(f"unknown entity type {entity_type}")
        return result
    finally:
        conn.close()


def reject_review(
    db_path: Path, review_id: int, actor: str, note: str = ""
) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        _schema(conn)
        with conn:
            review = conn.execute(
                "SELECT entity_type, raw_name FROM name_mapping_review "
                "WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if review is None:
                raise RuntimeError(f"review {review_id} not found")
            conn.execute(
                "UPDATE name_mapping_review SET status = 'rejected', "
                "reviewed_by = ?, reviewed_at = ?, note = ? "
                "WHERE review_id = ?",
                (actor, _now(), note or None, review_id),
            )
            _audit(conn, review["entity_type"], review["raw_name"], "reject",
                   None, None, actor, note)
        return {"review_id": review_id, "status": "rejected"}
    finally:
        conn.close()


def _print_reviews(rows: Iterable[sqlite3.Row]) -> None:
    for row in rows:
        print(
            f"{row['review_id']:>5}  {row['entity_type']:<9} "
            f"{row['raw_name']!r:<45} -> {row['suggested_canonical']!r} "
            f"[{row['match_method']} {row['match_score']:.2f}, "
            f"{row['occurrence_count']}x]"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and review the bank / line-item name registry"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="seed concepts/aliases and generate reviews")
    build.add_argument("--db", type=Path, default=Path("data/macau_balancete.sqlite"))
    build.add_argument("--lexicon", type=Path,
                       default=Path("labels/label_lexicon.csv"))
    build.add_argument("--bank-aliases", type=Path,
                       default=Path("labels/bank_aliases.csv"))
    build.add_argument("--report", type=Path)

    list_cmd = sub.add_parser("list", help="list pending name mappings")
    list_cmd.add_argument("--db", type=Path, default=Path("data/macau_balancete.sqlite"))
    list_cmd.add_argument("--entity-type", choices=["bank", "line_item"])
    list_cmd.add_argument("--match-method",
                          choices=["exact", "fuzzy", "bilingual_pair",
                                   "new_candidate"])
    list_cmd.add_argument("--limit", type=int, default=30)

    approve = sub.add_parser("approve", help="approve a pending mapping")
    approve.add_argument("--db", type=Path, default=Path("data/macau_balancete.sqlite"))
    approve.add_argument("--id", type=int, required=True)
    approve.add_argument("--by", required=True)
    approve.add_argument("--concept-id", type=int)
    approve.add_argument("--bank-id", type=int)
    approve.add_argument("--note", default="")

    reject = sub.add_parser("reject", help="reject a pending mapping")
    reject.add_argument("--db", type=Path, default=Path("data/macau_balancete.sqlite"))
    reject.add_argument("--id", type=int, required=True)
    reject.add_argument("--by", required=True)
    reject.add_argument("--note", default="")

    remap = sub.add_parser("remap", help="re-apply approved aliases to line_items")
    remap.add_argument("--db", type=Path, default=Path("data/macau_balancete.sqlite"))

    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            report = build_registry(
                args.db,
                lexicon_path=args.lexicon,
                bank_aliases_path=args.bank_aliases if args.bank_aliases.exists() else None,
            )
            report_path = args.report or args.db.with_name(
                f"{args.db.stem}_name_registry_report.json"
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"Report: {report_path.resolve()}")
        elif args.command == "list":
            _print_reviews(list_reviews(
                args.db, args.entity_type, args.limit, args.match_method
            ))
        elif args.command == "approve":
            result = approve_review(
                args.db, args.id, args.by,
                concept_id=args.concept_id, bank_id=args.bank_id,
                note=args.note,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "reject":
            print(json.dumps(
                reject_review(args.db, args.id, args.by, args.note),
                ensure_ascii=False, indent=2,
            ))
        elif args.command == "remap":
            conn = _connect(args.db)
            try:
                _schema(conn)
                with conn:
                    version = _next_registry_version(conn)
                    mapping = apply_line_item_mappings(conn, version)
                print(json.dumps(
                    {"mapping_version": version, "mapping": dict(mapping)},
                    ensure_ascii=False, indent=2,
                ))
            finally:
                conn.close()
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
