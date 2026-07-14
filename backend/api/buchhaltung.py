"""Buchhaltungs-API — offene Posten, Zahlungen und Mahnwesen zu veröffentlichten
Rechnungen.

Read-only in der Dev-Phase (Zahlung/Storno/Mahnung laufen über den
buchhaltung-Service, sind aber ohne Auth nicht im UI verdrahtet). Der
Zahlungsstatus und der offene Betrag sind in der DB NICHT gespeichert — sie
werden aus der vorzeichenbehafteten Summe der Zahlungen abgeleitet
(PAYMENT_SIGN, dieselbe Konvention wie im Service).
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import F, Max, Q
from django.http import HttpResponse
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.db_context import business_transaction
from db_core.mail_crypto import MailKeyError
from db_core.models import DunningLevel, DunningNotice, Invoice, Payment
from db_core.services import beleg as beleg_service
from db_core.services import beleg_versand as beleg_versand_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import datev as datev_service
from db_core.services import firma as firma_service
from db_core.services import mahnlauf as mahnlauf_service
from db_core.services import vier_augen
from db_core.services.buchhaltung import (
    PAYMENT_SIGN,
    mit_zahlungsstand as _mit_zahlungsstand,
    zahlungsspiegel as _zahlungsspiegel,
)
from db_core.services.mail import MailSendError

router = Router()

_ZERO = Decimal("0.00")

PAYMENT_STATUSES = ("OFFEN", "TEILZAHLUNG", "BEZAHLT", "UEBERZAHLT", "AUSGEGLICHEN")


# --- Ableitungen -----------------------------------------------------------
# Zahlungsstand, offener Betrag und die Grenze „ist das noch eine Forderung?"
# stehen NICHT in der DB — sie werden abgeleitet. Die Ableitung liegt in
# `db_core.services.buchhaltung` (dort, wo auch PAYMENT_SIGN wohnt): EINE
# Rechenstelle, von der offene Posten, Mahnwesen, Mahnlauf und Dossier ziehen.
# Hier wird nichts nachgerechnet — hier wird gemappt.


def _debtor_name(invoice):
    """Primärer Rechnungsschuldner (INVOICE_DEBTOR) als Anzeige, sonst None."""
    debtor = None
    for p in invoice.parties.all():
        if p.role == "INVOICE_DEBTOR":
            debtor = p
            if p.is_primary:
                break
    return debtor.party.display_name if debtor else None


# --- Schemas ---------------------------------------------------------------

class OpenItemOut(Schema):
    id: UUID
    invoice_number: str | None = None
    invoice_type: str
    status: str
    debtor: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    gross_total: Decimal | None = None
    paid_total: Decimal
    # Summe der veröffentlichten Storno-/Gutschriftbelege zu dieser Rechnung (≤ 0).
    credit_total: Decimal
    # Was zwischen diesem Beleg und seinem Gegenbeleg VERRECHNET ist (≥ 0). Auf der
    # Rechnung: der Teil der Kreditbelege, der die noch offene Forderung aufzehrt.
    # Auf dem Kreditbeleg: sein Anteil daran. Nur der Rest ist zu erstatten.
    verrechnet: Decimal
    # Brutto abzüglich des Verrechneten. Es gilt: open_amount = forderungsbetrag − paid_total.
    forderungsbetrag: Decimal
    # Forderungsbetrag minus Gezahltes. Negativ = Guthaben des Kunden (zu erstatten).
    open_amount: Decimal
    # Klartext für die Oberfläche (Status nie nur über Farbe):
    zu_erstatten: Decimal  # noch an den Kunden zurückzuzahlen (0, wenn nichts offen)
    erstattet: Decimal  # bereits an den Kunden zurückgezahlt
    payment_status: str
    is_overdue: bool
    # Aufgehoben durch einen veröffentlichten STORNO — fordert nichts mehr.
    # Status nie nur über Farbe: das UI schreibt „storniert" dazu.
    is_storniert: bool
    # Fordert diese Rechnung überhaupt (noch) Geld? Kreditbelege und stornierte
    # Rechnungen: nein.
    ist_forderung: bool
    dunning_level: int | None = None


class OpenItemListOut(Schema):
    items: list[OpenItemOut]
    total: int
    page: int
    page_size: int


class PaymentOut(Schema):
    id: UUID
    payment_type: str
    amount: Decimal
    currency: str
    paid_at: date
    import_source: str
    # Storno-Kennzeichnung für das UI (was ist noch stornierbar?):
    is_reversal: bool  # selbst eine Stornobuchung (payment_type STORNO_BUCHUNG)
    is_reversed: bool  # bereits durch eine Gegenbuchung storniert
    is_reversible: bool  # eingehende Zahlung, noch nicht storniert → reverse möglich


class PaymentDetailOut(PaymentOut):
    invoice_id: UUID
    external_reference: str


class PaymentRecordIn(Schema):
    amount: Decimal
    paid_at: date
    payment_type: str = "ZAHLUNG"
    external_reference: str | None = None
    currency: str = "EUR"


class PaymentReverseIn(Schema):
    paid_at: date | None = None


class DunningIssueIn(Schema):
    level: int
    issued_at: date
    note: str | None = None


class DunningNoticeOut(Schema):
    id: UUID
    level: int
    label: str
    issued_at: date
    note: str | None = None
    created_by: str | None = None


class InvoiceRefOut(Schema):
    id: UUID
    property_name: str
    project_name: str | None = None
    work_order_number: str | None = None


class CreditRefOut(Schema):
    id: UUID
    invoice_number: str | None = None
    invoice_type: str
    gross_total: Decimal | None = None


class OpenItemDetailOut(OpenItemOut):
    currency: str
    net_total: Decimal | None = None
    tax_total: Decimal | None = None
    reference: InvoiceRefOut
    # Bei Gutschrift/Storno: der referenzierte Ursprungsbeleg.
    origin: CreditRefOut | None = None
    # Storno-/Gutschriftbelege, die auf DIESE Rechnung verweisen.
    credit_notes: list[CreditRefOut]
    payments: list[PaymentOut]
    dunning: list[DunningNoticeOut]
    # Best-effort vorbelegte Schuldner-E-Mail für den Mahnungsversand-Dialog.
    recipient_email: str | None = None
    # Zahlungsbedingungen der Rechnung, rein informativ (read-only). Sie ändern
    # weder den Zahlungsstatus noch den offenen Betrag: maßgeblich bleibt, was
    # tatsächlich gezahlt wurde (Invariante des Zahlungsspiegels B-23). Ein
    # Skontoabzug erscheint als Zahlungsdifferenz und muss bewusst ausgebucht
    # werden — er wird nie automatisch unterstellt.
    discount_percent: Decimal | None = None
    discount_days: int | None = None
    payment_term_days: int | None = None
    skonto_bis: date | None = None
    skonto_betrag: Decimal | None = None
    skonto_zahlbetrag: Decimal | None = None


class CorrectionIn(Schema):
    positions: list[int]


class PendingApprovalOut(Schema):
    """Antwort, wenn eine Rechnungskorrektur/ein Storno erst einen genehmigten
    Vier-Augen-Antrag braucht (RECHNUNGSKORREKTUR)."""
    pending_approval: UUID
    action_code: str
    detail: str


# Zielreferenz der Vier-Augen-Anträge für Rechnungsvorgänge.
_INVOICE_TARGET = "invoicing.invoice"


class _KeineGenehmigung(Exception):
    """Intern: es liegt keine passende, unverbrauchte Genehmigung vor."""


def _korrektur_payload(operation, positions):
    """Die Beschreibung des Vorgangs, über den der Entscheider entscheidet.

    Sie identifiziert die Genehmigung mit: nur wer GENAU diesen Vorgang genehmigt
    bekommen hat, darf ihn ausführen.
    """
    return {"operation": operation, "positions": positions}


def _mit_genehmigung(actor, invoice_id, *, operation, positions, label, aktion):
    """Führt `aktion` nur mit einer passenden Vier-Augen-Genehmigung aus.

    RECHNUNGSKORREKTUR deckt laut four_eyes-Stammdaten „Rechnungskorrektur/Storno
    nach Veröffentlichung" ab — Storno UND Korrektur sind gleichermaßen
    genehmigungspflichtig, teilen sich also einen `action_code`. Deshalb wird die
    Genehmigung zusätzlich an den **Payload** gebunden: sonst ließe sich eine
    genehmigte Teilgutschrift („Position 1") als Vollstorno der ganzen Rechnung
    einlösen.

    Verbraucht wird die Genehmigung in DERSELBEN Transaktion wie die Aktion
    (`claim` mit `SELECT … FOR UPDATE`):
      * Zwei parallele Einlöser können nicht beide denselben Grant nutzen.
      * Scheitert die Aktion fachlich (422), rollt auch das Verbrauchen zurück —
        die Genehmigung bleibt gültig.
      * Es gibt kein Zeitfenster, in dem der Beleg geschrieben, die Genehmigung
        aber noch unverbraucht ist.
    """
    payload = _korrektur_payload(operation, positions)
    try:
        with business_transaction(actor):
            grant = vier_augen.claim(
                actor, action_code="RECHNUNGSKORREKTUR",
                target_table=_INVOICE_TARGET, target_id=invoice_id, payload=payload,
            )
            if grant is None:
                raise _KeineGenehmigung
            return aktion(), None
    except _KeineGenehmigung:
        pass
    except ValueError as exc:
        raise HttpError(422, str(exc))

    # Kein gültiger Grant → (deduplizierten) Antrag anlegen und 202 melden.
    pending = vier_augen.find_pending(
        "RECHNUNGSKORREKTUR", target_table=_INVOICE_TARGET, target_id=invoice_id
    )
    if pending is None or (pending.payload or {}) != payload:
        pending = vier_augen.request_approval(
            actor,
            action_code="RECHNUNGSKORREKTUR",
            payload=payload,
            target_table=_INVOICE_TARGET,
            target_id=invoice_id,
            reason=label,
        )
    return None, Status(
        202,
        PendingApprovalOut(
            pending_approval=pending.id,
            action_code="RECHNUNGSKORREKTUR",
            detail=(
                f"{label} ist Vier-Augen-pflichtig. Ein Freigabeantrag wurde "
                "angelegt und muss von einer zweiten Person genehmigt werden."
            ),
        ),
    )


class DunningRowOut(Schema):
    id: UUID
    invoice_number: str | None = None
    debtor: str | None = None
    due_date: date | None = None
    gross_total: Decimal | None = None
    open_amount: Decimal
    dunning_level: int | None = None
    last_issued_at: date | None = None
    # Verzugstage AUS dem Zahlungsspiegel (None, sobald nichts mehr offen ist) — die
    # Liste rechnet sie NICHT selbst nach.
    days_overdue: int | None = None
    # Storniert → die Mahnhistorie bleibt sichtbar (kein Löschen), aber es wird
    # keine weitere Stufe mehr ausgestellt.
    is_storniert: bool
    # Lässt sich diese Rechnung (weiter) mahnen? Nur eine offene Forderung.
    mahnbar: bool
    # Warum nicht mehr mahnbar? Das UI nennt den tatsächlichen Zustand (BEZAHLT ist
    # nicht „ausgeglichen": bei BEZAHLT hat jemand gezahlt).
    payment_status: str


class DunningListOut(Schema):
    items: list[DunningRowOut]
    levels: list[dict]


class DunningLevelOut(Schema):
    level: int
    label: str
    days_after_due: int
    active: bool
    # fee/interest_note bleiben NULL (STB-Vorbehalt B-22); zur Transparenz mit ausgegeben.
    fee: Decimal | None = None
    interest_note: str | None = None


class DunningLevelPatch(Schema):
    label: str | None = None
    days_after_due: int | None = None
    active: bool | None = None


class OpenItemFilter(Schema):
    q: str | None = None
    payment_status: str | None = None
    overdue: bool | None = None
    invoice_type: str | None = None


# --- Mapper ----------------------------------------------------------------

# Storno-Buchungen tragen die Referenz 'STORNO:<id-der-Ursprungszahlung>'
# (buchhaltung-Service, es gibt keinen FK). Daraus leitet das UI ab, welche
# Zahlung schon storniert ist.
_STORNO_PREFIX = "STORNO:"


def _reversed_ids(payments):
    """IDs (als str) der Zahlungen, zu denen bereits eine Stornobuchung existiert."""
    out = set()
    for p in payments:
        ref = p.external_reference or ""
        if ref.startswith(_STORNO_PREFIX):
            out.add(ref[len(_STORNO_PREFIX):])
    return out


def _payment_flags(p, reversed_ids):
    """(is_reversal, is_reversed, is_reversible) — Stornierbarkeit fürs UI.

    is_reversible spiegelt exakt die Service-Regel (reverse_payment): nur eine
    eingehende (positiv gewertete) Zahlung, die nicht bereits storniert wurde,
    lässt sich stornieren. Rückerstattungen und Storno-Buchungen (negativ) nicht.
    """
    is_reversal = p.payment_type == "STORNO_BUCHUNG"
    is_reversed = str(p.id) in reversed_ids
    is_reversible = PAYMENT_SIGN.get(p.payment_type, 0) > 0 and not is_reversed
    return is_reversal, is_reversed, is_reversible


def _payment_out(p, reversed_ids):
    is_reversal, is_reversed, is_reversible = _payment_flags(p, reversed_ids)
    return PaymentOut(
        id=p.id,
        payment_type=p.payment_type,
        amount=p.amount,
        currency=p.currency,
        paid_at=p.paid_at,
        import_source=p.import_source,
        is_reversal=is_reversal,
        is_reversed=is_reversed,
        is_reversible=is_reversible,
    )


def _payment_detail(p):
    # Einzelzahlung (Schreib-Antwort): den Storno-Status je Rechnung frisch laden.
    reversed_ids = _reversed_ids(
        Payment.objects.filter(
            invoice_id=p.invoice_id, external_reference__startswith=_STORNO_PREFIX
        )
    )
    is_reversal, is_reversed, is_reversible = _payment_flags(p, reversed_ids)
    return PaymentDetailOut(
        id=p.id,
        invoice_id=p.invoice_id,
        payment_type=p.payment_type,
        amount=p.amount,
        currency=p.currency,
        paid_at=p.paid_at,
        import_source=p.import_source,
        external_reference=p.external_reference,
        is_reversal=is_reversal,
        is_reversed=is_reversed,
        is_reversible=is_reversible,
    )


def _open_item_out(inv, today):
    """Mappt eine annotierte Rechnung — der Geldstand kommt AUS dem Zahlungsspiegel."""
    s = _zahlungsspiegel(inv, heute=today)
    return OpenItemOut(
        id=inv.id,
        invoice_number=inv.invoice_number,
        invoice_type=inv.invoice_type,
        status=inv.status,
        debtor=_debtor_name(inv),
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        gross_total=inv.gross_total,
        paid_total=s["paid_total"],
        credit_total=s["credit_total"],
        verrechnet=s["verrechnet"],
        forderungsbetrag=s["forderungsbetrag"],
        open_amount=s["open_amount"],
        zu_erstatten=s["zu_erstatten"],
        erstattet=s["erstattet"],
        payment_status=s["payment_status"],
        is_overdue=s["is_overdue"],
        is_storniert=s["is_storniert"],
        ist_forderung=s["ist_forderung"],
        dunning_level=s["dunning_level"],
    )


# --- Lesende Endpoints (Dev-Phase ohne Auth) -------------------------------

@router.get("/invoices", response=OpenItemListOut)
def list_open_items(
    request,
    filters: OpenItemFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Offene Posten: veröffentlichte Rechnungen mit abgeleitetem Zahlungsstatus
    und offenem Betrag. Filter: Zahlungsstatus, überfällig, Belegart, Suche
    (Rechnungsnummer).

    Die **Liste zeigt weiterhin jeden veröffentlichten Beleg** — auch stornierte
    Rechnungen und Kreditbelege (GoBD: es verschwindet nichts, und der Beleg bleibt
    aus der Buchhaltung erreichbar). Was sich ändert, ist die **Aussage**: Eine
    stornierte oder vollständig gutgeschriebene Rechnung fordert 0,00 € und ist nie
    „überfällig". Der `overdue`-Filter fördert nur noch echte Forderungen zutage.
    """
    require(request, "invoicing", "LESEN")
    if filters.payment_status and filters.payment_status not in PAYMENT_STATUSES:
        raise HttpError(422, f"Unbekannter payment_status '{filters.payment_status}'.")

    today = date.today()
    base = Invoice.objects.filter(status="VEROEFFENTLICHT")
    if filters.q:
        base = base.filter(invoice_number__icontains=filters.q.strip())
    if filters.invoice_type:
        base = base.filter(invoice_type=filters.invoice_type)
    # Überfällig ist nur, was überhaupt (noch) fordert → die Forderungsgrenze aus
    # dem Buchhaltungs-Service statt `gross_total > paid_total` (das hielt eine
    # stornierte Rechnung für einen überfälligen Posten).
    qs = (
        buchhaltung_service.offene_forderungen(base, stichtag=today)
        if filters.overdue
        else _mit_zahlungsstand(base)
    )
    qs = qs.prefetch_related("parties__party").order_by(
        F("due_date").asc(nulls_last=True), "-created_at", "id"
    )

    start = (page - 1) * page_size
    if filters.payment_status:
        # Der Zahlungsstatus ist abgeleitet (nicht in SQL filterbar) → alle mappen,
        # in Python filtern und erst dann paginieren. Nur dieser Zweig materialisiert
        # die volle Ergebnismenge.
        items = [
            it
            for it in (_open_item_out(inv, today) for inv in qs)
            if it.payment_status == filters.payment_status
        ]
        total = len(items)
        window = items[start:start + page_size]
    else:
        total = qs.count()
        window = [_open_item_out(inv, today) for inv in qs[start:start + page_size]]
    return OpenItemListOut(
        items=window, total=total, page=page, page_size=page_size
    )


@router.get("/invoices/{invoice_id}", response=OpenItemDetailOut)
def get_open_item(request, invoice_id: UUID):
    """Detail eines offenen Postens inkl. Zahlungen und Mahnverlauf."""
    require(request, "invoicing", "LESEN")
    today = date.today()
    inv = (
        _mit_zahlungsstand(
            Invoice.objects.filter(id=invoice_id, status="VEROEFFENTLICHT")
        )
        .select_related("property__address", "project", "work_order")
        .prefetch_related("parties__party")
        .first()
    )
    if inv is None:
        raise HttpError(404, "Veröffentlichte Rechnung nicht gefunden.")

    payment_rows = list(
        Payment.objects.filter(invoice_id=inv.id).order_by("paid_at", "imported_at")
    )
    reversed_ids = _reversed_ids(payment_rows)
    payments = [_payment_out(p, reversed_ids) for p in payment_rows]
    notices = [
        DunningNoticeOut(
            id=n.id,
            level=n.level_id,
            label=n.level.label,
            issued_at=n.issued_at,
            note=n.note,
            created_by=n.created_by.display_name if n.created_by_id else None,
        )
        for n in DunningNotice.objects.filter(invoice_id=inv.id)
        .select_related("level", "created_by")
        .order_by("level")
    ]

    base = _open_item_out(inv, today)
    reference = InvoiceRefOut(
        id=inv.id,
        property_name=f"{inv.property.name} · {inv.property.address.city}",
        project_name=inv.project.name if inv.project_id else None,
        work_order_number=inv.work_order.order_number if inv.work_order_id else None,
    )
    origin = None
    if inv.reference_invoice_id:
        o = Invoice.objects.filter(id=inv.reference_invoice_id).first()
        if o is not None:
            origin = CreditRefOut(
                id=o.id, invoice_number=o.invoice_number,
                invoice_type=o.invoice_type, gross_total=o.gross_total,
            )
    credit_notes = [
        CreditRefOut(
            id=c.id, invoice_number=c.invoice_number,
            invoice_type=c.invoice_type, gross_total=c.gross_total,
        )
        for c in Invoice.objects.filter(
            reference_invoice_id=inv.id, status="VEROEFFENTLICHT"
        ).order_by("created_at")
    ]
    zb = beleg_service.zahlungsbedingungen(inv) or {}
    return OpenItemDetailOut(
        **base.model_dump(),
        currency=inv.currency,
        net_total=inv.net_total,
        tax_total=inv.tax_total,
        reference=reference,
        origin=origin,
        credit_notes=credit_notes,
        payments=payments,
        dunning=notices,
        recipient_email=beleg_versand_service.debtor_email(inv),
        discount_percent=inv.discount_percent,
        discount_days=inv.discount_days,
        payment_term_days=inv.payment_term_days,
        skonto_bis=zb.get("skonto_bis"),
        skonto_betrag=zb.get("skonto_betrag"),
        skonto_zahlbetrag=zb.get("skonto_zahlbetrag"),
    )


# --- Schreibende Endpoints (Session-Auth Pflicht) --------------------------

@router.post(
    "/invoices/{invoice_id}/cancel",
    response={201: CreditRefOut, 202: PendingApprovalOut},
    auth=django_auth,
)
def cancel_invoice(request, invoice_id: UUID):
    """Storniert eine veröffentlichte Rechnung durch einen Stornobeleg (STORNO).

    Vier-Augen-pflichtig (RECHNUNGSKORREKTUR): ohne genehmigten Antrag wird ein
    Freigabeantrag angelegt (202) statt storniert; erst der genehmigte Antrag
    lässt den Storno zu (201)."""
    actor, _ = require(request, "invoicing", "STORNIEREN")
    credit, pending_response = _mit_genehmigung(
        actor, invoice_id, operation="STORNO", positions=None,
        label="Rechnungsstorno",
        aktion=lambda: beleg_service.create_cancellation(actor, invoice_id=invoice_id),
    )
    if pending_response is not None:
        return pending_response
    return Status(
        201,
        CreditRefOut(
            id=credit.id, invoice_number=credit.invoice_number,
            invoice_type=credit.invoice_type, gross_total=credit.gross_total,
        ),
    )


@router.post(
    "/invoices/{invoice_id}/correction",
    response={201: CreditRefOut, 202: PendingApprovalOut},
    auth=django_auth,
)
def correct_invoice(request, invoice_id: UUID, payload: CorrectionIn):
    """Erzeugt eine Rechnungskorrektur (GUTSCHRIFT) über die angegebenen Positionen.

    Vier-Augen-pflichtig (RECHNUNGSKORREKTUR): ohne genehmigten Antrag wird ein
    Freigabeantrag angelegt (202); erst der genehmigte Antrag lässt die Korrektur
    zu (201)."""
    # Korrektur/Gutschrift eines veröffentlichten Belegs = Storno-Folgebeleg → STORNIEREN.
    actor, _ = require(request, "invoicing", "STORNIEREN")
    credit, pending_response = _mit_genehmigung(
        actor, invoice_id, operation="GUTSCHRIFT", positions=payload.positions,
        label="Rechnungskorrektur",
        aktion=lambda: beleg_service.create_correction(
            actor, invoice_id=invoice_id, positions=payload.positions
        ),
    )
    if pending_response is not None:
        return pending_response
    return Status(
        201,
        CreditRefOut(
            id=credit.id, invoice_number=credit.invoice_number,
            invoice_type=credit.invoice_type, gross_total=credit.gross_total,
        ),
    )


@router.get("/dunning", response=DunningListOut)
def list_dunning(request, level: int | None = Query(None)):
    """Mahnliste: veröffentlichte Rechnungen, die als offene Forderung überfällig
    sind **oder** bereits gemahnt wurden. Optional nach aktueller Mahnstufe
    gefiltert (level=0 → überfällig, aber noch ungemahnt).

    **Ein neuer Mahnfall entsteht nur aus einer echten Forderung** (Grenze aus dem
    Buchhaltungs-Service: kein Kreditbeleg, nicht storniert, offener Betrag nach
    Gutschriften und Zahlungen > 0). Eine **bereits gemahnte** Rechnung bleibt
    dagegen sichtbar, auch wenn sie danach storniert wurde — die Mahnhistorie wird
    nicht gelöscht (GoBD). Sie ist dann aber `mahnbar=False`: der Mahnlauf
    überspringt sie, und das UI sagt es dazu.
    """
    require(request, "invoicing", "LESEN")
    today = date.today()
    # `storniert` ist die Annotation aus `mit_zahlungsstand` (Exists auf den
    # veröffentlichten STORNO) — dasselbe Prädikat wie in `forderungen()`.
    ist_forderung = ~Q(invoice_type__in=beleg_service.CREDIT_TYPES) & Q(storniert=False)
    qs = (
        _mit_zahlungsstand(Invoice.objects.filter(status="VEROEFFENTLICHT"))
        .filter(
            (ist_forderung & Q(due_date__lt=today, open_amount__gt=_ZERO))
            | Q(dunning_level__isnull=False)
        )
        .prefetch_related("parties__party")
        .order_by(F("due_date").asc(nulls_last=True), "id")
    )

    last_issued = {
        row["invoice_id"]: row["m"]
        for row in DunningNotice.objects.filter(invoice_id__in=[i.id for i in qs])
        .values("invoice_id")
        .annotate(m=Max("issued_at"))
    }

    items = []
    for inv in qs:
        s = _zahlungsspiegel(inv, heute=today)
        cur = s["dunning_level"] or 0
        if level is not None and cur != level:
            continue
        items.append(
            DunningRowOut(
                id=inv.id,
                invoice_number=inv.invoice_number,
                debtor=_debtor_name(inv),
                due_date=inv.due_date,
                gross_total=inv.gross_total,
                open_amount=s["open_amount"],
                dunning_level=s["dunning_level"],
                last_issued_at=last_issued.get(inv.id),
                # Überfälligkeitstage kommen AUS dem Zahlungsspiegel — hier wird
                # nichts nachgerechnet. Die eigene Rechnung („today − due_date",
                # sobald der Beleg eine Forderung IST) ließ eine voll bezahlte,
                # früher gemahnte Rechnung als „30 Tage überfällig" erscheinen,
                # direkt neben „nichts mehr offen". Im Verzug ist nur, wer schuldet.
                days_overdue=s["days_overdue"],
                is_storniert=s["is_storniert"],
                mahnbar=s["mahnbar"],
                payment_status=s["payment_status"],
            )
        )

    levels = [
        {"level": lv.level, "label": lv.label,
         "days_after_due": lv.days_after_due, "active": lv.active}
        for lv in DunningLevel.objects.order_by("level")
    ]
    return DunningListOut(items=items, levels=levels)


# --- Mahnstufen-Stammdaten (Konfiguration) ---------------------------------

def _dunning_level_out(lv):
    return DunningLevelOut(
        level=lv.level, label=lv.label, days_after_due=lv.days_after_due,
        active=lv.active, fee=lv.fee, interest_note=lv.interest_note,
    )


@router.get("/dunning-levels", response=list[DunningLevelOut])
def list_dunning_levels(request):
    """Alle Mahnstufen (inkl. deaktivierter), aufsteigend."""
    require(request, "invoicing", "LESEN")
    return [_dunning_level_out(lv) for lv in firma_service.list_dunning_levels()]


@router.put("/dunning-levels/{level}", response=DunningLevelOut, auth=django_auth)
def update_dunning_level(request, level: int, payload: DunningLevelPatch):
    """Pflegt Bezeichnung, Frist und Aktivierung einer Mahnstufe (AENDERN).

    fee/interest_note bleiben unangetastet (STB-Vorbehalt B-22). Das Deaktivieren
    einer mittleren Stufe wird abgelehnt (Lücken-Regel, 422), damit die
    Eskalation lückenlos ausführbar bleibt.
    """
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        lv = firma_service.update_dunning_level(
            actor, level=level, **payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _dunning_level_out(lv)


# --- Schreibende Endpoints: Zahlungen und Mahnwesen ------------------------

@router.post(
    "/invoices/{invoice_id}/payments", response={201: PaymentDetailOut}, auth=django_auth
)
def record_payment(request, invoice_id: UUID, payload: PaymentRecordIn):
    """Erfasst eine (Teil-)Zahlung zu einer veröffentlichten Rechnung.

    Recht AENDERN: eine Zahlung verändert den offenen Posten einer bestehenden
    Rechnung — kein Freigabe-/Storno-Vorgang, sondern die laufende Pflege des
    Zahlungsstands. amount ist stets positiv; das Vorzeichen ergibt sich aus dem
    payment_type (PAYMENT_SIGN). Die DB-Tore (nur veröffentlichte Rechnung B-23,
    Idempotenz) landen als 422 beim Aufrufer.
    """
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        payment = buchhaltung_service.record_payment(
            actor,
            invoice_id=invoice_id,
            amount=payload.amount,
            paid_at=payload.paid_at,
            payment_type=payload.payment_type,
            external_reference=payload.external_reference,
            currency=payload.currency,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _payment_detail(payment))


@router.post("/payments/{payment_id}/reverse", response=PaymentDetailOut, auth=django_auth)
def reverse_payment(request, payment_id: UUID, payload: PaymentReverseIn):
    """Storniert eine Zahlung durch eine Gegenbuchung (STORNO_BUCHUNG).

    Recht STORNIEREN: eine erfasste Zahlung nachträglich unwirksam zu machen ist
    ein Storno-Vorgang, nicht die normale Pflege — deshalb das eigene, engere
    Recht. Physisch wird nichts gelöscht (append-only); nur eingehende Zahlungen
    lassen sich stornieren, und keine doppelt (→ 422).
    """
    actor, _ = require(request, "invoicing", "STORNIEREN")
    try:
        storno = buchhaltung_service.reverse_payment(
            actor, payment_id=payment_id, paid_at=payload.paid_at
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _payment_detail(storno)


@router.post(
    "/invoices/{invoice_id}/dunning", response={201: DunningNoticeOut}, auth=django_auth
)
def issue_dunning_notice(request, invoice_id: UUID, payload: DunningIssueIn):
    """Erzeugt eine Mahnstufe (Zahlungserinnerung/Mahnung) zu einer Rechnung.

    Recht VERSENDEN: eine Mahnung ist eine nach außen wirkende Kundenkommunikation
    — dasselbe Recht, das auch Angebots-/Belegversand trägt. Die DB erzwingt eine
    veröffentlichte, zum issued_at fällige Rechnung und die nächste lückenlose
    Stufe (max+1); Verstöße kommen als 422 zurück.
    """
    actor, _ = require(request, "invoicing", "VERSENDEN")
    try:
        notice = buchhaltung_service.issue_dunning_notice(
            actor,
            invoice_id=invoice_id,
            level=payload.level,
            issued_at=payload.issued_at,
            note=payload.note,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    notice = (
        DunningNotice.objects.select_related("level", "created_by")
        .get(id=notice.id)
    )
    return Status(
        201,
        DunningNoticeOut(
            id=notice.id,
            level=notice.level_id,
            label=notice.level.label,
            issued_at=notice.issued_at,
            note=notice.note,
            created_by=notice.created_by.display_name if notice.created_by_id else None,
        ),
    )


# --- Mahnungsversand per E-Mail --------------------------------------------

class DunningEmailIn(Schema):
    # Optionaler Adress-Override; leer → serverseitig ermittelte Schuldner-EMAIL.
    to_address: str | None = None


class DunningEmailOut(Schema):
    sent: bool
    to_address: str


@router.post(
    "/dunning-notices/{notice_id}/send-email",
    response=DunningEmailOut,
    auth=django_auth,
)
def send_dunning_email(request, notice_id: UUID, payload: DunningEmailIn):
    """Versendet eine ausgestellte Mahnung/Zahlungserinnerung als E-Mail an den
    Schuldner, mit der Rechnung als PDF-Anhang.

    Recht VERSENDEN: eine Mahnung ist nach außen wirkende Kundenkommunikation —
    dasselbe Recht wie Rechnungs-/Angebotsversand und das Ausstellen der Mahnung.
    Reine Zustellung (kein Statuswechsel, keine GoBD-Berührung). Betreff/Body je
    Mahnstufe (Zahlungserinnerung sachlich-freundlich, Mahnung bestimmter). Fehler
    passwortfrei → 422 (kein Empfänger/Konto, Schlüssel- oder SMTP-Fehler);
    unbekannte Mahnung → 404.
    """
    actor, _ = require(request, "invoicing", "VERSENDEN")
    try:
        communication = beleg_versand_service.send_dunning_email(
            actor, dunning_notice_id=notice_id, to_address=payload.to_address
        )
    except LookupError as exc:
        raise HttpError(404, str(exc))
    except ValueError as exc:
        raise HttpError(422, str(exc))
    except (MailKeyError, MailSendError) as exc:
        # Passwortfreie, klare Meldung an das UI statt eines 500-Leaks.
        raise HttpError(422, str(exc))
    return DunningEmailOut(sent=True, to_address=communication.counterpart_raw)


# --- Mahnlauf (semi-automatischer Stapel) ----------------------------------

class MahnlaufCandidateOut(Schema):
    invoice_id: UUID
    invoice_number: str | None = None
    debtor: str | None = None
    due_date: date | None = None
    open_amount: Decimal
    current_level: int
    next_level: int
    next_level_label: str
    days_overdue: int
    # Ermittelte Schuldner-E-Mail; None → Stufe ausstellbar, aber nicht mailbar.
    recipient_email: str | None = None


class MahnlaufPreviewOut(Schema):
    stichtag: date
    candidates: list[MahnlaufCandidateOut]


class MahnlaufItemIn(Schema):
    invoice_id: UUID
    # Erwartete nächste Stufe (aus der Vorschau); der Lauf prüft sie erneut.
    level: int


class MahnlaufIn(Schema):
    items: list[MahnlaufItemIn]
    send_email: bool = True
    stichtag: date | None = None


class MahnlaufResultRowOut(Schema):
    invoice_id: UUID
    status: str  # issued | sent | skipped | failed
    level: int | None = None
    notice_id: UUID | None = None
    detail: str | None = None


class MahnlaufResultOut(Schema):
    issued: int
    sent: int
    skipped: int
    failed: int
    results: list[MahnlaufResultRowOut]


@router.get("/mahnlauf/vorschau", response=MahnlaufPreviewOut)
def mahnlauf_vorschau(request, stichtag: date | None = Query(None)):
    """Vorschau des Mahnlaufs: alle Rechnungen, die zum Stichtag (Vorgabe: heute)
    für ihre nächste Mahnstufe fällig sind. Reine Ansicht (invoicing/LESEN)."""
    require(request, "invoicing", "LESEN")
    tag = stichtag or date.today()
    candidates = mahnlauf_service.list_candidates(stichtag=tag)
    return MahnlaufPreviewOut(
        stichtag=tag,
        candidates=[MahnlaufCandidateOut(**c) for c in candidates],
    )


@router.post("/mahnlauf", response=MahnlaufResultOut, auth=django_auth)
def mahnlauf_ausfuehren(request, payload: MahnlaufIn):
    """Führt einen bestätigten Mahnlauf aus: stellt je Rechnung die nächste
    Mahnstufe aus und versendet sie optional per E-Mail.

    Recht VERSENDEN (wie die Einzel-Mahnung — nach außen wirkende Kundenkommuni-
    kation). Jede Rechnung ist unabhängig: der Lauf bricht bei einem Fehler nicht
    ab, sondern meldet je Zeile issued/sent/skipped/failed. Ein zwischenzeitlich
    anderweitig gemahnter Beleg wird übersprungen (kein Doppel-Eskalieren)."""
    actor, _ = require(request, "invoicing", "VERSENDEN")
    result = mahnlauf_service.run(
        actor,
        items=[{"invoice_id": i.invoice_id, "level": i.level} for i in payload.items],
        stichtag=payload.stichtag or date.today(),
        send_email=payload.send_email,
    )
    return MahnlaufResultOut(**result)


# --- DATEV-Export ----------------------------------------------------------

@router.get("/datev-export.csv")
def datev_export(request, von: date | None = Query(None), bis: date | None = Query(None)):
    """EXTF-Buchungsstapel der veröffentlichten Rechnungen im Zeitraum [von, bis]
    zum Download (DATEV-Import beim Steuerberater).

    Reiner Leseexport (invoicing/LESEN). Der Zeitraum muss in einem Kalenderjahr
    liegen. Fehlt die DATEV-Konfiguration im Firmenprofil oder ist ein Steuercode
    nicht zugeordnet, meldet der Service 422 mit klarer Begründung.

    `von`/`bis` sind bewusst als optional deklariert (mit Pflichtprüfung im Rumpf),
    damit die Rechteprüfung VOR der Parameter-Validierung greift — sonst bekäme ein
    Nutzer ohne Recht ein 422 (fehlende Parameter) statt des korrekten 403.
    """
    require(request, "invoicing", "LESEN")
    if von is None or bis is None:
        raise HttpError(422, "Bitte einen Zeitraum (von, bis) angeben.")
    try:
        dateiname, inhalt = datev_service.build_datev_export(von, bis)
    except datev_service.DatevExportError as exc:
        raise HttpError(422, str(exc))
    antwort = HttpResponse(inhalt, content_type="text/csv; charset=windows-1252")
    antwort["Content-Disposition"] = f'attachment; filename="{dateiname}"'
    antwort["X-Content-Type-Options"] = "nosniff"
    return antwort
