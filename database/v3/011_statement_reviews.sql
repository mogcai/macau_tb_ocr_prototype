-- Workbench statement-review workflow state.
-- A reviewed statement remains held/proposed until a separate adoption action.

CREATE TABLE IF NOT EXISTS statement_reviews (
    statement_id   TEXT PRIMARY KEY,
    review_status  TEXT NOT NULL CHECK (
        review_status IN ('pending', 'reviewed', 'needs_rework')
    ),
    reviewed_by    TEXT,
    reviewed_at    TEXT,
    note           TEXT,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_statement_reviews_status
    ON statement_reviews(review_status);
