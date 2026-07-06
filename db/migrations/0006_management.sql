-- Migration 0006: Verwaltungsmandat, Mandatseinheiten, Zuständigkeit, Befugnis
-- Beschlüsse: A-10 (Mandatstypen, Pflichtkontakt), A-11 (Teilmandate),
--             A-12 (Wechsel als Zeitraumsemantik), A-13 (Zuständigkeiten, Priorität),
--             A-26 (Befugnistypen, Wertgrenzen als Stammdaten)

BEGIN;

-- ---------------------------------------------------------------------------
-- management.management_mandate
-- ---------------------------------------------------------------------------
CREATE TABLE management.management_mandate (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    management_party_id       uuid NOT NULL REFERENCES identity.party (id),
    principal_party_id        uuid NOT NULL REFERENCES identity.party (id),
    property_id               uuid NOT NULL REFERENCES property.property (id),
    mandate_type              text NOT NULL CHECK (mandate_type IN
                              ('WEG_MANAGEMENT', 'RENTAL_MANAGEMENT',
                               'SPECIAL_PROPERTY_MANAGEMENT', 'SPECIAL_MANDATE')),
    scope_type                text NOT NULL CHECK (scope_type IN
                              ('ENTIRE_PROPERTY', 'SELECTED_UNITS')),
    valid_from                date NOT NULL,
    valid_until               date NULL,
    status                    text NOT NULL DEFAULT 'ACTIVE'
                              CHECK (status IN ('ACTIVE', 'ENDED')),
    contract_reference        text NULL,
    -- Beschluss A-10: mindestens ein Standardkontakt je Mandat
    default_contact_party_id  uuid NOT NULL REFERENCES identity.party (id),
    version                   integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK (management_party_id <> principal_party_id),
    -- F-10: ein beendetes Mandat besitzt immer ein Enddatum
    CHECK (status <> 'ENDED' OR valid_until IS NOT NULL),
    -- Ziel für zusammengesetzten FK der Mandatseinheiten
    UNIQUE (id, property_id),
    -- Vollmandate desselben Typs überlappen nie im selben Objekt
    CONSTRAINT excl_mandate_entire EXCLUDE USING gist (
        property_id WITH =,
        mandate_type WITH =,
        daterange(valid_from, valid_until) WITH &&
    ) WHERE (scope_type = 'ENTIRE_PROPERTY')
);

CREATE TRIGGER trg_mandate_updated_at
    BEFORE UPDATE ON management.management_mandate
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- management.management_mandate_unit — Teilmandate (A-11)
-- ---------------------------------------------------------------------------
CREATE TABLE management.management_mandate_unit (
    mandate_id   uuid NOT NULL,
    property_id  uuid NOT NULL,
    unit_id      uuid NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mandate_id, unit_id),
    -- Mandat und Einheit müssen zur selben Liegenschaft gehören
    FOREIGN KEY (mandate_id, property_id)
        REFERENCES management.management_mandate (id, property_id),
    FOREIGN KEY (unit_id, property_id)
        REFERENCES property.unit (id, property_id)
);

