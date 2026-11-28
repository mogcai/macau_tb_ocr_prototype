#!/usr/bin/env python3
"""Macau Gazette Balancete OCR prototype v0.7.0.

Extract embedded statement images, run native Apple Vision on macOS or an optional
PaddleOCR/Tesseract backend, reconstruct Account/Debit/Credit rows, normalize
Chinese labels and page-specific numeric formats, then export auditable CSV/XLSX.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
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

APP_VERSION = "0.7.0"
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


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, computed before OCR so provenance is OCR-time."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


_NATIVE_TB_AMOUNT_RE = re.compile(
    r"(?<![\d.,])\d{1,3}(?:[ ,.]\d{3})*[.,]\d{2}(?![\d.,])"
)


def has_native_text_tb(page_text: str) -> bool:
    """True for a vector/text-layer Balancete with no large embedded image.

    Some Gazette pages (2025-32 page 9 永豐銀行, 2026-17 page 465 廣發銀行)
    print the table with PDF text and vector rules instead of scanning an
    embedded bitmap. They must still be processed, but a heading alone is not
    enough evidence: require both the debit and credit column headings and a
    numeric grid. The rendered page is used as the source image downstream, so
    raw OCR evidence and bounding boxes are preserved like any scanned page.
    """
    text = norm_text(page_text)
    if not BALANCETE_HEADING_RE.search(text):
        return False
    upper = text.upper()
    has_columns = (
        ("借方" in text and "貸方" in text)
        or ("DEVEDORES" in upper and "CREDORES" in upper)
    )
    if not has_columns:
        return False
    return len(_NATIVE_TB_AMOUNT_RE.findall(text)) >= 5


def is_tb_page(page_text: str, has_large_image: bool) -> bool:
    upper = norm_text(page_text).upper()
    has_keyword = any(term in upper for term in TB_TERMS)
    # A few Portuguese-language pages put the bank/title only inside the image;
    # the native layer may contain only signatory lines and the publication fee.
    has_gazette_fee = "CUSTO DESTA PUBLICAÇÃO" in upper or "是項刊登費用" in upper
    if has_large_image and (has_keyword or has_gazette_fee):
        return True
    # Do not lower the embedded-image threshold for every page: a native text
    # page must show the TB heading plus debit/credit columns and amounts.
    return has_native_text_tb(page_text)


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


def _render_image_placement(
    page: fitz.Page,
    xref: int,
    width: int,
    height: int,
    out_path: Path,
) -> tuple[int, int] | None:
    """Render an embedded image's page area at its native resolution.

    Used when the XObject cannot be converted directly. ImageMask (stencil)
    images have no /ColorSpace; PyMuPDF exposes their samples as white-on-black,
    so saving them directly inverts the scan. The PDF renderer resolves the
    mask polarity and background correctly.
    """
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return None
    if not rects:
        return None
    rect = rects[0] & page.rect
    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
        return None
    zoom = max(width / rect.width, height / rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
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
        # ImageMask XObjects have colorspace None and cannot be converted (issue
        # 2026-06 page 31); render the placement instead of aborting the run.
        if pix.colorspace is not None:
            try:
                pix = fitz.Pixmap(fitz.csRGB, pix)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                pix.save(str(out_path))
                return TableImage(page_index, out_path, pix.width, pix.height, xref, True)
            except Exception:
                pass
        rendered = _render_image_placement(page, xref, pix.width, pix.height, out_path)
        if rendered is not None:
            return TableImage(page_index, out_path, rendered[0], rendered[1], xref, False)

    # Fallback: render the page itself. This is less ideal and should be manually checked.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=False)
    pix.save(str(out_path))
    return TableImage(page_index, out_path, pix.width, pix.height, None, False)


# Outer table frames sit near the image edges. Treating them as amount
# separators shifts every ROI (issue 5 page 6: right frame became sep2, so the
# real label|debit rule was misread as an account-code trim).
_COLUMN_LEFT_FRAME_MAX = 0.12
_COLUMN_RIGHT_FRAME_MIN = 0.88
_COLUMN_MIN_AMOUNT_GAP = 0.08
_COLUMN_CODE_GAP = 0.05
_COLUMN_CODE_STRENGTH_RATIO = 0.4
# Scanned rules are often interrupted or lighter than full-height table frames.
# A strict 12%-height opening misses the label|debit rule on inset/skewed
# tables (2025-46 pages 20, 28, 31, 32), so the amount separators are detected
# with a shorter kernel. Page-wide OCR is unaffected; this only locates columns.
_COLUMN_RULE_KERNEL_FRACTION = 0.06
_COLUMN_RULE_MIN_FRACTION = 0.05
# Amounts are right-aligned against the debit|credit rule. The detected rule
# can sit a few pixels to the left of the glyphs, so a 0.2% margin clips the
# final digit (2025-46 page 20). Extend only the debit ROI's right edge; the
# credit ROI stays close to the rule because a wider left edge degraded its
# recognition on 2025-46 page 17.
_AMOUNT_ROI_PADDING = 0.006


def _merge_vertical_rules(
    candidates: list[tuple[float, float]], *, merge_gap: float = 0.02
) -> list[tuple[float, float]]:
    """Merge near-duplicate vertical rules (double edges of one separator)."""
    merged: list[tuple[float, float]] = []
    for strength, frac in sorted(candidates, key=lambda item: item[1]):
        if merged and frac - merged[-1][1] < merge_gap:
            if strength > merged[-1][0]:
                merged[-1] = (strength, frac)
        else:
            merged.append((strength, frac))
    return merged


def _amount_column_widths_similar(gap_a: float, gap_b: float) -> bool:
    """True when two gaps look like side-by-side debit/credit columns."""
    if gap_a < _COLUMN_MIN_AMOUNT_GAP or gap_b < _COLUMN_MIN_AMOUNT_GAP:
        return False
    # Amount columns are rarely wider than ~1/3 of the page; a much larger gap
    # is the account-label span (or a code|label span), not an amount column.
    if gap_a > 0.32 or gap_b > 0.32:
        return False
    return min(gap_a, gap_b) / max(gap_a, gap_b) >= 0.55


def _label_x0_from_code_rules(
    candidates: list[tuple[float, float]],
    *,
    sep1: float,
    strength_pool: list[tuple[float, float]],
) -> float:
    """Raise label_x0 when a strong rule left of sep1 looks like an account-code column."""
    left_rules = [
        item for item in candidates if item[1] < sep1 - _COLUMN_CODE_GAP
    ]
    if not left_rules or not strength_pool:
        return 0.005
    code_strength, code_frac = max(left_rules, key=lambda item: item[1])
    strongest = max(item[0] for item in strength_pool)
    if code_strength >= _COLUMN_CODE_STRENGTH_RATIO * strongest:
        # Keep enough white space right of the code rule that the printed codes
        # cannot enter the label ROI. The exact fraction matters because Apple
        # Vision treats the ROI as a normalised rect; this matches the value
        # that previously produced clean labels on the issue-45 code-column
        # page.
        return min(0.99, max(0.005, code_frac + 0.0033))
    return 0.005


def _select_amount_column_rules(
    merged: list[tuple[float, float]], *, allow_code_column: bool
) -> tuple[float, float, float] | None:
    """Pick debit/credit separators from interior rules; optionally trim a code column.

    Returns ``(sep1, sep2, label_x0)`` or ``None`` when fewer than two usable
    interior rules remain.

    Layout cases handled here:

    - Two interior rules: label | debit | credit.
    - Three interior rules with similar right-hand gaps: label | debit | credit
      plus a right table frame that is not near the image edge (issue 5).
    - Three interior rules with a wide left span: code | label | debit | credit
      (issue 45 page 29).
    - Four+ interior rules: left/right table frames around a 3-column body;
      drop the outermost pair before choosing amount separators.
    """
    interior = [
        item for item in merged
        if _COLUMN_LEFT_FRAME_MAX < item[1] < _COLUMN_RIGHT_FRAME_MIN
    ]
    if len(interior) < 2:
        return None

    # Framed 3-column tables often keep left/right rules inside the interior
    # band (whitespace outside the printed table). Drop those outer frames.
    body = interior[1:-1] if len(interior) >= 4 else interior
    if len(body) < 2:
        return None

    label_x0 = 0.005
    if len(body) == 2:
        sep1, sep2 = body[0][1], body[1][1]
    elif len(body) >= 3:
        fracs = [item[1] for item in body]
        gap_left = fracs[1] - fracs[0]
        gap_right = fracs[2] - fracs[1]
        if _amount_column_widths_similar(gap_left, gap_right):
            # body[2] (and anything further right in body) is a trailing frame.
            sep1, sep2 = fracs[0], fracs[1]
        else:
            sep1, sep2 = fracs[-2], fracs[-1]
            if allow_code_column:
                label_x0 = _label_x0_from_code_rules(
                    body[:-2], sep1=sep1, strength_pool=body
                )
    else:
        return None

    if sep1 + _COLUMN_MIN_AMOUNT_GAP >= sep2:
        return None
    return sep1, sep2, label_x0


def _detect_vertical_rule_groups(image: Any) -> list[tuple[int, int, float]]:
    """Return ``(x_start, x_end, strength)`` for strong vertical rules.

    Shared by the amount-separator detector and by amount-column tiling. A
    full-height rule must never sit inside an OCR tile: it bridges every row in
    the ink projection and hides the blank gaps the tiler needs.
    """
    import cv2
    import numpy as np

    h, w = image.shape[:2]
    binary = cv2.threshold(
        image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    kernel_h = max(25, int(h * _COLUMN_RULE_KERNEL_FRACTION))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_h))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    projection = (vertical > 0).sum(axis=0)
    min_run = max(2, int(w * 0.0015))
    xs = np.where(projection >= max(20, int(h * _COLUMN_RULE_MIN_FRACTION)))[0]
    groups: list[list[int]] = []
    for x in xs:
        x = int(x)
        if groups and x - groups[-1][-1] <= min_run:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [
        (int(g[0]), int(g[-1]), float(projection[g].max()))
        for g in groups
    ]


def _table_vertical_span(image: Any) -> tuple[int, int] | None:
    """Return the top/bottom y of the table's long horizontal rules."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    h, w = image.shape[:2]
    binary = cv2.threshold(
        image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(20, int(w * 0.25)), 1)
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    row_ink = (horizontal > 0).sum(axis=1)
    ys = np.where(row_ink >= w * 0.25)[0]
    if not len(ys):
        return None
    groups: list[list[int]] = [[int(ys[0])]]
    for y in ys[1:]:
        y = int(y)
        if y - groups[-1][-1] <= 4:
            groups[-1].append(y)
        else:
            groups.append([y])
    centers = [int(round(sum(group) / len(group))) for group in groups]
    if len(centers) < 2 or centers[-1] - centers[0] < h * 0.05:
        return None
    return centers[0], centers[-1]


