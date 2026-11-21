# Workbench Code Review Report

- **Project:** `mogcai/macau_tb_ocr_prototype`
- **Scope requested:** `workbench/`
- **Review date:** 2026-11-19
- **Review status:** Preliminary / source-code access blocked

> **重要範圍聲明**
> 本次執行環境未能成功讀取 GitHub repository（GitHub 網頁及 API 請求均未能取得內容，亦未能 clone repository）。因此本文**沒有聲稱已完成逐檔 source-code review**，不會虛構檔名、行號、function 名稱或測試結果。
> 「已觀察問題」來自使用者實際操作回報；「待驗證風險」是根據該操作流程及 Workbench 常見資料流提出的檢查項目，必須對照 source code 和實際資料庫確認後，才可升級為 confirmed bug。

---

## 1. Executive Summary

目前已知兩項需要優先處理的問題：

1. **Mark Reviewed 之後再次 Save，record status 會由 `reviewed` 退回 `open`。** 這是已觀察到的狀態管理問題，屬於 **P1**。
2. **修改及 Mark Reviewed 之後，列表／記錄畫面仍顯示修改前的內容。** 這是已觀察到的資料呈現或持久化一致性問題，屬於 **P1**；但單憑畫面未能判斷資料庫是否也保存錯誤，必須做 reload 後核實。

在未讀到程式碼之前，不能判定兩項問題的根因。最需要追查的是 Save payload 是否重設 status、Review action 是否使用舊 record snapshot，以及 editor、列表和資料庫是否使用不同版本的 record。

**核心設計建議：** `Save` 只負責保存欄位資料；`Mark Reviewed` 才負責改變 review status。Reviewed record 預設必須在前端和後端均為 read-only。列表、詳情和編輯器必須以同一份已保存的 authoritative record 為準。

---

## 2. 已觀察問題（Confirmed from user report）

### WB-001 — Save 將 `reviewed` 狀態重設為 `open`

- **Severity:** P1 — Data integrity / workflow state
- **Status:** Confirmed behaviour; root cause unverified
- **Observed sequence:**
  1. 開啟一筆 record。
  2. 按 `Mark Reviewed`，狀態變成 `reviewed`。
  3. 再按 `Save`。
  4. record 自動變回 `open`。
- **Expected:**
  - `Save` 不得自行改變 review status。
  - `Save` 只保存可編輯欄位。
  - 一旦 record 是 `reviewed`，一般使用者不得再修改或保存其內容。
  - 前端禁用按鈕並不足夠；後端更新入口亦必須拒絕修改已 reviewed 的 record。
- **Likely causes to inspect (not confirmed):**
  - Save handler 或 payload 將 `status` 寫死為 `open`。
  - 更新時以新建 record 的 default status 覆蓋現有 status。
  - 前端送出的物件不包含 status，而後端 update/schema 將缺省 status 當成 `open`。
  - Save 完成後的 refresh/default-state 初始化，把狀態重新設為 `open`。
- **Required fix:**
  - 將一般欄位更新與 review state transition 分開。
  - 一般 update 不應接受或覆蓋 `status`；應由後端保留資料庫原有狀態。
  - 後端在寫入前檢查目前狀態；若已 `reviewed`，拒絕一般修改並回傳明確錯誤（例如 HTTP 409 或專案既有的等價錯誤格式）。
  - 只有專用的 review transition 才可把 `open` 轉為 `reviewed`。
- **Acceptance criteria:**
  - Save 一筆 `open` record 後，狀態仍為 `open`。
  - Mark Reviewed 後，狀態為 `reviewed`。
  - 對同一筆 `reviewed` record 再發送 Save/API update，後端拒絕；資料及狀態均不變。
  - Refresh、重新開頁、切換 record 後，狀態仍為 `reviewed`。

### WB-002 — Review 後畫面顯示舊內容

