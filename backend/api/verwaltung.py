"""API der Verwaltung — `management.management_mandate`.

Präfix `/api/management`, Rechtemodul **`management`**. Auch dieses Modul steht
seit 0026 in der Rechtematrix und wurde bis zu diesem Slice von **keinem**
Endpunkt benutzt. Es wird jetzt benutzt, statt das Mandat an `property` zu hängen:
Wer Räume und Anlagen pflegt, verhandelt damit noch lange keine
Verwaltungsverträge.

**Die Verwaltung ist keine Beteiligtenrolle an der Liegenschaft** — sie läuft
ausschließlich über das Mandat (`0004_property.sql` sagt es wörtlich). Die
fachliche Begründung steht im Modulkopf von `db_core/services/verwaltung.py`.

**Der Monteur bekommt `management/LESEN` mit `row_scope='EIGENE'` (0103):** Er
sieht, **wer sein Objekt verwaltet und wen er anruft**, wenn niemand aufmacht —
an seinen Objekten und an keinem anderen (fremdes Objekt → **404**). Schreiben
darf er nichts.

**Kein DELETE.** Ein Mandat wird **beendet** (`status='ENDED'` + `valid_until`);
der No-Delete-Trigger (0009) verbietet das Löschen physisch. Die Rechnungen von
damals liefen über diesen Verwalter.
"""
from datetime import date
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status

from api.objektgrenze import guard_objekt
from api.permissions import require_scoped
from db_core.models import Property
from db_core.services import identity as identity_service
from db_core.services import verwaltung as verwaltung_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class MandatIn(Schema):
    management_party_id: UUID
    principal_party_id: UUID
    #: Pflicht (A-10). Kein Default — ein geratener Ansprechpartner ist keiner.
    default_contact_party_id: UUID
    mandate_type: str
    scope_type: str = "ENTIRE_PROPERTY"
    valid_from: date
    valid_until: date | None = None
    contract_reference: str | None = None
    #: Nur bei `scope_type='SELECTED_UNITS'` — und dort mindestens eine.
    unit_ids: list[UUID] = []


class MandatPatch(Schema):
    """Korrigierbar sind Standardkontakt und Vertragsreferenz — mehr nicht.

    Verwalter, Auftraggeber, Mandatsart und Umfang ändern sich über ein
    **Nachfolgemandat** (A-11/A-12); die Mandatseinheiten sind DB-seitig
    unveränderlich (`trg_mandate_unit_immutable`).
    """

    default_contact_party_id: UUID | None = None
    contract_reference: str | None = None


class BeendenIn(Schema):
    valid_until: date


class ZustaendigkeitIn(Schema):
    responsibility_type: str
    responsible_party_id: UUID
    valid_from: date
    valid_until: date | None = None
    priority: int = 100


class KontaktOut(Schema):
    party_id: UUID
    display_name: str
    telefon: str | None = None
    email: str | None = None


class MandatsEinheitOut(Schema):
    unit_id: UUID
    unit_number: str


class ZustaendigkeitOut(Schema):
    id: UUID
    responsibility_type: str
    party_id: UUID
    display_name: str
    priority: int
    valid_from: date
    valid_until: date | None = None
    is_current: bool
    telefon: str | None = None
    email: str | None = None


class MandatOut(Schema):
    id: UUID
    property_id: UUID
    mandate_type: str
    scope_type: str
    status: str
    valid_from: date
    valid_until: date | None = None
    is_current: bool
    contract_reference: str | None = None
    #: Wer verwaltet (Stegos).
    verwaltung: KontaktOut
    #: Wer beauftragt und zahlt (die WEG) — **nicht** dasselbe.
    auftraggeber: KontaktOut
    #: Wen man anruft (Pflicht, A-10).
    standardkontakt: KontaktOut
    einheiten: list[MandatsEinheitOut]
    zustaendigkeiten: list[ZustaendigkeitOut]


# --- Abbildung -------------------------------------------------------------

