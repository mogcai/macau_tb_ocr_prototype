#!/usr/bin/env python3
"""Compare raw and standardised account labels against verified ground truth.

Example::

    python tools/gt_label_report.py ground_truth/issue45_2026 \
        --run output_v07_issue45_2026 --lexicon labels/label_lexicon.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macau_tb_ocr import (  # noqa: E402
    build_label_hierarchy,
    detect_label_continuations,
    load_label_lexicon,
    normalize_chinese_label,
    standardize_labels,
)
from gt_score import match_rows, norm_label  # noqa: E402


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description="比較 raw 與標準化標籤")
    parser.add_argument("gt_dir", help="ground truth 資料夾")
    parser.add_argument("--run", required=True, help="OCR 輸出資料夾")
    parser.add_argument("--lexicon", default="labels/label_lexicon.csv")
    args = parser.parse_args()

    lexicon = load_label_lexicon(args.lexicon)
    if not lexicon:
        raise SystemExit(f"找不到詞表：{args.lexicon}")
    gt_dir = Path(args.gt_dir)
    run_dir = Path(args.run)
    gt_rows = [
        row for row in read_csv(gt_dir / "gt_rows.csv")
        if str(row.get("verified", "")).strip() in {"1", "true", "yes", "y"}
    ]
    run_rows = read_csv(run_dir / "tb_rows.csv")
    run_docs = {
        int(row["pdf_page"]): row
        for row in read_csv(run_dir / "documents.csv")
        if row.get("pdf_page")
    } if (run_dir / "documents.csv").exists() else {}
    gt_pages = {
        int(row["pdf_page"]): row
        for row in read_csv(gt_dir / "gt_pages.csv")
        if row.get("pdf_page")
    } if (gt_dir / "gt_pages.csv").exists() else {}

    gt_by: dict[str, list[dict[str, str]]] = defaultdict(list)
    run_by: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in gt_rows:
        gt_by[row["pdf_page"]].append(row)
    for row in run_rows:
        run_by[row["pdf_page"]].append(row)

    raw_total = raw_exact = std_total = std_exact = 0
    wrong: list[tuple[str, str, str, str, str]] = []
    unmatched: list[tuple[str, str, str, str]] = []
    gt_status: Counter[str] = Counter()
    for page in sorted(gt_by, key=int):
        if page not in run_by:
            continue
        gt_page = gt_by[page]
        standardised: list[dict[str, str]] = []
        for row in gt_page:
            standardised.append({
                "label_raw": row.get("label_gt", ""),
                "label_normalized": normalize_chinese_label(
                    row.get("label_gt", ""), "s2hk"
                )[0],
                "debit_amount": row.get("debit_gt", ""),
                "credit_amount": row.get("credit_gt", ""),
                "review_reason": "",
                "review_status": "",
            })
        standardize_labels(standardised, lexicon)
        build_label_hierarchy(standardised)
        detect_label_continuations(standardised, lexicon)
        try:
            gt_height = float(gt_pages.get(int(page), {}).get("image_height") or 0)
        except ValueError:
            gt_height = 0.0
        try:
            run_height = float(run_docs.get(int(page), {}).get("image_height") or 0)
        except ValueError:
            run_height = 0.0
        match = match_rows(gt_page, run_by[page], gt_height, run_height)
        for index, gt in enumerate(gt_page):
            gt_status[standardised[index].get("label_standard_status", "")] += 1
            run_index = match.get(index)
            if run_index is None:
                continue
            run = run_by[page][run_index]
            expected_raw = norm_label(gt.get("label_gt"))
            actual_raw = norm_label(run.get("label_raw"))
            if expected_raw:
                raw_total += 1
                if expected_raw == actual_raw:
                    raw_exact += 1
            expected_std = standardised[index].get("label_standard") or ""
            actual_std = run.get("label_standard") or ""
            if not expected_std:
                continue
            std_total += 1
            if expected_std == actual_std:
                std_exact += 1
            elif actual_std:
                wrong.append((page, gt.get("gt_row_id", ""), expected_std,
                              actual_std, run.get("label_raw", "")))
            else:
                unmatched.append((page, gt.get("gt_row_id", ""),
                                  expected_std, run.get("label_raw", "")))

    run_status = Counter(row.get("label_standard_status", "") for row in run_rows)
    print(f"Raw label exact       : {raw_exact}/{raw_total} "
          f"({raw_exact / raw_total:.2%})" if raw_total else "Raw: no rows")
    print(f"Standardised exact    : {std_exact}/{std_total} "
          f"({std_exact / std_total:.2%})" if std_total else "Standardised: no rows")
    print("Run label status      : " + ", ".join(
        f"{key or '(blank)'}={value}" for key, value in sorted(run_status.items())
    ))
    print("GT label status       : " + ", ".join(
        f"{key or '(blank)'}={value}" for key, value in sorted(gt_status.items())
    ))
    print(f"Wrong standardised map: {len(wrong)}")
    for page, row_id, expected, actual, raw in wrong[:20]:
        print(f"  p{page} {row_id} raw={raw!r} expected={expected!r} got={actual!r}")
    print(f"Unmatched by run      : {len(unmatched)}")
    for page, row_id, expected, raw in unmatched[:20]:
        print(f"  p{page} {row_id} raw={raw!r} expected={expected!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
