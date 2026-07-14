"""API-Vertrag für Bauteilkatalog (0090), Grundriss (0091) und Raum-Status.

Der Vertrag ist fix — das Frontend wird parallel dagegen gebaut. Geprüft werden
deshalb die **Feldnamen** und die beiden Invarianten, die über die Leitung sichtbar
sein müssen:

* `template_id` ist ein **Herkunftsvermerk**, der U-Wert eine **Kopie**: Wer den
  Katalog korrigiert, verschiebt kein bestehendes Aufmaß.
* Hat ein Raum einen Umriss, sind `floor_area_m2`/`perimeter_m` **Ergebnis**, nicht
  Eingabe (`kennzahlen.geometrie_quelle == "GEZEICHNET"`) — ein mitgeschickter
  Wert wird verworfen.

Rechteweg: Modul `property` (der Raum ist Objektstammdatum). NUR_LESEN darf lesen,
nicht schreiben.
"""
import uuid
from decimal import Decimal

import pytest

from db_core.services import property as property_service

# 5 m × 4 m in Millimetern, im Uhrzeigersinn.
RECHTECK = {"vertices": [
    {"x_mm": 0, "y_mm": 0},
    {"x_mm": 5000, "y_mm": 0},
    {"x_mm": 5000, "y_mm": 4000},
    {"x_mm": 0, "y_mm": 4000},
]}


@pytest.fixture
def objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Katalog-Objekt", property_type="EINFAMILIENHAUS",
        street="Feldweg", house_number="3", postal_code="10115", city="Berlin",
    )


