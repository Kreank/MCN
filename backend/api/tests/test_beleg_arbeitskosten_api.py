"""API-Tests des § 35a-Arbeitskostenausweises (Migration 0076).

Der Ausweis kommt IMMER vom Server (`beleg.arbeitskosten`) — das Frontend rechnet
ihn nicht nach. Es sieht `bestimmbar` + `offen`, damit es den Bediener vor dem
Veröffentlichen zu den Positionen führen kann, deren Anteil noch fehlt.
"""
import pytest

from db_core.services import beleg as beleg_service
from db_core.services import property as property_service


@pytest.fixture
def objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Privathaushalt", property_type="EINFAMILIENHAUS",
        street="Eigenheimweg", house_number="4", postal_code="10115", city="Berlin",
    )


def _anlegen(client, objekt, lines, **kwargs):
    return client.post(
        "/api/invoicing/invoices",
        {"property_id": str(objekt.id), "lines": lines, **kwargs},
        content_type="application/json",
    )


@pytest.mark.django_db
def test_detail_liefert_den_ausweis(admin_client, objekt):
    r = _anlegen(admin_client, objekt, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": "10",
         "unit_price": "60.00", "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Rohr", "quantity": "1",
         "unit_price": "400.00", "tax_code": "DE_19"},
    ])
    assert r.status_code == 201, r.content
    body = r.json()

    assert body["show_labour_costs"] is True
    ausweis = body["arbeitskosten"]
    assert ausweis["bestimmbar"] is True
    assert ausweis["offen"] == []
    assert ausweis["net_amount"] == "600.00"
    assert ausweis["gross_amount"] == "714.00"
    # Der abgeleitete Anteil steht auch an der Position (der Editor zeigt ihn an).
    assert body["lines"][0]["labour_net_amount"] == "600.00"
    assert body["lines"][1]["labour_net_amount"] == "0.00"


@pytest.mark.django_db
def test_unbestimmte_position_wird_benannt(admin_client, objekt):
    """Das UI soll sagen können, WO der Anteil fehlt — nicht nur „geht nicht"."""
    r = _anlegen(admin_client, objekt, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": "10",
         "unit_price": "60.00", "tax_code": "DE_19"},
        {"line_type": "PAUSCHALE", "description": "Bad komplett", "quantity": "1",
         "unit_price": "2000.00", "tax_code": "DE_19"},
    ])
    assert r.status_code == 201
    ausweis = r.json()["arbeitskosten"]
    assert ausweis["bestimmbar"] is False
    assert ausweis["offen"] == [2]
    assert ausweis["net_amount"] is None
    assert r.json()["lines"][1]["labour_net_amount"] is None


@pytest.mark.django_db
def test_anteil_laesst_sich_setzen_und_der_ausweis_wird_bestimmbar(admin_client, objekt):
    r = _anlegen(admin_client, objekt, [
        {"line_type": "PAUSCHALE", "description": "Bad komplett", "quantity": "1",
         "unit_price": "2000.00", "tax_code": "DE_19", "labour_net_amount": "1200.00"},
    ])
    assert r.status_code == 201
    ausweis = r.json()["arbeitskosten"]
    assert ausweis["bestimmbar"] is True
    assert ausweis["net_amount"] == "1200.00"


@pytest.mark.django_db
def test_zu_hoher_anteil_ist_422_kein_500(admin_client, objekt):
    r = _anlegen(admin_client, objekt, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": "1",
         "unit_price": "60.00", "tax_code": "DE_19", "labour_net_amount": "61.00"},
    ])
    assert r.status_code == 422
    assert "Teil des Positionsbetrags" in r.json()["detail"]


@pytest.mark.django_db
def test_ausweis_laesst_sich_je_beleg_abschalten(admin_client, objekt):
    """B2B: der Block ist dort sachlich richtig, aber nutzlos."""
    inv_id = _anlegen(admin_client, objekt, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": "10",
         "unit_price": "60.00", "tax_code": "DE_19"},
    ]).json()["id"]

    r = admin_client.put(
        f"/api/invoicing/invoices/{inv_id}",
        {"show_labour_costs": False},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["show_labour_costs"] is False
    # Der Ausweis selbst bleibt berechenbar — nur aufs Papier kommt er nicht.
    assert r.json()["arbeitskosten"]["bestimmbar"] is True


@pytest.mark.django_db
def test_flag_bleibt_beim_positionswechsel_erhalten(admin_client, objekt):
    """Der Editor schickt beim Speichern nur die Positionen — das Kopf-Flag darf
    dabei nicht stillschweigend auf den Default zurückfallen."""
    inv_id = _anlegen(admin_client, objekt, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": "1",
         "unit_price": "60.00", "tax_code": "DE_19"},
    ], show_labour_costs=False).json()["id"]

    r = admin_client.put(
        f"/api/invoicing/invoices/{inv_id}",
        {"lines": [
            {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": "2",
             "unit_price": "60.00", "tax_code": "DE_19"},
        ]},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["show_labour_costs"] is False


@pytest.mark.django_db
def test_angebot_traegt_den_anteil_ebenfalls(admin_client, objekt):
    """Angebot und Rechnung teilen die Positionslogik — der Anteil ist auch dort da
    (der Ausweis selbst hängt an der Rechnung, § 35a Abs. 5 EStG)."""
    r = admin_client.post(
        "/api/invoicing/quotes",
        {"property_id": str(objekt.id), "title": "Angebot",
         "lines": [
             {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": "10",
              "unit_price": "60.00", "tax_code": "DE_19"},
         ]},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["lines"][0]["labour_net_amount"] == "600.00"


@pytest.mark.django_db
def test_null_schaltet_den_ausweis_nicht_ab(admin_client, objekt):
    """`show_labour_costs: null` heißt „nichts gesagt", nicht „abschalten" — sonst
    nähme ein Client, der das Feld leer mitschickt, dem Kunden still den Ausweis."""
    inv_id = _anlegen(admin_client, objekt, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": "1",
         "unit_price": "60.00", "tax_code": "DE_19"},
    ]).json()["id"]

    r = admin_client.put(
        f"/api/invoicing/invoices/{inv_id}",
        {"show_labour_costs": None, "invoice_date": "2026-07-13"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["show_labour_costs"] is True
