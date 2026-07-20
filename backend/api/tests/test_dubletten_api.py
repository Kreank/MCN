"""Dublettenvermeidung bei der Erfassung — Adresssuche, Steckbrief, Adressabgleich.

Der Fall, den diese Tests festhalten: Ein Mieter ruft an und nennt seine Adresse.
Findet der Liegenschafts-Picker daraufhin nichts, legt der Mitarbeiter eine
**Dublette** an — und an der hängen danach Vorgänge, Aufträge und Belege.

Drei Dinge müssen dafür stimmen, und jedes hat hier seinen Test:

1. Die Liste **sucht über die Adresse** (Straße, PLZ, Ort) und über die Adressen
   der **Gebäude** — nicht nur über Name und Nummer.
2. Jeder Treffer trägt seinen **Steckbrief** (Eigentümer ≠ Verwaltung, Telefon
   mit Herkunft), sonst ist eine Liste gleichnamiger WEGs keine Entscheidungshilfe.
3. Der **Adressabgleich** meldet auch den WEG-Fall: gleiche Straße, andere
   Hausnummer.
"""
import uuid
from datetime import date, datetime, timezone as dt_timezone

import pytest
from django.test import Client

from db_core.db_context import business_transaction
from db_core.models import Address
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import verwaltung as verwaltung_service

from .conftest import logged_in_client, make_role_user

DUBLETTEN = "/api/property/properties/adress-dubletten"

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)


def _adresse(actor, *, street, house_number, postal_code, city):
    """identity.address direkt anlegen (unveränderlich, nur INSERT)."""
    with business_transaction(actor):
        return Address.objects.create(
            id=uuid.uuid4(), street=street, house_number=house_number,
            postal_code=postal_code, city=city, country_code="DE",
        )


@pytest.fixture
def welt(app_user):
    """Die Albrechtstraße: **eine** WEG über mehrere Hausnummern.

    Die Liegenschaft steht unter „Albrechtstraße 30"; ihr Gebäude „22" liegt in
    der Nachbarhausnummer. Genau daran scheitert ein Gleichheitsabgleich.

    Dazu die Unterscheidung, um die es beim Steckbrief geht: Eigentümerin ist die
    **WEG**, verwaltet wird sie von der **Stegos Immobilien GmbH** (Mandat), und
    die Telefonnummer gehört dem **Standardkontakt des Mandats**.
    """
    a = app_user.id
    weg_partei = identity_service.create_organization(
        a, legal_name="WEG Albrechtstraße 30", organization_type="WEG",
    )
    verwalter = identity_service.create_organization(
        a, legal_name="Stegos Immobilien GmbH",
        organization_type="PROPERTY_MANAGEMENT",
    )
    kontakt = identity_service.create_person(a, first_name="Rita", last_name="Meier")
    identity_service.add_contact_point(
        a, kontakt.id, contact_type="PHONE", value="+49 30 79085327", is_primary=True,
    )
    identity_service.add_contact_point(
        a, weg_partei.id, contact_type="PHONE", value="030 11110825", is_primary=True,
    )

    weg = property_service.create_property(
        a, name="WEG Albrechtstr.", property_type="WEG",
        street="Albrechtstraße", house_number="30",
        postal_code="12167", city="Berlin",
    )
    property_service.add_party_role(
        a, property_id=weg.id, party_id=weg_partei.id,
        role="COMMUNITY_OF_OWNERS", valid_from=date(2020, 1, 1),
    )
    haus30 = property_service.add_building(
        a, property_id=weg.id, building_number="30", name="Haus 30",
    )
    property_service.add_unit(
        a, building_id=haus30.id, property_id=weg.id,
        unit_type="APARTMENT", unit_number="WE 1",
    )
    property_service.add_unit(
        a, building_id=haus30.id, property_id=weg.id,
        unit_type="APARTMENT", unit_number="WE 2",
    )
    # Das Gebäude in der NACHBARhausnummer — der Kern des WEG-Falls.
    adr22 = _adresse(a, street="Albrechtstraße", house_number="22",
                     postal_code="12167", city="Berlin")
    haus22 = property_service.add_building(
        a, property_id=weg.id, building_number="22", name="Haus 22",
        address_id=adr22.id,
    )
    verwaltung_service.create_mandat(
        a, property_id=weg.id,
        management_party_id=verwalter.id,
        principal_party_id=weg_partei.id,
        default_contact_party_id=kontakt.id,
        mandate_type="WEG_MANAGEMENT", scope_type="ENTIRE_PROPERTY",
        valid_from=date(2021, 1, 1),
    )

    # Ein zweites, unbeteiligtes Objekt in einer anderen Stadt.
    fremd = property_service.create_property(
        a, name="Rheinpassage Kontor", property_type="COMMERCIAL",
        street="Rheinstraße", house_number="9", postal_code="50667", city="Köln",
    )
    return {
        "app_user": app_user, "weg": weg, "fremd": fremd,
        "weg_partei": weg_partei, "verwalter": verwalter, "kontakt": kontakt,
        "haus22": haus22, "haus30": haus30,
    }


