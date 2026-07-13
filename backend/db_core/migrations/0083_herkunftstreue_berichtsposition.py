"""Die Herkunft einer Berichtsposition muss ihr auch ENTSPRECHEN (Herkunftstreue).

## Der Fehler, den diese Migration schließt

0080 gibt `workflow.site_report_line` zwei Spalten für das Soll:
`source_quote_line_id` (Herkunft) und `planned_quantity` (die eingefrorene
Sollmenge). Der CHECK `site_report_line_soll_nur_mit_herkunft` erzwingt bisher
nur: **eine Sollmenge gibt es nur mit Herkunft**. Er garantiert damit *Herkunft* —
aber nicht *Treue*:

* `planned_quantity` durfte **jede beliebige Zahl** sein, solange irgendeine
  Angebotsposition referenziert war (ein CHECK kann nicht in eine andere Tabelle
  schauen).
* Die Herkunft durfte eine Angebotsposition sein, die **etwas ganz anderes**
  beschreibt: die Kessel-Position als Herkunft einer Rohr-Zeile. Dann stünde
  „angeboten: 500" neben *Rohr DN20* — auf einem Dokument, das der Kunde
  unterschreibt und das danach versiegelt wird (`protect_site_report`, 0054).

Der Service leitet Soll und Identität inzwischen aus der Angebotsposition ab. Aber
ein Service ist eine Zusage, kein Tor: ein ORM-Direktweg, ein Skript oder ein
künftiger zweiter Schreibpfad (auch die KI schreibt durch dieselben Tore) käme
daran vorbei. Nach Projektregel setzt die **Datenbank** die Regel physisch durch.
Weil ein CHECK dafür nicht reicht (er sieht nur seine eigene Zeile), ist es ein
Trigger.

## Die Regel

    source_quote_line_id gesetzt  ⇒  planned_quantity  = quote_line.quantity
                                 ∧  source_article_id  = quote_line.source_article_id
                                 ∧  source_assembly_id = quote_line.source_assembly_id
                                 ∧  unit               = quote_line.unit
                                 ∧  description        = quote_line.description

Fünf Gleichungen, ein Zweck: **Soll- und Ist-Schlüssel des Abgleichs sind per
Konstruktion deckungsgleich.**

Die **Bezeichnung gehört dazu** — auch sie ist Identität, nicht bloß Anzeige. Ohne
sie bliebe das Loch offen, das der Trigger schließen soll: eine Zeile *„Rohr DN20"
· 5 Stk* mit der **Kessel**-Position als Herkunft erfüllt alle übrigen Gleichungen
(Artikel, Leistung, Einheit, Sollmenge werden ja aus dem Kessel abgeleitet) und
trüge auf dem unterschriebenen Dokument „Rohr DN20 · 5 Stk · angeboten 500". Kein
Feld widerspräche. Erst die fünfte Gleichung macht die Untreue sichtbar.

Die Präzisierung des Monteurs („Steigstrang, 2. OG") gehört deshalb in die
**Notiz** (`site_report_line.note`) — sie ist frei, steht neben der Zeile und
verfälscht die Identität nicht.

`IS DISTINCT FROM` statt `<>`: NULL = NULL muss hier *gleich* heißen (eine
Freitext-Angebotsposition ohne Artikelbezug erzeugt eine Berichtsposition ohne
Artikelbezug — das ist treu, nicht abweichend).

Die Quellzeile wird `FOR KEY SHARE` gelesen: derselbe Sperrmodus, den der
Fremdschlüssel ohnehin nimmt. Sie kann damit nicht gleichzeitig gelöscht werden,
während wir gegen sie prüfen.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE FUNCTION workflow.enforce_site_report_line_herkunft() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_quantity    numeric(15, 3);
    v_unit        text;
    v_article     uuid;
    v_assembly    uuid;
    v_description text;
BEGIN
    IF NEW.source_quote_line_id IS NULL THEN
        -- Ohne Herkunft gibt es nichts abzugleichen; dass dann auch keine
        -- Sollmenge dastehen darf, erzwingt bereits der CHECK
        -- site_report_line_soll_nur_mit_herkunft (0080).
        RETURN NEW;
    END IF;

    SELECT quantity, unit, source_article_id, source_assembly_id, description
      INTO v_quantity, v_unit, v_article, v_assembly, v_description
      FROM invoicing.quote_line
     WHERE id = NEW.source_quote_line_id
       FOR KEY SHARE;

    IF NOT FOUND THEN
        -- Der Fremdschlüssel fängt das normalerweise ab; die Meldung bleibt
        -- trotzdem stehen, damit der Trigger für sich genommen vollständig ist.
        RAISE EXCEPTION
            'site_report_line: Die als Herkunft angegebene Angebotsposition % existiert nicht.',
            NEW.source_quote_line_id;
    END IF;

    IF NEW.planned_quantity IS DISTINCT FROM v_quantity THEN
        RAISE EXCEPTION
            'site_report_line: Die Sollmenge (%) weicht von der Menge der Herkunfts-Angebotsposition % (%) ab. Das Soll wird aus dem Angebot übernommen und kann nicht frei gesetzt werden.',
            NEW.planned_quantity, NEW.source_quote_line_id, v_quantity;
    END IF;

    IF NEW.source_article_id IS DISTINCT FROM v_article
       OR NEW.source_assembly_id IS DISTINCT FROM v_assembly THEN
        RAISE EXCEPTION
            'site_report_line: Der Artikel-/Leistungsbezug weicht von der Herkunfts-Angebotsposition % ab. Bei gesetzter Herkunft wird er aus dem Angebot übernommen.',
            NEW.source_quote_line_id;
    END IF;

    IF NEW.unit IS DISTINCT FROM v_unit THEN
        RAISE EXCEPTION
            'site_report_line: Die Einheit (%) weicht von der Einheit der Herkunfts-Angebotsposition % (%) ab. Bei gesetzter Herkunft wird sie aus dem Angebot übernommen.',
            NEW.unit, NEW.source_quote_line_id, v_unit;
    END IF;

    -- Die fünfte Gleichung: ohne sie hinge die Sollmenge des Kessels an einer
    -- selbst getippten Rohr-Zeile, und kein Feld widerspräche (siehe Modulkopf).
    IF NEW.description IS DISTINCT FROM v_description THEN
        RAISE EXCEPTION
            'site_report_line: Die Bezeichnung (%) weicht von der Bezeichnung der Herkunfts-Angebotsposition % (%) ab. Die Bezeichnung einer angebotenen Position ist fest; Ergänzungen gehören in die Notiz.',
            NEW.description, NEW.source_quote_line_id, v_description;
    END IF;

    RETURN NEW;
END;
$$;

-- BEFORE INSERT OR UPDATE: ein DELETE kann keine Untreue erzeugen.
CREATE TRIGGER trg_site_report_line_herkunftstreue
    BEFORE INSERT OR UPDATE ON workflow.site_report_line
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_site_report_line_herkunft();

COMMENT ON COLUMN workflow.site_report_line.planned_quantity IS
    'Eingefrorene Sollmenge aus der Herkunfts-Angebotsposition. Gleicht per Trigger '
    '(workflow.enforce_site_report_line_herkunft, 0083) IMMER quote_line.quantity — '
    'ein frei gesetztes Soll stünde sonst auf einem unterschriebenen, versiegelten '
    'Kundendokument.';
COMMENT ON COLUMN workflow.site_report_line.source_quote_line_id IS
    'Herkunft: die Angebotsposition, deren Soll diese Zeile trägt. Artikel, Leistung, '
    'Einheit, Bezeichnung und planned_quantity müssen ihr entsprechen (Trigger 0083); '
    'die Identität der Zeile ist damit vollständig aus dem Angebot abgeleitet. Was der '
    'Monteur ergänzen will, gehört in note.';
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_site_report_line_herkunftstreue ON workflow.site_report_line;
DROP FUNCTION IF EXISTS workflow.enforce_site_report_line_herkunft();
COMMENT ON COLUMN workflow.site_report_line.planned_quantity IS NULL;
COMMENT ON COLUMN workflow.site_report_line.source_quote_line_id IS NULL;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0082_angebot_auftragszuordnung"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
