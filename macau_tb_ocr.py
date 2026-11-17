#!/usr/bin/env python3
"""Macau Gazette Balancete OCR prototype.

Extracts embedded statement images from PDFs, runs PaddleOCR by detected language,
reconstructs rows using the table's vertical rules, and exports CSV/XLSX review files.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
import unicodedata
from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import fitz  # PyMuPDF

APP_VERSION = "0.1.0"
TB_TERMS = (
    "試算表", "BALANCETE", "TRIAL BALANCE", "BALANCE OF THE LEDGER",
    "BALANCETE DO RAZAO", "BALANCETE DO RAZÃO",
)
PT_TERMS = (
    "BALANCETE", "DEVEDORES", "CREDORES", "SALDOS", "RAZAO", "RAZÃO",
    "DEBITOS", "CREDITOS", "DÉBITOS", "CRÉDITOS",
)
EN_TERMS = (
    "TRIAL BALANCE", "DEBIT", "CREDIT", "DEBITORS", "CREDITORS",
    "ASSETS", "LIABILITIES", "TOTALS",
)


@dataclass
class OCRBox:
    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float
    language: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass
class TableImage:
    page_index: int
    image_path: Path
    width: int
    height: int
    xref: int | None
    extracted_from_embedded_image: bool


def norm_text(value: str) -> str:
    """Unicode-normalise common compatibility glyphs in Chinese/Portuguese text."""
    return unicodedata.normalize("NFKC", value or "").replace("\u3000", " ").strip()


def has_chinese(text: str) -> bool:
    return any("\u3400" <= ch <= "\u9fff" for ch in text)


def detect_languages(page_text: str) -> list[str]:
    """Pick OCR language profiles using page-level native text as a hint.

    The Chinese Traditional model also reads English/numbers. Portuguese pages use
    the Portuguese recognizer. Bilingual pages can trigger both passes.
    """
    # Ignore the standard bilingual gazette footer/cost notice. It appears on
    # virtually every page and must not make Chinese TB pages look Portuguese.
    core = norm_text(page_text)
    core = re.split(r"是項刊登費用|Custo desta publicação", core, maxsplit=1, flags=re.I)[0]
    upper = core.upper()
    langs: list[str] = []
    if has_chinese(core):
        langs.append("chinese_cht")
    if any(term in upper for term in PT_TERMS) or re.search(
        r"DIRECTOR\s+EXECUTIVO|GERENTE|CHEFE\s+DA\s+CONTABILIDADE|COMISS[AÃ]O\s+EXECUTIVA|SUCURSAL", upper
    ):
        langs.append("pt")
    if any(term in upper for term in EN_TERMS) and not any(term in upper for term in PT_TERMS):
        langs.append("en")
    # A Latin-script page with the standard Macau Portuguese legal/signatory
    # vocabulary is more likely Portuguese than English.
    if not langs and re.search(r"MACAU|MACAO|DIRECTOR|BANCO|SUCURSAL", upper):
        langs.append("pt")
    if not langs:
        # If the native layer is Latin-only and lacks Portuguese cues, English is
        # the safer profile; an operator can override with --ocr-langs.
        langs = ["en"] if re.search(r"[A-Za-z]", core) and not has_chinese(core) else ["chinese_cht"]
    # Avoid duplicate language profiles while preserving order.
    return list(dict.fromkeys(langs))


def is_tb_page(page_text: str, has_large_image: bool) -> bool:
    upper = norm_text(page_text).upper()
    has_keyword = any(term in upper for term in TB_TERMS)
    # A few Portuguese-language pages put the bank/title only inside the image;
    # the native layer may contain only signatory lines and the publication fee.
    has_gazette_fee = "CUSTO DESTA PUBLICAÇÃO" in upper or "是項刊登費用" in upper
    return has_large_image and (has_keyword or has_gazette_fee)


def _cn_number(token: str) -> int | None:
    token = norm_text(token)
    digit_map = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                 "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if token.isdigit():
        return int(token)
    if not token or any(ch not in digit_map and ch != "十" for ch in token):
        return None
    if "十" not in token:
        try:
            return int("".join(str(digit_map[ch]) for ch in token))
        except KeyError:
            return None
    if token == "十":
        return 10
    left, right = token.split("十", 1)
    tens = digit_map.get(left, 1) if left else 1
    units = digit_map.get(right, 0) if right else 0
    return tens * 10 + units


def parse_statement_date(text: str) -> str:
    t = norm_text(text)
    # Chinese date: 於二零二六年九月三十日 or 於2026年09月30日
    m = re.search(r"[於于]?\s*([〇零一二三四五六七八九十0-9]+)年\s*([〇零一二三四五六七八九十0-9]+)月\s*([〇零一二三四五六七八九十0-9]+)日", t)
    if m:
        y, mo, d = (_cn_number(x) for x in m.groups())
        if y and mo and d:
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                pass
    # Portuguese/English date. Month names are normalised without accents.
    month_map = {
        "janeiro": 1, "january": 1, "fevereiro": 2, "february": 2,
        "marco": 3, "march": 3, "abril": 4, "april": 4,
        "maio": 5, "may": 5, "junho": 6, "june": 6,
        "julho": 7, "july": 7, "agosto": 8, "august": 8,
        "setembro": 9, "september": 9, "outubro": 10, "october": 10,
        "novembro": 11, "november": 11, "dezembro": 12, "december": 12,
    }
    folded = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii").lower()
    m = re.search(r"\b(\d{1,2})\s+(?:de\s+)?([a-z]+)\s+(?:de\s+)?(20\d{2})\b", folded)
    if m and m.group(2) in month_map:
        try:
            return date(int(m.group(3)), month_map[m.group(2)], int(m.group(1))).isoformat()
        except ValueError:
            pass
    return ""


def extract_metadata(page_text: str, pdf_page_number: int) -> dict[str, str | int]:
    t = norm_text(page_text)
    lines = [norm_text(x) for x in t.splitlines() if norm_text(x)]
    # Find first statement heading and treat the preceding one or two lines as the bank name.
    heading_idx = len(lines)
    heading_re = re.compile(r"試算表|BALANCETE|TRIAL\s+BALANCE|BALANCE\s+OF\s+THE\s+LEDGER", re.I)
    for i, line in enumerate(lines):
        if heading_re.search(line):
            heading_idx = i
            break
    heading_found = heading_idx < len(lines)
    bank_lines = lines[:heading_idx]
    bank_lines = [x for x in bank_lines if not re.fullmatch(r"\d{1,4}", x)]
    bank_lines = [x for x in bank_lines if not re.search(r"BOLETIM OFICIAL|澳門特別行政區公報|第\s*\d+\s*期|N\.?º\s*\d+", x, re.I)]
    # If the native PDF has no statement heading, these lines may only be
    # signatory roles; OCR of the image header will supply the entity name.
    bank_name = " | ".join(bank_lines[:3] if heading_found else []).strip(" |")
    # Printed gazette page number appears as a standalone number in the selectable text.
    printed_page = ""
    for line in reversed(lines):
        if re.fullmatch(r"\d{2,4}", line):
            printed_page = line
            break
    pub_date = ""
    m = re.search(r"(?:N\.?º\s*\d+\s*[—–-]\s*)(\d{2}-\d{2}-\d{4})", t, re.I)
    if m:
        pub_date = m.group(1)
    else:
        m = re.search(r"第\s*\d+\s*期.*?(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
        if m:
            try:
                pub_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                pass
    if heading_found and bank_name:
        bank_source = "pdf_text"
    elif heading_found:
        bank_source = "missing_in_pdf_text"
    else:
        bank_source = "image_header_check"
    return {
        "pdf_page": pdf_page_number,
        "printed_page": printed_page,
        "bank_name": bank_name,
        "statement_date": parse_statement_date(t),
        "publication_date": pub_date,
        "bank_name_source": bank_source,
    }


def extract_best_embedded_image(doc: fitz.Document, page: fitz.Page, page_index: int, out_path: Path) -> TableImage:
    candidates: list[tuple[int, int, int]] = []
    for item in page.get_images(full=True):
        xref = int(item[0])
        try:
            pix = fitz.Pixmap(doc, xref)
        except Exception:
            continue
        if pix.width >= 1200 and pix.height >= 900:
            candidates.append((pix.width * pix.height, xref, pix.width))
    if candidates:
        _, xref, _ = max(candidates)
        pix = fitz.Pixmap(doc, xref)
        # Convert CMYK/Gray/alpha to a predictable RGB PNG for OpenCV and OCR.
        if pix.colorspace is None or pix.colorspace.n != 3 or pix.alpha:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        else:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        return TableImage(page_index, out_path, pix.width, pix.height, xref, True)

    # Fallback: render the page itself. This is less ideal and should be manually checked.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=False)
    pix.save(str(out_path))
    return TableImage(page_index, out_path, pix.width, pix.height, None, False)


def detect_column_boundaries(image_path: Path) -> tuple[float, float, str]:
    """Find two internal vertical table rules; returns normalised x fractions."""
    try:
        import cv2
        import numpy as np
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError("cv2 could not load image")
        h, w = image.shape[:2]
        binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        kernel_h = max(25, int(h * 0.12))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_h))
        vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        projection = (vertical > 0).sum(axis=0)
        candidates = []
        min_run = max(2, int(w * 0.0015))
        xs = np.where(projection >= max(20, int(h * 0.10)))[0]
        if len(xs):
            groups: list[list[int]] = [[int(xs[0])]]
            for x in xs[1:]:
                x = int(x)
                if x - groups[-1][-1] <= min_run:
                    groups[-1].append(x)
                else:
                    groups.append([x])
            for g in groups:
                x = int(round(sum(g) / len(g)))
                frac = x / w
                strength = float(projection[g].max())
                if 0.08 < frac < 0.92:
                    candidates.append((strength, frac))
        # Select two strongest internal rules, then sort left-to-right.
        candidates.sort(reverse=True)
        chosen: list[float] = []
        for _, frac in candidates:
            if all(abs(frac - old) > 0.035 for old in chosen):
                chosen.append(frac)
            if len(chosen) == 2:
                break
        if len(chosen) == 2:
            chosen.sort()
            if chosen[0] + 0.08 < chosen[1]:
                return chosen[0], chosen[1], "detected_morphology"

        # Some scans have interrupted/faint vertical rules, so morphology-open
        # may not preserve them. Raw dark-pixel column projection is a fallback.
        dark_projection = (image < 128).sum(axis=0).astype(float)
        smooth_window = max(5, int(w * 0.002))
        smooth = np.convolve(dark_projection, np.ones(smooth_window), mode="same")
        local_peaks = []
        min_strength = max(h * 0.16, float(smooth.max()) * 0.16)
        for x in range(1, w - 1):
            frac = x / w
            if 0.08 < frac < 0.92 and smooth[x] >= smooth[x - 1] and smooth[x] > smooth[x + 1] and smooth[x] >= min_strength:
                local_peaks.append((float(smooth[x]), frac))
        local_peaks.sort(reverse=True)
        chosen = []
        for _, frac in local_peaks:
            if all(abs(frac - old) > 0.045 for old in chosen):
                chosen.append(frac)
            if len(chosen) == 2:
                break
        if len(chosen) == 2:
            chosen.sort()
            if chosen[0] + 0.08 < chosen[1]:
                return chosen[0], chosen[1], "detected_projection"
    except Exception:
        pass
    return 0.48, 0.75, "fallback_0.48_0.75"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value) if not isinstance(value, (str, bytes)) else [value]


def _parse_v3_result(item: Any, language: str) -> list[OCRBox]:
    payload = getattr(item, "json", item)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("res"), dict):
        payload = payload["res"]
    texts = _as_list(payload.get("rec_texts"))
    scores = _as_list(payload.get("rec_scores"))
    polys = _as_list(payload.get("rec_polys"))
    boxes = _as_list(payload.get("rec_boxes"))
    if not polys and boxes:
        polys = [
            [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]
            for b in boxes if len(b) >= 4
        ]
    out: list[OCRBox] = []
    for i, text in enumerate(texts):
        if i >= len(polys) or not str(text).strip():
            continue
        try:
            pts = _as_list(polys[i])
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            score = float(scores[i]) if i < len(scores) else 0.0
            out.append(OCRBox(str(text).strip(), score, min(xs), min(ys), max(xs), max(ys), language))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def run_tesseract_ocr(image_path: Path, language: str) -> list[OCRBox]:
    """Optional local baseline. PaddleOCR remains the recommended primary engine."""
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise RuntimeError("Tesseract engine 需要先安裝 pytesseract；另需安裝系統 tesseract executable。") from exc
    lang_map = {"chinese_cht": "HanT+eng", "pt": "por+eng", "en": "eng"}
    tess_lang = lang_map.get(language, "eng")
    data = pytesseract.image_to_data(
        str(image_path), lang=tess_lang, config="--psm 6", output_type=Output.DICT
    )
    out: list[OCRBox] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = str(data["text"][i]).strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
            if conf < 0:
                continue
            x0 = float(data["left"][i]); y0 = float(data["top"][i])
            x1 = x0 + float(data["width"][i]); y1 = y0 + float(data["height"][i])
            out.append(OCRBox(text, max(0.0, min(1.0, conf / 100.0)), x0, y0, x1, y1, language))
        except (ValueError, KeyError, IndexError):
            continue
    return out


def run_paddle_ocr(image_path: Path, language: str, device: str, version: str,
                   engines: dict[str, Any]) -> list[OCRBox]:
    if language not in engines:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "搵唔到 PaddleOCR。請先按 README 安裝 PaddlePaddle 及 PaddleOCR。"
            ) from exc
        engines[language] = PaddleOCR(
            lang=language,
            ocr_version=version,
            device=device,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_det_limit_type="max",
            text_det_limit_side_len=4096,
        )
    engine = engines[language]
    results = engine.predict(str(image_path))
    boxes: list[OCRBox] = []
    for item in results:
        boxes.extend(_parse_v3_result(item, language))
    return boxes


def run_ocr(image_path: Path, language: str, engine_name: str, device: str,
            version: str, engines: dict[str, Any]) -> list[OCRBox]:
    if engine_name == "tesseract":
        return run_tesseract_ocr(image_path, language)
    return run_paddle_ocr(image_path, language, device, version, engines)


def dedupe_boxes(boxes: list[OCRBox]) -> list[OCRBox]:
    """Deduplicate boxes when bilingual pages are OCR'd with multiple models."""
    kept: list[OCRBox] = []
    for box in sorted(boxes, key=lambda b: b.score, reverse=True):
        duplicate = False
        for old in kept:
            ix0, iy0 = max(box.x0, old.x0), max(box.y0, old.y0)
            ix1, iy1 = min(box.x1, old.x1), min(box.y1, old.y1)
            inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            area_a = max(1.0, (box.x1 - box.x0) * (box.y1 - box.y0))
            area_b = max(1.0, (old.x1 - old.x0) * (old.y1 - old.y0))
            iou = inter / max(1.0, area_a + area_b - inter)
            if iou >= 0.55:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return sorted(kept, key=lambda b: (b.cy, b.x0))


