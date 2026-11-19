-- v3: field verification, validation findings, review tasks, publication and audit.

CREATE TABLE IF NOT EXISTS field_verifications (
    field_verification_id       TEXT PRIMARY KEY,
    statement_row_id            TEXT NOT NULL,
    field_name                  TEXT NOT NULL CHECK (
        field_name IN
        ('label_normalized', 'debit_minor', 'debit_value_state',
         'credit_minor', 'credit_value_state', 'row_type', 'parent_row_id')
    ),
    original_value_text         TEXT,
    verified_value_text         TEXT,
    verification_status         TEXT NOT NULL CHECK (
        verification_status IN ('pending', 'verified', 'rejected', 'waived')
    ),
    carried_from_verification_id TEXT,
    verified_by                 TEXT,
    verified_at                 TEXT,
    reason                      TEXT,
    created_at                  TEXT NOT NULL,
    FOREIGN KEY (statement_row_id)
        REFERENCES statement_rows(statement_row_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (carried_from_verification_id)
        REFERENCES field_verifications(field_verification_id)
        ON DELETE RESTRICT,
    UNIQUE (statement_row_id, field_name),
    CHECK (
        carried_from_verification_id IS NULL OR
        carried_from_verification_id <> field_verification_id
    )
);

CREATE INDEX IF NOT EXISTS idx_field_verifications_row_field
    ON field_verifications(statement_row_id, field_name);

CREATE TABLE IF NOT EXISTS validation_findings (
    finding_id        TEXT PRIMARY KEY,
    statement_id      TEXT,
    statement_row_id  TEXT,
    document_page_id  TEXT,
    entity_id         TEXT,
    standard_item_id  TEXT,
    rule_key          TEXT NOT NULL,
    severity          TEXT NOT NULL CHECK (
        severity IN ('info', 'warning', 'error', 'critical')
    ),
    finding_status    TEXT NOT NULL CHECK (
        finding_status IN ('open', 'acknowledged', 'resolved', 'waived')
    ),
    details_json      TEXT,
    created_at        TEXT NOT NULL,
    resolved_at       TEXT,
    FOREIGN KEY (statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (statement_row_id)
        REFERENCES statement_rows(statement_row_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (document_page_id)
        REFERENCES document_pages(document_page_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (standard_item_id)
        REFERENCES standard_items(standard_item_id)
        ON DELETE RESTRICT,
    CHECK (
        (statement_id IS NOT NULL) +
        (statement_row_id IS NOT NULL) +
        (document_page_id IS NOT NULL) +
        (entity_id IS NOT NULL) +
        (standard_item_id IS NOT NULL) = 1
    )
);

CREATE INDEX IF NOT EXISTS idx_validation_findings_status
    ON validation_findings(finding_status, severity);
CREATE INDEX IF NOT EXISTS idx_validation_findings_statement
    ON validation_findings(statement_id);

CREATE TABLE IF NOT EXISTS review_tasks (
    review_task_id      TEXT PRIMARY KEY,
    scope_statement_id  TEXT,
    target_statement_id TEXT,
    statement_row_id    TEXT,
    finding_id          TEXT,
    mapping_id          TEXT,
    document_page_id    TEXT,
    entity_id           TEXT,
    standard_item_id    TEXT,
    root_cause_key      TEXT,
    group_key           TEXT,
    criteria_json       TEXT,
    affected_count      INTEGER,
    task_type           TEXT NOT NULL CHECK (
        task_type IN
        ('amount_review', 'label_review', 'mapping_review',
         'parent_context_review', 'page_classification', 'identity_review',
         'catalogue_decision', 'other')
    ),
    priority            TEXT NOT NULL CHECK (
        priority IN ('critical', 'high', 'normal', 'low')
    ),
    task_status         TEXT NOT NULL CHECK (
        task_status IN ('open', 'in_progress', 'resolved', 'waived')
    ),
    assigned_to         TEXT,
    created_at          TEXT NOT NULL,
    resolved_at         TEXT,
    FOREIGN KEY (scope_statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (target_statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (statement_row_id)
        REFERENCES statement_rows(statement_row_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (finding_id)
        REFERENCES validation_findings(finding_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (mapping_id)
        REFERENCES statement_row_mappings(mapping_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (document_page_id)
        REFERENCES document_pages(document_page_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (standard_item_id)
        REFERENCES standard_items(standard_item_id)
        ON DELETE RESTRICT,
    CHECK (
        (target_statement_id IS NOT NULL) +
        (statement_row_id IS NOT NULL) +
        (finding_id IS NOT NULL) +
        (mapping_id IS NOT NULL) +
        (document_page_id IS NOT NULL) +
        (entity_id IS NOT NULL) +
        (standard_item_id IS NOT NULL) +
        (root_cause_key IS NOT NULL) = 1
    ),
    CHECK (affected_count IS NULL OR affected_count >= 0),
    CHECK (root_cause_key IS NULL OR criteria_json IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_review_tasks_status
    ON review_tasks(task_status, priority, assigned_to);
CREATE INDEX IF NOT EXISTS idx_review_tasks_group
    ON review_tasks(group_key, task_status);
CREATE INDEX IF NOT EXISTS idx_review_tasks_scope
    ON review_tasks(scope_statement_id, task_status);

CREATE TABLE IF NOT EXISTS publication_records (
    publication_id           TEXT PRIMARY KEY,
    statement_id             TEXT NOT NULL,
    statement_extraction_id  TEXT NOT NULL,
    supersedes_publication_id TEXT,
    quality_level            TEXT NOT NULL CHECK (
        quality_level IN
        ('quality_checked', 'reviewed', 'fully_verified', 'legacy_unverified')
    ),
    publication_status       TEXT NOT NULL CHECK (
        publication_status IN ('published', 'revoked', 'superseded')
    ),
    published_by             TEXT,
    published_at             TEXT,
    reason                   TEXT,
    FOREIGN KEY (statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (statement_extraction_id, statement_id)
        REFERENCES statement_extractions(statement_extraction_id, statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (supersedes_publication_id, statement_id)
        REFERENCES publication_records(publication_id, statement_id)
        ON DELETE RESTRICT,
    UNIQUE (publication_id, statement_id, statement_extraction_id),
    UNIQUE (publication_id, statement_id),
    CHECK (
        supersedes_publication_id IS NULL OR
        supersedes_publication_id <> publication_id
    )
);

CREATE INDEX IF NOT EXISTS idx_publication_records_statement
    ON publication_records(statement_id, published_at);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_event_id          TEXT PRIMARY KEY,
    event_type              TEXT NOT NULL,
    source_document_id      TEXT,
    document_page_id        TEXT,
    entity_id               TEXT,
    statement_id            TEXT,
    statement_extraction_id TEXT,
    statement_row_id        TEXT,
    standard_item_id        TEXT,
    mapping_id              TEXT,
    field_verification_id   TEXT,
    review_task_id          TEXT,
    publication_id          TEXT,
    actor_id                TEXT,
    before_json             TEXT,
    after_json              TEXT,
    details_json            TEXT,
    reason                  TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (source_document_id)
        REFERENCES source_documents(source_document_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (document_page_id)
        REFERENCES document_pages(document_page_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (entity_id)
        REFERENCES reporting_entities(entity_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (statement_extraction_id)
        REFERENCES statement_extractions(statement_extraction_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (statement_row_id)
        REFERENCES statement_rows(statement_row_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (standard_item_id)
        REFERENCES standard_items(standard_item_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (mapping_id)
        REFERENCES statement_row_mappings(mapping_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (field_verification_id)
        REFERENCES field_verifications(field_verification_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (review_task_id)
        REFERENCES review_tasks(review_task_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_id)
        REFERENCES publication_records(publication_id)
        ON DELETE RESTRICT,
    CHECK (
        source_document_id IS NOT NULL OR
        document_page_id IS NOT NULL OR
        entity_id IS NOT NULL OR
        statement_id IS NOT NULL OR
        statement_extraction_id IS NOT NULL OR
        statement_row_id IS NOT NULL OR
        standard_item_id IS NOT NULL OR
        mapping_id IS NOT NULL OR
        field_verification_id IS NOT NULL OR
        review_task_id IS NOT NULL OR
        publication_id IS NOT NULL OR
        details_json IS NOT NULL
    )
);
