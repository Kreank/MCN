-- Migration 0030: Vier-Augen-Prinzip für Dubletten-Zusammenführung (B-38)
-- Review-Finding MEDIUM-1: B-38 ist beschlossen und nennt DUBLETTEN_MERGE
-- ausdrücklich — der Merge läuft daher als Antrag + unabhängige Bestätigung.

BEGIN;

CREATE TABLE identity.merge_request (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merged_party_id     uuid NOT NULL REFERENCES identity.party (id),
    canonical_party_id  uuid NOT NULL REFERENCES identity.party (id),
    -- A-04: Prüfgrundlage ist Pflicht; Namensgleichheit genügt nicht
    reason              text NOT NULL CHECK (btrim(reason) <> ''),
    requested_by        uuid NOT NULL REFERENCES security.app_user (id),
    requested_at        timestamptz NOT NULL DEFAULT now(),
    status              text NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING', 'CONFIRMED', 'REJECTED')),
    decided_by          uuid NULL REFERENCES security.app_user (id),
    decided_at          timestamptz NULL,
    decision_note       text NULL,
    CHECK (merged_party_id <> canonical_party_id),
    CHECK ((status = 'PENDING') = (decided_by IS NULL)),
    CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
    -- B-38: Die Entscheidung trifft zwingend eine ANDERE Person als der Antragsteller
    CHECK (decided_by IS NULL OR decided_by <> requested_by)
);

-- je Dublette höchstens ein offener Antrag
CREATE UNIQUE INDEX uq_merge_request_pending
    ON identity.merge_request (merged_party_id) WHERE status = 'PENDING';

-- Anträge sind Nachweise: Entscheidung einmalig, Inhalt unveränderlich
CREATE FUNCTION identity.guard_merge_request() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'PENDING' THEN
        RAISE EXCEPTION 'merge_request %: Entscheidung ist einmalig (B-38)', OLD.id;
    END IF;
    IF (to_jsonb(NEW) - 'status' - 'decided_by' - 'decided_at' - 'decision_note')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'status' - 'decided_by' - 'decided_at' - 'decision_note') THEN
        RAISE EXCEPTION 'merge_request %: Antragsinhalt ist unveränderlich', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_merge_request_guard
    BEFORE UPDATE ON identity.merge_request
    FOR EACH ROW EXECUTE FUNCTION identity.guard_merge_request();
CREATE TRIGGER trg_merge_request_no_delete
    BEFORE DELETE ON identity.merge_request
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_merge_request_no_truncate
    BEFORE TRUNCATE ON identity.merge_request
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON identity.merge_request FROM PUBLIC;

COMMIT;

-- Rückwärtsstrategie: DROP der Tabelle, nur solange keine Anträge entstanden sind.
