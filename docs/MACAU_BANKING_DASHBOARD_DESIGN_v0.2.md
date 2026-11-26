# 澳門銀行業數據分析 Dashboard — 設計規格

- **文件版本**：v0.2
- **日期**：2026-11-24
- **狀態**：已整合 PR #3 chart-first prototype；包含程式審視事項；待確認行業統計口徑及指標定義
- **依據專案**：`mogcai/macau_tb_ocr_prototype`
- **目標資料庫**：V3 operational database（`data/macau_balancete_v3.sqlite`）
- **介面定位**：只讀分析平台；不取代 Workbench，不直接修改 OCR、核實或採納資料

---

## 1. 目的與產品定位

建立一個以圖表為主的澳門銀行業財務數據 Dashboard，將澳門《政府公報》內各銀行 Balancete／財務報表整理成可追溯的時間序列及跨銀行比較資料。

Dashboard 應回答四類問題：

1. **澳門銀行業整體**：銀行業資產、負債、存款、貸款、盈利及財務結構如何變化？
2. **單一銀行**：某銀行的資產負債結構、盈利表現及歷史走勢如何？
3. **跨銀行比較**：同一結算日，各銀行的規模、結構及財務指標有何差異？
4. **資料可信程度**：每個數字來自哪份報表、哪一行、哪次採納／發布？有多少資料未覆蓋或未完成核實？

Dashboard 不應只是一個把 OCR 金額畫成圖表的介面。它必須保留 V3 的資料治理原則：原始 OCR evidence 不可改寫、有效列要反映核實及人工調整、mapping 狀態要透明、報表身份及發布狀態要可追蹤。

### 1.1 核心設計原則

- **V3 是唯一 operational source of truth**。不得以舊 V5 SQLite、legacy CSV 或 `archive/` 內資料作正式 dashboard 數據來源。
- **只讀**。Dashboard 不採納報表、不核實欄位、不修改 mapping，也不寫入 Workbench。
- **不把缺失當成零**。沒有報表、未採納、沒有對應科目或值不可讀，必須分開表示。
- **不把 OCR／mapping 狀態當成財務確認**。OCR confidence、`auto_check`、借貸平衡、`proposed` mapping 均不能單獨證明金額正確。
- **每個指標有明確公式及口徑**。不可只憑相似科目名稱推斷其財務含義。
- **跨銀行加總先於畫圖前完成口徑審批**。沒有完整機構範圍及去重規則時，不得標示為「澳門銀行業總計」。
- **每個數字可追溯**。由圖表可下鑽到銀行、報表、科目列、原始 OCR label、核實值及 PDF 頁面。

---

## 2. 現有 V3 Schema 與可用資料

### 2.1 主要資料實體

| V3 table / view | Dashboard 用途 | 主要欄位／注意事項 |
|---|---|---|
| `reporting_entities` | 銀行／分行／集團 registry | `entity_type`、`canonical_zh/pt/en`、`parent_entity_id`、`registry_status`、`merged_into_entity_id` |
| `reporting_entity_aliases` | 銀行名稱追溯 | 中文、葡文、英文名稱及 alias 審批狀態 |
| `statements` | 每一份機構財務報表的主檔 | `statement_date`、`publication_date`、`statement_type`、`reporting_scope`、`currency`、`unit_scale`、`identity_status`、`metadata_status`、`statement_status`、`adopted_statement_extraction_id` |
| `statement_extractions` | 每次報表抽取紀錄 | `extraction_status`、run、source document；一份 statement 可以有不同 extraction attempts |
| `extracted_rows` | 原始 OCR row evidence | `label_raw`、原始 debit/credit、解析值、value state、row type proposal、OCR confidence；不可當成最終有效列 |
| `statement_rows` | 已採納 extraction 的 row snapshot | append-only；保留採納時的列、順序、父子關係及原始值 |
| `v_current_statement_rows` | 當前 statement rows | 根據 adopted extraction 及有效 exclude/insert adjustments 組合 |
| `v_effective_statement_rows` | Dashboard 應使用的有效資料列 | 在 current rows 上套用已核實的欄位值；不可自行重建另一套 effective-row 邏輯 |
| `statement_row_adjustments` | 人工補列／排除列 | active `insert` 會加入有效列；active `exclude` 會把 OCR row 排除；原始 evidence 保留 |
| `standard_items` | 標準財務科目主檔 | `item_key`、中葡英文名稱、parent、`category`、`item_status`、merge 狀態 |
| `standard_item_aliases` | 標準科目 alias | 可按機構 scope；alias 本身亦有 status |
| `statement_row_mappings` | OCR row → standard item | `mapping_status`、`match_method`、`match_score`、reviewer；`proposed` 不等於核准 |
| `field_verifications` | 欄位核實 | 可核實 label、debit/credit amount、value state、row type、parent 等；`verified` 欄位由 effective view 採用 |
| `publication_records` | 正式發布及品質等級 | `published/revoked/superseded`、`quality_level`；發布必須對應 adopted extraction |
| `validation_findings` / `review_tasks` | 品質及工作流程 | 用於資料品質面板及發佈閘門 |
| `v_statement_quality_summary` | 報表品質摘要 | current row count、approved mapping count、open findings/tasks、publication quality |

### 2.2 Dashboard 取數規則

建議的查詢流程：

1. 由 `statements` 取得 statement metadata，join `reporting_entities`。
2. 只選符合 Dashboard 資料層級條件的 statement（見第 4 節）。
3. rows 一律由 `v_effective_statement_rows` 取得，不可直接把 `extracted_rows` 或 `statement_rows` 全部加總。
4. mapping 只取 `statement_row_mappings.mapping_status = 'approved'`，並 join `standard_items` 檢查 `item_status` 及 merge 狀態。
5. 所有派生指標由已核准的 indicator definition 計算；不應在前端臨時根據 label 名稱做 fuzzy matching。
6. 查詢及計算同時保留 `statement_id`、`statement_row_id`、`standard_item_id`、`publication_id` 等 provenance keys。

### 2.3 重要 Schema 限制

V3 已提供足夠資料建立基礎的報表、機構、row、mapping、核實及發布流程，但並未完整定義所有銀行財務指標所需的語義。特別是：

- `standard_items.category` 未必足以表示一個科目可否納入「總資產」、「客戶存款」或「淨利息收入」等指標。
- mapping 到同一標準科目，不代表該科目一定可直接加總；有些是父項／標題，有些是子項，有些是小計或總計。
- `row_type` 有 `heading/item/subtotal/total/...`，但仍要為每個指標設定可計算的 row rule。
- `statements` 有 `statement_date` 及 `statement_type`，但未見完整的損益表期間起訖／YTD 或季度流量 basis 欄位。
- `debit_minor`、`credit_minor` 及 `amount_decimal_places` 有助精確重建金額，但每類科目的正常餘額方向仍需要 indicator rule 明確定義。
- `publication_records` 的 quality level 不應被簡化成所有 published 數據都已完成逐欄財務核實。

因此，Dashboard 須加入一層獨立的 **Analytics Definition Layer**，不要把財務指標定義硬塞進 OCR 或 Workbench 流程。

---

## 3. Dashboard 資訊架構

建議以七個主要頁面組成：

