-- Controlled adoption reversal.
--
-- Adoption remains a one-way operation while a statement stays active.  A
-- statement can be returned to review by clearing the adopted extraction in
-- the same update that moves it out of 'active'.  A current publication must
-- be revoked first.

DROP TRIGGER IF EXISTS trg_statements_adoption_no_clear;
CREATE TRIGGER trg_statements_adoption_no_clear
BEFORE UPDATE OF adopted_statement_extraction_id ON statements
WHEN NEW.adopted_statement_extraction_id IS NULL
 AND OLD.adopted_statement_extraction_id IS NOT NULL
 AND NEW.statement_status = 'active'
BEGIN
    SELECT RAISE(ABORT,
        'cannot clear adopted extraction while statement is active; return it to review first');
END;

DROP TRIGGER IF EXISTS trg_statements_active_requires_adoption;
CREATE TRIGGER trg_statements_active_requires_adoption
BEFORE UPDATE OF statement_status, adopted_statement_extraction_id ON statements
WHEN NEW.statement_status = 'active'
 AND NEW.adopted_statement_extraction_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'active statement must have an adopted extraction');
END;

DROP TRIGGER IF EXISTS trg_statements_adoption_clear_no_publication;
CREATE TRIGGER trg_statements_adoption_clear_no_publication
BEFORE UPDATE OF adopted_statement_extraction_id ON statements
WHEN NEW.adopted_statement_extraction_id IS NULL
 AND OLD.adopted_statement_extraction_id IS NOT NULL
 AND EXISTS (
     SELECT 1 FROM publication_records p
     WHERE p.statement_id = NEW.statement_id
       AND p.publication_status = 'published'
 )
BEGIN
    SELECT RAISE(ABORT, 'revoke the current publication before returning to review');
END;
