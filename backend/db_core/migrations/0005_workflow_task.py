"""Neue Fachtabelle workflow.task (Aufgaben).

Hand-SQL nach db/README.md: neue Fachtabellen entstehen als Django-Migration
mit RunSQL und erben den Schutzstandard (No-Delete/Audit/No-Truncate) im selben
Schritt — Muster wie workflow.project_note (Migration 0035).

Eine Aufgabe ist ein leichtgewichtiges To-do mit optionaler Verknüpfung zu
Projekt und/oder Kontakt (Party), Zuständigem und Fälligkeit. Erledigen setzt
completed_by/completed_at; „löschen" gibt es nicht — Status VERWORFEN statt
physischem DELETE (Schutzstandard).
"""
from django.db import migrations

CREATE_SQL = r"""
CREATE TABLE workflow.task (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title               text NOT NULL CHECK (btrim(title) <> ''),
    description         text NULL,
    due_date            date NULL,
    status              text NOT NULL DEFAULT 'OFFEN'
                        CHECK (status IN ('OFFEN', 'ERLEDIGT', 'VERWORFEN')),
    assigned_to_user_id uuid NULL REFERENCES security.app_user (id),
    project_id          uuid NULL REFERENCES workflow.project (id),
    party_id            uuid NULL REFERENCES identity.party (id),
    completed_by        uuid NULL REFERENCES security.app_user (id),
    completed_at        timestamptz NULL,
    created_by          uuid NOT NULL REFERENCES security.app_user (id),
    version             integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    -- completed_by/completed_at nur gemeinsam, und genau dann bei ERLEDIGT.
    CONSTRAINT task_completed_pair
        CHECK ((completed_by IS NULL) = (completed_at IS NULL)),
    CONSTRAINT task_completed_status
        CHECK ((status = 'ERLEDIGT') = (completed_at IS NOT NULL))
);

CREATE INDEX idx_task_assignee ON workflow.task (assigned_to_user_id)
    WHERE assigned_to_user_id IS NOT NULL;
CREATE INDEX idx_task_project ON workflow.task (project_id)
    WHERE project_id IS NOT NULL;
CREATE INDEX idx_task_party ON workflow.task (party_id)
    WHERE party_id IS NOT NULL;
CREATE INDEX idx_task_status ON workflow.task (status);

-- Schutzstandard (No-Delete/Audit/No-Truncate), Muster wie 0035 project_note.
CREATE TRIGGER trg_task_updated_at BEFORE UPDATE ON workflow.task
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_task_audit AFTER UPDATE ON workflow.task
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_task_no_delete BEFORE DELETE ON workflow.task
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_task_no_truncate BEFORE TRUNCATE ON workflow.task
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON workflow.task FROM PUBLIC;

-- Verknüpfte Party darf nicht zusammengeführt (MERGED) sein — Muster wie
-- service_case (identity.assert_parties_not_merged mit Spaltennamen).
CREATE TRIGGER trg_task_no_merged
    BEFORE INSERT OR UPDATE ON workflow.task
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS workflow.task;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0004_project_projectcategory_projectproperty_servicecase"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
