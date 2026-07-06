-- Migration 0016: Beleg-Infrastruktur — Steuercodes, Belegkreise, Angebots-Statusautomat
-- Beschlüsse: B-11/B-12/B-13 (Kreise AN/RE/GS), B-15 (Angebotsstatus), B-18 (Steuercode-Struktur,
--             Inhalte mit STB-Vorbehalt), B-14 (Belegnummer erst bei Veröffentlichung)

BEGIN;

CREATE SCHEMA invoicing;

-- ---------------------------------------------------------------------------
-- Belegkreise AN/RE/GS ergänzen (B-11). Vergabe weiterhin über workflow.next_number;
-- RE/GS werden ausschließlich bei Veröffentlichung gezogen (B-13/B-14) — Veröffentlichung
-- in kurzen Transaktionen halten, damit keine unbeabsichtigten Lücken entstehen.
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.number_range DROP CONSTRAINT number_range_prefix_check;
ALTER TABLE workflow.number_range ADD CONSTRAINT number_range_prefix_check
    CHECK (prefix IN ('V', 'P', 'AU', 'E', 'AN', 'RE', 'GS'));

-- Angebots-Statusautomat (B-15)
ALTER TABLE workflow.status_transition DROP CONSTRAINT status_transition_entity_check;
ALTER TABLE workflow.status_transition ADD CONSTRAINT status_transition_entity_check
    CHECK (entity IN ('service_case', 'work_order', 'service_job', 'quote'));

INSERT INTO workflow.status_transition (entity, from_status, to_status, requires_reason) VALUES
    ('quote', 'ENTWURF',         'INTERN_GEPRUEFT', false),
    ('quote', 'INTERN_GEPRUEFT', 'ENTWURF',         true),
    ('quote', 'INTERN_GEPRUEFT', 'FREIGEGEBEN',     false),
    ('quote', 'FREIGEGEBEN',     'ENTWURF',         true),
    ('quote', 'FREIGEGEBEN',     'VERSENDET',       false),
    ('quote', 'VERSENDET',       'ANGENOMMEN',      false),
    ('quote', 'VERSENDET',       'ABGELEHNT',       false),
    ('quote', 'VERSENDET',       'ABGELAUFEN',      false),
    ('quote', 'VERSENDET',       'ERSETZT',         true),
    ('quote', 'ABGELAUFEN',      'ERSETZT',         true),
    ('quote', 'ABGELEHNT',       'ERSETZT',         true);

-- ---------------------------------------------------------------------------
-- Steuercodes (B-18): konfigurierbare Struktur mit Pflichttexten.
-- Die Startwerte sind KANDIDATEN und stehen unter STB-Vorbehalt (Vorbehalts-
-- Checkliste, Teil C des Entscheidungskatalogs); stb_confirmed_at dokumentiert
-- die vor Produktivbetrieb erforderliche schriftliche Bestätigung.
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.tax_code (
    code               text PRIMARY KEY,
    label              text NOT NULL,
    rate_percent       numeric(5, 2) NOT NULL CHECK (rate_percent >= 0),
    mandatory_text     text NULL,
    valid_from         date NOT NULL,
    valid_until        date NULL,
    stb_confirmed_at   date NULL,
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until IS NULL OR valid_until > valid_from)
);

CREATE TRIGGER trg_tax_code_updated_at
    BEFORE UPDATE ON invoicing.tax_code
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

INSERT INTO invoicing.tax_code (code, label, rate_percent, mandatory_text, valid_from) VALUES
    ('DE_19',  'Umsatzsteuer 19 %', 19.00, NULL, DATE '2026-01-01'),
    ('DE_7',   'Umsatzsteuer 7 %',   7.00, NULL, DATE '2026-01-01'),
    ('DE_0',   'Steuerfrei',         0.00, 'Steuerfreie Leistung. Pflichttext folgt nach STB-Bestätigung (B-18).', DATE '2026-01-01'),
    ('DE_13B', '§13b UStG Bauleistung (Steuerschuldnerschaft des Leistungsempfängers)', 0.00,
     'Steuerschuldnerschaft des Leistungsempfängers gemäß §13b UStG. Pflichttext-Wortlaut nach STB-Bestätigung (B-18).', DATE '2026-01-01');

COMMIT;

-- Rückwärtsstrategie: Statusübergänge und Steuercodes entfernen, Constraints
-- zurücksetzen, Schema löschen — nur solange keine Belege entstanden sind.
