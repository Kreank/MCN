-- Migration 0003: Anschriften, Kontaktwege, Beziehungen, externe Referenzen, Merge-Nachweis
-- Beschlüsse: A-03 (Beziehungstypen), A-04 (Merge auditiert), REV-A-05 (Namespaces), OPUS-07 (Exclusion über Zeitraum)

BEGIN;

-- ---------------------------------------------------------------------------
-- identity.address — nach fachlicher Referenzierung unveränderlich
-- ---------------------------------------------------------------------------
CREATE TABLE identity.address (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    street            text NOT NULL CHECK (btrim(street) <> ''),
    house_number      text NULL,
    address_addition  text NULL,
    postal_code       text NOT NULL CHECK (btrim(postal_code) <> ''),
    city              text NOT NULL CHECK (btrim(city) <> ''),
    country_code      char(2) NOT NULL DEFAULT 'DE' CHECK (country_code ~ '^[A-Z]{2}$'),
    latitude          numeric(9, 6) NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude         numeric(9, 6) NULL CHECK (longitude BETWEEN -180 AND 180),
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Eine Korrektur erzeugt eine neue Adresse; Inhalte werden nie geändert.
CREATE TRIGGER trg_address_immutable
    BEFORE UPDATE OR DELETE ON identity.address
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

-- ---------------------------------------------------------------------------
-- identity.party_address — zeitabhängige Zuordnung
-- ---------------------------------------------------------------------------
CREATE TABLE identity.party_address (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    party_id      uuid NOT NULL REFERENCES identity.party (id),
    address_id    uuid NOT NULL REFERENCES identity.address (id),
    address_type  text NOT NULL CHECK (address_type IN
                  ('BUSINESS', 'POSTAL', 'BILLING', 'PRIVATE')),
    is_primary    boolean NOT NULL DEFAULT false,
    valid_from    date NOT NULL,
    valid_until   date NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    -- OPUS-07: höchstens eine gleichzeitig primäre Adresse je Party und Typ,
    -- als Exclusion über den Gültigkeitszeitraum (historische Primärzuordnungen bleiben zulässig)
    CONSTRAINT excl_party_address_primary EXCLUDE USING gist (
        party_id WITH =,
        address_type WITH =,
        daterange(valid_from, valid_until) WITH &&
    ) WHERE (is_primary)
);

-- ---------------------------------------------------------------------------
-- identity.contact_point — zeitabhängiger Kontaktweg
-- ---------------------------------------------------------------------------
CREATE TABLE identity.contact_point (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    party_id      uuid NOT NULL REFERENCES identity.party (id),
    contact_type  text NOT NULL CHECK (contact_type IN
                  ('EMAIL', 'PHONE', 'MOBILE', 'FAX', 'PORTAL')),
    value         text NOT NULL CHECK (btrim(value) <> ''),
    label         text NULL,
    is_primary    boolean NOT NULL DEFAULT false,
    valid_from    date NOT NULL,
    valid_until   date NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT excl_contact_point_primary EXCLUDE USING gist (
        party_id WITH =,
        contact_type WITH =,
        daterange(valid_from, valid_until) WITH &&
    ) WHERE (is_primary)
);

-- ---------------------------------------------------------------------------
-- identity.party_relationship — beschlossene Typenliste (A-03)
-- ---------------------------------------------------------------------------
CREATE TABLE identity.party_relationship (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_party_id      uuid NOT NULL REFERENCES identity.party (id),
    to_party_id        uuid NOT NULL REFERENCES identity.party (id),
    relationship_type  text NOT NULL CHECK (relationship_type IN
                       ('CONTACT_PERSON_FOR', 'EMPLOYEE_OF',
                        'AUTHORIZED_REPRESENTATIVE_OF', 'LEGAL_GUARDIAN_OF',
                        'SUPPLIER_FOR', 'SUBCONTRACTOR_FOR')),
    valid_from         date NOT NULL,
    valid_until        date NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK (from_party_id <> to_party_id),
    -- Schutz gegen zeitgleiche Doppelerfassung derselben Beziehung
    CONSTRAINT excl_party_relationship_dup EXCLUDE USING gist (
        from_party_id WITH =,
        to_party_id WITH =,
        relationship_type WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

-- ---------------------------------------------------------------------------
-- Externe Referenzen (REV-A-05): kollisionsfreie Namespaces
-- ---------------------------------------------------------------------------
CREATE TABLE identity.party_external_reference (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    party_id          uuid NOT NULL REFERENCES identity.party (id),
    source_system     text NOT NULL CHECK (btrim(source_system) <> ''),
    source_namespace  text NOT NULL CHECK (btrim(source_namespace) <> ''),
    external_key      text NOT NULL CHECK (btrim(external_key) <> ''),
    source_party_id   uuid NULL REFERENCES identity.party (id),
    valid_from        date NOT NULL,
    valid_until       date NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    -- Zeitgleiche widersprüchliche Zuordnungen derselben Kombination sind unzulässig
    CONSTRAINT excl_party_extref EXCLUDE USING gist (
        source_system WITH =,
        source_namespace WITH =,
        external_key WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

-- ---------------------------------------------------------------------------
-- identity.party_merge — Append-only-Nachweis (A-04, OPUS-02)
-- ---------------------------------------------------------------------------
CREATE TABLE identity.party_merge (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merged_party_id     uuid NOT NULL REFERENCES identity.party (id),
    canonical_party_id  uuid NOT NULL REFERENCES identity.party (id),
    actor_user_id       uuid NOT NULL REFERENCES security.app_user (id),
    occurred_at         timestamptz NOT NULL DEFAULT now(),
    reason              text NOT NULL CHECK (btrim(reason) <> ''),
    correlation_id      uuid NULL,
    CHECK (merged_party_id <> canonical_party_id)
);

CREATE TRIGGER trg_party_merge_append_only
    BEFORE UPDATE OR DELETE ON identity.party_merge
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON identity.party_merge FROM PUBLIC;

COMMIT;

-- Rückwärtsstrategie: DROP der Tabellen in umgekehrter Reihenfolge, nur solange
-- keine Fachdaten entstanden sind.
