"""Tests der Fälligkeiten-Engine (maintenance.due_item, Migration 0071).

Der Kern: der Scheduler darf beliebig oft laufen. Deshalb steht die Idempotenz
hier an erster Stelle — und zwar gegen die echte DB, weil sie nicht vom Code,
sondern von drei statusunabhängigen UNIQUE-Indizes garantiert wird.

Deckt ab: Idempotenz (zweimal laufen = keine Dubletten), Vorlauf, alle drei
Fristenarten, Feiertags-/Sonntagsverschiebung eines abgeleiteten Termins,
Erledigen (mit Folgeobjekt) und Verwerfen (mit Begründung), der Termin landet im
Plantafel-Rückstand, ein verworfener Eintrag taucht NICHT wieder auf, und die
physischen Schutzregeln (kein DELETE, unveränderlicher Anker, finaler Status).
"""
import uuid
from datetime import date, timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.utils import ProgrammingError

from db_core.models import (
    DueItem,
    Holiday,
    Inspection,
    MaintenanceContract,
    MaintenanceEvent,
    Project,
    ServiceJob,
    Task,
    Warranty,
    WorkOrder,
)
from db_core.services import auftrag as auftrag_service
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services import gewaehrleistung as gewaehrleistung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import pruefung as pruefung_service
from db_core.services import wartung as wartung_service

STICHTAG = date(2026, 7, 13)  # ein Montag


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------

def _property(app_user, name="Fälligkeitsobjekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user, first="Karla", last="Kundin"):
    return identity_service.create_person(
        app_user.id, first_name=first, last_name=last
    )


def _vertrag(app_user, obj, **kwargs):
    defaults = dict(
        property_id=obj.id,
        name="Thermenwartung",
        start_date=STICHTAG,
        interval_kind="JAEHRLICH",
        due_action="BENACHRICHTIGUNG",
        lead_time_days=0,
    )
    defaults.update(kwargs)
    return wartung_service.create_contract(app_user.id, **defaults)


def _pruefart(app_user, **kwargs):
    defaults = dict(
        name=f"Prüfart {uuid.uuid4().hex[:6]}",
        interval_kind="JAEHRLICH",
        lead_time_days=0,
    )
    defaults.update(kwargs)
    return pruefung_service.create_inspection_type(app_user.id, **defaults)


def _pruefung(app_user, obj, art, **kwargs):
    defaults = dict(
        inspection_type_id=art.id,
        property_id=obj.id,
        start_date=STICHTAG,
    )
    defaults.update(kwargs)
    return pruefung_service.create_inspection(app_user.id, **defaults)


def _abgeschlossener_auftrag(app_user, obj, kunde):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Heizungstausch"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN"):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status=to
        )
    order.refresh_from_db()
    return order


def _gewaehrleistung(app_user, obj, kunde, **kwargs):
    order = _abgeschlossener_auftrag(app_user, obj, kunde)
    defaults = dict(
        work_order_id=order.id,
        start_date=STICHTAG - timedelta(days=365 * 5),  # läuft in Kürze ab
        duration_months=60,
        lead_time_days=30,
    )
    defaults.update(kwargs)
    return gewaehrleistung_service.create_warranty(app_user.id, **defaults)


# ---------------------------------------------------------------------------
# Idempotenz — die Kernanforderung
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_zweiter_lauf_erzeugt_keine_dublette(app_user):
    obj = _property(app_user)
    c = _vertrag(app_user, obj)

    erst = faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)
    assert len(erst["WARTUNG"]) == 1

    zweit = faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)
    assert zweit["WARTUNG"] == []  # nichts Neues

    assert DueItem.objects.filter(contract_id=c.id).count() == 1


@pytest.mark.django_db
def test_idempotenz_ist_physisch(app_user):
    """Der UNIQUE-Index verbietet die Dublette — nicht nur der Service."""
    obj = _property(app_user)
    c = _vertrag(app_user, obj)
    faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DueItem.objects.create(
                id=uuid.uuid4(), kind="WARTUNG", contract_id=c.id,
                property_id=obj.id, title="Dublette von Hand",
                due_date=c.next_due_date, lead_time_days=0,
            )