def group_boxes_into_rows(boxes: list[OCRBox], image_width: int, image_height: int,
                          boundaries: tuple[float, float]) -> list[dict[str, Any]]:
    if not boxes:
        return []
    heights = [b.height for b in boxes if b.height > 2]
    median_h = statistics.median(heights) if heights else max(10, image_height * 0.01)
    tolerance = max(8.0, median_h * 0.58)
    rows: list[dict[str, Any]] = []
    for box in sorted(boxes, key=lambda b: (b.cy, b.x0)):
        best = None
        best_dist = None
        for row in reversed(rows[-4:]):
            dist = abs(box.cy - row["cy"])
            if dist <= tolerance and (best_dist is None or dist < best_dist):
                best, best_dist = row, dist
        if best is None:
            rows.append({"cy": box.cy, "boxes": [box]})
        else:
            best["boxes"].append(box)
            best["cy"] = statistics.mean(x.cy for x in best["boxes"])
    rows.sort(key=lambda r: r["cy"])
    sep1, sep2 = boundaries
    structured: list[dict[str, Any]] = []
    for row in rows:
        cols: dict[str, list[OCRBox]] = {"label": [], "debit": [], "credit": []}
        for b in row["boxes"]:
            # Use the box's left edge, not its centre: long account descriptions
            # can extend close to the first separator while still belonging to col 1.
            start_frac = b.x0 / max(1.0, float(image_width))
            if start_frac < sep1:
                cols["label"].append(b)
            elif start_frac < sep2:
                cols["debit"].append(b)
            else:
                cols["credit"].append(b)
        for key in cols:
            cols[key].sort(key=lambda x: x.x0)
        def join_col(arr: list[OCRBox]) -> str:
            value = " ".join(x.text for x in arr).strip()
            if has_chinese(value):
                value = re.sub(r"\s+", "", value)
            return value
        all_scores = [x.score for x in row["boxes"]]
        label_scores = [x.score for x in cols["label"]]
        debit_scores = [x.score for x in cols["debit"]]
        credit_scores = [x.score for x in cols["credit"]]
        confidence = min(all_scores) if all_scores else 0.0
        structured.append({
            "y_center": round(row["cy"], 2),
            "label_raw": join_col(cols["label"]),
            "debit_raw": join_col(cols["debit"]),
            "credit_raw": join_col(cols["credit"]),
            "label_confidence": round(min(label_scores), 4) if label_scores else "",
            "debit_confidence": round(min(debit_scores), 4) if debit_scores else "",
            "credit_confidence": round(min(credit_scores), 4) if credit_scores else "",
            "row_confidence": round(confidence, 4),
            "row_average_confidence": round(statistics.mean(all_scores), 4) if all_scores else 0.0,
            "bbox_x0": round(min(x.x0 for x in row["boxes"]), 2),
            "bbox_y0": round(min(x.y0 for x in row["boxes"]), 2),
            "bbox_x1": round(max(x.x1 for x in row["boxes"]), 2),
            "bbox_y1": round(max(x.y1 for x in row["boxes"]), 2),
            "languages": ",".join(sorted(set(x.language for x in row["boxes"]))),
        })
    # Remove only recognizable column-heading rows near the top. Do not use a
    # fixed top crop: some TBs place the first real account line very close to y=0.
    header_re = re.compile(
        r"帳戶名稱|科目名稱|餘額|借方|貸方|SALDOS|DEVEDORES|CREDORES|"
        r"DESIGNA[CÇ][AÃ]O DAS CONTAS|BALANCETE DO RAZ[AÃ]O|TRIAL BALANCE",
        re.I,
    )
    return [
        r for r in structured
        if not (r["y_center"] <= image_height * 0.24 and header_re.search(
            " ".join((r.get("label_raw", ""), r.get("debit_raw", ""), r.get("credit_raw", "")))
        ))
    ]


