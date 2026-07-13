"""Baustellenbericht-API (workflow.site_report).

Ein Bericht hängt an einem **Anker**: am Auftrag (`work_order`), am Einsatz
(`service_job`) oder an beidem — nie im Leeren (DB-CHECK, Migration 0064). Damit
trägt auch der **freie Termin** (Einsatz ohne Auftrag, 0062) ein
Begehungsprotokoll. Fotos werden über die Datei-API (`/content/files` mit
`site_report_id`) angehängt. Die Kundenunterschrift wird als Base64-PNG
entgegengenommen, im Objektspeicher abgelegt und besiegelt den Bericht
(ENTWURF → UNTERZEICHNET); danach ist er unveränderlich.

Rechte-Tore (Modul `workflow`):
  * Lesen:      `LESEN`
  * Anlegen:    `ANLEGEN`
  * Ändern:     `AENDERN`
  * Unterschreiben (Abnahme): `AENDERN`

**row_scope 'EIGENE' (Monteur)** ist hier echt umgesetzt — der Bericht ist genau
das, was der Monteur vor Ort schreibt und unterschreiben lässt. Die Grenze hängt
(wie überall bei Einsätzen) allein an `workflow.job_assignment`:

  * Berichte **seines** Einsatzes: lesen, anlegen, ändern, unterschreiben lassen.
  * Bericht ohne Einsatzbezug oder an einem fremden Einsatz: **404** — die
    Existenz fremder Berichte wird nicht verraten (Muster `api/planung.py`).
  * Die **Auftragssicht** (`?work_order_id=…`) ist eine Dispositionssicht über
    alle Berichte der Baustelle und lässt sich nicht auf eigene Zeilen begrenzen:
    Scope 'EIGENE' → 403 (fail-closed). Der Monteur nimmt den Einsatzweg.

**Positionen und Soll-Ist (Migration 0080).** Der Bericht führt Positionen aus dem
Artikel-/Leistungsstamm — **ohne Preise** (ein unterschriebener Bericht mit Preisen
wäre eine Preisvereinbarung; der Preis entsteht erst in der Rechnung). Daraus
entsteht der Soll-Ist-Abgleich am Auftrag; auch er weist **keine Geldbeträge** aus.
Der Soll-Ist ist wie die Auftragsliste eine Dispositionssicht → Scope 'EIGENE' 403.
"""
import base64
import binascii
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require_scoped
from db_core.models import JobAssignment, ServiceJob, WorkOrder
from db_core.services import site_report as report_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class SiteReportOut(Schema):
    id: UUID
    work_order_id: UUID | None = None
    service_job_id: UUID | None = None
    report_date: date
    author_id: UUID | None = None
    author_name: str | None = None
    weather: str | None = None
    activity_text: str
    hours_worked: Decimal | None = None
    materials_note: str | None = None
    remarks: str | None = None
    status: str
    signed_by_name: str | None = None
    signed_at: datetime | None = None
    signature_file_id: UUID | None = None
    version: int
    created_at: datetime


class SiteReportListOut(Schema):
    items: list[SiteReportOut]
    total: int


class SiteReportLineOut(Schema):
    """Berichtsposition. **Trägt bewusst KEINE Preisfelder** (Migration 0080)."""

    id: UUID
    position_number: int
    line_type: str
    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    source_article_id: UUID | None = None
    source_assembly_id: UUID | None = None
    planned_quantity: Decimal | None = None
    source_quote_line_id: UUID | None = None
    note: str | None = None


class SiteReportDetailOut(SiteReportOut):
    lines: list[SiteReportLineOut] = []


