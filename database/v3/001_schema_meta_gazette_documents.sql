-- v3 foundation: schema metadata, gazette issues, source documents.
-- Target: SQLite 3.37+; shared design remains PostgreSQL-portable.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (length(updated_at) >= 19)
);

INSERT OR IGNORE INTO schema_meta (key, value, updated_at) VALUES
    ('schema_version',        '3.0', strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('id_generation_version', '1',   strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('app_version',           '0.7.0', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

CREATE TABLE IF NOT EXISTS gazette_issues (
    gazette_issue_id TEXT PRIMARY KEY,
    issue_key        TEXT NOT NULL UNIQUE,
    publication_date TEXT,
    source_url       TEXT,
    discovered_at    TEXT NOT NULL,
    notes            TEXT,
    CHECK (
        publication_date IS NULL OR (
            length(publication_date) = 10 AND
            publication_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_gazette_issues_publication_date
    ON gazette_issues(publication_date);

CREATE TABLE IF NOT EXISTS source_documents (
    source_document_id TEXT PRIMARY KEY,
    gazette_issue_id   TEXT,
    document_key       TEXT NOT NULL UNIQUE,
    source_url         TEXT,
    original_filename  TEXT,
    storage_uri        TEXT NOT NULL,
    sha256             TEXT NOT NULL,
    page_count         INTEGER,
    acquired_at        TEXT NOT NULL,
    mime_type          TEXT,
    FOREIGN KEY (gazette_issue_id)
        REFERENCES gazette_issues(gazette_issue_id)
        ON DELETE RESTRICT,
    CHECK (page_count IS NULL OR page_count > 0),
    CHECK (
        length(sha256) = 64 AND
        lower(sha256) NOT GLOB '*[^0-9a-f]*'
    )
);

CREATE INDEX IF NOT EXISTS idx_source_documents_sha256
    ON source_documents(sha256);
