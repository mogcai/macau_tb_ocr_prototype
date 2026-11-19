# SQL Audit — Macau Balancete v5

Generated: `2026-11-19T04:46:23+00:00`  
Database: `/Users/tonyfong/Projects/macau_tb_ocr_prototype/data/baseline/baseline_v5_20261118.sqlite`  
SHA-256: `a08ca1e24a5ae7cbf50067093124e7d42e5b7a2cbef36d56c6f6424586c598b6`

> This is a read-only audit. It does not migrate, normalise or approve any mapping.

## 1. Inventory

| Table / artifact | Count |
| --- | --- |
| runs | 14 |
| runs_published | 5 |
| statements | 118 |
| pages | 118 |
| line_items | 7156 |
| banks | 48 |
| bank_aliases | 3 |
| bank_name_aliases | 78 |
| standard_labels | 89 |
| standard_label_aliases | 218 |
| line_item_concepts | 216 |
| line_item_aliases | 219 |
| name_mapping_review | 154 |
| row_verification | 0 |
| name_mapping_audit | 0 |

## 2. Registry completeness

| Registry | Items | Both ZH+PT | ZH only | PT only | Neither | Used rows |
| --- | --- | --- | --- | --- | --- | --- |
| standard_labels (89 seed) | 89 | 72 | 15 | 2 | 0 | 6802 |
| line_item_concepts lex | 120 | 0 | 59 | 61 | 0 | 2215 |
| line_item_standard.csv | 65 | 55 | 4 | 6 | 0 | 0 |

- `line_item_concepts` auto-discovery: **96** items, **0** rows used; excluded from active catalogue.
- `label_lexicon.csv`: **120** entries (legacy mapping IDs).
- Reconciliation rows: **370**; `merge_candidate`: **112**, `needs_review`: **140**, `exclude_archive`: **96**.

## 3. Mapping coverage

| Run | App | Published | Rows | User mapped | User % | Concept mapped | Legacy present | Conflicts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| run_15489a6b74ffc1… | 0.6.3 | 0 | 713 | 700 | 98.18 | 0 | 686 | 11 |
| run_1a0ee38c6dba8c… | 0.7.0 | 0 | 130 | 123 | 94.62 | 0 | 65 | 2 |
| run_4a691bccd0b65f… | 0.7.0 | 0 | 129 | 125 | 96.9 | 0 | 126 | 2 |
| run_4d7d7c5203969e… | 0.7.0 | 0 | 243 | 228 | 93.83 | 0 | 226 | 3 |
| run_8b735dd8b309e9… | 0.4.2 | 1 | 65 | 62 | 95.38 | 60 | 60 | 1 |
| run_a7602c753e1ea4… | 0.4.2 | 1 | 258 | 256 | 99.22 | 252 | 252 | 4 |
| run_abf559738cde87… | 0.7.0 | 0 | 1367 | 1309 | 95.76 | 0 | 1290 | 13 |
| run_b8f7417b2f62c4… | 0.7.0 | 0 | 454 | 326 | 71.81 | 0 | 319 | 6 |
| run_d585d2517d77f8… | 0.7.0 | 0 | 517 | 470 | 90.91 | 0 | 469 | 6 |
| run_d605172cdcbc23… | 0.4.2 | 1 | 322 | 320 | 99.38 | 316 | 316 | 4 |
| run_e0f06167ebf350… | 0.4.2 | 1 | 129 | 127 | 98.45 | 125 | 125 | 2 |
| run_e8b648b9d7799c… | 0.7.0 | 0 | 1178 | 1121 | 95.16 | 0 | 1055 | 11 |
| run_eadbb15344cd15… | 0.6.4 | 0 | 64 | 64 | 100.0 | 0 | 64 | 1 |
| run_f1053163b8a35d… | 0.4.2 | 1 | 1587 | 1571 | 98.99 | 1462 | 1461 | 19 |

- Exact conflict rows after name folding: **85**.
- Partial-overlap rows (substring/qualifier, e.g. `代收帳` vs `代收帳(貸方)`): **15**; these are review items, not agreements.
- Raw concept-vs-user name mismatches before folding: **544**.
- Three-way exact conflict rows: **30**.

