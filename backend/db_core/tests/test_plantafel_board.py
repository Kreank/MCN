"""Service-Tests der Plantafel als Dispositionswerkzeug (planung.board_daten,
create_termin/update_termin) gegen die echte Test-DB.

Die vier Lücken, die dieses Modul absichert:

1. **Mehrtages-Balken** — ein Einsatz erscheint an JEDEM seiner Tage, nicht nur
   am Starttag. Der alte Board-Filter (`scheduled_start__date` im Fenster) ließ
   einen dreitägigen Auftrag in der Folgewoche verschwinden.
2. **Rückstand** — UNGEPLANTE Einsätze (ohne Planbeginn) hatten im wichtigsten
   Werkzeug gar keinen Ort. Ohne sie gibt es nichts, was man ins Raster zieht.
3. **Sperrflächen** — genehmigte Abwesenheiten gehören ins Board; sonst plant
   die Disposition auf Urlauber.
4. **Konflikte** — Doppelbelegung bleibt eine WEICHE Invariante (Migration 0025):
   sie wird sichtbar gemacht, nicht verhindert.
"""
import uuid
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from db_core.models import AppUser, Holiday, JobAssignment, JobResource, ServiceJob
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as hr_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service

# Ein Fenster mitten in der Woche (Mo 2026-07-13 … So 2026-07-19).
VON = date(2026, 7, 13)
BIS = date(2026, 7, 19)

# Vollzeit Mo–Fr, 8 Stunden.
VOLLZEIT = {
    "hours_monday": 8,
    "hours_tuesday": 8,
    "hours_wednesday": 8,
    "hours_thursday": 8,
    "hours_friday": 8,
}


def _t(tag, stunde):
    return datetime(2026, 7, tag, stunde, 0, tzinfo=dt_timezone.utc)


def _berlin(tag, stunde):
    """Betriebszeit — die Auslastung rechnet in Ortszeit, nicht in UTC."""
    return datetime(2026, 7, tag, stunde, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def _order(app_user, title="Auftrag"):
    obj = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    return auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title=title
    )


def _freigegebener_order(app_user, title="Auftrag in Ausführung"):
    """Auftrag durch alle Tore bis IN_AUSFUEHRUNG — nur so darf ein Einsatz
    tatsächlich ausgeführt werden (Ausführungstor ab UNTERWEGS)."""
    obj = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    principal = identity_service.create_organization(
        app_user.id, "Auftraggeber GmbH", "WEG"
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title=title
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for s in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status=s
        )
    return order


def _user(name="Monteur"):
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=name, status="ACTIVE", version=1
    )


def _board(**kw):
    kw.setdefault("date_from", VON)
    kw.setdefault("date_to", BIS)
    return planung_service.board_daten(**kw)


def _job_ids(board):
    return {j.id for j in board.jobs}


# ===========================================================================
# 1. Mehrtägige Einsätze
# ===========================================================================

@pytest.mark.django_db
def test_job_tage_deckt_alle_tage_ab(app_user):
    """Ein dreitägiger Einsatz belegt drei Tage — nicht einen Punkt am Montag."""
    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(13, 8), scheduled_end=_t(15, 16),
    )
    tage = planung_service._job_tage(job)
    assert tage == [date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15)]


@pytest.mark.django_db
def test_job_tage_ende_um_mitternacht_zaehlt_nicht_als_folgetag(app_user):
    """Halb-offenes Intervall: ein Termin bis exakt 00:00 endet am Vortag."""
    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        # 2026-07-13 08:00 UTC bis 2026-07-13 22:00 UTC = 14.07. 00:00 Ortszeit.
        scheduled_start=_t(13, 8), scheduled_end=_t(13, 22),
    )
    assert planung_service._job_tage(job) == [date(2026, 7, 13)]


@pytest.mark.django_db
def test_board_zeigt_ueberlappenden_einsatz_auch_ohne_start_im_fenster(app_user):
    """Der Kern des Mehrtages-Bugs: ein Einsatz, der VOR dem Fenster begann und
    hineinragt, gehört ins Board. Der alte Starttag-Filter warf ihn weg."""
    order = _order(app_user)
    einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(10, 8),   # Freitag davor
        scheduled_end=_t(14, 16),    # ragt bis Dienstag ins Fenster
    )
    board = _board()
    assert len(board.jobs) == 1


