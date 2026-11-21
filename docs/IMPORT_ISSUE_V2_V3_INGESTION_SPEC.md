# Import Issue V2 — V3 Ingestion Upgrade Specification (V2.0)

**Project:** Macau Gazette Balancete OCR  
**Primary target:** `tools/import_issue.py`  
**Related components:** `tools/replay_runs_to_v3.py`, `tools/build_v3_db.py`  
**Version:** V2.0  
**Status:** Implementation baseline  
**Date:** 2026-11-21  
**Supersedes:** v1.0 and v1.1 of this document

---

## 1. Objective

Upgrade the existing `tools/import_issue.py` into a reliable, traceable import entry point that supports the project's current V3 operational database, while preserving the existing V5 workflow.

The immediate objective is **not** to build the Workbench import UI. The objective is to make the importer itself safe and usable from both the command line and a future Workbench worker.

After this upgrade, the importer supports three explicit targets:

| Target | Purpose | Database effect |
|---|---|---|
| `v5` | Existing legacy workflow | Writes to the legacy V5 database, as it does today |
| `v3` | New standard workflow | Runs download + OCR, then replays raw OCR evidence into the V3 operational database |
| `ocr-only` | Download and OCR without database import | No V5 or V3 database writes |

**Backward compatibility rule:** if `--target` is omitted, retain the current V5 behaviour. Do not silently change the default target to V3 in this release.

The V3 target must never send the V3 database path to `tools/build_database.py`.

---

## 2. Verified current-state facts

These facts were checked against the repository and the operational V3 database on 2026-11-21. They are part of the contract for this upgrade; do not re-derive them differently without updating this section.

### 2.1 Operational data

```text
data/macau_balancete_v3.sqlite
  gazette_issues          14   (issue keys 2026-16 .. 2026-19, 2026-29 .. 2026-33, 2026-42 .. 2026-46)
  source_documents        14
  document_pages          118
  extraction_runs         14
  page_extractions        118
  extracted_rows          7,156
  statements              113
  statement_rows          7,046
  statement_row_mappings  7,251
  statement_reviews       68
```

All 14 `gazette_issues.source_url` and all 14 `source_documents.source_url` values are currently `NULL`.

### 2.2 Existing run artifacts

```text
outputs/runs/issue16_2026 .. issue46_2026   (14 run directories)
  import_manifest.json   8 present: 16, 17, 18, 19, 29, 30, 32, 33
                         6 absent:  31, 42, 43, 44, 45, 46
  run_report.json        14 present; all contain source_pdf, source_pdf_sha256, source_pdf_size
```

The 8 legacy manifests:

- use the key `"source"` for the original URL, not `"source_url"`;
- have no `manifest_version` and no `target`;
- use `steps.{download,ocr,db_import,bank_refresh,name_map}`;
- are hashed by `tools/audit_v5.py` as artifacts.

The 6 runs without a manifest must not be rejected by replay, and their source URL must not be guessed.

### 2.3 V3 schema facts relevant to this upgrade

- `gazette_issues.source_url` exists and is nullable.
- `source_documents.source_url` exists and is nullable.
- `source_documents.sha256` is `NOT NULL` with a 64-character lowercase-hex check.
- `idx_source_documents_sha256` already exists, so duplicate lookup by full hash needs no new index.
- `source_documents.document_key` is currently `issue_key + ":" + sha256[:16]`; it is a uniqueness key, not a full content-identity check.
- `source_documents` has **no file-size column**.
- `extraction_runs.config_json` is an unrestricted text column and is the appropriate place for additional provenance metadata in V2.0.
- `source_documents.storage_uri` currently stores an absolute local path, not a URI.
- `extraction_runs.run_report_uri` currently stores the absolute path to `run_report.json`; replay can use it to resolve an existing V3 run identity.
- The V3 database already contains statements, statement rows, mappings and reviews. Any new import must not alter them.

### 2.4 Current replay behaviour

- `tools/replay_runs_to_v3.py` writes only raw evidence tables and `schema_meta.last_successful_replay_at`.
- It emits one JSON object per run on stdout (JSON Lines), then exits 0 or 1.
- It reads `--db` (the V3 database), `--runs-dir`, `--run-dir`, `--v5-db`, `--log`.
- It derives the Gazette issue key from the run-directory name via `issue(\d+)_(\d{4})` and builds `YYYY-NN`.
- It optionally reads the legacy V5 `runs` table to reuse a V5 run ID.
- It sets `gazette_issues.source_url` and `source_documents.source_url` to `NULL`.
- It uses `INSERT`-if-absent, so an existing `gazette_issues` row is never updated.
- Its only changed-row guard is a per-page row-count comparison. A same-count change to one amount is silently skipped.

### 2.5 Current importer behaviour that must not regress

- `--db` defaults to `data/macau_balancete.sqlite` and is resolved against the **current working directory**.
- `--input-dir` defaults to `input`; `--outdir` defaults to `outputs/runs/issueNN_YYYY` when the filename matches the Gazette pattern, otherwise `outputs/runs/<stem>`. These relative paths are also cwd-based.
- Local PDF paths and `http://` URLs are currently supported for V5.
- `tests/test_import_issue.py` uses a local HTTP server at `127.0.0.1:0`.
- A failed early stage writes a shared `output_import_failed/import_manifest.json`.
- The V5 path-resolution behaviour above is part of the compatibility contract. Do not change it as part of the V3 upgrade.

---

## 3. Architecture and database separation

The existing V5 flow is approximately:

```text
Gazette PDF URL
  → download PDF
  → OCR / parser
  → output run directory
  → documents.csv and OCR result files
  → tools/build_database.py
  → legacy V5 database
  → legacy bank/name refresh and mapping steps
```

`tools/build_database.py` writes the older V5 schema. The Workbench and current review workflow use the V3 operational database at:

```text
data/macau_balancete_v3.sqlite
```

The V3 raw-evidence replay is already implemented separately:

```bash
python tools/replay_runs_to_v3.py \
  --v3-db data/macau_balancete_v3.sqlite \
  --run-dir "outputs/runs/issue33_2026__job_${JOB_ID}"
```

### 3.1 Non-negotiable database separation

- `--db` remains the legacy V5 database option for the V5 target.
- `--v3-db` is the V3 target option.
- V3 target must not invoke `tools/build_database.py`.
- V3 target must not run V5 bank refresh, V5 name mapping, or V5 publication steps.
- V3 target must not read or write the V5 database. It passes `--no-v5-lookup` to replay, and replay must actually skip the legacy lookup code path.
- Never pass the V3 database path to `tools/build_database.py`.
- Never use `data/macau_balancete_v3.sqlite` as the argument to the legacy `--db` option.
- Never write to a frozen/read-only V3 snapshot under `data/baseline/`.

### 3.2 Naming rule

The V3 replay tool must accept `--v3-db` as the primary V3 database option. The existing `--db` spelling is a deprecated alias for compatibility; new code and documentation use `--v3-db`.

This rule exists so that "`--db` means the legacy V5 database" remains unambiguous across the project.

### 3.3 Required replay-tool changes

Passing a new flag from the importer is not enough. The replay tool must implement these changes and prove them with tests.

| Capability | Status | Requirement |
|---|---|---|
| `--v3-db` | New | Primary V3 database option; `--db` remains a deprecated alias |
| `--no-v5-lookup` | New | Skip `load_legacy_runs()` entirely; do not open the V5 database |
| Run identity by `run_report_uri` | New | Reuse an existing V3 `extraction_runs` row before creating a new run ID |
| Evidence fingerprint | New | Store and verify a fingerprint of `run_report.json`, `documents.csv`, `tb_rows.csv` |
| Stable single-run result | New contract | When invoked with one `--run-dir`, stdout is exactly one JSON object; no extra text |
| Source URL / PDF provenance | New | Persist `gazette_issues.source_url`, `source_documents.source_url`, and size metadata |
| `--runs-dir`, `--run-dir`, `--log`, `--v5-db` | Existing | Preserve behaviour when `--no-v5-lookup` is not supplied |

Isolation is proven by a test that makes the V5 database path unreadable or nonexistent and asserts that the V3 replay still succeeds without opening it.

---

## 4. CLI design

### 4.1 Target selection

Add:

```text
--target {v5,v3,ocr-only}
```

Default:

```text
--target v5
```

This preserves existing invocations and scripts.

### 4.2 V3 database option

Add:

```text
--v3-db PATH
```

Default for the V3 target (resolved against the project root):

```text
data/macau_balancete_v3.sqlite
```

This option is only valid with `--target v3` (including `--replay-only`).

### 4.3 New V3-only options

