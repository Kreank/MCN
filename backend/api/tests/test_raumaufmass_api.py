"""API-Tests des Raumaufmaßes (Vertrag für das Frontend).

Der Vertrag ist fix: das Frontend wird parallel dagegen gebaut. Geprüft werden
deshalb nicht nur die Statuscodes, sondern die **Feldnamen** und — vor allem —
dass die Heizlast als `null` mit Grund über die Leitung geht, wo Eingaben fehlen.
Ein `0` an dieser Stelle wäre eine still zu klein ausgelegte Heizung.

Rechteweg: Modul `property` (der Raum ist Objektstammdatum). NUR_LESEN darf lesen,
aber nicht schreiben.
"""
import uuid
from decimal import Decimal

import pytest

from db_core.services import property as property_service
from db_core.services import raum as raum_service


@pytest.fixture
def objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Aufmaß-Objekt", property_type="EINFAMILIENHAUS",
        street="Feldweg", house_number="3", postal_code="10115", city="Berlin",
    )


def _raum_payload(**kwargs):
    daten = {
        "name": "Wohnzimmer",
        "floor_area_m2": "20.000",
        "room_height_m": "2.500",
        "indoor_temp_c": "20.0",
        "air_change_rate": "0.50",
    }
    daten.update(kwargs)
    return daten


def _aufbau_payload():
    return {
        "surfaces": [
            {
                "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
                "orientation": "S", "label": "Südwand",
                "gross_area_m2": "10.000", "u_value": "0.800",
            }
        ],
        "openings": [
            {
                "surface_ref": "s1", "opening_type": "FENSTER", "label": "Fenster",
                "quantity": 1, "width_m": "2.000", "height_m": "1.000",
                "u_value": "1.300",
            }
        ],
    }


def _post_raum(client, prop, **kwargs):
    r = client.post(
        f"/api/property/properties/{prop.id}/rooms",
        data=_raum_payload(**kwargs), content_type="application/json",
    )
    assert r.status_code == 201, r.content
    return r.json()


# --- Anlegen und Lesen -----------------------------------------------------

@pytest.mark.django_db
def test_raum_anlegen_liefert_generiertes_volumen(admin_client, objekt):
    body = _post_raum(admin_client, objekt)
    assert body["name"] == "Wohnzimmer"
    # volume_m3 ist eine GENERATED-Spalte: 20 × 2,5.
    assert Decimal(body["volume_m3"]) == Decimal("50.000")
    assert body["surfaces"] == []
    assert body["openings"] == []


@pytest.mark.django_db
def test_liste_und_detail(admin_client, objekt):
    angelegt = _post_raum(admin_client, objekt)
    r = admin_client.get(f"/api/property/properties/{objekt.id}/rooms")
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [angelegt["id"]]

    r = admin_client.get(f"/api/property/rooms/{angelegt['id']}")
    assert r.status_code == 200
    assert r.json()["name"] == "Wohnzimmer"


@pytest.mark.django_db
def test_liste_ohne_auslegungsdaten_am_objekt_ist_null_mit_grund(admin_client, objekt):
    """Trägt die Liegenschaft keine Auslegungs-Außentemperatur, sind die
    Hüllflächenwerte `null` MIT GRUND — nicht 0. Der Grund nennt das Feld, das zu
    pflegen ist."""
    _post_raum(admin_client, objekt)
    r = admin_client.get(f"/api/property/properties/{objekt.id}/rooms")
    k = r.json()[0]["kennzahlen"]
    assert k["heizlast_huellflaeche_w"] is None
    assert k["transmission_w"] is None
    assert "Außentemperatur" in k["unbekannt_grund"]
    assert "design_outdoor_temp_c" in k["unbekannt_grund"]


