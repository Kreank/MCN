-- Migration 0013: Auftrag (work_order) mit Auftragsrollen und Freigabe-Toren
-- Beschlüsse: B-03 (Status), B-01 (Tor Meldung→Auftrag), B-06 (Folgeauftrag/Gewährleistung),
--             A-25 (Auftraggebermatrix), A-26 (Nachweis in Textform, EMERGENCY_ORDER),
--             A-27 (Schuldner), A-23 (Notfall: Doku-Pflicht, Rechnung erst nach Klärung)

BEGIN;

CREATE TABLE workflow.work_order (
    id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number                  text NOT NULL UNIQUE
                                  DEFAULT workflow.next_number('AU')
                                  CHECK (order_number ~ '^AU-[0-9]{4}-[0-9]{6,}$'),
    project_id                    uuid NULL REFERENCES workflow.project (id),
    service_case_id               uuid NULL REFERENCES workflow.service_case (id),
    title                         text NOT NULL CHECK (btrim(title) <> ''),
    description                   text NULL,
    property_id                   uuid NOT NULL REFERENCES property.property (id),
    building_id                   uuid NULL,
    unit_id                       uuid NULL,
    asset_id                      uuid NULL,
    responsibility_scope          text NOT NULL DEFAULT 'UNKNOWN'
                                  CHECK (responsibility_scope IN
                                  ('UNKNOWN', 'COMMON_PROPERTY', 'PRIVATE_UNIT', 'MIXED')),
    status                        text NOT NULL DEFAULT 'ENTWURF'
                                  CHECK (status IN ('ENTWURF', 'FREIGABE_AUSSTEHEND',
                                  'FREIGEGEBEN', 'IN_PLANUNG', 'IN_AUSFUEHRUNG',
                                  'TECHNISCH_ABGESCHLOSSEN', 'KAUFMAENNISCH_GEPRUEFT',
                                  'ABGERECHNET', 'STORNIERT')),
    priority                      text NOT NULL DEFAULT 'NORMAL'
                                  REFERENCES workflow.priority_level (code),
    customer_reference            text NULL,
    -- Beschluss A-26: Nachweis der Beauftragung in Textform
    order_evidence_reference      text NULL,
    authority_id                  uuid NULL REFERENCES management.party_authority (id),
    -- Beschluss A-23/A-25: Notfallbeauftragung mit Dokumentationspflicht
    is_emergency                  boolean NOT NULL DEFAULT false,
    -- Beschluss WF-05 (Präzisierung A-21): Bestätigung des Verantwortungsbereichs am Auftrag
    responsibility_confirmed_at   timestamptz NULL,
    responsibility_confirmed_by   uuid NULL REFERENCES security.app_user (id),
    -- Beschluss B-06: Folgeauftrag statt Wiedereröffnung nach Abrechnung
    follow_up_of_work_order_id    uuid NULL REFERENCES workflow.work_order (id),
    is_warranty_case              boolean NOT NULL DEFAULT false,
    ordered_at                    timestamptz NULL,
    desired_date                  date NULL,
    version                       integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at                    timestamptz NOT NULL DEFAULT now(),
    updated_at                    timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (building_id, property_id) REFERENCES property.building (id, property_id),
    FOREIGN KEY (unit_id, building_id)     REFERENCES property.unit (id, building_id),
    FOREIGN KEY (asset_id, property_id)    REFERENCES property.technical_asset (id, property_id),
    CHECK (unit_id IS NULL OR building_id IS NOT NULL),
    CHECK (follow_up_of_work_order_id IS NULL OR follow_up_of_work_order_id <> id),
    -- Ein Gewährleistungsfall verweist immer auf den Ursprungsauftrag
    CHECK (NOT is_warranty_case OR follow_up_of_work_order_id IS NOT NULL),
    CHECK ((responsibility_confirmed_at IS NULL) = (responsibility_confirmed_by IS NULL)),
    -- Ziel für zusammengesetzte FKs der Belege (Liegenschaftskonsistenz, P3-12)
    UNIQUE (id, property_id)
);

-- Höchstens ein primärer Beteiligter je Auftrag und Rolle (WF-11, analog F-13)
-- (Index folgt nach der Tabellendefinition von work_order_party weiter unten.)

CREATE TRIGGER trg_work_order_updated_at
    BEFORE UPDATE ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

CREATE TRIGGER trg_work_order_initial_status
    BEFORE INSERT ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('ENTWURF');