@pytest.mark.django_db
def test_board_ignoriert_einsatz_ausserhalb_des_fensters(app_user):
    order = _order(app_user)
    einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(20, 8), scheduled_end=_t(20, 16),
    )
    assert _board().jobs == []


# ===========================================================================
# 2. Rückstand (Backlog)
# ===========================================================================

@pytest.mark.django_db
def test_backlog_enthaelt_ungeplante_einsaetze(app_user):
    order = _order(app_user)
    ungeplant = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id
    )
    einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
    )
    board = _board()
    assert [j.id for j in board.backlog] == [ungeplant.id]
    assert board.backlog_total == 1
    # Der verplante Einsatz liegt im Raster, nicht im Rückstand.
    assert len(board.jobs) == 1


@pytest.mark.django_db
def test_backlog_suche(app_user):
    order = _order(app_user)
    einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, title="Heizung entlüften"
    )
    einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, title="Dach decken"
    )
    board = _board(backlog_q="heizung")
    assert [j.title for j in board.backlog] == ["Heizung entlüften"]
    assert board.backlog_total == 1


@pytest.mark.django_db
def test_backlog_ohne_abgeschlossene_und_ausgefallene(app_user):
    """Ein abgesagter Termin ohne Zeitraum ist kein Rückstand — er ist erledigt."""
    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="GEPLANT"
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="AUSGEFALLEN",
        reason="Kunde abgesagt",
    )
    # Zeitraum bleibt gesetzt → im Raster, nicht im Rückstand.
    assert _board().backlog == []


# ===========================================================================
# 3. Bahnen und Auslastung
# ===========================================================================

@pytest.mark.django_db
def test_bahnen_enthalten_auch_leere_mitarbeiter(app_user):
    """Auf eine LEERE Bahn muss man ziehen können — sonst ist das Board nutzlos."""
    frei = _user("Zoe Ohnetermin")
    lanes = _board().lanes
    ids = [lane["id"] for lane in lanes if lane["kind"] == "USER"]
    assert frei.id in ids
    assert app_user.id in ids


@pytest.mark.django_db
def test_bahnen_enthalten_aktive_ressourcen_ohne_einsatz(app_user):
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    lanes = [lane for lane in _board().lanes if lane["kind"] == "RESOURCE"]
    assert [lane["id"] for lane in lanes] == [res.id]
    assert lanes[0]["sub"] == "FAHRZEUG"


@pytest.mark.django_db
def test_auslastung_plan_stunden_werden_aufs_fenster_beschnitten(app_user):
    """Ein Einsatz, der über den Rand ragt, zählt nur mit seinem SICHTBAREN
    Anteil — sonst stimmt die Wochensumme nicht.

    Das Fenster ist BETRIEBSZEIT (Europa/Berlin), nicht UTC: Montag 00:00 Berlin
    = Sonntag 22:00 UTC. Ein Einsatz von So 00:00 UTC bis Mo 06:00 UTC ragt damit
    mit 8 Stunden (22:00 → 06:00) ins Fenster.
    """
    order = _order(app_user)
    monteur = _user("Max Muster")
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(12, 0),   # Sonntag davor, 00:00 UTC
        scheduled_end=_t(13, 6),     # Montag 06:00 UTC
    )
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    lane = next(
        lane for lane in _board().lanes
        if lane["kind"] == "USER" and lane["id"] == monteur.id
    )
    # Ins Fenster ragt Mo 00:00–08:00 Berlin — aber davon ist nur 07:00–08:00
    # Arbeitszeit. Die Nachtlücke (So 16:00 → Mo 07:00) liegt vollständig im
    # Einsatz und fällt weg, auch wenn sie den Fensterrand überschreitet: Sonst
    # brächte der Montag 00:00–08:00 mit, und dieselbe Kachel ergäbe je nach
    # Lage des Fensters eine andere Auslastung.
    assert float(lane["plan_hours"]) == pytest.approx(1.0)


def _vertrag(app_user, konto, hours=None):
    """Vollzeitvertrag Mo–Fr für eine Bahn — gibt ihr ein Tagessoll."""
    person = identity_service.create_person(app_user.id, "Mon", "Teur")
    emp = hr_service.create_employee(
        app_user.id, app_user_id=konto.id, party_id=person.id,
        hired_on=date(2024, 1, 1),
    )
    return hr_service.create_contract(
        app_user.id, employee_id=emp.id, valid_from=date(2024, 1, 1),
        hours=hours or VOLLZEIT, vacation_days_per_year=30,
    )


