#!/usr/bin/env python3
"""P2-C: create logical statements and assign approved page rows.

This tool is idempotent.  It uses:

* reviewed page classifications (approved financial statements only);
* v5 statement metadata as legacy evidence;
* v3 entities, pages, extractions and extracted rows.

It creates ``statements``, ``statement_pages``, ``statement_extractions`` and
``statement_rows``, and updates ``extracted_rows`` assignment fields.  Raw OCR
fields on ``extracted_rows`` are protected by the 010 assignment trigger.

Default mode is a dry run; ``--apply`` writes the v3 database.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_V5 = ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite"
DEFAULT_REVIEWED = ROOT / "docs" / "audit" / "page_classification_reviewed.csv"
DEFAULT_PLAN = ROOT / "docs" / "audit" / "statement_segmentation_plan.csv"
NAMESPACE = uuid.UUID("d3f7c7b9-6b5e-5a4e-9d2e-8a1c2f3b4d55")
VERSION = "P2-C-1"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def stable_id(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def reporting_scope(entity_type: str) -> str:
    return {
        "bank": "bank",
        "branch": "branch",
        "banking_group": "group",
        "non_bank": "other",
    }.get(entity_type, "other")


def fingerprint(row: sqlite3.Row, document_page_id: str) -> str:
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


def load_entities(v3: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["entity_key"]: dict(row)
        for row in v3.execute(
            "SELECT entity_id, entity_key, canonical_zh, canonical_pt, entity_type, "
            "registry_status, merged_into_entity_id FROM reporting_entities"
        )
    }


def resolve_entity(entities: dict[str, dict[str, Any]], bank_key: str) -> dict[str, Any]:
    entity = entities.get(bank_key)
    if entity is None:
        raise ValueError(f"missing v3 entity for legacy bank_key {bank_key!r}")
    if entity["registry_status"] == "merged":
        survivor_id = entity["merged_into_entity_id"]
        for other in entities.values():
            if other["entity_id"] == survivor_id:
                return other
        raise ValueError(f"merged entity {bank_key} has no survivor")
    if entity["registry_status"] != "approved":
        raise ValueError(f"entity {bank_key} is not approved")
    return entity


def load_pages(v3: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["document_page_id"]: dict(row)
        for row in v3.execute(
            """
            SELECT dp.document_page_id, dp.source_document_id, dp.pdf_page,
                   dp.printed_page, dp.page_role, dp.classification_status,
                   gi.issue_key, r.run_id
            FROM document_pages dp
            JOIN source_documents sd ON sd.source_document_id = dp.source_document_id
            JOIN gazette_issues gi ON gi.gazette_issue_id = sd.gazette_issue_id
            JOIN page_extractions pe ON pe.document_page_id = dp.document_page_id
            JOIN extraction_runs r ON r.run_id = pe.run_id
            """
        )
    }


def load_legacy_statements(v5: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["statement_id"]: dict(row)
        for row in v5.execute(
            """
            SELECT s.statement_id, s.run_id, s.bank_id, s.bank_key,
                   s.bank_name_raw, s.publication_date, s.statement_date,
                   s.pdf_pages, s.page_count, s.identity_status
            FROM statements s
            """
        )
    }


def build_plan(db: Path, v5_db: Path, reviewed_csv: Path) -> list[dict[str, Any]]:
    v3 = connect_ro(db)
    v5 = connect_ro(v5_db)
    try:
        entities = load_entities(v3)
        pages = load_pages(v3)
        legacy = load_legacy_statements(v5)
        rows_by_page: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in v3.execute(
            "SELECT * FROM extracted_rows ORDER BY page_extraction_id, page_row_order"
        ):
            rows_by_page[row["document_page_id"]].append(row)
    finally:
        v3.close()
        v5.close()

    reviewed = [
        row for row in read_csv(reviewed_csv)
        if text(row.get("applied_classification_status")) == "approved"
        and text(row.get("applied_page_role")) == "financial_statement"
    ]
    if len(reviewed) != 113:
        raise ValueError(f"expected 113 approved financial pages, got {len(reviewed)}")

    groups: dict[str, dict[str, Any]] = {}
    for row in reviewed:
        page_id = text(row["document_page_id"])
        page = pages.get(page_id)
        if page is None:
            raise ValueError(f"missing v3 page {page_id}")
        legacy_statement_id = text(row.get("legacy_statement_id"))
        legacy_row = legacy.get(legacy_statement_id)
        if legacy_row is None:
            raise ValueError(f"missing v5 statement {legacy_statement_id}")
        entity = resolve_entity(entities, text(row.get("legacy_bank_key")))
        statement_date = text(legacy_row.get("statement_date")) or "unknown"
        scope = reporting_scope(text(entity.get("entity_type")))
        identity_key = f"{entity['entity_key']}|{statement_date}|balance_sheet|{scope}"
        statement_id = stable_id("statement", page["source_document_id"], identity_key)
        group = groups.setdefault(statement_id, {
            "statement_id": statement_id,
            "identity_key": identity_key,
            "entity": entity,
            "legacy_statement_id": legacy_statement_id,
            "legacy_statement": legacy_row,
            "issue_key": page["issue_key"],
            "source_document_id": page["source_document_id"],
            "run_id": page["run_id"],
            "entity_name_raw": text(row.get("reviewed_entity_name"))
            or text(legacy_row.get("bank_name_raw")),
            "pages": [],
        })
        group["pages"].append(page)

    plan: list[dict[str, Any]] = []
    for group in groups.values():
        group["pages"].sort(key=lambda item: (item["pdf_page"], item["document_page_id"]))
        statement_extraction_id = stable_id(
            "statement_extraction", group["statement_id"], group["run_id"]
        )
        row_count = sum(len(rows_by_page.get(page["document_page_id"], [])) for page in group["pages"])
        plan.append({
            **group,
            "statement_extraction_id": statement_extraction_id,
            "row_count": row_count,
        })
    plan.sort(key=lambda item: (item["issue_key"], item["pages"][0]["pdf_page"]))
    return plan


def apply_plan(db: Path, plan: list[dict[str, Any]], reviewer: str) -> None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        for group in plan:
            statement_id = group["statement_id"]
            entity = group["entity"]
            legacy = group["legacy_statement"]
            # Gazette issue lookup from the source document.
            issue_id = conn.execute(
                "SELECT gazette_issue_id FROM source_documents WHERE source_document_id = ?",
                (group["source_document_id"],),
            ).fetchone()
            if issue_id is None:
                raise ValueError(f"missing source document {group['source_document_id']}")
            existing = conn.execute(
                "SELECT 1 FROM statements WHERE statement_id = ?", (statement_id,)
            ).fetchone()
            if not existing:
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
                    ) VALUES (?, ?, ?, NULL, ?, 'legacy_import', 1.0, ?, ?, 'legacy_import',
                              ?, 'legacy_import', 'balance_sheet', 'page_classification',
                              ?, NULL, NULL, NULL, NULL, 2, ?, 'proposed', 'proposed',
                              'proposed', ?, ?)
                    """,
                    (
                        statement_id,
                        group["source_document_id"],
                        entity["entity_id"],
                        group["entity_name_raw"],
                        issue_id[0],
                        text(legacy.get("publication_date")) or None,
                        text(legacy.get("statement_date")) or None,
                        reporting_scope(text(entity.get("entity_type"))),
                        group["identity_key"],
                        now(),
                        f"P2-C segmentation; legacy_statement_id={group['legacy_statement_id']}; version={VERSION}",
                    ),
                )

            statement_extraction_id = group["statement_extraction_id"]
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
                        statement_extraction_id, statement_id, group["run_id"],
                        group["source_document_id"],
                        json.dumps([page["pdf_page"] for page in group["pages"]]),
                        now(),
                        f"P2-C segmentation; version={VERSION}",
                    ),
                )

            for page_order, page in enumerate(group["pages"], start=1):
                page_id = page["document_page_id"]
                if not conn.execute(
                    "SELECT 1 FROM statement_pages WHERE statement_id = ? AND document_page_id = ?",
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
                            statement_id, group["source_document_id"], page_id,
                            page_order, 1 if page_order == 1 else 0, now(),
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
                        "SELECT statement_id, statement_extraction_id, row_disposition "
                        "FROM extracted_rows WHERE extracted_row_id = ?",
                        (row["extracted_row_id"],),
                    ).fetchone()
                    if current["statement_id"] not in (None, statement_id):
                        raise ValueError(
                            f"row {row['extracted_row_id']} already assigned to {current['statement_id']}"
                        )
                    if current["row_disposition"] != "included":
                        conn.execute(
                            """
                            UPDATE extracted_rows
                            SET row_disposition='included',
                                statement_id=?,
                                statement_extraction_id=?,
                                statement_order=?
                            WHERE extracted_row_id=?
                            """,
                            (statement_id, statement_extraction_id, statement_order, row["extracted_row_id"]),
                        )
                    if not conn.execute(
                        "SELECT 1 FROM statement_rows WHERE statement_row_id = ?",
                        (stable_id("statement_row", statement_extraction_id, row["extracted_row_id"]),),
                    ).fetchone():
                        parent_status = text(row["parent_context_status"])
                        if parent_status == "identified":
                            parent_status = "unknown"  # no row-level parent link yet
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
                                stable_id("statement_row", statement_extraction_id, row["extracted_row_id"]),
                                statement_id,
                                statement_extraction_id,
                                row["extracted_row_id"],
                                statement_order,
                                row["row_type_proposed"],
                                parent_status,
                                row["label_normalized"],
                                row["label_language"],
                                row["debit_parsed_minor"],
                                row["debit_value_state"],
                                row["credit_parsed_minor"],
                                row["credit_value_state"],
                                fingerprint(row, page_id),
                                now(),
                            ),
                        )

            event_id = stable_id("statement_segment_event", statement_id, VERSION)
            if not conn.execute(
                "SELECT 1 FROM audit_events WHERE audit_event_id = ?", (event_id,)
            ).fetchone():
                conn.execute(
                    """
                    INSERT INTO audit_events(
                        audit_event_id, event_type, statement_id, actor_id,
                        after_json, details_json, reason, created_at
                    ) VALUES (?, 'statement_segmented', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        statement_id,
                        reviewer,
                        json.dumps({"statement_extraction_id": statement_extraction_id}, ensure_ascii=False),
                        json.dumps({
                            "legacy_statement_id": group["legacy_statement_id"],
                            "pages": [page["document_page_id"] for page in group["pages"]],
                            "row_count": group["row_count"],
                            "version": VERSION,
                        }, ensure_ascii=False, sort_keys=True),
                        "P2-C statement segmentation",
                        now(),
                    ),
                )

        # Rejected pages keep their OCR evidence but are explicitly excluded
        # from financial statement assignment.
        for page in conn.execute(
            "SELECT document_page_id FROM document_pages "
            "WHERE classification_status = 'rejected' ORDER BY document_page_id"
        ).fetchall():
            page_id = page["document_page_id"]
            updated = conn.execute(
                "UPDATE extracted_rows SET row_disposition='excluded_page_role' "
                "WHERE document_page_id = ? AND row_disposition = 'unassigned'",
                (page_id,),
            ).rowcount
            event_id = stable_id("page_rows_excluded", page_id, VERSION)
            if not conn.execute(
                "SELECT 1 FROM audit_events WHERE audit_event_id = ?", (event_id,)
            ).fetchone():
                conn.execute(
                    "INSERT INTO audit_events("
                    "audit_event_id, event_type, document_page_id, actor_id, "
                    "details_json, reason, created_at"
                    ") VALUES (?, 'page_rows_excluded', ?, ?, ?, ?, ?)",
                    (
                        event_id, page_id, reviewer,
                        json.dumps({"rows_excluded": updated, "version": VERSION}, ensure_ascii=False),
                        "P2-A rejected page; OCR evidence retained",
                        now(),
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--v5-db", type=Path, default=DEFAULT_V5)
    parser.add_argument("--reviewed-csv", type=Path, default=DEFAULT_REVIEWED)
    parser.add_argument("--plan-out", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--reviewer", default="reviewer")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    plan = build_plan(args.db, args.v5_db, args.reviewed_csv)
    if args.apply:
        apply_plan(args.db, plan, args.reviewer)

    args.plan_out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "statement_id", "identity_key", "entity_id", "entity_name_raw",
        "legacy_statement_id", "issue_key", "pages", "row_count",
        "statement_extraction_id",
    ]
    with args.plan_out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for group in plan:
            writer.writerow({
                "statement_id": group["statement_id"],
                "identity_key": group["identity_key"],
                "entity_id": group["entity"]["entity_id"],
                "entity_name_raw": group["entity_name_raw"],
                "legacy_statement_id": group["legacy_statement_id"],
                "issue_key": group["issue_key"],
                "pages": " | ".join(str(page["pdf_page"]) for page in group["pages"]),
                "row_count": group["row_count"],
                "statement_extraction_id": group["statement_extraction_id"],
            })
    print(json.dumps({
        "statements": len(plan),
        "pages": sum(len(group["pages"]) for group in plan),
        "rows": sum(group["row_count"] for group in plan),
        "applied": bool(args.apply),
        "plan_out": str(args.plan_out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
