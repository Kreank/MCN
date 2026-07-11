"""Akquisekanäle/Quellen: Katalog `company.acquisition_source` + Party-Bezug.

Hand-SQL nach db/README.md (Fachschema-Änderung als Django-RunSQL, kein ORM-DDL).
Fachquelle: „Wie ist der Kunde auf uns gekommen?" — für einen Handwerksbetrieb
eine wichtige Marketing-Kennzahl (Empfehlung/Website/Messe …).

Der Katalog spiegelt den Gewerk-/Niederlassungs-Katalog (company.trade, 0023):
natürlicher Code, Label, active-Flag, sort_order, Schutzstandard (updated_at/
Audit/No-Delete/No-Truncate/REVOKE) — kein Löschen, nur Deaktivieren. Zusätzlich
bekommt identity.party eine nullbare Quelle (gilt für Personen UND Organisationen;
schemaübergreifender FK ist in PostgreSQL zulässig). Ein paar gängige Kanäle sind
als Startdaten hinterlegt.

Reverse entfernt die Spalte und den Katalog wieder.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE company.acquisition_source (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code         text NOT NULL UNIQUE CHECK (code ~ '^[A-Z0-9_]{2,}$'),
    label        text NOT NULL CHECK (btrim(label) <> ''),
    active       boolean NOT NULL DEFAULT true,
    sort_order   integer NOT NULL DEFAULT 0,
    version      integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_acquisition_source_active ON company.acquisition_source (active);

CREATE TRIGGER trg_acquisition_source_updated_at
    BEFORE UPDATE ON company.acquisition_source
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_acquisition_source_audit
    AFTER UPDATE ON company.acquisition_source
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_acquisition_source_no_delete
    BEFORE DELETE ON company.acquisition_source
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_acquisition_source_no_truncate
    BEFORE TRUNCATE ON company.acquisition_source
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON company.acquisition_source FROM PUBLIC;

INSERT INTO company.acquisition_source (code, label, sort_order) VALUES
    ('EMPFEHLUNG',    'Empfehlung',            10),
    ('WEBSITE',       'Website',               20),
    ('SUCHMASCHINE',  'Suchmaschine',          30),
    ('TELEFON',       'Telefonanruf',          40),
    ('MESSE',         'Messe / Veranstaltung', 50),
    ('BESTANDSKUNDE', 'Bestandskunde',         60),
    ('SONSTIGE',      'Sonstige',              90);

ALTER TABLE identity.party
    ADD COLUMN acquisition_source_id uuid REFERENCES company.acquisition_source (id);
"""

REVERSE_SQL = r"""
ALTER TABLE identity.party DROP COLUMN acquisition_source_id;
DROP TABLE IF EXISTS company.acquisition_source;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0048_property_type_efh"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
