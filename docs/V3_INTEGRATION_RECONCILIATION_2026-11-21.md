# V3 Integration Reconciliation Report

**Date:** 2026-11-21
**Database:** `data/macau_balancete_v3.sqlite`
**Mode:** read-only (`mode=ro`)
**Schema:** `3.0`
**Scope:** 14 issues, 113 statements, 7,046 statement rows

This report records facts and rule violations. It does not modify the database.

## Status legend

| Status | Meaning |
|---|---|
| PASS | Required structural or workflow rule is satisfied. |
| WARNING | Data is structurally present, but a workflow stage is not complete or is explicitly pending. |
| FAIL | Structural corruption, missing required link, or impossible duplicate state. |
| NOT_APPLICABLE | The check does not apply to the current row/statement state. |

## 1. Executive summary

- Raw OCR evidence and the structural V3 chain are complete and internally consistent.
- No orphan links or duplicate business keys were found.
- Workflow state is not complete: review, adoption, mapping approval, identity/metadata, and publication are not aligned.
- Statement structural audit: `113 PASS`, `0 FAIL`.
- Statement workflow audit: `0 PASS`, `113 WARNING`.
- Statement overall audit: `0 PASS`, `113 WARNING`, `0 FAIL`.
- Row audit: `329 PASS`, `2283 WARNING`, `0 FAIL`, `4434 NOT_APPLICABLE`.
- Recommended pilot: `2026-46` on an isolated database copy; do not modify the operational DB.

## 2. Volume and reconciliation

| Item | Count |
|---|---:|
| Gazette issues | 14 |
| Source documents | 14 |
| Document pages | 118 |
| Financial statement pages (approved) | 113 |
| Rejected / non-statement pages | 5 |
| Extraction runs | 14 |
| Page extractions | 118 |
| Extracted rows | 7156 |
| Included extracted rows | 7046 |
| Excluded by page role | 110 |
| Statements | 113 |
| Statement rows | 7046 |
| Current rows (adopted statements) | 2260 |
| Statement row mappings | 7251 |
| Statement reviews | 71 |
| Publication records | 0 |

Row reconciliation:

```text
7,156 extracted rows = 7,046 included + 110 excluded_page_role
7,046 included rows = 7,046 statement_rows
113 financial statement pages = 113 statements
```

## 3. Structural integrity checks

| Check | Result | Status |
|---|---:|---|
| source_documents without gazette_issue | 0 | PASS |
| document_pages without source_document | 0 | PASS |
| page_extractions without document_page | 0 | PASS |
| extracted_rows without page_extraction | 0 | PASS |
| statements without source_document | 0 | PASS |
| statement_rows without statement | 0 | PASS |
| mappings without statement_row | 0 | PASS |
| reviews without statement | 0 | PASS |
| statements without statement_extraction | 0 | PASS |
| duplicate document_key | 0 | PASS |
| duplicate issue_key | 0 | PASS |
| duplicate statement identity_key | 0 | PASS |
| duplicate statement row order | 0 | PASS |
| rows with more than one approved mapping | 0 | PASS |

## 4. Issue-level reconciliation

