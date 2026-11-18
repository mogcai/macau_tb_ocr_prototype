# Changelog

## v0.6.0

- **User standard label import:** added `tools/import_user_standard.py` for `labels/standard_label_by_user.csv` (parent/child zh+pt). Schema v5 adds `standard_labels`, `standard_label_aliases`, `line_items.user_standard_label_id` and `user_standard_match_status`.
- **Validation/corrections:** the importer reports and corrects the shifted reserve block (股本/CAPITAL, 營運資金/FUNDO DE MANEIO, 法定儲備/RESERVA LEGAL, 自定儲備/RESERVA ESTATUTÁRIA, 其他儲備/OUTRAS RESERVAS, inserted 重估儲備/RESERVA DA REAVALIAÇÃO), AMCM child parent_pt, Greek Μ, 賬/帳 variants and 總收人→總收入. Corrected rows are `provisional`.
- **Mapping:** existing rows are mapped by parent/child path; 2198 rows matched (1874 parent_exact + 324 child_path_exact) and 163 stayed unmapped. Report: `data/user_standard_mapping_report.csv`; unmapped view: `v_user_standard_unmapped`.
- **Raw evidence untouched:** this mapping writes only `user_standard_label_id` / `user_standard_match_status`; `label_raw`, `label_standard` and the legacy concept registry are unchanged.
- **Tests:** 144 passing.

## v0.5.2

- **Standard line-item master:** added `tools/standard_line_items.py` with `export` and `apply`. `labels/line_item_standard.csv` is the human-maintained chart of accounts; one economic account is one standard, and Chinese/Portuguese/combined OCR text are aliases.
- **Draft master:** 65 standard rows, 59 with Chinese canonical names, 61 with Portuguese, 55 pt→zh merge keys and 6 unresolved pt-only rows. `labels/line_item_pt_zh_map.csv` records the curated proposals.
- **Safe apply:** `apply` loads only `status=approved` rows by default; provisional rows are skipped. It merges old concepts, moves aliases, updates `line_item_concept_id` and audits the change.
- **No auto concepts in standard mode:** after a standard master is loaded, unmatched OCR labels create a `needs_new_standard` review with no auto-created provisional concept.
- **Docs/tests:** added `STANDARD_LINE_ITEMS.md` and tests for export/apply/merge/standard mode. **141 tests passing.**
- Schema remains v4; the committed DB remains at the reviewed registry state until the standard master is approved and applied.

## v0.5.1

- **Review CSV workflow:** `name_registry.py export` writes `data/name_review_queue.csv` with editable `decision`, `approved_entity_id` and `note` columns; `import-review` applies approve/reject decisions. 153 pending items currently exported (8 bank + 145 line item).
- **Latin bank Chinese names:** added `labels/bank_name_translations.csv` with proposed Chinese canonical names for HANG SENG, ICBC Macau/group/head-office branch, ICBC Capital, Standard Chartered, HSBC and BNU. `build` fills `banks.canonical_zh` and creates pending `translation_seed` bank reviews; approval promotes `registry_status` to `approved`.
- **Docs/tests:** `NAME_STANDARDIZATION.md` covers export/import and bank translations. **137 tests passing.**
- Rebuilt the committed DB: 5 published runs, 38 statements, 2361 line items, 38 banks with `canonical_zh`, 216 concepts, 123 approved aliases, 8 pending bank translations and 145 pending line-item reviews.

## v0.5.0

- **Name Standardization Registry:** added `tools/name_registry.py` with `build`, `list`, `approve`, `reject` and `remap` commands. Raw OCR names are never overwritten.
- **Bank registry:** `banks` gains `canonical_zh`, `canonical_pt`, `canonical_en`, `entity_type`, `registry_status`; added `bank_name_aliases`.
- **Line-item concepts:** added `line_item_concepts`, `line_item_aliases`; `line_items` gains `line_item_concept_id`, `mapping_status`, `mapping_version`.
- **Review workflow:** same-language lexicon aliases are approved seeds. Cross-language adjacent Chinese/Portuguese lines become `bilingual_pair` pending candidates only; approving one merges the old concept into the target, moves aliases, updates `line_item_concept_id`, and writes `name_mapping_audit`.
- **New views:** `v_bank_registry`, `v_line_item_concepts`, `v_name_mapping_pending`, `v_line_item_concept_map`, `v_unmapped_line_item_names`.
- **Schema v4:** v3 databases migrate in place with the registry tables/columns.
- **Docs:** added `NAME_STANDARDIZATION.md` and cross-references in README, README_zh_HK, README_AGENT, SKILL and DATABASE.
- **Current seed:** 216 concepts (120 lexicon + 96 provisional discovery candidates), 123 approved line-item aliases, 145 pending line-item reviews, 38 bank entities; 2214 line items map `concept_exact`, 1 `concept_alias`, 140 `concept_unmapped`, 6 `concept_missing_label`.
- **Bank Chinese-name seed:** `labels/bank_name_translations.csv` supplies proposed Chinese names for Latin-only banks; they set `banks.canonical_zh` and create pending `translation_seed` reviews.
- **Review CSV workflow:** `name_registry.py export` writes `data/name_review_queue.csv` with blank `decision` / `approved_entity_id` / `note` columns; `import-review` applies the filled decisions.
- **Tests:** 137 passing.

