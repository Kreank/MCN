-- Migration 0015: Historienschutz und Audit für das Workflow-Modul
-- Grundlage: Benutzerbeschluss F-02 (Löschverbot + DB-Audit-Trigger), konsistent auf die
-- operativen Tabellen ausgedehnt. Aufträge werden storniert oder abgelehnt, nicht gelöscht.
-- Ausnahme: Beteiligten- und Einsatzzuordnungen dürfen korrigiert werden, solange sich der
-- übergeordnete Auftrag bzw. Einsatz noch vor der Freigabe befindet.

BEGIN;

-- ---------------------------------------------------------------------------
-- Löschverbote
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_project_no_delete BEFORE DELETE ON workflow.project
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_service_case_no_delete BEFORE DELETE ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_work_order_no_delete BEFORE DELETE ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_service_job_no_delete BEFORE DELETE ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

-- Projekt-Liegenschafts-Zuordnung: Entfernen nur solange das Projekt offen ist
CREATE FUNCTION workflow.protect_project_property() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status FROM workflow.project WHERE id = OLD.project_id;
    IF v_status IS DISTINCT FROM 'OPEN' THEN
        RAISE EXCEPTION
            'Projektzuordnung von % kann nach Projektabschluss nicht entfernt werden', OLD.project_id;
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_project_property_protect
    BEFORE DELETE ON workflow.project_property
    FOR EACH ROW EXECUTE FUNCTION workflow.protect_project_property();

-- Auftragsbeteiligte: Korrektur (DELETE) nur vor der Freigabe des Auftrags
CREATE FUNCTION workflow.protect_work_order_party() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status FROM workflow.work_order WHERE id = OLD.work_order_id;
    IF v_status NOT IN ('ENTWURF', 'FREIGABE_AUSSTEHEND') THEN
        RAISE EXCEPTION
            'Beteiligte des Auftrags % können nach Freigabe nicht mehr gelöscht werden (Historienschutz F-02); Abweichungen dokumentieren',
            OLD.work_order_id;
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_work_order_party_protect
    BEFORE DELETE ON workflow.work_order_party
    FOR EACH ROW EXECUTE FUNCTION workflow.protect_work_order_party();

-- WF-01: UPDATE-Pfade dürfen den DELETE-Schutz und die Tore nicht umgehen.
-- work_order_id ist unveränderlich; nach Freigabe sind party_id, role und source
-- fixiert (Korrektur = neue Zeile plus Dokumentation).
CREATE FUNCTION workflow.protect_work_order_party_update() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF NEW.work_order_id IS DISTINCT FROM OLD.work_order_id THEN
        RAISE EXCEPTION
            'work_order_party %: Der Auftragsbezug ist unveränderlich (WF-01)', OLD.id;
    END IF;
    IF NEW.party_id IS DISTINCT FROM OLD.party_id
       OR NEW.role IS DISTINCT FROM OLD.role
       OR NEW.source IS DISTINCT FROM OLD.source THEN
        SELECT status INTO v_status FROM workflow.work_order WHERE id = OLD.work_order_id;
        IF v_status NOT IN ('ENTWURF', 'FREIGABE_AUSSTEHEND') THEN
            RAISE EXCEPTION
                'work_order_party %: Party, Rolle und Herkunft sind nach Freigabe unveränderlich (WF-01/F-02); Abweichungen als neue Zeile dokumentieren',
                OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_work_order_party_update_protect
    BEFORE UPDATE ON workflow.work_order_party
    FOR EACH ROW EXECUTE FUNCTION workflow.protect_work_order_party_update();

-- Einsatzzuordnungen: Korrektur (DELETE) nur solange der Einsatz nicht abgeschlossen ist
CREATE FUNCTION workflow.protect_job_assignment() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status FROM workflow.service_job WHERE id = OLD.service_job_id;
    IF v_status IN ('ABGESCHLOSSEN', 'NACHARBEIT') THEN
        RAISE EXCEPTION
            'Zuordnungen des Einsatzes % können nach Abschluss nicht mehr gelöscht werden (Historienschutz F-02)',
            OLD.service_job_id;
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_job_assignment_protect
    BEFORE DELETE ON workflow.job_assignment
    FOR EACH ROW EXECUTE FUNCTION workflow.protect_job_assignment();

-- WF-07: UPDATE-Pfade unter denselben Bedingungen schützen wie DELETE.
-- project_property ist zeilenweise unveränderlich (Änderung = DELETE+INSERT bei offenem Projekt).
CREATE TRIGGER trg_project_property_immutable
    BEFORE UPDATE ON workflow.project_property
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

CREATE FUNCTION workflow.protect_job_assignment_update() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF NEW.service_job_id IS DISTINCT FROM OLD.service_job_id THEN
        RAISE EXCEPTION
            'job_assignment %: Der Einsatzbezug ist unveränderlich (WF-07)', OLD.id;
    END IF;
    SELECT status INTO v_status FROM workflow.service_job WHERE id = OLD.service_job_id;
    IF v_status IN ('ABGESCHLOSSEN', 'NACHARBEIT') THEN
        RAISE EXCEPTION
            'job_assignment %: Zuordnungen sind nach Einsatzabschluss unveränderlich (WF-07/F-02)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_job_assignment_update_protect
    BEFORE UPDATE ON workflow.job_assignment
    FOR EACH ROW EXECUTE FUNCTION workflow.protect_job_assignment_update();

-- ---------------------------------------------------------------------------
-- Automatisches Änderungs-Audit (Vorher/Nachher) wie in Migration 0009
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_project_audit AFTER UPDATE ON workflow.project
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_service_case_audit AFTER UPDATE ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_work_order_audit AFTER UPDATE ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_work_order_party_audit AFTER UPDATE ON workflow.work_order_party
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_service_job_audit AFTER UPDATE ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_job_assignment_audit AFTER UPDATE ON workflow.job_assignment
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger und Funktionen. Der Historienschutz darf nur
-- durch eine ausdrücklich beschlossene Korrekturmigration aufgehoben werden.
