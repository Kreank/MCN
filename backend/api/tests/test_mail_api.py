"""API-Tests Mailversand: Rechte-Tore, write-only Passwort, Testmail (gemockt).

KEIN echter Netzverkehr: get_connection wird gemockt. Der Fernet-Schlüssel wird
per override_settings gesetzt.
"""
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core.models import Communication, MailAccount

TEST_KEY = Fernet.generate_key().decode()
KEY = override_settings(MCN_MAIL_KEY=TEST_KEY)

PW = "s3hr-geheim-2026"

VALID = {
    "label": "Haupt",
    "host": "smtp.example.test",
    "port": 587,
    "security": "STARTTLS",
    "username": "post@example.test",
    "password": PW,
    "from_address": "post@example.test",
    "from_name": "Mitra",
}


def _put(client, **overrides):
    body = {**VALID, **overrides}
    return client.put("/api/company/mail-account", data=body,
                      content_type="application/json")


@pytest.mark.django_db
def test_lesen_leer(admin_client):
    r = admin_client.get("/api/company/mail-account")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False
    assert body["has_password"] is False
    assert "password" not in body


@pytest.mark.django_db
@KEY
def test_anlegen_und_passwort_niemals_zurueckgegeben(admin_client):
    r = _put(admin_client)
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["exists"] is True
    assert body["has_password"] is True
    # Passwort taucht in KEINER Antwort auf.
    assert "password" not in body
    assert PW not in r.content.decode()

    g = admin_client.get("/api/company/mail-account").json()
    assert g["host"] == "smtp.example.test"
    assert g["has_password"] is True
    assert "password" not in g

    # In der DB liegt nur die Chiffre, kein Klartext.
    raw = bytes(MailAccount.objects.get().password_encrypted)
    assert PW.encode() not in raw


@pytest.mark.django_db
@KEY
def test_aendern_nur_lesen_403(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = _put(c)
    assert r.status_code == 403
    assert MailAccount.objects.count() == 0


@pytest.mark.django_db
def test_lesen_fuer_nur_lesen_erlaubt(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = c.get("/api/company/mail-account")
    assert r.status_code == 200


@pytest.mark.django_db
@KEY
def test_ungueltiger_port_422(admin_client):
    r = _put(admin_client, port=70000)
    assert r.status_code == 422
    assert "Port" in r.json()["detail"]


@pytest.mark.django_db
def test_speichern_ohne_schluessel_fail_closed_422(admin_client):
    with override_settings(MCN_MAIL_KEY=""):
        r = _put(admin_client)
    assert r.status_code == 422
    assert "MCN_MAIL_KEY" in r.json()["detail"]
    assert MailAccount.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_testmail_erfolg(admin_client):
    assert _put(admin_client).status_code == 200
    fake_conn = MagicMock()
    fake_conn.send_messages.return_value = 1
    with patch("db_core.services.mail.get_connection", return_value=fake_conn):
        r = admin_client.post(
            "/api/company/mail-account/test",
            data={"to_address": "ziel@example.test"},
            content_type="application/json",
        )
    assert r.status_code == 200, r.content
    assert r.json()["sent"] is True
    sent = fake_conn.send_messages.call_args[0][0][0]
    assert sent.subject == "MCN Testmail"
    assert sent.to == ["ziel@example.test"]
    assert Communication.objects.filter(channel="EMAIL", direction="AUSGEHEND").count() == 1


@pytest.mark.django_db
@KEY
def test_testmail_smtp_fehler_422_passwortfrei(admin_client):
    assert _put(admin_client).status_code == 200
    fake_conn = MagicMock()
    fake_conn.send_messages.side_effect = OSError("Connection refused")
    with patch("db_core.services.mail.get_connection", return_value=fake_conn):
        r = admin_client.post(
            "/api/company/mail-account/test",
            data={"to_address": "ziel@example.test"},
            content_type="application/json",
        )
    assert r.status_code == 422
    assert PW not in r.content.decode()
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_testmail_ohne_konto_422(admin_client):
    r = admin_client.post(
        "/api/company/mail-account/test",
        data={"to_address": "ziel@example.test"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Mailkonto" in r.json()["detail"]


@pytest.mark.django_db
@KEY
def test_testmail_nur_lesen_403(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/company/mail-account/test",
        data={"to_address": "ziel@example.test"},
        content_type="application/json",
    )
    assert r.status_code == 403
