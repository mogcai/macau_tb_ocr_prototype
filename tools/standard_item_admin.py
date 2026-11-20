#!/usr/bin/env python3
"""Admin CLI for the v3 Standard Item Catalogue.

This is the interim path until the Workbench Standard List maintenance page is
available.  It changes only ``standard_items`` lifecycle fields and writes one
``audit_events`` row per action.  Existing mappings and OCR evidence are never
edited.

Examples::

    python tools/standard_item_admin.py list --status draft --search 負債
    python tools/standard_item_admin.py approve user:澳門特區負債證明書 \
        --actor tony --note "confirmed user catalogue item"
    python tools/standard_item_admin.py create \
        --zh 新科目 --pt NOVO --actor tony --note "new catalogue item"
    python tools/standard_item_admin.py retire user:舊科目 --actor tony
    python tools/standard_item_admin.py reopen user:舊科目 --actor tony
"""
from __future__ import annotations

import argparse
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


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    from workbench import service

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--actor", default=default_actor())
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list catalogue items and usage")
    listing.add_argument(
        "--status",
        choices=("all", "draft", "approved", "merged", "retired"),
        default="all",
    )
    listing.add_argument("--search", default="")

    approve = sub.add_parser("approve", help="approve a draft standard item")
    approve.add_argument("item")
    approve.add_argument("--note", default="")

    create = sub.add_parser("create", help="create a new draft standard item")
    create.add_argument("--zh", default="")
    create.add_argument("--pt", default="")
    create.add_argument("--en", default="")
    create.add_argument("--key", default="")
    create.add_argument("--parent", default="")
    create.add_argument("--category", default="")
    create.add_argument("--note", default="")

    retire = sub.add_parser("retire", help="retire a standard item")
    retire.add_argument("item")
    retire.add_argument("--note", default="")

    reopen = sub.add_parser("reopen", help="move a retired item back to draft")
    reopen.add_argument("item")
    reopen.add_argument("--note", default="")

    args = parser.parse_args(argv)
    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"database not found: {db_path}")

    try:
        if args.command == "list":
            rows = service.standard_item_admin_rows(db_path=db_path)
            if args.status != "all":
                rows = [row for row in rows if row["item_status"] == args.status]
            if args.search:
                needle = args.search.casefold()
                rows = [
                    row for row in rows
                    if needle in " ".join(
                        text for text in (
                            row.get("item_key"),
                            row.get("canonical_zh"),
                            row.get("canonical_pt"),
                            row.get("canonical_en"),
                            row.get("path"),
                        ) if text
                    ).casefold()
                ]
            print_json({"count": len(rows), "items": rows})
            return 0

        if args.command == "approve":
            result = service.approve_standard_item(
                args.item, actor=args.actor, note=args.note,
                source="standard_item_admin", db_path=db_path,
            )
            print_json(result)
            return 0

        if args.command == "create":
            result = service.create_standard_item(
                actor=args.actor,
                canonical_zh=args.zh,
                canonical_pt=args.pt,
                canonical_en=args.en,
                item_key=args.key or None,
                parent_standard_item_id=args.parent or None,
                category=args.category or None,
                note=args.note,
                source="standard_item_admin",
                db_path=db_path,
            )
            print_json(result)
            return 0

        if args.command == "retire":
            result = service.retire_standard_item(
                args.item, actor=args.actor, note=args.note,
                source="standard_item_admin", db_path=db_path,
            )
            print_json(result)
            return 0

        if args.command == "reopen":
            result = service.reopen_standard_item(
                args.item, actor=args.actor, note=args.note,
                source="standard_item_admin", db_path=db_path,
            )
            print_json(result)
            return 0
    except (LookupError, ValueError) as exc:
        print_json({"error": str(exc)})
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
