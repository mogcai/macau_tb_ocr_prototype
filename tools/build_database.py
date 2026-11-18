#!/usr/bin/env python3
"""Build a SQLite database from one or more OCR run directories.

The database keeps every OCR observation.  It does not merge runs silently:
each run gets its own ``statement`` rows, and the ``v_current_*`` views choose
the newest import for the same publication/date/bank paper key.

Usage::

    python tools/build_database.py \
        output_v08_issue45_2026 output_v06_issue44_2026 \
        --db data/macau_balancete.sqlite

The proposed business key is::

    publication_date, statement_date, bank, parent_label_standard,
    label_standard

It is *not* unique in real runs: labels repeat, rows can have no recognised
label, and one bank can use several pages.  This tool stores the stronger
``statement_id + label_path + occurrence`` identity and exposes the five-column
key through ``v_natural_key_duplicates`` for review.
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
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

APP_VERSION = "0.4.2"
SCHEMA_VERSION = 3
CJK_RE = re.compile(r"[\u3400-\u9fff]")

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_dir TEXT NOT NULL,
    source_pdf TEXT,
    source_pdf_sha256 TEXT,
    source_pdf_sha256_source TEXT,
    app_version TEXT,
    imported_at TEXT NOT NULL,
    tb_page_count INTEGER NOT NULL DEFAULT 0,
    line_item_count INTEGER NOT NULL DEFAULT 0,
    review_row_count INTEGER NOT NULL DEFAULT 0,
    is_published INTEGER NOT NULL DEFAULT 0
        CHECK (is_published IN (0, 1)),
    published_at TEXT,
    published_by TEXT,
    config_json TEXT,
    run_report_json TEXT
);

CREATE TABLE IF NOT EXISTS banks (
    bank_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    first_seen_run TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS bank_aliases (
    alias_key TEXT PRIMARY KEY,
    bank_key TEXT NOT NULL,
    alias_name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS statements (
    statement_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    paper_key TEXT NOT NULL,
    publication_date TEXT,
    publication_date_source TEXT NOT NULL,
    statement_date TEXT,
    statement_date_source TEXT NOT NULL,
    bank_id INTEGER REFERENCES banks(bank_id),
    bank_key TEXT,
    bank_name_raw TEXT,
    bank_name_alternatives TEXT,
    bank_name_source TEXT,
    pdf_pages TEXT NOT NULL,
    printed_pages TEXT,
    page_count INTEGER NOT NULL,
    metadata_status TEXT,
    identity_status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    review_row_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (run_id, paper_key)
);

CREATE TABLE IF NOT EXISTS pages (
    page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    pdf_page INTEGER NOT NULL,
    printed_page TEXT,
    statement_id TEXT REFERENCES statements(statement_id) ON DELETE CASCADE,
    bank_name_raw TEXT,
    publication_date TEXT,
    statement_date TEXT,
    metadata_status TEXT,
    ocr_status TEXT,
    ocr_languages TEXT,
    image_path TEXT,
    image_width INTEGER,
    image_height INTEGER,
    number_format_detected TEXT,
    number_format_confidence REAL,
    document_json TEXT,
    UNIQUE (run_id, pdf_page)
);

CREATE INDEX IF NOT EXISTS idx_pages_statement
    ON pages (statement_id);

CREATE TABLE IF NOT EXISTS line_items (
    line_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id TEXT NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    row_order INTEGER NOT NULL,
    pdf_page INTEGER NOT NULL,
    printed_page TEXT,
    bank_name_raw TEXT,
    label_raw TEXT NOT NULL DEFAULT '',
    label_normalized TEXT,
    label_standard TEXT,
    label_standard_id TEXT,
    label_standard_status TEXT,
    label_standard_score REAL,
    label_role TEXT,
    parent_label_raw TEXT,
    parent_label_standard TEXT,
    label_path TEXT,
    label_language TEXT,
    label_path_key TEXT NOT NULL DEFAULT '',
    occurrence INTEGER NOT NULL,
    natural_key TEXT NOT NULL,
    natural_key_status TEXT NOT NULL
        CHECK (natural_key_status IN ('complete', 'duplicate', 'missing_label')),
    debit_raw TEXT,
    credit_raw TEXT,
    debit_amount TEXT,
    credit_amount TEXT,
    debit_cents INTEGER,
    credit_cents INTEGER,
    number_format TEXT,
    number_format_confidence REAL,
    debit_parse_status TEXT,
    credit_parse_status TEXT,
    review_reason TEXT,
    review_status TEXT,
    row_confidence REAL,
    row_average_confidence REAL,
    source_image TEXT,
    bbox_x0 REAL,
    bbox_y0 REAL,
    bbox_x1 REAL,
    bbox_y1 REAL,
    UNIQUE (statement_id, row_order)
);

CREATE TABLE IF NOT EXISTS row_verification (
    line_item_id INTEGER PRIMARY KEY
        REFERENCES line_items(line_item_id) ON DELETE CASCADE,
    status TEXT NOT NULL
        CHECK (status IN ('verified', 'rejected', 'pending')),
    verified_at TEXT,
    verified_by TEXT,
    note TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_line_items_natural
    ON line_items (statement_id, label_path_key, occurrence);
CREATE INDEX IF NOT EXISTS idx_line_items_statement
    ON line_items (statement_id);
CREATE INDEX IF NOT EXISTS idx_line_items_label
    ON line_items (label_standard);
CREATE INDEX IF NOT EXISTS idx_line_items_parent
    ON line_items (parent_label_standard);
CREATE INDEX IF NOT EXISTS idx_statements_paper_key
    ON statements (paper_key);

-- Existing databases cannot gain CHECK constraints through ALTER TABLE, so
-- validate the most safety-critical enums with triggers as well.
CREATE TRIGGER IF NOT EXISTS trg_runs_is_published_insert
BEFORE INSERT ON runs
WHEN NEW.is_published NOT IN (0, 1)
BEGIN
    SELECT RAISE(ABORT, 'runs.is_published must be 0 or 1');
END;

CREATE TRIGGER IF NOT EXISTS trg_runs_is_published_update
BEFORE UPDATE OF is_published ON runs
WHEN NEW.is_published NOT IN (0, 1)
BEGIN
    SELECT RAISE(ABORT, 'runs.is_published must be 0 or 1');
END;

CREATE TRIGGER IF NOT EXISTS trg_runs_hash_source_insert
BEFORE INSERT ON runs
WHEN NEW.source_pdf_sha256_source IS NOT NULL
 AND NEW.source_pdf_sha256_source NOT IN ('ocr_report', 'import_time', 'missing')
BEGIN
    SELECT RAISE(ABORT, 'invalid runs.source_pdf_sha256_source');
END;

CREATE TRIGGER IF NOT EXISTS trg_line_items_natural_status_insert
BEFORE INSERT ON line_items
WHEN NEW.natural_key_status NOT IN ('complete', 'duplicate', 'missing_label')
BEGIN
    SELECT RAISE(ABORT, 'invalid line_items.natural_key_status');
END;

CREATE TRIGGER IF NOT EXISTS trg_line_items_natural_status_update
BEFORE UPDATE OF natural_key_status ON line_items
WHEN NEW.natural_key_status NOT IN ('complete', 'duplicate', 'missing_label')
BEGIN
    SELECT RAISE(ABORT, 'invalid line_items.natural_key_status');
END;
"""

