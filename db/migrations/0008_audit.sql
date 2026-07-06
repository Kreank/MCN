-- Migration 0008: Audit und Domain Events, Append-only physisch erzwungen
-- Beschlüsse: Audit ab erster schreibender Funktion (AGENT.md), OPUS-02

BEGIN;

-- ---------------------------------------------------------------------------
-- audit.audit_entry
-- ---------------------------------------------------------------------------
CREATE TABLE audit.audit_entry (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_type      text NOT NULL CHECK (actor_type IN ('USER', 'SYSTEM', 'MIGRATION')),
    actor_user_id   uuid NULL REFERENCES security.app_user (id),
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    action          text NOT NULL CHECK (btrim(action) <> ''),
    target_type     text NOT NULL CHECK (btrim(target_type) <> ''),
    target_id       uuid NOT NULL,
    correlation_id  uuid NULL,
    before_excerpt  jsonb NULL,
    after_excerpt   jsonb NULL,
    -- menschliche Akteure sind immer als Benutzer identifiziert
    CHECK ((actor_type = 'USER') = (actor_user_id IS NOT NULL))
);

CREATE INDEX idx_audit_entry_target ON audit.audit_entry (target_type, target_id);
CREATE INDEX idx_audit_entry_occurred ON audit.audit_entry (occurred_at);

-- ---------------------------------------------------------------------------
-- audit.domain_event — technisches Integrationsprotokoll; freie Aggregatreferenz
-- ist hier zulässig, fachliche Beziehungen bleiben in den Modultabellen.
-- ---------------------------------------------------------------------------
CREATE TABLE audit.domain_event (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      text NOT NULL CHECK (btrim(event_type) <> ''),
    aggregate_type  text NOT NULL CHECK (btrim(aggregate_type) <> ''),
    aggregate_id    uuid NOT NULL,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id  uuid NULL
);

CREATE INDEX idx_domain_event_aggregate ON audit.domain_event (aggregate_type, aggregate_id);
CREATE INDEX idx_domain_event_occurred ON audit.domain_event (occurred_at);

-- ---------------------------------------------------------------------------
-- Append-only-Durchsetzung (OPUS-02): Trigger + Rechteentzug
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_audit_entry_append_only
    BEFORE UPDATE OR DELETE ON audit.audit_entry
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

CREATE TRIGGER trg_domain_event_append_only
    BEFORE UPDATE OR DELETE ON audit.domain_event
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON audit.audit_entry FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON audit.domain_event FROM PUBLIC;

-- Hinweis: Anwendungsrollen erhalten bei ihrer Anlage (B-35 bis B-38) auf den
-- Append-only-Tabellen ausschließlich INSERT und SELECT.

COMMIT;

-- Rückwärtsstrategie: DROP der Tabellen, nur solange keine Fachdaten entstanden
-- sind. Auditdaten werden niemals rückwärts migriert.
