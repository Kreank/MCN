-- Migration 0042: Pipeline-Editor — Status-Katalog + konfigurierbare Übergänge
-- Der Statusautomat (workflow.status_transition, B-02/B-03/B-04/B-15) wird über
-- die Einstellungen konfigurierbar. Dafür braucht der Editor ein Vokabular:
-- workflow.status_catalog benennt je Entity die zulässigen Status (identisch zu
-- den CHECK-Constraints der Entity-Tabellen) mit deutschem Label und Reihenfolge.
-- Fremdschlüssel härten status_transition gegen Tippfehler-Status ab; ein
-- append-only Änderungsprotokoll erfüllt die Audit-Pflicht (AGENT.md §4).
--
-- Bewusst NICHT Teil dieser Migration: neue Status erfinden (die CHECK-
-- Constraints der Entity-Tabellen bleiben die Wahrheit) und mehrere benannte
-- Pipelines je Entity (z. B. je Gewerk) — Letzteres wäre eine fachliche
-- Erweiterung des Statusmodells und braucht einen eigenen Beschluss.

BEGIN;

-- ---------------------------------------------------------------------------
-- Status-Katalog: Vokabular des Editors, deckungsgleich mit den CHECK-Sets
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.status_catalog (
    entity      text NOT NULL CHECK (entity IN ('service_case', 'work_order', 'service_job', 'quote')),
    status      text NOT NULL CHECK (btrim(status) <> ''),
    label       text NOT NULL CHECK (btrim(label) <> ''),
    sort_order  integer NOT NULL,
    -- Anfangsstatus (workflow.enforce_initial_status): darf im Editor nicht
    -- als bloßes Ziel verwaisen und ist für die Darstellung markiert
    is_initial  boolean NOT NULL DEFAULT false,
    -- PL-1: beschlossene Terminal-/Freeze-Semantik gehört in den Katalog,
    -- nicht in die Abwesenheit von Kanten. is_final = kein Übergang verlässt
    -- diesen Status (B-03: ABGERECHNET). is_frozen = Inhalt ist in diesem
    -- Status eingefroren (B-30-Freeze-Set des Angebots); Kanten von frozen
    -- zu non-frozen würden den Freeze auftauen und sind gesperrt.
    is_final    boolean NOT NULL DEFAULT false,
    is_frozen   boolean NOT NULL DEFAULT false,
    PRIMARY KEY (entity, status),
    UNIQUE (entity, sort_order)
);

-- B-02: Vorgang (workflow.service_case, Migration 0012)
INSERT INTO workflow.status_catalog (entity, status, label, sort_order, is_initial) VALUES
    ('service_case', 'NEU',                 'Neu',                 1, true),
    ('service_case', 'IN_PRUEFUNG',         'In Prüfung',          2, false),
    ('service_case', 'RUECKFRAGE',          'Rückfrage',           3, false),
    ('service_case', 'FREIGABE_AUSSTEHEND', 'Freigabe ausstehend', 4, false),
    ('service_case', 'BEAUFTRAGT',          'Beauftragt',          5, false),
    ('service_case', 'ABGESCHLOSSEN',       'Abgeschlossen',       6, false),
    ('service_case', 'ABGELEHNT',           'Abgelehnt',           7, false);

-- B-03: Auftrag (workflow.work_order, Migration 0013).
-- ABGERECHNET ist final: „kein Übergang verlässt ABGERECHNET" (0010).
INSERT INTO workflow.status_catalog (entity, status, label, sort_order, is_initial, is_final) VALUES
    ('work_order', 'ENTWURF',                 'Entwurf',                 1, true,  false),
    ('work_order', 'FREIGABE_AUSSTEHEND',     'Freigabe ausstehend',     2, false, false),
    ('work_order', 'FREIGEGEBEN',             'Freigegeben',             3, false, false),
    ('work_order', 'IN_PLANUNG',              'In Planung',              4, false, false),
    ('work_order', 'IN_AUSFUEHRUNG',          'In Ausführung',           5, false, false),
    ('work_order', 'TECHNISCH_ABGESCHLOSSEN', 'Technisch abgeschlossen', 6, false, false),
    ('work_order', 'KAUFMAENNISCH_GEPRUEFT',  'Kaufmännisch geprüft',    7, false, false),
    ('work_order', 'ABGERECHNET',             'Abgerechnet',             8, false, true),
    ('work_order', 'STORNIERT',               'Storniert',               9, false, false);

-- B-04: Einsatz (workflow.service_job, Migration 0014)
INSERT INTO workflow.status_catalog (entity, status, label, sort_order, is_initial) VALUES
    ('service_job', 'UNGEPLANT',     'Ungeplant',     1, true),
    ('service_job', 'GEPLANT',       'Geplant',       2, false),
    ('service_job', 'BESTAETIGT',    'Bestätigt',     3, false),
    ('service_job', 'UNTERWEGS',     'Unterwegs',     4, false),
    ('service_job', 'VOR_ORT',       'Vor Ort',       5, false),
    ('service_job', 'PAUSIERT',      'Pausiert',      6, false),
    ('service_job', 'ABGESCHLOSSEN', 'Abgeschlossen', 7, false),
    ('service_job', 'NACHARBEIT',    'Nacharbeit',    8, false),
    ('service_job', 'AUSGEFALLEN',   'Ausgefallen',   9, false);

