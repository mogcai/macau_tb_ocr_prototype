"""OCR quality summary from a run folder (run_report / tb_rows / documents)."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_AMOUNT_HINT = re.compile(r"\d{2,}")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
_HAS_LATIN = re.compile(r"[A-Za-z]")


def looks_like_amount_label(label: str) -> bool:
    text = (label or "").strip()
    if not text or _HAS_CJK.search(text) or _HAS_LATIN.search(text):
        return False
    if not _AMOUNT_HINT.search(text):
        return False
    return "," in text or text.count(".") >= 2 or bool(re.search(r"\d+\.\d{2}$", text))


def _fraction(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round(part / whole, 4)


def summarize_run_dir(run_dir: Path | str | None) -> dict[str, Any] | None:
    """Return OCR quality metrics for a run directory, or None if unavailable."""
    if not run_dir:
        return None
    path = Path(run_dir)
    if not path.is_dir():
        return None
    rows_path = path / "tb_rows.csv"
    docs_path = path / "documents.csv"
    report_path = path / "run_report.json"
    if not rows_path.is_file():
        return None

    report: dict[str, Any] = {}
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}

    label_x0_by_page: dict[str, str] = {}
    if docs_path.is_file():
        try:
            with docs_path.open(newline="", encoding="utf-8-sig") as handle:
                for item in csv.DictReader(handle):
                    page = str(item.get("pdf_page") or "").strip()
                    if page:
                        label_x0_by_page[page] = str(item.get("label_column_x0") or "")
        except OSError:
            label_x0_by_page = {}

    by_page: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "n": 0, "review": 0, "empty": 0, "amt_in_label": 0,
            "zh": 0, "debit": 0, "credit": 0,
        }
    )
    reasons: Counter[str] = Counter()
    total = review = empty = amt_in_label = zh = debit = credit = 0

    try:
        with rows_path.open(newline="", encoding="utf-8-sig") as handle:
            for item in csv.DictReader(handle):
                page = str(item.get("pdf_page") or "?").strip() or "?"
                bucket = by_page[page]
                bucket["n"] += 1
                total += 1
                lab = (item.get("label_raw") or "").strip()
                if not lab:
                    empty += 1
                    bucket["empty"] += 1
                elif looks_like_amount_label(lab):
                    amt_in_label += 1
                    bucket["amt_in_label"] += 1
                if lab and _HAS_CJK.search(lab):
                    zh += 1
                    bucket["zh"] += 1
                if (item.get("debit_raw") or "").strip():
                    debit += 1
                    bucket["debit"] += 1
                if (item.get("credit_raw") or "").strip():
                    credit += 1
                    bucket["credit"] += 1
                reason = (item.get("review_reason") or "").strip()
                if reason:
                    review += 1
                    bucket["review"] += 1
                    for part in reason.replace(";", ",").split(","):
                        token = part.strip()
                        if token:
                            reasons[token] += 1
    except OSError:
        return None

    empty_rate = _fraction(empty, total)
    warn = bool(
        (empty_rate is not None and empty_rate >= 0.10) or amt_in_label > 0
    )
    pages = []
    for page in sorted(by_page, key=lambda value: (value.isdigit(), int(value) if value.isdigit() else value)):
        bucket = by_page[page]
        n = bucket["n"]
        pages.append({
            "pdf_page": int(page) if page.isdigit() else page,
            "rows": n,
            "review_rows": bucket["review"],
            "empty_labels": bucket["empty"],
            "empty_label_rate": _fraction(bucket["empty"], n),
            "amount_in_label": bucket["amt_in_label"],
            "zh_labels": bucket["zh"],
            "debit_cells": bucket["debit"],
            "credit_cells": bucket["credit"],
            "label_column_x0": label_x0_by_page.get(page) or None,
        })

    return {
        "run_dir": str(path),
        "rows": total,
        "review_rows": review,
        "review_rate": _fraction(review, total),
        "empty_labels": empty,
        "empty_label_rate": empty_rate,
        "amount_in_label": amt_in_label,
        "zh_labels": zh,
        "debit_cells": debit,
        "credit_cells": credit,
        "top_review_reasons": [
            {"reason": reason, "count": count}
            for reason, count in reasons.most_common(8)
        ],
        "pages": pages,
        "warning": warn,
        "warning_reasons": [
            reason for reason, enabled in (
                ("empty_label_rate_ge_10pct", empty_rate is not None and empty_rate >= 0.10),
                ("amount_values_in_label_column", amt_in_label > 0),
            ) if enabled
        ],
        "run_report": {
            "tb_rows_extracted": report.get("tb_rows_extracted"),
            "rows_for_review": report.get("rows_for_review"),
            "tb_pages_detected": report.get("tb_pages_detected"),
            "ocr_engine": report.get("ocr_engine"),
            "vision_level": report.get("vision_level"),
        } if report else {},
    }