class SiteReportLineIn(Schema):
    """Eingabe einer Berichtsposition. Ohne Preise (Migration 0080).

    `planned_quantity` ist **kein Eingabefeld**: Das Soll wird ausschließlich aus
    `source_quote_line_id` abgeleitet (ein mitgeschickter Wert wird verworfen), und
    ohne Herkunft ist es verboten (422). Es steht hier nur, damit ein Fälschungs-
    versuch als Fachfehler auffällt, statt stillschweigend zu wirken — ein frei
    gesetztes Soll landete sonst auf einem unterschriebenen Kundendokument.
    """

    line_type: str
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    source_article_id: UUID | None = None
    source_assembly_id: UUID | None = None
    planned_quantity: Decimal | None = None
    source_quote_line_id: UUID | None = None
    note: str | None = None


class SiteReportLinesIn(Schema):
    lines: list[SiteReportLineIn] = []


class SiteReportLinesOut(Schema):
    items: list[SiteReportLineOut]
    total: int


class VorbelegenIn(Schema):
    quote_id: UUID


class VorbelegbaresAngebotOut(Schema):
    """Auswahlkandidat für die Vorbelegung. **Ohne Beträge** — der Bericht führt
    keine Preise, und die Auswahlliste braucht auch keine."""

    id: UUID
    quote_number: str | None = None
    title: str
    status: str


class SollIstPositionOut(Schema):
    schluessel: str
    source_article_id: UUID | None = None
    source_assembly_id: UUID | None = None
    bezeichnung: str
    einheit: str | None = None
    soll: Decimal
    ist: Decimal
    differenz: Decimal
    # MEHRVERBRAUCH | MINDERVERBRAUCH | ZUSATZ | ENTFALLEN | UNVERAENDERT
    art: str


class SollIstAngebotOut(Schema):
    """Ein Angebot, das in das Soll eingeflossen ist. **Ohne Beträge.**"""

    id: UUID
    quote_number: str | None = None
    title: str
    status: str


class SollIstOut(Schema):
    work_order_id: UUID
    positionen: list[SollIstPositionOut]
    # Worauf stützt sich das Soll? Ohne diese Angabe wäre jede Differenz eine
    # Behauptung. Leer = dem Auftrag ist kein (gültiges) Angebot zugeordnet —
    # dann ist alles ZUSATZ, und der Nutzer sieht auch, warum.
    angebote: list[SollIstAngebotOut] = []
    # Sind unsignierte (= noch änderbare) Berichte eingeflossen? Dann ist das
    # Ergebnis vorläufig. Wird ausgewiesen, nicht verschwiegen.
    enthaelt_entwuerfe: bool


class SiteReportIn(Schema):
    report_date: date
    activity_text: str
    # Anker: mindestens eines von beiden. Beim freien Termin nur der Einsatz.
    work_order_id: UUID | None = None
    service_job_id: UUID | None = None
    weather: str | None = None
    hours_worked: Decimal | None = None
    materials_note: str | None = None
    remarks: str | None = None


class SiteReportUpdateIn(Schema):
    report_date: date | None = None
    service_job_id: UUID | None = None
    weather: str | None = None
    activity_text: str | None = None
    hours_worked: Decimal | None = None
    materials_note: str | None = None
    remarks: str | None = None


class SiteReportSignIn(Schema):
    signed_by_name: str
    # PNG der Unterschrift als Base64 (Canvas → toDataURL). Der Data-URL-Präfix
    # ("data:image/png;base64,") wird toleriert.
    signature_png_base64: str


# --- Mapper ----------------------------------------------------------------

def _out(report):
    return SiteReportOut(
        id=report.id,
        work_order_id=report.work_order_id,
        service_job_id=report.service_job_id,
        report_date=report.report_date,
        author_id=report.author_id,
        author_name=(report.author.display_name if report.author_id else None),
        weather=report.weather,
        activity_text=report.activity_text,
        hours_worked=report.hours_worked,
        materials_note=report.materials_note,
        remarks=report.remarks,
        status=report.status,
        signed_by_name=report.signed_by_name,
        signed_at=report.signed_at,
        signature_file_id=report.signature_file_id,
        version=report.version,
        created_at=report.created_at,
    )


