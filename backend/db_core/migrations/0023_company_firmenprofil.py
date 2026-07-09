"""Neues Fachschema company.* — Firmenprofil, Niederlassung, Gewerk-Katalog.

Hand-SQL nach db/README.md: neues Fachschema + Tabellen als Django-Migration
mit RunSQL, Schutzstandard (updated_at/Audit/No-Delete/No-Truncate/REVOKE).
Muster: 0019_hr_personal.py.

Fachquelle: docs/roadmap/13-firmeneinstellungen.md (Firmenprofil-Tabs
Allgemein/Kontakt/Bank, Niederlassungen, Gewerke). Das Firmenprofil ist die
**eine Wahrheit** über das ausstellende Unternehmen — es ersetzt u. a. den
Aussteller-Platzhalter im Beleg-PDF (services/beleg_pdf.py).

Grundsatzentscheidungen:

1. Eigenes Schema `company` statt Erweiterung von `security`. `security`
   beantwortet „darf ein Account etwas?"; `company` trägt die
   Unternehmens-Stammdaten (Identität, Kontakt, Bank, Gewerke). Andere
   Lebenszyklen, andere Rechte (jeder darf das Profil LESEN — es steht auf jedem
   Beleg —, nur ADMINISTRATION/GESCHAEFTSFUEHRUNG ändern; Rechte in 0024).

2. **Singleton company_profile.** Es gibt genau ein ausstellendes Unternehmen.
   Erzwungen NICHT über `CHECK (id = 1)`, sondern über eine boolesche
   Singleton-Spalte: `is_singleton boolean DEFAULT true`, `UNIQUE (is_singleton)`
   und `CHECK (is_singleton)`. Damit kann höchstens eine Zeile existieren.
   Bewusst gegen die naheliegende `id = 1`-Variante entschieden, weil der
   generische Audit-Trigger `audit.audit_row_update` die Zeilen-id per
   `(to_jsonb(NEW) ->> 'id')::uuid` protokolliert — eine `smallint`-id `1` würde
   den ::uuid-Cast sprengen und jedes UPDATE mit einem Audit-Fehler abbrechen.
   Ein `uuid`-PK hält den Schutzstandard (Audit) voll funktionsfähig; die
   Singleton-Spalte übernimmt die Ein-Zeilen-Garantie.

3. **Gewerk-Katalog neu (company.trade).** Recherche: einen echten Gewerk-Katalog
   gibt es im Schema NICHT. `workflow.project_category` (0043) ist eine
   Projekt-/Pipeline-Kategorie (Name/Farbe/Sortierung/Status) — die Auswertungen
   nähern „Gewerk" nur behelfsweise darüber an (services/auswertungen.py sagt
   explizit „ein echtes Gewerk-Feld gibt es im Schema nicht"). Ein Gewerk
   (Sanitär, Elektro, …) ist fachlich etwas anderes als ein Projekttyp. Deshalb
   wird `company.trade` als erste echte Gewerk-Wahrheit angelegt, NICHT an
   project_category angeklebt. Geseedet werden branchenübliche Handwerks-Gewerke
   (klassifizierende Stammdaten, keine erfundenen Firmenfakten).

Bankverbindung sind FIRMEN-Bankdaten (IBAN/BIC des ausstellenden Unternehmens),
nicht Mitarbeiter-Bankdaten — daher unkritischer als die in 0019 bewusst
ausgeklammerten Personal-Bankdaten. Die Roadmap nennt für Bankdaten-Änderungen
ein Vier-Augen-Prinzip (four_eyes 'BANKDATEN'); dessen app-seitige Durchsetzung
hängt wie bei HR am noch nicht gebauten Vier-Augen-Flow und ist hier NICHT
umgesetzt (auditiert wird die Änderung ohnehin).
"""
from django.db import migrations

