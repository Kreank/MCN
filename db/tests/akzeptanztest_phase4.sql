-- Akzeptanztest Phase 4 — Dateien, Dokumentenbuilder, Unterschrift, Kommunikation.
-- Läuft in einer Transaktion und rollt sich am Ende zurück.

BEGIN;

DO $$
DECLARE
    v_user   uuid;
    v_addr   uuid;
    v_party  uuid;
    v_prop   uuid;
    v_order  uuid;
    v_job    uuid;
    v_video  uuid;
    v_pdf    uuid;
    v_sig_img uuid;
    v_doc    uuid;
    v_doc2   uuid;
    v_inv    uuid;
    v_comm   uuid;
    v_count  integer;
BEGIN
    ---------------------------------------------------------------------------
    -- Fixtures
    ---------------------------------------------------------------------------
    INSERT INTO security.app_user (display_name) VALUES ('Phase4-Tester') RETURNING id INTO v_user;
    PERFORM set_config('app.current_user_id', v_user::text, true);

    INSERT INTO identity.address (street, house_number, postal_code, city)
    VALUES ('Contentweg', '4', '44444', 'Medienstadt') RETURNING id INTO v_addr;
    INSERT INTO identity.party (party_type, display_name) VALUES ('PERSON', 'Sigrid Signatur') RETURNING id INTO v_party;
    INSERT INTO identity.person (party_id, first_name, last_name) VALUES (v_party, 'Sigrid', 'Signatur');
    INSERT INTO property.property (name, address_id, property_type)
    VALUES ('Contentweg 4', v_addr, 'RENTAL_PROPERTY') RETURNING id INTO v_prop;
    INSERT INTO workflow.work_order (title, property_id) VALUES ('Dachrinne reinigen', v_prop)
    RETURNING id INTO v_order;
    INSERT INTO workflow.service_job (work_order_id) VALUES (v_order) RETURNING id INTO v_job;
    RAISE NOTICE 'OK  Fixtures Phase 4 angelegt';

    ---------------------------------------------------------------------------
    -- Test 1: Video-Datei (2 GB) registrieren und am Einsatz verknüpfen
    ---------------------------------------------------------------------------
    INSERT INTO content.file (storage_key, original_filename, mime_type, size_bytes, sha256,
                              media_metadata, uploaded_by)
    VALUES ('2026/07/baustelle-begehung-001.mp4', 'begehung.mp4', 'video/mp4',
            2147483648, repeat('a', 64),
            '{"duration_seconds": 483, "width": 3840, "height": 2160, "codec": "h264"}'::jsonb,
            v_user)
    RETURNING id INTO v_video;
    INSERT INTO content.file_link (file_id, service_job_id, link_category, created_by)
    VALUES (v_video, v_job, 'VIDEO_BEGEHUNG', v_user);
    RAISE NOTICE 'OK  Test 1a: 2-GB-Video als Metadatensatz registriert und am Einsatz verknüpft';

    BEGIN
        UPDATE content.file SET original_filename = 'manipuliert.mp4' WHERE id = v_video;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 1b: Datei-Steckbrief änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 1b: Datei-Steckbrief ist unveränderlich';
    END;
    BEGIN
        INSERT INTO content.file_link (file_id, service_job_id, property_id, created_by)
        VALUES (v_video, v_job, v_prop, v_user);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 1c: Link mit zwei Zielen akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 1c: Verknüpfung hat genau ein Ziel (kein freier Polymorphismus)';
    END;

    ---------------------------------------------------------------------------
    -- Test 2: Baustellenbericht — Veröffentlichung nur vollständig
    ---------------------------------------------------------------------------
    INSERT INTO content.file (storage_key, original_filename, mime_type, size_bytes, sha256, uploaded_by)
    VALUES ('2026/07/bericht-001.pdf', 'bericht.pdf', 'application/pdf', 123456, repeat('b', 64), v_user)
    RETURNING id INTO v_pdf;

    INSERT INTO content.document (document_type, title, service_job_id, builder_payload, created_by)
    VALUES ('EINSATZBERICHT', 'Baustellenbericht Dachrinne', v_job,
            '{"bloecke": [{"typ": "text", "inhalt": "Arbeiten ausgeführt"}, {"typ": "video", "file": "begehung"}]}'::jsonb,
            v_user)
    RETURNING id INTO v_doc;

    BEGIN
        UPDATE content.document SET status = 'VEROEFFENTLICHT' WHERE id = v_doc;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 2a: Veröffentlichung ohne Rendering akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 2a: Veröffentlichung ohne gerenderte Datei abgelehnt (B-30)';
    END;

    -- Hash muss dem SHA-256 der gerenderten Datei entsprechen (MEDIUM-Fix)
    BEGIN
        UPDATE content.document
        SET rendered_file_id = v_pdf, content_hash = md5('irgendwas'),
            status = 'VEROEFFENTLICHT'
        WHERE id = v_doc;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 2a2: falscher Inhalts-Hash akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 2a2: content_hash ist an SHA-256 der Datei gekoppelt (B-30/B-34)';
    END;

    UPDATE content.document
    SET rendered_file_id = v_pdf, content_hash = repeat('b', 64)
    WHERE id = v_doc;
    UPDATE content.document SET status = 'VEROEFFENTLICHT' WHERE id = v_doc;
    SELECT count(*) INTO v_count FROM content.document
    WHERE id = v_doc AND status = 'VEROEFFENTLICHT' AND published_by = v_user AND published_at IS NOT NULL;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 2b: Veröffentlichung unvollständig';
    END IF;
    RAISE NOTICE 'OK  Test 2b: Baustellenbericht veröffentlicht (Benutzer + Zeitpunkt erfasst)';

    ---------------------------------------------------------------------------
    -- Test 3 (B-30): veröffentlichtes Dokument eingefroren; ERSETZT nicht manuell
    ---------------------------------------------------------------------------
    BEGIN
        UPDATE content.document SET title = 'Manipuliert' WHERE id = v_doc;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3a: veröffentlichtes Dokument änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 3a: veröffentlichtes Dokument ist eingefroren (B-30)';
    END;
    BEGIN
        UPDATE content.document SET status = 'ERSETZT' WHERE id = v_doc;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3b: ERSETZT manuell setzbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 3b: ERSETZT entsteht nur durch neue Version (B-30)';
    END;
    -- HIGH-2-Regression: auch mit selbst gesetzter GUC bleibt ERSETZT gesperrt
    BEGIN
        PERFORM set_config('content.superseding_document', 'angriff', true);
        UPDATE content.document SET status = 'ERSETZT' WHERE id = v_doc;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 3c: GUC-Spoofing ermöglicht manuelles ERSETZT';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 3c: GUC-Spoofing wirkungslos — Triggertiefe wird geprüft (HIGH-2)';
    END;
    PERFORM set_config('content.superseding_document', '', true);

    ---------------------------------------------------------------------------
    -- Test 4 (B-34): Unterschrift — nur veröffentlicht, hash-gebunden, append-only
    ---------------------------------------------------------------------------
    INSERT INTO content.file (storage_key, original_filename, mime_type, size_bytes, sha256, uploaded_by)
    VALUES ('2026/07/signatur-001.png', 'unterschrift.png', 'image/png', 4096, repeat('c', 64), v_user)
    RETURNING id INTO v_sig_img;

    BEGIN
        INSERT INTO content.signature (document_id, signer_name, signature_image_file_id,
                                       signed_content_hash, captured_by)
        VALUES (v_doc, 'Sigrid Signatur', v_sig_img, 'falscher-hash', v_user);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4a: Unterschrift mit falschem Hash akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4a: Unterschrift ist an den Dokument-Hash gebunden (B-34)';
    END;
    INSERT INTO content.signature (document_id, signer_name, signer_party_id,
                                   signature_image_file_id, signed_content_hash, captured_by)
    VALUES (v_doc, 'Sigrid Signatur', v_party, v_sig_img, repeat('b', 64), v_user);
    RAISE NOTICE 'OK  Test 4b: Unterschrift auf veröffentlichtem Bericht erfasst';
    BEGIN
        UPDATE content.signature SET signer_name = 'Jemand anderes'
        WHERE document_id = v_doc;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 4c: Unterschrift änderbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 4c: Unterschriften sind append-only';
    END;

    ---------------------------------------------------------------------------
    -- Test 5 (B-30): neue Version ersetzt den Vorgänger kontrolliert
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO content.document (document_type, title, service_job_id, version,
                                      previous_version_id, created_by)
        VALUES ('EINSATZBERICHT', 'Bericht v5', v_job, 5, v_doc, v_user);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5a: falsche Versionsnummer akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 5a: Versionsnummer muss Vorgänger + 1 sein';
    END;
    INSERT INTO content.document (document_type, title, service_job_id, version,
                                  previous_version_id, builder_payload, created_by)
    VALUES ('EINSATZBERICHT', 'Baustellenbericht Dachrinne (korrigiert)', v_job, 2, v_doc,
            '{"bloecke": []}'::jsonb, v_user)
    RETURNING id INTO v_doc2;
    SELECT count(*) INTO v_count FROM content.document WHERE id = v_doc AND status = 'ERSETZT';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5b: Vorgänger nicht auf ERSETZT gestellt';
    END IF;
    RAISE NOTICE 'OK  Test 5b: Neue Version angelegt, Vorgänger automatisch ERSETZT (B-30)';
    BEGIN
        INSERT INTO content.document (document_type, title, service_job_id, version,
                                      previous_version_id, created_by)
        VALUES ('EINSATZBERICHT', 'Zweiter Nachfolger', v_job, 2, v_doc, v_user);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 5c: zweiter Nachfolger akzeptiert';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'OK  Test 5c: höchstens ein Nachfolger je Version';
    END;

    ---------------------------------------------------------------------------
    -- Test 6: Typ-Quellen-Kopplung des Builders
    ---------------------------------------------------------------------------
    BEGIN
        INSERT INTO content.document (document_type, title, created_by)
        VALUES ('ANGEBOT', 'Angebot ohne Quelle', v_user);
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 6a: Angebotsdokument ohne Angebot akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 6a: Angebotsdokument erfordert Angebotsquelle';
    END;
    INSERT INTO invoicing.invoice (invoice_type, property_id, work_order_id)
    VALUES ('RECHNUNG', v_prop, v_order) RETURNING id INTO v_inv;
    INSERT INTO content.document (document_type, title, invoice_id, rendered_file_id,
                                  content_hash, created_by)
    VALUES ('RECHNUNG', 'Rechnungsdokument', v_inv, v_pdf, repeat('b', 64), v_user)
    RETURNING id INTO v_doc2;
    BEGIN
        UPDATE content.document SET status = 'VEROEFFENTLICHT' WHERE id = v_doc2;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 6b: Rechnungsdokument zu Entwurfsbeleg veröffentlicht';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 6b: Rechnungsdokument erfordert veröffentlichten Beleg (B-21)';
    END;

    ---------------------------------------------------------------------------
    -- Test 7 (B-31/B-32): Kommunikation — Klärungskorb und Zuordnung
    ---------------------------------------------------------------------------
    INSERT INTO content.communication (channel, direction, subject, body, counterpart_raw, recorded_by)
    VALUES ('EMAIL', 'EINGEHEND', 'Wasserfleck', 'Guten Tag, bei uns tropft es...',
            'mieter@example.org', v_user)
    RETURNING id INTO v_comm;
    RAISE NOTICE 'OK  Test 7a: eingehende E-Mail landet im Klärungskorb';

    BEGIN
        UPDATE content.communication
        SET assignment_status = 'ZUGEORDNET', assignment_source = 'MANUELL'
        WHERE id = v_comm;
        SET CONSTRAINTS ALL IMMEDIATE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7b: ZUGEORDNET ohne Verknüpfung akzeptiert';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 7b: Zuordnung ohne Ziel abgelehnt (B-31)';
    END;
    SET CONSTRAINTS ALL DEFERRED;

    -- Zuordnung mit Ziel in einer Transaktion
    INSERT INTO content.communication_link (communication_id, work_order_id, created_by)
    VALUES (v_comm, v_order, v_user);
    UPDATE content.communication
    SET assignment_status = 'ZUGEORDNET', assignment_source = 'VORGANGSNUMMER'
    WHERE id = v_comm;
    SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;
    RAISE NOTICE 'OK  Test 7c: Zuordnung über Vorgangsnummer mit Ziel (B-31)';

    BEGIN
        INSERT INTO content.communication (channel, direction, subject, recorded_by,
                                           assignment_status, assignment_source)
        VALUES ('EMAIL', 'EINGEHEND', 'KI-Test', v_user, 'ZUGEORDNET', 'KI_BESTAETIGT');
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 7d: KI-Zuordnung ohne Bestätiger akzeptiert';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK  Test 7d: KI-Zuordnung erfordert menschlichen Bestätiger (B-31/B-42)';
    END;

    ---------------------------------------------------------------------------
    -- Test 8: Schutz — kein DELETE/TRUNCATE auf Dokumenten und Kommunikation
    ---------------------------------------------------------------------------
    BEGIN
        DELETE FROM content.communication WHERE id = v_comm;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 8a: Kommunikation löschbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 8a: Kommunikation ist nicht löschbar';
    END;
    BEGIN
        TRUNCATE content.document CASCADE;
        RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: Test 8b: Dokumente truncierbar';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FEHLGESCHLAGEN%' THEN RAISE; END IF;
        RAISE NOTICE 'OK  Test 8b: Dokumente gegen TRUNCATE geschützt (F-03)';
    END;

    RAISE NOTICE '';
    RAISE NOTICE 'ALLE PHASE-4-AKZEPTANZTESTS BESTANDEN';
END;
$$;

ROLLBACK;
