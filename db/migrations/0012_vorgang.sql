-- Migration 0012: Vorgang (service_case)
-- Beschlüsse: B-02 (Status), B-05 (Priorität), A-01 (Meldender wird nicht automatisch
--             Auftraggeber), A-21/A-23 (Verantwortungsbestätigung), A-24 (MIXED)

BEGIN;

CREATE TABLE workflow.service_case (
    id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number                   text NOT NULL UNIQUE
                                  DEFAULT workflow.next_number('V')
                                  CHECK (case_number ~ '^V-[0-9]{4}-[0-9]{6,}$'),
    project_id                    uuid NULL REFERENCES workflow.project (id),
    subject                       text NOT NULL CHECK (btrim(subject) <> ''),
    description                   text NULL,
    -- Der Meldende ist eine dokumentierte Rolle, niemals automatisch Auftraggeber (A-01).
    reported_by_party_id          uuid NULL REFERENCES identity.party (id),
    reported_by_contact_point_id  uuid NULL REFERENCES identity.contact_point (id),
    property_id                   uuid NOT NULL REFERENCES property.property (id),
    building_id                   uuid NULL,
    unit_id                       uuid NULL,
    asset_id                      uuid NULL,
    responsibility_scope          text NOT NULL DEFAULT 'UNKNOWN'
                                  CHECK (responsibility_scope IN
                                  ('UNKNOWN', 'COMMON_PROPERTY', 'PRIVATE_UNIT', 'MIXED')),
    responsibility_confirmed_at   timestamptz NULL,
    responsibility_confirmed_by   uuid NULL REFERENCES security.app_user (id),
    priority                      text NOT NULL DEFAULT 'NORMAL'
                                  REFERENCES workflow.priority_level (code),
    status                        text NOT NULL DEFAULT 'NEU'
                                  CHECK (status IN ('NEU', 'IN_PRUEFUNG', 'RUECKFRAGE',
                                  'FREIGABE_AUSSTEHEND', 'BEAUFTRAGT', 'ABGESCHLOSSEN',
                                  'ABGELEHNT')),
    received_at                   timestamptz NOT NULL DEFAULT now(),
    version                       integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at                    timestamptz NOT NULL DEFAULT now(),
    updated_at                    timestamptz NOT NULL DEFAULT now(),
    -- Standortkonsistenz wie bei technical_asset (deklarativ über zusammengesetzte FKs)
    FOREIGN KEY (building_id, property_id) REFERENCES property.building (id, property_id),
    FOREIGN KEY (unit_id, building_id)     REFERENCES property.unit (id, building_id),
    FOREIGN KEY (asset_id, property_id)    REFERENCES property.technical_asset (id, property_id),
    CHECK (unit_id IS NULL OR building_id IS NOT NULL),
    -- Eine bestätigte Verantwortung ist vollständig dokumentiert (A-21)
    CHECK ((responsibility_confirmed_at IS NULL) = (responsibility_confirmed_by IS NULL))
);

COMMENT ON COLUMN workflow.service_case.responsibility_scope IS
    'UNKNOWN bis zur Bestätigung durch die benannte Fachrolle (A-21); KI darf nur vorprüfen.';

CREATE TRIGGER trg_service_case_updated_at
    BEFORE UPDATE ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TRIGGER trg_service_case_initial_status
    BEFORE INSERT ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('NEU');

CREATE TRIGGER trg_service_case_status_validate
    BEFORE UPDATE OF status ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION workflow.validate_status_change('service_case');

CREATE TRIGGER trg_service_case_status_log
    AFTER INSERT OR UPDATE OF status ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION workflow.log_status_change('service_case');

-- Eine als bestätigt markierte Verantwortung darf nicht UNKNOWN sein;
-- ein Scope-Wechsel nach Bestätigung setzt eine neue Bestätigung voraus.
CREATE FUNCTION workflow.check_case_responsibility() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.responsibility_confirmed_at IS NOT NULL
       AND NEW.responsibility_scope = 'UNKNOWN' THEN
        RAISE EXCEPTION
            'Vorgang %: UNKNOWN kann nicht als bestätigte Verantwortung gelten (A-21)', NEW.id;
    END IF;
    IF TG_OP = 'UPDATE'
       AND NEW.responsibility_scope IS DISTINCT FROM OLD.responsibility_scope
       AND OLD.responsibility_confirmed_at IS NOT NULL
       AND NEW.responsibility_confirmed_at IS NOT DISTINCT FROM OLD.responsibility_confirmed_at THEN
        RAISE EXCEPTION
            'Vorgang %: Wechsel des Verantwortungsbereichs erfordert eine neue Bestätigung (A-21)', NEW.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_service_case_responsibility
    BEFORE INSERT OR UPDATE ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION workflow.check_case_responsibility();

-- Neue Meldende dürfen keine zusammengeführten Parties sein (A-04/F-06)
CREATE TRIGGER trg_service_case_no_merged
    BEFORE INSERT OR UPDATE ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('reported_by_party_id');

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktion und Tabelle, nur solange keine
-- Fachdaten entstanden sind.