## v0.4.2

- **Partial publish guard:** `--publish` now compares the new run's `pages.pdf_page` set with every currently published run for the same source PDF. A run missing any published page is refused unless `--force-publish` is passed. This prevents a single-page test run from removing the rest of a full published issue.
- **Publish by content hash:** peer lookup prefers `source_pdf_sha256`; runs without a recorded hash fall back to the `source_pdf` path. The same PDF copied to a different folder still supersedes the old published run.
- **OCR-time provenance:** `macau_tb_ocr.py` now computes `source_pdf_sha256` and `source_pdf_size` before OCR and writes both to `run_report.json`. The importer prefers this OCR-time hash, stores `runs.source_pdf_sha256_source` (`ocr_report` / `import_time` / `missing`) and warns if the PDF bytes no longer match the recorded hash.
- **Database constraints:** `runs.is_published` and `line_items.natural_key_status` have CHECK constraints for new databases plus BEFORE INSERT/UPDATE triggers for existing databases; `source_pdf_sha256_source` is validated. All importer connections set `PRAGMA foreign_keys = ON`.
- **Docker clarified:** the Linux image no longer copies the Apple Vision Swift worker and is documented as PaddleOCR-only.
- **Schema v3:** adds `runs.source_pdf_sha256_source`; v1/v2 databases migrate automatically.
- **Tests:** new coverage for partial-publish refusal/force, hash-based peer replacement, OCR-time hash precedence/mismatch, DB constraints/foreign keys, and file hashing. **130 tests passing.**
- **Validation:** re-ran issues 42–46 with v0.4.2 and rebuilt the committed database from the five new runs. The DB now has 5 published runs, 38 statements, 2361 line items, `source_pdf_sha256_source = ocr_report` for all five, 0 statements with incomplete identity, and no hash warnings.

## v0.4.1

- **`--replace` no longer silently deletes human verification.** The importer snapshots `row_verification` before deleting a run and restores it only when `statement_id + row_order` and the OCR fingerprint (`pdf_page`, raw/standard label, raw/canonical amounts) still match. A mismatch raises and rolls back the transaction. `--discard-verification` is the explicit destructive option. Tests cover preserve, discard and rollback-on-mismatch.
- **Published/current dataset separation.** `runs.is_published`, `published_at`, `published_by` and `--publish` mark a quality-checked run. `v_current_*` prefers published runs; `v_published_statements`, `v_published_line_items`, `v_published_balance_sheet` and `v_published_review_queue` expose the strict published dataset. A newer single-page staging run no longer replaces a published full run.
- **Verified totals.** `v_statement_index` now has `debit_verified_cents` / `credit_verified_cents` next to `debit_total_cents` / `credit_total_cents`; all totals coalesce to 0 instead of NULL.
- **Run provenance.** `run_id` is now a full SHA-256 over the canonical `run_report.json`, the source PDF SHA-256 (stored as `runs.source_pdf_sha256`), `documents.csv` and `tb_rows.csv`. Changing OCR settings or PDF bytes changes the run id.
- **Schema migrations.** `SCHEMA_VERSION` is now 2 and independent of project version. Opening a v1 DB adds the publish/provenance columns and publishes the newest existing run per source PDF; a DB newer than the app is refused.
- **Bug fixes:** imported runs report `tb_pages` from the actual page count (previously `len(statements)`); alias drift on an already imported run produces a warning instead of being silently ignored.
- **Version discipline:** added `VERSION` (project) and `tests/test_version_sync.py`; `SCHEMA_VERSION` remains separate in `tools/build_database.py`.
- Tests: 124 passing.

## v0.4.0

- Added `--header-fallback auto|off` (default `auto`): when the embedded table image yields no bank name or statement date, render the full PDF page header at 4x and OCR it separately. It never overwrites an existing name; recovered values set `bank_name_source = ocr_full_page_header`, are logged in `header_fallback_*` columns, and keep `images/page_XXXX_header_fallback.png` plus raw `header_fallback` OCR boxes. Issue-45 page 17 now recovers `郵政儲金局 | CAIXA ECONÓMICA POSTAL` (previously blank).
- Schema: `runs`, `banks`, `bank_aliases`, `statements`, `line_items`, `row_verification`; analysis views `v_balance_sheet`, `v_statement_index`, `v_current_line_items`, `v_all_line_items`, `v_natural_key_duplicates`, `v_review_queue`.
- The proposed business key `publication_date, statement_date, bank_name, parent_label_standard, label_standard` is retained for analysis but is **not** the primary key. Real runs contain repeated labels, blank labels and multi-page statements. Rows use a surrogate `line_item_id` and the natural key `statement_id + label_path + occurrence`.
- Missing `publication_date`/`statement_date` is derived only when all non-empty values in the same run agree; `*_source` records the origin (`documents.csv`, `tb_rows.csv`, `derived_from_run`, `mixed`, `missing`). Conflicting dates remain blank and are reported.
- Bank names are folded into a punctuation/accent-insensitive `bank_key`; `--bank-aliases` loads an `alias,canonical[,bank_key,note]` CSV. Group/branch entities are not merged automatically.
- Human verification is isolated in `row_verification` (`verified`/`rejected`/`pending`). `auto_check_pass`, OCR confidence and debit/credit equality still do not constitute financial verification.
- Added `tests/test_database.py` (12 tests) and `tests/test_header_fallback.py` (8 tests) covering header line extraction/noise, cross-language dedupe, non-fatal fallback failure, date derivation/conflict, duplicate labels, blank labels/unknown banks, aliases, comma-containing bank names, idempotent re-import/`--replace`, views and label paths. Tests: 112 passing.
- Added `DATABASE.md` and cross-references in README, README_zh_HK, README_AGENT and SKILL. Version bumped to v0.4.0.

