"""Eingangsbeleg accounting.receipt / receipt_line (TEIL B).

Eigene `receipt`-Tabelle (User-Entscheidung), NICHT eine gerichtete
invoicing.invoice: die Ausgangsrechnung ist GoBD-gesichert (eigener Belegkreis
RE/GS, Snapshot/Hash, Festschreibung, Statusautomat ENTWURF→VEROEFFENTLICHT). Die
Eingangsseite hat andere Pflichtfelder (Lieferant, Lieferanten-Rechnungsnummer,
Eingangsdatum), eine andere Nummernlogik (interne Ablagenummer statt fortlaufender
Ausgangsbelegkreis) und einen anderen Statusautomaten (Prüf-/Freigabe-/Buchungs-
Workflow). Getrennte Tabellen halten die Ausgangsseite regressionsfrei.

Nummer: eigene Sequenz `accounting.receipt_number_seq` (Muster hr.employee_number_seq),
KEIN workflow.next_number()-Belegkreis. Ein Eingangsbeleg ist ein FREMDER Beleg;
seine Ordnungsnummer ist eine interne Erfassungs-/Ablagenummer (EB-00001), kein
GoBD-Ausgangsbelegkreis.

Statusautomat (eigener Trigger, Muster hr.enforce_absence_status):
    ERFASST  -> GEPRUEFT | ABGELEHNT
    GEPRUEFT -> ERFASST (Rücksetzung zur Nachbesserung) | FREIGEGEBEN | ABGELEHNT
    FREIGEGEBEN -> GEPRUEFT (Freigabe zurücknehmen, solange nicht gebucht) | GEBUCHT
    GEBUCHT, ABGELEHNT sind FINAL.
Begründung der finalen Zustände: GEBUCHT = an die Buchhaltung/den Steuerberater
übergeben (unveränderlich, wie eine festgeschriebene Rechnung); ABGELEHNT = der
Beleg wird nicht verarbeitet (Korrektur = neuer Beleg, kein Wiederbeleben).
FREIGEBEN ist ein Tor (Recht FREIGEBEN in api/permissions): der Trigger erzwingt
zusätzlich, dass zur Freigabe jede Betragsposition kontiert ist (Buchungskonto)
und mindestens eine Position existiert.

Kein Löschen (GoBD): weder receipt noch die Historie werden physisch gelöscht.
Vor der Buchung ist die Korrektur = ABGELEHNT + neuer Beleg; ein Storno-Folgebeleg
für bereits GEBUCHTE Eingangsbelege ist in der Roadmap NICHT belegt und wird
deshalb NICHT gebaut (siehe Bericht). Positionen sind nur in ERFASST/GEPRUEFT
veränderbar (Muster invoicing.protect_invoice_children); ab FREIGEGEBEN sind
Kopf (Lieferant/Daten/Beträge) und Positionen unveränderlich.

Beträge werden serverseitig aus den Positionen gerechnet (Service
belegerfassung.py, Muster beleg.py::_prepare_lines): Decimal, ROUND_HALF_UP,
Steuer je Steuergruppe gerundet. Der DB-CHECK net_amount = round(quantity *
unit_price, 2) und gross_total = net_total + tax_total sichern die Konsistenz.
Steuersatz-Codeliste: die BESTEHENDE invoicing.tax_code wird per FK
wiederverwendet (keine zweite Steuerliste).
"""
from django.db import migrations

