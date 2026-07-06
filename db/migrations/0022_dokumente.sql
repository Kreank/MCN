-- Migration 0022: Dokumente (Dokumentenbuilder-Fundament) und digitale Unterschrift
-- Beschlüsse: B-29 (Dokumenttypen), B-30 (Unveränderlichkeit nach Veröffentlichung,
--             neue Version statt Überschreiben), B-34 (Unterschrift auf Gerät,
--             GF/Recht-Vorbehalt zum Beweiswert in der Checkliste), B-21 (Belegbindung)

BEGIN;

-- ---------------------------------------------------------------------------
-- content.document — fachliches Dokument mit Versionierung.
-- Der Dokumentenbuilder arbeitet auf `builder_payload` (strukturierter Inhalt)
-- und erzeugt beim Veröffentlichen eine gerenderte Datei (PDF) im Object Storage.
-- Quellen: Angebot, Rechnung oder Einsatz (Baustellen-/Einsatzbericht) — optional,
-- freie Dokumente (Protokoll, Schriftverkehr) haben keine Quelle.
-- ---------------------------------------------------------------------------
CREATE TABLE content.document (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_type         text NOT NULL CHECK (document_type IN
                          ('ANGEBOT', 'AUFTRAGSBESTAETIGUNG', 'RECHNUNG', 'GUTSCHRIFT',
                           'EINSATZBERICHT', 'PROTOKOLL', 'WARTUNGSBERICHT',
                           'EIGENTUEMERLISTE', 'VERTRAG_MANDAT', 'SCHRIFTVERKEHR',
                           'SONSTIGES')),
    title                 text NOT NULL CHECK (btrim(title) <> ''),
    status                text NOT NULL DEFAULT 'ENTWURF'
                          CHECK (status IN ('ENTWURF', 'VEROEFFENTLICHT', 'ERSETZT')),
    version               integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    previous_version_id   uuid NULL REFERENCES content.document (id),
    -- Builder-Quellen (höchstens eine)
    quote_id              uuid NULL REFERENCES invoicing.quote (id),
    invoice_id            uuid NULL REFERENCES invoicing.invoice (id),
    service_job_id        uuid NULL REFERENCES workflow.service_job (id),
    -- strukturierter Builder-Inhalt (Blöcke, Texte, Bild-/Video-Referenzen)
    builder_payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- gerenderte Ausgabedatei (PDF) — Pflicht ab Veröffentlichung
    rendered_file_id      uuid NULL REFERENCES content.file (id),
    content_hash          text NULL,
    published_at          timestamptz NULL,
    published_by          uuid NULL REFERENCES security.app_user (id),
    created_by            uuid NOT NULL REFERENCES security.app_user (id),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(quote_id, invoice_id, service_job_id) <= 1),
    CHECK (previous_version_id IS NULL OR previous_version_id <> id),
    CHECK (version = 1 OR previous_version_id IS NOT NULL),
    CHECK ((published_at IS NULL) = (published_by IS NULL)),
    -- Typ und Quelle passen zusammen
    CHECK (document_type <> 'ANGEBOT' OR quote_id IS NOT NULL),
    CHECK (document_type NOT IN ('RECHNUNG', 'GUTSCHRIFT') OR invoice_id IS NOT NULL),
    CHECK (document_type NOT IN ('EINSATZBERICHT', 'WARTUNGSBERICHT') OR service_job_id IS NOT NULL)
);

CREATE TRIGGER trg_document_updated_at
    BEFORE UPDATE ON content.document
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TRIGGER trg_document_initial_status
    BEFORE INSERT ON content.document
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('ENTWURF');

CREATE TRIGGER trg_document_status_log
    AFTER INSERT OR UPDATE OF status ON content.document
    FOR EACH ROW EXECUTE FUNCTION workflow.log_status_change('document');

-- ---------------------------------------------------------------------------
-- Statusdisziplin und B-30-Einfrieren:
--   ENTWURF -> VEROEFFENTLICHT (Rendering + Hash Pflicht; Quellbeleg muss
--     veröffentlicht sein)
--   VEROEFFENTLICHT -> ERSETZT (nur durch eine neue Version, die auf dieses
--     Dokument verweist)
-- ---------------------------------------------------------------------------
CREATE FUNCTION content.guard_document_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_quote_status   text;
    v_invoice_status text;
