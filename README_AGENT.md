# AI Agent Quick Start — Macau Gazette Balancete OCR v0.7.0

Read `AGENTS.md` first, then this file and `SKILL.md` before processing any PDF. macOS setup is in `MACOS_SETUP.md`; name registry workflow is in `NAME_STANDARDIZATION.md`. Layout / archive map: `PROJECT_LAYOUT.md`. Active V3 docs: `docs/README.md`.

## Purpose

Extract bank/financial-institution Balancete pages from Macau Government Gazette PDFs into Excel/CSV. Preserve the original image and raw OCR values, normalize account-label script and numeric values into separate fields, and route uncertain rows to human review. Operational review / adoption is on the V3 database and workbench; legacy V5 SQLite is frozen.

## v0.7.0 changes

- Added `tools/import_issue.py`: PDF URL → input → OCR → output → SQLite → bank refresh → user-standard remap.
- Existing PDFs/output dirs are never overwritten without explicit flags; new issues are staging until `--publish`.
- Supports `--download-only`, `--inspect-only`, `--dry-run`, `--no-db`, `--no-ocr`, `--no-name-map`, `--no-bank-refresh`.
- Verified with issue 33 (1 TB page, 64 rows, 32 review, 64/64 standard-mapped).

## v0.6.4 changes

- Imported 2026 issue 31: 11 pages / 713 rows, 506 review (low OCR confidence dominates).
- User standard mapping: 700/713 rows mapped; 13 unmapped.
- OCR repair aliases now take priority over a wrong non-empty `label_standard`.
- Added a bank alias for the 國泰海通 former-name variant; existing statement still points at the old bank entity until a rebuild/remap.

## v0.6.3 changes

- Support Portuguese-only parent standards (`user:pt:...`) for the TESOURO PÚBLICO rows.
- Split the combined `一般監管儲備特定監管儲備投資重估儲備` cell into three standards.
- Normalize `Reject` to rejected and skip rejected rows entirely.
- Correct 負價→負債 and 負償→負債; standards now 88 (85 approved, 3 provisional); 31 rows / 20 distinct unmapped.

## v0.6.2 changes

- Added `labels/user_standard_ocr_repairs.csv` with 32 high-confidence OCR typo / merged-string aliases.
- Repairs are applied after standard import and before remapping; they do not create new standards.
- Unmapped reduced from 89 rows to 50 rows (38 distinct); ambiguous and genuinely new accounts stay visible.

## v0.6.1 changes

- `import_user_standard.py build --from-cleaned` reads the human-edited `labels/standard_label_by_user.cleaned.csv` directly.
- Raw-label fallback maps rows whose `label_standard` is empty via `label_raw`.
- User-added standards (銀行貸款, 銀行結存, 銀行透支, 保留溢利, 預付款項, 總公司/關連公司欠款, 儲備) are now approved.
- Unmapped fell from 163 rows to 89 rows (70 distinct).

## v0.6.0 changes

- Added `tools/import_user_standard.py` for the user's parent/child standard CSV. It writes `standard_labels`, `standard_label_aliases` and `line_items.user_standard_label_id` (schema v5).
- The importer corrects the shifted reserve block, AMCM parent_pt, Greek Μ, 賬/帳 and 總收人; corrected rows are `provisional`.
- Mapping report: `data/user_standard_mapping_report.csv` (2198 mapped, 163 unmapped at import time).
- Existing OCR raw fields and the legacy concept registry are untouched.

## v0.5.2 changes

- Added `tools/standard_line_items.py` (`export` / `apply`) and the draft `labels/line_item_standard.csv`.
- `labels/line_item_pt_zh_map.csv` holds curated pt→zh proposals; 55 merge keys are present in the draft.
- `apply` only applies `status=approved` rows by default; provisional rows are skipped.
- Once a standard master is loaded, unmatched OCR labels become `needs_new_standard` reviews; no auto concepts.

## v0.5.1 changes

- `name_registry.py export` / `import-review` provide a CSV review workflow (`data/name_review_queue.csv`).
- `labels/bank_name_translations.csv` proposes Chinese names for Latin-only banks; `banks.canonical_zh` is filled and a pending `translation_seed` review is created.
- Added the bank / line-item name standardization registry (`tools/name_registry.py`).
- Same-language lexicon aliases are approved seeds; cross-language bilingual pairs become pending reviews, never automatic merges.
- Only approved aliases populate `line_items.line_item_concept_id`; raw OCR fields stay untouched.
- Use `build`, `list`, `approve`, `reject` and `remap`; see `NAME_STANDARDIZATION.md`.

