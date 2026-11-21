# Workbench v2 — Code Review Report

- **Repository:** `mogcai/macau_tb_ocr_prototype`
- **Reviewed scope:** `workbench/`、相關 v3 schema、Workbench tests 及既有 review notes
- **Review date:** 2026-11-20
- **Branch:** `main`
- **Review type:** Source-code review（未修改 repository）
- **Test execution:** 本次沒有在 repository runtime 執行完整 test suite；只檢查現有測試程式碼及其覆蓋範圍

---

## 1. Executive Summary

今次已成功讀取 GitHub 最新 `main` 分支的 Workbench 原始碼。上次提出的兩個核心問題已見到針對性修正：

1. `verify_row()` 不再修改 `statement_reviews`，並由 `_check_editable()` 阻止 reviewed / adopted statement 一般寫入。
2. Review rows、Review Queue 及 Statement rows 已共用 effective values，避免只在 Review 畫面顯示修正值、其他畫面仍讀取 base value。

但現版仍有以下需要處理的問題：

| ID | Severity | Finding | 狀態 |
|---|---|---|---|
| WB2-01 | **P1** | Review / Queue / Detail 未有一致鎖定 extraction version | 已確認 query 缺少版本限定；實際影響取決於同一 statement 是否存在多個 extraction |
| WB2-02 | **P1** | `amount` 狀態但輸入空白會被當成 `0.00` 保存 | 已由程式碼確認 |
| WB2-03 | **P1** | Mapping API 容許選用 draft / merged / retired standard item | 已由程式碼確認 |
| WB2-04 | **P2** | 一行 Save 或其他 reload action 會丟失同頁其他未保存修改 | 已由前端程式碼確認 |
| WB2-05 | **P2 / 設計風險** | 儲存一行會把五個欄位全部標記為 verified | 已由程式碼確認；需確認是否符合「row-level verification」業務定義 |
| WB2-06 | **P2** | 金額 parser 不支援葡文常見 decimal-comma 格式，並硬編碼兩位小數 | 已由程式碼確認 |
| SEC-01 | **部署阻斷** | Role / Actor 由用戶端自行指定，不能當作真正權限或身份驗證 | README 已聲明這是 local scaffold；只在本機可信環境使用才可接受 |

**建議：先處理 WB2-01、WB2-02、WB2-03。** 這三項直接關係到「審核的是哪一版資料」、「金額有沒有被錯誤地變成零」及「科目 mapping 是否有效」。

---

## 2. Findings

### WB2-01 — Review、Queue、Detail 和 Adopt 未鎖定同一個 Extraction Version

- **Severity:** P1
- **Status:** Source-level issue confirmed；多 extraction 時的實際資料影響需要在 operational DB 驗證
- **Files:**
  - `workbench/service.py` — `statement_detail()`：約 127–146 行
  - `workbench/service.py` — `adopt_statement()`：約 254–286 行
  - `workbench/service.py` — `EFFECTIVE_ROW_SQL`：約 358–426 行
  - `workbench/service.py` — `review_queue()` / `review_rows()`：約 540–690 行
  - `database/v3/005_statement_extractions_and_extracted_rows.sql` — `statement_extractions` 可為同一 statement 保存多個 run / extraction

**問題：**

`EFFECTIVE_ROW_SQL` 讀取 `statement_rows sr` 時，沒有以 `statement_extraction_id` 限定某一個 extraction；OCR row 部分只排除 active exclude adjustment。`review_rows()` 和 `review_queue()` 都直接使用這個 SQL，因此同一 `statement_id` 下如存在多個 extraction 的 rows，review metrics、mapping counts、totals 可能把不同版本的 rows 一併計算。

另一方面，`adopt_statement()` 先用 `review_queue()` 的整體結果判斷是否 ready，之後才以 `ORDER BY created_at DESC LIMIT 1` 選擇一個 extraction。這兩步沒有保證 review 所檢查的 rows 就是最後要 adopt 的 extraction。

另外，`statement_detail()` 將 `statement_extractions` 直接 LEFT JOIN 到 statements，再以 `fetchone()` 取一筆，沒有指定排序或 extraction selector；同一 statement 有多個 extraction 時，詳情頁顯示的 extraction ID / status 可能不是 current 或 latest extraction。

**可能後果：**