def _post_raum(client, prop, **kwargs):
    daten = {
        "name": "Wohnzimmer", "floor_area_m2": "20.000", "room_height_m": "2.500",
        "indoor_temp_c": "20.0", "air_change_rate": "0.50",
    }
    daten.update(kwargs)
    r = client.post(
        f"/api/property/properties/{prop.id}/rooms",
        data=daten, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    return r.json()


def _put_grundriss(client, raum_id, body=None):
    return client.put(
        f"/api/property/rooms/{raum_id}/grundriss",
        data=RECHTECK if body is None else body, content_type="application/json",
    )


def _post_vorlage(client, **kwargs):
    daten = {"kind": "FLAECHE", "name": "Außenwand, API-Test",
             "default_surface_type": "AUSSENWAND"}
    daten.update(kwargs)
    r = client.post(
        "/api/property/component-templates", data=daten,
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    return r.json()


# --- Bauteilkatalog --------------------------------------------------------

@pytest.mark.django_db
def test_katalog_liste_ohne_u_werte(admin_client):
    r = admin_client.get("/api/property/component-templates?kind=OEFFNUNG")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body, "Der Seed-Katalog aus 0090 ist leer."
    assert all(t["kind"] == "OEFFNUNG" for t in body)
    # Normrecht: keine DIN-Tabellenwerte im Produkt.
    assert all(t["u_value"] is None for t in body)
    assert {"id", "kind", "name", "default_surface_type", "default_opening_type",
            "u_value", "note", "status", "sort_index"} <= set(body[0])


@pytest.mark.django_db
def test_katalog_anlegen_und_u_wert_nachtragen(admin_client):
    t = _post_vorlage(admin_client)
    assert t["u_value"] is None      # eine Vorlage OHNE Wert ist der Normalzustand
    assert t["status"] == "AKTIV"

    r = admin_client.patch(
        f"/api/property/component-templates/{t['id']}",
        data={"u_value": "1.400"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert Decimal(r.json()["u_value"]) == Decimal("1.400")


@pytest.mark.django_db
def test_katalog_stilllegen_statt_loeschen(admin_client):
    t = _post_vorlage(admin_client, kind="OEFFNUNG", name="Fenster, Auslauf",
                      default_surface_type=None, default_opening_type="FENSTER")
    admin_client.patch(
        f"/api/property/component-templates/{t['id']}",
        data={"status": "INAKTIV"}, content_type="application/json",
    )
    aktive = admin_client.get("/api/property/component-templates?kind=OEFFNUNG").json()
    assert t["id"] not in {x["id"] for x in aktive}
    # Lesbar bleibt sie: bestehende Aufmaße zeigen darauf ("aus: …").
    alle = admin_client.get(
        "/api/property/component-templates?kind=OEFFNUNG&nur_aktive=false"
    ).json()
    assert t["id"] in {x["id"] for x in alle}


@pytest.mark.django_db
def test_katalog_u_wert_wird_in_die_wand_kopiert(admin_client, objekt):
    """DIE Invariante über die Leitung: Kopie, kein Verweis."""
    t = _post_vorlage(admin_client, name="Außenwand, Kopiertest", u_value="2.800")
    raum = _post_raum(admin_client, objekt)

    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [{
            "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
            "gross_area_m2": "10.000", "template_id": t["id"],
        }], "openings": []},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    wand = r.json()["surfaces"][0]
    assert Decimal(wand["u_value"]) == Decimal("2.800")   # KOPIE in der Zeile
    assert wand["template_id"] == t["id"]                 # Herkunftsvermerk

    # Katalog korrigieren → das bestehende Aufmaß bleibt unberührt.
    admin_client.patch(
        f"/api/property/component-templates/{t['id']}",
        data={"u_value": "1.000"}, content_type="application/json",
    )
    wand = admin_client.get(f"/api/property/rooms/{raum['id']}").json()["surfaces"][0]
    assert Decimal(wand["u_value"]) == Decimal("2.800")


@pytest.mark.django_db
def test_katalog_unbekannte_vorlage_422(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [{
            "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
            "gross_area_m2": "10.000", "template_id": str(uuid.uuid4()),
        }], "openings": []},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "unbekannte Bauteilvorlage" in r.json()["detail"]


@pytest.mark.django_db
def test_katalog_unbekannte_vorlage_patch_404(admin_client):
    r = admin_client.patch(
        f"/api/property/component-templates/{uuid.uuid4()}",
        data={"u_value": "1.000"}, content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_katalog_anlegen_ohne_recht_403(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/property/component-templates",
        data={"kind": "FLAECHE", "name": "Verboten"},
        content_type="application/json",
    )
    assert r.status_code == 403


# --- Grundriss -------------------------------------------------------------

@pytest.mark.django_db
def test_grundriss_rechnet_flaeche_und_umfang(admin_client, objekt):
    raum = _post_raum(admin_client, objekt, floor_area_m2="99.000")
    assert raum["kennzahlen"]["geometrie_quelle"] == "EINGEGEBEN"

    r = _put_grundriss(admin_client, raum["id"])
    assert r.status_code == 200, r.content
    body = r.json()
    assert Decimal(body["floor_area_m2"]) == Decimal("20.000")
    assert Decimal(body["perimeter_m"]) == Decimal("18.000")
    assert body["kennzahlen"]["geometrie_quelle"] == "GEZEICHNET"
    assert [(v["idx"], v["x_mm"], v["y_mm"]) for v in body["vertices"]] == [
        (0, 0, 0), (1, 5000, 0), (2, 5000, 4000), (3, 0, 4000)
    ]


@pytest.mark.django_db
def test_grundriss_patch_flaeche_wird_verworfen(admin_client, objekt):
    """Wer zeichnet, misst nicht doppelt — der Client-Wert gewinnt NICHT."""
    raum = _post_raum(admin_client, objekt)
    _put_grundriss(admin_client, raum["id"])

    r = admin_client.patch(
        f"/api/property/rooms/{raum['id']}",
        data={"floor_area_m2": "99.000", "perimeter_m": "3.000"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert Decimal(r.json()["floor_area_m2"]) == Decimal("20.000")
    assert Decimal(r.json()["perimeter_m"]) == Decimal("18.000")


@pytest.mark.django_db
def test_grundriss_entarteter_umriss_422(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    faelle = [
        ([{"x_mm": 0, "y_mm": 0}, {"x_mm": 1000, "y_mm": 0}], "mindestens 3 Punkte"),
        ([{"x_mm": 0, "y_mm": 0}, {"x_mm": 1000, "y_mm": 0},
          {"x_mm": 0, "y_mm": 0}], "aufeinanderliegen"),
        ([{"x_mm": 0, "y_mm": 0}, {"x_mm": 1000, "y_mm": 0},
          {"x_mm": 3000, "y_mm": 0}], "umschließt keine Fläche"),
        # Positive Fläche (3 m²) UND überschlagen: die Prüfung darf sich nicht auf
        # „Fläche > 0" verlassen, sie muss die Kanten wirklich schneiden.
        ([{"x_mm": 0, "y_mm": 0}, {"x_mm": 4000, "y_mm": 0},
          {"x_mm": 1000, "y_mm": 3000}, {"x_mm": 3000, "y_mm": 3000}],
         "überschlägt sich"),
    ]
    for punkte, meldung in faelle:
        r = _put_grundriss(admin_client, raum["id"], {"vertices": punkte})
        assert r.status_code == 422, (punkte, r.content)
        assert meldung in r.json()["detail"], (punkte, r.json())


@pytest.mark.django_db
def test_grundriss_wand_auf_kante_und_fenster_an_position(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    _put_grundriss(admin_client, raum["id"])

    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [{
            "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
            "edge_index": 0, "u_value": "0.800",
        }], "openings": [{
            "surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
            "width_m": "1.500", "height_m": "1.200", "u_value": "1.300",
            "position_m": "3.500",
        }]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    wand = r.json()["surfaces"][0]
    # Kante 0 ist 5 m lang, der Raum 2,5 m hoch → 12,5 m² (vom Server gerechnet).
    assert Decimal(wand["gross_area_m2"]) == Decimal("12.500")
    assert wand["edge_index"] == 0
    assert Decimal(wand["edge_length_m"]) == Decimal("5.000")
    assert wand["area_is_derived"] is True    # „aus der Zeichnung berechnet"
    assert Decimal(r.json()["openings"][0]["position_m"]) == Decimal("3.500")


@pytest.mark.django_db
def test_raumhoehe_zieht_abgeleitete_wandflaeche_mit(admin_client, objekt):
    """Der stille Rechenfehler, gegen den 0093 gebaut ist — über die Leitung.

    2,50 → 2,80 m: Die gerechnete Wand wächst mit, die Giebel-Handeingabe nicht.
    """
    raum = _post_raum(admin_client, objekt)
    _put_grundriss(admin_client, raum["id"])
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [
            {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
             "label": "Süd", "edge_index": 0, "u_value": "0.800"},
            {"ref": "s2", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
             "label": "Giebel", "edge_index": 1, "gross_area_m2": "7.500",
             "u_value": "0.800"},
        ], "openings": []},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    vorher = {s["label"]: s for s in r.json()["surfaces"]}
    assert Decimal(vorher["Süd"]["gross_area_m2"]) == Decimal("12.500")
    assert vorher["Süd"]["area_is_derived"] is True
    assert vorher["Giebel"]["area_is_derived"] is False

    r = admin_client.patch(
        f"/api/property/rooms/{raum['id']}",
        data={"room_height_m": "2.800"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    nachher = {s["label"]: s for s in r.json()["surfaces"]}
    assert Decimal(nachher["Süd"]["gross_area_m2"]) == Decimal("14.000")   # 5,0 × 2,8
    assert Decimal(nachher["Giebel"]["gross_area_m2"]) == Decimal("7.500")  # unberührt
    # Und die Heizlast folgt: (14,0 + 7,5) × 0,8 × 32 K = 550,4 W
    assert Decimal(
        r.json()["kennzahlen"]["wall_area_gross_m2"]
    ) == Decimal("21.500")


@pytest.mark.django_db
def test_neuberechnung_unter_die_fenster_422(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    _put_grundriss(admin_client, raum["id"])
    admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [{
            "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
            "label": "Südwand", "edge_index": 0, "u_value": "0.800",
        }], "openings": [{
            "surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
            "width_m": "4.800", "height_m": "2.500", "u_value": "1.300",
        }]},
        content_type="application/json",
    )
    # 2,0 m Raumhöhe → die Wand wäre nur noch 10,0 m² groß, das Fenster misst 12,0.
    r = admin_client.patch(
        f"/api/property/rooms/{raum['id']}",
        data={"room_height_m": "2.000"}, content_type="application/json",
    )
    assert r.status_code == 422, r.content
    detail = r.json()["detail"]
    assert "Südwand" in detail
    assert "passen nicht mehr hinein" in detail


@pytest.mark.django_db
def test_grundriss_oeffnung_passt_nicht_in_kante_422(admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    _put_grundriss(admin_client, raum["id"])
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [{
            "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
            "edge_index": 0, "u_value": "0.800",
        }], "openings": [{
            "surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
            "width_m": "1.500", "height_m": "1.200", "position_m": "4.000",
        }]},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "passt nicht in ihre Kante" in r.json()["detail"]


@pytest.mark.django_db
def test_grundriss_oeffnung_ohne_position_bleibt_gueltig(admin_client, objekt):
    """Fehlende Lage heißt unbekannt — nicht „bei 0 m", und nicht „zählt nicht"."""
    raum = _post_raum(admin_client, objekt)
    _put_grundriss(admin_client, raum["id"])
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [{
            "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
            "edge_index": 0, "u_value": "0.800",
        }], "openings": [{
            "surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
            "width_m": "2.000", "height_m": "1.000", "u_value": "1.300",
        }]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["openings"][0]["position_m"] is None
    assert Decimal(body["kennzahlen"]["opening_area_m2"]) == Decimal("2.000")
    # Sie zählt voll in die Heizlast (die Innentemperatur steht, die Außentemperatur
    # fehlt am Objekt → der Grund wird benannt, die Fläche ist trotzdem da).
    assert Decimal(body["surfaces"][0]["net_area_m2"]) == Decimal("10.500")


@pytest.mark.django_db
def test_decke_auf_einer_kante_422(admin_client, objekt):
    """Der Umriss ist die Draufsicht — seine Kanten sind die senkrechten Bauteile.

    Ohne diese Grenze bekäme die Decke `Kantenlänge × Raumhöhe` = 12,50 m² statt
    ihrer 20 m² — und wüchse als abgeleitete Fläche fortan mit der Raumhöhe.
    Das muss ein 422 sein, kein 500 aus dem CHECK.
    """
    raum = _post_raum(admin_client, objekt)
    _put_grundriss(admin_client, raum["id"])
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [{
            "ref": "s1", "surface_type": "DECKE", "adjacent": "UNBEHEIZT",
            "edge_index": 0, "temp_factor": "0.50",
        }], "openings": []},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "kann nicht auf einer Kante" in r.json()["detail"]

    # Gegenprobe: Decke OHNE Kante ist ein ganz normales Bauteil.
    r = admin_client.put(
        f"/api/property/rooms/{raum['id']}/aufbau",
        data={"surfaces": [{
            "ref": "s1", "surface_type": "DECKE", "adjacent": "UNBEHEIZT",
            "gross_area_m2": "20.000", "u_value": "0.300", "temp_factor": "0.50",
        }], "openings": []},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    decke = r.json()["surfaces"][0]
    assert decke["edge_index"] is None
    assert decke["area_is_derived"] is False
    assert Decimal(decke["gross_area_m2"]) == Decimal("20.000")


@pytest.mark.django_db
def test_grundriss_ohne_recht_403(client_with_role, admin_client, objekt):
    raum = _post_raum(admin_client, objekt)
    c = client_with_role("NUR_LESEN")
    assert _put_grundriss(c, raum["id"]).status_code == 403


@pytest.mark.django_db
def test_grundriss_unbekannter_raum_404(admin_client):
    assert _put_grundriss(admin_client, uuid.uuid4()).status_code == 404


# --- Raum stilllegen -------------------------------------------------------

@pytest.mark.django_db
def test_raum_stilllegen(admin_client, objekt):
    a = _post_raum(admin_client, objekt, name="Bleibt")
    b = _post_raum(admin_client, objekt, name="Weggefallen", floor_area_m2="10.000")

    r = admin_client.patch(
        f"/api/property/rooms/{b['id']}",
        data={"status": "INAKTIV"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "INAKTIV"

    liste = admin_client.get(f"/api/property/properties/{objekt.id}/rooms").json()
    assert [x["id"] for x in liste] == [a["id"]]

    alle = admin_client.get(
        f"/api/property/properties/{objekt.id}/rooms?mit_inaktiven=true"
    ).json()
    assert {x["id"] for x in alle} == {a["id"], b["id"]}

    # Einzeln bleibt der stillgelegte Raum abrufbar (sonst nicht reaktivierbar).
    einzeln = admin_client.get(f"/api/property/rooms/{b['id']}").json()
    assert einzeln["status"] == "INAKTIV"

    # Und er zählt nicht mehr in die Gebäudesummen.
    summe = admin_client.get(f"/api/property/properties/{objekt.id}/aufmass").json()
    assert summe["raeume_anzahl"] == 1
    assert Decimal(summe["flaeche_m2"]) == Decimal("20.000")
