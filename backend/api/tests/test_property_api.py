"""API-Tests der Property-Endpoints über den Django-Test-Client.

Analog zu test_identity_api: GET ohne Auth (Dev-Phase), POST verlangt
Django-Session + zugeordnetes app_user. Die Test-DB trägt alle echten Trigger.
"""
import uuid
from datetime import date

import pytest

from django.contrib.auth import get_user_model

from db_core.models import Property
from db_core.services import identity as identity_service
from db_core.services import property as property_service

User = get_user_model()


@pytest.fixture
def seeded(app_user):
    """Zwei Liegenschaften: eine WEG (mit Gebäude/Einheiten/Rollen), eine
    gewerbliche. Plus eine Party für die Eigentümerrolle."""
    eigentuemer = identity_service.create_organization(
        app_user.id, legal_name="WEG Beispielweg 1", organization_type="WEG",
    )
    weg = property_service.create_property(
        app_user.id, name="Wohnanlage Beispielweg", property_type="WEG",
        street="Beispielweg", house_number="1", postal_code="10115", city="Berlin",
    )
    building = property_service.add_building(
        app_user.id, property_id=weg.id, building_number="A", name="Vorderhaus",
    )
    property_service.add_unit(
        app_user.id, building_id=building.id, property_id=weg.id,
        unit_type="APARTMENT", unit_number="WE 1",
    )
    property_service.add_unit(
        app_user.id, building_id=building.id, property_id=weg.id,
        unit_type="APARTMENT", unit_number="WE 2",
    )
    property_service.add_party_role(
        app_user.id, property_id=weg.id, party_id=eigentuemer.id,
        role="COMMUNITY_OF_OWNERS", valid_from=date(2020, 1, 1),
    )

    commercial = property_service.create_property(
        app_user.id, name="Rheinpassage Kontor", property_type="COMMERCIAL",
        street="Rheinstraße", house_number="9", postal_code="50667", city="Köln",
    )
    return {
        "app_user": app_user,
        "weg": weg,
        "commercial": commercial,
        "building": building,
        "eigentuemer": eigentuemer,
    }


