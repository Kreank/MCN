"""Technische Anlagen (`property.technical_asset`) — Service- und API-Tests.

Die Bruchfälle sind hier wichtiger als der Normalfall (Lehre aus Welle 5):

* Anlage an **fremder** Liegenschaft anlegen (Gebäude/Einheit eines anderen
  Objekts) — muss scheitern, nicht stillschweigend die Standortkonsistenz brechen.
* Anlage **ohne** Liegenschaft — die Route trägt sie, ein unbekanntes Objekt → 404.
* Statuswechsel auf **INAKTIV statt Löschen** — und der Nachweis, dass die
  **DATENBANK** das Löschen verbietet (Migration 0101), nicht nur der fehlende
  Servicepfad. *Was im Service sitzt, ist umgehbar; erst was im Trigger sitzt, hält.*
* Einheit an **fremdem Gebäude** / Einheit **ohne** Gebäude (Konsistenz).
* Detail eines **nicht existierenden** Objekts → 404 ohne Existenzaussage.
* Preis-Analogie: `power_kw = 0` ist **kein** Wert — 0 kW hieße „heizt nicht".
* **Die Modul-Tore des Detail-Endpunkts**: Wartung/Prüfungen/Fälligkeiten hängen
  an `maintenance`, die Aufträge an `workflow`. Ohne das Recht fehlt der
  Baustein — die Antwort kommt trotzdem.
"""
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from db_core.models import TechnicalAsset
from db_core.services import anlage as anlage_service
from db_core.services import property as property_service


@pytest.fixture
def objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Anlagen-Objekt", property_type="WEG",
        street="Kesselweg", house_number="1", postal_code="10115", city="Berlin",
    )


@pytest.fixture
def fremd_objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Fremdes Objekt", property_type="WEG",
        street="Anderswo", house_number="9", postal_code="20095", city="Hamburg",
    )


def _gebaeude(app_user, prop, nummer="1"):
    return property_service.add_building(
        app_user.id, property_id=prop.id, building_number=nummer, name="Vorderhaus"
    )


def _einheit(app_user, gebaeude, nummer="EG-01"):
    return property_service.add_unit(
        app_user.id, building_id=gebaeude.id, property_id=gebaeude.property_id,
        unit_type="APARTMENT", unit_number=nummer,
    )


def _payload(**kwargs):
    daten = {"name": "Heizzentrale", "asset_type": "KESSEL_HEIZUNG"}
    daten.update(kwargs)
    return daten


def _post(client, prop, **kwargs):
    return client.post(
        f"/api/property/properties/{prop.id}/assets",
        data=_payload(**kwargs), content_type="application/json",
    )


# --- Normalfall ------------------------------------------------------------

@pytest.mark.django_db
def test_anlegen_und_lesen(admin_client, objekt):
    r = _post(
        admin_client, objekt, supply_type="ZENTRAL", manufacturer="Viessmann",
        model="Vitodens 200-W", year_built=2018, serial_number="7 5312 998",
        location_note="Keller, Heizraum", energy_source="GAS", power_kw="24.50",
    )
    assert r.status_code == 201, r.content
    daten = r.json()
    assert daten["name"] == "Heizzentrale"
    assert daten["asset_type"] == "KESSEL_HEIZUNG"
    assert daten["status"] == "AKTIV"
    # Der Kern des Auftrags: „zentrale Anlage" muss am Objekt stehen.
    assert daten["supply_type"] == "ZENTRAL"
    assert daten["manufacturer"] == "Viessmann"
    assert daten["year_built"] == 2018
    assert str(daten["power_kw"]) == "24.50"
    assert daten["wartungsvertraege"] == []
    assert daten["auftraege"] == []

    liste = admin_client.get(f"/api/property/properties/{objekt.id}/assets")
    assert liste.status_code == 200
    assert [a["id"] for a in liste.json()] == [daten["id"]]


@pytest.mark.django_db
def test_versorgung_default_ist_unbekannt_nicht_dezentral(admin_client, objekt):
    """Nicht erfasst heißt UNBEKANNT — nie stillschweigend „dezentral"."""
    r = _post(admin_client, objekt)
    assert r.json()["supply_type"] == "UNBEKANNT"