# ===========================================================================
# 1. Die Liste sucht über die Adresse
# ===========================================================================

@pytest.mark.django_db
def test_suche_ueber_strasse(admin_client, welt):
    """Der Anrufer nennt die Straße — der Name der Liegenschaft ist ein anderer."""
    r = admin_client.get("/api/property/properties?q=Albrechtstraße")
    assert r.status_code == 200, r.content
    assert {i["id"] for i in r.json()["items"]} == {str(welt["weg"].id)}


@pytest.mark.django_db
def test_suche_ueber_plz_und_ort(admin_client, welt):
    assert {i["id"] for i in admin_client.get(
        "/api/property/properties?q=12167").json()["items"]} == {str(welt["weg"].id)}
    assert {i["id"] for i in admin_client.get(
        "/api/property/properties?q=Köln").json()["items"]} == {str(welt["fremd"].id)}


@pytest.mark.django_db
def test_suche_ueber_strasse_und_hausnummer(admin_client, welt):
    """Zwei Tokens, zwei verschiedene Felder — UND über Tokens, ODER über Felder."""
    r = admin_client.get("/api/property/properties?q=Albrechtstraße 30")
    assert {i["id"] for i in r.json()["items"]} == {str(welt["weg"].id)}


@pytest.mark.django_db
def test_suche_findet_ueber_die_gebaeudeadresse(admin_client, welt):
    """**Der WEG-Fall in der Liste**: Die 22 steht nur am Gebäude, nicht am Objekt."""
    r = admin_client.get("/api/property/properties?q=Albrechtstraße 22")
    assert {i["id"] for i in r.json()["items"]} == {str(welt["weg"].id)}


@pytest.mark.django_db
def test_suche_findet_ausgeschriebene_strasse_bei_abgekuerztem_bestand(
    admin_client, welt
):
    """Gespeichert „Ahornstr.", getippt „Ahornstraße" — **der Hauptfall**.

    Der Anrufer sagt die ausgeschriebene Form, der Bestand trägt die abgekürzte.
    Ohne die Suffix-Normalisierung findet ein reiner Teilstringvergleich hier
    nichts (`ahornstrasse` steckt nicht in `ahornstr`) — und der Mitarbeiter legt
    genau die Dublette an, die dieser Slice verhindern soll. Eine eigene Straße
    (nicht die Albrechtstraße der Fixture) hält den Vergleich eindeutig.
    """
    kurz = property_service.create_property(
        welt["app_user"].id, name="Haus Kurzform", property_type="RENTAL_PROPERTY",
        street="Ahornstr.", house_number="7", postal_code="12167", city="Berlin",
    )
    for eingabe in ("Ahornstrasse", "Ahornstraße", "Ahornstr", "Ahornstr."):
        r = admin_client.get(f"/api/property/properties?q={eingabe}")
        assert r.status_code == 200, r.content
        assert {i["id"] for i in r.json()["items"]} == {str(kurz.id)}, eingabe


@pytest.mark.django_db
def test_suche_findet_abgekuerzte_strasse_bei_ausgeschriebenem_bestand(
    admin_client, welt
):
    """Die Gegenrichtung: gespeichert „Albrechtstraße", getippt „Albrechtstr"."""
    for eingabe in ("Albrechtstr", "Albrechtstr.", "Albrechtstrasse", "Albrechtstraße"):
        r = admin_client.get(f"/api/property/properties?q={eingabe}")
        assert r.status_code == 200, r.content
        assert {i["id"] for i in r.json()["items"]} == {str(welt["weg"].id)}, eingabe


