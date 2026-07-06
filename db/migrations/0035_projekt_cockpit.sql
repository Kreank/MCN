-- Migration 0035: Projekt-Cockpit-Fundament (Produktvision 2026-07-05,
-- HERO-Landkarte: Logbuch, Notizen, Checklisten; "alles führt zum Projekt").
-- Historisierungslinie des Hauses:
--   Logbuch = append-only Nachweis (wie Statusverlauf; Korrektur = neuer Eintrag)
--   Notizen = editierbar mit Audit, archivieren statt löschen
--   Checklisten = Vorlagen in den Einstellungen + freie Punkte im Projekt

BEGIN;

-- ---------------------------------------------------------------------------
-- Logbuch (Unternehmensfeed je Projekt) — append-only
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.project_log (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES workflow.project (id),
    category    text NOT NULL DEFAULT 'NOTIZ'
                CHECK (category IN ('NOTIZ', 'ANRUF', 'ABSPRACHE', 'ENTSCHEIDUNG', 'SYSTEM')),
    entry       text NOT NULL CHECK (btrim(entry) <> ''),
    created_by  uuid NOT NULL REFERENCES security.app_user (id),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_project_log_project ON workflow.project_log (project_id, created_at);
CREATE TRIGGER trg_project_log_no_update BEFORE UPDATE ON workflow.project_log
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_project_log_no_delete BEFORE DELETE ON workflow.project_log
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_project_log_no_truncate BEFORE TRUNCATE ON workflow.project_log
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON workflow.project_log FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Notizen — editierbar mit Audit, archivieren statt löschen
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.project_note (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES workflow.project (id),
    title       text,
    body        text NOT NULL CHECK (btrim(body) <> ''),
    status      text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'ARCHIVIERT')),
    created_by  uuid NOT NULL REFERENCES security.app_user (id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_project_note_project ON workflow.project_note (project_id);
CREATE TRIGGER trg_project_note_updated_at BEFORE UPDATE ON workflow.project_note
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_project_note_audit AFTER UPDATE ON workflow.project_note
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_project_note_no_delete BEFORE DELETE ON workflow.project_note
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_project_note_no_truncate BEFORE TRUNCATE ON workflow.project_note
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON workflow.project_note FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Checklisten: Vorlagen (Einstellungen) + Instanzen im Projekt
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.checklist_template (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    status      text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE workflow.checklist_template_item (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id  uuid NOT NULL REFERENCES workflow.checklist_template (id),
    position     integer NOT NULL CHECK (position > 0),
    label        text NOT NULL CHECK (btrim(label) <> ''),
    UNIQUE (template_id, position)
);

CREATE TABLE workflow.checklist (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid NOT NULL REFERENCES workflow.project (id),
    name         text NOT NULL CHECK (btrim(name) <> ''),
    template_id  uuid REFERENCES workflow.checklist_template (id),
    created_by   uuid NOT NULL REFERENCES security.app_user (id),
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_checklist_project ON workflow.checklist (project_id);

CREATE TABLE workflow.checklist_item (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    checklist_id  uuid NOT NULL REFERENCES workflow.checklist (id),
    position      integer NOT NULL CHECK (position > 0),
    label         text NOT NULL CHECK (btrim(label) <> ''),
    done_by       uuid REFERENCES security.app_user (id),
    done_at       timestamptz,
    -- erledigt = wer UND wann, nie nur eines von beidem
    CHECK ((done_by IS NULL) = (done_at IS NULL)),
    UNIQUE (checklist_id, position)
);
CREATE TRIGGER trg_checklist_item_audit AFTER UPDATE ON workflow.checklist_item
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_checklist_item_delete_audit AFTER DELETE ON workflow.checklist_item
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_checklist_template_updated_at BEFORE UPDATE ON workflow.checklist_template
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_checklist_template_audit AFTER UPDATE ON workflow.checklist_template
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();

-- ---------------------------------------------------------------------------
-- Dateien am Projekt (Bilder/Videos im Cockpit): file_link um project_id
-- erweitern; Ein-Ziel-Regel bleibt exakt erhalten
-- ---------------------------------------------------------------------------
ALTER TABLE content.file_link
    ADD COLUMN project_id uuid REFERENCES workflow.project (id);
ALTER TABLE content.file_link
    DROP CONSTRAINT file_link_check,
    ADD CONSTRAINT file_link_check CHECK (
        num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                     unit_id, asset_id, quote_id, invoice_id, party_id,
                     communication_id, project_id) = 1);

COMMIT;

-- Rückwärtsstrategie: neue Tabellen droppen; file_link-Spalte entfernen und
-- alten CHECK wiederherstellen — nur solange keine Cockpit-Daten entstanden.
