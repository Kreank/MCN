"""security.device_token — Bearer-Token je Gerät für die native App.

Hand-SQL nach db/README.md (Fachschema-Änderungen nur als RunSQL). Neben der
Session-Cookie-Auth des Web-Cockpits meldet sich die Android-App (Projekt
MCN-APP) mit einem Bearer-Token an.

## Schema-Wahl: `security`

Die Tabelle liegt in `security`, dem Schema der Authentifizierungs-/
Autorisierungs-nahen Tabellen: `security.app_user` (fachliche Identität),
`security.role`, `security.user_role`, `security.role_permission`. Ein
Geräte-Token ist ein Anmelde-Credential und gehört fachlich genau dorthin —
direkt neben `app_user`, dessen UUID es spiegelt. Der Fremdschlüssel
`app_user_id` bleibt so schemalokal; der zweite Fremdschlüssel auf das
Django-Login-Konto (`public.accounts_user`, von Django verwaltet) zeigt
schema-übergreifend dorthin.

## Sicherheit

`token_hash` ist der SHA-256-Hex des Tokens — das Klartext-Token wird NIE
gespeichert (es verlässt den Server nur einmalig in der Login-Antwort). `UNIQUE`
auf dem Hash. Widerruf über `revoked_at` (stilllegen statt löschen).

## Schutzstandard (Muster property.room / 0101)

No-Delete (Token werden widerrufen, nicht gelöscht) + No-Truncate + Audit auf
UPDATE (wer wann widerrufen hat). Der Audit-Trigger feuert nur AFTER UPDATE; der
INSERT beim Login ist damit nicht auditiert und verlangt kein
`app.current_user_id` — der Login-Insert eines Kontos ohne `app_user_id`
funktioniert dadurch über einen einfachen atomaren Insert (Bootstrapping wie in
seed_demo). `last_used_at` wird bewusst NICHT bei jedem Request fortgeschrieben
(das erzwänge eine Audit-Schreibtransaktion je Lese-Request) — die Spalte bleibt
in diesem Slice NULL, sie steht für eine spätere, grob gedrosselte Nutzung bereit.

## Rückwärts

Reine Struktur ohne Fachdaten-Altlast → vollständig reversibel (Trigger + Tabelle
fallen). Sobald echte Token existieren, gilt wie im Repo üblich: nur vorwärts.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE security.device_token (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       bigint NOT NULL REFERENCES public.accounts_user(id),
    app_user_id   uuid NULL REFERENCES security.app_user(id),
    token_hash    text NOT NULL UNIQUE,
    device_name   text NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz NULL,
    revoked_at    timestamptz NULL
);

CREATE INDEX idx_device_token_user ON security.device_token (user_id);
CREATE INDEX idx_device_token_app_user ON security.device_token (app_user_id)
    WHERE app_user_id IS NOT NULL;

-- Schutzstandard — Muster property.room (0086) / property.technical_asset (0101).
CREATE TRIGGER trg_device_token_audit
    AFTER UPDATE ON security.device_token
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_device_token_no_delete
    BEFORE DELETE ON security.device_token
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_device_token_no_truncate
    BEFORE TRUNCATE ON security.device_token
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON security.device_token FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_device_token_no_truncate ON security.device_token;
DROP TRIGGER IF EXISTS trg_device_token_no_delete ON security.device_token;
DROP TRIGGER IF EXISTS trg_device_token_audit ON security.device_token;
DROP TABLE IF EXISTS security.device_token;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0113_beleg_vorgang"),
        # Der Fremdschlüssel zeigt auf die von Django verwaltete Login-Tabelle;
        # deren Migration muss vorher gelaufen sein.
        ("accounts", "0002_alter_user_email_user_uniq_user_email_ci"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
