-- Migration 0027: KI-Grundlagen — abgeleitete Inhalte, Embeddings, Läufe, Vorschläge
-- Beschlüsse: B-39–B-47, AGENT.md §5 (Freigabe an Payload-Hash, Zielversion, Benutzer,
--             Ablaufzeit gebunden), CLAUDE.md (KI ohne direkten Schreibzugriff; Embeddings
--             sind abgeleitete Daten; relationale DB ist die fachliche Wahrheit)
-- Prompt-Injection-Grundsatz: Alle hier gespeicherten Inhalte sind DATEN, niemals
-- Anweisungen. is_untrusted kennzeichnet Inhalte aus externen Quellen.

BEGIN;

CREATE SCHEMA ai;

-- ---------------------------------------------------------------------------
-- ai.content_item — extrahierter Text als abgeleitete Kopie (Modul I).
-- Quelle über kontrollierte FKs (genau eine); die Fachwahrheit bleibt im Quellmodul.
-- ---------------------------------------------------------------------------
CREATE TABLE ai.content_item (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type       text NOT NULL CHECK (source_type IN
                      ('EMAIL', 'PDF', 'EINSATZBERICHT', 'FOTO_BESCHREIBUNG',
                       'PROTOKOLL', 'SONSTIGES')),
    communication_id  uuid NULL REFERENCES content.communication (id),
    document_id       uuid NULL REFERENCES content.document (id),
    file_id           uuid NULL REFERENCES content.file (id),
    extracted_text    text NOT NULL,
    language          text NULL,
    content_hash      text NOT NULL,
    -- Inhalte aus externen Quellen sind untrusted (Prompt-Injection-Schutz);
    -- Retrieval filtert zusätzlich nach Benutzerrechten (B-40).
    is_untrusted      boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(communication_id, document_id, file_id) = 1)
);

-- ---------------------------------------------------------------------------
-- ai.embedding — abgeleitete Daten; bei Quelländerung nachvollziehbar neu erzeugt.
-- Vektortyp bewusst modellagnostisch (float4[]); die pgvector-Einführung mit fester
-- Dimension folgt erst nach der B-47-Modellauswahl per eigener Migration.
-- Als abgeleitete Daten sind Embeddings lösch- und neu aufbaubar (kein Löschverbot).
-- ---------------------------------------------------------------------------
CREATE TABLE ai.embedding (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_item_id    uuid NOT NULL REFERENCES ai.content_item (id) ON DELETE CASCADE,
    chunk_index        integer NOT NULL CHECK (chunk_index >= 0),
    chunk_text         text NOT NULL,
    embedding_model    text NOT NULL,
    embedding_version  text NOT NULL,
    vector             real[] NOT NULL,
    content_hash       text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (content_item_id, chunk_index, embedding_model, embedding_version)
);

-- content_item darf als abgeleitete Kopie gelöscht werden (z. B. bei Quellkorrektur);
-- Embeddings hängen per CASCADE daran. Das ist die dokumentierte Ausnahme vom
-- Löschverbot: fachliche Wahrheit liegt ausschließlich in den Quellmodulen.

-- ---------------------------------------------------------------------------
-- ai.ai_run — Protokoll jedes KI-Laufs (B-44/B-45); append-only
-- ---------------------------------------------------------------------------
CREATE TABLE ai.ai_run (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name            text NOT NULL,
    model_version         text NOT NULL,
    workflow_name         text NOT NULL,
    workflow_version      text NOT NULL,
    prompt_version        text NOT NULL,
    triggered_by_user_id  uuid NOT NULL REFERENCES security.app_user (id),
    permission_context    jsonb NOT NULL DEFAULT '{}'::jsonb,
    sources               jsonb NOT NULL DEFAULT '[]'::jsonb,
    tools_used            jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at            timestamptz NOT NULL DEFAULT now(),
    finished_at           timestamptz NULL,
    result_status         text NULL CHECK (result_status IN ('OK', 'FEHLER', 'ABBRUCH')),
    error_message         text NULL,
    resource_usage        jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK ((finished_at IS NULL) = (result_status IS NULL))
);

-- Läufe dürfen genau einmal abgeschlossen werden; sonst append-only
CREATE FUNCTION ai.guard_ai_run_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.finished_at IS NOT NULL THEN
        RAISE EXCEPTION 'ai_run %: abgeschlossene Läufe sind unveränderlich', OLD.id;
    END IF;
    IF (to_jsonb(NEW) - 'finished_at' - 'result_status' - 'error_message' - 'resource_usage')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'finished_at' - 'result_status' - 'error_message' - 'resource_usage') THEN
        RAISE EXCEPTION 'ai_run %: nur der Abschluss darf nachgetragen werden', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ai_run_guard
    BEFORE UPDATE ON ai.ai_run
    FOR EACH ROW EXECUTE FUNCTION ai.guard_ai_run_update();
