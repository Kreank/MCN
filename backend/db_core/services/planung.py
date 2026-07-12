"""Planungs-Stammdaten-Service: Terminkategorien (workflow.appointment_category)
und Ressourcen (resource.resource) anlegen/ändern/archivieren sowie Ressourcen
einem Einsatz zuordnen (resource.job_resource).

Wie die übrigen Services laufen alle Writes über business_transaction (setzt
app.current_user_id für Audit). Codelisten/Wertebereiche werden vorab geprüft
(klarer ValueError → 422 statt IntegrityError → 500). Statuswechsel spiegeln die
DB-Trigger (0025) und werden dort zusätzlich physisch erzwungen.

Doppelbelegung einer Ressource ist auf DB-Ebene NICHT hart gesperrt (offene
Invariante, siehe Migration 0025): der service_job-Zeitraum ist nullable und
liegt in einer anderen Tabelle, eine saubere EXCLUDE-Constraint ist damit nicht
möglich. `assign_resource` liefert stattdessen einen NICHT-blockierenden
Warnhinweis, wenn sich bekannte Zeitfenster überlappen — die Zuordnung wird
trotzdem angelegt (Hero-Parität: Überlappung sichtbar, aber nicht verboten).
"""
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Absence,
    AppointmentCategory,
    AppUser,
    EmploymentContract,
    Holiday,
    JobAssignment,
    JobResource,
    Resource,
    ServiceJob,
)
from db_core.services import einsatz as einsatz_service
from db_core.services._validation import ensure_exists

# Die Plantafel ist ein Disponentenwerkzeug für einen deutschen Handwerksbetrieb:
# „Montag" ist der Montag in Europa/Berlin, nicht in UTC (settings.TIME_ZONE ist
# bewusst UTC, weil die Nummernkreise das so verlangen). Nur die ABLEITUNG von
# Kalendertagen aus Zeitstempeln läuft deshalb hier über die Betriebszeitzone;
# gespeichert wird unverändert timestamptz.
BOARD_TZ = ZoneInfo("Europe/Berlin")

# Sentinel für Teil-Updates am Termin: „Feld nicht mitgeschickt" ist etwas anderes
# als „Feld ausdrücklich auf NULL setzen" (Kategorie entfernen, Kontakt löschen).
_UNSET_TERMIN = object()

# Geschlossene Farb-Codeliste (spiegelt den CHECK in Migration 0025). Das UI
# bildet jeden Token WCAG-sicher auf ein Farbschema ab (dekorativer Punkt +
# IMMER den Kategorienamen als Text); freie Hex-Werte sind ausgeschlossen.
COLOR_TOKENS = ("NAVY", "ORANGE", "SAGE", "AMBER", "TEAL", "PLUM", "ROSE", "SLATE")

RESOURCE_TYPES = ("FAHRZEUG", "GERAET", "RAUM", "SONSTIGE")

# Erlaubte Ressourcen-Statuswechsel → {Zielstatus}. Spiegelt den DB-Trigger
# resource.enforce_resource_status.
RESOURCE_TRANSITIONS = {
    "AKTIV": {"INAKTIV"},
    "INAKTIV": {"AKTIV", "ARCHIVIERT"},
    "ARCHIVIERT": set(),
}


# ===========================================================================
# Terminkategorie
# ===========================================================================

def _active_name_taken(name, *, exclude_id=None):
    """True, wenn bereits eine AKTIVE Kategorie den Namen (case-insensitiv)
    trägt — spiegelt den Unique-Index uq_appointment_category_active_name, damit
    die Kollision als klarer ValueError (→422) statt IntegrityError (→500) endet."""
    qs = AppointmentCategory.objects.filter(status="AKTIV", name__iexact=name.strip())
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    return qs.exists()


def create_category(
    actor_app_user_id, *, name, color_token="NAVY", description=None, sort_order=0
):
    """Legt eine Terminkategorie (Status AKTIV) an."""
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    if color_token not in COLOR_TOKENS:
        raise ValueError(
            f"Ungültige color_token '{color_token}'. "
            f"Erlaubt: {', '.join(COLOR_TOKENS)}."
        )
    if _active_name_taken(name):
        raise ValueError(f"Eine aktive Kategorie '{name.strip()}' existiert bereits.")
    if sort_order is None:
        sort_order = 0
    with as_business_error():
        with business_transaction(actor_app_user_id):
            category = AppointmentCategory.objects.create(
                id=uuid.uuid4(),
                name=name.strip(),
                description=(description.strip() if description else None) or None,
                color_token=color_token,
                status="AKTIV",
                sort_order=sort_order,
                created_by_id=actor_app_user_id,
                version=1,
            )
            category.refresh_from_db()
    return category


def update_category(
    actor_app_user_id,
    *,
    category_id,
    name=None,
    color_token=None,
    description=None,
    sort_order=None,
):
    """Ändert Name/Farbe/Beschreibung/Sortierung einer Kategorie. Umbenennen
    wirkt auf bestehende Termine (die FK bleibt bestehen) — wie in Hero."""
    category = AppointmentCategory.objects.filter(id=category_id).first()
    if category is None:
        raise ValueError("Terminkategorie nicht gefunden.")
    if category.status != "AKTIV":
        raise ValueError("Archivierte Kategorien können nicht geändert werden.")
    fields = {}
    if name is not None:
        if not name.strip():
            raise ValueError("name darf nicht leer sein.")
        if _active_name_taken(name, exclude_id=category_id):
            raise ValueError(
                f"Eine aktive Kategorie '{name.strip()}' existiert bereits."
            )
        fields["name"] = name.strip()
    if color_token is not None:
        if color_token not in COLOR_TOKENS:
            raise ValueError(
                f"Ungültige color_token '{color_token}'. "
                f"Erlaubt: {', '.join(COLOR_TOKENS)}."
            )
        fields["color_token"] = color_token
    if description is not None:
        fields["description"] = description.strip() or None
    if sort_order is not None:
        fields["sort_order"] = sort_order
    if not fields:
        return category
    with as_business_error():
        with business_transaction(actor_app_user_id):
            AppointmentCategory.objects.filter(id=category_id).update(**fields)
    category.refresh_from_db()
    return category


