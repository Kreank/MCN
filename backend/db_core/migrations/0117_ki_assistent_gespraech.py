"""KI Slice 5 — Gesprächsspeicher für den „frag das CRM"-Assistenten.

Hand-SQL nach db/README.md (RunSQL). Zwei Tabellen tragen den konversationellen
Auskunfts-Assistenten: ein durabler, mehrturniger Verlauf, den der Nutzer geräte-
übergreifend fortsetzt.

Kernentscheidungen, die HIER physisch verankert werden:

1. **Rohtext ist DSGVO-löschbar, das Audit bleibt.** Der Nachrichtentext
   (`conversation_turn.content`) ist personenbezogener Rohtext — er lebt in einer
   **löschbaren** Tabelle (Art. 17), genau wie `ai.content_item` (0027). Das
   **unveränderliche** Audit ist der `ai.ai_run` je Assistenten-Antwort, DIE EIN
   MODELL ERZEUGT HAT: er hält Modell, Quellen-Refs und Ressourcenverbrauch —
   **nie** den Frage-/Antworttext. Löscht der Nutzer sein Gespräch, verschwindet der
   Rohtext; die `ai_run`-Zeilen bleiben als Nachweis „ein KI-Lauf fand statt".
   `ai_run_id` ist deshalb **nullbar**: die deterministische Fallback-Antwort (Modell
   nicht konfiguriert/erreichbar) läuft ohne Modell und trägt bewusst keinen Lauf —
   es gibt dann nichts zu auditieren. (Tool-Vertrag Rev 3, E-B.)

2. **Ein Gespräch gehört seinem Ersteller.** `created_by_user_id` ist unveränderlich;
   die API lässt nur den Eigentümer lesen/fortsetzen/löschen (persönliche Ressource,
   kein Rechte-Modul-Scope). Löschen ist unbedingt (Art. 17), Turns hängen per
   CASCADE daran.

3. **Turns sind append-only.** Eine einmal gesprochene Nachricht ändert sich nie
   (`util.forbid_mutation` auf UPDATE); sie stirbt nur mit ihrem Gespräch (CASCADE).
   DELETE ist auf der Turn-Tabelle für PUBLIC gesperrt — nur die referenzielle
   CASCADE des Elterngesprächs räumt sie ab (Referenzaktionen umgehen die
   Rechteprüfung). So kann kein Einzel-Turn aus einem Verlauf herausgelöscht werden.

4. **`ai_run_id` ist ein harter FK** (ai_run ist unlöschbar — sicher).
   **`proposal_id` ist eine WEICHE Referenz ohne FK** (analog `ai_run.sources`):
   ein aus dem Chat entworfener Vorschlag ist eigenständig und unter DSGVO löschbar
   (REJECTED/EXPIRED, 0110); ein harter FK + `ON DELETE SET NULL` würde als UPDATE
   am append-only Turn gegen dessen Immutabilitäts-Trigger laufen. Der Turn hält
   darum nur einen Best-Effort-Zeiger; die API toleriert dessen Fehlen.

5. **Single-Tenant** (kein tenant_id), konsistent mit dem übrigen ai-Schema.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ===========================================================================
-- ai.conversation — ein Gesprächsfaden des „frag das CRM"-Assistenten
-- ===========================================================================
CREATE TABLE ai.conversation (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Eigentümer: nur er sieht/führt/löscht dieses Gespräch (API-getort).
    created_by_user_id    uuid NOT NULL REFERENCES security.app_user (id),
    -- Aus der ersten Frage abgeleiteter Titel; anfangs leer erlaubt.
    title                 text NOT NULL DEFAULT '',
    status                text NOT NULL DEFAULT 'ACTIVE'
                          CHECK (status IN ('ACTIVE','ARCHIVED')),
    created_at            timestamptz NOT NULL DEFAULT now(),
    -- Letzte Aktivität (für „meine Gespräche, neueste zuerst").
    updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_conversation_owner ON ai.conversation (created_by_user_id, updated_at DESC);

-- ===========================================================================
-- ai.conversation_turn — eine Nachricht (Frage oder Antwort), append-only
-- ===========================================================================
CREATE TABLE ai.conversation_turn (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id       uuid NOT NULL REFERENCES ai.conversation (id) ON DELETE CASCADE,
    -- Reihenfolge im Gespräch (1,2,3…); genau eine Nummer je Gespräch.
    seq                   integer NOT NULL CHECK (seq >= 1),
    role                  text NOT NULL CHECK (role IN ('USER','ASSISTANT')),
    -- Personenbezogener Rohtext — löschbar (Art. 17), nie im Audit.
    content               text NOT NULL,
    -- Zitierte Entitäten der Antwort: [{"typ","id","titel"}] (weiche Refs, kein FK).
    sources               jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- Was der Assistent tat: AUSKUNFT | KENNZAHL | VORSCHLAG (nur ASSISTANT).
    intent                text NULL CHECK (intent IN ('AUSKUNFT','KENNZAHL','VORSCHLAG')),
    -- Provenance der Antwort: harter FK auf den (unlöschbaren) Lauf.
    ai_run_id             uuid NULL REFERENCES ai.ai_run (id),
    -- Aus dem Chat entworfener Vorschlag: WEICHE Referenz (kein FK, s. Kopf Pkt. 4).
    proposal_id           uuid NULL,
    -- Nutzte die Antwort untrusted Inhalte (Content-Poisoning-Hinweis fürs UI)?
    aus_untrusted_quelle  boolean NOT NULL DEFAULT false,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT conversation_turn_seq_uniq UNIQUE (conversation_id, seq),
    -- Rollen-Kohärenz: eine Nutzerfrage trägt keine Assistenten-Metadaten.
    CONSTRAINT conversation_turn_user_clean CHECK (
        role = 'ASSISTANT'
        OR (ai_run_id IS NULL AND proposal_id IS NULL AND intent IS NULL
            AND sources = '[]'::jsonb AND aus_untrusted_quelle = false)
    )
);

-- ---------------------------------------------------------------------------
-- Schutz & Integrität
-- ---------------------------------------------------------------------------

-- conversation: Startintegrität + unveränderliche Identität.
CREATE FUNCTION ai.guard_conversation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'ACTIVE' THEN
            RAISE EXCEPTION
                'conversation %: muss im Zustand ACTIVE angelegt werden', NEW.id;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'conversation %: Identität/Eigentümer/Anlagezeit sind unveränderlich', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_conversation_updated_at BEFORE UPDATE ON ai.conversation
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_conversation_guard BEFORE INSERT OR UPDATE ON ai.conversation
    FOR EACH ROW EXECUTE FUNCTION ai.guard_conversation();
-- Kein No-Delete: der Eigentümer darf sein Gespräch löschen (Art. 17). Aber
-- No-Truncate bleibt (kein Massen-Wipe).
CREATE TRIGGER trg_conversation_no_truncate BEFORE TRUNCATE ON ai.conversation
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON ai.conversation FROM PUBLIC;

-- conversation_turn: append-only. UPDATE verboten; DELETE nur per CASCADE des
-- Elterngesprächs (für PUBLIC gesperrt — Referenzaktion umgeht die Rechteprüfung).
CREATE TRIGGER trg_conversation_turn_no_update BEFORE UPDATE ON ai.conversation_turn
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_conversation_turn_no_truncate BEFORE TRUNCATE ON ai.conversation_turn
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON ai.conversation_turn FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP TABLE IF EXISTS ai.conversation_turn;
DROP TABLE IF EXISTS ai.conversation;
DROP FUNCTION IF EXISTS ai.guard_conversation() CASCADE;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0116_login_throttle"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
