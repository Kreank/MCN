"""Der gemahnte Betrag ist der offene Betrag — EINE Rechenstelle, auch im Brief.

Review-Befund (HOCH, kundenwirksam): `beleg_versand._open_amount` rechnete den
offenen Betrag ein **zweites** Mal aus (`gross_total − Σ PAYMENT_SIGN·amount`) und
kannte dabei die **Gutschriften nicht**. Bildschirm, Mahnlauf und offene Posten
sagten nach einer Teilgutschrift 285,60 € offen — der versendete Mahntext sagte
„Der noch offene Betrag beträgt 880,60 €". Der Kunde wurde über Geld gemahnt, das
das Haus selbst erlassen hatte.

Diese Tests binden den Betrag im **versendeten Text** an
`buchhaltung.zahlungsspiegel(...)["open_amount"]` — die eine Wahrheit. Sie waren
gegen den alten Code rot (Teilgutschrift: 880,60 statt 285,60).
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core.models import Invoice
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import identity as identity_service
from db_core.services import mail as mail_service
from db_core.services import property as property_service

from .test_forderung_grenze_api import (
    _BRUTTO,
    _HEUTE,
    _POS_1_BRUTTO,
    _POS_2_BRUTTO,
    _gepruefter_auftrag,
)

TEST_KEY = Fernet.generate_key().decode()
KEY = override_settings(MCN_MAIL_KEY=TEST_KEY)

SEND_URL = "/api/buchhaltung/dunning-notices/{}/send-email"


def _mailkonto(app_user):
    return mail_service.set_mail_account(
        app_user.id, label="Haupt", host="smtp.example.test", port=587,
        security="STARTTLS", username="post@example.test", password="smtp-geheim",
        from_address="post@example.test", from_name="Mitra",
    )


def _rechnung(app_user, *, name, skonto=False):
    """Überfällige, veröffentlichte Rechnung (880,60 € brutto) mit Schuldner-EMAIL.

    Zwei Positionen (285,60 € + 595,00 €), damit die Teilgutschrift greifbar ist.
    """
    obj = property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Klara", last_name="Kundin"
    )
    identity_service.add_contact_point(
        app_user.id, kunde.id, contact_type="EMAIL",
        value="schuldner@example.test", is_primary=True,
    )
    order = _gepruefter_auftrag(app_user, obj, kunde)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        invoice_date=_HEUTE - timedelta(days=90),
        due_date=_HEUTE - timedelta(days=30),
        payment_term_days=60 if skonto else None,
        discount_percent=Decimal("2.00") if skonto else None,
        discount_days=14 if skonto else None,
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
            {"line_type": "ARBEITSZEIT", "description": "Montage", "quantity": 10,
             "unit": "h", "unit_price": "50.00", "tax_code": "DE_19"},
        ],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    assert inv.gross_total == _BRUTTO
    return inv


def _mahnung(app_user, invoice_id, level=1):
    notice = None
    for lv in range(1, level + 1):
        notice = buchhaltung_service.issue_dunning_notice(
            app_user.id, invoice_id=invoice_id, level=lv,
            issued_at=_HEUTE - timedelta(days=level - lv + 1),
        )
    return notice


def _offener_betrag(invoice_id):
    """Die EINE Wahrheit: der offene Betrag aus dem Zahlungsspiegel."""
    inv = buchhaltung_service.mit_zahlungsstand(
        Invoice.objects.filter(id=invoice_id)
    ).get()
    return buchhaltung_service.zahlungsspiegel(inv, heute=_HEUTE)["open_amount"]


def _versendeter_text(admin_client, notice_id):
    conn = MagicMock()
    conn.send_messages.return_value = 1
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(
            SEND_URL.format(notice_id), content_type="application/json"
        )
    assert r.status_code == 200, r.content
    return conn.send_messages.call_args[0][0][0].body


def _betragszeile(text):
    """Die eine Betragszeile aus dem Mahntext („Der noch offene Betrag …")."""
    zeilen = [z for z in text.splitlines() if "offene Betrag beträgt" in z]
    assert len(zeilen) == 1, f"Erwartet genau eine Betragszeile, gefunden: {zeilen}"
    return zeilen[0]


# ===========================================================================
# BRUCHFALL — Teilgutschrift: der Brief nennt den ALTEN Betrag
# ===========================================================================

@pytest.mark.django_db
@KEY
def test_mahnbetrag_nach_teilgutschrift_ist_der_geminderte_betrag(
    admin_client, app_user
):
    """Rechnung 880,60 € + Teilgutschrift 595,00 € → gemahnt werden 285,60 €.

    Gegen den alten Code stand im versendeten Text „880,60 EUR": `_open_amount`
    kannte die Gutschriften nicht. Der Kunde wurde über Geld gemahnt, das das Haus
    selbst erlassen hatte.
    """
    _mailkonto(app_user)
    inv = _rechnung(app_user, name="Mahnbetrag-Gutschrift")
    gutschrift = beleg_service.create_correction(
        app_user.id, invoice_id=inv.id, positions=[2]
    )
    assert gutschrift.gross_total == -_POS_2_BRUTTO

    offen = _offener_betrag(inv.id)
    assert offen == _POS_1_BRUTTO, "Vorbedingung: Bildschirm/Mahnlauf sagen 285,60 €."

    notice = _mahnung(app_user, inv.id)
    zeile = _betragszeile(_versendeter_text(admin_client, notice.id))

    assert "285,60 EUR" in zeile
    assert "880,60" not in zeile, "Der erlassene Betrag darf nicht gemahnt werden."


# ===========================================================================
# Teilzahlung und Skonto — dieselbe Rechenstelle, keine zweite Wahrheit
# ===========================================================================

@pytest.mark.django_db
@KEY
def test_mahnbetrag_nach_teilzahlung_ist_der_restbetrag(admin_client, app_user):
    """Teilzahlung 300,00 € → gemahnt werden 580,60 € (= Zahlungsspiegel)."""
    _mailkonto(app_user)
    inv = _rechnung(app_user, name="Mahnbetrag-Teilzahlung")
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("300.00"),
        paid_at=_HEUTE - timedelta(days=5), payment_type="TEILZAHLUNG",
    )
    assert _offener_betrag(inv.id) == Decimal("580.60")

    notice = _mahnung(app_user, inv.id)
    zeile = _betragszeile(_versendeter_text(admin_client, notice.id))
    assert "580,60 EUR" in zeile


