"""Keep project, documentation and database schema versions distinct and synced."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import macau_tb_ocr as ocr  # noqa: E402
import tools.build_database as db  # noqa: E402


class VersionSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    def test_project_version_matches_code_and_config(self):
        self.assertEqual(ocr.APP_VERSION, self.version)
        self.assertEqual(db.APP_VERSION, self.version)
        config = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["version"], self.version)

    def test_document_titles_use_project_version(self):
        expected = f"v{self.version}"
        for name in (
            "README_zh_HK.md", "README_AGENT.md", "SKILL.md", "MACOS_SETUP.md",
        ):
            first_line = (ROOT / name).read_text(encoding="utf-8").splitlines()[0]
            self.assertIn(expected, first_line, name)
        database_doc = (ROOT / "DATABASE.md").read_text(encoding="utf-8")
        self.assertIn(expected, database_doc.splitlines()[0])
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"**Current prototype:** {expected}", readme)

    def test_schema_version_is_independent(self):
        self.assertEqual(db.SCHEMA_VERSION, 5)


if __name__ == "__main__":
    unittest.main()