def _plan_h(app_user, *spannen, vertrag=None):
    """Geplante Stunden auf der Bahn eines frischen Monteurs.

    Mehrere `(von, bis)`-Paare landen auf DEMSELBEN Monteur — nur so lässt sich
    prüfen, dass die Pause je Arbeitstag anfällt und nicht je Einsatz.
    """
    order = _order(app_user)
    monteur = _user(f"Monteur {uuid.uuid4().hex[:6]}")
    if vertrag is not None:
        _vertrag(app_user, monteur, **vertrag)
    for von, bis in spannen:
        job = einsatz_service.create_service_job(
            app_user.id, work_order_id=order.id, scheduled_start=von, scheduled_end=bis
        )
        einsatz_service.assign_user(
            app_user.id, service_job_id=job.id, assignee_user_id=monteur.id
        )
    lane = next(
        lane for lane in _board().lanes
        if lane["kind"] == "USER" and lane["id"] == monteur.id
    )
    return float(lane["plan_hours"])


@pytest.mark.django_db
def test_auslastung_mehrtaegig_zaehlt_arbeitstage_nicht_die_wanduhr(app_user):
    """Der Kern des Ganzen: Ein Einsatz über vier Tage belegt vier ARBEITSTAGE.

    Di 07:00 bis Fr 16:00 sind 81 Stunden Wanduhr. Gezählt gehören 4 × 8 h =
    32 h: Feierabend und Nacht sind keine Arbeitszeit, und je Tag geht eine
    Stunde Pause ab (07:00–16:00 Anwesenheit = 8 h Arbeit). Die Wanduhr ergäbe
    185 % Auslastung — ein Monteur, der überlastet aussieht, obwohl er normal
    verplant ist, und ein Fehler, den jede spätere Auswertung erbte.
    """
    assert _plan_h(app_user, (_berlin(14, 7), _berlin(17, 16))) == pytest.approx(32.0)


@pytest.mark.django_db
def test_auslastung_voller_tag_ist_acht_stunden_nicht_neun(app_user):
    """07:00–16:00 ist die Anwesenheit; eine Stunde davon ist Pause."""
    assert _plan_h(app_user, (_berlin(14, 7), _berlin(14, 16))) == pytest.approx(8.0)


@pytest.mark.django_db
def test_auslastung_kurzeinsatz_bekommt_keine_pause(app_user):
    """Bis sechs Stunden fällt keine Pause an (ArbZG § 4) — 5 h bleiben 5 h."""
    assert _plan_h(app_user, (_berlin(14, 7), _berlin(14, 12))) == pytest.approx(5.0)


@pytest.mark.django_db
def test_auslastung_sechs_stunden_bleiben_sechs(app_user):
    """Die Grenze ist MEHR als sechs Stunden. Genau sechs bleiben ungekürzt —
    sonst stünde ein Sechs-Stunden-Einsatz schlechter da als ein Fünf-Stunden-."""
    assert _plan_h(app_user, (_berlin(14, 7), _berlin(14, 13))) == pytest.approx(6.0)


@pytest.mark.django_db
def test_auslastung_abendarbeit_bleibt_stehen(app_user):
    """Was am Rand tatsächlich über den Feierabend hinausgeht, verschwindet
    NICHT: 07:00–20:00 sind 13 h Anwesenheit, 12 h Arbeit. Nur die vollständige
    Nachtlücke zwischen zwei Tagen wird herausgeschnitten — Überlast bleibt
    sichtbar. (Der Notdienst bekommt später eine eigene Behandlung.)"""
    assert _plan_h(app_user, (_berlin(14, 7), _berlin(14, 20))) == pytest.approx(12.0)


