# SQLite 整合資料庫（v0.5.0 / schema v4）

呢份文件講清楚點解唔直接用
`publication_date, statement_date, bank_name, parent_label_standard, label_standard`
做 primary key，以及實際 schema、匯入方法同分析 view。

## 一句結論

你提出嘅五欄 key **適合作為業務／分析維度，但唔適合做 primary key**。
資料庫會用 surrogate key（`line_item_id`）保存每一行 OCR observation，
另外用 `statement_id + label_path + occurrence` 做 natural key；
你嗰五欄會保留，並透過 `v_natural_key_duplicates` 顯示重複情況。

## 為何五欄 key 不夠唯一

用 issue 45 / issue 44 的實際輸出驗證：

| 問題 | 實際情況 |
|---|---|
| 同一 statement 有重複 label | issue 45 有 5 組（例如「代收帳」兩次），其中部分係 OCR 誤配到同一標準 label |
| 空白 `label_standard` | issue 45 有 126 行；page 20 葡文頁有 62 行中葡合併 label 未能標準化 |
| 一間銀行多頁 | 同一 statement 可以橫跨多頁；用 page 做 identity 會斷開 |
| `bank_name` 不穩定 | 中文／葡文／縮寫／`|`／全形空格／重音差異 |
| `publication_date` 缺失 | issue 45 有 13/26 頁原本空白，要由同一 PDF 其他頁推斷 |
| 銀行名在 table image 之外 | issue 45 page 17 的 logo 在 table crop 上方；`--header-fallback auto` 已可從完整頁首恢復 |
| 同一期可能有多次 OCR run | 新 run 不應靜默覆蓋舊 observation |
| `label_standard` 與語言綁定 | `現金` 與 `Caixa` 是兩個標準 label；跨語言比較需要 concept mapping |

所以：

- **Primary key**：`line_item_id`（surrogate，穩定、可 join）。
- **Statement identity**：`statement_id = run_id + ":" + paper_key`。
- **Paper key**：`publication_date | statement_date | bank_key`。
- **Row natural key**：`statement_id + label_path + occurrence`。
- **你提議的五欄**：保留在 view／欄位，重複時由 `occurrence` 區分，並在
  `v_natural_key_duplicates` 列出，唔會靜默合併。

`occurrence` 是同一 statement 內同一 `label_path` 的第幾次出現（1 起）。
`label_path` 比 `parent_label_standard + label_standard` 更強，因為它保留完整父子路徑；
兩個不同父項下的「澳門元」不會碰撞。

## Schema

```text
runs              一次 OCR 輸出（run_id = 內容 hash）
  ├─ pages        逐頁 documents.csv 原始 metadata（image、OCR status 等）
  └─ statements   一個 bank + publication_date + statement_date 的報表
       └─ line_items   逐行 OCR observation（surrogate PK）
            └─ row_verification   人手核對狀態（同 OCR 資料分開）

banks             正規化銀行實體（bank_key）
bank_aliases      alias → canonical bank_key（可選 CSV）
```

重要欄位：