- Review Queue 的 row count / balance / mapping coverage 混合了舊版與新版。
- Reviewer 以為已審核最新 OCR 結果，但 Adopt 實際採用另一份 extraction。
- 新增一個 failed / proposed extraction 後，因為 Adopt 只看最新 created extraction，即使較早已有 completed extraction，也可能因最新一份不是 completed 而拒絕 adoption。
- Detail 頁顯示的 extraction metadata 不穩定。

**建議修正：**

1. 明確定義 statement 的「工作中 extraction」選擇規則，例如 `current_review_extraction_id`，或明確選定最新 completed extraction。
2. `review_rows()`、`review_queue()`、`statement_detail()`、`adopt_statement()` 全部使用同一個 extraction ID。
3. Mark Reviewed 時將被審核的 `statement_extraction_id` 一併記錄；Adopt 時檢查該 ID 仍然是 ready 且 completed，不可重新隨意選另一份。
4. 新 OCR extraction 建立後，應有明確流程令舊 review sign-off 失效或保留舊版本的審核狀態，不可靜默沿用。
5. `statement_detail()` 不應對多筆 extraction 使用沒有排序的 `fetchone()`。

**Acceptance tests：**

- 同一 statement 建立 extraction A、B，兩者 row count / amounts 不同；Review B 時不得計入 A 的 rows。
- Mark Reviewed 後再新增 extraction C；Adopt 不可靜默採用 C，除非 C 就是已審核版本。
- 最新 extraction 為 failed、較舊 extraction 為 completed 時，系統須按明確政策處理，而不是由 SQL 排序偶然決定。
- Detail API 要穩定回傳明確選定的 extraction ID。

---

### WB2-02 — Amount 欄位留空會被當成 0.00

- **Severity:** P1
- **Status:** Confirmed from source code
- **File:** `workbench/service.py`，約 340–355、693–710、770–799 行

**問題：**

`_parse_money()` 對 `None` 或空字串直接回傳 `0`。當 UI 將 debit / credit state 設為 `amount`，但輸入框留空，後端不會拒絕，而會把該欄位保存成 0 minor units，即 `0.00`。

例如：

- `debit_state = "amount"`
- `debit = ""`

現行結果：`debit_minor = 0`，並被保存為 verified amount。

此外，當 state 是 `blank` / `dash` / `unreadable` 等非 amount 狀態時，OCR row 的 `verify_row()` 仍將 `debit_minor` / `credit_minor` 設為整數 `0`，然後寫入 `field_verifications.verified_value_text`。Effective query 會 CAST 該欄位為 INTEGER，因此可能出現 state 為 `blank`，但 effective minor value 卻是 `0` 的語意不一致。Manual row 路徑則會將非 amount 的 minor value 存成 `NULL`，兩條路徑亦不一致。

**可能後果：**

- 使用者不小心清空金額輸入框，按 Save 後真實金額變成 0.00。
- 同一個 blank / dash 狀態，在 OCR row 和 manual row 有不同的儲存表示。
- 下游若只讀取 minor value 而忽略 value state，可能把 blank / dash 誤當成零金額。

**建議修正：**

1. `state == "amount"` 時，金額必須非空並且可解析；空白應回傳 422 / 400 validation error，不能當成零。
2. `state != "amount"` 時，minor value 一律保存 `NULL`，而不是 `0`。
3. 對 OCR row、manual row 使用相同的 amount/state validation helper。
4. UI 對 amount 狀態而沒有金額值時，顯示 inline validation error，不要只彈一般錯誤視窗。
5. 另外補測試確認真正的 `0.00` 必須由使用者明確輸入 `0` 或 `0.00` 才成立。

**Acceptance tests：**

- amount + empty string → rejected，資料庫沒有變更。
- amount + `0.00` → 保存 0，state 為 amount。
- blank / dash + empty input → minor value 為 NULL，state 保持對應狀態。
- OCR row / manual row 的相同輸入，儲存結果一致。

---

### WB2-03 — Standard Item Mapping 容許 Draft / Merged / Retired Items

- **Severity:** P1
- **Status:** Confirmed from source code
- **Files:**
  - `workbench/service.py` — `standard_items()`：約 429–454 行
  - `workbench/service.py` — `approve_mapping()`：約 891–912 行
  - `database/v3/007_standard_catalogue_and_mapping.sql` — `standard_items.item_status` 定義

**問題：**

`standard_items()` 目前查詢整張 `standard_items` table，沒有按 `item_status` 過濾。Schema 明確容許 `draft`、`approved`、`merged`、`retired` 四種狀態。

