# Changelog

## v0.2.0

- Added native macOS Apple Vision OCR via `vision_ocr_worker.swift` and `--engine vision`.
- macOS now defaults to Vision; non-macOS defaults to PaddleOCR.
- Added separate Vision OCR regions for header, account label, debit and credit columns.
- Added runtime Vision language availability checks; Traditional Chinese will not silently fall back to Simplified Chinese.
- Added OpenCC `s2hk` account-label normalization as a separate `label_normalized` output; raw OCR is preserved.
- Added page-level numeric convention inference (`comma_dot` / `dot_comma` / `ambiguous`).
- Added grouping-aware OCR separator recovery, always flagged for human review.
- Split core requirements from optional PaddleOCR and Tesseract dependencies so an M1 Mac can use Vision without PaddlePaddle.
- Added number-normalization and engine-default unit tests.

## Validation note for this delivery

- Python syntax check: passed.
- Python unit tests: 27 discovered; 26 passed and 1 OpenCC integration test skipped because OpenCC is not installed in the build container. It is included in `requirements.txt` and will run after installation.
- `swiftc -parse vision_ocr_worker.swift`: passed. Full Swift type-check/link and Vision runtime require macOS; they were not run in this Linux environment.
- PDF inspect-only regression on the supplied issue-45 PDF: detected all 26 TB pages and extracted embedded images on all 26 pages. No OCR was run during this PDF inspection test.
