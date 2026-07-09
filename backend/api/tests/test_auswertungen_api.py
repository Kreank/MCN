"""API-Tests der Auswertungen-Endpoints (lesend, ohne Auth)."""
import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service


def _publish_invoice(app_user, obj, party, *, unit_price, quantity):
    order = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="A")
    auftrag_service.set_order_evidence(app_user.id, work_order_id=order.id, reference="N")
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=party.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, work_order_id=order.id,
        lines=[{"line_type": "MATERIAL", "description": "X", "quantity": quantity,
                "unit_price": unit_price, "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=party.id, role=role, is_primary=True
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)


@pytest.mark.django_db
def test_dashboards_liste(admin_client, db):
    r = admin_client.get("/api/auswertungen/dashboards")
    assert r.status_code == 200
    body = r.json()
    umsatz = next(d for d in body if d["key"] == "umsatz-projektuebersicht")
    assert umsatz["available"] is True


@pytest.mark.django_db
def test_umsatz_projektuebersicht(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    projekt_service.create_project(app_user.id, name="P1")
    weg = identity_service.create_person(app_user.id, first_name="W", last_name="EG")
    _publish_invoice(app_user, obj, weg, unit_price="100.00", quantity=2)

    r = admin_client.get("/api/auswertungen/umsatz-projektuebersicht")
    assert r.status_code == 200
    body = r.json()
    assert body["revenue"]["net_total"] == "200.00"
    assert body["revenue"]["invoice_count"] == 1
    assert body["projects"]["total"] == 1
    assert body["projects"]["open"] == 1
    assert len(body["timeline"]) == 1


@pytest.mark.django_db
def test_umsatz_projektuebersicht_leer(admin_client, db):
    r = admin_client.get("/api/auswertungen/umsatz-projektuebersicht")
    assert r.status_code == 200
    body = r.json()
    assert body["revenue"]["net_total"] == "0.00"
    assert body["revenue"]["invoice_count"] == 0
    assert body["timeline"] == []


@pytest.mark.django_db
def test_kunden_dashboard_verfuegbar(admin_client, db):
    body = admin_client.get("/api/auswertungen/dashboards").json()
    kunden = next(d for d in body if d["key"] == "kunden")
    assert kunden["available"] is True


@pytest.mark.django_db
def test_kunden_endpoint(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    anna = identity_service.create_person(app_user.id, first_name="Anna", last_name="A")
    _publish_invoice(app_user, obj, anna, unit_price="100.00", quantity=2)  # net 200

    r = admin_client.get("/api/auswertungen/kunden")
    assert r.status_code == 200
    body = r.json()
    assert body["customer_count"] == 1
    assert body["net_total"] == "200.00"
    assert body["customers"][0]["display_name"] == "Anna A"
    assert body["customers"][0]["net_total"] == "200.00"
