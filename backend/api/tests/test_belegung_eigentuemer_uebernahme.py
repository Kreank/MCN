"""Der Eigentümer aus der Belegungserfassung landet im Reiter „Eigentum".

Saschas Befund beim Testen der Demo:

> Bzw. kann ich ja bei Belegung auch Eigentümer als bewohnt angeben! Das
> kann/sollte optimalerweise übernommen werden beim Reiter Eigentum. Wollen ja
> keine doppelte Arbeit.

Die Fälle, die zählen, sind nicht der Normalfall, sondern die Ränder:

* Der Eigentümer ist **kein** Beteiligter der Belegung — wer vermietet, wohnt
  dort nicht. Er darf deshalb nicht in `occupancy_party` landen.
* Die Übernahme ist **wiederholbar**: Wer schon als Eigentümer geführt wird,
  bekommt keine zweite Beteiligung.
* Ein bereits **vollständig geklärter** Stand wird nicht nebenbei aufgeweicht.
* Belegung und Eigentum entstehen **zusammen oder gar nicht** — schlägt die
  Übernahme fehl, steht hinterher auch keine halbe Belegung da.
"""
from datetime import date, timedelta

import pytest

from db_core.models import Occupancy, OccupancyParty, OwnershipPeriod
from db_core.services import belegung as belegung_service
from db_core.services import eigentum as eigentum_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

HEUTE = date.today()
GESTERN = HEUTE - timedelta(days=1)


@pytest.fixture
def objekt(app_user):
    prop = property_service.create_property(
        app_user.id, name="WEG Badensche Straße 53", property_type="WEG",
        street="Badensche Straße", house_number="53", postal_code="10825",
        city="Berlin",
    )
    haus = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    einheit = property_service.add_unit(
        app_user.id, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="EG rechts",
    )
    return {"actor": app_user.id, "prop": prop, "einheit": einheit}


def _person(actor, nachname):
    return identity_service.create_person(actor, first_name="Max", last_name=nachname)


def _staende(unit_id):
    return list(
        OwnershipPeriod.objects.filter(unit_id=unit_id).prefetch_related("interests")
    )


# --- Normalfall -------------------------------------------------------------

@pytest.mark.django_db
def test_vermietet_und_eigentuemer_in_einem_zug(objekt):
    """Ein Formular, zwei Fakten — und der Eigentümer ist kein Mieter."""
    a = objekt["actor"]
    mieter = _person(a, "Robco")
    eigentuemer = _person(a, "Vermieter")

    occ = belegung_service.create_belegung(
        a,
        unit_id=objekt["einheit"].id,
        occupancy_type="RENTED",
        valid_from=HEUTE,
        contract_reference="MV-2026-14",
        mieter=[{"party_id": mieter.id, "role": "CONTRACTUAL_TENANT"}],
        eigentuemer_party_id=eigentuemer.id,
    )

    # Die Belegung kennt nur den Mieter.
    assert [z.party_id for z in occ.parties.all()] == [mieter.id]

    # Das Eigentum kennt nur den Eigentümer — mit schwacher, ehrlicher Aussage.
    staende = _staende(objekt["einheit"].id)
    assert len(staende) == 1
    stand = staende[0]
    assert stand.distribution_status == "PARTIAL"
    assert stand.valid_from == HEUTE
    assert stand.valid_until is None
    assert stand.source_type == "MANUAL"
    assert "MV-2026-14" in stand.source_reference
    beteiligung = stand.interests.get()
    assert beteiligung.owner_party_id == eigentuemer.id
    assert beteiligung.share_numerator is None
    assert beteiligung.confirmation_status == "UNCONFIRMED"


@pytest.mark.django_db
def test_eigennutzer_ist_zugleich_eigentuemer(objekt):
    """Derselbe Kontakt in beiden Rollen — genau der Fall aus dem Befund."""
    a = objekt["actor"]
    person = _person(a, "Musili")

    occ = belegung_service.create_belegung(
        a,
        unit_id=objekt["einheit"].id,
        occupancy_type="OWNER_OCCUPIED",
        valid_from=HEUTE,
        mieter=[{"party_id": person.id, "role": "OWNER_OCCUPANT"}],
        eigentuemer_party_id=person.id,
    )

    assert [z.party_id for z in occ.parties.all()] == [person.id]
    stand = _staende(objekt["einheit"].id)[0]
    assert stand.interests.get().owner_party_id == person.id