```text
--replay-only            Reuse an existing OCR run directory; do not download or OCR.
--reprocess              Allow repeated processing of the same PDF content.
--new-version            Allow the same issue key with different PDF content.
--reason TEXT            Required with --reprocess or --new-version; recorded in the manifest.
--issue-key YYYY-NN      Explicit canonical issue key; otherwise derived from URL/filename.
--allow-local-source     Allow a local PDF path for --target v3.
--allow-outside-output   Trusted CLI override for an --outdir outside the managed output area.
--json                   Print one machine-readable final result object to stdout.
```

`--reprocess` and `--new-version` are mutually exclusive. See §8 for the exact decision table.

`--no-v5-lookup` is a replay-tool option, not an importer option. The V3 importer always passes it and does not expose it to the user.

### 4.4 Full option compatibility matrix

`Conditional` means the option is accepted only when the stated condition holds; otherwise the importer must fail with a clear message, not silently ignore it.

| Existing/new option | V5 | V3 | OCR-only | V3 replay-only |
|---|---|---|---|---|
| positional `source` | URL or local path | HTTPS URL; local only with `--allow-local-source` | URL or local path | Not used; must be absent |
| `--target` | `v5` | `v3` | `ocr-only` | `v3` with `--replay-only` |
| `--db` | Supported; legacy DB only | Reject | Reject | Reject |
| `--v3-db` | Reject | Supported | Reject | Supported |
| `--input-dir` | Supported | Supported; must resolve under managed input | Supported | Reject |
| `--outdir` | Supported | Supported; unique and managed | Supported | Required; existing run directory |
| `--name` | Supported | Conditional; must not make issue identity unparseable | Supported | Reject |
| `--pages` | Supported | Supported for OCR stage | Supported | Reject |
| `--engine` | Supported | Supported for OCR stage | Supported | Reject |
| `--vision-level` | Supported | Supported for OCR stage | Supported | Reject |
| `--ocr-langs` | Supported | Supported for OCR stage | Supported | Reject |
| `--publish` | Preserve current semantics | Reject | Reject | Reject |
| `--force-publish` | Preserve current semantics | Reject | Reject | Reject |
| `--published-by` | Preserve current semantics | Reject | Reject | Reject |
| `--no-db` | Preserve current semantics | Reject; use `--target ocr-only` | Accepted as redundant alias | Reject |
| `--no-ocr` | Preserve current semantics | Reject; use `--replay-only` | Preserve current semantics; requires existing run artifacts | Reject |
| `--no-name-map` | Preserve current semantics | Reject; V5-only | Reject | Reject |
| `--no-bank-refresh` | Preserve current semantics | Reject; V5-only | Reject | Reject |
| `--inspect-only` | Preserve current semantics | Supported; no V3 writes | Supported | Reject |
| `--download-only` | Preserve current semantics | Supported; no OCR/replay | Supported | Reject |
| `--overwrite-pdf` | Preserve current semantics | Reject | Preserve current semantics | Reject |
| `--reuse-output` | Preserve current semantics | Reject; use `--replay-only` | Preserve current semantics | Reject |
| `--dry-run` | Preserve current semantics | Preflight + plan only; no download, OCR or V3 writes | No DB writes; plan only | Preflight + plan only |
| `--replay-only` | Reject | Supported | Reject | Supported (required) |
| `--reprocess` | Reject | Conditional; requires `--reason`; same PDF content only | Reject | Reject |
| `--new-version` | Reject | Conditional; requires `--reason`; same issue with different content only | Reject | Reject |
| `--reason` | Reject | Required with `--reprocess`/`--new-version` | Reject | Reject |
| `--issue-key` | Reject | Supported; must match `YYYY-NN` | Supported | Conditional; must match manifest/run |
| `--allow-local-source` | Reject (local is already supported) | Conditional; required for local PDF | Supported as no-op | Reject |
| `--allow-outside-output` | Reject | Conditional; trusted CLI only | Conditional | Conditional |
| `--json` | Supported | Supported | Supported | Supported |

Do not remove existing options just to simplify the new interface. If an option is not meaningful for a target, fail with a clear message.

Implementation notes:

- `--db` and `--v3-db` must use `default=None` in `argparse` and resolve their defaults per target. If `--db` keeps its current non-None default, the V3 target cannot distinguish "the user omitted `--db`" from "the user explicitly supplied the legacy database", and the reject rule becomes unenforceable.
- The positional `source` must become optional at the `argparse` level (`nargs="?"`) so that `--replay-only` can omit it. Enforce "source is required" for every other mode with an explicit preflight error.

### 4.5 Path resolution and V5 compatibility

Path behaviour is target-specific:

| Target | Default paths | Explicit relative paths |
|---|---|---|
| V5 | Preserve the current cwd-based behaviour exactly | Resolve against cwd |
| V3 | Resolve defaults against `ROOT` | Resolve against cwd; Workbench should pass absolute paths |
| OCR-only | Preserve V5-compatible cwd behaviour | Resolve against cwd |

Examples:

```text
V5 default:        <cwd>/data/macau_balancete.sqlite
V5 input default:  <cwd>/input
V3 default:        <ROOT>/data/macau_balancete_v3.sqlite
V3 input default:  <ROOT>/input
V3 run output:     <ROOT>/outputs/runs/...
```

Do not unify V5 path resolution with V3 in this upgrade. A V5 path change is a separate behaviour change and would violate the backward-compatibility rule.

The importer and replay subprocesses run with `cwd=ROOT`. This is existing importer behaviour and remains unchanged. A Workbench child process must pass absolute paths and must not rely on its own working directory for defaults.

### 4.6 Examples

V3 import:

```bash
python tools/import_issue.py \
  "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf" \
  --target v3 \
  --v3-db data/macau_balancete_v3.sqlite \
  --outdir "outputs/runs/issue33_2026__job_${JOB_ID}"
```

V3 replay-only retry after a replay failure:

```bash
python tools/import_issue.py \
  --target v3 \
  --v3-db data/macau_balancete_v3.sqlite \
  --replay-only \
  --outdir "outputs/runs/issue33_2026__job_${JOB_ID}"
```

V3 explicit new source version for the same issue:

```bash
python tools/import_issue.py \
  "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf" \
  --target v3 \
  --new-version \
  --reason "Gazette republished the PDF; content changed" \
  --outdir "outputs/runs/issue33_2026__job_${JOB_ID}"
```

Existing V5 import, unchanged:

```bash
python tools/import_issue.py "<URL>"
```

Explicit V5 target:

```bash
python tools/import_issue.py "<URL>" \
  --target v5 \
  --db data/macau_balancete.sqlite
```

OCR only:

```bash
python tools/import_issue.py "<URL>" --target ocr-only
```

Use `JOB_ID` (or `"$JOB_ID"`), never literal angle brackets, in copy-paste shell examples.

---

## 5. V3 target execution sequence

The V3 target executes these stages in order. A stage failure stops the pipeline and never starts a later stage.

### Stage 0 — Preflight validation

Before any network request or database mutation:

1. Parse the target and all options; reject incompatible combinations per §4.4.
2. Resolve paths according to §4.5.
3. Validate the V3 database:
   - it exists and opens read/write;
   - it is not under `data/baseline/`;
   - it contains the expected V3 tables.
4. Derive the canonical issue key:
   - accept `--issue-key` if supplied;
   - otherwise derive `YYYY-NN` from the URL filename or the local filename, using the known Gazette naming pattern (for example `anotariais-33-2026.pdf` → `2026-33`);
   - if the issue key cannot be derived, abort for V3. Do not create an `unknown_issue` run directory: the V3 `gazette_issues.issue_key` is `NOT NULL` and replay would fail.
5. Validate the output directory:
   - `--outdir` must not already exist for a normal import;
   - for `--replay-only`, it must already exist and contain the core artifacts;
   - it must resolve under `ROOT/outputs/runs` unless `--allow-outside-output` is explicitly supplied;
   - reject `..` traversal and symlink escape;
   - reject a symlink as the final output path.
6. Validate the source:
   - for a URL, apply §9.2 before any request;
   - for a local path, require `--allow-local-source`, reject non-files and symlink escape, and require a `.pdf` filename.
7. Validate `--name`: sanitized basename, `.pdf` suffix, and issue-key parseability.
8. For `--replay-only`, perform the integrity checks in §5.1.
9. `--dry-run`: print the complete planned command sequence and exit 0 without downloading, OCR or V3 writes.

No network request or database mutation occurs before preflight succeeds. Read-only V3 queries are allowed.

### 5.1 Replay-only integrity gate

`--replay-only` is a V3 retry mode for runs created by this importer. Before replay:

