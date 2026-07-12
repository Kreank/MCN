"""E-Rechnung (ZUGFeRD/Factur-X): genau EINE Ausfertigung je Beleg.

Die E-Rechnung ist eine **eigene Ausfertigung** neben dem normalen Beleg-PDF:
anderes Dokument (PDF/A-3B mit eingebettetem CII-XML), andere Bytes, eigener
Aufbewahrungszweck. Sie hängt daher mit einer eigenen `link_category`
('E_RECHNUNG') an `content.file_link` und bekommt — wie das Beleg-PDF in
Migration 0032 — einen eigenen partiellen UNIQUE-Index.

Warum kein CHECK anzupassen ist: `content.file_link.link_category` ist in
Migration 0021 als freies `text NULL` angelegt, ohne Wertebereichs-CHECK. Eine
neue Kategorie braucht also kein DDL an der Spalte. Der `num_nonnulls(...) = 1`-
CHECK betrifft nur die Ziel-Spalten (invoice_id ist bereits eine davon) und
bleibt unverändert gültig.

Der Index sichert physisch, was der Service annimmt: zwei parallele Erstabrufe
können nicht zwei ZUGFeRD-Ausfertigungen desselben Belegs ablegen (Muster P-1
aus dem Beleg-PDF; der Verlierer selektiert die Datei des Gewinners nach).
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE UNIQUE INDEX uq_file_link_erechnung
    ON content.file_link (invoice_id)
    WHERE link_category = 'E_RECHNUNG' AND invoice_id IS NOT NULL;
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS content.uq_file_link_erechnung;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0058_skonto_zahlungsbedingungen"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
