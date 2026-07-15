"""KI-Tool-Vertrag: workflow_run, tool, tool_call + content_item-Ergänzung.

Hand-SQL nach db/README.md (RunSQL). Der durchdachte, gegen drei Reviews gehärtete
Vertrag zwischen dem Orchestrator und der Werkzeug-Flotte (ASR/Vision/OCR/LLM/
DOMAIN_QUERY). Volltext & Begründungen: docs/ki-tool-vertrag.md (Rev 1–3).

Kernentscheidungen, die HIER physisch verankert werden (nach Deploy nicht mehr
billig änderbar — deshalb jetzt):

1. **`ai.workflow_run` ist der durable, wiederaufnehmbare Anker.** 0027s `ai_run`
   ist LLM-zentrisch und laufkurz (finish-once, EIN Modell) — der falsche Anker für
   einen async-Workflow, der mit mehreren Modellen (ASR+Embedder+LLM) arbeitet und
   über Stunden/Tage auf ein schlafendes Gerät wartet. `tool_call` UND jeder `ai_run`
   hängen am `workflow_run`; die **Idempotenz keyt auf (workflow_run, step_key)**,
   nie auf `ai_run` (sonst dedupliziert der Retry nach einem Resume nicht).

2. **Idempotenz = UNIQUE(workflow_run_id, step_key).** Ein Schritt hat GENAU EINE
   `tool_call`-Zeile; Retries wiederholen dieselbe Zeile (attempt++, Status zurück auf
   QUEUED), erzeugen keine neue. Ein bewusster Zweitaufruf ist ein NEUER `step_key`.
   Damit braucht es keinen gehashten Schlüssel und kein Partial-Prädikat.

3. **State-Machine per Trigger** (`guard_tool_call`/`guard_workflow_run`): nur erlaubte
   Übergänge, Terminalzustände sind final, Identitäts-/Eingabefelder unveränderlich.
   Das ist die Doktrin „die DB erzwingt, nicht der Code".

4. **Kein PII-Klartext im unlöschbaren Audit.** `tool_call` trägt nur Hashes/Refs und
   MCN-eigene, secret-freie Fehlercodes — niemals ein Transkript inline. Erzeugter
   personenbezogener Text lebt ausschließlich im **löschbaren** `content_item`
   (0027 erlaubt dessen Löschung); `content_item.source_tool_call_id` (UNIQUE) bindet
   ihn an seinen Aufruf und verhindert ein zweites Transkript bei doppeltem Ergebnis.

5. **`data_class` + Dispatcher-Tor**: `content_item.data_class` (vorerst einwertig
   LOCAL_ONLY); der Service verweigert den Versand, wenn Datenklasse > Tool-
   `data_boundary`. Die Spalte ist der schwer nachrüstbare Teil, die Werteskala wächst.

6. **Lease-basierte Queue**: `tool_call.leased_until` trägt das Claiming per
   `SELECT … FOR UPDATE SKIP LOCKED`; ein Stale-Reaper gibt hängende RUNNING frei.
   Deadlines sind serverautoritativ.

Bewusst NICHT `audit.audit_row_update` auf den drei Tabellen (anders als 0054): wie
`ai_run`/`ai_proposal` in 0027 sind dies `ai`-Operationstabellen — die per Trigger
gehärtete State-Machine plus No-Delete IST die Audit-Spur; ein Audit-Row je
Queue-Update (Poll/Lease) würde die Audit-Tabelle fluten. Single-Tenant (kein
tenant_id). Gerät ist passiv → MCN pollt (keine inbound-Endpunkte).
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ===========================================================================
-- ai.workflow_run — durabler, wiederaufnehmbarer Workflow-Lauf
-- ===========================================================================
CREATE TABLE ai.workflow_run (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_name         text NOT NULL CHECK (btrim(workflow_name) <> ''),
    workflow_version      text NOT NULL CHECK (btrim(workflow_version) <> ''),
    triggered_by_user_id  uuid NOT NULL REFERENCES security.app_user (id),
    status                text NOT NULL DEFAULT 'QUEUED'
                          CHECK (status IN ('QUEUED','RUNNING','WAITING','DONE','FAILED','CANCELLED')),
    -- Welcher Schritt gerade läuft / auf welchen gewartet wird (Resume-Cursor).
    current_step          text NULL,
    -- Durabler Arbeitszustand: NUR Referenzen/IDs, NIE personenbezogener Rohtext.
    context               jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message         text NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    finished_at           timestamptz NULL,
    -- Kohärenz: genau die Terminalzustände tragen einen Abschlusszeitpunkt.
    CONSTRAINT workflow_run_finished_coherent CHECK (
        (status IN ('DONE','FAILED','CANCELLED')) = (finished_at IS NOT NULL)
    )
);
CREATE INDEX idx_workflow_run_status ON ai.workflow_run (status, updated_at);

-- ===========================================================================
-- ai.tool — Registry der Werkzeuge (Konfiguration, nicht Code)
-- ===========================================================================
CREATE TABLE ai.tool (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_key              text NOT NULL UNIQUE CHECK (btrim(tool_key) <> ''),
    label                 text NOT NULL CHECK (btrim(label) <> ''),
    capability            text NOT NULL
                          CHECK (capability IN ('ASR','VISION','OCR','LLM','DOMAIN_QUERY')),
    -- SYNC/ASYNC = externes Gerät (MCN pollt bei ASYNC); INTERNAL = in-process
    -- (LLM über den Adapter, DOMAIN_QUERY über die Lese-Services).
    invocation_mode       text NOT NULL
                          CHECK (invocation_mode IN ('SYNC','ASYNC','INTERNAL')),
    endpoint_url          text NULL,
    -- Verweis auf das Geräte-Bearer (NIE das Secret selbst); Fernet at rest unter
    -- MCN_CRED_KEY (eigener Schlüssel, nicht MCN_MAIL_KEY).
    credential_reference  text NULL,
    -- Welche Datenklasse das Werkzeug empfangen darf (Tor gegen Abfluss).
    data_boundary         text NOT NULL DEFAULT 'LOCAL_ONLY'
                          CHECK (data_boundary IN ('LOCAL_ONLY')),
    timeout_seconds       integer NOT NULL DEFAULT 120 CHECK (timeout_seconds > 0),
    max_attempts          integer NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    backoff_seconds       numeric(8,2) NOT NULL DEFAULT 5 CHECK (backoff_seconds >= 0),
    capability_version    text NOT NULL DEFAULT '1',
    contract_version      text NOT NULL DEFAULT '1',
    status                text NOT NULL DEFAULT 'ACTIVE'
                          CHECK (status IN ('ACTIVE','INACTIVE')),
    last_seen_at          timestamptz NULL,
    last_health           text NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    -- Ein externes Werkzeug braucht einen Endpoint; ein internes nicht.
    CONSTRAINT tool_endpoint_coherent CHECK (
        (invocation_mode = 'INTERNAL') OR (endpoint_url IS NOT NULL AND btrim(endpoint_url) <> '')
    )
);
CREATE INDEX idx_tool_capability ON ai.tool (capability, status);

-- ===========================================================================
-- ai.tool_call — ein tatsächlicher Werkzeug-Aufruf (State-Machine + Queue)
-- ===========================================================================
CREATE TABLE ai.tool_call (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_run_id     uuid NOT NULL REFERENCES ai.workflow_run (id),
    tool_id             uuid NOT NULL REFERENCES ai.tool (id),
    -- Capability + Version bei Dispatch EINGEFROREN (self-describing, auch wenn die
    -- Registry-Zeile später wandert).
    capability          text NOT NULL
                        CHECK (capability IN ('ASR','VISION','OCR','LLM','DOMAIN_QUERY')),
    capability_version  text NOT NULL DEFAULT '1',
    contract_version    text NOT NULL DEFAULT '1',
    -- Logischer Schritt; (workflow_run_id, step_key) IST der Idempotenzschlüssel.
    step_key            text NOT NULL CHECK (btrim(step_key) <> ''),
    status              text NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','EXPIRED','CANCELLED')),
    attempt             integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    -- Claiming-Lease (SELECT ... FOR UPDATE SKIP LOCKED setzt leased_until); der
    -- Reaper gibt RUNNING mit abgelaufenem Lease wieder frei.
    leased_until        timestamptz NULL,
    -- Serverautoritative Deadline (die Geräteuhr entscheidet nicht).
    deadline_at         timestamptz NULL,
    -- NUR Referenzen/Hashes — nie personenbezogener Rohtext.
    request_hash        text NULL,
    input_ref           jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_ref          jsonb NULL,
    output_hash         text NULL,
    is_untrusted        boolean NOT NULL DEFAULT true,
    -- MCN-eigener, klassifizierter, secret-freier Fehler (nie Device-Freitext).
    error_code          text NULL,
    error_message       text NULL,
    -- Whitelist-validiert im Service (duration_ms, tokens, model).
    metrics             jsonb NOT NULL DEFAULT '{}'::jsonb,
    cost_units          numeric(14,4) NULL,
    cost_currency       text NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tool_call_idempotent UNIQUE (workflow_run_id, step_key)
);
-- Queue-Drain scannt nur offene Calls.
CREATE INDEX idx_tool_call_offen ON ai.tool_call (status, leased_until)
    WHERE status IN ('QUEUED','RUNNING');
CREATE INDEX idx_tool_call_run ON ai.tool_call (workflow_run_id);

-- content_item hängt sein Ergebnis GENAU EINMAL an seinen Aufruf.
ALTER TABLE ai.content_item
    ADD COLUMN data_class text NOT NULL DEFAULT 'LOCAL_ONLY'
        CHECK (data_class IN ('LOCAL_ONLY')),
    ADD COLUMN source_tool_call_id uuid NULL REFERENCES ai.tool_call (id);
CREATE UNIQUE INDEX uq_content_item_source_tool_call
    ON ai.content_item (source_tool_call_id)
    WHERE source_tool_call_id IS NOT NULL;

-- ai_run wird künftig unter einem workflow_run protokolliert (nullbar: Alt-Läufe
-- und reine Einzel-LLM-Aufrufe ohne Workflow bleiben gültig).
ALTER TABLE ai.ai_run
    ADD COLUMN workflow_run_id uuid NULL REFERENCES ai.workflow_run (id);
CREATE INDEX idx_ai_run_workflow_run ON ai.ai_run (workflow_run_id);

-- ---------------------------------------------------------------------------
-- Schutz & Statusautomaten
-- ---------------------------------------------------------------------------

-- workflow_run: erlaubte Übergänge; Identität unveränderlich.
CREATE FUNCTION ai.guard_workflow_run() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Startintegrität: ein Lauf entsteht IMMER als QUEUED (kein Direkt-INSERT in
    -- einen fortgeschrittenen/terminalen Zustand an der State-Machine vorbei).
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'QUEUED' THEN
            RAISE EXCEPTION
                'workflow_run %: muss im Zustand QUEUED angelegt werden', NEW.id;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.workflow_name IS DISTINCT FROM OLD.workflow_name
       OR NEW.workflow_version IS DISTINCT FROM OLD.workflow_version
       OR NEW.triggered_by_user_id IS DISTINCT FROM OLD.triggered_by_user_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'workflow_run %: Identität ist unveränderlich', OLD.id;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 'QUEUED'  AND NEW.status IN ('RUNNING','CANCELLED')) OR
            (OLD.status = 'RUNNING' AND NEW.status IN ('WAITING','DONE','FAILED','CANCELLED')) OR
            (OLD.status = 'WAITING' AND NEW.status IN ('RUNNING','FAILED','CANCELLED'))
        ) THEN
            RAISE EXCEPTION 'workflow_run %: unzulässiger Statusübergang % -> %',
                OLD.id, OLD.status, NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- tool_call: Identität/Eingabe eingefroren, attempt monoton, Übergänge erlaubt,
-- Terminalzustände final. Kein late-callback-Revival (mit dem Poll-Modell ohnehin
-- kein inbound, aber der Guard hält die Invariante bei jedem Schreiber).
CREATE FUNCTION ai.guard_tool_call() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Startintegrität: ein Aufruf entsteht IMMER als QUEUED/attempt=0 (kein
    -- Direkt-INSERT in einen Terminalzustand oder mit gefälschtem Versuchszähler).
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'QUEUED' OR NEW.attempt <> 0 THEN
            RAISE EXCEPTION
                'tool_call %: muss als QUEUED/attempt=0 angelegt werden', NEW.id;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.workflow_run_id IS DISTINCT FROM OLD.workflow_run_id
       OR NEW.tool_id IS DISTINCT FROM OLD.tool_id
       OR NEW.capability IS DISTINCT FROM OLD.capability
       OR NEW.capability_version IS DISTINCT FROM OLD.capability_version
       OR NEW.contract_version IS DISTINCT FROM OLD.contract_version
       OR NEW.step_key IS DISTINCT FROM OLD.step_key
       OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
       OR NEW.input_ref IS DISTINCT FROM OLD.input_ref
       OR NEW.is_untrusted IS DISTINCT FROM OLD.is_untrusted
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'tool_call %: unveränderliche Felder (Identität/Eingabe)', OLD.id;
    END IF;
    IF NEW.attempt < OLD.attempt THEN
        RAISE EXCEPTION 'tool_call %: attempt darf nicht sinken', OLD.id;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 'QUEUED'  AND NEW.status IN ('RUNNING','CANCELLED')) OR
            (OLD.status = 'RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED','EXPIRED','CANCELLED','QUEUED'))
        ) THEN
            RAISE EXCEPTION 'tool_call %: unzulässiger Statusübergang % -> %',
                OLD.id, OLD.status, NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- tool: tool_key und capability sind die Identität eines Werkzeugs.
CREATE FUNCTION ai.guard_tool() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tool_key IS DISTINCT FROM OLD.tool_key
       OR NEW.capability IS DISTINCT FROM OLD.capability
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'tool %: tool_key/capability sind unveränderlich', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

-- Trigger je Tabelle: updated_at, Guard, No-Delete, No-Truncate.
CREATE TRIGGER trg_workflow_run_updated_at BEFORE UPDATE ON ai.workflow_run
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_workflow_run_guard BEFORE INSERT OR UPDATE ON ai.workflow_run
    FOR EACH ROW EXECUTE FUNCTION ai.guard_workflow_run();
CREATE TRIGGER trg_workflow_run_no_delete BEFORE DELETE ON ai.workflow_run
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_workflow_run_no_truncate BEFORE TRUNCATE ON ai.workflow_run
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON ai.workflow_run FROM PUBLIC;

CREATE TRIGGER trg_tool_call_updated_at BEFORE UPDATE ON ai.tool_call
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_tool_call_guard BEFORE INSERT OR UPDATE ON ai.tool_call
    FOR EACH ROW EXECUTE FUNCTION ai.guard_tool_call();
CREATE TRIGGER trg_tool_call_no_delete BEFORE DELETE ON ai.tool_call
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_tool_call_no_truncate BEFORE TRUNCATE ON ai.tool_call
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON ai.tool_call FROM PUBLIC;

CREATE TRIGGER trg_tool_updated_at BEFORE UPDATE ON ai.tool
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_tool_guard BEFORE UPDATE ON ai.tool
    FOR EACH ROW EXECUTE FUNCTION ai.guard_tool();
CREATE TRIGGER trg_tool_no_delete BEFORE DELETE ON ai.tool
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_tool_no_truncate BEFORE TRUNCATE ON ai.tool
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON ai.tool FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS ai.idx_ai_run_workflow_run;
ALTER TABLE ai.ai_run DROP COLUMN IF EXISTS workflow_run_id;
DROP INDEX IF EXISTS ai.uq_content_item_source_tool_call;
ALTER TABLE ai.content_item DROP COLUMN IF EXISTS source_tool_call_id;
ALTER TABLE ai.content_item DROP COLUMN IF EXISTS data_class;
DROP TABLE IF EXISTS ai.tool_call;
DROP TABLE IF EXISTS ai.tool;
DROP TABLE IF EXISTS ai.workflow_run;
DROP FUNCTION IF EXISTS ai.guard_workflow_run() CASCADE;
DROP FUNCTION IF EXISTS ai.guard_tool_call() CASCADE;
DROP FUNCTION IF EXISTS ai.guard_tool() CASCADE;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0105_aiproposal_airun_contentitem_embedding"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
