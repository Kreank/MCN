"""Gewerk als echte Achse: Katalog, Bezug an den Fachobjekten, sprechende Nummern.

Fachlicher Hintergrund (User-Entscheidungen 2026-07-20)
-------------------------------------------------------
``company.trade`` existiert seit 0023 als Katalog — aber **kein einziges
Fachobjekt verwies darauf**. Gewerke ließen sich pflegen und mit nichts
verknüpfen. Die Auswertung „Marge je Gewerk" näherte das Gewerk deshalb über
``workflow.project_category`` an (siehe ``services/auswertungen.py``); was im
Einstellungsbereich als Gewerk gepflegt wurde, hatte darauf keinen Einfluss.
Diese Migration macht das Gewerk zur tragenden Achse.

Der Katalog wird auf die sechs tatsächlich betriebenen Gewerke gesetzt:
Sanitär, Heizung, Trockenbau, Maler, Fliesenleger, Elektriker.

Warum NEUE Zeilen statt Umbenennen
-----------------------------------
Der ``code`` ist der fachliche Schlüssel und nach Anlage unveränderlich (das
Pflegeformular kennt bewusst kein Code-Feld, ``TradePatch``). Er ist ab jetzt
zugleich das **Kürzel in der Nummer** — ``AU-HZG-26-0142``. Die Bestandscodes
(``TROCKENBAU``, ``ELEKTRO``, …) sind dafür zu lang, und ``SHK`` fasst Sanitär,
Heizung und Klima zusammen, die getrennt werden müssen. Also: sechs neue Zeilen
mit Kurzcodes, die alten werden **deaktiviert, nicht gelöscht** (No-Delete/GoBD;
sie verschwinden aus den Auswahllisten, bleiben in der Historie und lassen sich
jederzeit reaktivieren). Das ist gefahrlos, weil bisher nichts auf sie zeigt.

Der Bezug an den Fachobjekten
------------------------------
``trade_id`` (NULL-fähig) an Projekt, Vorgang, Auftrag und Einsatz. NULL-fähig,
weil das Gewerk beim Annehmen eines Anrufs noch unklar sein darf — die Erfassung
soll nie blockieren (User-Entscheidung). ``ON DELETE`` entfällt: der Katalog
kennt kein DELETE.

Mitarbeiter ↔ Gewerk ist n:m (ein Monteur kann Sanitär UND Heizung) und bekommt
deshalb eine eigene Tabelle ``hr.employee_trade`` mit dem vollen Schutzstandard.
Sie ersetzt die Behelfslösung ``hr.employee_qualification.kind = 'GEWERK'``
NICHT — die bleibt für Zertifikate/Schulungen zuständig; hier geht es um die
schlichte Frage „welches Gewerk kann wer".

Nummernvergabe: eigene Funktion statt Umbau der bestehenden
------------------------------------------------------------
``workflow.next_number()`` wird **nicht angefasst**. Angebot, Rechnung,
Gutschrift/Storno und Wartungsvertrag behalten Funktion, Format
(``PREFIX-JJJJ-NNNNNN``) und Trigger unverändert — Belegnummern sind
GoBD-gebunden, ein sprechendes Gewerk-Kürzel hat dort nichts verloren (das
Gewerk kann sich ändern, eine ausgegebene Belegnummer nie). Stattdessen kommt
``workflow.next_number_gewerk()`` für die vier INTERNEN Nummern hinzu.

Neues Format: ``PREFIX[-KUERZEL]-JJ-NNNN``

    AU-HZG-26-0142   Auftrag Heizung
    AU-26-0143       Auftrag ohne Gewerk (Ersatzformat)
    E-SAN-26-0088    Einsatz Sanitär

Zum zweistelligen Jahr, bewusst in Kauf genommen: 2126 erzeugt dieselben
Nummernstrings wie 2026. Da die Nummernspalten UNIQUE sind, gäbe es dann
Kollisionen statt stiller Dubletten — ein lauter Fehler in hundert Jahren, kein
Datenverlust. Das Altformat mit vierstelligem Jahr schützte davor; die kürzere
Nummer war die ausdrückliche Anforderung. Wer das Problem später auflösen will,
nimmt das Jahrhundert in den `scope` auf (der Zähler startet dann pro
Jahrhundert neu) — die Format-CHECKs erlauben das bereits.

Der Zähler läuft **je (Präfix, Gewerk, Jahr)** — jedes Gewerk zählt ab 1
(User-Entscheidung). Ungewerkte Nummern teilen sich den Kreis (`scope = ''`)
mit ``workflow.next_number()``; beide Funktionen zählen dort gemeinsam hoch,
sodass sich Alt- und Neuformat desselben Präfixes nie überholen können. ``workflow.number_range`` bekommt dafür eine
``scope``-Spalte im Primärschlüssel; ``scope = ''`` ist der ungewerkte Zähler
und zugleich der Bestandszähler, der einfach weiterläuft. Kollisionen mit alten
Nummern sind ausgeschlossen, weil das Jahr dort vierstellig ist:
``AU-2026-000142`` und ``AU-26-0143`` können sich nie treffen.

Warum die Vergabe vom DEFAULT auf einen Trigger wandert
--------------------------------------------------------
Ein Spalten-DEFAULT sieht die übrigen Spalten derselben Zeile nicht — er kann
das Gewerk also nicht kennen. Die vier Tabellen bekommen deshalb einen
BEFORE-INSERT-Trigger, der ``NEW.trade_id`` liest und die Nummer daraus baut.
Das ist derselbe Mechanismus, den Angebot und Rechnung längst benutzen.
Zusätzlich schließt der Trigger eine Lücke, die der DEFAULT offenließ: eine
explizit mitgegebene Nummer wird jetzt abgewiesen statt übernommen (P3-01).

Bestandsnummern bleiben gültig
-------------------------------
Die Format-CHECKs erlauben ab jetzt **beide** Formate. Bestehende Nummern
werden nicht umgeschrieben — bei internen Nummern wäre das verwirrend, bei
Belegnummern unzulässig. Der Schnitt gilt ab Umstellung.

Rückwärts
---------
Vollständig umkehrbar, solange keine Zeile eine Nummer im neuen Format trägt
(der wiederhergestellte CHECK würde sie zurückweisen) und keine Gewerk-Zuordnung
existiert. Der Katalog-Seed wird beim Zurückrollen auf den Stand von 0023
zurückgesetzt.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Katalog: sechs Gewerke, Altbestand deaktiviert
-- ---------------------------------------------------------------------------
-- Zuerst deaktivieren, dann anlegen: `SHK` wird durch SAN/HZG abgelöst, die
-- übrigen (Zimmerei, Dach, Metallbau, Garten, Gebäudereinigung) betreibt der
-- Betrieb nicht. Kein DELETE — die Tabelle verbietet es per Trigger, und die
-- Historie soll lesbar bleiben.
-- Der Code ist ab jetzt Bestandteil der Nummer und muss deshalb mit einem
-- Buchstaben beginnen. Bisher erlaubte der CHECK rein numerische Codes — ein
-- Gewerk mit dem Code `26` erzeugte `AU-26-26-0142`, ein Code `2026` sogar
-- `AU-2026-26-0142`. Beide sind nicht mehr eindeutig vom Jahresteil zu trennen
-- und wären über die Suche nicht auffindbar. Alle Bestandscodes beginnen
-- ohnehin mit einem Buchstaben, die Verschärfung trifft niemanden.
ALTER TABLE company.trade DROP CONSTRAINT trade_code_check;
ALTER TABLE company.trade ADD CONSTRAINT trade_code_check
    CHECK (code ~ '^[A-Z][A-Z0-9_]+$');

UPDATE company.trade SET active = false WHERE active;

INSERT INTO company.trade (code, label, sort_order) VALUES
    ('SAN', 'Sanitär',      10),
    ('HZG', 'Heizung',      20),
    ('TRO', 'Trockenbau',   30),
    ('MAL', 'Maler',        40),
    ('FLI', 'Fliesenleger', 50),
    ('ELT', 'Elektriker',   60)
ON CONFLICT (code) DO UPDATE
    SET label = EXCLUDED.label, sort_order = EXCLUDED.sort_order, active = true;

-- ---------------------------------------------------------------------------
-- 2. Gewerk am Fachobjekt
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.project     ADD COLUMN trade_id uuid NULL REFERENCES company.trade (id);
ALTER TABLE workflow.service_case ADD COLUMN trade_id uuid NULL REFERENCES company.trade (id);
ALTER TABLE workflow.work_order  ADD COLUMN trade_id uuid NULL REFERENCES company.trade (id);
ALTER TABLE workflow.service_job ADD COLUMN trade_id uuid NULL REFERENCES company.trade (id);

-- Teilindizes: Auswertungen und Filter fragen „alle Heizungsaufträge", nie
-- „alle ohne Gewerk" — die NULL-Zeilen gehören nicht in den Index.
CREATE INDEX idx_project_trade      ON workflow.project      (trade_id) WHERE trade_id IS NOT NULL;
CREATE INDEX idx_service_case_trade ON workflow.service_case (trade_id) WHERE trade_id IS NOT NULL;
CREATE INDEX idx_work_order_trade   ON workflow.work_order   (trade_id) WHERE trade_id IS NOT NULL;
CREATE INDEX idx_service_job_trade  ON workflow.service_job  (trade_id) WHERE trade_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. Mitarbeiter ↔ Gewerk (n:m)
-- ---------------------------------------------------------------------------
-- Voller Schutzstandard. `id`/`version`/`created_at`/`updated_at` sind für
-- audit.audit_row_update() Pflicht (der ::uuid-Cast dort setzt eine uuid-PK
-- namens `id` voraus, siehe 0023).
CREATE TABLE hr.employee_trade (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id uuid NOT NULL REFERENCES hr.employee (id),
    trade_id    uuid NOT NULL REFERENCES company.trade (id),
    -- Deaktivieren statt Löschen, wie im ganzen Haus: Die Tabelle verbietet
    -- DELETE (unten), und „Monteur X konnte 2026 Heizung" bleibt eine wahre
    -- Aussage, auch wenn er es heute nicht mehr macht. Ohne dieses Flag wäre
    -- eine einmal gesetzte Zuordnung unwiderruflich.
    active      boolean NOT NULL DEFAULT true,
    version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (employee_id, trade_id)
);
CREATE INDEX idx_employee_trade_trade ON hr.employee_trade (trade_id) WHERE active;

CREATE TRIGGER trg_employee_trade_updated_at
    BEFORE UPDATE ON hr.employee_trade
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_employee_trade_audit
    AFTER UPDATE ON hr.employee_trade
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_employee_trade_no_delete
    BEFORE DELETE ON hr.employee_trade
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_employee_trade_no_truncate
    BEFORE TRUNCATE ON hr.employee_trade
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.employee_trade FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 4. Nummernkreis um den Gewerk-Bereich erweitern
-- ---------------------------------------------------------------------------
-- `scope` = Gewerk-Kürzel, '' = ungewerkt. Der DEFAULT trägt die Bestandszeilen
-- verlustfrei in den neuen Primärschlüssel: sie zählen als ungewerkter Kreis
-- einfach weiter.
ALTER TABLE workflow.number_range ADD COLUMN scope text NOT NULL DEFAULT ''
    CHECK (scope = '' OR scope ~ '^[A-Z0-9_]{2,}$');
ALTER TABLE workflow.number_range DROP CONSTRAINT number_range_pkey;
ALTER TABLE workflow.number_range ADD PRIMARY KEY (prefix, scope, year);

-- Der erweiterte Schlüssel zwingt die BESTEHENDE Funktion zur Anpassung: ihr
-- `ON CONFLICT (prefix, year)` findet den Unique-Index nicht mehr und bricht
-- mit „no unique or exclusion constraint matching" ab — Angebot und Rechnung
-- bekämen keine Nummer mehr. Nachgezogen wird ausschließlich das Konfliktziel;
-- Format, Polsterung, Jahreslogik und Semantik bleiben Zeichen für Zeichen wie
-- in 0010. Die Funktion schreibt implizit in den ungewerkten Kreis (scope
-- DEFAULT ''), also genau dorthin, wo ihre Zähler ohnehin schon standen.
CREATE OR REPLACE FUNCTION workflow.next_number(p_prefix text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    -- Jahreszuordnung in UTC, unabhängig von der Sitzungszeitzone (WF-12)
    v_year  integer := extract(year FROM (now() AT TIME ZONE 'UTC'))::integer;
    v_value integer;
BEGIN
    INSERT INTO workflow.number_range (prefix, year, last_value)
    VALUES (p_prefix, v_year, 1)
    ON CONFLICT (prefix, scope, year)
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

CREATE FUNCTION workflow.next_number_gewerk(p_prefix text, p_trade_id uuid)
RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    -- Zweistellig und in UTC — deckungsgleich mit workflow.next_number(), damit
    -- beide Kreise dieselbe Jahresgrenze sehen (WF-12).
    v_year  integer := extract(year FROM (now() AT TIME ZONE 'UTC'))::integer;
    v_scope text := '';
    v_value integer;
BEGIN
    IF p_trade_id IS NOT NULL THEN
        SELECT code INTO v_scope FROM company.trade WHERE id = p_trade_id;
        IF v_scope IS NULL THEN
            RAISE EXCEPTION 'Unbekanntes Gewerk %', p_trade_id;
        END IF;
    END IF;

    INSERT INTO workflow.number_range (prefix, scope, year, last_value)
    VALUES (p_prefix, v_scope, v_year, 1)
    ON CONFLICT (prefix, scope, year)
    DO UPDATE SET last_value = workflow.number_range.last_value + 1
    RETURNING last_value INTO v_value;

    -- Wie in next_number(): oberhalb der Polsterbreite ungepolstert weiter,
    -- lpad würde sonst RECHTS abschneiden und Nummern doppeln (WF-03).
    RETURN p_prefix
        || CASE WHEN v_scope = '' THEN '' ELSE '-' || v_scope END
        || '-' || lpad((v_year % 100)::text, 2, '0')
        || '-' || CASE WHEN v_value < 10000
                       THEN lpad(v_value::text, 4, '0')
                       ELSE v_value::text END;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. Vergabe vom DEFAULT auf Trigger umstellen
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.project      ALTER COLUMN project_number DROP DEFAULT;
ALTER TABLE workflow.service_case ALTER COLUMN case_number    DROP DEFAULT;
ALTER TABLE workflow.work_order   ALTER COLUMN order_number   DROP DEFAULT;
ALTER TABLE workflow.service_job  ALTER COLUMN job_number     DROP DEFAULT;

-- Eine Funktion für alle vier: Präfix und Zielspalte kommen als Trigger-
-- Argumente, damit vier fast gleiche Funktionen entfallen.
CREATE FUNCTION workflow.assign_gewerk_number() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_prefix text := TG_ARGV[0];
    v_column text := TG_ARGV[1];
    v_vorhanden text;
BEGIN
    EXECUTE format('SELECT ($1).%I', v_column) INTO v_vorhanden USING NEW;
    -- P3-01: Nummern werden ausschließlich hier vergeben, nie übernommen. Der
    -- alte DEFAULT ließ eine mitgegebene Nummer stillschweigend durch.
    --
    -- Der Leerstring zählt als „nicht gesetzt": Ohne `db_default` schickt die
    -- ORM für ein unbelegtes TextField '' statt NULL — das ist kein Versuch,
    -- eine Nummer vorzugeben, sondern das Fehlen einer.
    IF v_vorhanden IS NOT NULL AND v_vorhanden <> '' THEN
        RAISE EXCEPTION '%: Nummern werden ausschließlich beim Anlegen vergeben (B-13/B-14)',
            TG_TABLE_NAME;
    END IF;
    NEW := json_populate_record(
        NEW,
        json_build_object(v_column, workflow.next_number_gewerk(v_prefix, NEW.trade_id))
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_project_number
    BEFORE INSERT ON workflow.project
    FOR EACH ROW EXECUTE FUNCTION workflow.assign_gewerk_number('P', 'project_number');
CREATE TRIGGER trg_service_case_number
    BEFORE INSERT ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION workflow.assign_gewerk_number('V', 'case_number');
CREATE TRIGGER trg_work_order_number
    BEFORE INSERT ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION workflow.assign_gewerk_number('AU', 'order_number');
CREATE TRIGGER trg_service_job_number
    BEFORE INSERT ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION workflow.assign_gewerk_number('E', 'job_number');

-- ---------------------------------------------------------------------------
-- 6. Format-CHECKs: beide Formate erlauben
-- ---------------------------------------------------------------------------
-- Bestandsnummern (PREFIX-JJJJ-NNNNNN) bleiben gültig, neue kommen als
-- PREFIX[-KUERZEL]-JJ-NNNN dazu. Die neuen Constraints sind explizit benannt —
-- die alten hießen nur so, weil PostgreSQL sie so generiert hat.
ALTER TABLE workflow.project DROP CONSTRAINT project_project_number_check;
ALTER TABLE workflow.project ADD CONSTRAINT project_number_format CHECK (
    project_number ~ '^P-[0-9]{4}-[0-9]{6,}$'
    OR project_number ~ '^P(-[A-Z0-9_]{2,})?-[0-9]{2}-[0-9]{4,}$');

ALTER TABLE workflow.service_case DROP CONSTRAINT service_case_case_number_check;
ALTER TABLE workflow.service_case ADD CONSTRAINT service_case_number_format CHECK (
    case_number ~ '^V-[0-9]{4}-[0-9]{6,}$'
    OR case_number ~ '^V(-[A-Z0-9_]{2,})?-[0-9]{2}-[0-9]{4,}$');

ALTER TABLE workflow.work_order DROP CONSTRAINT work_order_order_number_check;
ALTER TABLE workflow.work_order ADD CONSTRAINT work_order_number_format CHECK (
    order_number ~ '^AU-[0-9]{4}-[0-9]{6,}$'
    OR order_number ~ '^AU(-[A-Z0-9_]{2,})?-[0-9]{2}-[0-9]{4,}$');

ALTER TABLE workflow.service_job DROP CONSTRAINT service_job_job_number_check;
ALTER TABLE workflow.service_job ADD CONSTRAINT service_job_number_format CHECK (
    job_number ~ '^E-[0-9]{4}-[0-9]{6,}$'
    OR job_number ~ '^E(-[A-Z0-9_]{2,})?-[0-9]{2}-[0-9]{4,}$');
"""

