"""Service-Tests der Einsatz-Schicht (workflow.service_job) gegen die echte
Test-DB.

Der Statusautomat (workflow.status_transition, entity='service_job') und die
Trigger-Tore (Auftragsstatus bei INSERT, Ausführungs-Gate ab UNTERWEGS,
Korrekturfenster B-28 bei Zeit-/Material) sind scharf. app_user aus conftest.

Zum Setup wird ein Auftrag über die Auftrags-Services bis IN_AUSFUEHRUNG
geschaltet; die Freigabe-Tore des Auftrags sind DEFERRED und feuern unter der
Test-Transaktion nicht — die Statusspalte trägt aber sofort den Zielstatus, den
das (sofort feuernde) Einsatz-Ausführungs-Gate liest.
"""
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)


def _property(app_user, name="Objekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user, first="Erika", last="Auftraggeber"):
    return identity_service.create_person(app_user.id, first_name=first, last_name=last)


def _order(app_user, *, target="IN_AUSFUEHRUNG", title="Auftrag"):
    """Legt einen vollständig getorten Auftrag an und schaltet ihn bis target."""
    obj = _property(app_user)
    principal = _party(app_user)
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
    chain = ["FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
             "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"]
    for to_status in chain:
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status=to_status
        )
        if to_status == target:
            break
    return order


def _job_at(app_user, order, *, target):
    """Legt einen Einsatz an und schaltet ihn über die erlaubte Kette bis target."""
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=T0, scheduled_end=T1,
    )
    chain = ["GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT", "ABGESCHLOSSEN"]
    for to_status in chain:
        einsatz_service.advance_status(
            app_user.id, service_job_id=job.id, to_status=to_status
        )
        if to_status == target:
            break
    job.refresh_from_db()
    return job


# --- Anlage & Nummern ------------------------------------------------------

@pytest.mark.django_db
def test_create_service_job_startet_ungeplant(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id
    )
    assert job.status == "UNGEPLANT"
    assert job.job_number.startswith("E-")


@pytest.mark.django_db
def test_create_auf_abgerechnetem_auftrag_scheitert(app_user):
    """B-03/B-06: kein Einsatz auf abgerechnete/stornierte Aufträge."""
    order = _order(app_user, target="KAUFMAENNISCH_GEPRUEFT")
    # weiter bis ABGERECHNET braucht INVOICE_DEBTOR; der Übergang selbst reicht
    # nicht — aber STORNIERT ist einfacher zu erreichen:
    auftrag_service.advance_status(
        app_user.id, work_order_id=order.id, to_status="STORNIERT",
        reason="Testabbruch",
    )
    with pytest.raises(ValueError):
        einsatz_service.create_service_job(app_user.id, work_order_id=order.id)


# --- Terminierung ----------------------------------------------------------

@pytest.mark.django_db
def test_set_schedule_setzt_zeiten(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    einsatz_service.set_schedule(
        app_user.id, service_job_id=job.id, scheduled_start=T0, scheduled_end=T1
    )
    job.refresh_from_db()
    assert job.scheduled_start == T0
    assert job.scheduled_end == T1


@pytest.mark.django_db
def test_set_schedule_ende_vor_start_verboten(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    with pytest.raises(ValueError):
        einsatz_service.set_schedule(
            app_user.id, service_job_id=job.id, scheduled_start=T1, scheduled_end=T0
        )


# --- Statusautomat ---------------------------------------------------------

@pytest.mark.django_db
def test_ungueltiger_uebergang(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    with pytest.raises(ValueError):
        einsatz_service.advance_status(
            app_user.id, service_job_id=job.id, to_status="ABGESCHLOSSEN"
        )


@pytest.mark.django_db
def test_ausgefallen_verlangt_begruendung(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="GEPLANT"
    )
    with pytest.raises(ValueError):
        einsatz_service.advance_status(
            app_user.id, service_job_id=job.id, to_status="AUSGEFALLEN"
        )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="AUSGEFALLEN",
        reason="Kunde nicht angetroffen",
    )
    job.refresh_from_db()
    assert job.status == "AUSGEFALLEN"


@pytest.mark.django_db
def test_ausfuehrung_ohne_freigegebenen_auftrag_scheitert(app_user):
    """Ausführungs-Gate: UNTERWEGS setzt einen freigegebenen Auftrag voraus.
    Auf einem Auftrag im ENTWURF scheitert der Übergang (B-01/A-23) → ValueError."""
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Noch im Entwurf"
    )
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="GEPLANT"
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="BESTAETIGT"
    )
    with pytest.raises(ValueError):
        einsatz_service.advance_status(
            app_user.id, service_job_id=job.id, to_status="UNTERWEGS"
        )


