"""Der Objektbezug eines Belegs — „um wessen Wohnung geht es hier?"

Sascha am 2026-08-01, am Beispiel einer echten Liegenschaft: WEG
Wartenbergstraße, drei Häuser, 24 Wohneinheiten, **24 verschiedene Eigentümer**,
16 davon vermietet. Auf Angebot und Rechnung muss stehen:

    Eigentümer, Wohneinheit/Mieter — vertreten durch Verwaltung xyz

Dazu kommt: „wir haben so ziemlich alles vertreten" — neben der WEG auch
Objekte mit **einem** Eigentümer plus Verwaltung und ganz normale
Eigenheimbesitzer. Der Auflöser muss alle drei bedienen, ohne dass jemand etwas
konfiguriert.

Die fachliche Grenze dahinter (INVARIANTEN.md §2): **Ab
Strang-/Wohnungsabsperrung ist es Sondereigentum, alles davor läuft über die
WEG.**
"""
from datetime import date
from types import SimpleNamespace

import pytest

from db_core.services import belegung as belegung_service
from db_core.services import eigentum as eigentum_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import verwaltung as verwaltung_service
from db_core.services.belegbezug import bezug_aufloesen, bezug_zeilen


def _objekt(actor, *, name, typ="WEG"):
    return property_service.create_property(
        actor, name=name, property_type=typ,
        street="Wartenbergstraße", house_number="24",
        postal_code="10365", city="Berlin",
    )


def _auftrag(unit, prop):
    """Steht für den Auftrag — der Auflöser liest daran nur `unit`.

    Ein echter WorkOrder verlangt Beteiligte, Gewerk und Freigabetore; für die
    Frage „welche Wohnung" ist davon nichts nötig. Die Verdrahtung mit dem
    echten Auftrag deckt `test_beleg_bezug_snapshot` ab.
    """
    return SimpleNamespace(unit=unit, property=prop)


