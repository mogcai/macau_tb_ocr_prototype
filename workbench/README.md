# Workbench v1 Scaffold

FastAPI scaffold for the Data Verification Workbench.  It connects to the
**operational** v3 database and never writes to the read-only MVP snapshot.

## Run

```bash
.venv/bin/pip install -r requirements-workbench.txt
```
```bash
.venv/bin/uvicorn workbench.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>.

## Scope (Workbench v1)

1. Statement list (active / held / all)
2. Review Queue (`/review`) for held statements
3. Statement / page / row review
4. Field verification (`field_verifications`) with inline row editing
5. Manual row insert / exclude for OCR under-read and over-read
6. Preliminary Standard Item display and mapping approval
7. Adoption gate
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

| Role | Capabilities |
|---|---|
| `viewer` | read |
| `reviewer` | read, verify, review, map |
| `adopter` | read, verify, review, map, adopt |
| `publisher` | read, publish |
| `admin` | read, verify, review, map, adopt, publish |

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
GET  /api/standard-items
POST /api/review/{statement_id}/rows                   # insert missed row, reviewer
POST /api/review/{statement_id}/rows/{row_id}/verify  # X-Workbench-Role: reviewer
POST /api/review/{statement_id}/rows/{row_id}/exclude # exclude/remove row, reviewer
POST /api/review/{statement_id}/rows/{row_id}/map     # X-Workbench-Role: reviewer
POST /api/review/{statement_id}/adjustments/{id}/restore
POST /api/review/{statement_id}/review                 # X-Workbench-Role: reviewer
POST /api/statements/{statement_id}/adopt    # X-Workbench-Role: adopter
POST /api/statements/{statement_id}/return-to-review  # X-Workbench-Role: adopter
POST /api/statements/{statement_id}/publish  # X-Workbench-Role: publisher, 501 in v1
```

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
2. Row corrections are saved as `field_verifications`; any correction reopens the
   statement review state (`needs_rework`).
3. Every row must have an `approved` or a single `proposed` standard-item
   mapping.  Any `conflict` or `unmatched` row blocks review; such statements
   appear in the `mapping_conflict` / `mapping_unmatched` buckets with the
   counts shown in the queue.  Proposed mappings do not need to be approved
   first — they are trusted enough to pass the review gate.
4. Reviewer marks the statement **reviewed**; it moves from “Pending review” to
   “Reviewed — still held”. It is **not** adopted.
   Reviewed statements are read-only: Save returns `409` and the UI disables
   row editing until **Reopen for editing** is used. Save never changes the
   review status.
5. An adopter can adopt only a reviewed statement whose queue bucket is
   `ready_for_adoption`.
6. Adopted statements are read-only and listed under **Adopted — current
   dataset** with the same mapping coverage. An adopter can **Return to
   reviewed**, which clears the adopted extraction and puts the statement back
   into the reviewed/held queue. A current publication must be revoked first.
7. Publication remains a separate, not-yet-implemented gate.

This is a scaffold, not production authentication.  It does not yet implement
login, real user accounts, PDF rendering, import-batch upload, or publish
quality gates.  Those remaining pieces are the next Workbench slices.
