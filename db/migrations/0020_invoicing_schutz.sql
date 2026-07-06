-- Migration 0020: Historienschutz und Audit für das Belegmodul
-- Grundlage: F-02-Linie (Löschverbot + Audit), B-21/B-30 (Unveränderlichkeit)

BEGIN;

-- Belege werden niemals gelöscht; Entwürfe werden verworfen, indem sie nie
-- veröffentlicht werden (Angebot: ABGELEHNT/ABGELAUFEN; Rechnung: bleibt Entwurf).
CREATE TRIGGER trg_quote_no_delete BEFORE DELETE ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_invoice_no_delete BEFORE DELETE ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

-- Steuercodes: nie löschen (Belege referenzieren sie); Änderungen auditiert.
CREATE TRIGGER trg_tax_code_no_delete BEFORE DELETE ON invoicing.tax_code
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_tax_code_audit AFTER UPDATE ON invoicing.tax_code
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();

-- Änderungs-Audit auf Kopf- und Kindtabellen (Positions-/Beteiligtenänderungen im
-- Entwurf sind zulässig, aber nachvollziehbar).
CREATE TRIGGER trg_quote_audit AFTER UPDATE ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_invoice_audit AFTER UPDATE ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_quote_line_audit AFTER UPDATE ON invoicing.quote_line
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_quote_line_delete_audit AFTER DELETE ON invoicing.quote_line
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_invoice_line_audit AFTER UPDATE ON invoicing.invoice_line
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_invoice_line_delete_audit AFTER DELETE ON invoicing.invoice_line
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_invoice_party_audit AFTER UPDATE ON invoicing.invoice_party
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_invoice_party_delete_audit AFTER DELETE ON invoicing.invoice_party
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();

-- ---------------------------------------------------------------------------
-- P3-07: TRUNCATE-Schutz (F-03-Standard) für alle Beleg- und Erfassungstabellen
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_quote_no_truncate BEFORE TRUNCATE ON invoicing.quote
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_quote_line_no_truncate BEFORE TRUNCATE ON invoicing.quote_line
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_invoice_no_truncate BEFORE TRUNCATE ON invoicing.invoice
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_invoice_line_no_truncate BEFORE TRUNCATE ON invoicing.invoice_line
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_invoice_party_no_truncate BEFORE TRUNCATE ON invoicing.invoice_party
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_tax_code_no_truncate BEFORE TRUNCATE ON invoicing.tax_code
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_time_entry_no_truncate BEFORE TRUNCATE ON workflow.time_entry
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_material_entry_no_truncate BEFORE TRUNCATE ON workflow.material_entry
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();

REVOKE TRUNCATE ON invoicing.quote, invoicing.quote_line, invoicing.invoice,
    invoicing.invoice_line, invoicing.invoice_party, invoicing.tax_code,
    workflow.time_entry, workflow.material_entry FROM PUBLIC;

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger. Der Belegschutz darf nur durch eine
-- ausdrücklich beschlossene Korrekturmigration aufgehoben werden.
