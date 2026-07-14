"""Raumaufmaß (property.room, room_surface, room_opening).

Bis hierher kannte MCN kein Wort für den Ort, an dem gearbeitet wird. Die
Objektwelt endete bei der **Einheit** (`property.unit`, Migration 0004) — der
Wohnung, dem Ladenlokal. Der **Raum** fehlte, und mit ihm jede Grundlage für die
drei Fragen, die am Anfang eines Heizungs- oder Sanierungsauftrags stehen:

    Wie groß ist der Raum?   →  Fläche, Volumen, Umfang
    Wie viel Wärme braucht er?  →  raumweise Heizlast
    Wie viel Material geht rein?  →  Mengen aus echten Maßen

Das bisherige „Aufmaß" war ein reiner Taschenrechner im Browser (Welle 3,
`features/werkzeuge/aufmass-rechner.ts`): Teilmaße → Verschnitt → Gebinde, das
Ergebnis fiel als Menge in eine Angebotsposition und war danach **weg**. Nichts
davon überlebte den Beleg. Der Betrieb muss die Räume eines Objekts aber
**einmal** aufnehmen und **dauerhaft** behalten — sie ändern sich fast nie, und
jeder spätere Vorgang (Heizlast, Heizkörper, Leitungslängen, 3D-Planung) setzt
darauf auf. Deshalb ist der Raum **Objektstammdatum**, kein Werkzeug-Zwischenwert:
er hängt an der Liegenschaft, nicht am Vorgang.

## Anker: Liegenschaft, optional Gebäude/Einheit

`property_id` ist Pflicht, `building_id`/`unit_id` sind optional — dieselbe
Staffelung wie bei `property.technical_asset` (0004) und aus demselben Grund: Beim
Einfamilienhaus gibt es weder Gebäude- noch Einheitsgliederung, im WEG-Bestand
sehr wohl. Die Zuordnung wird über **zusammengesetzte FKs** gesichert
(`(building_id, property_id)` → building, `(unit_id, building_id)` → unit): ein
Raum kann damit physisch nicht in einer Einheit liegen, die zu einer fremden
Liegenschaft gehört.

`UNIQUE NULLS NOT DISTINCT (property_id, unit_id, storey, name)` verhindert den
häufigsten Fehler einer Begehung: denselben Raum zweimal aufnehmen. Ohne
`NULLS NOT DISTINCT` (PG 15+) liefe die Sperre bei nicht gesetzter Einheit oder
nicht gesetztem Geschoss ins Leere — und genau das ist der Regelfall im
Einfamilienhaus.

## Die Fläche ist die Wahrheit, Länge × Breite ist nur die Herleitung

`floor_area_m2` ist NOT NULL; `length_m`/`width_m` sind optional. Kein CHECK
erzwingt `area = length * width`, und das ist Absicht: Ein L-förmiger Raum, ein
Erker, eine Dachschräge — die Fläche stimmt dann nicht mit dem Rechteck überein,
und ein Zwang zur Rechteckform machte das Aufmaß für genau die Räume unbrauchbar,
für die man es braucht. Wer rechteckig misst, lässt sich die Fläche vom Client
vorrechnen; wer anders misst, trägt sie ein. Die DB verlangt nur, dass sie da und
positiv ist.

`volume_m3` ist dagegen eine **GENERATED-Spalte** — `floor_area_m2 * room_height_m`
ist keine Ermessensfrage, sondern eine Definition. Damit kann kein Client ein
inkonsistentes Volumen schreiben, und niemand muss es nachhalten.

`perimeter_m` (Umfang) ist optional und **kein** abgeleiteter Wert: Aus einer
Fläche folgt kein Umfang (dasselbe Quadratmeter-Maß hat als Quadrat einen anderen
Umfang als als Schlauch). Er wird gemessen — und trägt später die Sockelleiste,
die Fußleiste, die Leitungslänge an der Wand entlang.

## Hüllflächen und Öffnungen: die Voraussetzung für eine raumweise Heizlast

Der Heizlastrechner aus Welle 3 kann genau eine Sache: `Fläche × Kennwert`. Das
ist das **Kennwertverfahren** — brauchbar für eine Hausnummer, untauglich für die
Auslegung eines einzelnen Heizkörpers. Dafür braucht es die Hüllflächen des
Raumes mit ihren U-Werten:

  * `room_surface` — Außenwand, Innenwand, Dachschräge, Decke, Boden. Jede Fläche
    weiß, **woran sie grenzt** (`adjacent`: AUSSENLUFT | ERDREICH | UNBEHEIZT |
    BEHEIZT). Eine Innenwand zum beheizten Nachbarraum verliert keine Wärme.
  * `room_opening` — Fenster und Türen. Sie hängen **an ihrer Wand**
    (`surface_id`), nicht am Raum: Ein Raum mit zwei Außenwänden hat sonst keine
    definierte Nettowandfläche, weil unklar bleibt, aus welcher Wand das Fenster
    ausgeschnitten wird.

### INVARIANTE: Ein Fenster ist nie größer als seine Wand.

`property.enforce_room_opening_fits` erzwingt das **physisch** — beim Anlegen und
Ändern einer Öffnung **und** beim Verkleinern der Wand (sonst schrumpfte man die
Wand einfach unter ihre Fenster). Ohne diese Regel entstünde eine **negative
Nettowandfläche**, und die Heizlast dieses Raumes wäre nicht bloß ungenau,
sondern vorzeichenverkehrt: Die Wand gewönne Wärme.

Der Trigger sperrt dazu eine gemeinsame Zeile und serialisiert damit gegen den
offensichtlichen Wettlauf: zwei gleichzeitig eingefügte Fenster, von denen jedes
für sich passt, zusammen aber nicht. **`FOR UPDATE`, nicht `FOR SHARE`** — eine
Share-Sperre bekämen beide Schreiber gleichzeitig, beide läsen den alten Stand,
und beide kämen durch.

**NACHTRAG (Migration 0089):** Diese Fassung deckt nur die Grenze **je Wand** ab.
Eine Öffnung **ohne** Wandzuordnung (`surface_id IS NULL`) umgeht sie und konnte
die Nettowandfläche des Raumes ins Negative drücken (Review-Fund, reproduziert).
0089 ersetzt die Funktion: Sie prüft zusätzlich **je Raum** (Σ aller Öffnungen ≤
Σ aller Bauteilflächen), deckt das Löschen einer Wand mit ab und hebt den
Serialisierungspunkt von der Wand- auf die **Raumzeile**. Die Aussage oben gilt
also erst mit 0089 vollständig.

### Was die DB NICHT tut: rechnen mit Normwerten

U-Werte, Norm-Innentemperaturen, Temperaturkorrekturfaktoren und
Luftwechselraten sind **Eingaben**, keine mitgelieferten Tabellen. Das ist keine
Bequemlichkeit, sondern die Normrechtslage (siehe HANDOFF, Welle 2/Punkt 11): Die
Anwendung einer Rechenvorschrift ist frei, das Abdrucken der Tabellenwerte einer
DIN-Norm ist es nicht. MCN liefert deshalb **keine** DIN-Klimadaten und **keine**
f-Faktoren mit.

Daraus folgt die zweite Invariante, und sie ist dieselbe wie beim fehlenden
Einkaufspreis (Aufschlagsmatrix, 0069) und beim § 35a-Ausweis (0076):

    Fehlt ein U-Wert oder ein Temperaturfaktor, ist die Heizlast
    **UNBEKANNT — nicht 0**.

Ein fehlender U-Wert als 0 gelesen hieße: „diese Wand verliert keine Wärme". Der
Rechner meldet stattdessen, welche Fläche ihm fehlt. `temp_factor` ist für
AUSSENLUFT per CHECK auf 1.0 festgenagelt (das ist keine Normtabelle, sondern die
Definition der Temperaturdifferenz), für alles andere ist er eine Eingabe des
Betriebs.

## Schutzstandard

`room` erbt den vollen Standard: `set_updated_at`, `audit_row_update`,
**No-Delete**, No-Truncate. Ein aufgenommener Raum wird nicht gelöscht, er wird
`INAKTIV` (Vorbild: `property.property.status`) — ein Objektaufmaß ist ein
Nachweis über den Bestand, und wer eine Wohnung zusammenlegt, hat den alten Raum
gehabt.

`room_surface` und `room_opening` bekommen **kein No-Delete** — dieselbe
dokumentierte Ausnahme wie `invoicing.quote_line` (0018) und
`workflow.site_report_line` (0080): Das sind **Detailzeilen**, die der Editor als
Satz ersetzt (Delete+Insert), weil ein Teil-Update bei umsortierten Zeilen nicht
eindeutig ist. Ein DELETE-Verbot machte das Streichen eines falsch getippten
Fensters unmöglich. TRUNCATE bleibt überall verboten (es umginge jeden
Row-Trigger).
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- property.room — der Raum als Objektstammdatum
-- ---------------------------------------------------------------------------
CREATE TABLE property.room (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id     uuid NOT NULL REFERENCES property.property (id),
    building_id     uuid NULL,
    unit_id         uuid NULL,
    -- Geschoss als freier Text (KG, EG, 1. OG, DG …). Eine Codeliste wäre hier
    -- falsch: Souterrain, Hochparterre, Spitzboden, Zwischengeschoss — der
    -- Bestand ist erfinderischer als jede Aufzählung.
    storey          text NULL CHECK (storey IS NULL OR btrim(storey) <> ''),
    name            text NOT NULL CHECK (btrim(name) <> ''),
    room_type       text NULL CHECK (room_type IS NULL OR room_type IN
                    ('WOHNEN', 'SCHLAFEN', 'KUECHE', 'BAD', 'WC', 'FLUR',
                     'TREPPENHAUS', 'KELLER', 'DACHBODEN', 'TECHNIK', 'BUERO',
                     'LAGER', 'GEWERBE', 'SONSTIGES')),

    -- Geometrie. Die Fläche ist die Wahrheit; Länge/Breite sind die (optionale)
    -- Herleitung für den Rechteckfall — siehe Modulkopf.
    floor_area_m2   numeric(10, 3) NOT NULL CHECK (floor_area_m2 > 0),
    length_m        numeric(8, 3) NULL CHECK (length_m IS NULL OR length_m > 0),
    width_m         numeric(8, 3) NULL CHECK (width_m IS NULL OR width_m > 0),
    room_height_m   numeric(8, 3) NOT NULL CHECK (room_height_m > 0 AND room_height_m <= 20),
    -- Umfang: gemessen, nicht abgeleitet (aus einer Fläche folgt kein Umfang).
    perimeter_m     numeric(10, 3) NULL CHECK (perimeter_m IS NULL OR perimeter_m > 0),
    -- Definition, keine Ermessensfrage → die DB rechnet, nicht der Client.
    volume_m3       numeric(13, 3) GENERATED ALWAYS AS
                    (round(floor_area_m2 * room_height_m, 3)) STORED,

    -- Heizlast-Eingaben. ALLES Eingaben des Betriebs, keine Normtabellen.
    indoor_temp_c        numeric(4, 1) NULL
                         CHECK (indoor_temp_c IS NULL OR
                                (indoor_temp_c >= -30 AND indoor_temp_c <= 60)),
    air_change_rate      numeric(4, 2) NULL
                         CHECK (air_change_rate IS NULL OR air_change_rate >= 0),
    -- Kennwert je Raum (W/m²) — Übersteuerung des Gebäudekennwerts für das
    -- überschlägige Verfahren.
    heat_load_w_per_m2   numeric(6, 1) NULL
                         CHECK (heat_load_w_per_m2 IS NULL OR heat_load_w_per_m2 > 0),
    -- Weg zur Steigleitung/zum Verteiler. Eingabe, damit eine Leitungslängen-
    -- SCHÄTZUNG auf einer gemessenen Zahl beruht statt auf einem erfundenen
    -- Faktor.
    riser_distance_m     numeric(8, 2) NULL
                         CHECK (riser_distance_m IS NULL OR riser_distance_m >= 0),

    status          text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    note            text NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    -- Zusammengesetzte FKs: der Raum kann nicht in einer fremden Liegenschaft
    -- hängen (Vorbild: property.technical_asset, 0004).
    FOREIGN KEY (building_id, property_id) REFERENCES property.building (id, property_id),
    FOREIGN KEY (unit_id, building_id)     REFERENCES property.unit (id, building_id),
    CONSTRAINT room_einheit_braucht_gebaeude CHECK (unit_id IS NULL OR building_id IS NOT NULL),
    -- NULLS NOT DISTINCT: ohne das griffe die Dublettensperre im Einfamilienhaus
    -- (keine Einheit, oft kein Geschoss) gar nicht — dem Regelfall.
    CONSTRAINT room_dublette UNIQUE NULLS NOT DISTINCT (property_id, unit_id, storey, name)
);

CREATE INDEX idx_room_property ON property.room (property_id);
CREATE INDEX idx_room_unit ON property.room (unit_id);

-- Ziel für den zusammengesetzten FK der Hüllfläche.
ALTER TABLE property.room ADD CONSTRAINT room_id_property_key UNIQUE (id, property_id);

CREATE TRIGGER trg_room_updated_at
    BEFORE UPDATE ON property.room
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_room_audit
    AFTER UPDATE ON property.room
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_room_no_delete
    BEFORE DELETE ON property.room
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_room_no_truncate
    BEFORE TRUNCATE ON property.room
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.room FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- property.room_surface — Hüllfläche (Bauteil) des Raumes
-- ---------------------------------------------------------------------------
CREATE TABLE property.room_surface (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id         uuid NOT NULL REFERENCES property.room (id),
    surface_type    text NOT NULL CHECK (surface_type IN
                    ('AUSSENWAND', 'INNENWAND', 'DACHSCHRAEGE', 'DECKE', 'BODEN')),
    -- Woran grenzt die Fläche? Das — nicht die Bauteilart — entscheidet, ob
    -- Wärme verloren geht: eine Innenwand zum unbeheizten Treppenhaus verliert,
    -- eine Außenwand gibt es nicht zum beheizten Nachbarn.
    adjacent        text NOT NULL CHECK (adjacent IN
                    ('AUSSENLUFT', 'ERDREICH', 'UNBEHEIZT', 'BEHEIZT')),
    orientation     text NULL CHECK (orientation IS NULL OR orientation IN
                    ('N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW')),
    label           text NULL,
    -- BRUTTOfläche: die Öffnungen (Fenster/Türen) hängen daran und werden
    -- abgezogen. Netto = gross_area_m2 - Σ opening.area_m2.
    gross_area_m2   numeric(10, 3) NOT NULL CHECK (gross_area_m2 > 0),
    u_value         numeric(5, 3) NULL CHECK (u_value IS NULL OR u_value > 0),
    -- Temperaturkorrekturfaktor. Eingabe des Betriebs (KEINE DIN-Tabelle im
    -- Produkt). Für AUSSENLUFT ist er per Definition 1.0 — das ist keine
    -- Normtabelle, sondern die Bedeutung von „volle Temperaturdifferenz".
    temp_factor     numeric(4, 2) NULL
                    CHECK (temp_factor IS NULL OR (temp_factor >= 0 AND temp_factor <= 1)),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT room_surface_aussenluft_faktor CHECK (
        adjacent <> 'AUSSENLUFT' OR temp_factor IS NULL OR temp_factor = 1.0
    ),
    -- Ziel für den zusammengesetzten FK der Öffnung.
    CONSTRAINT room_surface_id_room_key UNIQUE (id, room_id)
);

CREATE INDEX idx_room_surface_room ON property.room_surface (room_id);

CREATE TRIGGER trg_room_surface_updated_at
    BEFORE UPDATE ON property.room_surface
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_room_surface_audit
    AFTER UPDATE ON property.room_surface
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
-- Kein No-Delete (dokumentierte Ausnahme, siehe Modulkopf): Detailzeile.
CREATE TRIGGER trg_room_surface_no_truncate
    BEFORE TRUNCATE ON property.room_surface
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.room_surface FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- property.room_opening — Fenster/Tür IN einer Hüllfläche
-- ---------------------------------------------------------------------------
CREATE TABLE property.room_opening (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id         uuid NOT NULL REFERENCES property.room (id),
    -- Die Öffnung hängt an IHRER Wand. NULL ist erlaubt (reiner Mengenabzug,
    -- z. B. für Malerarbeiten, ohne Bauteilzuordnung) — dann zählt sie nur
    -- gegen die Wandfläche des Raumes, nicht gegen eine bestimmte Wand.
    surface_id      uuid NULL,
    opening_type    text NOT NULL CHECK (opening_type IN
                    ('FENSTER', 'DACHFENSTER', 'TUER_AUSSEN', 'TUER_INNEN', 'SONSTIGES')),
    label           text NULL,
    quantity        integer NOT NULL DEFAULT 1 CHECK (quantity > 0),
    width_m         numeric(6, 3) NOT NULL CHECK (width_m > 0),
    height_m        numeric(6, 3) NOT NULL CHECK (height_m > 0),
    u_value         numeric(5, 3) NULL CHECK (u_value IS NULL OR u_value > 0),
    area_m2         numeric(12, 3) GENERATED ALWAYS AS
                    (round(quantity * width_m * height_m, 3)) STORED,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    -- Zusammengesetzter FK: die Öffnung kann nicht in einer Wand sitzen, die zu
    -- einem anderen Raum gehört.
    FOREIGN KEY (surface_id, room_id) REFERENCES property.room_surface (id, room_id)
);

CREATE INDEX idx_room_opening_room ON property.room_opening (room_id);
CREATE INDEX idx_room_opening_surface ON property.room_opening (surface_id);

CREATE TRIGGER trg_room_opening_updated_at
    BEFORE UPDATE ON property.room_opening
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_room_opening_audit
    AFTER UPDATE ON property.room_opening
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
-- Kein No-Delete (dokumentierte Ausnahme, siehe Modulkopf): Detailzeile.
CREATE TRIGGER trg_room_opening_no_truncate
    BEFORE TRUNCATE ON property.room_opening
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.room_opening FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- INVARIANTE: Ein Fenster ist nie größer als seine Wand.
--
-- Ohne diese Regel entsteht eine NEGATIVE Nettowandfläche — die Wand gewönne
-- Wärme. Ein CHECK reicht dafür nicht: die Bedingung spannt über zwei Tabellen.
-- ---------------------------------------------------------------------------
CREATE FUNCTION property.enforce_room_opening_fits() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_surface_id uuid;
    v_gross      numeric(10, 3);
    v_belegt     numeric;
    v_label      text;
BEGIN
    -- Aufruf von room_opening (INSERT/UPDATE) ODER von room_surface (UPDATE).
    IF TG_TABLE_NAME = 'room_opening' THEN
        v_surface_id := NEW.surface_id;
        IF v_surface_id IS NULL THEN
            RETURN NEW;  -- freier Abzug ohne Bauteilzuordnung, siehe Spaltenkommentar
        END IF;
    ELSE
        v_surface_id := NEW.id;
    END IF;

    -- Die WANDZEILE ist der Serialisierungspunkt: Jede Öffnung dieser Wand und
    -- jede Änderung der Wand selbst nimmt hier dieselbe Sperre. FOR UPDATE, nicht
    -- FOR SHARE — zwei gleichzeitig eingefügte Fenster bekämen eine Share-Sperre
    -- beide, läsen beide den alten Stand und passten „jedes für sich".
    -- Beim Wand-UPDATE hält der Aufrufer die Zeile ohnehin schon (Selbst-Sperre).
    SELECT gross_area_m2, coalesce(label, surface_type)
      INTO v_gross, v_label
      FROM property.room_surface
     WHERE id = v_surface_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RETURN NEW;  -- FK meldet das gleich selbst
    END IF;

    -- Beim Wand-UPDATE gilt die NEUE Bruttofläche.
    IF TG_TABLE_NAME = 'room_surface' THEN
        v_gross := NEW.gross_area_m2;
    END IF;

    -- Summe der Öffnungen dieser Wand. Kein FOR UPDATE — das ist mit
    -- Aggregatfunktionen nicht erlaubt (und wäre überflüssig: die Sperre auf der
    -- Wandzeile oben serialisiert bereits, und READ COMMITTED gibt dieser
    -- Anweisung einen frischen Snapshot, sobald sie erteilt ist).
    -- Die eigene Zeile wird beim UPDATE ausgeschlossen und durch NEW ersetzt —
    -- sonst zählte sie doppelt.
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

CREATE TRIGGER trg_room_opening_passt
    BEFORE INSERT OR UPDATE ON property.room_opening
    FOR EACH ROW EXECUTE FUNCTION property.enforce_room_opening_fits();
-- Die Gegenrichtung: die Wand darf nicht unter ihre Fenster schrumpfen.
CREATE TRIGGER trg_room_surface_passt
    BEFORE UPDATE OF gross_area_m2 ON property.room_surface
    FOR EACH ROW EXECUTE FUNCTION property.enforce_room_opening_fits();

COMMENT ON TABLE property.room IS
    'Raum als Objektstammdatum (Aufmaß). Fläche/Höhe sind Pflicht, Volumen ist generiert.';
COMMENT ON COLUMN property.room.floor_area_m2 IS
    'Die Wahrheit. length_m/width_m sind nur die Herleitung im Rechteckfall.';
COMMENT ON COLUMN property.room.perimeter_m IS
    'Gemessener Umfang — aus einer Fläche folgt kein Umfang.';
COMMENT ON COLUMN property.room_surface.temp_factor IS
    'Temperaturkorrekturfaktor, Eingabe des Betriebs. Fehlt er, ist die Heizlast UNBEKANNT, nicht 0.';
COMMENT ON COLUMN property.room_surface.u_value IS
    'U-Wert W/(m²·K), Eingabe des Betriebs. Keine DIN-Tabellen im Produkt.';
COMMENT ON COLUMN property.room_opening.surface_id IS
    'Die Wand, in der die Öffnung sitzt. NULL = reiner Mengenabzug ohne Bauteilzuordnung.';
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_room_surface_passt ON property.room_surface;
DROP TRIGGER IF EXISTS trg_room_opening_passt ON property.room_opening;
DROP FUNCTION IF EXISTS property.enforce_room_opening_fits();
DROP TABLE IF EXISTS property.room_opening;
DROP TABLE IF EXISTS property.room_surface;
DROP TABLE IF EXISTS property.room;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0088_abrechnungsbindung_haerten")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
