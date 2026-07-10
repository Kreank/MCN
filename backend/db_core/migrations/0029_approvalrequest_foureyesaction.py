# State-only-Migration zu Migration 0028 (Vier-Augen-Freigabe).
#
# Django-Konvention (db/README.md, docs/HANDOFF.md Abschnitt 3): das DDL für die
# Fachtabelle security.approval_request liegt in 0028 als Hand-SQL (RunSQL); diese
# Migration bildet nur den ORM-State ab (CreateModel für die managed=False-Models),
# damit `makemigrations --check` sauber bleibt. Sie erzeugt KEIN DDL.
#
# FK-Felder (action/requested_by/decided_by) fehlen im CreateModel bewusst — für
# managed=False-Models generiert Django sie nicht, und die echten Constraints
# stehen in 0028.
import django.db.models.functions.datetime
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0028_vier_augen_freigabe"),
    ]

    operations = [
        migrations.CreateModel(
            name="FourEyesAction",
            fields=[
                ("action_code", models.TextField(primary_key=True, serialize=False)),
                ("label", models.TextField()),
                ("active", models.BooleanField(db_default=True)),
                ("updated_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
            ],
            options={
                "db_table": 'security"."four_eyes_action',
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="ApprovalRequest",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("status", models.TextField()),
                ("payload", models.JSONField(db_default={})),
                ("target_table", models.TextField(blank=True, null=True)),
                ("target_id", models.UUIDField(blank=True, null=True)),
                ("reason", models.TextField(blank=True, null=True)),
                ("requested_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_note", models.TextField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.IntegerField(db_default=models.Value(1))),
                ("created_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ("updated_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
            ],
            options={
                "db_table": 'security"."approval_request',
                "managed": False,
            },
        ),
    ]