| 頁面 | 主要用途 | 主要視覺化 |
|---|---|---|
| 1. 澳門銀行業總覽 | 查看行業規模、結構及變化 | KPI cards、時間序列、資產結構、機構覆蓋率 |
| 2. 銀行資料庫 | 瀏覽所有機構及其資料狀況 | 銀行清單、機構分類、最近報表、資料狀態 |
| 3. 單一銀行 Profile | 查看某銀行歷史財務表現 | 資產負債走勢、資產／負債結構、盈利指標 |
| 4. 跨銀行比較 | 同期比較個別銀行 | 排名／橫向 bar chart、scatter plot、ratio comparison |
| 5. Banking Indicators | 專門查看重要銀行財務指標 | 指標卡、指標時間序列、指標定義與口徑 |
| 6. 時間序列分析 | 選擇指標及期間，分析長期變化 | multi-line chart、YoY/QoQ growth、period comparison |
| 7. Data Quality & Coverage | 查看資料完整度及可信程度 | coverage matrix、mapping coverage、publication status、missing periods |

### 3.1 全站共用篩選器

- 日期／結算日範圍（由資料庫實際可用日期動態產生）
- 機構分組：本地法人銀行、外地銀行分行、關聯／集團報表、全部（按明確口徑）
- 機構（單選或多選）
- 報表類型：`balance_sheet`、`income_statement`、其他類型
- 資料品質層級：Published / Reviewed / Adopted Preview（只供內部使用）
- 指標分類：規模、資產質素、流動性、資本、盈利、效率

篩選器不應允許使用者把不相容的 `reporting_scope`、statement type 或 currency 強行放在同一個比較結果內。若不相容，系統應提示並阻止計算，而不是只顯示 warning 後繼續加總。

### 3.2 與 PR #3（Chart-first Phase-1 Dashboard）的關係

PR #3（[Make the phase-1 landing page a chart-first view](https://github.com/mogcai/macau_tb_ocr_prototype/pull/3)）是本設計的**第一個 UI 實作基礎**，不是完整金融分析層。該 PR 把現有本機首頁改為圖表優先：

- `/`：顯示一個分組／結算日／候選科目的跨機構橫條，以及所選機構的三期觀察。
- `/coverage`：保留機構覆蓋、報表狀態、mapping 及缺口明細。
- `/compare`、`/entities/{entity_id}`、`/statements/{statement_id}`：保留跨行候選對照、銀行／機構明細及 statement drill-down。
- `/api/charts`：供首頁取得圖表 view model。
- 只讀 V3、loopback Host allow-list、現有 statement eligibility 及 8 + 3 + 4 perimeter 保持不變。

**本設計採納 PR #3 的 chart-first 方向及可用互動方式，但不把 `phase1_dashboard/charts.py` 當成正式指標計算引擎。** 它現階段畫的是「一個 candidate item 在某份已採納 balance sheet 的候選金額」，不是 Total Assets、Customer Deposits 或行業總量。

現有 PR 的三個結算日 `2026-03-31`、`2026-06-30`、`2026-09-30` 只屬第一批展示窗。正式 Dashboard 的日期選擇器必須由 V3 可用資料動態產生，不能將三個日期永久寫死。

因此產品分兩層：

1. **Candidate Explorer（沿用 PR #3）**：供內部查看已採納原始報表中可對照的候選行；可以顯示 `proposed` 等 mapping 狀態，但不得加總、排名、計算 share，亦不得將候選行標示為已核定財務指標。
2. **Financial Indicators Dashboard（本設計新增）**：由 approved indicator catalogue、正式 perimeter、quality gate 及 aggregation engine 計算；只有通過更嚴格規則的結果才可出現在正式 KPI、跨行排名及澳門銀行業整體圖表。

不可將兩層合併成同一個「數值已確認」狀態。UI 必須清楚標示 `Candidate / Adopted Preview` 與 `Published Indicator` 的差別。

---

## 4. 機構範圍及「澳門銀行業總體」加總方法

這是整個 Dashboard 最重要的設計決定。**不能把現有 15 個採納機構直接相加，亦不能把 8 間本地法人銀行的總和自動稱為澳門銀行業總計。**

### 4.1 現有第一期機構分組

現有 `phase1_dashboard/scope.py` 把第一批機構分成：

| 分組 | 數目 | 現有設計原則 |
|---|---:|---|
| 本地法人商業銀行 | 8 | 各自獨立；現有第一期不計算 8 間合計 |
| 關聯報表 | 3 | 必須分列，不可當成額外銀行加入本地法人組 |
| 外地銀行分行 | 4 | 不可直接同本地法人組相加 |

以上是第一期 dashboard 的顯示 perimeter，不代表已完成澳門全體持牌銀行的正式統計清單。

### 4.2 建立明確的 Aggregation Perimeter

每一個分析期間，系統必須有一份已審批的機構範圍表。建議先以版本化 CSV／YAML 管理，不直接修改 V3 核心表：

`analytics/config/entity_perimeter.csv`

建議欄位：

| 欄位 | 用途 |
|---|---|
| `entity_id` | 對應 V3 `reporting_entities.entity_id`，不要用名稱作唯一 key |
| `canonical_name` | 顯示用名稱 |
| `perimeter_group` | `local_incorporated_bank` / `foreign_bank_branch` / `related_group` / `other` |
| `sector_aggregation_role` | `include` / `exclude_related` / `reference_only` / `pending` |
| `valid_from` / `valid_to` | 處理機構成立、退出或法人範圍改變 |
| `expected_reporting_scope` | `bank` / `branch` / `group` 等 |
| `deduplication_key` | 表示不可重複納入的法律實體或報表範圍 |
| `approval_status` | `draft` / `approved` / `retired` |
| `approved_by` / `approved_at` | 審批 provenance |
| `source_reference` / `notes` | AMCM 名單或其他正式依據及判斷說明 |

### 4.3 三種不同的總體口徑

Dashboard 應把下列統計分開顯示，不能混為一個「總數」：

1. **本地法人銀行合計**：只包括正式確認的本地法人銀行 standalone statements。
2. **外地銀行澳門分行合計**：只包括正式確認的澳門分行範圍報表。
3. **澳門銀行業統計範圍合計**：按經審批的持牌機構清單，把互不重疊的本地法人及澳門分行納入；排除集團／母行／關聯合併報表等重複範圍。

第三種口徑要先與正式監管／統計範圍核對，並記錄生效日期。未確認前，介面只能稱為「已納入機構小計」，不可稱為「澳門銀行業總計」。

### 4.4 加總的必要條件

只有同時符合以下條件，某一結算日才可產生行業總量：

- perimeter version 已審批，並且列明該日期應納入的完整機構清單。
- 每個應納入 entity 在該結算日有且只有一份符合資格的 statement。
- 各 statement 的 `statement_type` 相同，且 `reporting_scope` 與 perimeter 預期一致。
- 不包含集團合併報表、母行報表或已由其他納入實體代表的重複範圍。
- currency / unit / decimal places 一致，或已按已審批且可追溯的轉換規則標準化。
- 該指標所需的科目 mapping 及 row rule 已審批。
- 沒有缺少的必要機構、重複 statement、ambiguous mapping 或不可讀金額。
- 指標所需的資料品質層級達到設定門檻。

**覆蓋不足時不可把缺少機構當成 0。** 可以顯示「已覆蓋機構小計」，並列明 `8 / 12`、coverage `66.7%` 等覆蓋狀況，但不得將其標題寫成「全澳銀行業總計」。正式總計預設要求 100% perimeter coverage。

