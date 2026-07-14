"""Grundriss: der Raum bekommt einen Umriss (property.room_vertex).

Bis hierher war das Aufmaß eine Zahlenliste: Fläche, Höhe, Umfang — jede Zahl von
Hand eingetippt, jede Wand einzeln angelegt, und **nichts davon wusste voneinander**.
Der Bediener konnte 20 m² Fläche mit 4 m Umfang eintragen (geometrisch unmöglich),
und niemand merkte es.

Der Umriss dreht das um: **Man zeichnet den Raum, und die Zahlen fallen heraus.**

    Polygon  →  Fläche (Gauß'sche Trapezformel)
             →  Umfang (Summe der Kantenlängen)
             →  je Kante EINE Wand, ihre Bruttofläche = Kantenlänge × Raumhöhe

Damit ist der Grundriss keine hübsche Beigabe, sondern die **Quelle** der
Kennzahlen — und die Vorstufe zur 3D-Planung: Polygon + `room_height_m` ist ein
extrudierbarer Körper.

## Koordinaten: Millimeter, ganzzahlig, je Geschoss

`x_mm`/`y_mm` sind `integer` in **Millimetern**, nicht `numeric` in Metern. Ein
Grundriss wird gezeichnet und gefangen (Raster/Snap); Gleitkomma-Koordinaten
erzeugen dabei Kanten, die „fast" aufeinander liegen, und Flächenberechnungen,
die je nach Rundung um Quadratzentimeter wandern. Millimeter sind für den Bau
exakt genug und **vergleichbar** — zwei Räume, die an derselben Wand liegen,
haben dann wirklich dieselben Punkte.

Die Koordinaten gelten **je Geschoss** (nicht je Raum): Alle Räume eines
`storey` liegen im selben System. Deshalb ergibt sich die Etagenübersicht ohne
weitere Daten — man zeichnet einfach alle Räume des Geschosses.

## Der Umriss ist optional — und dann ist er die Wahrheit

Ein Raum **ohne** Umriss bleibt gültig (Zahlen von Hand, wie bisher: der
Bestand ändert sich nicht, und nicht jeder Raum muss gezeichnet werden).

Hat ein Raum einen Umriss, gilt: **Wer zeichnet, misst nicht doppelt.**
`floor_area_m2` und `perimeter_m` werden dann vom Server **aus dem Polygon
gerechnet** und geschrieben — sie bleiben die einzige Wahrheit in ihrer Spalte
(kein zweiter Satz Zahlen, der auseinanderlaufen kann). Die Felder sind im UI
dann nicht mehr frei tippbar, sondern zeigen das Ergebnis der Zeichnung.

Die Fläche wird als **Betrag** der Trapezformel gerechnet — der Umlaufsinn
(im/gegen den Uhrzeigersinn) darf keine negative Fläche erzeugen.

## Kante ⇄ Wand: `room_surface.edge_index`

`edge_index = i` heißt: Diese Hüllfläche ist die Wand über der Kante von Punkt
`i` nach Punkt `i+1` (mit Umlauf zum Anfang). Der partielle UNIQUE-Index
verhindert **zwei Wände auf derselben Kante** — sonst zählte dieselbe Fläche
doppelt in die Heizlast.

`edge_index` ist NULL-fähig: Decke, Boden und Dachschräge haben keine Kante
(sie liegen über bzw. unter dem Polygon), und eine von Hand angelegte Wand ohne
Zeichnung ebenfalls. **Es gibt keinen FK auf die Kante** — sie ist keine Zeile,
sondern das Paar (vertex[i], vertex[i+1]). Die Gültigkeit von `edge_index`
gegen die Punktzahl prüft der Service beim Schreiben des Umrisses; ein
CHECK könnte das nicht (er sähe nur seine eigene Zeile).

## Öffnung in der Wand: `room_opening.position_m`

Der Abstand der linken Öffnungskante vom **Anfangspunkt** ihrer Kante. Damit
wird das Fenster maßstäblich gezeichnet — und es ist die Information, die eine
spätere Rohrnetz- oder 3D-Planung braucht (wo genau sitzt die Öffnung, wo kann
die Leitung entlang).

`position_m` ist NULL-fähig: Ein Fenster darf erfasst sein, ohne dass jemand
seine Lage in der Wand ausgemessen hat. Es zählt dann ganz normal in Fläche und
Heizlast — es wird nur **nicht gezeichnet**. Auch hier gilt die Hausregel:
Fehlende Lage heißt *unbekannt*, nicht *bei 0 m*.

Dass die Öffnung in ihre Kante **passt** (`position + Breite ≤ Kantenlänge`),
prüft der Service; die flächenmäßige Grenze (`Σ Öffnungen ≤ Wandfläche`) erzwingt
weiterhin die DB (`enforce_room_opening_fits`, 0086/0089).
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE property.room_vertex (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id    uuid NOT NULL REFERENCES property.room (id),
    -- Reihenfolge des Umlaufs. Kante i = (vertex i -> vertex i+1), zyklisch.
    idx        integer NOT NULL CHECK (idx >= 0),
    -- Millimeter, ganzzahlig, im Koordinatensystem des GESCHOSSES (siehe Modulkopf).
    x_mm       integer NOT NULL,
    y_mm       integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (room_id, idx),
    -- Zwei Punkte dürfen nicht aufeinander liegen (Kante der Länge 0 → Wand ohne
    -- Fläche, und der Umriss wäre entartet).
    UNIQUE (room_id, x_mm, y_mm)
);

CREATE INDEX idx_room_vertex_room ON property.room_vertex (room_id, idx);

CREATE TRIGGER trg_room_vertex_updated_at
    BEFORE UPDATE ON property.room_vertex
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_room_vertex_audit
    AFTER UPDATE ON property.room_vertex
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
-- Kein No-Delete: Der Umriss ist eine Detailzeilenmenge, die der Editor als Satz
-- ersetzt (dieselbe dokumentierte Ausnahme wie room_surface/room_opening, 0086).
CREATE TRIGGER trg_room_vertex_no_truncate
    BEFORE TRUNCATE ON property.room_vertex
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.room_vertex FROM PUBLIC;

-- Welche Polygonkante trägt diese Wand?
ALTER TABLE property.room_surface
    ADD COLUMN edge_index integer NULL CHECK (edge_index IS NULL OR edge_index >= 0);

-- Zwei Wände auf derselben Kante würden dieselbe Fläche doppelt in die Heizlast
-- zählen.
CREATE UNIQUE INDEX uq_room_surface_edge
    ON property.room_surface (room_id, edge_index)
    WHERE edge_index IS NOT NULL;

-- Abstand der linken Öffnungskante vom Anfangspunkt ihrer Kante.
ALTER TABLE property.room_opening
    ADD COLUMN position_m numeric(6, 3) NULL
        CHECK (position_m IS NULL OR position_m >= 0);

COMMENT ON TABLE property.room_vertex IS
    'Umriss des Raumes als Polygon (mm, Koordinatensystem des Geschosses). Hat ein Raum '
    'einen Umriss, sind floor_area_m2 und perimeter_m daraus gerechnet.';
COMMENT ON COLUMN property.room_surface.edge_index IS
    'Polygonkante (vertex i -> i+1), auf der diese Wand steht. NULL = Decke/Boden/'
    'Dachschräge oder von Hand angelegte Wand ohne Zeichnung.';
COMMENT ON COLUMN property.room_opening.position_m IS
    'Abstand vom Anfangspunkt der Kante. NULL = Lage nicht ausgemessen (die Öffnung '
    'zählt trotzdem in Fläche und Heizlast, sie wird nur nicht gezeichnet).';
"""

REVERSE_SQL = r"""
ALTER TABLE property.room_opening DROP COLUMN position_m;
DROP INDEX IF EXISTS property.uq_room_surface_edge;
ALTER TABLE property.room_surface DROP COLUMN edge_index;
DROP TABLE IF EXISTS property.room_vertex;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0090_bauteilkatalog")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