def parse_amount(raw: str) -> tuple[str, str]:
    """Return (decimal string, issue). Never silently repair ambiguous characters."""
    s = norm_text(raw).replace(" ", "").replace("−", "-").replace("–", "-")
    if not s:
        return "", ""
    if s in {"-", "—", "–", "－", "."}:
        return "", "dash_or_blank"
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    # Currency labels/symbols occasionally appear beside numeric values.
    s = re.sub(r"(?i)(MOP|HKD|USD|RMB|AOA|\$|€|£)", "", s)
    if re.search(r"[OoIl|]", s):
        candidate = s.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}))
        issue = "ambiguous_ocr_character"
    else:
        candidate, issue = s, ""
    # Both separators: rightmost one is decimal separator.
    if "," in candidate and "." in candidate:
        if candidate.rfind(".") > candidate.rfind(","):
            candidate = candidate.replace(",", "")
        else:
            candidate = candidate.replace(".", "").replace(",", ".")
    elif "," in candidate:
        tail = candidate.rsplit(",", 1)[-1]
        if len(tail) in (1, 2):
            candidate = candidate.replace(".", "").replace(",", ".")
        else:
            candidate = candidate.replace(",", "")
    # Remove thin-space/group separators, but do not guess letters or missing digits.
    if not re.fullmatch(r"-?\d+(?:\.\d{1,4})?", candidate):
        return "", issue or "unparsed_amount"
    try:
        value = Decimal(candidate)
        if negative:
            value = -value
        # Keep exact decimal representation and avoid binary float rounding.
        return format(value, "f"), issue
    except InvalidOperation:
        return "", issue or "unparsed_amount"


