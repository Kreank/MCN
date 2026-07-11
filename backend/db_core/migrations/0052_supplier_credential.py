"""IDS-Connect-Zugangsdaten je Lieferanten-Anbindung (pricing.supplier_credential).

Hand-SQL nach db/README.md: neue Fachtabelle als RunSQL mit Schutzstandard
(updated_at/Audit/No-Delete/No-Truncate/REVOKE). Muster: 0023/0046.

Fachquelle: IDS-Connect-Warenkorb-Verfahren (itek 2.5). Der Punchout zum
Händler-Shop überträgt Benutzername/Passwort (`name_kunde`/`pw_kunde`) und
optional die Kundennummer (`kndnr`). Diese Zugangsdaten sind ein SECRET und dürfen
NIE im Klartext in der DB liegen — das Passwort wird Fernet-verschlüsselt gespeichert
(dasselbe Verfahren wie das SMTP-Passwort, `mail_crypto`/`MCN_MAIL_KEY`).

Grundsatzentscheidungen:

1. **Eigene Tabelle statt Spalten an `supplier_connection`.** Migration 0029 hält
   ausdrücklich fest, dass in `supplier_connection` NIE ein Secret steht, nur ein
   `credential_reference`. Die echten (verschlüsselten) Zugangsdaten liegen deshalb
   in dieser separaten 1:1-Tabelle. So bleibt die Registry (breit lesbar) frei von
   Secrets; nur der Zugangsdaten-Pfad berührt das verschlüsselte Passwort.

2. **1:1 zur Anbindung** (`UNIQUE (connection_id)`): eine Anbindung hat höchstens
   einen Satz Zugangsdaten. Das Passwort liegt als `bytea` (Fernet-Chiffre);
   Benutzername/Kundennummer sind fachlich keine Geheimnisse (Klartext).

3. **Kein Löschen** (Schutzstandard): Zugangsdaten werden überschrieben oder das
   Passwort auf NULL gesetzt, nicht die Zeile gelöscht.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE pricing.supplier_credential (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id       uuid NOT NULL UNIQUE
                        REFERENCES pricing.supplier_connection (id),
    -- Fachlich keine Geheimnisse:
    username            text NULL,
    customer_number     text NULL,          -- kndnr (Kundennummer beim Händler)
    -- SECRET: Fernet-Chiffre (nie Klartext); NULL = kein Passwort hinterlegt.
    password_encrypted  bytea NULL,
    version             integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_supplier_credential_updated_at
    BEFORE UPDATE ON pricing.supplier_credential
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_supplier_credential_audit
    AFTER UPDATE ON pricing.supplier_credential
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_supplier_credential_no_delete
    BEFORE DELETE ON pricing.supplier_credential
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_supplier_credential_no_truncate
    BEFORE TRUNCATE ON pricing.supplier_credential
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON pricing.supplier_credential FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP TABLE IF EXISTS pricing.supplier_credential;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0051_company_datev_export"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