### 4.5 Ratio 的行業合計算法

不能把各銀行的 ratio 做簡單算術平均。應先加總其分子及分母，再計算行業加權 ratio：

- 行業 Loan-to-Deposit = 全體合資格銀行客戶貸款合計 ÷ 客戶存款合計
- 行業 ROA = 合資格銀行淨利潤合計 ÷ 平均總資產合計
- 行業 ROE = 合資格銀行淨利潤合計 ÷ 平均股東權益合計
- 行業 Cost-to-Income = 營運開支合計 ÷ 營運收入合計
- 市佔率 = 個別銀行合資格指標 ÷ 同期完整行業指標

各銀行 ratio 的簡單平均可以作為另一個明確標示的「銀行平均值」，但不能代替行業加權 ratio。

---

## 5. 指標設計：先有定義，再有圖表

所有指標必須存於一份版本化的 `analytics/config/indicator_catalogue.yml`（或 CSV），至少包含：

- `indicator_id`、中英文名稱、分類、說明
- `statement_type`、stock / flow 屬性
- 所需 `item_key` 清單及其角色（numerator / denominator / component）
- 每個 item 的 `normal_balance_side`（debit / credit）
- `row_type` 規則（item / subtotal / total）及 parent-child 規則
- `aggregation_method`（direct_total / sum_disjoint_leaf_items / ratio_of_sums）
- currency、unit、period basis 要求
- minimum data quality level
- 是否容許單一銀行展示、跨行比較及行業加總
- 定義版本、批准者、生效日期及 source reference

未有 approved indicator definition 的數字只能在 Data Explorer 以原始資料形式查閱，不可進入正式 KPI、跨銀行排名或行業合計。

### 5.1 第一階段應優先建立的核心指標

#### A. 規模及資產負債表（Balance Sheet Stock）

| 指標 | 建議定義 | 必要科目／條件 | 初步優先級 |
|---|---|---|---|
| 總資產 Total Assets | 期末總資產 | 已確認的總資產標準項；或經審批的資產明細集合 | P0 |
| 總負債 Total Liabilities | 期末總負債 | 已確認負債合計；不能把負債及權益混合 | P0 |
| 股東權益 Total Equity | 期末權益合計 | 股本、儲備、保留溢利等正確組合；不能只用股本或營運資金代替 | P0 |
| 客戶貸款 Customer Loans | 客戶貸款總額（gross / net 要分開） | 必須區分銀行同業貸款、客戶貸款及減值準備 | P0 |
| 客戶存款 Customer Deposits | 客戶存款合計 | 不包括存放於 AMCM 的資產；須分清銀行同業存款、公共機構存款及客戶存款定義 | P0 |
| 現金及央行結存 Cash & Central Bank Balances | 現金及符合定義的央行結存 | 明確定義 AMCM 存款是否納入；不得當作客戶存款 | P1 |
| 同業資產 Interbank Assets | 對銀行及金融機構的資產 | 要與客戶貸款分開 | P1 |
| 證券投資 Securities | 合資格債券／證券投資 | 須明確包括交易、投資及重估項目範圍 | P1 |
| 資產增長 Asset Growth | 本期總資產 ÷ 上期總資產 − 1 | 兩期 perimeter、scope 及科目定義一致 | P0 |

#### B. 資產負債結構比例

| 指標 | 公式 | 解讀 |
|---|---|---|
| 貸款／存款比率 Loan-to-Deposit | 客戶貸款 ÷ 客戶存款 | 反映貸款相對客戶存款的比重；不應稱為法定流動性比率 |
| 貸款／總資產 | 客戶貸款 ÷ 總資產 | 資產組合中客戶貸款的比重 |
| 存款／總資產 | 客戶存款 ÷ 總資產 | 客戶存款對資產規模的相對比例 |
| 權益／總資產 Equity-to-Assets | 股東權益 ÷ 總資產 | 資產對權益的帳面緩衝比例；不是監管資本充足率 |
| 流動資產代理比率 | 已定義的現金及高流動性資產 ÷ 總資產 | 只可稱為 dashboard proxy，不能當成 LCR |
| 資產類別佔比 | 個別資產類別 ÷ 總資產 | 分母必須用真正總資產，不可使用 Balancete `user:總計` |

#### C. 盈利及營運效率（Income Statement Flow）

| 指標 | 公式／定義 | 必要條件 | 初步優先級 |
|---|---|---|---|
| 利息收入 Interest Income | 期間利息收入 | 已確認 income statement item 及期間 basis | P1 |
| 利息支出 Interest Expense | 期間利息支出 | 要明確借貸方向及正負數表示 | P1 |
| 淨利息收入 Net Interest Income | 利息收入 − 利息支出 | 同一期間、同一 scope | P1 |
| 營運收入 Operating Income | 按審批定義的營運收入項合計 | 不可直接把所有 credit rows 加起來 | P1 |
| 營運開支 Operating Expenses | 按審批定義的營運開支項合計 | 不可混入資產負債表項目 | P1 |
| 稅後淨利潤 Net Profit | 期間稅後淨利潤 | 必須釐清 YTD / quarter / annual basis | P1 |
| Cost-to-Income | 營運開支 ÷ 營運收入 | 分子、分母定義一致；負數或零分母要特別處理 | P1 |
| ROA | 年化淨利潤 ÷ 平均總資產 | 需可靠期間流量及平均資產 | P1 |
| ROE | 年化淨利潤 ÷ 平均股東權益 | 需可靠期間流量及平均權益 | P1 |
| Net Interest Margin | 年化淨利息收入 ÷ 平均生息資產 | 需明確生息資產項及平均餘額 | P2 |

#### D. 風險及監管指標（需要額外資料）

以下不應單憑一般 Balancete 科目猜算：

| 指標 | 所需額外資料 | Dashboard 處理方式 |
|---|---|---|
| 不良貸款率 NPL Ratio | 不良貸款分類金額及客戶貸款總額 | 未有獨立分類資料時顯示「未有足夠資料」 |
| 撥備覆蓋率 Provision Coverage | 不良貸款及對應減值／撥備 | 不可以「一般監管儲備」或其他儲備科目代替 |
| 資本充足率 Capital Adequacy Ratio | 監管資本、風險加權資產（RWA） | 需要監管申報資料；不由股本／權益推算 |
| 流動性覆蓋率 LCR | HQLA、30 日淨現金流出 | 需要專門監管數據 |
| 淨穩定資金比率 NSFR | 可用穩定資金及所需穩定資金 | 需要專門監管數據 |

### 5.2 防止科目誤用及重複計算

以下規則必須寫入 indicator definitions 和 automated tests：

- `user:總計` 是 Balancete 的借貸平衡總和，不是總資產，不可作總資產、總負債或佔比分母。
- `user:AMCM存款` 是資產側存放於金管局的款項，不是客戶存款。
- `user:股本` 與 `user:營運資金` 不是同一科目，不得互換或合併成權益總額。
- 存款 parent heading 與其「澳門元／外幣」child rows 不可同時相加。
- 大西洋銀行等不同版面可能在父項標籤中直接包含貨幣資訊，須由 bank-specific mapping rule 處理，不可硬套其他銀行的 parent-child 規則。
- 不可把 subtotal / total 與其明細 rows 一起加總。
- 不可把 `proposed` mapping 當成 approved mapping；亦要檢查 `standard_items.item_status` 是否已 merged／retired。
- 同一 statement 同一標準項如出現重複、mapping conflict 或多個不同候選，該指標應標記 ambiguous，不能自行加總。