@pytest.mark.django_db
def test_dreifachlauf_ueber_alle_arten(app_user):
    obj = _property(app_user)
    kunde = _party(app_user)
    _vertrag(app_user, obj)
    _pruefung(app_user, obj, _pruefart(app_user))
    _gewaehrleistung(app_user, obj, kunde)

    for _ in range(3):
        faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)

    assert DueItem.objects.filter(kind="WARTUNG").count() == 1
    assert DueItem.objects.filter(kind="PRUEFUNG").count() == 1
    assert DueItem.objects.filter(kind="GEWAEHRLEISTUNG").count() == 1


# ---------------------------------------------------------------------------
# Vorlauf
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ausserhalb_des_vorlaufs_entsteht_nichts(app_user):
    obj = _property(app_user)
    # Fällig in 100 Tagen, Vorlauf 30 → am Stichtag noch nicht sichtbar.
    _vertrag(app_user, obj, start_date=STICHTAG + timedelta(days=100),
             lead_time_days=30)
    ergebnis = faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)
    assert ergebnis["WARTUNG"] == []
    assert DueItem.objects.count() == 0


@pytest.mark.django_db
def test_im_vorlauf_entsteht_die_faelligkeit_vorab(app_user):
    obj = _property(app_user)
    faellig = STICHTAG + timedelta(days=20)
    _vertrag(app_user, obj, start_date=faellig, lead_time_days=30)

    ergebnis = faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)
    assert len(ergebnis["WARTUNG"]) == 1
    item = ergebnis["WARTUNG"][0]
    assert item.due_date == faellig      # in der Zukunft …
    assert item.status == "OFFEN"        # … aber JETZT schon sichtbar
    assert item.lead_time_days == 30


@pytest.mark.django_db
def test_vorlauf_grenze_exakt(app_user):
    obj = _property(app_user)
    # Vorlauf beginnt exakt am Stichtag (due - lead == stichtag).
    _vertrag(app_user, obj, start_date=STICHTAG + timedelta(days=30),
             lead_time_days=30)
    assert len(faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG)["WARTUNG"]) == 1


# ---------------------------------------------------------------------------
# Die drei Fristenarten
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_pruefung_ohne_wartungsvertrag(app_user):
    """Prüffristen brauchen KEINEN Wartungsvertrag — das ist ihr Sinn."""
    obj = _property(app_user)
    art = _pruefart(app_user, name="Legionellenprüfung", lead_time_days=60)
    p = _pruefung(app_user, obj, art)

    assert MaintenanceContract.objects.count() == 0  # kein Vertrag im Spiel
    assert p.lead_time_days == 60                    # aus der Prüfart kopiert
    assert p.next_due_date == STICHTAG

    items = faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)["PRUEFUNG"]
    assert len(items) == 1
    assert items[0].kind == "PRUEFUNG"
    assert items[0].property_id == obj.id


@pytest.mark.django_db
def test_pruefung_kopiert_intervall_aus_der_pruefart(app_user):
    """Eine spätere Änderung der Prüfart verschiebt laufende Prüfungen NICHT."""
    obj = _property(app_user)
    art = _pruefart(app_user, interval_kind="JAEHRLICH", lead_time_days=30)
    p = _pruefung(app_user, obj, art)

    pruefung_service.update_inspection_type(
        app_user.id, inspection_type_id=art.id,
        interval_kind="TAGE", interval_days=7, lead_time_days=1,
    )
    p.refresh_from_db()
    assert p.interval_kind == "JAEHRLICH"  # unverändert
    assert p.lead_time_days == 30


@pytest.mark.django_db
def test_gewaehrleistung_aus_abgeschlossenem_auftrag(app_user):
    obj = _property(app_user)
    kunde = _party(app_user)
    w = _gewaehrleistung(app_user, obj, kunde, duration_months=60)
    assert w.end_date == date(w.start_date.year + 5, w.start_date.month,
                              w.start_date.day)

    items = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["GEWAEHRLEISTUNG"]
    assert len(items) == 1
    assert items[0].due_date == w.end_date


