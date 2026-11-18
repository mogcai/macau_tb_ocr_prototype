#!/usr/bin/env python3
"""Import the user's standard parent/child label CSV and map existing OCR rows.

Input columns (blank first column is tolerated):
    ,parent_zh,child_zh,parent_pt,child_pt,,

The raw OCR fields are never changed. The result is stored separately:
    standard_labels / standard_label_aliases
    line_items.user_standard_label_id
    line_items.user_standard_match_status

Usage::

    python tools/import_user_standard.py build \
        --db data/macau_balancete.sqlite \
        --csv labels/standard_label_by_user.csv

    python tools/import_user_standard.py report \
        --db data/macau_balancete.sqlite \
        --csv data/user_standard_mapping_report.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from macau_tb_ocr import normalize_label_key  # noqa: E402
from tools.build_database import (  # noqa: E402
    SCHEMA_VERSION,
    _connect,
    _schema,
    normalize_text,
)

CJK_RE = re.compile(r"[\u3400-\u9fff]")
GREEK_TO_LATIN = str.maketrans({
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X",
})
ZH_FIX = {
    "總收人": "總收入",
    "應收賬項": "應收帳項",
    "內部及調整賬": "內部及調整帳",
    "代客保管賬": "代客保管帳",
    "抵押賬": "抵押帳",
    "其他備查賬": "其他備查帳",
    "AMCM 存款": "AMCM存款",
}
# The source rows 47-54 are shifted by one pairing. These corrections are
# explicit and are marked provisional for human confirmation.
PT_BY_ZH_FIX = {
    "股本": "CAPITAL",
    "營運資金": "FUNDO DE MANEIO",
    "法定儲備": "RESERVA LEGAL",
    "自定儲備": "RESERVA ESTATUTÁRIA",
    "其他儲備": "OUTRAS RESERVAS",
    "歷年營業結果": "RESULTADOS TRANSITADOS DE EXERCÍCIOS ANTERIORES",
    "總收入": "PROVEITOS POR NATUREZA",
    "總支出": "CUSTOS POR NATUREZA",
}
PT_FIX = {
    "DEPÓSITOS DO SECTOR PUBLICO": "DEPÓSITOS DO SECTOR PÚBLICO",
    "RESULTADOS TRANSITADOS DE EXERCICIOS ANTERIORES":
        "RESULTADOS TRANSITADOS DE EXERCÍCIOS ANTERIORES",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_zh(value: str) -> str:
    text = normalize_text(value)
    return ZH_FIX.get(text, text)


def clean_pt(value: str) -> str:
    text = normalize_text(value).translate(GREEK_TO_LATIN)
    text = re.sub(r"\s+", " ", text).strip()
    return PT_FIX.get(text, text)


def _norm(text: str, language: str) -> str:
    return normalize_label_key(text, language)


def read_raw_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for index, row in enumerate(reader, start=2):
            if len(row) < 5:
                continue
            raw_parent_zh = normalize_text(row[1])
            raw_child_zh = normalize_text(row[2])
            raw_parent_pt = normalize_text(row[3])
            raw_child_pt = normalize_text(row[4])
            if not raw_parent_zh and not raw_parent_pt:
                continue
            parent_zh = clean_zh(raw_parent_zh)
            child_zh = clean_zh(raw_child_zh)
            parent_pt = clean_pt(raw_parent_pt)
            child_pt = clean_pt(raw_child_pt)
            notes: list[str] = []
            provisional = False
            if parent_zh == "AMCM存款" and parent_pt in {"PATACAS", "MOEDAS EXTERNAS"}:
                parent_pt = "DEPÓSITOS NA AMCM"
                notes.append("AMCM child parent_pt corrected")
                provisional = True
            if parent_zh in PT_BY_ZH_FIX and parent_pt != PT_BY_ZH_FIX[parent_zh]:
                notes.append(
                    f"pt pairing corrected from {parent_pt!r} to "
                    f"{PT_BY_ZH_FIX[parent_zh]!r}"
                )
                parent_pt = PT_BY_ZH_FIX[parent_zh]
                provisional = True
            if parent_zh != raw_parent_zh:
                notes.append(f"zh normalized {raw_parent_zh!r} -> {parent_zh!r}")
            if parent_pt != raw_parent_pt:
                notes.append(f"pt normalized {raw_parent_pt!r} -> {parent_pt!r}")
            if child_zh != raw_child_zh:
                notes.append(f"child zh normalized {raw_child_zh!r} -> {child_zh!r}")
            if child_pt != raw_child_pt:
                notes.append(f"child pt normalized {raw_child_pt!r} -> {child_pt!r}")
            rows.append({
                "source_row": index,
                "parent_zh": parent_zh,
                "child_zh": child_zh,
                "parent_pt": parent_pt,
                "child_pt": child_pt,
                "raw_parent_zh": raw_parent_zh,
                "raw_child_zh": raw_child_zh,
                "raw_parent_pt": raw_parent_pt,
                "raw_child_pt": raw_child_pt,
                "status": "provisional" if provisional else "approved",
                "note": "; ".join(notes),
            })
    # The source is missing 重估儲備 / RESERVA DA REAVALIAÇÃO because the
    # reserve block was shifted. Insert it after 其他儲備.
    insert_at = next(
        (i + 1 for i, row in enumerate(rows)
         if row["parent_zh"] == "其他儲備" and not row["child_zh"]),
        None,
    )
    if insert_at is not None:
        exists = any(
            row["parent_zh"] == "重估儲備" and not row["child_zh"]
            for row in rows
        )
        if not exists:
            rows.insert(insert_at, {
                "source_row": "47b",
                "parent_zh": "重估儲備",
                "child_zh": "",
                "parent_pt": "RESERVA DA REAVALIAÇÃO",
                "child_pt": "",
                "raw_parent_zh": "重估儲備",
                "raw_child_zh": "",
                "raw_parent_pt": "RESERVA DA REAVALIAÇÃO",
                "raw_child_pt": "",
                "status": "provisional",
                "note": "inserted: source block 47-54 is shifted and omits this standard",
            })
    return rows


def write_cleaned_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "source_row", "parent_zh", "child_zh", "parent_pt", "child_pt",
        "status", "note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: row.get(key, "") for key in fields} for row in rows])


def _standard_key(parent_zh: str, child_zh: str = "") -> str:
    return f"user:{parent_zh}" + (f">{child_zh}" if child_zh else "")


def import_standards(
    db_path: Path,
    csv_path: Path,
    *,
    cleaned_csv: Path | None = None,
) -> dict[str, Any]:
    rows = read_raw_csv(csv_path)
    if cleaned_csv:
        write_cleaned_csv(cleaned_csv, rows)
    conn = _connect(db_path)
    try:
        _schema(conn)
        with conn:
            # Parents first.
            parent_id: dict[str, int] = {}
            parent_status: dict[str, str] = {}
            parent_pt: dict[str, str] = {}
            for row in rows:
                zh = row["parent_zh"]
                if not zh:
                    continue
                if row["child_zh"]:
                    continue
                parent_pt.setdefault(zh, row["parent_pt"])
                parent_status[zh] = (
                    "provisional"
                    if parent_status.get(zh) == "provisional"
                    or row["status"] == "provisional"
                    else "approved"
                )
            for zh, pt in parent_pt.items():
                key = _standard_key(zh)
                conn.execute(
                    """
                    INSERT INTO standard_labels (
                        standard_key, canonical_zh, canonical_pt, level,
                        status, source, note
                    ) VALUES (?, ?, ?, 'parent', ?, 'user_csv', ?)
                    ON CONFLICT(standard_key) DO UPDATE SET
                        canonical_zh = excluded.canonical_zh,
                        canonical_pt = COALESCE(NULLIF(excluded.canonical_pt, ''),
                                                standard_labels.canonical_pt),
                        status = excluded.status,
                        note = COALESCE(NULLIF(excluded.note, ''),
                                       standard_labels.note)
                    """,
                    (key, zh, pt, parent_status.get(zh, "approved"), None),
                )
                parent_id[zh] = int(conn.execute(
                    "SELECT standard_id FROM standard_labels WHERE standard_key = ?",
                    (key,),
                ).fetchone()[0])
            # Children.
            child_rows: dict[tuple[str, str], dict[str, Any]] = {}
            for row in rows:
                if not row["child_zh"] or not row["parent_zh"]:
                    continue
                child_rows[(row["parent_zh"], row["child_zh"])] = row
            for (parent_zh, child_zh), row in child_rows.items():
                pid = parent_id.get(parent_zh)
                if pid is None:
                    continue
                key = _standard_key(parent_zh, child_zh)
                conn.execute(
                    """
                    INSERT INTO standard_labels (
                        standard_key, parent_standard_id, canonical_zh,
                        canonical_pt, level, status, source, note
                    ) VALUES (?, ?, ?, ?, 'child', ?, 'user_csv', ?)
                    ON CONFLICT(standard_key) DO UPDATE SET
                        parent_standard_id = excluded.parent_standard_id,
                        canonical_zh = excluded.canonical_zh,
                        canonical_pt = COALESCE(NULLIF(excluded.canonical_pt, ''),
                                                standard_labels.canonical_pt),
                        status = excluded.status,
                        note = COALESCE(NULLIF(excluded.note, ''),
                                       standard_labels.note)
                    """,
                    (key, pid, child_zh, row["child_pt"], row["status"], None),
                )
            # Aliases for parents and children.
            for row in rows:
                if row["child_zh"]:
                    key = _standard_key(row["parent_zh"], row["child_zh"])
                    standard_id = conn.execute(
                        "SELECT standard_id FROM standard_labels WHERE standard_key = ?",
                        (key,),
                    ).fetchone()
                    if standard_id is None:
                        continue
                    sid = int(standard_id[0])
                    aliases = [
                        (row["child_zh"], "zh-Hant"),
                        (row["raw_child_zh"], "zh-Hant"),
                        (row["child_pt"], "pt"),
                        (row["raw_child_pt"], "pt"),
                    ]
                else:
                    key = _standard_key(row["parent_zh"])
                    standard_id = conn.execute(
                        "SELECT standard_id FROM standard_labels WHERE standard_key = ?",
                        (key,),
                    ).fetchone()
                    if standard_id is None:
                        continue
                    sid = int(standard_id[0])
                    aliases = [
                        (row["parent_zh"], "zh-Hant"),
                        (row["raw_parent_zh"], "zh-Hant"),
                        (row["parent_pt"], "pt"),
                        (row["raw_parent_pt"], "pt"),
                    ]
                for raw, language in aliases:
                    raw = normalize_text(raw)
                    if not raw:
                        continue
                    normalized = _norm(raw, language)
                    try:
                        conn.execute(
                            """
                            INSERT INTO standard_label_aliases (
                                standard_id, raw_name, normalized_name,
                                language, status, source
                            ) VALUES (?, ?, ?, ?, ?, 'user_csv')
                            ON CONFLICT(standard_id, normalized_name, language)
                            DO UPDATE SET raw_name = excluded.raw_name
                            """,
                            (sid, raw, normalized, language,
                             "approved" if row["status"] == "approved"
                             else "provisional"),
                        )
                    except sqlite3.IntegrityError:
                        pass
            # The user chose 總計; keep 總額 as an alias.
            total = conn.execute(
                "SELECT standard_id FROM standard_labels WHERE standard_key = 'user:總計'"
            ).fetchone()
            if total:
                for alias in ("總額", "總額TOTAIS"):
                    conn.execute(
                        "INSERT INTO standard_label_aliases "
                        "(standard_id, raw_name, normalized_name, language, "
                        " status, source) VALUES (?, ?, ?, 'zh-Hant', "
                        "'approved', 'user_csv') "
                        "ON CONFLICT(standard_id, normalized_name, language) "
                        "DO NOTHING",
                        (total[0], alias, _norm(alias, "zh-Hant")),
                    )
            mapping = map_line_items(conn)
    finally:
        conn.close()
    return {
        "database": str(db_path.resolve()),
        "csv": str(csv_path.resolve()),
        "cleaned_csv": str(cleaned_csv.resolve()) if cleaned_csv else "",
        "schema_version": SCHEMA_VERSION,
        "rows": len(rows),
        "standards": len(parent_id) + len(child_rows),
        "mapping": dict(mapping),
        "generated_at": _now(),
    }


def map_line_items(conn: sqlite3.Connection) -> Counter[str]:
    rows = conn.execute(
        "SELECT standard_id, standard_key, canonical_zh, canonical_pt, "
        "level, parent_standard_id, status FROM standard_labels"
    ).fetchall()
    by_id = {int(row["standard_id"]): row for row in rows}
    parents_zh: dict[str, int] = {}
    parents_pt: dict[str, int] = {}
    children: dict[tuple[int, str], int] = {}
    children_by_zh: dict[str, list[int]] = defaultdict(list)
    children_by_pt: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        status = row["status"]
        if status == "rejected":
            continue
        if row["level"] == "parent":
            if row["canonical_zh"]:
                parents_zh.setdefault(_norm(row["canonical_zh"], "zh-Hant"),
                                      int(row["standard_id"]))
            if row["canonical_pt"]:
                parents_pt.setdefault(_norm(row["canonical_pt"], "pt"),
                                      int(row["standard_id"]))
        else:
            parent_id = row["parent_standard_id"]
            if row["canonical_zh"] and parent_id:
                children[(int(parent_id), _norm(row["canonical_zh"], "zh-Hant"))] = int(row["standard_id"])
                children_by_zh[_norm(row["canonical_zh"], "zh-Hant")].append(int(row["standard_id"]))
            if row["canonical_pt"] and parent_id:
                children[(int(parent_id), _norm(row["canonical_pt"], "pt"))] = int(row["standard_id"])
                children_by_pt[_norm(row["canonical_pt"], "pt")].append(int(row["standard_id"]))
    # Approved/known aliases extend the canonical lookup.
    for alias in conn.execute(
        "SELECT a.standard_id, a.normalized_name, a.language, s.level, "
        "s.parent_standard_id, s.status FROM standard_label_aliases a "
        "JOIN standard_labels s ON s.standard_id = a.standard_id "
        "WHERE a.status <> 'rejected' AND s.status <> 'rejected'"
    ):
        sid = int(alias["standard_id"])
        key = alias["normalized_name"]
        language = normalize_text(alias["language"])
        if alias["level"] == "parent":
            if language == "zh-Hant":
                parents_zh.setdefault(key, sid)
            elif language == "pt":
                parents_pt.setdefault(key, sid)
        else:
            parent_id = alias["parent_standard_id"]
            if not parent_id:
                continue
            children.setdefault((int(parent_id), key), sid)
            if language == "zh-Hant":
                children_by_zh[key].append(sid)
            elif language == "pt":
                children_by_pt[key].append(sid)
    stats: Counter[str] = Counter()
    for item in conn.execute(
        "SELECT line_item_id, label_standard, parent_label_standard, "
        "label_language FROM line_items"
    ).fetchall():
        label = normalize_text(item["label_standard"])
        parent = normalize_text(item["parent_label_standard"])
        language = normalize_text(item["label_language"]) or (
            "zh-Hant" if CJK_RE.search(label) else "pt"
        )
        standard_id = None
        status = "unmapped"
        if label and parent:
            parent_norm_zh = _norm(parent, "zh-Hant")
            parent_norm_pt = _norm(parent, "pt")
            parent_id = parents_zh.get(parent_norm_zh) or parents_pt.get(parent_norm_pt)
            if parent_id:
                standard_id = (
                    children.get((parent_id, _norm(label, "zh-Hant")))
                    or children.get((parent_id, _norm(label, "pt")))
                )
                if standard_id:
                    status = "child_path_exact"
        if standard_id is None and label:
            standard_id = (
                parents_zh.get(_norm(label, "zh-Hant"))
                or parents_pt.get(_norm(label, "pt"))
            )
            if standard_id:
                status = "parent_exact"
        if standard_id is None and label:
            candidates = set(children_by_zh.get(_norm(label, "zh-Hant"), []))
            candidates.update(children_by_pt.get(_norm(label, "pt"), []))
            if len(candidates) == 1:
                standard_id = next(iter(candidates))
                status = "child_unique"
        conn.execute(
            "UPDATE line_items SET user_standard_label_id = ?, "
            "user_standard_match_status = ? WHERE line_item_id = ?",
            (standard_id, status, item["line_item_id"]),
        )
        stats[status] += 1
    return stats


def write_report(db_path: Path, out_path: Path) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        _schema(conn)
        rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(label_standard, ''), label_raw) AS raw_name,
                   parent_label_standard, label_language,
                   COALESCE(user_standard_match_status, 'unmapped') AS match_status,
                   s.standard_key, s.canonical_zh, s.canonical_pt,
                   COUNT(*) AS occurrence_count,
                   COUNT(DISTINCT statement_id) AS statement_count
            FROM v_current_line_items li
            LEFT JOIN standard_labels s
                   ON s.standard_id = li.user_standard_label_id
            WHERE COALESCE(user_standard_label_id, 0) = 0
               OR user_standard_label_id IS NOT NULL
            GROUP BY raw_name, parent_label_standard, label_language,
                     match_status, s.standard_key, s.canonical_zh, s.canonical_pt
            ORDER BY match_status = 'unmapped' DESC, occurrence_count DESC
            """
        ).fetchall()
    finally:
        conn.close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "raw_name", "parent_label_standard", "label_language",
        "match_status", "standard_key", "standard_zh", "standard_pt",
        "occurrence_count", "statement_count",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "raw_name": row["raw_name"],
                "parent_label_standard": row["parent_label_standard"] or "",
                "label_language": row["label_language"] or "",
                "match_status": row["match_status"],
                "standard_key": row["standard_key"] or "",
                "standard_zh": row["canonical_zh"] or "",
                "standard_pt": row["canonical_pt"] or "",
                "occurrence_count": row["occurrence_count"],
                "statement_count": row["statement_count"],
            })
    return {
        "csv": str(out_path.resolve()),
        "rows": len(rows),
        "unmapped": sum(1 for row in rows if row["match_status"] == "unmapped"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import user standard labels and map existing OCR rows"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="import the user standard CSV")
    build.add_argument("--db", type=Path, default=Path("data/macau_balancete.sqlite"))
    build.add_argument("--csv", type=Path,
                       default=Path("labels/standard_label_by_user.csv"))
    build.add_argument("--cleaned-csv", type=Path,
                       default=Path("labels/standard_label_by_user.cleaned.csv"))
    build.add_argument("--report", type=Path,
                       default=Path("data/user_standard_mapping_report.csv"))
    report = sub.add_parser("report", help="write the current mapping report")
    report.add_argument("--db", type=Path, default=Path("data/macau_balancete.sqlite"))
    report.add_argument("--csv", type=Path,
                        default=Path("data/user_standard_mapping_report.csv"))
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = import_standards(
                args.db, args.csv, cleaned_csv=args.cleaned_csv
            )
            result["report"] = write_report(args.db, args.report)
        else:
            result = write_report(args.db, args.csv)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
