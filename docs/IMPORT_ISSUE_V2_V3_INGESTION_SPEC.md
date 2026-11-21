# Import Issue V2 — V3 Ingestion Upgrade Specification (v1.1)

**Project:** Macau Gazette Balancete OCR  
**Primary target:** `tools/import_issue.py`  
**Related components:** `tools/replay_runs_to_v3.py`, `tools/build_v3_db.py`  
**Status:** Proposed implementation specification, revision 2  
**Date:** 2026-11-21  
**Supersedes:** v1.0 of this document

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
  gazette_issues        14   (issue keys 2026-16 .. 2026-19, 2026-29 .. 2026-33, 2026-42 .. 2026-46)
  source_documents      14
  document_pages        118
  extraction_runs       14
  page_extractions      118
  extracted_rows        7,156
  statements            113
  statement_rows        7,046
  statement_row_mappings 7,251
  statement_reviews     68
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

### 2.5 Current importer behaviour that must not regress

- `--db` defaults to `data/macau_balancete.sqlite` and is resolved against the current working directory.
- `--input-dir` defaults to `input`; `--outdir` defaults to `outputs/runs/issueNN_YYYY` when the filename matches the Gazette pattern, otherwise `outputs/runs/<stem>`.
- Local PDF paths and `http://` URLs are currently supported for V5.
- `tests/test_import_issue.py` uses a local HTTP server at `127.0.0.1:0`.
- A failed early stage writes a shared `output_import_failed/import_manifest.json`.

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
- V3 target must not read or write the V5 database by default. It passes `--no-v5-lookup` to replay.
- Never pass the V3 database path to `tools/build_database.py`.
- Never use `data/macau_balancete_v3.sqlite` as the argument to the legacy `--db` option.
- Never write to a frozen/read-only V3 snapshot under `data/baseline/`.

### 3.2 Naming rule

The V3 replay tool must accept `--v3-db` as the primary V3 database option. The existing `--db` spelling is a deprecated alias for compatibility; new code and documentation use `--v3-db`.

This rule exists so that "`--db` means the legacy V5 database" remains unambiguous across the project.

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

Default (resolved against the project root, not the current working directory):

```text
data/macau_balancete_v3.sqlite
```

This option is only valid with `--target v3` (including `--replay-only`).

### 4.3 New V3-only options

```text
--replay-only            Reuse an existing OCR run directory; do not download or OCR.
--reprocess              Allow a second OCR run for the same PDF content.
--new-version            Allow the same issue key with different PDF content.
--reason TEXT            Required with --reprocess or --new-version; recorded in the manifest.
--issue-key YYYY-NN      Explicit canonical issue key; otherwise derived from URL/filename.
--allow-local-source     Allow a local PDF path for --target v3.
--allow-outside-output   Trusted CLI override for an --outdir outside the managed output area.
--json                   Print one machine-readable final result object to stdout.
```

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
| `--reprocess` | Reject | Conditional; requires `--reason` | Reject | Reject |
| `--new-version` | Reject | Conditional; requires `--reason` | Reject | Reject |
| `--reason` | Reject | Required with `--reprocess`/`--new-version` | Reject | Reject |
| `--issue-key` | Reject | Supported; must match `YYYY-NN` | Supported | Conditional; must match manifest/run |
| `--allow-local-source` | Reject (local is already supported) | Conditional; required for local PDF | Supported as no-op | Reject |
| `--allow-outside-output` | Reject | Conditional; trusted CLI only | Conditional | Conditional |
| `--json` | Supported | Supported | Supported | Supported |

Do not remove existing options just to simplify the new interface. If an option is not meaningful for a target, fail with a clear message.

Implementation note: `--db` and `--v3-db` must use `default=None` in `argparse` and resolve their defaults per target. If `--db` keeps its current non-None default, the V3 target cannot distinguish "the user omitted `--db`" from "the user explicitly supplied the legacy database", and the reject rule becomes unenforceable.

Implementation note: the positional `source` must become optional at the `argparse` level (`nargs="?"`) so that `--replay-only` can omit it. Enforce "source is required" for every other mode with an explicit preflight error.

### 4.5 Default paths and working directory

All default paths are resolved against the project root:

