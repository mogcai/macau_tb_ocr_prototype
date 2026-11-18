# Import a Gazette Issue（v0.7.0）

一個 command 完成：

1. 由 URL 下載 PDF 到 `input/`
2. 跑 OCR，建立新 `output_issueNN_YYYY/`
3. 匯入 SQLite
4. Refresh bank registry
5. 套用 approved user standard labels

```bash
python tools/import_issue.py \
  'https://images.bo.dsaj.gov.mo//bo/ii/2026/33/anotariais-33-2026.pdf' \
  --db data/macau_balancete.sqlite
```

## 安全規則

- 不會覆蓋已存在的 PDF；如果 URL 內容同現有檔案不同，會停止，除非明確加 `--overwrite-pdf`。
- 不會覆蓋已存在的 output folder；要 reuse 必須明確加 `--reuse-output`。
- OCR 失敗、`documents.csv` 不存在或 DB import 失敗，都會停止並留下 `import.log` / `import_manifest.json`。
- 新 issue 預設只係 **staging**，不會自動 publish。

## 常用模式

### 只下載

```bash
python tools/import_issue.py URL --download-only
```

### 先 inspect，不 OCR

```bash
python tools/import_issue.py URL --inspect-only \
  --outdir output_inspect_issue33
```

完成後睇 `documents.csv`、`images/`，確認 TB 頁範圍及 layout。

### Dry run

```bash
python tools/import_issue.py URL --dry-run
```

只會顯示下載、OCR、DB import 命令，不會執行。

### 跑 OCR + DB，不 publish

```bash
python tools/import_issue.py URL --db data/macau_balancete.sqlite
```

### Review 後正式 publish

```bash
python tools/import_issue.py URL --db data/macau_balancete.sqlite --publish
```

如果新 run 缺少已 published run 的頁數，會拒絕；要明確加 `--force-publish`。

## 其他 flags

```text
--name NAME              覆寫 PDF 檔名
--pages 7-32             OCR 頁範圍（預設 all）
--engine vision|paddle|tesseract
--vision-level fast|accurate
--ocr-langs auto|chinese_cht,pt,en
--no-db                  只 OCR，不 import DB
--no-ocr                 不重跑 OCR（需要已有 output）
--no-name-map            不套用 user standard labels
--no-bank-refresh        不 refresh bank canonical/aliases
--published-by NAME      publish 記錄
--force-publish          允許 partial run 取代 published run
--reuse-output           明確允許使用已有 output dir
--overwrite-pdf          明確允許替換不同內容的 input PDF
```

## 輸出

```text
input/anotariais-33-2026.pdf
outputs/runs/issue33_2026/
  documents.csv
  tb_rows.csv
  review_queue.csv
  ocr_raw.csv
  images/
  run_report.json
  import.log
  import_manifest.json
```

`import_manifest.json` 記錄：

```text
source URL
PDF path / SHA-256 / bytes
output directory
每個 step 的 command 及 status
開始／完成時間
error（如有）
```

## 實測

Issue 33：

```text
URL: https://images.bo.dsaj.gov.mo//bo/ii/2026/33/anotariais-33-2026.pdf
PDF SHA-256: 2d51dd30a19e8383f63a836dbe6a57efc8becb1b49378bd229e4be44f6d1c627
PDF pages: 11
TB pages: 1
Rows: 64
Review rows: 32
Bank: 中國建設銀行澳門分行
Statement date: 2026-06-30
Publication date: 2026-08-19
Number format: comma_dot
```

Import 後：

```text
run_eadbb15344cd15f416d01c6e45af3d7537f30a3832995884193104b77045ab1b
1 page / 64 rows / staging
user standard mapping: 64/64
```
