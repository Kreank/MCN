"""Planung: Terminkategorien (workflow.appointment_category) + Ressourcen
(neues Fachschema resource.*) und ihre n:m-Zuordnung zum Einsatz.

Hand-SQL nach db/README.md: neue Fachtabellen entstehen als Django-Migration mit
RunSQL und erben den Schutzstandard (updated_at/Audit/No-Delete/No-Truncate/
REVOKE). Muster: 0016_maintenance_wartung.py, 0019_hr_personal.py.

Migrationsnummer: 0023/0024 sind vom parallelen Firmenprofil-Slice belegt; diese
Migration ist die nächste freie (0025) und hängt an 0024.

--------------------------------------------------------------------------------
TEIL A — Terminkategorie: SCHEMA-ENTSCHEIDUNG `workflow.appointment_category`
--------------------------------------------------------------------------------
Die Terminkategorie ist eine schlanke Codeliste/Lookup, die unmittelbar am
Einsatz (workflow.service_job) hängt und dessen Darstellung (Farbe) im Kalender/
auf der Plantafel steuert. Sie hat KEINEN eigenen Lebenszyklus jenseits des
Planungskontexts — anders als hr.* (arbeitsrechtliche Beziehung) oder
maintenance.* (Vertrag mit Fälligkeit). Ein eigenes Schema wäre Overhead; die
Kategorie gehört fachlich in `workflow`, direkt neben service_job.

FARBE — ENTSCHEIDUNG: geschlossene Codeliste von Token-Bezeichnern, NICHT Hex.
Das Design (CLAUDE.md) verbietet freie Hex-Werte im UI und verlangt WCAG-Kontrast.
Deshalb speichert `color_token` nur einen von acht festen Bezeichnern; das
Frontend bildet ihn auf ein geprüftes Farbschema ab (dekorativer Farbpunkt +
IMMER den Kategorienamen als Text — Status nie nur über Farbe). Freie Hex-Werte
sind so physisch (CHECK) ausgeschlossen.

service_job bekommt eine OPTIONALE FK-Spalte auf die Kategorie (ALTER TABLE).

--------------------------------------------------------------------------------
TEIL B — Ressourcen: SCHEMA-ENTSCHEIDUNG neues Schema `resource.*`
--------------------------------------------------------------------------------
Ressourcen (Fahrzeuge/Geräte/Räume) sind planbare Betriebsmittel — eigenständige
Stammdaten mit eigenem Lebenszyklus, unabhängig von einem einzelnen Einsatz. Das
ist genau die Art Domäne, die (wie hr, maintenance) ein eigenes Schema verdient;
die Roadmap (06-planung) nennt `resource` ausdrücklich als Kandidaten. Der
PERMISSION-MODUL bleibt trotzdem `workflow` (Planungsdaten) — DB-Schema-Name und
Rechtematrix-Modul sind bewusst entkoppelt: `workflow` deckt Planung bereits ab,
also KEIN neues Rechtematrix-Modul nötig (kein 0021/0024-Pendant hier).

resource.resource      — Betriebsmittel: eigene Nummer (kein Beleg → eigene
                         Sequenz, Muster hr.employee_number), Typ-Codeliste,
                         Statusautomat AKTIV<->INAKTIV->ARCHIVIERT (final).
resource.job_resource  — n:m Einsatz <-> Ressource, höchstens einmal je
                         (Einsatz, Ressource) (UNIQUE).

DOPPELBELEGUNG (EXCLUDE) — ENTSCHEIDUNG: KEIN EXCLUDE, bewusst als offene
Invariante gemeldet. Begründung:
  1. Der maßgebliche Zeitraum liegt auf workflow.service_job und ist dort
     NULLABLE: scheduled_start/scheduled_end sind beide NULL-fähig (UNGEPLANT
     hat gar keinen Zeitraum; selbst bei gesetztem Start bleibt das Ende
     nullable). Eine EXCLUDE-Constraint kann nicht über eine Spalte einer
     ANDEREN Tabelle greifen — man müsste den Zeitraum nach job_resource
     denormalisieren und per Trigger aus service_job synchron halten. Das ist
     genau die „halbgare Regel", die die Aufgabe zu vermeiden verlangt, und sie
     würde bei NULL-Rändern (die häufigsten Konfliktkandidaten) still gar nicht
     greifen.
  2. Die Roadmap dokumentiert Doppelbuchung ausdrücklich als „bewusst weich"
     (Hero-Parität, keine DB-Constraint) — eine harte Sperre widerspräche dem.
Physische Doppelbelegung einer Ressource ist damit auf DB-Ebene NICHT
verhindert. Der Service liefert bei der Zuordnung einen nicht-blockierenden
Warnhinweis, wenn sich bekannte Zeitfenster überlappen (siehe services/planung).
"""
from django.db import migrations

