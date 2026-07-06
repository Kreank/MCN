#!/usr/bin/env bash
# Nebenläufigkeitstest Phase 3 (P3-02, geschärft nach NR3-01):
# Fixture ist VERÖFFENTLICHUNGSFÄHIG (kaufm. geprüfter Auftrag, Schuldner, Empfänger) —
# auf ungefixtem Code würde Session 2 die inkonsistente Rechnung VERÖFFENTLICHEN und der
# Test schlüge fehl. Erwartung mit Fix:
#   1. Session 1 ändert eine Position (100 -> 250) und hält die Transaktion offen.
#   2. Session 2 veröffentlicht parallel mit den ALTEN Summen -> wartet (FOR SHARE)
#      und scheitert exakt am B-19-Summen-Tor ("Summen inkonsistent").
#   3. Kontrolllauf: Veröffentlichung mit KORREKTEN Summen gelingt (beweist, dass das
#      Fixture wirklich veröffentlichungsfähig ist).
# Hinterlässt synthetische Daten (Präfix KONK3-) — nur gegen Wegwerf-Datenbanken.
#
# Aufruf: bash db/tests/nebenlaeufigkeitstest_phase3.sh [container] [db]

set -u
CONTAINER="${1:-mitra-crm-test}"
DB="${2:-mitra_crm_test}"
PSQL="docker exec -i $CONTAINER psql -qAt -U postgres -d $DB"
RUN="KONK3-$(date +%s)"

docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" >/dev/null <<SQL
DO \$\$
DECLARE
    v_user uuid; v_addr uuid; v_weg uuid; v_mgmt uuid; v_prop uuid;
    v_order uuid; v_inv uuid;
BEGIN
    INSERT INTO security.app_user (display_name) VALUES ('$RUN-User') RETURNING id INTO v_user;
    INSERT INTO identity.address (street, house_number, postal_code, city)
    VALUES ('Raceweg', '3', '99123', 'Wettlaufstadt') RETURNING id INTO v_addr;
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', '$RUN-WEG') RETURNING id INTO v_weg;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_weg, 'WEG', '$RUN-WEG');
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', '$RUN-Verwaltung') RETURNING id INTO v_mgmt;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_mgmt, 'PROPERTY_MANAGEMENT', '$RUN-Verwaltung');
    INSERT INTO property.property (name, address_id, property_type)
    VALUES ('$RUN-Objekt', v_addr, 'WEG') RETURNING id INTO v_prop;

    INSERT INTO workflow.work_order (title, property_id) VALUES ('$RUN-Auftrag', v_prop)
    RETURNING id INTO v_order;
    INSERT INTO workflow.work_order_party (work_order_id, party_id, role, source)
    VALUES (v_order, v_weg, 'PRINCIPAL', 'MANDATE'),
           (v_order, v_weg, 'INVOICE_DEBTOR', 'BILLING_INSTRUCTION');
    UPDATE workflow.work_order
    SET responsibility_scope = 'COMMON_PROPERTY', order_evidence_reference = '$RUN-Nachweis',
        responsibility_confirmed_at = now(), responsibility_confirmed_by = v_user
    WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'FREIGEGEBEN' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'IN_PLANUNG' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'IN_AUSFUEHRUNG' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'TECHNISCH_ABGESCHLOSSEN' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'KAUFMAENNISCH_GEPRUEFT' WHERE id = v_order;

    INSERT INTO invoicing.invoice (invoice_type, property_id, work_order_id)
    VALUES ('RECHNUNG', v_prop, v_order) RETURNING id INTO v_inv;
    INSERT INTO invoicing.invoice_line (invoice_id, position_number, line_type, description,
                                        quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
    VALUES (v_inv, 1, 'PAUSCHALE', '$RUN-Position', 1.000, 'psch', 100.00, 'DE_19', 19.00, 100.00);
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_inv, v_weg, 'INVOICE_DEBTOR');
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role, is_primary) VALUES (v_inv, v_mgmt, 'INVOICE_RECIPIENT', true);
END;
\$\$;
SQL

IDS=$($PSQL <<SQL
SELECT i.id || '|' || l.id
FROM invoicing.invoice i
JOIN invoicing.invoice_line l ON l.invoice_id = i.id
JOIN property.property p ON p.id = i.property_id
WHERE p.name = '$RUN-Objekt';
SQL
)
INV="${IDS%%|*}"; LINE="${IDS#*|}"
echo "Fixture: veröffentlichungsfähige Rechnung $INV (Position 100,00)"

TMP=$(mktemp -d)

# Session 1: Position ändern, Transaktion 4 Sekunden offen halten
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" > "$TMP/s1.log" 2>&1 <<SQL &
BEGIN;
UPDATE invoicing.invoice_line SET unit_price = 250.00, net_amount = 250.00 WHERE id = '$LINE';
SELECT pg_sleep(4);
COMMIT;
SQL
PID1=$!
sleep 1

# Session 2: parallel veröffentlichen (Summen passen zur ALTEN Position)
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" > "$TMP/s2.log" 2>&1 <<SQL &
UPDATE invoicing.invoice
SET net_total = 100.00, tax_total = 19.00, gross_total = 119.00,
    billing_snapshot = '{}'::jsonb, content_hash = md5('konk3'),
    status = 'VEROEFFENTLICHT'
WHERE id = '$INV';
SQL
PID2=$!
wait $PID1 $PID2 2>/dev/null
echo "--- Session 2 (parallele Veröffentlichung):"; tail -3 "$TMP/s2.log"

# NR3-01: PASS nur bei exakt der erwarteten Abweisung am Summen-Tor
S2_SUMMEN=$(grep -c "Summen inkonsistent" "$TMP/s2.log" || true)
MID=$($PSQL -c "SELECT status || '|' || coalesce(invoice_number, 'NULL') FROM invoicing.invoice WHERE id = '$INV';")

# Kontrolllauf: mit korrekten Summen MUSS die Veröffentlichung gelingen
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" > "$TMP/s3.log" 2>&1 <<SQL
UPDATE invoicing.invoice
SET net_total = 250.00, tax_total = 47.50, gross_total = 297.50,
    billing_snapshot = '{}'::jsonb, content_hash = md5('konk3-final'),
    status = 'VEROEFFENTLICHT'
WHERE id = '$INV';
SQL
FINAL=$($PSQL -c "SELECT status || '|' || coalesce(invoice_number, 'NULL') FROM invoicing.invoice WHERE id = '$INV';")
rm -rf "$TMP"

echo "Nach Race: $MID | Nach Kontrolllauf: $FINAL | Summen-Tor-Abweisungen: $S2_SUMMEN"
if [ "$S2_SUMMEN" -ge 1 ] && [ "${MID%%|*}" = "ENTWURF" ] && [ "${MID#*|}" = "NULL" ] \
   && [ "${FINAL%%|*}" = "VEROEFFENTLICHT" ] && [ "${FINAL#*|}" != "NULL" ]; then
  echo "NEBENLAEUFIGKEITSTEST PHASE 3 BESTANDEN: Race am Summen-Tor abgewiesen, korrekte Veröffentlichung möglich"
  exit 0
else
  echo "NEBENLAEUFIGKEITSTEST PHASE 3 FEHLGESCHLAGEN"
  exit 1
fi
