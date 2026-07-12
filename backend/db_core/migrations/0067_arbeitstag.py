"""Arbeitstag (workflow.work_day) — Tagesklammer, Freigabe, Korrekturschloss.

Der gesetzliche Kern der Zeiterfassung.

Rechtsrahmen (kein Nice-to-have)
--------------------------------
* BAG 13.09.2022 – 1 ABR 22/21 (nach EuGH C-55/18): der Arbeitgeber MUSS die
  Arbeitszeit erfassen.
* **§ 17 Abs. 1 MiLoG** gilt fuer Baugewerbe und Gebaeudereinigung/-dienst-
  leistung — also fuer unsere Zielgruppe — und verlangt HEUTE: **Beginn, Ende
  und Dauer** der taeglichen Arbeitszeit, aufgezeichnet **binnen sieben
  Kalendertagen**, **zwei Jahre** aufbewahrt, dem Zoll vorzulegen.
* Die ArbZG-Novelle (im Verfahren) wird elektronische, taggleiche Aufzeichnung
  inkl. Pausen verlangen.

Von-Bis liefert `workflow.time_entry` bereits. Was fehlte: die **tagesgenaue
Klammer** mit Arbeitgeber-Bestaetigung und ein Schloss, das eine spaetere
Aenderung nicht verschweigt. Genau das ist `workflow.work_day`.

Ein Zeitstrahl, zwei Auswertungen
---------------------------------
`time_entry` bleibt die einzige Quelle der Wahrheit. Die arbeitsrechtliche Sicht
entsteht aus Kategorie-Klassifikation (0066: `is_work_time`) + dieser Tages-
klammer + dem Soll-Vergleich im Service. Es gibt **keinen zweiten Datenbestand**.

Entscheidung: `user_id`, nicht `employee_id`
--------------------------------------------
Der Arbeitstag haengt an `security.app_user`, nicht an `hr.employee`. Grund:
`time_entry.user_id` ist ein app_user, und die Stempeluhr wird vom angemeldeten
Konto bedient. Haenge der Arbeitstag an `hr.employee`, koennte ein Konto ohne
Personalsatz keine Zeit mehr buchen — die Tagesklammer wuerde am
Stammdatenpflege-Zustand scheitern statt an der Fachlichkeit. Der
Soll-Vergleich (Vertragsraster, Abwesenheiten, Urlaub) loest `app_user →
hr.employee` im Service auf; das ist eine Ableitung, keine physische
Abhaengigkeit.

Entscheidung: Nachtschicht haengt am ANFANGSTAG
-----------------------------------------------
Der Arbeitstag eines Eintrags ist der **lokale Kalendertag seines Beginns**
(Europe/Berlin, nicht UTC — sonst rutschte eine Buchung um 01:00 MESZ auf den
Vortag). Eine Schicht 22:00–06:00 gehoert damit vollstaendig dem Tag, an dem sie
begann. Begruendung: § 17 MiLoG verlangt Beginn, Ende und **Dauer der taeglichen
Arbeitszeit** — eine Schicht an der Tagesgrenze zu zerschneiden erzeugte zwei
Fragmente, von denen keines die tatsaechliche Arbeitszeit abbildet, und machte
die Pausen- und Ruhezeitpruefung unmoeglich. Ein Splitten um Mitternacht waere
die Alternative; sie ist bewusst NICHT gewaehlt.

Zwei unabhaengige Schloesser (kein Ersatz fuereinander)
-------------------------------------------------------
1. **B-28** (`workflow.guard_entry_correction`, Migration 0017): das
   *kaufmaennische* Fenster — nach Einsatzabschluss nur mit Begruendung, nach
   kaufmaennischer Auftragspruefung gar nicht mehr. Bleibt unveraendert scharf.
2. **Arbeitstag-Schloss** (hier): das *arbeitsrechtliche* Fenster — eine
   Aenderung an einem BESTAETIGTen Tag verlangt eine Begruendung UND wirft den
   Tag auf ENTWURF zurueck. Die Bestaetigung des Arbeitgebers gilt dann eben
   nicht mehr; sie muss neu erteilt werden. Beides ist im Audit und im
   Statusprotokoll nachlesbar — genau das verlangt die Aufzeichnungspflicht.

Vier-Augen physisch: `decided_by <> user_id` (Trigger). Wer seine eigenen
Stunden bestaetigen darf, hat keine Aufzeichnung, sondern eine Behauptung.
"""
import django.db.models.functions.datetime
from django.db import migrations, models

