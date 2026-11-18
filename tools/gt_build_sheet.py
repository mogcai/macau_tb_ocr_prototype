#!/usr/bin/env python3
"""Build a pre-filled ground-truth worksheet from an existing OCR run.

The worksheet is only a scaffold: every row starts with ``verified=0`` and the
exact OCR strings that were used to pre-fill it. A human must compare each row
against the source image (or the generated crop) and set ``verified=1`` before
the row can be used for scoring. Pre-filled values are not ground truth.

Run examples::

    python tools/gt_build_sheet.py output_v02_issue45_full_1900 \
        --out ground_truth --issue issue45_2026 --pages 7,10

    python tools/gt_build_sheet.py output --out ground_truth \
        --issue issue45_2026 --pages 7,10,17,9,15,29 --no-crops
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from datetime import datetime
from pathlib import Path

GT_FIELDS = [
    "pdf_page", "gt_row_id", "bbox_y0", "bbox_y1", "y_frac",
    "label_gt", "debit_gt", "credit_gt",
    "debit_dash_gt", "credit_dash_gt",
    "orig_label_ocr", "orig_debit_ocr", "orig_credit_ocr",
    "orig_debit_status", "orig_credit_status",
    "gt_note", "verified", "reviewer", "reviewed_at",
]

PAGE_FIELDS = [
    "pdf_page", "image_path", "image_width", "image_height",
    "bank_name_gt", "statement_date_gt", "number_format_gt",
    "orig_bank_name_ocr", "orig_number_format_ocr",
    "page_note", "verified", "reviewer", "reviewed_at",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_pages(value: str) -> set[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            pages.update(range(int(start), int(end) + 1))
        else:
            pages.add(int(part))
    if not pages:
        raise SystemExit("--pages 需要頁碼，例如 7 或 7,10 或 7-10")
    return pages


def sync_missing_rows(page: int, gt_rows: list[dict[str, str]],
                      run_rows: list[dict[str, str]], image_height: float
                      ) -> list[dict[str, object]]:
    """Return GT rows for run rows that have no verified GT row yet.

    Matching is by row-top position, so existing verified rows are never
    rewritten. Added rows start with ``verified=0`` and an explicit note; the
    reviewer must still check them.
    """
    if not run_rows:
        return []
    heights = []
    for row in gt_rows:
        try:
            heights.append(float(row["bbox_y1"]) - float(row["bbox_y0"]))
        except (TypeError, ValueError, KeyError):
            continue
    tolerance = max(8.0, (statistics.median(heights) if heights else 30.0) * 0.6)

    def top(row: dict[str, str]) -> float | None:
        try:
            return float(row.get("bbox_y0") or "")
        except (TypeError, ValueError):
            return None

    candidates: list[tuple[float, int, int]] = []
    for gi, gt in enumerate(gt_rows):
        gt_y = top(gt)
        if gt_y is None:
            continue
        for ri, run in enumerate(run_rows):
            run_y = top(run)
            if run_y is None:
                continue
            distance = abs(gt_y - run_y)
            if distance <= tolerance:
                candidates.append((distance, gi, ri))
    candidates.sort()
    used_gt: set[int] = set()
    used_run: set[int] = set()
    for _, gi, ri in candidates:
        if gi in used_gt or ri in used_run:
            continue
        used_gt.add(gi)
        used_run.add(ri)

    max_index = 0
    for row in gt_rows:
        match = re.search(r"_r(\d+)$", row.get("gt_row_id", ""))
        if match:
            max_index = max(max_index, int(match.group(1)))

    added: list[dict[str, object]] = []
    unmatched = [i for i in range(len(run_rows)) if i not in used_run]
    for index in sorted(unmatched, key=lambda i: top(run_rows[i]) or 0.0):
        run = run_rows[index]
        max_index += 1
        try:
            y_frac = (round(float(run.get("bbox_y0") or 0) / image_height, 5)
                      if image_height else "")
        except (TypeError, ValueError):
            y_frac = ""
        debit_status = (run.get("debit_parse_status") or "").strip()
        credit_status = (run.get("credit_parse_status") or "").strip()
        added.append({
            "pdf_page": page,
            "gt_row_id": f"p{page:04d}_r{max_index:03d}",
            "bbox_y0": run.get("bbox_y0", ""),
            "bbox_y1": run.get("bbox_y1", ""),
            "y_frac": y_frac,
            "label_gt": (run.get("label_raw") or "").strip(),
            "debit_gt": (run.get("debit_amount") or "").strip(),
            "credit_gt": (run.get("credit_amount") or "").strip(),
            "debit_dash_gt": 1 if debit_status == "dash_or_blank" else 0,
            "credit_dash_gt": 1 if credit_status == "dash_or_blank" else 0,
            "orig_label_ocr": (run.get("label_raw") or "").strip(),
            "orig_debit_ocr": (run.get("debit_raw") or "").strip(),
            "orig_credit_ocr": (run.get("credit_raw") or "").strip(),
            "orig_debit_status": debit_status,
            "orig_credit_status": credit_status,
            "gt_note": "sync_added_unverified",
            "verified": 0,
            "reviewer": "",
            "reviewed_at": "",
        })
    return added


def make_crops(rows: list[dict[str, str]], images_dir: Path, out_dir: Path) -> int:
    try:
        from PIL import Image
    except ImportError:
        print("WARNING: Pillow 未安裝，跳過 crop 產生")
        return 0
    written = 0
    cache: dict[str, Image.Image] = {}
    for row in rows:
        page = row["pdf_page"]
        image_path = images_dir / f"page_{int(page):04d}_table.png"
        if not image_path.exists():
            continue
        if page not in cache:
            cache[page] = Image.open(image_path)
        image = cache[page]
        try:
            y0 = max(0, int(float(row["bbox_y0"]) - 6))
            y1 = min(image.height, int(float(row["bbox_y1"]) + 6))
        except (TypeError, ValueError):
            continue
        if y1 <= y0:
            continue
        crop = image.crop((0, y0, image.width, y1))
        target = out_dir / "crops" / f"page_{int(page):04d}" / f"{row['gt_row_id']}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        crop.save(target)
        written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="建立 ground-truth 工作表（需要人手核對）")
    parser.add_argument("run_dir", help="已完成 OCR 的輸出資料夾（需含 documents.csv、tb_rows.csv、images/）")
    parser.add_argument("--out", default="ground_truth", help="ground truth 根目錄（預設 ground_truth）")
    parser.add_argument("--issue", required=True, help="期號名稱，例如 issue45_2026")
    parser.add_argument("--pages", default="", help="要標註的 PDF 頁碼，例如 7,10 或 7-10")
    parser.add_argument("--reviewer", default="", help="標註者名稱（可稍後在 CSV 填）")
    parser.add_argument("--no-crops", action="store_true", help="不產生逐行 crop 圖")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true",
                      help="覆寫已存在的 gt_rows.csv（會清除已核對內容，慎用）")
    mode.add_argument("--append", action="store_true",
                      help="把未標註過的頁碼追加到現有工作表（保留已核對內容）")
    mode.add_argument("--rebuild-pages", default="",
                      help="重新產生指定頁碼（會重置該頁的 verified；其他頁保留）")
    mode.add_argument("--sync-missing", action="store_true",
                      help="只把新 run 有、GT 未有的行追加到現有工作表（保留所有 verified）")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    docs_path = run_dir / "documents.csv"
    rows_path = run_dir / "tb_rows.csv"
    if not docs_path.exists() or not rows_path.exists():
        raise SystemExit(f"{run_dir} 缺少 documents.csv 或 tb_rows.csv")

    rebuild_pages = parse_pages(args.rebuild_pages) if args.rebuild_pages else set()
    if rebuild_pages:
        pages = set(rebuild_pages)
    elif args.pages:
        pages = parse_pages(args.pages)
    elif args.sync_missing:
        pages = set()
    else:
        raise SystemExit("--pages、--rebuild-pages 或 --sync-missing 需要至少一個頁碼")
    docs = {int(d["pdf_page"]): d for d in read_csv(docs_path) if d.get("pdf_page")}
    missing = sorted(pages - set(docs))
    if missing:
        raise SystemExit(f"run 內找不到頁碼：{missing}")

    all_rows = read_csv(rows_path)
    out_dir = Path(args.out) / args.issue
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_out = out_dir / "gt_rows.csv"
    pages_out = out_dir / "gt_pages.csv"
    if args.sync_missing and not rows_out.exists():
        raise SystemExit("--sync-missing 需要已存在的 gt_rows.csv；請先用 --pages 建立。")
    existing_rows: list[dict[str, str]] = []
    existing_pages: list[dict[str, str]] = []
    if rows_out.exists():
        if args.force:
            pass
        elif args.sync_missing:
            existing_rows = read_csv(rows_out)
            existing_pages = read_csv(pages_out) if pages_out.exists() else []
            target_pages = pages or {
                int(row["pdf_page"]) for row in existing_rows if row.get("pdf_page")
            }
            target_missing = sorted(target_pages - set(docs))
            if target_missing:
                raise SystemExit(f"run 內找不到頁碼：{target_missing}")
            added: list[dict[str, object]] = []
            for page in sorted(target_pages):
                gt_page = [
                    row for row in existing_rows
                    if int(row.get("pdf_page") or 0) == page
                ]
                run_page = [
                    row for row in all_rows
                    if str(row.get("pdf_page")) == str(page)
                ]
                try:
                    image_height = float(docs[page].get("image_height") or 0)
                except ValueError:
                    image_height = 0.0
                added.extend(sync_missing_rows(page, gt_page, run_page, image_height))
            if not added:
                print("冇新行需要加入：新 run 與現有 GT 的行數/位置一致。")
                return 0
            write_csv(rows_out, GT_FIELDS, existing_rows + added)
            crop_count = 0 if args.no_crops else make_crops(
                added, run_dir / "images", out_dir
            )
            print(f"已追加 {len(added)} 行（verified=0，需人手核對）：")
            for row in added:
                print(f"  p{row['pdf_page']} {row['gt_row_id']} "
                      f"{row['label_gt']!r} D={row['debit_gt']!r} C={row['credit_gt']!r}")
            print(f"crops: {crop_count}")
            return 0
        elif args.append:
            existing_rows = read_csv(rows_out)
            existing_pages = read_csv(pages_out) if pages_out.exists() else []
            labelled_pages = {int(r["pdf_page"]) for r in existing_rows if r.get("pdf_page")}
            new_pages = pages - labelled_pages
            if not new_pages:
                raise SystemExit(f"{rows_out} 已包含頁碼 {sorted(pages)}，冇新頁可追加。")
            pages = new_pages
        elif rebuild_pages:
            existing_rows = read_csv(rows_out)
            existing_pages = read_csv(pages_out) if pages_out.exists() else []
            existing_rows = [
                row for row in existing_rows
                if int(row.get("pdf_page") or 0) not in rebuild_pages
            ]
            existing_pages = [
                page for page in existing_pages
                if int(page.get("pdf_page") or 0) not in rebuild_pages
            ]
            print(f"WARNING: 重建頁 {sorted(rebuild_pages)}；該頁原本的 verified 與修正會重置。")
        else:
            raise SystemExit(
                f"{rows_out} 已存在。為免覆蓋已人手核對的內容，預設不會重寫；\n"
                "加 --append 只追加新頁碼、--rebuild-pages 重建指定頁，"
                "或 --force 全部重建（會清除 verified 與修正）。"
            )
    source_pdf = ""
    report_path = run_dir / "run_report.json"
    if report_path.exists():
        try:
            source_pdf = json.loads(report_path.read_text(encoding="utf-8")).get("source_pdf", "")
        except json.JSONDecodeError:
            source_pdf = ""

    page_records: list[dict[str, object]] = []
    gt_rows: list[dict[str, object]] = []
    skipped_empty = 0
    for page in sorted(pages):
        doc = docs[page]
        image_height = 0.0
        try:
            image_height = float(doc.get("image_height") or 0)
        except ValueError:
            image_height = 0.0
        page_records.append({
            "pdf_page": page,
            "image_path": doc.get("image_path", ""),
            "image_width": doc.get("image_width", ""),
            "image_height": doc.get("image_height", ""),
            "bank_name_gt": doc.get("bank_name", ""),
            "statement_date_gt": doc.get("statement_date", ""),
            "number_format_gt": doc.get("number_format_detected", ""),
            "orig_bank_name_ocr": doc.get("bank_name", ""),
            "orig_number_format_ocr": doc.get("number_format_detected", ""),
            "page_note": "",
            "verified": 0,
            "reviewer": args.reviewer,
            "reviewed_at": "",
        })
        page_rows = [r for r in all_rows if str(r.get("pdf_page")) == str(page)]
        index = 0
        for row in page_rows:
            label = (row.get("label_raw") or "").strip()
            debit_ocr = (row.get("debit_raw") or "").strip()
            credit_ocr = (row.get("credit_raw") or "").strip()
            if not label and not debit_ocr and not credit_ocr:
                skipped_empty += 1
                continue
            index += 1
            debit_status = (row.get("debit_parse_status") or "").strip()
            credit_status = (row.get("credit_parse_status") or "").strip()
            try:
                y_frac = round(float(row.get("bbox_y0") or 0) / image_height, 5) if image_height else ""
            except (TypeError, ValueError):
                y_frac = ""
            gt_rows.append({
                "pdf_page": page,
                "gt_row_id": f"p{page:04d}_r{index:03d}",
                "bbox_y0": row.get("bbox_y0", ""),
                "bbox_y1": row.get("bbox_y1", ""),
                "y_frac": y_frac,
                # Pre-filled from OCR so the reviewer can confirm/correct, not trust.
                "label_gt": label,
                "debit_gt": (row.get("debit_amount") or "").strip(),
                "credit_gt": (row.get("credit_amount") or "").strip(),
                "debit_dash_gt": 1 if debit_status == "dash_or_blank" else 0,
                "credit_dash_gt": 1 if credit_status == "dash_or_blank" else 0,
                "orig_label_ocr": label,
                "orig_debit_ocr": debit_ocr,
                "orig_credit_ocr": credit_ocr,
                "orig_debit_status": debit_status,
                "orig_credit_status": credit_status,
                "gt_note": "",
                "verified": 0,
                "reviewer": args.reviewer,
                "reviewed_at": "",
            })

    all_page_records = existing_pages + page_records
    all_gt_rows = existing_rows + gt_rows
    write_csv(pages_out, PAGE_FIELDS, all_page_records)
    write_csv(rows_out, GT_FIELDS, all_gt_rows)

    crop_count = 0
    if not args.no_crops:
        crop_count = make_crops(gt_rows, run_dir / "images", out_dir)

    manifest = {
        "issue": args.issue,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_run": str(run_dir.resolve()),
        "source_pdf": source_pdf,
        "pages": sorted({int(r["pdf_page"]) for r in all_gt_rows if r.get("pdf_page")}),
        "new_pages": sorted(pages),
        "gt_rows": len(all_gt_rows),
        "new_gt_rows": len(gt_rows),
        "skipped_empty_rows": skipped_empty,
        "crops": crop_count,
        "note": "verified=0 means pre-filled by OCR and NOT yet human-checked. "
                "Only verified=1 rows are scored.",
    }
    (out_dir / "worksheet_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\n下一步：用試算表打開 {out_dir / 'gt_rows.csv'}，逐行對住圖／crops 核對，"
          f"改好後將 verified 設為 1。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
