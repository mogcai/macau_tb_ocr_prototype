-- v3 foundation: reporting entities, aliases and logical statements.

CREATE TABLE IF NOT EXISTS reporting_entities (
    entity_id            TEXT PRIMARY KEY,
    entity_key           TEXT NOT NULL UNIQUE,
    entity_type          TEXT NOT NULL CHECK (
        entity_type IN ('bank', 'branch', 'banking_group', 'non_bank')
    ),
    canonical_zh         TEXT,
    canonical_pt         TEXT,
    canonical_en         TEXT,
    parent_entity_id     TEXT,
    registry_status      TEXT NOT NULL DEFAULT 'proposed' CHECK (
        registry_status IN ('proposed', 'approved', 'excluded', 'merged', 'retired')
    ),
    merged_into_entity_id TEXT,
    merged_at            TEXT,
    notes                TEXT,
    created_at           TEXT NOT NULL,
    FOREIGN KEY (parent_entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (merged_into_entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT,
    CHECK (merged_into_entity_id IS NULL OR merged_into_entity_id <> entity_id),
    CHECK (merged_at IS NULL OR merged_into_entity_id IS NOT NULL),
    CHECK (registry_status <> 'merged' OR merged_into_entity_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_reporting_entities_parent
    ON reporting_entities(parent_entity_id);
CREATE INDEX IF NOT EXISTS idx_reporting_entities_status
    ON reporting_entities(registry_status);

CREATE TABLE IF NOT EXISTS reporting_entity_aliases (
    entity_alias_id  TEXT PRIMARY KEY,
    entity_id        TEXT NOT NULL,
    raw_name         TEXT NOT NULL,
    normalized_name  TEXT NOT NULL,
    language         TEXT NOT NULL DEFAULT 'unknown' CHECK (
        language IN ('zh-Hant', 'pt', 'en', 'unknown')
    ),
    alias_type       TEXT NOT NULL CHECK (
        alias_type IN ('official', 'historical', 'ocr_variant', 'abbreviation', 'branch', 'other')
    ),
    status           TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'approved', 'rejected', 'retired')
    ),
    source_reference TEXT,
    notes            TEXT,
    FOREIGN KEY (entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT,
    UNIQUE (entity_id, normalized_name, language),
    UNIQUE (entity_alias_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_reporting_entity_aliases_lookup
    ON reporting_entity_aliases(normalized_name, language);

CREATE TABLE IF NOT EXISTS statements (
    statement_id                    TEXT PRIMARY KEY,
    source_document_id              TEXT NOT NULL,
    reporting_entity_id             TEXT NOT NULL,
    entity_alias_id                 TEXT,
    entity_name_raw                 TEXT,
    entity_match_method             TEXT CHECK (
        entity_match_method IS NULL OR entity_match_method IN
        ('exact_alias', 'contextual', 'manual', 'legacy_import', 'unknown')
    ),
    entity_match_score              REAL,
    gazette_issue_id                TEXT,
    publication_date                TEXT,
    publication_date_source         TEXT CHECK (
        publication_date_source IS NULL OR publication_date_source IN
        ('issue_metadata', 'pdf', 'manual', 'legacy_import', 'unknown')
    ),
    statement_date                  TEXT,
    statement_date_source           TEXT CHECK (
        statement_date_source IS NULL OR statement_date_source IN
        ('pdf_header', 'documents_csv', 'manual', 'legacy_import', 'unknown')
    ),
    statement_type                  TEXT NOT NULL CHECK (
        statement_type IN
        ('balance_sheet', 'income_statement', 'cash_flow', 'other_financial_statement', 'unknown')
    ),
    statement_type_source           TEXT CHECK (
        statement_type_source IS NULL OR statement_type_source IN
        ('page_classification', 'pdf_header', 'manual', 'legacy_import', 'unknown')
    ),
    reporting_scope                 TEXT NOT NULL CHECK (
        reporting_scope IN ('bank', 'branch', 'group', 'other', 'unknown')
    ),
    currency                        TEXT,
    currency_source                 TEXT CHECK (
        currency_source IS NULL OR currency_source IN
        ('explicit_document', 'manual', 'legacy_import', 'unknown')
    ),
    unit_scale                      INTEGER,
    unit_scale_source               TEXT CHECK (
        unit_scale_source IS NULL OR unit_scale_source IN
        ('explicit_document', 'manual', 'legacy_import', 'unknown')
    ),
    amount_decimal_places           INTEGER,
    identity_key                    TEXT NOT NULL,
    identity_status                 TEXT NOT NULL CHECK (
        identity_status IN ('proposed', 'confirmed', 'needs_review')
    ),
    metadata_status                 TEXT NOT NULL DEFAULT 'proposed' CHECK (
        metadata_status IN ('proposed', 'confirmed', 'needs_review')
    ),
    statement_status                TEXT NOT NULL DEFAULT 'proposed' CHECK (
        statement_status IN ('proposed', 'active', 'excluded', 'merged', 'retired')
    ),
    merged_into_statement_id        TEXT,
    supersedes_statement_id         TEXT,
    adopted_statement_extraction_id TEXT,
    created_at                      TEXT NOT NULL,
    notes                           TEXT,
    FOREIGN KEY (source_document_id)
        REFERENCES source_documents(source_document_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (reporting_entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (entity_alias_id, reporting_entity_id)
        REFERENCES reporting_entity_aliases(entity_alias_id, entity_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (gazette_issue_id)
        REFERENCES gazette_issues(gazette_issue_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (merged_into_statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (supersedes_statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (adopted_statement_extraction_id, statement_id)
        REFERENCES statement_extractions(statement_extraction_id, statement_id)
        ON DELETE RESTRICT,
    UNIQUE (statement_id, source_document_id),
    CHECK (entity_match_score IS NULL OR (entity_match_score >= 0 AND entity_match_score <= 1)),
    CHECK (unit_scale IS NULL OR unit_scale IN (1, 1000, 1000000)),
    CHECK (amount_decimal_places IS NULL OR amount_decimal_places BETWEEN 0 AND 6),
    CHECK (currency IS NULL OR length(currency) = 3),
    CHECK (
        publication_date IS NULL OR (
            length(publication_date) = 10 AND
            publication_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        )
    ),
    CHECK (
        statement_date IS NULL OR (
            length(statement_date) = 10 AND
            statement_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        )
    ),
    CHECK (merged_into_statement_id IS NULL OR merged_into_statement_id <> statement_id),
    CHECK (supersedes_statement_id IS NULL OR supersedes_statement_id <> statement_id),
    CHECK (merged_into_statement_id IS NULL OR statement_status = 'merged'),
    CHECK (statement_status <> 'merged' OR merged_into_statement_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_statements_identity
    ON statements(identity_key);
CREATE INDEX IF NOT EXISTS idx_statements_entity_date_type
    ON statements(reporting_entity_id, statement_date, statement_type);
CREATE INDEX IF NOT EXISTS idx_statements_source_document
    ON statements(source_document_id);
CREATE INDEX IF NOT EXISTS idx_statements_status
    ON statements(statement_status);
CREATE INDEX IF NOT EXISTS idx_statements_adopted_extraction
    ON statements(adopted_statement_extraction_id);
