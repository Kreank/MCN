"""State-only (kein DDL): Django-Modellzustand für die Aufschlagsmatrix (0069).

Die Tabellen entstehen in `0069_aufschlagsmatrix` per Hand-SQL; die Models sind
`managed = False`. Diese Migration hält nur den Migrations-Graphen sauber
(`makemigrations --check`). FK-Felder und die neue Spalte
`article_sale_price.price_origin` fehlen hier absichtlich — für unmanaged Models
erzeugt Django dafür keine Operationen.

Abhängig von 0072, damit der Graph EIN Blatt behält (0072 führt den Zweig 0069
und den Zweig 0071 bereits zusammen).
"""
import django.db.models.functions.datetime
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0072_stundenausgleich_attest"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarkupRule",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("name", models.TextField()),
                ("product_group", models.TextField(blank=True, null=True)),
                ("calc_basis", models.TextField()),
                ("markup_percent", models.DecimalField(decimal_places=3, max_digits=9)),
                (
                    "min_margin_percent",
                    models.DecimalField(
                        blank=True, decimal_places=3, max_digits=9, null=True
                    ),
                ),
                ("status", models.TextField()),
                ("version", models.IntegerField()),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
            ],
            options={
                "db_table": 'pricing"."markup_rule',
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="MarkupRuleTier",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("min_quantity", models.DecimalField(decimal_places=3, max_digits=15)),
                ("markup_percent", models.DecimalField(decimal_places=3, max_digits=9)),
                ("status", models.TextField()),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
            ],
            options={
                "db_table": 'pricing"."markup_rule_tier',
                "managed": False,
            },
        ),
    ]

