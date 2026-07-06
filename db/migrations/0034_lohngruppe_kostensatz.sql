-- Migration 0034: Lohngruppen-Kostensatz.
-- hourly_rate ist der VERRECHNUNGSSATZ (VK an den Kunden). Für Gewinn je
-- Rubrik/Position braucht der Lohnanteil zusätzlich die internen KOSTEN
-- (cost_rate, z. B. AG-Brutto + Gemeinkosten). NULL = Kosten unbekannt,
-- die Auswertung rechnet dann konservativ mit dem Verrechnungssatz
-- (Gewinn 0 auf Lohn) und weist das aus, statt Gewinn zu erfinden.

BEGIN;

ALTER TABLE pricing.wage_group
    ADD COLUMN cost_rate numeric(12,2) CHECK (cost_rate >= 0);

COMMENT ON COLUMN pricing.wage_group.hourly_rate IS 'Verrechnungssatz (VK) je Stunde';
COMMENT ON COLUMN pricing.wage_group.cost_rate IS 'interner Kostensatz je Stunde; NULL = unbekannt (Auswertung rechnet konservativ mit Verrechnungssatz)';

COMMIT;

-- Rückwärtsstrategie: Spalte droppen.
