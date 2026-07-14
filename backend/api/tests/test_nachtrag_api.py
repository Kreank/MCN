"""API: „Nachtrag abrechnen" und der Ausgang eines Angebots.

Geprüft wird, was man nur an der API sieht:

* Die **Klärungsliste des Nachtrags ist strukturiert** — und ihre `quelle_id` ist
  ein **String** (der Schlüssel der Abweichung), keine UUID. Eine Zusatzleistung
  ohne Artikelbezug hat gar keine ID im Stamm, und gerade sie braucht die Klärung.
* Die Rechte sind **fail-closed**: Der Monteur schreibt Berichte — er stellt keine
  Nachtragsrechnungen (403), und er sieht die Preise der Vorschau nicht.
* Der Ausgang des Angebots (ANGENOMMEN) geht durch den Endpunkt — und lässt den
  Beleg unangetastet (B-30).
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from db_core.models import AppUser, BillingLink, Quote
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
    """Das Demo-Szenario: PAUSCHAL, 18 Ventile angeboten, 19 verbaut."""
    actor = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Backoffice", status="ACTIVE", version=1
    )
    obj = property_service.create_property(
        actor.id, name="Nachtrag-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        actor.id, first_name="Karla", last_name="Kundin"
    )
    order = auftrag_service.create_work_order(
        actor.id, property_id=obj.id, title="Thermostatventile"
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
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(
            actor.id, work_order_id=order.id, to_status=to
        )
    order.refresh_from_db()
    return {"actor": actor, "obj": obj, "kunde": kunde, "order": order}


def _artikel(daten, nummer, *, vk=None, beschreibung="Thermostatventil"):
    art = artikel_service.create_article(
        daten["actor"].id, article_number=nummer, description=beschreibung,
        unit="stk", line_type="MATERIAL",
    )
    if vk is not None:
        artikel_service.set_article_sale_price(
            daten["actor"].id, article_id=art.id, fixed_price=Decimal(vk),
            is_standard=True,
        )
    return art


def _angebot(daten, artikel, menge="18", preis="24.00"):
    quote = beleg_service.create_quote(
        daten["actor"].id, property_id=daten["obj"].id, title="Angebot",
        work_order_id=daten["order"].id,
        lines=[{
            "line_type": "MATERIAL", "description": "Thermostatventil",
            "quantity": menge, "unit": "stk", "unit_price": preis,
            "tax_code": "DE_19", "source_article_id": str(artikel.id),
        }],
    )
    beleg_service.send_quote(daten["actor"].id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote


def _bericht(daten, quote, menge="19"):
    from db_core.models import QuoteLine

    ql = QuoteLine.objects.get(quote_id=quote.id, position_number=1)
    report = report_service.create_report(
        daten["actor"].id, work_order_id=daten["order"].id,
        report_date=date(2026, 7, 6), activity_text="Ventile getauscht.",
    )
    report_service.set_report_lines(daten["actor"].id, report_id=report.id, lines=[{
        "line_type": "MATERIAL", "quantity": menge,
        "source_quote_line_id": str(ql.id),
    }])
    report_service.sign_report(
        daten["actor"].id, report_id=report.id, signed_by_name="Karla",
        signature_png=PNG_1x1,
    )
    return report


# --- Vorschau ---------------------------------------------------------------

@pytest.mark.django_db
def test_vorschau_zeigt_nur_die_differenz(admin_client, daten, fake_storage):
    artikel = _artikel(daten, "TH-A", vk="24.00")
    quote = _angebot(daten, artikel)
    _bericht(daten, quote, "19")

    r = admin_client.get(f"/api/workflow/work_orders/{daten['order'].id}/nachtrag")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["abrechenbar"] is True
    assert len(body["positionen"]) == 1
    pos = body["positionen"][0]
    assert pos["art"] == "MEHRVERBRAUCH"
    assert pos["soll"] == "18.000"
    assert pos["ist"] == "19.000"
    assert pos["menge"] == "1.000"          # die Differenz, nicht 19
    assert pos["einzelpreis"] == "24.00"
    assert body["summe"] == "24.00"
    assert body["preise_unbekannt"] is False


@pytest.mark.django_db
def test_vorschau_ist_ehrlich_wenn_nichts_abrechenbar_ist(
    admin_client, daten, fake_storage
):
    """Kein toter Knopf: Ohne Abweichung sagt die Vorschau das ausdrücklich."""
    artikel = _artikel(daten, "TH-B", vk="24.00")
    quote = _angebot(daten, artikel)
    _bericht(daten, quote, "18")            # Soll = Ist

    r = admin_client.get(f"/api/workflow/work_orders/{daten['order'].id}/nachtrag")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["positionen"] == []
    assert body["bereits_abgerechnet"] == []
    assert body["summe"] == "0.00"


# --- Abrechnen --------------------------------------------------------------

@pytest.mark.django_db
def test_rechnung_aus_nachtrag(admin_client, daten, fake_storage):
    artikel = _artikel(daten, "TH-C", vk="24.00")
    quote = _angebot(daten, artikel)
    _bericht(daten, quote, "19")

    r = admin_client.post(
        "/api/invoicing/invoices/aus-nachtrag",
        data={"work_order_id": str(daten["order"].id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "ENTWURF"
    assert body["net_total"] == "24.00"
    assert body["lines"][0]["quantity"] == "1.000"
    assert "Mehrmenge" in body["lines"][0]["description"]

    # Zweiter Lauf: nichts mehr da — und der Grund wird benannt.
    r2 = admin_client.post(
        "/api/invoicing/invoices/aus-nachtrag",
        data={"work_order_id": str(daten["order"].id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    assert r2.status_code == 422
    assert "bereits abgerechnet" in r2.json()["detail"]


@pytest.mark.django_db
def test_klaerungsliste_ist_strukturiert_und_traegt_einen_string_schluessel(
    admin_client, daten, fake_storage
):
    """Ohne Preis: 422 mit Klärungsliste — niemals eine Rechnung über 0,00 €."""
    artikel = _artikel(daten, "TH-D")            # kein VK ermittelbar
    quote = _angebot(daten, artikel)
    _bericht(daten, quote, "19")

    r = admin_client.post(
        "/api/invoicing/invoices/aus-nachtrag",
        data={"work_order_id": str(daten["order"].id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    body = r.json()
    pos = body["preis_unbekannt"]
    assert len(pos) == 1
    assert pos[0]["quelle_art"] == "ABWEICHUNG"
    assert pos[0]["quelle_id"].startswith("ARTIKEL:")     # String, keine UUID
    assert pos[0]["menge"] == "1.000"
    assert not BillingLink.objects.filter(
        invoice__work_order_id=daten["order"].id
    ).exists()

    # Der Ausweg: derselbe Aufruf mit genanntem Preis.
    r2 = admin_client.post(
        "/api/invoicing/invoices/aus-nachtrag",
        data={
            "work_order_id": str(daten["order"].id), "tax_code": "DE_19",
            "preise": {pos[0]["quelle_id"]: "31.00"},
        },
        content_type="application/json",
    )
    assert r2.status_code == 201, r2.content
    assert r2.json()["net_total"] == "31.00"


# --- Rechte -----------------------------------------------------------------

@pytest.mark.django_db
def test_nachtrag_ist_kein_monteurswerkzeug(client_with_role, daten, fake_storage):
    """Der Monteur schreibt Berichte — er stellt keine Rechnungen."""
    artikel = _artikel(daten, "TH-E", vk="24.00")
    quote = _angebot(daten, artikel)
    _bericht(daten, quote, "19")
    monteur = client_with_role("MONTEUR")

    r = monteur.post(
        "/api/invoicing/invoices/aus-nachtrag",
        data={"work_order_id": str(daten["order"].id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content
    assert not BillingLink.objects.filter(
        invoice__work_order_id=daten["order"].id
    ).exists()


@pytest.mark.django_db
def test_vorschau_ohne_invoicing_recht_verboten(client_with_role, daten):
    """Die Vorschau führt Einzelpreise — Geld hängt an `invoicing`, wie bei der
    offenen Abrechnung. DISPOSITION hat workflow/LESEN, aber kein invoicing."""
    dispo = client_with_role("DISPOSITION")
    r = dispo.get(f"/api/workflow/work_orders/{daten['order'].id}/nachtrag")
    assert r.status_code == 403, r.content


# --- Der dritte Weg: divergente Einheit (Review-Befund) ----------------------

def _zusatz_bericht(daten, artikel, menge, einheit):
    """Signierter Bericht mit ZUSATZ-Zeile (ohne Herkunft) in `einheit`."""
    report = report_service.create_report(
        daten["actor"].id, work_order_id=daten["order"].id,
        report_date=date(2026, 7, 6), activity_text="Ventile getauscht.",
    )
    report_service.set_report_lines(daten["actor"].id, report_id=report.id, lines=[{
        "line_type": "MATERIAL", "quantity": menge, "unit": einheit,
        "source_article_id": str(artikel.id),
    }])
    report_service.sign_report(
        daten["actor"].id, report_id=report.id, signed_by_name="Karla",
        signature_png=PNG_1x1,
    )
    return report


@pytest.mark.django_db
def test_aus_angebot_divergente_einheit_liefert_strukturierten_422(
    admin_client, daten, fake_storage
):
    """Nachtrag „Stk", dann Angebot „Stück" → 422 mit `einheit_uneindeutig`,
    NICHT 912 €."""
    ventil = _artikel(daten, "API-EIN-1", vk="24.00")
    _zusatz_bericht(daten, ventil, "19", "Stk")
    admin_client.post(
        "/api/invoicing/invoices/aus-nachtrag",
        data={"work_order_id": str(daten["order"].id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    # Angebot über denselben Artikel, Einheit „Stück".
    quote = beleg_service.create_quote(
        daten["actor"].id, property_id=daten["obj"].id, title="Angebot",
        work_order_id=daten["order"].id,
        lines=[{
            "line_type": "MATERIAL", "description": "Thermostatventil",
            "quantity": "19", "unit": "Stück", "unit_price": "24.00",
            "tax_code": "DE_19", "source_article_id": str(ventil.id),
        }],
    )
    beleg_service.send_quote(daten["actor"].id, quote_id=quote.id)

    r = admin_client.post(
        "/api/invoicing/invoices/aus-angebot",
        data={"quote_id": str(quote.id)},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    body = r.json()
    assert "einheit_uneindeutig" in body
    assert set(body["einheit_uneindeutig"][0]["einheiten"]) == {"stk", "stück"}
    from db_core.models import Invoice

    summe = sum(
        (i.net_total for i in Invoice.objects.filter(
            work_order_id=daten["order"].id, invoice_type="RECHNUNG")),
        Decimal("0.00"),
    )
    assert summe == Decimal("456.00")


@pytest.mark.django_db
def test_aus_nachtrag_divergente_einheit_klaerungsliste(
    admin_client, daten, fake_storage
):
    """Umgekehrt: Angebot „Stück" gebucht, dann Nachtrag „Stk" → 422 mit
    `einheit_uneindeutig` (Klärungsliste), kein Beleg."""
    ventil = _artikel(daten, "API-EIN-2", vk="24.00")
    quote = beleg_service.create_quote(
        daten["actor"].id, property_id=daten["obj"].id, title="Angebot",
        work_order_id=daten["order"].id,
        lines=[{
            "line_type": "MATERIAL", "description": "Thermostatventil",
            "quantity": "19", "unit": "Stück", "unit_price": "24.00",
            "tax_code": "DE_19", "source_article_id": str(ventil.id),
        }],
    )
    beleg_service.send_quote(daten["actor"].id, quote_id=quote.id)
    admin_client.post(
        "/api/invoicing/invoices/aus-angebot",
        data={"quote_id": str(quote.id)},
        content_type="application/json",
    )
    _zusatz_bericht(daten, ventil, "19", "Stk")

    # Die Vorschau weist den Konflikt schon aus.
    v = admin_client.get(f"/api/workflow/work_orders/{daten['order'].id}/nachtrag")
    assert v.status_code == 200
    assert len(v.json()["einheit_konflikte"]) == 1

    r = admin_client.post(
        "/api/invoicing/invoices/aus-nachtrag",
        data={"work_order_id": str(daten["order"].id), "tax_code": "DE_19"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    body = r.json()
    assert body.get("einheit_uneindeutig")
    assert not body.get("preis_unbekannt")


# --- Der Ausgang des Angebots ------------------------------------------------

@pytest.mark.django_db
def test_angebot_annehmen(admin_client, daten):
    artikel = _artikel(daten, "TH-F", vk="24.00")
    quote = _angebot(daten, artikel)
    vorher = Quote.objects.get(id=quote.id).content_hash

    r = admin_client.post(
        f"/api/invoicing/quotes/{quote.id}/status",
        data={"to_status": "ANGENOMMEN"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "ANGENOMMEN"
    # B-30: der festgeschriebene Inhalt bleibt Zeichen für Zeichen derselbe.
    assert Quote.objects.get(id=quote.id).content_hash == vorher


@pytest.mark.django_db
def test_ersetzt_wird_mit_grund_abgelehnt(admin_client, daten):
    artikel = _artikel(daten, "TH-G", vk="24.00")
    quote = _angebot(daten, artikel)
    r = admin_client.post(
        f"/api/invoicing/quotes/{quote.id}/status",
        data={"to_status": "ERSETZT"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Nachfolgeangebot" in r.json()["detail"]
    assert Quote.objects.get(id=quote.id).status == "VERSENDET"


@pytest.mark.django_db
def test_angebotsstatus_verlangt_das_recht(client_with_role, daten):
    artikel = _artikel(daten, "TH-H", vk="24.00")
    quote = _angebot(daten, artikel)
    monteur = client_with_role("MONTEUR")
    r = monteur.post(
        f"/api/invoicing/quotes/{quote.id}/status",
        data={"to_status": "ANGENOMMEN"},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content
    assert Quote.objects.get(id=quote.id).status == "VERSENDET"
