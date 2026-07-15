"""ai.tool: bearer_encrypted — das Geräte-Bearer, Fernet-verschlüsselt at rest.

Hand-SQL nach db/README.md. MCN authentifiziert die passiven Geräte mit einem
Bearer-Token; das liegt NIE im Klartext in der Tabelle, sondern Fernet-verschlüsselt
(db_core/cred_crypto.py, eigener MCN_CRED_KEY). Muster wie company.mail_account (das
SMTP-Passwort liegt dort ebenfalls als verschlüsselte Chiffre inline). Das Feld ist
veränderbar (guard_tool friert nur tool_key/capability ein); `credential_reference`
bleibt ein optionaler menschenlesbarer Verweis/Label.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE ai.tool ADD COLUMN bearer_encrypted bytea NULL;
"""

REVERSE_SQL = r"""
ALTER TABLE ai.tool DROP COLUMN IF EXISTS bearer_encrypted;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0107_tool_toolcall_workflowrun"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
