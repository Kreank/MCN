"""API-Tests Angebotsversand per E-Mail (POST /invoicing/quotes/{id}/send-email)
und das Angebots-PDF (GET /invoicing/quotes/{id}/pdf).

Spiegelbildlich zum Rechnungsversand. KEIN echter Netzverkehr: get_connection
wird gemockt. Der Fernet-Schlüssel wird per override_settings gesetzt.

Modellunterschied: ein Angebot hat KEINE eigenen Beteiligten — der Empfänger wird
best-effort über den (optionalen) Auftrag abgeleitet (work_order_party
INVOICE_RECIPIENT, ersatzweise PRINCIPAL) → dessen Partei → primäre laufende EMAIL.

Geprüft wird: Versand eines versendeten Angebots baut die Mail mit PDF-Anhang und
protokolliert eine content.communication; Empfänger-Ableitung über
work_order_party; Entwurf → 422; kein Empfänger/keine E-Mail → 422; SMTP-Fehler
passwortfrei → 422; Rechte-Gate 403; Adress-Override; recipient_email in der
Detailantwort; PDF nur ab VERSENDET (Entwurf → 404).
"""
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core.db_context import business_transaction
from db_core.models import Communication, Quote
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import mail as mail_service
from db_core.services import property as property_service

TEST_KEY = Fernet.generate_key().decode()
KEY = override_settings(MCN_MAIL_KEY=TEST_KEY)

PW = "smtp-geheim-2026"

SEND_URL = "/api/invoicing/quotes/{}/send-email"


def _mailkonto(app_user, *, password=PW):
    """Aktives SMTP-Absenderkonto anlegen (mit Passwort → Fernet-Chiffre)."""
    return mail_service.set_mail_account(
        app_user.id, label="Haupt", host="smtp.example.test", port=587,
        security="STARTTLS", username="post@example.test", password=password,
        from_address="post@example.test", from_name="Mitra",
    )


def _entwurf_quote(app_user, *, with_order=True, recipient_role="INVOICE_RECIPIENT",
                   with_email=True):
    """Angebots-Entwurf inkl. optionalem Auftrag mit Empfänger-Beteiligtem."""
    obj = property_service.create_property(
        app_user.id, name="Versand-Angebot", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    weg = identity_service.create_person(
        app_user.id, first_name="Quintus", last_name="Quote"
    )
    if with_email:
        identity_service.add_contact_point(
            app_user.id, weg.id, contact_type="EMAIL",
            value="angebot-kunde@example.test", is_primary=True,
        )
    order = None
    if with_order:
        order = auftrag_service.create_work_order(
            app_user.id, property_id=obj.id, title="Auftrag zum Angebot"
        )
        if recipient_role is not None:
            auftrag_service.add_work_order_party(
                app_user.id, work_order_id=order.id, party_id=weg.id,
                role=recipient_role, is_primary=True,
            )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Bad sanieren",
        lines=[{"line_type": "MATERIAL", "description": "Fliesen", "quantity": 20,
                "unit": "m2", "unit_price": "34.50", "tax_code": "DE_19"}],
    )
    if order is not None:
        with business_transaction(app_user.id):
            Quote.objects.filter(id=quote.id).update(work_order_id=order.id)
    quote.refresh_from_db()
    return quote, weg


def _sent_quote(app_user, **kw):
    quote, weg = _entwurf_quote(app_user, **kw)
    beleg_service.send_quote(app_user.id, quote_id=quote.id)
    quote.refresh_from_db()  # Belegnummer wird erst beim Versand vergeben.
    return quote, weg


def _fake_ok():
    conn = MagicMock()
    conn.send_messages.return_value = 1
    return conn


def _sent_message(conn):
    """Die an die SMTP-Verbindung übergebene EmailMessage."""
    return conn.send_messages.call_args[0][0][0]


@pytest.mark.django_db
@KEY
def test_send_quote_email_erfolg(admin_client, app_user):
    _mailkonto(app_user)
    quote, weg = _sent_quote(app_user)
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["sent"] is True
    # Empfänger best-effort über den Auftrag (INVOICE_RECIPIENT) abgeleitet.
    assert body["to_address"] == "angebot-kunde@example.test"

    msg = _sent_message(conn)
    assert msg.to == ["angebot-kunde@example.test"]
    assert quote.quote_number in msg.subject
    assert "Angebot" in msg.subject
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
    assert comm.counterpart_raw == "angebot-kunde@example.test"


@pytest.mark.django_db
@KEY
def test_send_quote_empfaenger_fallback_principal(admin_client, app_user):
    """Ohne INVOICE_RECIPIENT greift PRINCIPAL als Ersatz-Empfänger."""
    _mailkonto(app_user)
    quote, _ = _sent_quote(app_user, recipient_role="PRINCIPAL")
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code == 200, r.content
    assert r.json()["to_address"] == "angebot-kunde@example.test"


