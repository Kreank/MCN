"""Zeiterfassung — Stempeluhr, Tagesklammer, Freigabe, Pausen, Stundenkonto.

Ein Zeitstrahl, zwei Auswertungen
---------------------------------
`workflow.time_entry` bleibt die **einzige Quelle der Wahrheit**. Die operative
Sicht (Zeit am Einsatz, Nachkalkulation) und die arbeitsrechtliche Sicht
(§ 17 MiLoG: Beginn, Ende, Dauer je Tag; ArbZG § 4: Pausen) entstehen aus
DEMSELBEN Bestand — durch Kategorie-Klassifikation (`is_work_time`,
Migration 0066), die Tagesklammer (`workflow.work_day`, 0067) und den
Soll-Vergleich hier im Service. Es gibt keinen zweiten Datenbestand und keine
gespeicherten Salden (gleiche Konvention wie offener Rechnungsbetrag und
Urlaubsverbrauch: **abgeleitet, nie gespeichert**).

Die Stempeluhr
--------------
Eine laufende Buchung ist ein Eintrag mit `ended_at IS NULL`. Die DB laesst je
Mitarbeiter genau **eine** zu (partieller UNIQUE-Index auf `user_id WHERE
ended_at IS NULL`).

Sie belegt bewusst **kein** Intervall `[start, ∞)` — der EXCLUDE-Constraint
greift nur unter den ABGESCHLOSSENEN Buchungen (`WHERE ended_at IS NOT NULL`,
Migration 0066). Eine laufende Buchung hat schlicht noch kein Ende und kann
deshalb keine spaeter geplante Zeit blockieren. Die Kollision entstuende erst
beim Stoppen — damit der Monteur dann nicht in einer Sackgasse steht, prueft
`stempel_start` den Startzeitpunkt und `stempel_stopp` das Intervall VORHER
gegen die erfassten Zeiten (siehe dort).

Zustandsautomat (die vier Knoepfe des Monteurs):

    (nichts laeuft) --Start--> ARBEIT
    ARBEIT  --Pause--> PAUSE     (Arbeitsbuchung enden, Pausenbuchung starten)
    PAUSE   --Weiter--> ARBEIT   (Pausenbuchung enden, Arbeitsbuchung starten —
                                  Kategorie und Einsatz der Buchung VOR der
                                  Pause werden fortgeschrieben)
    ARBEIT  --Stopp--> (nichts)
    PAUSE   --Stopp--> (nichts)

Fehlerfest:
* **Start, obwohl etwas laeuft** → 422 mit Angabe, was laeuft. Kein stilles
  Umschalten: der Monteur soll sehen, dass seine Uhr noch lief.
* **Pause, obwohl nichts laeuft** → 422. **Pause waehrend einer Pause** → 422.
* **Weiter, obwohl keine Pause laeuft** → 422.
* **Vergessenes Stoppen**: die laufende Buchung ueberlebt den Tageswechsel. Sie
  ist nicht „automatisch zu Ende" — das waere eine erfundene Tatsache. Statt-
  dessen meldet `aktuell()` sie als `ueberfaellig` (Beginn liegt vor heute), das
  UI markiert sie, und Start ist blockiert, bis der Monteur stoppt oder den
  Eintrag korrigiert. So bleibt die Aufzeichnung ehrlich UND korrigierbar.

Nachtschicht
------------
Ein Eintrag gehoert dem lokalen Kalendertag seines **Beginns** (Europe/Berlin).
Eine Schicht 22:00–06:00 ist EIN Arbeitstag. Begruendung im Kopf von 0067.
"""
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Absence,
    AppUser,
    BreakRule,
    CompanyProfile,
    Employee,
    EmploymentContract,
    Holiday,
    ServiceJob,
    TimeAdjustment,
    TimeCategory,
    TimeEntry,
    WorkDay,
)
from db_core.services._validation import ensure_exists

# Die Betriebszeitzone. Django laeuft auf TIME_ZONE='UTC' (Nummernkreis-
# Jahreszuordnung, db/README.md) — der Arbeitstag ist aber ein LOKALER Kalender-
# tag. Muss mit workflow.local_day() (Migration 0067) uebereinstimmen.
BETRIEBS_TZ = ZoneInfo("Europe/Berlin")

WORK_DAY_STATUS = ("ENTWURF", "EINGEREICHT", "BESTAETIGT", "ABGELEHNT")
BREAK_MODES = ("KEINE", "GESETZLICH", "FESTE_ZEITEN")

# Ausgleichsarten (Codeliste, Migration 0072). Sie klassifizieren nur — das
# Vorzeichen steht in `minutes`, nicht in der Art.
ADJUSTMENT_TYPES = ("EINBEHALT", "AUSZAHLUNG", "FREIZEITAUSGLEICH", "KORREKTUR")

# ArbZG § 4: mehr als 6 h → 30 min, mehr als 9 h → 45 min Ruhepause.
_ARBZG_STUFEN = ((timedelta(hours=9), timedelta(minutes=45)),
                 (timedelta(hours=6), timedelta(minutes=30)))

# Wochentag (date.weekday(): Mo=0) → Spalte des Sollstunden-Rasters.
_WEEKDAY_FIELDS = (
    "hours_monday",
    "hours_tuesday",
    "hours_wednesday",
    "hours_thursday",
    "hours_friday",
    "hours_saturday",
    "hours_sunday",
)


def local_day(ts):
    """Lokaler Kalendertag eines Zeitpunkts — Gegenstueck zu workflow.local_day()."""
    return ts.astimezone(BETRIEBS_TZ).date()


def _local_dt(day, hh, mm):
    return datetime.combine(day, time(hh, mm), tzinfo=BETRIEBS_TZ)


# ---------------------------------------------------------------------------
# Zeitkategorien
# ---------------------------------------------------------------------------

def kategorien(include_archived=False):
    qs = TimeCategory.objects.all()
    if not include_archived:
        qs = qs.filter(status="AKTIV")
    return list(qs.order_by("sort_order", "name"))


def kategorie_by_code(code):
    return TimeCategory.objects.filter(code=code).first()


def standard_kategorie():
    """ARBEITSZEIT — die Kategorie, mit der die Stempeluhr startet."""
    cat = kategorie_by_code("ARBEITSZEIT")
    if cat is None:  # pragma: no cover — Seed der Migration 0066
        raise ValueError("Systemkategorie ARBEITSZEIT fehlt.")
    return cat


def pausen_kategorie():
    cat = kategorie_by_code("PAUSE")
    if cat is None:  # pragma: no cover
        raise ValueError("Systemkategorie PAUSE fehlt.")
    return cat