| Issue | Docs | Observed pages | Run-report pages | Financial pages | Statements | Included rows | Statement rows | Adopted | Reviewed | Needs rework | No review | Approved mappings | Current rows | Status | Findings |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2026-16 | 1 | 2 | 28 | 2 | 2 | 130 | 130 | 0 | 1 | 1 | 0 | 15 | 0 | WARNING | adopted 0/2; no publication; legacy coverage 2/28 pages; source_url missing |
| 2026-17 | 1 | 11 | 491 | 6 | 6 | 344 | 344 | 1 | 4 | 1 | 1 | 13 | 64 | WARNING | adopted 1/6; reviewed 5/6; no publication; legacy coverage 11/491 pages; source_url missing |
| 2026-18 | 1 | 8 | 17 | 8 | 8 | 517 | 517 | 2 | 4 | 0 | 4 | 68 | 130 | WARNING | adopted 2/8; reviewed 4/8; no publication; legacy coverage 8/17 pages; source_url missing |
| 2026-19 | 1 | 22 | 59 | 22 | 22 | 1367 | 1367 | 8 | 13 | 1 | 8 | 47 | 512 | WARNING | adopted 8/22; reviewed 14/22; no publication; legacy coverage 22/59 pages; source_url missing |
| 2026-29 | 1 | 2 | 11 | 2 | 2 | 129 | 129 | 0 | 2 | 0 | 0 | 4 | 0 | WARNING | adopted 0/2; no publication; legacy coverage 2/11 pages; source_url missing |
| 2026-30 | 1 | 4 | 17 | 4 | 4 | 243 | 243 | 1 | 3 | 0 | 1 | 15 | 65 | WARNING | adopted 1/4; reviewed 3/4; no publication; legacy coverage 4/17 pages; source_url missing |
| 2026-31 | 1 | 11 | 24 | 11 | 11 | 713 | 713 | 3 | 6 | 0 | 5 | 13 | 192 | WARNING | adopted 3/11; reviewed 6/11; no publication; legacy coverage 11/24 pages; source_url missing |
| 2026-32 | 1 | 19 | 24 | 19 | 19 | 1178 | 1178 | 4 | 11 | 3 | 5 | 109 | 249 | WARNING | adopted 4/19; reviewed 14/19; no publication; legacy coverage 19/24 pages; source_url missing |
| 2026-33 | 1 | 1 | 11 | 1 | 1 | 64 | 64 | 1 | 0 | 0 | 1 | 0 | 64 | WARNING | reviewed 0/1; no approved mappings; no publication; legacy coverage 1/11 pages; source_url missing |
| 2026-42 | 1 | 2 | 12 | 2 | 2 | 129 | 129 | 0 | 2 | 0 | 0 | 2 | 0 | WARNING | adopted 0/2; no publication; legacy coverage 2/12 pages; source_url missing |
| 2026-43 | 1 | 4 | 15 | 4 | 4 | 258 | 258 | 0 | 3 | 1 | 0 | 5 | 0 | WARNING | adopted 0/4; no publication; legacy coverage 4/15 pages; source_url missing |
| 2026-44 | 1 | 5 | 12 | 5 | 5 | 322 | 322 | 3 | 1 | 1 | 3 | 7 | 192 | WARNING | adopted 3/5; reviewed 2/5; no publication; legacy coverage 5/12 pages; source_url missing |
| 2026-45 | 1 | 26 | 32 | 26 | 26 | 1587 | 1587 | 13 | 12 | 0 | 14 | 28 | 792 | WARNING | adopted 13/26; reviewed 12/26; no publication; legacy coverage 26/32 pages; source_url missing |
| 2026-46 | 1 | 1 | 9 | 1 | 1 | 65 | 65 | 0 | 1 | 0 | 0 | 3 | 0 | WARNING | adopted 0/1; no publication; legacy coverage 1/9 pages; source_url missing |

### Provenance note

- `source_documents.page_count` in the operational DB currently equals observed pages (118 total).
- Legacy `run_report.pdf_pages` totals 762; the controlled Phase 2C legacy backfill would update `page_count` to the full PDF page count on a copy.
- `source_url` is NULL for all 14 operational documents; the controlled backfill can restore 8 URLs on a copy.
- These are provenance/backfill items, not raw-row corruption.

## 5. Statement-level reconciliation

