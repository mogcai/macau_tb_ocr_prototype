# v5 Baseline — read-only reference

This directory is the evidence baseline for the v3 controlled rebuild. The
audit never writes to the v5 database.

## Frozen database

| Item | Value |
|---|---|
| Original database | `data/macau_balancete.sqlite` |
| Baseline copy | `data/baseline/baseline_v5_20261118.sqlite` |
| SHA-256 | `a08ca1e24a5ae7cbf50067093124e7d42e5b7a2cbef36d56c6f6424586c598b6` |
| Size | 12,156,928 bytes |
| Schema version | 5 |
| App version | 0.7.0 |

The baseline copy is intentionally stored outside the tracked database file and
should be treated as read-only:

```bash
chmod 444 data/baseline/baseline_v5_20261118.sqlite
```

## Re-running the audit

```bash
python3 tools/audit_v5.py \
  --db data/baseline/baseline_v5_20261118.sqlite \
  --out docs/audit
```

The script opens SQLite with `mode=ro` and `PRAGMA query_only=ON`.

## Provenance checks covered

- SHA-256 of the database and all 14 source PDFs.
- SHA-256 of `run_report.json`, `tb_rows.csv`, `documents.csv`,
  `import_manifest.json` and `review_queue.csv` for every run.
- Comparison of `runs.source_pdf_sha256` against the actual PDF file.
- Image count and image bytes per run.
- SHA-256 of every CSV in `labels/`.

## Artifact files

| File | Purpose |
|---|---|
| `SQL_AUDIT_V5.md` | Human-readable audit report |
| `SQL_AUDIT_V5.json` | Machine-readable summary |
| `artifact_manifest.json` | Hashes and provenance evidence |
| `registry_inventory.csv` | All registry records from DB and CSVs |
| `standard_item_reconciliation.csv` | 89 × 65 × 120 candidate reconciliation |
| `mapping_conflicts.csv` | Exact conflicts after name folding |
| `mapping_partial_overlaps.csv` | Substring / qualifier partial overlaps |
| `mapping_coverage_by_run.csv` | Mapping coverage per run |
| `mapping_coverage_by_language.csv` | Mapping coverage per language |
| `entity_anomalies.csv` | Bank / entity cleanup candidates |
| `statement_anomalies.csv` | Statement-level anomalies |
| `page_anomalies.csv` | Page-level OCR / number-format anomalies |
| `amount_quality.csv` | Amount states and cents consistency |
| `amount_mismatches.csv` | Row-level amount mismatches (currently empty) |
| `legacy_id_crosswalk.csv` | `label_standard_id` → lexicon / concept / user standard |

## Important limitation

This audit reports evidence; it does not approve mappings. In particular:

- automatic `parent_exact` / `child_path_exact` matches are not human approval;
- `merge_candidate` in the reconciliation matrix is still a proposal;
- missing parent context remains missing, not inferred.
