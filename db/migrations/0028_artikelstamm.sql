-- Migration 0028: Lokaler Artikelstamm mit IDS-Connect-Fundament
-- Beschlüsse: B-25 (Preislisten, EK->VK über Aufschlagsgruppen), B-26 (KEINE
--             Bestandsführung — Artikelstamm ist Stammdaten, kein Lager),
--             REV-A-05-Muster (kollisionsfreie externe Referenzen je Quellsystem)
-- Zweck: Artikel aus Großhändler-Warenkörben (IDS-Connect) werden dauerhaft im
-- lokalen Stamm gespeichert; die Shop-Anbindung selbst ist App-Schicht.

BEGIN;

-- ---------------------------------------------------------------------------
-- pricing.article — der lokale Artikelstamm
-- ---------------------------------------------------------------------------
CREATE TABLE pricing.article (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    article_number       text NOT NULL UNIQUE CHECK (btrim(article_number) <> ''),
    description          text NOT NULL CHECK (btrim(description) <> ''),
    long_description     text NULL,
    -- GTIN/EAN: 8, 12, 13 oder 14 Ziffern; eindeutig, sofern vorhanden
    gtin                 text NULL CHECK (gtin ~ '^[0-9]{8}$|^[0-9]{12,14}$'),
    manufacturer_name    text NULL,
    manufacturer_number  text NULL,
    unit                 text NOT NULL CHECK (btrim(unit) <> ''),
    line_type            text NOT NULL DEFAULT 'MATERIAL' CHECK (line_type IN
                         ('MATERIAL', 'ARBEITSZEIT', 'PAUSCHALE', 'FREMDLEISTUNG',
                          'FAHRT', 'ZUSCHLAG')),
    product_group        text NULL,
    status               text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    version              integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_article_gtin ON pricing.article (gtin) WHERE gtin IS NOT NULL;
CREATE INDEX idx_article_manufacturer ON pricing.article (manufacturer_number)
    WHERE manufacturer_number IS NOT NULL;

CREATE TRIGGER trg_article_updated_at
    BEFORE UPDATE ON pricing.article
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_article_audit AFTER UPDATE ON pricing.article
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
-- Artikel werden deaktiviert, nicht gelöscht (Belege/Preislisten referenzieren sie)
CREATE TRIGGER trg_article_no_delete BEFORE DELETE ON pricing.article
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_article_no_truncate BEFORE TRUNCATE ON pricing.article
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON pricing.article FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- pricing.article_supplier_reference — Lieferantenbezüge (IDS-Connect u. a.)
-- Muster wie identity.party_external_reference: Quellsystem + Namespace verhindern
-- Kollisionen zwischen Großhändlern mit gleichen Artikelnummern.
-- ---------------------------------------------------------------------------
CREATE TABLE pricing.article_supplier_reference (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id               uuid NOT NULL REFERENCES pricing.article (id),
    supplier_party_id        uuid NOT NULL REFERENCES identity.party (id),
    -- z. B. 'IDS_CONNECT', 'DATANORM', 'MANUELL'
    source_system            text NOT NULL CHECK (btrim(source_system) <> ''),
    -- kollisionsfreier Geltungsbereich, z. B. Händlerkennung des IDS-Shops
    source_namespace         text NOT NULL CHECK (btrim(source_namespace) <> ''),
    supplier_article_number  text NOT NULL CHECK (btrim(supplier_article_number) <> ''),
    -- letzter bekannter Einkaufspreis (B-19: 2 Nachkommastellen); Historie über Audit
    last_purchase_price      numeric(15, 2) NULL,
    currency                 char(3) NULL CHECK (currency ~ '^[A-Z]{3}$'),
    discount_group           text NULL,
    last_imported_at         timestamptz NULL,
    valid_from               date NOT NULL,
    valid_until              date NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK ((last_purchase_price IS NULL) = (currency IS NULL)),
    -- Dieselbe Händler-Artikelnummer zeigt zeitgleich nur auf EINEN Stammartikel
    CONSTRAINT excl_supplier_ref EXCLUDE USING gist (
        source_system WITH =,
        source_namespace WITH =,
        supplier_article_number WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

CREATE INDEX idx_supplier_ref_article ON pricing.article_supplier_reference (article_id);
CREATE INDEX idx_supplier_ref_supplier ON pricing.article_supplier_reference (supplier_party_id);

CREATE TRIGGER trg_supplier_ref_updated_at
    BEFORE UPDATE ON pricing.article_supplier_reference
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_supplier_ref_audit AFTER UPDATE ON pricing.article_supplier_reference
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_supplier_ref_no_merged
    BEFORE INSERT OR UPDATE ON pricing.article_supplier_reference
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('supplier_party_id');
-- Identität einer Referenz ist unveränderlich (WF-01-/REV-A-05-Linie): Artikelbezug,
-- Lieferant, Quellsystem, Namespace und Händlernummer definieren die Referenz —
-- eine Änderung wäre eine stille Umdeutung aller historischen Importe.
-- Änderbar bleiben EK, Rabattgruppe, Importzeitpunkt und Gültigkeitsende.
CREATE FUNCTION pricing.protect_supplier_ref() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.article_id IS DISTINCT FROM OLD.article_id
       OR NEW.supplier_party_id IS DISTINCT FROM OLD.supplier_party_id
       OR NEW.source_system IS DISTINCT FROM OLD.source_system
       OR NEW.source_namespace IS DISTINCT FROM OLD.source_namespace
       OR NEW.supplier_article_number IS DISTINCT FROM OLD.supplier_article_number THEN
        RAISE EXCEPTION
            'article_supplier_reference %: Die Referenzidentität ist unveränderlich; Referenz beenden und neu anlegen (REV-A-05)',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_supplier_ref_protect
    BEFORE UPDATE ON pricing.article_supplier_reference
    FOR EACH ROW EXECUTE FUNCTION pricing.protect_supplier_ref();

-- MEDIUM-Fix (Review): EK-/Import-Historie ist nicht spurlos entfernbar —
-- Referenzen werden beendet (valid_until), nicht gelöscht (F-02-Linie).
CREATE TRIGGER trg_supplier_ref_no_delete
    BEFORE DELETE ON pricing.article_supplier_reference
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_supplier_ref_no_truncate
    BEFORE TRUNCATE ON pricing.article_supplier_reference
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON pricing.article_supplier_reference FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Preislistenpositionen erhalten einen optionalen Stammartikel-Bezug (B-25)
-- ---------------------------------------------------------------------------
ALTER TABLE pricing.price_list_item
    ADD COLUMN article_id uuid NULL REFERENCES pricing.article (id);
CREATE INDEX idx_price_list_item_article ON pricing.price_list_item (article_id)
    WHERE article_id IS NOT NULL;

-- LOW-Fix (Review): Position und Stammartikel müssen dieselbe Positionsart tragen
CREATE FUNCTION pricing.check_item_article_type() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_article_type text;
BEGIN
    IF NEW.article_id IS NOT NULL THEN
        SELECT line_type INTO v_article_type
        FROM pricing.article WHERE id = NEW.article_id FOR SHARE;
        IF v_article_type IS DISTINCT FROM NEW.line_type THEN
            RAISE EXCEPTION
                'price_list_item: Positionsart % passt nicht zur Artikelart % (B-24/B-25)',
                NEW.line_type, v_article_type;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_price_list_item_article_type
    BEFORE INSERT OR UPDATE OF article_id, line_type ON pricing.price_list_item
    FOR EACH ROW EXECUTE FUNCTION pricing.check_item_article_type();

COMMIT;

-- Rückwärtsstrategie: DROP der Tabellen und der Spalte, nur solange kein
-- Artikelstamm entstanden ist.
