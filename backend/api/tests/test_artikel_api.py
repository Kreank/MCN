"""API-Tests der Artikel-/Leistungs-Endpoints (read-only)."""
import uuid
from decimal import Decimal

import pytest
from .conftest import logged_in_client

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
    assert Decimal(body["list_price"]) == Decimal("2.40")   # 4 NK seit Migration 0039


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
    assert Decimal(body["list_price"]) == Decimal("2.40")   # 4 NK seit Migration 0039
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
    # Der Listenpreis wird auf VIER Nachkommastellen quantisiert (Migration 0039).
    # Frueher rundete die API auf zwei und machte aus 9,995 ein 10,00 — bei einem
    # Artikel mit Preiseinheit 100 waere das ein Fehler von einem halben Prozent,
    # der sich in jeden daraus abgeleiteten Verkaufspreis fortpflanzt.
    assert Decimal(body["list_price"]) == Decimal("9.9950")


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


@pytest.mark.django_db
def test_suche_blendet_inaktive_artikel_aus(admin_client, app_user):
    """Ein deaktivierter Artikel darf nicht wieder im Angebot landen.

    Gelöscht werden kann er nicht (trg_article_no_delete) — also muss die Suche
    ihn ausblenden, sonst hat das Deaktivieren keine Wirkung.
    """
    from db_core.services import artikel as artikel_service

    aktiv = artikel_service.create_article(
        app_user.id, article_number="SUCH-AKTIV", description="Sichtbar", unit="Stk"
    )
    inaktiv = artikel_service.create_article(
        app_user.id, article_number="SUCH-INAKTIV", description="Ausrangiert", unit="Stk"
    )
    artikel_service.set_article_status(
        app_user.id, article_id=inaktiv.id, status="INAKTIV"
    )

    nummern = lambda r: {i["article_number"] for i in r.json()["items"]}

    r = admin_client.get("/api/pricing/articles?q=SUCH-")
    assert r.status_code == 200
    assert nummern(r) == {"SUCH-AKTIV"}, "Inaktiver Artikel darf nicht erscheinen."

    # Wer ihn ausdrücklich sehen will, bekommt ihn.
    r2 = admin_client.get("/api/pricing/articles?q=SUCH-&status=INAKTIV")
    assert nummern(r2) == {"SUCH-INAKTIV"}
    r3 = admin_client.get("/api/pricing/articles?q=SUCH-&status=ALLE")
    assert nummern(r3) == {"SUCH-AKTIV", "SUCH-INAKTIV"}


# --- Bezugsquelle: Bestellkatalog vs. Ersatzteile ---------------------------

def _anbindung(app_user, namespace, kind):
    """Legt Lieferant + Anbindung an und gibt die Party zurück."""
    import uuid as _uuid

    from db_core.db_context import business_transaction
    from db_core.models import Party, SupplierConnection

    with business_transaction(app_user.id):
        party = Party.objects.create(
            id=_uuid.uuid4(), party_type="ORGANIZATION",
            display_name=f"Lieferant {namespace}", status="ACTIVE", version=1,
        )
        SupplierConnection.objects.create(
            id=_uuid.uuid4(), supplier_party_id=party.id, source_system="DATANORM",
            source_namespace=namespace, label=f"Lieferant {namespace}",
            status="ACTIVE", connection_kind=kind,
            version=1,
        )
    return party


def _referenz(app_user, article, party, namespace):
    import uuid as _uuid
    from datetime import date

    from db_core.db_context import business_transaction
    from db_core.models import ArticleSupplierReference

    with business_transaction(app_user.id):
        ArticleSupplierReference.objects.create(
            id=_uuid.uuid4(), article_id=article.id, supplier_party_id=party.id,
            source_system="DATANORM", source_namespace=namespace,
            supplier_article_number=article.article_number,
            last_purchase_price="1.0000", currency="EUR", valid_from=date.today(),
        )


