"""Service-Tests Mailversand: Verschlüsselung, Versand (SMTP gemockt), Protokoll.

KEIN echter Netzverkehr: `django.core.mail.get_connection` wird gemockt
(unittest.mock.patch auf die im Service importierte Referenz). Der Fernet-
Schlüssel wird per override_settings auf einen Wegwerf-Testschlüssel gesetzt —
NIE ein Klartext-Passwort im Repo.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core import mail_crypto
from db_core.mail_crypto import MailKeyError
from db_core.models import Communication, MailAccount
from db_core.services import mail as mail_service
from db_core.services.mail import MailSendError

from api.tests.conftest import make_app_user

# Wegwerf-Fernet-Schlüssel nur für die Tests (kein Repo-Geheimnis).
TEST_KEY = Fernet.generate_key().decode()
KEY = override_settings(MCN_MAIL_KEY=TEST_KEY)

PLAIN_PW = "s3hr-geheim-2026"


def _actor():
    return make_app_user().id


@pytest.mark.django_db
@KEY
def test_passwort_wird_verschluesselt_gespeichert():
    actor = _actor()
    acc = mail_service.set_mail_account(
        actor, label="Haupt", host="smtp.example.test", port=587,
        security="STARTTLS", username="post@example.test", password=PLAIN_PW,
        from_address="post@example.test", from_name="Mitra",
    )
    raw = bytes(MailAccount.objects.get(id=acc.id).password_encrypted)
    # Klartext taucht NIRGENDS in der gespeicherten Chiffre auf.
    assert PLAIN_PW.encode() not in raw
    assert raw != PLAIN_PW.encode()
    # Aber entschlüsselbar mit dem Schlüssel.
    assert mail_crypto.decrypt(raw) == PLAIN_PW


@pytest.mark.django_db
@KEY
def test_update_ohne_passwort_laesst_es_unveraendert():
    actor = _actor()
    acc = mail_service.set_mail_account(
        actor, label="Haupt", host="smtp.example.test", port=587,
        security="STARTTLS", password=PLAIN_PW,
        from_address="post@example.test",
    )
    vorher = bytes(acc.password_encrypted)
    # Update ohne password → Chiffre bleibt.
    acc2 = mail_service.set_mail_account(
        actor, label="Neu benannt", host="smtp.example.test", port=465,
        security="SSL", from_address="post@example.test",
    )
    assert acc2.id == acc.id
    assert bytes(acc2.password_encrypted) == vorher
    assert acc2.label == "Neu benannt"
    assert acc2.security == "SSL"


@pytest.mark.django_db
@KEY
def test_genau_ein_aktives_konto():
    actor = _actor()
    mail_service.set_mail_account(
        actor, label="A", host="a.test", port=587, security="STARTTLS",
        password=PLAIN_PW, from_address="a@a.test",
    )
    mail_service.set_mail_account(
        actor, label="B", host="b.test", port=25, security="NONE",
        from_address="b@b.test",
    )
    # Upsert: es bleibt bei genau einer (aktiven) Zeile.
    assert MailAccount.objects.filter(active=True).count() == 1
    assert MailAccount.objects.count() == 1
    assert mail_service.get_mail_account().host == "b.test"


@pytest.mark.django_db
@KEY
def test_ungueltiger_port_und_adresse():
    actor = _actor()
    with pytest.raises(ValueError):
        mail_service.set_mail_account(
            actor, label="A", host="a.test", port=70000, security="NONE",
            from_address="a@a.test",
        )
    with pytest.raises(ValueError):
        mail_service.set_mail_account(
            actor, label="A", host="a.test", port=25, security="NONE",
            from_address="keine-adresse",
        )


@pytest.mark.django_db
def test_fehlender_schluessel_ist_fail_closed():
    """Ohne MCN_MAIL_KEY schlägt das Verschlüsseln beim Speichern fehl."""
    actor = _actor()
    with override_settings(MCN_MAIL_KEY=""):
        with pytest.raises(MailKeyError):
            mail_service.set_mail_account(
                actor, label="A", host="a.test", port=587, security="STARTTLS",
                password=PLAIN_PW, from_address="a@a.test",
            )
    # Nichts wurde geschrieben (Verschlüsselung vor der Transaktion).
    assert MailAccount.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_mail_baut_nachricht_und_protokolliert():
    actor = _actor()
    mail_service.set_mail_account(
        actor, label="Haupt", host="smtp.example.test", port=587,
        security="STARTTLS", username="post@example.test", password=PLAIN_PW,
        from_address="post@example.test", from_name="Mitra Sanitär",
    )

    fake_conn = MagicMock(name="smtp_connection")
    fake_conn.send_messages.return_value = 1
    with patch("db_core.services.mail.get_connection", return_value=fake_conn) as gc:
        comm = mail_service.send_mail(
            actor, to_address="kunde@example.test", subject="Hallo",
            body="Testtext",
        )

    # Verbindung mit den Kontodaten aufgebaut (STARTTLS → use_tls, nicht use_ssl).
    _, kwargs = gc.call_args
    assert kwargs["host"] == "smtp.example.test"
    assert kwargs["port"] == 587
    assert kwargs["username"] == "post@example.test"
    assert kwargs["password"] == PLAIN_PW
    assert kwargs["use_tls"] is True
    assert kwargs["use_ssl"] is False
    assert kwargs["fail_silently"] is False

    # Nachricht korrekt aufgebaut.
    sent = fake_conn.send_messages.call_args[0][0][0]
    assert sent.subject == "Hallo"
    assert sent.body == "Testtext"
    assert sent.to == ["kunde@example.test"]
    assert sent.from_email == "Mitra Sanitär <post@example.test>"

    # Protokolliert als AUSGEHENDE EMAIL im Klärungskorb.
    row = Communication.objects.get(id=comm.id)
    assert row.channel == "EMAIL"
    assert row.direction == "AUSGEHEND"
    assert row.counterpart_raw == "kunde@example.test"
    assert row.recorded_by == actor
    assert row.assignment_status == "KLAERUNGSKORB"


@pytest.mark.django_db
@KEY
def test_send_mail_ohne_aktives_konto_fehler():
    actor = _actor()
    with pytest.raises(ValueError):
        mail_service.send_mail(
            actor, to_address="x@y.test", subject="s", body="b",
        )
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_smtp_fehler_wird_uebersetzt_und_nichts_protokolliert():
    actor = _actor()
    mail_service.set_mail_account(
        actor, label="Haupt", host="smtp.example.test", port=587,
        security="STARTTLS", password=PLAIN_PW, from_address="post@example.test",
    )
    fake_conn = MagicMock()
    fake_conn.send_messages.side_effect = OSError("Connection refused")
    with patch("db_core.services.mail.get_connection", return_value=fake_conn):
        with pytest.raises(MailSendError) as exc:
            mail_service.send_mail(
                actor, to_address="x@y.test", subject="s", body="b",
            )
    # Meldung enthält weder Passwort noch die rohe SMTP-Ursache.
    assert PLAIN_PW not in str(exc.value)
    assert "refused" not in str(exc.value).lower()
    # Kein Protokoll bei Fehlversand.
    assert Communication.objects.count() == 0
