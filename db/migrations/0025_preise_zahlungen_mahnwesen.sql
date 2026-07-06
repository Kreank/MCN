-- Migration 0025: Preislisten, Zahlungsspiegel und Mahnstruktur
-- Beschlüsse: B-25 (zentrale Preisliste, Aufschlagsgruppen, Kundenvereinbarungen),
--             B-16 (Freigabegrenzen als Stammdaten, KEINE erfundenen Beträge),
--             B-23 (Zahlungen: Buchhaltung führend, Einweg-Import-Spiegel),
--             B-22 (Mahnstufen-Struktur; Gebühren/Zinsen mit STB-Vorbehalt)

BEGIN;

CREATE SCHEMA pricing;

-- ---------------------------------------------------------------------------
-- B-25: Aufschlagsgruppen (EK -> VK) und zentrale Preisliste mit Gültigkeit
-- ---------------------------------------------------------------------------
CREATE TABLE pricing.markup_group (
    code            text PRIMARY KEY CHECK (btrim(code) <> ''),
    label           text NOT NULL,
    markup_percent  numeric(7, 4) NOT NULL CHECK (markup_percent >= 0),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_markup_group_updated_at
    BEFORE UPDATE ON pricing.markup_group
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TABLE pricing.price_list (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL CHECK (btrim(name) <> ''),
    valid_from   date NOT NULL,
    valid_until  date NULL,
    version      integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from)
);
CREATE TRIGGER trg_price_list_updated_at
    BEFORE UPDATE ON pricing.price_list
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TABLE pricing.price_list_item (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    price_list_id   uuid NOT NULL REFERENCES pricing.price_list (id),
    item_number     text NOT NULL CHECK (btrim(item_number) <> ''),
    description     text NOT NULL CHECK (btrim(description) <> ''),
    line_type       text NOT NULL CHECK (line_type IN
                    ('MATERIAL', 'ARBEITSZEIT', 'PAUSCHALE', 'FREMDLEISTUNG',
                     'FAHRT', 'ZUSCHLAG')),
    unit            text NOT NULL,
    -- B-19: Einzelpreise mit 2 Nachkommastellen
    unit_price      numeric(15, 2) NOT NULL,
    purchase_price  numeric(15, 2) NULL,
    markup_group    text NULL REFERENCES pricing.markup_group (code),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (price_list_id, item_number)
);
CREATE TRIGGER trg_price_list_item_updated_at
    BEFORE UPDATE ON pricing.price_list_item
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- Kundenspezifische Preisvereinbarungen: datiert und mit dokumentierter Grundlage
CREATE TABLE pricing.customer_price_agreement (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    party_id     uuid NOT NULL REFERENCES identity.party (id),
    description  text NOT NULL CHECK (btrim(description) <> ''),
    line_type    text NOT NULL CHECK (line_type IN
                 ('MATERIAL', 'ARBEITSZEIT', 'PAUSCHALE', 'FREMDLEISTUNG',
                  'FAHRT', 'ZUSCHLAG')),
    unit         text NOT NULL,
    unit_price   numeric(15, 2) NOT NULL,
    -- B-25/B-20: nur dokumentierte Vereinbarungen
    evidence_reference text NOT NULL CHECK (btrim(evidence_reference) <> ''),
    valid_from   date NOT NULL,
    valid_until  date NULL,
    created_by   uuid NOT NULL REFERENCES security.app_user (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from)
);
CREATE TRIGGER trg_customer_price_no_merged
    BEFORE INSERT OR UPDATE ON pricing.customer_price_agreement
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');

-- ---------------------------------------------------------------------------
-- B-16: Freigabegrenzen als Stammdaten je Rolle. Bewusst OHNE Seeds — es werden
-- keine Beträge erfunden; die GF pflegt die Grenzen vor dem Pilotbetrieb.
-- ---------------------------------------------------------------------------
CREATE TABLE pricing.approval_threshold (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role_code    text NOT NULL,
    scope        text NOT NULL CHECK (scope IN ('RABATT_PROZENT', 'BETRAG_ABSOLUT')),
    threshold    numeric(15, 2) NOT NULL CHECK (threshold >= 0),
    currency     char(3) NULL CHECK (currency ~ '^[A-Z]{3}$'),
    valid_from   date NOT NULL,
    valid_until  date NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK ((scope = 'BETRAG_ABSOLUT') = (currency IS NOT NULL))
);

-- ---------------------------------------------------------------------------
-- B-23: Zahlungsspiegel — das Buchhaltungssystem ist führend. Zeilen entstehen
-- ausschließlich per Import; Korrekturen erfolgen im führenden System und kommen
-- als Gegenbuchung zurück. Der Spiegel ist append-only.
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.payment (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id          uuid NOT NULL REFERENCES invoicing.invoice (id),
    payment_type        text NOT NULL CHECK (payment_type IN
                        ('ZAHLUNG', 'TEILZAHLUNG', 'UEBERZAHLUNG',
                         'RUECKERSTATTUNG', 'STORNO_BUCHUNG')),
    amount              numeric(15, 2) NOT NULL CHECK (amount <> 0),
    currency            char(3) NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$'),
    paid_at             date NOT NULL,
    -- Referenz und Quelle im führenden Buchhaltungssystem (idempotenter Import)
    import_source       text NOT NULL CHECK (btrim(import_source) <> ''),
    external_reference  text NOT NULL CHECK (btrim(external_reference) <> ''),
    imported_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (import_source, external_reference)
);

