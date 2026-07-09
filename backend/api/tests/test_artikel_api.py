"""API-Tests der Artikel-/Leistungs-Endpoints (read-only)."""
import uuid

import pytest

from db_core.services import artikel as artikel_service


@pytest.fixture
def seeded(app_user):
    mat = artikel_service.create_article(
        app_user.id, article_number="MAT-1", description="Dachziegel", unit="Stk",
        line_type="MATERIAL", list_price="2.40",
    )
    artikel_service.create_article(
        app_user.id, article_number="FAH-1", description="Anfahrt", unit="Fahrt",
        line_type="FAHRT", list_price="35.00",
    )
    wg = artikel_service.create_wage_group(
        app_user.id, name="Monteur", hourly_rate="58.00",
    )
    asm = artikel_service.create_assembly(
        app_user.id, assembly_number="LEI-1", name="Ziegel verlegen", unit="m²",
        components=[
            {"article_id": mat.id, "quantity": "12.000"},
            {"wage_group_id": wg.id, "minutes": "45.00"},
        ],
    )
    return {"app_user": app_user, "mat": mat, "assembly": asm}


@pytest.mark.django_db
def test_artikel_liste(admin_client, seeded):
    r = admin_client.get("/api/pricing/articles")
    assert r.status_code == 200
    assert r.json()["total"] == 2


@pytest.mark.django_db
def test_artikel_typfilter(admin_client, seeded):
    r = admin_client.get("/api/pricing/articles?line_type=FAHRT")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["article_number"] == "FAH-1"


@pytest.mark.django_db
def test_artikel_suche(admin_client, seeded):
    r = admin_client.get("/api/pricing/articles?q=Dachziegel")
    assert r.json()["items"][0]["description"] == "Dachziegel"


