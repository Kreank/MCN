"""Einsatz-Service: workflow.service_job anlegen, terminieren, Mitarbeiter
zuweisen, Statuswechsel durchführen und Zeiten/Materialien erfassen.

Wie die übrigen Services laufen alle Writes über business_transaction (setzt
app.current_user_id für Audit/Statusprotokoll; bei begründungspflichtigen
Übergängen zusätzlich app.status_reason). Die Einsatznummer (E-HZG-26-…) vergibt
die DB per BEFORE-INSERT-Trigger aus dem Gewerk (Migration 0120); das Model
lässt die Spalte ungesetzt und lädt frisch nach.

Seit Migration 0062 gibt es zwei Spielarten: den auftragsgebundenen Einsatz und
den **freien Termin** (work_order_id IS NULL — Begehung/Besichtigung/Beratung vor
der Beauftragung). Der freie Termin braucht einen eigenen Titel, darf optional an
einer Liegenschaft hängen und läuft ohne das Auftrags-Ausführungstor.

Der Einsatz hat einen Trigger-gestützten Statusautomaten (Migration 0014). Die
erlaubten Übergänge und die Begründungspflicht spiegeln workflow.status_transition
(0010) — sie werden hier vorab geprüft, damit Eingabefehler als klarer ValueError
(→422) statt als DB-Fehler (→500) enden. Die fachlichen Tore (Auftragsstatus bei
INSERT, Ausführung ab UNTERWEGS setzt einen freigegebenen Auftrag voraus) setzt
die DB als Trigger durch; sie werden über as_business_error in 422 übersetzt.

Zeit-/Materialerfassung unterliegt dem Korrekturfenster B-28 (Migration 0017):
bis Einsatzabschluss frei; danach nur mit Begründung; nach kaufmännischer
Auftragsprüfung gesperrt. Das setzt ausschließlich die DB durch.
"""
import uuid

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    AppointmentCategory,
    AppUser,
    Article,
    Building,
    JobAssignment,
    MaterialEntry,
    Property,
    ServiceJob,
    TimeCategory,
    TimeEntry,
    Trade,
    Unit,
    WorkOrder,
)
from db_core.services._validation import ensure_exists, ensure_party_usable

# Sentinel für Teil-Updates: „Feld nicht mitgeschickt" ist etwas anderes als
# „Feld ausdrücklich auf NULL setzen" (z. B. Kontakt wieder entfernen).
_UNSET = object()

ASSIGNMENT_ROLES = ("TECHNICIAN", "LEAD")
# Codes der Systemkategorien aus hr.time_category (Migration 0066). Sie lösen das
# frühere CHECK-Enum `time_entry.time_type` ab. Der Bestandspfad (Zeitbuchung am
# Einsatz) spricht weiter in diesen Codes; `log_time` löst sie auf die Kategorie
# auf. Freie Kategorien (Werkstatt, Büro, …) laufen über `category_id` —
# siehe services/zeiterfassung.py.
TIME_TYPES = (
    "ARBEITSZEIT",
    "FAHRTZEIT",
    "PAUSE",
    "BEREITSCHAFT",
    "NACHARBEIT",
    "INTERNE_ZEIT",
)

# Erlaubte Statusübergänge je Ausgangsstatus → {Zielstatus: begruendungspflichtig}.
# Wörtliche Spiegelung von workflow.status_transition (0010) für entity='service_job'.
SERVICE_JOB_TRANSITIONS = {
    "UNGEPLANT": {"GEPLANT": False},
    "GEPLANT": {"UNGEPLANT": True, "BESTAETIGT": False, "AUSGEFALLEN": True},
    "BESTAETIGT": {"GEPLANT": True, "UNTERWEGS": False, "AUSGEFALLEN": True},
    "UNTERWEGS": {"VOR_ORT": False, "AUSGEFALLEN": True},
    "VOR_ORT": {"PAUSIERT": False, "ABGESCHLOSSEN": False},
    "PAUSIERT": {"VOR_ORT": False},
    "ABGESCHLOSSEN": {"NACHARBEIT": True},
    "NACHARBEIT": {"GEPLANT": False},
    "AUSGEFALLEN": {},
}


