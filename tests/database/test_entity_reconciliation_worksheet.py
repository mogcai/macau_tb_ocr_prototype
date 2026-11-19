"""Tests for P2-B entity reconciliation proposal rules."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.build_entity_reconciliation_worksheet import propose  # noqa: E402


class EntityReconciliationWorksheetTests(unittest.TestCase):
    def _bank(self, bank_id: int, status: str = "provisional") -> dict:
        return {
            "bank_id": bank_id,
            "display_name": f"bank-{bank_id}",
            "registry_status": status,
        }

    def test_junk_entity_is_excluded(self) -> None:
        result = propose(self._bank(113), approved_pages=0, alias_count=0)
        self.assertEqual(result["proposed_action"], "exclude_non_entity")

    def test_survivor_is_kept(self) -> None:
        result = propose(self._bank(11), approved_pages=1, alias_count=0)
        self.assertEqual(result["proposed_action"], "keep_survivor")

    def test_merge_candidate_points_to_survivor(self) -> None:
        result = propose(self._bank(49), approved_pages=1, alias_count=0)
        self.assertEqual(result["proposed_action"], "merge_into_survivor")
        self.assertEqual(result["proposed_survivor_entity_id"], "11")

    def test_historical_name_merges_into_survivor(self) -> None:
        result = propose(self._bank(105), approved_pages=1, alias_count=0)
        self.assertEqual(result["proposed_action"], "merge_into_survivor")
        self.assertEqual(result["proposed_survivor_entity_id"], "11")
        self.assertEqual(result["review_reason"], "historical_name_of_11_49")

    def test_provisional_with_approved_pages(self) -> None:
        result = propose(self._bank(7), approved_pages=2, alias_count=0)
        self.assertEqual(result["proposed_action"], "approve_provisional")

    def test_approved_with_approved_pages(self) -> None:
        result = propose(self._bank(7, status="approved"), approved_pages=2, alias_count=0)
        self.assertEqual(result["proposed_action"], "keep_approved")

    def test_no_page_evidence_needs_review(self) -> None:
        result = propose(self._bank(7), approved_pages=0, alias_count=0)
        self.assertEqual(result["proposed_action"], "needs_review")


if __name__ == "__main__":
    unittest.main()
