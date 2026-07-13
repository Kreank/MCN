"""Service-Tests für Default-Dauer und Serientermine (Migration 0077).

Kern der Architektur: Ein Serientermin ist **kein virtuelles Vorkommen einer
Regel**, sondern eine Reihe echter, eigenständiger Einsätze — jeder mit eigener
Nummer, eigenem Status, eigenen Zuweisungen. Ein abgesagter Dienstag macht den
Mittwoch nicht kaputt.
"""
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from db_core.models import Holiday, JobAssignment, ServiceJob
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service

# Die Betriebszeitzone: ein Handwerkstermin ist eine Uhrzeit auf der Wanduhr.
BERLIN = ZoneInfo("Europe/Berlin")
# Montag, 06.07.2026, 08:00 UTC
MO = datetime(2026, 7, 6, 8, 0, tzinfo=dt_timezone.utc)


def _order(app_user):
    obj = property_service.create_property(
        app_user.id, name="Serienobjekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    return auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Wartungsrunde"
    )


def _termin(app_user, order, *, start=MO, dauer_min=90, **kwargs):
    return planung_service.create_termin(
        app_user.id,
        work_order_id=order.id,
        scheduled_start=start,
        scheduled_end=(start + timedelta(minutes=dauer_min)) if dauer_min else None,
        **kwargs,
    )


def _monatlich_start(tag):
    return datetime(2026, 1, tag, 9, 0, tzinfo=BERLIN)


# --- Default-Dauer je Terminkategorie ---------------------------------------

@pytest.mark.django_db
def test_kategorie_traegt_eine_uebliche_dauer(app_user):
    c = planung_service.create_category(
        app_user.id, name="Wartung Gastherme", default_duration_minutes=90
    )
    assert c.default_duration_minutes == 90

    c = planung_service.update_category(
        app_user.id, category_id=c.id, default_duration_minutes=120
    )
    assert c.default_duration_minutes == 120


@pytest.mark.django_db
def test_dauer_ist_optional_und_ausdruecklich_loeschbar(app_user):
    """„Keine übliche Dauer" ist ein gültiger Zustand — und nicht 0 Minuten."""
    c = planung_service.create_category(app_user.id, name="Sonstiges")
    assert c.default_duration_minutes is None

    c = planung_service.update_category(
        app_user.id, category_id=c.id, default_duration_minutes=45
    )
    assert c.default_duration_minutes == 45
    # Ausdrückliches None löscht sie wieder …
    c = planung_service.update_category(
        app_user.id, category_id=c.id, default_duration_minutes=None
    )
    assert c.default_duration_minutes is None

    # … ein Namensupdate OHNE das Feld lässt sie unangetastet (Sentinel).
    planung_service.update_category(
        app_user.id, category_id=c.id, default_duration_minutes=30
    )
    c = planung_service.update_category(app_user.id, category_id=c.id, name="Umbenannt")
    assert c.default_duration_minutes == 30


@pytest.mark.django_db
@pytest.mark.parametrize("dauer", [0, -30, 10081])
def test_unsinnige_dauer_wird_abgewiesen(app_user, dauer):
    with pytest.raises(ValueError, match="Dauer"):
        planung_service.create_category(
            app_user.id, name=f"Kat {dauer}", default_duration_minutes=dauer
        )


@pytest.mark.django_db
def test_geaenderte_kategoriedauer_verschiebt_keinen_bestehenden_termin(app_user):
    """Die Dauer ist ein VORSCHLAG für den Dialog. Bestehende Termine nachträglich
    zu strecken wäre eine stille Umplanung längst zugesagter Termine."""
    order = _order(app_user)
    kat = planung_service.create_category(
        app_user.id, name="Begehung", default_duration_minutes=60
    )
    job = _termin(app_user, order, dauer_min=60, appointment_category_id=kat.id)

    planung_service.update_category(
        app_user.id, category_id=kat.id, default_duration_minutes=240
    )
    job.refresh_from_db()
    assert job.scheduled_end - job.scheduled_start == timedelta(minutes=60)


# --- Serientermine -----------------------------------------------------------

