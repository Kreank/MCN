"""Der Kontakt-Steckbrief — Auskunft ja, fremde Objektnamen nein.

Drei Zeilen „Meier" im Kontakt-Picker sind keine Auskunft; Telefon, E-Mail,
Adresse und die Objekte mit Rolle beantworten „ist das der, den ich suche?".
Diese Datei hält beides fest: dass die Auswahl der Kontaktwege stimmt — **und**
dass das Feld `objekte` die Objektgrenze einhält.

**Der zweite Teil ist der wichtigere.** `objekte` ist das einzige Feld des
Steckbriefs, das nicht über die Party spricht, sondern über **Liegenschaften**.
Der Aufrufer (`api/identity.py::list_parties`) begrenzt die *Parties* — ein
Kontakt an meinem Objekt ist damit zu Recht sichtbar. Ungefiltert brächte er
aber die Namen **aller** seiner Objekte mit, und Liegenschaftsnamen tragen in
diesem Datenmodell regelmäßig die Adresse. Das ist genau das Leck, das
`services/objektsicht.py` ausschließt.
"""
import uuid
from datetime import date, datetime, timezone as dt_timezone

import pytest

from db_core.models import AppUser
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import kontakt_steckbrief
from db_core.services import property as property_service

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)

#: Die Abfragen aus `kontakt_steckbrief.steckbriefe`: Kontaktwege, Adressen, Rollen.
ERWARTETE_ABFRAGEN = 3

# Der Regelfall in den Tests: volle Sicht. Wo die Grenze geprüft wird, steht
# EIGENE ausdrücklich da.
ALLE = {"scope": "ALLE", "actor": None}


def _monteur_app_user(name="Mika Monteur"):
    """Der Akteur der Objektsicht ist ein `security.app_user`, keine Party."""
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=name, status="ACTIVE", version=1)


def _objekt(actor, *, name, hausnummer):
    return property_service.create_property(
        actor, name=name, property_type="WEG",
        street="Albrechtstraße", house_number=hausnummer,
        postal_code="12167", city="Berlin",
    )


# ===========================================================================
# 1. Die Auskunft selbst
# ===========================================================================

@pytest.mark.django_db
def test_telefon_email_und_adresse(app_user):
    """Primärer Kontaktweg je Art, dazu die primäre Adresse als Zeile."""
    a = app_user.id
    person = identity_service.create_person(a, first_name="Rita", last_name="Meier")
    # Der NICHT-primäre Weg steht zuerst — die Sortierung, nicht die Reihenfolge
    # der Anlage, muss entscheiden.
    identity_service.add_contact_point(
        a, person.id, contact_type="PHONE", value="030 0000000", is_primary=False)
    identity_service.add_contact_point(
        a, person.id, contact_type="PHONE", value="+49 30 79085327", is_primary=True)
    identity_service.add_contact_point(
        a, person.id, contact_type="EMAIL", value="r.meier@example.test",
        is_primary=True)
    identity_service.add_address(
        a, person.id, address_type="PRIVATE", street="Badensche Straße",
        house_number="53", postal_code="10825", city="Berlin", is_primary=True)

    brief = kontakt_steckbrief.steckbriefe([person.id], **ALLE)[person.id]
    assert brief.telefon == "+49 30 79085327"
    assert brief.email == "r.meier@example.test"
    assert brief.address_line == "Badensche Straße 53, 10825 Berlin"
    assert brief.objekte == []


@pytest.mark.django_db
def test_mobil_zaehlt_als_telefon(app_user):
    """MOBILE ist ein Telefon — wer nur PHONE prüft, zeigt bei Handynummern nichts."""
    a = app_user.id
    person = identity_service.create_person(a, first_name="Hans", last_name="Müller")
    identity_service.add_contact_point(
        a, person.id, contact_type="MOBILE", value="0170 1234567", is_primary=True)
    brief = kontakt_steckbrief.steckbriefe([person.id], **ALLE)[person.id]
    assert brief.telefon == "0170 1234567"


