"""Grouped multi-select comparison rules for the phase-1 dashboard.

The fixture database is synthetic; Tony's operational database is never read
or written.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from phase1_dashboard.app import TEMPLATES, create_app  # noqa: E402
from phase1_dashboard.matrix import build_matrix_view  # noqa: E402
from phase1_dashboard.present import ScopeError  # noqa: E402
from phase1_dashboard.scope import COMPARISON_DATES, LEGAL_BANKS  # noqa: E402
from tests.phase1_fixture import build_fixture  # noqa: E402


def _entity(entity_id: str, name: str) -> dict:
    return {
        "entity_id": entity_id,
        "entity_key": f"bank:{entity_id}",
        "entity_type": "bank",
        "canonical_zh": name,
        "canonical_pt": None,
        "canonical_en": None,
        "parent_entity_id": None,
        "registry_status": "approved",
        "merged_into_entity_id": None,
    }


def _statement(entity_id: str, name: str, date: str, *, statement_id: str) -> dict:
    return {
        "statement_id": statement_id,
        "entity_id": entity_id,
        "canonical_zh": name,
        "entity_type": "branch",
        "statement_date": date,
        "statement_type": "balance_sheet",
        "statement_status": "active",
        "reporting_scope": "bank",
        "currency": "MOP",
        "unit_scale": 1,
        "amount_decimal_places": 2,
        "identity_status": "proposed",
        "metadata_status": "proposed",
        "adopted_statement_extraction_id": "ext",
        "entity_name_raw": name,
        "review_status": None,
    }


def _candidate(key: str) -> dict:
    return {
        "item_key": key,
        "item_zh": key,
        "item_status": "approved",
        "merged_into_standard_item_id": None,
        "catalogue_current": True,
    }


def _row(
    item_key: str,
    minor: int,
    *,
    statement_id: str,
    row_id: str,
    side: str = "debit",
    row_type: str = "item",
    keys: list[str] | None = None,
    mapping_status: str = "proposed",
    credit_minor: int | None = None,
    credit_state: str | None = None,
) -> dict:
    resolved_credit = (
        credit_minor
        if credit_minor is not None
        else (minor if side == "credit" else None)
    )
    if credit_state is None:
        credit_state = (
            "amount" if credit_minor is not None or side == "credit" else "blank"
        )
    return {
        "statement_id": statement_id,
        "statement_row_id": row_id,
        "statement_type": "balance_sheet",
        "label": item_key,
        "label_raw": item_key,
        "row_type": row_type,
        "statement_order": 1,
        "mapping_status": mapping_status,
        "match_methods": ["bilingual_exact"],
        "candidates": [_candidate(key) for key in (keys or [item_key])],
        "catalogue_current": keys is None,
        "debit_state": "amount" if side == "debit" else "blank",
        "credit_state": credit_state,
        "debit": {
            "minor": minor if side == "debit" else None,
            "text": str(minor) if side == "debit" else None,
            "reading": None,
        },
        "credit": {
            "minor": resolved_credit,
            "text": None if resolved_credit is None else str(resolved_credit),
            "reading": None,
        },
        "currency": "MOP",
        "unit_scale": 1,
        "metadata_compliant": True,
        "amount_decimal_places": 2,
        "reporting_scope": "bank",
    }


def _dataset(
    names: list[str],
    dates: tuple[str, ...],
    rows_by: dict[tuple[str, str], list[dict]],
):
    entities = [_entity(f"e{index}", name) for index, name in enumerate(names)]
    statements: list[dict] = []
    rows: dict[str, list[dict]] = {}
    for index, name in enumerate(names):
        for date in dates:
            statement_id = f"s{index}-{date}"
            statements.append(
                _statement(f"e{index}", name, date, statement_id=statement_id)
            )
            supplied = rows_by.get((name, date))
            if supplied:
                rows[statement_id] = supplied
    return entities, statements, rows


def _view(
    names: list[str],
    dates: tuple[str, ...],
    rows_by: dict[tuple[str, str], list[dict]],
    **kwargs,
) -> dict:
    entities, statements, rows = _dataset(names, dates, rows_by)
    params = {
        "bucket_id": "legal",
        "entity_ids": [entity["entity_id"] for entity in entities],
        "item_keys": None,
        "statement_dates": list(dates),
        **kwargs,
    }
    return build_matrix_view(
        entities=entities,
        statements=statements,
        rows_by_statement=rows,
        **params,
    )


class MatrixRulesTests(unittest.TestCase):
    def test_multi_entity_item_and_date_selection_draws_grouped_bars(self) -> None:
        names = list(LEGAL_BANKS[:2])
        dates = ("2026-06-30", "2026-09-30")
        rows = {}
        for index, name in enumerate(names):
            for date in dates:
                for key, minor in (("user:放款", 100 + index), ("user:現金", 200 + index)):
                    rows.setdefault((name, date), []).append(
                        _row(
                            key,
                            minor,
                            statement_id=f"s{index}-{date}",
                            row_id=f"{key}-{index}-{date}",
                        )
                    )
        view = _view(
            names,
            dates,
            rows,
            item_keys=["user:放款", "user:現金"],
        )
        self.assertTrue(view["drawn"])
        self.assertEqual(view["mode"], "comparison")
        self.assertEqual(view["group_count"], 4)
        self.assertEqual(view["bar_count"], 8)
        self.assertEqual(view["gap_count"], 0)
        self.assertIn("bar-date-0", view["svg"])
        self.assertIn("bar-date-1", view["svg"])
        self.assertIn('data-display="1.00"', view["svg"])
        self.assertIn('data-display="2.00"', view["svg"])
        self.assertFalse(view["summed"])
        self.assertFalse(view["ranked"])
        self.assertNotIn("100000000", view["svg"])

    def test_missing_point_is_a_gap_not_zero(self) -> None:
        names = list(LEGAL_BANKS[:2])
        dates = ("2026-06-30", "2026-09-30")
        rows = {
            (names[0], "2026-06-30"): [
                _row("user:放款", 500, statement_id=f"s0-2026-06-30", row_id="a")
            ],
            (names[1], "2026-06-30"): [
                _row("user:放款", 600, statement_id=f"s1-2026-06-30", row_id="b")
            ],
            # 2026-09-30 statements exist, but no row for the candidate.
        }
        view = _view(names, dates, rows, item_keys=["user:放款"])
        self.assertTrue(view["drawn"])
        self.assertEqual(view["bar_count"], 2)
        slots = {
            (slot["entity_id"], slot["statement_date"]): slot
            for slot in view["slots"]
        }
        missing = slots[("e0", "2026-09-30")]
        self.assertEqual(missing["status"], "absent")
        self.assertIsNone(missing["display"])
        self.assertFalse(missing["included_in_chart"])
        self.assertIn("不是 0", missing["reason"])
        self.assertNotIn('data-minor="0"', view["svg"])
        self.assertIn("bar-gap", view["svg"])

    def test_two_rows_for_one_candidate_are_not_summed(self) -> None:
        names = [LEGAL_BANKS[0]]
        dates = ("2026-06-30",)
        rows = {
            (names[0], "2026-06-30"): [
                _row("user:放款", 11, statement_id="s0-2026-06-30", row_id="a"),
                _row("user:放款", 22, statement_id="s0-2026-06-30", row_id="b"),
            ]
        }
        view = _view(names, dates, rows, item_keys=["user:放款"])
        self.assertFalse(view["drawn"])
        self.assertEqual(view["bar_count"], 0)
        self.assertEqual(view["slots"][0]["status"], "multiple_rows")
        self.assertIn("未相加", view["slots"][0]["reason"])
        self.assertIsNone(view["slots"][0]["display"])
        self.assertNotIn("33", str(view["svg"]))

    def test_signature_mismatch_blocks_the_whole_chart(self) -> None:
        names = [LEGAL_BANKS[0]]
        dates = ("2026-06-30",)
        rows = {
            (names[0], "2026-06-30"): [
                _row(
                    "user:放款", 100,
                    statement_id="s0-2026-06-30", row_id="a", row_type="item",
                ),
                _row(
                    "user:其他資產", 200,
                    statement_id="s0-2026-06-30", row_id="b", row_type="subtotal",
                ),
            ]
        }
        view = _view(
            names,
            dates,
            rows,
            item_keys=["user:放款", "user:其他資產"],
        )
        self.assertFalse(view["drawn"])
        self.assertIsNone(view["svg"])
        self.assertIn("row_type", view["reason"])
        self.assertTrue(
            all(slot["included_in_chart"] is False for slot in view["slots"])
        )
        # The amount stays traceable in the table even though it is not drawn.
        self.assertEqual(
            sorted(slot["display"] for slot in view["slots"]),
            ["1.00", "2.00"],
        )
        self.assertTrue(all(slot["signature_mismatch"] for slot in view["slots"]))

    def test_zero_counterpart_is_drawn_and_flagged_for_review(self) -> None:
        names = [LEGAL_BANKS[0]]
        dates = ("2026-06-30",)
        rows = {
            (names[0], "2026-06-30"): [
                _row(
                    "user:放款",
                    100,
                    statement_id="s0-2026-06-30",
                    row_id="a",
                    credit_state="amount",
                    credit_minor=0,
                )
            ]
        }
        view = _view(names, dates, rows, item_keys=["user:放款"])
        self.assertTrue(view["drawn"])
        self.assertEqual(view["bar_count"], 1)
        slot = view["slots"][0]
        self.assertEqual(slot["side"], "debit")
        self.assertEqual(slot["display"], "1.00")
        self.assertTrue(slot["zero_counterpart"])
        self.assertIn("對邊 0.00", view["svg"])
        view["page_warnings"] = []
        html = TEMPLATES.get_template("matrix.html").render({
            "not_comparable": "未可安全比較",
            "window": list(COMPARISON_DATES),
            "buckets": [],
            "page_warnings": [],
            "mapping_statuses": (),
            "aggregate": {"reason": "不加總"},
            "model": view,
        })
        self.assertIn("對邊為 0.00；按 0 對邊規則選借方", html)

    def test_signed_display_allows_asset_and_liability_together(self) -> None:
        names = [LEGAL_BANKS[0]]
        dates = ("2026-06-30",)
        rows = {
            (names[0], "2026-06-30"): [
                _row(
                    "user:放款",
                    100,
                    statement_id="s0-2026-06-30",
                    row_id="a",
                ),
                _row(
                    "user:現金",
                    200,
                    statement_id="s0-2026-06-30",
                    row_id="b",
                    side="credit",
                ),
            ]
        }
        unsigned = _view(
            names, dates, rows, item_keys=["user:放款", "user:現金"]
        )
        self.assertFalse(unsigned["drawn"])
        self.assertIn("借貸方向", unsigned["reason"])
        self.assertIn("貸方 × -1", unsigned["reason"])

        signed = _view(
            names,
            dates,
            rows,
            item_keys=["user:放款", "user:現金"],
            signed=True,
        )
        self.assertTrue(signed["drawn"])
        self.assertTrue(signed["signed"])
        self.assertEqual(signed["bar_count"], 2)
        credit = next(
            slot for slot in signed["slots"] if slot["item_key"] == "user:現金"
        )
        self.assertEqual(credit["side"], "credit")
        self.assertEqual(credit["display"], "-2.00")
        self.assertEqual(credit["raw_display"], "2.00")
        self.assertTrue(credit["signed_for_display"])
        debit = next(
            slot for slot in signed["slots"] if slot["item_key"] == "user:放款"
        )
        self.assertEqual(debit["display"], "1.00")
        self.assertFalse(debit["signed_for_display"])
        self.assertIn('data-display="-2.00"', signed["svg"])
        self.assertIn("貸方 × -1（借方不變）", signed["svg"])
        self.assertIn("貸方已 × -1", signed["signature_note"])
        self.assertFalse(signed["summed"])

    def test_unknown_entity_or_item_is_reported_not_silently_used(self) -> None:
        names = [LEGAL_BANKS[0]]
        dates = ("2026-06-30",)
        rows = {
            (names[0], "2026-06-30"): [
                _row("user:放款", 100, statement_id="s0-2026-06-30", row_id="a")
            ]
        }
        view = _view(
            names,
            dates,
            rows,
            entity_ids=["nope"],
            item_keys=["user:不存在"],
        )
        self.assertEqual(view["ignored_entities"], ["nope"])
        self.assertEqual(view["ignored_items"], ["user:不存在"])
        self.assertEqual(view["selected_entities"], [])
        self.assertEqual(view["selected_items"], [])
        self.assertFalse(view["drawn"])
        self.assertIn("未選機構", view["reason"])

    def test_invalid_bucket_and_date_raise_scope_error(self) -> None:
        names = [LEGAL_BANKS[0]]
        with self.assertRaises(ScopeError):
            _view(names, COMPARISON_DATES, {}, bucket_id="all")
        with self.assertRaises(ScopeError):
            _view(names, COMPARISON_DATES, {}, statement_dates=["2025-12-31"])

    def test_selection_limits_are_reported(self) -> None:
        names = list(LEGAL_BANKS[:5])
        keys = [f"user:項目{index}" for index in range(6)]
        dates = ("2026-06-30",)
        rows = {}
        for index, name in enumerate(names):
            rows[(name, "2026-06-30")] = [
                _row(
                    key,
                    100,
                    statement_id=f"s{index}-2026-06-30",
                    row_id=f"{key}-{index}",
                )
                for key in keys
            ]
        too_many_groups = _view(names, dates, rows, item_keys=keys)
        self.assertFalse(too_many_groups["drawn"])
        self.assertIn("超過上限 24 組", too_many_groups["reason"])
        self.assertEqual(too_many_groups["groups"], [])

        many_keys = keys + ["user:第七項"]
        one_entity_rows = {
            (names[0], "2026-06-30"): [
                _row(
                    key,
                    100,
                    statement_id=f"s0-2026-06-30",
                    row_id=f"{key}-0",
                )
                for key in many_keys
            ]
        }
        too_many_items = _view(
            [names[0]],
            dates,
            one_entity_rows,
            item_keys=many_keys,
        )
        self.assertFalse(too_many_items["drawn"])
        self.assertIn("超過上限 6 個", too_many_items["reason"])


class MatrixPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tempdir.name) / "fixture.sqlite"
        build_fixture(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path), base_url="http://127.0.0.1")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def test_page_has_multi_selects_and_trace_table(self) -> None:
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        page = self.client.get("/matrix")
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="entity_id" multiple', page.text)
        self.assertIn('name="item_key" multiple', page.text)
        self.assertIn('name="statement_date" multiple', page.text)
        self.assertIn('name="signed"', page.text)
        self.assertIn("貸方 × -1（資產與負債同圖）", page.text)
        self.assertIn("逐點追溯", page.text)
        self.assertIn("缺口不是 0", page.text)
        self.assertIn("沒有合計、沒有排名", page.text)
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before)

    def test_submitted_empty_selection_does_not_fall_back_to_defaults(self) -> None:
        page = self.client.get("/matrix", params={"submitted": "1", "bucket": "legal"})
        self.assertEqual(page.status_code, 200)
        self.assertIn("未選機構", page.text)

    def test_api_accepts_repeated_parameters(self) -> None:
        coverage = self.client.get("/api/coverage").json()
        legal = next(
            bucket for bucket in coverage["buckets"] if bucket["id"] == "legal"
        )
        entity = next(item for item in legal["entities"] if item["entity_id"])
        body = self.client.get(
            "/api/matrix",
            params=[
                ("bucket", "legal"),
                ("entity_id", entity["entity_id"]),
                ("statement_date", "2026-06-30"),
            ],
        ).json()
        self.assertIn("slots", body)
        self.assertEqual(body["selected_dates"], ["2026-06-30"])
        self.assertEqual(body["selected_entities"], [entity["entity_id"]])
        self.assertFalse(body["summed"])
        self.assertFalse(body["ranked"])
        signed = self.client.get(
            "/api/matrix", params={"bucket": "legal", "signed": "1"}
        ).json()
        self.assertTrue(signed["signed"])
        self.assertIn("貸方（credit）金額 × -1", signed["caption"])

    def test_invalid_date_is_a_dashboard_error(self) -> None:
        response = self.client.get(
            "/api/matrix",
            params={"bucket": "legal", "statement_date": "2025-12-31"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("2026-03-31", response.json()["error"])

    def test_loopback_host_policy_applies(self) -> None:
        response = self.client.get("/matrix", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("中國銀行(澳門)股份有限公司", response.text)


if __name__ == "__main__":
    unittest.main()
