-- Workbench intake: per-page statement identity decisions.
--
-- `document_pages` records the page role and classification status.  A page
-- approved as `financial_statement` still needs the entity, statement date and
-- statement type that will be used to create the logical statement.  Those
-- decisions live here, not in free-text notes.

CREATE TABLE IF NOT EXISTS page_statement_decisions (
    document_page_id    TEXT PRIMARY KEY,
    reporting_entity_id TEXT NOT NULL,
    statement_date      TEXT NOT NULL,
    statement_type      TEXT NOT NULL DEFAULT 'balance_sheet' CHECK (
        statement_type IN (
            'balance_sheet', 'income_statement', 'cash_flow',
            'other_financial_statement', 'unknown'
        )
    ),
    decided_by          TEXT NOT NULL,
    decided_at          TEXT NOT NULL,
    note                TEXT,
    FOREIGN KEY (document_page_id)
        REFERENCES document_pages(document_page_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (reporting_entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT,
    CHECK (length(statement_date) = 10)
);

CREATE INDEX IF NOT EXISTS idx_page_statement_decisions_entity_date
    ON page_statement_decisions(reporting_entity_id, statement_date);
