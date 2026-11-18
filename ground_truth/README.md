# Ground truth（人手核對基準）

呢個資料夾用嚟放**人工核對過**的 OCR ground truth，同埋評分報告。目的係令之後任何 OCR / 規則改動都可以用同一批頁面、同一批行做 before/after，而唔係靠感覺。

## 三個不可妥協的原則

1. `verified=0` 的行係 OCR 預填，**唔係 ground truth**。評分工具只會計 `verified=1`。
2. GT 只記錄「圖片上實際印咗咩」。唔可以因為 OCR 讀錯而「順手改到合理」；有疑問就喺 `gt_note` 寫低。
3. 金額欄填**數值**（canonical，例：`119366280.10`），唔好填 `119.366.280,10`。千分位/小數點風格屬於頁面層 `number_format_gt`，唔屬於逐行金額。

## 目錄

```text
ground_truth/
  issue45_2026/
    gt_pages.csv          每頁 metadata（銀行、日期、數字格式）+ 核對狀態
    gt_rows.csv           逐行 label / debit / credit + 核對狀態
    worksheet_manifest.json  由邊個 run、邊個 PDF、幾時建立
    crops/page_0007/*.png 逐行裁圖，方便核對
    score_report.json     評分結果（由 gt_score.py 產生）
    score_errors.csv      錯行清單
```

## 第一步：選頁（唔好一次過標 26 頁）

建議由細到大：

| 批次 | 頁 | 原因 |
|---|---|---|
| 起步 | 7（中文）、10（葡文） | SKILL.md 的 smoke test 頁，約 127 行 |
| 第二批 | 17（雙語）、9（中文）、15（葡文） | 覆蓋雙語與不同 layout |
| 第三批 | 29 | 目前最差的一頁，用嚟量度改善 |

每次只加一兩頁，確認流程順暢先擴展。

## 第二步：建立工作表

用一個**現行 pipeline** 的 run（要有 `documents.csv`、`tb_rows.csv`、`images/`）：

```bash
python tools/gt_build_sheet.py output_v02_issue45_full_1900 \
  --out ground_truth --issue issue45_2026 --pages 7,10
```

會產生 `gt_pages.csv`、`gt_rows.csv`、逐行 `crops/`。全部 `verified=0`。

注意：
- 工具預設**唔會覆寫**已存在的 `gt_rows.csv`，避免清掉你核對過的內容。真係要重建才加 `--force`。
- 之後加頁用 `--append`，只會追加未標註過的頁碼，已核對的內容保持不變：

  ```bash
  python tools/gt_build_sheet.py output --out ground_truth \
    --issue issue45_2026 --pages 17,9,15 --append
  ```
- 如果 pipeline 修正令某頁的行結構改變（例如頁 29 修好欄位後由 53 行變 65 行），用 `--rebuild-pages` 只重建該頁：

  ```bash
  python tools/gt_build_sheet.py output_v03_issue45 --out ground_truth \
    --issue issue45_2026 --rebuild-pages 29
  ```

  該頁原有的 `verified` 與修正會被重置，其他頁不受影響；重建後要重新核對該頁。
- 如果只是 pipeline 修正後多讀到幾行（例如總額行、頁 29 補回的 11 行），用 `--sync-missing` 只追加缺少的行，**保留所有已核對內容**：

  ```bash
  python tools/gt_build_sheet.py output_v03_issue45 --out ground_truth \
    --issue issue45_2026 --sync-missing
  ```

  追加的行係 `verified=0`、`gt_note=sync_added_unverified`，需要人手核對後才計分。

## 第三步：逐行核對

### 3.1 先核 page 層

打開 `gt_pages.csv`，逐頁對住原圖確認：

| 欄 | 要核對咩 |
|---|---|
| `bank_name_gt` | 銀行全名（保留原文大小寫/重音） |
| `statement_date_gt` | 報表日期（ISO `YYYY-MM-DD`） |
| `number_format_gt` | 印出嚟係 `comma_dot`（1,234.56）定 `dot_comma`（1.234,56） |
| `verified` | 核對完設 `1`；`reviewer`、`reviewed_at` 順手填 |

`number_format_gt` 一定要照**圖片實際印刷**判斷，唔可以照抄 OCR 或按語言猜。

### 3.2 再核逐行

用 Excel / Numbers / LibreOffice 打開 `gt_rows.csv`（UTF-8 CSV），同一個視窗開 `crops/page_XXXX/` 的裁圖，逐行對：

| 欄 | 規則 |
|---|---|
| `label_gt` | 圖片上的帳目名稱原文。保留繁體/葡文/重音/大小寫。唔好順手 OpenCC 轉換。 |
| `debit_gt` / `credit_gt` | 數值，例 `595846.01`。空白 = 該格冇金額；`0.00` = 印住零。 |
| `debit_dash_gt` / `credit_dash_gt` | 印住 `-` 或 `—` 時設 `1`（同時 `*_gt` 留空）。 |
| `gt_note` | 任何唔肯定、頁面本身異常、跨行 label 等。 |
| `verified` | 只在你**親眼對過圖**之後設 `1`。 |
| `reviewer` / `reviewed_at` | 你個名 / `YYYY-MM-DD`。 |

