"""Service-Tests der Auswertungen (lesende Aggregationen) gegen die Test-DB.

Veröffentlichte Rechnungen werden über den echten publish-Pfad erzeugt (die
DEFERRED-Freigabe-Tore feuern unter der pytest-Transaktion nicht — ihre
Korrektheit prüft der Beleg-Slice; hier zählt nur die Aggregation).
"""
from datetime import date

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import auswertungen as auswertungen_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service


def _property(app_user, name="Objekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user):
    return identity_service.create_person(app_user.id, first_name="W", last_name="EG")


def _gepruefter_auftrag(app_user, obj, debtor):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag"
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


def _published_invoice(app_user, obj, party, *, unit_price, quantity=1,
                       invoice_date=None, invoice_type="RECHNUNG",
                       reference_invoice_id=None):
    """Erzeugt eine veröffentlichte Rechnung (net = quantity*unit_price) über den
    vollständigen, gültigen Publish-Pfad (geprüfter Auftrag + Beteiligte)."""
    work_order_id = None
    if invoice_type not in ("GUTSCHRIFT", "STORNO"):
        work_order_id = _gepruefter_auftrag(app_user, obj, party).id
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type=invoice_type,
        work_order_id=work_order_id, invoice_date=invoice_date,
        reference_invoice_id=reference_invoice_id,
        lines=[
            {"line_type": "MATERIAL", "description": "X", "quantity": quantity,
             "unit": "Stk", "unit_price": str(unit_price), "tax_code": "DE_19"},
        ],
    )
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=party.id,
        role="INVOICE_DEBTOR", is_primary=True,
    )
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=party.id,
        role="INVOICE_RECIPIENT", is_primary=True,
    )
    return beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)


@pytest.mark.django_db
def test_list_dashboards_enthaelt_umsatz(app_user):
    keys = {d["key"] for d in auswertungen_service.list_dashboards()}
    assert "umsatz-projektuebersicht" in keys


@pytest.mark.django_db
def test_umsatz_zaehlt_nur_veroeffentlichte(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice(app_user, obj, weg, unit_price="100.00")  # net 100
    # Entwurf (nicht veröffentlicht) — darf nicht zählen.
    beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Y", "quantity": 1,
                "unit_price": "500.00", "tax_code": "DE_19"}],
    )
    s = auswertungen_service.umsatz_projektuebersicht_summary()
    assert s["revenue"]["net_total"] == "100.00"
    assert s["revenue"]["invoice_count"] == 1


@pytest.mark.django_db
def test_gutschrift_mindert_umsatz(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice(app_user, obj, weg, unit_price="100.00")  # +100
    r2 = _published_invoice(app_user, obj, weg, unit_price="30.00")  # +30 → 130
    # Vollstorno von r2 erzeugt einen Beleg mit net −30 (echte Folgebeleg-Kette).
    beleg_service.create_cancellation(app_user.id, invoice_id=r2.id)
    s = auswertungen_service.umsatz_projektuebersicht_summary()
    assert s["revenue"]["net_total"] == "100.00"  # 130 − 30
    assert s["revenue"]["invoice_count"] == 2  # Korrekturbelege zählen nicht
    assert s["revenue"]["credit_count"] == 1


@pytest.mark.django_db
def test_kunden_umsatz_je_kunde_sortiert(app_user):
    obj = _property(app_user)
    anna = identity_service.create_person(app_user.id, first_name="Anna", last_name="A")
    bodo = identity_service.create_person(app_user.id, first_name="Bodo", last_name="B")
    _published_invoice(app_user, obj, anna, unit_price="100.00")
    _published_invoice(app_user, obj, bodo, unit_price="300.00")
    s = auswertungen_service.kunden_summary()
    assert s["customer_count"] == 2
    assert s["net_total"] == "400.00"
    # Nach Netto-Umsatz absteigend → Bodo zuerst.
    assert s["customers"][0]["display_name"] == "Bodo B"
    assert s["customers"][0]["net_total"] == "300.00"
    assert s["customers"][1]["net_total"] == "100.00"


@pytest.mark.django_db
def test_kunden_storno_mindert_kundenumsatz(app_user):
    obj = _property(app_user)
    anna = identity_service.create_person(app_user.id, first_name="Anna", last_name="A")
    r = _published_invoice(app_user, obj, anna, unit_price="100.00")
    beleg_service.create_cancellation(app_user.id, invoice_id=r.id)
    s = auswertungen_service.kunden_summary()
    row = next(c for c in s["customers"] if c["display_name"] == "Anna A")
    assert row["net_total"] == "0.00"
    assert row["invoice_count"] == 1
    assert row["credit_count"] == 1


@pytest.mark.django_db
def test_projekte_nach_gewerk_und_status(app_user):
    projekt_service.create_project(app_user.id, name="P1")
    projekt_service.create_project(app_user.id, name="P2")
    s = auswertungen_service.umsatz_projektuebersicht_summary()
    assert s["projects"]["total"] == 2
    assert s["projects"]["open"] == 2
    assert s["projects"]["by_gewerk"] == [{"name": "Ohne Kategorie", "count": 2}]


@pytest.mark.django_db
def test_datumsfilter_grenzt_umsatz_ein(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice(app_user, obj, weg, unit_price="100.00", invoice_date=date(2026, 1, 15))
    _published_invoice(app_user, obj, weg, unit_price="200.00", invoice_date=date(2026, 6, 15))
    s = auswertungen_service.umsatz_projektuebersicht_summary(
        date_from=date(2026, 6, 1), date_to=date(2026, 6, 30)
    )
    assert s["revenue"]["net_total"] == "200.00"
    assert s["revenue"]["invoice_count"] == 1
    # Zeitstrahl enthält genau den Junimonat.
    assert s["timeline"] == [{"month": "2026-06", "net": "200.00"}]


@pytest.mark.django_db
def test_umsatzverlauf_gruppiert_nach_monat(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice(app_user, obj, weg, unit_price="100.00", invoice_date=date(2026, 1, 10))
    _published_invoice(app_user, obj, weg, unit_price="50.00", invoice_date=date(2026, 1, 20))
    _published_invoice(app_user, obj, weg, unit_price="80.00", invoice_date=date(2026, 2, 5))
    s = auswertungen_service.umsatz_projektuebersicht_summary()
    monate = {t["month"]: t["net"] for t in s["timeline"]}
    assert monate["2026-01"] == "150.00"
    assert monate["2026-02"] == "80.00"
