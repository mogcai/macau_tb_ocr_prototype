"""Workbench intake: classify imported pages and create logical statements.

The V3 importer stops at raw OCR evidence.  This module is the next controlled
step:

1. review each imported page and approve/reject its page role;
2. for financial-statement pages, record the reporting entity, statement date
   and statement type;
3. build `statements`, `statement_extractions`, `statement_pages` and
   `statement_rows` from the approved pages, and assign the raw rows;
4. run the existing first-pass Standard Item auto-map (proposed mappings only).

It reuses the identity/fingerprint rules of `tools/build_statement_segmentation.py`
but does not depend on the legacy V5 statements, so it works for new issues.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.config import ROOT
from workbench.db import connect_v3

SCHEMA_FILE = ROOT / "database" / "v3" / "017_page_statement_decisions.sql"
AUTO_MAP = ROOT / "tools" / "p1_auto_map_standard_items.py"
V5_SEED = ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite"
NAMESPACE = uuid.UUID("d3f7c7b9-6b5e-5a4e-9d2e-8a1c2f3b4d55")
VERSION = "workbench-intake-1"

PAGE_ROLES = {
    "financial_statement", "audit_report", "shareholder_list",
    "cover", "contents", "other", "unknown",
}
STATEMENT_TYPES = {
    "balance_sheet", "income_statement", "cash_flow",
    "other_financial_statement", "unknown",
}
SCOPE_BY_ENTITY_TYPE = {
    "bank": "bank",
    "branch": "branch",
    "banking_group": "group",
    "non_bank": "other",
}

_initialised: set[str] = set()
_date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IntakeError(ValueError):
    """Invalid intake decision or build request."""

    status_code = 400


class IntakeConflict(ValueError):
    status_code = 409


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def _target_db_path() -> Path:
    from workbench import db as workbench_db

    return Path(workbench_db.V3_DB).resolve()


def ensure_schema() -> None:
    key = str(_target_db_path())
    if key in _initialised:
        return
    conn = connect_v3(write=True)
    try:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    _initialised.add(key)


def _conn() -> sqlite3.Connection:
    ensure_schema()
    return connect_v3(write=True)


def approved_entities() -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        rows = conn.execute(
            "SELECT entity_id, entity_key, canonical_zh, canonical_pt, "
            "entity_type FROM reporting_entities "
            "WHERE registry_status = 'approved' "
            "ORDER BY COALESCE(canonical_zh, canonical_pt, entity_key)"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect_v3()
    try:
        rows = conn.execute(
            """
            WITH stats AS (
                SELECT dp.source_document_id,
                       COUNT(*) AS pages,
                       SUM(CASE WHEN dp.classification_status = 'proposed'
                                THEN 1 ELSE 0 END) AS proposed_pages,
                       SUM(CASE WHEN dp.page_role = 'financial_statement'
                                 AND dp.classification_status = 'approved'
                                THEN 1 ELSE 0 END) AS approved_financial_pages,
                       SUM(CASE WHEN dp.classification_status = 'rejected'
                                THEN 1 ELSE 0 END) AS rejected_pages
                FROM document_pages dp
                GROUP BY dp.source_document_id
            ),
            rowstats AS (
                SELECT dp.source_document_id,
                       SUM(CASE WHEN er.row_disposition = 'unassigned' THEN 1 ELSE 0 END)
                           AS unassigned_rows,
                       SUM(CASE WHEN er.row_disposition = 'included' THEN 1 ELSE 0 END)
                           AS included_rows
                FROM extracted_rows er
                JOIN document_pages dp ON dp.document_page_id = er.document_page_id
                GROUP BY dp.source_document_id
            )
            SELECT sd.source_document_id, gi.issue_key, gi.publication_date,
                   sd.original_filename, sd.page_count,
                   COALESCE(stats.pages, 0) AS pages,
                   COALESCE(stats.proposed_pages, 0) AS proposed_pages,
                   COALESCE(stats.approved_financial_pages, 0) AS approved_financial_pages,
                   COALESCE(stats.rejected_pages, 0) AS rejected_pages,
                   COALESCE(rowstats.unassigned_rows, 0) AS unassigned_rows,
                   COALESCE(rowstats.included_rows, 0) AS included_rows
            FROM source_documents sd
            JOIN gazette_issues gi ON gi.gazette_issue_id = sd.gazette_issue_id
            LEFT JOIN stats ON stats.source_document_id = sd.source_document_id
            LEFT JOIN rowstats ON rowstats.source_document_id = sd.source_document_id
            WHERE COALESCE(stats.proposed_pages, 0) > 0
               OR COALESCE(rowstats.unassigned_rows, 0) > 0
            ORDER BY gi.issue_key DESC, sd.original_filename
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _page_rows(conn: sqlite3.Connection, source_document_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT dp.document_page_id, dp.pdf_page, dp.printed_page, dp.page_role,
               dp.classification_status, dp.classification_source,
               dp.classified_by, dp.classified_at, dp.notes,
               psd.reporting_entity_id, psd.statement_date, psd.statement_type,
               psd.note AS decision_note,
               (SELECT COUNT(*) FROM extracted_rows er
                 WHERE er.document_page_id = dp.document_page_id
                   AND er.row_disposition = 'unassigned') AS unassigned_rows,
               (SELECT COUNT(*) FROM extracted_rows er
                 WHERE er.document_page_id = dp.document_page_id
                   AND er.row_disposition = 'included') AS included_rows,
               (SELECT COUNT(*) FROM extracted_rows er
                 WHERE er.document_page_id = dp.document_page_id) AS total_rows,
               (SELECT GROUP_CONCAT(label, ' | ') FROM (
                    SELECT label_raw AS label FROM extracted_rows er
                    WHERE er.document_page_id = dp.document_page_id
                    ORDER BY er.page_row_order LIMIT 4)) AS sample_labels
        FROM document_pages dp
        LEFT JOIN page_statement_decisions psd
               ON psd.document_page_id = dp.document_page_id
        WHERE dp.source_document_id = ?
        ORDER BY dp.pdf_page
        """,
        (source_document_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _document(conn: sqlite3.Connection, source_document_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT sd.source_document_id, sd.original_filename, sd.page_count,
               sd.sha256, gi.issue_key, gi.publication_date,
               gi.gazette_issue_id
        FROM source_documents sd
        JOIN gazette_issues gi ON gi.gazette_issue_id = sd.gazette_issue_id
        WHERE sd.source_document_id = ?
        """,
        (source_document_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"source document not found: {source_document_id}")
    return dict(row)


def _entity_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT entity_id, entity_key, canonical_zh, canonical_pt, entity_type, "
        "registry_status, merged_into_entity_id FROM reporting_entities"
    ).fetchall()
    return {row["entity_id"]: row for row in rows}


