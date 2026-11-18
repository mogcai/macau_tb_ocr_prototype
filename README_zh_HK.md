# 澳門公報 Balancete OCR Prototype v0.7.0

用途：將澳門《公報》第二組銀行／金融機構的 Balancete（試算表）圖片抽取為 Excel / CSV，並可整合成 SQLite 資料庫作跨期分析。v0.2 專為 Apple Silicon Mac 加入原生 Apple Vision OCR，並改善繁體中文及財務數字格式處理。

## v0.7.0 One-command issue import

1. `tools/import_issue.py`：URL → input PDF → OCR → output folder → SQLite → bank refresh → user standard mapping。
2. 不覆蓋 PDF/output；預設 staging。
3. 支援 `--download-only` / `--inspect-only` / `--dry-run` / `--no-db`。
4. Issue 33 實測：1 TB page、64 rows、32 review、mapping 64/64。
5. 詳見 [`IMPORT_ISSUE.md`](IMPORT_ISSUE.md)。

## v0.6.4 Import 2026 issue 31

1. Issue 31：11 pages / 713 rows，506 review（主要 low OCR confidence）。
2. User standard mapping：700/713 mapped，13 unmapped。
3. OCR repair alias 優先於錯誤 label_standard。
4. 發現國泰海通 former-name 產生重複 bank entity；已加 alias。

## v0.6.3 More standards + split combined reserve row

1. 支援 Portuguese-only parent standards（`user:pt:...`）。
2. `一般監管儲備特定監管儲備投資重估儲備` 拆成 3 個 standards。
3. `澳門特區負價證明書`→負債；`其他應付款項及應計負償`→負債；Reject rows 不再建立 standard。
4. 標準 88（85 approved + 3 provisional）；未 map 31 行 / 20 distinct。

## v0.6.2 Map obvious OCR typos / merged strings

1. 新增 `labels/user_standard_ocr_repairs.csv`，只處理高信心 OCR typo 及合併字串。
2. 例如 `CAIXA現金`→現金、`EMPRESTIMOSEMMDEDASEXTERNAS外幣借款`→外幣借款、`足期存款`→定期存款、`重佔儲備`→重估儲備。
3. 真正新科目（`NOTAS EM CIRCULAÇÃO`、`TESOURO PÚBLICO` 等）或 context 不足的名稱仍然留 unmapped。
4. 未 map：50 行 / 38 distinct。

## v0.6.1 Use edited cleaned standard CSV

1. **Read cleaned CSV**：`import_user_standard.py build --from-cleaned` 直接讀 `labels/standard_label_by_user.cleaned.csv`，保留你改過的 status 及新增標準。
2. **Raw fallback**：`label_standard` 空白時用 `label_raw` 配 standard。
3. **新增 standards**：銀行貸款、銀行結存、銀行透支、保留溢利、預付款項、總公司／關連公司欠款、儲備。
4. **Mapping**：89 行 / 70 distinct 未 map，較之前 163 行減少。
5. 詳見 [`STANDARD_LINE_ITEMS.md`](STANDARD_LINE_ITEMS.md)。

## v0.6.0 User Standard Label Import

1. **User standard import**：`labels/standard_label_by_user.csv` 匯入 `standard_labels` / `standard_label_aliases`，再 map `line_items.user_standard_label_id`。
2. **Schema v5**：新增 standard label tables 及 line item mapping 欄位。
3. **Corrections**：reserve block 錯位、AMCM parent_pt、希臘字母 Μ、賬/帳、總收人 typo 已修正；修正行標 `provisional`。
4. **Mapping**：2198 rows mapped，163 unmapped；報告見 `data/user_standard_mapping_report.csv`。
5. 詳見 [`STANDARD_LINE_ITEMS.md`](STANDARD_LINE_ITEMS.md)。

## v0.5.2 Standard Line Item Master

1. **Standard master**：`labels/line_item_standard.csv` 係人手維護的科目主檔；一個經濟科目一個 standard，中文／葡文／OCR variant 只係 aliases。
2. **Draft**：65 條 standard rows，59 條有中文、61 條有葡文、55 條 pt→zh merge；6 條 pt-only 待決定。
3. **Apply**：`tools/standard_line_items.py apply` 只套用 `status=approved`；provisional 不會自動生效。
4. **No auto concepts**：standard mode 下，未匹配的 OCR name 只會產生 `needs_new_standard` review，不再自動建立 provisional concept。
5. 詳見 [`STANDARD_LINE_ITEMS.md`](STANDARD_LINE_ITEMS.md)。

