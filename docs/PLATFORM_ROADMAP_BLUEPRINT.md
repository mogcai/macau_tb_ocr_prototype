# Macau Banking Intelligence Platform — 整體藍圖及工作計劃

> **文件類型：** Product / Architecture / Delivery Blueprint  
> **版本：** v1.0  
> **規劃基準日期：** 2026-11-19  
> **現有基礎：** [`mogcai/macau_tb_ocr_prototype`](https://github.com/mogcai/macau_tb_ocr_prototype)  
> **文件狀態：** Proposed — 開發前須完成現有程式及資料庫盤點

---

## 1. 項目定位

將現有澳門銀行公報 Balancete OCR Prototype，逐步發展成一個長期運作、可自動更新、可追溯及可驗證的澳門銀行財務數據平台。

平台不只是將 PDF 轉換成 Excel，而是建立一條完整、可審計的數據生命週期：

1. 從官方來源發現及取得公報 PDF。
2. 保留原始文件及檔案指紋。
3. 使用現有 OCR / 表格重建流程抽取資料。
4. 進行格式、結構及財務規則檢查。
5. 由人工覆核有風險或需要判斷的資料。
6. 只將符合發布條件的資料發布到正式數據集。
7. 累積跨銀行、跨期數據，提供查詢、比較及互動分析。

### 1.1 三個產品目標

| 時間方向 | 目標 | 主要成果 |
|---|---|---|
| 短期 | 自動抽取準確可信的澳門銀行公報數據 | 自動導入、OCR、驗證、人工核實及受控發布 |
| 中期 | 整合澳門各銀行多年數據 | 統一銀行主檔、科目概念、歷史數據庫及資料覆蓋報告 |
| 長期 | 建立 Interactive Dashboard | 銀行比較、歷史趨勢、行業分析及可追溯 Data Explorer |

### 1.2 成功原則

**寧可將不確定數據留在待核實狀態，也不可將錯誤數據靜默發布。**

每一筆已發布數據均應可回答：

- 來自哪一份官方文件？
- 來自哪一頁、哪個表格位置？
- 使用哪一個 OCR run、程式版本及設定？
- 原始 OCR 值是甚麼？有否人工修正？
- 使用哪個銀行／科目 mapping 版本？
- 有沒有通過自動驗證及人工核實？
- 何時、由誰發布？

---

## 2. 現有 Prototype 基線及原則

本計劃以現有 Repository 為起點，並非推倒重做。現時主 README 已列出 v0.7.0 的 One-command Issue Import 功能；Repository 同時已有 SQLite 資料庫、OCR run、銀行及科目標準化、Review CSV、staging / publish 等基礎。

### 2.1 已有能力（按現有 Repository 文件）

| 現有能力 | Repository 證據 | 本計劃處理方式 |
|---|---|---|
| Python OCR pipeline | `macau_tb_ocr.py`、README | 保留，先建立穩定介面及回歸測試 |
| Apple Vision / macOS OCR worker | `vision_ocr_worker.swift`、`MACOS_VISION_OCR.md` | 保留作 macOS OCR worker；不可假設可直接在 Linux 容器執行 |
| PaddleOCR / Tesseract 依賴選項 | `requirements-*.txt` | 保留支援矩陣，按實際 benchmark 決定 routing |
| 多語言及金額格式處理 | README、config、tests | 建立獨立 benchmark，繼續保留原始文字及解析證據 |
| Ground truth / regression tests | `ground_truth/`、`tests/` | 擴大成分銀行、語言、版面及數字格式的驗收集 |
| SQLite 整合資料庫 | `DATABASE.md` | 短期沿用；先設 migration tests，再決定何時轉 PostgreSQL |
| OCR runs / observations | `runs`、`pages`、`statements`、`line_items` | 不覆蓋歷史 observation |
| 人工核實紀錄 | `row_verification` | 保留；Web UI 透過 API 管理，不直接改寫 OCR 原始值 |
| 銀行及科目 Registry | `banks`、aliases、concept / mapping review | 沿用既有 registry；補齊版本及 Web 管理操作 |
| Issue 自動下載及 Import | `tools/import_issue.py`、`IMPORT_ISSUE.md` | 包裝成可重試的背景 Job，不重寫核心流程 |
| Staging / Publish 保護 | `is_published`、`--publish` 等 | 沿用，擴展成 Web 審批及發布流程 |

> **版本文件注意：** Repository 主 README 的版本資訊與部分繁中說明文件顯示的版本可能不同。實作前應以 `main` branch 的實際程式、`VERSION`、`CHANGELOG.md` 及各文件逐一核對，先完成文件同步；不可只依賴單一 README 判斷現況。

### 2.2 不應破壞的現有設計

1. `label_raw`、原始銀行名稱及原始金額不可被標準化值覆蓋。
2. 每次 OCR run / observation 均要保留，不可靜默覆蓋舊結果。
3. OCR 自動檢查通過不等於人工財務核實通過。
4. 銀行及科目 mapping 的候選配對，未經批准不可自動合併不同實體／概念。
5. 發布前必須檢查完整性，避免局部或不完整 run 取代較完整的正式 run。
6. 同一報表可能跨頁、同一科目可能重複出現；不得以表面上相似的五欄資料直接當作唯一 Primary Key。

### 2.3 第一個技術工作：Baseline Audit

在改動程式前，先記錄：

- Git commit hash、branch、`VERSION` 及實際安裝依賴版本。
- 現有 SQLite schema version、tables、views、triggers 及 row counts。
- 主要 CLI 工具、輸入／輸出檔案格式及參數。
- 現有 Issue import 的完整步驟及失敗處理。
- Vision / Paddle / Tesseract 各自可以執行的 OS、語言及限制。
- 現有 ground truth 覆蓋的銀行、語言、年份、頁面類型及金額格式。
- 所有現有 Review / Publish 行為及已知限制。

**交付物：** `CURRENT_STATE_AUDIT.md`、資料庫 schema snapshot、現有測試報告及待修問題清單。

---

## 3. 產品及系統架構

### 3.1 邏輯架構

```text
                 Official Gazette / Manual PDF Upload
                                |
                                v
                     1. Source Acquisition
              Discover / Download / Hash / Register
                                |
                                v
                       2. Document Store
                 Original PDF + Metadata + Hash
                                |
                                v
                      3. Extraction Pipeline
          Page Detection -> OCR -> Table / Row Reconstruction
              -> Bank / Date Detection -> Label Mapping
                   -> Number Normalization
                                |
                                v
                     4. Quality & Review
          Format Checks / Structural Checks / Financial Rules
                    -> Review Queue -> Human Review
                                |
                                v
                       5. Controlled Publish
                                |
                                v
                       6. Data Platform
              Raw Observations / Standardized / Curated
                                |
                    +-----------+-----------+
                    |                       |
                    v                       v
               Admin API              Analytics API
                    |                       |
                    v                       v
             Operations UI          Interactive Dashboard
```

### 3.2 部署建議

初期採用 **Modular Monolith + Background Worker**，避免過早拆分 Microservices。

| 部分 | 建議方案 | 說明 |
|---|---|---|
| OCR / Extraction | 現有 Python pipeline | 保留核心演算法及 CSV / JSON 輸出契約 |
| Vision worker | macOS worker process | Apple Vision 依賴 macOS；與 Linux Web Server 分開 |
| Backend API | FastAPI | 提供 import、review、registry、publish、query API |
| Frontend | Next.js + TypeScript | Admin Portal 及未來 Dashboard 共用前端基礎 |
| Database（短期） | 現有 SQLite | 單機 / MVP 階段先沿用，避免不必要的即時遷移 |
| Database（正式多用戶） | PostgreSQL | 在需要併發寫入、權限、可靠部署前完成遷移 |
| Original PDF storage | 本機受控儲存起步；其後 S3-compatible Object Storage | PDF、頁面圖片及大型產物不應塞入主要交易表 |
| Background jobs | 初期採簡單持久化 Job Queue | 先支援 retry、timeout、heartbeat、idempotency；不急於 Kafka |
| Deployment | Docker（Web/API/DB）+ macOS worker | OCR worker 與一般 Linux 容器分離 |
| CI | GitHub Actions | Unit、integration、migration、regression tests |

技術選型屬建議，進入實作前需按現有程式依賴、硬件及部署環境做小型 PoC。不要為了追求技術完整而引入不必要基建。

### 3.3 核心資料分層

| Layer | 內容 | 是否可修改 |
|---|---|---|
| Raw | 原始 PDF、原始 OCR、頁面及 bounding boxes、原始欄位 | 不可覆蓋；只可追加新版本或新 observation |
| Standardized | 正規化日期、金額、銀行 ID、科目概念及 mapping 版本 | 可由新版本重算，但要保留版本／歷史 |
| Curated / Published | 通過發布規則的正式資料 | 受控修訂；必須有 audit trail 及可回滾發布版本 |

### 3.4 重要資料模型概念

以下是邏輯實體，不代表要立即新增同名資料表。先對照現有 schema，避免重複造表。

- `gazette_issues`：公報年度、Issue number、出版日期、官方來源及監察狀態。
- `source_documents`：原始 PDF、URL、SHA-256、檔案大小、下載時間及儲存位置。
- `runs`：一次 OCR / extraction run、版本、設定、狀態及執行紀錄。
- `pages`：PDF 頁數、頁面影像、OCR 狀態、偵測結果。
- `statements`：銀行、報表日期、出版日期、報表類型、涵蓋頁面。
- `line_items`：逐行 OCR observation、raw / normalized label、金額及位置證據。
- `row_verification`：人工核實狀態、核實人、時間及備註。
- `validation_results`：規則 ID、嚴重程度、結果、訊息及規則版本。
- `field_corrections`（建議擴展）：欄位原值、修正值、修正原因、核實人及時間；不可覆寫 raw observation。
- `audit_logs`：對資料、mapping、狀態及發布的可追溯操作紀錄。
- `banks` / `bank_aliases` / `line_item_concepts` / aliases / mapping review：沿用現有 registry 概念。

### 3.5 金額及日期規則

- 金額解析必須使用 `Decimal` 或精確整數最小貨幣單位；不可用 binary `float` 做財務值。
- 原始字串必須保存，例如 `1,234,567.89`、`1.234.567,89`、`-`、空白等。
- 正規化結果必須保留使用的 locale / number-format 判斷及解析狀態。
- 遇到格式歧義、負數符號不清、千分位／小數點不確定時，保留為 unresolved，禁止猜測後自動發布。
- `publication_date`、`statement_date`、下載日期及 OCR 執行日期必須分開。
- 必須保存 currency；不可假設所有原始金額永遠使用同一貨幣或單位。

---

## 4. 發展階段及 Milestones

時間只作單人／小型開發團隊的初步容量估算；實際時間由 Baseline Audit、歷史文件數量及測試結果調整。每個 milestone 均以驗收條件判斷完成，不以畫面完成或程式可執行作唯一標準。

### Phase 0 — Foundation & Baseline Audit

**目的：** 了解現有功能、鎖定數據契約及建立安全開發基線。  
**估算：** 1–2 星期。

工作項目：

- [ ] 核對 `main` branch、版本、程式、依賴及 docs。
- [ ] 匯出 SQLite schema、views、triggers 及現有 row counts。
- [ ] 梳理 `import_issue.py` 的步驟、輸出、錯誤及 staging / publish 行為。
- [ ] 建立現有 CLI 的 integration tests。
- [ ] 備份現有 PDF、OCR output、SQLite DB 及 registry CSV。
- [ ] 建立 `docs/` 架構及 ADR（Architecture Decision Records）流程。

**交付物：** `CURRENT_STATE_AUDIT.md`、schema snapshot、known issues list、baseline test report。

**完成標準：** 開發團隊可以明確指出每項現有能力的程式入口、輸入輸出、資料表及測試方法；可以安全重跑現有 Issue import 而不破壞舊資料。

### Phase 1 — Reliable Extraction & Data Quality

**目的：** 先提升數據可信度，再擴大自動化。  
**估算：** 3–6 星期，視乎 benchmark 補充量。

工作項目：

- [ ] 整理 OCR engine interface，讓 Vision / Paddle / 其他 engine 可按設定選擇。
- [ ] 固定輸出 schema 及欄位定義，避免每個 OCR engine 輸出格式不同。
- [ ] 補充繁中、葡文、英文及中葡混排 Ground Truth。
- [ ] 覆蓋不同年份、銀行、掃描質素、表格版面及多頁報表。
- [ ] 建立 amount parsing tests：comma-dot、dot-comma、負數、空白、dash、零、小數位及 OCR punctuation errors。
- [ ] 建立 bank / statement identity、parent-child、跨頁、重複科目測試。
- [ ] 將 OCR confidence、normalization status、validation status、human verification status 分開。
- [ ] 建立 Validation Rules Registry，規則要有 ID、版本、嚴重程度及測試案例。
- [ ] 產生每次 run 的品質報告及差異報告。

**交付物：** Ground Truth benchmark、OCR regression suite、number parser spec、validation rules v1、quality report。

**完成標準：** 新 OCR / normalization 版本必須通過固定 benchmark；所有異常有明確狀態及原因；不得以 OCR confidence 單獨作為發布條件。

### Phase 2 — Data Verification Workbench (MVP)

**目的：** 建立方便、可追溯的人工覆核介面。這是短期 Web App 的核心，優先於 Dashboard。  
**估算：** 3–5 星期作 MVP，再按使用情況改善。

工作項目：

- [ ] Import Batch / Issue 管理介面。
- [ ] Review Queue：可按 Issue、銀行、頁數、異常類型、OCR confidence、validation severity 篩選。
- [ ] PDF + extraction table split-screen。
- [ ] 點選 line item 後跳到 PDF 頁面及 bounding box（有座標時）。
- [ ] 顯示 raw value、normalized value、standard label 及 validation evidence。
- [ ] 支援 inline correction，原始 OCR 值不可覆蓋。
- [ ] 支援 verify / reject / needs investigation / save draft。
- [ ] 保留 reviewer、時間、修改前後值及原因。
- [ ] 加入安全批次核實及發布前檢查。
- [ ] Web 操作與現有 SQLite / Import pipeline 整合。

**交付物：** Data Verification Workbench、Review API、Audit trail、操作手冊及 MVP acceptance test。

**完成標準：** 使用者可由 UI 完成上載／選擇文件、查看抽取結果、對照 PDF、修正、核實、查看 audit 及受控發布；UI 不得直接修改 raw OCR output。

> 詳細 UI 規格見 [`DATA_VERIFICATION_WORKBENCH_SPEC.md`](DATA_VERIFICATION_WORKBENCH_SPEC.md)。

### Phase 3 — Gazette Monitoring & Automated Import

**目的：** 由人工啟動 Issue import 進一步變成持續監察及自動處理。  
**估算：** 2–4 星期，視乎官方網站穩定性及是否有可靠的公開索引。

工作項目：

- [ ] 研究官方公報可用的索引頁、RSS、API 或穩定 URL 規則；不得假設 URL 永遠固定。
- [ ] 定期檢查新 Issue，記錄 last checked time 及監察錯誤。
- [ ] 新文件以 URL、Issue metadata、SHA-256 去重。
- [ ] 新發現文件建立 import job；沿用 `tools/import_issue.py` 的核心流程。
- [ ] 支援 retry、timeout、heartbeat、cancel、resume 及 idempotency。
- [ ] 下載內容與已知 hash 不同時，保留版本並要求明確處理，不可靜默覆蓋。
- [ ] OCR / DB import 失敗時保留 manifest、log 及明確錯誤狀態。
- [ ] 新資料預設 staging；不可未經驗證自動發布。
- [ ] 提供 email / app notification（通知渠道可後定）。

**交付物：** Gazette Monitor、Job Scheduler、Import Job Dashboard、retry / alert handling。

**完成標準：** 可測試發現新 Issue、去重、下載、OCR、staging、失敗重試及告警；整個流程可追溯，任何失敗不會令舊 published data 消失。

### Phase 4 — Operations Portal & Production Hardening

**目的：** 把內部管理功能整理成可靠的日常操作系統。  
**估算：** 2–4 星期，可與 Phase 2 / 3 部分並行。

工作項目：

- [ ] System Overview：Job 狀態、待覆核數量、最後成功監察時間。
- [ ] Issue / PDF / Run / Statement / Review 管理頁。
- [ ] 使用者登入及基本角色權限（Admin / Reviewer / Read-only）。
- [ ] Audit log、錯誤紀錄、資料庫備份及 restore test。
- [ ] 設定管理與 secrets 管理；不可將 API keys / passwords 放入 repository。
- [ ] GitHub Actions 自動測試及部署流程。
- [ ] 建立 staging 環境，正式資料庫更新前先行測試。

**交付物：** Internal Operations Portal、role permissions、backup / restore runbook、deployment guide。

**完成標準：** 一般日常導入與核實不再依賴手動執行 CLI；有權限控制；可復原資料庫；錯誤可追蹤。

### Phase 5 — Historical Data Platform

**目的：** 穩定整合多銀行、多年份、多期數資料。  
**估算：** 4–8 星期起步；實際歷史回填時間取決於公報數量及 PDF 質素。

工作項目：

- [ ] 設計正式 PostgreSQL schema，先做 SQLite → PostgreSQL migration PoC。
- [ ] 建立 migration mapping、row-count reconciliation、hash / totals reconciliation。
- [ ] 保留原始 PDF、OCR runs、observations、review records、registry IDs 及 published status。
- [ ] 建立穩定 bank IDs，銀行名稱／分行／集團關係須明確分類。
- [ ] 沿用現有 label concepts 及 aliases；跨語言概念合併須人工批准。
- [ ] 建立 Data Dictionary：欄位、定義、幣種、單位、方向及可用年度。
- [ ] 建立 Historical Coverage Report：按年度、Issue、銀行、報表及科目顯示覆蓋率。
- [ ] 對歷史文件分批 import、核實及發布。
- [ ] 設定 reprocessing policy：新 OCR / mapping 版本可重跑，但舊 observation 保留。

**歷史回填建議次序：**

1. 先完成最近一期至最近 1–2 年的代表性資料，建立穩定流程。
2. 再按年度向前回填，並優先處理文件質素較好、銀行覆蓋較高的年份。
3. 每批完成後出 Coverage / Exceptions Report，不以「已跑 OCR」當成「已完成整合」。

**交付物：** PostgreSQL production schema、migration report、bank / label master、historical coverage dashboard、歷史數據匯入報告。

**完成標準：** 可跨銀行／跨年度查詢；抽樣核對遷移前後一致；每筆 curated data 可追溯原始 source；缺失及未核實資料清楚標示。

### Phase 6 — Financial Analytics Layer

**目的：** 先建立穩定的指標定義，再製作圖表。  
**估算：** 2–4 星期作第一批指標。

工作項目：

- [ ] 建立 analytics service / query layer，不把財務計算散落在前端。
- [ ] 每個指標有名稱、定義、公式、必要欄位、幣種／單位、期間邏輯及 caveats。
- [ ] 區分直接披露值、系統計算值及估算值。
- [ ] 只計算原始披露資料足夠支持的指標。
- [ ] YoY / QoQ 比較時檢查報表日期、期間、幣種及報表性質是否可比。
- [ ] 如有缺期、銀行合併、名稱變動或科目分類變動，需標示比較限制。

第一批可考慮：Total Assets、Total Liabilities、Equity（如披露）、Cash / Deposits、Loans、Customer Deposits、資產結構及可比期間增長。

ROA、ROE、NPL、資本充足率等指標只有在所需分子、分母及定義均有可靠來源時才加入；不能只靠 Balancete 不完整欄目推算。

**交付物：** Metric Dictionary、Analytics API、指標測試集及計算結果核對報告。

**完成標準：** 每個展示指標都可解釋其來源、公式及適用限制；前端與後端計算結果一致。

### Phase 7 — Interactive Dashboard

**目的：** 將已核實數據變成可互動的研究工具。  
**估算：** 3–6 星期作首個版本。

核心頁面：

- [ ] Macau Banking Overview：行業總量、最新資料日期、銀行覆蓋。
- [ ] Bank Profile：單一銀行歷史財務數據及科目結構。
- [ ] Bank Comparison：選擇多間銀行及共同報表日期比較。
- [ ] Historical Trends：跨年／跨期趨勢。
- [ ] Data Explorer：可篩選、檢視及匯出資料。
- [ ] Source Detail：點擊數字查看來源 PDF、頁數、原始值及核實狀態。

**交付物：** Interactive Dashboard、Data Explorer、CSV / Excel export、使用說明。

**完成標準：** Dashboard 不顯示無來源的數字；比較視圖會提示不可比的報表；匯出內容保留資料日期、單位及 source reference。

---

## 5. Data Quality、Review 及 Publish 政策

### 5.1 狀態必須分開管理

| 狀態維度 | 例子 | 含義 |
|---|---|---|
| OCR confidence | engine score | OCR engine 對辨識結果的信心；不是財務正確率 |
| Normalization status | parsed / ambiguous / failed | 數字、日期或文字是否成功正規化 |
| Mapping status | approved / pending / unmatched | 銀行或科目是否對應已批准的 Registry |
| Validation status | passed / warning / failed | 自動規則檢查結果 |
| Human verification status | pending / verified / rejected | 人工覆核結果 |
| Publication status | staging / published / superseded | 是否屬正式可用資料 |

不得將以上狀態壓成單一綜合分數，亦不可用 OCR confidence 直接推斷 `verified`。

### 5.2 Validation severity

- **Error / Critical：** 資料不可發布，例如必要欄位缺失、金額解析歧義、bank identity 未確定、來源文件／頁碼無法對應。
- **Warning：** 需要人工判斷，例如異常大幅變動、未核准 mapping、OCR 低信心、可疑重複行。
- **Info：** 提醒性資訊，例如使用了 fallback OCR、日期由同一 PDF 其他頁推斷。

具體規則須逐項以澳門銀行公報格式及人工核實結果建立，不能直接假設國際會計報表規則一定適用。

### 5.3 Publish gate

每個 statement / run 發布前必須檢查：

- source PDF 及 SHA-256 存在。
- 該 statement 所需頁面完整；沒有非預期漏頁。
- Bank identity、statement date、publication date 及 currency 等必要識別資料有清楚狀態。
- 所有 Critical errors 已解決。
- 需要人工核實的項目已核實或按明確批准的例外政策處理。
- 應抽樣的自動通過資料已抽樣檢查。
- 發布使用的 OCR、mapping、validation rule 版本已記錄。
- 發布操作及操作者已記錄。

任何 `force publish` 必須有權限限制、原因及 audit record；不能作為一般工作流程。

### 5.4 KPI / 衡量方法

先建立 baseline，再設定正式目標。建議至少量度：

| 指標 | 定義 |
|---|---|
| Exact amount accuracy | 在人工標註 benchmark 中，正規化後金額完全相同的欄位比例 |
| Label mapping precision | 被系統自動接受的 mapping 中，經 ground truth 證實正確的比例 |
| Row completeness | 應有的列／欄位成功抽取並對應的比例 |
| Critical error escape rate | 已發布資料中，事後發現 Critical error 的比例 |
| Source traceability | 可追溯到 PDF、頁數及位置證據的已發布欄位比例 |
| Review workload | 每 100 行需要人工處理的列數及平均處理時間 |
| Import reliability | 成功完成 download / OCR / staging 的 job 比例 |
| Recovery time | 失敗 job 從重試到恢復的時間 |

最終目標應由 benchmark 數據決定。可先把 **已發布資料 source traceability = 100%**、**Critical error 不得未處理即發布** 作制度性要求；OCR exact accuracy 及 mapping precision 則先量度，再逐版提升，不應在未測試前宣稱已達某個百分比。

---

## 6. 安全、備份及營運要求

- PDF、OCR output、SQLite / PostgreSQL、設定檔及 registry 都要有備份策略。
- 定期做 restore test；「備份成功」不等於「可成功還原」。
- 使用環境變數或 secret manager 保存憑證；禁止提交 `.env`、token 或私鑰。
- Admin、Reviewer、Read-only 權限分開；發布權限不應預設給所有人。
- 所有 correction、mapping approval、reject、publish、force publish 都要記錄操作者及時間。
- Job 必須有 idempotency key；重試不能產生重複正式資料。
- 保留錯誤 Log，但避免在 log 中暴露 secrets。
- 建立 staging / production 分離；新 migration 先在 staging 驗證。
- 任何 schema migration 前備份資料庫並測試 rollback / restore 方案。

---

## 7. Repository 演進建議

初期保留單一 Repository，不急於拆分多個 repos。目標可逐步整理如下；這是演進方向，不是要求一次過搬完：

```text
macau-banking-platform/
├── apps/
│   ├── api/                  # FastAPI
│   └── web/                  # Next.js
├── services/
│   ├── ocr_engine/           # 由現有 Python OCR 模組整理
│   ├── vision_worker/        # macOS / Swift worker adapter
│   ├── import_pipeline/      # 包裝現有 import_issue 流程
│   ├── validation/           # 可測試的規則引擎
│   └── analytics/            # 指標計算及查詢
├── database/
│   ├── migrations/
│   ├── schema/
│   └── seed/
├── config/
│   ├── banks/
│   ├── labels/
│   └── validation_rules/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   └── golden_dataset/
├── docs/
├── infrastructure/
└── README.md
```

遷移原則：每次只搬一個模組，保持舊 CLI 可用，先有測試再重構；不得以大量搬檔取代功能驗收。

---

## 8. 建議開發順序及優先級

| 優先級 | 工作 | 原因 |
|---|---|---|
| P0 | Baseline Audit + 備份 + 測試基線 | 避免在不了解現況下破壞現有資料 |
| P0 | Ground Truth / OCR / 數字解析驗收 | 建立可信度量度 |
| P0 | Data Verification Workbench MVP | 人工核實是短期可信數據的關鍵瓶頸 |
| P1 | Import Job / Gazette Monitoring | 將現有 one-command import 自動化 |
| P1 | Admin Portal / Audit / Backup | 日常營運及可靠性 |
| P1 | PostgreSQL migration | 當多用戶及正式部署需求出現時完成 |
| P2 | 歷史資料回填及 Coverage | 建立跨銀行、跨年度資料基礎 |
| P2 | Analytics API / Metric Dictionary | 確保分析定義一致 |
| P3 | Interactive Dashboard | 最後呈現已驗證的數據 |

### 粗略總時間

以一名主要開發者、兼顧測試及文件為假設，完整版本可先按 **約 6–9 個月** 作容量規劃。若歷史回填量大、官方網站變動頻繁或 UI 需要大量調整，時間會增加。應以每個 milestone 的驗收結果重新估算，不應為趕時間降低數據核實標準。

---

## 9. 首個 Sprint：即時可執行的工作清單

1. [ ] 確認 GitHub `main` branch 的最新 commit、`VERSION`、README 及 CHANGELOG 一致性。
2. [ ] 將目前 SQLite DB、PDF、OCR output、labels、ground truth 做完整備份。
3. [ ] 執行現有 test suite，保存 baseline 結果。
4. [ ] 抽取現有資料庫 schema / view / trigger / counts。
5. [ ] 選定 2–3 份代表性 Issue，涵蓋中文、葡文及不同銀行版面，作 UI MVP test fixture。
6. [ ] 明確定義 review queue 的資料欄位及 row-level / field-level 狀態。
7. [ ] 先完成 Workbench clickable prototype / wireframe，再定 API 與資料庫擴展。
8. [ ] 為每一個 correction、verify、reject 及 publish 操作定義 audit event。

**首個可交付版本的完成定義：** 一份 PDF 經現有 Import Pipeline 進入 staging，使用者可在 Web UI 對照原文核實／修正，系統保存原始值、修正紀錄及 reviewer，再通過 publish gate 發布；整個流程可由 source PDF 追溯至正式數據。

---

## 10. 相關現有文件

- [Repository](https://github.com/mogcai/macau_tb_ocr_prototype)
- [README](https://github.com/mogcai/macau_tb_ocr_prototype/blob/main/README.md)
- [DATABASE.md](https://github.com/mogcai/macau_tb_ocr_prototype/blob/main/DATABASE.md)
- [IMPORT_ISSUE.md](https://github.com/mogcai/macau_tb_ocr_prototype/blob/main/IMPORT_ISSUE.md)
- [NAME_STANDARDIZATION.md](https://github.com/mogcai/macau_tb_ocr_prototype/blob/main/NAME_STANDARDIZATION.md)
- [MACOS_VISION_OCR.md](https://github.com/mogcai/macau_tb_ocr_prototype/blob/main/MACOS_VISION_OCR.md)

