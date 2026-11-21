#!/usr/bin/env python3
"""Replay OCR run outputs into the v3 database as raw, unassigned evidence.

This first replay stage intentionally stops before Statement / Entity / Page
classification decisions.  It imports:

* gazette issues
* source documents
* physical document pages
* extraction runs
* page extractions
* extracted rows, with ``row_disposition = 'unassigned'``

Later phases assign page roles, logical statements and adopted statement rows.
The importer is deterministic and idempotent: repeating the same run must not
create duplicate rows.  It never writes to the v5 database.

Examples::

    python tools/replay_runs_to_v3.py \
        --db data/macau_balancete_v3.sqlite \
        --runs-dir outputs/runs

    # one run for a smoke test
    python tools/replay_runs_to_v3.py \
        --db /tmp/v3.sqlite --run-dir outputs/runs/issue46_2026
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_RUNS = ROOT / "outputs" / "runs"
DEFAULT_V5 = ROOT / "data" / "macau_balancete.sqlite"

NAMESPACE = uuid.UUID("f6f0d7b6-3e17-5f7b-9f5a-6a4f2f6c1d10")
ISSUE_RE = re.compile(r"issue(\d+)_(\d{4})", re.I)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
DASH_RE = re.compile(r"^[\-–—−－]+$")
ISSUE_KEY_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


class EvidenceChangedError(ValueError):
    """The same run path now contains different OCR evidence."""


class PageCoverageError(ValueError):
    """The run artifacts do not cover the PDF pages exactly once."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def maybe_int(value: Any) -> int | None:
    raw = text(value)
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def maybe_float(value: Any) -> float | None:
    raw = text(value)
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def label_language(label: str) -> str:
    if CJK_RE.search(label):
        return "zh-Hant"
    if LATIN_RE.search(label):
        return "pt"
    return "unknown"


def value_state_and_minor(raw: str, status: str, amount: str) -> tuple[str, int | None]:
    raw_text = text(raw)
    status_text = text(status).lower()
    if not raw_text:
        parsed = decimal_or_none(amount)
        if parsed is not None and (
            status_text == "parsed" or status_text.startswith("recovered")
        ):
            scaled = parsed * 100
            integral = scaled.to_integral_value()
            if scaled == integral:
                return "amount", int(integral)
        return "blank", None
    if DASH_RE.match(raw_text) or status_text == "dash":
        return "dash", None
    if "ambiguous" in status_text or "invalid" in status_text:
        return ("ambiguous" if "ambiguous" in status_text else "unreadable"), None
    parsed = decimal_or_none(amount)
    if parsed is not None:
        scaled = parsed * 100
        integral = scaled.to_integral_value()
        if scaled == integral:
            return "amount", int(integral)
        return "ambiguous", None
    if status_text == "parsed" or status_text.startswith("recovered"):
        return "ambiguous", None
    return "ambiguous", None


def decimal_or_none(value: Any) -> Decimal | None:
    raw = text(value).replace(",", "")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def row_type_for(label_role: str) -> str:
    return {
        "parent": "heading",
        "child": "item",
        "standalone": "item",
        "orphan_child": "item",
        "continuation_line": "continuation",
    }.get(text(label_role).lower(), "unknown")


def parent_status_for(label_role: str, parent_raw: str) -> str:
    role = text(label_role).lower()
    if text(parent_raw):
        return "identified"
    if role == "parent":
        return "root"
    if role == "child":
        return "unknown"
    return "not_applicable"