## v0.5.1 Review CSV 及銀行中文名

1. **Review CSV**：`name_registry.py export` 輸出 `data/name_review_queue.csv`，可填 `decision` / `approved_entity_id` / `note`，再用 `import-review` 套用。
2. **銀行中文名**：`labels/bank_name_translations.csv` 為 Latin-only 銀行提供 proposed 中文名；DB 填 `banks.canonical_zh` 並建立 pending `translation_seed` review。
3. **三層名稱管理**：raw OCR 名稱 → pending review → approved registry；raw 永不覆寫。
4. **Bank registry**：`banks` 加 canonical zh/pt、entity type、status；新增 `bank_name_aliases`。
5. **Line item registry**：新增 `line_item_concepts`、`line_item_aliases`，支援中葡文 aliases 及 concept merge。
6. **Bilingual candidate**：相鄰中葡行只會產生 pending candidate，批准後才合併 concept。

## v0.4.2 Provenance 及 Partial Publish 保護

1. **Partial run 保護**：`--publish` 會比較新舊 run 的 TB 頁碼集合；如果新 run 缺少已發布 run 的頁數，會拒絕，必須用 `--force-publish`。
2. **PDF hash 優先**：發布識別先用 `source_pdf_sha256`，再 fallback 路徑；同一 PDF 搬資料夾仍可正確取代舊 run。
3. **OCR-time provenance**：`macau_tb_ocr.py` 開始時計算 PDF SHA-256 及 size，寫入 `run_report.json`；DB 優先採用 `ocr_report` hash，並記錄 `source_pdf_sha256_source`。
4. **DB 約束**：加入 `CHECK` / trigger 驗證 `is_published`、`natural_key_status`、`source_pdf_sha256_source`；每個連線啟用 foreign keys。
5. **Docker**：Dockerfile 只支援 PaddleOCR，唔會包含 Apple Vision Swift worker。

## v0.4.1 資料完整性保護

1. **`--replace` 保護核實紀錄**：匯入前會 snapshot `row_verification`，只有同一 `statement_id + row_order` 及 OCR fingerprint 完全一致才恢復；不一致會 rollback，除非明確加 `--discard-verification`。
2. **Published run**：新增 `runs.is_published` 及 `--publish`。`v_current_*` 優先使用已發布 run，單頁測試 run 不會取代完整正式 run；`v_published_*` 只顯示正式發布資料。
3. **已核實總額**：`v_statement_index` 新增 `debit_verified_cents` / `credit_verified_cents`，同 OCR 總額分開。
4. **Run provenance**：run ID 改用完整 SHA-256，包含原始 PDF hash、CSV 及 `run_report.json` 設定。
5. **Schema migration**：schema version 升到 2，舊 DB 會自動加欄；既有 run 會標為 published。
6. **Bug fix**：imported run 的 `tb_pages` 改為實際 page count；alias 改動後重匯會 warning。

## v0.4.0 新增：SQLite 整合

1. **多 run 整合**：`tools/build_database.py` 可將多個 OCR 輸出資料夾匯入同一個 SQLite，保留每次 observation，不會靜默覆蓋。
2. **Key 設計**：你提出的五欄 key（publication date、statement date、bank、parent label、label）保留作 business key；資料庫用 `statement_id + label_path + occurrence` 做 natural key，因為實際資料有重複 label、空白 label 及多頁 statement。
3. **日期推斷**：`publication_date` 缺失時，只在同一 PDF 內所有非空日期一致時補上，記錄 `*_source`；有衝突則保持空白。
4. **銀行正規化**：以 `bank_key` 統一大小寫／重音／標點；可用 `bank_aliases.csv` 人手合併中文／葡文名稱。
5. **人手核實分開**：`row_verification` 表只記錄人手核實，`auto_check_pass` 不等於財務核實。
6. **頁首 fallback OCR**：`--header-fallback auto` 會喺 table image 冇銀行名／日期時，另外 render 完整 PDF 頁首再 OCR；唔會覆蓋已有名稱，evidence 存 `header_fallback_*` 及 `images/page_XXXX_header_fallback.png`。issue 45 page 17 已驗證可恢復 `郵政儲金局 | CAIXA ECONÓMICA POSTAL`。
7. 完整 schema、view 及 query 例子見 [`DATABASE.md`](DATABASE.md)。