VIEWS_SQL = """
DROP VIEW IF EXISTS v_current_statements;
CREATE VIEW v_current_statements AS
SELECT *
FROM (
    SELECT s.*,
           ROW_NUMBER() OVER (
               PARTITION BY s.paper_key
               ORDER BY r.is_published DESC, r.imported_at DESC, r.rowid DESC
           ) AS current_rank
    FROM statements s
    JOIN runs r ON r.run_id = s.run_id
)
WHERE current_rank = 1;

DROP VIEW IF EXISTS v_published_statements;
CREATE VIEW v_published_statements AS
SELECT *
FROM (
    SELECT s.*,
           ROW_NUMBER() OVER (
               PARTITION BY s.paper_key
               ORDER BY r.imported_at DESC, r.rowid DESC
           ) AS published_rank
    FROM statements s
    JOIN runs r ON r.run_id = s.run_id
    WHERE r.is_published = 1
)
WHERE published_rank = 1;

DROP VIEW IF EXISTS v_current_line_items;
CREATE VIEW v_current_line_items AS
SELECT li.*,
       s.paper_key,
       s.publication_date,
       s.publication_date_source,
       s.statement_date,
       s.statement_date_source,
       s.bank_id,
       s.bank_key,
       b.display_name AS bank_name,
       s.identity_status,
       s.metadata_status
FROM line_items li
JOIN v_current_statements s ON s.statement_id = li.statement_id
LEFT JOIN banks b ON b.bank_id = s.bank_id;

DROP VIEW IF EXISTS v_published_line_items;
CREATE VIEW v_published_line_items AS
SELECT li.*,
       s.paper_key,
       s.publication_date,
       s.publication_date_source,
       s.statement_date,
       s.statement_date_source,
       s.bank_id,
       s.bank_key,
       b.display_name AS bank_name,
       s.identity_status,
       s.metadata_status
FROM line_items li
JOIN v_published_statements s ON s.statement_id = li.statement_id
LEFT JOIN banks b ON b.bank_id = s.bank_id;

DROP VIEW IF EXISTS v_all_line_items;
CREATE VIEW v_all_line_items AS
SELECT li.*,
       s.paper_key,
       s.publication_date,
       s.publication_date_source,
       s.statement_date,
       s.statement_date_source,
       s.bank_id,
       s.bank_key,
       b.display_name AS bank_name,
       s.identity_status,
       s.metadata_status
FROM line_items li
JOIN statements s ON s.statement_id = li.statement_id
LEFT JOIN banks b ON b.bank_id = s.bank_id;

DROP VIEW IF EXISTS v_balance_sheet;
CREATE VIEW v_balance_sheet AS
SELECT publication_date,
       statement_date,
       bank_name,
       bank_key,
       parent_label_standard,
       label_standard,
       label_path,
       occurrence,
       label_standard_id,
       label_standard_status,
       debit_amount,
       credit_amount,
       debit_cents,
       credit_cents,
       debit_raw,
       credit_raw,
       number_format,
       debit_parse_status,
       credit_parse_status,
       review_status,
       review_reason,
       natural_key_status,
       natural_key,
       COALESCE(rv.status, 'unverified') AS verification_status,
       li.statement_id,
       li.line_item_id,
       li.pdf_page,
       li.row_order,
       li.source_image
FROM v_current_line_items li
LEFT JOIN row_verification rv ON rv.line_item_id = li.line_item_id;

DROP VIEW IF EXISTS v_published_balance_sheet;
CREATE VIEW v_published_balance_sheet AS
SELECT publication_date,
       statement_date,
       bank_name,
       bank_key,
       parent_label_standard,
       label_standard,
       label_path,
       occurrence,
       label_standard_id,
       label_standard_status,
       debit_amount,
       credit_amount,
       debit_cents,
       credit_cents,
       debit_raw,
       credit_raw,
       number_format,
       debit_parse_status,
       credit_parse_status,
       review_status,
       review_reason,
       natural_key_status,
       natural_key,
       COALESCE(rv.status, 'unverified') AS verification_status,
       li.statement_id,
       li.line_item_id,
       li.pdf_page,
       li.row_order,
       li.source_image
FROM v_published_line_items li
LEFT JOIN row_verification rv ON rv.line_item_id = li.line_item_id;

DROP VIEW IF EXISTS v_review_queue;
CREATE VIEW v_review_queue AS
SELECT li.*,
       COALESCE(rv.status, 'unverified') AS verification_status
FROM v_current_line_items li
LEFT JOIN row_verification rv ON rv.line_item_id = li.line_item_id
WHERE li.review_status = 'needs_review'
  AND COALESCE(rv.status, '') <> 'verified';

DROP VIEW IF EXISTS v_published_review_queue;
CREATE VIEW v_published_review_queue AS
SELECT li.*,
       COALESCE(rv.status, 'unverified') AS verification_status
FROM v_published_line_items li
LEFT JOIN row_verification rv ON rv.line_item_id = li.line_item_id
WHERE li.review_status = 'needs_review'
  AND COALESCE(rv.status, '') <> 'verified';

DROP VIEW IF EXISTS v_page_index;
CREATE VIEW v_page_index AS
SELECT p.page_id,
       p.run_id,
       p.pdf_page,
       p.printed_page,
       p.statement_id,
       s.paper_key,
       p.bank_name_raw,
       b.display_name AS bank_name,
       p.publication_date,
       p.statement_date,
       p.metadata_status,
       p.ocr_status,
       p.ocr_languages,
       p.image_path,
       p.image_width,
       p.image_height,
       p.number_format_detected,
       p.number_format_confidence,
       r.is_published
FROM pages p
LEFT JOIN statements s ON s.statement_id = p.statement_id
LEFT JOIN banks b ON b.bank_id = s.bank_id
LEFT JOIN runs r ON r.run_id = p.run_id;

DROP VIEW IF EXISTS v_run_index;
CREATE VIEW v_run_index AS
SELECT run_id,
       run_dir,
       source_pdf,
       source_pdf_sha256,
       source_pdf_sha256_source,
       app_version,
       imported_at,
       is_published,
       published_at,
       published_by,
       tb_page_count,
       line_item_count,
       review_row_count
FROM runs;

DROP VIEW IF EXISTS v_statement_index;
CREATE VIEW v_statement_index AS
SELECT s.statement_id,
       s.paper_key,
       s.publication_date,
       s.publication_date_source,
       s.statement_date,
       s.statement_date_source,
       b.display_name AS bank_name,
       s.bank_key,
       s.bank_name_raw,
       s.identity_status,
       s.metadata_status,
       COALESCE(r.is_published, 0) AS is_published,
       s.pdf_pages,
       s.printed_pages,
       s.page_count,
       s.row_count,
       s.review_row_count,
       SUM(CASE WHEN li.natural_key_status = 'missing_label' THEN 1 ELSE 0 END)
           AS missing_label_rows,
       SUM(CASE WHEN li.natural_key_status = 'duplicate' THEN 1 ELSE 0 END)
           AS duplicate_key_rows,
       SUM(CASE WHEN rv.status = 'verified' THEN 1 ELSE 0 END)
           AS verified_rows,
       COALESCE(SUM(CASE WHEN rv.status = 'verified' THEN li.debit_cents END), 0)
           AS debit_verified_cents,
       COALESCE(SUM(CASE WHEN rv.status = 'verified' THEN li.credit_cents END), 0)
           AS credit_verified_cents,
       COALESCE(SUM(li.debit_cents), 0) AS debit_total_cents,
       COALESCE(SUM(li.credit_cents), 0) AS credit_total_cents,
       SUM(CAST(li.debit_amount AS REAL)) AS debit_total_approx,
       SUM(CAST(li.credit_amount AS REAL)) AS credit_total_approx
FROM v_current_statements s
LEFT JOIN line_items li ON li.statement_id = s.statement_id
LEFT JOIN row_verification rv ON rv.line_item_id = li.line_item_id
LEFT JOIN banks b ON b.bank_id = s.bank_id
LEFT JOIN runs r ON r.run_id = s.run_id
GROUP BY s.statement_id;

DROP VIEW IF EXISTS v_natural_key_duplicates;
CREATE VIEW v_natural_key_duplicates AS
SELECT paper_key,
       publication_date,
       statement_date,
       bank_name,
       parent_label_standard,
       label_standard,
       COUNT(*) AS row_count,
       SUM(CASE WHEN review_status = 'needs_review' THEN 1 ELSE 0 END)
           AS review_rows
FROM v_current_line_items
WHERE natural_key_status <> 'missing_label'
GROUP BY paper_key,
         publication_date,
         statement_date,
         bank_name,
         parent_label_standard,
         label_standard
HAVING COUNT(*) > 1;
"""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_date(value: Any) -> tuple[str, bool]:
    """Return (ISO date or original text, is_valid)."""
    text = normalize_text(value)
    if not text:
        return "", True
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)),
                        int(match.group(3))).isoformat(), True
        except ValueError:
            return text, False
    try:
        return date.fromisoformat(text).isoformat(), True
    except ValueError:
        return text, False


