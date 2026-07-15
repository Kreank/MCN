#!/usr/bin/env bash
# Nebenläufigkeitstest KI-Werkzeug-Queue (Migration 0106):
# claim_batch (db_core/ai/runtime.py) verlässt sich auf
#   SELECT … ORDER BY … LIMIT n FOR UPDATE SKIP LOCKED
# damit zwei gleichzeitige Ticks (queue-worker-Instanzen) NIE denselben tool_call
# greifen. Ohne SKIP LOCKED würde Session 2 auf Session 1s Sperre WARTEN und danach
# dieselben Zeilen sehen -> Doppel-Dispatch ans Gerät.
#
# Erwartung:
#   1. Session 1 sperrt 2 QUEUED-Calls (FOR UPDATE SKIP LOCKED LIMIT 2) und hält die
#      Transaktion 3 s offen.
#   2. Session 2 claimt parallel (SKIP LOCKED LIMIT 4) -> bekommt die ANDEREN Calls,
#      NIE die von Session 1 gesperrten (kein Warten).
#   3. Schnittmenge leer, Vereinigung = alle 4.
#
# Hinterlässt synthetische Daten (Präfix KONKQ-) — nur gegen Wegwerf-Datenbanken.
# Aufruf: bash db/tests/nebenlaeufigkeitstest_tool_queue.sh [container] [db]
set -u
CONTAINER="${1:-mitra-crm-test}"
DB="${2:-mitra_crm_test}"
PSQL="docker exec -i $CONTAINER psql -qAt -U postgres -d $DB"
RUN="KONKQ-$(date +%s)"

docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" >/dev/null <<SQL
DO \$\$
DECLARE v_user uuid; v_wf uuid; v_tool uuid;
BEGIN
    INSERT INTO security.app_user (display_name) VALUES ('$RUN-User') RETURNING id INTO v_user;
    INSERT INTO ai.workflow_run (workflow_name, workflow_version, triggered_by_user_id)
    VALUES ('$RUN-wf', 'v1', v_user) RETURNING id INTO v_wf;
    INSERT INTO ai.tool (tool_key, label, capability, invocation_mode, endpoint_url)
    VALUES ('$RUN-tool', 'ASR', 'ASR', 'ASYNC', 'https://h/asr') RETURNING id INTO v_tool;
    INSERT INTO ai.tool_call (workflow_run_id, tool_id, capability, step_key) VALUES
        (v_wf, v_tool, 'ASR', '$RUN-1'), (v_wf, v_tool, 'ASR', '$RUN-2'),
        (v_wf, v_tool, 'ASR', '$RUN-3'), (v_wf, v_tool, 'ASR', '$RUN-4');
END;
\$\$;
SQL

BASE="SELECT id FROM ai.tool_call WHERE step_key LIKE '$RUN-%' AND status='QUEUED' ORDER BY step_key"
TMP=$(mktemp -d)

# Session 1: 2 Calls sperren, Transaktion offen halten
docker exec -i "$CONTAINER" psql -qAt -v ON_ERROR_STOP=1 -U postgres -d "$DB" > "$TMP/s1.log" 2>&1 <<SQL &
BEGIN;
$BASE LIMIT 2 FOR UPDATE SKIP LOCKED;
SELECT pg_sleep(3);
COMMIT;
SQL
PID1=$!
sleep 1

# Session 2: parallel claimen -> muss die ANDEREN bekommen (SKIP LOCKED, kein Warten)
$PSQL > "$TMP/s2.log" 2>&1 <<SQL
$BASE LIMIT 4 FOR UPDATE SKIP LOCKED;
SQL
wait $PID1 2>/dev/null

S1=$(grep -E '^[0-9a-f-]{36}$' "$TMP/s1.log" | sort)
S2=$(grep -E '^[0-9a-f-]{36}$' "$TMP/s2.log" | sort)
N1=$(echo "$S1" | grep -c .)
N2=$(echo "$S2" | grep -c .)
OVERLAP=$(comm -12 <(echo "$S1") <(echo "$S2") | grep -c .)
UNION=$(printf '%s\n%s\n' "$S1" "$S2" | grep -E '^[0-9a-f-]{36}$' | sort -u | grep -c .)
rm -rf "$TMP"

echo "Session 1 sperrte $N1, Session 2 claimte $N2; Schnittmenge=$OVERLAP, Vereinigung=$UNION"
if [ "$OVERLAP" -eq 0 ] && [ "$UNION" -eq 4 ] && [ "$N1" -eq 2 ] && [ "$N2" -eq 2 ]; then
  echo "NEBENLAEUFIGKEITSTEST TOOL-QUEUE BESTANDEN: SKIP LOCKED verhindert Doppel-Claim"
  exit 0
else
  echo "NEBENLAEUFIGKEITSTEST TOOL-QUEUE FEHLGESCHLAGEN"
  exit 1
fi
