-- Migration 0019: Rechnung (invoice) mit Beteiligten, Positionen und Veröffentlichung
-- Beschlüsse: B-17 (Rechnungsarten), B-13/B-14 (RE/GS fortlaufend, erst bei Veröffentlichung),
--             B-19 (Rundung), B-21/B-30 (unveränderlich, Korrektur nur Folgebeleg),
--             B-08 (abrechnungsbereit), A-24/A-27/A-29 (Schuldner, getrennte Rechnungen)

BEGIN;

CREATE TABLE invoicing.invoice (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- B-13/B-14: RE/GS-Nummer erst bei Veröffentlichung, fortlaufend
    invoice_number        text NULL UNIQUE
                          CHECK (invoice_number IS NULL OR
                                 invoice_number ~ '^(RE|GS)-[0-9]{4}-[0-9]{6,}$'),
    invoice_type          text NOT NULL CHECK (invoice_type IN
                          ('RECHNUNG', 'ABSCHLAGSRECHNUNG', 'TEILRECHNUNG',
                           'SCHLUSSRECHNUNG', 'GUTSCHRIFT', 'STORNO')),
    work_order_id         uuid NULL REFERENCES workflow.work_order (id),
    project_id            uuid NULL REFERENCES workflow.project (id),
    property_id           uuid NOT NULL REFERENCES property.property (id),
    -- B-21: Gutschrift/Storno referenzieren den Ursprungsbeleg
    reference_invoice_id  uuid NULL REFERENCES invoicing.invoice (id),
    status                text NOT NULL DEFAULT 'ENTWURF'
                          CHECK (status IN ('ENTWURF', 'VEROEFFENTLICHT')),
    invoice_date          date NULL,
    due_date              date NULL,
    currency              char(3) NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$'),
    net_total             numeric(15, 2) NULL,
    tax_total             numeric(15, 2) NULL,
    gross_total           numeric(15, 2) NULL,
    billing_snapshot      jsonb NULL,
    content_hash          text NULL,
    published_at          timestamptz NULL,
    version               integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (invoice_type NOT IN ('GUTSCHRIFT', 'STORNO') OR reference_invoice_id IS NOT NULL),
    CHECK (reference_invoice_id IS NULL OR reference_invoice_id <> id),
    CHECK (due_date IS NULL OR invoice_date IS NULL OR due_date >= invoice_date),
    -- P3-01: Belegnummer existiert erst ab Veröffentlichung (B-13/B-14)
    CHECK (status = 'VEROEFFENTLICHT' OR invoice_number IS NULL),
    -- P3-01: Kreis passt zur Belegart (GS nur für Gutschrift/Storno)
    CHECK (invoice_number IS NULL OR
           ((invoice_type IN ('GUTSCHRIFT', 'STORNO')) = (invoice_number LIKE 'GS-%'))),
    -- P3-12: Beleg und Auftrag gehören zur selben Liegenschaft
    FOREIGN KEY (work_order_id, property_id)
        REFERENCES workflow.work_order (id, property_id)
);

CREATE TRIGGER trg_invoice_updated_at
    BEFORE UPDATE ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TRIGGER trg_invoice_initial_status
    BEFORE INSERT ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('ENTWURF');

CREATE TRIGGER trg_invoice_status_log
    AFTER INSERT OR UPDATE OF status ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION workflow.log_status_change('invoice');

-- ---------------------------------------------------------------------------
-- invoicing.invoice_party — strukturierte Beteiligte (A-27/A-29)
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.invoice_party (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id          uuid NOT NULL REFERENCES invoicing.invoice (id),
    party_id            uuid NOT NULL REFERENCES identity.party (id),
    role                text NOT NULL CHECK (role IN
                        ('INVOICE_DEBTOR', 'INVOICE_RECIPIENT', 'REPRESENTATIVE', 'COST_BEARER')),
    is_primary          boolean NOT NULL DEFAULT false,
    allocation_percent  numeric(7, 4) NULL
                        CHECK (allocation_percent > 0 AND allocation_percent <= 100),
    -- A-29: Gesamtschuld nur mit dokumentierter Grundlage
    liability_group     text NULL,
    liability_basis     text NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (liability_group IS NULL OR liability_basis IS NOT NULL),
    UNIQUE (invoice_id, role, party_id)
);

