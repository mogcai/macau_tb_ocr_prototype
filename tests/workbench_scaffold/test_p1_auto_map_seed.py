"""Regression tests: re-seeding the catalogue must not undo Workbench curation.

`tools/p1_auto_map_standard_items.py --apply` runs on every Workbench intake
build.  Its legacy seed previously did `ON CONFLICT ... DO UPDATE SET
item_status=excluded.item_status`, which silently reset approved catalogue
items back to draft on the next build.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCHEMA = ROOT / "database" / "v3" / "007_standard_catalogue_and_mapping.sql"


def load_tool():
    path = ROOT / "tools" / "p1_auto_map_standard_items.py"
    spec = importlib.util.spec_from_file_location("p1_auto_map_standard_items", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_v5(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE standard_labels (
            standard_id INTEGER PRIMARY KEY,
            standard_key TEXT NOT NULL,
            canonical_zh TEXT,
            canonical_pt TEXT,
            canonical_en TEXT,
            status TEXT,
            parent_standard_id INTEGER
        );
        CREATE TABLE standard_label_aliases (
            alias_id INTEGER PRIMARY KEY,
            standard_id INTEGER NOT NULL,
            raw_name TEXT NOT NULL,
            language TEXT,
            normalized_name TEXT,
            status TEXT,
            note TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO standard_labels VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "user:現金", "現金", None, None, "approved", None),
            (2, "user:澳門特區負債證明書", "澳門特區負債證明書", None, None, "draft", None),
            (3, "user:pt:TESOURO PÚBLICO", None, "TESOURO PÚBLICO", None, "draft", None),
        ],
    )
    conn.execute(
        "INSERT INTO standard_label_aliases VALUES "
        "(1, 2, '澳門特區負債證明書', 'zh-Hant', '澳門特區負債證明書', 'approved', '')"
    )
    conn.commit()
    conn.close()


class SeedCatalogueTests(unittest.TestCase):
    def open_v5(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.v5_path)
        conn.row_factory = sqlite3.Row
        return conn

    def setUp(self) -> None:
        self.tool = load_tool()
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.v5_path = Path(self._temp.name) / "v5.sqlite"
        self.v3_path = Path(self._temp.name) / "v3.sqlite"
        make_v5(self.v5_path)
        self.v3 = sqlite3.connect(self.v3_path)
        self.v3.row_factory = sqlite3.Row
        self.v3.executescript(SCHEMA.read_text(encoding="utf-8"))
        self.addCleanup(self.v3.close)
        standards, aliases = self.tool.load_seed(self.open_v5())
        self.standards = standards
        self.aliases = aliases

    def seed(self) -> int:
        ids, preserved = self.tool.seed_catalogue(self.v3, self.standards, self.aliases)
        self.v3.commit()
        self.ids = ids
        return preserved

    def item(self, item_key: str) -> sqlite3.Row:
        return self.v3.execute(
            "SELECT * FROM standard_items WHERE item_key=?", (item_key,)
        ).fetchone()

    def test_reseed_keeps_approved_lifecycle_and_language_status(self) -> None:
        self.seed()
        self.v3.execute(
            "UPDATE standard_items SET item_status='approved', "
            "canonical_zh_status='approved' WHERE item_key='user:澳門特區負債證明書'"
        )
        self.v3.execute(
            "UPDATE standard_items SET item_status='approved', "
            "canonical_pt_status='approved' WHERE item_key='user:pt:TESOURO PÚBLICO'"
        )
        self.v3.commit()

        preserved = self.seed()

        self.assertEqual(preserved, 3)
        for key in ("user:澳門特區負債證明書", "user:pt:TESOURO PÚBLICO"):
            with self.subTest(key=key):
                row = self.item(key)
                self.assertEqual(row["item_status"], "approved")
        self.assertEqual(
            self.item("user:澳門特區負債證明書")["canonical_zh_status"], "approved"
        )
        self.assertEqual(
            self.item("user:pt:TESOURO PÚBLICO")["canonical_pt_status"], "approved"
        )

    def test_reseed_keeps_retired_state(self) -> None:
        self.seed()
        self.v3.execute(
            "UPDATE standard_items SET item_status='retired' "
            "WHERE item_key='user:澳門特區負債證明書'"
        )
        self.v3.commit()

        self.seed()

        self.assertEqual(self.item("user:澳門特區負債證明書")["item_status"], "retired")

    def test_reseed_does_not_overwrite_curated_names_parent_or_notes(self) -> None:
        self.seed()
        cash = self.item("user:現金")
        debt = self.item("user:澳門特區負債證明書")
        self.v3.execute(
            "UPDATE standard_items SET canonical_zh='現金及等價物', notes='curated' "
            "WHERE standard_item_id=?",
            (cash["standard_item_id"],),
        )
        self.v3.execute(
            "UPDATE standard_items SET parent_standard_item_id=? WHERE standard_item_id=?",
            (cash["standard_item_id"], debt["standard_item_id"]),
        )
        self.v3.commit()

        self.seed()

        cash = self.item("user:現金")
        debt = self.item("user:澳門特區負債證明書")
        self.assertEqual(cash["canonical_zh"], "現金及等價物")
        self.assertEqual(cash["notes"], "curated")
        self.assertEqual(debt["parent_standard_item_id"], cash["standard_item_id"])

    def test_reseed_fills_missing_fields_for_new_seed_rows(self) -> None:
        self.seed()
        v5 = self.open_v5()
        v5.execute(
            "INSERT INTO standard_labels VALUES (?, ?, ?, ?, ?, ?, ?)",
            (4, "user:新科目", "新科目", None, None, "draft", 1),
        )
        v5.commit()
        standards, aliases = self.tool.load_seed(v5)
        v5.close()

        self.tool.seed_catalogue(self.v3, standards, aliases)
        self.v3.commit()

        row = self.item("user:新科目")
        self.assertIsNotNone(row)
        self.assertEqual(row["item_status"], "draft")
        parent = self.item("user:現金")
        self.assertEqual(row["parent_standard_item_id"], parent["standard_item_id"])


if __name__ == "__main__":
    unittest.main()
