# 澳門公報 Balancete OCR Prototype

版本：0.1.0  
用途：將澳門《公報》第二組中銀行／金融機構的 Balancete（試算表）由 PDF 表格圖片抽取成 Excel / CSV。

## 呢個 Prototype 對第 45 期 PDF 做咗咩針對性設計？

已檢查的 PDF 特徵：

- 全份 32 頁；銀行試算表公告集中在第 7–32 頁。
- 每張 TB 表格以獨立高解像度圖片嵌入 PDF。
- 表格圖片可直接由 PyMuPDF 的 image xref 抽取，不必重新將整頁 PDF rasterize。
- 同一期內有繁體中文、葡萄牙文，亦預留英文 OCR profile。
- 銀行名稱、試算表日期大多仍可從 PDF text layer 取得；TB 表格帳戶列及借貸金額需要 OCR。
- 本期自動語言分流：16 頁繁體中文、8 頁葡萄牙文、2 頁中葡混合；未見純英文 TB 頁，但程式保留 `en` profile，可透過 `--ocr-langs en` 或自動語言提示處理後續英文公告。
- 第 28 頁的銀行名稱及試算表日期只存在表格圖片 header；第 17 頁原生文字層未提供銀行名稱，圖片亦未見明確機構標題，兩者應留作 metadata 覆核。

## 資料流程

```text
PDF
 ├─ Page text layer -> 銀行名稱、試算表日期、公報頁碼、語言提示
 └─ Embedded image xref -> 原始高解像度 TB PNG
          |
          v
   PaddleOCR (chinese_cht / pt / en)
          |
          v
   OCR bounding boxes + confidence
          |
          v
   OpenCV vertical-line detection
          |
          v
   Rebuild rows: Account | Debit | Credit
          |
          v
   Amount parsing + review rules
          |
          v
   CSV + Excel (TB_Rows / Review_Queue / Documents / OCR_Raw)
```

## 本次 Prototype 的驗證範圍（請先留意）

- 已對附件第 45 期 PDF 完成結構檢查：偵測 26 張 TB 圖片、抽出原圖，並為 26/26 張圖片偵測到兩條內部分隔線。
- 已測試語言分類、日期／金額解析、欄位分組及覆核規則；單元測試 12/12 通過。
- 交付內的 `sample_output/` 是**頁面清單及版面檢查結果**，不是 OCR 完成後的 TB 金額資料。此執行環境沒有安裝 PaddleOCR 模型，因此未在這裏聲稱已量度金額 OCR 準確率。
- 第一次在你部電腦運行時，PaddleOCR 會下載對應模型；正式採用前，請先用第 7 頁（繁中）及第 10 頁（葡文）做小批測試，人工建立 ground truth，再計算借方／貸方金額的 exact-match rate。
- `--confidence-threshold 0.88` 係 Prototype 初始門檻，並非已校準的生產門檻。

## 1. 環境安裝

建議使用 Python 3.10 或 3.11 的獨立環境。PaddlePaddle wheel 會因 macOS / Windows / Linux、CPU / GPU、Intel / Apple Silicon 而不同，請先按 PaddlePaddle 官方安裝頁選擇對應命令：

- https://www.paddlepaddle.org.cn/install/quick
- https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html

建立環境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

先安裝適合你部電腦的 PaddlePaddle，例如官方安裝頁提供的 CPU wheel，再安裝本專案套件：

```bash
python -m pip install paddlepaddle
python -m pip install -r requirements.txt
```

如果 `paddlepaddle` 喺你部 Mac 的 Python / CPU 架構上無相容 wheel，建議用 Linux Docker / Linux VM 運行 OCR。Apple Silicon 如需用 x86_64 Linux image，可先試 `docker build --platform linux/amd64 -t macau-tb-ocr .`。PDF 抽圖和 CSV 匯出部分本身只需要 PyMuPDF、OpenCV、OpenPyXL。

## 2. 先做 PDF 結構檢查（毋須下載 OCR 模型）

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir output_inspect \
  --pages 7-32 \
  --inspect-only
```

會產生：

- `output_inspect/documents.csv`：銀行、日期、PDF 頁碼、圖片解像度、建議 OCR 語言。
- `output_inspect/images/page_XXXX_table.png`：直接由 PDF 抽出的 TB 原圖。
- `output_inspect/run_report.json`：頁數及圖片抽取摘要。

先開幾張 PNG 睇清楚有冇抽錯圖片，確認係表格原圖。

## 3. 執行 OCR

建議正式測試用 PaddleOCR：

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf \
  --outdir output \
  --pages 7-32 \
  --engine paddle \
  --device cpu \
  --ocr-version PP-OCRv5 \
  --ocr-langs auto
```