@pytest.mark.django_db
@KEY
def test_send_quote_email_adress_override(admin_client, app_user):
    _mailkonto(app_user)
    quote, _ = _sent_quote(app_user)
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(
            SEND_URL.format(quote.id),
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
    quote, _ = _entwurf_quote(app_user)  # bleibt ENTWURF
    r = admin_client.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code == 422
    assert "versendet" in r.json()["detail"].lower()
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_ohne_empfaenger_email_422(admin_client, app_user):
    """Auftrag mit Empfänger-Rolle, aber ohne hinterlegte EMAIL → 422."""
    _mailkonto(app_user)
    quote, _ = _sent_quote(app_user, with_email=False)
    r = admin_client.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code == 422
    assert "E-Mail" in r.json()["detail"]
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_ohne_auftrag_422(admin_client, app_user):
    """Angebot ohne Auftrag → kein ableitbarer Empfänger → 422 (bis Nutzer eine
    Adresse angibt)."""
    _mailkonto(app_user)
    quote, _ = _sent_quote(app_user, with_order=False, with_email=False)
    r = admin_client.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code == 422
    assert "E-Mail" in r.json()["detail"]
    # Mit manueller Adresse geht es dann durch.
    conn = _fake_ok()
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r2 = admin_client.post(
            SEND_URL.format(quote.id),
            data={"to_address": "manuell@example.test"},
            content_type="application/json",
        )
    assert r2.status_code == 200, r2.content
    assert r2.json()["to_address"] == "manuell@example.test"


@pytest.mark.django_db
@KEY
def test_send_smtp_fehler_422_passwortfrei(admin_client, app_user):
    _mailkonto(app_user)
    quote, _ = _sent_quote(app_user)
    conn = MagicMock()
    conn.send_messages.side_effect = OSError("Connection refused")
    with patch("db_core.services.mail.get_connection", return_value=conn):
        r = admin_client.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code == 422
    # Das SMTP-Passwort taucht in keiner Fehlermeldung auf.
    assert PW not in r.content.decode()
    # Kein stiller Fehlschlag, aber auch keine Protokollzeile bei Sendefehler.
    assert Communication.objects.count() == 0


@pytest.mark.django_db
@KEY
def test_send_ohne_mailkonto_422(admin_client, app_user):
    quote, _ = _sent_quote(app_user)
    r = admin_client.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code == 422
    assert "Mailkonto" in r.json()["detail"]


@pytest.mark.django_db
@KEY
def test_send_recht_gate_403(client_with_role, app_user):
    _mailkonto(app_user)
    quote, _ = _sent_quote(app_user)
    c = client_with_role("NUR_LESEN")
    r = c.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code == 403
    assert Communication.objects.count() == 0


@pytest.mark.django_db
def test_send_ohne_login_abgelehnt(anonymous_client, app_user):
    quote, _ = _sent_quote(app_user)
    r = anonymous_client.post(SEND_URL.format(quote.id), content_type="application/json")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_detail_liefert_recipient_email(admin_client, app_user):
    """Die Detailantwort belegt die Dialog-Vorbelegung mit der abgeleiteten EMAIL."""
    quote, _ = _sent_quote(app_user)
    r = admin_client.get(f"/api/invoicing/quotes/{quote.id}")
    assert r.status_code == 200
    assert r.json()["recipient_email"] == "angebot-kunde@example.test"


@pytest.mark.django_db
def test_detail_entwurf_ohne_recipient_email(admin_client, app_user):
    """Für Entwürfe wird keine Empfänger-EMAIL aufgelöst (null)."""
    quote, _ = _entwurf_quote(app_user)
    r = admin_client.get(f"/api/invoicing/quotes/{quote.id}")
    assert r.status_code == 200
    assert r.json()["recipient_email"] is None


@pytest.mark.django_db
def test_quote_pdf_endpoint_ab_versendet(admin_client, app_user):
    quote, _ = _sent_quote(app_user)
    r = admin_client.get(f"/api/invoicing/quotes/{quote.id}/pdf")
    assert r.status_code == 200
    assert r["Content-Type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


@pytest.mark.django_db
def test_quote_pdf_endpoint_entwurf_404(admin_client, app_user):
    quote, _ = _entwurf_quote(app_user)
    r = admin_client.get(f"/api/invoicing/quotes/{quote.id}/pdf")
    assert r.status_code == 404


@pytest.mark.django_db
def test_quote_pdf_endpoint_lesen_erlaubt(client_with_role, app_user):
    """NUR_LESEN hat invoicing/LESEN → darf das PDF abrufen."""
    quote, _ = _sent_quote(app_user)
    c = client_with_role("NUR_LESEN")
    r = c.get(f"/api/invoicing/quotes/{quote.id}/pdf")
    assert r.status_code == 200


@pytest.mark.django_db
def test_quote_pdf_endpoint_recht_gate_403(client_with_role, app_user):
    """DISPOSITION hat kein invoicing/LESEN → 403 am PDF-Endpunkt."""
    quote, _ = _sent_quote(app_user)
    c = client_with_role("DISPOSITION")
    r = c.get(f"/api/invoicing/quotes/{quote.id}/pdf")
    assert r.status_code == 403