def _resolve_kategorie(category_id):
    """Kategorie-UUID prüfen: existiert und ist aktiv (sonst 422 statt 500)."""
    cat = TimeCategory.objects.filter(id=category_id).first()
    if cat is None:
        raise ValueError("Unbekannte Zeitkategorie.")
    if cat.status != "AKTIV":
        raise ValueError(f"Zeitkategorie '{cat.name}' ist archiviert.")
    return cat


def kategorie_anlegen(
    actor_app_user_id, *, name, is_work_time, description=None, sort_order=100
):
    if not name or not name.strip():
        raise ValueError("Der Name darf nicht leer sein.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            return TimeCategory.objects.create(
                id=uuid.uuid4(),
                code=None,
                name=name.strip(),
                description=(description or None),
                is_work_time=bool(is_work_time),
                is_system=False,
                status="AKTIV",
                sort_order=sort_order,
            )


def kategorie_aendern(
    actor_app_user_id, *, category_id, name=None, description=None,
    is_work_time=None, sort_order=None,
):
    cat = TimeCategory.objects.filter(id=category_id).first()
    if cat is None:
        raise ValueError("Unbekannte Zeitkategorie.")
    if name is not None:
        if not name.strip():
            raise ValueError("Der Name darf nicht leer sein.")
        cat.name = name.strip()
    if description is not None:
        cat.description = description.strip() or None
    if is_work_time is not None:
        cat.is_work_time = bool(is_work_time)
    if sort_order is not None:
        cat.sort_order = sort_order
    with as_business_error():
        with business_transaction(actor_app_user_id):
            cat.save(update_fields=["name", "description", "is_work_time", "sort_order"])
    cat.refresh_from_db()
    return cat


def kategorie_archivieren(actor_app_user_id, *, category_id):
    cat = TimeCategory.objects.filter(id=category_id).first()
    if cat is None:
        raise ValueError("Unbekannte Zeitkategorie.")
    if cat.is_system:
        raise ValueError(
            "Systemkategorien können nicht archiviert werden — sie tragen die "
            "arbeitsrechtliche Klassifikation."
        )
    cat.status = "ARCHIVIERT"
    with as_business_error():
        with business_transaction(actor_app_user_id):
            cat.save(update_fields=["status"])
    return cat


# ---------------------------------------------------------------------------
# Pausenregel + Feiertage
# ---------------------------------------------------------------------------

def pausenregel():
    return BreakRule.objects.first()


def _validiere_fenster(fixed_breaks):
    """[{"von": "HH:MM", "bis": "HH:MM"}] — Form, Reihenfolge, Überlappungsfreiheit."""
    fenster = []
    for i, w in enumerate(fixed_breaks or [], start=1):
        if not isinstance(w, dict) or "von" not in w or "bis" not in w:
            raise ValueError(f"Pausenfenster {i}: erwartet {{'von': ..., 'bis': ...}}.")
        try:
            von = time.fromisoformat(str(w["von"]))
            bis = time.fromisoformat(str(w["bis"]))
        except ValueError:
            raise ValueError(f"Pausenfenster {i}: Uhrzeit im Format HH:MM erwartet.")
        if bis <= von:
            raise ValueError(f"Pausenfenster {i}: 'bis' muss nach 'von' liegen.")
        fenster.append((von, bis))
    fenster.sort()
    for a, b in zip(fenster, fenster[1:]):
        if b[0] < a[1]:
            raise ValueError("Pausenfenster dürfen sich nicht überlappen.")
    return [
        {"von": v.strftime("%H:%M"), "bis": b.strftime("%H:%M")} for v, b in fenster
    ]


def pausenregel_setzen(actor_app_user_id, *, mode, fixed_breaks=None):
    if mode not in BREAK_MODES:
        raise ValueError(f"Ungültiger Modus. Erlaubt: {', '.join(BREAK_MODES)}.")
    fenster = _validiere_fenster(fixed_breaks) if mode == "FESTE_ZEITEN" else []
    if mode == "FESTE_ZEITEN" and not fenster:
        raise ValueError("Modus FESTE_ZEITEN verlangt mindestens ein Pausenfenster.")
    regel = BreakRule.objects.first()
    if regel is None:  # pragma: no cover — die Migration seedet die Zeile
        raise ValueError("Pausenregel nicht initialisiert.")
    regel.mode = mode
    regel.fixed_breaks = fenster
    with as_business_error():
        with business_transaction(actor_app_user_id):
            regel.save(update_fields=["mode", "fixed_breaks"])
    return regel


def _firmen_region():
    profil = CompanyProfile.objects.first()
    return profil.state_code if profil else None


def feiertage(von, bis):
    """{date: name} für den Zeitraum — bundesweit + Bundesland des Firmenprofils."""
    region = _firmen_region()
    qs = Holiday.objects.filter(day__gte=von, day__lte=bis)
    qs = qs.filter(Q(region__isnull=True) | Q(region=region)) if region else qs.filter(
        region__isnull=True
    )
    return {h.day: h.name for h in qs}


# ---------------------------------------------------------------------------
# Stempeluhr
# ---------------------------------------------------------------------------

def aktueller_eintrag(user_id):
    """Die laufende Buchung (ended_at IS NULL) oder None."""
    return (
        TimeEntry.objects.select_related("category", "service_job", "work_day")
        .filter(user_id=user_id, ended_at__isnull=True)
        .first()
    )


def aktuell(user_id, now=None):
    """Zustand der Stempeluhr: was läuft, seit wann, ist es überfällig?"""
    now = now or timezone.now()
    entry = aktueller_eintrag(user_id)
    if entry is None:
        return {"laeuft": False, "eintrag": None, "ueberfaellig": False, "zustand": "GESTOPPT"}
    ueberfaellig = local_day(entry.started_at) < local_day(now)
    zustand = "PAUSE" if not entry.category.is_work_time else "LAEUFT"
    return {
        "laeuft": True,
        "eintrag": entry,
        "ueberfaellig": ueberfaellig,
        "zustand": zustand,
    }


def _laufend_oder_fehler(user_id):
    entry = aktueller_eintrag(user_id)
    if entry is None:
        raise ValueError("Es läuft keine Zeitbuchung. Bitte zuerst „Start“ drücken.")
    return entry


def _spanne(entry):
    von = entry.started_at.astimezone(BETRIEBS_TZ)
    bis = entry.ended_at.astimezone(BETRIEBS_TZ)
    return f"{entry.category.name} am {von:%d.%m.%Y} von {von:%H:%M} bis {bis:%H:%M}"


def _kollision(user_id, von, bis, ausser=None):
    """Eine ABGESCHLOSSENE Buchung desselben Mitarbeiters, die [von, bis) trifft."""
    qs = (
        TimeEntry.objects.select_related("category")
        .filter(
            user_id=user_id,
            ended_at__isnull=False,
            started_at__lt=bis,
            ended_at__gt=von,
        )
        .order_by("started_at")
    )
    if ausser is not None:
        qs = qs.exclude(id=ausser)
    return qs.first()