```text
ROOT/data/macau_balancete.sqlite       (V5 --db default)
ROOT/data/macau_balancete_v3.sqlite    (V3 --v3-db default)
ROOT/input                              (default input)
ROOT/outputs/runs                       (default run output)
```

An explicitly supplied relative path keeps the current CLI meaning and is resolved against the current working directory. This preserves existing V5 workflows while making defaults independent of where the command is launched.

The importer and replay subprocesses must run with `cwd=ROOT`. A Workbench child process must not rely on its own working directory for defaults, and should pass absolute paths.

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
2. Resolve all default paths against `ROOT`, not the current working directory.
3. Validate the V3 database:
   - it exists and opens read/write;
   - it is not under `data/baseline/`;
   - it contains the expected V3 tables;
   - it contains `source_documents.file_size_bytes` (migration 016, §7.4). If missing, abort with the migration command.
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
   - for a URL, apply §9.1 before any request;
   - for a local path, require `--allow-local-source`, reject non-files and symlink escape, and require a `.pdf` filename.
7. Validate `--name`: sanitized basename, `.pdf` suffix, and issue-key parseability.
8. `--dry-run`: print the complete planned command sequence and exit 0 without downloading, OCR or V3 writes.

No network request or database mutation occurs before preflight succeeds. Read-only V3 queries are allowed.

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

### Stage 1b — Content duplicate gate

This gate applies to a normal V3 import that will run OCR and replay. Skip it for `--download-only` and `--inspect-only`; those modes do not create V3 records and must keep the downloaded file.

If `--reprocess --reason` is supplied, skip steps 2–4 and proceed to step 5.

After the download is complete and hashed, but **before OCR**:

1. Look up the full SHA-256 in `source_documents.sha256` (index `idx_source_documents_sha256`). Do not use the truncated `document_key` for content identity.
2. If the same hash exists for the same issue key:
   - return `status = already_imported`, `already_present = true`, with the existing issue/document/run references;
   - do not run OCR;
   - exit code 0.
3. If the same hash exists for a different issue key:
   - return `status = already_imported_content_conflict` and require an explicit operator decision;
   - do not run OCR;
   - exit code 3.
4. If the same issue key exists with a different hash:
   - return `status = new_source_version_required`;
   - require `--new-version` and `--reason`;
   - do not overwrite the existing document or run;
   - exit code 3 unless `--new-version` is supplied.
5. If `--reprocess` is supplied with `--reason`, allow a second OCR run for the same content. Create a new run directory and preserve the previous run.
6. Discard the temporary download when the result is `already_imported` and no reprocess is requested. Do not promote it to `input/`.

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

Before calling V3 replay, write `import_manifest.json` in the run directory with the schema in §11.

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
  --no-v5-lookup \
  --json
```

Requirements:

- pass only the V3 database to `replay_runs_to_v3.py`;
- do not call `tools/build_database.py` in this target;
- do not read the V5 database; `--no-v5-lookup` is mandatory for the V3 target;
- capture stdout/stderr and preserve the replay log;
- treat a non-zero exit code or an invalid replay result as failure;
- validate the replay result fields defined in §6.3;
- record the replay result/run identifier in the manifest;
- update the manifest to `completed` only after replay has succeeded and its result has been validated.

If the replay tool emits JSON Lines on stdout, parse the single object for this `--run-dir` and validate it. Prefer adding `--json` for a single top-level object; do not parse human-readable text.

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

With `--json`, print exactly one JSON object to stdout and keep logs on stderr. The object must contain the final manifest path and status.

A V3 import success must be described as **raw OCR evidence imported into V3**, not as a completed or reviewed financial statement.

---

## 6. Data boundary, replay contract and provenance

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
  "already_present": false
}
```

Status values: `ok`, `already_present`, `failed`.

Rules:

- `already_present = true` and `rows_inserted = 0` means the same run identity was already replayed; this is a successful idempotent result, not a failure.
- `status = failed` includes `error_type` and `error_message`.
- Exit code 0 for `ok` and `already_present`; 1 for `failed`; 2 for usage errors.
- The importer must not infer success from `rows_inserted = 0` alone; it must use `status` and `already_present`.

### 6.4 Run identity resolution in replay

