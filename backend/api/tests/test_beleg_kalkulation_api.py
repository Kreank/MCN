"""API-Tests: Abschnitte (Rubriken), Alternativpositionen und die interne
Kalkulationsübersicht.

Der Kalkulations-Endpunkt legt Einkaufspreise und Margen offen. Wer ein Angebot
lesen darf (`invoicing/LESEN`), darf deshalb NICHT automatisch die Marge sehen —
das Tor ist `pricing/LESEN` (dasselbe Recht, das den Artikelstamm samt EK gatet).
Die Rolle DISPOSITION hat invoicing/LESEN, aber kein pricing/LESEN, und ist damit
der scharfe Negativfall.
"""
import pytest

from db_core.db_context import business_transaction
from db_core.models import RolePermission
from db_core.services import property as property_service

from .conftest import logged_in_client

BASE = "/api/invoicing"


@pytest.fixture
def obj(app_user):
    return property_service.create_property(
        app_user.id, name="Kalk-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )


def _mat(desc, qty, price, **extra):
    return {
        "line_type": "MATERIAL", "description": desc, "quantity": str(qty),
        "unit": "Stk", "unit_price": str(price), "tax_code": "DE_19", **extra,
    }


def _angebot_anlegen(client, obj):
    return client.post(
        f"{BASE}/quotes",
        data={
            "property_id": str(obj.id),
            "title": "Angebot mit Abschnitten",
            "rubriken": [
                {"title": "Dach", "description": "Arbeiten am Dach"},
                {"title": "Fassade"},
            ],
            "lines": [
                _mat("Rinne", 2, 50, rubrik=1, unit_cost="30.00"),
                _mat("Kupfer", 1, 500, rubrik=1, line_kind="ALTERNATIV"),
                _mat("Putz", 4, 10, rubrik=2, unit_cost="7.50"),
            ],
        },
        content_type="application/json",
    )


@pytest.mark.django_db
def test_angebot_mit_abschnitten_anlegen(admin_client, obj):
    r = _angebot_anlegen(admin_client, obj)
    assert r.status_code == 201, r.content
    body = r.json()

    assert [ru["title"] for ru in body["rubriken"]] == ["Dach", "Fassade"]
    assert body["rubriken"][0]["description"] == "Arbeiten am Dach"

    zeilen = body["lines"]
    assert zeilen[0]["rubrik"] == 1 and zeilen[0]["line_kind"] == "NORMAL"
    assert zeilen[1]["line_kind"] == "ALTERNATIV"
    assert zeilen[2]["rubrik"] == 2

    # Die Alternative (500,00) zählt nicht: 100,00 + 40,00 netto.
    assert body["net_total"] == "140.00"
    # Der EK-Snapshot ist gespeichert und der Aufschlag daraus abgeleitet.
    assert zeilen[0]["unit_cost"] == "30.00"
    assert zeilen[0]["markup_percent"] == "66.667"   # (50-30)/30


@pytest.mark.django_db
def test_unbekannter_abschnitt_422(admin_client, obj):
    r = admin_client.post(
        f"{BASE}/quotes",
        data={
            "property_id": str(obj.id), "title": "X",
            "rubriken": [{"title": "Nur einer"}],
            "lines": [_mat("A", 1, 10, rubrik=2)],
        },
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Abschnitt 2 existiert nicht" in r.json()["detail"]


@pytest.mark.django_db
def test_kalkulation_je_abschnitt(admin_client, obj):
    quote_id = _angebot_anlegen(admin_client, obj).json()["id"]
    r = admin_client.get(f"{BASE}/quotes/{quote_id}/kalkulation")
    assert r.status_code == 200, r.content
    body = r.json()

    dach, fassade = body["abschnitte"]
    assert dach["title"] == "Dach"
    assert dach["netto"] == "100.00"
    assert dach["ek"] == "60.00"
    assert dach["deckungsbeitrag"] == "40.00"
    assert dach["marge_prozent"] == "40.00"
    assert dach["alternativ_netto"] == "500.00"
    assert dach["ek_vollstaendig"] is True

    assert fassade["marge_prozent"] == "25.00"
    assert body["gesamt"]["netto"] == "140.00"


@pytest.mark.django_db
def test_kalkulation_ohne_ek_meldet_luecke_statt_null(admin_client, obj):
    r = admin_client.post(
        f"{BASE}/quotes",
        data={
            "property_id": str(obj.id), "title": "Ohne EK",
            "lines": [_mat("Ware", 1, 100)],
        },
        content_type="application/json",
    )
    quote_id = r.json()["id"]
    body = admin_client.get(f"{BASE}/quotes/{quote_id}/kalkulation").json()
    gruppe = body["abschnitte"][0]
    assert gruppe["rubrik"] is None
    assert gruppe["ek_vollstaendig"] is False
    assert gruppe["deckungsbeitrag"] is None
    assert gruppe["marge_prozent"] is None
    assert gruppe["positionen_ohne_ek"] == 1


@pytest.mark.django_db
def test_kalkulation_ohne_pricing_recht_403(admin_client, obj, app_user):
    """Wer den Beleg lesen darf, darf nicht zwangsläufig die Marge sehen.

    In der Startmatrix hält heute jede Rolle mit `invoicing/LESEN` auch
    `pricing/LESEN` — die beiden Tore sind also faktisch deckungsgleich. Damit der
    Test trotzdem etwas beweist, wird NUR_LESEN das pricing-Recht gezielt
    entzogen: das Angebot bleibt lesbar, die Kalkulation nicht.
    """
    quote_id = _angebot_anlegen(admin_client, obj).json()["id"]
    with business_transaction(app_user.id):
        RolePermission.objects.filter(
            role_id="NUR_LESEN", module="pricing", action="LESEN"
        ).update(allowed=False)

    leser = logged_in_client("NUR_LESEN")
    # Das Angebot selbst darf sie sehen …
    assert leser.get(f"{BASE}/quotes/{quote_id}").status_code == 200
    # … die Kalkulation nicht, denn sie legt Einkaufspreise offen.
    assert leser.get(f"{BASE}/quotes/{quote_id}/kalkulation").status_code == 403


@pytest.mark.django_db
def test_kalkulation_ohne_login_401(anonymous_client, obj, admin_client):
    quote_id = _angebot_anlegen(admin_client, obj).json()["id"]
    r = anonymous_client.get(f"{BASE}/quotes/{quote_id}/kalkulation")
    assert r.status_code == 401


@pytest.mark.django_db
def test_kalkulation_unbekanntes_angebot_404(admin_client, db):
    import uuid
    r = admin_client.get(f"{BASE}/quotes/{uuid.uuid4()}/kalkulation")
    assert r.status_code == 404