@pytest.mark.django_db
@KEY
def test_mahnbetrag_bei_skonto_bleibt_der_volle_offene_betrag(admin_client, app_user):
    """**Skonto bucht nichts aus** (Projektgrenze aus dem Skonto-Slice).

    Die Skontofrist ist bei einer überfälligen Rechnung ohnehin abgelaufen; gemahnt
    wird der volle offene Betrag, nicht der Skonto-Zahlbetrag. Der Test hält fest,
    dass die konsolidierte Rechenstelle daran nichts ändert.
    """
    _mailkonto(app_user)
    inv = _rechnung(app_user, name="Mahnbetrag-Skonto", skonto=True)
    assert inv.discount_percent == Decimal("2.00")
    assert _offener_betrag(inv.id) == _BRUTTO

    notice = _mahnung(app_user, inv.id)
    zeile = _betragszeile(_versendeter_text(admin_client, notice.id))
    assert "880,60 EUR" in zeile
    # Der Skonto-Zahlbetrag (98 % von 880,60 = 862,99) wird NICHT gemahnt.
    assert "862,99" not in zeile


@pytest.mark.django_db
@KEY
def test_mahnbetrag_bei_teilgutschrift_und_teilzahlung(admin_client, app_user):
    """Beides zusammen: 880,60 − 595,00 (Gutschrift) − 100,00 (Zahlung) = 185,60 €."""
    _mailkonto(app_user)
    inv = _rechnung(app_user, name="Mahnbetrag-Beides")
    beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[2])
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("100.00"),
        paid_at=_HEUTE - timedelta(days=3), payment_type="TEILZAHLUNG",
    )
    offen = _offener_betrag(inv.id)
    assert offen == Decimal("185.60")

    notice = _mahnung(app_user, inv.id)
    zeile = _betragszeile(_versendeter_text(admin_client, notice.id))
    assert "185,60 EUR" in zeile


@pytest.mark.django_db
@KEY
def test_kein_betragssatz_wenn_nichts_mehr_offen_ist(admin_client, app_user):
    """Vollgutschrift nach der Mahnung → kein erfundener Betrag im Text.

    Der offene Betrag ist 0,00 €; der Brief nennt dann **keinen** Betrag (statt,
    wie vorher, den vollen Bruttobetrag).
    """
    _mailkonto(app_user)
    inv = _rechnung(app_user, name="Mahnbetrag-Vollgutschrift")
    notice = _mahnung(app_user, inv.id)
    beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[1, 2])
    assert _offener_betrag(inv.id) == Decimal("0.00")

    text = _versendeter_text(admin_client, notice.id)
    assert "offene Betrag beträgt" not in text
