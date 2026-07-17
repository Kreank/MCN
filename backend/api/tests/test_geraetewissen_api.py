"""Gerätewissen-API: gefilterte Sicht auf Hersteller-Ersatzteile.

Kernzusicherung: es erscheinen NUR Artikel aus den Hersteller-Namensräumen
(vaillant/junkers), niemals der Großhandels-Namensraum `bo`. Leere Kataloge
ergeben ein leeres Ergebnis (kein Fehler) — der Import läuft separat.
"""
import uuid as _uuid
from datetime import date

import pytest

from db_core.db_context import business_transaction
from db_core.models import ArticleSupplierReference, Party, SupplierConnection
from db_core.services import artikel as artikel_service


def _anbindung(app_user, namespace, kind):
    """Lieferant + Anbindung anlegen, Party zurückgeben."""
    with business_transaction(app_user.id):
        party = Party.objects.create(
            id=_uuid.uuid4(), party_type="ORGANIZATION",
            display_name=f"Lieferant {namespace}", status="ACTIVE", version=1,
        )
        SupplierConnection.objects.create(
            id=_uuid.uuid4(), supplier_party_id=party.id, source_system="DATANORM",
            source_namespace=namespace, label=f"Katalog {namespace}",
            status="ACTIVE", connection_kind=kind,
            net_price_semantics="EINHEIT", version=1,
        )
    return party


def _referenz(app_user, article, party, namespace, supplier_number=None):
    with business_transaction(app_user.id):
        ArticleSupplierReference.objects.create(
            id=_uuid.uuid4(), article_id=article.id, supplier_party_id=party.id,
            source_system="DATANORM", source_namespace=namespace,
            supplier_article_number=supplier_number or article.article_number,
            last_purchase_price="1.0000", list_price="2.0000", currency="EUR",
            valid_from=date.today(),
        )


@pytest.fixture
def kataloge(app_user, db):
    """Ein Vaillant-, ein Junkers- und ein Großhandels-Ersatzteil (bo)."""
    vaillant = _anbindung(app_user, "vaillant", "HERSTELLER")
    junkers = _anbindung(app_user, "junkers", "HERSTELLER")
    bo = _anbindung(app_user, "bo", "GROSSHAENDLER")

    va = artikel_service.create_article(
        app_user.id, article_number="DN-VAI-1",
        description="GWT Mikroschalter Vaillant", unit="Stk",
        manufacturer_name="Vaillant", list_price="12.5000",
    )
    ju = artikel_service.create_article(
        app_user.id, article_number="DN-JUN-1",
        description="GWT Zündelektrode Junkers", unit="Stk",
        manufacturer_name="Junkers",
    )
    grosshandel = artikel_service.create_article(
        app_user.id, article_number="DN-BO-1",
        description="GWT Kupferrohr 15mm", unit="m",
    )
    _referenz(app_user, va, vaillant, "vaillant", supplier_number="0020039605")
    _referenz(app_user, ju, junkers, "junkers", supplier_number="87186445670")
    _referenz(app_user, grosshandel, bo, "bo", supplier_number="BO-99999")
    return {"va": va, "ju": ju, "bo": grosshandel}


def _nummern(resp):
    assert resp.status_code == 200, resp.content
    return {i["article_number"] for i in resp.json()["items"]}


@pytest.mark.django_db
def test_nur_hersteller_namespaces_bo_erscheint_nicht(admin_client, kataloge):
    r = admin_client.get("/api/geraetewissen/ersatzteile?q=GWT")
    nummern = _nummern(r)
    assert nummern == {"DN-VAI-1", "DN-JUN-1"}
    assert "DN-BO-1" not in nummern, "Großhandels-Namensraum bo darf NICHT auftauchen."


@pytest.mark.django_db
def test_namespace_filter_grenzt_auf_einen_hersteller_ein(admin_client, kataloge):
    r = admin_client.get("/api/geraetewissen/ersatzteile?q=GWT&namespace=vaillant")
    assert _nummern(r) == {"DN-VAI-1"}


