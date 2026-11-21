# Data directory

| File / folder | Role | Status |
|---|---|---|
| `macau_balancete_v3.sqlite` | Operational V3 database (workbench, ingest, adopt) | **Current** |
| `baseline/` | Read-only MVP / v5 / pre-adoption snapshots | Frozen |
| `macau_balancete_import_report.json`, `macau_balancete_name_registry_report.json`, `name_review_queue.csv`, `user_standard_mapping_report.csv` | Legacy V5 import / mapping / review reports (paths inside refer to the old `data/` location) | Historical |
| `DATABASE.csv` | Ad-hoc export | Historical |

The legacy V5 database itself is archived at
`archive/database/macau_balancete.sqlite` and is read-only history; keep it for
replay / audit tooling, do not publish new analysis from it. Legacy tools
default to that archived path.

Most `data/*` files are gitignored except a few report/CSV exceptions in
`.gitignore`. Large SQLite files stay local.
