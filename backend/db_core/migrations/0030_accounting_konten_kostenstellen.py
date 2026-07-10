"""Neues Fachschema accounting.* — Buchungskonten und Kostenstellen (TEIL A).

Hand-SQL nach db/README.md: neues Fachschema + Tabellen entstehen als
Django-Migration mit RunSQL und erben den Schutzstandard (No-Delete/Audit/
No-Truncate). Muster: hr.* (0019), maintenance.* (0016).

Schema-Entscheidung (die Roadmap ließ die Zuordnung invoicing vs. billing
ausdrücklich OFFEN, siehe docs/roadmap/09-buchhaltung.md „Offene Punkte"):
ein eigenes Schema `accounting` statt Anreicherung von `invoicing`. Begründung:
`invoicing` ist die GoBD-gesicherte AUSGANGSseite (Rechnung mit Belegkreis,
Snapshot/Hash, Festschreibung). Buchungskonten, Kostenstellen und Eingangsbelege
(0031) sind die EINGANGS-/Kontierungsseite mit anderer Nummern- und Statuslogik.
Ein getrenntes Schema hält die Ausgangsseite regressionsfrei und bündelt die
Kontierungs-Stammdaten dort, wo die Eingangsbelege sie brauchen.

Kontenrahmen: die Roadmap nennt SKR03/SKR04 nur als in den Einstellungen
WÄHLBAREN Rahmen — sie legt KEINEN konkreten Kontenplan (Kontennummern) fest.
Deshalb wird KEIN Kontenrahmen geseedet (nichts erfunden); es entsteht nur die
Struktur. `chart_of_accounts` markiert optional die Rahmen-Zugehörigkeit eines
Kontos; `account_type` ist die buchhalterische GRUNDklassifikation
(Bestands-/Erfolgskonten), nicht ein spezifischer Kontenplan.

Kostenstelle: die Roadmap etabliert KEINE feste Verknüpfung der Kostenstelle mit
Projekt/Liegenschaft (die Kostenträger-Grundlage liegt in billing.*). Deshalb ist
`cost_center` bewusst freistehende Stammdaten; die Zuordnung erfolgt je
Beleg-Position (0031, receipt_line.cost_center_id), nicht am Stammsatz.

Kein Löschen (GoBD/Historienschutz): Konten/Kostenstellen werden über das
`active`-Flag ARCHIVIERT, nie physisch gelöscht — historische Belege behalten so
ihre Kontierung. Schutzstandard (No-Delete/No-Truncate/Audit) wie bei allen
Fachtabellen.
"""
from django.db import migrations

CREATE_SQL = r"""
CREATE SCHEMA accounting;

-- ---------------------------------------------------------------------------
-- Buchungskonto (Konto im Kontenrahmen)
-- ---------------------------------------------------------------------------
CREATE TABLE accounting.ledger_account (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_number     text NOT NULL UNIQUE CHECK (btrim(account_number) <> ''),
    label              text NOT NULL CHECK (btrim(label) <> ''),
    -- Buchhalterische Grundklassifikation (Bestands-/Erfolgskonten). KEIN
    -- spezifischer Kontenplan — der wird nicht erfunden (siehe Docstring).
    account_type       text NOT NULL CHECK (account_type IN
                       ('AKTIV', 'PASSIV', 'AUFWAND', 'ERTRAG')),
    -- Optionaler Rahmen-Bezug; die Roadmap nennt nur SKR03/SKR04 als Auswahl.
    chart_of_accounts  text NULL CHECK (chart_of_accounts IN ('SKR03', 'SKR04')),
    active             boolean NOT NULL DEFAULT true,
    notes              text NULL,
    created_by         uuid NOT NULL REFERENCES security.app_user (id),
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_ledger_account_active ON accounting.ledger_account (active);

CREATE TRIGGER trg_ledger_account_updated_at
    BEFORE UPDATE ON accounting.ledger_account
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_ledger_account_audit
    AFTER UPDATE ON accounting.ledger_account
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_ledger_account_no_delete
    BEFORE DELETE ON accounting.ledger_account
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_ledger_account_no_truncate
    BEFORE TRUNCATE ON accounting.ledger_account
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON accounting.ledger_account FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Kostenstelle (freistehende Stammdaten; Zuordnung je Beleg-Position)
-- ---------------------------------------------------------------------------
CREATE TABLE accounting.cost_center (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code         text NOT NULL UNIQUE CHECK (btrim(code) <> ''),
    label        text NOT NULL CHECK (btrim(label) <> ''),
    active       boolean NOT NULL DEFAULT true,
    notes        text NULL,
    created_by   uuid NOT NULL REFERENCES security.app_user (id),
    version      integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_cost_center_active ON accounting.cost_center (active);

CREATE TRIGGER trg_cost_center_updated_at
    BEFORE UPDATE ON accounting.cost_center
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_cost_center_audit
    AFTER UPDATE ON accounting.cost_center
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_cost_center_no_delete
    BEFORE DELETE ON accounting.cost_center
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_cost_center_no_truncate
    BEFORE TRUNCATE ON accounting.cost_center
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON accounting.cost_center FROM PUBLIC;
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS accounting.cost_center;
DROP TABLE IF EXISTS accounting.ledger_account;
DROP SCHEMA IF EXISTS accounting;
"""


class Migration(migrations.Migration):

    dependencies = [
        # Reservierte Nummer 0030; ein paralleler Zweig belegt 0028/0029. Diese
        # Migration hängt fachlich nur an der vorhandenen Baseline-Kette (0027);
        # bei der Zusammenführung entstehen zwei Blätter, die per Merge-Migration
        # zusammengeführt werden (Standard bei Parallel-Entwicklung).
        ("db_core", "0027_appointmentcategory_jobresource_resource"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
