"""API-Tests für Zahlungsbedingungen/Skonto an der Rechnung (Migration 0058).

Deckt ab: Anlegen und Ändern über die API, die abgeleiteten Skonto-Werte in der
Detailausgabe, die 422-Fälle (Wertebereich/Paarigkeit/Frist) und die
Rechteprüfung (ANLEGEN/AENDERN). Dazu die read-only Skonto-Info im
Offene-Posten-Detail der Buchhaltung.
"""
import json
from datetime import date

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _gepruefter_auftrag(app_user, obj, debtor):
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
    return order


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Skonto-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    weg = identity_service.create_person(
        app_user.id, first_name="Wanda", last_name="WEG"
    )
    order = _gepruefter_auftrag(app_user, obj, weg)
    return {"app_user": app_user, "obj": obj, "party": weg, "order": order}


_LINES = [
    {"line_type": "MATERIAL", "description": "Ziegel", "quantity": "100",
     "unit": "Stk", "unit_price": "10.00", "tax_code": "DE_19"},
]


def _payload(seeded, **extra):
    return {
        "property_id": str(seeded["obj"].id),
        "invoice_type": "RECHNUNG",
        "work_order_id": str(seeded["order"].id),
        "invoice_date": "2026-07-01",
        "lines": _LINES,
        **extra,
    }


def _post(client, body):
    return client.post(
        "/api/invoicing/invoices", data=json.dumps(body),
        content_type="application/json",
    )


def _put(client, invoice_id, body):
    return client.put(
        f"/api/invoicing/invoices/{invoice_id}", data=json.dumps(body),
        content_type="application/json",
    )


# --- Anlegen ---------------------------------------------------------------

@pytest.mark.django_db
def test_create_mit_skonto_liefert_abgeleitete_werte(admin_client, seeded):
    r = _post(admin_client, _payload(
        seeded, payment_term_days=30, discount_percent="2.5", discount_days=10
    ))
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["payment_term_days"] == 30
    assert body["discount_percent"] == "2.50"
    assert body["discount_days"] == 10
    # 1.190,00 brutto → 2,5 % = 29,75 EUR; zahlbar 1.160,25 EUR.
    assert body["gross_total"] == "1190.00"
    assert body["skonto_bis"] == "2026-07-11"
    assert body["skonto_betrag"] == "29.75"
    assert body["skonto_zahlbetrag"] == "1160.25"


@pytest.mark.django_db
def test_create_ohne_skonto_hat_keine_abgeleiteten_werte(admin_client, seeded):
    r = _post(admin_client, _payload(seeded, payment_term_days=14))
    assert r.status_code == 201
    body = r.json()
    assert body["payment_term_days"] == 14
    assert body["discount_percent"] is None
    assert body["skonto_bis"] is None
    assert body["skonto_betrag"] is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "extra",
    [
        {"payment_term_days": 400},
        {"payment_term_days": -1},
        {"discount_percent": "0", "discount_days": 5},
        {"discount_percent": "100", "discount_days": 5},
        {"discount_percent": "2"},                      # Frist fehlt
        {"discount_days": 10},                          # Satz fehlt
        {"payment_term_days": 7, "discount_percent": "2", "discount_days": 14},
    ],
)
def test_create_ungueltige_bedingungen_ergeben_422(admin_client, seeded, extra):
    r = _post(admin_client, _payload(seeded, **extra))
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_create_ohne_recht_ist_403(client_with_role, seeded):
    c = client_with_role("NUR_LESEN")
    r = _post(c, _payload(seeded, payment_term_days=30))
    assert r.status_code == 403


# --- Ändern ----------------------------------------------------------------

@pytest.mark.django_db
def test_update_setzt_bedingungen(admin_client, seeded):
    inv = beleg_service.create_invoice(
        seeded["app_user"].id, property_id=seeded["obj"].id,
        work_order_id=seeded["order"].id, invoice_date=date(2026, 7, 1),
        lines=_LINES,
    )
    r = _put(admin_client, inv.id, {
        "payment_term_days": 30, "discount_percent": "2", "discount_days": 14,
    })
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["discount_percent"] == "2.00"
    assert body["skonto_bis"] == "2026-07-15"
    assert body["skonto_betrag"] == "23.80"