@pytest.mark.django_db
def test_artikel_detail(admin_client, seeded):
    r = admin_client.get(f"/api/pricing/articles/{seeded['mat'].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["article_number"] == "MAT-1"
    assert body["list_price"] == "2.40"


@pytest.mark.django_db
def test_artikel_detail_404(admin_client, seeded):
    r = admin_client.get(f"/api/pricing/articles/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_leistung_liste(admin_client, seeded):
    r = admin_client.get("/api/pricing/assemblies")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["assembly_number"] == "LEI-1"


@pytest.mark.django_db
def test_leistung_detail_mit_stueckliste(admin_client, seeded):
    r = admin_client.get(f"/api/pricing/assemblies/{seeded['assembly'].id}")
    assert r.status_code == 200
    comps = r.json()["components"]
    assert len(comps) == 2
    assert comps[0]["kind"] == "MATERIAL"
    assert comps[0]["description"] == "Dachziegel"
    assert comps[1]["kind"] == "LOHN"
    assert comps[1]["description"] == "Monteur"


@pytest.mark.django_db
def test_leistung_detail_404(admin_client, seeded):
    r = admin_client.get(f"/api/pricing/assemblies/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_kalkulation_endpoint(admin_client, seeded):
    mat = seeded["mat"]
    grp = artikel_service.create_sale_price_group(
        seeded["app_user"].id, name="Auf50", calc_basis="LISTENPREIS",
        operator="AUFSCHLAG", percent_change="50.000",
    )
    artikel_service.set_article_sale_price(
        seeded["app_user"].id, article_id=mat.id, sale_price_group_id=grp.id,
        is_standard=True,
    )
    r = admin_client.get(f"/api/pricing/articles/{mat.id}/kalkulation")
    assert r.status_code == 200
    body = r.json()
    assert body["list_price"] == "2.40"
    assert body["variants"][0]["sale_price"] == "3.60"  # 2,40 + 50 %


@pytest.mark.django_db
def test_kalkulation_404(admin_client, db):
    r = admin_client.get(f"/api/pricing/articles/{uuid.uuid4()}/kalkulation")
    assert r.status_code == 404


# --- Schreibende Endpoints -------------------------------------------------

@pytest.mark.django_db
def test_create_article_happy(admin_client, db):
    r = admin_client.post(
        "/api/pricing/articles",
        data={
            "article_number": "NEU-1", "description": "Dämmplatte",
            "unit": "m²", "line_type": "MATERIAL", "list_price": "9.995",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["article_number"] == "NEU-1"
    # list_price wird auf 2 Nachkommastellen quantisiert (9,995 → 10,00).
    assert body["list_price"] == "10.00"


@pytest.mark.django_db
def test_create_article_ungueltiger_line_type_422(admin_client, db):
    r = admin_client.post(
        "/api/pricing/articles",
        data={
            "article_number": "X", "description": "Y", "unit": "Stk",
            "line_type": "FALSCH",
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_article_ohne_recht_403(client_with_role, db):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/pricing/articles",
        data={"article_number": "X", "description": "Y", "unit": "Stk"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_create_wage_group_happy(admin_client, db):
    r = admin_client.post(
        "/api/pricing/wage_groups",
        data={"name": "Geselle", "hourly_rate": "52.00"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["name"] == "Geselle"


@pytest.mark.django_db
def test_create_wage_group_ungueltige_art_422(admin_client, db):
    r = admin_client.post(
        "/api/pricing/wage_groups",
        data={"name": "X", "hourly_rate": "10.00", "kind": "FALSCH"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_assembly_happy(admin_client, seeded):
    r = admin_client.post(
        "/api/pricing/assemblies",
        data={
            "assembly_number": "LEI-NEU", "name": "Platte setzen", "unit": "m²",
            "components": [
                {"article_id": str(seeded["mat"].id), "quantity": "3.000"},
            ],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["assembly_number"] == "LEI-NEU"
    assert len(body["components"]) == 1
    assert body["components"][0]["kind"] == "MATERIAL"


@pytest.mark.django_db
def test_create_assembly_position_ohne_menge_422(admin_client, seeded):
    """Material-Position ohne quantity → fachlicher Fehler (kein DB-500)."""
    r = admin_client.post(
        "/api/pricing/assemblies",
        data={
            "assembly_number": "LEI-X", "name": "Kaputt", "unit": "m²",
            "components": [{"article_id": str(seeded["mat"].id)}],
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_sale_price_group_happy(admin_client, db):
    r = admin_client.post(
        "/api/pricing/sale_price_groups",
        data={
            "name": "Aufschlag 30", "calc_basis": "EK",
            "operator": "AUFSCHLAG", "percent_change": "30.000",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["name"] == "Aufschlag 30"


@pytest.mark.django_db
def test_create_sale_price_group_beide_werte_422(admin_client, db):
    """percent_change UND amount_change gesetzt verletzt das XOR → 422."""
    r = admin_client.post(
        "/api/pricing/sale_price_groups",
        data={
            "name": "Kaputt", "percent_change": "10.000", "amount_change": "5.00",
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_set_article_sale_price_happy(admin_client, seeded):
    grp = artikel_service.create_sale_price_group(
        seeded["app_user"].id, name="Auf20", calc_basis="LISTENPREIS",
        operator="AUFSCHLAG", percent_change="20.000",
    )
    r = admin_client.put(
        f"/api/pricing/articles/{seeded['mat'].id}/sale_price",
        data={"sale_price_group_id": str(grp.id), "is_standard": True},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["is_standard"] is True
    assert body["sale_price_group_id"] == str(grp.id)


@pytest.mark.django_db
def test_set_article_sale_price_beide_werte_422(admin_client, seeded):
    r = admin_client.put(
        f"/api/pricing/articles/{seeded['mat'].id}/sale_price",
        data={"fixed_price": "5.00", "sale_price_group_id": str(uuid.uuid4())},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_set_article_sale_price_unbekannter_artikel_404(admin_client, db):
    r = admin_client.put(
        f"/api/pricing/articles/{uuid.uuid4()}/sale_price",
        data={"fixed_price": "5.00"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_set_article_sale_price_ohne_recht_403(client_with_role, seeded):
    c = client_with_role("NUR_LESEN")
    r = c.put(
        f"/api/pricing/articles/{seeded['mat'].id}/sale_price",
        data={"fixed_price": "5.00"},
        content_type="application/json",
    )
    assert r.status_code == 403


# --- Stammdaten-Auswahllisten (Lohn-/VK-Gruppen) ---------------------------

@pytest.mark.django_db
def test_wage_groups_liste(admin_client, seeded):
    r = admin_client.get("/api/pricing/wage_groups")
    assert r.status_code == 200
    body = r.json()
    namen = {g["name"] for g in body}
    assert "Monteur" in namen
    # nur aktive standardmäßig, alle Felder vorhanden
    g = next(g for g in body if g["name"] == "Monteur")
    assert g["status"] == "AKTIV"
    assert g["kind"] == "LOHN"
    assert g["hourly_rate"] == "58.00"


@pytest.mark.django_db
def test_wage_groups_ohne_recht_403(client_with_role, seeded):
    """MONTEUR hat kein pricing-Recht → 403."""
    c = client_with_role("MONTEUR")
    r = c.get("/api/pricing/wage_groups")
    assert r.status_code == 403


@pytest.mark.django_db
def test_sale_price_groups_liste_und_filter(admin_client, seeded):
    grp = artikel_service.create_sale_price_group(
        seeded["app_user"].id, name="Auf40", calc_basis="EK",
        operator="AUFSCHLAG", percent_change="40.000",
    )
    r = admin_client.get("/api/pricing/sale_price_groups")
    assert r.status_code == 200
    ids = {g["id"] for g in r.json()}
    assert str(grp.id) in ids
    # Statusfilter: INAKTIV liefert die AKTIVE Gruppe nicht.
    r2 = admin_client.get("/api/pricing/sale_price_groups?status=INAKTIV")
    assert str(grp.id) not in {g["id"] for g in r2.json()}


@pytest.mark.django_db
def test_sale_price_groups_ohne_recht_403(client_with_role, seeded):
    c = client_with_role("MONTEUR")
    r = c.get("/api/pricing/sale_price_groups")
    assert r.status_code == 403


# --- Positions-Editor für Leistungen (Stückliste erweitern) ----------------

@pytest.mark.django_db
def test_add_assembly_components_happy(admin_client, seeded):
    """Weitere Position anhängen: Stückliste wächst, Position wird fortgezählt."""
    asm = seeded["assembly"]
    r = admin_client.post(
        f"/api/pricing/assemblies/{asm.id}/components",
        data={"components": [
            {"article_id": str(seeded["mat"].id), "quantity": "5.000"},
        ]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    comps = r.json()["components"]
    # Seeded: 2 Positionen; jetzt 3, die neue hinten (position 3).
    assert len(comps) == 3
    assert [c["position"] for c in comps] == [1, 2, 3]
    assert comps[2]["kind"] == "MATERIAL"


@pytest.mark.django_db
def test_add_assembly_components_xor_422(admin_client, seeded):
    """Position mit Material UND Lohn verletzt das XOR → 422 (kein DB-500)."""
    r = admin_client.post(
        f"/api/pricing/assemblies/{seeded['assembly'].id}/components",
        data={"components": [{"article_id": str(seeded["mat"].id)}]},  # ohne quantity
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_add_assembly_components_unbekannter_artikel_422(admin_client, seeded):
    """Unbekannter Artikel-FK → sauberer 422 (Vorab-Validierung)."""
    r = admin_client.post(
        f"/api/pricing/assemblies/{seeded['assembly'].id}/components",
        data={"components": [{"article_id": str(uuid.uuid4()), "quantity": "1.000"}]},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_add_assembly_components_unbekannte_leistung_404(admin_client, seeded):
    r = admin_client.post(
        f"/api/pricing/assemblies/{uuid.uuid4()}/components",
        data={"components": [{"article_id": str(seeded["mat"].id), "quantity": "1.000"}]},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_add_assembly_components_ohne_recht_403(client_with_role, seeded):
    """TECHNISCHE_LEITUNG hat pricing.LESEN, aber nicht AENDERN → 403."""
    c = client_with_role("TECHNISCHE_LEITUNG")
    r = c.post(
        f"/api/pricing/assemblies/{seeded['assembly'].id}/components",
        data={"components": [{"article_id": str(seeded["mat"].id), "quantity": "1.000"}]},
        content_type="application/json",
    )
    assert r.status_code == 403
