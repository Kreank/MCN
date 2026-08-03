"""Leistungen: Stammdaten, Status, Stückliste und Kalkulation.

Der Kern dieser Tests ist die Frage, wegen der man eine Stückliste überhaupt
pflegt: **Was kostet die Leistung?** Dazu gehört genauso, was passiert, wenn die
Antwort unvollständig ist — eine Summe, die eine preislose Position stillschweigend
als kostenlos führt, sähe aus wie ein Preis.
"""
import pytest

from db_core.services import artikel as artikel_service


@pytest.fixture
def vk_gruppe(app_user):
    """VK = Listenpreis (Aufschlag 0 %) — macht die Erwartungswerte lesbar."""
    return artikel_service.create_sale_price_group(
        app_user.id, name="Listenpreis 1:1", calc_basis="LISTENPREIS",
        operator="AUFSCHLAG", percent_change="0",
    )


def _artikel_mit_vk(app_user, vk_gruppe, *, beschreibung, listenpreis):
    art = artikel_service.create_article(
        app_user.id, description=beschreibung, unit="Stk", list_price=listenpreis,
    )
    artikel_service.set_verkaufspreise(
        app_user.id, article_id=art.id,
        entries=[{"sale_price_group_id": vk_gruppe.id, "fixed_price": None,
                  "is_standard": True}],
    )
    return art


@pytest.fixture
def leistung(app_user, vk_gruppe):
    """2 × Ziegel à 10,00 € + 30 min Monteur (60 €/h VK, 30 €/h Kosten).

    Erwartung: VK 20,00 + 30,00 = 50,00 €.
    """
    art = _artikel_mit_vk(
        app_user, vk_gruppe, beschreibung="Dachziegel", listenpreis="10.00",
    )
    wg = artikel_service.create_wage_group(
        app_user.id, name="Monteur", hourly_rate="60.00", cost_rate="30.00",
    )
    asm = artikel_service.create_assembly(
        app_user.id, name="Ziegel verlegen", unit="m²",
        components=[
            {"article_id": art.id, "quantity": "2.000"},
            {"wage_group_id": wg.id, "minutes": "30.00"},
        ],
    )
    return {"assembly": asm, "artikel": art, "wage_group": wg}


# --- Kalkulation -----------------------------------------------------------
@pytest.mark.django_db
def test_kalkulation_summiert_material_und_lohn(admin_client, leistung):
    r = admin_client.get(f"/api/pricing/assemblies/{leistung['assembly'].id}/kalkulation")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["material_vk"] == "20.00"
    assert body["lohn_vk"] == "30.00"
    assert body["vk_gesamt"] == "50.00"
    assert body["lohn_ek"] == "15.00"
    assert body["minuten_gesamt"] == "30.00"
    # Lohnanteil § 35a = der Lohnteil des Verkaufspreises.
    assert body["lohnanteil_vk"] == "30.00"


@pytest.mark.django_db
def test_marge_fehlt_ohne_einkaufspreise(admin_client, leistung):
    """Der Ziegel hat keinen Lieferantenbezug — sein EK ist unbekannt.

    Die Marge darf dann NICHT ausgewiesen werden: ohne Material-EK sähe sie
    deutlich besser aus, als sie ist.
    """
    r = admin_client.get(f"/api/pricing/assemblies/{leistung['assembly'].id}/kalkulation")
    body = r.json()
    assert body["vollstaendig"] is True       # der Verkaufspreis steht
    assert body["kosten_vollstaendig"] is False
    assert body["marge_prozent"] is None


@pytest.mark.django_db
def test_fehlender_vk_macht_die_summe_unvollstaendig(admin_client, app_user, leistung):
    """Ein Material ohne Verkaufspreis fliesst NICHT als 0,00 in die Summe."""
    ohne_preis = artikel_service.create_article(
        app_user.id, description="Ohne Preis", unit="Stk",
    )
    artikel_service.add_assembly_components(
        app_user.id, assembly_id=leistung["assembly"].id,
        components=[{"article_id": ohne_preis.id, "quantity": "1.000"}],
    )
    body = admin_client.get(
        f"/api/pricing/assemblies/{leistung['assembly'].id}/kalkulation"
    ).json()
    assert body["vollstaendig"] is False
    # Die bekannten Positionen bleiben in der Summe — sie wird nur nicht als
    # fertiger Preis ausgegeben (das entscheidet `vollstaendig`).
    assert body["vk_gesamt"] == "50.00"
    fehlende = [p for p in body["positionen"] if p["vk_summe"] is None]
    assert len(fehlende) == 1
    assert fehlende[0]["description"] == "Ohne Preis"


@pytest.mark.django_db
def test_lohn_ohne_kostensatz_rechnet_konservativ(admin_client, app_user, vk_gruppe):
    """Kostensatz NULL: es wird mit dem Verrechnungssatz gerechnet, nicht mit 0."""
    wg = artikel_service.create_wage_group(
        app_user.id, name="Ohne Kostensatz", hourly_rate="80.00",
    )
    asm = artikel_service.create_assembly(
        app_user.id, name="Nur Lohn", unit="Std",
        components=[{"wage_group_id": wg.id, "minutes": "60.00"}],
    )
    body = admin_client.get(f"/api/pricing/assemblies/{asm.id}/kalkulation").json()
    assert body["lohn_vk"] == "80.00"
    assert body["lohn_ek"] == "80.00"     # konservativ, nicht 0,00
    assert body["kosten_vollstaendig"] is False
    assert body["marge_prozent"] is None