CREATE UNIQUE INDEX uq_invoice_party_primary
    ON invoicing.invoice_party (invoice_id, role)
    WHERE is_primary;

CREATE TRIGGER trg_invoice_party_no_merged
    BEFORE INSERT OR UPDATE ON invoicing.invoice_party
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');

-- ---------------------------------------------------------------------------
-- invoicing.invoice_line — identische Positionsregeln wie quote_line (B-19/B-24)
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.invoice_line (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id        uuid NOT NULL REFERENCES invoicing.invoice (id),
    position_number   integer NOT NULL CHECK (position_number > 0),
    line_type         text NOT NULL CHECK (line_type IN
                      ('MATERIAL', 'ARBEITSZEIT', 'PAUSCHALE', 'FREMDLEISTUNG',
                       'FAHRT', 'ZUSCHLAG', 'TEXT', 'ZWISCHENSUMME')),
    description       text NOT NULL CHECK (btrim(description) <> ''),
    quantity          numeric(15, 3) NULL CHECK (quantity > 0),
    unit              text NULL,
    unit_price        numeric(15, 2) NULL,
    discount_percent  numeric(7, 4) NULL CHECK (discount_percent >= 0 AND discount_percent < 100),
    tax_code          text NULL REFERENCES invoicing.tax_code (code),
    tax_rate_percent  numeric(5, 2) NULL,
    net_amount        numeric(15, 2) NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (invoice_id, position_number),
    CHECK (
        (line_type IN ('TEXT', 'ZWISCHENSUMME')
         AND quantity IS NULL AND unit_price IS NULL AND net_amount IS NULL
         AND tax_code IS NULL AND tax_rate_percent IS NULL AND discount_percent IS NULL)
        OR
        (line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
         AND quantity IS NOT NULL AND unit_price IS NOT NULL AND net_amount IS NOT NULL
         AND tax_code IS NOT NULL AND tax_rate_percent IS NOT NULL)
    ),
    CHECK (net_amount IS NULL OR
           net_amount = round(quantity * unit_price * (1 - coalesce(discount_percent, 0) / 100), 2))
);

CREATE TRIGGER trg_invoice_line_updated_at
    BEFORE UPDATE ON invoicing.invoice_line
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- Veröffentlichung: Nummervergabe im BEFORE-Trigger, fachliche Tore als
-- verzögerter Constraint-Trigger.
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoicing.prepare_invoice_publish() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'VEROEFFENTLICHT' AND OLD.status = 'ENTWURF' THEN
        IF NEW.billing_snapshot IS NULL OR NEW.content_hash IS NULL THEN
            RAISE EXCEPTION
                'Rechnung %: Veröffentlichung ohne Snapshot und Inhalts-Hash ist unzulässig (B-21/B-30)',
                NEW.id;
        END IF;
        -- P3-01: Nummern werden ausschließlich hier vergeben, nie übernommen
        IF NEW.invoice_number IS NOT NULL THEN
            RAISE EXCEPTION
                'Rechnung %: Belegnummern werden ausschließlich bei Veröffentlichung vergeben (B-13/B-14)',
                NEW.id;
        END IF;
        NEW.invoice_number := workflow.next_number(
            CASE WHEN NEW.invoice_type IN ('GUTSCHRIFT', 'STORNO') THEN 'GS' ELSE 'RE' END);
        NEW.published_at := now();
        NEW.invoice_date := coalesce(NEW.invoice_date, (now() AT TIME ZONE 'UTC')::date);
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_invoice_prepare_publish
    BEFORE UPDATE OF status ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION invoicing.prepare_invoice_publish();

-- B-21: Eine veröffentlichte Rechnung ist vollständig unveränderlich.
CREATE FUNCTION invoicing.freeze_published_invoice() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'VEROEFFENTLICHT' THEN
        RAISE EXCEPTION
            'Rechnung % ist veröffentlicht und unveränderlich (B-21); Korrektur nur über Gutschrift/Storno',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_invoice_freeze
    BEFORE UPDATE ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION invoicing.freeze_published_invoice();