@pytest.mark.django_db
def test_raum_ohne_huellflaeche_hat_keine_wandflaeche(admin_client, objekt):
    """`wall_area_*` ist `null` (unbekannt), nicht 0 — sonst liefe eine erfundene
    0-Menge als Grundlage fürs Verputzen/Streichen in ein Angebot."""
    body = _post_raum(admin_client, objekt)
    k = body["kennzahlen"]
    assert k["wall_area_gross_m2"] is None
    assert k["wall_area_net_m2"] is None
    assert Decimal(k["opening_area_m2"]) == Decimal("0.000")


# --- Auslegungsdaten am Objekt (Migration 0089) ----------------------------

def _set_auslegung(client, prop, **daten):
    return client.patch(
        f"/api/property/properties/{prop.id}/auslegung",
        data=daten, content_type="application/json",
    )


@pytest.mark.django_db
def test_auslegung_setzen_und_zuruecksetzen(admin_client, objekt):
    """Der Vertrag fürs Frontend: PATCH …/auslegung, beide Felder NULL-fähig.
    Nicht gesendet = unverändert, ausdrücklich null = zurückgesetzt."""
    r = _set_auslegung(admin_client, objekt,
                       design_outdoor_temp_c="-12.0", heat_load_w_per_m2="55.0")
    assert r.status_code == 200, r.content
    assert Decimal(r.json()["design_outdoor_temp_c"]) == Decimal("-12.0")
    assert Decimal(r.json()["heat_load_w_per_m2"]) == Decimal("55.0")

    # Teil-Update: das nicht gesendete Feld bleibt stehen.
    r = _set_auslegung(admin_client, objekt, design_outdoor_temp_c="-14.0")
    assert Decimal(r.json()["design_outdoor_temp_c"]) == Decimal("-14.0")
    assert Decimal(r.json()["heat_load_w_per_m2"]) == Decimal("55.0")

    # Ausdrückliches null löscht.
    r = _set_auslegung(admin_client, objekt,
                       design_outdoor_temp_c=None, heat_load_w_per_m2=None)
    assert r.status_code == 200, r.content
    assert r.json() == {"design_outdoor_temp_c": None, "heat_load_w_per_m2": None}


@pytest.mark.django_db
def test_auslegung_am_objekt_laesst_jeden_raum_endpunkt_rechnen(admin_client, objekt):
    """Das Kernversprechen: KEIN Client schickt etwas mit — und trotzdem steht in
    JEDEM Raum-Endpunkt eine echte Heizlast."""
    _set_auslegung(admin_client, objekt, design_outdoor_temp_c="-10.0",
                   heat_load_w_per_m2="70.0")
    raum = _post_raum(admin_client, objekt)
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=_aufbau_payload(), content_type="application/json",
    )
    assert r.status_code == 200, r.content

    # 0,8×8×30 + 1,3×2×30 = 270 W; Lüftung 0,34×0,5×50×30 = 255 W.
    for k in (
        r.json()["kennzahlen"],                                        # PUT /aufbau
        admin_client.get(
            f"/api/property/rooms/{raum['id']}").json()["kennzahlen"],  # GET /rooms/{id}
        admin_client.get(
            f"/api/property/properties/{objekt.id}/rooms"
        ).json()[0]["kennzahlen"],                                     # Liste
    ):
        assert k["unbekannt_grund"] is None
        assert Decimal(k["transmission_w"]) == Decimal("270.0")
        assert Decimal(k["lueftung_w"]) == Decimal("255.0")
        assert Decimal(k["heizlast_huellflaeche_w"]) == Decimal("525.0")
        # Die ausgewiesene Summe IST die Summe der ausgewiesenen Teile.
        assert Decimal(k["heizlast_huellflaeche_w"]) == (
            Decimal(k["transmission_w"]) + Decimal(k["lueftung_w"])
        )
        assert Decimal(k["heizlast_kennwert_w"]) == Decimal("1400.0")  # 20 × 70

    # Und der PATCH-Endpunkt rechnet ebenfalls mit den Objektdaten.
    r = admin_client.patch(
        f"/api/property/rooms/{raum['id']}",
        data={"note": "geprüft"}, content_type="application/json",
    )
    assert Decimal(r.json()["kennzahlen"]["heizlast_huellflaeche_w"]) == Decimal("525.0")