@pytest.mark.django_db
def test_auslastung_pause_faellt_je_TAG_an_nicht_je_einsatz(app_user):
    """Zwei Termine an einem Tag sind derselbe Arbeitstag — eine Pause, nicht null.

    07:00–12:00 (5 h) und 12:00–16:00 (4 h) sind zusammen 9 h Anwesenheit = 8 h
    Arbeit. Zöge man die Pause je EINSATZ ab, bliebe jeder unter der
    Sechs-Stunden-Schwelle und der Tag stünde mit 9 h da — die Auslastung hinge
    dann daran, wie fein der Disponent den Tag zerschneidet.
    """
    stunden = _plan_h(
        app_user,
        (_berlin(14, 7), _berlin(14, 12)),
        (_berlin(14, 12), _berlin(14, 16)),
        vertrag={},
    )
    assert stunden == pytest.approx(8.0)


@pytest.mark.django_db
def test_auslastung_zaehlt_keine_tage_ohne_soll(app_user):
    """Das Wochenende zählt nicht mit — sonst liefe die Quote auseinander.

    Ein Einsatz von Donnerstag bis Dienstag berührt sechs Kalendertage. Das Soll
    (`_soll_stunden`) kennt aber nur die fünf Werktage, und Sa/So stehen im
    Vollzeitraster auf 0 h. Zählte der Zähler sie trotzdem, ergäbe das 48 h
    gegen 40 h Soll = 120 % — dieselbe Fehlerklasse wie die 185 % aus der
    Wanduhr-Rechnung, nur eine Ebene gröber.

    Im Fenster Mo–So (13.–19.07.2026) liegen vom Einsatz Do–Di die Tage Do, Fr,
    Sa, So: gezählt gehören nur Do und Fr = 16 h.
    """
    stunden = _plan_h(app_user, (_berlin(16, 7), _berlin(21, 16)), vertrag={})
    assert stunden == pytest.approx(16.0)


@pytest.mark.django_db
def test_auslastung_ist_unabhaengig_von_der_lage_des_fensters(app_user):
    """Dieselbe Kachel muss dieselbe Zahl ergeben, egal wo das Fenster liegt.

    Ein Einsatz Sa 07:00 → Mi 16:00 belegt im Fenster Mo–So die Arbeitstage Mo,
    Di, Mi = 24 h. Würde erst geschnitten und dann gerechnet, wäre die
    Nachtlücke So 16:00 → Mo 07:00 nicht mehr vollständig enthalten, bliebe
    stehen, und der Montag brächte 00:00–16:00 mit — 31 h statt 24 h.
    """
    order = _order(app_user)
    monteur = _user(f"Randlage {uuid.uuid4().hex[:6]}")
    _vertrag(app_user, monteur)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_berlin(11, 7),   # Samstag VOR dem Fenster
        scheduled_end=_berlin(15, 16),    # Mittwoch IM Fenster
    )
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    lane = next(
        lane for lane in _board().lanes
        if lane["kind"] == "USER" and lane["id"] == monteur.id
    )
    assert float(lane["plan_hours"]) == pytest.approx(24.0)


@pytest.mark.django_db
def test_auslastung_bei_ausgelaufenem_vertrag_verschwindet_nicht(app_user):
    """Ein Vertrag, der den Tag nicht deckt, darf den Zähler nicht leeren.

    Vorher fragte die Rechnung nur, OB Vertragszeilen existieren. Bei einem
    ausgelaufenen Vertrag fiel damit jeder Tag weg, und die Bahn las sich als
    „0,0 h geplant · Sollstunden unbekannt" — direkt neben den Kacheln, die
    sichtbar auf ihr lagen.
    """
    monteur = _user("Ausgelaufen")
    person = identity_service.create_person(app_user.id, "Alt", "Vertrag")
    emp = hr_service.create_employee(
        app_user.id, app_user_id=monteur.id, party_id=person.id,
        hired_on=date(2024, 1, 1),
    )
    hr_service.create_contract(
        app_user.id, employee_id=emp.id, valid_from=date(2024, 1, 1),
        valid_to=date(2024, 12, 31),  # längst vorbei
        hours=VOLLZEIT, vacation_days_per_year=30,
    )
    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_berlin(14, 7), scheduled_end=_berlin(14, 16),
    )
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    lane = next(
        lane for lane in _board().lanes
        if lane["kind"] == "USER" and lane["id"] == monteur.id
    )
    assert float(lane["plan_hours"]) == pytest.approx(8.0)
    assert lane["target_hours"] is None  # kein deckender Vertrag → unbekannt


