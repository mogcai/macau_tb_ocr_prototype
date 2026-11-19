# Data Verification Workbench — 短期數據核實工作 UI 規格

> **文件類型：** UX / Functional / Implementation Specification  
> **版本：** v1.0  
> **規劃基準日期：** 2026-11-19  
> **適用項目：** Macau Banking Intelligence Platform  
> **目標使用者：** 初期單一管理者／核實者；架構預留多人協作

---

## 1. 目的及設計原則

建立一個 **Data Import & Verification Workbench**，令使用者可以在同一個 Web 介面完成：

1. 上載或選擇已由系統下載的 Gazette PDF。
2. 查看 Import / OCR / Validation 進度。
3. 查看待核實的銀行、報表及行項目。
4. 同時對照 PDF 原圖及 OCR 抽取結果。
5. 修改錯誤欄位，但永遠保留原始 OCR observation。
6. 記錄人工核實狀態、操作者、時間及原因。
7. 完成 statement-level 檢查後，透過受控流程發布。

### 1.1 核心 UX 原則

- **Source-first：** 核實數字時，原始 PDF 是主要證據，OCR 只是候選結果。
- **並排檢視：** PDF 與抽取表格同屏，避免來回開 PDF / Excel。
- **定位同步：** 選取數據列時，PDF viewer 跳到對應頁面及 bounding box（有座標時）。
- **Raw immutable：** 任何修正都建立 correction record，不覆蓋 raw OCR。
- **狀態分離：** OCR confidence、validation status、mapping status、human verification status、publish status 不可混為一談。
- **Review is not publish：** 核實一行不等於整份 statement 已可發布。
- **Fast for repetitive work：** 提供鍵盤操作、連續核實及安全批次動作。
- **Visible uncertainty：** 無座標、解析歧義、缺頁或 mapping 未批准時，介面必須明確顯示。

### 1.2 MVP 範圍

**必須有：**

- Manual PDF upload 及現有 import result selection。
- Import batch / Issue 狀態列表。
- Review Queue 及篩選。
- PDF + extracted rows split-screen。
- PDF page navigation 及 bounding box highlight（有 source coordinates 時）。
- Row / field value inspection。
- Inline correction、verify、reject、needs investigation。
- Audit trail。
- Statement readiness check 及 staging / publish integration。

**暫時不做：**

- 複雜多人即時協作及鎖定服務。
- 自動根據人工修正即時改寫 OCR model。
- LLM 自行決定財務金額。
- Dashboard 財務圖表及銀行比較。
- 完整權限工作流以外的企業級 approval chain。

---

## 2. 使用者及權限

初期可以只有一個 Admin / Reviewer，但 API 及資料模型應預留角色。

| 角色 | 查看 | 修改 / 核實 | Mapping approval | Publish |
|---|---:|---:|---:|---:|
| Admin | Yes | Yes | Yes | Yes |
| Reviewer | Yes | Yes | 按授權 | No（MVP 可先由 Admin 發布） |
| Read-only | Yes | No | No | No |

每個寫入操作必須由登入使用者執行，後端不可單靠前端傳入的 `reviewer_id` 當作可信身份。

---

## 3. 資訊架構 / 導覽

```text
Operations Portal
├── Overview
├── Imports
│   ├── Import Batches
│   ├── Upload PDF
│   └── Issue / Run Detail
├── Verification
│   ├── Review Queue
│   └── Verification Workbench
├── Registries
│   ├── Banks & Aliases
│   └── Account Concepts & Aliases
├── Publish
│   └── Publish Readiness
└── Audit & Logs
```

MVP 可先實作 Imports、Review Queue、Workbench、Publish Readiness 四個主要頁面；Registry 管理可先只提供唯讀及跳轉至既有工具的方式，之後才加入完整 Web CRUD。

---

## 4. 核心工作流程

