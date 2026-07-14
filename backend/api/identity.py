"""Identity-API — Kontakte (Parties: Personen/Organisationen).

Views bleiben dünn: sie validieren, rufen die Service-Schicht und mappen auf
saubere Schemas. Keine Model-Instanzen verlassen die API.

**row_scope 'EIGENE' (Objektsicht, Migration 0099) — nur LESEN.**
Der Monteur bekam `identity/LESEN` aus einem einzigen, konkreten Grund: **Er muss
den Mieter anrufen können**, der „Heizkörper kalt" gemeldet hat. Also genau so viel
und nicht mehr:

  * **Lesen** (Liste, Detail, Adressen, Kontaktwege, Ansprechpartner): nur Kontakte,
    die an **einem meiner Objekte** hängen — Beteiligte der Liegenschaft, Beteiligte
    eines Auftrags dort, Melder eines Vorgangs dort, Ansprechpartner vor Ort an einem
    Einsatz dort. Die Definition steht in `db_core/services/objektsicht.py`
    (`eigene_party_q`), nicht hier.
  * Ein Kontakt **ohne** Objektbezug (Lieferant, fremder Kunde, das Adressbuch des
    Betriebs): **404** — nicht 403; seine Existenz geht den Monteur nichts an.
  * **Schreiben** (Person/Organisation anlegen, Adresse, Kontaktweg, Ansprechpartner,
    Akquisekanal): **403**. Die Matrix gibt ihm dafür ohnehin kein Recht (0099 setzt
    nur `identity/LESEN`); `_require_party` weist es zusätzlich ausdrücklich ab, damit
    eine spätere Matrixänderung nicht still einen Schreibpfad öffnet.
"""
from datetime import date
from uuid import UUID

from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.objektgrenze import guard_party
from api.permissions import require, require_scoped
from db_core.models import ContactPoint, Party, PartyAddress, PartyRelationship
from db_core.services import identity as identity_service
from db_core.services import objektsicht

router = Router()


def _require_party(request, party_id, action="LESEN"):
    """Prüft das Recht und dass der Kontakt existiert (sonst 404) — scope-bewusst.

    Gibt (actor_id, row_scope) zurück (wie require).

    * Scope 'EIGENE' **und** eine schreibende Aktion → **403**: Die Objektsicht ist
      eine Lesesicht. (Praktisch greift schon die Matrix — der Monteur hat kein
      `identity/ANLEGEN`/`AENDERN`. Dieser Riegel steht trotzdem hier: Ein Recht, das
      jemand später in der Matrixpflege setzt, darf nicht still einen ungefilterten
      Schreibpfad aufmachen.)
    * Scope 'EIGENE' und LESEN → nur ein Kontakt an einem meiner Objekte, sonst 404.
    """
    actor, scope = require_scoped(request, "identity", action)
    if scope == "EIGENE" and action != "LESEN":
        raise HttpError(
            403,
            "Ihre Rolle erlaubt nur den Zugriff auf eigene Datensätze; "
            "Kontaktdaten können Sie einsehen, aber nicht ändern.",
        )
    if not Party.objects.filter(id=party_id).exists():
        raise HttpError(404, "Kontakt nicht gefunden.")
    guard_party(scope, actor, party_id)
    return actor, scope


# --- Schemas ---------------------------------------------------------------

class PartyOut(Schema):
    id: UUID
    party_type: str
    display_name: str
    status: str


class PartyListOut(Schema):
    items: list[PartyOut]
    total: int
    page: int
    page_size: int


class PersonOut(Schema):
    salutation: str | None = None
    title: str | None = None
    first_name: str
    last_name: str
    birth_date: date | None = None


class OrganizationOut(Schema):
    organization_type: str
    legal_name: str
    legal_form: str | None = None
    registration_number: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None


class AcquisitionSourceRef(Schema):
    id: UUID
    code: str
    label: str


class PartyDetailOut(PartyOut):
    person: PersonOut | None = None
    organization: OrganizationOut | None = None
    acquisition_source: AcquisitionSourceRef | None = None


class AcquisitionSourceIn(Schema):
    # None löst die Quelle wieder (Kontakt ohne Kanal).
    source_id: UUID | None = None