def _tag_schloss_pruefen(user_id, ts, correction_reason):
    """Der Arbeitstag ist bestaetigt → jede Aenderung braucht eine Begruendung.

    Das setzt die DB physisch durch (`workflow.unseal_work_day`, 0067) — aber mit
    einer technischen Meldung („SET LOCAL app.correction_reason“), die im UI des
    Monteurs nichts zu suchen hat. Hier faellt die Entscheidung fachlich und
    VORHER: keine Begruendung → 422 mit einer Meldung, die sagt, was zu tun ist.
    Mit Begruendung laeuft der vorgesehene Weg (Tag faellt auf ENTWURF zurueck).
    """
    if correction_reason and correction_reason.strip():
        return
    tag = WorkDay.objects.filter(user_id=user_id, day=local_day(ts)).first()
    if tag is not None and tag.status == "BESTAETIGT":
        raise ValueError(
            f"Der Arbeitstag {tag.day:%d.%m.%Y} ist bereits bestätigt. Eine weitere "
            "Zeitbuchung an diesem Tag ist nur mit Begründung möglich — die "
            "Bestätigung fällt damit zurück auf „Entwurf“ und muss erneut "
            "eingeholt werden."
        )


def _neuer_eintrag(actor, *, user_id, category_id, service_job_id, started_at, note=None,
                   auto_generated=False, ended_at=None):
    return TimeEntry.objects.create(
        id=uuid.uuid4(),
        user_id=user_id,
        category_id=category_id,
        service_job_id=service_job_id,
        started_at=started_at,
        ended_at=ended_at,
        note=note,
        auto_generated=auto_generated,
    )


def stempel_start(actor_app_user_id, *, category_id=None, service_job_id=None,
                  note=None, correction_reason=None, now=None):
    """Start: eine neue laufende Buchung. Nur, wenn nichts läuft.

    Zwei Tore VOR dem Insert, damit der Monteur nicht in eine Sackgasse läuft:

    * **Startzeitpunkt in einer bereits erfassten Zeit** (der EXCLUDE greift nur
      unter den abgeschlossenen Buchungen, 0066): die laufende Buchung ließe sich
      anlegen, aber **nie mehr stoppen** — jeder Stopp-Versuch verletzte den
      Constraint. Also sofort 422 statt später Sackgasse.
    * **Bestätigter Arbeitstag**: ohne Begründung 422 mit fachlicher Meldung
      (siehe `_tag_schloss_pruefen`), mit Begründung fällt der Tag regulär auf
      ENTWURF zurück.
    """
    now = now or timezone.now()
    laufend = aktueller_eintrag(actor_app_user_id)
    if laufend is not None:
        raise ValueError(
            f"Es läuft bereits eine Buchung ({laufend.category.name}, seit "
            f"{laufend.started_at.astimezone(BETRIEBS_TZ):%d.%m.%Y %H:%M}). "
            "Bitte zuerst stoppen."
        )
    treffer = _kollision(actor_app_user_id, now, now + timedelta(microseconds=1))
    if treffer is not None:
        raise ValueError(
            f"Für diesen Zeitpunkt ist bereits eine Zeit erfasst ({_spanne(treffer)}). "
            "Die Uhr kann nicht in eine erfasste Zeit hinein starten — sie ließe "
            "sich danach nicht mehr stoppen. Bitte korrigieren Sie zuerst diesen "
            "Eintrag."
        )
    cat = _resolve_kategorie(category_id) if category_id else standard_kategorie()
    if service_job_id is not None:
        ensure_exists(ServiceJob, service_job_id, "Einsatz")
    _tag_schloss_pruefen(actor_app_user_id, now, correction_reason)
    with as_business_error():
        with business_transaction(
            actor_app_user_id, correction_reason=correction_reason
        ):
            entry = _neuer_eintrag(
                actor_app_user_id,
                user_id=actor_app_user_id,
                category_id=cat.id,
                service_job_id=service_job_id,
                started_at=now,
                note=note,
            )
    return _reload(entry.id)


def stempel_pause(actor_app_user_id, *, correction_reason=None, now=None):
    """Pause: laufende Arbeitsbuchung beenden, Pausenbuchung starten."""
    now = now or timezone.now()
    laufend = _laufend_oder_fehler(actor_app_user_id)
    if not laufend.category.is_work_time:
        raise ValueError("Es läuft bereits eine Pause.")
    if now <= laufend.started_at:
        raise ValueError("Die Pause kann nicht vor dem Beginn der Buchung liegen.")
    _stopp_pruefen(laufend, now)
    _tag_schloss_pruefen(actor_app_user_id, now, correction_reason)
    pause = pausen_kategorie()
    with as_business_error():
        with business_transaction(
            actor_app_user_id, correction_reason=correction_reason
        ):
            laufend.ended_at = now
            laufend.save(update_fields=["ended_at"])
            entry = _neuer_eintrag(
                actor_app_user_id,
                user_id=actor_app_user_id,
                # Die Pause hängt bewusst NICHT am Einsatz: sie ist keine
                # Leistung am Auftrag und darf nicht in die Nachkalkulation.
                category_id=pause.id,
                service_job_id=None,
                started_at=now,
            )
    return _reload(entry.id)


def stempel_weiter(actor_app_user_id, *, correction_reason=None, now=None):
    """Weiter: Pause beenden, die Arbeit VOR der Pause fortschreiben."""
    now = now or timezone.now()
    laufend = _laufend_oder_fehler(actor_app_user_id)
    if laufend.category.is_work_time:
        raise ValueError("Es läuft keine Pause — „Weiter“ ist nur aus der Pause möglich.")
    if now <= laufend.started_at:
        raise ValueError("Das Fortsetzen kann nicht vor dem Pausenbeginn liegen.")
    _stopp_pruefen(laufend, now)
    _tag_schloss_pruefen(actor_app_user_id, now, correction_reason)

    # Die Buchung unmittelbar vor der Pause bestimmt Kategorie und Einsatz.
    davor = (
        TimeEntry.objects.select_related("category")
        .filter(
            user_id=actor_app_user_id,
            ended_at__lte=laufend.started_at,
            category__is_work_time=True,
        )
        .order_by("-ended_at")
        .first()
    )
    cat_id = davor.category_id if davor else standard_kategorie().id
    job_id = davor.service_job_id if davor else None

    with as_business_error():
        with business_transaction(
            actor_app_user_id, correction_reason=correction_reason
        ):
            laufend.ended_at = now
            laufend.save(update_fields=["ended_at"])
            entry = _neuer_eintrag(
                actor_app_user_id,
                user_id=actor_app_user_id,
                category_id=cat_id,
                service_job_id=job_id,
                started_at=now,
            )
    return _reload(entry.id)