def _telefon_und_email(kontaktwege):
    """Mobil vor Festnetz (der Verwalter wird unterwegs angerufen)."""
    telefon = next(
        (c.value for c in kontaktwege if c.contact_type == "MOBILE"), None
    ) or next((c.value for c in kontaktwege if c.contact_type == "PHONE"), None)
    email = next((c.value for c in kontaktwege if c.contact_type == "EMAIL"), None)
    return telefon, email


def _kontakt_out(party, wege):
    telefon, email = _telefon_und_email(wege.get(party.id, []))
    return KontaktOut(
        party_id=party.id,
        display_name=party.display_name,
        telefon=telefon,
        email=email,
    )


def _wege_fuer(mandate):
    """Kommunikationswege aller beteiligten Parteien — **eine** Query."""
    ids = set()
    for m in mandate:
        ids.update(
            {m.management_party_id, m.principal_party_id, m.default_contact_party_id}
        )
        ids.update(r.responsible_party_id for r in m.responsibilities.all())
    return identity_service.contact_points_bulk(ids)


def _mandat_out(m, wege, stichtag):
    def _current(von, bis):
        return von <= stichtag and (bis is None or bis > stichtag)

    return MandatOut(
        id=m.id,
        property_id=m.property_id,
        mandate_type=m.mandate_type,
        scope_type=m.scope_type,
        status=m.status,
        valid_from=m.valid_from,
        valid_until=m.valid_until,
        # „Gilt" ist mehr als der Status: Ein Mandat mit abgelaufenem
        # `valid_until` gilt nicht, auch wenn niemand den Status nachgezogen hat.
        is_current=(m.status == "ACTIVE" and _current(m.valid_from, m.valid_until)),
        contract_reference=m.contract_reference,
        verwaltung=_kontakt_out(m.management_party, wege),
        auftraggeber=_kontakt_out(m.principal_party, wege),
        standardkontakt=_kontakt_out(m.default_contact_party, wege),
        einheiten=[
            MandatsEinheitOut(unit_id=u.unit_id, unit_number=u.unit.unit_number)
            for u in m.mandate_units.all()
        ],
        zustaendigkeiten=[
            ZustaendigkeitOut(
                id=r.id,
                responsibility_type=r.responsibility_type,
                party_id=r.responsible_party_id,
                display_name=r.responsible_party.display_name,
                priority=r.priority,
                valid_from=r.valid_from,
                valid_until=r.valid_until,
                is_current=_current(r.valid_from, r.valid_until),
                **dict(
                    zip(
                        ("telefon", "email"),
                        _telefon_und_email(wege.get(r.responsible_party_id, [])),
                    )
                ),
            )
            for r in m.responsibilities.all()
        ],
    )


def _ein_out(m):
    return _mandat_out(m, _wege_fuer([m]), date.today())


def _property_or_404(property_id):
    if not Property.objects.filter(id=property_id).exists():
        raise HttpError(404, "Liegenschaft nicht gefunden.")


def _mandat_or_404_scoped(mandate_id, actor, scope):
    property_id = verwaltung_service.property_id_des_mandats(mandate_id)
    if property_id is None:
        raise HttpError(404, "Mandat nicht gefunden.")
    guard_objekt(scope, actor, property_id, "Mandat nicht gefunden.")
    return property_id


# --- Endpunkte -------------------------------------------------------------

@router.get("/properties/{property_id}/mandate", response=list[MandatOut])
def list_mandate(request, property_id: UUID, historie: bool = False):
    """Die Mandate einer Liegenschaft — standardmäßig **nur die geltenden**.

    `historie=true` liefert auch die beendeten (wer verwaltete das Haus, als der
    Auftrag lief?).
    """
    actor, scope = require_scoped(request, "management", "LESEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    mandate = verwaltung_service.mandate_der_liegenschaft(
        property_id, nur_aktive=not historie
    )
    wege = _wege_fuer(mandate)
    stichtag = date.today()
    return [_mandat_out(m, wege, stichtag) for m in mandate]