```text
Manual Upload / Gazette Auto Download
                 |
                 v
          Create Import Batch
                 |
                 v
     Download / Hash / Duplicate Check
                 |
                 v
       Existing OCR Import Pipeline
                 |
                 v
           Staging Run Created
                 |
                 v
     Automated Validation + Queue Build
                 |
          +------+------+
          |             |
          v             v
     Critical /       Low-risk / Passed
     Warning items       candidates
          |             |
          v             v
      Human Review   Sampling / policy checks
          |             |
          +------+------+
                 |
                 v
        Statement Readiness Check
                 |
                 v
          Admin Publish Review
                 |
                 v
              Published
```

系統必須區分以下三層狀態：

1. **Job status：** Import / OCR / DB import 執行到哪一步。
2. **Review status：** 單一 row / field 有否被人工核實。
3. **Publication status：** statement / run 是否已正式發布。

不能因為 Job 完成，就自動將 rows 標示為 verified 或 published。

---

## 5. 畫面一：Import Batches / 導入管理

### 5.1 目的

讓使用者知道每份 PDF 由哪裡來、處理到哪一步、是否重複、失敗原因，以及下一步可做甚麼。

### 5.2 頁面內容

頂部摘要：

- New / Discovered Issues
- Processing
- Review Required
- Failed Jobs
- Ready to Publish
- Published（按日期範圍）

列表建議欄位：

| 欄位 | 說明 |
|---|---|
| Issue | 年份及 Issue number；未知時顯示待識別 |
| Source | Official URL / Manual Upload |
| PDF | 檔名、頁數、SHA-256 前綴 |
| Bank / Statements | 偵測到的銀行及報表數量；未確認要標示 |
| Import Status | Discovered / Downloaded / OCR Running / Staging / Failed |
| Review Status | Pending / In Progress / Complete |
| Last Updated | 最近更新時間 |
| Action | Open / Retry / Inspect / Reprocess（按權限及狀態） |

### 5.3 操作

**Manual Upload**

- 支援 PDF。
- 顯示檔案名稱、大小及頁數（上載後由後端確認）。
- 上載前後均需做 hash / duplicate check。
- 若發現相同 SHA-256，提示「此文件已存在」，提供開啟既有 record，不另建重複 run。
- 若同 URL / Issue metadata 但內容 hash 不同，建立新 document version 或 conflict review，不可靜默覆蓋。

**Start Import**

- 選擇 OCR engine / OCR profile（只顯示系統實際支援的選項）。
- 可選擇頁碼範圍作測試，但部分頁數 run 不可直接當成完整正式 run 發布。
- 顯示預計工作及不可逆動作提示。
- Import 使用背景 Job；離開頁面後仍繼續執行。

**Retry / Reprocess**

- Retry：重試失敗步驟，保持同一來源文件身份及 idempotency。
- Reprocess：用新 OCR / parser / mapping version 建立新 run，舊 run 保留。
- UI 必須顯示兩者不同，不可將 Reprocess 誤作覆蓋原結果。

### 5.4 Job status

建議枚舉：

`discovered` → `downloading` → `downloaded` → `queued` → `ocr_running` → `extracted` → `validating` → `staging_ready`

失敗及例外狀態：

`duplicate`、`download_failed`、`ocr_failed`、`validation_failed`、`import_failed`、`cancelled`。

每個 Job 都要有 `created_at`、`started_at`、`finished_at`、`last_heartbeat_at`、`retry_count`、`error_code` 及人類可讀 `error_message`。

---

## 6. 畫面二：Review Queue / 待核實工作清單

### 6.1 目的

不要讓使用者逐份 PDF 逐行尋找問題；系統按風險及異常類型整理成優先工作清單。

### 6.2 Summary cards

- Critical Errors
- High Priority Review
- Mapping Pending
- Structural Warnings
- Random Sample Pending
- Total Pending Rows

所有數字均由查詢結果實時計算；畫面需註明所選 Issue / 日期範圍，避免不同範圍混淆。

