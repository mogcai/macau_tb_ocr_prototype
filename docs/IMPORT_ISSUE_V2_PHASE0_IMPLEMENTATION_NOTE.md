# Importer V2 — Phase 0 Contract Verification Note

**Specification:** `docs/IMPORT_ISSUE_V2_V3_INGESTION_SPEC.md`, V2.4  
**Date:** 2026-11-21  
**Phase:** 0 — Contract verification  
**Status:** Complete; no implementation code changed  
**Operational DB inspected:** `data/macau_balancete_v3.sqlite`  
**Code inspected:** `tools/import_issue.py`, `tools/replay_runs_to_v3.py`, `macau_tb_ocr.py`, V3 DDL, importer/replay/V3 tests, CLI callers

---

## 1. Scope of this note

This note records the actual current behaviour of the repository against V2.4 before any refactor. It covers:

- the existing Importer CLI and V5 stage order;
- all known CLI callers/documentation;
- the V3 schema and operational data;
- OCR artifacts, especially `documents.csv` page coverage;
- the current replay contract and its limitations;
- the test baseline.

No importer, replay, OCR or database code was changed in Phase 0. The only new file is this note.

---

## 2. Verified current-state facts

### 2.1 Operational V3 database

```text
data/macau_balancete_v3.sqlite
  gazette_issues          14
  source_documents        14
  document_pages          118
  extraction_runs         14
  page_extractions        118
  extracted_rows          7,156
  statements              113
  statement_rows          7,046
  statement_row_mappings  7,251
  statement_reviews       68
```

- All 14 `gazette_issues.source_url` values are `NULL`.
- All 14 `source_documents.source_url` values are `NULL`.
- All 14 `extraction_runs.run_status` values are `completed`.
- Sum of `source_documents.page_count` is **118**.
- Sum of `run_report.pdf_pages` for the 14 source PDFs is **762**.
- The 118 observed pages are therefore historical OCR pages, not the complete PDF page set. Example: issue `2026-17` has `pdf_pages = 491` but `page_count = 11`.

This is the page-count gap V2.4 addresses. For legacy rows, V2.4 requires a controlled backfill using `legacy_partial`/`unknown` coverage; it does not permit inventing missing pages.

### 2.2 Existing run artifacts

```text
outputs/runs/issue16_2026 .. issue46_2026   (14 run directories)
  import_manifest.json   8 present: 16, 17, 18, 19, 29, 30, 32, 33
                         6 absent:  31, 42, 43, 44, 45, 46
  run_report.json        14 present
  documents.csv          14 present (TB pages only)
  tb_rows.csv            14 present
```

All 14 `run_report.json` files contain `source_pdf`, `source_pdf_sha256`, `source_pdf_size` and `pdf_pages`.

The eight legacy manifests use the key `source` for the URL and have no `manifest_version` or `target`. Six run directories have no manifest and must not fail a controlled legacy replay.

### 2.3 V3 schema facts

Relevant confirmed DDL:

- `source_documents.page_count INTEGER` with `CHECK (page_count IS NULL OR page_count > 0)`.
- `source_documents.sha256` is `NOT NULL` with a 64-character lowercase-hex check.
- `idx_source_documents_sha256` exists.
- `source_documents.document_key` is `issue_key + ":" + sha256[:16]` in the current replay writer.
- `extraction_runs.config_json` is unrestricted text and can carry provenance/coverage/fingerprint metadata additively.
- `extraction_runs.run_status` allows `queued`, `running`, `completed`, `failed`, `partial`.
- `page_extractions.ocr_status` allows `pending`, `completed`, `failed`, `skipped`.
- `document_pages` is unique by `(source_document_id, pdf_page)`.
- `page_extractions` is unique by `(run_id, document_page_id)`.
- `source_documents` has no file-size column. V2.4 correctly keeps size in `config_json`/manifest instead of adding a migration.
- Existing statements, statement rows, mappings, reviews, bindings, adoptions and publications must not be modified by raw-evidence import.

### 2.4 Current OCR artifacts and page coverage

Current `macau_tb_ocr.py` behaviour:

- It iterates the requested page range (`parse_page_range`, `--pages all` supported).
- `is_tb_page()` decides whether a page is a TB page.
- Non-TB pages are skipped with `continue` and **never receive a `documents.csv` row**.
- `documents.csv` therefore contains only TB pages.
- `run_report.pdf_pages` correctly records the full PDF page count (`len(doc)`).
- `run_report.tb_pages_detected` records the number of TB pages, not full PDF coverage.
- There is no full-page-record mode and no `skip_reason` column.

Consequence: V2.4's strict page-coverage rule cannot pass on current OCR output, even with literal `--pages all`. A V3 run would see only TB-page rows and be rejected for missing pages. This is a Phase 2 prerequisite, not a replay-only bug.

Required additive change: an OCR full-page-record mode (proposed name `--full-page-records`) that writes one `documents.csv` row per PDF page:

- TB completed: `ocr_status = "completed"`;
- non-TB intentionally skipped: `ocr_status = "skipped"`, with a skip reason;
- failed page: `ocr_status = "failed"`, with an error/reason.

This must not change V5's default OCR output or OCR recognition settings/models.

### 2.5 Current Importer behaviour

`tools/import_issue.py` currently:

- requires a positional `source`;
- has only V5 options (`--db`, `--input-dir`, `--outdir`, `--name`, `--pages`, `--engine`, `--vision-level`, `--ocr-langs`, publication/mapping flags, `--no-db`, `--no-ocr`, `--inspect-only`, `--download-only`, `--overwrite-pdf`, `--reuse-output`, `--dry-run`);
- has no `--target`, `--v3-db`, `--json`, `--replay-only`, duplicate/version flags, `--reason`, `--issue-key`, `--allow-local-source` or `--allow-outside-output`;
- downloads to `input/<name>` and promotes with `temp.replace(dest)` after a check (check-then-replace race);
- writes `import_manifest.json` with `Path.write_text()` (not atomic);
- prints progress and child output to stdout; child stderr is merged into stdout;
- on early failure writes a shared `output_import_failed/import_manifest.json` when no `--outdir` is supplied;
- never verifies the output directory is under a managed output root;
- never validates redirects, URL hosts, private addresses or local-source trust;
- uses `--db` default `Path("data/macau_balancete.sqlite")` relative to cwd;
- uses `--input-dir` default `Path("input")` relative to cwd;
- derives default output as `ROOT/outputs/runs/issueNN_YYYY` (or `<stem>`) through `output_dir_for()`;
- resolves an explicit relative `--outdir` against cwd;
- launches OCR/build/name commands with `cwd=ROOT`.

V5 path semantics confirmed and must be preserved:

```text
--db default:        <cwd>/data/macau_balancete.sqlite
--input-dir default: <cwd>/input
omitted --outdir:    <ROOT>/outputs/runs/issueNN_YYYY (or <stem>)
explicit --outdir:   resolved against cwd
child processes:     cwd=ROOT
```

### 2.6 Current replay behaviour

`tools/replay_runs_to_v3.py` currently:

- accepts `--db`, `--runs-dir`, `--run-dir`, `--v5-db`, `--log`;
- calls `load_legacy_runs(args.v5_db)` unconditionally;
- derives the Issue key only from the run-directory regex `issue(\d+)_(\d{4})`;
- has no `--v3-db`, `--issue-key` or `--no-v5-lookup`;
- writes `source_url = NULL` for both `gazette_issues` and `source_documents`;
- never reads `import_manifest.json`;
- computes `source_documents.page_count` as `len(page_numbers) or report.pdf_pages or 1`;
- inserts `extraction_runs.run_status = 'completed'` unconditionally;
- has no evidence fingerprint;
- has only the per-page row-count guard; a same-count amount change is silently skipped;
- returns per-run JSON Lines on stdout and exits 0/1;
- has no `already_present` field and no page-coverage fields;
- has no acquisition of the V3 importer lock;
- does not update existing `gazette_issues`/`source_documents` rows (insert-if-absent only).

---

## 3. Specification vs current state

### 3.1 Importer / CLI

