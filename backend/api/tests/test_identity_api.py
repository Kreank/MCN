"""API-Tests der Identity-Endpoints über den Django-Test-Client.

Der Test-Client baut auf der echten Test-DB auf; die Trigger sind scharf.
GET-Endpoints sind in der Dev-Phase ohne Auth, POST verlangt Django-Session
und ein zugeordnetes app_user.
"""
import uuid

import pytest

from django.contrib.auth import get_user_model

from db_core.models import Party
from db_core.db_context import business_transaction
from db_core.services import identity as identity_service

User = get_user_model()


@pytest.fixture
def seeded(app_user):
    """Ein kleiner, deterministischer Datenbestand: 3 Personen, 2 Orgs, 1 MERGED."""
    persons = [
        identity_service.create_person(app_user.id, first_name="Anna", last_name="Albrecht"),
        identity_service.create_person(app_user.id, first_name="Bernd", last_name="Böhm"),
        identity_service.create_person(app_user.id, first_name="Clara", last_name="Conrad"),
    ]
    orgs = [
        identity_service.create_organization(
            app_user.id, legal_name="Hausverwaltung Meyer GmbH",
            organization_type="PROPERTY_MANAGEMENT",
        ),
        identity_service.create_organization(
            app_user.id, legal_name="Elektro Albrecht GmbH", organization_type="COMPANY",
        ),
    ]
    # Eine Dublette in die erste Person zusammenführen → wird standardmäßig ausgeblendet.
    dublette = identity_service.create_person(app_user.id, first_name="Anna", last_name="Doppelt")
    with business_transaction(app_user.id):
        Party.objects.filter(id=dublette.id).update(
            status="MERGED", merged_into_party_id=persons[0].id
        )
    return {"app_user": app_user, "persons": persons, "orgs": orgs, "merged": dublette}


def _logged_in_client(client, *, with_app_user=True):
    kwargs = {"username": f"u{uuid.uuid4().hex[:8]}", "password": "x"}
    user = User.objects.create_user(**kwargs)
    if with_app_user:
        from db_core.models import AppUser
        au = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Login-Akteur", status="ACTIVE", version=1,
        )
        user.app_user_id = au.id
        user.save()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_liste_und_pagination(client, seeded):
    r = client.get("/api/identity/parties?page=1&page_size=2")
    assert r.status_code == 200
    body = r.json()
    # 3 Personen + 2 Orgs = 5 sichtbar (MERGED ausgeblendet)
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2


@pytest.mark.django_db
def test_suche_case_insensitive(client, seeded):
    r = client.get("/api/identity/parties?q=albrecht")
    assert r.status_code == 200
    names = {i["display_name"] for i in r.json()["items"]}
    assert "Anna Albrecht" in names
    assert "Elektro Albrecht GmbH" in names


@pytest.mark.django_db
def test_typfilter(client, seeded):
    r = client.get("/api/identity/parties?party_type=ORGANIZATION")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(i["party_type"] == "ORGANIZATION" for i in body["items"])


@pytest.mark.django_db
def test_merged_wird_ausgeblendet(client, seeded):
    r = client.get("/api/identity/parties")
    ids = {i["id"] for i in r.json()["items"]}
    assert str(seeded["merged"].id) not in ids
    # gezielt nach MERGED gefragt → sichtbar
    r2 = client.get("/api/identity/parties?status=MERGED")
    ids2 = {i["id"] for i in r2.json()["items"]}
    assert str(seeded["merged"].id) in ids2


@pytest.mark.django_db
def test_detail_person(client, seeded):
    pid = seeded["persons"][0].id
    r = client.get(f"/api/identity/parties/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["party_type"] == "PERSON"
    assert body["person"]["first_name"] == "Anna"
    assert body["organization"] is None


@pytest.mark.django_db
def test_detail_organisation(client, seeded):
    oid = seeded["orgs"][0].id
    r = client.get(f"/api/identity/parties/{oid}")
    assert r.status_code == 200
    body = r.json()
    assert body["party_type"] == "ORGANIZATION"
    assert body["organization"]["organization_type"] == "PROPERTY_MANAGEMENT"
    assert body["person"] is None


@pytest.mark.django_db
def test_detail_404(client, seeded):
    r = client.get(f"/api/identity/parties/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_create_person_eingeloggt(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/identity/parties/person",
        data={"first_name": "Neu", "last_name": "Kunde"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["display_name"] == "Neu Kunde"
    assert body["person"]["last_name"] == "Kunde"
    assert Party.objects.filter(id=body["id"]).exists()


@pytest.mark.django_db
def test_create_organisation_eingeloggt(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/identity/parties/organization",
        data={"legal_name": "Sanitär Wolff GmbH", "organization_type": "COMPANY"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["organization"]["legal_name"] == "Sanitär Wolff GmbH"


@pytest.mark.django_db
def test_create_ohne_app_user_id_403(client, db):
    c = _logged_in_client(client, with_app_user=False)
    r = c.post(
        "/api/identity/parties/person",
        data={"first_name": "Ohne", "last_name": "Akteur"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_create_ohne_login_abgelehnt(client, db):
    r = client.post(
        "/api/identity/parties/person",
        data={"first_name": "Anon", "last_name": "Ymous"},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_create_organisation_ungueltiger_typ(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/identity/parties/organization",
        data={"legal_name": "Kaputt", "organization_type": "FALSCH"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_ohne_csrf_token_abgelehnt(db):
    """SessionAuth erzwingt CSRF: POST ohne Token wird abgelehnt.

    Der Standard-Test-Client prüft CSRF nicht (enforce_csrf_checks=False);
    dieser Test weist den Schutz mit scharfer Prüfung explizit nach.
    """
    from django.test import Client

    csrf_client = Client(enforce_csrf_checks=True)
    _logged_in_client(csrf_client, with_app_user=True)
    r = csrf_client.post(
        "/api/identity/parties/person",
        data={"first_name": "Csrf", "last_name": "Fehlt"},
        content_type="application/json",
    )
    assert r.status_code == 403
    assert not Party.objects.filter(display_name="Csrf Fehlt").exists()
