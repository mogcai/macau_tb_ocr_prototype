#!/usr/bin/env python3
"""Score an OCR run against human-verified ground truth.

Only rows with ``verified=1`` in ``gt_rows.csv`` are scored. Metrics separate
structural row alignment, label text accuracy and amount correctness. A wrong
amount that the run marked ``auto_check_pass`` is reported on its own because it
is the most dangerous failure mode.

Example::

    python tools/gt_score.py ground_truth/issue45_2026 --run output_v03_issue45
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

TRUTHY = {"1", "true", "yes", "y"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def is_verified(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def norm_label(text: str | None) -> str:
    value = unicodedata.normalize("NFKC", text or "").replace("\u3000", " ")
    if any("\u3400" <= char <= "\u9fff" for char in value):
        # Vision joins or drops spaces inside Chinese labels; the project's row
        # join removes them too, so spacing is not an OCR-accuracy signal.
        return re.sub(r"\s+", "", value).strip()
    return " ".join(value.split())


DASH_RE = re.compile(r"^\s*[-－—–−]+\s*$")


def is_dash_cell(raw: str | None) -> bool:
    """True only when the cell prints a dash; blank is a different state."""
    return bool(DASH_RE.match(raw or ""))


def to_decimal(text: str | None) -> Decimal | None:
    raw = (text or "").strip().replace(" ", "").replace("\u00a0", "")
    if not raw or raw in {"-", "–", "—", "－"}:
        return None
    raw = raw.replace(",", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, char_b in enumerate(b, 1):
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (char_a != char_b),
            )
        previous = current
    return previous[-1]


def match_rows(gt_rows: list[dict[str, str]], run_rows: list[dict[str, str]],
               gt_height: float, run_height: float) -> dict[int, int]:
    """One-to-one match GT rows to run rows by nearest row top coordinate.

    Heights are stored as fractions of the image height when available, so a
    ground truth sheet still aligns if the OCR run re-renders the page at a
    different pixel scale.
    """
    if not gt_rows or not run_rows:
        return {}
    use_fraction = bool(gt_height and run_height and any(
        str(row.get("y_frac") or "").strip() for row in gt_rows
    ))
    gt_values: list[float | None] = []
    run_values: list[float | None] = []
    heights: list[float] = []
    for row in gt_rows:
        try:
            top = float(row["bbox_y0"])
            bottom = float(row["bbox_y1"])
        except (TypeError, ValueError, KeyError):
            gt_values.append(None)
            continue
        heights.append(bottom - top)
        if use_fraction:
            try:
                gt_values.append(float(row["y_frac"]))
            except (TypeError, ValueError, KeyError):
                gt_values.append(top / gt_height)
        else:
            gt_values.append(top)
    for row in run_rows:
        try:
            top = float(row.get("bbox_y0") or "")
        except (TypeError, ValueError):
            run_values.append(None)
            continue
        run_values.append(top / run_height if use_fraction else top)
    if use_fraction:
        tolerance = max(0.004, (statistics.median(heights) if heights else 0.0)
                        / gt_height * 0.6)
    else:
        tolerance = max(10.0, (statistics.median(heights) if heights else 30.0) * 0.6)
    candidates: list[tuple[float, int, int]] = []
    for gi, gt_y in enumerate(gt_values):
        if gt_y is None:
            continue
        for ri, run_y in enumerate(run_values):
            if run_y is None:
                continue
            distance = abs(gt_y - run_y)
            if distance <= tolerance:
                candidates.append((distance, gi, ri))
    candidates.sort()
    used_gt: set[int] = set()
    used_run: set[int] = set()
    match: dict[int, int] = {}
    for _, gi, ri in candidates:
        if gi in used_gt or ri in used_run:
            continue
        used_gt.add(gi)
        used_run.add(ri)
        match[gi] = ri
    # Only rows that genuinely have no coordinates may be paired by reading
    # order. A row with coordinates that fell outside the tolerance stays
    # unmatched, so a missing row plus a spurious row cannot cancel out and
    # inflate alignment F1.
    remaining_gt = [i for i, value in enumerate(gt_values) if value is None]
    remaining_run = [i for i, value in enumerate(run_values) if value is None]
    for gi, ri in zip(remaining_gt, remaining_run):
        match[gi] = ri
    return match


def score_page(page: int, gt_rows: list[dict[str, str]],
               run_rows: list[dict[str, str]],
               gt_height: float = 0.0, run_height: float = 0.0
               ) -> tuple[dict[str, object], list[dict[str, object]]]:
    match = match_rows(gt_rows, run_rows, gt_height, run_height)
    errors: list[dict[str, object]] = []

    labels_total = labels_exact = 0
    label_distance = 0
    label_characters = 0
    debit_total = debit_exact = 0
    credit_total = credit_exact = 0
    wrong_amount_rows = 0
    auto_pass_wrong = 0
    dash_mismatch_rows = 0
    review_rows = 0

    for gi, gt in enumerate(gt_rows):
        ri = match.get(gi)
        run = run_rows[ri] if ri is not None else {}
        expected_label = norm_label(gt.get("label_gt"))
        actual_label = norm_label(run.get("label_raw"))
        if expected_label:
            labels_total += 1
            label_characters += len(expected_label)
            if expected_label == actual_label:
                labels_exact += 1
            else:
                label_distance += edit_distance(expected_label, actual_label)
                errors.append({
                    "pdf_page": page, "gt_row_id": gt.get("gt_row_id", ""),
                    "field": "label", "gt_value": expected_label,
                    "run_value": actual_label,
                    "run_label_raw": run.get("label_raw", ""),
                    "run_review_status": run.get("review_status", ""),
                    "run_review_reason": run.get("review_reason", ""),
                })
        elif actual_label:
            errors.append({
                "pdf_page": page, "gt_row_id": gt.get("gt_row_id", ""),
                "field": "label_spurious", "gt_value": "",
                "run_value": actual_label,
                "run_label_raw": run.get("label_raw", ""),
                "run_review_status": run.get("review_status", ""),
                "run_review_reason": run.get("review_reason", ""),
            })

        row_wrong = False
        row_dash_mismatch = False
        for column in ("debit", "credit"):
            expected = to_decimal(gt.get(f"{column}_gt"))
            actual = to_decimal(run.get(f"{column}_amount"))
            gt_dash = str(gt.get(f"{column}_dash_gt") or "").strip() == "1"
            run_raw = run.get(f"{column}_raw") or ""
            run_status = run.get(f"{column}_parse_status") or ""
            run_dash = is_dash_cell(run_raw) or run_status == "dash"
            if expected is not None:
                if column == "debit":
                    debit_total += 1
                else:
                    credit_total += 1
                if actual == expected:
                    if column == "debit":
                        debit_exact += 1
                    else:
                        credit_exact += 1
                else:
                    row_wrong = True
                    errors.append({
                        "pdf_page": page, "gt_row_id": gt.get("gt_row_id", ""),
                        "field": f"{column}_value", "gt_value": str(expected),
                        "run_value": "" if actual is None else str(actual),
                        "run_label_raw": run.get("label_raw", ""),
                        "run_review_status": run.get("review_status", ""),
                        "run_review_reason": run.get("review_reason", ""),
                    })
            elif actual is not None:
                # GT says no amount (blank or dash); a value here is spurious.
                row_wrong = True
                errors.append({
                    "pdf_page": page, "gt_row_id": gt.get("gt_row_id", ""),
                    "field": f"{column}_spurious",
                    "gt_value": "dash" if gt_dash else "blank",
                    "run_value": str(actual),
                    "run_label_raw": run.get("label_raw", ""),
                    "run_review_status": run.get("review_status", ""),
                    "run_review_reason": run.get("review_reason", ""),
                })
            elif gt_dash != run_dash:
                # Both have no value, but a printed dash and an empty cell are
                # different states and must not be scored as a match.
                row_dash_mismatch = True
                errors.append({
                    "pdf_page": page, "gt_row_id": gt.get("gt_row_id", ""),
                    "field": f"{column}_state",
                    "gt_value": "dash" if gt_dash else "blank",
                    "run_value": "dash" if run_dash else "blank",
                    "run_label_raw": run.get("label_raw", ""),
                    "run_review_status": run.get("review_status", ""),
                    "run_review_reason": run.get("review_reason", ""),
                })
        if row_dash_mismatch:
            dash_mismatch_rows += 1
        if row_wrong:
            wrong_amount_rows += 1
            if run.get("review_status") == "auto_check_pass":
                auto_pass_wrong += 1
        if ri is not None and run.get("review_status") == "needs_review":
            review_rows += 1

    matched = len(match)
    gt_count = len(gt_rows)
    run_count = len(run_rows)
    precision = matched / run_count if run_count else 0.0
    recall = matched / gt_count if gt_count else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    metrics = {
        "gt_rows": gt_count,
        "run_rows": run_count,
        "matched_rows": matched,
        "gt_missing_rows": gt_count - matched,
        "run_spurious_rows": run_count - matched,
        "alignment_precision": round(precision, 4),
        "alignment_recall": round(recall, 4),
        "alignment_f1": round(f1, 4),
        "label_rows": labels_total,
        "label_exact": labels_exact,
        "label_exact_rate": round(labels_exact / labels_total, 4) if labels_total else None,
        "label_characters": label_characters,
        "label_edit_distance": label_distance,
        "label_cer": (
            round(label_distance / label_characters, 4)
            if label_characters else None
        ),
        "debit_rows": debit_total,
        "debit_exact": debit_exact,
        "debit_exact_rate": round(debit_exact / debit_total, 4) if debit_total else None,
        "credit_rows": credit_total,
        "credit_exact": credit_exact,
        "credit_exact_rate": round(credit_exact / credit_total, 4) if credit_total else None,
        "wrong_amount_rows": wrong_amount_rows,
        "auto_pass_wrong_amount_rows": auto_pass_wrong,
        "dash_mismatch_rows": dash_mismatch_rows,
        "review_rate": round(review_rows / matched, 4) if matched else None,
    }
    return metrics, errors


def merge_metrics(per_page: dict[int, dict[str, object]]) -> dict[str, object]:
    def total(key: str) -> int:
        return sum(int(page.get(key) or 0) for page in per_page.values())

    matched = total("matched_rows")
    gt_count = total("gt_rows")
    run_count = total("run_rows")
    precision = matched / run_count if run_count else 0.0
    recall = matched / gt_count if gt_count else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    label_rows = total("label_rows")
    debit_rows = total("debit_rows")
    credit_rows = total("credit_rows")
    return {
        "gt_rows": gt_count,
        "run_rows": run_count,
        "matched_rows": matched,
        "gt_missing_rows": total("gt_missing_rows"),
        "run_spurious_rows": total("run_spurious_rows"),
        "alignment_precision": round(precision, 4),
        "alignment_recall": round(recall, 4),
        "alignment_f1": round(f1, 4),
        "label_rows": label_rows,
        "label_exact": total("label_exact"),
        "label_exact_rate": round(total("label_exact") / label_rows, 4) if label_rows else None,
        "label_characters": total("label_characters"),
        "label_edit_distance": total("label_edit_distance"),
        "label_cer": (
            round(total("label_edit_distance") / total("label_characters"), 4)
            if total("label_characters") else None
        ),
        "debit_rows": debit_rows,
        "debit_exact": total("debit_exact"),
        "debit_exact_rate": round(total("debit_exact") / debit_rows, 4) if debit_rows else None,
        "credit_rows": credit_rows,
        "credit_exact": total("credit_exact"),
        "credit_exact_rate": round(total("credit_exact") / credit_rows, 4) if credit_rows else None,
        "wrong_amount_rows": total("wrong_amount_rows"),
        "auto_pass_wrong_amount_rows": total("auto_pass_wrong_amount_rows"),
        "dash_mismatch_rows": total("dash_mismatch_rows"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="用人工核對的 ground truth 評分 OCR run")
    parser.add_argument("gt_dir", help="ground truth 資料夾（含 gt_pages.csv、gt_rows.csv）")
    parser.add_argument("--run", required=True, help="要評分的 OCR 輸出資料夾")
    parser.add_argument("--out", default="", help="報告輸出資料夾（預設：ground truth 資料夾）")
    args = parser.parse_args()

    gt_dir = Path(args.gt_dir)
    run_dir = Path(args.run)
    rows = read_csv(gt_dir / "gt_rows.csv")
    pages_path = gt_dir / "gt_pages.csv"
    page_meta = {int(p["pdf_page"]): p for p in read_csv(pages_path)} if pages_path.exists() else {}

    verified = [r for r in rows if is_verified(r.get("verified"))]
    if not verified:
        raise SystemExit(
            "gt_rows.csv 內未有任何 verified=1 的行。\n"
            "請先逐行對照來源圖片／crops 核對，改好 label_gt、debit_gt、credit_gt，"
            "再將 verified 設為 1。未核對的行不會計分。"
        )

    run_rows_all = read_csv(run_dir / "tb_rows.csv")
    run_docs: dict[int, dict[str, str]] = {}
    run_docs_path = run_dir / "documents.csv"
    if run_docs_path.exists():
        run_docs = {int(d["pdf_page"]): d for d in read_csv(run_docs_path) if d.get("pdf_page")}
    gt_pages = sorted({int(r["pdf_page"]) for r in verified})
    # Score only pages that the run actually processed; a single-page run must
    # not be penalised for the other GT pages it never saw.
    if run_docs:
        pages = [p for p in gt_pages if p in run_docs]
        skipped_pages = [p for p in gt_pages if p not in run_docs]
    else:
        pages = gt_pages
        skipped_pages = []
    if not pages:
        raise SystemExit(
            f"run 內找不到 GT 頁碼 {gt_pages}；請確認 --run 是否包含這些頁。"
        )

    per_page: dict[int, dict[str, object]] = {}
    errors: list[dict[str, object]] = []
    for page in pages:
        gt_page = [r for r in verified if int(r["pdf_page"]) == page]
        run_page = [r for r in run_rows_all if str(r.get("pdf_page")) == str(page)]
        try:
            gt_height = float(page_meta.get(page, {}).get("image_height") or 0)
        except ValueError:
            gt_height = 0.0
        try:
            run_height = float(run_docs.get(page, {}).get("image_height") or 0)
        except ValueError:
            run_height = 0.0
        metrics, page_errors = score_page(
            page, gt_page, run_page, gt_height=gt_height, run_height=run_height
        )
        per_page[page] = metrics
        errors.extend(page_errors)

    overall = merge_metrics(per_page)
    unverified = len(rows) - len(verified)
    unverified_pages = [
        p for p in pages
        if not is_verified(page_meta.get(p, {}).get("verified"))
    ]
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ground_truth_dir": str(gt_dir.resolve()),
        "run_dir": str(run_dir.resolve()),
        "pages": pages,
        "skipped_pages_not_in_run": skipped_pages,
        "unverified_page_metadata": unverified_pages,
        "verified_gt_rows": len(verified),
        "unverified_gt_rows_excluded": unverified,
        "page_number_format_gt": {
            str(p): page_meta[p].get("number_format_gt", "") for p in pages if p in page_meta
        },
        "overall": overall,
        "per_page": {str(p): m for p, m in per_page.items()},
        "warnings": [
            "Row alignment is matched by y position, not by financial content.",
            "A wrong amount marked auto_check_pass is a silent-error candidate and must be investigated.",
            "These metrics describe OCR extraction only; they are not a financial audit.",
        ],
    }
    out_dir = Path(args.out) if args.out else gt_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "score_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    error_fields = ["pdf_page", "gt_row_id", "field", "gt_value", "run_value",
                    "run_label_raw", "run_review_status", "run_review_reason"]
    with (out_dir / "score_errors.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=error_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(errors)

    o = overall
    print(f"Ground truth: {len(verified)} verified rows on pages {pages} "
          f"({unverified} unverified rows excluded)")
    if skipped_pages:
        print(f"WARNING: GT pages not present in this run were skipped: {skipped_pages}")
    if unverified_pages:
        print(f"WARNING: page-level GT metadata is still verified=0 for pages {unverified_pages}; "
              f"row amounts/labels are still scored, but bank/date/number_format_gt are unchecked.")
    print(f"Rows: run={o['run_rows']} matched={o['matched_rows']} "
          f"gt_missing={o['gt_missing_rows']} run_spurious={o['run_spurious_rows']} "
          f"alignment F1={o['alignment_f1']}")
    print(f"Labels: exact={o['label_exact_rate']} CER={o['label_cer']} "
          f"(rows={o['label_rows']}, chars={o['label_characters']})")
    print(f"Debit : exact={o['debit_exact_rate']} (n={o['debit_rows']})")
    print(f"Credit: exact={o['credit_exact_rate']} (n={o['credit_rows']})")
    print(f"Wrong amounts: {o['wrong_amount_rows']} rows, "
          f"of which auto_check_pass: {o['auto_pass_wrong_amount_rows']}")
    print(f"Dash/blank state mismatches: {o['dash_mismatch_rows']} rows")
    print(f"Report: {out_dir / 'score_report.json'}")
    print(f"Errors: {out_dir / 'score_errors.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