def _norm_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\u3000|·•.,;:()（）\[\]【】\-—–/]+", "", text)


def _entity_index(conn: sqlite3.Connection) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT entity_id, canonical_zh, canonical_pt, canonical_en "
        "FROM reporting_entities WHERE registry_status = 'approved'"
    ):
        for value in (row["canonical_zh"], row["canonical_pt"], row["canonical_en"]):
            key = _norm_name(value)
            if key:
                index.setdefault(key, set()).add(row["entity_id"])
    for row in conn.execute(
        "SELECT a.raw_name, a.entity_id FROM reporting_entity_aliases a "
        "JOIN reporting_entities e ON e.entity_id = a.entity_id "
        "WHERE e.registry_status = 'approved' "
        "AND a.status <> 'rejected' AND a.status <> 'retired'"
    ):
        key = _norm_name(row["raw_name"])
        if key:
            index.setdefault(key, set()).add(row["entity_id"])
    return index


def _match_entity(
    raw_name: str,
    index: dict[str, set[str]],
    entities: dict[str, sqlite3.Row],
) -> tuple[str, float, str] | None:
    """Match a PDF bank name to one approved entity.

    Tries each ``|``-separated part, prefers a unique exact normalised match,
    then a fuzzy match with a clear margin.  Ambiguous matches are rejected so
    the reviewer has to choose.
    """
    best: tuple[str, float, str] | None = None
    for part in re.split(r"\|", str(raw_name or "")):
        key = _norm_name(part)
        if not key:
            continue
        exact = index.get(key)
        if exact and len(exact) == 1:
            return next(iter(exact)), 1.0, "exact"
        scored: list[tuple[float, set[str]]] = []
        for candidate, entity_ids in index.items():
            score = difflib.SequenceMatcher(None, key, candidate).ratio()
            if score >= 0.85:
                scored.append((score, entity_ids))
        if not scored:
            continue
        scored.sort(key=lambda item: item[0], reverse=True)
        score, entity_ids = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if len(entity_ids) == 1 and score - runner_up >= 0.03:
            entity_id = next(iter(entity_ids))
            if entity_id in entities and (
                best is None or score > best[1]
            ):
                best = (entity_id, round(score, 3), "fuzzy")
    return best


