"""Fälligkeiten-Engine — drei Fristenarten unter einem Dach (maintenance.*).

Bisher kannte das Wartungsmodul genau EINE Frist: `maintenance_contract.
next_due_date`. Der Betrieb hat aber drei Arten wiederkehrender Termine, die
alle dieselbe Frage beantworten („Was steht an?"), und deshalb dieselbe Liste,
dieselben Aktionen und denselben Nachweis brauchen:

  1. WARTUNG        — aus einem Wartungsvertrag (existiert seit 0016).
  2. PRUEFUNG       — wiederkehrende Prüfung an einer Liegenschaft/Anlage, OHNE
                      Wartungsvertrag (neu: maintenance.inspection_type +
                      maintenance.inspection).
  3. GEWAEHRLEISTUNG — Ablauf der Gewährleistungsfrist eines abgerechneten
                      Auftrags (neu: maintenance.warranty).

Kern ist `maintenance.due_item`: EIN Fälligkeitsmodell für alle drei Arten.

## Idempotenz ist eine physische Eigenschaft, keine Absprache

Der Scheduler darf beliebig oft laufen. Das ist hier nicht „der Code passt
schon auf", sondern über drei partielle UNIQUE-Indizes erzwungen:

    (contract_id,   due_date)   je Vertrag       höchstens EINE Fälligkeit
    (inspection_id, due_date)   je Prüfung       höchstens EINE Fälligkeit
    (warranty_id,   due_date)   je Gewährleistung höchstens EINE Fälligkeit

Die Indizes sind bewusst **statusunabhängig**. Damit gilt automatisch auch die
zweite Kernanforderung: **ein VERWORFENER Eintrag taucht nie wieder auf** — ein
erneuter Lauf kann für dieselbe Fälligkeit schlicht keine zweite Zeile anlegen.
Ein Statusfilter im Index (`WHERE status = 'OFFEN'`) hätte genau das kaputt
gemacht.

## Kein Löschen (GoBD/Audit)

`due_item` erbt den Schutzstandard: kein DELETE, kein TRUNCATE, jede Änderung
wird auditiert. „Verwerfen" ist ein Status mit Pflichtbegründung (CHECK), kein
DELETE. Der Statusautomat OFFEN → ERLEDIGT | VERWORFEN ist final und wird von
einem Trigger physisch erzwungen (kein Zurück, keine Umdeutung im Nachhinein).

## Bewusst KEINE Rechtsauskunft

`maintenance.inspection_type` ist eine **vom Betrieb gepflegte Stammdaten-
tabelle**. Diese Migration legt ein paar gängige SHK-Prüfarten als **Vorschlag**
an (`is_suggestion = true`) — mit Intervallen, die der Betrieb selbst prüfen und
anpassen muss. Es sind ausdrücklich **keine Normtabellen** und keine Zusicherung
von Rechtsverbindlichkeit; TrinkwV/KÜO/DGUV ändern sich, und die konkrete Frist
hängt am Einzelfall (Anlagengröße, Nutzung, Betreiberpflichten). Dasselbe gilt
für die Gewährleistung: `warranty.duration_months` ist **je Auftrag einstellbar**
mit einem konfigurierbaren Default am Firmenprofil — das Produkt schreibt keine
Frist vor und behauptet keine.

## Rechtematrix: neues Modul 'maintenance'

Wartung lief bisher auf dem Modul `workflow` mit (historisch, weil es beim Bau
von 0016 noch kein eigenes Modul gab). Mit drei Fristenarten, Prüf-Stammdaten
und Gewährleistung ist das ein eigener Verantwortungsbereich: die Disposition
darf Fälligkeiten planen, aber nicht jede Rolle darf eine Fälligkeit
**verwerfen** (= die Frist bewusst verstreichen lassen). Deshalb ein eigenes
Modul mit der Aktion STORNIEREN als Tor fürs Verwerfen.

Die Zeilen werden so gesetzt, dass **niemand Zugriff verliert**, den er heute
über `workflow` auf die Wartung hatte.
"""
from django.db import migrations

