# v3 Raw Replay — P4 first slice

**Date:** 2026-11-19
**Target:** `data/macau_balancete_v3.sqlite`
**Source:** 14 run directories under `outputs/runs/`
**v5 reference:** `data/baseline/baseline_v5_20261118.sqlite` (read-only)

This slice imports raw OCR evidence only. It deliberately does **not** create
financial statements, statement rows, entity approvals or mappings yet.

## Imported

| Object | Count |
|---|---:|
| Gazette issues | 14 |
| Source documents | 14 |
| Document pages | 118 |
| Extraction runs | 14 |
| Page extractions | 118 |
| Extracted rows | 7,156 |
| Statements | 0 (P2 not started) |
| Statement rows | 0 (P2 not started) |

All 7,156 extracted rows are `row_disposition = 'unassigned'`.  They are ready
for P2 Page Classification and Statement Segmentation.

## Reconciliation against v5

`docs/audit/replay_reconciliation.csv` compares every v5 `line_item` with the
v3 extracted row using:

```text
run_id + pdf_page + row_order
```

| Check | Result |
|---|---:|
| Rows in v5 | 7,156 |
| Rows in v3 | 7,156 |
| Missing in v3 | 0 |
| Extra in v3 | 0 |
| `label_raw` mismatches | 0 |
| `debit_raw` mismatches | 0 |
| `credit_raw` mismatches | 0 |
| Cents mismatches (where both sides have a value) | 0 |

## Amount states

| State | Debit | Credit |
|---|---:|---:|
| amount | 2,448 | 2,408 |
| blank | 4,624 | 4,641 |
| dash | 7 | 6 |
| ambiguous | 52 | 78 |
| unreadable | 25 | 23 |

The v5 audit reported 2,434 debit and 2,400 credit parsed rows using a looser
definition.  The v3 contract is stricter: `amount` requires an exact minor-unit
value.  Five rows with ambiguous OCR / recovered separators are therefore kept
as `ambiguous` rather than promoted to `amount`; their candidate
`debit_amount` / `credit_amount` and parse status remain in
`extracted_rows.row_payload_json`.

## Idempotency

Running the replay again must skip existing rows:

```bash
python3 tools/replay_runs_to_v3.py \
  --db data/macau_balancete_v3.sqlite \
  --log docs/audit/replay_import_log.csv
```

The second run inserts 0 rows and skips all existing rows.  If a run's row set
changes, the importer aborts instead of silently overwriting.

## Next phase

1. P2: Page Classification for 118 pages; issue 17 negative pages become
   `audit_report` / `shareholder_list` / `other`.
2. P2: Entity / Statement cleanup and `statement_pages`.
3. P1: Standard Item reconciliation and catalogue decisions.
4. P4 second slice: create `statement_extractions` and `statement_rows` for
   included pages, then map legacy evidence as `proposed`.