@pytest.mark.django_db
def test_query_parameter_uebersteuert_das_objekt(admin_client, objekt):
    """Was-wäre-wenn: der Parameter schlägt den Objektwert, ändert ihn aber nicht."""
    _set_auslegung(admin_client, objekt, design_outdoor_temp_c="-10.0")
    raum = _post_raum(admin_client, objekt)
    admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=_aufbau_payload(), content_type="application/json",
    )
    r = admin_client.get(
        f"/api/property/rooms/{raum['id']}?aussentemperatur_c=0"
    )
    # ΔT = 20 statt 30: 0,8×8×20 + 1,3×2×20 = 180 W.
    assert Decimal(r.json()["kennzahlen"]["transmission_w"]) == Decimal("180.0")
    # Der gespeicherte Objektwert bleibt unangetastet.
    a = admin_client.get(f"/api/property/properties/{objekt.id}/aufmass").json()
    assert Decimal(a["design_outdoor_temp_c"]) == Decimal("-10.0")


@pytest.mark.django_db
def test_aufmass_liefert_die_wirksamen_auslegungsdaten(admin_client, objekt):
    """Das Panel zeigt die Werte an und belegt damit sein Formular vor — ohne
    zweiten Endpunkt."""
    a = admin_client.get(f"/api/property/properties/{objekt.id}/aufmass").json()
    assert a["design_outdoor_temp_c"] is None
    assert a["heat_load_w_per_m2"] is None

    _set_auslegung(admin_client, objekt, design_outdoor_temp_c="-12.0",
                   heat_load_w_per_m2="65.0")
    a = admin_client.get(f"/api/property/properties/{objekt.id}/aufmass").json()
    assert Decimal(a["design_outdoor_temp_c"]) == Decimal("-12.0")
    assert Decimal(a["heat_load_w_per_m2"]) == Decimal("65.0")