## v0.3.1 修正

1. **收緊 fuzzy 覆核**：0.70–0.90 的標準化配對一律標 `label_standard_fuzzy_review`；低於 0.70 標 `label_standard_low_similarity`，全部都入覆核。
2. **詞庫整理**：`總收人` 併入 `總收入`；葡文 `prestadas`／`prestados` 合併；加入查/査、抵/扺、資/资 等異體字。
3. **Orphan 統計修正**：`documents.csv` 會正確顯示 orphan child 數量。
4. **GT 標籤報告**：`gt_label_report.py` 改用圖片高度配對，並列出 unmatched 及 exact/fuzzy/unmatched 統計。

## v0.3.0 重點升級

1. **標準標籤詞表**：新增 `labels/label_lexicon.csv`，OCR 標籤會對應到最相似標準名稱，寫入獨立欄位 `label_standard`；原始 `label_raw` 不變。
2. **語文正規化**：中文異體字、葡文大小寫／重音／空格差異會先正規化再比對；低信心對照會標示及入覆核。
3. **父子科目**：`-澳門元`／`-外幣`／`- Patacas` 等子項會連到上層科目，輸出 `label_role`、`parent_label_standard`、`label_path`。
4. **斷行標籤**：標籤跌落下一行時會合併比對，寫入 `label_combined_raw`，並標 `label_continuation_merged`。
5. **量度**：379 行 GT 的 raw label exact 79.95%，標準化後 **97.89%**。

## v0.2.1 重點修正

1. **Ground Truth 行配對**：只有真正缺少座標的行才可按閱讀次序配對；有座標但超出容差的行會保留為 unmatched，唔會再出現「漏行 + 多行」互相抵消而令 alignment F1 虛高。
2. **空白 vs 橫線**：`blank` 同 `dash` 分開處理，GT 建立同評分都獨立比較，唔會再把空白格當成印有橫線。
3. **第二輪金額重讀**：`--amount-second-pass ink`（預設）會對空白／低信心金額格做 3× 重讀；原 OCR 保留，採用值標 `recovered_second_pass` 並入覆核。
4. **版本及測試**：版本升到 v0.2.1，新增 GT 工具及回歸測試（82 個）。

## v0.2 重點升級

1. **Apple Vision 原生 OCR**：macOS 預設使用 `--engine vision`，毋須安裝 PaddlePaddle 或 PaddleOCR。Swift worker 會在首次執行時自動編譯並快取。
2. **分欄 OCR**：帳戶名稱欄使用繁體中文 `zh-Hant` 或葡文模型；借方、貸方數字欄獨立使用英文／數字 OCR。Header 另外辨識，用於補充銀行名稱及日期。
3. **繁體中文正規化**：使用 OpenCC `s2hk` 將常見簡體字形轉成香港繁體，寫入 `label_normalized`；`label_raw` 永遠保留原始 OCR 結果。
4. **數字格式推斷**：支援 `comma_dot`（`1,234,567.89`）及 `dot_comma`（`1.234.567,89`）。按每張 TB 的實際數字證據推斷，唔會因為葡文就假設係歐式格式。
5. **OCR 標點修復**：如果數字分組仍然符合三位分組結構，可以產生候選標準值，但會標示 `recovered_ocr_separator` 並進入覆核清單。
6. **PaddleOCR / Tesseract**：保留為可選後備及 benchmark 引擎。

## 第 45 期 PDF 的已知特徵

根據已提供的 2026 年第 45 期 PDF：

- 全份 32 頁；TB 圖片集中在第 7–32 頁。
- 26 張 TB 圖片可直接透過 PDF image xref 抽取。
- 同期包含繁體中文、葡文及中葡混合頁。
- 中文及葡文試算表不應以語言推斷數字格式；已檢視的中文第 7 頁及葡文第 10 頁均可見逗號千分位、句號小數點格式。
- 第 7 頁 OCR 測試資料包含 `119.366,280.10` 等標點混淆案例；v0.2 的數字修復測試覆蓋這類例子。

## 1. M1 Mac 安裝（推薦）

先檢查架構：

```bash
uname -m
python3 -c "import platform,sys; print(platform.machine(), sys.executable, platform.python_version())"
swift --version
```

