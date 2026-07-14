# MCN Backend — Django + gunicorn.
#
# Build-Kontext ist die REPO-WURZEL (nicht backend/): die Settings erwarten die
# SQL-Migrationen unter <repo>/db/migrations (config.settings.SQL_MIGRATIONS_DIR).
# Ohne db/ im Image kommt `migrate` nicht durch.
#
#   docker build -f deploy/backend.Dockerfile -t mcn-backend .
#
# Dasselbe Image betreibt auch den Scheduler-Container (anderer Entrypoint).

# ---------------------------------------------------------------------------
# 1. Abhängigkeiten (uv, aus offiziellem PyPI)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS deps

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.11.19

WORKDIR /app/backend
# Nur die Sperrdateien: so bleibt der teure Auflösungs-Layer über Codeänderungen
# hinweg im Cache.
COPY backend/pyproject.toml backend/uv.lock ./
# --frozen: die uv.lock ist bindend. Weicht sie von pyproject.toml ab, bricht der
# Build ab, statt still eine andere Version zu ziehen.
RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------------------
# 2. Laufzeit
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    DJANGO_SETTINGS_MODULE=config.settings

# tzdata: der Scheduler rechnet in Betriebszeit (Europe/Berlin), nicht in UTC.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app/backend

# Anwendungscode. db/ MUSS mit — siehe Kopf.
COPY backend/ /app/backend/
COPY db/ /app/db/

# Die eingebettete PDF-Schrift ist KEIN Systempaket, sondern liegt im Repo
# (db_core/assets/fonts/DejaVuSans*.ttf). Ohne sie bricht das Beleg-PDF erst im
# Betrieb — deshalb wird ihr Vorhandensein hier beim Bauen erzwungen.
RUN test -f /app/backend/db_core/assets/fonts/DejaVuSans.ttf \
 && test -f /app/backend/db_core/assets/fonts/DejaVuSans-Bold.ttf \
 || (echo "FEHLER: DejaVu-Schriften fehlen — Beleg-PDF/ZUGFeRD wuerden im Betrieb brechen." && exit 1)

# Entwicklungsreste dürfen nicht ins Image (eine mitkopierte .venv würde den PATH
# verwirren, __pycache__ ist Ballast). .dockerignore hält sie schon draußen —
# hier die zweite Bremse.
RUN rm -rf /app/backend/.venv /app/backend/staticfiles \
 && find /app -name '__pycache__' -type d -prune -exec rm -rf {} +

COPY deploy/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
COPY deploy/scheduler-entrypoint.sh /usr/local/bin/scheduler-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh /usr/local/bin/scheduler-entrypoint.sh

# Kein root. /srv/static ist das Ziel von collectstatic (MCN_STATIC_ROOT) und
# wird als Volume auch von nginx gelesen; das Verzeichnis muss dem App-Benutzer
# gehören, sonst scheitert collectstatic beim ersten Start.
RUN useradd --system --create-home --uid 10001 mcn \
 && mkdir -p /srv/static \
 && chown -R mcn:mcn /srv/static /app
USER mcn

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/backend-entrypoint.sh"]
