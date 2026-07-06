-- Migration 0007: Abrechnungsvorgaben und Verantwortungsregeln
-- Beschlüsse: A-24/A-29 (getrennte Rechnungen, dokumentierte Gesamtschuld),
--             A-27 (Schuldner = Auftraggeber als Standard), A-28 (Versand, Empfänger),
--             A-22 (versionierte Entscheidungshilfe, nur Vorschlag)

BEGIN;

-- ---------------------------------------------------------------------------
-- billing.billing_instruction — genau eine fachliche Ebene je Vorgabe
-- ---------------------------------------------------------------------------
CREATE TABLE billing.billing_instruction (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandate_id             uuid NULL REFERENCES management.management_mandate (id),
    property_id            uuid NULL REFERENCES property.property (id),
    unit_id                uuid NULL REFERENCES property.unit (id),
    billing_address_id     uuid NULL REFERENCES identity.address (id),
    -- Beschluss A-28: E-Mail als Standardversand
    delivery_method        text NOT NULL DEFAULT 'EMAIL'
                           CHECK (delivery_method IN ('EMAIL', 'PORTAL', 'POST')),
    delivery_contact_id    uuid NULL REFERENCES identity.contact_point (id),
    reference_requirement  text NULL,
    priority               integer NOT NULL DEFAULT 100 CHECK (priority > 0),
    valid_from             date NOT NULL,
    valid_until            date NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    -- genau eine Ebene: Mandat, Liegenschaft oder Einheit
    CHECK (num_nonnulls(mandate_id, property_id, unit_id) = 1)
);

CREATE TRIGGER trg_billing_instruction_updated_at
    BEFORE UPDATE ON billing.billing_instruction
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- billing.billing_instruction_party — mehrere Beteiligte als Rollenzeilen (REV-A-03)
-- ---------------------------------------------------------------------------
CREATE TABLE billing.billing_instruction_party (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instruction_id      uuid NOT NULL REFERENCES billing.billing_instruction (id),
    role                text NOT NULL CHECK (role IN
                        ('DEBTOR', 'INVOICE_RECIPIENT', 'REPRESENTATIVE', 'COST_BEARER')),
    party_id            uuid NOT NULL REFERENCES identity.party (id),
    allocation_percent  numeric(7, 4) NULL
                        CHECK (allocation_percent > 0 AND allocation_percent <= 100),
    is_primary          boolean NOT NULL DEFAULT false,
    -- Beschluss A-29: Gesamtschuld nur mit dokumentierter Grundlage
    liability_group     text NULL,
    liability_basis     text NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (liability_group IS NULL OR liability_basis IS NOT NULL),
    -- je Vorgabe höchstens eine Zeile pro Party und Rolle
    UNIQUE (instruction_id, role, party_id)
);

-- Genau ein primärer organisatorischer Rechnungsempfänger je Vorgabe
CREATE UNIQUE INDEX uq_billing_primary_recipient
    ON billing.billing_instruction_party (instruction_id)
    WHERE role = 'INVOICE_RECIPIENT' AND is_primary;

-- ---------------------------------------------------------------------------
-- billing.responsibility_rule — Beschluss A-22 (Struktur; Inhalte mit C-Vorbehalt)
-- Das Ergebnis ist ausschließlich ein Vorschlag; kaufmännisch wirksam wird nur
-- eine bestätigte Einzelfallentscheidung.
-- ---------------------------------------------------------------------------
CREATE TABLE billing.responsibility_rule (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    component                 text NOT NULL CHECK (btrim(component) <> ''),
    location                  text NULL,
    damage_type               text NULL,
    suggested_responsibility  text NOT NULL CHECK (suggested_responsibility IN
                              ('COMMON_PROPERTY', 'SPECIAL_PROPERTY', 'UNCLEAR')),
    source_hint               text NULL,
    rule_version              integer NOT NULL DEFAULT 1 CHECK (rule_version >= 1),
    valid_from                date NOT NULL,
    valid_until               date NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from)
);

CREATE TRIGGER trg_responsibility_rule_updated_at
    BEFORE UPDATE ON billing.responsibility_rule
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

COMMIT;

-- Rückwärtsstrategie: DROP der Tabellen in umgekehrter Reihenfolge, nur solange
-- keine Fachdaten entstanden sind.