REVERSE_SQL = r"""
ALTER TABLE workflow.service_job DROP CONSTRAINT service_job_number_format;
ALTER TABLE workflow.service_job ADD CONSTRAINT service_job_job_number_check
    CHECK (job_number ~ '^E-[0-9]{4}-[0-9]{6,}$');
ALTER TABLE workflow.work_order DROP CONSTRAINT work_order_number_format;
ALTER TABLE workflow.work_order ADD CONSTRAINT work_order_order_number_check
    CHECK (order_number ~ '^AU-[0-9]{4}-[0-9]{6,}$');
ALTER TABLE workflow.service_case DROP CONSTRAINT service_case_number_format;
ALTER TABLE workflow.service_case ADD CONSTRAINT service_case_case_number_check
    CHECK (case_number ~ '^V-[0-9]{4}-[0-9]{6,}$');
ALTER TABLE workflow.project DROP CONSTRAINT project_number_format;
ALTER TABLE workflow.project ADD CONSTRAINT project_project_number_check
    CHECK (project_number ~ '^P-[0-9]{4}-[0-9]{6,}$');

DROP TRIGGER trg_service_job_number  ON workflow.service_job;
DROP TRIGGER trg_work_order_number   ON workflow.work_order;
DROP TRIGGER trg_service_case_number ON workflow.service_case;
DROP TRIGGER trg_project_number      ON workflow.project;
DROP FUNCTION workflow.assign_gewerk_number();

ALTER TABLE workflow.service_job  ALTER COLUMN job_number     SET DEFAULT workflow.next_number('E');
ALTER TABLE workflow.work_order   ALTER COLUMN order_number   SET DEFAULT workflow.next_number('AU');
ALTER TABLE workflow.service_case ALTER COLUMN case_number    SET DEFAULT workflow.next_number('V');
ALTER TABLE workflow.project      ALTER COLUMN project_number SET DEFAULT workflow.next_number('P');

DROP FUNCTION workflow.next_number_gewerk(text, uuid);

-- Der ungewerkte Kreis ist der Bestandskreis; gewerkgebundene Zähler haben im
-- alten Schlüssel keinen Platz und verfallen.
DELETE FROM workflow.number_range WHERE scope <> '';
ALTER TABLE workflow.number_range DROP CONSTRAINT number_range_pkey;
ALTER TABLE workflow.number_range ADD PRIMARY KEY (prefix, year);
ALTER TABLE workflow.number_range DROP COLUMN scope;

-- Konfliktziel zurück auf den schmalen Schlüssel (Stand 0010).
CREATE OR REPLACE FUNCTION workflow.next_number(p_prefix text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    v_year  integer := extract(year FROM (now() AT TIME ZONE 'UTC'))::integer;
    v_value integer;
BEGIN
    INSERT INTO workflow.number_range (prefix, year, last_value)
    VALUES (p_prefix, v_year, 1)
    ON CONFLICT (prefix, year)
    DO UPDATE SET last_value = workflow.number_range.last_value + 1
    RETURNING last_value INTO v_value;

    RETURN p_prefix || '-' || v_year::text || '-' ||
           CASE WHEN v_value < 1000000
                THEN lpad(v_value::text, 6, '0')
                ELSE v_value::text END;
END;
$$;

DROP TABLE hr.employee_trade;

DROP INDEX workflow.idx_service_job_trade;
DROP INDEX workflow.idx_work_order_trade;
DROP INDEX workflow.idx_service_case_trade;
DROP INDEX workflow.idx_project_trade;
ALTER TABLE workflow.service_job  DROP COLUMN trade_id;
ALTER TABLE workflow.work_order   DROP COLUMN trade_id;
ALTER TABLE workflow.service_case DROP COLUMN trade_id;
ALTER TABLE workflow.project      DROP COLUMN trade_id;

ALTER TABLE company.trade DROP CONSTRAINT trade_code_check;
ALTER TABLE company.trade ADD CONSTRAINT trade_code_check
    CHECK (code ~ '^[A-Z0-9_]{2,}$');

UPDATE company.trade SET active = false WHERE code IN ('SAN','HZG','TRO','MAL','FLI','ELT');
UPDATE company.trade SET active = true WHERE code IN (
    'SHK','ELEKTRO','MALER','TROCKENBAU','FLIESEN','ZIMMEREI','DACH','METALLBAU',
    'GARTEN','GEBAEUDEREINIGUNG');
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0119_termin_ort_gebaeude_einheit"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
