# Import Issue V2 — V3 Ingestion Upgrade Specification (V2.4)

**Project:** Macau Gazette Balancete OCR  
**Primary target:** `tools/import_issue.py`  
**Related components:** `tools/replay_runs_to_v3.py`, `tools/build_v3_db.py`  
**Specification version:** V2.4  
**Status:** Implementation baseline  
**Supersedes:** V2.0, V2.1, V2.2 and V2.3  
**Canonical location:** `docs/IMPORT_ISSUE_V2_V3_INGESTION_SPEC.md` (this V2.4 content replaces the earlier working copies)

---

## 1. Objective

Upgrade `tools/import_issue.py` into a reliable, traceable ingestion entry point for the current V3 operational database, while preserving the existing V5 workflow.

This phase upgrades the command-line Importer only. It does **not** implement the Workbench import page, worker, page classification UI, Statement creation, Standard Item mapping, review, adoption or publication.

After this upgrade, the Importer supports three targets:

| Target | Purpose | Database effect |
|---|---|---|
| `v5` | Existing legacy import workflow | Preserves current V5 writes and side effects |
| `v3` | Standard new ingestion workflow | Downloads, OCRs, then replays raw OCR evidence into V3 |
| `ocr-only` | Download and OCR without database import | No V5 or V3 database writes |

**Default target (changed after V2.4 baseline):** `--target` now defaults to `v3`, because the operational database is V3. The V5 workflow is unchanged but must be requested explicitly with `--target v5`. The original V2.4 requirement kept `v5` as the silent default for backward compatibility; that requirement was superseded deliberately on 2026-11-21, together with its documentation and tests. This is an intentional, documented behaviour change, not an accidental default flip.

The V3 target must never pass the V3 database path to `tools/build_database.py`.

---

## 2. Verified current-state facts

The implementation phase must re-check these facts against the current checkout and local operational database. The values below describe the repository state reviewed for V2.4 and must not be treated as timeless constants.

### 2.1 Operational V3 data reviewed

`data/macau_balancete_v3.sqlite` contained 14 Gazette issues and 14 source documents, covering issue keys 2026-16 through 2026-19, 2026-29 through 2026-33, and 2026-42 through 2026-46. It contained 118 document pages, 14 extraction runs, 118 page extractions, 7,156 extracted rows, 113 statements, 7,046 statement rows, 7,251 statement-row mappings and 68 statement reviews.

At the time of review, the 14 issue-level and 14 document-level `source_url` fields were `NULL`. Existing `source_documents.page_count` values record observed OCR pages, not the full PDF page count; for example, issue 2026-17 has `pdf_pages = 491` in `run_report.json` but `page_count = 11` in V3. V2.4 corrects this for new runs and defines a controlled legacy backfill.

### 2.2 Existing OCR run artifacts reviewed

The 14 run directories were under `outputs/runs/issueNN_YYYY`. Eight had legacy `import_manifest.json` files and six did not. All 14 had `run_report.json` with `source_pdf`, `source_pdf_sha256` and `source_pdf_size`.

The eight legacy manifests:

- store the original URL under `source`, not `source_url`;
- have no `manifest_version` or `target`;
- use `steps.{download,ocr,db_import,bank_refresh,name_map}`;
- are included in V5 audit artifact hashing.

A missing manifest must not cause a valid legacy replay to fail. The source URL must never be guessed for the six runs without one.

### 2.3 V3 schema facts

- `gazette_issues.source_url` exists and is nullable.
- `source_documents.source_url` exists and is nullable.
- `source_documents.sha256` is `NOT NULL`, with a 64-character lowercase hexadecimal check.
- `idx_source_documents_sha256` exists.
- `source_documents.document_key` currently follows `issue_key + ":" + sha256[:16]`; it is a uniqueness key, not the authoritative full content identity.
- `source_documents` has no file-size column.
- `extraction_runs.config_json` is text and can store additional provenance metadata while preserving existing JSON keys.
- `source_documents.storage_uri` currently stores an absolute local path, not a network URI.
- `extraction_runs.run_report_uri` currently stores the absolute path to `run_report.json`.
- Existing statements, statement rows, mappings, reviews, bindings, adoptions and published records must not be altered by a new raw-evidence import.

### 2.4 Existing replay behaviour reviewed

The current `tools/replay_runs_to_v3.py`:

- imports Gazette issues, source documents, document pages, extraction runs, page extractions and unassigned extracted rows;
- derives issue identity from the run-directory name using `issue(\d+)_(\d{4})`;
- reads `--db`, `--runs-dir`, `--run-dir`, `--v5-db` and `--log`;
- currently loads the V5 run lookup unconditionally;
- currently writes `NULL` to issue and source-document URL fields;
- uses insert-if-absent logic;
- currently guards changed evidence by row count, which does not catch a same-count amount or label change;
- currently derives `source_documents.page_count` primarily from observed OCR page numbers, not from the PDF total in `run_report.pdf_pages`;
- currently emits one JSON object per run on stdout and exits 0 or 1.

The V2.4 implementation must explicitly change the behaviours identified in this section and prove the changes with tests.

### 2.5 Existing Importer behaviour to preserve

The existing `tools/import_issue.py` currently has these path semantics:

- `--db` defaults to `ROOT/archive/database/macau_balancete.sqlite` (the frozen legacy V5 archive); an explicitly supplied `--db` path retains the legacy cwd-relative resolution;
- `--input-dir` defaults to `Path("input")`, also relative to the current working directory;
- when `--outdir` is omitted, `output_dir_for()` returns an absolute directory under `ROOT/outputs/runs/`;
- an explicitly supplied relative `--outdir` is resolved against the current working directory;
- OCR and child commands are launched with `cwd=ROOT`.

Preserve these exact V5 path semantics. Do not incorrectly treat the derived default output directory as cwd-relative.

Other V5 behaviour to preserve:

- URL and local PDF sources are accepted, including `http://` sources;
- existing flags and their current semantics remain available;
- `tests/test_import_issue.py` includes a local HTTP server on `127.0.0.1`;
- V5 behaviour is preserved when `--target v5` is supplied explicitly.

---

## 3. Architecture and database separation

### 3.1 Existing V5 flow

```text
Gazette PDF URL or local PDF
  -> download / copy PDF
  -> OCR / parser
  -> run output directory
  -> documents.csv and OCR result files
  -> tools/build_database.py
  -> legacy V5 database
  -> legacy bank refresh and name mapping
  -> optional legacy publication behaviour
```

`tools/build_database.py` writes the older V5 schema. Do not use it for V3 ingestion.

### 3.2 New V3 flow

```text
Official Gazette PDF URL
  -> full-import preflight and V3 import lock
  -> download to unique temporary file
  -> validate %PDF-, size and SHA-256
  -> duplicate/version decision
       |-- duplicate / decision required
       |     -> delete temporary file, write result manifest, stop
       `-- approved
             -> no-clobber promote PDF to managed input
             -> OCR into a unique run directory
             -> write import_manifest.json
             -> tools/replay_runs_to_v3.py
             -> V3 raw OCR evidence (unassigned)
             -> awaiting Workbench page classification
```

### 3.3 Non-negotiable separation rules

- `--db` remains the V5 database option.
- `--v3-db` is the V3 database option for the Importer.
- V3 target must not call `tools/build_database.py`.
- V3 target must not call V5 bank refresh, V5 name mapping or V5 publication steps.
- V3 target must not open or query the V5 database.
- V3 target calls replay with `--no-v5-lookup`; the replay tool must actually skip the V5 lookup function and must not open the V5 DB file.
- Never pass `data/macau_balancete_v3.sqlite` to the V5 `--db` option.
- Never write to a frozen/read-only snapshot under `data/baseline/`.

### 3.4 Required replay-tool changes

| Capability | Required behaviour |
|---|---|
| `--v3-db PATH` | New primary V3 DB option; existing `--db` remains a deprecated alias |
| `--no-v5-lookup` | Skip `load_legacy_runs()` entirely and never open the V5 DB |
| `--issue-key YYYY-NN` | Accept explicit canonical identity from the Importer; validate against the manifest when present |
| Run identity lookup | Reuse an existing V3 extraction run by `run_report_uri` before considering any V5 lookup or stable-ID fallback |
| Evidence fingerprint | Compute, store and compare a versioned fingerprint for the three OCR evidence files |
| Page coverage | Validate `documents.csv` against `pdf_pages`; use `pdf_pages` for `source_documents.page_count`; record coverage metadata |
| Provenance | Persist original URL, PDF size, manifest origin and run paths in existing V3 fields/config JSON |
| Single-run result | With one `--run-dir`, stdout is exactly one JSON object and nothing else |
| Legacy replay | Continue to accept legacy manifests and runs without manifests for controlled backfill |

The isolation test must make the V5 DB path nonexistent or unreadable and prove that replay with `--no-v5-lookup` still succeeds without opening it.

---

## 4. CLI design

### 4.1 Target selection

Add:

```text
--target {v5,v3,ocr-only}
```

Default: `v3` (changed from the V2.4 baseline `v5`; see §1).

### 4.2 V3 database option

Add:

```text
--v3-db PATH
```

Default for V3: `ROOT/data/macau_balancete_v3.sqlite`.

The option is valid only with `--target v3`, including `--replay-only`.

### 4.3 New V3 options

```text
--replay-only            Replay an existing V3 OCR run; do not download or OCR.
--reprocess              Re-run OCR for the same PDF content after an explicit decision.
--new-version            Import different PDF content for an Issue after an explicit decision.
--reason TEXT            Required with --reprocess or --new-version.
--issue-key YYYY-NN      Optional Issue key assertion; required only when the source filename cannot establish it.
--allow-local-source     Allow a local PDF source for --target v3.
--allow-outside-output   Trusted CLI override for an --outdir outside the managed output directory.
--json                   Emit exactly one machine-readable result object on stdout.
```

`--reprocess` and `--new-version` are mutually exclusive. Their exact meaning is defined in §8.

`--no-v5-lookup` belongs to `replay_runs_to_v3.py`, not the Importer CLI. The Importer always supplies it for V3 replay.

### 4.4 Option compatibility matrix

Incompatible options must fail with a clear message. Do not silently ignore flags.

| Option | V5 | V3 | OCR-only | V3 replay-only |
|---|---|---|---|---|
| positional `source` | Required; URL or local path | Required except replay-only; HTTPS URL or local with flag | Required; URL or local path | Must be absent |
| `--target` | `v5` | `v3` | `ocr-only` | `v3` with `--replay-only` |
| `--db` | Supported; V5 only | Reject | Reject | Reject |
| `--v3-db` | Reject | Supported | Reject | Supported |
| `--input-dir` | Preserve current behaviour | Must resolve under `ROOT/input` | Preserve V5-compatible behaviour | Reject |
| `--outdir` | Preserve current behaviour | Unique managed run directory | Preserve current behaviour | Required existing run directory |
| `--name` | Supported | Sanitised PDF basename; must not contradict derived Issue identity | Supported | Reject |
| `--pages` | Preserve current behaviour | Literal `all` for full V3 import; diagnostic `--inspect-only` may use a range | Preserve current behaviour | Reject |
| `--engine` | Supported | Supported | Supported | Reject |
| `--vision-level` | Supported | Supported | Supported | Reject |
| `--ocr-langs` | Supported | Supported | Supported | Reject |
| `--publish` | Preserve current semantics | Reject | Reject | Reject |
| `--force-publish` | Preserve current semantics | Reject | Reject | Reject |
| `--published-by` | Preserve current semantics | Reject | Reject | Reject |
| `--no-db` | Preserve current semantics | Reject; use `--target ocr-only` | Accepted as redundant legacy alias | Reject |
| `--no-ocr` | Preserve current semantics | Reject; use `--replay-only` | Preserve current semantics | Reject |
| `--no-name-map` | Preserve current semantics | Reject | Reject | Reject |
| `--no-bank-refresh` | Preserve current semantics | Reject | Reject | Reject |
| `--inspect-only` | Preserve current semantics | Supported as non-replay diagnostic mode only | Supported | Reject |
| `--download-only` | Preserve current semantics | Supported as non-replay diagnostic mode only | Supported | Reject |
| `--overwrite-pdf` | Preserve current semantics | Reject | Preserve current semantics | Reject |
| `--reuse-output` | Preserve current semantics | Reject; use a new run or replay-only | Preserve current semantics | Reject |
| `--dry-run` | Preserve current semantics | Preflight and plan only | Plan only | Preflight and plan only |
| `--replay-only` | Reject | Supported | Reject | Required |
| `--reprocess` | Reject | Conditional; same PDF content only; requires reason | Reject | Reject |
| `--new-version` | Reject | Conditional; same Issue, different PDF only; requires reason | Reject | Reject |
| `--reason` | Reject | Required with either decision flag | Reject | Reject |
| `--issue-key` | Reject | Optional assertion when the filename is parseable; required when it is not | Supported for OCR-only metadata if desired | Optional assertion against manifest only |
| `--allow-local-source` | Reject (local is already supported) | Required for local PDF | No-op / preserve existing local support | Reject |
| `--allow-outside-output` | Reject | Trusted CLI only | Preserve V5-compatible behaviour | Trusted CLI only |
| `--json` | Supported | Supported | Supported | Supported |

The parser may use `default=None` for `--db`, `--input-dir` and `--v3-db` so that explicit arguments can be distinguished from defaults. Resolve the defaults per target:

- V5 `--db`: default `ROOT/archive/database/macau_balancete.sqlite`; an explicit path is resolved against cwd as before.
- V5 `--input-dir`: `Path("input")`, resolved against cwd, matching current behaviour.
- V5 omitted `--outdir`: retain `output_dir_for()` under `ROOT/outputs/runs`.
- V5 explicit relative `--outdir`: resolve against cwd, matching current behaviour.
- V3 default paths: resolve against `ROOT`.
- Explicit relative paths: resolve against cwd, unless a specific option documents otherwise.

The positional `source` must become optional at argparse level (`nargs="?"`) to support replay-only. Enforce source presence or absence during target-aware preflight.

### 4.5 Issue-key derivation and validation

The source filename is the identity evidence, not an arbitrary `--name` override.

1. Derive `YYYY-NN` from the original URL path basename or original local filename using the known Gazette naming pattern, e.g. `anotariais-33-2026.pdf` -> `2026-33`.
2. If derivation succeeds and `--issue-key` is also supplied, it is an assertion: the two values must match. If they differ, reject the import before downloading.
3. If the filename does not encode a recognised Issue key, require `--issue-key` for V3.
4. `--name` changes only the managed local PDF basename; it must not alter Issue identity.
5. `--issue-key` must match `^\d{4}-\d{2}$`; the year must be 1900-2999 and the issue number 1-99. `NN` is the Gazette issue number, not the month. Do not widen the format without updating the replay parser and existing V3 keys.
6. Do not add a force override for a mismatch in V2.4. Correct the source or filename metadata instead.

Example: `anotariais-33-2026.pdf` derives `2026-33`, so `--issue-key 2026-34` must be rejected before download. There is no silent override in V2.4.

### 4.6 Full V3 import examples

Normal V3 import:

```bash
python tools/import_issue.py \
  "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf" \
  --target v3 \
  --v3-db data/macau_balancete_v3.sqlite \
  --outdir "outputs/runs/issue33_2026__job_${JOB_ID}" \
  --json
