-- Migration 0040: Anbindungsart — Großhändler vs. Hersteller.
-- Benutzer-Klarstellung 2026-07-05: Das GERÄTEWISSEN durchsucht nur
-- HERSTELLER-Daten (Vaillant, Junkers/Bosch: Ersatzteile, Produkte,
-- Geräteverwendungen); der Großhandels-Artikelstamm (G.U.T) gehört NICHT
-- hinein. Im Artikelstamm selbst bleiben alle Quellen gemeinsam sichtbar.

BEGIN;

ALTER TABLE pricing.supplier_connection
    ADD COLUMN connection_kind text NOT NULL DEFAULT 'GROSSHAENDLER'
    CHECK (connection_kind IN ('GROSSHAENDLER', 'HERSTELLER'));

UPDATE pricing.supplier_connection
SET connection_kind = 'HERSTELLER'
WHERE source_namespace IN ('vaillant', 'ppt');

COMMIT;

-- Rückwärtsstrategie: Spalte droppen.