@pytest.mark.django_db
def test_ohne_angabe_entsteht_kein_eigentumsstand(objekt):
    """Keine Aussage ist keine Aussage — „nicht erfasst" bleibt „nicht erfasst"."""
    a = objekt["actor"]
    belegung_service.create_belegung(
        a,
        unit_id=objekt["einheit"].id,
        occupancy_type="VACANT",
        valid_from=HEUTE,
    )
    assert _staende(objekt["einheit"].id) == []


# --- Ränder -----------------------------------------------------------------

@pytest.mark.django_db
def test_uebernahme_haengt_sich_an_den_bestehenden_stand(objekt):
    """Ein laufender Stand wird ergänzt, nicht ein zweiter danebengestellt."""
    a = objekt["actor"]
    alt = _person(a, "Alteigentuemer")
    neu = _person(a, "Neueigentuemer")
    eigentum_service.create_stand(
        a,
        unit_id=objekt["einheit"].id,
        valid_from=GESTERN,
        source_type="OWNER_LIST",
        source_reference="Eigentümerliste vom 01.01.2026",
        distribution_status="PARTIAL",
        eigentuemer=[{"party_id": alt.id}],
    )

    belegung_service.create_belegung(
        a,
        unit_id=objekt["einheit"].id,
        occupancy_type="RENTED",
        valid_from=HEUTE,
        eigentuemer_party_id=neu.id,
    )

    staende = _staende(objekt["einheit"].id)
    assert len(staende) == 1
    assert {i.owner_party_id for i in staende[0].interests.all()} == {alt.id, neu.id}
    # Die Quelle des bestehenden Standes bleibt unangetastet.
    assert staende[0].source_type == "OWNER_LIST"


@pytest.mark.django_db
def test_derselbe_eigentuemer_wird_nicht_doppelt_eingetragen(objekt):
    """Wiederholbar: Zweimal dieselbe Aussage ist immer noch eine Aussage."""
    a = objekt["actor"]
    person = _person(a, "Eigner")
    belegung_service.create_belegung(
        a,
        unit_id=objekt["einheit"].id,
        occupancy_type="RENTED",
        valid_from=GESTERN,
        valid_until=HEUTE,
        eigentuemer_party_id=person.id,
    )
    belegung_service.create_belegung(
        a,
        unit_id=objekt["einheit"].id,
        occupancy_type="RENTED",
        valid_from=HEUTE,
        eigentuemer_party_id=person.id,
    )

    staende = _staende(objekt["einheit"].id)
    assert len(staende) == 1
    assert [i.owner_party_id for i in staende[0].interests.all()] == [person.id]


@pytest.mark.django_db
def test_vollstaendig_geklaerter_stand_wird_nicht_nebenbei_aufgeweicht(objekt):
    """Ein COMPLETE-Stand ist eine geprüfte Aussage — sie kippt nicht nebenbei.

    Und: Es entsteht **auch keine Belegung**. Beides gehört in eine
    Transaktion, sonst stünde hinterher eine halbe Erfassung da.
    """
    a = objekt["actor"]
    eigner = _person(a, "Alleineigentuemer")
    fremder = _person(a, "Fremder")
    eigentum_service.create_stand(
        a,
        unit_id=objekt["einheit"].id,
        valid_from=GESTERN,
        source_type="OWNER_LIST",
        source_reference="Grundbuchauszug",
        distribution_status="COMPLETE",
        eigentuemer=[
            {
                "party_id": eigner.id,
                "share_numerator": 1,
                "share_denominator": 1,
                "ownership_type": "SOLE",
                "confirmation_status": "CONFIRMED",
            }
        ],
    )

    with pytest.raises(ValueError, match="vollständig"):
        belegung_service.create_belegung(
            a,
            unit_id=objekt["einheit"].id,
            occupancy_type="RENTED",
            valid_from=HEUTE,
            eigentuemer_party_id=fremder.id,
        )

    assert not Occupancy.objects.filter(unit_id=objekt["einheit"].id).exists()
    assert _staende(objekt["einheit"].id)[0].interests.count() == 1


@pytest.mark.django_db
def test_unbekannter_kontakt_bricht_alles_ab(objekt):
    """Ein Tippfehler in der ID darf keine Belegung ohne Eigentümer hinterlassen."""
    import uuid

    a = objekt["actor"]
    with pytest.raises(ValueError):
        belegung_service.create_belegung(
            a,
            unit_id=objekt["einheit"].id,
            occupancy_type="RENTED",
            valid_from=HEUTE,
            eigentuemer_party_id=uuid.uuid4(),
        )
    assert not Occupancy.objects.filter(unit_id=objekt["einheit"].id).exists()