def validate_rows(rows: list[dict[str, Any]], confidence_threshold: float) -> list[dict[str, Any]]:
    for row in rows:
        debit, debit_issue = parse_amount(row.get("debit_raw", ""))
        credit, credit_issue = parse_amount(row.get("credit_raw", ""))
        row["debit_amount"] = debit
        row["credit_amount"] = credit
        issues: list[str] = []
        for col, issue in (("debit", debit_issue), ("credit", credit_issue)):
            if issue and issue != "dash_or_blank":
                issues.append(f"{col}:{issue}")
        if row.get("row_confidence", 0) < confidence_threshold:
            issues.append("low_ocr_confidence")
        if not row.get("label_raw") and (debit or credit):
            issues.append("amount_without_account_label")
        label_upper = norm_text(row.get("label_raw", "")).upper()
        if any(x in label_upper for x in ("總額", "合計", "TOTAL", "TOTAIS")):
            if debit and credit:
                try:
                    if abs(Decimal(debit) - Decimal(credit)) > Decimal("0.01"):
                        issues.append("debit_credit_total_mismatch")
                except InvalidOperation:
                    issues.append("total_validation_error")
        row["review_reason"] = ";".join(issues)
        row["review_status"] = "needs_review" if issues else "auto_check_pass"
    return rows


