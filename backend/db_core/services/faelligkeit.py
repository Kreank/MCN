"""Fälligkeiten-Engine — ein Modell für drei Fristenarten.

Beantwortet genau eine Frage: **Was steht an?** — und zwar über alle drei Arten
wiederkehrender Termine hinweg:

  WARTUNG         aus maintenance.maintenance_contract (existiert seit 0016)
  PRUEFUNG        aus maintenance.inspection (Prüffrist an Objekt/Anlage)
  GEWAEHRLEISTUNG aus maintenance.warranty (Fristablauf eines Auftrags)

Der Scheduler (`generiere`) erzeugt Fälligkeiten **im Voraus**: eine Fälligkeit
wird sichtbar, sobald `heute >= due_date - lead_time_days` (Vorlauf). Dadurch hat
der Betrieb Zeit zu reagieren, statt am Fälligkeitstag überrascht zu werden.

## Idempotenz

`generiere` ist beliebig oft wiederholbar. Das garantiert nicht dieser Code,
sondern die DB: drei partielle UNIQUE-Indizes über (anker_id, due_date),
**statusunabhängig**. Wir schreiben mit ON CONFLICT DO NOTHING (Django:
`bulk_create(ignore_conflicts=True)`-Äquivalent von Hand) — bei parallelen Läufen
gewinnt einer, der andere legt schlicht nichts an. Und weil der Index
statusunabhängig ist, kann ein VERWORFENER Eintrag nicht wieder auferstehen.

## Der Fortschreibungs-Vertrag

`next_due_date` der Quelle (Vertrag/Prüfung) ist der **nächste noch nicht
abgeschlossene** Termin. Sie rückt genau dann vor, wenn die zugehörige
Fälligkeit ERLEDIGT **oder VERWORFEN** wird. Das Verwerfen muss vorrücken —
sonst stünde die Quelle für immer auf demselben Datum, der Idempotenz-Index
verböte eine neue Zeile, und der Vertrag wäre still tot.

## Verhältnis zum Bestands-Scheduler

Das Command `wartung_faellige_ausloesen` (Vollautomatik für Wartungsverträge)
bleibt unverändert erhalten. Beide Richtungen sind geschlossen, es gibt keine
zwei Wahrheiten:

  * `wartung.trigger_action` schließt die passende Fälligkeit mit (siehe dort).
  * `erledigen()` schreibt umgekehrt einen `maintenance_event` in die Auslöse-
    Historie des Vertrags und rückt `next_due_date` **bis über den Stichtag
    hinaus** vor (derselbe Nachhol-Schutz wie `catch_up_until`). Ohne das bliebe
    ein mehrfach überfälliger Vertrag fällig, und die nächtliche Vollautomatik
    erzeugte ein ZWEITES Folgeobjekt für dieselbe, gerade erledigte Wartung.

## Feiertage

Die Fälligkeit selbst (`due_date`) wird **nie** verschoben — eine Prüffrist ist
ein Datum, das der Betrieb nicht durch einen Feiertag verschieben kann, und ein
verschobenes Fälligkeitsdatum wäre eine Falschaussage. Verschoben wird nur der
**daraus abgeleitete Terminvorschlag**: `naechster_werktag()` schiebt ihn über
Sonntage und Feiertage (hr.holiday, bundesweit + Bundesland des Firmenprofils)
hinweg nach vorn.
"""
import calendar
import uuid
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    DueItem,
    Inspection,
    MaintenanceContract,
    MaintenanceEvent,
    Warranty,
)
from db_core.services import aufgabe as aufgabe_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import einsatz as einsatz_service
from db_core.services import projekt as projekt_service
from db_core.services import zeiterfassung as zeit_service

KINDS = ("WARTUNG", "PRUEFUNG", "GEWAEHRLEISTUNG")
STATUSES = ("OFFEN", "ERLEDIGT", "VERWORFEN")

# Folgeaktionen aus der Fälligkeiten-Ansicht. Jede läuft durch den NORMALEN
# Service des Zielbereichs (Statusautomat, Tore, Rechte) — kein Sonderweg.
FOLGEAKTIONEN = ("TERMIN", "AUFTRAG", "PROJEKT", "AUFGABE", "ANGEBOT", "KEINE")