## 4. Entity anomalies

- Banks: **48**; flagged: **41**; serious (junk / duplicate): **13**.

| Bank ID | Name | Type | Status | Statements | Rows | Flag | Suggested |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 11 | 國泰海通證劵(澳門)一人有限公司 | bank | provisional | 1 | 64 | provisional_identity;possible_duplicate_name | review_merge |
| 22 | 郵政儲金局 | bank | provisional | 2 | 114 | provisional_identity;possible_duplicate_name | review_merge |
| 33 | The Hongkong and Shanghai Banking Corporation Limited \| | branch | approved | 2 | 131 | possible_duplicate_name | review_merge |
| 36 | 光大證劵投資服務(香港)有限公司—澳門分行 | branch | provisional | 1 | 21 | provisional_identity;possible_duplicate_name | review_merge |
| 49 | 國泰海通證劵(澳門)一人有限公司 \| 〔前稱為國泰君安證劵(澳門)一人有限公司〕 | bank | provisional | 1 | 64 | provisional_identity;possible_duplicate_name | review_merge |
| 58 | CAIXA ECONÓMICA POSTAL \| 澳門郵電CTT | bank | provisional | 1 | 57 | provisional_identity;possible_duplicate_name | review_merge |
| 69 | 光大證券投資服務(香港)有限公司—澳門分行 | branch | provisional | 2 | 43 | provisional_identity;possible_duplicate_name | review_merge |
| 91 | The Hongkong and Shanghal Banking Corporation Limited \| | branch | provisional | 1 | 65 | provisional_identity;possible_duplicate_name | review_merge |
| 112 | 南光石油化工有限公司 \| 《氹仔客運碼頭油庫運營》合同項目 | bank | provisional | 1 | 6 | junk_or_non_bank_name;provisional_identity | exclude_or_reclassify |
| 113 | 386 # 17H - 2026 #: 4/ 29 H \| Banco de Construção da Ch | branch | provisional | 1 | 38 | junk_or_non_bank_name;provisional_identity | exclude_or_reclassify |
| 114 | 548 # 17 # - 2026 : 4 / 29 H \| DEMONSTRAÇÃO DA POSIÇÃO | bank | provisional | 1 | 40 | junk_or_non_bank_name;provisional_identity | exclude_or_reclassify |
| 115 | Lista de detentores de mais de 5% do capital da institu | bank | provisional | 1 | 20 | junk_or_non_bank_name;provisional_identity | exclude_or_reclassify |
| 116 | Relatorio do Auditor Independente sobre as \| Demonstrac | bank | provisional | 1 | 6 | junk_or_non_bank_name;provisional_identity | exclude_or_reclassify |

## 5. Statement anomalies

- Statements: **118**; flagged: **104**; serious: **16**; legacy published: **38**.

