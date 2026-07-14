"""Bauteilkatalog (property.component_template) — Vorauswahl statt Zahlentipperei.

Das Raumaufmaß (0086/0089) verlangt an jeder Wand und an jedem Fenster einen
**U-Wert als Zahl**. Auf der Baustelle ist das die falsche Frage: Der Monteur
sieht ein **Doppelkastenfenster**, keine 2,7 W/(m²·K). Er sieht eine
**ungedämmte Ziegelwand**, keine 1,4. Die Zahl kennt der Betrieb — einmal —, und
danach nie wieder jemand auswendig.

`component_template` ist deshalb ein **Stammdatenkatalog** des Betriebs:

    kind = 'FLAECHE'   → Wandaufbauten, Decken, Böden, Dachschrägen
    kind = 'OEFFNUNG'  → Fenster- und Türarten

Eine Vorlage trägt einen Namen, eine Bauteilart als Vorschlag und den U-Wert.
Beim Erfassen wählt man die Vorlage, der Wert kommt mit.

## INVARIANTE: Die Vorlage ist eine KOPIERQUELLE, kein Verweis.

`room_surface.template_id` / `room_opening.template_id` sind ein
**Herkunftsvermerk** — der U-Wert wird beim Erfassen in die Zeile **kopiert**.
Dieselbe Regel wie bei der Belegposition (siehe HANDOFF: „Belegposition ist eine
Kopie, kein Verweis"), und aus demselben Grund: Korrigiert jemand später den
Katalogwert für „Fenster, 2-fach", darf sich damit **nicht rückwirkend die
Heizlast** eines Objekts ändern, das der Betrieb dem Kunden längst vorgerechnet
hat. Wer den neuen Wert will, übernimmt ihn ausdrücklich.

Der Rechner liest deshalb **immer** `room_surface.u_value`, nie den Katalog.
`template_id` ist für ihn unsichtbar; es dient der Anzeige („aus: Fenster,
3-fach") und dem späteren Angebot, veraltete Werte nachzuziehen.

## INVARIANTE: Der Katalog wird OHNE U-Werte ausgeliefert.

Die Seed-Zeilen tragen `u_value IS NULL` — nur Namen. Das ist Absicht, aus zwei
Gründen:

1. **Normrecht** (siehe HANDOFF, Welle 2/Punkt 11 und Modulkopf 0086): U-Werte
   für Bestandsbauteile stammen aus DIN-Tabellenwerken. Die Rechenvorschrift
   anzuwenden ist frei, die Tabellen mitzuliefern nicht.
2. **Verantwortung.** Der Betrieb unterschreibt am Ende die Auslegung. Er soll
   nicht Zahlen unterschreiben, die eine Software geraten hat — er trägt sie
   einmal ein und steht dafür gerade (Herstellerangabe oder eigener
   Erfahrungswert).

Ein Bauteil ohne hinterlegten U-Wert ist damit **kein Fehler**, sondern der
Normalzustand nach der Installation. Es verhält sich exakt wie ein fehlender
U-Wert an der Wand: die Heizlast ist dann **unbekannt — nicht 0**, und der
Rechner benennt, was fehlt. Der Katalog macht diese Lücke nur **einmal**
schließbar statt an jedem Fenster erneut.

## Schutzstandard

Voller Standard inklusive **No-Delete**: Eine Vorlage, die schon in einem Aufmaß
steckt, wird nicht gelöscht, sondern auf `INAKTIV` gesetzt — sonst verlöre die
Herkunftsangabe ihr Ziel. `status` steuert nur die **Auswahl** beim Erfassen;
bestehende Zeilen bleiben unberührt (ihr Wert ist ja kopiert).
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE property.component_template (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind                 text NOT NULL CHECK (kind IN ('FLAECHE', 'OEFFNUNG')),
    name                 text NOT NULL CHECK (btrim(name) <> ''),
    -- Vorschlag für die Bauteilart. Der Erfasser darf abweichen (dieselbe
    -- Fensterart sitzt mal in der Fassade, mal im Dach).
    default_surface_type text NULL CHECK (default_surface_type IS NULL OR
                         default_surface_type IN
                         ('AUSSENWAND', 'INNENWAND', 'DACHSCHRAEGE', 'DECKE', 'BODEN')),
    default_opening_type text NULL CHECK (default_opening_type IS NULL OR
                         default_opening_type IN
                         ('FENSTER', 'DACHFENSTER', 'TUER_AUSSEN', 'TUER_INNEN', 'SONSTIGES')),
    -- NULL = noch nicht hinterlegt. Das ist der AUSLIEFERUNGSZUSTAND, kein Mangel
    -- (siehe Modulkopf): keine DIN-Tabellen im Produkt.
    u_value              numeric(5, 3) NULL CHECK (u_value IS NULL OR u_value > 0),
    note                 text NULL,
    status               text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    sort_index           integer NOT NULL DEFAULT 0,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, name),
    -- Die Art gehört zur Gattung: eine Flächenvorlage schlägt keine Fensterart vor.
    CONSTRAINT component_template_art_passt_zur_gattung CHECK (
        (kind = 'FLAECHE'  AND default_opening_type IS NULL) OR
        (kind = 'OEFFNUNG' AND default_surface_type IS NULL)
    )
);

CREATE INDEX idx_component_template_kind ON property.component_template (kind, status);

CREATE TRIGGER trg_component_template_updated_at
    BEFORE UPDATE ON property.component_template
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_component_template_audit
    AFTER UPDATE ON property.component_template
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_component_template_no_delete
    BEFORE DELETE ON property.component_template
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_component_template_no_truncate
    BEFORE TRUNCATE ON property.component_template
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.component_template FROM PUBLIC;

-- Herkunftsvermerk am Aufmaß. Der WERT ist kopiert (siehe Modulkopf) — diese
-- Spalte sagt nur, WOHER er kam.
ALTER TABLE property.room_surface
    ADD COLUMN template_id uuid NULL REFERENCES property.component_template (id);
ALTER TABLE property.room_opening
    ADD COLUMN template_id uuid NULL REFERENCES property.component_template (id);

CREATE INDEX idx_room_surface_template ON property.room_surface (template_id);
CREATE INDEX idx_room_opening_template ON property.room_opening (template_id);

COMMENT ON COLUMN property.room_surface.template_id IS
    'Herkunft aus dem Bauteilkatalog. Der U-Wert ist KOPIERT — eine spätere '
    'Katalogkorrektur ändert dieses Aufmaß nicht.';
COMMENT ON COLUMN property.room_opening.template_id IS
    'Herkunft aus dem Bauteilkatalog. Der U-Wert ist KOPIERT.';
COMMENT ON COLUMN property.component_template.u_value IS
    'U-Wert W/(m²·K). Wird OHNE Wert ausgeliefert — der Betrieb trägt ihn ein und '
    'steht dafür gerade. Keine DIN-Tabellen im Produkt.';

-- ---------------------------------------------------------------------------
-- Seed: NUR NAMEN, KEINE WERTE. Siehe Modulkopf.
-- ---------------------------------------------------------------------------
INSERT INTO property.component_template (kind, name, default_surface_type, sort_index) VALUES
    ('FLAECHE', 'Außenwand, Ziegel ungedämmt',            'AUSSENWAND',   10),
    ('FLAECHE', 'Außenwand, Ziegel mit WDVS',             'AUSSENWAND',   20),
    ('FLAECHE', 'Außenwand, Beton ungedämmt',             'AUSSENWAND',   30),
    ('FLAECHE', 'Außenwand, Beton gedämmt',               'AUSSENWAND',   40),
    ('FLAECHE', 'Außenwand, Holzständer gedämmt',         'AUSSENWAND',   50),
    ('FLAECHE', 'Außenwand, Fachwerk',                    'AUSSENWAND',   60),
    ('FLAECHE', 'Innenwand, Mauerwerk',                   'INNENWAND',    70),
    ('FLAECHE', 'Innenwand, Leichtbau',                   'INNENWAND',    80),
    ('FLAECHE', 'Dachschräge, gedämmt',                   'DACHSCHRAEGE', 90),
    ('FLAECHE', 'Dachschräge, ungedämmt',                 'DACHSCHRAEGE', 100),
    ('FLAECHE', 'Decke zum unbeheizten Dachboden',        'DECKE',        110),
    ('FLAECHE', 'Decke zum beheizten Raum',               'DECKE',        120),
    ('FLAECHE', 'Boden gegen Erdreich',                   'BODEN',        130),
    ('FLAECHE', 'Boden gegen unbeheizten Keller',         'BODEN',        140),
    ('FLAECHE', 'Boden gegen Außenluft (auskragend)',     'BODEN',        150);

INSERT INTO property.component_template (kind, name, default_opening_type, sort_index) VALUES
    ('OEFFNUNG', 'Fenster, Einfachverglasung',            'FENSTER',      10),
    ('OEFFNUNG', 'Fenster, Doppelkastenfenster',          'FENSTER',      20),
    ('OEFFNUNG', 'Fenster, Verbundfenster',               'FENSTER',      30),
    ('OEFFNUNG', 'Fenster, Isolierglas 2-fach (alt)',     'FENSTER',      40),
    ('OEFFNUNG', 'Fenster, Wärmeschutzglas 2-fach',       'FENSTER',      50),
    ('OEFFNUNG', 'Fenster, Wärmeschutzglas 3-fach',       'FENSTER',      60),
    ('OEFFNUNG', 'Dachfenster, 2-fach',                   'DACHFENSTER',  70),
    ('OEFFNUNG', 'Dachfenster, 3-fach',                   'DACHFENSTER',  80),
    ('OEFFNUNG', 'Haustür, Holz (Bestand)',               'TUER_AUSSEN',  90),
    ('OEFFNUNG', 'Haustür, gedämmt',                      'TUER_AUSSEN',  100),
    ('OEFFNUNG', 'Terrassentür, 2-fach',                  'TUER_AUSSEN',  110),
    ('OEFFNUNG', 'Terrassentür, 3-fach',                  'TUER_AUSSEN',  120),
    ('OEFFNUNG', 'Innentür',                              'TUER_INNEN',   130),
    ('OEFFNUNG', 'Kellertür',                             'TUER_INNEN',   140);
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS property.idx_room_opening_template;
DROP INDEX IF EXISTS property.idx_room_surface_template;
ALTER TABLE property.room_opening DROP COLUMN template_id;
ALTER TABLE property.room_surface DROP COLUMN template_id;
DROP TABLE IF EXISTS property.component_template;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0089_auslegungsdaten_am_objekt")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
