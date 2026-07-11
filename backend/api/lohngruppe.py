"""Lohn-/Maschinengruppen-API (pricing.wage_group).

Modul `pricing` in der Rechtematrix: LESEN zum Anzeigen, ANLEGEN/AENDERN nur für
kalkulationsberechtigte Rollen. Schreibende Endpunkte laufen über den
lohngruppe-Service (business_transaction); Fachfehler → 422. Kein Löschen —
Gruppen werden über den Status INAKTIV deaktiviert.
"""
from decimal import Decimal
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_create
from db_core.services import lohngruppe as lohngruppe_service

router = Router()


class WageGroupOut(Schema):
    id: UUID
    name: str
    kind: str
    hourly_rate: Decimal
    cost_rate: Decimal | None = None
    status: str


class WageGroupIn(Schema):
    name: str
    kind: str = "LOHN"
    hourly_rate: Decimal
    cost_rate: Decimal | None = None


class WageGroupPatch(Schema):
    name: str | None = None
    kind: str | None = None
    hourly_rate: Decimal | None = None
    cost_rate: Decimal | None = None
    status: str | None = None


def _wage_group_out(g):
    return WageGroupOut(
        id=g.id, name=g.name, kind=g.kind, hourly_rate=g.hourly_rate,
        cost_rate=g.cost_rate, status=g.status,
    )


@router.get("/wage-groups", response=list[WageGroupOut])
def list_wage_groups(request, include_inactive: bool = True):
    require(request, "pricing", "LESEN")
    return [
        _wage_group_out(g)
        for g in lohngruppe_service.list_wage_groups(include_inactive=include_inactive)
    ]


@router.post("/wage-groups", response={201: WageGroupOut}, auth=django_auth)
def create_wage_group(request, payload: WageGroupIn):
    actor = require_create(request, "pricing", "ANLEGEN")
    try:
        group = lohngruppe_service.create_wage_group(actor, **payload.model_dump())
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _wage_group_out(group))


@router.put("/wage-groups/{wage_group_id}", response=WageGroupOut, auth=django_auth)
def update_wage_group(request, wage_group_id: UUID, payload: WageGroupPatch):
    actor, _ = require(request, "pricing", "AENDERN")
    try:
        group = lohngruppe_service.update_wage_group(
            actor, wage_group_id=wage_group_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _wage_group_out(group)
