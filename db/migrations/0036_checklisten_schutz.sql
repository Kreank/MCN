-- Migration 0036: Schutzstandard für Checklisten-Stammdaten nachziehen
-- (Review Projekt-Cockpit, PC-2) + Index-Randnotiz file_link.project_id.
-- Vorlagen werden deaktiviert statt gelöscht (0033-Linie); Instanzen und
-- Vorlagenpunkte bekommen Audit als Defense-in-depth-Rückfallebene.

BEGIN;

CREATE TRIGGER trg_checklist_template_no_delete BEFORE DELETE ON workflow.checklist_template
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_checklist_template_no_truncate BEFORE TRUNCATE ON workflow.checklist_template
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON workflow.checklist_template FROM PUBLIC;

CREATE TRIGGER trg_checklist_template_item_audit AFTER UPDATE ON workflow.checklist_template_item
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_checklist_template_item_delete_audit AFTER DELETE ON workflow.checklist_template_item
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_checklist_template_item_no_truncate BEFORE TRUNCATE ON workflow.checklist_template_item
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();

CREATE TRIGGER trg_checklist_delete_audit AFTER DELETE ON workflow.checklist
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_checklist_no_truncate BEFORE TRUNCATE ON workflow.checklist
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_checklist_item_no_truncate BEFORE TRUNCATE ON workflow.checklist_item
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();

CREATE INDEX idx_file_link_project ON content.file_link (project_id) WHERE project_id IS NOT NULL;

COMMIT;

-- Rückwärtsstrategie: Trigger und Index droppen, GRANTs wiederherstellen.