@pytest.mark.django_db
def test_gewaehrleistung_nur_bei_erbrachter_leistung(app_user):
    """Ein Auftrag im ENTWURF hat nichts zu gewährleisten."""
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Noch nicht gemacht"
    )
    with pytest.raises(ValueError, match="erbrachter Leistung"):
        gewaehrleistung_service.create_warranty(
            app_user.id, work_order_id=order.id
        )


@pytest.mark.django_db
def test_gewaehrleistung_je_auftrag_nur_eine(app_user):
    obj = _property(app_user)
    kunde = _party(app_user)
    w = _gewaehrleistung(app_user, obj, kunde)
    with pytest.raises(ValueError, match="bereits eine Gewährleistung"):
        gewaehrleistung_service.create_warranty(
            app_user.id, work_order_id=w.work_order_id
        )


@pytest.mark.django_db
def test_gewaehrleistung_frist_je_auftrag_einstellbar(app_user):
    obj = _property(app_user)
    kunde = _party(app_user)
    w = _gewaehrleistung(app_user, obj, kunde, duration_months=60)
    w = gewaehrleistung_service.update_warranty(
        app_user.id, warranty_id=w.id, duration_months=24
    )
    assert w.duration_months == 24
    # end_date wird IMMER neu gerechnet, nie separat gesetzt.
    assert w.end_date == faelligkeit_service.add_months(w.start_date, 24)


@pytest.mark.django_db
def test_fristaenderung_verwirft_die_alte_faelligkeit(app_user):
    """due_date ist unveränderlich → die alte Fälligkeit wird begründet verworfen."""
    obj = _property(app_user)
    kunde = _party(app_user)
    w = _gewaehrleistung(app_user, obj, kunde)
    faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)
    alt = DueItem.objects.get(warranty_id=w.id)
    assert alt.status == "OFFEN"

    gewaehrleistung_service.update_warranty(
        app_user.id, warranty_id=w.id, duration_months=72
    )
    alt.refresh_from_db()
    assert alt.status == "VERWORFEN"
    assert "gegenstandslos" in alt.resolution_note


@pytest.mark.django_db
def test_vertriebshinweis_nur_ohne_wartungsvertrag(app_user):
    """Der Hinweis „Anlage ohne Wartungsvertrag" ist ein Verkaufsargument —
    und verkürzt ausdrücklich KEINE Frist."""
    obj = _property(app_user)
    kunde = _party(app_user)
    w = _gewaehrleistung(app_user, obj, kunde, is_machinery=True)
    hinweis = gewaehrleistung_service.vertriebshinweis(w)
    assert hinweis is not None
    assert "Wartungsvertrag" in hinweis
    assert "keine Rechtsauskunft" in hinweis
    assert w.duration_months == 60  # unverändert — kein automatischer Eingriff

    # Mit aktivem Wartungsvertrag an der Liegenschaft verschwindet der Hinweis.
    _vertrag(app_user, obj)
    assert gewaehrleistung_service.vertriebshinweis(w) is None

    # Ohne maschinelle Anlage ebenfalls kein Hinweis.
    obj2 = _property(app_user, name="Zweites Objekt")
    w2 = _gewaehrleistung(app_user, obj2, kunde, is_machinery=False)
    assert gewaehrleistung_service.vertriebshinweis(w2) is None


# ---------------------------------------------------------------------------
# Feiertage / Sonntage
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_werktagsverschiebung_sonntag(app_user):
    sonntag = date(2026, 7, 12)
    assert sonntag.weekday() == 6
    assert faelligkeit_service.naechster_werktag(sonntag) == date(2026, 7, 13)


@pytest.mark.django_db
def test_werktagsverschiebung_feiertag(app_user):
    """3. Oktober 2026 ist ein Samstag — ein bundesweiter Feiertag laut hr.holiday."""
    feiertag = date(2026, 10, 3)
    assert Holiday.objects.filter(day=feiertag, region__isnull=True).exists()
    verschoben = faelligkeit_service.naechster_werktag(feiertag)
    assert verschoben > feiertag
    assert verschoben not in {feiertag}
    assert verschoben.weekday() != 6


