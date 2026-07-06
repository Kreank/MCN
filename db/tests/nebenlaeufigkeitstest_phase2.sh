#!/usr/bin/env bash
# Nebenläufigkeitstest Phase 2: parallele Nummernvergabe (B-11/B-12).
# 4 Sessions legen je 15 Aufträge gleichzeitig an; Erwartung: 60 Aufträge,
# 60 verschiedene Auftragsnummern, keine Kollision.
# Hinterlässt synthetische Daten (Präfix KONK2-) — nur gegen Wegwerf-Datenbanken.
#
# Aufruf: bash db/tests/nebenlaeufigkeitstest_phase2.sh [container] [db]

set -u
CONTAINER="${1:-mitra-crm-test}"
DB="${2:-mitra_crm_test}"
PSQL="docker exec -i $CONTAINER psql -qAt -U postgres -d $DB"

PROP=$($PSQL <<'SQL'
WITH a AS (
  INSERT INTO identity.address (street, house_number, postal_code, city)
  VALUES ('Parallelweg', '1', '11111', 'Rennstadt') RETURNING id
)
INSERT INTO property.property (name, address_id, property_type)
SELECT 'KONK2-Objekt', a.id, 'RENTAL_PROPERTY' FROM a RETURNING id;
SQL
)
echo "Fixture: property=$PROP"

run_inserter() {
  docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" <<SQL >/dev/null 2>&1
DO \$\$
DECLARE i integer;
BEGIN
  FOR i IN 1..15 LOOP
    INSERT INTO workflow.work_order (title, property_id)
    VALUES ('KONK2-Auftrag', '$PROP');
  END LOOP;
END;
\$\$;
SQL
  echo "INSERTER-EXIT:$?"
}

PIDS=()
for s in 1 2 3 4; do run_inserter & PIDS+=($!); done
wait "${PIDS[@]}"

VERDICT=$($PSQL <<SQL
SELECT CASE WHEN count(*) = 60 AND count(DISTINCT order_number) = 60
       THEN 'PASS' ELSE 'FAIL (' || count(*) || '/' || count(DISTINCT order_number) || ')' END
FROM workflow.work_order WHERE title = 'KONK2-Auftrag' AND property_id = '$PROP';
SQL
)
echo "Ergebnis: $VERDICT"
if [ "$VERDICT" = "PASS" ]; then
  echo "NEBENLAEUFIGKEITSTEST PHASE 2 BESTANDEN: 60 parallele Aufträge, 60 eindeutige Nummern"
  exit 0
else
  echo "NEBENLAEUFIGKEITSTEST PHASE 2 FEHLGESCHLAGEN"
  exit 1
fi
