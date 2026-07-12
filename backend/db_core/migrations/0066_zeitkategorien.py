"""Zeitkategorien (hr.time_category) + freie Zeit an workflow.time_entry.

Hand-SQL nach db/README.md: neue Fachtabelle als RunSQL mit Schutzstandard
(updated_at/Audit/No-Delete/No-Truncate/REVOKE). Muster: 0019 (hr.*), 0054.

Ausgangslage
------------
`workflow.time_entry.time_type` (Migration 0017, Beschluss B-27) ist ein hartes
CHECK-Enum mit sechs Werten. Ein Betrieb kann damit keine eigene Zeitart anlegen
(„Werkstatt", „Materialfahrt", „Schulung") — jede Änderung wäre eine Migration.
Das ist für eine Zeiterfassung, die der Betrieb selbst führt, nicht tragbar.

Entscheidung: harter Cut statt Koexistenz
-----------------------------------------
`time_type` wird **gedroppt**, nicht neben `category_id` weitergeführt. Zwei
parallele Klassifikationen wären eine Einladung zur Divergenz (welche gilt für
die Auswertung? welche für die Lohnabrechnung?), und es gibt **keinen
Produktivbestand** — die Zeiterfassung ist bis heute nur Beiwerk am Einsatz. Der
Backfill (`time_type` → gleichnamige Systemkategorie) ist verlustfrei.

`is_work_time` ist das **einzige fachlich harte Attribut** der Kategorie: nur
daran hängt, ob die Zeit als Arbeitszeit im Sinne von ArbZG/MiLoG zählt. Alles
andere (Name, Beschreibung, Sortierung) ist Betriebssache. Systemkategorien sind
nicht archivierbar, und `PAUSE.is_work_time` ist nicht umschaltbar — eine Pause,
die als Arbeitszeit zählt, wäre die Aufzeichnungspflicht ad absurdum geführt.

Freie Zeit (Zeit ohne Einsatz)
------------------------------
Der CHECK `time_type = 'INTERNE_ZEIT' OR service_job_id IS NOT NULL` entfällt.
Werkstattzeit, Bürozeit und die Fahrt zum Großhandel sind Arbeitszeit, hängen
aber an keinem Termin. Der Einsatzbezug wird damit für JEDE Kategorie optional.
(Der Bezug bleibt unveränderlich — `workflow.guard_entry_correction` prüft das
weiterhin.)

Laufende Buchung + Überlappungssperre
-------------------------------------
Die Stempeluhr braucht einen **offenen** Eintrag: `ended_at` wird nullbar
(`NULL` = läuft gerade). Der CHECK `ended_at > started_at` wird entsprechend zu
`ended_at IS NULL OR ended_at > started_at`.

Bisher konnte sich ein Monteur beliebig doppelt buchen. Neu:
`EXCLUDE USING gist (user_id WITH =, tstzrange(started_at, ended_at) WITH &&)
WHERE (ended_at IS NOT NULL)` — also **nur unter den abgeschlossenen Buchungen**.

Die naheliegende Fassung (laufende Buchung als `[start, ∞)`, ein Constraint fuer
Ueberlappung UND Einmaligkeit) wurde bewusst VERWORFEN: sie behauptet, der
Mitarbeiter arbeite bis in alle Ewigkeit, und verbietet damit jede Buchung, die
NACH dem laufenden Start liegt — auch eine voraus geplante Zeit am Termin von
morgen. Die Einmaligkeit der laufenden Buchung sichert stattdessen der partielle
UNIQUE-Index auf `user_id WHERE ended_at IS NULL`. Die Kollision entsteht erst
beim STOPPEN; der Service prueft sie dort — und schon beim Start — vor, damit
niemand in einer Sackgasse landet.

Vorlauf statt Blindflug
-----------------------
Der EXCLUDE-Constraint schlägt bei einem Bestand mit Überlappungen mit rohem
`exclusion_violation` fehl — ohne zu sagen, WELCHE Zeilen schuld sind. Die
Migration prüft das deshalb vorher selbst und nennt die betroffenen Paare
(Mitarbeiter, Zeitraum, IDs) in der Fehlermeldung.
"""
import django.db.models.functions.datetime
from django.db import migrations, models

