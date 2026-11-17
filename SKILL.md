---
name: macau-balancete-ocr
description: Run, inspect, troubleshoot, and quality-check the Macau Gazette Balancete OCR Python prototype. Use when processing Macau Government Gazette PDFs containing bank or financial-institution trial balance tables (Balancete / 試算表) and exporting structured Excel or CSV data.
---

# Macau Gazette Balancete OCR — Agent Skill

## 1. Purpose

Use this skill to operate the local Python project `macau_tb_ocr.py`. The project extracts bank/financial-institution Balancete table images from Macau Government Gazette PDFs, runs OCR, reconstructs rows into `Account | Debit | Credit`, and writes CSV/XLSX files with a review queue.

The required output is structured data, not merely OCR text. Preserve provenance and uncertainty. Financial amounts must never be silently corrected or represented as verified unless independently checked.

## 2. When to use this skill

Use it when the user asks to:

- process a Macau Gazette PDF for Balancete / 試算表 information;
- run or troubleshoot this prototype;
- extract account descriptions, debit and credit values;
- create or review the Excel/CSV output;
- compare OCR results with source PDF pages or improve extraction quality.

Do not use it for unrelated Gazette notices unless the user explicitly broadens the scope.

## 3. Project files

Expected project directory:

```text
macau_tb_ocr_prototype/
├── macau_tb_ocr.py          # CLI application; source of truth for actual options/behaviour
├── README_zh_HK.md          # human-oriented setup guide
├── README_AGENT.md          # concise operator guide
├── SKILL.md                 # this agent skill
├── requirements.txt         # core Python packages + PaddleOCR
├── requirements-tesseract.txt  # optional Tesseract Python wrapper
├── config.example.json      # reference settings only; not read by the current CLI
├── Dockerfile
├── tests/
└── sample_output/
```

The project does not include the original Gazette PDF. The caller must provide the source PDF path.

## 4. Known source characteristics: Gazette issue 45 of 2026

The supplied file `anotariais-45-2026.pdf` was inspected as a reference fixture:

- 32 PDF pages total.
- Balancete notices were found on PDF pages 7–32 (26 pages).
- The table body is embedded as a high-resolution image on each of those pages.
- The main image should be extracted from the PDF by image xref using PyMuPDF; avoid degrading it by rasterizing the entire page unless there is no suitable embedded table image.
- Chinese Traditional and Portuguese are both present; mixed-language pages occur. Keep English (`en`) available for future issues.
- For this issue, page 7 is a useful Traditional Chinese smoke test and page 10 is a useful Portuguese smoke test.
- Some metadata can be read from the PDF text layer, but image-header OCR/manual review is required where bank name or statement date is absent from the text layer. In the issue-45 inspection, page 17 and page 28 deserve special metadata review.

These page numbers are specific to issue 45. Never assume another issue has the same page range. Inspect every new PDF first.

## 5. Operating rules — must follow

1. **Never modify or overwrite the source PDF.** Treat it as immutable input.
2. **Use a new output directory for every run.** The script writes files directly into `--outdir`; do not reuse a populated directory unless the user explicitly authorizes replacement.
3. **Inspect before OCR on every new PDF.** Page ranges and embedded-image layouts can change between issues.
4. **Preserve original OCR text.** Keep `label_raw`, `debit_raw`, `credit_raw` and `ocr_raw.csv` unchanged as evidence.
5. **Do not silently repair numeric characters.** `O/0`, `I/1`, `l/1`, and `|/1` ambiguities must remain reviewable. The parser may output a candidate numeric value while attaching an ambiguity issue; it is not verified.
6. **Never treat `auto_check_pass` as proof of correctness.** It only means the current heuristic checks did not flag the row.
7. **Do not invent missing bank names, dates, account labels, or amounts.** Leave blanks and mark for manual review when the source does not support a value.
8. **Always preserve provenance:** PDF page number, printed page if available, source image path, bounding box, OCR language, and confidence.
9. **Check extraction completeness.** Compare `documents.csv` and `run_report.json` with the source PDF and expected page list before reporting success.
10. **Do not claim an OCR accuracy percentage without a manually labelled ground-truth dataset.** OCR engine confidence is not an accuracy metric.
11. **Do not claim the trial balance reconciles unless you independently calculate and verify it.** The present program does not calculate grand debit-column and credit-column totals. It only checks certain rows labelled as totals when both amounts are present.
12. **Check `column_boundary_source`.** `fallback_0.48_0.75` means the program could not detect both vertical rules and used fixed fractions; manually inspect those pages before trusting column placement.
13. **Check `embedded_image`.** If false, the program rendered the whole PDF page as a fallback. Inspect the resulting image because it may include page margins, other text, or a non-table image.
14. **Treat `config.example.json` as documentation only.** The current CLI does not load this file and has no `--config` argument. Use command-line arguments to control execution.
15. **Do not upgrade or change OCR libraries/models automatically** while processing production data. Propose a controlled change, preserve the current environment, and compare against the same ground-truth fixtures.

