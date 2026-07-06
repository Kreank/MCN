-- Migration 0018: Angebot (quote) mit Positionsstruktur und Veröffentlichungslogik
-- Beschlüsse: B-15 (Status), B-14 (Nummer erst bei Versand), B-19 (Rundung),
--             B-24 (Positionsarten), B-30 (unveränderlich nach Veröffentlichung)

BEGIN;

CREATE TABLE invoicing.quote (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- B-14: Belegnummer erst bei Versand (AN-Kreis)
    quote_number          text NULL UNIQUE
                          CHECK (quote_number IS NULL OR quote_number ~ '^AN-[0-9]{4}-[0-9]{6,}$'),
    work_order_id         uuid NULL REFERENCES workflow.work_order (id),
    project_id            uuid NULL REFERENCES workflow.project (id),
    property_id           uuid NOT NULL REFERENCES property.property (id),
    title                 text NOT NULL CHECK (btrim(title) <> ''),
    status                text NOT NULL DEFAULT 'ENTWURF'
                          CHECK (status IN ('ENTWURF', 'INTERN_GEPRUEFT', 'FREIGEGEBEN',
                          'VERSENDET', 'ANGENOMMEN', 'ABGELEHNT', 'ABGELAUFEN', 'ERSETZT')),
    quote_date            date NULL,
    valid_until_date      date NULL,
    currency              char(3) NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$'),
    net_total             numeric(15, 2) NULL,
    tax_total             numeric(15, 2) NULL,
    gross_total           numeric(15, 2) NULL,
    -- B-30: bei Versand unveränderlicher Snapshot der ausgegebenen Angaben
    billing_snapshot      jsonb NULL,
    content_hash          text NULL,
    sent_at               timestamptz NULL,
    replaced_by_quote_id  uuid NULL REFERENCES invoicing.quote (id),
    version               integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'ERSETZT' OR replaced_by_quote_id IS NOT NULL),
    CHECK (replaced_by_quote_id IS NULL OR replaced_by_quote_id <> id),
    -- P3-01: Belegnummer existiert erst ab Versand (B-13/B-14)
    CHECK (status IN ('VERSENDET', 'ANGENOMMEN', 'ABGELEHNT', 'ABGELAUFEN', 'ERSETZT')
           OR quote_number IS NULL),
    -- P3-12: Beleg und Auftrag gehören zur selben Liegenschaft
    FOREIGN KEY (work_order_id, property_id)
        REFERENCES workflow.work_order (id, property_id)
);

CREATE TRIGGER trg_quote_updated_at
    BEFORE UPDATE ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TRIGGER trg_quote_initial_status
    BEFORE INSERT ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('ENTWURF');

CREATE TRIGGER trg_quote_status_validate
    BEFORE UPDATE OF status ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION workflow.validate_status_change('quote');

CREATE TRIGGER trg_quote_status_log
    AFTER INSERT OR UPDATE OF status ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION workflow.log_status_change('quote');

-- ---------------------------------------------------------------------------
-- invoicing.quote_line — Positionsarten nach B-24, Rundung nach B-19
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.quote_line (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id          uuid NOT NULL REFERENCES invoicing.quote (id),
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
    UNIQUE (quote_id, position_number),
    -- Text- und Zwischensummenzeilen tragen keine Beträge; Betragszeilen sind vollständig
    CHECK (
        (line_type IN ('TEXT', 'ZWISCHENSUMME')
         AND quantity IS NULL AND unit_price IS NULL AND net_amount IS NULL
         AND tax_code IS NULL AND tax_rate_percent IS NULL AND discount_percent IS NULL)
        OR
        (line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
         AND quantity IS NOT NULL AND unit_price IS NOT NULL AND net_amount IS NOT NULL
         AND tax_code IS NOT NULL AND tax_rate_percent IS NOT NULL)
    ),
    -- B-19: kaufmännische Rundung je Position auf 2 Nachkommastellen
    CHECK (net_amount IS NULL OR
           net_amount = round(quantity * unit_price * (1 - coalesce(discount_percent, 0) / 100), 2))
);

