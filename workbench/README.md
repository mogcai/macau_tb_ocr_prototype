# Workbench v1 Scaffold

FastAPI scaffold for the Data Verification Workbench.  It connects to the
**operational** v3 database and never writes to the read-only MVP snapshot.

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
| `reviewer` | read, verify, review, map |
| `adopter` | read, verify, review, map, adopt |
| `publisher` | read, publish |
| `admin` | read, verify, review, map, adopt, publish, catalogue |

Adoption and publish are deliberately separate.  The publish endpoint returns
`501 Not Implemented` until the publication quality gate is defined.  This is a
safety feature, not a missing button.

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
POST /api/statements/{statement_id}/adopt    # X-Workbench-Role: adopter
POST /api/statements/{statement_id}/return-to-review  # X-Workbench-Role: adopter
POST /api/statements/{statement_id}/publish  # X-Workbench-Role: publisher, 501 in v1
```

The row `verify` body accepts `verified_fields` (a subset of
`label_normalized`, `debit_minor`, `debit_value_state`, `credit_minor`,
`credit_value_state`).  Fields whose submitted value changed are verified
automatically; omitted unchanged fields are not recorded.

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
6. An adopter can adopt only a reviewed statement whose queue bucket is
   `ready_for_adoption` and whose sign-off is still bound to the latest completed
   extraction.  If a newer extraction exists, the statement appears as
   `review_outdated` and adoption is refused until it is reviewed again.
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
login, real user accounts, PDF rendering, import-batch upload, or publish
quality gates.  Those remaining pieces are the next Workbench slices.
