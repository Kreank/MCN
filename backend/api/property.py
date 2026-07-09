"""Property-API — Liegenschaften (Objektwelt: property.property).

Aufbau wie die Identity-API: lesende Endpoints laufen in der Dev-Phase ohne
Auth (die Rechtematrix greift später), schreibende Endpoints verlangen eine
Django-Session und ein zugeordnetes security.app_user. Views bleiben dünn und
rufen die Service-Schicht; Model-Instanzen verlassen die API nicht.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db.models import Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.models import Property
from db_core.services import property as property_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class PropertyOut(Schema):
    id: UUID
    property_number: str
    name: str
    property_type: str
    status: str
    # Aus der verknüpften identity.address; in den Endpoints explizit gesetzt
    # (kein from_orm-Resolver, damit Liste und Detail denselben Pfad nutzen).
    city: str


class PropertyListOut(Schema):
    items: list[PropertyOut]
    total: int
    page: int
    page_size: int


class AddressOut(Schema):
    street: str
    house_number: str | None = None
    address_addition: str | None = None
    postal_code: str
    city: str
    country_code: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class PartyRoleOut(Schema):
    party_id: UUID
    party_display_name: str
    role: str
    valid_from: date
    valid_until: date | None = None
    is_current: bool


class UnitOut(Schema):
    id: UUID
    unit_type: str
    unit_number: str


class BuildingOut(Schema):
    id: UUID
    building_number: str
    name: str | None = None
    units: list[UnitOut]


class PropertyDetailOut(PropertyOut):
    version: int
    address: AddressOut
    buildings: list[BuildingOut]
    party_roles: list[PartyRoleOut]


class PropertyIn(Schema):
    name: str
    property_type: str
    street: str
    postal_code: str
    city: str
    house_number: str | None = None
    address_addition: str | None = None
    country_code: str = "DE"


class PropertyFilter(Schema):
    q: str | None = None
    property_type: str | None = None
    status: str | None = None


# --- Lesende Endpoints (Dev-Phase ohne Auth, siehe Modul-Docstring) --------

@router.get("/properties", response=PropertyListOut)
def list_properties(
    request,
    filters: PropertyFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Liegenschaften auflisten: Suche (Name/Nummer), Typ-/Statusfilter, Seiten.

    Die Ortsangabe stammt aus der verknüpften identity.address; sie wird per
    select_related mitgeladen, damit die Liste ohne N+1 auskommt.
    """
    require(request, "property", "LESEN")
    qs = Property.objects.select_related("address")

    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(
            Q(name__icontains=needle) | Q(property_number__icontains=needle)
        )
    if filters.property_type:
        qs = qs.filter(property_type=filters.property_type)
    if filters.status:
        qs = qs.filter(status=filters.status)

    qs = qs.order_by("property_number", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [
        PropertyOut(
            id=p.id,
            property_number=p.property_number,
            name=p.name,
            property_type=p.property_type,
            status=p.status,
            city=p.address.city,
        )
        for p in qs[start:start + page_size]
    ]
    return PropertyListOut(
        items=items, total=total, page=page, page_size=page_size
    )


def _property_detail(property_id):
    """Detail-Schema einer Liegenschaft inkl. Adresse, Gebäude/Einheiten und
    Party-Rollen; 404 wenn nicht vorhanden."""
    prop = (
        Property.objects.filter(id=property_id)
        .select_related("address")
        .prefetch_related("buildings__units", "party_roles__party")
        .first()
    )
    if prop is None:
        raise HttpError(404, "Liegenschaft nicht gefunden.")

    today = date.today()

    def _is_current(r):
        # daterange(valid_from, valid_until) ist [) — obere Grenze exklusiv:
        # eine Rolle mit valid_until = heute gilt heute nicht mehr.
        return r.valid_until is None or r.valid_until > today

    party_roles = [
        PartyRoleOut(
            party_id=r.party_id,
            party_display_name=r.party.display_name,
            role=r.role,
            valid_from=r.valid_from,
            valid_until=r.valid_until,
            is_current=_is_current(r),
        )
        # Aktuelle Rollen zuerst, innerhalb der Gruppe neueste zuerst.
        for r in sorted(
            prop.party_roles.all(),
            key=lambda r: (_is_current(r), r.valid_from),
            reverse=True,
        )
    ]
    buildings = [
        BuildingOut(
            id=b.id,
            building_number=b.building_number,
            name=b.name,
            units=[
                UnitOut(id=u.id, unit_type=u.unit_type, unit_number=u.unit_number)
                for u in sorted(b.units.all(), key=lambda u: u.unit_number)
            ],
        )
        for b in sorted(prop.buildings.all(), key=lambda b: b.building_number)
    ]

    return PropertyDetailOut(
        id=prop.id,
        property_number=prop.property_number,
        name=prop.name,
        property_type=prop.property_type,
        status=prop.status,
        city=prop.address.city,
        version=prop.version,
        address=AddressOut.from_orm(prop.address),
        buildings=buildings,
        party_roles=party_roles,
    )


# --- Schreibender Endpoint (Django-Session-Auth Pflicht) -------------------
# Reihenfolge hier unkritisch (POST /properties vs. GET /properties/{id}
# unterscheiden sich in Methode und Segmentzahl); der Aufbau folgt der
# Identity-API der Lesbarkeit halber: Liste, dann Write, dann Detail.

@router.post("/properties", response={201: PropertyDetailOut}, auth=django_auth)
def create_property(request, payload: PropertyIn):
    """Neue Liegenschaft anlegen (identity.address + property.property)."""
    actor, _ = require(request, "property", "ANLEGEN")
    try:
        prop = property_service.create_property(
            actor,
            name=payload.name,
            property_type=payload.property_type,
            street=payload.street,
            postal_code=payload.postal_code,
            city=payload.city,
            house_number=payload.house_number,
            address_addition=payload.address_addition,
            country_code=payload.country_code,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _property_detail(prop.id))


@router.get("/properties/{property_id}", response=PropertyDetailOut)
def get_property(request, property_id: UUID):
    """Detail einer Liegenschaft inkl. Adresse, Gebäude/Einheiten und Rollen."""
    require(request, "property", "LESEN")
    return _property_detail(property_id)
