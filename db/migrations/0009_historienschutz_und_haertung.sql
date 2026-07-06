-- Migration 0009: Historienschutz und Härtung
-- Grundlage: Review-Findings F-01, F-02, F-03, F-06 (docs/reviews) und
-- Benutzerbeschlüsse vom 5. Juli 2026:
--   - F-02: Löschverbot auf historisierten Kerntabellen + DB-Audit-Trigger für Änderungen
--   - F-12: keine Belegung auf COMMON_AREA/TECHNICAL_ROOM (in 0005 umgesetzt)

BEGIN;

-- ---------------------------------------------------------------------------
-- F-01: unit_type-Wechsel darf die A-08-Regel nicht umgehen.
-- Eine Einheit mit Eigentumsstand oder Belegung darf nicht zur
-- Gemeinschaftsfläche oder zum Technikraum umgetypt werden.
-- ---------------------------------------------------------------------------
CREATE FUNCTION property.forbid_unit_type_conflicts() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.unit_type IN ('COMMON_AREA', 'TECHNICAL_ROOM')
       AND OLD.unit_type NOT IN ('COMMON_AREA', 'TECHNICAL_ROOM') THEN
        IF EXISTS (SELECT 1 FROM tenure.ownership_period WHERE unit_id = NEW.id) THEN
            RAISE EXCEPTION
                'Einheit %: Typwechsel nach % unzulässig, es existieren Eigentumsstände (Beschluss A-08)',
                NEW.id, NEW.unit_type;
        END IF;
        IF EXISTS (SELECT 1 FROM tenure.occupancy WHERE unit_id = NEW.id) THEN
            RAISE EXCEPTION
                'Einheit %: Typwechsel nach % unzulässig, es existieren Belegungen (Beschluss F-12)',
                NEW.id, NEW.unit_type;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_unit_type_conflicts
    BEFORE UPDATE OF unit_type ON property.unit
    FOR EACH ROW EXECUTE FUNCTION property.forbid_unit_type_conflicts();

-- ---------------------------------------------------------------------------
-- F-06: Neue fachliche Referenzen auf zusammengeführte Parties sind unzulässig.
-- Bestehende historische Zeilen bleiben unverändert gültig.
-- ---------------------------------------------------------------------------
CREATE FUNCTION identity.assert_parties_not_merged() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_col     text;
    v_party   uuid;
    v_status  text;
    v_row     jsonb := to_jsonb(NEW);
BEGIN
    FOREACH v_col IN ARRAY TG_ARGV LOOP
        v_party := nullif(v_row ->> v_col, '')::uuid;
        IF v_party IS NOT NULL THEN
            -- FOR SHARE blockiert jedes gleichzeitige UPDATE der Party-Zeile und
            -- damit auch ein paralleles Zusammenführen (NR-01: KEY SHARE genügte
            -- nicht, da der Merge-Statuswechsel ein Non-Key-Update ist)
            SELECT status INTO v_status
            FROM identity.party WHERE id = v_party FOR SHARE;
            IF v_status = 'MERGED' THEN
                RAISE EXCEPTION
                    '%.%: Spalte % verweist auf zusammengeführte Party %; kanonische Party verwenden (A-04)',
                    TG_TABLE_SCHEMA, TG_TABLE_NAME, v_col, v_party;
            END IF;
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ownership_interest_no_merged BEFORE INSERT OR UPDATE ON tenure.ownership_interest
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('owner_party_id');
CREATE TRIGGER trg_occupancy_party_no_merged BEFORE INSERT OR UPDATE ON tenure.occupancy_party
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');
CREATE TRIGGER trg_billing_party_no_merged BEFORE INSERT OR UPDATE ON billing.billing_instruction_party
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');
CREATE TRIGGER trg_relationship_no_merged BEFORE INSERT OR UPDATE ON identity.party_relationship
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('from_party_id', 'to_party_id');
CREATE TRIGGER trg_property_role_no_merged BEFORE INSERT OR UPDATE ON property.property_party_role
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');
CREATE TRIGGER trg_mandate_no_merged BEFORE INSERT OR UPDATE ON management.management_mandate
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('management_party_id', 'principal_party_id', 'default_contact_party_id');
CREATE TRIGGER trg_responsibility_no_merged BEFORE INSERT OR UPDATE ON management.management_responsibility
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('responsible_party_id');
CREATE TRIGGER trg_authority_no_merged BEFORE INSERT OR UPDATE ON management.party_authority
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('principal_party_id', 'authorized_party_id');

