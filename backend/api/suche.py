"""Globale Suche — EIN Endpunkt über alle Entitäten.

    GET /api/suche?q=Badensche%20Stra%C3%9Fe%2053

Rein lesend. Die Arbeit macht `db_core/services/suche.py`; hier stehen das
Schema und die Rechteauswertung.

## Antwortform: flach, nicht gruppiert (bewusst)

Die Trefferliste ist **flach und ranggeordnet**. Der beste Treffer steht oben —
auch wenn er aus der „letzten" Kategorie kommt. Eine nach Entität gruppierte
Antwort würde den Rang der Kategorie unterordnen und genau die Liste erzeugen,
über die sich der Nutzer beschwert („listet eine elend lange Liste, irgendwo
darin steht sie"). Wer gruppiert anzeigen will, gruppiert im Frontend nach `typ`;
`kategorien` liefert dazu je Kategorie die Anzahl und `mehr_vorhanden`.

`direkttreffer` ist gesetzt, wenn **genau ein** exakter Kennungstreffer existiert
(Beleg-/Objekt-/Personalnummer, GTIN, Artikelnummer) — dann darf das Frontend
ohne Rückfrage dorthin springen. Der Direkttreffer steht zusätzlich als erster
Eintrag in `treffer` (Rang 0).

## Rechte

Kein 403, nur weil EINE Kategorie fehlt: Jede Kategorie hängt an ihrem Modul;
fehlt das Recht, fehlt die Kategorie — die Suche antwortet trotzdem. **403 gibt es
genau dann, wenn gar nichts lesbar ist** (Konto ohne jede Rolle): Wer nirgends
lesen darf, bekommt keine leere 200 („nichts gefunden"), sondern die Wahrheit
(„du darfst nicht suchen"). Geprüft wird mit `check` (fail-closed, es ist intern
`require` — row_scope EIGENE ergibt None). Die **einzige** Kategorie mit
definierter EIGENE-Semantik ist der **Einsatz** (eigene Zuweisung); dafür — und
nur dafür — wird der Scope zusätzlich weich über `require_scoped` gelesen. Alle
anderen Kategorien bleiben bei EIGENE komplett weg, statt ungefilterte Zeilen
auszuliefern. Ein Monteur findet damit seine Einsätze und sonst nichts.
"""
from uuid import UUID

from ninja import Query, Router, Schema
from ninja.errors import HttpError

from api.permissions import actor_id, check, require_scoped
from db_core.services import suche as suche_service

router = Router()


class TrefferOut(Schema):
    typ: str  # KONTAKT|LIEGENSCHAFT|PROJEKT|VORGANG|AUFTRAG|EINSATZ|ANGEBOT|RECHNUNG|ARTIKEL|LEISTUNG|MITARBEITER
    id: UUID
    titel: str
    # Untertitel MIT Kontext, z. B. „AU-2026-000012 · Badensche Straße 53 · IN_ARBEIT".
    untertitel: str
    status: str | None = None
    # 0 = Direkttreffer (Kennung exakt) · 1 = Primärfeld/Wortanfang ·
    # 2 = Primärfeld/Teilstring · 3 = nur über eine Beziehung.
    rang: int
    # Warum getroffen: „Adresse der Liegenschaft", „Kontaktweg des Beteiligten", …
    grund: str
    ist_direkttreffer: bool


class KategorieOut(Schema):
    typ: str
    anzahl: int
    mehr_vorhanden: bool


class SucheOut(Schema):
    begriff: str
    treffer: list[TrefferOut]
    direkttreffer: TrefferOut | None = None
    kategorien: list[KategorieOut]


def _sicht(request):
    """Rechtematrix → `suche.Sicht`. Fail-closed, ohne die Suche zu töten."""
    # Ein Konto ohne app_user hat keine fachliche Identität → 403 (Hausregel,
    # gilt für jeden fachlichen Endpunkt).
    actor = actor_id(request)

    # Weich: fehlendes Recht ODER row_scope EIGENE → None. Für alle Kategorien
    # ohne definierte EIGENE-Semantik ist genau das die richtige Antwort
    # (Kategorie entfällt).
    workflow_alle = check(request, "workflow", "LESEN") is not None

    # Einsätze: der einzige Ort mit definierter EIGENE-Semantik. `require_scoped`
    # wirft nur, wenn das Recht ganz fehlt — hier weich abgefangen.
    try:
        _, workflow_scope = require_scoped(request, "workflow", "LESEN")
    except HttpError:
        workflow_scope = None

    sicht = suche_service.Sicht(
        identity=check(request, "identity", "LESEN") is not None,
        property=check(request, "property", "LESEN") is not None,
        workflow=workflow_alle,
        workflow_eigene=(workflow_scope == "EIGENE"),
        invoicing=check(request, "invoicing", "LESEN") is not None,
        pricing=check(request, "pricing", "LESEN") is not None,
        hr=check(request, "hr", "LESEN") is not None,
        actor_id=actor,
    )
    if not sicht.hat_recht():
        # Wer NIRGENDS lesen darf, hat auch nichts zu suchen: 403 statt einer
        # leeren 200. Das ist kein Widerspruch zur Regel „kein 403, nur weil eine
        # Kategorie fehlt" — hier fehlt nicht eine Kategorie, sondern jede. Ein
        # leeres 200 wäre zudem eine Lüge („nichts gefunden" statt „du darfst
        # nicht suchen"), und die Endpunktprüfung (test_endpoint_schutz) besteht
        # zu Recht darauf, dass jede GET-Operation ohne Rolle 403 antwortet.
        raise HttpError(
            403,
            "Keine Berechtigung: LESEN in keinem durchsuchbaren Modul. "
            "Wenden Sie sich an die Administration.",
        )
    return sicht


@router.get("", response=SucheOut)
def globale_suche(request, q: str = Query("")):
    """Globale Suche über alle Entitäten, die der Anmelder sehen darf.

    Leerer oder zu kurzer Begriff (< 2 nutzbare Zeichen) → leere Liste, **kein
    Fehler**: Das Suchfeld tippt der Nutzer Zeichen für Zeichen, und ein 422 beim
    ersten Buchstaben wäre nur lästig.
    """
    ergebnis = suche_service.suche(q, sicht=_sicht(request))
    return ergebnis