CREATE_SQL = r"""
-- ===========================================================================
-- TEIL A — workflow.appointment_category (Terminkategorie)
-- ===========================================================================
CREATE TABLE workflow.appointment_category (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL CHECK (btrim(name) <> ''),
    description   text NULL,
    -- Farbe als geschlossene Codeliste (Token), NICHT als freier Hex-Wert.
    color_token   text NOT NULL DEFAULT 'NAVY'
                  CHECK (color_token IN ('NAVY', 'ORANGE', 'SAGE', 'AMBER',
                                         'TEAL', 'PLUM', 'ROSE', 'SLATE')),
    -- Archivieren statt Löschen (GoBD/Hero): archivierte Kategorien stehen für
    -- neue Termine nicht mehr zur Wahl, bestehende behalten sie.
    status        text NOT NULL DEFAULT 'AKTIV'
                  CHECK (status IN ('AKTIV', 'ARCHIVIERT')),
    sort_order    integer NOT NULL DEFAULT 0,
    created_by    uuid NOT NULL REFERENCES security.app_user (id),
    version       integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Aktive Kategorienamen eindeutig (case-insensitiv); archivierte blockieren die
-- Neuvergabe eines Namens nicht.
CREATE UNIQUE INDEX uq_appointment_category_active_name
    ON workflow.appointment_category (lower(name))
    WHERE status = 'AKTIV';
CREATE INDEX idx_appointment_category_status
    ON workflow.appointment_category (status);

-- Statusautomat: nur AKTIV -> ARCHIVIERT (final; kein Reaktivieren).
CREATE FUNCTION workflow.enforce_appointment_category_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;
    IF NOT (OLD.status = 'AKTIV' AND NEW.status = 'ARCHIVIERT') THEN
        RAISE EXCEPTION
            'Terminkategorie %: Statuswechsel % -> % ist nicht zulässig '
            '(nur AKTIV -> ARCHIVIERT)', NEW.id, OLD.status, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_appointment_category_updated_at
    BEFORE UPDATE ON workflow.appointment_category
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_appointment_category_status
    BEFORE UPDATE OF status ON workflow.appointment_category
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_appointment_category_status();
CREATE TRIGGER trg_appointment_category_audit
    AFTER UPDATE ON workflow.appointment_category
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_appointment_category_no_delete
    BEFORE DELETE ON workflow.appointment_category
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_appointment_category_no_truncate
    BEFORE TRUNCATE ON workflow.appointment_category
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON workflow.appointment_category FROM PUBLIC;

-- Optionale Kategorie-Zuordnung am Einsatz.
ALTER TABLE workflow.service_job
    ADD COLUMN appointment_category_id uuid NULL
    REFERENCES workflow.appointment_category (id);
CREATE INDEX idx_service_job_category
    ON workflow.service_job (appointment_category_id)
    WHERE appointment_category_id IS NOT NULL;

-- ===========================================================================
-- TEIL B — Schema resource.* (Ressourcen)
-- ===========================================================================
CREATE SCHEMA resource;

-- Ressourcennummer: eigene Sequenz, kein workflow-Belegkreis (kein Beleg).
CREATE SEQUENCE resource.resource_number_seq;

CREATE TABLE resource.resource (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_number  text NOT NULL UNIQUE
                     DEFAULT ('RES-' || lpad(nextval('resource.resource_number_seq')::text, 5, '0'))
                     CHECK (resource_number ~ '^RES-[0-9]{5,}$'),
    name             text NOT NULL CHECK (btrim(name) <> ''),
    resource_type    text NOT NULL CHECK (resource_type IN
                     ('FAHRZEUG', 'GERAET', 'RAUM', 'SONSTIGE')),
    status           text NOT NULL DEFAULT 'AKTIV'
                     CHECK (status IN ('AKTIV', 'INAKTIV', 'ARCHIVIERT')),
    notes            text NULL,
    created_by       uuid NOT NULL REFERENCES security.app_user (id),
    version          integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_resource_status ON resource.resource (status);
CREATE INDEX idx_resource_type ON resource.resource (resource_type);

-- Statusautomat: AKTIV<->INAKTIV, INAKTIV->ARCHIVIERT (final). Muster maintenance.
CREATE FUNCTION resource.enforce_resource_status() RETURNS trigger
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
            'Ressource %: Statuswechsel % -> % ist nicht zulässig '
            '(nur AKTIV<->INAKTIV, INAKTIV->ARCHIVIERT)',
            NEW.resource_number, OLD.status, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_resource_updated_at
    BEFORE UPDATE ON resource.resource
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_resource_status
    BEFORE UPDATE OF status ON resource.resource
    FOR EACH ROW EXECUTE FUNCTION resource.enforce_resource_status();
CREATE TRIGGER trg_resource_audit
    AFTER UPDATE ON resource.resource
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_resource_no_delete
    BEFORE DELETE ON resource.resource
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_resource_no_truncate
    BEFORE TRUNCATE ON resource.resource
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON resource.resource FROM PUBLIC;

-- n:m Einsatz <-> Ressource. KEIN EXCLUDE (offene Invariante, siehe Docstring).
CREATE TABLE resource.job_resource (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_job_id  uuid NOT NULL REFERENCES workflow.service_job (id),
    resource_id     uuid NOT NULL REFERENCES resource.resource (id),
    created_by      uuid NOT NULL REFERENCES security.app_user (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (service_job_id, resource_id)
);

CREATE INDEX idx_job_resource_job ON resource.job_resource (service_job_id);
CREATE INDEX idx_job_resource_resource ON resource.job_resource (resource_id);

-- Zuordnungen dürfen korrigiert (gelöscht) werden, solange der Einsatz nicht
-- abgeschlossen ist — analog workflow.protect_job_assignment (0015).
CREATE FUNCTION resource.protect_job_resource() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status FROM workflow.service_job WHERE id = OLD.service_job_id;
    IF v_status IN ('ABGESCHLOSSEN', 'NACHARBEIT') THEN
        RAISE EXCEPTION
            'Ressourcenzuordnungen des Einsatzes % können nach Abschluss nicht '
            'mehr entfernt werden (Historienschutz F-02)', OLD.service_job_id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN OLD;
END;
$$;

-- Einsatz- und Ressourcenbezug sind unveränderlich (Korrektur = DELETE+INSERT).
CREATE TRIGGER trg_job_resource_immutable
    BEFORE UPDATE ON resource.job_resource
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_job_resource_protect
    BEFORE DELETE ON resource.job_resource
    FOR EACH ROW EXECUTE FUNCTION resource.protect_job_resource();
CREATE TRIGGER trg_job_resource_no_truncate
    BEFORE TRUNCATE ON resource.job_resource
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON resource.job_resource FROM PUBLIC;
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS resource.job_resource;
DROP FUNCTION IF EXISTS resource.protect_job_resource();
DROP TABLE IF EXISTS resource.resource;
DROP FUNCTION IF EXISTS resource.enforce_resource_status();
DROP SEQUENCE IF EXISTS resource.resource_number_seq;
DROP SCHEMA IF EXISTS resource;

ALTER TABLE workflow.service_job DROP COLUMN IF EXISTS appointment_category_id;
DROP TABLE IF EXISTS workflow.appointment_category;
DROP FUNCTION IF EXISTS workflow.enforce_appointment_category_status();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0024_rechtematrix_company_modul"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