1. `import_manifest.json` must exist in the run directory, be valid JSON, and have `manifest_version: 2` and `target: v3`.
2. The manifest `issue_key` must equal the run-directory issue key or the explicit `--issue-key`.
3. `run_report.json` must exist and contain `source_pdf_sha256`.
4. The `run_report.json` hash must equal the manifest `pdf_sha256`.
5. The manifest `pdf_path` must exist, be a regular file, and its recomputed SHA-256 must equal the manifest `pdf_sha256`. If the original PDF is missing or has changed, stop and require the operator to restore the PDF or rerun OCR. Do not replay from unverified evidence.
6. `documents.csv` and `tb_rows.csv` must exist and be regular files.
7. `run_report.json`, `documents.csv`, `tb_rows.csv` and `import_manifest.json` must resolve inside the run directory; reject symlinks and `..` escape.
8. The `--v3-db` is authoritative. The manifest `v3_db` is informational; if it differs, record a warning in the manifest and log.

Legacy runs without a v2 manifest are not eligible for `import_issue.py --replay-only`. Use `tools/replay_runs_to_v3.py` directly for controlled legacy backfill; it reconstructs provenance per §6.8.

### Stage 1 — Download PDF

Reuse the current V5 download implementation only under the V5 target. The V3 target uses the hardened path described in §9.

Requirements:

- retain the current 250 MB maximum unless the project deliberately changes it;
- use bounded connect/read timeouts;
- stream the response to disk instead of loading an unbounded body into memory;
- verify the `%PDF-` file signature;
- compute SHA-256 while streaming;
- write to a job-specific temporary file and atomically rename only after all checks pass;
- preserve the original source URL in the manifest;
- never overwrite a previous PDF or run directory implicitly;
- if the destination filename already exists with the same bytes, reuse it and record `download_status = reused`;
- if the destination filename exists with different bytes, write the new PDF as `<stem>__<sha256[:8]>.pdf` for V3. The original file is preserved.

### Stage 1b — Content duplicate and version gate

This gate applies to a normal V3 import that will run OCR and replay. Skip it for `--download-only` and `--inspect-only`; those modes do not create V3 records and must keep the downloaded file.

After the download is complete and hashed, but **before OCR**, apply the decision table and precedence in §8.2–§8.3.

The short version:

1. If the same issue has documents but none with this hash, the source is a new version. `--reprocess` does not bypass this check. Require `--new-version --reason`.
2. If the same issue has a document with this hash, it is already imported. Return `already_imported`, or with `--reprocess --reason` reuse that source document and create a new extraction run.
3. If this issue has no documents but another issue has this hash, it is a cross-issue content conflict. Return `already_imported_content_conflict`, or with `--reprocess --reason` create a new source document under this issue.
4. If no document for this issue and no document with this hash exists, proceed normally.

Discard the temporary download when the result is `already_imported`, `already_imported_content_conflict`, or `new_source_version_required` and no explicit decision is supplied. Do not promote it to `input/`.

The duplicate result must be machine-readable and must not be reported as a financial verification or a new ingestion.

### Stage 2 — OCR

Reuse the existing OCR/parser command and output format.

- Run OCR using the current pipeline and default settings unless explicit supported OCR options are supplied.
- Capture stdout and stderr in the run-specific `import.log`.
- Store the exact command/options (excluding secrets) in the manifest.
- Stop immediately on non-zero exit status.
- Require these core artifacts before replay:
  - `run_report.json`;
  - `documents.csv`;
  - `tb_rows.csv` (a header-only or zero-row file is valid when the issue has no TB rows);
  - `run_report.source_pdf_sha256` must equal the manifest SHA-256;
  - `run_report.source_pdf` must point to the downloaded PDF or be recoverable from the manifest.
- Do not start V3 replay if OCR failed or a required artifact is missing/invalid.

### Stage 3 — Write the complete pre-replay manifest

Before calling V3 replay, write `import_manifest.json` in the run directory with the schema in §11. Use the atomic writer in §10.4.

Requirements:

- update the manifest at every stage boundary;
- include `manifest_version: 2`, `target`, `source_kind`, `source_url`, `source_filename`, `issue_key`, `pdf_path`, `pdf_sha256`, `pdf_bytes`, `run_dir`, `v3_db`, stage statuses and the replay fields;
- retain legacy fields described in §11.4 so existing artifact hashing does not break;
- if any stage fails, record the failed stage, exit status and a concise error summary before exiting.

### Stage 4 — Replay raw OCR evidence into V3

Invoke the existing replay tool as a subprocess using an argument array (never `shell=True`), with `cwd=ROOT`:

```bash
python tools/replay_runs_to_v3.py \
  --v3-db <v3-db-path> \
  --run-dir <run-dir> \
  --no-v5-lookup
```

Requirements:

- pass only the V3 database to `replay_runs_to_v3.py`;
- do not call `tools/build_database.py` in this target;
- do not read the V5 database; `--no-v5-lookup` is mandatory for the V3 target and must actually skip the legacy lookup;
- capture stdout/stderr and preserve the replay log;
- when invoked with one `--run-dir`, replay writes exactly one JSON object to stdout and no other text; parse and validate that object;
- treat a non-zero exit code or an invalid replay result as failure;
- validate the replay result fields defined in §6.3;
- verify the evidence fingerprint defined in §6.5;
- record the replay result/run identifier in the manifest;
- update the manifest to `completed` only after replay has succeeded and its result has been validated.

Do not parse human-readable log text and do not add a second, competing JSON mode. Stabilise the existing single-run JSON object.

### Stage 5 — Final result

On success, print a concise summary and exit code `0`, including:

- target;
- issue key;
- PDF filename and SHA-256;
- output directory;
- OCR result/page count where available;
- V3 database path;
- replay result/run ID;
- next step: page classification is still required.

With `--json`, stdout is exactly one JSON object per §10.3. All progress, child-process output and human-readable messages go to stderr and `import.log`.

A V3 import success must be described as **raw OCR evidence imported into V3**, not as a completed or reviewed financial statement.

---

## 6. Data boundary, identity and replay contract

### 6.1 What V3 import does and does not create

Newly replayed extracted rows are intentionally unassigned:

```text
row_disposition = 'unassigned'
statement_id = NULL
statement_extraction_id = NULL
statement_order = NULL
```

The V3 target in `import_issue.py` stops after raw evidence replay. It must **not**:

- decide which pages are financial statements;
- create or confirm bank/entity identities;
- create `statements` or `statement_extractions` from guesses;
- assign extracted rows to statements;
- approve Standard Item mappings;
- mark a statement reviewed;
- adopt an extraction;
- publish data.

The successful result is:

```text
V3 raw OCR replay completed — awaiting page classification
```

It is not yet eligible for the normal financial Review Queue.

### 6.2 Issue identity

The canonical V3 issue key is `YYYY-NN` (for example `2026-33`). It is stored in `gazette_issues.issue_key`.

The run-directory name remains `issueNN_YYYY__job_<unique-id>` for compatibility and display, but it is **not** the authoritative issue identity for new V3 imports.

Identity resolution order for replay:

1. explicit `--issue-key`, when supplied;
2. `import_manifest.json` `issue_key`, when present;
3. the existing run-directory name regex, for legacy runs only;
4. otherwise fail with a clear error.

Do not treat a filename or directory name as a unique source identity.

### 6.3 Replay result contract

`replay_runs_to_v3.py` must return a stable result with at least:

```json
{
  "status": "ok",
  "run_id": "...",
  "run_dir": "...",
  "issue_key": "2026-33",
  "gazette_issue_id": "...",
  "source_document_id": "...",
  "pages": 11,
  "page_extractions": 11,
  "rows_inserted": 64,
  "rows_skipped": 0,
  "already_present": false,
  "evidence_fingerprint": "..."
}
```

Status values: `ok`, `already_present`, `failed`.

Rules:

- `already_present = true` and `rows_inserted = 0` means the same run identity with the same evidence fingerprint was already replayed; this is a successful idempotent result, not a failure.
- `status = failed` includes `error_type` and `error_message`.
- Exit code 0 for `ok` and `already_present`; 1 for `failed`; 2 for usage errors.
- The importer must not infer success from `rows_inserted = 0` alone; it must use `status` and `already_present`.

### 6.4 Run identity resolution in replay

Before creating a new extraction run, replay resolves the run identity in this order:

1. an existing `extraction_runs` row whose `run_report_uri` equals `<run_dir>/run_report.json`;
2. the legacy V5 `runs` table, only when V5 lookup is enabled;
3. a deterministic `stable_id` fallback.