def _stopp_pruefen(laufend, now):
    """Das Ende der laufenden Buchung gegen die erfassten Zeiten pruefen.

    Erst mit dem Ende faellt die Buchung unter den EXCLUDE-Constraint (0066).
    Die rohe Constraint-Meldung sagt nur „ueberschneidet sich" — sie sagt nicht,
    WELCHER Eintrag im Weg liegt und was zu tun ist. Genau das steht hier.
    """
    treffer = _kollision(laufend.user_id, laufend.started_at, now, ausser=laufend.id)
    if treffer is None:
        return
    beginn = laufend.started_at.astimezone(BETRIEBS_TZ)
    raise ValueError(
        f"Die laufende Buchung (seit {beginn:%d.%m.%Y %H:%M}) überschneidet sich "
        f"mit einer bereits erfassten Zeit ({_spanne(treffer)}). Bitte korrigieren "
        "oder löschen Sie diesen Eintrag — danach lässt sich die Uhr stoppen. "
        "Alternativ können Sie die laufende Buchung löschen."
    )


def stempel_stopp(actor_app_user_id, *, correction_reason=None, now=None):
    """Stopp: die laufende Buchung beenden."""
    now = now or timezone.now()
    laufend = _laufend_oder_fehler(actor_app_user_id)
    if now <= laufend.started_at:
        raise ValueError("Das Ende kann nicht vor dem Beginn liegen.")
    _stopp_pruefen(laufend, now)
    with as_business_error():
        with business_transaction(
            actor_app_user_id, correction_reason=correction_reason
        ):
            laufend.ended_at = now
            laufend.save(update_fields=["ended_at"])
    return _reload(laufend.id)


def _reload(entry_id):
    return TimeEntry.objects.select_related(
        "category", "service_job", "work_day", "user"
    ).get(id=entry_id)


# ---------------------------------------------------------------------------
# Zeiteinträge (Erfassung/Korrektur von Hand)
# ---------------------------------------------------------------------------

def zeiteintrag_anlegen(
    actor_app_user_id, *, user_id, category_id, started_at, ended_at,
    service_job_id=None, note=None, correction_reason=None,
):
    if ended_at is None:
        raise ValueError("Ein erfasster Eintrag braucht ein Ende.")
    if ended_at <= started_at:
        raise ValueError("Das Ende muss nach dem Beginn liegen.")
    cat = _resolve_kategorie(category_id)
    ensure_exists(AppUser, user_id, "Mitarbeiter")
    if service_job_id is not None:
        ensure_exists(ServiceJob, service_job_id, "Einsatz")
    with as_business_error():
        with business_transaction(
            actor_app_user_id, correction_reason=correction_reason
        ):
            entry = _neuer_eintrag(
                actor_app_user_id,
                user_id=user_id,
                category_id=cat.id,
                service_job_id=service_job_id,
                started_at=started_at,
                ended_at=ended_at,
                note=note,
            )
    return _reload(entry.id)


def zeiteintrag_aendern(
    actor_app_user_id, *, entry_id, category_id=None, started_at=None, ended_at=None,
    note=None, correction_reason=None,
):
    """Ändert einen Eintrag. Der Einsatzbezug ist unveränderlich (B-28/P3-03) —
    Korrektur = Löschen im Fenster + Neuerfassung."""
    entry = TimeEntry.objects.select_related("category").filter(id=entry_id).first()
    if entry is None:
        raise ValueError("Unbekannter Zeiteintrag.")
    if category_id is not None:
        entry.category = _resolve_kategorie(category_id)
    if started_at is not None:
        entry.started_at = started_at
    if ended_at is not None:
        entry.ended_at = ended_at
    if note is not None:
        entry.note = note.strip() or None
    if entry.ended_at is not None and entry.ended_at <= entry.started_at:
        raise ValueError("Das Ende muss nach dem Beginn liegen.")
    with as_business_error():
        with business_transaction(
            actor_app_user_id, correction_reason=correction_reason
        ):
            entry.save(
                update_fields=["category", "started_at", "ended_at", "note"]
            )
    return _reload(entry.id)


def zeiteintrag_loeschen(actor_app_user_id, *, entry_id, correction_reason=None):
    """Löschen bleibt im B-28-Fenster erlaubt; das Vorher-Bild landet im Audit
    (`audit.audit_row_delete`, Migration 0017)."""
    entry = TimeEntry.objects.filter(id=entry_id).first()
    if entry is None:
        raise ValueError("Unbekannter Zeiteintrag.")
    with as_business_error():
        with business_transaction(
            actor_app_user_id, correction_reason=correction_reason
        ):
            entry.delete()


# ---------------------------------------------------------------------------
# Arbeitstag: Klammer, Einreichen, Freigabe
# ---------------------------------------------------------------------------

def arbeitstag(work_day_id):
    return (
        WorkDay.objects.select_related("user", "decided_by")
        .filter(id=work_day_id)
        .first()
    )


def arbeitstage(user_id=None, von=None, bis=None, status=None):
    qs = WorkDay.objects.select_related("user", "decided_by")
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    if von is not None:
        qs = qs.filter(day__gte=von)
    if bis is not None:
        qs = qs.filter(day__lte=bis)
    if status:
        qs = qs.filter(status=status)
    return qs.order_by("-day", "user__display_name")


def eintraege_am_tag(work_day_id):
    return list(
        TimeEntry.objects.select_related("category", "service_job", "user")
        .filter(work_day_id=work_day_id)
        .order_by("started_at")
    )


def tages_summen(entries):
    """Arbeitszeit/Pause je Tag (Sekunden) — laufende Buchungen zählen NICHT mit.

    Eine laufende Buchung hat kein Ende; sie in die Summe einzurechnen hieße,
    ein Ende zu erfinden. Das UI zeigt sie separat als „läuft".
    """
    arbeit = timedelta()
    pause = timedelta()
    laeuft = False
    for e in entries:
        if e.ended_at is None:
            laeuft = True
            continue
        dauer = e.ended_at - e.started_at
        if e.category.is_work_time:
            arbeit += dauer
        else:
            pause += dauer
    return {
        "arbeit_sekunden": int(arbeit.total_seconds()),
        "pause_sekunden": int(pause.total_seconds()),
        "laeuft": laeuft,
    }