def _blank_margin(image: Any, frac: float, side: str,
                  y_span: tuple[int, int] | None = None) -> bool:
    """True when the strip outside a vertical rule has no column content.

    Inset tables can put a frame at 15–25% of the page width, where the fixed
    near-edge cut-offs no longer apply (issue 20/2025 page 35). A frame's
    outside has few *rows* with any ink; a label or code column has ink on most
    rows. Row coverage, not total ink area, separates a frame from a seal or a
    scanning smudge that happens to overlap the margin.

    When the table's top/bottom rules are known, only the table body is
    measured. Otherwise every row that has ink in the central page region is
    used, so a table that occupies only the top quarter of a page is still
    measured over its own content rather than the full page. A 1% edge margin
    is excluded: scanner border lines run the full height and would otherwise
    make every margin look inked.
    """
    h, w = image.shape[:2]
    x = int(frac * w)
    edge = max(2, int(w * 0.01))
    if side == "left":
        band = image[:, edge:max(edge + 1, x - 8)]
    else:
        band = image[:, min(w - 1, x + 9):max(0, w - edge)]
    if y_span is not None:
        y0, y1 = max(0, y_span[0]), min(h, y_span[1] + 1)
        band = band[y0:y1]
        central = (image[y0:y1, edge:max(edge + 1, w - edge)] < 160).any(axis=1)
    else:
        central = (image[:, edge:max(edge + 1, w - edge)] < 160).any(axis=1)
    rows = central.nonzero()[0]
    if band.size == 0 or len(rows) == 0:
        return False
    has_ink = (band[rows] < 160).any(axis=1)
    return float(has_ink.mean()) < 0.6