| Table | 欄位 | 用途 |
|---|---|---|
| `runs` | `run_id`, `run_dir`, `source_pdf`, `source_pdf_sha256`, `source_pdf_sha256_source`, `app_version`, `imported_at`, `is_published`, `published_at`, `published_by` | 保存每次 OCR run、provenance 及發布狀態 |
| `pages` | `pdf_page`, `image_path`, `ocr_status`, `ocr_languages`, `document_json` | 保存每頁 `documents.csv` 原始 metadata |
| `statements` | `statement_id`, `paper_key`, `publication_date`, `publication_date_source`, `statement_date`, `statement_date_source`, `bank_id`, `identity_status`, `pdf_pages` | 一個報表 |
| `line_items` | `label_raw`, `label_standard`, `parent_label_standard`, `label_path`, `occurrence`, `natural_key_status`, `debit_amount`, `credit_amount`, `debit_cents`, `credit_cents`, `review_status` | 逐行資料及 provenance |
| `row_verification` | `line_item_id`, `status`, `verified_at`, `verified_by`, `note` | `verified` / `rejected` / `pending`，只可由人手寫入 |
| `bank_name_aliases` | `bank_id`, `raw_name`, `language`, `alias_type`, `status` | 銀行名稱 registry（approved / pending / rejected） |
| `line_item_concepts` | `concept_id`, `canonical_zh`, `canonical_pt`, `canonical_en`, `status`, `merged_into_concept_id` | 科目 concept registry |
| `line_item_aliases` | `concept_id`, `raw_name`, `language`, `alias_type`, `status` | 科目名稱 aliases |
| `name_mapping_review` | `entity_type`, `raw_name`, `suggested_entity_id`, `match_method`, `status` | 待批名稱配對 |
| `name_mapping_audit` | `entity_type`, `action`, `old_entity_id`, `new_entity_id`, `actor` | 批准／拒絕／merge audit |

`identity_status` 可能是：

- `complete`
- `missing_publication_date`
- `missing_statement_date`
- `missing_bank_name`
- `invalid_publication_date` / `invalid_statement_date`
- 多個問題以 `;` 分隔

`natural_key_status`：

- `complete`：statement 內 `label_path` 唯一。
- `duplicate`：同一 `label_path` 第二次或以上出現（`occurrence >= 2`）。
- `missing_label`：冇 `label_standard`，唔可以用嚟做科目比較。

## 日期推斷

`publication_date` 和 `statement_date` 會逐頁先讀 `documents.csv`，再讀 `tb_rows.csv`。
如果同一 run 內所有非空日期只有一個值，缺失頁會補上，並記錄：

- `documents.csv`
- `tb_rows.csv`
- `derived_from_run`
- `mixed`
- `missing`

如果同一 run 出現多個不同日期，**不會推斷**，缺失頁保持空白並在報告 warning。
不會用檔名或語言猜日期。

## 銀行正規化

`bank_key_for()` 會：

1. NFKC、全形空格轉半形、合併連續空白。
2. 移除重音（葡文）、轉細寫。
3. 移除標點及空格，只保留拉丁字母、數字、CJK。

所以 `BANCO X, S.A.`、`Banco X S.A.`、`BANCO  X, S.A.` 會得到同一個 `bank_key`。
但 `(GRUPO)`、`SUCURSAL DE MACAU`、`澳門分行` 等會保留，避免把不同法人／分行合併。

如要跨語言或跨名稱合併，提供 CSV：

```csv
alias,canonical,bank_key,note
葡萄牙商業銀行有限公司澳門分行,BANCO COMERCIAL PORTUGUÊS, S.A. SUCURSAL DE MACAU,,手動確認
```

- `alias` 和 `canonical` 都會 map 到同一個 `bank_key`（如 `bank_key` 空白，由 canonical 自動產生）。
- 如果 `bank_name` 是 `A | B` 這種 bilingual 合併名，而 A、B 兩邊都 map 到同一個 canonical bank，會用同一 `bank_key`，不會多開一個合成 name 的銀行。
- `labels/bank_aliases.csv` 目前只有一個經人手看圖確認的 bilingual seed（郵政儲金局 / CAIXA ECONÓMICA POSTAL）。
- 只合併你確認是同一個報表實體的名稱；不要為求整齊而合併集團／分行。
- Alias 改動**不會**自動改寫已存在 statement 的 `statement_id` / `bank_key`。匯入已存在 run 時會出 warning；如果同時 `--publish`，程式會拒絕發布，避免用舊 identity 當成正式 current。要套用新 mapping，應重建 DB 並重新 import 所有 run，唔好用 `--replace` 強改 identity（會令 verification fingerprint 對不上而 rollback）。

## Publish 與 current dataset

