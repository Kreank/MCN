"""API der Belegung — `tenure.occupancy` + `tenure.occupancy_party`.

Präfix `/api/tenure`, Rechtemodul **`tenure`**. Das Modul steht seit der
Startmatrix (0026) in der Rechtematrix und wurde bis zu diesem Slice von **keinem
einzigen Endpunkt** benutzt; es wird jetzt benutzt, statt die Belegung an
`property` zu hängen. Begründung: Wer Objektstammdaten pflegen darf (Gebäude,
Räume, Anlagen), darf damit nicht automatisch **Mietverhältnisse** ändern — das
sind zwei verschiedene Befugnisse, und die Matrix trennt sie bereits.

**Der Monteur bekommt `tenure/LESEN` mit `row_scope='EIGENE'` (Migration 0103).**
Das ist der Zweck des Slices: Er fährt zur Badenschen Straße, muss in die Wohnung
EG rechts und braucht **Name und Telefonnummer von Robco**. Er sieht sie an
**seinen** Objekten — und an keinem anderen, auch nicht über eine geratene ID
(`api/objektgrenze` → **404**, nicht 403: die Existenz einer fremden Wohnung geht
ihn nichts an). Schreiben darf er **nichts**: ANLEGEN/AENDERN bleiben für MONTEUR
`false`, es gibt hier also nichts zu filtern und nichts zu vergessen —
fail-closed durch Abwesenheit.

**Es gibt kein DELETE.** Eine Belegung wird beendet, ein Mieter zieht aus
(`valid_until`). Der No-Delete-Trigger (0009) verbietet es zusätzlich physisch —
der Schutz hängt nicht am fehlenden Pfad.

**Die Liegenschaft steht in der Route, nie im Payload.** An einer fremden
Liegenschaft lässt sich so keine Belegung anlegen, auch nicht mit gefälschtem
Body: Die Einheit wird gegen die Liegenschaft der Route geprüft (dieselbe Haltung
wie in `api/anlage.py`).
"""
from datetime import date
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status

from api.objektgrenze import guard_objekt
from api.permissions import require_scoped
from db_core.models import Property, Unit
from db_core.services import belegung as belegung_service
from db_core.services import identity as identity_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class MieterIn(Schema):
    party_id: UUID
    role: str = "CONTRACTUAL_TENANT"
    # Ohne eigenen Zeitraum erbt der Mieter den der Belegung — der Regelfall.
    valid_from: date | None = None
    valid_until: date | None = None


class MieterAddIn(MieterIn):
    """Body für „weitere:n Mieter:in setzen" — mit Eigentümer-Übernahme.

    Eigenes Schema statt eines Feldes an `MieterIn`: In `BelegungIn.mieter`
    stünde es je Zeile und suggerierte, jeder Mieter könne einen eigenen
    Eigentumsstand mitbringen. Der Eigentümer gehört zur **Einheit**, nicht zur
    Mieterzeile.
    """

    #: Trägt diesen Kontakt zugleich als Eigentümer im Reiter „Eigentum" ein.
    eigentuemer_party_id: UUID | None = None


class BelegungIn(Schema):
    unit_id: UUID
    occupancy_type: str
    valid_from: date
    valid_until: date | None = None
    contract_reference: str | None = None
    #: Leer = **Leerstand**. Ausdrücklich zulässig.
    mieter: list[MieterIn] = []
    #: Wem die Einheit gehört — landet im Reiter „Eigentum", nicht in
    #: `occupancy_party`. Wer vermietet, wohnt dort gerade nicht.
    eigentuemer_party_id: UUID | None = None


class BelegungPatch(Schema):
    """PATCH: nur die **gesendeten** Felder ändern (`exclude_unset`).

    `unit_id` fehlt hier bewusst — eine Belegung, die die Wohnung wechselt, ist
    keine Korrektur, sondern eine andere Belegung.
    """

    occupancy_type: str | None = None
    contract_reference: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class BeendenIn(Schema):
    valid_until: date


class MieterOut(Schema):
    id: UUID
    party_id: UUID
    display_name: str
    role: str
    valid_from: date
    valid_until: date | None = None
    is_current: bool
    # Genau dafür gibt es diesen Slice: Der Monteur muss anrufen können.
    telefon: str | None = None
    email: str | None = None


class BelegungOut(Schema):
    id: UUID
    unit_id: UUID
    unit_number: str
    unit_type: str
    occupancy_type: str
    contract_reference: str | None = None
    valid_from: date
    valid_until: date | None = None
    is_current: bool
    mieter: list[MieterOut]


