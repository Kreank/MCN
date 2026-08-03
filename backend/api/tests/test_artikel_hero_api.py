"""API-Tests der Hero-Parität des Artikelstamms (Migration 0042).

Deckt die neuen Felder im Artikel-Anlegen/Detail, die Steuercode-/Kostenstellen-
Validierung (422 statt 500), die VK-Tabelle (GET/PUT verkaufspreise) und den
primären Lieferantenbezug (PUT lieferant) ab.
"""
import uuid
from decimal import Decimal

import pytest

from .conftest import make_app_user
from db_core.services import artikel as artikel_service
from db_core.services import identity as identity_service


@pytest.fixture
def setup(admin_client):
    actor = make_app_user("Setup")
    return {"client": admin_client, "actor": actor}


def _create_article(client, **extra):
    body = {"article_number": f"A-{uuid.uuid4().hex[:8]}", "description": "Ventil",
            "unit": "Stk"}
    body.update(extra)
    return client.post("/api/pricing/articles", data=body,
                       content_type="application/json")


@pytest.mark.django_db
def test_create_mit_neuen_feldern(setup):
    r = _create_article(
        setup["client"], matchcode="VENT", manufacturer_type="Typ-X",
        min_order_quantity="5", quantity_step="5", delivery_time_days=3,
        tax_code="DE_19", price_unit=100, list_price="250.00",
    )
    assert r.status_code == 201, r.content
    b = r.json()
    assert b["matchcode"] == "VENT"
    assert b["manufacturer_type"] == "Typ-X"
    assert b["delivery_time_days"] == 3
    assert b["tax_code"] == "DE_19"
    assert b["price_unit"] == 100
    assert Decimal(b["min_order_quantity"]) == Decimal("5")


@pytest.mark.django_db
def test_create_unbekannter_tax_code_422(setup):
    r = _create_article(setup["client"], tax_code="XX_99")
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_unbekannte_kostenstelle_422(setup):
    r = _create_article(setup["client"], cost_center_id=str(uuid.uuid4()))
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_ungueltige_preiseinheit_422(setup):
    r = _create_article(setup["client"], price_unit=7)
    assert r.status_code == 422


@pytest.mark.django_db
def test_kostenstelle_im_detail(setup):
    # Kostenstelle über die Accounting-API anlegen (nicht duplizieren).
    cc = setup["client"].post(
        "/api/accounting/cost-centers",
        data={"code": f"KST-{uuid.uuid4().hex[:6]}", "label": "Werkstatt"},
        content_type="application/json",
    ).json()
    r = _create_article(setup["client"], cost_center_id=cc["id"])
    assert r.status_code == 201
    detail = setup["client"].get(f"/api/pricing/articles/{r.json()['id']}").json()
    assert detail["cost_center_id"] == cc["id"]
    assert "Werkstatt" in detail["cost_center_label"]