### 5.3 Debit / Credit 及金額標準化

V3 金額欄位以 minor value 儲存，應以 decimal arithmetic 精確處理，不要用 binary float 做財務加總。

建議先由測試報表確認 debit／credit 欄代表餘額方向還是期間發生額，再按科目設定正常餘額方向：

- 資產及費用類通常按 debit-nature 解讀。
- 負債、權益及收入類通常按 credit-nature 解讀。
- 若報表版式或科目實際方向不同，必須以該 statement／item 的已審批規則處理。

如果欄位確實代表 debit / credit balances，才可以按科目正常餘額方向計算 net balance；不可把 debit 與 credit 兩欄盲目相加。

概念上的金額換算：

`display_amount = minor_value / (10 ** amount_decimal_places) * unit_scale`

運算須使用 decimal，並保留原始 minor amount、decimal places、unit scale 及來源資料。

**現行範圍規則**：目前 P2-D validation worksheet 涵蓋的 113 份 statement，以 MOP、`unit_scale = 1` 作為專案層級 metadata rule；provenance 應記錄為 project/domain rule，而不是聲稱每份 PDF 都曾獨立人工核實。資料庫實際 metadata 如有不同，應顯示 exception 並停止靜默轉換。

### 5.4 存量與流量必須分開

- Balance Sheet 是某個 `statement_date` 的期末存量。
- Income Statement 是一段期間的流量。
- 若損益表金額其實是年初至今（YTD），不能直接當成單季金額。
- 只有在 fiscal year、起訖日期、報表 basis 及期間連續性均確認後，才可用本期 YTD 減上期 YTD 推算單季數字。
- 現時 V3 statement metadata 尚未完整表達 period start / YTD basis；第一階段應增加受控的 statement period metadata，或暫時只顯示原始報表期間，不產生 quarterly-only、ROA、ROE、NIM 等衍生結果。
- 年化 ROA／ROE／NIM 必須用 period length 及平均資產／權益／生息資產，不可以期末單點餘額直接冒充平均值。

---

## 6. 頁面及圖表 Wireframe 規格

### 6.1 澳門銀行業總覽（首頁）

頁面目的：讓使用者在 10 秒內了解行業規模、方向及數據覆蓋。

**頂部篩選列**：結算日、資料口徑（行業／本地法人／外地分行）、資料品質。

**第一行 KPI cards**：

- 總資產
- 客戶貸款
- 客戶存款
- 股東權益
- 期間淨利潤（只在損益期間清楚時顯示）
- 資料覆蓋率（例如已具合資格報表機構數／應有機構數）

每張 KPI card 要顯示：數值、單位、較上期變動、資料日期、覆蓋機構數、品質標記。缺乏完整行業 perimeter 時，KPI 標題須改為「已覆蓋機構小計」，並顯示 coverage，不可冒充全行業總額。

**第二行圖表**：

1. 總資產／總負債／股東權益時間序列（line chart）。
2. 資產結構（stacked bar 或 treemap）。
3. 客戶貸款與客戶存款走勢（雙線圖）。

**第三行圖表**：

1. Loan-to-Deposit、Equity-to-Assets、Cost-to-Income 等比率走勢。
2. 各機構資產規模比較（bar chart；只顯示符合相同比較口徑的機構）。
3. Data coverage matrix：銀行 × 結算日，標示 missing / adopted / published / verified。

### 6.2 銀行資料庫

以表格為主：

| 欄位 | 說明 |
|---|---|
| 銀行名稱 | canonical name，附 registry status |
| 機構類型 | bank / branch / banking group |
| 統計分組 | local bank / foreign branch / related group |
| 最近結算日 | 最近一份符合所選品質層級的 statement date |
| 報表類型 | balance sheet / income statement |
| 採納／發布狀態 | 狀態分開顯示 |
| Mapping coverage | approved rows / eligible rows |
| 財務核實 coverage | 重要金額欄位已 verified 比例 |

Merged、excluded、retired 實體不得當成一般活躍銀行，但必須可在資料品質／歷史頁面追查。

### 6.3 單一銀行 Profile

建議 tabs：

- **Overview**：規模 KPI 及最新一期摘要。
- **Balance Sheet**：資產、負債、權益結構及歷史變動。
- **Profitability**：淨利息收入、營運收入、開支、淨利潤、ROA、ROE（資料足夠時）。
- **Liquidity & Funding**：現金、AMCM 結存、同業資產、存款結構及 proxy ratios。
- **Statement Explorer**：逐行科目表，保留原始 label、standard item、debit／credit、mapping 與 verification 狀態。
- **Data History**：各期報表採納、發布、替換、品質及來源。

不要把沒有報表的期間畫成 0；以 gap marker 或空白區段表示。

### 6.4 PR #3 的 Phase-1 圖表首頁（既有實作基線）

PR #3 首頁先保留兩張簡單圖表，作為 Candidate Explorer 的過渡介面：

| 區塊 | 現有 PR #3 行為 | 本設計要求的定位 |
|---|---|---|
| 跨機構橫條 | 一個 bucket、一個 statement date、一個 candidate；按既定機構名單順序，不按金額排序 | 標題必須稱為「候選科目比較」，不可稱為資產規模排名或行業總量 |
| 單一機構時間線 | 顯示 2026-03-31、2026-06-30、2026-09-30 三期；缺期斷線 | 在三期以上資料到齊前稱為「三期觀察」；只有至少兩個有效日期才可稱為趨勢 |
| 篩選器 | bucket、結算日、candidate item、趨勢機構 | 後續改為動態日期及 indicator selector，保留機構／範圍篩選 |
| 缺口區 | 顯示無報表、無候選、ambiguous、metadata 不符等原因 | 保留原因碼及 drill-down；缺口永遠不是 0 |
| 明細入口 | `/coverage`、`/compare`、`/entities`、`/statements` | 保留作數字 provenance / data quality 的下鑽路徑 |

在正式 Indicator Dashboard 完成前，首頁圖表的標題、caption、tooltip 及匯出必須寫明：這是已採納報表中的候選 row observation；尚未代表經核准的金融指標，亦未構成澳門銀行業合計。

### 6.5 跨銀行比較

使用者可以選擇：

- 一個結算日
- 一個機構分組
- 一個指標
- 顯示金額／比例／市場份額

建議圖表：

- Horizontal bar：各銀行總資產、貸款、存款。
- Scatter plot：總資產 vs Loan-to-Deposit，bubble size 可代表資產規模。
- Ranking table：只在資料完整、口徑一致且 perimeter 已審批時開啟。

排序及 ranking 預設關閉。不能安全比較的銀行要列出原因，不應從排名中靜默消失。

### 6.6 時間序列分析

- x 軸使用 `statement_date`，不使用 publication date 或 OCR run date。
- 允許選擇銀行／行業組別及指標。
- 顯示實際 available reporting dates；不插值、不把 missing period 補零。
- 支援 QoQ、YoY growth，但必須有可比較的前一期／去年同期及一致 perimeter。
- 有機構加入／退出或統計範圍變動時，在圖表上顯示 perimeter change marker。
- balance sheet 使用期末值；income statement 顯示清楚標示的期間 basis（quarter / YTD / annual）。

