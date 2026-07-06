-- Akzeptanztest Phase 3 — Belege, Zeiten, Material; synthetische Daten.
-- Läuft in einer Transaktion und rollt sich am Ende zurück.

BEGIN;

DO $$
DECLARE
    v_user    uuid;
    v_addr    uuid;
    v_weg     uuid;
    v_mgmt    uuid;
    v_prop    uuid;
    v_order   uuid;
    v_job     uuid;
    v_time    uuid;
    v_quote   uuid;
    v_quote2  uuid;
    v_inv     uuid;
    v_credit  uuid;
    v_count   integer;
BEGIN
    ---------------------------------------------------------------------------
    -- Fixtures: Auftrag bis KAUFMAENNISCH_GEPRUEFT mit Schuldner
    ---------------------------------------------------------------------------
    INSERT INTO security.app_user (display_name) VALUES ('Phase3-Tester') RETURNING id INTO v_user;
    PERFORM set_config('app.current_user_id', v_user::text, true);

    INSERT INTO identity.address (street, house_number, postal_code, city)
    VALUES ('Belegweg', '3', '33333', 'Rechnungsstadt') RETURNING id INTO v_addr;
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'WEG Belegweg 3') RETURNING id INTO v_weg;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_weg, 'WEG', 'WEG Belegweg 3');
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'Verwaltung Beleg GmbH') RETURNING id INTO v_mgmt;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_mgmt, 'PROPERTY_MANAGEMENT', 'Verwaltung Beleg GmbH');
    INSERT INTO property.property (name, address_id, property_type)
    VALUES ('Belegweg 3', v_addr, 'WEG') RETURNING id INTO v_prop;

    INSERT INTO workflow.work_order (title, property_id) VALUES ('Heizungsreparatur', v_prop)
    RETURNING id INTO v_order;
    INSERT INTO workflow.work_order_party (work_order_id, party_id, role, source)
    VALUES (v_order, v_weg, 'PRINCIPAL', 'MANDATE'),
           (v_order, v_weg, 'INVOICE_DEBTOR', 'BILLING_INSTRUCTION'),
           (v_order, v_mgmt, 'INVOICE_RECIPIENT', 'BILLING_INSTRUCTION');
    UPDATE workflow.work_order
    SET responsibility_scope = 'COMMON_PROPERTY', order_evidence_reference = 'Beschluss ETV 2026-05',
        responsibility_confirmed_at = now(), responsibility_confirmed_by = v_user
    WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'FREIGEGEBEN' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'IN_PLANUNG' WHERE id = v_order;
    INSERT INTO workflow.service_job (work_order_id, scheduled_start, scheduled_end)
    VALUES (v_order, now(), now() + interval '2 hours') RETURNING id INTO v_job;
    UPDATE workflow.service_job SET status = 'GEPLANT' WHERE id = v_job;
    UPDATE workflow.service_job SET status = 'BESTAETIGT' WHERE id = v_job;
    UPDATE workflow.service_job SET status = 'UNTERWEGS' WHERE id = v_job;
    UPDATE workflow.service_job SET status = 'VOR_ORT' WHERE id = v_job;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Fixtures Phase 3 angelegt (Auftrag freigegeben, Einsatz vor Ort)';

    ---------------------------------------------------------------------------
    -- Test 1 (B-27): Zeiterfassung; Zeitart-Regeln
    ---------------------------------------------------------------------------
    INSERT INTO workflow.time_entry (service_job_id, user_id, time_type, started_at, ended_at)
    VALUES (v_job, v_user, 'ARBEITSZEIT', now() - interval '2 hours', now() - interval '30 minutes')
    RETURNING id INTO v_time;
    INSERT INTO workflow.material_entry (service_job_id, description, quantity, unit, recorded_by)
    VALUES (v_job, 'Umwälzpumpe', 1.000, 'Stk', v_user);
    INSERT INTO workflow.time_entry (user_id, time_type, started_at, ended_at)
    VALUES (v_user, 'INTERNE_ZEIT', now() - interval '1 hour', now());
    RAISE NOTICE 'OK  Test 1a: Zeit, Material und interne Zeit erfasst';
    BEGIN
        INSERT INTO workflow.time_entry (user_id, time_type, started_at, ended_at)
        VALUES (v_user, 'ARBEITSZEIT', now() - interval '1 hour', now());
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 1b: Arbeitszeit ohne Einsatz akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 1b: Arbeitszeit erfordert Einsatzbezug (B-27)';
    END;

    ---------------------------------------------------------------------------
    -- Test 2 (B-28): Korrekturfenster
    ---------------------------------------------------------------------------
    UPDATE workflow.time_entry SET ended_at = now() - interval '20 minutes' WHERE id = v_time;
    RAISE NOTICE 'OK  Test 2a: Korrektur vor Einsatzabschluss frei möglich';
    UPDATE workflow.service_job SET status = 'ABGESCHLOSSEN' WHERE id = v_job;
    BEGIN
        UPDATE workflow.time_entry SET ended_at = now() - interval '10 minutes' WHERE id = v_time;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 2b: Korrektur nach Abschluss ohne Begründung';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 2b: Korrektur nach Abschluss erfordert Begründung (B-28)';
    END;
    PERFORM set_config('app.correction_reason', 'Zeit falsch übertragen', true);
    UPDATE workflow.time_entry SET ended_at = now() - interval '10 minutes' WHERE id = v_time;
    PERFORM set_config('app.correction_reason', '', true);
    SELECT count(*) INTO v_count FROM audit.audit_entry
    WHERE target_type = 'workflow.time_entry' AND target_id = v_time AND action = 'ROW_UPDATE';
    IF v_count < 2 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 2c: Zeitkorrekturen nicht auditiert (%)', v_count;
    END IF;
    RAISE NOTICE 'OK  Test 2c: Korrektur mit Begründung möglich und auditiert';

    UPDATE workflow.work_order SET status = 'IN_AUSFUEHRUNG' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'TECHNISCH_ABGESCHLOSSEN' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'KAUFMAENNISCH_GEPRUEFT' WHERE id = v_order;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        PERFORM set_config('app.correction_reason', 'Versuch', true);
        UPDATE workflow.time_entry SET ended_at = now() WHERE id = v_time;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 2d: Korrektur nach kaufm. Freigabe möglich';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 2d: Nach kaufmännischer Freigabe keine Korrektur mehr (B-28)';
    END;
    PERFORM set_config('app.correction_reason', '', true);

    ---------------------------------------------------------------------------
    -- Test 3 (B-19): Positionsrundung und Steuersatzgruppen am Angebot
    ---------------------------------------------------------------------------
    INSERT INTO invoicing.quote (title, property_id, work_order_id)
    VALUES ('Angebot Heizungsreparatur', v_prop, v_order) RETURNING id INTO v_quote;
    BEGIN
        INSERT INTO invoicing.quote_line (quote_id, position_number, line_type, description,
                                          quantity, unit_price, tax_code, tax_rate_percent, net_amount)
        VALUES (v_quote, 1, 'MATERIAL', 'Falsch gerundet', 1.000, 10.00, 'DE_19', 19.00, 10.01);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3a: falsche Positionsrundung akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 3a: Positionsrundung wird erzwungen (B-19)';
    END;
    -- Zwei 19%-Positionen: Steuer je Gruppe auf Belegsumme (0.39), nicht je Position (0.40)
    INSERT INTO invoicing.quote_line (quote_id, position_number, line_type, description,
                                      quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
    VALUES (v_quote, 1, 'MATERIAL', 'Kleinteil A', 1.000, 'Stk', 1.03, 'DE_19', 19.00, 1.03),
           (v_quote, 2, 'MATERIAL', 'Kleinteil B', 1.000, 'Stk', 1.04, 'DE_19', 19.00, 1.04),
           (v_quote, 3, 'ARBEITSZEIT', 'Monteurstunde', 1.500, 'h', 60.00, 'DE_7', 7.00, 90.00);
    INSERT INTO invoicing.quote_line (quote_id, position_number, line_type, description)
    VALUES (v_quote, 4, 'TEXT', 'Hinweis: Ausführung innerhalb von 2 Wochen');

    UPDATE invoicing.quote SET status = 'INTERN_GEPRUEFT' WHERE id = v_quote;
    UPDATE invoicing.quote SET status = 'FREIGEGEBEN' WHERE id = v_quote;

    BEGIN
        UPDATE invoicing.quote
        SET net_total = 92.07, tax_total = 6.70, gross_total = 98.77,
            billing_snapshot = jsonb_build_object('debtor', 'WEG Belegweg 3'),
            content_hash = md5('falsch'),
            status = 'VERSENDET'
        WHERE id = v_quote;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3b: falsche Steuersumme akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 3b: Steuer je Positionsrundung (0.40) korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 4 (B-14/B-15/B-30): Versand mit korrekten Summen; danach eingefroren
    ---------------------------------------------------------------------------
    UPDATE invoicing.quote
    SET net_total = 92.07, tax_total = 6.69, gross_total = 98.76,
        billing_snapshot = jsonb_build_object('debtor', 'WEG Belegweg 3'),
        content_hash = md5('angebot-1'),
        status = 'VERSENDET'
    WHERE id = v_quote;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    SELECT count(*) INTO v_count FROM invoicing.quote
    WHERE id = v_quote AND quote_number ~ '^AN-[0-9]{4}-[0-9]{6}$' AND sent_at IS NOT NULL;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4a: AN-Nummer nicht vergeben';
    END IF;
    RAISE NOTICE 'OK  Test 4a: Versand mit Belegsummen-Steuer 6.69 und AN-Nummer (B-14/B-19)';

    BEGIN
        UPDATE invoicing.quote SET title = 'Manipuliert' WHERE id = v_quote;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4b: versendetes Angebot änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4b: versendetes Angebot ist eingefroren (B-30)';
    END;
    BEGIN
        UPDATE invoicing.quote_line SET net_amount = 999.99, unit_price = 999.99
        WHERE quote_id = v_quote AND position_number = 1;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4c: Position nach Versand änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4c: Positionen nach Versand unveränderlich (B-30)';
    END;
    UPDATE invoicing.quote SET status = 'ANGENOMMEN' WHERE id = v_quote;
    RAISE NOTICE 'OK  Test 4d: Statusfolge VERSENDET->ANGENOMMEN bleibt möglich (B-15)';

    ---------------------------------------------------------------------------
    -- Test 5: ERSETZT erfordert Nachfolgeverweis
    ---------------------------------------------------------------------------
    INSERT INTO invoicing.quote (title, property_id) VALUES ('Ersatzangebot', v_prop)
    RETURNING id INTO v_quote2;
    BEGIN
        PERFORM set_config('app.status_reason', 'Kunde wünscht Änderung', true);
        UPDATE invoicing.quote SET status = 'ERSETZT' WHERE id = v_quote;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5: ERSETZT ohne Nachfolger akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 5: ERSETZT erfordert Nachfolgeverweis';
    END;
    PERFORM set_config('app.status_reason', '', true);

    ---------------------------------------------------------------------------
    -- Test 6 (A-27/A-28/B-08): Veröffentlichungstore der Rechnung
    ---------------------------------------------------------------------------
    INSERT INTO invoicing.invoice (invoice_type, property_id, work_order_id, due_date)
    VALUES ('RECHNUNG', v_prop, v_order, (now() AT TIME ZONE 'UTC')::date + 14)
    RETURNING id INTO v_inv;
    INSERT INTO invoicing.invoice_line (invoice_id, position_number, line_type, description,
                                        quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
    VALUES (v_inv, 1, 'MATERIAL', 'Umwälzpumpe', 1.000, 'Stk', 250.00, 'DE_19', 19.00, 250.00),
           (v_inv, 2, 'ARBEITSZEIT', 'Monteurstunden', 1.500, 'h', 60.00, 'DE_19', 19.00, 90.00);
    UPDATE invoicing.invoice
    SET net_total = 340.00, tax_total = 64.60, gross_total = 404.60,
        billing_snapshot = jsonb_build_object('debtor', 'WEG Belegweg 3', 'recipient', 'Verwaltung Beleg GmbH'),
        content_hash = md5('rechnung-1')
    WHERE id = v_inv;

    -- 6a: ohne Schuldner
    BEGIN
        UPDATE invoicing.invoice SET status = 'VEROEFFENTLICHT' WHERE id = v_inv;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 6a: Veröffentlichung ohne Schuldner';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 6a: Veröffentlichung ohne Rechnungsschuldner abgelehnt (A-27)';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    -- 6b: Schuldner, der nicht am Auftrag bestätigt ist
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_inv, v_mgmt, 'INVOICE_DEBTOR');
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role, is_primary) VALUES (v_inv, v_mgmt, 'INVOICE_RECIPIENT', true);
    BEGIN
        UPDATE invoicing.invoice SET status = 'VEROEFFENTLICHT' WHERE id = v_inv;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 6b: fremder Schuldner akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 6b: Schuldner muss am Auftrag bestätigt sein (A-27)';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    -- 6c: korrekt (WEG als Schuldner, Verwaltung als primärer Empfänger)
    DELETE FROM invoicing.invoice_party WHERE invoice_id = v_inv AND party_id = v_mgmt AND role = 'INVOICE_DEBTOR';
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_inv, v_weg, 'INVOICE_DEBTOR');
    UPDATE invoicing.invoice SET status = 'VEROEFFENTLICHT' WHERE id = v_inv;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    SELECT count(*) INTO v_count FROM invoicing.invoice
    WHERE id = v_inv AND invoice_number ~ '^RE-[0-9]{4}-[0-9]{6}$' AND published_at IS NOT NULL;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 6c: RE-Nummer nicht vergeben';
    END IF;
    UPDATE workflow.work_order SET status = 'ABGERECHNET' WHERE id = v_order;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 6c: Rechnung veröffentlicht (RE-Nummer), Auftrag abgerechnet';

    ---------------------------------------------------------------------------
    -- Test 7 (B-21): veröffentlichte Rechnung vollständig unveränderlich
    ---------------------------------------------------------------------------
    BEGIN
        UPDATE invoicing.invoice SET net_total = 1.00 WHERE id = v_inv;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7a: veröffentlichte Rechnung änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7a: veröffentlichte Rechnung unveränderlich (B-21)';
    END;
    BEGIN
        UPDATE invoicing.invoice_line SET net_amount = 1.00, unit_price = 1.00
        WHERE invoice_id = v_inv AND position_number = 1;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7b: Position nach Veröffentlichung änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7b: Positionen nach Veröffentlichung unveränderlich';
    END;
    BEGIN
        DELETE FROM invoicing.invoice WHERE id = v_inv;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7c: Rechnung löschbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7c: Rechnungen sind nicht löschbar';
    END;

    ---------------------------------------------------------------------------
    -- Test 8 (B-17/B-21): Gutschrift als Folgebeleg mit GS-Nummer
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO invoicing.invoice (invoice_type, property_id) VALUES ('GUTSCHRIFT', v_prop);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 8a: Gutschrift ohne Ursprung akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 8a: Gutschrift erfordert Ursprungsbeleg (B-21)';
    END;
    INSERT INTO invoicing.invoice (invoice_type, property_id, reference_invoice_id)
    VALUES ('GUTSCHRIFT', v_prop, v_inv) RETURNING id INTO v_credit;
    INSERT INTO invoicing.invoice_line (invoice_id, position_number, line_type, description,
                                        quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
    VALUES (v_credit, 1, 'MATERIAL', 'Gutschrift Umwälzpumpe', 1.000, 'Stk', 250.00, 'DE_19', 19.00, 250.00);
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_credit, v_weg, 'INVOICE_DEBTOR');
    INSERT INTO invoicing.invoice_party (invoice_id, party_id, role, is_primary) VALUES (v_credit, v_mgmt, 'INVOICE_RECIPIENT', true);
    UPDATE invoicing.invoice
    SET net_total = 250.00, tax_total = 47.50, gross_total = 297.50,
        billing_snapshot = jsonb_build_object('ref', 'Gutschrift zu RE'),
        content_hash = md5('gutschrift-1'),
        status = 'VEROEFFENTLICHT'
    WHERE id = v_credit;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    SELECT count(*) INTO v_count FROM invoicing.invoice
    WHERE id = v_credit AND invoice_number ~ '^GS-[0-9]{4}-[0-9]{6}$';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 8b: GS-Nummer nicht vergeben';
    END IF;
    RAISE NOTICE 'OK  Test 8b: Gutschrift als Folgebeleg veröffentlicht (GS-Kreis)';

    ---------------------------------------------------------------------------
    -- Test 9 (A-29): mehrere Schuldner nur mit dokumentierter Grundlage
    ---------------------------------------------------------------------------
    DECLARE
        v_inv2 uuid;
    BEGIN
        INSERT INTO workflow.work_order_party (work_order_id, party_id, role, source)
        VALUES (v_order, v_mgmt, 'INVOICE_DEBTOR', 'MANUAL');
        INSERT INTO invoicing.invoice (invoice_type, property_id, work_order_id)
        VALUES ('TEILRECHNUNG', v_prop, v_order) RETURNING id INTO v_inv2;
        INSERT INTO invoicing.invoice_line (invoice_id, position_number, line_type, description,
                                            quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
        VALUES (v_inv2, 1, 'PAUSCHALE', 'Anfahrt', 1.000, 'psch', 50.00, 'DE_19', 19.00, 50.00);
        INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_inv2, v_weg, 'INVOICE_DEBTOR');
        INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_inv2, v_mgmt, 'INVOICE_DEBTOR');
        INSERT INTO invoicing.invoice_party (invoice_id, party_id, role, is_primary) VALUES (v_inv2, v_mgmt, 'INVOICE_RECIPIENT', true);
        UPDATE invoicing.invoice
        SET net_total = 50.00, tax_total = 9.50, gross_total = 59.50,
            billing_snapshot = '{}'::jsonb, content_hash = md5('teilrechnung'),
            status = 'VEROEFFENTLICHT'
        WHERE id = v_inv2;
        BEGIN
            SET CONSTRAINTS ALL IMMEDIATE;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 9: zwei Schuldner ohne Grundlage akzeptiert';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 9: mehrere Schuldner ohne dokumentierte Grundlage abgelehnt (A-29)';
        END;
        SET CONSTRAINTS ALL DEFERRED;
    END;

    ---------------------------------------------------------------------------
    -- Test 10: Steuercodes vorhanden und nicht löschbar (B-18-Struktur)
    ---------------------------------------------------------------------------
    SELECT count(*) INTO v_count FROM invoicing.tax_code;
    IF v_count < 4 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 10a: Steuercode-Kandidaten fehlen';
    END IF;
    BEGIN
        DELETE FROM invoicing.tax_code WHERE code = 'DE_7';
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 10b: Steuercode löschbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 10: Steuercode-Kandidaten vorhanden (STB-Vorbehalt) und nicht löschbar';
    END;

    ---------------------------------------------------------------------------
    -- Test 11 (P3-01): Belegnummern sind nicht manuell setzbar
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO invoicing.quote (title, property_id, quote_number)
        VALUES ('Nummern-Spoof', v_prop, 'AN-2026-424242');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 11a: Angebotsnummer im Entwurf setzbar';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 11a: Angebotsnummer nur beim Versand (B-13/B-14)';
    END;
    DECLARE
        v_spoof uuid;
    BEGIN
        INSERT INTO invoicing.invoice (invoice_type, property_id) VALUES ('RECHNUNG', v_prop)
        RETURNING id INTO v_spoof;
        BEGIN
            UPDATE invoicing.invoice SET invoice_number = 'RE-2026-999999' WHERE id = v_spoof;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 11b: Rechnungsnummer im Entwurf setzbar';
        EXCEPTION WHEN check_violation THEN
            RAISE NOTICE 'OK  Test 11b: Rechnungsnummer nur bei Veröffentlichung (B-13/B-14)';
        END;
    END;

    ---------------------------------------------------------------------------
    -- Test 12 (P3-03/P3-04): B-28 auch für Einsatzwechsel und neue Einträge
    ---------------------------------------------------------------------------
    BEGIN
        UPDATE workflow.time_entry SET service_job_id = NULL WHERE id = v_time;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 12a: Einsatzbezug verschiebbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 12a: Einsatzbezug von Zeiteinträgen unveränderlich (P3-03)';
    END;
    BEGIN
        INSERT INTO workflow.material_entry (service_job_id, description, quantity, unit, recorded_by)
        VALUES (v_job, 'Nachgeschobenes Material', 1.000, 'Stk', v_user);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 12b: Neuerfassung nach kaufm. Freigabe möglich';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 12b: Keine Neuerfassung nach kaufmännischer Freigabe (P3-04/B-28)';
    END;

    ---------------------------------------------------------------------------
    -- Test 13 (P3-05): falsch kopierter Steuersatz wird bei Veröffentlichung erkannt
    ---------------------------------------------------------------------------
    DECLARE
        v_bad uuid;
    BEGIN
        INSERT INTO invoicing.invoice (invoice_type, property_id, work_order_id)
        VALUES ('TEILRECHNUNG', v_prop, v_order) RETURNING id INTO v_bad;
        INSERT INTO invoicing.invoice_line (invoice_id, position_number, line_type, description,
                                            quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
        VALUES (v_bad, 1, 'PAUSCHALE', 'Falscher Satz', 1.000, 'psch', 100.00, 'DE_19', 7.00, 100.00);
        INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_bad, v_weg, 'INVOICE_DEBTOR');
        INSERT INTO invoicing.invoice_party (invoice_id, party_id, role, is_primary) VALUES (v_bad, v_mgmt, 'INVOICE_RECIPIENT', true);
        UPDATE invoicing.invoice
        SET net_total = 100.00, tax_total = 7.00, gross_total = 107.00,
            billing_snapshot = '{}'::jsonb, content_hash = md5('x'), status = 'VEROEFFENTLICHT'
        WHERE id = v_bad;
        BEGIN
            SET CONSTRAINTS ALL IMMEDIATE;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 13: falscher Steuersatz veröffentlicht';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 13: Steuersatz muss dem gültigen Steuercode entsprechen (P3-05)';
        END;
        SET CONSTRAINTS ALL DEFERRED;
    END;

    ---------------------------------------------------------------------------
    -- Test 14 (P3-06): Gutschrift-Schuldner müssen Ursprungs-Schuldner sein
    ---------------------------------------------------------------------------
    DECLARE
        v_gs2 uuid;
    BEGIN
        INSERT INTO invoicing.invoice (invoice_type, property_id, reference_invoice_id)
        VALUES ('GUTSCHRIFT', v_prop, v_inv) RETURNING id INTO v_gs2;
        INSERT INTO invoicing.invoice_line (invoice_id, position_number, line_type, description,
                                            quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
        VALUES (v_gs2, 1, 'PAUSCHALE', 'Fremde Gutschrift', 1.000, 'psch', 10.00, 'DE_19', 19.00, 10.00);
        INSERT INTO invoicing.invoice_party (invoice_id, party_id, role) VALUES (v_gs2, v_mgmt, 'INVOICE_DEBTOR');
        INSERT INTO invoicing.invoice_party (invoice_id, party_id, role, is_primary) VALUES (v_gs2, v_mgmt, 'INVOICE_RECIPIENT', true);
        UPDATE invoicing.invoice
        SET net_total = 10.00, tax_total = 1.90, gross_total = 11.90,
            billing_snapshot = '{}'::jsonb, content_hash = md5('y'), status = 'VEROEFFENTLICHT'
        WHERE id = v_gs2;
        BEGIN
            SET CONSTRAINTS ALL IMMEDIATE;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 14: fremder Gutschrift-Schuldner akzeptiert';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 14: Gutschrift-Schuldner an Ursprungsbeleg gebunden (A-27/B-21)';
        END;
        SET CONSTRAINTS ALL DEFERRED;
    END;

    ---------------------------------------------------------------------------
    -- Test 15 (P3-07): TRUNCATE auf Belegtabellen wird abgelehnt
    ---------------------------------------------------------------------------
    BEGIN
        TRUNCATE invoicing.invoice_line;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 15: invoice_line truncierbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 15: Belegtabellen gegen TRUNCATE geschützt (F-03/P3-07)';
    END;

    ---------------------------------------------------------------------------
    -- Test 16 (P3-08/P3-09): Nachfolgeverweis und Version nach Versand fixiert
    ---------------------------------------------------------------------------
    BEGIN
        UPDATE invoicing.quote SET replaced_by_quote_id = v_quote2 WHERE id = v_quote;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 16a: Nachfolgeverweis ohne ERSETZT setzbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 16a: Nachfolgeverweis nur beim ERSETZT-Übergang (P3-08)';
    END;
    BEGIN
        UPDATE invoicing.quote SET version = 99 WHERE id = v_quote;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 16b: version nach Versand änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 16b: version nach Versand eingefroren (P3-09)';
    END;

    ---------------------------------------------------------------------------
    -- Test 17 (P3-12): Beleg und Auftrag müssen zur selben Liegenschaft gehören
    ---------------------------------------------------------------------------
    DECLARE
        v_prop2 uuid;
    BEGIN
        INSERT INTO property.property (name, address_id, property_type)
        VALUES ('Fremdes Objekt', v_addr, 'RENTAL_PROPERTY') RETURNING id INTO v_prop2;
        BEGIN
            INSERT INTO invoicing.invoice (invoice_type, property_id, work_order_id)
            VALUES ('RECHNUNG', v_prop2, v_order);
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 17: Beleg auf fremder Liegenschaft';
        EXCEPTION WHEN foreign_key_violation THEN
            RAISE NOTICE 'OK  Test 17: Beleg-Liegenschaft muss zum Auftrag passen (P3-12)';
        END;
    END;

    ---------------------------------------------------------------------------
    -- Test 18: negative Zuschlagszeile (Nachlass) mit korrekter Rundung zulässig
    ---------------------------------------------------------------------------
    INSERT INTO invoicing.quote_line (quote_id, position_number, line_type, description,
                                      quantity, unit, unit_price, tax_code, tax_rate_percent, net_amount)
    VALUES (v_quote2, 1, 'ZUSCHLAG', 'Nachlass Pilotkunde', 1.000, 'psch', -10.00, 'DE_19', 19.00, -10.00);
    RAISE NOTICE 'OK  Test 18: negative Zuschlagszeile (Nachlass) korrekt erfassbar (B-24)';

    RAISE NOTICE '';
    RAISE NOTICE 'ALLE PHASE-3-AKZEPTANZTESTS BESTANDEN';
END;
$$;

ROLLBACK;
