-- Migration 0039: DATANORM-Preiseinheit persistieren (Review DN-1, HIGH).
-- P-Preisdateien kommen OHNE A-Sätze — der Import muss wissen, ob der Preis
-- je 1/10/100/1000 gilt. Gespeicherte Preise sind IMMER je Stück; der Code
-- hier dient nur der Umrechnung künftiger Preisläufe.

BEGIN;

ALTER TABLE pricing.article_supplier_reference
    ADD COLUMN price_unit_code smallint
    CHECK (price_unit_code BETWEEN 0 AND 3);
COMMENT ON COLUMN pricing.article_supplier_reference.price_unit_code IS
    'DATANORM-Preiseinheit (0=je1, 1=je10, 2=je100, 3=je1000). Gespeicherte Preise sind bereits je Stück umgerechnet.';

COMMIT;

-- Rückwärtsstrategie: Spalte droppen.
