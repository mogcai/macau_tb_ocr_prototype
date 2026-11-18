# Agent Skill: Macau Gazette Balancete OCR v0.4.2

## Purpose

Process Macau Government Gazette PDFs and extract bank / financial-institution Balancete (trial balance / balance of ledger) tables into auditable Excel and CSV files. This project is designed around high-resolution table images embedded in the PDF, mixed Traditional Chinese / Portuguese / English content, and financial amounts where punctuation errors can materially change values.

## 1. Read these files first

- `AGENTS.md`: mandatory agent behaviour and non-negotiable integrity rules.
- `README_AGENT.md`: concise operational runbook.
- `README_zh_HK.md`: human-readable installation and usage guide.
- `MACOS_SETUP.md`: Apple Silicon / macOS installation instructions.
- `MACOS_VISION_OCR.md`: Apple Vision architecture, ROI and language constraints.

## 2. Architecture (v0.4.2)

### Input and extraction

1. Input is a Gazette PDF; do not modify the source.
2. PyMuPDF inspects page text and embedded images.
3. Large embedded images are extracted by PDF image xref. Render the full PDF page only as a fallback.
4. `--inspect-only` creates a manifest without running OCR. Always use it on a new issue and visually inspect sample images before batch processing.

### OCR engines

- On macOS, default engine is `vision` (Apple Vision `VNRecognizeTextRequest`).
- On non-macOS, default engine is `paddle`.
- Optional engines are `paddle` and `tesseract`.
- Vision does not require PaddlePaddle, PaddleOCR, PyObjC, a cloud service or an API key. It uses the macOS Vision framework through `vision_ocr_worker.swift`; the worker is compiled to `.vision_cache/` on first run.

### Vision region strategy

Before OCR, Python detects the two vertical separators for Account / Debit / Credit. The Swift worker receives one image and a set of region tasks in a single process invocation per page. Some Balancetes add a leading account-code column (issue 45 page 29); the layout step takes the **two rightmost** rules as the debit/credit separators and trims the label ROI at the code/label rule, so codes are never treated as account labels.

- Header region: page image top 25%, for OCR metadata fallback.
- Full-page header fallback: if the embedded table image still has no bank name or statement date, `--header-fallback auto` renders the top 25% of the full PDF page at 4x and OCRs that separately. This covers logos above the table crop (issue-45 page 17). Recovered values are evidence, not verification: `bank_name_source = ocr_full_page_header`, `header_fallback_*` columns, `images/page_XXXX_header_fallback.png`, and raw `header_fallback` boxes.
- Account-label column: selected page language (`zh-Hant`, Portuguese, or English).
- Debit column: English/digit recognition.
- Credit column: English/digit recognition.

For bilingual pages, separate header/label tasks may run for both Chinese and Portuguese. Numeric column recognition is separated from Chinese/Portuguese label recognition. The worker resolves locale identifiers from the languages actually supported by the installed macOS Vision revision. It must refuse to fall back from `zh-Hant` to `zh-Hans`.

Vision `fast` and `accurate` modes are available. `accurate` is the default to protect small financial text; benchmark `fast` separately on the same pages. Vision `fast` has no Traditional Chinese recognizer, so any `zh-Hant` task is always executed at `accurate` regardless of `--vision-level`; the requested and actually used levels are both recorded (`vision_level` vs `vision_levels_used`). The default minimum text height is 0.004 relative to image height. Raising it may improve speed but can omit small rows.

The ROI mapping uses Apple's Vision coordinate-conversion API; it must still be visually smoke-tested on the user's Mac with pages 7 and 10, since the Vision worker cannot be executed in a Linux build environment. Do not claim that ROI coordinate mapping or Vision performance has been verified in a Linux environment.