### 6.7 Data Quality & Coverage

至少顯示：

- 機構應有報表數、已匯入報表數、已 review、已 adopted、已 published 數。
- 每期 statement coverage matrix。
- `identity_status`、`metadata_status` 分布。
- mapping approved / proposed / unmapped / conflict 數量。
- 重要指標的必要科目 mapping coverage。
- `field_verifications` 的 verified / pending / rejected coverage。
- open validation findings 及 open review tasks。
- unreadable、ambiguous、blank、dash 等 value state 分布。

品質頁面的目的不是只顯示一個總分，而是解釋某項指標為何可用／不可用。

---

## 7. 資料品質及顯示資格（Data Gating）

建議分成三個明確資料層級：

| 層級 | 條件 | 可否進正式 KPI／跨行／行業合計 |
|---|---|---|
| **Adopted Preview** | `statement_status = active` 且有 `adopted_statement_extraction_id` | 只供內部預覽；必須標示未正式發布 |
| **Published Reviewed** | 有 current `publication_records`，publication 對應 adopted extraction，且 `quality_level` 達到經審批門檻 | 可用於一般 dashboard，但重要指標仍須有 approved mapping 及欄位品質條件 |
| **Fully Verified** | Published 且 `quality_level = fully_verified`，指標所需 rows／amount fields 已核實 | 可用於高可信度分析及正式匯出 |

`quality_checked`、`reviewed`、`fully_verified`、`legacy_unverified` 必須按原值顯示，不應全部簡化成一個「verified」標籤。正式行業總計的最低門檻建議為 Published Reviewed；如指標屬重要財務比率，按 item rule 要求 fully verified 的必要金額欄位。

### 7.1 Statement gate

正式分析至少要檢查：

- statement 為 active，未 excluded / merged / retired。
- 有 adopted extraction，且 extraction status completed。
- current publication 存在並指向 adopted extraction（正式發布層）。
- `reporting_entity_id` 及 `statement_date` 可識別。
- `reporting_scope`、currency、unit scale 符合該指標及 perimeter 要求。
- 無重複的同機構、同日期、同類型、同 scope statement。

### 7.2 Row／mapping gate

- 使用 `v_effective_statement_rows`。
- 需要的 row value state 必須是 `amount`；`unreadable`、`ambiguous` 不可當成 0。
- 重要金額的 `field_verifications` status 需達指標門檻。
- `statement_row_mappings.mapping_status = approved`。
- 對應 `standard_items.item_status = approved`，並且沒有 merged／retired 狀態。
- 同一 statement 中不可有重複或衝突的同一標準科目；如出現，須標記 ambiguous。
- 若指標依靠多個子項加總，必須確認父項、子項、subtotal、total 無重複計算。

### 7.3 候選圖表與正式指標的雙重閘門

PR #3 的候選圖表和正式 KPI 不應共用同一個 eligibility rule。建議分成兩種模式：

**A. Candidate Explorer（內部預覽）**

- 使用 active + adopted statement 及 `v_effective_statement_rows`。
- 只允許一個不同的 current standard-item candidate，並保留原始 mapping status / match method。
- 可將 `proposed` candidate 畫成暫時觀察，但必須以明顯文字標示「候選／未核准」，不得將其用作正式跨行規模、指標、排名、market share 或 sector aggregation。
- 顯示 `currency`、`unit_scale`、`amount_decimal_places`、`reporting_scope`、`row_type`、`mapping_status`；比較時檢查各觀察點的比較簽章一致。
- `registry_status` 為 `merged`、`excluded` 或 `retired` 的機構不可靜默當成現行銀行；需排除或以獨立歷史／例外狀態呈現。

**B. Published Financial Indicators（正式分析）**

- statement 通過本節 7.1 的正式 statement gate，並符合該指標所設定的 publication quality level。
- mapping 必須 `approved`；`standard_items.item_status` 必須 `approved`，且沒有 merged／retired 狀態。
- 必須符合 indicator catalogue 的 row type、parent-child、normal balance side 及重複行規則。
- 必須有明確、已確認的 `reporting_scope`、currency、unit scale 及 decimal places；跨銀行／跨期比較簽章必須一致。
- 必須解決同一候選的所有 exact / ambiguous rows；一個 exact row 不可掩蓋同份 statement 另一個含有同一候選的 ambiguous row。
- 正式行業合計另須通過 perimeter completeness、無重疊 reporting scope 及 100% required entity coverage gate。

### 7.4 缺失與警告顯示

使用下列狀態，不要只顯示空白：

- `No statement`：未有該期報表。
- `Not adopted`：有報表但未採納。
- `Not published`：已採納但未正式發布。
- `No approved mapping`：有 row，但沒有可用的 approved mapping。
- `Ambiguous mapping`：多個不同候選或重複 row。
- `Unreadable amount`：金額不可讀。
- `Not applicable`：該機構／期間不適用。
- `Zero`：只有在來源明確記錄為數值 0 時才顯示。

---

## 8. Analytics Layer 及建議程式架構

### 8.1 不改動 V3 OCR／Workbench 核心流程

建議新增獨立目錄：

```text
analytics/
  README.md
  config/
    entity_perimeter.csv
    indicator_catalogue.yml
    statement_period_basis.csv
    bank_specific_row_rules.yml
  queries/
    sector_overview.sql
    entity_timeseries.sql
    bank_comparison.sql
  service/
    repository.py
    perimeter.py
    indicator_engine.py
    quality_gate.py
    provenance.py
  tests/
    fixtures/
    test_perimeter.py
    test_indicator_engine.py
    test_no_double_count.py
    test_quality_gate.py
```

分析模組只讀 V3 DB；config 及 definition files 經 code review 後版本化。不要讓 UI request 自行拼裝 SQL 或自行改變 indicator definition。

### 8.2 分層架構

```text
V3 SQLite (read-only)
        │
        ▼
V3 Repository
  - statements / entities
  - v_effective_statement_rows
  - approved mappings
  - verification / publication / findings
        │
        ▼
Quality Gate
  - statement eligibility
  - row eligibility
  - metadata / currency / unit
  - mapping ambiguity
        │
        ▼
Analytics Definition Layer
  - entity perimeter
  - item and row rules
  - stock / flow period rules
  - indicator formulas
        │
        ▼
Aggregation Engine
  - entity-level metrics
  - cross-bank comparisons
  - sector totals and weighted ratios
  - time series
        │
        ▼
Read-only API
        │
        ▼
Dashboard UI / Data Explorer / CSV export
```

### 8.3 技術實作建議

- **短期 MVP**：沿用 `phase1_dashboard` 的 FastAPI + Jinja2 架構，新增 analytics service 及圖表頁面；不需要同時重寫 Workbench。
- **前端圖表**：可採用 Apache ECharts 或 Plotly；統一金額格式、缺口狀態、tooltip 及下鑽行為。
- **中長期**：如平台逐步發展成多使用者產品，可按 roadmap 的 Next.js + TypeScript 方向遷移前端；Analytics Definition Layer 與計算服務應保持獨立，避免綁死於特定 UI。
- **Database access**：SQLite `mode=ro`、`PRAGMA query_only`；不要與 Workbench 共用可寫 connection。
- **Cache**：以 statement set、perimeter version、indicator definition version 作 cache key；發布／撤銷後要使相關 cache 失效。
- **Security**：本機 prototype 維持 loopback only。未有登入、權限及部署安全檢查前，不可公開暴露在網絡上。