@pytest.mark.django_db
def test_faelligkeit_selbst_wird_nie_verschoben(app_user):
    """Eine Frist ist eine Frist. Verschoben wird nur der abgeleitete Termin."""
    obj = _property(app_user)
    sonntag = date(2026, 7, 12)
    _vertrag(app_user, obj, start_date=sonntag)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    assert item.due_date == sonntag  # NICHT verschoben
    vorschlag, hinweis = faelligkeit_service.termin_vorschlag(item.due_date)
    assert vorschlag == date(2026, 7, 13)  # nur der Vorschlag
    assert "Sonntag" in hinweis


@pytest.mark.django_db
def test_wunschtermin_wird_auf_den_werktag_geschoben(app_user):
    """Ein Wunschtermin auf einen Sonntag wird als nächster Werktag vermerkt —
    verschoben wird der abgeleitete Termin, nie die Frist."""
    obj = _property(app_user)
    sonntag = date(2026, 7, 12)
    _vertrag(app_user, obj, start_date=sonntag)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    item, hinweis = faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id,
        folgeaktion="TERMIN", termin_datum=sonntag,
    )
    assert hinweis is not None and "Sonntag" in hinweis
    job = ServiceJob.objects.get(id=item.result_object_id)
    assert "13.07.2026" in job.access_instructions
    assert job.scheduled_start is None  # kein unsichtbarer Halb-Plan


@pytest.mark.django_db
def test_termin_MIT_datum_landet_ebenfalls_im_rueckstand(app_user):
    """Der DEFAULT-Fall des Dialogs: die Dispo gibt ein Datum an.

    Der Einsatz muss trotzdem im RÜCKSTAND landen. Ein Einsatz mit Zeitraum, aber
    ohne Zuweisung wäre in der Plantafel unsichtbar: er fiele aus dem Rückstand
    (der filtert auf „kein Beginn") und bekäme im Raster keine Bahn (Kacheln
    hängen an Monteur/Ressource). Das Datum ist deshalb ein Wunschtermin am
    Einsatz, kein Zeitraum.
    """
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    item, hinweis = faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id,
        folgeaktion="TERMIN", termin_datum=STICHTAG,  # Montag, kein Feiertag
    )
    job = ServiceJob.objects.get(id=item.result_object_id)
    assert job.status == "UNGEPLANT"          # = Rückstand
    assert job.scheduled_start is None        # nicht halb geplant
    assert job.scheduled_end is None
    assert "Wunschtermin" in job.access_instructions
    assert "13.07.2026" in job.access_instructions
    assert hinweis is not None and "Rückstand" in hinweis

    # Der Rückstand der Plantafel zeigt ihn wirklich (dieselbe Bedingung wie
    # planung.board_daten: kein Beginn, nicht abgeschlossen/ausgefallen).
    assert ServiceJob.objects.filter(
        id=job.id, scheduled_start__isnull=True
    ).exclude(status__in=("ABGESCHLOSSEN", "AUSGEFALLEN")).exists()


@pytest.mark.django_db
def test_wunschtermin_und_notiz_stehen_beide_am_einsatz(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    item, _ = faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="TERMIN",
        termin_datum=STICHTAG, notiz="Schlüssel beim Hausmeister.",
    )
    job = ServiceJob.objects.get(id=item.result_object_id)
    assert "Wunschtermin" in job.access_instructions
    assert "Schlüssel beim Hausmeister." in job.access_instructions


# ---------------------------------------------------------------------------
# Erledigen — Folgeobjekte über die normalen Services
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_termin_ohne_datum_landet_im_plantafel_rueckstand(app_user):
    """Der Kernnutzen: aus einer Fälligkeit wird ein ungeplanter Einsatz — genau
    das, was die Plantafel als Rückstand zeigt."""
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    item, _ = faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="TERMIN"
    )
    assert item.status == "ERLEDIGT"
    assert item.result_object_type == "workflow.service_job"

    job = ServiceJob.objects.get(id=item.result_object_id)
    assert job.status == "UNGEPLANT"        # = Rückstand
    assert job.scheduled_start is None
    assert job.work_order_id is None        # freier Termin (Migration 0062)
    assert job.property_id == obj.id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "aktion,typ,model",
    [
        ("AUFGABE", "workflow.task", Task),
        ("AUFTRAG", "workflow.work_order", WorkOrder),
        ("PROJEKT", "workflow.project", Project),
    ],
)
def test_folgeobjekte(app_user, aktion, typ, model):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    item, _ = faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion=aktion
    )
    assert item.result_object_type == typ
    obj_row = model.objects.get(id=item.result_object_id)
    # Die Folgeobjekte laufen durch die NORMALEN Statusautomaten.
    if model is WorkOrder:
        assert obj_row.status == "ENTWURF"


