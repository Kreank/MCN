"""Datei-API: Hochladen, Auflisten, Herunterladen, Verknüpfung lösen.

Rechte-Tore (Modul `content`):
  * Hochladen: `ANLEGEN`
  * Auflisten und Herunterladen: `LESEN`
  * Verknüpfung lösen: `AENDERN` (die Datei selbst bleibt bestehen)

**row_scope 'EIGENE' — echt umgesetzt, nicht mehr ignoriert.**
Eine Verknüpfung trägt ihr **Zielobjekt im Payload** (`site_report_id`,
`work_order_id`, `project_id` …). Damit ist sie genau der Fall, für den
`api/permissions.require_create` laut eigenem Docstring NICHT gedacht ist: Der
Erzeuger kann die Zeile einem fremden Elternobjekt zuordnen. Vorher konnte ein
Monteur ein Foto in den GoBD-relevanten Nachweis einer Baustelle einschleusen,
die er nie gesehen hat (Review-Befund).

Deshalb hier `require_scoped` + Ziel-Guard. Seit der **Objektsicht** (Migration
0099) hat die Grenze zwei Ausprägungen — und die Datei-API ist der Ort, an dem beide
zusammenkommen:

| Ziel | Grenze bei Scope 'EIGENE' |
|---|---|
| `service_job_id` | **meine Einsatzzuweisung** (`workflow.job_assignment`) |
| `site_report_id` | Bericht an einem solchen Einsatz |
| `property_id`, `unit_id`, `asset_id`, `work_order_id`, `service_case_id` | **mein Objekt** (`objektsicht`) — der Wartungsplan der Zentralanlage, das Datenblatt der Therme, das Foto vom Vorgang des Kollegen |
| `project_id`, `party_id`, `quote_id`, `invoice_id`, `article_id` | **403** — Projekt- und Kontaktakte, Belege und Artikelstamm sind nicht objektgebunden bzw. nicht seine Welt (fail-closed; `EIGENE` wird nie zu `ALLE` aufgeweitet) |

Der Download spiegelt exakt dieselbe Menge (`_datei_guard`): eine Datei ist für die
Objektsicht abrufbar, wenn **mindestens eine** ihrer Verknüpfungen in diese Liste
fällt — sonst 404. Ziel-Guard und Datei-Guard müssen deckungsgleich bleiben; liefe
der eine dem anderen davon, wäre entweder etwas hochladbar-aber-nicht-lesbar oder
lesbar-aber-nicht-hochladbar. Ein Test hält beide gegeneinander.

**Das Attest (`absence_id`) — besondere Kategorie, eigenes Tor (Migration 0072).**
Eine Arbeitsunfähigkeitsbescheinigung ist ein **Gesundheitsdatum** und damit eine
besondere Kategorie nach **DSGVO Art. 9**. Die Rechtsgrundlage ist Art. 9 Abs. 2
lit. b i. V. m. § 26 Abs. 3 BDSG (arbeitsrechtliche Pflichten: Entgeltfortzahlung
nach § 3 EFZG, Nachweis nach § 5 EFZG) — sie trägt die Verarbeitung **genau für
diesen Zweck und für niemanden sonst**.

Das `content`-Recht allein reicht dafür **nicht**. Die Disposition hat
`content/LESEN` mit Scope ALLE — sie darf Projektpläne und Baustellenfotos sehen,
aber selbstverständlich nicht die Krankschreibung eines Kollegen. Deshalb prüft
`_attest_guard` das Ziel `absence_id` **unabhängig vom content-Scope**:

  * **der Betroffene selbst** (die Abwesenheit hängt an seinem Personalsatz) —
    er lädt sein Attest hoch und sieht es; oder
  * die **Personalverwaltung**: `hr/LESEN` **und** `hr/AENDERN`, beide mit
    row_scope **ALLE** (Rechtematrix: ADMINISTRATION/GESCHAEFTSFUEHRUNG).

**Alles andere ist 404** — nicht 403: Die Existenz einer Abwesenheit (und damit
die Tatsache einer Krankmeldung) darf nicht einmal indirekt bestätigt werden. Der
Vorgesetzte ohne hr-Recht sieht nichts; die Disposition sieht nichts. Eine
Datei-Liste an einem anderen Ziel enthält Atteste nie, weil ein `file_link` an
genau einem Objekt hängt (`num_nonnulls = 1`).

**Der Dateiname wird verworfen.** `grippaler_infekt.pdf` verriete in jeder
Dateiliste die Diagnose. Der Anzeigename entsteht deshalb neutral aus dem
Zeitraum der Abwesenheit („Arbeitsunfaehigkeitsbescheinigung_2026-07-01_bis_
2026-07-05.pdf"); die Endung bleibt (sie bestimmt den MIME-Typ). Eine **Diagnose
wird nirgends gespeichert** — das System kennt nur „arbeitsunfähig von–bis".

Der Download läuft bewusst **durch die Anwendung** und nicht über eine
vorsignierte URL des Objektspeichers. Eine solche URL wäre nach dem Erzeugen für
jeden gültig, der sie besitzt — die Rechteprüfung liefe ins Leere, und die URL
landet in Browser-Verlauf, Proxy-Logs und Chatverläufen.

`Content-Disposition: attachment` erzwingt das Herunterladen statt der Anzeige im
Browser. Zusammen mit der Typ-Whitelist im Service (kein HTML, kein SVG)
verhindert das, dass hochgeladener Inhalt im Ursprung der Anwendung ausgeführt
wird.
"""
from pathlib import PurePosixPath
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

