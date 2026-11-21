# Import a Gazette Issue

一個 command 完成下載、OCR 同匯入**操作中嘅 V3 資料庫**：

1. 由 URL 下載 PDF 到 `input/`（temporary → 驗證 SHA-256 → no-clobber 放入正式位置）
2. OCR 全份 PDF 到新嘅 `outputs/runs/issueNN_YYYY__job_<id>/`
3. 將 raw OCR evidence replay 入 `data/macau_balancete_v3.sqlite`
4. 結果係 **raw evidence（staging）**，等 Workbench 做 page classification

```bash
python tools/import_issue.py \
  'https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf'
```

`--target` 預設係 `v3`，預設 DB 係 `data/macau_balancete_v3.sqlite`（由 `ROOT` 解析，唔受 cwd 影響）。

## V3 import 會做／唔會做

會做：

- 建立 `gazette_issues`、`source_documents`、`document_pages`、`extraction_runs`、`page_extractions`、`extracted_rows`
- page coverage 驗證（`documents.csv` 必須覆蓋 `1..pdf_pages` 每一頁）
- 記錄 `source_url`、PDF SHA-256 / bytes、page count、OCR page outcomes、evidence fingerprint

唔會做（留畀 Workbench）：

- 唔會 classify TB page、唔會建立 `statements` / `statement_extractions`
- 唔會 assign row 去 statement、唔會 approve mapping、唔會 review / adopt / publish
- 唔會 call `build_database.py`、`name_registry.py refresh-banks`、`import_user_standard.py map`

## 安全規則

- 新 issue 一定係 staging；V3 target 唔接受 `--publish` / `--force-publish`。
- 唔會覆蓋已存在嘅 input PDF；重複內容會走 duplicate/version gate，唔會靜靜再 import。
- 唔會覆蓋已存在嘅 run directory；每次 import 都有新 `job_id`。
- OCR 失敗、coverage 不符、replay 失敗，都會停並留下 `import.log` 同 manifest。
- 同一個 V3 DB 同時只可以有一個 import（`<db>.import.lock`）。
- `--pages` 必須係 `all`；partial page 只准用於 `--inspect-only` / `--target ocr-only` 診斷。

## 常用模式

### Dry run（只做 preflight，唔下載、唔 OCR、唔寫 DB）

```bash
python tools/import_issue.py URL --dry-run
```

### JSON 輸出（一個 object，方便 job runner）

```bash
python tools/import_issue.py URL --json
```

### 只下載（診斷）

```bash
python tools/import_issue.py URL --download-only
```

### 先 inspect layout，唔 replay 入 V3

```bash
python tools/import_issue.py URL --inspect-only --pages 7-32
```

完成後睇 `documents.csv`、`images/`，確認 TB 頁範圍及 layout。

### Replay 一個已存在嘅 run（唔重新下載／OCR）

```bash
python tools/import_issue.py \
  --replay-only \
  --outdir outputs/runs/issue33_2026__job_abc12345 \
  --json
```

### 同一份 PDF，刻意重跑 OCR

```bash
python tools/import_issue.py URL \
  --reprocess --reason "OCR engine/configuration retest"
```

### 同一個 issue，但係新版 PDF

```bash
python tools/import_issue.py URL \
  --new-version --reason "Official Gazette republished the PDF"
```

`--reprocess` 同 `--new-version` 互斥，兩者都要 `--reason`。

## V3 flags

```text
--target v3|v5|ocr-only   預設 v3
--v3-db PATH              V3 DB（預設 data/macau_balancete_v3.sqlite）
--issue-key YYYY-NN       檔名推導唔到 issue key 時必須提供
--replay-only             只 replay 已有 run，唔下載／OCR
--reprocess               同內容重跑 OCR（需 --reason）
--new-version             同 issue 新版本 PDF（需 --reason）
--reason TEXT
--allow-local-source      允許 local PDF（V3 預設只收 HTTPS）
--allow-outside-output    允許 --outdir 走出 outputs/runs
--json                    單一 machine-readable result object
--name NAME               覆寫 managed PDF basename（唔影響 issue identity）
--engine vision|paddle|tesseract
--vision-level fast|accurate
--ocr-langs auto|chinese_cht,pt,en
--dry-run
--download-only
--inspect-only
```

## 輸出

```text
input/anotariais-33-2026.pdf
outputs/runs/issue33_2026__job_a1b2c3d4/
  documents.csv
  tb_rows.csv
  review_queue.csv
  ocr_raw.csv
  images/
  run_report.json
  import.log
  import_manifest.json
```

失敗同「需要營運者決定」嘅結果唔會建立 run directory，而係寫入獨立結果目錄：

```text
output_import_failed/<job-id>/import_manifest.json
output_import_results/<job-id>/import_manifest.json
```

`import_manifest.json`（`manifest_version: 2`）記錄 source URL、PDF path / SHA-256 / bytes / pages、issue key、run dir、V3 DB、每個 stage 嘅 status、OCR 選項、coverage 計數同 error。

## Exit codes（V3）

```text
0  ok / already_present / already_imported
1  failed
2  usage / preflight error
3  import_locked / decision required（例如 new_version_required）
```

## Legacy V5（只喺明確要求時）

Legacy V5 observations DB 已封存到 `archive/database/macau_balancete.sqlite`，係凍結、唯讀嘅歷史資料，唔係正式發布路徑。舊流程（`build_database.py` + bank refresh + user standard mapping + 可選 publish）仍然保留，但必須明確加 `--target v5`。要寫入就先 copy 一份去工作位置再傳 `--db`：

```bash
cp archive/database/macau_balancete.sqlite /tmp/v5-work.sqlite

python tools/import_issue.py URL \
  --target v5 \
  --db /tmp/v5-work.sqlite
```

Review 後 legacy publish：

```bash
python tools/import_issue.py URL \
  --target v5 \
  --db /tmp/v5-work.sqlite --publish
```

V5 專屬 flags（只可以配 `--target v5`）：`--db`、`--publish`、`--force-publish`、`--published-by`、`--no-db`、`--no-ocr`、`--no-name-map`、`--no-bank-refresh`、`--overwrite-pdf`、`--reuse-output`。V3 收到呢啲 flags 會直接報 usage error，並提示加 `--target v5`。

### V5 實測記錄（Issue 33）

```text
PDF SHA-256: 2d51dd30a19e8383f63a836dbe6a57efc8becb1b49378bd229e4be44f6d1c627
PDF pages: 11
TB pages: 1
Rows: 64
Review rows: 32
Bank: 中國建設銀行澳門分行
Statement date: 2026-06-30
Number format: comma_dot
run: run_eadbb15344cd15f416d01c6e45af3d7537f30a3832995884193104b77045ab1b
```