@pytest.mark.django_db
def test_patch_aendert_nur_gesendete_felder(admin_client, objekt):
    a = _post(admin_client, objekt, manufacturer="Vaillant", year_built=2005).json()
    r = admin_client.patch(
        f"/api/property/assets/{a['id']}",
        data={"supply_type": "ZENTRAL"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["supply_type"] == "ZENTRAL"
    assert r.json()["manufacturer"] == "Vaillant"   # unangetastet
    assert r.json()["year_built"] == 2005


@pytest.mark.django_db
def test_patch_null_leert_ein_feld(admin_client, objekt):
    """Ausdrückliches `null` löscht die Angabe (sonst wäre sie nie korrigierbar)."""
    a = _post(admin_client, objekt, manufacturer="Falsch").json()
    r = admin_client.patch(
        f"/api/property/assets/{a['id']}",
        data={"manufacturer": None}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["manufacturer"] is None


# --- Migration 0101: echte Spalten, echter Schutz ---------------------------

@pytest.mark.django_db
def test_stammdaten_stehen_in_SPALTEN_nicht_im_json(admin_client, objekt):
    """Die Ersatzteilsuche von morgen sucht über Hersteller + Modell — also SQL.

    Ein JSON-Schlüssel, in den jeder alles schreiben darf, trägt das nicht.
    `attributes` bleibt leer, solange niemand echte Zusatzfakten hinterlegt.
    """
    a = _post(
        admin_client, objekt, manufacturer="Vaillant", model="ecoTEC plus",
        year_built=2016,
    ).json()
    asset = TechnicalAsset.objects.get(id=a["id"])
    assert asset.manufacturer == "Vaillant"
    assert asset.model == "ecoTEC plus"
    assert asset.year_built == 2016
    assert asset.attributes == {}

    # Und die Spalten sind auch per SQL durchsuchbar (der Punkt der Übung).
    treffer = TechnicalAsset.objects.filter(
        manufacturer__iexact="vaillant", model__icontains="ecotec"
    )
    assert [x.id for x in treffer] == [asset.id]


@pytest.mark.django_db(transaction=True)
def test_die_DATENBANK_verbietet_das_loeschen(admin_client, objekt):
    """Der No-Delete-Trigger (0101), nicht der fehlende Servicepfad.

    Vorher lebte der Schutz allein davon, dass der Service keine Löschfunktion
    anbietet — am Service vorbei war ein DELETE physisch möglich. Dieser Test
    geht bewusst **am Service vorbei** und muss trotzdem scheitern.
    """
    a = _post(admin_client, objekt).json()
    with pytest.raises(ProgrammingError, match="append-only"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM property.technical_asset WHERE id = %s", [a["id"]]
                )
    assert TechnicalAsset.objects.filter(id=a["id"]).exists()


@pytest.mark.django_db
def test_die_DATENBANK_verbietet_0_kw(admin_client, objekt):
    """`power_kw > 0` steht im CHECK, nicht nur im Service. 0 kW hieße „heizt nicht"."""
    a = _post(admin_client, objekt).json()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TechnicalAsset.objects.filter(id=a["id"]).update(power_kw=Decimal("0"))


@pytest.mark.django_db
def test_die_DATENBANK_verbietet_eine_erfundene_anlagenart(admin_client, objekt):
    """Der CHECK aus 0101 — sonst erzeugte ein Import eine namenlose Gruppe im UI."""
    a = _post(admin_client, objekt).json()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TechnicalAsset.objects.filter(id=a["id"]).update(asset_type="RAKETE")


@pytest.mark.django_db
def test_die_DATENBANK_verbietet_alte_anlagenart_nach_0112(admin_client, objekt):
    """Migration 0112 tauschte den CHECK auf die SHK-Codeliste.

    Ein alter Code (`HEIZUNG`, `THERME`, `WAERMEPUMPE` …) darf jetzt weder durch
    den Service noch an ihm vorbei in die DB — sonst wäre die Umstellung nur im
    Service passiert und der CHECK spräche noch die alte Sprache.
    """
    a = _post(admin_client, objekt).json()
    for alt in ("HEIZUNG", "THERME", "WAERMEPUMPE", "SOLARTHERMIE", "TRINKWASSER"):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TechnicalAsset.objects.filter(id=a["id"]).update(asset_type=alt)


@pytest.mark.django_db
def test_codelisten_sind_deckungsgleich_mit_der_db(objekt, app_user):
    """Paritätstest: Was der Service erlaubt, erlaubt auch der CHECK — und umgekehrt.

    Ohne diesen Test driften Service-Tupel und DB-CHECK auseinander, und der
    Fehler zeigt sich erst als 500 beim Kunden.
    """
    for art in anlage_service.ASSET_TYPES:
        asset = anlage_service.create_asset(
            app_user.id, objekt.id, {"name": f"A {art}", "asset_type": art}
        )
        assert asset.asset_type == art
    for quelle in anlage_service.ENERGY_SOURCES:
        asset = anlage_service.create_asset(
            app_user.id, objekt.id,
            {"name": f"E {quelle}", "asset_type": "KESSEL_HEIZUNG",
             "energy_source": quelle},
        )
        assert asset.energy_source == quelle
    for versorgung in anlage_service.SUPPLY_TYPES:
        asset = anlage_service.create_asset(
            app_user.id, objekt.id,
            {"name": f"V {versorgung}", "asset_type": "KESSEL_HEIZUNG",
             "supply_type": versorgung},
        )
        assert asset.supply_type == versorgung


@pytest.mark.django_db
def test_aenderung_wird_auditiert(admin_client, objekt, app_user):
    """0101 hängt `audit_row_update` an die Tabelle — vorher lief jede Änderung
    spurlos durch."""
    a = _post(admin_client, objekt).json()
    admin_client.patch(
        f"/api/property/assets/{a['id']}",
        data={"manufacturer": "Buderus"}, content_type="application/json",
    )
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit.audit_entry "
            "WHERE target_type = 'property.technical_asset' AND target_id = %s "
            "AND action = 'ROW_UPDATE' AND actor_type = 'USER'",
            [a["id"]],
        )
        assert cur.fetchone()[0] >= 1


# --- Bruchfall: fremde Liegenschaft / fremde Einheit -----------------------

@pytest.mark.django_db
def test_gebaeude_fremder_liegenschaft_wird_abgewiesen(
    admin_client, app_user, objekt, fremd_objekt
):
    fremdes_gebaeude = _gebaeude(app_user, fremd_objekt)
    r = _post(admin_client, objekt, building_id=str(fremdes_gebaeude.id))
    assert r.status_code == 422, r.content
    assert "nicht zur angegebenen Liegenschaft" in r.json()["detail"]
    assert TechnicalAsset.objects.count() == 0


@pytest.mark.django_db
def test_einheit_fremden_gebaeudes_wird_abgewiesen(admin_client, app_user, objekt):
    """Einheit gehört zu Gebäude 2, angegeben ist Gebäude 1 — DERSELBEN Liegenschaft.

    Der zusammengesetzte FK (unit_id, building_id) fängt das in der DB als 500 ab;
    hier muss es ein 422 mit Grund sein.
    """
    g1 = _gebaeude(app_user, objekt, "1")
    g2 = _gebaeude(app_user, objekt, "2")
    u2 = _einheit(app_user, g2)
    r = _post(admin_client, objekt, building_id=str(g1.id), unit_id=str(u2.id))
    assert r.status_code == 422, r.content
    assert "nicht zum angegebenen Gebäude" in r.json()["detail"]


@pytest.mark.django_db
def test_einheit_ohne_gebaeude_wird_abgeleitet(admin_client, app_user, objekt):
    """Befund I11 — **umgekehrtes Verhalten seit 2026-07-21.**

    Vorher wies `ensure_standort` eine Einheit ohne Gebäude ab („Eine Einheit
    setzt ein Gebäude voraus"). Das war eine Bringschuld für jeden Aufrufer,
    obwohl die Funktion die Gebäude-ID der Einheit an derselben Stelle bereits
    las, um sie zu vergleichen. Sie leitet den Wert jetzt ab.

    Der zusammengesetzte FK verlangt ihn trotzdem — also muss er auch
    **geschrieben** werden, nicht nur geprüft.
    """
    from db_core.models import TechnicalAsset

    g = _gebaeude(app_user, objekt)
    u = _einheit(app_user, g)
    r = _post(admin_client, objekt, unit_id=str(u.id))
    assert r.status_code == 201, r.content

    zeile = TechnicalAsset.objects.get(id=r.json()["id"])
    assert zeile.unit_id == u.id
    assert zeile.building_id == g.id, "Das Gebäude muss abgeleitet gespeichert sein"


@pytest.mark.django_db
def test_patch_prueft_gegen_den_zielzustand(admin_client, app_user, objekt, fremd_objekt):
    """Nachträglich ein fremdes Gebäude unterschieben — muss ebenfalls scheitern."""
    a = _post(admin_client, objekt).json()
    fremdes_gebaeude = _gebaeude(app_user, fremd_objekt)
    r = admin_client.patch(
        f"/api/property/assets/{a['id']}",
        data={"building_id": str(fremdes_gebaeude.id)},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


# --- Bruchfall: Anlage ohne Liegenschaft ------------------------------------

@pytest.mark.django_db
def test_anlage_an_unbekannter_liegenschaft_ist_404(admin_client):
    r = admin_client.post(
        f"/api/property/properties/{uuid.uuid4()}/assets",
        data=_payload(), content_type="application/json",
    )
    assert r.status_code == 404, r.content
    assert TechnicalAsset.objects.count() == 0


@pytest.mark.django_db
def test_property_id_im_payload_wird_ignoriert(admin_client, objekt, fremd_objekt):
    """Die Liegenschaft kommt aus der Route. Ein Payload-Feld gäbe es zweimal."""
    r = admin_client.post(
        f"/api/property/properties/{objekt.id}/assets",
        data=_payload(property_id=str(fremd_objekt.id)),
        content_type="application/json",
    )
    assert r.status_code in (201, 422)
    if r.status_code == 201:
        assert r.json()["property_id"] == str(objekt.id)


# --- Bruchfall: Statuswechsel statt Löschen ---------------------------------

@pytest.mark.django_db
def test_stilllegen_statt_loeschen(admin_client, objekt):
    a = _post(admin_client, objekt).json()
    r = admin_client.patch(
        f"/api/property/assets/{a['id']}",
        data={"status": "INAKTIV"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "INAKTIV"
    # Die Zeile lebt weiter — nur aus der Regelliste ist sie raus.
    assert TechnicalAsset.objects.filter(id=a["id"]).exists()

    liste = admin_client.get(f"/api/property/properties/{objekt.id}/assets")
    assert liste.json() == []
    mit = admin_client.get(
        f"/api/property/properties/{objekt.id}/assets?mit_inaktiven=true"
    )
    assert [x["id"] for x in mit.json()] == [a["id"]]
    # Einzeln bleibt sie abrufbar, sonst wäre sie nicht reaktivierbar.
    assert admin_client.get(f"/api/property/assets/{a['id']}").status_code == 200


@pytest.mark.django_db
def test_reaktivieren(admin_client, objekt):
    a = _post(admin_client, objekt).json()
    admin_client.patch(
        f"/api/property/assets/{a['id']}",
        data={"status": "INAKTIV"}, content_type="application/json",
    )
    r = admin_client.patch(
        f"/api/property/assets/{a['id']}",
        data={"status": "AKTIV"}, content_type="application/json",
    )
    assert r.json()["status"] == "AKTIV"


@pytest.mark.django_db
def test_delete_endpunkt_gibt_es_nicht(admin_client, objekt):
    a = _post(admin_client, objekt).json()
    r = admin_client.delete(f"/api/property/assets/{a['id']}")
    assert r.status_code in (404, 405)
    assert TechnicalAsset.objects.filter(id=a["id"]).exists()


def test_kein_loeschpfad_im_code():
    """Statisch: weder Service noch API bekommen je einen Löschpfad.

    Seit 0101 hält zusätzlich der No-Delete-Trigger — aber ein Löschversuch soll
    gar nicht erst gebaut werden (er endete als 500 statt als klare Antwort).
    """
    wurzel = Path(__file__).resolve().parents[2]
    for pfad in (wurzel / "db_core" / "services" / "anlage.py",
                 wurzel / "api" / "anlage.py"):
        quelle = pfad.read_text(encoding="utf-8")
        assert not re.search(r"\.delete\s*\(", quelle), f"Löschpfad in {pfad.name}"
        assert "router.delete" not in quelle


# --- Bruchfall: unbekanntes Objekt -----------------------------------------

@pytest.mark.django_db
def test_detail_unbekannter_anlage_ist_404(admin_client):
    r = admin_client.get(f"/api/property/assets/{uuid.uuid4()}")
    assert r.status_code == 404
    assert "nicht gefunden" in r.json()["detail"]


@pytest.mark.django_db
def test_patch_unbekannter_anlage_ist_404(admin_client):
    r = admin_client.patch(
        f"/api/property/assets/{uuid.uuid4()}",
        data={"name": "X"}, content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_liste_unbekannter_liegenschaft_ist_404(admin_client):
    r = admin_client.get(f"/api/property/properties/{uuid.uuid4()}/assets")
    assert r.status_code == 404


# --- Validierung ------------------------------------------------------------

@pytest.mark.django_db
def test_unbekannte_anlagenart_wird_abgewiesen(admin_client, objekt):
    r = _post(admin_client, objekt, asset_type="RAKETE")
    assert r.status_code == 422
    assert "asset_type" in r.json()["detail"]


@pytest.mark.django_db
def test_anlagenart_ist_pflicht(admin_client, objekt):
    r = admin_client.post(
        f"/api/property/properties/{objekt.id}/assets",
        data={"name": "Irgendwas"}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_leere_bezeichnung_wird_abgewiesen(admin_client, objekt):
    r = _post(admin_client, objekt, name="   ")
    assert r.status_code == 422
    assert "name" in r.json()["detail"]


@pytest.mark.django_db
def test_leistung_null_ist_kein_wert(admin_client, objekt):
    """0 kW hieße „heizt nicht". Unbekannt bleibt leer — nie 0 (Projektinvariante)."""
    r = _post(admin_client, objekt, power_kw="0")
    assert r.status_code == 422
    assert "größer als 0" in r.json()["detail"]


@pytest.mark.django_db
def test_baujahr_ausserhalb_des_bereichs(admin_client, objekt):
    assert _post(admin_client, objekt, year_built=19).status_code == 422
    assert _post(admin_client, objekt, year_built=3000).status_code == 422


# --- Bezüge + die Modul-Tore des Detail-Endpunkts (Review-Fund) --------------

def _wartung_und_auftrag(app_user, objekt, asset_id):
    from datetime import date

    from db_core.services import auftrag as auftrag_service
    from db_core.services import wartung as wartung_service

    auftrag_service.create_work_order(
        app_user.id, property_id=objekt.id, title="Heizung ausgefallen",
        asset_id=uuid.UUID(asset_id),
    )
    wartung_service.create_contract(
        app_user.id, name="Heizungswartung jährlich", property_id=objekt.id,
        start_date=date(2026, 1, 1), interval_kind="JAEHRLICH", due_action="AUFTRAG",
    )


@pytest.mark.django_db
def test_detail_zeigt_auftraege_und_wartungsvertraege(admin_client, app_user, objekt):
    a = _post(admin_client, objekt).json()
    _wartung_und_auftrag(app_user, objekt, a["id"])

    r = admin_client.get(f"/api/property/assets/{a['id']}")
    assert r.status_code == 200, r.content
    daten = r.json()
    assert daten["maintenance_sichtbar"] is True
    assert daten["workflow_sichtbar"] is True
    assert [x["title"] for x in daten["auftraege"]] == ["Heizung ausgefallen"]
    # Wartungsverträge hängen am OBJEKT — das Schema kennt keinen Anlagenbezug.
    assert len(daten["wartungsvertraege"]) == 1
    assert daten["wartungsvertraege"][0]["bezug"] == "LIEGENSCHAFT"


@pytest.mark.django_db
def test_detail_tort_jedes_modul_einzeln(admin_client, client_with_role, app_user, objekt):
    """**Der Review-Fund.** Wer `property/LESEN` hat, aber KEIN `maintenance/LESEN`,
    bekommt die Anlage — aber **nicht** ihre Wartungsverträge.

    Vorher hing der ganze Endpunkt allein an `property/LESEN`, und die
    Wartungsdaten flossen einfach mit. Dass das im Moment kein Leck war, lag nur
    daran, dass zufällig jede Rolle mit `property` auch `maintenance` hat — die
    nächste Matrixzeile hätte daraus wieder eins gemacht.

    NUR_LESEN dient hier als Kontrast: Die Rolle darf lesen; entscheidend ist, dass
    der Baustein **genau dann** fehlt, wenn das Modulrecht fehlt — und dass die
    Antwort das ausspricht (`*_sichtbar`), statt eine leere Liste zu zeigen.
    """
    a = _post(admin_client, objekt).json()
    _wartung_und_auftrag(app_user, objekt, a["id"])

    r = admin_client.get(f"/api/property/assets/{a['id']}")
    daten = r.json()
    # Der Vertrag existiert und ist für den Berechtigten sichtbar …
    assert daten["maintenance_sichtbar"] is True
    assert len(daten["wartungsvertraege"]) == 1

    # … und die Bausteine hängen wirklich am jeweiligen Modulrecht:
    # der Service liefert sie nur, wenn das Recht mitgegeben wird.
    asset = anlage_service.get_asset(a["id"])
    ohne = anlage_service.bezuege(asset, maintenance=False, workflow=False)
    assert ohne["wartungsvertraege"] == []
    assert ohne["pruefungen"] == []
    assert ohne["faelligkeiten"] == []
    assert ohne["auftraege"] == []
    assert ohne["maintenance_sichtbar"] is False
    assert ohne["workflow_sichtbar"] is False

    nur_wartung = anlage_service.bezuege(asset, maintenance=True, workflow=False)
    assert len(nur_wartung["wartungsvertraege"]) == 1
    assert nur_wartung["auftraege"] == []          # workflow fehlt → kein Auftrag
    assert nur_wartung["workflow_sichtbar"] is False


@pytest.mark.django_db
def test_auftrag_kann_an_eine_anlage_gebunden_werden(admin_client, objekt):
    """Der Schreibpfad für `work_order.asset_id` — vor diesem Slice gab es KEINEN.

    Die Spalte liegt seit 0013 in der DB; gesetzt hat sie nie ein Produktpfad
    (dasselbe Muster wie `quote.work_order_id` in Welle 5). Ein Anlagen-Detail, das
    „Aufträge an dieser Anlage" zeigt, wäre ohne diese Zeile dauerhaft leer.
    """
    a = _post(admin_client, objekt).json()
    r = admin_client.post(
        "/api/workflow/work_orders",
        data={
            "property_id": str(objekt.id), "title": "Therme prüfen",
            "asset_id": a["id"],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["asset_id"] == a["id"]
    assert r.json()["asset_name"] == "Heizzentrale"


@pytest.mark.django_db
def test_auftrag_an_anlage_fremder_liegenschaft_wird_abgewiesen(
    admin_client, objekt, fremd_objekt
):
    """Der zusammengesetzte FK (asset_id, property_id) wäre sonst ein 500."""
    a = _post(admin_client, objekt).json()
    r = admin_client.post(
        "/api/workflow/work_orders",
        data={
            "property_id": str(fremd_objekt.id), "title": "Fremd",
            "asset_id": a["id"],
        },
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "nicht zur angegebenen Liegenschaft" in r.json()["detail"]


# --- Rechte -----------------------------------------------------------------

@pytest.mark.django_db
def test_nur_lesen_darf_nicht_schreiben(client_with_role, objekt):
    c = client_with_role("NUR_LESEN")
    assert c.get(f"/api/property/properties/{objekt.id}/assets").status_code == 200
    assert _post(c, objekt).status_code == 403


@pytest.mark.django_db
def test_anonym_ist_gesperrt(anonymous_client, objekt):
    r = anonymous_client.get(f"/api/property/properties/{objekt.id}/assets")
    assert r.status_code == 401


# --- row_scope 'EIGENE': die Objektsicht des Monteurs (Migration 0099) ------
# Die Regel „was ist meins?" steht in db_core/services/objektsicht.py und wird
# hier NICHT nachgebaut — nur benutzt. Der Monteur sieht das Objekt, auf dem er
# je einen Einsatz hatte; jedes andere ist 404 (keine Existenzaussage).

def _monteur_mit_einsatz(app_user, prop):
    """Ein eingeloggter MONTEUR, der auf `prop` je einen Einsatz hatte.

    Genau das — und nur das — macht ein Objekt nach `objektsicht` zu „seinem".
    """
    from django.test import Client

    from db_core.services import einsatz as einsatz_service

    from .conftest import make_role_user

    user, monteur = make_role_user("MONTEUR")
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=prop.id
    )
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_monteur_sieht_und_erfasst_anlagen_am_eigenen_objekt(
    admin_client, app_user, objekt
):
    a = _post(admin_client, objekt).json()
    c = _monteur_mit_einsatz(app_user, objekt)

    liste = c.get(f"/api/property/properties/{objekt.id}/assets")
    assert liste.status_code == 200, liste.content
    assert [x["id"] for x in liste.json()] == [a["id"]]
    assert c.get(f"/api/property/assets/{a['id']}").status_code == 200

    # Er darf sie auch erfassen — genau dafür gibt es die Objektsicht.
    neu = _post(c, objekt, name="Etagentherme", asset_type="THERME_HEIZUNG",
                supply_type="DEZENTRAL")
    assert neu.status_code == 201, neu.content


@pytest.mark.django_db
def test_monteur_sieht_fremdes_objekt_nicht_404_statt_403(
    admin_client, app_user, objekt, fremd_objekt
):
    """Fremde Anlage → 404, nicht 403: die Existenz wird nicht verraten."""
    fremd = _post(admin_client, fremd_objekt).json()
    c = _monteur_mit_einsatz(app_user, objekt)

    assert c.get(f"/api/property/properties/{fremd_objekt.id}/assets").status_code == 404
    assert c.get(f"/api/property/assets/{fremd['id']}").status_code == 404
    assert _post(c, fremd_objekt).status_code == 404
    r = c.patch(
        f"/api/property/assets/{fremd['id']}",
        data={"status": "INAKTIV"}, content_type="application/json",
    )
    assert r.status_code == 404
    # …und die fremde Anlage ist unverändert aktiv geblieben.
    assert anlage_service.get_asset(fremd["id"]).status == "AKTIV"


# --- Service-Ebene ----------------------------------------------------------

@pytest.mark.django_db
def test_fremde_attribute_bleiben_erhalten(app_user, objekt):
    """`attributes` gehört jetzt jemand anderem — der Service fasst es nicht an."""
    from db_core.db_context import business_transaction

    asset = anlage_service.create_asset(
        app_user.id, objekt.id, {"name": "Therme", "asset_type": "THERME_HEIZUNG"}
    )
    with business_transaction(app_user.id):
        TechnicalAsset.objects.filter(id=asset.id).update(
            attributes={"anlagenbuch_nr": "V-77"}
        )
    anlage_service.update_asset(app_user.id, asset.id, {"manufacturer": "Buderus"})
    asset.refresh_from_db()
    assert asset.attributes == {"anlagenbuch_nr": "V-77"}
    assert asset.manufacturer == "Buderus"


@pytest.mark.django_db
def test_unbekanntes_feld_wird_abgewiesen(app_user, objekt):
    with pytest.raises(ValueError, match="Unbekannte Felder"):
        anlage_service.create_asset(
            app_user.id, objekt.id,
            {"name": "X", "asset_type": "KESSEL_HEIZUNG", "manufacturor": "Tippfehler"},
        )