def _line_out(line):
    return SiteReportLineOut(
        id=line.id,
        position_number=line.position_number,
        line_type=line.line_type,
        description=line.description,
        quantity=line.quantity,
        unit=line.unit,
        source_article_id=line.source_article_id,
        source_assembly_id=line.source_assembly_id,
        planned_quantity=line.planned_quantity,
        source_quote_line_id=line.source_quote_line_id,
        note=line.note,
    )


def _detail_out(report):
    return SiteReportDetailOut(
        **_out(report).dict(),
        lines=[_line_out(l) for l in report_service.list_report_lines(report.id)],
    )


def _dekodiere_signatur(base64_wert: str) -> bytes:
    roh = (base64_wert or "").strip()
    if roh.startswith("data:"):
        # data:image/png;base64,<...>
        _, _, roh = roh.partition(",")
    try:
        return base64.b64decode(roh, validate=True)
    except (binascii.Error, ValueError):
        raise HttpError(422, "Die Unterschrift ist kein gültiges Base64-PNG.")


# --- Zeilenbegrenzung ('EIGENE') -------------------------------------------

def _guard_own_job(job_id, actor, scope):
    """Scope 'EIGENE': nur ein Einsatz, dem der Akteur zugewiesen ist. Sonst 404.
    Muster: `api/planung.py::_guard_own_job`."""
    if scope != "EIGENE":
        return
    if not JobAssignment.objects.filter(
        service_job_id=job_id, assignee_id=actor
    ).exists():
        raise HttpError(404, "Einsatz nicht gefunden.")


def _guard_own_report(report, actor, scope):
    """Scope 'EIGENE': nur Berichte an einem Einsatz, dem der Akteur zugewiesen
    ist. Ein reiner Auftragsbericht (ohne Einsatz) ist für ihn **nicht** sichtbar
    — er hat keine Zuweisung, an der die Sicht hängen könnte. 404 statt 403."""
    if scope != "EIGENE":
        return
    if report.service_job_id is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    if not JobAssignment.objects.filter(
        service_job_id=report.service_job_id, assignee_id=actor
    ).exists():
        raise HttpError(404, "Bericht nicht gefunden.")


def _auftragssicht_verboten(scope):
    """Die Auftragssicht zeigt alle Berichte der Baustelle — sie lässt sich nicht
    auf eigene Zeilen begrenzen. Scope 'EIGENE' → 403 (fail-closed)."""
    if scope == "EIGENE":
        raise HttpError(
            403,
            "Ihre Rolle erlaubt nur den Zugriff auf eigene Datensätze; "
            "Berichte sind für Sie über den eigenen Einsatz erreichbar.",
        )


# --- Endpoints -------------------------------------------------------------

@router.get("/site_reports", response=SiteReportListOut)
def list_site_reports(
    request,
    work_order_id: UUID | None = None,
    service_job_id: UUID | None = None,
):
    """Baustellenberichte eines Auftrags ODER eines Einsatzes (neueste zuerst).

    Genau einer der beiden Filter ist zu setzen. Die Auftragsliste enthält auch
    die Berichte der Einsätze dieses Auftrags (der Bericht am auftragsgebundenen
    Einsatz trägt zwingend dessen Auftrag).
    """
    # Rechteprüfung VOR der Parametervalidierung: die Filter sind bewusst
    # optional, damit ein rollenloser Aufruf 403 (nicht 422) bekommt und die
    # Existenz von Auftrag/Einsatz nicht durchsickert.
    actor, scope = require_scoped(request, "workflow", "LESEN")
    if (work_order_id is None) == (service_job_id is None):
        raise HttpError(
            422, "Genau eines von work_order_id oder service_job_id ist erforderlich."
        )
    if service_job_id is not None:
        if not ServiceJob.objects.filter(id=service_job_id).exists():
            raise HttpError(404, "Einsatz nicht gefunden.")
        _guard_own_job(service_job_id, actor, scope)
    else:
        _auftragssicht_verboten(scope)
        if not WorkOrder.objects.filter(id=work_order_id).exists():
            raise HttpError(404, "Auftrag nicht gefunden.")
    reports = report_service.list_reports(
        work_order_id=work_order_id, service_job_id=service_job_id
    )
    items = [_out(r) for r in reports]
    return SiteReportListOut(items=items, total=len(items))


