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
