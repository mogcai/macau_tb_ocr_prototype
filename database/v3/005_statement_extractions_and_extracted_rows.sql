-- v3 foundation: statement extraction attempts and immutable OCR row evidence.

CREATE TABLE IF NOT EXISTS statement_extractions (
    statement_extraction_id TEXT PRIMARY KEY,
    statement_id            TEXT NOT NULL,
    run_id                  TEXT NOT NULL,
    source_document_id      TEXT NOT NULL,
    extraction_status       TEXT NOT NULL CHECK (
        extraction_status IN ('proposed', 'completed', 'failed', 'rejected')
    ),
    page_range_json         TEXT,
    created_at              TEXT NOT NULL,
    notes                   TEXT,
    FOREIGN KEY (statement_id, source_document_id)
        REFERENCES statements(statement_id, source_document_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (run_id, source_document_id)
        REFERENCES extraction_runs(run_id, source_document_id)
        ON DELETE RESTRICT,
    UNIQUE (statement_extraction_id, statement_id),
    UNIQUE (statement_extraction_id, run_id),
    UNIQUE (statement_extraction_id, statement_id, run_id),
    UNIQUE (statement_extraction_id, statement_id, run_id, source_document_id),
    UNIQUE (statement_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_statement_extractions_statement_run
    ON statement_extractions(statement_id, run_id);

CREATE TABLE IF NOT EXISTS extracted_rows (
    extracted_row_id        TEXT PRIMARY KEY,
    page_extraction_id      TEXT NOT NULL,
    document_page_id        TEXT NOT NULL,
    run_id                  TEXT NOT NULL,
    row_disposition         TEXT NOT NULL CHECK (
        row_disposition IN ('included', 'excluded_page_role', 'unassigned')
    ),
    statement_id            TEXT,
    statement_extraction_id TEXT,
    page_row_order          INTEGER NOT NULL,
    statement_order         INTEGER,
    label_raw               TEXT,
    label_normalized        TEXT,
    label_language          TEXT NOT NULL DEFAULT 'unknown' CHECK (
        label_language IN ('zh-Hant', 'pt', 'en', 'unknown')
    ),
    debit_raw               TEXT,
    credit_raw              TEXT,
    debit_parsed_minor      INTEGER,
    credit_parsed_minor     INTEGER,
    debit_value_state       TEXT NOT NULL CHECK (
        debit_value_state IN
        ('amount', 'dash', 'blank', 'unreadable', 'not_applicable', 'ambiguous')
    ),
    credit_value_state      TEXT NOT NULL CHECK (
        credit_value_state IN
        ('amount', 'dash', 'blank', 'unreadable', 'not_applicable', 'ambiguous')
    ),
    row_type_proposed       TEXT NOT NULL CHECK (
        row_type_proposed IN
        ('heading', 'item', 'subtotal', 'total', 'continuation', 'note', 'header', 'unknown')
    ),
    parent_extracted_row_id TEXT,
    parent_context_status   TEXT NOT NULL DEFAULT 'unknown' CHECK (
        parent_context_status IN ('root', 'identified', 'unknown', 'not_applicable')
    ),
    indent_level            INTEGER,
    row_band_y0             REAL,
    row_band_y1             REAL,
    label_bbox_x0           REAL,
    label_bbox_y0           REAL,
    label_bbox_x1           REAL,
    label_bbox_y1           REAL,
    debit_bbox_x0           REAL,
    debit_bbox_y0           REAL,
    debit_bbox_x1           REAL,
    debit_bbox_y1           REAL,
    credit_bbox_x0          REAL,
    credit_bbox_y0          REAL,
    credit_bbox_x1          REAL,
    credit_bbox_y1          REAL,
    row_confidence          REAL,
    row_payload_json        TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (page_extraction_id, document_page_id, run_id)
        REFERENCES page_extractions(page_extraction_id, document_page_id, run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (statement_extraction_id, statement_id, run_id)
        REFERENCES statement_extractions(statement_extraction_id, statement_id, run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (statement_id, document_page_id)
        REFERENCES statement_pages(statement_id, document_page_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (parent_extracted_row_id, page_extraction_id)
        REFERENCES extracted_rows(extracted_row_id, page_extraction_id)
        ON DELETE RESTRICT,
    UNIQUE (page_extraction_id, page_row_order),
    UNIQUE (extracted_row_id, statement_extraction_id),
    UNIQUE (extracted_row_id, page_extraction_id),
    CHECK (page_row_order >= 1),
    CHECK (statement_order IS NULL OR statement_order >= 1),
    CHECK (indent_level IS NULL OR indent_level >= 0),
    CHECK (
        (row_disposition = 'included' AND statement_id IS NOT NULL AND statement_extraction_id IS NOT NULL)
        OR
        (row_disposition IN ('excluded_page_role', 'unassigned')
         AND statement_id IS NULL AND statement_extraction_id IS NULL)
    ),
    CHECK (
        (debit_value_state = 'amount' AND debit_parsed_minor IS NOT NULL)
        OR
        (debit_value_state <> 'amount' AND debit_parsed_minor IS NULL)
    ),
    CHECK (
        (credit_value_state = 'amount' AND credit_parsed_minor IS NOT NULL)
        OR
        (credit_value_state <> 'amount' AND credit_parsed_minor IS NULL)
    ),
    CHECK (row_confidence IS NULL OR (row_confidence >= 0 AND row_confidence <= 1))
);

CREATE INDEX IF NOT EXISTS idx_extracted_rows_statement
    ON extracted_rows(statement_extraction_id, statement_order);
CREATE INDEX IF NOT EXISTS idx_extracted_rows_page
    ON extracted_rows(page_extraction_id, page_row_order);
CREATE INDEX IF NOT EXISTS idx_extracted_rows_disposition
    ON extracted_rows(row_disposition);
