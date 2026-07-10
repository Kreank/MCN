"""Datei-API: Hochladen, Auflisten, Herunterladen, Verknüpfung lösen.

Rechte-Tore (Modul `content`):
  * Hochladen: `ANLEGEN`
  * Auflisten und Herunterladen: `LESEN`
  * Verknüpfung lösen: `AENDERN` (die Datei selbst bleibt bestehen)

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

from django.http import HttpResponse
from ninja import File as NinjaFile
from ninja import Form, Query, Router, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_create
from db_core.services import dateien as dateien_service

router = Router()


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
    actor = require_create(request, "content", "ANLEGEN")
    ziele = {k: v for k, v in ziel.dict().items() if v}
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
    require(request, "content", "LESEN")
    ziele = {k: v for k, v in ziel.dict().items() if v}
    try:
        links = dateien_service.dateien_am_ziel(**ziele)
    except dateien_service.DateiFehler as exc:
        raise HttpError(422, str(exc))
    items = [_out(l) for l in links]
    return DateiListeOut(items=items, total=len(items))


@router.get("/files/{file_id}/download")
def datei_herunterladen(request, file_id: UUID):
    """Liefert den Dateiinhalt aus — durch die Anwendung, nicht per Direkt-URL."""
    require(request, "content", "LESEN")
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
    """Entfernt die Verknüpfung. Die Datei selbst bleibt (unveränderlich)."""
    actor, _ = require(request, "content", "AENDERN")
    try:
        dateien_service.verknuepfung_loesen(actor, link_id=link_id)
    except dateien_service.DateiFehler as exc:
        raise HttpError(404, str(exc))
    return Status(204, None)