CREATE TRIGGER trg_quote_line_updated_at
    BEFORE UPDATE ON invoicing.quote_line
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- Summenprüfung nach B-19: Netto = Summe der Positionsnetto; Steuer je
-- Steuersatzgruppe auf die Belegsumme gerundet; Brutto = Netto + Steuer.
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoicing.assert_quote_totals(p_quote_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_quote     invoicing.quote%ROWTYPE;
    v_net       numeric;
    v_tax       numeric;
    v_lines     integer;
BEGIN
    SELECT * INTO v_quote FROM invoicing.quote WHERE id = p_quote_id FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;

    SELECT count(*) FILTER (WHERE line_type NOT IN ('TEXT', 'ZWISCHENSUMME')),
           coalesce(sum(net_amount) FILTER (WHERE line_type NOT IN ('TEXT', 'ZWISCHENSUMME')), 0)
    INTO v_lines, v_net
    FROM invoicing.quote_line WHERE quote_id = p_quote_id;

    SELECT coalesce(sum(group_tax), 0) INTO v_tax
    FROM (
        SELECT round(sum(net_amount) * tax_rate_percent / 100, 2) AS group_tax
        FROM invoicing.quote_line
        WHERE quote_id = p_quote_id AND line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
        GROUP BY tax_code, tax_rate_percent
    ) g;

    IF v_lines = 0 THEN
        RAISE EXCEPTION 'Angebot %: mindestens eine Betragsposition erforderlich', p_quote_id;
    END IF;

    -- P3-05: kopierter Steuersatz muss dem am Belegdatum gültigen Steuercode entsprechen
    PERFORM 1
    FROM invoicing.quote_line l
    JOIN invoicing.tax_code t ON t.code = l.tax_code
    WHERE l.quote_id = p_quote_id AND l.line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
      AND (l.tax_rate_percent <> t.rate_percent
           OR coalesce(v_quote.quote_date, (now() AT TIME ZONE 'UTC')::date) < t.valid_from
           OR (t.valid_until IS NOT NULL
               AND coalesce(v_quote.quote_date, (now() AT TIME ZONE 'UTC')::date) >= t.valid_until))
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Angebot %: Positions-Steuersatz weicht vom gültigen Steuercode ab (B-18/B-19/P3-05)', p_quote_id;
    END IF;
    IF v_quote.net_total IS DISTINCT FROM v_net
       OR v_quote.tax_total IS DISTINCT FROM v_tax
       OR v_quote.gross_total IS DISTINCT FROM (v_net + v_tax) THEN
        RAISE EXCEPTION
            'Angebot %: Summen inkonsistent (erwartet Netto %, Steuer %, Brutto %) — B-19',
            p_quote_id, v_net, v_tax, v_net + v_tax;
    END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- Versand (B-14/B-30): Nummer aus dem AN-Kreis, Snapshot- und Summenpflicht.
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoicing.prepare_quote_send() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'VERSENDET' AND OLD.status <> 'VERSENDET' THEN
        IF NEW.billing_snapshot IS NULL OR NEW.content_hash IS NULL THEN
            RAISE EXCEPTION
                'Angebot %: Versand ohne Snapshot und Inhalts-Hash ist unzulässig (B-30)', NEW.id;
        END IF;
        -- P3-01: Nummern werden ausschließlich hier vergeben, nie übernommen
        IF NEW.quote_number IS NOT NULL THEN
            RAISE EXCEPTION
                'Angebot %: Belegnummern werden ausschließlich beim Versand vergeben (B-13/B-14)', NEW.id;
        END IF;
        NEW.quote_number := workflow.next_number('AN');
        NEW.sent_at := now();
        NEW.quote_date := coalesce(NEW.quote_date, (now() AT TIME ZONE 'UTC')::date);
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_quote_prepare_send
    BEFORE UPDATE OF status ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION invoicing.prepare_quote_send();

CREATE FUNCTION invoicing.check_quote_send() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'VERSENDET' THEN
        PERFORM invoicing.assert_quote_totals(NEW.id);
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_quote_send_gate
    AFTER UPDATE OF status ON invoicing.quote
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION invoicing.check_quote_send();

-- ---------------------------------------------------------------------------
-- B-30: Ab Versand ist der Angebotsinhalt unveränderlich; zulässig bleiben nur
-- Statusfolgen (ANGENOMMEN/ABGELEHNT/ABGELAUFEN/ERSETZT) und der Nachfolgeverweis.
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoicing.freeze_sent_quote() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- P3-08: Der Nachfolgeverweis darf nur zusammen mit dem Übergang nach ERSETZT
    -- gesetzt werden und ist danach unveränderlich.
    IF NEW.replaced_by_quote_id IS DISTINCT FROM OLD.replaced_by_quote_id THEN
        IF NOT (NEW.status = 'ERSETZT' AND OLD.status <> 'ERSETZT'
                AND OLD.replaced_by_quote_id IS NULL) THEN
            RAISE EXCEPTION
                'Angebot %: Nachfolgeverweis nur beim Übergang nach ERSETZT setzbar und danach unveränderlich (B-30/P3-08)',
                OLD.id;
        END IF;
    END IF;

    IF OLD.status IN ('VERSENDET', 'ANGENOMMEN', 'ABGELEHNT', 'ABGELAUFEN', 'ERSETZT') THEN
        -- P3-09: version gehört nicht zu den Ausnahmen — nach Versand eingefroren
        IF (to_jsonb(NEW) - 'status' - 'replaced_by_quote_id' - 'updated_at')
           IS DISTINCT FROM
           (to_jsonb(OLD) - 'status' - 'replaced_by_quote_id' - 'updated_at') THEN
            RAISE EXCEPTION
                'Angebot %: Inhalt ist nach Versand unveränderlich (B-30); Ersatzangebot verwenden', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_quote_freeze
    BEFORE UPDATE ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION invoicing.freeze_sent_quote();

-- Positionen sind nur bis zum Versand veränderbar
CREATE FUNCTION invoicing.protect_quote_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
    v_quote  uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.quote_id ELSE NEW.quote_id END;
BEGIN
    -- P3-02: FOR SHARE serialisiert Zeilenänderungen gegen einen laufenden Versand
    SELECT status INTO v_status FROM invoicing.quote WHERE id = v_quote FOR SHARE;
    IF v_status NOT IN ('ENTWURF', 'INTERN_GEPRUEFT', 'FREIGEGEBEN') THEN
        RAISE EXCEPTION
            'Angebot %: Positionen sind nach Versand unveränderlich (B-30)', v_quote;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_quote_line_protect
    BEFORE INSERT OR UPDATE OR DELETE ON invoicing.quote_line
    FOR EACH ROW EXECUTE FUNCTION invoicing.protect_quote_lines();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen, nur solange keine
-- Angebote entstanden sind.
