#!/usr/bin/env python3
"""Build the P2-A page classification decision worksheet.

This tool is read-only.  It reads v3 raw replay evidence and v5 legacy
statements as evidence, then writes a CSV for human review.  It does not update
``document_pages`` and it never approves a page role.

Usage::

    python tools/build_page_classification_worksheet.py \
        --db data/macau_balancete_v3.sqlite \
        --v5-db data/baseline/baseline_v5_20261118.sqlite \
        --out docs/audit/page_classification_decisions.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_V5 = ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite"
DEFAULT_OUT = ROOT / "docs" / "audit" / "page_classification_decisions.csv"

SHAREHOLDER_RE = re.compile(r"lista\s+de\s+detentores|accionistas|shareholder", re.I)
AUDIT_RE = re.compile(r"relat[oó]rio\s+do\s+auditor|auditor\s+independente|audit\s+report", re.I)
NON_BANK_RE = re.compile(r"南光石油|油庫|合同項目|contrato", re.I)
INCOME_RE = re.compile(
    r"demonstra[cç][aã]o\s+de\s+resultados|income\s+statement|"
    r"profit\s+and\s+loss|損益表|收益表",
    re.I,
)
BALANCE_RE = re.compile(
    r"posi[cç][aã]o\s+financeira|financial\s+position|balancete|"
    r"trial\s+balance|試算表|資產負債",
    re.I,
)
JUNK_HEADER_RE = re.compile(
    r"#\s*\d|\bDEMONSTRA|Lista\s+de\s+detentores|Relat[oó]rio\s+do\s+Auditor|"
    r"\bNenhum\b|^\s*\d{2,}",
    re.I,
)
ENTITY_HINT_RE = re.compile(r"banco|bank|銀行|有限公司|s\.a\.|sucursal|分行", re.I)


def connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def first_labels_by_page(conn: sqlite3.Connection, limit: int = 8) -> dict[tuple[str, int], list[str]]:
    result: dict[tuple[str, int], list[str]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT er.run_id, dp.pdf_page, er.label_raw
        FROM extracted_rows er
        JOIN document_pages dp ON dp.document_page_id = er.document_page_id
        WHERE er.label_raw IS NOT NULL AND er.label_raw <> ''
        ORDER BY er.run_id, dp.pdf_page, er.page_row_order
        """
    ):
        key = (row["run_id"], int(row["pdf_page"]))
        if len(result[key]) < limit:
            result[key].append(str(row["label_raw"]))
    return result


def v3_page_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            dp.document_page_id,
            dp.source_document_id,
            dp.pdf_page,
            dp.printed_page,
            gi.issue_key,
            r.run_id,
            pe.ocr_status,
            pe.number_format_detected,
            COUNT(er.extracted_row_id) AS row_count,
            SUM(CASE WHEN er.debit_value_state = 'amount'
                      OR er.credit_value_state = 'amount' THEN 1 ELSE 0 END) AS amount_row_count
        FROM document_pages dp
        JOIN source_documents sd ON sd.source_document_id = dp.source_document_id
        JOIN gazette_issues gi ON gi.gazette_issue_id = sd.gazette_issue_id
        JOIN extraction_runs r ON r.source_document_id = dp.source_document_id
        JOIN page_extractions pe
          ON pe.document_page_id = dp.document_page_id
         AND pe.run_id = r.run_id
        LEFT JOIN extracted_rows er
          ON er.document_page_id = dp.document_page_id
         AND er.run_id = r.run_id
        GROUP BY dp.document_page_id, dp.source_document_id, dp.pdf_page,
                 dp.printed_page, gi.issue_key, r.run_id, pe.ocr_status,
                 pe.number_format_detected
        ORDER BY gi.issue_key, dp.pdf_page
        """
    ).fetchall()
    return [dict(row) for row in rows]


def v5_page_metadata(conn: sqlite3.Connection) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT p.run_id, p.pdf_page, p.bank_name_raw, p.statement_id,
               s.bank_name_raw AS statement_bank_name_raw,
               s.bank_key, s.statement_date, s.publication_date,
               s.row_count, s.identity_status
        FROM pages p
        LEFT JOIN statements s ON s.statement_id = p.statement_id
        """
    ):
        result[(row["run_id"], int(row["pdf_page"]))] = dict(row)
    return result


