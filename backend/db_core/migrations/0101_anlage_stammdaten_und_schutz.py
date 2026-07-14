"""Die technische Anlage wird eine echte Fachentität: Spalten, CHECKs, Schutz.

`property.technical_asset` liegt seit **0004** in der Datenbank und war bis zum
Anlagen-Slice **totes Schema**: kein Service, kein Endpunkt, keine Zeile. Mit dem
Slice bekommt sie erstmals einen Schreibpfad — und damit greifen die eisernen
Regeln des Repos, die für eine tote Tabelle nie greifen mussten.

Diese Migration zahlt drei Schulden auf einmal (alle drei sind Review-Funde).

## 1. Der Schutzstandard (CLAUDE.md: „Neue Fachtabellen erben den Schutzstandard")

Die Tabelle trug bisher **nur** `trg_technical_asset_updated_at`. Zum Vergleich
`property.room` (0086): `set_updated_at` **+ `audit_row_update` + `no_delete` +
`no_truncate` + `REVOKE TRUNCATE`**. Bis heute waren Anlagenänderungen also
**nicht auditiert**, und ein `DELETE` war physisch möglich — der Schutz „stilllegen
statt löschen" lebte allein davon, dass der Service keine Löschfunktion anbietet.

Das ist genau die Lehre aus Welle 5: **Was im Service sitzt, ist umgehbar; erst
was im Trigger sitzt, hält.** Ein Auftrag, eine Prüfung und ein Baustellenbericht
zeigen auf die Anlage; verschwindet ihre Zeile, zeigen sie ins Leere. Ab hier
verhindert das die **Datenbank**, nicht der gute Wille des Aufrufers.

## 2. Echte Spalten statt Freitext-JSON

Der erste Durchgang legte Hersteller, Modell, Baujahr, Seriennummer und Status in
das `attributes jsonb` — nicht aus Überzeugung, sondern weil der Migrationsgraph
gesperrt war. Das ist vertretbar, solange niemand darauf **sucht**. Genau das ist
aber der nächste Schritt: Der Betrieb will zu einer konkreten **Vaillant-Therme**
das passende **Ersatzteil** finden, und die DATANORM-Kataloge (Junkers/Bosch,
Vaillant) liegen dafür bereit. Ein Join oder Index auf einen JSON-Schlüssel, in
den jeder Aufrufer alles schreiben darf, trägt das nicht: „Vaillant", „VAILLANT"
und „Vailant" wären drei Hersteller, und **keine** Datenbank könnte es merken.

Deshalb: echte Spalten mit echten CHECKs und Indizes. `attributes` bleibt — aber
für das, wofür ein JSONB da ist: **echte Zusatzfakten**, die kein Feld haben
(Anlagenbuch-Nummer des Verwalters, Kesselnummer des Schornsteinfegers …). Die
fünf Fakten, an denen das Produkt hängt, sind keine Zusatzfakten.

**`supply_type` (zentral/dezentral) wird ebenfalls Spalte** — obwohl der Review
sie nicht nennt. Sie ist der fachliche Grund für diesen ganzen Slice („Mieter
meldet Heizkörper kalt — ist es eine zentrale Anlage?"). Ein Fakt, der einen
Einsatz entscheidet, gehört nicht in einen Sack, in dem alles landen darf.

**`UNBEKANNT` ist ein zulässiger Wert und der DEFAULT.** Nicht erfasst heißt
nicht „dezentral" — dieselbe Haltung wie beim fehlenden Einkaufspreis und beim
fehlenden U-Wert: unbekannt ist unbekannt, nie ein stillschweigend geratener Wert.

**`power_kw` trägt `CHECK (power_kw > 0)`, nicht `>= 0`.** 0 kW hieße „heizt
nicht". Eine unbekannte Leistung bleibt `NULL`. Das ist dieselbe Grenze wie in
`services/abrechnung._ist_preis` — und sie liegt hier bewusst **im CHECK**, nicht
nur im Service (siehe oben).

## 3. `asset_type` bekommt eine CHECK-Codeliste — begründet

Bisher: freies `text`, NULL-fähig, **ohne CHECK**. Die Entscheidung, das zu
schließen, ist nicht selbstverständlich (eine Codeliste schnürt eine Domäne ein,
und Gebäudetechnik ist breit), deshalb die Begründung:

* **Das Repo führt Codelisten als CHECK.** `property_type`, `unit_type`,
  `room_type`, `line_type` — alle. Eine Ausnahme hier hieße: zwei Sorten
  Codeliste, und die schwächere gewinnt beim nächsten Import.
* **Die Kosten einer Erweiterung sind belegt niedrig.** `EINFAMILIENHAUS` kam mit
  0048 als **eine Zeile** hinzu. Ein CHECK ist keine Einbahnstraße.
* **Der Schaden ohne CHECK ist konkret.** Die Anlagenart steuert Anzeige,
  Gruppierung und (bald) die Ersatzteilsuche. Ein DATANORM-Import oder ein
  KI-Vorschlag mit `asset_type='Heizungsanlage (Gas)'` erzeugt eine Gruppe, die
  im UI namenlos bleibt — und niemand merkt es, weil nichts fehlschlägt.
* **`SONSTIGE` ist das Ventil.** Was in keine Schublade passt, hat eine.

`asset_type` wird zugleich **NOT NULL**: Eine Anlage, deren Art niemand kennt,
hilft dem Monteur nicht (der Service verlangt sie ohnehin schon). Bestandszeilen
ohne Art bekommen `SONSTIGE`.

## Bestandsdaten

Alles, was der erste Durchgang nach `attributes` geschrieben hat, wird in die
Spalten **übernommen** und danach aus dem JSON **entfernt** — sonst stünde jeder
Wert zweimal da, und die zwei Kopien liefen auseinander. Werte, die die neuen
CHECKs nicht bestehen (aus der Zeit ohne CHECK), fallen dabei auf `NULL` bzw. den
Default; sie gehen nicht verloren, sondern wandern nach `attributes.migration_rest`.

## Rückwärts

`reverse_sql` stellt den Zustand vor dieser Migration wieder her — inklusive
Rückschreiben der Spalten nach `attributes`. Es gibt hier **keinen Grund für
noop**: Der Weg zurück verliert nichts (das JSON war die vorherige Wahrheit), und
eine Migration, deren Rückweg lügt, ist schlimmer als keine.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Spalten
-- ---------------------------------------------------------------------------
ALTER TABLE property.technical_asset
    ADD COLUMN status        text NOT NULL DEFAULT 'AKTIV',
    ADD COLUMN supply_type   text NOT NULL DEFAULT 'UNBEKANNT',
    ADD COLUMN manufacturer  text NULL,
    ADD COLUMN model         text NULL,
    ADD COLUMN serial_number text NULL,
    ADD COLUMN year_built    integer NULL,
    ADD COLUMN energy_source text NULL,
    ADD COLUMN power_kw      numeric(7, 2) NULL,
    ADD COLUMN location_note text NULL,
    ADD COLUMN note          text NULL;

-- ---------------------------------------------------------------------------
-- 2. Bestandsdaten aus `attributes` übernehmen (vor den CHECKs!)
--    Nur Werte, die die künftigen CHECKs bestehen — der Rest wird nicht
--    stillschweigend verbogen, sondern unter `migration_rest` aufgehoben.
-- ---------------------------------------------------------------------------
UPDATE property.technical_asset SET
    status = CASE
        WHEN attributes->>'status' IN ('AKTIV', 'INAKTIV')
        THEN attributes->>'status' ELSE 'AKTIV' END,
    supply_type = CASE
        WHEN attributes->>'versorgung' IN ('ZENTRAL', 'DEZENTRAL', 'UNBEKANNT')
        THEN attributes->>'versorgung' ELSE 'UNBEKANNT' END,
    manufacturer  = NULLIF(btrim(COALESCE(attributes->>'hersteller', '')), ''),
    model         = NULLIF(btrim(COALESCE(attributes->>'modell', '')), ''),
    serial_number = NULLIF(btrim(COALESCE(attributes->>'seriennummer', '')), ''),
    location_note = NULLIF(btrim(COALESCE(attributes->>'standort', '')), ''),
    note          = NULLIF(btrim(COALESCE(attributes->>'notiz', '')), ''),
    year_built = CASE
        WHEN attributes->>'baujahr' ~ '^[0-9]{4}$'
         AND (attributes->>'baujahr')::int BETWEEN 1850 AND 2100
        THEN (attributes->>'baujahr')::int ELSE NULL END,
    energy_source = CASE
        WHEN attributes->>'energietraeger' IN
             ('GAS', 'OEL', 'FERNWAERME', 'STROM', 'PELLET', 'HOLZ', 'SOLAR',
              'UMWELTWAERME', 'SONSTIGE')
        THEN attributes->>'energietraeger' ELSE NULL END,
    power_kw = CASE
        WHEN attributes->>'leistung_kw' ~ '^[0-9]+(\.[0-9]+)?$'
         AND (attributes->>'leistung_kw')::numeric > 0
         AND (attributes->>'leistung_kw')::numeric <= 99999.99
        THEN round((attributes->>'leistung_kw')::numeric, 2) ELSE NULL END
WHERE attributes <> '{}'::jsonb;

-- Die zehn bekannten Schlüssel wandern aus dem JSON heraus (sonst stünde jeder
-- Wert zweimal da). FREMDE Schlüssel bleiben unangetastet — dafür ist das JSONB da.
--
-- Was einen der neuen CHECKs NICHT bestanden hätte, geht dabei nicht verloren,
-- sondern landet unter `migration_rest`. Betroffen sein können nur die fünf
-- geprüften Schlüssel; die Freitextfelder können nur „leer" scheitern, und ein
-- leerer Text ist kein Verlust. Ist nichts übrig, entsteht auch kein
-- `migration_rest` (das äußere jsonb_strip_nulls wirft den NULL-Schlüssel weg).
UPDATE property.technical_asset SET
    attributes = (
        attributes
            - 'status' - 'versorgung' - 'hersteller' - 'modell' - 'seriennummer'
            - 'standort' - 'notiz' - 'baujahr' - 'energietraeger' - 'leistung_kw'
    ) || jsonb_strip_nulls(jsonb_build_object('migration_rest',
        NULLIF(jsonb_strip_nulls(jsonb_build_object(
            'status', CASE
                WHEN jsonb_exists(attributes, 'status')
                 AND attributes->>'status' NOT IN ('AKTIV', 'INAKTIV')
                THEN attributes->'status' END,
            'versorgung', CASE
                WHEN jsonb_exists(attributes, 'versorgung')
                 AND attributes->>'versorgung' NOT IN
                     ('ZENTRAL', 'DEZENTRAL', 'UNBEKANNT')
                THEN attributes->'versorgung' END,
            'baujahr', CASE
                WHEN jsonb_exists(attributes, 'baujahr') AND year_built IS NULL
                THEN attributes->'baujahr' END,
            'energietraeger', CASE
                WHEN jsonb_exists(attributes, 'energietraeger') AND energy_source IS NULL
                THEN attributes->'energietraeger' END,
            'leistung_kw', CASE
                WHEN jsonb_exists(attributes, 'leistung_kw') AND power_kw IS NULL
                THEN attributes->'leistung_kw' END
        )), '{}'::jsonb)))
WHERE attributes <> '{}'::jsonb;

-- Anlagenart: Bestandszeilen ohne (oder mit unbekannter) Art werden SONSTIGE.
UPDATE property.technical_asset
   SET asset_type = 'SONSTIGE'
 WHERE asset_type IS NULL
    OR asset_type NOT IN
       ('HEIZUNG', 'THERME', 'WARMWASSER', 'WAERMEPUMPE', 'SOLARTHERMIE',
        'LUEFTUNG', 'KLIMA', 'TRINKWASSER', 'HEBEANLAGE', 'AUFZUG',
        'BRANDSCHUTZ', 'ELEKTRO', 'SONSTIGE');

-- ---------------------------------------------------------------------------
-- 3. CHECKs (jetzt, wo die Daten passen)
-- ---------------------------------------------------------------------------
ALTER TABLE property.technical_asset
    ALTER COLUMN asset_type SET NOT NULL,
    ADD CONSTRAINT technical_asset_type_check CHECK (asset_type IN
        ('HEIZUNG', 'THERME', 'WARMWASSER', 'WAERMEPUMPE', 'SOLARTHERMIE',
         'LUEFTUNG', 'KLIMA', 'TRINKWASSER', 'HEBEANLAGE', 'AUFZUG',
         'BRANDSCHUTZ', 'ELEKTRO', 'SONSTIGE')),
    ADD CONSTRAINT technical_asset_status_check CHECK (status IN ('AKTIV', 'INAKTIV')),
    ADD CONSTRAINT technical_asset_supply_check CHECK (supply_type IN
        ('ZENTRAL', 'DEZENTRAL', 'UNBEKANNT')),
    ADD CONSTRAINT technical_asset_energy_check CHECK (energy_source IS NULL OR
        energy_source IN ('GAS', 'OEL', 'FERNWAERME', 'STROM', 'PELLET', 'HOLZ',
                          'SOLAR', 'UMWELTWAERME', 'SONSTIGE')),
    -- Leerer Text ist kein Wert (Repo-Standard: btrim(...) <> '').
    ADD CONSTRAINT technical_asset_manufacturer_check
        CHECK (manufacturer IS NULL OR btrim(manufacturer) <> ''),
    ADD CONSTRAINT technical_asset_model_check
        CHECK (model IS NULL OR btrim(model) <> ''),
    ADD CONSTRAINT technical_asset_serial_check
        CHECK (serial_number IS NULL OR btrim(serial_number) <> ''),
    -- Obergrenze großzügig: die DB zieht die äußere Grenze, der Service die
    -- fachliche (Baujahr <= aktuelles Jahr + 1).
    ADD CONSTRAINT technical_asset_year_check
        CHECK (year_built IS NULL OR year_built BETWEEN 1850 AND 2100),
    -- 0 kW hieße „heizt nicht". Unbekannt ist NULL, nie 0.
    ADD CONSTRAINT technical_asset_power_check
        CHECK (power_kw IS NULL OR power_kw > 0);

-- Die Ersatzteilsuche von morgen sucht über Hersteller + Modell.
CREATE INDEX idx_technical_asset_hersteller
    ON property.technical_asset (lower(manufacturer), lower(model))
    WHERE manufacturer IS NOT NULL;
CREATE INDEX idx_technical_asset_property
    ON property.technical_asset (property_id);

-- ---------------------------------------------------------------------------
-- 4. Schutzstandard — Muster property.room (0086)
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_technical_asset_audit
    AFTER UPDATE ON property.technical_asset
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_technical_asset_no_delete
    BEFORE DELETE ON property.technical_asset
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_technical_asset_no_truncate
    BEFORE TRUNCATE ON property.technical_asset
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.technical_asset FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP TRIGGER trg_technical_asset_no_truncate ON property.technical_asset;
DROP TRIGGER trg_technical_asset_no_delete ON property.technical_asset;
DROP TRIGGER trg_technical_asset_audit ON property.technical_asset;

DROP INDEX property.idx_technical_asset_hersteller;
DROP INDEX property.idx_technical_asset_property;

-- Die Spalten zurück ins JSON — der Rückweg verliert nichts.
UPDATE property.technical_asset SET
    attributes = (attributes - 'migration_rest')
        || COALESCE(attributes->'migration_rest', '{}'::jsonb)
        || jsonb_strip_nulls(jsonb_build_object(
            'status', status,
            'versorgung', supply_type,
            'hersteller', manufacturer,
            'modell', model,
            'seriennummer', serial_number,
            'standort', location_note,
            'notiz', note,
            'baujahr', year_built,
            'energietraeger', energy_source,
            'leistung_kw', power_kw::text
        ));

ALTER TABLE property.technical_asset
    DROP CONSTRAINT technical_asset_type_check,
    DROP CONSTRAINT technical_asset_status_check,
    DROP CONSTRAINT technical_asset_supply_check,
    DROP CONSTRAINT technical_asset_energy_check,
    DROP CONSTRAINT technical_asset_manufacturer_check,
    DROP CONSTRAINT technical_asset_model_check,
    DROP CONSTRAINT technical_asset_serial_check,
    DROP CONSTRAINT technical_asset_year_check,
    DROP CONSTRAINT technical_asset_power_check,
    ALTER COLUMN asset_type DROP NOT NULL,
    DROP COLUMN status,
    DROP COLUMN supply_type,
    DROP COLUMN manufacturer,
    DROP COLUMN model,
    DROP COLUMN serial_number,
    DROP COLUMN year_built,
    DROP COLUMN energy_source,
    DROP COLUMN power_kw,
    DROP COLUMN location_note,
    DROP COLUMN note;
"""


class Migration(migrations.Migration):
    dependencies = [("db_core", "0100_monteur_wartung_lesen")]
    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