@router.post("/properties/{property_id}/mandate", response={201: MandatOut})
def create_mandat(request, property_id: UUID, payload: MandatIn):
    """Verwaltungsmandat anlegen (Mandat + Mandatseinheiten in EINER Transaktion).

    Die Scope-Regeln erzwingen **DEFERRED Constraint-Trigger** in der Datenbank:
    `ENTIRE_PROPERTY` **mit** Einheitenliste und `SELECTED_UNITS` **ohne**
    Einheiten scheitern beide — auch wenn dieser Endpunkt umgangen würde. Hier
    werden sie vorgeprüft, damit statt eines Triggertexts eine Meldung kommt, die
    sagt, was zu tun ist (422).
    """
    actor, scope = require_scoped(request, "management", "ANLEGEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    try:
        mandat = verwaltung_service.create_mandat(
            actor,
            property_id=property_id,
            management_party_id=payload.management_party_id,
            principal_party_id=payload.principal_party_id,
            default_contact_party_id=payload.default_contact_party_id,
            mandate_type=payload.mandate_type,
            scope_type=payload.scope_type,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            contract_reference=payload.contract_reference,
            unit_ids=payload.unit_ids,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _ein_out(mandat))


@router.patch("/mandate/{mandate_id}", response=MandatOut)
def update_mandat(request, mandate_id: UUID, payload: MandatPatch):
    """Standardkontakt oder Vertragsreferenz korrigieren — mehr geht nicht."""
    actor, scope = require_scoped(request, "management", "AENDERN")
    _mandat_or_404_scoped(mandate_id, actor, scope)
    try:
        mandat = verwaltung_service.update_mandat(
            actor, mandate_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _ein_out(mandat)


@router.post("/mandate/{mandate_id}/beenden", response=MandatOut)
def end_mandat(request, mandate_id: UUID, payload: BeendenIn):
    """Mandat beenden — Status **und** Enddatum in einem Zug (CHECK verlangt beides).

    Unumkehrbar: Ein beendetes Mandat wird nicht wiederbelebt, es wird durch ein
    Nachfolgemandat ersetzt. Das UI fragt vorher nach.
    """
    actor, scope = require_scoped(request, "management", "AENDERN")
    _mandat_or_404_scoped(mandate_id, actor, scope)
    try:
        mandat = verwaltung_service.end_mandat(
            actor, mandate_id, valid_until=payload.valid_until
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _ein_out(mandat)


@router.post("/mandate/{mandate_id}/zustaendigkeiten", response={201: MandatOut})
def add_zustaendigkeit(request, mandate_id: UUID, payload: ZustaendigkeitIn):
    """Weiteren Kontakt am Mandat erfassen (technisch, kaufmännisch, Notfall …)."""
    actor, scope = require_scoped(request, "management", "AENDERN")
    _mandat_or_404_scoped(mandate_id, actor, scope)
    try:
        mandat = verwaltung_service.add_zustaendigkeit(
            actor,
            mandate_id,
            responsibility_type=payload.responsibility_type,
            responsible_party_id=payload.responsible_party_id,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            priority=payload.priority,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _ein_out(mandat))


@router.post("/zustaendigkeiten/{responsibility_id}/beenden", response=MandatOut)
def end_zustaendigkeit(request, responsibility_id: UUID, payload: BeendenIn):
    """Zuständigkeit beenden (`valid_until`). Kein Löschen."""
    actor, scope = require_scoped(request, "management", "AENDERN")
    mandate_id = verwaltung_service.mandate_id_der_zustaendigkeit(responsibility_id)
    if mandate_id is None:
        raise HttpError(404, "Zuständigkeit nicht gefunden.")
    _mandat_or_404_scoped(mandate_id, actor, scope)
    try:
        mandat = verwaltung_service.end_zustaendigkeit(
            actor, responsibility_id, valid_until=payload.valid_until
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _ein_out(mandat)