| Issue | Statement | Entity | Type | Adopted | Review | Identity | Metadata | Rows | Current | Approved / Current | Structural | Workflow | Publication | Overall |
|---|---|---|---|---|---|---|---|---:|---:|---:|---|---|---|---|
| 2026-16 | bc19a882 | 葡萄牙商業銀行有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-16 | e0698f4d | 海通銀行澳門分行 | balance_sheet | no | needs_rework | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-17 | 189a9dc4 | 招商永隆銀行有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-17 | 3debd886 | 廣發銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-17 | 4180080f | 交通銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-17 | 8bda9f99 | 華南商業銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-17 | aca6d1e2 | 中信銀行(國際)有限公司澳門分行 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-17 | c648d7a9 | 海通國際證券有限公司(澳門分行) | balance_sheet | no | needs_rework | proposed | proposed | 23 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-18 | 22519ef8 | 永豐商業銀行股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-18 | 377a9339 | 國泰君安證券(澳門)一人有限公司 | balance_sheet | no | no_review | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-18 | 4661b298 | HANG SENG BANK LIMITED | balance_sheet | no | no_review | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-18 | 4aa389ce | 澳門華人銀行股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 66 | 66 | 0/66 | PASS | WARNING | WARNING | WARNING |
| 2026-18 | 627a4304 | 立橋銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 62 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-18 | 7477532f | 中國銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-18 | bb0e6ca6 | 星展銀行(香港)有限公司 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-18 | d1806753 | 中國銀行(澳門)股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 00ac8bd4 | STANDARD CHARTERED BANK, MACAU BRANCH | balance_sheet | yes | no_review | proposed | proposed | 67 | 67 | 0/67 | PASS | WARNING | WARNING | WARNING |
| 2026-19 | 0c469c65 | SOCIEDADE FINANCEIRA ICBC (MACAU) CAPITAL, S.A. | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-19 | 201ecc47 | BANCO INDUSTRIAL E COMERCIAL DA CHINA S.A., SUCURSAL DE MACAU | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-19 | 407b08d6 | 光大證券投資服務(香港)有限公司—澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 21 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 478e9f9a | 中國建設銀行澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 4e238faf | 中國農業銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 619ee1fa | 澳門發展銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 6b23bbd5 | 澳門商業銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 799b3ca5 | 澳門國際銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 8b9137fd | 華僑銀行(澳門)股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-19 | 96b81609 | 創興銀行有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 97f513a6 | 中國光大銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | 9ac81b14 | BANCO INDUSTRIAL E COMERCIAL DA CHINA (MACAU), S.A. | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-19 | a1ab8cb5 | The Hongkong and Shanghai Banking Corporation Limited | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | ad4799f9 | 澳門通股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | ae38af6c | 螞蟻銀行(澳門)股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | c3f3fa3e | BANCO NACIONAL ULTRAMARINO, S.A. | balance_sheet | no | reviewed | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | ca14a581 | 第一商業銀行股份有限公司 澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | ef758316 | 郵政儲金局 | balance_sheet | yes | no_review | proposed | proposed | 57 | 57 | 0/57 | PASS | WARNING | WARNING | WARNING |
| 2026-19 | f8491ee9 | BANCO INDUSTRIAL E COMERCIAL DA CHINA (MACAU), S.A. (GRUPO) | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-19 | fc02df2f | 東亞銀行 — 澳門分行 | balance_sheet | no | needs_rework | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-19 | fc680e0f | 大豐銀行股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-29 | bd8fd370 | 招商永隆銀行有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-29 | ee717e8d | 海通銀行澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-30 | 0db1f6e5 | 交通銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-30 | 5bf8179b | HANG SENG BANK LIMITED | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-30 | c6da52cc | 中信銀行(國際)有限公司 澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 51 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-30 | cad3c07f | 華南商業銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-31 | 038bfa64 | 星展銀行(香港)有限公司 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-31 | 040c2705 | 中國銀行(澳門)股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-31 | 297442fc | 國泰海通證劵(澳門)一人有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-31 | 333c5c30 | 永豐商業銀行股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-31 | 4986650b | 澳門華人銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 67 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-31 | 5414c486 | 廣發銀行股份有限公司澳門分行 | balance_sheet | no | no_review | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-31 | 6a36f82d | 中國銀行股份有限公司澳門分行 | balance_sheet | no | no_review | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-31 | 6d86e32c | 澳門國際銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-31 | 87d409ee | 中國農業銀行股份有限公司澳門分行 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-31 | c2d88cf1 | 立橋銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-31 | cc98fc54 | 第一商業銀行股份有限公司 澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 051e362c | BANCO NACIONAL ULTRAMARINO, S.A. | balance_sheet | no | reviewed | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 14bb0ecf | The Hongkong and Shanghai Banking Corporation Limited | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 16b4fdb9 | BANCO INDUSTRIAL E COMERCIAL DA CHINA S.A., SUCURSAL DE MACAU | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 2321dd86 | 光大證券投資服務(香港)有限公司—澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 22 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 2bcc18b1 | 澳門商業銀行股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-32 | 5e6eccbb | 中國光大銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 6cf1dfff | SOCIEDADE FINANCEIRA ICBC (MACAU) CAPITAL, S.A. | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 7106ce95 | STANDARD CHARTERED BANK, MACAU BRANCH | balance_sheet | no | needs_rework | proposed | proposed | 67 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 7a7dcf12 | BANCO INDUSTRIAL E COMERCIAL DA CHINA (MACAU), S.A. | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | 7b7042e9 | BANCO INDUSTRIAL E COMERCIAL DA CHINA (MACAU), S.A. (GRUPO) | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | a40bb6e8 | 螞蟻銀行(澳門)股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-32 | c08278b5 | 大豐銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | c8db3ff9 | 澳門通股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | cb926053 | 郵政儲金局 | balance_sheet | yes | no_review | proposed | proposed | 57 | 57 | 0/57 | PASS | WARNING | WARNING | WARNING |
| 2026-32 | d2dd11c4 | 創興銀行有限公司澳門分行 | balance_sheet | no | needs_rework | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | eaded8ce | 東亞銀行 — 澳門分行 | balance_sheet | no | no_review | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | f718ec7b | 華僑銀行(澳門)股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-32 | f73967c4 | 葡萄牙商業銀行有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-32 | ffc800e4 | 澳門發展銀行股份有限公司 | balance_sheet | no | needs_rework | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-33 | 40b05d3d | 中國建設銀行澳門分行 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-42 | 4f651093 | 招商永隆銀行有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-42 | 91ffca35 | 海通銀行澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-43 | 079c0c88 | 華南商業銀行股份有限公司澳門分行 | balance_sheet | no | needs_rework | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-43 | 7c324826 | 澳門華人銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 67 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-43 | cb52d8a2 | 中國農業銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-43 | e598d7b2 | 中信銀行(國際)有限公司 澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-44 | 4377f4fb | 第一商業銀行股份有限公司 澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-44 | 4562cb63 | 國泰海通證劵(澳門)一人有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-44 | cc22d04d | 中國建設銀行澳門分行 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-44 | d281ae6f | 永豐商業銀行股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-44 | f73fa03e | 交通銀行股份有限公司澳門分行 | balance_sheet | no | needs_rework | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 056497c3 | 光大證券投資服務(香港)有限公司—澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 21 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 1551d8e7 | BANCO INDUSTRIAL E COMERCIAL DA CHINA (MACAU), S.A. (GRUPO) | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | 19fbed1a | STANDARD CHARTERED BANK, MACAU BRANCH | balance_sheet | yes | no_review | proposed | proposed | 67 | 67 | 0/67 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | 1cf807d5 | 立橋銀行股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | 27e4457c | 創興銀行有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 36faad59 | 中國銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 3bdab87c | 澳門國際銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 47fdcef6 | BANCO INDUSTRIAL E COMERCIAL DA CHINA S.A., SUCURSAL DE MACAU | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 4ed5c37a | 東亞銀行 — 澳門分行 | balance_sheet | no | no_review | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 5844d5b8 | The Hongkong and Shanghai Banking Corporation Limited | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 5a81d5b6 | 螞蟻銀行(澳門)股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | 6b7e4173 | 海通國際證券有限公司(澳門分行) | balance_sheet | yes | no_review | proposed | proposed | 23 | 23 | 0/23 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | 6c6f0815 | BANCO NACIONAL ULTRAMARINO, S.A. | balance_sheet | no | reviewed | proposed | proposed | 63 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 73b5fdae | 郵政儲金局 | balance_sheet | yes | no_review | proposed | proposed | 57 | 57 | 0/57 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | 74f55bcc | BANCO INDUSTRIAL E COMERCIAL DA CHINA (MACAU), S.A. | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 7ef49d5a | 星展銀行(香港)有限公司 | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | 8e78583c | 廣發銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | 8f0d90b4 | 中國銀行(澳門)股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | a104d380 | HANG SENG BANK LIMITED | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | a4389334 | SOCIEDADE FINANCEIRA ICBC (MACAU) CAPITAL, S.A. | balance_sheet | yes | no_review | proposed | proposed | 65 | 65 | 0/65 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | ac3c619d | 華僑銀行(澳門)股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | bab6706a | 葡萄牙商業銀行有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 66 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | c57b23d6 | 中國光大銀行股份有限公司澳門分行 | balance_sheet | no | reviewed | proposed | proposed | 64 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-45 | d07bc350 | 大豐銀行股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | dec4cc99 | 澳門通股份有限公司 | balance_sheet | yes | no_review | proposed | proposed | 64 | 64 | 0/64 | PASS | WARNING | WARNING | WARNING |
| 2026-45 | ef25adbb | 澳門商業銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |
| 2026-46 | 33b17aff | 澳門發展銀行股份有限公司 | balance_sheet | no | reviewed | proposed | proposed | 65 | 0 | 0/0 | PASS | WARNING | NOT_APPLICABLE | WARNING |

