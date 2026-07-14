"""API-Tests: Position an einen GEBUNDENEN Rechnungsentwurf anhängen/zurücknehmen.

`POST /invoices/{id}/lines` und `DELETE /invoices/{id}/lines/last` sind der einzige
Weg, einen Beleg aus dem **Abrechnungslauf** noch zu ergänzen: Der Editor
(`PUT /invoices/{id}`) ersetzt den ganzen Positionssatz per Delete+Insert und läuft
damit zwangsläufig gegen die gebundene Zeile (Trigger, Migration 0088). Die
Service-Schicht ist eigens geprüft (`db_core/tests/test_beleg_gebundene_position.py`);
hier steht das, was nur die API hat:

* das Recht `invoicing/AENDERN` (fehlt es → 403; ohne Login → 401),
* der `201`-Wrapper des Anhängens (django-ninja `Status`),
* die Felder `gebunden` und `billing_source` in `InvoiceDetailOut`,
* das 422-Mapping der Fachfehler (veröffentlichter Beleg, Anrechnungsposition) —
  **nie ein 500**,
* und der § 35a-Ausweis: Eine angehängte PAUSCHALE ohne Arbeitskostenangabe macht
  den Ausweis der GANZEN Rechnung unbestimmbar. Genau davor schützt das Pflichtfeld
  im Dialog — der Server muss beide Wege korrekt abbilden.
"""
import json

import pytest

from db_core.services import abrechnung as abrechnung_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

_ANFAHRT = {
    "line_type": "PAUSCHALE",
    "description": "Anfahrtspauschale",
    "quantity": "1",
    "unit": "Stk",
    "unit_price": "45.00",
    "tax_code": "DE_19",
}


def _post_zeile(client, invoice_id, line):
    return client.post(
        f"/api/invoicing/invoices/{invoice_id}/lines",
        data=json.dumps(line),
        content_type="application/json",
    )


def _delete_letzte(client, invoice_id):
    return client.delete(f"/api/invoicing/invoices/{invoice_id}/lines/last")


@pytest.fixture
def gebundene_rechnung(app_user):
    """Rechnung (ENTWURF) aus einem Angebot — die Position trägt eine Bindung.

    Der Lohnanteil ist bestimmbar (ARBEITSZEIT → voll begünstigt): Die Rechnung
    weist § 35a aus, und genau dieser Ausweis steht auf dem Spiel, sobald eine
    Zeile ohne bestimmten Anteil dazukommt.
    """
    obj = property_service.create_property(
        app_user.id, name="Bindungs-Objekt", property_type="EINFAMILIENHAUS",
        street="Baustelle", house_number="2", postal_code="10115", city="Berlin",
    )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot",
        lines=[{
            "line_type": "ARBEITSZEIT", "description": "Montagestunden",
            "quantity": "10", "unit": "h", "unit_price": "60.00",
            "tax_code": "DE_19",
        }],
    )
    beleg_service.send_quote(app_user.id, quote_id=quote.id)
    invoice = abrechnung_service.rechnung_aus_angebot(app_user.id, quote_id=quote.id)
    return invoice


# --- Rechte ----------------------------------------------------------------

@pytest.mark.django_db
def test_anhaengen_ohne_recht_ist_403(client_with_role, gebundene_rechnung):
    """Anhängen ändert den Beleg — es verlangt `invoicing/AENDERN` wie der Editor."""
    leser = client_with_role("NUR_LESEN")
    r = _post_zeile(leser, gebundene_rechnung.id, _ANFAHRT)
    assert r.status_code == 403


@pytest.mark.django_db
def test_entfernen_ohne_recht_ist_403(client_with_role, gebundene_rechnung):
    ohne = client_with_role("MONTEUR")
    r = _delete_letzte(ohne, gebundene_rechnung.id)
    assert r.status_code == 403


@pytest.mark.django_db
def test_beide_wege_ohne_login_sind_401(anonymous_client, gebundene_rechnung):
    assert _post_zeile(anonymous_client, gebundene_rechnung.id, _ANFAHRT).status_code == 401
    assert _delete_letzte(anonymous_client, gebundene_rechnung.id).status_code == 401


# --- Der Normalfall: 201 + die neuen Felder --------------------------------

@pytest.mark.django_db
def test_anhaengen_liefert_201_und_die_neuen_felder(admin_client, gebundene_rechnung):
    """`gebunden` und `billing_source` sind das, woran das UI die gesperrten
    Zeilen erkennt — ohne sie liefe der Bediener in den Trigger (422)."""
    r = _post_zeile(
        admin_client, gebundene_rechnung.id,
        {**_ANFAHRT, "labour_net_amount": "45.00"},
    )
    assert r.status_code == 201, r.content
    body = r.json()

    assert body["gebunden"] is True
    zeilen = body["lines"]
    assert [l["description"] for l in zeilen] == ["Montagestunden", "Anfahrtspauschale"]
    # Die gebundene Zeile nennt ihre Herkunft, die angehängte trägt keine Bindung.
    assert zeilen[0]["billing_source"] == "ANGEBOTSPOSITION"
    assert zeilen[1]["billing_source"] is None
    # Summen rechnet der Server aus ALLEN Zeilen neu (600 + 45).
    assert body["net_total"] == "645.00"


@pytest.mark.django_db
def test_letzte_zeile_laesst_sich_zuruecknehmen(admin_client, gebundene_rechnung):
    _post_zeile(admin_client, gebundene_rechnung.id,
                {**_ANFAHRT, "labour_net_amount": "45.00"})
    r = _delete_letzte(admin_client, gebundene_rechnung.id)
    assert r.status_code == 200, r.content
    body = r.json()
    assert [l["description"] for l in body["lines"]] == ["Montagestunden"]
    assert body["net_total"] == "600.00"