def detect_column_layout(image_path: Path) -> tuple[float, float, float, str]:
    """Find the two amount-column rules and the label column's left edge.

    Returns ``(sep1, sep2, label_x0, source)``. Some Balancetes add a leading
    account-code column (issue 45 page 29 has code / label / debit / credit), so
    the two **rightmost interior** rules are the amount columns and any interior
    rule further left is treated as the code/label separator. Outer table frames
    near the image edges are ignored so they cannot displace the amount ROIs.
    Picking the strongest rules instead would put the label text into the debit
    ROI and both amounts into the credit ROI, which silently shifts every value.
    """
    try:
        import cv2
        import numpy as np
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError("cv2 could not load image")
        h, w = image.shape[:2]
        span = _table_vertical_span(image)
        candidates: list[tuple[float, float]] = []
        for g0, g1, strength in _detect_vertical_rule_groups(image):
            x = int(round((g0 + g1) / 2))
            frac = x / w
            # Keep near-edge peaks so frame filtering can drop them explicitly.
            if 0.05 < frac < 0.95:
                candidates.append((strength, frac))
        merged = _merge_vertical_rules(candidates)
        # An inset table's frame can sit at 15–25% of the page width, where the
        # fixed near-edge cut-offs no longer apply (issue 20/2025 page 35 put
        # the left frame at 0.157 and shifted every amount into the credit
        # column). Drop frames whose outside has no column content, but never
        # drop below the two amount separators. A seal can be detected as a
        # rule too, so repeat until the next rule actually has column content.
        while len(merged) > 2 and _blank_margin(image, merged[0][1], "left", span):
            merged = merged[1:]
        while len(merged) > 2 and _blank_margin(image, merged[-1][1], "right", span):
            merged = merged[:-1]
        selected = _select_amount_column_rules(merged, allow_code_column=True)
        if selected is not None:
            sep1, sep2, label_x0 = selected
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
            if (
                0.05 < frac < 0.95
                and smooth[x] >= smooth[x - 1]
                and smooth[x] > smooth[x + 1]
                and smooth[x] >= min_strength
            ):
                local_peaks.append((float(smooth[x]), frac))
        merged = _merge_vertical_rules(local_peaks)
        # Projection peaks come from dense text as well as rules, so the frame
        # heuristic is not applied here; it would drop the real separators when
        # a table's margin contains a seal or a full-width text block.
        # Projection peaks from dense label text can look like a code column, so
        # never trim label_x0 on this fallback path.
        selected = _select_amount_column_rules(merged, allow_code_column=False)
        if selected is not None:
            sep1, sep2, label_x0 = selected
            return sep1, sep2, label_x0, "detected_projection"
    except Exception:
        pass
    return 0.48, 0.75, 0.005, "fallback_0.48_0.75"


def detect_column_boundaries(image_path: Path) -> tuple[float, float, str]:
    """Backward-compatible wrapper returning only the two amount separators."""
    sep1, sep2, _label_x0, source = detect_column_layout(image_path)
    return sep1, sep2, source


# Numeric-column tiling. Apple Vision's accurate recognizer silently drops
# contiguous rows when one ROI spans a tall table (issue 19/2025 page 4 lost
# four credit amounts; page 5 lost most of the credit column), while the same
# cells read cleanly in short ROIs. Tiles are cut only at blank rows, so no
# amount is ever split.
_AMOUNT_TILE_MAX_HEIGHT_PX = 600
_AMOUNT_TILE_GAP_PX = 3
_AMOUNT_TILE_INK_RATIO = 0.004
_AMOUNT_TILE_MIN_INK_PX = 3


def _ink_row_tiles(
    masked: Any,
    x0: int,
    x1: int,
    *,
    max_tile_height: int,
) -> list[tuple[int, int]]:
    """Group ink rows into tiles whose cut points are blank row gaps."""
    import numpy as np

    h, w = masked.shape[:2]
    x0 = max(0, min(w - 1, x0))
    x1 = max(x0 + 1, min(w, x1))
    ink = (masked[:, x0:x1] < 160).sum(axis=1)
    threshold = max(
        _AMOUNT_TILE_MIN_INK_PX, int((x1 - x0) * _AMOUNT_TILE_INK_RATIO)
    )
    rows = np.where(ink >= threshold)[0]
    rows = rows[(rows > h * 0.03) & (rows < h * 0.99)]
    bands: list[tuple[int, int]] = []
    if len(rows):
        start = prev = int(rows[0])
        for value in rows[1:]:
            y = int(value)
            if y - prev > _AMOUNT_TILE_GAP_PX:
                bands.append((start, prev))
                start = y
            prev = y
        bands.append((start, prev))
    grouped: list[tuple[int, int]] = []
    current: list[tuple[int, int]] = []
    for band in bands:
        if current and band[1] - current[0][0] > max_tile_height:
            grouped.append((current[0][0], current[-1][1]))
            current = []
        current.append(band)
    if current:
        grouped.append((current[0][0], current[-1][1]))
    return [(max(0, y0 - 2), min(h, y1 + 2)) for y0, y1 in grouped]


def _mask_vertical_rules(image: Any) -> Any:
    """Return a copy of the image with every detected vertical rule whitened."""
    masked = image.copy()
    for g0, g1, _strength in _detect_vertical_rule_groups(image):
        masked[:, max(0, g0 - 1):g1 + 2] = 255
    return masked


def detect_amount_tiles(
    image: Any,
    sep1: float,
    sep2: float,
    *,
    max_tile_height: int = _AMOUNT_TILE_MAX_HEIGHT_PX,
) -> tuple[dict[str, list[tuple[int, int]]], Any]:
    """Split each amount column into row-gap-aligned OCR tiles.

    Returns ``(tiles, masked_image)`` where tile values are ``(y0, y1)`` pixel
    bounds in the source image. The returned image has every detected vertical
    rule painted white: a full-height rule inside the ROI both confuses Vision
    and hides the blank-row gaps this function uses as cut points.
    """
    h, w = image.shape[:2]
    masked = _mask_vertical_rules(image)

    columns = {
        "debit": (
            int((sep1 + 0.001) * w),
            int(min(0.995, sep2 + _AMOUNT_ROI_PADDING) * w),
        ),
        "credit": (int((sep2 + 0.001) * w), int(0.995 * w)),
    }
    tiles: dict[str, list[tuple[int, int]]] = {}
    for column, (x0, x1) in columns.items():
        grouped = _ink_row_tiles(masked, x0, x1, max_tile_height=max_tile_height)
        if not grouped:
            # No ink evidence: preserve the previous full-height behaviour.
            grouped = [(int(h * 0.035), int(h * 0.997))]
        tiles[column] = grouped
    return tiles, masked


