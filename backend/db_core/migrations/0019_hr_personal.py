"""Neues Fachschema hr.* — Personalstamm, Arbeitsvertrag, Abwesenheit, Urlaubsbudget.

Hand-SQL nach db/README.md: neues Fachschema + Tabellen entstehen als
Django-Migration mit RunSQL und erben den Schutzstandard (No-Delete/Audit/
No-Truncate). Muster: maintenance.* (0016).

Grundsatzentscheidung (docs/roadmap/12-mitarbeiter.md, Abschnitt „Offene Punkte"):
eigenes Fachschema `hr` statt Erweiterung von `security`. Begründung: `security`
beantwortet „darf dieser Account etwas?", `hr` beantwortet „welche
arbeitsrechtliche Beziehung besteht zu dieser Person?". Beides hat
unterschiedliche Lebenszyklen — ein ausgetretener Mitarbeiter behält seinen
Personalsatz (GoBD/Nachweis), verliert aber den Account.

Anker:
  * hr.employee.app_user_id  -> security.app_user (1:1, der Login)
  * hr.employee.party_id     -> identity.person (1:1, die Stammdaten/Name/Adresse)
Personendaten werden NICHT dupliziert; `hr` trägt ausschließlich das
Beschäftigungsverhältnis.

Enthalten (Scope-Entscheidung mit dem User):
  * hr.employee            Personalsatz, Statusautomat AKTIV<->INAKTIV->AUSGETRETEN
  * hr.employment_contract Arbeitsvertrag, versioniert, überlappungsfrei je
                           Mitarbeiter (EXCLUDE), Wochentag-Sollstunden-Raster,
                           Urlaubsanspruch/Jahr. Historische Verträge sind
                           physisch unveränderlich (kein rückwirkendes
                           Überschreiben — Hero-Fachregel).
  * hr.absence             Abwesenheitsantrag, Statusautomat
                           ENTWURF -> EINGEREICHT -> GENEHMIGT|ABGELEHNT,
                           ZURUECKGEZOGEN; überlappungsfrei für offene/genehmigte
                           Anträge (EXCLUDE).
  * hr.vacation_budget     Urlaubskonto je Mitarbeiter und Jahr (Anspruch +
                           Übertrag + manuelle Anpassung). Der VERBRAUCH wird
                           nicht gespeichert, sondern aus genehmigten
                           URLAUB-Abwesenheiten abgeleitet (gleiche Konvention
                           wie der offene Betrag in der Buchhaltung).

Bewusst NICHT enthalten (eigene Migration, Entscheidung mit dem User):
Steuerdaten und Bankdaten (IBAN). Sie sind besonders schützenswert
(DSGVO Art. 9/32), `security.four_eyes_action` kennt bereits die Aktion
'BANKDATEN', deren app-seitige Durchsetzung aber an Auth hängt. Ebenso offen:
Zeitkategorien/Pausenregeln/Stundenausgleich — dort ist erst die Abgrenzung zur
bestehenden operativen Zeiterfassung (workflow.time_entry) zu klären.

Kein Belegkreis: die Personalnummer ist kein Beleg (GoBD), daher eine eigene
Sequenz (Muster property.property_number_seq) statt workflow.next_number().
"""
from django.db import migrations

