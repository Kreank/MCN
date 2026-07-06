-- Migration 0021: Dateien (Object-Storage-Metadaten) und kontrollierte Verknüpfungen
-- Beschlüsse: Technische Standardrichtung (Object Storage für Dateien, CLAUDE.md),
--             B-29 (Foto als Dokumenttyp), B-30 (Unveränderlichkeit), F-02-Linie.
-- Binärdaten (Fotos, VIDEOS, PDFs, Scans) liegen im Object Storage; die Datenbank hält
-- den vollständigen Steckbrief inkl. SHA-256 und alle fachlichen Verknüpfungen.

BEGIN;

CREATE SCHEMA content;

-- ---------------------------------------------------------------------------
-- content.file — unveränderlicher Datei-Steckbrief
-- ---------------------------------------------------------------------------
CREATE TABLE content.file (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Schlüssel im Object Storage (Bucket-relativer Pfad); niemals wiederverwendet
    storage_key        text NOT NULL UNIQUE CHECK (btrim(storage_key) <> ''),
    original_filename  text NOT NULL CHECK (btrim(original_filename) <> ''),
    mime_type          text NOT NULL CHECK (mime_type ~ '^[a-z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$'),
    size_bytes         bigint NOT NULL CHECK (size_bytes >= 0),
    sha256             char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    -- Medien-Metadaten (Videos: Dauer/Auflösung/Codec; Bilder: Abmessungen; frei erweiterbar)
    media_metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
    uploaded_by        uuid NOT NULL REFERENCES security.app_user (id),
    uploaded_at        timestamptz NOT NULL DEFAULT now(),
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_file_sha256 ON content.file (sha256);

-- Dateiinhalte sind unveränderlich: Eine Korrektur ist eine neue Datei.
-- (Löschung folgt erst mit dem Aufbewahrungs-/Löschkonzept C-07/C-08.)
CREATE TRIGGER trg_file_immutable
    BEFORE UPDATE OR DELETE ON content.file
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_file_no_truncate
    BEFORE TRUNCATE ON content.file
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON content.file FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- content.file_link — kontrollierte Verknüpfung (echte FKs, genau ein Ziel;
-- keine freien polymorphen Fremdschlüssel, Domänenmodell §9)
-- ---------------------------------------------------------------------------
CREATE TABLE content.file_link (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id          uuid NOT NULL REFERENCES content.file (id),
    service_case_id  uuid NULL REFERENCES workflow.service_case (id),
    work_order_id    uuid NULL REFERENCES workflow.work_order (id),
    service_job_id   uuid NULL REFERENCES workflow.service_job (id),
    property_id      uuid NULL REFERENCES property.property (id),
    unit_id          uuid NULL REFERENCES property.unit (id),
    asset_id         uuid NULL REFERENCES property.technical_asset (id),
    quote_id         uuid NULL REFERENCES invoicing.quote (id),
    invoice_id       uuid NULL REFERENCES invoicing.invoice (id),
    party_id         uuid NULL REFERENCES identity.party (id),
    -- fachliche Einordnung, z. B. FOTO_VORHER, FOTO_NACHHER, VIDEO_BEGEHUNG, SCAN
    link_category    text NULL,
    created_by       uuid NOT NULL REFERENCES security.app_user (id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                        unit_id, asset_id, quote_id, invoice_id, party_id) = 1)
);

CREATE INDEX idx_file_link_file ON content.file_link (file_id);

COMMIT;

-- Rückwärtsstrategie: DROP der Tabellen, nur solange keine Dateien registriert sind.
-- Object-Storage-Objekte werden von Migrationen niemals berührt.
