# P1 First-Pass Standard Item Mapping

**Date:** 2026-11-19
**Standard item source:** the 89-item user-curated `standard_labels` list
**Rows processed:** 7,046 statement rows (active + held)
**Mapping approval:** none — every mapping is `proposed`

## Seeded catalogue

| Object | Count |
|---|---:|
| `standard_items` | 89 |
| approved items | 86 |
| draft/provisional items | 3 |
| `standard_item_aliases` | 218 |
| approved aliases | 186 |
| proposed aliases | 32 |

The 65-item draft catalogue and 120 lexicon entries were **not** merged into
this seed.  P1 reconciliation remains a separate decision.

## Auto-map result

| Row status | Rows |
|---|---:|
| `unique` | 6,796 |
| `conflict` | 65 |
| `unmatched` | 185 |

| Mapping row status | Rows |
|---|---:|
| `proposed` | 7,088 |
| `approved` | **0** |

65 conflict rows produce multiple proposed candidates sharing a
`conflict_group_id`; they are not auto-selected.

## Review file

```text
docs/audit/statement_row_mapping_review.csv
```

Columns include:

- issue / entity / statement date
- statement row / page / order
- raw label, effective label, language
- parent context and parent label
- mapping status (`unique` / `conflict` / `unmatched`)
- match method and score
- candidate standard-item keys and names
- `reviewer_decision` / `reviewer_note` (blank for review)

## Conflict / unmatched notes

- Conflicts are mainly repeated child labels such as `外幣` / `澳門元` /
  `PATACAS` where parent context is unknown. The source contains multiple
  standard child items with the same name under different parents.
- Unmatched rows are mostly OCR garbage, amount-only rows, or combined
  Portuguese/Chinese labels that still do not map after script splitting.
- No unmatched row was forced onto a standard item.

## Workbench integration

Workbench Review detail now shows:

- proposed standard item for `unique` rows;
- candidate count and candidate keys for `conflict` rows;
- `unmatched` status where there is no candidate;
- a standard-item selector and **Approve** action for reviewers.

`GET /api/review/{statement_id}/rows` includes `mapping_status`,
`standard_item_key`, `standardised_label` and `mapping_candidates`.

## Important

- `statement_row_mappings.mapping_status` is `proposed` only.
- Do not treat a unique auto-map as human approval.
- Selected reviewer decisions will need a separate approval step that writes
  `mapping_status='approved'` with reviewer/time and audit evidence.
