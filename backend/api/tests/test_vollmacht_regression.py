"""Regressionstests zur Beauftragungsvollmacht (AP2).

Diese Fälle stammen aus dem Review des ersten Anlaufs. Beide Befunde waren
keine Randfälle: Sie gaben dem Disponenten eine **falsche Antwort auf genau die
Frage**, wegen der es das Arbeitspaket gibt — „darf die Verwaltung das, und bis
zu welchem Betrag?".

* Eine Vollmacht, die für ein **anderes Objekt** erteilt wurde, erschien hier.
* Die **Notfallgrenze** wurde als Alltagsgrenze ausgegeben.
"""
from datetime import date
from decimal import Decimal

import pytest

from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import verwaltung as verwaltung_service
from db_core.services import vollmacht as vollmacht_service


@pytest.fixture
def anlage(app_user):
    """Eine WEG mit Verwaltungsmandat — die Ausgangslage beider Befunde."""
    a = app_user.id
    weg = identity_service.create_organization(
        a, legal_name="WEG Ahornweg 7", organization_type="WEG"
    )
    hv = identity_service.create_organization(
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
    verwaltung_service.create_mandat(
        a,
        property_id=prop.id,
        management_party_id=hv.id,
        principal_party_id=weg.id,
        default_contact_party_id=hv.id,
        mandate_type="WEG_MANAGEMENT",
        scope_type="ENTIRE_PROPERTY",
        valid_from=date(2024, 1, 1),
    )
    return {"actor": a, "prop": prop, "weg": weg, "hv": hv}


# --- Befund 1: fremde Vollmachten -------------------------------------------

@pytest.mark.django_db
def test_fremde_mandatsvollmacht_taucht_hier_nicht_auf(anlage):
    """Eine Vollmacht für ein ANDERES Objekt gehört nicht an diese Liegenschaft.

    Der erste Anlauf verodert „Mandat dieser Liegenschaft" mit „Vollmachtgeber
    hat hier eine Rolle" — ohne den Geltungsbereich zu prüfen. Eine
    mandatsgebundene Vollmacht für Liegenschaft B erschien damit an A, sobald
    die WEG an A irgendeine Rolle hatte. Der Disponent las dort „darf bis
    99.000 € beauftragen".
    """
    a = anlage["actor"]
    fremde = property_service.create_property(
        a, name="Ganz anderes Objekt", property_type="WEG",
        street="Anderswo", postal_code="20095", city="Hamburg",
    )
    fremde_hv = identity_service.create_organization(
        a, legal_name="Fremdverwaltung GmbH", organization_type="PROPERTY_MANAGEMENT"
    )
    fremdes_mandat = verwaltung_service.create_mandat(
        a,
        property_id=fremde.id,
        management_party_id=fremde_hv.id,
        # DIESELBE WEG wie an unserer Anlage — das ist der Hebel des Lecks.
        principal_party_id=anlage["weg"].id,
        default_contact_party_id=fremde_hv.id,
        mandate_type="WEG_MANAGEMENT",
        scope_type="ENTIRE_PROPERTY",
        valid_from=date(2024, 1, 1),
    )
    vollmacht_service.create_vollmacht(
        a,
        principal_party_id=anlage["weg"].id,
        authorized_party_id=fremde_hv.id,
        authority_type="ORDER",
        scope_type="MANDATE",
        mandate_id=fremdes_mandat.id,
        valid_from=date(2024, 1, 1),
        amount_limit="99000",
        currency="EUR",
    )

    an_unserer = list(
        vollmacht_service.vollmachten_der_liegenschaft(anlage["prop"].id)
    )
    assert all(v.authorized_party_id != fremde_hv.id for v in an_unserer), (
        "Eine Vollmacht für ein anderes Objekt darf hier nicht erscheinen"
    )
    assert (
        vollmacht_service.darf_beauftragen(anlage["prop"].id, fremde_hv.id)["darf"]
        is False
    )
    # Gegenprobe: Am richtigen Objekt gilt sie sehr wohl.
    assert (
        vollmacht_service.darf_beauftragen(fremde.id, fremde_hv.id)["darf"] is True
    )


@pytest.mark.django_db
def test_beendete_rolle_zieht_keine_vollmacht_mehr_her(anlage):
    """Eine 2016 beendete Eigentümerrolle darf keine Vollmacht mehr anziehen."""
    a = anlage["actor"]
    altobjekt = property_service.create_property(
        a, name="Altobjekt", property_type="WEG",
        street="Früherweg", postal_code="10115", city="Berlin",
    )
    ehemalige = identity_service.create_organization(
        a, legal_name="Ehemalige WEG", organization_type="WEG"
    )
    property_service.add_party_role(
        a, property_id=altobjekt.id, party_id=ehemalige.id,
        role="COMMUNITY_OF_OWNERS",
        valid_from=date(2015, 1, 1), valid_until=date(2016, 1, 1),
    )
    beauftragte = identity_service.create_organization(
        a, legal_name="Damalige Verwaltung", organization_type="PROPERTY_MANAGEMENT"
    )
    vollmacht_service.create_vollmacht(
        a,
        principal_party_id=ehemalige.id,
        authorized_party_id=beauftragte.id,
        authority_type="ORDER",
        valid_from=date(2015, 1, 1),
        amount_limit="50000",
        currency="EUR",
    )

    assert list(vollmacht_service.vollmachten_der_liegenschaft(altobjekt.id)) == [], (
        "Eine beendete Rolle darf die Vollmacht nicht mehr an das Objekt binden"
    )


# --- Befund 2: Notfall wird zur Alltagsgrenze -------------------------------

@pytest.mark.django_db
def test_notfallgrenze_gilt_nicht_im_alltag(anlage):
    """ORDER bis 5.000 € + EMERGENCY_ORDER bis 50.000 € — der Alltag bleibt 5.000.

    Der erste Anlauf nahm das Maximum über beide Arten. `darf_beauftragen`
    antwortete damit auf 20.000 € mit `True`, obwohl am Dienstagvormittag nur
    5.000 € gedeckt sind — grünes Licht für einen ungedeckten Auftrag.
    """
    a = anlage["actor"]
    for art, betrag in (("ORDER", "5000"), ("EMERGENCY_ORDER", "50000")):
        vollmacht_service.create_vollmacht(
            a,
            principal_party_id=anlage["weg"].id,
            authorized_party_id=anlage["hv"].id,
            authority_type=art,
            valid_from=date(2024, 1, 1),
            amount_limit=betrag,
            currency="EUR",
        )

    prop_id = anlage["prop"].id
    hv = anlage["hv"].id
    auskunft = vollmacht_service.darf_beauftragen(prop_id, hv)

    assert auskunft["grenze"] == Decimal("5000.00"), "Alltagsgrenze, nicht Notfall"
    assert auskunft["notfall_grenze"] == Decimal("50000.00")
    assert auskunft["nur_notfall"] is False, "sie darf auch im Alltag"
    assert (
        vollmacht_service.darf_beauftragen(prop_id, hv, betrag="20000")["darf"] is False
    ), "20.000 € sind im Alltag nicht gedeckt"
    assert (
        vollmacht_service.darf_beauftragen(prop_id, hv, betrag="4000")["darf"] is True
    )


@pytest.mark.django_db
def test_kopfzeile_nennt_alltag_und_notfall_getrennt(admin_client, anlage):
    a = anlage["actor"]
    for art, betrag in (("ORDER", "5000"), ("EMERGENCY_ORDER", "50000")):
        vollmacht_service.create_vollmacht(
            a,
            principal_party_id=anlage["weg"].id,
            authorized_party_id=anlage["hv"].id,
            authority_type=art,
            valid_from=date(2024, 1, 1),
            amount_limit=betrag,
            currency="EUR",
        )
    r = admin_client.get(f"/api/property/properties/{anlage['prop'].id}/kopfzeile")
    befugnis = r.json()["verwaltung"][0]["befugnis"]
    assert "bis 5.000,00" in befugnis
    assert "im Notfall bis 50.000,00" in befugnis
    assert not befugnis.startswith("nur im Notfall")


@pytest.mark.django_db
def test_nur_notfallvollmacht_wird_als_solche_gekennzeichnet(admin_client, anlage):
    vollmacht_service.create_vollmacht(
        anlage["actor"],
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["hv"].id,
        authority_type="EMERGENCY_ORDER",
        valid_from=date(2024, 1, 1),
        amount_limit="2000",
        currency="EUR",
    )
    r = admin_client.get(f"/api/property/properties/{anlage['prop'].id}/kopfzeile")
    assert r.json()["verwaltung"][0]["befugnis"].startswith("nur im Notfall")


# --- Währung ----------------------------------------------------------------

@pytest.mark.django_db
def test_fremdwaehrung_wird_nicht_zu_euro(admin_client, anlage):
    """5.000 CHF sind keine 5.000 €."""
    vollmacht_service.create_vollmacht(
        anlage["actor"],
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["hv"].id,
        authority_type="ORDER",
        valid_from=date(2024, 1, 1),
        amount_limit="5000",
        currency="CHF",
    )
    r = admin_client.get(f"/api/property/properties/{anlage['prop'].id}/kopfzeile")
    assert "CHF" in r.json()["verwaltung"][0]["befugnis"]


@pytest.mark.django_db
def test_gemischte_waehrungen_werden_nicht_verglichen(anlage):
    """4.000 EUR und 5.000 CHF numerisch zu vergleichen wäre erfunden."""
    a = anlage["actor"]
    for betrag, waehrung in (("4000", "EUR"), ("5000", "CHF")):
        zweite = identity_service.create_organization(
            a, legal_name=f"HV {waehrung}", organization_type="PROPERTY_MANAGEMENT"
        )
        vollmacht_service.create_vollmacht(
            a,
            principal_party_id=anlage["weg"].id,
            authorized_party_id=(anlage["hv"].id if waehrung == "EUR" else zweite.id),
            authority_type="ORDER",
            valid_from=date(2024, 1, 1),
            amount_limit=betrag,
            currency=waehrung,
        )
    # Beide Parteien getrennt — je eine Währung, keine Vermischung.
    auskunft = vollmacht_service.darf_beauftragen(anlage["prop"].id, anlage["hv"].id)
    assert auskunft["grenze"] == Decimal("4000.00")
    assert auskunft["waehrung"] == "EUR"


# --- Rechtefilterung der Kopfzeile ------------------------------------------

@pytest.mark.django_db
def test_buchhaltung_sieht_verwaltung_und_mieter_nicht(client_with_role, anlage):
    """Die Rechtefilterung POSITIV festgenagelt, nicht bedingt.

    Der erste Anlauf prüfte die Bedingung nur, WENN sie eintrat — er wäre auch
    grün geblieben, wenn die Filterung gar nicht gegriffen hätte.

    BUCHHALTUNG trägt `property/LESEN`, aber weder `management/LESEN` noch
    `tenure/LESEN`.
    """
    c = client_with_role("BUCHHALTUNG")
    r = c.get(f"/api/property/properties/{anlage['prop'].id}/kopfzeile")
    assert r.status_code == 200, r.content
    kopf = r.json()

    assert set(kopf["nicht_sichtbar"]) == {"Verwaltung", "Eigentümer und Mieter"}
    assert kopf["verwaltung"] == []
    assert kopf["eigentuemer"] == []
    assert kopf["mieter"] == []


@pytest.mark.django_db
def test_zukunftsdatierte_vollmacht_laesst_sich_widerrufen(anlage):
    """Eine noch nicht begonnene Vollmacht muss zurücknehmbar sein.

    Der erste Anlauf lehnte den Widerruf ab („kann nicht vor dem Beginn
    liegen") — man hätte warten müssen, bis sie gilt, um sie loszuwerden.
    """
    from datetime import timedelta

    a = anlage["actor"]
    beginn = date.today() + timedelta(days=30)
    v = vollmacht_service.create_vollmacht(
        a,
        principal_party_id=anlage["weg"].id,
        authorized_party_id=anlage["hv"].id,
        authority_type="ORDER",
        valid_from=beginn,
    )
    widerrufen = vollmacht_service.widerrufen(a, v.id)
    assert widerrufen.status == "REVOKED"
    assert widerrufen.valid_until == beginn + timedelta(days=1)

    # Und sie gilt auch am Beginn nicht mehr — der Status entscheidet.
    assert (
        vollmacht_service.darf_beauftragen(
            anlage["prop"].id, anlage["hv"].id, stichtag=beginn
        )["darf"]
        is False
    )
