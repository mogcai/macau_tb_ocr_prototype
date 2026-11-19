# P2-C Statement Segmentation

**Date:** 2026-11-19
**Database:** `data/macau_balancete_v3.sqlite`
**Input:** 113 approved `financial_statement` pages
**Status:** statements created as `proposed`; no adoption or publication yet

## Result

| Object | Count |
|---|---:|
| Logical statements | 113 |
| Statement pages | 113 |
| Statement extractions | 113 |
| Statement rows | 7,046 |
| Extracted rows included | 7,046 |
| Extracted rows excluded (rejected pages) | 110 |
| Extracted rows unassigned | 0 |
| Current statement rows (`v_current_statement_rows`) | 0 |

`v_current_statement_rows` is intentionally empty: a statement's current row
set only exists after its adopted extraction is selected.  P2-C creates the
candidate extraction and row snapshot; adoption is a later review gate.

## Scope distribution

| Reporting scope | Statements |
|---|---:|
| bank | 45 |
| branch | 60 |
| other (securities firms) | 8 |

## Statement status

- `statement_status = proposed`
- `identity_status = proposed`
- `metadata_status = proposed`
- `adopted_statement_extraction_id = NULL`
- `statement_extractions.extraction_status = completed`

The statement rows are therefore available for validation/review but are not
yet current published data.

## Excluded issue-17 pages

The 5 rejected issue-17 pages retain their OCR evidence:

- `row_disposition = excluded_page_role`
- no `statement_id` / `statement_extraction_id`
- 110 rows total

## Raw evidence protection

`database/v3/010_extracted_row_assignment_guard.sql` replaces the broad
append-only trigger with a narrower guard:

- OCR fields remain immutable.
- `row_disposition`, `statement_id`, `statement_extraction_id` and
  `statement_order` may be assigned once from `unassigned` to `included` or
  `excluded_page_role`.
- Included -> excluded reversal is rejected.

## Rebuild / apply

```bash
python3 tools/build_statement_segmentation.py \
  --db data/macau_balancete_v3.sqlite \
  --v5-db data/baseline/baseline_v5_20261118.sqlite \
  --reviewed-csv docs/audit/page_classification_reviewed.csv \
  --plan-out docs/audit/statement_segmentation_plan.csv
```

Add `--apply --reviewer tony` to write the database.  The tool is idempotent.

## Next

1. Statement identity/row validation and adoption.
2. P1 Standard Item reconciliation and `statement_row_mappings`.
3. Workbench review.