@pytest.mark.django_db
def test_bezugsquelle_trennt_katalog_von_ersatzteilen(admin_client, app_user):
    """Ein Herstellerersatzteil darf nicht in der Angebotssuche auftauchen.

    Der Angebotseditor fragt `bezugsquelle=GROSSHAENDLER` an — was nur beim
    Hersteller direkt zu bekommen ist, gehört dort nicht hin.
    """
    from db_core.services import artikel as artikel_service

    gh = _anbindung(app_user, "test-gh", "GROSSHAENDLER")
    he = _anbindung(app_user, "test-he", "HERSTELLER")

    katalog = artikel_service.create_article(
        app_user.id, article_number="BQ-KATALOG", description="BQTest Rohrstueck",
        unit="Stk",
    )
    ersatzteil = artikel_service.create_article(
        app_user.id, article_number="BQ-ERSATZ", description="BQTest Mikroschalter",
        unit="Stk",
    )
    eigen = artikel_service.create_article(
        app_user.id, article_number="BQ-EIGEN", description="BQTest Pauschale",
        unit="psch",
    )
    _referenz(app_user, katalog, gh, "test-gh")
    _referenz(app_user, ersatzteil, he, "test-he")
    # `eigen` bekommt bewusst keine Lieferantenreferenz.

    def nummern(url):
        r = admin_client.get(url)
        assert r.status_code == 200, r.content
        return {i["article_number"] for i in r.json()["items"]}

    alle = nummern("/api/pricing/articles?q=BQTest")
    assert alle == {"BQ-KATALOG", "BQ-ERSATZ", "BQ-EIGEN"}

    gross = nummern("/api/pricing/articles?q=BQTest&bezugsquelle=GROSSHAENDLER")
    assert "BQ-ERSATZ" not in gross, "Ersatzteil darf nicht im Angebot erscheinen."
    # Eigene Artikel ohne Lieferant bleiben beschaffbar.
    assert gross == {"BQ-KATALOG", "BQ-EIGEN"}

    hersteller = nummern("/api/pricing/articles?q=BQTest&bezugsquelle=HERSTELLER")
    assert "BQ-KATALOG" not in hersteller
    assert "BQ-ERSATZ" in hersteller


@pytest.mark.django_db
def test_unbekannte_bezugsquelle_422(admin_client, db):
    r = admin_client.get("/api/pricing/articles?bezugsquelle=QUATSCH")
    assert r.status_code == 422
    assert "Bezugsquelle" in r.json()["detail"]


@pytest.mark.django_db
def test_inaktive_anbindung_zaehlt_nicht(admin_client, app_user):
    """Eine deaktivierte Anbindung (z. B. ein Probeimport) darf ihre Artikel nicht
    weiter als beschaffbar ausweisen."""
    from db_core.db_context import business_transaction
    from db_core.models import SupplierConnection
    from db_core.services import artikel as artikel_service

    gh = _anbindung(app_user, "test-alt", "GROSSHAENDLER")
    a = artikel_service.create_article(
        app_user.id, article_number="BQ-ALT", description="BQAlt Ware", unit="Stk"
    )
    _referenz(app_user, a, gh, "test-alt")
    with business_transaction(app_user.id):
        SupplierConnection.objects.filter(source_namespace="test-alt").update(
            status="INACTIVE"
        )

    r = admin_client.get("/api/pricing/articles?q=BQAlt&bezugsquelle=GROSSHAENDLER")
    assert {i["article_number"] for i in r.json()["items"]} == set()


# --- Bearbeiten, Historie, Stammdaten-Übernahme -----------------------------