@pytest.mark.django_db
def test_gebaeudeadresse_ist_in_beiden_schreibweisen_auffindbar(
    admin_client, welt, app_user
):
    """Dieselbe Beidseitigkeit an der **Gebäude**adresse (der WEG-Fall).

    Die Straße steht hier nur am Gebäude, nicht an der Liegenschaft — genau der
    Fall, für den die Gebäude-Subquery existiert. Sie braucht die Suffixform
    ebenso, sonst ist auch dieser Weg richtungsabhängig.

    Beide Bestandsschreibweisen kommen vor: ein Gebäude in „Kurzestr." (abgekürzt)
    und eines in „Langestraße" (ausgeschrieben).
    """
    a = app_user.id
    kurz_objekt = property_service.create_property(
        a, name="Hof Nebenan", property_type="WEG",
        street="Rosenweg", house_number="1", postal_code="12167", city="Berlin",
    )
    property_service.add_building(
        a, property_id=kurz_objekt.id, building_number="8", name="Haus 8",
        address_id=_adresse(a, street="Kurzestr.", house_number="8",
                            postal_code="12167", city="Berlin").id,
    )
    lang_objekt = property_service.create_property(
        a, name="Hof Gegenüber", property_type="WEG",
        street="Nelkenweg", house_number="2", postal_code="12167", city="Berlin",
    )
    property_service.add_building(
        a, property_id=lang_objekt.id, building_number="5", name="Haus 5",
        address_id=_adresse(a, street="Langestraße", house_number="5",
                            postal_code="12167", city="Berlin").id,
    )

    # Bestand abgekürzt („Kurzestr.") — auch die ausgeschriebene Eingabe trifft.
    for eingabe in ("Kurzestrasse", "Kurzestraße", "Kurzestr", "Kurzestr."):
        r = admin_client.get(f"/api/property/properties?q={eingabe}")
        assert {i["id"] for i in r.json()["items"]} == {str(kurz_objekt.id)}, eingabe

    # Bestand ausgeschrieben („Langestraße") — auch die abgekürzte Eingabe trifft.
    for eingabe in ("Langestr", "Langestr.", "Langestrasse", "Langestraße"):
        r = admin_client.get(f"/api/property/properties?q={eingabe}")
        assert {i["id"] for i in r.json()["items"]} == {str(lang_objekt.id)}, eingabe


@pytest.mark.django_db
def test_suche_nach_name_und_nummer_bleibt(admin_client, welt):
    """Die alte Suche darf nicht verloren gehen — nur erweitert werden."""
    r = admin_client.get("/api/property/properties?q=Rheinpassage")
    assert {i["name"] for i in r.json()["items"]} == {"Rheinpassage Kontor"}
    r = admin_client.get(f"/api/property/properties?q={welt['weg'].property_number}")
    assert {i["id"] for i in r.json()["items"]} == {str(welt["weg"].id)}


# ===========================================================================
# 2. Der Steckbrief — Verwaltung ist NICHT der Eigentümer
# ===========================================================================

@pytest.mark.django_db
def test_steckbrief_trennt_eigentuemer_und_verwaltung(admin_client, welt):
    r = admin_client.get("/api/property/properties?q=Albrechtstraße")
    item = r.json()["items"][0]

    assert item["address_line"] == "Albrechtstraße 30, 12167 Berlin"
    # Eigentümerin ist die WEG (Rolle COMMUNITY_OF_OWNERS) …
    assert item["eigentuemer"] == ["WEG Albrechtstraße 30"]
    # … verwaltet wird sie von Stegos (Mandat) — zwei verschiedene Felder.
    assert item["verwaltung"] == "Stegos Immobilien GmbH"
    # Telefon aus dem Standardkontakt des Mandats, mit benannter Herkunft.
    assert item["telefon"] == "+49 30 79085327"
    assert item["telefon_quelle"] == "Verwaltung Rita Meier"
    assert item["einheiten_anzahl"] == 2
    # Nur die ABWEICHENDE Gebäudeadresse — die 30 steht schon in address_line.
    assert item["gebaeude_adressen"] == ["Albrechtstraße 22, 12167 Berlin"]