```

Replay-only retry (no source argument):

```bash
python tools/import_issue.py \
  --target v3 \
  --v3-db data/macau_balancete_v3.sqlite \
  --replay-only \
  --outdir "outputs/runs/issue33_2026__job_${JOB_ID}" \
  --json
```

Same PDF, same Issue, deliberate reprocess:

```bash
python tools/import_issue.py \
  "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf" \
  --target v3 --reprocess --reason "OCR engine/configuration retest" \
  --outdir "outputs/runs/issue33_2026__job_${NEW_JOB_ID}" \
  --json
```

Same Issue, different PDF version:

```bash
python tools/import_issue.py \
  "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf" \
  --target v3 --new-version --reason "Official Gazette republished the PDF with changed content" \
  --outdir "outputs/runs/issue33_2026__job_${NEW_JOB_ID}" \
  --json
```

Existing V5 invocation must now name its target explicitly:

```bash
python tools/import_issue.py "<URL>" --target v5
```

---

## 5. V3 execution sequence

### 5.1 Preflight and mode branching

No network request, OCR, manifest mutation or V3 write may occur until preflight succeeds.

First validate the target and mutually compatible options. Then branch by operation. These are **independent preflight paths**: a replay-only invocation must never execute the source-validation or filename-derived Issue-key steps from the full-import path, and a full import must never execute the replay-only manifest checks.

#### Path A — Full V3 import

1. Require a source URL or local PDF.
2. Validate the source using §9 before any network access.
3. Derive the Issue key from the original source filename, or validate the explicitly supplied `--issue-key` under §4.5.
4. Resolve `--v3-db` and verify that it is an existing writable V3 operational database with the expected tables.
5. Reject paths under `data/baseline/` and any frozen/read-only snapshot.
6. Validate the managed input directory and unique output directory. The output directory must not already exist.
7. Reject any `--pages` value other than the literal `all` for an ingestion that will proceed to V3 replay. A range is rejected even when it happens to cover the whole PDF in V2.4.
8. Validate `--name` as a sanitised `.pdf` basename.
9. If `--dry-run`, print the planned stages and exit without acquiring the lock, downloading, OCR or writing to V3.
10. Acquire the V3 import lock (§9.7). If unavailable, return `import_locked` without downloading or OCR.

#### Path B — V3 replay-only

Replay-only is a separate branch. It does **not** require or accept a source URL/local file and must not run source URL validation or filename-based Issue derivation. Its Issue identity comes from the manifest, not from a filename or directory name.

1. Require `--outdir`; it must identify an existing run directory.
2. Validate the V3 database as above (read-only is sufficient for dry-run).
3. If `--dry-run`: read and validate `import_manifest.json`, perform every read-only integrity check in §5.2, print the plan and exit without acquiring the lock or writing to V3.
4. For a non-dry-run: acquire the V3 import lock before reading or replaying the run.
5. Read and validate `import_manifest.json` in that run directory.
6. Derive the Issue key from the manifest. If `--issue-key` is supplied, it is only an assertion and must equal the manifest value.
7. Perform every integrity check in §5.2.
8. Replay exactly that run directory; do not download or OCR.

The non-dry-run lock is acquired before the replay-only database write so that two cooperating importer processes cannot race. Dry-run is read-only and does not take the lock; it must never call replay.

#### Path C — V3 diagnostic modes (`--download-only` / `--inspect-only`)

1. Require a source URL or local PDF; replay-only is not applicable.
2. Validate the source under §9.
3. Derive/validate the Issue key under §4.5 when possible; if it cannot be derived, `--issue-key` is required so the manifest and run name are stable.
4. Do not require `--v3-db` to exist and do not open it. If supplied, ignore it for this mode.
5. Validate a new unique `--outdir`; do not reuse an existing run directory.
6. Reject `--reprocess` and `--new-version`; no duplicate/version decision occurs.
7. Do not acquire the V3 import lock.
8. `--download-only`: download, verify and promote the PDF; do not OCR or replay. `--pages` is irrelevant.
9. `--inspect-only`: download, verify and run the existing OCR inspect mode; do not replay or write V3. A partial `--pages` range is allowed here because no V3 evidence is created.
10. Write a manifest with `operation: "download_only"` or `"inspect_only"`, the corresponding `status`, and no V3 provenance/replay fields.
11. Honour the strict `--json` contract in §10.4.
12. If `--dry-run` is supplied, print the plan and exit without downloading, OCR, manifest writes or V3 access.

### 5.2 Replay-only integrity gate

`--replay-only` is supported only for V3 runs created with manifest schema version 2.

Before replay:

1. `import_manifest.json` must exist, parse as a JSON object, have `manifest_version: 2`, `target: "v3"`, and `operation` either absent (early v2 manifest) or one of `full_import`, `replay_only`. Reject `download_only` and `inspect_only` manifests. `status` must be `running`, `failed` or `completed`; reject diagnostic statuses.
2. `issue_key` must be a canonical `YYYY-NN` value. An optional CLI `--issue-key` must match it exactly. The manifest is authoritative. If the run-directory name contains a parseable `issueNN_YYYY` prefix, that prefix is a consistency check only: a mismatch is an error. If the directory name is not parseable, no directory check is performed and the manifest remains authoritative.
3. `run_dir` in the manifest must resolve to the selected run directory; path mismatch is an error.
4. `run_report.json` must exist and include `source_pdf_sha256`.
5. The report SHA-256 must equal the manifest `pdf_sha256`.
6. The manifest `pdf_path` must exist as a regular non-symlink file. Recompute its SHA-256 and require an exact match with `pdf_sha256`. If the PDF is missing or changed, stop and require restoration of the original PDF or a new OCR run.
7. `documents.csv` and `tb_rows.csv` must both exist as regular non-symlink files. Header-only/zero-row `tb_rows.csv` is valid.
8. `run_report.json`, `documents.csv`, `tb_rows.csv` and `import_manifest.json` must resolve inside the run directory. Reject traversal and symlink escape.
9. Check the evidence fingerprint in §6.5 during replay; do not rely only on the PDF hash.
10. The `--v3-db` argument is authoritative. If manifest `v3_db` differs, write a warning to the manifest/log; do not silently change the selected database.
11. Perform a read-only duplicate check before replay:
    - if the manifest hash already exists under a different Issue and the manifest `decision_type` is not one of `reprocess` or `new_version`, reject with `already_imported_content_conflict`;
    - if the hash exists under the same Issue, allow the retry/repair; the run-identity lookup decides whether a new run is needed;
    - never create a second source document silently.

If `--dry-run` is supplied, perform the read-only manifest and integrity checks, print the plan, and do not acquire the lock or write to V3.

Legacy runs without a V2 manifest are not eligible for Importer `--replay-only`. Use `tools/replay_runs_to_v3.py` directly for controlled legacy backfill.

### 5.3 Stage 1 — Download to temporary storage

For V3, do not promote the download to its final managed input filename until after the duplicate/version gate.

1. Create a unique temporary file under the managed input directory, e.g. `.issue33_2026.<job-id>.part`.
2. Stream the response in bounded chunks. Do not load the full PDF into memory.
3. Enforce the 250 MB maximum while streaming, regardless of `Content-Length`.
4. Verify the `%PDF-` signature.
5. Compute SHA-256 and byte size during the stream.
6. On any failure, remove the partial file and record a stage failure. Never leave a partial file at the final PDF path.

Do not promote the temporary file at this stage. Promotion happens only after the duplicate/version gate in §5.4 authorises the ingestion.

For `--download-only` or `--inspect-only`, no V3 duplicate gate or replay takes place. These diagnostic modes are defined separately in §5.1; they do not require a writable V3 database, do not acquire the V3 import lock, and reject `--reprocess`/`--new-version`. They may promote the verified PDF to managed input storage and must mark the result as non-ingestion in the manifest. For V3 diagnostics, `--outdir` must be a new unique directory; reusing an existing run directory is not supported.

### 5.4 Stage 1b — Duplicate/version gate and PDF promotion

Run the gate after computing the temporary PDF hash and **before** promoting the file or starting OCR. Use the full 64-character SHA-256 against `source_documents.sha256`; never use `document_key` or a truncated hash as the content identity.

Apply the exact decision table in §8.2–§8.3.

#### If the result is duplicate or requires an operator decision

- Delete only the temporary download.
- Do not create the requested run directory.
- Write a unique result manifest under `ROOT/output_import_results/<job-id>/`.
- If a final input PDF with the same hash already exists, leave that existing file intact; the temporary file is the only file deleted.
- Stop before promotion and before OCR.

#### If processing is approved

Only after the gate authorises the ingestion, promote the verified temporary file into the managed input directory with true no-clobber semantics. A check followed by `os.replace()` is not sufficient: another process, another V3 database importer, or a diagnostic run can create the destination between the check and the replace.

1. Prefer an atomic create-if-absent primitive: `os.link(temp, final)` then `os.unlink(temp)` on the same filesystem. If `final` already exists, `os.link` raises `FileExistsError`; do not overwrite.
2. On `FileExistsError`, re-hash `final`:
   - same SHA-256 and a regular non-symlink file -> delete only the temporary file and reuse `final`;
   - different bytes or a symlink -> choose `<stem>__<sha256[:8]>.pdf` and retry the no-clobber promotion;
   - if the versioned path also collides, extend the suffix with more hash characters or a UUID and retry.
3. If the filesystem does not support hard links, reserve the final path with `os.open(final, O_CREAT | O_EXCL)` and copy the verified bytes into that reserved file, then fsync and delete the temporary file. Never use `os.replace()` onto an existing final path.
4. Bound the retry loop (for example, 10 attempts). If a unique destination cannot be reserved, fail with a clear `PdfPromotionError`; preserve the temporary file under a clearly marked `.unpromoted` name for diagnosis rather than overwriting anything.
5. Start OCR only after promotion succeeds.

The ordering is mandatory: **temporary download -> validation -> duplicate/version gate -> no-clobber promote or delete temp -> OCR**. A duplicate result must never promote the temporary file, and an approved result must never OCR before promotion. The same no-clobber rule applies to normal imports, diagnostics and importers using different V3 databases.

### 5.5 Stage 2 — OCR

Reuse the existing OCR/parser command and output format.

- Run the current OCR pipeline using existing defaults unless supported OCR options are explicitly supplied.
- A full V3 import (including `--reprocess` and `--new-version`) must use the literal `--pages all`. A range is rejected even if it happens to cover the whole PDF in this release, because preflight does not establish page coverage before download.
- This is required for page-count integrity: replay must validate every page and set `source_documents.page_count = pdf_pages`. Without full-page records, a partial OCR result could be mistaken for a complete document.
- Partial pages are allowed only for `ocr-only` and V3 `--inspect-only` diagnostics, which do not create V3 evidence. A future partial-replay feature must add explicit OCR coverage metadata before it can relax this rule.
- The V3 importer must pass the approved full-page-record OCR mode (for example, `--full-page-records`) so `documents.csv` contains one explicit row for every PDF page, including non-TB pages (`skipped`) and failed pages (`failed`). This is an internal importer-to-OCR setting, not a new user-facing Importer option.
- Capture OCR stdout and stderr in the run-specific `import.log`; preserve useful output on stderr for interactive CLI use.
- Record the exact command and OCR options in the manifest, excluding secrets.
- Stop immediately on a non-zero OCR exit code.
- Require `run_report.json`, `documents.csv` and `tb_rows.csv` before replay. `tb_rows.csv` may contain only a header.
- Require `run_report.source_pdf_sha256` to equal the downloaded PDF SHA-256.
- Require `run_report.source_pdf` to identify the same source PDF, or use the manifest path when the report stores a path that is no longer directly resolvable.
- Do not replay if an artifact is missing, unreadable, outside the run directory where applicable, or inconsistent with the manifest.

#### 5.5.1 Page-coverage validation

`--pages all` is a request, not proof that every PDF page was observed. Before replay, validate actual page coverage against `run_report.pdf_pages`.

The current OCR pipeline writes `documents.csv` only for pages it classifies as TB pages; non-TB pages are skipped and never appear in the file. That is enough for V5 but **not** enough for V3 page-count integrity. For a full V3 import, the importer must invoke OCR with an approved full-page-record mode (for example, `--full-page-records`). In that mode, OCR writes exactly one `documents.csv` row for every page in `1..pdf_pages`:

- TB page successfully processed: `ocr_status = "completed"`;
- non-TB page deliberately skipped: `ocr_status = "skipped"`, with an explicit skip reason such as `not_tb_page`;
- page processing failed: `ocr_status = "failed"`, with a useful error/reason;
- every page must have a record; a missing record is a coverage error.

The full-page-record mode is additive and must not change the default V5 OCR output. It is a required OCR-output change for V3 ingestion, not a change to OCR recognition models or thresholds. Adding a `skip_reason` (or equivalent) column to `documents.csv` is allowed additively; do not remove or repurpose existing columns.

For a new V3 Importer run (`manifest_origin = written`, strict mode):

1. `run_report.pdf_pages` must be present and a positive integer.
2. `documents.csv` must contain exactly one row for every page `1..pdf_pages`.
3. Every `documents.csv.pdf_page` value must be an integer in `1..pdf_pages`; duplicate page numbers are invalid; out-of-range pages are invalid.
4. A failed or skipped page must still have an explicit `documents.csv` row with `ocr_status` set to `failed` or `skipped`. Missing rows are invalid; do not silently drop pages.
5. Missing, duplicate, out-of-range or unparseable page records stop the import before replay. Write a failed manifest (`page_coverage_status = "invalid"`, `error_type = "PageCoverageError"`) and preserve the OCR artifacts for diagnosis. Do not write to V3.
6. `source_documents.page_count` must equal the validated `run_report.pdf_pages`, not `len(page_numbers)`. If a reprocess reuses an existing source document whose page count was wrong, update it to the validated `pdf_pages`; do not change the document identity.
7. Store `config_json.pdf_pages`, `config_json.ocr_pages_observed` and `config_json.ocr_page_coverage = "full"` on the extraction run.
8. Replay must independently repeat the strict validation; it must not trust the importer's manifest alone.

The failed-page policy is explicit: a page with `ocr_status = failed` or `skipped` is recorded as a document page and page extraction with that status, but it produces no extracted rows. It is not a successful OCR page and must not be counted as one.

Record these counts on the extraction run and in the manifest:

- `ocr_pages_observed`: all page records, including failed/skipped;
- `ocr_pages_successful`: pages with `ocr_status = completed`;
- `ocr_failed_pages`: pages with `ocr_status = failed`;
- `ocr_skipped_pages`: pages with `ocr_status = skipped`.

`ocr_page_coverage = "full"` means every page has a record, not that every page was successfully OCR'd. If `ocr_failed_pages > 0`, `extraction_runs.run_status` must be `partial` (the schema allows it), and the manifest must report the failed count. The importer must not describe the run as fully successful OCR.

For a controlled legacy replay (`manifest_origin = reconstructed` or no v2 manifest), the existing run artifacts are historical and may only contain the pages that were actually OCR'd. Legacy replay must not fabricate missing page records or extracted rows. Instead:

- use `run_report.pdf_pages` when valid as the true PDF total for `source_documents.page_count`;
- store the observed OCR page count and `config_json.ocr_page_coverage = "legacy_partial"`;
- preserve the historical `page_count` in `config_json.legacy_ocr_page_count` before correcting it;
- if `pdf_pages` is missing/invalid, keep the historical page count and set `ocr_page_coverage = "unknown"`;
- do not apply strict coverage rejection to legacy runs.

This distinction lets Workbench tell "PDF has 20 pages, all observed" from "PDF has 20 pages, only 5 historically OCR'd" without inventing evidence.

### 5.6 Stage 3 — Write the pre-replay manifest

Before starting V3 replay, write the complete manifest atomically using §10.4.

At minimum include:

- `manifest_version: 2`;
- `manifest_origin: "written"`;
- `target: "v3"`;
- `operation: "full_import"`;
- `job_id`;
- `source_kind`, `source_url`, `source_filename`;
- canonical `issue_key`;
- `pdf_path`, `pdf_sha256`, `pdf_bytes`, `pdf_pages`;
- `ocr_pages_observed`, `ocr_pages_successful`, `ocr_failed_pages`, `ocr_skipped_pages`, `page_coverage_status`, `ocr_page_coverage`;
- absolute `run_dir` and `v3_db`;
- stage status fields;
- decision type/reason when applicable;
- `started_at`, stage timestamps and error fields;
- legacy compatibility aliases required by §11.4.

Update the manifest after each stage boundary and before returning a stage failure.

### 5.7 Stage 4 — V3 replay

Call the replay tool with an argument array and `cwd=ROOT`; never use `shell=True`.

```bash
python tools/replay_runs_to_v3.py \
  --v3-db <absolute-v3-db-path> \
  --run-dir <absolute-run-directory> \
  --issue-key YYYY-NN \
  --no-v5-lookup
