-- Migration 0026: Rechte-Stammdaten — Rollen, Rechtematrix, Vier-Augen-Aktionen
-- Beschlüsse: B-35 (Rollen), B-36 (Matrix als gepflegtes Stammdatendokument,
--             GF nimmt ab), B-37 (ein Betrieb; Monteure nur eigene Einsätze),
--             B-38 (Vier-Augen-Liste)
-- Ehrlichkeitshinweis: Die DURCHSETZUNG der Matrix erfolgt in der App-Schicht
-- (die Anwendung verbindet sich als technischer DB-Benutzer). Diese Migration
-- liefert die beschlossenen Stammdaten und deren Integrität; echte DB-Rollen-
-- trennung folgt mit dem Betriebskonzept (C-11).

BEGIN;

-- ---------------------------------------------------------------------------
-- security.role — beschlossene Rollen (B-35)
-- ---------------------------------------------------------------------------
CREATE TABLE security.role (
    code        text PRIMARY KEY,
    label       text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_role_updated_at
    BEFORE UPDATE ON security.role
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

INSERT INTO security.role (code, label) VALUES
    ('ADMINISTRATION',      'Administration'),
    ('GESCHAEFTSFUEHRUNG',  'Geschäftsführung'),
    ('DISPOSITION',         'Disposition'),
    ('TECHNISCHE_LEITUNG',  'Technische Leitung'),
    ('BUCHHALTUNG',         'Buchhaltung'),
    ('MONTEUR',             'Monteur'),
    ('NUR_LESEN',           'Nur-Lesen');

-- Rollen sind beschlossene Stammdaten; Entfernen nur per Migration nach Beschluss
CREATE TRIGGER trg_role_no_delete BEFORE DELETE ON security.role
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

-- ---------------------------------------------------------------------------
-- security.user_role — zeitabhängige Rollenzuordnung
-- ---------------------------------------------------------------------------
CREATE TABLE security.user_role (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES security.app_user (id),
    role_code    text NOT NULL REFERENCES security.role (code),
    valid_from   date NOT NULL,
    valid_until  date NULL,
    granted_by   uuid NOT NULL REFERENCES security.app_user (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    -- keine zeitgleiche Doppelzuordnung derselben Rolle
    CONSTRAINT excl_user_role_dup EXCLUDE USING gist (
        user_id WITH =,
        role_code WITH =,
        daterange(valid_from, valid_until) WITH &&
    )
);

-- Rollenzuordnungen werden beendet, nicht gelöscht (F-02-Linie); auditiert
CREATE TRIGGER trg_user_role_no_delete BEFORE DELETE ON security.user_role
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_user_role_audit AFTER UPDATE ON security.user_role
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();

-- ---------------------------------------------------------------------------
-- security.role_permission — Rechtematrix (B-36) als Stammdaten.
-- Startmatrix gemäß Beschluss; die GF nimmt die vollständige Matrix ab.
-- ---------------------------------------------------------------------------
CREATE TABLE security.role_permission (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role_code   text NOT NULL REFERENCES security.role (code),
    module      text NOT NULL CHECK (module IN
                ('identity', 'property', 'management', 'tenure', 'billing',
                 'workflow', 'invoicing', 'pricing', 'content', 'security', 'ai')),
    action      text NOT NULL CHECK (action IN
                ('LESEN', 'ANLEGEN', 'AENDERN', 'FREIGEBEN', 'VERSENDEN',
                 'STORNIEREN', 'EXPORTIEREN', 'LOESCHEN')),
    allowed     boolean NOT NULL,
    -- B-37: Zeilenbegrenzung (z. B. Monteur nur eigene Einsätze) als Kennzeichen;
    -- die Auswertung erfolgt in der App-Schicht
    row_scope   text NOT NULL DEFAULT 'ALLE' CHECK (row_scope IN ('ALLE', 'EIGENE')),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (role_code, module, action)
);
CREATE TRIGGER trg_role_permission_updated_at
    BEFORE UPDATE ON security.role_permission
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_role_permission_audit AFTER UPDATE ON security.role_permission
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();

-- Startmatrix (B-36): beschlossene Kernfestlegungen. Vollständige Feinmatrix wird
-- als Stammdaten gepflegt und durch die GF abgenommen.
INSERT INTO security.role_permission (role_code, module, action, allowed, row_scope)
SELECT r.code, m.module, a.action,
       CASE
           WHEN r.code = 'ADMINISTRATION' THEN true
           WHEN r.code = 'GESCHAEFTSFUEHRUNG' THEN true
           WHEN r.code = 'NUR_LESEN' THEN a.action = 'LESEN'
           WHEN a.action = 'EXPORTIEREN' THEN false                     -- nur GF/Admin (B-36/C-10)
           WHEN a.action = 'LOESCHEN' THEN false                        -- Historienschutz F-02
           WHEN r.code = 'MONTEUR' THEN
                (m.module = 'workflow' AND a.action IN ('LESEN', 'ANLEGEN', 'AENDERN'))
                OR (m.module = 'content' AND a.action IN ('LESEN', 'ANLEGEN'))
           WHEN r.code = 'DISPOSITION' THEN
                m.module IN ('identity', 'property', 'management', 'tenure', 'workflow', 'content')
                AND a.action IN ('LESEN', 'ANLEGEN', 'AENDERN', 'VERSENDEN')
           WHEN r.code = 'TECHNISCHE_LEITUNG' THEN
                (m.module IN ('identity', 'property', 'management', 'tenure', 'workflow', 'content')
                 AND a.action IN ('LESEN', 'ANLEGEN', 'AENDERN', 'FREIGEBEN', 'VERSENDEN'))
                OR (m.module IN ('billing', 'invoicing', 'pricing') AND a.action = 'LESEN')
           WHEN r.code = 'BUCHHALTUNG' THEN
                (m.module IN ('billing', 'invoicing', 'pricing')
                 AND a.action IN ('LESEN', 'ANLEGEN', 'AENDERN', 'FREIGEBEN', 'VERSENDEN', 'STORNIEREN'))
                OR (m.module IN ('identity', 'property', 'workflow', 'content') AND a.action = 'LESEN')
           ELSE false
       END,
       CASE WHEN r.code = 'MONTEUR' THEN 'EIGENE' ELSE 'ALLE' END
FROM security.role r
CROSS JOIN (VALUES ('identity'), ('property'), ('management'), ('tenure'), ('billing'),
                   ('workflow'), ('invoicing'), ('pricing'), ('content'), ('security'), ('ai')) AS m(module)
CROSS JOIN (VALUES ('LESEN'), ('ANLEGEN'), ('AENDERN'), ('FREIGEBEN'), ('VERSENDEN'),
                   ('STORNIEREN'), ('EXPORTIEREN'), ('LOESCHEN')) AS a(action);

-- Nicht-Admin-Rollen haben keine security-/ai-Schreibrechte (Härtung der Startmatrix)
UPDATE security.role_permission
SET allowed = false
WHERE module IN ('security', 'ai')
  AND role_code NOT IN ('ADMINISTRATION', 'GESCHAEFTSFUEHRUNG')
  AND action <> 'LESEN';

-- pricing.approval_threshold erhält jetzt einen echten Rollenbezug (B-16)
ALTER TABLE pricing.approval_threshold
    ADD CONSTRAINT fk_approval_threshold_role
    FOREIGN KEY (role_code) REFERENCES security.role (code);

-- ---------------------------------------------------------------------------
-- security.four_eyes_action — Vier-Augen-Pflichtaktionen (B-38)
-- ---------------------------------------------------------------------------
CREATE TABLE security.four_eyes_action (
    action_code  text PRIMARY KEY,
    label        text NOT NULL,
    active       boolean NOT NULL DEFAULT true,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_four_eyes_updated_at
    BEFORE UPDATE ON security.four_eyes_action
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_four_eyes_no_delete BEFORE DELETE ON security.four_eyes_action
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();

INSERT INTO security.four_eyes_action (action_code, label) VALUES
    ('RECHNUNGSKORREKTUR', 'Rechnungskorrektur/Storno nach Veröffentlichung'),
    ('DUBLETTEN_MERGE',    'Zusammenführung von Dubletten'),
    ('BANKDATEN',          'Änderung von Bankdaten'),
    ('MASSENEXPORT',       'Massenexport von Daten'),
    ('KI_MASSENAKTION',    'KI-Massenaktion'),
    ('ANGEBOT_GF_GRENZE',  'Angebot oberhalb der GF-Wertgrenze');

COMMIT;

-- Rückwärtsstrategie: DROP der Tabellen und des FK, nur solange keine
-- Rollenzuordnungen entstanden sind.
