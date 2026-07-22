"""API des Eigentums — `tenure.ownership_period` + `tenure.ownership_interest`.

Präfix `/api/tenure`, Rechtemodul **`tenure`** — dasselbe wie die Belegung, und
aus demselben Grund: Wer Objektstammdaten pflegen darf (Gebäude, Räume,
Anlagen), darf damit nicht automatisch Eigentumsverhältnisse ändern.

**Die Liegenschaft steht in der Route, nie im Payload.** An einer fremden
Liegenschaft lässt sich so kein Eigentumsstand anlegen, auch nicht mit
gefälschtem Body: Die Einheit wird gegen die Liegenschaft der Route geprüft.
Dieselbe Haltung wie in `api/belegung.py` und `api/anlage.py`.

**Es gibt kein DELETE**, und anders als bei der Belegung auch kein „Beteiligten
beenden": Eine Beteiligung trägt keinen eigenen Zeitraum, der Zeitraum hängt am
Stand. Ein Eigentümerwechsel ist deshalb *Stand beenden → neuen Stand anlegen*.
Der No-Delete-Trigger (0009) verbietet das Löschen zusätzlich physisch.

**Der Monteur sieht das Eigentum SEINER Objekte — und nur diese.** Migration
0103 hat ihm `tenure/LESEN` mit `row_scope='EIGENE'` gegeben, damit er den
Mieter erreicht, bei dem er klingeln muss; das Eigentum hängt am selben Modul
und erbt die Sichtbarkeit.

Das ist bewusst so gelassen: Wem eine Wohnung gehört, ist keine geheime Angabe
(es steht oft am Klingelschild), und `guard_objekt` begrenzt es ohnehin auf die
Objekte seiner eigenen Einsätze — an einer fremden Liegenschaft antwortet die
API mit **404**, nicht 403, denn deren Existenz geht ihn nichts an. Eine
Sonderregel nur fürs Eigentum wäre eine zusätzliche Ausnahme ohne Gewinn.

Schreiben darf er nichts: ANLEGEN/AENDERN/FREIGEBEN bleiben für MONTEUR
`false` — fail-closed durch Abwesenheit, hier ist nichts zu filtern und nichts
zu vergessen.
"""
from datetime import date
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status

from api.objektgrenze import guard_objekt
from api.permissions import require_scoped
from db_core.models import OwnershipPeriod, Property, Unit
from db_core.services import eigentum as eigentum_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class EigentuemerIn(Schema):
    party_id: UUID
    #: Bruch, kein Prozentwert. Beide Felder leer = „Anteil unbekannt" (in
    #: einem vollständigen Stand unzulässig, sonst der Normalfall).
    share_numerator: int | None = None
    share_denominator: int | None = None
    ownership_type: str = "CO_OWNER"
    confirmation_status: str = "UNCONFIRMED"


class EigentumIn(Schema):
    unit_id: UUID
    valid_from: date
    source_type: str
    source_reference: str
    distribution_status: str = "UNRESOLVED"
    valid_until: date | None = None
    eigentuemer: list[EigentuemerIn] = []


class EigentumPatch(Schema):
    distribution_status: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    source_type: str | None = None
    source_reference: str | None = None


class EigentuemerPatch(Schema):
    share_numerator: int | None = None
    share_denominator: int | None = None
    ownership_type: str | None = None
    confirmation_status: str | None = None


class EigentuemerOut(Schema):
    id: UUID
    party_id: UUID
    display_name: str
    share_numerator: int | None = None
    share_denominator: int | None = None
    #: Der Anteil als lesbarer Text („1/3", „50 %", „unbekannt") — die
    #: Umrechnung gehört an eine Stelle, nicht in jede Oberfläche.
    anteil_text: str
    ownership_type: str
    confirmation_status: str