def bank_key_for(name: str) -> str:
    """Return a stable, punctuation-insensitive key for a bank name."""
    text = normalize_text(name)
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).casefold()
    body = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", ascii_text)
    return f"bank:{body}"


def load_bank_aliases(path: Path | None) -> dict[str, tuple[str, str]]:
    """Load ``alias,canonical[,bank_key,note]`` mappings.

    Returns normalized alias key -> (canonical bank key, canonical display name).
    """
    aliases: dict[str, tuple[str, str]] = {}
    if path is None:
        return aliases
    for row in read_csv(path):
        alias = normalize_text(row.get("alias"))
        canonical = normalize_text(row.get("canonical"))
        if not alias or not canonical:
            continue
        alias_key = bank_key_for(alias)
        canonical_key = normalize_text(row.get("bank_key")) or bank_key_for(canonical)
        value = (canonical_key, canonical)
        for key in (alias_key, bank_key_for(canonical)):
            if key in aliases and aliases[key] != value:
                raise ValueError(
                    f"bank alias {alias!r} maps to two different canonical banks"
                )
            aliases[key] = value
    return aliases


def resolve_bank(
    name: str, aliases: dict[str, tuple[str, str]]
) -> tuple[str | None, str]:
    raw = normalize_text(name)
    if not raw:
        return None, ""
    key = bank_key_for(raw)
    if key in aliases:
        return aliases[key]
    # A bilingual name such as "郵政儲金局 | CAIXA ECONÓMICA POSTAL" is not
    # itself an alias key. If every side maps to the same canonical bank, use
    # that bank instead of creating a third identity for the combined string.
    parts = [
        part.strip() for part in re.split(r"[|｜]", raw) if part.strip()
    ]
    if len(parts) > 1:
        resolved = [aliases.get(bank_key_for(part)) for part in parts]
        if all(item is not None for item in resolved):
            canonical = [item for item in resolved if item is not None]
            canonical_keys = {item[0] for item in canonical}
            if len(canonical_keys) == 1:
                return canonical[0]
    return key, raw


def infer_run_date(
    pages: Iterable[dict[str, Any]], field: str
) -> tuple[str, str]:
    """Infer one date for the whole run when all non-empty values agree.

    Returns (value, status): status is ``single``, ``conflict`` or ``missing``.
    """
    values = {
        page[field]
        for page in pages
        if page.get(field) and page.get(f"{field}_valid", True)
    }
    if len(values) == 1:
        return next(iter(values)), "single"
    if len(values) > 1:
        return "", "conflict"
    return "", "missing"


def _join_unique(values: Iterable[str]) -> str:
    return ",".join(dict.fromkeys(value for value in values if value))


def _to_float(value: Any) -> float | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_cents(value: Any) -> int | None:
    """Exact minor units for a canonical amount, or None if not exact cents."""
    text = normalize_text(value)
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    cents = amount * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _computed_source_pdf_sha256(report: dict[str, Any]) -> str:
    source = normalize_text(report.get("source_pdf"))
    if not source:
        return ""
    path = Path(source).expanduser()
    return _sha256_file(path) if path.is_file() else ""


def _source_pdf_sha256(report: dict[str, Any]) -> str:
    """Prefer the hash recorded at OCR time over an import-time computation."""
    recorded = normalize_text(report.get("source_pdf_sha256")).lower()
    if re.fullmatch(r"[0-9a-f]{64}", recorded):
        return recorded
    return _computed_source_pdf_sha256(report)


