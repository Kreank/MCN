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

LANGUAGE_CODE = "de-de"
TIME_ZONE = "UTC"          # Nummernkreis-Jahreszuordnung erfolgt in UTC (db/README.md)
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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