Before creating a new extraction run, replay resolves the run identity in this order:

1. an existing `extraction_runs` row whose `run_report_uri` equals `<run_dir>/run_report.json`;
2. the legacy V5 `runs` table, only when V5 lookup is enabled;
3. a deterministic `stable_id` fallback.

This order is mandatory because new V3 run directories have a `__job_<id>` suffix and do not match legacy V5 run-directory names. Without step 1, `--replay-only --no-v5-lookup` on an existing V3 run would create a new run ID and duplicate extracted rows.

### 6.5 Source provenance

The original Gazette URL must not be lost.

| Provenance item | V3 storage | Rule |
|---|---|---|
| Issue key | `gazette_issues.issue_key` | `YYYY-NN`; unique |
| Issue-level source URL | `gazette_issues.source_url` | Set at insert; update only when currently `NULL` (`COALESCE`); never overwrite a non-NULL value |
| Document-level source URL | `source_documents.source_url` | Exact PDF URL; set at insert; backfill only when `NULL`; never overwrite |
| PDF SHA-256 | `source_documents.sha256` | Full 64-hex hash; content identity |
| PDF byte size | `source_documents.file_size_bytes` | Added by migration 016 (§7.4) |
| PDF storage path | `source_documents.storage_uri` | Absolute local path; not a network URI |
| Original filename | `source_documents.original_filename` | Sanitized basename |
| Download time | `source_documents.acquired_at` | Manifest `download_finished_at` when present; otherwise replay time for legacy runs |
| OCR run | `extraction_runs.run_id` | Resolved per §6.4 |
| OCR report | `extraction_runs.run_report_uri` | Absolute path to `run_report.json` |
| Import manifest location | `extraction_runs.config_json.import_manifest` | Absolute path or `null` for reconstructed legacy runs |
| Manifest origin | `extraction_runs.config_json.manifest_origin` | `written` or `reconstructed` |
| Run directory | `extraction_runs.config_json.run_dir` | Absolute path; helps future retries |

Do not put secrets or URL query values in any of these fields.

### 6.6 Legacy manifest compatibility

- The replay tool must accept the legacy manifest key `"source"` as the source URL.
- A missing `import_manifest.json` must not fail replay when the core artifacts exist; reconstruct provenance from `run_report.json` and mark `manifest_origin = reconstructed`.
- Do not write a fabricated `import_manifest.json` into an old run directory. Store reconstruction metadata in V3 only.
- For the 6 existing runs without a manifest, leave `source_url` `NULL`. The known Gazette URL pattern is not evidence and must not be guessed.
- For the 8 existing runs with a legacy manifest, `source` supplies the URL during a deliberate backfill replay.
- `source_pdf_size` from every existing `run_report.json` can backfill `file_size_bytes` for all 14 existing documents.

### 6.7 Currency and unit rule

For in-scope Macau bank statements, the project rule remains:

```text
currency = MOP
unit_scale = 1
```

The Importer must not use this rule to infer or approve statement identity. Currency/unit metadata is applied in the later statement-confirmation workflow with provenance recorded as a project/domain rule, not as per-PDF manual verification.

---

## 7. Output directory, run identity and schema change

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

### 7.3 Failure fallback directory

Early failures and operator-decision results may occur before the run directory exists. Write those manifests to unique paths:

```text
output_import_failed/<job-id>/import_manifest.json       # stage failure
output_import_results/<job-id>/import_manifest.json      # already_imported / conflict / decision required
```

Do not reuse a single shared `output_import_failed/import_manifest.json`, because concurrent or consecutive failures would overwrite each other.

For `already_imported` and operator-decision results, do not create the requested run directory. This keeps the `--outdir` namespace free for a later explicit `--reprocess` or `--new-version` run. If `--outdir` was supplied, include it in the result manifest but do not create it.

### 7.4 Migration 016 — source document file size

The V3 schema has no file-size field. Add the smallest justified migration:

```sql
-- database/v3/016_source_document_file_size.sql
ALTER TABLE source_documents ADD COLUMN file_size_bytes INTEGER;

INSERT OR REPLACE INTO schema_meta(key, value, updated_at)
VALUES ('migration_016_source_document_file_size', 'applied', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
```

