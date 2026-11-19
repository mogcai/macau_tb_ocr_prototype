-- v3 foundation: OCR / extraction runs and run-specific page observations.

CREATE TABLE IF NOT EXISTS extraction_runs (
    run_id               TEXT PRIMARY KEY,
    source_document_id   TEXT NOT NULL,
    app_version          TEXT,
    ocr_engine_versions  TEXT,
    config_json          TEXT,
    started_at           TEXT,
    completed_at         TEXT,
    run_status           TEXT NOT NULL CHECK (
        run_status IN ('queued', 'running', 'completed', 'failed', 'partial')
    ),
    run_report_uri       TEXT,
    FOREIGN KEY (source_document_id)
        REFERENCES source_documents(source_document_id)
        ON DELETE RESTRICT,
    UNIQUE (run_id, source_document_id)
);

CREATE INDEX IF NOT EXISTS idx_extraction_runs_document_status
    ON extraction_runs(source_document_id, run_status);

CREATE TABLE IF NOT EXISTS page_extractions (
    page_extraction_id       TEXT PRIMARY KEY,
    run_id                   TEXT NOT NULL,
    document_page_id         TEXT NOT NULL,
    source_document_id       TEXT NOT NULL,
    ocr_status               TEXT NOT NULL CHECK (
        ocr_status IN ('pending', 'completed', 'failed', 'skipped')
    ),
    ocr_languages_json       TEXT,
    image_path               TEXT,
    image_width              INTEGER,
    image_height             INTEGER,
    number_format_detected   TEXT,
    number_format_confidence REAL,
    page_result_uri          TEXT,
    created_at               TEXT NOT NULL,
    FOREIGN KEY (run_id, source_document_id)
        REFERENCES extraction_runs(run_id, source_document_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (document_page_id, source_document_id)
        REFERENCES document_pages(document_page_id, source_document_id)
        ON DELETE RESTRICT,
    UNIQUE (run_id, document_page_id),
    UNIQUE (page_extraction_id, document_page_id, run_id),
    CHECK (image_width IS NULL OR image_width > 0),
    CHECK (image_height IS NULL OR image_height > 0),
    CHECK (
        number_format_confidence IS NULL OR
        (number_format_confidence >= 0 AND number_format_confidence <= 1)
    )
);

CREATE INDEX IF NOT EXISTS idx_page_extractions_run_page
    ON page_extractions(run_id, document_page_id);