@pytest.mark.django_db
def test_voller_durchlauf_bis_abgeschlossen(app_user):
    order = _order(app_user)
    job = _job_at(app_user, order, target="ABGESCHLOSSEN")
    assert job.status == "ABGESCHLOSSEN"


# --- Zuweisung -------------------------------------------------------------

@pytest.mark.django_db
def test_assign_user(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    a = einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=app_user.id, role="LEAD"
    )
    assert a.role == "LEAD"
    assert a.assignee_id == app_user.id


@pytest.mark.django_db
def test_assign_user_ungueltige_rolle(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    with pytest.raises(ValueError):
        einsatz_service.assign_user(
            app_user.id, service_job_id=job.id, assignee_user_id=app_user.id, role="CHEF"
        )


# --- Zeiten & Material -----------------------------------------------------

@pytest.mark.django_db
def test_log_time(app_user):
    order = _order(app_user)
    job = _job_at(app_user, order, target="VOR_ORT")
    entry = einsatz_service.log_time(
        app_user.id, service_job_id=job.id, user_id=app_user.id,
        time_type="ARBEITSZEIT", started_at=T0, ended_at=T1,
    )
    assert entry.time_type == "ARBEITSZEIT"


@pytest.mark.django_db
def test_log_time_ende_vor_start(app_user):
    order = _order(app_user)
    job = _job_at(app_user, order, target="VOR_ORT")
    with pytest.raises(ValueError):
        einsatz_service.log_time(
            app_user.id, service_job_id=job.id, user_id=app_user.id,
            time_type="ARBEITSZEIT", started_at=T1, ended_at=T0,
        )


@pytest.mark.django_db
def test_log_time_ungueltige_art(app_user):
    order = _order(app_user)
    job = _job_at(app_user, order, target="VOR_ORT")
    with pytest.raises(ValueError):
        einsatz_service.log_time(
            app_user.id, service_job_id=job.id, user_id=app_user.id,
            time_type="KAFFEEPAUSE", started_at=T0, ended_at=T1,
        )


@pytest.mark.django_db
def test_log_material(app_user):
    order = _order(app_user)
    job = _job_at(app_user, order, target="VOR_ORT")
    entry = einsatz_service.log_material(
        app_user.id, service_job_id=job.id,
        description="Injektionsharz", quantity=Decimal("3.5"), unit="kg",
        recorded_by=app_user.id,
    )
    assert entry.quantity == Decimal("3.5")


@pytest.mark.django_db
def test_log_material_menge_null(app_user):
    order = _order(app_user)
    job = _job_at(app_user, order, target="VOR_ORT")
    with pytest.raises(ValueError):
        einsatz_service.log_material(
            app_user.id, service_job_id=job.id,
            description="Nichts", quantity=Decimal("0"), unit="kg",
            recorded_by=app_user.id,
        )


# --- Kundenhistorie (Auftraggeber + Anzahl Aufträge/Termine) ---------------

def _order_for(app_user, obj, principal, title):
    """Getorten Auftrag für einen gegebenen Auftraggeber anlegen (bis IN_AUSFUEHRUNG)."""
    o = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title=title)
    auftrag_service.set_order_evidence(app_user.id, work_order_id=o.id, reference="N")
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=o.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=o.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for s in ["FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"]:
        auftrag_service.advance_status(app_user.id, work_order_id=o.id, to_status=s)
    return o


@pytest.mark.django_db
def test_kundenhistorie_zaehlt_ueber_alle_auftraege(app_user):
    obj = _property(app_user)
    principal = _party(app_user, first="Erika", last="Kundenschmidt")
    o1 = _order_for(app_user, obj, principal, "A1")
    einsatz_service.create_service_job(app_user.id, work_order_id=o1.id)
    einsatz_service.create_service_job(app_user.id, work_order_id=o1.id)
    o2 = _order_for(app_user, obj, principal, "A2")
    einsatz_service.create_service_job(app_user.id, work_order_id=o2.id)
    # Anderer Kunde — darf NICHT mitzählen.
    other = _party(app_user, first="Max", last="Anders")
    o3 = _order_for(app_user, obj, other, "A3")
    einsatz_service.create_service_job(app_user.id, work_order_id=o3.id)

    h = auftrag_service.kundenhistorie(o1.id)
    assert h["customer_name"] == "Erika Kundenschmidt"
    assert h["auftraege_gesamt"] == 2      # o1, o2
    assert h["termine_gesamt"] == 3        # 2 + 1


@pytest.mark.django_db
def test_kundenhistorie_ohne_auftraggeber(app_user):
    obj = _property(app_user)
    o = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="X")
    h = auftrag_service.kundenhistorie(o.id)
    assert h["customer_party_id"] is None
    assert h["auftraege_gesamt"] == 0
    assert h["termine_gesamt"] == 0
