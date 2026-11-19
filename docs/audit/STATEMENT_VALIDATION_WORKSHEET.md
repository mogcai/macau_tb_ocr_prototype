# Statement Validation / Adoption Worksheet

**File reviewed:** `docs/audit/statement_validation_decisions.csv`
**Rows:** 113 proposed statements
**Generated:** 2026-11-19
**Database:** `data/macau_balancete_v3.sqlite`

**Review status:** reviewed 2026-11-19.

| Applied decision | Statements |
|---|---:|
| `adopt` | 72 |
| `needs_further_review` | 41 |
| `v_current_statement_rows` | 4,462 rows |

The 41 held statements are covered by
`docs/audit/MANUAL_ADJUSTMENT_WORKFLOW.md`.

After the first manual adjustment round, 15 of those 41 became balanced.
See `docs/audit/MANUAL_ADJUSTMENT_ROUND2.md` and
`docs/audit/statement_validation_round2.csv`.

## Proposal summary

| Proposed validation | Statements |
|---|---:|
| `ready_for_adoption` | 76 |
| `needs_review` | 37 |

**Adoption rule (confirmed by project owner):**

- `debit_total_minor == credit_total_minor` → suggest `adopt`
- otherwise → `hold`
- critical structural failures (no rows/pages, row-count mismatch, incomplete
  extraction, unapproved entity, duplicate identity) still block adoption.

All 113 statements have matching v3 / v5 row counts.

Common non-blocking warnings:

| Flag | Statements |
|---|---:|
| `parent_context_unknown` | 105 |
| `debit_credit_mismatch` | 37 |
| `unreadable_amounts` | 21 |
| `missing_labels` | 19 |
| `ambiguous_amounts` | 18 |

**Currency / unit policy confirmed:** all 113 statements now use
`currency = MOP`, `unit_scale = 1`, with `currency_source = manual` and
`unit_scale_source = manual`.  This was applied with
`tools/set_statement_currency_policy.py` and 113 audit events.

`parent_context_unknown` remains a P1 mapping issue, not a statement identity
blocker.  The 37 `hold` statements are primarily those where debit and credit
totals differ.  `unreadable_amounts`, `missing_labels` and `ambiguous_amounts`
remain visible warnings even when totals balance.

## What `parent_context_unknown` means

The original report has a parent/child hierarchy, for example:

```text
現金
  澳門元
  外幣

AMCM 存款
  澳門元
  外幣
```

`澳門元` / `PATACAS` and `外幣` / `MOEDAS EXTERNAS` repeat under different
parents.  Without parent context, P1 cannot know whether a child row belongs to
`CASH_MOP`, `AMCM_DEPOSITS_MOP`, `DEMAND_DEPOSITS_MOP`, etc.  Auto-mapping would
risk assigning the right name to the wrong standard item.  The raw `label_path`
and `parent_label_raw` evidence still exists in `extracted_rows`/v5; P2-C did
not guess parent links.  This affects P1 mapping and dashboard grouping, not
whether the statement's debit/credit totals balance.

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

- `ready_for_adoption` means debit and credit totals are equal and no critical
  structural check failed (page/extraction/row-count/entity/identity).
- It does **not** mean the statement is financially verified or published.
  Missing labels, ambiguous/unreadable amounts and parent context remain
  visible warnings.
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