CREATE TRIGGER trg_ai_run_no_delete
    BEFORE DELETE ON ai.ai_run
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_ai_run_no_truncate
    BEFORE TRUNCATE ON ai.ai_run
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON ai.ai_run FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- ai.ai_proposal — KI-Vorschlag ohne fachliche Wirkung (B-41, AGENT.md §5).
-- Freigabe ist an Payload-Hash, Zieltyp/-ID, Zielversion, freigebenden Benutzer
-- und Ablaufzeit gebunden. Die Ausführung erfolgt ausschließlich durch die
-- App-Schicht über die Fach-API — niemals durch die KI selbst.
-- ---------------------------------------------------------------------------
CREATE TABLE ai.ai_proposal (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ai_run_id            uuid NOT NULL REFERENCES ai.ai_run (id),
    proposal_type        text NOT NULL CHECK (btrim(proposal_type) <> ''),
    target_type          text NOT NULL CHECK (btrim(target_type) <> ''),
    target_id            uuid NOT NULL,
    target_version       integer NULL,
    proposed_payload     jsonb NOT NULL,
    payload_hash         text NOT NULL CHECK (btrim(payload_hash) <> ''),
    status               text NOT NULL DEFAULT 'PENDING'
                         CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')),
    expires_at           timestamptz NOT NULL,
    approved_by_user_id  uuid NULL REFERENCES security.app_user (id),
    approved_at          timestamptz NULL,
    rejection_reason     text NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'APPROVED') = (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)),
    CHECK (status <> 'REJECTED' OR rejection_reason IS NOT NULL)
);

CREATE INDEX idx_ai_proposal_status ON ai.ai_proposal (status, expires_at);

-- Statusdisziplin: PENDING -> APPROVED/REJECTED/EXPIRED; Freigabe nur vor Ablauf;
-- Inhalt (Payload, Hash, Ziel) ist nach Anlage unveränderlich.
CREATE FUNCTION ai.guard_ai_proposal() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'status' - 'approved_by_user_id' - 'approved_at' - 'rejection_reason')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'status' - 'approved_by_user_id' - 'approved_at' - 'rejection_reason') THEN
        RAISE EXCEPTION
            'ai_proposal %: Vorschlagsinhalt ist unveränderlich (AGENT.md §5)', OLD.id;
    END IF;
    -- Entscheidungsfelder sind nur zusammen mit ihrem Statusübergang änderbar;
    -- eine nachträgliche Manipulation von approved_at/-by ist unzulässig.
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        IF NEW.approved_at IS DISTINCT FROM OLD.approved_at
           OR NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
           OR NEW.rejection_reason IS DISTINCT FROM OLD.rejection_reason THEN
            RAISE EXCEPTION
                'ai_proposal %: Entscheidungsfelder sind nach der Entscheidung unveränderlich (AGENT.md §5)',
                OLD.id;
        END IF;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF OLD.status <> 'PENDING' THEN
            RAISE EXCEPTION
                'ai_proposal %: Nur offene Vorschläge wechseln den Status (% -> % unzulässig)',
                OLD.id, OLD.status, NEW.status;
        END IF;
        IF NEW.status = 'APPROVED' THEN
            -- HIGH-1-Fix: Bindung an die SERVERZEIT — ein rückdatiertes approved_at
            -- kann die Ablaufzeit nicht umgehen; die Freigabezeit wird serverseitig
            -- gesetzt und ist damit nicht fälschbar (AGENT.md §5).
            IF now() > NEW.expires_at THEN
                RAISE EXCEPTION
                    'ai_proposal %: Freigabe nach Ablaufzeit ist unzulässig (AGENT.md §5)', OLD.id;
            END IF;
            NEW.approved_at := now();
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ai_proposal_guard
    BEFORE UPDATE ON ai.ai_proposal
    FOR EACH ROW EXECUTE FUNCTION ai.guard_ai_proposal();
CREATE TRIGGER trg_ai_proposal_no_delete
    BEFORE DELETE ON ai.ai_proposal
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_ai_proposal_no_truncate
    BEFORE TRUNCATE ON ai.ai_proposal
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON ai.ai_proposal FROM PUBLIC;

COMMIT;

-- Rückwärtsstrategie: DROP des Schemas, nur solange keine KI-Läufe protokolliert
-- sind. KI-Protokolle werden niemals rückwärts migriert.
