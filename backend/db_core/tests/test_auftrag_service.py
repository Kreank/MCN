"""Service-Tests der Auftrags-Schicht gegen die echte Test-DB.

Der Statusautomat (workflow.status_transition) und die Freigabe-/Abrechnungs-
Tore (deferred Constraint-Trigger) sind scharf. app_user aus conftest;
Liegenschaften/Parties über die bestehenden Services.
"""
import re

import pytest
from django.db import Error, connection

from db_core.db_context import business_transaction
from db_core.models import WorkOrder, WorkOrderParty
from db_core.services import auftrag as auftrag_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _force_deferred_checks():
    """Erzwingt die Prüfung DEFERRED Constraint-Trigger sofort.

    Die Auftrags-Tore sind DEFERRABLE INITIALLY DEFERRED und feuern erst beim
    echten COMMIT — den pytest je Test zurückrollt. SET CONSTRAINTS ALL IMMEDIATE
    wertet sie innerhalb der laufenden (Test-)Transaktion aus, sodass ihre Logik
    prüfbar wird.
    """
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _property(app_user, name="Objekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user, first="Erika", last="Auftraggeber"):
    return identity_service.create_person(app_user.id, first_name=first, last_name=last)


def _drive_to(app_user, order_id, *, target):
    """Schaltet einen Auftrag über die erlaubte Kette bis target."""
    chain = [
        "FREIGEGEBEN",
        "IN_PLANUNG",
        "IN_AUSFUEHRUNG",
        "TECHNISCH_ABGESCHLOSSEN",
        "KAUFMAENNISCH_GEPRUEFT",
        "ABGERECHNET",
    ]
    for to_status in chain:
        auftrag_service.advance_status(
            app_user.id, work_order_id=order_id, to_status=to_status
        )
        if to_status == target:
            break


@pytest.mark.django_db
def test_create_work_order_startet_entwurf(app_user):
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Dach abdichten"
    )
    assert order.title == "Dach abdichten"
    assert order.status == "ENTWURF"
    assert order.responsibility_scope == "UNKNOWN"
    assert order.version == 1
    assert re.match(r"^AU-[0-9]{4}-[0-9]{6,}$", order.order_number)


@pytest.mark.django_db
def test_create_work_order_leerer_titel(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="  ")


@pytest.mark.django_db
def test_create_work_order_ungueltige_prioritaet(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        auftrag_service.create_work_order(
            app_user.id, property_id=obj.id, title="X", priority="FALSCH"
        )


@pytest.mark.django_db
def test_add_party_ungueltige_rolle(app_user):
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="X"
    )
    p = _party(app_user)
    with pytest.raises(ValueError):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=p.id, role="CHEF"
        )


@pytest.mark.django_db
def test_confirm_responsibility_unknown_verboten(app_user):
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="X"
    )
    with pytest.raises(ValueError):
        auftrag_service.confirm_responsibility(
            app_user.id, work_order_id=order.id, scope="UNKNOWN"
        )


@pytest.mark.django_db
def test_ungueltiger_uebergang(app_user):
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="X"
    )
    with pytest.raises(ValueError):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status="ABGERECHNET"
        )


@pytest.mark.django_db
def test_storno_ohne_begruendung_verlangt_reason(app_user):
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="X"
    )
    with pytest.raises(ValueError):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status="STORNIERT"
        )
    # mit Begründung erlaubt
    auftrag_service.advance_status(
        app_user.id, work_order_id=order.id, to_status="STORNIERT",
        reason="Kunde hat abgesagt",
    )
    order.refresh_from_db()
    assert order.status == "STORNIERT"


@pytest.mark.django_db
def test_freigabe_ohne_tore_scheitert_am_trigger(app_user):
    """Freigabe ohne Nachweis/Verantwortung/Auftraggeber verletzt das DB-Tor."""
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="X"
    )
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            WorkOrder.objects.filter(id=order.id).update(status="FREIGEGEBEN")
            _force_deferred_checks()


@pytest.mark.django_db
def test_freigabe_mit_erfuellten_toren_besteht_trigger(app_user):
    """Sind Nachweis, Verantwortung und Auftraggeber gesetzt, passiert die
    Freigabe das DB-Tor (positive Gegenprobe)."""
    obj = _property(app_user)
    auftraggeber = _party(app_user, "Willi", "Wohnungseigentuemer")
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="X"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=auftraggeber.id,
        role="PRINCIPAL", is_primary=True,
    )
    with business_transaction(app_user.id):
        WorkOrder.objects.filter(id=order.id).update(status="FREIGEGEBEN")
        _force_deferred_checks()  # darf nicht werfen
    order.refresh_from_db()
    assert order.status == "FREIGEGEBEN"


@pytest.mark.django_db
def test_voller_durchlauf_bis_kaufmaennisch_geprueft(app_user):
    """Mit erfülltem Freigabe-Tor lässt sich der Auftrag bis
    KAUFMAENNISCH_GEPRUEFT und weiter bis ABGERECHNET durchschalten."""
    obj = _property(app_user)
    auftraggeber = _party(app_user, "Willi", "Wohnungseigentuemer")
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Vollständiger Auftrag"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="E-Mail vom 01.07."
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=auftraggeber.id,
        role="PRINCIPAL", is_primary=True,
    )

    _drive_to(app_user, order.id, target="KAUFMAENNISCH_GEPRUEFT")
    order.refresh_from_db()
    assert order.status == "KAUFMAENNISCH_GEPRUEFT"

    # Abrechnung verlangt zusätzlich einen Rechnungsschuldner (INVOICE_DEBTOR).
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=auftraggeber.id,
        role="INVOICE_DEBTOR", is_primary=True,
    )
    # Positivprobe des Abrechnungs-Tors: mit erzwungener Sofortprüfung, sonst
    # feuert der DEFERRED Trigger unter der Test-Transaktion nie.
    with business_transaction(app_user.id):
        WorkOrder.objects.filter(id=order.id).update(status="ABGERECHNET")
        _force_deferred_checks()  # darf nicht werfen
    order.refresh_from_db()
    assert order.status == "ABGERECHNET"


@pytest.mark.django_db
def test_tor_verletzung_wird_als_business_error_uebersetzt(app_user):
    """Ein Tor-Verstoß (DB RAISE EXCEPTION, P0001) wird von as_business_error zu
    ValueError → 422 statt eines rohen DB-Fehlers (500)."""
    from db_core.gate_errors import as_business_error

    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="X"
    )
    with pytest.raises(ValueError):
        with as_business_error():
            with business_transaction(app_user.id):
                WorkOrder.objects.filter(id=order.id).update(status="FREIGEGEBEN")
                _force_deferred_checks()


@pytest.mark.django_db
def test_abrechnung_ohne_schuldner_scheitert(app_user):
    """Ohne INVOICE_DEBTOR verletzt der Übergang nach ABGERECHNET das Tor."""
    obj = _property(app_user)
    auftraggeber = _party(app_user, "Willi", "Wohnungseigentuemer")
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Ohne Schuldner"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=auftraggeber.id,
        role="PRINCIPAL", is_primary=True,
    )
    _drive_to(app_user, order.id, target="KAUFMAENNISCH_GEPRUEFT")
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            WorkOrder.objects.filter(id=order.id).update(status="ABGERECHNET")
            _force_deferred_checks()
