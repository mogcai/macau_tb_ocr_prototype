# Importer V2.4 — Phase 2C.1 Checkpoint

**Date:** 2026-11-21
**Baseline:** `16864ce4c9127ed787064c3c68b9e86a29835c79`
**Status:** Phase 2C.1a corrective patch complete for review; not committed

---

## P1 findings addressed

| ID | Fix |
|---|---|
| P1-01 | Legacy runs always report `legacy_partial` when `pdf_pages` is valid; never `full` |
| P1-02 | Invalid `pdf_pages` leaves existing `page_count` untouched and records `unknown` coverage; no observed-max/1 fallback |
| P1-03 | Corrupt manifests, unsupported manifest versions and V2 manifests missing `issue_key` are rejected; only absent/legacy V5 manifests are reconstructed |
| P1-04 | Strict mode rejects blank, non-numeric, fractional and out-of-range `tb_rows.pdf_page` values |
| P1-05 | Replay-only acquires the V3 lock before manifest read and performs the read-only duplicate/version check inside that lock |
| P1-06 | Replay-only requires regular non-symlink artifacts resolving inside the run directory |
| P1-07 | `--replay-only --dry-run` performs full read-only validation, does not lock/replay/write, and failures never overwrite the manifest or create result files |
| P1-08 | Replay verifies the actual PDF SHA-256; strict runs require a readable PDF; legacy missing PDFs are marked unverified |
| P1-09 | Replay and Importer compare actual PDF page count with `run_report.pdf_pages` before coverage validation |
| P1-10 | Local PDFs use bounded streaming, 250 MB limit, SHA-256 and `%PDF-` validation |
| P1-11 | Remote/local temp names use UUIDs and exclusive create |
| P1-12 | URL policy rejects non-global addresses, non-443 ports and revalidates redirects; DNS-rebinding residual risk is documented rather than claimed as full SSRF protection |
| P1-13 | Importer validates the replay JSON contract and compares returned page coverage/run status with its own preflight values |
| P1-14 | Replay-only non-dry-run acquires the V3 lock before reading the manifest and querying duplicate state |
| P1-15 | Replay-only dry-run and preflight failures never overwrite the original manifest or create result files |
| P1-16 | Full imports reserve the run directory with an ownership record; only the reserving process may write into it |
| P1-17 | Direct replay validates all three evidence files are present, regular and non-symlink; manifest symlinks are rejected; header-only `tb_rows.csv` remains valid |
| P1-18 | V3 manifests missing version, unknown origins or reconstructed origins cannot downgrade strict validation |

---

## Additional behaviour

- Replay result now includes `pdf_hash_verified`.
- `gazette_issues.source_url` and `source_documents.source_url` remain NULL-only updates.
- Legacy `run_status` remains unchanged.
- No V3 schema change; no Workbench change.

---

## Tests

```text
Targeted importer/replay/V3/full-page/constraints   99/99 OK
Non-Workbench suite                                281/281 OK
Full suite on isolated DB copy                     321 tests, 2 known Workbench failures, 4 skipped
```

Known Workbench data-state failures remain unchanged:

```text
workbench_scaffold.test_workbench.ReviewWriteTests
  .test_standard_item_dropdown_excludes_inactive_items
workbench_scaffold.test_workbench.ReviewWriteTests
  .test_standard_item_mapping_is_visible_and_approvable
```

---

## Legacy 14-run isolated dry-run

```text
14/14 runs ok
rows_inserted = 0
rows_skipped  = 7,156
coverage      = legacy_partial (14/14)
pdf_pages     = [9, 11, 12, 15, 17, 24, 28, 32, 59, 491]
pdf_hash_verified = true (14/14)
run_status    = completed (14/14, unchanged)
```

Copy counts remained unchanged for `source_documents`, `document_pages`, `extraction_runs`, `page_extractions`, `extracted_rows`, `statements`, `statement_rows`, `statement_row_mappings` and `statement_reviews`.

---

## Residual limitation

DNS is validated before connecting and on each redirect, but `urllib` resolves the hostname again at connection time. A narrow DNS-rebinding window remains; the code documents this and does not claim complete SSRF protection.
