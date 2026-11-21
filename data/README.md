# Data directory

| File / folder | Role | Status |
|---|---|---|
| `macau_balancete_v3.sqlite` | Operational V3 database (workbench, ingest, adopt) | **Current** |
| `macau_balancete.sqlite` | Legacy V5 observations DB | **Frozen** — keep for replay / historical tools; do not publish new analysis from here |
| `baseline/` | Read-only MVP / v5 baseline snapshots | Frozen |
| `*_import_report.json`, `name_review_queue.csv`, `user_standard_mapping_report.csv` | Legacy import / mapping reports | Historical |
| `DATABASE.csv` | Ad-hoc export | Historical |

Most `data/*` files are gitignored except a few report/CSV exceptions in `.gitignore`. Large SQLite files stay local.