from api.permissions import check, require, require_scoped
from db_core.models import (
    Absence,
    FileLink,
    JobAssignment,
    ServiceCase,
    SiteReport,
    TechnicalAsset,
    Unit,
    WorkOrder,
)
from db_core.services import dateien as dateien_service
from db_core.services import objektsicht

router = Router()

# Fachliche Kategorie der Attest-Verknüpfung (Service-Whitelist).
ATTEST_KATEGORIE = "ATTEST"


# --- Attest ('absence_id') — DSGVO Art. 9, eigenes Tor ----------------------

def _personalverwaltung(request):
    """Darf dieses Konto Personalakten führen? (hr/LESEN **und** hr/AENDERN, ALLE)

    `check` ist fail-closed: bei row_scope EIGENE liefert es None. Ein Monteur
    fällt hier also durch — er kommt nur über den Eigentümer-Pfad an sein eigenes
    Attest.
    """
    return (
        check(request, "hr", "LESEN") == "ALLE"
        and check(request, "hr", "AENDERN") == "ALLE"
    )


def _attest_erlaubt(absence_id, actor, request):
    """Nur der Betroffene selbst oder die Personalverwaltung. Sonst: nein."""
    if absence_id is None:
        return False
    besitzer = (
        Absence.objects.filter(id=absence_id)
        .values_list("employee__app_user_id", flat=True)
        .first()
    )
    if besitzer is None:
        return False
    if besitzer == actor:
        return True
    return _personalverwaltung(request)


def _attest_guard(absence_id, actor, request):
    """404 für jeden, der weder betroffen noch Personalverwaltung ist.

    **404, nicht 403** — ein 403 bestätigte die Existenz der Abwesenheit und
    damit die Tatsache einer Krankmeldung.
    """
    if not _attest_erlaubt(absence_id, actor, request):
        raise HttpError(404, "Abwesenheit nicht gefunden.")


# --- Zeilenbegrenzung ('EIGENE') -------------------------------------------

def _eigener_job(job_id, actor):
    return JobAssignment.objects.filter(
        service_job_id=job_id, assignee_id=actor
    ).exists()


# Objektgebundene Zielarten (Objektsicht) → ORM-Pfad von der Zieltabelle zur
# Liegenschaft. **Eine** Tabelle, aus der sowohl `_ziel_guard` als auch
# `_datei_guard` ziehen — sonst liefen Upload- und Download-Grenze auseinander.
_OBJEKT_ZIELE = {
    "property_id": (None, "id"),
    "unit_id": (Unit, "property_id"),
    "asset_id": (TechnicalAsset, "property_id"),
    "work_order_id": (WorkOrder, "property_id"),
    "service_case_id": (ServiceCase, "property_id"),
}

