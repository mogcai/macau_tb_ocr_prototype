#!/usr/bin/env python3
"""Apply the curated financial classification CSV to the v3 Standard Catalogue.

Source CSV columns:

    account_name, category, classification_status

``account_name`` is either a Chinese catalogue path (``定期存款>外幣``) or a
Portuguese canonical name prefixed with ``pt:``.  Matching is exact: the tool
never fuzzy-matches a name, because a wrong classification silently changes
every future total.  Unmatched, ambiguous or duplicate rows are reported and
left untouched.

Only ``standard_items.category``, ``classification_status`` and
``classification_source`` are written.  Names, aliases, mappings, OCR evidence
and review state are never modified.  ``classification_status`` is separate from
``category`` so ``excluded`` (for example 總計) is not confused with an item that
has not been classified yet.  Every write creates one ``audit_events`` row.

Without ``--apply`` nothing is written.

Examples::

    # inspect what would change
    python tools/import_standard_classification.py --actor tony

    # write the classification
    python tools/import_standard_classification.py --actor tony --apply

    # replace a conflicting hand-entered category
    python tools/import_standard_classification.py --actor tony --apply --overwrite
"""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import os
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

from workbench.db import ensure_standard_item_classification_columns  # noqa: E402

DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_CSV = ROOT / "docs" / "macau_bank_account_classification.csv"
REQUIRED_COLUMNS = ("account_name", "category", "classification_status")
ALLOWED_STATUSES = ("classified", "excluded")
VERSION = "standard-classification-1"


def default_actor() -> str:
    return os.environ.get("USER") or getpass.getuser() or "cli"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def clean_name(value: str) -> str:
    return unicodedata.normalize("NFKC", text(value))


def clean_path(value: str) -> str:
    return ">".join(
        part.strip() for part in clean_name(value).split(">") if part.strip()
    )


def clean_key(value: str) -> str:
    return " ".join(clean_name(value).split()).casefold()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_label(path: Path, digest: str) -> str:
    try:
        relative = path.resolve().relative_to(ROOT)
    except ValueError:
        relative = Path(path.name)
    return f"csv:{relative.as_posix()}@sha256:{digest[:12]}"


def read_csv(path: Path) -> tuple[list[dict[str, Any]], list[str], str]:
    digest = sha256_file(path)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{path}: missing required column(s): {', '.join(missing)}"
            )
        for line_number, raw in enumerate(reader, start=2):
            account = text(raw.get("account_name"))
            category = text(raw.get("category"))
            status = text(raw.get("classification_status")).casefold()
            if not account:
                errors.append(f"line {line_number}: account_name is blank")
                continue
            if not category:
                errors.append(f"line {line_number} ({account}): category is blank")
                continue
            if status not in ALLOWED_STATUSES:
                errors.append(
                    f"line {line_number} ({account}): classification_status "
                    f"{status!r} is not one of {', '.join(ALLOWED_STATUSES)}"
                )
                continue
            rows.append({
                "line": line_number,
                "account_name": account,
                "category": category,
                "classification_status": status,
            })
    return rows, errors, digest


