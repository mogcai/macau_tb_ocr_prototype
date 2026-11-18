


# 澳門銀行公報試算表OCR


[詳細說明](README_zh_HK.md)

澳門特區政府公報（《澳門特別行政區公報》）銀行 Balancete（試算表）資料擷取工具，將 PDF 內的表格影像轉換成可供整理及分析的 CSV／Excel 資料。

本專案以 Python 開發，並針對澳門公報中常見的中文、葡文及英文銀行財務表格作 OCR 處理。

## 版本簡介

### v0.6.0 — User Standard Label Import

- 新增 `tools/import_user_standard.py`：將 `labels/standard_label_by_user.csv` 匯入 `standard_labels` / `standard_label_aliases`，再 map 現有 `line_items` 到 `user_standard_label_id`。
- 新增 schema v5：`standard_labels`、`standard_label_aliases`、`line_items.user_standard_label_id`、`user_standard_match_status`。
- 已修正用戶 CSV 的 reserve block 錯位、AMCM parent_pt、希臘字母 Μ、賬/帳及總收人 typo；修正行標 provisional。
- 映射結果：2198 rows mapped，163 unmapped；報告在 `data/user_standard_mapping_report.csv`。

### v0.5.2 — Standard Line Item Master

- 新增 `tools/standard_line_items.py`：由現有 registry + bilingual pairs 匯出 `labels/line_item_standard.csv`，再將 approved standard apply 回 DB。
- 新增 `labels/line_item_pt_zh_map.csv`：curated pt→zh standard proposals。
- 65 條 standard draft：59 條有中文、61 條有葡文、55 條 pt→zh merge；6 條 pt-only 待決定。
- `apply` 預設只套用 `status=approved`；provisional 不會靜默生效。
- Standard mode 下唔會再為每個 OCR variant 自動建立 provisional concept。

### v0.5.1 — Review CSV 及銀行中文名

- `name_registry.py export` 輸出 `data/name_review_queue.csv`，可直接填 `decision` / `approved_entity_id` / `note`，再用 `import-review` 匯入。
- `labels/bank_name_translations.csv` 為 Latin-only 銀行提供 proposed 中文名；DB 會填 `banks.canonical_zh` 並產生 pending `translation_seed` review。

### v0.5.0 — Name Standardization Registry

- 新增 `tools/name_registry.py`：建立 bank / line-item 的名稱標準化主檔、待審候選及 audit。
- 新增 `banks` registry 欄位、`bank_name_aliases`、`line_item_concepts`、`line_item_aliases`、`name_mapping_review`、`name_mapping_audit`。
- `line_items` 新增 `line_item_concept_id`、`mapping_status`、`mapping_version`；OCR raw 欄位永不改寫。
- 同語言 lexicon alias 會自動 approve；跨語言 bilingual pair 只會產生 pending review，批准後才合併 concept。
- 支援 `build` / `list` / `approve` / `reject` / `remap` CLI。
- 詳見 [`NAME_STANDARDIZATION.md`](NAME_STANDARDIZATION.md)。

### v0.4.2 — Provenance 及 Partial Publish 保護

- `--publish` 會比較新舊 run 的 PDF 頁碼集合；如果新 run 缺少已發布 run 的頁數，會拒絕發布，必須明確使用 `--force-publish`。
- 發布識別優先用 `source_pdf_sha256`，再 fallback `source_pdf` 路徑；同一 PDF 搬去不同資料夾仍可正確取代舊 published run。
- OCR 開始時已計算 `source_pdf_sha256` 及 `source_pdf_size`，寫入 `run_report.json`；DB 匯入優先採用 OCR-time hash，並記錄 `source_pdf_sha256_source`（`ocr_report` / `import_time` / `missing`）。如果 PDF 已被替換，會 warning。
- SQLite 加入 `CHECK` 及 trigger 驗證 `is_published`、`natural_key_status`、`source_pdf_sha256_source`；每次 DB 連線都啟用 `PRAGMA foreign_keys=ON`。
- Dockerfile 明確標示只支援 PaddleOCR，移除 Apple Vision Swift worker。

### v0.4.1 — 資料完整性保護

- `--replace` 會先保存 `row_verification`，只有在 `statement_id + row_order` 及 OCR fingerprint 完全一致時才恢復；不一致會 rollback，除非明確使用 `--discard-verification`。
- 新增 `runs.is_published` / `--publish`：`v_current_*` 優先使用已發布 run，測試用單頁 run 不會取代完整正式 run。
- `v_statement_index` 新增 `debit_verified_cents` / `credit_verified_cents`，分開顯示 OCR 總額及已核實總額。
- Run ID 改用完整 SHA-256，並納入原始 PDF SHA-256、`documents.csv`、`tb_rows.csv` 及完整 `run_report.json` 設定。
- 新增 schema migration 機制（schema v2），舊 DB 會自動加欄並把既有 run 標為 published。
- 修正 imported run 報告的 `tb_pages`（之前誤用 statement 數量）。
- 已存在的 run 若 alias 映射改變，匯入時會 warning，提示 rebuild 或 re-import。
- 版本同步：`VERSION` 檔案、`APP_VERSION`、`config.example.json` 及各文件。

