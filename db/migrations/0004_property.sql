-- Migration 0004: Liegenschaft, Gebäude, Einheit, technische Anlage
-- Beschlüsse: A-05 (Liegenschaftsdefinition/Typen), A-06 (Hierarchie), A-07 (Einheitstypen),
--             A-08 (Gemeinschaftsflächen), A-09 (Nummern), A-02/A-03 (Rollen inkl. CARETAKER)

BEGIN;

-- ---------------------------------------------------------------------------
-- property.property — Beschluss A-09: OBJ-#####, global eindeutig, fortlaufend
-- ---------------------------------------------------------------------------
CREATE SEQUENCE property.property_number_seq;

CREATE TABLE property.property (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_number  text NOT NULL UNIQUE
                     DEFAULT ('OBJ-' || lpad(nextval('property.property_number_seq')::text, 5, '0'))
                     CHECK (property_number ~ '^OBJ-[0-9]{5,}$'),
    name             text NOT NULL CHECK (btrim(name) <> ''),
    address_id       uuid NOT NULL REFERENCES identity.address (id),
    property_type    text NOT NULL CHECK (property_type IN
                     ('WEG', 'RENTAL_PROPERTY', 'COMMERCIAL', 'MIXED', 'OTHER')),
    status           text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version          integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_property_updated_at
    BEFORE UPDATE ON property.property
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- property.property_party_role — beschlossene Rollenliste (A-02/A-03)
-- Die Verwaltung wird ausschließlich über ein Mandat verbunden.
-- ---------------------------------------------------------------------------
CREATE TABLE property.property_party_role (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id  uuid NOT NULL REFERENCES property.property (id),
    party_id     uuid NOT NULL REFERENCES identity.party (id),
    role         text NOT NULL CHECK (role IN
                 ('COMMUNITY_OF_OWNERS', 'PROPERTY_OWNER', 'OPERATOR', 'CARETAKER')),
    valid_from   date NOT NULL,
    valid_until  date NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT excl_property_party_role_dup EXCLUDE USING gist (
        property_id WITH =,
        party_id WITH =,
        role WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

-- ---------------------------------------------------------------------------
-- property.building — gehört genau zu einer Liegenschaft
-- Sichtbare Gebäudenummer je Liegenschaft eindeutig (A-09 sinngemäß)
-- ---------------------------------------------------------------------------
CREATE TABLE property.building (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id      uuid NOT NULL REFERENCES property.property (id),
    building_number  text NOT NULL CHECK (btrim(building_number) <> ''),
    name             text NULL,
    address_id       uuid NULL REFERENCES identity.address (id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (property_id, building_number),
    -- Ziel für zusammengesetzte Fremdschlüssel (Konsistenz Einheit/Anlage)
    UNIQUE (id, property_id)
);

CREATE TRIGGER trg_building_updated_at
    BEFORE UPDATE ON property.building
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- property.unit — Beschluss A-07 (Typen, Pflichtfelder) und A-09 (Nummer je Liegenschaft)
-- property_id ist redundant und wird per zusammengesetztem FK konsistent gehalten.
-- ---------------------------------------------------------------------------
CREATE TABLE property.unit (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id  uuid NOT NULL,
    property_id  uuid NOT NULL,
    unit_type    text NOT NULL CHECK (unit_type IN
                 ('APARTMENT', 'COMMERCIAL', 'GARAGE', 'PARKING', 'STORAGE',
                  'COMMON_AREA', 'TECHNICAL_ROOM', 'OTHER')),
    unit_number  text NOT NULL CHECK (btrim(unit_number) <> ''),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (building_id, property_id)
        REFERENCES property.building (id, property_id),
    -- Beschluss A-09: sichtbare Einheitsnummer eindeutig je Liegenschaft
    UNIQUE (property_id, unit_number),
    -- Ziele für zusammengesetzte Fremdschlüssel anderer Tabellen
    UNIQUE (id, building_id),
    UNIQUE (id, property_id)
);

CREATE TRIGGER trg_unit_updated_at
    BEFORE UPDATE ON property.unit
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- property.technical_asset — Standortkonsistenz deklarativ über zusammengesetzte FKs
-- ---------------------------------------------------------------------------
CREATE TABLE property.technical_asset (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id  uuid NOT NULL REFERENCES property.property (id),
    building_id  uuid NULL,
    unit_id      uuid NULL,
    name         text NOT NULL CHECK (btrim(name) <> ''),
    asset_type   text NULL,
    attributes   jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    -- Ein Gebäude muss zur angegebenen Liegenschaft gehören
    FOREIGN KEY (building_id, property_id)
        REFERENCES property.building (id, property_id),
    -- Eine Einheit impliziert ihr Gebäude (und damit die Liegenschaft)
    FOREIGN KEY (unit_id, building_id)
        REFERENCES property.unit (id, building_id),
    -- unit_id ohne passendes building_id ist unzulässig
    CHECK (unit_id IS NULL OR building_id IS NOT NULL)
);

CREATE TRIGGER trg_technical_asset_updated_at
    BEFORE UPDATE ON property.technical_asset
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- Externe Referenzen für Liegenschaften und Einheiten (REV-A-05)
-- ---------------------------------------------------------------------------
CREATE TABLE property.property_external_reference (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id       uuid NOT NULL REFERENCES property.property (id),
    source_system     text NOT NULL CHECK (btrim(source_system) <> ''),
    source_namespace  text NOT NULL CHECK (btrim(source_namespace) <> ''),
    external_key      text NOT NULL CHECK (btrim(external_key) <> ''),
    source_party_id   uuid NULL REFERENCES identity.party (id),
    valid_from        date NOT NULL,
    valid_until       date NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT excl_property_extref EXCLUDE USING gist (
        source_system WITH =,
        source_namespace WITH =,
        external_key WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

CREATE TABLE property.unit_external_reference (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id           uuid NOT NULL REFERENCES property.unit (id),
    source_system     text NOT NULL CHECK (btrim(source_system) <> ''),
    source_namespace  text NOT NULL CHECK (btrim(source_namespace) <> ''),
    external_key      text NOT NULL CHECK (btrim(external_key) <> ''),
    source_party_id   uuid NULL REFERENCES identity.party (id),
    valid_from        date NOT NULL,
    valid_until       date NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT excl_unit_extref EXCLUDE USING gist (
        source_system WITH =,
        source_namespace WITH =,
        external_key WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

COMMIT;

-- Rückwärtsstrategie: DROP der Tabellen und der Sequenz in umgekehrter
-- Reihenfolge, nur solange keine Fachdaten entstanden sind.