Rules:

- New databases built by `tools/build_v3_db.py` apply the file in sorted order automatically.
- Existing operational databases must be migrated once, after a backup, using a small idempotent helper `tools/migrate_v3_db.py` that checks `PRAGMA table_info(source_documents)` and `schema_meta` before applying.
- Do not run `apply_schema()` against an existing database; `ALTER TABLE` is not idempotent.
- Backfill existing rows by re-running replay over the existing run directories after the migration. Replay updates `source_url` and `file_size_bytes` only when the current value is `NULL`, and inserts no new rows.
- Existing `document_key` values are not changed by this migration.

If the project decides not to change the schema, the fallback is to store `pdf_bytes` only in the manifest and `extraction_runs.config_json`, and to document the missing SQL-level query capability. That fallback must be decided in Phase 0, before implementation.

---

## 8. Duplicate handling, idempotency and retries

Duplicate handling distinguishes content identity from Gazette issue identity.

### 8.0 Operator decisions

`--reprocess` and `--new-version` are mutually exclusive. Both require a non-empty `--reason`, which is recorded in the manifest as `decision_reason` and in the replay log. Neither flag may be used to overwrite an existing PDF, source document, run or extracted row.

### 8.1 Content identity

Content identity is the full PDF SHA-256. Lookup uses `source_documents.sha256` and the existing `idx_source_documents_sha256` index. Do not use the truncated `document_key` for duplicate detection.

An explicit `--reprocess` or `--new-version` can legitimately produce more than one `source_documents` row for the same hash. Duplicate resolution therefore returns all matching rows, prefers a row with the same issue key when reporting `already_imported`, and never deletes or merges existing rows.

### 8.2 Same PDF bytes, same issue

- Return `already_imported` with the existing issue/document/run references.
- Do not create a second logical source document or replay record.
- Do not run OCR.
- Exit code 0 unless an explicit reprocess is requested.

### 8.3 Same PDF bytes, different issue

- Return `already_imported_content_conflict`.
- Do not silently create a second source document.
- Require `--reprocess --reason` for an intentional second ingestion.
- Exit code 3 when no explicit decision is supplied.
- The manifest records the conflicting existing issue key and document ID.

### 8.4 Same issue key, different PDF bytes

Treat this as a new source version:

- preserve the old PDF, source document and run;
- do not overwrite an existing Gazette issue or document silently;
- report `new_source_version_required`;
- require `--new-version --reason`;
- create a new `source_documents` row with its own `document_key`, hash and storage path;
- exit code 3 when no explicit decision is supplied.

V3 can represent multiple documents under one issue because `source_documents.document_key` is unique per issue+content. V3 has no "current version" flag; the Importer must not pick one. Page classification and statement confirmation must choose the document to review.

### 8.5 Replay idempotency

Keep `tools/replay_runs_to_v3.py` idempotent for the same run identity:

- replaying the same completed run must not create duplicate pages, page extractions or extracted rows;
- `already_present = true` with `rows_inserted = 0` is a successful result;
- a changed row set for an existing run must fail instead of overwriting raw evidence.

A new run directory containing the same PDF is not automatically the same run identity. Duplicate-content handling in §8.2–§8.4 must run before a second logical ingestion is created.

### 8.6 Retry stages

| Failure point | Correct retry behaviour |
|---|---|
| Preflight failed | Fix the flag, path, URL or database; no run artifacts exist |
| Download failed | Retry download; no OCR/V3 replay exists |
| Duplicate gate returned `already_imported` | No retry; use `--reprocess` only with a justified reason |
| Duplicate gate requires a version decision | Re-run with `--new-version --reason` or stop |
| OCR failed | Retry OCR into a new unique run directory; preserve the failed run |
| OCR completed, V3 replay failed | Retry with `--replay-only --outdir <existing run dir>`; do not rerun OCR |
| V3 replay completed | Return `already_imported` unless explicit `--reprocess` is requested |

The Workbench job table and UI are designed later. The CLI importer must expose enough stage/result information for a future worker to implement these retry rules.

---

## 9. URL, download and filesystem security

### 9.1 Scope

- The hardened policy applies to `--target v3` URL sources.
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