### 8.4 PR #3 現有模組的定位與演進

| 現有檔案／路由 | 目前責任 | 建議後續方向 |
|---|---|---|
| `phase1_dashboard/charts.py` | 將 candidate row 轉為 bar / 三期觀察的 view model，並產生 SVG | 保留為 Candidate Explorer adapter；不要在此加入 Total Assets、ratios 或行業合計公式 |
| `phase1_dashboard/service.py` | 讀取 V3 scope 並呼叫 chart builder | 保留 read-only orchestration；正式指標改呼叫獨立 `analytics` service |
| `phase1_dashboard/app.py` | FastAPI 路由、loopback Host policy、read-only DB | Phase-1 可延續；新增 `/api/v1/...` 時只串接 analytics service |
| `phase1_dashboard/templates/home.html` | 圖表首頁及篩選器 | 逐步由 candidate labels 升級為 indicator selector；清楚分開預覽與正式 KPI |
| `/coverage`、`/compare`、`/entities`、`/statements` | 資料覆蓋、候選對照、機構及 statement 明細 | 保留作所有正式 chart points 的 drill-down / provenance |

建議架構不要推倒 PR #3 重寫。先修正 candidate chart 的比較邊界，再將正式 indicator engine 加在 `analytics/`，由 API 將兩者分開供應。

### 8.5 建議 API

| Endpoint | 用途 |
|---|---|
| `GET /api/v1/overview` | 首頁 KPI、行業總量資格及覆蓋率 |
| `GET /api/v1/sector/timeseries` | 行業指標時間序列 |
| `GET /api/v1/entities` | 機構清單及 registry 狀態 |
| `GET /api/v1/entities/{entity_id}/profile` | 單一銀行 Profile |
| `GET /api/v1/compare` | 指定日期／分組／指標的跨銀行比較 |
| `GET /api/v1/indicators` | 指標目錄、公式、品質門檻 |
| `GET /api/v1/coverage` | 機構及期間 coverage matrix |
| `GET /api/v1/provenance/{statement_row_id}` | row-level provenance、source page 及核實紀錄 |
| `GET /api/v1/export` | 按目前篩選條件匯出 CSV，連同 units、period、quality、source IDs |

API 回應除數值外，必須附帶：`as_of_date`、`unit`、`indicator_definition_version`、`perimeter_version`、`coverage_count`、`quality_level`、`warnings` 及 provenance reference。

---

## 9. 時間序列及歷史資料規則

- 以 `statement_date` 作時間軸；`publication_date` 用於顯示資料何時刊登，不可代替結算日。
- 只畫資料庫實際有的結算日；目前第一期的比較日期設定是 2026-03-31、2026-06-30、2026-09-30，不能把這三點稱為完整長期歷史序列。
- 往後匯入新一期後，日期選擇器應動態更新，不應把三個日期永久寫死在 dashboard。
- 同一銀行同一期如有新 extraction，要由 adopted extraction 及 publication record 決定 current version，不可把不同 OCR runs 重複計入。
- 同一日期若有多份不同 statement type，必須分開顯示。
- 如某期未有有效資料，時間序列應斷線或顯示 gap；不作線性插值、不補 0。
- 任何 perimeter change、bank merger、entity alias correction 或 statement scope change，都要在相應日期留下 marker，避免把口徑變化誤當成業務增長。

---

## 10. 匯出與可追溯性

每次 CSV／Excel 匯出至少應包含：

- 指標 ID、名稱、定義版本及公式摘要
- entity ID、canonical bank name、entity type、reporting scope
- statement date、publication date、statement type
- currency、unit scale、decimal places、顯示單位
- 金額／ratio、分子及分母（ratio）
- perimeter version、coverage count、quality level
- statement ID、publication ID、statement row IDs、standard item IDs
- warnings（例如 partial coverage、unverified field、missing period）

匯出資料要保留「資料日期」與「發布日期」兩個概念；不要只輸出一個 date 欄。

---

## 11. 目前資料成熟度及啟用次序

根據 repository 的 `docs/V3_INTEGRATION_RECONCILIATION_2026-11-21.md` 當時記錄：

- 財務報表 statements：113 份。
- 已 adopted：36 份。
- 當時 adopted statements 的 current rows：2,260 行。
- 報告記錄的 publication records：0。
- 2,260 個 current rows 有 proposed candidate，但沒有 approved mapping。

以上是該 reconciliation 文件的 snapshot，不代表之後資料庫沒有變化。Dashboard 啟動時應直接讀取指定 V3 DB 重新計算最新狀態。

**含意**：設計可以先完成，但正式財務 KPI、跨銀行排名及行業加總不能因為 UI 已完成就自動啟用。必須先完成 publication workflow、approved standard-item mapping、核心 indicator rules 及 perimeter 審批。

---

## 12. 實施 Roadmap

### Phase 0 — PR #3 Candidate Explorer hardening

在 PR #3 merge / 作為第一期首頁之前，先處理以下圖表正確性事項：

1. 修正單一 exact candidate row 與另一個 ambiguous row 同時出現時，仍錯誤繪圖的問題。
2. 對跨機構及時間線比較加入完整 signature gate：`currency`、`unit_scale`、`amount_decimal_places`、`reporting_scope`、`row_type` 及借貸方向。
3. 圖表以 `amount_decimal_places` 顯示人類可讀的 MOP 金額；原始 minor 值仍可在 provenance 明細查看。不可將 `unit_scale` 與 decimal places 混為一談。
4. candidate 預覽容許顯示 `proposed` 只限清楚標示未核准；正式指標必須改用 approved mapping gate。
5. 顯示／處理 entity lifecycle，並檢查同一機構同一期其他未 adopted 的 balance-sheet statement，避免隱藏競爭報表。
6. row_type 必須在圖表 tooltip / 明細顯示；indicator engine 另按 catalogue 的 row rule 判斷是否可納入。
7. 只有至少兩個有效期間才稱為「趨勢」；一個點只稱為單期觀察。
8. 補上上述 boundary tests，然後執行 targeted tests 及完整 test suite。

完成後，PR #3 只代表可用的 Candidate Explorer 首頁，不代表已完成金融 KPI 或行業加總。

### Phase A — 定義口徑（先做）

1. 審批澳門銀行業統計 perimeter：哪些本地法人、哪些澳門分行、哪些關聯／集團報表要納入或排除。
2. 確認第一批核心科目：總資產、總負債、權益、客戶貸款、客戶存款、現金／央行結存、利息收入／支出、淨利潤。
3. 為每個指標定義 item keys、debit／credit side、parent-child、row type、subtotal／total 的處理方法。
4. 補上 income statement period basis（季度／YTD／年度及起訖日期）。
5. 確認每一類指標所需的 publication quality 及 field verification 門檻。

### Phase B — Analytics Engine

1. 實作 read-only V3 repository。
2. 實作 statement／row quality gate。
3. 實作 item rule 及 indicator catalogue loader。
4. 實作 entity-level metrics。
5. 實作 sector perimeter validation、去重及 coverage gate。
6. 實作 ratio-of-sums、平均餘額、流量年化及時間序列計算。
7. 建立 synthetic fixtures，測試缺報表、重複報表、不同 scope、parent-child double count、ambiguous mapping、unreadable value 等情況。

### Phase C — Dashboard MVP

