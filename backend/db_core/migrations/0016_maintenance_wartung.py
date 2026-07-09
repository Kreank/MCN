"""Neues Fachschema maintenance.* — Wartungsverträge (Kundendienst).

Hand-SQL nach db/README.md: neues Fachschema + Tabellen entstehen als
Django-Migration mit RunSQL und erben den Schutzstandard (No-Delete/Audit/
No-Truncate). Muster: workflow.task (0005), workflow.project_note (0035).

Ein Wartungsvertrag (maintenance.maintenance_contract) verwaltet wiederkehrende
Wartungs-/Prüfleistungen an einer Liegenschaft. Anders als Hero (Projekt ODER
Kunde) ankert MCN am Liegenschaftsbezug (property_id Pflicht) und lässt Kunde
(party_id) und Projekt (project_id) optional zu — die Domäne ist objektzentriert.

Statusautomat AKTIV ↔ INAKTIV, INAKTIV → ARCHIVIERT (finaler Status; kein
Row-Delete, keine Reaktivierung). Archivieren nur aus INAKTIV — ein
maintenance-eigener Trigger erzwingt das physisch.

Bei Fälligkeit (next_due_date) löst der Vertrag eine konfigurierte Aktion aus
(PROJEKT/AUFTRAG/AUFGABE/BENACHRICHTIGUNG); jede Auslösung wird append-only in
maintenance.maintenance_event protokolliert (Nachweis, welche Fälligkeit welches
Folgeobjekt erzeugt hat). Der Fälligkeits-Scheduler selbst ist noch nicht Teil
dieses Slices — die Auslösung erfolgt vorerst manuell über den Service.

Nummernkreis: Prefix 'W' (workflow.next_number('W')); der number_range-CHECK wird
dafür erweitert.
"""
from django.db import migrations

