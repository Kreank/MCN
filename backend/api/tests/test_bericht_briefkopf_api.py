"""Briefkopf des Baustellenberichts (Befund B3/B8, Runde 2).

Der Bericht kannte seinen Auftrag nur als UUID. Im PDF stand „Auftrag: <Titel>"
und „Objekt: <Name · Stadt>" — kein Auftraggeber, keine Auftragsnummer, keine
Straße, kein Mieter, keine Wohnungsnummer, kein Eigentümer. Sascha zum Bericht:
„halt das übliche Briefkopf-Gedöns", genau wie bei Angebot und Rechnung.

Alle Angaben lagen im Datenmodell und in fertigen Services bereit; sie waren nur
nie verdrahtet.

**Ausdrücklich nicht Gegenstand:** Preise. Der Bericht führt keine (Migration
0080) — daran ändert der Briefkopf nichts.
"""
from datetime import date

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import belegung as belegung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service


@pytest.fixture
def baustelle(app_user):
    """Eine WEG mit Gebäude, Wohnung im 3. OG, Mieterin, Eigentümer und Auftrag."""
    a = app_user.id
    eigentuemer = identity_service.create_organization(
        a, legal_name="WEG Ahornweg 7", organization_type="WEG"
    )
    kunde = identity_service.create_organization(
        a, legal_name="Hausverwaltung Nord GmbH", organization_type="PROPERTY_MANAGEMENT"
    )
    identity_service.add_address(
        a, kunde.id, address_type="BUSINESS",
        street="Verwalterweg", house_number="1", postal_code="10115", city="Berlin",
    )
    prop = property_service.create_property(
        a, name="Wohnanlage Ahornweg", property_type="WEG",
        street="Ahornweg", house_number="7", postal_code="10115", city="Berlin",
    )
    property_service.add_party_role(
        a, property_id=prop.id, party_id=eigentuemer.id,
        role="COMMUNITY_OF_OWNERS", valid_from=date(2020, 1, 1),
    )
    haus = property_service.add_building(
        a, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    wohnung = property_service.add_unit(
        a, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="WE 12", storey="3. OG",
    )
    mieterin = identity_service.create_person(a, first_name="Erika", last_name="Meyer")
    belegung_service.create_belegung(
        a, unit_id=wohnung.id, occupancy_type="RENTED", valid_from=date(2024, 1, 1),
        mieter=[{"party_id": mieterin.id, "role": "CONTRACTUAL_TENANT"}],
    )
    auftrag = auftrag_service.create_work_order(
        a, property_id=prop.id, title="Heizung tropft",
        building_id=haus.id, unit_id=wohnung.id,
    )
    auftrag_service.add_work_order_party(
        a, work_order_id=auftrag.id, party_id=kunde.id, role="PRINCIPAL",
        is_primary=True,
    )
    bericht = report_service.create_report(
        a, work_order_id=auftrag.id, service_job_id=None,
        report_date=date(2026, 7, 21), activity_text="Thermostat getauscht.",
    )
    return {"actor": app_user, "prop": prop, "auftrag": auftrag, "bericht": bericht}


@pytest.mark.django_db
def test_briefkopf_traegt_alles_was_drauf_gehoert(admin_client, baustelle):
    r = admin_client.get(f"/api/workflow/site_reports/{baustelle['bericht'].id}")
    assert r.status_code == 200, r.content
    kopf = r.json()["kopf"]
    assert kopf is not None, "Der Detail-Endpunkt muss den Briefkopf liefern"

    assert kopf["order_number"].startswith("AU-")
    assert kopf["order_title"] == "Heizung tropft"
    assert kopf["auftraggeber"] == "Hausverwaltung Nord GmbH"
    assert "Verwalterweg 1" in kopf["auftraggeber_adresse"]
    assert kopf["objekt_name"] == "Wohnanlage Ahornweg"
    assert "Ahornweg 7" in kopf["objekt_adresse"]
    assert kopf["gebaeude"] == "Vorderhaus"
    assert kopf["einheit"] == "WE 12"
    assert kopf["etage"] == "3. OG"
    assert kopf["mieter"] == ["Erika Meyer"]
    assert kopf["eigentuemer"] == ["WEG Ahornweg 7"]


@pytest.mark.django_db
def test_liste_traegt_den_briefkopf_nicht(admin_client, baustelle):
    """Bewusst: In einer Liste mit dreißig Berichten wäre er ein N+1 für
    Angaben, die dort niemand liest."""
    r = admin_client.get(
        f"/api/workflow/site_reports?work_order_id={baustelle['auftrag'].id}"
    )
    assert r.status_code == 200
    assert r.json()["items"][0]["kopf"] is None


@pytest.mark.django_db
def test_briefkopf_am_freien_termin_bleibt_leer_statt_zu_raten(admin_client, app_user):
    """Ein Begehungsprotokoll hat keinen Auftrag (work_order_id ist seit 0064
    nullable) — dann gibt es weder Auftraggeber noch Auftragsnummer.

    Leer heißt „gibt es nicht". Erfunden wird nichts.
    """
    from db_core.services import einsatz as einsatz_service

    prop = property_service.create_property(
        app_user.id, name="Freies Objekt", property_type="EINFAMILIENHAUS",
        street="Feldweg", house_number="3", postal_code="10115", city="Berlin",
    )
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=None, title="Begehung", property_id=prop.id,
    )
    bericht = report_service.create_report(
        app_user.id, work_order_id=None, service_job_id=job.id,
        report_date=date(2026, 7, 21), activity_text="Begehung durchgeführt.",
    )

    r = admin_client.get(f"/api/workflow/site_reports/{bericht.id}")
    assert r.status_code == 200, r.content
    kopf = r.json()["kopf"]
    assert kopf["order_number"] is None
    assert kopf["auftraggeber"] is None
    # Die Liegenschaft kommt in dem Fall über den Einsatz.
    assert kopf["objekt_name"] == "Freies Objekt"
    assert kopf["mieter"] == []


@pytest.mark.django_db
def test_briefkopf_ohne_einheit_hat_keinen_mieter(admin_client, app_user):
    """Auftrag am Gemeinschaftseigentum: keine Wohnung, also kein Mieter."""
    a = app_user.id
    prop = property_service.create_property(
        a, name="Gemeinschaftsobjekt", property_type="WEG",
        street="Ahornweg", house_number="7", postal_code="10115", city="Berlin",
    )
    auftrag = auftrag_service.create_work_order(
        a, property_id=prop.id, title="Treppenhaus streichen"
    )
    bericht = report_service.create_report(
        a, work_order_id=auftrag.id, service_job_id=None,
        report_date=date(2026, 7, 21), activity_text="Gestrichen.",
    )

    r = admin_client.get(f"/api/workflow/site_reports/{bericht.id}")
    assert r.status_code == 200, r.content
    kopf = r.json()["kopf"]
    assert kopf["einheit"] is None
    assert kopf["etage"] is None
    assert kopf["mieter"] == []
    assert kopf["objekt_name"] == "Gemeinschaftsobjekt"


@pytest.mark.django_db
def test_pdf_traegt_den_briefkopf(admin_client, baustelle):
    """Das PDF ist der Ort, an dem der Briefkopf am meisten gefehlt hat."""
    r = admin_client.get(
        f"/api/workflow/site_reports/{baustelle['bericht'].id}/pdf"
    )
    assert r.status_code == 200, r.content
    assert r["Content-Type"] == "application/pdf"
    assert len(r.content) > 1000