@pytest.mark.django_db
def test_steckbrief_faellt_auf_den_eigentuemer_zurueck(admin_client, welt, app_user):
    """Ohne Mandat ist der Eigentümer die Telefonquelle — und wird so benannt."""
    besitzer = identity_service.create_person(
        app_user.id, first_name="Hans", last_name="Müller")
    identity_service.add_contact_point(
        app_user.id, besitzer.id, contact_type="MOBILE",
        value="0170 1234567", is_primary=True,
    )
    property_service.add_party_role(
        app_user.id, property_id=welt["fremd"].id, party_id=besitzer.id,
        role="PROPERTY_OWNER", valid_from=date(2020, 1, 1),
    )
    r = admin_client.get("/api/property/properties?q=Rheinpassage")
    item = r.json()["items"][0]
    assert item["verwaltung"] is None
    assert item["eigentuemer"] == ["Hans Müller"]
    assert item["telefon"] == "0170 1234567"
    assert item["telefon_quelle"] == "Eigentümer Hans Müller"


# ===========================================================================
# 3. Der Adressabgleich: EXAKT / GEBAEUDE / STRASSE
# ===========================================================================

@pytest.mark.django_db
def test_dubletten_exakt(admin_client, welt):
    r = admin_client.get(
        f"{DUBLETTEN}?street=Albrechtstraße&house_number=30"
        f"&postal_code=12167&city=Berlin"
    )
    assert r.status_code == 200, r.content
    treffer = r.json()["treffer"]
    assert len(treffer) == 1
    assert treffer[0]["art"] == "EXAKT"
    assert treffer[0]["property"]["id"] == str(welt["weg"].id)
    # Der Steckbrief hängt auch am Abgleich — sonst wäre die Warnung anonym.
    assert treffer[0]["property"]["verwaltung"] == "Stegos Immobilien GmbH"


@pytest.mark.django_db
def test_dubletten_gebaeude(admin_client, welt):
    """**Der WEG-Fall.** Die 22 gehört zur bestehenden WEG — nicht neu anlegen."""
    r = admin_client.get(
        f"{DUBLETTEN}?street=Albrechtstraße&house_number=22&postal_code=12167"
    )
    treffer = r.json()["treffer"]
    assert [t["art"] for t in treffer] == ["GEBAEUDE"]
    assert treffer[0]["property"]["id"] == str(welt["weg"].id)
    assert "Gebäude 22" in treffer[0]["grund"]


@pytest.mark.django_db
def test_dubletten_strasse(admin_client, welt):
    """Gleiche Straße, andere Hausnummer — muss unbedingt zurückkommen."""
    r = admin_client.get(
        f"{DUBLETTEN}?street=Albrechtstraße&house_number=99&postal_code=12167"
    )
    treffer = r.json()["treffer"]
    assert [t["art"] for t in treffer] == ["STRASSE"]
    assert treffer[0]["grund"] == "Gleiche Straße, andere Hausnummer (Nr. 30)."


@pytest.mark.django_db
def test_dubletten_albrechtstr_findet_albrechtstrasse(admin_client, welt):
    """„Albrechtstr." ≡ „Albrechtstraße" — sonst ist der Abgleich richtungsabhängig."""
    for eingabe in ("Albrechtstr.", "Albrechtstr", "albrecht strasse", "ALBRECHTSTRASSE"):
        r = admin_client.get(
            f"{DUBLETTEN}?street={eingabe}&house_number=30&postal_code=12167"
        )
        assert r.status_code == 200, r.content
        treffer = r.json()["treffer"]
        assert [t["art"] for t in treffer] == ["EXAKT"], eingabe


@pytest.mark.django_db
def test_dubletten_plz_grenzt_ein(admin_client, welt):
    """Eine falsche PLZ schließt aus — sonst wäre jede Hauptstraße Deutschlands ein Treffer."""
    r = admin_client.get(f"{DUBLETTEN}?street=Albrechtstraße&postal_code=99999")
    assert r.json()["treffer"] == []
    # Ort statt PLZ: greift, wenn keine PLZ angegeben ist.
    r = admin_client.get(f"{DUBLETTEN}?street=Albrechtstraße&city=Hamburg")
    assert r.json()["treffer"] == []
    r = admin_client.get(f"{DUBLETTEN}?street=Albrechtstraße&city=Berlin")
    assert len(r.json()["treffer"]) == 1


