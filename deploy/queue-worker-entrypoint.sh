#!/bin/sh
# MCN KI-Werkzeug-Queue-Worker — treibt die Tool-Call-Queue in einem schnellen Loop.
#
# WARUM EIN EIGENER, SCHNELLER DIENST (nicht der tägliche scheduler):
# Fälligkeiten dürfen einen Tagesrhythmus haben, eine Werkzeug-Queue nicht — ein
# Sprachmemo um 9:00 darf nicht bis 3 Uhr nachts auf den ASR-Dispatch warten.
# Deshalb ein leichter Tick alle paar Sekunden.
#
# Ausgelegt auf GENAU EINE Instanz: die Ticks dieser Schleife überlappen nie
# (sequenziell + sleep). Der Claim ist per SKIP LOCKED disjunkt; wer auf >1 Replica
# skaliert, hält claim_limit klein gegenüber der Tool-Timeout (siehe runtime.py, F1).
# Ein einzelner leichter `manage.py`-Aufruf pro Tick ist ressourcenschonend; stirbt
# der Dienst, wirft ihn `restart: unless-stopped` wieder an.
set -eu

INTERVALL="${MCN_QUEUE_TICK_SECONDS:-20}"

# Optional ein eigener KI-Service-Account als Akteur (empfohlen). Ohne ihn nimmt das
# Command den ersten aktiven Account.
ACTOR_ARG=""
if [ -n "${MCN_AI_ACTOR_ID:-}" ]; then
    ACTOR_ARG="--actor ${MCN_AI_ACTOR_ID}"
fi

echo "[queue-worker] Tick alle ${INTERVALL} s."
while true; do
    if ! python manage.py ki_tool_queue_tick ${ACTOR_ARG}; then
        echo "[queue-worker] WARNUNG: Tick fehlgeschlagen — nächster Versuch in ${INTERVALL} s."
    fi
    sleep "${INTERVALL}"
done
