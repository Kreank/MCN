"""Anlagenart auf die SHK-Codeliste des Betriebs umstellen.

Die Anlagenart aus 0101 war eine allgemeine Gebäudetechnik-Liste (HEIZUNG,
THERME, WARMWASSER, WAERMEPUMPE, SOLARTHERMIE, LUEFTUNG, KLIMA, TRINKWASSER,
HEBEANLAGE, AUFZUG, BRANDSCHUTZ, ELEKTRO, SONSTIGE). Der Betrieb ist ein
**SHK-Fachbetrieb** (Heizung/Sanitär) und führt seine Anlagen nach genau neun
Arten, die die Einsatzrealität abbilden:

    Therme Heizung, Therme Combi, Erdwärmepumpe, Fernwärmestation,
    Kessel Heizung, Kessel Combi, Hebeanlage, Solaranlage, Sonstiges.

Neue Codes: THERME_HEIZUNG, THERME_COMBI, ERDWAERMEPUMPE, FERNWAERMESTATION,
KESSEL_HEIZUNG, KESSEL_COMBI, HEBEANLAGE, SOLARANLAGE, SONSTIGE.

## Warum diese Zuordnung der Bestandsdaten (alt → neu)

Die neue Liste unterscheidet **Therme vs. Kessel** und **Heizung vs. Combi**
(Heizung + Warmwasser in einem Gerät) — Unterscheidungen, die die alte Liste
nicht kannte. Wo die alte Art die Bauform nicht hergab, wird auf die
wahrscheinlichere Grundform abgebildet; die feineren Neuwerte (THERME_COMBI,
KESSEL_COMBI, FERNWAERMESTATION) entstehen erst durch neue Erfassung, nicht durch
Raten aus zu grober Altdatenlage.

* **THERME → THERME_HEIZUNG.** Eine „Therme" ohne weitere Angabe ist die
  Grundform; Combi ist die engere Aussage, die die Altdaten nicht belegen.
* **HEIZUNG → KESSEL_HEIZUNG.** Die generische „Heizung" des Altbestands ist im
  SHK-Alltag der (Heiz-)Kessel; als Kessel-Heizung eingeordnet.
* **WAERMEPUMPE → ERDWAERMEPUMPE.** Die neue Liste kennt nur die Erdwärmepumpe;
  der Altbestand wird darauf abgebildet (Sonstiges wäre unschärfer).
* **SOLARTHERMIE → SOLARANLAGE.** Direkte fachliche Entsprechung.
* **HEBEANLAGE → HEBEANLAGE.** Unverändert übernommen.
* **WARMWASSER, LUEFTUNG, KLIMA, TRINKWASSER, AUFZUG, BRANDSCHUTZ, ELEKTRO →
  SONSTIGE.** Diese Arten hat die SHK-Liste bewusst nicht mehr; sie fallen ins
  ausdrückliche Sammelbecken. `SONSTIGE` bleibt das Ventil (0101).

Der CHECK trägt weiter den Namen `technical_asset_type_check` — der Service
(`services/anlage._db_fehler`) mappt genau diesen Namen auf einen sauberen 422.

## Reihenfolge

Erst den alten CHECK lösen (sonst verletzten die neuen Codes ihn schon beim
UPDATE), dann die Bestandsdaten mappen, dann den neuen CHECK setzen. `asset_type`
bleibt NOT NULL — daran ändert sich nichts.

## Rückwärts

Best-effort-Rückmapping auf die alte Liste; die neuen Feinunterscheidungen
werden dabei auf die alte Grundform zurückgeführt (THERME_COMBI → THERME,
KESSEL_COMBI → HEIZUNG, FERNWAERMESTATION → HEIZUNG). Der Rückweg ist **verlustig
gegenüber dem Original**: Was vorwärts nach SONSTIGE zusammenlief (Warmwasser,
Lüftung, Klima, Trinkwasser, Aufzug, Brandschutz, Elektro), lässt sich nicht mehr
auseinanderhalten und bleibt SONSTIGE. Das ist die ehrliche Grenze einer
Codelisten-Verengung; der Rückweg stellt den CHECK und die grobe Einordnung
wieder her, nicht die verlorene Feinheit.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- Alten CHECK lösen: die neuen Codes verletzten ihn sonst schon beim UPDATE.
ALTER TABLE property.technical_asset
    DROP CONSTRAINT technical_asset_type_check;

-- Bestandsdaten alt → neu (Begründung im Modul-Docstring).
UPDATE property.technical_asset SET asset_type = CASE asset_type
    WHEN 'THERME'       THEN 'THERME_HEIZUNG'
    WHEN 'HEIZUNG'      THEN 'KESSEL_HEIZUNG'
    WHEN 'WAERMEPUMPE'  THEN 'ERDWAERMEPUMPE'
    WHEN 'SOLARTHERMIE' THEN 'SOLARANLAGE'
    WHEN 'HEBEANLAGE'   THEN 'HEBEANLAGE'
    -- WARMWASSER, LUEFTUNG, KLIMA, TRINKWASSER, AUFZUG, BRANDSCHUTZ, ELEKTRO
    -- und alles sonst → das ausdrückliche Sammelbecken.
    ELSE 'SONSTIGE'
END
WHERE asset_type IN
    ('THERME', 'HEIZUNG', 'WAERMEPUMPE', 'SOLARTHERMIE', 'WARMWASSER',
     'LUEFTUNG', 'KLIMA', 'TRINKWASSER', 'AUFZUG', 'BRANDSCHUTZ', 'ELEKTRO');

-- Neuer CHECK mit der SHK-Codeliste.
ALTER TABLE property.technical_asset
    ADD CONSTRAINT technical_asset_type_check CHECK (asset_type IN
        ('THERME_HEIZUNG', 'THERME_COMBI', 'ERDWAERMEPUMPE', 'FERNWAERMESTATION',
         'KESSEL_HEIZUNG', 'KESSEL_COMBI', 'HEBEANLAGE', 'SOLARANLAGE',
         'SONSTIGE'));
"""

