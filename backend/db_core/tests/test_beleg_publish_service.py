"""Service-Tests für Veröffentlichung (Rechnung) und Versand (Angebot).

Die Veröffentlichungs-/Versand-Tore der DB sind teils DEFERRED (feuern erst beim
COMMIT); unter der pytest-Transaktion werden sie mit SET CONSTRAINTS ALL
IMMEDIATE scharf geprüft. Der BEFORE-Trigger (Nummernvergabe, Snapshot-Pflicht)
feuert dagegen sofort. tax_code-Startwerte (DE_19) kommen aus 0016.
"""
import re

import pytest
from django.db import Error, connection

from db_core.db_context import business_transaction
from db_core.models import Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _force_deferred_checks():
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _property(app_user):
    return property_service.create_property(
        app_user.id, name="Beleg-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user, first="Wanda", last="WEG"):
    return identity_service.create_person(app_user.id, first_name=first, last_name=last)


def _gepruefter_auftrag(app_user, obj, debtor):
    """Auftrag bis KAUFMAENNISCH_GEPRUEFT, mit PRINCIPAL + INVOICE_DEBTOR."""
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag zur Rechnung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=debtor.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    return order


def _invoice(app_user, obj, order):
    return beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )


# --- Rechnung veröffentlichen ---------------------------------------------

@pytest.mark.django_db
def test_publish_invoice_vergibt_nummer_und_snapshot(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = _invoice(app_user, obj, order)
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=weg.id,
        role="INVOICE_DEBTOR", is_primary=True,
    )
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=weg.id,
        role="INVOICE_RECIPIENT", is_primary=True,
    )

    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    assert published.status == "VEROEFFENTLICHT"
    assert re.match(r"^RE-[0-9]{4}-[0-9]{6,}$", published.invoice_number)
    assert published.published_at is not None
    assert published.billing_snapshot is not None
    assert published.content_hash and len(published.content_hash) == 64


@pytest.mark.django_db
def test_publish_invoice_nur_entwurf(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = _invoice(app_user, obj, order)
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=weg.id,
        role="INVOICE_DEBTOR", is_primary=True,
    )
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=weg.id,
        role="INVOICE_RECIPIENT", is_primary=True,
    )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    with pytest.raises(ValueError):
        beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)


@pytest.mark.django_db
def test_publish_invoice_ohne_auftrag_scheitert(app_user):
    obj = _property(app_user)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 1,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )
    with pytest.raises(ValueError):
        beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)


@pytest.mark.django_db
def test_publish_gate_ohne_schuldner_scheitert(app_user):
    """Ohne INVOICE_DEBTOR/Empfänger verletzt die Veröffentlichung das Tor (A-27)."""
    obj = _property(app_user)
    weg = _party(app_user)
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = _invoice(app_user, obj, order)
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            Invoice.objects.filter(id=inv.id, status="ENTWURF").update(
                billing_snapshot={"x": 1}, content_hash="0" * 64,
                status="VEROEFFENTLICHT",
            )
            _force_deferred_checks()


@pytest.mark.django_db
def test_publish_gate_mit_beteiligten_besteht(app_user):
    """Mit geprüftem Auftrag, Schuldner und primärem Empfänger passiert die
    Veröffentlichung das DEFERRED-Tor (positive Gegenprobe, A-27/A-28/B-08).

    Erzwingt die Sofortprüfung, weil die Tore unter der pytest-Transaktion sonst
    nie feuern (kein echtes COMMIT)."""
    obj = _property(app_user)
    weg = _party(app_user)
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = _invoice(app_user, obj, order)
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=weg.id,
        role="INVOICE_DEBTOR", is_primary=True,
    )
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=weg.id,
        role="INVOICE_RECIPIENT", is_primary=True,
    )
    with business_transaction(app_user.id):
        Invoice.objects.filter(id=inv.id, status="ENTWURF").update(
            billing_snapshot={"x": 1}, content_hash="0" * 64,
            status="VEROEFFENTLICHT",
        )
        _force_deferred_checks()  # darf nicht werfen
    inv.refresh_from_db()
    assert inv.status == "VEROEFFENTLICHT"


# --- Angebot versenden -----------------------------------------------------

@pytest.mark.django_db
def test_send_quote_vergibt_nummer_und_snapshot(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot Dach",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 5,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )
    sent = beleg_service.send_quote(app_user.id, quote_id=q.id)
    assert sent.status == "VERSENDET"
    assert re.match(r"^AN-[0-9]{4}-[0-9]{6,}$", sent.quote_number)
    assert sent.sent_at is not None
    assert sent.billing_snapshot is not None
    assert sent.content_hash and len(sent.content_hash) == 64


@pytest.mark.django_db
def test_send_quote_nur_entwurf(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 5,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )
    beleg_service.send_quote(app_user.id, quote_id=q.id)
    with pytest.raises(ValueError):
        beleg_service.send_quote(app_user.id, quote_id=q.id)


# --- Rechnungsbeteiligte ---------------------------------------------------

@pytest.mark.django_db
def test_add_invoice_party_ungueltige_rolle(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 1,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )
    with pytest.raises(ValueError):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id, role="CHEF"
        )


@pytest.mark.django_db
def test_add_invoice_party_gesamtschuld_ohne_grundlage(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 1,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )
    with pytest.raises(ValueError):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id,
            role="INVOICE_DEBTOR", liability_group="G1",
        )
