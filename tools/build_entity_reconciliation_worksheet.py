#!/usr/bin/env python3
"""Build the P2-B reporting entity reconciliation worksheet.

The worksheet is read-only evidence for human review.  It uses:

* v5 ``banks`` / ``statements`` for legacy entity counts;
* the reviewed P2-A CSV for approved page and entity-name evidence.

It does not update ``reporting_entities`` and does not approve a merge.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V5 = ROOT / "data" / "baseline" / "baseline_v5_20261118.sqlite"
DEFAULT_REVIEWED = ROOT / "docs" / "audit" / "page_classification_reviewed.csv"
DEFAULT_OUT = ROOT / "docs" / "audit" / "reporting_entity_reconciliation.csv"

# Entity IDs identified by the v5 audit.
JUNK_ENTITY_IDS = {112, 113, 114, 115, 116}
SURVIVORS = {
    11: [49, 105],
    22: [58],
    33: [91],
    69: [36],
}
MERGE_TARGET = {
    49: 11,
    105: 11,
    58: 22,
    91: 33,
    36: 69,
}
GROUP_LABEL = {
    11: "G11_49_105",
    49: "G11_49_105",
    105: "G11_49_105",
    22: "G22_58",
    58: "G22_58",
    33: "G33_91",
    91: "G33_91",
    69: "G36_69",
    36: "G36_69",
}


def connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def load_banks(v5: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = v5.execute(
        """
        SELECT b.bank_id, b.bank_key, b.display_name, b.canonical_zh,
               b.canonical_pt, b.entity_type, b.registry_status,
               COUNT(DISTINCT s.statement_id) AS statement_count,
               COALESCE(SUM(s.row_count), 0) AS row_count
        FROM banks b
        LEFT JOIN statements s ON s.bank_id = b.bank_id
        GROUP BY b.bank_id, b.bank_key, b.display_name, b.canonical_zh,
                 b.canonical_pt, b.entity_type, b.registry_status
        ORDER BY b.bank_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def load_alias_counts(v5: sqlite3.Connection) -> Counter[int]:
    counts: Counter[int] = Counter()
    for row in v5.execute("SELECT bank_id FROM bank_name_aliases"):
        counts[int(row["bank_id"])] += 1
    return counts


def load_page_evidence(reviewed_csv: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "approved_pages": 0,
        "issues": set(),
        "names": set(),
    })
    for row in read_csv(reviewed_csv):
        bank_key = text(row.get("legacy_bank_key"))
        if not bank_key:
            continue
        entry = result[bank_key]
        if text(row.get("applied_classification_status")) == "approved":
            entry["approved_pages"] += 1
            issue = text(row.get("issue_number"))
            if issue:
                entry["issues"].add(issue)
            name = text(row.get("reviewed_entity_name"))
            if name:
                entry["names"].add(name)
    return result


def propose(
    bank: dict[str, Any],
    *,
    approved_pages: int,
    alias_count: int,
) -> dict[str, str]:
    bank_id = int(bank["bank_id"])
    if bank_id in JUNK_ENTITY_IDS:
        if bank_id == 112:
            reason = "non_bank_contract_project"
        else:
            reason = "ocr_header_or_report_junk_not_an_entity"
        return {
            "proposed_action": "exclude_non_entity",
            "proposed_survivor_entity_id": "",
            "proposed_survivor_name": "",
            "review_reason": reason,
        }

    if bank_id in SURVIVORS:
        merged = ", ".join(str(value) for value in SURVIVORS[bank_id])
        return {
            "proposed_action": "keep_survivor",
            "proposed_survivor_entity_id": "",
            "proposed_survivor_name": "",
            "review_reason": f"duplicate_name_group; merge candidates: {merged}",
        }

    if bank_id in MERGE_TARGET:
        survivor = MERGE_TARGET[bank_id]
        survivor_name = ""  # filled by caller from bank map
        reason = "duplicate_name_group"
        if bank_id == 105:
            reason = "historical_name_of_11_49"
        return {
            "proposed_action": "merge_into_survivor",
            "proposed_survivor_entity_id": str(survivor),
            "proposed_survivor_name": survivor_name,
            "review_reason": reason,
        }

    if approved_pages > 0:
        action = "keep_approved" if text(bank.get("registry_status")) == "approved" else "approve_provisional"
        return {
            "proposed_action": action,
            "proposed_survivor_entity_id": "",
            "proposed_survivor_name": "",
            "review_reason": "approved_page_evidence"
            + (f"; aliases={alias_count}" if alias_count else ""),
        }

    return {
        "proposed_action": "needs_review",
        "proposed_survivor_entity_id": "",
        "proposed_survivor_name": "",
        "review_reason": "no_approved_page_evidence",
    }