### 6.3 篩選條件

- Gazette Year / Issue
- Bank
- Statement Date
- PDF Page
- OCR Engine / Run
- Validation Severity
- Validation Rule
- OCR confidence range（只作輔助篩選）
- Mapping status
- Human verification status
- Field type：Label / Debit / Credit / Balance / Date / Bank identity

### 6.4 優先排序

建議排序次序：

1. Critical validation errors。
2. 金額解析歧義及疑似符號／小數點／千分位錯誤。
3. Bank / statement identity 問題。
4. Parent-child / row structure 問題。
5. 未批准的科目或銀行 mapping。
6. OCR 低信心度項目。
7. Random quality sampling。

排序必須顯示原因，不應用一個不透明 risk score 隱藏判斷。

### 6.5 Queue row 欄位

| 欄位 | 說明 |
|---|---|
| Issue / Statement | 對應公報及報表 |
| Bank | Canonical bank + raw bank name（有差異時） |
| Page / Row | PDF 頁數及行次序 |
| Raw Label | 原始 OCR 科目文字 |
| Standard Label | 已批准標準概念或 pending suggestion |
| Amount Preview | Debit / Credit / Balance 的原始及正規化摘要 |
| Issue | 主要 validation / mapping 問題 |
| OCR confidence | 原始分數及所屬 engine；不可當作真確率 |
| Review status | Pending / In Progress / Verified / Rejected |
| Action | Open in Workbench |

---

## 7. 畫面三：Verification Workbench / 核實工作台

### 7.1 桌面版版面

建議最少以 1440px 寬度作主要設計目標。使用左右雙欄，兩邊可調整寬度：

```text
+----------------------------------------------------------------------------+
| Issue 45 / 2026 | Bank | Statement Date | Run ID | Save status | Publish   |
+--------------------------------------+-------------------------------------+
|                                      | Review controls / field details     |
|  SOURCE PDF VIEWER                   |                                     |
|                                      | Filters: All / Errors / Pending     |
|  Page thumbnails                     |-------------------------------------|
|  +-------------------------------+   | Row | Label | Debit | Credit | Status|
|  |                               |   |-------------------------------------|
|  | PDF page                      |   |  1  | ...   | ...   | ...    | ...   |
|  |                               |   |  2  | ...   | ...   | ...   |
|  |   [highlight source region]   |   |  3  | ...   | ...   | ...   |
|  |                               |   |                                     |
|  +-------------------------------+   | Selected row detail                 |
|  Zoom / Fit / Rotate / Page         | Raw OCR / normalized / correction     |
|                                      | Validation messages / review actions  |
+--------------------------------------+-------------------------------------+
| Previous row | Next row | Verify | Needs investigation | Reject | Save     |
+----------------------------------------------------------------------------+
```

建議初始比例 PDF 55% / Data 45%，但使用者可拖曳調整。窄螢幕改為 PDF / Data tabs，不可將表格縮到無法閱讀。

### 7.2 Header / statement context

固定顯示：

- Issue year / number
- PDF filename / SHA-256 short fingerprint
- Bank canonical name + source raw name
- Publication date
- Statement date
- Currency（未知時顯示 Unknown，不可自行假設）
- Statement page range
- OCR run ID / OCR engine / app version
- Run status：staging / published / superseded
- Review progress：verified / pending / rejected / total

若 bank、statement date 或 publication date 仍未確認，使用醒目提示，不可藏在 metadata drawer。

### 7.3 PDF viewer

MVP 功能：

- 頁面縮圖及頁碼跳轉。
- Zoom in / out、Fit width、Fit page。
- Rotate（如 PDF 頁面方向不正確）。
- 選取 row 時跳至該 row 的 PDF page。
- 若保存了 bounding box，顯示高亮框；支援多個 boxes / merged row evidence。
- 點選 PDF 區域時可記錄目前位置作輔助核對（MVP 可先不支援反向自動配對）。
- 可開啟原始 PDF 新視窗；下載權限按角色控制。

