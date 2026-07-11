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
from decimal import Decimal
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_create
from db_core.models import SupplierConnection
from db_core.services import anbindung as anbindung_service
from db_core.services import ids_warenkorb as ids_warenkorb_service

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


# --- IDS-Connect: Warenkorb-Rückgabe vorschauen ----------------------------

class ResolvedPositionOut(Schema):
    art_no: str
    qty: Decimal
    unit: str | None = None
    short_text: str | None = None
    ean: str | None = None
    article_id: UUID | None = None
    article_number: str | None = None
    article_name: str | None = None
    matched: bool
    ambiguous: bool


class WarenkorbPreviewOut(Schema):
    connection_id: UUID
    source_namespace: str
    total: int
    matched: int
    positions: list[ResolvedPositionOut]


@router.post(
    "/supplier-connections/{connection_id}/warenkorb/preview",
    response=WarenkorbPreviewOut,
    auth=django_auth,
)
def warenkorb_preview(request, connection_id: UUID):
    """Parst einen IDS-Rückgabe-Warenkorb (XML im Request-Body) und löst seine
    Positionen gegen den Artikelstamm der Anbindung auf (Vorschau, rein lesend).

    Das ist der Kern des IDS-Connect-Rückflusses: Der Body ist das vom Shop
    zurückgegebene `<Warenkorb>`-XML; die Antwort zeigt je Position, ob sie einem
    Stammartikel zugeordnet werden konnte. Recht `pricing/LESEN`. Ungültiges XML →
    422. Der spätere echte Rückgabe-Endpunkt (Shop-Roundtrip mit Token) nutzt
    dieselbe Service-Logik.
    """
    require(request, "pricing", "LESEN")
    conn = SupplierConnection.objects.filter(id=connection_id).first()
    if conn is None:
        raise HttpError(404, "Anbindung nicht gefunden.")
    try:
        positions = ids_warenkorb_service.parse_returned_cart(request.body)
    except ids_warenkorb_service.WarenkorbError as exc:
        raise HttpError(422, str(exc))
    resolved = ids_warenkorb_service.resolve_positions(conn.source_namespace, positions)
    return WarenkorbPreviewOut(
        connection_id=conn.id,
        source_namespace=conn.source_namespace,
        total=len(resolved),
        matched=sum(1 for r in resolved if r.matched),
        positions=[
            ResolvedPositionOut(
                art_no=r.art_no, qty=r.qty, unit=r.unit, short_text=r.short_text,
                ean=r.ean, article_id=r.article_id, article_number=r.article_number,
                article_name=r.article_name, matched=r.matched, ambiguous=r.ambiguous,
            )
            for r in resolved
        ],
    )


# --- IDS-Connect: Zugangsdaten + Punchout ----------------------------------

class CredentialStatusOut(Schema):
    username: str | None = None
    customer_number: str | None = None
    has_password: bool


class CredentialIn(Schema):
    username: str | None = None
    customer_number: str | None = None
    # Write-only: None = unverändert, "" = Passwort löschen, sonst neu setzen.
    password: str | None = None


class PunchoutIn(Schema):
    hook_url: str
    target: str | None = None
    # Aktion ist derzeit fest WKE (leeren Warenkorb füllen lassen). Die Übergabe
    # eines bestehenden Warenkorbs (WKS) kommt mit dem Warenkorb-Handover-Slice.


class PunchoutOut(Schema):
    url: str
    method: str
    enctype: str
    fields: dict[str, str]


def _connection_or_404(connection_id):
    conn = SupplierConnection.objects.filter(id=connection_id).first()
    if conn is None:
        raise HttpError(404, "Anbindung nicht gefunden.")
    return conn


@router.get("/supplier-connections/{connection_id}/credentials", response=CredentialStatusOut)
def get_credentials(request, connection_id: UUID):
    """Status der IDS-Zugangsdaten (Benutzername/Kundennummer + `has_password`) —
    das Passwort wird NIE zurückgegeben. Recht `pricing/LESEN`."""
    require(request, "pricing", "LESEN")
    _connection_or_404(connection_id)
    return CredentialStatusOut(**anbindung_service.credential_status(connection_id))


@router.put("/supplier-connections/{connection_id}/credentials", response=CredentialStatusOut, auth=django_auth)
def put_credentials(request, connection_id: UUID, payload: CredentialIn):
    """IDS-Zugangsdaten setzen/ändern. Passwort ist write-only (None=unverändert,
    ""=löschen); es wird verschlüsselt gespeichert. Recht `pricing/AENDERN`."""
    actor, _ = require(request, "pricing", "AENDERN")
    _connection_or_404(connection_id)
    try:
        # Nur ausdrücklich gesendete Felder ändern (Passwort nur, wenn mitgeschickt) —
        # sonst nullte ein reines Passwort-Update Benutzername/Kundennummer.
        status = anbindung_service.set_credentials(
            actor, connection_id=connection_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return CredentialStatusOut(**status)


@router.post("/supplier-connections/{connection_id}/punchout", response=PunchoutOut, auth=django_auth)
def punchout(request, connection_id: UUID, payload: PunchoutIn):
    """Baut die IDS-Punchout-Formularfelder (itek 2.5) zum Öffnen des Händler-Shops.

    Der Client submittet damit ein POST-Formular an `url`. Die Antwort enthält das
    Klartext-Passwort (`fields.pw_kunde`) — dem IDS-Verfahren inhärent (der Browser
    meldet sich beim Shop an); nur über HTTPS. Deshalb `pricing/AENDERN`. Fehlende
    Connector-URL/Zugangsdaten → 422.
    """
    require(request, "pricing", "AENDERN")
    _connection_or_404(connection_id)
    try:
        result = anbindung_service.build_punchout(
            connection_id, hook_url=payload.hook_url, action="WKE",
            target=payload.target,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return PunchoutOut(**result)
