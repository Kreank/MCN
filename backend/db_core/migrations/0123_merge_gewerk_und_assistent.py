"""Führt die beiden Migrationszweige wieder zusammen.

Zwei Arbeitsstränge sind unabhängig voneinander auf `0119` aufgesetzt und haben
beide eine `0120` angelegt:

* `0120_assistent_intent_rueckfrage` — KI-Assistent (Fragesätze, Rückfragen),
  kam über `origin/develop` herein und steckt bereits in `origin/main`.
* `0120_gewerk_fundament` → `0121_employeetrade` → `0122_disposition_darf_freigeben`
  — Gewerk-Achse und der Anruf-Durchstich.

Damit hatte der Graph zwei Blätter, und jedes `migrate` wäre mit „Conflicting
migrations detected" abgebrochen.

Warum eine Merge-Migration und kein Umnummerieren
--------------------------------------------------
Den Assistenten-Zweig ans Ende zu schieben (0120 → 0123) wäre die aufgeräumtere
Historie, aber gefährlich: Django führt angewendete Migrationen über ihren
**Namen** in `django_migrations`. `0120_assistent_intent_rueckfrage` ist über
`origin/main` bereits ausgeliefert und auf der Demo-Instanz vermutlich
angewendet. Nach einer Umbenennung hielte der Server die Migration für
ausstehend und liefe sie ein zweites Mal — ihr SQL ist nicht idempotent.

Diese Migration ist deshalb bewusst leer: Sie ändert nichts, sie erklärt dem
Graphen nur, dass beide Zweige ab hier gemeinsam weiterlaufen. Die Reihenfolge
der beiden Zweige zueinander ist fachlich beliebig — sie fassen disjunkte
Tabellen an (`ai.*` bzw. `company.trade` / `workflow.*` / `security.role_permission`).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0120_assistent_intent_rueckfrage"),
        ("db_core", "0122_disposition_darf_freigeben"),
    ]

    operations = []