CREATE FUNCTION invoicing.check_payment_invoice() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status FROM invoicing.invoice WHERE id = NEW.invoice_id;
    IF v_status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
        RAISE EXCEPTION
            'Zahlung: Rechnung % ist nicht veröffentlicht (B-23)', NEW.invoice_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_payment_invoice
    BEFORE INSERT ON invoicing.payment
    FOR EACH ROW EXECUTE FUNCTION invoicing.check_payment_invoice();

CREATE TRIGGER trg_payment_append_only
    BEFORE UPDATE OR DELETE ON invoicing.payment
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_payment_no_truncate
    BEFORE TRUNCATE ON invoicing.payment
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON invoicing.payment FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- B-22: Mahnstufen als Stammdaten (Startwerte der Fristen sind beschlossen;
-- Gebühren und Verzugszinsen bleiben NULL bis zur STB-/GF-Bestätigung —
-- Vorbehalts-Checkliste Teil C).
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.dunning_level (
    level           integer PRIMARY KEY CHECK (level > 0),
    label           text NOT NULL,
    days_after_due  integer NOT NULL CHECK (days_after_due >= 0),
    fee             numeric(15, 2) NULL,
    interest_note   text NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_dunning_level_updated_at
    BEFORE UPDATE ON invoicing.dunning_level
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

INSERT INTO invoicing.dunning_level (level, label, days_after_due, fee, interest_note) VALUES
    (1, 'Zahlungserinnerung', 7,  NULL, 'gebührenfrei'),
    (2, 'Mahnung 1',          21, NULL, 'Gebühr/Zinsen nach gesetzlicher Regelung — STB-Vorbehalt (B-22)'),
    (3, 'Mahnung 2',          35, NULL, 'Gebühr/Zinsen nach gesetzlicher Regelung — STB-Vorbehalt (B-22)');

CREATE TABLE invoicing.dunning_notice (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id   uuid NOT NULL REFERENCES invoicing.invoice (id),
    level        integer NOT NULL REFERENCES invoicing.dunning_level (level),
    issued_at    date NOT NULL,
    document_id  uuid NULL REFERENCES content.document (id),
    note         text NULL,
    created_by   uuid NOT NULL REFERENCES security.app_user (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (invoice_id, level)
);

-- Mahnungen nur auf veröffentlichte, fällige Rechnungen; Stufen lückenlos aufsteigend
CREATE FUNCTION invoicing.check_dunning_notice() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_invoice invoicing.invoice%ROWTYPE;
    v_max     integer;
BEGIN
    SELECT * INTO v_invoice FROM invoicing.invoice WHERE id = NEW.invoice_id FOR SHARE;
    IF v_invoice.status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
        RAISE EXCEPTION 'Mahnung: Rechnung % ist nicht veröffentlicht (B-22)', NEW.invoice_id;
    END IF;
    IF v_invoice.due_date IS NULL OR NEW.issued_at <= v_invoice.due_date THEN
        RAISE EXCEPTION 'Mahnung: Rechnung % ist zum % nicht fällig (B-22)', NEW.invoice_id, NEW.issued_at;
    END IF;
    SELECT coalesce(max(level), 0) INTO v_max
    FROM invoicing.dunning_notice WHERE invoice_id = NEW.invoice_id;
    IF NEW.level <> v_max + 1 THEN
        RAISE EXCEPTION
            'Mahnung: Stufe % ist nicht die nächste Stufe (erwartet %) — B-22', NEW.level, v_max + 1;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_dunning_notice_check
    BEFORE INSERT ON invoicing.dunning_notice
    FOR EACH ROW EXECUTE FUNCTION invoicing.check_dunning_notice();

CREATE TRIGGER trg_dunning_notice_append_only
    BEFORE UPDATE OR DELETE ON invoicing.dunning_notice
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_dunning_notice_no_truncate
    BEFORE TRUNCATE ON invoicing.dunning_notice
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON invoicing.dunning_notice FROM PUBLIC;

-- Stammdaten-Audit
CREATE TRIGGER trg_price_list_audit AFTER UPDATE ON pricing.price_list
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_price_list_item_audit AFTER UPDATE ON pricing.price_list_item
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_customer_price_audit AFTER UPDATE ON pricing.customer_price_agreement
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_approval_threshold_audit AFTER UPDATE ON pricing.approval_threshold
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
-- dunning_level und markup_group besitzen keine uuid-id (natürlicher Schlüssel);
-- der generische Audit-Trigger ist dort nicht anwendbar — Änderungen laufen über
-- Migrationen bzw. werden mit dem Rechtemodell (B-36) abgesichert.

COMMIT;

-- Rückwärtsstrategie: DROP der Objekte, nur solange keine Fachdaten entstanden sind.
-- Zahlungs- und Mahnzeilen werden niemals rückwärts migriert.
