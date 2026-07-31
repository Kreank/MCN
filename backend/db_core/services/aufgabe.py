"""Aufgaben-Service: Aufgaben anlegen, ihren Status ändern, Rückfragen führen.

Alle Writes über business_transaction (Audit). „Löschen" gibt es nicht — eine
Aufgabe wird erledigt (ERLEDIGT) oder verworfen (VERWORFEN); der DB-Trigger
verbietet physisches DELETE. Erledigen setzt completed_by/completed_at
(DB-CHECK erzwingt die Konsistenz zum Status).

Seit Migration 0137 hängt an jeder Aufgabe ein **Faden**
(`workflow.task_comment`) und jede Beteiligung erzeugt eine **Benachrichtigung**
(`notify.notification`). Die Regel dahinter ist eine einzige und gilt überall in
dieser Datei: **wer an der Aufgabe hängt, erfährt, was mit ihr geschieht** —
Ersteller und Zuständiger, nie der Auslöser selbst. Ohne das lag eine zugewiesene
Aufgabe stumm in einer Liste, und die Rückfrage dazu lief über WhatsApp, also
außerhalb des Systems, das die Aufgabe führt.

Beides läuft in DERSELBEN Transaktion wie die Fachaktion: Ein „erledigt, aber
niemand erfuhr es" wäre kein verspäteter, sondern ein falscher Zustand.
"""
import uuid

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import AppUser, Project, Task, TaskComment, WorkOrder
from db_core.services import benachrichtigung as benachrichtigung_service
from db_core.services._validation import ensure_exists, ensure_party_usable


# --- Faden und Postfach -----------------------------------------------------

#: Wie viel von einem Kommentar in der Glocke steht. Länger gelesen wird dort
#: ohnehin nicht; der ganze Text steht einen Klick entfernt an der Aufgabe.
_AUSZUG_ZEICHEN = 140

#: Obergrenze eines Beitrags. Deckungsgleich mit dem Formular im Frontend —
#: die Zeile ist append-only, ein Fehlgriff bliebe für immer stehen.
MAX_KOMMENTAR_ZEICHEN = 4000


def _name(app_user_id):
    """Anzeigename des Akteurs für Vermerk und Meldung („Marius hat …")."""
    if app_user_id is None:
        return "System"
    name = (
        AppUser.objects.filter(id=app_user_id)
        .values_list("display_name", flat=True)
        .first()
    )
    return name or "Unbekannt"


def _auszug(text):
    text = " ".join(text.split())
    if len(text) <= _AUSZUG_ZEICHEN:
        return text
    return text[: _AUSZUG_ZEICHEN - 1].rstrip() + "…"


def _vermerken(task, actor_app_user_id, text):
    """Schreibt einen SYSTEM-Eintrag in den Faden der Aufgabe.

    Warum zusätzlich zum Audit: Das Audit ist für die Revision, der Faden für
    die beiden Menschen, die an der Aufgabe arbeiten. „Erledigt am Freitag" muss
    zwischen den Rückfragen stehen, die dazu führten — sonst erzählt der Verlauf
    nur die halbe Geschichte und man sucht den Rest wieder im Telefonprotokoll.
    """
    return TaskComment.objects.create(
        id=uuid.uuid4(),
        task_id=task.id,
        kind="SYSTEM",
        body=text,
        created_by_id=actor_app_user_id,
    )


def _beteiligte(task):
    """Wer an dieser Aufgabe hängt: Ersteller und Zuständiger.

    Der Auslöser fällt im Benachrichtigungs-Baustein selbst weg, ebenso die
    Dublette, wenn beide Rollen auf derselben Person liegen.
    """
    return [task.created_by_id, task.assigned_to_id]


