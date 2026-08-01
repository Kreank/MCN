"""Django-Settings für das MCN-Backend.

Architekturentscheidungen (siehe backend/README.md):
- Die Datenbank ist database-first: Fachschema und Regeln leben in db/migrations/*.sql.
  Django-Models auf Fachtabellen sind grundsätzlich managed = False.
- Djangos eigene Tabellen (auth, sessions, admin, ...) liegen im Schema "public";
  das Fachschema nutzt eigene Schemas (identity, workflow, invoicing, ...).
- Isolationsstufe bleibt READ COMMITTED (Betriebsannahme aus db/README.md) — nirgends anheben.
- SET LOCAL app.current_user_id / app.status_reason laufen über db_core.db_context,
  nicht über Middleware (Begründung im backend/README.md).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent      # backend/
REPO_ROOT = BASE_DIR.parent                            # Repo-Wurzel (enthält db/)
SQL_MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

SECRET_KEY = os.environ.get(
    "MCN_SECRET_KEY", "django-insecure-nur-fuer-entwicklung"
)
# Fail-safe: Produktion muss nicht daran denken, DEBUG auszuschalten, sondern die
# Entwicklung muss es bewusst einschalten (`MCN_DEBUG=1`). An DEBUG hängen die
# Secure-Flags der Cookies und die Vergabe des Dev-Passworts in seed_demo — ein
# versehentlich mit Default-DEBUG deployter Dienst wäre sonst gleich doppelt offen.
DEBUG = os.environ.get("MCN_DEBUG", "0") == "1"

# Der SECRET_KEY ist die EINZIGE Einstellung, die bei Vergessen ÖFFNET statt zu
# schließen: Aus ihm werden Session-Cookies UND Passwort-Reset-Token abgeleitet.
# Bliebe in Produktion der repo-öffentliche Default (oder der .env.example-
# Platzhalter) stehen, liefen beide mit einem jedem bekannten Schlüssel — Sitzungen
# und fremde Passwort-Resets wären fälschbar, und wegen des Fail-safe-DEBUG liefe
# der Dienst dabei klaglos weiter. Deshalb hier fail-closed: ohne echten Schlüssel
# bricht der Start ab (wie beim fehlenden PDF-Font), statt offen zu laufen.
_UNSICHERE_KEYS = {
    "django-insecure-nur-fuer-entwicklung",
    "HIER_EIN_ERZEUGTES_GEHEIMNIS_EINSETZEN",
}
if not DEBUG and SECRET_KEY in _UNSICHERE_KEYS:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "MCN_SECRET_KEY ist in Produktion (MCN_DEBUG=0) nicht gesetzt — es steht "
        "noch der repo-öffentliche Default bzw. der .env.example-Platzhalter. "
        "Erzeuge einen Schlüssel (z. B. `python -c \"import secrets; "
        "print(secrets.token_urlsafe(64))\"`) und setze MCN_SECRET_KEY."
    )
ALLOWED_HOSTS = os.environ.get("MCN_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "accounts",
    "db_core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Verbindung zur PostgreSQL-Instanz. Zugangsdaten ausschließlich über
# Umgebungsvariablen; Port-Default 55432 entspricht dem lokalen Dev-Container.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("MCN_DB_NAME", "mitra_crm_dev"),
        "USER": os.environ.get("MCN_DB_USER", "postgres"),
        "PASSWORD": os.environ.get("MCN_DB_PASSWORD", ""),
        "HOST": os.environ.get("MCN_DB_HOST", "localhost"),
        "PORT": os.environ.get("MCN_DB_PORT", "55432"),
        # Isolationsstufe NICHT konfigurieren: Django/PostgreSQL-Default ist
        # READ COMMITTED, genau wie db/README.md es verbindlich vorschreibt.
        "TEST": {
            # Erlaubt nebenläufige pytest-Läufe (z. B. mehrere Agenten) auf
            # getrennten Wegwerf-Datenbanken. Ohne die Variable bleibt es beim
            # Django-Default `test_<NAME>`.
            "NAME": os.environ.get("MCN_TEST_DB_NAME") or None,
        },
    }
}

AUTH_USER_MODEL = "accounts.User"

# Anmeldung mit E-Mail statt Benutzername (eigenes Login, kein Fremdanbieter).
# ModelBackend bleibt als zweites Backend, damit der Django-Admin und
# `createsuperuser`/`manage.py shell` weiter über den Benutzernamen funktionieren.
AUTHENTICATION_BACKENDS = [
    "accounts.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Sitzung und CSRF ------------------------------------------------------
# Das Session-Cookie ist HttpOnly (Django-Default) — JavaScript darf es nicht
# lesen. Das CSRF-Cookie muss lesbar sein: das Frontend schickt seinen Wert als
# X-CSRFToken-Header zurück (Djangos Double-Submit-Verfahren).
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12       # ein Arbeitstag
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"

# In Produktion laufen beide Cookies nur über HTTPS. Im Dev-Betrieb (DEBUG)
# gibt es kein TLS, dort muss das Flag aus bleiben.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

CSRF_TRUSTED_ORIGINS = os.environ.get(
    "MCN_CSRF_TRUSTED_ORIGINS", "http://localhost:4200,http://127.0.0.1:8000"
).split(",")

# Hinter einem TLS-terminierenden Reverse Proxy (nginx, siehe deploy/) kommt die
# Anfrage im Container als http an. Ohne diesen Hinweis hält Django jede Anfrage
# für unverschlüsselt und lässt die CSRF-Referer-Prüfung (die nur für HTTPS
# greift) aus. Bewusst OPT-IN: den Header darf man nur trauen, wenn ein Proxy ihn
# garantiert setzt/überschreibt — sonst könnte ihn ein Client selbst fälschen.
_BEHIND_PROXY = os.environ.get("MCN_BEHIND_TLS_PROXY", "0") == "1"
if _BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Dieselbe Topologie-Aussage steuert, ob wir dem Client-IP-Header trauen: NUR
# hinter unserem nginx ist `X-Real-IP` vertrauenswürdig (der Proxy setzt ihn
# ÜBERSCHREIBEND auf die echte Peer-Adresse, deploy/nginx/app.conf.template). Ohne
# Proxy (Dev/Direktbetrieb) darf KEIN Header geglaubt werden — sonst könnte ein
# Client seine IP fälschen und den Login-Brute-Force-Schutz umgehen. Genutzt von
# db_core/services/login_schutz.client_ip.
MCN_TRUST_PROXY_IP = _BEHIND_PROXY

# Eigener Fernet-Schlüssel für die at-rest-Verschlüsselung der KI-Werkzeug-/Geräte-
# Zugangsdaten (db_core/cred_crypto.py). BEWUSST getrennt von MCN_MAIL_KEY, damit
# Geräteflotte und Mailversand nicht gekoppelt sind (Lehre aus dem IDS-Vorfall).
MCN_CRED_KEY = os.environ.get("MCN_CRED_KEY", "")

LANGUAGE_CODE = "de-de"
TIME_ZONE = "UTC"          # Nummernkreis-Jahreszuordnung erfolgt in UTC (db/README.md)
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# Ziel von `collectstatic`. Gebraucht wird das nur im Containerbetrieb: dort
# sammelt der Entrypoint die Django-Admin-Assets in ein Volume, aus dem nginx
# `/static/` ausliefert (Django selbst liefert ohne DEBUG keine statischen
# Dateien aus). Im Dev-Betrieb bleibt das Verzeichnis leer und ungenutzt.
STATIC_ROOT = os.environ.get("MCN_STATIC_ROOT") or (BASE_DIR / "staticfiles")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Mailversand: Transportweg -------------------------------------------
# Django-Default ist der SMTP-Backend. Der gesamte Versand des Systems
# (Rechnung, Angebot, MAHNUNG, Passwort-Reset) läuft über
# django.core.mail.get_connection() und damit über GENAU diese Einstellung —
# es gibt keinen zweiten Weg nach außen.
# Auf einer Demo-Instanz wird sie deshalb auf
#   django.core.mail.backends.console.EmailBackend
# gesetzt: dann landet jede Mail im Container-Log statt beim Kunden. Der
# Default bleibt SMTP, damit eine echte Installation nicht stillschweigend
# nichts versendet.
EMAIL_BACKEND = os.environ.get(
    "MCN_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)

# Angular-Dev-Server. Der Dev-Proxy (frontend/proxy.conf.json) leitet /api auf
# denselben Origin, CORS greift dort also gar nicht — die Einstellung deckt den
# Fall ab, dass das Frontend direkt gegen :8000 spricht. Cookies erfordern dann
# CORS_ALLOW_CREDENTIALS; ein Wildcard-Origin ist damit ausgeschlossen.
CORS_ALLOWED_ORIGINS = os.environ.get(
    "MCN_CORS_ORIGINS", "http://localhost:4200"
).split(",")
CORS_ALLOW_CREDENTIALS = True

# --- Objektspeicher (MinIO / S3) für die GoBD-Beleg-Archivierung -----------
# Binärdaten (Beleg-PDFs) liegen im Object Storage; die Datenbank hält nur den
# Steckbrief (content.file: storage_key, sha256, size). Zugangsdaten kommen
# ausschließlich aus der Umgebung (niemals ins Repo), analog zu MCN_DB_*.
# Für die lokale Entwicklung sind die MinIO-Standardwerte voreingestellt; in
# jeder anderen Umgebung MUSS access/secret über die Env gesetzt werden.
MCN_MINIO_ENDPOINT = os.environ.get("MCN_MINIO_ENDPOINT", "http://127.0.0.1:9100")
MCN_MINIO_ACCESS_KEY = os.environ.get("MCN_MINIO_ACCESS_KEY", "minioadmin")
MCN_MINIO_SECRET_KEY = os.environ.get("MCN_MINIO_SECRET_KEY", "minioadmin")
MCN_MINIO_BUCKET = os.environ.get("MCN_MINIO_BUCKET", "mcn-belege")
# Region ist für MinIO bedeutungslos, aber der S3-Signaturalgorithmus braucht
# einen Wert; us-east-1 ist der MinIO-Default.
MCN_MINIO_REGION = os.environ.get("MCN_MINIO_REGION", "us-east-1")

# --- Mailversand (SMTP-Absenderkonto, verschlüsselt at rest) ---------------
# Fernet-Schlüssel für die Verschlüsselung des SMTP-Passworts (company.mail_account,
# Migration 0046). base64-kodierter 32-Byte-Schlüssel aus der Umgebung — NIE ins
# Repo. Fehlt der Schlüssel, ist Speichern/Versenden fail-closed nicht möglich
# (db_core/mail_crypto.py). Erzeugen: `python -c "from cryptography.fernet import
# Fernet; print(Fernet.generate_key().decode())"`. Kein Default: ein leerer Wert
# erzwingt die bewusste Bereitstellung (wie MCN_DB_PASSWORD).
MCN_MAIL_KEY = os.environ.get("MCN_MAIL_KEY", "")

# --- Passwort-Zurücksetzen (Einmal-Link per E-Mail) ------------------------
# Gültigkeitsdauer des Reset-Tokens (django.contrib.auth.tokens.
# default_token_generator liest PASSWORD_RESET_TIMEOUT). 12 Stunden = 43200 s,
# wie im Hero-CRM. Der Token ist zustandslos und wird single-use, sobald sich
# das Passwort (bzw. last_login) ändert — es gibt keine eigene Token-Tabelle.
PASSWORD_RESET_TIMEOUT = 43200

# Basis-URL des Frontends für den Reset-Link in der E-Mail. Der Link zeigt auf
# die (anmeldefreie) Angular-Route /passwort-zuruecksetzen. In jeder Umgebung
# außer der lokalen Entwicklung MUSS die öffentliche Frontend-URL gesetzt werden
# (sonst verweist die Mail auf localhost).
MCN_FRONTEND_BASE_URL = os.environ.get(
    "MCN_FRONTEND_BASE_URL", "http://localhost:4200"
)

# --- Brute-Force-/Rate-Limit-Schutz am Login (security.login_throttle) ------
# Fehlversuche werden pro Konto+IP und pro IP in einem gleitenden Fenster gezählt
# (db_core/services/login_schutz.py). Erreicht ein Zähler seine Schwelle, wird der
# Schlüssel für MCN_LOGIN_LOCKOUT_SECONDS gesperrt (429). Die Konto+IP-Schwelle ist
# streng (Passwort-Durchprobieren), die IP-Schwelle großzügiger (Credential-
# Spraying über viele Konten). Bewusst KEIN reiner Konto-Lockout — das wäre ein
# Denial-of-Service gegen beliebige Nutzer. Alle env-überschreibbar.
#
# Betriebs-Hinweise (bewusste Trade-offs):
# * Der IP-Zähler trifft eine GETEILTE Ausgangs-IP (NAT/CGNAT/Büro) gemeinsam —
#   viele Nutzer hinter einer IP können die Schwelle zusammen reißen. Ein
#   erfolgreicher Login entlastet nur den Konto+IP-Zähler, NICHT den IP-Zähler
#   (der verfällt nur über sein Fenster). Für große Standorte ggf. IP_THRESHOLD
#   anheben. Ohne unseren nginx (MCN_TRUST_PROXY_IP) läuft ohnehin ALLES auf eine
#   IP (REMOTE_ADDR) — dann den IP-Schutz großzügig oder wirkungslos halten.
# * LOCKOUT_SECONDS SOLLTE >= WINDOW_SECONDS sein. Ist die Sperre kürzer als das
#   Fenster, lockt der erste Fehlversuch nach Ablauf sofort neu (der Zähler steht
#   noch über der Schwelle) — die Sperre gilt faktisch bis zum Fensterende. Die
#   Defaults sind gleich (900/900).
MCN_LOGIN_ACCT_THRESHOLD = int(os.environ.get("MCN_LOGIN_ACCT_THRESHOLD", "5"))
MCN_LOGIN_IP_THRESHOLD = int(os.environ.get("MCN_LOGIN_IP_THRESHOLD", "30"))
MCN_LOGIN_WINDOW_SECONDS = int(os.environ.get("MCN_LOGIN_WINDOW_SECONDS", "900"))
MCN_LOGIN_LOCKOUT_SECONDS = int(os.environ.get("MCN_LOGIN_LOCKOUT_SECONDS", "900"))

# --- Öffentliche Links (security.public_link, Migration 0141) --------------
# Anmeldefreie Routen (heute: „Angebot online annehmen"). Gedrosselt wird über
# DIESELBE DB-Mechanik wie der Login (security.login_register_failure) mit
# eigenem Schlüsselnamensraum `plink:ip:…` — siehe
# db_core/services/oeffentlicher_link.py. Gezählt werden nur FEHLSCHLÄGE
# (unbekanntes/abgelaufenes Token), damit ein Kunde sich durch Neuladen nicht
# selbst aussperrt. Die Schwelle ist deutlich strenger als beim Login: Einen
# Kundenlink öffnet man ein paar Mal, man rät ihn nicht.
MCN_PUBLIC_LINK_IP_THRESHOLD = int(
    os.environ.get("MCN_PUBLIC_LINK_IP_THRESHOLD", "10")
)
MCN_PUBLIC_LINK_WINDOW_SECONDS = int(
    os.environ.get("MCN_PUBLIC_LINK_WINDOW_SECONDS", "900")
)
MCN_PUBLIC_LINK_LOCKOUT_SECONDS = int(
    os.environ.get("MCN_PUBLIC_LINK_LOCKOUT_SECONDS", "900")
)
# Vorgeschlagene Gültigkeit eines Freigabelinks, wenn der Nutzer nichts angibt.
MCN_PUBLIC_LINK_TTL_DAYS = int(os.environ.get("MCN_PUBLIC_LINK_TTL_DAYS", "14"))

# ⚠️ EIGENER SCHALTER für den Versand des Freigabelinks per E-Mail.
#
# Betreff, Text und Versandweg sind fertig verdrahtet (beleg_versand.
# send_quote_freigabe_email) — es fehlt bewusst NUR diese Freischaltung. Solange
# sie auf 0 steht, verweigert der Service den Versand mit 422, BEVOR eine
# Verbindung aufgebaut oder eine Nachricht gebaut wird.
#
# Warum ein zweiter Schalter neben MCN_EMAIL_BACKEND: Der Mail-Backend-Schalter
# gilt für ALLES (Rechnung, Mahnung, Passwort-Reset). Wer ihn eines Tages auf
# SMTP stellt, um Rechnungen zu versenden, macht damit sonst zugleich einen
# fabrikneuen, nie im Feld erprobten Kundenversand scharf — an echte
# Kundenadressen, im Echtbetrieb. Diese Freischaltung ist die bewusste,
# getrennte Entscheidung dafür.
MCN_PUBLIC_LINK_MAIL_ENABLED = os.environ.get("MCN_PUBLIC_LINK_MAIL", "0") == "1"
