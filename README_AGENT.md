# AI Agent Quick Start — Macau Gazette Balancete OCR v0.3.1

Read `AGENTS.md` first, then this file and `SKILL.md` before processing any PDF. macOS setup is in `MACOS_SETUP.md`.

## Purpose

Extract bank/financial-institution Balancete pages from Macau Government Gazette PDFs into Excel/CSV. Preserve the original image and raw OCR values, normalize account-label script and numeric values into separate fields, and route uncertain rows to human review.

## v0.3 changes

- Standard-label lexicon (`labels/label_lexicon.csv`) maps OCR names to `label_standard`; `label_raw` is never overwritten. Fuzzy 0.70–0.90 mappings are reviewed.
- Parent/child hierarchy for `-澳門元`, `-外幣`, `- Patacas` and dash-less sub-items (`label_role`, `parent_label_standard`, `label_path`).
- Wrapped labels are merged into `label_combined_raw` / `label_standard` only when the combined text extends the previous standard.
- Ground-truth tooling and true CER (total edits ÷ total GT characters); see `ground_truth/README.md`.

## v0.2 changes

- macOS default: native Apple Vision (`--engine vision`); no PaddlePaddle/PaddleOCR installation required.
- OCR is region-based: header, account label, debit, credit. Chinese uses `zh-Hant`; Portuguese uses an available `pt-*` locale; amount columns use English/digits.
- Simplified-to-HK-Traditional conversion is written to `label_normalized`; `label_raw` remains unchanged.
- Numeric punctuation is inferred from statement values, never from the document language. `comma_dot` and `dot_comma` are supported. Recoverable OCR separator substitutions are still flagged for review.
- PaddleOCR and Tesseract are optional alternatives.

## Setup (M1/M2/M3/M4 Mac)

```bash
xcode-select --install   # only if swiftc is missing
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

Check Vision language availability:

```bash
swiftc -O vision_ocr_worker.swift -o /tmp/macau_tb_vision_worker
/tmp/macau_tb_vision_worker --list-languages | grep -E 'zh-Hant|pt-|en-'
```

Do not silently substitute `zh-Hans` if `zh-Hant` is unavailable.

## Standard runbook

### 1. Inspect first

```bash
python macau_tb_ocr.py /path/to/input.pdf \
  --outdir output_inspect_YYYYMMDD --inspect-only
```

Review `documents.csv`, page ranges, embedded image dimensions, metadata, and sample images.

### 2. Chinese and Portuguese smoke tests on macOS

```bash
python macau_tb_ocr.py /path/to/input.pdf --outdir output_p07 \
  --pages 7 --engine vision --vision-level accurate --ocr-langs auto

python macau_tb_ocr.py /path/to/input.pdf --outdir output_p10 \
  --pages 10 --engine vision --vision-level accurate --ocr-langs auto
```

Use page numbers confirmed by the current PDF's inspection; 7 and 10 are only the known examples from issue 45 of 2026.

To compare speed, repeat one page with `--vision-level fast` and a different output directory. Do not compare confidence scores between engines as if they were calibrated.

### 3. Batch after sample review

```bash
python macau_tb_ocr.py /path/to/input.pdf \
  --outdir output_full_YYYYMMDD --pages 7-32 \
  --engine vision --vision-level accurate --ocr-langs auto
```

Replace `7-32` with the confirmed TB page range.

## Numeric-format behaviour

The parser infers the page-level format from OCR numeric evidence:

- `comma_dot`: `1,234,567.89`
- `dot_comma`: `1.234.567,89`

It does not assume Portuguese means `dot_comma`. In issue 45, both Chinese and Portuguese sample TBs visibly use comma thousands and dot decimals.

Examples of OCR separator recovery under a confirmed `comma_dot` page profile:

- `119.366,280.10` → `119366280.10`
- `2.496,722,065.08` → `2496722065.08`
- `405.228,717.57` → `405228717.57`
- `65.045.622.601.52` → `65045622601.52`
- `14.702,016,826,10` → `14702016826.10`
- `5,083,140,620 78` → `5083140620.78`

These values are recovered candidates, not silently approved financial data. Their parse status is `recovered_ocr_separator` and the row remains in `review_queue.csv`.

## Important columns

- `label_raw`: unmodified OCR account text.
- `label_normalized`: OpenCC `s2hk` converted label (separate from raw).
- `label_standard`, `label_standard_status`, `label_standard_score`: mapped standard label from `labels/label_lexicon.csv`; never overwrites raw.
- `label_role`, `parent_label_raw`, `parent_label_standard`, `label_path`: parent/child structure for dash sub-items.
- `label_combined_raw`, `label_continuation_raw`: wrapped-label detection; flagged for review.
- `debit_raw`, `credit_raw`: exact OCR strings.
- `debit_amount`, `credit_amount`: canonical values (dot decimal separator, no grouping marks).
- `number_format`: page-level inferred or explicitly overridden format.
- `number_format_confidence`, `number_format_evidence`: evidence supporting the page profile.
- `debit_parse_status`, `credit_parse_status`: `parsed`, `recovered_ocr_separator`, `ambiguous_format`, `invalid_grouping`, `ambiguous_ocr_character`, etc.
- `review_reason`, `review_status`: automatic validation results; `auto_check_pass` is not financial verification.
- `source_image`, PDF page, bounding box and OCR raw evidence: audit trail.

## Useful CLI options

```text
--engine vision|paddle|tesseract
--vision-level fast|accurate
--vision-minimum-text-height 0.004
--vision-preprocess on|off
--vision-language-correction off|labels|all
--amount-verifier off|tesseract
--amount-second-pass ink|off
--label-lexicon labels/label_lexicon.csv
--no-label-standardization
--row-structure clustered|auto|projection
--ocr-langs auto|chinese_cht,pt,en
--number-format auto|comma_dot|dot_comma
--chinese-label-normalization off|s2t|s2hk
--no-repair-ocr-separators
--confidence-threshold 0.88
--inspect-only
```

On macOS, engine defaults to Vision. On non-macOS, it defaults to Paddle. Paddle is optional and requires a separately installed PaddlePaddle backend plus `requirements-paddle.txt`.

## Ground truth benchmark

Do not claim OCR accuracy or a speed improvement without a human-verified benchmark. Build the worksheet from a finished run, verify every row against the image/crops, then score:

```bash
python tools/gt_build_sheet.py output_v02_issue45_full_1900 \
  --out ground_truth --issue issue45_2026 --pages 7,10
# human step: correct label/debit/credit in gt_rows.csv, set verified=1
python tools/gt_score.py ground_truth/issue45_2026 --run output_v03_issue45
```

Only `verified=1` rows are scored. `ground_truth/README.md` has the full rules and metric definitions. Never treat the OCR pre-filled worksheet as ground truth, and never overwrite a verified sheet with `--force`.

## Success criteria

A run is complete only when:
1. The detected pages match the inspected manifest.
2. Every page has its embedded source image.
3. The OCR output and row alignment are sampled visually.
4. `number_format` is supported by visible numeric evidence or an explicit operator override.
5. All separator-recovered, ambiguous-format, low-confidence, blank-label and fallback-boundary rows are reviewed or marked pending.
6. The run report distinguishes extracted, normalized, recovered, and human-verified data.

Never claim accuracy or improved speed without a manually checked ground-truth sample on the target Mac.