CREATE_SQL = r"""
CREATE SCHEMA company;

-- ---------------------------------------------------------------------------
-- Firmenprofil (Singleton) — Identität, Anschrift, Kontakt, Steuer, Bank, GF
-- ---------------------------------------------------------------------------
CREATE TABLE company.company_profile (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Singleton-Garantie: höchstens eine Zeile (siehe Docstring).
    is_singleton       boolean NOT NULL DEFAULT true,
    -- Identität
    company_name       text NOT NULL CHECK (btrim(company_name) <> ''),
    legal_form         text NULL,          -- Rechtsform (GmbH, e.K., …)
    -- Anschrift
    street             text NULL,
    postal_code        text NULL,
    city               text NULL,
    country            char(2) NOT NULL DEFAULT 'DE' CHECK (country ~ '^[A-Z]{2}$'),
    -- Bundesland (steuert Feiertage in der Planung; ISO-3166-2-DE-Kürzel, z. B. 'BY')
    state_code         text NULL,
    -- Kontakt
    phone              text NULL,
    email              text NULL,
    web                text NULL,
    -- Steuer / Register
    tax_number         text NULL,          -- Steuernummer
    vat_id             text NULL,          -- USt-IdNr.
    commercial_register text NULL,         -- Handelsregister (z. B. 'HRB 12345, AG Musterstadt')
    -- Bankverbindung (Firma)
    bank_name          text NULL,
    iban               text NULL,
    bic                text NULL,
    -- Geschäftsführung: Name + frei wählbare Bezeichnung (fließt in Fußzeilen)
    managing_director  text NULL,
    managing_director_title text NULL,
    -- Standard-Anzeigesprache
    default_language   char(2) NOT NULL DEFAULT 'de' CHECK (default_language ~ '^[a-z]{2}$'),
    -- Optionale Logo-Referenz auf content.file (MinIO-Anbindung ist späterer
    -- Slice). Bewusst ohne harten FK, damit dieses Schema self-contained bleibt
    -- und das Profil ohne Content-Modul pflegbar ist.
    logo_file_id       uuid NULL,
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT company_profile_singleton_unique UNIQUE (is_singleton),
    CONSTRAINT company_profile_singleton_true CHECK (is_singleton)
);

CREATE TRIGGER trg_company_profile_updated_at
    BEFORE UPDATE ON company.company_profile
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_company_profile_audit
    AFTER UPDATE ON company.company_profile
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_company_profile_no_delete
    BEFORE DELETE ON company.company_profile
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_company_profile_no_truncate
    BEFORE TRUNCATE ON company.company_profile
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON company.company_profile FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Niederlassung
-- ---------------------------------------------------------------------------
CREATE TABLE company.branch (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL CHECK (btrim(name) <> ''),
    street       text NULL,
    postal_code  text NULL,
    city         text NULL,
    country      char(2) NOT NULL DEFAULT 'DE' CHECK (country ~ '^[A-Z]{2}$'),
    phone        text NULL,
    email        text NULL,
    -- Deaktivieren statt Löschen (GoBD/No-Delete; historische Bezüge bleiben).
    active       boolean NOT NULL DEFAULT true,
    version      integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_branch_active ON company.branch (active);

CREATE TRIGGER trg_branch_updated_at
    BEFORE UPDATE ON company.branch
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_branch_audit
    AFTER UPDATE ON company.branch
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_branch_no_delete
    BEFORE DELETE ON company.branch
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_branch_no_truncate
    BEFORE TRUNCATE ON company.branch
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON company.branch FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Gewerk-Katalog (erste echte Gewerk-Wahrheit; siehe Docstring)
-- ---------------------------------------------------------------------------
CREATE TABLE company.trade (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code         text NOT NULL UNIQUE CHECK (code ~ '^[A-Z0-9_]{2,}$'),
    label        text NOT NULL CHECK (btrim(label) <> ''),
    active       boolean NOT NULL DEFAULT true,
    sort_order   integer NOT NULL DEFAULT 0,
    version      integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_trade_active ON company.trade (active);

CREATE TRIGGER trg_trade_updated_at
    BEFORE UPDATE ON company.trade
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_trade_audit
    AFTER UPDATE ON company.trade
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_trade_no_delete
    BEFORE DELETE ON company.trade
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_trade_no_truncate
    BEFORE TRUNCATE ON company.trade
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON company.trade FROM PUBLIC;

-- Branchenübliche Handwerks-/Gebäudeservice-Gewerke (klassifizierende
-- Stammdaten). Kein erfundener Firmenbezug — nur der Katalog, aus dem die Firma
-- später ihre Gewerke wählt.
INSERT INTO company.trade (code, label, sort_order) VALUES
    ('SHK',        'Sanitär, Heizung, Klima', 10),
    ('ELEKTRO',    'Elektrotechnik',          20),
    ('MALER',      'Maler und Lackierer',     30),
    ('TROCKENBAU', 'Trockenbau',              40),
    ('FLIESEN',    'Fliesen-, Platten- und Mosaikleger', 50),
    ('ZIMMEREI',   'Zimmerei und Holzbau',    60),
    ('DACH',       'Dachdeckerei',            70),
    ('METALLBAU',  'Metallbau',               80),
    ('GARTEN',     'Garten- und Landschaftsbau', 90),
    ('GEBAEUDEREINIGUNG', 'Gebäudereinigung', 100);
"""

DROP_SQL = r"""
DROP TABLE IF EXISTS company.trade;
DROP TABLE IF EXISTS company.branch;
DROP TABLE IF EXISTS company.company_profile;
DROP SCHEMA IF EXISTS company;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0022_role_rolepermission_userrole"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md).
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
