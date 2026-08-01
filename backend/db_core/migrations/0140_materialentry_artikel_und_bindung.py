"""State-only: die beiden neuen Felder aus 0139 im Django-Modellzustand nachziehen.

**Kein DDL.** Beide Spalten legt 0139 als Hand-SQL an; `AddField` auf einem
`managed = False`-Model erzeugt per Definition keine Datenbankoperation
(`Model._meta.can_migrate()` ist dort falsch, der SchemaEditor überspringt sie).
Diese Migration hält allein den **Migrationszustand** deckungsgleich mit
`db_core/models.py`.

Warum sie trotzdem von Hand geschrieben ist: `makemigrations` sieht die Änderung
nicht. Djangos Autodetector sortiert `managed = False`-Models in die
`unmanaged_keys` und vergleicht ihre Felder gar nicht erst — er erzeugt für sie
nur `CreateModel`/`DeleteModel`. `makemigrations db_core --check` bleibt deshalb
mit und ohne diese Datei „No changes detected"; ohne sie liefe der Zustand
allerdings still auseinander, und die nächste Person, die hier etwas ableitet,
läse einen veralteten Stand.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0139_material_abrechenbar"),
        # **Zusammenführung mit dem parallel gebauten Link-Slice.** 0139 und 0141
        # hängen beide an 0138 — das ergibt zwei Blätter im Migrationsgraphen, und
        # `migrate` verweigert dann den Dienst („Conflicting migrations detected").
        # Weil diese Migration reiner Zustand ohne DDL ist, ist sie der billigste
        # Ort für den Zusammenschluss: Sie ordnet sich hinter beide Stränge und
        # macht 0140 zum einzigen Blatt. Kein Merge-Stub nötig.
        #
        # Wer den Link-Slice zurücknimmt, nimmt diese Zeile mit — sonst zeigt sie
        # ins Leere. Umgekehrt darf 0141/0142 NICHT auf 0140 zeigen, das ergäbe
        # einen Zyklus.
        ("db_core", "0142_publiclink"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialentry",
            name="source_article",
            field=models.ForeignKey(
                blank=True,
                db_column="source_article_id",
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="material_entries",
                to="db_core.article",
            ),
        ),
        migrations.AddField(
            model_name="billinglink",
            name="material_entry",
            field=models.ForeignKey(
                blank=True,
                db_column="material_entry_id",
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="billing_links",
                to="db_core.materialentry",
            ),
        ),
    ]