CREATE_SQL = r"""
CREATE SCHEMA hr;

-- Personalnummer: eigene Sequenz, kein workflow-Belegkreis (kein Beleg).
CREATE SEQUENCE hr.employee_number_seq;

-- ---------------------------------------------------------------------------
-- Personalsatz
-- ---------------------------------------------------------------------------
CREATE TABLE hr.employee (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_number  text NOT NULL UNIQUE
                     DEFAULT ('MA-' || lpad(nextval('hr.employee_number_seq')::text, 5, '0'))
                     CHECK (employee_number ~ '^MA-[0-9]{5,}$'),
    -- 1:1 zum Login-Konto; ohne Account kein Personalsatz (Hero: Anlage legt
    -- Benutzer an). ON DELETE bewusst nicht gesetzt — es wird nie gelöscht.
    app_user_id      uuid NOT NULL UNIQUE REFERENCES security.app_user (id),
    -- 1:1 zu den Stammdaten. FK direkt auf identity.person erzwingt, dass hier
    -- eine natürliche Person hängt (party_type='PERSON'), nicht eine Firma.
    party_id         uuid NOT NULL UNIQUE REFERENCES identity.person (party_id),
    wage_group_id    uuid NULL REFERENCES pricing.wage_group (id),
    status           text NOT NULL DEFAULT 'AKTIV'
                     CHECK (status IN ('AKTIV', 'INAKTIV', 'AUSGETRETEN')),
    hired_on         date NOT NULL,
    left_on          date NULL,
    notes            text NULL,
    created_by       uuid NOT NULL REFERENCES security.app_user (id),
    version          integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT employee_left_after_hired
        CHECK (left_on IS NULL OR left_on >= hired_on),
    -- Austritt und Austrittsdatum bedingen einander.
    CONSTRAINT employee_left_on_matches_status
        CHECK ((status = 'AUSGETRETEN') = (left_on IS NOT NULL))
);

CREATE INDEX idx_employee_status ON hr.employee (status);
CREATE INDEX idx_employee_wage_group ON hr.employee (wage_group_id)
    WHERE wage_group_id IS NOT NULL;

-- Statusautomat: AKTIV <-> INAKTIV (z. B. Elternzeit/Ruhen), INAKTIV|AKTIV ->
-- AUSGETRETEN (final; kein Row-Delete, keine Wiedereinstellung auf demselben
-- Satz — Wiedereintritt ist ein neuer Personalsatz).
CREATE FUNCTION hr.enforce_employee_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'AUSGETRETEN' THEN
        RAISE EXCEPTION
            'Mitarbeiter %: AUSGETRETEN ist ein finaler Status und kann nicht '
            'verlassen werden (Wiedereintritt = neuer Personalsatz)',
            NEW.employee_number
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_employee_updated_at
    BEFORE UPDATE ON hr.employee
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_employee_status
    BEFORE UPDATE OF status ON hr.employee
    FOR EACH ROW EXECUTE FUNCTION hr.enforce_employee_status();
CREATE TRIGGER trg_employee_audit
    AFTER UPDATE ON hr.employee
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_employee_no_delete
    BEFORE DELETE ON hr.employee
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_employee_no_truncate
    BEFORE TRUNCATE ON hr.employee
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.employee FROM PUBLIC;

-- Die verknüpfte Person darf nicht zusammengeführt (MERGED) worden sein.
CREATE TRIGGER trg_employee_no_merged
    BEFORE INSERT OR UPDATE ON hr.employee
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');

-- ---------------------------------------------------------------------------
-- Arbeitsvertrag (versioniert)
-- ---------------------------------------------------------------------------
CREATE TABLE hr.employment_contract (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id             uuid NOT NULL REFERENCES hr.employee (id),
    valid_from              date NOT NULL,
    -- letzter Gültigkeitstag, inklusiv; NULL = unbefristet
    valid_to                date NULL,
    -- Wochentag-Sollstunden-Raster (Hero). 0 = kein Arbeitstag; die Summe darf
    -- nicht 0 sein, sonst gäbe es keinen Arbeitstag und kein Urlaubsverbrauch.
    hours_monday            numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_monday    BETWEEN 0 AND 24),
    hours_tuesday           numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_tuesday   BETWEEN 0 AND 24),
    hours_wednesday         numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_wednesday BETWEEN 0 AND 24),
    hours_thursday          numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_thursday  BETWEEN 0 AND 24),
    hours_friday            numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_friday    BETWEEN 0 AND 24),
    hours_saturday          numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_saturday  BETWEEN 0 AND 24),
    hours_sunday            numeric(4,2) NOT NULL DEFAULT 0 CHECK (hours_sunday    BETWEEN 0 AND 24),
    vacation_days_per_year  numeric(5,2) NOT NULL CHECK (vacation_days_per_year BETWEEN 0 AND 366),
    wage_group_id           uuid NULL REFERENCES pricing.wage_group (id),
    status                  text NOT NULL DEFAULT 'AKTIV'
                            CHECK (status IN ('AKTIV', 'GEKUENDIGT')),
    termination_reason      text NULL,
    notes                   text NULL,
    created_by              uuid NOT NULL REFERENCES security.app_user (id),
    version                 integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT contract_valid_to_after_from
        CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT contract_has_working_day
        CHECK (hours_monday + hours_tuesday + hours_wednesday + hours_thursday
             + hours_friday + hours_saturday + hours_sunday > 0),
    -- Kündigung braucht ein Ende und eine Begründung.
    CONSTRAINT contract_termination_complete
        CHECK (status <> 'GEKUENDIGT'
               OR (valid_to IS NOT NULL AND btrim(coalesce(termination_reason, '')) <> '')),
    -- Ein Mitarbeiter hat zu jedem Zeitpunkt höchstens einen Vertrag.
    CONSTRAINT excl_contract_overlap EXCLUDE USING gist (
        employee_id WITH =,
        daterange(valid_from, valid_to, '[]') WITH &&
    )
);

CREATE INDEX idx_contract_employee ON hr.employment_contract (employee_id);

-- Kein rückwirkendes Überschreiben (Hero-Fachregel): an einem bestehenden
-- Vertrag dürfen nur Ende, Status, Kündigungsgrund und Notiz geändert werden.
-- Eine Arbeitszeitänderung erzeugt einen NEUEN Vertrag.
CREATE FUNCTION hr.enforce_contract_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.employee_id            IS DISTINCT FROM OLD.employee_id
    OR NEW.valid_from             IS DISTINCT FROM OLD.valid_from
    OR NEW.hours_monday           IS DISTINCT FROM OLD.hours_monday
    OR NEW.hours_tuesday          IS DISTINCT FROM OLD.hours_tuesday
    OR NEW.hours_wednesday        IS DISTINCT FROM OLD.hours_wednesday
    OR NEW.hours_thursday         IS DISTINCT FROM OLD.hours_thursday
    OR NEW.hours_friday           IS DISTINCT FROM OLD.hours_friday
    OR NEW.hours_saturday         IS DISTINCT FROM OLD.hours_saturday
    OR NEW.hours_sunday           IS DISTINCT FROM OLD.hours_sunday
    OR NEW.vacation_days_per_year IS DISTINCT FROM OLD.vacation_days_per_year
    OR NEW.wage_group_id          IS DISTINCT FROM OLD.wage_group_id
    THEN
        RAISE EXCEPTION
            'Arbeitsvertrag %: Beginn, Sollstunden, Urlaubsanspruch und '
            'Lohngruppe sind unveränderlich — eine Änderung erfordert einen '
            'neuen Vertrag',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
    IF OLD.status = 'GEKUENDIGT' AND NEW.status <> 'GEKUENDIGT' THEN
        RAISE EXCEPTION
            'Arbeitsvertrag %: GEKUENDIGT ist ein finaler Status', OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_contract_updated_at
    BEFORE UPDATE ON hr.employment_contract
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_contract_immutable
    BEFORE UPDATE ON hr.employment_contract
    FOR EACH ROW EXECUTE FUNCTION hr.enforce_contract_immutable();
CREATE TRIGGER trg_contract_audit
    AFTER UPDATE ON hr.employment_contract
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_contract_no_delete
    BEFORE DELETE ON hr.employment_contract
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_contract_no_truncate
    BEFORE TRUNCATE ON hr.employment_contract
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.employment_contract FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Abwesenheit (Antrag mit Statusautomat)
-- ---------------------------------------------------------------------------
CREATE TABLE hr.absence (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id     uuid NOT NULL REFERENCES hr.employee (id),
    absence_type    text NOT NULL CHECK (absence_type IN
                    ('URLAUB', 'KRANKHEIT', 'ELTERNZEIT', 'SONDERURLAUB',
                     'UNBEZAHLT', 'FORTBILDUNG')),
    start_date      date NOT NULL,
    end_date        date NOT NULL,
    -- halbe Tage am Rand des Zeitraums
    half_day_start  boolean NOT NULL DEFAULT false,
    half_day_end    boolean NOT NULL DEFAULT false,
    -- angerechnete Arbeitstage; vom Service aus dem Sollstunden-Raster des zum
    -- Zeitraum gültigen Vertrags berechnet (Tage mit Soll 0 zählen nicht).
    days_count      numeric(5,2) NOT NULL CHECK (days_count > 0),
    status          text NOT NULL DEFAULT 'ENTWURF' CHECK (status IN
                    ('ENTWURF', 'EINGEREICHT', 'GENEHMIGT', 'ABGELEHNT',
                     'ZURUECKGEZOGEN')),
    reason          text NULL,
    decided_by      uuid NULL REFERENCES security.app_user (id),
    decided_at      timestamptz NULL,
    decision_note   text NULL,
    created_by      uuid NOT NULL REFERENCES security.app_user (id),
    version         integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT absence_end_after_start CHECK (end_date >= start_date),
    -- Ein eintägiger Zeitraum kann nur EINE Hälfte sein.
    CONSTRAINT absence_single_day_half
        CHECK (start_date <> end_date OR NOT half_day_end),
    -- Entscheidung und Entscheider bedingen einander.
    CONSTRAINT absence_decision_complete
        CHECK ((status IN ('GENEHMIGT', 'ABGELEHNT'))
               = (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
    -- Ablehnung ist begründungspflichtig.
    CONSTRAINT absence_rejection_needs_note
        CHECK (status <> 'ABGELEHNT' OR btrim(coalesce(decision_note, '')) <> ''),
    -- Offene und genehmigte Abwesenheiten dürfen sich nicht überlappen;
    -- abgelehnte/zurückgezogene blockieren nichts.
    CONSTRAINT excl_absence_overlap EXCLUDE USING gist (
        employee_id WITH =,
        daterange(start_date, end_date, '[]') WITH &&
    ) WHERE (status IN ('ENTWURF', 'EINGEREICHT', 'GENEHMIGT'))
);

CREATE INDEX idx_absence_employee ON hr.absence (employee_id);
CREATE INDEX idx_absence_status ON hr.absence (status);
CREATE INDEX idx_absence_range ON hr.absence (start_date, end_date);

-- Statusautomat: Antrag beginnt als ENTWURF.
CREATE FUNCTION hr.enforce_absence_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;
    IF NOT (
           (OLD.status = 'ENTWURF'     AND NEW.status IN ('EINGEREICHT', 'ZURUECKGEZOGEN'))
        OR (OLD.status = 'EINGEREICHT' AND NEW.status IN ('GENEHMIGT', 'ABGELEHNT', 'ZURUECKGEZOGEN'))
    ) THEN
        RAISE EXCEPTION
            'Abwesenheit %: Statuswechsel % -> % ist nicht zulässig '
            '(ENTWURF -> EINGEREICHT -> GENEHMIGT|ABGELEHNT; '
            'ZURUECKGEZOGEN aus ENTWURF|EINGEREICHT)',
            NEW.id, OLD.status, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_absence_initial_status
    BEFORE INSERT ON hr.absence
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('ENTWURF');
CREATE TRIGGER trg_absence_updated_at
    BEFORE UPDATE ON hr.absence
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_absence_status
    BEFORE UPDATE OF status ON hr.absence
    FOR EACH ROW EXECUTE FUNCTION hr.enforce_absence_status();
CREATE TRIGGER trg_absence_audit
    AFTER UPDATE ON hr.absence
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_absence_no_delete
    BEFORE DELETE ON hr.absence
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_absence_no_truncate
    BEFORE TRUNCATE ON hr.absence
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.absence FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Urlaubskonto je Mitarbeiter und Jahr
-- ---------------------------------------------------------------------------
CREATE TABLE hr.vacation_budget (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id        uuid NOT NULL REFERENCES hr.employee (id),
    year               integer NOT NULL CHECK (year BETWEEN 2000 AND 2100),
    entitlement_days   numeric(5,2) NOT NULL CHECK (entitlement_days >= 0),
    carryover_days     numeric(5,2) NOT NULL DEFAULT 0 CHECK (carryover_days >= 0),
    -- manuelle Korrektur, ausdrücklich auch negativ (Hero)
    adjustment_days    numeric(5,2) NOT NULL DEFAULT 0,
    adjustment_reason  text NULL,
    created_by         uuid NOT NULL REFERENCES security.app_user (id),
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT vacation_budget_unique_year UNIQUE (employee_id, year),
    -- Eine Anpassung ist begründungspflichtig.
    CONSTRAINT vacation_budget_adjustment_needs_reason
        CHECK (adjustment_days = 0 OR btrim(coalesce(adjustment_reason, '')) <> '')
);

CREATE TRIGGER trg_vacation_budget_updated_at
    BEFORE UPDATE ON hr.vacation_budget
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_vacation_budget_audit
    AFTER UPDATE ON hr.vacation_budget
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_vacation_budget_no_delete
    BEFORE DELETE ON hr.vacation_budget
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_vacation_budget_no_truncate
    BEFORE TRUNCATE ON hr.vacation_budget
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.vacation_budget FROM PUBLIC;
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS hr.vacation_budget;
DROP TABLE IF EXISTS hr.absence;
DROP TABLE IF EXISTS hr.employment_contract;
DROP TABLE IF EXISTS hr.employee;
DROP FUNCTION IF EXISTS hr.enforce_absence_status();
DROP FUNCTION IF EXISTS hr.enforce_contract_immutable();
DROP FUNCTION IF EXISTS hr.enforce_employee_status();
DROP SEQUENCE IF EXISTS hr.employee_number_seq;
DROP SCHEMA IF EXISTS hr;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0018_articlesaleprice_articlesupplierreference_and_more"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