def connect_ro(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def load_legacy_runs(path: Path) -> dict[str, str]:
    connection = connect_ro(path)
    if connection is None:
        return {}
    try:
        return {
            Path(row["run_dir"]).name: str(row["run_id"])
            for row in connection.execute("SELECT run_id, run_dir FROM runs")
        }
    finally:
        connection.close()


def canonical_issue_key(value: Any) -> str:
    match = ISSUE_KEY_RE.fullmatch(text(value))
    if not match:
        raise ValueError(f"invalid issue key: {value!r}")
    year, issue = int(match.group(1)), int(match.group(2))
    if not (1900 <= year <= 2999 and 1 <= issue <= 99):
        raise ValueError(f"issue key out of range: {value!r}")
    return f"{year}-{issue:02d}"


def directory_issue_key(run_dir: Path) -> str | None:
    match = ISSUE_RE.search(run_dir.name)
    if not match:
        return None
    issue, year = match.group(1), match.group(2)
    return f"{year}-{int(issue):02d}"


def read_manifest(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "import_manifest.json")


def resolve_issue_key(
    run_dir: Path,
    manifest: dict[str, Any],
    explicit: str | None,
) -> str:
    manifest_value = text(manifest.get("issue_key"))
    manifest_key = canonical_issue_key(manifest_value) if manifest_value else None
    dir_key = directory_issue_key(run_dir)
    if explicit:
        key = canonical_issue_key(explicit)
        if manifest_key and manifest_key != key:
            raise ValueError(
                f"--issue-key {key} disagrees with manifest {manifest_key}"
            )
        if dir_key and dir_key != key:
            raise ValueError(
                f"--issue-key {key} disagrees with run directory {dir_key}"
            )
        return key
    if manifest_key:
        if dir_key and dir_key != manifest_key:
            raise ValueError(
                f"manifest issue {manifest_key} disagrees with run directory {dir_key}"
            )
        return manifest_key
    if dir_key:
        return dir_key
    raise ValueError(f"cannot establish issue key from {run_dir.name}")


def manifest_is_strict(manifest: dict[str, Any]) -> bool:
    return (
        manifest.get("manifest_version") == 2
        and text(manifest.get("target")) == "v3"
        and text(manifest.get("manifest_origin") or "written") == "written"
    )


def manifest_source_url(manifest: dict[str, Any]) -> str:
    return text(manifest.get("source_url") or manifest.get("source"))


def evidence_fingerprint(run_dir: Path) -> str:
    parts = ["import-evidence-v1"]
    for name in ("run_report.json", "documents.csv", "tb_rows.csv"):
        path = run_dir / name
        if path.is_file():
            parts.append(name + "\0" + sha256_file(path))
        else:
            parts.append(name + "\0MISSING")
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def page_number(value: Any) -> int | None:
    raw = text(value)
    if not raw.isdigit():
        return None
    return int(raw)


def validate_page_coverage(
    report: dict[str, Any],
    documents: list[dict[str, str]],
    rows_csv: list[dict[str, str]],
    *,
    strict: bool,
) -> dict[str, Any]:
    pdf_pages = maybe_int(report.get("pdf_pages"))
    document_pages = sorted({
        page for row in documents
        if (page := page_number(row.get("pdf_page"))) is not None
    })
    observed_pages = sorted({
        page for row in [*documents, *rows_csv]
        if (page := page_number(row.get("pdf_page"))) is not None
    })
    statuses = [text(row.get("ocr_status")).lower() for row in documents]
    completed = sum(1 for status in statuses if status in ("completed", "extracted_only"))
    failed = sum(1 for status in statuses if status == "failed")
    skipped = sum(1 for status in statuses if status in ("skipped", "not_applicable"))

    if strict:
        if pdf_pages is None or pdf_pages <= 0:
            raise PageCoverageError("run_report.pdf_pages is missing or invalid")
        expected = list(range(1, pdf_pages + 1))
        if document_pages != expected:
            raise PageCoverageError(
                f"documents.csv pages do not cover 1-{pdf_pages}: {document_pages[:5]}"
            )
        if len(documents) != pdf_pages:
            raise PageCoverageError(
                f"documents.csv has {len(documents)} rows for {pdf_pages} pages"
            )
        if any(page > pdf_pages for page in observed_pages):
            raise PageCoverageError(
                f"OCR output contains pages beyond pdf_pages={pdf_pages}"
            )
        invalid = [status for status in statuses if status not in
                   ("completed", "failed", "skipped", "not_applicable", "extracted_only")]
        if invalid:
            raise PageCoverageError(f"invalid ocr_status values: {sorted(set(invalid))}")
        coverage = "full"
    elif pdf_pages is None or pdf_pages <= 0:
        coverage = "unknown"
    else:
        coverage = (
            "full" if len(observed_pages) == pdf_pages and failed == 0
            else "legacy_partial"
        )

    return {
        "pdf_pages": pdf_pages or (max(observed_pages) if observed_pages else 1),
        "ocr_pages_observed": len(observed_pages),
        "ocr_completed_pages": completed,
        "ocr_skipped_pages": skipped,
        "ocr_failed_pages": failed,
        "ocr_page_coverage": coverage,
    }


def insert_if_absent(
    conn: sqlite3.Connection,
    table: str,
    key_column: str,
    values: dict[str, Any],
) -> bool:
    existing = conn.execute(
        f"SELECT 1 FROM {table} WHERE {key_column} = ?", (values[key_column],)
    ).fetchone()
    if existing:
        return False
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values)
    return True