前端用 `standard_items()` 建立 mapping dropdown，因此所有狀態都有機會出現在選項。更重要的是，`approve_mapping()` 只檢查 standard item ID 是否存在，沒有檢查 item status。因此即使用 API 直接提交，draft / merged / retired item 亦可能獲批為 mapping。

Review readiness 只檢查是否存在 proposed / approved mapping，沒有檢查 mapped standard item 是否仍然有效。結果是無效 catalogue item 有機會通過 mapping gate，甚至隨 statement adoption 一併進入 current dataset。

**建議修正：**

1. Mapping dropdown 預設只顯示 `item_status = 'approved'` 的項目。
2. `approve_mapping()` 必須在後端再次檢查 item status；不能只靠 UI filter。
3. `merged` item 應拒絕直接 mapping，並提示改用 `merged_into_standard_item_id` 指向的項目。
4. `retired` / `draft` item 應拒絕新 mapping；如有歷史 mapping，應保留歷史但明確標示。
5. `mapping_ready` / adoption readiness 應檢查目前 effective mapping 指向的 item 仍然有效。

**Acceptance tests：**

- approved item 可正常 mapping。
- draft / retired / merged item 由 UI 不會出現，API 直接提交亦回應 409 或 422。
- merged item 應回傳 target item 提示。
- 某 item 由 approved 改為 retired 後，相關 statement 不應靜默保持 ready-for-adoption。

---

### WB2-04 — Save / Map / Delete / Insert 後 Reload 會丟失其他未保存修改

- **Severity:** P2
- **Status:** Confirmed from frontend flow
- **File:** `workbench/templates/review_detail.html`，約 248–284、307–419 行

**問題：**

前端用單一 `dirty` boolean 追蹤所有 `.cell` 欄位。每一個 row Save 成功都直接 `window.location.reload()`。Map、Delete / Exclude、Restore、Insert 成功亦會 reload。

因此當使用者修改多行但只 Save 其中一行，該次 Save 成功後整頁 reload，其他行未保存的修改會消失。若使用者先修改某行，再進行 mapping / delete / restore / insert，該操作成功後同樣會 reload，令之前修改的內容消失。

現有 dirty guard 只在按 `Mark reviewed` 時檢查，而且只監察 `.cell`，沒有在其他會 reload 的操作前作統一檢查。

**建議修正：**

1. 改用 per-row dirty tracking，例如 `dirtyRows = Set(statement_row_id)`。
2. 每次只 Save 一行後，只更新該行 server response，不要整頁 reload；或提供明確的 Save All。
3. Map / Delete / Restore / Insert 等會令資料重新載入的操作前，如存在其他 dirty rows，必須阻止操作或要求先保存 / 放棄修改。
4. 離開頁面、切換 statement 或切換 filter 時亦應提示未保存修改。
5. Save 成功後只清除已成功保存的 row dirty state，不可清除其他 rows 的 dirty state。

**Acceptance tests：**

- 同時修改兩行，只 Save 第一行；第二行不得靜默消失。
- 修改一行後直接 Map 另一行；系統必須提示先處理 dirty row，或保留其修改。
- 修改一行後 Delete / Restore / Insert；不得丟失未保存內容。
- 伺服器 Save 失敗時，輸入內容及 dirty state 必須保留。

---

### WB2-05 — Row Save 會將五個欄位全部記錄為 Verified

- **Severity:** P2（資料 provenance / UX 設計風險）
- **Status:** Confirmed from source code；是否違反預期取決於 Save 是否代表整行已核實
- **Files:**
  - `workbench/templates/review_detail.html`，約 258–264 行
  - `workbench/service.py`，約 770–799 行
  - `tests/workbench_scaffold/test_workbench.py`，`test_verify_writes_field_verification_and_audit`

**問題：**

UI 的 Save payload 每次都送出 label、debit、credit、debit_state、credit_state 五個欄位。後端 `verify_row()` 亦固定建立五筆 `field_verifications`，而且全部標記為 `verification_status='verified'`。

即使使用者實際只修正 label，debit / credit 兩個金額欄位亦會被記錄為已核實。現有測試亦明確要求一次 row save 產生五筆 field verifications。

若產品定義係「Save 一行即代表 reviewer 已逐欄檢查整行」，這個設計可以成立；但目前按鈕文字只是 Save，而且沒有明確提示「五個欄位都會被標記為已核實」。

**風險：**