# Fehlermeldung je objektgebundener Zielart (404 — die Existenz wird nicht verraten).
_OBJEKT_MELDUNG = {
    "property_id": "Liegenschaft nicht gefunden.",
    "unit_id": "Einheit nicht gefunden.",
    "asset_id": "Anlage nicht gefunden.",
    "work_order_id": "Auftrag nicht gefunden.",
    "service_case_id": "Vorgang nicht gefunden.",
}


def _objekt_der_zielart(art, wert):
    """Liegenschaft hinter einem objektgebundenen Ziel — `None`, wenn es nicht existiert."""
    model, feld = _OBJEKT_ZIELE[art]
    if model is None:  # property_id zeigt direkt auf die Liegenschaft
        return wert
    return model.objects.filter(id=wert).values_list(feld, flat=True).first()


def _ziel_guard(ziele: dict, actor, scope, request, *, schreibend=False):
    """Setzt die Grenzen auf dem Zielobjekt der Verknüpfung durch.

    Zwei Grenzen, unabhängig voneinander:

    1. **`absence_id` (Attest)** — geprüft **immer**, auch bei Scope 'ALLE':
       Gesundheitsdaten hängen nicht am content-Recht (sonst läse die Disposition
       mit). Siehe Modul-Docstring.
    2. **row_scope 'EIGENE'** — und hier ist **Lesen weiter als Schreiben**:

       * **Lesen** (`schreibend=False`, `GET /files`): eigener Einsatz, Berichte
         daran **und** die objektgebundenen Ziele an meinen Objekten
         (`_OBJEKT_ZIELE`) — das Datenblatt der Zentralanlage, das Foto vom Vorgang
         des Kollegen, der Wartungsplan am Objekt.
       * **Schreiben** (`schreibend=True`, `POST /files`): **nur** eigener Einsatz
         und Berichte daran — unverändert wie vor der Objektsicht. Ein Upload ist
         eine Aussage über eine Akte; „ich war an diesem Haus" ist kein Grund, in die
         Objektakte oder in den Auftrag der Kollegin hineinzuschreiben. Wer Fotos
         hinterlassen will, hängt sie an seinen Einsatz oder seinen Bericht — dort
         gehören sie hin, und dort sind sie am Objekt sichtbar.

       Jede andere Zielart (Projekt, Kontakt, Beleg, Artikel) ist 403 (fail-closed).
    """
    if len(ziele) != 1:
        if scope == "EIGENE" or "absence_id" in ziele:
            # Kein Guard kann hier greifen → nichts durchlassen. (Die
            # Genau-ein-Ziel-Regel selbst prüft ohnehin der Service, 422.)
            raise HttpError(422, "Eine Datei hängt an genau einem Objekt.")
        return
    art, wert = next(iter(ziele.items()))

    if art == "absence_id":
        _attest_guard(wert, actor, request)
        return

    if scope != "EIGENE":
        return
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
    if art in _OBJEKT_ZIELE and not schreibend:
        prop_id = _objekt_der_zielart(art, wert)
        if not objektsicht.ist_eigenes_objekt(actor, prop_id):
            raise HttpError(404, _OBJEKT_MELDUNG[art])
        return
    if art in _OBJEKT_ZIELE:
        raise HttpError(
            403,
            "Ihre Rolle erlaubt nur den Zugriff auf eigene Datensätze; hochladen "
            "können Sie an Ihrem Einsatz und an dessen Berichten.",
        )
    raise HttpError(
        403,
        "Ihre Rolle erlaubt nur den Zugriff auf eigene Datensätze; Dateien sind "
        "für Sie an Ihren Einsätzen, deren Berichten und an Ihren Objekten "
        "möglich.",
    )


