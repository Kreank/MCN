"""Tests des Passwort-Reset-Flows (/api/auth/password-reset/{request,confirm}).

Sicherheitsschwerpunkte:
  * Anti-Enumeration: /request antwortet für bekannte UND unbekannte Adressen
    identisch (Status + Body). Der Versand läuft im Hintergrund (`_run_in_background`)
    — in den Tests synchron gepatcht, damit die Zusicherungen deterministisch sind.
  * Der Reset-Link (Token + uid) landet NIE in content.communication (die Mail
    geht bewusst NICHT über send_mail, das den Body protokollieren würde).
  * Der Token ist zustandslos (default_token_generator) und wird durch die
    Passwortänderung single-use.
  * Einheitliche 400-Meldung für ungültige/abgelaufene Token, unbekannte uid und
    deaktivierte Konten; 422 nur für ein zu schwaches Passwort.
  * Beide Endpunkte sind `auth=None`, verlangen aber CSRF (wie login/logout).
"""
import uuid
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import Client, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

import api.auth as auth_module
from db_core.models import Communication, MailAccount
from db_core.services import mail as mail_service

from .conftest import make_role_user

User = get_user_model()

REQUEST_URL = "/api/auth/password-reset/request"
CONFIRM_URL = "/api/auth/password-reset/confirm"
NEW_PW = "frisches-passwort-2027"
OLD_PW = "altes-passwort-2026"
REQUEST_DETAIL = "Falls ein Konto existiert, wurde eine E-Mail gesendet."
INVALID_LINK = "Der Link ist ungültig oder abgelaufen."


def _post(client, url, data, token=None):
    kwargs = {"content_type": "application/json"}
    if token is not None:
        kwargs["HTTP_X_CSRFTOKEN"] = token
    return client.post(url, data=data, **kwargs)


