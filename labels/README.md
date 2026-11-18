# Standard label lexicon

`label_lexicon.csv` maps OCR account labels to a standard government-form
vocabulary. It never replaces `label_raw`; OCR output is copied to
`label_standard` with a match status and score.

## Columns

| Column | Meaning |
|---|---|
| `label_id` | Stable id (`zh_001`, `pt_001`). |
| `label_std` | Standard label. For child entries the leading dash is not included. |
| `language` | `zh-Hant` or `pt`. |
| `level` | `parent` or `child` (dash sub-items such as 澳門元 / Patacas). |
| `aliases` | `|`-separated variants (case/accent/punctuation differences are also folded at runtime). |
| `note` | Free text for curation notes. |

## Matching

- Chinese keys fold Traditional variant characters (`帳`/`賬`, `券`/`劵`, `歷`/`歴`, …), spaces and brackets.
- Portuguese keys fold case, accents, punctuation and spacing.
- Exact hits are `exact`. Otherwise the best `difflib` ratio wins when it clears the language threshold and a margin; those are `fuzzy`.
- Fuzzy scores below 0.70 are `label_standard_low_similarity`; 0.70–0.90 are `label_standard_fuzzy_review`; only ≥0.90 are treated as low-risk candidates without a new review reason. A wrong standard name is worse than an unmatched one.

## Rebuilding

```bash
python tools/build_label_lexicon.py ground_truth/issue45_2026/gt_rows.csv \
  --out labels/label_lexicon.csv
```

The script groups case/accent/punctuation variants, prefers a mixed-case form
over all-caps, and marks dash concepts as `child`. Review the diff before
committing: the seed comes from verified GT, but GT labels can still contain
residual OCR errors.

After the v0.3.1 cleanup the remaining ≥0.90 near-duplicate pairs are genuinely
different accounts (`在本地…` vs `在外地…`, `Outra Reserva` vs `Outras reservas`),
not OCR variants.

## Measuring

```bash
python tools/gt_label_report.py ground_truth/issue45_2026 \
  --run output_v07_issue45_2026 --lexicon labels/label_lexicon.csv
```

It reports raw exact match, standardised exact match and the wrong mappings.
Measured on the 379-row verified GT: raw 79.42% → standardised 98.15% (in-sample; validate on another issue).