-- Summenprüfung (B-19) analog zum Angebot
CREATE FUNCTION invoicing.assert_invoice_totals(p_invoice_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_invoice invoicing.invoice%ROWTYPE;
    v_net     numeric;
    v_tax     numeric;
    v_lines   integer;
BEGIN
    SELECT * INTO v_invoice FROM invoicing.invoice WHERE id = p_invoice_id FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;

    SELECT count(*) FILTER (WHERE line_type NOT IN ('TEXT', 'ZWISCHENSUMME')),
           coalesce(sum(net_amount) FILTER (WHERE line_type NOT IN ('TEXT', 'ZWISCHENSUMME')), 0)
    INTO v_lines, v_net
    FROM invoicing.invoice_line WHERE invoice_id = p_invoice_id;

    SELECT coalesce(sum(group_tax), 0) INTO v_tax
    FROM (
        SELECT round(sum(net_amount) * tax_rate_percent / 100, 2) AS group_tax
        FROM invoicing.invoice_line
        WHERE invoice_id = p_invoice_id AND line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
        GROUP BY tax_code, tax_rate_percent
    ) g;

    IF v_lines = 0 THEN
        RAISE EXCEPTION 'Rechnung %: mindestens eine Betragsposition erforderlich', p_invoice_id;
    END IF;

    -- P3-05: kopierter Steuersatz muss dem am Belegdatum gültigen Steuercode entsprechen
    PERFORM 1
    FROM invoicing.invoice_line l
    JOIN invoicing.tax_code t ON t.code = l.tax_code
    WHERE l.invoice_id = p_invoice_id AND l.line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
      AND (l.tax_rate_percent <> t.rate_percent
           OR coalesce(v_invoice.invoice_date, (now() AT TIME ZONE 'UTC')::date) < t.valid_from
           OR (t.valid_until IS NOT NULL
               AND coalesce(v_invoice.invoice_date, (now() AT TIME ZONE 'UTC')::date) >= t.valid_until))
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Rechnung %: Positions-Steuersatz weicht vom gültigen Steuercode ab (B-18/B-19/P3-05)', p_invoice_id;
    END IF;
    IF v_invoice.net_total IS DISTINCT FROM v_net
       OR v_invoice.tax_total IS DISTINCT FROM v_tax
       OR v_invoice.gross_total IS DISTINCT FROM (v_net + v_tax) THEN
        RAISE EXCEPTION
            'Rechnung %: Summen inkonsistent (erwartet Netto %, Steuer %, Brutto %) — B-19',
            p_invoice_id, v_net, v_tax, v_net + v_tax;
    END IF;
END;
$$;

-- Veröffentlichungstor (deferred): Summen, Beteiligte, Auftragsbezug, Folgebelege
CREATE FUNCTION invoicing.check_invoice_publish() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_debtors            integer;
    v_debtors_no_basis   integer;
    v_primary_recipients integer;
    v_order_status       text;
    v_bad_debtors        integer;
    v_ref_status         text;
BEGIN
    IF NEW.status <> 'VEROEFFENTLICHT' THEN
        RETURN NULL;
    END IF;

    PERFORM invoicing.assert_invoice_totals(NEW.id);

    SELECT count(*) FILTER (WHERE role = 'INVOICE_DEBTOR'),
           count(*) FILTER (WHERE role = 'INVOICE_DEBTOR' AND liability_basis IS NULL),
           count(*) FILTER (WHERE role = 'INVOICE_RECIPIENT' AND is_primary)
    INTO v_debtors, v_debtors_no_basis, v_primary_recipients
    FROM invoicing.invoice_party WHERE invoice_id = NEW.id;

    IF v_debtors < 1 THEN
        RAISE EXCEPTION
            'Rechnung %: Veröffentlichung ohne Rechnungsschuldner ist unzulässig (A-27)', NEW.id;
    END IF;
    -- A-24/A-29: getrennte Rechnungen als Standard; mehrere Schuldner nur mit
    -- dokumentierter Grundlage je Schuldner
    IF v_debtors > 1 AND v_debtors_no_basis > 0 THEN
        RAISE EXCEPTION
            'Rechnung %: Mehrere Schuldner erfordern eine dokumentierte Grundlage je Schuldner (A-29); Standard sind getrennte Rechnungen (A-24)',
            NEW.id;
    END IF;
    IF v_primary_recipients <> 1 THEN
        RAISE EXCEPTION
            'Rechnung %: Genau ein primärer Rechnungsempfänger ist erforderlich (A-28)', NEW.id;
    END IF;

    IF NEW.invoice_type IN ('GUTSCHRIFT', 'STORNO') THEN
        SELECT status INTO v_ref_status
        FROM invoicing.invoice WHERE id = NEW.reference_invoice_id;
        IF v_ref_status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
            RAISE EXCEPTION
                'Rechnung %: Gutschrift/Storno erfordert einen veröffentlichten Ursprungsbeleg (B-21)', NEW.id;
        END IF;
        -- P3-06 (A-27/B-21): Schuldner des Korrekturbelegs müssen Schuldner des
        -- Ursprungsbelegs sein — der Korrekturbeleg korrigiert genau diesen Beleg.
        SELECT count(*) INTO v_bad_debtors
        FROM invoicing.invoice_party ip
        WHERE ip.invoice_id = NEW.id AND ip.role = 'INVOICE_DEBTOR'
          AND NOT EXISTS (
              SELECT 1 FROM invoicing.invoice_party rp
              WHERE rp.invoice_id = NEW.reference_invoice_id
                AND rp.role = 'INVOICE_DEBTOR' AND rp.party_id = ip.party_id);
        IF v_bad_debtors > 0 THEN
            RAISE EXCEPTION
                'Rechnung %: % Schuldner sind keine Schuldner des Ursprungsbelegs (A-27/B-21/P3-06)',
                NEW.id, v_bad_debtors;
        END IF;
    ELSE
        -- B-08: Rechnungen entstehen aus kaufmännisch geprüften Aufträgen
        IF NEW.work_order_id IS NULL THEN
            RAISE EXCEPTION
                'Rechnung %: Rechnungsarten außer Gutschrift/Storno erfordern einen Auftrag (B-08)', NEW.id;
        END IF;
        SELECT status INTO v_order_status
        FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;
        IF v_order_status NOT IN ('KAUFMAENNISCH_GEPRUEFT', 'ABGERECHNET') THEN
            RAISE EXCEPTION
                'Rechnung %: Auftrag muss kaufmännisch geprüft sein (B-08), ist %', NEW.id, v_order_status;
        END IF;
        -- A-27: Rechnungsschuldner müssen als INVOICE_DEBTOR am Auftrag bestätigt sein
        SELECT count(*) INTO v_bad_debtors
        FROM invoicing.invoice_party ip
        WHERE ip.invoice_id = NEW.id AND ip.role = 'INVOICE_DEBTOR'
          AND NOT EXISTS (
              SELECT 1 FROM workflow.work_order_party wp
              WHERE wp.work_order_id = NEW.work_order_id
                AND wp.role = 'INVOICE_DEBTOR' AND wp.party_id = ip.party_id);
        IF v_bad_debtors > 0 THEN
            RAISE EXCEPTION
                'Rechnung %: % Schuldner sind nicht als Rechnungsschuldner des Auftrags bestätigt (A-27)',
                NEW.id, v_bad_debtors;
        END IF;
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_invoice_publish_gate
    AFTER UPDATE OF status ON invoicing.invoice
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION invoicing.check_invoice_publish();

-- Positionen und Beteiligte sind nur im Entwurf veränderbar
CREATE FUNCTION invoicing.protect_invoice_children() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status  text;
    v_invoice uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;
BEGIN
    -- P3-02: FOR SHARE serialisiert Zeilenänderungen gegen eine laufende Veröffentlichung
    SELECT status INTO v_status FROM invoicing.invoice WHERE id = v_invoice FOR SHARE;
    IF v_status <> 'ENTWURF' THEN
        RAISE EXCEPTION
            'Rechnung %: Positionen und Beteiligte sind nach Veröffentlichung unveränderlich (B-21)',
            v_invoice;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_invoice_line_protect
    BEFORE INSERT OR UPDATE OR DELETE ON invoicing.invoice_line
    FOR EACH ROW EXECUTE FUNCTION invoicing.protect_invoice_children();

CREATE TRIGGER trg_invoice_party_protect
    BEFORE INSERT OR UPDATE OR DELETE ON invoicing.invoice_party
    FOR EACH ROW EXECUTE FUNCTION invoicing.protect_invoice_children();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen, nur solange keine
-- Belege entstanden sind. Veröffentlichte Belege werden niemals rückwärts migriert.