- **Severity:** P1 — Data consistency / misleading review result
- **Status:** Confirmed UI symptom; persistence status unknown
- **Observed behaviour:** 修改資料並 Mark Reviewed 後，該 record 仍然顯示修改前的資料。
- **Important distinction:** 必須先分清楚是「只在 UI 顯示舊資料」還是「資料庫實際保存了舊資料」。兩者根因和修正位置不同。
- **Diagnostic procedure:**
  1. 選一筆測試 record，記錄一個容易辨認的欄位原值。
  2. 修改該欄位並 Save。
  3. 不要只看目前 editor；重新載入該 record，核實新值是否仍存在。
  4. 再 Mark Reviewed，重新載入頁面及列表。
  5. 比較 editor、detail view、list row 及實際 persisted record 的同一欄位。
- **Interpretation:**
  - **Reload 後資料正確、畫面即時顯示舊值：** 優先查 frontend state/cache、component props、list refresh 或 stale object reference。
  - **Save 後 reload 已是舊值：** 優先查 Save payload、資料轉換／欄位 mapping、資料庫 update、record identity。
  - **Save 後 reload 正確，但 Mark Reviewed 後變舊：** 優先查 Review handler 是否用舊 snapshot 覆寫整筆 record，或 review 操作是否觸發舊資料回寫。
  - **只有列表舊、詳情正確：** 優先查 list query/cache/invalidation；不可把 list 的舊資料誤當資料庫未保存。
- **Likely causes to inspect (not confirmed):**
  - Mark Reviewed action 使用載入時的 original record，而不是 Save 後的最新 record。
  - Save 更新資料庫但沒有更新前端 canonical state；列表仍持有舊 object。
  - editor、list、detail view 使用不同資料源或不同欄位 mapping。
  - UI 使用 optimistic update，但 server response 未回寫到 state；或 request 完成後又被舊 fetch response 覆蓋。
  - review 操作只更新 status，卻用不完整／過期 payload 覆寫其他欄位。
- **Required fix:**
  - Save 必須以 server 回傳的已保存 record 更新 client canonical state。
  - Review 必須基於最新已保存版本，不能用 editor 開啟時的舊 snapshot。
  - Review 成功後，detail view 和 list view 必須更新／重新取得同一個 authoritative record。
  - 對非同步請求使用 request/version guard，避免較舊的 response 覆蓋較新的 state。
- **Acceptance criteria:**
  - 修改欄位後 Save，立即顯示新值。
  - Mark Reviewed 後，editor/detail/list 均顯示相同新值。
  - Browser refresh / 離開再返回後，新值仍存在。
  - Review action 不得令任何非 status 欄位回退到舊值。

---

## 3. 待驗證的高風險項目（Not yet confirmed bugs）

以下不是已確認缺陷，而是應在 `workbench/` source code 中逐項追查的風險。只有找到對應程式碼路徑或重現測試後，才應標記為 bug。