Experimental helpers are available but off by default: `--amount-verifier tesseract` (digit-only second reading kept as evidence; disagreements are only escalated when Vision's parse already failed) and `--row-structure auto|projection` (ink-projection row bands with a guard). On issue 45 both left row counts and blank-label counts unchanged, so they are opt-in and not evidence of higher accuracy.

Amount cells that are empty, unparsed, ambiguous or low-confidence **and contain ink** get one targeted re-read at 3x (`--amount-second-pass ink`, default). The first reading stays in `*_raw`; the re-read is stored in `*_second_pass_raw`. A re-read is adopted only when the first reading was empty/unparsed, or when a low-confidence first reading disagrees with a clean re-read; adopted values are marked `recovered_second_pass` and always enter review. On issue 45 this fixed all page-15 amounts with no change on the other benchmark pages.

## 3. Environment setup

### Apple Silicon macOS (recommended target)

```bash
uname -m
python3 -c "import platform,sys; print(platform.machine(), sys.executable, platform.python_version())"
swift --version
```

Expected native architecture: `arm64`. If `swiftc` is not present:

```bash
xcode-select --install
```

Install Python requirements:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

`requirements.txt` provides PyMuPDF, OpenCV, NumPy, OpenPyXL, Pillow and OpenCC. It intentionally excludes PaddlePaddle and PaddleOCR.

Check supported Vision languages:

```bash
swiftc -O vision_ocr_worker.swift -o /tmp/macau_tb_vision_worker
/tmp/macau_tb_vision_worker --list-languages
```

Check for `zh-Hant`, a Portuguese locale (`pt-PT` / `pt-BR` / other `pt-*` as available), and an English locale (`en-US` or other `en-*`). The Vision worker must not silently substitute Simplified Chinese. If the Traditional Chinese language is unavailable, stop and report the exact supported language list.

### Optional Paddle fallback

Install a platform-compatible PaddlePaddle backend separately, then:

```bash
python -m pip install -r requirements-paddle.txt
```

On Apple Silicon, use the compatible macOS ARM64 CPU wheel and `--device cpu`. Do not assume Paddle GPU is available on macOS. Refer to `MACOS_SETUP.md` for platform notes.

### Optional Tesseract

Install the Tesseract executable and appropriate language data (Traditional Chinese, Portuguese, English), then:

```bash
python -m pip install -r requirements-tesseract.txt
```

## 4. Standard runbook

### Step A — validate environment and CLI

```bash
python --version
python -m pip show pymupdf opencv-python-headless openpyxl opencc-python-reimplemented
python macau_tb_ocr.py --help
python -m unittest discover -s tests -v
```

On macOS, also verify `swiftc` and supported Vision languages before OCR.

### Step B — inspect new PDF first

```bash
python macau_tb_ocr.py /path/to/input.pdf \
  --outdir output_inspect_YYYYMMDD --inspect-only
```

Review:
- `documents.csv`: all detected TB pages, bank/date metadata, dimensions and embedded-image status.
- `images/page_XXXX_table.png`: visually confirm one sample Chinese page, one Portuguese page and any unusual layout.
- `run_report.json`: detected page count and source PDF details.

Do not reuse issue-45 page numbers for a new issue without inspection.

### Step C — smoke test Chinese and Portuguese

For issue 45 of 2026, examples are PDF page 7 (Traditional Chinese) and PDF page 10 (Portuguese):

```bash
python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_v02_p07 --pages 7 \
  --engine vision --vision-level accurate --ocr-langs auto

python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_v02_p10 --pages 10 \
  --engine vision --vision-level accurate --ocr-langs auto
```

Inspect output rows against the source images. For speed comparison, rerun a page with `--vision-level fast` to a separate output directory. Record elapsed time and manual exact-match results; do not infer performance from assumptions.

### Step D — batch OCR

Only after the smoke tests are visually sampled:

```bash
python macau_tb_ocr.py /path/to/input.pdf \
  --outdir output_full_YYYYMMDD --pages 7-32 \
  --engine vision --vision-level accurate --ocr-langs auto
```

Replace the page range with the current issue's inspected range. Use a unique output directory for every run.

### Step E — verify outputs

Expected outputs:

- `macau_balancete.xlsx`: sheets `TB_Rows`, `Review_Queue`, `Documents`, `OCR_Raw`.
- `tb_rows.csv`: reconstructed table rows.
- `review_queue.csv`: rows requiring human review.
- `documents.csv`: one record per detected TB page and page-level OCR / number-format diagnostics.
- `ocr_raw.csv`: raw OCR observations, recognition language, region, confidence and bounding box.
- `images/`: extracted source table images.
- `run_report.json`: run summary and configuration.

A run is complete only when every TB page is present, source images exist, row/column alignment has been sampled, all number-format repairs and ambiguities are routed for review, and the report clearly separates OCR extraction from human verification.

## 5. Language and script policies

- Determine page OCR language from page-level native PDF text / operator override, not from Gazette boilerplate footer text.
- `chinese_cht` maps to Apple Vision `zh-Hant`; never use `zh-Hans` as a silent fallback.
- `pt` maps to an available Portuguese locale; the worker prefers `pt-PT`, then `pt-BR`, then another supported `pt-*` identifier.
- `en` maps to an available English locale, preferring `en-US`.
- Numeric debit/credit regions use English/digit recognition regardless of document language.
- Vision `label_raw` is immutable OCR output. OpenCC `s2hk` creates a separate `label_normalized` value only. Script conversion does not prove the account name was correctly recognized.
- Raw OCR confidence values from Vision and PaddleOCR are not calibrated to the same scale; do not compare them as probabilities or accuracy scores.

### Standard label lexicon and hierarchy

- `labels/label_lexicon.csv` is seeded from verified GT and may be curated by hand. Matching is exact or fuzzy after language-specific folding: Traditional Chinese variant characters, and Portuguese case/accent/spacing.
- `label_raw` is never overwritten. `label_standard` and `label_standard_id` hold the mapped standard name; `label_standard_status` is `exact` / `fuzzy` / `unmatched` / `continuation`, plus `label_standard_score`.
- Fuzzy matches below 0.70 (`label_standard_low_similarity`) and 0.70–0.90 (`label_standard_fuzzy_review`) are routed to review; only ≥0.90 is a low-risk candidate. Near-miss unmatched labels with amounts are `label_standard_uncertain`. Never treat a fuzzy mapping as verified data.
- Dash sub-items are children of the nearest preceding parent: `label_role`, `parent_label_raw`, `parent_label_standard`, `label_path`. Dash-less sub-items (`PATACAS`) are reclassified only on an exact child-lexicon hit; orphans are flagged.
- A label wrapped onto the line above a row with amounts is merged into `label_combined_raw` / `label_standard` only when the combined text extends the previous standard; the original rows and `label_raw` stay intact. Flagged `label_continuation_merged`.
- Rebuild the lexicon with `tools/build_label_lexicon.py` after adding verified labels; measure with `tools/gt_label_report.py`.

## 6. Numeric format detection and normalization

### Supported page formats

- `comma_dot`: thousands comma, decimal dot, e.g. `1,234,567.89`.
- `dot_comma`: thousands dot, decimal comma, e.g. `1.234.567,89`.
- `ambiguous`: insufficient or conflicting evidence; amounts must remain untrusted and reviewable.

**Never infer number format from language.** Portuguese documents may use comma-dot format, and the known issue-45 Portuguese page 10 visually uses comma thousands / dot decimal. Infer per statement page from actual numeric strings or use an explicit operator override only after visual confirmation.

### OCR separator recovery

The normalizer supports grouping-aware recovery when the integer side still forms a valid 3-digit grouping pattern. Under a confirmed `comma_dot` profile, sample strings from the user's review file can be handled as:

| Raw OCR | Canonical candidate |
|---|---:|
| `119.366,280.10` | `119366280.10` |
| `2.496,722,065.08` | `2496722065.08` |
| `405.228,717.57` | `405228717.57` |
| `65.045.622.601.52` | `65045622601.52` |
| `14.702,016,826,10` | `14702016826.10` |
| `5,083,140,620 78` | `5083140620.78` |

A space between digit groups is treated as a possible separator (`5,083,140,620 78`), while a space next to existing punctuation is removed as whitespace noise (`32, 713, 062.68` → `32713062.68`, no repair). When the two readings of a spaced token disagree (`1 234 56`) the amount is left `ambiguous_format` instead of guessed.

Every such repair returns `recovered_ocr_separator` and must enter `review_queue.csv`. No digit is added, removed or inferred. Invalid grouping returns `invalid_grouping` / `ambiguous_format`. O/0, I/1 and similar character substitutions remain flagged `ambiguous_ocr_character`, even if a candidate numeric value is emitted.

Canonical CSV decimal representation uses digits, no thousands separator and a dot decimal separator. Excel numeric columns are stored as numeric cells; `*_raw` columns retain original OCR strings.

### CLI number options

- `--number-format auto` (default): infer from page numeric evidence.
- `--number-format comma_dot`: operator-confirmed override.
- `--number-format dot_comma`: operator-confirmed override.
- `--no-repair-ocr-separators`: disable structurally valid OCR separator recovery.

## 7. Data dictionary

### `documents.csv`

Contains PDF/printed page, bank name, statement/publication dates, metadata source/status, image dimensions/xref, OCR language routing, OCR status, detected number format/confidence/evidence, Vision elapsed time and source image path. When `--header-fallback` runs, `header_fallback_status`, `header_fallback_bank_name`, `header_fallback_lines`, `header_fallback_image` and related fields record the full-page header evidence; `bank_name_source = ocr_full_page_header` when the name came from that pass.

### `tb_rows.csv` / Excel `TB_Rows`

Important fields:
- `label_raw`: exact account label OCR string.
- `label_normalized`: separate OpenCC-normalized label.
- `label_normalization_status`: converted / unchanged / normalization dependency or error state.
- `debit_raw`, `credit_raw`: exact OCR monetary strings.
- `debit_amount`, `credit_amount`: canonical decimal candidates.
- `number_format`, `number_format_confidence`, `number_format_evidence`: page-level number convention and evidence.
- `debit_parse_status`, `credit_parse_status`: parser / recovery state.
- `row_confidence`, `row_average_confidence`: OCR confidence information.
- `review_reason`, `review_status`: review logic output.
- `column_boundary_source`: detected method or fallback.
- PDF page, image path and bounding box: provenance to source image.

### `ocr_raw.csv` / Excel `OCR_Raw`

Stores unmodified OCR text, actual recognition language, header/table region, confidence and coordinates.

## 7.1 SQLite integration

`tools/build_database.py` merges run folders without overwriting earlier observations:

```bash
python tools/build_database.py output_v08_issue45_2026 output_v06_issue44_2026 \
  --db data/macau_balancete.sqlite
```

- Tables: `runs`, `banks`, `bank_aliases`, `statements`, `line_items`, `row_verification`.
- A statement is identified by `run_id + paper_key`, where
  `paper_key = publication_date | statement_date | bank_key`.
- Rows use `line_item_id` as PK and `statement_id + label_path + occurrence` as the natural key.
  Repeated labels are not silently merged; they appear in `v_natural_key_duplicates`.
- `publication_date` is derived from other pages in the same run only when all non-empty
  values agree; conflicts stay blank and are reported.
- Bank names are folded to `bank_key`; optional aliases merge verified multilingual names.
- Human verification is stored only in `row_verification`. An `auto_check_pass` row is
  still unverified. `debit_cents` / `credit_cents` hold exact minor units; the
  `*_total_approx` columns are REAL sums for quick checks only.
- Read `DATABASE.md` before writing SQL. Do not change schema fields without a rebuild plan.

## 7.2 Data-integrity workflow (v0.4.2)

- **Partial publish guard:** `--publish` compares the new run's `pages.pdf_page` set with the currently published run(s) for the same source PDF. Missing pages block publishing unless `--force-publish`. Publish identity uses `source_pdf_sha256` first, then path.
- **OCR-time hash:** the OCR run writes `source_pdf_sha256` and `source_pdf_size` to `run_report.json` before processing. The importer uses that hash; `runs.source_pdf_sha256_source` records `ocr_report`, `import_time` or `missing`, and a changed PDF raises a warning.
- **DB constraints:** CHECK/trigger validation covers `runs.is_published`, `line_items.natural_key_status` and `source_pdf_sha256_source`; all importer connections enable `PRAGMA foreign_keys=ON`.

- **Publish before using as current:** imports are staging observations. After quality checks, re-run with `--publish` (or pass it on the import) to mark a run as the published dataset for its source PDF. `v_current_*` prefers published runs; `v_published_*` excludes staging runs.
- **`--replace` and verification:** `--replace` snapshots `row_verification` and restores only rows whose `statement_id + row_order` and OCR fingerprint still match. A mismatch aborts and rolls back. Use `--discard-verification` only when intentionally dropping verification.
- **Verified totals:** `debit_total_cents` / `credit_total_cents` cover all parseable rows; `debit_verified_cents` / `credit_verified_cents` cover only `row_verification.status = 'verified'`.
- **Provenance:** `runs.run_id` is a full SHA-256 over source PDF bytes, CSV outputs and `run_report.json`; `source_pdf_sha256` is stored separately.
- **Schema:** `SCHEMA_VERSION` is independent of project version. `_schema()` migrates old databases forward and refuses a database newer than the application.
- **Alias changes:** changing `bank_aliases.csv` does not rewrite existing `bank_key` values. The importer reports drift; rebuild the database or re-import affected runs explicitly.

## 8. Review policy

Always route these to review:

- `recovered_ocr_separator`.
- `ambiguous_format`, `invalid_grouping`, `ambiguous_ocr_character`.
- Page-level `number_format == ambiguous` when amounts exist.
- Low confidence, blank account label with an amount, fallback column boundaries, row alignment anomalies, or missing document metadata.

`auto_check_pass` only means the row passed prototype rule checks. It is not audited, source-confirmed, or financially verified data. Matching debit / credit totals is only a plausibility check and does not prove every account line is correct.

## 9. Test and benchmark requirements

Run:

```bash
python -m unittest discover -s tests -v
```

Before changing OCR routes, row grouping, normalization, thresholds or number rules:

1. Freeze a manually reviewed ground-truth dataset containing Chinese and Portuguese pages.
2. Compare the same original embedded images.
3. Measure account label character error / exact match, debit exact match, credit exact match, row alignment accuracy, and runtime on the target M1 Mac.
4. Preserve raw OCR and configuration for reproducibility.
5. Report separate metrics for each language and `fast` / `accurate` level.

`tools/gt_build_sheet.py` creates a pre-filled worksheet (all rows `verified=0`), `ground_truth/README.md` defines the manual review rules, and `tools/gt_score.py` reports label exact/CER, debit/credit exact, row alignment F1, review rate and `auto_pass_wrong_amount_rows`. Only rows marked `verified=1` by a human are scored.

Do not claim an OCR accuracy rate or speed improvement without a calculation from this benchmark.

## 10. Troubleshooting

### Engine `vision` unavailable

- Vision works only on macOS.
- Install Swift Command Line Tools with `xcode-select --install` if `swiftc` is missing.
- The first run compiles `vision_ocr_worker.swift` into `.vision_cache/`.

### `zh-Hant` unavailable

Stop. Print `--list-languages` output and report the macOS/Vision language support. Never silently substitute `zh-Hans`.

### Portuguese or English locale unavailable

Stop with the locally supported language list. Do not silently use an unrelated model.

### OCR is slow

Compare `fast` and `accurate` on the same page, same image, separate output folders. The minimum text-height threshold trades off small-text coverage and speed. Do not raise it without checking missed rows.

### Number format is wrong or ambiguous

Inspect `number_format`, `number_format_confidence`, `number_format_evidence`, `*_raw`, `*_amount`, and `*_parse_status`. Confirm visually before passing a `--number-format` override. Never use Portuguese/Chinese language alone as the number-format rule.

### OpenCC dependency missing

Install `requirements.txt` in the active virtual environment. Keep `label_raw` untouched; `label_normalized` should not be manually overwritten.

### Zero TB pages detected

Inspect the native PDF text and image list. Gazette layout may have changed. Update the TB-page detection rules only after inspecting the new issue and add a regression test.

## 11. Current limitations

- Vision runtime, language availability, speed and OCR quality must be verified on the actual macOS machine; this package's Python tests run outside macOS and cannot execute Apple Vision.
- Current ROI y-range and two-separator / three-column assumptions were tuned around issue 45 and need revalidation for other layouts.
- `--amount-verifier tesseract` and `--row-structure projection` are unvalidated experiments; they must be compared against manual ground truth before being enabled for real runs.
- Bilingual pages may produce overlapping language observations; visually sample those rows.
- OpenCC converts script/region variants but cannot correct wrong account recognition.
- Number separator recovery relies on structurally valid digit grouping; it cannot identify omitted, duplicated or substituted digits with certainty.
- `label_standard` remains language-specific (`現金` and `Caixa` are separate labels). The SQLite schema stores `label_language`, but cross-language concept mapping must be curated and verified before use.
- The program does not replace independent reconciliation or human verification.