@pytest.mark.django_db
def test_serie_erzeugt_echte_eigenstaendige_termine(app_user):
    order = _order(app_user)
    kat = planung_service.create_category(app_user.id, name="Baustellenbegehung")
    job = _termin(
        app_user, order, dauer_min=90,
        appointment_category_id=kat.id,
        assignee_ids=[app_user.id],
        access_instructions="Schlüssel beim Hausmeister",
    )

    ergebnis = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=3
    )
    erzeugt = ergebnis["erzeugt"]
    assert len(erzeugt) == 3

    job.refresh_from_db()
    # Der Ausgangstermin IST das erste Vorkommen und trägt dieselbe Klammer.
    assert job.series_id == ergebnis["series_id"]
    assert all(j.series_id == ergebnis["series_id"] for j in erzeugt)

    # Jedes Vorkommen ist ein eigener Einsatz mit eigener Nummer und Status.
    assert len({j.job_number for j in erzeugt}) == 3
    assert all(j.status == "GEPLANT" for j in erzeugt)

    # Takt, Dauer und Kontext sind übernommen.
    assert [j.scheduled_start for j in erzeugt] == [
        MO + timedelta(weeks=1), MO + timedelta(weeks=2), MO + timedelta(weeks=3),
    ]
    for j in erzeugt:
        assert j.scheduled_end - j.scheduled_start == timedelta(minutes=90)
        assert j.appointment_category_id == kat.id
        assert j.access_instructions == "Schlüssel beim Hausmeister"
        assert j.work_order_id == order.id
        assert JobAssignment.objects.filter(
            service_job_id=j.id, assignee_id=app_user.id
        ).exists()


@pytest.mark.django_db
def test_abgesagtes_vorkommen_laesst_die_uebrigen_unberuehrt(app_user):
    """Der eigentliche Grund für echte Zeilen statt einer Serienregel."""
    order = _order(app_user)
    job = _termin(app_user, order)
    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="TAEGLICH", anzahl=3
    )["erzeugt"]

    einsatz_service.advance_status(
        app_user.id, service_job_id=erzeugt[1].id, to_status="AUSGEFALLEN",
        reason="Kunde abwesend",
    )
    erzeugt[0].refresh_from_db()
    erzeugt[2].refresh_from_db()
    assert erzeugt[0].status == "GEPLANT"
    assert erzeugt[2].status == "GEPLANT"


@pytest.mark.django_db
def test_serie_haelt_den_takt_ueber_einen_feiertag(app_user):
    """INVARIANTE: Die Werktagsverschiebung wirkt auf das einzelne Vorkommen, NIE
    auf das Raster. Sonst würde aus „jeden Montag" nach dem ersten Feiertag
    dauerhaft „jeden Dienstag"."""
    # Montag der 2. Woche ist Feiertag.
    feiertag = (MO + timedelta(weeks=1)).date()
    # region=NULL = bundesweit (Migration 0068).
    Holiday.objects.create(id=uuid.uuid4(), day=feiertag, name="Testfeiertag")
    order = _order(app_user)
    job = _termin(app_user, order)

    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2,
        werktags=True,
    )["erzeugt"]

    # Vorkommen 1 weicht auf den Dienstag aus …
    assert erzeugt[0].scheduled_start.date() == feiertag + timedelta(days=1)
    # … Vorkommen 2 liegt trotzdem wieder auf dem MONTAG (Takt unverschoben).
    assert erzeugt[1].scheduled_start == MO + timedelta(weeks=2)


@pytest.mark.django_db
def test_monatliche_serie_klemmt_den_monatstag(app_user):
    """31.01. + 1 Monat = 28.02., nicht der 03.03. — sonst wanderte eine
    Monatsserie über das Jahr immer weiter nach hinten."""
    order = _order(app_user)
    start = datetime(2026, 1, 31, 9, 0, tzinfo=dt_timezone.utc)
    job = _termin(app_user, order, start=start, dauer_min=60)

    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="MONATLICH", anzahl=2,
        werktags=False,
    )["erzeugt"]
    assert erzeugt[0].scheduled_start.date() == datetime(2026, 2, 28).date()
    assert erzeugt[1].scheduled_start.date() == datetime(2026, 3, 31).date()


@pytest.mark.django_db
def test_termin_ohne_beginn_laesst_sich_nicht_wiederholen(app_user):
    """Ein Termin im Rückstand hat kein Raster, aus dem Folgetermine entstünden."""
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    with pytest.raises(ValueError, match="Beginn"):
        planung_service.serie_anlegen(
            app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2
        )


