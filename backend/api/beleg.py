"""Beleg-API — Angebote (invoicing.quote) inkl. Positionen.

Lesen in der Dev-Phase ohne Auth; Anlegen verlangt Django-Session + app_user.
Deckt Anlage bis ENTWURF sowie Liste/Detail ab (Versand-Workflow folgt separat).
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Q
from django.http import HttpResponse
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.mail_crypto import MailKeyError
from db_core.models import Invoice, Quote
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf as beleg_pdf_service
from db_core.services import beleg_versand as beleg_versand_service
from db_core.services import erechnung as erechnung_service
from db_core.services.mail import MailSendError

router = Router()


# --- Schemas ---------------------------------------------------------------

class PropertyRefOut(Schema):
    id: UUID
    property_number: str
    name: str
    city: str


class QuoteOut(Schema):
    id: UUID
    quote_number: str | None = None
    title: str
    status: str
    currency: str
    quote_date: date | None = None
    net_total: Decimal | None = None
    gross_total: Decimal | None = None
    property: PropertyRefOut


class QuoteListOut(Schema):
    items: list[QuoteOut]
    total: int
    page: int
    page_size: int


class QuoteLineOut(Schema):
    position_number: int
    line_type: str
    # NORMAL | ALTERNATIV | BEDARF — Alternativ/Bedarf zählen nicht in die Summe.
    line_kind: str = "NORMAL"
    # Abschnittsnummer (1-basiert), null = keinem Abschnitt zugeordnet.
    rubrik: int | None = None
    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    discount_percent: Decimal | None = None
    tax_code: str | None = None
    tax_rate_percent: Decimal | None = None
    net_amount: Decimal | None = None
    # § 35a-Arbeitskostenanteil (netto) dieser Position. **null = unbestimmt**,
    # nicht 0,00 — dann weist der Beleg keine Arbeitskosten aus.
    labour_net_amount: Decimal | None = None
    # Interner Kalkulations-Snapshot (nicht auf dem Kundenbeleg).
    unit_cost: Decimal | None = None
    markup_percent: Decimal | None = None
    source_article_id: UUID | None = None
    source_assembly_id: UUID | None = None
    # Anrechnungsposition einer Schlussrechnung (negativer Betrag). Read-only: der
    # Editor darf sie nicht ändern — sie wird aus der Verkettung erzeugt.
    advance_invoice_id: UUID | None = None


class RubrikOut(Schema):
    position_number: int
    title: str
    description: str | None = None


class ProjectRefOut(Schema):
    id: UUID
    project_number: str
    name: str


class QuoteDetailOut(QuoteOut):
    valid_until_date: date | None = None
    tax_total: Decimal | None = None
    version: int
    project: ProjectRefOut | None = None
    sent_at: datetime | None = None
    has_snapshot: bool = False
    content_hash: str | None = None
    # Vorbelegung für den E-Mail-Versand: primäre EMAIL der best-effort über den
    # Auftrag abgeleiteten Empfängerpartei (INVOICE_RECIPIENT, sonst PRINCIPAL).
    # Nur bei versendeten Angeboten aufgelöst; null, wenn kein Auftrag/kein
    # Kommunikationsweg hinterlegt ist (dann trägt der Nutzer sie manuell ein).
    recipient_email: str | None = None
    rubriken: list[RubrikOut] = []
    lines: list[QuoteLineOut]


class QuoteLineIn(Schema):
    line_type: str
    description: str
    line_kind: str = "NORMAL"
    rubrik: int | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    discount_percent: Decimal | None = None
    tax_code: str | None = None
    # § 35a-Arbeitskostenanteil (netto). Weglassen = vom Server ableiten
    # (ARBEITSZEIT/FAHRT voll, MATERIAL 0,00, sonst unbestimmt); ein gesetzter
    # Wert gewinnt immer und muss ein Teil des Positionsbetrags sein.
    labour_net_amount: Decimal | None = None
    unit_cost: Decimal | None = None
    markup_percent: Decimal | None = None
    sale_price_group_id: UUID | None = None
    source_article_id: UUID | None = None
    source_assembly_id: UUID | None = None


class RubrikIn(Schema):
    title: str
    description: str | None = None


class QuoteIn(Schema):
    property_id: UUID
    title: str
    project_id: UUID | None = None
    quote_date: date | None = None
    valid_until_date: date | None = None
    rubriken: list[RubrikIn] = []
    lines: list[QuoteLineIn] = []


# --- Kalkulationsübersicht -------------------------------------------------

class KalkAbschnittOut(Schema):
    """Ein Abschnitt der internen Kalkulation. `rubrik=null` = „Ohne Abschnitt"."""
    rubrik: int | None = None
    title: str
    description: str | None = None
    netto: Decimal
    ek: Decimal
    deckungsbeitrag: Decimal | None = None
    marge_prozent: Decimal | None = None
    # Sagt dem UI, dass die Marge nicht berechenbar war (fehlender EK), statt eine
    # 0 zu zeigen, die wie „kein Gewinn" aussähe.
    ek_vollstaendig: bool
    positionen: int
    positionen_ohne_ek: int
    alternativ_netto: Decimal
    bedarf_netto: Decimal
    arbeitszeit: Decimal