def _datei_guard(file_id, actor, scope, request):
    """Download-Grenze — zwei Prüfungen.

    **1. Attest-Grenze (für JEDEN Scope) — fail-closed.** Trägt die Datei
    **mindestens eine** Attest-Verknüpfung, gilt die Attest-Grenze für die
    **ganze Datei**: Sie ist nur abrufbar, wenn der Abrufer für mindestens eine
    dieser Abwesenheiten befugt ist (Betroffener oder Personalverwaltung). Sonst
    404 — auch für die Disposition mit `content/LESEN` ALLE.

    Eine frühere Fassung verlangte, dass die Datei **ausschließlich** an
    Abwesenheiten hängt, mit dem Argument, ein zusätzlich anderswo verknüpfter
    Inhalt sei dort ohnehin abrufbar. Das war die falsche Richtung für einen
    Guard (Review-Befund A2): Es genügte, dass der Monteur sein Attest-PDF
    zusätzlich an seinen eigenen Einsatz hängt (`content/ANLEGEN` mit Scope
    EIGENE erlaubt ihm das) — und die Krankschreibung lag offen für die ganze
    Disposition. **Ein Guard fällt auf sicher, nicht auf offen.**

    Der zweite Riegel liegt im Service: ein Attest wird **nie dedupliziert** und
    nie auf ein bestehendes Objekt gelegt (`services/dateien.py`). Ein
    Gesundheitsdatum ist damit physisch nie dieselbe Datei wie ein
    Projektdokument — diese Prüfung hier ist der Gürtel zum Hosenträger.

    **2. row_scope 'EIGENE'**: mindestens eine Verknüpfung muss zeigen auf

      * einen **eigenen Einsatz** oder einen Bericht daran (Zuweisung), oder
      * eines meiner **Objekte** — direkt (`property_id`) oder über Einheit, Anlage,
        Auftrag, Vorgang (`_OBJEKT_ZIELE`), oder
      * eine **eigene Abwesenheit** (sonst sähe er sein eigenes Attest nicht).

    **Diese Menge ist exakt die Lesemenge von `_ziel_guard`** (`schreibend=False`).
    Die beiden dürfen nicht auseinanderlaufen: Was in einer Liste erscheint, muss
    herunterladbar sein — und was nicht erscheinen darf, darf auch nicht über eine
    geratene `file_id` fallen. Ein Test hält beide gegeneinander.

    Der **Upload** ist bewusst enger (nur eigener Einsatz/Bericht) — das ist kein
    Widerspruch, sondern die Asymmetrie „Lesen weiter als Schreiben".
    """
    attest_ids = list(
        FileLink.objects.filter(file_id=file_id, absence_id__isnull=False)
        .values_list("absence_id", flat=True)
    )
    if attest_ids:
        if not any(_attest_erlaubt(a, actor, request) for a in attest_ids):
            raise HttpError(404, "Datei nicht gefunden.")
        return

    if scope != "EIGENE":
        return
    eigene_jobs = JobAssignment.objects.filter(assignee_id=actor).values(
        "service_job_id"
    )
    objekte = objektsicht.eigene_property_ids(actor)
    sichtbar = FileLink.objects.filter(file_id=file_id).filter(
        Q(service_job_id__in=eigene_jobs)
        | Q(site_report__service_job_id__in=eigene_jobs)
        | Q(absence__employee__app_user_id=actor)
        # Objektsicht — dieselben fünf Zielarten wie in `_OBJEKT_ZIELE`.
        | Q(property_id__in=objekte)
        | Q(unit__property_id__in=objekte)
        | Q(work_order__property_id__in=objekte)
        | Q(service_case__property_id__in=objekte)
        # `file_link.asset_id` ist in der DB ein FK, im Model aber ein rohes
        # UUID-Feld (technical_asset war beim Bau von 0021 nicht abgebildet) —
        # deshalb hier eine Subquery statt eines Joins.
        | Q(
            asset_id__in=TechnicalAsset.objects.filter(
                property_id__in=objekte
            ).values("id")
        )
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
    # Attest an einer Abwesenheit — DSGVO Art. 9, eigener Guard (s. o.).
    absence_id: UUID | None = None


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


# --- Kategorien (Befund A4/A5, Migration 0127) -----------------------------

class KategorieOut(Schema):
    id: UUID
    code: str
    label: str
    is_system: bool
    status: str
    sort_order: int


class KategorieIn(Schema):
    """Neue Kategorie. `code` wird normalisiert (Großbuchstaben, Unterstriche).

    Fehlt er, wird er aus der Bezeichnung abgeleitet — „Baustellenbericht"
    ergibt `BAUSTELLENBERICHT`.
    """

    label: str
    code: str | None = None
    sort_order: int = 100


class KategoriePatch(Schema):
    """Nur Bezeichnung und Reihenfolge. Der **Code bleibt** (siehe Service)."""

    label: str | None = None
    sort_order: int | None = None


@router.get("/file-categories", response=list[KategorieOut])
def list_kategorien(
    request,
    nur_aktive: bool = Query(True),
    ohne_system: bool = Query(False),
):
    """Die gepflegte Kategorienliste.

    `ohne_system=true` für die Auswahl beim Hochladen: ARTIKELBILD, ATTEST,
    BELEG_PDF und E_RECHNUNG vergibt ausschließlich das Programm und sie
    gehören nicht in ein Auswahlfeld.
    """
    require(request, "content", "LESEN")
    return dateien_service.kategorien(nur_aktive=nur_aktive, ohne_system=ohne_system)


@router.post("/file-categories", response={201: KategorieOut}, auth=django_auth)
def kategorie_anlegen(request, payload: KategorieIn):
    """Eigene Kategorie anlegen (Befund A5).

    `content/AENDERN`, nicht ANLEGEN: Hier entsteht keine Datei, sondern eine
    Stammdatenzeile, die für alle gilt — das ist eine Verwaltungshandlung.
    """
    actor, _ = require(request, "content", "AENDERN")
    try:
        kategorie = dateien_service.kategorie_anlegen(
            actor,
            code=payload.code or payload.label,
            label=payload.label,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, kategorie)


@router.patch("/file-categories/{category_id}", response=KategorieOut, auth=django_auth)
def kategorie_aendern(request, category_id: UUID, payload: KategoriePatch):
    """Bezeichnung und Reihenfolge ändern — der Code bleibt unangetastet."""
    actor, _ = require(request, "content", "AENDERN")
    try:
        return dateien_service.kategorie_aendern(
            actor, category_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))