### Statement findings summary

- Statements structurally PASS: 113
- Statements structurally FAIL: 0
- Statements not adopted: 77
- Statements adopted: 36
- Statements reviewed: 63
- Statements needs_rework: 8
- Statements with no review: 42
- Statements with identity proposed: 113
- Statements with metadata proposed: 113
- Statements with rows lacking any mapping: 18
- Adopted statements with 0 approved current-row mappings: 36
- Publication NOT_APPLICABLE (not adopted): 77
- Publication WARNING (adopted, not published): 36

## 6. Row-level reconciliation

| Metric | Count |
|---|---:|
| Statement rows | 7046 |
| Current rows | 2260 |
| Current rows with an approved mapping | 0 |
| Current rows without an approved mapping | 2260 |
| Rows with more than one approved mapping | 0 |
| Rows with no mapping candidate | 23 |
| Rows with a conflict group | 0 |
| Row audit PASS | 329 |
| Row audit WARNING | 2283 |
| Row audit FAIL | 0 |
| Row audit NOT_APPLICABLE | 4434 |

Full row audit CSV: `outputs/integration_audit_2026-11-21/row_audit.csv`.

### Per-statement row summary

| Issue | Statement | All rows | Current rows | Current approved | Approved mappings | Proposed | Rejected | Rows without mapping | Status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-16 | bc19a882 | 66 | 0 | 0 | 13 | 52 | 29 | 1 | WARNING |
| 2026-16 | e0698f4d | 64 | 0 | 0 | 2 | 62 | 0 | 0 | WARNING |
| 2026-17 | 189a9dc4 | 65 | 0 | 0 | 3 | 62 | 0 | 0 | WARNING |
| 2026-17 | 3debd886 | 64 | 0 | 0 | 3 | 61 | 8 | 0 | WARNING |
| 2026-17 | 4180080f | 65 | 0 | 0 | 1 | 63 | 0 | 1 | WARNING |
| 2026-17 | 8bda9f99 | 63 | 0 | 0 | 6 | 57 | 0 | 0 | WARNING |
| 2026-17 | aca6d1e2 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-17 | c648d7a9 | 23 | 0 | 0 | 0 | 18 | 0 | 5 | WARNING |
| 2026-18 | 22519ef8 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-18 | 377a9339 | 64 | 0 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-18 | 4661b298 | 65 | 0 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-18 | 4aa389ce | 66 | 66 | 0 | 0 | 66 | 0 | 0 | WARNING |
| 2026-18 | 627a4304 | 62 | 0 | 0 | 21 | 40 | 4 | 1 | WARNING |
| 2026-18 | 7477532f | 66 | 0 | 0 | 0 | 66 | 0 | 0 | WARNING |
| 2026-18 | bb0e6ca6 | 65 | 0 | 0 | 35 | 30 | 0 | 0 | WARNING |
| 2026-18 | d1806753 | 65 | 0 | 0 | 12 | 53 | 12 | 0 | WARNING |
| 2026-19 | 00ac8bd4 | 67 | 67 | 0 | 0 | 67 | 0 | 0 | WARNING |
| 2026-19 | 0c469c65 | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-19 | 201ecc47 | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-19 | 407b08d6 | 21 | 0 | 0 | 2 | 19 | 0 | 0 | WARNING |
| 2026-19 | 478e9f9a | 64 | 0 | 0 | 7 | 57 | 4 | 0 | WARNING |
| 2026-19 | 4e238faf | 64 | 0 | 0 | 1 | 63 | 0 | 0 | WARNING |
| 2026-19 | 619ee1fa | 63 | 0 | 0 | 22 | 41 | 4 | 0 | WARNING |
| 2026-19 | 6b23bbd5 | 65 | 0 | 0 | 0 | 64 | 0 | 1 | WARNING |
| 2026-19 | 799b3ca5 | 64 | 0 | 0 | 4 | 60 | 8 | 0 | WARNING |
| 2026-19 | 8b9137fd | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-19 | 96b81609 | 64 | 0 | 0 | 1 | 63 | 0 | 0 | WARNING |
| 2026-19 | 97f513a6 | 64 | 0 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-19 | 9ac81b14 | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-19 | a1ab8cb5 | 65 | 0 | 0 | 0 | 64 | 0 | 1 | WARNING |
| 2026-19 | ad4799f9 | 64 | 0 | 0 | 1 | 63 | 0 | 0 | WARNING |
| 2026-19 | ae38af6c | 64 | 0 | 0 | 1 | 63 | 1 | 0 | WARNING |
| 2026-19 | c3f3fa3e | 63 | 0 | 0 | 1 | 62 | 0 | 0 | WARNING |
| 2026-19 | ca14a581 | 66 | 0 | 0 | 5 | 61 | 0 | 0 | WARNING |
| 2026-19 | ef758316 | 57 | 57 | 0 | 0 | 57 | 0 | 0 | WARNING |
| 2026-19 | f8491ee9 | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-19 | fc02df2f | 64 | 0 | 0 | 2 | 62 | 4 | 0 | WARNING |
| 2026-19 | fc680e0f | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-29 | bd8fd370 | 65 | 0 | 0 | 1 | 64 | 0 | 0 | WARNING |
| 2026-29 | ee717e8d | 64 | 0 | 0 | 3 | 61 | 4 | 0 | WARNING |
| 2026-30 | 0db1f6e5 | 64 | 0 | 0 | 2 | 62 | 0 | 0 | WARNING |
| 2026-30 | 5bf8179b | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-30 | c6da52cc | 51 | 0 | 0 | 7 | 44 | 0 | 0 | WARNING |
| 2026-30 | cad3c07f | 63 | 0 | 0 | 6 | 57 | 12 | 0 | WARNING |
| 2026-31 | 038bfa64 | 65 | 0 | 0 | 1 | 64 | 0 | 0 | WARNING |
| 2026-31 | 040c2705 | 65 | 0 | 0 | 2 | 63 | 4 | 0 | WARNING |
| 2026-31 | 297442fc | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-31 | 333c5c30 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-31 | 4986650b | 67 | 0 | 0 | 0 | 66 | 0 | 1 | WARNING |
| 2026-31 | 5414c486 | 63 | 0 | 0 | 0 | 63 | 0 | 0 | WARNING |
| 2026-31 | 6a36f82d | 66 | 0 | 0 | 6 | 60 | 8 | 0 | WARNING |
| 2026-31 | 6d86e32c | 64 | 0 | 0 | 2 | 62 | 0 | 0 | WARNING |
| 2026-31 | 87d409ee | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-31 | c2d88cf1 | 65 | 0 | 0 | 1 | 63 | 0 | 1 | WARNING |
| 2026-31 | cc98fc54 | 66 | 0 | 0 | 1 | 65 | 0 | 0 | WARNING |
| 2026-32 | 051e362c | 63 | 0 | 0 | 0 | 63 | 0 | 0 | WARNING |
| 2026-32 | 14bb0ecf | 66 | 0 | 0 | 0 | 64 | 0 | 2 | WARNING |
| 2026-32 | 16b4fdb9 | 66 | 0 | 0 | 0 | 65 | 0 | 1 | WARNING |
| 2026-32 | 2321dd86 | 22 | 0 | 0 | 1 | 20 | 0 | 1 | WARNING |
| 2026-32 | 2bcc18b1 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-32 | 5e6eccbb | 64 | 0 | 0 | 64 | 0 | 0 | 0 | WARNING |
| 2026-32 | 6cf1dfff | 66 | 0 | 0 | 0 | 65 | 0 | 1 | WARNING |
| 2026-32 | 7106ce95 | 67 | 0 | 0 | 3 | 64 | 8 | 0 | WARNING |
| 2026-32 | 7a7dcf12 | 65 | 0 | 0 | 1 | 64 | 0 | 0 | WARNING |
| 2026-32 | 7b7042e9 | 65 | 0 | 0 | 1 | 64 | 0 | 0 | WARNING |
| 2026-32 | a40bb6e8 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-32 | c08278b5 | 64 | 0 | 0 | 1 | 63 | 0 | 0 | WARNING |
| 2026-32 | c8db3ff9 | 64 | 0 | 0 | 2 | 62 | 4 | 0 | WARNING |
| 2026-32 | cb926053 | 57 | 57 | 0 | 0 | 57 | 0 | 0 | WARNING |
| 2026-32 | d2dd11c4 | 63 | 0 | 0 | 9 | 54 | 4 | 0 | WARNING |
| 2026-32 | eaded8ce | 64 | 0 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-32 | f718ec7b | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-32 | f73967c4 | 65 | 0 | 0 | 12 | 53 | 37 | 0 | WARNING |
| 2026-32 | ffc800e4 | 65 | 0 | 0 | 15 | 49 | 9 | 1 | WARNING |
| 2026-33 | 40b05d3d | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-42 | 4f651093 | 65 | 0 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-42 | 91ffca35 | 64 | 0 | 0 | 2 | 62 | 4 | 0 | WARNING |
| 2026-43 | 079c0c88 | 63 | 0 | 0 | 1 | 62 | 0 | 0 | WARNING |
| 2026-43 | 7c324826 | 67 | 0 | 0 | 0 | 66 | 0 | 1 | WARNING |
| 2026-43 | cb52d8a2 | 64 | 0 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-43 | e598d7b2 | 64 | 0 | 0 | 4 | 60 | 0 | 0 | WARNING |
| 2026-44 | 4377f4fb | 66 | 0 | 0 | 1 | 65 | 5 | 0 | WARNING |
| 2026-44 | 4562cb63 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-44 | cc22d04d | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-44 | d281ae6f | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-44 | f73fa03e | 64 | 0 | 0 | 6 | 58 | 0 | 0 | WARNING |
| 2026-45 | 056497c3 | 21 | 0 | 0 | 1 | 20 | 0 | 0 | WARNING |
| 2026-45 | 1551d8e7 | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-45 | 19fbed1a | 67 | 67 | 0 | 0 | 67 | 0 | 0 | WARNING |
| 2026-45 | 1cf807d5 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-45 | 27e4457c | 65 | 0 | 0 | 5 | 60 | 4 | 0 | WARNING |
| 2026-45 | 36faad59 | 66 | 0 | 0 | 2 | 64 | 0 | 0 | WARNING |
| 2026-45 | 3bdab87c | 64 | 0 | 0 | 2 | 62 | 0 | 0 | WARNING |
| 2026-45 | 47fdcef6 | 64 | 0 | 0 | 2 | 62 | 8 | 0 | WARNING |
| 2026-45 | 4ed5c37a | 64 | 0 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-45 | 5844d5b8 | 65 | 0 | 0 | 0 | 64 | 0 | 1 | WARNING |
| 2026-45 | 5a81d5b6 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-45 | 6b7e4173 | 23 | 23 | 0 | 0 | 23 | 0 | 0 | WARNING |
| 2026-45 | 6c6f0815 | 63 | 0 | 0 | 0 | 63 | 0 | 0 | WARNING |
| 2026-45 | 73b5fdae | 57 | 57 | 0 | 0 | 57 | 0 | 0 | WARNING |
| 2026-45 | 74f55bcc | 64 | 0 | 0 | 4 | 60 | 8 | 0 | WARNING |
| 2026-45 | 7ef49d5a | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-45 | 8e78583c | 64 | 0 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-45 | 8f0d90b4 | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-45 | a104d380 | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-45 | a4389334 | 65 | 65 | 0 | 0 | 65 | 0 | 0 | WARNING |
| 2026-45 | ac3c619d | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-45 | bab6706a | 66 | 0 | 0 | 12 | 53 | 30 | 1 | WARNING |
| 2026-45 | c57b23d6 | 64 | 0 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-45 | d07bc350 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-45 | dec4cc99 | 64 | 64 | 0 | 0 | 64 | 0 | 0 | WARNING |
| 2026-45 | ef25adbb | 65 | 0 | 0 | 0 | 64 | 0 | 1 | WARNING |
| 2026-46 | 33b17aff | 65 | 0 | 0 | 3 | 61 | 5 | 1 | WARNING |