@pytest.mark.django_db
@pytest.mark.parametrize("anzahl", [0, -1, 53])
def test_anzahl_ist_begrenzt(app_user, anzahl):
    order = _order(app_user)
    job = _termin(app_user, order)
    with pytest.raises(ValueError, match="Anzahl"):
        planung_service.serie_anlegen(
            app_user.id, service_job_id=job.id, intervall="WOECHENTLICH",
            anzahl=anzahl,
        )


@pytest.mark.django_db
def test_unbekanntes_intervall_wird_abgewiesen(app_user):
    order = _order(app_user)
    job = _termin(app_user, order)
    with pytest.raises(ValueError, match="Intervall"):
        planung_service.serie_anlegen(
            app_user.id, service_job_id=job.id, intervall="STUENDLICH", anzahl=2
        )


@pytest.mark.django_db
def test_zweite_serie_verlaengert_die_reihe_und_dupliziert_nichts(app_user):
    """Wer eine Reihe verlängert, bekommt keine zweite Klammer — und vor allem
    keine DUBLETTEN.

    Review-Fund: Der Takt lief jedes Mal neu aus dem Ausgangstermin, ein zweiter
    „Wiederholen"-Klick erzeugte die Vorkommen 1..n ein zweites Mal (zwei
    deckungsgleiche Einsätze am selben Tag). Der alte Test prüfte nur die ANZAHL
    und ging deshalb grün durch.
    """
    order = _order(app_user)
    job = _termin(app_user, order)
    erste = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2
    )
    zweite = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=1
    )
    assert zweite["series_id"] == erste["series_id"]

    reihe = planung_service.serie(job.id)
    starts = [j.scheduled_start for j in reihe]
    assert len(reihe) == 4                      # Ausgang + 3 Folgetermine
    assert len(set(starts)) == 4                # …alle an VERSCHIEDENEN Terminen
    # Die Verlängerung hängt HINTEN an, sie füllt die Reihe nicht doppelt.
    assert starts == [
        MO, MO + timedelta(weeks=1), MO + timedelta(weeks=2), MO + timedelta(weeks=3),
    ]


@pytest.mark.django_db
def test_verschobenes_erstes_vorkommen_kippt_den_takt_nicht(app_user):
    """Der Anker hält den WILLEN fest, nicht den Zustand.

    Review-Fund: Der Takt wurde aus dem aktuellen Bestand rekonstruiert. Zog der
    Disponent den ersten Montagstermin auf den Dienstag, wurde aus „jeden Montag"
    ab der nächsten Verlängerung dauerhaft „jeden Dienstag" — obwohl er nur EINEN
    Termin verschoben hatte.
    """
    order = _order(app_user)
    job = _termin(app_user, order)   # Montag
    planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2,
        werktags=False,
    )
    # Der Disponent zieht den Ausgangstermin auf den Dienstag.
    planung_service.update_termin(
        app_user.id, service_job_id=job.id,
        scheduled_start=MO + timedelta(days=1),
        scheduled_end=MO + timedelta(days=1, hours=2),
    )
    neu = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2,
        werktags=False,
    )["erzeugt"]
    # Die Reihe bleibt eine MONTAGS-Reihe.
    for j in neu:
        assert j.scheduled_start.astimezone(BERLIN).weekday() == 0
    assert [j.scheduled_start for j in neu] == [
        MO + timedelta(weeks=3), MO + timedelta(weeks=4),
    ]


@pytest.mark.django_db
def test_abgesagtes_erstes_vorkommen_kippt_den_monatstag_nicht(app_user):
    """Gleiche Wurzel: Wird das erste Vorkommen in den Rückstand zurückgelegt,
    darf der Anker nicht auf den geklemmten 28.02. rutschen."""
    order = _order(app_user)
    job = _termin(app_user, order, start=_monatlich_start(31), dauer_min=60)
    februar = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="MONATLICH", anzahl=1,
        werktags=False,
    )["erzeugt"][0]

    # Das ERSTE Vorkommen (31.01.) wird abgesagt …
    planung_service.update_termin(
        app_user.id, service_job_id=job.id,
        scheduled_start=None, reason="Kunde hat abgesagt",
    )
    # … und die Reihe vom Februar-Termin aus verlängert (der Ausgangstermin liegt
    # jetzt im Rückstand und trägt keinen Beginn mehr).
    neu = planung_service.serie_anlegen(
        app_user.id, service_job_id=februar.id, intervall="MONATLICH", anzahl=1,
        werktags=False,
    )["erzeugt"]
    # Der März muss wieder auf den 31. — der Anker (31.01.) hat die Absage
    # überlebt; aus dem geklemmten 28.02. weitergerechnet wäre es der 28.03.
    assert neu[0].scheduled_start.astimezone(BERLIN).date() == date(2026, 3, 31)