CREATE_SQL = r"""
-- Interne Erfassungs-/Ablagenummer, eigene Sequenz (kein Ausgangsbelegkreis).
CREATE SEQUENCE accounting.receipt_number_seq;

-- ---------------------------------------------------------------------------
-- Eingangsbeleg (Kopf)
-- ---------------------------------------------------------------------------
CREATE TABLE accounting.receipt (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_number           text NOT NULL UNIQUE
                             DEFAULT ('EB-' || lpad(nextval('accounting.receipt_number_seq')::text, 5, '0'))
                             CHECK (receipt_number ~ '^EB-[0-9]{5,}$'),
    -- Lieferant: Pflicht. FK auf identity.party (Person ODER Organisation).
    supplier_party_id        uuid NOT NULL REFERENCES identity.party (id),
    -- Rechnungsnummer des Lieferanten (dessen Belegkreis, nicht unserer).
    supplier_invoice_number  text NULL,
    receipt_date             date NOT NULL,   -- Belegdatum (Datum der Lieferantenrechnung)
    received_date            date NOT NULL    -- Eingangsdatum bei uns
                             DEFAULT (now() AT TIME ZONE 'UTC')::date,
    due_date                 date NULL,       -- Fälligkeit (manuell)
    currency                 char(3) NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$'),
    -- Serverseitig aus den Positionen berechnet (Client liefert keine Summen).
    net_total                numeric(15, 2) NOT NULL DEFAULT 0 CHECK (net_total >= 0),
    tax_total                numeric(15, 2) NOT NULL DEFAULT 0 CHECK (tax_total >= 0),
    gross_total              numeric(15, 2) NOT NULL DEFAULT 0 CHECK (gross_total >= 0),
    status                   text NOT NULL DEFAULT 'ERFASST' CHECK (status IN
                             ('ERFASST', 'GEPRUEFT', 'FREIGEGEBEN', 'GEBUCHT', 'ABGELEHNT')),
    rejection_reason         text NULL,
    notes                    text NULL,
    created_by               uuid NOT NULL REFERENCES security.app_user (id),
    version                  integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT receipt_due_after_receipt_date
        CHECK (due_date IS NULL OR due_date >= receipt_date),
    CONSTRAINT receipt_gross_consistent
        CHECK (gross_total = net_total + tax_total),
    -- Ablehnung ist begründungspflichtig.
    CONSTRAINT receipt_rejection_needs_reason
        CHECK (status <> 'ABGELEHNT' OR btrim(coalesce(rejection_reason, '')) <> '')
);

CREATE INDEX idx_receipt_status ON accounting.receipt (status);
CREATE INDEX idx_receipt_supplier ON accounting.receipt (supplier_party_id);
CREATE INDEX idx_receipt_dates ON accounting.receipt (receipt_date, due_date);

-- ---------------------------------------------------------------------------
-- Eingangsbeleg-Position
-- ---------------------------------------------------------------------------
CREATE TABLE accounting.receipt_line (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_id         uuid NOT NULL REFERENCES accounting.receipt (id),
    position_number    integer NOT NULL CHECK (position_number > 0),
    description        text NOT NULL CHECK (btrim(description) <> ''),
    quantity           numeric(15, 3) NOT NULL CHECK (quantity > 0),
    unit               text NULL,
    unit_price         numeric(15, 2) NOT NULL CHECK (unit_price >= 0),
    -- Steuersatz aus der BESTEHENDEN Codeliste invoicing.tax_code (keine zweite).
    tax_code           text NOT NULL REFERENCES invoicing.tax_code (code),
    tax_rate_percent   numeric(5, 2) NOT NULL CHECK (tax_rate_percent >= 0),
    net_amount         numeric(15, 2) NOT NULL,
    -- Kontierung: Buchungskonto und Kostenstelle je Position (0030).
    ledger_account_id  uuid NULL REFERENCES accounting.ledger_account (id),
    cost_center_id     uuid NULL REFERENCES accounting.cost_center (id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (receipt_id, position_number),
    CONSTRAINT receipt_line_net_matches
        CHECK (net_amount = round(quantity * unit_price, 2))
);

CREATE INDEX idx_receipt_line_receipt ON accounting.receipt_line (receipt_id);
CREATE INDEX idx_receipt_line_ledger ON accounting.receipt_line (ledger_account_id)
    WHERE ledger_account_id IS NOT NULL;
CREATE INDEX idx_receipt_line_cost_center ON accounting.receipt_line (cost_center_id)
    WHERE cost_center_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Statusautomat
-- ---------------------------------------------------------------------------
CREATE FUNCTION accounting.enforce_receipt_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;
    IF NOT (
           (OLD.status = 'ERFASST'     AND NEW.status IN ('GEPRUEFT', 'ABGELEHNT'))
        OR (OLD.status = 'GEPRUEFT'    AND NEW.status IN ('ERFASST', 'FREIGEGEBEN', 'ABGELEHNT'))
        OR (OLD.status = 'FREIGEGEBEN' AND NEW.status IN ('GEPRUEFT', 'GEBUCHT'))
    ) THEN
        RAISE EXCEPTION
            'Eingangsbeleg %: Statuswechsel % -> % ist nicht zulässig '
            '(ERFASST -> GEPRUEFT -> FREIGEGEBEN -> GEBUCHT; '
            'ABGELEHNT aus ERFASST|GEPRUEFT; GEBUCHT und ABGELEHNT sind final)',
            NEW.receipt_number, OLD.status, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    -- Freigabe-Tor: kontierte Positionen (Buchungskonto je Betragszeile) und
    -- mindestens eine Position.
    IF NEW.status = 'FREIGEGEBEN' THEN
        IF NOT EXISTS (SELECT 1 FROM accounting.receipt_line WHERE receipt_id = NEW.id) THEN
            RAISE EXCEPTION
                'Eingangsbeleg %: Freigabe erfordert mindestens eine Position',
                NEW.receipt_number USING ERRCODE = 'raise_exception';
        END IF;
        IF EXISTS (SELECT 1 FROM accounting.receipt_line
                   WHERE receipt_id = NEW.id AND ledger_account_id IS NULL) THEN
            RAISE EXCEPTION
                'Eingangsbeleg %: Freigabe erfordert ein Buchungskonto je Position (Kontierung)',
                NEW.receipt_number USING ERRCODE = 'raise_exception';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- Ab FREIGEGEBEN/GEBUCHT/ABGELEHNT sind Kopf-Fachdaten unveränderlich; nur der
-- Statuswechsel selbst bleibt erlaubt (z. B. FREIGEGEBEN -> GEPRUEFT reaktiviert
-- die Bearbeitung wieder).
CREATE FUNCTION accounting.freeze_receipt() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('FREIGEGEBEN', 'GEBUCHT', 'ABGELEHNT') THEN
        IF NEW.supplier_party_id       IS DISTINCT FROM OLD.supplier_party_id
        OR NEW.supplier_invoice_number IS DISTINCT FROM OLD.supplier_invoice_number
        OR NEW.receipt_date            IS DISTINCT FROM OLD.receipt_date
        OR NEW.received_date           IS DISTINCT FROM OLD.received_date
        OR NEW.due_date                IS DISTINCT FROM OLD.due_date
        OR NEW.currency                IS DISTINCT FROM OLD.currency
        OR NEW.net_total               IS DISTINCT FROM OLD.net_total
        OR NEW.tax_total               IS DISTINCT FROM OLD.tax_total
        OR NEW.gross_total             IS DISTINCT FROM OLD.gross_total
        THEN
            RAISE EXCEPTION
                'Eingangsbeleg %: nach Freigabe sind Lieferant, Daten und Beträge '
                'unveränderlich — nur der Statuswechsel bleibt erlaubt',
                OLD.receipt_number USING ERRCODE = 'raise_exception';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_receipt_initial_status
    BEFORE INSERT ON accounting.receipt
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('ERFASST');
CREATE TRIGGER trg_receipt_updated_at
    BEFORE UPDATE ON accounting.receipt
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_receipt_freeze
    BEFORE UPDATE ON accounting.receipt
    FOR EACH ROW EXECUTE FUNCTION accounting.freeze_receipt();
CREATE TRIGGER trg_receipt_status
    BEFORE UPDATE OF status ON accounting.receipt
    FOR EACH ROW EXECUTE FUNCTION accounting.enforce_receipt_status();
CREATE TRIGGER trg_receipt_status_log
    AFTER INSERT OR UPDATE OF status ON accounting.receipt
    FOR EACH ROW EXECUTE FUNCTION workflow.log_status_change('receipt');
CREATE TRIGGER trg_receipt_audit
    AFTER UPDATE ON accounting.receipt
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_receipt_no_delete
    BEFORE DELETE ON accounting.receipt
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_receipt_no_truncate
    BEFORE TRUNCATE ON accounting.receipt
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON accounting.receipt FROM PUBLIC;

-- Der verknüpfte Lieferant darf nicht zusammengeführt (MERGED) worden sein.
CREATE TRIGGER trg_receipt_supplier_no_merged
    BEFORE INSERT OR UPDATE ON accounting.receipt
    FOR EACH ROW EXECUTE FUNCTION identity.assert_parties_not_merged('supplier_party_id');

-- Positionen sind nur veränderbar, solange der Beleg in ERFASST/GEPRUEFT steht.
CREATE FUNCTION accounting.protect_receipt_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status  text;
    v_receipt uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.receipt_id ELSE NEW.receipt_id END;
BEGIN
    -- FOR SHARE serialisiert Zeilenänderungen gegen einen laufenden Statuswechsel.
    SELECT status INTO v_status FROM accounting.receipt WHERE id = v_receipt FOR SHARE;
    IF v_status NOT IN ('ERFASST', 'GEPRUEFT') THEN
        RAISE EXCEPTION
            'Eingangsbeleg %: Positionen sind nur in ERFASST/GEPRUEFT veränderbar (aktuell %)',
            v_receipt, v_status USING ERRCODE = 'raise_exception';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_receipt_line_updated_at
    BEFORE UPDATE ON accounting.receipt_line
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_receipt_line_protect
    BEFORE INSERT OR UPDATE OR DELETE ON accounting.receipt_line
    FOR EACH ROW EXECUTE FUNCTION accounting.protect_receipt_lines();
CREATE TRIGGER trg_receipt_line_no_truncate
    BEFORE TRUNCATE ON accounting.receipt_line
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON accounting.receipt_line FROM PUBLIC;
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS accounting.receipt_line;
DROP TABLE IF EXISTS accounting.receipt;
DROP FUNCTION IF EXISTS accounting.protect_receipt_lines();
DROP FUNCTION IF EXISTS accounting.freeze_receipt();
DROP FUNCTION IF EXISTS accounting.enforce_receipt_status();
DROP SEQUENCE IF EXISTS accounting.receipt_number_seq;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0030_accounting_konten_kostenstellen"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
