# Archive

Frozen / superseded material kept for history. **Do not treat anything here as the current source of truth.**

| Path | Contents |
|---|---|
| `docs/specs/` | Superseded data-model drafts (v2.2 plan, v3 draft 1.0, old schema notes) |
| `docs/checkpoints/` | Import Issue v2 phase notes / checkpoints |
| `docs/reviews/` | One-off workbench review reports and responses |
| `docs/examples/` | Phase 2A examples and early sample-output notes |
| `database/` | Frozen legacy V5 database (`macau_balancete.sqlite`, schema 5) |
| `ocr_runs/` | Old OCR experiment output folders (formerly `outputs/archive/`) |
| `workspaces/` | Local scratch: `lasped/`, `review/`, failed-import crumbs |
| `notebooks/` | Ad-hoc notebooks (e.g. `scratch.ipynb`) |

## Current locations (not archived)

- OCR pipeline: repo root (`macau_tb_ocr.py`, `vision_ocr_worker.swift`)
- Active docs: `docs/` (see `docs/README.md`)
- Operational V3 DB: `data/macau_balancete_v3.sqlite`
- V3 DDL: `database/v3/`
- Live OCR runs: `outputs/runs/`
- Workbench: `workbench/`

## Archived V5 database

- `database/macau_balancete.sqlite` is the frozen legacy V5 observations DB
  (schema 5, 12,156,928 bytes). It is kept only for replay / historical audits.
- Legacy tools (`build_database.py`, `name_registry.py`,
  `import_user_standard.py`, `standard_line_items.py`, `audit_v5.py`,
  `replay_runs_to_v3.py` V5 lookup) default to this path.
- Treat it as read-only. Copy it to a working location and pass `--db` before
  any V5 write; do not resume publishing from this file.

Archived on 2026-11-21 to separate DeepSeek-era / migration scaffolding from the operational tree. The V5 database was archived on 2026-11-21 when the importer default moved to V3.
