-- v3 foundation: bilingual standard item catalogue and row mappings.

CREATE TABLE IF NOT EXISTS standard_items (
    standard_item_id            TEXT PRIMARY KEY,
    item_key                    TEXT NOT NULL UNIQUE,
    canonical_zh                TEXT,
    canonical_zh_status         TEXT NOT NULL DEFAULT 'missing' CHECK (
        canonical_zh_status IN ('missing', 'draft', 'approved', 'rejected')
    ),
    canonical_pt                TEXT,
    canonical_pt_status         TEXT NOT NULL DEFAULT 'missing' CHECK (
        canonical_pt_status IN ('missing', 'draft', 'approved', 'rejected')
    ),
    canonical_en                TEXT,
    canonical_en_status         TEXT NOT NULL DEFAULT 'missing' CHECK (
        canonical_en_status IN ('missing', 'draft', 'approved', 'rejected')
    ),
    parent_standard_item_id     TEXT,
    category                    TEXT,
    item_status                 TEXT NOT NULL DEFAULT 'draft' CHECK (
        item_status IN ('draft', 'approved', 'merged', 'retired')
    ),
    merged_into_standard_item_id TEXT,
    created_at                  TEXT NOT NULL,
    notes                       TEXT,
    FOREIGN KEY (parent_standard_item_id)
        REFERENCES standard_items(standard_item_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (merged_into_standard_item_id)
        REFERENCES standard_items(standard_item_id)
        ON DELETE RESTRICT,
    CHECK (merged_into_standard_item_id IS NULL OR merged_into_standard_item_id <> standard_item_id),
    CHECK (
        (canonical_zh IS NULL AND canonical_zh_status = 'missing')
        OR
        (canonical_zh IS NOT NULL AND canonical_zh_status <> 'missing')
    ),
    CHECK (
        (canonical_pt IS NULL AND canonical_pt_status = 'missing')
        OR
        (canonical_pt IS NOT NULL AND canonical_pt_status <> 'missing')
    ),
    CHECK (
        (canonical_en IS NULL AND canonical_en_status = 'missing')
        OR
        (canonical_en IS NOT NULL AND canonical_en_status <> 'missing')
    ),
    CHECK (merged_into_standard_item_id IS NULL OR item_status = 'merged'),
    CHECK (item_status <> 'merged' OR merged_into_standard_item_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_standard_items_status
    ON standard_items(item_status);
CREATE INDEX IF NOT EXISTS idx_standard_items_parent
    ON standard_items(parent_standard_item_id);

CREATE TABLE IF NOT EXISTS standard_item_aliases (
    standard_item_alias_id TEXT PRIMARY KEY,
    standard_item_id       TEXT NOT NULL,
    scope_entity_id        TEXT,
    raw_name               TEXT NOT NULL,
    normalized_name        TEXT NOT NULL,
    language               TEXT NOT NULL DEFAULT 'unknown' CHECK (
        language IN ('zh-Hant', 'pt', 'en', 'unknown')
    ),
    alias_type             TEXT NOT NULL CHECK (
        alias_type IN ('bank_label', 'historical', 'abbreviation', 'ocr_variant', 'translation', 'other')
    ),
    source_registry        TEXT,
    source_legacy_id       TEXT,
    alias_status           TEXT NOT NULL DEFAULT 'proposed' CHECK (
        alias_status IN ('proposed', 'approved', 'rejected', 'retired')
    ),
    notes                  TEXT,
    FOREIGN KEY (standard_item_id)
        REFERENCES standard_items(standard_item_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (scope_entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_global_item_alias
    ON standard_item_aliases(standard_item_id, normalized_name, language)
    WHERE scope_entity_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_scoped_item_alias
    ON standard_item_aliases(standard_item_id, scope_entity_id, normalized_name, language)
    WHERE scope_entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_standard_item_aliases_lookup
    ON standard_item_aliases(normalized_name, language);
CREATE INDEX IF NOT EXISTS idx_standard_item_aliases_scope
    ON standard_item_aliases(scope_entity_id, normalized_name, language);

CREATE TABLE IF NOT EXISTS statement_row_mappings (
    mapping_id             TEXT PRIMARY KEY,
    statement_row_id       TEXT NOT NULL,
    standard_item_id       TEXT NOT NULL,
    conflict_group_id      TEXT,
    mapping_status         TEXT NOT NULL CHECK (
        mapping_status IN ('proposed', 'approved', 'rejected', 'superseded')
    ),
    match_method           TEXT CHECK (
        match_method IS NULL OR match_method IN
        ('parent_exact', 'child_path_exact', 'bilingual_exact', 'alias_exact', 'fuzzy', 'manual', 'legacy_import')
    ),
    match_score            REAL,
    mapping_version        TEXT,
    evidence_source        TEXT,
    context_json           TEXT,
    carried_from_mapping_id TEXT,
    reviewed_by            TEXT,
    reviewed_at            TEXT,
    created_at             TEXT NOT NULL,
    notes                  TEXT,
    FOREIGN KEY (statement_row_id)
        REFERENCES statement_rows(statement_row_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (standard_item_id)
        REFERENCES standard_items(standard_item_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (carried_from_mapping_id)
        REFERENCES statement_row_mappings(mapping_id)
        ON DELETE RESTRICT,
    CHECK (match_score IS NULL OR (match_score >= 0 AND match_score <= 1)),
    CHECK (
        carried_from_mapping_id IS NULL OR
        carried_from_mapping_id <> mapping_id
    ),
    CHECK (
        mapping_status <> 'approved' OR
        (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_statement_row_mappings_row_status
    ON statement_row_mappings(statement_row_id, mapping_status);
CREATE INDEX IF NOT EXISTS idx_statement_row_mappings_conflict
    ON statement_row_mappings(conflict_group_id);
CREATE INDEX IF NOT EXISTS idx_statement_row_mappings_item
    ON statement_row_mappings(standard_item_id);
