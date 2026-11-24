"""Schema-compatible V3 fixture for dashboard tests.

Built with the checked-in V3 DDL. It is synthetic and is not a snapshot of
Tony's database.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.build_v3_db import apply_schema

from phase1_dashboard.scope import (
    COMPARISON_DATES,
    FOREIGN_BRANCHES,
    LEGAL_BANKS,
    RELATED_ENTITIES,
)

NOW = "2026-11-24T09:00:00Z"
SHA = "ab" * 32

POISON_NOT_ADOPTED = 999999019
POISON_OUTSIDE = 777777019
POISON_OUTSIDER = 888888019
POISON_EXCLUDED = 424242019
POISON_OTHER_TYPE = 313131019
POISON_COMMERCIAL_GAP = 919191919

TOTALS = {
    "中國銀行(澳門)股份有限公司": 1001,
    "大豐銀行股份有限公司": 222222,
    "大西洋銀行股份有限公司": 50,
    "立橋銀行股份有限公司": 111111,
}


class FixtureBuilder:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.page_no = 0
        self.seq = 0
        self.meta: dict[str, object] = {
            "poisons": {
                "not_adopted": POISON_NOT_ADOPTED,
                "outside": POISON_OUTSIDE,
                "outsider": POISON_OUTSIDER,
                "excluded": POISON_EXCLUDED,
                "other_type": POISON_OTHER_TYPE,
                "commercial_gap": POISON_COMMERCIAL_GAP,
            },
            "totals": dict(TOTALS),
        }

    def _id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}{self.seq:04d}"

    def _fp(self) -> str:
        self.seq += 1
        return f"{self.seq:064x}"

    def base(self) -> None:
        c = self.connection
        c.execute(
            "INSERT INTO gazette_issues(gazette_issue_id, issue_key, publication_date, discovered_at) "
            "VALUES(?,?,?,?)",
            ("G1", "2026-fixture", "2026-11-18", NOW),
        )
        c.execute(
            "INSERT INTO source_documents("
            "source_document_id, gazette_issue_id, document_key, storage_uri, sha256, page_count, acquired_at"
            ") VALUES(?,?,?,?,?,?,?)",
            ("D1", "G1", "doc:fixture", "file:///tmp/fixture.pdf", SHA, 200, NOW),
        )
        c.execute(
            "INSERT INTO extraction_runs(run_id, source_document_id, run_status) VALUES(?,?,?)",
            ("R1", "D1", "completed"),
        )
        items = (
            ("si-total", "user:總計", "總計", "TOTAIS"),
            ("si-cash", "user:現金", "現金", "CAIXA"),
            ("si-other", "user:其他", "其他", "OUTROS"),
        )
        for item_id, key, zh, pt in items:
            c.execute(
                "INSERT INTO standard_items("
                "standard_item_id, item_key, canonical_zh, canonical_zh_status, "
                "canonical_pt, canonical_pt_status, item_status, created_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (item_id, key, zh, "approved", pt, "approved", "approved", NOW),
            )
        c.execute(
            "INSERT INTO standard_items("
            "standard_item_id, item_key, canonical_zh, canonical_zh_status, "
            "canonical_pt, canonical_pt_status, item_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            ("si-retired", "user:已退役", "已退役", "approved", "RETIRADO", "approved", "retired", NOW),
        )
        c.execute(
            "INSERT INTO standard_items("
            "standard_item_id, item_key, canonical_zh, canonical_zh_status, "
            "canonical_pt, canonical_pt_status, item_status, "
            "merged_into_standard_item_id, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "si-merged", "user:已合併", "已合併", "approved", "FUNDIDO", "approved",
                "merged", "si-cash", NOW,
            ),
        )

    def add_entity(
        self,
        *,
        canonical_zh: str,
        entity_type: str,
        registry_status: str = "approved",
    ) -> str:
        entity_id = self._id("e")
        self.connection.execute(
            "INSERT INTO reporting_entities("
            "entity_id, entity_key, entity_type, canonical_zh, registry_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            (entity_id, f"bank:{entity_id}", entity_type, canonical_zh, registry_status, NOW),
        )
        return entity_id

    def add_statement(
        self,
        entity_id: str,
        *,
        statement_date: str,
        adopted: bool,
        statement_type: str = "balance_sheet",
        reporting_scope: str = "bank",
        currency: str = "MOP",
        unit_scale: int | None = 1,
        decimal_places: int | None = 2,
        review_status: str | None = None,
        entity_name_raw: str | None = None,
    ) -> tuple[str, str, str, str]:
        self.page_no += 1
        statement_id = self._id("s")
        extraction_id = self._id("se")
        page_id = self._id("p")
        page_extraction_id = self._id("pe")
        c = self.connection
        c.execute(
            "INSERT INTO document_pages("
            "document_page_id, source_document_id, pdf_page, page_role, "
            "classification_status, classification_source"
            ") VALUES(?,?,?,?,?,?)",
            (page_id, "D1", self.page_no, "financial_statement", "approved", "human"),
        )
        c.execute(
            "INSERT INTO statements("
            "statement_id, source_document_id, reporting_entity_id, entity_name_raw, "
            "statement_date, statement_date_source, statement_type, statement_type_source, "
            "reporting_scope, currency, currency_source, unit_scale, unit_scale_source, "
            "amount_decimal_places, identity_key, identity_status, metadata_status, "
            "statement_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                statement_id, "D1", entity_id, entity_name_raw,
                statement_date, "manual", statement_type, "manual",
                reporting_scope, currency, "manual", unit_scale, "manual",
                decimal_places, f"identity:{statement_id}", "proposed", "proposed",
                "proposed", NOW,
            ),
        )
        c.execute(
            "INSERT INTO statement_extractions("
            "statement_extraction_id, statement_id, run_id, source_document_id, "
            "extraction_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            (extraction_id, statement_id, "R1", "D1", "completed", NOW),
        )
        c.execute(
            "INSERT INTO statement_pages("
            "statement_id, source_document_id, document_page_id, page_order, is_primary, created_at"
            ") VALUES(?,?,?,?,?,?)",
            (statement_id, "D1", page_id, 1, 1, NOW),
        )
        c.execute(
            "INSERT INTO page_extractions("
            "page_extraction_id, run_id, document_page_id, source_document_id, ocr_status, created_at"
            ") VALUES(?,?,?,?,?,?)",
            (page_extraction_id, "R1", page_id, "D1", "completed", NOW),
        )
        if review_status:
            c.execute(
                "INSERT INTO statement_reviews(statement_id, review_status, updated_at) VALUES(?,?,?)",
                (statement_id, review_status, NOW),
            )
        if adopted:
            c.execute(
                "UPDATE statements SET adopted_statement_extraction_id=?, statement_status='active' "
                "WHERE statement_id=?",
                (extraction_id, statement_id),
            )
        return statement_id, extraction_id, page_id, page_extraction_id

    def add_ocr_row(
        self,
        *,
        statement_id: str,
        extraction_id: str,
        page_id: str,
        page_extraction_id: str,
        order: int,
        label: str,
        label_raw: str | None = None,
        debit_minor: int | None = None,
        credit_minor: int | None = None,
        row_type: str = "item",
    ) -> str:
        row_id = self._id("sr")
        extracted_id = self._id("er")
        debit_state = "amount" if debit_minor is not None else "blank"
        credit_state = "amount" if credit_minor is not None else "blank"
        self.connection.execute(
            "INSERT INTO extracted_rows("
            "extracted_row_id, page_extraction_id, document_page_id, run_id, row_disposition, "
            "statement_id, statement_extraction_id, page_row_order, statement_order, "
            "label_raw, label_normalized, label_language, "
            "debit_raw, debit_parsed_minor, debit_value_state, "
            "credit_raw, credit_parsed_minor, credit_value_state, "
            "row_type_proposed, parent_context_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                extracted_id, page_extraction_id, page_id, "R1", "included",
                statement_id, extraction_id, order, order,
                label_raw if label_raw is not None else label, label, "zh-Hant",
                None if debit_minor is None else str(debit_minor), debit_minor, debit_state,
                None if credit_minor is None else str(credit_minor), credit_minor, credit_state,
                row_type, "root", NOW,
            ),
        )
        self.connection.execute(
            "INSERT INTO statement_rows("
            "statement_row_id, statement_id, statement_extraction_id, extracted_row_id, "
            "statement_order, row_type, parent_context_status, label_normalized, label_language, "
            "debit_minor, debit_value_state, credit_minor, credit_value_state, "
            "row_fingerprint, fingerprint_version, carry_forward_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row_id, statement_id, extraction_id, extracted_id,
                order, row_type, "root", label, "zh-Hant",
                debit_minor, debit_state, credit_minor, credit_state,
                self._fp(), 1, "none", NOW,
            ),
        )
        return row_id

    def add_mapping(
        self,
        row_id: str,
        *,
        item_id: str,
        status: str,
        method: str | None,
    ) -> None:
        reviewed_by = "fixture" if status == "approved" else None
        reviewed_at = NOW if status == "approved" else None
        self.connection.execute(
            "INSERT INTO statement_row_mappings("
            "mapping_id, statement_row_id, standard_item_id, mapping_status, match_method, "
            "reviewed_by, reviewed_at, created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (self._id("m"), row_id, item_id, status, method, reviewed_by, reviewed_at, NOW),
        )

    def add_exclude(self, statement_id: str, row_id: str) -> None:
        self.connection.execute(
            "INSERT INTO statement_row_adjustments("
            "adjustment_id, statement_id, adjustment_type, target_statement_row_id, "
            "reason, adjusted_by, adjusted_at, adjustment_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (self._id("adj"), statement_id, "exclude", row_id, "fixture exclude", "fixture", NOW, "active", NOW),
        )

    def add_insert(
        self,
        statement_id: str,
        *,
        order: float,
        label: str,
        debit_minor: int,
        item_id: str,
    ) -> str:
        adjustment_id = self._id("adj")
        self.connection.execute(
            "INSERT INTO statement_row_adjustments("
            "adjustment_id, statement_id, adjustment_type, display_order, row_type, "
            "label_normalized, label_language, debit_minor, debit_value_state, "
            "credit_value_state, standard_item_id, reason, adjusted_by, adjusted_at, "
            "adjustment_status, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                adjustment_id, statement_id, "insert", order, "item",
                label, "zh-Hant", debit_minor, "amount",
                "blank", item_id, "fixture insert", "fixture", NOW,
                "active", NOW,
            ),
        )
        return adjustment_id


def _scope_for(name: str) -> tuple[str, str]:
    if name in LEGAL_BANKS:
        return "bank", "bank"
    if name == "中國工商銀行(澳門)股份有限公司(集團)":
        return "bank", "group"
    return "branch", "branch"


def build_fixture(path: Path) -> dict[str, object]:
    path = Path(path)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        apply_schema(connection)
        builder = FixtureBuilder(connection)
        builder.base()
        entities: dict[str, str] = {}
        for name in (*LEGAL_BANKS, *RELATED_ENTITIES, *FOREIGN_BRANCHES):
            entity_type, _scope = _scope_for(name)
            registry = "proposed" if name == "立橋銀行股份有限公司" else "approved"
            entities[name] = builder.add_entity(
                canonical_zh=name,
                entity_type=entity_type,
                registry_status=registry,
            )
        outsider = "澳門華人銀行股份有限公司"
        entities[outsider] = builder.add_entity(canonical_zh=outsider, entity_type="bank")

        gaps = {
            ("中國銀行(澳門)股份有限公司", "2026-03-31"): ("reviewed", POISON_NOT_ADOPTED),
            ("澳門商業銀行股份有限公司", "2026-09-30"): ("reviewed", POISON_COMMERCIAL_GAP),
            ("中國銀行股份有限公司澳門分行", "2026-06-30"): ("pending", None),
        }
        skipped = {
            ("恒生銀行有限公司澳門分行", "2026-03-31"),
            ("中國銀行股份有限公司澳門分行", "2026-09-30"),
        }
        for name in (*LEGAL_BANKS, *RELATED_ENTITIES, *FOREIGN_BRANCHES):
            entity_type, scope = _scope_for(name)
            for date in COMPARISON_DATES:
                key = (name, date)
                if key in skipped:
                    continue
                unit_scale: int | None = 1000 if name == "大西洋銀行股份有限公司" else 1
                decimal_places = None if name == "大西洋銀行股份有限公司" else 2
                if key in gaps:
                    review_status, poison = gaps[key]
                    ids = builder.add_statement(
                        entities[name],
                        statement_date=date,
                        adopted=False,
                        reporting_scope=scope,
                        currency="MOP",
                        unit_scale=unit_scale,
                        decimal_places=decimal_places,
                        review_status=review_status,
                        entity_name_raw=name,
                    )
                    if poison is not None:
                        row_id = builder.add_ocr_row(
                            statement_id=ids[0],
                            extraction_id=ids[1],
                            page_id=ids[2],
                            page_extraction_id=ids[3],
                            order=1,
                            label="不應出現",
                            debit_minor=poison,
                            credit_minor=poison,
                        )
                        builder.add_mapping(row_id, item_id="si-total", status="proposed", method="bilingual_exact")
                    if name == "中國銀行(澳門)股份有限公司":
                        builder.meta["boc_gap_statement_id"] = ids[0]
                    continue
                ids = builder.add_statement(
                    entities[name],
                    statement_date=date,
                    adopted=True,
                    reporting_scope=scope,
                    currency="MOP",
                    unit_scale=unit_scale,
                    decimal_places=decimal_places,
                    entity_name_raw=name,
                )
                _add_usable_rows(builder, name, date, ids)
        outside = builder.add_statement(
            entities["立橋銀行股份有限公司"],
            statement_date="2025-12-31",
            adopted=True,
            entity_name_raw="立橋銀行股份有限公司",
        )
        outside_row = builder.add_ocr_row(
            statement_id=outside[0], extraction_id=outside[1], page_id=outside[2],
            page_extraction_id=outside[3], order=1, label="窗以外",
            debit_minor=POISON_OUTSIDE, credit_minor=POISON_OUTSIDE,
        )
        builder.add_mapping(outside_row, item_id="si-total", status="proposed", method="bilingual_exact")
        builder.meta["outside_statement_id"] = outside[0]

        other = builder.add_statement(
            entities[outsider],
            statement_date="2026-06-30",
            adopted=True,
            entity_name_raw=outsider,
        )
        other_row = builder.add_ocr_row(
            statement_id=other[0], extraction_id=other[1], page_id=other[2],
            page_extraction_id=other[3], order=1, label="名單以外",
            debit_minor=POISON_OUTSIDER, credit_minor=POISON_OUTSIDER,
        )
        builder.add_mapping(other_row, item_id="si-total", status="approved", method="manual")
        builder.meta["outsider_entity_id"] = entities[outsider]
        builder.meta["outsider_statement_id"] = other[0]

        income = builder.add_statement(
            entities["華僑銀行(澳門)股份有限公司"],
            statement_date="2026-06-30",
            adopted=True,
            statement_type="income_statement",
            entity_name_raw="華僑銀行(澳門)股份有限公司",
        )
        income_row = builder.add_ocr_row(
            statement_id=income[0], extraction_id=income[1], page_id=income[2],
            page_extraction_id=income[3], order=1, label="總計",
            debit_minor=POISON_OTHER_TYPE, credit_minor=POISON_OTHER_TYPE,
        )
        builder.add_mapping(income_row, item_id="si-total", status="proposed", method="bilingual_exact")
        builder.meta["income_statement_id"] = income[0]

        builder.meta["entities"] = entities
        builder.meta["libo_entity_id"] = entities["立橋銀行股份有限公司"]
        builder.meta["boc_entity_id"] = entities["中國銀行(澳門)股份有限公司"]
        builder.meta["bnu_entity_id"] = entities["大西洋銀行股份有限公司"]
        connection.commit()
        return builder.meta
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _add_usable_rows(builder: FixtureBuilder, name: str, date: str, ids: tuple[str, str, str, str]) -> None:
    statement_id, extraction_id, page_id, page_extraction_id = ids
    common = {
        "statement_id": statement_id,
        "extraction_id": extraction_id,
        "page_id": page_id,
        "page_extraction_id": page_extraction_id,
    }
    if name == "立橋銀行股份有限公司" and date == "2026-06-30":
        cash = builder.add_ocr_row(
            **common, order=1, label="現金", label_raw="現金", debit_minor=1000,
        )
        builder.add_mapping(cash, item_id="si-cash", status="approved", method="manual")
        builder.add_mapping(cash, item_id="si-cash", status="proposed", method="alias_exact")
        total = builder.add_ocr_row(
            **common, order=2, label="總計", label_raw="總額",
            debit_minor=TOTALS[name], credit_minor=TOTALS[name],
        )
        builder.add_mapping(total, item_id="si-total", status="proposed", method="bilingual_exact")
        stray = builder.add_ocr_row(**common, order=3, label="雜項", label_raw="雜項", debit_minor=7)
        builder.add_mapping(stray, item_id="si-other", status="rejected", method="fuzzy")
        hidden = builder.add_ocr_row(
            **common, order=4, label="已剔除", debit_minor=POISON_EXCLUDED, credit_minor=POISON_EXCLUDED,
        )
        builder.add_exclude(statement_id, hidden)
        builder.meta["libo_statement_id"] = statement_id
        return
    if name in TOTALS and date == "2026-06-30":
        total = builder.add_ocr_row(
            **common, order=1, label="總計", label_raw="TOTAIS",
            debit_minor=TOTALS[name], credit_minor=TOTALS[name],
        )
        builder.add_mapping(total, item_id="si-total", status="proposed", method="bilingual_exact")
        return
    cash = builder.add_ocr_row(**common, order=1, label="現金", label_raw="現金", debit_minor=10)
    builder.add_mapping(cash, item_id="si-cash", status="proposed", method="bilingual_exact")
    if name == "中國工商銀行(澳門)股份有限公司" and date == "2026-09-30":
        builder.add_insert(statement_id, order=2, label="現金", debit_minor=50, item_id="si-cash")
        builder.meta["insert_statement_id"] = statement_id
    if name == "大豐銀行股份有限公司" and date == "2026-09-30":
        retired = builder.add_ocr_row(
            **common, order=2, label="已退役", label_raw="已退役", debit_minor=410041,
        )
        builder.add_mapping(retired, item_id="si-retired", status="approved", method="manual")
        builder.add_insert(
            statement_id, order=3, label="已合併", debit_minor=430043, item_id="si-merged",
        )
        builder.meta["lifecycle_statement_id"] = statement_id