-- ---------------------------------------------------------------------------
-- Bereichsübergreifende Mandatsregeln (deferred, damit Mandat + Einheiten in
-- einer Transaktion entstehen können):
--   1. ENTIRE_PROPERTY hat keine Mandatseinheiten.
--   2. SELECTED_UNITS hat mindestens eine Mandatseinheit.
--   3. Kein Zeitraumkonflikt desselben Mandatstyps je Einheit:
--      Teilmandate gegen Vollmandate und gegen andere Teilmandate.
-- ---------------------------------------------------------------------------
CREATE FUNCTION management.assert_mandate_valid(p_mandate_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_mandate     management.management_mandate%ROWTYPE;
    v_unit_count  integer;
    v_conflict    uuid;
BEGIN
    SELECT * INTO v_mandate
    FROM management.management_mandate
    WHERE id = p_mandate_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- Liegenschaft sperren: serialisiert konkurrierende Mandatsänderungen
    PERFORM 1 FROM property.property WHERE id = v_mandate.property_id FOR UPDATE;

    SELECT count(*) INTO v_unit_count
    FROM management.management_mandate_unit
    WHERE mandate_id = p_mandate_id;

    IF v_mandate.scope_type = 'ENTIRE_PROPERTY' AND v_unit_count > 0 THEN
        RAISE EXCEPTION 'Mandat %: ENTIRE_PROPERTY darf keine Mandatseinheiten besitzen', p_mandate_id;
    END IF;

    IF v_mandate.scope_type = 'SELECTED_UNITS' AND v_unit_count = 0 THEN
        RAISE EXCEPTION 'Mandat %: SELECTED_UNITS erfordert mindestens eine Mandatseinheit', p_mandate_id;
    END IF;

    -- Konflikt Teilmandat gegen zeitlich überlappendes Vollmandat desselben Typs
    IF v_mandate.scope_type = 'SELECTED_UNITS' THEN
        SELECT m.id INTO v_conflict
        FROM management.management_mandate m
        WHERE m.property_id = v_mandate.property_id
          AND m.mandate_type = v_mandate.mandate_type
          AND m.scope_type = 'ENTIRE_PROPERTY'
          AND m.id <> v_mandate.id
          AND daterange(m.valid_from, m.valid_until)
              && daterange(v_mandate.valid_from, v_mandate.valid_until)
        LIMIT 1;

        IF v_conflict IS NOT NULL THEN
            RAISE EXCEPTION
                'Mandat %: Zeitraum kollidiert mit Vollmandat % desselben Typs', p_mandate_id, v_conflict;
        END IF;

        -- Konflikt zwischen Teilmandaten desselben Typs auf derselben Einheit
        SELECT m2.id INTO v_conflict
        FROM management.management_mandate_unit u1
        JOIN management.management_mandate_unit u2
          ON u2.unit_id = u1.unit_id AND u2.mandate_id <> u1.mandate_id
        JOIN management.management_mandate m2 ON m2.id = u2.mandate_id
        WHERE u1.mandate_id = v_mandate.id
          AND m2.mandate_type = v_mandate.mandate_type
          AND daterange(m2.valid_from, m2.valid_until)
              && daterange(v_mandate.valid_from, v_mandate.valid_until)
        LIMIT 1;

        IF v_conflict IS NOT NULL THEN
            RAISE EXCEPTION
                'Mandat %: Einheitskonflikt mit Teilmandat % desselben Typs', p_mandate_id, v_conflict;
        END IF;
    ELSE
        -- Vollmandat gegen zeitlich überlappende Teilmandate desselben Typs
        SELECT m.id INTO v_conflict
        FROM management.management_mandate m
        WHERE m.property_id = v_mandate.property_id
          AND m.mandate_type = v_mandate.mandate_type
          AND m.scope_type = 'SELECTED_UNITS'
          AND daterange(m.valid_from, m.valid_until)
              && daterange(v_mandate.valid_from, v_mandate.valid_until)
        LIMIT 1;

        IF v_conflict IS NOT NULL THEN
            RAISE EXCEPTION
                'Mandat %: Vollmandat kollidiert mit Teilmandat % desselben Typs', p_mandate_id, v_conflict;
        END IF;
    END IF;
END;
$$;

CREATE FUNCTION management.check_mandate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM management.assert_mandate_valid(NEW.id);
    RETURN NULL;
END;
$$;

CREATE FUNCTION management.check_mandate_unit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM management.assert_mandate_valid(NEW.mandate_id);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM management.assert_mandate_valid(OLD.mandate_id);
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_mandate_scope
    AFTER INSERT OR UPDATE ON management.management_mandate
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION management.check_mandate();

CREATE CONSTRAINT TRIGGER trg_mandate_unit_scope
    AFTER INSERT OR UPDATE OR DELETE ON management.management_mandate_unit
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION management.check_mandate_unit();

-- ---------------------------------------------------------------------------
-- management.management_responsibility — Beschluss A-13
-- ---------------------------------------------------------------------------
CREATE TABLE management.management_responsibility (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandate_id            uuid NOT NULL REFERENCES management.management_mandate (id),
    responsibility_type   text NOT NULL CHECK (responsibility_type IN
                          ('TECHNICAL_CONTACT', 'COMMERCIAL_CONTACT',
                           'ACCOUNTING_CONTACT', 'EMERGENCY_CONTACT', 'APPROVER')),
    responsible_party_id  uuid NOT NULL REFERENCES identity.party (id),
    -- Beschluss A-13: Eskalationsreihenfolge über Priorität (kleiner = früher)
    priority              integer NOT NULL DEFAULT 100 CHECK (priority > 0),
    valid_from            date NOT NULL,
    valid_until           date NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    -- F-10: keine zeitgleiche Doppelerfassung derselben Zuständigkeit
    CONSTRAINT excl_responsibility_dup EXCLUDE USING gist (
        mandate_id WITH =,
        responsibility_type WITH =,
        responsible_party_id WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

-- ---------------------------------------------------------------------------
-- management.party_authority — Beschluss A-26
-- ---------------------------------------------------------------------------
CREATE TABLE management.party_authority (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_party_id    uuid NOT NULL REFERENCES identity.party (id),
    authorized_party_id   uuid NOT NULL REFERENCES identity.party (id),
    mandate_id            uuid NULL REFERENCES management.management_mandate (id),
    authority_type        text NOT NULL CHECK (authority_type IN
                          ('ORDER', 'APPROVAL', 'EMERGENCY_ORDER')),
    scope_type            text NOT NULL DEFAULT 'GENERAL'
                          CHECK (scope_type IN ('GENERAL', 'MANDATE')),
    -- Wertgrenzen sind Stammdaten je Befugnis; kein pauschaler Betrag
    amount_limit          numeric(15, 2) NULL CHECK (amount_limit > 0),
    currency              char(3) NULL CHECK (currency ~ '^[A-Z]{3}$'),
    valid_from            date NOT NULL,
    valid_until           date NULL,
    evidence_document_id  uuid NULL, -- FK folgt mit dem Dokumentmodul
    status                text NOT NULL DEFAULT 'ACTIVE'
                          CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED')),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK (principal_party_id <> authorized_party_id),
    CHECK ((amount_limit IS NULL) = (currency IS NULL)),
    CHECK ((scope_type = 'MANDATE') = (mandate_id IS NOT NULL)),
    -- F-10: eine abgelaufene Befugnis besitzt immer ein Enddatum
    CHECK (status <> 'EXPIRED' OR valid_until IS NOT NULL)
);

CREATE TRIGGER trg_party_authority_updated_at
    BEFORE UPDATE ON management.party_authority
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen in umgekehrter
-- Reihenfolge, nur solange keine Fachdaten entstanden sind.