@pytest.mark.django_db
def test_auslastung_ohne_vertrag_streicht_keine_tage(app_user):
    """Ohne Vertrag gibt es kein Tagessoll — dann fehlt der Maßstab, welcher Tag
    ein Arbeitstag ist. Gestrichen wird deshalb nichts, nur die Pause geht ab.
    Der Nenner steht in diesem Fall ohnehin auf „unbekannt"."""
    # Sa 18.07. + So 19.07., je 07:00–16:00 → 2 × 8 h, obwohl beides Wochenende ist.
    stunden = _plan_h(
        app_user,
        (_berlin(18, 7), _berlin(18, 16)),
        (_berlin(19, 7), _berlin(19, 16)),
    )
    assert stunden == pytest.approx(16.0)


@pytest.mark.django_db
def test_auslastung_ohne_vertrag_ist_unbekannt_nicht_null(app_user):
    """Ohne gültigen Arbeitsvertrag gibt es KEIN Soll — None, niemals 0. Sonst
    sähe jeder Mitarbeiter ohne Vertrag maximal überlastet aus (dieselbe Regel
    wie beim fehlenden EK in der Margenauswertung)."""
    monteur = _user("Ohne Vertrag")
    lane = next(
        lane for lane in _board().lanes
        if lane["kind"] == "USER" and lane["id"] == monteur.id
    )
    assert lane["target_hours"] is None


# ===========================================================================
# 4. Konflikte (nicht blockierend)
# ===========================================================================

@pytest.mark.django_db
def test_konflikt_doppelbelegung_mitarbeiter(app_user):
    order = _order(app_user)
    monteur = _user("Max Muster")
    a = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 12),
    )
    b = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 10), scheduled_end=_t(14, 14),
    )
    for j in (a, b):
        einsatz_service.assign_user(
            app_user.id, service_job_id=j.id, assignee_user_id=monteur.id
        )
    board = _board()
    arten = {
        k["kind"] for k in board.konflikte[a.id]
    }
    assert "DOPPELBELEGUNG" in arten
    assert "DOPPELBELEGUNG" in {k["kind"] for k in board.konflikte[b.id]}
    # Weiche Invariante: die Zuweisungen SIND geschrieben, nichts wurde blockiert.
    assert JobAssignment.objects.filter(assignee_id=monteur.id).count() == 2


@pytest.mark.django_db
def test_konflikt_doppelbelegung_ressource(app_user):
    order = _order(app_user)
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    a = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 12),
    )
    b = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 9), scheduled_end=_t(14, 11),
    )
    for j in (a, b):
        planung_service.assign_resource(
            app_user.id, service_job_id=j.id, resource_id=res.id
        )
    board = _board()
    assert "DOPPELBELEGUNG" in {k["kind"] for k in board.konflikte[a.id]}
    assert JobResource.objects.filter(resource_id=res.id).count() == 2


@pytest.mark.django_db
def test_konflikt_offenes_ende(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=_t(14, 8)
    )
    board = _board()
    assert "OFFENES_ENDE" in {k["kind"] for k in board.konflikte[job.id]}


@pytest.mark.django_db
def test_kein_konflikt_bei_luecke(app_user):
    order = _order(app_user)
    monteur = _user("Max Muster")
    a = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 12),
    )
    b = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 12), scheduled_end=_t(14, 16),
    )
    for j in (a, b):
        einsatz_service.assign_user(
            app_user.id, service_job_id=j.id, assignee_user_id=monteur.id
        )
    board = _board()
    # [08,12) und [12,16) berühren sich nur — das ist keine Doppelbelegung.
    assert board.konflikte.get(a.id, []) == []
    assert board.konflikte.get(b.id, []) == []


