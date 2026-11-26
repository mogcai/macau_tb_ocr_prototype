#!/usr/bin/env python3
"""Approve unambiguous auto-mapped standard items in one audited pass.

The first-pass auto-map only writes ``proposed`` mappings.  When a statement has
already been reviewed (or adopted) row by row, approving each proposal by hand
is busy work.  This CLI approves every proposal that is unambiguous:

* the row has exactly one proposal and no approved mapping;
* the same standard item is proposed on only one row of the statement;
* the standard item is approved and not merged/retired;
* the item is not already approved or inserted on another row.

Everything else is skipped and reported, never guessed.  Amounts, OCR evidence
and review state are not touched, and no row becomes financially verified.

Reviewed/adopted statements are read-only by default; pass ``--allow-locked``
to include them.  Without ``--apply`` nothing is written.

Examples::

    # dry run over reviewed/adopted statements
    python tools/bulk_approve_mappings.py --actor tony

    # approve them for real
    python tools/bulk_approve_mappings.py --actor tony --allow-locked --apply

    # only statements still in review
    python tools/bulk_approve_mappings.py --actor tony --scope editable

    # a single statement
    python tools/bulk_approve_mappings.py --actor tony --allow-locked \\
        --statement-id <statement_id> --apply
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DB = ROOT / "data" / "macau_balancete_v3.sqlite"


def default_actor() -> str:
    return os.environ.get("USER") or getpass.getuser() or "cli"


def main(argv: list[str] | None = None) -> int:
    from workbench import service

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--actor", default=default_actor())
    parser.add_argument("--note", default="")
    parser.add_argument(
        "--scope",
        choices=service.BULK_APPROVAL_SCOPES,
        default="reviewed",
        help="reviewed = reviewed/adopted (default), editable = still in review, all",
    )
    parser.add_argument(
        "--allow-locked",
        action="store_true",
        help="include reviewed/adopted statements (normally read-only)",
    )
    parser.add_argument(
        "--statement-id",
        action="append",
        dest="statement_ids",
        metavar="ID",
        help="limit to one statement (repeatable)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the approvals; without this flag it is a dry run",
    )
    parser.add_argument(
        "--skipped-csv",
        type=Path,
        metavar="PATH",
        help="write every skipped row and the reason to a CSV for manual review",
    )
    args = parser.parse_args(argv)

    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"database not found: {db_path}")

    try:
        result = service.bulk_approve_proposed_mappings(
            actor=args.actor,
            note=args.note,
            statement_ids=args.statement_ids,
            scope=args.scope,
            allow_locked=args.allow_locked,
            apply=args.apply,
            collect_skipped=bool(args.skipped_csv),
            db_path=db_path,
        )
    except (LookupError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    if args.skipped_csv:
        path = args.skipped_csv.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        skipped = result.get("skipped") or []
        fields = [
            "statement_id", "statement_row_id", "statement_order",
            "label", "standard_item_id", "standard_item_key", "reason",
        ]
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for entry in skipped:
                writer.writerow({field: entry.get(field, "") for field in fields})
        result["skipped_csv"] = str(path)
        result["skipped_rows"] = len(skipped)
    result.pop("skipped", None)

    if (
        not args.apply
        and not args.allow_locked
        and result["locked_statements_skipped"]
    ):
        result["hint"] = (
            "reviewed/adopted statements were skipped; rerun with --allow-locked "
            "to include them"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
