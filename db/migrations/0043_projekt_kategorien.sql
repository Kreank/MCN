-- Migration 0043: Projektordner (project_category) — benutzerdefinierte
-- Unterteilung der Projekte (Benutzer-Klarstellung 2026-07-06: „Pipeline"
-- meint Projekte unterteilen können, z. B. Heizung / Sanitär / Wartung;
-- der Benutzer legt die Ordner selbst an). Ordner sind reine
-- Gliederung/Filter — KEIN eigener Statusautomat je Ordner (offener Punkt
-- „mehrere benannte Pipelines je Entity" aus 0042, bräuchte Beschluss).
-- Ordner dürfen GELÖSCHT werden (Benutzerentscheidung 2026-07-06):
-- enthaltene Projekte wandern dabei in die übergeordnete Ebene (Zuordnung
-- wird gelöst) — das erledigt der API-Endpunkt in einer Transaktion;
-- der FK verhindert verwaiste Referenzen. Die Zuordnung ist optional.

BEGIN;

CREATE TABLE workflow.project_category (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    -- Anzeigefarbe (Karten/Pillen im Client), z. B. '#EF804E'
    color_hex   text NULL CHECK (color_hex ~ '^#[0-9A-Fa-f]{6}$'),
    sort_order  integer NOT NULL DEFAULT 0,
    status      text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_project_category_updated_at
    BEFORE UPDATE ON workflow.project_category
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- Bewusst KEINE vorangelegten Ordner: der Benutzer erstellt seine
-- Unterteilung vollständig selbst (Benutzerentscheidung 2026-07-06).

ALTER TABLE workflow.project
    ADD COLUMN category_id uuid NULL REFERENCES workflow.project_category (id);

CREATE INDEX idx_project_category ON workflow.project (category_id)
    WHERE category_id IS NOT NULL;

COMMIT;

-- Rückwärtsstrategie: ALTER TABLE workflow.project DROP COLUMN category_id;
-- DROP TABLE workflow.project_category (nur solange keine Fachdaten
-- entstanden sind).