## 7. Pilot: 2026-46

| Field | Value |
|---|---|
| Statement ID | 33b17aff-e023-543d-b7fe-5662b52a456b |
| Entity | 澳門發展銀行股份有限公司 |
| Statement date | 2026-09-30 |
| Statement type | balance_sheet |
| Statement status | proposed |
| Identity status | proposed |
| Metadata status | proposed |
| Adopted | 0 |
| Review status | reviewed |
| Reviewed by | workbench-ui |
| Reviewed at | 2026-11-19T15:40:52+00:00 |
| Page links | 1 |
| Extractions | 1 |
| All rows | 65 |
| Current rows | 0 |
| Approved mappings | 3 |
| Proposed mappings | 61 |
| Rejected mappings | 5 |
| Rows without mapping | 1 |
| Field verifications | 4 |
| Publications | 0 |
| Structural status | PASS |
| Workflow status | WARNING |
| Publication status | NOT_APPLICABLE |
| Overall status | WARNING |
| Findings | not adopted; identity proposed; metadata proposed; rows without mapping 1 |

Pilot interpretation:

- `2026-46` is structurally complete: 1 page, 1 statement, 1 extraction, 65 rows.
- It is already reviewed (`reviewed`) and is a good candidate to validate the remaining workflow.
- It is not adopted, so `v_current_statement_rows` is empty and current-row mapping coverage is `0/0`.
- It has 3 approved mappings, 61 proposed mappings, 5 rejected candidates, and 4 field verifications on its 65 rows.
- Identity and metadata remain `proposed`; publication is `NOT_APPLICABLE` until the statement is adopted.

