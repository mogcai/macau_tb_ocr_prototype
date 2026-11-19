-- Workbench row adjustments for OCR over-read / under-read.
--
-- Raw OCR evidence (extracted_rows / statement_rows) is never updated or
-- deleted.  Instead:
--   * exclude : hides an existing OCR row from the effective statement;
--   * insert  : adds a manually verified row that OCR missed.
--
-- Both adjustment types are recorded with actor/reason and an audit event.
-- Adjustments can be voided (restored); the adjustment row itself is retained.

CREATE TABLE IF NOT EXISTS statement_row_adjustments (
    adjustment_id          TEXT PRIMARY KEY,
    statement_id           TEXT NOT NULL,
    adjustment_type        TEXT NOT NULL CHECK (
        adjustment_type IN ('exclude', 'insert')
    ),
    target_statement_row_id TEXT,
    display_order          REAL,
    pdf_page               INTEGER,
    row_type               TEXT CHECK (
        row_type IS NULL OR row_type IN
        ('heading', 'item', 'subtotal', 'total', 'continuation', 'note', 'header', 'unknown')
    ),
    label_normalized       TEXT,
    label_language         TEXT NOT NULL DEFAULT 'unknown' CHECK (
        label_language IN ('zh-Hant', 'pt', 'en', 'unknown')
    ),
    debit_minor            INTEGER,
    debit_value_state      TEXT NOT NULL DEFAULT 'blank' CHECK (
        debit_value_state IN
        ('amount', 'dash', 'blank', 'unreadable', 'not_applicable', 'ambiguous')
    ),
    credit_minor           INTEGER,
    credit_value_state     TEXT NOT NULL DEFAULT 'blank' CHECK (
        credit_value_state IN
        ('amount', 'dash', 'blank', 'unreadable', 'not_applicable', 'ambiguous')
    ),
    standard_item_id       TEXT,
    reason                 TEXT,
    adjusted_by            TEXT NOT NULL,
    adjusted_at            TEXT NOT NULL,
    adjustment_status      TEXT NOT NULL DEFAULT 'active' CHECK (
        adjustment_status IN ('active', 'void')
    ),
    voided_by              TEXT,
    voided_at              TEXT,
    void_reason            TEXT,
    created_at             TEXT NOT NULL,
    FOREIGN KEY (statement_id)
        REFERENCES statements(statement_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (target_statement_row_id)
        REFERENCES statement_rows(statement_row_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (standard_item_id)
        REFERENCES standard_items(standard_item_id)
        ON DELETE RESTRICT,
    CHECK (
        (adjustment_type = 'exclude'
         AND target_statement_row_id IS NOT NULL
         AND display_order IS NULL
         AND row_type IS NULL
         AND label_normalized IS NULL
         AND debit_minor IS NULL
         AND credit_minor IS NULL
         AND standard_item_id IS NULL)
        OR
        (adjustment_type = 'insert'
         AND target_statement_row_id IS NULL
         AND display_order IS NOT NULL
         AND display_order > 0
         AND row_type IS NOT NULL)
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
        (adjustment_status = 'active'
         AND voided_by IS NULL AND voided_at IS NULL)
        OR
        (adjustment_status = 'void'
         AND voided_by IS NOT NULL AND voided_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_statement_row_adjustments_active_exclude
    ON statement_row_adjustments(target_statement_row_id)
    WHERE adjustment_type = 'exclude' AND adjustment_status = 'active';

CREATE INDEX IF NOT EXISTS idx_statement_row_adjustments_statement
    ON statement_row_adjustments(statement_id, adjustment_status, display_order);

-- v_current_statement_rows now applies active adjustments:
--   * excluded OCR rows are omitted;
--   * inserted manual rows are included alongside adopted OCR rows.
DROP VIEW IF EXISTS v_current_statement_rows;
CREATE VIEW v_current_statement_rows AS
SELECT sr.*
FROM statement_rows sr
JOIN statements s
  ON s.statement_id = sr.statement_id
WHERE sr.statement_extraction_id = s.adopted_statement_extraction_id
  AND NOT EXISTS (
      SELECT 1
      FROM statement_row_adjustments a
      WHERE a.adjustment_type = 'exclude'
        AND a.adjustment_status = 'active'
        AND a.target_statement_row_id = sr.statement_row_id
  )
UNION ALL
SELECT
    a.adjustment_id AS statement_row_id,
    a.statement_id,
    s.adopted_statement_extraction_id AS statement_extraction_id,
    a.adjustment_id AS extracted_row_id,
    NULL AS parent_row_id,
    a.display_order AS statement_order,
    a.row_type,
    'unknown' AS parent_context_status,
    a.label_normalized,
    a.label_language,
    a.debit_minor,
    a.debit_value_state,
    a.credit_minor,
    a.credit_value_state,
    lower(substr(hex(a.adjustment_id) || hex(zeroblob(32)), 1, 64)) AS row_fingerprint,
    1 AS fingerprint_version,
    'none' AS carry_forward_status,
    NULL AS carried_from_statement_row_id,
    a.created_at
FROM statement_row_adjustments a
JOIN statements s
  ON s.statement_id = a.statement_id
WHERE a.adjustment_type = 'insert'
  AND a.adjustment_status = 'active'
  AND s.statement_status = 'active'
  AND s.adopted_statement_extraction_id IS NOT NULL;

DROP VIEW IF EXISTS v_unmapped_statement_rows;
CREATE VIEW v_unmapped_statement_rows AS
SELECT sr.*
FROM v_current_statement_rows sr
WHERE NOT EXISTS (
    SELECT 1
    FROM statement_row_mappings m
    WHERE m.statement_row_id = sr.statement_row_id
      AND m.mapping_status IN ('proposed', 'approved')
)
AND NOT EXISTS (
    SELECT 1
    FROM statement_row_adjustments a
    WHERE a.adjustment_id = sr.statement_row_id
      AND a.adjustment_type = 'insert'
      AND a.adjustment_status = 'active'
      AND a.standard_item_id IS NOT NULL
);
