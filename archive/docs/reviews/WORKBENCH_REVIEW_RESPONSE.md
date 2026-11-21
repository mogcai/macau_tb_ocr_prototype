# Workbench Review Report — Resolution

> Superseded for Workbench v2 by
> `docs/WORKBENCH_REVIEW_V2_RESPONSE.md` (2026-11-20).

**Date:** 2026-11-19
**Report addressed:** `docs/WORKBENCH_REVIEW_REPORT_2026-11-19.md`
**Scope:** Workbench v1 source review and fixes

The preliminary report was written without source access.  This document maps
its confirmed observations to the actual code and fixes.

## WB-001 — Save resets reviewed status

**Status: Fixed.**

Before:

- `workbench/service.py::verify_row` inserted a `statement_reviews` row with
  `review_status='needs_rework'` on every row save.  That made a reviewed
  statement appear to revert after Save.

After:

- `verify_row` no longer writes review state.
- If a statement is already `reviewed`, `verify_row` raises
  `StatementReviewedError` and the API returns `409 Conflict`.
- Reviewed rows are disabled in the UI; the detail page shows
  **Reopen for editing** instead of Save.
- Only the dedicated review endpoint changes review state:
  `POST /api/review/{statement_id}/review`.

Regression test:

```text
tests/workbench_scaffold/test_workbench.py::ReviewWriteTests::test_reviewed_statement_is_read_only_until_reopened
```

## WB-002 — list/detail shows old values after save/review

**Status: Fixed for both review and held-statement views.**

- Save writes `field_verifications`, not `statement_rows`.
- `review_rows` and `review_queue` both read effective values with
  `COALESCE(field_verifications..., base value)`.
- The held-statement detail endpoint (`/api/statements/{id}/rows`) was still
  reading base `statement_rows`; it now delegates to the same effective-row
  query.  This was the cause of “changes in Review have no effect in Held”.
- The UI reloads after Save and after Mark Reviewed; it does not keep a stale
  client object as the authoritative record.
- Regression test asserts that a saved label/debit/amount is returned by the
  refreshed row endpoint:

```text
tests/workbench_scaffold/test_workbench.py::ReviewWriteTests::test_saved_values_are_returned_by_detail_rows
```

## Unsaved changes before Mark Reviewed

The detail page tracks dirty inputs and blocks **Mark Reviewed** until the
changed rows are saved.  This is a UI guard; the review endpoint itself only
changes review state.

## Not a code bug: 96b81609 remains held after review

`96b81609-3028-5901-9ad9-a4e550cf5e06` (2026-03-31 創興銀行) is:

- `review_status = reviewed`
- `statement_status = proposed` (held)
- 190 field verifications
- debit `33,601,665,542.94`, credit `33,598,762,012.04`
- difference `2,903,530.90`

Reviewed is not adopted, and the statement is still unbalanced.  Adoption is
correctly blocked until the remaining difference is resolved or the statement
is rerun.

The static files `statement_validation_round2.csv` and
`statement_manual_review.csv` are CLI snapshots; Workbench does not rewrite
them.  The live source is `data/macau_balancete_v3.sqlite` and the Workbench
review queue.

## Remaining report risks

- WB-R01 reviewed read-only: covered by backend 409 + UI disable.
- WB-R03 save error handling: UI alerts and leaves the row editable on failure.
- WB-R04 unsaved changes: dirty guard added.
- WB-R05 record identity: stable `statement_row_id` / `statement_id` used
  throughout.
- WB-R10 list/filter consistency: statements list now shows `review_status`;
  review queue splits pending/reviewed.
- WB-R12 null/empty/zero: amount states are explicit; blank/dash save zero
  minor units but retain state.
- WB-R13 numeric normalization: amounts enter as display strings and are
  converted to integer minor units with a 2-decimal check.
- OCR rerun, import-batch upload, publication gate and full export remain
  outside Workbench v1.

## Test result

`220 tests OK`.
