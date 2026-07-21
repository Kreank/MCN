"""AP4 — Kontaktstammdaten und Liegenschaft korrigieren (Befunde H2–H8).

Saschas Befund lautete „Nichts lässt sich nachträglich bearbeiten". Der
Kernbefund dahinter: Auf Kontaktdaten existierte genau EINE echte Sperre (der
append-only-Adressinhalt, H1). Alles andere war fehlende API-Oberfläche — kein
Trigger, keine Begründung im Code, nur nie gebaut.

Diese Tests decken die neuen Schreibpfade ab und halten fest, was NICHT geht
und warum.
"""
import uuid
from datetime import date

import pytest

from django.contrib.auth import get_user_model

from db_core.models import Address, ContactPoint, PartyAddress, Person, Property
from db_core.services import identity as identity_service
from db_core.services import property as property_service

User = get_user_model()


@pytest.fixture
def kontakt(app_user):
    """Eine Person mit Telefon und Adresse."""
    party = identity_service.create_person(
        app_user.id, first_name="Erika", last_name="Meyer"
    )
    punkt = identity_service.add_contact_point(
        app_user.id, party.id, contact_type="PHONE", value="030 1234567", is_primary=True
    )
    adresse = identity_service.add_address(
        app_user.id, party.id, address_type="PRIVATE",
        street="Ahornweg", house_number="7", postal_code="10115", city="Berlin",
    )
    return {"app_user": app_user, "party": party, "punkt": punkt, "adresse": adresse}


# --- H2: Kommunikationsweg korrigieren -------------------------------------