This order is mandatory because new V3 run directories have a `__job_<id>` suffix and do not match legacy V5 run-directory names. Without step 1, `--replay-only --no-v5-lookup` on an existing V3 run would create a new run ID and duplicate extracted rows.

### 6.5 Evidence fingerprint

The current replay guard only compares row counts. To prevent a same-count change from being silently skipped, replay must compute and store an evidence fingerprint.

Version 1 algorithm:

```text
parts = ["import-evidence-v1"]
for name in ("run_report.json", "documents.csv", "tb_rows.csv"):
    if file exists and is a regular file:
        parts.append(name + "\0" + sha256_hex(file bytes))
    else:
        parts.append(name + "\0MISSING")
evidence_fingerprint = sha256_hex("\0".join(parts).encode("utf-8"))
```

`sha256_hex` returns lowercase hexadecimal. Rules:

- Store `evidence_fingerprint` and `evidence_fingerprint_version: 1` in `extraction_runs.config_json`, preserving existing keys.
- On a new run, compute and store the fingerprint when the run row is created.
- On an existing run:
  - if the stored fingerprint exists and matches: proceed idempotently;
  - if the stored fingerprint exists and differs: raise a distinct `EvidenceChangedError`, roll back the transaction, exit non-zero, and leave a failed manifest; never skip silently;
  - if no stored fingerprint exists (legacy run): compute and store it on the first replay, then proceed. Do not refuse a legacy run solely because the key was added later.
- If `config_json` is invalid JSON, fail rather than overwrite it.
- A changed row count still fails through the existing page-count check; the fingerprint additionally catches same-count changes.
- `import_manifest.json` is not part of the fingerprint. Updating the manifest does not make a run replay-different.
- A genuinely new OCR run must use a new run directory and therefore a new run ID; it must not rewrite the evidence of an existing run.

### 6.6 Source provenance

The original Gazette URL must not be lost.

| Provenance item | V3 storage | Rule |
|---|---|---|
| Issue key | `gazette_issues.issue_key` | `YYYY-NN`; unique |
| Issue-level source URL | `gazette_issues.source_url` | Set at insert; update only when currently `NULL` (`COALESCE`); never overwrite a non-NULL value |
| Document-level source URL | `source_documents.source_url` | Exact PDF URL; set at insert; backfill only when `NULL`; never overwrite |
| PDF SHA-256 | `source_documents.sha256` | Full 64-hex hash; content identity |
| PDF byte size | `extraction_runs.config_json.source_pdf_bytes` and manifest `pdf_bytes` | No V3 schema change in V2.0; SQL-level size queries are a documented gap |
| PDF storage path | `source_documents.storage_uri` | Absolute local path; not a network URI |
| Original filename | `source_documents.original_filename` | Sanitized basename |
| Download time | `source_documents.acquired_at` | Manifest `download_finished_at` when present; otherwise replay time for legacy runs |
| OCR run | `extraction_runs.run_id` | Resolved per §6.4 |
| OCR report | `extraction_runs.run_report_uri` | Absolute path to `run_report.json` |
| Import manifest location | `extraction_runs.config_json.import_manifest` | Absolute path or `null` for reconstructed legacy runs |
| Manifest origin | `extraction_runs.config_json.manifest_origin` | `written` or `reconstructed` |
| Run directory | `extraction_runs.config_json.run_dir` | Absolute path; helps future retries |
| Evidence fingerprint | `extraction_runs.config_json.evidence_fingerprint` | Versioned per §6.5 |

Do not put secrets or URL query values in any of these fields.

### 6.7 V2.0 does not add a file-size column

The importer does not modify the V3 schema in V2.0. `pdf_bytes` is preserved in:

- `import_manifest.json` (`pdf_bytes`);
- `run_report.json` (`source_pdf_size`);
- `extraction_runs.config_json.source_pdf_bytes`.

If a future feature needs SQL-level size filtering or reporting, add a dedicated migration in a separate change after reviewing `DATABASE.md`. Do not add an ad-hoc column during this upgrade.

### 6.8 Legacy manifest compatibility

- The replay tool must accept the legacy manifest key `"source"` as the source URL.
- A missing `import_manifest.json` must not fail replay when the core artifacts exist; reconstruct provenance from `run_report.json` and mark `manifest_origin = reconstructed`.
- Do not write a fabricated `import_manifest.json` into an old run directory. Store reconstruction metadata in V3 only.
- For the 6 existing runs without a manifest, leave `source_url` `NULL`. The known Gazette URL pattern is not evidence and must not be guessed.
- For the 8 existing runs with a legacy manifest, `source` supplies the URL during a deliberate backfill replay.
- `source_pdf_size` from every existing `run_report.json` can backfill `config_json.source_pdf_bytes` for all 14 existing documents.

### 6.9 Currency and unit rule

For in-scope Macau bank statements, the project rule remains:

```text
currency = MOP
unit_scale = 1
```

The Importer must not use this rule to infer or approve statement identity. Currency/unit metadata is applied in the later statement-confirmation workflow with provenance recorded as a project/domain rule, not as per-PDF manual verification.

---

## 7. Output directory and run identity

### 7.1 Run directory

For V3 imports, use a unique run directory. Keep the `issueNN_YYYY` prefix for compatibility with replay and display.

Recommended pattern:

```text
outputs/runs/issueNN_YYYY__job_<unique-id>
```

Requirements:

- never overwrite an existing output directory by default;
- do not derive a filesystem path directly from an untrusted URL;
- sanitize the filename/stem;
- use a generated unique ID for each new OCR run;
- allow an explicit, validated `--outdir` for trusted CLI/Workbench use;
- reject an `--outdir` outside `outputs/runs` unless `--allow-outside-output` is supplied;
- preserve failed run directories for diagnosis.

### 7.2 Run identity

Use the manifest `issue_key` and the replay result `run_id` as the logical identity. Do not use the directory name or PDF filename as a unique source identity.

For a replay-only retry, reuse the exact completed OCR run directory and do not repeat OCR.

For a reprocess, use a new run directory and preserve the previous run.

### 7.3 Failure and result directories

Early failures and operator-decision results may occur before the run directory exists. Write those manifests to unique paths:

```text
ROOT/output_import_failed/<job-id>/import_manifest.json   # stage failure
ROOT/output_import_results/<job-id>/import_manifest.json  # already_imported / conflict / decision required / import_locked
```

Do not reuse a single shared `output_import_failed/import_manifest.json`, because concurrent or consecutive failures would overwrite each other.

For `already_imported`, operator-decision and `import_locked` results, do not create the requested run directory. This keeps the `--outdir` namespace free for a later explicit `--reprocess` or `--new-version` run. If `--outdir` was supplied, include it in the result manifest but do not create it.

### 7.4 Provenance backfill for existing runs

After the replay-tool changes are deployed, a deliberate replay of the existing 14 run directories can backfill:

- `gazette_issues.source_url` from the 8 legacy manifest `source` values;
- `source_documents.source_url` from the same values;
- `extraction_runs.config_json.source_pdf_bytes` from every `run_report.json`;
- `extraction_runs.config_json.evidence_fingerprint` and `manifest_origin`.

The backfill must insert no new rows and must not overwrite non-NULL values. The 6 runs without a manifest keep `source_url = NULL`. Run the backfill while no importer is active; the direct replay tool does not take the importer's advisory lock.

---

## 8. Duplicate handling, idempotency and retries

Duplicate handling distinguishes content identity from Gazette issue identity.

### 8.0 Operator decisions

`--reprocess` and `--new-version` are mutually exclusive. Both require a non-empty `--reason`, which is recorded in the manifest as `decision_reason` and in the replay log.

- `--reprocess` only authorises repeated processing of the **same PDF content**. It never authorises a different PDF under the same issue.
- `--new-version` only authorises **different PDF content for the same issue**. It never authorises a second run for identical content.
- Neither flag may be used to overwrite an existing PDF, source document, run or extracted row.

### 8.1 Content identity

Content identity is the full PDF SHA-256. Lookup uses `source_documents.sha256` and the existing `idx_source_documents_sha256` index. Do not use the truncated `document_key` for duplicate detection.

Multiple `source_documents` rows with the same hash are possible only when:

- an operator explicitly uses `--reprocess` to import the same PDF under a **different issue**; or
- a legacy/manual import already created such rows.

They are not created by a same-issue reprocess. A same-issue reprocess reuses the existing source document.

Duplicate resolution returns all matching rows, prefers a row with the same issue key when reporting `already_imported`, and never deletes or merges existing rows.

### 8.2 Decision table

