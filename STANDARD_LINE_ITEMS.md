# Standard Line Items（v0.5.2）

## 為何要有 standard master

Pairwise `name_mapping_review` 適合處理少量 uncertain names，但唔適合做長期科目主檔：

- OCR 會讀到中文、葡文、中葡合併、錯字及縮寫。
- 同一個科目可能有多個 OCR 寫法。
- 如果每個 OCR 寫法都建立一個 concept，concept 數量會失控。

所以应该分開：

```text
Standard line item（唯一、人手維護）
        ↑
Aliases（中文／葡文／OCR variant／combined）
        ↑
OCR label_raw / label_standard
```

Original OCR fields 永遠保留；standard mapping 只寫入：

```text
line_items.line_item_concept_id
line_items.mapping_status
line_items.mapping_version
```

## User-provided standard CSV (v0.6.1)

```bash
# after editing labels/standard_label_by_user.cleaned.csv
python tools/import_user_standard.py build \
  --db data/macau_balancete.sqlite \
  --csv labels/standard_label_by_user.cleaned.csv --from-cleaned \
  --report data/user_standard_mapping_report.csv
```

`--from-cleaned` reads the edited file directly; without it, the tool refuses to overwrite an existing cleaned CSV unless `--overwrite-cleaned` is passed.

會寫入：

```text
standard_labels
standard_label_aliases
line_items.user_standard_label_id
line_items.user_standard_match_status
```

User CSV 的 parent/child 每一行會成為一個 standard（子項會有 parent_standard_id）。現有 row 用 `label_standard` + `parent_label_standard` 對 path；葡萄牙文同時以 canonical_pt / aliases 匹配。

Current edited file: **74 standards, all approved**. 8 new standards were added by the user (銀行貸款、銀行結存、銀行透支、保留溢利/（損失）、預付款項、總公司之欠款、關連公司之欠款、儲備).

Mapping: 1874 `parent_exact`, 324 `child_path_exact`, 66 `raw_parent_exact`, 8 `raw_child_path_exact`; 89 rows / 70 distinct remain unmapped.

### 已修正但需要確認的地方

- Reserve block 錯位：`股本` 原本配 `FUNDO DE ΜΑΝΕΙΟ`，已改為 `CAPITAL`；`營運資金` → `FUNDO DE MANEIO`；`法定儲備` → `RESERVA LEGAL`；`自定儲備` → `RESERVA ESTATUTÁRIA`；`其他儲備` → `OUTRAS RESERVAS`；另補 `重估儲備` / `RESERVA DA REAVALIAÇÃO`。
- `總收人` → `總收入`；`總收入` → `PROVEITOS POR NATUREZA`；`總支出` → `CUSTOS POR NATUREZA`。
- `AMCM 存款` 的 child rows 原本 parent_pt 用了 `PATACAS` / `MOEDAS EXTERNAS`，已改為 `DEPÓSITOS NA AMCM`。
- 希臘字母 `Μ`（U+039C）已轉回拉丁 `M`；`賬` 統一為 `帳`；`AMCM 存款` 統一為 `AMCM存款`。

以上修正行在 cleaned CSV 標 `provisional`，未經人手確認前不應當成 approved。

## 現有 draft master

```text
labels/line_item_standard.csv
labels/line_item_pt_zh_map.csv
```

`line_item_standard.csv` 欄位：

| 欄位 | 用途 |
|---|---|
| `standard_key` | 穩定 concept key（通常沿用 `lex:zh_...`） |
| `canonical_zh` | 標準中文科目 |
| `canonical_pt` | 標準葡文科目 |
| `canonical_en` | 英文（如有） |
| `level` | `parent` / `child` |
| `parent_key` | 父科目 `standard_key` |
| `category` | asset / liability / income / expense / off_balance 等 |
| `status` | `approved` / `provisional` |
| `aliases_zh` | `|` 分隔中文 aliases |
| `aliases_pt` | `|` 分隔葡文 aliases |
| `aliases_other` | 其他語言／OCR variants |
| `merge_keys` | 要合併入此 standard 的舊 concept keys |
| `source` / `note` | 來源及備註 |

目前 draft：

```text
65 standard rows
59 有 canonical_zh
61 有 canonical_pt
55 條 pt→zh merge keys
6 條 pt-only 未決定中文名
```

6 條未決定：

```text
lex:pt_018  Depósitos à ordem no exterior
lex:pt_019  Depósitos à ordem noutras instituições de crédito
lex:pt_046  Outras reservas
lex:pt_047  Outros Valores
lex:pt_055  RESERVA DA REAVALIAÇÃO
lex:pt_041  no Território
```

Draft 中 `provisional` 表示：

- 由 bilingual pair 或 curated pt→zh proposal 而來，未經人手確認。
- `apply` 預設會跳過，不會靜默套用。

## 匯出 master

```bash
python tools/standard_line_items.py export \
  --db data/macau_balancete.sqlite \
  --csv labels/line_item_standard.csv
```

會由現有 registry + bilingual candidates + `labels/line_item_pt_zh_map.csv` 產生 draft。

## 人手 review

在 `labels/line_item_standard.csv`：

1. 將已確認的 row `status` 改為 `approved`。
2. 填入 6 條 pt-only row 的 `canonical_zh`，或把它們 map 到現有 `standard_key`。
3. 有需要時填 `parent_key` / `category`。
4. 保留 `merge_keys`；apply 時會將舊 concept merge 入 target。

## Apply

```bash
python tools/standard_line_items.py apply \
  --db data/macau_balancete.sqlite \
  --csv labels/line_item_standard.csv \
  --by tony
```

行為：

- 只 apply `status=approved` 的 row（除非 `--include-provisional`）。
- Upsert `line_item_concepts`。
- 將 `merge_keys` 的舊 concept merge 入 target：
  - aliases 移到 target
  - `line_items.line_item_concept_id` 更新
  - 舊 concept 標 `merged`
  - 寫入 `name_mapping_audit`
- 將 `aliases_zh` / `aliases_pt` / `aliases_other` 加為 approved aliases。
- Re-apply mapping，更新 `mapping_status`。

## 之後的 OCR mapping

- 中文 OCR label → 中文 alias → standard concept。
- 葡文 OCR label → 葡文 alias → 同一 standard concept。
- 中葡合併 OCR label（例如 `CAIXA現金`）→ 一個 combined alias → standard concept。
- 未命中任何 standard alias → `concept_unmapped`，進入 review；**不再自動建立 provisional concept**（下一步會收緊）。

## 原則

- 一個經濟科目只有一個 standard concept。
- 語言版本係 alias，不是另一個 concept。
- 相似字不等於同一科目；`代收帳` / `代收帳(貸方)` 要分開。
- `provisional` 不會自動套用；要人手改 `status=approved`。
- Raw OCR evidence 永遠保留。
