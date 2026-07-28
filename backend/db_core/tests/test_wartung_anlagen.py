"""Wartungsvertrag ↔ technische Anlage (`maintenance.contract_asset`, 0135).

Der Befund aus dem Praxistest: Am Anlagendetail stand „welche Anlage sie
abdecken, sagt das System (noch) nicht". Diese Tests halten fest, was jetzt
stattdessen gilt — und vor allem, was **nicht** passieren darf:

* Ein Vertrag, der Anlage A nennt, taucht bei Anlage B **nicht** auf.
* Ein Vertrag **ohne** Zuordnung gilt weiter fürs ganze Objekt (Bestandsdaten).
* Vertrag und Anlage müssen zur selben Liegenschaft gehören — vorab als
  Fachfehler, in der DB als zusammengesetzter FK.
* Zuordnungen werden nie gelöscht, nur beendet (`active = false`).
"""
from datetime import date

import pytest

from db_core.models import MaintenanceContractAsset, WorkOrder
from db_core.services import anlage as anlage_service
from db_core.services import property as property_service
from db_core.services import wartung as wartung_service


@pytest.fixture
def objekt(app_user):
    """Ein Haus mit zwei Wohnungen und je einer Therme."""
    prop = property_service.create_property(
        app_user.id, name="Münsterstraße 24", property_type="WEG",
        street="Münsterstraße", house_number="24", postal_code="44145", city="Dortmund",
    )
    haus = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    we1 = property_service.add_unit(
        app_user.id, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="1", storey="EG",
    )
    we2 = property_service.add_unit(
        app_user.id, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="2", storey="1. OG",
    )
    therme1 = anlage_service.create_asset(
        app_user.id, prop.id,
        {"name": "Therme WE 1", "asset_type": "THERME_COMBI", "unit_id": we1.id},
    )
    therme2 = anlage_service.create_asset(
        app_user.id, prop.id,
        {"name": "Therme WE 2", "asset_type": "THERME_COMBI", "unit_id": we2.id},
    )
    return {
        "actor": app_user, "prop": prop, "haus": haus,
        "we1": we1, "we2": we2, "therme1": therme1, "therme2": therme2,
    }


def _vertrag(objekt, **kwargs):
    defaults = dict(
        property_id=objekt["prop"].id,
        name="Thermenwartung",
        start_date=date(2026, 6, 1),
        interval_kind="JAEHRLICH",
        due_action="AUFGABE",
    )
    defaults.update(kwargs)
    return wartung_service.create_contract(objekt["actor"].id, **defaults)


# --- Zuordnen ---------------------------------------------------------------

@pytest.mark.django_db
def test_vertrag_ohne_zuordnung_gilt_objektweit(objekt):
    """Bestandsverhalten: keine Zuordnung = gilt fürs ganze Objekt."""
    v = _vertrag(objekt)
    assert wartung_service.contract_assets(v.id) == []

    for anlage in (objekt["therme1"], objekt["therme2"]):
        treffer = anlage_service._vertraege_der_anlage(anlage)
        assert [t["contract"].id for t in treffer] == [v.id]
        assert treffer[0]["bezug"] == "LIEGENSCHAFT"


@pytest.mark.django_db
def test_zuordnung_beim_anlegen(objekt):
    v = _vertrag(objekt, asset_ids=[objekt["therme1"].id])
    assert [a.id for a in wartung_service.contract_assets(v.id)] == [
        objekt["therme1"].id
    ]


@pytest.mark.django_db
def test_vertrag_der_eine_anlage_nennt_erscheint_bei_der_anderen_nicht(objekt):
    """Der Kern des Befunds — und die Regel, die ihn behebt."""
    v = _vertrag(objekt, asset_ids=[objekt["therme1"].id])

    bei_1 = anlage_service._vertraege_der_anlage(objekt["therme1"])
    assert [t["contract"].id for t in bei_1] == [v.id]
    assert bei_1[0]["bezug"] == "ANLAGE"

    bei_2 = anlage_service._vertraege_der_anlage(objekt["therme2"])
    assert bei_2 == []


@pytest.mark.django_db
def test_objektweiter_und_anlagenvertrag_stehen_nebeneinander(objekt):
    objektweit = _vertrag(objekt, name="Rahmenvertrag Haus")
    genau = _vertrag(objekt, name="Therme WE 1", asset_ids=[objekt["therme1"].id])

    treffer = anlage_service._vertraege_der_anlage(objekt["therme1"])
    # Der ausdrückliche Bezug steht vorn — er ist die Antwort, der andere Kontext.
    assert [t["contract"].id for t in treffer] == [genau.id, objektweit.id]
    assert [t["bezug"] for t in treffer] == ["ANLAGE", "LIEGENSCHAFT"]


# --- Ändern -----------------------------------------------------------------

@pytest.mark.django_db
def test_set_assets_ersetzt_die_menge_vollstaendig(objekt):
    v = _vertrag(objekt, asset_ids=[objekt["therme1"].id])
    wartung_service.set_contract_assets(
        objekt["actor"].id, contract_id=v.id, asset_ids=[objekt["therme2"].id]
    )
    assert [a.id for a in wartung_service.contract_assets(v.id)] == [
        objekt["therme2"].id
    ]


