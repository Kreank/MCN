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

**row_scope 'EIGENE' — zwei verschiedene Grenzen, und das ist der Kern dieses Moduls.**

Seit der Objektsicht (Migration 0099) gilt hier NICHT mehr eine Grenze, sondern zwei
— je nachdem, ob gelesen oder geschrieben wird:

| Was | Grenze | Warum |
|---|---|---|
| **LESEN** (Bericht, Berichtsliste, Soll-Ist) | **mein Objekt** | „Zwei Tage vorher hat bei einem anderen Mieter der Heizkörper geleckt" — der Bericht des Kollegen an *diesem Haus* ist genau die Information, für die dieser Slice gebaut wurde. Sie ihm vorzuenthalten war der Fehler. |
| **SCHREIBEN** (anlegen, ändern, unterschreiben, Positionen, vorbelegen) | **mein Einsatz** (`workflow.job_assignment`) | Ein Baustellenbericht ist ein **Nachweis**, der unterschrieben und versiegelt wird. Wer ihn schreibt, behauptet, dort gewesen zu sein. Objektkenntnis ist kein Recht, im Namen einer Kollegin zu quittieren. |

Vorher warf `_auftragssicht_verboten` bei Scope 'EIGENE' **403** auf jede
Auftragssicht — das war genau die Sperre, die dem Monteur die Berichte der Kollegen
vorenthielt. Sie ist durch `_guard_auftragssicht` ersetzt: dieselbe Sicht, aber auf
meine Objekte begrenzt (fremd → **404**, die Existenz wird nicht verraten).

Ein Bericht **ohne Einsatzbezug** (reiner Auftragsbericht) ist damit für die
Objektsicht **lesbar** (er hängt am Auftrag, der an meinem Objekt hängt) — aber
weiterhin **nicht änderbar**: `_guard_own_report` verlangt zum Schreiben die
Einsatzzuweisung.

**Positionen und Soll-Ist (Migration 0080).** Der Bericht führt Positionen aus dem
Artikel-/Leistungsstamm — **ohne Preise** (ein unterschriebener Bericht mit Preisen
wäre eine Preisvereinbarung; der Preis entsteht erst in der Rechnung). Auch der
Soll-Ist weist **keine Geldbeträge** aus — deshalb ist er für die Objektsicht lesbar.
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

from django.http import HttpResponse

from api.auftrag import guard_auftrag
from api.objektgrenze import guard_objekt
from api.permissions import require_scoped
from db_core.models import JobAssignment, ServiceJob, WorkOrder
from db_core.services import site_report as report_service
from db_core.services import site_report_pdf as report_pdf_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class SiteReportKopfOut(Schema):
    """Briefkopf des Berichts (Befund B3/B8) — „das übliche Briefkopf-Gedöns".

    Alle Felder optional: Ein Bericht am **freien Termin** hat keinen Auftrag
    und damit weder Auftraggeber noch Auftragsnummer; ein Auftrag am
    Gemeinschaftseigentum hat keine Einheit und damit keinen Mieter. Leer
    heißt „gibt es nicht", nicht „nicht geladen".
    """

    order_number: str | None = None
    order_title: str | None = None
    auftraggeber: str | None = None
    auftraggeber_adresse: str | None = None
    objekt_name: str | None = None
    objekt_nummer: str | None = None
    objekt_adresse: str | None = None
    gebaeude: str | None = None
    einheit: str | None = None
    etage: str | None = None
    # Mehrere sind der Normalfall (Ehepaar = zwei Beteiligte).
    mieter: list[str] = []
    eigentuemer: list[str] = []
    # Fertige Anschriftblöcke für die Blatt-Darstellung (Befund B1/B2): dieselbe
    # Form wie beim Beleg, aus denselben Funktionen. Der Bericht ist ein
    # Dokument des Hauses und trägt denselben Kopf wie Angebot und Rechnung.
    aussteller: list[str] = []
    empfaenger: list[str] = []


class SiteReportOut(Schema):
    id: UUID
    # Der Briefkopf. Bisher kannte der Bericht seinen Auftrag nur als UUID —
    # weder Auftraggeber noch Adresse, Mieter, Wohnung oder Auftragsnummer
    # waren über die API erreichbar (Befund B8).
    kopf: SiteReportKopfOut | None = None
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