def ocr_boxes_to_lines(boxes: list[OCRBox], max_y: float) -> list[str]:
    """Merge word-level OCR boxes into reading lines for image-header metadata."""
    selected = [b for b in boxes if b.cy <= max_y]
    if not selected:
        return []
    median_h = statistics.median(b.height for b in selected)
    tolerance = max(8.0, median_h * 0.60)
    groups: list[dict[str, Any]] = []
    for box in sorted(selected, key=lambda b: (b.cy, b.x0)):
        target = next((g for g in reversed(groups[-4:]) if abs(box.cy - g["cy"]) <= tolerance), None)
        if target is None:
            groups.append({"cy": box.cy, "items": [box]})
        else:
            target["items"].append(box)
            target["cy"] = statistics.mean(x.cy for x in target["items"])
    lines = []
    for group in sorted(groups, key=lambda g: g["cy"]):
        items = sorted(group["items"], key=lambda b: b.x0)
        line = " ".join(x.text for x in items).strip()
        if has_chinese(line):
            line = re.sub(r"\s+", "", line)
        if line:
            lines.append(line)
    return lines


def make_export_row(meta: dict[str, Any], row: dict[str, Any], image_path: Path) -> dict[str, Any]:
    result = {**meta, **row}
    result["source_image"] = str(image_path)
    result.pop("y_center", None)
    return result


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        path.write_text("", encoding="utf-8-sig")
        return
    # Stable union of fields in first-seen order.
    fields: list[str] = []
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def write_excel(path: Path, rows: list[dict[str, Any]], docs: list[dict[str, Any]], raw: list[dict[str, Any]]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    datasets = [
        ("TB_Rows", rows),
        ("Review_Queue", [r for r in rows if r.get("review_status") == "needs_review"]),
        ("Documents", docs),
        ("OCR_Raw", raw),
    ]
    for title, records in datasets:
        ws = wb.create_sheet(title)
        fields: list[str] = []
        for record in records:
            for key in record:
                if key not in fields:
                    fields.append(key)
        if not fields:
            fields = ["No data"]
        ws.append(fields)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="244062")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for record in records:
            values = []
            for key in fields:
                value = record.get(key, "")
                if key in {"debit_amount", "credit_amount"} and value not in ("", None):
                    try:
                        value = Decimal(str(value))  # Excel numeric cell; raw OCR text remains in *_raw.
                    except InvalidOperation:
                        pass
                values.append(value)
            ws.append(values)
        for row_idx in range(2, ws.max_row + 1):
            for key in ("debit_amount", "credit_amount"):
                if key in fields:
                    cell = ws.cell(row=row_idx, column=fields.index(key) + 1)
                    cell.number_format = '#,##0.00;[Red]-#,##0.00'
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        ws.row_dimensions[1].height = 30
        for idx, field in enumerate(fields, 1):
            values = [str(field)] + [str(r.get(field, "")) for r in records[:200]]
            width = min(48, max(12, max(len(v) for v in values) + 2))
            ws.column_dimensions[get_column_letter(idx)].width = width
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def process_pdf(args: argparse.Namespace) -> dict[str, Any]:
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 不存在：{pdf_path}")
    out_dir = Path(args.outdir).expanduser().resolve()
    images_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    engines: dict[str, Any] = {}
    document_rows: list[dict[str, Any]] = []
    tb_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    page_start, page_end = parse_page_range(args.pages, len(doc))

    for page_index in range(page_start, page_end):
        page = doc[page_index]
        page_text = page.get_text("text")
        large_images = []
        for item in page.get_images(full=True):
            try:
                pix = fitz.Pixmap(doc, int(item[0]))
                if pix.width >= 1200 and pix.height >= 900:
                    large_images.append((pix.width * pix.height, int(item[0])))
            except Exception:
                continue
        if not is_tb_page(page_text, bool(large_images)):
            continue

        meta = extract_metadata(page_text, page_index + 1)
        image_path = images_dir / f"page_{page_index + 1:04d}_table.png"
        table_image = extract_best_embedded_image(doc, page, page_index + 1, image_path)
        langs = detect_languages(page_text) if args.ocr_langs == "auto" else [x.strip() for x in args.ocr_langs.split(",") if x.strip()]
        docs_record = {
            **meta,
            "image_width": table_image.width,
            "image_height": table_image.height,
            "image_xref": table_image.xref or "",
            "embedded_image": table_image.extracted_from_embedded_image,
            "ocr_languages": ",".join(langs),
            "ocr_status": "extracted_only" if args.inspect_only else "pending",
            "metadata_status": "ocr_or_manual_review" if (not meta.get("bank_name") or not meta.get("statement_date")) else "metadata_extracted",
            "image_path": str(image_path),
        }
        document_rows.append(docs_record)

        if args.inspect_only:
            continue

        all_boxes: list[OCRBox] = []
        for lang in langs:
            boxes = run_ocr(image_path, lang, args.engine, args.device, args.ocr_version, engines)
            all_boxes.extend(boxes)
        all_boxes = dedupe_boxes(all_boxes)
        # Fill missing/embedded-only entity name and date from the OCR header lines.
        header_lines = ocr_boxes_to_lines(all_boxes, table_image.height * 0.24)
        ocr_header_text = "\n".join(header_lines)
        if meta.get("bank_name_source") == "image_header_check":
            heading_pos = next((i for i, x in enumerate(header_lines) if re.search(
                r"試算表|BALANCETE|TRIAL\s+BALANCE|BALANCE\s+OF\s+THE\s+LEDGER", x, re.I
            )), None)
            # Only take pre-title lines when the image itself actually contains a
            # statement title. Do not mistake the table's column header for a bank.
            name_lines = header_lines[:heading_pos] if heading_pos is not None else []
            if name_lines:
                meta["bank_name"] = " | ".join(name_lines[:2])
                meta["bank_name_source"] = "ocr_header"
            else:
                meta["bank_name_source"] = "missing_in_pdf_and_image_header"
        if not meta.get("statement_date"):
            meta["statement_date"] = parse_statement_date(ocr_header_text)
        meta["metadata_status"] = "metadata_extracted" if (meta.get("bank_name") and meta.get("statement_date")) else "needs_manual_review"
        document_rows[-1].update({k: meta.get(k, "") for k in (
            "bank_name", "statement_date", "bank_name_source", "metadata_status"
        )})
        b1, b2, boundary_source = detect_column_boundaries(image_path)
        for box in all_boxes:
            raw_rows.append({
                **meta,
                "language": box.language,
                "text": box.text,
                "confidence": round(box.score, 4),
                "x0": round(box.x0, 2), "y0": round(box.y0, 2),
                "x1": round(box.x1, 2), "y1": round(box.y1, 2),
                "image_path": str(image_path),
            })
        rows = group_boxes_into_rows(all_boxes, table_image.width, table_image.height, (b1, b2))
        rows = validate_rows(rows, args.confidence_threshold)
        for row in rows:
            row["column_boundary_source"] = boundary_source
            tb_rows.append(make_export_row(meta, row, image_path))
        document_rows[-1]["ocr_status"] = "completed"
        document_rows[-1]["ocr_box_count"] = len(all_boxes)
        document_rows[-1]["table_row_count"] = len(rows)
        document_rows[-1]["review_row_count"] = sum(r["review_status"] == "needs_review" for r in rows)

    # Write exports.
    write_csv(out_dir / "documents.csv", document_rows)
    write_csv(out_dir / "tb_rows.csv", tb_rows)
    write_csv(out_dir / "review_queue.csv", [r for r in tb_rows if r.get("review_status") == "needs_review"])
    write_csv(out_dir / "ocr_raw.csv", raw_rows)
    if not args.inspect_only:
        write_excel(out_dir / "macau_balancete.xlsx", tb_rows, document_rows, raw_rows)
    report = {
        "app_version": APP_VERSION,
        "source_pdf": str(pdf_path),
        "pdf_pages": len(doc),
        "tb_pages_detected": len(document_rows),
        "tb_rows_extracted": len(tb_rows),
        "rows_for_review": sum(r.get("review_status") == "needs_review" for r in tb_rows),
        "mode": "inspect_only" if args.inspect_only else "ocr",
        "ocr_engine": args.engine,
        "ocr_version": args.ocr_version,
        "languages": args.ocr_langs,
    }
    (out_dir / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    doc.close()
    return report


def parse_page_range(value: str, total_pages: int) -> tuple[int, int]:
    if value.strip().lower() == "all":
        return 0, total_pages
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", value)
    if not m:
        raise ValueError("--pages 格式應為 all、7 或 7-32")
    start = max(1, int(m.group(1)))
    end = int(m.group(2) or start)
    if end < start:
        raise ValueError("--pages 的結束頁不可小於開始頁")
    return start - 1, min(total_pages, end)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="澳門公報 Balancete OCR / Excel / CSV prototype")
    p.add_argument("pdf", help="輸入 PDF 路徑")
    p.add_argument("--outdir", default="output", help="輸出資料夾（預設：output）")
    p.add_argument("--pages", default="all", help="處理頁數，例如 7-32；預設 all")
    p.add_argument("--engine", default="paddle", choices=["paddle", "tesseract"], help="OCR 引擎；PaddleOCR 為建議主引擎，Tesseract 作本機 baseline")
    p.add_argument("--device", default="cpu", choices=["cpu", "gpu"], help="OCR 運算裝置（PaddleOCR 使用）")
    p.add_argument("--ocr-version", default="PP-OCRv5", help="PaddleOCR 模型版本")
    p.add_argument("--ocr-langs", default="auto", help="auto 或逗號分隔，例如 chinese_cht,pt,en")
    p.add_argument("--confidence-threshold", type=float, default=0.88, help="低於此 OCR 信心值會進入覆核清單")
    p.add_argument("--inspect-only", action="store_true", help="只抽取內嵌圖片及 metadata，不執行 OCR")
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = process_pdf(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