def _melden(task, actor_app_user_id, empfaenger, kind, text, *, titel=None):
    """Eine Benachrichtigung an die Beteiligten — Aufgabentitel als Überschrift.

    Der Titel steht oben, weil er das ist, was man in der Glocke wiedererkennt;
    was geschehen ist, steht darunter. Umgekehrt läse sich eine Liste aus fünf
    Zeilen „Aufgabe erledigt" ohne jeden Anhaltspunkt, welche gemeint ist.

    `titel` überschreibt den aktuellen Titel. Gebraucht wird das an genau einer
    Stelle: Wer die Aufgabe verliert, darf ihren **alten** Titel sehen, nicht
    einen, den derselbe PATCH gerade erst gesetzt hat und den er nie zu Gesicht
    bekam (siehe `_zustaendigkeit_gewechselt`).
    """
    benachrichtigung_service.viele_benachrichtigen(
        empfaenger,
        kind=kind,
        title=titel if titel is not None else task.title,
        body=text,
        target_type=benachrichtigung_service.ZIEL_AUFGABE,
        target_id=task.id,
        ausgeloest_von=actor_app_user_id,
    )


def create_task(
    actor_app_user_id,
    *,
    title,
    description=None,
    due_date=None,
    assigned_to_user_id=None,
    project_id=None,
    party_id=None,
    work_order_id=None,
):
    """Legt eine Aufgabe im Status OFFEN an (created_by = Akteur).

    Die drei Bezuege sind **kombinierbar** (Befund D2): Eine Aufgabe am Auftrag
    haengt fast immer auch am Kunden, den man deswegen anruft. Die DB erzwingt
    deshalb bewusst keine Exklusivitaet.
    """
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
    ensure_exists(AppUser, assigned_to_user_id, "Benutzer")
    ensure_exists(Project, project_id, "Projekt")
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    ensure_party_usable(party_id, "Kontakt")
    with business_transaction(actor_app_user_id):
        task = Task.objects.create(
            id=uuid.uuid4(),
            title=title.strip(),
            description=description,
            due_date=due_date,
            status="OFFEN",
            assigned_to_id=assigned_to_user_id,
            project_id=project_id,
            party_id=party_id,
            work_order_id=work_order_id,
            created_by_id=actor_app_user_id,
            version=1,
        )
        # Wer die Aufgabe bekommt, erfährt davon. Sich selbst zugewiesene
        # Aufgaben melden nichts (der Baustein filtert den Auslöser).
        _melden(
            task,
            actor_app_user_id,
            [assigned_to_user_id],
            "AUFGABE_ZUGEWIESEN",
            f"{_name(actor_app_user_id)} hat Ihnen diese Aufgabe zugewiesen.",
        )
    return task


#: Sentinel für „Feld nicht übergeben" — trennt „nicht gesetzt" von „auf None
#: gesetzt" (Löschen einer optionalen Zuordnung). Die API reicht nur die
#: tatsächlich übergebenen Felder durch (exclude_unset).
_UNSET = object()


def update_task(
    actor_app_user_id,
    task_id,
    *,
    title=_UNSET,
    description=_UNSET,
    due_date=_UNSET,
    assigned_to_user_id=_UNSET,
    project_id=_UNSET,
    party_id=_UNSET,
    work_order_id=_UNSET,
):
    """Ändert die inhaltlichen Felder einer Aufgabe — nur die übergebenen.

    Ausdrücklich KEIN Statuswechsel: Erledigen/Verwerfen/Wiederöffnen laufen über
    die eigenen Funktionen (mit ihrer completed_by/at-Konsistenz). Ein nicht
    übergebenes Feld (`_UNSET`) bleibt unverändert; `None` löscht eine optionale
    Zuordnung. Unbekannte Fremdschlüssel → ValueError (die API übersetzt in 422).
    """
    if title is not _UNSET and (not title or not title.strip()):
        raise ValueError("title darf nicht leer sein.")
    if assigned_to_user_id is not _UNSET:
        ensure_exists(AppUser, assigned_to_user_id, "Benutzer")
    if project_id is not _UNSET:
        ensure_exists(Project, project_id, "Projekt")
    if party_id is not _UNSET:
        ensure_party_usable(party_id, "Kontakt")
    if work_order_id is not _UNSET:
        ensure_exists(WorkOrder, work_order_id, "Auftrag")

    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        # Vor dem Speichern merken: nur ein tatsächlicher WECHSEL der
        # Zuständigkeit ist eine Nachricht wert. Wer beim Bearbeiten des Titels
        # dieselbe Person erneut einträgt, löst nichts aus.
        vorher_zustaendig = task.assigned_to_id
        # Auch der Titel: Ein einziger PATCH darf Titel UND Zuständigkeit
        # ändern. Der Entzugs-Meldung muss dann der ALTE Titel beiliegen — den
        # neuen hat der bisherige Zuständige nie gesehen und wird ihn auch nie
        # sehen können (404 nach dem Entzug).
        vorher_titel = task.title
        update_fields = []
        if title is not _UNSET:
            task.title = title.strip()
            update_fields.append("title")
        if description is not _UNSET:
            task.description = description
            update_fields.append("description")
        if due_date is not _UNSET:
            task.due_date = due_date
            update_fields.append("due_date")
        if assigned_to_user_id is not _UNSET:
            task.assigned_to_id = assigned_to_user_id
            update_fields.append("assigned_to")
        if project_id is not _UNSET:
            task.project_id = project_id
            update_fields.append("project")
        if party_id is not _UNSET:
            task.party_id = party_id
            update_fields.append("party")
        if work_order_id is not _UNSET:
            task.work_order_id = work_order_id
            update_fields.append("work_order")
        if update_fields:
            task.save(update_fields=update_fields)
        # Normalisiert vergleichen: Die API reicht mal ein UUID-Objekt, mal
        # einen String durch. Ungleiche Typen läsen sich sonst als Wechsel und
        # schrieben einen erfundenen Vermerk in den Faden.
        unveraendert = benachrichtigung_service.gleiche_id(
            task.assigned_to_id, vorher_zustaendig
        )
        if "assigned_to" in update_fields and not unveraendert:
            _zustaendigkeit_gewechselt(
                task, actor_app_user_id, vorher_zustaendig, vorher_titel
            )
    return task