1. 澳門銀行業總覽（在符合條件前顯示小計及未啟用原因）。
2. 銀行資料庫及單一銀行 Profile。
3. 跨銀行比較。
4. 時間序列及 QoQ／YoY。
5. Data Quality & Coverage。
6. Provenance drill-down 及 CSV export。

### Phase D — 進階金融分析

1. ROA、ROE、NIM 等需期間及平均餘額的指標。
2. 資產質素及減值分析（如能取得足夠資料）。
3. 監管資本及流動性指標（需獨立監管資料來源）。
4. Sector concentration、market share、HHI 等行業分析。

---

## 13. 驗收標準（Acceptance Criteria）

### PR #3 Candidate Explorer

- [ ] 一個 exact candidate row 旁若同時存在包含該 candidate 的 ambiguous row，該機構／期間必須顯示 ambiguous，不可取其中一行繼續畫圖。
- [ ] 跨行及跨期圖表必須核對 currency、unit_scale、amount_decimal_places、reporting_scope、row_type 及借貸方向。
- [ ] MOP 顯示值按 amount_decimal_places 格式化；原始 minor 另作追溯值，不得誤標為完整 MOP 金額。
- [ ] proposed / insert-direct candidate 必須明確標示為未核准候選；不得進正式 KPI／行業合計。
- [ ] merged / excluded / retired entity 不會靜默顯示成現行銀行。
- [ ] 同期出現另一份未 adopted balance-sheet statement 時會標示競爭報表／缺口，而不是無提示地使用 adopted 一份。
- [ ] 單一有效日期不會被標示為時間趨勢。
- [ ] 圖表上的每個點可追溯 row_type、mapping status、statement、來源頁及金額核實狀態。

### 資料正確性

- [ ] Dashboard 只讀取 V3 operational DB，拒絕舊 V5 DB。
- [ ] Dashboard 使用 `v_effective_statement_rows`，包括有效 insert，排除 active exclude，並套用 verified field values。
- [ ] 同一 statement 的不同 extraction 不會重複加總。
- [ ] Published view 只使用 current publication 對應的 adopted extraction。
- [ ] 未 approved mapping、merged／retired standard item、ambiguous mapping 不會靜默進入正式指標。
- [ ] debit／credit 及 decimal conversion 有測試覆蓋，沒有 float precision loss。

### 行業總量

- [ ] 行業 perimeter 有版本、生效日期、審批者及 source reference。
- [ ] 相關／集團報表不會同其所代表的銀行重複計入。
- [ ] 分行與本地法人按經審批口徑處理，不會因名稱相似或 parent-child 關係自動合併。
- [ ] 缺少任一必要機構時，畫面顯示小計及 coverage，不會標示完整行業總計。
- [ ] Sector ratios 使用 ratio-of-sums，而不是簡單平均各銀行百分比。

### 指標計算

- [ ] `user:總計` 不會被用作 Total Assets。
- [ ] AMCM deposits 不會被歸類為 customer deposits。
- [ ] Deposits parent 與 child rows 不會重複計算。
- [ ] Equity、share capital、working capital 分開處理。
- [ ] Balance sheet stock 與 income statement flow 分開處理。
- [ ] period basis 不明時不計算 quarterly-only profit、ROA、ROE 或 NIM。
- [ ] NPL、CAR、LCR、NSFR 等欠缺監管資料時不會估算或冒充正式指標。

### 介面及追溯

- [ ] 每個 KPI 顯示日期、單位、coverage 及 quality marker。
- [ ] 缺失資料、未採納、未發布、不可讀、ambiguous 與真實零值清楚區分。
- [ ] 每個 chart point 可追溯到 statement／row／PDF page。
- [ ] 匯出包含公式版本、perimeter version、單位及來源 IDs。
- [ ] Dashboard 不提供任何會修改 V3、Workbench 或 mapping 的操作。

---

## 14. Repository 參考文件

本設計基於以下現有專案文件及程式：

- `PROJECT_LAYOUT.md` — 確認 V3 為 operational DB，V5 為 frozen legacy。
- `DATABASE.md` — 舊 V5 schema 及 legacy 資料說明；只作歷史參考，不作 dashboard source。
- `database/v3/002_entities_statements.sql` — reporting entities 及 statements schema。
- `database/v3/005_statement_extractions_and_extracted_rows.sql` — extraction 與 immutable OCR evidence。
- `database/v3/006_statement_rows.sql` — adopted statement row snapshot。
- `database/v3/007_standard_catalogue_and_mapping.sql` — standard items 及 mappings。
- `database/v3/008_verification_publish_audit.sql` — field verification、publication、audit。
- `database/v3/009_constraints_triggers_indexes_views.sql` — V3 views、publication 及有效 rows 規則。
- `database/v3/012_statement_row_adjustments.sql` — row insert／exclude adjustments。
- `phase1_dashboard/README.md` — 現有本機唯讀 dashboard 的定位及限制。
- PR #3 — [Make the phase-1 landing page a chart-first view](https://github.com/mogcai/macau_tb_ocr_prototype/pull/3)，新增 `phase1_dashboard/charts.py`、圖表首頁、`/api/charts` 及 chart boundary tests。
- `phase1_dashboard/scope.py` — 現有 8 + 3 + 4 機構分組及未啟用合計原因。
- `phase1_dashboard/queries.py`、`phase1_dashboard/present.py` — 現有查詢、effective rows、mapping 及比較安全規則。
- `phase1_dashboard/charts.py`（PR #3 branch）— Candidate Explorer 的 chart-slot eligibility、bar / trend SVG 及 chart view model；本設計另列明在正式指標使用前需要補強的比較簽章。
- `docs/V3_INTEGRATION_RECONCILIATION_2026-11-21.md` — V3 當時採納、核實及發布成熟度 snapshot。
- `docs/PLATFORM_ROADMAP_BLUEPRINT.md` — Analytics Layer 及 Interactive Dashboard 的長期方向。

---

## 15. 最終建議

PR #3 的 chart-first 方向值得保留：它令現有 Phase-1 dashboard 有一個清晰的視覺入口，同時保留 `/coverage`、`/compare`、`/entities`、`/statements` 等追溯頁面。但 PR #3 目前仍是 Candidate Explorer，尚未達到正式金融分析 dashboard 的比較標準。

建議工作順序：

1. 先完成 Phase 0 的 chart correctness fixes，確保候選圖表不會因 ambiguous rows、不同 decimal places 或不同 reporting scope 而產生誤導。
2. 審批澳門銀行業 perimeter，分清本地法人、分行及關聯／集團報表，並確認完整應有機構名單。
3. 審批核心科目 mapping 及 indicator catalogue，包括 row type、parent-child、debit／credit 及流量期間定義。
4. 建立獨立 Analytics Engine、quality gate、aggregation engine 及 provenance。
5. 才逐步開放正式 Total Assets、Customer Deposits、Customer Loans、Loan-to-Deposit、ROA／ROE、NIM、market share 及 sector totals。

**PR #3 建議：暫時不要把它視為金融指標 Dashboard 已完成。** 至少應先修正本文件第 16 節列出的 P1 correctness issues，並補測後再 merge；其餘 P2 項目應納入同一個 hardening phase。即使 PR #3 merge，行業合計仍應保持 disabled，直至 perimeter 及 indicator rules 經正式審批。

---

## 16. PR #3 整合審視及待修正事項

