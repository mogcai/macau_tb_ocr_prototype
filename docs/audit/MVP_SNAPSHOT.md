# MVP Snapshot — Current Dataset

**Created:** 2026-11-19
**Snapshot:** `data/baseline/macau_balancete_v3_mvp_20261119.sqlite`
**Manifest:** `docs/audit/MVP_SNAPSHOT_MANIFEST.json`

The snapshot is a **read-only delivery artifact**, not a second operational
authority.  All review/adoption writes continue to target
`data/macau_balancete_v3.sqlite`.

## Scope

| Item | Value |
|---|---:|
| Adopted / active statements | 72 |
| Current rows | 4,462 |
| Held statements excluded | 41 |
| Held rows excluded | 2,584 |
| Active field verifications | **0** |
| Active financially verified | **false** |

The 89 field verifications from the manual-adjustment round belong to the
held statements and are intentionally excluded from this snapshot.

## Read-only enforcement

- Snapshot file is `chmod 444`.
- The builder refuses to overwrite the operational source DB.
- `workbench.db.connect_v3(..., write=True)` refuses the snapshot path.
- Workbench writes always target the full operational v3 DB, where held records
  remain available for review.

## Reproduce

```bash
python tools/build_mvp_snapshot.py \
  --source data/macau_balancete_v3.sqlite \
  --out data/baseline/macau_balancete_v3_mvp_20261119.sqlite \
  --manifest docs/audit/MVP_SNAPSHOT_MANIFEST.json \
  --force
```

The manifest records source SHA-256, snapshot SHA-256, schema version, counts,
integrity check, and `active_financially_verified = false`.

## Important

Adoption is not financial verification.  The 72 active statements passed
structural/adoption review, but the active field-verification count is zero.
Publication remains blocked until a separate quality gate is defined.
