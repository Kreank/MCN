"""Benachrichtigungen — das persönliche Postfach (`notify.notification`).

Bereichsübergreifender Baustein: Die Fachservices rufen `benachrichtigen()`
**innerhalb ihrer eigenen** `business_transaction` auf. Deshalb öffnet diese
Funktion bewusst KEINE eigene Transaktion — die Benachrichtigung ist Teil der
Aktion, die sie meldet. Schlüge sie fehl, wäre die Meldung falsch, nicht bloß
verspätet; ein „erledigt, aber niemand erfuhr es" ist genau der Zustand, den
dieser Slice beseitigt.

Zwei Dinge nimmt der Baustein den Aufrufern ab, damit sie an keiner Stelle neu
bedacht werden müssen:

  * **Kein Empfänger, keine Zeile.** Eine Aufgabe ohne Zuständigen oder eine
    Aktion des Empfängers selbst erzeugt still nichts. Der Aufrufer muss nicht
    prüfen, ob es diesmal jemanden zu benachrichtigen gibt.
  * **Sich selbst benachrichtigt niemand.** Wer seine eigene Aufgabe abhakt,
    bekommt dafür keinen roten Punkt — das ist die Sorte Rauschen, an der ein
    Postfach binnen einer Woche stirbt. Die DB verbietet es zusätzlich per
    CHECK (Migration 0137); hier wird es still abgefangen, damit ein
    Doppelempfänger nicht die ganze Fachaktion umwirft.
"""
import uuid

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import Notification

#: Ziel-Kennung für Aufgaben. Das Frontend leitet daraus die Route ab
#: (`workflow.task` → `/aufgaben/{id}`); in der DB steht bewusst keine URL.
ZIEL_AUFGABE = "workflow.task"


def _uid(wert):
    """Normalisiert auf UUID — der Vergleich Akteur/Empfänger muss stimmen.

    Die Aufrufer reichen mal ein UUID-Objekt (aus dem ORM), mal einen String
    (aus der Session) durch. Ohne Normalisierung verglichen wären `UUID(x)` und
    `'x'` ungleich, die Selbstbenachrichtigung liefe durch und der DB-CHECK
    risse die auslösende Fachaktion mit ab.
    """
    if wert is None:
        return None
    return wert if isinstance(wert, uuid.UUID) else uuid.UUID(str(wert))


def gleiche_id(a, b):
    """Vergleicht zwei Benutzer-Ids typunabhängig (UUID-Objekt vs. String)."""
    return _uid(a) == _uid(b)


def benachrichtigen(
    *,
    empfaenger_user_id,
    kind,
    title,
    target_type,
    target_id,
    body=None,
    ausgeloest_von=None,
):
    """Legt eine Benachrichtigung an — oder tut nichts (siehe Modulkopf).

    Läuft in der Transaktion des Aufrufers; ohne eine solche wäre der
    Benutzerkontext (`app.current_user_id`) nicht gesetzt.
    """
    empfaenger = _uid(empfaenger_user_id)
    ausloeser = _uid(ausgeloest_von)
    if empfaenger is None or empfaenger == ausloeser:
        return None
    return Notification.objects.create(
        id=uuid.uuid4(),
        recipient_id=empfaenger,
        kind=kind,
        title=title,
        body=body,
        target_type=target_type,
        target_id=target_id,
        triggered_by_id=ausloeser,
        version=1,
    )


def viele_benachrichtigen(empfaenger_ids, **kwargs):
    """Wie `benachrichtigen`, für mehrere Empfänger — Dubletten fallen weg.

    Erledigt eine Person die Aufgabe, die sie selbst angelegt hat, stehen
    Ersteller und Zuständiger auf derselben ID; ohne die Entdopplung bekäme sie
    dieselbe Meldung zweimal.
    """
    gesehen = set()
    erzeugt = []
    for empfaenger in empfaenger_ids:
        schluessel = _uid(empfaenger)
        if schluessel is None or schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        eintrag = benachrichtigen(empfaenger_user_id=schluessel, **kwargs)
        if eintrag is not None:
            erzeugt.append(eintrag)
    return erzeugt


# --- Lesen ------------------------------------------------------------------

def liste(user_id, *, nur_ungelesen=False, page=1, page_size=20):
    """Postfach des Benutzers, neueste zuerst. Gibt (items, total) zurück."""
    qs = Notification.objects.select_related("triggered_by").filter(
        recipient_id=_uid(user_id)
    )
    if nur_ungelesen:
        qs = qs.filter(read_at__isnull=True)
    qs = qs.order_by("-created_at", "id")
    total = qs.count()
    start = (page - 1) * page_size
    return list(qs[start:start + page_size]), total


def ungelesen_zaehlen(user_id):
    return Notification.objects.filter(
        recipient_id=_uid(user_id), read_at__isnull=True
    ).count()


# --- Lesestatus -------------------------------------------------------------

def als_gelesen(actor_app_user_id, notification_id):
    """Markiert EINE Benachrichtigung des Akteurs als gelesen. Idempotent.

    Die Fremdzeile ist durch den Filter ausgeschlossen: gefiltert wird auf
    Empfänger UND Id, ein fremder Treffer ergibt schlicht 0 Zeilen. Der Rückgabe-
    wert sagt, ob die Zeile dem Akteur gehört — nicht, ob sie vorher ungelesen
    war (sonst wäre der zweite Klick ein 404).
    """
    actor = _uid(actor_app_user_id)
    with business_transaction(actor):
        getroffen = Notification.objects.filter(
            id=notification_id, recipient_id=actor, read_at__isnull=True
        ).update(read_at=timezone.now())
        if getroffen:
            return True
        # Entweder schon gelesen (idempotent: True) oder fremd/unbekannt (False).
        return Notification.objects.filter(
            id=notification_id, recipient_id=actor
        ).exists()


def alle_gelesen(actor_app_user_id):
    """Markiert alle ungelesenen Benachrichtigungen des Akteurs als gelesen."""
    actor = _uid(actor_app_user_id)
    with business_transaction(actor):
        return Notification.objects.filter(
            recipient_id=actor, read_at__isnull=True
        ).update(read_at=timezone.now())
