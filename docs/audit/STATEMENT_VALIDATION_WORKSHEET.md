# Statement Validation / Adoption Worksheet

**File to review:** `docs/audit/statement_validation_decisions.csv`
**Rows:** 113 proposed statements
**Generated:** 2026-11-19
**Database:** `data/macau_balancete_v3.sqlite` (read-only for this worksheet)

This worksheet is the human review gate before adopting a statement extraction.
It does **not** adopt, publish or financially verify anything.

## Summary

| Proposed validation | Statements |
|---|---:|
| `ready_for_adoption` | 71 |
| `needs_review` | 42 |

All 113 statements have matching v3 / v5 row counts.

Common non-blocking warnings:

| Flag | Statements |
|---|---:|
| `parent_context_unknown` | 105 |
| `unreadable_amounts` | 21 |
| `missing_labels` | 19 |
| `ambiguous_amounts` | 18 |

**Currency / unit policy confirmed:** all 113 statements now use
`currency = MOP`, `unit_scale = 1`, with `currency_source = manual` and
`unit_scale_source = manual`.  This was applied with
`tools/set_statement_currency_policy.py` and 113 audit events.

`parent_context_unknown` remains a P1 mapping issue, not a statement identity
blocker.  `unreadable_amounts`, `missing_labels` and `ambiguous_amounts` remain
the reasons for the 42 `needs_review` statements.

## What to review

Open the CSV and filter `proposed_validation_status = needs_review` first.
For each row, compare:

- `entity_name`, `statement_date`, `publication_date`
- `row_count` vs `legacy_row_count`
- `debit_total_display`, `credit_total_display`
- `label_missing_count`, `ambiguous_count`, `unreadable_count`
- `parent_unknown_count`
- `validation_flags` and `validation_reason`

Then fill:

- `reviewer_decision`:
  - `adopt`
  - `hold`
  - `reject`
  - `needs_further_review`
- `reviewer_note`: reason / exception

Do not edit the evidence columns.  The AI proposal and human decision must stay
separate.

## Important interpretation

- `ready_for_adoption` means the statement passed **structural** checks:
  page exists, extraction is completed, row count matches legacy evidence, no
  missing/ambiguous/unreadable amount, and identity is unique.
- It does **not** mean the statement is financially verified or published.
- `hold` / `needs_review` means at least one structural risk exists; inspect the
  flagged rows before adoption.
- Adoption only selects the current `statement_extraction_id`; publication is a
  separate quality gate.
- Issue 17's five rejected pages are not in this worksheet; their 110 OCR rows
  remain `excluded_page_role`.

## Rebuild

```bash
python3 tools/build_statement_validation_worksheet.py \
  --db data/macau_balancete_v3.sqlite \
  --v5-db data/baseline/baseline_v5_20261118.sqlite \
  --plan docs/audit/statement_segmentation_plan.csv \
  --out docs/audit/statement_validation_decisions.csv
```

After review, the adoption importer will only apply `reviewer_decision = adopt`
and will write an audit event for every adopted statement.
