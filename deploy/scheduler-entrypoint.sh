#!/bin/sh
# MCN Fälligkeiten-Scheduler — führt `wartung_faellige_ausloesen` täglich aus.
#
# WARUM EIN EIGENER CONTAINER UND KEIN HOST-CRON:
# Ein Host-Cron ist ein Schritt, den der Betreiber von Hand einrichten muss und
# beim Umzug auf den nächsten Server vergisst. Fehlt er, passiert etwas
# Tückisches: Das System läuft, sieht gesund aus — nur Wartungen, Prüffristen
# und Gewährleistungen werden NIE fällig, und niemand versteht warum. Als
# compose-Dienst kommt der Scheduler mit dem Stack hoch, hat dieselben Env-Vars,
# dieselbe Datenbank, dieselben Logs (`docker compose logs scheduler`) und wird
# von `restart: unless-stopped` wieder angeworfen, wenn er stirbt.
#
# Kein cron-Daemon: der bräuchte ein Zusatzpaket, einen zweiten Log-Weg und eine
# eigene Umgebungsübergabe. Eine Schleife, die bis zur nächsten Uhrzeit schläft,
# tut dasselbe mit weniger beweglichen Teilen.
#
# Doppelte Läufe sind harmlos: das Command ist idempotent, und die Idempotenz
# garantieren drei partielle UNIQUE-Indizes in der DATENBANK — nicht dieser Code.
set -eu

STUNDE="${MCN_SCHEDULER_HOUR:-3}"
MINUTE="${MCN_SCHEDULER_MINUTE:-15}"
export TZ="${MCN_SCHEDULER_TZ:-Europe/Berlin}"

lauf() {
    echo "[scheduler] $(date '+%F %T %Z') — wartung_faellige_ausloesen"
    if ! python manage.py wartung_faellige_ausloesen; then
        echo "[scheduler] WARNUNG: Lauf fehlgeschlagen — nächster Versuch morgen."
    fi
    # Den Login-Drosselzähler (security.login_throttle, Migration 0116) beschneiden.
    # Er ist transienter Cache; ohne diesen Prune wüchse die Tabelle mit jedem je
    # gesehenen (Konto,IP)-Paar. Eigener Fehlerpfad, damit ein Prune-Fehler den
    # Fälligkeitslauf nicht verschluckt (und umgekehrt).
    if ! python manage.py login_throttle_aufraeumen; then
        echo "[scheduler] WARNUNG: login_throttle_aufraeumen fehlgeschlagen."
    fi
}

echo "[scheduler] täglicher Lauf um ${STUNDE}:${MINUTE} (${TZ})."

# Erster Lauf sofort (optional). Für die Demo praktisch: dann steht „Was steht
# an?" nicht bis morgen früh leer da.
if [ "${MCN_SCHEDULER_RUN_ON_START:-0}" = "1" ]; then
    lauf
fi

while true; do
    jetzt=$(date +%s)
    ziel=$(date -d "today ${STUNDE}:${MINUTE}" +%s)
    if [ "${ziel}" -le "${jetzt}" ]; then
        ziel=$(date -d "tomorrow ${STUNDE}:${MINUTE}" +%s)
    fi
    schlaf=$((ziel - jetzt))
    echo "[scheduler] nächster Lauf in ${schlaf} s."
    sleep "${schlaf}"
    lauf
done
