"""Datei-API: Hochladen, Auflisten, Herunterladen, Verknüpfung lösen.

Rechte-Tore (Modul `content`):
  * Hochladen: `ANLEGEN`
  * Auflisten und Herunterladen: `LESEN`
  * Verknüpfung lösen: `AENDERN` (die Datei selbst bleibt bestehen)

**row_scope 'EIGENE' (Monteur) — echt umgesetzt, nicht mehr ignoriert.**
Eine Verknüpfung trägt ihr **Zielobjekt im Payload** (`site_report_id`,
`work_order_id`, `project_id` …). Damit ist sie genau der Fall, für den
`api/permissions.require_create` laut eigenem Docstring NICHT gedacht ist: Der
Erzeuger kann die Zeile einem fremden Elternobjekt zuordnen. Vorher konnte ein
Monteur ein Foto in den GoBD-relevanten Nachweis einer Baustelle einschleusen,
die er nie gesehen hat (Review-Befund).

Deshalb hier `require_scoped` + Ziel-Guard. Der Monteur hat genau eine Grenze —
die **Einsatzzuweisung** (`workflow.job_assignment`), wie überall sonst auch:

  * `service_job_id`: nur ein Einsatz, dem er zugewiesen ist → sonst **404**.
  * `site_report_id`: nur ein Bericht an einem solchen Einsatz → sonst **404**.
  * jedes andere Ziel (Projekt, Auftrag, Kontakt, Beleg …): **403** — für diese
    Objekte gibt es keine „eigene Zeile", an der die Sicht hängen könnte
    (fail-closed; `EIGENE` wird nie zu `ALLE` aufgeweitet).

Der Download ist an dieselbe Grenze gebunden: eine Datei ist für den Monteur nur
abrufbar, wenn **mindestens eine** ihrer Verknüpfungen auf einen eigenen Einsatz
oder einen Bericht daran zeigt — sonst 404.

Der Download läuft bewusst **durch die Anwendung** und nicht über eine
vorsignierte URL des Objektspeichers. Eine solche URL wäre nach dem Erzeugen für
jeden gültig, der sie besitzt — die Rechteprüfung liefe ins Leere, und die URL
landet in Browser-Verlauf, Proxy-Logs und Chatverläufen.

`Content-Disposition: attachment` erzwingt das Herunterladen statt der Anzeige im
Browser. Zusammen mit der Typ-Whitelist im Service (kein HTML, kein SVG)
verhindert das, dass hochgeladener Inhalt im Ursprung der Anwendung ausgeführt
wird.
"""
from urllib.parse import quote
from uuid import UUID

from django.db.models import Q
from django.http import HttpResponse
from ninja import File as NinjaFile
from ninja import Form, Query, Router, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_scoped
from db_core.models import FileLink, JobAssignment, SiteReport
from db_core.services import dateien as dateien_service

router = Router()


# --- Zeilenbegrenzung ('EIGENE') -------------------------------------------

def _eigener_job(job_id, actor):
    return JobAssignment.objects.filter(
        service_job_id=job_id, assignee_id=actor
    ).exists()


def _ziel_guard(ziele: dict, actor, scope):
    """Setzt die 'EIGENE'-Grenze auf dem Zielobjekt der Verknüpfung durch.

    `ziele` ist das bereits auf gesetzte Werte reduzierte Ziel-Dict. Bei Scope
    'ALLE' passiert nichts. Bei 'EIGENE' sind nur der eigene Einsatz und Berichte
    daran zulässig (404 bei fremden, 403 bei nicht scopebaren Zielarten).
    """
    if scope != "EIGENE":
        return
    if len(ziele) != 1:
        # Genau-ein-Ziel prüft der Service (422). Hier nichts durchlassen.
        raise HttpError(422, "Eine Datei hängt an genau einem Objekt.")
    art, wert = next(iter(ziele.items()))
    if art == "service_job_id":
        if not _eigener_job(wert, actor):
            raise HttpError(404, "Einsatz nicht gefunden.")
        return
    if art == "site_report_id":
        report = SiteReport.objects.filter(id=wert).only(
            "id", "service_job_id"
        ).first()
        if report is None or report.service_job_id is None:
            raise HttpError(404, "Bericht nicht gefunden.")
        if not _eigener_job(report.service_job_id, actor):
            raise HttpError(404, "Bericht nicht gefunden.")
        return
    raise HttpError(
        403,
        "Ihre Rolle erlaubt nur den Zugriff auf eigene Datensätze; Dateien sind "
        "für Sie am eigenen Einsatz und an dessen Berichten möglich.",
    )


def _datei_guard(file_id, actor, scope):
    """Download-Grenze: mindestens eine Verknüpfung der Datei muss auf einen
    eigenen Einsatz (oder einen Bericht daran) zeigen. Sonst 404."""
    if scope != "EIGENE":
        return
    eigene_jobs = JobAssignment.objects.filter(assignee_id=actor).values(
        "service_job_id"
    )
    sichtbar = FileLink.objects.filter(file_id=file_id).filter(
        Q(service_job_id__in=eigene_jobs)
        | Q(site_report__service_job_id__in=eigene_jobs)
    ).exists()
    if not sichtbar:
        raise HttpError(404, "Datei nicht gefunden.")


class DateiOut(Schema):
    file_id: UUID
    link_id: UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    link_category: str | None = None
    uploaded_at: str
    uploaded_by: str | None = None


class DateiListeOut(Schema):
    items: list[DateiOut]
    total: int


