"""Beleg-API — Angebote (invoicing.quote) und Rechnungen (invoicing.invoice).

## Die Rechte-Landkarte dieses Routers (seit Migration 0102 tragend)

Bis 0102 hatte die Rolle MONTEUR auf `invoicing` **kein** Recht; jeder Endpunkt
hier antwortete ihr mit 403, ohne dass eine Zeile Code das tun musste. Seit 0102
trägt sie `invoicing/LESEN` mit **row_scope EIGENE** — „er darf das Angebot seines
Objekts sehen, aber ohne Preise". Damit hängt die Sperre nicht mehr an der
Abwesenheit des Rechts, sondern an **diesen Torfunktionen**:

| Endpunkt | Tor | EIGENE bekommt |
|---|---|---|
| alles zur **RECHNUNG** (Liste, Detail, PDF, ZUGFeRD, Anrechnung) | `require` | **403** — fail-closed, ohne eine einzige Filterzeile |
| **Angebot** mit Preisen (Liste, Detail, PDF, Kalkulation) | `require` | **403** |
| **Angebot ohne Preise** (`/quotes/mengen`, `/quotes/{id}/mengen`) | `require_scoped` | die versendeten/angenommenen Angebote **seiner** Objekte, **preisfrei** |
| jeder Schreibpfad (anlegen, ändern, versenden, stornieren, fakturieren) | `require` | **403** |

`require` wirft bei Scope EIGENE **immer** 403 (`api/permissions.py`) — deshalb ist
jeder Endpunkt dieser Datei, der nicht ausdrücklich `require_scoped` benutzt,
automatisch dicht. **Wer hier einen Lesepfad auf `require_scoped` umstellt, öffnet
ihn für den Monteur** und muss dann selbst zeilen- UND feldbegrenzen.

## Die Falle: Geldfelder stehen nicht nur da, wo man sie vermutet

`QuoteLineOut` führt neben `unit_price`/`net_amount` auch **`unit_cost` (den
Einkaufspreis)** und **`markup_percent` (den Aufschlag)** — den internen
Kalkulations-Snapshot, der nicht einmal auf dem Kundenbeleg steht. Wer eine
preisfreie Sicht als „QuoteLineOut minus unit_price" baut, gibt dem Monteur die
**Marge**. Deshalb ist die Mengensicht **additiv**: `QuoteLineMengenOut` führt nur
die ausdrücklich erlaubten Felder. Was nicht drinsteht, kann nicht durchrutschen.
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

from api.permissions import check, require, require_scoped
from db_core.mail_crypto import MailKeyError
from db_core.models import BillingLink, Invoice, Quote
from db_core.services import abrechnung as abrechnung_service
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf as beleg_pdf_service
from db_core.services import beleg_versand as beleg_versand_service
from db_core.services import erechnung as erechnung_service
from db_core.services import objektsicht
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
    # Auftragsbezug (= Soll dieser Baustelle). Steht schon in der Liste, damit die
    # Auftrags-Mappe die zuordenbaren von den bereits zugeordneten Angeboten
    # unterscheiden kann, ohne jedes einzeln nachzuladen.
    work_order_id: UUID | None = None
    # Vorgangsbezug (Migration 0113): der Vorgang, an dem der Beleg hängt.
    service_case_id: UUID | None = None


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
    # Herkunft der **Abrechnungsbindung** (Migration 0084, vierte Quelle seit 0139):
    # BERICHTSPOSITION | ZEITBUCHUNG | ANGEBOTSPOSITION | MATERIALBUCHUNG —
    # oder null (frei erfasst).
    #
    # Nur bei Rechnungen gesetzt. Das UI braucht es, um genau **diese** Zeilen als
    # unveränderlich zu kennzeichnen (der Trigger sperrt UPDATE/DELETE je Zeile,
    # nicht den Beleg) — statt den Nutzer in einen Serverfehler laufen zu lassen.
    billing_source: str | None = None


class RubrikOut(Schema):
    position_number: int
    title: str
    description: str | None = None


class ProjectRefOut(Schema):
    id: UUID
    project_number: str
    name: str


class WorkOrderRefOut(Schema):
    id: UUID
    order_number: str
    title: str


class BezugZeileOut(Schema):
    """Eine beschriftete Zeile des Objektbezugs („Eigentümer: Klaus Meier")."""

    label: str
    wert: str


class DokumentkopfOut(Schema):
    """Briefkopf für die Bildschirmdarstellung (Befund G1).

    `aussteller` und `empfaenger` sind fertige Zeilenlisten — die Zusammensetzung
    einer Anschrift (Zusatz vor Straße, PLZ und Ort in einer Zeile, Land nur bei
    Auslandsbelegen) gehört an EINE Stelle und nicht ins Frontend. Es ist
    dieselbe Funktion, aus der das PDF sein Anschriftfeld baut.

    `bezug` folgt derselben Regel: Wohneinheit, Eigentümer, Mieter und
    „Vertreten durch" kommen fertig beschriftet aus `services/belegbezug.py` —
    dieselbe Quelle, aus der die PDFs ihren Block bauen. Leer, wenn nichts
    ableitbar war; dann zeigt die Ansicht keinen Block.
    """

    aussteller: list[str] = []
    empfaenger: list[str] = []
    bezug: list[BezugZeileOut] = []
    # Stammt der Kopf aus dem eingefrorenen Beleg (veröffentlichte Rechnung) oder
    # aus den Live-Daten (Angebot, Entwurf)? Die Ansicht kann das kennzeichnen.
    aus_snapshot: bool = False


class QuoteDetailOut(QuoteOut):
    valid_until_date: date | None = None
    tax_total: Decimal | None = None
    version: int
    project: ProjectRefOut | None = None
    # Auftragsbezug (= Soll dieser Baustelle, siehe QuoteIn). null = keinem Auftrag
    # zugeordnet: das Angebot fließt dann in keinen Soll-Ist-Abgleich ein.
    work_order: WorkOrderRefOut | None = None
    sent_at: datetime | None = None
    has_snapshot: bool = False
    content_hash: str | None = None
    # Vorbelegung für den E-Mail-Versand: primäre EMAIL der best-effort über den
    # Auftrag abgeleiteten Empfängerpartei (INVOICE_RECIPIENT, sonst PRINCIPAL).
    # Nur bei versendeten Angeboten aufgelöst; null, wenn kein Auftrag/kein
    # Kommunikationsweg hinterlegt ist (dann trägt der Nutzer sie manuell ein).
    recipient_email: str | None = None
    # Anschreiben-Freitext im Belegkopf (Dokumente-9). Beleginhalt: ab VERSENDET
    # eingefroren (B-30). null/leer = kein Anschreiben.
    cover_letter: str | None = None
    rubriken: list[RubrikOut] = []
    # Briefkopf für die Dokumentansicht (G1). null, wenn er sich nicht bilden
    # lässt — die Ansicht fällt dann auf ihre schlichte Darstellung zurück.
    dokumentkopf: DokumentkopfOut | None = None
    lines: list[QuoteLineOut]


# --- Die Mengensicht: das Angebot OHNE Geld (Migration 0102) ---------------
#
# **Additiv gebaut, nicht subtraktiv.** Diese Schemata erben NICHT von QuoteOut /
# QuoteLineOut und listen kein Geldfeld auf — sie führen ausschließlich, was der
# Monteur sehen darf. Eine Vererbung „QuoteLineOut ohne unit_price" wäre eine
# Feldliste, die man beim nächsten neuen Betragsfeld (und es kamen bisher fünf
# dazu: discount_percent, labour_net_amount, unit_cost, markup_percent,
# tax_rate_percent) stillschweigend vergisst.

class QuoteLineMengenOut(Schema):
    """Eine Angebotsposition, wie der Monteur sie braucht: **was und wie viel**.

    Kein `unit_price`, kein `net_amount`, kein `discount_percent`, kein
    `tax_rate_percent`, kein `labour_net_amount` — und vor allem **kein `unit_cost`
    und kein `markup_percent`** (Einkaufspreis und Aufschlag; sie stehen nicht
    einmal auf dem Kundenbeleg).

    `line_kind` bleibt drin und ist wichtig: Eine ALTERNATIV- oder BEDARFS-Position
    ist **nicht beauftragt**. Sie wegzulassen wäre gefährlicher, als sie zu zeigen —
    der Monteur baute sie sonst als Teil des Auftrags ein.
    """
    position_number: int
    line_type: str
    line_kind: str = "NORMAL"
    rubrik: int | None = None
    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    # Artikel-/Leistungsbezug: die Brücke in den Artikelstamm („welches Rohr genau?").
    # Der Artikelstamm selbst bleibt hinter `pricing/LESEN` — die ID verrät keinen Preis.
    source_article_id: UUID | None = None
    source_assembly_id: UUID | None = None


class QuoteMengenOut(Schema):
    """Angebotskopf ohne Summen (`net_total`/`tax_total`/`gross_total` fehlen)."""
    id: UUID
    quote_number: str | None = None
    title: str
    status: str
    quote_date: date | None = None
    valid_until_date: date | None = None
    property: PropertyRefOut
    work_order_id: UUID | None = None
    # Sagt dem UI ins Gesicht, was fehlt — statt Spalten stillschweigend wegzulassen.
    # Für Scope ALLE false: dort ist die Mengensicht eine Arbeitsansicht (Kommission,
    # Materialliste), keine Beschneidung.
    preise_ausgeblendet: bool = False


class QuoteMengenListOut(Schema):
    items: list[QuoteMengenOut]
    total: int
    page: int
    page_size: int


class QuoteMengenDetailOut(QuoteMengenOut):
    project: ProjectRefOut | None = None
    work_order: WorkOrderRefOut | None = None
    sent_at: datetime | None = None
    rubriken: list[RubrikOut] = []
    lines: list[QuoteLineMengenOut]


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
    # Auftragsbezug: die Aussage „dieses Angebot ist das Soll dieser Baustelle".
    # Der Soll-Ist-Abgleich am Baustellenbericht (0080) stützt sich ausschließlich
    # darauf. Der Auftrag muss zur selben Liegenschaft (und, falls gesetzt, zum
    # selben Projekt) gehören — sonst 422.
    work_order_id: UUID | None = None
    # Vorgangsbezug (Migration 0113): verankert das Angebot am Vorgang. Fehlt er,
    # aber der Auftrag hängt an einem Vorgang, wird dieser geerbt. Muss zur selben
    # Liegenschaft (und, falls der Vorgang ein Projekt trägt, zu diesem) gehören.
    service_case_id: UUID | None = None
    quote_date: date | None = None
    valid_until_date: date | None = None
    # Anschreiben-Freitext im Belegkopf (Dokumente-9), optional.
    cover_letter: str | None = None
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


# --- Live-Vorschau des Editors ---------------------------------------------

class VorschauZeileOut(Schema):
    """Die serverberechneten Rechenwerte einer Position, in Payload-Reihenfolge.
    Textzeilen tragen keinen Betrag → durchweg null."""
    net_amount: Decimal | None = None
    markup_percent: Decimal | None = None
    tax_rate_percent: Decimal | None = None
    labour_net_amount: Decimal | None = None


class VorschauOut(Schema):
    """Live-Vorschau: dieselbe Rechnung wie das PUT, aber ohne zu speichern.

    `kalkulation` ist null, wenn der Nutzer kein `pricing/LESEN` hat — KEIN 403 für
    den Gesamtendpunkt, die (preisfreien) Summen sieht er trotzdem.
    """
    lines: list[VorschauZeileOut]
    net_total: Decimal
    tax_total: Decimal
    gross_total: Decimal
    kalkulation: KalkulationOut | None = None


class QuoteFilter(Schema):
    q: str | None = None
    status: str | None = None
    property_id: UUID | None = None
    project_id: UUID | None = None
    # Vorgangsbezug (Migration 0113): der Beleg hängt DIREKT am Vorgang ODER an einem
    # Auftrag dieses Vorgangs. So findet die Vorgangsmappe beide Wege.
    service_case_id: UUID | None = None
    work_order_id: UUID | None = None


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
        work_order_id=quote.work_order_id,
        service_case_id=quote.service_case_id,
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
    if filters.service_case_id:
        # DIREKT am Vorgang ODER an einem Auftrag dieses Vorgangs (Migration 0113).
        qs = qs.filter(
            Q(service_case_id=filters.service_case_id)
            | Q(work_order__service_case_id=filters.service_case_id)
        )
    if filters.work_order_id:
        qs = qs.filter(work_order_id=filters.work_order_id)

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
    work_order = (
        WorkOrderRefOut(
            id=quote.work_order.id,
            order_number=quote.work_order.order_number,
            title=quote.work_order.title,
        )
        if quote.work_order_id
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
        work_order_id=quote.work_order_id,
        service_case_id=quote.service_case_id,
        work_order=work_order,
        sent_at=quote.sent_at,
        has_snapshot=quote.billing_snapshot is not None,
        content_hash=quote.content_hash,
        recipient_email=recipient_email,
        cover_letter=quote.cover_letter,
        rubriken=_rubriken_out(quote),
        dokumentkopf=beleg_service.dokumentkopf(quote),
        lines=lines,
    )


# --- Die Mengensicht: Endpunkte (Migration 0102) ---------------------------
#
# Die EINZIGEN beiden Lesepfade dieses Routers mit `require_scoped`. Sie sind der
# Grund, aus dem alle anderen bei `require` bleiben dürfen.
#
# ⚠️ REIHENFOLGE: `/quotes/mengen` MUSS vor der ERSTEN Operation auf
# `/quotes/{quote_id}` stehen (das ist `PUT`, weiter unten). django-ninja registriert
# eine URL an der Stelle ihrer **ersten** Operation und bindet den Pfadparameter als
# **String** — steht `/quotes/{quote_id}` zuerst, schluckt es „mengen" und pydantic
# antwortet mit 422 (nicht 404, nicht 403). Genau das ist hier einmal passiert;
# `api/tests/test_endpoint_schutz.py` hat es gefangen.

def _mengen_kopf(quote, *, preise_ausgeblendet):
    return {
        "id": quote.id,
        "quote_number": quote.quote_number,
        "title": quote.title,
        "status": quote.status,
        "quote_date": quote.quote_date,
        "valid_until_date": quote.valid_until_date,
        "property": _property_ref(quote),
        "work_order_id": quote.work_order_id,
        "preise_ausgeblendet": preise_ausgeblendet,
    }


@router.get("/quotes/mengen", response=QuoteMengenListOut)
def list_quotes_mengen(
    request,
    filters: QuoteFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Angebote **ohne Preise** — die Liste, die der Monteur sehen darf.

    Scope EIGENE: nur Angebote an **meinen** Objekten und nur im Status
    VERSENDET/ANGENOMMEN (`objektsicht.angebote_begrenzen` — die eine Heimat der
    Regel; die Begründung für den Statusfilter steht dort). Scope ALLE: dieselbe
    Liste über alle Angebote — die Mengensicht ist dort eine Arbeitsansicht
    (Kommissionierung), keine Beschneidung, und `preise_ausgeblendet` ist false.
    """
    actor, scope = require_scoped(request, "invoicing", "LESEN")
    qs = objektsicht.angebote_begrenzen(
        Quote.objects.select_related("property__address"), scope, actor
    )

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
    items = [
        QuoteMengenOut(**_mengen_kopf(q, preise_ausgeblendet=scope == "EIGENE"))
        for q in qs[start:start + page_size]
    ]
    return QuoteMengenListOut(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/quotes/{quote_id}/mengen", response=QuoteMengenDetailOut)
def get_quote_mengen(request, quote_id: UUID):
    """Ein Angebot **ohne Preise**: Positionen mit Menge und Einheit.

    Fachlich der Kern dieses Slices: Der Monteur muss wissen, **was beauftragt ist**
    (12 m Kupferrohr DN20, sechs Thermostatventile) — sonst baut er das Falsche ein
    oder übersieht eine Position. Was es kostet, geht ihn nichts an.

    Scope EIGENE: fremdes Objekt oder ein Angebot im Entwurf → **404** (keine
    Existenzaussage, Hausregel). Ein **PDF** gibt es dafür bewusst nicht: Das
    Angebots-PDF trägt Preise, und `GET /quotes/{id}/pdf` bleibt deshalb bei
    `require` (403).
    """
    actor, scope = require_scoped(request, "invoicing", "LESEN")
    if scope == "EIGENE" and not objektsicht.ist_eigenes_angebot(actor, quote_id):
        raise HttpError(404, "Angebot nicht gefunden.")

    quote = (
        Quote.objects.filter(id=quote_id)
        .select_related("property__address", "project", "work_order")
        .prefetch_related("lines", "rubriken")
        .first()
    )
    if quote is None:
        raise HttpError(404, "Angebot nicht gefunden.")

    nummern = _rubrik_nummern(quote)
    lines = [
        QuoteLineMengenOut(
            position_number=l.position_number,
            line_type=l.line_type,
            line_kind=l.line_kind,
            rubrik=nummern.get(l.rubrik_id),
            description=l.description,
            quantity=l.quantity,
            unit=l.unit,
            source_article_id=l.source_article_id,
            source_assembly_id=l.source_assembly_id,
        )
        for l in sorted(quote.lines.all(), key=lambda l: l.position_number)
    ]
    return QuoteMengenDetailOut(
        **_mengen_kopf(quote, preise_ausgeblendet=scope == "EIGENE"),
        project=(
            ProjectRefOut(
                id=quote.project.id,
                project_number=quote.project.project_number,
                name=quote.project.name,
            )
            if quote.project_id
            else None
        ),
        work_order=(
            WorkOrderRefOut(
                id=quote.work_order.id,
                order_number=quote.work_order.order_number,
                title=quote.work_order.title,
            )
            if quote.work_order_id
            else None
        ),
        sent_at=quote.sent_at,
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
            work_order_id=payload.work_order_id,
            service_case_id=payload.service_case_id,
            quote_date=payload.quote_date,
            valid_until_date=payload.valid_until_date,
            cover_letter=payload.cover_letter,
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
    # Auftragsbezug setzen (oder mit `null` lösen). Weggelassen = unverändert.
    # In JEDEM Status möglich (0082): der Auftrag entsteht regelmäßig erst NACH der
    # Annahme des Angebots. Interner Verweis, kein Beleginhalt — B-30 gilt weiter
    # für alles andere.
    work_order_id: UUID | None = None
    # Projektzuordnung setzen (oder mit `null` lösen). Weggelassen = unverändert.
    # Nur im Entwurf möglich (Verschieben) — ab VERSENDET friert die DB alles ein.
    project_id: UUID | None = None
    # Anschreiben-Freitext (Dokumente-9). Beleginhalt: nur im editierbaren Status
    # (ENTWURF/INTERN_GEPRUEFT/FREIGEGEBEN) änderbar, ab VERSENDET eingefroren.
    # Weggelassen = unverändert, `null`/leer = löschen.
    cover_letter: str | None = None
    rubriken: list[RubrikIn] | None = None
    lines: list[QuoteLineIn] | None = None


@router.put("/quotes/{quote_id}", response=QuoteDetailOut, auth=django_auth)
def update_quote(request, quote_id: UUID, payload: QuoteUpdateIn):
    """Angebot ändern.

    Ab VERSENDET friert die DB den **Beleginhalt** ein (422): Titel, Daten,
    Positionen, Abschnitte. Die **Auftragszuordnung** (`work_order_id`) bleibt
    dagegen in jedem Status setz- und lösbar (Migration 0082). Die
    **Projektzuordnung** (`project_id`, „Verschieben") ist nur im Entwurf änderbar
    und wird serverseitig gegen einen hängenden Auftrag geprüft (422 bei Konflikt).
    """
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
            work_order_id=(
                payload.work_order_id if "work_order_id" in gesetzt else ...
            ),
            project_id=payload.project_id if "project_id" in gesetzt else ...,
            cover_letter=payload.cover_letter if "cover_letter" in gesetzt else ...,
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


@router.post("/quotes/{quote_id}/vorschau", response=VorschauOut, auth=django_auth)
def quote_vorschau(request, quote_id: UUID, payload: QuoteUpdateIn):
    """Live-Vorschau eines Angebots: rechnet den Editor-Payload (wie `PUT /quotes/{id}`)
    durch, ohne zu speichern — Positionsnetto, Kopfsummen und Kalkulationsleiste
    sofort aktuell.

    Leseartig, ändert nichts → `invoicing/LESEN` genügt (wie `GET /quotes/{id}`,
    `require`: Scope EIGENE bekommt 403). Die Kalkulation (EK/Marge) zusätzlich nur
    mit `pricing/LESEN` — sonst `kalkulation: null`, ohne den Endpunkt zu sperren
    (Muster von `GET /quotes/{id}/kalkulation`). Der Beleg muss existieren (404),
    darf aber in JEDEM Status sein. Payload-Fehler → 422 (wie beim PUT).
    """
    require(request, "invoicing", "LESEN")
    mit_kalkulation = check(request, "pricing", "LESEN") is not None
    try:
        return beleg_service.vorschau_quote(
            quote_id,
            lines=[line.dict() for line in payload.lines or []],
            rubriken=[r.dict() for r in payload.rubriken or []],
            mit_kalkulation=mit_kalkulation,
        )
    except beleg_service.BelegNichtGefunden as exc:
        raise HttpError(404, str(exc))
    except ValueError as exc:
        raise HttpError(422, str(exc))


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


class QuoteStatusIn(Schema):
    """Der Ausgang eines versendeten Angebots.

    Nur die Kanten, die der DB-Statusautomat kennt (Migration 0016) UND die ohne
    Zusatzobjekt auskommen: ANGENOMMEN | ABGELEHNT | ABGELAUFEN. **ERSETZT steht
    hier bewusst nicht** — es verlangt ein Nachfolgeangebot (DB-CHECK, 0018) und ist
    damit der Vorgang „Ersatzangebot anlegen", kein Statuswechsel.
    """
    to_status: str
    reason: str | None = None


@router.post("/quotes/{quote_id}/status", response=QuoteDetailOut, auth=django_auth)
def set_quote_status(request, quote_id: UUID, payload: QuoteStatusIn):
    """Angebot annehmen / ablehnen / als abgelaufen festhalten.

    Der Statusautomat erlaubt VERSENDET → ANGENOMMEN seit Migration 0016 — **gesetzt
    hat den Status nie ein Produktpfad**. Ein Angebot blieb für immer „versendet",
    auch wenn der Kunde längst zugesagt hatte.

    **B-30 bleibt unangetastet**: Snapshot und Inhalts-Hash des versendeten Angebots
    ändern sich nicht; geschrieben wird ausschließlich die Statusspalte.

    Recht `invoicing/AENDERN`: Der Ausgang eines Angebots ist eine kaufmännische
    Feststellung, kein Versand und kein Storno.
    """
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        beleg_service.set_quote_status(
            actor, quote_id=quote_id, to_status=payload.to_status,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _quote_detail(quote_id)


class QuoteCopyIn(Schema):
    """Ziel der Kopie. Weggelassene Felder erben Liegenschaft/Projekt der Quelle;
    `project_id: null` erzeugt eine projektlose Kopie."""
    property_id: UUID | None = None
    project_id: UUID | None = None


@router.post(
    "/quotes/{quote_id}/kopie", response={201: QuoteDetailOut}, auth=django_auth
)
def copy_quote(request, quote_id: UUID, payload: QuoteCopyIn):
    """Dupliziert ein Angebot als neuen Entwurf (Kopf „… (Kopie)", Abschnitte,
    Positionen wertgleich). Ziel-Liegenschaft/-Projekt wählbar (Default: wie Quelle).

    Recht ANLEGEN — es entsteht ein neuer Beleg (Scope EIGENE → 403, fail-closed).
    GoBD: das Ergebnis ist ein frischer ENTWURF ohne Snapshot; der Auftragsbezug
    wird bewusst nicht mitkopiert. Aus jedem Status kopierbar (die Quelle wird nur
    gelesen).
    """
    actor, _ = require(request, "invoicing", "ANLEGEN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        quote = beleg_service.kopiere_angebot(
            actor,
            quote_id=quote_id,
            property_id=payload.property_id if "property_id" in gesetzt else ...,
            project_id=payload.project_id if "project_id" in gesetzt else ...,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _quote_detail(quote.id))


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


@router.get("/quotes/{quote_id}/pdf/vorschau")
def quote_pdf_vorschau(request, quote_id: UUID):
    """Vorschau-PDF eines Angebots in JEDEM Status (on-the-fly, unarchiviert).

    Noch nicht versendete Angebote tragen einen deutlichen ENTWURF-Aufdruck.
    Es wird bewusst NICHTS archiviert — die verbindliche Ausfertigung entsteht
    weiterhin ausschließlich über GET /quotes/{id}/pdf ab VERSENDET (GoBD:
    eine Ausfertigung je Beleg)."""
    require(request, "invoicing", "LESEN")
    pdf = beleg_pdf_service.render_quote_preview(quote_id)
    if pdf is None:
        raise HttpError(404, "Angebot nicht gefunden.")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="vorschau.pdf"'
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
    """Detail eines Angebots inkl. Positionen **und Preisen**.

    `require` (nicht `require_scoped`): Scope EIGENE → 403. Der Monteur liest sein
    Angebot über `/quotes/{id}/mengen` — hier steht die Kalkulation (`unit_cost`,
    `markup_percent`), und die geht ihn nichts an.
    """
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
    # Vorgangsbezug (Migration 0113): der Vorgang, an dem der Beleg hängt.
    service_case_id: UUID | None = None


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
    work_order_id: UUID | None = None
    work_order_number: str | None = None
    # Trägt die Rechnung **aktive** Abrechnungsbindungen (Migration 0084)?
    #
    # Gesperrt sind damit die **gebundenen Zeilen** — nicht der Beleg (Migration
    # 0088 hat den Trigger genau darauf verengt): `protect_billed_invoice_lines`
    # weist UPDATE und DELETE einer gebundenen Zeile ab; das **INSERT einer neuen**
    # Zeile ist erlaubt (sie kann keine Bindung tragen).
    #
    # Fürs UI folgt daraus zweierlei:
    #  * Der **Editor** (`PUT /invoices/{id}`) ist trotzdem verschlossen: Er
    #    ersetzt den ganzen Positionssatz per Delete+Insert und trifft dabei die
    #    gebundene Zeile (422). Ihn anzubieten hieße, in eine Sackgasse zu führen.
    #  * **Ergänzen geht trotzdem** — über `POST /invoices/{id}/lines` (eine Zeile
    #    ans Ende). Die Notbremse `bindungen-loesen` (die alle gebundenen
    #    Positionen verwirft) bleibt dem verunglückten Lauf vorbehalten.
    gebunden: bool = False
    published_at: datetime | None = None
    has_snapshot: bool = False
    content_hash: str | None = None
    # Vorbelegung für den E-Mail-Versand: primäre EMAIL der Empfängerpartei
    # (INVOICE_RECIPIENT, sonst INVOICE_DEBTOR). Nur bei veröffentlichten Rechnungen
    # aufgelöst; null, wenn kein Kommunikationsweg hinterlegt ist.
    recipient_email: str | None = None
    parties: list[InvoicePartyOut] = []
    rubriken: list[RubrikOut] = []
    # Briefkopf für die Dokumentansicht (G1). Bei einer veröffentlichten
    # Rechnung aus dem eingefrorenen Snapshot — dort kostet er keine einzige
    # zusätzliche Abfrage. Beim Entwurf wird nur die EINE Empfängerpartei
    # aufgelöst, nicht der ganze Beteiligtensatz: Diese Antwort bauen dreizehn
    # Endpunkte, auch jedes POST.
    dokumentkopf: DokumentkopfOut | None = None
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
    # Vorgangsbezug (Migration 0113): siehe QuoteIn.service_case_id.
    service_case_id: UUID | None = None
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
    # Vorgangsbezug (Migration 0113): DIREKT am Vorgang ODER an einem Auftrag dieses
    # Vorgangs.
    service_case_id: UUID | None = None
    work_order_id: UUID | None = None


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
        service_case_id=invoice.service_case_id,
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
    if filters.service_case_id:
        # DIREKT am Vorgang ODER an einem Auftrag dieses Vorgangs (Migration 0113).
        qs = qs.filter(
            Q(service_case_id=filters.service_case_id)
            | Q(work_order__service_case_id=filters.service_case_id)
        )
    if filters.work_order_id:
        qs = qs.filter(work_order_id=filters.work_order_id)
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
    # Aktive Abrechnungsbindungen dieser Rechnung: EINE Query, kein N+1. Nur
    # `released_at IS NULL` zählt — eine gelöste Bindung sperrt nichts mehr.
    bindung_je_zeile = dict(
        BillingLink.objects.filter(
            invoice_id=invoice.id, released_at__isnull=True
        ).values_list("invoice_line_id", "source_kind")
    )
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
            billing_source=bindung_je_zeile.get(l.id),
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
        service_case_id=invoice.service_case_id,
        work_order_id=invoice.work_order_id,
        work_order_number=(
            invoice.work_order.order_number if invoice.work_order_id else None
        ),
        gebunden=bool(bindung_je_zeile),
        published_at=invoice.published_at,
        has_snapshot=invoice.billing_snapshot is not None,
        content_hash=invoice.content_hash,
        recipient_email=recipient_email,
        parties=parties,
        rubriken=_rubriken_out(invoice),
        dokumentkopf=beleg_service.dokumentkopf(invoice),
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
            service_case_id=payload.service_case_id,
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


# ---------------------------------------------------------------------------
# Abrechnung: aus Angebot bzw. aus Auftrag (Migration 0084)
# ---------------------------------------------------------------------------

class PreisVorschlagOut(Schema):
    """Ein **Vorschlag**, nie ein automatisch gesetzter Preis."""
    art: str                      # LETZTER_PREIS | LISTENPREIS | LOHNGRUPPE
    betrag: Decimal
    quelle: str


class PreisKlaerungOut(Schema):
    """Eine Position, für die der Server **keinen** Preis hat.

    Strukturiert, damit das UI daraus eine Klärungsmaske bauen kann: Der Nutzer
    nennt den Einzelpreis und schickt denselben Aufruf mit `preise` erneut. Eine
    0-€-Position gibt es nicht, und weggelassen wird auch nichts.
    """
    quelle_art: str               # BERICHTSPOSITION | ZEITGRUPPE | MATERIALBUCHUNG
    quelle_id: UUID
    bezeichnung: str
    menge: Decimal | None = None
    einheit: str | None = None
    # EK_FEHLT | KEINE_VK_REGEL | KEINE_HERKUNFT | LEISTUNG_UNVOLLSTAENDIG |
    # LOHNGRUPPE_FEHLT | VK_NULL | LOHNSATZ_NULL | MATERIAL_OHNE_ARTIKEL
    #
    # MATERIAL_OHNE_ARTIKEL: Die Materialbuchung am Einsatz nennt keinen Artikel,
    # sondern nur freien Text — daraus lässt sich kein VK ableiten. Der Ausweg ist
    # ein anderer als bei EK_FEHLT (Artikel am Einsatz zuordnen bzw. Preis nennen),
    # deshalb ein eigener Grund.
    #
    # VK_NULL / LOHNSATZ_NULL sind die **stille Null**: Der Server hat eine Zahl,
    # aber sie ist 0,00 € (0-EK aus dem Import, Festpreis 0,00, Lohnsatz 0,00 €/h
    # — die CHECKs erlauben überall `>= 0`). Das ist kein Preis, sondern eine
    # Lücke. Eigener Grund, damit das UI den Nutzer in den **Stamm** schickt und
    # nicht auf die Suche nach einem fehlenden Einkaufspreis.
    grund: str
    grund_text: str
    vorschlaege: list[PreisVorschlagOut] = []


class PreisKlaerungFehlerOut(Schema):
    detail: str
    preis_unbekannt: list[PreisKlaerungOut]


class EinheitKonfliktOut(Schema):
    """Ein Posten, dessen Mengen in verschiedenen Einheiten vorliegen.

    Fail-closed: Derselbe Artikel/dieselbe Leistung ist schon unter einer anderen
    Einheit in Rechnung (z. B. Nachtrag „Stk", Angebot „Stück"). Nicht summierbar,
    nicht durchlassbar — ein Mensch vereinheitlicht oder entscheidet.
    """
    identitaet: str
    bezeichnung: str
    einheiten: list[str]


class EinheitUneindeutigFehlerOut(Schema):
    detail: str
    einheit_uneindeutig: list[EinheitKonfliktOut]


class RechnungAusAngebotIn(Schema):
    quote_id: UUID
    invoice_date: date | None = None
    due_date: date | None = None
    payment_term_days: int | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None
    show_labour_costs: bool = True


class RechnungAusAuftragIn(Schema):
    work_order_id: UUID
    # Pflicht und bewusst ohne Default: Welcher Steuersatz gilt, ist eine
    # steuerliche Entscheidung des Belegs — kein Ratespiel des Servers.
    tax_code: str
    # Die Klärung des Menschen: {quelle_id → Einzelpreis} für Positionen, deren
    # Preis der Server NICHT kennt. Für alle anderen wird er abgelehnt (422) —
    # sonst ließe sich die eine Rechenstelle stillschweigend unterlaufen.
    preise: dict[UUID, Decimal] = {}
    mit_berichten: bool = True
    mit_zeiten: bool = True
    # Am Einsatz gebuchtes Material (Migration 0139). Der Schalter ist der Ausweg
    # aus der Doppelerfassung: Steht dieselbe Sache im Bericht UND als
    # Materialbuchung, weist der Server den Lauf ab — dann entscheidet ein Mensch,
    # welche der beiden Quellen die Wahrheit ist.
    mit_material: bool = True
    invoice_date: date | None = None
    due_date: date | None = None
    payment_term_days: int | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None
    show_labour_costs: bool = True


@router.post(
    "/invoices/aus-angebot",
    response={201: InvoiceDetailOut, 422: EinheitUneindeutigFehlerOut},
    auth=django_auth,
)
def rechnung_aus_angebot(request, payload: RechnungAusAngebotIn):
    """Rechnung (ENTWURF) aus einem Angebot — die **Angebotskopie**.

    Positionen werden wertgleich kopiert (der Kunde hat *diesen* Preis
    akzeptiert, nicht den heutigen Listenpreis); ALTERNATIV/BEDARF bleiben außen
    vor. Jede übernommene Betragsposition bekommt eine **Abrechnungsbindung** —
    ein zweiter Lauf über dasselbe Angebot scheitert (422).

    Ist derselbe Posten bereits über einen **Nachtrag** unter einer anderen Einheit
    fakturiert (z. B. „Stk"/„Stück"), antwortet der Endpunkt fail-closed mit **422
    und `einheit_uneindeutig`** — die Angebotskopie käme sonst doppelt obendrauf.
    """
    actor, _ = require(request, "invoicing", "ANLEGEN")
    try:
        invoice = abrechnung_service.rechnung_aus_angebot(
            actor,
            quote_id=payload.quote_id,
            invoice_date=payload.invoice_date,
            due_date=payload.due_date,
            payment_term_days=payload.payment_term_days,
            discount_percent=payload.discount_percent,
            discount_days=payload.discount_days,
            show_labour_costs=payload.show_labour_costs,
        )
    except abrechnung_service.EinheitUneindeutig as exc:
        return Status(
            422,
            EinheitUneindeutigFehlerOut(
                detail=str(exc),
                einheit_uneindeutig=[EinheitKonfliktOut(**k) for k in exc.konflikte],
            ),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _invoice_detail(invoice.id))


@router.post(
    "/invoices/aus-auftrag",
    response={
        201: InvoiceDetailOut,
        422: PreisKlaerungFehlerOut | EinheitUneindeutigFehlerOut,
    },
    auth=django_auth,
)
def rechnung_aus_auftrag(request, payload: RechnungAusAuftragIn):
    """Rechnung (ENTWURF) aus **Bericht + Zeiten + Material** eines REGIE-Auftrags.

    Nur unterzeichnete Berichte (ein nicht abgenommener Nachweis ist keine
    Abrechnungsgrundlage), nur Arbeitszeit-Buchungen und das am Einsatz gebuchte
    Material; alles, was bereits eine aktive Bindung trägt, bleibt draußen.
    **Preise rechnet der Server** (`vk_vorschlag` / `wage_group.hourly_rate`).

    Ist dieselbe Sache sowohl als **Berichtsposition** als auch als
    **Materialbuchung** erfasst, antwortet der Endpunkt fail-closed mit **422** —
    zusammen fakturiert stünde sie zweimal auf der Rechnung, und keine der vier
    UNIQUE-Sperren sähe es (verschiedene Quellen). Der Ausweg ist ein Schalter
    (`mit_berichten` / `mit_material`), nicht eine Vermutung des Servers.
    Divergieren dabei die Einheiten desselben Artikels („Stk"/„Stück"), kommt die
    Antwort als `einheit_uneindeutig`.

    Steht für eine Position kein Preis fest, antwortet der Endpunkt mit **422 und
    einer strukturierten Klärungsliste** (`preis_unbekannt`) — nicht mit 0,00 €
    und nicht mit einer stillschweigend weggelassenen Position. Der Nutzer nennt
    die fehlenden Einzelpreise in `preise` und ruft denselben Endpunkt erneut auf.

    Das Recht ist dasselbe wie fürs Anlegen (`invoicing/ANLEGEN`): Ein genannter
    Preis geht in **diesen Beleg**, nie in den Artikelstamm.
    """
    actor, _ = require(request, "invoicing", "ANLEGEN")
    try:
        invoice = abrechnung_service.rechnung_aus_auftrag(
            actor,
            work_order_id=payload.work_order_id,
            tax_code=payload.tax_code,
            preise={str(k): v for k, v in (payload.preise or {}).items()},
            mit_berichten=payload.mit_berichten,
            mit_zeiten=payload.mit_zeiten,
            mit_material=payload.mit_material,
            invoice_date=payload.invoice_date,
            due_date=payload.due_date,
            payment_term_days=payload.payment_term_days,
            discount_percent=payload.discount_percent,
            discount_days=payload.discount_days,
            show_labour_costs=payload.show_labour_costs,
        )
    except abrechnung_service.PreisUnbekannt as exc:
        return Status(
            422,
            PreisKlaerungFehlerOut(
                detail=str(exc),
                preis_unbekannt=[PreisKlaerungOut(**p) for p in exc.positionen],
            ),
        )
    except abrechnung_service.EinheitUneindeutig as exc:
        return Status(
            422,
            EinheitUneindeutigFehlerOut(
                detail=str(exc),
                einheit_uneindeutig=[EinheitKonfliktOut(**k) for k in exc.konflikte],
            ),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _invoice_detail(invoice.id))


class NachtragKlaerungOut(Schema):
    """Preisklärung des Nachtrags — **derselbe Weg**, andere Klärungseinheit.

    Geklärt wird je **Abweichung** (Schlüssel des Soll-Ist, z. B.
    `ARTIKEL:<uuid>:stk`), nicht je Berichtszeile: Der Mehrverbrauch entsteht aus
    der Summe über alle Berichte des Auftrags. Deshalb ist `quelle_id` hier ein
    **String** und keine UUID — die Freitextposition (ZUSATZ ohne Artikelbezug) hat
    gar keine ID im Stamm, und gerade sie braucht die Klärung am dringendsten.
    """
    quelle_art: str               # ABWEICHUNG
    quelle_id: str
    bezeichnung: str
    menge: Decimal | None = None
    einheit: str | None = None
    grund: str
    grund_text: str
    vorschlaege: list[PreisVorschlagOut] = []


class NachtragKlaerungFehlerOut(Schema):
    detail: str
    # Genau eine der beiden Listen trägt Einträge: fehlende Preise ODER
    # uneindeutige Einheiten (fail-closed). Das UI unterscheidet daran, ob es die
    # Preisklärung öffnet oder auf die Einheiten-Vereinheitlichung hinweist.
    preis_unbekannt: list[NachtragKlaerungOut] = []
    einheit_uneindeutig: list[EinheitKonfliktOut] = []


class RechnungAusNachtragIn(Schema):
    work_order_id: UUID
    # Pflicht und bewusst ohne Default — wie beim Regieweg: Welcher Steuersatz
    # gilt, ist eine steuerliche Entscheidung des Belegs, kein Ratespiel.
    tax_code: str
    # {schluessel → Einzelpreis} — nur, wo der Server keinen Preis hat.
    preise: dict[str, Decimal] = {}
    invoice_date: date | None = None
    due_date: date | None = None
    payment_term_days: int | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None
    show_labour_costs: bool = True


@router.post(
    "/invoices/aus-nachtrag",
    response={201: InvoiceDetailOut, 422: NachtragKlaerungFehlerOut},
    auth=django_auth,
)
def rechnung_aus_nachtrag(request, payload: RechnungAusNachtragIn):
    """Rechnung (ENTWURF) über die **Abweichungen** eines PAUSCHAL-Auftrags.

    Die Lücke, die dieser Endpunkt schließt: Der Soll-Ist wies den Mehrverbrauch
    sauber aus — abrechnen ließ er sich nur von Hand. Jetzt entsteht daraus ein
    Beleg: MEHRVERBRAUCH mit der **Differenzmenge**, ZUSATZ mit der vollen Menge,
    beides nur aus **unterzeichneten** Berichten und nur, soweit noch nicht
    fakturiert. Jede Position bindet ihre Berichtszeilen — die Doppelabrechnung ist
    physisch gesperrt, der Storno gibt sie wieder frei.

    Fehlt ein Preis: **422 mit Klärungsliste** (`preis_unbekannt`) — niemals eine
    Position über 0,00 €. Recht wie bei den anderen Abrechnungswegen
    (`invoicing/ANLEGEN`): Der Monteur schreibt Berichte, er stellt keine Rechnungen.
    """
    actor, _ = require(request, "invoicing", "ANLEGEN")
    try:
        invoice = abrechnung_service.rechnung_aus_nachtrag(
            actor,
            work_order_id=payload.work_order_id,
            tax_code=payload.tax_code,
            preise={str(k): v for k, v in (payload.preise or {}).items()},
            invoice_date=payload.invoice_date,
            due_date=payload.due_date,
            payment_term_days=payload.payment_term_days,
            discount_percent=payload.discount_percent,
            discount_days=payload.discount_days,
            show_labour_costs=payload.show_labour_costs,
        )
    except abrechnung_service.EinheitUneindeutig as exc:
        # Muss VOR PreisUnbekannt/ValueError stehen (Unterklasse). Fail-closed:
        # kein Beleg, sondern die strukturierte Einheiten-Klärung.
        return Status(
            422,
            NachtragKlaerungFehlerOut(
                detail=str(exc),
                einheit_uneindeutig=[
                    EinheitKonfliktOut(**k) for k in exc.konflikte
                ],
            ),
        )
    except abrechnung_service.PreisUnbekannt as exc:
        return Status(
            422,
            NachtragKlaerungFehlerOut(
                detail=str(exc),
                preis_unbekannt=[NachtragKlaerungOut(**p) for p in exc.positionen],
            ),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _invoice_detail(invoice.id))


class BindungLoesenIn(Schema):
    reason: str


@router.post(
    "/invoices/{invoice_id}/bindungen-loesen",
    response=InvoiceDetailOut,
    auth=django_auth,
)
def bindungen_loesen(request, invoice_id: UUID, payload: BindungLoesenIn):
    """Löst die Abrechnungsbindungen eines **Entwurfs** und entfernt die
    gebundenen Positionen aus ihm.

    Der Weg aus einem verunglückten Entwurf: Die Quellen werden wieder
    abrechenbar — und zwar **weil der Entwurf sie nicht mehr in Rechnung
    stellt**. Beides in einer Transaktion; die Doppelabrechnungssperre bleibt
    lückenlos.

    Recht **STORNIEREN** (wie Storno/Gutschrift): Eine gestellte Bindung wieder
    aufzulösen ist eine bewusste, begründungspflichtige kaufmännische
    Entscheidung. Eine **veröffentlichte** Rechnung wird nicht entbunden, sondern
    storniert (422).
    """
    actor, _ = require(request, "invoicing", "STORNIEREN")
    try:
        abrechnung_service.bindungen_loesen(
            actor, invoice_id=invoice_id, reason=payload.reason
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _invoice_detail(invoice_id)


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


@router.post(
    "/invoices/{invoice_id}/lines", response={201: InvoiceDetailOut}, auth=django_auth
)
def add_invoice_line(request, invoice_id: UUID, payload: QuoteLineIn):
    """Hängt EINE Position an einen Rechnungsentwurf an (ans Ende der Leistung).

    Trägt der Beleg die **Anrechnung** von Abschlagsrechnungen, so bleibt die die
    letzte Position: die neue Zeile geht davor, der Abzug rückt nach.

    Der Weg, einen **gebundenen** Entwurf zu ergänzen (Anfahrtspauschale, Rabatt,
    Zusatztext), ohne die Notbremse `bindungen-loesen` zu ziehen: Der Editor
    (`PUT /invoices/{id}`) ersetzt den ganzen Positionssatz per Delete+Insert und
    läuft damit zwangsläufig gegen die gebundene Zeile (422). Das **INSERT einer
    neuen** Zeile lässt der DB-Trigger dagegen ausdrücklich zu (Migration 0088) —
    sie kann keine Bindung tragen.

    Recht `invoicing/AENDERN` wie beim Editor. Summen rechnet der Server aus allen
    Zeilen neu.
    """
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        beleg_service.add_invoice_line(
            actor, invoice_id=invoice_id, line=payload.dict()
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _invoice_detail(invoice_id))


@router.delete("/invoices/{invoice_id}/lines/last", response=InvoiceDetailOut, auth=django_auth)
def remove_last_invoice_line(request, invoice_id: UUID):
    """Entfernt die **letzte** Position eines Entwurfs — nur, wenn sie ungebunden ist.

    Die Rücknahme einer gerade angehängten Zeile. Bewusst nur die letzte: jede
    andere zu entfernen hieße umnummerieren, und ein UPDATE auf eine gebundene
    Zeile weist die DB ab. Eine gebundene letzte Zeile → 422 (dann bleibt nur
    `bindungen-loesen`). Eine **Anrechnungsposition** ebenfalls → 422: sie gehört
    zur Abschlagsverkettung und wird über die Abschlagszuordnung gepflegt.
    """
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        beleg_service.remove_last_invoice_line(actor, invoice_id=invoice_id)
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


@router.post("/invoices/{invoice_id}/vorschau", response=VorschauOut, auth=django_auth)
def invoice_vorschau(request, invoice_id: UUID, payload: InvoiceUpdateIn):
    """Live-Vorschau einer Rechnung: rechnet den Editor-Payload (wie
    `PUT /invoices/{id}`) durch, ohne zu speichern.

    Bei einer SCHLUSSRECHNUNG mit angerechneten Abschlägen fließt die Anrechnung —
    wie beim echten Speichern — in Summen und Kalkulation ein; sonst wichen die
    Vorschauwerte von den gestellten ab. Rechte wie bei `quote_vorschau`:
    `invoicing/LESEN` für den Endpunkt, `pricing/LESEN` zusätzlich für die
    Kalkulation (sonst null). 404, wenn die Rechnung fehlt; 422 bei Payload-Fehler.
    """
    require(request, "invoicing", "LESEN")
    mit_kalkulation = check(request, "pricing", "LESEN") is not None
    try:
        return beleg_service.vorschau_invoice(
            invoice_id,
            lines=[line.dict() for line in payload.lines or []],
            rubriken=[r.dict() for r in payload.rubriken or []],
            mit_kalkulation=mit_kalkulation,
        )
    except beleg_service.BelegNichtGefunden as exc:
        raise HttpError(404, str(exc))
    except ValueError as exc:
        raise HttpError(422, str(exc))


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


@router.get("/invoices/{invoice_id}/pdf/vorschau")
def invoice_pdf_vorschau(request, invoice_id: UUID):
    """Vorschau-PDF einer Rechnung in JEDEM Status (on-the-fly, unarchiviert).

    Unveröffentlichte Belege tragen einen deutlichen ENTWURF-Aufdruck und
    keinen Giro-Code (ein Entwurf fordert keine Zahlung). Es wird bewusst
    NICHTS archiviert — die GoBD-Ausfertigung entsteht weiterhin
    ausschließlich über GET /invoices/{id}/pdf ab VEROEFFENTLICHT."""
    require(request, "invoicing", "LESEN")
    pdf = beleg_pdf_service.render_invoice_preview(invoice_id)
    if pdf is None:
        raise HttpError(404, "Rechnung nicht gefunden.")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="vorschau.pdf"'
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

@router.delete("/quotes/{quote_id}", response={204: None}, auth=django_auth)
def delete_quote(request, quote_id: UUID):
    """Ein **noch nicht versendetes** Angebot löschen.

    Sascha, 2026-08-02: „Entwürfe alle löschbar … das müllt das System zu."
    Maßgeblich ist die Belegnummer: Sie entsteht erst beim Versand, und solange
    sie fehlt, war das Angebot nie beim Kunden. Danach antwortet der Dienst mit
    422 — ein ausgestelltes Angebot wird abgelehnt oder ersetzt, nicht entfernt.
    """
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        beleg_service.delete_quote(actor, quote_id=quote_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return 204, None
