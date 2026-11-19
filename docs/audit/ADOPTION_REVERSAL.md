# Adoption Reversal and Adopted Review View

**Date:** 2026-11-19
**Migration:** `database/v3/013_adoption_reversal.sql`

## What changed

Adoption used to be one-way: `trg_statements_adoption_no_clear` rejected any
attempt to clear `adopted_statement_extraction_id`.

The Workbench now supports a controlled reversal:

- `POST /api/statements/{statement_id}/return-to-review`
- capability: `adopt` (adopter/admin)
- writes one audit event: `workbench_statement_returned_to_review`

The update is atomic:

1. `adopted_statement_extraction_id` becomes `NULL`.
2. `statement_status` moves from `active` to `proposed`.
3. `statement_reviews.review_status` becomes `reviewed`.

The statement then appears in the reviewed-held section of the review queue and
can be reopened for editing or adopted again under the current gate.

## Schema guards

| Trigger | Rule |
|---|---|
| `trg_statements_adoption_no_clear` | cannot clear adoption while the statement stays `active` |
| `trg_statements_active_requires_adoption` | an `active` statement must have an adopted extraction |
| `trg_statements_adoption_clear_no_publication` | a current publication must be revoked before adoption is cleared |

## Adopted statements use the new view

Adopted statements are no longer hidden from the review surface:

- `/review` has an **Adopted — current dataset** section with rows, mapping
  coverage, row issues, debit/credit difference and reviewer status.
- `/review/{statement_id}` opens the adopted statement in read-only mode with
  the same effective rows and standard-item columns.
- `/statements` and `/statements/{statement_id}` show mapping coverage and the
  proposed/approved standard item.

Editing an adopted statement returns `409` until it is returned to reviewed and
then reopened.