每次匯入都只是 **staging observation**。`runs.is_published` 預設 0；完成品質檢查後才用 `--publish` 將某個 run 定為該 source PDF 的正式 current。

- `v_current_*`：同一 `paper_key` 優先取已 published run；如果完全冇 published，才取最新 import（向後兼容）。
- `v_published_*`：只包含 `is_published = 1` 的 run，適合正式分析。
- `v_all_line_items`：所有 run 的 observation，包括 staging。
- `v_run_index`：列出所有 run 的 published 狀態、PDF SHA-256 及設定摘要。

因此單頁測試 run 不會取代已 published 的完整 run。

`--publish` 亦會比較 TB 頁碼集合：如果新 run 缺少現有 published run 的頁數，會拒絕，必須明確用 `--force-publish`。發布識別優先使用 `source_pdf_sha256`（同一 PDF 搬去不同資料夾仍可取代舊 run），冇 hash 才 fallback `source_pdf` 路徑。

## 匯入

```bash
python tools/build_database.py \
  output_v11_issue45_2026_full output_v06_issue44_2026 \
  --db data/macau_balancete.sqlite \
  --publish --published-by reviewer

# 只有確認要移除部分頁數時：
python tools/build_database.py output_partial --db data/macau_balancete.sqlite \
  --publish --force-publish --published-by reviewer
```

- 預設 DB：`data/macau_balancete.sqlite`。
- 同一內容重複匯入會 `skipped_existing`；要覆蓋用 `--replace`。
- `--replace` 會 snapshot `row_verification`，只有同一 `statement_id + row_order` 及 OCR fingerprint 完全一致才恢復；不一致會 rollback。真的想放棄核實紀錄才加 `--discard-verification`。
- 每次匯入會寫 `<db stem>_import_report.json`。
- `--bank-aliases labels/bank_aliases.csv` 可載入人手 alias；如果已存在 run 的 alias 映射改變，報告會 warning，唔會靜默改寫舊 statement identity。

輸出摘要包括：

- runs / pages / statements / line items / banks 數量
- `complete` / `duplicate` / `missing_label` 數量
- 五欄 key 重複組數
- 日期、銀行、identity warnings

## 分析用 views

| View | 用途 |
|---|---|
| `v_page_index` | 逐頁 metadata + 對應 statement／bank／published 狀態 |
| `v_run_index` | 所有 run 的 published 狀態、PDF SHA-256、版本及行數 |
| `v_current_statements` | 同一 `paper_key` 優先取 published run，否則最新 import |
| `v_current_line_items` | 對應 current 的逐行資料 + 銀行名 |
| `v_published_statements` / `v_published_line_items` | 只含已發布 run |
| `v_all_line_items` | 所有 run 的 observation（做 before/after 比較） |
| `v_balance_sheet` | current 分析用扁平表，包含五欄 key、金額、review、verification |
| `v_published_balance_sheet` | 只含已發布 run 的扁平表 |
| `v_statement_index` | 每 statement 的行數、review 數、缺失 label、重複 key、全部及已核實總額 |
| `v_natural_key_duplicates` | 五欄 key 的重複組（唔計 `missing_label`） |
| `v_review_queue` / `v_published_review_queue` | 仍需 review 且未 `verified` 的行（current／published） |
| `v_bank_registry` / `v_line_item_concepts` | 名稱 registry 及 alias 數目 |
| `v_name_mapping_pending` | 待批名稱配對 |
| `v_line_item_concept_map` | 逐行 label 與 concept |
| `v_unmapped_line_item_names` | 仍未 approved 的名稱及出現次數 |

例子：