日後檢查 verification provenance 時，無法分辨使用者真正檢查過哪些欄位；修正 label 也可能令未曾核對的金額被當成 verified。

**建議修正（二選一）：**

- **方案 A — Field-level verification（較推薦）：** UI 傳送實際修改 / 明確勾選核實的欄位，後端只更新指定欄位；未修改欄位不增加 verification record。
- **方案 B — Row-level verification：** 保留五欄一起核實，但將按鈕改為「Save & verify entire row」，並在第一次使用時清楚說明所有顯示欄位會被標記為 verified。

不可只靠欄位值有沒有改變判斷是否已核實，因為使用者亦可能檢查後確認原值正確；如採 field-level，應有明確的 per-field verify action / checkbox。

---

### WB2-06 — Money Parser 不支援葡文 Decimal-comma 格式，並硬編碼兩位小數

- **Severity:** P2
- **Status:** Confirmed from source code
- **File:** `workbench/service.py`，約 331–355 行

**問題：**

`_parse_money()` 一律移除逗號，再以 Decimal 解析，並固定乘以 100、只接受兩位小數。

因此葡文 / 歐陸常見格式，例如 `1.234,56`，會被處理成 `1.23456`，繼而因小數位超過兩位而拒絕。`1.234` 若本意是 1,234，亦會被當成 1.234 而拒絕。

另外 `_money()` 雖然用 `amount_decimal_places` 計算 minor unit 的 scale，但最後固定使用 `,.2f` 輸出；`_parse_money()` 則完全沒有讀取 statement 的 `amount_decimal_places`。所以 schema 若支援非兩位小數 statement，顯示與輸入都會出錯。

**建議修正：**

1. 不要用「移除所有逗號」處理不同 locale 的數字。
2. UI 應明確規定輸入格式，或按語言 / 使用者 locale 使用明確 parser；不應猜測 `1,234` 和 `1,234` 的含義。
3. parser 接收 statement 的 `amount_decimal_places`，並按該設定驗證精度及轉 minor units。
4. formatter 使用對應的 decimal places，而非固定 `.2f`。
5. 若本 project 已決定所有 Balancete 金額一律兩位小數及統一使用 dot decimal，應在 UI 明示只接受 `1234.56`，並提供例子；不要讓葡文使用者誤以為可輸入 `1.234,56`。

**Acceptance tests：**

- 依照正式輸入規格測試 `1234.56`、`1,234.56`、`1.234,56`、負數、零及空字串。
- 若只支援 dot decimal，逗號格式應明確拒絕並顯示預期格式，而非轉成另一個值。
- `amount_decimal_places` 不同的測試 statement，其 parser / formatter 必須保持一致。

---

## 3. Security / Deployment Blocker

### SEC-01 — Role 與 Actor 由 Client 自行指定，不是真正 Authentication

- **Severity:** 對任何非本機可信環境屬部署阻斷
- **Status:** Confirmed；而且 `workbench/README.md` 已明確聲明這是 local scaffold，不是 production authentication
- **File:** `workbench/main.py`，約 64–76、93–100 行

**問題：**

- API 可以由 `X-Workbench-Role` header 指定角色。
- `/set-role/{role}` 任何人都可呼叫，包括 `/set-role/admin`，並設定 admin cookie。
- `X-Workbench-Actor` 由 request header 提供，audit actor 可由呼叫者自行偽造。

所以這套 capability check 只適合本機開發測試，不能防止使用者自行提升權限，也不能作為可信 audit identity。

**建議：**

在 Workbench 開放給 LAN、雲端或其他使用者之前，必須加入真正登入、server-side session、角色由 server-side account / permission mapping 決定，以及 actor 由登入身份產生。移除 production API 對任意 `X-Workbench-Role` / `X-Workbench-Actor` 的信任。若暫時只供本機使用，應確保服務只 bind `127.0.0.1`，不要對外開放。

---

## 4. 設計上合理、暫時不列為 Bug 的地方

1. **Reviewed 與 Adopted 分開：** `reviewed` statement 仍然 held，需 adopter 另外 adopt。這個分層合理，README 亦有清楚交代。
2. **Raw OCR 不直接覆寫：** 人工更正使用 `field_verifications`；OCR over-read / under-read 使用 `statement_row_adjustments`，方向正確。
3. **Reviewed / Adopted 後由後端鎖定：** `_check_editable()` 除 UI disable 外，亦在寫入 API 的 service 層拒絕修改，這點正確。
4. **Publication 尚未實作：** publish endpoint 回傳 501，避免在 quality gate 未定義前誤發佈，這是合理的 fail-closed 設計。
5. **Proposed mapping 可通過 reviewer mapping gate：** README 明確表示單一 proposed mapping 被視為足以通過 review mapping gate；這是既定業務選擇。不過仍應確認只有有效的 approved catalogue item 可以被採納。

