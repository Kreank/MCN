"""API der Beauftragungsvollmacht — `management.party_authority` (A-26).

Präfix `/api/management`, Rechtemodul **`management`** — dasselbe wie das
Verwaltungsmandat, an dem die Vollmacht fachlich hängt.

**Die Frage, die am Telefon zählt:** Ruft die Hausverwaltung an und sagt „machen
Sie mal", ist die Frage nicht, ob sie nett ist, sondern ob sie **darf** — und
bis zu welchem Betrag. Ohne diese Angabe nimmt der Disponent einen Auftrag
entgegen, den am Ende niemand bezahlen will.

**Es gibt kein DELETE.** Eine Vollmacht wird widerrufen (`status = REVOKED` mit
Enddatum). Wer wann wie weit bevollmächtigt war, ist der Nachweis.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status

from api.objektgrenze import guard_objekt
from api.permissions import require, require_scoped
from db_core.models import Property
from db_core.services import vollmacht as vollmacht_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class VollmachtIn(Schema):
    principal_party_id: UUID
    authorized_party_id: UUID
    authority_type: str
    valid_from: date
    scope_type: str = "GENERAL"
    mandate_id: UUID | None = None
    #: Wertgrenze und Währung gehören zusammen. Ohne Grenze: beide leer.
    amount_limit: Decimal | None = None
    currency: str | None = None
    valid_until: date | None = None


class VollmachtOut(Schema):
    id: UUID
    principal_party_id: UUID
    principal_name: str
    authorized_party_id: UUID
    authorized_name: str
    authority_type: str
    scope_type: str
    mandate_id: UUID | None = None
    amount_limit: Decimal | None = None
    currency: str | None = None
    #: Fertiger Text der Wertgrenze („bis 5.000,00 €" / „ohne Wertgrenze") —
    #: die Formatierung gehört an eine Stelle, nicht in jede Oberfläche.
    grenze_text: str
    valid_from: date
    valid_until: date | None = None
    status: str
    is_current: bool


class BeauftragungsauskunftOut(Schema):
    """Die Antwort auf „darf der das?" — eine Auskunft, keine Sperre.

    Alltag und Notfall stehen **getrennt**: Wer ORDER bis 5.000 € und
    EMERGENCY_ORDER bis 50.000 € trägt, darf am Dienstagvormittag 5.000 €.
    Beides zu einer Zahl zusammenzufassen hieße, grünes Licht für einen
    ungedeckten Auftrag zu geben.
    """

    darf: bool
    grenze: Decimal | None = None
    waehrung: str | None = None
    grenze_text: str
    notfall_grenze: Decimal | None = None
    notfall_grenze_text: str | None = None
    #: Darf AUSSCHLIESSLICH im Notfall beauftragen.
    nur_notfall: bool = False
    #: Darf genehmigen, aber nicht beauftragen.
    nur_freigabe: bool = False
    arten: list[str]


# --- Abbildung -------------------------------------------------------------

def grenze_text(betrag, waehrung):
    """Wertgrenze als lesbarer Text.

    Deutsche Schreibweise mit Tausenderpunkt: „bis 5.000,00 €". Ohne Grenze
    steht ausdrücklich „ohne Wertgrenze" da — ein leeres Feld ließe offen, ob
    es keine Grenze gibt oder nur niemand eine eingetragen hat.
    """
    if betrag is None:
        return "ohne Wertgrenze"
    zahl = f"{betrag:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    zeichen = "€" if (waehrung or "EUR") == "EUR" else (waehrung or "")
    return f"bis {zahl} {zeichen}".strip()


def _out(v, stichtag):
    return VollmachtOut(
        id=v.id,
        principal_party_id=v.principal_party_id,
        principal_name=v.principal_party.display_name,
        authorized_party_id=v.authorized_party_id,
        authorized_name=v.authorized_party.display_name,
        authority_type=v.authority_type,
        scope_type=v.scope_type,
        mandate_id=v.mandate_id,
        amount_limit=v.amount_limit,
        currency=v.currency,
        grenze_text=grenze_text(v.amount_limit, v.currency),
        valid_from=v.valid_from,
        valid_until=v.valid_until,
        status=v.status,
        is_current=(
            v.status == "ACTIVE"
            and v.valid_from <= stichtag
            and (v.valid_until is None or v.valid_until > stichtag)
        ),
    )


def _property_or_404(property_id):
    if not Property.objects.filter(id=property_id).exists():
        raise HttpError(404, "Liegenschaft nicht gefunden.")


# --- Endpunkte -------------------------------------------------------------

@router.get("/properties/{property_id}/vollmachten", response=list[VollmachtOut])
def list_vollmachten(request, property_id: UUID, nur_aktive: bool = True):
    """Die Vollmachten, die an dieser Liegenschaft gelten.

    Zwei Wege führen hierher: die mandatsgebundene Vollmacht (hängt an einem
    Verwaltungsmandat dieser Liegenschaft) und die allgemeine (der
    Vollmachtgeber hat hier eine Beteiligtenrolle). Ohne den zweiten Weg fehlte
    genau der Fall, den A-26 im Blick hat — die Eigentümergemeinschaft
    bevollmächtigt die Verwaltung allgemein, nicht je Objekt.
    """
    actor, scope = require_scoped(request, "management", "LESEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    stichtag = date.today()
    return [
        _out(v, stichtag)
        for v in vollmacht_service.vollmachten_der_liegenschaft(
            property_id, nur_aktive=nur_aktive
        )
    ]


@router.get(
    "/properties/{property_id}/darf-beauftragen", response=BeauftragungsauskunftOut
)
def darf_beauftragen(
    request, property_id: UUID, party_id: UUID, betrag: Decimal | None = None
):
    """Darf diese Partei hier beauftragen — und bis wie viel?

    Eine **Auskunft, keine Sperre**: Ob ein Auftrag angenommen wird, entscheidet
    der Betrieb; hier steht nur, was vereinbart ist. Die Freigabetore des
    Auftrags sind ein eigener Mechanismus.
    """
    actor, scope = require_scoped(request, "management", "LESEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    auskunft = vollmacht_service.darf_beauftragen(
        property_id, party_id, betrag=betrag
    )
    return BeauftragungsauskunftOut(
        darf=auskunft["darf"],
        grenze=auskunft["grenze"],
        waehrung=auskunft["waehrung"],
        grenze_text=grenze_text(auskunft["grenze"], auskunft["waehrung"]),
        notfall_grenze=auskunft["notfall_grenze"],
        notfall_grenze_text=(
            grenze_text(auskunft["notfall_grenze"], auskunft["notfall_waehrung"])
            if auskunft["notfall_grenze"] is not None or auskunft["nur_notfall"]
            else None
        ),
        nur_notfall=auskunft["nur_notfall"],
        nur_freigabe=auskunft["nur_freigabe"],
        arten=auskunft["arten"],
    )


@router.post("/vollmachten", response={201: VollmachtOut})
def create_vollmacht(request, payload: VollmachtIn):
    """Vollmacht anlegen.

    `require` (nicht `require_scoped`): Eine allgemeine Vollmacht hängt an
    keiner Liegenschaft — es gibt kein Objekt, gegen das sich ein Scope prüfen
    ließe. Wer Vollmachten pflegt, braucht deshalb das Recht ohne Objektbezug.
    """
    actor, _ = require(request, "management", "ANLEGEN")
    try:
        v = vollmacht_service.create_vollmacht(
            actor,
            principal_party_id=payload.principal_party_id,
            authorized_party_id=payload.authorized_party_id,
            authority_type=payload.authority_type,
            valid_from=payload.valid_from,
            scope_type=payload.scope_type,
            mandate_id=payload.mandate_id,
            amount_limit=payload.amount_limit,
            currency=payload.currency,
            valid_until=payload.valid_until,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _out(v, date.today()))


@router.post("/vollmachten/{authority_id}/widerrufen", response=VollmachtOut)
def widerrufen(request, authority_id: UUID, valid_until: date | None = None):
    """Vollmacht widerrufen — sie wird nicht gelöscht."""
    actor, _ = require(request, "management", "AENDERN")
    try:
        v = vollmacht_service.widerrufen(actor, authority_id, valid_until=valid_until)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _out(v, date.today())