def v5_first_labels(conn: sqlite3.Connection, limit: int = 8) -> dict[tuple[str, int], list[str]]:
    result: dict[tuple[str, int], list[str]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT run_id, pdf_page, label_raw
        FROM line_items
        WHERE label_raw IS NOT NULL AND label_raw <> ''
        ORDER BY line_item_id
        """
    ):
        key = (row["run_id"], int(row["pdf_page"]))
        if len(result[key]) < limit:
            result[key].append(str(row["label_raw"]))
    return result


def clean_header(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def entity_candidate(header: str, labels: list[str]) -> str:
    candidates: list[str] = []
    for part in re.split(r"\s*\|\s*", header):
        part = clean_header(part)
        if part and not JUNK_HEADER_RE.search(part):
            candidates.append(part)
    if candidates:
        # Prefer an explicit bank/entity hint; otherwise the first clean segment.
        hinted = [part for part in candidates if ENTITY_HINT_RE.search(part)]
        return (hinted or candidates)[0]
    for label in labels:
        text = clean_header(label)
        if text and ENTITY_HINT_RE.search(text) and not JUNK_HEADER_RE.search(text):
            return text
    return ""


def classify(header: str, labels: list[str], row_count: int, amount_rows: int) -> dict[str, Any]:
    text = " ".join([header, *labels])
    entity = entity_candidate(header, labels)
    reasons: list[str] = []
    confidence = 0.55

    if SHAREHOLDER_RE.search(text):
        role = "shareholder_list"
        v3_role = "shareholder_list"
        statement_type = ""
        reasons.append("shareholder_list_terms")
        confidence = 0.9
    elif AUDIT_RE.search(text):
        role = "audit_report"
        v3_role = "audit_report"
        statement_type = ""
        reasons.append("audit_report_terms")
        confidence = 0.9
    elif NON_BANK_RE.search(text):
        role = "other"
        v3_role = "other"
        statement_type = ""
        reasons.append("non_bank_contract_or_project")
        confidence = 0.75
    elif INCOME_RE.search(text):
        role = "income_statement"
        v3_role = "financial_statement"
        statement_type = "income_statement"
        reasons.append("income_statement_terms")
        confidence = 0.75
    elif BALANCE_RE.search(text) or amount_rows >= 5:
        role = "balance_sheet"
        v3_role = "financial_statement"
        statement_type = "balance_sheet"
        reasons.append("balance_sheet_terms" if BALANCE_RE.search(text) else "amount_rows")
        confidence = 0.8
    elif row_count > 0:
        role = "unknown"
        v3_role = "unknown"
        statement_type = ""
        reasons.append("no_reliable_financial_statement_evidence")
        confidence = 0.35
    else:
        role = "unknown"
        v3_role = "unknown"
        statement_type = ""
        reasons.append("no_extracted_rows")
        confidence = 0.2

    if JUNK_HEADER_RE.search(header):
        reasons.append("header_contaminated")
        confidence = min(confidence, 0.55)
    if not entity and role not in ("shareholder_list", "audit_report", "other"):
        reasons.append("entity_unresolved")
        confidence = min(confidence, 0.45)
    if amount_rows == 0 and role == "balance_sheet":
        reasons.append("no_parsed_amounts")
        confidence = min(confidence, 0.5)
    if role in ("balance_sheet", "income_statement") and amount_rows > 0:
        reasons.append("has_parsed_amounts")

    review_status = "needs_review" if (
        role == "unknown"
        or confidence < 0.8
        or "header_contaminated" in reasons
        or "entity_unresolved" in reasons
        or "no_parsed_amounts" in reasons
    ) else "ready_for_review"

    return {
        "proposed_page_role": role,
        "proposed_v3_page_role": v3_role,
        "proposed_statement_type": statement_type,
        "proposed_entity_name": entity,
        "classification_status": "proposed",
        "review_status": review_status,
        "proposed_confidence": round(confidence, 2),
        "review_reason": ";".join(reasons),
    }


def build_worksheet(db: Path, v5_db: Path) -> list[dict[str, Any]]:
    v3 = connect_ro(db)
    v5 = connect_ro(v5_db)
    try:
        pages = v3_page_rows(v3)
        labels3 = first_labels_by_page(v3)
        labels5 = v5_first_labels(v5)
        meta5 = v5_page_metadata(v5)
    finally:
        v3.close()
        v5.close()

    output: list[dict[str, Any]] = []
    for page in pages:
        key = (page["run_id"], int(page["pdf_page"]))
        meta = meta5.get(key, {})
        header = clean_header(
            meta.get("bank_name_raw") or meta.get("statement_bank_name_raw") or ""
        )
        labels = labels3.get(key) or labels5.get(key) or []
        result = classify(
            header,
            labels,
            int(page["row_count"] or 0),
            int(page["amount_row_count"] or 0),
        )
        output.append({
            "document_page_id": page["document_page_id"],
            "issue_number": page["issue_key"],
            "pdf_page": page["pdf_page"],
            "ocr_header_raw": header,
            "proposed_page_role": result["proposed_page_role"],
            "classification_status": result["classification_status"],
            "proposed_entity_name": result["proposed_entity_name"],
            "review_reason": result["review_reason"],
            "reviewer_decision": "",
            "reviewer_note": "",
            "proposed_v3_page_role": result["proposed_v3_page_role"],
            "proposed_statement_type": result["proposed_statement_type"],
            "review_status": result["review_status"],
            "proposed_confidence": result["proposed_confidence"],
            "legacy_statement_id": meta.get("statement_id") or "",
            "legacy_bank_key": meta.get("bank_key") or "",
            "run_id": page["run_id"],
            "source_document_id": page["source_document_id"],
            "printed_page": page["printed_page"] or "",
            "row_count": page["row_count"],
            "amount_row_count": page["amount_row_count"],
            "number_format_detected": page["number_format_detected"] or "",
            "ocr_status": page["ocr_status"] or "",
            "first_labels": " | ".join(labels),
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--v5-db", type=Path, default=DEFAULT_V5)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rows = build_worksheet(args.db.expanduser().resolve(), args.v5_db.expanduser().resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "document_page_id", "issue_number", "pdf_page", "ocr_header_raw",
        "proposed_page_role", "classification_status", "proposed_entity_name",
        "review_reason", "reviewer_decision", "reviewer_note",
        "proposed_v3_page_role", "proposed_statement_type", "review_status",
        "proposed_confidence", "legacy_statement_id", "legacy_bank_key",
        "run_id", "source_document_id", "printed_page", "row_count",
        "amount_row_count", "number_format_detected", "ocr_status", "first_labels",
    ]
    with args.out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} pages to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