def _check_category(appointment_category_id):
    if appointment_category_id is None:
        return
    category = AppointmentCategory.objects.filter(id=appointment_category_id).first()
    if category is None:
        raise ValueError(f"Terminkategorie {appointment_category_id} existiert nicht")
    if category.status != "AKTIV":
        raise ValueError("Nur aktive Kategorien können zugewiesen werden.")


def _check_property_matches_order(work_order_id, property_id):
    """Liegenschaft am Einsatz muss zum Auftrag passen (DB: zusammengesetzter FK).

    Vorabprüfung, damit der Verstoß als klarer 422 statt als IntegrityError (500)
    endet. Ohne Auftrag (freier Termin) ist jede existierende Liegenschaft ok.
    """
    if property_id is None:
        return
    ensure_exists(Property, property_id, "Liegenschaft")
    if work_order_id is None:
        return
    order_property_id = (
        WorkOrder.objects.filter(id=work_order_id)
        .values_list("property_id", flat=True)
        .first()
    )
    if order_property_id != property_id:
        raise ValueError(
            "Die Liegenschaft des Einsatzes muss die Liegenschaft des Auftrags sein."
        )


def _clean_title(title):
    """Leerstring/Whitespace → None (der DB-CHECK verbietet leere Titel)."""
    if title is None:
        return None
    cleaned = title.strip()
    return cleaned or None


def _check_building_unit(property_id, building_id, unit_id):
    """Gebäude/Einheit müssen zur Liegenschaft bzw. zum Gebäude passen (Migration 0119).

    Vorabprüfung, damit ein Verstoß als klarer 422 statt als IntegrityError (500)
    endet — die DB erzwingt dasselbe über zusammengesetzte FKs und CHECKs. Es wird
    der ZIELZUSTAND geprüft (die bereits aufgelösten effektiven Werte), nicht ein
    Teil-Payload: wer nur die Einheit setzt, muss sich am vorhandenen Gebäude
    messen lassen.
    """
    if unit_id is not None and building_id is None:
        raise ValueError("Eine Einheit setzt ein Gebäude voraus.")
    if building_id is not None and property_id is None:
        raise ValueError(
            "Ein Gebäude/eine Einheit am Einsatz setzt eine Liegenschaft voraus."
        )
    if building_id is not None:
        b_property_id = (
            Building.objects.filter(id=building_id)
            .values_list("property_id", flat=True)
            .first()
        )
        if b_property_id is None:
            raise ValueError("Gebäude existiert nicht.")
        if b_property_id != property_id:
            raise ValueError("Das Gebäude gehört nicht zur angegebenen Liegenschaft.")
    if unit_id is not None:
        u_building_id = (
            Unit.objects.filter(id=unit_id)
            .values_list("building_id", flat=True)
            .first()
        )
        if u_building_id is None:
            raise ValueError("Einheit existiert nicht.")
        if u_building_id != building_id:
            raise ValueError("Die Einheit gehört nicht zum angegebenen Gebäude.")


