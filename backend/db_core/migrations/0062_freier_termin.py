"""Freier Termin ohne Auftrag (workflow.service_job).

Fachlicher Hintergrund (User-Entscheidung 2026-07-12)
-----------------------------------------------------
Nicht jeder Termin gehört zu einem Auftrag. Eine **Begehung**, eine
**Besichtigung** oder eine **Beratung** findet statt, *bevor* es überhaupt einen
Auftrag gibt — oft sogar, bevor der Kunde als Kontakt angelegt ist. Bisher war
`service_job.work_order_id` NOT NULL: ein solcher Termin ließ sich physisch nicht
speichern. Diese Migration öffnet den Einsatz für den freien Termin, ohne die
Tore für auftragsgebundene Einsätze aufzuweichen.

Drei Entscheidungen
-------------------
**1. `work_order_id` wird NULLABLE — der Auftragsbezug ist damit die Ausnahme,
nicht die Regel? Nein.** Er bleibt der Normalfall; NULL bedeutet ausdrücklich
„freier Termin". Damit ein solcher Termin nicht als anonymer Zeitblock endet,
braucht er einen **fachlichen Anker**:

**2. `title text` am Einsatz.** Der Titel kam bisher ausschließlich vom Auftrag
(`work_order.title`) — ohne Auftrag hätte der Termin gar keine Bezeichnung.
Deshalb: `CHECK (work_order_id IS NOT NULL OR title IS NOT NULL)`. Für
auftragsgebundene Einsätze bleibt `title` **optional** (Fallback = Auftragstitel);
so muss kein Bestandsdatensatz nachgepflegt werden und die bestehende Anzeige
bleibt gültig. Ein leerer/Whitespace-Titel ist verboten (`btrim(title) <> ''`) —
sonst wäre die Pflicht durch ein Leerzeichen aushebelbar.

**3. `property_id uuid NULL REFERENCES property.property`.** Die Liegenschaft kam
bisher ausschließlich über `work_order.property_id` (NOT NULL). Ein freier Termin
hat keinen Auftrag — ohne eigene Spalte könnten Plantafel und Kalender bei ihm
keinen Ort anzeigen, und die Begehung „an welchem Objekt eigentlich?" wäre nicht
beantwortbar. Die Spalte ist **optional**: die Begehung eines Objekts, das noch
nicht im System steht, muss trotzdem terminierbar sein.

Konsistenz Auftrag ↔ Liegenschaft: **zusammengesetzter Fremdschlüssel**
`(work_order_id, property_id) → workflow.work_order (id, property_id)`, exakt das
Muster von `invoicing.invoice` (P3-12). PostgreSQL prüft einen mehrspaltigen FK
per MATCH SIMPLE nur, wenn **alle** Spalten belegt sind. Das liefert genau die
gewünschte Semantik ohne einen einzigen Trigger:

| work_order_id | property_id | Prüfung |
|---|---|---|
| NULL | NULL | frei, ohne Objekt — erlaubt |
| NULL | gesetzt | freier Termin an einer Liegenschaft — nur der Einzel-FK greift |
| gesetzt | NULL | auftragsgebunden, Objekt kommt vom Auftrag (Fallback) |
| gesetzt | gesetzt | **muss** die Liegenschaft des Auftrags sein — FK erzwingt es |

Die Tore, die `work_order_id` voraussetzen
------------------------------------------
- `workflow.check_job_order_status` (BEFORE INSERT): keine Anlage auf
  ABGERECHNET/STORNIERT. Bei NULL gibt es keinen Auftrag → nichts zu prüfen.
- `workflow.check_job_execution_gate` (BEFORE UPDATE OF status): Ausführung ab
  UNTERWEGS setzt einen freigegebenen Auftrag voraus (B-01/A-23). Bei NULL gibt
  es **keine Beauftragung, auf die man warten könnte** — der freie Termin läuft
  ohne dieses Tor bis ABGESCHLOSSEN durch. Das ist der ganze Zweck: Eine Begehung
  ist genau die Tätigkeit, die *vor* der Beauftragung stattfindet. Der restliche
  Statusautomat (`workflow.status_transition`, entity='service_job') kennt keinen
  einzigen weiteren Auftragsbezug und bleibt unverändert scharf.
- **NEU: `work_order_id` ist unveränderlich** (WF-01-Linie). Beide Tore oben
  hängen an INSERT bzw. am Statuswechsel — ein späteres UPDATE des
  Auftragsbezugs hätte sie umgangen und einen laufenden Einsatz an einen
  **abgerechneten** Auftrag hängen können (die Lücke bestand latent schon vorher,
  wird mit dem NULL-Fall aber real: „freien Termin nachträglich an einen Auftrag
  hängen"). Ein freier Termin bleibt frei; wer ihn beauftragt haben will, legt
  einen Einsatz am Auftrag an — die Begehung bleibt als eigener, ehrlicher
  Vorgang stehen.

Korrekturfenster B-28 (Zeit/Material) — notwendige Anpassung an den NULL-Fall
-----------------------------------------------------------------------------
`workflow.guard_entry_correction` (Migration 0017) ermittelt Einsatz- **und**
Auftragsstatus über einen **INNER JOIN** auf `work_order`. Das war bisher
korrekt: `work_order_id` war NOT NULL, der Join fand immer eine Zeile. **Kein
Altbug** — der Fall konnte physisch nicht auftreten.

Mit dem freien Termin (work_order_id IS NULL) liefert derselbe Join jedoch
**keine Zeile**: beide Statusvariablen blieben NULL und das Korrekturfenster
griffe für freie Termine überhaupt nicht (Zeiten auf einem längst
abgeschlossenen freien Termin wären ohne Begründung nachbuchbar). Der Join wird
deshalb im Zuge dieses Slices auf **LEFT JOIN** umgestellt — eine zwingende
Anpassung an die neue Nullability, keine Reparatur.

Für auftragsgebundene Einsätze bleibt das Verhalten zeilenweise identisch; für
freie Termine gilt der Einsatz-Teil des Fensters (Begründungspflicht ab
ABGESCHLOSSEN) weiter, der Auftrags-Teil (kaufmännische Prüfung) entfällt mangels
Auftrag.

Schutzstandard: `workflow.service_job` trägt No-Delete, Änderungs-Audit und
No-Truncate bereits seit 0015/0009 — die Trigger hängen an der Tabelle und
erfassen neue Spalten automatisch mit.

Rückwärts: nur solange kein freier Termin existiert (sonst verlöre `DROP COLUMN`
Fachdaten und `SET NOT NULL` schlüge fehl). Der Reverse setzt die alten
Trigger-Funktionen wieder ein.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Auftragsbezug wird optional; Titel und Liegenschaft am Einsatz
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.service_job
    ALTER COLUMN work_order_id DROP NOT NULL;

ALTER TABLE workflow.service_job
    ADD COLUMN title       text NULL,
    ADD COLUMN property_id uuid NULL REFERENCES property.property (id);

-- Ein freier Termin (ohne Auftrag) braucht einen Titel; ein leerer oder nur aus
-- Leerzeichen bestehender Titel ist nie zulässig (sonst wäre die Pflicht durch
-- ein Leerzeichen aushebelbar).
ALTER TABLE workflow.service_job
    ADD CONSTRAINT service_job_title_not_blank
        CHECK (title IS NULL OR btrim(title) <> ''),
    ADD CONSTRAINT service_job_freier_termin_braucht_titel
        CHECK (work_order_id IS NOT NULL OR title IS NOT NULL);

-- P3-12-Muster (wie invoicing.invoice): Ist der Einsatz auftragsgebunden UND
-- trägt er eine eigene Liegenschaft, muss es die des Auftrags sein. MATCH SIMPLE
-- lässt die Kombinationen mit NULL bewusst durch (freier Termin bzw. Fallback
-- auf die Auftrags-Liegenschaft).
ALTER TABLE workflow.service_job
    ADD CONSTRAINT service_job_property_matches_order
        FOREIGN KEY (work_order_id, property_id)
        REFERENCES workflow.work_order (id, property_id);

CREATE INDEX idx_service_job_property ON workflow.service_job (property_id)
    WHERE property_id IS NOT NULL;

COMMENT ON COLUMN workflow.service_job.work_order_id IS
    'Auftrag des Einsatzes. NULL = freier Termin (Begehung/Besichtigung/Beratung ohne Auftrag).';
COMMENT ON COLUMN workflow.service_job.title IS
    'Titel des Einsatzes. Pflicht beim freien Termin (ohne Auftrag); sonst optional (Fallback: Auftragstitel).';
COMMENT ON COLUMN workflow.service_job.property_id IS
    'Liegenschaft des Einsatzes. Beim auftragsgebundenen Einsatz optional und dann zwingend die Liegenschaft des Auftrags (zusammengesetzter FK).';

-- ---------------------------------------------------------------------------
-- 2. Auftragsbezug ist unveränderlich (WF-01-Linie)
-- ---------------------------------------------------------------------------
-- check_job_order_status feuert nur BEI INSERT, das Ausführungs-Tor nur beim
-- Statuswechsel. Ohne diesen Riegel ließe sich ein laufender Einsatz per UPDATE
-- an einen ABGERECHNETEN Auftrag hängen — oder ein freier Termin nachträglich
-- „beauftragen", vorbei an beiden Toren.
CREATE FUNCTION workflow.protect_service_job_order() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.work_order_id IS DISTINCT FROM OLD.work_order_id THEN
        RAISE EXCEPTION
            'Einsatz %: Der Auftragsbezug ist unveränderlich (WF-01). Ein freier Termin bleibt frei; für einen Auftrag wird ein eigener Einsatz angelegt.',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_service_job_order_immutable
    BEFORE UPDATE ON workflow.service_job
    FOR EACH ROW EXECUTE FUNCTION workflow.protect_service_job_order();

-- ---------------------------------------------------------------------------
-- 3. Tore, die den Auftrag lesen: NULL sauber behandeln
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION workflow.check_job_order_status() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    -- Freier Termin (kein Auftrag): nichts zu prüfen.
    IF NEW.work_order_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT status INTO v_status FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;
    IF v_status IN ('ABGERECHNET', 'STORNIERT') THEN
        RAISE EXCEPTION
            'Einsatz für Auftrag % unzulässig: Auftrag ist % (B-03/B-06 — Folgeauftrag verwenden)',
            NEW.work_order_id, v_status;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION workflow.check_job_execution_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF NEW.status = 'UNTERWEGS' AND OLD.status <> 'UNTERWEGS' THEN
        -- Freier Termin: es gibt keinen Auftrag, dessen Freigabe man abwarten
        -- könnte — eine Begehung findet gerade VOR der Beauftragung statt.
        IF NEW.work_order_id IS NULL THEN
            RETURN NEW;
        END IF;
        SELECT status INTO v_status
        FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;
        IF v_status NOT IN ('FREIGEGEBEN', 'IN_PLANUNG', 'IN_AUSFUEHRUNG') THEN
            RAISE EXCEPTION
                'Einsatz %: Ausführung erfordert einen freigegebenen Auftrag (B-01/A-23), Auftrag ist %',
                NEW.id, v_status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- 4. Korrekturfenster B-28: INNER JOIN -> LEFT JOIN (Anpassung, kein Bugfix)
-- ---------------------------------------------------------------------------
-- Der INNER JOIN war bis hierher korrekt (work_order_id war NOT NULL). Ab jetzt
-- fände er beim freien Termin KEINE Zeile: beide Statusvariablen blieben NULL
-- und das Fenster griffe für freie Termine gar nicht.
CREATE OR REPLACE FUNCTION workflow.guard_entry_correction() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_job_id       uuid;
    v_job_status   text;
    v_order_status text;
    v_reason       text := nullif(current_setting('app.correction_reason', true), '');
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.service_job_id IS DISTINCT FROM OLD.service_job_id THEN
        RAISE EXCEPTION
            '%.%: Der Einsatzbezug ist unveränderlich (B-28/P3-03)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    v_job_id := CASE WHEN TG_OP = 'INSERT' THEN NEW.service_job_id ELSE OLD.service_job_id END;
    IF v_job_id IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;

    -- LEFT JOIN: der freie Termin hat keinen Auftrag; sein Einsatzstatus muss
    -- trotzdem gelesen werden (Begründungspflicht ab ABGESCHLOSSEN).
    SELECT j.status, o.status INTO v_job_status, v_order_status
    FROM workflow.service_job j
    LEFT JOIN workflow.work_order o ON o.id = j.work_order_id
    WHERE j.id = v_job_id
    FOR SHARE OF j;

    IF v_order_status IN ('KAUFMAENNISCH_GEPRUEFT', 'ABGERECHNET') THEN
        RAISE EXCEPTION
            '%.%: Nach kaufmännischer Freigabe des Auftrags sind Zeit-/Materialänderungen unzulässig (B-28)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    IF v_job_status IN ('ABGESCHLOSSEN', 'NACHARBEIT') AND v_reason IS NULL THEN
        RAISE EXCEPTION
            '%.%: Änderung nach Einsatzabschluss erfordert eine Begründung (SET LOCAL app.correction_reason, B-28)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION workflow.guard_entry_correction() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_job_id       uuid;
    v_job_status   text;
    v_order_status text;
    v_reason       text := nullif(current_setting('app.correction_reason', true), '');
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.service_job_id IS DISTINCT FROM OLD.service_job_id THEN
        RAISE EXCEPTION
            '%.%: Der Einsatzbezug ist unveränderlich (B-28/P3-03)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    v_job_id := CASE WHEN TG_OP = 'INSERT' THEN NEW.service_job_id ELSE OLD.service_job_id END;
    IF v_job_id IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;

    SELECT j.status, o.status INTO v_job_status, v_order_status
    FROM workflow.service_job j
    JOIN workflow.work_order o ON o.id = j.work_order_id
    WHERE j.id = v_job_id
    FOR SHARE OF j;

    IF v_order_status IN ('KAUFMAENNISCH_GEPRUEFT', 'ABGERECHNET') THEN
        RAISE EXCEPTION
            '%.%: Nach kaufmännischer Freigabe des Auftrags sind Zeit-/Materialänderungen unzulässig (B-28)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    IF v_job_status IN ('ABGESCHLOSSEN', 'NACHARBEIT') AND v_reason IS NULL THEN
        RAISE EXCEPTION
            '%.%: Änderung nach Einsatzabschluss erfordert eine Begründung (SET LOCAL app.correction_reason, B-28)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE OR REPLACE FUNCTION workflow.check_job_execution_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF NEW.status = 'UNTERWEGS' AND OLD.status <> 'UNTERWEGS' THEN
        SELECT status INTO v_status
        FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;
        IF v_status NOT IN ('FREIGEGEBEN', 'IN_PLANUNG', 'IN_AUSFUEHRUNG') THEN
            RAISE EXCEPTION
                'Einsatz %: Ausführung erfordert einen freigegebenen Auftrag (B-01/A-23), Auftrag ist %',
                NEW.id, v_status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION workflow.check_job_order_status() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;
    IF v_status IN ('ABGERECHNET', 'STORNIERT') THEN
        RAISE EXCEPTION
            'Einsatz für Auftrag % unzulässig: Auftrag ist % (B-03/B-06 — Folgeauftrag verwenden)',
            NEW.work_order_id, v_status;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_service_job_order_immutable ON workflow.service_job;
DROP FUNCTION IF EXISTS workflow.protect_service_job_order();

DROP INDEX IF EXISTS workflow.idx_service_job_property;

ALTER TABLE workflow.service_job
    DROP CONSTRAINT IF EXISTS service_job_property_matches_order,
    DROP CONSTRAINT IF EXISTS service_job_freier_termin_braucht_titel,
    DROP CONSTRAINT IF EXISTS service_job_title_not_blank;

ALTER TABLE workflow.service_job
    DROP COLUMN IF EXISTS property_id,
    DROP COLUMN IF EXISTS title;

ALTER TABLE workflow.service_job
    ALTER COLUMN work_order_id SET NOT NULL;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0061_invoiceadvance"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