def _source_pdf_sha_for_import(
    report: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """Return (sha256, source, warnings) for a run being imported."""
    recorded = normalize_text(report.get("source_pdf_sha256")).lower()
    computed = _computed_source_pdf_sha256(report)
    warnings: list[str] = []
    if re.fullmatch(r"[0-9a-f]{64}", recorded):
        if computed and computed != recorded:
            warnings.append(
                "source PDF hash differs from the hash recorded by OCR; "
                "keeping the OCR-time hash, but the PDF may have been replaced"
            )
        return recorded, "ocr_report", warnings
    if recorded:
        warnings.append(
            "run_report source_pdf_sha256 is not a valid SHA-256; "
            "falling back to import-time computation"
        )
    if computed:
        warnings.append(
            "run_report has no source_pdf_sha256; computed at import time "
            "(re-run OCR to capture provenance at source)"
        )
        return computed, "import_time", warnings
    warnings.append(
        "run_report has no source_pdf_sha256 and the source PDF is unavailable; "
        "provenance hash is empty"
    )
    return "", "missing", warnings


def _run_id_for(
    run_dir: Path,
    report: dict[str, Any],
    source_pdf_sha256: str | None = None,
) -> str:
    """Content-addressed run id: PDF bytes, CSV output and OCR settings."""
    if source_pdf_sha256 is None:
        source_pdf_sha256 = _source_pdf_sha256(report)
    digest = hashlib.sha256()
    digest.update(b"macau-balancete-run-v1\0")
    digest.update(json.dumps(
        report, ensure_ascii=False, sort_keys=True
    ).encode("utf-8"))
    digest.update(b"\0pdf\0")
    digest.update(source_pdf_sha256.encode("utf-8") or b"missing")
    for name in ("documents.csv", "tb_rows.csv"):
        path = run_dir / name
        digest.update(b"\0file\0" + name.encode("utf-8") + b"\0")
        if path.exists():
            digest.update(path.read_bytes())
    return f"run_{digest.hexdigest()}"


def _label_language(row: dict[str, str]) -> str:
    label_id = normalize_text(row.get("label_standard_id"))
    if label_id.startswith("zh_"):
        return "zh-Hant"
    if label_id.startswith("pt_"):
        return "pt"
    raw = normalize_text(row.get("label_raw"))
    if CJK_RE.search(raw):
        return "zh-Hant"
    if raw:
        return "latin"
    return ""


def _identity_status(page: dict[str, Any]) -> str:
    issues: list[str] = []
    if not page["publication_date"]:
        issues.append("missing_publication_date")
    elif not page["publication_date_valid"]:
        issues.append("invalid_publication_date")
    if not page["statement_date"]:
        issues.append("missing_statement_date")
    elif not page["statement_date_valid"]:
        issues.append("invalid_statement_date")
    if not page["bank_key"]:
        issues.append("missing_bank_name")
    return ";".join(issues) or "complete"


def _collect_pages(
    run_dir: Path,
    run_id: str,
    aliases: dict[str, tuple[str, str]],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, str]]], dict[str, Any]]:
    docs_path = run_dir / "documents.csv"
    rows_path = run_dir / "tb_rows.csv"
    if not rows_path.exists():
        raise FileNotFoundError(f"missing tb_rows.csv in {run_dir}")
    documents = read_csv(docs_path) if docs_path.exists() else []
    rows = read_csv(rows_path)
    if not documents:
        warnings.append(f"{run_dir.name}: documents.csv missing or empty")

    docs_by_page: dict[int, dict[str, str]] = {}
    bad_docs = 0
    for doc in documents:
        page = _to_int(doc.get("pdf_page"))
        if page is None:
            bad_docs += 1
        else:
            docs_by_page[page] = doc

    rows_by_page: dict[int, list[dict[str, str]]] = defaultdict(list)
    bad_rows = 0
    for row in rows:
        page = _to_int(row.get("pdf_page"))
        if page is None:
            bad_rows += 1
        else:
            rows_by_page[page].append(row)
    if bad_docs:
        warnings.append(
            f"{run_dir.name}: {bad_docs} documents.csv row(s) have no pdf_page"
        )
    if bad_rows:
        warnings.append(
            f"{run_dir.name}: {bad_rows} tb_rows.csv row(s) have no pdf_page"
        )

    page_numbers = sorted(set(docs_by_page) | set(rows_by_page))
    pages: list[dict[str, Any]] = []
    for page_number in page_numbers:
        doc = docs_by_page.get(page_number, {})
        page_rows = rows_by_page.get(page_number, [])
        first_row = page_rows[0] if page_rows else {}

        def pick(field: str) -> tuple[str, str]:
            doc_value = doc.get(field, "")
            row_value = first_row.get(field, "")
            if normalize_text(doc_value):
                return normalize_text(doc_value), "documents.csv"
            if normalize_text(row_value):
                return normalize_text(row_value), "tb_rows.csv"
            return "", "missing"

        pub_raw, pub_source = pick("publication_date")
        stmt_raw, stmt_source = pick("statement_date")
        publication_date, publication_valid = normalize_date(pub_raw)
        statement_date, statement_valid = normalize_date(stmt_raw)

        bank_raw, bank_source = pick("bank_name")
        if doc.get("bank_name") and first_row.get("bank_name"):
            if bank_key_for(doc["bank_name"]) != bank_key_for(first_row["bank_name"]):
                warnings.append(
                    f"{run_dir.name} page {page_number}: documents.csv bank "
                    f"{doc['bank_name']!r} differs from tb_rows.csv bank "
                    f"{first_row['bank_name']!r}; using documents.csv"
                )
        bank_key, bank_display = resolve_bank(bank_raw, aliases)

        pages.append({
            "pdf_page": page_number,
            "doc": doc,
            "printed_page": normalize_text(doc.get("printed_page"))
            or normalize_text(first_row.get("printed_page")),
            "publication_date": publication_date,
            "publication_date_valid": publication_valid,
            "publication_date_source": pub_source if publication_date else "missing",
            "statement_date": statement_date,
            "statement_date_valid": statement_valid,
            "statement_date_source": stmt_source if statement_date else "missing",
            "bank_name_raw": bank_raw,
            "bank_display": bank_display,
            "bank_key": bank_key,
            "bank_name_source": normalize_text(doc.get("bank_name_source"))
            or normalize_text(first_row.get("bank_name_source")),
            "metadata_status": normalize_text(doc.get("metadata_status"))
            or normalize_text(first_row.get("metadata_status")),
            "rows": page_rows,
        })

    publication_date, publication_status = infer_run_date(pages, "publication_date")
    statement_date, statement_status = infer_run_date(pages, "statement_date")
    if publication_status == "conflict":
        warnings.append(f"{run_dir.name}: conflicting publication_date values in one run")
    if statement_status == "conflict":
        warnings.append(f"{run_dir.name}: conflicting statement_date values in one run")

    for page in pages:
        if not page["publication_date"] and publication_status == "single":
            page["publication_date"] = publication_date
            page["publication_date_source"] = "derived_from_run"
        if not page["statement_date"] and statement_status == "single":
            page["statement_date"] = statement_date
            page["statement_date_source"] = "derived_from_run"
        page["identity_status"] = _identity_status(page)
        if page["identity_status"] != "complete":
            warnings.append(
                f"{run_dir.name} page {page['pdf_page']}: "
                f"{page['identity_status']}"
            )

    return pages, rows_by_page, {
        "row_count": len(rows),
        "page_count": len(pages),
        "publication_date": publication_date,
        "publication_date_status": publication_status,
        "statement_date": statement_date,
        "statement_date_status": statement_status,
    }