```

Requirements:

- pass only the V3 database path;
- pass the Issue key explicitly from the validated manifest;
- `--no-v5-lookup` is mandatory and must skip all V5 DB access;
- do not call `tools/build_database.py`, `name_registry.py refresh-banks` or `import_user_standard.py map`;
- parse the single-run JSON object from stdout; progress goes to stderr/log;
- validate all required replay result fields, the evidence fingerprint and page coverage (`pdf_pages`, `ocr_pages_observed`, `ocr_page_coverage`);
- the replay tool must independently repeat strict page-coverage validation for new V3 runs and reject missing/duplicate/out-of-range pages before writing V3;
- for strict new runs, create a `document_pages` row and a `page_extractions` row for every page `1..pdf_pages`, including `skipped`/`failed` pages; skipped/failed pages produce no extracted rows;
- set `extraction_runs.run_status = 'partial'` when any page failed; do not report a failed page as successful;
- for strict new runs, `source_documents.page_count` must be the validated `pdf_pages`, never `len(page_numbers)`;
- for legacy runs, follow the documented `legacy_partial` / `unknown` policy in §5.5.1 and §6.8;
- a non-zero exit code or invalid JSON/result is a failure;
- update the manifest to completed only after replay success and result validation.

### 5.8 Stage 5 — Final result

Success means only:

```text
V3 raw OCR evidence imported — awaiting page classification
```

It does not mean a financial statement is identified, reviewed, adopted or published.

The result includes target, operation, Issue key, PDF filename/hash, run directory, manifest path, V3 DB, replay run ID, page count and row counts where available.

---

## 6. V3 data boundary, identity and provenance

### 6.1 Raw-evidence boundary

Newly replayed rows remain:

```text
row_disposition = 'unassigned'
statement_id = NULL
statement_extraction_id = NULL
statement_order = NULL
```

The Importer/Replay must not:

- classify pages as financial statements;
- decide bank/entity identity;
- create `statements` or `statement_extractions` based on OCR guesses;
- assign raw rows to statements;
- approve Standard Item mappings;
- mark statements reviewed;
- bind or adopt an extraction;
- publish data.

Only the later Workbench confirmation flow may perform the relevant page classification and statement preparation.

### 6.2 Issue identity

Canonical Issue identity is `YYYY-NN`, stored in `gazette_issues.issue_key`.

Resolution order for a new V3 run:

1. derive the Issue key from the original source filename when possible;
2. if `--issue-key` is supplied, it must match the derived value; if the filename cannot establish the Issue, `--issue-key` is required;
3. for replay-only, use the manifest Issue key; an explicit `--issue-key` is only an assertion;
4. for legacy direct replay only, fall back to the existing run-directory regex.

If the source filename yields an Issue key and the explicit value disagrees, reject. Never silently trust a mismatching CLI value.

For V3 replay-only, the manifest remains authoritative. The run-directory prefix is only a consistency check; the directory name must not override the manifest or establish a different Issue. The legacy directory-regex fallback applies only to direct legacy replay, not to Importer `--replay-only`.

### 6.3 Replay result contract

For one `--run-dir`, replay returns one JSON object:

```json
{
  "status": "ok",
  "run_id": "...",
  "run_dir": "/abs/outputs/runs/issue33_2026__job_a1b2c3d4",
  "issue_key": "2026-33",
  "gazette_issue_id": "...",
  "source_document_id": "...",
  "pages": 11,
  "page_extractions": 11,
  "pdf_pages": 11,
  "ocr_pages_observed": 11,
  "ocr_pages_successful": 11,
  "ocr_failed_pages": 0,
  "ocr_skipped_pages": 0,
  "ocr_page_coverage": "full",
  "rows_inserted": 64,
  "rows_skipped": 0,
  "already_present": false,
  "evidence_fingerprint": "..."
}
```

Status values:

- `ok`: this exact run/evidence was replayed successfully for the first time;
- `already_present`: this run identity already exists with the same fingerprint; no duplicate raw evidence was added;
- `failed`: replay failed and contains `error_type` and `error_message`.

Exit codes: 0 for `ok` or `already_present`; 1 for a replay failure; 2 for usage/preflight errors.

Do not infer success from `rows_inserted == 0` alone. Use `status` and `already_present`.

### 6.4 Replay run identity

Before creating an extraction run, resolve the run identity in this order:

1. existing V3 `extraction_runs` row whose `run_report_uri` resolves to the current `<run_dir>/run_report.json`;
2. legacy V5 `runs` table only if V5 lookup is enabled;
3. deterministic stable-ID fallback for a genuinely new run.

For new V3 imports, `--no-v5-lookup` means step 2 is omitted entirely.

A replay-only retry must find the existing V3 extraction run by report path and reuse its run ID. A normal new OCR attempt must use a new run directory and a new run ID.

### 6.5 Evidence fingerprint

The existing row-count guard does not detect same-count content changes. Add an evidence fingerprint to `replay_runs_to_v3.py`.

Version 1 algorithm:

```text
parts = ["import-evidence-v1"]
for name in ("run_report.json", "documents.csv", "tb_rows.csv"):
    if file exists and is a regular non-symlink file:
        parts.append(name + "\0" + sha256_hex(file bytes))
    else:
        parts.append(name + "\0MISSING")
