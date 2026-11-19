-- v3 foundation: adopted statement row snapshots (append-only).

CREATE TABLE IF NOT EXISTS statement_rows (
    statement_row_id           TEXT PRIMARY KEY,
    statement_id               TEXT NOT NULL,
    statement_extraction_id    TEXT NOT NULL,
    extracted_row_id           TEXT NOT NULL,
    parent_row_id              TEXT,
    statement_order            INTEGER,
    row_type                   TEXT NOT NULL CHECK (
        row_type IN
        ('heading', 'item', 'subtotal', 'total', 'continuation', 'note', 'header', 'unknown')
    ),
    parent_context_status      TEXT NOT NULL DEFAULT 'unknown' CHECK (
        parent_context_status IN ('root', 'identified', 'unknown', 'not_applicable')
    ),
    label_normalized           TEXT,
    label_language             TEXT NOT NULL DEFAULT 'unknown' CHECK (
        label_language IN ('zh-Hant', 'pt', 'en', 'unknown')
    ),
    debit_minor                INTEGER,
    debit_value_state          TEXT NOT NULL CHECK (
        debit_value_state IN
        ('amount', 'dash', 'blank', 'unreadable', 'not_applicable', 'ambiguous')
    ),
    credit_minor               INTEGER,
    credit_value_state         TEXT NOT NULL CHECK (
        credit_value_state IN
        ('amount', 'dash', 'blank', 'unreadable', 'not_applicable', 'ambiguous')
    ),
    row_fingerprint            TEXT NOT NULL,
    fingerprint_version        INTEGER NOT NULL,
    carry_forward_status       TEXT NOT NULL DEFAULT 'none' CHECK (
        carry_forward_status IN ('none', 'exact_match_candidate', 'changed', 'ambiguous')
    ),
    carried_from_statement_row_id TEXT,
    created_at                 TEXT NOT NULL,
    FOREIGN KEY (statement_extraction_id, statement_id)
        REFERENCES statement_extractions(statement_extraction_id, statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (extracted_row_id, statement_extraction_id)
        REFERENCES extracted_rows(extracted_row_id, statement_extraction_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (parent_row_id, statement_id, statement_extraction_id)
        REFERENCES statement_rows(statement_row_id, statement_id, statement_extraction_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (carried_from_statement_row_id)
        REFERENCES statement_rows(statement_row_id)
        ON DELETE RESTRICT,
    UNIQUE (statement_extraction_id, extracted_row_id),
    UNIQUE (statement_row_id, statement_id, statement_extraction_id),
    CHECK (statement_order IS NULL OR statement_order >= 1),
    CHECK (
        length(row_fingerprint) = 64 AND
        lower(row_fingerprint) NOT GLOB '*[^0-9a-f]*'
    ),
    CHECK (
        (debit_value_state = 'amount' AND debit_minor IS NOT NULL)
        OR
        (debit_value_state <> 'amount' AND debit_minor IS NULL)
    ),
    CHECK (
        (credit_value_state = 'amount' AND credit_minor IS NOT NULL)
        OR
        (credit_value_state <> 'amount' AND credit_minor IS NULL)
    ),
    CHECK (
        carried_from_statement_row_id IS NULL OR
        carried_from_statement_row_id <> statement_row_id
    )
);

CREATE INDEX IF NOT EXISTS idx_statement_rows_extraction_order
    ON statement_rows(statement_extraction_id, statement_order);
CREATE INDEX IF NOT EXISTS idx_statement_rows_statement_extraction
    ON statement_rows(statement_id, statement_extraction_id);
CREATE INDEX IF NOT EXISTS idx_statement_rows_fingerprint
    ON statement_rows(row_fingerprint);
