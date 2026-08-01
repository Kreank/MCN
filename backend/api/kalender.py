"""Kalender-API — Termine als iCalendar-Datei (.ics, RFC 5545).

Die offene Kalenderschnittstelle: Ein Termin bzw. ein Zeitraum wird als Datei
heruntergeladen und in Outlook/Google/Apple importiert. **Einweg** — es gibt
keinen Rückschreibpfad; was im fremden Kalender geändert wird, kommt nicht
zurück ins MCN.

Bewusst NICHT in diesem Slice: der abonnierbare Token-Feed
(`/kalender/feed/{token}.ics`) aus `docs/roadmap/06-planung.md`. Ein
dauerhaft gültiger, sitzungsloser Link ist ein eigener Sicherheitsbaustein
(Ausstellen, Anzeigen, Widerrufen, Ablauf, Hash-Speicherung) und gehört auf den
gemeinsamen Token-Unterbau, den schon der IDS-Warenkorb-Hook und das
Gerätetoken benutzen — nicht als Anhängsel eines Exportbuttons.

Rechte: dieselben Tore wie die Einsatzanzeige (`workflow/LESEN`). Wer nur
`EIGENE` sehen darf, bekommt einen fremden Einsatz als **404** (die Existenz
wird nicht verraten) und im Zeitraum-Export ausschließlich seine eigenen
Termine — die Zuweisung ist dabei die einzige Grenze, nie der Auftrag.

Serialisiert wird in `db_core/services/kalender.py`; dort steht auch, warum die
Datei UTC-Zeitpunkte statt einer VTIMEZONE trägt und was bewusst NICHT in die
`DESCRIPTION` geht (Zutrittshinweise, Abschlussnotizen, Preise, Namen).
"""
from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.http import HttpResponse
from ninja import Query, Router
from ninja.errors import HttpError

from api.permissions import require_scoped
# Bewusst importiert statt nachgebaut: Liste und Detail-Guard MÜSSEN dieselbe
# Definition von „eigen" benutzen. Zwei Kopien derselben Regel driften
# auseinander, und die Lücke fällt erst auf, wenn sie ausgenutzt wird
# (docs/INVARIANTEN.md, Kap. 5).
from api.planung import _guard_own_job
from db_core.betriebszeit import BETRIEBS_TZ
from db_core.models import ServiceJob
from db_core.services import kalender as kalender_service

router = Router()

#: Obergrenze des Zeitraum-Exports. Ein Jahr plus einen Tag deckt „das ganze
#: Jahr inklusive Silvester" ab; alles darüber ist keine Kalenderabfrage mehr,
#: sondern ein Datenabzug.
MAX_TAGE = 366

#: Vollständig geladene Einsatz-Abfrage. Alles, was `_ort_text` im Service
#: anfasst, ist hier mitgeladen — sonst eine Extra-Query je Termin.
_SELECT_RELATED = (
    "work_order__property__address",
    "work_order__building__address",
    "work_order__unit",
    "property__address",
    "building__address",
    "unit",
    "appointment_category",
)


def _antwort(inhalt: str, dateiname: str) -> HttpResponse:
    """ICS als Download. UTF-8 ist für text/calendar die Vorgabe (RFC 5545 §6)."""
    antwort = HttpResponse(
        inhalt.encode("utf-8"), content_type="text/calendar; charset=utf-8"
    )
    antwort["Content-Disposition"] = f'attachment; filename="{dateiname}"'
    antwort["X-Content-Type-Options"] = "nosniff"
    return antwort


@router.get("/einsatz/{job_id}.ics")
def einsatz_ics(request, job_id: UUID):
    """Einen Einsatz als iCalendar-Datei herunterladen.

    Dasselbe Tor wie `GET /planung/einsaetze/{id}`: `workflow/LESEN`, bei Scope
    'EIGENE' nur zugewiesene Einsätze — fremde mit 404.

    Ein Einsatz ohne Planbeginn (UNGEPLANT) ergibt kein gültiges VEVENT und
    wird mit 422 abgelehnt statt als leere Datei ausgeliefert; eine
    Kalenderdatei ohne Termin sieht wie ein geglückter Export aus.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    job = ServiceJob.objects.select_related(*_SELECT_RELATED).filter(id=job_id).first()
    if job is None:
        raise HttpError(404, "Einsatz nicht gefunden.")
    _guard_own_job(job_id, actor, scope)
    termin = kalender_service.termin_aus_job(job)
    if termin is None:  # kein Planbeginn → kein gültiges VEVENT
        raise HttpError(
            422,
            "Der Einsatz hat noch keinen Termin — ohne Planbeginn lässt sich "
            "kein Kalendereintrag erzeugen.",
        )
    inhalt = kalender_service.vcalendar([termin], name=termin.titel)
    return _antwort(inhalt, kalender_service.dateiname_einzel(job.job_number or ""))


@router.get("/einsaetze.ics")
def einsaetze_ics(
    request,
    von: date | None = Query(None),
    bis: date | None = Query(None),
    assignee_id: UUID | None = Query(None),
):
    """Alle Termine eines Zeitraums als iCalendar-Datei.

    `von`/`bis` sind **Kalendertage in Betriebszeit** (Europe/Berlin), beide
    einschließlich — nicht UTC-Tage. Sonst fiele ein Termin am 1. um 00:30 MESZ
    aus dem Fenster „ab dem 1." heraus (Invariante Kap. 7).

    `von`/`bis` sind bewusst als optional deklariert und im Rumpf geprüft, damit
    die Rechteprüfung VOR der Parametervalidierung greift — sonst bekäme ein
    Konto ohne Recht ein 422 statt des korrekten 403 (Muster: DATEV-Export).

    Zeilenbegrenzung: Bei Scope 'EIGENE' wird `assignee_id` auf den Akteur
    **erzwungen** (nicht nur vorbelegt) — ein Monteur exportiert ausschließlich
    seinen eigenen Kalender.

    Abgesagte Termine (AUSGEFALLEN) sind enthalten und tragen STATUS:CANCELLED.
    Sie wegzulassen hieße, dass sie im abonnierten Kalender stehen bleiben.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    if von is None or bis is None:
        raise HttpError(422, "Bitte einen Zeitraum (von, bis) angeben.")
    if bis < von:
        raise HttpError(422, "Das Ende des Zeitraums liegt vor seinem Beginn.")
    if (bis - von).days + 1 > MAX_TAGE:
        raise HttpError(
            422,
            f"Der Zeitraum darf höchstens {MAX_TAGE} Tage umfassen; "
            "bitte in kleinere Abschnitte teilen.",
        )
    if scope == "EIGENE":
        assignee_id = actor

    # Tagesgrenzen in Betriebszeit: [von 00:00, bis+1 Tag 00:00).
    von_ab = datetime.combine(von, time.min, tzinfo=BETRIEBS_TZ)
    bis_vor = datetime.combine(bis + timedelta(days=1), time.min, tzinfo=BETRIEBS_TZ)

    qs = ServiceJob.objects.select_related(*_SELECT_RELATED).filter(
        scheduled_start__gte=von_ab, scheduled_start__lt=bis_vor
    )
    if assignee_id is not None:
        qs = qs.filter(assignments__assignee_id=assignee_id).distinct()
    jobs = list(qs.order_by("scheduled_start", "id"))

    inhalt = kalender_service.baue_ics(jobs, name="MCN Termine")
    return _antwort(inhalt, kalender_service.dateiname_zeitraum(von, bis))