@pytest.mark.django_db
def test_konflikt_abwesenheit(app_user):
    """Termin auf einer GENEHMIGTEN Abwesenheit → Warnung im Board (nicht Sperre).

    Ohne diesen Hinweis plant die Disposition auf Urlauber — bei Hero steht die
    Abwesenheit im Board, bei uns bisher nicht.
    """
    konto = _user("Urlauber Konto")
    person = identity_service.create_person(app_user.id, "Uta", "Urlaub")
    emp = hr_service.create_employee(
        app_user.id, app_user_id=konto.id, party_id=person.id,
        hired_on=date(2024, 1, 1),
    )
    hr_service.create_contract(
        app_user.id, employee_id=emp.id, valid_from=date(2024, 1, 1),
        hours=VOLLZEIT, vacation_days_per_year=30,
    )
    abwesenheit = hr_service.create_absence(
        app_user.id, employee_id=emp.id, absence_type="URLAUB",
        start_date=date(2026, 7, 14), end_date=date(2026, 7, 16),
    )
    hr_service.submit_absence(app_user.id, absence_id=abwesenheit.id)
    hr_service.approve_absence(app_user.id, absence_id=abwesenheit.id)

    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(15, 8), scheduled_end=_t(15, 16),
    )
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=konto.id
    )
    board = _board()
    assert "ABWESENHEIT" in {k["kind"] for k in board.konflikte[job.id]}
    assert len(board.absences) == 1
    # DSGVO Art. 9: Die Plantafel weiß, DASS jemand abwesend ist — nie, WARUM.
    assert "absence_type" not in board.absences[0]
    assert "label" not in board.absences[0]
    # Der Termin steht trotzdem — die Warnung blockiert nicht (weiche Invariante).
    assert ServiceJob.objects.filter(id=job.id).exists()
    # Sollstunden: 5 Werktage × 8 h MINUS die 3 Urlaubstage (Di–Do) → 2 × 8 h.
    # Ein Urlauber darf nicht mit „0 von 40 h" neben seinem Urlaubsband stehen
    # und frei aussehen.
    lane = next(
        lane for lane in board.lanes
        if lane["kind"] == "USER" and lane["id"] == konto.id
    )
    assert float(lane["target_hours"]) == pytest.approx(16.0)


@pytest.mark.django_db
def test_konflikt_feiertag(app_user):
    """Ein Termin an einem Feiertag → Sperrfläche im Board UND Konflikt an der
    Kachel.

    Der Pfad war ungetestet: der einzige Test prüfte `holidays == []` und wäre
    auch grün geblieben, wenn `_feiertage()` dauerhaft nichts geliefert hätte.
    """
    Holiday.objects.create(
        id=uuid.uuid4(), day=date(2026, 7, 15), name="Testfeiertag"
    )
    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(15, 8), scheduled_end=_t(15, 16),
    )
    board = _board()
    assert board.holidays == [(date(2026, 7, 15), "Testfeiertag")]
    konflikte = board.konflikte[job.id]
    assert "FEIERTAG" in {k["kind"] for k in konflikte}
    assert any("Testfeiertag" in k["text"] for k in konflikte)
    # Und der Feiertag zählt nicht ins Soll — auch das nur über den echten
    # Tabelleninhalt prüfbar.
    assert "Feiertag" in " ".join(
        planung_service.belegungs_warnungen(job.id)
    )


@pytest.mark.django_db
def test_feiertag_ausserhalb_des_fensters_bleibt_draussen(app_user):
    Holiday.objects.create(
        id=uuid.uuid4(), day=date(2026, 8, 1), name="Weit weg"
    )
    assert _board().holidays == []


@pytest.mark.django_db
def test_belegungs_warnungen_melden_abwesenheit(app_user):
    """Auch der Umplanen-Pfad (Drag & Drop) meldet die Abwesenheit."""
    konto = _user("Urlauber Konto 2")
    person = identity_service.create_person(app_user.id, "Ute", "Urlaub")
    emp = hr_service.create_employee(
        app_user.id, app_user_id=konto.id, party_id=person.id,
        hired_on=date(2024, 1, 1),
    )
    hr_service.create_contract(
        app_user.id, employee_id=emp.id, valid_from=date(2024, 1, 1),
        hours=VOLLZEIT, vacation_days_per_year=30,
    )
    ab = hr_service.create_absence(
        app_user.id, employee_id=emp.id, absence_type="KRANKHEIT",
        start_date=date(2026, 7, 15), end_date=date(2026, 7, 15),
    )
    hr_service.submit_absence(app_user.id, absence_id=ab.id)
    hr_service.approve_absence(app_user.id, absence_id=ab.id)

    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(15, 8), scheduled_end=_t(15, 16),
    )
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=konto.id
    )
    warnungen = planung_service.belegungs_warnungen(job.id)
    assert any("abwesend" in w for w in warnungen)
    # Aber NIE die Art: `belegungs_warnungen` speist die `warnings` jeder
    # Antwort von /schedule, /assignments und /termine — die Abwesenheitsart
    # (Gesundheitsdatum, DSGVO Art. 9) hat dort nichts verloren.
    assert not any("Krankheit" in w for w in warnungen)