CREATE_SQL = r"""
CREATE SCHEMA maintenance;

-- Belegkreis W für Wartungsverträge ergänzen (Muster wie 0016 für RE/GS).
ALTER TABLE workflow.number_range DROP CONSTRAINT number_range_prefix_check;
ALTER TABLE workflow.number_range ADD CONSTRAINT number_range_prefix_check
    CHECK (prefix IN ('V', 'P', 'AU', 'E', 'AN', 'RE', 'GS', 'W'));

CREATE TABLE maintenance.maintenance_contract (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_number   text NOT NULL UNIQUE
                      DEFAULT workflow.next_number('W')
                      CHECK (contract_number ~ '^W-[0-9]{4}-[0-9]{6,}$'),
    name              text NOT NULL CHECK (btrim(name) <> ''),
    property_id       uuid NOT NULL REFERENCES property.property (id),
    party_id          uuid NULL REFERENCES identity.party (id),
    project_id        uuid NULL REFERENCES workflow.project (id),
    status            text NOT NULL DEFAULT 'AKTIV'
                      CHECK (status IN ('AKTIV', 'INAKTIV', 'ARCHIVIERT')),
    start_date        date NOT NULL,
    interval_kind     text NOT NULL CHECK (interval_kind IN
                      ('JAEHRLICH', 'MONATLICH', 'WOECHENTLICH', 'TAGE', 'FESTES_DATUM')),
    interval_days     integer NULL CHECK (interval_days > 0),
    fixed_date        date NULL,
    next_due_date     date NULL,
    due_action        text NOT NULL CHECK (due_action IN
                      ('PROJEKT', 'AUFTRAG', 'AUFGABE', 'BENACHRICHTIGUNG')),
    lead_time_days    integer NULL CHECK (lead_time_days >= 0),
    notes             text NULL,
    created_by        uuid NOT NULL REFERENCES security.app_user (id),
    version           integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    -- Intervallart und Zusatzfeld gehören zusammen.
    CONSTRAINT contract_interval_days_required
        CHECK (interval_kind <> 'TAGE' OR interval_days IS NOT NULL),
    CONSTRAINT contract_interval_fixed_required
        CHECK (interval_kind <> 'FESTES_DATUM' OR fixed_date IS NOT NULL)
);

CREATE INDEX idx_maintenance_contract_property
    ON maintenance.maintenance_contract (property_id);
CREATE INDEX idx_maintenance_contract_status
    ON maintenance.maintenance_contract (status);
CREATE INDEX idx_maintenance_contract_next_due
    ON maintenance.maintenance_contract (next_due_date)
    WHERE next_due_date IS NOT NULL AND status = 'AKTIV';

-- Statusautomat: nur AKTIV<->INAKTIV und INAKTIV->ARCHIVIERT (final).
CREATE FUNCTION maintenance.enforce_contract_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;
    IF NOT (
           (OLD.status = 'AKTIV'   AND NEW.status = 'INAKTIV')
        OR (OLD.status = 'INAKTIV' AND NEW.status = 'AKTIV')
        OR (OLD.status = 'INAKTIV' AND NEW.status = 'ARCHIVIERT')
    ) THEN
        RAISE EXCEPTION
            'Wartungsvertrag %: Statuswechsel % -> % ist nicht zulässig '
            '(nur AKTIV<->INAKTIV, INAKTIV->ARCHIVIERT)',
            NEW.contract_number, OLD.status, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_maintenance_contract_updated_at
    BEFORE UPDATE ON maintenance.maintenance_contract
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_maintenance_contract_status
    BEFORE UPDATE OF status ON maintenance.maintenance_contract
    FOR EACH ROW EXECUTE FUNCTION maintenance.enforce_contract_status();
CREATE TRIGGER trg_maintenance_contract_audit
    AFTER UPDATE ON maintenance.maintenance_contract
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_maintenance_contract_no_delete
    BEFORE DELETE ON maintenance.maintenance_contract
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_maintenance_contract_no_truncate
    BEFORE TRUNCATE ON maintenance.maintenance_contract
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON maintenance.maintenance_contract FROM PUBLIC;

-- Verknüpfte Party (Kunde) darf nicht zusammengeführt (MERGED) sein.
CREATE TRIGGER trg_maintenance_contract_no_merged
    BEFORE INSERT OR UPDATE ON maintenance.maintenance_contract
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');

-- Ausgelöste Fälligkeits-Aktionen (append-only Nachweis).
CREATE TABLE maintenance.maintenance_event (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id         uuid NOT NULL REFERENCES maintenance.maintenance_contract (id),
    occurred_at         timestamptz NOT NULL DEFAULT now(),
    due_date            date NULL,
    action              text NOT NULL CHECK (action IN
                        ('PROJEKT', 'AUFTRAG', 'AUFGABE', 'BENACHRICHTIGUNG')),
    result_object_type  text NULL,
    result_object_id    uuid NULL,
    note                text NULL,
    triggered_by        uuid NULL REFERENCES security.app_user (id),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_maintenance_event_contract
    ON maintenance.maintenance_event (contract_id);

CREATE TRIGGER trg_maintenance_event_append_only
    BEFORE UPDATE OR DELETE ON maintenance.maintenance_event
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_maintenance_event_no_truncate
    BEFORE TRUNCATE ON maintenance.maintenance_event
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON maintenance.maintenance_event FROM PUBLIC;
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS maintenance.maintenance_event;
DROP TABLE IF EXISTS maintenance.maintenance_contract;
DROP FUNCTION IF EXISTS maintenance.enforce_contract_status();
DROP SCHEMA IF EXISTS maintenance;
ALTER TABLE workflow.number_range DROP CONSTRAINT number_range_prefix_check;
ALTER TABLE workflow.number_range ADD CONSTRAINT number_range_prefix_check
    CHECK (prefix IN ('V', 'P', 'AU', 'E', 'AN', 'RE', 'GS'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0015_dunninglevel_dunningnotice_payment"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
