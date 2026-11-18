#!/usr/bin/env python3
"""Macau Gazette Balancete OCR prototype v0.4.0.

Extract embedded statement images, run native Apple Vision on macOS or an optional
PaddleOCR/Tesseract backend, reconstruct Account/Debit/Credit rows, normalize
Chinese labels and page-specific numeric formats, then export auditable CSV/XLSX.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import fitz  # PyMuPDF

APP_VERSION = "0.4.0"
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
BALANCETE_HEADING_RE = re.compile(
    r"試算表|BALANCETE|TRIAL\s+BALANCE|BALANCE\s+OF\s+THE\s+LEDGER", re.I
)
# Gazette running header / fee notice lines are not part of the bank name.
HEADER_NOISE_RE = re.compile(
    r"BOLETIM\s+OFICIAL|澳門特別行政區公報|REGI[ÃA]O\s+ADMINISTRATIVA|"
    r"N\.?\s*[ºo°]\s*\d+|S[ÉE]RIE|CUSTO\s+DESTA\s+PUBLICA|是項刊登費用|"
    r"^\d{1,4}$|^\d{2}[-/]\d{2}[-/]\d{4}$",
    re.I,
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
    region: str = "table"

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
    for i, line in enumerate(lines):
        if BALANCETE_HEADING_RE.search(line):
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


def extract_bank_name_from_header_lines(
    lines: list[str], max_lines: int = 2
) -> tuple[str, list[str]]:
    """Extract a bank name from full-page header OCR lines.

    The full-page header includes the gazette running title (issue number,
    date, series, page number) and sometimes a parent-company line below the
    logo. Only the lines before the Balancete title are candidates; running
    header and fee notice lines are removed. The returned ``used_lines`` are
    kept as audit evidence.
    """
    heading_idx = len(lines)
    for index, line in enumerate(lines):
        if BALANCETE_HEADING_RE.search(norm_text(line)):
            heading_idx = index
            break
    candidates = lines[:heading_idx] if heading_idx < len(lines) else lines[:6]
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in candidates:
        text = norm_text(line)
        if not text or HEADER_NOISE_RE.search(text):
            continue
        if not re.search(r"[A-Za-z\u3400-\u9fff]", text):
            continue
        # A date such as "30 de Setembro de 2026" is not a bank name.
        if re.fullmatch(r"[\d\s.,/º°\-–—]+", text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= max_lines:
            break
    return " | ".join(cleaned), cleaned


def render_page_header_image(
    page: fitz.Page,
    out_path: Path,
    fraction: float = 0.25,
    zoom: float = 4.0,
) -> tuple[int, int]:
    """Render the top of the full PDF page for bank-name fallback OCR.

    The embedded table image may start below the bank logo (issue 45 page 17),
    so a table-image-only OCR can never recover the entity name. This renders
    the page header separately and keeps it as audit evidence.
    """
    rect = page.rect
    clip = fitz.Rect(
        rect.x0, rect.y0,
        rect.x1, rect.y0 + rect.height * max(0.05, min(0.5, fraction)),
    )
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    if pix.colorspace is None or pix.colorspace.n != 3 or pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    return pix.width, pix.height


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


def detect_column_layout(image_path: Path) -> tuple[float, float, float, str]:
    """Find the two amount-column rules and the label column's left edge.

    Returns ``(sep1, sep2, label_x0, source)``. Some Balancetes add a leading
    account-code column (issue 45 page 29 has code / label / debit / credit), so
    the two **rightmost** rules are the amount columns and any rule further left
    is treated as the code/label separator. Picking the strongest rules instead
    would put the label text into the debit ROI and both amounts into the credit
    ROI, which silently shifts every value.
    """
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
        candidates: list[tuple[float, float]] = []
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
        # Double rules (two edges of one separator) are merged before choosing.
        merged: list[tuple[float, float]] = []
        for strength, frac in sorted(candidates, key=lambda item: item[1]):
            if merged and frac - merged[-1][1] < 0.02:
                if strength > merged[-1][0]:
                    merged[-1] = (strength, frac)
            else:
                merged.append((strength, frac))
        if len(merged) >= 2:
            right = merged[-2:]
            sep1, sep2 = right[0][1], right[1][1]
            if sep1 + 0.08 < sep2:
                label_x0 = 0.005
                if len(merged) >= 3:
                    left_rules = [item for item in merged[:-2] if item[1] < sep1 - 0.05]
                    if left_rules:
                        code_strength, code_frac = max(left_rules, key=lambda item: item[1])
                        strongest = max(item[0] for item in merged)
                        if code_strength >= 0.4 * strongest:
                            label_x0 = min(0.99, max(0.005, code_frac + 0.003))
                return sep1, sep2, label_x0, "detected_morphology"

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
        merged = []
        for strength, frac in sorted(local_peaks, key=lambda item: item[1]):
            if merged and frac - merged[-1][1] < 0.02:
                if strength > merged[-1][0]:
                    merged[-1] = (strength, frac)
            else:
                merged.append((strength, frac))
        if len(merged) >= 2:
            sep1, sep2 = merged[-2][1], merged[-1][1]
            if sep1 + 0.08 < sep2:
                # The projection fallback cannot distinguish a real code column
                # from peaks caused by dense label text, so it never trims the
                # label ROI. Only a clear morphology rule may do that.
                return sep1, sep2, 0.005, "detected_projection"
    except Exception:
        pass
    return 0.48, 0.75, 0.005, "fallback_0.48_0.75"


def detect_column_boundaries(image_path: Path) -> tuple[float, float, str]:
    """Backward-compatible wrapper returning only the two amount separators."""
    sep1, sep2, _label_x0, source = detect_column_layout(image_path)
    return sep1, sep2, source


def detect_row_bands(image_path: Path, boundaries: tuple[float, float]
                     ) -> tuple[list[tuple[float, float]], str]:
    """Detect text rows from the horizontal ink projection of the amount columns.

    Gazette Balancetes use dotted row separators that morphology-open cannot
    keep, and tall Chinese glyphs can bridge neighbouring label lines. The amount
    columns contain short, isolated numeric lines, so their projection gives
    cleaner row boundaries; boxes from label-only rows (no amount) fall outside
    every band and are clustered separately. Bands are a structural hint only.
    """
    try:
        import cv2
        import numpy as np
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return [], "clustered"
        h, w = image.shape[:2]
        if h < 50 or w < 100:
            return [], "clustered"
        sep1, _sep2 = boundaries
        x0, x1 = int(max(0.0, sep1) * w), int(0.995 * w)
        if x1 - x0 < 100:
            return [], "clustered"
        binary = cv2.threshold(
            image[:, x0:x1], 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )[1]
        counts = (binary > 0).sum(axis=1)
        ink_threshold = max(5, int((x1 - x0) * 0.01))
        runs: list[list[int]] = []
        in_run = False
        start = 0
        for y, value in enumerate(counts > ink_threshold):
            if value and not in_run:
                start = y
                in_run = True
            elif not value and in_run:
                runs.append([start, y])
                in_run = False
        if in_run:
            runs.append([start, len(counts)])
        if not runs:
            return [], "clustered"
        # Merge sub-line gaps (broken strokes, ligature gaps) before filtering.
        merge_gap = max(3, int(h * 0.001))
        merged: list[list[int]] = []
        for run in runs:
            if merged and run[0] - merged[-1][1] <= merge_gap:
                merged[-1][1] = max(merged[-1][1], run[1])
            else:
                merged.append([run[0], run[1]])
        min_height = max(6, int(h * 0.003))
        bands = [
            (float(a), float(b))
            for a, b in merged
            if b - a >= min_height
        ]
        if len(bands) < 2:
            return [], "clustered"
        return bands, "projection"
    except Exception:
        return [], "clustered"


def _cluster_boxes(boxes: list[OCRBox], tolerance: float) -> list[dict[str, Any]]:
    """Cluster boxes into reading rows by vertical centre distance."""
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
    return sorted(rows, key=lambda r: r["cy"])


def _band_for_box(box: OCRBox, bands: list[tuple[float, float]]) -> int | None:
    """Return the band whose vertical span covers most of the box, if any."""
    best_index: int | None = None
    best_overlap = 0.0
    for index, (y0, y1) in enumerate(bands):
        overlap = min(box.y1, y1) - max(box.y0, y0)
        if overlap > best_overlap:
            best_overlap = overlap
            best_index = index
    if best_index is not None and best_overlap >= 0.5 * box.height:
        return best_index
    return None


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
    """Optional local Tesseract baseline for comparison and fallback."""
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


def _ensure_vision_worker() -> Path:
    """Compile the bundled Swift Vision worker once, on the user's Mac."""
    if sys.platform != "darwin":
        raise RuntimeError("Apple Vision OCR 只可以在 macOS 執行。")
    source = Path(__file__).with_name("vision_ocr_worker.swift")
    if not source.exists():
        raise FileNotFoundError(f"搵唔到 Swift Vision worker：{source}")
    compiler = shutil.which("swiftc")
    if not compiler:
        raise RuntimeError("搵唔到 swiftc。請先執行：xcode-select --install")
    cache_dir = Path(__file__).with_name(".vision_cache")
    cache_dir.mkdir(exist_ok=True)
    binary = cache_dir / "macau_tb_vision_worker"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        command = [compiler, "-O", str(source), "-o", str(binary)]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Swift Vision worker 編譯超時。請確認 Xcode Command Line Tools 已安裝。") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout)[-5000:]
            raise RuntimeError(f"Swift Vision worker 編譯失敗：\n{detail}")
    return binary


