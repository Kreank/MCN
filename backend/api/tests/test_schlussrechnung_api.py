"""API-Tests für Abschlags-/Teil-/Schlussrechnung (Migration 0060).

Deckt ab: Belegart beim Anlegen, die Liste der anrechenbaren Abschläge, das
Anrechnen beim Anlegen und über `PUT …/advances`, die Verkettung in beiden
Richtungen in der Detailausgabe, den Zahlbetrag als offenen Posten — und die
Rechte (LESEN/ANLEGEN/AENDERN).
"""
import json
from decimal import Decimal

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _auftrag(app_user, obj, debtor, *, bis="KAUFMAENNISCH_GEPRUEFT"):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=debtor.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
        if to == bis:
            break
    return order


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="SR-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Sieglinde", last_name="Schuldner"
    )
    order = _auftrag(app_user, obj, kunde)
    ar = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="ABSCHLAGSRECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "PAUSCHALE", "description": "1. Abschlag", "quantity": 1,
                "unit_price": "1000.00", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=ar.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=ar.id)
    ar.refresh_from_db()
    return {"app_user": app_user, "obj": obj, "kunde": kunde, "order": order, "ar": ar}


_LEISTUNG = [
    {"line_type": "PAUSCHALE", "description": "Gesamtleistung", "quantity": "1",
     "unit_price": "5000.00", "tax_code": "DE_19"},
]


def _post(client, body):
    return client.post(
        "/api/invoicing/invoices", data=json.dumps(body),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_anrechenbare_abschlaege_endpunkt(admin_client, seeded):
    r = admin_client.get(
        "/api/invoicing/invoices/anrechenbare-abschlaege"
        f"?work_order_id={seeded['order'].id}"
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert [a["id"] for a in body] == [str(seeded["ar"].id)]
    assert body[0]["invoice_type"] == "ABSCHLAGSRECHNUNG"
    assert body[0]["gross_total"] == "1190.00"
    assert body[0]["angerechnet"] is False


@pytest.mark.django_db
def test_schlussrechnung_anlegen_rechnet_an(admin_client, seeded):
    r = _post(admin_client, {
        "property_id": str(seeded["obj"].id),
        "invoice_type": "SCHLUSSRECHNUNG",
        "work_order_id": str(seeded["order"].id),
        "lines": _LEISTUNG,
        "advance_invoice_ids": [str(seeded["ar"].id)],
    })
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["gross_total"] == "4760.00"          # Zahlbetrag
    assert body["leistung_brutto"] == "5950.00"      # volle Leistung
    assert len(body["advances"]) == 1
    assert body["advances"][0]["invoice_number"] == seeded["ar"].invoice_number
    assert body["advances"][0]["gross_amount"] == "1190.00"
    # Die Anrechnungsposition ist als solche erkennbar (read-only im Editor).
    abzug = [l for l in body["lines"] if l["advance_invoice_id"]]
    assert len(abzug) == 1 and abzug[0]["net_amount"] == "-1000.00"


@pytest.mark.django_db
def test_abschlag_zeigt_die_anrechnende_schlussrechnung(admin_client, seeded):
    """Verkettung in der Gegenrichtung (Rechnungsmappe des Abschlags)."""
    r = _post(admin_client, {
        "property_id": str(seeded["obj"].id),
        "invoice_type": "SCHLUSSRECHNUNG",
        "work_order_id": str(seeded["order"].id),
        "lines": _LEISTUNG,
        "advance_invoice_ids": [str(seeded["ar"].id)],
    })
    sr_id = r.json()["id"]
    detail = admin_client.get(f"/api/invoicing/invoices/{seeded['ar'].id}").json()
    assert detail["angerechnet_in"]["id"] == sr_id
    assert detail["angerechnet_in"]["status"] == "ENTWURF"


@pytest.mark.django_db
def test_advances_setzen_und_leeren(admin_client, seeded):
    r = _post(admin_client, {
        "property_id": str(seeded["obj"].id),
        "invoice_type": "SCHLUSSRECHNUNG",
        "work_order_id": str(seeded["order"].id),
        "lines": _LEISTUNG,
    })
    sr_id = r.json()["id"]
    assert r.json()["gross_total"] == "5950.00"

    r = admin_client.put(
        f"/api/invoicing/invoices/{sr_id}/advances",
        data=json.dumps({"advance_invoice_ids": [str(seeded["ar"].id)]}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["gross_total"] == "4760.00"

    r = admin_client.put(
        f"/api/invoicing/invoices/{sr_id}/advances",
        data=json.dumps({"advance_invoice_ids": []}),
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["gross_total"] == "5950.00"
    assert r.json()["advances"] == []


@pytest.mark.django_db
def test_anrechnung_auf_normaler_rechnung_ist_422(admin_client, seeded):
    r = _post(admin_client, {
        "property_id": str(seeded["obj"].id),
        "invoice_type": "RECHNUNG",
        "work_order_id": str(seeded["order"].id),
        "lines": _LEISTUNG,
        "advance_invoice_ids": [str(seeded["ar"].id)],
    })
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_offener_posten_ist_der_zahlbetrag(admin_client, seeded):
    """Die Schlussrechnung steht mit der DIFFERENZ im Mahnwesen — nicht mit der
    Gesamtleistung (sonst wäre der Abschlag doppelt gefordert)."""
    app_user = seeded["app_user"]
    sr = beleg_service.create_invoice(
        app_user.id, property_id=seeded["obj"].id, invoice_type="SCHLUSSRECHNUNG",
        work_order_id=seeded["order"].id,
        lines=[{"line_type": "PAUSCHALE", "description": "Gesamtleistung",
                "quantity": 1, "unit_price": "5000.00", "tax_code": "DE_19"}],
        advance_invoice_ids=[seeded["ar"].id],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=sr.id, party_id=seeded["kunde"].id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)

    r = admin_client.get(f"/api/buchhaltung/invoices/{sr.id}")
    assert r.status_code == 200, r.content
    assert Decimal(r.json()["open_amount"]) == Decimal("4760.00")


# --- Rechte ----------------------------------------------------------------

@pytest.mark.django_db
def test_advances_ohne_recht_ist_403(client_with_role, seeded):
    leser = client_with_role("NUR_LESEN")
    r = leser.put(
        f"/api/invoicing/invoices/{seeded['ar'].id}/advances",
        data=json.dumps({"advance_invoice_ids": []}),
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_anrechenbare_abschlaege_ohne_recht_ist_403(client_with_role, seeded):
    ohne = client_with_role("MONTEUR")
    r = ohne.get(
        "/api/invoicing/invoices/anrechenbare-abschlaege"
        f"?work_order_id={seeded['order'].id}"
    )
    assert r.status_code == 403
