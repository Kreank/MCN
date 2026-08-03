"""Automatische Nummernvergabe (Migration 0149).

Geprüft wird das Versprechen der Masken: „Leer lassen genügt." Dazu gehört
beides — dass eine leere Nummer gefüllt wird UND dass eine eingetragene
unangetastet bleibt. Der zweite Teil ist der wichtigere: Alle DATANORM- und
IDS-Importe geben ihre Nummern selbst vor.
"""
import pytest

from db_core.services import artikel as artikel_service
from db_core.services import property as property_service


# --- Artikel ---------------------------------------------------------------
@pytest.mark.django_db
def test_artikel_ohne_nummer_bekommt_art_nummer(app_user):
    a = artikel_service.create_article(
        app_user.id, description="Dachziegel", unit="Stk",
    )
    assert a.article_number.startswith("ART-")
    # Fünfstellig gepolstert — die Nummer soll in einer Liste ausgerichtet stehen.
    assert len(a.article_number) == len("ART-00001")


@pytest.mark.django_db
def test_artikel_nummern_laufen_hoch(app_user):
    erste = artikel_service.create_article(
        app_user.id, description="Erster", unit="Stk",
    )
    zweite = artikel_service.create_article(
        app_user.id, description="Zweiter", unit="Stk",
    )
    assert erste.article_number != zweite.article_number
    assert int(zweite.article_number[4:]) == int(erste.article_number[4:]) + 1


@pytest.mark.django_db
def test_eigene_artikelnummer_bleibt(app_user):
    """Der Import-Pfad: eine gesetzte Nummer ist die Nummer."""
    a = artikel_service.create_article(
        app_user.id, article_number="DN-GUT-4711", description="Ziegel", unit="Stk",
    )
    assert a.article_number == "DN-GUT-4711"


@pytest.mark.django_db
def test_automatische_nummer_weicht_belegter_aus(app_user):
    """Von Hand vergebene ART-Nummern dürfen den Zähler nicht blockieren.

    Ohne die Freiheitsprüfung im Trigger liefe der Zähler in die belegte Nummer
    und der Anlegevorgang bräche mit einem UNIQUE-Verstoß ab — ausgerechnet dem
    Fehler, den die Automatik abschaffen soll.
    """
    belegt = artikel_service.create_article(
        app_user.id, description="Platzhalter", unit="Stk",
    )
    naechste = f"ART-{int(belegt.article_number[4:]) + 1:05d}"
    artikel_service.create_article(
        app_user.id, article_number=naechste, description="Von Hand", unit="Stk",
    )
    weiter = artikel_service.create_article(
        app_user.id, description="Danach", unit="Stk",
    )
    assert weiter.article_number != naechste


@pytest.mark.django_db
def test_artikel_anlegen_ohne_nummer_ueber_api(admin_client):
    r = admin_client.post(
        "/api/pricing/articles",
        data={"description": "Ohne Nummer", "unit": "Stk"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["article_number"].startswith("ART-")


@pytest.mark.django_db
def test_artikel_kopieren_ohne_nummer(admin_client, app_user):
    quelle = artikel_service.create_article(
        app_user.id, article_number="QUELLE-1", description="Vorlage", unit="Stk",
    )
    r = admin_client.post(
        f"/api/pricing/articles/{quelle.id}/copy",
        data={},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["article_number"].startswith("ART-")


# --- Leistungen ------------------------------------------------------------
@pytest.mark.django_db
def test_leistung_ohne_nummer_bekommt_lei_nummer(app_user):
    a = artikel_service.create_assembly(
        app_user.id, name="Ziegel verlegen", unit="m²",
    )
    assert a.assembly_number.startswith("LEI-")


@pytest.mark.django_db
def test_eigene_leistungsnummer_bleibt(app_user):
    a = artikel_service.create_assembly(
        app_user.id, assembly_number="WARTUNG-01", name="Wartung", unit="Pauschal",
    )
    assert a.assembly_number == "WARTUNG-01"


# --- Gebäude und Einheiten -------------------------------------------------
@pytest.fixture
def objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Nummernobjekt", property_type="WEG",
        street="Zählerweg", house_number="1", postal_code="10115", city="Berlin",
    )


@pytest.mark.django_db
def test_gebaeude_zaehlen_je_liegenschaft_hoch(app_user, objekt):
    erstes = property_service.add_building(app_user.id, property_id=objekt.id)
    zweites = property_service.add_building(app_user.id, property_id=objekt.id)
    assert erstes.building_number == "1"
    assert zweites.building_number == "2"


@pytest.mark.django_db
def test_zwei_liegenschaften_zaehlen_unabhaengig(app_user, objekt):
    anderes = property_service.create_property(
        app_user.id, name="Zweitobjekt", property_type="WEG",
        street="Anderswo", house_number="9", postal_code="20095", city="Hamburg",
    )
    property_service.add_building(app_user.id, property_id=objekt.id)
    erstes_dort = property_service.add_building(app_user.id, property_id=anderes.id)
    # Der Zähler gehört der Liegenschaft, nicht dem System.
    assert erstes_dort.building_number == "1"


@pytest.mark.django_db
def test_sprechende_gebaeudenummer_bleibt(app_user, objekt):
    """„Hinterhaus" ist keine Nummer, die man hochzählt — sie bleibt stehen."""
    haus = property_service.add_building(
        app_user.id, property_id=objekt.id, building_number="Hinterhaus",
    )
    assert haus.building_number == "Hinterhaus"
    # Und sie verschiebt den Zähler nicht: die nächste automatische ist die 1.
    naechstes = property_service.add_building(app_user.id, property_id=objekt.id)
    assert naechstes.building_number == "1"


@pytest.mark.django_db
def test_einheiten_zaehlen_je_liegenschaft_ueber_gebaeude_hinweg(app_user, objekt):
    """A-09: die Einheitsnummer ist je LIEGENSCHAFT eindeutig.

    Ein je Gebäude neu startender Zähler liefe im zweiten Gebäude sofort in
    `UNIQUE (property_id, unit_number)`.
    """
    haus1 = property_service.add_building(app_user.id, property_id=objekt.id)
    haus2 = property_service.add_building(app_user.id, property_id=objekt.id)
    e1 = property_service.add_unit(
        app_user.id, building_id=haus1.id, property_id=objekt.id,
        unit_type="APARTMENT",
    )
    e2 = property_service.add_unit(
        app_user.id, building_id=haus1.id, property_id=objekt.id,
        unit_type="APARTMENT",
    )
    e3 = property_service.add_unit(
        app_user.id, building_id=haus2.id, property_id=objekt.id,
        unit_type="APARTMENT",
    )
    assert [e1.unit_number, e2.unit_number, e3.unit_number] == ["01", "02", "03"]


@pytest.mark.django_db
def test_sprechende_einheitsnummer_bleibt(app_user, objekt):
    haus = property_service.add_building(app_user.id, property_id=objekt.id)
    e = property_service.add_unit(
        app_user.id, building_id=haus.id, property_id=objekt.id,
        unit_type="APARTMENT", unit_number="12 links",
    )
    assert e.unit_number == "12 links"