@router.get("/site_reports/{report_id}", response=SiteReportDetailOut)
def get_site_report(request, report_id: UUID):
    """Ein Baustellenbericht im Detail — **mit seinen Positionen**.

    Fremder Bericht (Scope 'EIGENE') → 404.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    _guard_own_report(report, actor, scope)
    return _detail_out(report)


@router.post("/site_reports", response={201: SiteReportOut}, auth=django_auth)
def create_site_report(request, payload: SiteReportIn):
    """Neuen Baustellenbericht (Status ENTWURF) anlegen.

    `require_scoped` statt `require_create`: Der Bericht hängt an einem
    **fremden Elternobjekt** (Auftrag/Einsatz). Ein Monteur (Scope 'EIGENE') darf
    ihn nur an einem Einsatz anlegen, dem er zugewiesen ist — sonst schriebe er
    Nachweise an Baustellen, die er nie gesehen hat. Der Auftrag wird aus dem
    Einsatz abgeleitet (Service); ein widersprüchlicher `work_order_id` → 422.
    """
    actor, scope = require_scoped(request, "workflow", "ANLEGEN")
    if scope == "EIGENE":
        if payload.service_job_id is None:
            raise HttpError(
                403,
                "Ihre Rolle erlaubt nur den Zugriff auf eigene Datensätze; "
                "ein Bericht ist nur an einem Ihnen zugewiesenen Einsatz möglich.",
            )
        if not ServiceJob.objects.filter(id=payload.service_job_id).exists():
            raise HttpError(404, "Einsatz nicht gefunden.")
        _guard_own_job(payload.service_job_id, actor, scope)
    try:
        report = report_service.create_report(
            actor,
            work_order_id=payload.work_order_id,
            service_job_id=payload.service_job_id,
            report_date=payload.report_date,
            activity_text=payload.activity_text,
            weather=payload.weather,
            hours_worked=payload.hours_worked,
            materials_note=payload.materials_note,
            remarks=payload.remarks,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _out(report))


@router.put("/site_reports/{report_id}", response=SiteReportOut, auth=django_auth)
def update_site_report(request, report_id: UUID, payload: SiteReportUpdateIn):
    """Einen Bericht ändern — nur im ENTWURF. Nur gesetzte Felder werden geändert."""
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    _guard_own_report(report, actor, scope)
    fields = payload.dict(exclude_unset=True)
    if scope == "EIGENE" and "service_job_id" in fields:
        # Ein UMhängen ist verboten (sonst schriebe der Monteur an einem fremden
        # Einsatz). Den unveränderten Wert mitzuschicken ist dagegen harmlos —
        # Formulare senden ihre Felder vollständig; das darf kein 403 auslösen.
        if str(fields["service_job_id"] or "") != str(report.service_job_id or ""):
            raise HttpError(
                403,
                "Der Einsatzbezug des Berichts ist Dispositionsdatum und für Ihre "
                "Rolle nicht änderbar.",
            )
        fields.pop("service_job_id")
    try:
        report = report_service.update_report(actor, report_id=report_id, **fields)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _out(report)


@router.post("/site_reports/{report_id}/sign", response=SiteReportOut, auth=django_auth)
def sign_site_report(request, report_id: UUID, payload: SiteReportSignIn):
    """Bericht mit der Kundenunterschrift besiegeln (ENTWURF → UNTERZEICHNET).

    Die Abnahme geschieht **vor Ort** — der Monteur (Scope 'EIGENE') lässt sie am
    eigenen Einsatz unterschreiben; ein fremder Bericht ist mit 404 abgeriegelt.
    """
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    _guard_own_report(report, actor, scope)
    signature = _dekodiere_signatur(payload.signature_png_base64)
    try:
        report = report_service.sign_report(
            actor,
            report_id=report_id,
            signed_by_name=payload.signed_by_name,
            signature_png=signature,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _out(report)


# --- Positionen (Migration 0080) -------------------------------------------

@router.put(
    "/site_reports/{report_id}/positionen",
    response=SiteReportLinesOut,
    auth=django_auth,
)
def set_site_report_lines(request, report_id: UUID, payload: SiteReportLinesIn):
    """Die Positionen eines Berichts **vollständig ersetzen** (nur im ENTWURF).

    Der Aufrufer schickt immer den ganzen Positionssatz (wie im Beleg-Editor); die
    Positionsnummern werden 1-basiert neu vergeben. **Preise gibt es hier nicht** —
    der Bericht führt Menge und Einheit, der Preis entsteht in der Rechnung.
    """
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    _guard_own_report(report, actor, scope)
    try:
        lines = report_service.set_report_lines(
            actor,
            report_id=report_id,
            lines=[l.dict() for l in payload.lines],
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    items = [_line_out(l) for l in lines]
    return SiteReportLinesOut(items=items, total=len(items))


@router.get(
    "/site_reports/{report_id}/vorbelegen-angebote",
    response=list[VorbelegbaresAngebotOut],
)
def vorbelegbare_angebote(request, report_id: UUID):
    """Die Angebote, aus denen dieser Bericht vorbelegt werden kann.

    Nur die Auswahlliste für `POST …/vorbelegen` — deshalb hängt sie am **selben**
    Recht wie die Aktion (`AENDERN`), nicht am bloßen Lesen: eine Nur-Lese-Rolle
    braucht die Angebotstitel des Auftrags hier nicht zu sehen. Fremder Bericht
    (Scope 'EIGENE') → 404. Bericht ohne Auftrag (freier Termin) → leere Liste.
    """
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    _guard_own_report(report, actor, scope)
    return [
        VorbelegbaresAngebotOut(
            id=q.id, quote_number=q.quote_number, title=q.title, status=q.status
        )
        for q in report_service.angebote_zur_vorbelegung(report_id)
    ]


@router.post(
    "/site_reports/{report_id}/vorbelegen",
    response=SiteReportLinesOut,
    auth=django_auth,
)
def vorbelegen_site_report(request, report_id: UUID, payload: VorbelegenIn):
    """Positionen aus einem Angebot des Auftrags als **Soll** übernehmen.

    Nur in einen leeren Bericht im ENTWURF, nur aus einem Angebot dieses Auftrags,
    nur die NORMAL-Positionen. Ist startet gleich dem Soll — der Monteur korrigiert
    nur die Abweichungen.
    """
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    _guard_own_report(report, actor, scope)
    try:
        lines = report_service.vorbelegen_aus_angebot(
            actor, report_id=report_id, quote_id=payload.quote_id
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    items = [_line_out(l) for l in lines]
    return SiteReportLinesOut(items=items, total=len(items))


# --- Soll-Ist-Abgleich am Auftrag ------------------------------------------

@router.get("/work_orders/{work_order_id}/soll-ist", response=SollIstOut)
def soll_ist_abgleich(request, work_order_id: UUID):
    """Angebots-Soll gegen Berichts-Ist über alle Berichte des Auftrags.

    Reine Rechenarbeit, **keine Geldbeträge**. Wie die Berichts-Auftragssicht ist
    das eine Dispositionssicht über die ganze Baustelle — sie lässt sich nicht auf
    eigene Zeilen begrenzen: Scope 'EIGENE' → 403 (fail-closed).
    """
    _actor, scope = require_scoped(request, "workflow", "LESEN")
    _auftragssicht_verboten(scope)
    if not WorkOrder.objects.filter(id=work_order_id).exists():
        raise HttpError(404, "Auftrag nicht gefunden.")
    try:
        ergebnis = report_service.soll_ist(work_order_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return SollIstOut(**ergebnis)
