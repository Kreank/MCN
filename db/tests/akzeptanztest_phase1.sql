-- Akzeptanztest Phase 1 — synthetische Daten, keine realen Personen (AGENT.md)
-- Ausführung: psql -v ON_ERROR_STOP=1 -f akzeptanztest_phase1.sql
-- Der gesamte Test läuft in einer Transaktion und wird am Ende zurückgerollt;
-- er hinterlässt keine Daten.

BEGIN;

DO $$
DECLARE
    -- Stammdaten
    v_user          uuid;
    v_addr          uuid;
    v_mgmt          uuid;  -- Verwaltung (Organisation)
    v_weg           uuid;  -- WEG (Organisation)
    v_owner_a       uuid;  -- Eigentümer A (Person)
    v_owner_b       uuid;  -- Eigentümer B (Person)
    v_owner_c       uuid;  -- Eigentümer C (Person)
    v_tenant        uuid;  -- Mieter (Person)
    v_dup           uuid;  -- Dublette von Eigentümer A
    v_prop          uuid;
    v_bldg          uuid;
    v_unit1         uuid;
    v_unit2         uuid;
    v_unit3         uuid;
    v_common        uuid;
    v_mandate       uuid;
    v_mandate2      uuid;
    v_period        uuid;
    v_occ           uuid;
    v_audit         uuid;
