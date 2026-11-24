"""Rules for the local phase-1 dashboard.

The fixture is a synthetic V3 database. It does not copy Tony's snapshot.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from phase1_dashboard.app import TEMPLATES, create_app, host_header_allowed  # noqa: E402
from phase1_dashboard import queries  # noqa: E402
from phase1_dashboard.db import DashboardDataError, connect_readonly, is_v5_analysis_path  # noqa: E402
from phase1_dashboard.present import (  # noqa: E402
    aggregate_placeholder,
    build_compare,
    build_coverage,
    classify_row,
    format_minor,
    is_usable_statement,
)
from phase1_dashboard.scope import (  # noqa: E402
    ADOPTED_NAMES,
    COMPARISON_DATES,
    FOREIGN_BRANCHES,
    LEGAL_BANKS,
    RELATED_ENTITIES,
)
from phase1_dashboard.__main__ import resolve_host  # noqa: E402
from tests.phase1_fixture import (  # noqa: E402
    POISON_COMMERCIAL_GAP,
    POISON_EXCLUDED,
    POISON_NOT_ADOPTED,
    POISON_OTHER_TYPE,
    POISON_OUTSIDE,
    POISON_OUTSIDER,
    TOTALS,
    build_fixture,
)

POISONS = (
    POISON_NOT_ADOPTED,
    POISON_OUTSIDE,
    POISON_OUTSIDER,
    POISON_EXCLUDED,
    POISON_OTHER_TYPE,
    POISON_COMMERCIAL_GAP,
)


def _entity(entity_id: str, name: str, entity_type: str = "bank") -> dict:
    return {
        "entity_id": entity_id,
        "entity_key": f"bank:{entity_id}",
        "entity_type": entity_type,
        "canonical_zh": name,
        "canonical_pt": None,
        "canonical_en": None,
        "parent_entity_id": None,
        "registry_status": "proposed",
    }


def _statement(
    entity_id: str,
    name: str,
    date: str,
    *,
    adopted: bool = True,
    status: str = "active",
    statement_type: str = "balance_sheet",
    statement_id: str | None = None,
) -> dict:
    return {
        "statement_id": statement_id or (
            f"{entity_id}-{date}" if statement_type == "balance_sheet" else f"{entity_id}-{date}-{statement_type}"
        ),
        "entity_id": entity_id,
        "canonical_zh": name,
        "entity_type": "branch",
        "statement_date": date,
        "statement_type": statement_type,
        "statement_status": status if adopted else "proposed",
        "reporting_scope": "branch",
        "currency": "MOP",
        "unit_scale": 1,
        "amount_decimal_places": 2,
        "identity_status": "proposed",
        "metadata_status": "proposed",
        "adopted_statement_extraction_id": "ext" if adopted else None,
        "entity_name_raw": name,
        "review_status": None,
    }


class RuleTests(unittest.TestCase):
    def test_identity_proposed_stays_usable(self) -> None:
        statement = _statement("e", LEGAL_BANKS[0], "2026-06-30")
        self.assertEqual(statement["identity_status"], "proposed")
        self.assertTrue(is_usable_statement(statement))

    def test_outside_window_and_unadopted_are_not_usable(self) -> None:
        self.assertFalse(is_usable_statement(_statement("e", LEGAL_BANKS[0], "2025-12-31")))
        self.assertFalse(is_usable_statement(
            _statement("e", LEGAL_BANKS[0], "2026-03-31", adopted=False)
        ))
        self.assertFalse(is_usable_statement(
            _statement("e", "澳門華人銀行股份有限公司", "2026-06-30")
        ))

    def test_known_gap_names_are_not_hard_coded_as_missing(self) -> None:
        name = "恒生銀行有限公司澳門分行"
        model = build_coverage(
            entities=[_entity("hs", name, "branch")],
            statements=[_statement("hs", name, "2026-03-31")],
            rows_by_statement={},
        )
        foreign = next(bucket for bucket in model["buckets"] if bucket["id"] == "foreign_branch")
        entity = next(item for item in foreign["entities"] if item["canonical_zh"] == name)
        self.assertEqual(entity["periods"]["2026-03-31"]["kind"], "usable")
        self.assertIsNone(entity["periods"]["2026-03-31"]["amounts"])

    def test_duplicate_names_are_not_merged(self) -> None:
        name = LEGAL_BANKS[2]
        model = build_coverage(
            entities=[_entity("a", name), _entity("b", name)],
            statements=[
                _statement("a", name, "2026-06-30"),
                _statement("b", name, "2026-06-30"),
            ],
            rows_by_statement={},
        )
        legal = next(bucket for bucket in model["buckets"] if bucket["id"] == "legal")
        rows = [item for item in legal["entities"] if item["canonical_zh"] == name]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(item["duplicate_name"] and item["merged"] is False for item in rows))
        self.assertIsNone(legal["entity_total"])

    def test_aggregate_is_blocked(self) -> None:
        placeholder = aggregate_placeholder()
        self.assertFalse(placeholder["enabled"])
        self.assertFalse(placeholder["computed"])
        self.assertIsNone(placeholder["amounts"])
        self.assertIsNone(placeholder["legal_bank_sum"])
        self.assertIsNone(placeholder["all_fifteen_sum"])
        self.assertIsNone(placeholder["column_total"])

    def test_mapping_status_and_unmapped_zero_is_not_safe(self) -> None:
        approved = classify_row(
            "r1",
            [
                {"statement_row_id": "r1", "mapping_status": "approved", "match_method": "manual", "item_key": "user:現金", "item_zh": "現金", "item_status": "approved", "merged_into_standard_item_id": None},
                {"statement_row_id": "r1", "mapping_status": "proposed", "match_method": "alias_exact", "item_key": "user:現金", "item_zh": "現金", "item_status": "approved"},
                {"statement_row_id": "r1", "mapping_status": "rejected", "match_method": "fuzzy", "item_key": "user:其他", "item_zh": "其他", "item_status": "retired"},
            ],
            None,
        )
        self.assertEqual(approved["mapping_status"], "approved")
        self.assertTrue(approved["also_proposed"])
        self.assertTrue(approved["catalogue_current"])
        self.assertFalse(approved["presented_as_approved_scale"])
        self.assertIsNone(approved["rank"])
        retired = classify_row(
            "r5",
            [{
                "statement_row_id": "r5",
                "mapping_status": "approved",
                "match_method": "manual",
                "item_key": "user:已退役",
                "item_status": "retired",
                "merged_into_standard_item_id": None,
                "standard_item_id": "si-retired",
            }],
            None,
        )
        self.assertEqual(retired["mapping_status"], "approved")
        self.assertFalse(retired["catalogue_current"])
        self.assertEqual(retired["candidates"][0]["item_status"], "retired")
        self.assertFalse(retired["presented_as_approved_scale"])
        proposed = classify_row(
            "r2",
            [{"statement_row_id": "r2", "mapping_status": "proposed", "match_method": "bilingual_exact", "item_key": "user:總計", "item_zh": "總計"}],
            None,
        )
        self.assertEqual(proposed["mapping_status"], "proposed")
        self.assertTrue(any("不是總資產" in text for text in proposed["draft_warnings"]))
        unmapped = classify_row(
            "r3",
            [{"statement_row_id": "r3", "mapping_status": "rejected", "match_method": "fuzzy", "item_key": "user:其他"}],
            None,
        )
        self.assertEqual(unmapped["mapping_status"], "unmapped")
        inserted = classify_row(
            "r4",
            [],
            {
                "standard_item_id": "si-merged",
                "item_key": "user:已合併",
                "item_zh": "已合併",
                "item_status": "merged",
                "merged_into_standard_item_id": "si-cash",
            },
        )
        self.assertEqual(inserted["mapping_status"], "insert-direct")
        self.assertFalse(inserted["catalogue_current"])
        self.assertEqual(inserted["candidates"][0]["item_status"], "merged")
        self.assertEqual(inserted["candidates"][0]["merged_into_standard_item_id"], "si-cash")
        model = build_coverage(
            entities=[_entity("e", LEGAL_BANKS[0])],
            statements=[_statement("e", LEGAL_BANKS[0], "2026-06-30")],
            rows_by_statement={"e-2026-06-30": [proposed]},
        )
        self.assertEqual(model["mapping"]["unmapped"], 0)
        self.assertFalse(model["mapping"]["unmapped_zero_means_safely_mapped"])
        self.assertFalse(model["mapping"]["safely_cross_comparable"])

    def test_share_and_amount_formatting_do_not_rescale(self) -> None:
        shown = format_minor(111111, 2)
        self.assertEqual(shown["minor"], 111111)
        self.assertEqual(shown["reading"], "1,111.11")
        raw = format_minor(50, None)
        self.assertEqual(raw["text"], "50")
        self.assertIsNone(raw["reading"])
        self.assertIsNone(format_minor(None, 2)["minor"])
        compared = build_compare(
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:總計",
            display="share",
            entities=[_entity("a", LEGAL_BANKS[0]), _entity("b", LEGAL_BANKS[1])],
            statements=[
                _statement("a", LEGAL_BANKS[0], "2026-06-30"),
                _statement("b", LEGAL_BANKS[1], "2026-06-30"),
            ],
            rows_by_statement={},
        )
        self.assertIsNone(compared["share"]["value"])
        self.assertFalse(compared["share"]["available"])
        self.assertIsNone(compared["column_total"])
        self.assertFalse(compared["summed"])

    def test_compare_rejects_a_mixed_or_outside_slice(self) -> None:
        from phase1_dashboard.present import ScopeError
        with self.assertRaises(ScopeError):
            build_compare(
                bucket_id="all",
                statement_date="2026-06-30",
                item_key=None,
                display="amount",
                entities=[],
                statements=[],
                rows_by_statement={},
            )
        with self.assertRaises(ScopeError):
            build_compare(
                bucket_id="legal",
                statement_date="2025-12-31",
                item_key=None,
                display="amount",
                entities=[],
                statements=[],
                rows_by_statement={},
            )

    def test_adopted_names_match_the_three_buckets(self) -> None:
        self.assertEqual(len(LEGAL_BANKS), 8)
        self.assertEqual(len(RELATED_ENTITIES), 3)
        self.assertEqual(len(FOREIGN_BRANCHES), 4)
        self.assertEqual(len(ADOPTED_NAMES), 15)
        self.assertEqual(COMPARISON_DATES, ("2026-03-31", "2026-06-30", "2026-09-30"))
        self.assertIn("香港上海滙豐銀行有限公司澳門分行", FOREIGN_BRANCHES)
        self.assertNotIn("澳門華人銀行股份有限公司", ADOPTED_NAMES)

    def test_compare_distinguishes_missing_statement_balance_sheet_and_candidate(self) -> None:
        absent_name, income_name, sheet_name, value_name = LEGAL_BANKS[:4]
        income_only = _statement("income", income_name, "2026-06-30", statement_type="income_statement")
        sheet = _statement("sheet", sheet_name, "2026-06-30")
        sheet_income = _statement("sheet", sheet_name, "2026-06-30", statement_type="income_statement")
        valued = _statement("valued", value_name, "2026-06-30")
        income_amount = 919_000_001
        leaked_from_income = 919_000_002
        balance_other = 919_000_003
        balance_hit = 919_000_004

        def candidate(item_key: str, item_status: str = "approved") -> dict:
            return {
                "item_key": item_key,
                "item_zh": item_key,
                "item_status": item_status,
                "merged_into_standard_item_id": None,
                "catalogue_current": item_status == "approved",
            }

        def row(statement: dict, item_key: str, minor: int, item_status: str = "approved") -> dict:
            return {
                "statement_id": statement["statement_id"],
                "statement_type": statement["statement_type"],
                "mapping_status": "proposed",
                "candidates": [candidate(item_key, item_status)],
                "debit": {"minor": minor, "text": str(minor), "reading": None, "note": None},
                "credit": {"minor": None, "text": None, "reading": None, "note": None},
                "currency": "MOP",
                "unit_scale": 1,
                "amount_decimal_places": 2,
                "reporting_scope": "bank",
                "match_methods": ["bilingual_exact"],
                "draft_warnings": [],
                "catalogue_current": item_status == "approved",
                "presented_as_approved_scale": False,
                "share": {"available": False, "value": None},
            }

        model = build_compare(
            bucket_id="legal",
            statement_date="2026-06-30",
            item_key="user:總計",
            display="amount",
            entities=[
                _entity("none", absent_name),
                _entity("income", income_name),
                _entity("sheet", sheet_name),
                _entity("valued", value_name),
            ],
            statements=[income_only, sheet, sheet_income, valued],
            rows_by_statement={
                income_only["statement_id"]: [row(income_only, "user:總計", income_amount)],
                sheet["statement_id"]: [row(sheet, "user:現金", balance_other)],
                sheet_income["statement_id"]: [row(sheet_income, "user:總計", leaked_from_income)],
                valued["statement_id"]: [row(valued, "user:總計", balance_hit)],
            },
        )
        by_name = {item["canonical_zh"]: item for item in model["entities"]}
        missing = by_name[absent_name]
        self.assertEqual(missing["kind"], "gap")
        self.assertEqual(missing["rows"], [])
        self.assertEqual(missing["adopted_statement_types"], [])
        income_only_cell = by_name[income_name]
        self.assertEqual(income_only_cell["kind"], "no_balance_sheet")
        self.assertEqual(income_only_cell["adopted_statement_types"], ["income_statement"])
        self.assertNotIn("此期沒有此候選", income_only_cell["note"])
        self.assertEqual(income_only_cell["rows"], [])
        self.assertNotIn(str(income_amount), str(income_only_cell))
        absent = by_name[sheet_name]
        self.assertEqual(absent["kind"], "absent")
        self.assertIn("balance_sheet", absent["adopted_statement_types"])
        self.assertIn("income_statement", absent["adopted_statement_types"])
        self.assertIn("此期沒有此候選", absent["note"])
        self.assertEqual(absent["rows"], [])
        self.assertNotIn(str(leaked_from_income), str(absent))
        self.assertNotIn(str(balance_other), str(absent))
        present = by_name[value_name]
        self.assertEqual(present["kind"], "values")
        self.assertEqual([item["debit"]["minor"] for item in present["rows"]], [balance_hit])
        self.assertIsNone(present["entity_total"])
        self.assertIsNone(model["column_total"])
        html = TEMPLATES.get_template("compare.html").render({
            "not_comparable": "未可安全比較",
            "window": list(COMPARISON_DATES),
            "buckets": [],
            "page_warnings": [],
            "mapping_statuses": (),
            "aggregate": {"reason": "不加總"},
            "model": {
                "bucket": "legal",
                "bucket_rule": "不可相加",
                "statement_date": "2026-06-30",
                "statement_type": "balance_sheet",
                "display": "amount",
                "needs_item": False,
                "scale_note": "",
                "draft_warnings": [],
                "available_items": [],
                "share": {"reason": "未計算"},
                "item_key": "user:總計",
                "entities": [income_only_cell],
            },
        })
        self.assertIn("沒有 balance_sheet", html)
        self.assertNotIn("此期沒有此候選", html)


class HostAndDatabaseTests(unittest.TestCase):
    def test_only_loopback_is_accepted(self) -> None:
        self.assertEqual(resolve_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(resolve_host("localhost"), "127.0.0.1")
        self.assertEqual(resolve_host("::1"), "127.0.0.1")
        for host in ("0.0.0.0", "192.168.1.8", ""):
            with self.subTest(host=host):
                with self.assertRaises(SystemExit):
                    resolve_host(host)

    def test_host_header_allows_loopback_names_and_ports_only(self) -> None:
        for value in (
            "127.0.0.1",
            "127.0.0.1:8010",
            "127.0.0.1:1",
            "127.0.0.1:65535",
            "localhost",
            "localhost:80",
            "LOCALHOST:8000",
            "  localhost:8010  ",
        ):
            with self.subTest(value=value):
                self.assertTrue(host_header_allowed(value))
        for value in (
            None,
            "",
            "evil.example",
            "evil.example:8010",
            "127.0.0.1.evil.example",
            "localhost.evil.example",
            "0.0.0.0",
            "0.0.0.0:8010",
            "[::1]",
            "[::1]:8010",
            "127.0.0.1:8010:1",
            "127.0.0.1:",
            "localhost:0",
            "localhost:65536",
            "localhost:abc",
            "127.0.0.1:80, evil.example",
            "user@127.0.0.1",
        ):
            with self.subTest(value=value):
                self.assertFalse(host_header_allowed(value))

    def test_http_rejects_rebound_host_before_reading_the_database(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "missing.sqlite"
        client = TestClient(create_app(missing), base_url="http://127.0.0.1")
        rejected = client.get("/api/coverage", headers={"Host": "evil.example"})
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("Host", rejected.json()["error"])
        self.assertNotIn("canonical_zh", rejected.text)
        allowed = client.get("/health", headers={"Host": "127.0.0.1"})
        self.assertEqual(allowed.status_code, 400)
        self.assertIn("找不到資料庫", allowed.json()["error"])

    def test_v5_path_is_refused_before_it_is_read_as_analysis(self) -> None:
        self.assertTrue(is_v5_analysis_path(Path("archive/database/macau_balancete.sqlite")))
        self.assertFalse(is_v5_analysis_path(Path("data/macau_balancete_v3.sqlite")))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "archive" / "database" / "macau_balancete.sqlite"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"this is not a database")
            with self.assertRaises(DashboardDataError) as caught:
                connect_readonly(target)
            self.assertIn("V5", str(caught.exception))

    def test_connection_is_query_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "my db" / "macau_balancete_v3.sqlite"
            path.parent.mkdir()
            build_fixture(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            connection = connect_readonly(path)
            try:
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("UPDATE statements SET notes='x'")
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE should_not_exist(x)")
            finally:
                connection.close()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


class DashboardFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tempdir.name) / "fixture.sqlite"
        cls.meta = build_fixture(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path), base_url="http://127.0.0.1")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def test_sql_against_real_views_matches_independent_usable_count(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            placeholders = ",".join("?" for _ in ADOPTED_NAMES)
            dates = ",".join("?" for _ in COMPARISON_DATES)
            usable = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM statements s
                JOIN reporting_entities e ON e.entity_id = s.reporting_entity_id
                WHERE e.canonical_zh IN ({placeholders})
                  AND s.statement_date IN ({dates})
                  AND s.statement_status = 'active'
                  AND s.adopted_statement_extraction_id IS NOT NULL
                """,
                tuple(ADOPTED_NAMES) + COMPARISON_DATES,
            ).fetchone()[0]
            covered = connection.execute(
                f"""
                SELECT e.canonical_zh, s.statement_date, COUNT(*)
                FROM statements s
                JOIN reporting_entities e ON e.entity_id = s.reporting_entity_id
                WHERE e.canonical_zh IN ({placeholders})
                  AND s.statement_date IN ({dates})
                  AND s.statement_status = 'active'
                  AND s.adopted_statement_extraction_id IS NOT NULL
                GROUP BY e.canonical_zh, s.statement_date
                """,
                tuple(ADOPTED_NAMES) + COMPARISON_DATES,
            ).fetchall()
        finally:
            connection.close()
        body = self.client.get("/api/coverage").json()
        self.assertEqual(body["usable_statement_count"], usable)
        self.assertEqual(body["gap_count"], len(ADOPTED_NAMES) * len(COMPARISON_DATES) - len(covered))
        self.assertEqual(body["window"], list(COMPARISON_DATES))
        self.assertFalse(body["mapping"]["safely_cross_comparable"])
        self.assertFalse(body["aggregate"]["computed"])
        self.assertIsNone(body["aggregate"]["legal_bank_sum"])
        self.assertIsNone(body["aggregate"]["all_fifteen_sum"])
        self.assertGreater(usable, 0)

    def test_coverage_groups_and_gaps_come_from_the_database(self) -> None:
        body = self.client.get("/api/coverage").json()
        buckets = {bucket["id"]: bucket for bucket in body["buckets"]}
        self.assertEqual(
            [item["canonical_zh"] for item in buckets["legal"]["entities"]],
            list(LEGAL_BANKS),
        )
        self.assertEqual(
            [item["canonical_zh"] for item in buckets["related"]["entities"]],
            list(RELATED_ENTITIES),
        )
        self.assertEqual(
            [item["canonical_zh"] for item in buckets["foreign_branch"]["entities"]],
            list(FOREIGN_BRANCHES),
        )
        self.assertTrue(all(bucket["summed"] is False for bucket in body["buckets"]))
        legal_names = {item["canonical_zh"] for item in buckets["legal"]["entities"]}
        related_names = {item["canonical_zh"] for item in buckets["related"]["entities"]}
        self.assertIn("中國工商銀行(澳門)股份有限公司", legal_names)
        self.assertIn("中國工商銀行(澳門)股份有限公司(集團)", related_names)
        self.assertNotIn("中國工商銀行(澳門)股份有限公司(集團)", legal_names)
        self.assertNotIn("中國工商銀行股份有限公司澳門分行", legal_names)

        def cell(bucket: str, name: str, date: str) -> dict:
            entity = next(item for item in buckets[bucket]["entities"] if item["canonical_zh"] == name)
            return entity["periods"][date]

        boc_gap = cell("legal", "中國銀行(澳門)股份有限公司", "2026-03-31")
        self.assertEqual(boc_gap["kind"], "gap")
        self.assertIn("reviewed", boc_gap["reason"])
        self.assertIsNone(boc_gap["amounts"])
        boc_open = cell("legal", "中國銀行(澳門)股份有限公司", "2026-06-30")
        self.assertEqual(boc_open["kind"], "usable")
        self.assertEqual(boc_open["statements"][0]["identity_status"], "proposed")
        self.assertEqual(boc_open["statements"][0]["metadata_status"], "proposed")
        commercial = cell("legal", "澳門商業銀行股份有限公司", "2026-09-30")
        self.assertEqual(commercial["kind"], "gap")
        self.assertIn("reviewed", commercial["reason"])
        branch_gap = cell("related", "中國銀行股份有限公司澳門分行", "2026-06-30")
        self.assertEqual(branch_gap["kind"], "gap")
        self.assertNotIn("reviewed", branch_gap["reason"])
        self.assertEqual(cell("related", "中國銀行股份有限公司澳門分行", "2026-09-30")["reason"], "沒有報表")
        self.assertEqual(cell("foreign_branch", "恒生銀行有限公司澳門分行", "2026-03-31")["reason"], "沒有報表")
        self.assertEqual(cell("foreign_branch", "恒生銀行有限公司澳門分行", "2026-06-30")["kind"], "usable")
        group = next(item for item in buckets["related"]["entities"] if item["canonical_zh"].endswith("(集團)"))
        self.assertEqual(group["entity_type"], "bank")
        self.assertIsNone(group["parent_entity_id"])
        libo = next(item for item in buckets["legal"]["entities"] if item["canonical_zh"] == "立橋銀行股份有限公司")
        self.assertEqual(libo["registry_status"], "proposed")
        outside_dates = [item["statement_date"] for item in body["outside_window"]]
        self.assertIn("2025-12-31", outside_dates)
        self.assertTrue(all(item["amounts"] is None for item in body["outside_window"]))
        encoded = str(body)
        for poison in POISONS:
            self.assertNotIn(str(poison), encoded)

    def test_compare_does_not_sum_rescale_or_mix(self) -> None:
        blocked = self.client.get("/api/compare", params={"bucket": "all", "statement_date": "2026-06-30"})
        self.assertEqual(blocked.status_code, 400)
        self.assertIsNone(blocked.json()["amounts"])
        outside = self.client.get("/api/compare", params={"bucket": "legal", "statement_date": "2025-12-31"})
        self.assertEqual(outside.status_code, 400)
        catalogue = self.client.get(
            "/api/compare",
            params={"bucket": "legal", "statement_date": "2026-06-30"},
        ).json()
        self.assertTrue(catalogue["needs_item"])
        self.assertNotIn("111111", str(catalogue["available_items"]))
        keys = [item["item_key"] for item in catalogue["available_items"]]
        self.assertEqual(keys, sorted(keys))
        body = self.client.get(
            "/api/compare",
            params={
                "bucket": "legal",
                "statement_date": "2026-06-30",
                "item_key": "user:總計",
                "display": "amount",
                "entity_id": self.meta["outsider_entity_id"],
            },
        ).json()
        self.assertEqual([item["canonical_zh"] for item in body["entities"]], list(LEGAL_BANKS))
        self.assertIsNone(body["column_total"])
        self.assertIsNone(body["legal_bank_sum"])
        self.assertIsNone(body["ranking"])
        self.assertFalse(body["presented_as_approved_scale"])
        self.assertTrue(body["scale_mismatch"])
        minors = []
        for entity in body["entities"]:
            self.assertIsNone(entity["entity_total"])
            for row in entity["rows"]:
                minors.append(row["debit"]["minor"])
                self.assertEqual(row["statement_type"], "balance_sheet")
                self.assertFalse(row["presented_as_approved_scale"])
                self.assertIsNone(row["share"]["value"])
        self.assertEqual(sorted(minors), sorted(TOTALS.values()))
        self.assertNotIn(sum(TOTALS.values()), minors)
        bnu = next(item for item in body["entities"] if item["canonical_zh"] == "大西洋銀行股份有限公司")
        self.assertEqual(bnu["rows"][0]["debit"]["minor"], 50)
        self.assertEqual(bnu["rows"][0]["unit_scale"], 1000)
        self.assertIsNone(bnu["rows"][0]["debit"]["reading"])
        self.assertEqual(bnu["rows"][0]["mapping_status"], "proposed")
        self.assertTrue(any("不是總資產" in text for text in bnu["rows"][0]["draft_warnings"]))
        encoded = str(body)
        for poison in POISONS:
            self.assertNotIn(str(poison), encoded)
        share = self.client.get(
            "/api/compare",
            params={
                "bucket": "legal",
                "statement_date": "2026-06-30",
                "item_key": "user:總計",
                "display": "share",
            },
        ).json()
        self.assertEqual(share["display"], "share")
        self.assertIsNone(share["share"]["value"])
        self.assertNotIn("percent", share)

    def test_rows_keep_mapping_status_and_drop_hidden_amounts(self) -> None:
        libo = self.client.get(f"/api/statements/{self.meta['libo_statement_id']}").json()
        self.assertTrue(libo["usable"])
        statuses = [row["mapping_status"] for row in libo["rows"]]
        self.assertEqual(sorted(statuses), ["approved", "proposed", "unmapped"])
        approved = next(row for row in libo["rows"] if row["mapping_status"] == "approved")
        self.assertTrue(approved["also_proposed"])
        self.assertEqual(approved["label_raw"], "現金")
        self.assertIn("manual", approved["match_methods"])
        proposed = next(row for row in libo["rows"] if row["mapping_status"] == "proposed")
        self.assertEqual(proposed["candidates"][0]["item_key"], "user:總計")
        self.assertIn("bilingual_exact", proposed["match_methods"])
        self.assertNotIn(str(POISON_EXCLUDED), str(libo))
        inserted = self.client.get(f"/api/statements/{self.meta['insert_statement_id']}").json()
        insert_rows = [row for row in inserted["rows"] if row["mapping_status"] == "insert-direct"]
        self.assertEqual(len(insert_rows), 1)
        self.assertEqual(insert_rows[0]["debit"]["minor"], 50)
        self.assertTrue(insert_rows[0]["insert_without_ocr_label"])
        self.assertEqual(insert_rows[0]["candidates"][0]["item_key"], "user:現金")
        self.assertEqual(insert_rows[0]["candidates"][0]["item_status"], "approved")
        self.assertIsNone(insert_rows[0]["candidates"][0]["merged_into_standard_item_id"])
        self.assertTrue(insert_rows[0]["catalogue_current"])
        approved = next(row for row in libo["rows"] if row["mapping_status"] == "approved")
        self.assertEqual(approved["candidates"][0]["item_status"], "approved")
        self.assertTrue(approved["catalogue_current"])
        gap = self.client.get(f"/api/statements/{self.meta['boc_gap_statement_id']}").json()
        self.assertFalse(gap["usable"])
        self.assertEqual(gap["rows"], [])
        self.assertNotIn(str(POISON_NOT_ADOPTED), str(gap))
        outside = self.client.get(f"/api/statements/{self.meta['outside_statement_id']}").json()
        self.assertFalse(outside["usable"])
        self.assertEqual(outside["rows"], [])
        self.assertNotIn(str(POISON_OUTSIDE), str(outside))
        missing = self.client.get(f"/api/statements/{self.meta['outsider_statement_id']}")
        self.assertEqual(missing.status_code, 404)
        outsider = self.client.get(f"/api/entities/{self.meta['outsider_entity_id']}")
        self.assertEqual(outsider.status_code, 404)

    def test_retired_and_merged_items_stay_visible(self) -> None:
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        body = self.client.get(f"/api/statements/{self.meta['lifecycle_statement_id']}").json()
        retired = next(
            row for row in body["rows"]
            if row["candidates"] and row["candidates"][0]["item_key"] == "user:已退役"
        )
        self.assertEqual(retired["mapping_status"], "approved")
        self.assertEqual(retired["candidates"][0]["item_status"], "retired")
        self.assertIsNone(retired["candidates"][0]["merged_into_standard_item_id"])
        self.assertFalse(retired["catalogue_current"])
        self.assertFalse(retired["presented_as_approved_scale"])
        merged = next(
            row for row in body["rows"]
            if row["mapping_status"] == "insert-direct"
        )
        self.assertEqual(merged["candidates"][0]["item_key"], "user:已合併")
        self.assertEqual(merged["candidates"][0]["item_status"], "merged")
        self.assertEqual(merged["candidates"][0]["merged_into_standard_item_id"], "si-cash")
        self.assertFalse(merged["catalogue_current"])
        self.assertEqual(merged["debit"]["minor"], 430043)
        page = self.client.get(f"/statements/{self.meta['lifecycle_statement_id']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("user:已退役", page.text)
        self.assertIn("retired", page.text)
        self.assertIn("user:已合併", page.text)
        self.assertIn("merged", page.text)
        self.assertIn("si-cash", page.text)
        self.assertIn("科目不是現行有效項", page.text)
        compared = self.client.get(
            "/api/compare",
            params={
                "bucket": "legal",
                "statement_date": "2026-09-30",
                "item_key": "user:已退役",
            },
        ).json()
        taifung = next(item for item in compared["entities"] if item["canonical_zh"] == "大豐銀行股份有限公司")
        self.assertEqual(taifung["kind"], "values")
        self.assertEqual(taifung["rows"][0]["candidates"][0]["item_status"], "retired")
        self.assertFalse(taifung["rows"][0]["catalogue_current"])
        traced: list[str] = []
        connection = connect_readonly(self.db_path)
        try:
            connection.set_trace_callback(traced.append)
            queries.fetch_mappings(connection, [retired["statement_row_id"]])
            queries.fetch_inserts(connection, [self.meta["lifecycle_statement_id"]])
        finally:
            connection.close()
        self.assertTrue(traced)
        for sql in traced:
            self.assertTrue(
                sql.lstrip().upper().startswith("SELECT"),
                sql,
            )
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before)

    def test_same_bank_view_does_not_invent_gap_values_or_mix_statement_types(self) -> None:
        boc = self.client.get(f"/api/entities/{self.meta['boc_entity_id']}?date=2026-03-31").json()
        self.assertEqual(boc["periods"]["2026-03-31"]["kind"], "gap")
        self.assertEqual(boc["selected_rows_by_statement"], [])
        self.assertNotIn(str(POISON_NOT_ADOPTED), str(boc))
        for item in boc["pivot"]:
            self.assertEqual(item["cells"]["2026-03-31"]["kind"], "gap")
            self.assertIsNone(item["cells"]["2026-03-31"]["total"])
            self.assertIsNone(item["total"])
        ocbc = self.client.get(
            "/api/entities/"
            + self.meta["entities"]["華僑銀行(澳門)股份有限公司"]
            + "?date=2026-06-30"
        ).json()
        types = [block["statement"]["statement_type"] for block in ocbc["selected_rows_by_statement"]]
        self.assertIn("balance_sheet", types)
        self.assertIn("income_statement", types)
        for item in ocbc["pivot"]:
            for row in item["cells"]["2026-06-30"]["rows"]:
                self.assertNotEqual(row["debit"]["minor"], POISON_OTHER_TYPE)
        income_rows = [
            row
            for block in ocbc["selected_rows_by_statement"]
            if block["statement"]["statement_type"] == "income_statement"
            for row in block["rows"]
        ]
        self.assertEqual(income_rows[0]["debit"]["minor"], POISON_OTHER_TYPE)

    def test_html_shows_status_and_does_not_write(self) -> None:
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        text = page.text
        for name in ADOPTED_NAMES:
            self.assertIn(name, text)
        self.assertNotIn("澳門華人銀行股份有限公司", text)
        self.assertIn("insert-direct", text)
        self.assertIn("proposed", text)
        self.assertIn("approved", text)
        self.assertIn("unmapped", text)
        self.assertIn("缺口", text)
        self.assertIn("不是總資產", text)
        for poison in POISONS:
            self.assertNotIn(str(poison), text)
        compare = self.client.get(
            "/compare",
            params={"bucket": "legal", "statement_date": "2026-06-30", "item_key": "user:總計"},
        )
        self.assertEqual(compare.status_code, 200)
        self.assertIn("沒有合計欄", compare.text)
        self.assertIn("proposed，不可當成已核准規模或排名。", compare.text)
        self.assertNotIn(str(sum(TOTALS.values())), compare.text)
        for poison in POISONS:
            self.assertNotIn(str(poison), compare.text)
        share = self.client.get(
            "/compare",
            params={
                "bucket": "legal",
                "statement_date": "2026-06-30",
                "item_key": "user:總計",
                "display": "share",
            },
        )
        self.assertIn("未計算", share.text)
        self.assertNotIn("%", share.text)
        statement = self.client.get(f"/statements/{self.meta['libo_statement_id']}")
        self.assertIn("Workbench 已標記，不是財務接受", statement.text)
        self.assertIn("insert-direct", self.client.get(f"/statements/{self.meta['insert_statement_id']}").text)
        statement_url = f"/api/statements/{self.meta['libo_statement_id']}"
        self.assertEqual(self.client.post(statement_url).status_code, 405)
        self.assertEqual(self.client.post(statement_url + "/adopt").status_code, 404)
        aggregate = self.client.get("/api/aggregate")
        self.assertFalse(aggregate.json()["computed"])
        self.assertIsNone(aggregate.json()["amounts"])
        for host in ("127.0.0.1", "127.0.0.1:8010", "localhost", "localhost:8010"):
            response = self.client.get("/health", headers={"Host": host})
            self.assertEqual(response.status_code, 200, host)
        for host in ("evil.example", "evil.example:8010", "127.0.0.1.evil.example", "0.0.0.0:8010"):
            response = self.client.get("/api/coverage", headers={"Host": host})
            self.assertEqual(response.status_code, 400, host)
            self.assertNotIn("中國銀行(澳門)股份有限公司", response.text)
        health = self.client.get("/health").json()
        self.assertTrue(health["read_only"])
        self.assertFalse(health["authentication"])
        self.assertEqual(health["host_policy"], "127.0.0.1")
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before)
        self.assertFalse(Path(str(self.db_path) + "-wal").exists())


if __name__ == "__main__":
    unittest.main()