CREATE_SQL = r"""
-- Der Statusautomat-Katalog kennt bisher nur service_case/work_order/
-- service_job/quote (0010, erweitert in 0016; Katalog + FK aus 0042). Beide
-- CHECKs muessen erweitert werden, sonst schlaegt der FK
-- status_transition → status_catalog fehl.
ALTER TABLE workflow.status_transition DROP CONSTRAINT status_transition_entity_check;
ALTER TABLE workflow.status_transition ADD CONSTRAINT status_transition_entity_check
    CHECK (entity IN ('service_case', 'work_order', 'service_job', 'quote', 'work_day'));

ALTER TABLE workflow.status_catalog DROP CONSTRAINT status_catalog_entity_check;
ALTER TABLE workflow.status_catalog ADD CONSTRAINT status_catalog_entity_check
    CHECK (entity IN ('service_case', 'work_order', 'service_job', 'quote', 'work_day'));

-- BESTAETIGT ist NICHT final: eine Korrektur wirft den Tag auf ENTWURF zurueck
-- (genau das verlangt die Aufzeichnungspflicht — eine Aenderung darf nicht
-- verschwiegen, aber auch nicht verhindert werden).
INSERT INTO workflow.status_catalog (entity, status, label, sort_order, is_initial) VALUES
    ('work_day', 'ENTWURF',     'Entwurf',     1, true),
    ('work_day', 'EINGEREICHT', 'Eingereicht', 2, false),
    ('work_day', 'BESTAETIGT',  'Bestätigt',   3, false),
    ('work_day', 'ABGELEHNT',   'Abgelehnt',   4, false);

-- ---------------------------------------------------------------------------
-- workflow.work_day — die Tagesklammer
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.work_day (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES security.app_user (id),
    day            date NOT NULL,
    status         text NOT NULL DEFAULT 'ENTWURF'
                   CHECK (status IN ('ENTWURF', 'EINGEREICHT', 'BESTAETIGT', 'ABGELEHNT')),
    submitted_at   timestamptz NULL,
    decided_by     uuid NULL REFERENCES security.app_user (id),
    decided_at     timestamptz NULL,
    decision_note  text NULL,
    note           text NULL,
    version        integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT work_day_unique UNIQUE (user_id, day),
    -- Eingereicht/entschieden ⇒ Einreichzeitpunkt vorhanden.
    CONSTRAINT work_day_submitted_pair CHECK (
        (status IN ('EINGEREICHT', 'BESTAETIGT', 'ABGELEHNT')) = (submitted_at IS NOT NULL)
    ),
    -- Entscheidung und Entscheider bedingen einander.
    CONSTRAINT work_day_decision_pair CHECK (
        (status IN ('BESTAETIGT', 'ABGELEHNT'))
        = (decided_by IS NOT NULL AND decided_at IS NOT NULL)
    ),
    -- Ablehnung ist begruendungspflichtig (Muster hr.absence).
    CONSTRAINT work_day_rejection_needs_note CHECK (
        status <> 'ABGELEHNT' OR btrim(coalesce(decision_note, '')) <> ''
    )
);

CREATE INDEX idx_work_day_user_day ON workflow.work_day (user_id, day DESC);
CREATE INDEX idx_work_day_status ON workflow.work_day (status, day DESC);

INSERT INTO workflow.status_transition (entity, from_status, to_status, requires_reason) VALUES
    ('work_day', 'ENTWURF',     'EINGEREICHT', false),
    -- Zuruecknehmen bzw. automatischer Rueckfall, wenn der Tag nach dem
    -- Einreichen noch bearbeitet wird: keine Begruendungspflicht.
    ('work_day', 'EINGEREICHT', 'ENTWURF',     false),
    ('work_day', 'EINGEREICHT', 'BESTAETIGT',  false),
    ('work_day', 'EINGEREICHT', 'ABGELEHNT',   true),
    ('work_day', 'ABGELEHNT',   'ENTWURF',     false),
    ('work_day', 'ABGELEHNT',   'EINGEREICHT', false),
    -- Korrektur eines bestaetigten Tages: nur mit Begruendung, und die
    -- Bestaetigung faellt weg (sie muss neu erteilt werden).
    ('work_day', 'BESTAETIGT',  'ENTWURF',     true);

-- Vier-Augen + Unveraenderlichkeit von Mitarbeiter/Datum.
CREATE FUNCTION workflow.enforce_work_day() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.decided_by IS NOT NULL AND NEW.decided_by = NEW.user_id THEN
        RAISE EXCEPTION
            'Arbeitstag % (%): Ein Mitarbeiter kann seinen eigenen Arbeitstag '
            'nicht bestaetigen oder ablehnen (Vier-Augen-Prinzip)',
            NEW.day, NEW.user_id
            USING ERRCODE = 'raise_exception';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.day IS DISTINCT FROM OLD.day THEN
            RAISE EXCEPTION
                'Arbeitstag %: Mitarbeiter und Datum sind unveraenderlich', OLD.id
                USING ERRCODE = 'raise_exception';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_work_day_updated_at
    BEFORE UPDATE ON workflow.work_day
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_work_day_initial_status
    BEFORE INSERT ON workflow.work_day
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('ENTWURF');
CREATE TRIGGER trg_work_day_enforce
    BEFORE INSERT OR UPDATE ON workflow.work_day
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_work_day();
CREATE TRIGGER trg_work_day_status
    BEFORE UPDATE OF status ON workflow.work_day
    FOR EACH ROW EXECUTE FUNCTION workflow.validate_status_change('work_day');
CREATE TRIGGER trg_work_day_status_log
    AFTER INSERT OR UPDATE OF status ON workflow.work_day
    FOR EACH ROW EXECUTE FUNCTION workflow.log_status_change('work_day');
CREATE TRIGGER trg_work_day_audit
    AFTER UPDATE ON workflow.work_day
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_work_day_no_delete
    BEFORE DELETE ON workflow.work_day
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_work_day_no_truncate
    BEFORE TRUNCATE ON workflow.work_day
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON workflow.work_day FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Tagesklammer an workflow.time_entry
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.time_entry
    ADD COLUMN work_day_id uuid NULL REFERENCES workflow.work_day (id);

-- Der lokale Kalendertag des BEGINNS (Nachtschicht → Anfangstag, s. Kopf).
CREATE FUNCTION workflow.local_day(p_ts timestamptz) RETURNS date
LANGUAGE sql IMMUTABLE AS $$
    SELECT (p_ts AT TIME ZONE 'Europe/Berlin')::date;
$$;

-- Arbeitstag holen oder anlegen (race-fest ueber den UNIQUE-Constraint).
CREATE FUNCTION workflow.ensure_work_day(p_user uuid, p_day date) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
    SELECT id INTO v_id FROM workflow.work_day WHERE user_id = p_user AND day = p_day;
    IF v_id IS NULL THEN
        INSERT INTO workflow.work_day (user_id, day) VALUES (p_user, p_day)
        ON CONFLICT (user_id, day) DO NOTHING
        RETURNING id INTO v_id;
        IF v_id IS NULL THEN
            SELECT id INTO v_id FROM workflow.work_day WHERE user_id = p_user AND day = p_day;
        END IF;
    END IF;
    RETURN v_id;
END;
$$;

-- Das arbeitsrechtliche Schloss: wer an einem BESTAETIGTen Tag etwas aendert,
-- braucht eine Begruendung — und die Bestaetigung faellt weg. Ein EINGEREICHTer
-- Tag faellt ohne Begruendung auf ENTWURF zurueck (die Einreichung waere sonst
-- eine Luege).
CREATE FUNCTION workflow.unseal_work_day(p_id uuid, p_reason text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
    IF p_id IS NULL THEN RETURN; END IF;
    SELECT status INTO v_status FROM workflow.work_day WHERE id = p_id FOR UPDATE;
    IF v_status IS NULL THEN RETURN; END IF;

    IF v_status = 'BESTAETIGT' THEN
        IF p_reason IS NULL THEN
            RAISE EXCEPTION
                'Arbeitstag %: der Tag ist bestaetigt — eine Aenderung erfordert '
                'eine Begruendung (SET LOCAL app.correction_reason) und setzt die '
                'Bestaetigung zurueck', p_id
                USING ERRCODE = 'raise_exception';
        END IF;
        -- Der Rueckfall ist begruendungspflichtig (status_transition) — die
        -- Begruendung landet im Statusprotokoll.
        PERFORM set_config('app.status_reason', p_reason, true);
    END IF;

    IF v_status IN ('BESTAETIGT', 'EINGEREICHT') THEN
        UPDATE workflow.work_day
        SET status = 'ENTWURF', submitted_at = NULL,
            decided_by = NULL, decided_at = NULL, decision_note = NULL
        WHERE id = p_id;
    END IF;
END;
$$;

CREATE FUNCTION workflow.attach_time_entry_work_day() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_day    date;
    v_wd     uuid;
    v_reason text := coalesce(
        nullif(current_setting('app.correction_reason', true), ''),
        nullif(current_setting('app.status_reason', true), '')
    );
BEGIN
    -- Der bisherige Arbeitstag (UPDATE/DELETE) wird entsiegelt.
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM workflow.unseal_work_day(OLD.work_day_id, v_reason);
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    v_day := workflow.local_day(NEW.started_at);
    v_wd  := workflow.ensure_work_day(NEW.user_id, v_day);

    IF NEW.work_day_id IS NOT NULL AND NEW.work_day_id <> v_wd THEN
        RAISE EXCEPTION
            'time_entry %: work_day_id passt nicht zu Mitarbeiter und Beginndatum '
            '(der Arbeitstag ergibt sich aus user_id und started_at)',
            coalesce(NEW.id::text, '(neu)')
            USING ERRCODE = 'raise_exception';
    END IF;
    NEW.work_day_id := v_wd;

    -- Auch der Zieltag wird entsiegelt (Verschieben in einen bestaetigten Tag).
    PERFORM workflow.unseal_work_day(v_wd, v_reason);
    RETURN NEW;
END;
$$;

-- Backfill: jeder bestehende Eintrag bekommt seinen Arbeitstag.
-- Wie in 0066: das B-28-Tor haengt an UPDATE und wuerde den Backfill an jeder
-- Zeit blockieren, deren Auftrag bereits kaufmaennisch geprueft ist. Ein
-- Schema-Backfill ist keine fachliche Aenderung — Trigger fuer die Dauer des
-- Backfills aussetzen, danach sofort wieder scharf schalten.
INSERT INTO workflow.work_day (user_id, day)
SELECT DISTINCT user_id, workflow.local_day(started_at) FROM workflow.time_entry
ON CONFLICT (user_id, day) DO NOTHING;

ALTER TABLE workflow.time_entry DISABLE TRIGGER trg_time_entry_correction;

UPDATE workflow.time_entry e
SET work_day_id = w.id
FROM workflow.work_day w
WHERE w.user_id = e.user_id AND w.day = workflow.local_day(e.started_at);

ALTER TABLE workflow.time_entry ENABLE TRIGGER trg_time_entry_correction;

ALTER TABLE workflow.time_entry ALTER COLUMN work_day_id SET NOT NULL;
CREATE INDEX idx_time_entry_work_day ON workflow.time_entry (work_day_id);

-- Der Trigger fuellt work_day_id BEFORE INSERT selbst — damit ist NOT NULL auch
-- fuer Aufrufer erfuellt, die den Arbeitstag nicht kennen (z. B. der bestehende
-- Zeitbuchungspfad am Einsatz).
CREATE TRIGGER trg_time_entry_work_day
    BEFORE INSERT OR UPDATE OR DELETE ON workflow.time_entry
    FOR EACH ROW EXECUTE FUNCTION workflow.attach_time_entry_work_day();
"""