### v0.4.0 — SQLite 整合資料庫

新增 `tools/build_database.py`，將多個 OCR run 整合成 SQLite，保留每次 OCR observation，
唔會靜默覆蓋舊 run。

- 資料表：`runs`、`banks`、`bank_aliases`、`statements`、`line_items`、`row_verification`。
- 分析 view：`v_balance_sheet`、`v_statement_index`、`v_current_line_items`、
  `v_natural_key_duplicates`、`v_review_queue`。
- 你提出的五欄 key（publication date / statement date / bank / parent label / label）
  會保留作 business key；資料庫另用 `statement_id + label_path + occurrence` 做 natural key，
  因為同一 statement 會有重複 label、空白 label 及多頁情況。
- `publication_date` 缺失時只會在同一 PDF 內所有非空日期一致時推斷，並記錄
  `publication_date_source`；有衝突則保持空白並 warning。
- 銀行名稱以 `bank_key` 正規化，可用 `bank_aliases.csv` 人手合併中文／葡文名稱。
- 人手核實放在獨立 `row_verification` 表；`auto_check_pass` 永遠唔等於財務核實。
- `--header-fallback auto`：當 table image 讀不到銀行名／日期，會另外 OCR 完整 PDF 頁首；issue 45 page 17 可恢復 `郵政儲金局 | CAIXA ECONÓMICA POSTAL`。
- 詳見 [`DATABASE.md`](DATABASE.md)。

### v0.1 — Python Prototype

初始原型版本，主要用於驗證由 PDF 擷取 Balancete 表格的可行性。

主要功能：
- 從 PDF 擷取 Balancete 頁面及表格影像。
- 使用 PaddleOCR 進行文字辨識。
- 初步擷取科目名稱及財務數字。
- 輸出 CSV，並提供人工覆核資料。
- 支援基本中文及葡文表格測試。

**版本定位：** 技術概念驗證及 OCR 流程測試。

### v0.2 — Enhanced OCR Prototype

在 v0.1 基礎上改善 OCR 引擎、語言處理及財務數字辨識。

主要改進：

- **Apple Vision OCR：** macOS 優先使用原生 Vision OCR，減少對大型 OCR 套件的依賴。
- **多語言處理：** 支援繁體中文、葡文及英文辨識設定。
- **中文文字處理：** 提供 OpenCC 繁簡轉換選項，保留原始 OCR 文字。
- **數字格式辨識：** 支援兩種格式：
  - `1,234,567.89`
  - `1.234.567,89`
- **OCR 數字修正：** 嘗試修復部分 OCR 標點錯誤，並標記修正結果供人工覆核。
- **信心度管理：** 記錄 OCR 信心度，方便識別低信心度資料。
- **人工覆核：** 保留原始辨識結果及修正後資料，減少錯誤被直接當成正確數值。

**版本定位：** 改善實際公報處理能力，為後續批量擷取及自動化建立基礎。

### v0.3.1 — 標籤標準化收緊

- Fuzzy mapping 0.70–0.90 一律標 `label_standard_fuzzy_review` 入覆核；<0.70 維持 `label_standard_low_similarity`。≥0.90 才視為低風險候選。
- 詞庫清理：`總收人` 併入 `總收入`（alias）；`prestadas`／`prestados` 葡文印刷變體合併；加入 査/查、扺/抵、资/資 等異體字正規化。
- 修正 `documents.csv` 的 `label_orphan_children` 統計（之前固定為 0）。
- `gt_label_report.py` 改用頁面高度配對、列出 unmatched 及 exact/fuzzy/unmatched 數量。
- 版本同步至 v0.3.1。

### v0.3.0 — 標準標籤詞表

- **標準標籤對照**：由已核對 GT 建立 `labels/label_lexicon.csv`，OCR 標籤會對應到最相似嘅標準名稱，寫入獨立欄位 `label_standard`；`label_raw` 永不覆蓋。
- **中葡文正規化**：中文異體字（帳/賬、券/劵、歷/歴…）、葡文大小寫／重音／空格差異會先正規化再比對；信心不足會標 `label_standard_uncertain` 或 `label_standard_low_similarity`。
- **父子層**：`-澳門元`、`-外幣`、`- Patacas` 等子項會掛到上層科目，輸出 `label_role`、`parent_label_standard`、`label_path`；無上層會標 `label_orphan_child`。
- **斷行標籤**：如果標籤跌落下一行，會用上一行＋本行合併比對，寫入 `label_combined_raw` 同 `label_standard`，並標 `label_continuation_merged`。
- 新增 `tools/build_label_lexicon.py`、`tools/gt_label_report.py`；GT 量度：raw label exact 79.95% → **standardised 97.89%**。

### v0.2.1 — Ground Truth 評分修正

