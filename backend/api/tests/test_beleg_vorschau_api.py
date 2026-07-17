"""API-Tests der Live-Vorschau (POST /quotes|invoices/{id}/vorschau).

Der Endpunkt ist leseartig: `invoicing/LESEN` genügt, die Kalkulation zusätzlich
nur mit `pricing/LESEN`. Fehlt letzteres, kommt die Vorschau trotzdem (200) —
nur mit `kalkulation: null`, KEIN 403 für den Gesamtendpunkt. Dezimalwerte sind
verlustfreie Strings wie überall in der API.
"""
import uuid

import pytest

from db_core.db_context import business_transaction
from db_core.models import RolePermission
from db_core.services import property as property_service

from .conftest import logged_in_client

BASE = "/api/invoicing"


@pytest.fixture
def obj(app_user):
    return property_service.create_property(
        app_user.id, name="Vorschau-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )


def _mat(desc, qty, price, **extra):
    return {
        "line_type": "MATERIAL", "description": desc, "quantity": str(qty),
        "unit": "Stk", "unit_price": str(price), "tax_code": "DE_19", **extra,
    }


def _leeres_angebot(client, obj):
    return client.post(
        f"{BASE}/quotes",
        data={"property_id": str(obj.id), "title": "Leer"},
        content_type="application/json",
    ).json()["id"]


@pytest.mark.django_db
def test_vorschau_dezimal_als_string_und_reihenfolge(admin_client, obj):
    quote_id = _leeres_angebot(admin_client, obj)
    r = admin_client.post(
        f"{BASE}/quotes/{quote_id}/vorschau",
        data={"lines": [
            _mat("Rinne", 2, 50, unit_cost="30.00"),
            {"line_type": "TEXT", "description": "Hinweis"},
        ]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    # Verlustfreie Strings.
    assert body["net_total"] == "100.00"
    assert body["gross_total"] == "119.00"
    # Payload-Reihenfolge; Textzeile null.
    assert body["lines"][0]["net_amount"] == "100.00"
    assert body["lines"][0]["markup_percent"] == "66.667"
    assert body["lines"][1]["net_amount"] is None
    # Mit pricing-Recht (Admin) ist die Kalkulation da.
    assert body["kalkulation"]["gesamt"]["ek"] == "60.00"


@pytest.mark.django_db
def test_vorschau_ungueltige_position_422(admin_client, obj):
    quote_id = _leeres_angebot(admin_client, obj)
    r = admin_client.post(
        f"{BASE}/quotes/{quote_id}/vorschau",
        data={"lines": [{"line_type": "MATERIAL", "description": "Ohne Steuer",
                         "quantity": "1", "unit_price": "10"}]},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_vorschau_unbekanntes_angebot_404(admin_client, db):
    r = admin_client.post(
        f"{BASE}/quotes/{uuid.uuid4()}/vorschau",
        data={"lines": []}, content_type="application/json",
    )
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_vorschau_ohne_login_401(anonymous_client, obj, admin_client):
    quote_id = _leeres_angebot(admin_client, obj)
    r = anonymous_client.post(
        f"{BASE}/quotes/{quote_id}/vorschau",
        data={"lines": []}, content_type="application/json",
    )
    assert r.status_code == 401


@pytest.mark.django_db
def test_vorschau_kalkulation_null_ohne_pricing_recht(admin_client, obj, app_user):
    """Ohne pricing/LESEN kommt die Vorschau (200) — nur ohne Kalkulation.

    Wie im Kalkulations-Endpunkt hält in der Startmatrix jede Rolle mit
    invoicing/LESEN auch pricing/LESEN; NUR_LESEN wird das pricing-Recht gezielt
    entzogen, damit der Test etwas beweist. Anders als GET /…/kalkulation (403)
    liefert die Vorschau die (preisfreien) Summen weiter.
    """
    quote_id = _leeres_angebot(admin_client, obj)
    with business_transaction(app_user.id):
        RolePermission.objects.filter(
            role_id="NUR_LESEN", module="pricing", action="LESEN"
        ).update(allowed=False)

    leser = logged_in_client("NUR_LESEN")
    r = leser.post(
        f"{BASE}/quotes/{quote_id}/vorschau",
        data={"lines": [_mat("Ware", 1, 100, unit_cost="60.00")]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["kalkulation"] is None
    # Die Summen bleiben sichtbar (preisfrei).
    assert body["net_total"] == "100.00"


@pytest.mark.django_db
def test_expliziter_markup_kommt_im_get_zurueck(admin_client, obj):
    """Ein ausdrücklich gesetzter Aufschlag wird gespeichert und NICHT vom
    abgeleiteten überschrieben (abgeleitet wäre 150,000 statt 25,000)."""
    r = admin_client.post(
        f"{BASE}/quotes",
        data={"property_id": str(obj.id), "title": "Aufschlag",
              "lines": [_mat("A", 1, 100, unit_cost="40.00", markup_percent="25.000")]},
        content_type="application/json",
    )
    quote_id = r.json()["id"]
    body = admin_client.get(f"{BASE}/quotes/{quote_id}").json()
    assert body["lines"][0]["markup_percent"] == "25.000"
    assert body["lines"][0]["unit_cost"] == "40.00"
