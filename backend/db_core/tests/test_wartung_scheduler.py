"""Tests des Wartungs-Fälligkeits-Schedulers (Management-Command
wartung_faellige_ausloesen) gegen die echte Test-DB.

Deckt ab: fällige Verträge werden ausgelöst (AUFGABE→task, PROJEKT→project) und
next_due vorgerückt; nicht fällige und inaktive Verträge bleiben unberührt; ein
zweiter Lauf am selben Stichtag löst nicht erneut aus (Idempotenz); --dry-run
schreibt nichts; ein fehlerhafter Vertrag bricht den Lauf nicht ab.
"""
from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command

from db_core.models import (
    MaintenanceContract,
    MaintenanceEvent,
    Project,
    Task,
)
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import wartung as wartung_service

STICHTAG = "2026-07-11"
STICHTAG_D = date(2026, 7, 11)


def _property(app_user, name="Wartungsobjekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _contract(app_user, obj, **kwargs):
    defaults = dict(
        property_id=obj.id,
        name="Thermenwartung",
        start_date=STICHTAG_D,  # next_due = Startdatum → am Stichtag fällig
        interval_kind="JAEHRLICH",
        due_action="BENACHRICHTIGUNG",
    )
    defaults.update(kwargs)
    return wartung_service.create_contract(app_user.id, **defaults)


def _run(app_user, **opts):
    out, err = StringIO(), StringIO()
    call_command(
        "wartung_faellige_ausloesen",
        stichtag=STICHTAG,
        actor=str(app_user.id),
        stdout=out,
        stderr=err,
        **opts,
    )
    return out.getvalue(), err.getvalue()


@pytest.mark.django_db
def test_scheduler_loest_faellige_aus_und_rueckt_vor(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)  # JAEHRLICH, fällig am Stichtag
    _run(app_user)
    assert MaintenanceEvent.objects.filter(contract_id=c.id).count() == 1
    c.refresh_from_db()
    assert c.next_due_date == date(2027, 7, 11)  # + 1 Jahr, > Stichtag


@pytest.mark.django_db
def test_scheduler_erzeugt_folgeobjekte(app_user):
    obj = _property(app_user)
    c_auf = _contract(app_user, obj, due_action="AUFGABE")
    c_prj = _contract(app_user, obj, due_action="PROJEKT")
    _run(app_user)
    e_auf = MaintenanceEvent.objects.get(contract_id=c_auf.id)
    e_prj = MaintenanceEvent.objects.get(contract_id=c_prj.id)
    assert e_auf.result_object_type == "workflow.task"
    assert Task.objects.filter(id=e_auf.result_object_id).exists()
    assert e_prj.result_object_type == "workflow.project"
    assert Project.objects.filter(id=e_prj.result_object_id).exists()


@pytest.mark.django_db
def test_scheduler_ignoriert_nicht_faellige(app_user):
    obj = _property(app_user)
    # Fällig erst nach dem Stichtag → nicht ausgelöst.
    c = _contract(app_user, obj, start_date=date(2026, 8, 1))
    _run(app_user)
    assert MaintenanceEvent.objects.filter(contract_id=c.id).count() == 0
    c.refresh_from_db()
    assert c.next_due_date == date(2026, 8, 1)  # unverändert


@pytest.mark.django_db
def test_scheduler_ignoriert_inaktive(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)  # fällig, aber gleich INAKTIV
    wartung_service.set_status(app_user.id, contract_id=c.id, to_status="INAKTIV")
    _run(app_user)
    assert MaintenanceEvent.objects.filter(contract_id=c.id).count() == 0


@pytest.mark.django_db
def test_scheduler_idempotent_zweiter_lauf(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)
    _run(app_user)
    _run(app_user)  # selber Stichtag: nicht mehr fällig
    assert MaintenanceEvent.objects.filter(contract_id=c.id).count() == 1


@pytest.mark.django_db
def test_dry_run_schreibt_nichts(app_user):
    obj = _property(app_user)
    c = _contract(app_user, obj)
    out, _ = _run(app_user, dry_run=True)
    assert MaintenanceEvent.objects.filter(contract_id=c.id).count() == 0
    c.refresh_from_db()
    assert c.next_due_date == STICHTAG_D  # nicht vorgerückt
    assert "TROCKENLAUF" in out


@pytest.mark.django_db
def test_ein_fehler_bricht_lauf_nicht_ab(app_user, monkeypatch):
    obj = _property(app_user)
    kaputt = _contract(app_user, obj, name="Kaputt")
    heil = _contract(app_user, obj, name="Heil")

    original = wartung_service.trigger_action

    def flaky(actor_id, *, contract_id, **kwargs):
        if contract_id == kaputt.id:
            raise RuntimeError("simulierter Fehler")
        return original(actor_id, contract_id=contract_id, **kwargs)

    monkeypatch.setattr(wartung_service, "trigger_action", flaky)
    out, err = _run(app_user)

    # Der heile Vertrag wurde trotz Fehler beim anderen ausgelöst.
    assert MaintenanceEvent.objects.filter(contract_id=heil.id).count() == 1
    assert MaintenanceEvent.objects.filter(contract_id=kaputt.id).count() == 0
    assert "FEHLER" in err
    assert "1 Fehler" in out