CREATE_SQL = r"""
-- ---------------------------------------------------------------------------
-- Voraussetzung: Anlagenbezug einer Prüfung soll physisch zur Liegenschaft
-- passen. Dafür braucht property.technical_asset einen zusammengesetzten
-- Kandidatenschlüssel (id, property_id) — reines Zusatz-UNIQUE, keine
-- Verhaltensänderung an der Bestandstabelle.
-- ---------------------------------------------------------------------------
ALTER TABLE property.technical_asset
    ADD CONSTRAINT technical_asset_id_property_key UNIQUE (id, property_id);

-- ---------------------------------------------------------------------------
-- Firmenprofil: Default der Gewährleistungsfrist (konfigurierbar, nicht fix)
-- ---------------------------------------------------------------------------
ALTER TABLE company.company_profile
    ADD COLUMN warranty_default_months integer NOT NULL DEFAULT 60
        CHECK (warranty_default_months > 0 AND warranty_default_months <= 240),
    ADD COLUMN warranty_default_lead_days integer NOT NULL DEFAULT 90
        CHECK (warranty_default_lead_days >= 0 AND warranty_default_lead_days <= 730);

COMMENT ON COLUMN company.company_profile.warranty_default_months IS
    'Voreinstellung der Gewährleistungsfrist in Monaten. Je Auftrag überschreibbar. '
    'Betriebliche Einstellung, keine Rechtsauskunft.';

-- ---------------------------------------------------------------------------
-- maintenance.inspection_type — Prüfart (Stammdaten, vom Betrieb gepflegt)
-- ---------------------------------------------------------------------------
CREATE TABLE maintenance.inspection_type (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name              text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    interval_kind     text NOT NULL CHECK (interval_kind IN
                      ('JAEHRLICH', 'MONATLICH', 'WOECHENTLICH', 'TAGE')),
    interval_days     integer NULL CHECK (interval_days > 0),
    -- Vorlauf: so viele Tage VOR der Fälligkeit erscheint sie in „Was steht an?"
    lead_time_days    integer NOT NULL DEFAULT 30 CHECK (lead_time_days >= 0),
    -- Zuständigkeit als freier Text (Betrieb, Fachfirma, Schornsteinfeger, …).
    -- Bewusst keine Codeliste: das Produkt kennt die Zuständigkeitsordnung nicht.
    responsibility    text NULL,
    notes             text NULL,
    -- Vorschlag aus der Auslieferung (true) vs. selbst angelegt (false). Wird im
    -- UI als „Vorschlag, keine Rechtsauskunft" gekennzeichnet.
    is_suggestion     boolean NOT NULL DEFAULT false,
    is_active         boolean NOT NULL DEFAULT true,
    created_by        uuid NULL REFERENCES security.app_user (id),
    version           integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT inspection_type_days_required
        CHECK (interval_kind <> 'TAGE' OR interval_days IS NOT NULL)
);

CREATE TRIGGER trg_inspection_type_updated_at
    BEFORE UPDATE ON maintenance.inspection_type
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_inspection_type_audit
    AFTER UPDATE ON maintenance.inspection_type
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_inspection_type_no_delete
    BEFORE DELETE ON maintenance.inspection_type
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_inspection_type_no_truncate
    BEFORE TRUNCATE ON maintenance.inspection_type
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON maintenance.inspection_type FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- maintenance.inspection — konkrete wiederkehrende Prüfung an Objekt/Anlage
--
-- Die Intervall-/Vorlauffelder werden bei der Anlage aus der Prüfart KOPIERT
-- (nicht referenziert): eine spätere Änderung der Prüfart darf den Plan einer
-- laufenden Prüfung nicht rückwirkend verschieben. Gleiche Haltung wie bei der
-- Belegposition (Kopie, kein Verweis).
-- ---------------------------------------------------------------------------
CREATE TABLE maintenance.inspection (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_type_id uuid NOT NULL REFERENCES maintenance.inspection_type (id),
    property_id       uuid NOT NULL REFERENCES property.property (id),
    asset_id          uuid NULL,
    name              text NOT NULL CHECK (btrim(name) <> ''),
    status            text NOT NULL DEFAULT 'AKTIV'
                      CHECK (status IN ('AKTIV', 'INAKTIV', 'ARCHIVIERT')),
    start_date        date NOT NULL,
    interval_kind     text NOT NULL CHECK (interval_kind IN
                      ('JAEHRLICH', 'MONATLICH', 'WOECHENTLICH', 'TAGE')),
    interval_days     integer NULL CHECK (interval_days > 0),
    lead_time_days    integer NOT NULL DEFAULT 30 CHECK (lead_time_days >= 0),
    next_due_date     date NULL,
    responsibility    text NULL,
    party_id          uuid NULL REFERENCES identity.party (id),
    notes             text NULL,
    created_by        uuid NOT NULL REFERENCES security.app_user (id),
    version           integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT inspection_days_required
        CHECK (interval_kind <> 'TAGE' OR interval_days IS NOT NULL),
    -- Die Anlage muss zur Liegenschaft der Prüfung gehören (physisch, nicht nur
    -- im Service). Möglich durch das UNIQUE (id, property_id) oben.
    CONSTRAINT inspection_asset_fk
        FOREIGN KEY (asset_id, property_id)
        REFERENCES property.technical_asset (id, property_id)
);

CREATE INDEX idx_inspection_property ON maintenance.inspection (property_id);
CREATE INDEX idx_inspection_next_due ON maintenance.inspection (next_due_date)
    WHERE next_due_date IS NOT NULL AND status = 'AKTIV';

-- Statusautomat: identisch zum Wartungsvertrag (AKTIV<->INAKTIV, INAKTIV->ARCHIVIERT).
CREATE FUNCTION maintenance.enforce_inspection_status() RETURNS trigger
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
            'Prüfung %: Statuswechsel % -> % ist nicht zulässig '
            '(nur AKTIV<->INAKTIV, INAKTIV->ARCHIVIERT)',
            NEW.name, OLD.status, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_inspection_updated_at
    BEFORE UPDATE ON maintenance.inspection
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_inspection_status
    BEFORE UPDATE OF status ON maintenance.inspection
    FOR EACH ROW EXECUTE FUNCTION maintenance.enforce_inspection_status();
CREATE TRIGGER trg_inspection_audit
    AFTER UPDATE ON maintenance.inspection
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_inspection_no_delete
    BEFORE DELETE ON maintenance.inspection
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_inspection_no_truncate
    BEFORE TRUNCATE ON maintenance.inspection
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON maintenance.inspection FROM PUBLIC;

CREATE TRIGGER trg_inspection_no_merged
    BEFORE INSERT OR UPDATE ON maintenance.inspection
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');

-- ---------------------------------------------------------------------------
-- maintenance.warranty — Gewährleistungsfrist eines Auftrags
--
-- Genau EINE Gewährleistung je Auftrag (UNIQUE). `basis` ist ein LABEL, keine
-- Rechtsfolge: das Produkt leitet aus 'BGB'/'VOB' keine Frist ab, es merkt sich
-- nur, was der Betrieb vereinbart hat. `duration_months` ist die Wahrheit.
-- ---------------------------------------------------------------------------
CREATE TABLE maintenance.warranty (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id     uuid NOT NULL UNIQUE REFERENCES workflow.work_order (id),
    property_id       uuid NOT NULL REFERENCES property.property (id),
    party_id          uuid NULL REFERENCES identity.party (id),
    basis             text NOT NULL DEFAULT 'BGB'
                      CHECK (basis IN ('BGB', 'VOB', 'INDIVIDUELL')),
    start_date        date NOT NULL,
    duration_months   integer NOT NULL CHECK (duration_months > 0
                                              AND duration_months <= 240),
    end_date          date NOT NULL,
    lead_time_days    integer NOT NULL DEFAULT 90 CHECK (lead_time_days >= 0),
    -- Merkposten für den Vertrieb: eine wartungsbedürftige Anlage OHNE
    -- Wartungsvertrag kann eine kürzere Frist haben. Reiner Hinweis-Schalter,
    -- keine automatische Fristverkürzung.
    is_machinery      boolean NOT NULL DEFAULT false,
    status            text NOT NULL DEFAULT 'AKTIV'
                      CHECK (status IN ('AKTIV', 'ARCHIVIERT')),
    notes             text NULL,
    created_by        uuid NOT NULL REFERENCES security.app_user (id),
    version           integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT warranty_end_after_start CHECK (end_date > start_date)
);

CREATE INDEX idx_warranty_property ON maintenance.warranty (property_id);
CREATE INDEX idx_warranty_end ON maintenance.warranty (end_date)
    WHERE status = 'AKTIV';

CREATE TRIGGER trg_warranty_updated_at
    BEFORE UPDATE ON maintenance.warranty
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_warranty_audit
    AFTER UPDATE ON maintenance.warranty
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_warranty_no_delete
    BEFORE DELETE ON maintenance.warranty
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_warranty_no_truncate
    BEFORE TRUNCATE ON maintenance.warranty
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON maintenance.warranty FROM PUBLIC;

CREATE TRIGGER trg_warranty_no_merged
    BEFORE INSERT OR UPDATE ON maintenance.warranty
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');

-- ---------------------------------------------------------------------------
-- maintenance.due_item — DAS Fälligkeitsmodell (alle drei Arten)
-- ---------------------------------------------------------------------------
CREATE TABLE maintenance.due_item (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind               text NOT NULL CHECK (kind IN
                       ('WARTUNG', 'PRUEFUNG', 'GEWAEHRLEISTUNG')),
    contract_id        uuid NULL REFERENCES maintenance.maintenance_contract (id),
    inspection_id      uuid NULL REFERENCES maintenance.inspection (id),
    warranty_id        uuid NULL REFERENCES maintenance.warranty (id),
    -- Denormalisiert aus der Quelle: die Ansicht filtert nach Objekt, ohne drei
    -- Joins zu raten. Wird vom Service beim Anlegen gesetzt und nie geändert.
    property_id        uuid NULL REFERENCES property.property (id),
    title              text NOT NULL CHECK (btrim(title) <> ''),
    due_date           date NOT NULL,
    -- Vorlauf: ab (due_date - lead_time_days) ist die Fälligkeit „sichtbar".
    lead_time_days     integer NOT NULL DEFAULT 0 CHECK (lead_time_days >= 0),
    status             text NOT NULL DEFAULT 'OFFEN'
                       CHECK (status IN ('OFFEN', 'ERLEDIGT', 'VERWORFEN')),
    -- Was daraus wurde (Termin/Auftrag/Angebot/Aufgabe/Projekt) bzw. warum nicht.
    result_object_type text NULL,
    result_object_id   uuid NULL,
    resolution_note    text NULL,
    resolved_at        timestamptz NULL,
    resolved_by        uuid NULL REFERENCES security.app_user (id),
    created_by         uuid NULL REFERENCES security.app_user (id),
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),

    -- Genau EIN Anker, und er passt zur Art. Ohne das ließe sich eine
    -- „Gewährleistung" an einen Wartungsvertrag hängen.
    CONSTRAINT due_item_anchor CHECK (
        (kind = 'WARTUNG'
             AND contract_id IS NOT NULL
             AND inspection_id IS NULL AND warranty_id IS NULL)
     OR (kind = 'PRUEFUNG'
             AND inspection_id IS NOT NULL
             AND contract_id IS NULL AND warranty_id IS NULL)
     OR (kind = 'GEWAEHRLEISTUNG'
             AND warranty_id IS NOT NULL
             AND contract_id IS NULL AND inspection_id IS NULL)
    ),
    -- Verwerfen ist begründungspflichtig (GoBD: kein stilles Wegwischen).
    CONSTRAINT due_item_verwerfen_begruendet CHECK (
        status <> 'VERWORFEN'
        OR (resolution_note IS NOT NULL AND btrim(resolution_note) <> '')
    ),
    -- Erledigt/Verworfen tragen immer Zeitpunkt und Person.
    CONSTRAINT due_item_abschluss_vollstaendig CHECK (
        status = 'OFFEN'
        OR (resolved_at IS NOT NULL AND resolved_by IS NOT NULL)
    ),
    CONSTRAINT due_item_offen_ohne_abschluss CHECK (
        status <> 'OFFEN'
        OR (resolved_at IS NULL AND resolved_by IS NULL
            AND result_object_id IS NULL)
    )
);

-- IDEMPOTENZ (Kernanforderung): je Quelle und Fälligkeitsdatum höchstens EINE
-- Zeile — statusunabhängig. Damit erzeugt ein zweiter Scheduler-Lauf keine
-- Dublette, UND ein verworfener Eintrag kann nicht wieder auferstehen.
CREATE UNIQUE INDEX uq_due_item_contract
    ON maintenance.due_item (contract_id, due_date) WHERE contract_id IS NOT NULL;
CREATE UNIQUE INDEX uq_due_item_inspection
    ON maintenance.due_item (inspection_id, due_date) WHERE inspection_id IS NOT NULL;
CREATE UNIQUE INDEX uq_due_item_warranty
    ON maintenance.due_item (warranty_id, due_date) WHERE warranty_id IS NOT NULL;

CREATE INDEX idx_due_item_offen ON maintenance.due_item (due_date)
    WHERE status = 'OFFEN';
CREATE INDEX idx_due_item_property ON maintenance.due_item (property_id);

-- Statusautomat: OFFEN -> ERLEDIGT | VERWORFEN, beide final. Kein Rücksprung,
-- keine Umdeutung (ERLEDIGT -> VERWORFEN wäre eine nachträgliche Umschreibung
-- der Historie).
CREATE FUNCTION maintenance.enforce_due_item_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;
    IF OLD.status <> 'OFFEN' THEN
        RAISE EXCEPTION
            'Fälligkeit "%" ist bereits % — ein weiterer Statuswechsel (-> %) '
            'ist nicht zulässig.', OLD.title, OLD.status, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    IF NEW.status NOT IN ('ERLEDIGT', 'VERWORFEN') THEN
        RAISE EXCEPTION
            'Fälligkeit "%": Statuswechsel OFFEN -> % ist nicht zulässig.',
            OLD.title, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

-- Anker, Art und Fälligkeitsdatum sind nach dem INSERT unveränderlich: sonst
-- ließe sich eine erledigte Fälligkeit auf ein anderes Datum umhängen und die
-- Idempotenz-Indizes wären wertlos. property_id und lead_time_days gehören
-- dazu: beide sind SCHNAPPSCHÜSSE der Quelle zum Zeitpunkt der Erzeugung (die
-- Ansicht filtert nach Objekt, der Vorlauf begründet die Sichtbarkeit) — eine
-- nachträgliche Änderung schriebe die Vergangenheit um.
CREATE FUNCTION maintenance.protect_due_item_anchor() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.contract_id IS DISTINCT FROM OLD.contract_id
       OR NEW.inspection_id IS DISTINCT FROM OLD.inspection_id
       OR NEW.warranty_id IS DISTINCT FROM OLD.warranty_id
       OR NEW.property_id IS DISTINCT FROM OLD.property_id
       OR NEW.due_date IS DISTINCT FROM OLD.due_date
       OR NEW.lead_time_days IS DISTINCT FROM OLD.lead_time_days THEN
        RAISE EXCEPTION
            'Fälligkeit "%": Art, Bezug, Liegenschaft, Fälligkeitsdatum und '
            'Vorlauf sind unveränderlich.',
            OLD.title
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_due_item_updated_at
    BEFORE UPDATE ON maintenance.due_item
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_due_item_anchor
    BEFORE UPDATE ON maintenance.due_item
    FOR EACH ROW EXECUTE FUNCTION maintenance.protect_due_item_anchor();
CREATE TRIGGER trg_due_item_status
    BEFORE UPDATE OF status ON maintenance.due_item
    FOR EACH ROW EXECUTE FUNCTION maintenance.enforce_due_item_status();
CREATE TRIGGER trg_due_item_audit
    AFTER UPDATE ON maintenance.due_item
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_due_item_no_delete
    BEFORE DELETE ON maintenance.due_item
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_due_item_no_truncate
    BEFORE TRUNCATE ON maintenance.due_item
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON maintenance.due_item FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- maintenance_event: eine Wartung kann jetzt auch VON HAND erledigt werden
--
-- Bisher entstand ein Event nur über die Vollautomatik (wartung.trigger_action),
-- und deren Aktionen waren exakt die vier due_action-Werte des Vertrags. Erledigt
-- ein Mensch dieselbe Wartung in der Fälligkeiten-Ansicht, WÄHLT er das
-- Folgeobjekt — und die Auslöse-Historie des Vertrags muss das genauso nachweisen
-- (sonst gäbe es zwei Wahrheiten: die Fälligkeit ist erledigt, der Vertrag weiß
-- nichts davon). Dafür braucht `action` die zusätzlichen Ausgänge der Ansicht:
--   TERMIN / ANGEBOT   Folgeobjekte, die die Vollautomatik nicht kennt
--   VERMERK            erledigt ohne Folgeobjekt (Vermerk ist Pflicht)
--   VERWORFEN          Frist bewusst verstreichen lassen (Recht STORNIEREN)
-- Die Tabelle ist append-only; Bestandszeilen behalten ihre Werte.
-- ---------------------------------------------------------------------------
ALTER TABLE maintenance.maintenance_event
    DROP CONSTRAINT maintenance_event_action_check;
ALTER TABLE maintenance.maintenance_event
    ADD CONSTRAINT maintenance_event_action_check CHECK (action IN
        ('PROJEKT', 'AUFTRAG', 'AUFGABE', 'BENACHRICHTIGUNG',
         'TERMIN', 'ANGEBOT', 'VERMERK', 'VERWORFEN'));

-- ---------------------------------------------------------------------------
-- Prüfart-VORSCHLÄGE (kein Normkatalog, keine Rechtsauskunft)
--
-- Diese Zeilen sind ein Startpunkt, damit die Ansicht nicht leer ist. Intervall
-- und Zuständigkeit sind vom Betrieb zu prüfen und anzupassen; sie sind je
-- Objekt/Anlage ohnehin unterschiedlich. is_suggestion = true kennzeichnet sie
-- im UI. Sie lassen sich deaktivieren (is_active = false), aber nicht löschen.
-- ---------------------------------------------------------------------------
INSERT INTO maintenance.inspection_type
    (name, interval_kind, interval_days, lead_time_days, responsibility,
     is_suggestion, notes)
VALUES
    ('Trinkwasser: Legionellenprüfung', 'JAEHRLICH', NULL, 60,
     'Zugelassenes Untersuchungslabor / Probenehmer', true,
     'Vorschlag. Das zutreffende Intervall (u. a. jährlich oder dreijährlich) und '
     'die Pflicht überhaupt hängen an Anlage und Nutzung — bitte selbst prüfen. '
     'Keine Rechtsauskunft.'),
    ('Schornsteinfeger / Feuerstättenschau', 'JAEHRLICH', NULL, 30,
     'Bevollmächtigter Bezirksschornsteinfeger', true,
     'Vorschlag. Termine und Intervalle setzt der Feuerstättenbescheid. '
     'Keine Rechtsauskunft.'),
    ('Rückflussverhinderer prüfen', 'JAEHRLICH', NULL, 30,
     'Fachbetrieb', true,
     'Vorschlag. Intervall selbst festlegen. Keine Rechtsauskunft.'),
    ('Sicherheitsventil prüfen', 'JAEHRLICH', NULL, 30,
     'Fachbetrieb', true,
     'Vorschlag. Intervall selbst festlegen. Keine Rechtsauskunft.'),
    ('Rauchwarnmelder prüfen', 'JAEHRLICH', NULL, 45,
     'Betrieb / beauftragter Dienstleister', true,
     'Vorschlag. Landesrecht und Herstellerangaben beachten. Keine Rechtsauskunft.'),
    ('Druckbehälter: wiederkehrende Prüfung', 'JAEHRLICH', NULL, 90,
     'Zugelassene Überwachungsstelle', true,
     'Vorschlag. Prüffristen ergeben sich aus der Gefährdungsbeurteilung '
     '(Anlagengröße/Druck). Keine Rechtsauskunft.');

-- ---------------------------------------------------------------------------
-- Rechtematrix: neues Modul 'maintenance'
--
-- Aktionen im Modul:
--   LESEN       Fälligkeiten/Verträge/Prüfungen/Gewährleistungen sehen
--   ANLEGEN     Vertrag, Prüfart, Prüfung, Gewährleistung anlegen
--   AENDERN     Status, Fälligkeit ERLEDIGEN (Folgeobjekt erzeugen), Fristen ändern
--   STORNIEREN  Fälligkeit VERWERFEN (Frist bewusst verstreichen lassen)
--   EXPORTIEREN Listen exportieren
--   FREIGEBEN/VERSENDEN/LOESCHEN  aktuell ohne Endpunkt (LOESCHEN nie: GoBD)
--
-- Belegung (begründet):
--   ADMINISTRATION / GESCHAEFTSFUEHRUNG  voll außer LOESCHEN (Historienschutz)
--   TECHNISCHE_LEITUNG                   voll außer LOESCHEN — sie verantwortet
--                                        Prüffristen und Gewährleistung
--   DISPOSITION                          LESEN/ANLEGEN/AENDERN/EXPORTIEREN, aber
--                                        KEIN STORNIEREN: eine Prüffrist bewusst
--                                        verfallen zu lassen ist keine Dispo-
--                                        Entscheidung
--   BUCHHALTUNG / NUR_LESEN              nur LESEN
--   MONTEUR                              kein Zugriff (Fälligkeitsplanung ist
--                                        kein Monteurs-Arbeitsbereich; er sieht
--                                        das Ergebnis als Einsatz/Aufgabe)
--
-- Damit verliert niemand einen Zugriff, den er heute über 'workflow' auf die
-- Wartungsverträge hatte (MONTEUR hatte dort row_scope EIGENE → fail-closed 403).
-- ---------------------------------------------------------------------------
ALTER TABLE security.role_permission DROP CONSTRAINT role_permission_module_check;
ALTER TABLE security.role_permission ADD CONSTRAINT role_permission_module_check
    CHECK (module IN ('identity', 'property', 'management', 'tenure', 'billing',
                      'workflow', 'invoicing', 'pricing', 'content', 'security',
                      'ai', 'hr', 'company', 'accounting', 'maintenance'));

INSERT INTO security.role_permission (role_code, module, action, allowed, row_scope)
SELECT r.code, 'maintenance', a.action,
       CASE
           WHEN a.action = 'LOESCHEN' THEN false          -- GoBD: niemand löscht
           WHEN r.code IN ('ADMINISTRATION', 'GESCHAEFTSFUEHRUNG',
                           'TECHNISCHE_LEITUNG') THEN true
           WHEN r.code = 'DISPOSITION'
                AND a.action IN ('LESEN', 'ANLEGEN', 'AENDERN', 'EXPORTIEREN')
                THEN true
           WHEN a.action = 'LESEN'
                AND r.code IN ('BUCHHALTUNG', 'NUR_LESEN') THEN true
           ELSE false                                     -- MONTEUR: kein Zugriff
       END,
       'ALLE'
FROM security.role r
CROSS JOIN (VALUES ('LESEN'), ('ANLEGEN'), ('AENDERN'), ('FREIGEBEN'), ('VERSENDEN'),
                   ('STORNIEREN'), ('EXPORTIEREN'), ('LOESCHEN')) AS a(action);
"""

