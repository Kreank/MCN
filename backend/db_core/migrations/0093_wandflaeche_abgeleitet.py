"""Die abgeleitete Wandfläche weiß, dass sie abgeleitet ist.

Mit dem Grundriss (0091) kann eine Wand auf einer Polygonkante stehen; ihre
Bruttofläche ist dann **Kantenlänge × Raumhöhe** und wird beim Speichern
gerechnet, statt getippt.

Nur: Danach steht sie als Zahl in `gross_area_m2` — und **niemand weiß mehr, dass
sie gerechnet war**. Die Folge ist ein stiller Fehler mit voller Wucht:

    Raumhöhe von 2,50 m auf 2,80 m korrigiert
      → alle Kantenwände behalten ihre alte Fläche
      → die Heizlast rechnet weiter mit 2,50 m
      → das Ergebnis sieht völlig normal aus. Es ist nur falsch.

Dasselbe beim Nachzeichnen des Umrisses: Die Wand steht auf einer Kante, die
inzwischen 4,37 m lang ist, und trägt die Fläche der alten 4,00-m-Kante.

Ein pauschales „bei jeder Änderung alles nachrechnen" verbietet sich, weil die
Übersteuerung ein **legitimer Fachfall** ist: Die Giebelwand ist ein Dreieck, der
Erker springt vor, die Dachschräge frisst die halbe Wand. Wer dort eine Fläche
von Hand einträgt, will sie behalten — ein Nachrechnen zerstörte sie
klammheimlich.

Beides zugleich geht nur, wenn die Zeile **selbst weiß**, woher ihr Wert stammt:

    area_is_derived = true   →  gross_area_m2 = Kantenlänge × Raumhöhe.
                               Der Server rechnet sie bei JEDER Änderung von
                               Umriss oder Raumhöhe neu. Sie ist kein Datum,
                               sondern ein Ergebnis.

    area_is_derived = false  →  Handeingabe. Wird NIE überschrieben.

Dieselbe Unterscheidung wie beim § 35a-Arbeitskostenanteil (0076): „abgeleitet"
vs. „abweichend angegeben" — und aus demselben Grund. Dort ging es darum, dass
ein abgeleiteter Wert nach einer Mengenänderung nicht falsch erstarrt; hier
darum, dass er nach einer Höhenänderung nicht falsch erstarrt. Es ist derselbe
Fehler, und er hat schon einmal 600 € Lohn auf eine 1.200-€-Position geschrieben.

Der CHECK erzwingt die einzige sinnvolle Kopplung: **Abgeleitet gibt es nur auf
einer Kante.** Eine Decke hat keine Kantenlänge, aus der sich etwas ableiten
ließe.

Bestandszeilen bekommen `false` — sie sind sämtlich von Hand erfasst (der
Grundriss ist neuer als sie). Das ist die sichere Richtung: Es wird nie etwas
überschrieben, was der Betrieb eingetragen hat.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE property.room_surface
    ADD COLUMN area_is_derived boolean NOT NULL DEFAULT false;

-- Abgeleitet werden kann nur, was auf einer Kante steht: Decke, Boden und
-- Dachschräge haben keine Kantenlänge.
ALTER TABLE property.room_surface
    ADD CONSTRAINT room_surface_abgeleitet_nur_auf_kante CHECK (
        NOT area_is_derived OR edge_index IS NOT NULL
    );

COMMENT ON COLUMN property.room_surface.area_is_derived IS
    'true = gross_area_m2 ist gerechnet (Kantenlänge × Raumhöhe) und wird bei jeder '
    'Änderung von Umriss oder Raumhöhe NEU gerechnet. false = Handeingabe, wird nie '
    'überschrieben (Giebel, Erker, Dachschräge).';
"""

REVERSE_SQL = r"""
ALTER TABLE property.room_surface
    DROP CONSTRAINT room_surface_abgeleitet_nur_auf_kante;
ALTER TABLE property.room_surface DROP COLUMN area_is_derived;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0092_componenttemplate_roomvertex")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