## 8. Key findings

1. **Structural integration is sound.** The raw evidence, page, extraction, statement-row, mapping and review links have no orphans or duplicate keys.
2. **Workflow integration is incomplete.** Only 36/113 statements are adopted; 63 reviewed statements are not adopted; 36 adopted statements have no review record.
3. **Current-row mapping approval is zero.** All 2,260 current rows have a proposed candidate, but none has an approved mapping. The 329 approved mappings belong to non-adopted statements.
4. **Identity and metadata are unresolved.** All 113 statements are still `proposed` at identity and metadata level.
5. **No publication has occurred.** `publication_records = 0`.
6. **Legacy provenance backfill is pending.** Operational DB has 118 observed pages and no `source_url`; controlled backfill on a copy would set full PDF page counts and restore 8 URLs.
7. **Mapping counts are not row coverage.** 7,251 mappings cover 7,046 rows because a row can have multiple proposed/rejected candidates and one approved mapping. Row-level coverage is the meaningful measure.

## 9. Proposed acceptance criteria

| Layer | Acceptance |
|---|---|
| Issue | One source document, expected page set, one extraction run, financial pages reconcile to statements. |
| Statement | Page link + completed extraction + identity/metadata confirmed + review + adoption + current rows. |
| Row | Raw OCR values preserved; one approved mapping or explicit rejected/exception decision; no conflict. |
| Publication | Only an adopted, reviewed extraction may be published; publish and rollback are audited. |

## 10. Recommended next step

1. Use `2026-46` as the pilot on an isolated DB copy.
2. Confirm identity/metadata and review semantics before changing any state.
3. Approve mappings and adopt the existing extraction on the copy.
4. Verify current-row coverage and publication readiness.
5. Only after pilot validation, define the rollout order for the other 13 issues.

---

Generated read-only from the operational V3 database. No database rows were changed.