DROP_SQL = r"""
DELETE FROM security.role_permission WHERE module = 'maintenance';
ALTER TABLE security.role_permission DROP CONSTRAINT role_permission_module_check;
ALTER TABLE security.role_permission ADD CONSTRAINT role_permission_module_check
    CHECK (module IN ('identity', 'property', 'management', 'tenure', 'billing',
                      'workflow', 'invoicing', 'pricing', 'content', 'security',
                      'ai', 'hr', 'company', 'accounting'));

-- maintenance_event ist append-only: die neuen Aktionen lassen sich nicht
-- wegräumen. Existiert bereits ein Event mit TERMIN/ANGEBOT/VERMERK/VERWORFEN,
-- scheitert das ADD CONSTRAINT — genau richtig: das reverse_sql gilt nur,
-- solange keine Fachdaten entstanden sind (db/README.md).
ALTER TABLE maintenance.maintenance_event
    DROP CONSTRAINT maintenance_event_action_check;
ALTER TABLE maintenance.maintenance_event
    ADD CONSTRAINT maintenance_event_action_check CHECK (action IN
        ('PROJEKT', 'AUFTRAG', 'AUFGABE', 'BENACHRICHTIGUNG'));

DROP TABLE IF EXISTS maintenance.due_item;
DROP FUNCTION IF EXISTS maintenance.enforce_due_item_status();
DROP FUNCTION IF EXISTS maintenance.protect_due_item_anchor();
DROP TABLE IF EXISTS maintenance.warranty;
DROP TABLE IF EXISTS maintenance.inspection;
DROP FUNCTION IF EXISTS maintenance.enforce_inspection_status();
DROP TABLE IF EXISTS maintenance.inspection_type;

ALTER TABLE company.company_profile
    DROP COLUMN warranty_default_months,
    DROP COLUMN warranty_default_lead_days;

ALTER TABLE property.technical_asset
    DROP CONSTRAINT technical_asset_id_property_key;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0068_pausenregel_feiertage"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
