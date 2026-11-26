"""Chart and overview rules for the initial banking dashboard.

The fixture is synthetic; Tony's operational database is never read or written.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from phase1_dashboard.app import create_app  # noqa: E402
from phase1_dashboard.charts import build_chart_view, chart_slot  # noqa: E402
from phase1_dashboard.scope import COMPARISON_DATES, LEGAL_BANKS  # noqa: E402
from tests.phase1_fixture import build_fixture  # noqa: E402


def _entity(
    entity_id: str,
    name: str,
    *,
    registry_status: str = "approved",
    merged_into_entity_id: str | None = None,
) -> dict:
    return {
        "entity_id": entity_id,
        "entity_key": f"bank:{entity_id}",
        "entity_type": "bank",
        "canonical_zh": name,
        "canonical_pt": None,
        "canonical_en": None,
        "parent_entity_id": None,
        "registry_status": registry_status,
        "merged_into_entity_id": merged_into_entity_id,
    }


def _statement(
    entity_id: str,
    name: str,
    date: str,
    *,
    adopted: bool = True,
    statement_type: str = "balance_sheet",
    reporting_scope: str = "bank",
    currency: str = "MOP",
    unit_scale: int = 1,
    decimal_places: int | None = 2,
    statement_id: str | None = None,
) -> dict:
    return {
        "statement_id": statement_id or f"{entity_id}-{date}",
        "entity_id": entity_id,
        "canonical_zh": name,
        "entity_type": "branch",
        "statement_date": date,
        "statement_type": statement_type,
        "statement_status": "active" if adopted else "proposed",
        "reporting_scope": reporting_scope,
        "currency": currency,
        "unit_scale": unit_scale,
        "amount_decimal_places": decimal_places,
        "identity_status": "proposed",
        "metadata_status": "proposed",
        "adopted_statement_extraction_id": "ext" if adopted else None,
        "entity_name_raw": name,
        "review_status": "reviewed" if not adopted else None,
    }


def _candidate(key: str, *, item_status: str = "approved") -> dict:
    return {
        "item_key": key,
        "item_zh": key,
        "item_status": item_status,
        "merged_into_standard_item_id": None,
        "catalogue_current": item_status == "approved",
    }


def _row(
    item_key: str,
    minor: int | None,
    *,
    keys: list[str] | None = None,
    debit_state: str = "amount",
    credit_state: str = "blank",
    credit_minor: int | None = None,
    item_status: str = "approved",
    currency: str = "MOP",
    unit_scale: int = 1,
    decimal_places: int | None = 2,
    reporting_scope: str = "bank",
    row_type: str = "item",
    mapping_status: str = "proposed",
    statement_id: str = "s",
    statement_row_id: str = "sr",
    side: str = "debit",
) -> dict:
    chosen = keys or [item_key]
    return {
        "statement_id": statement_id,
        "statement_row_id": statement_row_id,
        "statement_type": "balance_sheet",
        "label": item_key,
        "label_raw": item_key,
        "row_type": row_type,
        "statement_order": 1,
        "mapping_status": mapping_status,
        "match_methods": ["bilingual_exact"],
        "candidates": [
            _candidate(key, item_status=item_status) for key in chosen
        ],
        "catalogue_current": all(
            _candidate(key, item_status=item_status)["catalogue_current"]
            for key in chosen
        ) and len(chosen) == 1,
        "debit_state": debit_state,
        "credit_state": credit_state,
        "debit": {
            "minor": minor if side == "debit" else None,
            "text": None if minor is None else str(minor),
            "reading": None,
        },
        "credit": {
            "minor": credit_minor,
            "text": None if credit_minor is None else str(credit_minor),
            "reading": None,
        },
        "currency": currency,
        "unit_scale": unit_scale,
        "metadata_compliant": currency == "MOP" and unit_scale == 1,
        "amount_decimal_places": decimal_places,
        "reporting_scope": reporting_scope,
    }


def _cell(statements: list[dict], *, not_adopted: list[dict] | None = None) -> dict:
    return {
        "kind": "usable" if statements else "gap",
        "reason": None if statements else "沒有報表",
        "statements": [
            {
                "statement_id": statement["statement_id"],
                "statement_type": statement["statement_type"],
                "reporting_scope": statement["reporting_scope"],
                "currency": statement["currency"],
                "unit_scale": statement["unit_scale"],
                "amount_decimal_places": statement["amount_decimal_places"],
                "mapping_counts": {
                    "approved": 0, "proposed": 1, "insert-direct": 0, "unmapped": 0,
                },
            }
            for statement in statements
        ],
        "not_adopted": not_adopted or [],
        "not_adopted_reason": "在窗內，reviewed，未 adopted" if not_adopted else None,
        "mixed_statement_types": False,
    }


class ChartSlotTests(unittest.TestCase):
    def test_exact_row_beside_ambiguous_row_is_still_ambiguous(self) -> None:
        statement = _statement("e0", LEGAL_BANKS[0], COMPARISON_DATES[1])
        rows = {
            statement["statement_id"]: [
                _row("user:現金", 10, statement_id=statement["statement_id"]),
                _row(
                    "user:現金",
                    20,
                    keys=["user:現金", "user:其他資產"],
                    statement_id=statement["statement_id"],
                ),
            ]
        }
        slot = chart_slot(_cell([statement]), rows, "user:現金")
        self.assertEqual(slot["status"], "ambiguous")
        self.assertFalse(slot["included_in_chart"])
        self.assertIsNone(slot["point"])

    def test_decimal_places_missing_is_not_assumed_to_be_two(self) -> None:
        statement = _statement("e0", LEGAL_BANKS[0], COMPARISON_DATES[1], decimal_places=None)
        rows = {
            statement["statement_id"]: [
                _row(
                    "user:現金",
                    10,
                    decimal_places=None,
                    statement_id=statement["statement_id"],
                )
            ]
        }
        slot = chart_slot(_cell([statement]), rows, "user:現金")
        self.assertEqual(slot["status"], "decimal_places")
        self.assertIsNone(slot["point"])

    def test_competing_nom_adopted_balance_sheet_blocks_the_point(self) -> None:
        adopted = _statement("e0", LEGAL_BANKS[0], COMPARISON_DATES[1])
        competitor = _statement(
            "e0", LEGAL_BANKS[0], COMPARISON_DATES[1], adopted=False,
            statement_id="competitor",
        )
        rows = {
            adopted["statement_id"]: [
                _row("user:現金", 10, statement_id=adopted["statement_id"])
            ]
        }
        cell = _cell(
            [adopted],
            not_adopted=[{
                "statement_id": competitor["statement_id"],
                "statement_type": competitor["statement_type"],
                "reporting_scope": competitor["reporting_scope"],
                "statement_status": competitor["statement_status"],
            }],
        )
        slot = chart_slot(cell, rows, "user:現金")
        self.assertEqual(slot["status"], "competing_statement")
        self.assertFalse(slot["included_in_chart"])

    def test_point_keeps_raw_minor_and_decimal_display(self) -> None:
        statement = _statement("e0", LEGAL_BANKS[0], COMPARISON_DATES[1])
        rows = {
            statement["statement_id"]: [
                _row(
                    "user:現金", 123456, statement_id=statement["statement_id"],
                )
            ]
        }
        slot = chart_slot(_cell([statement]), rows, "user:現金")
        self.assertEqual(slot["status"], "chart")
        self.assertEqual(slot["point"]["minor"], 123456)
        self.assertEqual(slot["point"]["display"], "1,234.56")
        self.assertEqual(slot["point"]["row_type"], "item")


class ChartViewTests(unittest.TestCase):
    def _view(self, specs, **kwargs):
        names = LEGAL_BANKS[:4]
        entities = [_entity(f"e{index}", name) for index, name in enumerate(names)]
        statements: list[dict] = []
        rows: dict[str, list[dict]] = {}
        for index, (entity, name) in enumerate(zip(entities, names)):
            for date in COMPARISON_DATES:
                spec = specs.get((name, date))
                if spec is None:
                    continue
                statement = _statement(
                    entity["entity_id"], name, date,
                    statement_id=f"s{index}-{date}",
                    reporting_scope=spec.get("_scope", "bank"),
                    currency=spec.get("_currency", "MOP"),
                    unit_scale=spec.get("_unit_scale", 1),
                    decimal_places=spec.get("_decimal_places", 2),
                )
                statements.append(statement)
                rows[statement["statement_id"]] = [
                    _row(
                        "user:現金",
                        spec.get("minor", 0),
                        keys=spec.get("keys"),
                        row_type=spec.get("row_type", "item"),
                        item_status=spec.get("item_status", "approved"),
                        decimal_places=statement["amount_decimal_places"],
                        reporting_scope=statement["reporting_scope"],
                        statement_id=statement["statement_id"],
                        statement_row_id=f"sr-{index}-{date}",
                        side=spec.get("side", "debit"),
                    )
                ]
        return build_chart_view(
            entities=entities,
            statements=statements,
            rows_by_statement=rows,
            **kwargs,
        )

    def test_display_amount_not_raw_minor_in_svg(self) -> None:
        name_a, name_b = LEGAL_BANKS[:2]
        view = self._view(
            {
                (name_a, "2026-06-30"): {"minor": 123456},
                (name_b, "2026-06-30"): {"minor": 654321},
            },
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:現金",
            entity_id=None,
        )
        self.assertTrue(view["cross_bank"]["drawn"])
        self.assertIn("1,234.56", view["cross_bank"]["svg"])
        self.assertIn('data-minor="123456"', view["cross_bank"]["svg"])

    def test_mixed_decimal_places_blocks_the_whole_comparison(self) -> None:
        name_a, name_b = LEGAL_BANKS[:2]
        view = self._view(
            {
                (name_a, "2026-06-30"): {"minor": 100, "_decimal_places": 2},
                (name_b, "2026-06-30"): {"minor": 100, "_decimal_places": 0},
            },
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:現金",
            entity_id=None,
        )
        self.assertFalse(view["cross_bank"]["drawn"])
        self.assertIsNone(view["cross_bank"]["svg"])
        self.assertIn("小數位", view["cross_bank"]["reason"])
        self.assertTrue(all(slot["minor"] is None for slot in view["cross_bank"]["slots"]))

    def test_mixed_scope_and_row_type_blocks_comparison(self) -> None:
        name_a, name_b = LEGAL_BANKS[:2]
        view = self._view(
            {
                (name_a, "2026-06-30"): {"minor": 100},
                (name_b, "2026-06-30"): {"minor": 100, "_scope": "branch"},
            },
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:現金",
            entity_id=None,
        )
        self.assertFalse(view["cross_bank"]["drawn"])
        self.assertIn("reporting_scope", view["cross_bank"]["reason"])

        view = self._view(
            {
                (name_a, "2026-06-30"): {"minor": 100, "row_type": "item"},
                (name_b, "2026-06-30"): {"minor": 100, "row_type": "subtotal"},
            },
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:現金",
            entity_id=None,
        )
        self.assertFalse(view["cross_bank"]["drawn"])
        self.assertIn("row_type", view["cross_bank"]["reason"])

    def test_one_point_is_an_observation_not_a_trend(self) -> None:
        name = LEGAL_BANKS[0]
        view = self._view(
            {(name, "2026-06-30"): {"minor": 100}},
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:現金",
            entity_id="e0",
        )
        self.assertTrue(view["trend"]["drawn"])
        self.assertFalse(view["trend"]["is_trend"])
        self.assertEqual(view["trend"]["mode"], "observation")
        self.assertIn("單期觀察", view["trend"]["reason"])
        self.assertEqual(view["trend"]["svg"].count('class="trend-line"'), 0)
        self.assertEqual(view["trend"]["svg"].count("<circle"), 1)

    def test_missing_quarter_stays_a_gap(self) -> None:
        name = LEGAL_BANKS[0]
        view = self._view(
            {
                (name, "2026-03-31"): {"minor": 10},
                (name, "2026-09-30"): {"minor": 30},
            },
            bucket_id="legal",
            statement_date="2026-09-30",
            item_key="user:現金",
            entity_id="e0",
        )
        slots = {slot["statement_date"]: slot for slot in view["trend"]["slots"]}
        self.assertEqual(slots["2026-06-30"]["status"], "gap")
        self.assertIsNone(slots["2026-06-30"]["minor"])
        self.assertNotIn('data-minor="0"', view["trend"]["svg"])
        # A missing quarter breaks the line: no segment across the gap.
        self.assertEqual(view["trend"]["svg"].count('class="trend-line"'), 0)
        self.assertEqual(view["trend"]["svg"].count("<circle"), 2)

    def test_lifecycle_entity_is_not_drawn_as_a_current_bank(self) -> None:
        names = LEGAL_BANKS[:3]
        entities = [_entity(f"e{index}", name) for index, name in enumerate(names)]
        entities[2] = _entity(
            "e2", names[2], registry_status="merged", merged_into_entity_id="e0"
        )
        statements = []
        rows = {}
        for index, entity in enumerate(entities):
            statement = _statement(entity["entity_id"], entity["canonical_zh"], "2026-06-30")
            statements.append(statement)
            rows[statement["statement_id"]] = [
                _row("user:現金", 100 * (index + 1), statement_id=statement["statement_id"])
            ]
        view = build_chart_view(
            entities=entities,
            statements=statements,
            rows_by_statement=rows,
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:現金",
            entity_id=None,
        )
        merged = [slot for slot in view["cross_bank"]["slots"] if slot["canonical_zh"] == names[2]][0]
        self.assertEqual(merged["status"], "lifecycle")
        self.assertFalse(merged["included_in_chart"])
        self.assertNotIn(names[2], [slot["canonical_zh"] for slot in view["entities"]])

    def test_sum_and_rank_stay_off(self) -> None:
        name_a, name_b = LEGAL_BANKS[:2]
        view = self._view(
            {
                (name_a, "2026-06-30"): {"minor": 100},
                (name_b, "2026-06-30"): {"minor": 200},
            },
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:現金",
            entity_id=None,
        )
        self.assertFalse(view["summed"])
        self.assertFalse(view["ranked"])
        self.assertFalse(view["financially_validated"])
        self.assertFalse(view["cross_bank"]["summed"])
        self.assertFalse(view["cross_bank"]["ranked"])


class DashboardPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tempdir.name) / "fixture.sqlite"
        cls.meta = build_fixture(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path), base_url="http://127.0.0.1")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def test_home_is_an_overview_with_charts(self) -> None:
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("<svg", page.text)
        self.assertIn("已採納可用報表", page.text)
        self.assertIn("候選行觀察", page.text)
        self.assertIn("資料覆蓋", page.text)
        self.assertNotIn("<th>canonical_zh</th>", page.text)
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before)

    def test_bank_directory_and_indicator_pages(self) -> None:
        directory = self.client.get("/entities")
        self.assertEqual(directory.status_code, 200)
        self.assertIn("銀行資料庫", directory.text)
        self.assertIn(LEGAL_BANKS[0], directory.text)

        indicators = self.client.get("/indicators")
        self.assertEqual(indicators.status_code, 200)
        self.assertIn("總資產", indicators.text)
        self.assertIn("未啟用", indicators.text)
        # No indicator value was calculated for the catalogue.
        self.assertNotIn("已啟用正式指標</div>\n      <div class=\"value\">1", indicators.text)

    def test_chart_api_keeps_gating_flags(self) -> None:
        model = self.client.get("/api/charts").json()
        self.assertFalse(model["summed"])
        self.assertFalse(model["ranked"])
        self.assertFalse(model["financially_validated"])
        self.assertFalse(model["kpis"]["formal_indicators_enabled"])
        self.assertIn("coverage_matrix", model)

    def test_loopback_host_policy_still_applies(self) -> None:
        for path in ("/", "/entities", "/indicators"):
            response = self.client.get(path, headers={"Host": "evil.example"})
            self.assertEqual(response.status_code, 400, path)


if __name__ == "__main__":
    unittest.main()
