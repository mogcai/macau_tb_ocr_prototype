#!/usr/bin/env python3
"""Read-only audit of the v5 Macau Balancete SQLite database.

The audit intentionally never writes to the database.  It produces machine
readable JSON, human readable Markdown and CSV evidence files that can be used
as the baseline for the v3 controlled rebuild.

Usage::

    python tools/audit_v5.py \
        --db data/baseline/baseline_v5_20261118.sqlite \
        --out docs/audit

The output is evidence, not a migration.  In particular:

* automatic mappings are reported as evidence, never as approved;
* registry candidates are compared, not merged;
* parent/child context is not guessed when the source data does not contain it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]

CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
CHILD_LABEL_RE = re.compile(r"^[\-–—•·]\s*")
LABEL_ZH_VARIANTS = str.maketrans({
    "賬": "帳",
    "帐": "帳",
    "劵": "券",
    "産": "產",
    "価": "價",
    "歴": "歷",
    "査": "查",
    "扺": "抵",
    "资": "資",
    "総": "總",
    "収": "收",
})
JUNK_ENTITY_RE = re.compile(
    r"(#\s*\d|\bDEMONSTRA|Lista\s+de\s+detentores|"
    r"Relat[oó]rio\s+do\s+Auditor|\bNenhum\b|油庫|contrato|"
    r"^\s*\d{2,})",
    re.I,
)
DASH_RE = re.compile(r"^[\-–—−－]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Small helpers (kept local so the audit does not import OCR runtime deps)
# ---------------------------------------------------------------------------

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    return re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()


def has_chinese(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def language_of(text: str, fallback: str = "") -> str:
    value = norm_text(text)
    if has_chinese(value):
        return "zh-Hant"
    if LATIN_RE.search(value):
        return "pt"
    return fallback


def normalize_label_key(text: str, language: str) -> str:
    value = CHILD_LABEL_RE.sub("", norm_text(text))
    if language == "zh-Hant":
        value = value.translate(LABEL_ZH_VARIANTS)
        value = re.sub(r"[()（）〔〕【】\[\]、,，:：;；]", "", value)
        return re.sub(r"\s+", "", value)
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^0-9A-Za-z\s]", " ", ascii_text)
    return re.sub(r"\s+", "", ascii_text).casefold()


def bank_key_for(name: str) -> str:
    text = norm_text(name)
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).casefold()
    body = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", ascii_text)
    return f"bank:{body}"


def entity_name_key(name: str) -> str:
    text = norm_text(name)
    if has_chinese(text):
        return normalize_label_key(text, "zh-Hant")
    return bank_key_for(text)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def connect_ro(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def split_alias(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def split_merge_keys(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;|]", value or "") if part.strip()]


def decimal_or_none(value: Any) -> Decimal | None:
    text = norm_text(value).replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_data(conn: sqlite3.Connection) -> dict[str, Any]:
    standards = rows(conn, "SELECT * FROM standard_labels ORDER BY standard_id")
    standard_by_id = {int(row["standard_id"]): row for row in standards}
    for row in standards:
        parent = standard_by_id.get(row.get("parent_standard_id"))
        row["parent_key"] = parent["standard_key"] if parent else ""
        row["usage_rows"] = 0
        row["aliases"] = []

    standard_aliases = rows(
        conn,
        "SELECT alias_id, standard_id, raw_name, normalized_name, language, status "
        "FROM standard_label_aliases ORDER BY alias_id",
    )
    aliases_by_standard: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for alias in standard_aliases:
        aliases_by_standard[int(alias["standard_id"])].append(alias)
    for row in standards:
        row["aliases"] = aliases_by_standard.get(int(row["standard_id"]), [])

    concepts = rows(conn, "SELECT * FROM line_item_concepts ORDER BY concept_id")
    concept_by_id = {int(row["concept_id"]): row for row in concepts}
    for row in concepts:
        parent = concept_by_id.get(row.get("parent_concept_id"))
        row["parent_key"] = parent["concept_key"] if parent else ""
        row["usage_rows"] = 0
        row["aliases"] = []

    concept_aliases = rows(
        conn,
        "SELECT alias_id, concept_id, raw_name, normalized_name, language, "
        "alias_type, status FROM line_item_aliases ORDER BY alias_id",
    )
    aliases_by_concept: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for alias in concept_aliases:
        aliases_by_concept[int(alias["concept_id"])].append(alias)
    for row in concepts:
        row["aliases"] = aliases_by_concept.get(int(row["concept_id"]), [])

    runs = rows(conn, "SELECT * FROM runs ORDER BY imported_at, run_id")
    banks = rows(conn, "SELECT * FROM banks ORDER BY bank_id")
    statements = rows(
        conn,
        "SELECT s.*, r.app_version, r.is_published, "
        "b.display_name AS bank_display_name, b.entity_type, b.registry_status "
        "FROM statements s "
        "LEFT JOIN runs r ON r.run_id = s.run_id "
        "LEFT JOIN banks b ON b.bank_id = s.bank_id "
        "ORDER BY s.publication_date, s.statement_date, s.bank_key",
    )
    pages = rows(
        conn,
        "SELECT p.*, r.app_version FROM pages p "
        "LEFT JOIN runs r ON r.run_id = p.run_id ORDER BY p.run_id, p.pdf_page",
    )
    line_items = rows(
        conn,
        """
        SELECT li.line_item_id, li.statement_id, li.run_id, li.row_order,
               li.pdf_page, li.printed_page, li.bank_name_raw,
               li.label_raw, li.label_normalized, li.label_standard,
               li.label_standard_id, li.label_standard_status, li.label_language,
               li.label_role, li.parent_label_raw, li.parent_label_standard,
               li.label_path, li.occurrence, li.natural_key_status,
               li.line_item_concept_id, c.concept_key,
               c.canonical_zh AS concept_zh, c.canonical_pt AS concept_pt,
               li.user_standard_label_id,
               s.standard_key AS user_standard_key,
               s.canonical_zh AS user_zh, s.canonical_pt AS user_pt,
               li.user_standard_match_status,
               li.debit_raw, li.credit_raw, li.debit_amount, li.credit_amount,
               li.debit_cents, li.credit_cents, li.number_format,
               li.debit_parse_status, li.credit_parse_status,
               li.review_status, li.review_reason,
               li.row_confidence, li.row_average_confidence
        FROM line_items li
        LEFT JOIN line_item_concepts c ON c.concept_id = li.line_item_concept_id
        LEFT JOIN standard_labels s ON s.standard_id = li.user_standard_label_id
        ORDER BY li.line_item_id
        """,
    )

    for item in line_items:
        if item.get("user_standard_label_id") is not None:
            standard = standard_by_id.get(int(item["user_standard_label_id"]))
            if standard:
                standard["usage_rows"] = int(standard.get("usage_rows", 0)) + 1
        if item.get("line_item_concept_id") is not None:
            concept = concept_by_id.get(int(item["line_item_concept_id"]))
            if concept:
                concept["usage_rows"] = int(concept.get("usage_rows", 0)) + 1

    label_lexicon = read_csv(ROOT / "labels" / "label_lexicon.csv")
    draft = read_csv(ROOT / "labels" / "line_item_standard.csv")
    user_cleaned = read_csv(ROOT / "labels" / "standard_label_by_user.cleaned.csv")

    name_reviews = rows(conn, "SELECT * FROM name_mapping_review ORDER BY review_id")
    bank_aliases = rows(conn, "SELECT * FROM bank_aliases ORDER BY alias_key")
    bank_name_aliases = rows(conn, "SELECT * FROM bank_name_aliases ORDER BY alias_id")
    schema_meta = {row["key"]: row["value"] for row in rows(conn, "SELECT * FROM schema_meta")}

    counts = {
        "runs": len(runs),
        "runs_published": sum(1 for row in runs if int(row.get("is_published") or 0) == 1),
        "statements": len(statements),
        "pages": len(pages),
        "line_items": len(line_items),
        "banks": len(banks),
        "bank_aliases": len(bank_aliases),
        "bank_name_aliases": len(bank_name_aliases),
        "standard_labels": len(standards),
        "standard_label_aliases": len(standard_aliases),
        "line_item_concepts": len(concepts),
        "line_item_aliases": len(concept_aliases),
        "name_mapping_review": len(name_reviews),
        "row_verification": int(
            conn.execute("SELECT COUNT(*) FROM row_verification").fetchone()[0]
        ),
        "name_mapping_audit": int(
            conn.execute("SELECT COUNT(*) FROM name_mapping_audit").fetchone()[0]
        ),
    }

    return {
        "schema_meta": schema_meta,
        "counts": counts,
        "runs": runs,
        "banks": banks,
        "statements": statements,
        "pages": pages,
        "line_items": line_items,
        "standards": standards,
        "concepts": concepts,
        "standard_aliases": standard_aliases,
        "concept_aliases": concept_aliases,
        "label_lexicon": label_lexicon,
        "draft": draft,
        "user_cleaned": user_cleaned,
        "name_reviews": name_reviews,
        "bank_aliases": bank_aliases,
        "bank_name_aliases": bank_name_aliases,
    }


# ---------------------------------------------------------------------------
# Registry inventory and reconciliation
# ---------------------------------------------------------------------------

def registry_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for row in data["standards"]:
        items.append({
            "source": "standard_labels",
            "source_id": str(row["standard_id"]),
            "source_file": "db:standard_labels",
            "item_key": row["standard_key"],
            "canonical_zh": row.get("canonical_zh") or "",
            "canonical_pt": row.get("canonical_pt") or "",
            "canonical_en": row.get("canonical_en") or "",
            "parent_key": row.get("parent_key") or "",
            "level": row.get("level") or "",
            "status": row.get("status") or "",
            "category": row.get("category") or "",
            "usage_rows": int(row.get("usage_rows") or 0),
            "merge_keys": "",
            "aliases": [
                {
                    "raw_name": alias.get("raw_name") or "",
                    "language": alias.get("language") or language_of(alias.get("raw_name") or ""),
                    "status": alias.get("status") or "",
                    "source": "standard_label_aliases",
                }
                for alias in row.get("aliases", [])
            ],
            "note": row.get("note") or "",
        })

    for row in data["concepts"]:
        if str(row.get("concept_key") or "").startswith("auto:"):
            continue
        items.append({
            "source": "line_item_concepts_lex",
            "source_id": str(row["concept_id"]),
            "source_file": "db:line_item_concepts",
            "item_key": row.get("concept_key") or "",
            "canonical_zh": row.get("canonical_zh") or "",
            "canonical_pt": row.get("canonical_pt") or "",
            "canonical_en": row.get("canonical_en") or "",
            "parent_key": row.get("parent_key") or "",
            "level": row.get("level") or "",
            "status": row.get("status") or "",
            "category": row.get("category") or "",
            "usage_rows": int(row.get("usage_rows") or 0),
            "merge_keys": "",
            "aliases": [
                {
                    "raw_name": alias.get("raw_name") or "",
                    "language": alias.get("language") or language_of(alias.get("raw_name") or ""),
                    "status": alias.get("status") or "",
                    "source": "line_item_aliases",
                }
                for alias in row.get("aliases", [])
            ],
            "note": row.get("note") or "",
        })

    for row in data["draft"]:
        key = norm_text(row.get("standard_key"))
        if not key:
            continue
        aliases: list[dict[str, str]] = []
        for field, language in (
            ("aliases_zh", "zh-Hant"),
            ("aliases_pt", "pt"),
            ("aliases_other", ""),
        ):
            for alias in split_alias(row.get(field) or ""):
                aliases.append({
                    "raw_name": alias,
                    "language": language or language_of(alias),
                    "status": row.get("status") or "",
                    "source": "line_item_standard.csv",
                })
        items.append({
            "source": "line_item_standard_csv",
            "source_id": key,
            "source_file": "labels/line_item_standard.csv",
            "item_key": key,
            "canonical_zh": norm_text(row.get("canonical_zh")),
            "canonical_pt": norm_text(row.get("canonical_pt")),
            "canonical_en": norm_text(row.get("canonical_en")),
            "parent_key": norm_text(row.get("parent_key")),
            "level": norm_text(row.get("level")),
            "status": norm_text(row.get("status")),
            "category": norm_text(row.get("category")),
            "usage_rows": 0,
            "merge_keys": norm_text(row.get("merge_keys")),
            "aliases": aliases,
            "note": norm_text(row.get("note")),
        })

    for row in data["label_lexicon"]:
        label_id = norm_text(row.get("label_id"))
        if not label_id:
            continue
        aliases = [
            {
                "raw_name": alias,
                "language": norm_text(row.get("language")),
                "status": "approved",
                "source": "label_lexicon.csv",
            }
            for alias in split_alias(row.get("aliases") or "")
        ]
        items.append({
            "source": "label_lexicon_csv",
            "source_id": label_id,
            "source_file": "labels/label_lexicon.csv",
            "item_key": f"lex:{label_id}",
            "canonical_zh": norm_text(row.get("label_std")) if row.get("language") == "zh-Hant" else "",
            "canonical_pt": norm_text(row.get("label_std")) if row.get("language") == "pt" else "",
            "canonical_en": "",
            "parent_key": "",
            "level": norm_text(row.get("level")),
            "status": "approved",
            "category": "",
            "usage_rows": 0,
            "merge_keys": "",
            "aliases": aliases,
            "note": norm_text(row.get("note")),
        })
    return items


def canonical_keys(item: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    zh = item.get("canonical_zh") or ""
    pt = item.get("canonical_pt") or ""
    if zh:
        keys.add(("zh-Hant", normalize_label_key(zh, "zh-Hant")))
    if pt:
        keys.add(("pt", normalize_label_key(pt, "pt")))
    return {key for key in keys if key[1]}


def alias_keys(item: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for alias in item.get("aliases") or []:
        raw = norm_text(alias.get("raw_name"))
        if not raw:
            continue
        language = alias.get("language") or language_of(raw)
        if language not in ("zh-Hant", "pt"):
            language = language_of(raw)
        key = normalize_label_key(raw, language)
        if key:
            keys.add((language, key))
    return keys


def reconcile(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_items = registry_items(data)
    for item in all_items:
        item["_canon"] = canonical_keys(item)
        item["_alias"] = alias_keys(item)

    # The 96 auto-discovery concepts are deliberately excluded from the active
    # candidate set.  They remain visible in the inventory and in this matrix
    # as archive evidence.  `label_lexicon.csv` is legacy evidence only; it is
    # not a fourth catalogue and is therefore not a matching target.
    active_sources = {
        "standard_labels",
        "line_item_concepts_lex",
        "line_item_standard_csv",
    }
    active = [item for item in all_items if item["source"] in active_sources]

    canon_index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    alias_index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    key_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in active:
        for key in item["_canon"]:
            canon_index[key].append(item)
        for key in item["_alias"]:
            alias_index[key].append(item)
        key_index[item["item_key"]].append(item)

    output: list[dict[str, Any]] = []
    for item in all_items:
        if item["source"] == "label_lexicon_csv":
            continue
        suggestions: dict[tuple[str, str], dict[str, Any]] = {}

        def consider(other: dict[str, Any], score: int, basis: str) -> None:
            if other is item:
                return
            ident = (other["source"], other["source_id"])
            if ident not in suggestions or score > suggestions[ident]["score"]:
                suggestions[ident] = {
                    "score": score,
                    "basis": basis,
                    "item": other,
                }

        for key in item["_canon"]:
            for other in canon_index.get(key, []):
                shared = item["_canon"] & other["_canon"]
                score = 3 * len(shared)
                basis = "exact_bilingual" if len(shared) >= 2 else "exact_name"
                if item.get("parent_key") and item.get("parent_key") == other.get("parent_key"):
                    score += 2
                    basis += "+parent_context"
                if item.get("level") and item.get("level") == other.get("level"):
                    score += 1
                consider(other, score, basis)

        for key in item["_alias"]:
            for other in alias_index.get(key, []):
                if other["source"] == item["source"] and other["source_id"] == item["source_id"]:
                    continue
                consider(other, 1, "alias")

        merge_targets: list[dict[str, Any]] = []
        for merge_key in split_merge_keys(item.get("merge_keys") or ""):
            for other in key_index.get(merge_key, []):
                merge_targets.append(other)
                consider(other, 2, "merge_key_proposed")

        # Conservative fuzzy suggestion: only used when no exact/alias hit.
        if not suggestions:
            item_lang_keys = {
                language: key for language, key in item["_canon"]
            }
            for language, key in item_lang_keys.items():
                best: tuple[float, dict[str, Any]] | None = None
                for other in active:
                    if other["source"] == item["source"] and other["source_id"] == item["source_id"]:
                        continue
                    other_key = dict(other["_canon"]).get(language)
                    if not other_key:
                        continue
                    score = ratio(key, other_key)
                    if score >= 0.92 and (best is None or score > best[0]):
                        best = (score, other)
                if best is not None:
                    consider(best[1], 1, "fuzzy_candidate")

        ranked = sorted(
            suggestions.values(),
            key=lambda entry: (-entry["score"], entry["item"]["source"], entry["item"]["source_id"]),
        )
        top = ranked[:3]
        top_score = top[0]["score"] if top else 0
        tied = [entry for entry in ranked if entry["score"] == top_score]

        ambiguous: list[str] = []
        if item.get("level") == "child" or any(entry["item"].get("level") == "child" for entry in top):
            if not any("parent_context" in entry["basis"] for entry in top):
                ambiguous.append("child_parent_context_missing")
        if len(tied) > 1:
            ambiguous.append("multiple_equal_candidates")
        if item.get("merge_keys") and not merge_targets:
            ambiguous.append("merge_key_target_missing")

        if not top:
            action = "needs_review_unmatched"
        elif ambiguous:
            action = "needs_review"
        elif top_score >= 4 and len(tied) == 1 and "exact" in top[0]["basis"]:
            action = "merge_candidate"
        else:
            action = "needs_review"

        output.append({
            "source": item["source"],
            "source_id": item["source_id"],
            "source_file": item["source_file"],
            "item_key": item["item_key"],
            "canonical_zh": item["canonical_zh"],
            "canonical_pt": item["canonical_pt"],
            "canonical_en": item["canonical_en"],
            "parent_key": item["parent_key"],
            "level": item["level"],
            "status": item["status"],
            "category": item["category"],
            "usage_rows": item["usage_rows"],
            "merge_keys": item["merge_keys"],
            "alias_count": len(item["_alias"]),
            "suggested_match_1": _match_label(top[0]) if len(top) > 0 else "",
            "suggested_match_2": _match_label(top[1]) if len(top) > 1 else "",
            "suggested_match_3": _match_label(top[2]) if len(top) > 2 else "",
            "match_basis": ";".join(sorted({entry["basis"] for entry in top})),
            "match_score": top_score,
            "ambiguity": ";".join(ambiguous),
            "suggested_action": action,
            "decision": "",
            "note": item.get("note") or "",
        })

    # Auto-discovery concepts are included in the matrix as excluded archive
    # evidence, not as active candidates.
    for row in data["concepts"]:
        if not str(row.get("concept_key") or "").startswith("auto:"):
            continue
        output.append({
            "source": "line_item_concepts_auto",
            "source_id": str(row["concept_id"]),
            "source_file": "db:line_item_concepts",
            "item_key": row.get("concept_key") or "",
            "canonical_zh": row.get("canonical_zh") or "",
            "canonical_pt": row.get("canonical_pt") or "",
            "canonical_en": row.get("canonical_en") or "",
            "parent_key": row.get("parent_key") or "",
            "level": row.get("level") or "",
            "status": row.get("status") or "",
            "category": row.get("category") or "",
            "usage_rows": int(row.get("usage_rows") or 0),
            "merge_keys": "",
            "alias_count": len(row.get("aliases") or []),
            "suggested_match_1": "",
            "suggested_match_2": "",
            "suggested_match_3": "",
            "match_basis": "auto_discovery",
            "match_score": 0,
            "ambiguity": "excluded_from_active_catalogue",
            "suggested_action": "exclude_archive",
            "decision": "",
            "note": row.get("note") or "",
        })

    inventory = []
    for item in all_items:
        inventory.append({
            "source": item["source"],
            "source_id": item["source_id"],
            "source_file": item["source_file"],
            "item_key": item["item_key"],
            "canonical_zh": item["canonical_zh"],
            "canonical_pt": item["canonical_pt"],
            "canonical_en": item["canonical_en"],
            "parent_key": item["parent_key"],
            "level": item["level"],
            "status": item["status"],
            "category": item["category"],
            "usage_rows": item["usage_rows"],
            "alias_count": len(item["_alias"]),
            "merge_keys": item["merge_keys"],
            "note": item["note"],
        })
    for row in data["concepts"]:
        if str(row.get("concept_key") or "").startswith("auto:"):
            inventory.append({
                "source": "line_item_concepts_auto",
                "source_id": str(row["concept_id"]),
                "source_file": "db:line_item_concepts",
                "item_key": row.get("concept_key") or "",
                "canonical_zh": row.get("canonical_zh") or "",
                "canonical_pt": row.get("canonical_pt") or "",
                "canonical_en": row.get("canonical_en") or "",
                "parent_key": row.get("parent_key") or "",
                "level": row.get("level") or "",
                "status": row.get("status") or "",
                "category": row.get("category") or "",
                "usage_rows": int(row.get("usage_rows") or 0),
                "alias_count": len(row.get("aliases") or []),
                "merge_keys": "",
                "note": row.get("note") or "",
            })
    return output, inventory


def _match_label(entry: dict[str, Any]) -> str:
    item = entry["item"]
    return f'{item["source"]}:{item["item_key"]} (score={entry["score"]}, {entry["basis"]})'


# ---------------------------------------------------------------------------
# Mapping conflicts and coverage
# ---------------------------------------------------------------------------

def _name_set(zh: str, pt: str, language: str = "") -> set[tuple[str, str]]:
    values: set[tuple[str, str]] = set()
    for value, fallback in ((zh, "zh-Hant"), (pt, "pt")):
        text = norm_text(value)
        if not text:
            continue
        lang = language_of(text, fallback)
        key = normalize_label_key(text, lang)
        if key:
            values.add((lang, key))
    return values


def _pair_state(left: set[tuple[str, str]], right: set[tuple[str, str]]) -> str:
    if not left or not right:
        return "missing"
    shared = {language for language, _ in left} & {language for language, _ in right}
    if not shared:
        return "no_shared_language"
    for language in shared:
        left_keys = {key for lang, key in left if lang == language}
        right_keys = {key for lang, key in right if lang == language}
        if left_keys & right_keys:
            return "agree"
        if any(a in b or b in a for a in left_keys for b in right_keys):
            return "partial"
    return "disagree"


def mapping_conflicts(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], set[int]]:
    output: list[dict[str, Any]] = []
    partial_output: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    conflict_ids: set[int] = set()

    for item in data["line_items"]:
        legacy = _name_set(item.get("label_standard") or "", "", item.get("label_language") or "")
        concept = _name_set(item.get("concept_zh") or "", item.get("concept_pt") or "")
        user = _name_set(item.get("user_zh") or "", item.get("user_pt") or "")

        states = {
            "legacy_vs_concept": _pair_state(legacy, concept),
            "legacy_vs_user": _pair_state(legacy, user),
            "concept_vs_user": _pair_state(concept, user),
        }
        disagree = [name for name, state in states.items() if state == "disagree"]
        partial = [name for name, state in states.items() if state == "partial"]
        populated = [name for name, value in (("legacy", legacy), ("concept", concept), ("user", user)) if value]

        raw_concept = {norm_text(value) for value in (item.get("concept_zh"), item.get("concept_pt")) if norm_text(value)}
        raw_user = {norm_text(value) for value in (item.get("user_zh"), item.get("user_pt")) if norm_text(value)}
        if raw_concept and raw_user and not (raw_concept & raw_user):
            counts["raw_concept_user_name_mismatch_rows"] += 1

        if disagree:
            conflict_ids.add(int(item["line_item_id"]))
            counts["conflict_rows"] += 1
            if len(populated) >= 3:
                counts["three_way_rows"] += 1
            for name in disagree:
                counts[name] += 1
            output.append({
                "line_item_id": item["line_item_id"],
                "statement_id": item.get("statement_id") or "",
                "run_id": item.get("run_id") or "",
                "row_order": item.get("row_order") or "",
                "label_raw": item.get("label_raw") or "",
                "label_language": item.get("label_language") or "",
                "legacy_label": item.get("label_standard") or "",
                "legacy_id": item.get("label_standard_id") or "",
                "legacy_status": item.get("label_standard_status") or "",
                "concept_key": item.get("concept_key") or "",
                "concept_zh": item.get("concept_zh") or "",
                "concept_pt": item.get("concept_pt") or "",
                "user_standard_key": item.get("user_standard_key") or "",
                "user_zh": item.get("user_zh") or "",
                "user_pt": item.get("user_pt") or "",
                "user_match_status": item.get("user_standard_match_status") or "",
                "conflict_pairs": ";".join(disagree),
                "partial_pairs": ";".join(partial),
                "evidence_sources": ";".join(populated),
                "mapping_status": "conflict",
            })
        elif partial:
            counts["partial_rows"] += 1
            for name in partial:
                counts[name + "_partial"] += 1
            partial_output.append({
                "line_item_id": item["line_item_id"],
                "statement_id": item.get("statement_id") or "",
                "run_id": item.get("run_id") or "",
                "row_order": item.get("row_order") or "",
                "label_raw": item.get("label_raw") or "",
                "label_language": item.get("label_language") or "",
                "legacy_label": item.get("label_standard") or "",
                "legacy_id": item.get("label_standard_id") or "",
                "legacy_status": item.get("label_standard_status") or "",
                "concept_key": item.get("concept_key") or "",
                "concept_zh": item.get("concept_zh") or "",
                "concept_pt": item.get("concept_pt") or "",
                "user_standard_key": item.get("user_standard_key") or "",
                "user_zh": item.get("user_zh") or "",
                "user_pt": item.get("user_pt") or "",
                "user_match_status": item.get("user_standard_match_status") or "",
                "conflict_pairs": "",
                "partial_pairs": ";".join(partial),
                "evidence_sources": ";".join(populated),
                "mapping_status": "partial_overlap",
            })
        else:
            if legacy and concept and user:
                counts["three_way_agree_rows"] += 1
            elif legacy and user:
                counts["legacy_user_agree_rows"] += 1

    return output, partial_output, dict(counts), conflict_ids


def mapping_coverage(data: dict[str, Any], conflict_ids: set[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs_by_id = {row["run_id"]: row for row in data["runs"]}
    by_run: dict[str, Counter[str]] = defaultdict(Counter)
    by_language: dict[str, Counter[str]] = defaultdict(Counter)

    for item in data["line_items"]:
        run_id = item.get("run_id") or ""
        language = item.get("label_language") or "(blank)"
        for target in (by_run[run_id], by_language[language]):
            target["rows"] += 1
            if item.get("user_standard_label_id") is not None:
                target["user_mapped"] += 1
            else:
                target["user_unmapped"] += 1
            if item.get("line_item_concept_id") is not None:
                target["concept_mapped"] += 1
            else:
                target["concept_unmapped"] += 1
            if item.get("label_standard_id"):
                target["legacy_present"] += 1
            else:
                target["legacy_missing"] += 1
            if int(item["line_item_id"]) in conflict_ids:
                target["conflict_rows"] += 1
            if not norm_text(item.get("label_standard")):
                target["no_standard_label"] += 1

    run_rows: list[dict[str, Any]] = []
    for run_id, counts in sorted(by_run.items()):
        run = runs_by_id.get(run_id, {})
        row = {
            "run_id": run_id,
            "run_dir": run.get("run_dir") or "",
            "app_version": run.get("app_version") or "",
            "is_published": int(run.get("is_published") or 0),
            "source_pdf_sha256": run.get("source_pdf_sha256") or "",
            "rows": counts["rows"],
            "user_mapped": counts["user_mapped"],
            "user_unmapped": counts["user_unmapped"],
            "concept_mapped": counts["concept_mapped"],
            "concept_unmapped": counts["concept_unmapped"],
            "legacy_present": counts["legacy_present"],
            "legacy_missing": counts["legacy_missing"],
            "conflict_rows": counts["conflict_rows"],
            "no_standard_label": counts["no_standard_label"],
        }
        row["user_mapped_pct"] = round(100 * row["user_mapped"] / row["rows"], 2) if row["rows"] else 0
        row["conflict_pct"] = round(100 * row["conflict_rows"] / row["rows"], 2) if row["rows"] else 0
        run_rows.append(row)

    language_rows: list[dict[str, Any]] = []
    for language, counts in sorted(by_language.items()):
        row = {"label_language": language, **counts}
        row["user_mapped_pct"] = round(100 * counts["user_mapped"] / counts["rows"], 2) if counts["rows"] else 0
        language_rows.append(row)

    return run_rows, language_rows


# ---------------------------------------------------------------------------
# Entity / statement / page anomalies
# ---------------------------------------------------------------------------

def _union_find() -> tuple[dict[int, int], Any, Any]:
    parent: dict[int, int] = {}

    def find(value: int) -> int:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    return parent, find, union


def entity_anomalies(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    statements_by_bank: Counter[int] = Counter()
    rows_by_bank: Counter[int] = Counter()
    statement_to_bank = {
        row["statement_id"]: row.get("bank_id") for row in data["statements"]
    }
    for statement in data["statements"]:
        if statement.get("bank_id") is not None:
            statements_by_bank[int(statement["bank_id"])] += 1
    for item in data["line_items"]:
        bank_id = statement_to_bank.get(item.get("statement_id"))
        if bank_id is not None:
            rows_by_bank[int(bank_id)] += 1

    alias_count: Counter[int] = Counter()
    for alias in data["bank_name_aliases"]:
        alias_count[int(alias["bank_id"])] += 1

    parent, find, union = _union_find()
    for bank in data["banks"]:
        find(int(bank["bank_id"]))

    # Explicit aliases are stronger evidence than name similarity.
    banks_by_key = {bank["bank_key"]: int(bank["bank_id"]) for bank in data["banks"]}
    alias_groups: dict[str, set[int]] = defaultdict(set)
    for alias in data["bank_name_aliases"]:
        bank_id = int(alias["bank_id"])
        for value in (alias.get("raw_name"), alias.get("normalized_name")):
            key = entity_name_key(value or "")
            if key and key != "bank:":
                alias_groups[key].add(bank_id)
    for alias in data["bank_aliases"]:
        bank_id = banks_by_key.get(alias.get("bank_key") or "")
        if bank_id is None:
            continue
        for value in (alias.get("alias_name"), alias.get("canonical_name")):
            key = entity_name_key(value or "")
            if key and key != "bank:":
                alias_groups[key].add(bank_id)
    for bank_ids in alias_groups.values():
        ordered = sorted(bank_ids)
        for other in ordered[1:]:
            union(ordered[0], other)

    # OCR-variant candidates.  This intentionally requires the same entity
    # type and a matching distinctive prefix so that common Chinese legal
    # suffixes (銀行 / 分行 / 股份有限公司) do not create false merges.
    for index, left in enumerate(data["banks"]):
        for right in data["banks"][index + 1:]:
            if left.get("entity_type") != right.get("entity_type"):
                continue
            left_name = norm_text(left.get("display_name"))
            right_name = norm_text(right.get("display_name"))
            if not left_name or not right_name:
                continue
            left_cjk = has_chinese(left_name)
            right_cjk = has_chinese(right_name)
            if left_cjk != right_cjk:
                continue
            left_key = entity_name_key(left_name)
            right_key = entity_name_key(right_name)
            if has_chinese(left_name):
                if left_key[:4] != right_key[:4]:
                    continue
                threshold = 0.97
            else:
                if not (LATIN_RE.search(left_name) and LATIN_RE.search(right_name)):
                    continue
                if left_key[:10] != right_key[:10]:
                    continue
                threshold = 0.95
            if abs(len(left_key) - len(right_key)) > 3:
                continue
            if ratio(left_key, right_key) >= threshold:
                union(int(left["bank_id"]), int(right["bank_id"]))

    rows_out: list[dict[str, Any]] = []
    flagged = 0
    groups: dict[int, list[int]] = defaultdict(list)
    for bank in data["banks"]:
        bank_id = int(bank["bank_id"])
        groups[find(bank_id)].append(bank_id)

    for bank in data["banks"]:
        bank_id = int(bank["bank_id"])
        display = norm_text(bank.get("display_name"))
        reasons: list[str] = []
        if JUNK_ENTITY_RE.search(display):
            reasons.append("junk_or_non_bank_name")
        if not (has_chinese(display) or LATIN_RE.search(display)):
            reasons.append("no_readable_entity_name")
        if bank.get("registry_status") == "provisional":
            reasons.append("provisional_identity")
        group = groups.get(find(bank_id), [])
        if len(group) > 1:
            reasons.append("possible_duplicate_name")
        if reasons:
            flagged += 1
        action = "keep"
        if "junk_or_non_bank_name" in reasons or "no_readable_entity_name" in reasons:
            action = "exclude_or_reclassify"
        elif "possible_duplicate_name" in reasons:
            action = "review_merge"
        elif "provisional_identity" in reasons:
            action = "review_identity"
        rows_out.append({
            "bank_id": bank_id,
            "bank_key": bank.get("bank_key") or "",
            "display_name": display,
            "canonical_zh": norm_text(bank.get("canonical_zh")),
            "canonical_pt": norm_text(bank.get("canonical_pt")),
            "entity_type": bank.get("entity_type") or "",
            "registry_status": bank.get("registry_status") or "",
            "statements": statements_by_bank.get(bank_id, 0),
            "rows": rows_by_bank.get(bank_id, 0),
            "aliases": alias_count.get(bank_id, 0),
            "duplicate_group": find(bank_id) if len(group) > 1 else "",
            "flag": ";".join(reasons),
            "suggested_action": action,
            "note": bank.get("notes") or "",
        })

    return rows_out, {
        "banks": len(data["banks"]),
        "flagged_banks": flagged,
        "serious_banks": sum(
            1 for row in rows_out
            if "junk_or_non_bank_name" in row["flag"] or "possible_duplicate_name" in row["flag"]
        ),
        "duplicate_groups": sum(1 for group in groups.values() if len(group) > 1),
        "junk_or_non_bank": sum(1 for row in rows_out if "junk_or_non_bank_name" in row["flag"]),
        "provisional": sum(1 for row in rows_out if "provisional_identity" in row["flag"]),
    }


def statement_anomalies(data: dict[str, Any], entity_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bank_flag = {int(row["bank_id"]): row for row in entity_rows}
    row_counts: Counter[str] = Counter()
    mapped_counts: Counter[str] = Counter()
    missing_labels: Counter[str] = Counter()
    duplicate_identity: Counter[tuple[str, str, str]] = Counter()

    for item in data["line_items"]:
        statement_id = item.get("statement_id") or ""
        row_counts[statement_id] += 1
        if item.get("user_standard_label_id") is not None or item.get("line_item_concept_id") is not None or item.get("label_standard_id"):
            mapped_counts[statement_id] += 1
        if not norm_text(item.get("label_standard")):
            missing_labels[statement_id] += 1
    for statement in data["statements"]:
        duplicate_identity[(
            statement.get("bank_key") or "",
            statement.get("statement_date") or "",
            statement.get("publication_date") or "",
        )] += 1

    output: list[dict[str, Any]] = []
    for statement in data["statements"]:
        statement_id = statement["statement_id"]
        bank_id = statement.get("bank_id")
        flags: list[str] = []
        bank = bank_flag.get(int(bank_id)) if bank_id is not None else None
        if bank and bank.get("flag"):
            flags.append("bank_identity:" + bank["flag"])
        if statement.get("identity_status") != "complete":
            flags.append("incomplete_identity")
        count = row_counts.get(statement_id, 0)
        if count == 0:
            flags.append("no_rows")
        if count > 5 and mapped_counts.get(statement_id, 0) / count < 0.2:
            flags.append("low_mapping_ratio")
        if int(statement.get("is_published") or 0) == 1:
            flags.append("legacy_published_unverified")
        key = (
            statement.get("bank_key") or "",
            statement.get("statement_date") or "",
            statement.get("publication_date") or "",
        )
        if duplicate_identity[key] > 1:
            flags.append("duplicate_statement_identity")
        if int(statement.get("page_count") or 0) <= 0:
            flags.append("no_page_count")
        try:
            pages = [part for part in re.split(r"[,;]", statement.get("pdf_pages") or "") if part.strip()]
            if statement.get("page_count") and len(pages) != int(statement["page_count"]):
                flags.append("page_count_mismatch")
        except (TypeError, ValueError):
            flags.append("pdf_pages_unparseable")
        output.append({
            "statement_id": statement_id,
            "run_id": statement.get("run_id") or "",
            "app_version": statement.get("app_version") or "",
            "is_published": int(statement.get("is_published") or 0),
            "bank_id": bank_id if bank_id is not None else "",
            "bank_key": statement.get("bank_key") or "",
            "bank_name_raw": norm_text(statement.get("bank_name_raw")),
            "publication_date": statement.get("publication_date") or "",
            "statement_date": statement.get("statement_date") or "",
            "statement_date_source": statement.get("statement_date_source") or "",
            "publication_date_source": statement.get("publication_date_source") or "",
            "identity_status": statement.get("identity_status") or "",
            "page_count": statement.get("page_count") or 0,
            "pdf_pages": statement.get("pdf_pages") or "",
            "row_count": count,
            "mapped_rows": mapped_counts.get(statement_id, 0),
            "missing_label_rows": missing_labels.get(statement_id, 0),
            "flag": ";".join(flags),
            "suggested_action": "review_statement" if flags else "keep",
        })
    return output


def page_anomalies(data: dict[str, Any], statement_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    statement_flag = {row["statement_id"]: row for row in statement_rows}
    output: list[dict[str, Any]] = []
    for page in data["pages"]:
        flags: list[str] = []
        if page.get("ocr_status") != "completed":
            flags.append("ocr_not_completed")
        if page.get("number_format_detected") in ("ambiguous", "dot_comma"):
            flags.append("number_format_review")
        if not norm_text(page.get("document_json")):
            flags.append("missing_document_json")
        if page.get("statement_id") and statement_flag.get(page["statement_id"], {}).get("flag"):
            flags.append("statement_anomaly")
        image_path = norm_text(page.get("image_path"))
        if image_path and not Path(image_path).exists():
            flags.append("image_missing")
        output.append({
            "page_id": page["page_id"],
            "run_id": page.get("run_id") or "",
            "app_version": page.get("app_version") or "",
            "pdf_page": page.get("pdf_page"),
            "printed_page": page.get("printed_page") or "",
            "statement_id": page.get("statement_id") or "",
            "bank_name_raw": norm_text(page.get("bank_name_raw")),
            "ocr_status": page.get("ocr_status") or "",
            "number_format_detected": page.get("number_format_detected") or "",
            "number_format_confidence": page.get("number_format_confidence") or "",
            "image_path": image_path,
            "flag": ";".join(flags),
        })
    return output


# ---------------------------------------------------------------------------
# Amount quality
# ---------------------------------------------------------------------------

def amount_quality(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    statement_info = {row["statement_id"]: row for row in data["statements"]}
    per_statement: dict[str, Counter[str]] = defaultdict(Counter)
    mismatches: list[dict[str, Any]] = []
    format_counts: Counter[str] = Counter()

    for item in data["line_items"]:
        statement_id = item.get("statement_id") or ""
        counts = per_statement[statement_id]
        counts["rows"] += 1
        format_counts[item.get("number_format") or "(blank)"] += 1

        for side in ("debit", "credit"):
            raw = item.get(f"{side}_raw") or ""
            amount = decimal_or_none(item.get(f"{side}_amount"))
            cents = item.get(f"{side}_cents")
            status = item.get(f"{side}_parse_status") or ""
            if not norm_text(raw):
                counts[f"{side}_blank"] += 1
            elif DASH_RE.match(norm_text(raw)):
                counts[f"{side}_dash"] += 1
            elif amount is None:
                counts[f"{side}_unparsed"] += 1
            else:
                counts[f"{side}_amount"] += 1
                if amount < 0:
                    counts[f"{side}_negative"] += 1
            if status == "parsed":
                counts[f"{side}_status_parsed"] += 1
            elif status and status not in ("blank", "dash"):
                counts[f"{side}_status_other"] += 1
            if amount is not None and cents is None:
                counts[f"{side}_cents_missing"] += 1
            if amount is not None and cents is not None:
                try:
                    expected = int((amount * 100).to_integral_value())
                    if int(cents) != expected:
                        counts[f"{side}_cents_mismatch"] += 1
                        mismatches.append({
                            "line_item_id": item["line_item_id"],
                            "statement_id": statement_id,
                            "side": side,
                            "raw": raw,
                            "amount": str(amount),
                            "cents": cents,
                            "expected_cents": expected,
                            "reason": "cents_mismatch",
                        })
                except (InvalidOperation, ValueError):
                    pass
            if status == "parsed" and amount is None:
                mismatches.append({
                    "line_item_id": item["line_item_id"],
                    "statement_id": statement_id,
                    "side": side,
                    "raw": raw,
                    "amount": "",
                    "cents": cents,
                    "expected_cents": "",
                    "reason": "parsed_but_no_amount",
                })

        if norm_text(item.get("debit_raw")) and norm_text(item.get("credit_raw")):
            counts["both_raw"] += 1
        if decimal_or_none(item.get("debit_amount")) is not None and decimal_or_none(item.get("credit_amount")) is not None:
            counts["both_amount"] += 1
        if not norm_text(item.get("label_standard")):
            counts["amount_without_label"] += 1

    output: list[dict[str, Any]] = []
    for statement in data["statements"]:
        statement_id = statement["statement_id"]
        counts = per_statement.get(statement_id, Counter())
        output.append({
            "statement_id": statement_id,
            "run_id": statement.get("run_id") or "",
            "app_version": statement.get("app_version") or "",
            "bank_name_raw": norm_text(statement.get("bank_name_raw")),
            "publication_date": statement.get("publication_date") or "",
            "statement_date": statement.get("statement_date") or "",
            "rows": counts.get("rows", 0),
            "debit_amount": counts.get("debit_amount", 0),
            "debit_blank": counts.get("debit_blank", 0),
            "debit_dash": counts.get("debit_dash", 0),
            "debit_unparsed": counts.get("debit_unparsed", 0),
            "debit_negative": counts.get("debit_negative", 0),
            "debit_cents_missing": counts.get("debit_cents_missing", 0),
            "debit_cents_mismatch": counts.get("debit_cents_mismatch", 0),
            "credit_amount": counts.get("credit_amount", 0),
            "credit_blank": counts.get("credit_blank", 0),
            "credit_dash": counts.get("credit_dash", 0),
            "credit_unparsed": counts.get("credit_unparsed", 0),
            "credit_negative": counts.get("credit_negative", 0),
            "credit_cents_missing": counts.get("credit_cents_missing", 0),
            "credit_cents_mismatch": counts.get("credit_cents_mismatch", 0),
            "both_raw": counts.get("both_raw", 0),
            "both_amount": counts.get("both_amount", 0),
            "amount_without_label": counts.get("amount_without_label", 0),
        })

    summary = {
        "number_format": dict(format_counts),
        "rows_with_amount_mismatch": len(mismatches),
    }
    return output, mismatches, summary


# ---------------------------------------------------------------------------
# Legacy crosswalk
# ---------------------------------------------------------------------------

def legacy_crosswalk(data: dict[str, Any]) -> list[dict[str, Any]]:
    lexicon_by_id = {
        norm_text(row.get("label_id")): row for row in data["label_lexicon"]
    }
    standards_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data["standards"]:
        for value in (row.get("canonical_zh"), row.get("canonical_pt")):
            text = norm_text(value)
            if text:
                standards_by_name[normalize_label_key(text, language_of(text))].append(row)

    usage: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, set[str]] = defaultdict(set)
    languages: dict[str, set[str]] = defaultdict(set)
    concepts: dict[str, set[str]] = defaultdict(set)
    users: dict[str, set[str]] = defaultdict(set)

    for item in data["line_items"]:
        legacy_id = norm_text(item.get("label_standard_id"))
        if not legacy_id:
            continue
        usage[legacy_id]["rows"] += 1
        if item.get("label_standard"):
            examples[legacy_id].add(norm_text(item["label_standard"]))
        if item.get("label_language"):
            languages[legacy_id].add(norm_text(item["label_language"]))
        if item.get("concept_key"):
            concepts[legacy_id].add(item["concept_key"])
        if item.get("user_standard_key"):
            users[legacy_id].add(item["user_standard_key"])

    output: list[dict[str, Any]] = []
    for legacy_id, counts in sorted(usage.items()):
        lex = lexicon_by_id.get(legacy_id)
        lex_std = norm_text(lex.get("label_std")) if lex else ""
        candidates = standards_by_name.get(
            normalize_label_key(lex_std, language_of(lex_std)), []
        ) if lex_std else []
        output.append({
            "legacy_id": legacy_id,
            "rows": counts["rows"],
            "legacy_labels": " | ".join(sorted(examples[legacy_id])),
            "languages": " | ".join(sorted(languages[legacy_id])),
            "label_lexicon_std": lex_std,
            "label_lexicon_language": norm_text(lex.get("language")) if lex else "",
            "label_lexicon_level": norm_text(lex.get("level")) if lex else "",
            "concept_keys": " | ".join(sorted(concepts[legacy_id])),
            "user_standard_keys": " | ".join(sorted(users[legacy_id])),
            "suggested_standard_keys": " | ".join(sorted({row["standard_key"] for row in candidates})),
            "suggested_action": "map_via_lexicon" if lex else "needs_review",
        })
    return output


# ---------------------------------------------------------------------------
# Artifact manifest / provenance
# ---------------------------------------------------------------------------

def artifact_manifest(db_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    runs = []
    mismatches: list[dict[str, Any]] = []
    for run in data["runs"]:
        run_dir = Path(run.get("run_dir") or "")
        files: list[dict[str, Any]] = []
        if run_dir.is_dir():
            for name in (
                "run_report.json",
                "tb_rows.csv",
                "documents.csv",
                "import_manifest.json",
                "review_queue.csv",
            ):
                path = run_dir / name
                if path.exists():
                    files.append({
                        "name": name,
                        "size": path.stat().st_size,
                        "sha256": sha256_file(path),
                    })
            images = run_dir / "images"
            image_count = len(list(images.glob("*"))) if images.is_dir() else 0
            image_bytes = sum(path.stat().st_size for path in images.glob("*")) if images.is_dir() else 0
        else:
            image_count = 0
            image_bytes = 0

        source_pdf = Path(run.get("source_pdf") or "")
        actual_hash = ""
        if source_pdf.is_file():
            actual_hash = sha256_file(source_pdf)
        expected_hash = run.get("source_pdf_sha256") or ""
        match = bool(actual_hash and expected_hash and actual_hash == expected_hash)
        if actual_hash and expected_hash and not match:
            mismatches.append({
                "run_id": run["run_id"],
                "source_pdf": str(source_pdf),
                "expected": expected_hash,
                "actual": actual_hash,
            })
        runs.append({
            "run_id": run["run_id"],
            "run_dir": str(run_dir),
            "app_version": run.get("app_version") or "",
            "is_published": int(run.get("is_published") or 0),
            "source_pdf": str(source_pdf),
            "source_pdf_sha256_expected": expected_hash,
            "source_pdf_sha256_actual": actual_hash,
            "source_pdf_hash_match": match,
            "files": files,
            "image_count": image_count,
            "image_bytes": image_bytes,
        })

    pdfs = []
    input_dir = ROOT / "input"
    if input_dir.is_dir():
        for path in sorted(input_dir.glob("*.pdf")):
            pdfs.append({
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })

    labels = []
    for path in sorted((ROOT / "labels").glob("*.csv")):
        labels.append({
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })

    return {
        "generated_at": now(),
        "database": {
            "path": str(db_path),
            "size": db_path.stat().st_size,
            "sha256": sha256_file(db_path),
        },
        "baseline_copy": {
            "path": str(ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite"),
            "sha256_file": str(ROOT / "data" / "baseline" / "baseline_v5_20261118.sha256"),
            "exists": (ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite").exists(),
        },
        "pdfs": pdfs,
        "labels": labels,
        "runs": runs,
        "provenance_mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _md_cell(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


def md_table(headers: list[str], rows: list[list[Any]], limit: int = 20) -> str:
    visible = rows[:limit]
    lines = [
        "| " + " | ".join(_md_cell(header) for header in headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in visible:
        lines.append("| " + " | ".join(_md_cell(value) for value in row) + " |")
    if len(rows) > limit:
        lines.append(f"| … | 另有 {len(rows) - limit} 項，詳見 CSV |")
    return "\n".join(lines)


def build_markdown(
    data: dict[str, Any],
    summary: dict[str, Any],
    reconciliation: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    entity_rows: list[dict[str, Any]],
    statement_rows: list[dict[str, Any]],
    page_rows: list[dict[str, Any]],
    amount_rows: list[dict[str, Any]],
    artifact: dict[str, Any],
) -> str:
    counts = data["counts"]
    lines: list[str] = []
    lines.append("# SQL Audit — Macau Balancete v5")
    lines.append("")
    lines.append(f"Generated: `{summary['generated_at']}`  ")
    lines.append(f"Database: `{summary['database']['path']}`  ")
    lines.append(f"SHA-256: `{summary['database']['sha256']}`")
    lines.append("")
    lines.append("> This is a read-only audit. It does not migrate, normalise or approve any mapping.")
    lines.append("")

    lines.append("## 1. Inventory")
    lines.append("")
    lines.append(md_table(
        ["Table / artifact", "Count"],
        [[key, value] for key, value in counts.items()],
        limit=50,
    ))
    lines.append("")

    lines.append("## 2. Registry completeness")
    lines.append("")
    std = summary["registries"]["standard_labels"]
    concept = summary["registries"]["concepts"]
    draft = summary["registries"]["draft"]
    lines.append(md_table(
        ["Registry", "Items", "Both ZH+PT", "ZH only", "PT only", "Neither", "Used rows"],
        [
            ["standard_labels (89 seed)", std["total"], std["both"], std["zh_only"], std["pt_only"], std["neither"], std["used_rows"]],
            ["line_item_concepts lex", concept["total"], concept["both"], concept["zh_only"], concept["pt_only"], concept["neither"], concept["used_rows"]],
            ["line_item_standard.csv", draft["total"], draft["both"], draft["zh_only"], draft["pt_only"], draft["neither"], 0],
        ],
        limit=10,
    ))
    lines.append("")
    lines.append(f"- `line_item_concepts` auto-discovery: **{concept['auto_total']}** items, **{concept['auto_used_rows']}** rows used; excluded from active catalogue.")
    lines.append(f"- `label_lexicon.csv`: **{summary['registries']['label_lexicon']['total']}** entries (legacy mapping IDs).")
    lines.append(f"- Reconciliation rows: **{len(reconciliation)}**; `merge_candidate`: **{summary['reconciliation']['merge_candidate']}**, `needs_review`: **{summary['reconciliation']['needs_review']}**, `exclude_archive`: **{summary['reconciliation']['exclude_archive']}**.")
    lines.append("")

    lines.append("## 3. Mapping coverage")
    lines.append("")
    lines.append(md_table(
        ["Run", "App", "Published", "Rows", "User mapped", "User %", "Concept mapped", "Legacy present", "Conflicts"],
        [
            [row["run_id"][:18] + "…", row["app_version"], row["is_published"], row["rows"], row["user_mapped"], row["user_mapped_pct"], row["concept_mapped"], row["legacy_present"], row["conflict_rows"]]
            for row in coverage
        ],
        limit=20,
    ))
    lines.append("")
    lines.append(f"- Exact conflict rows after name folding: **{summary['conflicts']['conflict_rows']}**.")
    lines.append(f"- Partial-overlap rows (substring/qualifier, e.g. `代收帳` vs `代收帳(貸方)`): **{summary['conflicts']['partial_overlap_rows']}**; these are review items, not agreements.")
    lines.append(f"- Raw concept-vs-user name mismatches before folding: **{summary['conflicts'].get('raw_concept_user_name_mismatch_rows', 0)}**.")
    lines.append(f"- Three-way exact conflict rows: **{summary['conflicts'].get('three_way_rows', 0)}**.")
    lines.append("")

    lines.append("## 4. Entity anomalies")
    lines.append("")
    entity_serious = [
        row for row in entity_rows
        if "junk_or_non_bank_name" in row["flag"] or "possible_duplicate_name" in row["flag"]
    ]
    lines.append(f"- Banks: **{summary['entities']['banks']}**; flagged: **{summary['entities']['flagged_banks']}**; serious (junk / duplicate): **{summary['entities']['serious_banks']}**.")
    lines.append("")
    lines.append(md_table(
        ["Bank ID", "Name", "Type", "Status", "Statements", "Rows", "Flag", "Suggested"],
        [
            [row["bank_id"], row["display_name"][:55], row["entity_type"], row["registry_status"], row["statements"], row["rows"], row["flag"], row["suggested_action"]]
            for row in entity_serious
        ],
        limit=25,
    ))
    lines.append("")

    lines.append("## 5. Statement anomalies")
    lines.append("")
    serious_statements = [
        row for row in statement_rows
        if any(
            flag not in ("legacy_published_unverified",)
            and flag != "bank_identity:provisional_identity"
            for flag in row["flag"].split(";")
            if flag
        )
    ]
    lines.append(f"- Statements: **{summary['statements']['total']}**; flagged: **{summary['statements']['flagged']}**; serious: **{summary['statements']['serious']}**; legacy published: **{summary['statements']['legacy_published']}**.")
    lines.append("")
    lines.append(md_table(
        ["Statement", "Bank", "Date", "Rows", "Mapped", "Published", "Flag"],
        [
            [row["statement_id"][:24] + "…", row["bank_name_raw"][:35], row["statement_date"], row["row_count"], row["mapped_rows"], row["is_published"], row["flag"]]
            for row in serious_statements
        ],
        limit=25,
    ))
    lines.append("")

    lines.append("## 6. Page anomalies")
    lines.append("")
    serious_pages = [
        row for row in page_rows
        if any(
            flag in ("ocr_not_completed", "number_format_review", "image_missing", "missing_document_json")
            for flag in row["flag"].split(";")
        )
    ]
    lines.append(f"- Pages: **{summary['pages']['total']}**; flagged: **{summary['pages']['flagged']}**; serious (OCR/format/image): **{summary['pages']['serious']}**.")
    lines.append("")
    lines.append(md_table(
        ["Page", "Run", "PDF page", "Bank", "OCR", "Number format", "Flag"],
        [
            [row["page_id"], row["run_id"][:16] + "…", row["pdf_page"], row["bank_name_raw"][:30], row["ocr_status"], row["number_format_detected"], row["flag"]]
            for row in serious_pages
        ],
        limit=25,
    ))
    lines.append("")

    lines.append("## 7. Amount quality")
    lines.append("")
    lines.append(md_table(
        ["Statement", "Bank", "Rows", "Debit parsed", "Credit parsed", "Both columns", "No label", "Cents mismatch"],
        [
            [row["statement_id"][:24] + "…", row["bank_name_raw"][:35], row["rows"], row["debit_amount"], row["credit_amount"], row["both_amount"], row["amount_without_label"], row["debit_cents_mismatch"] + row["credit_cents_mismatch"]]
            for row in amount_rows
        ],
        limit=20,
    ))
    lines.append("")
    lines.append(f"- Number formats: `{summary['amounts']['number_format']}`.")
    lines.append(f"- Amount mismatch rows: **{summary['amounts']['rows_with_amount_mismatch']}** (see `amount_mismatches.csv`).")
    lines.append("")

    lines.append("## 8. Provenance")
    lines.append("")
    lines.append(f"- PDFs hashed: **{len(artifact['pdfs'])}**.")
    lines.append(f"- Runs with source PDF hash recorded: **{sum(1 for row in artifact['runs'] if row['source_pdf_sha256_expected'])}** / {len(artifact['runs'])}.")
    lines.append(f"- PDF hash mismatches: **{len(artifact['provenance_mismatches'])}**.")
    lines.append(f"- Baseline copy exists: **{artifact['baseline_copy']['exists']}**.")
    lines.append("")

    lines.append("## 9. Immediate conclusions")
    lines.append("")
    lines.append("1. The v5 database has no human verification rows; legacy publication is not financial verification.")
    lines.append("2. `standard_labels` is a curated seed, not a complete bilingual catalogue.")
    lines.append("3. `line_item_concepts` is a monolingual lexicon plus unused auto-discovery noise, not a second bilingual catalogue.")
    lines.append("4. `label_standard_id` points to `labels/label_lexicon.csv`, not a database table; the crosswalk must keep it.")
    lines.append("5. Rebuild is safer than in-place migration, but replay must preserve unknown parent context and legacy IDs.")
    lines.append("")
    lines.append("## 10. Output files")
    lines.append("")
    for name in summary["output_files"]:
        lines.append(f"- `{name}`")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "archive" / "database" / "macau_balancete.sqlite")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "audit")
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    out_dir = args.out.expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"database not found: {db_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = connect_ro(db_path)
    try:
        data = load_data(conn)
    finally:
        conn.close()

    reconciliation, inventory = reconcile(data)
    conflicts, partial_conflicts, conflict_counts, conflict_ids = mapping_conflicts(data)
    coverage, coverage_by_language = mapping_coverage(data, conflict_ids)
    entity_rows, entity_summary = entity_anomalies(data)
    statement_rows = statement_anomalies(data, entity_rows)
    page_rows = page_anomalies(data, statement_rows)
    amount_rows, amount_mismatches, amount_summary = amount_quality(data)
    crosswalk = legacy_crosswalk(data)
    artifact = artifact_manifest(db_path, data)

    standard_both = sum(1 for row in data["standards"] if norm_text(row.get("canonical_zh")) and norm_text(row.get("canonical_pt")))
    standard_zh_only = sum(1 for row in data["standards"] if norm_text(row.get("canonical_zh")) and not norm_text(row.get("canonical_pt")))
    standard_pt_only = sum(1 for row in data["standards"] if norm_text(row.get("canonical_pt")) and not norm_text(row.get("canonical_zh")))
    standard_neither = len(data["standards"]) - standard_both - standard_zh_only - standard_pt_only

    lex_concepts = [row for row in data["concepts"] if not str(row.get("concept_key") or "").startswith("auto:")]
    auto_concepts = [row for row in data["concepts"] if str(row.get("concept_key") or "").startswith("auto:")]
    concept_both = sum(1 for row in lex_concepts if norm_text(row.get("canonical_zh")) and norm_text(row.get("canonical_pt")))
    concept_zh_only = sum(1 for row in lex_concepts if norm_text(row.get("canonical_zh")) and not norm_text(row.get("canonical_pt")))
    concept_pt_only = sum(1 for row in lex_concepts if norm_text(row.get("canonical_pt")) and not norm_text(row.get("canonical_zh")))
    concept_neither = len(lex_concepts) - concept_both - concept_zh_only - concept_pt_only

    draft_both = sum(1 for row in data["draft"] if norm_text(row.get("canonical_zh")) and norm_text(row.get("canonical_pt")))
    draft_zh_only = sum(1 for row in data["draft"] if norm_text(row.get("canonical_zh")) and not norm_text(row.get("canonical_pt")))
    draft_pt_only = sum(1 for row in data["draft"] if norm_text(row.get("canonical_pt")) and not norm_text(row.get("canonical_zh")))
    draft_neither = len(data["draft"]) - draft_both - draft_zh_only - draft_pt_only

    action_counts = Counter(row["suggested_action"] for row in reconciliation)
    summary = {
        "generated_at": now(),
        "database": {
            "path": str(db_path),
            "size": db_path.stat().st_size,
            "sha256": sha256_file(db_path),
            "schema_meta": data["schema_meta"],
        },
        "counts": data["counts"],
        "registries": {
            "standard_labels": {
                "total": len(data["standards"]),
                "both": standard_both,
                "zh_only": standard_zh_only,
                "pt_only": standard_pt_only,
                "neither": standard_neither,
                "used_rows": sum(int(row.get("usage_rows") or 0) for row in data["standards"]),
            },
            "concepts": {
                "total": len(lex_concepts),
                "both": concept_both,
                "zh_only": concept_zh_only,
                "pt_only": concept_pt_only,
                "neither": concept_neither,
                "used_rows": sum(int(row.get("usage_rows") or 0) for row in lex_concepts),
                "auto_total": len(auto_concepts),
                "auto_used_rows": sum(int(row.get("usage_rows") or 0) for row in auto_concepts),
            },
            "draft": {
                "total": len(data["draft"]),
                "both": draft_both,
                "zh_only": draft_zh_only,
                "pt_only": draft_pt_only,
                "neither": draft_neither,
            },
            "label_lexicon": {"total": len(data["label_lexicon"])},
        },
        "reconciliation": {
            "rows": len(reconciliation),
            **{name: action_counts.get(name, 0) for name in sorted(action_counts)},
        },
        "conflicts": {
            **conflict_counts,
            "conflict_rows": len(conflicts),
            "partial_overlap_rows": len(partial_conflicts),
            "review_rows": len(conflicts) + len(partial_conflicts),
        },
        "entities": entity_summary,
        "statements": {
            "total": len(statement_rows),
            "flagged": sum(1 for row in statement_rows if row["flag"]),
            "serious": sum(
                1 for row in statement_rows
                if any(
                    flag not in ("legacy_published_unverified",)
                    and flag != "bank_identity:provisional_identity"
                    for flag in row["flag"].split(";")
                    if flag
                )
            ),
            "legacy_published": sum(1 for row in statement_rows if "legacy_published_unverified" in row["flag"]),
        },
        "pages": {
            "total": len(page_rows),
            "flagged": sum(1 for row in page_rows if row["flag"]),
            "serious": sum(
                1 for row in page_rows
                if any(flag in ("ocr_not_completed", "number_format_review", "image_missing", "missing_document_json") for flag in row["flag"].split(";"))
            ),
        },
        "amounts": amount_summary,
        "provenance": {
            "pdfs": len(artifact["pdfs"]),
            "runs": len(artifact["runs"]),
            "hash_mismatches": len(artifact["provenance_mismatches"]),
        },
        "output_files": [],
    }

    files = {
        "SQL_AUDIT_V5.json": None,
        "SQL_AUDIT_V5.md": None,
        "registry_inventory.csv": (inventory, [
            "source", "source_id", "source_file", "item_key", "canonical_zh",
            "canonical_pt", "canonical_en", "parent_key", "level", "status",
            "category", "usage_rows", "alias_count", "merge_keys", "note",
        ]),
        "standard_item_reconciliation.csv": (reconciliation, [
            "source", "source_id", "source_file", "item_key", "canonical_zh",
            "canonical_pt", "canonical_en", "parent_key", "level", "status",
            "category", "usage_rows", "merge_keys", "alias_count",
            "suggested_match_1", "suggested_match_2", "suggested_match_3",
            "match_basis", "match_score", "ambiguity", "suggested_action",
            "decision", "note",
        ]),
        "mapping_conflicts.csv": (conflicts, [
            "line_item_id", "statement_id", "run_id", "row_order", "label_raw",
            "label_language", "legacy_label", "legacy_id", "legacy_status",
            "concept_key", "concept_zh", "concept_pt", "user_standard_key",
            "user_zh", "user_pt", "user_match_status", "conflict_pairs",
            "partial_pairs", "evidence_sources", "mapping_status",
        ]),
        "mapping_partial_overlaps.csv": (partial_conflicts, [
            "line_item_id", "statement_id", "run_id", "row_order", "label_raw",
            "label_language", "legacy_label", "legacy_id", "legacy_status",
            "concept_key", "concept_zh", "concept_pt", "user_standard_key",
            "user_zh", "user_pt", "user_match_status", "conflict_pairs",
            "partial_pairs", "evidence_sources", "mapping_status",
        ]),
        "mapping_coverage_by_run.csv": (coverage, [
            "run_id", "run_dir", "app_version", "is_published",
            "source_pdf_sha256", "rows", "user_mapped", "user_unmapped",
            "user_mapped_pct", "concept_mapped", "concept_unmapped",
            "legacy_present", "legacy_missing", "conflict_rows",
            "conflict_pct", "no_standard_label",
        ]),
        "mapping_coverage_by_language.csv": (coverage_by_language, [
            "label_language", "rows", "user_mapped", "user_unmapped",
            "user_mapped_pct", "concept_mapped", "concept_unmapped",
            "legacy_present", "legacy_missing", "conflict_rows",
            "no_standard_label",
        ]),
        "entity_anomalies.csv": (entity_rows, [
            "bank_id", "bank_key", "display_name", "canonical_zh",
            "canonical_pt", "entity_type", "registry_status", "statements",
            "rows", "aliases", "duplicate_group", "flag", "suggested_action",
            "note",
        ]),
        "statement_anomalies.csv": (statement_rows, [
            "statement_id", "run_id", "app_version", "is_published", "bank_id",
            "bank_key", "bank_name_raw", "publication_date", "statement_date",
            "statement_date_source", "publication_date_source", "identity_status",
            "page_count", "pdf_pages", "row_count", "mapped_rows",
            "missing_label_rows", "flag", "suggested_action",
        ]),
        "page_anomalies.csv": (page_rows, [
            "page_id", "run_id", "app_version", "pdf_page", "printed_page",
            "statement_id", "bank_name_raw", "ocr_status",
            "number_format_detected", "number_format_confidence", "image_path",
            "flag",
        ]),
        "amount_quality.csv": (amount_rows, [
            "statement_id", "run_id", "app_version", "bank_name_raw",
            "publication_date", "statement_date", "rows", "debit_amount",
            "debit_blank", "debit_dash", "debit_unparsed", "debit_negative",
            "debit_cents_missing", "debit_cents_mismatch", "credit_amount",
            "credit_blank", "credit_dash", "credit_unparsed", "credit_negative",
            "credit_cents_missing", "credit_cents_mismatch", "both_raw",
            "both_amount", "amount_without_label",
        ]),
        "amount_mismatches.csv": (amount_mismatches, [
            "line_item_id", "statement_id", "side", "raw", "amount", "cents",
            "expected_cents", "reason",
        ]),
        "legacy_id_crosswalk.csv": (crosswalk, [
            "legacy_id", "rows", "legacy_labels", "languages",
            "label_lexicon_std", "label_lexicon_language", "label_lexicon_level",
            "concept_keys", "user_standard_keys", "suggested_standard_keys",
            "suggested_action",
        ]),
        "artifact_manifest.json": None,
    }

    for name, payload in files.items():
        path = out_dir / name
        if name == "SQL_AUDIT_V5.json":
            continue
        if name == "SQL_AUDIT_V5.md":
            continue
        if name == "artifact_manifest.json":
            write_json(path, artifact)
        else:
            rows_out, fields = payload  # type: ignore[misc]
            write_csv(path, rows_out, fields)

    markdown = build_markdown(
        data, summary, reconciliation, conflicts, coverage, entity_rows,
        statement_rows, page_rows, amount_rows, artifact,
    )
    (out_dir / "SQL_AUDIT_V5.md").write_text(markdown + "\n", encoding="utf-8")

    summary["output_files"] = sorted(set(files.keys()) | {"SQL_AUDIT_V5.md", "SQL_AUDIT_V5.json"})
    write_json(out_dir / "SQL_AUDIT_V5.json", summary)

    print(json.dumps({
        "out": str(out_dir),
        "counts": data["counts"],
        "reconciliation": summary["reconciliation"],
        "conflicts": summary["conflicts"],
        "flagged_banks": entity_summary["flagged_banks"],
        "flagged_statements": summary["statements"]["flagged"],
        "amount_mismatches": len(amount_mismatches),
        "provenance_mismatches": len(artifact["provenance_mismatches"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
