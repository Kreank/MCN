-- Migration 0005: Eigentum und Belegung mit Historie
-- Beschlüsse: A-14/A-15 (Quellen, Stichtag), A-16 (Anteile, exakt 100 %),
--             A-17/A-18 (Belegung, Überlappungsverbot), A-19/A-20 (Selbstnutzung, Leerstand),
--             A-08-Präzisierung (COMMON_AREA/TECHNICAL_ROOM ohne Eigentumsstand, vormals OPUS-04),
--             OPUS-01 (exakt-rationale LCM-Summation)

BEGIN;

-- ---------------------------------------------------------------------------
-- tenure.ownership_period — Eigentumsstand einer Einheit
-- ---------------------------------------------------------------------------
CREATE TABLE tenure.ownership_period (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id               uuid NOT NULL REFERENCES property.unit (id),
    distribution_status   text NOT NULL DEFAULT 'UNRESOLVED'
                          CHECK (distribution_status IN ('COMPLETE', 'PARTIAL', 'UNRESOLVED')),
    valid_from            date NOT NULL,
    valid_until           date NULL,
    -- Beschluss A-14: Quellenangabe ist Pflicht
    source_type           text NOT NULL CHECK (source_type IN
                          ('MANAGEMENT_NOTICE', 'OWNER_LIST', 'ORDER_STATEMENT',
                           'IMPORT', 'MANUAL')),
    source_reference      text NOT NULL CHECK (btrim(source_reference) <> ''),
    confirmed_at          timestamptz NULL,
    confirmed_by_user_id  uuid NULL REFERENCES security.app_user (id),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK ((confirmed_at IS NULL) = (confirmed_by_user_id IS NULL)),
    -- keine zwei wirksamen Eigentumsstände derselben Einheit zum selben Zeitpunkt
    CONSTRAINT excl_ownership_period EXCLUDE USING gist (
        unit_id WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

CREATE TRIGGER trg_ownership_period_updated_at
    BEFORE UPDATE ON tenure.ownership_period
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- A-08-Präzisierung: COMMON_AREA und TECHNICAL_ROOM tragen keinen Eigentumsstand
CREATE FUNCTION tenure.forbid_common_area_ownership() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_unit_type text;
BEGIN
    -- FOR SHARE blockiert einen gleichzeitigen unit_type-Wechsel (F-01/F-11)
    SELECT unit_type INTO v_unit_type FROM property.unit WHERE id = NEW.unit_id FOR SHARE;
    IF v_unit_type IN ('COMMON_AREA', 'TECHNICAL_ROOM') THEN
        RAISE EXCEPTION
            'Einheit % (Typ %) trägt keinen Eigentumsstand: Eigentum an Gemeinschaftsflächen folgt der Gemeinschaft (Beschluss A-08)',
            NEW.unit_id, v_unit_type;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ownership_no_common_area
    BEFORE INSERT OR UPDATE OF unit_id ON tenure.ownership_period
    FOR EACH ROW EXECUTE FUNCTION tenure.forbid_common_area_ownership();

-- ---------------------------------------------------------------------------
-- tenure.ownership_interest — Beteiligung mit rationalem Anteil
-- ---------------------------------------------------------------------------
CREATE TABLE tenure.ownership_interest (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ownership_period_id  uuid NOT NULL REFERENCES tenure.ownership_period (id),
    owner_party_id       uuid NOT NULL REFERENCES identity.party (id),
    share_numerator      integer NULL,
    share_denominator    integer NULL,
    ownership_type       text NOT NULL DEFAULT 'CO_OWNER'
                         CHECK (ownership_type IN ('SOLE', 'CO_OWNER')),
    confirmation_status  text NOT NULL DEFAULT 'UNCONFIRMED'
                         CHECK (confirmation_status IN ('CONFIRMED', 'UNCONFIRMED')),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    -- Zähler und Nenner gemeinsam gesetzt, positiv, Zähler höchstens Nenner
    CHECK ((share_numerator IS NULL) = (share_denominator IS NULL)),
    CHECK (share_numerator IS NULL OR
           (share_numerator > 0 AND share_denominator > 0 AND
            share_numerator <= share_denominator)),
    -- OPUS-01: Nennergrenze gegen LCM-Überlauf
    CHECK (share_denominator IS NULL OR share_denominator <= 1000000),
    -- je Stand höchstens eine Beteiligung derselben Party
    UNIQUE (ownership_period_id, owner_party_id)
);

CREATE TRIGGER trg_ownership_interest_updated_at
    BEFORE UPDATE ON tenure.ownership_interest
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- Exakt-rationale Vollständigkeitsprüfung (A-16, OPUS-01)
-- LCM-Methode: D = lcm(alle Nenner); vollständig genau dann, wenn
-- Σ Zähler_i * (D / Nenner_i) = D. Keine Dezimaldivision, keine Toleranz.
-- Sperrt die betroffene Einheit gegen parallele Falschstände.
-- ---------------------------------------------------------------------------
CREATE FUNCTION tenure.assert_ownership_period_valid(p_period_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_period      tenure.ownership_period%ROWTYPE;
    v_lcm         numeric := 1;
    v_sum         numeric := 0;
    v_count       integer := 0;
    v_null_share  integer := 0;
    v_unconfirmed integer := 0;
    v_sole        integer := 0;
    r             record;
BEGIN
    SELECT * INTO v_period
    FROM tenure.ownership_period
    WHERE id = p_period_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN; -- Stand wurde in derselben Transaktion entfernt
    END IF;

    -- Einheit sperren, damit parallele Änderungen serialisiert werden
    PERFORM 1 FROM property.unit WHERE id = v_period.unit_id FOR UPDATE;

    IF v_period.distribution_status <> 'COMPLETE' THEN
        RETURN; -- PARTIAL/UNRESOLVED dürfen unvollständig sein
    END IF;

    FOR r IN
        SELECT share_numerator, share_denominator, confirmation_status, ownership_type
        FROM tenure.ownership_interest
        WHERE ownership_period_id = p_period_id
    LOOP
        v_count := v_count + 1;
        IF r.share_numerator IS NULL THEN
            v_null_share := v_null_share + 1;
        ELSE
            v_lcm := lcm(v_lcm, r.share_denominator::numeric);
        END IF;
        IF r.confirmation_status <> 'CONFIRMED' THEN
            v_unconfirmed := v_unconfirmed + 1;
        END IF;
        IF r.ownership_type = 'SOLE' THEN
            v_sole := v_sole + 1;
        END IF;
    END LOOP;

    IF v_count = 0 THEN
        RAISE EXCEPTION 'Vollständiger Eigentumsstand % ohne Beteiligung ist unzulässig', p_period_id;
    END IF;
    IF v_null_share > 0 THEN
        RAISE EXCEPTION 'Vollständiger Eigentumsstand % enthält % Beteiligung(en) ohne Anteil', p_period_id, v_null_share;
    END IF;
    IF v_unconfirmed > 0 THEN
        RAISE EXCEPTION 'Vollständiger Eigentumsstand % enthält % unbestätigte Beteiligung(en)', p_period_id, v_unconfirmed;
    END IF;
    -- F-05: SOLE ist in einem vollständigen Stand genau eine Beteiligung mit 100 Prozent
    IF v_sole > 0 AND v_count <> 1 THEN
        RAISE EXCEPTION
            'Vollständiger Eigentumsstand %: SOLE erfordert genau eine Beteiligung, gefunden %',
            p_period_id, v_count;
    END IF;

    FOR r IN
        SELECT share_numerator, share_denominator
        FROM tenure.ownership_interest
        WHERE ownership_period_id = p_period_id
    LOOP
        v_sum := v_sum + r.share_numerator::numeric * (v_lcm / r.share_denominator::numeric);
    END LOOP;

    IF v_sum <> v_lcm THEN
        RAISE EXCEPTION
            'Vollständiger Eigentumsstand %: Anteilssumme %/% ist nicht exakt 100 Prozent',
            p_period_id, v_sum, v_lcm;
    END IF;
END;
$$;

CREATE FUNCTION tenure.check_interest_totals() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM tenure.assert_ownership_period_valid(NEW.ownership_period_id);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM tenure.assert_ownership_period_valid(OLD.ownership_period_id);
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION tenure.check_period_totals() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tenure.assert_ownership_period_valid(NEW.id);
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_ownership_interest_totals
    AFTER INSERT OR UPDATE OR DELETE ON tenure.ownership_interest
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION tenure.check_interest_totals();

CREATE CONSTRAINT TRIGGER trg_ownership_period_totals
    AFTER INSERT OR UPDATE OF distribution_status ON tenure.ownership_period
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION tenure.check_period_totals();

-- ---------------------------------------------------------------------------
-- tenure.occupancy — primäre Nutzungsart (A-17 bis A-20)
-- ---------------------------------------------------------------------------
CREATE TABLE tenure.occupancy (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id             uuid NOT NULL REFERENCES property.unit (id),
    occupancy_type      text NOT NULL CHECK (occupancy_type IN
                        ('RENTED', 'OWNER_OCCUPIED', 'VACANT',
                         'COMMERCIAL_USE', 'OTHER', 'UNKNOWN')),
    -- Beschluss A-17: optionale Vertragsreferenz, keine Mietbeträge
    contract_reference  text NULL,
    valid_from          date NOT NULL,
    valid_until         date NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    -- Beschluss A-18: primäre Belegungszeiträume überlappen nie
    CONSTRAINT excl_occupancy EXCLUDE USING gist (
        unit_id WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

CREATE TRIGGER trg_occupancy_updated_at
    BEFORE UPDATE ON tenure.occupancy
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- Benutzerbeschluss F-12 (5. Juli 2026): COMMON_AREA und TECHNICAL_ROOM tragen
-- keine Belegung. Ein tatsächlich vermieteter Raum ist als eigene Einheit
-- passenden Typs (z. B. STORAGE) zu führen.
CREATE FUNCTION tenure.forbid_common_area_occupancy() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_unit_type text;
BEGIN
    -- FOR SHARE blockiert einen gleichzeitigen unit_type-Wechsel (F-01/F-11)
    SELECT unit_type INTO v_unit_type FROM property.unit WHERE id = NEW.unit_id FOR SHARE;
    IF v_unit_type IN ('COMMON_AREA', 'TECHNICAL_ROOM') THEN
        RAISE EXCEPTION
            'Einheit % (Typ %) trägt keine Belegung (Beschluss F-12 zu A-08/A-17)',
            NEW.unit_id, v_unit_type;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_occupancy_no_common_area
    BEFORE INSERT OR UPDATE OF unit_id ON tenure.occupancy
    FOR EACH ROW EXECUTE FUNCTION tenure.forbid_common_area_occupancy();

-- ---------------------------------------------------------------------------
-- tenure.occupancy_party — beschlossene Rollenliste (A-03, A-19)
-- ---------------------------------------------------------------------------
CREATE TABLE tenure.occupancy_party (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occupancy_id  uuid NOT NULL REFERENCES tenure.occupancy (id),
    party_id      uuid NOT NULL REFERENCES identity.party (id),
    role          text NOT NULL CHECK (role IN
                  ('CONTRACTUAL_TENANT', 'CO_TENANT', 'OCCUPANT',
                   'OWNER_OCCUPANT', 'COMMERCIAL_USER')),
    valid_from    date NOT NULL,
    valid_until   date NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    -- F-10: keine zeitgleiche Doppelerfassung derselben Party in derselben Rolle
    CONSTRAINT excl_occupancy_party_dup EXCLUDE USING gist (
        occupancy_id WITH =,
        party_id WITH =,
        role WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

-- Beteiligtenzeitraum muss innerhalb des Belegungszeitraums liegen.
-- Deferred, damit Belegung und Beteiligte in einer Transaktion entstehen können.
CREATE FUNCTION tenure.assert_occupancy_party_contained(p_party_row_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_ok boolean;
BEGIN
    SELECT daterange(op.valid_from, op.valid_until) <@ daterange(o.valid_from, o.valid_until)
    INTO v_ok
    FROM tenure.occupancy_party op
    JOIN tenure.occupancy o ON o.id = op.occupancy_id
    WHERE op.id = p_party_row_id;

    IF FOUND AND NOT v_ok THEN
        RAISE EXCEPTION
            'Beteiligtenzeitraum von occupancy_party % liegt nicht innerhalb des Belegungszeitraums', p_party_row_id;
    END IF;
END;
$$;

CREATE FUNCTION tenure.check_occupancy_party_range() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tenure.assert_occupancy_party_contained(NEW.id);
    RETURN NULL;
END;
$$;

CREATE FUNCTION tenure.check_occupancy_children_ranges() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT id FROM tenure.occupancy_party WHERE occupancy_id = NEW.id LOOP
        PERFORM tenure.assert_occupancy_party_contained(r.id);
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_occupancy_party_range
    AFTER INSERT OR UPDATE ON tenure.occupancy_party
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION tenure.check_occupancy_party_range();

CREATE CONSTRAINT TRIGGER trg_occupancy_shrink_range
    AFTER UPDATE OF valid_from, valid_until ON tenure.occupancy
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION tenure.check_occupancy_children_ranges();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen in umgekehrter
-- Reihenfolge, nur solange keine Fachdaten entstanden sind.