### 16.1 PR 狀態及範圍（審視時點：2026-11-24）

- PR：[#3 Make the phase-1 landing page a chart-first view](https://github.com/mogcai/macau_tb_ocr_prototype/pull/3)
- 狀態：Open，未 merged；base `main`，head branch `cursor/phase1-chart-dashboard-3204`。
- Head commit：`94f9fd21fd7086d4e8701acdd9c8573e80b5aad0`。
- 改動：8 個檔案，約 +797 / -3；沒有改 `database/v3/`、V3 schema 或 Workbench 核心流程。
- 新增 `phase1_dashboard/charts.py`、首頁模板及 `/api/charts`；首頁改為 chart-first，覆蓋表搬到 `/coverage`。
- PR 說明自報 58 個 targeted tests 通過（33 dashboard、22 V3 constraint、3 version-sync），但沒有執行完整 `python -m unittest discover -s tests`。審視時沒有取得附屬 workflow runs；PR screenshots 明確使用 synthetic fixture，不是實際操作資料庫。
- PR 沒有已提交 review、inline thread 或 top-level review comment。

PR 的主要優點：保留 read-only V3、loopback Host allow-list、既有三組機構 perimeter、明確顯示缺口而非補 0、不做 sum／ranking／share，並為 chart slot 加了 boundary tests。這些原則應吸收進正式設計。

### 16.2 P1 — 合併前應修正的正確性問題

**P1-1：一個 exact row 可能掩蓋同一候選的 ambiguous row**

`chart_slot()` 先找 `candidate_keys(row) == [item_key]`。當只有一個 exact row 時便直接畫圖；後面的 ambiguous-row 檢查不會執行。若同一份 balance sheet 另有一行同時包含該 candidate 及另一個 candidate，圖表仍可能採用 exact row，忽略另一行造成的衝突。

修正：先找出所有 `item_key in candidate_keys(row)` 的行；只要其中任何一行有多個不同候選，整個 entity-period 必須標示 `ambiguous`。只有在沒有 ambiguous row 且剛好一行 exact candidate 時才可畫圖。新增「exact + ambiguous 同時存在」測試。

**P1-2：跨行／跨期比較 signature 不完整**

現有 `chart_slot()` 逐點檢查 `currency = MOP` 及 `unit_scale = 1`；`_cross_bank()` 及 `_trend()` 主要只檢查借／貸方向一致。它們沒有驗證不同點的 `amount_decimal_places`、`reporting_scope` 及 `row_type` 一致。結果可能把不同小數尺度、不同報表範圍，或 item / subtotal / total 不同類型放在同一圖表。

修正：每個 point 輸出完整 comparison signature；同一 bar chart / time series 的 signature 不一致時，整組比較不可畫，並逐點列出原因。最低要核對 `currency`、`unit_scale`、`amount_decimal_places`、`reporting_scope`、`statement_type`、指標要求的 `row_type` 及 normal-balance side。

**P1-3：圖表使用 raw minor 整數作視覺數值**

`present.format_minor()` 已可按 `amount_decimal_places` 產生 `reading`，但 `charts.py` 的 point 仍以 `minor` 作 SVG 長度及標籤；首頁亦顯示 `minor`。圖表標題雖有說明「儲存 minor」，但一般使用者容易把它理解成 MOP 金額，而且不同 decimal places 的 raw integers 不可直接比較。

修正：圖表顯示值應由 `minor / 10 ** amount_decimal_places` 得出 MOP reading，並保留 raw minor 供 provenance。若 decimal places 未記錄或跨點不一致，禁止比較。`unit_scale` 是報表單位倍率，`amount_decimal_places` 是儲存小數尺度，兩者必須分開處理；不可因專案規則 MOP、unit_scale=1 就跳過 decimal places。

### 16.3 P2 — Candidate Explorer hardening 應補足

**P2-1：proposed mapping 仍可畫圖**

`catalogue_current` 檢查的是 standard item 是否現行有效，不代表 row mapping 已 approved。現有圖表可包含 `proposed`，甚至人工 `insert-direct` candidate。作為內部 Candidate Explorer 可以接受，但必須明確標示「未核准候選」；正式指標必須要求 `mapping_status = approved`。

**P2-2：row_type 有傳入 point，但沒有作 eligibility 或視覺註記**

`chart_slot()` 未限制／比較 `row_type`，圖表只在 bar label 顯示 mapping status。應在候選圖表 tooltip / gap detail 顯示 row_type；正式 indicator 由 indicator definition 指定允許的 `item`、`subtotal` 或 `total` 規則，避免把不同層次或父子項混作相同口徑。

**P2-3：entity lifecycle 沒有傳入 chart slot**

`build_coverage()` 會描述 `merged`、`excluded`、`retired` lifecycle，但 `build_chart_view()` 建立 bars 時主要只帶 entity id/name。這些實體有可能被畫成普通現行機構。應把 `registry_status`、`merged_into_entity_id` 傳入 chart model，排除或明確分組顯示，不可靜默當成現行銀行。

**P2-4：同期未 adopted 的競爭報表可能被忽略**

`_period_cell()` 會把未採納報表放在 `not_adopted`；`chart_slot()` 沒有檢查該欄位。若同一 entity/date 有一份 adopted balance sheet，另有一份同類型但未 adopted 的報表，首頁可能只畫 adopted 數值而不提醒。應把 `not_adopted` 的 statement type / reporting scope 資料帶入 chart slot；同一口徑存在 competing balance-sheet statement 時顯示缺口或重大警告。單純未 adopted 的 income statement 不應無故阻止 balance-sheet candidate chart。

**P2-5：單一點不應稱為趨勢**

`_trend()` 以至少一個點作為 `drawn = True`，因此只有一季資料亦會顯示為 trend。建議少於兩個有效日期時顯示「單期觀察」，只有兩個或以上日期才稱為趨勢；若只有一點，可保留 KPI／marker，但不可暗示變化方向。

**P2-6：完整測試尚未完成**

PR description 表示未執行完整 test discovery。合併前應執行完整測試集，並補測 P1/P2 邊界個案；screenshots 以 synthetic fixture 測試是合理做法，但不應當作真實銀行數據驗證。

### 16.4 PR #3 可以及不可以交付的功能

| 能力 | PR #3 | 正式 Dashboard 設計目標 |
|---|---|---|
| chart-first 首頁 | 有 | 保留及擴充 |
| candidate item 跨機構觀察 | 有，限第一批機構及三個結算日 | 升級為 approved indicator comparison |
| 單一銀行三期觀察 | 有 | 擴展為所有實際可用日期的 time series |
| Total Assets / Customer Deposits 等正式指標 | 沒有 | 由 indicator catalogue + quality gate 計算 |
| 行業總量 | 沒有；明確不 sum | perimeter 完整及無重疊後才開放 |
| ratio / ROA / ROE / NIM | 沒有 | 須有正確分子、分母及期間基礎 |
| market share / ranking | 沒有；明確不做 | 只在正式指標及完整 coverage 下開放 |
| provenance drill-down | 保留既有頁面入口 | 每個 point 必須下鑽到 statement、row、PDF page |

結論：**PR #3 是合理的 Candidate Explorer UI foundation，但不是整個 Banking Dashboard 的 Analytics implementation。** 先修正 P1，將 P2 加入 hardening，再按本文件 Phase A–D 建立正式指標和行業合計。
