# Manual Adjustment — Round 2

**Date:** 2026-11-19
**Applied corrections:** 89 field verifications from 41 held statements
**Skipped:** 19 rows/statements marked `rerun_ocr`

## Result after corrections

| Group | Statements |
|---|---:|
| Newly balanced and ready for review | 15 |
| Still unbalanced (`row_adjustment`) | 7 |
| `rerun_ocr` | 19 |

The 72 statements adopted earlier remain unchanged.

## Files

### 1. Updated validation for the 41 held statements

`docs/audit/statement_validation_round2.csv`

- 15 rows: `ready_for_adoption` / `adopt`
- 26 rows: `needs_review` / `hold`
- `manual_review_action` shows whether the statement is on the row-adjustment
  or rerun-OCR path.

### 2. Row review for the 7 still-unbalanced statements

`docs/audit/statement_row_adjustments_round2.csv`

Contains all 448 rows for:

| Issue | Entity | Difference (minor) |
|---|---|---:|
| 2026-18 | 中國銀行(澳門)股份有限公司 | 200 |
| 2026-16 | 海通銀行澳門分行 | -64,446,400 |
| 2026-32 | 創興銀行有限公司澳門分行 | 1,000,000 |
| 2026-31 | 廣發銀行股份有限公司澳門分行 | -600,000,000 |
| 2026-32 | 澳門發展銀行股份有限公司 | 1,001,547,213,830 |
| 2026-42 | 招商永隆銀行有限公司澳門分行 | -6 |
| 2026-43 | 華南商業銀行股份有限公司澳門分行 | -300 |

The file includes effective values after round-1 corrections.  Fill only rows
that are actually wrong; leave the rest blank.

Alternatively, if the remaining difference is due to poor OCR or a missing row,
set `review_action = rerun_ocr` for that statement in the round-2 validation
file instead of further row edits.

## Next step

1. Review `statement_validation_round2.csv`.
2. Mark the 15 newly balanced statements `adopt` if accepted.
3. For the 7 unbalanced statements, either fill row corrections in
   `statement_row_adjustments_round2.csv` or switch them to `rerun_ocr`.
4. The 19 rerun-OCR statements remain held until a new OCR extraction is
   generated.

No statement is adopted automatically by this round.
