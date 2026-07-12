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

from django.http import HttpResponse
from ninja import File as NinjaFile
from ninja import Form, Router, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_create
from db_core.models import PunchoutSession, SupplierConnection
from db_core.services import anbindung as anbindung_service
from db_core.services import datanorm_import as datanorm_import_service
from db_core.services import ids_warenkorb as ids_warenkorb_service
from db_core.services import punchout_session as punchout_service

router = Router()

# Obergrenze für den DATANORM-Upload (komprimiert). Größere Vollkataloge laufen
# über das CLI-Kommando (Streaming aus Datei), nicht durch einen Upload.
DATANORM_MAX_UPLOAD = 80 * 1024 * 1024


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
    net_price: Decimal | None = None
    vat: Decimal | None = None
    article_id: UUID | None = None
    article_number: str | None = None
    article_name: str | None = None
    matched: bool
    ambiguous: bool


def _resolved_out(r):
    return ResolvedPositionOut(
        art_no=r.art_no, qty=r.qty, unit=r.unit, short_text=r.short_text,
        ean=r.ean, net_price=r.net_price, vat=r.vat, article_id=r.article_id,
        article_number=r.article_number, article_name=r.article_name,
        matched=r.matched, ambiguous=r.ambiguous,
    )


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
        positions=[_resolved_out(r) for r in resolved],
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


# --- IDS-Connect: Warenkorb-Roundtrip (Session + Token-gesicherter Rückruf) ---

class CartPositionIn(Schema):
    art_no: str
    qty: Decimal
    unit: str | None = None


class PunchoutSessionIn(Schema):
    action: str = "WKE"
    quote_id: UUID | None = None
    # Nur für WKS: der aktuelle Angebots-Warenkorb, den der Shop übernehmen soll.
    positions: list[CartPositionIn] = []


class PunchoutSessionStartOut(Schema):
    session_id: UUID
    action: str
    punchout: PunchoutOut


class PunchoutSessionOut(Schema):
    id: UUID
    connection_id: UUID
    quote_id: UUID | None = None
    action: str
    status: str
    redeemed_at: datetime | None = None
    total: int
    matched: int
    positions: list[ResolvedPositionOut]


# Basis-URL des Rückgabe-Endpunkts (ohne Token). Muss zur Router-Registrierung
# in api.py passen (`/api` + `/pricing`).
_HOOK_PFAD = "/api/pricing/warenkorb-return/"


@router.post(
    "/supplier-connections/{connection_id}/punchout-session",
    response={201: PunchoutSessionStartOut},
    auth=django_auth,
)
def start_punchout_session(request, connection_id: UUID, payload: PunchoutSessionIn):
    """Startet einen IDS-Warenkorb-Roundtrip: legt eine token-gesicherte Session an
    und liefert das Punchout-Formular (mit hookurl auf den Rückgabe-Endpunkt).

    Der Client submittet `punchout` als POST-Formular an den Shop; der Shop meldet
    den fertigen Warenkorb an die hookurl zurück. Bei `action='WKS'` wird der
    aktuelle Angebots-Warenkorb (`positions`) mitgegeben. Recht `pricing/AENDERN`
    (die Antwort enthält das Klartext-Passwort im Punchout-Formular). Fehlende
    URL/Zugangsdaten → 422.
    """
    actor, _ = require(request, "pricing", "AENDERN")
    _connection_or_404(connection_id)
    hook_base = request.build_absolute_uri(_HOOK_PFAD)
    positions = [
        ids_warenkorb_service.CartPosition(art_no=p.art_no, qty=p.qty, unit=p.unit)
        for p in payload.positions
    ]
    try:
        session, punchout_form = punchout_service.start_session(
            actor,
            connection_id=connection_id,
            hook_base=hook_base,
            action=payload.action,
            quote_id=payload.quote_id,
            positions=positions,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, PunchoutSessionStartOut(
        session_id=session.id, action=session.action,
        punchout=PunchoutOut(**punchout_form),
    ))


@router.get("/punchout-sessions/{session_id}", response=PunchoutSessionOut)
def get_punchout_session(request, session_id: UUID):
    """Status einer Punchout-Session; sobald sie eingelöst ist, die aufgelösten
    Warenkorb-Positionen (Vorschau fürs Übernehmen ins Angebot). Recht
    `pricing/LESEN`. Das Frontend pollt diesen Endpunkt nach dem Punchout."""
    require(request, "pricing", "LESEN")
    session, resolved = punchout_service.session_preview(session_id)
    if session is None:
        raise HttpError(404, "Punchout-Session nicht gefunden.")
    return PunchoutSessionOut(
        id=session.id,
        connection_id=session.connection_id,
        quote_id=session.quote_id,
        action=session.action,
        status=session.status,
        redeemed_at=session.redeemed_at,
        total=len(resolved),
        matched=sum(1 for r in resolved if r.matched),
        positions=[_resolved_out(r) for r in resolved],
    )


def _extract_cart_bytes(request) -> bytes:
    """Zieht das Warenkorb-XML aus dem Shop-Rückruf.

    itek-Shops POSTen den Warenkorb je nach Konfiguration als Formularfeld
    (`warenkorb`/`Warenkorb`), als Datei-Upload oder als reinen XML-Body. Alle drei
    werden akzeptiert; Formular-/Datei-Werte werden verlustfrei zu Bytes gemacht.
    """
    ct = (request.content_type or "").lower()
    if ct.startswith("multipart/") or ct.startswith("application/x-www-form-urlencoded"):
        for key in ("warenkorb", "Warenkorb"):
            datei = request.FILES.get(key)
            if datei is not None:
                return datei.read()
            wert = request.POST.get(key)
            if wert:
                return wert.encode(request.encoding or "utf-8")
        # Formular-POST ohne das erwartete Feld: NICHT auf request.body
        # zurückfallen (das Lesen von POST hat den Stream verbraucht → sonst
        # RawPostDataException/500). Leerer Korb → parse meldet sauber 422.
        return b""
    return request.body