class PersonIn(Schema):
    first_name: str
    last_name: str
    salutation: str | None = None
    title: str | None = None
    birth_date: date | None = None


class OrganizationIn(Schema):
    legal_name: str
    organization_type: str
    display_name: str | None = None
    legal_form: str | None = None
    registration_number: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None


class PartyFilter(Schema):
    q: str | None = None
    party_type: str | None = None
    status: str | None = None


# --- Kontaktmappe: Adressen / Kontaktwege / Ansprechpartner ----------------

class AddressOut(Schema):
    street: str
    house_number: str | None = None
    address_addition: str | None = None
    postal_code: str
    city: str
    country_code: str


class PartyAddressOut(Schema):
    id: UUID
    address_type: str
    is_primary: bool
    valid_from: date
    valid_until: date | None = None
    address: AddressOut


class AddressIn(Schema):
    address_type: str
    street: str
    postal_code: str
    city: str
    house_number: str | None = None
    address_addition: str | None = None
    country_code: str = "DE"
    is_primary: bool = True
    valid_from: date | None = None


class ContactPointOut(Schema):
    id: UUID
    contact_type: str
    value: str
    label: str | None = None
    is_primary: bool
    valid_from: date
    valid_until: date | None = None


class ContactPointIn(Schema):
    contact_type: str
    value: str
    label: str | None = None
    is_primary: bool = False
    valid_from: date | None = None


class ContactPersonOut(Schema):
    relationship_id: UUID
    person_party_id: UUID
    display_name: str
    valid_from: date
    valid_until: date | None = None


class ContactPersonIn(Schema):
    # Entweder eine bestehende Person referenzieren …
    person_party_id: UUID | None = None
    # … oder eine neue Person in einem Vorgang anlegen:
    first_name: str | None = None
    last_name: str | None = None
    salutation: str | None = None
    title: str | None = None
    valid_from: date | None = None


# --- Lesende Endpoints (Dev-Phase ohne Auth, siehe Modul-Docstring) --------

