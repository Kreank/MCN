"""API der Sammelrechnung: `POST /api/invoicing/invoices/sammelrechnung`.

Geprüft wird, was man nur an der API sieht: der 201 mit dem fertigen Beleg, der
**422** bei zwei Eigentümern (die Invariante, die dieser Endpunkt als einziger
durchsetzt — INVARIANTEN.md §2) und das Recht (`invoicing/ANLEGEN`: der Monteur
schreibt Berichte, er stellt keine Rechnungen).
"""
import uuid
from datetime import date

import pytest

from db_core.models import AppUser
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import eigentum as eigentum_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _pos(desc, preis="30.00"):
    return [{
        "line_type": "MATERIAL", "description": desc, "quantity": 5,
        "unit": "m2", "unit_price": preis, "tax_code": "DE_19",
    }]


@pytest.fixture
def haus(db):
    """Drei Wohnungen: zwei gehören Herrn Meier, eine Frau Yilmaz."""
    actor = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Backoffice", status="ACTIVE", version=1
    )
    a = actor.id
    prop = property_service.create_property(
        a, name="WEG Sammel-API", property_type="WEG",
        street="Sammelweg", house_number="7", postal_code="10365", city="Berlin",
    )
    gebaeude = property_service.add_building(
        a, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    einheiten = {
        nr: property_service.add_unit(
            a, building_id=gebaeude.id, property_id=prop.id,
            unit_type="APARTMENT", unit_number=nr, storey=etage,
        )
        for nr, etage in (("1", "EG"), ("2", "1. OG"), ("3", "2. OG"))
    }
    meier = identity_service.create_person(a, first_name="Klaus", last_name="Meier")
    yilmaz = identity_service.create_person(a, first_name="Aylin", last_name="Yilmaz")
    for nr, partei in (("1", meier), ("2", meier), ("3", yilmaz)):
        eigentum_service.create_stand(
            a, unit_id=einheiten[nr].id, valid_from=date(2020, 1, 1),
            source_type="OWNER_LIST", source_reference="Eigentümerliste",
            distribution_status="COMPLETE",
            eigentuemer=[{
                "party_id": partei.id, "share_numerator": 1,
                "share_denominator": 1, "ownership_type": "SOLE",
                "confirmation_status": "CONFIRMED",
            }],
        )
    return {"actor": actor, "prop": prop, "einheiten": einheiten}


def _entwurf(haus, nr, bezeichnung):
    a = haus["actor"].id
    order = auftrag_service.create_work_order(
        a, property_id=haus["prop"].id, title=f"Bad WE {nr}",
        unit_id=haus["einheiten"][nr].id,
    )
    return beleg_service.create_invoice(
        a, property_id=haus["prop"].id, invoice_type="RECHNUNG",
        work_order_id=order.id, lines=_pos(bezeichnung),
    )


@pytest.mark.django_db
def test_sammelrechnung_liefert_den_fertigen_beleg(admin_client, haus):
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")

    r = admin_client.post(
        "/api/invoicing/invoices/sammelrechnung",
        data={"invoice_ids": [str(e1.id), str(e2.id)]},
        content_type="application/json",
    )

    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "ENTWURF"
    assert body["net_total"] == "300.00"
    assert [l["description"] for l in body["lines"]] == ["Bad EG", "Bad 1. OG"]
    assert [ru["title"] for ru in body["rubriken"]] == [
        "Vorderhaus · EG · WE 1",
        "Vorderhaus · 1. OG · WE 2",
    ]


@pytest.mark.django_db
def test_zwei_eigentuemer_sind_ein_422(admin_client, haus):
    """Die Invariante, die sonst niemand mehr durchsetzt (INVARIANTEN.md §2)."""
    meier = _entwurf(haus, "1", "Bad EG")
    yilmaz = _entwurf(haus, "3", "Bad 2. OG")

    r = admin_client.post(
        "/api/invoicing/invoices/sammelrechnung",
        data={"invoice_ids": [str(meier.id), str(yilmaz.id)]},
        content_type="application/json",
    )

    assert r.status_code == 422
    assert "verschiedenen Eigentümern" in r.json()["detail"]


@pytest.mark.django_db
def test_die_verworfenen_entwuerfe_verschwinden_aus_der_liste(admin_client, haus):
    """Verworfene Entwürfe stehen niemandem mehr im Weg — sie bleiben aber lesbar."""
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")
    admin_client.post(
        "/api/invoicing/invoices/sammelrechnung",
        data={"invoice_ids": [str(e1.id), str(e2.id)]},
        content_type="application/json",
    )

    offen = admin_client.get("/api/invoicing/invoices").json()
    ids = {item["id"] for item in offen["items"]}
    assert str(e1.id) not in ids and str(e2.id) not in ids
    # Direkt aufgerufen ist der Entwurf weiterhin da.
    einzeln = admin_client.get(f"/api/invoicing/invoices/{e1.id}")
    assert einzeln.status_code == 200
    assert einzeln.json()["status"] == "VERWORFEN"


@pytest.mark.django_db
def test_ohne_recht_kein_beleg(client, haus):
    """Fail-closed: ohne Anmeldung entsteht keine Rechnung."""
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")

    r = client.post(
        "/api/invoicing/invoices/sammelrechnung",
        data={"invoice_ids": [str(e1.id), str(e2.id)]},
        content_type="application/json",
    )

    assert r.status_code in (401, 403)