def detect_label_tiles(
    image: Any,
    sep1: float,
    label_x0: float,
    *,
    max_tile_height: int = _AMOUNT_TILE_MAX_HEIGHT_PX,
) -> list[tuple[int, int]]:
    """Split the account-label column into row-gap-aligned OCR tiles.

    The label ROI spans the whole table, so it is vulnerable to the same Vision
    block-dropping as the amount columns (issue 19/2025 page 9 lost a dozen
    labels). Returns ``[]`` when there is too little width to tile.
    """
    h, w = image.shape[:2]
    masked = _mask_vertical_rules(image)
    x0 = int(max(0.005, label_x0) * w)
    x1 = int(max(0.01, sep1 - 0.002) * w)
    if x1 - x0 < 50:
        return []
    return _ink_row_tiles(masked, x0, x1, max_tile_height=max_tile_height)


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
    label_tiles: list[tuple[int, int]] = []

    # The account-label ROI spans the whole table and is just as vulnerable to
    # Vision dropping contiguous rows as the amount columns, so it is tiled the
    # same way. Header stays a single short ROI.
    tiles: dict[str, list[tuple[int, int]]] = {}
    image_height = 0
    try:
        import cv2
        gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    except Exception:  # noqa: BLE001 - tiling is an optimisation, not a prerequisite
        gray = None
    if gray is not None:
        image_height = gray.shape[0]
        # Tiles are derived from a rule-masked copy so blank gaps are visible.
        # OCR itself runs on the untouched image: whitening a rule column can
        # clip a final glyph that touches the rule (2025-19 page 8
        # "…745.66" became "…745.6€").
        tiles, _masked = detect_amount_tiles(gray, sep1, sep2)
        label_tiles = detect_label_tiles(gray, sep1, label_x0)

    for profile_name, lang_code in profiles:
        tasks.append({"name": f"header_{profile_name}", "language": lang_code,
                      "level": level, "x0": 0.005, "y0": 0.0,
                      "x1": 0.995, "y1": 0.25})
        if label_tiles:
            for y0, y1 in label_tiles:
                tasks.append({
                    "name": f"label_{profile_name}", "language": lang_code,
                    "level": level, "x0": max(0.005, label_x0),
                    "y0": y0 / image_height,
                    "x1": max(0.01, sep1 - 0.002),
                    "y1": y1 / image_height,
                })
        else:
            tasks.append({
                "name": f"label_{profile_name}", "language": lang_code,
                "level": level, "x0": max(0.005, label_x0),
                "y0": body_y0, "x1": max(0.01, sep1 - 0.002),
                "y1": body_y1,
            })

    # Numeric columns use a dedicated Latin/digit recognizer, not a Chinese or
    # Portuguese language assumption. They are read in short row-gap-aligned
    # tiles; one full-height ROI makes Vision silently drop contiguous rows on
    # tall tables.
    numeric_x = {
        "debit": (min(0.99, sep1 + 0.002),
                  max(sep1 + 0.01, sep2 + _AMOUNT_ROI_PADDING)),
        "credit": (min(0.99, sep2 + 0.002), 0.995),
    }
    for column, (x0, x1) in numeric_x.items():
        column_tiles = tiles.get(column)
        if column_tiles:
            for y0, y1 in column_tiles:
                tasks.append({
                    "name": f"{column}_numeric", "language": "en",
                    "level": level, "x0": x0,
                    "y0": max(body_y0, y0 / image_height),
                    "x1": x1, "y1": min(body_y1, y1 / image_height),
                })
        else:
            tasks.append({
                "name": f"{column}_numeric", "language": "en",
                "level": level, "x0": x0, "y0": body_y0,
                "x1": x1, "y1": body_y1,
            })

    # Language correction is a per-region policy: text labels may benefit from
    # a lexicon, but an amount must never be "corrected" before it is stored
    # raw.
    for task in tasks:
        is_numeric = task["name"].endswith("_numeric")
        task["usesLanguageCorrection"] = (
            language_correction == "all"
            or (language_correction == "labels" and not is_numeric)
        )

    boxes, diagnostics = _invoke_vision_worker(
        image_path, tasks,
        minimum_text_height=minimum_text_height,
        preprocess=preprocess,
        level=level,
        language_correction=language_correction,
    )

    diagnostics["vision_numeric_tiles"] = sum(len(v) for v in tiles.values())
    diagnostics["vision_label_tiles"] = len(label_tiles)
    diagnostics["vision_numeric_tile_source"] = (
        "ink_gaps" if any(v for v in tiles.values()) else "full_height"
    )
    return boxes, diagnostics


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
        x1 = int(max(sep1 + 0.01, sep2 + _AMOUNT_ROI_PADDING) * width)
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
                             max_cells: int = 240) -> dict[str, Any]:
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
    # The debit cell's OCR window intentionally extends past the debit|credit
    # rule so the final digit is not clipped. Use a rule-masked copy only for
    # the ink test; the OCR crop keeps the original pixels, and trailing rule
    # residue is cleaned (and flagged) after recognition.
    try:
        ink_image = _mask_vertical_rules(image)
    except Exception:  # noqa: BLE001 - masking is best-effort
        ink_image = image
    worker = _ensure_vision_worker()
    sep1, sep2 = boundaries
    columns = {
        "debit": (min(0.99, sep1 + 0.002), max(sep1 + 0.01, sep2 + _AMOUNT_ROI_PADDING)),
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
            # Row boxes come from label/amount clusters; a couple of pixels of
            # vertical slack keeps the full glyph height in the crop.
            cell_top = max(0, top - 2)
            cell_bottom = min(height, bottom + 2)
            if x1 - x0 < 20 or cell_bottom - cell_top < 8:
                continue
            try:
                ink = _cell_ink_fraction(ink_image, x0, x1, cell_top, cell_bottom)
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


# Some Balancetes print a negative amount as a short minus rule at the far left
# of the amount cell, far from the right-aligned digits (issue 20/2025 page 35,
# 歷年營業結果: "-" at x=1124, digits start at x=1295). Vision does not report
# the rule because it is only 1–3 px tall and below minimumTextHeight, so the
# amount silently loses its sign and the printed column no longer balances.
_MARGIN_SIGN_STRIP_PX = 40
_MARGIN_SIGN_START_PX = 4
_MARGIN_SIGN_MAX_START_PX = 22
_MARGIN_SIGN_MIN_WIDTH_PX = 6
_MARGIN_SIGN_MAX_WIDTH_PX = 16
_MARGIN_SIGN_MAX_HEIGHT_PX = 3
_MARGIN_SIGN_MIN_AREA_PX = 8


def _find_margin_sign(image: Any, width: int, height: int,
                      left_frac: float, top: int, bottom: int
                      ) -> tuple[int, int, int, int] | None:
    """Return ``(x, y, w, h)`` for a minus rule at the cell's left margin."""
    import cv2
    import numpy as np

    if bottom - top < 8:
        return None
    left = int(left_frac * width)
    x0 = max(0, left + _MARGIN_SIGN_START_PX)
    x1 = min(width, left + _MARGIN_SIGN_STRIP_PX)
    if x1 - x0 < _MARGIN_SIGN_MIN_WIDTH_PX:
        return None
    y0 = max(0, top - 1)
    y1 = min(height, bottom + 1)
    strip = (image[y0:y1, x0:x1] < 160).astype(np.uint8)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(strip, 8)
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        global_y = y0 + y
        if (
            _MARGIN_SIGN_MIN_WIDTH_PX <= w <= _MARGIN_SIGN_MAX_WIDTH_PX
            and 2 <= h <= _MARGIN_SIGN_MAX_HEIGHT_PX
            and area >= _MARGIN_SIGN_MIN_AREA_PX
            and x0 + x <= left + _MARGIN_SIGN_MAX_START_PX
            and global_y - top >= 3
            and bottom - global_y >= 4
        ):
            return (int(x0 + x), int(global_y), int(w), int(h))
    return None


def apply_margin_signs(rows: list[dict[str, Any]], image_path: Path,
                       width: int, height: int,
                       boundaries: tuple[float, float]) -> dict[str, Any]:
    """Negate amounts whose cell carries a printed minus rule at its left edge.

    The raw OCR text never contains the rule, so ``*_raw`` stays untouched and
    the sign is recorded separately. Every adopted sign enters review.
    """
    diagnostics: dict[str, Any] = {
        "margin_sign_status": "off",
        "margin_sign_applied": 0,
    }
    if not rows:
        return diagnostics
    try:
        import cv2
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    except Exception:  # noqa: BLE001
        image = None
    if image is None:
        diagnostics["margin_sign_status"] = "unavailable"
        return diagnostics
    sep1, sep2 = boundaries
    applied = 0
    for row in rows:
        try:
            top = int(float(row.get("bbox_y0") or 0))
            bottom = int(float(row.get("bbox_y1") or 0))
        except (TypeError, ValueError):
            continue
        for column, left_frac in (("debit", sep1), ("credit", sep2)):
            amount = str(row.get(f"{column}_amount") or "")
            if not amount or amount.startswith("-"):
                continue
            try:
                if Decimal(amount) == 0:
                    continue
            except InvalidOperation:
                continue
            sign = _find_margin_sign(image, width, height, left_frac, top, bottom)
            if sign is None:
                continue
            row[f"{column}_amount"] = "-" + amount
            row[f"{column}_margin_sign"] = "negative"
            row[f"{column}_margin_sign_bbox"] = ",".join(str(value) for value in sign)
            status = str(row.get(f"{column}_parse_status") or "")
            row[f"{column}_parse_status"] = (
                "recovered_negative_margin"
                if status in {"", "parsed"}
                else f"{status};recovered_negative_margin"
            )
            reason = row.get("review_reason") or ""
            row["review_reason"] = ";".join(
                value for value in (reason, f"{column}:recovered_negative_margin") if value
            )
            row["review_status"] = "needs_review"
            applied += 1
    diagnostics["margin_sign_status"] = "ran"
    diagnostics["margin_sign_applied"] = applied
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
    # A cell crop can include the table rule, which Vision reads as a trailing
    # "|" or ".....". Remove only unmistakable trailing rule residue (a lone
    # decimal dot/comma is left untouched) and flag the repair for review.
    artifact = False
    trimmed = re.sub(r"(?<=[0-9])[\s]*(?:[|¦│…·]+[\s]*|[.,]{2,}[\s]*)$", "", s)
    if trimmed != s:
        s = trimmed.strip()
        artifact = True
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

    issue = "recovered_ocr_artifact" if artifact else ""
    if re.search(r"[OoIl|]", s):
        s = s.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}))
        issue = issue or "ambiguous_ocr_character"
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
        if row.get("geometry_suspect"):
            issues.append("geometry_suspect")
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


