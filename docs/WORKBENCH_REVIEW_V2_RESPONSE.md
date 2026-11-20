# Workbench v2 Review — Verification and Resolution

**Date:** 2026-11-20
**Report reviewed:** `docs/WORKBENCH_REVIEW_V2_2026-11-20.md`
**Scope:** `workbench/`, `database/v3/015_review_extraction_binding.sql`,
Workbench regression tests, and migration tooling.

## 1. Review verification

The report's source-level findings were checked against the actual code.  The
six Workbench findings and SEC-01 were reproducible; no finding was based on a
misread function or line.

Two clarifications were added while implementing:

- **WB2-05:** the five-field verification issue applies to OCR rows written by
  `verify_row()`.  Manual inserted rows are stored in
  `statement_row_adjustments` and do not create `field_verifications` at all, so
  field-level provenance for manual rows remains a schema limitation.
- **WB2-06:** the report correctly noted that decimal-comma input was ambiguous.
  We chose an explicit input contract instead of locale guessing: dot decimal,
  optional comma thousands groups, and decimal-comma values are rejected.

## 2. Resolution by finding

### WB2-01 — Extraction version binding

**Fixed.**

- Added `statement_review_bindings` (`015_review_extraction_binding.sql`) with
  one active binding per statement and voided binding history.
- `review_rows()`, `review_queue()`, `statement_detail()` and `adopt_statement()`
  now resolve one extraction in this order: adopted extraction → active review
  binding → latest completed extraction.
- `EFFECTIVE_ROW_SQL` filters OCR `statement_rows` by that extraction, so rows
  from different attempts cannot be mixed into one metric.
- Marking reviewed binds the latest completed extraction and records it in the
  audit event.
- Adoption requires an active binding and refuses if a newer completed
  extraction exists.  The queue shows `review_outdated` and blocks the adopter.
- Reopening voids the active binding; returning an adopted statement to review
  rebinds the extraction that was adopted.
- Legacy reviewed records without a binding were backfilled only where exactly
  one completed extraction existed (45 statements), with audit events.

Tests:
`test_review_binds_extraction_and_stale_extraction_blocks_adoption`,
`test_mark_reviewed_then_adopt`,
`test_return_to_review_restores_reviewed_held_state`.

Remaining limitation: manual row inserts/excludes are statement-level overlays
and are not tied to an extraction.  They intentionally carry across attempts;
the queue continues to show them as manual rows.  Adding an extraction key to
`statement_row_adjustments` is a follow-up data-model change.

### WB2-02 — Empty amount saved as 0.00

**Fixed.**

- `_parse_money()` raises when state is `amount` and the value is empty.
- Non-amount states store `NULL` minor values, not integer `0`, for both OCR
  and manual rows.
- `EFFECTIVE_ROW_SQL` and `v_effective_statement_rows` now distinguish "no
  verification" from "verified as NULL" with `CASE WHEN EXISTS (...)`, so a
  blank state cannot fall back to the raw OCR amount.
- The row Save button validates required amounts inline before the request and
  shows server validation errors in the row instead of a generic alert.

Tests:
`test_amount_state_requires_a_value`,
`test_non_amount_state_stores_null_minor`.

### WB2-03 — Draft / merged / retired standard items

**Fixed.**

- `standard_items()` returns only `approved` items by default; the API exposes
  `include_inactive=true` for inspection.
- `approve_mapping()` and `insert_row()` re-check `item_status` on the server.
  Draft and retired items are rejected; merged items return the target item key.
- The queue counts `invalid_mapping_rows` for the effective OCR mapping and for
  manual row items, adds `invalid_standard_item` to `mapping_blockers`, and uses
  the `invalid_mapping` bucket.
- Adopted-statement metrics use the same inactive-item check.
- The row UI flags an approved/proposed mapping whose catalogue item is no
  longer approved.

Tests:
`test_standard_item_dropdown_excludes_inactive_items`,
`test_inactive_standard_items_cannot_be_mapped`.

### WB2-04 — Reload discards other unsaved rows

**Fixed.**

- Replaced the single `dirty` flag with `dirtyRows` keyed by `statement_row_id`.
- Saving a row updates that row from the server response without reloading the
  page; only that row's dirty state is cleared.
