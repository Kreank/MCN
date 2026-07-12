"""Zeiterfassung-API — Stempeluhr, eigene Tage, Verwaltung, Export, Kategorien.

Rechte (Modul `hr`, Rechtematrix 0021/0068)
-------------------------------------------
Zeiten sind Personendaten (DSGVO). **Kein Monteur sieht fremde Zeiten.**

| Endpunkt | Tor | Wirkung bei row_scope EIGENE |
|---|---|---|
| Stempeluhr (`/stempel/*`, `/aktuell`) | `require_scoped` | wirkt **immer** nur auf den Akteur — die API nimmt gar keine fremde user_id entgegen |
| `/meine-tage`, `/tage/{id}` (eigener) | `require_scoped` | Filter auf den Akteur; fremder Tag → 404 |
| `/tage/{id}/einreichen` | `require_scoped` | nur der eigene Tag (auch für ALLE — Einreichen ist die Handlung des Beschäftigten) |
| `/zeiterfassung` (Verwaltung) | `require` | **403** (fail-closed) |
| `/tage/{id}/bestaetigen|ablehnen` | `require` + `hr/FREIGEBEN` | 403; zusätzlich Vier-Augen im DB-Trigger |
| `/eintraege` (CRUD) | `require_scoped` | nur eigene Einträge; `user_id` wird auf den Akteur gezwungen |
| `/stundenliste.csv` | `require` + `hr/EXPORTIEREN` | 403 |
| `/hr/zeitkategorien` (Pflege) | `require` | 403 |

MONTEUR erhält mit 0068 `hr/LESEN` und `hr/AENDERN` mit row_scope EIGENE. Alle
`require`-gesicherten `hr`-Endpunkte (Personalliste, Abwesenheiten aller,
Verträge) bleiben für ihn damit auf 403 — fail-closed, wie das Repo es verlangt.

Fremde Zeilen antworten mit **404**, nicht 403 (ihre Existenz wird nicht
verraten). Recht → 403; fachliches Tor (Statusautomat, Arbeitstag-Schloss,
B-28) → 422.
"""
import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.http import HttpResponse
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status

from api.permissions import require, require_scoped
from db_core.models import Employee, TimeEntry, WorkDay
from db_core.services import zeiterfassung as zeit_service

router = Router()
# Zweiter Router: die Stammdatenpflege liegt fachlich bei den HR-Einstellungen
# (`/api/hr/zeitkategorien`, `/api/hr/pausenregel`, `/api/hr/feiertage`).
hr_router = Router()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class KategorieOut(Schema):
    id: UUID
    code: str | None
    name: str
    description: str | None
    is_work_time: bool
    is_system: bool
    status: str
    sort_order: int


class KategorieIn(Schema):
    name: str
    is_work_time: bool = True
    description: str | None = None
    sort_order: int = 100


class KategorieUpdateIn(Schema):
    name: str | None = None
    is_work_time: bool | None = None
    description: str | None = None
    sort_order: int | None = None


class PausenfensterOut(Schema):
    von: str
    bis: str


class PausenregelOut(Schema):
    mode: str
    fixed_breaks: list[PausenfensterOut]


class PausenregelIn(Schema):
    mode: str
    fixed_breaks: list[PausenfensterOut] = []


class FeiertagOut(Schema):
    day: date
    name: str
    region: str | None


class EintragOut(Schema):
    id: UUID
    work_day_id: UUID
    user_id: UUID
    user: str | None
    category_id: UUID
    kategorie: str
    is_work_time: bool
    started_at: datetime
    ended_at: datetime | None
    dauer_sekunden: int | None
    auto_generated: bool
    service_job_id: UUID | None
    einsatz: str | None
    note: str | None


class AktuellOut(Schema):
    laeuft: bool
    zustand: str  # GESTOPPT | LAEUFT | PAUSE
    ueberfaellig: bool
    eintrag: EintragOut | None
    # Bezugstag der Summen. NICHT „heute": laeuft eine Buchung seit gestern
    # (vergessenes Stoppen), sind es die Summen von GESTERN — die Feldnamen
    # sagen das jetzt, statt es unter dem Label „heute" zu verstecken.
    tag: date | None
    tag_arbeit_sekunden: int
    tag_pause_sekunden: int
    work_day_id: UUID | None
    tagesstatus: str | None