**重要 fallback：** 如果 row 沒有可靠 bounding box，仍要跳到頁碼，但顯示「沒有可用位置證據」，不可畫一個猜測框。若 row 是跨行合併或 continuation merge，要顯示全部 source boxes / pages。

### 7.4 Extracted table

最少顯示欄位：

- Row order
- Label raw
- Label normalized / standard
- Parent label / label path / row role
- Debit raw / normalized
- Credit raw / normalized
- Balance raw / normalized（只在來源有該欄時顯示）
- OCR confidence（按欄位，如有）
- Page / bbox evidence
- Validation status
- Human review status

表格功能：

- 按問題類型 filter。
- 按頁碼、row order 排序；預設保留文件閱讀順序。
- 選取 row 時 PDF viewer 同步定位。
- 有修正的欄位顯示 corrected badge，並可展開原始值。
- 有警告的欄位顯示 rule ID 及簡短原因；點擊後顯示詳細 validation evidence。
- 不可只用顏色代表狀態，需有文字／icon label，兼顧色弱及可讀性。

### 7.5 Selected row detail panel

分成四組：

**A. Source / Raw Evidence**

- raw label / raw numeric strings。
- OCR engine / confidence / language。
- PDF page / bounding box。
- 原始 OCR observation ID。

**B. Normalized / Mapped Values**

- normalized label。
- standard concept ID / canonical label。
- parent / child relationship。
- normalized amount。
- number-format interpretation（例如 comma-dot / dot-comma / unresolved）。
- mapping status / mapping version。

**C. Validation**

以 severity 分組：Error / Warning / Info。

每條結果包含：

- Rule ID。
- Rule version。
- Field / row scope。
- Result。
- Human-readable explanation。
- 可執行的修正建議（只作 suggestion，不自動套用）。

**D. Human Review**

- Review status。
- Reviewer。
- Verified at。
- Reviewer note。
- Correction history。
- 可用 actions：Verify / Correct / Needs investigation / Reject。

---

## 8. 修正與核實規則

### 8.1 不可覆蓋原始值

任何欄位修正都要保存：

- entity / row ID
- field name
- original raw value
- current normalized value（如有）
- corrected value
- correction reason
- reviewer user ID
- timestamp
- source evidence reference
- correction schema / rule version

原始 OCR output 只可讀，不可由 UI 更新。

### 8.2 金額輸入

- 金額輸入採用明確 decimal parser。
- UI 顯示原始字串及系統解析結果；不可只顯示格式化後數字。
- 使用者修正時，保留輸入原文及標準化 decimal 值。
- 小數位及負數格式按 statement / currency profile 驗證；未定義時顯示警告，不自行補零或移動小數點。
- `-`、blank、`0` 是不同值，不可互相替換。
- 若 UI 需輸入 `1.234.567,89` 等格式，應顯示系統接受的 locale / format，避免含糊輸入。

### 8.3 科目名稱及 Mapping

- 修改 raw label 不應成為修改 Registry 的操作。
- 若是 OCR typo，可修正該次 observation 的 normalized display / correction value。
- 若要建立新 alias 或合併 concept，建立 Registry review task，經批准後才影響其他 rows。
- fuzzy match、bilingual pair 或 AI suggestion 只作候選；未批准不可自動變成 approved mapping。
- 顯示 parent / child context，避免只按相似文字合併不同科目。

### 8.4 Row-level 與 Field-level verification

現有資料庫已有 `row_verification` 人工核實概念；MVP 可先沿用 row-level status，並在 UI 明確要求核實者查看該行所有必要欄位。

但長遠應加入 field-level correction / verification history，以便記錄「label 已確認、debit 已確認、credit 仍有疑問」等情況。建議新增獨立 correction / review event 結構，而不是把所有欄位塞進一個 row status。