@pytest.mark.django_db
def test_monatliche_reihe_behaelt_beim_verlaengern_den_monatstag(app_user):
    """Der Anker bleibt das ERSTE Vorkommen: Nach dem geklemmten Februar (28.)
    muss der März wieder auf den 31. — sonst wanderte die Reihe nach vorn."""
    order = _order(app_user)
    job = _termin(app_user, order, start=_monatlich_start(31), dauer_min=60)

    planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="MONATLICH", anzahl=1,
        werktags=False,
    )
    planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="MONATLICH", anzahl=1,
        werktags=False,
    )
    tage = [
        j.scheduled_start.astimezone(BERLIN).date()
        for j in planung_service.serie(job.id)
    ]
    assert tage == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]


@pytest.mark.django_db
def test_serie_lesen_liefert_nichts_fuer_einen_einzeltermin(app_user):
    order = _order(app_user)
    job = _termin(app_user, order)
    assert planung_service.serie(job.id) == []


@pytest.mark.django_db
def test_serie_haelt_die_ortszeit_ueber_die_zeitumstellung(app_user):
    """Ein Handwerkstermin ist eine Uhrzeit auf der WANDUHR, kein UTC-Stempel.

    Review-Fund: Der Takt lief in UTC — eine Wochenserie über das Ende der
    Sommerzeit verschob den Termin von 08:00 auf 07:00 Ortszeit. Der Monteur
    stünde eine Stunde zu früh vor der Tür.
    """
    order = _order(app_user)
    # Montag, 19.10.2026, 08:00 Berlin (noch MESZ, UTC+2).
    start = datetime(2026, 10, 19, 8, 0, tzinfo=BERLIN)
    job = _termin(app_user, order, start=start, dauer_min=90)

    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2,
        werktags=False,
    )["erzeugt"]

    # 26.10. liegt hinter der Umstellung (MEZ, UTC+1) — die ORTSZEIT bleibt 08:00.
    for j in erzeugt:
        lokal = j.scheduled_start.astimezone(BERLIN)
        assert (lokal.hour, lokal.minute) == (8, 0)
    assert erzeugt[0].scheduled_start.astimezone(BERLIN).date() == date(2026, 10, 26)
    # Und die Dauer bleibt 90 Minuten (nicht 30, nicht 150).
    assert all(
        j.scheduled_end - j.scheduled_start == timedelta(minutes=90) for j in erzeugt
    )


@pytest.mark.django_db
def test_werktagspruefung_nutzt_den_berliner_kalendertag(app_user):
    """Ein Termin am Montag 00:30 Ortszeit ist in UTC noch Sonntag 22:30.

    Review-Fund: Die Verschiebung prüfte den UTC-Tag und schob ihn deshalb
    grundlos auf den Dienstag.
    """
    order = _order(app_user)
    start = datetime(2026, 7, 6, 0, 30, tzinfo=BERLIN)  # Montag, 00:30 Ortszeit
    job = _termin(app_user, order, start=start, dauer_min=60)

    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=1,
        werktags=True,
    )["erzeugt"]
    lokal = erzeugt[0].scheduled_start.astimezone(BERLIN)
    assert lokal.weekday() == 0            # bleibt MONTAG
    assert lokal.date() == date(2026, 7, 13)