-- Bestätigungsregeln des Verantwortungsbereichs gelten auch am Auftrag (WF-05/A-21);
-- die Funktion aus 0012 arbeitet spaltengleich.
CREATE TRIGGER trg_work_order_responsibility
    BEFORE INSERT OR UPDATE ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION workflow.check_case_responsibility();

CREATE TRIGGER trg_work_order_status_validate
    BEFORE UPDATE OF status ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION workflow.validate_status_change('work_order');

CREATE TRIGGER trg_work_order_status_log
    AFTER INSERT OR UPDATE OF status ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION workflow.log_status_change('work_order');

-- ---------------------------------------------------------------------------
-- workflow.work_order_party — Beteiligte des konkreten Auftrags (A-25/A-27/A-29)
-- ---------------------------------------------------------------------------
CREATE TABLE workflow.work_order_party (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id        uuid NOT NULL REFERENCES workflow.work_order (id),
    party_id             uuid NOT NULL REFERENCES identity.party (id),
    role                 text NOT NULL CHECK (role IN
                         ('PRINCIPAL', 'REPRESENTATIVE', 'SERVICE_RECIPIENT', 'OCCUPANT',
                          'COST_BEARER', 'INVOICE_DEBTOR', 'INVOICE_RECIPIENT',
                          'REPORTER', 'ON_SITE_CONTACT')),
    -- Herkunft der Rollenauflösung (Abschnitt 4.5 des Phase-1-Entwurfs)
    source               text NOT NULL DEFAULT 'MANUAL' CHECK (source IN
                         ('MANDATE', 'OWNERSHIP', 'OCCUPANCY', 'BILLING_INSTRUCTION', 'MANUAL')),
    source_reference_id  uuid NULL,
    resolved_at          timestamptz NOT NULL DEFAULT now(),
    is_primary           boolean NOT NULL DEFAULT false,
    allocation_percent   numeric(7, 4) NULL
                         CHECK (allocation_percent > 0 AND allocation_percent <= 100),
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (work_order_id, role, party_id)
);

-- WF-11: höchstens ein primärer Beteiligter je Auftrag und Rolle
CREATE UNIQUE INDEX uq_work_order_party_primary
    ON workflow.work_order_party (work_order_id, role)
    WHERE is_primary;

CREATE TRIGGER trg_work_order_party_no_merged
    BEFORE INSERT OR UPDATE ON workflow.work_order_party
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('party_id');

-- ---------------------------------------------------------------------------
-- Freigabe-Tore (deferred, damit Rollen und Statuswechsel in einer Transaktion
-- entstehen können). Beschlusssemantik WF-04/WF-05 vom 5. Juli 2026:
--   FREIGEGEBEN (B-01): Nachweis in Textform (A-26) immer. Normale Aufträge
--     zusätzlich: bestätigter Verantwortungsbereich (A-21) und Auftraggeber (A-25).
--     Dokumentierte Notfälle (is_emergency) dürfen mit ungeklärter Verantwortung
--     und ohne Auftraggeber starten (A-23 Gefahrenabwehr).
--   ABGERECHNET (B-08/A-23/A-27): immer bestätigter Verantwortungsbereich,
--     Auftraggeber UND Rechnungsschuldner — auch bei Notfällen.
-- ---------------------------------------------------------------------------
CREATE FUNCTION workflow.check_work_order_gates() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM workflow.recheck_work_order_gates(NEW.id);
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_work_order_gates
    AFTER INSERT OR UPDATE OF status, responsibility_scope, order_evidence_reference,
                              is_emergency, responsibility_confirmed_at, responsibility_confirmed_by
    ON workflow.work_order
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION workflow.check_work_order_gates();

