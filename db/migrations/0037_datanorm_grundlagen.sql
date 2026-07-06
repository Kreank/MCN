-- Migration 0037: DATANORM-Grundlagen (Benutzer stellt echte v4-Dateien von
-- G.U.T, Vaillant und Junkers/PPT bereit; HERO-Wissensbasis als Vorlage).
-- Preissemantik (verbindlich, Benutzer-Warnung 2026-07-05):
--   Preiskennzeichen 1 = LISTENPREIS (brutto) -> EK = Liste * (1 - Rabatt)
--   Preiskennzeichen 2 = NETTOPREIS -> direkt EK
--   Preise in Cent je Preiseinheit (0=1/1=10/2=100/3=1000) — Import rechnet um.
--   Der Kunden-VK wird NIE aus DATANORM gesetzt (nur VK-Gruppen-Formeln).

BEGIN;

-- Rabattgruppen je Lieferant (R-Sätze bzw. .RAB-Dateien)
CREATE TABLE pricing.supplier_discount_group (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system     text NOT NULL DEFAULT 'DATANORM',
    source_namespace  text NOT NULL CHECK (source_namespace ~ '^[a-z0-9][a-z0-9-]*$'),
    code              text NOT NULL CHECK (btrim(code) <> ''),
    percent           numeric(6,3) NOT NULL CHECK (percent >= 0 AND percent <= 100),
    label             text,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_namespace, code)
);
CREATE TRIGGER trg_supplier_discount_group_updated_at
    BEFORE UPDATE ON pricing.supplier_discount_group
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_supplier_discount_group_audit
    AFTER UPDATE ON pricing.supplier_discount_group
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_supplier_discount_group_no_truncate
    BEFORE TRUNCATE ON pricing.supplier_discount_group
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();

-- Händler-Referenz: Listenpreis getrennt vom errechneten EK ablegen
ALTER TABLE pricing.article_supplier_reference
    ADD COLUMN list_price numeric(15,2) CHECK (list_price >= 0);
COMMENT ON COLUMN pricing.article_supplier_reference.list_price IS
    'Hersteller-/Händler-Listenpreis (DATANORM Preiskennzeichen 1); EK = last_purchase_price';
COMMENT ON COLUMN pricing.article_supplier_reference.last_purchase_price IS
    'Einkaufspreis: Netto (Preiskennzeichen 2) oder Liste*(1-Rabattgruppe); NULL = unbekannt';

-- Suche: GTIN und Artikelnummern schnell auffindbar (Ersatzteilsuche)
CREATE INDEX IF NOT EXISTS idx_article_gtin ON pricing.article (gtin) WHERE gtin IS NOT NULL;
CREATE INDEX idx_supplier_ref_number ON pricing.article_supplier_reference (supplier_article_number);

COMMIT;

-- Rückwärtsstrategie: Tabelle/Spalte/Indizes droppen.