建議狀態：

- `pending`：未核實。
- `in_progress`：正在處理，尚未完成。
- `verified`：核實者確認當前需要核對的欄位／整行。
- `needs_investigation`：證據不足或需其他人處理。
- `rejected`：該 row / observation 不可作正式資料；須填原因。

`rejected` 不等於刪除；原始資料仍保留。

### 8.5 Keyboard shortcuts

建議預設：

| Shortcut | Action | 注意 |
|---|---|---|
| `J` / `K` 或 `↓` / `↑` | 下一行 / 上一行 | 在輸入欄位打字時停用 |
| `Enter` | 開啟／確認目前選取項目 | 不可在未確認時直接 publish |
| `V` | Verify selected row | 只在未聚焦文字輸入框時生效 |
| `E` | Focus correction editor | 不覆蓋 raw value |
| `R` | Needs investigation | 需可填原因 |
| `Esc` | 關閉 drawer / 取消目前編輯 | 未儲存修改要提示 |
| `Ctrl/Cmd + Enter` | Save current correction | 儲存不等於核實 |

快捷鍵需可查看及調整；高風險操作不應只依賴單鍵觸發。

---

## 9. 批次核實及抽樣

### 9.1 安全批次核實

支援使用者選擇多行後批次 verify，但必須：

- 顯示選取數量及涵蓋頁面。
- 排除有 Critical error、unresolved number format、unconfirmed bank / date identity 的項目，除非有明確 override 權限及原因。
- 使用者先確認批次操作摘要。
- 記錄批次 ID、行項目清單、操作者、時間及當時 validation result snapshot。
- 不可將未選取或被 filter 隱藏的行一併核實。

### 9.2 Random quality sampling

對自動檢查通過的資料仍應做抽樣，避免只核對系統已認為有問題的項目。

- 抽樣比例及抽樣單位（row / page / statement）在設定中明確記錄。
- 記錄抽樣方法、seed、被抽中項目及結果。
- 若抽樣發現錯誤，可擴大該頁、該類型或該 OCR run 的檢查範圍。
- 抽樣通過不代表所有同類數據都 verified。

---

## 10. 畫面四：Publish Readiness / 發布檢查

### 10.1 Statement readiness summary

發布前以 checklist 顯示：

| Check | 必須結果 |
|---|---|
| Source PDF and hash | Present |
| Expected pages | Complete / exceptions explained |
| Bank identity | Confirmed |
| Statement date | Confirmed or documented exception |
| Publication date | Confirmed or documented exception |
| Currency / unit | Known or explicitly marked unknown with approval |
| Critical validation errors | Zero unresolved |
| Required review items | Completed |
| Sampling | Completed where policy requires |
| Mapping | Approved or exception documented |
| Run provenance | OCR / parser / mapping / rules version recorded |

### 10.2 Publish 操作

- 只有有 publish permission 的使用者可執行。
- Publish 前展示 statement / page coverage 及與目前 published run 的差異。
- 如新 run 少頁、少 statement 或疑似不完整，預設拒絕發布。
- 強制發布必須填寫理由，並建立高嚴重度 audit event。
- 發布後保留上一個 published run，能夠查閱及按程序回滾。
- 發布結果需可從 dashboard / data API 查到 source reference 及 verification status。

---

## 11. 建議資料結構及 API

以下是 Web Workbench 所需的邏輯模型及 API 草案。命名需在對照現有 schema 後定案；不要為了符合草案而重複新增現有欄位。

### 11.1 現有結構優先重用

Repository 現有 `runs`、`pages`、`statements`、`line_items`、`row_verification` 及銀行／科目 registry 應優先重用。

特別是：

- `runs`：OCR run、source PDF hash、版本及 published status。
- `pages`：PDF page、image path、OCR status 及 page metadata。
- `statements`：銀行、日期、statement identity 及頁面範圍。
- `line_items`：原始 label、標準化 label、label path、金額及 row provenance。
- `row_verification`：人工核實 status、verified_at、verified_by、note。