## 6. Environment setup

Use Python 3.10 or 3.11 in an isolated virtual environment where possible.

```bash
python3 -m venv .venv
source .venv/bin/activate       # macOS/Linux
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Install PaddlePaddle for the current OS, CPU/GPU, and hardware architecture using the official selector:

- https://www.paddlepaddle.org.cn/install/quick

Then install project requirements:

```bash
python -m pip install -r requirements.txt
```

PaddlePaddle must be installed first using a compatible wheel. Do not assume the same install command works on Intel macOS, Apple Silicon, Windows, and Linux. The first PaddleOCR run may download recognition/detection models and therefore needs network access; subsequent runs normally use the local model cache.

Optional Tesseract baseline:

- Install the system Tesseract executable separately.
- Install the required language packs (`HanT`, `por`, `eng`) for the relevant language tests.
- Install the Python wrapper with `python -m pip install -r requirements-tesseract.txt`.
- Use `--engine tesseract` only as an optional baseline/benchmark, not as the default production route.

## 7. Standard runbook

### Step A — verify the project and CLI

Run from the project directory:

```bash
python macau_tb_ocr.py --help
python -m unittest discover -s tests -v
```

If tests fail, stop and report the failure before processing financial data. Do not edit the tests merely to make them pass.

### Step B — inspect a new PDF first (no OCR)

For the known issue-45 file:

```bash
python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_issue45_inspection \
  --pages 7-32 \
  --inspect-only
```

For another issue, initially omit `--pages` to inspect all pages:

```bash
python macau_tb_ocr.py /path/to/input.pdf \
  --outdir output_inspection \
  --inspect-only
```

The inspect-only mode does not run OCR. It should create:

- `documents.csv`
- `images/page_XXXX_table.png`
- `run_report.json`

Inspect these values:

- `tb_pages_detected`: is this plausible for the source PDF?
- `pdf_page`, `printed_page`: are the detected pages the expected Gazette pages?
- `bank_name`, `statement_date`: are metadata populated and plausible?
- `image_width`, `image_height`: is the extracted image a table image at useful resolution?
- `embedded_image`: is the image directly extracted from the PDF?
- `ocr_languages`: does the detected language match the table?
- Open several extracted PNGs and check that they show the full Balancete and not a different page object.

For issue 45, expect 26 TB pages in PDF page range 7–32. This is a comparison target, not a universal rule for future issues.

### Step C — run small language-specific smoke tests

Use fresh output directories. Start with a Chinese page and a Portuguese page:

```bash
python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_issue45_p07 \
  --pages 7 --engine paddle --device cpu \
  --ocr-version PP-OCRv5 --ocr-langs auto

python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_issue45_p10 \
  --pages 10 --engine paddle --device cpu \
  --ocr-version PP-OCRv5 --ocr-langs auto
```

Also test a bilingual or unusual-layout page before batch processing (for issue 45, inspect pages 17, 20, or 28 as appropriate). `auto` selects one or more OCR language profiles from the page's selectable PDF text layer. For a page whose language cannot be detected reliably, override it explicitly:

```bash
--ocr-langs chinese_cht
--ocr-langs pt
--ocr-langs en
--ocr-langs chinese_cht,pt
```

For mixed-language pages, `auto` may execute multiple recognizers and deduplicate overlapping boxes. If the native text layer is absent or misleading, explicitly choose a language based on visual inspection of that page.

### Step D — inspect smoke-test results

For each run, inspect:

- `run_report.json` — confirms run mode, number of detected TB pages, number of rows, and rows sent to review.
- `documents.csv` — bank/date/language metadata and OCR row counts.
- `tb_rows.csv` — reconstructed account/debit/credit rows.
- `review_queue.csv` — flagged rows.
- `ocr_raw.csv` — OCR text boxes, confidence scores, and pixel coordinates.
- `images/` — original extracted table images.
- `macau_balancete.xlsx` — workbook containing `TB_Rows`, `Review_Queue`, `Documents`, and `OCR_Raw` sheets.

Open the relevant source image alongside the CSV/XLSX. Confirm that account descriptions and amounts remain in the correct columns. OCR confidence alone is not sufficient.

### Step E — batch OCR

Only after smoke tests are acceptable, run the entire identified TB page range. For issue 45:

```bash
python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_issue45_full \
  --pages 7-32 \
  --engine paddle --device cpu \
  --ocr-version PP-OCRv5 --ocr-langs auto \
  --confidence-threshold 0.88
