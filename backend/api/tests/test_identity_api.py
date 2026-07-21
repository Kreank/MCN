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

        from .conftest import grant_role
        au = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Login-Akteur", status="ACTIVE", version=1,
        )
        user.app_user_id = au.id
        user.save()
        # Rechte-Durchsetzung: das Login-Konto braucht jetzt eine Rolle. Für die
        # positiven Schreibtests reicht ADMINISTRATION (darf alles).
        grant_role(au.id, "ADMINISTRATION")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_liste_und_pagination(admin_client, seeded):
    r = admin_client.get("/api/identity/parties?page=1&page_size=2")
    assert r.status_code == 200
    body = r.json()
    # 3 Personen + 2 Orgs = 5 sichtbar (MERGED ausgeblendet)
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2


@pytest.mark.django_db
def test_suche_case_insensitive(admin_client, seeded):
    r = admin_client.get("/api/identity/parties?q=albrecht")
    assert r.status_code == 200
    names = {i["display_name"] for i in r.json()["items"]}
    assert "Anna Albrecht" in names
    assert "Elektro Albrecht GmbH" in names


@pytest.mark.django_db
def test_typfilter(admin_client, seeded):
    r = admin_client.get("/api/identity/parties?party_type=ORGANIZATION")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(i["party_type"] == "ORGANIZATION" for i in body["items"])


@pytest.mark.django_db
def test_merged_wird_ausgeblendet(admin_client, seeded):
    r = admin_client.get("/api/identity/parties")
    ids = {i["id"] for i in r.json()["items"]}
    assert str(seeded["merged"].id) not in ids
    # gezielt nach MERGED gefragt → sichtbar
    r2 = admin_client.get("/api/identity/parties?status=MERGED")
    ids2 = {i["id"] for i in r2.json()["items"]}
    assert str(seeded["merged"].id) in ids2