_COLUMN_HEADER_RE = re.compile(
    r"帳戶名稱|帳目名稱|科目|DESIGNA[CÇ][AÃ]O|ACCOUNT|借方|貸方|"
    r"DEVEDORES|CREDORES|SALDO|餘額",
    re.IGNORECASE,
)
_TOTAL_ROW_RE = re.compile(
    r"^(?:總|與|絛|総)額$|^合計$|^TOTAIS$|^TOTAL$", re.IGNORECASE
)
_AMOUNT_LIKE_LABEL_RE = re.compile(r"\d[\d.,\s]{4,}\d")
_CLEAN_AMOUNT_RAW_RE = re.compile(r"^[\s$]*[\(\-]?[\d.,\s]+[\)]?$")
_COLUMN_HEADER_ROW_RE = re.compile(
    r"(?:帳|账|眼|腿|腦|脳|户|戶).{0,3}名(?:稱|稍|禡|称)?"
    r"|餘額|與額|絛額|借方|貸方|SALDO|DEVEDORES|CREDORES|DESIGNA",
    re.IGNORECASE,
)


def drop_column_header_rows(rows: list[dict[str, Any]], image_height: int) -> int:
    """Remove the table's column-heading row before it becomes an account row."""
    dropped = 0
    kept: list[dict[str, Any]] = []
    for row in rows:
        if image_height > 0 and row.get("y_center", image_height) <= image_height * 0.10:
            label = norm_text(row.get("label_raw", ""))
            amount = norm_text(row.get("debit_raw") or row.get("credit_raw") or "")
            if not label and re.fullmatch(r"\d{1,4}", amount):
                # Printed page numbers and years sit in the top header band.
                dropped += 1
                continue
        if (
            dropped < 3
            and image_height > 0
            and row.get("y_center", image_height) <= image_height * 0.25
            and _COLUMN_HEADER_ROW_RE.search(norm_text(row.get("label_raw", "")))
        ):
            dropped += 1
            continue
        kept.append(row)
    if dropped:
        rows[:] = kept
    return dropped