@pytest.mark.django_db
def test_dubletten_ohne_strasse_422(admin_client, welt):
    assert admin_client.get(DUBLETTEN).status_code == 422
    assert admin_client.get(f"{DUBLETTEN}?street=%20%20").status_code == 422
    # Eine Straße, die sich zu nichts normalisiert, ist keine Straße.
    assert admin_client.get(f"{DUBLETTEN}?street=---").status_code == 422


@pytest.mark.django_db
def test_dubletten_sortierung_exakt_vor_gebaeude_vor_strasse(admin_client, welt, app_user):
    """Drei Objekte in derselben Straße, drei Arten — die Reihenfolge ist die Aussage."""
    exakt = property_service.create_property(
        app_user.id, name="Haus Albrecht 22", property_type="RENTAL_PROPERTY",
        street="Albrechtstraße", house_number="22", postal_code="12167", city="Berlin",
    )
    r = admin_client.get(
        f"{DUBLETTEN}?street=Albrechtstraße&house_number=22&postal_code=12167"
    )
    treffer = r.json()["treffer"]
    assert [t["art"] for t in treffer] == ["EXAKT", "GEBAEUDE"]
    assert treffer[0]["property"]["id"] == str(exakt.id)
    assert treffer[1]["property"]["id"] == str(welt["weg"].id)


@pytest.mark.django_db
def test_dubletten_limit(admin_client, welt, app_user):
    for i in range(5):
        property_service.create_property(
            app_user.id, name=f"Haus {i}", property_type="RENTAL_PROPERTY",
            street="Albrechtstraße", house_number=str(40 + i),
            postal_code="12167", city="Berlin",
        )
    r = admin_client.get(f"{DUBLETTEN}?street=Albrechtstraße&limit=2")
    assert len(r.json()["treffer"]) == 2
    assert admin_client.get(f"{DUBLETTEN}?street=X&limit=99").status_code == 422


# ===========================================================================
# 4. Rechte: der Abgleich ist kein Nebeneingang
# ===========================================================================

@pytest.mark.django_db
def test_dubletten_scope_eigene_begrenzt(client_with_role, welt):
    """Scope EIGENE ohne Einsatz am Objekt: **keine** Zeile — auch hier nicht.

    Sonst wäre der Abgleich das bequemste Leck: „Straße rein, Existenz raus"
    für ein Konto, dem die Liste dieselbe Liegenschaft verwehrt.
    """
    c = client_with_role("MONTEUR")
    r = c.get(f"{DUBLETTEN}?street=Albrechtstraße&postal_code=12167")
    assert r.status_code == 200, r.content
    assert r.json()["treffer"] == []
    # Gegenprobe: Die Liste zeigt ihm ebenfalls nichts.
    assert c.get("/api/property/properties").json()["items"] == []


@pytest.mark.django_db
def test_dubletten_ohne_recht_403(client_with_role, welt):
    c = client_with_role("MONTEUR", with_app_user=False)
    assert c.get(f"{DUBLETTEN}?street=Albrechtstraße").status_code == 403
    # Auch OHNE Straße: erst das Recht (403), dann die Eingabe (422). Andernfalls
    # bestätigte die Fehlermeldung die Existenz des Endpunkts für Unbefugte.
    assert c.get(DUBLETTEN).status_code == 403


@pytest.mark.django_db
def test_dubletten_ohne_login_abgelehnt(anonymous_client, welt):
    r = anonymous_client.get(f"{DUBLETTEN}?street=Albrechtstraße")
    assert r.status_code in (401, 403)


# ===========================================================================
# 5. Kontakte: Telefonsuche in fremder Schreibweise + Steckbrief
# ===========================================================================

