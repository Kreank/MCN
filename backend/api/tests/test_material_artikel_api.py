"""Der Artikel-Picker der Materialbuchung — **preisfrei für den Monteur**.

Invariante Kap. 5: „Der Monteur sieht sein ganzes Objekt, aber nie Preise."
`GET /api/pricing/articles` liefert `list_price` und hängt fail-closed an
`pricing/LESEN` — ein Recht, das der Monteur nicht hat. Er braucht die Auswahl
trotzdem: Ohne Artikelbezug ist seine Materialbuchung nicht bepreisbar (Migration
0139), und dann fehlt sie entweder auf der Rechnung oder blockiert sie.

Deshalb ein **eigener** Endpunkt mit einem **eigenen Schema ohne Geldfeld** —
dieselbe Bauweise wie beim preisfreien Angebot. Diese Suite hält beides fest:
dass der Monteur suchen darf, und dass durch diese Tür kein Preis kommt.
"""
import uuid
from decimal import Decimal

import pytest

from db_core.models import MaterialEntry
from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import property as property_service

from .conftest import make_app_user

# Alles, was nach Geld aussieht. Die Antwort darf NICHTS davon führen — nicht
# „zufällig leer", sondern gar nicht (das Schema kennt die Felder nicht).
GELDFELDER = {
    "list_price", "unit_price", "sale_price", "purchase_price", "price",
    "ek", "vk", "net_amount", "unit_cost", "markup_percent", "fixed_price",
}


@pytest.fixture
def artikel(db):
    actor = make_app_user("Stammdatenpflege")
    return artikel_service.create_article(
        actor.id, article_number="MAT-4711", description="Kupferrohr 18",
        unit="m", line_type="MATERIAL",
    )


@pytest.mark.django_db
def test_monteur_findet_artikel_und_sieht_keinen_preis(client_with_role, artikel):
    """Der Kern: Suche ja, Preis nein."""
    client = client_with_role("MONTEUR")
    resp = client.get("/api/planung/material-artikel?q=MAT-4711")
    assert resp.status_code == 200, resp.content
    (treffer,) = resp.json()
    assert treffer["article_number"] == "MAT-4711"
    assert treffer["description"] == "Kupferrohr 18"
    assert treffer["unit"] == "m"
    # **Kein Geldfeld** — auch nicht mit null-Wert.
    assert not (set(treffer) & GELDFELDER), f"Preis im Monteur-UI: {treffer}"
    assert set(treffer) == {"id", "article_number", "description", "unit"}


@pytest.mark.django_db
def test_der_teure_weg_bleibt_dem_monteur_verschlossen(client_with_role, artikel):
    """Gegenprobe: Der Artikelstamm selbst (mit Preisen) bleibt zu.

    Ohne diese Zusicherung wäre der neue Endpunkt sinnlos — man könnte den Preis
    ja nebenan holen.
    """
    client = client_with_role("MONTEUR")
    assert client.get("/api/pricing/articles").status_code == 403


@pytest.mark.django_db
def test_ohne_workflow_recht_kein_zugriff(client_with_role, artikel):
    """Ein Konto ohne Rolle bekommt 403 — der Endpunkt ist getort."""
    client = client_with_role(None)
    assert client.get("/api/planung/material-artikel").status_code == 403


@pytest.mark.django_db
def test_inaktive_artikel_erscheinen_nicht(client_with_role, artikel):
    """Ausrangiertes Material soll nicht neu gebucht werden."""
    actor = make_app_user("Stammdatenpflege 2")
    artikel_service.set_article_status(
        actor.id, article_id=artikel.id, status="INAKTIV"
    )
    client = client_with_role("MONTEUR")
    resp = client.get("/api/planung/material-artikel?q=MAT-4711")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.django_db
def test_material_buchen_mit_artikel(client_with_role, artikel):
    """Der Artikelbezug kommt am Einsatz an — und der Monteur nennt keinen Preis."""
    actor = make_app_user("Disposition")
    obj = property_service.create_property(
        actor.id, name="Objekt", property_type="WEG", street="Weg",
        house_number="1", postal_code="10115", city="Berlin",
    )
    order = auftrag_service.create_work_order(
        actor.id, property_id=obj.id, title="Heizung"
    )
    job = einsatz_service.create_service_job(actor.id, work_order_id=order.id)

    client = client_with_role("ADMINISTRATION")
    resp = client.post(
        f"/api/planung/einsaetze/{job.id}/materials",
        data={
            "description": "Kupferrohr 18", "quantity": "4", "unit": "m",
            "source_article_id": str(artikel.id),
        },
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["source_article_id"] == str(artikel.id)
    assert body["source_article_number"] == "MAT-4711"
    assert not (set(body) & GELDFELDER)
    entry = MaterialEntry.objects.get(service_job_id=job.id)
    assert entry.source_article_id == artikel.id
    assert entry.quantity == Decimal("4.000")


@pytest.mark.django_db
def test_unbekannter_artikel_ist_422_kein_500(client_with_role):
    """Fremdschlüssel vorab prüfen (`ensure_exists`) — 422 statt IntegrityError."""
    actor = make_app_user("Disposition 2")
    obj = property_service.create_property(
        actor.id, name="Objekt 2", property_type="WEG", street="Weg",
        house_number="2", postal_code="10115", city="Berlin",
    )
    order = auftrag_service.create_work_order(
        actor.id, property_id=obj.id, title="Heizung"
    )
    job = einsatz_service.create_service_job(actor.id, work_order_id=order.id)

    client = client_with_role("ADMINISTRATION")
    resp = client.post(
        f"/api/planung/einsaetze/{job.id}/materials",
        data={
            "description": "Rohr", "quantity": "1", "unit": "m",
            "source_article_id": str(uuid.uuid4()),
        },
        content_type="application/json",
    )
    assert resp.status_code == 422, resp.content