```sql
-- 最新一期每間銀行、每個 statement 的資料質量
SELECT publication_date, statement_date, bank_name, row_count,
       review_row_count, missing_label_rows, duplicate_key_rows
FROM v_statement_index
ORDER BY publication_date DESC, bank_name;

-- 只取正式發布、完整、已人手核對的科目
SELECT publication_date, statement_date, bank_name,
       parent_label_standard, label_standard, debit_amount, credit_amount
FROM v_published_balance_sheet
WHERE natural_key_status = 'complete'
  AND verification_status = 'verified';

-- 全部 OCR 總額 vs 已核實總額
SELECT bank_name, statement_date,
       debit_total_cents, debit_verified_cents,
       credit_total_cents, credit_verified_cents
FROM v_statement_index
WHERE is_published = 1;

-- 找出重複的五欄 key
SELECT * FROM v_natural_key_duplicates
ORDER BY publication_date DESC, bank_name;
```

**注意：** `review_status = 'auto_check_pass'` 只代表通過程式規則，不是財務核實。
財務核實必須在 `row_verification` 寫入 `verified`，並且有人對過原圖。

## 跨語言科目

目前 `label_standard` 仍然跟語言走：

- 中文：`現金`
- 葡文：`Caixa`

所以同一個經濟科目在中文銀行和葡文銀行會是兩個 label。
資料庫已保留 `label_language`，但**未**自動建立中葡 concept mapping。
下一步應由人手建立 `label_concepts.csv`（例如 `cash` → `現金` / `Caixa`），
再由 importer 加入 `label_concept_id`；未經核對前不要自動翻譯或合併。

## Schema migration

- `VERSION` 是 project version（v0.4.1）。
- `SCHEMA_VERSION` 是資料庫 schema version（現時 4），兩者獨立。
- 開啟舊 DB 時會自動 migrate：v2 加 `source_pdf_sha256`、`is_published`、`published_at`、`published_by`，並將每個 source PDF 最新的既有 run 標為 published；v3 加 `source_pdf_sha256_source`（`ocr_report` / `import_time` / `missing`）；v4 加名稱 registry 表及 `line_items` mapping 欄位。
- DB schema 比程式新時會拒絕開啟，避免舊程式寫壞新結構。

## 名稱標準化 registry

`tools/name_registry.py` 建立銀行／科目名稱主檔及 review workflow；approved alias 才會寫入 `line_items.line_item_concept_id`。完整流程見 [`NAME_STANDARDIZATION.md`](NAME_STANDARDIZATION.md)。

```bash
python tools/name_registry.py build --db data/macau_balancete.sqlite
python tools/name_registry.py list --db data/macau_balancete.sqlite
python tools/name_registry.py approve --db data/macau_balancete.sqlite \
  --id 124 --by reviewer
```

## 限制

- 不會把不同 OCR run 靜默合併；`v_current_*` 只取最新匯入。
- `identity_status` 不完整的 statement 會用 `incomplete:<run_id>:page:<n>` 作 `paper_key`，所以不會蓋過完整的 statement；分析前應先過濾 `identity_status = 'complete'`。
- 用未包含 `--header-fallback` 的舊 run 匯入時，page 17 仍會是 `missing_bank_name`；用新 run 重新 OCR 後再匯入，才會寫入 `郵政儲金局 | CAIXA ECONÓMICA POSTAL`。
- 不會用 OCR confidence、`auto_check_pass` 或借貸相等去證明財務正確。
- `line_items.debit_cents` / `credit_cents` 是精確的 minor units（例如 `910238538228.96` → `91023853822896`），只會在金額恰好是整仙時填入；`v_statement_index` 另有 `debit_total_cents` / `credit_total_cents`（所有可解析行）及 `debit_verified_cents` / `credit_verified_cents`（只計 verified 行）。
- `debit_total_approx` / `credit_total_approx` 是 `CAST(... AS REAL)` 的近似總和，只作快速檢查；精確計算用 `*_cents` 或 `debit_amount` / `credit_amount` 文字值。
- Schema 變更採「重建 DB」策略；`CREATE TABLE IF NOT EXISTS` 不會自動加欄。