@pytest.mark.django_db
def test_lieferant_setzen_und_detail(setup):
    art = _create_article(setup["client"], price_unit=100).json()
    supplier = identity_service.create_organization(
        setup["actor"].id, legal_name="Großhandel AG", organization_type="COMPANY",
    )
    r = setup["client"].put(
        f"/api/pricing/articles/{art['id']}/lieferant",
        data={"supplier_party_id": str(supplier.id),
              "supplier_article_number": "GH-4711",
              "last_purchase_price": "200.00"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    b = r.json()
    assert b["supplier_party_id"] == str(supplier.id)
    assert b["supplier_name"] == "Großhandel AG"
    assert b["supplier_article_number"] == "GH-4711"
    assert Decimal(b["last_purchase_price"]) == Decimal("200.00")


@pytest.mark.django_db
def test_lieferant_unbekannte_partei_422(setup):
    art = _create_article(setup["client"]).json()
    r = setup["client"].put(
        f"/api/pricing/articles/{art['id']}/lieferant",
        data={"supplier_party_id": str(uuid.uuid4()),
              "supplier_article_number": "X"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_verkaufspreise_get_und_put(setup):
    art = _create_article(setup["client"], list_price="100.00").json()
    grp = artikel_service.create_sale_price_group(
        setup["actor"].id, name=f"G-{uuid.uuid4().hex[:6]}",
        calc_basis="LISTENPREIS", operator="AUFSCHLAG", percent_change="30.000",
    )
    # PUT: Tabelle setzen mit Überschreibung + Standard
    r = setup["client"].put(
        f"/api/pricing/articles/{art['id']}/verkaufspreise",
        data={"entries": [{"sale_price_group_id": str(grp.id),
                           "fixed_price": "140.00", "is_standard": True}]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    row = r.json()["groups"][0]
    assert row["computed_sale_price"] == "130.00"
    assert row["override_price"] == "140.00"
    assert row["effective_sale_price"] == "140.00"
    assert row["is_standard"] is True

    # GET liefert dasselbe
    g = setup["client"].get(f"/api/pricing/articles/{art['id']}/verkaufspreise").json()
    assert g["groups"][0]["effective_sale_price"] == "140.00"


@pytest.mark.django_db
def test_verkaufspreise_put_ohne_standard_422(setup):
    art = _create_article(setup["client"], list_price="100.00").json()
    grp = artikel_service.create_sale_price_group(
        setup["actor"].id, name=f"G-{uuid.uuid4().hex[:6]}",
        calc_basis="LISTENPREIS", operator="AUFSCHLAG", percent_change="10.000",
    )
    r = setup["client"].put(
        f"/api/pricing/articles/{art['id']}/verkaufspreise",
        data={"entries": [{"sale_price_group_id": str(grp.id), "is_standard": False}]},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_verkaufspreise_404(setup):
    r = setup["client"].get(f"/api/pricing/articles/{uuid.uuid4()}/verkaufspreise")
    assert r.status_code == 404


# --- Artikel kopieren (Hero „Kopieren") ------------------------------------

@pytest.mark.django_db
def test_copy_article_uebernimmt_felder_vk_und_lieferant(setup):
    client, actor = setup["client"], setup["actor"]
    src = _create_article(
        client, matchcode="QVENT", manufacturer_name="ACME",
        price_unit=100, list_price="250.00", tax_code="DE_19",
    ).json()

    # VK-Gruppe + Überschreibung + Standard am Quellartikel.
    grp = artikel_service.create_sale_price_group(
        actor.id, name=f"G-{uuid.uuid4().hex[:6]}",
        calc_basis="LISTENPREIS", operator="AUFSCHLAG", percent_change="30.000",
    )
    client.put(
        f"/api/pricing/articles/{src['id']}/verkaufspreise",
        data={"entries": [{"sale_price_group_id": str(grp.id),
                           "fixed_price": "333.00", "is_standard": True}]},
        content_type="application/json",
    )
    # Primärer Lieferantenbezug am Quellartikel.
    supplier = identity_service.create_organization(
        actor.id, legal_name="Kopie-Lieferant AG", organization_type="COMPANY",
    )
    client.put(
        f"/api/pricing/articles/{src['id']}/lieferant",
        data={"supplier_party_id": str(supplier.id),
              "supplier_article_number": "GH-COPY",
              "last_purchase_price": "180.00"},
        content_type="application/json",
    )

    neu_nr = f"COPY-{uuid.uuid4().hex[:8]}"
    r = client.post(
        f"/api/pricing/articles/{src['id']}/copy",
        data={"article_number": neu_nr}, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    b = r.json()
    # Neuer, eigenständiger Artikel mit neuer Nummer, Status AKTIV.
    assert b["article_number"] == neu_nr
    assert b["id"] != src["id"]
    assert b["status"] == "AKTIV"
    # Stammfelder inkl. price_unit übernommen.
    assert b["matchcode"] == "QVENT"
    assert b["manufacturer_name"] == "ACME"
    assert b["price_unit"] == 100
    assert b["tax_code"] == "DE_19"
    assert Decimal(b["list_price"]) == Decimal("250.00")
    # Lieferantenbezug mitkopiert (als MANUELL).
    assert b["supplier_party_id"] == str(supplier.id)
    assert b["supplier_name"] == "Kopie-Lieferant AG"
    assert b["supplier_article_number"] == "GH-COPY"
    assert Decimal(b["last_purchase_price"]) == Decimal("180.00")
    # VK-Überschreibung + Standard mitkopiert.
    vk = client.get(f"/api/pricing/articles/{b['id']}/verkaufspreise").json()
    row = next(g for g in vk["groups"] if g["sale_price_group_id"] == str(grp.id))
    assert row["override_price"] == "333.00"
    assert row["is_standard"] is True


@pytest.mark.django_db
def test_copy_article_uebernimmt_keine_gtin(setup):
    """`uq_article_gtin` ist eindeutig — die Kopie darf die GTIN nicht duplizieren
    (sonst IntegrityError/500). Sie startet ohne GTIN."""
    client = setup["client"]
    src = _create_article(client).json()
    client.put(f"/api/pricing/articles/{src['id']}",
               data={"gtin": "4006381333931"}, content_type="application/json")
    r = client.post(
        f"/api/pricing/articles/{src['id']}/copy",
        data={"article_number": f"GC-{uuid.uuid4().hex[:8]}"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["gtin"] is None


@pytest.mark.django_db
def test_copy_article_leere_nummer_wird_vergeben(setup):
    """Seit Migration 0149 ist die leere Nummer kein Fehler mehr, sondern der
    Normalfall: die DB vergibt die nächste freie (ART-#####)."""
    src = _create_article(setup["client"]).json()
    r = setup["client"].post(
        f"/api/pricing/articles/{src['id']}/copy",
        data={"article_number": "  "}, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["article_number"].startswith("ART-")


@pytest.mark.django_db
def test_copy_article_doppelte_nummer_422(setup):
    src = _create_article(setup["client"]).json()
    other = _create_article(setup["client"]).json()
    r = setup["client"].post(
        f"/api/pricing/articles/{src['id']}/copy",
        data={"article_number": other["article_number"]},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "vergeben" in r.json()["detail"]


@pytest.mark.django_db
def test_copy_article_unbekannte_quelle_404(setup):
    r = setup["client"].post(
        f"/api/pricing/articles/{uuid.uuid4()}/copy",
        data={"article_number": "X-COPY"}, content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_copy_article_ohne_recht_403(client_with_role, app_user):
    a = artikel_service.create_article(
        app_user.id, article_number="RIGHTS-1", description="X", unit="Stk",
    )
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"/api/pricing/articles/{a.id}/copy",
        data={"article_number": "RIGHTS-COPY"}, content_type="application/json",
    )
    assert r.status_code == 403


# --- Liste: Lieferantenname ohne N+1 ---------------------------------------

@pytest.mark.django_db
def test_liste_supplier_name_ohne_n_plus_1(setup):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client, actor = setup["client"], setup["actor"]
    supplier = identity_service.create_organization(
        actor.id, legal_name="Listen-Lieferant AG", organization_type="COMPANY",
    )

    def _mit_lieferant(nr):
        art = _create_article(client, description=f"NPLUS {nr}").json()
        client.put(
            f"/api/pricing/articles/{art['id']}/lieferant",
            data={"supplier_party_id": str(supplier.id),
                  "supplier_article_number": f"GH-{nr}",
                  "last_purchase_price": "10.00"},
            content_type="application/json",
        )

    for i in range(5):
        _mit_lieferant(i)

    # supplier_name steht korrekt in den Listen-Items.
    r = client.get("/api/pricing/articles?q=NPLUS&page_size=25")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 5
    assert all(it["supplier_name"] == "Listen-Lieferant AG" for it in items)

    # Query-Zahl hängt an der Seitengröße, nicht an der Zeilenzahl: eine Seite mit
    # 1 Artikel und eine mit 5 brauchen gleich viele Queries (kein N+1).
    def _queries(url):
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(url)
            assert resp.status_code == 200
        return len(ctx.captured_queries)

    q_eine = _queries("/api/pricing/articles?q=NPLUS&page_size=1")
    q_fuenf = _queries("/api/pricing/articles?q=NPLUS&page_size=25")
    assert q_eine == q_fuenf, (
        f"N+1 beim Lieferantennamen: {q_eine} (1 Zeile) vs {q_fuenf} (5 Zeilen)."
    )
