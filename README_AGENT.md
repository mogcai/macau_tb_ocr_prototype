# AI Agent Quick Start — Macau Balancete OCR

This is the short operational entry point for an AI coding/automation agent. For full constraints, data dictionary, quality rules, and troubleshooting, read [`SKILL.md`](SKILL.md). For a human setup walkthrough, read [`README_zh_HK.md`](README_zh_HK.md).

## Mission

Given a Macau Government Gazette PDF, identify bank/financial-institution Balancete pages, extract the embedded table image, run language-aware OCR, reconstruct `Account | Debit | Credit` rows, and export Excel/CSV with evidence and a review queue.

## Non-negotiable rules

- Never overwrite the source PDF.
- Use a unique `--outdir` per run; current CLI writes directly into that folder.
- Run `--inspect-only` before OCR on each new Gazette PDF.
- Inspect `documents.csv` and sample extracted PNGs before starting a batch.
- Preserve `*_raw` OCR text and original images. Never silently correct amounts.
- `auto_check_pass` is not proof of financial correctness.
- Review low-confidence rows, suspicious numeric characters, blank account labels, and fallback column boundaries.
- Do not report OCR accuracy without manual ground truth.
- `config.example.json` is a reference file only; current CLI does not load it.

## Commands

### 1. Check CLI and tests

```bash
python macau_tb_ocr.py --help
python -m unittest discover -s tests -v
```

### 2. Inspect a new PDF (no OCR)

```bash
python macau_tb_ocr.py /path/to/input.pdf \
  --outdir output_inspection_YYYYMMDD \
  --inspect-only
```

For known 2026 issue 45 only, TB pages are PDF pages 7–32:

```bash
python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_issue45_inspection \
  --pages 7-32 --inspect-only
```

Verify `run_report.json`, `documents.csv`, bank/date metadata, language, image dimensions, `embedded_image`, and the extracted PNGs.

### 3. Run a Chinese and Portuguese smoke test

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

For future PDFs, choose one visually confirmed Chinese page, one Portuguese page, and one mixed/unusual page from that issue; do not reuse fixed page numbers blindly.

### 4. Batch after smoke tests

```bash
python macau_tb_ocr.py /path/to/input.pdf \
  --outdir output_full_YYYYMMDD \
  --pages 7-32 --engine paddle --device cpu \
  --ocr-version PP-OCRv5 --ocr-langs auto \
  --confidence-threshold 0.88
```

Replace `7-32` with the page range confirmed by that PDF's inspection.

## Expected outputs

- `macau_balancete.xlsx` — sheets: `TB_Rows`, `Review_Queue`, `Documents`, `OCR_Raw`
- `tb_rows.csv` — reconstructed row data
- `review_queue.csv` — rows flagged for review
- `documents.csv` — one row per detected statement page
- `ocr_raw.csv` — OCR boxes, confidence, and pixel coordinates
- `images/` — extracted table images
- `run_report.json` — run summary

## Success criteria

A run is complete only when:

1. Detected statement pages match the inspected page manifest.
2. Each detected page has a source image and document row.
3. OCR output is present and the row count is plausible.
4. Account/debit/credit columns have been visually sampled against source images.
5. All `needs_review` rows and any fallback-boundary pages are reviewed or explicitly reported as pending.
6. The report clearly distinguishes extracted data from verified data.

The prototype does not currently reconcile the grand debit and credit column totals. Treat all parsed numbers as candidates until checked against the original table or a ground-truth dataset.
