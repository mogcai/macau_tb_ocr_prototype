"""Tests for the P2-A page classification proposal rules."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.build_page_classification_worksheet import classify, entity_candidate  # noqa: E402


class PageClassificationWorksheetTests(unittest.TestCase):
    def test_balance_sheet_and_entity_from_header(self) -> None:
        result = classify(
            "BANCO TESTE | DEMONSTRAÇÃO DA POSIÇÃO FINANCEIRA",
            ["Activos", "Caixa"],
            30,
            12,
        )
        self.assertEqual(result["proposed_page_role"], "balance_sheet")
        self.assertEqual(result["proposed_v3_page_role"], "financial_statement")
        self.assertEqual(result["proposed_statement_type"], "balance_sheet")
        self.assertEqual(result["proposed_entity_name"], "BANCO TESTE")

    def test_shareholder_list(self) -> None:
        result = classify(
            "Lista de detentores de mais de 5% do capital",
            ["Accionistas Qualificados"],
            10,
            0,
        )
        self.assertEqual(result["proposed_page_role"], "shareholder_list")
        self.assertEqual(result["proposed_v3_page_role"], "shareholder_list")

    def test_audit_report(self) -> None:
        result = classify(
            "Relatorio do Auditor Independente sobre as Demonstracoes Financeiras",
            [],
            6,
            0,
        )
        self.assertEqual(result["proposed_page_role"], "audit_report")
        self.assertEqual(result["proposed_v3_page_role"], "audit_report")

    def test_non_bank_contract(self) -> None:
        result = classify(
            "南光石油化工有限公司 | 氹仔客運碼頭油庫運營合同項目",
            ["其他綜合收益"],
            6,
            0,
        )
        self.assertEqual(result["proposed_page_role"], "other")
        self.assertEqual(result["proposed_v3_page_role"], "other")

    def test_unknown_without_evidence(self) -> None:
        result = classify("ral", ["109,297,571,349"], 38, 0)
        self.assertEqual(result["proposed_page_role"], "unknown")
        self.assertEqual(result["review_status"], "needs_review")

    def test_entity_from_contaminated_header(self) -> None:
        entity = entity_candidate(
            "386 # 17H - 2026 #: 4/ 29 H | Banco de Construção da China, S.A. Sucursal de Macau",
            [],
        )
        self.assertIn("Banco de Construção", entity)

    def test_entity_from_first_labels_when_header_is_header_noise(self) -> None:
        entity = entity_candidate(
            "548 # 17 # - 2026 : 4 / 29 H | DEMONSTRAÇÃO DA POSIÇÃO FINANCEIRA",
            ["BANCO COMERCIAL HUA NAN, S.A., SU", "Activos"],
        )
        self.assertIn("HUA NAN", entity)


if __name__ == "__main__":
    unittest.main()