def merge_config_json(raw: Any, updates: dict[str, Any]) -> str:
    config: dict[str, Any] = {}
    if text(raw):
        try:
            loaded = json.loads(text(raw))
        except json.JSONDecodeError as exc:
            raise ValueError("extraction_runs.config_json is invalid JSON") from exc
        if not isinstance(loaded, dict):
            raise ValueError("extraction_runs.config_json is not a JSON object")
        config = loaded
    config.update(updates)
    return json.dumps(config, ensure_ascii=False, sort_keys=True)


def find_existing_run(
    conn: sqlite3.Connection,
    run_report_path: Path,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT run_id, source_document_id, config_json, run_status "
        "FROM extraction_runs WHERE run_report_uri = ?",
        (str(run_report_path.resolve()),),
    ).fetchone()


def get_or_create_document(
    conn: sqlite3.Connection,
    *,
    gazette_issue_id: str,
    issue_key: str,
    source_pdf: str,
    sha256: str,
    page_count: int,
    source_url: str = "",
) -> str:
    document_key = f"{issue_key}:{sha256[:16]}"
    existing = conn.execute(
        "SELECT source_document_id, sha256 FROM source_documents WHERE document_key = ?",
        (document_key,),
    ).fetchone()
    if existing:
        if existing["sha256"] != sha256:
            raise ValueError(f"source document hash changed for {document_key}")
        document_id = str(existing["source_document_id"])
        conn.execute(
            """
            UPDATE source_documents
            SET source_url = COALESCE(source_url, ?),
                page_count = ?
            WHERE source_document_id = ?
            """,
            (source_url or None, page_count, document_id),
        )
        return document_id
    document_id = stable_id("document", document_key, sha256)
    conn.execute(
        """
        INSERT INTO source_documents(
            source_document_id, gazette_issue_id, document_key, source_url,
            original_filename, storage_uri, sha256, page_count, acquired_at, mime_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id, gazette_issue_id, document_key, source_url or None,
            Path(source_pdf).name, source_pdf, sha256, page_count, now(), "application/pdf",
        ),
    )
    return document_id


def get_or_create_page(
    conn: sqlite3.Connection,
    *,
    source_document_id: str,
    pdf_page: int,
    printed_page: str,
) -> str:
    existing = conn.execute(
        "SELECT document_page_id FROM document_pages "
        "WHERE source_document_id = ? AND pdf_page = ?",
        (source_document_id, pdf_page),
    ).fetchone()
    if existing:
        return str(existing["document_page_id"])
    page_id = stable_id("document_page", source_document_id, pdf_page)
    conn.execute(
        """
        INSERT INTO document_pages(
            document_page_id, source_document_id, pdf_page, printed_page,
            page_role, classification_status, classification_source,
            classified_by, classified_at, classification_version, notes
        ) VALUES (?, ?, ?, ?, 'unknown', 'proposed', 'automatic', NULL, NULL, NULL, NULL)
        """,
        (page_id, source_document_id, pdf_page, printed_page or None),
    )
    return page_id


def get_or_create_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_document_id: str,
    report: dict[str, Any],
    run_report_path: Path,
    config_updates: dict[str, Any],
    run_status: str | None,
    fingerprint: str,
    existing_run: sqlite3.Row | None = None,
) -> None:
    if existing_run is None:
        existing_run = conn.execute(
            "SELECT run_id, source_document_id, config_json, run_status "
            "FROM extraction_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if existing_run:
        if existing_run["source_document_id"] != source_document_id:
            raise ValueError(f"run {run_id} already belongs to another document")
        config = {}
        if text(existing_run["config_json"]):
            try:
                config = json.loads(text(existing_run["config_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError("extraction_runs.config_json is invalid JSON") from exc
            if not isinstance(config, dict):
                raise ValueError("extraction_runs.config_json is not a JSON object")
        stored = text(config.get("evidence_fingerprint"))
        if stored and stored != fingerprint:
            raise EvidenceChangedError(
                f"evidence changed for run {run_id}: "
                f"stored {stored}, current {fingerprint}"
            )
        updates = dict(config_updates)
        updates["evidence_fingerprint"] = fingerprint
        updates["evidence_fingerprint_version"] = 1
        conn.execute(
            "UPDATE extraction_runs SET config_json = ? WHERE run_id = ?",
            (merge_config_json(existing_run["config_json"], updates), run_id),
        )
        if run_status is not None:
            conn.execute(
                "UPDATE extraction_runs SET run_status = ? WHERE run_id = ?",
                (run_status, run_id),
            )
        return

    engine = text(report.get("ocr_engine"))
    engine_version = text(report.get("ocr_version"))
    config = {
        key: report.get(key)
        for key in (
            "mode", "ocr_engine", "ocr_version", "vision_level",
            "vision_preprocess", "vision_language_correction", "header_fallback",
            "row_structure", "amount_verifier", "amount_second_pass",
            "label_standardization", "label_lexicon", "number_format_override",
            "chinese_label_normalization", "repair_ocr_separators", "languages",
        )
        if key in report
    }
    config.update(config_updates)
    config["evidence_fingerprint"] = fingerprint
    config["evidence_fingerprint_version"] = 1
    conn.execute(
        """
        INSERT INTO extraction_runs(
            run_id, source_document_id, app_version, ocr_engine_versions,
            config_json, started_at, completed_at, run_status, run_report_uri
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            run_id, source_document_id, text(report.get("app_version")),
            json.dumps({"engine": engine, "version": engine_version}, ensure_ascii=False),
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            run_status or "completed",
            str(run_report_path.resolve()),
        ),
    )