# ===========================================================================
# 5. Termin anlegen/ändern in EINEM Vorgang
# ===========================================================================

@pytest.mark.django_db
def test_create_termin_setzt_alles_in_einem_zug(app_user):
    order = _order(app_user)
    monteur = _user("Max Muster")
    kat = planung_service.create_category(app_user.id, name="Vor-Ort-Termin")
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    job = planung_service.create_termin(
        app_user.id,
        work_order_id=order.id,
        scheduled_start=_t(14, 8),
        scheduled_end=_t(14, 16),
        appointment_category_id=kat.id,
        assignee_ids=[monteur.id, app_user.id],
        resource_ids=[res.id],
    )
    job.refresh_from_db()
    assert job.status == "GEPLANT"
    assert job.appointment_category_id == kat.id
    assert JobAssignment.objects.filter(service_job_id=job.id).count() == 2
    assert JobResource.objects.filter(service_job_id=job.id).count() == 1


@pytest.mark.django_db
def test_create_termin_ohne_zeit_landet_im_rueckstand(app_user):
    """Kein Beginn → UNGEPLANT. Das ist kein Fehler, sondern der zweite legitime
    Weg: erst die Arbeit erfassen, den Termin später ins Raster ziehen."""
    order = _order(app_user)
    job = planung_service.create_termin(app_user.id, work_order_id=order.id)
    assert job.status == "UNGEPLANT"
    assert [j.id for j in _board().backlog] == [job.id]


@pytest.mark.django_db
def test_create_termin_ist_atomar(app_user):
    """Scheitert ein Schritt der Kette, entsteht KEIN halber Termin.

    Die inaktive Ressource wird vorab abgefangen; der Test sichert zusätzlich,
    dass auch ein Fehler NACH der Anlage (unbekannter Mitarbeiter) nichts
    zurücklässt.
    """
    order = _order(app_user)
    vorher = ServiceJob.objects.count()
    with pytest.raises(ValueError):
        planung_service.create_termin(
            app_user.id,
            work_order_id=order.id,
            scheduled_start=_t(14, 8),
            scheduled_end=_t(14, 16),
            assignee_ids=[uuid.uuid4()],  # gibt es nicht
        )
    assert ServiceJob.objects.count() == vorher


@pytest.mark.django_db
def test_update_termin_ersetzt_zuweisungen_vollstaendig(app_user):
    order = _order(app_user)
    a, b = _user("Anton"), _user("Berta")
    job = planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
        assignee_ids=[a.id],
    )
    planung_service.update_termin(
        app_user.id, service_job_id=job.id, assignee_ids=[b.id]
    )
    ids = set(
        JobAssignment.objects.filter(service_job_id=job.id).values_list(
            "assignee_id", flat=True
        )
    )
    assert ids == {b.id}


@pytest.mark.django_db
def test_update_termin_aus_dem_rueckstand_wird_geplant(app_user):
    """Ein Termin aus dem Rückstand, der eine Zeit bekommt, IST geplant — sonst
    stünde er weiter im Rückstand, obwohl er sichtbar im Raster liegt."""
    order = _order(app_user)
    job = planung_service.create_termin(app_user.id, work_order_id=order.id)
    assert job.status == "UNGEPLANT"
    planung_service.update_termin(
        app_user.id, service_job_id=job.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
    )
    job.refresh_from_db()
    assert job.status == "GEPLANT"
    assert _board().backlog == []


@pytest.mark.django_db
def test_update_termin_ende_vor_beginn_wird_abgelehnt(app_user):
    order = _order(app_user)
    job = planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
    )
    with pytest.raises(ValueError):
        planung_service.update_termin(
            app_user.id, service_job_id=job.id, scheduled_end=_t(14, 6)
        )


