-- Workbench: bind reviewer sign-off to the exact extraction that was reviewed.
--
-- A statement may have several extraction attempts.  Review metrics, review
-- rows and adoption must all refer to the same attempt.  The active binding is
-- the attempt a reviewer signed off; read paths fall back to the latest
-- completed extraction only while no binding exists.

CREATE TABLE IF NOT EXISTS statement_review_bindings (
    binding_id              TEXT PRIMARY KEY,
    statement_id            TEXT NOT NULL,
    statement_extraction_id TEXT NOT NULL,
    binding_status          TEXT NOT NULL CHECK (
        binding_status IN ('active', 'void')
    ),
    bound_by                TEXT NOT NULL,
    bound_at                TEXT NOT NULL,
    voided_by               TEXT,
    voided_at               TEXT,
    note                    TEXT,
    FOREIGN KEY (statement_extraction_id, statement_id)
        REFERENCES statement_extractions(statement_extraction_id, statement_id)
        ON DELETE RESTRICT,
    CHECK (binding_status <> 'void' OR voided_at IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_statement_review_binding
    ON statement_review_bindings(statement_id)
    WHERE binding_status = 'active';

CREATE INDEX IF NOT EXISTS idx_statement_review_bindings_extraction
    ON statement_review_bindings(statement_extraction_id);

CREATE INDEX IF NOT EXISTS idx_statement_extractions_statement_status_created
    ON statement_extractions(statement_id, extraction_status, created_at DESC);

-- Effective values must distinguish "no verification" from "verified as NULL".
-- A blank state has a NULL minor amount, so COALESCE() would wrongly fall back
-- to the raw OCR amount when a reviewer clears an amount.
DROP VIEW IF EXISTS v_effective_statement_rows;
CREATE VIEW v_effective_statement_rows AS
SELECT
    sr.*,
    COALESCE((
        SELECT fv.verified_value_text
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'label_normalized'
          AND fv.verification_status = 'verified'
    ), sr.label_normalized) AS effective_label_normalized,
    CASE WHEN EXISTS (
        SELECT 1 FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'debit_minor'
          AND fv.verification_status = 'verified'
    ) THEN (
        SELECT CAST(fv.verified_value_text AS INTEGER)
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'debit_minor'
          AND fv.verification_status = 'verified'
    ) ELSE sr.debit_minor END AS effective_debit_minor,
    COALESCE((
        SELECT fv.verified_value_text
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'debit_value_state'
          AND fv.verification_status = 'verified'
    ), sr.debit_value_state) AS effective_debit_value_state,
    CASE WHEN EXISTS (
        SELECT 1 FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'credit_minor'
          AND fv.verification_status = 'verified'
    ) THEN (
        SELECT CAST(fv.verified_value_text AS INTEGER)
        FROM field_verifications fv
        WHERE fv.statement_row_id = sr.statement_row_id
          AND fv.field_name = 'credit_minor'
          AND fv.verification_status = 'verified'
    ) ELSE sr.credit_minor END AS effective_credit_minor,
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