@pytest.mark.django_db
def test_erledigen_ohne_folgeobjekt_braucht_vermerk(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    with pytest.raises(ValueError, match="Vermerk"):
        faelligkeit_service.erledigen(
            app_user.id, due_item_id=item.id, folgeaktion="KEINE"
        )
    item, _ = faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="KEINE",
        notiz="Kunde hat telefonisch bestätigt, dass geprüft wurde.",
    )
    assert item.status == "ERLEDIGT"
    assert item.result_object_id is None


@pytest.mark.django_db
def test_erledigen_schreibt_die_quelle_fort(app_user):
    obj = _property(app_user)
    c = _vertrag(app_user, obj)  # JAEHRLICH ab STICHTAG
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="AUFGABE"
    )
    c.refresh_from_db()
    assert c.next_due_date == date(2027, 7, 13)  # ein Jahr weiter


@pytest.mark.django_db
def test_zweimal_erledigen_geht_nicht(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="AUFGABE"
    )
    with pytest.raises(ValueError, match="bereits erledigt"):
        faelligkeit_service.erledigen(
            app_user.id, due_item_id=item.id, folgeaktion="AUFGABE"
        )
    assert Task.objects.count() == 1  # KEIN zweites Folgeobjekt


# ---------------------------------------------------------------------------
# Verwerfen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_verwerfen_braucht_begruendung(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    with pytest.raises(ValueError, match="begründungspflichtig"):
        faelligkeit_service.verwerfen(
            app_user.id, due_item_id=item.id, begruendung="   "
        )


@pytest.mark.django_db
def test_verwerfen_ist_kein_delete(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    item = faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id,
        begruendung="Objekt wurde verkauft, Vertrag läuft aus.",
    )
    assert item.status == "VERWORFEN"
    assert item.resolution_note.startswith("Objekt wurde verkauft")
    assert item.resolved_by_id == app_user.id
    assert DueItem.objects.filter(id=item.id).exists()  # die Zeile bleibt


@pytest.mark.django_db
def test_verworfener_eintrag_taucht_nicht_wieder_auf(app_user):
    """DIE Anforderung: der Scheduler darf einen verworfenen Eintrag nicht
    wiederbeleben — auch nicht nach beliebig vielen Läufen."""
    obj = _property(app_user)
    c = _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    verworfen_am = item.due_date

    faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id, begruendung="Kunde will nicht."
    )
    # Der Vertrag wurde fortgeschrieben — er verstummt nicht.
    c.refresh_from_db()
    assert c.next_due_date == date(2027, 7, 13)

    for _ in range(3):
        faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)

    offen = DueItem.objects.filter(contract_id=c.id, status="OFFEN")
    assert offen.count() == 0
    assert DueItem.objects.filter(
        contract_id=c.id, due_date=verworfen_am
    ).count() == 1  # genau die eine, verworfene Zeile

    # Auch ein Lauf im nächsten Jahr erzeugt nur die NÄCHSTE Fälligkeit.
    neu = faelligkeit_service.generiere(
        app_user.id, stichtag=date(2027, 7, 13)
    )["WARTUNG"]
    assert len(neu) == 1
    assert neu[0].due_date == date(2027, 7, 13)


@pytest.mark.django_db
def test_verwerfen_schreibt_auch_pruefung_fort(app_user):
    obj = _property(app_user)
    p = _pruefung(app_user, obj, _pruefart(app_user))
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["PRUEFUNG"][0]
    faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id, begruendung="Anlage stillgelegt."
    )
    p.refresh_from_db()
    assert p.next_due_date == date(2027, 7, 13)


