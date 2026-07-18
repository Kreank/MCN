"""Brute-Force-/Rate-Limit-Schutz am Login (security.login_throttle).

Der Session-Login und der Geräte-Bearer-Login (`api/auth.py`) rufen VOR der
Authentifizierung `gesperrt_bis()` und registrieren bei Fehlschlag über
`registriere_fehlversuch()` einen Versuch; ein erfolgreicher Login räumt den
Konto-Zähler mit `erfolg()` ab.

Wichtig: Diese Funktionen laufen **ohne** `business_transaction` — der Aufrufer
ist noch nicht authentifiziert, es gibt keinen `app.current_user_id`. Sie
schreiben per rohem Cursor. Der Fehlversuch MUSS persistiert werden, auch wenn
der Endpunkt anschließend mit 401 antwortet: aktuell (ohne ATOMIC_REQUESTS) per
Autocommit sofort; und selbst mit ATOMIC_REQUESTS bliebe er erhalten, weil
django-ninja den `HttpError` INNERHALB der View abfängt und eine reguläre Antwort
zurückgibt — es propagiert keine Exception zu Djangos atomic-Wrapper, also wird
committet statt zurückgerollt. Die Zähllogik selbst ist atomar (UPSERT).

Schwellen/Fenster kommen aus den Settings (env-überschreibbar), damit Betrieb und
Tests sie ohne Codeänderung anpassen können.
"""
from django.conf import settings
from django.db import connection


def client_ip(request) -> str:
    """Die vertrauenswürdige Client-IP.

    **NICHT X-Forwarded-For.** Unser nginx setzt XFF per
    `proxy_add_x_forwarded_for` — es HÄNGT die echte Peer-Adresse HINTEN an, der
    erste Eintrag bleibt vom Client frei wählbar. Würde man ihn glauben, ließe sich
    der Brute-Force-Schutz durch Rotieren gefälschter IPs komplett umgehen UND ein
    Opfer gezielt von seiner eigenen IP aussperren (der Angreifer schickt dessen IP
    als ersten XFF-Wert).

    Vertrauenswürdig ist allein `X-Real-IP`: nginx setzt sie ÜBERSCHREIBEND auf
    `$remote_addr` (deploy/nginx/app.conf.template), ein mitgeschickter Wert wird
    verworfen. Und selbst das nur, wenn wir tatsächlich HINTER unserem nginx laufen
    (`MCN_TRUST_PROXY_IP`, aus MCN_BEHIND_TLS_PROXY abgeleitet). Sonst — Dev oder
    Direktbetrieb — trauen wir KEINEM Header und nehmen REMOTE_ADDR (dann laufen
    zwar alle auf eine IP, aber der Schutz ist nicht fälschbar).
    """
    if getattr(settings, "MCN_TRUST_PROXY_IP", False):
        real = request.META.get("HTTP_X_REAL_IP")
        if real and real.strip():
            return real.strip()
    return request.META.get("REMOTE_ADDR") or "unbekannt"


def _keys(email: str, ip: str) -> tuple[str, str]:
    e = (email or "").strip().lower()
    return f"acct:{e}|ip:{ip}", f"ip:{ip}"


def _cfg(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def gesperrt_bis(email: str, ip: str):
    """Das späteste aktive `locked_until` der beteiligten Schlüssel, sonst None."""
    acct_key, ip_key = _keys(email, ip)
    with connection.cursor() as cur:
        cur.execute("SELECT security.login_is_locked(%s)", [[acct_key, ip_key]])
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def registriere_fehlversuch(email: str, ip: str) -> None:
    """Verbucht einen Fehlversuch auf beiden Schlüsseln (Konto+IP und IP)."""
    acct_key, ip_key = _keys(email, ip)
    window = _cfg("MCN_LOGIN_WINDOW_SECONDS", 900)
    lockout = _cfg("MCN_LOGIN_LOCKOUT_SECONDS", 900)
    acct_threshold = _cfg("MCN_LOGIN_ACCT_THRESHOLD", 5)
    ip_threshold = _cfg("MCN_LOGIN_IP_THRESHOLD", 30)
    with connection.cursor() as cur:
        cur.execute(
            "SELECT security.login_register_failure(%s, %s, %s, %s)",
            [acct_key, acct_threshold, window, lockout],
        )
        cur.execute(
            "SELECT security.login_register_failure(%s, %s, %s, %s)",
            [ip_key, ip_threshold, window, lockout],
        )


def erfolg(email: str, ip: str) -> None:
    """Räumt nach erfolgreichem Login den Konto+IP-Zähler ab.

    Bewusst NUR den Konto+IP-Schlüssel: Der reine IP-Zähler soll durch sein
    Zeitfenster verfallen, nicht durch einen einzelnen erfolgreichen Login (sonst
    setzte ein erratenes Konto den Spraying-Schutz für die ganze IP zurück)."""
    acct_key, _ip_key = _keys(email, ip)
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM security.login_throttle WHERE bucket_key = %s", [acct_key]
        )


def prune(older_than_seconds: int = 86400) -> int:
    """Räumt alte, nicht (mehr) gesperrte Zeilen weg. Rückgabe: Anzahl gelöschter."""
    with connection.cursor() as cur:
        cur.execute("SELECT security.login_throttle_prune(%s)", [older_than_seconds])
        row = cur.fetchone()
    return row[0] if row else 0
