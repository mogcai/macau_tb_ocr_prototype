# Changelog

## v0.2.1

- **Ground truth scoring fix:** `match_rows()` now pairs rows by reading order only when both sides genuinely lack coordinates. Rows with coordinates outside the tolerance stay unmatched, so one missing row plus one spurious row can no longer cancel out and inflate `alignment_f1`. Added regression tests for missing/spurious rows and coordinate-less rows.
- **Blank vs dash are now distinct:** `parse_amount()` returns `blank` for an empty cell and `dash` for a printed `-`/`—`/`–`/`−`; only `blank`/`dash` are treated as legitimate empty states in review logic. `gt_build_sheet.py` decides `*_dash_gt` from the cell's OCR text, not from a conflated parse status. `gt_score.py` compares blank/dash/value states independently and reports `dash_mismatch_rows` plus `*_state` errors.
- Repaired the existing issue-45 GT dash flags (522 cells had been pre-filled as dash because the status was `dash_or_blank`); recorded in `ground_truth/issue45_2026/gt_changelog.md`. Dashes with no OCR text should still be confirmed visually.
- Added second-pass timing per page (`amount_second_pass_seconds`) alongside cell/fill counts.
- Upgraded version strings to v0.2.1 across `APP_VERSION`, README, SKILL, README_AGENT, MACOS_SETUP and `config.example.json`.
- README notes that `--vision-level fast` on a `zh-Hant` page only affects the Latin/digit regions; Chinese regions always run at `accurate`.
- Tests: 82 passing (new GT matcher, dash/blank parser and scorer tests).

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
- Vision `fast` has no Traditional Chinese recognizer; `zh-Hant` header/label tasks are now automatically run at `accurate` and the requested/used levels are recorded in `documents.csv`.
- Added `--vision-language-correction off|labels|all` (default `labels`: text regions only, numeric columns untouched) and experimental `--vision-preprocess on|off` (default `off`) with per-page provenance in `documents.csv`.
- Added experimental `--amount-verifier tesseract` (evidence-only second digit reading; never overwrites raw/parsed amounts) and `--row-structure auto|projection` (guarded ink-projection row bands). Both default off; on issue 45 they changed no row counts and added 5 review rows, so no accuracy improvement is claimed.
- Added `tests/test_v2_1_structure_verifier.py` covering separator-free digit signatures, verifier statuses, TSV parsing, band assignment and review policy.
- Added `tools/gt_build_sheet.py` + `tools/gt_score.py` and `ground_truth/README.md` for a human-verified benchmark (label exact/CER, amount exact, row alignment, auto-pass-wrong-amount). `ground_truth/issue45_2026` now has 127 human-verified rows on pages 7 and 10 (see `gt_changelog.md`); only `verified=1` rows are ever scored.
- Separator recovery now handles a substituted decimal glyph (`14.702,016,826,10`) and a printed space used as the decimal mark (`5,083,140,620 78`); both are flagged `recovered_ocr_separator`. Spaces next to existing punctuation are whitespace noise and do not trigger a repair. Conflicting space readings stay `ambiguous_format`.
- Column layout detection now picks the **two rightmost** vertical rules and trims the label ROI at a leading account-code separator. Issue-45 page 29 (code / label / debit / credit) previously fed label text into the debit ROIs and both amounts into the credit ROI.
- The table-body ROI bottom moved from 0.985 to 0.997 so the final total row is not clipped (page 29 `總額` was read as garbage).
- A single trailing letter on an amount (`30,214,693,162.03g`) is removed and flagged `recovered_ocr_character`.
- `gt_score.py` scores only GT pages present in the run (warning for skipped pages) and warns when page-level GT metadata is still `verified=0`. `gt_build_sheet.py` gained `--sync-missing` (append only rows the new run has and the GT lacks, preserving verified rows).
- Bilingual label dedupe now resolves overlapping cross-language boxes by script: a Chinese line keeps the Chinese reading, a Latin line keeps the Portuguese reading. Issue-45 page 17 label exact 70.2% → **98.3%**, CER 6.37 → **0.02** on the verified GT.
- GT expanded to 380 verified rows (pages 7, 9, 10, 15, 17, 29), including totals and page metadata. Current baseline: label exact 79.7% / CER 0.2823, debit exact 95.0%, credit exact 98.2%, 8 wrong amounts (all page 15), 0 auto-pass wrong.
- Added `--amount-second-pass ink` (default): a targeted 3× re-read of empty/unparsed/low-confidence amount cells that contain ink. First readings stay in `*_raw`; re-reads go to `*_second_pass_raw`; adopted values are `recovered_second_pass` and always reviewed. On the 380-row GT: debit exact 95.0% → **100%**, credit exact 98.2% → **100%**, wrong amounts 8 → **0**, auto-pass wrong 0. Cost ~190 cell re-reads (+30 s per 26-page run).
- GT fix: p0015_r045 credit `3527968` → `3537968.00` (printed 3.537.968,00).
- First ground-truth measurement (127 verified rows, pages 7+10): debit exact 48/49 → **49/49**, credit exact 45/46 → **46/46**, wrong amounts 2 → **0**, labels unchanged at 77.95% exact / 0.2992 CER.

## Validation note for this delivery

- Python syntax check: passed.
- Python unit tests: 27 discovered; 26 passed and 1 OpenCC integration test skipped because OpenCC is not installed in the build container. It is included in `requirements.txt` and will run after installation.
- `swiftc -parse vision_ocr_worker.swift`: passed. Full Swift type-check/link and Vision runtime require macOS; they were not run in this Linux environment.
- PDF inspect-only regression on the supplied issue-45 PDF: detected all 26 TB pages and extracted embedded images on all 26 pages. No OCR was run during this PDF inspection test.