- Store downloaded PDFs only under the configured managed input directory (`ROOT/input` by default).
- Store run output only under the configured managed output directory (`ROOT/outputs/runs` by default) for normal Workbench use.
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

---

## 10. Failure handling and manifest state

### 10.1 Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success, `already_imported`, `download_only`, or `inspect_only` |
| 1 | A required stage failed (download, OCR, replay, manifest write) |
| 2 | Usage or preflight error |
| 3 | Operator decision required (`already_imported_content_conflict`, `new_source_version_required`) |

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
- Write failed manifests to the unique per-job location in §7.3.

Do not claim durable background-job support at this stage. The CLI is a synchronous command-line orchestrator; Workbench wraps it in its own persistent worker/job mechanism later.

---

## 11. Import manifest schema v2

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
  "decision_reason": null,
  "last_exit_code": null,
  "error_type": null,
  "error_message": null,
  "v3": {
    "gazette_issue_id": null,
    "source_document_id": null,
    "run_id": null,
    "already_present": false,
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

### 12.1 Backward compatibility tests

- Existing `tests/test_import_issue.py` must continue passing unchanged.
- The `http://127.0.0.1` download fixture must still work for V5.
- Existing V5 CLI defaults and option behaviours must remain unchanged.
- V5 target still invokes the existing V5 database pipeline with the legacy DB only.
- Existing `--no-db`, `--download-only`, `--no-ocr`, `--inspect-only`, `--dry-run`, `--overwrite-pdf` and `--reuse-output` behaviours must be preserved or have explicitly tested compatibility mappings.
- `--target` omitted behaves exactly like `--target v5`.

### 12.2 V3 target tests

- V3 target downloads and OCRs, then invokes `replay_runs_to_v3.py` with `--v3-db`, `--run-dir`, `--no-v5-lookup` and `--json`.
- V3 target never invokes `tools/build_database.py`, `name_registry.py refresh-banks`, or `import_user_standard.py map`.
- V3 target rejects incompatible `--db`, `--publish`, `--force-publish`, `--published-by`, `--no-name-map` and `--no-bank-refresh`.
- `--no-db` with V3 is rejected with a message pointing to `--target ocr-only`.
- `--no-ocr` with V3 is rejected with a message pointing to `--replay-only`.
- `--replay-only` does not download or OCR and reuses the existing run directory.
- V3 replay is not called when download/OCR fails or a required artifact is missing.
- V3 replay failure returns non-zero and leaves an accurate failed manifest.
- Successful V3 target writes manifest status only after replay is confirmed.
- Successful result clearly states that raw OCR evidence is imported and page classification remains outstanding.
- Exit codes 0/1/2/3 are covered.

### 12.3 Manifest and provenance tests

- A new V3 import writes `manifest_version: 2`, canonical `issue_key`, source URL, SHA-256 and byte size.
- The source URL is written into `gazette_issues.source_url` and `source_documents.source_url`.
- PDF SHA-256 and byte size are recorded correctly in V3.
- `source_documents.file_size_bytes` exists after migration 016 and is backfilled from `run_report.json`.
- Legacy manifests using `"source"` are read correctly.
- Missing manifests do not fail replay; `manifest_origin = reconstructed`.
- Existing non-NULL `source_url` is never overwritten.
- Existing NULL `source_url` is backfilled when provenance is available.
- Replaying the existing 14 runs inserts no new rows and fills available provenance.
- Issue key, source document, OCR run and replay run ID are traceable to each other.
- Existing issue/document/run identity is reused for replay-only retries.

### 12.4 Duplicate and retry tests

- Same PDF hash, same issue returns `already_imported` and does not create a second ingestion.
- Same PDF hash, different issue returns `already_imported_content_conflict` and exit code 3.
- Same issue key, different hash returns `new_source_version_required` and exit code 3.
- `--new-version --reason` creates a second source document and preserves the first.
- `--reprocess --reason` creates a new run directory and preserves the first run.
- OCR retry receives a new run directory.
- Replay-only retry reuses the existing OCR run directory and does not duplicate rows.
- Replaying the exact same run is idempotent.
- A changed row set for an existing run fails instead of overwriting.

### 12.5 Security tests

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

### 12.6 Regression isolation test

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

### Phase 0 — Verify current contracts

1. Re-read the current `tools/import_issue.py`, its tests and every CLI caller.
2. Inventory all existing options and the exact V5 stage order.
3. Inspect the current V3 DDL/migrations and `tools/replay_runs_to_v3.py` result contract.
4. Confirm official Gazette PDF hostnames from real project source URLs.
5. Confirm the facts in §2 against the current database and run directories.
6. Decide whether to apply migration 016 or use the documented fallback.
7. Identify every place where source URL, hash and file size can be stored without violating the V3 data model.

**Deliverable:** a short implementation note in the PR/commit describing compatibility and V3 provenance decisions.

### Phase 1 — Refactor importer into explicit stages

1. Refactor download, OCR, V5 import and manifest writing into testable functions without changing their behaviour.
2. Add target parsing with `v5` as the default.
3. Add a target-aware download policy; keep V5 behaviour intact.
4. Add consistent stage results, exit codes and manifest updates.
5. Make all default paths resolve against `ROOT`.

**Deliverable:** refactored importer with no functional regression in V5 mode.

### Phase 2 — Add V3 orchestration and provenance

1. Add `--target v3`, `--v3-db`, `--replay-only`, `--reprocess`, `--new-version`, `--reason`, `--issue-key`, `--allow-local-source`, `--allow-outside-output` and `--json`.
2. Run download + OCR without the V5 DB import steps.
3. Add the content duplicate gate before OCR.
4. Write manifest schema v2 with legacy aliases.
5. Update replay to accept `--v3-db` and `--no-v5-lookup`.
6. Add the run-identity lookup by `run_report_uri` in replay.
7. Add the replay result contract and `--json` mode.
8. Add migration 016 and `tools/migrate_v3_db.py`.
9. Persist source URL, hash, file size and run identity in V3.
10. Add duplicate handling and replay-only retry support.
11. Add a deliberate provenance backfill replay for existing runs.

**Deliverable:** one CLI command can safely import a Gazette issue's raw OCR evidence into V3, and a replay-only retry can repair a failed manifest without duplicating data.

### Phase 3 — Harden network and filesystem boundaries

1. Add the official-host allowlist and redirect validation.
2. Enforce HTTPS, destination-address checks, bounded streaming and PDF validation.
3. Enforce managed output paths and atomic file promotion.
4. Add security regression tests.
5. Keep V5 download tests green.

**Deliverable:** safe URL-based V3 ingestion suitable for a local Workbench to call.

**Gate:** Workbench must not call the V3 target until Phase 3 and Phase 4 are complete.

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

**Deliverable:** recorded integration-test result with issue key, hash, page/row counts and V3 replay reference.

---

## 14. Files expected to change

| File | Expected work |
|---|---|
| `tools/import_issue.py` | Add targets, V3 orchestration, duplicate gate, staged manifest v2, exit codes, safe download policy, `--json` |
| `tools/replay_runs_to_v3.py` | Accept `--v3-db` and `--no-v5-lookup`; persist source URL/PDF provenance; resolve run identity by `run_report_uri`; stable `--json` result; retain idempotency |
| `tools/migrate_v3_db.py` | New idempotent helper for migration 016 on existing databases |
| `database/v3/016_source_document_file_size.sql` | Add `source_documents.file_size_bytes` and record `schema_meta` |
| `tests/test_import_issue.py` | Expand CLI, V5 compatibility, V3 target, failure, security and duplicate tests |
| `tests/test_import_issue_v3.py` | Focused V3 orchestration, provenance and exit-code tests (new file; or extend the existing file) |
| `tests/database/test_v3_replay_idempotency.py` | Extend run-identity, `--no-v5-lookup`, legacy manifest, backfill and result-contract tests |
| `README.md` / `IMPORT_ISSUE.md` | Document V5, V3, OCR-only, replay-only and the different database paths |
| `DATABASE.md` | Document migration 016 and the provenance fields |
| `CHANGELOG.md` | Record the importer upgrade |

Avoid modifying Workbench routes/templates in this phase, except for a small interface note if needed. Do not add the Workbench import-job table as part of this importer upgrade unless the current architecture demonstrates a concrete reason it belongs in the shared ingestion layer. Persistent Workbench job tracking is a separate follow-on task.

---

## 15. Follow-on Workbench integration contract

Once this importer upgrade is complete and validated, Workbench calls it as a child process rather than reimplementing download/OCR/replay logic.

Expected call pattern:

```bash
python tools/import_issue.py \
  "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf" \
  --target v3 \
  --v3-db data/macau_balancete_v3.sqlite \
  --outdir "outputs/runs/issue33_2026__job_${WORKBENCH_JOB_ID}" \
  --json
```

Workbench must:

- run the child process with `cwd=ROOT`;
- pass an explicit `--outdir` derived from its job ID;
- treat exit code 0 with `status = already_imported` as a non-error;
- surface exit code 3 as "operator decision required";
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
- deploying the Workbench to a LAN/public host;
- introducing Celery, Redis or another distributed job queue;
- automatic backfill of source URLs by guessing a URL pattern.

---

## 17. Definition of done

The importer upgrade is complete only when all of the following are true:

- [ ] Existing V5 commands and tests still work without users changing their commands.
- [ ] `--target v3` performs download, OCR and V3 raw-evidence replay in one command.
- [ ] `--replay-only` retries replay without OCR and without duplicating rows.
- [ ] V3 target uses `--v3-db` and never sends the V3 database to the V5 database builder.
- [ ] V3 target passes `--no-v5-lookup` and does not read or write V5.
- [ ] Original source URL, PDF SHA-256, file size, issue identity and replay identity are traceable.
- [ ] Migration 016 is applied and documented; existing rows are backfilled where evidence exists.
- [ ] V3 raw rows remain unassigned pending page classification.
- [ ] Duplicate and retry behaviour is explicit, tested and non-destructive.
- [ ] `already_imported` and operator-decision results are machine-readable with distinct exit codes.
- [ ] URL, redirect, file-size, PDF, path and local-source safety tests pass.
- [ ] Existing V3 records/reviews/adoptions/publication are unchanged by importing a new issue.
- [ ] A real Gazette issue has completed an end-to-end V3 integration test on an isolated database copy.
- [ ] Documentation clearly distinguishes V5 import, V3 raw ingestion, OCR-only operation and replay-only retry.

---

## Appendix A — Changes from v1.0

| v1.0 issue | v1.1 resolution |
|---|---|
| `--no-ocr` contradicted replay-only retry | Added `--replay-only`; V3 rejects `--no-ocr` with a clear message |
| `--db` naming collision between V5 and replay | Replay uses `--v3-db`; `--db` is a deprecated alias |
| Replay silently reads V5 | V3 passes `--no-v5-lookup`; legacy lookup is opt-in |
| Duplicate identity used truncated `document_key` | Full `sha256` lookup using the existing index |
| Same hash / different issue undefined | `already_imported_content_conflict`, exit code 3 |
| `issue_key` format inconsistent (`issueNN_YYYY` vs `YYYY-NN`) | Canonical `YYYY-NN`; explicit manifest field and replay argument |
| Run-directory name was the identity source | Manifest-first identity; replay resolves existing run by `run_report_uri` |
| Legacy manifests use `"source"` and 6 runs have none | Compatibility rule and reconstructed-provenance rule added |
| Existing 14 rows have NULL source URLs | `COALESCE` backfill from 8 legacy manifests; 6 stay NULL |
| No file-size column | Migration 016 plus idempotent migration helper |
| `--overwrite-pdf` contradicts V3 preservation | Rejected for V3; versioned filename instead |
| V3 hardening would break V5 HTTP test | Target-scoped download policy |
| No byte-size/provenance migration path | `tools/migrate_v3_db.py` and backfill replay |
| Manifest schema had conflicting shapes | Canonical v2 schema plus legacy aliases |
| Angle-bracket shell placeholders | Replaced with `JOB_ID` |
| Workbench had no machine-readable result | `--json`, exit codes and manifest path in the result |
| Integration test could hit the operational DB | Isolated copy required |
| `unknown_issue` run directory would fail replay | V3 preflight rejects underivable issue keys |
| Replay `rows_inserted = 0` was ambiguous | Added `already_present` and stable result fields |
