"""Tests for the debit/credit adoption recommendation rule."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.build_statement_validation_worksheet import decide_validation  # noqa: E402


class StatementValidationRuleTests(unittest.TestCase):
    def test_equal_totals_suggest_adopt(self) -> None:
        self.assertEqual(
            decide_validation(1000, 1000, ["parent_context_unknown"], 65, 1),
            ("ready_for_adoption", "adopt"),
        )

    def test_unequal_totals_hold(self) -> None:
        self.assertEqual(
            decide_validation(1000, 999, ["debit_credit_mismatch"], 65, 1),
            ("needs_review", "hold"),
        )

    def test_missing_rows_reject(self) -> None:
        self.assertEqual(
            decide_validation(0, 0, ["no_statement_rows"], 0, 1),
            ("reject", "reject"),
        )

    def test_critical_structural_flag_holds_even_if_totals_equal(self) -> None:
        self.assertEqual(
            decide_validation(1000, 1000, ["row_count_mismatch"], 65, 1),
            ("needs_review", "hold"),
        )


if __name__ == "__main__":
    unittest.main()
