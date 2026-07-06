-- Akzeptanztest Phase 2 — Workflow-Modul; synthetische Daten, keine realen Personen.
-- Ausführung: psql -v ON_ERROR_STOP=1 -f akzeptanztest_phase2.sql
-- Läuft in einer Transaktion und rollt sich am Ende zurück (Nummernkreis-Zählerstände
-- können durch Rollback Lücken erhalten — gemäß B-13 zulässig).

BEGIN;

DO $$
DECLARE
    v_user     uuid;
    v_addr     uuid;
    v_weg      uuid;
    v_mgmt     uuid;
    v_tenant   uuid;
    v_dup      uuid;
    v_prop     uuid;
    v_prop2    uuid;
    v_bldg     uuid;
    v_unit     uuid;
    v_proj     uuid;
    v_case     uuid;
    v_order    uuid;
    v_order2   uuid;
    v_job      uuid;
    v_count    integer;
    v_num_a    text;
    v_num_b    text;
BEGIN
    ---------------------------------------------------------------------------
    -- Fixtures
    ---------------------------------------------------------------------------
    INSERT INTO security.app_user (display_name) VALUES ('Phase2-Tester') RETURNING id INTO v_user;
    PERFORM set_config('app.current_user_id', v_user::text, true);

    INSERT INTO identity.address (street, house_number, postal_code, city)
    VALUES ('Testallee', '2', '54321', 'Prüfstadt') RETURNING id INTO v_addr;

    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'WEG Testallee 2') RETURNING id INTO v_weg;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_weg, 'WEG', 'WEG Testallee 2');
    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'Verwaltung Phase2 GmbH') RETURNING id INTO v_mgmt;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_mgmt, 'PROPERTY_MANAGEMENT', 'Verwaltung Phase2 GmbH');
    INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'Mia Melder') RETURNING id INTO v_tenant;
    INSERT INTO identity.person (party_id, first_name, last_name) VALUES (v_tenant, 'Mia', 'Melder');

    INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'Dublette Phase2') RETURNING id INTO v_dup;
    INSERT INTO identity.person (party_id, first_name, last_name) VALUES (v_dup, 'Dub', 'Lette');
    UPDATE identity.party SET status = 'MERGED', merged_into_party_id = v_tenant WHERE id = v_dup;
    INSERT INTO identity.party_merge (merged_party_id, canonical_party_id, actor_user_id, reason)
    VALUES (v_dup, v_tenant, v_user, 'Phase2-Testfixture');

    INSERT INTO property.property (name, address_id, property_type)
    VALUES ('Testallee 2', v_addr, 'WEG') RETURNING id INTO v_prop;
    INSERT INTO property.property (name, address_id, property_type)
    VALUES ('Testallee 4', v_addr, 'WEG') RETURNING id INTO v_prop2;
    INSERT INTO property.building (property_id, building_number) VALUES (v_prop, 'A') RETURNING id INTO v_bldg;
    INSERT INTO property.unit (building_id, property_id, unit_type, unit_number)
    VALUES (v_bldg, v_prop, 'APARTMENT', 'WE 10') RETURNING id INTO v_unit;

    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Fixtures Phase 2 angelegt';

    ---------------------------------------------------------------------------
    -- Test 1 (B-11/B-12): Nummernformate und getrennte Kreise
    ---------------------------------------------------------------------------
    v_num_a := workflow.next_number('V');
    v_num_b := workflow.next_number('AU');
    IF v_num_a !~ '^V-[0-9]{4}-[0-9]{6}$' OR v_num_b !~ '^AU-[0-9]{4}-[0-9]{6}$' THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 1: Nummernformat falsch (% / %)', v_num_a, v_num_b;
    END IF;
    RAISE NOTICE 'OK  Test 1: Nummernkreise PREFIX-JJJJ-###### (% | %)', v_num_a, v_num_b;

    ---------------------------------------------------------------------------
    -- Test 2 (B-10): Projekt mit zwei Liegenschaften
    ---------------------------------------------------------------------------
    INSERT INTO workflow.project (name) VALUES ('Rahmenmaßnahme Dachsanierung') RETURNING id INTO v_proj;
    INSERT INTO workflow.project_property (project_id, property_id) VALUES (v_proj, v_prop), (v_proj, v_prop2);
    RAISE NOTICE 'OK  Test 2: Projekt umfasst zwei Liegenschaften (B-10)';

    ---------------------------------------------------------------------------
    -- Test 3 (B-02): Vorgang mit Statusfluss; Meldender wird dokumentiert
    ---------------------------------------------------------------------------
    INSERT INTO workflow.service_case (subject, property_id, building_id, unit_id, reported_by_party_id, priority)
    VALUES ('Wasserfleck an der Decke', v_prop, v_bldg, v_unit, v_tenant, 'DRINGEND')
    RETURNING id INTO v_case;
    UPDATE workflow.service_case SET status = 'IN_PRUEFUNG' WHERE id = v_case;
    UPDATE workflow.service_case SET status = 'RUECKFRAGE' WHERE id = v_case;
    UPDATE workflow.service_case SET status = 'IN_PRUEFUNG' WHERE id = v_case;
    RAISE NOTICE 'OK  Test 3: Vorgangsstatusfluss NEU->IN_PRUEFUNG->RUECKFRAGE->IN_PRUEFUNG';

    ---------------------------------------------------------------------------
    -- Test 4 (B-02): unerlaubter Statussprung wird abgelehnt
    ---------------------------------------------------------------------------
    BEGIN
        UPDATE workflow.service_case SET status = 'BEAUFTRAGT' WHERE id = v_case;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4: Sprung IN_PRUEFUNG->BEAUFTRAGT akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4: unerlaubter Statussprung korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 5: Rücksprung ohne Begründung abgelehnt, mit Begründung protokolliert
    ---------------------------------------------------------------------------
    UPDATE workflow.service_case SET status = 'FREIGABE_AUSSTEHEND' WHERE id = v_case;
    BEGIN
        UPDATE workflow.service_case SET status = 'IN_PRUEFUNG' WHERE id = v_case;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5a: Rücksprung ohne Begründung akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 5a: Rücksprung ohne Begründung korrekt abgelehnt';
    END;
    PERFORM set_config('app.status_reason', 'Unterlagen unvollständig', true);
    UPDATE workflow.service_case SET status = 'IN_PRUEFUNG' WHERE id = v_case;
    PERFORM set_config('app.status_reason', '', true);
    SELECT count(*) INTO v_count FROM workflow.status_change
    WHERE entity = 'service_case' AND entity_id = v_case AND reason = 'Unterlagen unvollständig';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5b: Begründung nicht protokolliert';
    END IF;
    RAISE NOTICE 'OK  Test 5b: Rücksprung mit Begründung ausgeführt und protokolliert';

    ---------------------------------------------------------------------------
    -- Test 6 (A-21): bestätigte Verantwortung; UNKNOWN nicht bestätigbar
    ---------------------------------------------------------------------------
    BEGIN
        UPDATE workflow.service_case
        SET responsibility_confirmed_at = now(), responsibility_confirmed_by = v_user
        WHERE id = v_case; -- scope ist noch UNKNOWN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 6a: UNKNOWN wurde als bestätigt akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 6a: UNKNOWN kann nicht als bestätigt gelten';
    END;
    UPDATE workflow.service_case
    SET responsibility_scope = 'COMMON_PROPERTY',
        responsibility_confirmed_at = now(), responsibility_confirmed_by = v_user
    WHERE id = v_case;
    RAISE NOTICE 'OK  Test 6b: Verantwortung COMMON_PROPERTY bestätigt';

    ---------------------------------------------------------------------------
    -- Test 7 (B-01/A-25/A-26): Freigabe-Tor des Auftrags
    ---------------------------------------------------------------------------
    INSERT INTO workflow.work_order (title, property_id, building_id, unit_id, service_case_id,
                                     responsibility_scope, priority)
    VALUES ('Deckenschaden beheben', v_prop, v_bldg, v_unit, v_case, 'COMMON_PROPERTY', 'DRINGEND')
    RETURNING id INTO v_order;

    -- 7a: Freigabe ohne Nachweis und ohne Auftraggeber muss scheitern
    BEGIN
        UPDATE workflow.work_order SET status = 'FREIGEGEBEN' WHERE id = v_order;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7a: Freigabe ohne Tor-Voraussetzungen akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7a: Freigabe ohne Nachweis/Auftraggeber korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    -- 7b: Meldender als REPORTER macht ihn nicht zum Auftraggeber (A-01)
    INSERT INTO workflow.work_order_party (work_order_id, party_id, role, source)
    VALUES (v_order, v_tenant, 'REPORTER', 'MANUAL');
    BEGIN
        UPDATE workflow.work_order SET order_evidence_reference = 'Beschluss ETV 2026-03' WHERE id = v_order;
        UPDATE workflow.work_order SET status = 'FREIGEGEBEN' WHERE id = v_order;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7b: REPORTER genügte als Auftraggeber';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7b: Meldender ersetzt keinen Auftraggeber (A-01)';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    -- 7c: Mit WEG als PRINCIPAL, Verwaltung als REPRESENTATIVE, Nachweis und
    -- bestätigtem Verantwortungsbereich (WF-05/A-21) gelingt die Freigabe
    INSERT INTO workflow.work_order_party (work_order_id, party_id, role, source)
    VALUES (v_order, v_weg, 'PRINCIPAL', 'MANDATE'),
           (v_order, v_mgmt, 'REPRESENTATIVE', 'MANDATE');
    UPDATE workflow.work_order
    SET order_evidence_reference = 'Beschluss ETV 2026-03',
        responsibility_confirmed_at = now(), responsibility_confirmed_by = v_user
    WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'FREIGEGEBEN' WHERE id = v_order;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    UPDATE workflow.service_case SET status = 'FREIGABE_AUSSTEHEND' WHERE id = v_case;
    UPDATE workflow.service_case SET status = 'BEAUFTRAGT' WHERE id = v_case;
    RAISE NOTICE 'OK  Test 7c: Freigabe mit WEG-Auftraggeber, Vertreter und Textform-Nachweis';

    ---------------------------------------------------------------------------
    -- Test 8 (F-06): zusammengeführte Party nicht als Auftragsbeteiligte
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO workflow.work_order_party (work_order_id, party_id, role)
        VALUES (v_order, v_dup, 'ON_SITE_CONTACT');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 8: MERGED-Party als Beteiligte akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 8: zusammengeführte Party korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 9: Auftraggeber kann nach Freigabe nicht entfernt werden
    ---------------------------------------------------------------------------
    BEGIN
        DELETE FROM workflow.work_order_party WHERE work_order_id = v_order AND role = 'PRINCIPAL';
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 9: PRINCIPAL nach Freigabe entfernbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 9: Auftraggeber nach Freigabe nicht löschbar (Historienschutz)';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 10 (B-04/B-01): Einsatzfluss; kein Einsatz auf stornierten Auftrag
    ---------------------------------------------------------------------------
    UPDATE workflow.work_order SET status = 'IN_PLANUNG' WHERE id = v_order;
    INSERT INTO workflow.service_job (work_order_id, scheduled_start, scheduled_end)
    VALUES (v_order, now() + interval '1 day', now() + interval '1 day 2 hours')
    RETURNING id INTO v_job;
    INSERT INTO workflow.job_assignment (service_job_id, assignee_user_id, role)
    VALUES (v_job, v_user, 'LEAD');
    UPDATE workflow.service_job SET status = 'GEPLANT' WHERE id = v_job;
    UPDATE workflow.service_job SET status = 'BESTAETIGT' WHERE id = v_job;
    UPDATE workflow.service_job SET status = 'UNTERWEGS' WHERE id = v_job;
    UPDATE workflow.service_job SET status = 'VOR_ORT' WHERE id = v_job;
    UPDATE workflow.service_job SET status = 'ABGESCHLOSSEN' WHERE id = v_job;
    RAISE NOTICE 'OK  Test 10a: Einsatzfluss bis ABGESCHLOSSEN';

    BEGIN
        UPDATE workflow.service_job SET status = 'VOR_ORT' WHERE id = v_job;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 10b: ABGESCHLOSSEN->VOR_ORT akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 10b: unerlaubter Einsatz-Rücksprung abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 11 (B-03/B-08/A-27): Abrechnung erst mit Rechnungsschuldner
    ---------------------------------------------------------------------------
    UPDATE workflow.work_order SET status = 'IN_AUSFUEHRUNG' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'TECHNISCH_ABGESCHLOSSEN' WHERE id = v_order;
    UPDATE workflow.work_order SET status = 'KAUFMAENNISCH_GEPRUEFT' WHERE id = v_order;
    BEGIN
        UPDATE workflow.work_order SET status = 'ABGERECHNET' WHERE id = v_order;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 11a: Abrechnung ohne Schuldner akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 11a: Abrechnung ohne Rechnungsschuldner korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    INSERT INTO workflow.work_order_party (work_order_id, party_id, role, source)
    VALUES (v_order, v_weg, 'INVOICE_DEBTOR', 'BILLING_INSTRUCTION'),
           (v_order, v_mgmt, 'INVOICE_RECIPIENT', 'BILLING_INSTRUCTION');
    UPDATE workflow.work_order SET status = 'ABGERECHNET' WHERE id = v_order;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 11b: Abrechnung mit Schuldner (WEG) und Empfänger (Verwaltung)';

    ---------------------------------------------------------------------------
    -- Test 12 (B-03/B-06): ABGERECHNET ist final; Folgeauftrag mit Gewährleistung
    ---------------------------------------------------------------------------
    BEGIN
        PERFORM set_config('app.status_reason', 'Versuch', true);
        UPDATE workflow.work_order SET status = 'STORNIERT' WHERE id = v_order;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 12a: Storno nach Abrechnung akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 12a: Storno nach Abrechnung korrekt abgelehnt';
    END;
    PERFORM set_config('app.status_reason', '', true);

    BEGIN
        INSERT INTO workflow.service_job (work_order_id) VALUES (v_order);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 12b: Einsatz auf abgerechneten Auftrag akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 12b: kein neuer Einsatz auf abgerechneten Auftrag';
    END;

    INSERT INTO workflow.work_order (title, property_id, responsibility_scope,
                                     follow_up_of_work_order_id, is_warranty_case,
                                     order_evidence_reference)
    VALUES ('Nacharbeit Deckenschaden', v_prop, 'COMMON_PROPERTY', v_order, true,
            'Gewährleistung zu AU des Ursprungsauftrags')
    RETURNING id INTO v_order2;
    RAISE NOTICE 'OK  Test 12c: Folgeauftrag mit Gewährleistungskennzeichen (B-06)';

    ---------------------------------------------------------------------------
    -- Test 13 (F-02): kein DELETE auf Aufträgen; Änderungen werden auditiert
    ---------------------------------------------------------------------------
    BEGIN
        DELETE FROM workflow.work_order WHERE id = v_order2;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 13a: Auftrag löschbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 13a: Aufträge sind nicht löschbar';
    END;
    SELECT count(*) INTO v_count FROM audit.audit_entry
    WHERE target_type = 'workflow.work_order' AND target_id = v_order AND action = 'ROW_UPDATE';
    IF v_count < 1 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 13b: Auftragsänderungen nicht auditiert';
    END IF;
    RAISE NOTICE 'OK  Test 13b: Auftragsänderungen automatisch auditiert (% Einträge)', v_count;

    ---------------------------------------------------------------------------
    -- Test 14: Statusprotokoll ist append-only und vollständig
    ---------------------------------------------------------------------------
    SELECT count(*) INTO v_count FROM workflow.status_change
    WHERE entity = 'work_order' AND entity_id = v_order;
    -- Erwartet: Anlage (ENTWURF) + 6 erfolgreiche Übergänge = 7; zurückgerollte
    -- Fehlversuche erscheinen korrekt NICHT im Protokoll.
    IF v_count <> 7 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 14a: Statusprotokoll unvollständig oder überzählig (%)', v_count;
    END IF;
    BEGIN
        UPDATE workflow.status_change SET reason = 'MANIPULIERT'
        WHERE entity = 'work_order' AND entity_id = v_order;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 14b: Statusprotokoll änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 14: Statusprotokoll vollständig (% Einträge) und append-only', v_count;
    END;

    ---------------------------------------------------------------------------
    -- Test 15: Standortkonsistenz — Einheit ohne Gebäude unzulässig
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO workflow.service_case (subject, property_id, unit_id)
        VALUES ('Kaputtes Fenster', v_prop, v_unit);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 15: Einheit ohne Gebäude akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 15: Vorgangs-Standortkonsistenz erzwungen';
    END;

    ---------------------------------------------------------------------------
    -- Test 16 (A-23/A-25): Notfallauftrag — Freigabe mit Doku, Abrechnung erst nach Klärung
    ---------------------------------------------------------------------------
    DECLARE
        v_emergency uuid;
        v_draft     uuid;
        v_party_row uuid;
        v_job2      uuid;
        v_num       text;
    BEGIN
        -- 16a (WF-05): Notfall darf mit UNGEKLÄRTER Verantwortung (UNKNOWN) und ohne
        -- Auftraggeber freigegeben werden — Pflichtdokumentation bleibt
        INSERT INTO workflow.work_order (title, property_id, is_emergency, order_evidence_reference)
        VALUES ('Rohrbruch Notdienst', v_prop, true,
                'Telefonische Notfallmeldung Hausmeister, 05.07.2026 03:20')
        RETURNING id INTO v_emergency;
        UPDATE workflow.work_order SET status = 'FREIGEGEBEN' WHERE id = v_emergency;
        SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
        RAISE NOTICE 'OK  Test 16a: Notfallfreigabe mit UNKNOWN-Verantwortung und Pflichtdoku (WF-05/A-23)';

        UPDATE workflow.work_order SET status = 'IN_PLANUNG' WHERE id = v_emergency;
        UPDATE workflow.work_order SET status = 'IN_AUSFUEHRUNG' WHERE id = v_emergency;
        UPDATE workflow.work_order SET status = 'TECHNISCH_ABGESCHLOSSEN' WHERE id = v_emergency;
        UPDATE workflow.work_order SET status = 'KAUFMAENNISCH_GEPRUEFT' WHERE id = v_emergency;
        BEGIN
            UPDATE workflow.work_order SET status = 'ABGERECHNET' WHERE id = v_emergency;
            SET CONSTRAINTS ALL IMMEDIATE;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 16b: Notfall ohne nachträgliche Klärung abgerechnet';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 16b: Notfall-Abrechnung erst nach Verantwortungs-/Beteiligtenklärung (A-23)';
        END;
        SET CONSTRAINTS ALL DEFERRED;

        -- 16c: Nach vollständiger nachträglicher Klärung gelingt die Abrechnung
        UPDATE workflow.work_order
        SET responsibility_scope = 'COMMON_PROPERTY',
            responsibility_confirmed_at = now(), responsibility_confirmed_by = v_user
        WHERE id = v_emergency;
        INSERT INTO workflow.work_order_party (work_order_id, party_id, role, source)
        VALUES (v_emergency, v_weg, 'PRINCIPAL', 'MANUAL'),
               (v_emergency, v_weg, 'INVOICE_DEBTOR', 'MANUAL');
        UPDATE workflow.work_order SET status = 'ABGERECHNET' WHERE id = v_emergency;
        SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
        RAISE NOTICE 'OK  Test 16c: Notfall nach nachträglicher Bestätigung abgerechnet (A-25)';

        ---------------------------------------------------------------------------
        -- Test 17 (WF-02): INSERT mit fortgeschrittenem Anfangsstatus wird abgelehnt
        ---------------------------------------------------------------------------
        BEGIN
            INSERT INTO workflow.service_case (subject, property_id, status)
            VALUES ('Bypass-Versuch', v_prop, 'ABGESCHLOSSEN');
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 17a: Vorgang mit ABGESCHLOSSEN angelegt';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 17a: Vorgang muss im Status NEU beginnen (WF-02)';
        END;
        BEGIN
            INSERT INTO workflow.work_order (title, property_id, status)
            VALUES ('Bypass-Versuch', v_prop, 'STORNIERT');
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 17b: Auftrag als STORNIERT angelegt';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 17b: Auftrag muss im Status ENTWURF beginnen (WF-02)';
        END;

        ---------------------------------------------------------------------------
        -- Test 18 (WF-01): UPDATE-Umgehungen auf work_order_party
        ---------------------------------------------------------------------------
        INSERT INTO workflow.work_order (title, property_id)
        VALUES ('Entwurfsauftrag als Ziel', v_prop) RETURNING id INTO v_draft;
        SELECT id INTO v_party_row FROM workflow.work_order_party
        WHERE work_order_id = v_order AND role = 'PRINCIPAL';
        BEGIN
            UPDATE workflow.work_order_party SET work_order_id = v_draft WHERE id = v_party_row;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 18a: Auftragsbezug einer Rolle verschiebbar';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 18a: Auftragsbezug der Rolle ist unveränderlich (WF-01)';
        END;
        BEGIN
            UPDATE workflow.work_order_party SET party_id = v_tenant WHERE id = v_party_row;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 18b: Auftraggeber nach Abrechnung austauschbar';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 18b: Beteiligtenidentität nach Freigabe unveränderlich (WF-01)';
        END;

        ---------------------------------------------------------------------------
        -- Test 19 (WF-06): Vorbereitung vor Freigabe erlaubt, Ausführung nicht
        ---------------------------------------------------------------------------
        INSERT INTO workflow.service_job (work_order_id, scheduled_start, scheduled_end)
        VALUES (v_draft, now() + interval '2 days', now() + interval '2 days 2 hours')
        RETURNING id INTO v_job2;
        UPDATE workflow.service_job SET status = 'GEPLANT' WHERE id = v_job2;
        UPDATE workflow.service_job SET status = 'BESTAETIGT' WHERE id = v_job2;
        RAISE NOTICE 'OK  Test 19a: Einsatzvorbereitung auf ENTWURF-Auftrag zulässig (A-23)';
        BEGIN
            UPDATE workflow.service_job SET status = 'UNTERWEGS' WHERE id = v_job2;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 19b: Ausführung ohne Auftragsfreigabe möglich';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 19b: Ausführung erfordert freigegebenen Auftrag (WF-06/B-01)';
        END;

        ---------------------------------------------------------------------------
        -- Test 20 (WF-04): Wiedereröffnung nach Abrechnung des Auftrags gesperrt
        ---------------------------------------------------------------------------
        UPDATE workflow.service_case SET status = 'ABGESCHLOSSEN' WHERE id = v_case;
        BEGIN
            PERFORM set_config('app.status_reason', 'Versuch Wiedereröffnung', true);
            UPDATE workflow.service_case SET status = 'IN_PRUEFUNG' WHERE id = v_case;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 20: Wiedereröffnung trotz abgerechnetem Auftrag';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 20: Wiedereröffnung nach Abrechnung gesperrt (WF-04/B-06)';
        END;
        PERFORM set_config('app.status_reason', '', true);

        ---------------------------------------------------------------------------
        -- Test 21: TRUNCATE auf Statusprotokoll wird abgelehnt
        ---------------------------------------------------------------------------
        BEGIN
            TRUNCATE workflow.status_change;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 21: Statusprotokoll truncierbar';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 21: Statusprotokoll gegen TRUNCATE geschützt (F-03)';
        END;

        ---------------------------------------------------------------------------
        -- Test 22 (WF-03): Nummernüberlauf polstert nicht mehr, keine Kollision
        ---------------------------------------------------------------------------
        INSERT INTO workflow.number_range (prefix, year, last_value)
        VALUES ('E', extract(year FROM (now() AT TIME ZONE 'UTC'))::integer, 999999)
        ON CONFLICT (prefix, year) DO UPDATE SET last_value = 999999;
        v_num := workflow.next_number('E');
        IF v_num !~ '^E-[0-9]{4}-1000000$' THEN
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 22: Überlaufnummer falsch (%)', v_num;
        END IF;
        RAISE NOTICE 'OK  Test 22: Nummernüberlauf ohne Kollision (%)', v_num;

        ---------------------------------------------------------------------------
        -- Test 23 (WF-10): Gewährleistung nur auf abgerechneten Ursprung
        ---------------------------------------------------------------------------
        BEGIN
            INSERT INTO workflow.work_order (title, property_id, is_warranty_case,
                                             follow_up_of_work_order_id, order_evidence_reference)
            VALUES ('Falsche Gewährleistung', v_prop, true, v_draft, 'Test');
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 23: Gewährleistung auf ENTWURF-Ursprung';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 23: Gewährleistung erfordert abgerechneten Ursprung (B-06)';
        END;

        ---------------------------------------------------------------------------
        -- Test 24 (WF-11): höchstens ein primärer Beteiligter je Rolle
        ---------------------------------------------------------------------------
        INSERT INTO workflow.work_order_party (work_order_id, party_id, role, is_primary)
        VALUES (v_draft, v_weg, 'PRINCIPAL', true);
        BEGIN
            INSERT INTO workflow.work_order_party (work_order_id, party_id, role, is_primary)
            VALUES (v_draft, v_mgmt, 'PRINCIPAL', true);
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 24: zwei primäre Auftraggeber akzeptiert';
        EXCEPTION WHEN unique_violation THEN
            RAISE NOTICE 'OK  Test 24: zweiter primärer Beteiligter je Rolle abgelehnt (WF-11)';
        END;

        ---------------------------------------------------------------------------
        -- Test 25 (WF-07): Zuordnungen nach Abschluss unveränderlich
        ---------------------------------------------------------------------------
        BEGIN
            UPDATE workflow.job_assignment SET role = 'TECHNICIAN'
            WHERE service_job_id = v_job; -- v_job ist ABGESCHLOSSEN
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 25: Zuordnung nach Abschluss änderbar';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 25: Einsatzzuordnung nach Abschluss unveränderlich (WF-07)';
        END;

        ---------------------------------------------------------------------------
        -- Test 26 (NR2-01): Bestätigung kann nach Freigabe nicht entfernt werden
        ---------------------------------------------------------------------------
        BEGIN
            UPDATE workflow.work_order
            SET responsibility_confirmed_at = NULL, responsibility_confirmed_by = NULL
            WHERE id = v_order; -- v_order ist ABGERECHNET (freigegebener Zustand)
            SET CONSTRAINTS ALL IMMEDIATE;
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 26: Bestätigung nach Freigabe entfernbar';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
            RAISE NOTICE 'OK  Test 26: Verantwortungsbestätigung nach Freigabe unveränderlich (NR2-01)';
        END;
        SET CONSTRAINTS ALL DEFERRED;
    END;

    RAISE NOTICE '';
    RAISE NOTICE 'ALLE PHASE-2-AKZEPTANZTESTS BESTANDEN';
END;
$$;

ROLLBACK;
