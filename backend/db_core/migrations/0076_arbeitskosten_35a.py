"""Arbeitskostenanteil je Position (§ 35a EStG) — Ausweis auf der Rechnung.

Fachlicher Hintergrund: Für Handwerkerleistungen im Privathaushalt kann der
Kunde **20 % der Arbeitskosten** (Lohn-, Maschinen- und Fahrtkosten, inkl. der
darauf entfallenden USt; max. 1.200 EUR/Jahr) von seiner Steuerschuld abziehen
(§ 35a Abs. 3 EStG). Voraussetzung ist eine Rechnung, die den **Arbeitskosten-
anteil gesondert ausweist** — Materialkosten sind nicht begünstigt. Fehlt der
Ausweis, ist der Bonus für den Kunden verloren; eine eigene Schätzung durch den
Kunden erkennt die Finanzverwaltung nicht an.

**Warum eine eigene Spalte und nicht nur `line_type`?**
`line_type` klassifiziert die Position bereits (MATERIAL | ARBEITSZEIT | FAHRT |
PAUSCHALE | FREMDLEISTUNG | ZUSCHLAG). Für drei dieser Arten ist der Anteil
daraus aber **nicht ableitbar**: eine PAUSCHALE („Bad komplett") enthält
typischerweise Material UND Arbeit, eine FREMDLEISTUNG bringt die Aufteilung des
Subunternehmers mit, ein ZUSCHLAG kann auf beides gehen. Ein geratener Anteil
wäre eine Falschaussage gegenüber dem Finanzamt — in die eine Richtung
Steuerverkürzung, in die andere ein verschenkter Bonus.

Deshalb trägt **jede Position ihren begünstigten Nettoanteil selbst**:

- `labour_net_amount IS NULL` = **unbestimmt** (nicht „null Euro"!). Solange eine
  summenwirksame Position unbestimmt ist, weist der Beleg **keine** Arbeitskosten
  aus — lieber kein Ausweis als ein falscher. Dieselbe Konvention wie bei Marge,
  Auslastung und VK-Vorschlag: unbekannt ist nicht 0.
- Der Service belegt ihn automatisch vor, wo die Art eindeutig ist (ARBEITSZEIT/
  FAHRT → voller Betrag, MATERIAL → 0,00) und lässt ihn überall überschreiben —
  auch auf einer MATERIAL-Zeile, denn Verbrauchsmittel (Schmier-, Reinigungs-,
  Dichtmittel) sind nach § 35a begünstigt, obwohl sie Material sind.

Physisch abgesichert (CHECK):
- Nur **Betragszeilen** tragen einen Anteil (TEXT/ZWISCHENSUMME nie).
- Der Anteil hat **dasselbe Vorzeichen** wie der Positionsbetrag und ist
  betragsmäßig **nicht größer** als er. Damit gilt die Aussage „Arbeitskosten
  sind ein Teil dieser Position" physisch — auf einer Gutschrift/einem Storno
  (negative Beträge) genauso wie auf einer Rechnung.

`invoicing.invoice.show_labour_costs` steuert den Ausweis je Beleg, **Default
true**: Der Regelfall des Betriebs ist der Privatkunde, und ein vergessener Haken
kostet den Kunden bares Geld. Auf einer B2B-Rechnung ist der Block sachlich
richtig, nur nutzlos — er lässt sich dort abschalten.

Die Spalte liegt auch auf `quote_line`: Angebot und Rechnung teilen sich
Positionslogik (`_prepare_lines`/`_write_lines`), und ein Angebot, das die
Arbeitskosten schon ausweist, ist im Privatkundengeschäft ein Verkaufsargument.
Der **Ausweis selbst** (Kopf-Flag, PDF-Block) hängt bewusst nur an der Rechnung:
§ 35a Abs. 5 EStG verlangt die **Rechnung**, kein Angebot.

Rückwärts: rein additiv (zwei Spalten + ein Flag, keine Datenmigration). Ein
Bestandsbeleg behält `NULL` = unbestimmt und weist folglich nichts aus — was
korrekt ist: für einen bereits veröffentlichten Beleg lässt sich der Anteil nicht
nachträglich bestimmen, und veröffentlichte Positionen sind unveränderlich (B-30).
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE invoicing.quote_line
    ADD COLUMN labour_net_amount numeric(15,2) NULL;
ALTER TABLE invoicing.invoice_line
    ADD COLUMN labour_net_amount numeric(15,2) NULL;

-- Der Anteil ist ein TEIL des Positionsbetrags: gleiches Vorzeichen, nicht
-- größer. Ohne Betrag (Text-/Zwischensummenzeile) gibt es keinen Anteil.
ALTER TABLE invoicing.quote_line
    ADD CONSTRAINT quote_line_labour_share
        CHECK (labour_net_amount IS NULL
               OR (line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
                   AND net_amount IS NOT NULL
                   AND labour_net_amount * net_amount >= 0
                   AND abs(labour_net_amount) <= abs(net_amount)));

ALTER TABLE invoicing.invoice_line
    ADD CONSTRAINT invoice_line_labour_share
        CHECK (labour_net_amount IS NULL
               OR (line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
                   AND net_amount IS NOT NULL
                   AND labour_net_amount * net_amount >= 0
                   AND abs(labour_net_amount) <= abs(net_amount)));

ALTER TABLE invoicing.invoice
    ADD COLUMN show_labour_costs boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN invoicing.invoice_line.labour_net_amount IS
    'Nach § 35a EStG beguenstigter Arbeitskostenanteil (netto) dieser Position. NULL = unbestimmt (nicht 0,00): dann weist der Beleg keine Arbeitskosten aus.';
COMMENT ON COLUMN invoicing.quote_line.labour_net_amount IS
    'Nach § 35a EStG beguenstigter Arbeitskostenanteil (netto) dieser Position. NULL = unbestimmt (nicht 0,00).';
COMMENT ON COLUMN invoicing.invoice.show_labour_costs IS
    'Arbeitskosten nach § 35a EStG auf dem Beleg ausweisen (Default true: Privatkunde ist der Regelfall).';
"""

REVERSE_SQL = r"""
ALTER TABLE invoicing.invoice DROP COLUMN show_labour_costs;
ALTER TABLE invoicing.invoice_line DROP CONSTRAINT invoice_line_labour_share;
ALTER TABLE invoicing.quote_line DROP CONSTRAINT quote_line_labour_share;
ALTER TABLE invoicing.invoice_line DROP COLUMN labour_net_amount;
ALTER TABLE invoicing.quote_line DROP COLUMN labour_net_amount;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0075_stundenausgleich_vier_augen"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