def build(v5_db: Path, reviewed_csv: Path) -> list[dict[str, Any]]:
    v5 = connect_ro(v5_db)
    try:
        banks = load_banks(v5)
        aliases = load_alias_counts(v5)
    finally:
        v5.close()
    evidence = load_page_evidence(reviewed_csv)
    bank_by_id = {int(bank["bank_id"]): bank for bank in banks}

    rows: list[dict[str, Any]] = []
    for bank in banks:
        bank_id = int(bank["bank_id"])
        bank_key = text(bank["bank_key"])
        page = evidence.get(bank_key, {"approved_pages": 0, "issues": set(), "names": set()})
        proposed = propose(
            bank,
            approved_pages=int(page["approved_pages"]),
            alias_count=aliases.get(bank_id, 0),
        )
        if proposed["proposed_action"] == "merge_into_survivor":
            survivor_id = int(proposed["proposed_survivor_entity_id"])
            proposed["proposed_survivor_name"] = text(
                bank_by_id.get(survivor_id, {}).get("display_name")
            )
        related = set()
        if bank_id in SURVIVORS:
            related.update(SURVIVORS[bank_id])
        if bank_id in MERGE_TARGET:
            related.add(MERGE_TARGET[bank_id])
            related.update(SURVIVORS.get(MERGE_TARGET[bank_id], []))
        related.discard(bank_id)
        rows.append({
            "entity_id": bank_id,
            "entity_key": bank_key,
            "display_name": text(bank.get("display_name")),
            "canonical_zh": text(bank.get("canonical_zh")),
            "canonical_pt": text(bank.get("canonical_pt")),
            "entity_type": text(bank.get("entity_type")),
            "registry_status": text(bank.get("registry_status")),
            "statement_count": bank.get("statement_count") or 0,
            "row_count": bank.get("row_count") or 0,
            "alias_count": aliases.get(bank_id, 0),
            "approved_page_count": page["approved_pages"],
            "approved_issue_keys": " | ".join(sorted(page["issues"])),
            "reviewed_entity_names": " | ".join(sorted(page["names"])),
            "duplicate_group": GROUP_LABEL.get(bank_id, ""),
            "related_entity_ids": " | ".join(str(value) for value in sorted(related)),
            **proposed,
            "reviewer_decision": "",
            "reviewer_survivor_entity_id": "",
            "reviewer_note": "",
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v5-db", type=Path, default=DEFAULT_V5)
    parser.add_argument("--reviewed-csv", type=Path, default=DEFAULT_REVIEWED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rows = build(args.v5_db.expanduser().resolve(), args.reviewed_csv.expanduser().resolve())
    fields = [
        "entity_id", "entity_key", "display_name", "canonical_zh", "canonical_pt",
        "entity_type", "registry_status", "statement_count", "row_count",
        "alias_count", "approved_page_count", "approved_issue_keys",
        "reviewed_entity_names", "duplicate_group", "related_entity_ids",
        "proposed_action", "proposed_survivor_entity_id", "proposed_survivor_name",
        "review_reason", "reviewer_decision", "reviewer_survivor_entity_id",
        "reviewer_note",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} entities to {args.out}")
    print(Counter(row["proposed_action"] for row in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
