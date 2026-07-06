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
DEBUG = os.environ.get("MCN_DEBUG", "1") == "1"
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
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "de-de"
TIME_ZONE = "UTC"          # Nummernkreis-Jahreszuordnung erfolgt in UTC (db/README.md)
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Angular-Dev-Server
CORS_ALLOWED_ORIGINS = os.environ.get(
    "MCN_CORS_ORIGINS", "http://localhost:4200"
).split(",")
