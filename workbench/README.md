# Workbench v1 Scaffold

FastAPI scaffold for the Data Verification Workbench.  It connects to the
**operational** v3 database and never writes to the read-only MVP snapshot.

The phase-1 inspection screen is a separate local app,
[`phase1_dashboard`](../phase1_dashboard/README.md). It only reads V3 and does
not review, map, or adopt. Workbench stays the place that changes those states.

## Run

```bash
.venv/bin/pip install -r requirements-workbench.txt
```
```bash
.venv/bin/uvicorn workbench.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

## Scope (Workbench v1)

1. Statement list (active / held / all)
2. Review Queue (`/review`) for held statements
3. Statement / page / row review
4. Field verification (`field_verifications`) with inline row editing and
   explicit per-field confirmation
5. Manual row insert / exclude for OCR under-read and over-read
6. Preliminary Standard Item display and mapping approval (approved items only)
7. Adoption gate bound to the reviewed extraction
8. Audit trail
9. Read-only MVP snapshot visibility
10. Import issue jobs (background V3 raw ingestion, URL or PDF upload)
11. Import intake: OCR quality preview, page classification, reporting
   entity/date, statement creation, discard pending builds / OCR reprocess

The 89-item user catalogue is seeded into `standard_items`; statement rows have
`proposed` mappings from the first-pass auto-map.  Review Queue rows show
mapped/unmapped counts, and the Review detail shows the proposed standard item
or conflict candidates.  A reviewer can approve a standard item mapping with
the `map` capability.  Mapping approval is separate from financial verification
and from adoption.

## Roles (local scaffold)

For local browsing, use the role links in the top bar (they set a
`workbench_role` cookie via `/set-role/{role}`).  API clients can pass the
`X-Workbench-Role` header.  Default is `viewer`.

These role and actor values are supplied by the client.  They are a local
capability scaffold, **not authentication**, and must not be trusted on a LAN
or public deployment.  Run the service on `127.0.0.1` only.

| Role | Capabilities |
|---|---|
| `viewer` | read |
| `reviewer` | read, verify, review, map, import, classify |
| `adopter` | read, verify, review, map, import, classify, adopt |
| `publisher` | read, publish |
| `admin` | read, verify, review, map, import, classify, adopt, publish, catalogue |

Adoption and publish are deliberately separate.  The publish endpoint returns
`501 Not Implemented` until the publication quality gate is defined.  This is a
safety feature, not a missing button.  Adoption has its own working page at
`/adopt` (ready / blocked / adopted), and the Adopt button is also available on
each reviewed record.

## Snapshot enforcement

- Operational DB: `data/macau_balancete_v3.sqlite`
- Snapshot: `data/baseline/macau_balancete_v3_mvp_20261119.sqlite` (chmod 444)
- `workbench.db.connect_v3(..., write=True)` refuses the snapshot path.
- Snapshot manifest: `docs/audit/MVP_SNAPSHOT_MANIFEST.json`

## API

```text
GET  /health
GET  /api/summary
GET  /api/statements?status=all|active|held
GET  /api/statements/{statement_id}
GET  /api/statements/{statement_id}/rows
GET  /api/review/queue
GET  /api/review/{statement_id}/rows
GET  /api/review/{statement_id}/adjustments
GET  /api/standard-items?include_inactive=false
GET  /api/catalogue/items
GET  /imports                                      # Import job list + forms
GET  /imports/{job_id}                             # Import job detail + log
GET  /api/imports?limit=50
POST /api/imports                                  # start URL import, reviewer+
POST /api/imports/upload                           # raw PDF body + X-Filename, reviewer+
GET  /api/imports/{job_id}
GET  /api/imports/{job_id}/log
POST /api/imports/{job_id}/retry                   # failed/interrupted only
POST /api/imports/{job_id}/decision                # reprocess / new_version + reason
GET  /intake                                       # Import intake batch list
GET  /intake/{source_document_id}                  # Page classification + build
GET  /api/intake
GET  /api/intake/{source_document_id}             # includes ocr_quality summary
POST /api/intake/{source_document_id}/pages/{document_page_id}  # classify, reviewer+
POST /api/intake/{source_document_id}/auto-classify             # apply PDF-text suggestions
POST /api/intake/{source_document_id}/build                     # create statements
POST /api/intake/{source_document_id}/reprocess                 # OCR reprocess via import job
POST /api/intake/{source_document_id}/discard-pending           # discard all pending builds
GET  /adopt                                      # Adoption working list (reviewed → adopt)
GET  /catalogue                                  # Standard List page
POST /api/catalogue/items                        # create draft item, admin
POST /api/catalogue/items/{id}/approve           # admin
POST /api/catalogue/items/{id}/retire            # admin
POST /api/catalogue/items/{id}/reopen            # admin
POST /api/review/{statement_id}/rows                   # insert missed row, reviewer
POST /api/review/{statement_id}/rows/{row_id}/verify  # field verification, reviewer
POST /api/review/{statement_id}/rows/{row_id}/exclude # exclude/remove row, reviewer
POST /api/review/{statement_id}/rows/{row_id}/map     # X-Workbench-Role: reviewer
POST /api/review/{statement_id}/adjustments/{id}/restore
POST /api/review/{statement_id}/review                 # X-Workbench-Role: reviewer
POST /api/review/{statement_id}/discard                # discard pending (never reviewed)
POST /api/statements/{statement_id}/adopt    # X-Workbench-Role: adopter
POST /api/statements/{statement_id}/return-to-review  # X-Workbench-Role: adopter
POST /api/statements/{statement_id}/publish  # X-Workbench-Role: publisher, 501 in v1
```

The row `verify` body accepts `verified_fields` (a subset of
`label_normalized`, `debit_minor`, `debit_value_state`, `credit_minor`,
`credit_value_state`).  Fields whose submitted value changed are verified
automatically; omitted unchanged fields are not recorded.

## Import jobs

`/imports` starts `tools/import_issue.py --target v3` as a background
subprocess.  OCR never runs inside an HTTP request; the page polls the job row
and the job log tail.

- Only `--target v3` is launched. `--publish`, bank refresh and name mapping are
  never passed: a job produces raw OCR evidence and stops at staging.
- Statuses: `queued`, `running`, `completed`, `failed`, `needs_decision`,
  `interrupted`, `cancelled`. `needs_decision` means the importer returned
  `new_source_version_required` or `already_imported_content_conflict`; use
  **Reprocess** or **New version** with a reason.
- A second queued/running job for the same source is rejected with `409`.
  Duplicate PDF content is still decided by the importer's own duplicate gate,
  so `already_imported` comes back as a completed job with that result status.
- Job rows live in `import_jobs` (`database/v3/016_import_jobs.sql`). The table
  is ensured on first use; apply the migration explicitly on other databases.
- Uploaded PDFs are staged under `input/.uploads/<uuid>/<original name>.pdf`
  (gitignored). Keep them while the job may be retried or reprocessed.
- Job logs: `outputs/import_jobs/<job_id>.log`.
- On startup, jobs left `queued`/`running` by a previous process are marked
  `interrupted`, never silently re-run. **Retry** reruns the stored argv.

## Import intake

A finished import creates raw rows only. `/intake` lists documents that still
have `proposed` pages or `unassigned` rows. The batch page shows an **OCR
quality preview** from the latest run folder (`tb_rows.csv` / `documents.csv` /
`run_report.json`): row counts, empty-label rate, amount-looking labels, and
top review reasons. Warnings are advisory; classify/build stay available.
**Reprocess OCR** (import capability) queues the matching import job when no
adopted statement exists; pending builds must be discarded first (or use
`discard_pending` on the reprocess call).

Bank names and statement dates are already extracted from the PDF text into the
run's `documents.csv`, so the page shows a suggested entity (exact or fuzzy
match against approved canonical names and aliases) and date. **Apply PDF
suggestions** classifies every proposed page at once; the reviewer still
confirms each match before building.

For each page a reviewer:

1. picks the page role and approves or rejects it (reject requires a note);
2. for an approved `financial_statement` page, records the approved reporting
   entity, statement date and statement type.

Pages with no extracted rows (non-TB pages) can be bulk-approved as `other`.
Ambiguous names are never guessed: the page is reported as unmatched and must
be classified manually.

Decisions are written to `document_pages` plus `page_statement_decisions`
(migration `database/v3/017_page_statement_decisions.sql`) and audited in
`audit_events`. Every page must be classified before **Build statements** runs.
Intake row counts and build assignment use only the **latest** extraction run
for that document; older runs stay in the DB as audit evidence.

Building creates `statements`, `statement_extractions`, `statement_pages` and
`statement_rows`, assigns the raw `extracted_rows` (`included`), and marks rows
on non-financial/rejected pages `excluded_page_role`. Row fingerprints reuse the
P2-C rules. The build is idempotent: an existing statement extraction is reused
and raw OCR values are never changed. Classification is refused once a page is
already assigned to a statement.

After the build, the existing first-pass auto-map
(`tools/p1_auto_map_standard_items.py --apply`) creates **proposed** Standard
Item mappings only. It never approves a mapping. Statements then appear in the
Review Queue like any other statement.

**Discard pending** (review capability) undoes a never-reviewed /
never-adopted build: unassigns `extracted_rows` (migration `018`), rejects the
extraction, marks the statement `excluded`, and leaves `statement_rows` as a
historical snapshot. Excluded statements do not appear in the Review Queue.
Reviewed, adopted, active, or published statements cannot be discarded this way.

The Intake page lists every discardable statement under **Pending statements**
with a per-statement **Discard** button. Use it when a built page has the wrong
entity/date: discard the statement that owns the page, change the page, then
build again. The build refuses to reclassify a page while its rows still belong
to a statement and names the owning statement in the error.

Re-running **Build** after a discard or a new OCR run reopens that statement
(`excluded` → `proposed`, audit event `statement_reopened`) and assigns the new
run's rows, so it returns to the Review Queue. A discard is only permanent when
the statement was reviewed, adopted or published; rebuild then fails with a
conflict instead of assigning rows to a hidden statement.

**Reprocess OCR** resolves and validates the completed import job (including the
uploaded PDF) before discarding anything, so a failed reprocess leaves the
pending statement untouched.

Legacy databases that ran the 2026-11-22 rename migration may have schema
objects pointing at the dropped `statement_rows__pre_discard_fk` table. Repair
them with:

```bash
python tools/repair_statement_rows_refs.py --db data/macau_balancete_v3.sqlite
python tools/repair_statement_rows_refs.py --db data/macau_balancete_v3.sqlite --apply
```

The repair tool is idempotent, backs up the database first, and refuses to
commit unless `PRAGMA foreign_key_check` is clean.

Identity rules: `reporting_scope` comes from the approved entity type, currency
is `MOP` and `unit_scale` is 1 under the Macau domain rule, and the identity key
is `entity_key|statement_date|statement_type|scope`.

## Important

### Row adjustments

OCR under-read and over-read are handled with
`statement_row_adjustments` rather than editing OCR evidence:

- **Insert** adds a manual row that OCR missed. Its standard item can be set
  immediately or approved later.
- **Delete** on an OCR row creates an active `exclude` adjustment; the raw
  `extracted_rows` / `statement_rows` evidence is untouched.
- **Restore** voids the adjustment and the original row returns.

Active adjustments are applied by `v_current_statement_rows`, so adopted
statements include inserted rows and omit excluded rows.  Inserted rows obey
the same mapping gate as OCR rows: they block review until a standard item is
selected.

### Review workflow

1. Reviewer opens `/review`.
2. Row corrections are saved as `field_verifications`.  Saving verifies the
   fields whose values changed and any fields the reviewer explicitly ticks;
   untouched, unticked fields are not recorded as verified.
3. Every row must have an `approved` or a single `proposed` standard-item
   mapping, and the mapped catalogue item must still be `approved`.  Any
   `conflict`, `unmatched`, duplicate or inactive-item mapping blocks review;
   such statements appear in the corresponding queue buckets.
4. A standard item may be approved for only one row in a statement.  The
   dropdown only offers `approved` catalogue items, and the API/schema reject a
   duplicate approval or a draft / merged / retired item.
5. Reviewer marks the statement **reviewed**; it moves from “Pending review” to
   “Reviewed — still held”. It is **not** adopted.
   Marking reviewed records the exact `statement_extraction_id` in
   `statement_review_bindings`.  Reviewed statements are read-only: Save returns
   `409` and the UI disables row editing until **Reopen for editing** is used.
   Reopening voids the active binding; a new review binds the current extraction.
6. An adopter works from `/adopt`, which lists reviewed statements in three
   groups: **Ready to adopt**, **Reviewed — not adoptable** (with the blocking
   bucket) and **Adopted — current dataset**. The same **Adopt** button appears
   in the reviewed record (`/review/{id}`) and on `/statements/{id}`. Only a
   reviewed statement whose queue bucket is `ready_for_adoption` and whose
   sign-off is still bound to the latest completed extraction can be adopted;
   the API refuses every other state.  If a newer extraction exists, the
   statement appears as `review_outdated` and adoption is refused until it is
   reviewed again.
7. Adopted statements are read-only and listed under **Adopted — current
   dataset** with the same mapping coverage. An adopter can **Return to
   reviewed**, which clears the adopted extraction, rebinds the same extraction
   and puts the statement back into the reviewed/held queue. A current
   publication must be revoked first.
8. Publication remains a separate, not-yet-implemented gate.

### Extraction binding

A statement may have several OCR attempts.  Queue metrics, row display, review
rows and adoption all resolve the same extraction:

1. the adopted extraction, for an active statement;
2. the active reviewer binding, when a sign-off exists;
3. otherwise the latest completed extraction.

Review rows from another extraction are never mixed in.  A new completed
extraction after sign-off invalidates adoption readiness but does not silently
replace the reviewed rows.

Existing databases must apply
`database/v3/015_review_extraction_binding.sql`.  Reviewed records created
before that schema can be bound with
`tools/backfill_workbench_review_bindings.py` (dry-run by default; `--apply`
writes one binding and one audit event per unambiguous statement).

### Standard list maintenance

`/catalogue` lists every standard item with its lifecycle status and usage
counts.  `admin` can create a draft item, approve it, retire it, or reopen a
retired item.  New items always start as **draft** and are not offered in
mapping dropdowns until approved.  Approving a draft also approves its
non-missing canonical-name statuses.

Retiring an item does not delete mappings or OCR evidence, but any mapping that
still points to the retired item becomes invalid and blocks review/adoption
until it is remapped or the item is reopened.  The UI shows the usage counts
before retirement and asks for confirmation.  Every create / approve / retire /
reopen action writes `audit_events`.

A local admin CLI is also available:

```bash
python tools/standard_item_admin.py list --status draft --search 負債
python tools/standard_item_admin.py --actor tony \
  approve "user:澳門特區負債證明書" --note "confirmed"
```

### Amount input

Amount fields use a dotted decimal contract: `1234.56` or `1,234.56`.
Commas are thousands separators, never decimal points, so `1.234,56` is
rejected.  An `amount` state with an empty value is rejected instead of being
saved as `0.00`; use `blank` or `dash` for non-amount cells.  Parsing and
formatting use the statement's `amount_decimal_places`.

### Unsaved edits

The review detail page tracks dirty rows individually.  Saving one row updates
only that row without a page reload.  Mapping, delete/restore, insert, return
and review actions are blocked while other rows (or the insert form) have
unsaved edits, and leaving the page prompts for confirmation.

This is a scaffold, not production authentication.  It does not yet implement
login, real user accounts, PDF rendering, or publish quality gates.  Import
supports one PDF per job (URL or upload); batch folder import and job
cancellation are not implemented yet.  Intake builds statements synchronously
from a single source document and does not yet support splitting one document
into several runs.