@pytest.mark.django_db
def test_artikel_bearbeiten_und_historie(admin_client, app_user):
    from db_core.services import artikel as artikel_service

    a = artikel_service.create_article(
        app_user.id, article_number="UPD-1", description="Alt", unit="Stk"
    )
    r = admin_client.put(
        f"/api/pricing/articles/{a.id}",
        data={"description": "Neu", "manufacturer_name": "ACME", "list_price": "0.1290"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["description"] == "Neu"
    assert Decimal(body["list_price"]) == Decimal("0.1290")   # 4 NK erhalten

    hist = admin_client.get(f"/api/pricing/articles/{a.id}/historie").json()
    assert hist, "Die Aenderung muss in der Audit-Spur stehen."
    felder = {f["feld"] for e in hist for f in e["felder"]}
    assert "description" in felder
    eintrag = hist[0]["felder"]
    diff = {f["feld"]: (f["vorher"], f["nachher"]) for f in eintrag}
    assert diff["description"] == ("Alt", "Neu")


@pytest.mark.django_db
def test_artikelnummer_duplikat_422(admin_client, app_user):
    from db_core.services import artikel as artikel_service

    artikel_service.create_article(
        app_user.id, article_number="DUP-A", description="A", unit="Stk"
    )
    b = artikel_service.create_article(
        app_user.id, article_number="DUP-B", description="B", unit="Stk"
    )
    r = admin_client.put(
        f"/api/pricing/articles/{b.id}",
        data={"article_number": "DUP-A"}, content_type="application/json",
    )
    assert r.status_code == 422
    assert "bereits vergeben" in r.json()["detail"]


@pytest.mark.django_db
def test_ungueltige_gtin_422(admin_client, app_user):
    from db_core.services import artikel as artikel_service

    a = artikel_service.create_article(
        app_user.id, article_number="GTIN-1", description="X", unit="Stk"
    )
    # 4024074403976 ist gueltig, 4024074403977 nicht (falsche Pruefziffer).
    ok = admin_client.put(
        f"/api/pricing/articles/{a.id}",
        data={"gtin": "4024074403976"}, content_type="application/json",
    )
    assert ok.status_code == 200, ok.content

    schlecht = admin_client.put(
        f"/api/pricing/articles/{a.id}",
        data={"gtin": "4024074403977"}, content_type="application/json",
    )
    assert schlecht.status_code == 422
    assert "Prüfziffer" in schlecht.json()["detail"]


@pytest.mark.django_db
def test_stammdaten_uebernahme_braucht_pricing_aendern(admin_client, app_user):
    """Wer ein Angebot schreiben darf, darf nicht den Artikelstamm umschreiben.

    In der Startmatrix haelt jede Rolle mit `invoicing/AENDERN` auch
    `pricing/AENDERN` (BUCHHALTUNG, TECHNISCHE_LEITUNG). Damit der Test etwas
    beweist, wird BUCHHALTUNG das pricing-Recht gezielt entzogen: das Angebot
    darf sie weiter schreiben, den Stamm nicht mehr anfassen.
    """
    from db_core.db_context import business_transaction
    from db_core.models import RolePermission
    from db_core.services import artikel as artikel_service

    a = artikel_service.create_article(
        app_user.id, article_number="UEB-1", description="Original", unit="Stk"
    )
    with business_transaction(app_user.id):
        RolePermission.objects.filter(
            role_id="BUCHHALTUNG", module="pricing", action="AENDERN"
        ).update(allowed=False)

    buchhaltung = logged_in_client("BUCHHALTUNG")
    r = buchhaltung.post(
        f"/api/pricing/articles/{a.id}/stammdaten-uebernehmen",
        data={"description": "Umgeschrieben"}, content_type="application/json",
    )
    assert r.status_code == 403
    a.refresh_from_db()
    assert a.description == "Original"

    # Mit dem passenden Recht geht es.
    ok = admin_client.post(
        f"/api/pricing/articles/{a.id}/stammdaten-uebernehmen",
        data={"description": "Umgeschrieben", "verkaufspreis": "21.50"},
        content_type="application/json",
    )
    assert ok.status_code == 200, ok.content
    a.refresh_from_db()
    assert a.description == "Umgeschrieben"


@pytest.mark.django_db
def test_stammdaten_uebernahme_kennt_keinen_einkaufspreis(admin_client, app_user):
    from db_core.services import artikel as artikel_service

    a = artikel_service.create_article(
        app_user.id, article_number="UEB-2", description="X", unit="Stk"
    )
    r = admin_client.post(
        f"/api/pricing/articles/{a.id}/stammdaten-uebernehmen",
        data={"unit_cost": "4.00"}, content_type="application/json",
    )
    # `unit_cost` ist im Schema nicht vorgesehen -> es kommt nichts an -> 422.
    assert r.status_code == 422
