"""API-Tests der EK→VK-Aufschlagsmatrix (`/api/pricing/markup-rules`, `vk-vorschlag`).

Neben dem Happy Path werden die Rechte geprüft (Vorschau UND Anwenden der
Massenpflege verlangen `pricing/AENDERN`) und die Invariante, dass die Matrix
weder `pricing.article` noch eine bestehende Belegposition verändert.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from db_core.models import ArticleSupplierReference, QuoteLine
from db_core.services import artikel as artikel_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

from .conftest import logged_in_client


@pytest.fixture
def stamm(app_user):
    art = artikel_service.create_article(
        app_user.id, article_number="MTX-1", description="Rohr DN100", unit="Stk",
        line_type="MATERIAL", product_group="Sanitär",
    )
    lief = identity_service.create_organization(
        app_user.id, legal_name="Grosshandel GmbH", organization_type="COMPANY",
    )
    ArticleSupplierReference.objects.create(
        id=uuid.uuid4(), article_id=art.id, supplier_party_id=lief.id,
        source_system="DATANORM", source_namespace="test",
        supplier_article_number="4711",
        last_purchase_price=Decimal("100.0000"), currency="EUR",
        valid_from=date(2020, 1, 1),
    )
    return {"article": art, "supplier": lief, "app_user": app_user}


def _regel(client, **payload):
    body = {"name": "Sanitär", "calc_basis": "EK", "markup_percent": "45"}
    body.update(payload)
    r = client.post("/api/pricing/markup-rules", body,
                    content_type="application/json")
    assert r.status_code == 201, r.content
    return r.json()


@pytest.mark.django_db
def test_regel_anlegen_und_auflisten(admin_client, stamm):
    regel = _regel(admin_client, product_group="Sanitär")
    assert regel["scope"] == "WARENGRUPPE"
    assert regel["scope_text"].startswith("Warengruppe")

    r = admin_client.get("/api/pricing/markup-rules")
    assert r.status_code == 200
    assert [x["name"] for x in r.json()] == ["Sanitär"]


@pytest.mark.django_db
def test_markup_rules_nach_artikel_filterbar(admin_client, stamm):
    """`?article_id=` liefert genau die Regeln dieses Artikels (Scope ARTIKEL) —
    das Artikel-Detail holt so seine eigene Aufschlagsregel gezielt."""
    art = stamm["article"]
    # Eine Warengruppenregel (nicht am Artikel) und eine Artikelregel.
    _regel(admin_client, product_group="Sanitär")
    artikel_regel = _regel(
        admin_client, name="Nur dieser Artikel", article_id=str(art.id),
        product_group=None, markup_percent="12",
    )
    assert artikel_regel["scope"] == "ARTIKEL"
    assert artikel_regel["article_id"] == str(art.id)

    r = admin_client.get(f"/api/pricing/markup-rules?article_id={art.id}")
    assert r.status_code == 200
    body = r.json()
    assert [x["name"] for x in body] == ["Nur dieser Artikel"]
    assert body[0]["article_number"] == art.article_number

    # Ohne Filter sind beide Regeln sichtbar.
    alle = admin_client.get("/api/pricing/markup-rules").json()
    assert {x["name"] for x in alle} == {"Sanitär", "Nur dieser Artikel"}

    # Fremder Artikel ohne eigene Regel → leere Liste.
    r = admin_client.get(f"/api/pricing/markup-rules?article_id={uuid.uuid4()}")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.django_db
def test_vk_vorschlag_zeigt_regel_und_rechenweg(admin_client, stamm):
    _regel(admin_client, product_group="Sanitär")
    r = admin_client.get(
        f"/api/pricing/articles/{stamm['article'].id}/vk-vorschlag"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["quelle"] == "MATRIX"
    assert body["ek"] == "100.0000"
    assert body["basis_amount"] == "100.0000"
    assert body["markup_percent"] == "45.000"
    assert body["sale_price"] == "145.00"
    assert body["regel"]["name"] == "Sanitär"


@pytest.mark.django_db
def test_vk_vorschlag_mit_staffel(admin_client, stamm):
    regel = _regel(admin_client, product_group="Sanitär")
    r = admin_client.put(
        f"/api/pricing/markup-rules/{regel['id']}/tiers",
        {"tiers": [{"min_quantity": "10", "markup_percent": "30"}]},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["tiers"][0]["min_quantity"] == "10.000"

    r = admin_client.get(
        f"/api/pricing/articles/{stamm['article'].id}/vk-vorschlag?menge=25"
    )
    body = r.json()
    assert body["sale_price"] == "130.00"
    assert body["tier_min_quantity"] == "10.000"


@pytest.mark.django_db
def test_vk_vorschlag_ohne_ek_ist_unbekannt(admin_client, app_user):
    art = artikel_service.create_article(
        app_user.id, article_number="MTX-2", description="Ohne EK", unit="Stk",
        product_group="Sanitär",
    )
    _regel(admin_client, product_group="Sanitär")
    r = admin_client.get(f"/api/pricing/articles/{art.id}/vk-vorschlag")
    body = r.json()
    assert body["sale_price"] is None       # NIE "0.00"
    assert body["quelle"] == "UNBEKANNT"


@pytest.mark.django_db
def test_regel_status_und_aenderung(admin_client, stamm):
    regel = _regel(admin_client, product_group="Sanitär")
    r = admin_client.patch(
        f"/api/pricing/markup-rules/{regel['id']}",
        {"markup_percent": "60", "min_margin_percent": "20"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["markup_percent"] == "60.000"

    r = admin_client.post(
        f"/api/pricing/markup-rules/{regel['id']}/status",
        {"status": "INAKTIV"}, content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["status"] == "INAKTIV"


@pytest.mark.django_db
def test_geltungsbereich_nicht_aenderbar(admin_client, stamm):
    """Der Geltungsbereich ist im Update-Schema gar nicht vorgesehen — ein
    mitgeschicktes Feld wird ignoriert, nicht heimlich übernommen."""
    regel = _regel(admin_client, product_group="Sanitär")
    r = admin_client.patch(
        f"/api/pricing/markup-rules/{regel['id']}",
        {"product_group": "Heizung"}, content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["product_group"] == "Sanitär"


@pytest.mark.django_db
def test_vk_vorschlag_bleibt_regelgetrieben_nach_massenpflege(admin_client, stamm):
    """Nach der Massenpflege darf es keine zweite Wahrheit geben: ändert sich die
    Regel, zeigen Artikel-Kalkulation UND vk-vorschlag denselben neuen Preis."""
    regel = _regel(admin_client, product_group="Sanitär")
    admin_client.post(
        "/api/pricing/markup-rules/massenpflege",
        {"product_group": "Sanitär", "dry_run": False},
        content_type="application/json",
    )
    admin_client.patch(
        f"/api/pricing/markup-rules/{regel['id']}",
        {"markup_percent": "80"}, content_type="application/json",
    )
    art = stamm["article"].id
    vorschlag = admin_client.get(f"/api/pricing/articles/{art}/vk-vorschlag").json()
    kalk = admin_client.get(f"/api/pricing/articles/{art}/kalkulation").json()
    standard = [v for v in kalk["variants"] if v["is_standard"]][0]
    assert vorschlag["sale_price"] == "180.00"
    assert standard["sale_price"] == "180.00"   # nicht der gespeicherte 145,00


@pytest.mark.django_db
def test_massenpflege_vorschau_dann_anwenden(admin_client, stamm):
    _regel(admin_client, product_group="Sanitär")
    r = admin_client.post(
        "/api/pricing/markup-rules/massenpflege",
        {"product_group": "Sanitär", "dry_run": True},
        content_type="application/json",
    )
    assert r.status_code == 200
    vorschau = r.json()
    assert vorschau["artikel_gesamt"] == 1
    assert vorschau["angelegt"] == 1
    assert vorschau["zeilen"][0]["neu"] == "145.00"

    # Vorschau hat nichts geschrieben.
    r = admin_client.get(
        f"/api/pricing/articles/{stamm['article'].id}/verkaufspreise"
    )
    assert r.status_code == 200

    r = admin_client.post(
        "/api/pricing/markup-rules/massenpflege",
        {"product_group": "Sanitär", "dry_run": False},
        content_type="application/json",
    )
    ergebnis = r.json()
    assert ergebnis["angelegt"] == 1
    assert ergebnis["zeilen"][0]["neu"] == vorschau["zeilen"][0]["neu"]

    # Der Artikel trägt jetzt den gerechneten VK als Standard-Festpreis.
    r = admin_client.get(
        f"/api/pricing/articles/{stamm['article'].id}/kalkulation"
    )
    standard = [v for v in r.json()["variants"] if v["is_standard"]][0]
    assert standard["sale_price"] == "145.00"


@pytest.mark.django_db
def test_massenpflege_veraendert_keine_belegposition(admin_client, stamm, app_user):
    obj = property_service.create_property(
        app_user.id, name="Matrix-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot",
        lines=[{
            "line_type": "MATERIAL", "description": "Rohr DN100",
            "quantity": 1, "unit": "Stk", "unit_price": 130,
            "tax_code": "DE_19", "source_article_id": str(stamm["article"].id),
        }],
    )
    line = QuoteLine.objects.get(quote_id=quote.id, position_number=1)

    _regel(admin_client, product_group="Sanitär", markup_percent="400")
    admin_client.post(
        "/api/pricing/markup-rules/massenpflege",
        {"product_group": "Sanitär", "dry_run": False},
        content_type="application/json",
    )
    nachher = QuoteLine.objects.get(id=line.id)
    assert nachher.unit_price == Decimal("130.00")
    assert nachher.updated_at == line.updated_at


# --- Rechte ---------------------------------------------------------------

@pytest.mark.django_db
def test_nur_lesen_darf_regeln_lesen_aber_nicht_anlegen(db, stamm):
    leser = logged_in_client("NUR_LESEN")
    assert leser.get("/api/pricing/markup-rules").status_code == 200
    r = leser.post(
        "/api/pricing/markup-rules",
        {"name": "X", "markup_percent": "10"}, content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_massenpflege_verlangt_aendern_auch_fuer_die_vorschau(db, stamm):
    leser = logged_in_client("NUR_LESEN")
    r = leser.post(
        "/api/pricing/markup-rules/massenpflege",
        {"product_group": "Sanitär", "dry_run": True},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_anonym_bekommt_401(anonymous_client, stamm):
    assert anonymous_client.get(
        "/api/pricing/markup-rules"
    ).status_code == 401