- Map, delete/remove, restore, insert, return and review actions are blocked
  while other rows have unsaved edits.
- The insert form has its own dirty flag; unrelated actions are blocked while it
  is dirty, but the insert action itself is allowed.
- `beforeunload` prompts when any edit is unsaved.
- Failed saves keep the edited values and dirty state.

Tests:
`test_review_detail_uses_per_row_dirty_guard_and_field_checks` (template
contract); browser interaction remains a manual UI check.

### WB2-05 — Save marks all five fields verified

**Fixed using field-level verification (report option A).**

- The row UI now exposes a `verified` checkbox for each of the five fields.
- The Save payload includes `verified_fields`; the API accepts it.
- The backend verifies fields the reviewer ticked **and** fields whose value
  changed (a changed value is an explicit human edit and must become effective).
- Untouched, unticked fields do not create or update verification records.
- The button is `Save & verify`, and the row help text states the rule.
- Manual rows keep checkboxes disabled because the schema stores them in
  `statement_row_adjustments`, not `field_verifications`.

Tests:
`test_verify_writes_field_verification_and_audit`.

### WB2-06 — Decimal comma and hard-coded two decimals

**Fixed.**

- `_parse_money(value, places)` accepts `1234.56`, `1,234.56`, negative values
  and zero.  It rejects empty values, decimal-comma values such as `1.234,56`,
  malformed grouping, spaces and symbols.
- Parser and formatter use the statement's `amount_decimal_places`.
- `_money()` formats with the matching number of decimal places.
- The UI states the input contract and shows `1234.56` placeholders.

Tests: `MoneyContractTests` (three cases) and
`test_statement_decimal_places_drive_parse_and_format`.

### SEC-01 — Client-supplied role and actor

**Documented / local-only.**  This remains a scaffold, not authentication.  The
README now states that role/actor headers are not trusted identities,
`/set-role` is local convenience only, and the service should bind
`127.0.0.1`.  No production authentication was added in this change.

## 3. Migration and data operations

1. `database/v3/015_review_extraction_binding.sql` creates the binding table and
   recreates `v_effective_statement_rows` with NULL-safe effective amounts.
2. `tools/backfill_workbench_review_bindings.py` is a dry-run by default.  On
   this dataset it found 45 reviewed statements, each with exactly one completed
   extraction; `--apply` created 45 active bindings and one audit event per
   binding.
3. No source PDF, OCR row, statement row, mapping or verification was modified
   by the migration.

## 4. Standard list maintenance (follow-up)

The original Workbench v1 had no way to maintain `standard_items`; a draft item
was simply invisible after the approved-only mapping filter.  This is now
covered by:

- `tools/standard_item_admin.py` — local admin CLI with `list`, `create`,
  `approve`, `retire` and `reopen`, writing `audit_events`.
- `/catalogue` — Standard List page with status/name search, usage counts,
  draft creation, approve/retire/reopen actions, and admin-only write
  capability (`catalogue`).  All other roles can read the list.
- `workbench/service.py` — shared catalogue lifecycle functions used by both the
  CLI and the Workbench API.

Retiring an item leaves existing mappings and OCR evidence intact, but those
mappings become invalid for review/adoption.  New items start as `draft` and
must be approved explicitly.

On the operational database, `user:澳門特區負債證明書` was approved through the
CLI with an audit event (`standard_item_approved`, actor `tony`) so it now
appears in the mapping dropdown.  It already existed in the catalogue as draft;
no OCR or mapping row was changed.  The corresponding row in
`labels/standard_label_by_user.cleaned.csv` was changed from `provisional` to
`approved` so the curated source agrees with the live catalogue.

The frozen v5 baseline still records this item as provisional.  A future
from-scratch v3 seed that reads the frozen baseline must either re-import the
cleaned CSV into v5 first or re-approve the item through the Standard List /
CLI; otherwise the draft status will return.

## 5. Test result

```text
.venv/bin/python -m unittest discover -s tests -t tests
Ran 252 tests
OK (skipped=2)
```

The skips are `test_duplicate_approved_item_blocks_review` (no duplicate in the
fixture) and `test_reviewed_statement_with_unresolved_mapping_can_be_reopened`
(no reviewed statement currently has unresolved mappings).
