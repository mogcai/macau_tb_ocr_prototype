-- One approved standard item per statement.
--
-- A statement may contain many rows, but the same standard item may only be
-- approved for one row.  The same rule applies to manually inserted rows.
-- Proposed mappings are suggestions and are not constrained.

CREATE TRIGGER IF NOT EXISTS trg_mapping_unique_item_insert
BEFORE INSERT ON statement_row_mappings
WHEN NEW.mapping_status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'standard item already approved in this statement')
    WHERE EXISTS (
        SELECT 1
        FROM statement_row_mappings m
        JOIN statement_rows sr ON sr.statement_row_id = m.statement_row_id
        JOIN statement_rows target ON target.statement_row_id = NEW.statement_row_id
        WHERE m.mapping_status = 'approved'
          AND m.standard_item_id = NEW.standard_item_id
          AND m.statement_row_id <> NEW.statement_row_id
          AND sr.statement_id = target.statement_id
    )
    OR EXISTS (
        SELECT 1
        FROM statement_row_adjustments a
        JOIN statement_rows target ON target.statement_row_id = NEW.statement_row_id
        WHERE a.statement_id = target.statement_id
          AND a.adjustment_type = 'insert'
          AND a.adjustment_status = 'active'
          AND a.standard_item_id = NEW.standard_item_id
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_mapping_unique_item_update
BEFORE UPDATE OF mapping_status, standard_item_id, statement_row_id
ON statement_row_mappings
WHEN NEW.mapping_status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'standard item already approved in this statement')
    WHERE EXISTS (
        SELECT 1
        FROM statement_row_mappings m
        JOIN statement_rows sr ON sr.statement_row_id = m.statement_row_id
        JOIN statement_rows target ON target.statement_row_id = NEW.statement_row_id
        WHERE m.mapping_status = 'approved'
          AND m.standard_item_id = NEW.standard_item_id
          AND m.statement_row_id <> NEW.statement_row_id
          AND sr.statement_id = target.statement_id
    )
    OR EXISTS (
        SELECT 1
        FROM statement_row_adjustments a
        JOIN statement_rows target ON target.statement_row_id = NEW.statement_row_id
        WHERE a.statement_id = target.statement_id
          AND a.adjustment_type = 'insert'
          AND a.adjustment_status = 'active'
          AND a.standard_item_id = NEW.standard_item_id
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_adjustment_unique_item_insert
BEFORE INSERT ON statement_row_adjustments
WHEN NEW.adjustment_type = 'insert'
 AND NEW.adjustment_status = 'active'
 AND NEW.standard_item_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'standard item already approved in this statement')
    WHERE EXISTS (
        SELECT 1
        FROM statement_row_mappings m
        JOIN statement_rows sr ON sr.statement_row_id = m.statement_row_id
        WHERE sr.statement_id = NEW.statement_id
          AND m.mapping_status = 'approved'
          AND m.standard_item_id = NEW.standard_item_id
    )
    OR EXISTS (
        SELECT 1
        FROM statement_row_adjustments a
        WHERE a.statement_id = NEW.statement_id
          AND a.adjustment_type = 'insert'
          AND a.adjustment_status = 'active'
          AND a.standard_item_id = NEW.standard_item_id
          AND a.adjustment_id <> NEW.adjustment_id
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_adjustment_unique_item_update
BEFORE UPDATE OF standard_item_id, adjustment_status, adjustment_type
ON statement_row_adjustments
WHEN NEW.adjustment_type = 'insert'
 AND NEW.adjustment_status = 'active'
 AND NEW.standard_item_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'standard item already approved in this statement')
    WHERE EXISTS (
        SELECT 1
        FROM statement_row_mappings m
        JOIN statement_rows sr ON sr.statement_row_id = m.statement_row_id
        WHERE sr.statement_id = NEW.statement_id
          AND m.mapping_status = 'approved'
          AND m.standard_item_id = NEW.standard_item_id
    )
    OR EXISTS (
        SELECT 1
        FROM statement_row_adjustments a
        WHERE a.statement_id = NEW.statement_id
          AND a.adjustment_type = 'insert'
          AND a.adjustment_status = 'active'
          AND a.standard_item_id = NEW.standard_item_id
          AND a.adjustment_id <> NEW.adjustment_id
    );
END;