class ZielFilter(Schema):
    """Genau eines dieser Felder wird gesetzt — wie in der Datenbank."""
    project_id: UUID | None = None
    property_id: UUID | None = None
    unit_id: UUID | None = None
    asset_id: UUID | None = None
    party_id: UUID | None = None
    service_case_id: UUID | None = None
    work_order_id: UUID | None = None
    service_job_id: UUID | None = None
    quote_id: UUID | None = None
    invoice_id: UUID | None = None
    article_id: UUID | None = None
    site_report_id: UUID | None = None


def _out(link):
    datei = link.file
    return DateiOut(
        file_id=datei.id,
        link_id=link.id,
        original_filename=datei.original_filename,
        mime_type=datei.mime_type,
        size_bytes=datei.size_bytes,
        link_category=link.link_category,
        uploaded_at=datei.uploaded_at.isoformat(),
        uploaded_by=(
            link.created_by.display_name if link.created_by_id else None
        ),
    )


@router.post("/files", response={201: DateiOut}, auth=django_auth)
def datei_hochladen(
    request,
    datei: UploadedFile = NinjaFile(...),
    ziel: ZielFilter = Form(...),
    link_category: str = Form("DOKUMENT"),
):
    """Lädt eine Datei hoch und hängt sie an genau ein Objekt.

    Der Dateiname ist reine Anzeige; der Speicherort entsteht aus einer UUID.
    Der Dateityp wird aus der Endung gegen eine Whitelist geprüft, nicht aus dem
    vom Browser gemeldeten Content-Type übernommen.
    """
    actor, scope = require_scoped(request, "content", "ANLEGEN")
    ziele = {k: v for k, v in ziel.dict().items() if v}
    _ziel_guard(ziele, actor, scope)
    try:
        _, link = dateien_service.datei_hochladen(
            actor,
            dateiname=datei.name,
            inhalt=datei.read(),
            link_category=link_category,
            **ziele,
        )
    except dateien_service.DateiFehler as exc:
        raise HttpError(422, str(exc))
    link = dateien_service.FileLink.objects.select_related("file", "created_by").get(
        id=link.id
    )
    return Status(201, _out(link))


@router.get("/files", response=DateiListeOut)
def dateien_auflisten(request, ziel: ZielFilter = Query(...)):
    """Alle Dateien an einem Zielobjekt (Projekt, Liegenschaft, Kontakt …)."""
    actor, scope = require_scoped(request, "content", "LESEN")
    ziele = {k: v for k, v in ziel.dict().items() if v}
    _ziel_guard(ziele, actor, scope)
    try:
        links = dateien_service.dateien_am_ziel(**ziele)
    except dateien_service.DateiFehler as exc:
        raise HttpError(422, str(exc))
    items = [_out(l) for l in links]
    return DateiListeOut(items=items, total=len(items))


@router.get("/files/{file_id}/download")
def datei_herunterladen(request, file_id: UUID):
    """Liefert den Dateiinhalt aus — durch die Anwendung, nicht per Direkt-URL."""
    actor, scope = require_scoped(request, "content", "LESEN")
    _datei_guard(file_id, actor, scope)
    try:
        datei, inhalt = dateien_service.datei_inhalt(file_id)
    except dateien_service.DateiFehler as exc:
        raise HttpError(404, str(exc))
    antwort = HttpResponse(inhalt, content_type=datei.mime_type)
    # attachment: der Inhalt wird nie im Ursprung der Anwendung gerendert.
    antwort["Content-Disposition"] = (
        f"attachment; {_dateiname_kopfteil(datei.original_filename)}"
    )
    antwort["X-Content-Type-Options"] = "nosniff"
    return antwort


def _dateiname_kopfteil(dateiname: str) -> str:
    """Baut den `filename`-Teil von Content-Disposition (RFC 6266/5987).

    HTTP-Kopfzeilen sind latin-1: ein Dateiname mit Emoji, Euro-Zeichen oder
    kyrillischer Schrift ließe `HttpResponse` beim Setzen der Kopfzeile werfen —
    hochladen ginge, herunterladen quittierte mit 500. Anführungszeichen und
    Backslashes müssen zudem escapt werden, sonst bricht der Name aus dem
    Quoting aus. Gleiches Vorgehen wie `django.http.FileResponse.set_headers`.
    """
    try:
        dateiname.encode("ascii")
    except UnicodeEncodeError:
        return "filename*=utf-8''{}".format(quote(dateiname))
    escaped = dateiname.replace("\\", "\\\\").replace('"', '\\"')
    return f'filename="{escaped}"'


@router.delete("/links/{link_id}", response={204: None}, auth=django_auth)
def verknuepfung_loesen(request, link_id: UUID):
    """Entfernt die Verknüpfung. Die Datei selbst bleibt (unveränderlich).

    `require` (fail-closed): Das Lösen ist Dispositions-/Bürotätigkeit; Scope
    'EIGENE' → 403. Der Monteur hat ohnehin kein `content/AENDERN`. Am
    **unterzeichneten** Baustellenbericht verbietet die DB das Lösen (0065) — das
    kommt als 422 zurück, nicht als 404.
    """
    actor, _ = require(request, "content", "AENDERN")
    try:
        dateien_service.verknuepfung_loesen(actor, link_id=link_id)
    except dateien_service.VerknuepfungGesperrt as exc:
        raise HttpError(422, str(exc))
    except dateien_service.DateiFehler as exc:
        raise HttpError(404, str(exc))
    return Status(204, None)
