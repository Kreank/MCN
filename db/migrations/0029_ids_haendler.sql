-- Migration 0029: IDS-Händler-Registry — Anbindungen als offene Stammdaten
-- Benutzeranforderung (5. Juli 2026): mehrere IDS-Schnittstellen (u. a. G.U.T.,
-- Vaillant, Reisser, Viessmann), neue Händler jederzeit ergänzbar.
-- Beschlüsse: B-25 (EK-Quellen), REV-A-05 (kollisionsfreie Namespaces),
-- CLAUDE.md (KEINE Secrets in der Datenbank — nur Verweis auf den Secret-Store)

BEGIN;

CREATE TABLE pricing.supplier_connection (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_party_id     uuid NOT NULL REFERENCES identity.party (id),
    source_system         text NOT NULL DEFAULT 'IDS_CONNECT'
                          CHECK (btrim(source_system) <> ''),
    -- stabiler, kleingeschriebener Namespace — Grundlage der Artikelreferenzen
    source_namespace      text NOT NULL CHECK (source_namespace ~ '^[a-z0-9][a-z0-9-]*$'),
    label                 text NOT NULL CHECK (btrim(label) <> ''),
    shop_url              text NULL,
    -- Verweis auf Zugangsdaten im Secret-Store der App-Schicht;
    -- das Secret selbst wird NIEMALS in der Datenbank gespeichert (CLAUDE.md)
    credential_reference  text NULL,
    status                text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    last_import_at        timestamptz NULL,
    version               integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_namespace)
);

CREATE TRIGGER trg_supplier_connection_updated_at
    BEFORE UPDATE ON pricing.supplier_connection
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_supplier_connection_audit
    AFTER UPDATE ON pricing.supplier_connection
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_supplier_connection_no_merged
    BEFORE INSERT OR UPDATE ON pricing.supplier_connection
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('supplier_party_id');
-- Anbindungen werden deaktiviert, nicht gelöscht (Artikelreferenzen zeigen auf den Namespace)
CREATE TRIGGER trg_supplier_connection_no_delete
    BEFORE DELETE ON pricing.supplier_connection
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_supplier_connection_no_truncate
    BEFORE TRUNCATE ON pricing.supplier_connection
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON pricing.supplier_connection FROM PUBLIC;

-- Der Namespace einer Anbindung ist unveränderlich (Artikelreferenzen hängen daran);
-- ein neuer Namespace ist eine neue Anbindung.
CREATE FUNCTION pricing.protect_supplier_connection() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.source_system IS DISTINCT FROM OLD.source_system
       OR NEW.source_namespace IS DISTINCT FROM OLD.source_namespace
       OR NEW.supplier_party_id IS DISTINCT FROM OLD.supplier_party_id THEN
        RAISE EXCEPTION
            'supplier_connection %: System, Namespace und Lieferant sind unveränderlich; neue Anbindung anlegen (REV-A-05)',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_supplier_connection_protect
    BEFORE UPDATE ON pricing.supplier_connection
    FOR EACH ROW EXECUTE FUNCTION pricing.protect_supplier_connection();

COMMIT;

-- Rückwärtsstrategie: DROP der Tabelle, nur solange keine Anbindungen registriert sind.
