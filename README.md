


# 澳門銀行公報試算表OCR


[詳細說明](README_zh_HK.md)

澳門特區政府公報（《澳門特別行政區公報》）銀行 Balancete（試算表）資料擷取工具，將 PDF 內的表格影像轉換成可供整理及分析的 CSV／Excel 資料。

本專案以 Python 開發，並針對澳門公報中常見的中文、葡文及英文銀行財務表格作 OCR 處理。

## 版本簡介

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

## 版本比較

| 功能 | v0.1 | v0.2 |
|---|---|---|
| PDF 表格影像擷取 | ✓ | ✓ |
| PaddleOCR | ✓ | 可選 |
| Apple Vision OCR | — | ✓ |
| 繁體中文處理 | 基本 | 改善 |
| 葡文及英文處理 | 基本 | 改善 |
| 千分位及小數點格式 | 基本 | 支援兩種格式 |
| OCR 標點修正 | — | ✓ |
| 信心度及人工覆核 | 基本 | 改善 |

## 系統需求

- macOS（建議 Apple Silicon，例如 M1 或更新型號）
- Python 3
- Xcode Command Line Tools（使用 Apple Vision 時需要）
- PDF 格式的澳門公報文件

v0.2 在 macOS 上預設使用 Apple Vision；PaddleOCR 及 Tesseract 可按需要選用。

## 安裝及執行（v0.2）

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

---

**Project:** Macau Gazette Balancete OCR  
**Current prototype:** v0.2  
**Purpose:** Extract and normalize bank Balancete data from Macau Gazette PDFs.