@pytest.mark.django_db
def test_kontaktweg_vor_gueltigkeitsbeginn_zaehlt_nicht(app_user):
    """Ein Kontaktweg, der erst später gilt, ist am Stichtag keine Auskunft.

    (Die obere, **exklusive** Grenze prüft `test_property_steckbrief.py` an der
    Rolle — `gilt()` ist dieselbe Funktion für beide Steckbriefe.)
    """
    a = app_user.id
    person = identity_service.create_person(a, first_name="Alt", last_name="Nummer")
    identity_service.add_contact_point(
        a, person.id, contact_type="PHONE", value="030 111", is_primary=True,
        valid_from=date(2020, 1, 1))
    brief = kontakt_steckbrief.steckbriefe(
        [person.id], stichtag=date(2019, 12, 31), **ALLE)[person.id]
    assert brief.telefon is None


@pytest.mark.django_db
def test_objekte_tragen_das_rollenlabel(app_user):
    """„WEG Albrechtstr. (Eigentümergemeinschaft)" — Name ohne Rolle sagt zu wenig."""
    a = app_user.id
    weg = identity_service.create_organization(
        a, legal_name="WEG Albrechtstraße 30", organization_type="WEG")
    obj = _objekt(a, name="WEG Albrechtstr.", hausnummer="30")
    property_service.add_party_role(
        a, property_id=obj.id, party_id=weg.id,
        role="COMMUNITY_OF_OWNERS", valid_from=date(2020, 1, 1))

    brief = kontakt_steckbrief.steckbriefe([weg.id], **ALLE)[weg.id]
    assert brief.objekte == ["WEG Albrechtstr. (Eigentümergemeinschaft)"]


@pytest.mark.django_db
def test_abfragezahl_bleibt_konstant(app_user, django_assert_num_queries):
    """Drei Abfragen für einen Kontakt, drei für zehn — der Picker feuert je Tastendruck."""
    a = app_user.id
    parties = []
    for i in range(10):
        p = identity_service.create_person(a, first_name="Kontakt", last_name=f"Nr{i}")
        identity_service.add_contact_point(
            a, p.id, contact_type="PHONE", value=f"030 {i}0000", is_primary=True)
        identity_service.add_address(
            a, p.id, address_type="PRIVATE", street="Teststraße",
            house_number=str(i), postal_code="10115", city="Berlin", is_primary=True)
        obj = _objekt(a, name=f"Objekt {i}", hausnummer=str(i))
        property_service.add_party_role(
            a, property_id=obj.id, party_id=p.id,
            role="PROPERTY_OWNER", valid_from=date(2020, 1, 1))
        parties.append(p)

    with django_assert_num_queries(ERWARTETE_ABFRAGEN):
        kontakt_steckbrief.steckbriefe([parties[0].id], **ALLE)
    with django_assert_num_queries(ERWARTETE_ABFRAGEN):
        briefe = kontakt_steckbrief.steckbriefe([p.id for p in parties], **ALLE)
    assert len(briefe) == 10
    assert all(briefe[p.id].telefon for p in parties)


