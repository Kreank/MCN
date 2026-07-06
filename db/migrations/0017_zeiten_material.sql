-- Migration 0017: Zeit- und Materialerfassung am Einsatz
-- Beschlüsse: B-27 (Zeitarten), B-28 (Korrekturregeln), B-26 (keine Bestandsführung —
--             nur Verbrauchserfassung), A-18-Prinzip (auditierte Korrekturen)

BEGIN;

-- ---------------------------------------------------------------------------
-- workflow.time_entry — Beschluss B-27
-- INTERNE_ZEIT darf ohne Einsatzbezug erfasst werden; alle anderen Zeitarten
-- gehören zu einem Einsatz.
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.time_entry (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_job_id  uuid NULL REFERENCES workflow.service_job (id),
    user_id         uuid NOT NULL REFERENCES security.app_user (id),
    time_type       text NOT NULL CHECK (time_type IN
                    ('ARBEITSZEIT', 'FAHRTZEIT', 'PAUSE', 'BEREITSCHAFT',
                     'NACHARBEIT', 'INTERNE_ZEIT')),
    started_at      timestamptz NOT NULL,
    ended_at        timestamptz NOT NULL,
    note            text NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (ended_at > started_at),
    CHECK (time_type = 'INTERNE_ZEIT' OR service_job_id IS NOT NULL)
);

CREATE TRIGGER trg_time_entry_updated_at
    BEFORE UPDATE ON workflow.time_entry
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- workflow.material_entry — Verbrauchserfassung (B-26: keine Bestandsführung)
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.material_entry (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_job_id  uuid NOT NULL REFERENCES workflow.service_job (id),
    description     text NOT NULL CHECK (btrim(description) <> ''),
    quantity        numeric(15, 3) NOT NULL CHECK (quantity > 0),
    unit            text NOT NULL CHECK (btrim(unit) <> ''),
    note            text NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    recorded_by     uuid NOT NULL REFERENCES security.app_user (id)
);

CREATE TRIGGER trg_material_entry_updated_at
    BEFORE UPDATE ON workflow.material_entry
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- Beschluss B-28: Korrekturfenster.
--   Bis Einsatzabschluss: frei korrigierbar (Monteur; Rollenprüfung folgt mit B-36).
--   Nach Einsatzabschluss: nur mit Begründung (SET LOCAL app.correction_reason),
--     vollständig auditiert.
--   Nach kaufmännischer Freigabe des Auftrags (KAUFMAENNISCH_GEPRUEFT/ABGERECHNET):
--     keine Änderung mehr.
-- Einträge ohne Einsatzbezug (INTERNE_ZEIT) bleiben frei korrigierbar.
-- ---------------------------------------------------------------------------
CREATE FUNCTION workflow.guard_entry_correction() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_job_id       uuid;
    v_job_status   text;
    v_order_status text;
    v_reason       text := nullif(current_setting('app.correction_reason', true), '');
BEGIN
    -- P3-03 (WF-01-Linie): Der Einsatzbezug ist unveränderlich; Korrektur =
    -- Löschen im Fenster + Neuerfassung.
    IF TG_OP = 'UPDATE' AND NEW.service_job_id IS DISTINCT FROM OLD.service_job_id THEN
        RAISE EXCEPTION
            '%.%: Der Einsatzbezug ist unveränderlich (B-28/P3-03)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    -- P3-04: Das Korrekturfenster gilt auch für neue Einträge (INSERT).
    v_job_id := CASE WHEN TG_OP = 'INSERT' THEN NEW.service_job_id ELSE OLD.service_job_id END;
    IF v_job_id IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;

    SELECT j.status, o.status INTO v_job_status, v_order_status
    FROM workflow.service_job j
    JOIN workflow.work_order o ON o.id = j.work_order_id
    WHERE j.id = v_job_id
    FOR SHARE OF j;

    IF v_order_status IN ('KAUFMAENNISCH_GEPRUEFT', 'ABGERECHNET') THEN
        RAISE EXCEPTION
            '%.%: Nach kaufmännischer Freigabe des Auftrags sind Zeit-/Materialänderungen unzulässig (B-28)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    IF v_job_status IN ('ABGESCHLOSSEN', 'NACHARBEIT') AND v_reason IS NULL THEN
        RAISE EXCEPTION
            '%.%: Änderung nach Einsatzabschluss erfordert eine Begründung (SET LOCAL app.correction_reason, B-28)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_time_entry_correction
    BEFORE INSERT OR UPDATE OR DELETE ON workflow.time_entry
    FOR EACH ROW EXECUTE FUNCTION workflow.guard_entry_correction();

CREATE TRIGGER trg_material_entry_correction
    BEFORE INSERT OR UPDATE OR DELETE ON workflow.material_entry
    FOR EACH ROW EXECUTE FUNCTION workflow.guard_entry_correction();

-- Änderungen werden auditiert (F-02-Linie); DELETE bleibt im B-28-Fenster erlaubt
-- und wird über den Audit-Trigger nicht erfasst — deshalb zusätzlich ein
-- Lösch-Audit mit Vorher-Bild.
CREATE FUNCTION audit.audit_row_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_user uuid := nullif(current_setting('app.current_user_id', true), '')::uuid;
BEGIN
    INSERT INTO audit.audit_entry
        (actor_type, actor_user_id, action, target_type, target_id, before_excerpt)
    VALUES
        (CASE WHEN v_user IS NULL THEN 'SYSTEM' ELSE 'USER' END,
         v_user, 'ROW_DELETE',
         TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
         (to_jsonb(OLD) ->> 'id')::uuid,
         to_jsonb(OLD));
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_time_entry_audit AFTER UPDATE ON workflow.time_entry
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_time_entry_delete_audit AFTER DELETE ON workflow.time_entry
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_material_entry_audit AFTER UPDATE ON workflow.material_entry
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_material_entry_delete_audit AFTER DELETE ON workflow.material_entry
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen, nur solange keine
-- Fachdaten entstanden sind.
