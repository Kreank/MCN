-- Migration 0010: Workflow-Infrastruktur — Nummernkreise, Statusautomaten, Prioritäten
-- Beschlüsse: B-02/B-03/B-04 (Status), B-05 (Prioritäten), B-11/B-12/B-13 (Nummernkreise)

BEGIN;

CREATE SCHEMA workflow;

-- ---------------------------------------------------------------------------
-- Nummernkreise (B-11/B-12): PREFIX-JJJJ-######, global eindeutig je Kreis.
-- Interne Nummern dürfen Lücken enthalten (B-13); Vergabe über Zeilensperre.
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.number_range (
    prefix      text NOT NULL CHECK (prefix IN ('V', 'P', 'AU', 'E')),
    year        integer NOT NULL CHECK (year BETWEEN 2000 AND 2200),
    last_value  integer NOT NULL DEFAULT 0 CHECK (last_value >= 0),
    PRIMARY KEY (prefix, year)
);

-- Belegkreise AN/RE/GS folgen in Phase 3 (Veröffentlichungslogik, B-13/B-14).

CREATE FUNCTION workflow.next_number(p_prefix text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    -- Jahreszuordnung in UTC, unabhängig von der Sitzungszeitzone (WF-12)
    v_year  integer := extract(year FROM (now() AT TIME ZONE 'UTC'))::integer;
    v_value integer;
BEGIN
    INSERT INTO workflow.number_range (prefix, year, last_value)
    VALUES (p_prefix, v_year, 1)
    ON CONFLICT (prefix, year)
    DO UPDATE SET last_value = workflow.number_range.last_value + 1
    RETURNING last_value INTO v_value;

    -- WF-03: lpad trunkiert rechts; oberhalb von 999999 wird ungepolstert
    -- weitergezählt (Format-CHECK erlaubt 6 oder mehr Stellen)
    RETURN p_prefix || '-' || v_year::text || '-' ||
           CASE WHEN v_value < 1000000
                THEN lpad(v_value::text, 6, '0')
                ELSE v_value::text END;
END;
$$;

-- ---------------------------------------------------------------------------
-- Prioritäten als konfigurierbare Stammdaten (B-05) mit beschlossenen Startwerten.
-- Die Auswertung von Arbeitstagen erfolgt anwendungsseitig; Arbeitszeit- und
-- Bereitschaftsregeln sind ausdrücklich nicht Teil von B-05.
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.priority_level (
    code             text PRIMARY KEY CHECK (code IN ('NORMAL', 'DRINGEND', 'NOTFALL')),
    label            text NOT NULL,
    reaction_target  interval NOT NULL,
    is_business_days boolean NOT NULL DEFAULT true,
    sort_order       integer NOT NULL,
    version          integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_priority_level_updated_at
    BEFORE UPDATE ON workflow.priority_level
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

INSERT INTO workflow.priority_level (code, label, reaction_target, is_business_days, sort_order) VALUES
    ('NOTFALL',  'Notfall',  interval '2 hours', false, 1),
    ('DRINGEND', 'Dringend', interval '1 day',   true,  2),
    ('NORMAL',   'Normal',   interval '5 days',  true,  3);

-- ---------------------------------------------------------------------------
-- Statusautomaten (B-02/B-03/B-04): erlaubte Übergänge als Stammdaten.
-- Rücksprünge sind nur mit Begründung zulässig (requires_reason).
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.status_transition (
    entity           text NOT NULL CHECK (entity IN ('service_case', 'work_order', 'service_job')),
    from_status      text NOT NULL,
    to_status        text NOT NULL,
    requires_reason  boolean NOT NULL DEFAULT false,
    PRIMARY KEY (entity, from_status, to_status)
);

-- B-02: Vorgang
INSERT INTO workflow.status_transition (entity, from_status, to_status, requires_reason) VALUES
    ('service_case', 'NEU',                 'IN_PRUEFUNG',         false),
    ('service_case', 'NEU',                 'ABGELEHNT',           true),
    ('service_case', 'IN_PRUEFUNG',         'RUECKFRAGE',          false),
    ('service_case', 'RUECKFRAGE',          'IN_PRUEFUNG',         false),
    ('service_case', 'IN_PRUEFUNG',         'FREIGABE_AUSSTEHEND', false),
    ('service_case', 'IN_PRUEFUNG',         'ABGELEHNT',           true),
    ('service_case', 'FREIGABE_AUSSTEHEND', 'IN_PRUEFUNG',         true),
    ('service_case', 'FREIGABE_AUSSTEHEND', 'BEAUFTRAGT',          false),
    ('service_case', 'FREIGABE_AUSSTEHEND', 'ABGELEHNT',           true),
    ('service_case', 'BEAUFTRAGT',          'ABGESCHLOSSEN',       false),
    -- Wiedereröffnung nur mit Begründung; kaufmännische Grenze regelt B-06 am Auftrag
    ('service_case', 'ABGESCHLOSSEN',       'IN_PRUEFUNG',         true);

-- B-03: Auftrag (kein Übergang verlässt ABGERECHNET; Storno nur vor Abrechnung)
INSERT INTO workflow.status_transition (entity, from_status, to_status, requires_reason) VALUES
    ('work_order', 'ENTWURF',                 'FREIGABE_AUSSTEHEND',     false),
    ('work_order', 'ENTWURF',                 'FREIGEGEBEN',             false),
    ('work_order', 'ENTWURF',                 'STORNIERT',               true),
    ('work_order', 'FREIGABE_AUSSTEHEND',     'ENTWURF',                 true),
    ('work_order', 'FREIGABE_AUSSTEHEND',     'FREIGEGEBEN',             false),
    ('work_order', 'FREIGABE_AUSSTEHEND',     'STORNIERT',               true),
    ('work_order', 'FREIGEGEBEN',             'IN_PLANUNG',              false),
    ('work_order', 'FREIGEGEBEN',             'STORNIERT',               true),
    ('work_order', 'IN_PLANUNG',              'FREIGEGEBEN',             true),
    ('work_order', 'IN_PLANUNG',              'IN_AUSFUEHRUNG',          false),
    ('work_order', 'IN_PLANUNG',              'STORNIERT',               true),
    ('work_order', 'IN_AUSFUEHRUNG',          'IN_PLANUNG',              true),
    ('work_order', 'IN_AUSFUEHRUNG',          'TECHNISCH_ABGESCHLOSSEN', false),
    ('work_order', 'IN_AUSFUEHRUNG',          'STORNIERT',               true),
    ('work_order', 'TECHNISCH_ABGESCHLOSSEN', 'IN_AUSFUEHRUNG',          true),
    ('work_order', 'TECHNISCH_ABGESCHLOSSEN', 'KAUFMAENNISCH_GEPRUEFT',  false),
    ('work_order', 'TECHNISCH_ABGESCHLOSSEN', 'STORNIERT',               true),
    ('work_order', 'KAUFMAENNISCH_GEPRUEFT',  'TECHNISCH_ABGESCHLOSSEN', true),
    ('work_order', 'KAUFMAENNISCH_GEPRUEFT',  'ABGERECHNET',             false),
    ('work_order', 'KAUFMAENNISCH_GEPRUEFT',  'STORNIERT',               true);

-- B-04: Einsatz
INSERT INTO workflow.status_transition (entity, from_status, to_status, requires_reason) VALUES
    ('service_job', 'UNGEPLANT',     'GEPLANT',       false),
    ('service_job', 'GEPLANT',       'UNGEPLANT',     true),
    ('service_job', 'GEPLANT',       'BESTAETIGT',    false),
    ('service_job', 'GEPLANT',       'AUSGEFALLEN',   true),
    ('service_job', 'BESTAETIGT',    'GEPLANT',       true),
    ('service_job', 'BESTAETIGT',    'UNTERWEGS',     false),
    ('service_job', 'BESTAETIGT',    'AUSGEFALLEN',   true),
    ('service_job', 'UNTERWEGS',     'VOR_ORT',       false),
    ('service_job', 'UNTERWEGS',     'AUSGEFALLEN',   true),
    ('service_job', 'VOR_ORT',       'PAUSIERT',      false),
    ('service_job', 'PAUSIERT',      'VOR_ORT',       false),
    ('service_job', 'VOR_ORT',       'ABGESCHLOSSEN', false),
    ('service_job', 'ABGESCHLOSSEN', 'NACHARBEIT',    true),
    ('service_job', 'NACHARBEIT',    'GEPLANT',       false);

-- ---------------------------------------------------------------------------
-- Statuswechsel-Protokoll (append-only) und gemeinsame Prüf-/Protokollfunktionen.
-- Begründungen werden über SET app.status_reason übergeben; der Benutzer über
-- SET app.current_user_id (wie in Migration 0009).
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.status_change (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity              text NOT NULL,
    entity_id           uuid NOT NULL,
    from_status         text NULL,
    to_status           text NOT NULL,
    reason              text NULL,
    changed_by_user_id  uuid NULL REFERENCES security.app_user (id),
    occurred_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_status_change_entity ON workflow.status_change (entity, entity_id, occurred_at);

CREATE TRIGGER trg_status_change_append_only
    BEFORE UPDATE OR DELETE ON workflow.status_change
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_status_change_no_truncate
    BEFORE TRUNCATE ON workflow.status_change
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON workflow.status_change FROM PUBLIC;

-- WF-02: Neue Zeilen beginnen immer im definierten Anfangsstatus; ein INSERT mit
-- fortgeschrittenem Status würde Übergangsdisziplin, Begründungspflicht und
-- lückenloses Protokoll umgehen. TG_ARGV[0] = erwarteter Anfangsstatus.
CREATE FUNCTION workflow.enforce_initial_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> TG_ARGV[0] THEN
        RAISE EXCEPTION
            '%.%: Neue Zeilen müssen im Status % beginnen (WF-02); Übergänge laufen über den Statusautomaten',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_ARGV[0];
    END IF;
    RETURN NEW;
END;
$$;

-- Validiert einen Statuswechsel gegen die Übergangstabelle (BEFORE UPDATE OF status).
-- TG_ARGV[0] = Entity-Name in workflow.status_transition.
CREATE FUNCTION workflow.validate_status_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_requires_reason boolean;
    v_reason          text := nullif(current_setting('app.status_reason', true), '');
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;

    SELECT requires_reason INTO v_requires_reason
    FROM workflow.status_transition
    WHERE entity = TG_ARGV[0] AND from_status = OLD.status AND to_status = NEW.status;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            '%: Statusübergang % -> % ist nicht erlaubt (B-02/B-03/B-04)',
            TG_ARGV[0], OLD.status, NEW.status;
    END IF;

    IF v_requires_reason AND v_reason IS NULL THEN
        RAISE EXCEPTION
            '%: Statusübergang % -> % erfordert eine Begründung (SET app.status_reason)',
            TG_ARGV[0], OLD.status, NEW.status;
    END IF;

    RETURN NEW;
END;
$$;

-- Protokolliert jeden Statuswechsel (AFTER INSERT OR UPDATE OF status).
CREATE FUNCTION workflow.log_status_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_user   uuid := nullif(current_setting('app.current_user_id', true), '')::uuid;
    v_reason text := nullif(current_setting('app.status_reason', true), '');
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.status = OLD.status THEN
        RETURN NULL;
    END IF;

    INSERT INTO workflow.status_change
        (entity, entity_id, from_status, to_status, reason, changed_by_user_id)
    VALUES
        (TG_ARGV[0], NEW.id,
         CASE WHEN TG_OP = 'UPDATE' THEN OLD.status ELSE NULL END,
         NEW.status, v_reason, v_user);

    -- WF-09: Begründung ist einmalig gültig und wird nach Gebrauch verbraucht,
    -- damit sie nicht versehentlich späteren Übergängen zugeordnet wird.
    IF v_reason IS NOT NULL THEN
        PERFORM set_config('app.status_reason', '', true);
    END IF;
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- Zusätzliches FK-Ziel für Standortkonsistenz technischer Anlagen in Phase 2
-- ---------------------------------------------------------------------------
ALTER TABLE property.technical_asset
    ADD CONSTRAINT uq_technical_asset_id_property UNIQUE (id, property_id);

COMMIT;

-- Rückwärtsstrategie: DROP der Objekte in umgekehrter Reihenfolge, nur solange
-- keine Fachdaten entstanden sind. Statusprotokoll wird niemals rückwärts migriert.