@pytest.mark.django_db
def test_taeglicher_takt_erzeugt_keine_dublette_ueber_den_sonntag(app_user):
    """Review-Fund: Der verschobene Sonntag landete auf dem Montag, an dem die
    Serie ohnehin einen Termin hatte — zwei deckungsgleiche Einsätze, die sich
    das Board anschließend selbst als Doppelbelegung meldete."""
    order = _order(app_user)
    # Mittwoch, 01.07.2026 — der Takt läuft über Sonntag, den 05.07.
    start = datetime(2026, 7, 1, 8, 0, tzinfo=BERLIN)
    job = _termin(app_user, order, start=start, dauer_min=60)

    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="TAEGLICH", anzahl=6,
        werktags=True,
    )["erzeugt"]
    tage = [j.scheduled_start.astimezone(BERLIN).date() for j in erzeugt]
    assert len(tage) == len(set(tage))          # keine Dublette
    assert date(2026, 7, 5) not in tage         # der Sonntag entfällt
    assert date(2026, 7, 6) in tage             # der Montag bleibt EINMAL


@pytest.mark.django_db
def test_archivierte_ressource_wird_nicht_in_die_zukunft_kopiert(app_user):
    """Review-Fund: `JobResource` hat keinen Status-Trigger — ohne die
    Planbarkeitsprüfung wanderte eine archivierte Ressource in jeden Folgetermin."""
    order = _order(app_user)
    res = planung_service.create_resource(
        app_user.id, name="Alter Transporter", resource_type="FAHRZEUG"
    )
    job = _termin(app_user, order, resource_ids=[res.id])
    planung_service.set_resource_status(
        app_user.id, resource_id=res.id, to_status="INAKTIV"
    )
    planung_service.set_resource_status(
        app_user.id, resource_id=res.id, to_status="ARCHIVIERT"
    )

    with pytest.raises(ValueError):
        planung_service.serie_anlegen(
            app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2
        )
    # Nichts halb angelegt.
    assert ServiceJob.objects.filter(series_id__isnull=False).count() == 0


@pytest.mark.django_db
def test_zuweisungsrolle_wandert_mit(app_user):
    """Review-Fund: Ein LEAD des Ausgangstermins war in jedem Folgetermin nur
    noch gewöhnlicher Techniker — die Kolonne hätte keinen Führenden."""
    order = _order(app_user)
    job = _termin(app_user, order)
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=app_user.id, role="LEAD"
    )
    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=1
    )["erzeugt"]
    zuweisung = JobAssignment.objects.get(service_job_id=erzeugt[0].id)
    assert zuweisung.role == "LEAD"


@pytest.mark.django_db
def test_freier_termin_laesst_sich_wiederholen(app_user):
    """Eine Begehung ohne Auftrag ist der Regelfall für eine Reihe (Bauleitung)."""
    obj = property_service.create_property(
        app_user.id, name="Baustelle", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    job = planung_service.create_termin(
        app_user.id, title="Wöchentliche Baubegehung", property_id=obj.id,
        scheduled_start=MO, scheduled_end=MO + timedelta(hours=2),
    )
    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2
    )["erzeugt"]
    for j in erzeugt:
        assert j.work_order_id is None
        assert j.title == "Wöchentliche Baubegehung"
        assert j.property_id == obj.id


@pytest.mark.django_db
def test_abgesagtes_vorkommen_bleibt_teil_der_serie(app_user):
    """Ein in den Rückstand zurückgelegtes Vorkommen (`scheduled_start = NULL`)
    gehört weiter zur Reihe — es herauszufiltern hieße, die Absage zu
    verschweigen. (Review-Fund: die Serienansicht lief darauf in einen 500er.)"""
    order = _order(app_user)
    job = _termin(app_user, order)
    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2
    )["erzeugt"]

    planung_service.update_termin(
        app_user.id, service_job_id=erzeugt[0].id,
        scheduled_start=None, reason="Kunde hat abgesagt",
    )
    reihe = planung_service.serie(job.id)
    assert len(reihe) == 3
    ohne_start = [j for j in reihe if j.scheduled_start is None]
    assert len(ohne_start) == 1
    assert ohne_start[0].status == "UNGEPLANT"


@pytest.mark.django_db
def test_termin_ohne_ende_bleibt_ohne_ende(app_user):
    """Kopiert wird die DAUER — hat der Ausgangstermin keine, erfindet die Serie
    auch keine."""
    order = _order(app_user)
    job = _termin(app_user, order, dauer_min=None)
    erzeugt = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="TAEGLICH", anzahl=1
    )["erzeugt"]
    assert erzeugt[0].scheduled_end is None
