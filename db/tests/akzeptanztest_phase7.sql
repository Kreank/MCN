-- Akzeptanztest Phase 7 — Artikelstamm mit IDS-Connect-Fundament.
-- Läuft in einer Transaktion und rollt sich am Ende zurück.

BEGIN;

DO $$
DECLARE
    v_user   uuid;
    v_gc     uuid;
    v_rf     uuid;
    v_art    uuid;
    v_art2   uuid;
    v_ref    uuid;
    v_list   uuid;
    v_count  integer;
BEGIN
    ---------------------------------------------------------------------------
    -- Fixtures: zwei Großhändler als Parties
    ---------------------------------------------------------------------------
    INSERT INTO security.app_user (display_name) VALUES ('Phase7-Tester') RETURNING id INTO v_user;
    PERFORM set_config('app.current_user_id', v_user::text, true);
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'GC Gruppe Test') RETURNING id INTO v_gc;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_gc, 'COMPANY', 'GC Gruppe Test');
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'Richter+Frenzel Test') RETURNING id INTO v_rf;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_rf, 'COMPANY', 'Richter+Frenzel Test');
    RAISE NOTICE 'OK  Fixtures Phase 7 (zwei Großhändler)';

    ---------------------------------------------------------------------------
    -- Test 1: IDS-Import legt Artikel im lokalen Stamm an
    ---------------------------------------------------------------------------
    INSERT INTO pricing.article (article_number, description, gtin, manufacturer_name,
                                 manufacturer_number, unit, product_group)
    VALUES ('ART-000001', 'Umwälzpumpe Alpha2 25-60', '4056817231234', 'Grundfos',
            '99231234', 'Stk', 'Pumpen')
    RETURNING id INTO v_art;
    INSERT INTO pricing.article_supplier_reference
        (article_id, supplier_party_id, source_system, source_namespace,
         supplier_article_number, last_purchase_price, currency, discount_group,
         last_imported_at, valid_from)
    VALUES (v_art, v_gc, 'IDS_CONNECT', 'gc-shop', '4711', 185.19, 'EUR', 'P1',
            now(), (now() AT TIME ZONE 'UTC')::date);
    RAISE NOTICE 'OK  Test 1: IDS-Artikel mit GTIN, Hersteller und Händler-EK im Stamm';

    ---------------------------------------------------------------------------
    -- Test 2: gleiche Händlernummer in ZWEI Namespaces zulässig; im selben nicht
    ---------------------------------------------------------------------------
    INSERT INTO pricing.article (article_number, description, unit)
    VALUES ('ART-000002', 'Dichtungssatz', 'Set') RETURNING id INTO v_art2;
    INSERT INTO pricing.article_supplier_reference
        (article_id, supplier_party_id, source_system, source_namespace,
         supplier_article_number, valid_from)
    VALUES (v_art2, v_rf, 'IDS_CONNECT', 'rf-shop', '4711', (now() AT TIME ZONE 'UTC')::date);
    RAISE NOTICE 'OK  Test 2a: Nummer 4711 bei zwei Händlern kollisionsfrei (Namespaces)';
    BEGIN
        INSERT INTO pricing.article_supplier_reference
            (article_id, supplier_party_id, source_system, source_namespace,
             supplier_article_number, valid_from)
        VALUES (v_art2, v_gc, 'IDS_CONNECT', 'gc-shop', '4711', (now() AT TIME ZONE 'UTC')::date);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 2b: zeitgleiche Doppelzuordnung akzeptiert';
    EXCEPTION WHEN exclusion_violation THEN
        RAISE NOTICE 'OK  Test 2b: dieselbe Händlernummer zeigt zeitgleich nur auf einen Artikel';
    END;

    ---------------------------------------------------------------------------
    -- Test 3: GTIN eindeutig; Format geprüft
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO pricing.article (article_number, description, gtin, unit)
        VALUES ('ART-000003', 'Duplikat-GTIN', '4056817231234', 'Stk');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3a: doppelte GTIN akzeptiert';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'OK  Test 3a: GTIN ist eindeutig';
    END;
    BEGIN
        INSERT INTO pricing.article (article_number, description, gtin, unit)
        VALUES ('ART-000004', 'Falsche GTIN', 'ABC123', 'Stk');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3b: ungültige GTIN akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 3b: GTIN-Format wird geprüft';
    END;

    ---------------------------------------------------------------------------
    -- Test 4: EK-Aktualisierung beim Re-Import ist auditiert
    ---------------------------------------------------------------------------
    SELECT id INTO v_ref FROM pricing.article_supplier_reference
    WHERE article_id = v_art AND source_namespace = 'gc-shop';
    UPDATE pricing.article_supplier_reference
    SET last_purchase_price = 192.40, last_imported_at = now()
    WHERE id = v_ref;
    SELECT count(*) INTO v_count FROM audit.audit_entry
    WHERE target_type = 'pricing.article_supplier_reference'
      AND target_id = v_ref AND action = 'ROW_UPDATE';
    IF v_count < 1 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4: EK-Änderung nicht auditiert';
    END IF;
    RAISE NOTICE 'OK  Test 4: EK-Aktualisierung beim Re-Import mit Vorher/Nachher auditiert';

    ---------------------------------------------------------------------------
    -- Test 5: Artikelbezug der Referenz unveränderlich; Artikel nicht löschbar
    ---------------------------------------------------------------------------
    BEGIN
        UPDATE pricing.article_supplier_reference SET article_id = v_art2 WHERE id = v_ref;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5a: Referenz umhängbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 5a: Artikelbezug der Händlerreferenz unveränderlich';
    END;
    BEGIN
        DELETE FROM pricing.article WHERE id = v_art2;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5b: Artikel löschbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 5b: Artikel werden deaktiviert, nicht gelöscht';
    END;

    ---------------------------------------------------------------------------
    -- Test 6: Preislistenposition mit Stammartikel-Bezug (B-25)
    ---------------------------------------------------------------------------
    INSERT INTO pricing.price_list (name, valid_from) VALUES ('Testliste', (now() AT TIME ZONE 'UTC')::date)
    RETURNING id INTO v_list;
    INSERT INTO pricing.price_list_item (price_list_id, item_number, description, line_type,
                                         unit, unit_price, article_id)
    VALUES (v_list, 'ART-000001', 'Umwälzpumpe Alpha2 25-60', 'MATERIAL', 'Stk', 250.00, v_art);
    RAISE NOTICE 'OK  Test 6: VK-Position verweist auf den Stammartikel (EK->VK über B-25)';

    ---------------------------------------------------------------------------
    -- Test 7 (Review-Fixes): Referenz-Härtung und Typkonsistenz
    ---------------------------------------------------------------------------
    BEGIN
        DELETE FROM pricing.article_supplier_reference WHERE id = v_ref;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7a: Händlerreferenz löschbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7a: EK-/Import-Historie nicht löschbar (F-02)';
    END;
    BEGIN
        UPDATE pricing.article_supplier_reference
        SET supplier_article_number = '9999' WHERE id = v_ref;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7b: Referenzidentität änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7b: Referenzidentität unveränderlich (REV-A-05)';
    END;
    BEGIN
        INSERT INTO pricing.price_list_item (price_list_id, item_number, description,
                                             line_type, unit, unit_price, article_id)
        VALUES (v_list, 'FALSCH-01', 'Falsche Art', 'ARBEITSZEIT', 'h', 60.00, v_art);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7c: Positionsart passt nicht zum Artikel';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7c: Positionsart muss zur Artikelart passen (B-24/B-25)';
    END;

    ---------------------------------------------------------------------------
    -- Test 8: GTIN-Randfälle (8/14 gültig, 10 ungültig)
    ---------------------------------------------------------------------------
    INSERT INTO pricing.article (article_number, description, gtin, unit)
    VALUES ('ART-GTIN8', 'GTIN-8-Artikel', '12345678', 'Stk');
    INSERT INTO pricing.article (article_number, description, gtin, unit)
    VALUES ('ART-GTIN14', 'GTIN-14-Artikel', '12345678901234', 'Stk');
    BEGIN
        INSERT INTO pricing.article (article_number, description, gtin, unit)
        VALUES ('ART-GTIN10', 'GTIN-10-Artikel', '1234567890', 'Stk');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 8: 10-stellige GTIN akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 8: GTIN-Längen 8/14 gültig, 10 abgelehnt';
    END;

    RAISE NOTICE '';
    RAISE NOTICE 'ALLE PHASE-7-AKZEPTANZTESTS BESTANDEN';
END;
$$;

ROLLBACK;