class EigentumOut(Schema):
    id: UUID
    unit_id: UUID
    unit_number: str
    unit_type: str
    distribution_status: str
    valid_from: date
    valid_until: date | None = None
    is_current: bool
    source_type: str
    source_reference: str
    confirmed_at: str | None = None
    eigentuemer: list[EigentuemerOut]


class EinheitEigentumOut(Schema):
    """Eine Einheit **mit** ihrem geltenden Eigentumsstand — die Sicht der Mappe.

    Einheiten ohne Stand erscheinen mit `eigentum = null`. Das heißt **„nicht
    erfasst"**, nicht „herrenlos" — der Unterschied ist real, und das UI spricht
    ihn aus.
    """

    unit_id: UUID
    unit_number: str
    unit_type: str
    #: A-08: Gemeinschafts-/Technikflächen tragen keinen Eigentumsstand — das UI
    #: bietet dort gar nicht erst einen Knopf an, statt den 422 vorzuführen.
    eigentumsfaehig: bool
    eigentum: EigentumOut | None = None


class EigentuemerRefOut(Schema):
    """Ein Eigentümer der Liegenschaft — für die Auswahl als Rechnungsempfänger."""

    party_id: UUID
    display_name: str


# --- Abbildung -------------------------------------------------------------

def anteil_text(numerator, denominator):
    """Bruch → lesbarer Text.

    Ein glatter Prozentwert wird als Prozent gezeigt („1/2" → „50 %"), weil das
    die Sprache der Eigentümerlisten ist. Krumme Brüche bleiben Brüche: „1/3"
    ist die Wahrheit, „33,33 %" wäre gerundet — und Rundung ist genau das, was
    dieses Modell vermeidet.
    """
    if numerator is None or denominator is None:
        return "unbekannt"
    prozent = numerator * 100
    if prozent % denominator == 0:
        return f"{prozent // denominator} %"
    return f"{numerator}/{denominator}"


def _eigentuemer_out(i):
    return EigentuemerOut(
        id=i.id,
        party_id=i.owner_party_id,
        display_name=i.owner_party.display_name,
        share_numerator=i.share_numerator,
        share_denominator=i.share_denominator,
        anteil_text=anteil_text(i.share_numerator, i.share_denominator),
        ownership_type=i.ownership_type,
        confirmation_status=i.confirmation_status,
    )


def _eigentum_out(stand, stichtag):
    return EigentumOut(
        id=stand.id,
        unit_id=stand.unit_id,
        unit_number=stand.unit.unit_number,
        unit_type=stand.unit.unit_type,
        distribution_status=stand.distribution_status,
        valid_from=stand.valid_from,
        valid_until=stand.valid_until,
        # daterange ist [) — ein `valid_until` von heute gilt heute nicht mehr.
        is_current=(
            stand.valid_from <= stichtag
            and (stand.valid_until is None or stand.valid_until > stichtag)
        ),
        source_type=stand.source_type,
        source_reference=stand.source_reference,
        confirmed_at=stand.confirmed_at.isoformat() if stand.confirmed_at else None,
        eigentuemer=[_eigentuemer_out(i) for i in stand.interests.all()],
    )


def _property_or_404(property_id):
    if not Property.objects.filter(id=property_id).exists():
        raise HttpError(404, "Liegenschaft nicht gefunden.")


def _unit_in_property(unit_id, property_id):
    """Die Einheit muss zur Liegenschaft der Route gehören.

    Ohne diese Prüfung ließe sich über die eigene Liegenschaft ein Stand an
    einer fremden Einheit anlegen — die Objektgrenze wäre dann nur dekorativ.
    """
    if not Unit.objects.filter(id=unit_id, property_id=property_id).exists():
        raise HttpError(404, "Einheit nicht gefunden.")


def _stand_or_404_scoped(period_id, actor, scope):
    """Stand laden — bei Scope 'EIGENE' nur an meinem Objekt (sonst 404)."""
    stand = (
        OwnershipPeriod.objects.filter(id=period_id)
        .select_related("unit")
        .first()
    )
    if stand is None:
        raise HttpError(404, "Eigentumsstand nicht gefunden.")
    guard_objekt(scope, actor, stand.unit.property_id, "Eigentumsstand nicht gefunden.")
    return stand