@router.post(
    "/file-categories/{category_id}/deaktivieren",
    response=KategorieOut,
    auth=django_auth,
)
def kategorie_deaktivieren(request, category_id: UUID):
    """Deaktivieren statt löschen (Befund A5).

    Alte Dateien tragen ihre Kategorie noch; eine gelöschte machte die Historie
    unlesbar — und der Fremdschlüssel ließe es ohnehin nicht zu.
    Systemkategorien wehrt schon der Service ab, die DB zusätzlich per Trigger.
    """
    actor, _ = require(request, "content", "AENDERN")
    try:
        return dateien_service.kategorie_status(actor, category_id, aktiv=False)
    except ValueError as exc:
        raise HttpError(422, str(exc))


@router.post(
    "/file-categories/{category_id}/aktivieren",
    response=KategorieOut,
    auth=django_auth,
)
def kategorie_aktivieren(request, category_id: UUID):
    """Eine deaktivierte Kategorie wieder in die Auswahl holen."""
    actor, _ = require(request, "content", "AENDERN")
    try:
        return dateien_service.kategorie_status(actor, category_id, aktiv=True)
    except ValueError as exc:
        raise HttpError(422, str(exc))


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

    **Ziel `absence_id` (Attest):** Der vom Client gelieferte Dateiname wird
    **verworfen** und durch einen neutralen ersetzt — er darf keine Diagnose
    tragen (DSGVO Art. 9 / Art. 5 Datenminimierung: gespeichert wird
    „arbeitsunfähig von–bis", nicht *warum*). Die Kategorie ist fest `ATTEST`;
    eine frei gewählte Kategorie brächte hier keinen Nutzen, aber die Gefahr,
    dass jemand die Datei mit einer sprechenden Kategorie versieht.
    """
    actor, scope = require_scoped(request, "content", "ANLEGEN")
    ziele = {k: v for k, v in ziel.dict().items() if v}
    # `schreibend=True`: Die Objektsicht ist eine LESE-Sicht. Hochladen darf die
    # Rolle mit Scope 'EIGENE' nur an ihrem Einsatz und dessen Berichten — sie
    # schreibt nicht in die Objektakte oder in den Auftrag der Kollegin.
    _ziel_guard(ziele, actor, scope, request, schreibend=True)

    dateiname = datei.name
    if ziele.get("absence_id"):
        dateiname = _attest_dateiname(ziele["absence_id"], datei.name)
        link_category = ATTEST_KATEGORIE
    elif link_category == ATTEST_KATEGORIE:
        # Eine „Attest"-Kategorie an einem Projekt/Auftrag wäre eine
        # Gesundheitsaussage am falschen Objekt — und stünde außerhalb des
        # Attest-Guards. Fail-closed.
        raise HttpError(
            422,
            "Die Kategorie ATTEST ist ausschließlich an einer Abwesenheit "
            "zulässig.",
        )

    try:
        _, link = dateien_service.datei_hochladen(
            actor,
            dateiname=dateiname,
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


def _attest_dateiname(absence_id, original):
    """Neutraler Anzeigename: „Arbeitsunfaehigkeitsbescheinigung_<von>_bis_<bis>.<ext>".

    Der Zeitraum steht ohnehin in der Abwesenheit — der Name verrät also nichts,
    was der Berechtigte nicht schon sieht. Was er NICHT verrät: die Diagnose. Ein
    Upload namens `burnout.pdf` würde sonst in jeder Ansicht mitgelesen, in der
    der Dateiname erscheint.
    """
    endung = PurePosixPath((original or "").lower()).suffix
    zeitraum = (
        Absence.objects.filter(id=absence_id)
        .values_list("start_date", "end_date")
        .first()
    )
    if zeitraum is None:  # pragma: no cover — der Guard hat sie bereits geprüft
        raise HttpError(404, "Abwesenheit nicht gefunden.")
    von, bis = zeitraum
    return f"Arbeitsunfaehigkeitsbescheinigung_{von}_bis_{bis}{endung}"


@router.get("/files", response=DateiListeOut)
def dateien_auflisten(request, ziel: ZielFilter = Query(...)):
    """Alle Dateien an einem Zielobjekt (Projekt, Liegenschaft, Kontakt …).

    Atteste erscheinen **nur** in der Liste zu genau ihrer `absence_id` — und die
    ist durch den Attest-Guard gedeckt. In keiner anderen Dateiliste taucht ein
    Attest auf, weil eine Verknüpfung an genau einem Objekt hängt.
    """
    actor, scope = require_scoped(request, "content", "LESEN")
    ziele = {k: v for k, v in ziel.dict().items() if v}
    _ziel_guard(ziele, actor, scope, request)
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
    _datei_guard(file_id, actor, scope, request)
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

    **Attest:** Das Lösen verlangt zusätzlich die Attest-Befugnis (Betroffener
    oder Personalverwaltung), sonst 404 — `content/AENDERN` allein (Disposition!)
    reicht nicht. Praktisch heißt das: **nur die Personalverwaltung** kann ein
    Attest lösen. Der Beschäftigte kommt über `require` (Scope EIGENE → 403) gar
    nicht hierher, und das ist richtig so: Die Bescheinigung ist der Nachweis
    seines Anspruchs auf Entgeltfortzahlung (§ 5 EFZG) und liegt in der
    Aufbewahrung des Arbeitgebers. Sie einseitig wieder zurückziehen zu können,
    entwertete den Nachweis. Das Entfernen nach Ablauf der Aufbewahrungsfrist ist
    genau diese Bürotätigkeit — und die Datei selbst bleibt in `content.file`
    (echtes Löschen ist Sache des Löschkonzepts, Beschluss C-07/C-08).
    """
    actor, _ = require(request, "content", "AENDERN")
    absence_id = (
        FileLink.objects.filter(id=link_id)
        .values_list("absence_id", flat=True)
        .first()
    )
    if absence_id is not None:
        _attest_guard(absence_id, actor, request)
    try:
        dateien_service.verknuepfung_loesen(actor, link_id=link_id)
    except dateien_service.VerknuepfungGesperrt as exc:
        raise HttpError(422, str(exc))
    except dateien_service.DateiFehler as exc:
        raise HttpError(404, str(exc))
    return Status(204, None)
