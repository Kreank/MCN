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
(„du darfst nicht suchen").

**row_scope EIGENE = die Objektsicht** (Migration 0099). Für `identity`, `property`
und `workflow` wird der Scope **weich** gelesen (`require_scoped` im try) und als
`*_eigene`-Flagge weitergereicht; der Service begrenzt die Grundmenge jeder Kategorie
auf **meine Objekte** (`db_core/services/objektsicht.py`). `invoicing`, `pricing` und
`hr` bleiben beim harten `check` (fail-closed → EIGENE ergibt None): Belege, Preise
und Personaldaten haben in diesem Slice keine Objektsicht — und die Rolle MONTEUR hat
dort ohnehin kein Recht.

Der Monteur findet damit sein Objekt, dessen Vorgänge, Aufträge, Einsätze und
Kontakte — und **kein** Angebot, **keine** Rechnung, **kein** fremdes Objekt (auch
nicht über dessen exakte Objektnummer: der Direkttreffer-Pfad zieht aus derselben
rechtegefilterten Grundmenge).
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


# Module mit Objektsicht: Hier wird der row_scope WEICH gelesen (ALLE oder EIGENE).
# `invoicing`/`pricing`/`hr` stehen bewusst nicht darin — sie kennen in diesem Slice
# keine Objektsicht und bleiben beim harten, fail-closed `check`.
_OBJEKTSICHT_MODULE = ("identity", "property", "workflow")


def _sicht(request):
    """Rechtematrix → `suche.Sicht`. Fail-closed, ohne die Suche zu töten.

    Der `require_scoped`-Aufruf steht **absichtlich hier** und nicht in einem
    Unterhelfer: `api/tests/test_endpoint_schutz.py` weist statisch nach, dass jede
    View ein Recht prüft — direkt oder über EINEN Modul-Helfer. Eine zweite
    Delegationsstufe würde der Scanner nicht mehr finden, und `GET /api/suche` (der
    Endpunkt, den jeder aufruft) sähe für ihn ungeschützt aus.
    """
    # Ein Konto ohne app_user hat keine fachliche Identität → 403 (Hausregel,
    # gilt für jeden fachlichen Endpunkt).
    actor = actor_id(request)

    # ALLE | EIGENE | None je Modul mit Objektsicht.
    scopes = {}
    for modul in _OBJEKTSICHT_MODULE:
        if check(request, modul, "LESEN") is not None:
            scopes[modul] = "ALLE"
            continue
        # `check` ist fail-closed (intern `require`) und liefert bei EIGENE None —
        # den Scope holt deshalb `require_scoped`; es wirft nur, wenn das Recht ganz
        # fehlt, und das wird hier weich abgefangen (keine 403 auf die Gesamtsuche).
        try:
            _, scopes[modul] = require_scoped(request, modul, "LESEN")
        except HttpError:
            scopes[modul] = None

    sicht = suche_service.Sicht(
        identity=scopes["identity"] == "ALLE",
        property=scopes["property"] == "ALLE",
        workflow=scopes["workflow"] == "ALLE",
        identity_eigene=scopes["identity"] == "EIGENE",
        property_eigene=scopes["property"] == "EIGENE",
        workflow_eigene=scopes["workflow"] == "EIGENE",
        # Geld, Preise, Personal: KEINE Objektsicht in diesem Slice — hartes `check`
        # (row_scope EIGENE → None → Kategorie entfällt).
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
