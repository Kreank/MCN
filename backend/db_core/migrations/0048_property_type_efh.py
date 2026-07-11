"""Liegenschaftstyp 'EINFAMILIENHAUS' ergänzen (selbstgenutztes Einfamilienhaus).

Hand-SQL nach db/README.md: Fachschema-Änderung als Django-Migration mit RunSQL,
kein ORM-DDL. property.property kannte bisher nur
WEG|RENTAL_PROPERTY|COMMERCIAL|MIXED|OTHER (column-level CHECK aus
0004_property.sql).

Fachquelle: Schnellerfassung/Privatkunde. Für den EFH-Fall (der Regelfall beim
Privateigentümer) fehlte ein Typ für das selbstgenutzte Einfamilienhaus; 'OTHER'
ist nichtssagend und 'RENTAL_PROPERTY' fachlich falsch (kein Mietobjekt). Ohne
eigenen Typ fühlt sich der Liegenschaftsbezug beim Privatkunden künstlich an.

Nur der CHECK wird ersetzt; keine neue Spalte, kein Datenumbau. Der Constraint
ist in 0004 anonym definiert → PostgreSQL-Defaultname
`property_property_type_check`. Zusätzlich kennen die Service-Whitelist
(db_core/services/property.py) und die Frontend-Union den Wert. Reverse stellt
den ursprünglichen 5-Werte-CHECK wieder her (schlägt fahr, falls bereits Zeilen
den neuen Wert tragen — gewolltes lautes Verhalten).
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE property.property DROP CONSTRAINT property_property_type_check;
ALTER TABLE property.property ADD CONSTRAINT property_property_type_check
    CHECK (property_type IN
        ('WEG', 'RENTAL_PROPERTY', 'COMMERCIAL', 'MIXED', 'OTHER', 'EINFAMILIENHAUS'));
"""

REVERSE_SQL = r"""
ALTER TABLE property.property DROP CONSTRAINT property_property_type_check;
ALTER TABLE property.property ADD CONSTRAINT property_property_type_check
    CHECK (property_type IN
        ('WEG', 'RENTAL_PROPERTY', 'COMMERCIAL', 'MIXED', 'OTHER'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0047_communication_mailaccount"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
