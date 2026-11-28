-- One approved standard item per *current* extraction, not per historical
-- snapshot.
--
-- A statement can have several statement_extractions (a re-OCR or a new run).
-- The 014 triggers compared approved mappings across every extraction of the
-- statement, so a new extraction could never be bulk-approved once the previous
-- extraction had approvals: the UPDATE aborted with
-- "standard item already approved in this statement".
--
-- The rule now applies to rows in the same statement_extraction_id. Manual
-- inserts have no extraction, so they are compared against the statement's
-- current extraction (adopted, else latest completed).

DROP TRIGGER IF EXISTS trg_mapping_unique_item_insert;
DROP TRIGGER IF EXISTS trg_mapping_unique_item_update;
DROP TRIGGER IF EXISTS trg_adjustment_unique_item_insert;
DROP TRIGGER IF EXISTS trg_adjustment_unique_item_update;

CREATE TRIGGER trg_mapping_unique_item_insert
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
          AND sr.statement_extraction_id = target.statement_extraction_id
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

CREATE TRIGGER trg_mapping_unique_item_update
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
          AND sr.statement_extraction_id = target.statement_extraction_id
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

CREATE TRIGGER trg_adjustment_unique_item_insert
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
          AND sr.statement_extraction_id = COALESCE(
              (SELECT s.adopted_statement_extraction_id FROM statements s
                WHERE s.statement_id = NEW.statement_id),
              (SELECT se.statement_extraction_id FROM statement_extractions se
                WHERE se.statement_id = NEW.statement_id
                  AND se.extraction_status = 'completed'
                ORDER BY se.created_at DESC, se.statement_extraction_id DESC LIMIT 1)
          )
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

CREATE TRIGGER trg_adjustment_unique_item_update
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
          AND sr.statement_extraction_id = COALESCE(
              (SELECT s.adopted_statement_extraction_id FROM statements s
                WHERE s.statement_id = NEW.statement_id),
              (SELECT se.statement_extraction_id FROM statement_extractions se
                WHERE se.statement_id = NEW.statement_id
                  AND se.extraction_status = 'completed'
                ORDER BY se.created_at DESC, se.statement_extraction_id DESC LIMIT 1)
          )
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