@pytest.mark.django_db
def test_detail_person(admin_client, seeded):
    pid = seeded["persons"][0].id
    r = admin_client.get(f"/api/identity/parties/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["party_type"] == "PERSON"
    assert body["person"]["first_name"] == "Anna"
    assert body["organization"] is None


@pytest.mark.django_db
def test_detail_organisation(admin_client, seeded):
    oid = seeded["orgs"][0].id
    r = admin_client.get(f"/api/identity/parties/{oid}")
    assert r.status_code == 200
    body = r.json()
    assert body["party_type"] == "ORGANIZATION"
    assert body["organization"]["organization_type"] == "PROPERTY_MANAGEMENT"
    assert body["person"] is None


@pytest.mark.django_db
def test_detail_404(admin_client, seeded):
    r = admin_client.get(f"/api/identity/parties/{uuid.uuid4()}")
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
def test_create_person_ohne_vornamen(client, db):
    """Befund B1 (Migration 0125): Der Vorname ist optional.

    Der Anzeigename ist dann der Nachname allein — er darf nicht mit einem
    führenden Leerzeichen entstehen.
    """
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/identity/parties/person",
        data={"last_name": "Özdemir"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["display_name"] == "Özdemir"
    assert body["person"]["first_name"] is None


@pytest.mark.django_db
def test_create_person_in_einem_rutsch(client, db):
    """Befund F1/F3: Telefon, E-Mail und Adresse gleich bei der Anlage.

    Vorher: Dialog → Liste → Kontakt wiederfinden → Mappe → Reiter Stammdaten →
    Dialog Kontaktweg (zweimal) → Reiter Adressen → Dialog Adresse. Für einen
    einzigen zusammenhängenden Vorgang.
    """
    from db_core.models import ContactPoint, PartyAddress

    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/identity/parties/person",
        data={
            "first_name": "Erika",
            "last_name": "Mustermann",
            "kontakt": {"phone": "030 1234567", "email": "e.mustermann@example.org"},
            "adresse": {
                "street": "Ahornweg",
                "house_number": "7",
                "postal_code": "10115",
                "city": "Berlin",
            },
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    pid = r.json()["id"]

    wege = ContactPoint.objects.filter(party_id=pid)
    assert {w.contact_type for w in wege} == {"PHONE", "EMAIL"}
    assert all(w.is_primary for w in wege)

    adressen = PartyAddress.objects.filter(party_id=pid).select_related("address")
    assert adressen.count() == 1
    zuordnung = adressen.first()
    assert zuordnung.address_type == "PRIVATE"
    assert zuordnung.address.street == "Ahornweg"
    assert zuordnung.address.city == "Berlin"


@pytest.mark.django_db
def test_create_person_ohne_zusatzbloecke_unveraendert(client, db):
    """Die Blöcke sind optional — ohne sie muss der Endpunkt sich wie bisher
    verhalten. Sonst wäre die Erweiterung ein verstecktes Pflichtfeld."""
    from db_core.models import ContactPoint, PartyAddress

    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/identity/parties/person",
        data={"first_name": "Ohne", "last_name": "Zusatz"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    pid = r.json()["id"]
    assert not ContactPoint.objects.filter(party_id=pid).exists()
    assert not PartyAddress.objects.filter(party_id=pid).exists()


@pytest.mark.django_db
def test_durchstich_ist_alles_oder_nichts(client, db):
    """Scheitert die Adresse, darf keine Person ohne sie zurückbleiben.

    Eine solche Waise wäre nicht mehr zu entfernen — `identity.party` trägt den
    No-Delete-Schutz. Derselbe Grund wie bei `quick_intake`.

    Hier scheitert die Vorabprüfung des Service (unbekannter Adresstyp), also
    noch vor jedem Schreibzugriff. Den Pfad, bei dem erst die DATENBANK ablehnt
    und ein Savepoint zurückrollt, prüft `test_durchstich_savepoint_rollt_zurueck`.
    """
    from db_core.models import Party

    c = _logged_in_client(client, with_app_user=True)
    vorher = Party.objects.count()
    r = c.post(
        "/api/identity/parties/person",
        data={
            "last_name": "Waise",
            "adresse": {
                "street": "Ahornweg",
                "postal_code": "10115",
                "city": "Berlin",
                "address_type": "GIBTESNICHT",
            },
        },
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert Party.objects.count() == vorher
    assert not Party.objects.filter(display_name="Waise").exists()


@pytest.mark.django_db
def test_durchstich_savepoint_rollt_zurueck(app_user):
    """Der Pfad, den `kontakt_durchstich` im Docstring zusagt.

    Scheitert die Adresse erst **in der Datenbank**, muss der innere Savepoint
    zurückrollen UND die äußere Klammer alles davor mitnehmen — auch den
    Kommunikationsweg, der zwischen Party und Adresse entsteht.

    Ausgelöst über `excl_party_address_primary`: Die Party trägt bereits eine
    primäre PRIVATE-Adresse, eine zweite im selben Zeitraum ist unzulässig.
    Das ist ein echter DB-Constraint, keine Vorabprüfung — genau der Fall, um
    den es geht.
    """
    from db_core.models import ContactPoint, PartyAddress
    from db_core.services import identity as identity_service

    bestand = identity_service.create_person(
        app_user.id, first_name="Bereits", last_name="Vorhanden"
    )
    identity_service.add_address(
        app_user.id, bestand.id, address_type="PRIVATE",
        street="Erstweg", postal_code="10115", city="Berlin",
    )

    with pytest.raises(ValueError, match="primäre Adresse"):
        identity_service.kontakt_durchstich(
            app_user.id,
            # Statt eine neue Party zu bauen, die vorhandene zurückgeben: So
            # trifft die zweite Primäradresse zuverlässig den Constraint.
            anlegen=lambda: bestand,
            phone="030 000111",
            adresse={
                "address_type": "PRIVATE",
                "street": "Zweitweg",
                "postal_code": "10115",
                "city": "Berlin",
            },
        )

    # Der Kommunikationsweg entstand VOR der Adresse — er muss mit zurück.
    assert not ContactPoint.objects.filter(
        party_id=bestand.id, value="030 000111"
    ).exists()
    # Und die Erstadresse steht unversehrt.
    assert PartyAddress.objects.filter(party_id=bestand.id).count() == 1


@pytest.mark.django_db
def test_create_organisation_mit_kontaktdaten(client, db):
    """Auch die Organisation — und ihre Adresse ist per Vorgabe BUSINESS."""
    from db_core.models import PartyAddress

    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/identity/parties/organization",
        data={
            "legal_name": "Sanitär Wolff GmbH",
            "organization_type": "COMPANY",
            "kontakt": {"phone": "030 999888"},
            "adresse": {
                "street": "Kantstraße",
                "postal_code": "10625",
                "city": "Berlin",
            },
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    zuordnung = PartyAddress.objects.get(party_id=r.json()["id"])
    assert zuordnung.address_type == "BUSINESS"


@pytest.mark.django_db
def test_create_person_ohne_nachnamen_ist_422(client, db):
    """Der Nachname bleibt Pflicht (Befund B3) — ohne ihn kein Anzeigename."""
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/identity/parties/person",
        data={"first_name": "Erika"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


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
def test_create_ohne_login_abgelehnt(anonymous_client, db):
    r = anonymous_client.post(
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
def test_contact_point_anlegen_und_lesen(admin_client, seeded):
    party = seeded["persons"][0]
    r = admin_client.post(
        f"/api/identity/parties/{party.id}/contact-points",
        data={"contact_type": "EMAIL", "value": "anna@example.test"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["value"] == "anna@example.test"
    r2 = admin_client.get(f"/api/identity/parties/{party.id}/contact-points")
    assert r2.status_code == 200
    assert any(c["value"] == "anna@example.test" for c in r2.json())


@pytest.mark.django_db
def test_contact_point_ungueltiger_typ_422(admin_client, seeded):
    party = seeded["persons"][0]
    r = admin_client.post(
        f"/api/identity/parties/{party.id}/contact-points",
        data={"contact_type": "RAUCHZEICHEN", "value": "x"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_contact_point_deaktivieren(admin_client, seeded):
    party = seeded["persons"][0]
    r = admin_client.post(
        f"/api/identity/parties/{party.id}/contact-points",
        data={"contact_type": "PHONE", "value": "+49 89 999",
              "valid_from": "2020-01-01"},
        content_type="application/json",
    )
    cp_id = r.json()["id"]
    r2 = admin_client.post(
        f"/api/identity/parties/{party.id}/contact-points/{cp_id}/deactivate",
        content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2.json()["valid_until"] is not None


@pytest.mark.django_db
def test_adresse_anlegen_und_exklusivitaet_422(admin_client, seeded):
    party = seeded["persons"][1]
    r = admin_client.post(
        f"/api/identity/parties/{party.id}/addresses",
        data={"address_type": "BILLING", "street": "Weg", "postal_code": "12345",
              "city": "Ort", "is_primary": True, "valid_from": "2020-01-01"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["address"]["city"] == "Ort"
    # zweite primäre BILLING im überlappenden Zeitraum → 422
    r2 = admin_client.post(
        f"/api/identity/parties/{party.id}/addresses",
        data={"address_type": "BILLING", "street": "Anders", "postal_code": "54321",
              "city": "Zweitort", "is_primary": True, "valid_from": "2020-06-01"},
        content_type="application/json",
    )
    assert r2.status_code == 422


@pytest.mark.django_db
def test_ansprechpartner_zuordnen_bestehend(admin_client, seeded):
    org = seeded["orgs"][0]
    person = seeded["persons"][2]
    r = admin_client.post(
        f"/api/identity/parties/{org.id}/contact-persons",
        data={"person_party_id": str(person.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["person_party_id"] == str(person.id)
    r2 = admin_client.get(f"/api/identity/parties/{org.id}/contact-persons")
    assert any(c["person_party_id"] == str(person.id) for c in r2.json())


@pytest.mark.django_db
def test_ansprechpartner_neu_anlegen(admin_client, seeded):
    org = seeded["orgs"][0]
    r = admin_client.post(
        f"/api/identity/parties/{org.id}/contact-persons",
        data={"first_name": "Frisch", "last_name": "Kontakt"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["display_name"] == "Frisch Kontakt"


@pytest.mark.django_db
def test_ansprechpartner_unbekannte_person_422(admin_client, seeded):
    org = seeded["orgs"][0]
    r = admin_client.post(
        f"/api/identity/parties/{org.id}/contact-persons",
        data={"person_party_id": str(uuid.uuid4())},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_kontaktweg_eines_objektfremden_kontakts_404(client_with_role, seeded):
    """Objektsicht (0099): Der MONTEUR hat jetzt `identity/LESEN` mit row_scope
    EIGENE — er soll den **Mieter anrufen** können, der die Meldung gemacht hat.

    Sichtbar ist damit aber ausschließlich, wer an einem **seiner Objekte** hängt.
    Dieser Monteur hat keinen Einsatz → kein Objekt → jeder Kontakt ist für ihn
    **404** (nicht 403: seine Existenz geht ihn nichts an). Das Adressbuch des
    Betriebs bleibt zu."""
    party = seeded["persons"][0]
    c = client_with_role("MONTEUR")
    r = c.get(f"/api/identity/parties/{party.id}/contact-points")
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_kontaktweg_schreiben_ohne_recht_403(client_with_role, seeded):
    party = seeded["persons"][0]
    c = client_with_role("NUR_LESEN")  # darf lesen, nicht anlegen
    r = c.post(
        f"/api/identity/parties/{party.id}/contact-points",
        data={"contact_type": "EMAIL", "value": "x@example.test"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_kontaktweg_unbekannter_kontakt_404(admin_client, db):
    r = admin_client.get(f"/api/identity/parties/{uuid.uuid4()}/contact-points")
    assert r.status_code == 404


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


# --- Akquisekanal am Kontakt (0049) ----------------------------------------

@pytest.mark.django_db
def test_akquisekanal_setzen_detail_und_loesen(admin_client):
    pid = admin_client.post(
        "/api/identity/parties/person",
        data={"first_name": "Quelle", "last_name": "Test"},
        content_type="application/json",
    ).json()["id"]
    sid = admin_client.get("/api/company/acquisition-sources").json()[0]["id"]

    r = admin_client.put(
        f"/api/identity/parties/{pid}/acquisition-source",
        data={"source_id": sid}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["acquisition_source"]["id"] == sid
    # Auch das Detail liefert die Quelle.
    d = admin_client.get(f"/api/identity/parties/{pid}").json()
    assert d["acquisition_source"]["id"] == sid
    # Wieder lösen.
    r2 = admin_client.put(
        f"/api/identity/parties/{pid}/acquisition-source",
        data={"source_id": None}, content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2.json()["acquisition_source"] is None


@pytest.mark.django_db
def test_akquisekanal_unbekannt_422(admin_client):
    pid = admin_client.post(
        "/api/identity/parties/person",
        data={"first_name": "Q", "last_name": "T"},
        content_type="application/json",
    ).json()["id"]
    r = admin_client.put(
        f"/api/identity/parties/{pid}/acquisition-source",
        data={"source_id": str(uuid.uuid4())}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_akquisekanal_setzen_ohne_recht_403(client_with_role):
    """NUR_LESEN hat kein identity/AENDERN."""
    c = client_with_role("NUR_LESEN")
    r = c.put(
        f"/api/identity/parties/{uuid.uuid4()}/acquisition-source",
        data={"source_id": None}, content_type="application/json",
    )
    assert r.status_code == 403


# --- Freies Notizfeld am Kontakt (Hero-Angleichung Kontakte-3) -------------

@pytest.mark.django_db
def test_party_note_setzen_und_auslesen(admin_client, seeded):
    pid = str(seeded["persons"][0].id)
    # Ohne Notiz ist das Feld None.
    assert admin_client.get(f"/api/identity/parties/{pid}").json()["note"] is None

    r = admin_client.put(
        f"/api/identity/parties/{pid}/note",
        data={"note": "  VIP-Kunde, nur per Mail  "},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["note"] == "VIP-Kunde, nur per Mail"
    # Persistent im Detail auslesbar.
    assert (
        admin_client.get(f"/api/identity/parties/{pid}").json()["note"]
        == "VIP-Kunde, nur per Mail"
    )
    # Leeren normalisiert auf None.
    r2 = admin_client.put(
        f"/api/identity/parties/{pid}/note",
        data={"note": ""}, content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2.json()["note"] is None


@pytest.mark.django_db
def test_party_note_ohne_recht_403(client_with_role):
    """NUR_LESEN hat kein identity/AENDERN."""
    c = client_with_role("NUR_LESEN")
    r = c.put(
        f"/api/identity/parties/{uuid.uuid4()}/note",
        data={"note": "x"}, content_type="application/json",
    )
    assert r.status_code == 403


# --- Bezeichnung an der Objektadresse (Hero-Angleichung Kontakte-6) --------

@pytest.mark.django_db
def test_adresse_label_setzen_und_listen(admin_client, seeded):
    party = seeded["persons"][2]
    r = admin_client.post(
        f"/api/identity/parties/{party.id}/addresses",
        data={"address_type": "BUSINESS", "street": "Nordweg", "postal_code": "22111",
              "city": "Hamburg", "is_primary": True, "valid_from": "2021-01-01",
              "label": "  Baustelle Nord  "},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    # Anlage-Antwort trägt das (getrimmte) Label.
    assert r.json()["label"] == "Baustelle Nord"
    # Die Adressliste führt es mit.
    lst = admin_client.get(f"/api/identity/parties/{party.id}/addresses").json()
    assert any(a["label"] == "Baustelle Nord" for a in lst)


@pytest.mark.django_db
def test_adresse_ohne_label_bleibt_none(admin_client, seeded):
    party = seeded["persons"][1]
    r = admin_client.post(
        f"/api/identity/parties/{party.id}/addresses",
        data={"address_type": "POSTAL", "street": "Ohnename", "postal_code": "10000",
              "city": "Berlin", "is_primary": True, "valid_from": "2021-01-01"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["label"] is None