def vision_supported_languages() -> list[str]:
    binary = _ensure_vision_worker()
    completed = subprocess.run([str(binary), "--list-languages"], capture_output=True,
                               text=True, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "無法查詢 Apple Vision 語言")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _vision_language_profiles(languages: list[str]) -> list[tuple[str, str]]:
    profiles: list[tuple[str, str]] = []
    for lang in languages:
        if lang in {"chinese_cht", "zh-Hant", "zh"}:
            profiles.append(("zh-Hant", "zh-Hant"))
        elif lang in {"pt", "pt-PT", "Portuguese"}:
            profiles.append(("pt", "pt"))
        elif lang in {"en", "English", "en-US"}:
            profiles.append(("en", "en"))
    return list(dict.fromkeys(profiles)) or [("zh-Hant", "zh-Hant")]


def run_vision_ocr(image_path: Path, languages: list[str],
                   boundaries: tuple[float, float], level: str = "accurate",
                   minimum_text_height: float = 0.004, preprocess: bool = False,
                   language_correction: str = "labels",
                   label_x0: float = 0.005) -> tuple[list[OCRBox], dict[str, Any]]:
    """Run native Vision once per page, with separate label/amount column ROIs.

    ROI positions are normalised top-left fractions in the source image. Chinese
    uses zh-Hant, Portuguese uses a Portuguese model, and amount columns use
    English/digits. A page's script/language never dictates its number format.

    ``preprocess`` applies the worker's optional contrast/grayscale filter once
    per page. ``language_correction`` is ``off``, ``labels`` (text regions only)
    or ``all``; numeric columns default to no correction so Vision cannot rewrite
    an amount before it is preserved as raw evidence.
    """
    image_path = image_path.resolve()
    sep1, sep2 = boundaries
    # Table body excludes the column-heading band. The bottom edge is kept close
    # to the image border because tall Balancetes print the final total row at
    # the very bottom (issue 45 page 29); 0.985 clipped its glyphs into garbage.
    body_y0, body_y1 = 0.035, 0.997
    profiles = _vision_language_profiles(languages)
    tasks: list[dict[str, Any]] = []

    for profile_name, lang_code in profiles:
        tasks.append({"name": f"header_{profile_name}", "language": lang_code,
                      "level": level, "x0": 0.005, "y0": 0.0, "x1": 0.995, "y1": 0.25})
        tasks.append({"name": f"label_{profile_name}", "language": lang_code,
                      "level": level, "x0": max(0.005, label_x0), "y0": body_y0,
                      "x1": max(0.01, sep1 - 0.002), "y1": body_y1})

    # Numeric columns use a dedicated Latin/digit recognizer, not a Chinese or
    # Portuguese language assumption.
    tasks.extend([
        {"name": "debit_numeric", "language": "en", "level": level,
         "x0": min(0.99, sep1 + 0.002), "y0": body_y0,
         "x1": max(sep1 + 0.01, sep2 - 0.002), "y1": body_y1},
        {"name": "credit_numeric", "language": "en", "level": level,
         "x0": min(0.99, sep2 + 0.002), "y0": body_y0,
         "x1": 0.995, "y1": body_y1},
    ])

    # Language correction is a per-region policy: text labels may benefit from a
    # lexicon, but an amount must never be "corrected" before it is stored raw.
    for task in tasks:
        is_numeric = task["name"].endswith("_numeric")
        task["usesLanguageCorrection"] = (
            language_correction == "all"
            or (language_correction == "labels" and not is_numeric)
        )

    return _invoke_vision_worker(
        image_path, tasks,
        minimum_text_height=minimum_text_height,
        preprocess=preprocess,
        level=level,
        language_correction=language_correction,
    )


