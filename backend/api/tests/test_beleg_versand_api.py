"""API-Tests Rechnungsversand per E-Mail (POST /invoicing/invoices/{id}/send-email).

KEIN echter Netzverkehr: get_connection wird gemockt. Der Fernet-Schlüssel wird
per override_settings gesetzt, damit sich ein Mailkonto mit Passwort anlegen und
später entschlüsseln lässt.

Geprüft wird: Versand einer veröffentlichten Rechnung baut die Mail mit
PDF-Anhang und protokolliert eine content.communication; Entwurf → 422; kein
Empfänger/keine E-Mail → 422; SMTP-Fehler passwortfrei → 422; Rechte-Gate 403;
Adress-Override; recipient_email in der Detailantwort.
"""
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core.models import Communication
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import mail as mail_service
from db_core.services import property as property_service

from .test_beleg_publish_api import _gepruefter_auftrag

TEST_KEY = Fernet.generate_key().decode()
KEY = override_settings(MCN_MAIL_KEY=TEST_KEY)

PW = "smtp-geheim-2026"

SEND_URL = "/api/invoicing/invoices/{}/send-email"


def _mailkonto(app_user, *, password=PW):
    """Aktives SMTP-Absenderkonto anlegen (mit Passwort → Fernet-Chiffre)."""
    return mail_service.set_mail_account(
        app_user.id, label="Haupt", host="smtp.example.test", port=587,
        security="STARTTLS", username="post@example.test", password=password,
        from_address="post@example.test", from_name="Mitra",
    )


def _published_invoice(app_user, *, with_email=True):
    """Veröffentlichte Rechnung; die Empfängerpartei erhält optional eine EMAIL."""
    obj = property_service.create_property(
        app_user.id, name="Versand-Objekt", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    weg = identity_service.create_person(
        app_user.id, first_name="Petra", last_name="Post"
    )
    if with_email:
        identity_service.add_contact_point(
            app_user.id, weg.id, contact_type="EMAIL",
            value="kunde@example.test", is_primary=True,
        )
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()  # Belegnummer wird erst bei der Veröffentlichung vergeben.
    return inv, weg


def _fake_ok():
    conn = MagicMock()
    conn.send_messages.return_value = 1
    return conn


def _sent_message(conn):
    """Die an die SMTP-Verbindung übergebene EmailMessage."""
    return conn.send_messages.call_args[0][0][0]


@pytest.mark.django_db
@KEY
def test_send_invoice_email_erfolg(admin_client, app_user):
    _mailkonto(app_user)
    inv, weg = _published_invoice(app_user)
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(SEND_URL.format(inv.id), content_type="application/json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["sent"] is True
    assert body["to_address"] == "kunde@example.test"

    msg = _sent_message(conn)
    assert msg.to == ["kunde@example.test"]
    assert inv.invoice_number in msg.subject
    # Genau ein PDF-Anhang.
    assert len(msg.attachments) == 1
    filename, content, mimetype = msg.attachments[0]
    assert filename.endswith(".pdf")
    assert mimetype == "application/pdf"
    assert bytes(content)[:4] == b"%PDF"

    # Protokolliert als ausgehende, kaufmännische E-Mail an die Empfängerpartei.
    comm = Communication.objects.get(channel="EMAIL", direction="AUSGEHEND")
    assert comm.is_commercial is True
    assert str(comm.counterpart_party_id) == str(weg.id)
    assert comm.counterpart_raw == "kunde@example.test"


@pytest.mark.django_db
@KEY
def test_send_invoice_email_adress_override(admin_client, app_user):
    _mailkonto(app_user)
    inv, _ = _published_invoice(app_user)
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(
            SEND_URL.format(inv.id),
            data={"to_address": "abweichend@example.test"},
            content_type="application/json",
        )
    assert r.status_code == 200, r.content
    assert r.json()["to_address"] == "abweichend@example.test"
    assert _sent_message(conn).to == ["abweichend@example.test"]


@pytest.mark.django_db
@KEY
def test_send_entwurf_422(admin_client, app_user):
    _mailkonto(app_user)
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "X", "quantity": 1,
                "unit_price": "1.00", "tax_code": "DE_19"}],
    )
    r = admin_client.post(SEND_URL.format(inv.id), content_type="application/json")
    assert r.status_code == 422
    assert "veröffentlicht" in r.json()["detail"].lower()
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_ohne_empfaenger_email_422(admin_client, app_user):
    _mailkonto(app_user)
    inv, _ = _published_invoice(app_user, with_email=False)
    r = admin_client.post(SEND_URL.format(inv.id), content_type="application/json")
    assert r.status_code == 422
    assert "E-Mail" in r.json()["detail"]
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_smtp_fehler_422_passwortfrei(admin_client, app_user):
    _mailkonto(app_user)
    inv, _ = _published_invoice(app_user)
    conn = MagicMock()
    conn.send_messages.side_effect = OSError("Connection refused")
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(SEND_URL.format(inv.id), content_type="application/json")
    assert r.status_code == 422
    # Das SMTP-Passwort taucht in keiner Fehlermeldung auf.
    assert PW not in r.content.decode()
    # Kein stiller Fehlschlag, aber auch keine Protokollzeile bei Sendefehler.
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_ohne_mailkonto_422(admin_client, app_user):
    inv, _ = _published_invoice(app_user)
    r = admin_client.post(SEND_URL.format(inv.id), content_type="application/json")
    assert r.status_code == 422
    assert "Mailkonto" in r.json()["detail"]


@pytest.mark.django_db
@KEY
def test_send_recht_gate_403(client_with_role, app_user):
    _mailkonto(app_user)
    inv, _ = _published_invoice(app_user)
    c = client_with_role("NUR_LESEN")
    r = c.post(SEND_URL.format(inv.id), content_type="application/json")
    assert r.status_code == 403
    assert Communication.objects.count() == 0


@pytest.mark.django_db
def test_send_ohne_login_abgelehnt(anonymous_client, app_user):
    inv, _ = _published_invoice(app_user)
    r = anonymous_client.post(SEND_URL.format(inv.id), content_type="application/json")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_detail_liefert_recipient_email(admin_client, app_user):
    """Die Detailantwort belegt die Dialog-Vorbelegung mit der Empfänger-EMAIL."""
    inv, _ = _published_invoice(app_user)
    r = admin_client.get(f"/api/invoicing/invoices/{inv.id}")
    assert r.status_code == 200
    assert r.json()["recipient_email"] == "kunde@example.test"