class EinheitOut(Schema):
    """Eine Einheit **mit** ihrer geltenden Belegung — die Sicht der Mappe.

    Einheiten ohne Belegungszeile erscheinen mit `belegung = null`. Das heißt
    **„nicht erfasst"**, nicht „leerstehend" — der Unterschied ist real, und das
    UI spricht ihn aus. Leerstand ist eine erfasste Belegung vom Typ `VACANT`.
    """

    unit_id: UUID
    unit_number: str
    unit_type: str
    #: F-12: COMMON_AREA/TECHNICAL_ROOM tragen keine Belegung — das UI bietet
    #: dort gar nicht erst einen Knopf an, statt den 422 vorzuführen.
    belegbar: bool
    belegung: BelegungOut | None = None


# --- Abbildung -------------------------------------------------------------

def _telefon_und_email(kontaktwege):
    """Die eine Nummer und die eine Adresse, die der Monteur braucht.

    Reihenfolge: MOBILE vor PHONE (er ruft unterwegs an); innerhalb eines Typs
    entscheidet `is_primary` (die Sortierung kommt aus dem identity-Service).
    """
    telefon = next(
        (c.value for c in kontaktwege if c.contact_type == "MOBILE"),
        None,
    ) or next((c.value for c in kontaktwege if c.contact_type == "PHONE"), None)
    email = next((c.value for c in kontaktwege if c.contact_type == "EMAIL"), None)
    return telefon, email


def _mieter_out(zeile, wege, stichtag):
    telefon, email = _telefon_und_email(wege.get(zeile.party_id, []))
    return MieterOut(
        id=zeile.id,
        party_id=zeile.party_id,
        display_name=zeile.party.display_name,
        role=zeile.role,
        valid_from=zeile.valid_from,
        valid_until=zeile.valid_until,
        # daterange ist [) — ein `valid_until` von heute gilt heute nicht mehr.
        is_current=(
            zeile.valid_from <= stichtag
            and (zeile.valid_until is None or zeile.valid_until > stichtag)
        ),
        telefon=telefon,
        email=email,
    )


def _belegung_out(occ, wege, stichtag):
    return BelegungOut(
        id=occ.id,
        unit_id=occ.unit_id,
        unit_number=occ.unit.unit_number,
        unit_type=occ.unit.unit_type,
        occupancy_type=occ.occupancy_type,
        contract_reference=occ.contract_reference,
        valid_from=occ.valid_from,
        valid_until=occ.valid_until,
        is_current=(
            occ.valid_from <= stichtag
            and (occ.valid_until is None or occ.valid_until > stichtag)
        ),
        mieter=[
            _mieter_out(z, wege, stichtag)
            for z in sorted(
                occ.parties.all(), key=lambda z: (z.role, z.valid_from)
            )
        ],
    )


def _wege_fuer(belegungen):
    """Kommunikationswege aller beteiligten Parteien — **eine** Query."""
    ids = {z.party_id for occ in belegungen for z in occ.parties.all()}
    return identity_service.contact_points_bulk(ids)


def _property_or_404(property_id):
    if not Property.objects.filter(id=property_id).exists():
        raise HttpError(404, "Liegenschaft nicht gefunden.")


def _belegung_or_404_scoped(occupancy_id, actor, scope):
    """Belegung laden — bei Scope 'EIGENE' nur an meinem Objekt (sonst 404)."""
    property_id = belegung_service.property_id_der_belegung(occupancy_id)
    if property_id is None:
        raise HttpError(404, "Belegung nicht gefunden.")
    guard_objekt(scope, actor, property_id, "Belegung nicht gefunden.")
    return property_id


def _ein_out(occ):
    stichtag = date.today()
    return _belegung_out(occ, _wege_fuer([occ]), stichtag)


# --- Endpunkte -------------------------------------------------------------

