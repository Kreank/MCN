"""API-Tests der Lieferanten-Anbindungs-Endpoints (/api/pricing/supplier-connections).

Deckt Auth (401 anonym, 403 ohne Recht) und den CRUD-Fluss (Liste/Anlegen/Ändern)
über den Django-Test-Client ab. Setup legt eine Lieferanten-Party über den
identity-Service an.
"""
import uuid

import pytest
from django.test import Client

from db_core.db_context import business_transaction
from db_core.models import AppUser, ArticleSupplierReference
from db_core.services import anbindung as anbindung_service
from db_core.services import artikel as artikel_service
from db_core.services import identity as identity_service
from .conftest import make_role_user

_URL = "/api/pricing/supplier-connections"


def _seed_actor():
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name="Seed", status="ACTIVE", version=1
    )


def _lieferant():
    return identity_service.create_person(
        _seed_actor().id, first_name="Liefer", last_name="GH"
    )


@pytest.mark.django_db
def test_anonym_401(anonymous_client):
    assert anonymous_client.get(_URL).status_code == 401


@pytest.mark.django_db
def test_anlegen_ohne_recht_403(db):
    """Eingeloggt ohne Rolle → 403 auf POST (require_create prüft ANLEGEN)."""
    user, _ = make_role_user(None)
    c = Client()
    c.force_login(user)
    p = _lieferant()
    r = c.post(_URL, data={
        "supplier_party_id": str(p.id), "source_namespace": "gut", "label": "G.U.T.",
    }, content_type="application/json")
    assert r.status_code == 403


@pytest.mark.django_db
def test_crud_fluss(admin_client):
    p = _lieferant()
    # Anlegen
    r = admin_client.post(_URL, data={
        "supplier_party_id": str(p.id), "source_namespace": "GUT", "label": "G.U.T.",
        "source_system": "IDS_CONNECT", "connection_kind": "GROSSHAENDLER",
        "shop_url": "https://shop.gut.example",
    }, content_type="application/json")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["source_namespace"] == "gut"        # kleingeschrieben
    assert body["supplier_name"] is not None
    conn_id = body["id"]

    # Liste enthält die Anbindung
    liste = admin_client.get(_URL).json()
    assert any(x["id"] == conn_id for x in liste)

    # Ändern (PATCH): Status + credential_reference
    r2 = admin_client.patch(f"{_URL}/{conn_id}", data={
        "status": "INACTIVE", "credential_reference": "gut-prod",
    }, content_type="application/json")
    assert r2.status_code == 200, r2.content
    assert r2.json()["status"] == "INACTIVE"
    assert r2.json()["credential_reference"] == "gut-prod"


@pytest.mark.django_db
def test_doppelter_namespace_422(admin_client):
    p = _lieferant()
    payload = {
        "supplier_party_id": str(p.id), "source_namespace": "gut", "label": "G.U.T.",
    }
    assert admin_client.post(_URL, data=payload, content_type="application/json").status_code == 201
    r = admin_client.post(_URL, data=payload, content_type="application/json")
    assert r.status_code == 422


# --- Warenkorb-Preview (IDS-Rückflusskern) ---------------------------------

_CART = (
    '<Warenkorb xsi:schemaLocation="http://www.itek.de/Shop-Anbindung/Warenkorb/ x.xsd">'
    "<WarenkorbInfo><Version>2.5</Version></WarenkorbInfo><Order>"
    "<OrderItem><ArtNo>4711</ArtNo><Qty>50.00</Qty><QU>MTR</QU></OrderItem>"
    "<OrderItem><ArtNo>9999</ArtNo><Qty>1.00</Qty><QU>PCE</QU></OrderItem>"
    "</Order></Warenkorb>"
)


def _connection_mit_artikel():
    actor = _seed_actor()
    supplier = identity_service.create_person(actor.id, first_name="Gross", last_name="Handel")
    conn = anbindung_service.create_connection(
        actor.id, supplier_party_id=supplier.id, source_namespace="gut",
        label="G.U.T.", source_system="IDS_CONNECT",
    )
    art = artikel_service.create_article(
        actor.id, article_number="A-4711", description="Kabelring", unit="m",
    )
    with business_transaction(actor.id):
        ArticleSupplierReference.objects.create(
            id=uuid.uuid4(), article_id=art.id, supplier_party_id=supplier.id,
            source_system="DATANORM", source_namespace="gut",
            supplier_article_number="4711", valid_from="2020-01-01",
        )
    return conn, art


@pytest.mark.django_db
def test_warenkorb_preview_mapped(admin_client):
    conn, art = _connection_mit_artikel()
    r = admin_client.post(
        f"{_URL}/{conn.id}/warenkorb/preview", data=_CART,
        content_type="application/xml",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["total"] == 2 and body["matched"] == 1
    treffer = next(p for p in body["positions"] if p["art_no"] == "4711")
    assert treffer["matched"] and treffer["article_number"] == "A-4711"
    fehl = next(p for p in body["positions"] if p["art_no"] == "9999")
    assert not fehl["matched"] and fehl["article_id"] is None


@pytest.mark.django_db
def test_warenkorb_preview_ungueltiges_xml_422(admin_client):
    conn, _ = _connection_mit_artikel()
    r = admin_client.post(
        f"{_URL}/{conn.id}/warenkorb/preview", data="<Warenkorb><Order>",
        content_type="application/xml",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_warenkorb_preview_unbekannte_anbindung_404(admin_client):
    r = admin_client.post(
        f"{_URL}/{uuid.uuid4()}/warenkorb/preview", data=_CART,
        content_type="application/xml",
    )
    assert r.status_code == 404