@pytest.mark.django_db
def test_verworfene_faelligkeit_nicht_mehr_erledigbar(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id, begruendung="Nicht nötig."
    )
    with pytest.raises(ValueError, match="bereits verworfen"):
        faelligkeit_service.erledigen(
            app_user.id, due_item_id=item.id, folgeaktion="AUFGABE"
        )


# ---------------------------------------------------------------------------
# Physische Schutzregeln (die DB, nicht der Service)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_due_item_kein_delete(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    with pytest.raises(ProgrammingError):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "DELETE FROM maintenance.due_item WHERE id = %s", [str(item.id)]
            )


@pytest.mark.django_db
def test_due_item_anker_und_datum_unveraenderlich(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    with pytest.raises(ProgrammingError, match="unveränderlich"):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE maintenance.due_item SET due_date = %s WHERE id = %s",
                [date(2030, 1, 1), str(item.id)],
            )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "spalte,wert",
    [("property_id", None), ("lead_time_days", 99)],
)
def test_due_item_objekt_und_vorlauf_unveraenderlich(app_user, spalte, wert):
    """Beide sind Schnappschüsse der Quelle: die Ansicht filtert nach Objekt, der
    Vorlauf begründet die Sichtbarkeit. Nachträglich ändern hieße, die
    Vergangenheit umzuschreiben."""
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    with pytest.raises(ProgrammingError, match="unveränderlich"):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                f"UPDATE maintenance.due_item SET {spalte} = %s WHERE id = %s",
                [wert, str(item.id)],
            )


@pytest.mark.django_db
def test_due_item_status_ist_final(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id, begruendung="Weg damit."
    )
    # VERWORFEN → ERLEDIGT wäre eine nachträgliche Umschreibung der Historie.
    with pytest.raises(ProgrammingError, match="bereits VERWORFEN"):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE maintenance.due_item SET status = 'ERLEDIGT' WHERE id = %s",
                [str(item.id)],
            )


@pytest.mark.django_db
def test_anker_muss_zur_art_passen(app_user):
    """Eine „Gewährleistung" an einem Wartungsvertrag ist physisch unmöglich."""
    obj = _property(app_user)
    c = _vertrag(app_user, obj)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DueItem.objects.create(
                id=uuid.uuid4(), kind="GEWAEHRLEISTUNG", contract_id=c.id,
                property_id=obj.id, title="Falscher Anker",
                due_date=STICHTAG, lead_time_days=0,
            )


@pytest.mark.django_db
def test_verwerfen_ohne_begruendung_ist_physisch_verboten(app_user):
    """Auch an der Service-Prüfung vorbei: der CHECK verlangt die Begründung."""
    obj = _property(app_user)
    _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE maintenance.due_item "
                "SET status = 'VERWORFEN', resolved_at = now(), resolved_by = %s "
                "WHERE id = %s",
                [str(app_user.id), str(item.id)],
            )


# ---------------------------------------------------------------------------
# Verzahnung mit dem Bestands-Scheduler (wartung.trigger_action)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trigger_action_schliesst_die_faelligkeit_mit(app_user):
    """Es gibt nur EINE Wahrheit: die Vollautomatik schließt die due_item mit."""
    obj = _property(app_user)
    c = _vertrag(app_user, obj, due_action="AUFGABE", lead_time_days=14)
    # Erst im Vorlauf erzeugen …
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    assert item.status == "OFFEN"

    # … dann löst die Vollautomatik aus.
    event, _ = wartung_service.trigger_action(
        app_user.id, contract_id=c.id, catch_up_until=STICHTAG
    )
    item.refresh_from_db()
    assert item.status == "ERLEDIGT"
    assert item.result_object_id == event.result_object_id
    assert item.result_object_type == "workflow.task"
    assert DueItem.objects.filter(contract_id=c.id).count() == 1


@pytest.mark.django_db
def test_trigger_action_holt_den_nachweis_nach(app_user):
    """Fälligkeit ohne Vorlauf, Scheduler lief nie → der Nachweis entsteht
    trotzdem, direkt als ERLEDIGT."""
    obj = _property(app_user)
    c = _vertrag(app_user, obj, due_action="AUFGABE")
    assert DueItem.objects.count() == 0

    wartung_service.trigger_action(
        app_user.id, contract_id=c.id, catch_up_until=STICHTAG
    )
    item = DueItem.objects.get(contract_id=c.id)
    assert item.status == "ERLEDIGT"
    assert item.due_date == STICHTAG
    assert MaintenanceEvent.objects.filter(contract_id=c.id).count() == 1