@pytest.mark.django_db
def test_bo_als_namespace_filter_liefert_leer(admin_client, kataloge):
    """`bo` ist nicht konfiguriert → leere Menge, nie eine Aufweichung der Sicht."""
    r = admin_client.get("/api/geraetewissen/ersatzteile?q=GWT&namespace=bo")
    assert r.status_code == 200, r.content
    assert r.json()["items"] == []
    assert r.json()["total"] == 0


@pytest.mark.django_db
def test_suche_ueber_herstellereigene_nummer(admin_client, kataloge):
    """Der Monteur sucht die Sachnummer vom Gerät, nicht die interne DN-Nummer."""
    r = admin_client.get("/api/geraetewissen/ersatzteile?q=0020039605")
    assert _nummern(r) == {"DN-VAI-1"}
    treffer = r.json()["items"][0]
    assert treffer["supplier_article_number"] == "0020039605"
    assert treffer["namespace"] == "vaillant"
    assert treffer["manufacturer_name"] == "Vaillant"
    assert treffer["list_price"] == "12.5000"


@pytest.mark.django_db
def test_detail_read_only(admin_client, kataloge):
    va_id = str(kataloge["va"].id)
    r = admin_client.get(f"/api/geraetewissen/ersatzteile/{va_id}")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["supplier_article_number"] == "0020039605"
    assert body["namespace"] == "vaillant"
    assert body["supplier_list_price"] == "2.0000"


@pytest.mark.django_db
def test_detail_grosshandel_404(admin_client, kataloge):
    """Ein reiner bo-Artikel ist über die Gerätewissen-Detailsicht nicht auffindbar."""
    bo_id = str(kataloge["bo"].id)
    r = admin_client.get(f"/api/geraetewissen/ersatzteile/{bo_id}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_hersteller_facetten_mit_anzahl(admin_client, kataloge):
    r = admin_client.get("/api/geraetewissen/hersteller")
    assert r.status_code == 200, r.content
    facetten = {h["namespace"]: h for h in r.json()}
    assert set(facetten) == {"vaillant", "junkers"}
    assert "bo" not in facetten
    assert facetten["vaillant"]["anzahl"] == 1
    assert facetten["junkers"]["anzahl"] == 1
    assert facetten["vaillant"]["label"] == "Katalog vaillant"


@pytest.mark.django_db
def test_leerer_katalog_liefert_leer_ohne_fehler(admin_client, db):
    """Ohne importierte Kataloge: leere Liste, Facetten mit anzahl=0."""
    r = admin_client.get("/api/geraetewissen/ersatzteile")
    assert r.status_code == 200, r.content
    assert r.json()["items"] == []
    assert r.json()["total"] == 0

    r2 = admin_client.get("/api/geraetewissen/hersteller")
    assert r2.status_code == 200, r2.content
    facetten = {h["namespace"]: h["anzahl"] for h in r2.json()}
    assert facetten == {"vaillant": 0, "junkers": 0}


@pytest.mark.django_db
def test_ohne_recht_403(client_with_role, kataloge):
    """Ohne pricing/LESEN kein Zugriff (fail-closed, wie die Artikel-API)."""
    client = client_with_role("MONTEUR")
    r = client.get("/api/geraetewissen/ersatzteile")
    assert r.status_code == 403


@pytest.mark.django_db
def test_inaktiver_artikel_nicht_in_der_liste(admin_client, app_user, kataloge):
    """Ausrangierte (INAKTIV) Ersatzteile verschwinden aus der Suche."""
    va = kataloge["va"]
    artikel_service.set_article_status(app_user.id, article_id=va.id, status="INAKTIV")
    r = admin_client.get("/api/geraetewissen/ersatzteile?q=GWT")
    assert "DN-VAI-1" not in _nummern(r)