def _out(report, *, mit_kopf=False):
    """Bericht als Ausgabeschema.

    `mit_kopf` nur im Detail: Der Briefkopf kostet je Bericht mehrere Abfragen
    (Auftraggeber, Adresse, Belegung, Eigentümer). In einer Liste mit dreißig
    Berichten wäre das ein N+1 für Angaben, die dort niemand liest.
    """
    return SiteReportOut(
        id=report.id,
        kopf=(
            SiteReportKopfOut(**report_service.kopfdaten(report))
            if mit_kopf
            else None
        ),
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
    # Nur hier der Briefkopf — in der Liste wäre er ein N+1 (siehe `_out`).
    return SiteReportDetailOut(
        **_out(report, mit_kopf=True).dict(),
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

def _eigener_job(job_id, actor):
    """Bin ich diesem Einsatz zugewiesen? (`workflow.job_assignment`)"""
    return JobAssignment.objects.filter(
        service_job_id=job_id, assignee_id=actor
    ).exists()


def _guard_own_job(job_id, actor, scope):
    """**SCHREIB**-Grenze: Scope 'EIGENE' → nur ein Einsatz, dem der Akteur
    zugewiesen ist. Sonst 404. Muster: `api/planung.py::_guard_own_job`.

    Fürs **Lesen** gilt die weitere Grenze `_guard_job_lesen` (Zuweisung ODER Objekt).
    """
    if scope != "EIGENE":
        return
    if not _eigener_job(job_id, actor):
        raise HttpError(404, "Einsatz nicht gefunden.")


def _guard_own_report(report, actor, scope):
    """**SCHREIB**-Grenze: Scope 'EIGENE' → nur Berichte an einem Einsatz, dem der
    Akteur zugewiesen ist. Ein reiner Auftragsbericht (ohne Einsatz) ist für ihn
    **nicht schreibbar** — er hat keine Zuweisung, an der das Recht hängen könnte.
    404 statt 403.

    **Nicht** für das Lesen verwenden: Dafür gilt `_guard_report_lesen` (mein
    Objekt). Wer diese Funktion versehentlich in einen Lesepfad zurückschreibt, nimmt
    dem Monteur genau die Berichte der Kollegen wieder weg, für die dieser Slice
    gebaut wurde.
    """
    if scope != "EIGENE":
        return
    if report.service_job_id is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    if not JobAssignment.objects.filter(
        service_job_id=report.service_job_id, assignee_id=actor
    ).exists():
        raise HttpError(404, "Bericht nicht gefunden.")


def _report_property_id(report):
    """Die Liegenschaft, an der der Bericht hängt — über Einsatz ODER Auftrag.

    Dieselbe Coalesce-Kette wie überall (freier Termin trägt sie selbst, der
    auftragsgebundene Einsatz oft nur über seinen Auftrag). `None` heißt: an keiner —
    dann fällt der Guard auf 404.
    """
    if report.work_order_id is not None:
        return (
            WorkOrder.objects.filter(id=report.work_order_id)
            .values_list("property_id", flat=True)
            .first()
        )
    if report.service_job_id is not None:
        job = (
            ServiceJob.objects.filter(id=report.service_job_id)
            .values("property_id", "work_order__property_id")
            .first()
        )
        if job is not None:
            return job["property_id"] or job["work_order__property_id"]
    return None


def _guard_job_lesen(job_id, actor, scope):
    """**LESE**-Grenze am Einsatz: eigene Zuweisung **ODER** mein Objekt.

    Die Objektsicht **erweitert** die alte Grenze, sie ersetzt sie nicht. Das ist
    keine Feinheit, sondern ein realer Bruchfall: Ein **freier Termin** (Begehung
    ohne Auftrag, Migration 0062) darf `property_id` NULL tragen — er hängt dann an
    **gar keinem** Objekt. Prüfte man nur das Objekt, verlöre der Monteur den Bericht
    an seinem **eigenen** freien Termin (404), obwohl er ihn selbst geschrieben hat.
    (Genau das ist beim ersten Anlauf dieses Slices passiert.)
    """
    if scope != "EIGENE":
        return
    if _eigener_job(job_id, actor):
        return
    job = (
        ServiceJob.objects.filter(id=job_id)
        .values("property_id", "work_order__property_id")
        .first()
    )
    prop_id = (
        (job["property_id"] or job["work_order__property_id"])
        if job is not None
        else None
    )
    guard_objekt(scope, actor, prop_id, "Einsatz nicht gefunden.")


def _guard_report_lesen(report, actor, scope):
    """**LESE**-Grenze am Bericht: eigener Einsatz **ODER** mein Objekt.

    Der Bericht des Kollegen an meinem Objekt ist der Zweck dieses Slices: Wer heute
    zur „Heizkörper kalt"-Meldung fährt, muss den Bericht von vorgestern lesen können,
    in dem steht, dass am Nachbar-Heizkörper ein Leck war. Der Bericht am **eigenen
    freien Termin** (ohne Objekt) bleibt daneben lesbar — siehe `_guard_job_lesen`.
    """
    if scope != "EIGENE":
        return
    if report.service_job_id is not None and _eigener_job(
        report.service_job_id, actor
    ):
        return
    guard_objekt(
        scope, actor, _report_property_id(report), "Bericht nicht gefunden."
    )


def _guard_auftragssicht(work_order_id, actor, scope):
    """Auftragssicht (alle Berichte / Soll-Ist einer Baustelle): mein Objekt, sonst 404.

    Ersetzt das frühere pauschale 403. Die Sicht **lässt** sich sehr wohl begrenzen —
    nur eben am Objekt, nicht an der Zuweisung. Ein Auftrag trägt IMMER eine
    Liegenschaft (`work_order.property_id` ist NOT NULL) — hier gibt es die
    NULL-Falle des freien Termins also nicht.
    """
    guard_auftrag(work_order_id, actor, scope)


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

    Scope 'EIGENE' (Objektsicht):

      * `?work_order_id=…` → **alle** Berichte dieses Auftrags, sofern er an einem
        meiner Objekte hängt (auch die der Kollegen). Fremder Auftrag → 404.
      * `?service_job_id=…` → die Berichte dieses Einsatzes, wenn er **mir zugewiesen
        ist ODER an einem meiner Objekte hängt**. Beides, nicht nur das Zweite: Der
        **freie Termin** darf ohne Liegenschaft existieren (0062) — prüfte man nur das
        Objekt, verlöre der Monteur den Bericht an seinem eigenen freien Termin.
        Weder das eine noch das andere → 404.
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
        _guard_job_lesen(service_job_id, actor, scope)
    else:
        if not WorkOrder.objects.filter(id=work_order_id).exists():
            raise HttpError(404, "Auftrag nicht gefunden.")
        _guard_auftragssicht(work_order_id, actor, scope)
    reports = report_service.list_reports(
        work_order_id=work_order_id, service_job_id=service_job_id
    )
    items = [_out(r) for r in reports]
    return SiteReportListOut(items=items, total=len(items))


@router.get("/site_reports/{report_id}", response=SiteReportDetailOut)
def get_site_report(request, report_id: UUID):
    """Ein Baustellenbericht im Detail — **mit seinen Positionen**.

    Scope 'EIGENE': jeder Bericht an einem meiner Objekte — auch der eines Kollegen.
    Bericht an einem fremden Objekt → 404.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    _guard_report_lesen(report, actor, scope)
    return _detail_out(report)


@router.get("/site_reports/{report_id}/pdf")
def site_report_pdf(request, report_id: UUID):
    """Bericht-PDF (Markenlayout) — on-the-fly, keine Archivierung.

    ENTWURF trägt einen deutlichen ENTWURF-Aufdruck; ein unterzeichneter
    Bericht zeigt den Unterschriftsblock. Zugriffsgrenze wie das Detail
    (`_guard_report_lesen`): Scope 'EIGENE' sieht Berichte an eigenen
    Objekten, fremde → 404."""
    actor, scope = require_scoped(request, "workflow", "LESEN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    _guard_report_lesen(report, actor, scope)
    pdf = report_pdf_service.render_site_report_pdf(report_id)
    if pdf is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="baustellenbericht-{report.report_date}.pdf"'
    )
    return response


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

    Reine Rechenarbeit, **keine Geldbeträge** — deshalb für die Objektsicht lesbar:
    Scope 'EIGENE' → begrenzt auf meine Objekte (fremder Auftrag: 404). Der Soll-Ist
    sagt dem Monteur, was am Auftrag geplant war und was tatsächlich verbaut wurde;
    **Preise stehen nicht darin** — er zieht Mengen aus dem Angebot, keine Beträge.
    Seit Migration 0102 ist das keine Nebenbemerkung mehr, sondern die Regel des
    Hauses: Der Monteur liest das Angebot (`GET /invoicing/quotes/{id}/mengen`), aber
    nie einen Betrag. Wer hier eine Geldspalte ergänzt, öffnet sie ihm.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    if not WorkOrder.objects.filter(id=work_order_id).exists():
        raise HttpError(404, "Auftrag nicht gefunden.")
    _guard_auftragssicht(work_order_id, actor, scope)
    try:
        ergebnis = report_service.soll_ist(work_order_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return SollIstOut(**ergebnis)
