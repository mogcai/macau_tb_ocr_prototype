# Manual Adjustment Workflow — 41 Held Statements

**Date:** 2026-11-19
**Held statements:** 41 (`reviewer_decision = needs_further_review`)
**Adopted statements:** 72 (already active)

Two files are prepared.

## 1. Statement-level triage

`docs/audit/statement_manual_review.csv`

One row per held statement.  Review:

- `debit_total_display` / `credit_total_display`
- `debit_credit_difference_minor`
- `flagged_row_count` / `flagged_reasons`
- `recommended_action`

Recommended actions in this round:

| Action | Statements |
|---|---:|
| `row_adjustment` | 24 |
| `rerun_ocr` | 17 |

Fill:

- `review_action`: `row_adjustment`, `rerun_ocr`, `accept_hold`, `reject`, `needs_more_info`
- `manual_note`: reason / instruction

## 2. Row-level adjustments

`docs/audit/statement_row_adjustments.csv`

Contains the 60 rows with known missing labels or ambiguous/unreadable amounts.
Fill only for `row_adjustment` statements:

- `corrected_label`
- `corrected_debit` (display amount, e.g. `1,234.50`)
- `corrected_credit`
- `corrected_debit_value_state`
- `corrected_credit_value_state`
- `reviewer_note`
- `adjustment_status`

Allowed value states:

```text
amount / dash / blank / unreadable / not_applicable / ambiguous
```

Leave fields blank when no change is needed.

## Important rules

- Do not edit raw OCR evidence (`label_raw`, `debit_raw`, `credit_raw`).
- Corrections will be written to `field_verifications`, not into `statement_rows`.
- A row with a corrected amount should use `corrected_debit_value_state = amount`
  (or credit) so the effective value can be calculated.
- If a row is missing entirely, the MVP path is `rerun_ocr`, not manual row insertion.
- Adopted statements are already active; the 41 held statements remain proposed
  until a new validation/adoption decision is made.

## Next application step

After the two CSVs are filled:

1. Apply row corrections as `field_verifications`.
2. Recalculate statement totals from effective values.
3. Rebuild the statement validation worksheet.
4. Adopt only statements that pass the debit/credit rule and have no critical
   structural failure.
