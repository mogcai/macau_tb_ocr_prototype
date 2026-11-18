# 名稱標準化 Registry（v0.5.0）

呢個 registry 將三種名稱分開管理：

1. **Raw names**：OCR / PDF 原始文字，永不覆寫。
2. **Pending review**：系統提出的配對候選，未批准前不會套用。
3. **Approved registry**：經人手批准的名稱、語言、alias 及唯一 ID。

銀行名稱同會計科目分開兩套 registry，唔會將「總行／分行／集團」或者「相似但不同」的科目自動合併。

## 資料表

| Table | 用途 |
|---|---|
| `banks` | 銀行實體；`canonical_zh` / `canonical_pt` / `entity_type` / `registry_status` |
| `bank_name_aliases` | 銀行 raw name alias，分 `official` / `branch` / `group` / `ocr_variant` / `combined` |
| `line_item_concepts` | 會計科目 concept；可同時有 `canonical_zh`、`canonical_pt`、`canonical_en` |
| `line_item_aliases` | 科目 raw name alias，分 `official` / `ocr_variant` / `translation` 等 |
| `name_mapping_review` | 待批配對候選；`pending` / `approved` / `rejected` |
| `name_mapping_audit` | 批准、拒絕、concept merge 的 audit trail |
| `line_items.line_item_concept_id` | 已批准 concept；`mapping_status` / `mapping_version` 記錄套用狀態 |

`label_raw`、`label_standard`、`bank_name_raw` 不會被 registry 改寫。

## Build

```bash
python tools/name_registry.py build \
  --db data/macau_balancete.sqlite \
  --lexicon labels/label_lexicon.csv \
  --bank-aliases labels/bank_aliases.csv
```

Build 會：

1. 由 `labels/label_lexicon.csv` seed 同語言 concepts 及 approved aliases。
2. 由 `banks` / `bank_name_aliases` seed 銀行 registry；現有 `bank_aliases.csv` 會成為 approved aliases。
3. 掃描 `v_current_line_items`，為未 approved 的 raw/standard label 建立候選。
4. 掃描 bilingual statements，將相鄰中葡行配對成 `bilingual_pair` 候選（只係 evidence，不會自動批准）。
5. 只將 approved aliases 套用到 `line_items.line_item_concept_id`。

Report 會寫入：

```text
data/macau_balancete_name_registry_report.json
```

## 查看待審候選

```bash
python tools/name_registry.py list --db data/macau_balancete.sqlite --limit 30
python tools/name_registry.py list --db data/macau_balancete.sqlite \
  --entity-type line_item --limit 50
python tools/name_registry.py list --db data/macau_balancete.sqlite \
  --match-method bilingual_pair --limit 50
```

輸出會按 `occurrence_count` 排序，方便先處理最常見名稱。

## 批准

```bash
python tools/name_registry.py approve --db data/macau_balancete.sqlite \
  --id 124 --by tony --note "p17 bilingual pair"
```

- 如果候選係跨語言 pair（例如 `Caixa` → `現金`），批准會將舊葡文 concept **merge** 入目標中文 concept：
  - aliases 移到目標 concept
  - `line_items.line_item_concept_id` 更新
  - 舊 concept 標 `merged`，`merged_into_concept_id` 指向目標
  - 寫入 `name_mapping_audit`
- 如果候選只係 OCR variant，會新增 approved alias，不會 merge concept。
- 批准後會自動 re-apply mapping。

## 拒絕

```bash
python tools/name_registry.py reject --db data/macau_balancete.sqlite \
  --id 125 --by tony --note "different account"
```

拒絕只會更新 review status，不會新增 alias；同一候選不會被下一次 build 自動重設為 pending。

## 重新套用

如果 OCR run 被 `--replace`，或手動改了 alias：

```bash
python tools/name_registry.py remap --db data/macau_balancete.sqlite
```

## Mapping status

| 值 | 意思 |
|---|---|
| `concept_exact` | `label_standard` 直接命中 approved alias |
| `concept_alias` | `label_standard` 未命中，但 `label_raw` 命中 approved alias |
| `concept_unmapped` | 有名稱但未有 approved concept |
| `concept_missing_label` | 空白 label，沒有可映射文字 |

## 檢視用 views

| View | 用途 |
|---|---|
| `v_bank_registry` | 銀行 entity + alias 數目 |
| `v_line_item_concepts` | concepts + alias 數目 |
| `v_name_mapping_pending` | 所有 pending candidates |
| `v_line_item_concept_map` | 逐行 OCR label 與 concept |
| `v_unmapped_line_item_names` | 仍未有 approved mapping 的標準名稱 |

例子：

```sql
SELECT * FROM v_name_mapping_pending
ORDER BY occurrence_count DESC;

SELECT label_standard, label_language, occurrence_count
FROM v_unmapped_line_item_names
LIMIT 30;

SELECT c.canonical_zh, c.canonical_pt, COUNT(*) AS rows
FROM v_line_item_concept_map m
JOIN line_item_concepts c ON c.concept_id = m.line_item_concept_id
GROUP BY c.concept_id
ORDER BY rows DESC;
```

## 目前 seed（2026-11-18，issues 42–46）

```text
banks:                       38
concepts:                   216（120 lexicon + 96 provisional discovery）
approved line-item aliases: 123
pending line-item reviews:  145
line items concept_exact:  2214
concept_alias:                1
concept_unmapped:           140（有名稱但未 approved）
concept_missing_label:        6（空白 label）
```

Pending 之中有 29 條 `bilingual_pair`，來自 issue 45 page 17 等雙語頁。
佢哋只係候選；未經 `approve` 不會改寫 concept 或 line item mapping。

## 原則

- **系統可以建議，但只有 approved mapping 才可正式套用。**
- 中葡 bilingual pair 只係候選，唔會自動當成翻譯。
- 銀行集團、持牌法人、分行、歷史名稱要分開；不要為求整齊而合併。
- 相似度高唔等於同一科目；`代收帳` / `代收帳（貸方）` 之類要人手判斷。
- Registry 改動會寫入 `name_mapping_audit`，但唔會改寫 OCR raw evidence。