def _fachfehler(exc):
    raise HttpError(422, str(exc))


# --- Endpunkte -------------------------------------------------------------

@router.get("/properties/{property_id}/eigentum", response=list[EinheitEigentumOut])
def list_eigentum(request, property_id: UUID, historie: bool = False):
    """Das Eigentum einer Liegenschaft — **je Einheit**, mit Beteiligten.

    Die Einheiten kommen vollständig (auch die ohne Stand und die nicht
    eigentumsfähigen); der Stand hängt daran. Sonst müsste das UI zwei Listen
    zusammenführen und könnte „nicht erfasst" nicht von „gehört niemandem"
    unterscheiden — was ohnehin niemals zutrifft.

    `historie=true` liefert zusätzlich die **beendeten** Stände: Wer wem wann
    verkauft hat, ist der eigentliche Nachweis.
    """
    actor, scope = require_scoped(request, "tenure", "LESEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)

    stichtag = date.today()
    staende = eigentum_service.staende_der_liegenschaft(
        property_id, stichtag=stichtag, historie=historie
    )
    je_einheit = {}
    for stand in staende:
        je_einheit.setdefault(stand.unit_id, []).append(stand)

    ausgabe = []
    for u in Unit.objects.filter(property_id=property_id).order_by("unit_number"):
        for stand in je_einheit.get(u.id, [None]):
            ausgabe.append(
                EinheitEigentumOut(
                    unit_id=u.id,
                    unit_number=u.unit_number,
                    unit_type=u.unit_type,
                    eigentumsfaehig=u.unit_type not in eigentum_service.OHNE_EIGENTUM,
                    eigentum=(
                        _eigentum_out(stand, stichtag) if stand is not None else None
                    ),
                )
            )
    return ausgabe


@router.get(
    "/properties/{property_id}/eigentuemer", response=list[EigentuemerRefOut]
)
def list_eigentuemer(request, property_id: UUID):
    """Die Eigentümer einer Liegenschaft, dublettenfrei.

    Der Weg zu Saschas „20 Rechnungsadressen, die ich immer angeben muss": Wer
    als Eigentümer einer Einheit geführt wird, kommt als Rechnungsempfänger in
    Frage. Ohne Anteile — für die Auswahl eines Empfängers ist gleichgültig, ob
    jemandem die Hälfte oder ein Achtel gehört.
    """
    actor, scope = require_scoped(request, "tenure", "LESEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    return [
        EigentuemerRefOut(party_id=p.id, display_name=p.display_name)
        for p in eigentum_service.eigentuemer_der_liegenschaft(property_id)
    ]


@router.post(
    "/properties/{property_id}/eigentum", response={201: EigentumOut}
)
def create_eigentum(request, property_id: UUID, payload: EigentumIn):
    """Eigentumsstand samt Beteiligten anlegen — in einer Transaktion.

    Nicht in zwei Schritten: Ein vollständiger Stand ohne Beteiligte ist
    unzulässig und ließe sich gar nicht erst anlegen, um danach befüllt zu
    werden.
    """
    actor, scope = require_scoped(request, "tenure", "ANLEGEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    _unit_in_property(payload.unit_id, property_id)

    try:
        stand = eigentum_service.create_stand(
            actor,
            unit_id=payload.unit_id,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            source_type=payload.source_type,
            source_reference=payload.source_reference,
            distribution_status=payload.distribution_status,
            eigentuemer=[e.dict() for e in payload.eigentuemer],
        )
    except ValueError as exc:
        _fachfehler(exc)
    return Status(201, _eigentum_out(stand, date.today()))


@router.patch("/eigentum/{period_id}", response=EigentumOut)
def update_eigentum(request, period_id: UUID, payload: EigentumPatch):
    """Kopfdaten ändern (Quelle, Vollständigkeitsgrad, Zeitraum).

    `exclude_unset`: Ein nicht gesendetes Feld bleibt, wie es ist — sonst
    setzte ein Teil-Update das offene Ende eines Standes still auf null.
    """
    actor, scope = require_scoped(request, "tenure", "AENDERN")
    _stand_or_404_scoped(period_id, actor, scope)
    felder = payload.dict(exclude_unset=True)
    if not felder:
        raise HttpError(422, "Es wurde kein Feld übergeben.")
    try:
        stand = eigentum_service.update_stand(actor, period_id, felder)
    except ValueError as exc:
        _fachfehler(exc)
    return _eigentum_out(stand, date.today())


@router.post("/eigentum/{period_id}/beenden", response=EigentumOut)
def beenden(request, period_id: UUID, valid_until: date):
    """Stand beenden — der erste Schritt des Eigentümerwechsels."""
    actor, scope = require_scoped(request, "tenure", "AENDERN")
    _stand_or_404_scoped(period_id, actor, scope)
    try:
        stand = eigentum_service.beenden(actor, period_id, valid_until=valid_until)
    except ValueError as exc:
        _fachfehler(exc)
    return _eigentum_out(stand, date.today())


@router.post("/eigentum/{period_id}/eigentuemer", response={201: EigentumOut})
def add_eigentuemer(request, period_id: UUID, payload: EigentuemerIn):
    """Einen Eigentümer an einem bestehenden Stand ergänzen."""
    actor, scope = require_scoped(request, "tenure", "AENDERN")
    _stand_or_404_scoped(period_id, actor, scope)
    try:
        stand = eigentum_service.add_eigentuemer(
            actor,
            period_id=period_id,
            party_id=payload.party_id,
            share_numerator=payload.share_numerator,
            share_denominator=payload.share_denominator,
            ownership_type=payload.ownership_type,
            confirmation_status=payload.confirmation_status,
        )
    except ValueError as exc:
        _fachfehler(exc)
    return Status(201, _eigentum_out(stand, date.today()))


@router.patch("/eigentuemer/{interest_id}", response=EigentumOut)
def update_eigentuemer(request, interest_id: UUID, payload: EigentuemerPatch):
    """Anteil, Art oder Bestätigung einer Beteiligung ändern.

    Der Kontakt selbst ist unveränderlich — ein anderer Eigentümer ist eine
    andere Aussage, kein korrigiertes Feld.
    """
    actor, scope = require_scoped(request, "tenure", "AENDERN")
    from db_core.models import OwnershipInterest

    beteiligung = (
        OwnershipInterest.objects.filter(id=interest_id)
        .select_related("ownership_period__unit")
        .first()
    )
    if beteiligung is None:
        raise HttpError(404, "Beteiligung nicht gefunden.")
    guard_objekt(
        scope,
        actor,
        beteiligung.ownership_period.unit.property_id,
        "Beteiligung nicht gefunden.",
    )

    felder = payload.dict(exclude_unset=True)
    if not felder:
        raise HttpError(422, "Es wurde kein Feld übergeben.")
    try:
        stand = eigentum_service.update_eigentuemer(actor, interest_id, felder)
    except ValueError as exc:
        _fachfehler(exc)
    return _eigentum_out(stand, date.today())


@router.post("/eigentum/{period_id}/bestaetigen", response=EigentumOut)
def bestaetigen(request, period_id: UUID):
    """Den Stand als geprüft bestätigen (Zeitpunkt + Person)."""
    actor, scope = require_scoped(request, "tenure", "FREIGEBEN")
    _stand_or_404_scoped(period_id, actor, scope)
    try:
        stand = eigentum_service.bestaetigen(actor, period_id)
    except ValueError as exc:
        _fachfehler(exc)
    return _eigentum_out(stand, date.today())