def arbeitstag_einreichen(actor_app_user_id, *, work_day_id):
    tag = arbeitstag(work_day_id)
    if tag is None:
        raise ValueError("Unbekannter Arbeitstag.")
    if tag.status not in ("ENTWURF", "ABGELEHNT"):
        raise ValueError(
            f"Ein Arbeitstag im Status {tag.status} kann nicht eingereicht werden."
        )
    entries = eintraege_am_tag(work_day_id)
    if not entries:
        raise ValueError("Der Arbeitstag enthält keine Zeitbuchung.")
    if any(e.ended_at is None for e in entries):
        raise ValueError(
            "Es läuft noch eine Buchung an diesem Tag. Bitte zuerst stoppen."
        )
    tag.status = "EINGEREICHT"
    tag.submitted_at = timezone.now()
    tag.decided_by_id = None
    tag.decided_at = None
    tag.decision_note = None
    with as_business_error():
        with business_transaction(actor_app_user_id):
            tag.save(
                update_fields=[
                    "status", "submitted_at", "decided_by", "decided_at",
                    "decision_note",
                ]
            )
    return arbeitstag(work_day_id)


def arbeitstag_bestaetigen(actor_app_user_id, *, work_day_id):
    """Bestätigen — Führungsaufgabe. Vier-Augen: nie der eigene Tag (DB-Trigger)."""
    tag = arbeitstag(work_day_id)
    if tag is None:
        raise ValueError("Unbekannter Arbeitstag.")
    if tag.status != "EINGEREICHT":
        raise ValueError(
            f"Nur ein eingereichter Arbeitstag kann bestätigt werden "
            f"(Status: {tag.status})."
        )
    if tag.user_id == actor_app_user_id:
        raise ValueError(
            "Der eigene Arbeitstag kann nicht selbst bestätigt werden "
            "(Vier-Augen-Prinzip)."
        )
    tag.status = "BESTAETIGT"
    tag.decided_by_id = actor_app_user_id
    tag.decided_at = timezone.now()
    with as_business_error():
        with business_transaction(actor_app_user_id):
            tag.save(update_fields=["status", "decided_by", "decided_at"])
    return arbeitstag(work_day_id)


def arbeitstag_ablehnen(actor_app_user_id, *, work_day_id, note):
    if not note or not note.strip():
        raise ValueError("Die Ablehnung ist begründungspflichtig.")
    tag = arbeitstag(work_day_id)
    if tag is None:
        raise ValueError("Unbekannter Arbeitstag.")
    if tag.status != "EINGEREICHT":
        raise ValueError(
            f"Nur ein eingereichter Arbeitstag kann abgelehnt werden "
            f"(Status: {tag.status})."
        )
    if tag.user_id == actor_app_user_id:
        raise ValueError(
            "Der eigene Arbeitstag kann nicht selbst abgelehnt werden "
            "(Vier-Augen-Prinzip)."
        )
    tag.status = "ABGELEHNT"
    tag.decided_by_id = actor_app_user_id
    tag.decided_at = timezone.now()
    tag.decision_note = note.strip()
    with as_business_error():
        # Der Statusübergang EINGEREICHT → ABGELEHNT ist begründungspflichtig
        # (workflow.status_transition, Migration 0067).
        with business_transaction(actor_app_user_id, status_reason=note.strip()):
            tag.save(
                update_fields=["status", "decided_by", "decided_at", "decision_note"]
            )
    return arbeitstag(work_day_id)


# ---------------------------------------------------------------------------
# Pausen-Engine
# ---------------------------------------------------------------------------

def _soll_pause(arbeit):
    for grenze, pflicht in _ARBZG_STUFEN:
        if arbeit > grenze:
            return pflicht
    return timedelta()


def _fenster_fuer_tag(regel, tag):
    return [
        (_local_dt(tag, *map(int, w["von"].split(":"))),
         _local_dt(tag, *map(int, w["bis"].split(":"))))
        for w in (regel.fixed_breaks or [])
    ]


def _ohne(intervall, belegt):
    """[von, bis) abzueglich der Intervalle in `belegt` — als Liste von Stuecken."""
    stuecke = [intervall]
    for b_von, b_bis in belegt:
        naechste = []
        for s, t in stuecke:
            if b_bis <= s or b_von >= t:
                naechste.append((s, t))
                continue
            if s < b_von:
                naechste.append((s, b_von))
            if b_bis < t:
                naechste.append((b_bis, t))
        stuecke = naechste
    return stuecke


def _feste_intervalle(regel, tag, entries):
    """Die zu schneidenden Intervalle im Modus FESTE_ZEITEN.

    Naiv waere: die konfigurierten Fenster blind herausschneiden. Das ist falsch,
    sobald der Mitarbeiter seine Pause SELBST gestempelt hat. Beispiel (Review):
    Fenster 12:00–12:30, gestempelte Pause 12:15–12:45 → der blinde Schnitt
    widmet zusaetzlich 12:00–12:15 zu Pause um: 45 min Pause statt 30, 15 min
    Arbeitszeit vernichtet. Das ist eine Falschaussage in einer nach § 17 MiLoG
    aufzeichnungspflichtigen Erfassung.

    Richtig: das Fenster gibt den **Umfang** der betrieblichen Pause vor
    (`bis − von`). Eine gestempelte Pause, die das Fenster beruehrt, IST die
    Pause dieses Fensters — sie zaehlt mit ihrer vollen Dauer an. Nur der
    Fehlbetrag wird noch geschnitten, und zwar ausschliesslich aus den Teilen des
    Fensters, in denen wirklich gearbeitet wurde. Ohne gestempelte Pause bleibt
    es beim bisherigen Verhalten: das ganze Fenster wird umgewidmet.
    """
    pausen = [
        (e.started_at, e.ended_at)
        for e in entries
        if e.ended_at is not None and not e.category.is_work_time
    ]
    intervalle = []
    zugeordnet = set()
    for von, bis in _fenster_fuer_tag(regel, tag):
        bereits = timedelta()
        for i, (p_von, p_bis) in enumerate(pausen):
            if i in zugeordnet or p_bis <= von or p_von >= bis:
                continue
            zugeordnet.add(i)
            bereits += p_bis - p_von
        fehlend = (bis - von) - bereits
        if fehlend <= timedelta():
            continue
        rest = fehlend
        for s, t in _ohne((von, bis), pausen):
            nimm = min(rest, t - s)
            if nimm <= timedelta():
                continue
            intervalle.append((s, s + nimm))
            rest -= nimm
            if rest <= timedelta():
                break
    intervalle.sort()
    return intervalle


