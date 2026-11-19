# Workbench Row Adjustments

**Date:** 2026-11-19
**Migration:** `database/v3/012_statement_row_adjustments.sql`
**Workbench scope:** insert missed rows, exclude over-read rows, restore either

## Why a separate table

`extracted_rows` and `statement_rows` are append-only OCR evidence.  The
Workbench must not update or delete them to fix an over-read or under-read page.

`statement_row_adjustments` stores review overlays instead:

| Type | Meaning |
|---|---|
| `insert` | a manual row that OCR missed |
| `exclude` | an existing OCR row that OCR read by mistake |

Both carry actor, timestamp, reason and an `audit_events` record.  Voiding an
adjustment retains the row and its reason; it does not delete history.

## Effective rows

`v_current_statement_rows` now:

- omits OCR rows with an active `exclude` adjustment;
- adds active `insert` adjustments for adopted statements;
- keeps the original OCR rows intact in their own tables.

`v_unmapped_statement_rows` does not report an inserted row as unmapped once its
`standard_item_id` is set.

Workbench uses the same union for the review queue and review detail, so
inserted/excluded rows change row counts, totals and mapping coverage
immediately.

## Review gate

- Every effective row must have `approved`, or a single `proposed`, mapping.
- Inserted rows block the gate until a standard item is selected.
- `conflict` and `unmatched` rows still block review.
- Reviewed statements reject insert, exclude, restore and mapping changes with
  `409` until **Reopen for editing**.

## Ordering

Inserted rows use a `REAL` `display_order` between neighbouring rows
(`1.5`, `1.25`, ...).  Existing `statement_order` values are never rewritten.

## Snapshot

`tools/build_mvp_snapshot.py` copies `statement_row_adjustments` for adopted
statements.  `current_rows` in the manifest is counted from
`v_current_statement_rows`, so it includes inserts and excludes exclusions.