@pytest.mark.django_db
def test_update_ohne_felder_laesst_bedingungen_stehen(admin_client, seeded):
    """Der Sentinel greift auch über die API: was nicht im Payload steht, bleibt."""
    inv = beleg_service.create_invoice(
        seeded["app_user"].id, property_id=seeded["obj"].id,
        work_order_id=seeded["order"].id, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10, lines=_LINES,
    )
    r = _put(admin_client, inv.id, {"invoice_date": "2026-07-02"})
    assert r.status_code == 200
    body = r.json()
    assert body["payment_term_days"] == 30
    assert body["discount_percent"] == "2.00"
    assert body["skonto_bis"] == "2026-07-12"  # neue Basis: 02.07. + 10 Tage


@pytest.mark.django_db
def test_update_leert_bedingungen_mit_null(admin_client, seeded):
    inv = beleg_service.create_invoice(
        seeded["app_user"].id, property_id=seeded["obj"].id,
        work_order_id=seeded["order"].id, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10, lines=_LINES,
    )
    r = _put(admin_client, inv.id, {
        "discount_percent": None, "discount_days": None,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["discount_percent"] is None and body["discount_days"] is None
    assert body["skonto_betrag"] is None
    assert body["payment_term_days"] == 30


@pytest.mark.django_db
def test_update_halbes_skonto_ergibt_422(admin_client, seeded):
    inv = beleg_service.create_invoice(
        seeded["app_user"].id, property_id=seeded["obj"].id,
        work_order_id=seeded["order"].id, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10, lines=_LINES,
    )
    r = _put(admin_client, inv.id, {"discount_days": None})
    assert r.status_code == 422


@pytest.mark.django_db
def test_update_ohne_recht_ist_403(client_with_role, seeded):
    inv = beleg_service.create_invoice(
        seeded["app_user"].id, property_id=seeded["obj"].id,
        work_order_id=seeded["order"].id, lines=_LINES,
    )
    c = client_with_role("NUR_LESEN")
    r = _put(c, inv.id, {"payment_term_days": 30})
    assert r.status_code == 403


# --- Detail + Buchhaltung ---------------------------------------------------

@pytest.mark.django_db
def test_detail_zeigt_bedingungen_nach_veroeffentlichung(admin_client, seeded):
    app_user, weg = seeded["app_user"], seeded["party"]
    inv = beleg_service.create_invoice(
        app_user.id, property_id=seeded["obj"].id,
        work_order_id=seeded["order"].id, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10, lines=_LINES,
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id,
            role=role, is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)

    r = admin_client.get(f"/api/invoicing/invoices/{inv.id}")
    assert r.status_code == 200
    body = r.json()
    # Fälligkeit wurde beim Veröffentlichen aus dem Zahlungsziel abgeleitet.
    assert body["due_date"] == "2026-07-31"
    assert body["skonto_bis"] == "2026-07-11"
    assert body["skonto_betrag"] == "23.80"

    # Und dieselben Werte read-only im Offene-Posten-Detail der Buchhaltung.
    r2 = admin_client.get(f"/api/buchhaltung/invoices/{inv.id}")
    assert r2.status_code == 200
    op = r2.json()
    assert op["discount_percent"] == "2.00"
    assert op["discount_days"] == 10
    assert op["payment_term_days"] == 30
    assert op["skonto_bis"] == "2026-07-11"
    assert op["skonto_betrag"] == "23.80"
    assert op["skonto_zahlbetrag"] == "1166.20"
    # Invariante: Skonto ändert weder offenen Betrag noch Zahlungsstatus.
    assert op["open_amount"] == "1190.00"
    assert op["payment_status"] == "OFFEN"


@pytest.mark.django_db
def test_create_frist_nach_faelligkeit_ergibt_422(admin_client, seeded):
    """Die Fälligkeit (due_date) ist die maßgebliche Schranke, nicht nur das
    Zahlungsziel — eine Skontofrist dahinter wird abgelehnt, nicht gedeckelt."""
    r = _post(admin_client, _payload(
        seeded, due_date="2026-07-05", discount_percent="2", discount_days="10",
    ))
    assert r.status_code == 422, r.content
    assert "Fälligkeit" in r.json()["detail"]