- **GT 行配對修正**：只有真正缺少座標的行才按閱讀次序配對；有座標但超出容差的行保留為 unmatched，避免「漏一行 + 多一行」互相抵消而虛高 alignment F1。
- **空白與橫線分開**：`blank`（空白）與 `dash`（印有 `-`／`—`）而家係唔同狀態；GT 建立同評分都會獨立比較，唔會再把空白格當成橫線。
- **第二輪金額重讀**：新增 `--amount-second-pass ink`（預設），針對空白／低信心金額格做 3× 重讀，原 OCR 保留，採用值標示 `recovered_second_pass` 並入覆核。
- 版本更新至 v0.2.1，新增 GT 工具與 82 個測試。

## 版本比較

| 功能 | v0.1 | v0.2 | v0.3 | v0.4 |
|---|---|---|---|---|
| PDF 表格影像擷取 | ✓ | ✓ | ✓ | ✓ |
| PaddleOCR | ✓ | 可選 | 可選 | 可選 |
| Apple Vision OCR | — | ✓ | ✓ | ✓ |
| 繁體中文處理 | 基本 | 改善 | 改善 | 改善 |
| 葡文及英文處理 | 基本 | 改善 | 改善 | 改善 |
| 千分位及小數點格式 | 基本 | 支援兩種格式 | 支援兩種格式 | 支援兩種格式 |
| OCR 標點修正 | — | ✓ | ✓ | ✓ |
| 信心度及人工覆核 | 基本 | 改善 | 改善 | 改善 |
| 標準科目詞表 | — | — | ✓ | ✓ |
| 科目父子層 | — | — | ✓ | ✓ |
| 跨行標籤合併 | — | — | ✓ | ✓ |
| Ground Truth / CER 評分 | — | — | ✓ | ✓ |
| SQLite 跨期整合 | — | — | — | ✓ |
| 銀行名稱正規化／alias | — | — | — | ✓ |
| 人手核實獨立紀錄 | — | — | — | ✓ |

## 系統需求

- macOS（建議 Apple Silicon，例如 M1 或更新型號）
- Python 3
- Xcode Command Line Tools（使用 Apple Vision 時需要）
- PDF 格式的澳門公報文件

v0.2 在 macOS 上預設使用 Apple Vision；PaddleOCR 及 Tesseract 可按需要選用。

**Linux / Docker：** `Dockerfile` 只支援 PaddleOCR，唔會複製或編譯 Apple Vision 的 Swift worker。Apple Vision run 必須在 macOS 原生環境執行。

## 安裝及執行（v0.4）

```bash
# 建立虛擬環境
python3 -m venv .venv
source .venv/bin/activate

# 安裝基本依賴
python -m pip install --upgrade pip
pip install -r requirements.txt
```

首次使用 Apple Vision 時，確保已安裝 Xcode Command Line Tools：

```bash
xcode-select --install
```

測試單一頁面：

```bash
python macau_tb_ocr.py input/anotariais-45-2026.pdf \
  --outdir output \
  --pages 7 \
  --engine vision \
  --vision-level fast \
  --ocr-langs auto
```

> 註：`zh-Hant` 在 Vision `fast` 沒有辨識器，worker 會自動將中文 header/label 區域改用 `accurate`；`fast` 只影響數字／葡文區域。

處理指定頁面範圍：

```bash
python macau_tb_ocr.py input/anotariais-45-2026.pdf \
  --outdir output \
  --pages 7-32 \
  --engine vision \
  --vision-level accurate \
  --ocr-langs auto
```

## 輸出及資料覆核

OCR 結果應保留原始文字、辨識結果及數字正規化資料。

特別注意：

- 不應單憑中文或葡文判斷數字格式。
- 不確定的數字應標記供人工覆核。
- 自動修正的數字必須保留原始 OCR 結果。
- CSV 標準化數字使用英文小數點格式，例如 `1234567.89`。

## 整合成 SQLite

完成多期 OCR 後，可將 run 資料夾整合成一個資料庫：

```bash
python tools/build_database.py \
  output_v08_issue45_2026 output_v06_issue44_2026 \
  --db data/macau_balancete.sqlite
```

之後用 view 查詢：

```sql
SELECT * FROM v_statement_index ORDER BY publication_date DESC;
SELECT * FROM v_natural_key_duplicates;
SELECT * FROM v_balance_sheet WHERE verification_status = 'verified';
```

Schema、key 設計理由、日期推斷及銀行 alias 規則見 [`DATABASE.md`](DATABASE.md)。

## 測試資料

專案包含測試用例，涵蓋中文、葡文及不同數字格式。

執行測試：

```bash
python -m unittest discover -s tests -v
```

## 開發方向

後續版本預計繼續改善：

1. 提升繁體中文及葡文 OCR 準確度。
2. 改善表格欄位及金額自動定位。
3. 加強數字錯誤偵測及人工覆核流程。
4. 支援更多年度及期數的澳門公報。
5. 建立穩定的批量處理及 Excel 輸出流程。
6. 建立經人手核對的中葡科目 concept mapping，令 `現金` 與 `Caixa` 可以跨語言比較。
7. 為 SQLite 加入銀行／科目層級的跨期比較報表。

---

**Project:** Macau Gazette Balancete OCR

**Current prototype:** v0.6.0

**Purpose:** Extract and normalize bank Balancete data from Macau Gazette PDFs.