def _run_documents(conn: sqlite3.Connection, source_document_id: str) -> dict[int, dict[str, str]]:
    """Read documents.csv for the latest run; it holds PDF-text bank names."""
    row = conn.execute(
        "SELECT run_report_uri, config_json FROM extraction_runs "
        "WHERE source_document_id = ? ORDER BY rowid DESC LIMIT 1",
        (source_document_id,),
    ).fetchone()
    if row is None:
        return {}
    run_dir: Path | None = None
    if row["run_report_uri"]:
        run_dir = Path(row["run_report_uri"]).parent
    if run_dir is None and row["config_json"]:
        try:
            run_dir = Path(json.loads(row["config_json"]).get("run_dir") or "")
        except (ValueError, TypeError):
            run_dir = None
    if not run_dir:
        return {}
    path = run_dir / "documents.csv"
    if not path.is_file():
        return {}
    result: dict[int, dict[str, str]] = {}
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for item in csv.DictReader(handle):
                try:
                    page = int(item.get("pdf_page") or 0)
                except ValueError:
                    continue
                if page:
                    result[page] = {
                        "bank_name": (item.get("bank_name") or "").strip(),
                        "statement_date": (item.get("statement_date") or "").strip(),
                        "ocr_status": (item.get("ocr_status") or "").strip(),
                        "skip_reason": (item.get("skip_reason") or "").strip(),
                    }
    except OSError:
        return {}
    return result


def _suggestions(
    conn: sqlite3.Connection, source_document_id: str
) -> dict[int, dict[str, Any]]:
    documents = _run_documents(conn, source_document_id)
    if not documents:
        return {}
    entities = _entity_map(conn)
    index = _entity_index(conn)
    suggestions: dict[int, dict[str, Any]] = {}
    for page, item in documents.items():
        bank_name = item.get("bank_name", "")
        date = item.get("statement_date", "")
        if not _date_re.fullmatch(date or ""):
            date = ""
        match = _match_entity(bank_name, index, entities) if bank_name else None
        suggestion: dict[str, Any] = {
            "pdf_bank_name": bank_name or None,
            "pdf_statement_date": date or None,
            "suggested_entity_id": None,
            "suggested_entity_name": None,
            "suggested_score": None,
            "suggested_method": None,
            "ocr_status": item.get("ocr_status") or None,
            "skip_reason": item.get("skip_reason") or None,
        }
        if match is not None:
            entity_id, score, method = match
            entity = entities.get(entity_id)
            suggestion.update({
                "suggested_entity_id": entity_id,
                "suggested_entity_name": (
                    entity["canonical_zh"] or entity["canonical_pt"]
                    or entity["entity_key"]
                ) if entity is not None else entity_id,
                "suggested_score": score,
                "suggested_method": method,
            })
        suggestions[page] = suggestion
    return suggestions