def _invoke_vision_worker(
    image_path: Path,
    tasks: list[dict[str, Any]],
    *,
    minimum_text_height: float,
    preprocess: bool,
    level: str,
    language_correction: str,
) -> tuple[list[OCRBox], dict[str, Any]]:
    """Run one Vision worker process for an explicit task list."""
    binary = _ensure_vision_worker()
    image_path = image_path.resolve()
    payload = {"imagePath": str(image_path), "tasks": tasks,
               "minimumTextHeight": minimum_text_height,
               "preprocessImage": preprocess}
    try:
        completed = subprocess.run([str(binary)], input=json.dumps(payload),
                                   capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Apple Vision OCR 超時：{image_path.name}") from exc
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Apple Vision OCR 執行失敗")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Apple Vision 回傳無效 JSON：{completed.stdout[:500]}") from exc

    boxes: list[OCRBox] = []
    for item in result.get("observations", []):
        try:
            task_region = str(item.get("region", "table"))
            region = "header" if task_region.startswith("header_") else "table"
            boxes.append(OCRBox(
                text=str(item["text"]), score=float(item.get("confidence", 0.0)),
                x0=float(item["x0"]), y0=float(item["y0"]),
                x1=float(item["x1"]), y1=float(item["y1"]),
                language=str(item.get("selectedLanguage", "")), region=region,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    diagnostics = {
        "vision_elapsed_seconds": result.get("elapsedSeconds", ""),
        "vision_supported_languages": ",".join(result.get("supportedLanguages", [])),
        "vision_observation_count": len(boxes),
        "vision_level": level,
        # Traditional Chinese has no Vision `fast` recognizer, so the worker
        # routes zh-Hant tasks to `accurate`. Record the levels actually used
        # next to the operator-requested level for audit.
        "vision_levels_used": ",".join(result.get("levelsUsed", [])),
        "vision_preprocessed": result.get("preprocessing", "none"),
        "vision_preprocess_requested": "on" if preprocess else "off",
        "vision_language_correction": language_correction,
        "vision_minimum_text_height": minimum_text_height,
    }
    return boxes, diagnostics


def run_vision_header_ocr(
    image_path: Path,
    languages: list[str],
    level: str = "accurate",
    minimum_text_height: float = 0.004,
    preprocess: bool = False,
    language_correction: str = "labels",
) -> tuple[list[OCRBox], dict[str, Any]]:
    """OCR a rendered full-page header image with one full-region task per language."""
    profiles = _vision_language_profiles(languages)
    tasks: list[dict[str, Any]] = []
    for profile_name, lang_code in profiles:
        tasks.append({
            "name": f"header_full_{profile_name}",
            "language": lang_code,
            "level": level,
            "x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0,
            "usesLanguageCorrection": language_correction in {"labels", "all"},
        })
    return _invoke_vision_worker(
        image_path, tasks,
        minimum_text_height=minimum_text_height,
        preprocess=preprocess,
        level=level,
        language_correction=language_correction,
    )


def _box_iou(a: OCRBox, b: OCRBox) -> float:
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area_a = max(1.0, (a.x1 - a.x0) * (a.y1 - a.y0))
    area_b = max(1.0, (b.x1 - b.x0) * (b.y1 - b.y0))
    return inter / max(1.0, area_a + area_b - inter)


def _preferred_cross_language(a: OCRBox, b: OCRBox) -> OCRBox:
    """Choose which overlapping bilingual reading to keep.

    Chinese and Portuguese names sit on separate lines in bilingual Gazettes, so
    two strongly overlapping boxes from different language passes are duplicate
    readings of the same line, not two real labels. Prefer the reading whose
    script matches the line: Chinese text from the Chinese pass, Latin text from
    the Portuguese pass, then the higher confidence.
    """
    a_chinese, b_chinese = has_chinese(a.text), has_chinese(b.text)
    if a_chinese != b_chinese:
        return a if a_chinese else b
    a_portuguese = a.language.lower().startswith("pt")
    b_portuguese = b.language.lower().startswith("pt")
    if a_portuguese != b_portuguese:
        return a if a_portuguese else b
    return a if a.score >= b.score else b


def dedupe_boxes(boxes: list[OCRBox]) -> list[OCRBox]:
    """Deduplicate boxes when bilingual pages are OCR'd with multiple models."""
    kept: list[OCRBox] = []
    for box in sorted(boxes, key=lambda b: b.score, reverse=True):
        duplicate = False
        for old in kept:
            if box.region != old.region:
                continue
            # Cross-language overlaps are resolved after this loop with script
            # awareness; exact duplicates are dropped immediately.
            if box.language != old.language and norm_text(box.text) != norm_text(old.text):
                continue
            if _box_iou(box, old) >= 0.55:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    # Two language passes can read the same line differently; keep one reading.
    resolved = list(kept)
    changed = True
    while changed:
        changed = False
        for i in range(len(resolved)):
            for j in range(i + 1, len(resolved)):
                a, b = resolved[i], resolved[j]
                if a.region != b.region or a.language == b.language:
                    continue
                if _box_iou(a, b) < 0.55:
                    continue
                loser = b if _preferred_cross_language(a, b) is a else a
                resolved.remove(loser)
                changed = True
                break
            if changed:
                break
    return sorted(resolved, key=lambda b: (b.cy, b.x0))


def group_boxes_into_rows(boxes: list[OCRBox], image_width: int, image_height: int,
                          boundaries: tuple[float, float],
                          row_bands: list[tuple[float, float]] | None = None
                          ) -> list[dict[str, Any]]:
    if not boxes:
        return []
    heights = [b.height for b in boxes if b.height > 2]
    median_h = statistics.median(heights) if heights else max(10, image_height * 0.01)
    tolerance = max(8.0, median_h * 0.58)
    source = "projection" if row_bands else "clustered"
    rows: list[dict[str, Any]] = []
    if row_bands:
        buckets: dict[int, list[OCRBox]] = {}
        orphans: list[OCRBox] = []
        for box in boxes:
            index = _band_for_box(box, row_bands)
            if index is None:
                orphans.append(box)
            else:
                buckets.setdefault(index, []).append(box)
        for index in sorted(buckets):
            y0, y1 = row_bands[index]
            rows.append({
                "cy": statistics.mean(x.cy for x in buckets[index]),
                "boxes": buckets[index],
                "band": (y0, y1),
            })
        if orphans:
            orphan_rows = _cluster_boxes(orphans, tolerance)
            for orphan_row in orphan_rows:
                orphan_row["orphan"] = True
            rows.extend(orphan_rows)
        rows.sort(key=lambda r: r["cy"])
    else:
        rows = _cluster_boxes(boxes, tolerance)
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
        col_boxes = {}
        for key, arr in cols.items():
            if arr:
                col_boxes[key] = (
                    min(x.x0 for x in arr), min(x.y0 for x in arr),
                    max(x.x1 for x in arr), max(x.y1 for x in arr),
                )
            else:
                col_boxes[key] = None
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
            "label_bbox_x0": round(col_boxes["label"][0], 2) if col_boxes["label"] else "",
            "label_bbox_y0": round(col_boxes["label"][1], 2) if col_boxes["label"] else "",
            "label_bbox_x1": round(col_boxes["label"][2], 2) if col_boxes["label"] else "",
            "label_bbox_y1": round(col_boxes["label"][3], 2) if col_boxes["label"] else "",
            "debit_bbox_x0": round(col_boxes["debit"][0], 2) if col_boxes["debit"] else "",
            "debit_bbox_y0": round(col_boxes["debit"][1], 2) if col_boxes["debit"] else "",
            "debit_bbox_x1": round(col_boxes["debit"][2], 2) if col_boxes["debit"] else "",
            "debit_bbox_y1": round(col_boxes["debit"][3], 2) if col_boxes["debit"] else "",
            "credit_bbox_x0": round(col_boxes["credit"][0], 2) if col_boxes["credit"] else "",
            "credit_bbox_y0": round(col_boxes["credit"][1], 2) if col_boxes["credit"] else "",
            "credit_bbox_x1": round(col_boxes["credit"][2], 2) if col_boxes["credit"] else "",
            "credit_bbox_y1": round(col_boxes["credit"][3], 2) if col_boxes["credit"] else "",
            "row_structure_source": "clustered" if row.get("orphan") else source,
            "row_band_y0": round(row["band"][0], 2) if "band" in row else "",
            "row_band_y1": round(row["band"][1], 2) if "band" in row else "",
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


@lru_cache(maxsize=1)
def _tesseract_version() -> str:
    """Return the installed Tesseract version line, or "" when unavailable."""
    try:
        completed = subprocess.run(
            ["tesseract", "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (completed.stdout or completed.stderr).strip()
    return text.splitlines()[0].strip() if text else ""


def _numeric_column_crop(image: Any, width: int, height: int,
                         boundaries: tuple[float, float], column: str
                         ) -> tuple[Any, int]:
    sep1, sep2 = boundaries
    if column == "debit":
        x0 = int(min(0.99, sep1 + 0.002) * width)
        x1 = int(max(sep1 + 0.01, sep2 - 0.002) * width)
    else:
        x0 = int(min(0.99, sep2 + 0.002) * width)
        x1 = int(0.995 * width)
    y0 = int(0.035 * height)
    y1 = int(0.997 * height)
    return image[y0:y1, max(0, x0):min(width, x1)], y0


def _parse_tesseract_tsv(payload: str, y_offset: int) -> list[dict[str, Any]]:
    """Group Tesseract word rows into reading lines in full-image coordinates."""
    reader = csv.DictReader(io.StringIO(payload), delimiter="\t")
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in reader:
        try:
            level = int(record.get("level") or -1)
        except (TypeError, ValueError):
            continue
        if level != 5:
            continue
        word = (record.get("text") or "").strip()
        if not word:
            continue
        key = (
            record.get("block_num") or "",
            record.get("par_num") or "",
            record.get("line_num") or "",
        )
        try:
            top = float(record.get("top") or 0)
            line_height = float(record.get("height") or 0)
        except (TypeError, ValueError):
            continue
        group = groups.setdefault(key, {"parts": [], "top": float("inf"), "bottom": 0.0})
        group["parts"].append(word)
        group["top"] = min(group["top"], top)
        group["bottom"] = max(group["bottom"], top + line_height)
    lines: list[dict[str, Any]] = []
    for group in groups.values():
        lines.append({
            "y_center": y_offset + (group["top"] + group["bottom"]) / 2.0,
            "text": " ".join(group["parts"]),
        })
    return lines


def tesseract_numeric_lines(image_path: Path, width: int, height: int,
                            boundaries: tuple[float, float], column: str
                            ) -> list[dict[str, Any]]:
    """Independent digit-only reading of one amount column (verification only)."""
    import cv2
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"cv2 could not load {image_path.name}")
    crop, y_offset = _numeric_column_crop(image, width, height, boundaries, column)
    if crop.size == 0:
        return []
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        cv2.imwrite(str(temp_path), crop)
        completed = subprocess.run(
            [
                "tesseract", str(temp_path), "stdout",
                "--psm", "6",
                "-c", "tessedit_char_whitelist=0123456789.,-",
                "tsv",
            ],
            capture_output=True, text=True, timeout=180,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout).strip()[:300] or "tesseract failed"
            )
        return _parse_tesseract_tsv(completed.stdout, y_offset)
    finally:
        temp_path.unlink(missing_ok=True)


def _match_lines_to_rows(rows: list[dict[str, Any]], lines: list[dict[str, Any]],
                         tolerance: float) -> dict[int, dict[str, Any]]:
    """One-to-one nearest match so one verifier line cannot validate two rows."""
    candidates: list[tuple[float, int, int]] = []
    for i, row in enumerate(rows):
        for j, line in enumerate(lines):
            distance = abs(row["y_center"] - line["y_center"])
            if distance <= tolerance:
                candidates.append((distance, i, j))
    candidates.sort()
    matched_rows: set[int] = set()
    matched_lines: set[int] = set()
    match: dict[int, dict[str, Any]] = {}
    for _, i, j in candidates:
        if i in matched_rows or j in matched_lines:
            continue
        matched_rows.add(i)
        matched_lines.add(j)
        match[i] = lines[j]
    return match


def attach_amount_verifier(rows: list[dict[str, Any]], image_path: Path,
                           width: int, height: int,
                           boundaries: tuple[float, float], engine: str
                           ) -> dict[str, Any]:
    """Attach an independent Tesseract reading to each row as *evidence only*.

    The verifier never replaces `debit_raw`/`credit_raw` and never changes a
    parsed amount: agreement is recorded, disagreement is routed to review.
    """
    diagnostics: dict[str, Any] = {
        "amount_verifier_engine": "",
        "amount_verifier_status": "off",
        "amount_verifier_lines": 0,
    }
    if engine == "off" or not rows:
        return diagnostics
    version = _tesseract_version()
    if not version:
        diagnostics["amount_verifier_status"] = "unavailable"
        return diagnostics
    diagnostics["amount_verifier_engine"] = version
    try:
        debit_lines = tesseract_numeric_lines(image_path, width, height, boundaries, "debit")
        credit_lines = tesseract_numeric_lines(image_path, width, height, boundaries, "credit")
    except Exception as exc:  # noqa: BLE001 - verifier must never break a run
        diagnostics["amount_verifier_status"] = f"error: {exc}"
        return diagnostics
    diagnostics["amount_verifier_status"] = "ran"
    diagnostics["amount_verifier_lines"] = len(debit_lines) + len(credit_lines)
    tolerance = max(12.0, height * 0.012)
    for column, lines in (("debit", debit_lines), ("credit", credit_lines)):
        matches = _match_lines_to_rows(rows, lines, tolerance)
        for index, line in matches.items():
            rows[index][f"{column}_verifier_raw"] = line["text"]
            rows[index][f"{column}_verifier_y_delta"] = round(
                abs(rows[index]["y_center"] - line["y_center"]), 2
            )
    return diagnostics


_AMOUNT_SIGNATURE_STRIP = re.compile(r"[.,\-\u2212\u2013\u2014\s\u00a0\u202f]")


def amount_digit_signature(text: str) -> str:
    """Separator-free signature used to compare two engines' digit readings."""
    return _AMOUNT_SIGNATURE_STRIP.sub("", norm_text(text or ""))


def amount_verifier_status(vision_raw: str, verifier_raw: str) -> str:
    vision = amount_digit_signature(vision_raw)
    verifier = amount_digit_signature(verifier_raw)
    if not vision and not verifier:
        return "blank"
    if vision and verifier:
        return "agree" if vision == verifier else "digit_mismatch"
    return "verifier_only" if verifier else "vision_only"


def _cell_ink_fraction(image: Any, x0: int, x1: int, y0: int, y1: int) -> float:
    """Fraction of dark pixels in one amount cell (used to skip empty cells)."""
    import cv2
    crop = image[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if crop.size == 0:
        return 0.0
    binary = cv2.threshold(
        crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    return float((binary > 0).sum()) / float(crop.size)


def second_pass_amount_cells(rows: list[dict[str, Any]], image_path: Path,
                             width: int, height: int,
                             boundaries: tuple[float, float],
                             page_format: str,
                             confidence_threshold: float = 0.88,
                             max_cells: int = 80) -> dict[str, Any]:
    """Re-read suspicious amount cells from the source image at 3x scale.

    The primary OCR remains in ``*_raw``. A second, targeted reading is stored in
    ``*_second_pass_raw`` and only adopted (always with review) when the primary
    cell was empty/unparsed, or when a low-confidence primary disagrees with a
    clean high-resolution re-read.
    """
    diagnostics: dict[str, Any] = {
        "amount_second_pass_status": "off",
        "amount_second_pass_cells": 0,
        "amount_second_pass_filled": 0,
        "amount_second_pass_seconds": 0.0,
    }
    if not rows:
        return diagnostics
    started = time.perf_counter()
    try:
        import cv2
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    except Exception:  # noqa: BLE001
        image = None
    if image is None:
        diagnostics["amount_second_pass_status"] = "unavailable"
        return diagnostics
    worker = _ensure_vision_worker()
    sep1, sep2 = boundaries
    columns = {
        "debit": (min(0.99, sep1 + 0.002), max(sep1 + 0.01, sep2 - 0.002)),
        "credit": (min(0.99, sep2 + 0.002), 0.995),
    }
    cells = 0
    filled = 0
    for row in rows:
        if cells >= max_cells:
            break
        try:
            top = int(float(row.get("bbox_y0") or 0))
            bottom = int(float(row.get("bbox_y1") or 0))
        except (TypeError, ValueError):
            continue
        if bottom - top < 8:
            continue
        for column, (fx0, fx1) in columns.items():
            if cells >= max_cells:
                break
            primary = (row.get(f"{column}_raw") or "").strip()
            value, issue = parse_amount(primary, page_format) if primary else ("", "")
            try:
                column_confidence = float(row.get(f"{column}_confidence") or 1.0)
            except (TypeError, ValueError):
                column_confidence = 1.0
            needs_second_pass = (
                not primary
                or not value
                or issue == "ambiguous_ocr_character"
                or (value and column_confidence < confidence_threshold)
            )
            if not needs_second_pass:
                continue
            x0 = max(0, int(fx0 * width))
            x1 = min(width, int(fx1 * width))
            cell_top, cell_bottom = max(0, top), min(height, bottom)
            if x1 - x0 < 20 or cell_bottom - cell_top < 8:
                continue
            try:
                ink = _cell_ink_fraction(image, x0, x1, cell_top, cell_bottom)
            except Exception:  # noqa: BLE001
                ink = 0.0
            if ink < 0.02:
                continue
            crop = image[cell_top:cell_bottom, x0:x1]
            if crop.size == 0:
                continue
            crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            crop = cv2.copyMakeBorder(crop, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
                temp_path = Path(handle.name)
            try:
                cv2.imwrite(str(temp_path), crop)
                payload = {
                    "imagePath": str(temp_path),
                    "tasks": [{
                        "name": f"{column}_second_pass",
                        "language": "en",
                        "level": "accurate",
                        "x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0,
                        "usesLanguageCorrection": False,
                    }],
                    "minimumTextHeight": 0.004,
                    "preprocessImage": False,
                }
                completed = subprocess.run(
                    [str(worker)], input=json.dumps(payload),
                    capture_output=True, text=True, timeout=120,
                )
                if completed.returncode != 0:
                    continue
                observations = json.loads(completed.stdout).get("observations", [])
                text = " ".join(
                    str(item.get("text", "")).strip()
                    for item in observations
                    if str(item.get("text", "")).strip()
                )
            except Exception:  # noqa: BLE001 - a second pass must never break a run
                text = ""
            finally:
                temp_path.unlink(missing_ok=True)
            row[f"{column}_second_pass_raw"] = text
            cells += 1
            if text:
                filled += 1
    diagnostics["amount_second_pass_status"] = "ran"
    diagnostics["amount_second_pass_cells"] = cells
    diagnostics["amount_second_pass_filled"] = filled
    diagnostics["amount_second_pass_seconds"] = round(time.perf_counter() - started, 2)
    return diagnostics


def _clean_amount_token(raw: str) -> tuple[str, bool, str]:
    """Strip display-only material and map common OCR-confusable glyphs.

    Returns (token, negative, issue). The original input is never modified in
    the exported *_raw field. A space between digits is kept as a possible
    separator because Gazette scans sometimes print a space where a decimal or
    thousands mark belongs; it is only used with page-level format evidence and
    any resulting repair is flagged by the caller.
    """
    s = norm_text(raw).replace("\u00a0", " ").replace("\u202f", " ")
    # Spaces next to existing punctuation are pure whitespace noise
    # ("32, 713, 062.68"); keep only gaps that could themselves be separators.
    s = re.sub(r"\s+(?=[.,])", "", s)
    s = re.sub(r"(?<=[.,])\s+", "", s)
    s = re.sub(r"(?<=\d)\s+(?=\d)", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    if not s:
        return "", False, "blank"
    if s in {"-", "－"}:
        return "", False, "dash"
    if s in {".", ","}:
        return "", False, "invalid_characters"

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.startswith("-"):
        negative = not negative
        s = s[1:]

    s = re.sub(r"(?i)(MOP|HKD|USD|RMB|AOA|澳门元|澳門元|\$|€|£)", "", s)
    s = s.strip()
    if not s:
        return "", negative, "blank"

    issue = ""
    if re.search(r"[OoIl|]", s):
        s = s.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}))
        issue = "ambiguous_ocr_character"
    # A single trailing letter is a common scan/OCR artifact on amounts
    # (30,214,693,162.03g). Removing it invents no digit and is flagged.
    if re.fullmatch(r"[0-9.,' ]+[A-Za-z]", s):
        s = s[:-1]
        issue = issue or "recovered_ocr_character"
    if not re.fullmatch(r"[0-9.,' ]+", s):
        return "", negative, issue or "invalid_characters"
    s = s.replace("'", "")
    return s.strip(), negative, issue


def _grouping_is_valid(groups: list[str]) -> bool:
    if not groups or not all(x.isdigit() for x in groups):
        return False
    if not (1 <= len(groups[0]) <= 3):
        return False
    return all(len(x) == 3 for x in groups[1:])


def _parse_variant(token: str, style: str, forced_repair: bool = False
                   ) -> tuple[str, str, bool]:
    """Parse one token under one style; spaces may act as separators."""
    decimal_sep = "." if style == "comma_dot" else ","
    grouping_sep = "," if style == "comma_dot" else "."
    separators = [c for c in token if c in ",. "]
    if not separators:
        if token.isdigit():
            return token, "", forced_repair
        return "", "unparsed_amount", False

    last_sep_pos = max(token.rfind("."), token.rfind(","), token.rfind(" "))
    last_sep = token[last_sep_pos]
    tail = token[last_sep_pos + 1:]
    prefix = token[:last_sep_pos]

    # Financial balances normally use two decimal places. The last separator is
    # accepted as the decimal mark when the remaining digit groups are still
    # structurally valid, even if OCR substituted the glyph (comma vs dot) or a
    # space was printed instead. Any such substitution is flagged as repaired.
    if len(tail) in (1, 2, 4) and tail.isdigit():
        int_groups = re.split(r"[., ]", prefix) if prefix else ["0"]
        if not _grouping_is_valid(int_groups):
            return "", "invalid_grouping", False
        integer = "".join(int_groups)
        if not integer:
            integer = "0"
        repaired = (forced_repair or last_sep != decimal_sep or any(
            sep != grouping_sep for sep in re.findall(r"[., ]", prefix)
        ))
        value = integer + "." + tail
        return value, ("recovered_ocr_separator" if repaired else ""), repaired

    # Grouping-only values: every group after the first must have three digits.
    groups = re.split(r"[., ]", token)
    if _grouping_is_valid(groups):
        original_separators = set(separators)
        expected_only = bool(original_separators) and original_separators <= {grouping_sep}
        if expected_only:
            return "".join(groups), "", forced_repair
        # Recover only a consistent all-one-separator grouping substitution.
        # Mixed punctuation with a 3-digit tail may instead be a nonstandard
        # decimal precision, so do not silently collapse it into an integer.
        if len(original_separators) == 1:
            return "".join(groups), "recovered_ocr_separator", True
        return "", "ambiguous_format", False

    return "", "ambiguous_format", False


def _parse_amount_for_style(token: str, style: str) -> tuple[str, str, bool]:
    """Parse a token, resolving spaces as decimal marks or as OCR noise.

    A space can be a real decimal mark (`5,083,140,620 78`) or an inserted gap
    inside a digit group (`6,04 1,499.40`). Both readings are tried; if they
    disagree the token stays ambiguous instead of being guessed.
    """
    if " " not in token:
        return _parse_variant(token, style)
    with_spaces = _parse_variant(token, style)
    without_spaces = _parse_variant(token.replace(" ", ""), style, forced_repair=True)
    # Compare numerically: "0.00" and "000" are the same amount even though the
    # raw candidate strings differ.
    candidates: dict[Decimal | str, tuple[str, str, bool]] = {}
    for candidate in (with_spaces, without_spaces):
        value = candidate[0]
        if not value:
            continue
        try:
            key: Decimal | str = Decimal(value)
        except InvalidOperation:
            key = value
        candidates.setdefault(key, candidate)
    if len(candidates) > 1:
        return "", "ambiguous_format", False
    if candidates:
        value, issue, repaired = next(iter(candidates.values()))
        repaired = repaired or with_spaces[2] or without_spaces[2]
        if repaired and not issue:
            issue = "recovered_ocr_separator"
        return value, issue, repaired
    return "", with_spaces[1] or without_spaces[1] or "ambiguous_format", False


def infer_number_format(values: Iterable[str]) -> dict[str, Any]:
    """Infer punctuation convention from evidence on one statement page.

    Do not infer from language. Prefer numbers with a two-digit decimal tail
    and valid 3-digit integer groups; single simple values contribute less.
    """
    scores = {"comma_dot": 0.0, "dot_comma": 0.0}
    evidence: list[dict[str, str]] = []
    for raw in values:
        token, _, clean_issue = _clean_amount_token(str(raw or ""))
        # Spaces are never punctuation evidence: remove them here only, so a
        # stray gap inside a number cannot change the page-level convention.
        token = token.replace(" ", "")
        if not token or not any(ch in token for ch in ",."):
            continue
        last_pos = max(token.rfind("."), token.rfind(","))
        last_sep = token[last_pos]
        tail = token[last_pos + 1:]
        prefix = token[:last_pos]
        groups = re.split(r"[.,]", prefix) if prefix else ["0"]
        all_groups = re.split(r"[.,]", token)
        has_valid_groups = _grouping_is_valid(groups)
        all_grouping_valid = _grouping_is_valid(all_groups)
        if (len(tail) == 3 and tail.isdigit() and all_grouping_valid
                and len(all_groups) > 1 and set(re.findall(r"[.,]", token)) == {last_sep}):
            grouping_style = "comma_dot" if last_sep == "," else "dot_comma"
            scores[grouping_style] += 1.0
            evidence.append({"raw": str(raw), "vote": grouping_style, "weight": "1.0"})
        elif last_sep == "." and len(tail) in (1, 2, 4) and tail.isdigit() and has_valid_groups:
            weight = 3.0 if len(tail) == 2 and len(groups) > 1 else 1.0
            scores["comma_dot"] += weight
            evidence.append({"raw": str(raw), "vote": "comma_dot", "weight": str(weight)})
        elif last_sep == "," and len(tail) in (1, 2, 4) and tail.isdigit() and has_valid_groups:
            weight = 3.0 if len(tail) == 2 and len(groups) > 1 else 1.0
            scores["dot_comma"] += weight
            evidence.append({"raw": str(raw), "vote": "dot_comma", "weight": str(weight)})
        else:
            # A single clear decimal separator with two trailing digits is useful
            # evidence, while a bare grouping token remains weak evidence.
            parts = re.split(r"[.,]", token)
            if len(parts) == 2 and len(parts[-1]) == 2 and parts[-1].isdigit():
                if last_sep == ".":
                    scores["comma_dot"] += 1.5
                    evidence.append({"raw": str(raw), "vote": "comma_dot", "weight": "1.5"})
                else:
                    scores["dot_comma"] += 1.5
                    evidence.append({"raw": str(raw), "vote": "dot_comma", "weight": "1.5"})

    total = scores["comma_dot"] + scores["dot_comma"]
    if total <= 0 or abs(scores["comma_dot"] - scores["dot_comma"]) < 1.0:
        chosen = "ambiguous"
        confidence = 0.0 if total <= 0 else round(max(scores.values()) / total, 3)
    else:
        chosen = max(scores, key=scores.get)
        confidence = round(scores[chosen] / total, 3)
    return {"number_format": chosen, "format_confidence": confidence,
            "format_scores": scores, "format_evidence": evidence}


def parse_amount(raw: str, number_format: str = "auto",
                 repair_ocr_separators: bool = True) -> tuple[str, str]:
    """Return (canonical decimal string, parse status).

    Canonical output always uses digits and a dot decimal separator. OCR text
    is preserved separately by callers. Ambiguous formats are never guessed.
    """
    token, negative, initial_issue = _clean_amount_token(raw)
    if not token:
        return "", initial_issue

    styles = [number_format] if number_format in {"comma_dot", "dot_comma"} else []
    if not styles:
        inferred = infer_number_format([raw])["number_format"]
        if inferred in {"comma_dot", "dot_comma"}:
            styles = [inferred]
        else:
            # Token-level inference is allowed only when one convention is
            # structurally unambiguous; never prefer a language-based default.
            if token.count(".") and token.count(","):
                styles = ["comma_dot" if token.rfind(".") > token.rfind(",") else "dot_comma"]
            elif token.count(".") == 1 and token.rsplit(".", 1)[-1].isdigit() and len(token.rsplit(".", 1)[-1]) == 2:
                styles = ["comma_dot"]
            elif token.count(",") == 1 and token.rsplit(",", 1)[-1].isdigit() and len(token.rsplit(",", 1)[-1]) == 2:
                styles = ["dot_comma"]
            else:
                return "", initial_issue or "ambiguous_format"

    value, style_issue, repaired = _parse_amount_for_style(token, styles[0])
    if not value:
        return "", initial_issue or style_issue or "unparsed_amount"
    if repaired and not repair_ocr_separators:
        return "", initial_issue or "separator_repair_disabled"
    try:
        decimal_value = Decimal(value)
        if negative:
            decimal_value = -decimal_value
        canonical = format(decimal_value, "f")
    except InvalidOperation:
        return "", initial_issue or "unparsed_amount"

    issues = [x for x in (initial_issue, style_issue) if x]
    return canonical, ";".join(dict.fromkeys(issues))


@lru_cache(maxsize=3)
def _get_opencc_converter(conversion: str):
    from opencc import OpenCC
    return OpenCC(conversion)


def normalize_chinese_label(raw: str, conversion: str = "s2hk") -> tuple[str, str]:
    """Keep OCR raw text intact and return a separate Traditional label field."""
    value = norm_text(raw)
    if not value or conversion == "off" or not has_chinese(value):
        return value, "unchanged"
    try:
        converter = _get_opencc_converter(conversion)
        converted = converter.convert(value)
        return converted, "converted" if converted != value else "unchanged"
    except ImportError:
        return value, "opencc_missing"
    except Exception as exc:
        return value, f"opencc_error:{type(exc).__name__}"

def validate_rows(rows: list[dict[str, Any]], confidence_threshold: float,
                  number_format_override: str = "auto",
                  chinese_label_normalization: str = "s2hk",
                  repair_ocr_separators: bool = True,
                  amount_verifier: bool = False,
                  amount_second_pass: bool = False) -> list[dict[str, Any]]:
    profile = infer_number_format(
        [value for row in rows for value in (row.get("debit_raw", ""), row.get("credit_raw", ""))]
    )
    page_format = number_format_override if number_format_override in {"comma_dot", "dot_comma"} else profile["number_format"]

    for row in rows:
        label_normalized, label_status = normalize_chinese_label(
            row.get("label_raw", ""), chinese_label_normalization
        )
        row["label_normalized"] = label_normalized
        row["label_normalization_status"] = label_status
        row["number_format"] = page_format
        row["number_format_confidence"] = profile["format_confidence"]
        row["number_format_evidence"] = json.dumps(profile["format_evidence"], ensure_ascii=False)

        debit, debit_issue = parse_amount(row.get("debit_raw", ""), page_format, repair_ocr_separators)
        credit, credit_issue = parse_amount(row.get("credit_raw", ""), page_format, repair_ocr_separators)
        row["debit_amount"] = debit
        row["credit_amount"] = credit
        row["debit_parse_status"] = debit_issue or ("parsed" if debit else "blank")
        row["credit_parse_status"] = credit_issue or ("parsed" if credit else "blank")

        if amount_second_pass:
            for column in ("debit", "credit"):
                if f"{column}_second_pass_raw" not in row:
                    continue
                secondary_raw = (row.get(f"{column}_second_pass_raw") or "").strip()
                secondary_value, _secondary_issue = (
                    parse_amount(secondary_raw, page_format, repair_ocr_separators)
                    if secondary_raw else ("", "")
                )
                primary_value = row.get(f"{column}_amount") or ""
                primary_issue = row.get(f"{column}_parse_status") or ""
                status = "empty"
                if secondary_value:
                    if not primary_value:
                        row[f"{column}_amount"] = secondary_value
                        row[f"{column}_parse_status"] = "recovered_second_pass"
                        status = "used"
                    else:
                        same = False
                        try:
                            same = Decimal(primary_value) == Decimal(secondary_value)
                        except InvalidOperation:
                            same = primary_value == secondary_value
                        if same:
                            status = "agree"
                        else:
                            try:
                                column_confidence = float(
                                    row.get(f"{column}_confidence") or 1.0
                                )
                            except (TypeError, ValueError):
                                column_confidence = 1.0
                            if (primary_issue == "parsed"
                                    and column_confidence < confidence_threshold):
                                row[f"{column}_amount"] = secondary_value
                                row[f"{column}_parse_status"] = "recovered_second_pass"
                                status = "replaced_low_confidence"
                            else:
                                status = "differs"
                elif primary_value:
                    status = "secondary_unparsed"
                row[f"{column}_second_pass_status"] = status

        issues: list[str] = []
        for col, issue in (("debit", debit_issue), ("credit", credit_issue)):
            if issue and issue not in {"blank", "dash"}:
                issues.append(f"{col}:{issue}")
        if amount_second_pass:
            for col in ("debit", "credit"):
                status = row.get(f"{col}_second_pass_status", "")
                if status in {"used", "replaced_low_confidence"}:
                    issues.append(f"{col}:recovered_second_pass")
                elif status == "differs":
                    issues.append(f"{col}:second_pass_differs")
        if amount_verifier:
            for col in ("debit", "credit"):
                status = amount_verifier_status(
                    row.get(f"{col}_raw", ""), row.get(f"{col}_verifier_raw", "")
                )
                row[f"{col}_verifier_status"] = status
                # Tesseract is the weaker reader here, so a disagreement is only
                # escalated when Vision itself already failed to parse the amount
                # cleanly. All readings stay in the evidence columns either way.
                vision_unsure = row.get(f"{col}_parse_status") not in {
                    "parsed", "blank", "dash",
                }
                if status == "digit_mismatch":
                    row[f"{col}_verifier_note"] = "digit_mismatch_vision_parsed" if not vision_unsure else "digit_mismatch_vision_unparsed"
                    if vision_unsure:
                        issues.append(f"{col}:verifier_digit_mismatch")
                elif status == "verifier_only":
                    issues.append(f"{col}:verifier_found_unread_amount")
        if page_format == "ambiguous" and (row.get("debit_raw") or row.get("credit_raw")):
            issues.append("page_number_format_ambiguous")
        if label_status.startswith("opencc_"):
            issues.append(f"label:{label_status}")
        if row.get("row_confidence", 0) < confidence_threshold:
            issues.append("low_ocr_confidence")
        if not row.get("label_raw") and (debit or credit):
            issues.append("amount_without_account_label")

        label_upper = norm_text(label_normalized).upper()
        if any(x in label_upper for x in ("總額", "合計", "TOTAL", "TOTAIS")):
            if debit and credit:
                try:
                    if abs(Decimal(debit) - Decimal(credit)) > Decimal("0.01"):
                        issues.append("debit_credit_total_mismatch")
                except InvalidOperation:
                    issues.append("total_validation_error")
        row["review_reason"] = ";".join(dict.fromkeys(issues))
        row["review_status"] = "needs_review" if issues else "auto_check_pass"
    return rows


CHILD_LABEL_RE = re.compile(r"^[\-–—•·]\s*")
LABEL_ZH_VARIANTS = str.maketrans({
    "賬": "帳",
    "帐": "帳",
    "劵": "券",
    "産": "產",
    "価": "價",
    "歴": "歷",
    "査": "查",
    "扺": "抵",
    "资": "資",
    "総": "總",
    "収": "收",
})
# Fuzzy thresholds are deliberately conservative: a wrong account name is more
# expensive than an unmatched one, which stays visible in the output.
LABEL_FUZZY_THRESHOLD = {"zh-Hant": 0.50, "pt": 0.80}
LABEL_FUZZY_MARGIN = 0.05


def _label_language(value: str) -> str:
    return "zh-Hant" if has_chinese(value) else "pt"


def normalize_label_key(text: str, language: str) -> str:
    """Normalise a label for exact/fuzzy lexicon matching."""
    value = CHILD_LABEL_RE.sub("", norm_text(text))
    if language == "zh-Hant":
        value = value.translate(LABEL_ZH_VARIANTS)
        value = re.sub(r"[()（）〔〕【】\[\]、,，:：;；]", "", value)
        return re.sub(r"\s+", "", value)
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^0-9A-Za-z\s]", " ", ascii_text)
    return re.sub(r"\s+", "", ascii_text).casefold()


@lru_cache(maxsize=4)
def _load_label_lexicon_cached(path_str: str, mtime: float):
    entries: list[dict[str, Any]] = []
    with open(path_str, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            standard = norm_text(row.get("label_std", ""))
            if not standard:
                continue
            language = (row.get("language") or _label_language(standard)).strip()
            level = (row.get("level") or "parent").strip()
            keys = {normalize_label_key(standard, language)}
            for alias in (row.get("aliases") or "").split("|"):
                if alias.strip():
                    keys.add(normalize_label_key(alias, language))
            entries.append({
                "id": (row.get("label_id") or "").strip(),
                "std": standard,
                "language": language,
                "level": level,
                "keys": frozenset(keys),
            })
    return tuple(entries)


def load_label_lexicon(path: str | Path) -> list[dict[str, Any]]:
    lexicon_path = Path(path).expanduser()
    if not lexicon_path.exists():
        return []
    return list(_load_label_lexicon_cached(
        str(lexicon_path.resolve()), lexicon_path.stat().st_mtime
    ))


def _match_standard_label(label: str, lexicon: list[dict[str, Any]],
                          level: str) -> tuple[dict[str, Any] | None, float, str]:
    language = _label_language(label)
    key = normalize_label_key(label, language)
    candidates = [
        entry for entry in lexicon
        if entry["language"] == language and entry["level"] == level
    ]
    if not candidates:
        return None, 0.0, "unmatched"
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in candidates:
        score = max(
            (difflib.SequenceMatcher(None, key, candidate).ratio()
             for candidate in entry["keys"]),
            default=0.0,
        )
        scored.append((score, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if key in best["keys"]:
        return best, 1.0, "exact"
    threshold = LABEL_FUZZY_THRESHOLD.get(language, 0.80)
    if best_score >= threshold and (best_score - second_score) >= LABEL_FUZZY_MARGIN:
        return best, best_score, "fuzzy"
    return None, best_score, "unmatched"


def standardize_labels(rows: list[dict[str, Any]],
                       lexicon: list[dict[str, Any]]) -> dict[str, Any]:
    """Map labels to the standard lexicon without touching ``label_raw``."""
    diagnostics: dict[str, Any] = {
        "label_lexicon_entries": len(lexicon),
        "label_standard_exact": 0,
        "label_standard_fuzzy": 0,
        "label_standard_unmatched": 0,
        "label_continuation_rows": 0,
        "label_orphan_children": 0,
    }
    for row in rows:
        raw = norm_text(row.get("label_raw", ""))
        if not raw:
            row["label_standard"] = ""
            row["label_standard_id"] = ""
            row["label_standard_status"] = ""
            row["label_standard_score"] = ""
            row["label_role"] = ""
            continue
        is_child = bool(CHILD_LABEL_RE.match(raw))
        source = row.get("label_normalized") or raw
        if not is_child and raw[:1] in {"，", ",", "一"}:
            candidate = raw[1:].strip()
            child_match, _, child_status = _match_standard_label(
                candidate, lexicon, "child"
            )
            if child_match and child_status == "exact":
                is_child = True
                source = candidate
        row["label_role"] = "child" if is_child else "standalone"
        match, score, status = _match_standard_label(
            source, lexicon, "child" if is_child else "parent"
        )
        if not match and not is_child:
            # Some forms print sub-items without the leading dash (PATACAS,
            # Moedas externas). An exact child-lexicon hit reclassifies the row.
            child_match, child_score, child_status = _match_standard_label(
                source, lexicon, "child"
            )
            if child_match and child_status == "exact":
                match, score, status = child_match, child_score, child_status
                row["label_role"] = "child"
        row["label_standard"] = match["std"] if match else ""
        row["label_standard_id"] = match["id"] if match else ""
        row["label_standard_status"] = status
        row["label_standard_score"] = round(float(score), 3)
        diagnostics[f"label_standard_{status}"] += 1
    return diagnostics


def build_label_hierarchy(rows: list[dict[str, Any]]) -> None:
    """Attach dash-prefixed sub-items to the nearest preceding parent label."""
    last_parent: dict[str, Any] | None = None
    for row in rows:
        role = row.get("label_role")
        if role == "child":
            if last_parent is None:
                row["label_role"] = "orphan_child"
                row["label_path"] = row.get("label_standard") or norm_text(
                    row.get("label_raw", "")
                )
                continue
            parent_name = last_parent.get("label_standard") or norm_text(
                last_parent.get("label_raw", "")
            )
            child_name = row.get("label_standard") or CHILD_LABEL_RE.sub(
                "", norm_text(row.get("label_raw", ""))
            )
            row["parent_label_raw"] = last_parent.get("label_raw", "")
            row["parent_label_standard"] = last_parent.get("label_standard", "")
            row["label_path"] = f"{parent_name} > {child_name}" if parent_name else child_name
            last_parent["label_role"] = "parent"
        elif role == "standalone":
            last_parent = row


def detect_label_continuations(rows: list[dict[str, Any]],
                               lexicon: list[dict[str, Any]]) -> int:
    """Detect a label wrapped onto the line above a row that carries amounts."""
    count = 0
    for index, row in enumerate(rows):
        if index == 0:
            continue
        raw = norm_text(row.get("label_raw", ""))
        if not raw or row.get("label_role") in {"child", "orphan_child"}:
            continue
        if not (row.get("debit_amount") or row.get("credit_amount")):
            continue
        previous = rows[index - 1]
        previous_raw = norm_text(previous.get("label_raw", ""))
        if not previous_raw:
            continue
        if previous.get("debit_amount") or previous.get("credit_amount"):
            continue
        if previous.get("label_role") in {"child", "orphan_child"}:
            continue
        combined = f"{previous_raw} {raw}"
        match, score, _status = _match_standard_label(combined, lexicon, "parent")
        if not match:
            continue
        # The combined text always contains the previous label, so a high score
        # alone is not evidence of a wrapped label. A real wrap extends the
        # previous standard ("... no Território"); a different concept does not.
        previous_standard = previous.get("label_standard") or ""
        if match["std"] == previous_standard:
            continue
        prefix_wrap = bool(
            previous_standard
            and match["std"].startswith(previous_standard)
            and score >= 0.90
            and len(raw) <= 30
        )
        current_score = float(row.get("label_standard_score") or 0.0)
        threshold = LABEL_FUZZY_THRESHOLD.get(_label_language(combined), 0.80)
        score_wrap = (
            row.get("label_standard_status") != "exact"
            and score >= max(threshold, current_score + 0.15)
        )
        if not (prefix_wrap or score_wrap):
            continue
        row["label_continuation_raw"] = previous_raw
        row["label_combined_raw"] = combined
        row["label_standard"] = match["std"]
        row["label_standard_id"] = match["id"]
        row["label_standard_status"] = "continuation"
        row["label_standard_score"] = round(float(score), 3)
        previous["label_role"] = "continuation_line"
        count += 1
    return count


def apply_label_review(rows: list[dict[str, Any]]) -> None:
    """Route uncertain, orphan and merged labels to human review."""
    for row in rows:
        reasons: list[str] = []
        status = row.get("label_standard_status", "")
        if status == "continuation":
            reasons.append("label_continuation_merged")
        if row.get("label_role") == "orphan_child":
            reasons.append("label_orphan_child")
        score = float(row.get("label_standard_score") or 0.0)
        if (status == "unmatched"
                and score >= 0.55
                and (row.get("debit_amount") or row.get("credit_amount"))):
            reasons.append("label_standard_uncertain")
        if status == "fuzzy":
            if score < 0.70:
                reasons.append("label_standard_low_similarity")
            elif score < 0.90:
                # A 0.70-0.90 Chinese match can be a one-character OCR error on
                # a short account name; keep it visible for human review.
                reasons.append("label_standard_fuzzy_review")
        if not reasons:
            continue
        existing = [x for x in (row.get("review_reason") or "").split(";") if x]
        row["review_reason"] = ";".join(dict.fromkeys(existing + reasons))
        row["review_status"] = "needs_review"


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


def recover_header_metadata(
    page: fitz.Page,
    images_dir: Path,
    page_number: int,
    languages: list[str],
    *,
    engine: str,
    level: str,
    minimum_text_height: float,
    preprocess: bool,
    language_correction: str,
    engines: dict[str, Any],
    device: str,
    ocr_version: str,
) -> tuple[dict[str, Any], list[OCRBox]]:
    """Best-effort recovery of bank name/date from the full PDF page header.

    This is deliberately separate from the embedded table image: some Gazettes
    put the bank logo above the table crop (issue 45 page 17). The function
    never overwrites a name that was already found; it only supplies evidence
    when the table-image header is empty.
    """
    image_path = images_dir / f"page_{page_number:04d}_header_fallback.png"
    diagnostics: dict[str, Any] = {
        "header_fallback_status": "no_name",
        "header_fallback_engine": engine,
        "header_fallback_image": str(image_path),
        "header_fallback_lines": "",
        "header_fallback_bank_name_lines": "",
        "header_fallback_bank_name": "",
        "header_fallback_statement_date": "",
        "header_fallback_observation_count": 0,
    }
    started = time.perf_counter()
    try:
        width, height = render_page_header_image(page, image_path)
        langs = languages or ["chinese_cht"]
        if engine == "vision":
            boxes, vision_diagnostics = run_vision_header_ocr(
                image_path, langs, level=level,
                minimum_text_height=minimum_text_height,
                preprocess=preprocess,
                language_correction=language_correction,
            )
            boxes = dedupe_boxes(boxes)
            for key, value in vision_diagnostics.items():
                diagnostics[f"header_fallback_{key}"] = value
        else:
            boxes = []
            for language in langs:
                boxes.extend(run_ocr(
                    image_path, language, engine, device, ocr_version, engines
                ))
            for box in boxes:
                box.region = "header"
            boxes = dedupe_boxes(boxes)
        diagnostics["header_fallback_elapsed_seconds"] = round(
            time.perf_counter() - started, 3
        )
        diagnostics["header_fallback_observation_count"] = len(boxes)
        diagnostics["header_fallback_image_width"] = width
        diagnostics["header_fallback_image_height"] = height
        lines = ocr_boxes_to_lines(boxes, float(height))
        diagnostics["header_fallback_lines"] = " | ".join(lines[:20])
        bank_name, used_lines = extract_bank_name_from_header_lines(lines)
        statement_date = parse_statement_date("\n".join(lines))
        diagnostics["header_fallback_bank_name"] = bank_name
        diagnostics["header_fallback_bank_name_lines"] = " | ".join(used_lines)
        diagnostics["header_fallback_statement_date"] = statement_date
        if bank_name or statement_date:
            diagnostics["header_fallback_status"] = "recovered"
        else:
            diagnostics["header_fallback_status"] = "no_name"
        return diagnostics, boxes
    except Exception as exc:  # best-effort fallback must not abort the page
        diagnostics["header_fallback_status"] = "failed"
        diagnostics["header_fallback_error"] = str(exc)[-1000:]
        diagnostics["header_fallback_elapsed_seconds"] = round(
            time.perf_counter() - started, 3
        )
        return diagnostics, []


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
            "header_fallback_requested": args.header_fallback,
            "header_fallback_status": (
                "inspect_only" if args.inspect_only else "not_needed"
            ),
        }
        document_rows.append(docs_record)

        if args.inspect_only:
            continue

        b1, b2, label_x0, boundary_source = detect_column_layout(image_path)
        vision_diagnostics: dict[str, Any] = {}
        if args.engine == "vision":
            all_boxes, vision_diagnostics = run_vision_ocr(
                image_path, langs, (b1, b2), level=args.vision_level,
                minimum_text_height=args.vision_minimum_text_height,
                preprocess=args.vision_preprocess == "on",
                language_correction=args.vision_language_correction,
                label_x0=label_x0,
            )
        else:
            all_boxes = []
            for lang in langs:
                boxes = run_ocr(image_path, lang, args.engine, args.device, args.ocr_version, engines)
                all_boxes.extend(boxes)
        all_boxes = dedupe_boxes(all_boxes)
        header_boxes = [b for b in all_boxes if b.region == "header"]
        if not header_boxes:
            header_boxes = [b for b in all_boxes if b.cy <= table_image.height * 0.24]
        table_boxes = [b for b in all_boxes if b.region == "table"]
        # Fill missing/embedded-only entity name and date from the dedicated header ROI.
        header_lines = ocr_boxes_to_lines(header_boxes, table_image.height * 0.24)
        ocr_header_text = "\n".join(header_lines)
        if meta.get("bank_name_source") == "image_header_check":
            heading_pos = next((
                i for i, x in enumerate(header_lines)
                if BALANCETE_HEADING_RE.search(x)
            ), None)
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
        if args.header_fallback == "auto" and (
            not meta.get("bank_name") or not meta.get("statement_date")
        ):
            fallback_diagnostics, fallback_boxes = recover_header_metadata(
                page, images_dir, page_index + 1, langs,
                engine=args.engine,
                level=args.vision_level,
                minimum_text_height=args.vision_minimum_text_height,
                preprocess=args.vision_preprocess == "on",
                language_correction=args.vision_language_correction,
                engines=engines,
                device=args.device,
                ocr_version=args.ocr_version,
            )
            if (
                fallback_diagnostics.get("header_fallback_bank_name")
                and not meta.get("bank_name")
            ):
                meta["bank_name"] = fallback_diagnostics["header_fallback_bank_name"]
                # This name came from the full PDF page header, not the embedded
                # table image; keep the provenance explicit.
                meta["bank_name_source"] = "ocr_full_page_header"
            if (
                fallback_diagnostics.get("header_fallback_statement_date")
                and not meta.get("statement_date")
            ):
                meta["statement_date"] = fallback_diagnostics[
                    "header_fallback_statement_date"
                ]
            fallback_image = fallback_diagnostics.get("header_fallback_image", "")
            for box in fallback_boxes:
                raw_rows.append({
                    **meta,
                    "language": box.language,
                    "region": "header_fallback",
                    "text": box.text,
                    "confidence": round(box.score, 4),
                    "x0": round(box.x0, 2), "y0": round(box.y0, 2),
                    "x1": round(box.x1, 2), "y1": round(box.y1, 2),
                    "image_path": fallback_image,
                })
            document_rows[-1].update(fallback_diagnostics)
        elif args.header_fallback == "off":
            document_rows[-1]["header_fallback_status"] = "off"
        meta["metadata_status"] = "metadata_extracted" if (meta.get("bank_name") and meta.get("statement_date")) else "needs_manual_review"
        document_rows[-1].update({k: meta.get(k, "") for k in (
            "bank_name", "statement_date", "bank_name_source", "metadata_status"
        )})
        for box in all_boxes:
            raw_rows.append({
                **meta,
                "language": box.language,
                "region": box.region,
                "text": box.text,
                "confidence": round(box.score, 4),
                "x0": round(box.x0, 2), "y0": round(box.y0, 2),
                "x1": round(box.x1, 2), "y1": round(box.y1, 2),
                "image_path": str(image_path),
            })
        row_bands: list[tuple[float, float]] = []
        row_structure_source = "clustered"
        row_structure_guard = ""
        if args.row_structure != "clustered":
            candidate_bands, candidate_source = detect_row_bands(image_path, (b1, b2))
            if candidate_source == "projection" and candidate_bands:
                heights = [b.height for b in table_boxes if b.height > 2]
                median_box = statistics.median(heights) if heights else 0.0
                clusters = _cluster_boxes(table_boxes, max(8.0, median_box * 0.58))
                band_heights = [y1 - y0 for y0, y1 in candidate_bands]
                band_count_ok = 0.6 * len(clusters) <= len(candidate_bands) <= 1.3 * len(clusters)
                band_height_ok = not median_box or statistics.median(band_heights) <= 1.5 * median_box
                # A band holding two reading clusters means the projection merged
                # table rows; using it would silently drop a row.
                per_band: list[int] = [0] * len(candidate_bands)
                for cluster in clusters:
                    for index, (y0, y1) in enumerate(candidate_bands):
                        if y0 <= cluster["cy"] < y1:
                            per_band[index] += 1
                            break
                single_row_bands = not per_band or max(per_band) <= 1
                if not band_count_ok:
                    row_structure_guard = "band_count_mismatch"
                elif not band_height_ok:
                    row_structure_guard = "band_height_mismatch"
                elif not single_row_bands:
                    row_structure_guard = "band_merged_rows"
                if args.row_structure == "projection" or not row_structure_guard:
                    row_bands = candidate_bands
                    row_structure_source = "projection"
        rows = group_boxes_into_rows(
            table_boxes, table_image.width, table_image.height, (b1, b2),
            row_bands=row_bands or None,
        )
        second_pass_diagnostics: dict[str, Any] = {
            "amount_second_pass_status": "off",
            "amount_second_pass_cells": 0,
            "amount_second_pass_filled": 0,
            "amount_second_pass_seconds": 0.0,
        }
        if args.amount_second_pass == "ink" and args.engine == "vision":
            page_profile = infer_number_format(
                [value for row in rows
                 for value in (row.get("debit_raw", ""), row.get("credit_raw", ""))]
            )
            page_format = (
                args.number_format
                if args.number_format in {"comma_dot", "dot_comma"}
                else page_profile["number_format"]
            )
            second_pass_diagnostics = second_pass_amount_cells(
                rows, image_path, table_image.width, table_image.height, (b1, b2),
                page_format, args.confidence_threshold,
            )
        verifier_diagnostics = attach_amount_verifier(
            rows, image_path, table_image.width, table_image.height, (b1, b2),
            args.amount_verifier,
        )
        rows = validate_rows(
            rows, args.confidence_threshold,
            number_format_override=args.number_format,
            chinese_label_normalization=args.chinese_label_normalization,
            repair_ocr_separators=args.repair_ocr_separators,
            amount_verifier=verifier_diagnostics.get("amount_verifier_status") == "ran",
            amount_second_pass=second_pass_diagnostics.get("amount_second_pass_status") == "ran",
        )
        label_diagnostics: dict[str, Any] = {}
        if args.label_standardization:
            lexicon = load_label_lexicon(args.label_lexicon)
            if lexicon:
                label_diagnostics = standardize_labels(rows, lexicon)
                build_label_hierarchy(rows)
                label_diagnostics["label_orphan_children"] = sum(
                    1 for row in rows if row.get("label_role") == "orphan_child"
                )
                label_diagnostics["label_continuation_rows"] = detect_label_continuations(
                    rows, lexicon
                )
                apply_label_review(rows)
            else:
                label_diagnostics = {"label_standard_status": "lexicon_missing"}
        if rows:
            document_rows[-1]["number_format_detected"] = rows[0].get("number_format", "")
            document_rows[-1]["number_format_confidence"] = rows[0].get("number_format_confidence", "")
            document_rows[-1]["number_format_evidence"] = rows[0].get("number_format_evidence", "")
        document_rows[-1].update(vision_diagnostics)
        document_rows[-1].update(verifier_diagnostics)
        document_rows[-1].update(second_pass_diagnostics)
        document_rows[-1].update(label_diagnostics)
        document_rows[-1]["row_structure_source"] = row_structure_source
        document_rows[-1]["row_structure_guard"] = row_structure_guard
        document_rows[-1]["row_band_count"] = len(row_bands)
        document_rows[-1]["label_column_x0"] = round(label_x0, 4)
        for row in rows:
            row["column_boundary_source"] = boundary_source
            if str(boundary_source).startswith("fallback"):
                current = row.get("review_reason", "")
                row["review_reason"] = ";".join(x for x in (current, "fallback_column_boundaries") if x)
                row["review_status"] = "needs_review"
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
        "vision_level": args.vision_level if args.engine == "vision" else "",
        "vision_preprocess": args.vision_preprocess if args.engine == "vision" else "",
        "vision_language_correction": args.vision_language_correction if args.engine == "vision" else "",
        "header_fallback": args.header_fallback,
        "header_fallback_pages": sum(
            1 for row in document_rows
            if row.get("header_fallback_status") == "recovered"
        ),
        "row_structure": args.row_structure,
        "amount_verifier": args.amount_verifier,
        "amount_second_pass": args.amount_second_pass,
        "label_standardization": args.label_standardization,
        "label_lexicon": args.label_lexicon if args.label_standardization else "",
        "number_format_override": args.number_format,
        "chinese_label_normalization": args.chinese_label_normalization,
        "repair_ocr_separators": args.repair_ocr_separators,
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
    default_engine = "vision" if sys.platform == "darwin" else "paddle"
    p = argparse.ArgumentParser(description="澳門公報 Balancete OCR / Excel / CSV prototype v0.4.0")
    p.add_argument("pdf", help="輸入 PDF 路徑")
    p.add_argument("--outdir", default="output", help="輸出資料夾（預設：output）")
    p.add_argument("--pages", default="all", help="處理頁數，例如 7-32；預設 all")
    p.add_argument("--engine", default=default_engine, choices=["vision", "paddle", "tesseract"],
                   help=f"OCR 引擎；macOS 預設 Vision，其他平台預設 Paddle（目前：{default_engine}）")
    p.add_argument("--device", default="cpu", choices=["cpu", "gpu"], help="OCR 運算裝置（PaddleOCR 使用）")
    p.add_argument("--ocr-version", default="PP-OCRv5", help="PaddleOCR 模型版本")
    p.add_argument("--ocr-langs", default="auto", help="auto 或逗號分隔，例如 chinese_cht,pt,en")
    p.add_argument("--vision-level", default="accurate", choices=["fast", "accurate"],
                   help="Apple Vision 模式；accurate 較重視識別完整度，fast 較快")
    p.add_argument("--vision-minimum-text-height", type=float, default=0.004,
                   help="Vision 最小相對文字高度；降低可讀更細文字但可能較慢")
    p.add_argument("--vision-preprocess", default="off", choices=["on", "off"],
                   help="Vision 辨識前灰階／對比預處理（實驗性；預設 off，需先做人工標註比對）")
    p.add_argument("--vision-language-correction", default="labels",
                   choices=["off", "labels", "all"],
                   help="Vision 語言修正：off、只套用於文字標籤（預設 labels）、或全部區域")
    p.add_argument("--header-fallback", default="auto", choices=["auto", "off"],
                   help="銀行名／日期缺失時，另外 OCR 完整 PDF 頁首（預設 auto；只補缺失值）")
    p.add_argument("--row-structure", default="clustered", choices=["auto", "projection", "clustered"],
                   help="行分組：clustered（預設）或 experimental projection；auto 只在安全檢查通過時用投影")
    p.add_argument("--amount-verifier", default="off", choices=["off", "tesseract"],
                   help="experimental 金額交叉驗證（tesseract）；只加證據，唔會改 raw 或已解析金額")
    p.add_argument("--amount-second-pass", default="ink", choices=["off", "ink"],
                   help="對空白／低信心金額格以 3x 重讀（只加證據；採用時標 recovered_second_pass 並入覆核）")
    p.add_argument("--label-lexicon",
                   default=str(Path(__file__).with_name("labels") / "label_lexicon.csv"),
                   help="標準標籤詞表 CSV；預設 labels/label_lexicon.csv")
    p.add_argument("--no-label-standardization", dest="label_standardization",
                   action="store_false",
                   help="停用標籤標準化及父子層偵測")
    p.set_defaults(label_standardization=True)
    p.add_argument("--number-format", default="auto", choices=["auto", "comma_dot", "dot_comma"],
                   help="數字格式；auto 按每張 TB 證據推斷，不按中文／葡文猜測")
    p.add_argument("--chinese-label-normalization", default="s2hk", choices=["off", "s2t", "s2hk"],
                   help="另行輸出中文標籤正規化欄位；raw OCR 永不覆蓋")
    p.add_argument("--no-repair-ocr-separators", dest="repair_ocr_separators", action="store_false",
                   help="停用有分組證據的 OCR 千分位標點修復")
    p.set_defaults(repair_ocr_separators=True)
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