BEGIN
    IF OLD.status = 'ENTWURF' AND NEW.status = 'VEROEFFENTLICHT' THEN
        IF NEW.rendered_file_id IS NULL OR NEW.content_hash IS NULL THEN
            RAISE EXCEPTION
                'Dokument %: Veröffentlichung ohne gerenderte Datei und Inhalts-Hash ist unzulässig (B-30)',
                NEW.id;
        END IF;
        -- MEDIUM-Fix: Der Inhalts-Hash ist physisch an die gerenderte Datei gekoppelt
        -- (SHA-256 aus dem unveränderlichen Datei-Steckbrief) — keine App-Zusage nötig.
        IF NEW.content_hash IS DISTINCT FROM
           (SELECT sha256 FROM content.file WHERE id = NEW.rendered_file_id) THEN
            RAISE EXCEPTION
                'Dokument %: content_hash muss dem SHA-256 der gerenderten Datei entsprechen (B-30/B-34)',
                NEW.id;
        END IF;
        IF NEW.quote_id IS NOT NULL THEN
            SELECT status INTO v_quote_status FROM invoicing.quote WHERE id = NEW.quote_id;
            IF v_quote_status NOT IN ('VERSENDET', 'ANGENOMMEN', 'ABGELEHNT', 'ABGELAUFEN', 'ERSETZT') THEN
                RAISE EXCEPTION
                    'Dokument %: Angebotsdokument erfordert ein versendetes Angebot (B-14/B-30)', NEW.id;
            END IF;
        END IF;
        IF NEW.invoice_id IS NOT NULL THEN
            SELECT status INTO v_invoice_status FROM invoicing.invoice WHERE id = NEW.invoice_id;
            IF v_invoice_status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
                RAISE EXCEPTION
                    'Dokument %: Rechnungs-/Gutschriftsdokument erfordert einen veröffentlichten Beleg (B-21)', NEW.id;
            END IF;
        END IF;
        NEW.published_at := now();
        NEW.published_by := nullif(current_setting('app.current_user_id', true), '')::uuid;
        IF NEW.published_by IS NULL THEN
            RAISE EXCEPTION
                'Dokument %: Veröffentlichung erfordert einen angemeldeten Benutzer (SET LOCAL app.current_user_id)',
                NEW.id;
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'VEROEFFENTLICHT' AND NEW.status = 'ERSETZT' THEN
        -- HIGH-2-Fix: Nur der Versionierungs-Trigger der Nachfolgeversion darf das
        -- setzen. Die GUC allein genügt NICHT (von jedem Aufrufer setzbar) — der
        -- legitime Pfad läuft zwingend verschachtelt im INSERT-Trigger der neuen
        -- Version (pg_trigger_depth() >= 2).
        IF pg_trigger_depth() < 2
           OR nullif(current_setting('content.superseding_document', true), '') IS NULL THEN
            RAISE EXCEPTION
                'Dokument %: ERSETZT entsteht nur durch eine neue Version, nicht manuell (B-30)', OLD.id;
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION
            'Dokument %: Statusübergang % -> % ist nicht erlaubt', OLD.id, OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_document_transition
    BEFORE UPDATE OF status ON content.document
    FOR EACH ROW EXECUTE FUNCTION content.guard_document_transition();

-- B-30: Veröffentlichte und ersetzte Dokumente sind inhaltlich eingefroren
CREATE FUNCTION content.freeze_published_document() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('VEROEFFENTLICHT', 'ERSETZT') THEN
        IF (to_jsonb(NEW) - 'status' - 'updated_at')
           IS DISTINCT FROM
           (to_jsonb(OLD) - 'status' - 'updated_at') THEN
            RAISE EXCEPTION
                'Dokument %: Inhalt ist nach Veröffentlichung unveränderlich (B-30); neue Version anlegen', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_document_freeze
    BEFORE UPDATE ON content.document
    FOR EACH ROW EXECUTE FUNCTION content.freeze_published_document();

