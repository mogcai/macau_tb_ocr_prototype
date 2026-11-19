# Workbench v1 Scaffold

FastAPI scaffold for the Data Verification Workbench.  It connects to the
**operational** v3 database and never writes to the read-only MVP snapshot.

## Run

```bash
.venv/bin/pip install -r requirements-workbench.txt
.venv/bin/uvicorn workbench.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>.

## Scope (Workbench v1)

1. Statement list (active / held / all)
2. Review Queue (`/review`) for held statements
3. Statement / page / row review
4. Field verification (`field_verifications`) with inline row editing
5. Adoption gate
6. Audit trail
7. Read-only MVP snapshot visibility

**Out of scope:** Standard Item mapping and P1 catalogue reconciliation.  The
`standard_items` and `statement_row_mappings` tables remain empty in this
version.

## Roles (local scaffold)

For local browsing, use the role links in the top bar (they set a
`workbench_role` cookie via `/set-role/{role}`).  API clients can pass the
`X-Workbench-Role` header.  Default is `viewer`.

| Role | Capabilities |
|---|---|
| `viewer` | read |
| `reviewer` | read, verify |
| `adopter` | read, verify, adopt |
| `publisher` | read, publish |
| `admin` | read, verify, adopt, publish |

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
POST /api/review/{statement_id}/rows/{row_id}/verify  # X-Workbench-Role: reviewer
POST /api/review/{statement_id}/review                 # X-Workbench-Role: reviewer
POST /api/statements/{statement_id}/adopt    # X-Workbench-Role: adopter
POST /api/statements/{statement_id}/publish  # X-Workbench-Role: publisher, 501 in v1
```

## Important

### Review workflow

1. Reviewer opens `/review`.
2. Row corrections are saved as `field_verifications`; any correction reopens the
   statement review state (`needs_rework`).
3. Reviewer marks the statement **reviewed**; it moves from “Pending review” to
   “Reviewed — still held”. It is **not** adopted.
4. An adopter can adopt only a reviewed statement whose queue bucket is
   `ready_for_adoption`.
5. Publication remains a separate, not-yet-implemented gate.

This is a scaffold, not production authentication.  It does not yet implement
login, real user accounts, PDF rendering, import-batch upload, or publish
quality gates.  Those remaining pieces are the next Workbench slices.