@router.get("/parties", response=PartyListOut)
def list_parties(
    request,
    filters: PartyFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Parties auflisten: Suche (display_name), Typ-/Statusfilter, Pagination.

    Ohne expliziten Statusfilter werden zusammengeführte (MERGED) Parties
    ausgeblendet; wer MERGED sehen will, setzt status=MERGED gezielt.

    Scope 'EIGENE': nur Kontakte an meinen Objekten (`distinct()` — derselbe Kontakt
    kann über mehrere Wege an mehreren meiner Objekte hängen).
    """
    actor, scope = require_scoped(request, "identity", "LESEN")
    qs = Party.objects.all()
    if scope == "EIGENE":
        qs = qs.filter(objektsicht.eigene_party_q(actor)).distinct()

    if filters.q:
        qs = qs.filter(display_name__icontains=filters.q)
    if filters.party_type:
        qs = qs.filter(party_type=filters.party_type)
    if filters.status:
        qs = qs.filter(status=filters.status)
    else:
        qs = qs.exclude(status="MERGED")

    qs = qs.order_by("display_name", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = list(qs[start:start + page_size])
    return PartyListOut(
        items=items, total=total, page=page, page_size=page_size
    )


def _party_detail(party_id):
    """Detail-Schema einer Party inkl. Subtyp; 404 wenn nicht vorhanden."""
    party = (
        Party.objects.filter(id=party_id)
        .select_related("person", "organization", "acquisition_source")
        .first()
    )
    if party is None:
        raise HttpError(404, "Party nicht gefunden.")

    person = getattr(party, "person", None) if party.party_type == "PERSON" else None
    organization = (
        getattr(party, "organization", None)
        if party.party_type == "ORGANIZATION"
        else None
    )
    src = party.acquisition_source
    return PartyDetailOut(
        id=party.id,
        party_type=party.party_type,
        display_name=party.display_name,
        status=party.status,
        person=PersonOut.from_orm(person) if person else None,
        organization=OrganizationOut.from_orm(organization) if organization else None,
        acquisition_source=(
            AcquisitionSourceRef(id=src.id, code=src.code, label=src.label)
            if src else None
        ),
    )


# --- Schreibende Endpoints (Django-Session-Auth Pflicht) -------------------
# Vor der {party_id}-Detailroute registriert: der Pfad-Konverter würde sonst
# die literalen Pfade /person bzw. /organization schlucken.

@router.post("/parties/person", response={201: PartyDetailOut}, auth=django_auth)
def create_person(request, payload: PersonIn):
    """Neue Person anlegen (Party PERSON + identity.person)."""
    actor, _ = require(request, "identity", "ANLEGEN")
    try:
        party = identity_service.create_person(
            actor,
            first_name=payload.first_name,
            last_name=payload.last_name,
            salutation=payload.salutation,
            title=payload.title,
            birth_date=payload.birth_date,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _party_detail(party.id))


@router.post("/parties/organization", response={201: PartyDetailOut}, auth=django_auth)
def create_organization(request, payload: OrganizationIn):
    """Neue Organisation anlegen (Party ORGANIZATION + identity.organization)."""
    actor, _ = require(request, "identity", "ANLEGEN")
    try:
        party = identity_service.create_organization(
            actor,
            legal_name=payload.legal_name,
            organization_type=payload.organization_type,
            display_name=payload.display_name,
            legal_form=payload.legal_form,
            registration_number=payload.registration_number,
            tax_number=payload.tax_number,
            vat_id=payload.vat_id,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _party_detail(party.id))


@router.get("/parties/{party_id}", response=PartyDetailOut)
def get_party(request, party_id: UUID):
    """Detail einer Party inkl. Subtyp-Feldern (Person ODER Organisation).

    Scope 'EIGENE': Kontakt ohne Bezug zu einem meiner Objekte → 404.
    """
    actor, scope = require_scoped(request, "identity", "LESEN")
    if not Party.objects.filter(id=party_id).exists():
        raise HttpError(404, "Party nicht gefunden.")
    guard_party(scope, actor, party_id, "Party nicht gefunden.")
    return _party_detail(party_id)


@router.put("/parties/{party_id}/acquisition-source", response=PartyDetailOut, auth=django_auth)
def set_acquisition_source(request, party_id: UUID, payload: AcquisitionSourceIn):
    """Akquisekanal eines Kontakts setzen/ändern (`source_id=null` löst ihn)."""
    actor, _ = require(request, "identity", "AENDERN")
    try:
        identity_service.set_party_acquisition_source(
            actor, party_id=party_id, source_id=payload.source_id
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _party_detail(party_id)


# --- Kontaktwege -----------------------------------------------------------

@router.get("/parties/{party_id}/contact-points", response=list[ContactPointOut])
def list_contact_points(request, party_id: UUID):
    """Aktive Kommunikationswege eines Kontakts (Tel/Mobil/E-Mail/Fax/Portal)."""
    _require_party(request, party_id, "LESEN")
    return identity_service.list_contact_points(party_id)


@router.post(
    "/parties/{party_id}/contact-points",
    response={201: ContactPointOut},
    auth=django_auth,
)
def create_contact_point(request, party_id: UUID, payload: ContactPointIn):
    """Kommunikationsweg anlegen."""
    actor, _ = _require_party(request, party_id, "ANLEGEN")
    try:
        point = identity_service.add_contact_point(
            actor,
            party_id,
            contact_type=payload.contact_type,
            value=payload.value,
            label=payload.label,
            is_primary=payload.is_primary,
            valid_from=payload.valid_from,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, point)


@router.post(
    "/parties/{party_id}/contact-points/{contact_point_id}/deactivate",
    response=ContactPointOut,
    auth=django_auth,
)
def deactivate_contact_point(request, party_id: UUID, contact_point_id: UUID):
    """Kommunikationsweg beenden (deaktivieren, kein Löschen)."""
    actor, _ = _require_party(request, party_id, "AENDERN")
    if not ContactPoint.objects.filter(id=contact_point_id, party_id=party_id).exists():
        raise HttpError(404, "Kommunikationsweg nicht gefunden.")
    try:
        point = identity_service.deactivate_contact_point(actor, contact_point_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return point


# --- Adressen --------------------------------------------------------------

@router.get("/parties/{party_id}/addresses", response=list[PartyAddressOut])
def list_addresses(request, party_id: UUID):
    """Aktive Adresszuordnungen eines Kontakts inkl. Adressdaten."""
    _require_party(request, party_id, "LESEN")
    return identity_service.list_addresses(party_id)


@router.post(
    "/parties/{party_id}/addresses",
    response={201: PartyAddressOut},
    auth=django_auth,
)
def create_address(request, party_id: UUID, payload: AddressIn):
    """Adresse anlegen und dem Kontakt mit Typ zuordnen."""
    actor, _ = _require_party(request, party_id, "ANLEGEN")
    try:
        link = identity_service.add_address(
            actor,
            party_id,
            address_type=payload.address_type,
            street=payload.street,
            postal_code=payload.postal_code,
            city=payload.city,
            house_number=payload.house_number,
            address_addition=payload.address_addition,
            country_code=payload.country_code,
            is_primary=payload.is_primary,
            valid_from=payload.valid_from,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    # link.address ist bereits geladen (create hat die FK gesetzt); für die
    # saubere Ausgabe frisch mit select_related nachladen.
    return Status(201, _address_link(link.id))


# --- Ansprechpartner (nur für Organisationen sinnvoll) ---------------------

@router.get("/parties/{party_id}/contact-persons", response=list[ContactPersonOut])
def list_contact_persons(request, party_id: UUID):
    """Ansprechpartner (Personen) einer Organisation."""
    _require_party(request, party_id, "LESEN")
    return [_contact_person_out(r) for r in identity_service.list_contact_persons(party_id)]


@router.post(
    "/parties/{party_id}/contact-persons",
    response={201: ContactPersonOut},
    auth=django_auth,
)
def create_contact_person(request, party_id: UUID, payload: ContactPersonIn):
    """Person als Ansprechpartner zuordnen — bestehend oder neu angelegt."""
    actor, _ = _require_party(request, party_id, "ANLEGEN")
    new_person = None
    if payload.person_party_id is None:
        if not payload.first_name or not payload.last_name:
            raise HttpError(
                422,
                "Entweder eine bestehende Person wählen oder Vor- und Nachname "
                "einer neuen Person angeben.",
            )
        new_person = {
            "first_name": payload.first_name,
            "last_name": payload.last_name,
            "salutation": payload.salutation,
            "title": payload.title,
        }
    try:
        rel = identity_service.add_contact_person(
            actor,
            party_id,
            person_party_id=payload.person_party_id,
            new_person=new_person,
            valid_from=payload.valid_from,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    # Für die Ausgabe den Anzeigenamen nachladen.
    rel = PartyRelationship.objects.select_related("from_party").get(pk=rel.id)
    return Status(201, _contact_person_out(rel))


@router.post(
    "/parties/{party_id}/contact-persons/{relationship_id}/remove",
    response=ContactPersonOut,
    auth=django_auth,
)
def remove_contact_person(request, party_id: UUID, relationship_id: UUID):
    """Ansprechpartner-Zuordnung beenden (kein Löschen)."""
    actor, _ = _require_party(request, party_id, "AENDERN")
    if not PartyRelationship.objects.filter(
        id=relationship_id, to_party_id=party_id,
        relationship_type="CONTACT_PERSON_FOR",
    ).exists():
        raise HttpError(404, "Ansprechpartner-Zuordnung nicht gefunden.")
    try:
        rel = identity_service.remove_contact_person(actor, relationship_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    rel = PartyRelationship.objects.select_related("from_party").get(pk=rel.id)
    return _contact_person_out(rel)


def _address_link(link_id):
    link = (
        PartyAddress.objects.filter(id=link_id).select_related("address").first()
    )
    return PartyAddressOut(
        id=link.id,
        address_type=link.address_type,
        is_primary=link.is_primary,
        valid_from=link.valid_from,
        valid_until=link.valid_until,
        address=AddressOut.from_orm(link.address),
    )


def _contact_person_out(rel):
    return ContactPersonOut(
        relationship_id=rel.id,
        person_party_id=rel.from_party_id,
        display_name=rel.from_party.display_name,
        valid_from=rel.valid_from,
        valid_until=rel.valid_until,
    )
