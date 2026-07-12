"""API-Tests „Freier Termin ohne Auftrag" (POST/PATCH /api/planung/einsaetze).

Geprüft wird:
* Anlegen ohne Auftrag (mit/ohne Titel), Titel-Fallback beim auftragsgebundenen
  Einsatz, `is_free`-Kennzeichen,
* Liste/Detail/Plantafel mit **gemischten** Terminen (frei + auftragsgebunden) —
  kein Absturz an work_order=None, Liegenschaft aus dem eigenen Feld,
* Nachtragen des Kontakts (PATCH),
* Rechte: ANLEGEN/AENDERN fail-closed, row_scope EIGENE (Monteur sieht einen
  freien Termin NUR, wenn er ihm zugewiesen ist — der fehlende Auftrag macht ihn
  nicht öffentlich), und dass ein Monteur am eigenen Termin nur Kontakt/Zutritt
  nachtragen darf.
"""
from datetime import datetime, timezone as dt_timezone

import pytest
from django.test import Client

from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

from .conftest import make_app_user, make_role_user

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)
JSON = "application/json"


def _client(role="ADMINISTRATION"):
    user, app_user = make_role_user(role)
    client = Client()
    client.force_login(user)
    return client, app_user


def _property(actor_id, name="Begehungsobjekt"):
    return property_service.create_property(
        actor_id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )


def _order(actor_id, obj=None):
    obj = obj or _property(actor_id, name="Auftragshaus")
    principal = identity_service.create_person(
        actor_id, first_name="Petra", last_name="Prinzipal"
    )
    order = auftrag_service.create_work_order(
        actor_id, property_id=obj.id, title="Sockelrisse setzen"
    )
    auftrag_service.set_order_evidence(
        actor_id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        actor_id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        actor_id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(
            actor_id, work_order_id=order.id, to_status=to_status
        )
    return order


# --- Anlegen ---------------------------------------------------------------

@pytest.mark.django_db
def test_freier_termin_anlegen():
    client, _ = _client()
    r = client.post(
        "/api/planung/einsaetze",
        data={
            "title": "Begehung Dachgeschoss",
            "scheduled_start": T0.isoformat(),
            "scheduled_end": T1.isoformat(),
        },
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["title"] == "Begehung Dachgeschoss"
    assert body["is_free"] is True
    assert body["work_order"] is None
    assert body["property"] is None
    assert body["status"] == "UNGEPLANT"


@pytest.mark.django_db
def test_freier_termin_ohne_titel_422():
    client, _ = _client()
    r = client.post("/api/planung/einsaetze", data={}, content_type=JSON)
    assert r.status_code == 422, r.content
    assert "titel" in r.json()["detail"].lower()


@pytest.mark.django_db
def test_freier_termin_mit_liegenschaft():
    client, app_user = _client()
    obj = _property(app_user.id)
    r = client.post(
        "/api/planung/einsaetze",
        data={"title": "Besichtigung", "property_id": str(obj.id)},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["property"]["id"] == str(obj.id)
    assert body["property"]["city"] == "Berlin"
    assert body["is_free"] is True


@pytest.mark.django_db
def test_auftragsgebundener_termin_faellt_auf_auftragstitel_zurueck():
    client, app_user = _client()
    order = _order(app_user.id)
    r = client.post(
        "/api/planung/einsaetze",
        data={"work_order_id": str(order.id)},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["title"] == "Sockelrisse setzen"
    assert body["is_free"] is False
    assert body["work_order"]["id"] == str(order.id)
    assert body["property"]["name"] == "Auftragshaus"


@pytest.mark.django_db
def test_fremde_liegenschaft_am_auftragstermin_422():
    client, app_user = _client()
    order = _order(app_user.id)
    fremd = _property(app_user.id, name="Fremdes Objekt")
    r = client.post(
        "/api/planung/einsaetze",
        data={"work_order_id": str(order.id), "property_id": str(fremd.id)},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


# --- Liste / Detail / Plantafel mit gemischten Terminen --------------------

@pytest.mark.django_db
def test_liste_mit_gemischten_terminen():
    client, app_user = _client()
    order = _order(app_user.id)
    einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0
    )
    einsatz_service.create_service_job(
        app_user.id, title="Begehung Hinterhof", scheduled_start=T0
    )
    r = client.get("/api/planung/einsaetze")
    assert r.status_code == 200, r.content
    items = r.json()["items"]
    assert len(items) == 2
    frei = [i for i in items if i["is_free"]]
    gebunden = [i for i in items if not i["is_free"]]
    assert len(frei) == 1 and len(gebunden) == 1
    assert frei[0]["work_order"] is None
    assert frei[0]["title"] == "Begehung Hinterhof"
    assert gebunden[0]["work_order"]["order_number"]


@pytest.mark.django_db
def test_suche_findet_freien_termin_ueber_titel():
    client, app_user = _client()
    order = _order(app_user.id)
    einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    einsatz_service.create_service_job(app_user.id, title="Begehung Hinterhof")

    r = client.get("/api/planung/einsaetze?q=Hinterhof")
    assert r.status_code == 200, r.content
    assert r.json()["total"] == 1
    # Die Auftragssuche darf durch den LEFT JOIN nicht kaputtgehen.
    r = client.get("/api/planung/einsaetze?q=Sockelrisse")
    assert r.json()["total"] == 1


@pytest.mark.django_db
def test_detail_eines_freien_termins():
    client, app_user = _client()
    obj = _property(app_user.id)
    kontakt = identity_service.create_person(
        app_user.id, first_name="Nora", last_name="Nachbar"
    )
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=obj.id,
        on_site_contact_party_id=kontakt.id,
    )
    r = client.get(f"/api/planung/einsaetze/{job.id}")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["is_free"] is True
    assert body["work_order"] is None
    assert body["property"]["id"] == str(obj.id)
    assert body["on_site_contact"] == "Nora Nachbar"
    assert body["history"][0]["to_status"] == "UNGEPLANT"


@pytest.mark.django_db
def test_plantafel_zeigt_freien_termin():
    client, app_user = _client()
    obj = _property(app_user.id)
    order = _order(app_user.id)
    einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0
    )
    einsatz_service.create_service_job(
        app_user.id, title="Begehung Hinterhof", property_id=obj.id,
        scheduled_start=T0,
    )
    # Freier Termin ganz ohne Liegenschaft — property_name muss None sein.
    einsatz_service.create_service_job(
        app_user.id, title="Telefonberatung", scheduled_start=T0
    )
    tag = T0.date().isoformat()
    r = client.get(f"/api/planung/plantafel?date_from={tag}&date_to={tag}")
    assert r.status_code == 200, r.content
    jobs = {j["title"]: j for j in r.json()["jobs"]}
    assert jobs["Begehung Hinterhof"]["is_free"] is True
    assert jobs["Begehung Hinterhof"]["property_name"] == "Begehungsobjekt"
    assert jobs["Telefonberatung"]["property_name"] is None
    assert jobs["Sockelrisse setzen"]["is_free"] is False
    assert jobs["Sockelrisse setzen"]["property_name"] == "Auftragshaus"


# --- Kontakt nachtragen (PATCH) --------------------------------------------

@pytest.mark.django_db
def test_kontakt_nachtragen():
    client, app_user = _client()
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    kontakt = identity_service.create_person(
        app_user.id, first_name="Nora", last_name="Nachbar"
    )
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"on_site_contact_party_id": str(kontakt.id)},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["on_site_contact"] == "Nora Nachbar"


@pytest.mark.django_db
def test_kontakt_wieder_entfernen():
    client, app_user = _client()
    kontakt = identity_service.create_person(
        app_user.id, first_name="Nora", last_name="Nachbar"
    )
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", on_site_contact_party_id=kontakt.id
    )
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"on_site_contact_party_id": None},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["on_site_contact"] is None


@pytest.mark.django_db
def test_patch_unbekannter_einsatz_404():
    from uuid import uuid4

    client, _ = _client()
    r = client.patch(
        f"/api/planung/einsaetze/{uuid4()}",
        data={"title": "X"},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_patch_titel_leeren_422():
    client, app_user = _client()
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"title": ""},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


# --- Rechte / row_scope ----------------------------------------------------

