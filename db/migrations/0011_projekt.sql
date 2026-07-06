-- Migration 0011: Projekt als optionale Klammer
-- Beschlüsse: B-09 (Projekt nur für größere Maßnahmen), B-10 (mehrere Liegenschaften),
--             B-11/B-12 (Nummernkreis P)

BEGIN;

CREATE TABLE workflow.project (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_number       text NOT NULL UNIQUE
                         DEFAULT workflow.next_number('P')
                         CHECK (project_number ~ '^P-[0-9]{4}-[0-9]{6,}$'),
    name                 text NOT NULL CHECK (btrim(name) <> ''),
    -- Technischer Minimalstatus; ein fachlicher Projekt-Statusautomat ist nicht
    -- Gegenstand von B-09/B-10 und wird bei Bedarf über Migration erweitert.
    status               text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
    start_date           date NULL,
    target_end_date      date NULL,
    responsible_user_id  uuid NULL REFERENCES security.app_user (id),
    version              integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (target_end_date IS NULL OR start_date IS NULL OR target_end_date >= start_date)
);

CREATE TRIGGER trg_project_updated_at
    BEFORE UPDATE ON workflow.project
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- Beschluss B-10: Ein Projekt kann mehrere Liegenschaften umfassen.
CREATE TABLE workflow.project_property (
    project_id   uuid NOT NULL REFERENCES workflow.project (id),
    property_id  uuid NOT NULL REFERENCES property.property (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, property_id)
);

COMMIT;

-- Rückwärtsstrategie: DROP der Tabellen, nur solange keine Fachdaten entstanden sind.