@pytest.mark.django_db
def test_weggenommene_zuordnung_wird_beendet_nicht_geloescht(objekt):
    v = _vertrag(objekt, asset_ids=[objekt["therme1"].id])
    wartung_service.set_contract_assets(
        objekt["actor"].id, contract_id=v.id, asset_ids=[]
    )
    zeile = MaintenanceContractAsset.objects.get(
        contract_id=v.id, asset_id=objekt["therme1"].id
    )
    assert zeile.active is False


@pytest.mark.django_db
def test_erneutes_zuordnen_reaktiviert_dieselbe_zeile(objekt):
    v = _vertrag(objekt, asset_ids=[objekt["therme1"].id])
    wartung_service.set_contract_assets(
        objekt["actor"].id, contract_id=v.id, asset_ids=[]
    )
    wartung_service.set_contract_assets(
        objekt["actor"].id, contract_id=v.id, asset_ids=[objekt["therme1"].id]
    )
    zeilen = MaintenanceContractAsset.objects.filter(
        contract_id=v.id, asset_id=objekt["therme1"].id
    )
    assert zeilen.count() == 1  # kein zweiter Datensatz
    assert zeilen.first().active is True


@pytest.mark.django_db
def test_leere_liste_ist_gueltig_und_heisst_objektweit(objekt):
    v = _vertrag(objekt)
    wartung_service.set_contract_assets(
        objekt["actor"].id, contract_id=v.id, asset_ids=[]
    )
    assert wartung_service.contract_assets(v.id) == []


# --- Grenzen ----------------------------------------------------------------

@pytest.mark.django_db
def test_fremde_liegenschaft_wird_abgewiesen(objekt, app_user):
    anderes = property_service.create_property(
        app_user.id, name="Anderes Objekt", property_type="RENTAL_PROPERTY",
        street="Woanders", postal_code="44137", city="Dortmund",
    )
    fremde = anlage_service.create_asset(
        app_user.id, anderes.id, {"name": "Fremde", "asset_type": "KESSEL_HEIZUNG"}
    )
    v = _vertrag(objekt)
    with pytest.raises(ValueError, match="andere"):
        wartung_service.set_contract_assets(
            objekt["actor"].id, contract_id=v.id, asset_ids=[fremde.id]
        )


@pytest.mark.django_db
def test_stillgelegte_anlage_wird_nicht_neu_zugeordnet(objekt):
    anlage_service.update_asset(
        objekt["actor"].id, objekt["therme1"].id, {"status": "INAKTIV"}
    )
    v = _vertrag(objekt)
    with pytest.raises(ValueError, match="stillgelegt"):
        wartung_service.set_contract_assets(
            objekt["actor"].id, contract_id=v.id, asset_ids=[objekt["therme1"].id]
        )


@pytest.mark.django_db
def test_bestehende_zuordnung_ueberlebt_die_stilllegung(objekt):
    """Die Vergangenheit wird nicht umgeschrieben — nur Neuzuordnen ist gesperrt."""
    v = _vertrag(objekt, asset_ids=[objekt["therme1"].id])
    anlage_service.update_asset(
        objekt["actor"].id, objekt["therme1"].id, {"status": "INAKTIV"}
    )
    # Dieselbe Menge erneut setzen darf nicht scheitern.
    wartung_service.set_contract_assets(
        objekt["actor"].id, contract_id=v.id, asset_ids=[objekt["therme1"].id]
    )
    assert [a.id for a in wartung_service.contract_assets(v.id)] == [
        objekt["therme1"].id
    ]


@pytest.mark.django_db
def test_archivierter_vertrag_nimmt_keine_zuordnung_mehr(objekt):
    v = _vertrag(objekt)
    wartung_service.set_status(objekt["actor"].id, contract_id=v.id, to_status="INAKTIV")
    wartung_service.set_status(
        objekt["actor"].id, contract_id=v.id, to_status="ARCHIVIERT"
    )
    with pytest.raises(ValueError, match="archivierter"):
        wartung_service.set_contract_assets(
            objekt["actor"].id, contract_id=v.id, asset_ids=[objekt["therme1"].id]
        )


# --- Folgeobjekt ------------------------------------------------------------

@pytest.mark.django_db
def test_auftrag_erbt_die_einzige_anlage(objekt):
    """Deckt der Vertrag genau eine Anlage ab, steht sie im erzeugten Auftrag."""
    v = _vertrag(objekt, due_action="AUFTRAG", asset_ids=[objekt["therme1"].id])
    event, _ = wartung_service.trigger_action(objekt["actor"].id, contract_id=v.id)
    auftrag = WorkOrder.objects.get(id=event.result_object_id)
    assert auftrag.asset_id == objekt["therme1"].id
    assert auftrag.unit_id == objekt["we1"].id
    assert auftrag.building_id == objekt["haus"].id


@pytest.mark.django_db
def test_auftrag_bleibt_ohne_anlage_wenn_es_mehrere_sind(objekt):
    """Bei zwei Anlagen wird nicht geraten, welche gemeint ist."""
    v = _vertrag(
        objekt,
        due_action="AUFTRAG",
        asset_ids=[objekt["therme1"].id, objekt["therme2"].id],
    )
    event, _ = wartung_service.trigger_action(objekt["actor"].id, contract_id=v.id)
    auftrag = WorkOrder.objects.get(id=event.result_object_id)
    assert auftrag.asset_id is None
