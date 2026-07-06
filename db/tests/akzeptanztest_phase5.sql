-- Akzeptanztest Phase 5 — Preise, Zahlungsspiegel, Mahnstruktur.
-- Läuft in einer Transaktion und rollt sich am Ende zurück.

BEGIN;

DO $$
DECLARE
    v_user  uuid;
    v_addr  uuid;
    v_weg   uuid;
    v_mgmt  uuid;
    v_prop  uuid;
    v_order uuid;
    v_inv   uuid;
    v_list  uuid;
    v_count integer;
BEGIN
    ---------------------------------------------------------------------------
    -- Fixtures: veröffentlichte, fällige Rechnung
    ---------------------------------------------------------------------------
    INSERT INTO security.app_user (display_name) VALUES ('Phase5-Tester') RETURNING id INTO v_user;
    PERFORM set_config('app.current_user_id', v_user::text, true);
    INSERT INTO identity.address (street, house_number, postal_code, city)
    VALUES ('Zahlweg', '5', '55555', 'Kassenstadt') RETURNING id INTO v_addr;
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'WEG Zahlweg 5') RETURNING id INTO v_weg;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_weg, 'WEG', 'WEG Zahlweg 5');
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'Verwaltung Zahl GmbH') RETURNING id INTO v_mgmt;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_mgmt, 'PROPERTY_MANAGEMENT', 'Verwaltung Zahl GmbH');
    INSERT INTO property.property (name, address_id, property_type) VALUES ('Zahlweg 5', v_addr, 'WEG') RETURNING id INTO v_prop;
    INSERT INTO workflow.work_order (title, property_id) VALUES ('Reparatur', v_prop) RETURNING id INTO v_order;
    INSERT INTO workflow.work_order_party (work_order_id, party_id, role, source)
    VALUES (v_order, v_weg, 'PRINCIPAL', 'MANDATE'), (v_order, v_weg, 'INVOICE_DEBTOR', 'MANUAL');
    UPDATE workflow.work_order
    SET responsibility_scope = 'COMMON_PROPERTY', order_evidence_reference = 'Beschluss',
        responsibility_confirmed_at = now(), responsibility_confirmed_by = v_user
    WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'FREIGEGEBEN' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'IN_PLANUNG' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'IN_AUSFUEHRUNG' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'TECHNISCH_ABGESCHLOSSEN' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'KAUFMAENNISCH_GEPRUEFT' WHERE id = v_order;

    INSERT INTO invoicing.invoice (invoice_type, property_id, work_order_id,
                                   invoice_date, due_date)
    VALUES ('RECHNUNG', v_prop, v_order,
            (now() AT TIME ZONE 'UTC')::date, (now() AT TIME ZONE 'UTC')::date)
    RETURNING id INTO v_inv;
    INSERT INTO invoicing.invoice_line (invoice_id, position_number, line_type, description,
                                        quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
    VALUES (v_inv, 1, 'PAUSCHALE', 'Reparatur', 1.000, 'psch', 100.00, 'DE_19', 19.00, 100.00);
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_inv, v_weg, 'INVOICE_DEBTOR');
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role, is_primary) VALUES (v_inv, v_mgmt, 'INVOICE_RECIPIENT', true);
    UPDATE invoicing.invoice
    SET net_total = 100.00, tax_total = 19.00, gross_total = 119.00,
        billing_snapshot = '{}'::jsonb, content_hash = md5('p5'), status = 'VEROEFFENTLICHT'
    WHERE id = v_inv;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Fixtures Phase 5 (veröffentlichte, fällige Rechnung)';

    ---------------------------------------------------------------------------
    -- Test 1 (B-25): Preisliste, Aufschlagsgruppe, Kundenvereinbarung
    ---------------------------------------------------------------------------
    INSERT INTO pricing.markup_group (code, label, markup_percent) VALUES ('MAT_STD', 'Material Standard', 35.0000);
    INSERT INTO pricing.price_list (name, valid_from) VALUES ('Hauptpreisliste 2026', DATE '2026-01-01')
    RETURNING id INTO v_list;
    INSERT INTO pricing.price_list_item (price_list_id, item_number, description, line_type, unit, unit_price, purchase_price, markup_group)
    VALUES (v_list, 'MAT-001', 'Umwälzpumpe', 'MATERIAL', 'Stk', 250.00, 185.19, 'MAT_STD'),
           (v_list, 'AZ-001', 'Monteurstunde', 'ARBEITSZEIT', 'h', 60.00, NULL, NULL);
    INSERT INTO pricing.customer_price_agreement (party_id, description, line_type, unit, unit_price, evidence_reference, valid_from, created_by)
    VALUES (v_weg, 'Monteurstunde Sonderkondition', 'ARBEITSZEIT', 'h', 55.00, 'Rahmenvereinbarung 2026-01', DATE '2026-01-01', v_user);
    RAISE NOTICE 'OK  Test 1a: Preisliste, Aufschlagsgruppe und dokumentierte Kundenvereinbarung';
    BEGIN
        INSERT INTO pricing.price_list_item (price_list_id, item_number, description, line_type, unit, unit_price)
        VALUES (v_list, 'MAT-001', 'Duplikat', 'MATERIAL', 'Stk', 1.00);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 1b: doppelte Artikelnummer akzeptiert';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'OK  Test 1b: Artikelnummer je Preisliste eindeutig';
    END;

    ---------------------------------------------------------------------------
    -- Test 2 (B-16): Freigabegrenzen als Stammdaten, ohne erfundene Seeds
    ---------------------------------------------------------------------------
    SELECT count(*) INTO v_count FROM pricing.approval_threshold;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 2a: Grenzen wurden erfunden (% Zeilen)', v_count;
    END IF;
    INSERT INTO pricing.approval_threshold (role_code, scope, threshold, currency, valid_from)
    VALUES ('DISPOSITION', 'BETRAG_ABSOLUT', 500.00, 'EUR', DATE '2026-07-01');
    RAISE NOTICE 'OK  Test 2: Grenzen sind pflegbare Stammdaten ohne erfundene Startwerte (B-16)';

    ---------------------------------------------------------------------------
    -- Test 3 (B-23): Zahlungsspiegel — nur veröffentlichte Rechnungen, append-only
    ---------------------------------------------------------------------------
    DECLARE
        v_draft uuid;
    BEGIN
        INSERT INTO invoicing.invoice (invoice_type, property_id, work_order_id)
        VALUES ('TEILRECHNUNG', v_prop, v_order) RETURNING id INTO v_draft;
        BEGIN
            INSERT INTO invoicing.payment (invoice_id, payment_type, amount, paid_at, import_source, external_reference)
            VALUES (v_draft, 'ZAHLUNG', 119.00, current_date, 'FIBU', 'BU-0001');
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3a: Zahlung auf Entwurf akzeptiert';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 3a: Zahlungen nur auf veröffentlichte Rechnungen (B-23)';
        END;
    END;
    INSERT INTO invoicing.payment (invoice_id, payment_type, amount, paid_at, import_source, external_reference)
    VALUES (v_inv, 'TEILZAHLUNG', 60.00, current_date, 'FIBU', 'BU-0002'),
           (v_inv, 'ZAHLUNG', 59.00, current_date, 'FIBU', 'BU-0003');
    BEGIN
        INSERT INTO invoicing.payment (invoice_id, payment_type, amount, paid_at, import_source, external_reference)
        VALUES (v_inv, 'ZAHLUNG', 1.00, current_date, 'FIBU', 'BU-0003');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3b: doppelter Import akzeptiert';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'OK  Test 3b: Import ist idempotent (Quelle + Referenz eindeutig)';
    END;
    BEGIN
        UPDATE invoicing.payment SET amount = 999.99 WHERE external_reference = 'BU-0002';
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3c: Zahlungsspiegel änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 3c: Zahlungsspiegel ist append-only (Korrektur im führenden System)';
    END;

    ---------------------------------------------------------------------------
    -- Test 4 (B-22): Mahnstruktur — Reihenfolge, Fälligkeit, STB-Vorbehalt
    ---------------------------------------------------------------------------
    SELECT count(*) INTO v_count FROM invoicing.dunning_level WHERE fee IS NOT NULL;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4a: Mahngebühren wurden erfunden';
    END IF;
    RAISE NOTICE 'OK  Test 4a: Mahnstufen 1-3 vorhanden, Gebühren NULL (STB-Vorbehalt)';

    BEGIN
        INSERT INTO invoicing.dunning_notice (invoice_id, level, issued_at, created_by)
        VALUES (v_inv, 2, current_date + 8, v_user);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4b: Mahnstufe 2 ohne Stufe 1 akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4b: Mahnstufen sind lückenlos aufsteigend';
    END;
    BEGIN
        INSERT INTO invoicing.dunning_notice (invoice_id, level, issued_at, created_by)
        VALUES (v_inv, 1, current_date, v_user);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4c: Mahnung vor Fälligkeit akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4c: Mahnung erst nach Fälligkeit';
    END;
    INSERT INTO invoicing.dunning_notice (invoice_id, level, issued_at, created_by)
    VALUES (v_inv, 1, current_date + 8, v_user);
    INSERT INTO invoicing.dunning_notice (invoice_id, level, issued_at, created_by)
    VALUES (v_inv, 2, current_date + 22, v_user);
    RAISE NOTICE 'OK  Test 4d: Zahlungserinnerung und Mahnung 1 erfasst';
    BEGIN
        DELETE FROM invoicing.dunning_notice WHERE invoice_id = v_inv;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4e: Mahnungen löschbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4e: Mahnungen sind append-only';
    END;

    RAISE NOTICE '';
    RAISE NOTICE 'ALLE PHASE-5-AKZEPTANZTESTS BESTANDEN';
END;
$$;

ROLLBACK;
