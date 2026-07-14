"""Eine Kante trägt nur eine WAND — keine Decke, keinen Boden, keine Dachschräge.

Review-Fund am Grundriss-Slice, reproduziert über die API:

    PUT /rooms/{id}/aufbau
    { "surface_type": "DECKE", "adjacent": "UNBEHEIZT", "edge_index": 0 }   # ohne Fläche

Der Service prüfte `edge_index` nur gegen die **Anzahl** der Kanten, nie gegen die
**Bauteilart**. Also leitete er brav ab: Deckenfläche = Kantenlänge × Raumhöhe =
5,00 m × 2,50 m = 12,50 m². Die Decke dieses Raumes ist **20 m²**.

Zwei Fehler in einem:
  * Die Transmission rechnet ab sofort mit einer um 37 % zu kleinen Decke.
  * Schlimmer noch: Die Fläche ist als `area_is_derived` markiert und **folgt
    fortan der Raumhöhe** — eine Decke, die größer wird, wenn der Raum höher wird.

Der Modulkopf von 0093 formuliert die Regel bereits ausdrücklich („Eine Decke hat
keine Kantenlänge, aus der sich etwas ableiten ließe") — aber er formulierte sie
nur in Prosa. Der CHECK dort erzwang lediglich `area_is_derived → edge_index IS
NOT NULL`, also die eine Richtung.

Der Angular-Client hielt die Regel von sich aus ein. Das ist genau **kein**
Argument: Nach der Vision geht die KI durch **dieselben Tore wie ein Mensch** —
und ihr Weg ist der Service, nicht das Formular. Eine Regel, die nur ein Client
kennt, ist keine Regel, sondern eine Gewohnheit.

Deshalb steht sie jetzt da, wo sie hingehört: als CHECK auf der Zeile.

    edge_index IS NULL  ODER  surface_type IN ('AUSSENWAND', 'INNENWAND')

Ein Polygon ist die Draufsicht: Seine Kanten sind die **senkrechten** Bauteile.
Decke und Boden liegen über bzw. unter der Fläche, die Dachschräge steht schief
darüber — für sie alle ist die Grundfläche die Bezugsgröße, nicht eine Kante.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE property.room_surface
    ADD CONSTRAINT room_surface_kante_nur_an_der_wand CHECK (
        edge_index IS NULL OR surface_type IN ('AUSSENWAND', 'INNENWAND')
    );

COMMENT ON COLUMN property.room_surface.edge_index IS
    'Polygonkante (vertex i -> i+1), auf der diese Wand steht. Nur an AUSSENWAND/'
    'INNENWAND zulässig: Decke, Boden und Dachschräge stehen auf keiner Kante der '
    'Draufsicht — ihre Bezugsgröße ist die Grundfläche.';
"""

REVERSE_SQL = r"""
ALTER TABLE property.room_surface
    DROP CONSTRAINT room_surface_kante_nur_an_der_wand;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0093_wandflaeche_abgeleitet")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