@pytest.mark.django_db
def test_trigger_action_ueberschreibt_verworfene_faelligkeit_nicht(app_user):
    obj = _property(app_user)
    c = _vertrag(app_user, obj, due_action="AUFGABE")
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id, begruendung="Diesmal nicht."
    )
    c.refresh_from_db()
    # Der Vertrag steht jetzt auf 2027 → am Stichtag nicht mehr fällig.
    assert c.next_due_date == date(2027, 7, 13)

    item.refresh_from_db()
    assert item.status == "VERWORFEN"  # unangetastet


# ---------------------------------------------------------------------------
# Liste
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_liste_filtert_und_zaehlt_ueberfaellige(app_user):
    obj = _property(app_user)
    _vertrag(app_user, obj, name="A", start_date=STICHTAG - timedelta(days=10))
    _pruefung(app_user, obj, _pruefart(app_user))
    faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)

    alle = faelligkeit_service.liste(status="OFFEN", stichtag=STICHTAG)
    assert alle.count() == 2
    nur_wartung = faelligkeit_service.liste(
        status="OFFEN", kind="WARTUNG", stichtag=STICHTAG
    )
    assert nur_wartung.count() == 1
    assert faelligkeit_service.ueberfaellig_count(STICHTAG) == 1


# ---------------------------------------------------------------------------
# Die Gegenrichtung: von Hand erledigen schreibt Historie UND holt nach
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erledigen_schreibt_die_auslose_historie_des_vertrags(app_user):
    """Es darf nicht zwei Wahrheiten geben: wird eine Wartung von Hand erledigt,
    weist die Historie des Vertrags das genauso nach wie bei der Vollautomatik."""
    obj = _property(app_user)
    c = _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]

    item, _ = faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="TERMIN",
        termin_datum=STICHTAG,
    )
    event = MaintenanceEvent.objects.get(contract_id=c.id)
    assert event.action == "TERMIN"
    assert event.due_date == item.due_date
    assert event.result_object_type == "workflow.service_job"
    assert event.result_object_id == item.result_object_id
    assert event.triggered_by_id == app_user.id


@pytest.mark.django_db
def test_erledigen_ohne_folgeobjekt_wird_als_vermerk_protokolliert(app_user):
    obj = _property(app_user)
    c = _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="KEINE",
        notiz="Der Kunde hat die Therme selbst warten lassen.",
    )
    event = MaintenanceEvent.objects.get(contract_id=c.id)
    assert event.action == "VERMERK"
    assert event.result_object_id is None
    assert "selbst warten" in event.note


@pytest.mark.django_db
def test_verwerfen_wird_am_vertrag_protokolliert(app_user):
    obj = _property(app_user)
    c = _vertrag(app_user, obj)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["WARTUNG"][0]
    faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id, begruendung="Objekt wird verkauft."
    )
    event = MaintenanceEvent.objects.get(contract_id=c.id)
    assert event.action == "VERWORFEN"
    assert "Objekt wird verkauft." in event.note


@pytest.mark.django_db
def test_erledigen_holt_einen_ueberfaelligen_vertrag_nach(app_user):
    """Ein mehrere Intervalle überfälliger Vertrag darf nach dem Erledigen nicht
    fällig BLEIBEN — sonst löst die nächtliche Vollautomatik ihn erneut aus und
    erzeugt ein zweites Folgeobjekt für dieselbe Wartung."""
    heute = date.today()
    obj = _property(app_user)
    c = _vertrag(
        app_user, obj, interval_kind="MONATLICH",
        start_date=heute - timedelta(days=200),  # ~6 Intervalle überfällig
        due_action="AUFGABE",
    )
    item = faelligkeit_service.generiere(app_user.id, stichtag=heute)["WARTUNG"][0]
    assert item.due_date < heute

    faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="AUFGABE"
    )
    c.refresh_from_db()
    assert c.next_due_date > heute, "Der Vertrag ist immer noch fällig."

    # Der Scheduler-Lauf (Phase 2) findet nichts mehr — kein zweites Folgeobjekt.
    assert not MaintenanceContract.objects.filter(
        id=c.id, status="AKTIV", next_due_date__lte=heute
    ).exists()
    assert Task.objects.count() == 1