class StempelIn(Schema):
    category_id: UUID | None = None
    service_job_id: UUID | None = None
    note: str | None = None
    # Ein bestaetigter Arbeitstag ist nicht tot: der Monteur darf weiter
    # stempeln, wenn er es begruendet — der Tag faellt dann auf ENTWURF zurueck
    # (workflow.unseal_work_day, 0067). Ohne dieses Feld kaeme er aus der Uhr
    # nicht mehr heraus.
    correction_reason: str | None = None


class EintragCreateIn(Schema):
    category_id: UUID
    started_at: datetime
    ended_at: datetime
    user_id: UUID | None = None
    service_job_id: UUID | None = None
    note: str | None = None
    correction_reason: str | None = None


class EintragUpdateIn(Schema):
    category_id: UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    note: str | None = None
    correction_reason: str | None = None


class TagOut(Schema):
    id: UUID
    user_id: UUID
    user: str | None
    day: date
    status: str
    submitted_at: datetime | None
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None
    arbeit_sekunden: int
    pause_sekunden: int
    laeuft: bool
    eintraege_anzahl: int


class TagDetailOut(TagOut):
    eintraege: list[EintragOut]


class AblehnenIn(Schema):
    note: str


class StundenkontoOut(Schema):
    employee_id: UUID
    von: date
    bis: date
    soll: Decimal
    ist: Decimal
    pause: Decimal
    abwesend: Decimal
    saldo: Decimal
    tage_gesamt: int
    tage_offen: int
    tage_eingereicht: int
    tage_bestaetigt: int


class MeldungOut(Schema):
    detail: str


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------

def _eintrag_out(e):
    dauer = (
        int((e.ended_at - e.started_at).total_seconds()) if e.ended_at else None
    )
    return EintragOut(
        id=e.id,
        work_day_id=e.work_day_id,
        user_id=e.user_id,
        user=e.user.display_name if e.user_id else None,
        category_id=e.category_id,
        kategorie=e.category.name,
        is_work_time=e.category.is_work_time,
        started_at=e.started_at,
        ended_at=e.ended_at,
        dauer_sekunden=dauer,
        auto_generated=e.auto_generated,
        service_job_id=e.service_job_id,
        einsatz=e.service_job.job_number if e.service_job_id else None,
        note=e.note,
    )


def _tag_out(tag, entries, detail=False):
    summen = zeit_service.tages_summen(entries)
    daten = dict(
        id=tag.id,
        user_id=tag.user_id,
        user=tag.user.display_name if tag.user_id else None,
        day=tag.day,
        status=tag.status,
        submitted_at=tag.submitted_at,
        decided_at=tag.decided_at,
        decided_by=tag.decided_by.display_name if tag.decided_by_id else None,
        decision_note=tag.decision_note,
        arbeit_sekunden=summen["arbeit_sekunden"],
        pause_sekunden=summen["pause_sekunden"],
        laeuft=summen["laeuft"],
        eintraege_anzahl=len(entries),
    )
    if detail:
        return TagDetailOut(**daten, eintraege=[_eintrag_out(e) for e in entries])
    return TagOut(**daten)


def _kategorie_out(c):
    return KategorieOut(
        id=c.id,
        code=c.code,
        name=c.name,
        description=c.description,
        is_work_time=c.is_work_time,
        is_system=c.is_system,
        status=c.status,
        sort_order=c.sort_order,
    )


def _eintraege_je_tag(tage):
    """Ein Query für alle Tage der Liste (N+1 vermeiden)."""
    ids = [t.id for t in tage]
    gruppen = {i: [] for i in ids}
    if not ids:
        return gruppen
    for e in (
        TimeEntry.objects.select_related("category", "service_job", "user")
        .filter(work_day_id__in=ids)
        .order_by("started_at")
    ):
        gruppen[e.work_day_id].append(e)
    return gruppen


# ---------------------------------------------------------------------------
# Stempeluhr — wirkt IMMER auf den Akteur
# ---------------------------------------------------------------------------

