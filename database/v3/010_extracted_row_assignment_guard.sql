-- Allow one-way assignment of raw extracted rows without allowing OCR evidence
-- to change.  Raw OCR fields remain immutable; only row_disposition,
-- statement_id, statement_extraction_id and statement_order may be assigned.
--
-- This replaces the broad append-only trigger created in 009.

DROP TRIGGER IF EXISTS trg_extracted_rows_no_update;

CREATE TRIGGER IF NOT EXISTS trg_extracted_rows_guard_update
BEFORE UPDATE ON extracted_rows
BEGIN
    SELECT RAISE(ABORT, 'extracted_rows OCR evidence is immutable')
    WHERE NEW.page_extraction_id IS NOT OLD.page_extraction_id
       OR NEW.document_page_id IS NOT OLD.document_page_id
       OR NEW.run_id IS NOT OLD.run_id
       OR NEW.page_row_order IS NOT OLD.page_row_order
       OR NEW.label_raw IS NOT OLD.label_raw
       OR NEW.label_normalized IS NOT OLD.label_normalized
       OR NEW.label_language IS NOT OLD.label_language
       OR NEW.debit_raw IS NOT OLD.debit_raw
       OR NEW.credit_raw IS NOT OLD.credit_raw
       OR NEW.debit_parsed_minor IS NOT OLD.debit_parsed_minor
       OR NEW.credit_parsed_minor IS NOT OLD.credit_parsed_minor
       OR NEW.debit_value_state IS NOT OLD.debit_value_state
       OR NEW.credit_value_state IS NOT OLD.credit_value_state
       OR NEW.row_type_proposed IS NOT OLD.row_type_proposed
       OR NEW.parent_extracted_row_id IS NOT OLD.parent_extracted_row_id
       OR NEW.parent_context_status IS NOT OLD.parent_context_status
       OR NEW.indent_level IS NOT OLD.indent_level
       OR NEW.row_band_y0 IS NOT OLD.row_band_y0
       OR NEW.row_band_y1 IS NOT OLD.row_band_y1
       OR NEW.label_bbox_x0 IS NOT OLD.label_bbox_x0
       OR NEW.label_bbox_y0 IS NOT OLD.label_bbox_y0
       OR NEW.label_bbox_x1 IS NOT OLD.label_bbox_x1
       OR NEW.label_bbox_y1 IS NOT OLD.label_bbox_y1
       OR NEW.debit_bbox_x0 IS NOT OLD.debit_bbox_x0
       OR NEW.debit_bbox_y0 IS NOT OLD.debit_bbox_y0
       OR NEW.debit_bbox_x1 IS NOT OLD.debit_bbox_x1
       OR NEW.debit_bbox_y1 IS NOT OLD.debit_bbox_y1
       OR NEW.credit_bbox_x0 IS NOT OLD.credit_bbox_x0
       OR NEW.credit_bbox_y0 IS NOT OLD.credit_bbox_y0
       OR NEW.credit_bbox_x1 IS NOT OLD.credit_bbox_x1
       OR NEW.credit_bbox_y1 IS NOT OLD.credit_bbox_y1
       OR NEW.row_confidence IS NOT OLD.row_confidence
       OR NEW.row_payload_json IS NOT OLD.row_payload_json
       OR NEW.created_at IS NOT OLD.created_at;

    SELECT RAISE(ABORT, 'invalid extracted row assignment transition')
    WHERE NOT (
        OLD.row_disposition = NEW.row_disposition
        OR (OLD.row_disposition = 'unassigned'
            AND NEW.row_disposition IN ('included', 'excluded_page_role'))
        OR (OLD.row_disposition = 'excluded_page_role'
            AND NEW.row_disposition = 'included')
    );
END;