def _zustaendigkeit_gewechselt(
    task, actor_app_user_id, vorher_zustaendig, vorher_titel
):
    """Vermerk im Faden + Meldung an BEIDE Seiten des Wechsels.

    Der bisherige Zuständige bekommt eine eigene Meldung. Der Faden allein
    genügt für ihn nicht: Wer nur „eigene" Zeilen sehen darf, verliert mit der
    Umhängung den Zugang zur Aufgabe und käme nie wieder an den Vermerk. Ohne
    diese Meldung verschwände die Aufgabe **signallos** aus seiner Liste — genau
    das Loch, das dieser Slice schließt, nur an der anderen Seite.

    Die Meldung trägt deshalb bewusst nichts Neues: den Titel, den er ohnehin
    kannte (`vorher_titel` — derselbe PATCH kann ihn geändert haben), und den
    Namen dessen, der jetzt zuständig ist; an ihn übergibt er. Kein Auszug aus
    dem Faden: Ist er nicht auch der Ersteller, verliert er mit der Umhängung
    den Lesezugriff, und die Glocke darf ihm nichts zeigen, was die API ihm
    verweigert.

    Der **Ersteller** bekommt hier nichts — die einzige Ausnahme von der Regel
    im Modulkopf. Er wartet auf die Erledigung, nicht auf die Personalie, und
    erfährt den Namen ohnehin mit der Erledigungsmeldung.
    """
    neuer_name = _name(task.assigned_to_id) if task.assigned_to_id else None
    _vermerken(
        task,
        actor_app_user_id,
        f"Zuständigkeit gewechselt zu {neuer_name}."
        if neuer_name
        else "Zuständigkeit aufgehoben.",
    )
    _melden(
        task,
        actor_app_user_id,
        [task.assigned_to_id],
        "AUFGABE_ZUGEWIESEN",
        f"{_name(actor_app_user_id)} hat Ihnen diese Aufgabe zugewiesen.",
    )
    _melden(
        task,
        actor_app_user_id,
        [vorher_zustaendig],
        "AUFGABE_ENTZOGEN",
        (
            f"{_name(actor_app_user_id)} hat die Aufgabe an {neuer_name} übertragen."
            if neuer_name
            else f"{_name(actor_app_user_id)} hat die Zuständigkeit aufgehoben."
        ),
        titel=vorher_titel,
    )


def _load(task_id):
    task = Task.objects.filter(id=task_id).first()
    if task is None:
        raise ValueError("Aufgabe nicht gefunden.")
    return task


