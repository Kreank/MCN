"""Der erledigte Kreditbeleg bleibt im UI auffindbar (API-Seite von Befund 2).

`payment_status` leitet sich jetzt aus dem OFFENEN BETRAG ab (siehe
`db_core/tests/test_zahlungsstatus.py`). Dieser Test hält die Folge davon an der
API fest: Ein vollständig erstatteter Kreditbeleg verschwindet aus dem Filter
„Offen" und ist unter „Bezahlt" zu finden — er wird nicht unauffindbar.

Zusätzlich der Bestandsfall: Die stornierte Rechnung steht auf AUSGEGLICHEN und
ist über genau diesen Filter erreichbar (das Segment dafür fehlte im Frontend).
"""
from datetime import timedelta
from decimal import Decimal

import pytest

from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

from .test_forderung_grenze_api import (
    _BRUTTO,
    _HEUTE,
    _POS_2_BRUTTO,
    _gepruefter_auftrag,
    _veroeffentlichte_rechnung,
)

_NULL = Decimal("0.00")


def _ids(admin_client, status):
    body = admin_client.get(
        f"/api/buchhaltung/invoices?payment_status={status}&page_size=100"
    ).json()
    return {i["id"]: i for i in body["items"]}


@pytest.mark.django_db
def test_erstatteter_kreditbeleg_steht_unter_bezahlt_statt_offen(
    admin_client, app_user
):
    """Bezahlte Rechnung + Gutschrift −595,00 € + Rückerstattung → offen 0,00 €.

    Gegen den alten Code stand der erledigte Kreditbeleg dauerhaft im Filter
    „Offen" (weil `paid` nach der Erstattung negativ ist und die Statusableitung
    auf `paid <= 0` entschied, bevor sie den offenen Betrag ansah).

    Die Rechnung wird hier zuerst **voll bezahlt**: Nur dann ist überhaupt Geld da,
    das zu erstatten wäre. Auf einer offenen Rechnung wird die Gutschrift verrechnet
    (siehe `test_gutschrift_auf_offene_rechnung_wird_verrechnet`).
    """
    inv = _veroeffentlichte_rechnung(app_user, name="Erstattungs-Objekt")["inv"]
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=_BRUTTO, paid_at=_HEUTE
    )
    gutschrift = beleg_service.create_correction(
        app_user.id, invoice_id=inv.id, positions=[2]
    )
    assert gutschrift.gross_total == -_POS_2_BRUTTO
    # Vorbedingung: unerstattet ist der Kreditbeleg offen (zugunsten des Kunden).
    assert str(gutschrift.id) in _ids(admin_client, "OFFEN")

    buchhaltung_service.record_payment(
        app_user.id, invoice_id=gutschrift.id, amount=_POS_2_BRUTTO,
        paid_at=_HEUTE, payment_type="RUECKERSTATTUNG",
    )

    assert str(gutschrift.id) not in _ids(admin_client, "OFFEN")
    zeile = _ids(admin_client, "BEZAHLT")[str(gutschrift.id)]
    assert Decimal(zeile["open_amount"]) == _NULL
    assert zeile["ist_forderung"] is False


@pytest.mark.django_db
def test_gutschrift_auf_offene_rechnung_wird_verrechnet(admin_client, app_user):
    """Teilgutschrift auf eine UNBEZAHLTE Rechnung: **nichts zu erstatten.**

    Der Kreditbeleg wird mit der offenen Forderung verrechnet (285,60 € bleiben zu
    zahlen) und steht selbst auf 0,00 € — AUSGEGLICHEN, nicht „OFFEN". Vorher stand
    er mit −595,00 € als Erstattungspflicht in der Liste, obwohl der Kunde nie einen
    Cent gezahlt hatte.
    """
    inv = _veroeffentlichte_rechnung(app_user, name="Verrechnungs-Objekt")["inv"]
    gutschrift = beleg_service.create_correction(
        app_user.id, invoice_id=inv.id, positions=[2]
    )

    kredit = _ids(admin_client, "AUSGEGLICHEN")[str(gutschrift.id)]
    assert Decimal(kredit["verrechnet"]) == _POS_2_BRUTTO
    assert Decimal(kredit["open_amount"]) == _NULL
    assert Decimal(kredit["zu_erstatten"]) == _NULL

    original = _ids(admin_client, "OFFEN")[str(inv.id)]
    assert Decimal(original["open_amount"]) == _BRUTTO - _POS_2_BRUTTO


@pytest.mark.django_db
def test_stornierte_rechnung_ist_ueber_den_filter_ausgeglichen_erreichbar(
    admin_client, app_user
):
    """AUSGEGLICHEN ist ein echter Segmentwert — kein Beleg darf nur unter „Alle"
    auffindbar sein."""
    inv = _veroeffentlichte_rechnung(app_user, name="Ausgeglichen-Objekt")["inv"]
    beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    zeile = _ids(admin_client, "AUSGEGLICHEN")[str(inv.id)]
    assert Decimal(zeile["open_amount"]) == _NULL
    assert zeile["is_storniert"] is True


@pytest.mark.django_db
def test_ueberzahlte_rechnung_ist_ueber_den_filter_ueberzahlt_erreichbar(
    admin_client, app_user
):
    """**Echte** Überzahlung: Der Kunde überweist mehr, als in Rechnung steht.

    Sie steht am Original (UEBERZAHLT, negativer offener Betrag) und bleibt
    filterbar — die Erstattung darf nicht in der Liste untergehen. Der Verrechnungs-
    Slice fasst diesen Fall **nicht** an: Ohne Kreditbeleg gibt es nichts zu
    verrechnen. (Der Storno einer bezahlten Rechnung führt dagegen nicht mehr
    hierher — seine Erstattungspflicht steht auf dem Kreditbeleg, siehe
    `test_forderung_grenze_api.test_teilzahlung_auf_stornierte_rechnung_wird_nicht_verschluckt`.)
    """
    obj = property_service.create_property(
        app_user.id, name="Ueberzahlt-Objekt", property_type="WEG",
        street="Weg", house_number="3", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Uwe", last_name="Ueberzahler"
    )
    order = _gepruefter_auftrag(app_user, obj, kunde)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        invoice_date=_HEUTE - timedelta(days=30),
        due_date=_HEUTE - timedelta(days=1),
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    assert inv.gross_total == Decimal("285.60")
    # 485,60 € auf eine Rechnung über 285,60 € — 200,00 € zu viel.
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("485.60"), paid_at=_HEUTE,
        payment_type="UEBERZAHLUNG",
    )

    zeile = _ids(admin_client, "UEBERZAHLT")[str(inv.id)]
    assert Decimal(zeile["open_amount"]) == Decimal("-200.00")
    assert Decimal(zeile["zu_erstatten"]) == Decimal("200.00")
    assert Decimal(zeile["verrechnet"]) == _NULL
