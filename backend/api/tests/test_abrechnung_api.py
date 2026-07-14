"""API des Abrechnungs-Slices: aus Angebot / aus Auftrag, offene Abrechnung.

Geprüft werden die Dinge, die man nur an der API sieht:

* Der **422 mit Klärungsliste** ist strukturiert (`preis_unbekannt`), nicht bloß
  ein Fließtext — sonst könnte das UI keine Klärungsmaske daraus bauen, und der
  Nutzer stünde vor einer Sackgasse.
* Die Rechte sind **fail-closed**: Die offene Abrechnung ist eine Auftragssicht
  über die ganze Baustelle; ein Konto mit `row_scope EIGENE` (MONTEUR) bekommt
  **403**, nicht etwa alle Zeilen.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from db_core.models import AppUser, BillingLink
from db_core.services import abrechnung as abrechnung_service
from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        return None

    def get_object(self, key):
        raise KeyError(key)

    def remove_object(self, key):
        pass


@pytest.fixture
def fake_storage(monkeypatch):
    from db_core import storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage", lambda: FakeStorage())


@pytest.fixture
def daten(db):
    """Auftrag (KAUFMAENNISCH_GEPRUEFT) mit Kunde und Liegenschaft."""
    actor = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Backoffice", status="ACTIVE", version=1
    )
    obj = property_service.create_property(
        actor.id, name="API-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        actor.id, first_name="Karla", last_name="Kundin"
    )
    order = auftrag_service.create_work_order(
        actor.id, property_id=obj.id, title="Heizung"
    )
    auftrag_service.set_order_evidence(
        actor.id, work_order_id=order.id, reference="Mail"
    )
    auftrag_service.confirm_responsibility(
        actor.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            actor.id, work_order_id=order.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(
            actor.id, work_order_id=order.id, to_status=to
        )
    order.refresh_from_db()
    return {"actor": actor, "obj": obj, "kunde": kunde, "order": order}


def _angebot(daten, lines):
    quote = beleg_service.create_quote(
        daten["actor"].id, property_id=daten["obj"].id, title="Angebot",
        work_order_id=daten["order"].id, lines=lines,
    )
    beleg_service.send_quote(daten["actor"].id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote


def _pos(desc, qty, preis, **extra):
    return {
        "line_type": "MATERIAL", "description": desc, "quantity": qty,
        "unit": "m", "unit_price": preis, "tax_code": "DE_19", **extra,
    }


# --- Aus Angebot ------------------------------------------------------------

@pytest.mark.django_db
def test_rechnung_aus_angebot(admin_client, daten):
    quote = _angebot(daten, [_pos("Rohr DN20", "10", "12.50")])
    r = admin_client.post(
        "/api/invoicing/invoices/aus-angebot",
        data={"quote_id": str(quote.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "ENTWURF"
    assert body["net_total"] == "125.00"
    assert [l["description"] for l in body["lines"]] == ["Rohr DN20"]

    # Zweiter Lauf: Doppelabrechnung — abgelehnt.
    r2 = admin_client.post(
        "/api/invoicing/invoices/aus-angebot",
        data={"quote_id": str(quote.id)},
        content_type="application/json",
    )
    assert r2.status_code == 422
    assert "bereits abgerechnet" in r2.json()["detail"]


# --- Aus Auftrag: der strukturierte 422 -------------------------------------

@pytest.mark.django_db
def test_aus_auftrag_liefert_die_klaerungsliste_strukturiert(
    admin_client, daten, fake_storage
):
    """Kein Fließtext-Fehler: Das UI muss daraus eine Klärungsmaske bauen können."""
    actor, order = daten["actor"], daten["order"]
    abrechnung_service.set_billing_mode(
        actor.id, work_order_id=order.id, billing_mode="REGIE"
    )
    artikel = artikel_service.create_article(
        actor.id, article_number="API-1", description="Rohr ohne EK", unit="m",
        line_type="MATERIAL", list_price=Decimal("9.00"),
    )
    report = report_service.create_report(
        actor.id, work_order_id=order.id, report_date=date(2026, 7, 6),
        activity_text="Rohre verlegt.",
    )
    report_service.set_report_lines(actor.id, report_id=report.id, lines=[{
        "line_type": "MATERIAL", "description": "Rohr ohne EK", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    report_service.sign_report(
        actor.id, report_id=report.id, signed_by_name="Karla", signature_png=PNG_1x1,
    )

    r = admin_client.post(
        "/api/invoicing/invoices/aus-auftrag",
        data={"work_order_id": str(order.id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    body = r.json()
    assert "preis_unbekannt" in body
    pos = body["preis_unbekannt"]
    assert len(pos) == 1
    assert pos[0]["quelle_art"] == "BERICHTSPOSITION"
    assert pos[0]["grund"] in ("EK_FEHLT", "KEINE_VK_REGEL")
    assert pos[0]["menge"] == "5.000"
    # Der Listenpreis ist ein VORSCHLAG, kein automatisch gesetzter Preis.
    arten = {v["art"] for v in pos[0]["vorschlaege"]}
    assert "LISTENPREIS" in arten

    # Und jetzt mit genanntem Preis: die Rechnung entsteht.
    quelle_id = pos[0]["quelle_id"]
    r2 = admin_client.post(
        "/api/invoicing/invoices/aus-auftrag",
        data={
            "work_order_id": str(order.id),
            "tax_code": "DE_19",
            "preise": {quelle_id: "19.90"},
        },
        content_type="application/json",
    )
    assert r2.status_code == 201, r2.content
    body2 = r2.json()
    assert body2["lines"][0]["unit_price"] == "19.90"
    assert body2["net_total"] == "99.50"          # 5 × 19,90 — vom Server


# --- Offene Abrechnung ------------------------------------------------------

@pytest.mark.django_db
def test_offene_abrechnung_zeigt_preis_status(admin_client, daten, fake_storage):
    actor, order = daten["actor"], daten["order"]
    artikel = artikel_service.create_article(
        actor.id, article_number="API-2", description="Rohr", unit="m",
        line_type="MATERIAL",
    )
    report = report_service.create_report(
        actor.id, work_order_id=order.id, report_date=date(2026, 7, 6),
        activity_text="Rohre verlegt.",
    )
    report_service.set_report_lines(actor.id, report_id=report.id, lines=[{
        "line_type": "MATERIAL", "description": "Rohr", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    report_service.sign_report(
        actor.id, report_id=report.id, signed_by_name="Karla", signature_png=PNG_1x1,
    )

    r = admin_client.get(f"/api/workflow/work_orders/{order.id}/offene-abrechnung")
    assert r.status_code == 200, r.content
    body = r.json()
    # Default PAUSCHAL: Nachweis, KEIN Rechnungsposten.
    assert body["billing_mode"] == "PAUSCHAL"
    assert body["abrechenbar"] is False
    assert body["berichtspositionen"][0]["preis_status"] == "UNBEKANNT"
    assert body["berichtspositionen"][0]["einzelpreis"] is None   # nie 0


@pytest.mark.django_db
def test_offene_abrechnung_fuer_monteur_verboten(client_with_role, daten):
    """EIGENE ist eine Zeilenbegrenzung, die diese Auftragssicht nicht umsetzen
    kann — also 403 (fail-closed), keine stille Preisgabe der ganzen Baustelle."""
    monteur = client_with_role("MONTEUR")
    r = monteur.get(
        f"/api/workflow/work_orders/{daten['order'].id}/offene-abrechnung"
    )
    assert r.status_code == 403, r.content


# --- Abrechnungsart ---------------------------------------------------------

@pytest.mark.django_db
def test_billing_mode_umschalten(admin_client, daten):
    order = daten["order"]
    r = admin_client.get(f"/api/workflow/work_orders/{order.id}")
    assert r.json()["billing_mode"] == "PAUSCHAL"

    r = admin_client.patch(
        f"/api/workflow/work_orders/{order.id}",
        data={"billing_mode": "REGIE"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["billing_mode"] == "REGIE"

    r = admin_client.patch(
        f"/api/workflow/work_orders/{order.id}",
        data={"billing_mode": "QUATSCH"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_pauschal_auftrag_laesst_sich_nicht_aus_zeiten_abrechnen(admin_client, daten):
    r = admin_client.post(
        "/api/invoicing/invoices/aus-auftrag",
        data={"work_order_id": str(daten["order"].id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "PAUSCHAL" in r.json()["detail"]


# --- Bindungen lösen --------------------------------------------------------

@pytest.mark.django_db
def test_bindungen_loesen_raeumt_den_entwurf(admin_client, daten):
    quote = _angebot(daten, [_pos("Rohr", "10", "12.50")])
    r = admin_client.post(
        "/api/invoicing/invoices/aus-angebot",
        data={"quote_id": str(quote.id)},
        content_type="application/json",
    )
    invoice_id = r.json()["id"]

    r = admin_client.post(
        f"/api/invoicing/invoices/{invoice_id}/bindungen-loesen",
        data={"reason": "Falsches Angebot erwischt"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["lines"] == []
    assert r.json()["net_total"] == "0.00"

    # Die Quelle ist wieder frei.
    r = admin_client.post(
        "/api/invoicing/invoices/aus-angebot",
        data={"quote_id": str(quote.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content


# --- Rechte: die Schreib-Endpunkte sind getort -------------------------------

@pytest.mark.django_db
def test_schreibende_abrechnung_verlangt_das_recht(client_with_role, daten):
    """Ohne `invoicing`-Rechte kommt niemand an die Faktura.

    Der MONTEUR hat in der Rechtematrix ausschließlich `workflow` (row_scope
    EIGENE) — kein `invoicing`. Alle drei Schreibwege müssen 403 liefern:
    Rechnungen entstehen zu lassen (`ANLEGEN`) und eine gestellte Bindung wieder
    aufzulösen (`STORNIEREN`) sind kaufmännische Entscheidungen.
    """
    quote = _angebot(daten, [_pos("Rohr", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        daten["actor"].id, quote_id=quote.id
    )
    monteur = client_with_role("MONTEUR")

    r = monteur.post(
        "/api/invoicing/invoices/aus-angebot",
        data={"quote_id": str(quote.id)},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content

    r = monteur.post(
        "/api/invoicing/invoices/aus-auftrag",
        data={"work_order_id": str(daten["order"].id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content

    r = monteur.post(
        f"/api/invoicing/invoices/{invoice.id}/bindungen-loesen",
        data={"reason": "Ich probier mal was"},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content

    # Und die Bindung steht unverändert — 403 heißt: nichts ist passiert.
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, released_at__isnull=True
    ).count() == 1
