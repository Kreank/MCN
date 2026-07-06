-- Migration 0031: Nachreview Welle 2 (docs/reviews/2026-07-05-endpunkt-welle-2.md)
-- N-1: Eine bereits zusammengeführte Partei darf nie erneut oder in ein anderes
--      Ziel zusammengeführt werden — der Merge-Zustand ist Historie und damit
--      unveränderlich (AGENT §3). Physisch am DB-Layer erzwungen.
-- N-6: Begründungspflicht-Meldung ohne internen Konfigurationsnamen.

BEGIN;

-- ---------------------------------------------------------------------------
-- N-1: Merge-Zustand einer Partei ist unumkehrbar und unveränderlich.
-- ---------------------------------------------------------------------------
CREATE FUNCTION identity.guard_party_merge_state() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'MERGED'
       AND (NEW.status IS DISTINCT FROM OLD.status
            OR NEW.merged_into_party_id IS DISTINCT FROM OLD.merged_into_party_id) THEN
        RAISE EXCEPTION
            'Partei %: bereits zusammengeführt — Merge-Zustand ist Historie und unveränderlich (B-38)',
            OLD.id;
    END IF;
    IF OLD.merged_into_party_id IS NOT NULL
       AND NEW.merged_into_party_id IS DISTINCT FROM OLD.merged_into_party_id THEN
        RAISE EXCEPTION
            'Partei %: Zusammenführungsziel ist unveränderlich (B-38)', OLD.id;
    END IF;
    IF NEW.status = 'MERGED' AND NEW.merged_into_party_id IS NULL THEN
        RAISE EXCEPTION
            'Partei %: Status MERGED erfordert eine Zielpartei', OLD.id;
    END IF;
    IF NEW.merged_into_party_id = NEW.id THEN
        RAISE EXCEPTION
            'Partei %: kann nicht mit sich selbst zusammengeführt werden', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_party_merge_state_guard
    BEFORE UPDATE ON identity.party
    FOR EACH ROW EXECUTE FUNCTION identity.guard_party_merge_state();

-- ---------------------------------------------------------------------------
-- N-6: Fachmeldung ohne internen Konfigurationsnamen (Informationsdisziplin).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION workflow.validate_status_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_requires_reason boolean;
    v_reason          text := nullif(current_setting('app.status_reason', true), '');
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;

    SELECT requires_reason INTO v_requires_reason
    FROM workflow.status_transition
    WHERE entity = TG_ARGV[0] AND from_status = OLD.status AND to_status = NEW.status;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            '%: Statusübergang % -> % ist nicht erlaubt (B-02/B-03/B-04)',
            TG_ARGV[0], OLD.status, NEW.status;
    END IF;

    IF v_requires_reason AND v_reason IS NULL THEN
        RAISE EXCEPTION
            '%: Statusübergang % -> % erfordert eine Begründung',
            TG_ARGV[0], OLD.status, NEW.status;
    END IF;

    RETURN NEW;
END;
$$;

COMMIT;

-- Rückwärtsstrategie: Trigger/Funktion droppen und validate_status_change aus
-- Migration 0010 wiederherstellen.