def _table_rules_from_image(
    image_path: Path | None, image_height: int,
) -> tuple[float | None, float | None]:
    """Find the table's top/header and bottom rules from long horizontal ink."""
    if image_path is None or image_height <= 0:
        return None, None
    try:
        if not Path(image_path).is_file():
            return None, None
        import cv2
        import numpy as np
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None, None
    if image is None:
        return None, None
    h, w = image.shape[:2]
    binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(20, int(w * 0.25)), 1)
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    row_ink = (horizontal > 0).sum(axis=1)
    ys = np.where(row_ink >= w * 0.25)[0]
    if not len(ys):
        return None, None

    def group_centers(values) -> list[float]:
        if not len(values):
            return []
        groups: list[list[int]] = [[int(values[0])]]
        for y in values[1:]:
            y = int(y)
            if y - groups[-1][-1] <= 4:
                groups[-1].append(y)
            else:
                groups.append([y])
        return [sum(group) / len(group) for group in groups]

    top_groups = group_centers(ys[(ys >= h * 0.04) & (ys <= h * 0.35)])
    bottom_groups = group_centers(ys[(ys >= h * 0.60) & (ys <= h * 0.99)])
    top = None
    if top_groups:
        # The second long rule closes the column-heading band; drop that row.
        if len(top_groups) >= 2 and top_groups[1] - top_groups[0] <= h * 0.12:
            top = top_groups[1]
        else:
            top = top_groups[0]
    bottom = bottom_groups[-1] if bottom_groups else None
    return top, bottom


def filter_table_body_boxes(
    boxes: list[OCRBox], image_height: int, image_path: Path | None = None,
) -> tuple[list[OCRBox], float | None, float | None, int]:
    """Drop boxes above the column headings and below the printed total.

    The table OCR tasks start at 3.5% of the image, so a dense page header (bank
    address, telephone, signatures) can otherwise become table rows. This only
    filters row *grouping*; every OCR box stays in ``ocr_raw.csv``.
    """
    if not boxes or image_height <= 0:
        return boxes, None, None, 0
    header_hits = [
        b for b in boxes
        if b.cy <= image_height * 0.22 and _COLUMN_HEADER_RE.search(norm_text(b.text))
    ]
    body_top = max((b.y1 for b in header_hits), default=None)
    image_top, image_bottom = _table_rules_from_image(image_path, image_height)
    if body_top is None:
        body_top = image_top
    total_hits = [
        b for b in boxes
        if _TOTAL_ROW_RE.match(norm_text(b.text))
    ]
    body_bottom = None
    if total_hits:
        total_row = max(total_hits, key=lambda b: b.cy)
        heights = [b.height for b in boxes]
        tolerance = max(15.0, (statistics.median(heights) if heights else 20.0) * 1.5)
        row_boxes = [b for b in boxes if abs(b.cy - total_row.cy) <= tolerance]
        body_bottom = max(b.y1 for b in row_boxes)
    if image_bottom is not None and image_bottom > (body_top or 0.0):
        body_bottom = max(body_bottom or 0.0, image_bottom)
    if body_top is None and body_bottom is None:
        return boxes, None, None, 0
    kept = [
        b for b in boxes
        if (body_top is None or b.cy > body_top)
        and (body_bottom is None or b.cy <= body_bottom)
    ]
    if not kept:
        return boxes, body_top, body_bottom, 0
    return kept, body_top, body_bottom, len(boxes) - len(kept)


def _is_column_rule_artifact(box: OCRBox, image_width: int) -> bool:
    """True for a narrow single-character reading of a table rule."""
    text = (box.text or "").strip()
    if len(text) != 1 or text.isdigit() or text in {"-", "—", "–"}:
        return False
    return (box.x1 - box.x0) <= max(4.0, image_width * 0.006)


def recover_header_region_table_boxes(
    table_boxes: list[OCRBox],
    header_boxes: list[OCRBox],
    body_top: float,
    min_x: float = 0.0,
) -> list[OCRBox]:
    """Add header-ROI readings that lie inside the table, without duplicates.

    The fixed 25% header ROI overlaps tables that start high on the page. A
    numeric cell missed by the table pass can still exist only in the header
    pass, so it is recovered; readings that already exist in the table pass are
    left alone. Only boxes right of the label column are considered, because
    account-code boxes in the header pass would otherwise be glued onto labels
    (issue 45 page 29).
    """
    recovered: list[OCRBox] = []
    for box in header_boxes:
        if box.cy <= body_top or box.cx < min_x:
            continue
        text = norm_text(box.text)
        # Only recover monetary readings; page numbers, dates and headings are
        # also present in the 25% header band.
        if not (
            _CLEAN_AMOUNT_RAW_RE.match(text)
            and re.search(r"[.,]\s*\d{2}$", text)
        ):
            continue
        duplicate = any(
            _box_iou(box, existing) >= 0.40
            or (
                norm_text(box.text) == norm_text(existing.text)
                and abs(box.cy - existing.cy) <= max(8.0, box.height * 0.60)
            )
            for existing in table_boxes + recovered
        )
        if not duplicate:
            recovered.append(box)
    return table_boxes + recovered