核對重點：

- **唔好接受預填**。OCR 預填只係幫你少打幾個字；每一行都要對圖。預填錯而你又冇改，GT 就會變成「OCR 的鏡子」，之後所有指標都會虛高。
- **跨行 label**：如果一個帳目名稱在圖片上佔兩行，GT 應該合併成同一個 `label_gt`（用空格分隔），金額放同一行；`y_frac` 保持工具填的值，唔好手改。
- **總額/合計行**都要標，呢啲行係檢查 debit/credit 平衡的關鍵。
- **數字格式**：`119.366.280,10` → GT 填 `119366280.10`；`1,234.56` → `1234.56`。負數用 `-123.45`。
- **千分位唔確定**：先睇 `number_format_gt`，再用右對齊/3 位分組判斷；仍然唔肯定就喺 `gt_note` 寫低，唔好亂填。
- **空白 vs 零**：印 `-` 同印 `0,00` 係兩件事，分別用 dash flag 同 `0.00` 表示。

### 3.3 分批提交

建議每核完一頁就：
1. 儲存 CSV（保持 UTF-8）。
2. 即刻跑一次評分，確認 alignment 係 100% 左右（如果 GT 係由同一個 run 建立，代表 plumbing 正常）。
3. Commit（GT CSV 應該入 git；`crops/` 可以唔入）。

之後任何對 `verified=1` 行的修正（包括第二重核對發現的錯字），都要喺 `gt_changelog.md` 記錄：邊一行、改咗咩、依據係咩。GT 都可以有錯，但改動必須可追溯。

## 第四步：評分

```bash
python tools/gt_score.py ground_truth/issue45_2026 --run output_v03_issue45
```

輸出：
- `score_report.json`：整體 + 逐頁指標
- `score_errors.csv`：每一條錯 label / 錯金額 / 多出金額，連 run 的 label、review_status、review_reason

終端會印出摘要：

```text
Rows: run=... matched=... gt_missing=... run_spurious=... alignment F1=...
Labels: exact=... CER=... (n=...)
Debit : exact=... (n=...)
Credit: exact=... (n=...)
Wrong amounts: ... rows, of which auto_check_pass: ...
```

如果 `--run` 只包含部分 GT 頁（例如只跑頁 29），其他頁會自動跳過並顯示 warning；頁面層 metadata 未 `verified=1` 亦會警告。

## 指標點解讀

| 指標 | 意思 | 注意 |
|---|---|---|
| `alignment_f1` | 行對齊：GT 行有冇對應 run 行 | 用 y 位置配對（優先 `y_frac`），唔係用內容配對 |
| `gt_missing_rows` | GT 有、run 冇 | 可能漏行或行被合併 |
| `run_spurious_rows` | run 有、GT 冇 | 可能重複行或把 header 當成資料 |
| `label_exact_rate` | 標籤逐字完全相同 | 中文忽略空格差異；葡文只合併多餘空格 |
| `label_cer` | 字元錯誤率（Levenshtein / 長度） | 越低越好；`0.05` = 平均每 20 字錯 1 字 |
| `debit_exact_rate` / `credit_exact_rate` | 金額數值完全相同 | 以 Decimal 比較，`1.0` = `1.00` |
| `wrong_amount_rows` | 有金額錯的行數 | 最關鍵 |
| `auto_pass_wrong_amount_rows` | **系統自動放行但金額錯** | 最危險，必須逐條查 |

## 唔可以講嘅結論

- 「GT 由 OCR 預填，所以 exact rate 100%」→ 呢個只係 plumbing 測試，唔係 accuracy。
- 「debit/credit 合計相等，所以已核實」→ 只係 plausibility check。
- 「confidence 高就準」→ Vision 的 confidence 未校準。
- 未夠 `verified` 行數之前，唔好宣稱任何準確率或改善幅度。

## 常見錯誤

| 錯誤 | 後果 |
|---|---|
| 照抄 OCR 預填，冇對圖 | GT 與 OCR 同錯，指標永遠完美 |
| 把 `dot_comma` 頁的金額打成 `1.234,56` | 數值比較全部錯 |
| 把 `-` 當 `0.00` | 錯行會被當成正確 |
| 自行把簡體轉繁體 | GT 唔再係圖片原文 |
| 手改 `y_frac` / `bbox_*` | 行對齊會錯，alignment F1 失真 |
| 用 `--force` 重建已核對的 CSV | 辛苦核對的內容會消失 |
| GT 頁面同 run 頁面試圖唔同 | 對齊失敗，指標無意義 |

## 建議節奏

1. 先完成 **7 + 10**，確認指标計算同流程冇問題（約 127 行）。
2. 加入 **17（雙語）+ 9 + 15**，總數約 380 行。
3. 加入 **29**，專門量度最差頁有冇改善。
4. 之後每次改 OCR/parser，都用同一份 GT 跑 `gt_score.py`，並保留 `score_report.json` 做版本對比。

有 GT 之後，先可以負責任咁講「呢個改動令 debit exact rate 由 X% 升到 Y%」；冇 GT 就只能講「輸出有差異」。