@pytest.mark.django_db
def test_nachgesetzte_person_kann_eigentuemer_mitbringen(objekt):
    """Zweiter Weg: „Weitere:n Mieter:in setzen" trägt den Eigentümer mit ein."""
    a = objekt["actor"]
    erst = _person(a, "Erstmieter")
    zweit = _person(a, "Zweitmieter")
    occ = belegung_service.create_belegung(
        a,
        unit_id=objekt["einheit"].id,
        occupancy_type="RENTED",
        valid_from=HEUTE,
        mieter=[{"party_id": erst.id, "role": "CONTRACTUAL_TENANT"}],
    )
    assert _staende(objekt["einheit"].id) == []

    belegung_service.add_mieter(
        a, occ.id, party_id=zweit.id, role="CO_TENANT", eigentuemer_party_id=zweit.id
    )

    assert OccupancyParty.objects.filter(occupancy_id=occ.id).count() == 2
    stand = _staende(objekt["einheit"].id)[0]
    assert stand.interests.get().owner_party_id == zweit.id


# --- API --------------------------------------------------------------------

@pytest.mark.django_db
def test_api_uebernimmt_den_eigentuemer_und_zeigt_ihn_im_eigentum(
    admin_client, objekt
):
    """End-to-end: erfassen über die Belegungs-API, lesen über die Eigentums-API."""
    a = objekt["actor"]
    eigentuemer = _person(a, "Hausbesitzer")

    antwort = admin_client.post(
        f"/api/tenure/properties/{objekt['prop'].id}/belegung",
        data={
            "unit_id": str(objekt["einheit"].id),
            "occupancy_type": "RENTED",
            "valid_from": HEUTE.isoformat(),
            "eigentuemer_party_id": str(eigentuemer.id),
        },
        content_type="application/json",
    )
    assert antwort.status_code == 201, antwort.content
    # Der Eigentümer taucht NICHT in der Mieterliste auf.
    assert antwort.json()["mieter"] == []

    eigentum = admin_client.get(
        f"/api/tenure/properties/{objekt['prop'].id}/eigentum"
    )
    assert eigentum.status_code == 200
    zeile = next(
        z for z in eigentum.json() if z["unit_id"] == str(objekt["einheit"].id)
    )
    assert zeile["eigentum"]["distribution_status"] == "PARTIAL"
    namen = [e["display_name"] for e in zeile["eigentum"]["eigentuemer"]]
    assert namen == [eigentuemer.display_name]

    # Und in der Empfängerliste („20 Rechnungsadressen") steht er auch.
    empfaenger = admin_client.get(
        f"/api/tenure/properties/{objekt['prop'].id}/eigentuemer"
    )
    assert [e["display_name"] for e in empfaenger.json()] == [
        eigentuemer.display_name
    ]


@pytest.mark.django_db
def test_api_meldet_den_konflikt_lesbar_statt_500(admin_client, objekt):
    a = objekt["actor"]
    eigner = _person(a, "Alleineigentuemer")
    fremder = _person(a, "Fremder")
    eigentum_service.create_stand(
        a,
        unit_id=objekt["einheit"].id,
        valid_from=GESTERN,
        source_type="OWNER_LIST",
        source_reference="Grundbuchauszug",
        distribution_status="COMPLETE",
        eigentuemer=[
            {
                "party_id": eigner.id,
                "share_numerator": 1,
                "share_denominator": 1,
                "ownership_type": "SOLE",
                "confirmation_status": "CONFIRMED",
            }
        ],
    )

    antwort = admin_client.post(
        f"/api/tenure/properties/{objekt['prop'].id}/belegung",
        data={
            "unit_id": str(objekt["einheit"].id),
            "occupancy_type": "RENTED",
            "valid_from": HEUTE.isoformat(),
            "eigentuemer_party_id": str(fremder.id),
        },
        content_type="application/json",
    )
    assert antwort.status_code == 422
    assert "Eigentum" in antwort.json()["detail"]


@pytest.mark.django_db
def test_monteur_kann_ueber_die_belegung_kein_eigentum_anlegen(
    client_with_role, objekt
):
    """Kein Schlupfloch: Der Nebeneingang trägt dieselben Rechte wie der Haupteingang."""
    a = objekt["actor"]
    eigentuemer = _person(a, "Hausbesitzer")
    client = client_with_role("MONTEUR")
    antwort = client.post(
        f"/api/tenure/properties/{objekt['prop'].id}/belegung",
        data={
            "unit_id": str(objekt["einheit"].id),
            "occupancy_type": "RENTED",
            "valid_from": HEUTE.isoformat(),
            "eigentuemer_party_id": str(eigentuemer.id),
        },
        content_type="application/json",
    )
    assert antwort.status_code == 403
    assert not OwnershipPeriod.objects.filter(unit_id=objekt["einheit"].id).exists()
