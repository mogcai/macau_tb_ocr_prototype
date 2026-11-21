# Project layout

Cleaned 2026-11-21. Goal: one obvious “current” tree; DeepSeek-era / migration scaffolding under `archive/`.

## Current (use these)

```text
macau_tb_ocr.py              OCR entrypoint
vision_ocr_worker.swift      Apple Vision worker
workbench/                   Verification UI (V3 DB)
tools/                       CLI: ingest, replay, registry, worksheets, GT
database/v3/                 V3 DDL migrations (source of truth for schema files)
data/
  macau_balancete_v3.sqlite  Operational V3 database
  macau_balancete.sqlite     Frozen legacy V5 (replay / historical tools)
  baseline/                  Read-only MVP / v5 baselines
  README.md                  DB roles
docs/                        Active specs + audit worksheets  → docs/README.md
outputs/runs/                Current OCR run folders
labels/                      Lexicon / standards / aliases
ground_truth/                Manual GT + scores
tests/                       Unit tests
```

## Archive (read-only history)

```text
archive/                     → archive/README.md
  docs/specs|checkpoints|reviews|examples/
  ocr_runs/                  formerly outputs/archive/
  workspaces/                lasped, review notes, failed-import crumbs
  notebooks/                 scratch notebooks
```

## Do not

- Treat `archive/` or legacy V5 docs as the operational publish path.
- Move `docs/audit/` without updating tool defaults.
- Overwrite `data/macau_balancete_v3.sqlite` from experiments; use a copy.
- Claim OCR accuracy without `ground_truth/` scores on the target Mac.

## Source of truth cheat sheet

| Concern | Truth |
|---|---|
| OCR raw extraction | Run folder under `outputs/runs/` + `label_raw` / `*_raw` |
| Operational statements / review | V3 DB + workbench |
| V3 schema files | `database/v3/*.sql` |
| Legacy V5 observations | `data/macau_balancete.sqlite` (frozen) |
| Agent behaviour | `AGENTS.md` + `SKILL.md` |