# Wie eine Folgeaktion in der Auslöse-Historie des Wartungsvertrags heißt
# (maintenance_event.action, Domäne in Migration 0071 erweitert).
EVENT_AKTION = {
    "TERMIN": "TERMIN",
    "AUFTRAG": "AUFTRAG",
    "PROJEKT": "PROJEKT",
    "AUFGABE": "AUFGABE",
    "ANGEBOT": "ANGEBOT",
    "KEINE": "VERMERK",
}


# ---------------------------------------------------------------------------
# Datumsarithmetik
# ---------------------------------------------------------------------------

def add_months(d, n):
    """Addiert n Monate mit Tages-Clamping (31.01. + 1 Monat → 28./29.02.)."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def naechstes_intervall(basis, interval_kind, interval_days=None):
    """Nächster Termin nach `basis`. None, wenn es keine Wiederholung gibt."""
    if interval_kind == "JAEHRLICH":
        return add_months(basis, 12)
    if interval_kind == "MONATLICH":
        return add_months(basis, 1)
    if interval_kind == "WOECHENTLICH":
        return basis + timedelta(weeks=1)
    if interval_kind == "TAGE":
        return basis + timedelta(days=interval_days or 0) if interval_days else None
    return None  # FESTES_DATUM (Vertrag): einmalig


def naechster_werktag(d, feiertage=None):
    """Schiebt ein Datum über Sonntage und Feiertage hinweg nach vorn.

    Bewusst NUR Sonntag (nicht Samstag): im Handwerk ist der Samstag ein
    möglicher Arbeitstag (Notdienst, Kundentermine), und das Produkt soll den
    Betrieb nicht bevormunden. Feiertage kommen aus `hr.holiday` (bundesweit +
    Bundesland des Firmenprofils, siehe zeiterfassung.feiertage).

    Wird NUR auf abgeleitete Termine angewandt, nie auf die Fälligkeit selbst.
    """
    if d is None:
        return None
    if feiertage is None:
        feiertage = zeit_service.feiertage(d, d + timedelta(days=21))
    kandidat = d
    for _ in range(21):  # 3 Wochen reichen für jede Feiertagshäufung
        if kandidat.weekday() != 6 and kandidat not in feiertage:
            return kandidat
        kandidat += timedelta(days=1)
    return kandidat


def termin_vorschlag(due_date, feiertage=None):
    """(datum, hinweis) — Werktags-Vorschlag zu einer Fälligkeit.

    `feiertage` ist der bereits geladene Feiertagskalender. Wer eine LISTE von
    Fälligkeiten abbildet, lädt ihn EINMAL (`feiertage_fenster`) und reicht ihn
    durch — sonst kostet jede Zeile zwei Queries (Firmenprofil + hr.holiday).
    """
    verschoben = naechster_werktag(due_date, feiertage)
    if verschoben == due_date:
        return verschoben, None
    return verschoben, (
        f"{due_date:%d.%m.%Y} fällt auf einen Sonntag oder Feiertag — "
        f"Vorschlag: {verschoben:%d.%m.%Y}."
    )


def feiertage_fenster(daten):
    """Feiertagskalender für eine Menge von Fälligkeitsdaten — EINE Abfrage.

    Der Puffer nach hinten entspricht dem Suchfenster von `naechster_werktag`
    (bis zu 21 Tage nach vorn schieben).
    """
    daten = [d for d in daten if d is not None]
    if not daten:
        return {}
    return zeit_service.feiertage(min(daten), max(daten) + timedelta(days=21))


# ---------------------------------------------------------------------------
# Erzeugen (der Scheduler-Kern)
# ---------------------------------------------------------------------------

def _sichtbar_ab(due_date, lead_time_days):
    return due_date - timedelta(days=lead_time_days or 0)


def _anlegen(actor_app_user_id, **felder):
    """Legt eine Fälligkeit an — oder nichts, wenn es sie schon gibt.

    Der UNIQUE-Index ist die Wahrheit. Der Create läuft in einem eigenen
    Savepoint (`atomic`), damit ein Konflikt nicht die umgebende Transaktion des
    Scheduler-Laufs abbricht — bei parallelen Läufen fängt der IntegrityError den
    Verlierer, und der Lauf macht mit dem nächsten Vertrag weiter.
    """
    try:
        with transaction.atomic():
            return DueItem.objects.create(
                id=uuid.uuid4(), created_by_id=actor_app_user_id, **felder
            )
    except IntegrityError:
        return None


def _generiere_wartung(actor, stichtag):
    """Fälligkeiten aus aktiven Wartungsverträgen (im Vorlauf-Fenster)."""
    neu = []
    qs = MaintenanceContract.objects.filter(
        status="AKTIV", next_due_date__isnull=False
    ).select_related("property")
    for c in qs:
        lead = c.lead_time_days or 0
        if _sichtbar_ab(c.next_due_date, lead) > stichtag:
            continue  # noch nicht im Vorlauf-Fenster
        item = _anlegen(
            actor,
            kind="WARTUNG",
            contract_id=c.id,
            property_id=c.property_id,
            title=f"Wartung: {c.name}",
            due_date=c.next_due_date,
            lead_time_days=lead,
        )
        if item:
            neu.append(item)
    return neu


def _generiere_pruefung(actor, stichtag):
    """Fälligkeiten aus aktiven Prüfungen (im Vorlauf-Fenster)."""
    neu = []
    qs = Inspection.objects.filter(
        status="AKTIV", next_due_date__isnull=False
    ).select_related("property")
    for i in qs:
        if _sichtbar_ab(i.next_due_date, i.lead_time_days) > stichtag:
            continue
        item = _anlegen(
            actor,
            kind="PRUEFUNG",
            inspection_id=i.id,
            property_id=i.property_id,
            title=f"Prüfung: {i.name}",
            due_date=i.next_due_date,
            lead_time_days=i.lead_time_days,
        )
        if item:
            neu.append(item)
    return neu


def _generiere_gewaehrleistung(actor, stichtag):
    """Fälligkeiten aus laufenden Gewährleistungen (Fristablauf, im Vorlauf)."""
    neu = []
    qs = Warranty.objects.filter(status="AKTIV").select_related(
        "property", "work_order"
    )
    for w in qs:
        if _sichtbar_ab(w.end_date, w.lead_time_days) > stichtag:
            continue
        item = _anlegen(
            actor,
            kind="GEWAEHRLEISTUNG",
            warranty_id=w.id,
            property_id=w.property_id,
            title=(
                f"Gewährleistung läuft ab: Auftrag {w.work_order.order_number} "
                f"({w.work_order.title})"
            ),
            due_date=w.end_date,
            lead_time_days=w.lead_time_days,
        )
        if item:
            neu.append(item)
    return neu


def generiere(actor_app_user_id, *, stichtag=None, arten=None):
    """Erzeugt alle im Vorlauf-Fenster liegenden Fälligkeiten. Idempotent.

    Gibt {art: [DueItem, …]} der NEU entstandenen Einträge zurück (bereits
    vorhandene erscheinen nicht — genau das ist die Idempotenz).
    """
    stichtag = stichtag or date.today()
    arten = set(arten or KINDS)
    ergebnis = {k: [] for k in KINDS}
    with as_business_error():
        with business_transaction(actor_app_user_id):
            if "WARTUNG" in arten:
                ergebnis["WARTUNG"] = _generiere_wartung(actor_app_user_id, stichtag)
            if "PRUEFUNG" in arten:
                ergebnis["PRUEFUNG"] = _generiere_pruefung(actor_app_user_id, stichtag)
            if "GEWAEHRLEISTUNG" in arten:
                ergebnis["GEWAEHRLEISTUNG"] = _generiere_gewaehrleistung(
                    actor_app_user_id, stichtag
                )
    return ergebnis


# ---------------------------------------------------------------------------
# Fortschreibung der Quelle
# ---------------------------------------------------------------------------

def _fortschreiben(item, *, stichtag=None):
    """Rückt die Quelle einer abgeschlossenen Fälligkeit auf den nächsten Termin.

    Muss für ERLEDIGT **und** VERWORFEN laufen (siehe Modulkopf). Läuft INNERHALB
    der bereits offenen business_transaction des Aufrufers.

    Gewährleistung ist einmalig — dort gibt es nichts fortzuschreiben.
    """
    stichtag = stichtag or date.today()
    if item.kind == "WARTUNG":
        c = MaintenanceContract.objects.filter(id=item.contract_id).first()
        if c is None or c.next_due_date is None:
            return None
        # Nur vorrücken, wenn die Quelle noch auf DIESER Fälligkeit steht — sonst
        # hat sie ein anderer Pfad (z. B. wartung.trigger_action) schon vorgerückt.
        if c.next_due_date != item.due_date:
            return c.next_due_date
        neu = naechstes_intervall(c.next_due_date, c.interval_kind, c.interval_days)
        # NACHHOL-SCHUTZ (identisch zu wartung.trigger_action(catch_up_until=…)):
        # Ein mehrere Intervalle überfälliger Vertrag stünde nach einem einzelnen
        # Sprung immer noch in der Vergangenheit — die nächtliche Vollautomatik
        # (Phase 2 des Schedulers) würde ihn dann erneut auslösen und ein ZWEITES
        # Folgeobjekt für dieselbe, gerade von Hand erledigte Wartung erzeugen.
        # Deshalb bis über den Stichtag hinaus vorrücken, ohne weitere Events:
        # eine Wartung, ein Nachweis. (Nur die WARTUNG kennt diese Vollautomatik;
        # Prüfungen löst niemand automatisch aus — dort bliebe ein Nachholsprung
        # ein stilles Verschlucken verpasster Prüftermine.)
        while neu is not None and neu <= stichtag:
            neu = naechstes_intervall(neu, c.interval_kind, c.interval_days)
        MaintenanceContract.objects.filter(id=c.id).update(next_due_date=neu)
        return neu
    if item.kind == "PRUEFUNG":
        i = Inspection.objects.filter(id=item.inspection_id).first()
        if i is None or i.next_due_date is None:
            return None
        if i.next_due_date != item.due_date:
            return i.next_due_date
        neu = naechstes_intervall(i.next_due_date, i.interval_kind, i.interval_days)
        Inspection.objects.filter(id=i.id).update(next_due_date=neu)
        return neu
    return None  # GEWAEHRLEISTUNG: einmalig


def datum_bereits_abgeschlossen(*, contract_id=None, inspection_id=None,
                                warranty_id=None, datum):
    """Gibt es zu (Anker, Datum) bereits eine ERLEDIGTE/VERWORFENE Fälligkeit?

    Der Idempotenz-Index ist **statusunabhängig** — das ist richtig so (ein
    verworfener Eintrag darf nicht auferstehen), hat aber eine Kehrseite: zeigt
    eine Quelle wieder auf ein schon abgeschlossenes Datum, kann `generiere()`
    dort **nichts mehr anlegen**. Die Quelle wäre still tot.

    Deshalb prüfen die Quellen-Services (Prüfung, Gewährleistung), bevor sie ein
    Datum setzen, ob es „verbrannt" ist — und sagen es dem Menschen (422), statt
    ihn in eine tote Frist laufen zu lassen.
    """
    qs = DueItem.objects.exclude(status="OFFEN").filter(due_date=datum)
    if contract_id is not None:
        qs = qs.filter(contract_id=contract_id)
    elif inspection_id is not None:
        qs = qs.filter(inspection_id=inspection_id)
    elif warranty_id is not None:
        qs = qs.filter(warranty_id=warranty_id)
    else:
        return False
    return qs.exists()


# ---------------------------------------------------------------------------
# Folgeobjekte
# ---------------------------------------------------------------------------

def _quelle_party_id(item):
    if item.kind == "WARTUNG" and item.contract_id:
        return MaintenanceContract.objects.filter(
            id=item.contract_id
        ).values_list("party_id", flat=True).first()
    if item.kind == "PRUEFUNG" and item.inspection_id:
        return Inspection.objects.filter(
            id=item.inspection_id
        ).values_list("party_id", flat=True).first()
    if item.kind == "GEWAEHRLEISTUNG" and item.warranty_id:
        return Warranty.objects.filter(
            id=item.warranty_id
        ).values_list("party_id", flat=True).first()
    return None


def _quelle_project_id(item):
    if item.kind == "WARTUNG" and item.contract_id:
        return MaintenanceContract.objects.filter(
            id=item.contract_id
        ).values_list("project_id", flat=True).first()
    return None


def _folgeobjekt(actor, item, *, folgeaktion, termin_datum, notiz):
    """Erzeugt das Folgeobjekt über den NORMALEN Service des Zielbereichs.

    Kein Sonderweg an Statusautomaten/Toren vorbei — genau wie beim manuellen
    Anlegen aus dem UI. Gibt (typ, id, hinweis) zurück.
    """
    if folgeaktion == "KEINE":
        return None, None, None

    beschreibung = (
        f"Automatisch aus der Fälligkeit „{item.title}“ "
        f"(fällig {item.due_date:%d.%m.%Y}) erzeugt."
    )
    if notiz:
        beschreibung = f"{beschreibung}\n\n{notiz}"

    if folgeaktion == "AUFGABE":
        task = aufgabe_service.create_task(
            actor,
            title=item.title,
            description=beschreibung,
            due_date=item.due_date,
            project_id=_quelle_project_id(item),
            party_id=_quelle_party_id(item),
        )
        return "workflow.task", task.id, None

    if item.property_id is None:
        raise ValueError(
            "Diese Fälligkeit hat keine Liegenschaft — daraus lässt sich nur "
            "eine Aufgabe erzeugen."
        )

    if folgeaktion == "TERMIN":
        # Der Termin entsteht IMMER als UNGEPLANTER Einsatz — im Plantafel-
        # RÜCKSTAND, genau da, wo die Disposition ihn aufgreift und ins Raster
        # zieht.
        #
        # Bewusst OHNE `scheduled_start`, auch wenn ein Datum genannt wurde: die
        # Plantafel ordnet Kacheln über die ZUWEISUNG zu (Monteur/Ressource =
        # Bahn). Ein Einsatz mit Zeitraum, aber ohne Zuweisung fiele aus dem
        # Rückstand (der zeigt nur Einsätze ohne Beginn) und bekäme im Raster
        # keine Bahn — er wäre schlicht UNSICHTBAR. Die Fälligkeiten-Ansicht
        # kennt aber keine Zuweisung (wer die Wartung fährt, entscheidet die
        # Dispo, nicht die Frist).
        #
        # Ein genanntes Datum ist deshalb ein WUNSCHTERMIN: er wird als Vermerk
        # am Einsatz hinterlegt (auf den nächsten Werktag geschoben) — ein
        # Hinweis für die Disposition, kein zweiter, konkurrierender Plan.
        hinweis = None
        zeilen = []
        if termin_datum is not None:
            verschoben = naechster_werktag(termin_datum)
            wunsch = f"Wunschtermin aus der Fälligkeit: {verschoben:%d.%m.%Y}"
            if verschoben != termin_datum:
                wunsch += (
                    f" (gewünscht war der {termin_datum:%d.%m.%Y} — "
                    "ein Sonntag oder Feiertag)"
                )
                hinweis = (
                    f"{termin_datum:%d.%m.%Y} ist ein Sonntag oder Feiertag — "
                    f"als Wunschtermin wurde der {verschoben:%d.%m.%Y} vermerkt. "
                    "Der Einsatz liegt im Rückstand der Plantafel; die "
                    "Disposition zieht ihn mit Monteur ins Raster."
                )
            else:
                hinweis = (
                    f"Der Wunschtermin {verschoben:%d.%m.%Y} ist am Einsatz "
                    "vermerkt. Der Einsatz liegt im Rückstand der Plantafel; "
                    "die Disposition zieht ihn mit Monteur ins Raster."
                )
            zeilen.append(wunsch + ".")
        if notiz and notiz.strip():
            zeilen.append(notiz.strip())
        job = einsatz_service.create_service_job(
            actor,
            work_order_id=None,  # freier Termin (Migration 0062)
            title=item.title,
            property_id=item.property_id,
            on_site_contact_party_id=_quelle_party_id(item),
            access_instructions="\n".join(zeilen) or None,
        )
        return "workflow.service_job", job.id, hinweis

    if folgeaktion == "AUFTRAG":
        order = auftrag_service.create_work_order(
            actor,
            property_id=item.property_id,
            title=item.title,
            project_id=_quelle_project_id(item),
            description=beschreibung,
            desired_date=item.due_date,
        )
        return "workflow.work_order", order.id, None

    if folgeaktion == "PROJEKT":
        project = projekt_service.create_project(
            actor, name=item.title, property_ids=[item.property_id]
        )
        return "workflow.project", project.id, None

    if folgeaktion == "ANGEBOT":
        quote = beleg_service.create_quote(
            actor,
            property_id=item.property_id,
            title=item.title,
            project_id=_quelle_project_id(item),
        )
        return "invoicing.quote", quote.id, None

    raise ValueError(f"Unbekannte Folgeaktion '{folgeaktion}'.")


# ---------------------------------------------------------------------------
# Erledigen / Verwerfen
# ---------------------------------------------------------------------------

def _lade_offen(due_item_id):
    item = DueItem.objects.filter(id=due_item_id).first()
    if item is None:
        raise ValueError("Fälligkeit nicht gefunden.")
    if item.status != "OFFEN":
        raise ValueError(
            f"Die Fälligkeit ist bereits {item.status.lower()} und kann nicht "
            "erneut bearbeitet werden."
        )
    return item


def _wartungs_event(actor_app_user_id, item, *, aktion, typ, obj_id, note):
    """Schreibt die Erledigung/Verwerfung in die Auslöse-Historie des Vertrags.

    Ohne das gäbe es zwei Wahrheiten: die Fälligkeit wäre erledigt, die Historie
    des Wartungsvertrags aber leer — obwohl die Wartung stattgefunden hat und
    `next_due_date` vorgerückt ist. Die Vollautomatik (wartung.trigger_action)
    schreibt denselben Nachweis; hier ist es der Mensch, der handelt.

    Läuft INNERHALB der business_transaction des Aufrufers.
    """
    if item.kind != "WARTUNG" or not item.contract_id:
        return None
    return MaintenanceEvent.objects.create(
        id=uuid.uuid4(),
        contract_id=item.contract_id,
        due_date=item.due_date,
        action=aktion,
        result_object_type=typ,
        result_object_id=obj_id,
        note=note,
        triggered_by_id=actor_app_user_id,
    )


def erledigen(
    actor_app_user_id,
    *,
    due_item_id,
    folgeaktion="KEINE",
    termin_datum=None,
    notiz=None,
):
    """Erledigt eine Fälligkeit: erzeugt das Folgeobjekt und schreibt die Quelle fort.

    `folgeaktion` ∈ TERMIN | AUFTRAG | PROJEKT | AUFGABE | ANGEBOT | KEINE.
    Gibt (item, hinweis) zurück; `hinweis` erklärt Wunschtermin/Feiertag.

    Bei WARTUNG entsteht zusätzlich ein `maintenance_event` — die Auslöse-Historie
    des Vertrags weist die Erledigung genauso nach wie die der Vollautomatik.
    """
    if folgeaktion not in FOLGEAKTIONEN:
        raise ValueError(
            f"Unbekannte Folgeaktion '{folgeaktion}'. "
            f"Erlaubt: {', '.join(FOLGEAKTIONEN)}."
        )
    item = _lade_offen(due_item_id)
    if folgeaktion == "KEINE" and not (notiz and notiz.strip()):
        raise ValueError(
            "Ohne Folgeobjekt braucht das Erledigen einen Vermerk "
            "(was wurde stattdessen getan?)."
        )

    hinweis = None
    with as_business_error():
        with business_transaction(actor_app_user_id):
            # Erneut sperren: zwei parallele Erledigungen dürfen nicht zwei
            # Folgeobjekte erzeugen. Der Statustrigger fängt den Zweiten ohnehin,
            # aber sein Folgeobjekt wäre dann schon geschrieben.
            gesperrt = (
                DueItem.objects.select_for_update()
                .filter(id=due_item_id, status="OFFEN")
                .first()
            )
            if gesperrt is None:
                raise ValueError(
                    "Die Fälligkeit ist bereits abgeschlossen (parallele Bearbeitung)."
                )
            typ, obj_id, hinweis = _folgeobjekt(
                actor_app_user_id,
                gesperrt,
                folgeaktion=folgeaktion,
                termin_datum=termin_datum,
                notiz=notiz,
            )
            DueItem.objects.filter(id=due_item_id).update(
                status="ERLEDIGT",
                result_object_type=typ,
                result_object_id=obj_id,
                resolution_note=(notiz.strip() if notiz else None),
                resolved_at=timezone.now(),
                resolved_by_id=actor_app_user_id,
            )
            _wartungs_event(
                actor_app_user_id,
                gesperrt,
                aktion=EVENT_AKTION[folgeaktion],
                typ=typ,
                obj_id=obj_id,
                note=(
                    notiz.strip()
                    if notiz and notiz.strip()
                    else "Aus der Fälligkeiten-Ansicht erledigt."
                ),
            )
            _fortschreiben(gesperrt)
    item.refresh_from_db()
    return item, hinweis


def verwerfen(actor_app_user_id, *, due_item_id, begruendung):
    """Verwirft eine Fälligkeit — begründungspflichtig, kein DELETE.

    Die Quelle wird trotzdem fortgeschrieben: sonst stünde sie für immer auf
    demselben Datum, der Idempotenz-Index verböte eine neue Zeile, und der
    Vertrag/die Prüfung wäre still tot.
    """
    if not begruendung or not begruendung.strip():
        raise ValueError("Das Verwerfen einer Fälligkeit ist begründungspflichtig.")
    _lade_offen(due_item_id)

    with as_business_error():
        with business_transaction(
            actor_app_user_id, status_reason=begruendung.strip()
        ):
            gesperrt = (
                DueItem.objects.select_for_update()
                .filter(id=due_item_id, status="OFFEN")
                .first()
            )
            if gesperrt is None:
                raise ValueError(
                    "Die Fälligkeit ist bereits abgeschlossen (parallele Bearbeitung)."
                )
            DueItem.objects.filter(id=due_item_id).update(
                status="VERWORFEN",
                resolution_note=begruendung.strip(),
                resolved_at=timezone.now(),
                resolved_by_id=actor_app_user_id,
            )
            # Auch das Verwerfen gehört in die Historie des Vertrags: die
            # Fälligkeit ist weg, next_due_date rückt vor — ohne Nachweis wüsste
            # der Vertrag nicht, warum ein Intervall übersprungen wurde.
            _wartungs_event(
                actor_app_user_id,
                gesperrt,
                aktion="VERWORFEN",
                typ=None,
                obj_id=None,
                note=f"Fälligkeit verworfen: {begruendung.strip()}",
            )
            _fortschreiben(gesperrt)
    item = DueItem.objects.get(id=due_item_id)
    return item


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def liste(
    *,
    status="OFFEN",
    kind=None,
    property_id=None,
    von=None,
    bis=None,
    nur_sichtbare=True,
    stichtag=None,
):
    """Fälligkeiten als Queryset — die „Was steht an?"-Liste.

    `nur_sichtbare`: blendet Einträge aus, deren Vorlauf-Fenster noch nicht
    begonnen hat. Die werden zwar nie erzeugt (der Scheduler legt sie erst im
    Fenster an), aber ein manuell vorgezogener Eintrag könnte es sein.
    """
    stichtag = stichtag or date.today()
    qs = DueItem.objects.select_related(
        "property__address", "contract", "inspection", "warranty__work_order",
        "resolved_by",
    )
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    if property_id:
        qs = qs.filter(property_id=property_id)
    if von:
        qs = qs.filter(due_date__gte=von)
    if bis:
        qs = qs.filter(due_date__lte=bis)
    if nur_sichtbare and status == "OFFEN":
        # „Sichtbar ab" = due_date - lead_time_days <= stichtag. Als DB-Ausdruck
        # über zwei Spalten (Datum minus Intervall aus einer int-Spalte) wäre das
        # backend-spezifisch; die Menge offener Fälligkeiten ist klein, deshalb
        # die Vorauswahl der IDs in Python — bewusst auf einem ROHEN Queryset
        # (kein select_related, kein Modellaufbau der Beziehungen).
        ids = [
            d_id
            for d_id, due, lead in DueItem.objects.filter(
                id__in=qs.values("id")
            ).values_list("id", "due_date", "lead_time_days")
            if _sichtbar_ab(due, lead) <= stichtag
        ]
        qs = qs.filter(id__in=ids)
    return qs.order_by("due_date", "kind", "title")


def ueberfaellig_count(stichtag=None):
    stichtag = stichtag or date.today()
    return DueItem.objects.filter(status="OFFEN", due_date__lt=stichtag).count()
