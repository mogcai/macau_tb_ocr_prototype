# 澳門公報 Balancete OCR Prototype v0.3.1

用途：將澳門《公報》第二組銀行／金融機構的 Balancete（試算表）圖片抽取為 Excel / CSV。v0.2 專為 Apple Silicon Mac 加入原生 Apple Vision OCR，並改善繁體中文及財務數字格式處理。

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
cd /path/to/macau_tb_ocr_prototype_v0.3
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
  --outdir output_v02_p07 --pages 7 \
  --engine vision --vision-level accurate --ocr-langs auto
```

葡文第 10 頁：

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir output_v02_p10 --pages 10 \
  --engine vision --vision-level accurate --ocr-langs auto
```

速度比較：

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir output_v02_p07_fast --pages 7 \
  --engine vision --vision-level fast --ocr-langs auto
```

比較 `fast` 同 `accurate` 時必須使用相同 PDF 頁及不同輸出資料夾，記錄實際時間及人工核對結果。唔好只比較 OCR confidence。

第一次執行會將 `vision_ocr_worker.swift` 編譯到 `.vision_cache/`；之後會重用編譯檔。

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
--ocr-langs auto|chinese_cht,pt,en
--number-format auto|comma_dot|dot_comma
--chinese-label-normalization off|s2t|s2hk
--no-repair-ocr-separators
--confidence-threshold 0.88
--inspect-only
```

完整 Agent 操作規則請閱讀 `SKILL.md`；快速入口見 `README_AGENT.md`。
