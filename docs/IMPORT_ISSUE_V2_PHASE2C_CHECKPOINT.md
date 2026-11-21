# Importer V2.4 — Phase 2C Checkpoint

**Date:** 2026-11-21  
**Status:** Implementation complete for review; not committed  
**Spec:** `docs/IMPORT_ISSUE_V2_V3_INGESTION_SPEC.md` V2.4

---

## Delivered

### Replay contract

- `--v3-db` primary option; `--db` remains a deprecated alias.
- `--no-v5-lookup` skips `load_legacy_runs()` and never opens the V5 DB.
- `--issue-key` assertion; manifest is authoritative and a parseable run-directory prefix is only a consistency check.
- Run identity resolved by `run_report_uri` before V5/stable-ID fallback.
- Strict page coverage for written v2 manifests: one `documents.csv` row per `1..pdf_pages`, no duplicates, gaps or out-of-range pages.
- `source_documents.page_count = pdf_pages` for strict runs.
- `extraction_runs.run_status = partial` when `ocr_failed_pages > 0`; non-TB `skipped` pages do not make a run partial.
- Versioned evidence fingerprint in `extraction_runs.config_json`; same-row-count changes raise `EvidenceChangedError` and roll back.
- `gazette_issues.source_url` and `source_documents.source_url` backfilled only when NULL.
- Legacy runs: `legacy_partial`/`unknown` coverage, old `legacy_ocr_page_count` preserved, existing `run_status` unchanged, no fabricated pages or rows.
- Single-run JSON result includes page coverage, counts, run status, fingerprint and `already_present`.

### Importer

- `--target {v5,v3,ocr-only}`, default V5.
- V5-safe refactor from Phase 1 retained; V5 cwd-based path behaviour preserved.
- `tools/v3_ingest.py` orchestrates V3:
  - HTTPS allowlist and redirect validation;
  - private/loopback/link-local address rejection;
  - temporary download, `%PDF-` check, SHA-256 and byte size;
  - duplicate/version gate before PDF promotion and OCR;
  - atomic no-clobber promotion (`os.link`, `O_CREAT|O_EXCL` fallback);
  - `--full-page-records` OCR;
  - strict page coverage before replay;
  - atomic manifest v2;
  - V3 replay subprocess with `--v3-db`, `--issue-key`, `--no-v5-lookup`;
  - V3 import lock using `realpath` of the DB;
  - replay-only, dry-run and diagnostic modes;
  - strict `--json` result object with exit codes 0/1/2/3.
- V3 writes only raw evidence; no statements, reviews, mappings, adoption or publication.

---

## Test results

| Suite | Result |
|---|---|
| Full-page OCR tests | 12/12 OK |
| Importer + replay + V3 contract + full-page targeted | 72/72 OK |
| Non-Workbench suite | 254/254 OK |
| Full suite on isolated DB copy | 294 tests, 2 Workbench data-state failures, 4 skipped |

Known Workbench failures (not Phase 2C code):

```text
workbench_scaffold.test_workbench.ReviewWriteTests
  .test_standard_item_dropdown_excludes_inactive_items
workbench_scaffold.test_workbench.ReviewWriteTests
  .test_standard_item_mapping_is_visible_and_approvable
```

The second failure is due to the current operational DB already having an `approved` mapping where the test expects `unique/conflict`.

---

## Legacy 14-run backfill dry-run

Run against an isolated copy of `data/macau_balancete_v3.sqlite`; operational DB was not modified.

```text
14/14 runs ok
rows_inserted = 0
rows_skipped  = 7,156
coverage      = legacy_partial for all 14
run_status    = completed for all 14 (unchanged)
```

Counts before/after on the copy:

| Object | Before | After |
|---|---:|---:|
| source_documents | 14 | 14 |
| document_pages | 118 | 118 |
| extraction_runs | 14 | 14 |
| page_extractions | 118 | 118 |
| extracted_rows | 7,156 | 7,156 |
| statements | 113 | 113 |
| statement_rows | 7,046 | 7,046 |
| statement_row_mappings | 7,251 | 7,251 |
| statement_reviews | 71 | 71 |

Metadata changes:

```text
SUM(source_documents.page_count)  118 -> 762
source_documents.source_url non-null    8
gazette_issues.source_url non-null      8
```

No missing page records or extracted rows were fabricated.

---

## Known limitation / deferred

- `--json` is implemented for `--target v3`. V5 and `--target ocr-only` still use the existing human-readable output; they reject `--json` with a clear error rather than emitting mixed output. V5 JSON can be added separately without changing V5 default behaviour.
- Phase 2C does not execute the legacy backfill against the operational DB. Only the capability and an isolated dry-run are delivered.
- Workbench files and the two known Workbench data-state failures remain untouched.

---

## Review checklist

- [x] Duplicate/version gate before PDF promotion and OCR.
- [x] Replay-only does not require or validate a source URL.
- [x] Strict coverage rejects missing, duplicate and out-of-range pages.
- [x] Fingerprint detects same-row-count evidence changes.
- [x] Lock, no-clobber promotion and atomic manifest are implemented and tested.
- [x] Legacy backfill does not fabricate historical evidence.
- [x] V3 ingestion has no review/publish side effects.
