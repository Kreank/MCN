"""API-Tests der Lieferanten-Anbindungs-Endpoints (/api/pricing/supplier-connections).

Deckt Auth (401 anonym, 403 ohne Recht) und den CRUD-Fluss (Liste/Anlegen/Ändern)
über den Django-Test-Client ab. Setup legt eine Lieferanten-Party über den
identity-Service an.
"""
import uuid

import pytest
from django.test import Client

from db_core.models import AppUser
from db_core.services import identity as identity_service
from .conftest import make_role_user

_URL = "/api/pricing/supplier-connections"


def _lieferant():
    actor = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Seed", status="ACTIVE", version=1
    )
    return identity_service.create_person(actor.id, first_name="Liefer", last_name="GH")


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
