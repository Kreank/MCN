-- Migration 0023: Kommunikation mit Zuordnungskaskade und Klärungskorb
-- Beschlüsse: B-31 (Zuordnung: Vorgangsnummer -> Absender -> KI mit Bestätigung),
--             B-32 (Kanäle), B-33 (Sichtbarkeitsklassen), A-01 (Meldender bleibt Rolle)

BEGIN;

CREATE TABLE content.communication (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel                text NOT NULL CHECK (channel IN
                           ('EMAIL', 'TELEFONNOTIZ', 'SMS_MESSENGER', 'PORTAL',
                            'BRIEF', 'GESPRAECHSNOTIZ')),
    direction              text NOT NULL CHECK (direction IN
                           ('EINGEHEND', 'AUSGEHEND', 'INTERN')),
    subject                text NULL,
    body                   text NULL,
    -- Absender/Empfänger: Party wenn bekannt, sonst Rohangabe (E-Mail-Adresse etc.)
    counterpart_party_id   uuid NULL REFERENCES identity.party (id),
    counterpart_raw        text NULL,
    occurred_at            timestamptz NOT NULL DEFAULT now(),
    recorded_by            uuid NOT NULL REFERENCES security.app_user (id),
    -- B-33: Sichtbarkeitsklassen (Detailmatrix mit B-36; hier die Kennzeichnung)
    is_internal            boolean NOT NULL DEFAULT false,
    is_commercial          boolean NOT NULL DEFAULT false,
    -- B-31: Zuordnungsstatus und -herkunft
    assignment_status      text NOT NULL DEFAULT 'KLAERUNGSKORB'
                           CHECK (assignment_status IN ('KLAERUNGSKORB', 'ZUGEORDNET')),
    assignment_source      text NULL CHECK (assignment_source IN
                           ('VORGANGSNUMMER', 'ABSENDER', 'KI_BESTAETIGT', 'MANUELL')),
    assignment_confirmed_by uuid NULL REFERENCES security.app_user (id),
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    -- Zuordnung ist immer begründet; KI-Zuordnung immer menschlich bestätigt (B-31/B-42)
    CHECK (assignment_status <> 'ZUGEORDNET' OR assignment_source IS NOT NULL),
    CHECK (assignment_source <> 'KI_BESTAETIGT' OR assignment_confirmed_by IS NOT NULL)
);

CREATE TRIGGER trg_communication_updated_at
    BEFORE UPDATE ON content.communication
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TRIGGER trg_communication_no_merged
    BEFORE INSERT OR UPDATE ON content.communication
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('counterpart_party_id');

-- ---------------------------------------------------------------------------
-- content.communication_link — Ziel der Zuordnung (kontrollierte FKs)
-- ---------------------------------------------------------------------------
CREATE TABLE content.communication_link (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    communication_id  uuid NOT NULL REFERENCES content.communication (id),
    service_case_id   uuid NULL REFERENCES workflow.service_case (id),
    work_order_id     uuid NULL REFERENCES workflow.work_order (id),
    project_id        uuid NULL REFERENCES workflow.project (id),
    property_id       uuid NULL REFERENCES property.property (id),
    quote_id          uuid NULL REFERENCES invoicing.quote (id),
    invoice_id        uuid NULL REFERENCES invoicing.invoice (id),
    party_id          uuid NULL REFERENCES identity.party (id),
    created_by        uuid NOT NULL REFERENCES security.app_user (id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(service_case_id, work_order_id, project_id, property_id,
                        quote_id, invoice_id, party_id) = 1)
);

CREATE INDEX idx_communication_link_comm ON content.communication_link (communication_id);

-- Anhänge: file_link erhält den Kommunikationsbezug als weiteres Ziel
ALTER TABLE content.file_link
    ADD COLUMN communication_id uuid NULL REFERENCES content.communication (id);
ALTER TABLE content.file_link DROP CONSTRAINT file_link_check;
ALTER TABLE content.file_link ADD CONSTRAINT file_link_check
    CHECK (num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                        unit_id, asset_id, quote_id, invoice_id, party_id,
                        communication_id) = 1);

-- ---------------------------------------------------------------------------
-- B-31: Konsistenz Zuordnungsstatus <-> Verknüpfungen (deferred).
-- ZUGEORDNET erfordert mindestens eine Verknüpfung; ohne Verknüpfung bleibt die
-- Kommunikation im Klärungskorb.
-- ---------------------------------------------------------------------------
CREATE FUNCTION content.assert_communication_assignment(p_comm_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_comm  content.communication%ROWTYPE;
    v_links integer;
BEGIN
    SELECT * INTO v_comm FROM content.communication WHERE id = p_comm_id FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;

    SELECT count(*) INTO v_links
    FROM content.communication_link WHERE communication_id = p_comm_id;

    IF v_comm.assignment_status = 'ZUGEORDNET' AND v_links = 0 THEN
        RAISE EXCEPTION
            'Kommunikation %: ZUGEORDNET ohne Verknüpfung ist unzulässig (B-31 — Klärungskorb verwenden)',
            p_comm_id;
    END IF;
    IF v_comm.assignment_status = 'KLAERUNGSKORB' AND v_links > 0 THEN
        RAISE EXCEPTION
            'Kommunikation %: Verknüpfungen erfordern den Status ZUGEORDNET (B-31)', p_comm_id;
    END IF;
END;
$$;

CREATE FUNCTION content.check_communication_assignment() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM content.assert_communication_assignment(NEW.id);
    RETURN NULL;
END;
$$;

CREATE FUNCTION content.check_communication_link_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM content.assert_communication_assignment(NEW.communication_id);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        IF TG_OP = 'DELETE' OR OLD.communication_id IS DISTINCT FROM NEW.communication_id THEN
            PERFORM content.assert_communication_assignment(OLD.communication_id);
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_communication_assignment
    AFTER INSERT OR UPDATE OF assignment_status ON content.communication
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION content.check_communication_assignment();

CREATE CONSTRAINT TRIGGER trg_communication_link_assignment
    AFTER INSERT OR UPDATE OR DELETE ON content.communication_link
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION content.check_communication_link_change();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen sowie der
-- file_link-Erweiterung, nur solange keine Kommunikation entstanden ist.