| V2.4 requirement | Current state | Required work |
|---|---|---|
| `--target {v5,v3,ocr-only}`, default v5 | Absent | Phase 1/2 |
| `--v3-db`, V3-only | Absent; only V5 `--db` | Phase 2 |
| `--replay-only` without positional source | Absent; source required | Phase 2 |
| `--json` strict stdout | Absent; human stdout and merged child output | Phase 2 |
| `--reprocess`, `--new-version`, `--reason` | Absent | Phase 2 |
| `--issue-key` assertion/mismatch rejection | Absent | Phase 2 |
| `--allow-local-source`, `--allow-outside-output` | Absent | Phase 2 |
| V3 temp download -> validation -> duplicate gate -> no-clobber promote -> OCR | V5 downloads and `temp.replace(dest)` directly | Phase 2/3 |
| V3 duplicate/version decision table | Absent | Phase 2 |
| V3 page coverage and `page_count = pdf_pages` | Absent; replay uses observed pages | Phase 2 |
| V3 provenance (`source_url`, size, coverage) | Absent; replay writes NULL URLs | Phase 2 |
| V3 import lock | Absent | Phase 3 |
| V3 URL allowlist/HTTPS/redirect/address policy | Absent; V5 accepts HTTP and any host | Phase 3 |
| Atomic manifest | Absent; `write_text()` | Phase 1/3 |
| Unique per-job result directories | Absent; shared `output_import_failed` | Phase 2 |
| V5 cwd-based path semantics | Present | Preserve exactly |

### 3.2 Replay

| V2.4 requirement | Current state | Required work |
|---|---|---|
| `--v3-db` primary, `--db` alias | Only `--db` | Phase 2 |
| `--no-v5-lookup` and no V5 open | V5 lookup unconditional | Phase 2 |
| `--issue-key` explicit/assertion | Absent; regex only | Phase 2 |
| Manifest-first identity; run-dir prefix consistency only | Manifest not read; regex authoritative | Phase 2 |
| Legacy manifest `source` compatibility | Manifest not read | Phase 2 |
| `run_report_uri` run-identity lookup | Absent | Phase 2 |
| Page coverage strict/legacy policy | Absent; observed page count | Phase 2 |
| `source_documents.page_count = pdf_pages` | Uses observed page count | Phase 2 |
| `run_status = partial` for failed pages | Always `completed` | Phase 2 |
| Evidence fingerprint | Absent; row-count only | Phase 3 |
| Stable single-run JSON result/`already_present` | Partial; no `already_present`/coverage | Phase 2 |
| Source URL/size/coverage persistence | NULL URL; no coverage/config enrichment | Phase 2 |
| Idempotency for same run identity | Row-count insert-if-absent | Phase 2/3 |

### 3.3 OCR

| V2.4 requirement | Current state | Required work |
|---|---|---|
| Full-page `documents.csv` records for V3 | Only TB pages; non-TB pages skipped | Phase 2 additive OCR mode |
| Explicit failed/skipped page records | Absent | Phase 2 |
| `run_report.pdf_pages` available | Present (`len(doc)`) | Use as authoritative total |
| V5 default output unchanged | Present | Preserve |

### 3.4 Tests / docs

| V2.4 requirement | Current state | Required work |
|---|---|---|
| Importer V5 regression tests | 4 helper-level tests; no CLI target tests | Phase 1/2 |
| V3 orchestration tests | None | Phase 2 |
| Replay page coverage tests | None | Phase 2 |
| Fingerprint/no-clobber/lock/JSON tests | None | Phase 3 |
| Docs for V5/V3/OCR-only/replay-only | V5 only in `IMPORT_ISSUE.md`; V3 spec exists | Phase 2/4 |

---

## 4. Test baseline (before implementation)

Full suite command:

```bash
python3 -m unittest discover -s tests -p 'test*.py' -v
```

Result:

```text
Ran 252 tests in 17.175s
FAILED (failures=1, skipped=4)
```

Pre-existing failure:

```text
workbench_scaffold.test_workbench.ReviewWriteTests
  .test_standard_item_dropdown_excludes_inactive_items
AssertionError: False is not true
```

This is a Workbench data-state test unrelated to the importer/replay scope. It must be recorded but not modified in Phase 0/1.

Targeted baseline:

```bash
python3 -m unittest \
  tests.test_import_issue \
  tests.database.test_v3_replay_idempotency \
  tests.database.test_v3_constraints -v
```

Result:

```text
Ran 30 tests in 1.709s
OK
```

No OCR, download or operational-database write was performed during Phase 0.

---

## 5. Confirmed invariants and constraints