@pytest.mark.django_db
def test_verwerfen_holt_einen_ueberfaelligen_vertrag_ebenfalls_nach(app_user):
    heute = date.today()
    obj = _property(app_user)
    c = _vertrag(
        app_user, obj, interval_kind="MONATLICH",
        start_date=heute - timedelta(days=200),
    )
    item = faelligkeit_service.generiere(app_user.id, stichtag=heute)["WARTUNG"][0]
    faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id, begruendung="Heizung wird ersetzt."
    )
    c.refresh_from_db()
    assert c.next_due_date > heute


# ---------------------------------------------------------------------------
# Umdatieren der Quelle — keine zwei offenen Fälligkeiten, kein stiller Tod
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_pruefung_umdatieren_verwirft_die_alte_faelligkeit(app_user):
    """Sonst stünden nach dem Umdatieren ZWEI offene Fälligkeiten für dieselbe
    Prüfung — die alte zum alten Datum, die neue vom Scheduler."""
    obj = _property(app_user)
    p = _pruefung(app_user, obj, _pruefart(app_user))
    faelligkeit_service.generiere(app_user.id, stichtag=STICHTAG)

    neu = STICHTAG + timedelta(days=14)
    pruefung_service.update_inspection(
        app_user.id, inspection_id=p.id, next_due_date=neu
    )
    alt = DueItem.objects.get(inspection_id=p.id, due_date=STICHTAG)
    assert alt.status == "VERWORFEN"
    assert "geändert" in alt.resolution_note

    faelligkeit_service.generiere(app_user.id, stichtag=neu)
    offen = DueItem.objects.filter(inspection_id=p.id, status="OFFEN")
    assert offen.count() == 1
    assert offen.first().due_date == neu


@pytest.mark.django_db
def test_pruefung_darf_nicht_auf_ein_abgeschlossenes_datum_zurueck(app_user):
    """Der Idempotenz-Index ist statusunabhängig (richtig so). Zeigte die Prüfung
    wieder auf ein abgeschlossenes Datum, könnte der Scheduler dort nie wieder
    etwas anlegen — die Prüfung wäre STILL tot. Also: klare Ansage."""
    obj = _property(app_user)
    p = _pruefung(app_user, obj, _pruefart(app_user))
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["PRUEFUNG"][0]
    faelligkeit_service.verwerfen(
        app_user.id, due_item_id=item.id, begruendung="Termin platzte."
    )

    with pytest.raises(ValueError, match="abgeschlossene Fälligkeit"):
        pruefung_service.update_inspection(
            app_user.id, inspection_id=p.id, next_due_date=STICHTAG
        )
    p.refresh_from_db()
    assert p.next_due_date == date(2027, 7, 13)  # unverändert fortgeschrieben


@pytest.mark.django_db
def test_gewaehrleistung_darf_nicht_auf_ein_abgeschlossenes_datum_zurueck(app_user):
    obj = _property(app_user)
    kunde = _party(app_user)
    w = _gewaehrleistung(app_user, obj, kunde)
    item = faelligkeit_service.generiere(
        app_user.id, stichtag=STICHTAG
    )["GEWAEHRLEISTUNG"][0]
    faelligkeit_service.erledigen(
        app_user.id, due_item_id=item.id, folgeaktion="KEINE",
        notiz="Nachbesserung eingefordert.",
    )
    altes_ende = w.end_date

    # Frist verlängern …
    gewaehrleistung_service.update_warranty(
        app_user.id, warranty_id=w.id, duration_months=72
    )
    # … und wieder zurück auf das abgeschlossene Datum: das wäre der stille Tod.
    with pytest.raises(ValueError, match="abgeschlossene Fälligkeit"):
        gewaehrleistung_service.update_warranty(
            app_user.id, warranty_id=w.id, duration_months=60
        )
    w.refresh_from_db()
    assert w.end_date != altes_ende
