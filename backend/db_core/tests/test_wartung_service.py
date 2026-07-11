"""Service-Tests der Wartungs-Schicht (maintenance.maintenance_contract) gegen die
echte Test-DB.

Der Statusautomat (AKTIV↔INAKTIV, INAKTIV→ARCHIVIERT) wird sowohl im Service als
auch per maintenance-eigenem Trigger erzwungen. Fälligkeits-Auslösung
protokolliert append-only in maintenance_event und rückt next_due_date vor.
"""
from datetime import date, timedelta

import pytest

from db_core.models import (
    MaintenanceContract,
    MaintenanceEvent,
    Project,
    Task,
    WorkOrder,
)
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import wartung as wartung_service


def _property(app_user, name="Wartungsobjekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user, first="Karl", last="Kunde"):
    return identity_service.create_person(app_user.id, first_name=first, last_name=last)


def _contract(app_user, obj, **kwargs):
    defaults = dict(
        property_id=obj.id,
        name="Thermenwartung",
        start_date=date(2026, 6, 1),
        interval_kind="JAEHRLICH",
        due_action="AUFGABE",
    )
    defaults.update(kwargs)
    return wartung_service.create_contract(app_user.id, **defaults)


# --- Anlage ----------------------------------------------------------------

@pytest.mark.django_db
def test_create_contract_startet_aktiv(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)
    assert c.status == "AKTIV"
    assert c.contract_number.startswith("W-")
    # Erste Fälligkeit = Startdatum (bei Intervallarten).
    assert c.next_due_date == date(2026, 6, 1)


@pytest.mark.django_db
def test_create_contract_festes_datum(app_user):
    obj = _property(app_user)
    c = _contract(
        app_user, obj, interval_kind="FESTES_DATUM", fixed_date=date(2026, 9, 15)
    )
    assert c.next_due_date == date(2026, 9, 15)


@pytest.mark.django_db
def test_create_contract_tage_ohne_intervall(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        _contract(app_user, obj, interval_kind="TAGE")


@pytest.mark.django_db
def test_create_contract_festes_datum_ohne_datum(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        _contract(app_user, obj, interval_kind="FESTES_DATUM")


@pytest.mark.django_db
def test_create_contract_ungueltige_aktion(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        _contract(app_user, obj, due_action="RAKETENSTART")


# --- Statusautomat ---------------------------------------------------------

@pytest.mark.django_db
def test_status_aktiv_inaktiv_archiviert(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)
    wartung_service.set_status(app_user.id, contract_id=c.id, to_status="INAKTIV")
    c.refresh_from_db()
    assert c.status == "INAKTIV"
    wartung_service.set_status(app_user.id, contract_id=c.id, to_status="ARCHIVIERT")
    c.refresh_from_db()
    assert c.status == "ARCHIVIERT"


@pytest.mark.django_db
def test_archivieren_nur_aus_inaktiv(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)
    with pytest.raises(ValueError):
        wartung_service.set_status(app_user.id, contract_id=c.id, to_status="ARCHIVIERT")


@pytest.mark.django_db
def test_archiviert_ist_final(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)
    wartung_service.set_status(app_user.id, contract_id=c.id, to_status="INAKTIV")
    wartung_service.set_status(app_user.id, contract_id=c.id, to_status="ARCHIVIERT")
    with pytest.raises(ValueError):
        wartung_service.set_status(app_user.id, contract_id=c.id, to_status="AKTIV")


# --- Fälligkeits-Auslösung -------------------------------------------------

@pytest.mark.django_db
def test_trigger_erzeugt_event_und_rueckt_faelligkeit_vor(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)  # JAEHRLICH, next_due 2026-06-01
    event, contract = wartung_service.trigger_action(app_user.id, contract_id=c.id)
    assert event.action == "AUFGABE"
    assert event.due_date == date(2026, 6, 1)
    # JAEHRLICH → nächste Fälligkeit ein Jahr später.
    assert contract.next_due_date == date(2027, 6, 1)
    assert MaintenanceEvent.objects.filter(contract_id=c.id).count() == 1


@pytest.mark.django_db
def test_trigger_aufgabe_erzeugt_task(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj, due_action="AUFGABE")
    event, _ = wartung_service.trigger_action(app_user.id, contract_id=c.id)
    assert event.result_object_type == "workflow.task"
    assert Task.objects.filter(id=event.result_object_id).exists()


@pytest.mark.django_db
def test_trigger_festes_datum_setzt_faelligkeit_none(app_user):
    obj = _property(app_user)
    c = _contract(
        app_user, obj, interval_kind="FESTES_DATUM", fixed_date=date(2026, 9, 15),
        due_action="BENACHRICHTIGUNG",
    )
    _, contract = wartung_service.trigger_action(app_user.id, contract_id=c.id)
    assert contract.next_due_date is None


@pytest.mark.django_db
def test_trigger_festes_datum_nicht_doppelt(app_user):
    """FESTES_DATUM: nach der einmaligen Auslösung (next_due=None) kein zweites Mal."""
    obj = _property(app_user)
    c = _contract(
        app_user, obj, interval_kind="FESTES_DATUM", fixed_date=date(2026, 9, 15),
        due_action="BENACHRICHTIGUNG",
    )
    wartung_service.trigger_action(app_user.id, contract_id=c.id)
    with pytest.raises(ValueError):
        wartung_service.trigger_action(app_user.id, contract_id=c.id)


@pytest.mark.django_db
def test_trigger_nur_aktiv(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)
    wartung_service.set_status(app_user.id, contract_id=c.id, to_status="INAKTIV")
    with pytest.raises(ValueError):
        wartung_service.trigger_action(app_user.id, contract_id=c.id)


@pytest.mark.django_db
def test_monatlich_rueckt_einen_monat_vor(app_user):
    obj = _property(app_user)
    c = _contract(
        app_user, obj, interval_kind="MONATLICH", start_date=date(2026, 1, 31),
        due_action="BENACHRICHTIGUNG",
    )
    _, contract = wartung_service.trigger_action(app_user.id, contract_id=c.id)
    # 31.01. + 1 Monat → 28.02. (Tages-Clamping).
    assert contract.next_due_date == date(2026, 2, 28)


# --- Echte Folgeobjekte PROJEKT/AUFTRAG ------------------------------------

@pytest.mark.django_db
def test_trigger_projekt_erzeugt_projekt(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj, due_action="PROJEKT")
    event, _ = wartung_service.trigger_action(app_user.id, contract_id=c.id)
    assert event.result_object_type == "workflow.project"
    assert Project.objects.filter(id=event.result_object_id).exists()


@pytest.mark.django_db
def test_trigger_auftrag_erzeugt_work_order_entwurf(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj, due_action="AUFTRAG")
    event, _ = wartung_service.trigger_action(app_user.id, contract_id=c.id)
    assert event.result_object_type == "workflow.work_order"
    order = WorkOrder.objects.filter(id=event.result_object_id).first()
    assert order is not None
    # Auftrag entsteht als Entwurf an der Liegenschaft des Vertrags.
    assert order.status == "ENTWURF"
    assert order.property_id == obj.id


@pytest.mark.django_db
def test_trigger_benachrichtigung_ohne_folgeobjekt(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj, due_action="BENACHRICHTIGUNG")
    event, _ = wartung_service.trigger_action(app_user.id, contract_id=c.id)
    assert event.result_object_type is None
    assert event.result_object_id is None


# --- Nachhol-/Idempotenz-Logik des Schedulers (catch_up_until) --------------

@pytest.mark.django_db
def test_catch_up_rueckt_ueber_stichtag_und_erzeugt_ein_event(app_user):
    """Mehrere verpasste Wochen: EINE Auslösung, Plan bis über den Stichtag vor."""
    obj = _property(app_user)
    stichtag = date(2026, 7, 11)
    c = _contract(
        app_user, obj, interval_kind="WOECHENTLICH",
        start_date=stichtag - timedelta(weeks=3),  # 3 Wochen überfällig
        due_action="BENACHRICHTIGUNG",
    )
    assert c.next_due_date == stichtag - timedelta(weeks=3)
    event, contract = wartung_service.trigger_action(
        app_user.id, contract_id=c.id, catch_up_until=stichtag
    )
    # Genau ein Event, dessen due_date die ursprüngliche (älteste) Fälligkeit ist.
    assert MaintenanceEvent.objects.filter(contract_id=c.id).count() == 1
    assert event.due_date == stichtag - timedelta(weeks=3)
    # Plan steht jetzt hinter dem Stichtag → nicht mehr fällig.
    assert contract.next_due_date > stichtag


@pytest.mark.django_db
def test_ohne_catch_up_rueckt_nur_ein_intervall(app_user):
    """Default (manuelle Auslösung): genau ein Intervall, kein catch_up."""
    obj = _property(app_user)
    start = date(2026, 6, 1)
    c = _contract(
        app_user, obj, interval_kind="WOECHENTLICH", start_date=start,
        due_action="BENACHRICHTIGUNG",
    )
    _, contract = wartung_service.trigger_action(app_user.id, contract_id=c.id)
    assert contract.next_due_date == start + timedelta(weeks=1)
