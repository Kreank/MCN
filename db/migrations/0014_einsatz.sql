-- Migration 0014: Einsatz (service_job) und Einsatzzuordnung
-- Beschlüsse: B-04 (Einsatzstatus), B-07 (technische Vollständigkeit als Prozessregel),
--             B-11/B-12 (Nummernkreis E)

BEGIN;

CREATE TABLE workflow.service_job (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_number                text NOT NULL UNIQUE
                              DEFAULT workflow.next_number('E')
                              CHECK (job_number ~ '^E-[0-9]{4}-[0-9]{6,}$'),
    work_order_id             uuid NOT NULL REFERENCES workflow.work_order (id),
    status                    text NOT NULL DEFAULT 'UNGEPLANT'
                              CHECK (status IN ('UNGEPLANT', 'GEPLANT', 'BESTAETIGT',
                              'UNTERWEGS', 'VOR_ORT', 'PAUSIERT', 'ABGESCHLOSSEN',
                              'NACHARBEIT', 'AUSGEFALLEN')),
    scheduled_start           timestamptz NULL,
    scheduled_end             timestamptz NULL,
    actual_start              timestamptz NULL,
    actual_end                timestamptz NULL,
    on_site_contact_party_id  uuid NULL REFERENCES identity.party (id),
    access_instructions       text NULL,
    completion_notes          text NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CHECK (scheduled_end IS NULL OR scheduled_start IS NULL OR scheduled_end > scheduled_start),
    CHECK (actual_end IS NULL OR actual_start IS NULL OR actual_end > actual_start),
    -- Ein geplanter Einsatz besitzt einen Planungszeitraum
    CHECK (status NOT IN ('GEPLANT', 'BESTAETIGT') OR scheduled_start IS NOT NULL)
);

CREATE TRIGGER trg_service_job_updated_at
    BEFORE UPDATE ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TRIGGER trg_service_job_initial_status
    BEFORE INSERT ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('UNGEPLANT');

CREATE TRIGGER trg_service_job_status_validate
    BEFORE UPDATE OF status ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION workflow.validate_status_change('service_job');

CREATE TRIGGER trg_service_job_status_log
    AFTER INSERT OR UPDATE OF status ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION workflow.log_status_change('service_job');

CREATE TRIGGER trg_service_job_no_merged
    BEFORE INSERT OR UPDATE ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('on_site_contact_party_id');

-- Einsätze dürfen vor der Auftragsfreigabe angelegt und geplant werden
-- (A-23: technische Vorbereitung zulässig), aber nicht auf abgerechnete oder
-- stornierte Aufträge (B-03/B-06 — Folgeauftrag verwenden).
CREATE FUNCTION workflow.check_job_order_status() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;
    IF v_status IN ('ABGERECHNET', 'STORNIERT') THEN
        RAISE EXCEPTION
            'Einsatz für Auftrag % unzulässig: Auftrag ist % (B-03/B-06 — Folgeauftrag verwenden)',
            NEW.work_order_id, v_status;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_service_job_order_status
    BEFORE INSERT ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION workflow.check_job_order_status();

-- WF-06 (A-23/B-01): Die AUSFÜHRUNG eines Einsatzes (ab UNTERWEGS) setzt einen
-- freigegebenen Auftrag voraus; Vorbereitung (GEPLANT/BESTAETIGT) ist zulässig.
CREATE FUNCTION workflow.check_job_execution_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF NEW.status = 'UNTERWEGS' AND OLD.status <> 'UNTERWEGS' THEN
        SELECT status INTO v_status
        FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;
        IF v_status NOT IN ('FREIGEGEBEN', 'IN_PLANUNG', 'IN_AUSFUEHRUNG') THEN
            RAISE EXCEPTION
                'Einsatz %: Ausführung erfordert einen freigegebenen Auftrag (B-01/A-23), Auftrag ist %',
                NEW.id, v_status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_service_job_execution_gate
    BEFORE UPDATE OF status ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION workflow.check_job_execution_gate();

-- ---------------------------------------------------------------------------
-- workflow.job_assignment — Zuordnung von Mitarbeitern zu Einsätzen
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.job_assignment (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_job_id    uuid NOT NULL REFERENCES workflow.service_job (id),
    assignee_user_id  uuid NOT NULL REFERENCES security.app_user (id),
    role              text NOT NULL DEFAULT 'TECHNICIAN'
                      CHECK (role IN ('TECHNICIAN', 'LEAD')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (service_job_id, assignee_user_id)
);

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktion und Tabellen, nur solange keine
-- Fachdaten entstanden sind.