def create_service_job(
    actor_app_user_id,
    *,
    work_order_id=None,
    title=None,
    property_id=None,
    building_id=None,
    unit_id=None,
    scheduled_start=None,
    scheduled_end=None,
    on_site_contact_party_id=None,
    access_instructions=None,
    appointment_category_id=None,
    trade_id=None,
):
    """Legt einen workflow.service_job (Einsatz) im Initialstatus UNGEPLANT an.

    Zwei Spielarten (Migration 0062):

    * **auftragsgebunden** (work_order_id gesetzt): wie bisher. Der Trigger
      verhindert die Anlage auf abgerechnete/stornierte Aufträge (B-03/B-06).
      `title` ist optional (Fallback: Auftragstitel); `property_id` ist optional
      und muss, wenn gesetzt, die Liegenschaft des Auftrags sein.
    * **freier Termin** (work_order_id=None): Begehung/Besichtigung/Beratung ohne
      Auftrag. `title` ist dann **Pflicht**; `property_id` und der Kontakt
      (on_site_contact_party_id) bleiben optional — bei einer Begehung ist der
      Kunde oft noch gar nicht angelegt und wird über update_service_job
      nachgetragen.

    Der Trigger erzwingt UNGEPLANT als Startstatus. appointment_category_id ist
    optional; nur AKTIVE Kategorien sind zuweisbar (Migration 0025).
    """
    title = _clean_title(title)
    if work_order_id is None and title is None:
        raise ValueError("Ein freier Termin ohne Auftrag braucht einen Titel.")
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    _check_property_matches_order(work_order_id, property_id)
    _check_building_unit(property_id, building_id, unit_id)
    ensure_party_usable(on_site_contact_party_id, "Ansprechpartner vor Ort")
    _check_category(appointment_category_id)
    ensure_exists(Trade, trade_id, "Gewerk")
    # Gewerk erben (0120): Ein Einsatz zum Heizungsauftrag ist ein
    # Heizungseinsatz — das soll niemand zweimal eingeben. Ausdrücklich
    # mitgegebenes `trade_id` gewinnt; der freie Termin (ohne Auftrag) hat
    # nichts zu erben und bleibt ohne Gewerk, bis jemand eines wählt.
    if trade_id is None and work_order_id is not None:
        trade_id = (
            WorkOrder.objects.filter(id=work_order_id)
            .values_list("trade_id", flat=True)
            .first()
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            job = ServiceJob.objects.create(
                id=uuid.uuid4(),
                work_order_id=work_order_id,
                title=title,
                property_id=property_id,
                building_id=building_id,
                unit_id=unit_id,
                status="UNGEPLANT",
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                on_site_contact_party_id=on_site_contact_party_id,
                access_instructions=access_instructions,
                appointment_category_id=appointment_category_id,
                trade_id=trade_id,
            )
            # Die Einsatznummer vergibt erst der BEFORE-INSERT-Trigger aus dem
            # Gewerk (0120) — vor dem Nachladen steht sie nicht in der Instanz.
            job.refresh_from_db()
    return job


def update_service_job(
    actor_app_user_id,
    *,
    service_job_id,
    on_site_contact_party_id=_UNSET,
    title=_UNSET,
    property_id=_UNSET,
    building_id=_UNSET,
    unit_id=_UNSET,
    access_instructions=_UNSET,
    trade_id=_UNSET,
):
    """Trägt Stammangaben eines Einsatzes nach (Teil-Update, Sentinel-basiert).

    Hauptzweck: den **Ansprechpartner vor Ort nachtragen**. Bei einer Begehung
    steht der Kontakt oft erst nach dem Termin fest (oder existiert im System
    noch gar nicht) — deshalb muss er nachträglich setzbar sein. Zusätzlich
    lassen sich Titel, Liegenschaft und Zutrittshinweise korrigieren.

    Nicht mitgegebene Felder bleiben unangetastet; ausdrückliches ``None`` löscht
    das Feld (Kontakt entfernen). Der Auftragsbezug ist NICHT änderbar (DB-Trigger
    WF-01): ein freier Termin bleibt frei.

    Regeln (→ ValueError = 422):
    * Ein freier Termin darf seinen Titel nicht verlieren.
    * Eine Liegenschaft muss bei auftragsgebundenen Einsätzen die des Auftrags
      sein.

    **Gewerk (`trade_id`):** dieselbe Sentinel-Semantik wie überall hier —
    ``_UNSET`` heißt „nicht anfassen", ausdrückliches ``None`` heißt „Gewerk
    entfernen". Es wird beim Update NICHT erneut vom Auftrag geerbt: Das Erben
    ist eine Voreinstellung der Anlage (`create_service_job`), keine laufende
    Bindung. Sonst ließe sich ein bewusst abweichendes Gewerk (Sanitärtermin auf
    einem Heizungsauftrag) nie wieder löschen — es käme beim nächsten Speichern
    von selbst zurück. Das Gewerk bestimmt zugleich das Kürzel der
    Einsatznummer; eine bereits vergebene Nummer ändert sich dadurch nicht
    (der Trigger vergibt sie nur beim INSERT).
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None:
        raise ValueError("Einsatz nicht gefunden.")

    felder = {}
    if title is not _UNSET:
        neuer_titel = _clean_title(title)
        if neuer_titel is None and job.work_order_id is None:
            raise ValueError("Ein freier Termin ohne Auftrag braucht einen Titel.")
        felder["title"] = neuer_titel
    if property_id is not _UNSET:
        _check_property_matches_order(job.work_order_id, property_id)
    # Ort (Liegenschaft/Gebäude/Einheit): den ZIELZUSTAND prüfen. Nicht
    # mitgeschickte Teile behalten den Bestandswert; ändert sich einer, muss die
    # ganze Hierarchie zueinander passen (DB: zusammengesetzte FKs + CHECK). So
    # scheitert z. B. „Liegenschaft wechseln, Gebäude stehen lassen" hier als
    # klarer 422 statt als IntegrityError.
    if any(w is not _UNSET for w in (property_id, building_id, unit_id)):
        eff_property = job.property_id if property_id is _UNSET else property_id
        eff_building = job.building_id if building_id is _UNSET else building_id
        eff_unit = job.unit_id if unit_id is _UNSET else unit_id
        _check_building_unit(eff_property, eff_building, eff_unit)
    if property_id is not _UNSET:
        felder["property_id"] = property_id
    if building_id is not _UNSET:
        felder["building_id"] = building_id
    if unit_id is not _UNSET:
        felder["unit_id"] = unit_id
    if on_site_contact_party_id is not _UNSET:
        ensure_party_usable(on_site_contact_party_id, "Ansprechpartner vor Ort")
        felder["on_site_contact_party_id"] = on_site_contact_party_id
    if trade_id is not _UNSET:
        ensure_exists(Trade, trade_id, "Gewerk")
        felder["trade_id"] = trade_id
    if access_instructions is not _UNSET:
        felder["access_instructions"] = (
            access_instructions.strip() or None
            if isinstance(access_instructions, str)
            else access_instructions
        )
    if not felder:
        return job

    with as_business_error():
        with business_transaction(actor_app_user_id):
            ServiceJob.objects.filter(id=service_job_id).update(**felder)
    job.refresh_from_db()
    return job


def set_schedule(
    actor_app_user_id, *, service_job_id, scheduled_start, scheduled_end=None
):
    """Setzt/ändert den Planungszeitraum eines Einsatzes (ohne Statuswechsel).

    Der DB-CHECK verlangt scheduled_end > scheduled_start; ein geplanter/
    bestätigter Einsatz braucht einen scheduled_start (deshalb hier Pflicht).
    """
    if scheduled_start is None:
        raise ValueError("scheduled_start darf nicht leer sein.")
    if scheduled_end is not None and scheduled_end <= scheduled_start:
        raise ValueError("scheduled_end muss nach scheduled_start liegen.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            updated = ServiceJob.objects.filter(id=service_job_id).update(
                scheduled_start=scheduled_start, scheduled_end=scheduled_end
            )
            if not updated:
                raise ValueError("Einsatz nicht gefunden.")
    return ServiceJob.objects.get(id=service_job_id)


def clear_schedule(actor_app_user_id, *, service_job_id):
    """Nimmt den Planungszeitraum wieder weg (Termin zurück in den Rückstand).

    OHNE Statuswechsel — den führt der Aufrufer VORHER durch: Der DB-CHECK (0014)
    verlangt für GEPLANT/BESTAETIGT einen scheduled_start; ein Einsatz muss also
    erst UNGEPLANT sein, bevor der Zeitraum fallen darf. Die zusammengesetzte
    Klammer liegt in `planung.update_termin`.
    """
    job = ServiceJob.objects.filter(id=service_job_id).only("id", "status").first()
    if job is None:
        raise ValueError("Einsatz nicht gefunden.")
    if job.status in ("GEPLANT", "BESTAETIGT"):
        raise ValueError(
            f"Ein Einsatz im Status {job.status} braucht einen Planbeginn; der "
            "Zeitraum kann nicht entfernt werden."
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            ServiceJob.objects.filter(id=service_job_id).update(
                scheduled_start=None, scheduled_end=None
            )
    return ServiceJob.objects.get(id=service_job_id)


def advance_status(actor_app_user_id, *, service_job_id, to_status, reason=None):
    """Führt einen Statuswechsel des Einsatzes durch.

    Prüft den Übergang vorab gegen die Übergangstabelle (→422 statt 500) und
    verlangt bei begründungspflichtigen Übergängen einen reason. Die fachlichen
    Tore (Ausführung ab UNTERWEGS setzt einen freigegebenen Auftrag voraus) prüft
    die DB und wird als 422 übersetzt.
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None:
        raise ValueError("Einsatz nicht gefunden.")
    allowed = SERVICE_JOB_TRANSITIONS.get(job.status, {})
    if to_status not in allowed:
        raise ValueError(
            f"Übergang {job.status} → {to_status} ist nicht erlaubt."
        )
    requires_reason = allowed[to_status]
    if requires_reason and not (reason and reason.strip()):
        raise ValueError(
            f"Übergang {job.status} → {to_status} erfordert eine Begründung."
        )
    felder = {"status": to_status}
    felder.update(_ist_zeiten(job, to_status))
    with as_business_error():
        with business_transaction(
            actor_app_user_id, status_reason=reason.strip() if reason else None
        ):
            ServiceJob.objects.filter(id=service_job_id).update(**felder)
    job.refresh_from_db()
    return job


def _ist_zeiten(job, to_status):
    """Stempelt die IST-Zeiten des Einsatzes am Statuswechsel.

    `actual_start`/`actual_end` existierten seit Migration 0014, wurden aber von
    keinem Service je gesetzt — tote Struktur. Der Statusautomat ist die einzige
    Stelle, an der das System sicher weiß, wann die Arbeit wirklich begann und
    endete:

    * **VOR_ORT** = Arbeitsbeginn vor Ort → `actual_start` (nicht UNTERWEGS: das
      ist die Anfahrt; die Fahrtzeit ist eine eigene Zeitart in der Zeiterfassung).
    * **ABGESCHLOSSEN** = Arbeitsende → `actual_end`.

    Beide werden nur gesetzt, wenn sie noch leer sind: Ein Einsatz, der über
    PAUSIERT zurück nach VOR_ORT geht, behält seinen ersten Arbeitsbeginn; eine
    Nacharbeit überschreibt das Abschlussdatum des ersten Durchlaufs nicht. Die
    Zeitbuchung (`workflow.time_entry`) bleibt davon unberührt — sie ist die
    abrechnungsrelevante Erfassung, das hier ist der Verlaufsstempel.
    """
    jetzt = timezone.now()
    if to_status == "VOR_ORT" and job.actual_start is None:
        return {"actual_start": jetzt}
    if to_status == "ABGESCHLOSSEN" and job.actual_end is None:
        return {"actual_end": jetzt}
    return {}


def assign_user(actor_app_user_id, *, service_job_id, assignee_user_id, role="TECHNICIAN"):
    """Weist dem Einsatz einen Mitarbeiter (security.app_user) zu.

    Höchstens ein Eintrag je (Einsatz, Mitarbeiter) (DB-UNIQUE). Rolle TECHNICIAN
    (Standard) oder LEAD.
    """
    if role not in ASSIGNMENT_ROLES:
        raise ValueError(
            f"Ungültige role '{role}'. Erlaubt: {', '.join(ASSIGNMENT_ROLES)}."
        )
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    ensure_exists(AppUser, assignee_user_id, "Mitarbeiter")
    # Doppelzuweisung verletzt sonst den UNIQUE(service_job_id, assignee_user_id).
    if JobAssignment.objects.filter(
        service_job_id=service_job_id, assignee_id=assignee_user_id
    ).exists():
        raise ValueError("Dieser Mitarbeiter ist dem Einsatz bereits zugewiesen.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            assignment = JobAssignment.objects.create(
                id=uuid.uuid4(),
                service_job_id=service_job_id,
                assignee_id=assignee_user_id,
                role=role,
            )
    return assignment


def unassign_user(actor_app_user_id, *, service_job_id, assignee_user_id):
    """Hebt die Zuweisung eines Mitarbeiters am Einsatz auf (Korrektur).

    Gegenstück zu assign_user — nötig, um einen Einsatz auf der Plantafel von
    einer Bahn in eine andere zu ziehen (die alte Zuweisung muss weichen).

    Der DB-Trigger `workflow.protect_job_assignment` lässt das Löschen nur zu,
    solange der Einsatz nicht ABGESCHLOSSEN/NACHARBEIT ist (Historienschutz
    F-02); danach kommt der Fehler als fachlicher 422 zurück, NICHT als 500.
    """
    link = JobAssignment.objects.filter(
        service_job_id=service_job_id, assignee_id=assignee_user_id
    ).first()
    if link is None:
        raise ValueError("Dieser Mitarbeiter ist dem Einsatz nicht zugewiesen.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            JobAssignment.objects.filter(id=link.id).delete()


def log_time(
    actor_app_user_id,
    *,
    service_job_id,
    user_id,
    time_type=None,
    category_id=None,
    started_at,
    ended_at,
    note=None,
):
    """Erfasst eine Zeit am Einsatz. ended_at muss nach started_at liegen.

    Zwei Wege zur Kategorie (hr.time_category, Migration 0066):
      * `time_type` — der Code einer Systemkategorie (Bestandspfad, B-27).
      * `category_id` — eine beliebige, auch betriebseigene Kategorie.
    Genau einer von beiden muss gesetzt sein.

    Zwei Schlösser prüft die DB:
      * B-28 (kaufmännisch): bis Einsatzabschluss frei, danach nur mit
        Begründung, nach kaufmännischer Auftragsprüfung gesperrt.
      * Arbeitstag (0067): eine Buchung an einem bestätigten Tag verlangt eine
        Begründung und wirft den Tag auf ENTWURF zurück. Der Arbeitstag selbst
        wird vom Trigger gesetzt.
    """
    if (time_type is None) == (category_id is None):
        raise ValueError("Genau eines von time_type oder category_id ist anzugeben.")
    if time_type is not None:
        if time_type not in TIME_TYPES:
            raise ValueError(
                f"Ungültige time_type '{time_type}'. Erlaubt: {', '.join(TIME_TYPES)}."
            )
        cat = TimeCategory.objects.filter(code=time_type).first()
        if cat is None:  # pragma: no cover — Seed der Migration 0066
            raise ValueError(f"Systemkategorie {time_type} fehlt.")
        category_id = cat.id
    else:
        cat = TimeCategory.objects.filter(id=category_id).first()
        if cat is None:
            raise ValueError("Unbekannte Zeitkategorie.")
        if cat.status != "AKTIV":
            raise ValueError(f"Zeitkategorie '{cat.name}' ist archiviert.")
    if ended_at is None or ended_at <= started_at:
        raise ValueError("ended_at muss nach started_at liegen.")
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    ensure_exists(AppUser, user_id, "Mitarbeiter")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            entry = TimeEntry.objects.create(
                id=uuid.uuid4(),
                service_job_id=service_job_id,
                user_id=user_id,
                category_id=category_id,
                started_at=started_at,
                ended_at=ended_at,
                note=note,
            )
    return TimeEntry.objects.select_related("category").get(id=entry.id)


def log_material(
    actor_app_user_id,
    *,
    service_job_id,
    description,
    quantity,
    unit,
    recorded_by,
    note=None,
    source_article_id=None,
):
    """Erfasst einen Materialverbrauch am Einsatz (B-26: reine Verbrauchserfassung,
    keine Bestandsführung). quantity muss > 0 sein; das Korrekturfenster (B-28)
    prüft die DB.

    **`source_article_id` macht die Buchung abrechenbar** (Migration 0139): Nur mit
    Artikelbezug kann der Abrechnungslauf einen Verkaufspreis ermitteln
    (`aufschlagsmatrix.vk_vorschlag`) — ohne ihn landet die Buchung in der
    Preisklärung, statt mit 0,00 € durchzugehen oder stillschweigend zu fehlen.
    Der Bezug ist **optional**: Die Freitextbuchung („Dichtung aus dem Fahrzeug")
    bleibt der Alltag und wird nicht erzwungen.

    Der Artikel ist die **Identität** des Verbrauchten, **kein Lagerbestand**
    (B-26): Hier wird nichts fortgeschrieben, reserviert oder abgebucht.

    **Kein Preis** — nicht als Parameter, nicht in der Zeile. Die Erfassung vor Ort
    liefert die Menge, das Belegwesen den Preis (Invariante Kap. 3, dieselbe Regel
    wie am Baustellenbericht).
    """
    if not description or not description.strip():
        raise ValueError("description darf nicht leer sein.")
    if not unit or not unit.strip():
        raise ValueError("unit darf nicht leer sein.")
    if quantity is None or quantity <= 0:
        raise ValueError("quantity muss größer als 0 sein.")
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    ensure_exists(AppUser, recorded_by, "Erfasser")
    if source_article_id is not None:
        # Fremdschlüssel vorab prüfen → 422 statt IntegrityError-500.
        ensure_exists(Article, source_article_id, "Artikel")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            entry = MaterialEntry.objects.create(
                id=uuid.uuid4(),
                service_job_id=service_job_id,
                description=description.strip(),
                quantity=quantity,
                unit=unit.strip(),
                recorded_by_id=recorded_by,
                note=note,
                source_article_id=source_article_id,
            )
    return entry
