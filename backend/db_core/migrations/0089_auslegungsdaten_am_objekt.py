"""Auslegungsdaten an der Liegenschaft — und die Nettowandfläche wird nie negativ.

Zwei Review-Funde am Raumaufmaß (0086).

## 1. Auslegungsdaten gehören ans Objekt

Ein grundsätzlicher Fund: Die raumweise
Heizlast braucht eine **Auslegungs-Außentemperatur** (ΔT = innen − außen). Die
lag bis hierher nur als Query-Parameter am Aufmaß-Endpunkt — also **nirgends**.
Kein Bildschirm fragte sie ab, kein Client schickte sie, und der Rechner meldete
folgerichtig für jeden noch so sorgfältig aufgenommenen Raum „unbekannt — die
Außentemperatur fehlt". Das Kernversprechen des Slices löste sich nie ein.

Der Fehler war die Verortung, nicht die Rechnung. Die Auslegungs-Außentemperatur
ist **keine Frage an den Aufruf, sondern eine Eigenschaft des Objekts**: Sie
folgt aus dem Standort und ändert sich nie. Dasselbe gilt für den überschlägigen
**Gebäudekennwert** (W/m²) — er beschreibt die Bauweise dieses Hauses, nicht den
Wunsch des Aufrufers. Beide gehören deshalb an die Liegenschaft, gleichauf mit
Adresse und Objektart.

    design_outdoor_temp_c   Auslegungs-Außentemperatur (Norm-Außentemperatur)
    heat_load_w_per_m2      Gebäudekennwert für das Kennwertverfahren

**Beide bleiben NULL-fähig, und das ist der Punkt.** Sie werden NICHT vorbelegt:
Die Norm-Außentemperatur eines Ortes ist ein DIN-Tabellenwert, und
Gebäudekennwerte sind es ebenso — MCN liefert dafür keine Tabellen mit (siehe
Modulkopf 0086 und HANDOFF, Welle 2/Punkt 11). Der Betrieb trägt die Werte ein,
die er verantwortet. Fehlen sie, bleibt die Heizlast **unbekannt — nicht 0**.

Der Raum darf die Objektwerte übersteuern (`room.heat_load_w_per_m2`, 0086); die
Rangfolge ist damit dieselbe wie überall im Haus: Einzelfall schlägt Vorgabe,
und fehlt beides, wird nichts erfunden.

## 2. INVARIANTE nachgeschärft: die Nettowandfläche wird nie negativ

0086 versprach im Modulkopf, eine negative Nettowandfläche sei physisch
unmöglich. Der Trigger hielt das nur **pro Wand** — und stieg bei einer Öffnung
**ohne** Wandzuordnung (`surface_id IS NULL`, der erlaubte „reine Mengenabzug")
sofort aus. Reproduziert: Raum mit einer Wand von 10 m² brutto, dazu eine freie
Öffnung von 5 × 5 m → gespeichert, `wall_area_net_m2 = −15,000 m²`. Die Heizlast
blieb zwar richtig (freie Öffnungen zählen nicht in die Transmission), aber die
Nettowandfläche ist die **Mengengrundlage** fürs Verputzen, Streichen, Tapezieren
— und eine negative Menge ist Unsinn, der ungeprüft in ein Angebot liefe.

`enforce_room_opening_fits` prüft deshalb ab jetzt **zwei** Grenzen:

    (a) je Wand:  Σ Öffnungen dieser Wand  ≤  Bruttofläche der Wand
    (b) je Raum:  Σ ALLER Öffnungen         ≤  Σ Bruttoflächen aller Wände

(b) fängt genau die Öffnungen, die (a) nicht sieht. Hat der Raum **noch keine
Hüllfläche**, greift (b) nicht — dann ist die Wandfläche schlicht *unbekannt*
(nicht 0), und der Monteur darf Fenster erfassen, bevor er die Wände aufnimmt.
Der Kennzahlen-Service meldet sie in diesem Fall als `null`, nicht als negative
oder erfundene Zahl.

Neu abgedeckt ist damit auch das **Löschen einer Wand**, unter der freie
Öffnungen liegen — bisher konnte es (b) verletzen. Der Serialisierungspunkt wird
von der Wand- auf die **Raumzeile** gehoben: Nur so sind zwei gleichzeitige
Schreibvorgänge an *verschiedenen* Wänden desselben Raumes gegen (b)
serialisiert.

## 3. Der Anker ist unveränderlich: ein Bauteil bleibt in seinem Raum

Die letzte Lücke, die ein zweiter Review-Durchgang fand: Ein
`UPDATE room_surface SET room_id = <anderer Raum>` löste **keinen** der beiden
Trigger aus (der eine hört auf `UPDATE OF gross_area_m2`, der andere auf DELETE).
Die Wand verließ den Raum, ihre Fläche fiel aus Σ Bauteilflächen — die freien
Öffnungen blieben. Negative Nettowandfläche, reproduziert.

`property.forbid_room_reassign` macht den Anker deshalb **unveränderlich**:
Eine Wand wandert nicht in einen anderen Raum, ein Fenster auch nicht. Wer
umhängen will, streicht im alten Raum und erfasst im neuen. Dieselbe Haltung wie
beim Auftragsbezug des Einsatzes (0062) und aus demselben Grund: Ein nachträglich
verschobener Anker umgeht genau die Prüfungen, die beim Anlegen gegriffen haben.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE property.property
    ADD COLUMN design_outdoor_temp_c numeric(4, 1) NULL
        CHECK (design_outdoor_temp_c IS NULL OR
               (design_outdoor_temp_c >= -40 AND design_outdoor_temp_c <= 30)),
    ADD COLUMN heat_load_w_per_m2 numeric(6, 1) NULL
        CHECK (heat_load_w_per_m2 IS NULL OR heat_load_w_per_m2 > 0);

COMMENT ON COLUMN property.property.design_outdoor_temp_c IS
    'Auslegungs-Außentemperatur des Standorts. Eingabe des Betriebs — KEINE mitgelieferte '
    'DIN-Klimatabelle. Fehlt sie, ist die Heizlast unbekannt, nicht 0.';
COMMENT ON COLUMN property.property.heat_load_w_per_m2 IS
    'Gebäudekennwert (W/m²) für das überschlägige Kennwertverfahren. Der Raum darf ihn '
    'übersteuern (property.room.heat_load_w_per_m2).';

-- ---------------------------------------------------------------------------
-- INVARIANTE (nachgeschärft, siehe Modulkopf):
--   (a) je Wand:  Σ Öffnungen dieser Wand ≤ Bruttofläche der Wand
--   (b) je Raum:  Σ ALLER Öffnungen       ≤ Σ Bruttoflächen aller Wände
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION property.enforce_room_opening_fits() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_room       uuid;
    v_surface_id uuid;      -- betroffene Wand (NULL = freier Mengenabzug)
    v_drop       uuid;      -- Wand, die gerade gelöscht wird
    v_skip_open  uuid;      -- Öffnung, die gerade geschrieben wird (alte Zeile ignorieren)
    v_add_area   numeric;   -- Fläche der neuen/geänderten Öffnung
    v_neu_id     uuid;      -- Wand, deren Bruttofläche gerade geändert wird …
    v_neu_gross  numeric;   -- … und ihr NEUER Wert
    v_gross      numeric;
    v_belegt     numeric;
    v_label      text;
    v_summe_wand numeric;
    v_summe_oeff numeric;
BEGIN
    -- Felder von NEW/OLD werden HIER in lokale Variablen gehoben. In den
    -- SQL-Anweisungen unten darf `NEW.gross_area_m2` nicht vorkommen: PL/pgSQL
    -- löst die Feldreferenz schon beim Planen auf, und beim Aufruf aus
    -- `room_opening` hat NEW dieses Feld nicht — der Trigger scheiterte selbst
    -- dann, wenn der CASE-Zweig nie zutrifft (verifiziert).
    IF TG_TABLE_NAME = 'room_opening' THEN
        v_room       := NEW.room_id;
        v_surface_id := NEW.surface_id;
        v_skip_open  := NEW.id;
        v_add_area   := round(NEW.quantity * NEW.width_m * NEW.height_m, 3);
    ELSIF TG_OP = 'DELETE' THEN            -- room_surface DELETE
        v_room := OLD.room_id;
        v_drop := OLD.id;
    ELSE                                   -- room_surface UPDATE OF gross_area_m2
        v_room       := NEW.room_id;
        v_surface_id := NEW.id;
        v_neu_id     := NEW.id;
        v_neu_gross  := NEW.gross_area_m2;
    END IF;

    -- Serialisierungspunkt ist die RAUMzeile, nicht die Wand: Grenze (b) spannt
    -- über alle Wände des Raumes, zwei gleichzeitige Schreibvorgänge an
    -- VERSCHIEDENEN Wänden wären über eine Wandsperre nicht serialisiert.
    -- READ COMMITTED gibt jeder folgenden Anweisung einen frischen Snapshot,
    -- sobald die Sperre erteilt ist.
    PERFORM 1 FROM property.room WHERE id = v_room FOR UPDATE;

    -- (a) je Wand — nur wenn eine Wand betroffen ist.
    IF v_surface_id IS NOT NULL THEN
        SELECT gross_area_m2, coalesce(label, surface_type)
          INTO v_gross, v_label
          FROM property.room_surface
         WHERE id = v_surface_id;

        IF FOUND THEN
            IF v_neu_id IS NOT NULL THEN
                v_gross := v_neu_gross;   -- die NEUE Bruttofläche gilt
            END IF;

            SELECT coalesce(sum(area_m2), 0) INTO v_belegt
              FROM property.room_opening
             WHERE surface_id = v_surface_id
               AND (v_skip_open IS NULL OR id <> v_skip_open);

            IF TG_TABLE_NAME = 'room_opening' THEN
                v_belegt := v_belegt + v_add_area;
            END IF;

            IF v_belegt > v_gross THEN
                RAISE EXCEPTION
                    'Die Öffnungen (% m²) sind größer als die Fläche „%" (% m²).',
                    v_belegt, v_label, v_gross
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'room_opening_passt_in_flaeche';
            END IF;
        END IF;
    END IF;

    -- (b) je Raum — fängt die Öffnungen OHNE Wandzuordnung und das Löschen einer
    -- Wand, unter der freie Öffnungen liegen.
    SELECT coalesce(sum(
               CASE WHEN v_neu_id IS NOT NULL AND id = v_neu_id
                    THEN v_neu_gross
                    ELSE gross_area_m2 END), 0)
      INTO v_summe_wand
      FROM property.room_surface
     WHERE room_id = v_room
       AND (v_drop IS NULL OR id <> v_drop);

    -- Kein Bauteil aufgenommen ⇒ die Wandfläche ist UNBEKANNT, nicht 0. Dann gibt
    -- es keine Grenze, gegen die man prüfen könnte — Fenster dürfen vor den
    -- Wänden erfasst werden.
    IF v_summe_wand = 0 THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;

    SELECT coalesce(sum(area_m2), 0) INTO v_summe_oeff
      FROM property.room_opening
     WHERE room_id = v_room
       AND (v_skip_open IS NULL OR id <> v_skip_open);

    IF TG_TABLE_NAME = 'room_opening' THEN
        v_summe_oeff := v_summe_oeff + v_add_area;
    END IF;

    IF v_summe_oeff > v_summe_wand THEN
        RAISE EXCEPTION
            'Die Öffnungen des Raumes (% m²) sind größer als seine Bauteilflächen (% m²) — '
            'die Nettowandfläche wäre negativ.',
            v_summe_oeff, v_summe_wand
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'room_opening_passt_in_flaeche';
    END IF;

    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;

-- Neu: das Löschen einer Wand kann Grenze (b) verletzen.
CREATE TRIGGER trg_room_surface_loeschen_passt
    BEFORE DELETE ON property.room_surface
    FOR EACH ROW EXECUTE FUNCTION property.enforce_room_opening_fits();

-- ---------------------------------------------------------------------------
-- Die letzte Lücke in Grenze (b): das UMHÄNGEN einer Wand in einen anderen Raum.
--
-- Kein Trigger oben fasst das: `trg_room_surface_passt` hört auf
-- `UPDATE OF gross_area_m2`, der Lösch-Trigger auf DELETE. Ein
-- `UPDATE room_surface SET room_id = <anderer Raum>` löst also keinen von beiden
-- aus — die Wand verlässt den Raum, ihre Fläche fällt aus Σ Bauteilflächen
-- heraus, und die freien Öffnungen bleiben stehen. Ergebnis: negative
-- Nettowandfläche, reproduziert im Review.
--
-- Man könnte die Triggerliste erweitern und OLD.room_id nachprüfen. Die
-- fachlich richtige Antwort ist aber einfacher: **Eine Wand wandert nicht in
-- einen anderen Raum, und ein Fenster auch nicht.** Ein Bauteil gehört zu dem
-- Raum, in dem es aufgenommen wurde; „umhängen" heißt in Wahrheit: im alten Raum
-- streichen, im neuen neu erfassen. Der Anker ist damit unveränderlich — dieselbe
-- Haltung wie beim Auftragsbezug des Einsatzes (0062), und aus demselben Grund:
-- ein nachträglich verschobener Anker umgeht die Prüfungen, die beim Anlegen
-- gegriffen haben.
-- ---------------------------------------------------------------------------
CREATE FUNCTION property.forbid_room_reassign() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.room_id IS DISTINCT FROM OLD.room_id THEN
        RAISE EXCEPTION
            'Ein Bauteil kann nicht in einen anderen Raum verschoben werden. '
            'Im alten Raum streichen und im neuen neu erfassen.'
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'bauteil_bleibt_im_raum';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_room_surface_bleibt_im_raum
    BEFORE UPDATE OF room_id ON property.room_surface
    FOR EACH ROW EXECUTE FUNCTION property.forbid_room_reassign();
CREATE TRIGGER trg_room_opening_bleibt_im_raum
    BEFORE UPDATE OF room_id ON property.room_opening
    FOR EACH ROW EXECUTE FUNCTION property.forbid_room_reassign();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_room_opening_bleibt_im_raum ON property.room_opening;
DROP TRIGGER IF EXISTS trg_room_surface_bleibt_im_raum ON property.room_surface;
DROP FUNCTION IF EXISTS property.forbid_room_reassign();
DROP TRIGGER IF EXISTS trg_room_surface_loeschen_passt ON property.room_surface;

-- Zurück auf die Fassung aus 0086 (nur Grenze (a), Sperre auf der Wandzeile).
CREATE OR REPLACE FUNCTION property.enforce_room_opening_fits() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_surface_id uuid;
    v_gross      numeric(10, 3);
    v_belegt     numeric;
    v_label      text;
BEGIN
    IF TG_TABLE_NAME = 'room_opening' THEN
        v_surface_id := NEW.surface_id;
        IF v_surface_id IS NULL THEN
            RETURN NEW;
        END IF;
    ELSE
        v_surface_id := NEW.id;
    END IF;

    SELECT gross_area_m2, coalesce(label, surface_type)
      INTO v_gross, v_label
      FROM property.room_surface
     WHERE id = v_surface_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'room_surface' THEN
        v_gross := NEW.gross_area_m2;
    END IF;

    SELECT coalesce(sum(area_m2), 0)
      INTO v_belegt
      FROM property.room_opening
     WHERE surface_id = v_surface_id
       AND (TG_TABLE_NAME <> 'room_opening' OR id <> NEW.id);

    IF TG_TABLE_NAME = 'room_opening' THEN
        v_belegt := v_belegt + round(NEW.quantity * NEW.width_m * NEW.height_m, 3);
    END IF;

    IF v_belegt > v_gross THEN
        RAISE EXCEPTION
            'Die Öffnungen (% m²) sind größer als die Fläche „%" (% m²).',
            v_belegt, v_label, v_gross
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'room_opening_passt_in_flaeche';
    END IF;

    RETURN NEW;
END;
$$;

ALTER TABLE property.property
    DROP COLUMN design_outdoor_temp_c,
    DROP COLUMN heat_load_w_per_m2;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0087_room_roomopening_roomsurface")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