def _aktuell_out(actor):
    zustand = zeit_service.aktuell(actor)
    eintrag = zustand["eintrag"]
    # Bezugstag: der Tag der laufenden Buchung (bei vergessenem Stoppen also der
    # VORTAG — sonst zeigte das UI eine leere Timeline zu einer laufenden Uhr),
    # sonst der heutige Tag. Er steht als `tag` mit in der Antwort, damit das UI
    # keine Vortagssummen als „heute" ausgeben kann.
    bezug = zeit_service.local_day(
        eintrag.started_at if eintrag else timezone.now()
    )
    tag = (
        WorkDay.objects.select_related("user", "decided_by")
        .filter(user_id=actor, day=bezug)
        .first()
    )
    entries = zeit_service.eintraege_am_tag(tag.id) if tag else []
    summen = zeit_service.tages_summen(entries)
    return AktuellOut(
        laeuft=zustand["laeuft"],
        zustand=zustand["zustand"],
        ueberfaellig=zustand["ueberfaellig"],
        eintrag=_eintrag_out(eintrag) if eintrag else None,
        tag=bezug,
        tag_arbeit_sekunden=summen["arbeit_sekunden"],
        tag_pause_sekunden=summen["pause_sekunden"],
        work_day_id=tag.id if tag else None,
        tagesstatus=tag.status if tag else None,
    )


@router.get("/aktuell", response=AktuellOut)
def aktuell(request):
    """Was läuft gerade? Immer der Akteur — die API kennt hier keine fremde ID."""
    actor, _ = require_scoped(request, "hr", "LESEN")
    return _aktuell_out(actor)


def _stempel(request, fn, **kwargs):
    actor, _ = require_scoped(request, "hr", "AENDERN")
    try:
        fn(actor, **kwargs)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _aktuell_out(actor)


@router.post("/stempel/start", response=AktuellOut)
def stempel_start(request, payload: StempelIn):
    """Start — eine neue laufende Buchung (Standard: Arbeitszeit).

    `correction_reason` ist der Weg aus einem bestätigten Arbeitstag: ohne
    Begründung 422 mit fachlicher Meldung, mit Begründung fällt der Tag auf
    ENTWURF zurück und die Uhr läuft."""
    return _stempel(
        request,
        zeit_service.stempel_start,
        category_id=payload.category_id,
        service_job_id=payload.service_job_id,
        note=payload.note,
        correction_reason=payload.correction_reason,
    )


@router.post("/stempel/pause", response=AktuellOut)
def stempel_pause(request, correction_reason: str | None = Query(None)):
    """Pause — Arbeitsbuchung beenden, Pausenbuchung starten."""
    return _stempel(
        request, zeit_service.stempel_pause, correction_reason=correction_reason
    )


@router.post("/stempel/weiter", response=AktuellOut)
def stempel_weiter(request, correction_reason: str | None = Query(None)):
    """Weiter — Pause beenden, Arbeit von vor der Pause fortschreiben."""
    return _stempel(
        request, zeit_service.stempel_weiter, correction_reason=correction_reason
    )


@router.post("/stempel/stopp", response=AktuellOut)
def stempel_stopp(request, correction_reason: str | None = Query(None)):
    """Stopp — laufende Buchung beenden."""
    return _stempel(
        request, zeit_service.stempel_stopp, correction_reason=correction_reason
    )


# ---------------------------------------------------------------------------
# Meine Tage
# ---------------------------------------------------------------------------

@router.get("/meine-tage", response=list[TagOut])
def meine_tage(
    request,
    von: date | None = Query(None),
    bis: date | None = Query(None),
):
    """Die eigenen Arbeitstage. Ohne Zeitraum: die letzten 30 Tage."""
    actor, _ = require_scoped(request, "hr", "LESEN")
    heute = date.today()
    von = von or (heute - timedelta(days=30))
    bis = bis or heute
    tage = list(zeit_service.arbeitstage(user_id=actor, von=von, bis=bis))
    gruppen = _eintraege_je_tag(tage)
    return [_tag_out(t, gruppen[t.id]) for t in tage]


def _load_tag(tag_id, actor, scope, *, eigener_pflicht=False):
    """Lädt einen Arbeitstag; fremde Zeile → 404 (Existenz nicht verraten)."""
    tag = zeit_service.arbeitstag(tag_id)
    if tag is None:
        raise HttpError(404, "Arbeitstag nicht gefunden.")
    if (scope == "EIGENE" or eigener_pflicht) and tag.user_id != actor:
        raise HttpError(404, "Arbeitstag nicht gefunden.")
    return tag