-- Zentrale Torprüfung. Sperrt den Auftrag und prüft den Ist-Zustand.
CREATE FUNCTION workflow.recheck_work_order_gates(p_order_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_order         workflow.work_order%ROWTYPE;
    v_has_principal boolean;
    v_has_debtor    boolean;
BEGIN
    SELECT * INTO v_order FROM workflow.work_order WHERE id = p_order_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF v_order.status IN ('FREIGEGEBEN', 'IN_PLANUNG', 'IN_AUSFUEHRUNG',
                          'TECHNISCH_ABGESCHLOSSEN', 'KAUFMAENNISCH_GEPRUEFT', 'ABGERECHNET') THEN
        SELECT EXISTS (SELECT 1 FROM workflow.work_order_party
                       WHERE work_order_id = p_order_id AND role = 'PRINCIPAL')
        INTO v_has_principal;

        IF v_order.order_evidence_reference IS NULL THEN
            RAISE EXCEPTION
                'Auftrag %: Freigabe ohne Beauftragungsnachweis in Textform ist unzulässig (A-26)',
                p_order_id;
        END IF;
        IF NOT v_order.is_emergency THEN
            IF v_order.responsibility_scope = 'UNKNOWN'
               OR v_order.responsibility_confirmed_at IS NULL THEN
                RAISE EXCEPTION
                    'Auftrag %: Freigabe ohne bestätigten Verantwortungsbereich ist unzulässig (B-01/A-21)',
                    p_order_id;
            END IF;
            IF NOT v_has_principal THEN
                RAISE EXCEPTION
                    'Auftrag %: Freigabe ohne Auftraggeber (PRINCIPAL) ist unzulässig (B-01/A-25)',
                    p_order_id;
            END IF;
        END IF;
    END IF;

    IF v_order.status = 'ABGERECHNET' THEN
        SELECT EXISTS (SELECT 1 FROM workflow.work_order_party
                       WHERE work_order_id = p_order_id AND role = 'PRINCIPAL'),
               EXISTS (SELECT 1 FROM workflow.work_order_party
                       WHERE work_order_id = p_order_id AND role = 'INVOICE_DEBTOR')
        INTO v_has_principal, v_has_debtor;

        IF v_order.responsibility_scope = 'UNKNOWN'
           OR v_order.responsibility_confirmed_at IS NULL THEN
            RAISE EXCEPTION
                'Auftrag %: Abrechnung ohne bestätigten Verantwortungsbereich ist unzulässig (WF-05/A-21/A-23)',
                p_order_id;
        END IF;
        IF NOT v_has_principal OR NOT v_has_debtor THEN
            RAISE EXCEPTION
                'Auftrag %: Abrechnung ohne bestätigten Auftraggeber und Rechnungsschuldner ist unzulässig (A-23/A-27/B-08)',
                p_order_id;
        END IF;
    END IF;
END;
$$;

-- Rollenänderungen dürfen ein bereits freigegebenes Tor nicht rückwirkend brechen.
-- WF-01: Bei einem work_order_id-Wechsel werden BEIDE Aufträge geprüft.
CREATE FUNCTION workflow.check_gates_on_party_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM workflow.recheck_work_order_gates(NEW.work_order_id);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        IF TG_OP = 'DELETE' OR OLD.work_order_id IS DISTINCT FROM NEW.work_order_id THEN
            PERFORM workflow.recheck_work_order_gates(OLD.work_order_id);
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_work_order_party_gates
    AFTER INSERT OR UPDATE OR DELETE ON workflow.work_order_party
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION workflow.check_gates_on_party_change();

-- WF-10 (B-06): Ein Gewährleistungs-Folgeauftrag setzt einen abgerechneten
-- Ursprungsauftrag voraus.
CREATE FUNCTION workflow.check_warranty_origin() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_origin_status text;
BEGIN
    IF NEW.is_warranty_case THEN
        SELECT status INTO v_origin_status
        FROM workflow.work_order WHERE id = NEW.follow_up_of_work_order_id FOR SHARE;
        IF v_origin_status IS DISTINCT FROM 'ABGERECHNET' THEN
            RAISE EXCEPTION
                'Auftrag %: Gewährleistungsfall erfordert einen abgerechneten Ursprungsauftrag (B-06), Ursprung ist %',
                NEW.id, coalesce(v_origin_status, 'unbekannt');
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_work_order_warranty_origin
    BEFORE INSERT OR UPDATE OF is_warranty_case, follow_up_of_work_order_id
    ON workflow.work_order
    FOR EACH ROW EXECUTE FUNCTION workflow.check_warranty_origin();

-- WF-04 (Beschluss vom 5. Juli 2026, Präzisierung B-02/B-06): Wiedereröffnung
-- eines Vorgangs nur, solange kein zugehöriger Auftrag abgerechnet ist.
CREATE FUNCTION workflow.check_case_reopen() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'ABGESCHLOSSEN' AND NEW.status = 'IN_PRUEFUNG' THEN
        IF EXISTS (SELECT 1 FROM workflow.work_order
                   WHERE service_case_id = OLD.id AND status = 'ABGERECHNET') THEN
            RAISE EXCEPTION
                'Vorgang %: Wiedereröffnung nach Abrechnung ist unzulässig (B-06) — Folgevorgang anlegen',
                OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_service_case_reopen
    BEFORE UPDATE OF status ON workflow.service_case
    FOR EACH ROW EXECUTE FUNCTION workflow.check_case_reopen();

COMMIT;

-- Rückwärtsstrategie: DROP der Trigger, Funktionen und Tabellen, nur solange keine
-- Fachdaten entstanden sind.