@pytest.fixture
def weg(app_user):
    """Die Wartenbergstraße im Kleinen: zwei Häuser, zwei Wohnungen, Verwaltung."""
    a = app_user.id
    prop = _objekt(a, name="WEG Wartenbergstraße")
    vorderhaus = property_service.add_building(
        a, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    we12 = property_service.add_unit(
        a, building_id=vorderhaus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="12", storey="3. OG",
    )
    we4 = property_service.add_unit(
        a, building_id=vorderhaus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="4", storey="1. OG",
    )
    keller = property_service.add_unit(
        a, building_id=vorderhaus.id, property_id=prop.id,
        unit_type="TECHNICAL_ROOM", unit_number="Heizraum",
    )
    meier = identity_service.create_person(a, first_name="Klaus", last_name="Meier")
    yilmaz = identity_service.create_person(a, first_name="Aylin", last_name="Yilmaz")
    verwaltung = identity_service.create_organization(
        a, legal_name="Hausverwaltung Stegos GmbH", organization_type="PROPERTY_MANAGEMENT"
    )
    gemeinschaft = identity_service.create_organization(
        a, legal_name="WEG Wartenbergstraße 24", organization_type="WEG"
    )
    return {
        "actor": a, "prop": prop, "we12": we12, "we4": we4, "keller": keller,
        "meier": meier, "yilmaz": yilmaz, "verwaltung": verwaltung,
        "gemeinschaft": gemeinschaft,
    }


def _eigentuemer_der_wohnung(daten, unit, partei):
    return eigentum_service.create_stand(
        daten["actor"],
        unit_id=unit.id,
        valid_from=date(2020, 1, 1),
        source_type="OWNER_LIST",
        source_reference="Eigentümerliste der Verwaltung",
        distribution_status="COMPLETE",
        eigentuemer=[{
            "party_id": partei.id,
            "share_numerator": 1,
            "share_denominator": 1,
            "ownership_type": "SOLE",
            # COMPLETE duldet nur belegte Beteiligungen — ein „vollständiger"
            # Stand aus unbestätigten Angaben wäre eine Behauptung.
            "confirmation_status": "CONFIRMED",
        }],
    )


def _mandat(daten, *, scope="ENTIRE_PROPERTY", unit_ids=None):
    return verwaltung_service.create_mandat(
        daten["actor"],
        property_id=daten["prop"].id,
        management_party_id=daten["verwaltung"].id,
        principal_party_id=daten["gemeinschaft"].id,
        default_contact_party_id=daten["verwaltung"].id,
        mandate_type="WEG_MANAGEMENT",
        scope_type=scope,
        valid_from=date(2020, 1, 1),
        unit_ids=unit_ids,
    )


def _labels(bezug):
    return {label: wert for label, wert in bezug_zeilen(bezug)}


# --- Fall 1: WEG, Wohnung vermietet -----------------------------------------

@pytest.mark.django_db
def test_weg_vermietete_wohnung_nennt_eigentuemer_mieter_und_verwaltung(weg):
    """Der Regelfall aus Saschas Beispiel: 16 von 24 Wohnungen sind vermietet."""
    _eigentuemer_der_wohnung(weg, weg["we12"], weg["meier"])
    belegung_service.create_belegung(
        weg["actor"], unit_id=weg["we12"].id, occupancy_type="RENTED",
        valid_from=date(2023, 5, 1),
        mieter=[{"party_id": weg["yilmaz"].id, "role": "CONTRACTUAL_TENANT"}],
    )
    _mandat(weg)

    zeilen = _labels(bezug_aufloesen(_auftrag(weg["we12"], weg["prop"]), weg["prop"]))

    assert "Vorderhaus" in zeilen["Wohneinheit"]
    assert "WE 12" in zeilen["Wohneinheit"]
    assert "Klaus Meier" in zeilen["Eigentümer"]
    assert "Yilmaz" in zeilen["Mieter"]
    assert zeilen["Vertreten durch"] == "Hausverwaltung Stegos GmbH"


# --- Fall 2: WEG, Eigentümer wohnt selbst -----------------------------------

@pytest.mark.django_db
def test_selbst_bewohnte_wohnung_nennt_keinen_mieter(weg):
    """Die anderen 8: kein Mieter, aber der Vermerk gehört an den Eigentümer."""
    _eigentuemer_der_wohnung(weg, weg["we4"], weg["meier"])
    belegung_service.create_belegung(
        weg["actor"], unit_id=weg["we4"].id, occupancy_type="OWNER_OCCUPIED",
        valid_from=date(2019, 1, 1),
    )
    _mandat(weg)

    zeilen = _labels(bezug_aufloesen(_auftrag(weg["we4"], weg["prop"]), weg["prop"]))

    assert zeilen["Eigentümer"] == "Klaus Meier (bewohnt selbst)"
    assert "Mieter" not in zeilen


# --- Fall 3: Gemeinschaftseigentum ------------------------------------------

@pytest.mark.django_db
def test_technikraum_laeuft_ueber_die_gemeinschaft_ohne_mieter(weg):
    """Steigstrang, Keller, Heizraum — alles vor der Wohnungsabsperrung.

    Der Trigger `forbid_common_area_ownership` verbietet dort einen
    Eigentumsstand; der Bezug muss deshalb auf die Gemeinschaft ausweichen und
    darf **keinen** Mieter behaupten.
    """
    property_service.add_party_role(
        weg["actor"], property_id=weg["prop"].id, party_id=weg["gemeinschaft"].id,
        role="COMMUNITY_OF_OWNERS", valid_from=date(2020, 1, 1),
    )
    _mandat(weg)

    bezug = bezug_aufloesen(_auftrag(weg["keller"], weg["prop"]), weg["prop"])
    zeilen = _labels(bezug)

    assert bezug["gemeinschaftseigentum"] is True
    assert zeilen["Eigentümergemeinschaft"] == "WEG Wartenbergstraße 24"
    assert zeilen["Bereich"] == "Gemeinschaftseigentum"
    assert "Mieter" not in zeilen
    assert zeilen["Vertreten durch"] == "Hausverwaltung Stegos GmbH"


@pytest.mark.django_db
def test_auftrag_ohne_einheit_ist_gemeinschaftssache(weg):
    """Kein `unit` am Auftrag = Arbeit am Objekt, nicht in einer Wohnung."""
    property_service.add_party_role(
        weg["actor"], property_id=weg["prop"].id, party_id=weg["gemeinschaft"].id,
        role="COMMUNITY_OF_OWNERS", valid_from=date(2020, 1, 1),
    )

    bezug = bezug_aufloesen(_auftrag(None, weg["prop"]), weg["prop"])

    assert bezug["gemeinschaftseigentum"] is True
    assert bezug["einheit"] is None


# --- Fall 4: Ein Eigentümer für das ganze Objekt ----------------------------

@pytest.mark.django_db
def test_mietshaus_mit_einem_eigentuemer_kommt_von_der_liegenschaft(app_user):
    """Kein Eigentumsstand je Wohnung — der Eigentümer hängt am ganzen Objekt.

    Genau dafür wurde am 2026-07-21 entschieden, „Haus gehört Herrn X"
    anteilslos als Beteiligtenrolle zu führen statt als Eigentumsstand.
    """
    a = app_user.id
    prop = _objekt(a, name="Mietshaus Lindenallee", typ="RENTAL_PROPERTY")
    haus = property_service.add_building(a, property_id=prop.id, building_number="1")
    wohnung = property_service.add_unit(
        a, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="2",
    )
    besitzer = identity_service.create_person(a, first_name="Rita", last_name="Groß")
    property_service.add_party_role(
        a, property_id=prop.id, party_id=besitzer.id,
        role="PROPERTY_OWNER", valid_from=date(2015, 1, 1),
    )

    bezug = bezug_aufloesen(_auftrag(wohnung, prop), prop)

    assert bezug["eigentuemer"] == ["Rita Groß"]
    assert bezug["eigentuemer_herkunft"] == "OBJEKT"


# --- Fall 5: Eigenheim ------------------------------------------------------

@pytest.mark.django_db
def test_eigenheim_ohne_verwaltung_zeigt_keine_vertretungszeile(app_user):
    """Ohne Mandat entfällt die Zeile ersatzlos — nicht „Vertreten durch: —"."""
    a = app_user.id
    prop = _objekt(a, name="Einfamilienhaus Musterweg", typ="EINFAMILIENHAUS")
    besitzer = identity_service.create_person(a, first_name="Klaus", last_name="Meier")
    property_service.add_party_role(
        a, property_id=prop.id, party_id=besitzer.id,
        role="PROPERTY_OWNER", valid_from=date(2010, 1, 1),
    )

    zeilen = _labels(bezug_aufloesen(_auftrag(None, prop), prop))

    assert zeilen["Eigentümer"] == "Klaus Meier"
    assert "Vertreten durch" not in zeilen
    # Regression: Die erste Fassung setzte „kein Wohnungsbezug" mit
    # „Gemeinschaftseigentum" gleich — auf dem Blatt eines Einfamilienhauses
    # stand dann „Bereich: Gemeinschaftseigentum". Gemeinschaftseigentum gibt es
    # nur, wo es auch eine Gemeinschaft gibt. Am gerenderten PDF aufgefallen.
    assert "Bereich" not in zeilen


# --- Das Teilmandat darf nicht überall auftauchen ---------------------------

@pytest.mark.django_db
def test_teilmandat_gilt_nur_fuer_seine_einheiten(weg):
    """Sonst stünde auf dem Beleg für WE 12 die Verwaltung, die nur WE 4 betreut."""
    _eigentuemer_der_wohnung(weg, weg["we12"], weg["meier"])
    _mandat(weg, scope="SELECTED_UNITS", unit_ids=[weg["we4"].id])

    fuer_we12 = _labels(bezug_aufloesen(_auftrag(weg["we12"], weg["prop"]), weg["prop"]))
    fuer_we4 = _labels(bezug_aufloesen(_auftrag(weg["we4"], weg["prop"]), weg["prop"]))

    assert "Vertreten durch" not in fuer_we12
    assert fuer_we4["Vertreten durch"] == "Hausverwaltung Stegos GmbH"


# --- Nichts ableitbar -------------------------------------------------------

@pytest.mark.django_db
def test_ohne_jede_angabe_bleibt_der_beleg_wie_er_war(app_user):
    """Kein Eigentümer, kein Mieter, keine Verwaltung → kein Block.

    Der Beleg soll dann aussehen wie vor diesem Slice, statt eine leere
    Überschrift zu tragen.
    """
    prop = _objekt(app_user.id, name="Objekt ohne Angaben", typ="RENTAL_PROPERTY")

    assert bezug_aufloesen(_auftrag(None, prop), prop) is None
    assert bezug_zeilen(None) == []