class KalkulationOut(Schema):
    abschnitte: list[KalkAbschnittOut]
    gesamt: KalkAbschnittOut


class QuoteFilter(Schema):
    q: str | None = None
    status: str | None = None
    property_id: UUID | None = None
    project_id: UUID | None = None


def _property_ref(quote):
    p = quote.property
    return PropertyRefOut(
        id=p.id, property_number=p.property_number, name=p.name, city=p.address.city
    )


def _quote_out(quote):
    return QuoteOut(
        id=quote.id,
        quote_number=quote.quote_number,
        title=quote.title,
        status=quote.status,
        currency=quote.currency,
        quote_date=quote.quote_date,
        net_total=quote.net_total,
        gross_total=quote.gross_total,
        property=_property_ref(quote),
    )


# --- Lesende Endpoints -----------------------------------------------------

@router.get("/quotes", response=QuoteListOut)
def list_quotes(
    request,
    filters: QuoteFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Angebote auflisten: Suche (Titel/Nummer), Status-/Liegenschafts-/Projektfilter."""
    require(request, "invoicing", "LESEN")
    qs = Quote.objects.select_related("property__address")

    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(Q(title__icontains=needle) | Q(quote_number__icontains=needle))
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.property_id:
        qs = qs.filter(property_id=filters.property_id)
    if filters.project_id:
        qs = qs.filter(project_id=filters.project_id)

    qs = qs.order_by("-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_quote_out(q) for q in qs[start:start + page_size]]
    return QuoteListOut(items=items, total=total, page=page, page_size=page_size)


def _rubrik_nummern(beleg):
    """UUID → Abschnittsnummer. Die API spricht in Nummern, nicht in Fremdschlüsseln."""
    return {r.id: r.position_number for r in beleg.rubriken.all()}


def _rubriken_out(beleg):
    return [
        RubrikOut(
            position_number=r.position_number, title=r.title, description=r.description
        )
        for r in sorted(beleg.rubriken.all(), key=lambda r: r.position_number)
    ]


def _quote_detail(quote_id):
    quote = (
        Quote.objects.filter(id=quote_id)
        .select_related("property__address", "project", "work_order")
        .prefetch_related("lines", "rubriken", "work_order__parties__party")
        .first()
    )
    if quote is None:
        raise HttpError(404, "Angebot nicht gefunden.")

    nummern = _rubrik_nummern(quote)
    lines = [
        QuoteLineOut(
            position_number=l.position_number,
            line_type=l.line_type,
            line_kind=l.line_kind,
            rubrik=nummern.get(l.rubrik_id),
            description=l.description,
            quantity=l.quantity,
            unit=l.unit,
            unit_price=l.unit_price,
            discount_percent=l.discount_percent,
            tax_code=l.tax_code_id,
            tax_rate_percent=l.tax_rate_percent,
            net_amount=l.net_amount,
            labour_net_amount=l.labour_net_amount,
            unit_cost=l.unit_cost,
            markup_percent=l.markup_percent,
            source_article_id=l.source_article_id,
            source_assembly_id=l.source_assembly_id,
        )
        for l in sorted(quote.lines.all(), key=lambda l: l.position_number)
    ]
    project = (
        ProjectRefOut(
            id=quote.project.id,
            project_number=quote.project.project_number,
            name=quote.project.name,
        )
        if quote.project_id
        else None
    )
    # Empfänger-E-Mail nur für den Versand relevant (nur versendete Angebote
    # lassen sich senden) — für Entwürfe die zusätzliche Auflösung sparen.
    recipient_email = (
        beleg_versand_service.quote_recipient_email(quote)
        if quote.status == "VERSENDET"
        else None
    )
    return QuoteDetailOut(
        id=quote.id,
        quote_number=quote.quote_number,
        title=quote.title,
        status=quote.status,
        currency=quote.currency,
        quote_date=quote.quote_date,
        valid_until_date=quote.valid_until_date,
        net_total=quote.net_total,
        tax_total=quote.tax_total,
        gross_total=quote.gross_total,
        version=quote.version,
        property=_property_ref(quote),
        project=project,
        sent_at=quote.sent_at,
        has_snapshot=quote.billing_snapshot is not None,
        content_hash=quote.content_hash,
        recipient_email=recipient_email,
        rubriken=_rubriken_out(quote),
        lines=lines,
    )


# --- Schreibender Endpoint (Session-Auth Pflicht) --------------------------

@router.post("/quotes", response={201: QuoteDetailOut}, auth=django_auth)
def create_quote(request, payload: QuoteIn):
    """Neues Angebot (Status ENTWURF) mit Positionen anlegen."""
    actor, _ = require(request, "invoicing", "ANLEGEN")
    try:
        quote = beleg_service.create_quote(
            actor,
            property_id=payload.property_id,
            title=payload.title,
            project_id=payload.project_id,
            quote_date=payload.quote_date,
            valid_until_date=payload.valid_until_date,
            rubriken=[r.dict() for r in payload.rubriken],
            lines=[line.dict() for line in payload.lines],
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _quote_detail(quote.id))


class QuoteUpdateIn(Schema):
    """Der Editor schickt immer den ganzen Beleg: Positionen und Abschnitte werden
    vollständig ersetzt. Weggelassene Kopffelder bleiben unverändert."""
    title: str | None = None
    quote_date: date | None = None
    valid_until_date: date | None = None
    rubriken: list[RubrikIn] | None = None
    lines: list[QuoteLineIn] | None = None


@router.put("/quotes/{quote_id}", response=QuoteDetailOut, auth=django_auth)
def update_quote(request, quote_id: UUID, payload: QuoteUpdateIn):
    """Angebotsentwurf ändern. Ab VERSENDET friert die DB den Beleg ein (422)."""
    actor, _ = require(request, "invoicing", "AENDERN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        beleg_service.update_quote(
            actor,
            quote_id=quote_id,
            title=payload.title,
            # Sentinel `...` unterscheidet „leeren" (None) von „nicht ändern".
            quote_date=payload.quote_date if "quote_date" in gesetzt else ...,
            valid_until_date=(
                payload.valid_until_date if "valid_until_date" in gesetzt else ...
            ),
            rubriken=[r.dict() for r in payload.rubriken or []],
            lines=(
                [line.dict() for line in payload.lines]
                if payload.lines is not None
                else None
            ),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _quote_detail(quote_id)


@router.get("/quotes/{quote_id}/kalkulation", response=KalkulationOut)
def quote_kalkulation(request, quote_id: UUID):
    """Interne Kalkulationsübersicht je Abschnitt (EK, Deckungsbeitrag, Marge).

    Enthält die Einkaufspreise — deshalb ein eigenes Recht: wer ein Angebot lesen
    darf, darf nicht zwangsläufig die Marge sehen. `pricing/LESEN` gatet den
    Artikelstamm samt EK und ist damit das passende Tor.
    """
    require(request, "pricing", "LESEN")
    try:
        return beleg_service.quote_kalkulation(quote_id)
    except ValueError as exc:
        raise HttpError(404, str(exc))


@router.post("/quotes/{quote_id}/send", response=QuoteDetailOut, auth=django_auth)
def send_quote(request, quote_id: UUID):
    """Angebot versenden (ENTWURF → … → VERSENDET); DB vergibt die AN-Nummer und
    friert den Beleg ein."""
    actor, _ = require(request, "invoicing", "VERSENDEN")
    try:
        beleg_service.send_quote(actor, quote_id=quote_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _quote_detail(quote_id)


@router.get("/quotes/{quote_id}/pdf")
def quote_pdf(request, quote_id: UUID):
    """PDF-Ausfertigung eines versendeten Angebots.

    Beim ersten Abruf wird die Ausfertigung archiviert (MinIO +
    content.file/file_link, link_category='BELEG_PDF'); jeder weitere Abruf
    liefert dieselbe archivierte Datei aus. Ist der Objektspeicher nicht
    erreichbar, wird on-the-fly ausgeliefert (Degradation).

    Nur versendete Angebote (ab VERSENDET) erhalten eine Ausfertigung; für
    Entwürfe/unbekannte Angebote → 404. Das Archivieren ist ein automatischer
    Nebeneffekt des Lesens (kein eigenes Recht) — die actor_id wird nur als
    uploaded_by/created_by fürs Audit geführt."""
    actor, _ = require(request, "invoicing", "LESEN")
    pdf = beleg_pdf_service.get_or_archive_quote_pdf(actor, quote_id)
    if pdf is None:
        raise HttpError(404, "Versendetes Angebot nicht gefunden.")
    quote = Quote.objects.filter(id=quote_id).only("quote_number", "id").first()
    raw = quote.quote_number or str(quote_id)
    # Dateinamen auf unbedenkliche Zeichen beschränken (Defense-in-Depth gegen
    # Header-Injection; Belegnummern sind ohnehin AN-Format).
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{safe or "angebot"}.pdf"'
    return response


class QuoteEmailIn(Schema):
    # Optional: überschreibt die abgeleitete Empfänger-Adresse (der Nutzer darf sie
    # im Dialog bestätigen/korrigieren). Weglassen = ableiten.
    to_address: str | None = None


class QuoteEmailOut(Schema):
    sent: bool
    to_address: str


@router.post(
    "/quotes/{quote_id}/send-email", response=QuoteEmailOut, auth=django_auth
)
def send_quote_email(request, quote_id: UUID, payload: QuoteEmailIn):
    """Versendet ein versendetes Angebot als PDF-Anhang per E-Mail.

    Recht VERSENDEN: der Belegversand ist eine nach außen wirkende
    Kundenkommunikation — dasselbe Recht wie Rechnungsversand und Mahnung. Reine
    Zustellung: kein Statuswechsel, keine GoBD-Berührung (das Angebot ist mit dem
    Versand bereits festgeschrieben). Der Versand protokolliert eine
    content.communication-Zeile.

    Fehler → 422 (passwortfrei): Entwurf/unbekannt, kein Empfänger/keine E-Mail,
    kein Mailkonto, Schlüssel- oder SMTP-Fehler.
    """
    actor, _ = require(request, "invoicing", "VERSENDEN")
    try:
        communication = beleg_versand_service.send_quote_email(
            actor, quote_id=quote_id, to_address=payload.to_address
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    except (MailKeyError, MailSendError) as exc:
        # Passwortfreie, klare Meldung an das UI statt eines 500-Leaks.
        raise HttpError(422, str(exc))
    return QuoteEmailOut(sent=True, to_address=communication.counterpart_raw)


@router.get("/quotes/{quote_id}", response=QuoteDetailOut)
def get_quote(request, quote_id: UUID):
    """Detail eines Angebots inkl. Positionen."""
    require(request, "invoicing", "LESEN")
    return _quote_detail(quote_id)


# --- Rechnungen (invoicing.invoice) ----------------------------------------

class InvoiceOut(Schema):
    id: UUID
    invoice_number: str | None = None
    invoice_type: str
    status: str
    currency: str
    invoice_date: date | None = None
    net_total: Decimal | None = None
    gross_total: Decimal | None = None
    property: PropertyRefOut


class InvoiceListOut(Schema):
    items: list[InvoiceOut]
    total: int
    page: int
    page_size: int


class InvoicePartyOut(Schema):
    party_id: UUID
    display_name: str
    role: str
    is_primary: bool
    allocation_percent: Decimal | None = None


class AdvanceSteuergruppeOut(Schema):
    tax_code: str
    tax_rate_percent: Decimal
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal


class InvoiceAdvanceOut(Schema):
    """Eine von dieser Schlussrechnung angerechnete Abschlags-/Teilrechnung."""
    advance_invoice_id: UUID
    invoice_number: str | None = None
    invoice_type: str
    invoice_date: date | None = None
    net_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal
    steuergruppen: list[AdvanceSteuergruppeOut] = []


class FinalInvoiceRefOut(Schema):
    """Die Schlussrechnung, die diese Abschlagsrechnung anrechnet (Gegenrichtung)."""
    id: UUID
    invoice_number: str | None = None
    invoice_date: date | None = None
    status: str


class AnrechenbarerAbschlagOut(Schema):
    id: UUID
    invoice_number: str | None = None
    invoice_type: str
    invoice_date: date | None = None
    net_total: Decimal | None = None
    tax_total: Decimal | None = None
    gross_total: Decimal | None = None
    # In einem ANDEREN Schlussrechnungs-Entwurf bereits vorgemerkt (bindet nicht).
    vorgemerkt: bool = False
    # Von DIESER Schlussrechnung bereits angerechnet (Häkchen im UI).
    angerechnet: bool = False


class ArbeitskostenOut(Schema):
    """Der § 35a-Ausweis (Lohn-, Maschinen-, Fahrtkosten) — vom Server gerechnet.

    `bestimmbar=false` heißt: der Beleg weist nichts aus. `grund` sagt warum:

    - `OFFENE_POSITIONEN` — mindestens eine Position hat ihren Anteil nicht
      bestimmt; `offen` nennt die Positionsnummern.
    - `UNSTIMMIG` — das Ergebnis ist kein Teil des Rechnungsbetrags (negativ oder
      größer als er). Entsteht nur bei einer Schlussrechnung, deren angerechneter
      Abschlag fehlerhaft erfasst wurde.

    Die Beträge sind dann **null = unbekannt, nicht 0**.
    """
    bestimmbar: bool
    grund: str | None = None
    offen: list[int] = []
    net_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    gross_amount: Decimal | None = None


class InvoiceDetailOut(InvoiceOut):
    due_date: date | None = None
    tax_total: Decimal | None = None
    # Zahlungsbedingungen je Rechnung (Migration 0058).
    payment_term_days: int | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None
    # § 35a EStG (Migration 0076): Ausweis-Schalter + der berechnete Ausweis.
    show_labour_costs: bool = True
    arbeitskosten: ArbeitskostenOut | None = None
    # Abgeleitet (read-only) aus Belegdatum, Bruttobetrag und Skonto — der Server
    # rechnet, das UI zeigt nur an.
    skonto_bis: date | None = None
    skonto_betrag: Decimal | None = None
    skonto_zahlbetrag: Decimal | None = None
    version: int
    project: ProjectRefOut | None = None
    work_order_number: str | None = None
    published_at: datetime | None = None
    has_snapshot: bool = False
    content_hash: str | None = None
    # Vorbelegung für den E-Mail-Versand: primäre EMAIL der Empfängerpartei
    # (INVOICE_RECIPIENT, sonst INVOICE_DEBTOR). Nur bei veröffentlichten Rechnungen
    # aufgelöst; null, wenn kein Kommunikationsweg hinterlegt ist.
    recipient_email: str | None = None
    parties: list[InvoicePartyOut] = []
    rubriken: list[RubrikOut] = []
    lines: list[QuoteLineOut]
    # Schlussrechnung → angerechnete Abschläge (Verkettung, GoBD).
    advances: list[InvoiceAdvanceOut] = []
    # Abschlags-/Teilrechnung → die Schlussrechnung, die sie anrechnet
    # (Gegenrichtung derselben Kette; die Rechnungsmappe zeigt beide Wege).
    angerechnet_in: FinalInvoiceRefOut | None = None
    # Nur bei Anrechnung: Leistung vor Abzug (Kopfsummen + Anrechnung). Der
    # Zahlbetrag ist gross_total.
    leistung_netto: Decimal | None = None
    leistung_steuer: Decimal | None = None
    leistung_brutto: Decimal | None = None


class InvoiceIn(Schema):
    property_id: UUID
    invoice_type: str = "RECHNUNG"
    project_id: UUID | None = None
    work_order_id: UUID | None = None
    reference_invoice_id: UUID | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    payment_term_days: int | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None
    # § 35a-Ausweis. Default true: der Privatkunde ist der Regelfall, und ein
    # vergessener Haken kostet ihn 20 % der Arbeitskosten.
    show_labour_costs: bool = True
    rubriken: list[RubrikIn] = []
    lines: list[QuoteLineIn] = []
    # Nur SCHLUSSRECHNUNG: die anzurechnenden Abschlags-/Teilrechnungen. Die
    # negativen Anrechnungspositionen je Steuersatz erzeugt der Server.
    advance_invoice_ids: list[UUID] = []


class InvoicePartyIn(Schema):
    party_id: UUID
    role: str
    is_primary: bool = False
    allocation_percent: Decimal | None = None
    liability_group: str | None = None
    liability_basis: str | None = None


class InvoiceFilter(Schema):
    q: str | None = None
    status: str | None = None
    invoice_type: str | None = None
    property_id: UUID | None = None
    project_id: UUID | None = None


def _invoice_out(invoice):
    return InvoiceOut(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_type=invoice.invoice_type,
        status=invoice.status,
        currency=invoice.currency,
        invoice_date=invoice.invoice_date,
        net_total=invoice.net_total,
        gross_total=invoice.gross_total,
        property=_property_ref(invoice),
    )


@router.get("/invoices", response=InvoiceListOut)
def list_invoices(
    request,
    filters: InvoiceFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Rechnungen auflisten: Suche (Nummer), Status-/Typ-/Objekt-/Projektfilter."""
    require(request, "invoicing", "LESEN")
    qs = Invoice.objects.select_related("property__address")
    if filters.q:
        qs = qs.filter(invoice_number__icontains=filters.q.strip())
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.invoice_type:
        qs = qs.filter(invoice_type=filters.invoice_type)
    if filters.property_id:
        qs = qs.filter(property_id=filters.property_id)
    if filters.project_id:
        qs = qs.filter(project_id=filters.project_id)
    qs = qs.order_by("-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_invoice_out(i) for i in qs[start:start + page_size]]
    return InvoiceListOut(items=items, total=total, page=page, page_size=page_size)


def _invoice_detail(invoice_id):
    invoice = (
        Invoice.objects.filter(id=invoice_id)
        .select_related("property__address", "project", "work_order")
        .prefetch_related("lines", "parties__party", "rubriken")
        .first()
    )
    if invoice is None:
        raise HttpError(404, "Rechnung nicht gefunden.")

    nummern = _rubrik_nummern(invoice)
    lines = [
        QuoteLineOut(
            position_number=l.position_number,
            line_type=l.line_type,
            line_kind=l.line_kind,
            rubrik=nummern.get(l.rubrik_id),
            description=l.description,
            quantity=l.quantity,
            unit=l.unit,
            unit_price=l.unit_price,
            discount_percent=l.discount_percent,
            tax_code=l.tax_code_id,
            tax_rate_percent=l.tax_rate_percent,
            net_amount=l.net_amount,
            labour_net_amount=l.labour_net_amount,
            unit_cost=l.unit_cost,
            markup_percent=l.markup_percent,
            source_article_id=l.source_article_id,
            source_assembly_id=l.source_assembly_id,
            advance_invoice_id=l.advance_invoice_id,
        )
        for l in sorted(invoice.lines.all(), key=lambda l: l.position_number)
    ]
    parties = [
        InvoicePartyOut(
            party_id=p.party.id,
            display_name=p.party.display_name,
            role=p.role,
            is_primary=p.is_primary,
            allocation_percent=p.allocation_percent,
        )
        for p in sorted(invoice.parties.all(), key=lambda p: (p.role, not p.is_primary))
    ]
    project = (
        ProjectRefOut(
            id=invoice.project.id,
            project_number=invoice.project.project_number,
            name=invoice.project.name,
        )
        if invoice.project_id
        else None
    )
    # Empfänger-E-Mail nur für den Versand relevant (nur veröffentlichte Belege
    # lassen sich senden) — für Entwürfe die zusätzliche contact_point-Abfrage sparen.
    recipient_email = (
        beleg_versand_service.recipient_email(invoice)
        if invoice.status == "VEROEFFENTLICHT"
        else None
    )
    # Einzige Rechenstelle für Skonto (dieselbe, die das PDF nutzt).
    zb = beleg_service.zahlungsbedingungen(invoice) or {}

    # Verkettung Abschlag ↔ Schlussrechnung in BEIDE Richtungen.
    advances = (
        beleg_service.anrechnungen(invoice)
        if invoice.invoice_type == beleg_service.FINAL_TYPE
        else []
    )
    spiegel = beleg_service.leistungssummen(invoice, advances) if advances else None
    angerechnet_in = None
    if invoice.invoice_type in beleg_service.ADVANCE_TYPES:
        # Mehrere Kandidaten sind möglich, solange sie ENTWÜRFE sind (ein Entwurf
        # bindet nichts). Die veröffentlichte Schlussrechnung ist die verbindliche
        # Aussage — sie gewinnt.
        # distinct(): eine Schlussrechnung trägt je Steuersatz eine Verkettungszeile
        # — ohne distinct käme sie mehrfach zurück.
        finals = list(
            Invoice.objects.filter(advances__advance_invoice_id=invoice.id)
            .only("id", "invoice_number", "invoice_date", "status")
            .order_by("-published_at", "-created_at")
            .distinct()
        )
        final = next(
            (f for f in finals if f.status == "VEROEFFENTLICHT"),
            finals[0] if finals else None,
        )
        if final is not None:
            angerechnet_in = FinalInvoiceRefOut(
                id=final.id,
                invoice_number=final.invoice_number,
                invoice_date=final.invoice_date,
                status=final.status,
            )
    return InvoiceDetailOut(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_type=invoice.invoice_type,
        status=invoice.status,
        currency=invoice.currency,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        payment_term_days=invoice.payment_term_days,
        discount_percent=invoice.discount_percent,
        discount_days=invoice.discount_days,
        skonto_bis=zb.get("skonto_bis"),
        skonto_betrag=zb.get("skonto_betrag"),
        skonto_zahlbetrag=zb.get("skonto_zahlbetrag"),
        show_labour_costs=invoice.show_labour_costs,
        # Einzige Rechenstelle des § 35a-Ausweises (dieselbe, die das PDF nutzt).
        arbeitskosten=ArbeitskostenOut(**beleg_service.arbeitskosten(invoice)),
        net_total=invoice.net_total,
        tax_total=invoice.tax_total,
        gross_total=invoice.gross_total,
        version=invoice.version,
        property=_property_ref(invoice),
        project=project,
        work_order_number=(
            invoice.work_order.order_number if invoice.work_order_id else None
        ),
        published_at=invoice.published_at,
        has_snapshot=invoice.billing_snapshot is not None,
        content_hash=invoice.content_hash,
        recipient_email=recipient_email,
        parties=parties,
        rubriken=_rubriken_out(invoice),
        lines=lines,
        advances=[InvoiceAdvanceOut(**a) for a in advances],
        angerechnet_in=angerechnet_in,
        leistung_netto=(spiegel or {}).get("leistung_net"),
        leistung_steuer=(spiegel or {}).get("leistung_tax"),
        leistung_brutto=(spiegel or {}).get("leistung_gross"),
    )


@router.post("/invoices", response={201: InvoiceDetailOut}, auth=django_auth)
def create_invoice(request, payload: InvoiceIn):
    """Neue Rechnung (Status ENTWURF) mit Positionen anlegen.

    Belegart: RECHNUNG, ABSCHLAGSRECHNUNG, TEILRECHNUNG oder SCHLUSSRECHNUNG
    (Gutschrift/Storno entstehen nur als Folgebeleg). Bei einer Schlussrechnung
    rechnen `advance_invoice_ids` die genannten Abschläge an — die negativen
    Anrechnungspositionen je Steuersatz erzeugt der Server.
    """
    actor, _ = require(request, "invoicing", "ANLEGEN")
    try:
        invoice = beleg_service.create_invoice(
            actor,
            property_id=payload.property_id,
            invoice_type=payload.invoice_type,
            project_id=payload.project_id,
            work_order_id=payload.work_order_id,
            reference_invoice_id=payload.reference_invoice_id,
            invoice_date=payload.invoice_date,
            due_date=payload.due_date,
            payment_term_days=payload.payment_term_days,
            discount_percent=payload.discount_percent,
            discount_days=payload.discount_days,
            show_labour_costs=payload.show_labour_costs,
            rubriken=[r.dict() for r in payload.rubriken],
            lines=[line.dict() for line in payload.lines],
            advance_invoice_ids=payload.advance_invoice_ids,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _invoice_detail(invoice.id))


@router.get("/invoices/anrechenbare-abschlaege", response=list[AnrechenbarerAbschlagOut])
def anrechenbare_abschlaege(
    request,
    work_order_id: UUID | None = None,
    final_invoice_id: UUID | None = None,
):
    """Die anrechenbaren Abschlags-/Teilrechnungen eines Auftrags.

    Für die Schlussrechnung: veröffentlicht, nicht storniert/gutgeschrieben und
    von keiner veröffentlichten Schlussrechnung angerechnet. `final_invoice_id`
    markiert zusätzlich, was der genannte Schlussrechnungs-Entwurf schon anrechnet.

    `work_order_id` ist fachlich Pflicht, technisch aber optional annotiert: eine
    Pflicht-Query-Annotation ließe django-ninja schon VOR dem View validieren —
    ein Aufruf ohne Recht bekäme dann 422 statt 403 und verriete damit, dass es
    den Endpunkt gibt (test_endpoint_schutz prüft genau das). Erst Recht, dann
    Eingabe.
    """
    require(request, "invoicing", "LESEN")
    if work_order_id is None:
        raise HttpError(422, "work_order_id ist erforderlich.")
    try:
        return beleg_service.anrechenbare_abschlaege(
            work_order_id, final_invoice_id=final_invoice_id
        )
    except ValueError as exc:
        raise HttpError(404, str(exc))


class InvoiceAdvancesIn(Schema):
    advance_invoice_ids: list[UUID] = []


@router.put("/invoices/{invoice_id}/advances", response=InvoiceDetailOut, auth=django_auth)
def set_invoice_advances(request, invoice_id: UUID, payload: InvoiceAdvancesIn):
    """Setzt die angerechneten Abschläge eines Schlussrechnungs-ENTWURFS neu.

    Ersetzt die Verkettung vollständig und baut die Anrechnungspositionen daraus
    neu auf. Nach der Veröffentlichung ist die Anrechnung unveränderlich (422).
    """
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        beleg_service.set_invoice_advances(
            actor,
            invoice_id=invoice_id,
            advance_invoice_ids=payload.advance_invoice_ids,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _invoice_detail(invoice_id)


class InvoiceUpdateIn(Schema):
    """Der Editor schickt immer den ganzen Beleg: Positionen und Abschnitte werden
    vollständig ersetzt. Weggelassene Kopffelder bleiben unverändert. Eine Rechnung
    hat keinen Titel (Identität über Typ + Nummer)."""
    invoice_date: date | None = None
    due_date: date | None = None
    payment_term_days: int | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None
    show_labour_costs: bool | None = None
    rubriken: list[RubrikIn] | None = None
    lines: list[QuoteLineIn] | None = None


@router.put("/invoices/{invoice_id}", response=InvoiceDetailOut, auth=django_auth)
def update_invoice(request, invoice_id: UUID, payload: InvoiceUpdateIn):
    """Rechnungsentwurf ändern. Ab VEROEFFENTLICHT friert die DB den Beleg ein (422)."""
    actor, _ = require(request, "invoicing", "AENDERN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        beleg_service.update_invoice(
            actor,
            invoice_id=invoice_id,
            # Sentinel `...` unterscheidet „leeren" (None) von „nicht ändern".
            invoice_date=payload.invoice_date if "invoice_date" in gesetzt else ...,
            due_date=payload.due_date if "due_date" in gesetzt else ...,
            payment_term_days=(
                payload.payment_term_days if "payment_term_days" in gesetzt else ...
            ),
            discount_percent=(
                payload.discount_percent if "discount_percent" in gesetzt else ...
            ),
            discount_days=(
                payload.discount_days if "discount_days" in gesetzt else ...
            ),
            # `null` heißt „nichts gesagt", nicht „abschalten" — sonst nähme ein
            # Client, der das Feld leer mitschickt, dem Kunden still den Ausweis.
            show_labour_costs=(
                payload.show_labour_costs
                if payload.show_labour_costs is not None
                else ...
            ),
            rubriken=[r.dict() for r in payload.rubriken or []],
            lines=(
                [line.dict() for line in payload.lines]
                if payload.lines is not None
                else None
            ),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _invoice_detail(invoice_id)


@router.get("/invoices/{invoice_id}/kalkulation", response=KalkulationOut)
def invoice_kalkulation(request, invoice_id: UUID):
    """Interne Kalkulationsübersicht je Abschnitt (enthält EK → `pricing/LESEN`)."""
    require(request, "pricing", "LESEN")
    try:
        return beleg_service.invoice_kalkulation(invoice_id)
    except ValueError as exc:
        raise HttpError(404, str(exc))


@router.post(
    "/invoices/{invoice_id}/parties",
    response={201: InvoiceDetailOut},
    auth=django_auth,
)
def add_invoice_party(request, invoice_id: UUID, payload: InvoicePartyIn):
    """Rechnungsbeteiligten (Schuldner/Empfänger …) hinzufügen (nur im Entwurf)."""
    # Beteiligten am Entwurf ergänzen = Änderung am Beleg → AENDERN.
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        beleg_service.add_invoice_party(
            actor,
            invoice_id=invoice_id,
            party_id=payload.party_id,
            role=payload.role,
            is_primary=payload.is_primary,
            allocation_percent=payload.allocation_percent,
            liability_group=payload.liability_group,
            liability_basis=payload.liability_basis,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _invoice_detail(invoice_id))


@router.post("/invoices/{invoice_id}/publish", response=InvoiceDetailOut, auth=django_auth)
def publish_invoice(request, invoice_id: UUID):
    """Rechnung veröffentlichen (ENTWURF → VEROEFFENTLICHT); DB vergibt die Nummer
    und prüft die Tore (Auftrag geprüft, Schuldner/Empfänger)."""
    # Veröffentlichen ist das Freigabetor der Rechnung → FREIGEBEN.
    actor, _ = require(request, "invoicing", "FREIGEBEN")
    try:
        beleg_service.publish_invoice(actor, invoice_id=invoice_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _invoice_detail(invoice_id)


@router.get("/invoices/{invoice_id}", response=InvoiceDetailOut)
def get_invoice(request, invoice_id: UUID):
    """Detail einer Rechnung inkl. Positionen."""
    require(request, "invoicing", "LESEN")
    return _invoice_detail(invoice_id)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(request, invoice_id: UUID):
    """PDF-Ausfertigung einer veröffentlichten Rechnung.

    Beim ersten Abruf wird die Ausfertigung GoBD-fest archiviert (MinIO +
    content.file/file_link); jeder weitere Abruf liefert dieselbe archivierte
    Datei aus. Ist der Objektspeicher nicht erreichbar, wird on-the-fly
    ausgeliefert (Degradation) und die Archivierung später nachgeholt.

    Nur festgeschriebene Belege (VEROEFFENTLICHT) erhalten eine Ausfertigung; für
    Entwürfe/unbekannte Belege → 404. Das Archivieren ist ein automatischer
    Nebeneffekt des Lesens (kein eigenes Recht) — die actor_id wird nur als
    uploaded_by/created_by fürs Audit geführt; die DB-Tore bleiben unberührt."""
    actor, _ = require(request, "invoicing", "LESEN")
    pdf = beleg_pdf_service.get_or_archive_invoice_pdf(actor, invoice_id)
    if pdf is None:
        raise HttpError(404, "Veröffentlichte Rechnung nicht gefunden.")
    invoice = Invoice.objects.filter(id=invoice_id).only("invoice_number").first()
    raw = invoice.invoice_number or str(invoice_id)
    # Dateinamen auf unbedenkliche Zeichen beschränken (Defense-in-Depth gegen
    # Header-Injection; Belegnummern sind ohnehin RE-/GS-Format).
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{safe or "beleg"}.pdf"'
    return response


def _beleg_dateiname(invoice_id):
    """Unbedenklicher Dateiname aus der Belegnummer (Header-Injection-Schutz)."""
    invoice = Invoice.objects.filter(id=invoice_id).only("invoice_number").first()
    raw = (invoice.invoice_number if invoice else None) or str(invoice_id)
    return "".join(ch for ch in raw if ch.isalnum() or ch in "-_") or "beleg"


@router.get("/invoices/{invoice_id}/zugferd.pdf")
def invoice_zugferd_pdf(request, invoice_id: UUID):
    """E-Rechnung (ZUGFeRD/Factur-X): Hybrid-PDF mit eingebettetem CII-XML.

    Eigene Ausfertigung neben dem Beleg-PDF (link_category 'E_RECHNUNG',
    Migration 0059): PDF/A-3B mit dem maschinenlesbaren EN16931-XML im Anhang.
    Beim ersten Abruf wird sie archiviert, danach unverändert ausgeliefert.

    Nur veröffentlichte Rechnungen (sonst 404 — ein Entwurf ist keine Rechnung).
    Lässt die Datenlage kein gültiges EN16931-XML zu (kein Firmenprofil, kein
    Empfänger, inkonsistente Steueraufteilung), antwortet der Endpunkt mit 422 und
    nennt den Grund — statt eine E-Rechnung auszuliefern, die der Empfänger
    zurückweist."""
    actor, _ = require(request, "invoicing", "LESEN")
    try:
        pdf = erechnung_service.get_or_archive_zugferd_pdf(actor, invoice_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    if pdf is None:
        raise HttpError(404, "Veröffentlichte Rechnung nicht gefunden.")
    response = HttpResponse(pdf, content_type="application/pdf")
    name = f"{_beleg_dateiname(invoice_id)}-zugferd.pdf"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@router.get("/invoices/{invoice_id}/zugferd.xml")
def invoice_zugferd_xml(request, invoice_id: UUID):
    """Das reine CII-XML der E-Rechnung (Prüf-/Debug-Ansicht).

    Dasselbe XML, das im Hybrid-PDF steckt — hier einzeln, damit es sich gegen
    einen externen Validator (z. B. Mustang/KoSiT) halten lässt. Bewusst NICHT
    archiviert: die aufbewahrungspflichtige Ausfertigung ist das Hybrid-PDF."""
    require(request, "invoicing", "LESEN")
    try:
        xml = erechnung_service.build_cii_xml_for(invoice_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    if xml is None:
        raise HttpError(404, "Veröffentlichte Rechnung nicht gefunden.")
    response = HttpResponse(xml, content_type="application/xml; charset=utf-8")
    name = f"{_beleg_dateiname(invoice_id)}-zugferd.xml"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


class InvoiceEmailIn(Schema):
    # Optional: überschreibt die aus der Empfängerpartei ermittelte Adresse (der
    # Nutzer darf sie im Dialog bestätigen/korrigieren). Weglassen = ermitteln.
    to_address: str | None = None


class InvoiceEmailOut(Schema):
    sent: bool
    to_address: str


@router.post(
    "/invoices/{invoice_id}/send-email", response=InvoiceEmailOut, auth=django_auth
)
def send_invoice_email(request, invoice_id: UUID, payload: InvoiceEmailIn):
    """Versendet eine veröffentlichte Rechnung als PDF-Anhang per E-Mail.

    Recht VERSENDEN: der Belegversand ist eine nach außen wirkende
    Kundenkommunikation — dasselbe Recht wie Angebotsversand und Mahnung. Reine
    Zustellung: kein Statuswechsel, keine GoBD-Berührung (der Beleg ist bereits
    festgeschrieben). Der Versand protokolliert eine content.communication-Zeile.

    Fehler → 422 (passwortfrei): Entwurf/unbekannt, kein Empfänger/keine E-Mail,
    kein Mailkonto, Schlüssel- oder SMTP-Fehler.
    """
    actor, _ = require(request, "invoicing", "VERSENDEN")
    try:
        communication = beleg_versand_service.send_invoice_email(
            actor, invoice_id=invoice_id, to_address=payload.to_address
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    except (MailKeyError, MailSendError) as exc:
        # Passwortfreie, klare Meldung an das UI statt eines 500-Leaks.
        raise HttpError(422, str(exc))
    return InvoiceEmailOut(sent=True, to_address=communication.counterpart_raw)