| Existing state for the target issue | Hash relation | Default result | Explicit approval |
|---|---|---|---|
| Same issue has a document with this hash | same | `already_imported` | `--reprocess --reason`: reuse the existing `source_documents` row, create a new extraction run |
| No document for this issue; same hash exists under another issue | same | `already_imported_content_conflict` | `--reprocess --reason`: create a new `source_documents` row under this issue, create a new run |
| Same issue has document(s), none with this hash | different | `new_source_version_required` | `--new-version --reason`: create a new `source_documents` row, preserve the old version |
| No document for this issue and no document with this hash anywhere | none | normal import | not applicable |

Cross-issue `--reprocess` is the only legitimate way to create a second `source_documents` row with the same hash. A same-issue `--reprocess` must not increase the `source_documents` count.

### 8.3 Algorithm and precedence

Apply these checks in order. The same-issue different-hash check has precedence over cross-issue content matching.

1. Compute the full SHA-256.
2. Query all `source_documents` rows with this hash.
3. Query all `source_documents` rows for the target issue.
4. If the target issue has at least one document and none has this hash:
   - this is a new source version;
   - `--reprocess` does **not** bypass this check;
   - without `--new-version`: return `new_source_version_required` with exit code 3;
   - with `--new-version --reason`: proceed and create a new source document; preserve the old version.
5. If a document with this hash exists for the target issue:
   - without `--reprocess`: return `already_imported` with exit code 0;
   - with `--reprocess --reason`: reuse that source document and create a new extraction run;
   - with `--new-version`: reject, because the content is identical; tell the operator to use `--reprocess`.
6. If this issue has no documents, but another issue has this hash:
   - without `--reprocess`: return `already_imported_content_conflict` with exit code 3;
   - with `--reprocess --reason`: create a new source document under the target issue and proceed;
   - with `--new-version`: reject, because this is not a same-issue version change; tell the operator to use `--reprocess`.
7. If no document for this issue and no document with this hash exists: proceed with a normal import.
8. Record the decision in the manifest (`decision_type`, `decision_reason`) and in the replay log.

### 8.4 Same PDF, same issue

- Return `already_imported` with the existing issue/document/run references.
- Do not create a second logical source document or replay record.
- Do not run OCR.
- Exit code 0 unless an explicit reprocess is requested.
- With `--reprocess --reason`, reuse the existing `source_documents` row and create a new extraction run in a new run directory. The source-document count must not change.

### 8.5 Same PDF, different issue

- Return `already_imported_content_conflict`.
- Do not silently create a second source document.
- Require `--reprocess --reason` for an intentional cross-issue import.
- Exit code 3 when no explicit decision is supplied.
- The manifest records the conflicting existing issue key and document ID.
- With `--reprocess --reason`, create a new `source_documents` row under the target issue and proceed. This is the only expected same-hash multi-row case.

### 8.6 Same issue, different PDF

Treat this as a new source version:

- preserve the old PDF, source document and run;
- do not overwrite an existing Gazette issue or document silently;
- report `new_source_version_required`;
- require `--new-version --reason`;
- create a new `source_documents` row with its own `document_key`, hash and storage path;
- exit code 3 when no explicit decision is supplied;
- `--reprocess` is not a valid substitute.

V3 can represent multiple documents under one issue because `source_documents.document_key` is unique per issue+content. V3 has no "current version" flag; the Importer must not pick one. Page classification and statement confirmation must choose the document to review.

### 8.7 Replay idempotency

Keep `tools/replay_runs_to_v3.py` idempotent for the same run identity and the same evidence:

- replaying the same completed run must not create duplicate pages, page extractions or extracted rows;
- `already_present = true` with `rows_inserted = 0` is a successful result;
- a changed row count for an existing run must fail;
- a same-count change to any label, amount, status or other evidence field must fail through the evidence fingerprint;
- a changed row set for an existing run must fail instead of overwriting raw evidence;
- a genuinely new OCR run must use a new run directory and a new run ID.

A new run directory containing the same PDF is not automatically the same run identity. Duplicate-content handling in §8.2–§8.6 must run before a second logical ingestion is created.

### 8.8 Retry stages

| Failure point | Correct retry behaviour |
|---|---|
| Preflight failed | Fix the flag, path, URL or database; no run artifacts exist |
| Import lock held | Wait for the other import to finish; retry |
| Download failed | Retry download; no OCR/V3 replay exists |
| Duplicate gate returned `already_imported` | No retry; use `--reprocess` only with a justified reason |
| Duplicate gate requires a version decision | Re-run with `--new-version --reason` or stop |
| OCR failed | Retry OCR into a new unique run directory; preserve the failed run |
| OCR completed, V3 replay failed | Retry with `--replay-only --outdir <existing run dir>`; do not rerun OCR |
| V3 replay completed | Return `already_imported` unless explicit `--reprocess` is requested |

The Workbench job table and UI are designed later. The CLI importer must expose enough stage/result information for a future worker to implement these retry rules.

---

## 9. URL, download, filesystem, process and concurrency safety

### 9.1 Scope

- The hardened URL policy applies to `--target v3` URL sources.
- The V5 target preserves its current download behaviour, including `http://`, local paths and the existing `127.0.0.1` test fixture.
- The OCR-only target preserves V5 download behaviour; it is not the Workbench-facing ingestion path.
- Do not harden the shared `fetch_pdf()` in a way that breaks V5 tests. Use a target-aware policy object or a separate V3 download function.

### 9.2 URL policy for V3

- HTTPS only.
- Use an explicit, configurable allowlist of official Gazette hostnames. The verified host in this project is `images.bo.dsaj.gov.mo`. Do not add wildcard subdomains without checking real official URLs.
- Reject URL user-info (`username:password@host`).
- Reject IP-literal URLs.
- Reject URL query strings and fragments for V3. Official Gazette PDF URLs do not require them; rejecting them avoids leaking credentials or signed parameters into the manifest.
- Resolve the hostname and reject private, loopback, link-local, multicast and reserved destinations.
- Revalidate every redirect target using the same rules.
- Limit redirects (for example, 5).
- Do not forward credentials or sensitive headers across redirects.
- Do not trust `Content-Type` alone to establish that a response is a PDF.

`urllib.request.urlopen` follows redirects automatically. Implement an explicit `HTTPRedirectHandler` that validates each `newurl`; do not rely on post-hoc checks of only the final URL.

DNS rebinding is a residual risk for a local trusted tool. At minimum, re-resolve the host at connection time and reject if any resolved address violates the policy. Document the residual risk rather than claiming full SSRF protection.

### 9.3 Download limits

- Keep the 250 MB maximum.
- Enforce the limit while streaming, even when `Content-Length` is absent or false.
- Use bounded connect/read timeouts.
- Use a job-specific temporary filename (for example, `.<name>.<uuid>.part`).
- Delete the incomplete temporary file on failure, or retain it only under a clearly marked temporary name.
- Verify the final file signature and compute SHA-256 before promoting the file to its final path.
- Promote atomically with `os.replace`.
- If the destination exists with the same hash, reuse it; if it exists with different content, use a new versioned filename for V3.

### 9.4 Filesystem safety

- Store downloaded PDFs only under the configured managed input directory (`ROOT/input` by default for V3).
- Store run output only under the configured managed output directory (`ROOT/outputs/runs` by default for V3).
- Resolve and validate paths to prevent `..` traversal and symlink escape.
- Reject a symlink as the final destination path.
- Never use the URL path as a raw local path.
- Use generated job/run identifiers for folder names.
- `--allow-outside-output` is a trusted CLI override; it must be documented and must not be exposed to untrusted callers.

### 9.5 Local source files for V3

Workbench may later upload a PDF instead of supplying a URL. Define the behaviour now:

- `--target v3` accepts a local PDF only with `--allow-local-source`.
- The file must exist, be a regular file, and have a `.pdf` suffix.
- Reject symlink escape outside the allowed input area.
- Copy it into the managed input directory unless it is already there.
- Record `source_kind = local_file`, `source_url = null`, `original_filename`, SHA-256 and file size.
- Replay and provenance rules are otherwise identical.

### 9.6 Process safety

- Use `subprocess.run`/`Popen` with an argument list.
- Never use `shell=True` or interpolate untrusted URL/path values into a shell command.
- Set `cwd=ROOT` and a controlled environment.
- Capture stdout and stderr separately or in a predictable combined log.
- Record exit codes and stage names.
- Pass `--no-v5-lookup` to replay for the V3 target.

### 9.7 Import lock and concurrency

Two simultaneous CLI imports must not both pass the duplicate gate and create two OCR runs for the same content.

Implement a V3 database import lock:

