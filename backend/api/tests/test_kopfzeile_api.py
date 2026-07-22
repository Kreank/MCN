"""Kopfzeile der Liegenschaft (Arbeitspaket AP2) und die Vollmacht (A-26).

Sascha: „Das sind Daten, die der Dispo schnell wissen will." Verwaltung,
Eigentümer und Mieter standen verteilt in drei Reitern — und die entscheidende
Angabe fehlte ganz: **wer bis zu welchem Betrag beauftragen darf**. Ohne sie
nimmt der Disponent einen Auftrag entgegen, den am Ende niemand bezahlen will.

`management.party_authority` liegt seit Migration 0006 in der Datenbank und war
bis zu diesem Slice von null Backend-Zeilen benutzt.
"""
from datetime import date
from decimal import Decimal

import pytest

from db_core.services import belegung as belegung_service
from db_core.services import eigentum as eigentum_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import verwaltung as verwaltung_service
from db_core.services import vollmacht as vollmacht_service


@pytest.fixture
def anlage(app_user):
    """Eine WEG mit Verwaltung, Eigentümerin, Mieterin — und einer Vollmacht."""
    a = app_user.id
    weg = identity_service.create_organization(
        a, legal_name="WEG Ahornweg 7", organization_type="WEG"
    )
    hausverwaltung = identity_service.create_organization(
        a, legal_name="Stegos Hausverwaltung", organization_type="PROPERTY_MANAGEMENT"
    )
    prop = property_service.create_property(
        a, name="Wohnanlage Ahornweg", property_type="WEG",
        street="Ahornweg", house_number="7", postal_code="10115", city="Berlin",
    )
    property_service.add_party_role(
        a, property_id=prop.id, party_id=weg.id,
        role="COMMUNITY_OF_OWNERS", valid_from=date(2020, 1, 1),
    )
    haus = property_service.add_building(
        a, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    wohnung = property_service.add_unit(
        a, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="WE 12",
    )

    mandat = verwaltung_service.create_mandat(
        a,
        property_id=prop.id,
        management_party_id=hausverwaltung.id,
        principal_party_id=weg.id,
        default_contact_party_id=hausverwaltung.id,
        mandate_type="WEG_MANAGEMENT",
        scope_type="ENTIRE_PROPERTY",
        valid_from=date(2024, 1, 1),
    )

    eigentuemerin = identity_service.create_person(
        a, first_name="Erika", last_name="Eigentum"
    )
    eigentum_service.create_stand(
        a, unit_id=wohnung.id, valid_from=date(2024, 1, 1),
        source_type="OWNER_LIST", source_reference="Eigentümerliste 2024",
        distribution_status="PARTIAL",
        eigentuemer=[{"party_id": eigentuemerin.id}],
    )

    mieterin = identity_service.create_person(
        a, first_name="Marta", last_name="Mieter"
    )
    belegung_service.create_belegung(
        a, unit_id=wohnung.id, occupancy_type="RENTED", valid_from=date(2024, 6, 1),
        mieter=[{"party_id": mieterin.id, "role": "CONTRACTUAL_TENANT"}],
    )

    return {
        "actor": a, "prop": prop, "weg": weg, "verwaltung": hausverwaltung,
        "mandat": mandat, "eigentuemerin": eigentuemerin, "mieterin": mieterin,
    }


# --- Die Vollmacht ---------------------------------------------------------

@pytest.mark.django_db
def test_vollmacht_mit_wertgrenze(anlage):
    v = vollmacht_service.create_vollmacht(
        anlage["actor"],
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["verwaltung"].id,
        authority_type="ORDER",
        valid_from=date(2024, 1, 1),
        amount_limit="5000",
        currency="EUR",
    )
    assert v.amount_limit == Decimal("5000.00")
    assert v.status == "ACTIVE"


@pytest.mark.django_db
def test_waehrung_wird_ergaenzt_statt_abgelehnt(anlage):
    """Betrag ohne Währung ist ein Bedienfehler, kein Fachfehler.

    Der CHECK verlangt beide oder keins; „5000" ohne Währung ist offensichtlich
    als Euro gemeint. Den Nutzer dafür abzuweisen wäre Pedanterie.
    """
    v = vollmacht_service.create_vollmacht(
        anlage["actor"],
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["verwaltung"].id,
        authority_type="ORDER",
        valid_from=date(2024, 1, 1),
        amount_limit="5000",
    )
    assert v.currency == "EUR"


@pytest.mark.django_db
def test_waehrung_ohne_betrag_ist_keine_aussage(anlage):
    with pytest.raises(vollmacht_service.VollmachtError, match="gehört eine Wertgrenze"):
        vollmacht_service.create_vollmacht(
            anlage["actor"],
            principal_party_id=anlage["weg"].id,
            authorized_party_id=anlage["verwaltung"].id,
            authority_type="ORDER",
            valid_from=date(2024, 1, 1),
            currency="EUR",
        )


@pytest.mark.django_db
def test_sich_selbst_bevollmaechtigen_geht_nicht(anlage):
    with pytest.raises(vollmacht_service.VollmachtError, match="verschieden"):
        vollmacht_service.create_vollmacht(
            anlage["actor"],
            principal_party_id=anlage["weg"].id,
            authorized_party_id=anlage["weg"].id,
            authority_type="ORDER",
            valid_from=date(2024, 1, 1),
        )


@pytest.mark.django_db
def test_nur_freigabebefugnis_darf_nicht_beauftragen(anlage):
    """APPROVAL heißt „ich genehmige", nicht „machen Sie mal".

    Der Unterschied entscheidet, ob der Disponent den Auftrag annehmen darf.
    """
    a = anlage["actor"]
    vollmacht_service.create_vollmacht(
        a,
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["verwaltung"].id,
        authority_type="APPROVAL",
        valid_from=date(2024, 1, 1),
    )
    auskunft = vollmacht_service.darf_beauftragen(
        anlage["prop"].id, anlage["verwaltung"].id
    )
    assert auskunft["darf"] is False
    assert auskunft["arten"] == ["APPROVAL"]


@pytest.mark.django_db
def test_auskunft_prueft_gegen_die_wertgrenze(anlage):
    a = anlage["actor"]
    vollmacht_service.create_vollmacht(
        a,
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["verwaltung"].id,
        authority_type="ORDER",
        valid_from=date(2024, 1, 1),
        amount_limit="5000",
        currency="EUR",
    )
    prop_id = anlage["prop"].id
    verwaltung_id = anlage["verwaltung"].id

    assert vollmacht_service.darf_beauftragen(prop_id, verwaltung_id)["darf"] is True
    assert (
        vollmacht_service.darf_beauftragen(prop_id, verwaltung_id, betrag="4999")["darf"]
        is True
    )
    assert (
        vollmacht_service.darf_beauftragen(prop_id, verwaltung_id, betrag="5001")["darf"]
        is False
    )


@pytest.mark.django_db
def test_widerrufene_vollmacht_zaehlt_nicht_mehr(anlage):
    a = anlage["actor"]
    v = vollmacht_service.create_vollmacht(
        a,
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["verwaltung"].id,
        authority_type="ORDER",
        valid_from=date(2024, 1, 1),
    )
    vollmacht_service.widerrufen(a, v.id)
    auskunft = vollmacht_service.darf_beauftragen(
        anlage["prop"].id, anlage["verwaltung"].id
    )
    assert auskunft["darf"] is False

    # Widerrufen heißt nicht gelöscht — der Nachweis bleibt.
    from db_core.models import PartyAuthority

    assert PartyAuthority.objects.filter(id=v.id).exists()


# --- Die Kopfzeile ---------------------------------------------------------

@pytest.mark.django_db
def test_kopfzeile_traegt_alle_vier_angaben(admin_client, anlage):
    """Verwaltung, Eigentümer, Mieter — und was die Verwaltung darf."""
    vollmacht_service.create_vollmacht(
        anlage["actor"],
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["verwaltung"].id,
        authority_type="ORDER",
        valid_from=date(2024, 1, 1),
        amount_limit="5000",
        currency="EUR",
    )
    r = admin_client.get(f"/api/property/properties/{anlage['prop'].id}/kopfzeile")
    assert r.status_code == 200, r.content
    kopf = r.json()

    assert [v["display_name"] for v in kopf["verwaltung"]] == ["Stegos Hausverwaltung"]
    assert kopf["verwaltung"][0]["befugnis"] == "bis 5.000,00 €"
    assert [e["display_name"] for e in kopf["eigentuemer"]] == ["Erika Eigentum"]
    assert [m["display_name"] for m in kopf["mieter"]] == ["Marta Mieter"]
    assert kopf["nicht_sichtbar"] == []


@pytest.mark.django_db
def test_kopfzeile_sagt_wenn_keine_vollmacht_hinterlegt_ist(admin_client, anlage):
    """Ein leeres Feld ließe offen, ob es keine Vollmacht gibt oder nur niemand
    eine eingetragen hat. Die Kopfzeile spricht es aus."""
    r = admin_client.get(f"/api/property/properties/{anlage['prop'].id}/kopfzeile")
    assert r.status_code == 200
    assert (
        r.json()["verwaltung"][0]["befugnis"] == "keine Beauftragungsvollmacht hinterlegt"
    )


@pytest.mark.django_db
def test_kopfzeile_kennzeichnet_die_notfallvollmacht(admin_client, anlage):
    """„Nur im Notfall" ist etwas anderes als „darf beauftragen".

    Wer das verwechselt, nimmt am Dienstagvormittag einen Auftrag entgegen, den
    die Verwaltung gar nicht erteilen durfte.
    """
    vollmacht_service.create_vollmacht(
        anlage["actor"],
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["verwaltung"].id,
        authority_type="EMERGENCY_ORDER",
        valid_from=date(2024, 1, 1),
        amount_limit="2000",
        currency="EUR",
    )
    r = admin_client.get(f"/api/property/properties/{anlage['prop'].id}/kopfzeile")
    assert "nur im Notfall" in r.json()["verwaltung"][0]["befugnis"]


@pytest.mark.django_db
def test_kopfzeile_traegt_die_rufnummer(admin_client, anlage):
    """Der Disponent will anrufen, nicht erst die Kontaktmappe öffnen."""
    identity_service.add_contact_point(
        anlage["actor"],
        anlage["mieterin"].id,
        contact_type="MOBILE",
        value="+49 170 1234567",
        is_primary=True,
    )
    r = admin_client.get(f"/api/property/properties/{anlage['prop'].id}/kopfzeile")
    assert r.json()["mieter"][0]["telefon"] == "+49 170 1234567"