def archive_category(actor_app_user_id, *, category_id):
    """Archiviert eine Kategorie (statt Löschen). Bestehende Termine behalten
    die Kategorie; für neue Termine steht sie nicht mehr zur Wahl."""
    category = AppointmentCategory.objects.filter(id=category_id).first()
    if category is None:
        raise ValueError("Terminkategorie nicht gefunden.")
    if category.status == "ARCHIVIERT":
        raise ValueError("Die Kategorie ist bereits archiviert.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            AppointmentCategory.objects.filter(id=category_id).update(
                status="ARCHIVIERT"
            )
    category.refresh_from_db()
    return category


def set_job_category(actor_app_user_id, *, service_job_id, category_id):
    """Setzt oder entfernt (category_id=None) die Terminkategorie eines Einsatzes.

    Nur AKTIVE Kategorien sind zuweisbar; category_id=None löscht die Zuordnung.
    """
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    if category_id is not None:
        category = AppointmentCategory.objects.filter(id=category_id).first()
        if category is None:
            raise ValueError(f"Terminkategorie {category_id} existiert nicht")
        if category.status != "AKTIV":
            raise ValueError("Nur aktive Kategorien können zugewiesen werden.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            ServiceJob.objects.filter(id=service_job_id).update(
                appointment_category_id=category_id
            )
    return ServiceJob.objects.get(id=service_job_id)


# ===========================================================================
# Ressource
# ===========================================================================