def get_or_create_page_extraction(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    document_page_id: str,
    source_document_id: str,
    document_row: dict[str, str],
) -> str:
    existing = conn.execute(
        "SELECT page_extraction_id FROM page_extractions "
        "WHERE run_id = ? AND document_page_id = ?",
        (run_id, document_page_id),
    ).fetchone()
    if existing:
        return str(existing["page_extraction_id"])
    page_extraction_id = stable_id("page_extraction", run_id, document_page_id)
    languages = split_pipe(document_row.get("ocr_languages") or document_row.get("languages"))
    conn.execute(
        """
        INSERT INTO page_extractions(
            page_extraction_id, run_id, document_page_id, source_document_id,
            ocr_status, ocr_languages_json, image_path, image_width, image_height,
            number_format_detected, number_format_confidence, page_result_uri, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            page_extraction_id, run_id, document_page_id, source_document_id,
            normalize_ocr_status(document_row.get("ocr_status")),
            json.dumps(languages, ensure_ascii=False) if languages else None,
            text(document_row.get("image_path")) or None,
            maybe_int(document_row.get("image_width")),
            maybe_int(document_row.get("image_height")),
            text(document_row.get("number_format_detected")) or None,
            maybe_float(document_row.get("number_format_confidence")),
            now(),
        ),
    )
    return page_extraction_id


def split_pipe(value: Any) -> list[str]:
    return [part.strip() for part in text(value).split("|") if part.strip()]


def normalize_ocr_status(value: Any) -> str:
    status = text(value).lower()
    if status in ("completed", "ok", "success"):
        return "completed"
    if status in ("failed", "error"):
        return "failed"
    if status in ("skipped", "not_applicable"):
        return "skipped"
    return "pending"


def payload_for_row(row: dict[str, str], run_dir: Path, csv_row: int) -> str:
    payload = {
        "_replay": {"run_dir": str(run_dir), "csv_row": csv_row},
        "label_standard": text(row.get("label_standard")) or None,
        "label_standard_id": text(row.get("label_standard_id")) or None,
        "label_standard_status": text(row.get("label_standard_status")) or None,
        "label_standard_score": maybe_float(row.get("label_standard_score")),
        "label_role": text(row.get("label_role")) or None,
        "label_path": text(row.get("label_path")) or None,
        "parent_label_standard": text(row.get("parent_label_standard")) or None,
        "number_format": text(row.get("number_format")) or None,
        "number_format_confidence": maybe_float(row.get("number_format_confidence")),
        "debit_parse_status": text(row.get("debit_parse_status")) or None,
        "credit_parse_status": text(row.get("credit_parse_status")) or None,
        "debit_amount": text(row.get("debit_amount")) or None,
        "credit_amount": text(row.get("credit_amount")) or None,
        "review_reason": text(row.get("review_reason")) or None,
        "review_status": text(row.get("review_status")) or None,
        "row_structure_source": text(row.get("row_structure_source")) or None,
        "column_boundary_source": text(row.get("column_boundary_source")) or None,
        "source_image": text(row.get("source_image")) or None,
        "label_confidence": maybe_float(row.get("label_confidence")),
        "debit_confidence": maybe_float(row.get("debit_confidence")),
        "credit_confidence": maybe_float(row.get("credit_confidence")),
        "row_average_confidence": maybe_float(row.get("row_average_confidence")),
        "debit_second_pass_raw": text(row.get("debit_second_pass_raw")) or None,
        "credit_second_pass_raw": text(row.get("credit_second_pass_raw")) or None,
        "debit_second_pass_status": text(row.get("debit_second_pass_status")) or None,
        "credit_second_pass_status": text(row.get("credit_second_pass_status")) or None,
        "label_normalization_status": text(row.get("label_normalization_status")) or None,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def replay_run(
    conn: sqlite3.Connection,
    run_dir: Path,
    *,
    legacy_runs: dict[str, str],
    issue_key_override: str | None = None,
) -> dict[str, Any]:
    report = read_json(run_dir / "run_report.json")
    documents = read_csv(run_dir / "documents.csv")
    rows_csv = read_csv(run_dir / "tb_rows.csv")
    if not documents and not rows_csv:
        raise ValueError(f"no documents.csv or tb_rows.csv in {run_dir}")

    manifest = read_manifest(run_dir)
    manifest_target = text(manifest.get("target"))
    if manifest_target and manifest_target != "v3":
        raise ValueError(f"manifest target is not v3: {manifest_target}")
    strict = manifest_is_strict(manifest)
    issue_key = resolve_issue_key(run_dir, manifest, issue_key_override)
    coverage = validate_page_coverage(
        report, documents, rows_csv, strict=strict
    )
    pdf_pages = int(coverage["pdf_pages"])
    source_url = manifest_source_url(manifest)

    publication_date = first_value(documents, "publication_date") or first_value(rows_csv, "publication_date")
    gazette_issue_id = stable_id("gazette_issue", issue_key)
    insert_if_absent(
        conn,
        "gazette_issues",
        "gazette_issue_id",
        {
            "gazette_issue_id": gazette_issue_id,
            "issue_key": issue_key,
            "publication_date": publication_date or None,
            "source_url": source_url or None,
            "discovered_at": now(),
            "notes": f"replayed from {run_dir.name}",
        },
    )
    if source_url:
        conn.execute(
            "UPDATE gazette_issues SET source_url = COALESCE(source_url, ?) "
            "WHERE gazette_issue_id = ?",
            (source_url, gazette_issue_id),
        )

    source_pdf = text(report.get("source_pdf"))
    sha256 = text(report.get("source_pdf_sha256"))
    if not sha256 and source_pdf and Path(source_pdf).is_file():
        sha256 = sha256_file(Path(source_pdf))
    if not sha256:
        raise ValueError(f"missing source PDF SHA-256 in {run_dir}")

    document_key = f"{issue_key}:{sha256[:16]}"
    existing_document = conn.execute(
        "SELECT page_count FROM source_documents WHERE document_key = ?",
        (document_key,),
    ).fetchone()
    legacy_page_count = (
        existing_document["page_count"] if existing_document is not None and not strict
        else None
    )

    page_numbers = sorted({
        page for row in [*documents, *rows_csv]
        if (page := page_number(row.get("pdf_page"))) is not None
    })
    source_document_id = get_or_create_document(
        conn,
        gazette_issue_id=gazette_issue_id,
        issue_key=issue_key,
        source_pdf=source_pdf or str(run_dir),
        sha256=sha256,
        page_count=pdf_pages,
        source_url=source_url,
    )

    run_report_path = run_dir / "run_report.json"
    existing_run = find_existing_run(conn, run_report_path)
    if existing_run is not None:
        run_id = str(existing_run["run_id"])
    else:
        run_id = legacy_runs.get(run_dir.name)
        if not run_id:
            run_id = stable_id(
                "run", source_document_id, text(report.get("app_version")),
                run_dir.name,
            )
        existing_run = conn.execute(
            "SELECT run_id, source_document_id, config_json, run_status "
            "FROM extraction_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    fingerprint = evidence_fingerprint(run_dir)
    if strict:
        run_status = (
            "partial" if coverage["ocr_failed_pages"] > 0 else "completed"
        )
    else:
        run_status = None if existing_run is not None else "completed"

    config_updates: dict[str, Any] = {
        "pdf_pages": pdf_pages,
        "ocr_pages_observed": coverage["ocr_pages_observed"],
        "ocr_completed_pages": coverage["ocr_completed_pages"],
        "ocr_skipped_pages": coverage["ocr_skipped_pages"],
        "ocr_failed_pages": coverage["ocr_failed_pages"],
        "ocr_page_coverage": coverage["ocr_page_coverage"],
        "source_pdf_bytes": maybe_int(report.get("source_pdf_size")),
        "import_manifest": (
            str(run_dir / "import_manifest.json")
            if (run_dir / "import_manifest.json").is_file() else None
        ),
        "manifest_origin": "written" if strict else "reconstructed",
        "run_dir": str(run_dir.resolve()),
    }
    if legacy_page_count is not None:
        config_updates["legacy_ocr_page_count"] = legacy_page_count
    get_or_create_run(
        conn,
        run_id=run_id,
        source_document_id=source_document_id,
        report=report,
        run_report_path=run_report_path,
        config_updates=config_updates,
        run_status=run_status,
        fingerprint=fingerprint,
        existing_run=existing_run,
    )

    document_by_page: dict[int, dict[str, str]] = {}
    for row in documents:
        page = page_number(row.get("pdf_page"))
        if page is not None:
            document_by_page.setdefault(page, row)

    page_ids: dict[int, str] = {}
    for page in page_numbers:
        doc_row = document_by_page.get(page, {})
        page_ids[page] = get_or_create_page(
            conn,
            source_document_id=source_document_id,
            pdf_page=page,
            printed_page=text(doc_row.get("printed_page")),
        )

    page_extraction_ids: dict[int, str] = {}
    for page in page_numbers:
        page_extraction_ids[page] = get_or_create_page_extraction(
            conn,
            run_id=run_id,
            document_page_id=page_ids[page],
            source_document_id=source_document_id,
            document_row=document_by_page.get(page, {}),
        )

    rows_by_page: dict[int, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for index, row in enumerate(rows_csv, start=1):
        page = page_number(row.get("pdf_page"))
        if page is None:
            continue
        rows_by_page[page].append((index, row))
    if strict:
        status_by_page = {
            page: text(row.get("ocr_status")).lower()
            for row in documents
            if (page := page_number(row.get("pdf_page"))) is not None
        }
        for page in rows_by_page:
            if status_by_page.get(page) in ("failed", "skipped", "not_applicable"):
                raise PageCoverageError(
                    f"page {page} is {status_by_page[page]} but has extracted rows"
                )

    inserted = 0
    skipped = 0
    for page, page_rows in rows_by_page.items():
        page_extraction_id = page_extraction_ids[page]
        existing_count = conn.execute(
            "SELECT COUNT(*) FROM extracted_rows WHERE page_extraction_id = ?",
            (page_extraction_id,),
        ).fetchone()[0]
        if existing_count and existing_count != len(page_rows):
            raise ValueError(
                f"{run_dir.name} page {page}: existing {existing_count} rows, "
                f"incoming {len(page_rows)} rows"
            )
        for page_order, (csv_row, row) in enumerate(page_rows, start=1):
            extracted_row_id = stable_id("extracted_row", page_extraction_id, page_order)
            label_raw = text(row.get("label_raw"))
            debit_raw = text(row.get("debit_raw"))
            credit_raw = text(row.get("credit_raw"))
            debit_state, debit_minor = value_state_and_minor(
                debit_raw, text(row.get("debit_parse_status")), text(row.get("debit_amount"))
            )
            credit_state, credit_minor = value_state_and_minor(
                credit_raw, text(row.get("credit_parse_status")), text(row.get("credit_amount"))
            )
            values = {
                "extracted_row_id": extracted_row_id,
                "page_extraction_id": page_extraction_id,
                "document_page_id": page_ids[page],
                "run_id": run_id,
                "row_disposition": "unassigned",
                "statement_id": None,
                "statement_extraction_id": None,
                "page_row_order": page_order,
                "statement_order": None,
                "label_raw": label_raw or None,
                "label_normalized": text(row.get("label_normalized")) or None,
                "label_language": label_language(label_raw or text(row.get("label_normalized"))),
                "debit_raw": debit_raw or None,
                "credit_raw": credit_raw or None,
                "debit_parsed_minor": debit_minor,
                "credit_parsed_minor": credit_minor,
                "debit_value_state": debit_state,
                "credit_value_state": credit_state,
                "row_type_proposed": row_type_for(row.get("label_role") or ""),
                "parent_extracted_row_id": None,
                "parent_context_status": parent_status_for(
                    row.get("label_role") or "", row.get("parent_label_raw") or ""
                ),
                "indent_level": None,
                "row_band_y0": maybe_float(row.get("row_band_y0")),
                "row_band_y1": maybe_float(row.get("row_band_y1")),
                "label_bbox_x0": maybe_float(row.get("label_bbox_x0")),
                "label_bbox_y0": maybe_float(row.get("label_bbox_y0")),
                "label_bbox_x1": maybe_float(row.get("label_bbox_x1")),
                "label_bbox_y1": maybe_float(row.get("label_bbox_y1")),
                "debit_bbox_x0": maybe_float(row.get("debit_bbox_x0")),
                "debit_bbox_y0": maybe_float(row.get("debit_bbox_y0")),
                "debit_bbox_x1": maybe_float(row.get("debit_bbox_x1")),
                "debit_bbox_y1": maybe_float(row.get("debit_bbox_y1")),
                "credit_bbox_x0": maybe_float(row.get("credit_bbox_x0")),
                "credit_bbox_y0": maybe_float(row.get("credit_bbox_y0")),
                "credit_bbox_x1": maybe_float(row.get("credit_bbox_x1")),
                "credit_bbox_y1": maybe_float(row.get("credit_bbox_y1")),
                "row_confidence": maybe_float(row.get("row_confidence")),
                "row_payload_json": payload_for_row(row, run_dir, csv_row),
                "created_at": now(),
            }
            if insert_if_absent(conn, "extracted_rows", "extracted_row_id", values):
                inserted += 1
            else:
                skipped += 1

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "issue_key": issue_key,
        "gazette_issue_id": gazette_issue_id,
        "source_document_id": source_document_id,
        "pages": len(page_numbers),
        "page_extractions": len(page_extraction_ids),
        "pdf_pages": pdf_pages,
        "ocr_pages_observed": coverage["ocr_pages_observed"],
        "ocr_completed_pages": coverage["ocr_completed_pages"],
        "ocr_skipped_pages": coverage["ocr_skipped_pages"],
        "ocr_failed_pages": coverage["ocr_failed_pages"],
        "ocr_page_coverage": coverage["ocr_page_coverage"],
        "run_status": run_status or (
            str(existing_run["run_status"]) if existing_run is not None else "completed"
        ),
        "rows_inserted": inserted,
        "rows_skipped": skipped,
        "already_present": existing_run is not None and inserted == 0,
        "evidence_fingerprint": fingerprint,
    }

def first_value(rows: Iterable[dict[str, str]], column: str) -> str:
    for row in rows:
        value = text(row.get(column))
        if value:
            return value
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--v3-db", "--db", dest="db", type=Path, default=DEFAULT_DB,
        help="V3 database (--db is a deprecated alias)",
    )
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--run-dir", type=Path, action="append", default=[])
    parser.add_argument("--v5-db", type=Path, default=DEFAULT_V5)
    parser.add_argument(
        "--no-v5-lookup", action="store_true",
        help="never open the V5 database for legacy run IDs",
    )
    parser.add_argument("--issue-key", default=None)
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"v3 database not found: {db_path}")
    run_dirs = (
        [path.expanduser().resolve() for path in args.run_dir]
        if args.run_dir
        else sorted(path for path in args.runs_dir.glob("issue*_*") if path.is_dir())
    )
    if not run_dirs:
        parser.error("no run directories found")

    legacy_runs = (
        {} if args.no_v5_lookup
        else load_legacy_runs(args.v5_db.expanduser().resolve())
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    log_rows: list[dict[str, Any]] = []
    try:
        for run_dir in run_dirs:
            try:
                result = replay_run(
                    conn,
                    run_dir,
                    legacy_runs=legacy_runs,
                    issue_key_override=args.issue_key,
                )
                result["status"] = (
                    "already_present" if result.get("already_present") else "ok"
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001 - keep per-run failures visible
                conn.rollback()
                result = {
                    "run_id": "",
                    "run_dir": str(run_dir),
                    "issue_key": "",
                    "pages": 0,
                    "page_extractions": 0,
                    "rows_inserted": 0,
                    "rows_skipped": 0,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            log_rows.append(result)
            print(json.dumps(result, ensure_ascii=False))

        if all(row["status"] in ("ok", "already_present") for row in log_rows):
            conn.execute(
                "INSERT INTO schema_meta(key, value, updated_at) VALUES('last_successful_replay_at', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (now(), now()),
            )
            conn.commit()
    finally:
        conn.close()

    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "run_id", "run_dir", "issue_key", "pages", "page_extractions",
                    "pdf_pages", "ocr_pages_observed", "ocr_completed_pages",
                    "ocr_skipped_pages", "ocr_failed_pages", "ocr_page_coverage",
                    "run_status", "rows_inserted", "rows_skipped",
                    "already_present", "evidence_fingerprint", "status", "error",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(log_rows)
    return 0 if all(row["status"] in ("ok", "already_present") for row in log_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