M1 原生環境應顯示 `arm64`。如未有 Swift compiler：

```bash
xcode-select --install
```

建立環境及安裝 Python 基礎套件：

```bash
cd /path/to/macau_tb_ocr_prototype_v0.4
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

**Vision 路徑不需要：** `paddlepaddle`、`paddleocr` 或任何 API key。

檢查本機 Vision 語言：

```bash
swiftc -O vision_ocr_worker.swift -o /tmp/macau_tb_vision_worker
/tmp/macau_tb_vision_worker --list-languages | grep -E 'zh-Hant|pt-|en-'
```

需要有 `zh-Hant`、至少一個 `pt-*`、以及 `en-*`。程式唔會將 `zh-Hant` 靜默 fallback 成 `zh-Hans`。

## 2. 先做 PDF 結構檢查

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir output_inspect --pages 7-32 --inspect-only
```

檢查：
- `documents.csv`：銀行名稱、日期、頁碼、語言、圖片尺寸。
- `images/page_XXXX_table.png`：抽取的原圖是否正確。
- `run_report.json`：TB 頁數、抽圖結果。

新一期 PDF 必須重新 inspect，唔好假設 TB 一定在 7–32 頁。

## 3. Apple Vision OCR 測試

繁體中文第 7 頁：

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir outputs/runs/issue45_p07 --pages 7 \
  --engine vision --vision-level accurate --ocr-langs auto
```

葡文第 10 頁：

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir outputs/runs/issue45_p10 --pages 10 \
  --engine vision --vision-level accurate --ocr-langs auto
```

速度比較：

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir outputs/runs/issue45_p07_fast --pages 7 \
  --engine vision --vision-level fast --ocr-langs auto
```

比較 `fast` 同 `accurate` 時必須使用相同 PDF 頁及不同輸出資料夾，記錄實際時間及人工核對結果。唔好只比較 OCR confidence。

第一次執行會將 `vision_ocr_worker.swift` 編譯到 `.vision_cache/`；之後會重用編譯檔。

如果 table image 本身冇銀行名（例如 issue 45 page 17 的 logo 在 table crop 上方），`--header-fallback auto`（預設）會另外 render 完整 PDF 頁首再 OCR。可用 `--header-fallback off` 停用；恢復結果會寫入 `header_fallback_*` 欄位及 `images/page_XXXX_header_fallback.png`，唔會覆蓋原有 OCR 名稱。

## 4. 完整批次執行

完成中、葡文 smoke test 並人工抽查後：

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir output_issue45_v02 --pages 7-32 \
  --engine vision --vision-level accurate --ocr-langs auto
```

如某一期所有 TB 已確認使用英美式數字：

```bash
--number-format comma_dot
```

如確認使用歐式數字：

```bash
--number-format dot_comma
```

未確認時應保留 `auto`。不可因為文件係葡文而直接設定 `dot_comma`。

## 5. 數字正規化規則

| 原始 OCR | Page profile | 標準候選值 | 狀態 |
|---|---|---:|---|
| `1,234,567.89` | comma_dot | `1234567.89` | parsed |
| `1.234.567,89` | dot_comma | `1234567.89` | parsed |
| `119.366,280.10` | comma_dot | `119366280.10` | recovered_ocr_separator |
| `2.496,722,065.08` | comma_dot | `2496722065.08` | recovered_ocr_separator |
| `65.045.622.601.52` | comma_dot | `65045622601.52` | recovered_ocr_separator |

規則：
- 千分位和小數點的判斷以整張 TB 的數字證據為準，不使用語言作假設。
- 只在所有數字分組仍合理時修復標點。
- 修復結果必須保留原文並標示覆核，不會自動變成已驗證資料。
- 不補回缺失數字；不符合三位分組結構的字串會標記 `invalid_grouping` 或 `ambiguous_format`。
- `O/0`、`I/1` 等 OCR 字符混淆可以產生候選值，但仍會標記 `ambiguous_ocr_character`。

## 6. 輸出資料

| 檔案／欄位 | 用途 |
|---|---|
| `macau_balancete.xlsx` | Excel 工作簿 |
| `tb_rows.csv` | 每行帳戶資料 |
| `review_queue.csv` | 需人工覆核的資料 |
| `documents.csv` | 每張 TB 的銀行、日期、語言、number format profile、OCR 執行時間 |
| `ocr_raw.csv` | 原始 OCR 字串、信心值、區域、座標 |
| `images/` | 原始 TB 圖片 |
| `run_report.json` | 執行摘要 |