def create_resource(actor_app_user_id, *, name, resource_type, notes=None):
    """Legt eine Ressource (Status AKTIV) an."""
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(
            f"Ungültige resource_type '{resource_type}'. "
            f"Erlaubt: {', '.join(RESOURCE_TYPES)}."
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            res = Resource.objects.create(
                id=uuid.uuid4(),
                name=name.strip(),
                resource_type=resource_type,
                status="AKTIV",
                notes=(notes.strip() if notes else None) or None,
                created_by_id=actor_app_user_id,
                version=1,
            )
            res.refresh_from_db()
    return res


def update_resource(
    actor_app_user_id, *, resource_id, name=None, resource_type=None, notes=None
):
    """Ändert Name/Typ/Notiz einer Ressource (nicht den Status — dafür
    set_resource_status)."""
    res = Resource.objects.filter(id=resource_id).first()
    if res is None:
        raise ValueError("Ressource nicht gefunden.")
    if res.status == "ARCHIVIERT":
        raise ValueError("Archivierte Ressourcen können nicht geändert werden.")
    fields = {}
    if name is not None:
        if not name.strip():
            raise ValueError("name darf nicht leer sein.")
        fields["name"] = name.strip()
    if resource_type is not None:
        if resource_type not in RESOURCE_TYPES:
            raise ValueError(
                f"Ungültige resource_type '{resource_type}'. "
                f"Erlaubt: {', '.join(RESOURCE_TYPES)}."
            )
        fields["resource_type"] = resource_type
    if notes is not None:
        fields["notes"] = notes.strip() or None
    if not fields:
        return res
    with as_business_error():
        with business_transaction(actor_app_user_id):
            Resource.objects.filter(id=resource_id).update(**fields)
    res.refresh_from_db()
    return res


def set_resource_status(actor_app_user_id, *, resource_id, to_status):
    """Wechselt den Ressourcenstatus (AKTIV↔INAKTIV, INAKTIV→ARCHIVIERT).

    Der Übergang wird vorab geprüft (→422 statt 500); der DB-Trigger erzwingt ihn
    zusätzlich physisch. 'ARCHIVIERT' entspricht dem Hero-„Entfernen".
    """
    res = Resource.objects.filter(id=resource_id).first()
    if res is None:
        raise ValueError("Ressource nicht gefunden.")
    if to_status not in RESOURCE_TRANSITIONS.get(res.status, set()):
        raise ValueError(
            f"Statuswechsel {res.status} → {to_status} ist nicht zulässig."
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            Resource.objects.filter(id=resource_id).update(status=to_status)
    res.refresh_from_db()
    return res


# ===========================================================================
# Zuordnung Ressource ↔ Einsatz
# ===========================================================================

def ueberlappt(a_start, a_end, b_start, b_end):
    """Überlappen zwei Planungszeiträume? Verträgt ein FEHLENDES Ende.

    Bisher schwieg die Prüfung, sobald irgendwo ein Ende fehlte — die einzige
    Stelle, an der wir vor Doppelbelegung warnen, war damit ausgerechnet an den
    unsaubersten Daten blind. Ein Termin ohne Ende ist aber keine Nicht-Information:
    sein BEGINN ist bekannt. Also:

    * beide Enden bekannt → halb-offene Intervalle [start, end);
    * ein Ende fehlt      → dieser Termin gilt als **Zeitpunkt**; er kollidiert,
      wenn sein Beginn im Zeitraum des anderen liegt;
    * beide Enden fehlen  → nur bei identischem Beginn.

    Damit wird nichts erfunden (eine unbekannte Dauer wird NICHT geraten), aber
    auch nichts verschwiegen. Für den Rest gibt es den eigenen Konflikt
    OFFENES_ENDE: „Ende fehlt — Überlappung nicht prüfbar."
    """
    if a_start is None or b_start is None:
        return False
    if a_end is None and b_end is None:
        return a_start == b_start
    if a_end is None:
        return b_start <= a_start < b_end
    if b_end is None:
        return a_start <= b_start < a_end
    return a_start < b_end and b_start < a_end


def _kandidaten_filter(start, end):
    """SQL-Vorfilter auf Einsätze, die mit [start, end) überhaupt kollidieren KÖNNEN.

    Ohne ihn lud jeder Drag die GESAMTE Historie jeder betroffenen Person
    (nach zwei Betriebsjahren Tausende Zeilen je Monteur) nur, um sie in Python
    zu 99 % wieder wegzuwerfen.

    Der Filter ist bewusst eine **Obermenge** von `ueberlappt` — die exakte
    Bewertung (samt NULL-Rand: „Ende fehlt" = Zeitpunkt) bleibt in Python, denn
    sie ist in SQL nicht ohne Blindstellen ausdrückbar. Bewiesen wird nur, dass
    er nichts wegwirft, was überlappen könnte:

    * `end is None` (der eigene Termin ist ein ZEITPUNKT): jede Überlappung
      verlangt `o.start <= start` — beide Zweige von `ueberlappt`
      (`b_start <= a_start < b_end` bzw. Gleichstand) fordern das.
    * `end` gesetzt: jede Überlappung verlangt `o.start < end` UND
      (`o.end > start` ODER `o.end IS NULL`).
    """
    if end is None:
        return Q(service_job__scheduled_start__lte=start)
    return Q(service_job__scheduled_start__lt=end) & (
        Q(service_job__scheduled_end__gt=start)
        | Q(service_job__scheduled_end__isnull=True)
    )


def _kollisionen(job):
    """(Mitarbeiter-, Ressourcen-)Kollisionen eines Einsatzes als Textliste.

    Kandidaten werden über `_kandidaten_filter` auf das Zeitfenster eingegrenzt
    und dann in Python mit `ueberlappt` bewertet — der NULL-Fall lässt sich in
    SQL nicht sauber ausdrücken, ohne wieder blind zu werden.
    """
    if job.scheduled_start is None:
        return []
    start, end = job.scheduled_start, job.scheduled_end
    fenster = _kandidaten_filter(start, end)
    warnings = []

    user_ids = list(
        JobAssignment.objects.filter(service_job_id=job.id).values_list(
            "assignee_id", flat=True
        )
    )
    if user_ids:
        for a in (
            JobAssignment.objects.filter(assignee_id__in=user_ids)
            .exclude(service_job_id=job.id)
            .filter(service_job__scheduled_start__isnull=False)
            .filter(fenster)
            .select_related("assignee", "service_job")
        ):
            o = a.service_job
            if ueberlappt(start, end, o.scheduled_start, o.scheduled_end):
                warnings.append(
                    f"{a.assignee.display_name} ist im selben Zeitfenster bereits "
                    f"Einsatz {o.job_number} zugewiesen (Doppelbelegung)."
                )

    resource_ids = list(
        JobResource.objects.filter(service_job_id=job.id).values_list(
            "resource_id", flat=True
        )
    )
    if resource_ids:
        for link in (
            JobResource.objects.filter(resource_id__in=resource_ids)
            .exclude(service_job_id=job.id)
            .filter(service_job__scheduled_start__isnull=False)
            .filter(fenster)
            .select_related("resource", "service_job")
        ):
            o = link.service_job
            if ueberlappt(start, end, o.scheduled_start, o.scheduled_end):
                warnings.append(
                    f"Ressource {link.resource.name} ist im selben Zeitfenster "
                    f"bereits Einsatz {o.job_number} zugeordnet (Doppelbelegung)."
                )
    return warnings


def _overlap_warnings(service_job, resource_id):
    """Warnhinweise für eine NEUE Ressourcenzuordnung (die Zeile existiert noch
    nicht, deshalb nicht über `_kollisionen`)."""
    start, end = service_job.scheduled_start, service_job.scheduled_end
    if start is None:
        return []
    warnings = []
    others = (
        JobResource.objects.filter(resource_id=resource_id)
        .exclude(service_job_id=service_job.id)
        .filter(service_job__scheduled_start__isnull=False)
        .filter(_kandidaten_filter(start, end))
        .select_related("service_job")
    )
    for link in others:
        o = link.service_job
        if ueberlappt(start, end, o.scheduled_start, o.scheduled_end):
            warnings.append(
                f"Ressource ist im selben Zeitfenster bereits Einsatz "
                f"{o.job_number} zugeordnet (Doppelbelegung)."
            )
    return warnings


def belegungs_warnungen(service_job_id):
    """Nicht-blockierende Belegungs-Hinweise für den AKTUELLEN Zustand eines
    Einsatzes — Doppelbelegung (Mitarbeiter UND Ressourcen), Termin auf einer
    genehmigten Abwesenheit, Termin am Feiertag, fehlendes Ende.

    Wird nach einem Umplanen (set_schedule) oder einer Zuweisung gelesen. Alle
    Hinweise sind **bewusst nicht blockierend** (offene Invariante, siehe
    Modul-Docstring): Sie werden sichtbar gemacht, nicht verhindert.
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None or job.scheduled_start is None:
        return []
    warnings = list(_kollisionen(job))

    tage = _job_tage(job)
    # Abwesenheit: genehmigter Urlaub/Krankheit einer zugewiesenen Person, die
    # den Termin überschneidet. Ein Disponent, der auf einen Urlauber plant,
    # merkt es sonst erst, wenn niemand auf der Baustelle steht.
    user_ids = list(
        JobAssignment.objects.filter(service_job_id=job.id).values_list(
            "assignee_id", flat=True
        )
    )
    if user_ids and tage:
        for ab in (
            Absence.objects.filter(
                status="GENEHMIGT",
                employee__app_user_id__in=user_ids,
                start_date__lte=tage[-1],
                end_date__gte=tage[0],
            )
            .select_related("employee__app_user")
            .order_by("start_date")
        ):
            # KEINE Abwesenheitsart. Sie ist eine besondere Kategorie nach
            # DSGVO Art. 9 (Gesundheitsdaten) und hängt am `hr`-Tor; die Planung
            # läuft über `workflow`. Der Disponent braucht für seinen Zweck nur
            # „abwesend, von–bis" — siehe Modul-Docstring des Board-Abschnitts.
            warnings.append(
                f"{ab.employee.app_user.display_name} ist im Terminzeitraum "
                f"abwesend ({ab.start_date:%d.%m.} – {ab.end_date:%d.%m.})."
            )

    if tage:
        for tag, name in sorted(_feiertage(tage[0], tage[-1]).items()):
            warnings.append(
                f"Der Termin liegt auf einem Feiertag ({name}, {tag:%d.%m.})."
            )

    if job.scheduled_end is None:
        warnings.append(
            "Für den Termin ist kein Ende gepflegt — eine Überlappung mit anderen "
            "Terminen lässt sich nicht vollständig prüfen."
        )
    return sorted(set(warnings))


# ===========================================================================
# Abwesenheiten und Feiertage (gelesen, nicht gepflegt)
# ===========================================================================
# DATENSCHUTZ — die Grenze dieses Moduls: Die Planung liest, DASS jemand
# abwesend ist (von–bis), niemals WARUM. Die Abwesenheitsart (`absence_type`)
# unterscheidet Urlaub von Krankheit und ist damit ein Gesundheitsdatum —
# besondere Kategorie nach DSGVO Art. 9. Sie hängt am `hr`-Tor
# (api/mitarbeiter.py: `require(request, "hr", "LESEN")`) und darf hier nicht
# vorbei: Die Plantafel hängt an `workflow`/LESEN, das ein Disponent OHNE
# hr-Recht hat. Wer die Art wissen darf, holt sie über den hr-Endpunkt.
#
# Es gibt deshalb in diesem Modul bewusst KEIN Art-Label und keinen Weg,
# `absence_type` nach außen zu tragen.


def _job_tage(job):
    """Kalendertage (Europa/Berlin), über die sich ein Einsatz erstreckt.

    Ein dreitägiger Einsatz belegt drei Tage — bisher rechnete das Board nur mit
    dem Starttag. Ein Ende exakt um Mitternacht zählt NICHT als weiterer Tag
    (halb-offenes Intervall), sonst wäre jeder Tagestermin bis 24:00 zweitägig.
    """
    if job.scheduled_start is None:
        return []
    erster = job.scheduled_start.astimezone(BOARD_TZ).date()
    if job.scheduled_end is None:
        return [erster]
    ende = job.scheduled_end.astimezone(BOARD_TZ)
    letzter = ende.date()
    if ende.time() == time(0, 0) and letzter > erster:
        letzter -= timedelta(days=1)
    if letzter < erster:
        letzter = erster
    return [
        erster + timedelta(days=i) for i in range((letzter - erster).days + 1)
    ]


def _feiertage(von, bis):
    """{date: name} im Zeitraum (`hr.holiday`, Migration 0068).

    Regionale Feiertage werden bewusst NICHT weggefiltert: Der Betrieb hat (noch)
    keine hinterlegte Region; würde man eine raten, stünde ein falscher Feiertag
    im Board. Alles, was in der Tabelle steht, gilt — und die Tabelle pflegt der
    Betrieb.
    """
    if von is None or bis is None:
        return {}
    rows = Holiday.objects.filter(day__gte=von, day__lte=bis).values_list("day", "name")
    return dict(rows)


def assign_resource(actor_app_user_id, *, service_job_id, resource_id):
    """Ordnet einem Einsatz eine Ressource zu (resource.job_resource).

    Höchstens ein Eintrag je (Einsatz, Ressource) (DB-UNIQUE). Gibt
    (link, warnings) zurück; warnings ist eine Liste nicht-blockierender
    Doppelbelegungs-Hinweise (die Zuordnung wird dennoch angelegt).
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None:
        raise ValueError(f"Einsatz {service_job_id} existiert nicht")
    res = Resource.objects.filter(id=resource_id).first()
    if res is None:
        raise ValueError(f"Ressource {resource_id} existiert nicht")
    if res.status != "AKTIV":
        raise ValueError("Nur aktive Ressourcen können zugeordnet werden.")
    if JobResource.objects.filter(
        service_job_id=service_job_id, resource_id=resource_id
    ).exists():
        raise ValueError("Diese Ressource ist dem Einsatz bereits zugeordnet.")
    warnings = _overlap_warnings(job, resource_id)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            link = JobResource.objects.create(
                id=uuid.uuid4(),
                service_job_id=service_job_id,
                resource_id=resource_id,
                created_by_id=actor_app_user_id,
            )
    return link, warnings


def unassign_resource(actor_app_user_id, *, service_job_id, resource_id):
    """Entfernt eine Ressourcenzuordnung (nur vor Einsatzabschluss; der
    DB-Trigger blockiert nach ABGESCHLOSSEN/NACHARBEIT → 422)."""
    link = JobResource.objects.filter(
        service_job_id=service_job_id, resource_id=resource_id
    ).first()
    if link is None:
        raise ValueError("Diese Ressource ist dem Einsatz nicht zugeordnet.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            JobResource.objects.filter(id=link.id).delete()


# ===========================================================================
# Plantafel-Board
# ===========================================================================
# Das Board ist ein DISPOSITIONS-Werkzeug, kein Kalenderabbild. Es muss deshalb
# drei Dinge liefern, die die alte Fassung nicht hatte:
#
#   1. **Alle Bahnen** — auch die LEEREN. Wer nur Bahnen zeigt, auf denen schon
#      etwas liegt, kann nichts auf eine freie Person ziehen. Genau das ist der
#      Sinn einer Plantafel.
#   2. **Den Rückstand (Backlog)** — die UNGEPLANTEN Einsätze. Sie hatten im
#      wichtigsten Werkzeug bisher gar keinen Ort.
#   3. **Die Sperrflächen** — genehmigte Abwesenheiten und Feiertage. Ohne sie
#      plant der Disponent auf Urlauber.
#
# Konflikte werden BERECHNET und ausgeliefert, nicht erzwungen: Doppelbelegung
# bleibt eine bewusst weiche Invariante (Migration 0025).

KONFLIKT_TYPEN = ("DOPPELBELEGUNG", "ABWESENHEIT", "FEIERTAG", "OFFENES_ENDE")

# Wie viele Tage das Board höchstens auf einmal zeigt (Monatsansicht + Ränder).
MAX_BOARD_TAGE = 45


class BoardDaten:
    """Reines Transportobjekt (die API mappt daraus ihre Schemas)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _tage(von, bis):
    return [von + timedelta(days=i) for i in range((bis - von).days + 1)]


def _fenster(von, bis):
    """[Beginn, Ende) des Board-Fensters als aware datetimes in Betriebszeit."""
    start = datetime.combine(von, time(0, 0), tzinfo=BOARD_TZ)
    ende = datetime.combine(bis + timedelta(days=1), time(0, 0), tzinfo=BOARD_TZ)
    return start, ende


def _soll_stunden(vertraege, tage, freie_tage=()):
    """Sollstunden aus dem Wochenraster des gültigen Vertrags, über die Tage summiert.

    Ohne gültigen Vertrag gibt es KEINE Sollstunden — dann liefert die Funktion
    None und die Auslastung wird als „unbekannt" ausgewiesen, niemals als 0
    (sonst sähe jeder Mitarbeiter ohne Vertrag maximal überlastet aus; dieselbe
    Regel wie beim fehlenden EK in der Margenauswertung).

    `freie_tage` (genehmigte Abwesenheit, Feiertag) zählen NICHT ins Soll. Sonst
    stünde ein Mitarbeiter in zwei Wochen Urlaub mit „0 von 80 h · 0 % ausgelastet"
    direkt neben seinem eigenen Urlaubsband — er sähe frei aus, obwohl er gerade
    der Einzige ist, den man NICHT verplanen kann. Wer die ganze Woche abwesend
    ist, hat 0 h Soll: die Auslastung ist dann `null` (unbekannt/gegenstandslos),
    nicht 0 % — dieselbe Regel wie beim fehlenden Vertrag.
    """
    if not vertraege:
        return None
    felder = (
        "hours_monday", "hours_tuesday", "hours_wednesday", "hours_thursday",
        "hours_friday", "hours_saturday", "hours_sunday",
    )
    summe = Decimal("0")
    getroffen = False
    for tag in tage:
        for v in vertraege:
            if v.valid_from <= tag and (v.valid_to is None or tag <= v.valid_to):
                getroffen = True
                if tag not in freie_tage:
                    summe += getattr(v, felder[tag.weekday()])
                break
    return summe if getroffen else None


def board_daten(*, date_from, date_to, q=None, category_id=None, backlog_q=None):
    """Alles, was die Plantafel für einen Zeitraum braucht — in einem Rutsch.

    `q`/`category_id` filtern die Kacheln IM Raster (nicht die Bahnen: eine Bahn
    verschwindet nicht, nur weil gerade nichts darauf liegt). `backlog_q` sucht
    im Rückstand.
    """
    tage = _tage(date_from, date_to)
    fenster_start, fenster_ende = _fenster(date_from, date_to)

    # --- Einsätze im Fenster: ÜBERLAPPUNG, nicht Starttag -------------------
    # Ein dreitägiger Auftrag, der am Freitag begann, gehört auch in die Woche
    # darauf. Der alte Filter (`scheduled_start__date` im Fenster) ließ ihn dort
    # verschwinden — ein mehrtägiger Einsatz war ein Punkt am Starttag.
    qs = (
        ServiceJob.objects.filter(scheduled_start__lt=fenster_ende)
        .filter(
            Q(scheduled_end__gt=fenster_start)
            | Q(scheduled_end__isnull=True, scheduled_start__gte=fenster_start)
        )
        .select_related("work_order__property", "property", "appointment_category")
        .prefetch_related("assignments__assignee", "resource_links__resource")
        .order_by("scheduled_start", "id")
    )
    if q:
        qs = qs.filter(_suchfilter(q))
    if category_id:
        qs = qs.filter(appointment_category_id=category_id)
    jobs = list(qs)

    # --- Bahnen: ALLE aktiven Mitarbeiter und Betriebsmittel ----------------
    user_lanes = {
        u.id: u.display_name
        for u in AppUser.objects.filter(status="ACTIVE").order_by("display_name", "id")
    }
    resource_lanes = {
        r.id: r for r in Resource.objects.filter(status="AKTIV").order_by("name", "id")
    }
    # Wer/was schon verplant ist, behält seine Bahn auch nach einer Deaktivierung —
    # sonst fiele ein bereits geplanter Einsatz unsichtbar aus dem Board.
    for j in jobs:
        for a in j.assignments.all():
            user_lanes.setdefault(a.assignee_id, a.assignee.display_name)
        for link in j.resource_links.all():
            resource_lanes.setdefault(link.resource_id, link.resource)

    # --- Abwesenheiten (genehmigt) im Fenster --------------------------------
    # OHNE `absence_type`/Label: „abwesend, von–bis" ist alles, was die Planung
    # braucht; die ART ist ein Gesundheitsdatum und bleibt hinter dem hr-Tor
    # (siehe Abschnitt „Abwesenheiten und Feiertage").
    absences = [
        {
            "id": ab.id,
            "app_user_id": ab.employee.app_user_id,
            "start_date": ab.start_date,
            "end_date": ab.end_date,
        }
        for ab in Absence.objects.filter(
            status="GENEHMIGT", start_date__lte=date_to, end_date__gte=date_from
        ).select_related("employee").order_by("start_date", "id")
    ]
    abwesend_an: dict = {}
    for ab in absences:
        tag = ab["start_date"]
        while tag <= ab["end_date"]:
            abwesend_an.setdefault(ab["app_user_id"], set()).add(tag)
            tag += timedelta(days=1)

    feiertage = _feiertage(date_from, date_to)
    konflikte = _board_konflikte(jobs, abwesend_an, feiertage)

    # --- Auslastung je Mitarbeiter-Bahn --------------------------------------
    plan_minuten = {uid: 0 for uid in user_lanes}
    for j in jobs:
        if j.scheduled_start is None or j.scheduled_end is None:
            continue
        # Auf das Fenster beschneiden: ein Einsatz, der über den Rand ragt, zählt
        # nur mit seinem sichtbaren Anteil — sonst stimmt die Wochensumme nicht.
        a = max(j.scheduled_start, fenster_start)
        b = min(j.scheduled_end, fenster_ende)
        if b <= a:
            continue
        minuten = int((b - a).total_seconds() // 60)
        for asg in j.assignments.all():
            if asg.assignee_id in plan_minuten:
                plan_minuten[asg.assignee_id] += minuten

    vertraege: dict = {}
    for v in EmploymentContract.objects.filter(
        employee__app_user_id__in=list(user_lanes)
    ).select_related("employee"):
        vertraege.setdefault(v.employee.app_user_id, []).append(v)

    lanes = []
    for uid, name in sorted(user_lanes.items(), key=lambda kv: (kv[1], str(kv[0]))):
        # Abwesenheits- und Feiertage zählen nicht ins Soll dieser Bahn.
        frei = abwesend_an.get(uid, set()) | set(feiertage)
        lanes.append(
            {
                "kind": "USER",
                "id": uid,
                "display_name": name,
                "sub": None,
                "plan_hours": (Decimal(plan_minuten.get(uid, 0)) / Decimal(60)).quantize(
                    Decimal("0.01")
                ),
                "target_hours": _soll_stunden(vertraege.get(uid), tage, frei),
            }
        )
    for r in sorted(resource_lanes.values(), key=lambda r: (r.name, str(r.id))):
        lanes.append(
            {
                "kind": "RESOURCE",
                "id": r.id,
                "display_name": r.name,
                "sub": r.resource_type,
                "plan_hours": None,
                "target_hours": None,
            }
        )

    # --- Rückstand: die UNGEPLANTEN Einsätze ---------------------------------
    # Genau das, was man ins Raster ziehen soll. Sie haben keinen Zeitraum, stehen
    # also zeitlich nirgends — deshalb eine eigene Leiste, kein Teil des Rasters.
    bqs = (
        ServiceJob.objects.filter(scheduled_start__isnull=True)
        .exclude(status__in=("ABGESCHLOSSEN", "AUSGEFALLEN"))
        .select_related("work_order__property", "property", "appointment_category")
        .order_by("-created_at", "id")
    )
    if backlog_q:
        bqs = bqs.filter(_suchfilter(backlog_q))
    backlog_total = bqs.count()
    backlog = list(bqs[:100])

    return BoardDaten(
        date_from=date_from,
        date_to=date_to,
        lanes=lanes,
        jobs=jobs,
        konflikte=konflikte,
        absences=absences,
        holidays=sorted(feiertage.items()),
        backlog=backlog,
        backlog_total=backlog_total,
        unassigned_count=sum(
            1 for j in jobs if not j.assignments.all() and not j.resource_links.all()
        ),
    )


def _suchfilter(needle):
    n = needle.strip()
    return (
        Q(job_number__icontains=n)
        | Q(title__icontains=n)
        | Q(work_order__order_number__icontains=n)
        | Q(work_order__title__icontains=n)
        | Q(property__name__icontains=n)
        | Q(work_order__property__name__icontains=n)
    )


def _board_konflikte(jobs, abwesend_an, feiertage):
    """{job_id: [{kind, text}]} — in Python berechnet, damit der NULL-Rand
    (fehlendes Ende) nicht stillschweigend durchrutscht.

    Für die Doppelbelegung werden die Einsätze IM FENSTER gegeneinander geprüft.
    Ein Konflikt mit einem Einsatz außerhalb des Sichtfensters ist hier ohnehin
    nicht handhabbar; er erscheint beim Umplanen (`belegungs_warnungen`, das gegen
    die ganze DB prüft) und im Einsatz-Detail.
    """
    ergebnis: dict = {}
    nach_key: dict = {}
    for j in jobs:
        for a in j.assignments.all():
            nach_key.setdefault(("USER", a.assignee_id), []).append(
                (a.assignee.display_name, j)
            )
        for link in j.resource_links.all():
            nach_key.setdefault(("RESOURCE", link.resource_id), []).append(
                (link.resource.name, j)
            )

    def melden(job, kind, text):
        ergebnis.setdefault(job.id, []).append({"kind": kind, "text": text})

    for (kind, _key), eintraege in nach_key.items():
        for i, (name, a) in enumerate(eintraege):
            for _name2, b in eintraege[i + 1:]:
                if not ueberlappt(
                    a.scheduled_start, a.scheduled_end,
                    b.scheduled_start, b.scheduled_end,
                ):
                    continue
                wer = name if kind == "USER" else f"Ressource {name}"
                melden(
                    a, "DOPPELBELEGUNG",
                    f"{wer} ist zeitgleich auch auf {b.job_number} eingeplant.",
                )
                melden(
                    b, "DOPPELBELEGUNG",
                    f"{wer} ist zeitgleich auch auf {a.job_number} eingeplant.",
                )

    for j in jobs:
        tage = _job_tage(j)
        for a in j.assignments.all():
            treffer = abwesend_an.get(a.assignee_id, set())
            if any(t in treffer for t in tage):
                # Nur DASS, nicht WARUM — die Abwesenheitsart ist ein
                # Gesundheitsdatum und bleibt hinter dem hr-Tor.
                melden(
                    j, "ABWESENHEIT",
                    f"{a.assignee.display_name} ist im Terminzeitraum abwesend.",
                )
        for t in tage:
            if t in feiertage:
                melden(
                    j, "FEIERTAG",
                    f"Der Termin liegt auf einem Feiertag ({feiertage[t]}, "
                    f"{t:%d.%m.}).",
                )
        if j.scheduled_end is None:
            melden(
                j, "OFFENES_ENDE",
                "Kein Ende gepflegt — die Dauer ist unbekannt und eine Überlappung "
                "nur eingeschränkt prüfbar.",
            )

    # n:m-Zuweisungen melden dieselbe Kollision mehrfach → entdoppeln, Reihenfolge
    # bleibt stabil.
    for jid, liste in ergebnis.items():
        gesehen = set()
        eindeutig = []
        for k in liste:
            schluessel = (k["kind"], k["text"])
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)
            eindeutig.append(k)
        ergebnis[jid] = eindeutig
    return ergebnis


# ===========================================================================
# Termin anlegen/ändern aus dem Board (zusammengesetzter Vorgang)
# ===========================================================================
# Das Board-Formular schreibt Einsatz, Kategorie, Zuweisungen und Betriebsmittel
# in EINEM Vorgang. Vorher hätte das UI vier bis acht Einzelrufe hintereinander
# absetzen müssen — jeder davon ein möglicher Teilfehler, der einen halb
# angelegten Termin hinterlässt (genau dieser Fall stand als ehrliche
# Fehlermeldung im alten Plantafel-Code). Hier liegt die Kette in EINER
# business_transaction: entweder steht der Termin vollständig, oder gar nicht.
# Die verschachtelten Service-Aufrufe öffnen dabei Django-SAVEPOINTs innerhalb
# der äußeren Klammer — die Trigger-Tore und das Audit greifen unverändert.
#
# NICHT enthalten (mit Absicht): der Auftragsbezug beim Ändern. Er ist in der DB
# unveränderlich (Trigger WF-01, Migration 0062) — ein „freien Termin zum Auftrag
# hochstufen" durch die Hintertür gibt es hier nicht.


def _eindeutig(ids):
    """Doppelte IDs entfernen, Reihenfolge erhalten.

    Ein UI, das dieselbe Person/Ressource zweimal mitschickt, meint sie einmal.
    Ohne diese Stelle liefe der zweite Anlauf in den UNIQUE-Index und der Nutzer
    bekäme einen 500er für eine harmlose Eingabe.
    """
    return list(dict.fromkeys(ids or ()))


def _pruefe_planbar(assignee_ids, resource_ids):
    for uid in assignee_ids or ():
        ensure_exists(AppUser, uid, "Mitarbeiter")
    for rid in resource_ids or ():
        res = Resource.objects.filter(id=rid).first()
        if res is None:
            raise ValueError(f"Ressource {rid} existiert nicht")
        if res.status != "AKTIV":
            raise ValueError(
                f"Ressource '{res.name}' ist nicht aktiv und nicht einplanbar."
            )


def _pruefe_zeitraum(scheduled_start, scheduled_end):
    if scheduled_end is not None and scheduled_start is None:
        raise ValueError("Ein Ende ohne Beginn ergibt keinen Termin.")
    if (
        scheduled_start is not None
        and scheduled_end is not None
        and scheduled_end <= scheduled_start
    ):
        raise ValueError("Das Ende muss nach dem Beginn liegen.")


def create_termin(
    actor_app_user_id,
    *,
    work_order_id=None,
    title=None,
    property_id=None,
    scheduled_start=None,
    scheduled_end=None,
    on_site_contact_party_id=None,
    access_instructions=None,
    appointment_category_id=None,
    assignee_ids=(),
    resource_ids=(),
):
    """Legt einen Termin mit allem an, was am Board dranhängt.

    Ist ein Beginn gesetzt, hebt der Vorgang den Einsatz anschließend von
    UNGEPLANT auf GEPLANT (der DB-Statusautomat verlangt den Beginn dafür). OHNE
    Beginn entsteht bewusst ein Eintrag im **Rückstand** — das ist kein Fehler,
    sondern der zweite legitime Weg: erst die Arbeit erfassen, den Termin später
    ins Raster ziehen.
    """
    assignee_ids = _eindeutig(assignee_ids)
    resource_ids = _eindeutig(resource_ids)
    _pruefe_planbar(assignee_ids, resource_ids)
    _pruefe_zeitraum(scheduled_start, scheduled_end)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            job = einsatz_service.create_service_job(
                actor_app_user_id,
                work_order_id=work_order_id,
                title=title,
                property_id=property_id,
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                on_site_contact_party_id=on_site_contact_party_id,
                access_instructions=access_instructions,
                appointment_category_id=appointment_category_id,
            )
            for uid in assignee_ids or ():
                einsatz_service.assign_user(
                    actor_app_user_id, service_job_id=job.id, assignee_user_id=uid
                )
            for rid in resource_ids or ():
                JobResource.objects.create(
                    id=uuid.uuid4(),
                    service_job_id=job.id,
                    resource_id=rid,
                    created_by_id=actor_app_user_id,
                )
            if scheduled_start is not None:
                einsatz_service.advance_status(
                    actor_app_user_id, service_job_id=job.id, to_status="GEPLANT"
                )
    return ServiceJob.objects.get(id=job.id)


def update_termin(
    actor_app_user_id,
    *,
    service_job_id,
    title=_UNSET_TERMIN,
    property_id=_UNSET_TERMIN,
    scheduled_start=_UNSET_TERMIN,
    scheduled_end=_UNSET_TERMIN,
    on_site_contact_party_id=_UNSET_TERMIN,
    access_instructions=_UNSET_TERMIN,
    appointment_category_id=_UNSET_TERMIN,
    assignee_ids=None,
    resource_ids=None,
    reason=None,
):
    """Ändert einen Termin in einem Vorgang.

    `assignee_ids`/`resource_ids` sind der SOLL-Zustand (Vollersetzung): Was nicht
    mehr drinsteht, wird gelöst. `None` heißt „nicht anfassen". Die übrigen Felder
    nutzen dasselbe Sentinel-Muster wie `update_service_job`: „nicht mitgeschickt"
    ist etwas anderes als „ausdrücklich auf NULL".

    **`scheduled_start=None` (ausdrücklich) legt den Termin ZURÜCK IN DEN
    RÜCKSTAND**: Zeitraum auf NULL, Status GEPLANT → UNGEPLANT. Das ist die
    Gegenbewegung zum Ziehen ins Raster; sie darf kein stiller No-Op sein. Der
    Statuswechsel ist begründungspflichtig (SERVICE_JOB_TRANSITIONS) — ohne
    `reason` gibt es einen klaren Fehler, keine halbe Änderung. Aus jedem anderen
    Status als GEPLANT/UNGEPLANT ist der Rückweg nicht zulässig (der Einsatz läuft
    bereits) und endet ebenfalls als Fehler.
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None:
        raise ValueError("Einsatz nicht gefunden.")
    assignee_ids = None if assignee_ids is None else _eindeutig(assignee_ids)
    resource_ids = None if resource_ids is None else _eindeutig(resource_ids)
    _pruefe_planbar(assignee_ids or (), resource_ids or ())

    # Ausdrückliches „kein Beginn" = zurück in den Rückstand. Ein Ende ohne Beginn
    # ergibt keinen Termin, deshalb fällt es hier mit weg (statt den Vorgang an
    # `_pruefe_zeitraum` scheitern zu lassen — der Wille ist eindeutig).
    zurueck_in_rueckstand = (
        scheduled_start is not _UNSET_TERMIN and scheduled_start is None
    )
    if zurueck_in_rueckstand:
        scheduled_end = None

    start = job.scheduled_start if scheduled_start is _UNSET_TERMIN else scheduled_start
    ende = job.scheduled_end if scheduled_end is _UNSET_TERMIN else scheduled_end
    zeit_geaendert = (
        scheduled_start is not _UNSET_TERMIN or scheduled_end is not _UNSET_TERMIN
    )
    if zeit_geaendert:
        _pruefe_zeitraum(start, ende)
    if zurueck_in_rueckstand and job.status not in ("GEPLANT", "UNGEPLANT"):
        raise ValueError(
            f"Ein Einsatz im Status {job.status} kann nicht in den Rückstand "
            "zurückgelegt werden."
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            stamm = {}
            for name, wert in (
                ("title", title),
                ("property_id", property_id),
                ("on_site_contact_party_id", on_site_contact_party_id),
                ("access_instructions", access_instructions),
            ):
                if wert is not _UNSET_TERMIN:
                    stamm[name] = wert
            if stamm:
                einsatz_service.update_service_job(
                    actor_app_user_id, service_job_id=service_job_id, **stamm
                )
            if zurueck_in_rueckstand:
                # Reihenfolge: erst der Statuswechsel, DANN der Zeitraum. Der
                # DB-CHECK (0014) verlangt für GEPLANT/BESTAETIGT einen
                # scheduled_start — würde man ihn zuerst nullen, risse die
                # Constraint. Umgekehrt ist UNGEPLANT MIT Zeitraum erlaubt.
                if job.status == "GEPLANT":
                    einsatz_service.advance_status(
                        actor_app_user_id,
                        service_job_id=service_job_id,
                        to_status="UNGEPLANT",
                        reason=reason,
                    )
                einsatz_service.clear_schedule(
                    actor_app_user_id, service_job_id=service_job_id
                )
            elif zeit_geaendert and start is not None:
                einsatz_service.set_schedule(
                    actor_app_user_id,
                    service_job_id=service_job_id,
                    scheduled_start=start,
                    scheduled_end=ende,
                )
            if appointment_category_id is not _UNSET_TERMIN:
                set_job_category(
                    actor_app_user_id,
                    service_job_id=service_job_id,
                    category_id=appointment_category_id,
                )
            if assignee_ids is not None:
                alt = set(
                    JobAssignment.objects.filter(
                        service_job_id=service_job_id
                    ).values_list("assignee_id", flat=True)
                )
                neu = set(assignee_ids)
                for uid in neu - alt:
                    einsatz_service.assign_user(
                        actor_app_user_id,
                        service_job_id=service_job_id,
                        assignee_user_id=uid,
                    )
                for uid in alt - neu:
                    einsatz_service.unassign_user(
                        actor_app_user_id,
                        service_job_id=service_job_id,
                        assignee_user_id=uid,
                    )
            if resource_ids is not None:
                alt = set(
                    JobResource.objects.filter(
                        service_job_id=service_job_id
                    ).values_list("resource_id", flat=True)
                )
                neu = set(resource_ids)
                for rid in neu - alt:
                    JobResource.objects.create(
                        id=uuid.uuid4(),
                        service_job_id=service_job_id,
                        resource_id=rid,
                        created_by_id=actor_app_user_id,
                    )
                weg = alt - neu
                if weg:
                    JobResource.objects.filter(
                        service_job_id=service_job_id, resource_id__in=list(weg)
                    ).delete()
            # Ein Termin aus dem Rückstand, der jetzt eine Zeit hat, IST geplant —
            # sonst bliebe er formal UNGEPLANT und stünde weiter im Rückstand,
            # obwohl er sichtbar im Raster liegt.
            if (
                start is not None
                and ServiceJob.objects.filter(
                    id=service_job_id, status="UNGEPLANT"
                ).exists()
            ):
                einsatz_service.advance_status(
                    actor_app_user_id,
                    service_job_id=service_job_id,
                    to_status="GEPLANT",
                )
    return ServiceJob.objects.get(id=service_job_id)