def _logged_in_client(client, *, with_app_user=True):
    from .conftest import grant_role
    kwargs = {"username": f"u{uuid.uuid4().hex[:8]}", "password": "x"}
    user = User.objects.create_user(**kwargs)
    if with_app_user:
        from db_core.models import AppUser
        au = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Login-Akteur", status="ACTIVE", version=1,
        )
        user.app_user_id = au.id
        user.save()
        grant_role(au.id, "ADMINISTRATION")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_liste_und_pagination(admin_client, seeded):
    r = admin_client.get("/api/property/properties?page=1&page_size=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert len(body["items"]) == 1
    # Ortsangabe kommt aus der verknüpften Adresse.
    assert "city" in body["items"][0]


@pytest.mark.django_db
def test_suche_nach_name(admin_client, seeded):
    r = admin_client.get("/api/property/properties?q=Rheinpassage")
    assert r.status_code == 200
    names = {i["name"] for i in r.json()["items"]}
    assert names == {"Rheinpassage Kontor"}


@pytest.mark.django_db
def test_suche_nach_nummer(admin_client, seeded):
    # Alle Demonummern beginnen mit OBJ- → Suche liefert beide.
    r = admin_client.get("/api/property/properties?q=OBJ-")
    assert r.json()["total"] == 2


@pytest.mark.django_db
def test_typfilter(admin_client, seeded):
    r = admin_client.get("/api/property/properties?property_type=COMMERCIAL")
    body = r.json()
    assert body["total"] == 1
    assert all(i["property_type"] == "COMMERCIAL" for i in body["items"])


@pytest.mark.django_db
def test_detail_mit_adresse_gebaeuden_rollen(admin_client, seeded):
    pid = seeded["weg"].id
    r = admin_client.get(f"/api/property/properties/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["property_type"] == "WEG"
    assert body["address"]["street"] == "Beispielweg"
    assert body["address"]["city"] == "Berlin"
    # Ein Gebäude mit zwei Einheiten.
    assert len(body["buildings"]) == 1
    assert body["buildings"][0]["building_number"] == "A"
    assert len(body["buildings"][0]["units"]) == 2
    # Eigentümergemeinschaft als aktuelle Rolle.
    roles = body["party_roles"]
    assert len(roles) == 1
    assert roles[0]["role"] == "COMMUNITY_OF_OWNERS"
    assert roles[0]["party_display_name"] == "WEG Beispielweg 1"
    assert roles[0]["is_current"] is True


@pytest.mark.django_db
def test_detail_404(admin_client, seeded):
    r = admin_client.get(f"/api/property/properties/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_create_eingeloggt(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/property/properties",
        data={
            "name": "Neubau Nord",
            "property_type": "RENTAL_PROPERTY",
            "street": "Nordallee",
            "postal_code": "20095",
            "city": "Hamburg",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["name"] == "Neubau Nord"
    assert body["property_number"].startswith("OBJ-")
    assert body["address"]["city"] == "Hamburg"
    assert Property.objects.filter(id=body["id"]).exists()


@pytest.mark.django_db
def test_create_ungueltiger_typ_422(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/property/properties",
        data={
            "name": "Kaputt", "property_type": "FALSCH",
            "street": "S", "postal_code": "1", "city": "C",
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_ohne_app_user_id_403(client, db):
    c = _logged_in_client(client, with_app_user=False)
    r = c.post(
        "/api/property/properties",
        data={
            "name": "Ohne Akteur", "property_type": "OTHER",
            "street": "S", "postal_code": "1", "city": "C",
        },
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_create_ohne_login_abgelehnt(anonymous_client, db):
    r = anonymous_client.post(
        "/api/property/properties",
        data={
            "name": "Anon", "property_type": "OTHER",
            "street": "S", "postal_code": "1", "city": "C",
        },
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


# --- Schreibende Unterstruktur-Endpoints -----------------------------------

@pytest.mark.django_db
def test_add_building_happy(admin_client, seeded):
    r = admin_client.post(
        f"/api/property/properties/{seeded['weg'].id}/buildings",
        data={"building_number": "B", "name": "Hinterhaus"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["building_number"] == "B"
    assert body["name"] == "Hinterhaus"
    assert body["units"] == []


@pytest.mark.django_db
def test_add_building_leere_nummer_422(admin_client, seeded):
    r = admin_client.post(
        f"/api/property/properties/{seeded['weg'].id}/buildings",
        data={"building_number": "  "},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_add_building_doppelte_nummer_422(admin_client, seeded):
    # Gebäude "A" existiert bereits an der WEG (seeded). Die UNIQUE-Verletzung
    # muss als 422 mit klarer Meldung enden, nicht als 500.
    r = admin_client.post(
        f"/api/property/properties/{seeded['weg'].id}/buildings",
        data={"building_number": "A", "name": "Doppelgänger"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "bereits" in r.json()["detail"]


@pytest.mark.django_db
def test_add_building_unbekannte_liegenschaft_404(admin_client, seeded):
    r = admin_client.post(
        f"/api/property/properties/{uuid.uuid4()}/buildings",
        data={"building_number": "B"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_add_building_ohne_recht_403(client_with_role, seeded):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"/api/property/properties/{seeded['weg'].id}/buildings",
        data={"building_number": "B"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_add_unit_happy(admin_client, seeded):
    r = admin_client.post(
        f"/api/property/buildings/{seeded['building'].id}/units",
        data={"unit_type": "GARAGE", "unit_number": "TG 1"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["unit_type"] == "GARAGE"
    assert body["unit_number"] == "TG 1"


@pytest.mark.django_db
def test_add_unit_ungueltiger_typ_422(admin_client, seeded):
    r = admin_client.post(
        f"/api/property/buildings/{seeded['building'].id}/units",
        data={"unit_type": "FALSCH", "unit_number": "1"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_add_unit_unbekanntes_gebaeude_404(admin_client, seeded):
    r = admin_client.post(
        f"/api/property/buildings/{uuid.uuid4()}/units",
        data={"unit_type": "APARTMENT", "unit_number": "1"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_add_party_role_happy(admin_client, seeded):
    r = admin_client.post(
        f"/api/property/properties/{seeded['weg'].id}/parties",
        data={
            "party_id": str(seeded["eigentuemer"].id),
            "role": "PROPERTY_OWNER",
            "valid_from": "2021-01-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["role"] == "PROPERTY_OWNER"
    assert body["party_display_name"] == "WEG Beispielweg 1"
    assert body["is_current"] is True


@pytest.mark.django_db
def test_add_party_role_ungueltige_rolle_422(admin_client, seeded):
    r = admin_client.post(
        f"/api/property/properties/{seeded['weg'].id}/parties",
        data={
            "party_id": str(seeded["eigentuemer"].id),
            "role": "HAUSMEISTER",
            "valid_from": "2021-01-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_add_party_role_ende_vor_beginn_422(admin_client, seeded):
    # valid_until vor valid_from verletzt property_party_role_check; die
    # Vorabprüfung muss das als 422 abweisen, nicht als 500 durchschlagen lassen.
    r = admin_client.post(
        f"/api/property/properties/{seeded['weg'].id}/parties",
        data={
            "party_id": str(seeded["eigentuemer"].id),
            "role": "PROPERTY_OWNER",
            "valid_from": "2021-06-01",
            "valid_until": "2021-03-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "nach dem Gültig-ab-Datum" in r.json()["detail"]


@pytest.mark.django_db
def test_add_party_role_ohne_recht_403(client_with_role, seeded):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"/api/property/properties/{seeded['weg'].id}/parties",
        data={
            "party_id": str(seeded["eigentuemer"].id),
            "role": "PROPERTY_OWNER",
            "valid_from": "2021-01-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 403