@pytest.mark.django_db
def test_auslegung_unbekannte_liegenschaft_404(admin_client, objekt):
    r = admin_client.patch(
        f"/api/property/properties/{uuid.uuid4()}/auslegung",
        data={"design_outdoor_temp_c": "-10.0"}, content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_auslegung_ohne_recht_403(client_with_role, objekt):
    c = client_with_role("NUR_LESEN")
    r = c.patch(
        f"/api/property/properties/{objekt.id}/auslegung",
        data={"design_outdoor_temp_c": "-10.0"}, content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_auslegung_wertebereich_422(admin_client, objekt):
    r = _set_auslegung(admin_client, objekt, design_outdoor_temp_c="-60.0")
    assert r.status_code == 422, r.content
    assert "design_outdoor_temp_c" in r.json()["detail"]


# --- Bedienfehler bleiben 422 (nie 500) ------------------------------------

@pytest.mark.django_db
def test_zu_grosse_flaeche_422(admin_client, objekt):
    """numeric(10,3) → „numeric field overflow" wäre ein DataError (500)."""
    r = admin_client.post(
        f"/api/property/properties/{objekt.id}/rooms",
        data=_raum_payload(floor_area_m2="99999999"),
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    detail = r.json()["detail"]
    assert "Grundfläche" in detail and "9.999.999,999" in detail


@pytest.mark.django_db
def test_zu_grosse_luftwechselrate_422(admin_client, objekt):
    r = admin_client.post(
        f"/api/property/properties/{objekt.id}/rooms",
        data=_raum_payload(air_change_rate="100"), content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Luftwechselrate" in r.json()["detail"]


@pytest.mark.django_db
def test_zu_grosser_kennwert_422(admin_client, objekt):
    r = admin_client.post(
        f"/api/property/properties/{objekt.id}/rooms",
        data=_raum_payload(heat_load_w_per_m2="100000"),
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Kennwert" in r.json()["detail"]


@pytest.mark.django_db
def test_zu_feiner_u_wert_422(admin_client, objekt):
    """numeric(5,3) rundete 0,0001 auf 0,000 → CHECK-Verletzung → 500."""
    raum = _post_raum(admin_client, objekt)
    payload = _aufbau_payload()
    payload["surfaces"][0]["u_value"] = "0.0001"
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=payload, content_type="application/json",
    )
    assert r.status_code == 422, r.content
    detail = r.json()["detail"]
    assert "U-Wert" in detail and "0,001" in detail


@pytest.mark.django_db
def test_zu_feines_fenstermass_422(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    payload = _aufbau_payload()
    payload["openings"][0]["width_m"] = "0.0004"
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=payload, content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Breite" in r.json()["detail"]


@pytest.mark.django_db
def test_freie_oeffnung_groesser_als_alle_bauteile_422(admin_client, objekt):
    """Raumweite Grenze (b) aus 0089 — der Trigger meldet, der Service übersetzt."""
    raum = _post_raum(admin_client, objekt)
    payload = _aufbau_payload()
    payload["openings"][0]["surface_ref"] = None       # freier Mengenabzug
    payload["openings"][0]["width_m"] = "5.000"
    payload["openings"][0]["height_m"] = "5.000"       # 25 m² gegen 10 m² Wand
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=payload, content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Nettowandfläche" in r.json()["detail"]


@pytest.mark.django_db
def test_freie_oeffnung_ohne_huellflaeche_erlaubt(admin_client, objekt):
    """Ohne jede Hüllfläche gibt es keine Grenze — Fenster dürfen vor den Wänden
    erfasst werden; die Wandfläche bleibt `null`."""
    raum = _post_raum(admin_client, objekt)
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={
            "surfaces": [],
            "openings": [{
                "surface_ref": None, "opening_type": "FENSTER", "quantity": 1,
                "width_m": "5.000", "height_m": "5.000",
            }],
        },
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    k = r.json()["kennzahlen"]
    assert Decimal(k["opening_area_m2"]) == Decimal("25.000")
    assert k["wall_area_net_m2"] is None


@pytest.mark.django_db
def test_detail_404(admin_client, objekt):
    r = admin_client.get(f"/api/property/rooms/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_liste_unbekannte_liegenschaft_404(admin_client, objekt):
    r = admin_client.get(f"/api/property/properties/{uuid.uuid4()}/rooms")
    assert r.status_code == 404


@pytest.mark.django_db
def test_anlegen_ungueltiger_room_type_422(admin_client, objekt):
    r = admin_client.post(
        f"/api/property/properties/{objekt.id}/rooms",
        data=_raum_payload(room_type="PARTYKELLER"),
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_anlegen_dublette_422(admin_client, objekt):
    _post_raum(admin_client, objekt)
    r = admin_client.post(
        f"/api/property/properties/{objekt.id}/rooms",
        data=_raum_payload(), content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "bereits ein Raum" in r.json()["detail"]


# --- PATCH -----------------------------------------------------------------

@pytest.mark.django_db
def test_patch_aendert_nur_gesendete_felder(admin_client, objekt):
    raum = _post_raum(admin_client, objekt, note="Erstaufnahme")
    r = admin_client.patch(
        f"/api/property/rooms/{raum['id']}",
        data={"room_height_m": "3.000"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert Decimal(body["volume_m3"]) == Decimal("60.000")  # zieht nach
    assert body["note"] == "Erstaufnahme"
    assert body["name"] == "Wohnzimmer"


@pytest.mark.django_db
def test_patch_404(admin_client, objekt):
    r = admin_client.patch(
        f"/api/property/rooms/{uuid.uuid4()}",
        data={"name": "X"}, content_type="application/json",
    )
    assert r.status_code == 404


# --- PUT /aufbau -----------------------------------------------------------

@pytest.mark.django_db
def test_aufbau_setzen_und_heizlast_rechnen(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=_aufbau_payload(), content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    wand = body["surfaces"][0]
    fenster = body["openings"][0]
    # surface_ref ist aufgelöst.
    assert fenster["surface_id"] == wand["id"]
    assert Decimal(fenster["area_m2"]) == Decimal("2.000")
    assert Decimal(wand["net_area_m2"]) == Decimal("8.000")

    # Die Geometrie steht immer; die Heizlast rechnet, sobald die Liegenschaft
    # ihre Auslegungsdaten trägt (siehe test_auslegung_am_objekt_…).
    k = body["kennzahlen"]
    assert Decimal(k["wall_area_gross_m2"]) == Decimal("10.000")
    assert Decimal(k["opening_area_m2"]) == Decimal("2.000")
    assert Decimal(k["wall_area_net_m2"]) == Decimal("8.000")


@pytest.mark.django_db
def test_aufbau_fenster_groesser_als_wand_422(admin_client, objekt):
    """Der DB-Trigger ist ein Bedienfehler → 422 mit Klartext, nicht 500."""
    raum = _post_raum(admin_client, objekt)
    payload = _aufbau_payload()
    payload["openings"][0]["width_m"] = "5.000"
    payload["openings"][0]["height_m"] = "5.000"  # 25 m² in 10 m² Wand
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=payload, content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "größer als die Fläche" in r.json()["detail"]


@pytest.mark.django_db
def test_aufbau_unbekannte_surface_ref_422(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    payload = _aufbau_payload()
    payload["openings"][0]["surface_ref"] = "gibtsnicht"
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=payload, content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "unbekannte Hüllfläche" in r.json()["detail"]


@pytest.mark.django_db
def test_aufbau_ersetzt_den_satz(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=_aufbau_payload(), content_type="application/json",
    )
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [], "openings": []}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["surfaces"] == []
    assert r.json()["openings"] == []


# --- Aufmaß (Gebäudesummen) ------------------------------------------------

@pytest.mark.django_db
def test_aufmass_mit_aussentemperatur(admin_client, objekt, app_user):
    raum = _post_raum(admin_client, objekt, riser_distance_m="6.00")
    admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=_aufbau_payload(), content_type="application/json",
    )
    r = admin_client.get(
        f"/api/property/properties/{objekt.id}/aufmass"
        "?aussentemperatur_c=-10&kennwert_w_m2=70"
    )
    assert r.status_code == 200, r.content
    a = r.json()
    assert a["raeume_anzahl"] == 1
    assert Decimal(a["flaeche_m2"]) == Decimal("20.000")
    assert Decimal(a["volumen_m3"]) == Decimal("50.000")
    # Kennwert: 20 × 70 = 1400 W.
    assert Decimal(a["heizlast_kennwert_w"]) == Decimal("1400.0")
    # Hülle: 0,8×8×1×30 + 1,3×2×1×30 + 0,34×0,5×50×30 = 192 + 78 + 255 = 525 W.
    assert Decimal(a["heizlast_huellflaeche_w"]) == Decimal("525.0")
    assert a["unbekannt_raeume"] == []
    # SCHÄTZUNG: 2 × 6 m.
    assert Decimal(a["leitungslaenge_schaetzung_m"]) == Decimal("12.000")
    assert a["raeume_ohne_steigleitung"] == 0
    assert any("SCHÄTZUNG" in h for h in a["hinweise"])


@pytest.mark.django_db
def test_aufmass_ohne_parameter_ist_unbekannt_nicht_null(admin_client, objekt):
    """INVARIANTE über die Leitung: fehlen Eingaben, ist die Heizlast `null` —
    und die betroffenen Räume werden benannt."""
    raum = _post_raum(admin_client, objekt)
    admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=_aufbau_payload(), content_type="application/json",
    )
    r = admin_client.get(f"/api/property/properties/{objekt.id}/aufmass")
    assert r.status_code == 200, r.content
    a = r.json()
    assert a["heizlast_kennwert_w"] is None
    assert a["heizlast_huellflaeche_w"] is None
    assert a["unbekannt_raeume"] == ["Wohnzimmer"]


@pytest.mark.django_db
def test_aufmass_fehlender_u_wert_benennt_die_flaeche(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    payload = _aufbau_payload()
    payload["surfaces"][0]["u_value"] = None
    payload["openings"] = []
    admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=payload, content_type="application/json",
    )
    r = admin_client.get(
        f"/api/property/properties/{objekt.id}/aufmass?aussentemperatur_c=-10"
    )
    a = r.json()
    assert a["heizlast_huellflaeche_w"] is None
    assert a["unbekannt_raeume"] == ["Wohnzimmer"]
    assert any("Südwand" in h for h in a["hinweise"])


@pytest.mark.django_db
def test_aufmass_ohne_raeume_ist_null_nicht_0(admin_client, objekt):
    """Ohne aufgenommenen Raum ist die Heizlast `null` — sonst zeigte das UI
    „0,0 kW" für ein Objekt, das schlicht noch nicht vermessen ist."""
    _set_auslegung(admin_client, objekt, design_outdoor_temp_c="-10.0",
                   heat_load_w_per_m2="70.0")
    a = admin_client.get(f"/api/property/properties/{objekt.id}/aufmass").json()
    assert a["raeume_anzahl"] == 0
    assert a["heizlast_kennwert_w"] is None
    assert a["heizlast_huellflaeche_w"] is None
    assert any("kein Raum aufgenommen" in h for h in a["hinweise"])
    # Pflichtfelder (NOT NULL): leere Summe = 0.
    assert Decimal(a["flaeche_m2"]) == Decimal("0.000")
    # NULL-fähige Felder: unbekannt, nicht 0 — eine Leitungslänge „0,0 m" liefe
    # als Menge in ein Angebot.
    assert a["umfang_m"] is None
    assert a["leitungslaenge_schaetzung_m"] is None


@pytest.mark.django_db
def test_aufmass_ohne_umfang_und_steigleitung_ist_null(admin_client, objekt):
    """Räume ohne gemessenen Umfang/Steigleitungsweg → `null`, nicht „0,00 m"."""
    _post_raum(admin_client, objekt)          # weder perimeter_m noch riser_distance_m
    a = admin_client.get(f"/api/property/properties/{objekt.id}/aufmass").json()
    assert a["raeume_anzahl"] == 1
    assert a["umfang_m"] is None
    assert a["leitungslaenge_schaetzung_m"] is None
    assert any("Leitungslänge ist unbekannt" in h for h in a["hinweise"])


@pytest.mark.django_db
def test_aufmass_unbekannte_liegenschaft_404(admin_client, objekt):
    r = admin_client.get(f"/api/property/properties/{uuid.uuid4()}/aufmass")
    assert r.status_code == 404


# --- Rechteweg -------------------------------------------------------------

@pytest.mark.django_db
def test_lesen_ohne_recht_403(client_with_role, objekt):
    c = client_with_role("MONTEUR")
    r = c.get(f"/api/property/properties/{objekt.id}/rooms")
    # MONTEUR hat kein property/LESEN (oder nur EIGENE → fail-closed). Beides 403.
    assert r.status_code == 403


@pytest.mark.django_db
def test_anlegen_ohne_recht_403(client_with_role, objekt):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"/api/property/properties/{objekt.id}/rooms",
        data=_raum_payload(), content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_aufbau_ohne_recht_403(client_with_role, admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    c = client_with_role("NUR_LESEN")
    r = c.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data=_aufbau_payload(), content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_patch_ohne_recht_403(client_with_role, admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    c = client_with_role("NUR_LESEN")
    r = c.patch(
        f"/api/property/rooms/{raum['id']}",
        data={"name": "Umbenannt"}, content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_ohne_login_abgelehnt(anonymous_client, objekt):
    r = anonymous_client.get(f"/api/property/properties/{objekt.id}/rooms")
    assert r.status_code in (401, 403)