- V5 path semantics are exactly as documented in §2.5; preserve them.
- The V3 schema supports `run_status = 'partial'`, `page_extractions.ocr_status = 'failed' | 'skipped'`, and additive `config_json` metadata. No V3 migration is required for V2.4.
- `source_documents.page_count` can be corrected without schema change; it has a positive-value check.
- `source_documents` and `gazette_issues` rows are currently insert-only in replay; V2.4 needs controlled `COALESCE`-style URL backfill and page-count correction.
- The existing 14 legacy runs cannot satisfy strict full-page coverage because their OCR output only contains TB pages. They must use the legacy `legacy_partial`/`unknown` policy; do not invent missing pages or rows.
- The operational DB has real statements/reviews/mappings/publications; integration tests must use an isolated DB copy.
- `macau_tb_ocr.py` must gain an additive full-page-record mode before strict V3 coverage can pass. This is an OCR output change only, not a model/threshold change.
- V5 default behaviour and path resolution must remain unchanged, including HTTP/localhost test support.

---

## 6. Proposed phase handoff

### Phase 1 — V5-safe refactor

- Split download/OCR/V5 import/manifest writing into testable functions without behaviour change.
- Add target parsing with V5 default, but do not implement V3 execution yet.
- Preserve cwd-based V5 `--db`/`--input-dir`, ROOT-derived default outdir, cwd-based explicit relative outdir, child `cwd=ROOT`.
- Route manifest writes through one writer; implement atomic write once.
- Run the full existing suite and compare with the Phase 0 baseline.

### Phase 2 — V3 raw ingestion

- Add `--target v3`, `--v3-db`, `--replay-only`, duplicate/version flags, `--reason`, `--issue-key`, local-source/output overrides and `--json`.
- Add the additive OCR full-page-record mode and strict page-coverage validation.
- Implement duplicate/version gate before promotion; no-clobber promotion.
- Update replay for `--v3-db`, `--no-v5-lookup`, `--issue-key`, manifest identity, `run_report_uri` lookup, page coverage, `page_count = pdf_pages`, partial status, provenance and `already_present`.
- Implement legacy `legacy_partial`/`unknown` backfill policy.
- Keep all raw rows unassigned.

### Phase 3 — Integrity, security and concurrency

- Evidence fingerprint and same-count change detection.
- No-clobber PDF promotion and cross-DB input race test.
- Atomic manifest failure-injection tests.
- URL allowlist/HTTPS/redirect/address checks, managed paths, symlink rejection.
- V3 import lock with `realpath` identity.
- Strict JSON contract, job-ID/result-directory collision handling.
- Full security/concurrency tests; V5 tests remain green.

### Phase 4 — Isolated integration test

- Copy the operational V3 DB to an isolated test database.
- Ingest one new Gazette PDF end-to-end.
- Verify page coverage, provenance, unassigned rows, duplicate/retry, JSON, lock and unchanged existing records.

---

## 7. Open points requiring confirmation before Phase 2

1. **Full-page-record option name/columns.** V2.4 says “for example, `--full-page-records`”. Proposed: `--full-page-records`, adding additive `skip_reason`/`status_reason` columns to `documents.csv`. Confirm name and column set.
2. **Legacy backfill timing.** V2.4 places the `legacy_partial` page-count/coverage correction in the controlled backfill. Confirm whether it runs in Phase 2 or as a separate maintenance step after Phase 2 tests.
3. **Legacy run status.** V2.4 requires `run_status = 'partial'` when observed pages are lower than `pdf_pages` or a page failed. This will change the existing 14 rows from `completed` to `partial` during backfill. Confirm this is acceptable for Workbench/review consumers.
4. **Hard-link support.** Proposed primary promotion is `os.link` + unlink on macOS, with `O_CREAT | O_EXCL` copy fallback. Confirm the target filesystem supports hard links.
5. **Pre-existing Workbench test failure.** Recorded as baseline; not in scope for Phase 1/2. Confirm it should remain untouched.

---

## 8. Phase 0 conclusion

The V2.4 architecture is implementable without a V3 schema migration. The main new prerequisite discovered in actual code is the OCR full-page-record output: current `documents.csv` is TB-only, so strict page coverage cannot be satisfied until the additive OCR mode exists. The legacy 14 runs must follow the partial-coverage exception and must not be used to validate strict coverage.

No code was changed. Phase 1 (V5-safe refactor) can start independently; Phase 2 implementation requires the open points above to be resolved.
