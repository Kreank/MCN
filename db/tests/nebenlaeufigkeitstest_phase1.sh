#!/usr/bin/env bash
# Nebenläufigkeitstest Phase 1 (F-04/F-07): paralleler 100-Prozent-Konflikt aus
# zwei echten Sessions. Erwartung: genau eine Session gewinnt; der Endstand ist
# exakt 100 Prozent; die zweite Session bricht mit Fehler ab (kein stiller
# Falschstand). Betriebsannahme: READ COMMITTED (siehe db/README.md).
#
# Aufruf:   bash db/tests/nebenlaeufigkeitstest_phase1.sh [container] [db]
# Hinweis:  Der Test legt eigene synthetische Daten an (Präfix KONK-TEST) und
#           lässt sie stehen, da Sitzungsübergreifendes Rollback nicht möglich
#           ist. Nur gegen Wegwerf-/Testdatenbanken ausführen.

set -u
CONTAINER="${1:-mitra-crm-test}"
DB="${2:-mitra_crm_test}"
PSQL="docker exec -i $CONTAINER psql -qAt -U postgres -d $DB"

# --- Fixtures -----------------------------------------------------------------
IDS=$($PSQL <<'SQL'
WITH a AS (
  INSERT INTO identity.address (street, house_number, postal_code, city)
  VALUES ('Konkurrenzweg', '9', '99999', 'Teststadt') RETURNING id
), p1 AS (
  INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'KONK-TEST Owner 1') RETURNING id
), p2 AS (
  INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'KONK-TEST Owner 2') RETURNING id
), p3 AS (
  INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'KONK-TEST Owner 3') RETURNING id
), pr AS (
  INSERT INTO property.property (name, address_id, property_type)
  SELECT 'KONK-TEST Objekt', a.id, 'RENTAL_PROPERTY' FROM a RETURNING id
), b AS (
  INSERT INTO property.building (property_id, building_number)
  SELECT pr.id, 'K1' FROM pr RETURNING id, property_id
), u AS (
  INSERT INTO property.unit (building_id, property_id, unit_type, unit_number)
  SELECT b.id, b.property_id, 'APARTMENT', 'KONK-WE-01' FROM b RETURNING id
), per AS (
  INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference)
  SELECT u.id, 'PARTIAL', DATE '2020-01-01', 'MANUAL', 'KONK-TEST' FROM u RETURNING id
), i1 AS (
  INSERT INTO identity.person (party_id, first_name, last_name) SELECT p1.id, 'K', 'Eins' FROM p1
), i2 AS (
  INSERT INTO identity.person (party_id, first_name, last_name) SELECT p2.id, 'K', 'Zwei' FROM p2
), i3 AS (
  INSERT INTO identity.person (party_id, first_name, last_name) SELECT p3.id, 'K', 'Drei' FROM p3
), base AS (
  INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id, share_numerator, share_denominator, confirmation_status)
  SELECT per.id, p1.id, 40, 100, 'CONFIRMED' FROM per, p1 RETURNING ownership_period_id
)
SELECT per.id || '|' || p2.id || '|' || p3.id FROM per, p2, p3;
SQL
)
PERIOD="${IDS%%|*}"; REST="${IDS#*|}"; OWNER2="${REST%%|*}"; OWNER3="${REST#*|}"
echo "Fixture: period=$PERIOD (40 % vorhanden)"

# --- Zwei konkurrierende Sessions: jede will +60 % und COMPLETE ---------------
run_writer() {
  local owner="$1" tag="$2"
  docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" <<SQL 2>&1
BEGIN;
SET CONSTRAINTS ALL DEFERRED;
INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id, share_numerator, share_denominator, confirmation_status)
VALUES ('$PERIOD', '$owner', 60, 100, 'CONFIRMED');
UPDATE tenure.ownership_period SET distribution_status = 'COMPLETE' WHERE id = '$PERIOD';
SELECT pg_sleep(3);
COMMIT;
SQL
  echo "WRITER-$tag-EXIT:$?"
}

TMPDIR_TEST=$(mktemp -d)
run_writer "$OWNER2" A > "$TMPDIR_TEST/a.log" 2>&1 &
PID_A=$!
sleep 1
run_writer "$OWNER3" B > "$TMPDIR_TEST/b.log" 2>&1 &
PID_B=$!
wait $PID_A $PID_B 2>/dev/null
echo "--- Session A:"; tail -2 "$TMPDIR_TEST/a.log"
echo "--- Session B:"; tail -2 "$TMPDIR_TEST/b.log"

# --- Auswertung ----------------------------------------------------------------
VERDICT=$($PSQL <<SQL
SELECT CASE WHEN count(*) = 2
             AND sum(share_numerator::numeric / share_denominator) = 1
             AND (SELECT distribution_status FROM tenure.ownership_period WHERE id = '$PERIOD') = 'COMPLETE'
        THEN 'PASS' ELSE 'FAIL' END
FROM tenure.ownership_interest WHERE ownership_period_id = '$PERIOD';
SQL
)
FAILS=$(cat "$TMPDIR_TEST/a.log" "$TMPDIR_TEST/b.log" | grep -c "ERROR" || true)
rm -rf "$TMPDIR_TEST"
echo "Endstand-Prüfung: $VERDICT, abgebrochene Sessions: $FAILS"

if [ "$VERDICT" = "PASS" ] && [ "$FAILS" -ge 1 ]; then
  echo "NEBENLAEUFIGKEITSTEST BESTANDEN: genau ein Schreiber gewann, Endstand exakt 100 %"
  exit 0
else
  echo "NEBENLAEUFIGKEITSTEST FEHLGESCHLAGEN (verdict=$VERDICT fails=$FAILS)"
  exit 1
fi
