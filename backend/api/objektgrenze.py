"""Die Objektgrenze als HTTP-Tor — dünn, ohne eigene Definition.

Die **fachliche Regel** („was ist meins?") steht ausschließlich in
`db_core/services/objektsicht.py`. Hier steht nur, was daraus in HTTP wird:

  * Fremdes Objekt → **404**, nicht 403. Ein 403 bestätigte die Existenz der
    Liegenschaft, des Auftrags, des Vorgangs — das ist die Hausregel des Repos
    (`backend/README.md`: „Fremde Zeilen antworten mit 404 statt 403").
  * Scope `ALLE` → gar keine Prüfung (die Funktionen sind No-Ops).

**Wer `require_scoped` benutzt, MUSS eine dieser Funktionen aufrufen** (oder
`objektsicht.begrenzen` für Listen). `require_scoped` ohne Filter ist ein stiller
Datenleak — die schlimmste Art, einen Zeilen-Scope zu „unterstützen".
"""
from ninja.errors import HttpError

from db_core.services import objektsicht


def guard_objekt(scope, actor, property_id, meldung="Liegenschaft nicht gefunden."):
    """Scope 'EIGENE': 404, wenn die Liegenschaft nicht meine ist."""
    if scope != "EIGENE":
        return
    if not objektsicht.ist_eigenes_objekt(actor, property_id):
        raise HttpError(404, meldung)


def guard_projekt(scope, actor, project_id, meldung="Projekt nicht gefunden."):
    """Scope 'EIGENE': 404, wenn keine Liegenschaft des Projekts meine ist."""
    if scope != "EIGENE":
        return
    if not objektsicht.ist_eigenes_projekt(actor, project_id):
        raise HttpError(404, meldung)


def guard_party(scope, actor, party_id, meldung="Kontakt nicht gefunden."):
    """Scope 'EIGENE': 404, wenn der Kontakt an keinem meiner Objekte hängt."""
    if scope != "EIGENE":
        return
    if not objektsicht.ist_eigene_party(actor, party_id):
        raise HttpError(404, meldung)


def verbiete_eigene(scope, meldung):
    """Scope 'EIGENE': 403 — diese Aktion gibt es für die Objektsicht nicht.

    Für Schreibpfade und übergreifende Auswertungen, die sich **nicht** auf ein
    Objekt begrenzen lassen. Bewusst 403 (nicht 404): Hier wird keine Existenz
    verraten, sondern eine Rolle abgewiesen.
    """
    if scope == "EIGENE":
        raise HttpError(403, meldung)