def mark_geometry_suspect_rows(rows: list[dict[str, Any]]) -> int:
    """Flag rows where the label cell swallowed an amount.

    A wrong label|debit boundary can leave every amount inside ``label_raw``
    while ``debit_raw``/``credit_raw`` stay empty. Those rows are dangerous
    because the amount has disappeared without a parse error, so they must not
    silently auto-pass.
    """
    flagged = 0
    for row in rows:
        if row.get("debit_raw") or row.get("credit_raw"):
            continue
        label = norm_text(row.get("label_raw", ""))
        if not label or not _AMOUNT_LIKE_LABEL_RE.search(label):
            continue
        row["geometry_suspect"] = True
        flagged += 1
    return flagged


def apply_confidence_review_gate(
    rows: list[dict[str, Any]], confidence_threshold: float,
) -> int:
    """Auto-pass low-confidence rows only when an exact lexicon label proves it.

    Apple Vision returns 0.3/0.5 confidence for many correct Traditional Chinese
    labels. The old single-threshold gate sent every such row to review even when
    the label matched the curated lexicon exactly and the amounts parsed cleanly.
    Raw confidence and the row's original reasons are preserved in other columns;
    ``review_confidence_waived`` records why the warning was removed.
    """
    waived = 0
    clean_status = {"parsed", "blank", "dash", ""}
    for row in rows:
        row["review_confidence_waived"] = ""
        reasons = [x for x in (row.get("review_reason") or "").split(";") if x]
        if reasons != ["low_ocr_confidence"]:
            continue
        if row.get("label_standard_status") != "exact":
            continue
        try:
            if float(row.get("label_standard_score") or 0.0) < 1.0:
                continue
        except (TypeError, ValueError):
            continue
        if not norm_text(row.get("label_raw", "")):
            continue
        amount_ok = True
        for column in ("debit", "credit"):
            if (row.get(f"{column}_parse_status") or "") not in clean_status:
                amount_ok = False
                break
            if not (row.get(f"{column}_raw") or "").strip():
                continue
            raw = (row.get(f"{column}_raw") or "").strip()
            if not _CLEAN_AMOUNT_RAW_RE.match(raw):
                amount_ok = False
                break
            try:
                column_confidence = float(
                    row.get(f"{column}_confidence") or 1.0
                )
            except (TypeError, ValueError):
                amount_ok = False
                break
            if column_confidence < confidence_threshold:
                amount_ok = False
                break
        if not amount_ok:
            continue
        row["review_reason"] = ""
        row["review_status"] = "auto_check_pass"
        row["review_confidence_waived"] = "label_exact"
        waived += 1
    return waived


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


class PageCoverageError(ValueError):
    """Raised when full-page records do not cover the PDF exactly once."""


def _safe_page_text(doc: fitz.Document, page_index: int) -> str:
    try:
        return doc[page_index].get_text("text")
    except Exception:
        return ""


def _safe_page_meta(page_text: str, page_number: int) -> dict[str, Any]:
    try:
        return extract_metadata(page_text or "", page_number)
    except Exception:
        return {
            "pdf_page": page_number,
            "printed_page": "",
            "bank_name": "",
            "statement_date": "",
            "publication_date": "",
            "bank_name_source": "unavailable",
        }


def make_page_record(
    page_text: str,
    page_number: int,
    *,
    status: str,
    skip_reason: str = "",
    error_message: str = "",
    is_tb_page: bool = False,
) -> dict[str, Any]:
    record = _safe_page_meta(page_text, page_number)
    record.update({
        "ocr_status": status,
        "skip_reason": skip_reason,
        "error_message": error_message,
        "is_tb_page": is_tb_page,
        "metadata_status": (
            "not_applicable" if status == "skipped"
            else "ocr_failed" if status == "failed"
            else record.get("metadata_status", "")
        ),
    })
    if status == "skipped":
        record["bank_name"] = ""
        record["statement_date"] = ""
        record["bank_name_source"] = "not_applicable"
    return record