@pytest.mark.django_db
def test_gebundene_zeile_zurueckzunehmen_ist_422(admin_client, gebundene_rechnung):
    """Die Bindung ist der Nachweis der Abrechnung — 422, kein 500."""
    r = _delete_letzte(admin_client, gebundene_rechnung.id)
    assert r.status_code == 422, r.content
    assert "gebunden" in r.json()["detail"]


# --- § 35a: der Ausweis der GANZEN Rechnung hängt an dieser einen Zeile -----

@pytest.mark.django_db
def test_anhaengen_mit_arbeitskosten_haelt_den_ausweis(admin_client, gebundene_rechnung):
    """Anfahrt ist in voller Höhe begünstigt → 600 + 45 = 645 € Arbeitskosten."""
    r = _post_zeile(
        admin_client, gebundene_rechnung.id,
        {**_ANFAHRT, "labour_net_amount": "45.00"},
    )
    assert r.status_code == 201, r.content
    ausweis = r.json()["arbeitskosten"]
    assert ausweis["bestimmbar"] is True
    assert ausweis["net_amount"] == "645.00"
    assert ausweis["gross_amount"] == "767.55"


@pytest.mark.django_db
def test_pauschale_ohne_angabe_zerstoert_den_ausweis(admin_client, gebundene_rechnung):
    """Der Grund für das Pflichtfeld im Dialog, hier als Server-Nachweis:

    Ohne `labour_net_amount` bleibt der Anteil einer PAUSCHALE unbestimmt — und
    EINE solche Zeile macht den Ausweis der ganzen Rechnung unbestimmbar
    (`OFFENE_POSITIONEN`). Der Privatkunde verlöre 20 % Steuerermäßigung auf
    ALLES, nicht nur auf die Pauschale. Der Server sagt hier ehrlich, WO die
    Lücke sitzt (Position 2) — das UI muss sie vorher verhindern.
    """
    r = _post_zeile(admin_client, gebundene_rechnung.id, _ANFAHRT)
    assert r.status_code == 201, r.content
    ausweis = r.json()["arbeitskosten"]
    assert ausweis["bestimmbar"] is False
    assert ausweis["grund"] == "OFFENE_POSITIONEN"
    assert ausweis["offen"] == [2]
    assert ausweis["net_amount"] is None


@pytest.mark.django_db
def test_zu_hoher_arbeitskostenanteil_ist_422(admin_client, gebundene_rechnung):
    """Der Anteil muss ein TEIL des Positionsbetrags sein (DB-CHECK) → 422, kein 500."""
    r = _post_zeile(
        admin_client, gebundene_rechnung.id,
        {**_ANFAHRT, "labour_net_amount": "46.00"},
    )
    assert r.status_code == 422
    assert "Teil des Positionsbetrags" in r.json()["detail"]


# --- Veröffentlichter Beleg: zu (422, kein 500) ----------------------------

@pytest.fixture
def veroeffentlichte_rechnung(app_user):
    obj = property_service.create_property(
        app_user.id, name="Publiziert-Objekt", property_type="WEG",
        street="Baustelle", house_number="7", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Sieglinde", last_name="Schuldner"
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Bad-Sanierung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Auftrag per Mail"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "ARBEITSZEIT", "description": "Leistung",
                "quantity": 1, "unit_price": "500.00", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


@pytest.mark.django_db
def test_anhaengen_an_veroeffentlichte_rechnung_ist_422(
    admin_client, veroeffentlichte_rechnung
):
    """GoBD: der veröffentlichte Beleg ist eingefroren — ein Fachfehler, kein 500."""
    r = _post_zeile(admin_client, veroeffentlichte_rechnung.id, _ANFAHRT)
    assert r.status_code == 422, r.content
    assert "unveränderlich" in r.json()["detail"]

    r = _delete_letzte(admin_client, veroeffentlichte_rechnung.id)
    assert r.status_code == 422, r.content
    assert "unveränderlich" in r.json()["detail"]


# --- Schlussrechnung: die Anrechnungsposition ist tabu ----------------------

@pytest.mark.django_db
def test_anrechnungsposition_entfernen_ist_422(admin_client, app_user):
    """Die letzte Zeile einer Schlussrechnung IST die Anrechnung. Sie einzeln zu
    entfernen ließe die Verkettung stehen und forderte den bereits gezahlten
    Abschlag ein zweites Mal — 422, kein 500."""
    obj = property_service.create_property(
        app_user.id, name="SR-Objekt", property_type="WEG",
        street="Baustelle", house_number="9", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Konrad", last_name="Kunde"
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Ausbau"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)

    ar = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="ABSCHLAGSRECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "ARBEITSZEIT", "description": "1. Abschlag",
                "quantity": 1, "unit_price": "1000.00", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=ar.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=ar.id)
    for to in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)

    sr = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="SCHLUSSRECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "ARBEITSZEIT", "description": "Gesamtleistung",
                "quantity": 1, "unit_price": "5000.00", "tax_code": "DE_19"}],
        advance_invoice_ids=[ar.id],
    )

    r = _delete_letzte(admin_client, sr.id)
    assert r.status_code == 422, r.content
    assert "Anrechnung" in r.json()["detail"]

    # Der Beleg ist unversehrt: Leistung 5.000 − Abschlag 1.000 = 4.760 € brutto.
    detail = admin_client.get(f"/api/invoicing/invoices/{sr.id}").json()
    assert detail["gross_total"] == "4760.00"
    assert len(detail["lines"]) == 2