def _link_teile(user):
    """uid + gültiger Token für einen Nutzer (wie der Server sie erzeugt)."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uid, token


@pytest.fixture
def sync_dispatch(monkeypatch):
    """Zustellung synchron statt im Thread — deterministisch für Tests."""
    monkeypatch.setattr(auth_module, "_run_in_background",
                        lambda target, *args: target(*args))


# --- /request: Anti-Enumeration --------------------------------------------


@pytest.mark.django_db
def test_request_identisch_fuer_bekannt_und_unbekannt(monkeypatch):
    # Versand komplett unterdrücken — hier zählt nur die Antwort-Gleichheit.
    monkeypatch.setattr(auth_module, "_run_in_background", lambda *a, **k: None)
    make_role_user("NUR_LESEN", email="bekannt@example.test")

    r_bekannt = _post(Client(), REQUEST_URL, {"email": "bekannt@example.test"})
    r_unbekannt = _post(Client(), REQUEST_URL, {"email": "niemand@example.test"})

    assert r_bekannt.status_code == 200
    assert r_unbekannt.status_code == 200
    # Identischer Body → verrät nicht, ob die Adresse existiert.
    assert r_bekannt.json() == r_unbekannt.json()
    assert r_bekannt.json()["detail"] == REQUEST_DETAIL


@pytest.mark.django_db
def test_request_bekannt_ruft_versand_mit_gueltigem_link(monkeypatch, sync_dispatch):
    spy = Mock()
    monkeypatch.setattr(auth_module, "_send_reset_email", spy)
    make_role_user("NUR_LESEN", email="reset@example.test")

    r = _post(Client(), REQUEST_URL, {"email": "reset@example.test"})
    assert r.status_code == 200

    spy.assert_called_once()
    to_address, link = spy.call_args.args
    assert to_address == "reset@example.test"

    q = parse_qs(urlparse(link).query)
    uid, token = q["uid"][0], q["token"][0]
    # Token + uid gehören zusammen und sind gültig → confirm gelingt.
    r2 = _post(Client(), CONFIRM_URL,
               {"uid": uid, "token": token, "new_password": NEW_PW})
    assert r2.status_code == 200, r2.content


@pytest.mark.django_db
def test_request_unbekannt_versendet_nichts(monkeypatch, sync_dispatch):
    spy = Mock()
    monkeypatch.setattr(auth_module, "_send_reset_email", spy)

    r = _post(Client(), REQUEST_URL, {"email": "niemand@example.test"})
    assert r.status_code == 200
    spy.assert_not_called()


@pytest.mark.django_db
def test_request_inaktives_konto_versendet_nichts(monkeypatch, sync_dispatch):
    spy = Mock()
    monkeypatch.setattr(auth_module, "_send_reset_email", spy)
    user, _ = make_role_user("NUR_LESEN", email="inaktiv@example.test")
    user.is_active = False
    user.save()

    r = _post(Client(), REQUEST_URL, {"email": "inaktiv@example.test"})
    assert r.status_code == 200  # weiterhin identische Antwort
    spy.assert_not_called()


@pytest.mark.django_db
def test_request_case_insensitiv(monkeypatch, sync_dispatch):
    spy = Mock()
    monkeypatch.setattr(auth_module, "_send_reset_email", spy)
    make_role_user("NUR_LESEN", email="gross@example.test")

    r = _post(Client(), REQUEST_URL, {"email": "GROSS@EXAMPLE.TEST"})
    assert r.status_code == 200
    spy.assert_called_once()


# --- /request: kein Token in content.communication -------------------------


@pytest.mark.django_db
def test_kein_token_in_communication(sync_dispatch):
    """Der Reset-Fluss schreibt NIE eine content.communication-Zeile (kein
    Token-Leak). Läuft real (ohne Mailkonto → schweigend nichts versandt)."""
    make_role_user("NUR_LESEN", email="log@example.test")
    before = Communication.objects.count()

    r = _post(Client(), REQUEST_URL, {"email": "log@example.test"})
    assert r.status_code == 200
    assert Communication.objects.count() == before


# --- /request: kein Mailkonto konfiguriert (Req 6) -------------------------


@pytest.mark.django_db
def test_ohne_mailkonto_trotzdem_200(sync_dispatch):
    """Ohne konfiguriertes Absenderkonto liefert /request trotzdem 200 (Anti-
    Enumeration), sendet aber nichts und wirft kein 500."""
    make_role_user("NUR_LESEN", email="nomail@example.test")
    assert MailAccount.objects.filter(active=True).first() is None

    r = _post(Client(), REQUEST_URL, {"email": "nomail@example.test"})
    assert r.status_code == 200
    assert r.json()["detail"] == REQUEST_DETAIL


@pytest.mark.django_db
def test_mit_mailkonto_versendet_ueber_smtp(monkeypatch, sync_dispatch):
    """Mit konfiguriertem Konto wird die echte Zustellung (get_connection →
    send_messages) durchlaufen — SMTP hier gemockt, keine echte Verbindung."""
    user, app_user = make_role_user("ADMINISTRATION", email="smtp@example.test")
    # Konto ohne Passwort (offenes Relay) → keine Fernet-Entschlüsselung nötig.
    mail_service.set_mail_account(
        app_user.id, label="Test", host="smtp.test", port=587,
        security="STARTTLS", from_address="mcn@example.test", from_name="MCN",
    )
    fake_conn = Mock()
    monkeypatch.setattr(auth_module, "get_connection", lambda **kw: fake_conn)

    r = _post(Client(), REQUEST_URL, {"email": "smtp@example.test"})
    assert r.status_code == 200
    # Die Mail wurde über die gebaute Verbindung verschickt …
    assert fake_conn.send_messages.called
    sent = fake_conn.send_messages.call_args.args[0][0]
    # … mit dem Reset-Link im Body, aber NICHT in content.communication.
    assert "/passwort-zuruecksetzen?uid=" in sent.body
    assert "token=" in sent.body
    assert Communication.objects.count() == 0


# --- /confirm: Erfolg + single-use -----------------------------------------


@pytest.mark.django_db
def test_confirm_setzt_passwort_login_neu_ja_alt_nein():
    user, _ = make_role_user("NUR_LESEN", email="c@example.test", password=OLD_PW)
    uid, token = _link_teile(user)

    r = _post(Client(), CONFIRM_URL,
              {"uid": uid, "token": token, "new_password": NEW_PW})
    assert r.status_code == 200, r.content

    # Login mit dem NEUEN Passwort gelingt …
    assert _post(Client(), "/api/auth/login",
                 {"email": "c@example.test", "password": NEW_PW}).status_code == 200
    # … mit dem ALTEN nicht mehr.
    assert _post(Client(), "/api/auth/login",
                 {"email": "c@example.test", "password": OLD_PW}).status_code == 401


@pytest.mark.django_db
def test_confirm_token_single_use():
    """Nach erfolgreicher Änderung ist derselbe Token entwertet (der Hash bindet
    das Passwort) → erneuter Aufruf scheitert mit der einheitlichen Meldung."""
    user, _ = make_role_user("NUR_LESEN", email="once@example.test", password=OLD_PW)
    uid, token = _link_teile(user)

    assert _post(Client(), CONFIRM_URL,
                 {"uid": uid, "token": token, "new_password": NEW_PW}).status_code == 200
    r2 = _post(Client(), CONFIRM_URL,
               {"uid": uid, "token": token, "new_password": "noch-anderes-2028"})
    assert r2.status_code == 400
    assert r2.json()["detail"] == INVALID_LINK


# --- /confirm: einheitliche Fehler (kein Leak) -----------------------------


@pytest.mark.django_db
def test_confirm_manipulierter_token_400():
    user, _ = make_role_user("NUR_LESEN", email="tamper@example.test")
    uid, token = _link_teile(user)
    r = _post(Client(), CONFIRM_URL,
              {"uid": uid, "token": token + "x", "new_password": NEW_PW})
    assert r.status_code == 400
    assert r.json()["detail"] == INVALID_LINK


@pytest.mark.django_db
def test_confirm_unbekannte_uid_400():
    # uid eines nicht existierenden Kontos (hohe pk), beliebiger Token.
    uid = urlsafe_base64_encode(force_bytes(999_999_999))
    r = _post(Client(), CONFIRM_URL,
              {"uid": uid, "token": "irgendwas-abc", "new_password": NEW_PW})
    assert r.status_code == 400
    assert r.json()["detail"] == INVALID_LINK


@pytest.mark.django_db
def test_confirm_kaputte_uid_400():
    r = _post(Client(), CONFIRM_URL,
              {"uid": "@@nicht-base64@@", "token": "x", "new_password": NEW_PW})
    assert r.status_code == 400
    assert r.json()["detail"] == INVALID_LINK


@pytest.mark.django_db
def test_confirm_inaktives_konto_400():
    user, _ = make_role_user("NUR_LESEN", email="ci@example.test")
    uid, token = _link_teile(user)
    user.is_active = False
    user.save()
    r = _post(Client(), CONFIRM_URL,
              {"uid": uid, "token": token, "new_password": NEW_PW})
    assert r.status_code == 400
    assert r.json()["detail"] == INVALID_LINK


@pytest.mark.django_db
@override_settings(PASSWORD_RESET_TIMEOUT=-1)
def test_confirm_abgelaufener_token_400():
    """Mit negativem Timeout gilt jeder Token als abgelaufen → einheitliches 400."""
    user, _ = make_role_user("NUR_LESEN", email="alt@example.test")
    uid, token = _link_teile(user)
    r = _post(Client(), CONFIRM_URL,
              {"uid": uid, "token": token, "new_password": NEW_PW})
    assert r.status_code == 400
    assert r.json()["detail"] == INVALID_LINK


@pytest.mark.django_db
def test_confirm_schwaches_passwort_422():
    user, _ = make_role_user("NUR_LESEN", email="schwach@example.test", password=OLD_PW)
    uid, token = _link_teile(user)
    r = _post(Client(), CONFIRM_URL,
              {"uid": uid, "token": token, "new_password": "kurz1"})
    assert r.status_code == 422
    # Deutsche Validator-Meldung; NIE das Passwort selbst im Fehlertext.
    assert "12 Zeichen" in r.json()["detail"]
    assert "kurz1" not in r.json()["detail"]
    # Das alte Passwort gilt weiterhin (nichts wurde gesetzt).
    assert _post(Client(), "/api/auth/login",
                 {"email": "schwach@example.test", "password": OLD_PW}).status_code == 200


# --- CSRF-Pflicht beider Endpunkte -----------------------------------------


@pytest.mark.django_db
def test_request_ohne_csrf_403():
    strict = Client(enforce_csrf_checks=True)
    r = strict.post(REQUEST_URL, data={"email": "x@example.test"},
                    content_type="application/json")
    assert r.status_code == 403


@pytest.mark.django_db
def test_confirm_ohne_csrf_403():
    strict = Client(enforce_csrf_checks=True)
    r = strict.post(CONFIRM_URL,
                    data={"uid": "a", "token": "b", "new_password": NEW_PW},
                    content_type="application/json")
    assert r.status_code == 403


@pytest.mark.django_db
def test_request_mit_csrf_gelingt(monkeypatch):
    monkeypatch.setattr(auth_module, "_run_in_background", lambda *a, **k: None)
    strict = Client(enforce_csrf_checks=True)
    strict.get("/api/auth/csrf")
    token = strict.cookies["csrftoken"].value
    r = _post(strict, REQUEST_URL, {"email": "x@example.test"}, token=token)
    assert r.status_code == 200