BEGIN
    ---------------------------------------------------------------------------
    -- Fixtures
    ---------------------------------------------------------------------------
    INSERT INTO security.app_user (display_name) VALUES ('Testbenutzer') RETURNING id INTO v_user;

    INSERT INTO identity.address (street, house_number, postal_code, city)
    VALUES ('Musterstraße', '1', '12345', 'Musterstadt') RETURNING id INTO v_addr;

    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'Hausverwaltung Test GmbH') RETURNING id INTO v_mgmt;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_mgmt, 'PROPERTY_MANAGEMENT', 'Hausverwaltung Test GmbH');

    INSERT INTO identity.party (party_type, display_name) VALUES ('ORGANIZATION', 'WEG Musterstraße 1') RETURNING id INTO v_weg;
    INSERT INTO identity.organization (party_id, organization_type, legal_name) VALUES (v_weg, 'WEG', 'WEG Musterstraße 1');

    INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'Anna Alpha') RETURNING id INTO v_owner_a;
    INSERT INTO identity.person (party_id, first_name, last_name) VALUES (v_owner_a, 'Anna', 'Alpha');

    INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'Bernd Beta') RETURNING id INTO v_owner_b;
    INSERT INTO identity.person (party_id, first_name, last_name) VALUES (v_owner_b, 'Bernd', 'Beta');

    INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'Clara Gamma') RETURNING id INTO v_owner_c;
    INSERT INTO identity.person (party_id, first_name, last_name) VALUES (v_owner_c, 'Clara', 'Gamma');

    INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'Milan Mieter') RETURNING id INTO v_tenant;
    INSERT INTO identity.person (party_id, first_name, last_name) VALUES (v_tenant, 'Milan', 'Mieter');

    INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'Anna Alpha (Dublette)') RETURNING id INTO v_dup;
    INSERT INTO identity.person (party_id, first_name, last_name) VALUES (v_dup, 'Anna', 'Alpha');

    INSERT INTO property.property (name, address_id, property_type)
    VALUES ('Musterstraße 1', v_addr, 'WEG') RETURNING id INTO v_prop;

    INSERT INTO property.building (property_id, building_number) VALUES (v_prop, 'H1') RETURNING id INTO v_bldg;

    INSERT INTO property.unit (building_id, property_id, unit_type, unit_number)
    VALUES (v_bldg, v_prop, 'APARTMENT', 'WE 01') RETURNING id INTO v_unit1;
    INSERT INTO property.unit (building_id, property_id, unit_type, unit_number)
    VALUES (v_bldg, v_prop, 'APARTMENT', 'WE 02') RETURNING id INTO v_unit2;
    INSERT INTO property.unit (building_id, property_id, unit_type, unit_number)
    VALUES (v_bldg, v_prop, 'APARTMENT', 'WE 03') RETURNING id INTO v_unit3;
    INSERT INTO property.unit (building_id, property_id, unit_type, unit_number)
    VALUES (v_bldg, v_prop, 'COMMON_AREA', 'Treppenhaus') RETURNING id INTO v_common;

    INSERT INTO property.property_party_role (property_id, party_id, role, valid_from)
    VALUES (v_prop, v_weg, 'COMMUNITY_OF_OWNERS', DATE '2020-01-01');

    RAISE NOTICE 'OK  Fixtures angelegt (Verwaltung, WEG, Liegenschaft, 3 Wohnungen, Gemeinschaftsfläche)';

    ---------------------------------------------------------------------------
    -- Test 1: WEG-Vollmandat anlegen (A-10)
    ---------------------------------------------------------------------------
    INSERT INTO management.management_mandate
        (management_party_id, principal_party_id, property_id, mandate_type,
         scope_type, valid_from, default_contact_party_id)
    VALUES (v_mgmt, v_weg, v_prop, 'WEG_MANAGEMENT', 'ENTIRE_PROPERTY',
            DATE '2020-01-01', v_mgmt)
    RETURNING id INTO v_mandate;
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 1: WEG-Vollmandat';

    ---------------------------------------------------------------------------
    -- Test 2: Eigentum 60/40 als COMPLETE (A-16)
    ---------------------------------------------------------------------------
    INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference, confirmed_at, confirmed_by_user_id)
    VALUES (v_unit1, 'COMPLETE', DATE '2020-01-01', 'OWNER_LIST', 'Eigentümerliste 2020', now(), v_user)
    RETURNING id INTO v_period;
    INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id, share_numerator, share_denominator, confirmation_status)
    VALUES (v_period, v_owner_a, 60, 100, 'CONFIRMED'),
           (v_period, v_owner_b, 40, 100, 'CONFIRMED');
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 2: 60/40 vollständig akzeptiert';

    ---------------------------------------------------------------------------
    -- Test 3 (OPUS-01 Positivfall): 1/3 + 1/3 + 1/3 exakt akzeptiert
    ---------------------------------------------------------------------------
    INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference, confirmed_at, confirmed_by_user_id)
    VALUES (v_unit2, 'COMPLETE', DATE '2020-01-01', 'OWNER_LIST', 'Eigentümerliste 2020', now(), v_user)
    RETURNING id INTO v_period;
    INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id, share_numerator, share_denominator, confirmation_status)
    VALUES (v_period, v_owner_a, 1, 3, 'CONFIRMED'),
           (v_period, v_owner_b, 1, 3, 'CONFIRMED'),
           (v_period, v_owner_c, 1, 3, 'CONFIRMED');
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 3: 1/3+1/3+1/3 exakt als 100%% akzeptiert (LCM-Prüfung)';

    ---------------------------------------------------------------------------
    -- Test 4 (OPUS-01 Negativfall): 1/3 + 1/3 + 333/1000 muss abgelehnt werden
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference, confirmed_at, confirmed_by_user_id)
        VALUES (v_unit3, 'COMPLETE', DATE '2020-01-01', 'OWNER_LIST', 'Eigentümerliste 2020', now(), v_user)
        RETURNING id INTO v_period;
        INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id, share_numerator, share_denominator, confirmation_status)
        VALUES (v_period, v_owner_a, 1, 3, 'CONFIRMED'),
               (v_period, v_owner_b, 1, 3, 'CONFIRMED'),
               (v_period, v_owner_c, 333, 1000, 'CONFIRMED');
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4: 1/3+1/3+333/1000 wurde fälschlich als vollständig akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4: 1/3+1/3+333/1000 korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 5: Summe über 100 Prozent muss abgelehnt werden
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference, confirmed_at, confirmed_by_user_id)
        VALUES (v_unit3, 'COMPLETE', DATE '2020-01-01', 'OWNER_LIST', 'Eigentümerliste 2020', now(), v_user)
        RETURNING id INTO v_period;
        INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id, share_numerator, share_denominator, confirmation_status)
        VALUES (v_period, v_owner_a, 60, 100, 'CONFIRMED'),
               (v_period, v_owner_b, 50, 100, 'CONFIRMED');
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5: 110 Prozent wurden fälschlich akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 5: 110%% korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 6: ungeklärte Anteile bleiben speicherbar (A-16, PARTIAL)
    ---------------------------------------------------------------------------
    INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, valid_until, source_type, source_reference)
    VALUES (v_unit3, 'PARTIAL', DATE '2020-01-01', DATE '2021-01-01', 'MANAGEMENT_NOTICE', 'Mitteilung 2020-05')
    RETURNING id INTO v_period;
    INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id)
    VALUES (v_period, v_owner_c);
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 6: bekannter Eigentümer ohne bekannten Anteil (PARTIAL) speicherbar';

    ---------------------------------------------------------------------------
    -- Test 7 (OPUS-04/A-08): Gemeinschaftsfläche darf keinen Eigentumsstand tragen
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference)
        VALUES (v_common, 'UNRESOLVED', DATE '2020-01-01', 'MANUAL', 'Testfall');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7: Eigentumsstand auf COMMON_AREA wurde fälschlich akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7: Eigentumsstand auf Gemeinschaftsfläche korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 8: Belegung — vermietet, rückwirkender Wechsel, Überlappungsverbot (A-18)
    ---------------------------------------------------------------------------
    INSERT INTO tenure.occupancy (unit_id, occupancy_type, valid_from, valid_until)
    VALUES (v_unit1, 'RENTED', DATE '2020-01-01', DATE '2024-06-01') RETURNING id INTO v_occ;
    INSERT INTO tenure.occupancy_party (occupancy_id, party_id, role, valid_from, valid_until)
    VALUES (v_occ, v_tenant, 'CONTRACTUAL_TENANT', DATE '2020-01-01', DATE '2024-06-01');
    INSERT INTO tenure.occupancy (unit_id, occupancy_type, valid_from)
    VALUES (v_unit1, 'VACANT', DATE '2024-06-01');
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 8a: Mieterwechsel zu bestätigtem Leerstand, taggenau anschließend';

    BEGIN
        INSERT INTO tenure.occupancy (unit_id, occupancy_type, valid_from)
        VALUES (v_unit1, 'RENTED', DATE '2024-01-01');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 8b: überlappende primäre Belegung wurde fälschlich akzeptiert';
    EXCEPTION WHEN exclusion_violation THEN
        RAISE NOTICE 'OK  Test 8b: überlappende primäre Belegung korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 9: Beteiligtenzeitraum außerhalb der Belegung muss abgelehnt werden
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO tenure.occupancy_party (occupancy_id, party_id, role, valid_from)
        VALUES (v_occ, v_owner_a, 'OCCUPANT', DATE '2019-01-01');
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 9: Beteiligtenzeitraum außerhalb der Belegung wurde akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 9: Beteiligtenzeitraum außerhalb der Belegung korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 10: doppelte Einheitsnummer je Liegenschaft (A-09)
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO property.unit (building_id, property_id, unit_type, unit_number)
        VALUES (v_bldg, v_prop, 'APARTMENT', 'WE 01');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 10: doppelte Einheitsnummer wurde fälschlich akzeptiert';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'OK  Test 10: doppelte Einheitsnummer je Liegenschaft korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 11: zwei gleichlautende externe Nummern aus verschiedenen Namespaces
    ---------------------------------------------------------------------------
    INSERT INTO property.property_external_reference (property_id, source_system, source_namespace, external_key, valid_from)
    VALUES (v_prop, 'HERO', 'verwaltung-alt', '4711', DATE '2020-01-01'),
           (v_prop, 'HERO', 'verwaltung-neu', '4711', DATE '2020-01-01');
    RAISE NOTICE 'OK  Test 11a: gleiche Nummer 4711 in zwei Namespaces zulässig';

    BEGIN
        INSERT INTO property.property_external_reference (property_id, source_system, source_namespace, external_key, valid_from)
        VALUES (v_prop, 'HERO', 'verwaltung-alt', '4711', DATE '2022-01-01');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 11b: zeitgleiche widersprüchliche externe Referenz akzeptiert';
    EXCEPTION WHEN exclusion_violation THEN
        RAISE NOTICE 'OK  Test 11b: zeitgleiche Doppelzuordnung im selben Namespace korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 12: Dubletten-Zusammenführung mit Nachweis; Kette wird verhindert (A-04)
    ---------------------------------------------------------------------------
    UPDATE identity.party
    SET status = 'MERGED', merged_into_party_id = v_owner_a
    WHERE id = v_dup;
    INSERT INTO identity.party_merge (merged_party_id, canonical_party_id, actor_user_id, reason)
    VALUES (v_dup, v_owner_a, v_user, 'Dublette nach Prüfung von E-Mail und Adresse');
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 12a: kontrollierte Zusammenführung mit Audit-Nachweis';

    BEGIN
        UPDATE identity.party
        SET status = 'MERGED', merged_into_party_id = v_dup
        WHERE id = v_owner_b;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 12b: Zusammenführungskette wurde fälschlich akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 12b: Zusammenführungskette korrekt verhindert';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 13: Append-only auf Audit (OPUS-02)
    ---------------------------------------------------------------------------
    INSERT INTO audit.audit_entry (actor_type, actor_user_id, action, target_type, target_id)
    VALUES ('USER', v_user, 'MERGE_PARTY', 'identity.party', v_dup)
    RETURNING id INTO v_audit;
    BEGIN
        UPDATE audit.audit_entry SET action = 'MANIPULIERT' WHERE id = v_audit;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 13: Audit-Eintrag konnte geändert werden';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 13: Audit ist append-only';
    END;

    ---------------------------------------------------------------------------
    -- Test 14: Teilmandat ohne Einheiten muss abgelehnt werden (A-11)
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO management.management_mandate
            (management_party_id, principal_party_id, property_id, mandate_type,
             scope_type, valid_from, default_contact_party_id)
        VALUES (v_mgmt, v_owner_a, v_prop, 'SPECIAL_PROPERTY_MANAGEMENT',
                'SELECTED_UNITS', DATE '2021-01-01', v_mgmt);
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 14: SELECTED_UNITS ohne Einheiten wurde akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 14: Teilmandat ohne Einheiten korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 15: Sondereigentumsmandat für ausgewählte Einheiten (A-11) + Verwaltungswechsel (A-12)
    ---------------------------------------------------------------------------
    INSERT INTO management.management_mandate
        (management_party_id, principal_party_id, property_id, mandate_type,
         scope_type, valid_from, default_contact_party_id)
    VALUES (v_mgmt, v_owner_a, v_prop, 'SPECIAL_PROPERTY_MANAGEMENT',
            'SELECTED_UNITS', DATE '2021-01-01', v_mgmt)
    RETURNING id INTO v_mandate2;
    INSERT INTO management.management_mandate_unit (mandate_id, property_id, unit_id)
    VALUES (v_mandate2, v_prop, v_unit1);
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 15a: Sondereigentumsmandat für ausgewählte Einheit';

    -- Verwaltungswechsel: altes WEG-Mandat endet am Stichtag, neues beginnt taggenau
    UPDATE management.management_mandate
    SET valid_until = DATE '2025-01-01', status = 'ENDED'
    WHERE id = v_mandate;
    INSERT INTO management.management_mandate
        (management_party_id, principal_party_id, property_id, mandate_type,
         scope_type, valid_from, default_contact_party_id)
    VALUES (v_mgmt, v_weg, v_prop, 'WEG_MANAGEMENT', 'ENTIRE_PROPERTY',
            DATE '2025-01-01', v_mgmt);
    SET CONSTRAINTS ALL IMMEDIATE;
    SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 15b: Verwaltungswechsel am Stichtag ohne Überlappung';

    ---------------------------------------------------------------------------
    -- Test 16: Abrechnungsvorgabe mit mehreren Beteiligten (A-28/A-29)
    ---------------------------------------------------------------------------
    DECLARE
        v_instr uuid;
    BEGIN
        INSERT INTO billing.billing_instruction (property_id, delivery_method, valid_from)
        VALUES (v_prop, 'EMAIL', DATE '2020-01-01') RETURNING id INTO v_instr;
        INSERT INTO billing.billing_instruction_party (instruction_id, role, party_id, is_primary)
        VALUES (v_instr, 'INVOICE_RECIPIENT', v_mgmt, true),
               (v_instr, 'DEBTOR', v_weg, false);
        INSERT INTO billing.billing_instruction_party (instruction_id, role, party_id, allocation_percent)
        VALUES (v_instr, 'COST_BEARER', v_owner_a, 60),
               (v_instr, 'COST_BEARER', v_owner_b, 40);
        RAISE NOTICE 'OK  Test 16a: Verwaltung als Empfänger, WEG als Schuldner, zwei Kostenträger';

        BEGIN
            INSERT INTO billing.billing_instruction_party (instruction_id, role, party_id, is_primary)
            VALUES (v_instr, 'INVOICE_RECIPIENT', v_weg, true);
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 16b: zweiter primärer Rechnungsempfänger wurde akzeptiert';
        EXCEPTION WHEN unique_violation THEN
            RAISE NOTICE 'OK  Test 16b: zweiter primärer Rechnungsempfänger korrekt abgelehnt';
        END;
    END;

    ---------------------------------------------------------------------------
    -- Test 17 (F-01): unit_type-Wechsel zu COMMON_AREA trotz Eigentumsstand
    ---------------------------------------------------------------------------
    BEGIN
        UPDATE property.unit SET unit_type = 'COMMON_AREA' WHERE id = v_unit1;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 17: Typwechsel trotz Eigentumsstand akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 17: Typwechsel zu COMMON_AREA trotz Eigentumsstand korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 18 (F-05): SOLE mit zwei Beteiligungen muss abgelehnt werden
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference, confirmed_at, confirmed_by_user_id)
        VALUES (v_unit3, 'COMPLETE', DATE '2021-01-01', 'OWNER_LIST', 'Testliste', now(), v_user)
        RETURNING id INTO v_period;
        INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id, share_numerator, share_denominator, ownership_type, confirmation_status)
        VALUES (v_period, v_owner_a, 1, 2, 'SOLE', 'CONFIRMED'),
               (v_period, v_owner_b, 1, 2, 'SOLE', 'CONFIRMED');
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 18: SOLE mit zwei Beteiligungen akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 18: SOLE mit zwei Beteiligungen korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 19: COMPLETE mit unbestätigter Beteiligung muss abgelehnt werden
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference, confirmed_at, confirmed_by_user_id)
        VALUES (v_unit3, 'COMPLETE', DATE '2022-01-01', 'OWNER_LIST', 'Testliste', now(), v_user)
        RETURNING id INTO v_period;
        INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id, share_numerator, share_denominator, confirmation_status)
        VALUES (v_period, v_owner_a, 1, 1, 'UNCONFIRMED');
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 19: COMPLETE mit unbestätigter Beteiligung akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 19: COMPLETE mit unbestätigter Beteiligung korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 20 (F-02): DELETE auf historisiertem Eigentumsstand muss scheitern
    ---------------------------------------------------------------------------
    BEGIN
        DELETE FROM tenure.ownership_period WHERE unit_id = v_unit1;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 20: DELETE auf Eigentumsstand akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 20: DELETE auf historisiertem Eigentumsstand korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 21 (F-02): Änderung an Belegung erzeugt automatisch einen Audit-Eintrag
    ---------------------------------------------------------------------------
    DECLARE
        v_audit_count integer;
    BEGIN
        UPDATE tenure.occupancy SET contract_reference = 'MV-2020-001' WHERE id = v_occ;
        SELECT count(*) INTO v_audit_count
        FROM audit.audit_entry
        WHERE action = 'ROW_UPDATE' AND target_type = 'tenure.occupancy' AND target_id = v_occ;
        IF v_audit_count < 1 THEN
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 21: kein Audit-Eintrag für Belegungsänderung';
        END IF;
        RAISE NOTICE 'OK  Test 21: Belegungsänderung automatisch auditiert (Vorher/Nachher)';
    END;

    ---------------------------------------------------------------------------
    -- Test 22: Append-only vollständig (domain_event, party_merge, DELETE auf Audit)
    ---------------------------------------------------------------------------
    INSERT INTO audit.domain_event (event_type, aggregate_type, aggregate_id)
    VALUES ('TEST_EVENT', 'test', v_unit1);
    BEGIN
        UPDATE audit.domain_event SET event_type = 'MANIPULIERT' WHERE aggregate_id = v_unit1;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 22a: domain_event änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 22a: domain_event ist append-only';
    END;
    BEGIN
        UPDATE identity.party_merge SET reason = 'MANIPULIERT' WHERE merged_party_id = v_dup;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 22b: party_merge änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 22b: party_merge ist append-only';
    END;
    BEGIN
        DELETE FROM audit.audit_entry WHERE id = v_audit;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 22c: Audit-Eintrag löschbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 22c: Audit-Einträge sind nicht löschbar';
    END;

    ---------------------------------------------------------------------------
    -- Test 23: überlappende Vollmandate desselben Typs (Exclusion)
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO management.management_mandate
            (management_party_id, principal_party_id, property_id, mandate_type,
             scope_type, valid_from, default_contact_party_id)
        VALUES (v_mgmt, v_weg, v_prop, 'WEG_MANAGEMENT', 'ENTIRE_PROPERTY',
                DATE '2024-06-01', v_mgmt);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 23: überlappendes Vollmandat akzeptiert';
    EXCEPTION WHEN exclusion_violation THEN
        RAISE NOTICE 'OK  Test 23: überlappende Vollmandate desselben Typs korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 24 (F-06): neue Beteiligung auf zusammengeführte Party muss scheitern
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO tenure.ownership_period (unit_id, distribution_status, valid_from, source_type, source_reference)
        VALUES (v_unit3, 'PARTIAL', DATE '2023-01-01', 'MANUAL', 'Testfall F-06')
        RETURNING id INTO v_period;
        INSERT INTO tenure.ownership_interest (ownership_period_id, owner_party_id)
        VALUES (v_period, v_dup);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 24: Referenz auf MERGED-Party akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 24: neue Referenz auf zusammengeführte Party korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 25 (F-12): Belegung auf Gemeinschaftsfläche muss scheitern
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO tenure.occupancy (unit_id, occupancy_type, valid_from)
        VALUES (v_common, 'RENTED', DATE '2020-01-01');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 25: Belegung auf COMMON_AREA akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 25: Belegung auf Gemeinschaftsfläche korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 26 (OPUS-07): primäre Adressen — historisch erlaubt, zeitgleich verboten
    ---------------------------------------------------------------------------
    INSERT INTO identity.party_address (party_id, address_id, address_type, is_primary, valid_from, valid_until)
    VALUES (v_mgmt, v_addr, 'BUSINESS', true, DATE '2018-01-01', DATE '2020-01-01');
    INSERT INTO identity.party_address (party_id, address_id, address_type, is_primary, valid_from)
    VALUES (v_mgmt, v_addr, 'BUSINESS', true, DATE '2020-01-01');
    RAISE NOTICE 'OK  Test 26a: historische und aktuelle Primäradresse nacheinander zulässig';
    BEGIN
        INSERT INTO identity.party_address (party_id, address_id, address_type, is_primary, valid_from)
        VALUES (v_mgmt, v_addr, 'BUSINESS', true, DATE '2023-01-01');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 26b: zweite zeitgleiche Primäradresse akzeptiert';
    EXCEPTION WHEN exclusion_violation THEN
        RAISE NOTICE 'OK  Test 26b: zweite zeitgleiche Primäradresse korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 27: Party-Typ-Konsistenz (Personendatensatz für Organisation)
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO identity.person (party_id, first_name, last_name)
        VALUES (v_weg, 'Falsch', 'Zugeordnet');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 27: Person auf Organisations-Party akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 27: Personendatensatz auf Organisations-Party korrekt abgelehnt';
    END;

    ---------------------------------------------------------------------------
    -- Test 28: technische Anlage — Einheit ohne Gebäudeangabe unzulässig
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO property.technical_asset (property_id, unit_id, name)
        VALUES (v_prop, v_unit1, 'Therme ohne Gebäude');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 28: Anlage mit Einheit ohne Gebäude akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 28: Anlagen-Standortkonsistenz korrekt erzwungen';
    END;

    ---------------------------------------------------------------------------
    -- Test 29: konkurrierendes Update mit veralteter Versionsnummer
    ---------------------------------------------------------------------------
    DECLARE
        v_rows integer;
    BEGIN
        UPDATE property.property SET name = name, version = version + 1
        WHERE id = v_prop AND version = 1;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows <> 1 THEN
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 29a: erstes Versions-Update griff nicht';
        END IF;
        UPDATE property.property SET name = name, version = version + 1
        WHERE id = v_prop AND version = 1;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows <> 0 THEN
            RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 29b: veraltete Version wurde nicht erkannt';
        END IF;
        RAISE NOTICE 'OK  Test 29: veraltete Versionsnummer trifft null Zeilen (Versionskonflikt erkennbar)';
    END;

    ---------------------------------------------------------------------------
    -- Test 30 (F-10): beendetes Mandat ohne Enddatum unzulässig
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO management.management_mandate
            (management_party_id, principal_party_id, property_id, mandate_type,
             scope_type, valid_from, status, default_contact_party_id)
        VALUES (v_mgmt, v_owner_b, v_prop, 'RENTAL_MANAGEMENT', 'ENTIRE_PROPERTY',
                DATE '2020-01-01', 'ENDED', v_mgmt);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 30: ENDED ohne Enddatum akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 30: beendetes Mandat ohne Enddatum korrekt abgelehnt';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    ---------------------------------------------------------------------------
    -- Test 31: rückwirkende Belegung in eine Lücke (Positivfall A-18)
    ---------------------------------------------------------------------------
    INSERT INTO tenure.occupancy (unit_id, occupancy_type, valid_from, valid_until)
    VALUES (v_unit2, 'RENTED', DATE '2021-01-01', DATE '2022-01-01');
    INSERT INTO tenure.occupancy (unit_id, occupancy_type, valid_from, valid_until)
    VALUES (v_unit2, 'UNKNOWN', DATE '2020-01-01', DATE '2021-01-01');
    RAISE NOTICE 'OK  Test 31: rückwirkende Belegung in eine Lücke zulässig';

    RAISE NOTICE '';
    RAISE NOTICE 'ALLE AKZEPTANZTESTS BESTANDEN';
END;
$$;

ROLLBACK;