@router.get("/tage/{tag_id}", response=TagDetailOut)
def tag_detail(request, tag_id: UUID):
    actor, scope = require_scoped(request, "hr", "LESEN")
    tag = _load_tag(tag_id, actor, scope)
    return _tag_out(tag, zeit_service.eintraege_am_tag(tag.id), detail=True)


@router.post("/tage/{tag_id}/einreichen", response=TagDetailOut)
def tag_einreichen(request, tag_id: UUID):
    """Arbeitstag einreichen — die Handlung des Beschäftigten, immer der eigene Tag."""
    actor, scope = require_scoped(request, "hr", "AENDERN")
    tag = _load_tag(tag_id, actor, scope, eigener_pflicht=True)
    try:
        tag = zeit_service.arbeitstag_einreichen(actor, work_day_id=tag.id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _tag_out(tag, zeit_service.eintraege_am_tag(tag.id), detail=True)


# ---------------------------------------------------------------------------
# Verwaltung (row_scope ALLE)
# ---------------------------------------------------------------------------

_ZEITRAEUME = ("heute", "woche", "monat", "jahr")


def _zeitraum(name, von, bis):
    if von and bis:
        if bis < von:
            raise HttpError(422, "Das Ende des Zeitraums liegt vor dem Beginn.")
        return von, bis
    heute = date.today()
    if name == "heute":
        return heute, heute
    if name == "woche":
        start = heute - timedelta(days=heute.weekday())
        return start, start + timedelta(days=6)
    if name == "jahr":
        return date(heute.year, 1, 1), date(heute.year, 12, 31)
    # Default: Monat
    start = heute.replace(day=1)
    naechster = (start + timedelta(days=32)).replace(day=1)
    return start, naechster - timedelta(days=1)


@router.get("", response=list[TagOut])
def liste(
    request,
    zeitraum: str = Query("monat"),
    von: date | None = Query(None),
    bis: date | None = Query(None),
    user_id: UUID | None = Query(None),
    status: str | None = Query(None),
):
    """Verwaltungssicht: Arbeitstage aller Mitarbeiter im Zeitraum.

    `require` (fail-closed): row_scope EIGENE → 403. Der Monteur nutzt
    `/meine-tage`."""
    require(request, "hr", "LESEN")
    if zeitraum not in _ZEITRAEUME and not (von and bis):
        raise HttpError(422, f"Zeitraum muss einer von {', '.join(_ZEITRAEUME)} sein.")
    if status is not None and status not in zeit_service.WORK_DAY_STATUS:
        raise HttpError(422, "Unbekannter Status.")
    v, b = _zeitraum(zeitraum, von, bis)
    tage = list(zeit_service.arbeitstage(user_id=user_id, von=v, bis=b, status=status))
    gruppen = _eintraege_je_tag(tage)
    return [_tag_out(t, gruppen[t.id]) for t in tage]


@router.post("/tage/{tag_id}/bestaetigen", response=TagDetailOut)
def tag_bestaetigen(request, tag_id: UUID):
    """Bestätigen — Führungsaufgabe (`hr/FREIGEBEN`).

    Vier-Augen: der eigene Tag ist ausgeschlossen — im Service UND physisch im
    DB-Trigger (`workflow.enforce_work_day`)."""
    actor, _ = require(request, "hr", "FREIGEBEN")
    tag = _load_tag(tag_id, actor, "ALLE")
    try:
        tag = zeit_service.arbeitstag_bestaetigen(actor, work_day_id=tag.id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _tag_out(tag, zeit_service.eintraege_am_tag(tag.id), detail=True)


@router.post("/tage/{tag_id}/ablehnen", response=TagDetailOut)
def tag_ablehnen(request, tag_id: UUID, payload: AblehnenIn):
    """Ablehnen — begründungspflichtig (CHECK + Statusautomat)."""
    actor, _ = require(request, "hr", "FREIGEBEN")
    tag = _load_tag(tag_id, actor, "ALLE")
    try:
        tag = zeit_service.arbeitstag_ablehnen(
            actor, work_day_id=tag.id, note=payload.note
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _tag_out(tag, zeit_service.eintraege_am_tag(tag.id), detail=True)


@router.post("/tage/{tag_id}/pausen-anwenden", response=TagDetailOut)
def tag_pausen_anwenden(request, tag_id: UUID, correction_reason: str | None = Query(None)):
    """Fehlende Pflichtpausen einrechnen (Pausenregel des Betriebs)."""
    actor, scope = require_scoped(request, "hr", "AENDERN")
    tag = _load_tag(tag_id, actor, scope)
    try:
        zeit_service.pausen_regel_anwenden(
            actor, work_day_id=tag.id, correction_reason=correction_reason
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    tag = zeit_service.arbeitstag(tag.id)
    return _tag_out(tag, zeit_service.eintraege_am_tag(tag.id), detail=True)


# ---------------------------------------------------------------------------
# Zeiteinträge (CRUD)
# ---------------------------------------------------------------------------

def _load_eintrag(entry_id, actor, scope):
    e = (
        TimeEntry.objects.select_related("category", "user", "service_job", "work_day")
        .filter(id=entry_id)
        .first()
    )
    if e is None:
        raise HttpError(404, "Zeiteintrag nicht gefunden.")
    if scope == "EIGENE" and e.user_id != actor:
        raise HttpError(404, "Zeiteintrag nicht gefunden.")
    return e


@router.post("/eintraege", response={201: EintragOut})
def eintrag_anlegen(request, payload: EintragCreateIn):
    """Zeiteintrag von Hand erfassen.

    Bei row_scope EIGENE wird `user_id` auf den Akteur gezwungen; eine
    ausdrücklich fremde user_id ist 403 (nicht still umgebogen)."""
    actor, scope = require_scoped(request, "hr", "AENDERN")
    user_id = payload.user_id
    if scope == "EIGENE":
        if user_id not in (None, actor):
            raise HttpError(
                403,
                "Ihre Rolle erlaubt nur eigene Zeiten; eine Zeit kann nicht für "
                "eine andere Person erfasst werden.",
            )
        user_id = actor
    elif user_id is None:
        user_id = actor
    try:
        entry = zeit_service.zeiteintrag_anlegen(
            actor,
            user_id=user_id,
            category_id=payload.category_id,
            started_at=payload.started_at,
            ended_at=payload.ended_at,
            service_job_id=payload.service_job_id,
            note=payload.note,
            correction_reason=payload.correction_reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _eintrag_out(entry))


@router.patch("/eintraege/{entry_id}", response=EintragOut)
def eintrag_aendern(request, entry_id: UUID, payload: EintragUpdateIn):
    actor, scope = require_scoped(request, "hr", "AENDERN")
    _load_eintrag(entry_id, actor, scope)
    try:
        entry = zeit_service.zeiteintrag_aendern(
            actor,
            entry_id=entry_id,
            category_id=payload.category_id,
            started_at=payload.started_at,
            ended_at=payload.ended_at,
            note=payload.note,
            correction_reason=payload.correction_reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _eintrag_out(entry)


@router.delete("/eintraege/{entry_id}", response={200: MeldungOut})
def eintrag_loeschen(
    request, entry_id: UUID, correction_reason: str | None = Query(None)
):
    """Löschen bleibt im B-28-Fenster erlaubt; das Vorher-Bild landet im Audit."""
    actor, scope = require_scoped(request, "hr", "AENDERN")
    _load_eintrag(entry_id, actor, scope)
    try:
        zeit_service.zeiteintrag_loeschen(
            actor, entry_id=entry_id, correction_reason=correction_reason
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(200, MeldungOut(detail="Zeiteintrag gelöscht."))


@router.get("/einsaetze/{job_id}/eintraege", response=list[EintragOut])
def eintraege_am_einsatz(request, job_id: UUID):
    """Die am Einsatz gebuchten Zeiten — Grundlage der Zeitbuchung im
    Baustellenbericht."""
    actor, scope = require_scoped(request, "hr", "LESEN")
    qs = (
        TimeEntry.objects.select_related("category", "user", "service_job")
        .filter(service_job_id=job_id)
        .order_by("started_at")
    )
    if scope == "EIGENE":
        qs = qs.filter(user_id=actor)
    return [_eintrag_out(e) for e in qs]


# ---------------------------------------------------------------------------
# Stundenkonto + Export
# ---------------------------------------------------------------------------

class MitarbeitendeOut(Schema):
    user_id: UUID
    employee_id: UUID
    name: str
    employee_number: str


@router.get("/mitarbeitende", response=list[MitarbeitendeOut])
def mitarbeitende(request):
    """Die Filterliste der Verwaltungssicht (Personalsätze mit Login-Konto).

    Eigener Endpunkt statt `/hr/employees`: dort steht die `app_user_id` bewusst
    nicht im Schema, und die Zeiterfassung filtert über `user_id`. `require`
    (fail-closed) — der Monteur bekommt hier nichts."""
    require(request, "hr", "LESEN")
    # `Employee.party` zeigt auf identity.person (NICHT auf identity.party) —
    # `display_name` liegt eine Ebene höher an der Party. Der Name wird deshalb
    # aus Vor-/Nachname gebildet; ein Browser-Durchlauf hat den FieldError
    # (500) aufgedeckt.
    rows = (
        Employee.objects.exclude(status="AUSGETRETEN")
        .select_related("party")
        .order_by("party__last_name", "party__first_name")
    )
    return [
        MitarbeitendeOut(
            user_id=e.app_user_id,
            employee_id=e.id,
            name=f"{e.party.first_name} {e.party.last_name}".strip(),
            employee_number=e.employee_number,
        )
        for e in rows
    ]


@router.get("/stundenkonto", response=StundenkontoOut)
def stundenkonto(
    request,
    employee_id: UUID | None = Query(None),
    user_id: UUID | None = Query(None),
    von: date | None = Query(None),
    bis: date | None = Query(None),
):
    """Soll/Ist/Saldo — abgeleitet, nie gespeichert.

    Ohne Angabe: der eigene Personalsatz. `user_id` (app_user) ist die Sicht der
    Zeiterfassung, `employee_id` die des Personalstamms — beide führen zum
    selben Konto. Bei row_scope EIGENE ist ein fremder Personalsatz 404."""
    actor, scope = require_scoped(request, "hr", "LESEN")
    eigener = Employee.objects.filter(app_user_id=actor).first()

    if user_id is not None and employee_id is None:
        ziel = Employee.objects.filter(app_user_id=user_id).first()
        if ziel is None:
            raise HttpError(404, "Zu diesem Konto gibt es keinen Personalsatz.")
        employee_id = ziel.id

    if employee_id is None:
        if eigener is None:
            raise HttpError(404, "Zu diesem Konto gibt es keinen Personalsatz.")
        employee_id = eigener.id
    elif scope == "EIGENE" and (eigener is None or eigener.id != employee_id):
        raise HttpError(404, "Mitarbeiter nicht gefunden.")

    v, b = _zeitraum("monat", von, bis)
    try:
        return zeit_service.stundenkonto(employee_id, v, b)
    except ValueError as exc:
        raise HttpError(404, str(exc))


@router.get("/stundenliste.csv")
def stundenliste_csv(
    request,
    von: date | None = Query(None),
    bis: date | None = Query(None),
    user_id: UUID | None = Query(None),
):
    """Stundenliste als CSV — die **Vorlagefähigkeit** nach § 17 Abs. 1 MiLoG
    (Beginn, Ende, Dauer je Tag; zwei Jahre aufzubewahren, dem Zoll vorzulegen).

    Deutsches Excel-Format (Semikolon, UTF-8 mit BOM, Komma-Dezimal) wie die
    übrigen Exporte."""
    require(request, "hr", "EXPORTIEREN")
    v, b = _zeitraum("monat", von, bis)
    zeilen = zeit_service.stundenliste(v, b, user_id=user_id)

    puffer = io.StringIO()
    writer = csv.writer(
        puffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n"
    )
    writer.writerow(
        [
            "Mitarbeiter", "Tag", "Beginn", "Ende", "Dauer (h)", "Kategorie",
            "Arbeitszeit", "Automatisch", "Einsatz", "Tagesstatus",
            "Bestätigt von", "Notiz",
        ]
    )
    for z in zeilen:
        writer.writerow(
            [
                z["mitarbeiter"],
                z["tag"].strftime("%d.%m.%Y"),
                z["beginn"].strftime("%H:%M"),
                z["ende"].strftime("%H:%M") if z["ende"] else "läuft",
                (
                    str(z["dauer_stunden"]).replace(".", ",")
                    if z["dauer_stunden"] is not None
                    else ""
                ),
                z["kategorie"],
                "Ja" if z["arbeitszeit"] else "Nein",
                "Ja" if z["automatisch"] else "Nein",
                z["einsatz"],
                z["tagesstatus"],
                z["bestaetigt_von"],
                z["notiz"],
            ]
        )
    inhalt = puffer.getvalue().encode("utf-8-sig")
    antwort = HttpResponse(inhalt, content_type="text/csv; charset=utf-8")
    antwort["Content-Disposition"] = (
        f'attachment; filename="stundenliste_{v.isoformat()}_{b.isoformat()}.csv"'
    )
    antwort["X-Content-Type-Options"] = "nosniff"
    return antwort


# ---------------------------------------------------------------------------
# Stammdaten: Zeitkategorien, Pausenregel, Feiertage (Router /api/hr)
# ---------------------------------------------------------------------------

@hr_router.get("/zeitkategorien", response=list[KategorieOut])
def kategorien(request, include_archived: bool = Query(False)):
    """Lesen darf jeder mit `hr/LESEN` — auch der Monteur (er braucht die Liste
    für die Stempeluhr). Kategorien sind keine Personendaten."""
    require_scoped(request, "hr", "LESEN")
    return [_kategorie_out(c) for c in zeit_service.kategorien(include_archived)]


@hr_router.post("/zeitkategorien", response={201: KategorieOut})
def kategorie_anlegen(request, payload: KategorieIn):
    actor, _ = require(request, "hr", "ANLEGEN")
    try:
        cat = zeit_service.kategorie_anlegen(
            actor,
            name=payload.name,
            is_work_time=payload.is_work_time,
            description=payload.description,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _kategorie_out(cat))


@hr_router.patch("/zeitkategorien/{category_id}", response=KategorieOut)
def kategorie_aendern(request, category_id: UUID, payload: KategorieUpdateIn):
    actor, _ = require(request, "hr", "AENDERN")
    try:
        cat = zeit_service.kategorie_aendern(
            actor,
            category_id=category_id,
            name=payload.name,
            description=payload.description,
            is_work_time=payload.is_work_time,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _kategorie_out(cat)


@hr_router.post("/zeitkategorien/{category_id}/archivieren", response=KategorieOut)
def kategorie_archivieren(request, category_id: UUID):
    """Systemkategorien sind nicht archivierbar (Service + DB-Trigger)."""
    actor, _ = require(request, "hr", "AENDERN")
    try:
        cat = zeit_service.kategorie_archivieren(actor, category_id=category_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _kategorie_out(cat)


@hr_router.get("/pausenregel", response=PausenregelOut)
def pausenregel(request):
    require_scoped(request, "hr", "LESEN")
    regel = zeit_service.pausenregel()
    return PausenregelOut(
        mode=regel.mode,
        fixed_breaks=[PausenfensterOut(**w) for w in (regel.fixed_breaks or [])],
    )


@hr_router.put("/pausenregel", response=PausenregelOut)
def pausenregel_setzen(request, payload: PausenregelIn):
    actor, _ = require(request, "hr", "AENDERN")
    try:
        regel = zeit_service.pausenregel_setzen(
            actor,
            mode=payload.mode,
            fixed_breaks=[w.dict() for w in payload.fixed_breaks],
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return PausenregelOut(
        mode=regel.mode,
        fixed_breaks=[PausenfensterOut(**w) for w in (regel.fixed_breaks or [])],
    )


@hr_router.get("/feiertage", response=list[FeiertagOut])
def feiertage(request, jahr: int | None = Query(None)):
    """Feiertage des Jahres (bundesweit + Bundesland aus dem Firmenprofil)."""
    require_scoped(request, "hr", "LESEN")
    jahr = jahr or date.today().year
    tage = zeit_service.feiertage(date(jahr, 1, 1), date(jahr, 12, 31))
    region = zeit_service._firmen_region()
    return [
        FeiertagOut(day=d, name=n, region=region) for d, n in sorted(tage.items())
    ]