def _group_pages(
    run_dir: Path,
    run_id: str,
    pages: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        if (
            page["publication_date"]
            and page["statement_date"]
            and page["bank_key"]
        ):
            paper_key = "|".join((
                page["publication_date"],
                page["statement_date"],
                page["bank_key"],
            ))
        else:
            paper_key = f"incomplete:{run_id}:page:{page['pdf_page']}"
        page["paper_key"] = paper_key
        page["statement_id"] = f"{run_id}:{paper_key}"
        groups[paper_key].append(page)

    statements: list[dict[str, Any]] = []
    for paper_key, group in groups.items():
        group.sort(key=lambda item: item["pdf_page"])
        pages_sorted = [page["pdf_page"] for page in group]
        if pages_sorted != list(range(pages_sorted[0], pages_sorted[-1] + 1)):
            warnings.append(
                f"{run_dir.name}: statement {paper_key} spans non-contiguous "
                f"pages {pages_sorted}"
            )
        raw_names = list(dict.fromkeys(
            page["bank_name_raw"] for page in group if page["bank_name_raw"]
        ))
        display_names = list(dict.fromkeys(
            page["bank_display"] for page in group if page["bank_display"]
        ))
        bank_name_raw = raw_names[0] if raw_names else ""
        alternatives = display_names
        if len(alternatives) > 1:
            warnings.append(
                f"{run_dir.name}: statement {paper_key} has several bank name "
                f"variants {alternatives}"
            )
        identity_issues: list[str] = []
        for page in group:
            for issue in page["identity_status"].split(";"):
                if issue and issue != "complete" and issue not in identity_issues:
                    identity_issues.append(issue)
        identity = ";".join(identity_issues) or "complete"
        metadata_status = "needs_manual_review" if any(
            page["metadata_status"] not in ("", "metadata_extracted")
            or page["identity_status"] != "complete"
            for page in group
        ) else (group[0]["metadata_status"] or "metadata_extracted")

        statement = {
            "statement_id": f"{run_id}:{paper_key}",
            "run_id": run_id,
            "paper_key": paper_key,
            "publication_date": group[0]["publication_date"],
            "publication_date_source": _join_unique(
                page["publication_date_source"] for page in group
            ) or "missing",
            "statement_date": group[0]["statement_date"],
            "statement_date_source": _join_unique(
                page["statement_date_source"] for page in group
            ) or "missing",
            "bank_key": group[0]["bank_key"],
            "bank_name_raw": bank_name_raw,
            "bank_name_alternatives": json.dumps(
                alternatives if len(alternatives) > 1 else [],
                ensure_ascii=False,
            ),
            "bank_name_source": _join_unique(
                page["bank_name_source"] for page in group
            ),
            "pdf_pages": ",".join(str(page) for page in pages_sorted),
            "printed_pages": _join_unique(
                page["printed_page"] for page in group
            ),
            "page_count": len(group),
            "metadata_status": metadata_status,
            "identity_status": identity or "complete",
            "pages": group,
        }
        if (
            statement["publication_date_source"]
            and "," in statement["publication_date_source"]
        ):
            statement["publication_date_source"] = "mixed"
        if (
            statement["statement_date_source"]
            and "," in statement["statement_date_source"]
        ):
            statement["statement_date_source"] = "mixed"
        statements.append(statement)
    statements.sort(key=lambda item: item["pages"][0]["pdf_page"])
    return statements


def _prepare_line_items(
    run_id: str,
    statement: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    occurrences: Counter[str] = Counter()
    lines: list[dict[str, Any]] = []
    row_order = 0
    for page in statement["pages"]:
        for row in page["rows"]:
            row_order += 1
            label_raw = "" if row.get("label_raw") is None else str(row["label_raw"])
            label_standard = normalize_text(row.get("label_standard"))
            parent_label_standard = normalize_text(
                row.get("parent_label_standard")
            )
            label_path = normalize_text(row.get("label_path"))
            if not label_path and label_standard:
                label_path = (
                    f"{parent_label_standard} > {label_standard}"
                    if parent_label_standard
                    else label_standard
                )
            label_path_key = label_path if label_standard else ""
            occurrences[label_path_key] += 1
            occurrence = occurrences[label_path_key]
            if not label_path_key:
                natural_key_status = "missing_label"
            elif occurrence > 1:
                natural_key_status = "duplicate"
            else:
                natural_key_status = "complete"
            natural_key = "|".join((
                statement["paper_key"],
                label_path_key,
                str(occurrence),
            ))
            lines.append({
                "statement_id": statement["statement_id"],
                "run_id": run_id,
                "row_order": row_order,
                "pdf_page": _to_int(row.get("pdf_page"))
                or page["pdf_page"],
                "printed_page": normalize_text(row.get("printed_page"))
                or page["printed_page"],
                "bank_name_raw": statement["bank_name_raw"],
                "label_raw": label_raw,
                "label_normalized": normalize_text(
                    row.get("label_normalized")
                ),
                "label_standard": label_standard,
                "label_standard_id": normalize_text(
                    row.get("label_standard_id")
                ),
                "label_standard_status": normalize_text(
                    row.get("label_standard_status")
                ),
                "label_standard_score": _to_float(
                    row.get("label_standard_score")
                ),
                "label_role": normalize_text(row.get("label_role")),
                "parent_label_raw": normalize_text(
                    row.get("parent_label_raw")
                ),
                "parent_label_standard": parent_label_standard,
                "label_path": label_path,
                "label_language": _label_language(row),
                "label_path_key": label_path_key,
                "occurrence": occurrence,
                "natural_key": natural_key,
                "natural_key_status": natural_key_status,
                "debit_raw": "" if row.get("debit_raw") is None else str(row["debit_raw"]),
                "credit_raw": "" if row.get("credit_raw") is None else str(row["credit_raw"]),
                "debit_amount": normalize_text(row.get("debit_amount")),
                "credit_amount": normalize_text(row.get("credit_amount")),
                "debit_cents": _to_cents(row.get("debit_amount")),
                "credit_cents": _to_cents(row.get("credit_amount")),
                "number_format": normalize_text(row.get("number_format")),
                "number_format_confidence": _to_float(
                    row.get("number_format_confidence")
                ),
                "debit_parse_status": normalize_text(
                    row.get("debit_parse_status")
                ),
                "credit_parse_status": normalize_text(
                    row.get("credit_parse_status")
                ),
                "review_reason": normalize_text(row.get("review_reason")),
                "review_status": normalize_text(row.get("review_status")),
                "row_confidence": _to_float(row.get("row_confidence")),
                "row_average_confidence": _to_float(
                    row.get("row_average_confidence")
                ),
                "source_image": normalize_text(row.get("source_image")),
                "bbox_x0": _to_float(row.get("bbox_x0")),
                "bbox_y0": _to_float(row.get("bbox_y0")),
                "bbox_x1": _to_float(row.get("bbox_x1")),
                "bbox_y1": _to_float(row.get("bbox_y1")),
            })
    if any(line["natural_key_status"] == "missing_label" for line in lines):
        warnings.append(
            f"{statement['statement_id']}: "
            f"{sum(line['natural_key_status'] == 'missing_label' for line in lines)} "
            "rows have no standard label"
        )
    statement["row_count"] = len(lines)
    statement["review_row_count"] = sum(
        line["review_status"] == "needs_review" for line in lines
    )
    return lines


def _upsert_bank(
    conn: sqlite3.Connection,
    bank_key: str,
    display_name: str,
    run_id: str,
) -> int:
    normalized = bank_key_for(display_name).removeprefix("bank:") or bank_key.removeprefix("bank:")
    conn.execute(
        """
        INSERT INTO banks (bank_key, display_name, normalized_name, first_seen_run)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(bank_key) DO UPDATE SET
            display_name = CASE
                WHEN banks.display_name = '' THEN excluded.display_name
                ELSE banks.display_name
            END
        """,
        (bank_key, display_name, normalized, run_id),
    )
    row = conn.execute(
        "SELECT bank_id FROM banks WHERE bank_key = ?", (bank_key,)
    ).fetchone()
    return int(row[0])


def _insert_run(conn: sqlite3.Connection, run: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO runs (
            run_id, run_dir, source_pdf, source_pdf_sha256,
            source_pdf_sha256_source, app_version,
            imported_at, tb_page_count, line_item_count, review_row_count,
            is_published, published_at, published_by,
            config_json, run_report_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run["run_id"],
            run["run_dir"],
            run["source_pdf"],
            run.get("source_pdf_sha256", ""),
            run.get("source_pdf_sha256_source", ""),
            run["app_version"],
            run["imported_at"],
            run["page_count"],
            run["line_count"],
            run["review_count"],
            1 if run.get("is_published") else 0,
            run.get("published_at"),
            run.get("published_by"),
            json.dumps(run["config"], ensure_ascii=False, sort_keys=True),
            json.dumps(run["report"], ensure_ascii=False, sort_keys=True),
        ),
    )


def _insert_pages(
    conn: sqlite3.Connection,
    run_id: str,
    statement: dict[str, Any],
) -> None:
    for page in statement["pages"]:
        doc = page.get("doc") or {}
        conn.execute(
            """
            INSERT INTO pages (
                run_id, pdf_page, printed_page, statement_id,
                bank_name_raw, publication_date, statement_date,
                metadata_status, ocr_status, ocr_languages,
                image_path, image_width, image_height,
                number_format_detected, number_format_confidence,
                document_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                page["pdf_page"],
                page["printed_page"],
                statement["statement_id"],
                page["bank_name_raw"],
                page["publication_date"],
                page["statement_date"],
                page["metadata_status"],
                normalize_text(doc.get("ocr_status")),
                normalize_text(doc.get("ocr_languages")),
                normalize_text(doc.get("image_path")),
                _to_int(doc.get("image_width")),
                _to_int(doc.get("image_height")),
                normalize_text(doc.get("number_format_detected")),
                _to_float(doc.get("number_format_confidence")),
                json.dumps(doc, ensure_ascii=False, sort_keys=True),
            ),
        )


def _delete_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "DELETE FROM line_items WHERE statement_id IN "
        "(SELECT statement_id FROM statements WHERE run_id = ?)",
        (run_id,),
    )
    conn.execute("DELETE FROM statements WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


VERIFICATION_FINGERPRINT_FIELDS = (
    "pdf_page", "label_raw", "parent_label_standard", "label_standard",
    "debit_raw", "credit_raw", "debit_amount", "credit_amount",
)


def _verification_fingerprint(row: sqlite3.Row | dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        normalize_text(row[field] if row[field] is not None else "")
        for field in VERIFICATION_FINGERPRINT_FIELDS
    )


def _snapshot_run_verification(
    conn: sqlite3.Connection, run_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT li.statement_id, li.row_order, li.pdf_page,
               li.label_raw, li.parent_label_standard, li.label_standard,
               li.debit_raw, li.credit_raw, li.debit_amount, li.credit_amount,
               rv.status, rv.verified_at, rv.verified_by, rv.note
        FROM row_verification rv
        JOIN line_items li ON li.line_item_id = rv.line_item_id
        WHERE li.run_id = ?
        ORDER BY li.statement_id, li.row_order
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _restore_run_verification(
    conn: sqlite3.Connection, snapshot: list[dict[str, Any]]
) -> int:
    """Restore verification by stable row identity and OCR fingerprint."""
    if not snapshot:
        return 0
    restored = 0
    mismatched: list[str] = []
    for item in snapshot:
        row = conn.execute(
            """
            SELECT line_item_id, pdf_page, label_raw, parent_label_standard,
                   label_standard, debit_raw, credit_raw, debit_amount,
                   credit_amount
            FROM line_items
            WHERE statement_id = ? AND row_order = ?
            """,
            (item["statement_id"], item["row_order"]),
        ).fetchone()
        if row is None or _verification_fingerprint(row) != _verification_fingerprint(item):
            mismatched.append(f"{item['statement_id']}#{item['row_order']}")
            continue
        conn.execute(
            """
            INSERT INTO row_verification (
                line_item_id, status, verified_at, verified_by, note
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(line_item_id) DO UPDATE SET
                status = excluded.status,
                verified_at = excluded.verified_at,
                verified_by = excluded.verified_by,
                note = excluded.note
            """,
            (
                row["line_item_id"],
                item["status"],
                item["verified_at"],
                item["verified_by"],
                item["note"],
            ),
        )
        restored += 1
    if mismatched:
        raise RuntimeError(
            f"{len(mismatched)} verified row(s) no longer match after re-import "
            f"({', '.join(mismatched[:5])}); transaction rolled back. "
            "Export the verification records and use --discard-verification "
            "only if you intend to drop them."
        )
    return restored


def _run_page_set(conn: sqlite3.Connection, run_id: str) -> set[int]:
    return {
        int(row[0])
        for row in conn.execute(
            "SELECT DISTINCT pdf_page FROM pages WHERE run_id = ?", (run_id,)
        )
    }


def _published_peers(
    conn: sqlite3.Connection,
    run_id: str,
    source_pdf: str,
    source_pdf_sha256: str,
) -> list[sqlite3.Row]:
    """Find published runs for the same source PDF, preferring content hash."""
    if source_pdf_sha256:
        return conn.execute(
            "SELECT run_id, source_pdf FROM runs "
            "WHERE run_id <> ? AND is_published = 1 AND ("
            "  source_pdf_sha256 = ? OR "
            "  (COALESCE(source_pdf_sha256, '') = '' AND source_pdf = ?)"
            ")",
            (run_id, source_pdf_sha256, source_pdf),
        ).fetchall()
    if source_pdf:
        return conn.execute(
            "SELECT run_id, source_pdf FROM runs "
            "WHERE run_id <> ? AND is_published = 1 AND source_pdf = ?",
            (run_id, source_pdf),
        ).fetchall()
    return []


def _publish_run(
    conn: sqlite3.Connection,
    run_id: str,
    published_by: str,
    force: bool = False,
) -> None:
    run = conn.execute(
        "SELECT source_pdf, source_pdf_sha256 FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise RuntimeError(f"cannot publish unknown run {run_id}")
    new_pages = _run_page_set(conn, run_id)
    peers = _published_peers(
        conn, run_id, run["source_pdf"] or "", run["source_pdf_sha256"] or ""
    )
    missing: list[str] = []
    for peer in peers:
        peer_pages = _run_page_set(conn, peer["run_id"])
        absent = sorted(peer_pages - new_pages)
        if peer_pages and absent:
            missing.append(f"{peer['run_id']} pages {absent}")
    if missing and not force:
        raise RuntimeError(
            "refusing to publish an incomplete run: the currently published "
            "run covers pages this run does not (" + "; ".join(missing) + "). "
            "Use --force-publish only after confirming those pages should be "
            "removed from the published dataset."
        )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for peer in peers:
        conn.execute(
            "UPDATE runs SET is_published = 0 WHERE run_id = ?",
            (peer["run_id"],),
        )
    conn.execute(
        "UPDATE runs SET is_published = 1, "
        "published_at = COALESCE(published_at, ?), "
        "published_by = COALESCE(published_by, ?) "
        "WHERE run_id = ?",
        (now, published_by or "operator", run_id),
    )


def _check_bank_alias_drift(
    conn: sqlite3.Connection,
    run_id: str,
    aliases: dict[str, tuple[str, str]],
) -> list[str]:
    conflicts: list[str] = []
    for row in conn.execute(
        "SELECT statement_id, bank_name_raw, bank_key FROM statements "
        "WHERE run_id = ? ORDER BY statement_id",
        (run_id,),
    ):
        new_key, _ = resolve_bank(row["bank_name_raw"], aliases)
        if new_key and new_key != row["bank_key"]:
            conflicts.append(
                f"{row['statement_id']}: {row['bank_key']} -> {new_key}"
            )
    return conflicts


def _load_alias_rows(
    conn: sqlite3.Connection,
    path: Path | None,
) -> None:
    if path is None:
        return
    for row in read_csv(path):
        alias = normalize_text(row.get("alias"))
        canonical = normalize_text(row.get("canonical"))
        if not alias or not canonical:
            continue
        alias_key = bank_key_for(alias)
        canonical_key = normalize_text(row.get("bank_key")) or bank_key_for(canonical)
        conn.execute(
            """
            INSERT INTO bank_aliases (
                alias_key, bank_key, alias_name, canonical_name, note
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias_key) DO UPDATE SET
                bank_key = excluded.bank_key,
                alias_name = excluded.alias_name,
                canonical_name = excluded.canonical_name,
                note = excluded.note
            """,
            (
                alias_key,
                canonical_key,
                alias,
                canonical,
                normalize_text(row.get("note")),
            ),
        )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _get_schema_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _migrate_schema(conn: sqlite3.Connection, from_version: int) -> None:
    """Apply forward migrations from ``from_version`` to SCHEMA_VERSION."""
    if from_version < 2:
        runs_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()
        if runs_exists:
            columns = _table_columns(conn, "runs")
            additions = (
                ("source_pdf_sha256", "TEXT"),
                ("is_published", "INTEGER NOT NULL DEFAULT 0"),
                ("published_at", "TEXT"),
                ("published_by", "TEXT"),
            )
            for name, ddl in additions:
                if name not in columns:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {ddl}")
            # Runs imported before the publish workflow stay visible. If one
            # source PDF has several old runs, publish only the newest one so a
            # test run cannot become current merely because it was imported last.
            conn.execute("UPDATE runs SET is_published = 0")
            conn.execute(
                """
                UPDATE runs SET is_published = 1
                WHERE rowid IN (
                    SELECT rowid FROM (
                        SELECT rowid,
                               ROW_NUMBER() OVER (
                                   PARTITION BY COALESCE(NULLIF(source_pdf, ''), run_id)
                                   ORDER BY imported_at DESC, rowid DESC
                               ) AS rank
                        FROM runs
                    )
                    WHERE rank = 1
                )
                """
            )
    if from_version < 3:
        runs_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()
        if runs_exists:
            columns = _table_columns(conn, "runs")
            if "source_pdf_sha256_source" not in columns:
                conn.execute(
                    "ALTER TABLE runs ADD COLUMN source_pdf_sha256_source TEXT"
                )
            conn.execute(
                "UPDATE runs SET source_pdf_sha256_source = "
                "CASE WHEN COALESCE(source_pdf_sha256, '') <> '' "
                "THEN 'import_time' ELSE 'missing' END "
                "WHERE source_pdf_sha256_source IS NULL"
            )


def _schema(conn: sqlite3.Connection) -> None:
    version = _get_schema_version(conn)
    if version == 0:
        # A pre-schema_meta database with a runs table is v1.
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone():
            version = 1
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {version} is newer than this application "
            f"({SCHEMA_VERSION}); upgrade the application"
        )
    if 0 < version < SCHEMA_VERSION:
        _migrate_schema(conn, version)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(VIEWS_SQL)
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('app_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (APP_VERSION,),
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def build_database(
    db_path: Path,
    run_dirs: list[Path],
    aliases_path: Path | None = None,
    replace: bool = False,
    publish: bool = False,
    published_by: str = "operator",
    force_publish: bool = False,
    discard_verification: bool = False,
) -> dict[str, Any]:
    """Import run directories into ``db_path`` and return a summary report."""
    aliases = load_bank_aliases(aliases_path)
    warnings: list[str] = []
    run_results: list[dict[str, Any]] = []
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect(db_path)
    try:
        _schema(conn)
        _load_alias_rows(conn, aliases_path)
        with conn:
            for run_dir in run_dirs:
                run_dir = run_dir.expanduser().resolve()
                if not run_dir.is_dir():
                    raise NotADirectoryError(f"run directory not found: {run_dir}")
                report = read_json(run_dir / "run_report.json")
                source_pdf_sha256, source_pdf_sha256_source, hash_warnings = (
                    _source_pdf_sha_for_import(report)
                )
                warnings.extend(
                    f"{run_dir.name}: {warning}" for warning in hash_warnings
                )
                run_id = _run_id_for(run_dir, report, source_pdf_sha256)
                existing = conn.execute(
                    "SELECT tb_page_count, line_item_count, is_published, "
                    "published_at, published_by FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                drift = _check_bank_alias_drift(conn, run_id, aliases) if existing else []
                if drift:
                    warnings.append(
                        f"{run_dir.name}: bank alias mapping changed for "
                        f"{len(drift)} statement(s); rebuild the database or "
                        "re-import with --replace after exporting verification"
                    )
                verification_snapshot: list[dict[str, Any]] = []
                if existing and not replace:
                    if publish and drift:
                        raise RuntimeError(
                            f"{run_dir.name}: refusing to publish because bank "
                            "alias mapping changed for existing statements; "
                            "rebuild the database or re-import with --replace"
                        )
                    if publish:
                        _publish_run(conn, run_id, published_by, force=force_publish)
                    run_results.append({
                        "run_id": run_id,
                        "run_dir": str(run_dir),
                        "status": (
                            "skipped_existing_published" if publish
                            else "skipped_existing"
                        ),
                        "tb_pages": existing["tb_page_count"],
                        "line_items": existing["line_item_count"],
                        "published": bool(existing["is_published"] or publish),
                    })
                    warnings.append(
                        f"{run_dir.name}: already imported as {run_id}; "
                        "use --replace to re-import it"
                    )
                    continue
                if existing and replace:
                    verification_snapshot = _snapshot_run_verification(conn, run_id)
                    if verification_snapshot and discard_verification:
                        warnings.append(
                            f"{run_dir.name}: discarding "
                            f"{len(verification_snapshot)} verification row(s)"
                        )
                        verification_snapshot = []
                    _delete_run(conn, run_id)

                pages, _rows_by_page, page_stats = _collect_pages(
                    run_dir, run_id, aliases, warnings
                )
                statements = _group_pages(run_dir, run_id, pages, warnings)
                _insert_run(conn, {
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "source_pdf": normalize_text(report.get("source_pdf")),
                    "source_pdf_sha256": source_pdf_sha256,
                    "source_pdf_sha256_source": source_pdf_sha256_source,
                    "app_version": normalize_text(report.get("app_version"))
                    or APP_VERSION,
                    "imported_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "page_count": page_stats["page_count"],
                    "line_count": 0,
                    "review_count": 0,
                    "is_published": bool(existing["is_published"]) if existing else False,
                    "published_at": existing["published_at"] if existing else None,
                    "published_by": existing["published_by"] if existing else None,
                    "config": {
                        key: report.get(key)
                        for key in (
                            "mode", "ocr_engine", "vision_level",
                            "row_structure", "amount_second_pass",
                            "number_format_override", "repair_ocr_separators",
                        )
                        if key in report
                    },
                    "report": report,
                })
                line_count = review_count = 0
                for statement in statements:
                    bank_id = None
                    if statement["bank_key"]:
                        display = next(
                            (
                                page["bank_display"]
                                for page in statement["pages"]
                                if page["bank_display"]
                            ),
                            statement["bank_name_raw"],
                        )
                        bank_id = _upsert_bank(
                            conn, statement["bank_key"], display, run_id
                        )
                    lines = _prepare_line_items(run_id, statement, warnings)
                    line_count += len(lines)
                    review_count += statement["review_row_count"]
                    conn.execute(
                        """
                        INSERT INTO statements (
                            statement_id, run_id, paper_key,
                            publication_date, publication_date_source,
                            statement_date, statement_date_source,
                            bank_id, bank_key, bank_name_raw,
                            bank_name_alternatives, bank_name_source,
                            pdf_pages, printed_pages, page_count,
                            metadata_status, identity_status,
                            row_count, review_row_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            statement["statement_id"],
                            run_id,
                            statement["paper_key"],
                            statement["publication_date"],
                            statement["publication_date_source"],
                            statement["statement_date"],
                            statement["statement_date_source"],
                            bank_id,
                            statement["bank_key"],
                            statement["bank_name_raw"],
                            statement["bank_name_alternatives"],
                            statement["bank_name_source"],
                            statement["pdf_pages"],
                            statement["printed_pages"],
                            statement["page_count"],
                            statement["metadata_status"],
                            statement["identity_status"],
                            statement["row_count"],
                            statement["review_row_count"],
                        ),
                    )
                    _insert_pages(conn, run_id, statement)
                    conn.executemany(
                        """
                        INSERT INTO line_items (
                            statement_id, run_id, row_order, pdf_page,
                            printed_page, bank_name_raw, label_raw,
                            label_normalized, label_standard, label_standard_id,
                            label_standard_status, label_standard_score,
                            label_role, parent_label_raw, parent_label_standard,
                            label_path, label_language, label_path_key,
                            occurrence, natural_key, natural_key_status,
                            debit_raw, credit_raw, debit_amount, credit_amount,
                            debit_cents, credit_cents,
                            number_format, number_format_confidence,
                            debit_parse_status, credit_parse_status,
                            review_reason, review_status, row_confidence,
                            row_average_confidence, source_image,
                            bbox_x0, bbox_y0, bbox_x1, bbox_y1
                        ) VALUES (
                            :statement_id, :run_id, :row_order, :pdf_page,
                            :printed_page, :bank_name_raw, :label_raw,
                            :label_normalized, :label_standard, :label_standard_id,
                            :label_standard_status, :label_standard_score,
                            :label_role, :parent_label_raw, :parent_label_standard,
                            :label_path, :label_language, :label_path_key,
                            :occurrence, :natural_key, :natural_key_status,
                            :debit_raw, :credit_raw, :debit_amount, :credit_amount,
                            :debit_cents, :credit_cents,
                            :number_format, :number_format_confidence,
                            :debit_parse_status, :credit_parse_status,
                            :review_reason, :review_status, :row_confidence,
                            :row_average_confidence, :source_image,
                            :bbox_x0, :bbox_y0, :bbox_x1, :bbox_y1
                        )
                        """,
                        lines,
                    )
                conn.execute(
                    "UPDATE runs SET line_item_count = ?, review_row_count = ? "
                    "WHERE run_id = ?",
                    (line_count, review_count, run_id),
                )
                restored_verification = _restore_run_verification(
                    conn, verification_snapshot
                )
                if restored_verification:
                    warnings.append(
                        f"{run_dir.name}: restored "
                        f"{restored_verification} verification row(s)"
                    )
                if publish:
                    _publish_run(conn, run_id, published_by, force=force_publish)
                run_results.append({
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "status": "replaced" if existing else "imported",
                    "tb_pages": page_stats["page_count"],
                    "line_items": line_count,
                    "review_rows": review_count,
                    "published": bool(publish or (existing and existing["is_published"])),
                    "verification_restored": restored_verification,
                })
    finally:
        conn.close()

    return _build_report(db_path, run_dirs, run_results, warnings)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _group_counts(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {
        str(row[0] or "(blank)"): int(row[1])
        for row in conn.execute(sql).fetchall()
    }


def _build_report(
    db_path: Path,
    run_dirs: list[Path],
    run_results: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        totals = {
            "runs": _scalar(conn, "SELECT COUNT(*) FROM runs"),
            "published_runs": _scalar(
                conn, "SELECT COUNT(*) FROM runs WHERE is_published = 1"
            ),
            "banks": _scalar(conn, "SELECT COUNT(*) FROM banks"),
            "pages": _scalar(conn, "SELECT COUNT(*) FROM pages"),
            "statements": _scalar(conn, "SELECT COUNT(*) FROM statements"),
            "line_items": _scalar(conn, "SELECT COUNT(*) FROM line_items"),
            "review_rows": _scalar(
                conn,
                "SELECT COUNT(*) FROM line_items WHERE review_status = 'needs_review'",
            ),
            "statements_with_missing_identity": _scalar(
                conn,
                "SELECT COUNT(*) FROM statements WHERE identity_status <> 'complete'",
            ),
        }
        natural_key_status = _group_counts(
            conn,
            "SELECT natural_key_status, COUNT(*) FROM line_items "
            "GROUP BY natural_key_status ORDER BY natural_key_status",
        )
        publication_date_sources = _group_counts(
            conn,
            "SELECT publication_date_source, COUNT(*) FROM statements "
            "GROUP BY publication_date_source",
        )
        statement_date_sources = _group_counts(
            conn,
            "SELECT statement_date_source, COUNT(*) FROM statements "
            "GROUP BY statement_date_source",
        )
        identity_status = _group_counts(
            conn,
            "SELECT identity_status, COUNT(*) FROM statements "
            "GROUP BY identity_status",
        )
        duplicate_groups = _scalar(
            conn, "SELECT COUNT(*) FROM v_natural_key_duplicates"
        )
        duplicate_rows = _scalar(
            conn,
            "SELECT COALESCE(SUM(row_count), 0) FROM v_natural_key_duplicates",
        )
        duplicate_paper_keys = _scalar(
            conn,
            "SELECT COUNT(*) FROM ("
            " SELECT paper_key FROM statements GROUP BY paper_key "
            " HAVING COUNT(DISTINCT run_id) > 1)",
        )
        if duplicate_paper_keys:
            warnings.append(
                f"{duplicate_paper_keys} paper key(s) exist in more than one run; "
                "v_current_* prefers the published run, then the newest import"
            )
    finally:
        conn.close()

    return {
        "database": str(db_path.resolve()),
        "app_version": APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_dirs": [str(path.expanduser().resolve()) for path in run_dirs],
        "runs": run_results,
        "totals": totals,
        "business_key_quality": {
            "rows": totals["line_items"],
            "unique_complete_rows": natural_key_status.get("complete", 0),
            "duplicate_extra_rows": natural_key_status.get("duplicate", 0),
            "duplicate_groups": duplicate_groups,
            "rows_in_duplicate_groups": duplicate_rows,
            "missing_label_rows": natural_key_status.get("missing_label", 0),
        },
        "natural_key_status": natural_key_status,
        "publication_date_sources": publication_date_sources,
        "statement_date_sources": statement_date_sources,
        "identity_status": identity_status,
        "natural_key_duplicates": {
            "groups": duplicate_groups,
            "rows": duplicate_rows,
        },
        "warnings": warnings,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _print_report(report: dict[str, Any]) -> None:
    totals = report["totals"]
    print(f"Database: {report['database']}")
    for run in report["runs"]:
        published = " [published]" if run.get("published") else ""
        print(
            f"  {run['status']:<26} {run['run_id']}  "
            f"{run.get('tb_pages', 0)} page(s), "
            f"{run.get('line_items', 0)} row(s){published}  {run['run_dir']}"
        )
    print(
        f"Totals: {totals['runs']} run(s) "
        f"({totals.get('published_runs', 0)} published), "
        f"{totals['pages']} page(s), "
        f"{totals['statements']} statement(s), "
        f"{totals['line_items']} line item(s), {totals['banks']} bank(s)"
    )
    print(
        "Natural keys: "
        + ", ".join(
            f"{key}={value}"
            for key, value in report["natural_key_status"].items()
        )
    )
    print(
        f"Duplicate five-column key groups: "
        f"{report['natural_key_duplicates']['groups']} "
        f"({report['natural_key_duplicates']['rows']} rows)"
    )
    if report["warnings"]:
        print(f"Warnings: {len(report['warnings'])}")
        for warning in report["warnings"][:12]:
            print(f"  - {warning}")
        if len(report["warnings"]) > 12:
            print(f"  ... {len(report['warnings']) - 12} more in the report")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import OCR run folders into a SQLite Balancete database"
    )
    parser.add_argument(
        "run_dirs", nargs="+", type=Path,
        help="one or more OCR output directories (each needs tb_rows.csv)",
    )
    parser.add_argument(
        "--db", type=Path, default=Path("data/macau_balancete.sqlite"),
        help="SQLite file to create or update",
    )
    parser.add_argument(
        "--bank-aliases", type=Path,
        help="CSV: alias,canonical[,bank_key,note] "
             "(default: labels/bank_aliases.csv if present)",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="replace an already imported run; verification is preserved when "
             "the row identity and OCR fingerprint still match",
    )
    parser.add_argument(
        "--discard-verification", action="store_true",
        help="with --replace, explicitly drop verification rows if they cannot "
             "be matched",
    )
    parser.add_argument(
        "--publish", action="store_true",
        help="mark the imported run as published/current for its source PDF",
    )
    parser.add_argument(
        "--force-publish", action="store_true",
        help="allow publishing a run whose page set is smaller than the "
             "currently published run for the same source PDF",
    )
    parser.add_argument(
        "--published-by", default="operator",
        help="name recorded in runs.published_by when using --publish",
    )
    parser.add_argument(
        "--report", type=Path,
        help="JSON report path (default: <db stem>_import_report.json)",
    )
    args = parser.parse_args(argv)

    report_path = args.report or (
        args.db.with_name(f"{args.db.stem}_import_report.json")
    )
    aliases_path = args.bank_aliases
    if aliases_path is None:
        default_aliases = Path("labels/bank_aliases.csv")
        aliases_path = default_aliases if default_aliases.exists() else None
    try:
        report = build_database(
            args.db, args.run_dirs,
            aliases_path=aliases_path,
            replace=args.replace,
            publish=args.publish,
            published_by=args.published_by,
            force_publish=args.force_publish,
            discard_verification=args.discard_verification,
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}")
        return 1
    write_report(report_path, report)
    _print_report(report)
    print(f"Report: {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
