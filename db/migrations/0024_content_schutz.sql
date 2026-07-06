-- Migration 0024: Historienschutz und Audit für das Content-Modul
-- Grundlage: F-02-Linie, B-30, B-33 (Nachvollziehbarkeit der Kommunikation)

BEGIN;

-- Dokumente und Kommunikation werden nie gelöscht (Aufbewahrung/Löschung folgt C-07/C-08)
CREATE TRIGGER trg_document_no_delete BEFORE DELETE ON content.document
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_communication_no_delete BEFORE DELETE ON content.communication
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

-- TRUNCATE-Schutz (F-03-Standard)
CREATE TRIGGER trg_document_no_truncate BEFORE TRUNCATE ON content.document
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_document_link_no_truncate BEFORE TRUNCATE ON content.document_link
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_file_link_no_truncate BEFORE TRUNCATE ON content.file_link
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_communication_no_truncate BEFORE TRUNCATE ON content.communication
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_communication_link_no_truncate BEFORE TRUNCATE ON content.communication_link
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON content.document, content.document_link, content.file_link,
    content.communication, content.communication_link FROM PUBLIC;

-- Verknüpfungen historisierter/veröffentlichter Objekte: Links auf veröffentlichte
-- Dokumente sind fixiert (der veröffentlichte Stand bleibt nachvollziehbar)
CREATE FUNCTION content.protect_document_links() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
    v_doc    uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.document_id ELSE NEW.document_id END;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.document_id IS DISTINCT FROM OLD.document_id THEN
        RAISE EXCEPTION 'document_link %: Der Dokumentbezug ist unveränderlich', OLD.id;
    END IF;
    SELECT status INTO v_status FROM content.document WHERE id = v_doc FOR SHARE;
    IF TG_OP IN ('UPDATE', 'DELETE') AND v_status IN ('VEROEFFENTLICHT', 'ERSETZT') THEN
        RAISE EXCEPTION
            'document_link %: Verknüpfungen veröffentlichter Dokumente sind unveränderlich (B-30)', OLD.id;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_document_link_protect
    BEFORE UPDATE OR DELETE ON content.document_link
    FOR EACH ROW EXECUTE FUNCTION content.protect_document_links();

-- file_link: Datei- und Zielbezug unveränderlich (Korrektur = löschen + neu, auditiert)
CREATE FUNCTION content.protect_file_link() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.file_id IS DISTINCT FROM OLD.file_id THEN
        RAISE EXCEPTION 'file_link %: Der Dateibezug ist unveränderlich', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_file_link_protect
    BEFORE UPDATE ON content.file_link
    FOR EACH ROW EXECUTE FUNCTION content.protect_file_link();

-- Änderungs- und Lösch-Audit
CREATE TRIGGER trg_document_audit AFTER UPDATE ON content.document
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_communication_audit AFTER UPDATE ON content.communication
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_file_link_audit AFTER UPDATE ON content.file_link
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_file_link_delete_audit AFTER DELETE ON content.file_link
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_document_link_delete_audit AFTER DELETE ON content.document_link
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_communication_link_delete_audit AFTER DELETE ON content.communication_link
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger und Funktionen. Der Schutz darf nur durch eine
-- ausdrücklich beschlossene Korrekturmigration aufgehoben werden.