另有一點應在 UI 清楚表達：目前 Mark Reviewed 主要要求 mapping ready；statement 即使不平衡或有 row issues，仍可能被標記 reviewed，但仍會留在 held / 不可 adopt。若這是刻意的 reviewer sign-off 定義，建議將按鈕附近的說明改成「Mark review completed — outstanding blockers may still keep this statement held」，避免用戶將 reviewed 理解成 adoption-ready。

---

## 5. Test Coverage Gap

現有 `tests/workbench_scaffold/test_workbench.py` 已覆蓋多個重要流程，包括：

- Role capability / snapshot read-only guard。
- Save 後有效欄位可重新讀取。
- Reviewed / adopted 後禁止編輯。
- Mapping conflict、unmatched、duplicate item。
- Manual insert、exclude、restore。
- Return to review。

但目前未見針對以下情境的專項 regression tests：

| Test ID | Scenario | Expected |
|---|---|---|
| WB2-T01 | 同一 statement 有兩份或以上 extraction | Queue / Detail / Adopt 全部鎖定同一 extraction |
| WB2-T02 | 最新 extraction failed，但舊版 completed | 按明確 extraction policy 決定，不可隨機選取 |
| WB2-T03 | amount state + empty input | 拒絕保存，不可自動轉 0 |
| WB2-T04 | blank / dash state | effective minor value 按資料模型保存為 NULL |
| WB2-T05 | draft / merged / retired standard item mapping | UI 隱藏、API 拒絕 |
| WB2-T06 | 同時修改兩行，只 Save 一行 | 另一行修改保留或要求先保存 |
| WB2-T07 | Dirty row 後執行 Map / Exclude / Restore / Insert | 不可靜默丟失 dirty changes |
| WB2-T08 | 只修改 label 後 Save | 金額欄位 verification provenance 符合既定設計 |
| WB2-T09 | 葡文 decimal-comma 金額 | 按明確 locale / input contract 正確處理或明確拒絕 |
| WB2-T10 | 非兩位 decimal places statement | parser / formatter 使用 statement 設定 |

`docs/WORKBENCH_REVIEW_RESPONSE.md` 記錄之前曾有 `220 tests OK`；這是 repository 文件內既有結果。本次 review 沒有重新執行該 220 tests，因此不能將之當成本次測試通過證明。

---

## 6. Recommended Fix Order

### P1 — 必須先處理

1. **WB2-01 Extraction version binding**：確保 reviewer 審核的版本就是最後 adopt 的版本。
2. **WB2-02 Empty amount validation**：禁止 amount 空白被當作 0，並統一 NULL / state 語意。
3. **WB2-03 Standard item lifecycle validation**：只有有效 catalogue item 才可作新 mapping / adoption。

### P2 — 緊接處理

4. **WB2-04 Dirty state / reload data loss**：改為 per-row dirty state，避免其他未保存修改消失。
5. **WB2-05 Verification provenance**：決定 Save 是 field-level 還是 row-level verification，然後令 UI、API、audit 語意一致。
6. **WB2-06 Locale / decimal scale**：明確定義輸入格式，使用 statement precision。

### Before external deployment

7. **SEC-01 Authentication / actor identity**：未完成前只限本機可信環境，不可當作多人或公開服務。

---

## 7. Overall Conclusion

現版 Workbench 比上一輪成熟：review state 與 data save 已分離、raw OCR evidence 保留、manual row adjustments 有 audit trail、reviewed / adopted write lock 亦有後端保護。

但目前仍未適合將整個 Review → Adopt 流程視為完全可靠。最重要的風險是 extraction version 未被明確固定；其次是金額空白被當成零，以及 mapping 可引用無效的 standard item。這些都可能影響最終銀行財務資料，而不只是介面體驗。

**建議先完成 P1 三項，再做 P2 和回歸測試；在 extraction version binding、amount validation、catalogue lifecycle gate 完成前，不建議把 Workbench 審核結果當作可無條件採納的最終資料。**

