"""Rechtematrix: Der Monteur liest die Wartung seines Objekts.

## Der Anlass

Migration 0099 gab dem Monteur die **Objektsicht** — mit einer Ausnahme, die eine
zu viel war: `maintenance` blieb auf `false` (so hatte es 0071 festgelegt, mit der
Begründung „Fälligkeitsplanung ist kein Monteurs-Arbeitsbereich; er sieht das
Ergebnis als Einsatz/Aufgabe"). Im Dossier stand für ihn deshalb
`wartung_sichtbar: false`.

Das widerspricht der Entscheidung des Users: **„Er muss und darf alles sehen —
Rechnungen sind die einzige Ausnahme."** Ein Wartungsvertrag ist keine Rechnung. Er
ist die Information „diese Zentralanlage wird jährlich gewartet, nächste Fälligkeit
im März, Gewährleistung läuft noch bis November". Genau das braucht der Monteur, der
vor der Anlage steht — es ist derselbe Heizkörper-Fall, der 0099 ausgelöst hat.

## Warum das gefahrlos ist

Das Schema `maintenance` (sechs Tabellen: `maintenance_contract`,
`maintenance_event`, `inspection_type`, `inspection`, `warranty`, `due_item`) führt
**keine einzige Geldspalte** — über `information_schema` gegengeprüft, null Treffer
auf numeric/price/amount/betrag/cost. Es gibt dort nichts zu verbergen. Die Grenze
zwischen „darf alles sehen" und „nie eine Rechnung" verläuft weiterhin exakt an
`invoicing`/`pricing`, und die bleiben unangetastet auf `false`.

## Was diese Migration ändert

MONTEUR: `maintenance` **LESEN** → `allowed=true, row_scope='EIGENE'`.

**ANLEGEN / AENDERN / FREIGEBEN / STORNIEREN bleiben `false`.** Er liest den
Vertrag; er schließt keinen, er verschiebt keine Frist und er verwirft keine
Fälligkeit. Das ist keine Vorsicht, sondern die Rollentrennung: Eine Fälligkeit
verfallen zu lassen ist eine kaufmännische Entscheidung (so schon der Modulkopf von
0071).

Wie in 0099 gilt: **Das `allowed=true` allein begrenzt nichts.** Die Zeilen begrenzt
`db_core/services/objektsicht.py` — die eine Heimat der Regel „meine Objekte".
Contract, Inspection und Warranty tragen `property_id` NOT NULL; `due_item.property_id`
ist nullable und wird deshalb zusätzlich über seinen Anker aufgelöst
(`objektsicht.due_item_q`). `inspection_type` ist **globales Stammdatum ohne
Objektbezug** (wie der Bauteilkatalog): lesbar, nicht schreibbar.

## Warum eine NEUE Migration statt einer Änderung an 0099

0099 ist auf der Dev-DB bereits angewandt. Eine nachträgliche Änderung würde dort nie
laufen — und der Migrationsgraph ist die einzige Wahrheit darüber, was in einer
Umgebung wirklich gilt. Vorwärts korrigieren, nie rückwärts umschreiben
(`db/README.md`, Rückwärtsstrategie).
"""
from django.db import migrations

CREATE_SQL = r"""
UPDATE security.role_permission
SET allowed = true, row_scope = 'EIGENE'
WHERE role_code = 'MONTEUR'
  AND module = 'maintenance'
  AND action = 'LESEN';
"""

DROP_SQL = r"""
UPDATE security.role_permission
SET allowed = false, row_scope = 'ALLE'
WHERE role_code = 'MONTEUR'
  AND module = 'maintenance'
  AND action = 'LESEN';
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0099_monteur_objektsicht")]

    operations = [
        # reverse_sql stellt exakt den Zustand aus 0071 wieder her: allowed=false,
        # row_scope='ALLE' (das maintenance-INSERT von 0071 setzte 'ALLE' für JEDE
        # Rolle — anders als die Startmatrix 0026, die MONTEUR durchgängig auf
        # 'EIGENE' stellte). Reine Stammdatenpflege, keine Fachdaten.
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