def _carve(actor, entries, intervalle, pause_cat_id):
    """Schneidet Intervalle aus den Arbeitsbuchungen heraus und legt dort
    auto-generierte Pausen an.

    Das ist der ehrliche Weg, eine Pflichtpause „abzuziehen": die betroffene
    Arbeitszeit wird zu Pause umgewidmet, statt hinten eine Pause anzuhängen
    (was die Anwesenheit künstlich verlängerte). Jede so entstandene Buchung
    trägt `auto_generated = true` und ist im UI gekennzeichnet.
    """
    neu = []
    for von, bis in intervalle:
        for e in list(entries):
            if e.ended_at is None or not e.category.is_work_time or e.auto_generated:
                continue
            if e.ended_at <= von or e.started_at >= bis:
                continue
            s, t = e.started_at, e.ended_at
            schnitt_von = max(s, von)
            schnitt_bis = min(t, bis)
            if schnitt_bis <= schnitt_von:
                continue
            # Kopf behalten (oder Eintrag ganz auflösen)
            if schnitt_von > s:
                e.ended_at = schnitt_von
                e.save(update_fields=["ended_at"])
            else:
                e.delete()
                entries.remove(e)
            # Schwanz als neuer Eintrag
            if t > schnitt_bis:
                rest = TimeEntry.objects.create(
                    id=uuid.uuid4(),
                    user_id=e.user_id,
                    category_id=e.category_id,
                    service_job_id=e.service_job_id,
                    started_at=schnitt_bis,
                    ended_at=t,
                    note=e.note,
                )
                rest.category = e.category
                entries.append(rest)
            p = TimeEntry.objects.create(
                id=uuid.uuid4(),
                user_id=e.user_id,
                category_id=pause_cat_id,
                service_job_id=None,
                started_at=schnitt_von,
                ended_at=schnitt_bis,
                auto_generated=True,
                note="Automatisch eingefügte Pause",
            )
            neu.append(p)
            entries.sort(key=lambda x: x.started_at)
    return neu


def pausen_regel_anwenden(actor_app_user_id, *, work_day_id, correction_reason=None):
    """Fügt die fehlenden Pflichtpausen ein (idempotent).

    GESETZLICH: fehlt Ruhezeit nach ArbZG § 4, wird sie vom **Ende** der
    Arbeitszeit des Tages abgeschnitten und als Pause umgewidmet. Der Betrieb
    kann sie danach von Hand korrigieren (die Buchung ist ein ganz normaler
    Eintrag, nur mit `auto_generated = true`).

    Volle Stufe, keine Kappung (Entscheidung zum Review-Befund S8)
    -------------------------------------------------------------
    Bei 6 h 01 brutto werden die vollen 30 min abgeschnitten (→ 5 h 31 netto) und
    NICHT auf `min(30 min, arbeit − 6 h)` = 1 min gekappt. Gruende:

    * ArbZG § 4 Satz 2 kennt Ruhepausen nur in Abschnitten von **mindestens
      15 Minuten**. Eine gekappte 1-Minuten-„Pause" waere keine Ruhepause,
      sondern eine erfundene Zahl — genau das, was eine MiLoG-Aufzeichnung nicht
      enthalten darf.
    * Die Kappung ist ausserdem zirkulaer: sie drueckt die Nettoarbeitszeit auf
      exakt 6 h, womit die Pausenpflicht rechnerisch entfaellt, die sie gerade
      erst ausgeloest hat.
    * Die Praemisse dieser Funktion ist „die Pause WURDE genommen, nur nicht
      gestempelt". Unter dieser Praemisse ist die gesetzliche Mindestdauer der
      ehrliche Wert. Wurde sie NICHT genommen, ist jede automatische Pause eine
      Falschaussage — dann darf der Betrieb die Regel nicht anwenden (Modus
      KEINE) und muss den ArbZG-Verstoss stehenlassen.

    Wer weniger abziehen will, korrigiert die (als `auto_generated` markierte)
    Buchung von Hand — das ist der vorgesehene, nachvollziehbare Weg.

    FESTE_ZEITEN: die konfigurierten Fenster werden aus der Arbeitszeit
    herausgeschnitten — abzueglich dessen, was der Mitarbeiter dort bereits
    selbst als Pause gestempelt hat (`_feste_intervalle`).
    """
    tag = arbeitstag(work_day_id)
    if tag is None:
        raise ValueError("Unbekannter Arbeitstag.")
    regel = pausenregel()
    if regel is None or regel.mode == "KEINE":
        return []
    pause_cat = pausen_kategorie()

    with as_business_error():
        with business_transaction(
            actor_app_user_id, correction_reason=correction_reason
        ):
            entries = eintraege_am_tag(work_day_id)
            if any(e.ended_at is None for e in entries):
                raise ValueError(
                    "Es läuft noch eine Buchung an diesem Tag — Pausen können erst "
                    "danach eingerechnet werden."
                )
            summen = tages_summen(entries)
            arbeit = timedelta(seconds=summen["arbeit_sekunden"])
            pause = timedelta(seconds=summen["pause_sekunden"])

            if regel.mode == "FESTE_ZEITEN":
                intervalle = _feste_intervalle(regel, tag.day, entries)
                if not intervalle:
                    return []
            else:
                fehlend = _soll_pause(arbeit) - pause
                if fehlend <= timedelta():
                    return []
                # Vom Ende der Arbeitszeit her abschneiden.
                arbeitsbuchungen = [
                    e for e in entries
                    if e.ended_at and e.category.is_work_time and not e.auto_generated
                ]
                if not arbeitsbuchungen:
                    return []
                intervalle = []
                rest = fehlend
                for e in sorted(arbeitsbuchungen, key=lambda x: x.ended_at, reverse=True):
                    dauer = e.ended_at - e.started_at
                    nimm = min(rest, dauer)
                    if nimm <= timedelta():
                        break
                    intervalle.append((e.ended_at - nimm, e.ended_at))
                    rest -= nimm
                    if rest <= timedelta():
                        break
                intervalle.sort()

            return _carve(actor_app_user_id, entries, intervalle, pause_cat.id)


# ---------------------------------------------------------------------------
# Stundenausgleich (hr.time_adjustment, Migration 0072)
# ---------------------------------------------------------------------------
# Der Saldo bleibt ABGELEITET. Ein Ausgleich ist kein „Saldo überschreiben"
# (es gibt keinen gespeicherten Saldo, den man überschreiben könnte), sondern
# die dritte Größe der Formel:
#
#     Saldo = Ist − Soll + Σ Ausgleich
#
# Deshalb steht hier nur die Buchung; gerechnet wird weiterhin in
# `stundenkonto()`.


def _ausgleich_qs(employee_id=None, von=None, bis=None, nur_wirksam=False):
    qs = TimeAdjustment.objects.select_related(
        "employee", "employee__party", "created_by", "reversal_of"
    )
    if employee_id is not None:
        qs = qs.filter(employee_id=employee_id)
    if von is not None:
        qs = qs.filter(effective_on__gte=von)
    if bis is not None:
        qs = qs.filter(effective_on__lte=bis)
    if nur_wirksam:
        # Die Summe läuft NUR über die wirksamen Buchungen: nicht storniert und
        # selbst kein Storno. Beide Zeilen eines Storno-Vorgangs bleiben stehen
        # (GoBD), aber keine von beiden zählt noch.
        qs = qs.filter(status="GEBUCHT", reversal_of__isnull=True)
    return qs