REVERSE_SQL = r"""
ALTER TABLE property.technical_asset
    DROP CONSTRAINT technical_asset_type_check;

-- Best-effort zurück auf die alte Liste. Verlustig: was vorwärts nach SONSTIGE
-- zusammenlief, bleibt SONSTIGE (die Feinheit ist nicht mehr rekonstruierbar).
UPDATE property.technical_asset SET asset_type = CASE asset_type
    WHEN 'THERME_HEIZUNG'    THEN 'THERME'
    WHEN 'THERME_COMBI'      THEN 'THERME'
    WHEN 'ERDWAERMEPUMPE'    THEN 'WAERMEPUMPE'
    WHEN 'FERNWAERMESTATION' THEN 'HEIZUNG'
    WHEN 'KESSEL_HEIZUNG'    THEN 'HEIZUNG'
    WHEN 'KESSEL_COMBI'      THEN 'HEIZUNG'
    WHEN 'SOLARANLAGE'       THEN 'SOLARTHERMIE'
    WHEN 'HEBEANLAGE'        THEN 'HEBEANLAGE'
    ELSE 'SONSTIGE'
END
WHERE asset_type IN
    ('THERME_HEIZUNG', 'THERME_COMBI', 'ERDWAERMEPUMPE', 'FERNWAERMESTATION',
     'KESSEL_HEIZUNG', 'KESSEL_COMBI', 'SOLARANLAGE');

ALTER TABLE property.technical_asset
    ADD CONSTRAINT technical_asset_type_check CHECK (asset_type IN
        ('HEIZUNG', 'THERME', 'WARMWASSER', 'WAERMEPUMPE', 'SOLARTHERMIE',
         'LUEFTUNG', 'KLIMA', 'TRINKWASSER', 'HEBEANLAGE', 'AUFZUG',
         'BRANDSCHUTZ', 'ELEKTRO', 'SONSTIGE'));
"""


class Migration(migrations.Migration):
    dependencies = [("db_core", "0111_ids_preis_semantik")]
    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