def _group_pages(
    document: dict[str, Any], pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for page in pages:
        if (
            page["page_role"] != "financial_statement"
            or page["classification_status"] != "approved"
        ):
            continue
        if not page["reporting_entity_id"] or not page["statement_date"]:
            raise IntakeError(
                f"page {page['pdf_page']} is an approved financial statement "
                "but has no entity/statement date decision"
            )
        key = (
            page["reporting_entity_id"],
            page["statement_date"],
            page["statement_type"] or "balance_sheet",
        )
        groups.setdefault(key, []).append(page)
    result = []
    for (entity_id, statement_date, statement_type), group in groups.items():
        group.sort(key=lambda item: item["pdf_page"])
        result.append({
            "entity_id": entity_id,
            "statement_date": statement_date,
            "statement_type": statement_type,
            "pages": group,
            "page_numbers": [item["pdf_page"] for item in group],
            "row_count": sum(item["total_rows"] for item in group),
        })
    result.sort(key=lambda item: item["pages"][0]["pdf_page"])
    return result


def batch_detail(source_document_id: str) -> dict[str, Any]:
    ensure_schema()
    conn = connect_v3()
    try:
        document = _document(conn, source_document_id)
        pages = _page_rows(conn, source_document_id)
        suggestions = _suggestions(conn, source_document_id)
    finally:
        conn.close()
    for page in pages:
        suggestion = suggestions.get(page["pdf_page"], {})
        page["pdf_bank_name"] = suggestion.get("pdf_bank_name")
        page["pdf_statement_date"] = suggestion.get("pdf_statement_date")
        page["suggested_entity_id"] = suggestion.get("suggested_entity_id")
        page["suggested_entity_name"] = suggestion.get("suggested_entity_name")
        page["suggested_score"] = suggestion.get("suggested_score")
        page["suggested_method"] = suggestion.get("suggested_method")
    pending = [page for page in pages if page["classification_status"] == "proposed"]
    preview: list[dict[str, Any]] = []
    preview_error = None
    try:
        preview = _group_pages(document, pages)
    except IntakeError as exc:
        preview_error = str(exc)
    entities = approved_entities()
    names = {
        entity["entity_id"]: entity.get("canonical_zh")
        or entity.get("canonical_pt")
        or entity["entity_key"]
        for entity in entities
    }
    for group in preview:
        group["entity_name"] = names.get(group["entity_id"], group["entity_id"])
    suggested_ids = {
        page["suggested_entity_id"]
        for page in pages
        if page["suggested_entity_id"]
    }
    unmatched = [
        page["pdf_page"] for page in pages
        if page["classification_status"] == "proposed"
        and page["total_rows"] > 0
        and not page["suggested_entity_id"]
    ]
    return {
        "document": document,
        "pages": pages,
        "pending_pages": len(pending),
        "entities": entities,
        "preview": preview,
        "preview_error": preview_error,
        "can_build": bool(preview) and not pending and not preview_error,
        "suggestion_summary": {
            "suggested_pages": sum(
                1 for page in pages if page["suggested_entity_id"]
            ),
            "suggested_entities": len(suggested_ids),
            "unmatched_pages": unmatched,
            "source": "documents.csv (PDF text)" if suggestions else None,
        },
    }


def _audit(
    conn: sqlite3.Connection,
    event_id: str,
    event_type: str,
    actor: str,
    *,
    source_document_id: str | None = None,
    document_page_id: str | None = None,
    entity_id: str | None = None,
    statement_id: str | None = None,
    statement_extraction_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    reason: str = "",
) -> None:
    if conn.execute(
        "SELECT 1 FROM audit_events WHERE audit_event_id = ?", (event_id,)
    ).fetchone():
        return
    conn.execute(
        "INSERT INTO audit_events("
        "audit_event_id, event_type, source_document_id, document_page_id, "
        "entity_id, statement_id, statement_extraction_id, actor_id, "
        "before_json, after_json, details_json, reason, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id, event_type, source_document_id, document_page_id,
            entity_id, statement_id, statement_extraction_id, actor,
            json.dumps(before, ensure_ascii=False, sort_keys=True) if before else None,
            json.dumps(after, ensure_ascii=False, sort_keys=True) if after else None,
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            reason, now(),
        ),
    )


