"""Baustellenbericht-API (workflow.site_report).

Berichte hängen an einem Auftrag (`work_order`); Fotos werden über die Datei-API
(`/content/files` mit `site_report_id`) angehängt. Die Kundenunterschrift wird als
Base64-PNG entgegengenommen, im Objektspeicher abgelegt und besiegelt den Bericht
(ENTWURF → UNTERZEICHNET); danach ist er unveränderlich.

Rechte-Tore (Modul `workflow`, wie beim Auftrag, zu dem der Bericht gehört):
  * Lesen:      `LESEN`
  * Anlegen:    `ANLEGEN`
  * Ändern:     `AENDERN`
  * Unterschreiben (Abnahme): `AENDERN`
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

from api.permissions import require
from db_core.models import WorkOrder
from db_core.services import site_report as report_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class SiteReportOut(Schema):
    id: UUID
    work_order_id: UUID
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


class SiteReportIn(Schema):
    work_order_id: UUID
    report_date: date
    activity_text: str
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


def _dekodiere_signatur(base64_wert: str) -> bytes:
    roh = (base64_wert or "").strip()
    if roh.startswith("data:"):
        # data:image/png;base64,<...>
        _, _, roh = roh.partition(",")
    try:
        return base64.b64decode(roh, validate=True)
    except (binascii.Error, ValueError):
        raise HttpError(422, "Die Unterschrift ist kein gültiges Base64-PNG.")


# --- Endpoints -------------------------------------------------------------

@router.get("/site_reports", response=SiteReportListOut)
def list_site_reports(request, work_order_id: UUID | None = None):
    """Baustellenberichte eines Auftrags (neueste zuerst)."""
    # Rechteprüfung VOR der Parametervalidierung: `work_order_id` ist bewusst
    # optional, damit ein rollenloser Aufruf 403 (nicht 422) bekommt und die
    # Existenz des Auftrags nicht durchsickert.
    require(request, "workflow", "LESEN")
    if work_order_id is None:
        raise HttpError(422, "work_order_id ist erforderlich.")
    if not WorkOrder.objects.filter(id=work_order_id).exists():
        raise HttpError(404, "Auftrag nicht gefunden.")
    reports = report_service.list_reports(work_order_id)
    items = [_out(r) for r in reports]
    return SiteReportListOut(items=items, total=len(items))


@router.get("/site_reports/{report_id}", response=SiteReportOut)
def get_site_report(request, report_id: UUID):
    """Ein Baustellenbericht im Detail."""
    require(request, "workflow", "LESEN")
    report = report_service.get_report(report_id)
    if report is None:
        raise HttpError(404, "Bericht nicht gefunden.")
    return _out(report)


@router.post("/site_reports", response={201: SiteReportOut}, auth=django_auth)
def create_site_report(request, payload: SiteReportIn):
    """Neuen Baustellenbericht (Status ENTWURF) anlegen."""
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        report = report_service.create_report(
            actor,
            work_order_id=payload.work_order_id,
            report_date=payload.report_date,
            activity_text=payload.activity_text,
            service_job_id=payload.service_job_id,
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
    actor, _ = require(request, "workflow", "AENDERN")
    fields = payload.dict(exclude_unset=True)
    try:
        report = report_service.update_report(actor, report_id=report_id, **fields)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _out(report)


@router.post("/site_reports/{report_id}/sign", response=SiteReportOut, auth=django_auth)
def sign_site_report(request, report_id: UUID, payload: SiteReportSignIn):
    """Bericht mit der Kundenunterschrift besiegeln (ENTWURF → UNTERZEICHNET)."""
    actor, _ = require(request, "workflow", "AENDERN")
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
