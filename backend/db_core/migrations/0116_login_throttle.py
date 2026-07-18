"""security.login_throttle — Brute-Force-/Rate-Limit-Schutz am Login.

Hand-SQL nach db/README.md. Der Login (`api/auth.py`: Session-Login **und**
Geräte-Bearer-Login) hatte keinerlei Drosselung — ein Angreifer konnte Passwörter
unbegrenzt durchprobieren. Diese Tabelle zählt Fehlversuche pro **Schlüssel** in
einem gleitenden Fenster und sperrt den Schlüssel bei Überschreitung.

## Warum DB-gestützt (nicht in-memory)

Der Dienst läuft mit mehreren gunicorn-Workern (und potenziell mehreren
Containern) und wird neu gestartet. Ein In-Memory-Zähler wäre pro Prozess
getrennt und nach jedem Neustart weg — ein Scheinschutz. Postgres ist der einzige
geteilte, persistente Zustand (kein Redis im Stack), also lebt der Zähler hier.

## Schlüssel-Wahl (DoS-bewusst)

- `acct:<email>|ip:<ip>` — pro Konto **und** IP. Stoppt Passwort-Durchprobieren
  aus einer Quelle, **ohne** dass ein Angreifer das Opfer aussperren kann: die
  Sperre hängt an *seiner* IP, nicht am Konto allein. (Ein reiner Konto-Lockout
  wäre ein triviales Denial-of-Service gegen beliebige Nutzer.)
- `ip:<ip>` — pro IP global. Fängt das Durchprobieren **vieler** Konten von einer
  Quelle (Credential-Spraying) mit einer höheren Schwelle ab.

## Bewusst OHNE Schutzstandard (No-Delete/Audit)

Anders als Fachtabellen ist dies **transienter Sicherheits-Zustand** (ein Cache,
kein Geschäftsvorfall, kein GoBD-Beleg). Er MUSS beschnitten werden können (sonst
wächst die Tabelle mit jedem je gesehenen (Konto,IP)-Paar) — deshalb KEIN
`forbid_mutation` auf DELETE/TRUNCATE und KEIN Audit. Analog zu `django_session`.
Geschrieben wird VOR der Authentifizierung, also ohne `app.current_user_id`; die
Funktionen laufen deshalb ohne den Benutzerkontext der `business_transaction`.

## Rückwärts

Reine Infrastruktur ohne Fachdaten → vollständig reversibel.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE security.login_throttle (
    bucket_key    text PRIMARY KEY,
    fail_count    integer NOT NULL DEFAULT 0,
    window_start  timestamptz NOT NULL DEFAULT now(),
    locked_until  timestamptz NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
-- Für den Prune-Lauf (alte Zeilen wegräumen).
CREATE INDEX idx_login_throttle_updated ON security.login_throttle (updated_at);

-- Einen Fehlversuch für einen Schlüssel verbuchen (atomar per UPSERT). Ist das
-- Fenster abgelaufen, beginnt der Zähler neu. Erreicht der Zähler die Schwelle,
-- wird bis now()+lockout gesperrt. Rückgabe: locked_until (oder NULL).
CREATE FUNCTION security.login_register_failure(
    p_key text,
    p_threshold integer,
    p_window_seconds integer,
    p_lockout_seconds integer
) RETURNS timestamptz
LANGUAGE plpgsql AS $$
DECLARE
    v_now    timestamptz := now();
    v_count  integer;
    v_locked timestamptz;
BEGIN
    INSERT INTO security.login_throttle AS t (bucket_key, fail_count, window_start, updated_at)
    VALUES (p_key, 1, v_now, v_now)
    ON CONFLICT (bucket_key) DO UPDATE SET
        fail_count = CASE
            WHEN t.window_start < v_now - make_interval(secs => p_window_seconds) THEN 1
            ELSE t.fail_count + 1
        END,
        window_start = CASE
            WHEN t.window_start < v_now - make_interval(secs => p_window_seconds) THEN v_now
            ELSE t.window_start
        END,
        updated_at = v_now
    RETURNING t.fail_count, t.locked_until INTO v_count, v_locked;

    IF v_count >= p_threshold THEN
        v_locked := v_now + make_interval(secs => p_lockout_seconds);
        UPDATE security.login_throttle
           SET locked_until = v_locked, updated_at = v_now
         WHERE bucket_key = p_key;
    END IF;
    RETURN v_locked;
END;
$$;

-- Ist einer der Schlüssel aktuell gesperrt? Rückgabe: das späteste noch aktive
-- locked_until (für die Antwort/Retry-Hinweis) oder NULL.
CREATE FUNCTION security.login_is_locked(p_keys text[])
RETURNS timestamptz
LANGUAGE sql STABLE AS $$
    SELECT max(locked_until)
      FROM security.login_throttle
     WHERE bucket_key = ANY(p_keys)
       AND locked_until IS NOT NULL
       AND locked_until > now();
$$;

-- Alte Zeilen wegräumen (für den Prune-Lauf). Gesperrte bleiben bis zum Ablauf
-- ihrer Sperre erhalten.
CREATE FUNCTION security.login_throttle_prune(p_older_than_seconds integer)
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v_deleted integer;
BEGIN
    WITH del AS (
        DELETE FROM security.login_throttle
         WHERE updated_at < now() - make_interval(secs => p_older_than_seconds)
           AND (locked_until IS NULL OR locked_until < now())
        RETURNING 1
    )
    SELECT count(*) INTO v_deleted FROM del;
    RETURN v_deleted;
END;
$$;
"""

REVERSE_SQL = r"""
DROP FUNCTION IF EXISTS security.login_throttle_prune(integer);
DROP FUNCTION IF EXISTS security.login_is_locked(text[]);
DROP FUNCTION IF EXISTS security.login_register_failure(text, integer, integer, integer);
DROP TABLE IF EXISTS security.login_throttle;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0115_devicetoken_model"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