**Validation:** full issue-45 rerun with the fallback (`output_v11_issue45_2026_full`) detected 26 TB pages and extracted 1587 rows, identical to `output_v08` on label/amount/review fields. Page 17 recovered `郵政儲金局 | CAIXA ECONÓMICA POSTAL` (`bank_name_source = ocr_full_page_header`); the rebuilt SQLite now has 31/31 statements with `identity_status = complete`, 31 banks, 1909 line items and the same 7 duplicate business-key groups / 132 missing-label rows.

**Known limitation:** `label_standard` is still language-specific (`現金` vs `Caixa`). A curated Chinese/Portuguese concept table is required before cross-language aggregation.

## v0.3.1

- **Tightened fuzzy label acceptance:** fuzzy 0.70–0.90 now always adds `label_standard_fuzzy_review`; below 0.70 adds `label_standard_low_similarity`. Only ≥0.90 is treated as a low-risk candidate. Exact matches remain automatic.
- **Lexicon curation:** merged printed variants (`總收人` into `總收入`; `prestadas`/`prestados` Portuguese forms) and added 查/査、抵/扺、資/资 異體字 folding. Fixed the corresponding GT labels (`其他固定資產`, `抵押賬`, `其他備查賬`). Lexicon now 120 entries; the remaining near-duplicate audit only shows genuinely different accounts (`本地`/`外地`, `Outra Reserva`/`Outras reservas`).
- **Orphan statistics fix:** `documents.csv` now reports `label_orphan_children` correctly (previously always 0).
- **`gt_label_report.py`:** matches rows with page image heights (same scale logic as `gt_score`), prints exact/fuzzy/unmatched/continuation counts for run and GT, and lists unmatched raw labels.
- Measured after retune: raw label exact 79.42% → **standardised exact 98.15%** (372/379), 3 wrong mappings (2 row-shift/data issues), 4 unmatched. CER 0.0232 (109/4704 characters). Amounts remain 100% exact with 0 auto-pass wrong.
- Tests: 92 passing. Version bumped to v0.3.1.

**Caveat:** the lexicon is still seeded from the same verified GT used for this score. The 98.15% is an in-sample measurement, not proof of cross-issue generalisation; validate on Issue 46 (or another gazette) before claiming production accuracy.

## v0.3.0

- Added a standard-label lexicon (`labels/label_lexicon.csv`, seeded from verified GT) and `--label-lexicon`. OCR is never overwritten: results go to `label_standard`, `label_standard_id`, `label_standard_status`, `label_standard_score`.
- Exact/fuzzy matching is language-aware (Traditional Chinese variant folding; Portuguese case/accent/spacing folding) with a conservative margin; uncertain and low-similarity mappings are routed to review (`label_standard_uncertain`, `label_standard_low_similarity`).
- Added parent/child detection for sub-items (`-澳門元`, `-外幣`, `- Patacas`, dash-less `PATACAS`): `label_role`, `parent_label_raw`, `parent_label_standard`, `label_path`; orphan children are flagged.
- Added wrapped-label detection: a fragment below a label-only row is merged into `label_combined_raw`/`label_standard` when the combined text extends the previous standard (`label_continuation_merged`); `label_raw` is untouched.
- Added `tools/build_label_lexicon.py` (rebuild lexicon from verified GT) and `tools/gt_label_report.py` (raw vs standardised exact match, wrong mappings).
- Measured on the 379-row verified GT: raw label exact 79.95% → **standardised exact 97.89%** (371/379), 3 wrong mappings (2 are row-shift/data issues), 5 unmatched. 3 wrapped labels recovered, 4 orphan children flagged.
- **Ground truth CER fix:** `label_cer` is now total edit distance divided by total GT characters (per page and across pages), not average edit distance per label row. `label_characters` and `label_edit_distance` are reported so the calculation is auditable. Existing v0.3.0 GT score: **CER 0.0225** (106 edits / 4704 characters), replacing the previous 0.2797 average-per-row figure.
- Added tests for lexicon matching, child hierarchy and continuation detection. Tests: 91 passing.
- Version bumped to v0.3.0.

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
