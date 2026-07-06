-- Migration 0002: security.app_user, identity.party, person, organization
-- Beschlüsse: A-02 (Organisationstypen), OPUS-03 (Benutzerreferenzziel), A-04 (Merge-Grundregeln)

BEGIN;

-- ---------------------------------------------------------------------------
-- security.app_user — minimales Referenzziel, bewusst kein Berechtigungssystem
-- ---------------------------------------------------------------------------
CREATE TABLE security.app_user (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name        text NOT NULL CHECK (btrim(display_name) <> ''),
    principal_party_id  uuid NULL,
    status              text NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'DISABLED')),
    version             integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_app_user_updated_at
    BEFORE UPDATE ON security.app_user
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- identity.party
-- ---------------------------------------------------------------------------
CREATE TABLE identity.party (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    party_type            text NOT NULL CHECK (party_type IN ('PERSON', 'ORGANIZATION')),
    display_name          text NOT NULL CHECK (btrim(display_name) <> ''),
    status                text NOT NULL DEFAULT 'ACTIVE'
                          CHECK (status IN ('ACTIVE', 'INACTIVE', 'MERGED')),
    merged_into_party_id  uuid NULL REFERENCES identity.party (id),
    version               integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    -- MERGED genau dann, wenn ein Zusammenführungsziel gesetzt ist
    CHECK ((status = 'MERGED') = (merged_into_party_id IS NOT NULL)),
    -- keine Selbstzusammenführung
    CHECK (merged_into_party_id IS NULL OR merged_into_party_id <> id)
);

CREATE INDEX idx_party_merged_into ON identity.party (merged_into_party_id)
    WHERE merged_into_party_id IS NOT NULL;

CREATE TRIGGER trg_party_updated_at
    BEFORE UPDATE ON identity.party
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- Verzögerter Constraint-Trigger gegen Ketten und Zyklen (REV-A-07):
-- 1. Das Ziel einer Zusammenführung muss kanonisch sein (selbst nicht MERGED).
-- 2. Eine Party, auf die andere zusammengeführt wurden, darf nicht selbst
--    zusammengeführt werden (keine Kettenbildung).
CREATE FUNCTION identity.assert_merge_canonical() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_target_status text;
BEGIN
    IF NEW.merged_into_party_id IS NOT NULL THEN
        SELECT status INTO v_target_status
        FROM identity.party
        WHERE id = NEW.merged_into_party_id
        FOR UPDATE;

        IF v_target_status = 'MERGED' THEN
            RAISE EXCEPTION
                'Party % kann nicht in bereits zusammengeführte Party % zusammengeführt werden (Kette/Zyklus)',
                NEW.id, NEW.merged_into_party_id;
        END IF;
    END IF;

    IF NEW.status = 'MERGED' THEN
        IF EXISTS (
            SELECT 1 FROM identity.party
            WHERE merged_into_party_id = NEW.id
        ) THEN
            RAISE EXCEPTION
                'Party % ist Zusammenführungsziel anderer Parties und darf nicht selbst zusammengeführt werden (Kette)',
                NEW.id;
        END IF;
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_party_merge_canonical
    AFTER INSERT OR UPDATE OF status, merged_into_party_id ON identity.party
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION identity.assert_merge_canonical();

-- ---------------------------------------------------------------------------
-- identity.person / identity.organization
-- ---------------------------------------------------------------------------
CREATE TABLE identity.person (
    party_id    uuid PRIMARY KEY REFERENCES identity.party (id),
    salutation  text NULL,
    title       text NULL,
    first_name  text NOT NULL CHECK (btrim(first_name) <> ''),
    last_name   text NOT NULL CHECK (btrim(last_name) <> ''),
    birth_date  date NULL
);

CREATE TABLE identity.organization (
    party_id             uuid PRIMARY KEY REFERENCES identity.party (id),
    -- Beschlossene Codeliste (A-02); genau ein Haupttyp je Organisation.
    organization_type    text NOT NULL CHECK (organization_type IN
                         ('PROPERTY_MANAGEMENT', 'WEG', 'COMPANY',
                          'AUTHORITY', 'INSURER', 'OTHER')),
    legal_name           text NOT NULL CHECK (btrim(legal_name) <> ''),
    legal_form           text NULL,
    registration_number  text NULL,
    tax_number           text NULL,
    vat_id               text NULL
);

-- Typkonsistenz Party <-> Subtyp
CREATE FUNCTION identity.assert_party_type() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_expected text := TG_ARGV[0];
    v_actual   text;
BEGIN
    -- FOR SHARE blockiert einen gleichzeitigen Typwechsel der Party (F-11)
    SELECT party_type INTO v_actual FROM identity.party WHERE id = NEW.party_id FOR SHARE;
    IF v_actual IS DISTINCT FROM v_expected THEN
        RAISE EXCEPTION 'Party % hat Typ %, erwartet wird %', NEW.party_id, v_actual, v_expected;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_person_party_type
    BEFORE INSERT OR UPDATE OF party_id ON identity.person
    FOR EACH ROW EXECUTE FUNCTION identity.assert_party_type('PERSON');

CREATE TRIGGER trg_organization_party_type
    BEFORE INSERT OR UPDATE OF party_id ON identity.organization
    FOR EACH ROW EXECUTE FUNCTION identity.assert_party_type('ORGANIZATION');

-- Ein nachträglicher Typwechsel einer Party mit vorhandenem Subtyp-Datensatz ist unzulässig.
CREATE FUNCTION identity.forbid_party_type_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.party_type <> OLD.party_type THEN
        IF EXISTS (SELECT 1 FROM identity.person WHERE party_id = OLD.id)
           OR EXISTS (SELECT 1 FROM identity.organization WHERE party_id = OLD.id) THEN
            RAISE EXCEPTION 'party_type von % kann nicht geändert werden: Subtyp-Datensatz vorhanden', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_party_type_change
    BEFORE UPDATE OF party_type ON identity.party
    FOR EACH ROW EXECUTE FUNCTION identity.forbid_party_type_change();

-- FK von app_user auf party erst jetzt möglich
ALTER TABLE security.app_user
    ADD CONSTRAINT fk_app_user_party
    FOREIGN KEY (principal_party_id) REFERENCES identity.party (id);

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen in umgekehrter
-- Reihenfolge, nur solange keine Fachdaten entstanden sind.
