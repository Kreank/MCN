"""Vier-Augen-Prinzip: Antrag/Freigabe-Modell (security.approval_request).

Bis zu diesem Slice war `security.four_eyes_action` (Migration 0026 db/) reine
Stammdatenliste — sie wurde NIRGENDS ausgewertet. Diese Migration liefert die
physische Durchsetzung des Vier-Augen-Prinzips: eine Vier-Augen-pflichtige
Änderung wird nicht mehr direkt geschrieben, sondern als Antrag angelegt, den ein
ZWEITER Mensch (bzw. dieselbe KI durch dasselbe Tor) genehmigen muss.

Kernregel physisch (CHECK `approval_four_eyes`): `decided_by <> requested_by`.
Wer beantragt, kann nicht selbst freigeben — das ist der Kern des Prinzips und
liegt bewusst in der Datenbank, nicht nur im Service. (Der Service prüft es
zusätzlich, damit der Aufrufer einen klaren 422 statt eines IntegrityError sieht.)

Zielobjekt-Referenz: bewusst polymorph (`target_table` + `target_id`), NICHT als
harte FKs. Begründung: das Freigabemodell ist ein Querschnitt über viele Schemata
(company.company_profile, invoicing.invoice, künftig identity-Merge, hr-Bankdaten,
KI-Massenaktionen). Ein FK je Zieltabelle würde die Tabelle an jede Fachtabelle
koppeln und bei MASSENEXPORT/KI_MASSENAKTION ganz fehlschlagen (kein einzelnes
Ziel). Die Integrität des Ziels prüft der jeweilige Anwendungspfad beim Anwenden
(assert_approved/Applier), nicht die Fremdschlüsselebene.

`payload jsonb` trägt die beantragte Änderung (z. B. die neuen Bankdaten oder die
zu korrigierenden Positionsnummern). `applied_at` markiert den Verbrauch einer
Genehmigung (Einmaligkeit): eine genehmigte Bankdatenänderung wird beim Genehmigen
angewandt, eine genehmigte Rechnungskorrektur beim tatsächlichen Korrekturlauf —
in beiden Fällen darf dieselbe Genehmigung nicht zweimal wirken.

Statusautomat (Trigger, Muster hr.absence 0019): ANGEFORDERT -> GENEHMIGT |
ABGELEHNT | ZURUECKGEZOGEN; Endzustände sind final. Ablehnung ist
begründungspflichtig (CHECK, Muster hr.absence). Schutzstandard geerbt
(updated_at/audit/no-delete/no-truncate/REVOKE).
"""
from django.db import migrations

CREATE_SQL = r"""
CREATE TABLE security.approval_request (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_code    text NOT NULL REFERENCES security.four_eyes_action (action_code),
    status         text NOT NULL DEFAULT 'ANGEFORDERT'
                   CHECK (status IN ('ANGEFORDERT', 'GENEHMIGT', 'ABGELEHNT',
                                     'ZURUECKGEZOGEN')),
    -- die beantragte Änderung (z. B. {"iban": "...", "bic": "..."} oder
    -- {"operation": "GUTSCHRIFT", "positions": [1, 2]}).
    payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- polymorphe Zielreferenz; beide zusammen gesetzt oder beide NULL.
    target_table   text NULL,
    target_id      uuid NULL,
    -- Begründung des Antragstellers (optional, informativ).
    reason         text NULL,
    requested_by   uuid NOT NULL REFERENCES security.app_user (id),
    requested_at   timestamptz NOT NULL DEFAULT now(),
    decided_by     uuid NULL REFERENCES security.app_user (id),
    decided_at     timestamptz NULL,
    decision_note  text NULL,
    -- Zeitpunkt des Verbrauchs (Einmaligkeit); nur bei GENEHMIGT gesetzt.
    applied_at     timestamptz NULL,
    version        integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    -- KERN des Vier-Augen-Prinzips: Antragsteller darf nicht selbst entscheiden.
    CONSTRAINT approval_four_eyes
        CHECK (decided_by IS NULL OR decided_by <> requested_by),
    -- Entscheidung und Entscheider bedingen einander.
    CONSTRAINT approval_decision_complete
        CHECK ((status IN ('GENEHMIGT', 'ABGELEHNT'))
               = (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
    -- Ablehnung ist begründungspflichtig.
    CONSTRAINT approval_rejection_needs_note
        CHECK (status <> 'ABGELEHNT' OR btrim(coalesce(decision_note, '')) <> ''),
    -- Zielreferenz nur als Paar.
    CONSTRAINT approval_target_pair
        CHECK ((target_table IS NULL) = (target_id IS NULL)),
    -- Verbrauch nur an einer erteilten Genehmigung.
    CONSTRAINT approval_applied_only_when_granted
        CHECK (applied_at IS NULL OR status = 'GENEHMIGT')
);

CREATE INDEX idx_approval_status ON security.approval_request (status);
CREATE INDEX idx_approval_action ON security.approval_request (action_code);
CREATE INDEX idx_approval_target ON security.approval_request (target_table, target_id)
    WHERE target_table IS NOT NULL;
-- offene Genehmigungen je Ziel schnell findbar (Torfunktion assert_approved).
CREATE INDEX idx_approval_open_grant
    ON security.approval_request (action_code, target_table, target_id)
    WHERE status = 'GENEHMIGT' AND applied_at IS NULL;

-- Statusautomat: Antrag beginnt als ANGEFORDERT, danach nur ein finaler Schritt.
CREATE FUNCTION security.enforce_approval_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;
    IF NOT (
        OLD.status = 'ANGEFORDERT'
        AND NEW.status IN ('GENEHMIGT', 'ABGELEHNT', 'ZURUECKGEZOGEN')
    ) THEN
        RAISE EXCEPTION
            'Freigabeantrag %: Statuswechsel % -> % ist nicht zulässig '
            '(ANGEFORDERT -> GENEHMIGT|ABGELEHNT|ZURUECKGEZOGEN)',
            NEW.id, OLD.status, NEW.status
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_approval_initial_status
    BEFORE INSERT ON security.approval_request
    FOR EACH ROW EXECUTE FUNCTION workflow.enforce_initial_status('ANGEFORDERT');
CREATE TRIGGER trg_approval_updated_at
    BEFORE UPDATE ON security.approval_request
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_approval_status
    BEFORE UPDATE OF status ON security.approval_request
    FOR EACH ROW EXECUTE FUNCTION security.enforce_approval_status();
CREATE TRIGGER trg_approval_audit
    AFTER UPDATE ON security.approval_request
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_approval_no_delete
    BEFORE DELETE ON security.approval_request
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_approval_no_truncate
    BEFORE TRUNCATE ON security.approval_request
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON security.approval_request FROM PUBLIC;
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS security.approval_request;
DROP FUNCTION IF EXISTS security.enforce_approval_status();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0027_appointmentcategory_jobresource_resource"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
