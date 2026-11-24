# 第一期本機檢視（phase-1 dashboard）

Tony 在自己電腦上打開、對着自己的 V3 SQLite 看已採納首批機構。這不是 Workbench，不會改對照、不會改 adopted、不會寫入資料庫，也沒有登入。只綁定 `127.0.0.1`，不要對區域網或公開地址開放。

比較窗只限結算日 **2026-03-31、2026-06-30、2026-09-30**。這不是日曆年 2025，也不是完整最近一年。`2025-12-31` 不計入預設可用集。

可用數只計 **adopted**（`statement_status = active` 而且有 adopted extraction）。窗內但未 adopted 的報表只顯示為缺口，不補數。`identity_status` / `metadata_status` 仍是 proposed，不會因此把已 adopted 的報表剔走。

15 個 `canonical_zh` 分成三組，組與組不相加，組內也不設合計：

- 8 間本地法人商業銀行，各自獨立
- 3 份關聯報表，必須分列，不可當成額外一間銀行
- 4 間外地銀行分行，不可同本地法人加總

「加總後睇澳門銀行整體情況」這個目標仍在，但現在把已採納名單相加並不成立，所以加總位置是封着的，不計算。

每一行都顯示 `mapping_status`：`approved`、`proposed`、`insert-direct`、`unmapped`。`approved` 是 Workbench 標記，不是財務接受，也不等於科目仍然有效。科目的 `item_status`（以及 `merged_into_standard_item_id`）另外顯示；`retired` 或 `merged` 的對照，包括人工 insert，仍然列出，不會被靜默拿掉。`v_unmapped = 0` 只表示幾乎每行都有候選，不是已經可以安全跨行比較。金額與佔比的欄位都在，但具體指標尚未決定，本版不計算佔比，也不把 `user:總計` 當成總資產。跨行對照會分開四種情況：沒有 adopted 報表、有 adopted 但沒有 balance_sheet、有 balance_sheet 但沒有所選候選、候選有金額。只有損益表的一期不會寫成「此期沒有此候選」。

## 安裝

在專案根目錄：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-workbench.txt
```

介面用現有的 FastAPI / Jinja2，不另加一套前端。

## 啟動

路徑用參數或環境變數，程式裡沒有寫死某一部電腦的檔案位置。

```bash
python -m phase1_dashboard --db /你的路徑/macau_balancete_v3.sqlite
```

或：

```bash
export PHASE1_DASHBOARD_DB=/你的路徑/macau_balancete_v3.sqlite
python -m phase1_dashboard
```

然後打開 <http://127.0.0.1:8010/>。預設埠是 `8010`，避免撞上 Workbench 的 `8000`。`--host` 只接受 `127.0.0.1`（`localhost` 與 `::1` 會改綁到 `127.0.0.1`）。其他地址會直接拒絕。請求的 `Host` 只接受 `127.0.0.1` 或 `localhost`，可以帶一個埠號；其他 `Host` 回 HTTP 400，避免 DNS rebinding 讀到這份沒有登入的本機資料。

資料庫以 `mode=ro` 與 `PRAGMA query_only` 打開。請指向 V3。檔名 `macau_balancete.sqlite`（包括 `archive/database/` 裡的 V5）會被拒絕，不會拿來當分析資料。

## 畫面上有什麼

- **覆蓋**：三組機構 × 三個結算日。可用格顯示幣別、`unit_scale`、`reporting_scope`、identity / metadata，以及四種 mapping 行數。缺的格子寫「缺口」和原因。
- **機構頁**：同一間機構的三季。只把資產負債表上恰好一個候選標準項的行排在一起。對不齊的行留在原位。不同 `statement_type` 不分開加總。
- **跨行對照**：一次只選一個分組、一個結算日、一個候選 `item_key`。沒有合計欄，沒有排名。幣別或 `unit_scale` 不一致時保持原記載，不換成同一單位。
- **佔比**：可以切換，但分母尚未決定，畫面寫「未計算」。

草稿警示（`user:總計`、`user:AMCM存款`、`user:股本` / `user:營運資金`、存款父項與大西洋銀行版面）來自 2026-11-22 的對照草稿，不是已凍結的官方對照。畫面上的行數與缺口來自你打開的那個資料庫，不是寫死的覆蓋統計。

## 測試

```bash
python -m unittest tests.test_phase1_dashboard -v
```

測試用臨時的合成 V3 資料庫，不讀、不改 Tony 的操作庫，也不讀 V5。