### 11.2 建議擴展（需 Migration Review）

| Entity / Table | 主要欄位 | 用途 |
|---|---|---|
| `import_jobs` | job_id, source_document_id, status, retry_count, timestamps, error | UI 追蹤背景工作 |
| `validation_results` | entity_id, rule_id, rule_version, severity, result, evidence_json | 保存可追溯檢查結果 |
| `field_corrections` | entity_id, field_name, raw_value, corrected_value, reason, reviewer_id, timestamp | 不覆蓋 raw 的欄位修正 |
| `review_events` | entity_id, action, actor_id, note, before / after reference, timestamp | 完整核實操作歷史 |
| `publish_events` | run_id / statement_id, actor_id, reason, diff summary, timestamp | 發布及回滾審計 |

### 11.3 API 草案

| Method | Endpoint | 用途 |
|---|---|---|
| `GET` | `/api/imports` | 導入批次列表及篩選 |
| `POST` | `/api/imports/upload` | 上載 PDF 並建立 import job |
| `POST` | `/api/imports/{id}/retry` | 重試失敗 job |
| `POST` | `/api/imports/{id}/reprocess` | 用新設定建立新 run |
| `GET` | `/api/imports/{id}` | 批次詳情及處理步驟 |
| `GET` | `/api/review-queue` | 待核實清單及篩選 |
| `GET` | `/api/statements/{id}/review` | statement、rows、validation 及 review 資訊 |
| `GET` | `/api/documents/{id}/pages/{page}` | PDF page / image / bbox evidence |
| `PATCH` | `/api/line-items/{id}/corrections` | 建立 correction，不覆蓋 raw |
| `POST` | `/api/line-items/{id}/verify` | 核實 row / field |
| `POST` | `/api/line-items/{id}/reject` | 拒絕並記錄原因 |
| `POST` | `/api/review-batches/verify` | 安全批次核實 |
| `GET` | `/api/statements/{id}/publish-readiness` | 發布檢查摘要 |
| `POST` | `/api/runs/{id}/publish` | 受控發布 |
| `GET` | `/api/audit-events` | 查閱 audit logs |

所有寫入 API 應支援認證、權限檢查、input validation、idempotency / duplicate submission protection 及結構化錯誤回應。

---

## 12. UI 狀態及錯誤處理

介面必須設計以下狀態，不可只做 happy path：

- Loading / OCR running。
- Empty queue（無待核實資料）。
- PDF loading failed。
- Page image unavailable，但原 PDF 仍可開啟。
- Row 沒有 bbox。
- Statement metadata ambiguous。
- Duplicate PDF / changed PDF content。
- Save failed / network disconnected。
- Another user updated the row（多人版本預留）。
- Permission denied。
- Data already published / superseded。
- Import failed with retry action。

修改未儲存時離開頁面要提示。若資料已被另一個操作更新，需提示使用者 reload / compare，不可靜默覆蓋。

---

## 13. MVP 驗收測試

### 13.1 Import

- [ ] 可上載合法 PDF，建立唯一 source document。
- [ ] 相同 PDF hash 不會建立重複 import record。
- [ ] PDF 內容不同但 metadata / URL 相同時會建立 conflict 或新版本提示。
- [ ] OCR job 完成後建立 staging run，而非自動 published。
- [ ] OCR / DB import 失敗時可見錯誤及 retry。

### 13.2 Review Queue

- [ ] 可按 Issue、bank、page、severity、mapping status 篩選。
- [ ] OCR confidence 與 validation severity 分開顯示。
- [ ] 每項 review task 可以開啟正確 statement / row。

### 13.3 Workbench

