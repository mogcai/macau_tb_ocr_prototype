# P2-B Reporting Entity Reconciliation Worksheet

**Date:** 2026-11-19
**Output:** `docs/audit/reporting_entity_reconciliation.csv`
**Source:** v5 bank registry + reviewed P2-A page decisions
**Rows:** 48 legacy entities

**Status: reviewed and applied 2026-11-19.** The human-reviewed CSV was imported
with `tools/apply_entity_reconciliation_decisions.py`:

- 38 active approved entities
- 5 merged legacy entities (with `merged_into_entity_id`)
- 5 excluded non-entities (no v3 entity row)
- 57 approved entity aliases
- 48 entity audit events

The original proposal columns are preserved.

## Proposal summary

| Proposed action | Entities |
|---|---:|
| `keep_approved` | 7 |
| `keep_survivor` | 4 |
| `approve_provisional` | 27 |
| `merge_into_survivor` | 5 |
| `exclude_non_entity` | 5 |

## Duplicate groups

| Group | Survivor | Merge candidates | Evidence |
|---|---|---|---|
| `G11_49_105` | 11 國泰海通證劵(澳門)一人有限公司 | 49, 105 | 49 is the same name with former-name note; 105 is the historical 國泰君安 name |
| `G22_58` | 22 郵政儲金局 | 58 | P2-A reviewer selected 郵政儲金局 for 2026-32 page 13 |
| `G33_91` | 33 The Hongkong and Shanghai Banking Corporation Limited | 91 | OCR typo `Shanghal` |
| `G36_69` | 69 光大證券投資服務(香港)有限公司—澳門分行 | 36 | OCR variant 證劵 vs 證券; 69 has two statements |

## Excluded non-entities

| Entity ID | Reason |
|---:|---|
| 112 | 南光石油化工 contract/project, non-bank |
| 113 | OCR header junk; page 211 was rejected in P2-A |
| 114 | OCR header junk; page 373 was rejected in P2-A |
| 115 | Shareholder list text, not an entity |
| 116 | Auditor report text, not an entity |

## Review fields (already filled in this round)

The fields below were used for the applied review:

- `reviewer_decision`:
  - `approve_entity`
  - `approve_merge`
  - `exclude_entity`
  - `keep_provisional`
  - `needs_review`
- `reviewer_survivor_entity_id`: required for `approve_merge`
- `reviewer_note`: reason / alias / scope decision

The AI proposal and human decision remain separate columns in the CSV.

## Notes

- 27 `approve_provisional` entities have at least one P2-A approved financial
  statement page.  This is evidence for identity approval, not financial
  verification of the statement.
- Securities firms are included because the P2-A reviewer explicitly approved
  them as in-scope (`需然係證券公司，但係都包括係項目範圍內`).  Their
  `entity_type` should be reviewed as `non_bank` (or a future
  `other_financial_institution`), even where v5 labelled them `bank`/`branch`.
- 113/114 are not automatically merged into Banco de Construção / Hua Nan:
  their pages were rejected in P2-A.  If scope changes, revisit from the P2-A
  worksheet first.

## Rebuild

```bash
python3 tools/build_entity_reconciliation_worksheet.py \
  --v5-db data/baseline/baseline_v5_20261118.sqlite \
  --reviewed-csv docs/audit/page_classification_reviewed.csv \
  --out docs/audit/reporting_entity_reconciliation.csv
```