def save_page_decision(
    source_document_id: str,
    document_page_id: str,
    *,
    page_role: str,
    decision: str,
    actor: str,
    entity_id: str | None = None,
    statement_date: str | None = None,
    statement_type: str = "balance_sheet",
    note: str = "",
) -> dict[str, Any]:
    if page_role not in PAGE_ROLES:
        raise IntakeError(f"unknown page role: {page_role}")
    if decision not in ("approve", "reject"):
        raise IntakeError("decision must be approve or reject")
    if statement_type not in STATEMENT_TYPES:
        raise IntakeError(f"unknown statement type: {statement_type}")
    note = str(note or "").strip()
    if decision == "reject" and not note:
        raise IntakeError("a note is required when rejecting a page")

    conn = _conn()
    try:
        page = conn.execute(
            "SELECT * FROM document_pages WHERE document_page_id = ? "
            "AND source_document_id = ?",
            (document_page_id, source_document_id),
        ).fetchone()
        if page is None:
            raise LookupError(f"page not found in document: {document_page_id}")
        assigned = conn.execute(
            "SELECT COUNT(*) FROM extracted_rows WHERE document_page_id = ? "
            "AND row_disposition = 'included'",
            (document_page_id,),
        ).fetchone()[0]
        if assigned:
            raise IntakeConflict(
                "this page is already assigned to a statement; statements are "
                "append-only, create a new run instead of reclassifying it"
            )

        applied_role = "other" if decision == "reject" else page_role
        applied_status = "approved" if decision == "approve" else "rejected"
        entity_id = str(entity_id or "").strip() or None
        statement_date = str(statement_date or "").strip() or None
        if applied_role == "financial_statement" and applied_status == "approved":
            if not entity_id:
                raise IntakeError("an approved financial page needs a reporting entity")
            if not statement_date or not _date_re.fullmatch(statement_date):
                raise IntakeError("statement date must be YYYY-MM-DD")
            try:
                datetime.strptime(statement_date, "%Y-%m-%d")
            except ValueError as exc:
                raise IntakeError("statement date must be a real date") from exc
            entity = conn.execute(
                "SELECT registry_status, merged_into_entity_id FROM reporting_entities "
                "WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            if entity is None:
                raise IntakeError("reporting entity not found")
            if entity["registry_status"] != "approved":
                raise IntakeError("reporting entity is not approved")
        else:
            entity_id = None
            statement_date = None
            statement_type = "balance_sheet"

        decision_note = "; ".join(
            part for part in (
                f"workbench-intake decision={decision}",
                f"note={note}" if note else "",
                f"entity={entity_id}" if entity_id else "",
                f"statement_date={statement_date}" if statement_date else "",
                f"statement_type={statement_type}",
            ) if part
        )
        before = {
            "page_role": page["page_role"],
            "classification_status": page["classification_status"],
            "notes": page["notes"],
        }
        conn.execute(
            """
            UPDATE document_pages
            SET page_role = ?, classification_status = ?, classification_source = 'human',
                classified_by = ?, classified_at = ?, classification_version = ?, notes = ?
            WHERE document_page_id = ?
            """,
            (
                applied_role, applied_status, actor, now(), VERSION,
                decision_note, document_page_id,
            ),
        )
        if entity_id:
            conn.execute(
                """
                INSERT INTO page_statement_decisions(
                    document_page_id, reporting_entity_id, statement_date,
                    statement_type, decided_by, decided_at, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_page_id) DO UPDATE SET
                    reporting_entity_id = excluded.reporting_entity_id,
                    statement_date = excluded.statement_date,
                    statement_type = excluded.statement_type,
                    decided_by = excluded.decided_by,
                    decided_at = excluded.decided_at,
                    note = excluded.note
                """,
                (
                    document_page_id, entity_id, statement_date, statement_type,
                    actor, now(), note or None,
                ),
            )
        else:
            conn.execute(
                "DELETE FROM page_statement_decisions WHERE document_page_id = ?",
                (document_page_id,),
            )
        _audit(
            conn,
            stable_id(
                "page_decision", document_page_id, applied_role, applied_status,
                entity_id or "", statement_date or "", statement_type, note,
            ),
            "page_classified",
            actor,
            source_document_id=source_document_id,
            document_page_id=document_page_id,
            entity_id=entity_id,
            before=before,
            after={
                "page_role": applied_role,
                "classification_status": applied_status,
                "reporting_entity_id": entity_id,
                "statement_date": statement_date,
                "statement_type": statement_type,
            },
            details={"version": VERSION, "decision": decision, "note": note},
            reason=note or f"page classification {applied_status}",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return batch_detail(source_document_id)


def auto_classify(
    source_document_id: str,
    *,
    actor: str,
    classify_empty_pages: bool = True,
) -> dict[str, Any]:
    """Apply PDF-text suggestions to every still-proposed page.

    Pages with a matched entity + statement date become approved financial
    statements.  Pages with no extracted rows can be approved as `other` so the
    batch can be built without clicking through cover pages.  Anything else is
    left for the reviewer and reported back.
    """
    detail = batch_detail(source_document_id)
    counts = {"financial": 0, "empty": 0, "skipped": 0, "unmatched": []}
    for page in detail["pages"]:
        if page["classification_status"] != "proposed":
            counts["skipped"] += 1
            continue
        entity_id = page["suggested_entity_id"]
        statement_date = page["pdf_statement_date"]
        if entity_id and statement_date:
            save_page_decision(
                source_document_id,
                page["document_page_id"],
                page_role="financial_statement",
                decision="approve",
                actor=actor,
                entity_id=entity_id,
                statement_date=statement_date,
                statement_type="balance_sheet",
                note=f"auto: PDF text match {page['pdf_bank_name'] or ''}".strip(),
            )
            counts["financial"] += 1
        elif classify_empty_pages and page["total_rows"] == 0:
            save_page_decision(
                source_document_id,
                page["document_page_id"],
                page_role="other",
                decision="approve",
                actor=actor,
                note="auto: non-TB page, no extracted rows",
            )
            counts["empty"] += 1
        else:
            counts["unmatched"].append(page["pdf_page"])
    counts["classified"] = counts["financial"] + counts["empty"]
    counts["detail"] = batch_detail(source_document_id)
    return counts


def _fingerprint(row: sqlite3.Row, document_page_id: str) -> str:
    payload = {
        "document_page_id": document_page_id,
        "page_row_order": row["page_row_order"],
        "label_raw": row["label_raw"],
        "label_normalized": row["label_normalized"],
        "debit_raw": row["debit_raw"],
        "credit_raw": row["credit_raw"],
        "row_type": row["row_type_proposed"],
        "parent_context_status": row["parent_context_status"],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _page_run_id(conn: sqlite3.Connection, document_page_id: str) -> str:
    row = conn.execute(
        "SELECT run_id FROM page_extractions WHERE document_page_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (document_page_id,),
    ).fetchone()
    if row is None:
        raise IntakeError(f"page has no OCR extraction: {document_page_id}")
    return row["run_id"]


def build_statements(source_document_id: str, actor: str) -> dict[str, Any]:
    ensure_schema()
    conn = _conn()
    summary: dict[str, Any] = {
        "statements_created": 0,
        "statements_existing": 0,
        "rows_assigned": 0,
        "rows_excluded": 0,
        "statements": [],
    }
    try:
        document = _document(conn, source_document_id)
        pages = _page_rows(conn, source_document_id)
        pending = [page["pdf_page"] for page in pages
                   if page["classification_status"] == "proposed"]
        if pending:
            raise IntakeError(
                "classify every page before building statements; pending pages: "
                + ", ".join(str(page) for page in pending[:20])
            )
        groups = _group_pages(document, pages)
        if not groups:
            raise IntakeError("no approved financial-statement pages to build")
        entities = _entity_map(conn)

        for group in groups:
            entity = entities.get(group["entity_id"])
            if entity is None or entity["registry_status"] != "approved":
                raise IntakeError("reporting entity is missing or not approved")
            scope = SCOPE_BY_ENTITY_TYPE.get(entity["entity_type"], "other")
            identity_key = (
                f"{entity['entity_key']}|{group['statement_date']}|"
                f"{group['statement_type']}|{scope}"
            )
            statement_id = stable_id(
                "statement", source_document_id, identity_key
            )
            run_ids = {
                _page_run_id(conn, page["document_page_id"])
                for page in group["pages"]
            }
            if len(run_ids) != 1:
                raise IntakeError(
                    f"pages {group['page_numbers']} come from different OCR runs"
                )
            run_id = run_ids.pop()
            statement_extraction_id = stable_id(
                "statement_extraction", statement_id, run_id
            )
            existing = conn.execute(
                "SELECT 1 FROM statements WHERE statement_id = ?", (statement_id,)
            ).fetchone()
            if existing:
                summary["statements_existing"] += 1
            else:
                conn.execute(
                    """
                    INSERT INTO statements(
                        statement_id, source_document_id, reporting_entity_id,
                        entity_alias_id, entity_name_raw, entity_match_method,
                        entity_match_score, gazette_issue_id, publication_date,
                        publication_date_source, statement_date, statement_date_source,
                        statement_type, statement_type_source, reporting_scope,
                        currency, currency_source, unit_scale, unit_scale_source,
                        amount_decimal_places, identity_key, identity_status,
                        metadata_status, statement_status, created_at, notes
                    ) VALUES (?, ?, ?, NULL, ?, 'manual', 1.0, ?, ?, 'issue_metadata',
                              ?, 'manual', ?, 'page_classification', ?,
                              'MOP', 'manual', 1, 'manual', 2, ?,
                              'proposed', 'proposed', 'proposed', ?, ?)
                    """,
                    (
                        statement_id, source_document_id, entity["entity_id"],
                        entity["canonical_zh"] or entity["canonical_pt"]
                        or entity["entity_key"],
                        document["gazette_issue_id"],
                        document["publication_date"],
                        group["statement_date"],
                        group["statement_type"],
                        scope,
                        identity_key,
                        now(),
                        f"Workbench intake; version={VERSION}; "
                        f"pages={group['page_numbers']}",
                    ),
                )
                summary["statements_created"] += 1
                _audit(
                    conn,
                    stable_id("statement_segmented", statement_id, VERSION),
                    "statement_segmented",
                    actor,
                    source_document_id=source_document_id,
                    entity_id=entity["entity_id"],
                    statement_id=statement_id,
                    details={
                        "pages": group["page_numbers"],
                        "row_count": group["row_count"],
                        "identity_key": identity_key,
                        "version": VERSION,
                    },
                    reason="Workbench intake statement creation",
                )

            if not conn.execute(
                "SELECT 1 FROM statement_extractions WHERE statement_extraction_id = ?",
                (statement_extraction_id,),
            ).fetchone():
                conn.execute(
                    """
                    INSERT INTO statement_extractions(
                        statement_extraction_id, statement_id, run_id,
                        source_document_id, extraction_status, page_range_json,
                        created_at, notes
                    ) VALUES (?, ?, ?, ?, 'completed', ?, ?, ?)
                    """,
                    (
                        statement_extraction_id, statement_id, run_id,
                        source_document_id,
                        json.dumps(group["page_numbers"]),
                        now(),
                        f"Workbench intake; version={VERSION}",
                    ),
                )

            for page_order, page in enumerate(group["pages"], start=1):
                page_id = page["document_page_id"]
                if not conn.execute(
                    "SELECT 1 FROM statement_pages WHERE statement_id = ? "
                    "AND document_page_id = ?",
                    (statement_id, page_id),
                ).fetchone():
                    conn.execute(
                        """
                        INSERT INTO statement_pages(
                            statement_id, source_document_id, document_page_id,
                            page_order, is_primary, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            statement_id, source_document_id, page_id, page_order,
                            1 if page_order == 1 else 0, now(),
                        ),
                    )
                rows = conn.execute(
                    "SELECT * FROM extracted_rows WHERE document_page_id = ? "
                    "ORDER BY page_row_order",
                    (page_id,),
                ).fetchall()
                for row in rows:
                    statement_order = (page_order - 1) * 100000 + int(row["page_row_order"])
                    current = conn.execute(
                        "SELECT statement_id, row_disposition FROM extracted_rows "
                        "WHERE extracted_row_id = ?",
                        (row["extracted_row_id"],),
                    ).fetchone()
                    if current["statement_id"] not in (None, statement_id):
                        raise IntakeConflict(
                            f"row {row['extracted_row_id']} is already assigned "
                            f"to another statement"
                        )
                    if current["row_disposition"] != "included":
                        conn.execute(
                            "UPDATE extracted_rows SET row_disposition='included', "
                            "statement_id=?, statement_extraction_id=?, statement_order=? "
                            "WHERE extracted_row_id=?",
                            (
                                statement_id, statement_extraction_id,
                                statement_order, row["extracted_row_id"],
                            ),
                        )
                        summary["rows_assigned"] += 1
                    statement_row_id = stable_id(
                        "statement_row", statement_extraction_id, row["extracted_row_id"]
                    )
                    if not conn.execute(
                        "SELECT 1 FROM statement_rows WHERE statement_row_id = ?",
                        (statement_row_id,),
                    ).fetchone():
                        parent_status = row["parent_context_status"]
                        if parent_status == "identified":
                            parent_status = "unknown"
                        conn.execute(
                            """
                            INSERT INTO statement_rows(
                                statement_row_id, statement_id, statement_extraction_id,
                                extracted_row_id, parent_row_id, statement_order, row_type,
                                parent_context_status, label_normalized, label_language,
                                debit_minor, debit_value_state, credit_minor,
                                credit_value_state, row_fingerprint, fingerprint_version,
                                carry_forward_status, created_at
                            ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'none', ?)
                            """,
                            (
                                statement_row_id, statement_id,
                                statement_extraction_id, row["extracted_row_id"],
                                statement_order, row["row_type_proposed"],
                                parent_status,
                                row["label_normalized"], row["label_language"],
                                row["debit_parsed_minor"], row["debit_value_state"],
                                row["credit_parsed_minor"], row["credit_value_state"],
                                _fingerprint(row, page_id), now(),
                            ),
                        )
            summary["statements"].append({
                "statement_id": statement_id,
                "entity_name": entity["canonical_zh"] or entity["canonical_pt"],
                "statement_date": group["statement_date"],
                "pages": group["page_numbers"],
                "rows": group["row_count"],
            })

        # Every non-financial or rejected page keeps its OCR evidence but its
        # rows are no longer "unassigned".
        excluded_pages = conn.execute(
            "SELECT document_page_id, page_role, classification_status "
            "FROM document_pages WHERE source_document_id = ? "
            "AND (page_role <> 'financial_statement' OR classification_status <> 'approved')",
            (source_document_id,),
        ).fetchall()
        for page in excluded_pages:
            updated = conn.execute(
                "UPDATE extracted_rows SET row_disposition='excluded_page_role' "
                "WHERE document_page_id = ? AND row_disposition = 'unassigned'",
                (page["document_page_id"],),
            ).rowcount
            summary["rows_excluded"] += updated
            _audit(
                conn,
                stable_id("page_rows_excluded", page["document_page_id"], VERSION),
                "page_rows_excluded",
                actor,
                source_document_id=source_document_id,
                document_page_id=page["document_page_id"],
                details={
                    "rows_excluded": updated,
                    "page_role": page["page_role"],
                    "classification_status": page["classification_status"],
                    "version": VERSION,
                },
                reason="page is not an approved financial statement",
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    summary["mapping"] = _auto_map(source_document_id)
    return summary


def _auto_map(source_document_id: str) -> dict[str, Any]:
    if not AUTO_MAP.is_file() or not V5_SEED.is_file():
        return {"status": "skipped", "reason": "auto-map tool or V5 seed missing"}
    out = ROOT / "outputs" / "intake" / f"{source_document_id}_mapping_review.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(AUTO_MAP),
        "--v3-db", str(_target_db_path()),
        "--v5-db", str(V5_SEED),
        "--out", str(out),
        "--apply",
    ]
    try:
        completed = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True,
            check=False, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    if completed.returncode != 0:
        return {
            "status": "failed",
            "error": (completed.stderr or completed.stdout).strip()[-1000:],
        }
    for line in reversed([item for item in completed.stdout.splitlines() if item.strip()]):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        return {"status": "completed", **parsed}
    return {"status": "completed"}