evidence_fingerprint = sha256_hex("\0".join(parts).encode("utf-8"))
```

For new Importer V3 runs, all three files are required, so `MISSING` is not accepted. The missing marker exists only for legacy replay compatibility.

Store these keys in `extraction_runs.config_json`, preserving all existing OCR configuration keys:

```json
{
  "evidence_fingerprint": "<64 lowercase hex>",
  "evidence_fingerprint_version": 1
}
```

Rules:

- On a new run, compute and store the fingerprint when creating the extraction run.
- On an existing run with a fingerprint: if it matches, replay idempotently; if it differs, raise a distinct `EvidenceChangedError`, roll back the run transaction and exit non-zero. Never skip changed evidence silently and never overwrite old evidence.
- If the existing run has no fingerprint (legacy run), compute and store one on first controlled replay, preserving existing `config_json` keys. This establishes a baseline from the artifacts available at the time of backfill; it does not prove that those legacy files are identical to the files used during the historical import.
- If `config_json` is invalid JSON, fail rather than replace it with a new object.
- A changed row count continues to fail; the fingerprint additionally catches same-count changes to labels, amounts, parse statuses, page metadata and other bytes in the three files.
- `import_manifest.json` is intentionally excluded, so status updates do not change the fingerprint.
- A new OCR run must use a new directory and a new run identity.

### 6.6 Source provenance

Persist provenance without changing the V3 schema:

| Provenance | V3 storage | Rule |
|---|---|---|
| Issue key | `gazette_issues.issue_key` | Canonical `YYYY-NN` |
| Issue-level source URL | `gazette_issues.source_url` | Set on insert; backfill only when `NULL`; never overwrite a non-NULL value |
| Document-level source URL | `source_documents.source_url` | Exact original PDF URL; set on insert; backfill only when `NULL`; never overwrite |
| PDF SHA-256 | `source_documents.sha256` | Full hash; content identity |
| PDF size | `extraction_runs.config_json.source_pdf_bytes` | Integer from manifest/report |
| PDF total pages | `source_documents.page_count` | Validated `run_report.pdf_pages` for new runs; legacy correction only in controlled backfill |
| Page coverage | `extraction_runs.config_json.ocr_page_coverage` | `full`, `legacy_partial` or `unknown` |
| Observed OCR pages | `extraction_runs.config_json.ocr_pages_observed` | Count of `documents.csv` page rows; for strict runs must equal `pdf_pages` |
| OCR page outcomes | `extraction_runs.config_json.ocr_pages_successful` / `ocr_failed_pages` / `ocr_skipped_pages` | Explicit counts; failed pages are not successful OCR |
| Extraction status | `extraction_runs.run_status` | `partial` when any page failed; `completed` otherwise |
| PDF local path | `source_documents.storage_uri` | Absolute local path, not a network URL |
| Original filename | `source_documents.original_filename` | Sanitised filename |
| Acquisition time | `source_documents.acquired_at` | Manifest download completion time when available; otherwise replay time for legacy run |
| OCR run identity | `extraction_runs.run_id` | Resolved per §6.4 |
| OCR report | `extraction_runs.run_report_uri` | Absolute path to `run_report.json` |
| Import manifest | `extraction_runs.config_json.import_manifest` | Absolute path; `null` for reconstructed legacy runs |
| Manifest origin | `extraction_runs.config_json.manifest_origin` | `written` or `reconstructed` |
| Run directory | `extraction_runs.config_json.run_dir` | Absolute path |
| Evidence fingerprint | `extraction_runs.config_json.evidence_fingerprint` | Versioned per §6.5 |

`gazette_issues.source_url` is an Issue-level convenience field. When multiple PDF versions exist for one Issue, it retains the first non-NULL URL; the exact URL for each version is stored on its `source_documents` row.

If an existing source document's `storage_uri` is missing or unreadable and a reprocess promotes a PDF with the same hash, update `storage_uri` to the promoted path; do not change document identity. If the hash differs, that is a new version, not a reprocess.

Do not store secrets or URL query values. V3 URL policy rejects query strings and fragments.

### 6.7 No V3 schema migration in V2.4

Do not add a file-size column or migration in this upgrade. PDF size is stored in:

- `import_manifest.json` as `pdf_bytes`;
- `run_report.json` as `source_pdf_size` where supported;
- `extraction_runs.config_json.source_pdf_bytes`.

This is sufficient for provenance and Workbench display. If SQL-level file-size filtering/reporting becomes a real requirement, propose a separate database migration after reviewing the data model.

### 6.8 Legacy manifest and provenance backfill

The replay tool must:

- read the legacy `source` field as a source URL;
- accept a missing `import_manifest.json` when the core legacy OCR artifacts exist;
- reconstruct provenance from `run_report.json` and mark `manifest_origin = reconstructed`;
- not write a fabricated manifest into a legacy run directory;
- leave the source URL `NULL` for runs with no source URL evidence;
- read the eight legacy manifest `source` values during deliberate backfill;
- backfill PDF size into `config_json.source_pdf_bytes` from `run_report.json` where present;
- update a source URL only when the V3 field is currently `NULL`;
- backfill `config_json.pdf_pages` and `config_json.ocr_pages_observed` from `run_report.pdf_pages` and the observed `documents.csv` rows;
- backfill `config_json.ocr_pages_successful`, `ocr_failed_pages` and `ocr_skipped_pages` from the observed page statuses;
- correct `source_documents.page_count` to the validated `run_report.pdf_pages` where available, preserving the old value in `config_json.legacy_ocr_page_count`;
- mark legacy coverage as `config_json.ocr_page_coverage = "legacy_partial"` (or `"unknown"` when `pdf_pages` is unavailable);
- set `extraction_runs.run_status = "partial"` when `pdf_pages` is valid and the observed page count is lower, or when any observed page failed; leave the existing status unchanged when coverage is `unknown`;
- preserve all existing rows and all existing `config_json` keys.

Legacy backfill must not fabricate missing `document_pages`, page extractions or extracted rows. A page count larger than the number of observed `document_pages` is expected for legacy partial runs and is the reason the coverage field exists.

A controlled replay of the existing 14 runs must insert no new raw evidence rows when their evidence is unchanged. It may update provenance/page_count metadata as above. Run it on a backup/copy first, with no Importer active. The direct replay command does not acquire the Importer's advisory lock.

### 6.9 Macau currency/unit domain rule

For in-scope Macau bank statements:

```text
currency = MOP
unit_scale = 1
```

This is a project/domain rule. The Importer must not use it to infer or approve Statement identity. The later statement-confirmation workflow applies it with project-rule provenance, not as per-PDF manual verification.

---

## 7. Run directories and result manifests

### 7.1 V3 run directory

Use unique run directories that preserve the existing issue prefix:

```text
ROOT/outputs/runs/issueNN_YYYY__job_<job_id>
```

`job_id` is allocated per §7.4.

Requirements:

- never overwrite an existing run directory;
- use a generated unique job/run identifier;
- do not derive a filesystem path from an untrusted URL;
- sanitise any filename/stem used in a path;
- allow an explicit `--outdir` only after validation;
- require `--outdir` to be inside `ROOT/outputs/runs` for normal operation unless `--allow-outside-output` is supplied by a trusted CLI operator;
- reject `..` traversal, symlink escape and a symlink as the final destination;
- preserve failed run directories and logs.

The run-directory name is for compatibility and display. The manifest Issue key and replay run ID are authoritative logical identities.

### 7.2 Replay-only directory

Replay-only reuses the exact existing run directory. It does not create a new run directory, rename files or re-run OCR.

### 7.3 Unique result directories

Failures and operator-decision results can occur before the normal run directory exists. Write a separate result manifest:

```text
ROOT/output_import_failed/<job-id>/import_manifest.json
ROOT/output_import_results/<job-id>/import_manifest.json
```

Use `output_import_failed` for stage failures and `output_import_results` for `already_imported`, content conflict, new-version-required and `import_locked` outcomes.

Never reuse one shared `output_import_failed/import_manifest.json`; concurrent or consecutive operations must not overwrite one another.

For duplicate/decision/lock results, do not create the requested run directory. Include the intended `run_dir` in the result JSON/manifest as a planned path.

### 7.4 Job ID and default run directory

Every V3 invocation allocates a `job_id` before any filesystem mutation.

- If `--outdir` is supplied, a trailing `__job_<id>` segment is a **preferred label**, not proof of uniqueness. Sanitise it and treat collisions as a normal race.
- If `--outdir` is omitted, generate `job_id = uuid4().hex[:8]` (extend on collision) and use `ROOT/outputs/runs/issueNN_YYYY__job_<job_id>`.
- Reserve the run directory with `mkdir(exist_ok=False)`. If it already exists, regenerate the ID or fail; never reuse or overwrite it.
- Reserve every result directory with `mkdir(exist_ok=False)`. If `ROOT/output_import_results/<job_id>/` or `ROOT/output_import_failed/<job_id>/` already exists, append a unique suffix (for example, `<job_id>-<uuid4().hex[:4]>`) and retry, or fail with a clear collision error. Never write into a directory already owned by another invocation.
- Use the actual, successfully reserved `job_id` in the manifest and the `--json` result. Do not report the preferred label if a suffix was added.
- Two invocations with the same `--outdir` must not share a result directory. The normal-import run directory is also no-clobber; a race that loses the reservation fails without overwriting.

For replay-only, `--outdir` is required and the run directory already exists. `job_id` is the preferred label from that path, but the result directory still uses the atomic `mkdir(exist_ok=False)` reservation above; the run directory itself is not renamed.

---

## 8. Duplicate handling, versions, idempotency and retries

### 8.1 Operator-decision flags

`--reprocess` and `--new-version` are mutually exclusive. Both require a non-empty `--reason` after trimming whitespace.

- `--reprocess` authorises another OCR extraction for the **same PDF content**. It never authorises different bytes for an Issue that already has a document.
- `--new-version` authorises different PDF content for an Issue that already has a document. It never authorises another run for identical content.
- Neither option overwrites an existing PDF, source document, extraction run or extracted row.
- Both options are invalid with `--download-only`, `--inspect-only` or `--replay-only`; reject them rather than silently ignoring the decision flag.

Record `decision_type` and `decision_reason` in the manifest and replay log.

### 8.2 Decision table

| Existing state for target Issue | Hash relationship | Default result | Explicit decision |
|---|---|---|---|
| Target Issue has a document with this hash | Same Issue, same content | `already_imported` | `--reprocess --reason`: reuse that source document and create a new extraction run |
| Target Issue has documents, none has this hash | Same Issue, different content | `new_source_version_required` | `--new-version --reason`: create a new source document; preserve old version |
| Target Issue has no documents; another Issue has this hash | Different Issue, same content | `already_imported_content_conflict` | `--reprocess --reason`: create a source document under the target Issue and continue |
| No document for target Issue and no matching hash anywhere | New content | Normal import | None required |

Edge case: the target Issue has an older document, none matches hash H, and H exists under another Issue. `--new-version` creates the new same-Issue version, so the resulting same-hash rows across Issues are expected and must be tested. `--reprocess` remains invalid here because the target Issue's content is changing.

### 8.3 Decision algorithm and precedence

After hashing the temporary PDF, but before promotion or OCR:

1. Query all `source_documents` with the full SHA-256.
2. Query all `source_documents` associated with the target Issue.
3. If the target Issue already has one or more documents and none has this hash:
   - result is `new_source_version_required`;
   - `--reprocess` does not bypass this check;
   - require `--new-version --reason` to proceed.
4. If the target Issue has a document with this hash:
   - without `--reprocess`, return `already_imported` (exit 0);
   - with `--reprocess --reason`, reuse the existing `source_documents` row and create a new extraction run;
   - reject `--new-version`, because the bytes are identical.
5. If the target Issue has no documents, but another Issue has this hash:
   - without `--reprocess`, return `already_imported_content_conflict` (exit 3);
   - with `--reprocess --reason`, create a new source document under the target Issue and continue;
   - reject `--new-version`, because this is not a same-Issue version change.
6. If no matching hash exists and the target Issue has no documents:
   - proceed normally only when neither `--reprocess` nor `--new-version` is supplied;
   - reject `--reprocess` and `--new-version` with a clear message, because there is no prior content or version to act on.
7. Reject `--reprocess` and `--new-version` together, and reject either flag with a missing/empty reason.

A same-Issue reprocess must not increase the number of `source_documents` rows. A cross-Issue reprocess may create a second row with the same SHA because the Issue identity differs. A `--new-version` import can also create a same-hash multi-row case when the new version's content matches a document already stored under another Issue; this is legitimate only with `--new-version --reason`, and the other Issue's rows must remain unchanged.

### 8.4 Source document identity

- Use the full SHA-256 for duplicate decisions.
- Preserve the existing `document_key` convention for new source documents: `issue_key + ":" + sha256[:16]`.
- Same Issue + same hash reuses the existing source document.
- Same Issue + different hash under `--new-version` creates a new source document with its own key and storage path.
- Different Issue + same hash under explicit `--reprocess` creates a source document under the target Issue.
- Never delete, merge or rewrite existing source-document rows.

V3 has no current-version selector for source documents. The Importer must not choose which version is current. The later page-classification/Statement-confirmation workflow must make that decision explicitly.

### 8.5 Replay idempotency

Idempotency is based on both run identity and evidence fingerprint:

- same `run_report_uri` + same fingerprint: `already_present`, no duplicate rows;
- same run path + changed fingerprint: `EvidenceChangedError`, transaction rollback and failure;
- different run path: a new OCR run identity, even when the PDF hash is the same; duplicate policy must authorise it first;
- changed row count: existing row-count check also fails;
- do not update old extracted-row values in place.

### 8.6 Retry matrix

| Failure/result | Correct retry |
|---|---|
| Preflight failure | Correct the URL, flags, path or DB; no ingestion artifacts should exist |
| Import lock held | Wait for the active process; retry the command |
| Download failed | Retry download; no OCR or replay should exist |
| `already_imported` | No retry; use `--reprocess --reason` only when another OCR run is justified |
| `new_source_version_required` | Use `--new-version --reason`, or stop |
| Cross-Issue content conflict | Use `--reprocess --reason` only when cross-Issue reuse is justified |
| OCR failed | Preserve failed run; retry OCR in a new run directory |
| OCR completed, replay failed | Use `--replay-only` against the same run directory; do not rerun OCR |
| Replay succeeded | A normal repeat returns `already_imported`; an intentional new extraction requires `--reprocess --reason` |

---

## 9. URL, download, filesystem and concurrency safety

### 9.1 Scope

The hardened URL and filesystem policy applies to V3 ingestion.

- V5 preserves existing URL/local-file behaviour, including HTTP and the localhost test fixture.
- OCR-only preserves V5-compatible download behaviour.
- Do not change the shared V5 `fetch_pdf()` in a way that breaks legacy behaviour. Use a dedicated V3 downloader or target-specific policy.

### 9.2 URL policy for V3

- HTTPS only.
- Use an explicit configurable allowlist of official Gazette hostnames. The currently verified host is `images.bo.dsaj.gov.mo`; Phase 0 must check real official URLs before adding any other host.
- Configure the allowlist with `GAZETTE_ALLOWED_HOSTS` as comma-separated exact hostnames. Default when unset: `images.bo.dsaj.gov.mo`. Workbench must not override it. Do not use suffix matching or wildcards.
- Do not allow wildcard subdomains by default.
- Reject URL user-info, IP-literal hosts, query strings and fragments.
- Resolve hostnames and reject private, loopback, link-local, multicast, reserved and unspecified IP addresses.
- Validate every redirect target under the same scheme/host/address policy.
- Limit redirects (recommended maximum: 5).
- Do not forward credentials or sensitive headers across redirects.
- Use a custom `HTTPRedirectHandler`; `urllib.request.urlopen()` automatic redirect handling must not bypass redirect validation.
- Do not trust `Content-Type` alone. Verify the PDF signature and enforce the size limit while streaming.
- Revalidate DNS at connection time where feasible. Document the residual DNS-rebinding limitation of a local trusted tool; do not claim complete SSRF protection unless the connection is pinned to the validated address.

### 9.3 Download limits and atomic promotion

- Keep the 250 MB maximum.
- Enforce it during streaming, even with missing or false `Content-Length`.
- Use bounded connect/read timeouts.
- Write to a unique `.part` file in the managed input directory.
- Compute size and SHA-256 while streaming.
- Validate `%PDF-` before duplicate checking.
- Do not promote the file until the duplicate/version gate authorises processing (except non-ingestion diagnostic modes).
- Promote only with an atomic no-clobber operation (`os.link` + unlink, or `O_CREAT | O_EXCL` + copy). Never use check-then-`os.replace()`; it can overwrite a file created by another importer or diagnostic run.
- Delete incomplete temporary files on failure and delete the temporary file after a duplicate/decision result.
- Never overwrite an existing different PDF. Use a SHA-suffixed filename when necessary.

### 9.4 Filesystem safety

- V3 input defaults to `ROOT/input`. An explicit V3 `--input-dir` must resolve to `ROOT/input` or a subdirectory of it; otherwise reject. There is no V2.4 override for V3 input outside the managed root.
- V3 run output defaults to `ROOT/outputs/runs`.
- Validate resolved paths against managed roots to prevent traversal and symlink escape.
- Reject a symlink as a final destination.
- Never use a raw URL path as a filesystem path.
- Use generated identifiers for job/run directories.
- `--allow-outside-output` is a trusted CLI override and must not be exposed to an untrusted Workbench request.

### 9.5 Local source files

V3 accepts a local PDF only with `--allow-local-source`.

- Require a regular `.pdf` file; reject a symlink as the source path.
- Copy it to the managed input directory using a sanitised basename and the same temporary-file/hash/promotion procedure.
- Compute and record SHA-256 and byte size.
- Record `source_kind = "local_file"` and `source_url = null`.
- Do not assume local file metadata is an official Gazette URL.

### 9.6 Process safety

- Use `subprocess.run()` or `Popen()` with argument arrays.
- Never use `shell=True`.
- Run OCR and replay with `cwd=ROOT` and a controlled environment.
- Capture child stdout/stderr separately or combine them only into the log; in `--json` mode all child output goes to stderr/log.
- Record commands (excluding secrets), stage names and exit codes in the manifest.
- V3 replay must use `--no-v5-lookup`.

### 9.7 V3 import lock

Two concurrent Importer processes must not both pass the duplicate gate and start OCR for the same V3 DB.

Implement a POSIX advisory lock:

```text
<resolved-v3-db-path>.import.lock
```

Requirements:

- resolve the V3 DB path with `os.path.realpath()` before deriving the lock path, so a symlink and its target (or a relative and absolute path to the same DB) share the same lock file;
- use `fcntl.flock(LOCK_EX | LOCK_NB)`;
- acquire after read-only preflight and before download, duplicate queries, OCR or replay;
- hold through final manifest/result writing;
- release in `finally` on every return/exception path;
- if unavailable, return `status = import_locked`, exit code 3, and do not download or OCR;
- apply the lock to normal V3 import and replay-only;
- V5 and OCR-only do not acquire this V3 lock;
- the lock file may remain after release; the kernel lock state, not file existence, indicates whether it is held.

This lock protects cooperating Importer processes only, not arbitrary external V3 writers. Direct maintenance replay/backfill does not acquire it; run maintenance only when no Importer is active.

This is a macOS/POSIX design. If Windows support is later required, replace `fcntl` with an explicit cross-platform lock implementation.

---

## 10. Failure handling, exit codes and JSON contract

### 10.1 Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success, `already_imported`, `download_only`, `inspect_only` or `planned` |
| 1 | Required stage failure: download, OCR, replay, fingerprint or manifest write |
| 2 | Usage or preflight error |
| 3 | Operator decision required or import lock held |

### 10.2 Overall statuses

Use the following result statuses consistently in manifest and JSON:

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

Do not use `replay_only` as an overall result status. Operation mode and execution result are separate fields.

### 10.3 Operation field

Record the operation independently:

```text
v5_import
full_import
replay_only
download_only
inspect_only
ocr_only
```

For V5, use `target: "v5"`, `operation: "v5_import"`, `issue_key: null`, `job_id: null` and `v3: null`. For OCR-only and V3 diagnostic modes, use `v3: null`; OCR-only may also use `job_id: null`. For preflight failures before a PDF is downloaded, `pdf_sha256` may be `null`.

Example successful replay-only result:

```json
{
  "status": "completed",
  "operation": "replay_only",
  "exit_code": 0
}
```

A completed replay-only operation is a successful replay; it must not be confused with a new full download/OCR import.

### 10.4 Strict `--json` output

With `--json`:

- stdout contains exactly one JSON object and nothing else;
- all progress, command lines, OCR output, replay output and human-readable errors go to stderr and the log;
- success, stage failure, duplicate, operator-decision, lock-held and preflight errors detected after option parsing follow the same object contract;
- argparse usage errors that occur before the parser can determine `--json` may use normal usage output on stderr with exit code 2;
- `--help` is exempt;
- `manifest_path` is `null` only when preflight fails before a result manifest can be written;
- `issue_key` is `null` only when it cannot be established safely;
- `v3` is `null` for V5, OCR-only and V3 diagnostic results;
- `job_id` is always present for V3 results produced after argument parsing; V5 and OCR-only results may set it to `null`. Argparse-level usage errors are the only exception.

Recommended object:

```json
{
  "status": "completed",
  "operation": "full_import",
  "exit_code": 0,
  "target": "v3",
  "job_id": "a1b2c3d4",
  "issue_key": "2026-33",
  "run_dir": "/abs/outputs/runs/issue33_2026__job_a1b2c3d4",
  "manifest_path": "/abs/outputs/runs/issue33_2026__job_a1b2c3d4/import_manifest.json",
  "pdf_sha256": "<64 lowercase hex>",
  "v3": {
    "gazette_issue_id": "...",
    "source_document_id": "...",
    "run_id": "...",
    "already_present": false,
    "evidence_fingerprint": "<64 lowercase hex>",
    "pages": 11,
    "page_extractions": 11,
    "pdf_pages": 11,
    "ocr_pages_observed": 11,
    "ocr_page_coverage": "full",
    "rows_inserted": 64,
    "rows_skipped": 0
  },
  "error": null
}
```

On failure, `error` is `{"type": "...", "message": "..."}`. The JSON `status` must correspond to the exit code and manifest status.

### 10.5 Failure manifest behaviour

- Update the manifest after each stage boundary.
- Record `current_stage`, per-stage status, last exit code, error type/message, and timestamps.
- Preserve PDF, OCR artifacts and logs after replay failure.
- Never report `completed` when a required stage failed.
- Avoid exposing credentials, URL query values or secrets in error text.
- Keep the failure manifest in a unique job-specific directory.

### 10.6 Atomic manifest writing

`write_manifest()` must never truncate a live manifest. Implement:

1. serialise the complete manifest;
2. create a unique temporary file in the same directory;
3. write, `flush()` and `os.fsync()` it;
4. `os.replace(temp, final)`;
5. optionally fsync the containing directory;
6. remove the temporary file on failure.

A crash must leave either the previous valid manifest or the new complete manifest, never partially written JSON.

---

## 11. Import manifest schema v2 (Importer V2.4)

`manifest_version` remains the integer `2`. V2.4 is the Importer/specification version, not a new manifest schema. Replay reads version 2. A future schema change must bump the version and support dual reads.

### 11.1 Canonical example

```json
{
  "manifest_version": 2,
  "manifest_origin": "written",
  "target": "v3",
  "operation": "full_import",
  "job_id": "a1b2c3d4",
  "source_kind": "url",
  "source_url": "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf",
  "source_filename": "anotariais-33-2026.pdf",
  "issue_key": "2026-33",
  "pdf_path": "/abs/input/anotariais-33-2026.pdf",
  "pdf_sha256": "<64 lowercase hex>",
  "pdf_bytes": 3232716,
  "pdf_pages": 11,
  "ocr_pages_observed": 11,
  "ocr_pages_successful": 11,
  "ocr_failed_pages": 0,
  "ocr_skipped_pages": 0,
  "page_coverage_status": "valid",
  "ocr_page_coverage": "full",
  "run_dir": "/abs/outputs/runs/issue33_2026__job_a1b2c3d4",
  "v3_db": "/abs/data/macau_balancete_v3.sqlite",
  "status": "running",
  "current_stage": "v3_replay",
  "download_status": "completed",
  "ocr_status": "completed",
  "v3_replay_status": "pending",
  "started_at": "2026-11-21T00:00:00+00:00",
  "download_finished_at": "2026-11-21T00:01:00+00:00",
  "ocr_finished_at": "2026-11-21T00:10:00+00:00",
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

### 11.2 Stage statuses

```text
pending
completed
reused
skipped
failed
not_applicable
```

These are stage statuses only. Operation values such as `replay_only` must never appear as a manifest or result status; use `status: "completed"` with `operation: "replay_only"` instead.

### 11.3 Manifest invariants

- `manifest_version` is integer `2`.
- For V3 manifests, `job_id` is present and stable for the invocation's run/result directories. V5 manifests may omit it.
- `issue_key` is canonical `YYYY-NN` when known.
- In V3 manifests, `operation` distinguishes `full_import`, `replay_only`, `download_only`, `inspect_only` and `ocr_only`. V5 manifests may omit it or use `v5_import`.
- For a run manifest, `run_dir` is the actual absolute run directory, not a shell fragment. For a result/decision manifest, it may be the intended planned path.
- `v3_replay_status = completed` only after a validated successful replay result.
- V3 `status = completed` means raw evidence only; it does not mean a financial Statement is reviewed.
- `pdf_sha256` is the full 64-character hash when present; it may be null for a result manifest that stopped before download.
- `pdf_pages` is a positive integer for a V3 run manifest.
- New V3 runs use `ocr_page_coverage = "full"` and `source_documents.page_count = pdf_pages`; legacy runs may use `"legacy_partial"` or `"unknown"`.
- `ocr_pages_observed = ocr_pages_successful + ocr_failed_pages + ocr_skipped_pages` for a strict run.
- If `ocr_failed_pages > 0`, `extraction_runs.run_status` is `partial` and the manifest reports the failure; a failed page is never counted as successful.
- `decision_reason` is non-empty whenever a decision flag was used.
- `decision_type` is `reprocess` or `new_version` when applicable.
- The manifest contains no secrets or URL query values.
- Result/decision manifests outside a run directory may have `run_dir` as the intended path and `pdf_sha256`/replay fields as null if the operation stopped before download.

### 11.4 Legacy aliases

Preserve old manifest fields where applicable:

| Legacy key | V2 manifest field | Rule |
|---|---|---|
| `source` | `source_url` | Mirror URL; readers accept `source` in legacy manifests |
| `pdf` | `pdf_path` | Mirror PDF path |
| `outdir` | `run_dir` | Mirror output directory |
| `db` | V5 database path | `null` for V3 |
| `error` | `error_message` | Mirror error message |
| `steps` | Per-command history | Keep for V5 and audit compatibility |
| `started_at` | `started_at` | Retain |
| `finished_at` | `finished_at` | Retain |

Do not remove fields from the old manifests or modify existing manifest artifacts during this upgrade. New V3 manifests may carry the compatibility aliases with V3-appropriate values.

V5 manifests keep their existing legacy top-level shape (`source`, `pdf`, `outdir`, `db`, `steps`, `status`, `started_at`, `finished_at`). New fields such as `manifest_version`, `operation` or `source_url` may be added additively, but no existing V5 key may be removed or repurposed. V5 `--json` uses `target: "v5"`, `operation: "v5_import"`, `issue_key: null`, `job_id: null` and `v3: null`.

---

## 12. Testing requirements

### 12.1 V5 backward compatibility

- V5 tests remain green after naming `--target v5` explicitly (the default changed to V3).
- V5 invocations without `--target` are rejected by the V3 preflight with a hint to add `--target v5`; they must not silently run as V5.
- V5 `--db` and `--input-dir` defaults remain cwd-relative.
- V5 omitted `--outdir` still uses `ROOT/outputs/runs/...` through `output_dir_for()`.
- Explicit relative V5 `--outdir` remains cwd-relative.
- Local PDF and HTTP `127.0.0.1` download tests remain green.
- Existing `--no-db`, `--download-only`, `--no-ocr`, `--inspect-only`, `--dry-run`, `--overwrite-pdf`, `--reuse-output`, publication and mapping behaviours are preserved or covered by explicit compatibility tests.
- OCR-only preserves V5-compatible path and download behaviour.
- V5 `--json` returns `target: "v5"`, `operation: "v5_import"`, `issue_key: null`, `job_id: null` and `v3: null`.
- New V5 manifests keep the legacy keys (`source`, `pdf`, `outdir`, `db`, `steps`, `status`, `started_at`, `finished_at`) and only add fields.

### 12.2 V3 target and database isolation

- V3 target downloads, hashes, OCRs and then replays to V3.
- V3 never calls `tools/build_database.py`, `name_registry.py refresh-banks` or `import_user_standard.py map`.
- V3 rejects explicitly supplied `--db`, V5 publication flags, V5 name mapping flags and V5 bank-refresh flags.
- V3 `--no-db` is rejected with guidance to use `--target ocr-only`.
- V3 `--no-ocr` is rejected with guidance to use `--replay-only`.
- `--no-v5-lookup` demonstrably prevents opening the V5 DB, even if the configured V5 DB path does not exist.
- V3 default paths resolve against `ROOT` when launched from another cwd.
- V3 rejects `--pages 1-5` (and any non-`all` value) before download when the run will be replayed; `ocr-only` and V3 `--inspect-only` may use a range.
- V3 rejects `--reprocess` or `--new-version` on an Issue with no prior document and no matching hash.
- OCR full-page-record mode emits one `documents.csv` row per PDF page; non-TB pages are `skipped`, failed pages are `failed`, and default V5 output remains TB-only.

Page-coverage tests:

| Case | Expected result |
|---|---|
| 20-page PDF, 20 page records | Pass; `source_documents.page_count = 20`; `ocr_page_coverage = "full"` |
| 20-page PDF, missing page 10 | Reject before V3 write; `PageCoverageError` |
| 20-page PDF, page 10 OCR failed but has an explicit row | Accept the record as failed; create the page/page-extraction with failed status and no extracted rows; `run_status = "partial"`; `ocr_failed_pages = 1`; do not count it as successful OCR |
| 20-page PDF, extra page 21 record | Reject before V3 write |
| 20-page PDF, duplicate page 10 record | Reject before V3 write |

- V3 `--download-only` / `--inspect-only` do not require a V3 database, do not acquire the V3 lock, require a new unique `--outdir`, and reject decision flags.
- A failed download or OCR never invokes replay.
- A failed replay returns non-zero and leaves an accurate manifest.

### 12.3 Replay-only integrity and branch behaviour

- Replay-only works without a positional source argument.
- Replay-only does not run URL validation, filename-derived Issue identification, download or OCR.
- Replay-only accepts an unparseable run-directory name when the manifest Issue key is valid.
- Replay-only rejects a parseable run-directory prefix that disagrees with the manifest.
- Missing manifest, wrong target, wrong manifest version or invalid Issue key is rejected.
- Manifest Issue mismatch against explicit `--issue-key` is rejected.
- Manifest Issue mismatch against a parseable run-directory issue prefix is rejected.
- Run-directory path mismatch is rejected.
- A `download_only` or `inspect_only` manifest is rejected by replay-only.
- A cross-Issue hash without manifest `decision_type` in `{reprocess, new_version}` is rejected by replay-only; either explicit decision is accepted.
- `run_report.source_pdf_sha256` mismatch is rejected.
- Missing/modified PDF is rejected.
- Missing `documents.csv` or `tb_rows.csv` is rejected.
- Strict replay-only rejects missing, duplicate or out-of-range page records.
- A strict failed/skipped page with an explicit record is accepted as a failed/skipped page extraction with no extracted rows.
- Artifact symlinks, traversal and paths escaping the run directory are rejected.
- Valid replay-only retry succeeds and is idempotent.

### 12.4 Issue identity tests

- `anotariais-33-2026.pdf` derives `2026-33`.
- Matching explicit `--issue-key` is accepted.
- `anotariais-33-2026.pdf` with `--issue-key 2026-34` is rejected before download.
- An unparseable filename requires an explicit valid `--issue-key`.
- `--name` cannot override or change the Issue identity inferred from the original source filename.

### 12.5 Manifest and provenance

- New V3 import writes manifest version 2, operation, job ID, source URL, Issue key, SHA-256, byte size, `pdf_pages`, `ocr_pages_observed` and `ocr_page_coverage`.
- Source URL is written to issue and document provenance fields, only filling `NULL` values.
- PDF size is written to `extraction_runs.config_json.source_pdf_bytes` and the manifest.
- Existing non-NULL URLs and existing `config_json` keys are never overwritten.
- Legacy `source` manifests are read correctly.
- Missing legacy manifests do not fail replay; `manifest_origin = reconstructed`.
- Existing 14 runs can be replayed for controlled provenance backfill without inserting new raw rows when evidence is unchanged.
- Legacy backfill corrects `source_documents.page_count` to `run_report.pdf_pages`, records `ocr_page_coverage = "legacy_partial"` and preserves the old value in `config_json.legacy_ocr_page_count`.
- Legacy backfill marks `extraction_runs.run_status = "partial"` when observed pages are fewer than `pdf_pages` or any page failed.
- Legacy backfill does not fabricate `document_pages`, page extractions or extracted rows for pages that were never OCR'd.
- Exact Issue/document/run identities remain traceable.
- A reprocess whose old `storage_uri` is missing updates `storage_uri` to the newly promoted same-hash PDF without changing document identity.

### 12.6 Duplicate/version/retry decisions

- Same hash + same Issue returns `already_imported` by default.
- Same hash + same Issue + `--reprocess --reason` reuses the source-document row and creates a new run.
- Same hash + different Issue returns cross-Issue conflict by default.
- Same hash + different Issue + `--reprocess --reason` creates a source-document row under the second Issue.
- Same Issue + different hash returns `new_source_version_required` by default.
- Same Issue + different hash + `--new-version --reason` creates a new source document and preserves the old one.
- Same Issue + different hash + `--reprocess` is rejected and remains `new_source_version_required`.
- Same hash + `--new-version` is rejected with guidance to use `--reprocess` where applicable.
- Same Issue + different hash matching another Issue + `--new-version` creates a new document and leaves the other Issue unchanged; same-hash rows across Issues are expected.
- `--reprocess` or `--new-version` on a brand-new Issue with no prior document and no matching hash is rejected.
- `--reprocess` and `--new-version` together are rejected.
- `--reprocess` or `--new-version` with `--download-only`, `--inspect-only` or `--replay-only` is rejected.
- Empty/missing reason is rejected.
- Duplicate/decision results delete the temporary download and do not create the normal run directory.
- OCR retry creates a new run directory.
- Replay-only retry reuses the original run directory and does not duplicate rows.

### 12.7 Fingerprint and replay idempotency

- First replay stores the evidence fingerprint.
- Replaying unchanged artifacts returns `already_present`, with zero newly inserted rows.
- Changing one amount while preserving row count raises `EvidenceChangedError`.
- Changing a label or parse status while preserving row count raises `EvidenceChangedError`.
- Changing `documents.csv` metadata with same row count raises `EvidenceChangedError`.
- Changing only `import_manifest.json` does not change the fingerprint.
- A legacy run with no fingerprint gets one on first controlled replay.
- A run with a stored fingerprint and changed evidence fails and rolls back the replay transaction.
- Existing `config_json` keys survive fingerprint insertion.

### 12.8 Download/path/security

- V3 rejects HTTP.
- V3 rejects non-allowlisted hosts, user-info, IP literals, query strings and fragments.
- `GAZETTE_ALLOWED_HOSTS` unset uses only `images.bo.dsaj.gov.mo`; an explicit list is exact-host matching.
- V3 `--input-dir` outside `ROOT/input` is rejected.
- Redirect to a non-allowlisted host or private/local address is rejected.
- Non-PDF content is rejected.
- Oversized response is rejected even without trustworthy `Content-Length`.
- Incomplete download is never promoted.
- A duplicate result deletes only the temporary file and leaves an existing final PDF with the same hash intact.
- An approved result promotes the temporary PDF before OCR starts; OCR is not invoked for a duplicate result.
- Same-name/different-hash download is stored under a versioned filename without overwriting.
- Two importers using different V3 databases and the same input directory cannot overwrite each other's PDF; the no-clobber test verifies both files survive.
- A simulated race that creates the final path after the pre-check still cannot be overwritten; the importer re-checks the hash and reuses or versions.
- Path traversal, symlink escape and symlink destination are rejected.
- Outside output directory is rejected without trusted override.
- Local source is rejected without `--allow-local-source`.
- Subprocess calls use argument arrays and no shell.
- V5 localhost HTTP test remains green.

### 12.9 Concurrency

- A second V3 import returns `import_locked` with exit code 3 while the first process holds the lock.
- Lock is released after success, handled failure and interruption/process exit.
- A symlink path and the real path to the same V3 DB resolve to the same lock; only one importer can hold it.
- A relative and absolute path to the same V3 DB also resolve to the same lock.
- Replay-only also respects the lock for a normal run.
- Replay-only `--dry-run` does not take the lock and still performs read-only validation.
- V5 and OCR-only do not acquire the V3 lock.
- Two invocations with the same `--outdir`/preferred job label reserve distinct result directories; neither overwrites the other's result manifest.
- Direct maintenance replay is documented as outside the lock; integration tests do not run it concurrently with imports.

### 12.10 JSON output

- With `--json`, stdout parses as exactly one JSON object.
- No PDF progress, command line, OCR progress or replay progress appears on stdout.
- Success, failure, duplicate, decision-required, lock-held and preflight-error paths use the JSON result contract when `--json` was parsed.
- Manifest path points to the actual file when one was written.
- `operation` distinguishes full import from replay-only and diagnostic modes.
- For V3, `job_id` is present and matches the run/result directory; a missing `--outdir` generates a unique job ID rather than reusing a shared name. V5/OCR-only may return `null`.
- V5 and OCR-only JSON results set `v3` to `null`; V5 sets `operation: "v5_import"` and `issue_key: null`.
- Diagnostic V3 JSON results set `v3` to `null` and use `operation: "download_only"` or `"inspect_only"`.
- `replay_only` appears only in `operation`, never in `status` or a stage status.

### 12.11 Existing-data isolation

Run integration tests on an isolated copy of the operational V3 DB, never directly on the operational DB.

Assert that a new Issue import does not modify prior:

- `statements`;
- `statement_rows`;
- `statement_reviews`;
- `statement_review_bindings`;
- `statement_row_mappings`;
- adoption records;
- publication records;
- `extracted_rows` belonging to previous runs.

Check database integrity and foreign keys after the test.

---

## 13. Implementation sequence

Do not start Workbench UI implementation in this phase.

### Phase 0 — Contract verification

1. Re-read current Importer, tests and every CLI caller.
2. Record exact V5 option semantics and path behaviour.
3. Inspect current V3 DDL, migrations and replay result shape.
4. Confirm official Gazette URL hostnames from real project source URLs.
5. Re-check operational DB counts and existing run artifacts.
6. Confirm that the current OCR only writes TB-page rows to `documents.csv`; define the additive full-page-record mode needed for V3 coverage validation.
7. Confirm V2.4 does not change the V3 schema.
8. Identify source URL, hash, size, page coverage and run metadata write locations.

**Deliverable:** short implementation note in the PR/commit describing the contracts and any discovered deviations from this specification.

### Phase 1 — V5-safe refactor

1. Refactor existing download, OCR, V5 import and manifest functions into testable units without changing behaviour.
2. Add target parsing with V5 as the default.
3. Preserve V5 cwd-based defaults for `--db` and `--input-dir`, ROOT-based derived output path, cwd-based explicit relative `--outdir`, and existing child-process cwd.
4. Add target-specific download policy without changing V5 downloader behaviour.
5. Route all manifest writes through one writer function and implement the atomic write there; do not leave a second write path for Phase 3.
6. Run all existing tests before proceeding.

**Deliverable:** refactored Importer with no functional V5 regression.

### Phase 2 — V3 raw ingestion

1. Add `--target v3`, `--v3-db`, `--replay-only`, duplicate/version flags, `--reason`, `--issue-key`, `--allow-local-source`, `--allow-outside-output` and `--json`.
2. Implement separate preflight branches for normal import and replay-only.
3. Implement V3 temporary download, hashing and duplicate/version gate before final PDF promotion.
4. Add the additive full-page-record mode to `macau_tb_ocr.py`; V3 uses it, V5 keeps its current output.
5. Enforce full-PDF OCR and strict page-coverage validation for replayable V3 ingestion; set `source_documents.page_count = pdf_pages`.
6. Write manifest v2 (Importer V2.4) with legacy aliases.
7. Update replay to accept `--v3-db`, `--issue-key` and `--no-v5-lookup`.
8. Implement run identity lookup by `run_report_uri`.
9. Stabilise the one-run JSON result.
10. Persist source URL, hash, size, page coverage and run provenance without a schema migration.
11. Implement replay-only integrity checks and retry.
12. Add the legacy `legacy_partial` page-count/coverage policy for controlled backfill.
13. Add a controlled provenance backfill procedure for existing runs.

**Deliverable:** one CLI command can safely import a new Gazette Issue's raw OCR evidence into V3; replay-only can retry without re-running OCR or duplicating evidence.

### Phase 3 — Replay integrity, security and concurrency

1. Implement and test evidence fingerprinting.
2. Implement official-host allowlist and redirect validation.
3. Enforce HTTPS, destination-address checks, streaming size limits and PDF signature validation.
4. Enforce managed input/output path boundaries and no-clobber PDF promotion (`os.link`/`O_EXCL`, never check-then-replace).
5. Add failure-injection tests for the atomic manifest writer; do not reimplement it.
6. Implement the V3 advisory import lock with `realpath` resolution.
7. Complete security, page-coverage, no-clobber, fingerprint, atomic-write, lock and JSON tests.
8. Keep all V5 tests green.

**Gate:** do not expose V3 ingestion to Workbench before Phase 3 is complete.

### Phase 4 — Real Gazette integration test

Use one Issue not already ingested, against an isolated copy of the V3 DB. Do not test against the operational DB. If no un-ingested official Issue is available, use a controlled fixture PDF under a new, otherwise-unused Issue key and record that the test used a fixture rather than a newly published Gazette issue.

Verify:

1. PDF downloads intact; size and SHA-256 match.
2. OCR artifacts are complete and cover the full PDF.
3. Replay completes and stores provenance/fingerprint.
4. Pages and extracted rows enter V3 as unassigned raw evidence.
5. No Statement is automatically confirmed or placed in the financial Review Queue.
6. Repeating the same import follows the duplicate policy.
7. Replay-only retry does not duplicate rows.
8. Same-count modified evidence fails.
9. Existing statements, reviews, bindings, mappings, adoptions and publications are unchanged.
10. A concurrent second import receives `import_locked`.
11. `--json` stdout parses as exactly one JSON object.

**Deliverable:** recorded integration-test result with Issue key, PDF hash/size, page/row counts, V3 replay run ID and database integrity result.

---

## 14. Files expected to change

| File | Expected work |
|---|---|
| `tools/import_issue.py` | Target routing, V3 orchestration, temporary download/duplicate gate, manifest v2 (Importer V2.4), atomic writer, JSON contract, lock and V5 compatibility |
| `tools/replay_runs_to_v3.py` | `--v3-db`, `--issue-key`, `--no-v5-lookup`, V3 run identity lookup, fingerprint, provenance, page-coverage validation, `page_count = pdf_pages` for strict runs, one-run JSON result and legacy compatibility |
| `macau_tb_ocr.py` | Add the additive full-page-record mode used by V3 (`documents.csv` row per PDF page, explicit skipped/failed status); do not change default V5 output or OCR recognition settings |
| `tests/test_import_issue.py` | Preserve existing tests; add V5 path/CLI regression coverage |
| `tests/test_import_issue_v3.py` | New V3 orchestration, replay-only, duplicate, JSON, lock and security tests (or extend current test file) |
| `tests/database/test_v3_replay_idempotency.py` | Run identity, fingerprint, source provenance, page coverage, backfill and replay result tests |
| `tests/test_full_page_records.py` | New OCR full-page-record mode: every PDF page has a row, non-TB pages are `skipped`, failures are `failed`, and default V5 output is unchanged |
| `README.md` / `IMPORT_ISSUE.md` | Document V5, V3, OCR-only, replay-only and database separation |
| `DATABASE.md` | Document provenance stored in existing V3 fields and `config_json`; no schema change |
| `CHANGELOG.md` | Record Importer V2.4 |

No `database/v3/*.sql` change and no migration helper are required for V2.4.

Do not modify Workbench routes or templates in this phase. Do not add a Workbench import-job table to the shared ingestion layer. Persistent Workbench job tracking is a separate follow-on task.

---

## 15. Follow-on Workbench integration contract

After Importer V2.4 is implemented and validated, Workbench calls it as a child process rather than reimplementing download/OCR/replay logic.

Example:

```bash
python tools/import_issue.py \
  "https://images.bo.dsaj.gov.mo/bo/ii/2026/33/anotariais-33-2026.pdf" \
  --target v3 \
  --v3-db /abs/data/macau_balancete_v3.sqlite \
  --outdir "/abs/outputs/runs/issue33_2026__job_${WORKBENCH_JOB_ID}" \
  --json
```

Workbench must:

- execute with `cwd=ROOT`;
- pass absolute V3 DB and output paths;
- pass a generated unique job ID in the run-directory name;
- parse exactly one JSON object from stdout;
- treat exit 0 with `status = already_imported` as a non-error;
- surface exit code 3 as operator decision required or import locked;
- obtain the manifest path from the JSON result;
- base retries on the last completed manifest stage;
- never infer financial review from import success.

Workbench separately implements:

- persistent import jobs and status history;
- one-at-a-time local worker execution;
- job list/detail/progress/log UI;
- retry controls;
- page classification and source evidence display;
- bank/entity, statement date, statement type and page grouping confirmation;
- transactional creation of proposed Statements and Statement Rows;
- proposed Standard Item mapping scoped to new Statements;
- hand-off to the existing Review Queue.

A successful Importer run means only that raw OCR evidence is in V3.

---

## 16. Explicit non-goals

V2.4 does not include:

- Workbench Import Issue page/API/worker implementation;
- page classification UI;
- bank/entity identity approval;
- Statement grouping or Statement creation;
- Standard Item mapping or approval;
- review, extraction binding, adoption or publication;
- replacing the OCR engine or amount parser;
- changing the V5 schema or removing the legacy V5 workflow;
- changing V5 path-resolution behaviour;
- changing the V3 schema or adding a file-size column;
- deploying Workbench to a LAN/public host;
- introducing Celery, Redis or a distributed job queue;
- guessing source URLs for legacy runs without URL evidence.

---

## 17. Definition of done

Importer V2.4 is complete only when all are true:

- [ ] Existing V5 commands and tests work without changing user commands.
- [ ] V5 path behaviour is unchanged, including the distinction between cwd-based DB/input paths and ROOT-derived default output path.
- [ ] `--target v3` runs download, full-PDF OCR and V3 raw-evidence replay in one command.
- [ ] Replay-only works without a source argument and verifies manifest, PDF, OCR artifact and run-directory Issue identity integrity.
- [ ] Replay-only never executes source validation or filename-derived Issue identification; the manifest is authoritative and the run-directory prefix is only a consistency check.
- [ ] V3 duplicate/version decisions follow §8 exactly, including the brand-new-Issue decision-flag rejection.
- [ ] Duplicate checking occurs before final PDF promotion and before OCR; a duplicate deletes only the temporary file and leaves an existing same-hash final PDF intact.
- [ ] An approved import promotes the PDF before OCR starts.
- [ ] Full V3 import rejects every `--pages` value except literal `all`; partial pages are available only through `ocr-only` and V3 `--inspect-only`.
- [ ] Full V3 import produces explicit `documents.csv` records for every page `1..pdf_pages`, including skipped non-TB pages and failed pages; missing, duplicate or out-of-range page records stop the import before V3 writes.
- [ ] `source_documents.page_count` equals the validated `pdf_pages` for new runs; legacy backfill uses `legacy_partial`/`unknown` coverage and never fabricates missing pages.
- [ ] A failed page sets `extraction_runs.run_status = "partial"` and is never counted as a successful OCR page.
- [ ] PDF promotion is no-clobber: another importer or diagnostic run cannot overwrite the destination, and cross-DB input-directory races preserve both files.
- [ ] Replay-only dry-run performs read-only validation without acquiring the lock; normal replay-only acquires it before replay.
- [ ] Every V3 invocation has a unique `job_id` used consistently by the run and result directories, and colliding `--outdir` job labels cannot share result directories.
- [ ] Mismatched source filename and explicit Issue key is rejected, including the `anotariais-33-2026.pdf` / `--issue-key 2026-34` case.
- [ ] V3 uses `--v3-db` and never passes that DB to the V5 builder.
- [ ] V3 replay passes `--no-v5-lookup` and a test proves the V5 DB is never opened.
- [ ] Same-count evidence changes are caught by the fingerprint and roll back.
- [ ] Source URL, SHA-256, PDF size, Issue identity and run identity are traceable.
- [ ] No V3 schema migration is required.
- [ ] Manifest writes are atomic.
- [ ] Concurrent V3 imports are serialised by the advisory lock, including when the same DB is addressed through a symlink or a relative path.
- [ ] `--json` stdout contains exactly one object for all supported result paths, including V5, OCR-only and V3 diagnostic modes.
- [ ] V3 diagnostic modes are defined, do not require a V3 database and reject decision flags.
- [ ] V3 rows remain unassigned pending Workbench page classification.
- [ ] Existing V3 Statements, Reviews, Mappings, Adoptions and Publications remain unchanged in isolated integration tests.
- [ ] A real Gazette Issue passes end-to-end integration testing against an isolated V3 database copy.
- [ ] Documentation distinguishes V5 import, V3 raw ingestion, OCR-only and replay-only.

---

## Appendix A — Changes from V2.0/V2.1 to V2.2

| Earlier issue | V2.2 resolution |
|---|---|
| Replay-only preflight still required a source/filename | Split normal import and replay-only preflight branches; replay-only derives Issue identity from its V2 manifest and skips source validation |
| Download was promoted before duplicate result, conflicting with temporary-file cleanup | Download remains temporary through the duplicate/version gate; only authorised ingestion promotes it to managed input storage |
| Explicit `--issue-key` could contradict source filename | Mismatch is rejected; explicit key is an optional assertion when the filename is parseable and required when it is not |
| V3 partial-page OCR could create incomplete issue evidence | Replayable V3 ingestion requires `--pages all`; use OCR-only for partial-page experiments |
| Replay result mixed operation mode and result status | Add independent `operation` field; replay-only success is `status=completed`, `operation=replay_only` |
| V5 path compatibility wording was imprecise | Document exact current behaviour: cwd-based V5 DB/input defaults, ROOT-derived default output path, cwd-based explicit relative outdir |
| V2.0 fingerprint lacked explicit same-count tests for several evidence files | Add same-count amount, label, status and page-metadata regression tests |
| V2.1 was an untracked working copy while V2.0 remained the canonical committed file | V2.2 content is merged into `docs/IMPORT_ISSUE_V2_V3_INGESTION_SPEC.md`; the V2.1 working copy is removed |
| `--issue-key` help said "only when it cannot be derived" but the validation section allowed an assertion | Unified semantics: derive from the source filename; explicit key is an optional assertion when parseable and required when not |
| Replay-only did not cross-check manifest Issue against the run-directory prefix | Added the parseable-prefix cross-check |
| New-Issue imports silently ignored `--reprocess` / `--new-version` | Both flags are rejected when no prior document/version exists |
| `<job-id>` was used without a generation or collision rule | Added `job_id` allocation, default run-directory naming and result-directory use |
| Diagnostic modes had no preflight branch | Added explicit `--download-only` / `--inspect-only` branches: no V3 DB/lock, new outdir, decision flags rejected |
| `--pages all` did not say whether a full-page range is allowed | V2.2 requires the literal `all`; ranges are rejected |
| `--new-version` could create a same-hash row across Issues without documentation | Added the edge case and test expectation |
| Manifest section title said V2.1 while `manifest_version` remained 2 | Retitled as manifest schema v2 (Importer V2.2) and documented the version rule |
| V5 `--json` and V5 manifest shape were undefined | Defined `v5_import`, `v3: null`, null `issue_key` and legacy V5 manifest keys |
| Atomic manifest writing was assigned to both Phase 1 and Phase 3 | Phase 1 implements it once; Phase 3 only adds failure-injection tests |
| Allowlist configuration surface was undefined | Added `GAZETTE_ALLOWED_HOSTS` with the default host |
| V3 `--input-dir` "managed" was ambiguous | Restricted explicit V3 input to the `ROOT/input` subtree |
| A reprocess could leave a stale `storage_uri` when the old PDF was missing | Allowed updating `storage_uri` for a same-hash promoted PDF without changing identity |

---

## Appendix B — Changes from V2.2 to V2.3

| V2.2 issue | V2.3 resolution |
|---|---|
| Replay-only preflight still read like a variant of the full-import path | Split preflight into independent Path A (full import), Path B (replay-only) and Path C (diagnostic); replay-only never runs source validation or filename-derived Issue identification |
| Manifest Issue key and run-directory name could be read as competing authorities | Manifest is authoritative for replay-only; a parseable run-directory prefix is only a consistency check, and an unparseable directory name is accepted when the manifest is valid |
| Download promotion and duplicate-result cleanup order was ambiguous | Made the order explicit: temporary download -> validation -> duplicate/version gate -> delete temp or promote -> OCR. A duplicate result never promotes; an existing same-hash final PDF is never deleted |
| `--issue-key` mismatch risk was documented but lacked a concrete example | Added the `anotariais-33-2026.pdf` + `--issue-key 2026-34` rejection example and tests; no silent override |
| V3 partial-page OCR could misrecord `source_documents.page_count` | Full V3 import now explicitly requires literal `--pages all`; explained the page-count consequence; partial pages remain only for `ocr-only` and V3 `--inspect-only` diagnostics |
| `replay_only` could still be confused with a result status | Added an explicit note that `replay_only` is an operation, never a manifest or result status; success is `status=completed`, `operation=replay_only` |
| Lock path could differ for symlink and real paths | Lock path now derives from `os.path.realpath()` on the V3 DB; added symlink/relative-path concurrency tests |
| Promotion-order and replay-only preflight behaviour lacked regression tests | Added tests for duplicate temp cleanup, existing final PDF preservation, promotion-before-OCR, unparseable replay-only directory, and symlink-lock identity |

---

## Appendix C — Changes from V2.3 to V2.4

| V2.3 issue | V2.4 resolution |
|---|---|
| `--pages all` did not prove that every PDF page was actually observed | Added strict page-coverage validation against `run_report.pdf_pages`: one record per page, no missing/duplicate/out-of-range pages, explicit failed/skipped page records, and `source_documents.page_count = pdf_pages` for new runs |
| Legacy runs only contain the pages that were historically OCR'd | Added a legacy exception: correct `page_count` to `pdf_pages` where valid, record `ocr_page_coverage = "legacy_partial"` and `legacy_ocr_page_count`, and never fabricate missing pages/rows |
| `os.replace()` could overwrite a PDF created by another importer or diagnostic run | Replaced check-then-replace with atomic no-clobber promotion (`os.link` + unlink, or `O_CREAT`/`O_EXCL` + copy); same-hash reuse and versioned retry remain; added cross-DB input-directory race tests |
| Replay-only dry-run lock order was ambiguous | Path B now has separate dry-run and normal branches: dry-run performs read-only validation without the lock; normal replay-only acquires the lock before replay |
| `--outdir` `__job_` segment was assumed to be unique | `job_id` is now a preferred label only; run/result directories are reserved with `mkdir(exist_ok=False)` and colliding result IDs get a unique suffix or fail |
| Page-coverage, no-clobber and job-ID collision behaviour lacked tests | Added the 20-page coverage table, cross-DB PDF race test, dry-run lock test and duplicate result-directory test |