@pytest.mark.django_db
def test_telefonnummer_korrigieren(admin_client, kontakt):
    """Befund H2: Eine vertippte Nummer war bisher nicht zu retten."""
    r = admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/contact-points/{kontakt['punkt'].id}",
        data={"value": "030 7654321"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["value"] == "030 7654321"

    kontakt["punkt"].refresh_from_db()
    assert kontakt["punkt"].value == "030 7654321"
    # Korrektur, kein Wechsel: Es entsteht KEINE zweite Zeile.
    assert ContactPoint.objects.filter(party_id=kontakt["party"].id).count() == 1


@pytest.mark.django_db
def test_beendeter_kontaktweg_ist_nicht_mehr_korrigierbar(admin_client, kontakt):
    """Ein beendeter Weg ist Geschichte — Historie wird nicht umgeschrieben."""
    identity_service.deactivate_contact_point(
        kontakt["app_user"].id, kontakt["punkt"].id
    )
    r = admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/contact-points/{kontakt['punkt'].id}",
        data={"value": "030 999"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "beendet" in r.json()["detail"]


@pytest.mark.django_db
def test_leerer_kontaktwert_wird_abgelehnt(admin_client, kontakt):
    r = admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/contact-points/{kontakt['punkt'].id}",
        data={"value": "   "},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_kontaktweg_fremder_party_ist_404(admin_client, kontakt):
    """Die Zuordnung muss stimmen — sonst ließe sich fremdes Zeug ändern."""
    fremd = identity_service.create_person(
        kontakt["app_user"].id, first_name="Fremd", last_name="Person"
    )
    r = admin_client.patch(
        f"/api/identity/parties/{fremd.id}/contact-points/{kontakt['punkt'].id}",
        data={"value": "030 999"},
        content_type="application/json",
    )
    assert r.status_code == 404


# --- H3: Adresszuordnung ändern, ersetzen, beenden -------------------------


@pytest.mark.django_db
def test_adresszuordnung_aendern(admin_client, kontakt):
    """Befund H3: `party_address` hatte außer POST keine Schreiboperation."""
    r = admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/addresses/{kontakt['adresse'].id}",
        data={"address_type": "POSTAL", "label": "Ferienwohnung"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["address_type"] == "POSTAL"
    assert r.json()["label"] == "Ferienwohnung"


@pytest.mark.django_db
def test_adressinhalt_wird_ersetzt_nicht_geaendert(admin_client, kontakt):
    """Befund H1: `identity.address` ist append-only — Korrektur = neue Zeile.

    Prüft beides: Die Zuordnung zeigt danach auf die NEUE Adresse, und die
    ALTE Zeile steht unversehrt (Belege können sie geschnappschusst haben).
    """
    alte_id = kontakt["adresse"].address_id
    adressen_vorher = Address.objects.count()

    r = admin_client.post(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/addresses/{kontakt['adresse'].id}/ersetzen",
        data={
            "street": "Birkenallee",
            "house_number": "12",
            "postal_code": "10117",
            "city": "Berlin",
        },
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["address"]["street"] == "Birkenallee"

    kontakt["adresse"].refresh_from_db()
    assert kontakt["adresse"].address_id != alte_id
    assert Address.objects.count() == adressen_vorher + 1
    alt = Address.objects.get(id=alte_id)
    assert alt.street == "Ahornweg", "Die alte Adresszeile darf sich nicht ändern"


@pytest.mark.django_db
def test_ersetzen_behaelt_typ_und_primaer(admin_client, kontakt):
    """Ersetzt wird der Inhalt, nicht die Zuordnung."""
    r = admin_client.post(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/addresses/{kontakt['adresse'].id}/ersetzen",
        data={"street": "Birkenallee", "postal_code": "10117", "city": "Berlin"},
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["address_type"] == "PRIVATE"
    assert body["is_primary"] is True


@pytest.mark.django_db
def test_adresszuordnung_beenden(admin_client, kontakt):
    """Der Umzug — kein Löschen (Trigger seit 0126)."""
    r = admin_client.post(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/addresses/{kontakt['adresse'].id}/beenden",
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["valid_until"] is not None

    # Die Zeile bleibt, sie ist nur nicht mehr aktiv.
    assert PartyAddress.objects.filter(id=kontakt["adresse"].id).exists()


@pytest.mark.django_db
def test_beenden_am_anlagetag_datiert_auf_morgen(admin_client, kontakt):
    """`CHECK (valid_until > valid_from)` verbietet ein Ende am selben Tag.

    Eine noch heute zurückgenommene Zuordnung wäre sonst nicht beendbar —
    spurlos tilgen ist die Politik des Repos nicht (F-02).
    """
    r = admin_client.post(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/addresses/{kontakt['adresse'].id}/beenden",
        content_type="application/json",
    )
    assert r.status_code == 200
    kontakt["adresse"].refresh_from_db()
    assert kontakt["adresse"].valid_until > kontakt["adresse"].valid_from


# --- H8: Historie abrufbar -------------------------------------------------


@pytest.mark.django_db
def test_include_ended_zeigt_die_historie(admin_client, kontakt):
    """Befund H8: `include_ended` existierte im Service, wurde nie angeboten."""
    identity_service.deactivate_contact_point(
        kontakt["app_user"].id, kontakt["punkt"].id
    )
    pid = kontakt["party"].id

    ohne = admin_client.get(f"/api/identity/parties/{pid}/contact-points")
    assert ohne.status_code == 200
    assert ohne.json() == []

    mit = admin_client.get(
        f"/api/identity/parties/{pid}/contact-points?include_ended=true"
    )
    assert mit.status_code == 200
    assert len(mit.json()) == 1
    assert mit.json()[0]["valid_until"] is not None


@pytest.mark.django_db
def test_include_ended_bei_adressen(admin_client, kontakt):
    identity_service.end_party_address(kontakt["app_user"].id, kontakt["adresse"].id)
    pid = kontakt["party"].id

    assert admin_client.get(f"/api/identity/parties/{pid}/addresses").json() == []
    mit = admin_client.get(f"/api/identity/parties/{pid}/addresses?include_ended=true")
    assert len(mit.json()) == 1


# --- H4: Namen ändern ------------------------------------------------------


@pytest.mark.django_db
def test_heirat_zieht_den_anzeigenamen_mit(admin_client, kontakt):
    """Befund H4 — und der eigentliche Fallstrick dabei.

    `party.display_name` ist der Name in jeder Liste, jeder Suche und jedem
    Beleg. Bliebe er stehen, hieße die Person in ihrer Mappe „Schmidt" und
    überall sonst weiter „Meyer" — schlimmer als gar keine Änderung, weil der
    Widerspruch nicht auffällt.
    """
    r = admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}/person",
        data={"last_name": "Schmidt"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["display_name"] == "Erika Schmidt"

    kontakt["party"].refresh_from_db()
    assert kontakt["party"].display_name == "Erika Schmidt"
    assert Person.objects.get(party_id=kontakt["party"].id).first_name == "Erika"


@pytest.mark.django_db
def test_nachname_darf_nicht_geleert_werden(admin_client, kontakt):
    r = admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}/person",
        data={"last_name": "  "},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Nachname" in r.json()["detail"]


@pytest.mark.django_db
def test_vorname_laesst_sich_nachtraeglich_leeren(admin_client, kontakt):
    """Seit 0125 optional — das muss auch nachträglich gelten."""
    r = admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}/person",
        data={"first_name": None},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["display_name"] == "Meyer"


@pytest.mark.django_db
def test_umfirmierung(admin_client, app_user):
    org = identity_service.create_organization(
        app_user.id, legal_name="Wolff GmbH", organization_type="COMPANY"
    )
    r = admin_client.patch(
        f"/api/identity/parties/{org.id}/organization",
        data={"legal_name": "Wolff & Sohn GmbH"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["display_name"] == "Wolff & Sohn GmbH"


@pytest.mark.django_db
def test_abweichender_anzeigename_ueberlebt_umfirmierung(admin_client, app_user):
    """Ein bewusst gesetzter Anzeigename ist keine Ableitung und bleibt.

    `identity.organization` hat keine Spalte `display_name` — nach dem Anlegen
    ist nicht mehr unterscheidbar, ob der Anzeigename an der Party abgeleitet
    oder gesetzt wurde. Die Regel: Er folgt nur mit, wenn er exakt der alte
    Rechtsname war.
    """
    org = identity_service.create_organization(
        app_user.id, legal_name="Wolff GmbH", organization_type="COMPANY",
        display_name="Wolff Sanitär",
    )
    r = admin_client.patch(
        f"/api/identity/parties/{org.id}/organization",
        data={"legal_name": "Wolff & Sohn GmbH"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["display_name"] == "Wolff Sanitär"


@pytest.mark.django_db
def test_person_patch_auf_organisation_ist_422(admin_client, app_user):
    """Der Subtyp muss passen — sonst liefe der Aufruf ins Leere."""
    org = identity_service.create_organization(
        app_user.id, legal_name="Wolff GmbH", organization_type="COMPANY"
    )
    r = admin_client.patch(
        f"/api/identity/parties/{org.id}/person",
        data={"last_name": "Quatsch"},
        content_type="application/json",
    )
    assert r.status_code == 422


# --- H5: Liegenschaft korrigieren ------------------------------------------


@pytest.fixture
def objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Wohnhaus Süd", property_type="WEG",
        street="Südweg", house_number="4", postal_code="10115", city="Berlin",
    )


@pytest.mark.django_db
def test_liegenschaft_umbenennen(admin_client, objekt):
    r = admin_client.patch(
        f"/api/property/properties/{objekt.id}",
        data={"name": "Wohnanlage Süd"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["name"] == "Wohnanlage Süd"


@pytest.mark.django_db
def test_liegenschaftsadresse_wird_ersetzt(admin_client, objekt):
    """Befund H5 — und die Anschrift ist das, wonach der Monteur fährt."""
    alte_id = objekt.address_id
    r = admin_client.patch(
        f"/api/property/properties/{objekt.id}",
        data={
            "adresse": {
                "street": "Nordweg",
                "house_number": "9",
                "postal_code": "10117",
                "city": "Berlin",
            }
        },
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["address"]["street"] == "Nordweg"

    objekt.refresh_from_db()
    assert objekt.address_id != alte_id
    assert Address.objects.get(id=alte_id).street == "Südweg"


@pytest.mark.django_db
def test_ungueltiges_laenderkuerzel_ist_422_kein_500(admin_client, objekt):
    """Vorher endete das als roher IntegrityError."""
    r = admin_client.patch(
        f"/api/property/properties/{objekt.id}",
        data={
            "adresse": {
                "street": "Nordweg", "postal_code": "10117",
                "city": "Berlin", "country_code": "xx",
            }
        },
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Länderkürzel" in r.json()["detail"]


@pytest.mark.django_db
def test_liegenschaft_404(admin_client, objekt):
    r = admin_client.patch(
        f"/api/property/properties/{uuid.uuid4()}",
        data={"name": "X"},
        content_type="application/json",
    )
    assert r.status_code == 404


# --- Randfälle, die ein Review eingefordert hat ----------------------------


@pytest.mark.django_db
def test_beenden_und_neu_am_selben_tag(admin_client, kontakt):
    """Der Fehlgriff am selben Tag — und die Falle dahinter.

    `excl_party_address_primary` schließt sich über den Gültigkeitszeitraum.
    Eine heute beendete Zeile belegt [heute, morgen) und überlappt die neue
    [heute, ) weiterhin — der natürliche Weg „beenden, dann richtig anlegen"
    endete deshalb in einem 422, das eine bereits beendete Adresse als
    Konflikt meldete. Eine Primäradresse, die keinen Tag galt, verliert jetzt
    beim Beenden ihr Primär-Kennzeichen.
    """
    pid = kontakt["party"].id
    admin_client.post(
        f"/api/identity/parties/{pid}/addresses/{kontakt['adresse'].id}/beenden",
        content_type="application/json",
    )
    r = admin_client.post(
        f"/api/identity/parties/{pid}/addresses",
        data={
            "address_type": "PRIVATE", "street": "Birkenallee",
            "postal_code": "10117", "city": "Berlin", "is_primary": True,
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content

    kontakt["adresse"].refresh_from_db()
    assert kontakt["adresse"].is_primary is False
    assert kontakt["adresse"].valid_until is not None


@pytest.mark.django_db
def test_kontaktweg_beenden_und_neu_am_selben_tag(admin_client, kontakt):
    """Dieselbe Falle beim Kommunikationsweg (war schon vor AP4 da)."""
    pid = kontakt["party"].id
    admin_client.post(
        f"/api/identity/parties/{pid}/contact-points/{kontakt['punkt'].id}/deactivate",
        content_type="application/json",
    )
    r = admin_client.post(
        f"/api/identity/parties/{pid}/contact-points",
        data={"contact_type": "PHONE", "value": "030 7654321", "is_primary": True},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content


@pytest.mark.django_db
def test_legal_name_und_display_name_zusammen(admin_client, app_user):
    """Beide gesendet — der ausdrückliche Anzeigename gewinnt."""
    org = identity_service.create_organization(
        app_user.id, legal_name="Wolff GmbH", organization_type="COMPANY"
    )
    r = admin_client.patch(
        f"/api/identity/parties/{org.id}/organization",
        data={"legal_name": "Wolff & Sohn GmbH", "display_name": "Wolff Sanitär"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["display_name"] == "Wolff Sanitär"


@pytest.mark.django_db
def test_leerer_display_name_faellt_auf_den_rechtsnamen(admin_client, app_user):
    org = identity_service.create_organization(
        app_user.id, legal_name="Wolff GmbH", organization_type="COMPANY",
        display_name="Wolff Sanitär",
    )
    r = admin_client.patch(
        f"/api/identity/parties/{org.id}/organization",
        data={"display_name": ""},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["display_name"] == "Wolff GmbH"


@pytest.mark.django_db
def test_beendete_zuordnung_ist_nicht_mehr_ersetzbar(admin_client, kontakt):
    identity_service.end_party_address(kontakt["app_user"].id, kontakt["adresse"].id)
    r = admin_client.post(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/addresses/{kontakt['adresse'].id}/ersetzen",
        data={"street": "Birkenallee", "postal_code": "10117", "city": "Berlin"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "beendet" in r.json()["detail"]


@pytest.mark.django_db
def test_monteur_erreicht_keinen_der_neuen_schreibpfade(client_with_role, kontakt, objekt):
    """Die Objektgrenze — der sicherheitsrelevante Teil, und er fehlte.

    `_require_party` weist row_scope EIGENE bei jeder Nicht-LESEN-Aktion hart
    ab (403); `PATCH /properties/{id}` läuft über `guard_objekt` und antwortet
    an fremden Objekten mit 404 statt 403 — eine 403 verriete deren Existenz.
    """
    c = client_with_role("MONTEUR")
    pid = kontakt["party"].id

    verboten = [
        c.patch(
            f"/api/identity/parties/{pid}/contact-points/{kontakt['punkt'].id}",
            data={"value": "030 999"}, content_type="application/json",
        ),
        c.patch(
            f"/api/identity/parties/{pid}/addresses/{kontakt['adresse'].id}",
            data={"label": "X"}, content_type="application/json",
        ),
        c.post(
            f"/api/identity/parties/{pid}/addresses/{kontakt['adresse'].id}/beenden",
            content_type="application/json",
        ),
        c.patch(
            f"/api/identity/parties/{pid}/person",
            data={"last_name": "Fremd"}, content_type="application/json",
        ),
    ]
    for r in verboten:
        assert r.status_code in (403, 404), r.content

    # Die Liegenschaft gehört ihm nicht → 404, nicht 403.
    r = c.patch(
        f"/api/property/properties/{objekt.id}",
        data={"name": "Übernommen"}, content_type="application/json",
    )
    assert r.status_code == 404, r.content


# --- H7: Der Nachweis ------------------------------------------------------


@pytest.mark.django_db
def test_jede_aenderung_hinterlaesst_einen_audit_eintrag(admin_client, kontakt):
    """Befund H7: Vor 0126 gab es zu keiner dieser Änderungen einen Nachweis.

    Geprüft wird auch `identity.person` — dort heißt der Primärschlüssel
    `party_id` und nicht `id`. Der Standard-Audit-Trigger hätte stumm NULL als
    `target_id` geschrieben; dafür gibt es seit 0126 `audit_row_update_key`.
    """
    from django.db import connection

    admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}"
        f"/contact-points/{kontakt['punkt'].id}",
        data={"value": "030 7654321"},
        content_type="application/json",
    )
    admin_client.patch(
        f"/api/identity/parties/{kontakt['party'].id}/person",
        data={"last_name": "Schmidt"},
        content_type="application/json",
    )

    with connection.cursor() as cur:
        cur.execute(
            """SELECT target_type, target_id, actor_type
               FROM audit.audit_entry
               WHERE target_type IN ('identity.contact_point', 'identity.person',
                                     'identity.party')
               ORDER BY occurred_at"""
        )
        zeilen = cur.fetchall()

    typen = {z[0] for z in zeilen}
    assert "identity.contact_point" in typen
    assert "identity.person" in typen
    assert "identity.party" in typen, "display_name-Fortschreibung muss auch zählen"

    # Der springende Punkt: target_id ist gefüllt, auch bei person.
    person_zeile = next(z for z in zeilen if z[0] == "identity.person")
    assert person_zeile[1] == kontakt["party"].id
    assert person_zeile[2] == "USER"


@pytest.mark.django_db
def test_die_datenbank_verbietet_das_loeschen(kontakt, objekt):
    """No-Delete aus 0126 auf ALLEN sechs Tabellen.

    Savepoint statt `transaction=True`: Letzteres erzeugte einen
    Teardown-Fehler und vergrößerte die bekannte 19er-Baseline (Djangos
    `flush` benutzt TRUNCATE, das die neuen Trigger verbieten).

    Der erste Wurf dieses Tests hatte einen `(Property, None)`-Eintrag mit
    `continue` — er sah aus, als prüfe er die Liegenschaft, und übersprang sie.
    Ausgerechnet die zwei Trigger mit der größten Reichweite (`property` und
    `party`) waren damit ungetestet. Ein Review hat den toten Zweig gefunden.
    """
    from django.db import ProgrammingError, transaction

    from db_core.models import Party

    faelle = (
        (ContactPoint, kontakt["punkt"].id),
        (PartyAddress, kontakt["adresse"].id),
        (Person, kontakt["party"].id),
        (Party, kontakt["party"].id),
        (Property, objekt.id),
    )
    for modell, pk in faelle:
        assert pk is not None, f"Kein Prüfling für {modell.__name__}"
        with pytest.raises(ProgrammingError, match="append-only"):
            with transaction.atomic():
                modell.objects.filter(pk=pk).delete()
