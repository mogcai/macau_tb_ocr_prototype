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


def issue_identity(run_dir: Path) -> tuple[str, str]:
    match = ISSUE_RE.search(run_dir.name)
    if not match:
        raise ValueError(f"cannot parse issue number/year from {run_dir.name}")
    issue, year = match.group(1), match.group(2)
    issue_key = f"{year}-{int(issue):02d}"
    return issue_key, issue_key


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


def get_or_create_document(
    conn: sqlite3.Connection,
    *,
    gazette_issue_id: str,
    issue_key: str,
    source_pdf: str,
    sha256: str,
    page_count: int,
) -> str:
    document_key = f"{issue_key}:{sha256[:16]}"
    existing = conn.execute(
        "SELECT source_document_id, sha256 FROM source_documents WHERE document_key = ?",
        (document_key,),
    ).fetchone()
    if existing:
        if existing["sha256"] != sha256:
            raise ValueError(f"source document hash changed for {document_key}")
        return str(existing["source_document_id"])
    document_id = stable_id("document", document_key, sha256)
    conn.execute(
        """
        INSERT INTO source_documents(
            source_document_id, gazette_issue_id, document_key, source_url,
            original_filename, storage_uri, sha256, page_count, acquired_at, mime_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id, gazette_issue_id, document_key, None,
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
) -> None:
    existing = conn.execute(
        "SELECT source_document_id FROM extraction_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if existing:
        if existing["source_document_id"] != source_document_id:
            raise ValueError(f"run {run_id} already belongs to another document")
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
    conn.execute(
        """
        INSERT INTO extraction_runs(
            run_id, source_document_id, app_version, ocr_engine_versions,
            config_json, started_at, completed_at, run_status, run_report_uri
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 'completed', ?)
        """,
        (
            run_id, source_document_id, text(report.get("app_version")),
            json.dumps({"engine": engine, "version": engine_version}, ensure_ascii=False),
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            str(run_report_path),
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
) -> dict[str, Any]:
    report = read_json(run_dir / "run_report.json")
    documents = read_csv(run_dir / "documents.csv")
    rows_csv = read_csv(run_dir / "tb_rows.csv")
    if not documents and not rows_csv:
        raise ValueError(f"no documents.csv or tb_rows.csv in {run_dir}")

    issue_key, issue_id = issue_identity(run_dir)
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
            "source_url": None,
            "discovered_at": now(),
            "notes": f"replayed from {run_dir.name}",
        },
    )

    source_pdf = text(report.get("source_pdf"))
    sha256 = text(report.get("source_pdf_sha256"))
    if not sha256 and source_pdf and Path(source_pdf).is_file():
        sha256 = sha256_file(Path(source_pdf))
    if not sha256:
        raise ValueError(f"missing source PDF SHA-256 in {run_dir}")

    page_numbers = sorted({
        int(row["pdf_page"])
        for row in [*documents, *rows_csv]
        if text(row.get("pdf_page")).isdigit()
    })
    source_document_id = get_or_create_document(
        conn,
        gazette_issue_id=gazette_issue_id,
        issue_key=issue_key,
        source_pdf=source_pdf or str(run_dir),
        sha256=sha256,
        page_count=len(page_numbers) or maybe_int(report.get("pdf_pages")) or 1,
    )

    run_id = legacy_runs.get(run_dir.name)
    if not run_id:
        run_id = stable_id("run", source_document_id, text(report.get("app_version")), run_dir.name)
    get_or_create_run(
        conn,
        run_id=run_id,
        source_document_id=source_document_id,
        report=report,
        run_report_path=run_dir / "run_report.json",
    )

    document_by_page: dict[int, dict[str, str]] = {}
    for row in documents:
        page = maybe_int(row.get("pdf_page"))
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
        page = maybe_int(row.get("pdf_page"))
        if page is None:
            continue
        rows_by_page[page].append((index, row))

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
        "pages": len(page_numbers),
        "page_extractions": len(page_extraction_ids),
        "rows_inserted": inserted,
        "rows_skipped": skipped,
    }


def first_value(rows: Iterable[dict[str, str]], column: str) -> str:
    for row in rows:
        value = text(row.get(column))
        if value:
            return value
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--run-dir", type=Path, action="append", default=[])
    parser.add_argument("--v5-db", type=Path, default=DEFAULT_V5)
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

    legacy_runs = load_legacy_runs(args.v5_db.expanduser().resolve())
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    log_rows: list[dict[str, Any]] = []
    try:
        for run_dir in run_dirs:
            try:
                result = replay_run(conn, run_dir, legacy_runs=legacy_runs)
                result["status"] = "ok"
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

        if all(row["status"] == "ok" for row in log_rows):
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
                    "rows_inserted", "rows_skipped", "status", "error",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(log_rows)
    return 0 if all(row["status"] == "ok" for row in log_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
