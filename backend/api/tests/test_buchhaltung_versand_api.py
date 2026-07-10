"""API-Tests Mahnungsversand per E-Mail
(POST /buchhaltung/dunning-notices/{id}/send-email).

KEIN echter Netzverkehr: get_connection wird gemockt. Der Fernet-Schlüssel wird
per override_settings gesetzt, damit sich ein Mailkonto mit Passwort anlegen und
später entschlüsseln lässt.

Geprüft wird: Versand einer ausgestellten Mahnung baut die Mail mit dem
Rechnungs-PDF als Anhang, protokolliert eine content.communication und trägt je
Stufe den passenden Betreff/Ton; kein Schuldner-Kontakt → 422; SMTP-Fehler
passwortfrei → 422; Rechte-Gate 403; unbekannte Mahnung → 404; Adress-Override;
recipient_email in der Detailantwort.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core.models import Communication
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import identity as identity_service
from db_core.services import mail as mail_service
from db_core.services import property as property_service

from .test_beleg_publish_api import _gepruefter_auftrag

TEST_KEY = Fernet.generate_key().decode()
KEY = override_settings(MCN_MAIL_KEY=TEST_KEY)

PW = "smtp-geheim-2026"

SEND_URL = "/api/buchhaltung/dunning-notices/{}/send-email"


def _mailkonto(app_user, *, password=PW):
    return mail_service.set_mail_account(
        app_user.id, label="Haupt", host="smtp.example.test", port=587,
        security="STARTTLS", username="post@example.test", password=password,
        from_address="post@example.test", from_name="Mitra",
    )


def _published_overdue_invoice(app_user, *, with_email=True):
    """Veröffentlichte, fällige Rechnung; der Schuldner erhält optional eine EMAIL."""
    obj = property_service.create_property(
        app_user.id, name="Mahn-Objekt", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    schuldner = identity_service.create_person(
        app_user.id, first_name="Petra", last_name="Post"
    )
    if with_email:
        identity_service.add_contact_point(
            app_user.id, schuldner.id, contact_type="EMAIL",
            value="schuldner@example.test", is_primary=True,
        )
    order = _gepruefter_auftrag(app_user, obj, schuldner)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        invoice_date=date.today() - timedelta(days=90),
        due_date=date.today() - timedelta(days=60),
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=schuldner.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv, schuldner


def _dunning(app_user, invoice_id, level):
    """Stufen 1..level lückenlos ausstellen; gibt die oberste Mahnung zurück."""
    notice = None
    for lv in range(1, level + 1):
        notice = buchhaltung_service.issue_dunning_notice(
            app_user.id, invoice_id=invoice_id, level=lv,
            issued_at=date.today() - timedelta(days=level - lv + 1),
        )
    return notice


def _fake_ok():
    conn = MagicMock()
    conn.send_messages.return_value = 1
    return conn


def _sent_message(conn):
    return conn.send_messages.call_args[0][0][0]


@pytest.mark.django_db
@KEY
def test_send_zahlungserinnerung_erfolg(admin_client, app_user):
    """Stufe 1 (Zahlungserinnerung): freundlicher Betreff/Ton + PDF-Anhang."""
    _mailkonto(app_user)
    inv, schuldner = _published_overdue_invoice(app_user)
    notice = _dunning(app_user, inv.id, 1)
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(SEND_URL.format(notice.id), content_type="application/json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["sent"] is True
    assert body["to_address"] == "schuldner@example.test"

    msg = _sent_message(conn)
    assert msg.to == ["schuldner@example.test"]
    # Betreff trägt das Stufen-Label + die Rechnungsnummer.
    assert msg.subject == f"1. Zahlungserinnerung zu Rechnung {inv.invoice_number}"
    assert "Zahlungserinnerung" in msg.subject
    # Freundlicher Ton (Zahlungserinnerung), keine Zahlungsaufforderung.
    assert "kein vollständiger Zahlungseingang" in msg.body
    assert "fordern Sie hiermit auf" not in msg.body
    # Genau ein PDF-Anhang (die Rechnung).
    assert len(msg.attachments) == 1
    filename, content, mimetype = msg.attachments[0]
    assert filename.endswith(".pdf")
    assert mimetype == "application/pdf"
    assert bytes(content)[:4] == b"%PDF"

    # Protokolliert als ausgehende, kaufmännische E-Mail an den Schuldner.
    comm = Communication.objects.get(channel="EMAIL", direction="AUSGEHEND")
    assert comm.is_commercial is True
    assert str(comm.counterpart_party_id) == str(schuldner.id)
    assert comm.counterpart_raw == "schuldner@example.test"


@pytest.mark.django_db
@KEY
def test_send_mahnung_bestimmterer_ton(admin_client, app_user):
    """Stufe 4 (Mahnung): bestimmter Betreff/Ton (Zahlungsaufforderung)."""
    _mailkonto(app_user)
    inv, _ = _published_overdue_invoice(app_user)
    notice = _dunning(app_user, inv.id, 4)
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(SEND_URL.format(notice.id), content_type="application/json")
    assert r.status_code == 200, r.content
    msg = _sent_message(conn)
    assert msg.subject == f"1. Mahnung zu Rechnung {inv.invoice_number}"
    assert "Mahnung" in msg.subject
    # Bestimmter Ton: Zahlungsaufforderung statt freundlicher Erinnerung.
    assert "fordern Sie hiermit auf" in msg.body
    assert "kein vollständiger Zahlungseingang" not in msg.body


@pytest.mark.django_db
@KEY
def test_send_adress_override(admin_client, app_user):
    _mailkonto(app_user)
    inv, _ = _published_overdue_invoice(app_user)
    notice = _dunning(app_user, inv.id, 1)
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(
            SEND_URL.format(notice.id),
            data={"to_address": "abweichend@example.test"},
            content_type="application/json",
        )
    assert r.status_code == 200, r.content
    assert r.json()["to_address"] == "abweichend@example.test"
    assert _sent_message(conn).to == ["abweichend@example.test"]


@pytest.mark.django_db
@KEY
def test_send_ohne_schuldner_email_422(admin_client, app_user):
    _mailkonto(app_user)
    inv, _ = _published_overdue_invoice(app_user, with_email=False)
    notice = _dunning(app_user, inv.id, 1)
    r = admin_client.post(SEND_URL.format(notice.id), content_type="application/json")
    assert r.status_code == 422
    assert "E-Mail" in r.json()["detail"]
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_smtp_fehler_422_passwortfrei(admin_client, app_user):
    _mailkonto(app_user)
    inv, _ = _published_overdue_invoice(app_user)
    notice = _dunning(app_user, inv.id, 1)
    conn = MagicMock()
    conn.send_messages.side_effect = OSError("Connection refused")
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(SEND_URL.format(notice.id), content_type="application/json")
    assert r.status_code == 422
    # Das SMTP-Passwort taucht in keiner Fehlermeldung auf.
    assert PW not in r.content.decode()
    # Kein stiller Fehlschlag, aber auch keine Protokollzeile bei Sendefehler.
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_ohne_mailkonto_422(admin_client, app_user):
    inv, _ = _published_overdue_invoice(app_user)
    notice = _dunning(app_user, inv.id, 1)
    r = admin_client.post(SEND_URL.format(notice.id), content_type="application/json")
    assert r.status_code == 422
    assert "Mailkonto" in r.json()["detail"]


@pytest.mark.django_db
@KEY
def test_send_unbekannte_mahnung_404(admin_client, app_user):
    _mailkonto(app_user)
    r = admin_client.post(SEND_URL.format(uuid4()), content_type="application/json")
    assert r.status_code == 404


@pytest.mark.django_db
@KEY
def test_send_recht_gate_403(client_with_role, app_user):
    _mailkonto(app_user)
    inv, _ = _published_overdue_invoice(app_user)
    notice = _dunning(app_user, inv.id, 1)
    c = client_with_role("NUR_LESEN")
    r = c.post(SEND_URL.format(notice.id), content_type="application/json")
    assert r.status_code == 403
    assert Communication.objects.count() == 0


@pytest.mark.django_db
def test_send_ohne_login_abgelehnt(anonymous_client, app_user):
    inv, _ = _published_overdue_invoice(app_user)
    notice = _dunning(app_user, inv.id, 1)
    r = anonymous_client.post(SEND_URL.format(notice.id), content_type="application/json")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_detail_liefert_recipient_email_und_notice_id(admin_client, app_user):
    """Die Detailantwort belegt die Dialog-Vorbelegung (Schuldner-EMAIL) und
    liefert je Mahnung die id (zum Adressieren des Versand-Endpunkts)."""
    inv, _ = _published_overdue_invoice(app_user)
    notice = _dunning(app_user, inv.id, 1)
    r = admin_client.get(f"/api/buchhaltung/invoices/{inv.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["recipient_email"] == "schuldner@example.test"
    assert body["dunning"][0]["id"] == str(notice.id)