| Statement | Bank | Date | Rows | Mapped | Published | Flag |
| --- | --- | --- | --- | --- | --- | --- |
| run_b8f7417b2f62c49f5846… | 386 # 17H - 2026 #: 4/ 29 H \| Banco | 2025-12-31 | 38 | 0 | 0 | bank_identity:junk_or_non_bank_name;provisional_identity;low_mapping_ratio |
| run_b8f7417b2f62c49f5846… | 548 # 17 # - 2026 : 4 / 29 H \| DEMO | 2025-12-31 | 40 | 3 | 0 | bank_identity:junk_or_non_bank_name;provisional_identity;low_mapping_ratio |
| run_b8f7417b2f62c49f5846… | 南光石油化工有限公司 \| 《氹仔客運碼頭油庫運營》合同項目 | 2025-12-31 | 6 | 0 | 0 | bank_identity:junk_or_non_bank_name;provisional_identity;low_mapping_ratio |
| run_b8f7417b2f62c49f5846… | Lista de detentores de mais de 5% d | 2026-04-29 | 20 | 0 | 0 | bank_identity:junk_or_non_bank_name;provisional_identity;low_mapping_ratio |
| run_b8f7417b2f62c49f5846… | Relatorio do Auditor Independente s | 2026-04-29 | 6 | 0 | 0 | bank_identity:junk_or_non_bank_name;provisional_identity;low_mapping_ratio |
| run_abf559738cde877a16c3… | The Hongkong and Shanghal Banking C | 2026-03-31 | 65 | 64 | 0 | bank_identity:provisional_identity;possible_duplicate_name |
| run_abf559738cde877a16c3… | 光大證券投資服務(香港)有限公司—澳門分行 | 2026-03-31 | 21 | 19 | 0 | bank_identity:provisional_identity;possible_duplicate_name |
| run_abf559738cde877a16c3… | 郵政儲金局 \| CAIXA ECONÓMICA POSTAL | 2026-03-31 | 57 | 57 | 0 | bank_identity:provisional_identity;possible_duplicate_name |
| run_15489a6b74ffc1573d0b… | 國泰海通證劵(澳門)一人有限公司 \| 〔前稱為國泰君安證劵(澳門)一人 | 2026-06-30 | 64 | 64 | 0 | bank_identity:provisional_identity;possible_duplicate_name |
| run_e8b648b9d7799c111a01… | CAIXA ECONÓMICA POSTAL \| 澳門郵電CTT | 2026-06-30 | 57 | 57 | 0 | bank_identity:provisional_identity;possible_duplicate_name |
| run_e8b648b9d7799c111a01… | The Hongkong and Shanghai Banking C | 2026-06-30 | 66 | 64 | 0 | bank_identity:possible_duplicate_name |
| run_e8b648b9d7799c111a01… | 光大證券投資服務(香港)有限公司—澳門分行 | 2026-06-30 | 22 | 21 | 0 | bank_identity:provisional_identity;possible_duplicate_name |
| run_d605172cdcbc2303d4bb… | 國泰海通證劵(澳門)一人有限公司 | 2026-09-30 | 64 | 64 | 1 | bank_identity:provisional_identity;possible_duplicate_name;legacy_published_unverified |
| run_f1053163b8a35d300de8… | The Hongkong and Shanghai Banking C | 2026-09-30 | 65 | 64 | 1 | bank_identity:possible_duplicate_name;legacy_published_unverified |
| run_f1053163b8a35d300de8… | 光大證劵投資服務(香港)有限公司—澳門分行 | 2026-09-30 | 21 | 21 | 1 | bank_identity:provisional_identity;possible_duplicate_name;legacy_published_unverified |
| run_f1053163b8a35d300de8… | 郵政儲金局 \| CAIXA ECONÓMICA POSTAL | 2026-09-30 | 57 | 57 | 1 | bank_identity:provisional_identity;possible_duplicate_name;legacy_published_unverified |

## 6. Page anomalies

- Pages: **118**; flagged: **104**; serious (OCR/format/image): **5**.

| Page | Run | PDF page | Bank | OCR | Number format | Flag |
| --- | --- | --- | --- | --- | --- | --- |
| 112 | run_b8f7417b2f62… | 29 | 南光石油化工有限公司 \| 《氹仔客運碼頭油庫運營》合同項目 | completed | ambiguous | number_format_review;statement_anomaly |
| 113 | run_b8f7417b2f62… | 211 | 386 # 17H - 2026 #: 4/ 29 H \| | completed | ambiguous | number_format_review;statement_anomaly |
| 114 | run_b8f7417b2f62… | 373 | 548 # 17 # - 2026 : 4 / 29 H \| | completed | dot_comma | number_format_review;statement_anomaly |
| 115 | run_b8f7417b2f62… | 433 | Lista de detentores de mais de | completed | ambiguous | number_format_review;statement_anomaly |
| 116 | run_b8f7417b2f62… | 491 | Relatorio do Auditor Independe | completed | ambiguous | number_format_review;statement_anomaly |

## 7. Amount quality

