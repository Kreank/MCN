"""Neue Fachtabelle company.mail_account — firmenweites SMTP-Absenderkonto.

Hand-SQL nach db/README.md: neue Fachtabelle als Django-Migration mit RunSQL,
Schutzstandard (updated_at/Audit/No-Delete/No-Truncate/REVOKE). Muster: die
Tabellen aus 0023 (0026 db_core), company.company_profile/branch/trade.

Fachquelle: docs/roadmap/14 (Mailversand/Absenderkonto). Dieses Konto liefert die
verschlüsselten SMTP-Zugangsdaten für den Versand (Passwort-Chiffre, nie
Klartext) und den Absender (from_address/from_name). Es gehört ins Schema
`company`, weil es firmenweite Absenderkonfiguration ist (eine Wahrheit über das
ausstellende Unternehmen), keine Benutzer- oder Rechtefrage.

Grundsatzentscheidungen:

1. **Passwort ausschließlich verschlüsselt (`password_encrypted bytea`).** Der
   Klartext wird NIE in der DB abgelegt. Die App verschlüsselt symmetrisch mit
   Fernet (cryptography) vor dem Schreiben und entschlüsselt nur zur Sendezeit;
   der Schlüssel liegt in der Umgebung (`MCN_MAIL_KEY`), nicht in der DB. Fehlt
   der Schlüssel, ist weder Speichern noch Senden möglich (fail-closed in der
   App). Die Spalte ist nullable: ein Konto kann (theoretisch) ohne Passwort
   auskommen (offenes Relay), und beim Update bleibt ein nicht mitgesendetes
   Passwort unverändert.

2. **Höchstens ein aktives Konto** über einen partiellen UNIQUE-Index
   `(active) WHERE active`. Der Versand lädt genau das aktive Konto; mehrere
   gleichzeitig aktive Absenderkonten wären mehrdeutig. Deaktivieren statt
   Löschen (GoBD/No-Delete; historische Bezüge bleiben).

3. **`port integer`, nicht `smallint`.** Der gültige TCP-Portbereich reicht bis
   65535; ein `smallint` (max. 32767) kann diesen oberen Rand physisch nicht
   halten — ein `CHECK (port BETWEEN 1 AND 65535)` auf einer smallint-Spalte
   wäre am oberen Ende nie erfüllbar (Overflow vor der Prüfung). Deshalb bewusst
   `integer` mit dem vollständigen Bereichs-CHECK. Die üblichen SMTP-Ports
   (25/465/587/2525) liegen ohnehin darunter.

4. **from_address grober E-Mail-CHECK** (eine @, kein Whitespace, ein Punkt in
   der Domain). Feinvalidierung passiert in der App; der CHECK verhindert nur
   offensichtlichen Unsinn, damit auf jedem Beleg/Absender etwas Sinnvolles
   steht.
"""
from django.db import migrations

CREATE_SQL = r"""
CREATE TABLE company.mail_account (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label              text NOT NULL CHECK (btrim(label) <> ''),
    host               text NOT NULL CHECK (btrim(host) <> ''),
    -- integer (nicht smallint): der Portbereich reicht bis 65535 (siehe Docstring).
    port               integer NOT NULL CHECK (port BETWEEN 1 AND 65535),
    security           text NOT NULL CHECK (security IN ('NONE', 'STARTTLS', 'SSL')),
    username           text NULL,
    -- Fernet-Chiffre des SMTP-Passworts. NIE Klartext. Nullable: unverändert
    -- lassen beim Update bzw. Konto ohne Passwort.
    password_encrypted bytea NULL,
    from_address       text NOT NULL
                       CHECK (from_address ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    from_name          text NULL,
    -- Deaktivieren statt Löschen; höchstens ein aktives Konto (partieller Unique).
    active             boolean NOT NULL DEFAULT true,
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Höchstens ein aktives Absenderkonto.
CREATE UNIQUE INDEX uq_mail_account_single_active
    ON company.mail_account (active) WHERE active;

CREATE TRIGGER trg_mail_account_updated_at
    BEFORE UPDATE ON company.mail_account
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_mail_account_audit
    AFTER UPDATE ON company.mail_account
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_mail_account_no_delete
    BEFORE DELETE ON company.mail_account
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_mail_account_no_truncate
    BEFORE TRUNCATE ON company.mail_account
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON company.mail_account FROM PUBLIC;
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS company.mail_account;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0045_statuscatalog_statustransition"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