def complete_task(actor_app_user_id, task_id):
    """Markiert eine Aufgabe als erledigt (setzt completed_by/at).

    Idempotent: ist die Aufgabe bereits erledigt, bleibt completed_by/at
    (der ursprüngliche Erlediger/Zeitpunkt) unverändert erhalten — und es wird
    kein zweites Mal gemeldet. Genau hier hing der Befund: Der Ersteller erfuhr
    bisher nichts davon, dass seine Aufgabe fertig war.
    """
    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        if task.status == "ERLEDIGT":
            return task
        task.status = "ERLEDIGT"
        task.completed_by_id = actor_app_user_id
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "completed_by", "completed_at"])
        _vermerken(task, actor_app_user_id, "Als erledigt markiert.")
        _melden(
            task,
            actor_app_user_id,
            _beteiligte(task),
            "AUFGABE_ERLEDIGT",
            f"{_name(actor_app_user_id)} hat die Aufgabe erledigt.",
        )
    return task


def discard_task(actor_app_user_id, task_id):
    """Verwirft eine Aufgabe (Status VERWORFEN statt Löschen). Idempotent."""
    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        if task.status == "VERWORFEN":
            return task
        task.status = "VERWORFEN"
        task.completed_by = None
        task.completed_at = None
        task.save(update_fields=["status", "completed_by", "completed_at"])
        _vermerken(task, actor_app_user_id, "Verworfen.")
        # Auch das Verwerfen wird gemeldet: Wer auf die Erledigung wartet, muss
        # erfahren, dass sie nicht mehr kommt — sonst wartet er weiter.
        _melden(
            task,
            actor_app_user_id,
            _beteiligte(task),
            "AUFGABE_VERWORFEN",
            f"{_name(actor_app_user_id)} hat die Aufgabe verworfen.",
        )
    return task


def reopen_task(actor_app_user_id, task_id):
    """Öffnet eine erledigte/verworfene Aufgabe wieder (Status OFFEN). Idempotent."""
    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        if task.status == "OFFEN":
            return task
        task.status = "OFFEN"
        task.completed_by = None
        task.completed_at = None
        task.save(update_fields=["status", "completed_by", "completed_at"])
        _vermerken(task, actor_app_user_id, "Wieder geöffnet.")
        _melden(
            task,
            actor_app_user_id,
            _beteiligte(task),
            "AUFGABE_WIEDEROFFEN",
            f"{_name(actor_app_user_id)} hat die Aufgabe wieder geöffnet.",
        )
    return task


# --- Rückfragen -------------------------------------------------------------

def kommentieren(actor_app_user_id, task_id, body):
    """Schreibt eine Wortmeldung in den Faden und meldet sie der Gegenseite.

    Das ist die Antwort auf „was, wenn der Mitarbeiter eine Frage dazu hat":
    Die Frage steht an der Aufgabe, nicht im Telefonprotokoll, und der andere
    erfährt davon, ohne die Liste im Blick behalten zu müssen.

    Die Zeile ist append-only (DB-Trigger) — auch der Verfasser kann sie später
    weder ändern noch löschen. Genau deshalb wird die Länge HIER begrenzt und
    nicht nur im Formular: Ein versehentlich eingefügter Riesentext ließe sich
    nie mehr entfernen, und die Spalte ist `text`, also ohne eigenes Limit.
    """
    if not body or not body.strip():
        raise ValueError("Der Kommentar darf nicht leer sein.")
    text = body.strip()
    if len(text) > MAX_KOMMENTAR_ZEICHEN:
        raise ValueError(
            f"Der Beitrag ist zu lang (höchstens {MAX_KOMMENTAR_ZEICHEN} Zeichen)."
        )
    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        kommentar = TaskComment.objects.create(
            id=uuid.uuid4(),
            task_id=task.id,
            kind="KOMMENTAR",
            body=text,
            created_by_id=actor_app_user_id,
        )
        _melden(
            task,
            actor_app_user_id,
            _beteiligte(task),
            "AUFGABE_KOMMENTAR",
            f"{_name(actor_app_user_id)}: {_auszug(text)}",
        )
    return kommentar


def kommentare(task_id):
    """Der Faden einer Aufgabe, älteste zuerst (Meta.ordering)."""
    return list(
        TaskComment.objects.select_related("created_by").filter(task_id=task_id)
    )
