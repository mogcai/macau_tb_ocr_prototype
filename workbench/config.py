"""Workbench configuration.

The operational v3 database is writable by authorised workbench operations.
The MVP snapshot is always read-only.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V3_DB = Path(os.environ.get("WORKBENCH_V3_DB", ROOT / "data" / "macau_balancete_v3.sqlite"))
SNAPSHOT_DB = Path(
    os.environ.get(
        "WORKBENCH_SNAPSHOT_DB",
        ROOT / "data" / "baseline" / "macau_balancete_v3_mvp_20261119.sqlite",
    )
)
SNAPSHOT_MANIFEST = Path(
    os.environ.get(
        "WORKBENCH_SNAPSHOT_MANIFEST",
        ROOT / "docs" / "audit" / "MVP_SNAPSHOT_MANIFEST.json",
    )
)