@pytest.mark.django_db
def test_update_termin_zurueck_in_den_rueckstand(app_user):
    """Die Gegenbewegung zum Ziehen ins Raster: `scheduled_start=None` legt den
    Termin zurück in den Rückstand — Zeitraum weg, Status GEPLANT → UNGEPLANT.

    Vorher verschluckte der Vorgang das stillschweigend (200 mit unverändertem
    Termin): Der Disponent zog den Termin heraus und er blieb einfach liegen.
    """
    order = _order(app_user)
    job = planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
    )
    assert job.status == "GEPLANT"

    planung_service.update_termin(
        app_user.id, service_job_id=job.id,
        scheduled_start=None, reason="Kunde hat den Termin abgesagt",
    )
    job.refresh_from_db()
    assert job.status == "UNGEPLANT"
    assert job.scheduled_start is None
    assert job.scheduled_end is None
    board = _board()
    assert [j.id for j in board.backlog] == [job.id]
    assert board.jobs == []


@pytest.mark.django_db
def test_update_termin_rueckstand_ohne_begruendung_wird_abgelehnt(app_user):
    """GEPLANT → UNGEPLANT ist begründungspflichtig (SERVICE_JOB_TRANSITIONS).
    Ohne Begründung scheitert der GANZE Vorgang — kein halb entplanter Termin."""
    order = _order(app_user)
    job = planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
    )
    with pytest.raises(ValueError):
        planung_service.update_termin(
            app_user.id, service_job_id=job.id, scheduled_start=None
        )
    job.refresh_from_db()
    assert job.status == "GEPLANT"
    assert job.scheduled_start is not None


@pytest.mark.django_db
def test_update_termin_rueckstand_nur_aus_geplant(app_user):
    """Ein Einsatz, der schon läuft, geht nicht zurück in den Rückstand."""
    order = _freigegebener_order(app_user)
    job = planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
    )
    for status in ("BESTAETIGT", "UNTERWEGS"):
        einsatz_service.advance_status(
            app_user.id, service_job_id=job.id, to_status=status
        )
    with pytest.raises(ValueError):
        planung_service.update_termin(
            app_user.id, service_job_id=job.id,
            scheduled_start=None, reason="Doch nicht",
        )
    job.refresh_from_db()
    assert job.status == "UNTERWEGS"
    assert job.scheduled_start is not None


@pytest.mark.django_db
def test_create_termin_vertraegt_doppelte_ids(app_user):
    """Dieselbe Ressource zweimal im Payload meint sie einmal — kein 500er am
    UNIQUE-Index."""
    order = _order(app_user)
    monteur = _user("Max Muster")
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    job = planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
        assignee_ids=[monteur.id, monteur.id],
        resource_ids=[res.id, res.id],
    )
    assert JobAssignment.objects.filter(service_job_id=job.id).count() == 1
    assert JobResource.objects.filter(service_job_id=job.id).count() == 1


@pytest.mark.django_db
def test_update_termin_kategorie_entfernen(app_user):
    order = _order(app_user)
    kat = planung_service.create_category(app_user.id, name="Vor-Ort-Termin")
    job = planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
        appointment_category_id=kat.id,
    )
    planung_service.update_termin(
        app_user.id, service_job_id=job.id, appointment_category_id=None
    )
    job.refresh_from_db()
    assert job.appointment_category_id is None


# ===========================================================================
# 6. IST-Zeiten am Statuswechsel (waren tote Struktur)
# ===========================================================================

@pytest.mark.django_db
def test_actual_start_und_end_werden_gestempelt(app_user):
    """`actual_start`/`actual_end` existierten seit 0014, wurden aber von keinem
    Service je gesetzt. Der Statusautomat ist die einzige Stelle, an der das
    System sicher weiß, wann die Arbeit begann und endete."""
    order = _freigegebener_order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=_t(14, 8), scheduled_end=_t(14, 16),
    )
    for status in ("GEPLANT", "BESTAETIGT", "UNTERWEGS"):
        einsatz_service.advance_status(
            app_user.id, service_job_id=job.id, to_status=status
        )
    job.refresh_from_db()
    # UNTERWEGS ist die Anfahrt, nicht der Arbeitsbeginn.
    assert job.actual_start is None

    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="VOR_ORT"
    )
    job.refresh_from_db()
    assert job.actual_start is not None
    erster_start = job.actual_start

    # Pause und Rückkehr überschreiben den ersten Arbeitsbeginn NICHT.
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="PAUSIERT"
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="VOR_ORT"
    )
    job.refresh_from_db()
    assert job.actual_start == erster_start
    assert job.actual_end is None

    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="ABGESCHLOSSEN"
    )
    job.refresh_from_db()
    assert job.actual_end is not None