def item_path(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    current: dict[str, Any] | None = item
    while current is not None and current["standard_item_id"] not in seen:
        seen.add(current["standard_item_id"])
        parts.append(
            text(current.get("canonical_zh"))
            or text(current.get("canonical_pt"))
            or current["item_key"]
        )
        parent_id = current.get("parent_standard_item_id")
        current = by_id.get(parent_id) if parent_id else None
    return ">".join(reversed(parts))


def load_items(
    conn: sqlite3.Connection, *, has_classification: bool,
) -> list[dict[str, Any]]:
    status = (
        "classification_status" if has_classification
        else "'unclassified' AS classification_status"
    )
    source = (
        "classification_source" if has_classification
        else "NULL AS classification_source"
    )
    items = [dict(row) for row in conn.execute(
        "SELECT standard_item_id, item_key, canonical_zh, canonical_pt, "
        "parent_standard_item_id, category, "
        f"{status}, {source}, item_status "
        "FROM standard_items ORDER BY item_key"
    )]
    by_id = {item["standard_item_id"]: item for item in items}
    for item in items:
        item["path"] = item_path(item, by_id)
    return items


def match_item(
    row: dict[str, Any],
    by_path: dict[str, list[dict[str, Any]]],
    by_pt: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    account = row["account_name"]
    if account.casefold().startswith("pt:"):
        candidates = by_pt.get(clean_key(account[3:]), [])
        return (candidates[0] if len(candidates) == 1 else None), candidates, "canonical_pt"
    candidates = by_path.get(clean_path(account), [])
    return (candidates[0] if len(candidates) == 1 else None), candidates, "catalogue_path"


def plan_action(
    item: dict[str, Any], row: dict[str, Any], source: str, overwrite: bool,
) -> tuple[str, str]:
    current_category = text(item.get("category"))
    current_status = text(item.get("classification_status")) or "unclassified"
    current_source = text(item.get("classification_source"))
    if current_category and current_category != row["category"] and not overwrite:
        return "conflict", (
            f"existing category {current_category!r} differs from CSV "
            f"{row['category']!r}; rerun with --overwrite to replace it"
        )
    if (
        current_category == row["category"]
        and current_status == row["classification_status"]
        and current_source == source
    ):
        return "noop", ""
    if not current_category and current_status == "unclassified":
        return "set", ""
    return "update", ""


def write_audit(
    conn: sqlite3.Connection,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    actor: str,
    source: str,
    action: str,
) -> None:
    conn.execute(
        "INSERT INTO audit_events("
        "audit_event_id, event_type, standard_item_id, actor_id, "
        "before_json, after_json, details_json, reason, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            "standard_item_classified",
            after["standard_item_id"],
            actor,
            json.dumps(before, ensure_ascii=False, default=str),
            json.dumps(after, ensure_ascii=False, default=str),
            json.dumps(
                {"source": source, "version": VERSION, "action": action},
                ensure_ascii=False,
            ),
            "Applied curated financial classification",
            now(),
        ),
    )


def classify(
    db_path: Path,
    csv_path: Path,
    *,
    actor: str,
    apply: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    rows, errors, digest = read_csv(csv_path)
    source = source_label(csv_path, digest)
    if errors:
        return {
            "csv": str(csv_path),
            "source": source,
            "errors": errors,
            "applied": False,
        }

    if apply:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
    else:
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        if apply:
            ensure_standard_item_classification_columns(conn)
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(standard_items)")
        }
        items = load_items(
            conn, has_classification="classification_status" in columns,
        )
        by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_pt: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            by_path[clean_path(item["path"])].append(item)
            if text(item.get("canonical_pt")):
                by_pt[clean_key(item["canonical_pt"])].append(item)

        plans: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        claimed: dict[str, int] = {}

        for row in rows:
            item, candidates, method = match_item(row, by_path, by_pt)
            plan = {**row, "match_method": method}
            if item is None:
                if candidates:
                    plan["action"] = "ambiguous"
                    plan["candidates"] = [
                        {"item_key": candidate["item_key"], "path": candidate["path"]}
                        for candidate in candidates
                    ]
                    ambiguous.append(plan)
                else:
                    plan["action"] = "unmatched"
                    unmatched.append(plan)
                continue
            if item["standard_item_id"] in claimed:
                plan["action"] = "duplicate"
                plan["first_line"] = claimed[item["standard_item_id"]]
                duplicates.append(plan)
                continue
            claimed[item["standard_item_id"]] = row["line"]
            action, reason = plan_action(item, row, source, overwrite)
            plans.append({
                **plan,
                "standard_item_id": item["standard_item_id"],
                "item_key": item["item_key"],
                "item_status": item["item_status"],
                "path": item["path"],
                "action": action,
                "reason": reason,
            })

        writable = [plan for plan in plans if plan["action"] in ("set", "update")]
        counts = Counter(plan["action"] for plan in plans)

        predicted: dict[str, str] = {}
        if apply:
            try:
                for plan in writable:
                    item = next(
                        item for item in items
                        if item["standard_item_id"] == plan["standard_item_id"]
                    )
                    before = {
                        "standard_item_id": item["standard_item_id"],
                        "item_key": item["item_key"],
                        "category": item.get("category"),
                        "classification_status": item.get("classification_status"),
                        "classification_source": item.get("classification_source"),
                    }
                    conn.execute(
                        "UPDATE standard_items SET category=?, "
                        "classification_status=?, classification_source=? "
                        "WHERE standard_item_id=?",
                        (
                            plan["category"],
                            plan["classification_status"],
                            source,
                            plan["standard_item_id"],
                        ),
                    )
                    after = {
                        **before,
                        "category": plan["category"],
                        "classification_status": plan["classification_status"],
                        "classification_source": source,
                    }
                    write_audit(
                        conn, before=before, after=after, actor=actor,
                        source=source, action=plan["action"],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        for plan in plans:
            if plan["action"] in ("set", "update"):
                predicted[plan["standard_item_id"]] = plan["classification_status"]
        unclassified = [
            {
                "item_key": item["item_key"],
                "path": item["path"],
                "item_status": item["item_status"],
            }
            for item in items
            if item["item_status"] != "merged"
            and predicted.get(item["standard_item_id"], item["classification_status"])
            == "unclassified"
        ]

        result = {
            "csv": str(csv_path),
            "source": source,
            "apply": apply,
            "rows": len(rows),
            "matched": len(plans) + len(duplicates),
            "counts": dict(counts),
            "matched_items": len(claimed),
            "unmatched": unmatched,
            "ambiguous": ambiguous,
            "duplicates": duplicates,
            "unclassified_standard_items": unclassified,
            "plans": sorted(
                plans + unmatched + ambiguous + duplicates,
                key=lambda plan: plan["line"],
            ),
        }
        if not apply and writable:
            result["hint"] = "dry run only; rerun with --apply to write"
        return result
    finally:
        conn.close()


def write_report(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "line", "account_name", "category", "classification_status",
        "standard_item_id", "item_key", "path", "action", "reason",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for plan in result.get("plans", []):
            writer.writerow({field: plan.get(field, "") for field in fields})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--actor", default=default_actor())
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the classification; without this flag it is a dry run",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing, different category value",
    )
    parser.add_argument("--report", type=Path, help="write every planned row to CSV")
    args = parser.parse_args(argv)

    db_path = args.db.expanduser().resolve()
    csv_path = args.csv.expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"database not found: {db_path}")
    if not csv_path.is_file():
        parser.error(f"classification CSV not found: {csv_path}")

    try:
        result = classify(
            db_path, csv_path, actor=args.actor,
            apply=args.apply, overwrite=args.overwrite,
        )
    except (LookupError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    if result.get("errors"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    if args.report:
        report_path = args.report.expanduser().resolve()
        write_report(report_path, result)
        result["report"] = str(report_path)

    result.pop("plans", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