-- Neue Version: nur auf VEROEFFENTLICHT/ERSETZT, gleicher Typ, Version+1;
-- der Vorgänger wird in derselben Transaktion auf ERSETZT gestellt.
CREATE FUNCTION content.handle_document_version() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_prev content.document%ROWTYPE;
BEGIN
    IF NEW.previous_version_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT * INTO v_prev FROM content.document
    WHERE id = NEW.previous_version_id FOR UPDATE;

    IF v_prev.status = 'ENTWURF' THEN
        RAISE EXCEPTION
            'Dokument %: Ein Entwurf wird bearbeitet, nicht versioniert (B-30)', NEW.previous_version_id;
    END IF;
    IF v_prev.document_type <> NEW.document_type THEN
        RAISE EXCEPTION
            'Dokument %: Neue Version muss denselben Dokumenttyp behalten', NEW.id;
    END IF;
    IF NEW.version <> v_prev.version + 1 THEN
        RAISE EXCEPTION
            'Dokument %: Neue Version muss Vorgängerversion + 1 tragen (% erwartet)', NEW.id, v_prev.version + 1;
    END IF;
    IF v_prev.status = 'VEROEFFENTLICHT' THEN
        PERFORM set_config('content.superseding_document', NEW.id::text, true);
        UPDATE content.document SET status = 'ERSETZT' WHERE id = v_prev.id;
        PERFORM set_config('content.superseding_document', '', true);
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_document_version
    BEFORE INSERT ON content.document
    FOR EACH ROW EXECUTE FUNCTION content.handle_document_version();

-- Höchstens eine nicht-ersetzte Version je Vorgänger
CREATE UNIQUE INDEX uq_document_successor ON content.document (previous_version_id)
    WHERE previous_version_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- content.document_link — kontrollierte Verknüpfung zu Fachobjekten
-- ---------------------------------------------------------------------------
CREATE TABLE content.document_link (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id      uuid NOT NULL REFERENCES content.document (id),
    service_case_id  uuid NULL REFERENCES workflow.service_case (id),
    work_order_id    uuid NULL REFERENCES workflow.work_order (id),
    service_job_id   uuid NULL REFERENCES workflow.service_job (id),
    property_id      uuid NULL REFERENCES property.property (id),
    unit_id          uuid NULL REFERENCES property.unit (id),
    party_id         uuid NULL REFERENCES identity.party (id),
    project_id       uuid NULL REFERENCES workflow.project (id),
    mandate_id       uuid NULL REFERENCES management.management_mandate (id),
    created_by       uuid NOT NULL REFERENCES security.app_user (id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                        unit_id, party_id, project_id, mandate_id) = 1)
);

CREATE INDEX idx_document_link_document ON content.document_link (document_id);

-- ---------------------------------------------------------------------------
-- content.signature — Unterschrift auf dem Gerät (B-34)
-- Nur auf veröffentlichten Dokumenten; an deren Inhalts-Hash gebunden.
-- Beweiswert: GF/Recht-Vorbehalt in der Vorbehalts-Checkliste (Teil C).
-- ---------------------------------------------------------------------------
CREATE TABLE content.signature (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id              uuid NOT NULL REFERENCES content.document (id),
    signer_name              text NOT NULL CHECK (btrim(signer_name) <> ''),
    signer_party_id          uuid NULL REFERENCES identity.party (id),
    signature_image_file_id  uuid NOT NULL REFERENCES content.file (id),
    signed_content_hash      text NOT NULL,
    signed_at                timestamptz NOT NULL DEFAULT now(),
    captured_by              uuid NOT NULL REFERENCES security.app_user (id),
    device_info              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION content.check_signature() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_doc content.document%ROWTYPE;
BEGIN
    SELECT * INTO v_doc FROM content.document WHERE id = NEW.document_id FOR SHARE;
    IF v_doc.status NOT IN ('VEROEFFENTLICHT', 'ERSETZT') THEN
        RAISE EXCEPTION
            'Unterschrift: Dokument % ist nicht veröffentlicht (B-34: erst veröffentlichen, dann unterschreiben)',
            NEW.document_id;
    END IF;
    IF NEW.signed_content_hash IS DISTINCT FROM v_doc.content_hash THEN
        RAISE EXCEPTION
            'Unterschrift: Hash stimmt nicht mit dem veröffentlichten Dokumentinhalt überein (B-34)';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_signature_check
    BEFORE INSERT ON content.signature
    FOR EACH ROW EXECUTE FUNCTION content.check_signature();

-- Unterschriften sind append-only
CREATE TRIGGER trg_signature_append_only
    BEFORE UPDATE OR DELETE ON content.signature
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_signature_no_truncate
    BEFORE TRUNCATE ON content.signature
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON content.signature FROM PUBLIC;

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen, nur solange keine
-- Dokumente entstanden sind. Veröffentlichte Dokumente werden niemals rückwärts migriert.