@router.post("/warenkorb-return/{token}", auth=None)
def warenkorb_return(request, token: str):
    """**Unauthentifizierter** Rückgabe-Endpunkt: der Händler-Shop POSTet hierher
    den fertigen Warenkorb (die hookurl aus dem Punchout trägt das Token).

    Autorisierung ist das Token (in der DB nur als Hash). Der Warenkorb wird der
    Session zugeordnet und gespeichert; das Frontend liest ihn über
    `GET /punchout-sessions/{id}`. Antwort ist eine schlichte HTML-Bestätigung, die
    im Shop-Browserfenster erscheint. Ungültiges/abgelaufenes Token oder XML → die
    Bestätigungsseite nennt den Fehler (Status 200/422), damit der Nutzer nicht vor
    einer nackten API-Fehlerseite steht.
    """
    try:
        punchout_service.receive_cart(token, _extract_cart_bytes(request))
    except (punchout_service.PunchoutError,
            ids_warenkorb_service.WarenkorbError) as exc:
        return HttpResponse(_return_html(fehler=str(exc)), status=422,
                            content_type="text/html; charset=utf-8")
    return HttpResponse(_return_html(fehler=None),
                        content_type="text/html; charset=utf-8")


# --- DATANORM-Import (Datei-Upload) ----------------------------------------

class DatanormBeispielOut(Schema):
    artikelnummer: str
    bezeichnung: str
    aktion: str
    einkaufspreis: str | None = None


class DatanormImportOut(Schema):
    namespace: str
    version: str | None = None
    waehrung: str | None = None
    stand: str | None = None
    angelegt: int
    aktualisiert: int
    deaktiviert: int
    ohne_einkaufspreis: int
    verarbeitet: int
    fehler: list[str]
    beispiele: list[DatanormBeispielOut]
    dry_run: bool


def _datei_bytes(datei: UploadedFile | None, *, pflicht: bool, feld: str) -> bytes | None:
    if datei is None:
        if pflicht:
            raise HttpError(422, f"{feld} ist erforderlich.")
        return None
    if datei.size and datei.size > DATANORM_MAX_UPLOAD:
        raise HttpError(
            422,
            f"{feld} ist zu groß ({datei.size / 1_048_576:.0f} MB, max. "
            f"{DATANORM_MAX_UPLOAD // 1_048_576} MB). Für Vollkataloge das "
            "CLI-Kommando nutzen.",
        )
    return datei.read()


@router.post(
    "/supplier-connections/{connection_id}/imports/datanorm",
    response=DatanormImportOut,
    auth=django_auth,
)
def datanorm_import(
    request,
    connection_id: UUID,
    stamm: UploadedFile = NinjaFile(...),
    preise: UploadedFile | None = NinjaFile(None),
    dry_run: bool = Form(False),
):
    """Importiert eine DATANORM-Datei (Stammdatei + optionale Preisdatei) gegen die
    Anbindung — legt Artikel/Preise an oder aktualisiert sie (Upsert).

    `dry_run=true` liefert die Auswertung, ohne zu schreiben (Vorschau). Recht
    `pricing/ANLEGEN`. Ungültige Datei/Anbindung → 422. Für Vollkataloge (mehrere
    GB) das CLI-Kommando `datanorm_import` verwenden.
    """
    actor = require_create(request, "pricing", "ANLEGEN")
    _connection_or_404(connection_id)
    stamm_bytes = _datei_bytes(stamm, pflicht=True, feld="Die Stammdatei")
    preise_bytes = _datei_bytes(preise, pflicht=False, feld="Die Preisdatei")
    try:
        ergebnis = datanorm_import_service.import_datanorm(
            actor,
            connection_id=connection_id,
            stamm_bytes=stamm_bytes,
            preise_bytes=preise_bytes,
            dry_run=dry_run,
        )
    except ValueError as exc:
        # DatanormImportFehler und (über as_business_error) übersetzte Gate-Fehler
        # sind ValueError → 422. Hinweis: der Import committet je Batch, ein Fehler
        # mitten drin kann also einen Teilimport hinterlassen; ein erneuter Lauf ist
        # dank Upsert idempotent.
        raise HttpError(422, str(exc))
    return DatanormImportOut(**ergebnis)


def _return_html(*, fehler: str | None) -> str:
    """Minimale, in sich geschlossene Bestätigungsseite (kein externer Inhalt)."""
    if fehler:
        titel = "Warenkorb konnte nicht übernommen werden"
        # fehler ist eine kontrollierte Servermeldung; zur Sicherheit escapen.
        from html import escape
        text = escape(fehler)
        farbe = "#db9c4d"
    else:
        titel = "Warenkorb empfangen"
        text = "Sie können dieses Fenster schließen und zu MCN zurückkehren."
        farbe = "#9fcd99"
    return (
        "<!doctype html><html lang=de><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{titel}</title></head>"
        "<body style='font-family:system-ui,sans-serif;background:#1c3244;"
        "color:#fff;display:flex;min-height:100vh;margin:0;align-items:center;"
        "justify-content:center'>"
        "<main style='max-width:32rem;padding:2rem;text-align:center'>"
        f"<div style='font-size:3rem;color:{farbe}'>&#10003;</div>"
        f"<h1 style='font-size:1.4rem'>{titel}</h1>"
        f"<p style='opacity:.85'>{text}</p></main></body></html>"
    )