```

The default threshold `0.88` is an initial prototype setting, not a calibrated production threshold. Keep the default while establishing a benchmark; do not lower it merely to reduce review workload.

### Step F — report completion honestly

A successful process exit is not the same as a successful extraction. Before reporting completion, verify:

- the source PDF opened and the intended page range was processed;
- the number of detected TB pages is plausible and matches the inspected manifest;
- expected output files exist and are non-empty where data is expected;
- each TB page has a corresponding source image and document record;
- rows with empty labels, suspicious numeric strings, low confidence, or fallback column boundaries are flagged/reviewed;
- bank name and statement date missing from the PDF text layer were checked against the image header;
- no financial value was manually changed without traceable review evidence.

If OCR dependencies or models are unavailable, say that the inspection/extraction stage completed but OCR did not. Do not manufacture TB rows from the sample inspection files.

## 8. CLI reference

```text
python macau_tb_ocr.py PDF [options]
```

| Option | Meaning | Default |
|---|---|---|
| `pdf` | Input PDF path | required |
| `--outdir` | Output folder | `output` |
| `--pages` | `all`, one page (e.g. `7`), or inclusive range (e.g. `7-32`) | `all` |
| `--engine` | `paddle` or `tesseract` | `paddle` |
| `--device` | `cpu` or `gpu` (PaddleOCR) | `cpu` |
| `--ocr-version` | PaddleOCR version | `PP-OCRv5` |
| `--ocr-langs` | `auto` or comma-separated `chinese_cht,pt,en` | `auto` |
| `--confidence-threshold` | Row confidence review threshold | `0.88` |
| `--inspect-only` | Extract images/metadata without OCR | off |

Page numbers are **1-based and inclusive** in the CLI.

## 9. Output data dictionary

### `documents.csv`

- `pdf_page`: 1-based page number in the PDF.
- `printed_page`: printed Gazette page number if found.
- `bank_name`: bank/institution name from PDF text, or image header OCR when supported.
- `statement_date`: normalized statement date (`YYYY-MM-DD`) when parsed.
- `publication_date`: Gazette publication date when parsed.
- `bank_name_source`: indicates whether name came from `pdf_text`, `ocr_header`, or needs image/manual review.
- `image_width`, `image_height`, `image_xref`, `embedded_image`: image provenance and extraction details.
- `ocr_languages`, `ocr_status`, `metadata_status`: language and processing status.
- `image_path`: path to the extracted table image.
- After OCR, `ocr_box_count`, `table_row_count`, `review_row_count` may also be present.

### `tb_rows.csv` / `TB_Rows` sheet

- `label_raw`, `debit_raw`, `credit_raw`: original OCR cell text.
- `debit_amount`, `credit_amount`: parsed decimal candidates. These are not verified values if `review_reason` is non-empty.
- `label_confidence`, `debit_confidence`, `credit_confidence`: minimum OCR box confidence for each column where present.
- `row_confidence`: minimum OCR confidence in the row.
- `row_average_confidence`: mean OCR confidence in the row.
- `bbox_x0`, `bbox_y0`, `bbox_x1`, `bbox_y1`: row bounding box in pixels of the extracted image.
- `languages`: OCR profile(s) used for row boxes.
- `review_reason`, `review_status`: rule-based review annotations.
- `column_boundary_source`: `detected_morphology`, `detected_projection`, or fallback.
- `pdf_page`, `printed_page`, `bank_name`, `statement_date`, etc.: source metadata.
- `source_image`: extracted table image path.

### `ocr_raw.csv` / `OCR_Raw` sheet

Contains each OCR box with `language`, `text`, `confidence`, `x0`, `y0`, `x1`, `y1`, plus document metadata and image path. This is the audit trail for reconstructing rows.

### `review_queue.csv` / `Review_Queue` sheet

A filtered copy of rows where `review_status == needs_review`. Review reasons may include:

- `low_ocr_confidence`
- `debit:ambiguous_ocr_character`
- `credit:unparsed_amount`
- `amount_without_account_label`
- `debit_credit_total_mismatch`

`dash_or_blank` is treated as a legitimate empty/dash value and does not itself trigger review.

## 10. Current algorithm limitations

The agent must understand these limitations before treating output as reliable:

- Page discovery uses selectable PDF text plus large embedded-image heuristics. A new Gazette issue can have different page layouts or multiple images per page.
- The extractor chooses the largest embedded image above a minimum dimension. If the PDF contains multiple large images, confirm the selected image visually.
- If no suitable embedded image is found, the script renders the full page at 4x scale and marks `embedded_image` false. This is a fallback, not equivalent to clean table extraction.
- OCR row reconstruction uses vertical position clustering and two vertical column boundaries. Multi-line account names, merged cells, faint rules, and nonstandard layouts may be misaligned.
- Column boundary fallback fractions are `0.48` and `0.75`; this must trigger manual inspection.
- `confidence_threshold` is not calibrated against ground truth.
- `auto_check_pass` is a heuristic label, not proof that an account row or amount is correct.
- The program does not presently calculate the overall debit-column sum versus credit-column sum. It only checks a row whose label contains a total keyword, when both amounts are populated.
- The example configuration file is not loaded by the CLI.

## 11. Quality assurance and benchmark protocol

Before production or unattended scheduled runs, create a manually verified ground-truth dataset from representative pages:

- Traditional Chinese table;
- Portuguese table;
- bilingual table;
- low-contrast/faint vertical rules;
- page with image-only bank/date metadata;
- pages with negative, zero, blank, and large grouped amounts;
- pages with multi-line account descriptions and total rows.

For each row, label the account description, debit amount, and credit amount from the original image. Compare program output to this dataset and report at least:

- account-label exact/normalized match rate;
- debit exact-match rate;
- credit exact-match rate;
- row alignment/omission rate;
- review-queue precision and recall if measured.

Never derive an accuracy percentage from PaddleOCR confidence scores. Never tune a threshold against the same data used only for visual demonstration; hold out pages for evaluation.

## 12. Troubleshooting

### `搵唔到 PaddleOCR` / module import failure

- Confirm the virtual environment is active.
- Install a PaddlePaddle wheel compatible with the host OS/architecture first.
- Reinstall requirements in the same Python environment.
- Run `python -c "import paddle; import paddleocr; print(paddle.__version__)"`.

### Model download fails

- First inference may need network access to fetch model files.
- Retry only after checking connectivity and available disk space.
- For an offline machine, stage/cache the approved model files separately; do not substitute a different model without documenting it.

### `documents.csv` has zero TB pages

- Run `--inspect-only` and inspect page text and images.
- Confirm that the PDF actually contains selectable TB title/official footer text and a sufficiently large table image.
- The current TB detector may need a new page-detection rule for a different Gazette layout. Do not force OCR on all pages without checking for false positives.

### OCR rows appear empty or shifted

- Open `images/page_XXXX_table.png`.
- Check `column_boundary_source` and `ocr_raw.csv` coordinates.
- If fallback boundaries are used, manually inspect and do not trust debit/credit placement.
- Test the correct language explicitly (`chinese_cht`, `pt`, or `en`).
- Do not edit amount values to make totals reconcile.

### Output files overwritten

- Stop further execution.
- Use a fresh output directory for future runs.
- Restore from the original PDF or prior output backup; this tool does not maintain run history for you.

## 13. Suggested next development tasks

Only implement after the current output has been benchmarked:

1. Add a configurable layout profile per bank/template.
2. Add row-level crops for a second OCR pass on low-confidence amounts.
3. Add explicit account/debit/credit header detection and line-item alignment tests.
4. Add a separate reconciliation report for total debit and total credit columns, without modifying source amounts.
5. Add a review UI that displays the original row crop beside raw and parsed values.
6. Add a scheduler/downloader only after deterministic processing and quality metrics are established.