def ausgleiche(employee_id=None, von=None, bis=None):
    return list(_ausgleich_qs(employee_id, von, bis).order_by("-effective_on", "-created_at"))


def ausgleich_minuten(employee_id, von, bis):
    """Σ der wirksamen Ausgleichsminuten im Zeitraum (vorzeichenbehaftet)."""
    summe = (
        _ausgleich_qs(employee_id, von, bis, nur_wirksam=True)
        .values_list("minutes", flat=True)
    )
    return sum(summe)


def _minuten_zu_stunden(minuten):
    return (Decimal(minuten) / Decimal(60)).quantize(Decimal("0.01"))


def _kein_eigenes_konto(actor_app_user_id, employee_id):
    """Niemand bewegt sein EIGENES Arbeitszeitkonto — auch nicht per Storno.

    Ein Storno **ist** eine Ausgleichsbuchung: eigene Zeile in derselben Tabelle,
    negierte Minuten, wirkt auf denselben abgeleiteten Saldo. Ohne diese Prüfung
    im Storno-Pfad war das Tor der Buchung wertlos: Der Geschäftsführer bucht sich
    −30 h (422 — geht nicht) … aber er storniert die Buchung eines Kollegen auf
    SEINEM Konto und schreibt sich damit +30 h gut (Review-Befund A1,
    per HTTP reproduziert).

    Deshalb liegt die Regel an EINER Stelle und gilt für JEDEN Schreibpfad — und
    zusätzlich physisch im Trigger `hr.enforce_time_adjustment` (Migration 0075),
    damit sie auch an der Service-Schicht vorbei nicht umgehbar ist.
    """
    eigener = Employee.objects.filter(app_user_id=actor_app_user_id).values_list(
        "id", flat=True
    ).first()
    if eigener is not None and eigener == employee_id:
        raise ValueError(
            "Das eigene Arbeitszeitkonto kann nicht selbst ausgeglichen werden — "
            "der Stundenausgleich ist eine Führungsentscheidung (Vier-Augen-Prinzip). "
            "Das gilt auch für das Stornieren einer Buchung auf dem eigenen Konto."
        )


def ausgleich_buchen(
    actor_app_user_id, *, employee_id, adjustment_type, effective_on, minutes, reason
):
    """Bucht einen Stundenausgleich auf das Arbeitszeitkonto.

    Vier Tore, alle auch physisch in der DB (Migration 0072):

    * **Begründung ist Pflicht.** Eine Kontobewegung ohne Grund ist für den
      Beschäftigten nicht nachvollziehbar und für die Prüfung wertlos.
    * **0 Minuten ist keine Buchung** (Rauschen in der Aufzeichnung).
    * **Niemand gleicht sein eigenes Konto aus.** Der Ausgleich ist eine
      Führungsentscheidung des Arbeitgebers — sonst schriebe sich der
      Beschäftigte seine Minusstunden selbst weg. (Das Recht `hr/AENDERN` mit
      row_scope ALLE hält den Monteur ohnehin draußen; dieses Tor greift auch
      gegen den Geschäftsführer, der zugleich Mitarbeiter ist.)
    * **Der Mitarbeiter muss existieren** (sonst IntegrityError → 500).
    """
    if adjustment_type not in ADJUSTMENT_TYPES:
        raise ValueError(
            f"Unbekannte Ausgleichsart. Erlaubt: {', '.join(ADJUSTMENT_TYPES)}."
        )
    if not (reason or "").strip():
        raise ValueError("Eine Ausgleichsbuchung ist begründungspflichtig.")
    minutes = int(minutes)
    if minutes == 0:
        raise ValueError("Der Ausgleich muss von null verschieden sein.")
    if abs(minutes) > 600_000:
        raise ValueError("Der Ausgleich ist unplausibel groß (max. 10.000 Stunden).")

    emp = Employee.objects.filter(id=employee_id).first()
    if emp is None:
        raise ValueError("Unbekannter Mitarbeiter.")
    _kein_eigenes_konto(actor_app_user_id, emp.id)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            eintrag = TimeAdjustment.objects.create(
                id=uuid.uuid4(),
                employee_id=emp.id,
                adjustment_type=adjustment_type,
                effective_on=effective_on,
                minutes=minutes,
                reason=reason.strip(),
                status="GEBUCHT",
                reversal_of_id=None,
                created_by_id=actor_app_user_id,
            )
    return _ausgleich_reload(eintrag.id)


def ausgleich_stornieren(actor_app_user_id, *, adjustment_id, reason):
    """Storniert eine Ausgleichsbuchung — append-only, kein Löschen (GoBD).

    Es entsteht eine **zweite Zeile** mit den negierten Minuten und einem Verweis
    auf die Ursprungsbuchung; die Ursprungsbuchung geht auf STORNIERT. Beide
    bleiben sichtbar, beide fallen aus der Summe. Der Trigger erzwingt das
    Vorzeichen, den Mitarbeiter und die Einmaligkeit.

    **Das Vier-Augen-Tor gilt hier genauso** (`_kein_eigenes_konto`): Ein Storno
    ist eine Kontobewegung wie jede andere — wer sein Konto nicht selbst
    ausgleichen darf, darf es auch nicht durch Stornieren einer fremden Buchung
    tun.
    """
    if not (reason or "").strip():
        raise ValueError("Ein Storno ist begründungspflichtig.")
    original = TimeAdjustment.objects.filter(id=adjustment_id).first()
    if original is None:
        raise ValueError("Unbekannte Ausgleichsbuchung.")
    if original.reversal_of_id is not None:
        raise ValueError("Eine Storno-Buchung kann nicht storniert werden.")
    if original.status != "GEBUCHT":
        raise ValueError("Die Buchung ist bereits storniert.")
    _kein_eigenes_konto(actor_app_user_id, original.employee_id)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            storno = TimeAdjustment.objects.create(
                id=uuid.uuid4(),
                employee_id=original.employee_id,
                adjustment_type=original.adjustment_type,
                effective_on=original.effective_on,
                minutes=-original.minutes,
                reason=reason.strip(),
                status="GEBUCHT",
                reversal_of_id=original.id,
                created_by_id=actor_app_user_id,
            )
    return _ausgleich_reload(storno.id)


def _ausgleich_reload(adjustment_id):
    return TimeAdjustment.objects.select_related(
        "employee", "employee__party", "created_by", "reversal_of"
    ).get(id=adjustment_id)