| ID | Priority | 檢查項目 | 需要驗證的失敗情況 |
|---|---|---|---|
| WB-R01 | P1 | Reviewed read-only 是否只在 UI 實施 | 直接呼叫 API、快捷鍵、其他頁面或批次操作仍可修改 reviewed record |
| WB-R02 | P1 | Save / Review 是否存在舊 snapshot | Review 用舊 object 覆蓋剛保存的新欄位 |
| WB-R03 | P1 | Save 是否有完整錯誤處理 | 保存失敗但 UI 顯示成功、清除 dirty flag 或允許 Mark Reviewed |
| WB-R04 | P1 | Mark Reviewed 是否要求先保存 | 有未保存修改仍可 review，最後 reviewed 的內容不是畫面所見內容 |
| WB-R05 | P1 | record identity 是否穩定 | 使用列表 index、排序位置或非唯一檔名更新錯 record |
| WB-R06 | P1 | 多入口更新是否共用 lock rule | 單筆 Save 被鎖，但 inline edit、批次 edit、import 或其他 handler 可修改 reviewed record |
| WB-R07 | P2 | 異步請求次序／重複提交 | 快速連按 Save/Review，較舊 response 覆蓋較新資料或產生重複操作 |
| WB-R08 | P2 | 切換 record 前的 dirty-state 處理 | 未保存修改在切換 record 時靜默丟失，或錯誤套用到下一筆 |
| WB-R09 | P2 | OCR rerun / re-import 對人工修正的影響 | 重新 OCR 或重新載入來源資料覆蓋人工修正／reviewed 資料 |
| WB-R10 | P2 | list/filter/count 是否即時一致 | record 已 reviewed，但仍留在 Open filter 或 status counts 未更新 |
| WB-R11 | P2 | Export 是否讀取正確版本 | 匯出時使用原始 OCR／舊 cache，而非最新已採納資料 |
| WB-R12 | P2 | 重要欄位的 null / empty / zero handling | `0`、空字串、`null` 被混為一談，保存時意外清除欄位或數值 |
| WB-R13 | P2 | 數值欄位正規化及顯示 | UI 格式化數值後再保存，造成精度、正負號、小數點或千分位轉換錯誤 |
| WB-R14 | P2 | Record 版本／並行修改控制 | 兩個分頁或兩次載入以舊版本保存，靜默覆蓋新修改 |
| WB-R15 | P3 | Empty/loading/error state | 載入失敗時顯示空白／舊 record，令使用者誤以為資料已清空或已更新 |

### 特別需要檢查：record identity

如果 Workbench 以 array index、顯示順序、PDF 頁碼或檔名作為更新定位，排序、filter 或重複檔名可能令操作落到錯誤 record。應確認每筆 record 有穩定且唯一的 ID，所有 Save、Review、Reload、Export 都以該 ID 對應同一筆資料。

### 特別需要檢查：OCR 原始結果與人工修正分離

OCR 原始 evidence 不應被人工編輯直接覆蓋。Workbench 應清楚區分：

- 原始 OCR 結果／原始 evidence；
- 人工修正後、目前採用的欄位值；
- review status 及 review metadata。

重新 OCR 或重新匯入時，不應未經明確操作就覆蓋已保存的人工修正，尤其不應覆蓋 reviewed record。

---

## 4. 建議的 Review State Model

建議把資料內容及 review status 視為兩個不同責任：

- **Record data**：可編輯欄位、人工修正後的值。
- **Review state**：`open` / `reviewed`，以及 reviewer、reviewed_at、被 review 的資料版本。

最少應遵守以下轉換：

| Current state | Action | Expected result |
|---|---|---|
| New | Create | `open` |
| `open` | Edit + Save | 資料更新；status 保持 `open` |
| `open` | Mark Reviewed | status 變成 `reviewed`，鎖定當前已保存版本 |
| `reviewed` | Save / Edit | 拒絕；資料及 status 不變 |
| `reviewed` | Reload / View | 只讀取及顯示已 reviewed 的同一版本 |

**不建議**讓一般 Save 同時決定 status。也不建議前端自行將 status 重設成 default。Review 是獨立而明確的狀態轉換。

### Unsaved changes 的處理

Mark Reviewed 前應檢查 dirty state：

- 最安全的預設：有未保存修改時，阻止 Mark Reviewed，提示先 Save。
- 另一可接受方案：使用單一後端 transaction，先保存完整修改，再將該版本標記 reviewed；兩步必須原子化，不能只成功一半。

無論採用哪種方式，都必須確保 reviewed 的是使用者最後確認的內容。

### Reviewed 後的例外修正

使用者目前要求 reviewed 後不可再改。預設應完全 read-only。若將來業務上需要更正，應設計獨立、受權限控制並留 audit trail 的 Reopen / correction 流程，而不是重新啟用普通 Save。

---

## 5. 最低回歸測試矩陣

修正 WB-001、WB-002 後，至少執行以下測試：

