"""Lieferanten-Anbindungs-API (pricing.supplier_connection).

Registry der Katalog-Anbindungen (DATANORM / IDS-Connect). Modul `pricing` in der
Rechtematrix: LESEN zum Anzeigen, ANLEGEN/AENDERN zum Pflegen. Schreibende
Endpunkte laufen über den anbindung-Service (business_transaction); Fachfehler →
422. Kein Löschen — Anbindungen werden über den Status INACTIVE deaktiviert.

`credential_reference` ist ein **Verweis** auf den Secret-Store, nie das Secret
selbst (CLAUDE.md/0029) — daher als schlichter Text geführt, kein write-only-Feld.
Der eigentliche IDS-Connect-Warenkorb-Roundtrip ist ein späterer Backend-Slice.
"""
from datetime import datetime
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_create
from db_core.services import anbindung as anbindung_service

router = Router()


class SupplierConnectionOut(Schema):
    id: UUID
    supplier_party_id: UUID
    supplier_name: str | None = None
    source_system: str
    source_namespace: str
    label: str
    connection_kind: str
    shop_url: str | None = None
    credential_reference: str | None = None
    status: str
    last_import_at: datetime | None = None


class SupplierConnectionIn(Schema):
    supplier_party_id: UUID
    source_namespace: str
    label: str
    source_system: str = "IDS_CONNECT"
    connection_kind: str = "GROSSHAENDLER"
    shop_url: str | None = None
    credential_reference: str | None = None


class SupplierConnectionPatch(Schema):
    label: str | None = None
    connection_kind: str | None = None
    shop_url: str | None = None
    credential_reference: str | None = None
    status: str | None = None


def _connection_out(c):
    return SupplierConnectionOut(
        id=c.id,
        supplier_party_id=c.supplier_party_id,
        supplier_name=c.supplier_party.display_name if c.supplier_party_id else None,
        source_system=c.source_system,
        source_namespace=c.source_namespace,
        label=c.label,
        connection_kind=c.connection_kind,
        shop_url=c.shop_url,
        credential_reference=c.credential_reference,
        status=c.status,
        last_import_at=c.last_import_at,
    )


@router.get("/supplier-connections", response=list[SupplierConnectionOut])
def list_supplier_connections(request, include_inactive: bool = True):
    require(request, "pricing", "LESEN")
    return [
        _connection_out(c)
        for c in anbindung_service.list_connections(include_inactive=include_inactive)
    ]


@router.post("/supplier-connections", response={201: SupplierConnectionOut}, auth=django_auth)
def create_supplier_connection(request, payload: SupplierConnectionIn):
    actor = require_create(request, "pricing", "ANLEGEN")
    try:
        conn = anbindung_service.create_connection(actor, **payload.model_dump())
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _connection_out(conn))


@router.patch("/supplier-connections/{connection_id}", response=SupplierConnectionOut, auth=django_auth)
def update_supplier_connection(request, connection_id: UUID, payload: SupplierConnectionPatch):
    actor, _ = require(request, "pricing", "AENDERN")
    try:
        conn = anbindung_service.update_connection(
            actor, connection_id=connection_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _connection_out(conn)
