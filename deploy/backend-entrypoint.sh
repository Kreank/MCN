#!/bin/sh
# MCN Backend — Start im Container.
#
#   1. auf Postgres warten (echte Verbindung, nicht nur „Port offen")
#   2. migrate
#   3. collectstatic  (nginx liefert /static/ aus dem gemeinsamen Volume)
#   4. Demo-Seed
#   5. Demo-Passwörter setzen
#   6. gunicorn
#
# Schritt 4 und 5 laufen nur, wenn sie ausdrücklich eingeschaltet sind
# (MCN_SEED=1 bzw. MCN_DEMO_PASSWORD gesetzt). Eine Produktivinstanz setzt
# beides nicht und bekommt reines migrate + gunicorn.
set -eu

banner() {
    echo "================================================================"
    echo "  $*"
    echo "================================================================"
}

# ---------------------------------------------------------------------------
# 1. Auf die Datenbank warten
# ---------------------------------------------------------------------------
# `depends_on: service_healthy` in der compose-Datei prüft mit pg_isready IM
# Postgres-Container. Das ist der eigentliche Wächter. Diese Schleife ist die
# zweite Bremse: sie prüft, dass DIESER Container sich mit DIESEN Zugangsdaten
# tatsächlich verbinden kann (falsches Passwort, falsche DB, Netz noch nicht da).
# Ein Port, der offen ist, beweist davon nichts.
echo "[entrypoint] warte auf die Datenbank ..."
python - <<'PY'
import sys, time
import django
from django.db import connections
from django.db.utils import OperationalError

django.setup()
frist = time.monotonic() + 90
while True:
    try:
        connections["default"].ensure_connection()
        print("[entrypoint] Datenbank ist bereit.")
        break
    except OperationalError as exc:
        if time.monotonic() > frist:
            print(f"[entrypoint] FEHLER: Datenbank nach 90 s nicht erreichbar: {exc}")
            sys.exit(1)
        time.sleep(2)
    finally:
        connections["default"].close()
PY

# ---------------------------------------------------------------------------
# 2. Migrationen — die Datenbank ist die Quelle der Wahrheit (db/migrations/*.sql
#    laufen über die Django-Migrationskette mit).
# ---------------------------------------------------------------------------
echo "[entrypoint] migrate ..."
python manage.py migrate --noinput

# ---------------------------------------------------------------------------
# 3. Statische Dateien (Django-Admin). Ohne DEBUG liefert Django selbst nichts
#    aus — nginx tut es, aus dem gemeinsamen Volume MCN_STATIC_ROOT.
# ---------------------------------------------------------------------------
echo "[entrypoint] collectstatic ..."
python manage.py collectstatic --noinput --clear >/dev/null

# ---------------------------------------------------------------------------
# 4. Demo-Seed
# ---------------------------------------------------------------------------
# ACHTUNG, die Falle: `seed_demo` (und der spätere `seed_szenario`) BRICHT AB,
# wenn settings.DEBUG aus ist, und vergibt Login-Passwörter ebenfalls nur bei
# DEBUG (_ensure_login → set_unusable_password). Auf dem Server läuft das System
# aber mit MCN_DEBUG=0 (fail-safe).
#
# Deshalb bekommt GENAU DIESER EINE Aufruf MCN_DEBUG=1 mit — als
# Prozessumgebung, die nirgendwo sonst hinreicht. Das ist ein
# Management-Command, kein Webserver: es werden keine Anfragen bedient, keine
# Cookies gesetzt, keine Fehlerseiten ausgeliefert. gunicorn (Schritt 6) startet
# unverändert mit MCN_DEBUG=0.
#
# Die Passwortvergabe verlässt sich NICHT auf diesen Kniff — sie passiert
# ausdrücklich in Schritt 5, damit sie auch dann noch trägt, wenn der Seed-Befehl
# gegen `seed_szenario` getauscht wird (MCN_SEED_COMMAND).
#
# MCN_SEED_ARGS wird bewusst UNGEQUOTET expandiert (Wortauftrennung erwünscht) —
# so lässt sich z. B. `--mit-passwoertern` durchreichen, ohne dieses Skript
# anzufassen.
if [ "${MCN_SEED:-0}" = "1" ]; then
    SEED_COMMAND="${MCN_SEED_COMMAND:-seed_demo}"
    echo "[entrypoint] Seed: ${SEED_COMMAND} ${MCN_SEED_ARGS:-} (idempotent) ..."
    # shellcheck disable=SC2086
    if ! MCN_DEBUG=1 python manage.py "${SEED_COMMAND}" ${MCN_SEED_ARGS:-}; then
        banner "WARNUNG: Seed '${SEED_COMMAND}' fehlgeschlagen — die Demo hat keine Daten."
    fi
fi

# ---------------------------------------------------------------------------
# 5. Demo-Passwörter
# ---------------------------------------------------------------------------
# Ohne diesen Schritt tragen alle Seed-Konten ein unbenutzbares Passwort und
# niemand kommt in die Demo. Der Befehl verlangt MCN_DEMO_INSTANZ=1 und ist
# damit auf einem Produktivsystem wirkungslos.
if [ -n "${MCN_DEMO_PASSWORD:-}" ]; then
    echo "[entrypoint] Demo-Passwörter setzen ..."
    if ! python manage.py demo_passwoerter_setzen; then
        banner "WARNUNG: Demo-Passwoerter nicht gesetzt — der Login wird scheitern."
    fi
fi

# ---------------------------------------------------------------------------
# 6. gunicorn — kein Port nach außen, nur im Docker-Netz erreichbar (nginx).
# ---------------------------------------------------------------------------
echo "[entrypoint] gunicorn startet ..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${MCN_GUNICORN_WORKERS:-3}" \
    --timeout "${MCN_GUNICORN_TIMEOUT:-120}" \
    --access-logfile - \
    --error-logfile - \
    --capture-output