| Test ID | Setup / Action | Expected result |
|---|---|---|
| T01 | 建立新 record | status = `open` |
| T02 | 修改一個文字欄位後 Save | 新值持久化；status 仍為 `open` |
| T03 | 修改一個數值欄位後 Save | 數值精確保存；status 仍為 `open` |
| T04 | Save 後 refresh | 顯示最新保存值 |
| T05 | Mark Reviewed | status = `reviewed`；資料內容不變 |
| T06 | reviewed 後再按 Save | UI 不提供修改入口；後端亦拒絕修改 |
| T07 | reviewed 後直接呼叫 update endpoint | 回傳明確拒絕；資料完全不變 |
| T08 | reviewed 後 refresh / 重新開啟 | status 和欄位值均維持 reviewed 時的版本 |
| T09 | 有 unsaved changes 時按 Mark Reviewed | 阻止操作並提示先 Save，或原子保存後 review |
| T10 | Save 完成後立即 Mark Reviewed | reviewed 的資料必須是剛保存的新值 |
| T11 | Save 失敗後按 Mark Reviewed | 不可把未持久化資料標記為 reviewed |
| T12 | 快速連按 Save / Review | 不產生狀態回退、舊 response 覆蓋或重複更新 |
| T13 | 兩個分頁先後修改同一 record | 偵測版本衝突；不可靜默覆蓋較新資料 |
| T14 | reviewed record 進行 OCR rerun / re-import | 不可靜默覆蓋已 reviewed 資料 |
| T15 | Open / Reviewed filter 及 count | 列表、filter、count 與 persisted status 一致 |
| T16 | Export reviewed record | 匯出內容與最新 reviewed record 完全一致 |

建議把 WB-001、WB-002 及 T05–T12 寫成自動化 regression tests；不能只靠手動點擊測試。

---

## 6. Source Code Review 時必須追蹤的程式路徑

要完成真正的 code-level review，應在 `workbench/` 找出並串起以下路徑：

1. **Load**：record 從何處載入；是否有 cache；唯一 ID 如何定義。
2. **Edit**：欄位變更寫入哪個 state；dirty state 如何判斷。
3. **Save**：UI handler → request/payload → backend handler/service → database update → response → client state refresh。
4. **Review**：按鈕 handler → status transition → database update → list/detail refresh。
5. **Reload**：重新進入同一 record 時，真正讀取哪個資料源。
6. **Other write paths**：batch update、OCR rerun、import、delete/restore、export 前的資料組裝。

每個已確認 bug 的最終報告應補上：檔案路徑、function／method 名稱、程式碼行號、最小重現步驟、root cause、修正 diff，以及對應 regression test。

---

## 7. 建議修正次序

1. **先釐清 WB-002 的資料庫真相**：比較 Save 後、Review 後、Browser refresh 後的 persisted record，確定舊資料只在 UI 還是已寫回資料庫。
2. **修正 WB-001**：Save 不再改 status；後端封鎖 reviewed record 的一般更新。
3. **修正 WB-002**：Save 和 Review 都以最新 server record 更新 client state；禁止舊 snapshot 覆蓋新資料。
4. **補齊 regression tests**：優先完成 T02、T04–T12。
5. **再完成 WB-R01–R15 檢查**：尤其是 OCR rerun、record identity、並行修改和 Export。

---

## 8. 本次結論與限制

- WB-001：已由使用者觀察到，必須修正。
- WB-002：已由使用者觀察到 UI 顯示舊資料；但 persisted data 是否同樣錯誤仍待驗證。
- WB-R01–R15：目前全部是待查風險，不應當作已發現的 code bug。
- 本文件沒有宣稱已執行 repository tests，也沒有聲稱找到具體程式碼行號。

**要完成下一版真正的 source-level review，需要可讀取的 `workbench/` 原始碼（例如 GitHub 連接成功，或提供 repository ZIP）。** 屆時應把本文件中的每項風險逐項標記為 `Confirmed`、`Not Reproducible` 或 `Not Applicable`，並加入檔案路徑、行號及測試證據。