def finalize_full_page_records(
    records: list[dict[str, Any]],
    doc: fitz.Document,
    page_start: int,
    page_end: int,
    failure: tuple[int, bool, Exception] | None = None,
) -> list[dict[str, Any]]:
    """Fill every missing page record and sort the result by pdf_page."""
    by_page: dict[int, dict[str, Any]] = {}
    for row in records:
        try:
            page_number = int(row["pdf_page"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PageCoverageError(f"invalid pdf_page in page record: {row!r}") from exc
        if page_number in by_page:
            raise PageCoverageError(f"duplicate page record for page {page_number}")
        if not (page_start < page_number <= page_end):
            raise PageCoverageError(
                f"page {page_number} is outside the requested range "
                f"{page_start + 1}-{page_end}"
            )
        by_page[page_number] = row

    failure_index, failure_is_tb, failure_exc = failure or (None, False, None)
    for page_index in range(page_start, page_end):
        page_number = page_index + 1
        if page_number in by_page:
            if failure_index == page_index and failure_exc is not None:
                by_page[page_number].update({
                    "ocr_status": "failed",
                    "skip_reason": "",
                    "error_message": f"{type(failure_exc).__name__}: {failure_exc}"[:500],
                    "is_tb_page": bool(failure_is_tb),
                    "metadata_status": "ocr_failed",
                })
            continue

        page_text = _safe_page_text(doc, page_index)
        if failure_index == page_index and failure_exc is not None:
            record = make_page_record(
                page_text, page_number,
                status="failed",
                error_message=f"{type(failure_exc).__name__}: {failure_exc}"[:500],
                is_tb_page=bool(failure_is_tb),
            )
        elif failure_index is not None and page_index > failure_index:
            record = make_page_record(
                page_text, page_number,
                status="skipped",
                skip_reason="not_processed_after_failure",
            )
        else:
            record = make_page_record(
                page_text, page_number,
                status="skipped",
                skip_reason="not_tb_page",
            )
        records.append(record)
        by_page[page_number] = record

    records.sort(key=lambda row: int(row["pdf_page"]))
    return records


def validate_page_records(records: list[dict[str, Any]], pdf_pages: int) -> None:
    pages: list[int] = []
    for row in records:
        try:
            pages.append(int(row["pdf_page"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise PageCoverageError(f"invalid pdf_page in page record: {row!r}") from exc
    if len(pages) != len(set(pages)):
        raise PageCoverageError("duplicate pdf_page records")
    expected = list(range(1, pdf_pages + 1))
    if pages != expected:
        raise PageCoverageError(
            f"page records do not cover 1-{pdf_pages}: got {pages[:5]}"
            + ("..." if len(pages) > 5 else "")
        )


def process_pdf(args: argparse.Namespace) -> dict[str, Any]:
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 不存在：{pdf_path}")
    # Capture provenance before OCR. Hashing at import time is unsafe if the
    # file is later moved or replaced.
    source_pdf_sha256 = sha256_file(pdf_path)
    source_pdf_size = pdf_path.stat().st_size
    out_dir = Path(args.outdir).expanduser().resolve()
    images_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    engines: dict[str, Any] = {}
    document_rows: list[dict[str, Any]] = []
    tb_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    page_start, page_end = parse_page_range(args.pages, len(doc))
    if args.full_page_records:
        if args.inspect_only:
            raise ValueError("--full-page-records cannot be combined with --inspect-only")
        if args.pages.strip().lower() != "all":
            raise ValueError("--full-page-records requires --pages all")
        if len(doc) == 0:
            raise ValueError("--full-page-records requires at least one PDF page")

    current_page_index: int | None = None
    current_page_is_tb = False
    page_failure: tuple[int, bool, Exception] | None = None
    page_tb_start = 0
    page_raw_start = 0

    try:
        for page_index in range(page_start, page_end):
            current_page_index = page_index
            current_page_is_tb = False
            page_tb_start = len(tb_rows)
            page_raw_start = len(raw_rows)
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
            page_is_tb = is_tb_page(page_text, bool(large_images))
            current_page_is_tb = page_is_tb
            if not page_is_tb:
                if args.full_page_records:
                    document_rows.append(make_page_record(
                        page_text,
                        page_index + 1,
                        status="skipped",
                        skip_reason="not_tb_page",
                    ))
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
            if args.full_page_records:
                docs_record.update({
                    "skip_reason": "",
                    "error_message": "",
                    "is_tb_page": True,
                })
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
            table_boxes, table_body_top, table_body_bottom, table_body_filtered = (
                filter_table_body_boxes(table_boxes, table_image.height, image_path)
            )
            if table_body_top is not None:
                # The fixed 25% header ROI overlaps tables that start high on
                # the page. Recover header-region readings below the table top
                # so those first rows are not lost from the body.
                merged_boxes = recover_header_region_table_boxes(
                    table_boxes, header_boxes, table_body_top,
                    min_x=b1 * table_image.width,
                )
                if len(merged_boxes) != len(table_boxes):
                    table_boxes, table_body_top, table_body_bottom, table_body_filtered = (
                        filter_table_body_boxes(
                            merged_boxes,
                            table_image.height,
                            image_path,
                        )
                    )
            table_boxes = [
                b for b in table_boxes
                if not _is_column_rule_artifact(b, table_image.width)
            ]
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
            column_header_rows_dropped = drop_column_header_rows(
                rows, table_image.height
            )
            geometry_suspect_rows = mark_geometry_suspect_rows(rows)
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
            margin_sign_diagnostics = apply_margin_signs(
                rows, image_path, table_image.width, table_image.height, (b1, b2),
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
                    label_diagnostics["review_confidence_waived"] = (
                        apply_confidence_review_gate(rows, args.confidence_threshold)
                    )
                else:
                    label_diagnostics = {"label_standard_status": "lexicon_missing"}
            if rows:
                document_rows[-1]["number_format_detected"] = rows[0].get("number_format", "")
                document_rows[-1]["number_format_confidence"] = rows[0].get("number_format_confidence", "")
                document_rows[-1]["number_format_evidence"] = rows[0].get("number_format_evidence", "")
            document_rows[-1].update(vision_diagnostics)
            document_rows[-1].update(verifier_diagnostics)
            document_rows[-1].update(second_pass_diagnostics)
            document_rows[-1].update(margin_sign_diagnostics)
            document_rows[-1].update(label_diagnostics)
            document_rows[-1]["table_body_top"] = (
                round(table_body_top, 2) if table_body_top is not None else ""
            )
            document_rows[-1]["table_body_bottom"] = (
                round(table_body_bottom, 2) if table_body_bottom is not None else ""
            )
            document_rows[-1]["table_body_filtered_boxes"] = table_body_filtered
            document_rows[-1]["column_header_rows_dropped"] = column_header_rows_dropped
            document_rows[-1]["geometry_suspect_rows"] = geometry_suspect_rows
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
    except Exception as exc:
        if not args.full_page_records:
            raise
        failure_page = current_page_index if current_page_index is not None else page_start
        page_failure = (failure_page, current_page_is_tb, exc)
        # Do not keep partial evidence from the failed page.
        del tb_rows[page_tb_start:]
        del raw_rows[page_raw_start:]

    if args.full_page_records:
        document_rows = finalize_full_page_records(
            document_rows, doc, page_start, page_end, page_failure
        )
        validate_page_records(document_rows, len(doc))

    tb_pages_detected = (
        sum(1 for row in document_rows if row.get("is_tb_page") is True)
        if args.full_page_records else len(document_rows)
    )

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
        "source_pdf_sha256": source_pdf_sha256,
        "source_pdf_size": source_pdf_size,
        "pdf_pages": len(doc),
        "tb_pages_detected": tb_pages_detected,
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
    if args.full_page_records:
        report.update({
            "full_page_records": True,
            "page_records": len(document_rows),
            "ocr_pages_observed": len(document_rows),
            "ocr_completed_pages": sum(
                1 for row in document_rows if row.get("ocr_status") == "completed"
            ),
            "ocr_skipped_pages": sum(
                1 for row in document_rows if row.get("ocr_status") == "skipped"
            ),
            "ocr_failed_pages": sum(
                1 for row in document_rows if row.get("ocr_status") == "failed"
            ),
            "ocr_page_coverage": "full",
        })
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
    p = argparse.ArgumentParser(description="澳門公報 Balancete OCR / Excel / CSV prototype v0.7.0")
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
    p.add_argument(
        "--full-page-records",
        action="store_true",
        help="每頁寫入一條 documents.csv 記錄（V3 coverage）；需要 --pages all",
    )
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