-- ---------------------------------------------------------------------------
-- F-02 (Benutzerbeschluss): Löschverbot auf historisierten Kerntabellen.
-- Historie wird beendet, nicht gelöscht; Korrekturen erfolgen vorwärts.
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_ownership_period_no_delete BEFORE DELETE ON tenure.ownership_period
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_ownership_interest_no_delete BEFORE DELETE ON tenure.ownership_interest
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_occupancy_no_delete BEFORE DELETE ON tenure.occupancy
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_occupancy_party_no_delete BEFORE DELETE ON tenure.occupancy_party
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_party_relationship_no_delete BEFORE DELETE ON identity.party_relationship
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_property_party_role_no_delete BEFORE DELETE ON property.property_party_role
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_mandate_no_delete BEFORE DELETE ON management.management_mandate
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_responsibility_no_delete BEFORE DELETE ON management.management_responsibility
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_authority_no_delete BEFORE DELETE ON management.party_authority
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_billing_instruction_no_delete BEFORE DELETE ON billing.billing_instruction
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_billing_party_no_delete BEFORE DELETE ON billing.billing_instruction_party
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
-- Mandatseinheiten sind unveränderlich; Umfangskorrekturen erfolgen über ein
-- Nachfolgemandat (A-11: Beendigung als Bestätigungsaufgabe).
CREATE TRIGGER trg_mandate_unit_immutable BEFORE UPDATE OR DELETE ON management.management_mandate_unit
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

-- ---------------------------------------------------------------------------
-- F-02 (Benutzerbeschluss): DB-Audit-Trigger — jede Änderung an historisierten
-- Kerntabellen wird mit Vorher-/Nachher-Auszug protokolliert. Setzt die
-- Anwendung `app.current_user_id`, wird der Benutzer erfasst, sonst SYSTEM.
-- ---------------------------------------------------------------------------
CREATE FUNCTION audit.audit_row_update() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_user uuid := nullif(current_setting('app.current_user_id', true), '')::uuid;
BEGIN
    INSERT INTO audit.audit_entry
        (actor_type, actor_user_id, action, target_type, target_id,
         before_excerpt, after_excerpt)
    VALUES
        (CASE WHEN v_user IS NULL THEN 'SYSTEM' ELSE 'USER' END,
         v_user,
         'ROW_UPDATE',
         TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
         (to_jsonb(NEW) ->> 'id')::uuid,
         to_jsonb(OLD),
         to_jsonb(NEW));
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_ownership_period_audit AFTER UPDATE ON tenure.ownership_period
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_ownership_interest_audit AFTER UPDATE ON tenure.ownership_interest
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_occupancy_audit AFTER UPDATE ON tenure.occupancy
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_occupancy_party_audit AFTER UPDATE ON tenure.occupancy_party
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_party_relationship_audit AFTER UPDATE ON identity.party_relationship
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_property_party_role_audit AFTER UPDATE ON property.property_party_role
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_mandate_audit AFTER UPDATE ON management.management_mandate
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_responsibility_audit AFTER UPDATE ON management.management_responsibility
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_authority_audit AFTER UPDATE ON management.party_authority
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_billing_instruction_audit AFTER UPDATE ON billing.billing_instruction
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_billing_party_audit AFTER UPDATE ON billing.billing_instruction_party
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();

-- ---------------------------------------------------------------------------
-- F-03: Append-only auch gegen TRUNCATE absichern.
-- Hinweis: Ein Tabellen-Owner kann Trigger deaktivieren; echte Härtung folgt
-- mit der Rollentrennung ab B-35.
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_party_merge_no_truncate BEFORE TRUNCATE ON identity.party_merge
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_audit_entry_no_truncate BEFORE TRUNCATE ON audit.audit_entry
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_domain_event_no_truncate BEFORE TRUNCATE ON audit.domain_event
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger und Funktionen. Der Historienschutz darf
-- nur durch eine ausdrücklich beschlossene Korrekturmigration aufgehoben werden.
