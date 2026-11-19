# One Standard Item per Statement

**Date:** 2026-11-19
**Migration:** `database/v3/014_standard_item_uniqueness.sql`

## Rule

Within one statement, an approved standard item may be used by only one row.
Proposed mappings are suggestions and may still repeat; only approved
mappings, and manually inserted rows with a selected standard item, count as
"used".

## Enforcement

| Layer | Behaviour |
|---|---|
| Workbench API | rejects a second approval with `400` and names the conflicting row |
| Workbench dropdown | hides items already approved on another row; the row's own item stays selected |
| Insert-row form | hides items already approved in the statement |
| SQLite triggers | reject duplicate approved mappings and duplicate active inserted-row items |
| Queue metric | `duplicate_mapping_items` blocks `mapping_ready` and shows a `duplicate` badge |

## Existing duplicate

The operational DB has one pre-existing duplicate approved item:

- `user:代客保管帳(貸方)` approved on rows 60 and 61 of
  `bab6706a-55cd-50b0-bd40-7ed29f9d00da`
- row 61 label is `代收帳(貸方)`, so `user:代收帳(貸方)` is the likely correct
  item

It is intentionally not auto-changed. The statement is flagged as a duplicate
and review is blocked until the reviewer corrects one of the two rows.