@pytest.mark.django_db
def test_leere_eingabe_fragt_gar_nicht(db, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert kontakt_steckbrief.steckbriefe([], **ALLE) == {}


# ===========================================================================
# 2. Die Objektgrenze — das Feld `objekte` verrät keine fremden Liegenschaften
# ===========================================================================

@pytest.fixture
def monteur_welt(app_user):
    """Monteur mit Einsatz an A. Eine Party ist Eigentümerin an A **und** an B.

    B ist für den Monteur nicht vorhanden — er war dort nie. Dass die Party
    sichtbar ist (sie hängt an A), macht B nicht sichtbar.
    """
    a = app_user.id
    monteur_user = _monteur_app_user()

    eigner = identity_service.create_organization(
        a, legal_name="Sammel-Eigentümerin GmbH", organization_type="COMPANY")

    objekt_a = _objekt(a, name="Albrechtstraße 30 (mein Objekt)", hausnummer="30")
    objekt_b = _objekt(a, name="Rheinstraße 9 (fremdes Objekt)", hausnummer="9")
    for obj in (objekt_a, objekt_b):
        property_service.add_party_role(
            a, property_id=obj.id, party_id=eigner.id,
            role="PROPERTY_OWNER", valid_from=date(2020, 1, 1))

    # Der Einsatz an A — der freie Termin trägt die Liegenschaft direkt.
    job = einsatz_service.create_service_job(
        a, title="Begehung Heizungskeller", property_id=objekt_a.id,
        scheduled_start=T0, scheduled_end=T1)
    einsatz_service.assign_user(
        a, service_job_id=job.id, assignee_user_id=monteur_user.id)

    return {
        "monteur_user": monteur_user, "eigner": eigner,
        "objekt_a": objekt_a, "objekt_b": objekt_b,
    }


@pytest.mark.django_db
def test_objekte_zeigen_bei_scope_eigene_nur_eigene_objekte(monteur_welt):
    """**Der Befund.** A ja, B niemals — auch nicht als Name in einer Liste.

    Ohne diese Begrenzung liefert die Kontaktsuche dem Monteur den Namen einer
    Liegenschaft, an der er nie war. Der Name enthält hier — wie im echten
    Bestand — die Adresse.
    """
    eigner = monteur_welt["eigner"]
    brief = kontakt_steckbrief.steckbriefe(
        [eigner.id], scope="EIGENE", actor=monteur_welt["monteur_user"].id,
    )[eigner.id]

    assert brief.objekte == ["Albrechtstraße 30 (mein Objekt) (Eigentümer)"]
    assert all("Rheinstraße" not in t for t in brief.objekte)


@pytest.mark.django_db
def test_scope_alle_sieht_beide_objekte(monteur_welt):
    """Gegenprobe: Ohne Objektgrenze stehen beide da — der Filter ist die Ursache."""
    eigner = monteur_welt["eigner"]
    brief = kontakt_steckbrief.steckbriefe([eigner.id], **ALLE)[eigner.id]
    assert len(brief.objekte) == 2
    assert any("Rheinstraße" in t for t in brief.objekte)


@pytest.mark.django_db
def test_scope_eigene_ohne_akteur_zeigt_kein_objekt(monteur_welt):
    """Fail-closed: Ein Konto ohne `app_user` hat keine eigenen Objekte — also keine."""
    eigner = monteur_welt["eigner"]
    brief = kontakt_steckbrief.steckbriefe(
        [eigner.id], scope="EIGENE", actor=None)[eigner.id]
    assert brief.objekte == []


@pytest.mark.django_db
def test_fremde_objekte_verdraengen_die_eigenen_nicht(app_user):
    """`MAX_OBJEKTE` greift NACH der Grenze — sonst kappen fremde Objekte die eigenen.

    Die Kappung sortiert nach `-valid_from`. Läge sie vor der Begrenzung, füllten
    hier drei **jüngere** fremde Rollen die Liste, und das eine eigene Objekt —
    das einzige, das der Monteur sehen darf und sehen soll — fiele heraus.
    """
    a = app_user.id
    monteur_user = _monteur_app_user()
    eigner = identity_service.create_organization(
        a, legal_name="Sammel-Eigentümerin GmbH", organization_type="COMPANY")

    mein = _objekt(a, name="Mein Objekt", hausnummer="30")
    property_service.add_party_role(
        a, property_id=mein.id, party_id=eigner.id,
        role="PROPERTY_OWNER", valid_from=date(2020, 1, 1))
    # Vier JÜNGERE fremde Rollen — mehr als MAX_OBJEKTE.
    for i in range(4):
        fremd = _objekt(a, name=f"Fremdes Objekt {i}", hausnummer=str(40 + i))
        property_service.add_party_role(
            a, property_id=fremd.id, party_id=eigner.id,
            role="PROPERTY_OWNER", valid_from=date(2024, 1, 1))

    job = einsatz_service.create_service_job(
        a, title="Begehung", property_id=mein.id,
        scheduled_start=T0, scheduled_end=T1)
    einsatz_service.assign_user(
        a, service_job_id=job.id, assignee_user_id=monteur_user.id)

    brief = kontakt_steckbrief.steckbriefe(
        [eigner.id], scope="EIGENE", actor=monteur_user.id)[eigner.id]
    assert brief.objekte == ["Mein Objekt (Eigentümer)"]