@pytest.mark.django_db
def test_leere_stueckliste_ergibt_keinen_preis(admin_client, app_user):
    asm = artikel_service.create_assembly(app_user.id, name="Leer", unit="Stk")
    body = admin_client.get(f"/api/pricing/assemblies/{asm.id}/kalkulation").json()
    assert body["vk_gesamt"] == "0.00"
    assert body["positionen"] == []


# --- Stückliste ersetzen ---------------------------------------------------
@pytest.mark.django_db
def test_stueckliste_ersetzen_sortiert_neu(admin_client, leistung):
    """Umsortieren: dieselben Positionen, getauschte Reihenfolge."""
    asm_id = leistung["assembly"].id
    r = admin_client.put(
        f"/api/pricing/assemblies/{asm_id}/components",
        data={
            "components": [
                {"wage_group_id": str(leistung["wage_group"].id), "minutes": "30.00"},
                {"article_id": str(leistung["artikel"].id), "quantity": "2.000"},
            ]
        },
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    arten = [c["kind"] for c in r.json()["components"]]
    assert arten == ["LOHN", "MATERIAL"]
    # Positionsnummern folgen der Reihenfolge, lückenlos ab 1.
    assert [c["position"] for c in r.json()["components"]] == [1, 2]


@pytest.mark.django_db
def test_stueckliste_ersetzen_entfernt_positionen(admin_client, leistung):
    asm_id = leistung["assembly"].id
    r = admin_client.put(
        f"/api/pricing/assemblies/{asm_id}/components",
        data={"components": [
            {"article_id": str(leistung["artikel"].id), "quantity": "5.000"},
        ]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    komponenten = r.json()["components"]
    assert len(komponenten) == 1
    assert komponenten[0]["quantity"] == "5.000"


@pytest.mark.django_db
def test_stueckliste_darf_geleert_werden(admin_client, leistung):
    """Anders als beim Anhängen ist die leere Liste hier eine Aussage."""
    r = admin_client.put(
        f"/api/pricing/assemblies/{leistung['assembly'].id}/components",
        data={"components": []},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["components"] == []


@pytest.mark.django_db
def test_stueckliste_liefert_fremdschluessel_zurueck(admin_client, leistung):
    """Ohne article_id/wage_group_id könnte der Editor nichts zurückschicken."""
    r = admin_client.get(f"/api/pricing/assemblies/{leistung['assembly'].id}")
    material = [c for c in r.json()["components"] if c["kind"] == "MATERIAL"][0]
    lohn = [c for c in r.json()["components"] if c["kind"] == "LOHN"][0]
    assert material["article_id"] == str(leistung["artikel"].id)
    assert lohn["wage_group_id"] == str(leistung["wage_group"].id)


# --- Stammdaten und Status -------------------------------------------------
@pytest.mark.django_db
def test_stammdaten_aendern(admin_client, leistung):
    r = admin_client.put(
        f"/api/pricing/assemblies/{leistung['assembly'].id}",
        data={"name": "Ziegel neu verlegen", "unit": "Stk",
              "internal_name": "intern"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["name"] == "Ziegel neu verlegen"
    assert body["unit"] == "Stk"
    assert body["internal_name"] == "intern"


@pytest.mark.django_db
def test_doppelte_leistungsnummer_gibt_422(admin_client, app_user, leistung):
    andere = artikel_service.create_assembly(
        app_user.id, assembly_number="BELEGT-1", name="Andere", unit="Stk",
    )
    r = admin_client.put(
        f"/api/pricing/assemblies/{leistung['assembly'].id}",
        data={"assembly_number": andere.assembly_number},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "bereits vergeben" in r.json()["detail"]


@pytest.mark.django_db
def test_leere_pflichtangabe_gibt_422(admin_client, leistung):
    r = admin_client.put(
        f"/api/pricing/assemblies/{leistung['assembly'].id}",
        data={"name": "   "},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_status_umschalten(admin_client, leistung):
    asm_id = leistung["assembly"].id
    r = admin_client.post(
        f"/api/pricing/assemblies/{asm_id}/status",
        data={"status": "INAKTIV"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "INAKTIV"

    r = admin_client.post(
        f"/api/pricing/assemblies/{asm_id}/status",
        data={"status": "AKTIV"}, content_type="application/json",
    )
    assert r.json()["status"] == "AKTIV"


@pytest.mark.django_db
def test_unbekannter_status_gibt_422(admin_client, leistung):
    r = admin_client.post(
        f"/api/pricing/assemblies/{leistung['assembly'].id}/status",
        data={"status": "GELOESCHT"}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_unbekannte_leistung_gibt_404(admin_client):
    import uuid

    fehlt = uuid.uuid4()
    assert admin_client.put(
        f"/api/pricing/assemblies/{fehlt}", data={"name": "X"},
        content_type="application/json",
    ).status_code == 404
    assert admin_client.get(
        f"/api/pricing/assemblies/{fehlt}/kalkulation"
    ).status_code == 404
