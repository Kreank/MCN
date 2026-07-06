-- Migration 0033: Kalkulations-Grundlagen für den Dokumentenbuilder
-- Beschlüsse vom 2026-07-05 (4 Builder-Grundsatzentscheidungen, HERO-Vorlage):
--   1. Rubriken als Strukturelemente mit interner Auswertung je Rubrik
--   2. Formelbasierte VK-Gruppen; Positionen frieren EK/VK/Gruppe beim Einfügen ein
--   3. Leistungen als Stücklisten (Material + Lohngruppen)
--   4. Drei-Schichten-Trennung (Kunden-PDF / interne Kalkulation / Kalk-Snapshot)
-- Grundsatz: Geldwerte NUMERIC; Preise im Beleg sind Snapshots (Historisierung),
-- Aktualisierung nur explizit über die API (zwei Modi, HERO-Vorbild).

BEGIN;

-- ---------------------------------------------------------------------------
-- Lohngruppen (Beschluss 3): Stundensätze für Leistungs-Stücklisten.
-- 'MASCHINE' bildet Maschinen-/Gerätekosten ab (HERO nutzt dafür Lohngruppen).
-- ---------------------------------------------------------------------------
CREATE TABLE pricing.wage_group (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    kind         text NOT NULL DEFAULT 'LOHN' CHECK (kind IN ('LOHN', 'MASCHINE')),
    hourly_rate  numeric(12,2) NOT NULL CHECK (hourly_rate >= 0),
    status       text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    version      integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- VK-Gruppen (Beschluss 2): Formel = Basis (EK/Listenpreis) + Auf-/Abschlag
-- in Prozent ODER Betrag (genau eines).
-- ---------------------------------------------------------------------------
CREATE TABLE pricing.sale_price_group (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    calc_basis      text NOT NULL DEFAULT 'EK' CHECK (calc_basis IN ('EK', 'LISTENPREIS')),
    operator        text NOT NULL DEFAULT 'AUFSCHLAG' CHECK (operator IN ('AUFSCHLAG', 'ABSCHLAG')),
    percent_change  numeric(9,3) CHECK (percent_change >= 0),
    amount_change   numeric(12,2) CHECK (amount_change >= 0),
    CHECK ((percent_change IS NULL) <> (amount_change IS NULL)),
    status          text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    version         integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Listenpreis am Artikel (HERO: EK, Listenpreis, VK-Varianten)
ALTER TABLE pricing.article
    ADD COLUMN list_price numeric(12,2) CHECK (list_price >= 0);

-- VK-Varianten je Artikel: Gruppe ODER Festpreis; genau eine Standard-Variante
CREATE TABLE pricing.article_sale_price (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id           uuid NOT NULL REFERENCES pricing.article (id),
    label                text NOT NULL DEFAULT 'Standard' CHECK (btrim(label) <> ''),
    sale_price_group_id  uuid REFERENCES pricing.sale_price_group (id),
    fixed_price          numeric(12,2) CHECK (fixed_price >= 0),
    CHECK ((sale_price_group_id IS NULL) <> (fixed_price IS NULL)),
    is_standard          boolean NOT NULL DEFAULT false,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (article_id, label)
);
CREATE UNIQUE INDEX uq_article_sale_price_standard
    ON pricing.article_sale_price (article_id) WHERE is_standard;

-- ---------------------------------------------------------------------------
-- Leistungen (Beschluss 3): Stückliste aus Material-Artikeln und Lohnanteilen.
-- ---------------------------------------------------------------------------
CREATE TABLE pricing.assembly (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assembly_number  text NOT NULL UNIQUE CHECK (btrim(assembly_number) <> ''),
    name             text NOT NULL CHECK (btrim(name) <> ''),
    internal_name    text,
    unit             text NOT NULL CHECK (btrim(unit) <> ''),
    description      text,
    status           text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    version          integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pricing.assembly_component (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assembly_id    uuid NOT NULL REFERENCES pricing.assembly (id),
    position       integer NOT NULL DEFAULT 1 CHECK (position > 0),
    article_id     uuid REFERENCES pricing.article (id),
    wage_group_id  uuid REFERENCES pricing.wage_group (id),
    quantity       numeric(12,3) CHECK (quantity > 0),
    minutes        numeric(9,2) CHECK (minutes > 0),
    note           text,
    -- Material braucht Menge, Lohn braucht Minuten — nie beides
    CHECK ((article_id IS NOT NULL AND quantity IS NOT NULL
            AND wage_group_id IS NULL AND minutes IS NULL)
        OR (wage_group_id IS NOT NULL AND minutes IS NOT NULL
            AND article_id IS NULL AND quantity IS NULL)),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Rubriken (Beschluss 1): Strukturelemente je Beleg (Angebot ODER Rechnung).
-- Kunden-PDF zeigt Zwischensummen; die interne Auswertung (EK/Aufschlag/Gewinn
-- je Rubrik) rechnet die API aus den eingefrorenen Positionswerten.
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.beleg_rubrik (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id         uuid REFERENCES invoicing.quote (id),
    invoice_id       uuid REFERENCES invoicing.invoice (id),
    CHECK ((quote_id IS NULL) <> (invoice_id IS NULL)),
    position_number  integer NOT NULL CHECK (position_number > 0),
    title            text NOT NULL CHECK (btrim(title) <> ''),
    description      text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_beleg_rubrik_quote
    ON invoicing.beleg_rubrik (quote_id, position_number) WHERE quote_id IS NOT NULL;
CREATE UNIQUE INDEX uq_beleg_rubrik_invoice
    ON invoicing.beleg_rubrik (invoice_id, position_number) WHERE invoice_id IS NOT NULL;

-- Rubriken folgen der Beleg-Einfrierung (B-30/B-21): nur im Entwurfsfenster änderbar
CREATE FUNCTION invoicing.protect_beleg_rubrik() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_quote   uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.quote_id ELSE NEW.quote_id END;
    v_invoice uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;
    v_status  text;
BEGIN
    IF v_quote IS NOT NULL THEN
        SELECT status INTO v_status FROM invoicing.quote WHERE id = v_quote FOR SHARE;
        IF v_status NOT IN ('ENTWURF', 'INTERN_GEPRUEFT', 'FREIGEGEBEN') THEN
            RAISE EXCEPTION
                'Angebot %: Rubriken sind nach Versand unveränderlich (B-30)', v_quote;
        END IF;
    ELSE
        SELECT status INTO v_status FROM invoicing.invoice WHERE id = v_invoice FOR SHARE;
        IF v_status <> 'ENTWURF' THEN
            RAISE EXCEPTION
                'Rechnung %: Rubriken sind nach Veröffentlichung unveränderlich (B-21)', v_invoice;
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
CREATE TRIGGER trg_beleg_rubrik_protect
    BEFORE INSERT OR UPDATE OR DELETE ON invoicing.beleg_rubrik
    FOR EACH ROW EXECUTE FUNCTION invoicing.protect_beleg_rubrik();

-- ---------------------------------------------------------------------------
-- Positions-Erweiterung (Beschlüsse 1+2): Rubrik-Zuordnung und eingefrorene
-- Kalkulationswerte. unit_cost = EK-Snapshot, markup_percent = Aufschlag-
-- Snapshot (darf negativ sein = bewusster Verlust), sale_price_group_id =
-- verwendete Formel, source_* = Herkunft aus Artikelstamm/Leistung.
-- Die bestehenden Einfrier-Trigger decken die neuen Spalten automatisch ab.
-- ---------------------------------------------------------------------------
ALTER TABLE invoicing.quote_line
    ADD COLUMN rubrik_id uuid REFERENCES invoicing.beleg_rubrik (id),
    ADD COLUMN unit_cost numeric(12,2) CHECK (unit_cost >= 0),
    ADD COLUMN markup_percent numeric(9,3),
    ADD COLUMN sale_price_group_id uuid REFERENCES pricing.sale_price_group (id),
    ADD COLUMN source_article_id uuid REFERENCES pricing.article (id),
    ADD COLUMN source_assembly_id uuid REFERENCES pricing.assembly (id);

ALTER TABLE invoicing.invoice_line
    ADD COLUMN rubrik_id uuid REFERENCES invoicing.beleg_rubrik (id),
    ADD COLUMN unit_cost numeric(12,2) CHECK (unit_cost >= 0),
    ADD COLUMN markup_percent numeric(9,3),
    ADD COLUMN sale_price_group_id uuid REFERENCES pricing.sale_price_group (id),
    ADD COLUMN source_article_id uuid REFERENCES pricing.article (id),
    ADD COLUMN source_assembly_id uuid REFERENCES pricing.assembly (id);

-- Rubrik muss zum selben Beleg gehören wie die Position
CREATE FUNCTION invoicing.check_line_rubrik() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_ok boolean;
BEGIN
    IF NEW.rubrik_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'quote_line' THEN
        SELECT (r.quote_id = NEW.quote_id) INTO v_ok
        FROM invoicing.beleg_rubrik r WHERE r.id = NEW.rubrik_id;
    ELSE
        SELECT (r.invoice_id = NEW.invoice_id) INTO v_ok
        FROM invoicing.beleg_rubrik r WHERE r.id = NEW.rubrik_id;
    END IF;
    IF v_ok IS DISTINCT FROM true THEN
        RAISE EXCEPTION
            'Position %: Rubrik % gehört nicht zu diesem Beleg', NEW.id, NEW.rubrik_id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_quote_line_rubrik
    BEFORE INSERT OR UPDATE OF rubrik_id ON invoicing.quote_line
    FOR EACH ROW EXECUTE FUNCTION invoicing.check_line_rubrik();
CREATE TRIGGER trg_invoice_line_rubrik
    BEFORE INSERT OR UPDATE OF rubrik_id ON invoicing.invoice_line
    FOR EACH ROW EXECUTE FUNCTION invoicing.check_line_rubrik();

-- ---------------------------------------------------------------------------
-- Pflege-Standard: updated_at, Audit, kein TRUNCATE auf Stammdaten
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_wage_group_updated_at BEFORE UPDATE ON pricing.wage_group
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_sale_price_group_updated_at BEFORE UPDATE ON pricing.sale_price_group
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_article_sale_price_updated_at BEFORE UPDATE ON pricing.article_sale_price
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_assembly_updated_at BEFORE UPDATE ON pricing.assembly
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_assembly_component_updated_at BEFORE UPDATE ON pricing.assembly_component
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_beleg_rubrik_updated_at BEFORE UPDATE ON invoicing.beleg_rubrik
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TRIGGER trg_wage_group_audit AFTER UPDATE ON pricing.wage_group
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_sale_price_group_audit AFTER UPDATE ON pricing.sale_price_group
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_article_sale_price_audit AFTER UPDATE ON pricing.article_sale_price
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_assembly_audit AFTER UPDATE ON pricing.assembly
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_assembly_component_audit AFTER UPDATE ON pricing.assembly_component
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_beleg_rubrik_audit AFTER UPDATE ON invoicing.beleg_rubrik
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_article_sale_price_delete_audit AFTER DELETE ON pricing.article_sale_price
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_assembly_component_delete_audit AFTER DELETE ON pricing.assembly_component
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_beleg_rubrik_delete_audit AFTER DELETE ON invoicing.beleg_rubrik
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();

-- Lohn-/VK-Gruppen und Leistungen: deaktivieren statt löschen (FKs schützen
-- zusätzlich); TRUNCATE bleibt überall verboten
CREATE TRIGGER trg_wage_group_no_delete BEFORE DELETE ON pricing.wage_group
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_sale_price_group_no_delete BEFORE DELETE ON pricing.sale_price_group
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_assembly_no_delete BEFORE DELETE ON pricing.assembly
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_wage_group_no_truncate BEFORE TRUNCATE ON pricing.wage_group
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_sale_price_group_no_truncate BEFORE TRUNCATE ON pricing.sale_price_group
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_article_sale_price_no_truncate BEFORE TRUNCATE ON pricing.article_sale_price
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_assembly_no_truncate BEFORE TRUNCATE ON pricing.assembly
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_assembly_component_no_truncate BEFORE TRUNCATE ON pricing.assembly_component
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_beleg_rubrik_no_truncate BEFORE TRUNCATE ON invoicing.beleg_rubrik
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE ON pricing.wage_group, pricing.sale_price_group, pricing.assembly FROM PUBLIC;
REVOKE TRUNCATE ON pricing.wage_group, pricing.sale_price_group,
    pricing.article_sale_price, pricing.assembly, pricing.assembly_component,
    invoicing.beleg_rubrik FROM PUBLIC;

COMMIT;

-- Rückwärtsstrategie: neue Tabellen/Trigger droppen, Spalten der *_line- und
-- article-Tabellen entfernen — nur solange keine Kalkulationsdaten entstanden.
