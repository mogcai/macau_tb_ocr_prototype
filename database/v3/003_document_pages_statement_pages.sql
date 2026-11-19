-- v3 foundation: physical PDF pages and logical statement page coverage.

CREATE TABLE IF NOT EXISTS document_pages (
    document_page_id     TEXT PRIMARY KEY,
    source_document_id   TEXT NOT NULL,
    pdf_page             INTEGER NOT NULL,
    printed_page         TEXT,
    page_role            TEXT NOT NULL DEFAULT 'unknown' CHECK (
        page_role IN
        ('financial_statement', 'audit_report', 'shareholder_list',
         'cover', 'contents', 'other', 'unknown')
    ),
    classification_status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        classification_status IN ('proposed', 'approved', 'rejected')
    ),
    classification_source TEXT NOT NULL DEFAULT 'automatic' CHECK (
        classification_source IN ('automatic', 'human', 'imported')
    ),
    classified_by        TEXT,
    classified_at        TEXT,
    classification_version TEXT,
    notes                TEXT,
    FOREIGN KEY (source_document_id)
        REFERENCES source_documents(source_document_id)
        ON DELETE RESTRICT,
    UNIQUE (source_document_id, pdf_page),
    UNIQUE (document_page_id, source_document_id),
    CHECK (pdf_page >= 1),
    CHECK (
        classified_at IS NULL OR (
            length(classified_at) >= 19
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_document_pages_document_page
    ON document_pages(source_document_id, pdf_page);
CREATE INDEX IF NOT EXISTS idx_document_pages_role_status
    ON document_pages(page_role, classification_status);

CREATE TABLE IF NOT EXISTS statement_pages (
    statement_id        TEXT NOT NULL,
    source_document_id  TEXT NOT NULL,
    document_page_id    TEXT NOT NULL,
    page_order          INTEGER NOT NULL,
    is_primary          INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    created_at          TEXT NOT NULL,
    PRIMARY KEY (statement_id, document_page_id),
    FOREIGN KEY (statement_id, source_document_id)
        REFERENCES statements(statement_id, source_document_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (document_page_id, source_document_id)
        REFERENCES document_pages(document_page_id, source_document_id)
        ON DELETE RESTRICT,
    UNIQUE (statement_id, page_order)
);

CREATE INDEX IF NOT EXISTS idx_statement_pages_statement_order
    ON statement_pages(statement_id, page_order);
