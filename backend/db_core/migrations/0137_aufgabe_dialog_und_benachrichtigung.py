"""Rückfrage an der Aufgabe + Benachrichtigungen — `workflow.task_comment`,
Schema `notify` mit `notify.notification`.

**Der Befund (Sascha, 2026-07-31).** „Wenn ich eine Aufgabe für einen anderen
Mitarbeiter erstelle — was passiert, wenn er sie abgeschlossen hat? Bekommt der
Ersteller eine Nachricht? Und was, wenn der Mitarbeiter eine Frage dazu hat?"
Beides ging bisher nicht. Eine zugewiesene Aufgabe lag stumm in einer Liste, die
niemand aufmacht, wenn ihn nichts dorthin ruft; die Erledigung setzte
`completed_by/at` und sonst nichts, und der Ersteller war im UI nicht einmal
sichtbar. Die Rückfrage lief deshalb zwangsläufig über Telefon oder WhatsApp —
also außerhalb des Systems, das die Aufgabe führt. Was dort besprochen wurde,
stand hinterher nirgends.

Zwei Tabellen, bewusst getrennt:

1. **`workflow.task_comment` — der Faden an der Aufgabe.** Append-only nach dem
   Muster `workflow.project_log` (0035): kein UPDATE, kein DELETE. Eine
   Rückfrage, die man hinterher stillschweigend umschreiben kann, ist als
   Nachweis wertlos — genau deshalb liegt sie hier und nicht in einem
   editierbaren Notizfeld. Korrektur = neuer Eintrag, wie im ganzen Haus.

   **Kein `seq` wie bei `ai.conversation_turn` (0117).** Dort nummeriert die
   Reihenfolge den Modellkontext und muss lückenlos sein. Hier genügt
   `created_at, id`: zwei gleichzeitige Kommentare wären mit `seq` ein
   UNIQUE-Konflikt (und damit ein Retry) für nichts.

   **Deshalb aber `clock_timestamp()` statt `now()`.** `now()` ist in Postgres
   der **Transaktions**zeitstempel: Zwei Zeilen derselben Transaktion — etwa
   „erledigt" plus die Abschlussnotiz — bekämen bitgleiche Werte, und die
   Sortierung fiele auf eine zufällige UUID zurück. Der Faden erzählte die
   Geschichte dann in willkürlicher Reihenfolge. `clock_timestamp()` liest die
   Uhr bei jeder Anweisung neu und trägt genau die Ordnung, die `seq` sonst
   hätte tragen müssen.

   **`kind` trennt Gesagtes von Geschehenem.** SYSTEM-Zeilen schreibt der
   Aufgaben-Service bei Statuswechseln, damit der Faden die Geschichte der
   Aufgabe vollständig erzählt („erledigt am …", darunter die Rückfrage, die
   dazu führte). Ohne diese Trennung stünde entweder eine halbe Chronik da oder
   Systemtext ließe sich als Wortmeldung eines Menschen missverstehen.
   `created_by` bleibt auch bei SYSTEM der auslösende Mensch — es gibt keinen
   anonymen Eintrag.

2. **`notify.notification` — das Postfach.** Eigenes Schema, weil die
   Benachrichtigung keinem Fachbereich gehört: dieselbe Tabelle trägt später
   Termine, Freigaben und KI-Vorschläge. In `workflow` gelegt hätte sie den
   nächsten Aufrufer aus `invoicing` zu einer zweiten Tabelle verleitet.

   **Das Ziel ist eine WEICHE Referenz** (`target_type` + `target_id`, kein FK)
   — dasselbe Zugeständnis wie in `audit.domain_event` (0008). Ein harter FK
   ginge nur auf genau eine Tabelle und wäre bei der zweiten Benachrichtigungs-
   art sofort im Weg. Die API toleriert ein verschwundenes Ziel.

   **Sich selbst benachrichtigt niemand — die DB verbietet es** (CHECK
   `notification_kein_selbstruf`). Wer seine eigene Aufgabe abhakt, soll dafür
   keinen roten Punkt bekommen; das ist die Sorte Rauschen, an der ein Postfach
   binnen einer Woche stirbt und danach ungelesen bleibt. Ein Anwendungsfehler
   an einer künftigen Aufrufstelle wird hier physisch gestoppt, nicht nur im
   Service abgefangen.

   **Änderbar ist ausschließlich `read_at`.** `notify.guard_notification()`
   friert Empfänger, Art, Text, Ziel, Auslöser und Anlagezeit ein. Sonst könnte
   eine Zeile nach Zustellung ihren Inhalt wechseln — eine Benachrichtigung, der
   man nicht ansieht, was sie ursprünglich meldete, ist keine.

   `read_at` darf auch wieder auf NULL zurück („als ungelesen markieren"); die
   API bietet es in diesem Slice nicht an, die Tabelle steht dem aber nicht im
   Weg.

   **Der Schutzstandard bleibt vollständig** (Audit/No-Delete/No-Truncate,
   CLAUDE.md). Das kostet einen `audit.audit_entry` je gelesener Zeile — bewusst
   in Kauf genommen: eine Ausnahme vom Standard müsste man an jeder neuen
   Tabelle neu begründen, die Audit-Menge ist demgegenüber vernachlässigbar.

**`kind` ist ein geschlossenes Vokabular.** Eine neue Benachrichtigungsart
kostet eine Migration. Das ist Absicht: eine offene Textspalte hätte binnen
weniger Slices vier Schreibweisen derselben Art, und das UI könnte für keine
davon eine verlässliche Beschriftung liefern.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ===========================================================================
-- workflow.task_comment — Rückfragen und Antworten an der Aufgabe (append-only)
-- ===========================================================================
CREATE TABLE workflow.task_comment (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     uuid NOT NULL REFERENCES workflow.task (id),
    -- KOMMENTAR = Wortmeldung eines Menschen, SYSTEM = Statuswechsel-Vermerk
    -- (schreibt der Service, siehe Kopf). Beides im selben Faden, damit die
    -- Aufgabe EINE Chronik hat statt zweier halber.
    kind        text NOT NULL DEFAULT 'KOMMENTAR'
                CHECK (kind IN ('KOMMENTAR', 'SYSTEM')),
    body        text NOT NULL CHECK (btrim(body) <> ''),
    created_by  uuid NOT NULL REFERENCES security.app_user (id),
    -- clock_timestamp(), NICHT now(): siehe Kopf. now() ist der Transaktions-
    -- zeitstempel und ordnet zwei Zeilen derselben Transaktion nicht.
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Die einzige Frage an diese Tabelle: „der Faden dieser Aufgabe, älteste zuerst".
CREATE INDEX idx_task_comment_task ON workflow.task_comment (task_id, created_at);

CREATE TRIGGER trg_task_comment_no_update BEFORE UPDATE ON workflow.task_comment
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_task_comment_no_delete BEFORE DELETE ON workflow.task_comment
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_task_comment_no_truncate BEFORE TRUNCATE ON workflow.task_comment
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON workflow.task_comment FROM PUBLIC;

-- ===========================================================================
-- notify.notification — persönliches Postfach je Benutzer
-- ===========================================================================
CREATE SCHEMA notify;

CREATE TABLE notify.notification (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_user_id uuid NOT NULL REFERENCES security.app_user (id),
    kind              text NOT NULL CHECK (kind IN (
                          'AUFGABE_ZUGEWIESEN',
                          -- Die Aufgabe ist einem anderen übertragen worden.
                          -- Ohne diese Art verschwände sie signallos aus der
                          -- Liste dessen, der sie bisher hatte — genau das
                          -- Loch, das dieser Slice schließt.
                          'AUFGABE_ENTZOGEN',
                          'AUFGABE_ERLEDIGT',
                          'AUFGABE_WIEDEROFFEN',
                          'AUFGABE_VERWORFEN',
                          'AUFGABE_KOMMENTAR'
                      )),
    -- Was in der Glocke steht. Kurz und ohne Kontextsuche lesbar; der Bezug
    -- (Aufgabentitel, Zitat) steht in body.
    title             text NOT NULL CHECK (btrim(title) <> ''),
    body              text NULL,
    -- WEICHE Referenz aufs Ziel (kein FK, siehe Kopf): 'workflow.task'.
    target_type       text NOT NULL CHECK (btrim(target_type) <> ''),
    target_id         uuid NOT NULL,
    -- Wer sie ausgelöst hat. NULL = System (Scheduler, KI) — dafür gibt es
    -- heute keinen Aufrufer, die Spalte nimmt ihn aber vorweg.
    triggered_by      uuid NULL REFERENCES security.app_user (id),
    read_at           timestamptz NULL,
    version           integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    -- Niemand benachrichtigt sich selbst (siehe Kopf).
    CONSTRAINT notification_kein_selbstruf
        CHECK (triggered_by IS NULL OR triggered_by <> recipient_user_id)
);

-- Die Glocke fragt zweierlei: den Zähler (nur ungelesen) und die Liste
-- (alles, neueste zuerst). Der Teilindex hält den Zähler klein — er wächst
-- nicht mit dem Archiv, sondern nur mit dem, was offen ist.
CREATE INDEX idx_notification_ungelesen
    ON notify.notification (recipient_user_id, created_at DESC)
    WHERE read_at IS NULL;
CREATE INDEX idx_notification_empfaenger
    ON notify.notification (recipient_user_id, created_at DESC);

-- Nach der Zustellung ist nur noch der Lesestatus beweglich.
CREATE FUNCTION notify.guard_notification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.recipient_user_id IS DISTINCT FROM OLD.recipient_user_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.body IS DISTINCT FROM OLD.body
       OR NEW.target_type IS DISTINCT FROM OLD.target_type
       OR NEW.target_id IS DISTINCT FROM OLD.target_id
       OR NEW.triggered_by IS DISTINCT FROM OLD.triggered_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION
            'notification %: nur der Lesestatus (read_at) ist änderbar', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_notification_guard BEFORE UPDATE ON notify.notification
    FOR EACH ROW EXECUTE FUNCTION notify.guard_notification();
CREATE TRIGGER trg_notification_updated_at BEFORE UPDATE ON notify.notification
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_notification_audit AFTER UPDATE ON notify.notification
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_notification_no_delete BEFORE DELETE ON notify.notification
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_notification_no_truncate BEFORE TRUNCATE ON notify.notification
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON notify.notification FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP TABLE IF EXISTS notify.notification;
DROP FUNCTION IF EXISTS notify.guard_notification() CASCADE;
DROP SCHEMA IF EXISTS notify;
DROP TABLE IF EXISTS workflow.task_comment;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0136_maintenancecontractasset"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
