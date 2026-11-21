-- Workbench: background import jobs for the V3 importer.
--
-- Import jobs are operational control records, not financial evidence.  They
-- record which operator asked for which run and where the artifacts/logs live.
-- Raw OCR evidence stays in the extraction tables once replay succeeds.

CREATE TABLE IF NOT EXISTS import_jobs (
    job_id          TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (
        status IN (
            'queued', 'running', 'completed', 'failed',
            'needs_decision', 'interrupted', 'cancelled'
        )
    ),
    operation       TEXT NOT NULL CHECK (
        operation IN ('full_import', 'dry_run', 'replay_only')
    ),
    source_kind     TEXT NOT NULL CHECK (source_kind IN ('url', 'upload')),
    source          TEXT NOT NULL,
    issue_key       TEXT,
    argv_json       TEXT NOT NULL,
    pdf_path        TEXT,
    pdf_sha256      TEXT,
    run_dir         TEXT,
    manifest_path   TEXT,
    log_path        TEXT,
    result_status   TEXT,
    result_json     TEXT,
    error_type      TEXT,
    error_message   TEXT,
    retry_of        TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    FOREIGN KEY (retry_of) REFERENCES import_jobs(job_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_created
    ON import_jobs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_import_jobs_status
    ON import_jobs(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_import_jobs_source
    ON import_jobs(source, status);