## v0.4.2 changes

- `--publish` now refuses a run whose TB page set does not cover the currently published run for the same source PDF; `--force-publish` is explicit.
- Publish identity prefers `source_pdf_sha256` over path, so the same PDF copied elsewhere still supersedes the old published run.
- OCR records `source_pdf_sha256` / `source_pdf_size` in `run_report.json`; the importer prefers the OCR-time hash and records `source_pdf_sha256_source = ocr_report|import_time|missing`. A changed PDF produces a warning.
- SQLite adds CHECK/trigger validation for `is_published`, `natural_key_status` and `source_pdf_sha256_source`; every importer connection enables foreign keys.
- Docker is PaddleOCR-only; Apple Vision remains macOS-native.

## v0.4.1 changes

- `--replace` snapshots `row_verification` and restores it only when `statement_id + row_order` and the OCR fingerprint match. A mismatch rolls back; `--discard-verification` is the explicit destructive escape hatch.
- `runs.is_published` plus `--publish` separate staging imports from the current dataset. `v_current_*` prefers published runs; `v_published_*` is the strict published view.
- `v_statement_index` reports `debit_verified_cents` / `credit_verified_cents` separately from all-row totals.
- Run IDs use full SHA-256 over the source PDF bytes, CSV outputs and `run_report.json`. Schema migrations now run automatically (schema v5).
- `tb_pages` is the page count, not the statement count. Alias drift on an already imported run emits a warning.
- `VERSION` is the project version source of truth; `SCHEMA_VERSION` is independent.

## v0.4 changes

- `tools/build_database.py` imports one or more run folders into SQLite. Every run is kept; `v_current_*` views select the newest import per `paper_key`.
- The proposed five-column key (publication date, statement date, bank, parent label, label) is a business key, not a primary key. Labels repeat, blank labels exist, and one statement can span pages. The database uses `statement_id + label_path + occurrence` as the natural key and exposes duplicates in `v_natural_key_duplicates`.
- Missing `publication_date` is derived only when all non-empty dates in the same run agree (`publication_date_source = derived_from_run`). Conflicting dates stay blank and produce a warning.
- Bank names are folded into `bank_key`; an optional `alias,canonical,bank_key,note` CSV merges verified Chinese/Portuguese variants.
- `--header-fallback auto` (default) renders the full PDF page header at 4x and OCRs it only when the embedded table image has no bank name or statement date. It never overwrites an existing value; evidence is in `header_fallback_*` columns and `images/page_XXXX_header_fallback.png`. Issue-45 page 17 recovers `郵政儲金局 | CAIXA ECONÓMICA POSTAL`.
- Human verification lives in `row_verification`; `auto_check_pass` is never financial verification.
- Read `DATABASE.md` before writing SQL or adding schema fields.

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

### 4. Integrate runs into SQLite

After individual runs are inspected:

```bash
python tools/build_database.py \
  outputs/runs/issue45_2026 outputs/runs/issue44_2026 \
  --db data/macau_balancete.sqlite \
  --publish --published-by reviewer
```

Imports are staging observations until `--publish` marks a run as the current
dataset for its source PDF. Use `--replace` only when you intend to replace an
already-imported run; it preserves `row_verification` only when the row identity
and OCR fingerprint match. Read the generated `*_import_report.json`, then check
`v_statement_index`, `v_run_index` and `v_natural_key_duplicates` before
analysis. Do not treat `auto_check_pass` as verified financial data.

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
--header-fallback auto|off
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

Database importer options:

```text
--db data/macau_balancete.sqlite
--replace
--discard-verification
--publish
--force-publish
--published-by NAME
--bank-aliases labels/bank_aliases.csv
--report PATH
```

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
7. If a SQLite import was run, `*_import_report.json` was reviewed; duplicate/missing labels, missing bank/date identity and `--replace` decisions are explicit.
8. Formal analysis uses `v_published_*`; staging imports are not treated as the current dataset until `--publish`.

Never claim accuracy or improved speed without a manually checked ground-truth sample on the target Mac.
