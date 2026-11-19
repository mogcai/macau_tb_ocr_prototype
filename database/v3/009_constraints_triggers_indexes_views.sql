-- v3: partial unique indexes, append-only / adoption / publication gates and views.

-- ---------------------------------------------------------------------------
-- Partial unique indexes
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_approved_mapping_per_row
    ON statement_row_mappings(statement_row_id)
    WHERE mapping_status = 'approved';

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_publication_per_statement
    ON publication_records(statement_id)
    WHERE publication_status = 'published';

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_primary_page_per_statement
    ON statement_pages(statement_id)
    WHERE is_primary = 1;

-- ---------------------------------------------------------------------------
-- Additional indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
    ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_type
    ON audit_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_validation_findings_page
    ON validation_findings(document_page_id);
CREATE INDEX IF NOT EXISTS idx_review_tasks_root_cause
    ON review_tasks(root_cause_key);

-- ---------------------------------------------------------------------------
-- Append-only protection
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS trg_extracted_rows_no_update
BEFORE UPDATE ON extracted_rows
BEGIN
    SELECT RAISE(ABORT, 'extracted_rows is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_extracted_rows_no_delete
BEFORE DELETE ON extracted_rows
BEGIN
    SELECT RAISE(ABORT, 'extracted_rows is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_statement_rows_no_update
BEFORE UPDATE ON statement_rows
BEGIN
    SELECT RAISE(ABORT, 'statement_rows is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_statement_rows_no_delete
BEFORE DELETE ON statement_rows
BEGIN
    SELECT RAISE(ABORT, 'statement_rows is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

-- ---------------------------------------------------------------------------
-- Adoption gates
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS trg_statements_insert_null_adoption
BEFORE INSERT ON statements
WHEN NEW.adopted_statement_extraction_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'new statement must start with NULL adopted extraction');
END;

CREATE TRIGGER IF NOT EXISTS trg_statements_adoption_no_clear
BEFORE UPDATE OF adopted_statement_extraction_id ON statements
WHEN NEW.adopted_statement_extraction_id IS NULL
 AND OLD.adopted_statement_extraction_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'cannot clear adopted extraction; adopt another completed extraction instead');
END;

CREATE TRIGGER IF NOT EXISTS trg_statements_adoption_completed
BEFORE UPDATE OF adopted_statement_extraction_id ON statements
WHEN NEW.adopted_statement_extraction_id IS NOT NULL
 AND (OLD.adopted_statement_extraction_id IS NULL
      OR NEW.adopted_statement_extraction_id <> OLD.adopted_statement_extraction_id)
BEGIN
    SELECT RAISE(ABORT, 'adopted extraction must belong to statement and be completed')
    WHERE NOT EXISTS (
        SELECT 1
        FROM statement_extractions se
        WHERE se.statement_extraction_id = NEW.adopted_statement_extraction_id
          AND se.statement_id = NEW.statement_id
          AND se.extraction_status = 'completed'
    );

    SELECT RAISE(ABORT, 'supersede or revoke current publication before adoption change')
    WHERE EXISTS (
        SELECT 1
        FROM publication_records p
        WHERE p.statement_id = NEW.statement_id
          AND p.publication_status = 'published'
          AND p.statement_extraction_id <> NEW.adopted_statement_extraction_id
    );
END;

-- ---------------------------------------------------------------------------
-- Publication gates
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS trg_publication_insert_requires_adopted
BEFORE INSERT ON publication_records
WHEN NEW.publication_status = 'published'
BEGIN
    SELECT RAISE(ABORT, 'publication must target the adopted completed extraction')
    WHERE NOT EXISTS (
        SELECT 1
        FROM statements s
        JOIN statement_extractions se
          ON se.statement_extraction_id = NEW.statement_extraction_id
         AND se.statement_id = NEW.statement_id
        WHERE s.statement_id = NEW.statement_id
          AND s.adopted_statement_extraction_id = NEW.statement_extraction_id
          AND se.extraction_status = 'completed'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_publication_update_requires_adopted
BEFORE UPDATE OF publication_status, statement_extraction_id ON publication_records
WHEN NEW.publication_status = 'published'
BEGIN
    SELECT RAISE(ABORT, 'publication must target the adopted completed extraction')
    WHERE NOT EXISTS (
        SELECT 1
        FROM statements s
        JOIN statement_extractions se
          ON se.statement_extraction_id = NEW.statement_extraction_id
         AND se.statement_id = NEW.statement_id
        WHERE s.statement_id = NEW.statement_id
          AND s.adopted_statement_extraction_id = NEW.statement_extraction_id
          AND se.extraction_status = 'completed'
    );
END;

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------

CREATE VIEW IF NOT EXISTS v_current_statement_rows AS
SELECT sr.*
FROM statement_rows sr
JOIN statements s
  ON s.statement_id = sr.statement_id
WHERE sr.statement_extraction_id = s.adopted_statement_extraction_id;

CREATE VIEW IF NOT EXISTS v_effective_statement_rows AS
SELECT
    sr.*,
    COALESCE((
        SELECT fv.verified_value_text
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'label_normalized'
          AND fv.verification_status = 'verified'
    ), sr.label_normalized) AS effective_label_normalized,
    COALESCE((
        SELECT CAST(fv.verified_value_text AS INTEGER)
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'debit_minor'
          AND fv.verification_status = 'verified'
    ), sr.debit_minor) AS effective_debit_minor,
    COALESCE((
        SELECT fv.verified_value_text
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'debit_value_state'
          AND fv.verification_status = 'verified'
    ), sr.debit_value_state) AS effective_debit_value_state,
    COALESCE((
        SELECT CAST(fv.verified_value_text AS INTEGER)
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'credit_minor'
          AND fv.verification_status = 'verified'
    ), sr.credit_minor) AS effective_credit_minor,
    COALESCE((
        SELECT fv.verified_value_text
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'credit_value_state'
          AND fv.verification_status = 'verified'
    ), sr.credit_value_state) AS effective_credit_value_state,
    COALESCE((
        SELECT fv.verified_value_text
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'row_type'
          AND fv.verification_status = 'verified'
    ), sr.row_type) AS effective_row_type,
    COALESCE((
        SELECT fv.verified_value_text
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'parent_row_id'
          AND fv.verification_status = 'verified'
    ), sr.parent_row_id) AS effective_parent_row_id
FROM v_current_statement_rows sr;

CREATE VIEW IF NOT EXISTS v_approved_statement_row_mappings AS
SELECT m.*
FROM statement_row_mappings m
WHERE m.mapping_status = 'approved';

CREATE VIEW IF NOT EXISTS v_current_publication AS
SELECT p.*
FROM publication_records p
JOIN statements s
  ON s.statement_id = p.statement_id
WHERE p.publication_status = 'published'
  AND p.statement_extraction_id = s.adopted_statement_extraction_id;

CREATE VIEW IF NOT EXISTS v_mapping_conflicts AS
SELECT
    conflict_group_id,
    statement_row_id,
    COUNT(*) AS candidate_count
FROM statement_row_mappings
WHERE conflict_group_id IS NOT NULL
GROUP BY conflict_group_id, statement_row_id;

CREATE VIEW IF NOT EXISTS v_unmapped_statement_rows AS
SELECT sr.*
FROM v_current_statement_rows sr
WHERE NOT EXISTS (
    SELECT 1
    FROM statement_row_mappings m
    WHERE m.statement_row_id = sr.statement_row_id
      AND m.mapping_status IN ('proposed', 'approved')
);

CREATE VIEW IF NOT EXISTS v_statement_quality_summary AS
SELECT
    s.statement_id,
    (SELECT COUNT(*) FROM v_current_statement_rows sr
      WHERE sr.statement_id = s.statement_id) AS current_row_count,
    (SELECT COUNT(*) FROM v_approved_statement_row_mappings m
      JOIN v_current_statement_rows sr ON sr.statement_row_id = m.statement_row_id
      WHERE sr.statement_id = s.statement_id) AS approved_mapping_count,
    (SELECT COUNT(*) FROM validation_findings vf
      WHERE vf.statement_id = s.statement_id
        AND vf.finding_status = 'open') AS open_finding_count,
    (SELECT COUNT(*) FROM review_tasks rt
      WHERE rt.scope_statement_id = s.statement_id
        AND rt.task_status IN ('open', 'in_progress')) AS open_task_count,
    (SELECT p.quality_level FROM v_current_publication p
      WHERE p.statement_id = s.statement_id
      LIMIT 1) AS publication_quality
FROM statements s;
