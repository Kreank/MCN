"""Identity-API — Kontakte (Parties: Personen/Organisationen).

Lesende Endpoints laufen in der Dev-Phase bewusst ohne Auth; die Durchsetzung
der Rechtematrix (C-11 / B-35) übernimmt das später. Schreibende Endpoints
erfordern eine Django-Session (ninja django_auth) und einen zugeordneten
security.app_user (accounts.User.app_user_id) — sonst lehnt db_context ab.

Views bleiben dünn: sie validieren, rufen die Service-Schicht und mappen auf
saubere Schemas. Keine Model-Instanzen verlassen die API.
"""
from datetime import date
from uuid import UUID

from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.models import Party
from db_core.services import identity as identity_service

router = Router()


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


class PartyDetailOut(PartyOut):
    person: PersonOut | None = None
    organization: OrganizationOut | None = None


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
    """
    require(request, "identity", "LESEN")
    qs = Party.objects.all()

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
        .select_related("person", "organization")
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
    return PartyDetailOut(
        id=party.id,
        party_type=party.party_type,
        display_name=party.display_name,
        status=party.status,
        person=PersonOut.from_orm(person) if person else None,
        organization=OrganizationOut.from_orm(organization) if organization else None,
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
    """Detail einer Party inkl. Subtyp-Feldern (Person ODER Organisation)."""
    require(request, "identity", "LESEN")
    return _party_detail(party_id)
