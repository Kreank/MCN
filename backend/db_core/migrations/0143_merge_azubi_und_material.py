"""Führt die beiden Migrationszweige wieder zusammen.

Zwei Arbeitsstränge sind unabhängig voneinander auf `0138` aufgesetzt und haben
beide eine `0139` angelegt:

* `0139_rolle_azubi` — die Rolle AZUBI, kam über `origin/develop` herein und
  steckt bereits in `origin/main`.
* `0139_material_abrechenbar` … `0142_publiclink` — Material zur Rechnung,
  öffentliche Links, Systemakteur. Dieser Strang hat sich intern schon selbst
  zusammengeführt: `0140` hängt an `0139_material_abrechenbar` **und** an
  `0142_publiclink` und ist damit sein einziges Blatt (siehe Kopf von `0140`).
  Diese Migration hängt deshalb an `0140`, nicht an `0142` — sonst bliebe `0140`
  als drittes Blatt stehen.

Damit hatte der Graph zwei Blätter, und jedes `migrate` wäre mit „Conflicting
migrations detected" abgebrochen.

Warum eine Merge-Migration und kein Umnummerieren — dieselbe Begründung wie in
`0123_merge_gewerk_und_assistent`: Django führt angewendete Migrationen über
ihren **Namen** in `django_migrations`. `0139_rolle_azubi` ist über
`origin/main` bereits ausgeliefert; nach einer Umbenennung hielte der Server sie
für ausstehend und liefe sie ein zweites Mal.

Diese Migration ist deshalb bewusst leer. Die Reihenfolge der beiden Zweige
zueinander ist fachlich beliebig: `0139_rolle_azubi` schreibt ausschließlich
AZUBI-Zeilen (explizite Matrix, nichts aus anderen Rollen kopiert), der andere
Zweig fasst `workflow.material_entry`, `invoicing.billing_link` und die neuen
`security.public_link` / `SYSTEM_SELBSTBEDIENUNG` an. Kein gemeinsamer
Schreibpunkt.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0139_rolle_azubi"),
        ("db_core", "0140_materialentry_artikel_und_bindung"),
    ]

    operations = []