@router.get(
    "/properties/{property_id}/belegung", response=list[EinheitOut]
)
def list_belegung(request, property_id: UUID, historie: bool = False):
    """Die Belegung einer Liegenschaft — **je Einheit**, mit Mietern.

    Die Einheiten kommen vollständig (auch die unbelegten und die nicht
    belegbaren); die Belegung hängt daran. Sonst müsste das UI zwei Listen
    zusammenführen und könnte „nicht erfasst" nicht von „leerstehend"
    unterscheiden.

    `historie=true` liefert zusätzlich die **beendeten** Belegungen — „wer wohnte
    hier, als der Schaden entstand?". Sie erscheinen als eigene Zeilen unter
    ihrer Einheit.
    """
    actor, scope = require_scoped(request, "tenure", "LESEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)

    stichtag = date.today()
    belegungen = belegung_service.belegungen_der_liegenschaft(
        property_id, stichtag=stichtag, historie=historie
    )
    wege = _wege_fuer(belegungen)
    je_einheit = {}
    for occ in belegungen:
        je_einheit.setdefault(occ.unit_id, []).append(occ)

    einheiten = Unit.objects.filter(property_id=property_id).order_by("unit_number")
    ausgabe = []
    for u in einheiten:
        for occ in je_einheit.get(u.id, [None]):
            ausgabe.append(
                EinheitOut(
                    unit_id=u.id,
                    unit_number=u.unit_number,
                    unit_type=u.unit_type,
                    belegbar=u.unit_type
                    not in belegung_service.UNIT_TYPES_OHNE_BELEGUNG,
                    belegung=(
                        _belegung_out(occ, wege, stichtag) if occ is not None else None
                    ),
                )
            )
    return ausgabe


@router.post(
    "/properties/{property_id}/belegung", response={201: BelegungOut}
)
def create_belegung(request, property_id: UUID, payload: BelegungIn):
    """Belegung erfassen — optional gleich mit ihren Mietern (eine Transaktion).

    Ohne `mieter` ist es **Leerstand** (`occupancy_type='VACANT'`) — ausdrücklich
    zulässig.

    Die Einheit muss zur Liegenschaft der **Route** gehören; ein Payload mit einer
    fremden `unit_id` wird abgewiesen (404), nicht stillschweigend ausgeführt.
    """
    actor, scope = require_scoped(request, "tenure", "ANLEGEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    if not Unit.objects.filter(
        id=payload.unit_id, property_id=property_id
    ).exists():
        raise HttpError(404, "Einheit nicht gefunden.")
    try:
        occ = belegung_service.create_belegung(
            actor,
            unit_id=payload.unit_id,
            occupancy_type=payload.occupancy_type,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            contract_reference=payload.contract_reference,
            mieter=[m.dict() for m in payload.mieter],
            eigentuemer_party_id=payload.eigentuemer_party_id,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _ein_out(occ))


@router.patch("/belegung/{occupancy_id}", response=BelegungOut)
def update_belegung(request, occupancy_id: UUID, payload: BelegungPatch):
    """Belegung ändern — **und beenden** (`valid_until` setzen).

    Es gibt kein DELETE: Der Baustellenbericht von damals zeigt auf die Wohnung,
    in der damals Musili wohnte.
    """
    actor, scope = require_scoped(request, "tenure", "AENDERN")
    _belegung_or_404_scoped(occupancy_id, actor, scope)
    try:
        occ = belegung_service.update_belegung(
            actor, occupancy_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _ein_out(occ)


@router.post("/belegung/{occupancy_id}/mieter", response={201: BelegungOut})
def add_mieter(request, occupancy_id: UUID, payload: MieterAddIn):
    """Einen Mieter/Nutzer an eine bestehende Belegung setzen.

    Mehrere Beteiligte sind der Normalfall (Ehepaar, Mitbewohner), kein
    Sonderfall — deshalb ein eigener Endpunkt und kein „der eine Mieter".
    """
    actor, scope = require_scoped(request, "tenure", "AENDERN")
    if payload.eigentuemer_party_id is not None:
        # Die Übernahme kann einen Eigentumsstand ANLEGEN. Wer nur ändern darf,
        # bekommt hier kein Schlupfloch — fail-closed vor dem ersten Schreiben.
        require_scoped(request, "tenure", "ANLEGEN")
    _belegung_or_404_scoped(occupancy_id, actor, scope)
    try:
        occ = belegung_service.add_mieter(
            actor,
            occupancy_id,
            party_id=payload.party_id,
            role=payload.role,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            eigentuemer_party_id=payload.eigentuemer_party_id,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _ein_out(occ))


@router.post("/mieter/{occupancy_party_id}/beenden", response=BelegungOut)
def end_mieter(request, occupancy_party_id: UUID, payload: BeendenIn):
    """Ein Mieter zieht aus (`valid_until`). Kein Löschen — die Historie bleibt."""
    actor, scope = require_scoped(request, "tenure", "AENDERN")
    zeile = belegung_service.mieter_zeile(occupancy_party_id)
    if zeile is None:
        raise HttpError(404, "Mieter nicht gefunden.")
    occupancy_id, property_id = zeile
    guard_objekt(scope, actor, property_id, "Mieter nicht gefunden.")
    try:
        occ = belegung_service.end_mieter(
            actor, occupancy_party_id, valid_until=payload.valid_until
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _ein_out(occ)