- acquire an exclusive advisory lock with `fcntl.flock(LOCK_EX | LOCK_NB)` on `<v3-db>.import.lock`;
- acquire it after preflight and before download; hold it through duplicate checking, OCR, replay and final manifest writing;
- release it in a `finally` block on every exit path;
- if the lock is already held, return `status = import_locked` and exit code 3 without downloading or OCR-ing;
- write an `import_locked` result manifest under `output_import_results/<job-id>/`;
- keep the lock file next to the V3 database; it is not part of the schema and may remain on disk after release.

The lock is advisory. It protects cooperating importer processes, not arbitrary external writers. Workbench should still run one import at a time.

This is a POSIX/macOS design. If the project later supports Windows, replace `fcntl` with a cross-platform lock library rather than silently weakening the guarantee.

---

## 10. Failure handling, exit codes and result contract

### 10.1 Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success, `already_imported`, `download_only`, `inspect_only`, or `planned` |
| 1 | A required stage failed (download, OCR, replay, fingerprint, manifest write) |
| 2 | Usage or preflight error |
| 3 | Operator decision required: content conflict, new version required, or import lock held |

### 10.2 Manifest stage statuses

Use explicit stage statuses:

```json
{
  "download_status": "completed",
  "ocr_status": "completed",
  "v3_replay_status": "failed",
  "current_stage": "v3_replay",
  "last_exit_code": 1,
  "error_type": "V3ReplayError",
  "error_message": "..."
}
```

Requirements:

- Update the manifest at every stage boundary.
- Preserve logs and intermediate outputs on failure.
- Return a non-zero exit code on any failed required stage.
- Do not report overall success when V3 replay failed.
- Do not delete the downloaded PDF or OCR output after a replay failure.
- Keep error text useful but avoid exposing credentials, URL query values or other secrets.
- Make it possible to identify whether a run is safe to retry at download, OCR or replay stage.
- Write failed and decision manifests to the unique per-job locations in §7.3.

Do not claim durable background-job support at this stage. The CLI is a synchronous command-line orchestrator; Workbench wraps it in its own persistent worker/job mechanism later.

### 10.3 `--json` result contract

`--json` is a strict output mode:

- stdout contains exactly one JSON object and nothing else;
- all progress, OCR output, replay output and human-readable errors go to stderr and `import.log`;
- the same contract applies to success, stage failure, duplicate, decision-required, lock-held and preflight errors detected after argument parsing;
- argparse-level usage errors before `--json` is parsed may use the normal stderr usage message and exit code 2;
- `--help` is exempt and prints normal help;
- `manifest_path` is `null` only when preflight fails before a result directory can be created; `issue_key` is `null` when it cannot be derived.

Recommended object:

```json
{
  "status": "completed",
  "exit_code": 0,
  "target": "v3",
  "issue_key": "2026-33",
  "run_dir": "/abs/outputs/runs/issue33_2026__job_abc123",
  "manifest_path": "/abs/outputs/runs/issue33_2026__job_abc123/import_manifest.json",
  "pdf_sha256": "2d51dd30a19e8383f63a836dbe6a57efc8becb1b49378bd229e4be44f6d1c627",
  "v3": {
    "gazette_issue_id": "...",
    "source_document_id": "...",
    "run_id": "...",
    "already_present": false,
    "evidence_fingerprint": "..."
  },
  "error": null
}
```

`status` values:

```text
completed
failed
already_imported
already_imported_content_conflict
new_source_version_required
import_locked
usage_error
planned
download_only
inspect_only
```

On failure, `error` is `{"type": "...", "message": "..."}` and `exit_code` is non-zero.

Implementation note: `run_command()` currently prints command lines and merges child stderr into stdout. In `--json` mode it must route those messages to stderr and `import.log`; stdout is reserved for the final object.

### 10.4 Atomic manifest writing

`write_manifest()` must never truncate a live manifest. Implement:

1. serialise the complete manifest to a temporary file in the same directory;
2. `flush()` and `os.fsync()`;
3. `os.replace(temp, final)`;
4. optionally fsync the containing directory.

Use a unique temp name such as `.<name>.<pid>.<uuid>.tmp`. Remove the temp file on failure. A process crash must leave either the previous valid manifest or the new complete manifest, never a half-written JSON file.

---

## 11. Import manifest schema V2.0

### 11.1 Canonical example

```json
{
  "manifest_version": 2,
  "manifest_origin": "written",
  "target": "v3",
  "source_kind": "url",
  "source_url": "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf",
  "source_filename": "anotariais-33-2026.pdf",
  "issue_key": "2026-33",
  "pdf_path": "/abs/input/anotariais-33-2026.pdf",
  "pdf_sha256": "2d51dd30a19e8383f63a836dbe6a57efc8becb1b49378bd229e4be44f6d1c627",
  "pdf_bytes": 3232716,
  "run_dir": "/abs/outputs/runs/issue33_2026__job_abc123",
  "v3_db": "/abs/data/macau_balancete_v3.sqlite",
  "status": "running",
  "current_stage": "download",
  "download_status": "pending",
  "ocr_status": "pending",
  "v3_replay_status": "pending",
  "started_at": "2026-11-21T00:00:00+00:00",
  "download_finished_at": null,
  "finished_at": null,
  "decision_type": null,
  "decision_reason": null,
  "last_exit_code": null,
  "error_type": null,
  "error_message": null,
  "v3": {
    "gazette_issue_id": null,
    "source_document_id": null,
    "run_id": null,
    "already_present": false,
    "evidence_fingerprint": null,
    "pages": null,
    "page_extractions": null,
    "rows_inserted": null,
    "rows_skipped": null
  },
  "steps": {}
}
```

### 11.2 Status values

Overall `status`:

```text
running
completed
failed
already_imported
already_imported_content_conflict
new_source_version_required
import_locked
download_only
inspect_only
replay_only
```

Stage statuses:

```text
pending
completed
reused
skipped
failed
not_applicable
```

### 11.3 Invariants

- `manifest_version` is `2`.
- `issue_key` is canonical `YYYY-NN`.
- `run_dir` is the actual absolute run directory, not a relative shell fragment.
- `v3_replay_status` is `completed` only after a validated replay result.
- `status = completed` for V3 means raw evidence only; it does not mean financially reviewed.
- `already_present = true` with `rows_inserted = 0` is a successful idempotent replay.
- `pdf_sha256` is the full 64-character hash.
- `decision_reason` is non-empty whenever `--reprocess` or `--new-version` was used.
- `decision_type` is `reprocess` or `new_version` when applicable.
- No secrets or URL query values.

### 11.4 Legacy aliases

Preserve these fields for compatibility with existing tooling and the 8 legacy manifests:

| Legacy key | v2 equivalent | Rule |
|---|---|---|
| `source` | `source_url` | Mirror the URL; accept `source` when reading old manifests |
| `pdf` | `pdf_path` | Mirror the path |
| `outdir` | `run_dir` | Mirror the directory |
| `db` | V5 database path | `null` for V3 |
| `error` | `error_message` | Mirror the message |
| `steps` | Per-command history | Retain for V5 and audit compatibility; new readers use stage fields |
| `started_at` | `started_at` | Retain |
| `finished_at` | `finished_at` | Retain |

Do not require existing consumers to understand v2 fields, and do not remove the legacy keys that `tools/audit_v5.py` hashes.

---

## 12. Testing requirements

### 12.1 V5 backward compatibility

- Existing `tests/test_import_issue.py` must continue passing unchanged.
- The `http://127.0.0.1` download fixture must still work for V5.
- Existing V5 CLI defaults and option behaviours must remain unchanged.
- V5 target still invokes the existing V5 database pipeline with the legacy DB only.
- V5 path resolution remains cwd-based: a test must invoke the V5 path helper from a temporary cwd and assert the default DB/input paths remain cwd-relative.
- Existing `--no-db`, `--download-only`, `--no-ocr`, `--inspect-only`, `--dry-run`, `--overwrite-pdf` and `--reuse-output` behaviours must be preserved or have explicitly tested compatibility mappings.
- `--target` omitted behaves exactly like `--target v5`.
- OCR-only preserves V5-compatible path behaviour.

### 12.2 V3 target and isolation