@pytest.mark.django_db
def test_kontaktsuche_ueber_telefonnummer_andere_formatierung(admin_client, welt):
    """Getippt „030 79085327", gespeichert „+49 30 79085327" — muss trotzdem treffen."""
    for eingabe in ("3079085327", "79085327", "+49 30 79085327"):
        r = admin_client.get(f"/api/identity/parties?q={eingabe}")
        assert r.status_code == 200, r.content
        ids = {i["id"] for i in r.json()["items"]}
        assert str(welt["kontakt"].id) in ids, eingabe


@pytest.mark.django_db
def test_kontaktsuche_ueber_adresse(admin_client, welt, app_user):
    person = identity_service.create_person(
        app_user.id, first_name="Petra", last_name="Zilch")
    identity_service.add_contact_point(
        app_user.id, person.id, contact_type="EMAIL",
        value="p.zilch@example.test", is_primary=True,
    )
    identity_service.add_address(
        app_user.id, person.id, address_type="PRIVATE", street="Badensche Straße",
        house_number="53", postal_code="10825", city="Berlin", is_primary=True,
    )
    r = admin_client.get("/api/identity/parties?q=Badensche 53")
    assert {i["id"] for i in r.json()["items"]} == {str(person.id)}

    # E-Mail als Suchbegriff findet den Kontakt ebenfalls.
    r = admin_client.get("/api/identity/parties?q=p.zilch@example.test")
    assert {i["id"] for i in r.json()["items"]} == {str(person.id)}


@pytest.mark.django_db
def test_kontakt_steckbrief_zeigt_objekt_mit_rolle(admin_client, welt):
    r = admin_client.get("/api/identity/parties?q=WEG Albrechtstraße 30")
    item = next(
        i for i in r.json()["items"] if i["id"] == str(welt["weg_partei"].id)
    )
    assert item["telefon"] == "030 11110825"
    assert item["objekte"] == ["WEG Albrechtstr. (Eigentümergemeinschaft)"]


@pytest.mark.django_db
def test_kontakt_steckbrief_nennt_keine_fremden_objekte(welt, app_user):
    """**Die Objektgrenze im Steckbrief.** Sichtbarer Kontakt ≠ sichtbare Objekte.

    Der Monteur hat einen Einsatz an der Albrechtstraße; die WEG-Partei ist dort
    Eigentümerin — und zusätzlich am Kölner Objekt, an dem er nie war. Der Kontakt
    ist für ihn zu Recht sichtbar (er hängt an seinem Objekt); die Namen seiner
    **fremden** Liegenschaften darf er darüber nicht erfahren. Liegenschaftsnamen
    tragen hier die Adresse.
    """
    a = app_user.id
    # Dieselbe Partei ist auch am fremden Objekt Eigentümerin.
    property_service.add_party_role(
        a, property_id=welt["fremd"].id, party_id=welt["weg_partei"].id,
        role="PROPERTY_OWNER", valid_from=date(2020, 1, 1),
    )

    user, monteur = make_role_user("MONTEUR")
    job = einsatz_service.create_service_job(
        a, title="Begehung Heizungskeller", property_id=welt["weg"].id,
        scheduled_start=T0, scheduled_end=T1,
    )
    einsatz_service.assign_user(
        a, service_job_id=job.id, assignee_user_id=monteur.id,
    )
    c = Client()
    c.force_login(user)

    r = c.get("/api/identity/parties?q=WEG Albrechtstraße 30")
    assert r.status_code == 200, r.content
    item = next(
        i for i in r.json()["items"] if i["id"] == str(welt["weg_partei"].id)
    )
    assert item["objekte"] == ["WEG Albrechtstr. (Eigentümergemeinschaft)"]
    assert all("Rheinpassage" not in t for t in item["objekte"])

    # Gegenprobe: Mit voller Sicht stehen beide Objekte da — die Grenze ist die
    # Ursache, nicht ein fehlender Datensatz.
    admin = logged_in_client("ADMINISTRATION")
    voll = next(
        i for i in admin.get(
            "/api/identity/parties?q=WEG Albrechtstraße 30"
        ).json()["items"]
        if i["id"] == str(welt["weg_partei"].id)
    )
    assert len(voll["objekte"]) == 2


@pytest.mark.django_db
def test_kontaktsuche_nach_name_bleibt(admin_client, welt):
    r = admin_client.get("/api/identity/parties?q=Meier")
    assert {i["id"] for i in r.json()["items"]} == {str(welt["kontakt"].id)}