CREATE_SQL = r"""
-- ---------------------------------------------------------------------------
-- hr.time_category — Zeitkategorien als pflegbare Stammdaten
-- ---------------------------------------------------------------------------
CREATE TABLE hr.time_category (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Systemkategorien tragen einen stabilen Code (Backfill/Service-Referenz:
    -- der Pausen-Stempel muss PAUSE finden, ohne sich auf den Namen zu stützen).
    code          text NULL UNIQUE CHECK (code IS NULL OR code ~ '^[A-Z_]+$'),
    name          text NOT NULL CHECK (btrim(name) <> ''),
    description   text NULL,
    -- Das einzige fachlich harte Attribut: zaehlt diese Zeit als Arbeitszeit
    -- (ArbZG/MiLoG)? Alles andere ist Betriebssache.
    is_work_time  boolean NOT NULL,
    is_system     boolean NOT NULL DEFAULT false,
    status        text NOT NULL DEFAULT 'AKTIV'
                  CHECK (status IN ('AKTIV', 'ARCHIVIERT')),
    sort_order    integer NOT NULL DEFAULT 100,
    version       integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    -- Systemkategorien sind per Definition Code-behaftet.
    CONSTRAINT time_category_system_has_code
        CHECK (NOT is_system OR code IS NOT NULL)
);

-- Name eindeutig unter den AKTIVEN, case-insensitiv. Archivierte Namen
-- blockieren nicht (sonst wäre ein Name nach dem Archivieren für immer verbrannt).
CREATE UNIQUE INDEX uq_time_category_name_active
    ON hr.time_category (lower(btrim(name))) WHERE status = 'AKTIV';
CREATE INDEX idx_time_category_sort ON hr.time_category (status, sort_order, name);

CREATE FUNCTION hr.protect_time_category() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.is_system THEN
        IF NEW.status <> 'AKTIV' THEN
            RAISE EXCEPTION
                'Zeitkategorie %: Systemkategorien können nicht archiviert werden',
                OLD.name USING ERRCODE = 'raise_exception';
        END IF;
        IF NEW.code IS DISTINCT FROM OLD.code OR NEW.is_system IS DISTINCT FROM OLD.is_system THEN
            RAISE EXCEPTION
                'Zeitkategorie %: Code und Systemkennzeichen sind unveränderlich',
                OLD.name USING ERRCODE = 'raise_exception';
        END IF;
        -- Die Pause ist keine Arbeitszeit. Das ist keine Einstellung.
        IF OLD.code = 'PAUSE' AND NEW.is_work_time IS DISTINCT FROM OLD.is_work_time THEN
            RAISE EXCEPTION
                'Zeitkategorie PAUSE: is_work_time ist nicht umschaltbar — eine '
                'Pause zaehlt nie als Arbeitszeit (ArbZG/MiLoG)'
                USING ERRCODE = 'raise_exception';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_time_category_updated_at
    BEFORE UPDATE ON hr.time_category
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_time_category_protect
    BEFORE UPDATE ON hr.time_category
    FOR EACH ROW EXECUTE FUNCTION hr.protect_time_category();
CREATE TRIGGER trg_time_category_audit
    AFTER UPDATE ON hr.time_category
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_time_category_no_delete
    BEFORE DELETE ON hr.time_category
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_time_category_no_truncate
    BEFORE TRUNCATE ON hr.time_category
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.time_category FROM PUBLIC;

-- Seed: die sechs bisherigen time_type-Werte als Systemkategorien (B-27),
-- dazu vier gaengige SHK-Kategorien als NICHT-System (der Betrieb darf sie
-- umbenennen, umsortieren oder archivieren).
INSERT INTO hr.time_category (code, name, description, is_work_time, is_system, sort_order) VALUES
    ('ARBEITSZEIT',  'Arbeitszeit',  'Produktive Arbeitszeit am Einsatz oder im Betrieb', true,  true,  10),
    ('FAHRTZEIT',    'Fahrtzeit',    'Fahrt zum und vom Einsatzort',                      true,  true,  20),
    ('PAUSE',        'Pause',        'Gesetzliche Ruhepause (keine Arbeitszeit)',         false, true,  30),
    ('BEREITSCHAFT', 'Bereitschaft', 'Rufbereitschaft/Bereitschaftsdienst',               true,  true,  40),
    ('NACHARBEIT',   'Nacharbeit',   'Nachbesserung/Maengelbeseitigung',                  true,  true,  50),
    ('INTERNE_ZEIT', 'Interne Zeit', 'Interne Taetigkeit ohne Einsatzbezug',              true,  true,  60);

INSERT INTO hr.time_category (name, description, is_work_time, is_system, sort_order) VALUES
    ('Werkstatt',     'Vorbereitung, Reparatur und Ruestzeiten in der Werkstatt', true, false,  70),
    ('Buero',         'Bueroarbeit, Angebote, Abrechnung',                        true, false,  80),
    ('Materialfahrt', 'Fahrt zum Grosshandel/Materialbeschaffung',                true, false,  90),
    ('Schulung',      'Fortbildung, Unterweisung, Sicherheitsschulung',           true, false, 100);

-- ---------------------------------------------------------------------------
-- workflow.time_entry: Kategorie statt Enum, freie Zeit, laufende Buchung
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.time_entry
    ADD COLUMN category_id uuid NULL REFERENCES hr.time_category (id);

-- Backfill: verlustfrei ueber den Code (kein Produktivbestand, aber Dev-/Demo-
-- und Testdaten sollen die Migration ueberleben).
--
-- Das B-28-Korrekturfenster (`workflow.guard_entry_correction`, 0017) haengt an
-- UPDATE und wuerde den Backfill an jeder Zeit blockieren, deren Auftrag bereits
-- kaufmaennisch geprueft ist. Das Tor schuetzt FACHLICHE Aenderungen; ein
-- Schema-Backfill, der denselben Sachverhalt nur anders speichert, ist keine.
-- Deshalb wird der Trigger fuer die Dauer des Backfills ausgesetzt — und danach
-- sofort wieder scharf geschaltet.
ALTER TABLE workflow.time_entry DISABLE TRIGGER trg_time_entry_correction;

UPDATE workflow.time_entry e
SET category_id = c.id
FROM hr.time_category c
WHERE c.code = e.time_type;

ALTER TABLE workflow.time_entry ENABLE TRIGGER trg_time_entry_correction;

ALTER TABLE workflow.time_entry ALTER COLUMN category_id SET NOT NULL;
CREATE INDEX idx_time_entry_category ON workflow.time_entry (category_id);

-- DROP COLUMN raeumt die beiden abhaengigen CHECKs gleich mit ab:
--   * time_type IN (...)                                (Enum)
--   * time_type = 'INTERNE_ZEIT' OR service_job_id IS NOT NULL
-- Damit wird der Einsatzbezug fuer JEDE Kategorie optional — Werkstatt-, Buero-
-- und Materialfahrtzeit haengen an keinem Termin und sind trotzdem Arbeitszeit.
ALTER TABLE workflow.time_entry DROP COLUMN time_type;

-- Laufende Buchung: ended_at NULL = laeuft gerade.
ALTER TABLE workflow.time_entry ALTER COLUMN ended_at DROP NOT NULL;

-- Der alte CHECK (ended_at > started_at) traegt einen generierten Namen; er wird
-- ueber den Katalog gesucht und durch die NULL-vertraegliche Fassung ersetzt.
DO $$
DECLARE c record;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'workflow.time_entry'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%ended_at%'
    LOOP
        EXECUTE format('ALTER TABLE workflow.time_entry DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE workflow.time_entry
    ADD CONSTRAINT time_entry_end_after_start
    CHECK (ended_at IS NULL OR ended_at > started_at);

-- Genau EINE laufende Buchung je Mitarbeiter.
CREATE UNIQUE INDEX uq_time_entry_running
    ON workflow.time_entry (user_id) WHERE ended_at IS NULL;

-- Keine Ueberlappung je Mitarbeiter — aber nur unter den ABGESCHLOSSENEN
-- Buchungen.
--
-- Die naheliegende Fassung waere, die laufende Buchung als [start, ∞) zu
-- modellieren; sie erschlaegt Ueberlappung und Einmaligkeit in EINEM Constraint.
-- Sie ist aber falsch: sie behauptet, der Mitarbeiter arbeite bis in alle
-- Ewigkeit, und verbietet damit jede Buchung, die NACH dem laufenden Start
-- liegt — auch eine voraus geplante Zeit an einem Termin von morgen. Genau das
-- ist beim Browser-Durchlauf aufgeschlagen.
--
-- Richtig ist: eine laufende Buchung hat schlicht noch kein Ende und kann
-- deshalb (noch) mit nichts kollidieren. Die Kollision entsteht erst beim
-- STOPPEN — dann traegt sie ein Ende, faellt unter den Constraint und wird
-- gegen alle abgeschlossenen Buchungen geprueft. Der Fehler kommt also genau
-- dann, wenn er fachlich entsteht, und er ist dann auch korrigierbar.
-- Die Einmaligkeit der laufenden Buchung sichert der partielle UNIQUE-Index.
--
-- Vorlauf: liegt im Bestand schon eine Ueberlappung (Dev-/Demo-Daten, Altimport),
-- bricht ADD CONSTRAINT mit einem rohen `exclusion_violation` ab und nennt KEINE
-- Zeile. Der Anwender stuende im Dunkeln. Also erst selbst nachsehen und die
-- schuldigen Paare benennen — Beginn, Ende, IDs und Mitarbeiter.
DO $$
DECLARE
    v_anzahl integer;
    v_liste  text;
BEGIN
    SELECT count(*), string_agg(z.zeile, E'\n' ORDER BY z.zeile)
    INTO v_anzahl, v_liste
    FROM (
        SELECT format(
                   '  %s (%s): %s bis %s [%s]  ueberlappt  %s bis %s [%s]',
                   coalesce(u.display_name, '?'), a.user_id,
                   a.started_at, a.ended_at, a.id,
                   b.started_at, b.ended_at, b.id
               ) AS zeile
        FROM workflow.time_entry a
        JOIN workflow.time_entry b
          ON b.user_id = a.user_id
         AND b.id > a.id
         AND tstzrange(a.started_at, a.ended_at)
             && tstzrange(b.started_at, b.ended_at)
        LEFT JOIN security.app_user u ON u.id = a.user_id
        WHERE a.ended_at IS NOT NULL AND b.ended_at IS NOT NULL
        LIMIT 50
    ) z;

    IF v_anzahl > 0 THEN
        RAISE EXCEPTION E'Migration 0066: der Bestand enthaelt % ueberlappende Zeitbuchungs-Paare. Der neue EXCLUDE-Constraint kann so nicht angelegt werden. Betroffen (max. 50):\n%\nBitte diese Zeiten korrigieren oder loeschen und die Migration erneut ausfuehren.',
            v_anzahl, v_liste
            USING ERRCODE = 'raise_exception';
    END IF;
END $$;

ALTER TABLE workflow.time_entry
    ADD CONSTRAINT excl_time_entry_overlap EXCLUDE USING gist (
        user_id WITH =,
        tstzrange(started_at, ended_at) WITH &&
    ) WHERE (ended_at IS NOT NULL);
"""

