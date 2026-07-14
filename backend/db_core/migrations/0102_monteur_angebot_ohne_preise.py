"""Rechtematrix: Der Monteur liest das Angebot seines Objekts — OHNE Preise.

## Der Anlass

0099/0100 gaben dem Monteur die Objektsicht. Eine Ausnahme blieb bewusst offen:
`invoicing` stand weiter auf `false`, also sah er **kein Angebot**. Das war für
den Zwischenstand richtig (eine halbe Preisunterdrückung wäre schlimmer als gar
keine), widerspricht aber der Entscheidung des Users:

  „Er muss und darf alles sehen! **Rechnungen** sind die einzige Ausnahme, die
   vollkommen irrelevant sind für den Monteur. Der Rest muss sichtbar sein. Und
   ja — dann **ohne Preise! Nur Mengen**."

Fachlich ist das zwingend: Der Monteur muss wissen, **was beauftragt ist** — 12 m
Kupferrohr DN20, sechs Thermostatventile —, sonst baut er das Falsche ein oder
übersieht eine Position. Der **Preis** dieser Position geht ihn nichts an; er
trägt eure Kalkulation sonst über jede Baustelle.

## Die Trennlinie: RECHNUNG ist etwas anderes als ANGEBOT

Diese Migration öffnet `invoicing/LESEN` — und damit potenziell **beide**
Belegarten. **Die Rechnung bleibt für EIGENE trotzdem unsichtbar**, aber nicht
mehr durch die Abwesenheit des Rechts, sondern durch eine Regel im Lesepfad. Das
ist eine Verschlechterung der Beweislage, und sie wird bewusst in Kauf genommen —
mit zwei Gegenmaßnahmen, ohne die diese Migration nicht laufen darf:

1. **Die Rechnung ist für `row_scope='EIGENE'` in JEDEM Lesepfad gesperrt**
   (Liste, Detail, PDF, ZUGFeRD, Suche, Dossier, offene Posten). Fail-closed.
2. **Kein Geldfeld verlässt den Server**, wenn der Scope EIGENE ist — geprüft
   nicht per Feldliste (die vergisst man), sondern durch einen Test, der die
   **serialisierte Antwort** nach Beträgen absucht.

## Die Falle, die diese Migration erst gefährlich macht

`QuoteLineOut` liefert nicht nur `unit_price` und `net_amount`, sondern auch
**`unit_cost` (der Einkaufspreis) und `markup_percent` (der Aufschlag)** — der
eigene Kommentar im Schema nennt sie „interner Kalkulations-Snapshot (nicht auf
dem Kundenbeleg)". Sie stehen nicht im PDF, aber sie stehen **im JSON**. Wer beim
Wegnehmen nur an `unit_price` denkt, gibt dem Monteur die **Marge** — also mehr,
als der Kunde je zu sehen bekommt.

`pricing` bleibt deshalb auf `false`: Der Artikelstamm führt EK und
Aufschlagsmatrix.

## Was diese Migration ändert

MONTEUR: `invoicing` **LESEN** → `allowed=true, row_scope='EIGENE'`.
ANLEGEN / AENDERN / FREIGEBEN / VERSENDEN / STORNIEREN bleiben `false` — er liest
das Angebot, er schreibt keines, er versendet keines und er storniert nichts.

Wie in 0099/0100 gilt: **`allowed=true` allein begrenzt nichts.** Die Zeilen
begrenzt `db_core/services/objektsicht.py`; die Belegart und die Geldfelder
begrenzt der Lesepfad.
"""
from django.db import migrations

CREATE_SQL = r"""
UPDATE security.role_permission
SET allowed = true, row_scope = 'EIGENE'
WHERE role_code = 'MONTEUR'
  AND module = 'invoicing'
  AND action = 'LESEN';
"""

DROP_SQL = r"""
UPDATE security.role_permission
SET allowed = false, row_scope = 'EIGENE'
WHERE role_code = 'MONTEUR'
  AND module = 'invoicing'
  AND action = 'LESEN';
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0101_anlage_stammdaten_und_schutz")]

    operations = [
        # reverse_sql stellt den Zustand aus 0026 wieder her: allowed=false,
        # row_scope='EIGENE' (die Startmatrix stellte MONTEUR durchgängig auf
        # 'EIGENE' — anders als das maintenance-INSERT aus 0071, das 'ALLE'
        # setzte; deshalb hier NICHT 'ALLE' zurückschreiben).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