- V3 target downloads and OCRs, then invokes `replay_runs_to_v3.py` with `--v3-db`, `--run-dir`, `--no-v5-lookup`.
- V3 target never invokes `tools/build_database.py`, `name_registry.py refresh-banks`, or `import_user_standard.py map`.
- V3 target rejects incompatible `--db`, `--publish`, `--force-publish`, `--published-by`, `--no-name-map` and `--no-bank-refresh`.
- `--no-db` with V3 is rejected with a message pointing to `--target ocr-only`.
- `--no-ocr` with V3 is rejected with a message pointing to `--replay-only`.
- `--replay-only` does not download or OCR and reuses the existing run directory.
- V3 replay is not called when download/OCR fails or a required artifact is missing.
- V3 replay failure returns non-zero and leaves an accurate failed manifest.
- Successful V3 target writes manifest status only after replay is confirmed.
- Successful result clearly states that raw OCR evidence is imported and page classification remains outstanding.
- Isolation proof: point `--v5-db` at a nonexistent or unreadable path and assert replay still succeeds without opening it.
- Exit codes 0/1/2/3 are covered.
- V3 default paths resolve against `ROOT`, including when the process is launched from another cwd.

### 12.3 Replay-only integrity

- Missing `import_manifest.json` is rejected.
- Wrong manifest `target` is rejected.
- Manifest `issue_key` mismatch is rejected.
- `run_report.source_pdf_sha256` mismatch with manifest is rejected.
- Modified or missing PDF is rejected.
- Missing `documents.csv` or `tb_rows.csv` is rejected.
- Artifact symlink or `..` escape is rejected.
- Valid replay-only run succeeds and is idempotent.

### 12.4 Manifest and provenance

- A new V3 import writes `manifest_version: 2`, canonical `issue_key`, source URL, SHA-256 and byte size.
- The source URL is written into `gazette_issues.source_url` and `source_documents.source_url`.
- PDF SHA-256 is recorded in `source_documents.sha256`.
- PDF byte size is recorded in `import_manifest.json` and `extraction_runs.config_json.source_pdf_bytes`; no V3 schema change is required.
- Legacy manifests using `"source"` are read correctly.
- Missing manifests do not fail replay; `manifest_origin = reconstructed`.
- Existing non-NULL `source_url` is never overwritten.
- Existing NULL `source_url` is backfilled when provenance is available.
- Replaying the existing 14 runs inserts no new rows and fills available provenance.
- Issue key, source document, OCR run and replay run ID are traceable to each other.
- Existing issue/document/run identity is reused for replay-only retries.
- Atomic manifest writing leaves the previous manifest intact when `os.replace` fails.

### 12.5 Duplicate and retry decisions

- Same PDF hash, same issue returns `already_imported` and does not create a second ingestion.
- Same PDF hash, same issue with `--reprocess` reuses the existing source document and creates a new extraction run; `source_documents` count is unchanged.
- Same PDF hash, different issue returns `already_imported_content_conflict` and exit code 3.
- Same PDF hash, different issue with `--reprocess` creates a second source document under the target issue.
- Same issue key, different hash returns `new_source_version_required` and exit code 3.
- Same issue key, different hash with `--new-version` creates a second source document and preserves the first.
- Same issue key, different hash with `--reprocess` still returns `new_source_version_required`; reprocess must not bypass the version check.
- Same PDF hash with `--new-version` is rejected with guidance to use `--reprocess`.
- Different issue, same hash with `--new-version` is rejected with guidance to use `--reprocess`.
- `--reprocess` and `--new-version` together are rejected.
- Missing or empty `--reason` is rejected.
- OCR retry receives a new run directory.
- Replay-only retry reuses the existing OCR run directory and does not duplicate rows.
- Replaying the exact same run is idempotent.
- A changed row set for an existing run fails instead of overwriting.

### 12.6 Evidence fingerprint

- Replaying unchanged artifacts returns `already_present` and inserts zero rows.
- Changing one amount while keeping the same row count fails with `EvidenceChangedError`.
- Changing a label or parse status while keeping the same row count fails.
- Changing only `import_manifest.json` does not fail the fingerprint check.
- A legacy run with no stored fingerprint gets one on first replay.
- A run with a stored fingerprint and changed evidence fails; the transaction rolls back.
- `config_json` existing keys are preserved when the fingerprint is added.

### 12.7 Security

- HTTP URL rejected for V3.
- Non-allowlisted hostname rejected.
- URL user-info rejected.
- URL query string and fragment rejected for V3.
- Redirect to non-allowlisted host rejected.
- Redirect to localhost/private/link-local address rejected.
- Non-PDF content rejected.
- Oversized body rejected even without a trustworthy `Content-Length`.
- Incomplete download is not promoted to the final PDF path.
- Path traversal and symlink escape rejected.
- `--outdir` outside the managed area rejected without `--allow-outside-output`.
- Local source rejected without `--allow-local-source`.
- Subprocess arguments are passed as an array; no shell invocation is used.
- V5 `http://127.0.0.1` test remains green.

### 12.8 Concurrency

- A second V3 import fails with `import_locked` and exit code 3 while the first holds the lock.
- The lock is released when the first process exits successfully or fails.
- `--replay-only` also respects the lock.
- V5 and OCR-only imports do not take the V3 lock.

### 12.9 JSON output

- With `--json`, stdout contains exactly one JSON object.
- No `PDF:`, `$ command`, OCR progress or replay progress text appears on stdout.
- Successful, failed, duplicate, decision-required, lock-held and preflight-error paths all emit the JSON contract.
- `manifest_path` is present and points to the actual manifest, including failure/result manifests.

### 12.10 Regression isolation

Use a copy of the operational V3 database and assert that importing one new issue does not alter:

- existing `statements`;
- existing `statement_rows`;
- existing `statement_reviews`;
- existing `statement_review_bindings`;
- existing `statement_row_mappings`;
- existing adopted extractions;
- existing published records;
- existing `extracted_rows` from prior runs.

Record the baseline counts listed in §2.1 before and after the test.

---

## 13. Implementation sequence

Implement in this order. Do not start with Workbench UI.

### Phase 0 — Contract verification

1. Re-read the current `tools/import_issue.py`, its tests and every CLI caller.
2. Inventory all existing options and the exact V5 stage order.
3. Confirm the V5 cwd-based path behaviour and record it as a compatibility contract.
4. Inspect the current V3 DDL/migrations and `tools/replay_runs_to_v3.py` result contract.
5. Confirm official Gazette PDF hostnames from real project source URLs.
6. Confirm the facts in §2 against the current database and run directories.
7. Decide explicitly that V2.0 does not change the V3 schema; document the `config_json` provenance fallback.
8. Identify every place where source URL, hash and file size can be stored without violating the V3 data model.

**Deliverable:** a short implementation note in the PR/commit describing compatibility and V3 provenance decisions.

### Phase 1 — V5-safe refactor

1. Refactor download, OCR, V5 import and manifest writing into testable functions without changing their behaviour.
2. Add target parsing with `v5` as the default.
3. Add a target-aware download policy; keep V5 behaviour intact.
4. Add consistent stage results, exit codes and manifest updates.
5. Keep V5 path resolution cwd-based; do not unify it with V3.
6. Put manifest writes behind a single writer function so Phase 3 can make it atomic.

**Deliverable:** refactored importer with no functional regression in V5 mode.

### Phase 2 — V3 raw ingestion

1. Add `--target v3`, `--v3-db`, `--replay-only`, `--reprocess`, `--new-version`, `--reason`, `--issue-key`, `--allow-local-source`, `--allow-outside-output` and `--json`.
2. Run download + OCR without the V5 DB import steps.
3. Add the content duplicate and version gate per §8.2–§8.3.
4. Write manifest schema v2 with legacy aliases.
5. Update replay to accept `--v3-db` and implement `--no-v5-lookup` for real.
6. Add the run-identity lookup by `run_report_uri` in replay.
7. Stabilise the single-run replay JSON result.
8. Persist source URL, SHA-256 and size metadata in V3 without a schema change.
9. Add replay-only retry support with §5.1 integrity checks.
10. Add a deliberate provenance backfill replay for existing runs.

**Deliverable:** one CLI command can safely import a Gazette issue's raw OCR evidence into V3, and a replay-only retry can repair a failed manifest without duplicating data.

**Gate:** until Phase 3 is complete, the V3 target is for controlled single-operator testing only. Do not expose it to Workbench.

### Phase 3 — Replay integrity, security and concurrency

1. Add the evidence fingerprint and same-count change protection.
2. Add the official-host allowlist and redirect validation.
3. Enforce HTTPS, destination-address checks, bounded streaming and PDF validation.
4. Enforce managed output paths and atomic file promotion.
5. Make manifest writes atomic.
6. Add the V3 import lock.
7. Add security, fingerprint, atomic-write and lock tests.
8. Keep V5 download tests green.

**Deliverable:** safe V3 ingestion suitable for a local Workbench to call, with evidence-integrity and concurrency guarantees.

### Phase 4 — Real Gazette integration test

Use one selected real Gazette issue that has not already been ingested, against an isolated copy of the V3 database. The operational database already contains 14 issues; do not test against it directly.

Verify:

1. PDF is downloaded intact and the hash matches.
2. OCR output files are complete.
3. V3 replay completes.
4. Source URL and PDF hash are traceable in V3.
5. Pages and extracted rows appear in V3 as raw/unassigned evidence.
6. No financial statements are automatically confirmed or sent to the Review Queue.
7. Running the same command again follows the duplicate policy and does not silently duplicate or overwrite data.
8. Replay-only retry does not duplicate rows.
9. Existing statements, reviews, mappings and publications in the copy are unchanged.
10. A second concurrent import receives `import_locked`.
11. `--json` stdout parses cleanly as one object.

**Deliverable:** recorded integration-test result with issue key, hash, page/row counts and V3 replay reference.

---

## 14. Files expected to change

| File | Expected work |
|---|---|
| `tools/import_issue.py` | Add targets, V3 orchestration, duplicate/version gate, staged manifest v2, atomic writer, exit codes, safe download policy, import lock, `--json` |
| `tools/replay_runs_to_v3.py` | Accept `--v3-db` and implement `--no-v5-lookup`; persist source URL/PDF provenance; resolve run identity by `run_report_uri`; add evidence fingerprint; stabilise single-run JSON result; retain idempotency |
| `tests/test_import_issue.py` | Expand CLI, V5 compatibility (including cwd paths), V3 target, failure, security and duplicate tests |
| `tests/test_import_issue_v3.py` | Focused V3 orchestration, replay-only integrity, JSON, lock and exit-code tests (new file; or extend the existing file) |
| `tests/database/test_v3_replay_idempotency.py` | Extend run-identity, `--no-v5-lookup`, legacy manifest, fingerprint, backfill and result-contract tests |
| `README.md` / `IMPORT_ISSUE.md` | Document V5, V3, OCR-only, replay-only and the different database paths |
| `DATABASE.md` | Document `extraction_runs.config_json` provenance keys; do not change the V3 schema |
| `CHANGELOG.md` | Record the importer upgrade |

No `database/v3/*.sql` change and no migration tool are required in V2.0.

Avoid modifying Workbench routes/templates in this phase, except for a small interface note if needed. Do not add the Workbench import-job table as part of this importer upgrade unless the current architecture demonstrates a concrete reason it belongs in the shared ingestion layer. Persistent Workbench job tracking is a separate follow-on task.

---

## 15. Follow-on Workbench integration contract

Once this importer upgrade is complete and validated, Workbench calls it as a child process rather than reimplementing download/OCR/replay logic.

Expected call pattern:

```bash
python tools/import_issue.py \
  "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf" \
  --target v3 \
  --v3-db /abs/data/macau_balancete_v3.sqlite \
  --outdir "/abs/outputs/runs/issue33_2026__job_${WORKBENCH_JOB_ID}" \
  --json
```

Workbench must:

- run the child process with `cwd=ROOT`;
- pass absolute paths;
- treat exit code 0 with `status = already_imported` as a non-error;
- surface exit code 3 as "operator decision required" or "import locked";
- parse stdout as exactly one JSON object;
- read the manifest path from the JSON result;
- implement retry controls based on the last completed stage;
- never infer financial review from a successful import.

Workbench will separately provide:

- persistent import job records and status history;
- one-at-a-time local worker execution;
- job list/detail/progress/log UI;
- retry controls based on the import manifest's last completed stage;
- page classification and evidence display;
- bank/entity, statement date, statement type and page grouping confirmation;
- transactional creation of proposed V3 statements and statement rows;
- proposed Standard Item mapping scoped to newly created statements;
- hand-off to the existing Review Queue.

A successful Importer run means only that raw OCR evidence has been replayed into V3.

---

## 16. Explicit non-goals

This specification does not include:

- Workbench Import Issue page/API/worker implementation;
- page classification UI;
- bank/entity identity approval;
- statement grouping or statement creation;
- Standard Item mapping or approval;
- review, adoption or publication;
- replacing the OCR engine or amount parser;
- changing the V5 schema or removing the legacy V5 workflow;
- changing V5 path-resolution behaviour;
- changing the V3 schema or adding a file-size column;
- deploying the Workbench to a LAN/public host;
- introducing Celery, Redis or another distributed job queue;
- automatic backfill of source URLs by guessing a URL pattern.

---

## 17. Definition of done

The importer upgrade is complete only when all of the following are true:

- [ ] Existing V5 commands and tests still work without users changing their commands.
- [ ] Existing V5 cwd-based path behaviour is unchanged.
- [ ] `--target v3` performs download, OCR and V3 raw-evidence replay in one command.
- [ ] `--replay-only` retries replay without OCR, validates manifest/PDF/artifact integrity, and does not duplicate rows.
- [ ] V3 target uses `--v3-db` and never sends the V3 database to the V5 database builder.
- [ ] V3 target passes `--no-v5-lookup` and a test proves the V5 database is not opened.
- [ ] Original source URL, PDF SHA-256, byte size, issue identity and replay identity are traceable.
- [ ] PDF byte size is stored in the manifest and `extraction_runs.config_json`; no V3 schema change is required.
- [ ] Evidence fingerprint detects a same-row-count change and fails safely.
- [ ] Manifest writes are atomic.
- [ ] A second concurrent V3 import is blocked by the import lock.
- [ ] `--json` stdout is exactly one JSON object for success, failure, duplicate, decision-required, lock-held and preflight-error paths.
- [ ] V3 raw rows remain unassigned pending page classification.
- [ ] Duplicate and retry behaviour follows the §8.2 decision table, including the precedence rule.
- [ ] URL, redirect, file-size, PDF, path and local-source safety tests pass.
- [ ] Existing V3 records/reviews/adoptions/publication are unchanged by importing a new issue.
- [ ] A real Gazette issue has completed an end-to-end V3 integration test on an isolated database copy.
- [ ] Documentation clearly distinguishes V5 import, V3 raw ingestion, OCR-only operation and replay-only retry.

---

## Appendix A — Changes from v1.0/v1.1 to V2.0

| Earlier issue | V2.0 resolution |
|---|---|
| `--no-ocr` contradicted replay-only retry | Added `--replay-only`; V3 rejects `--no-ocr` with a clear message |
| `--db` naming collision between V5 and replay | Replay uses `--v3-db`; `--db` is a deprecated alias |
| Replay silently reads V5 | V3 passes `--no-v5-lookup`; replay must skip the lookup and a test proves isolation |
| Duplicate identity used truncated `document_key` | Full `sha256` lookup using the existing index |
| `--reprocess` could skip the different-PDF version check | Unified §8.2 decision table and §8.3 precedence; reprocess only covers same content |
| Same-issue reprocess could create a second source document | Same-issue reprocess reuses the existing source document; only cross-issue reprocess creates a second row |
| Same hash / different issue undefined | `already_imported_content_conflict`, exit code 3 |
| `issue_key` format inconsistent (`issueNN_YYYY` vs `YYYY-NN`) | Canonical `YYYY-NN`; explicit manifest field and replay argument |
| Run-directory name was the identity source | Manifest-first identity; replay resolves existing run by `run_report_uri` |
| Legacy manifests use `"source"` and 6 runs have none | Compatibility rule and reconstructed-provenance rule added |
| Existing 14 rows have NULL source URLs | `COALESCE` backfill from 8 legacy manifests; 6 stay NULL |
| No file-size column | V2.0 keeps size in manifest and `extraction_runs.config_json`; no migration |
| `--overwrite-pdf` contradicts V3 preservation | Rejected for V3; versioned filename instead |
| V3 hardening would break V5 HTTP test | Target-scoped download policy |
| Changed row with same count was silently skipped | Versioned evidence fingerprint stored in `extraction_runs.config_json` |
| V5 cwd path behaviour could be broken by refactor | V5 path behaviour explicitly preserved; only V3 defaults use `ROOT` |
| Replay-only did not verify source integrity | Added manifest/target/issue/hash/PDF/artifact checks |
| `--json` could be polluted by child output | Strict stdout contract; progress and child output go to stderr/log |
| Manifest writes could leave partial JSON | Atomic temp-file write and `os.replace` |
| Two concurrent imports could duplicate work | V3 import lock with `fcntl.flock` |
| Angle-bracket shell placeholders | Replaced with `JOB_ID` |
| Workbench had no machine-readable result | `--json`, exit codes and manifest path in the result |
| Integration test could hit the operational DB | Isolated copy required |
| `unknown_issue` run directory would fail replay | V3 preflight rejects underivable issue keys |
| Replay `rows_inserted = 0` was ambiguous | Added `already_present` and stable result fields |
| Version labels were inconsistent | Document version unified as V2.0 |
