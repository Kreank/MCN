#!/usr/bin/env bash
# Nebenläufigkeitstest Berichtspositionen (Migration 0080):
# Der Trigger workflow.protect_site_report_lines liest den Berichtsstatus mit
# FOR SHARE. Diese eine Zeile SQL ist die ganze Invariante — ohne sie könnte eine
# Position noch NACH der Kundenunterschrift in den versiegelten Bericht rutschen
# (Session 2 läse den alten Status ENTWURF, während Session 1 gerade unterschreibt).
#
# Erwartung:
#   1. Session 1 unterzeichnet den Bericht und hält die Transaktion 4 s offen.
#   2. Session 2 schreibt parallel eine Position in DENSELBEN Bericht -> sie
#      WARTET (FOR SHARE gegen die von Session 1 gesperrte Berichtszeile) und
#      scheitert nach dem Commit von Session 1 an der Versiegelung.
#   3. Der Bericht trägt hinterher KEINE Position (kein „dazwischen").
#   4. Kontrolllauf: derselbe INSERT in einen ENTWURFS-Bericht gelingt (beweist,
#      dass das Fixture schreibbar ist und der Test nicht aus falschem Grund grün ist).
#
# Hinterlässt synthetische Daten (Präfix KONKB-) — nur gegen Wegwerf-Datenbanken.
#
# Aufruf: bash db/tests/nebenlaeufigkeitstest_berichtspositionen.sh [container] [db]

set -u
CONTAINER="${1:-mitra-crm-test}"
DB="${2:-mitra_crm_test}"
PSQL="docker exec -i $CONTAINER psql -qAt -U postgres -d $DB"
RUN="KONKB-$(date +%s)"

docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" >/dev/null <<SQL
DO \$\$
DECLARE
    v_user uuid; v_addr uuid; v_prop uuid; v_order uuid;
    v_file uuid; v_signiert uuid; v_entwurf uuid;
BEGIN
    INSERT INTO security.app_user (display_name) VALUES ('$RUN-User') RETURNING id INTO v_user;
    INSERT INTO identity.address (street, house_number, postal_code, city)
    VALUES ('Raceweg', '7', '99123', 'Wettlaufstadt') RETURNING id INTO v_addr;
    INSERT INTO property.property (name, address_id, property_type)
    VALUES ('$RUN-Objekt', v_addr, 'WEG') RETURNING id INTO v_prop;
    INSERT INTO workflow.work_order (title, property_id)
    VALUES ('$RUN-Auftrag', v_prop) RETURNING id INTO v_order;

    -- Unterschrift (content.file) — der CHECK site_report_signed_complete verlangt sie.
    INSERT INTO content.file (storage_key, original_filename, mime_type, size_bytes,
                              sha256, uploaded_by)
    VALUES ('signature/$RUN', 'unterschrift.png', 'image/png', 68,
            repeat('a', 64), v_user) RETURNING id INTO v_file;

    INSERT INTO workflow.site_report (work_order_id, report_date, author_id, activity_text)
    VALUES (v_order, current_date, v_user, '$RUN-Bericht (Race)') RETURNING id INTO v_signiert;
    INSERT INTO workflow.site_report (work_order_id, report_date, author_id, activity_text)
    VALUES (v_order, current_date, v_user, '$RUN-Bericht (Kontrolle)') RETURNING id INTO v_entwurf;
END;
\$\$;
SQL

IDS=$($PSQL <<SQL
SELECT string_agg(r.id::text, '|' ORDER BY r.activity_text)
FROM workflow.site_report r
WHERE r.activity_text LIKE '$RUN-%';
SQL
)
# Sortiert nach activity_text: "(Kontrolle)" < "(Race)"
KONTROLLE="${IDS%%|*}"; SIGNIERT="${IDS#*|}"
FILE=$($PSQL -c "SELECT id FROM content.file WHERE storage_key = 'signature/$RUN';")
echo "Fixture: Bericht $SIGNIERT (wird unterzeichnet), Kontrollbericht $KONTROLLE"

TMP=$(mktemp -d)

# Session 1: Bericht unterzeichnen, Transaktion 4 Sekunden offen halten
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" > "$TMP/s1.log" 2>&1 <<SQL &
BEGIN;
UPDATE workflow.site_report
SET status = 'UNTERZEICHNET', signed_by_name = 'Klara Kundin', signed_at = now(),
    signature_file_id = '$FILE'
WHERE id = '$SIGNIERT';
SELECT pg_sleep(4);
COMMIT;
SQL
PID1=$!
sleep 1

# Session 2: parallel eine Position in denselben Bericht schreiben.
# Ohne FOR SHARE läse der Trigger den ALTEN Status (ENTWURF) und ließe die
# Position durch -> ein unterschriebener Nachweis mit nachträglicher Position.
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" > "$TMP/s2.log" 2>&1 <<SQL &
INSERT INTO workflow.site_report_line
    (site_report_id, position_number, line_type, description, quantity, unit)
VALUES ('$SIGNIERT', 1, 'MATERIAL', '$RUN-Nachtrag', 1.000, 'Stk');
SQL
PID2=$!
wait $PID1 $PID2 2>/dev/null
echo "--- Session 2 (paralleles Positionsschreiben):"; tail -3 "$TMP/s2.log"

S2_VERSIEGELT=$(grep -c "unterzeichnet" "$TMP/s2.log" || true)
ZEILEN=$($PSQL -c "SELECT count(*) FROM workflow.site_report_line WHERE site_report_id = '$SIGNIERT';")
STATUS=$($PSQL -c "SELECT status FROM workflow.site_report WHERE id = '$SIGNIERT';")

# Kontrolllauf: derselbe INSERT muss in einem ENTWURF gelingen.
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" > "$TMP/s3.log" 2>&1 <<SQL
INSERT INTO workflow.site_report_line
    (site_report_id, position_number, line_type, description, quantity, unit)
VALUES ('$KONTROLLE', 1, 'MATERIAL', '$RUN-Kontrollposition', 1.000, 'Stk');
SQL
KONTROLL_ZEILEN=$($PSQL -c "SELECT count(*) FROM workflow.site_report_line WHERE site_report_id = '$KONTROLLE';")
rm -rf "$TMP"

echo "Nach Race: Status=$STATUS, Positionen=$ZEILEN | Kontrolllauf: Positionen=$KONTROLL_ZEILEN | Abweisungen=$S2_VERSIEGELT"
if [ "$S2_VERSIEGELT" -ge 1 ] && [ "$STATUS" = "UNTERZEICHNET" ] && [ "$ZEILEN" = "0" ] \
   && [ "$KONTROLL_ZEILEN" = "1" ]; then
  echo "NEBENLAEUFIGKEITSTEST BERICHTSPOSITIONEN BESTANDEN: Position gegen laufende Unterschrift serialisiert und abgewiesen"
  exit 0
else
  echo "NEBENLAEUFIGKEITSTEST BERICHTSPOSITIONEN FEHLGESCHLAGEN"
  exit 1
fi