@pytest.mark.django_db
def test_nur_lesen_darf_keinen_freien_termin_anlegen():
    client, _ = _client("NUR_LESEN")
    r = client.post(
        "/api/planung/einsaetze",
        data={"title": "Begehung"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_monteur_darf_keinen_freien_termin_anlegen():
    """`require` ist fail-closed: Scope EIGENE → 403 (auch ohne Auftrag)."""
    client, _ = _client("MONTEUR")
    r = client.post(
        "/api/planung/einsaetze",
        data={"title": "Begehung"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_monteur_sieht_freien_termin_nur_wenn_zugewiesen():
    """Datenleck-Probe: Ein freier Termin hat keinen Auftrag und keine
    Liegenschaft, über die man Sichtbarkeit ableiten könnte — die EIGENE-Grenze
    hängt allein an der Zuweisung. Ohne Zuweisung: unsichtbar (nicht plötzlich
    öffentlich)."""
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)

    meiner = einsatz_service.create_service_job(
        dispo.id, title="Meine Begehung", scheduled_start=T0
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=meiner.id, assignee_user_id=monteur.id
    )
    fremder = einsatz_service.create_service_job(
        dispo.id, title="Fremde Begehung", scheduled_start=T0
    )

    r = client.get("/api/planung/einsaetze")
    assert r.status_code == 200, r.content
    items = r.json()["items"]
    assert [i["id"] for i in items] == [str(meiner.id)]

    # Fremder freier Termin: 404, nicht 403 — die Existenz wird nicht verraten.
    r = client.get(f"/api/planung/einsaetze/{fremder.id}")
    assert r.status_code == 404, r.content
    r = client.get(f"/api/planung/einsaetze/{meiner.id}")
    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_monteur_darf_kontakt_am_eigenen_termin_nachtragen():
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    job = einsatz_service.create_service_job(
        dispo.id, title="Begehung", scheduled_start=T0
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    kontakt = identity_service.create_person(
        dispo.id, first_name="Nora", last_name="Nachbar"
    )
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"on_site_contact_party_id": str(kontakt.id)},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["on_site_contact"] == "Nora Nachbar"


@pytest.mark.django_db
def test_monteur_darf_titel_nicht_aendern():
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    job = einsatz_service.create_service_job(dispo.id, title="Begehung")
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"title": "Umgewidmet"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    job.refresh_from_db()
    assert job.title == "Begehung"


@pytest.mark.django_db
def test_monteur_darf_liegenschaft_nicht_aendern():
    """Die Liegenschaft ist Dispositionsdatum — auch am eigenen freien Termin."""
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    job = einsatz_service.create_service_job(dispo.id, title="Begehung")
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    obj = _property(dispo.id, name="Fremdes Objekt")
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"property_id": str(obj.id)},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    job.refresh_from_db()
    assert job.property_id is None


@pytest.mark.django_db
def test_monteur_darf_kontakt_am_auftragstermin_nicht_aendern():
    """Am AUFTRAGSGEBUNDENEN Einsatz ist auch der Vor-Ort-Kontakt ein
    Dispositionsdatum (die Disposition hat ihn mit dem Auftrag gesetzt) — der
    Monteur darf ihn dort nicht ersetzen oder löschen."""
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    order = _order(dispo.id)
    kontakt = identity_service.create_person(
        dispo.id, first_name="Petra", last_name="Prinzipal"
    )
    job = einsatz_service.create_service_job(
        dispo.id, work_order_id=order.id, on_site_contact_party_id=kontakt.id
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"on_site_contact_party_id": None},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    job.refresh_from_db()
    assert job.on_site_contact_party_id == kontakt.id


@pytest.mark.django_db
def test_monteur_darf_fremden_termin_nicht_patchen():
    dispo = make_app_user("Dispo")
    user, _monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    job = einsatz_service.create_service_job(dispo.id, title="Fremde Begehung")
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"on_site_contact_party_id": None},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_patch_ohne_anmeldung_401():
    client, app_user = _client()
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    anonym = Client()
    r = anonym.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"title": "X"},
        content_type=JSON,
    )
    assert r.status_code == 401, r.content
