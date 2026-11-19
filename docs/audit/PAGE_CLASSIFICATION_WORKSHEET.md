# P2-A Page Classification Worksheet

**Date:** 2026-11-19
**Output:** `docs/audit/page_classification_decisions.csv`
**Database:** `data/macau_balancete_v3.sqlite` (read-only for this worksheet)
**Rows:** 118 document pages

This worksheet is a proposal only. It does not update `document_pages` and does
not approve any page. AI proposal and human decision are separate fields.

## Proposal summary

| Proposed role | Pages |
|---|---:|
| `balance_sheet` | 114 |
| `other` | 1 |
| `unknown` | 1 |
| `shareholder_list` | 1 |
| `audit_report` | 1 |

| Review status | Pages |
|---|---:|
| `ready_for_review` | 113 |
| `needs_review` | 5 |

The five `needs_review` pages are all in issue 2026-17.

## Issue 17 review notes

The audit previously described five issue-17 pages as non-financial. The page
evidence is more nuanced:

| PDF page | Proposal | Evidence |
|---:|---|---|
| 29 | `other` | 南光石油化工 contract/project page; non-bank |
| 211 | `unknown` | Header suggests Banco de Construção, but OCR labels are numeric/unreliable |
| 373 | `balance_sheet` | Header is contaminated, but first labels contain `BANCO COMERCIAL HUA NAN` and `DEMONSTRAÇÃO DA POSIÇÃO FINANCEIRA`; 21 parsed amount rows |
| 433 | `shareholder_list` | `Lista de detentores...`, `Accionistas Qualificados` |
| 491 | `audit_report` | `Relatorio do Auditor Independente...` |

**Do not exclude page 373 merely because its header is contaminated.** It is
likely a Hua Nan balance sheet and needs entity/header review.

## How to review

1. Open the corresponding PDF page and compare with `ocr_header_raw` and
   `first_labels`.
2. Fill `reviewer_decision` with one of:
   - `approve_proposed`
   - `change_role` (put the corrected role in `reviewer_note`)
   - `reject_page`
   - `needs_further_investigation`
3. Add a short reason in `reviewer_note`.
4. Do not edit `proposed_page_role` or `proposed_entity_name`; keep the AI
   proposal intact for audit.
5. Only after review should an importer write `document_pages.page_role` and
   `document_pages.classification_status`.

## Rebuild the worksheet

```bash
python3 tools/build_page_classification_worksheet.py \
  --db data/macau_balancete_v3.sqlite \
  --v5-db data/baseline/baseline_v5_20261118.sqlite \
  --out docs/audit/page_classification_decisions.csv
```

The script is read-only. It does not modify v3 or v5.