- [ ] 選取 row 可跳到正確 PDF page。
- [ ] 有 bbox 時顯示正確 highlight；沒有 bbox 時不畫假框。
- [ ] raw OCR values 永遠保持不變。
- [ ] correction 保存 original / corrected / reason / reviewer / timestamp。
- [ ] blank、dash、zero、negative、comma-dot、dot-comma 可分開處理。
- [ ] verify / reject / needs investigation 都有 audit record。
- [ ] 儲存失敗不會顯示成功；未儲存離開會提示。

### 13.4 Batch & Publish

- [ ] 批次核實只作用於明確選取的 rows。
- [ ] 有 Critical error 的 row 不會被普通批次 verify 靜默略過。
- [ ] publish readiness 會列出未完成項目。
- [ ] 不完整 page set 不會取代完整 published run。
- [ ] publish / force publish 均有操作者、時間及理由紀錄。
- [ ] 發布後可以追溯原始 PDF、run、page、row 及 correction。

### 13.5 Usability

- [ ] 使用者可只用鍵盤完成常用的下一行、上一行、編輯及核實操作。
- [ ] 1440px 桌面畫面 PDF / table 同時可讀。
- [ ] 低寬度畫面有可用的 tab / stacked layout。
- [ ] 顏色不是唯一狀態提示；所有重要狀態有文字標籤。

---

## 14. 分階段開發建議

### MVP-1：可用的人工核實介面

- Import list / batch detail。
- Review queue。
- PDF + table split-screen。
- row-level verify / reject / needs investigation。
- correction record。
- audit trail。
- staging / publish readiness read-only check。

### MVP-2：效率提升

- Keyboard shortcuts。
- Batch verification。
- Random sampling queue。
- Validation rule detail panel。
- Mapping suggestion approval / reject。
- Review workload metrics。

### MVP-3：多人及完整營運

- User roles / assignment。
- Work item assignment and concurrency handling。
- Field-level verification history。
- Correction rule proposal and approval workflow。
- Advanced audit search。

---

## 15. 建議技術實作次序

1. **先確認 evidence contract：** 確認 `documents.csv` / `tb_rows.csv` / `ocr_raw.csv` / `run_report.json` 現有欄位如何映射到 DB，尤其 page、bbox、raw amount、confidence 及 source hash。
2. **先做 read-only API：** 將現有 SQLite 的 imports、statements、line_items、review_queue 暴露給前端，驗證資料模型。
3. **建立 PDF viewer：** 支援頁面、zoom、page navigation 及 bbox overlay。
4. **建立 correction / verification write API：** 所有寫入均保留 audit，不直接更新 raw OCR。
5. **建立 publish readiness：** 先只顯示檢查結果，再接上受控 publish action。
6. **加入 job control：** UI 觸發現有 `tools/import_issue.py` wrapper；不要將長時間 OCR 放在 HTTP request 內同步執行。
7. **加上測試及備份：** 用代表性 Issue fixtures 測試完整流程。

---

## 16. MVP Definition of Done

WorkBench MVP 只有在以下全部成立時才算完成：

- 使用者可從 Web UI 上載 PDF 或打開現有 staging run。
- 能清楚知道來源 PDF、SHA-256、Issue、銀行及 statement metadata。
- PDF 與抽取結果可同屏顯示，row selection 可定位至 source page / bbox evidence。
- 可修正錯誤欄位，但 raw OCR 不會被覆蓋。
- 可核實、拒絕或標記需要調查，並保存 reviewer、時間及原因。
- Review Queue 可按優先級和問題類型篩選。
- Publish readiness 會阻止未解決 Critical errors 或不完整 run。
- 所有發布操作均可追溯，已發布資料不會被新 OCR run 靜默覆蓋。
- 主要流程有 automated tests 及人工操作測試紀錄。

**最重要的驗收場景：** 以一份真實澳門銀行 Balancete PDF，從 Import → OCR → Review Queue → PDF 對照 → 人工修正 → Verify → Publish Readiness → Publish 全流程走通，並能追溯任一已發布金額的原始 PDF 位置與修正紀錄。

