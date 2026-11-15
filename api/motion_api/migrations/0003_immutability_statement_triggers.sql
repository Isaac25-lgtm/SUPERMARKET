-- Row-level triggers never fire when a DELETE matches no rows; add statement-level
-- guards so any DELETE/TRUNCATE (and UPDATE) on the append-only tables is refused outright.
CREATE TRIGGER audit_no_delete_stmt BEFORE DELETE OR UPDATE ON motion.audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION motion.audit_immutable();
CREATE TRIGGER approval_action_no_delete_stmt BEFORE DELETE OR UPDATE OR TRUNCATE ON motion.approval_action
    FOR EACH STATEMENT EXECUTE FUNCTION motion.audit_immutable();
CREATE TRIGGER sync_inbox_no_delete BEFORE DELETE OR TRUNCATE ON motion.sync_inbox
    FOR EACH STATEMENT EXECUTE FUNCTION motion.audit_immutable();