`tb_rows.csv` 重要欄位：

- `label_raw`：OCR 原始帳戶名稱，永不覆蓋。
- `label_normalized`：OpenCC 正規化後帳戶名稱。
- `label_normalization_status`：`converted` / `unchanged` / `opencc_missing` 等。
- `debit_raw` / `credit_raw`：OCR 原始金額字串。
- `debit_amount` / `credit_amount`：統一用句號小數點、無千分位符號的標準數值字串；Excel 另會儲存為數值 cell。
- `number_format`：`comma_dot` / `dot_comma` / `ambiguous`。
- `number_format_confidence`、`number_format_evidence`：格式判斷證據。
- `debit_parse_status` / `credit_parse_status`：解析或修復狀態。
- `review_reason` / `review_status`：覆核原因及狀態。
- `source_image`、PDF 頁數及 bbox：方便返查原圖。

Excel sheets：`TB_Rows`、`Review_Queue`、`Documents`、`OCR_Raw`。

## 6.1 整合成 SQLite

```bash
python tools/build_database.py \
  outputs/runs/issue45_2026 outputs/runs/issue44_2026 \
  --db data/macau_balancete.sqlite
```

匯入後主要 view：

- `v_statement_index`：每間銀行／每個 statement 的行數、review 數、缺失 label、重複 key、全部及已核實總額。
- `v_balance_sheet`：current 分析用扁平表，含 `verification_status`。
- `v_published_balance_sheet`：只含已 publish run 的扁平表。
- `v_natural_key_duplicates`：五欄 business key 的重複組。
- `v_review_queue`：仍需 review 且未 `verified` 的行。
- `v_run_index`：run 的 published 狀態、PDF SHA-256、版本及行數。

查詢例子：

```sql
SELECT publication_date, statement_date, bank_name, row_count,
       review_row_count, missing_label_rows
FROM v_statement_index
ORDER BY publication_date DESC, bank_name;

SELECT * FROM v_natural_key_duplicates;
```

Schema 及點解五欄 key 不能直接做 primary key，見 [`DATABASE.md`](DATABASE.md)。

## 7. 品質控制

1. `raw` 欄位永遠不可被自動改寫。
2. `recovered_ocr_separator` 必須進入 `review_queue.csv`。
3. `ambiguous_format`、`invalid_grouping`、`ambiguous_ocr_character` 必須覆核。
4. 簡轉繁只係字形正規化，唔代表帳戶文字已經正確；原始 OCR 必須保留。
5. OCR confidence 不可當成準確率。
6. 借貸兩欄相等只係輔助檢查，不代表每行帳戶正確。
7. 投產前必須人工標註繁中及葡文頁，計算帳戶名稱 exact match、借方／貸方 exact match、行欄對位準確度。

## 8. 可選 OCR 引擎

macOS 預設 `vision`。若需 PaddleOCR benchmark：

```bash
# 先按官方指引安裝適用於平台的 PaddlePaddle
python -m pip install paddlepaddle
python -m pip install -r requirements-paddle.txt
python macau_tb_ocr.py input.pdf --outdir output_paddle --pages 7 --engine paddle --device cpu
```

Tesseract：先安裝系統 Tesseract 及語言資料，再安裝：

```bash
python -m pip install -r requirements-tesseract.txt
```

## 9. 單元測試

```bash
python -m unittest discover -s tests -v
```

目前測試涵蓋日期抽取、語言判斷、三欄分組、英美／歐式數字格式、OCR 標點修復、錯誤分組拒絕及覆核規則。Swift Vision 必須在 macOS 實際編譯及執行後，先能確認當地語言支援、ROI 座標、速度及 OCR 準確度。

## 10. CLI 參數

```text
--engine vision|paddle|tesseract
--vision-level fast|accurate
--vision-minimum-text-height 0.004
--header-fallback auto|off
--ocr-langs auto|chinese_cht,pt,en
--number-format auto|comma_dot|dot_comma
--chinese-label-normalization off|s2t|s2hk
--no-repair-ocr-separators
--confidence-threshold 0.88
--inspect-only
```

完整 Agent 操作規則請閱讀 `SKILL.md`；快速入口見 `README_AGENT.md`。