| Statement | Bank | Rows | Debit parsed | Credit parsed | Both columns | No label | Cents mismatch |
| --- | --- | --- | --- | --- | --- | --- | --- |
| run_1a0ee38c6dba8cca3d27… | 海通銀行澳門分行 | 64 | 10 | 8 | 5 | 2 | 0 |
| run_1a0ee38c6dba8cca3d27… | 葡萄牙商業銀行有限公司澳門分行 \| BANCO COMERCIAL P | 66 | 21 | 17 | 3 | 63 | 0 |
| run_b8f7417b2f62c49f5846… | 386 # 17H - 2026 #: 4/ 29 H \| Banco | 38 | 0 | 0 | 0 | 38 | 0 |
| run_b8f7417b2f62c49f5846… | 548 # 17 # - 2026 : 4 / 29 H \| DEMO | 40 | 18 | 6 | 3 | 37 | 0 |
| run_b8f7417b2f62c49f5846… | 南光石油化工有限公司 \| 《氹仔客運碼頭油庫運營》合同項目 | 6 | 0 | 0 | 0 | 6 | 0 |
| run_b8f7417b2f62c49f5846… | 中信銀行(國際)有限公司澳門分行 | 64 | 17 | 19 | 3 | 2 | 0 |
| run_b8f7417b2f62c49f5846… | 交通銀行股份有限公司澳門分行 | 65 | 21 | 22 | 3 | 2 | 0 |
| run_b8f7417b2f62c49f5846… | 廣發銀行股份有限公司澳門分行 | 64 | 18 | 19 | 3 | 1 | 0 |
| run_b8f7417b2f62c49f5846… | 招商永隆銀行有限公司澳門分行 | 65 | 18 | 16 | 3 | 3 | 0 |
| run_b8f7417b2f62c49f5846… | 海通國際證券有限公司(澳門分行) | 23 | 6 | 6 | 1 | 14 | 0 |
| run_b8f7417b2f62c49f5846… | 華南商業銀行股份有限公司澳門分行 | 63 | 63 | 62 | 62 | 6 | 0 |
| run_b8f7417b2f62c49f5846… | Lista de detentores de mais de 5% d | 20 | 0 | 0 | 0 | 20 | 0 |
| run_b8f7417b2f62c49f5846… | Relatorio do Auditor Independente s | 6 | 0 | 0 | 0 | 6 | 0 |
| run_d585d2517d77f8e16ff3… | HANG SENG BANK LIMITED \| SUCURSAL D | 65 | 18 | 18 | 3 | 0 | 0 |
| run_d585d2517d77f8e16ff3… | 中國銀行(澳門)股份有限公司 | 65 | 20 | 24 | 3 | 11 | 0 |
| run_d585d2517d77f8e16ff3… | 中國銀行股份有限公司澳門分行 | 66 | 27 | 26 | 3 | 3 | 0 |
| run_d585d2517d77f8e16ff3… | 國泰君安證券(澳門)一人有限公司 | 64 | 10 | 7 | 1 | 0 | 0 |
| run_d585d2517d77f8e16ff3… | 星展銀行(香港)有限公司 \| 澳門分行 | 65 | 13 | 21 | 16 | 11 | 0 |
| run_d585d2517d77f8e16ff3… | 永豐商業銀行股份有限公司 \| 澳門分行 | 64 | 14 | 15 | 3 | 0 | 0 |
| run_d585d2517d77f8e16ff3… | 澳門華人銀行股份有限公司 | 66 | 20 | 19 | 3 | 2 | 0 |
| … | 另有 98 項，詳見 CSV |

- Number formats: `{'comma_dot': 7046, 'ambiguous': 70, 'dot_comma': 40}`.
- Amount mismatch rows: **0** (see `amount_mismatches.csv`).

## 8. Provenance

- PDFs hashed: **14**.
- Runs with source PDF hash recorded: **14** / 14.
- PDF hash mismatches: **0**.
- Baseline copy exists: **True**.

## 9. Immediate conclusions

1. The v5 database has no human verification rows; legacy publication is not financial verification.
2. `standard_labels` is a curated seed, not a complete bilingual catalogue.
3. `line_item_concepts` is a monolingual lexicon plus unused auto-discovery noise, not a second bilingual catalogue.
4. `label_standard_id` points to `labels/label_lexicon.csv`, not a database table; the crosswalk must keep it.
5. Rebuild is safer than in-place migration, but replay must preserve unknown parent context and legacy IDs.

## 10. Output files


