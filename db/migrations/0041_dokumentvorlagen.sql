-- Migration 0041: Dokumentvorlagen (Produktvision 2026-07-05 — Vorlagen mit
-- Platzhaltern, vom Nutzer in den Einstellungen konfigurierbar).
--
-- Eine Vorlage liefert die Abschnittsstruktur (bloecke wie im Bericht:
-- ueberschrift + text) mit Platzhaltern ({{benutzer.name}}, {{projekt.name}},
-- {{einsatz.nummer}}, {{datum}} …). Die Auflösung passiert beim INSTANZIIEREN
-- (Schnappschuss-Prinzip wie bei Checklisten-Vorlagen, 0035): spätere
-- Vorlagenänderungen verändern keine bestehenden Dokumente.
--
-- Vorlagen sind nur für freie Dokumenttypen zulässig — Belegdokumente
-- (Angebot/Rechnung/Gutschrift) entstehen aus Belegen, nie aus Vorlagen.

BEGIN;

CREATE TABLE content.document_template (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL CHECK (btrim(name) <> ''),
    document_type text NOT NULL CHECK (document_type IN
                  ('EINSATZBERICHT', 'WARTUNGSBERICHT', 'PROTOKOLL',
                   'SCHRIFTVERKEHR', 'SONSTIGES')),
    status        text NOT NULL DEFAULT 'AKTIV' CHECK (status IN ('AKTIV', 'INAKTIV')),
    -- Versionszähler für Nebenläufigkeit (Muster wage_group: 409 bei veralteter Version)
    version       integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    bloecke       jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(bloecke) = 'array'),
    created_by    uuid NOT NULL REFERENCES security.app_user (id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Namen aktiver Vorlagen sind eindeutig (inaktive dürfen den Namen freigeben)
CREATE UNIQUE INDEX uq_document_template_name
    ON content.document_template (lower(name)) WHERE status = 'AKTIV';

CREATE TRIGGER trg_document_template_updated_at
    BEFORE UPDATE ON content.document_template
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

COMMIT;

-- Rückwärtsstrategie: DROP TABLE content.document_template;