DROP_SQL = r"""
ALTER TABLE workflow.time_entry DROP CONSTRAINT excl_time_entry_overlap;
DROP INDEX workflow.uq_time_entry_running;
ALTER TABLE workflow.time_entry DROP CONSTRAINT time_entry_end_after_start;
DELETE FROM workflow.time_entry WHERE ended_at IS NULL;
ALTER TABLE workflow.time_entry ALTER COLUMN ended_at SET NOT NULL;
ALTER TABLE workflow.time_entry ADD COLUMN time_type text;
UPDATE workflow.time_entry e SET time_type = coalesce(c.code, 'INTERNE_ZEIT')
FROM hr.time_category c WHERE c.id = e.category_id;
ALTER TABLE workflow.time_entry ALTER COLUMN time_type SET NOT NULL;
ALTER TABLE workflow.time_entry ADD CHECK (time_type IN
    ('ARBEITSZEIT', 'FAHRTZEIT', 'PAUSE', 'BEREITSCHAFT', 'NACHARBEIT', 'INTERNE_ZEIT'));
ALTER TABLE workflow.time_entry ADD CHECK (ended_at > started_at);
ALTER TABLE workflow.time_entry ADD CHECK
    (time_type = 'INTERNE_ZEIT' OR service_job_id IS NOT NULL);
DROP INDEX workflow.idx_time_entry_category;
ALTER TABLE workflow.time_entry DROP COLUMN category_id;
DROP FUNCTION IF EXISTS hr.protect_time_category() CASCADE;
DROP TABLE IF EXISTS hr.time_category;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0065_bericht_anhaenge_versiegeln"),
    ]

    operations = [
        # reverse_sql zulaessig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
        # State-only (managed=False ⇒ kein DDL), damit `makemigrations --check`
        # sauber bleibt. Bewusst hier statt in einer eigenen Datei: die
        # Migrationsnummern 0066–0068 sind fuer diesen Slice reserviert, 0069
        # gehoert einem parallelen Zweig.
        migrations.CreateModel(
            name="TimeCategory",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("code", models.TextField(blank=True, null=True)),
                ("name", models.TextField()),
                ("description", models.TextField(blank=True, null=True)),
                ("is_work_time", models.BooleanField()),
                ("is_system", models.BooleanField(db_default=False)),
                ("status", models.TextField(db_default=models.Value("AKTIV"))),
                ("sort_order", models.IntegerField(db_default=models.Value(100))),
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
            options={"db_table": 'hr"."time_category', "managed": False},
        ),
    ]