# ---------------------------------------------------------------------------
# Stundenkonto — abgeleitet, nie gespeichert
# ---------------------------------------------------------------------------

def _contract_on(contracts, day):
    for c in contracts:
        if c.valid_from <= day and (c.valid_to is None or c.valid_to >= day):
            return c
    return None


def _soll_stunden(contract, day):
    if contract is None:
        return Decimal("0")
    return Decimal(getattr(contract, _WEEKDAY_FIELDS[day.weekday()]))


def stundenkonto(employee_id, von, bis):
    """Soll/Ist/Ausgleich/Saldo für einen Mitarbeiter und Zeitraum.

    Soll      = Vertragsraster je Kalendertag
                − Feiertage (bundesweit + Bundesland des Firmenprofils)
                − genehmigte Abwesenheiten (halbe Randtage zählen 0,5)
    Ist       = Summe aller Buchungen mit `category.is_work_time` (also Arbeit,
                Fahrt, Bereitschaft, Nacharbeit, Werkstatt … — NICHT nur
                'ARBEITSZEIT', wie es die alte Auswertung tat)
    Ausgleich = Σ der wirksamen Ausgleichsbuchungen (hr.time_adjustment, 0072),
                deren Verbuchungszeitpunkt im Zeitraum liegt — vorzeichenbehaftet
    Saldo     = Ist + Abwesenheitsstunden − Soll + Ausgleich

    Nichts davon ist gespeichert — auch der Saldo nicht. Die Ausgleichsbuchung
    ist eine **Buchung**, kein gespeicherter Saldo: Sie ändert die Formel, nicht
    die Konvention. Gleiche Linie wie offener Rechnungsbetrag und
    Urlaubsverbrauch.
    """
    emp = Employee.objects.select_related("app_user").filter(id=employee_id).first()
    if emp is None:
        raise ValueError("Unbekannter Mitarbeiter.")

    contracts = list(EmploymentContract.objects.filter(employee_id=employee_id))
    feier = feiertage(von, bis)

    absences = list(
        Absence.objects.filter(
            employee_id=employee_id,
            status="GENEHMIGT",
            start_date__lte=bis,
            end_date__gte=von,
        )
    )

    soll = Decimal("0")
    abwesend = Decimal("0")
    tag = von
    while tag <= bis:
        contract = _contract_on(contracts, tag)
        stunden = _soll_stunden(contract, tag)
        if stunden > 0 and tag not in feier:
            soll += stunden
            faktor = _abwesenheits_faktor(absences, tag)
            abwesend += stunden * faktor
        tag += timedelta(days=1)

    # Ist: alle Arbeitszeit-Kategorien; laufende Buchungen bleiben außen vor.
    entries = TimeEntry.objects.select_related("category").filter(
        user_id=emp.app_user_id,
        ended_at__isnull=False,
        work_day__day__gte=von,
        work_day__day__lte=bis,
    )
    ist_sek = 0
    pause_sek = 0
    for e in entries:
        d = int((e.ended_at - e.started_at).total_seconds())
        if e.category.is_work_time:
            ist_sek += d
        else:
            pause_sek += d
    ist = (Decimal(ist_sek) / Decimal(3600)).quantize(Decimal("0.01"))
    pause = (Decimal(pause_sek) / Decimal(3600)).quantize(Decimal("0.01"))

    tage = list(arbeitstage(user_id=emp.app_user_id, von=von, bis=bis))
    offen = sum(1 for t in tage if t.status in ("ENTWURF", "ABGELEHNT"))

    ausgleich_min = ausgleich_minuten(employee_id, von, bis)
    ausgleich = _minuten_zu_stunden(ausgleich_min)

    soll = soll.quantize(Decimal("0.01"))
    abwesend = abwesend.quantize(Decimal("0.01"))
    saldo = (ist + abwesend - soll + ausgleich).quantize(Decimal("0.01"))
    return {
        "employee_id": emp.id,
        "von": von,
        "bis": bis,
        "soll": soll,
        "ist": ist,
        "pause": pause,
        "abwesend": abwesend,
        "ausgleich": ausgleich,
        "saldo": saldo,
        "tage_gesamt": len(tage),
        "tage_offen": offen,
        "tage_bestaetigt": sum(1 for t in tage if t.status == "BESTAETIGT"),
        "tage_eingereicht": sum(1 for t in tage if t.status == "EINGEREICHT"),
    }


def _abwesenheits_faktor(absences, tag):
    """0 = anwesend, 0.5 = halber Tag, 1 = ganzer Tag abwesend."""
    for a in absences:
        if a.start_date <= tag <= a.end_date:
            if a.half_day_start and tag == a.start_date:
                return Decimal("0.5")
            if a.half_day_end and tag == a.end_date:
                return Decimal("0.5")
            return Decimal("1")
    return Decimal("0")


# ---------------------------------------------------------------------------
# Export (Vorlagepflicht § 17 MiLoG)
# ---------------------------------------------------------------------------

def stundenliste(von, bis, user_id=None):
    """Zeilen für den CSV-Export: eine Zeile je Zeitbuchung.

    § 17 Abs. 1 MiLoG verlangt **Beginn, Ende und Dauer** der täglichen
    Arbeitszeit und die Vorlage an die Zollbehörde. Genau das steht hier —
    zusätzlich Kategorie, Arbeitszeit-Kennzeichen, Einsatz und der Status des
    Arbeitstages (damit erkennbar ist, was der Arbeitgeber bestätigt hat).
    """
    qs = (
        TimeEntry.objects.select_related(
            "category", "user", "work_day", "work_day__decided_by", "service_job"
        )
        .filter(work_day__day__gte=von, work_day__day__lte=bis)
        .order_by("user__display_name", "started_at")
    )
    if user_id is not None:
        qs = qs.filter(user_id=user_id)

    zeilen = []
    for e in qs:
        dauer = (
            (e.ended_at - e.started_at).total_seconds() / 3600 if e.ended_at else None
        )
        zeilen.append(
            {
                "mitarbeiter": e.user.display_name,
                "tag": e.work_day.day,
                "beginn": e.started_at.astimezone(BETRIEBS_TZ),
                "ende": e.ended_at.astimezone(BETRIEBS_TZ) if e.ended_at else None,
                "dauer_stunden": (
                    Decimal(str(round(dauer, 2))) if dauer is not None else None
                ),
                "kategorie": e.category.name,
                "arbeitszeit": e.category.is_work_time,
                "automatisch": e.auto_generated,
                "einsatz": e.service_job.job_number if e.service_job_id else "",
                "tagesstatus": e.work_day.status,
                "bestaetigt_von": (
                    e.work_day.decided_by.display_name
                    if e.work_day.decided_by_id
                    else ""
                ),
                "notiz": e.note or "",
            }
        )
    return zeilen
