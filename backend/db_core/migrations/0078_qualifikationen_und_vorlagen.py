"""Plantafel Stufe 1 (Welle B): Qualifikationen + Zuweisungs-Vorlagen.

Zwei User-Entscheidungen tragen dieses Schema:

**1) „Lose Gruppen, wechselnd" — KEIN festes Kolonnen-/Team-Modell.**
Der Betrieb fährt nicht in benannten Kolonnen, sondern in wiederkehrenden
Konstellationen. Ein hartes Team-Modell (Bahnen je Kolonne, Zuweisung an das
Team) bildete eine Ordnung ab, die es nicht gibt — und niemand pflegte sie.
Deshalb `workflow.assignment_template`: eine benannte **Personengruppe als
VORSCHLAG**. Der Termin-Dialog übernimmt sie auf Knopfdruck; die Zuweisung
bleibt danach eine gewöhnliche `job_assignment` an Einzelpersonen. Die Vorlage
**bindet nichts** — wer abweicht, weicht ab, und kein Trigger hält ihn auf.

**2) „Dynamisch halten, wir müssen sehr flexibel bleiben" — Qualifikationen sind
STAMMDATEN, nicht Code.**
Der User nannte Gewerke (SHK/Elektro), nachweispflichtige Befähigungen
(Gasschein, Kälteschein § 5 ChemKlimaschutzV, Absturzsicherung) UND
Herstellerschulungen (Viessmann, Vaillant) — und bat ausdrücklich um
Flexibilität. Drei fest verdrahtete Arten wären genau das Gegenteil: Jede neue
Schulung bräuchte eine Migration.

Deshalb ist `hr.qualification` ein **frei pflegbarer Katalog**:
- `kind` ist eine **Gruppierung als DATENWERT ohne CHECK** — der Betrieb legt
  „GEWERK", „ZERTIFIKAT", „HERSTELLERSCHULUNG" oder was immer er braucht selbst
  an. Ein Enum im Code hätte genau die Starre erzeugt, die der User nicht will.
- `expires` sagt, ob die Zuordnung ein **Gültig-bis** verlangt (ein Gasschein
  läuft ab, „Geselle SHK" nicht). Das erzwingt der CHECK auf
  `hr.employee_qualification`: `expires = true` → `valid_until` ist Pflicht.

**Der Bedarf hängt an zwei Stellen** und wird VEREINIGT:
- `workflow.appointment_category_qualification` — was ein Termintyp *immer*
  braucht („Wartung Gastherme" → Gasschein).
- `workflow.service_job_qualification` — was DIESER eine Termin zusätzlich
  braucht (Sonderfall am Objekt).

**INVARIANTE: Der Abgleich WARNT, er BLOCKIERT NICHT.** Wie die Doppelbelegung
(Beschluss aus Migration 0025) ist eine fehlende Qualifikation ein Hinweis an den
Disponenten, keine Sperre. Es gibt keinen Trigger, der eine Zuweisung ohne
Nachweis verhindert — sonst stünde der Notdienst am Sonntag vor einem gesperrten
Board, und die Disposition führte ihre Wahrheit wieder auf Papier. Der Service
liefert die Warnung; der Mensch entscheidet.

Alle drei neuen Tabellen erben den Schutzstandard (No-Delete/No-Truncate/Audit).
Die reinen Verknüpfungstabellen (Katalogbedarf, Vorlagenmitglied) sind davon
ausgenommen — sie tragen keine Historie, sondern eine Auswahl, und müssen sich
korrigieren lassen.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ===========================================================================
-- TEIL A — hr.qualification (frei pflegbarer Katalog)
-- ===========================================================================
CREATE TABLE hr.qualification (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        text NOT NULL CHECK (btrim(code) <> ''),
    label       text NOT NULL CHECK (btrim(label) <> ''),
    -- Freie Gruppierung (GEWERK | ZERTIFIKAT | HERSTELLERSCHULUNG | …).
    -- BEWUSST OHNE CHECK: Der Betrieb legt seine Arten selbst an. Ein Enum
    -- verlangte fuer jede neue Schulungsart eine Migration.
    kind        text NULL CHECK (kind IS NULL OR btrim(kind) <> ''),
    description text NULL,
    -- Verlangt die Zuordnung ein Gueltig-bis? (Gasschein ja, Gesellenbrief nein.)
    expires     boolean NOT NULL DEFAULT false,
    active      boolean NOT NULL DEFAULT true,
    sort_order  integer NOT NULL DEFAULT 0,
    created_by  uuid NOT NULL REFERENCES security.app_user (id),
    version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Der Code ist der fachliche Schluessel (case-insensitiv eindeutig).
CREATE UNIQUE INDEX uq_qualification_code ON hr.qualification (lower(code));
CREATE INDEX idx_qualification_kind ON hr.qualification (kind) WHERE active;

-- ===========================================================================
-- TEIL B — hr.employee_qualification (wer kann was, bis wann)
-- ===========================================================================
CREATE TABLE hr.employee_qualification (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id      uuid NOT NULL REFERENCES hr.employee (id),
    qualification_id uuid NOT NULL REFERENCES hr.qualification (id),
    valid_from       date NULL,
    -- Pflicht, wenn der Katalog `expires` sagt (Trigger unten). NULL = laeuft nie ab.
    valid_until      date NULL,
    -- Woraus geht der Nachweis hervor (Urkundennummer, Ablage)? KEIN Dateiupload:
    -- ein Zeugnis ist ein Personaldokument und gehoert hinter das hr-Tor, nicht
    -- in die allgemeine Dateiablage.
    evidence_note    text NULL,
    created_by       uuid NOT NULL REFERENCES security.app_user (id),
    version          integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT employee_qualification_zeitraum
        CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from)
);

-- Eine Qualifikation je Mitarbeiter (Verlaengerung = valid_until fortschreiben,
-- nicht eine zweite Zeile anlegen — sonst waere „gueltig?" mehrdeutig).
CREATE UNIQUE INDEX uq_employee_qualification
    ON hr.employee_qualification (employee_id, qualification_id);
CREATE INDEX idx_employee_qualification_ablauf
    ON hr.employee_qualification (valid_until)
    WHERE valid_until IS NOT NULL;

-- `expires` aus dem Katalog physisch durchsetzen: Ein ablaufpflichtiger Nachweis
-- ohne Gueltig-bis waere eine Behauptung ohne Substanz.
--
-- `FOR SHARE` sperrt die Katalogzeile: Sonst gibt es unter READ COMMITTED ein
-- Rennen. T1 stellt die Qualifikation auf `expires = true` (und sieht keine
-- Bestandszeile ohne Gueltig-bis), waehrend T2 zeitgleich genau so eine Zeile
-- anlegt und in seinem Snapshot noch `expires = false` liest. Beide committen -
-- und die verbotene Zeile existiert. Mit FOR SHARE hier und FOR UPDATE im
-- Katalog-Trigger serialisieren sich die beiden Wege gegeneinander.
CREATE FUNCTION hr.enforce_employee_qualification() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_expires boolean;
    v_label   text;
BEGIN
    SELECT expires, label INTO v_expires, v_label
    FROM hr.qualification WHERE id = NEW.qualification_id
    FOR SHARE;

    IF v_expires AND NEW.valid_until IS NULL THEN
        RAISE EXCEPTION
            'Die Qualifikation % ist ablaufpflichtig und verlangt ein Gueltig-bis.',
            v_label
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_employee_qualification_enforce
    BEFORE INSERT OR UPDATE ON hr.employee_qualification
    FOR EACH ROW EXECUTE FUNCTION hr.enforce_employee_qualification();

-- Die Gegenrichtung: Wer eine Qualifikation NACHTRAEGLICH auf ablaufpflichtig
-- stellt, waehrend Nachweise ohne Gueltig-bis daran haengen, macht sie
-- schlagartig regelwidrig - und der Trigger oben stuende beim naechsten
-- Speichern eines unbeteiligten Feldes im Weg, ohne dass jemand verstuende
-- warum. Das gehoert PHYSISCH verhindert, nicht nur im Service (jedes UPDATE
-- aus psql oder kuenftigem Code kaeme sonst daran vorbei).
CREATE FUNCTION hr.enforce_qualification_expires() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_offen integer;
BEGIN
    IF NEW.expires AND NOT OLD.expires THEN
        SELECT count(*) INTO v_offen
        FROM hr.employee_qualification
        WHERE qualification_id = NEW.id AND valid_until IS NULL;

        IF v_offen > 0 THEN
            RAISE EXCEPTION
                '% Nachweis(e) dieser Qualifikation tragen kein Gueltig-bis. Trage dort zuerst ein Ablaufdatum nach.',
                v_offen
                USING ERRCODE = 'P0001';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_qualification_expires
    BEFORE UPDATE ON hr.qualification
    FOR EACH ROW EXECUTE FUNCTION hr.enforce_qualification_expires();

-- ===========================================================================
-- TEIL C — Bedarf: an der Terminkategorie UND am einzelnen Termin
-- ===========================================================================
-- Der wirksame Bedarf ist die VEREINIGUNG beider. Die Kategorie traegt den
-- Regelfall („Wartung Gastherme braucht den Gasschein"), der Termin den
-- Sonderfall („an diesem Objekt zusaetzlich Absturzsicherung").
CREATE TABLE workflow.appointment_category_qualification (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    appointment_category_id uuid NOT NULL
                            REFERENCES workflow.appointment_category (id),
    qualification_id        uuid NOT NULL REFERENCES hr.qualification (id),
    created_by              uuid NOT NULL REFERENCES security.app_user (id),
    created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_appointment_category_qualification
    ON workflow.appointment_category_qualification
       (appointment_category_id, qualification_id);

CREATE TABLE workflow.service_job_qualification (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service_job_id   uuid NOT NULL REFERENCES workflow.service_job (id),
    qualification_id uuid NOT NULL REFERENCES hr.qualification (id),
    created_by       uuid NOT NULL REFERENCES security.app_user (id),
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_service_job_qualification
    ON workflow.service_job_qualification (service_job_id, qualification_id);

-- ===========================================================================
-- TEIL D — workflow.assignment_template (lose Gruppen als VORSCHLAG)
-- ===========================================================================
CREATE TABLE workflow.assignment_template (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL CHECK (btrim(name) <> ''),
    description text NULL,
    active      boolean NOT NULL DEFAULT true,
    sort_order  integer NOT NULL DEFAULT 0,
    created_by  uuid NOT NULL REFERENCES security.app_user (id),
    version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_assignment_template_active_name
    ON workflow.assignment_template (lower(name)) WHERE active;

CREATE TABLE workflow.assignment_template_member (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id uuid NOT NULL REFERENCES workflow.assignment_template (id),
    -- Die Bahn der Plantafel haengt an security.app_user (wie job_assignment),
    -- nicht an hr.employee — eine Vorlage soll auch einen Kollegen ohne
    -- Personalsatz aufnehmen koennen.
    assignee_user_id uuid NOT NULL REFERENCES security.app_user (id),
    -- TECHNICIAN | LEAD (wie workflow.job_assignment).
    role        text NOT NULL DEFAULT 'TECHNICIAN'
                CHECK (role IN ('TECHNICIAN', 'LEAD')),
    created_by  uuid NOT NULL REFERENCES security.app_user (id),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_assignment_template_member
    ON workflow.assignment_template_member (template_id, assignee_user_id);

-- ===========================================================================
-- Schutzstandard (No-Delete/No-Truncate/Audit) fuer die STAMMTABELLEN.
-- ===========================================================================
-- Die Verknuepfungstabellen (Bedarf, Vorlagenmitglied) sind bewusst AUSGENOMMEN:
-- Sie tragen keine Historie, sondern eine Auswahl. Ein falsch gesetzter Haken
-- muss sich wieder loesen lassen, ohne Karteileichen zu hinterlassen.
CREATE TRIGGER trg_qualification_updated_at
    BEFORE UPDATE ON hr.qualification
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_qualification_audit
    AFTER UPDATE ON hr.qualification
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_qualification_no_delete
    BEFORE DELETE ON hr.qualification
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_qualification_no_truncate
    BEFORE TRUNCATE ON hr.qualification
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.qualification FROM PUBLIC;

CREATE TRIGGER trg_employee_qualification_updated_at
    BEFORE UPDATE ON hr.employee_qualification
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_employee_qualification_audit
    AFTER UPDATE ON hr.employee_qualification
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
-- Das DELETE bleibt hier bewusst ERLAUBT (ein falscher Haken ist kein
-- Geschaeftsvorfall, und eine Karteileiche „hat den Gasschein, eigentlich aber
-- nicht" waere gefaehrlicher als die Loeschung) - dann MUSS es aber auditiert
-- werden: Es verschwindet eine Zeile der Personalakte. Ohne diesen Trigger
-- verschwaende sie spurlos (Muster: db/migrations/0017 fuer time_entry).
CREATE TRIGGER trg_employee_qualification_delete_audit
    AFTER DELETE ON hr.employee_qualification
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_employee_qualification_no_truncate
    BEFORE TRUNCATE ON hr.employee_qualification
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON hr.employee_qualification FROM PUBLIC;

-- Die reinen Verknuepfungstabellen (Bedarf, Vorlagenmitglied) duerfen geloescht
-- werden - sie tragen eine Auswahl, keine Historie. TRUNCATE bleibt trotzdem
-- gesperrt: Ein versehentliches TRUNCATE loeschte den kompletten
-- Qualifikationsbedarf des Betriebs auf einen Schlag, und die Plantafel wuerde
-- ab da schweigen, statt zu warnen.
CREATE TRIGGER trg_acq_no_truncate
    BEFORE TRUNCATE ON workflow.appointment_category_qualification
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_sjq_no_truncate
    BEFORE TRUNCATE ON workflow.service_job_qualification
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_atm_no_truncate
    BEFORE TRUNCATE ON workflow.assignment_template_member
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON workflow.appointment_category_qualification FROM PUBLIC;
REVOKE TRUNCATE ON workflow.service_job_qualification FROM PUBLIC;
REVOKE TRUNCATE ON workflow.assignment_template_member FROM PUBLIC;

CREATE TRIGGER trg_assignment_template_updated_at
    BEFORE UPDATE ON workflow.assignment_template
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_assignment_template_audit
    AFTER UPDATE ON workflow.assignment_template
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_assignment_template_no_delete
    BEFORE DELETE ON workflow.assignment_template
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_assignment_template_no_truncate
    BEFORE TRUNCATE ON workflow.assignment_template
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON workflow.assignment_template FROM PUBLIC;

COMMENT ON TABLE hr.qualification IS
    'Frei pflegbarer Qualifikationskatalog. `kind` ist eine Gruppierung als DATENWERT ohne CHECK - der Betrieb legt seine Arten selbst an.';
COMMENT ON TABLE workflow.assignment_template IS
    'Benannte Personengruppe als VORSCHLAG fuer den Termin-Dialog (lose Gruppen, kein Team-Modell). Bindet nichts.';
"""

REVERSE_SQL = r"""
DROP TABLE workflow.assignment_template_member;
DROP TABLE workflow.assignment_template;
DROP TABLE workflow.service_job_qualification;
DROP TABLE workflow.appointment_category_qualification;
DROP TRIGGER trg_employee_qualification_enforce ON hr.employee_qualification;
DROP TABLE hr.employee_qualification;
DROP TRIGGER trg_qualification_expires ON hr.qualification;
DROP TABLE hr.qualification;
DROP FUNCTION hr.enforce_employee_qualification();
DROP FUNCTION hr.enforce_qualification_expires();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0077_termindauer_und_serie"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