-- B-15: Angebot (invoicing.quote, Migration 0018).
-- Ab VERSENDET ist der Inhalt eingefroren (invoicing.freeze_sent_quote);
-- das Freeze-Set ist deckungsgleich mit is_frozen markiert.
INSERT INTO workflow.status_catalog (entity, status, label, sort_order, is_initial, is_frozen) VALUES
    ('quote', 'ENTWURF',         'Entwurf',         1, true,  false),
    ('quote', 'INTERN_GEPRUEFT', 'Intern geprüft',  2, false, false),
    ('quote', 'FREIGEGEBEN',     'Freigegeben',     3, false, false),
    ('quote', 'VERSENDET',       'Versendet',       4, false, true),
    ('quote', 'ANGENOMMEN',      'Angenommen',      5, false, true),
    ('quote', 'ABGELEHNT',       'Abgelehnt',       6, false, true),
    ('quote', 'ABGELAUFEN',      'Abgelaufen',      7, false, true),
    ('quote', 'ERSETZT',         'Ersetzt',         8, false, true);

-- ---------------------------------------------------------------------------
-- Härtung: Übergänge dürfen nur Katalog-Status verwenden, keine Selbstkanten
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.status_transition
    ADD CONSTRAINT fk_status_transition_from
        FOREIGN KEY (entity, from_status) REFERENCES workflow.status_catalog (entity, status),
    ADD CONSTRAINT fk_status_transition_to
        FOREIGN KEY (entity, to_status) REFERENCES workflow.status_catalog (entity, status),
    ADD CONSTRAINT ck_status_transition_no_self CHECK (from_status <> to_status);

-- ---------------------------------------------------------------------------
-- PL-1: Beschlossene Invarianten dürfen nicht per Konfiguration kippen.
-- Kein Übergang verlässt einen finalen Status (B-03: ABGERECHNET); keine
-- Kante von einem eingefrorenen zu einem nicht eingefrorenen Status —
-- sie würde den Inhalts-Freeze (B-30) auftauen. Eine Lockerung dieser
-- Regeln wäre ein eigener Beschluss und liefe über eine neue Migration.
-- ---------------------------------------------------------------------------
CREATE FUNCTION workflow.guard_pipeline_config() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_von workflow.status_catalog%ROWTYPE;
    v_nach workflow.status_catalog%ROWTYPE;
BEGIN
    SELECT * INTO v_von FROM workflow.status_catalog
    WHERE entity = NEW.entity AND status = NEW.from_status;
    SELECT * INTO v_nach FROM workflow.status_catalog
    WHERE entity = NEW.entity AND status = NEW.to_status;

    IF v_von.is_final THEN
        RAISE EXCEPTION
            'Pipeline %: % ist final — kein Übergang verlässt diesen Status (B-03)',
            NEW.entity, NEW.from_status;
    END IF;
    IF v_von.is_frozen AND NOT v_nach.is_frozen THEN
        RAISE EXCEPTION
            'Pipeline %: % -> % würde den Inhalts-Freeze auftauen (B-30) — nicht konfigurierbar',
            NEW.entity, NEW.from_status, NEW.to_status;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_status_transition_guard
    BEFORE INSERT OR UPDATE ON workflow.status_transition
    FOR EACH ROW EXECUTE FUNCTION workflow.guard_pipeline_config();

-- ---------------------------------------------------------------------------
-- Audit: jede Pipeline-Änderung append-only protokollieren (Akteur, Zeitpunkt,
-- Aktion, Ziel — AGENT.md §4). Benutzer kommt wie überall aus
-- SET app.current_user_id (Db.Tx der API).
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.pipeline_change (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity              text NOT NULL,
    from_status         text NOT NULL,
    to_status           text NOT NULL,
    action              text NOT NULL CHECK (action IN ('ANGELEGT', 'GEAENDERT', 'GELOESCHT')),
    requires_reason     boolean NOT NULL,
    changed_by_user_id  uuid NULL REFERENCES security.app_user (id),
    occurred_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_pipeline_change_entity ON workflow.pipeline_change (entity, occurred_at);

CREATE TRIGGER trg_pipeline_change_append_only
    BEFORE UPDATE OR DELETE ON workflow.pipeline_change
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_pipeline_change_no_truncate
    BEFORE TRUNCATE ON workflow.pipeline_change
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON workflow.pipeline_change FROM PUBLIC;

CREATE FUNCTION workflow.log_pipeline_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_user uuid := nullif(current_setting('app.current_user_id', true), '')::uuid;
BEGIN
    INSERT INTO workflow.pipeline_change
        (entity, from_status, to_status, action, requires_reason, changed_by_user_id)
    VALUES (
        coalesce(NEW.entity, OLD.entity),
        coalesce(NEW.from_status, OLD.from_status),
        coalesce(NEW.to_status, OLD.to_status),
        CASE TG_OP WHEN 'INSERT' THEN 'ANGELEGT'
                   WHEN 'UPDATE' THEN 'GEAENDERT'
                   ELSE 'GELOESCHT' END,
        coalesce(NEW.requires_reason, OLD.requires_reason),
        v_user);
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_status_transition_audit
    AFTER INSERT OR UPDATE OR DELETE ON workflow.status_transition
    FOR EACH ROW EXECUTE FUNCTION workflow.log_pipeline_change();

COMMIT;

-- Rückwärtsstrategie: Trigger und Tabellen in umgekehrter Reihenfolge entfernen
-- (DROP TRIGGER trg_status_transition_audit; DROP FUNCTION log_pipeline_change;
-- DROP TABLE pipeline_change; ALTER TABLE status_transition DROP CONSTRAINT …;
-- DROP TABLE status_catalog). Das Änderungsprotokoll wird niemals rückwärts
-- migriert, solange Fachdaten entstanden sind.