DROP_SQL = r"""
DROP TRIGGER IF EXISTS trg_time_entry_work_day ON workflow.time_entry;
DROP FUNCTION IF EXISTS workflow.attach_time_entry_work_day() CASCADE;
DROP FUNCTION IF EXISTS workflow.unseal_work_day(uuid, text) CASCADE;
DROP FUNCTION IF EXISTS workflow.ensure_work_day(uuid, date) CASCADE;
DROP INDEX IF EXISTS workflow.idx_time_entry_work_day;
ALTER TABLE workflow.time_entry DROP COLUMN work_day_id;
DROP FUNCTION IF EXISTS workflow.local_day(timestamptz) CASCADE;
DROP FUNCTION IF EXISTS workflow.enforce_work_day() CASCADE;
DELETE FROM workflow.status_change WHERE entity = 'work_day';
DELETE FROM workflow.status_transition WHERE entity = 'work_day';
DELETE FROM workflow.status_catalog WHERE entity = 'work_day';
DROP TABLE IF EXISTS workflow.work_day;
ALTER TABLE workflow.status_transition DROP CONSTRAINT status_transition_entity_check;
ALTER TABLE workflow.status_transition ADD CONSTRAINT status_transition_entity_check
    CHECK (entity IN ('service_case', 'work_order', 'service_job', 'quote'));
ALTER TABLE workflow.status_catalog DROP CONSTRAINT status_catalog_entity_check;
ALTER TABLE workflow.status_catalog ADD CONSTRAINT status_catalog_entity_check
    CHECK (entity IN ('service_case', 'work_order', 'service_job', 'quote'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0066_zeitkategorien"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
        # State-only (managed=False ⇒ kein DDL); FK-Felder fehlen absichtlich.
        migrations.CreateModel(
            name="WorkDay",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("day", models.DateField()),
                ("status", models.TextField(db_default=models.Value("ENTWURF"))),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_note", models.TextField(blank=True, null=True)),
                ("note", models.TextField(blank=True, null=True)),
                ("version", models.IntegerField(db_default=models.Value(1))),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
            ],
            options={"db_table": 'workflow"."work_day', "managed": False},
        ),
    ]
