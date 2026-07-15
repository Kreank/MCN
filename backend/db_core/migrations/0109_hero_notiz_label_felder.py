"""HERO-Angleichung: vier additive Freitext-Spalten auf Fachtabellen.

Hand-SQL nach db/README.md. Alle vier Spalten sind NULL-fähige `text`-Felder
(kein Pflichtfeld), rein additiv — kein Schutzstandard-Zusatz nötig (keine neue
Tabelle). Referenz: docs/roadmap/hero-angleichung-luecken.md.

1. workflow.project.internal_note  — freies Notizfeld am Projekt (Projekte-7).
2. identity.party.note             — freies Notizfeld am Kontakt (Kontakte-3).
3. identity.party_address.label    — freier Titel einer Objektadresse (Kontakte-6).
4. invoicing.quote.cover_letter    — Anschreiben-Freitext am Angebot (Dokumente-9).

`quote.cover_letter` ist Beleginhalt: der bestehende Trigger
`invoicing.freeze_sent_quote` vergleicht `to_jsonb(NEW) - status -
replaced_by_quote_id - updated_at` und friert damit die neue Spalte ab Versand
automatisch mit ein (B-30). Der Service (beleg.update_quote) zieht dieselbe
Grenze und liefert die Fachmeldung; die DB bleibt letzte Instanz.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE workflow.project        ADD COLUMN internal_note text NULL;
ALTER TABLE identity.party          ADD COLUMN note          text NULL;
ALTER TABLE identity.party_address  ADD COLUMN label         text NULL;
ALTER TABLE invoicing.quote         ADD COLUMN cover_letter  text NULL;
"""

REVERSE_SQL = r"""
ALTER TABLE invoicing.quote         DROP COLUMN IF EXISTS cover_letter;
ALTER TABLE identity.party_address  DROP COLUMN IF EXISTS label;
ALTER TABLE identity.party          DROP COLUMN IF EXISTS note;
ALTER TABLE workflow.project        DROP COLUMN IF EXISTS internal_note;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0108_tool_bearer"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