如想先確認程式流程、或以另一套 OCR 作 benchmark，可用 Tesseract baseline（需另外安裝 Tesseract executable 和對應語言資料 `HanT`、`por`、`eng`）：

```bash
python macau_tb_ocr.py anotariais-45-2026.pdf --outdir output_tesseract \
  --pages 7-10 --engine tesseract --ocr-langs auto
```

安裝 Python wrapper：`python -m pip install -r requirements-tesseract.txt`。Tesseract baseline 只用來測試流程及比較，不代表建議用它作正式生產模型。財務金額必須以 ground truth 作 exact-match benchmark。

`auto` 會先排除公報固定的中葡雙語頁尾，再利用每頁試算表標題／機構文字判斷 OCR 語言：

- `chinese_cht`：繁體中文表格
- `pt`：葡萄牙文表格
- `en`：英文表格（英文文字亦可由中文模型處理；此 profile 作補充）

第一次運行會下載 OCR 模型，需要網絡連線；之後模型會快取在本機。

如需對同一頁做多模型比對：

```bash
python macau_tb_ocr.py input.pdf --outdir output_compare --pages 7-32 \
  --ocr-langs chinese_cht,pt,en
```

這會較慢，適合做 benchmark，唔建議一開始就每日跑三次。

## 4. 輸出檔案

| 檔案 | 內容 |
|---|---|
| `macau_balancete.xlsx` | Excel 工作簿 |
| `tb_rows.csv` | 每個 TB account row 一行 |
| `review_queue.csv` | 需要人工核對的列 |
| `documents.csv` | 每份 TB 的銀行、日期、頁碼和圖片來源 |
| `ocr_raw.csv` | 每個 OCR text box、confidence、座標及語言 |
| `images/` | 保留的原始 TB 圖片 |
| `run_report.json` | 本次執行摘要 |

Excel 有四個 sheet：`TB_Rows`、`Review_Queue`、`Documents`、`OCR_Raw`。

## 5. 非常重要的準確度原則

1. `debit_raw`、`credit_raw` 永遠保留 OCR 原文。
2. `debit_amount`、`credit_amount` 只有通過基本數字格式驗證先會填入。
3. OCR 將 `O` 認成 `0`、`l` 認成 `1` 等可疑情況不會靜默修正，會放入 `review_queue.csv`。
4. 低於 `--confidence-threshold` 的列會標示 `needs_review`。
5. 借貸合計不一致時會標示覆核；唔會自動改金額。
6. 每一行保留 PDF 頁數、圖片路徑、OCR bounding box，方便回看原圖。

`auto_check_pass` 只代表通過 Prototype 的基本規則，**唔等於已證明財務數據正確**。正式投產前必須用人工標註的 ground-truth 樣本測試金額 exact-match rate。

## 6. 目前限制／下一步

- 目前以表格中的兩條主要垂直分隔線劃分「帳戶名稱／借方／貸方」。如果個別銀行採用不同欄位布局，需增加版面 profile。
- 行合併以 OCR box 的垂直中心距離作初步分組；多行科目、跨行標籤需加入人工校正樣本。
- `statement_date` 對中文數字日期和葡文月份有基本處理；遇到特殊字體／格式應加入 fixtures 測試。
- 下一階段最值得做：對第 7、10、12、20、27、32 頁人工校對每個帳戶與金額，建立 benchmark，量度 Account label accuracy、Debit/Credit exact match、Row alignment accuracy。

## 7. 常用命令

```bash
# 只處理第 10 頁
python macau_tb_ocr.py anotariais-45-2026.pdf --outdir output_p10 --pages 10

# 調低覆核門檻（只適合探索，不建議正式數據）
python macau_tb_ocr.py anotariais-45-2026.pdf --outdir output_low_threshold --pages 7-32 --confidence-threshold 0.80

# GPU（必須先裝好相容的 GPU PaddlePaddle）
python macau_tb_ocr.py anotariais-45-2026.pdf --outdir output_gpu --pages 7-32 --device gpu
```

## 8. AI Agent 運行文件

如果你會交由 AI agent（例如 coding agent 或自動化 agent）運行，請先讀：

- [`SKILL.md`](SKILL.md)：完整 agent skill，包含執行步驟、不可違反的資料準則、輸出欄位、品質檢查及 troubleshooting。
- [`README_AGENT.md`](README_AGENT.md)：精簡操作入口及常用命令。

重要：目前 `config.example.json` 只係參考設定，CLI 未有讀取此 JSON 的功能；實際執行設定請使用 command-line options。
