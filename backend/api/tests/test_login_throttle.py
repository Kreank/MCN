"""Brute-Force-/Rate-Limit-Schutz am Login (security.login_throttle).

Beweist gegen echte DB: Nach zu vielen Fehlversuchen wird der Login (Session UND
Gerät) mit 429 gedrosselt — konto+IP-gebunden (eine andere IP bleibt frei, ein
erfolgreicher Login räumt den Zähler ab), und pro IP über viele Konten
(Credential-Spraying). Plus der Prune-Lauf.
"""
import pytest
from django.test import Client, override_settings

from db_core.services import login_schutz
from .conftest import make_role_user

PW = "login-passwort-2026"
URL = "/api/auth/login"
DEVICE_URL = "/api/auth/device/login"


def _login(email, password, ip="10.0.0.1", url=URL, device_name=None):
    data = {"email": email, "password": password}
    if device_name is not None:
        data["device_name"] = device_name
    # Hinter dem Proxy ist X-Real-IP die vertrauenswürdige Quelle (nginx setzt sie
    # überschreibend). Die Tests, die IP-Differenzierung prüfen, aktivieren dafür
    # MCN_TRUST_PROXY_IP.
    return Client().post(
        url, data=data, content_type="application/json", HTTP_X_REAL_IP=ip,
    )


@pytest.mark.django_db
@override_settings(MCN_LOGIN_ACCT_THRESHOLD=3, MCN_LOGIN_IP_THRESHOLD=100, MCN_TRUST_PROXY_IP=True)
def test_sperrt_nach_zu_vielen_fehlversuchen():
    make_role_user("NUR_LESEN", email="opfer@example.test", password=PW)
    for _ in range(3):
        assert _login("opfer@example.test", "falsch-falsch-99").status_code == 401
    # Jetzt gesperrt — sogar das RICHTIGE Passwort kommt nicht mehr durch (429).
    r = _login("opfer@example.test", PW)
    assert r.status_code == 429
    assert "Anmeldeversuche" in r.json()["detail"]


@pytest.mark.django_db
@override_settings(MCN_LOGIN_ACCT_THRESHOLD=3, MCN_LOGIN_IP_THRESHOLD=100, MCN_TRUST_PROXY_IP=True)
def test_andere_ip_nicht_gesperrt():
    make_role_user("NUR_LESEN", email="opfer2@example.test", password=PW)
    for _ in range(3):
        _login("opfer2@example.test", "falsch-falsch-99", ip="1.1.1.1")
    # Sperre hängt an Konto+IP: von einer anderen IP kommt das Opfer normal rein
    # (kein konto-weiter Lockout → kein DoS gegen den echten Nutzer).
    r = _login("opfer2@example.test", PW, ip="2.2.2.2")
    assert r.status_code == 200


@pytest.mark.django_db
@override_settings(MCN_LOGIN_ACCT_THRESHOLD=3, MCN_LOGIN_IP_THRESHOLD=100, MCN_TRUST_PROXY_IP=True)
def test_erfolg_setzt_zaehler_zurueck():
    make_role_user("NUR_LESEN", email="wechsel@example.test", password=PW)
    _login("wechsel@example.test", "falsch-falsch-99")
    _login("wechsel@example.test", "falsch-falsch-99")   # 2 Fehlversuche
    assert _login("wechsel@example.test", PW).status_code == 200   # Erfolg → Reset
    # Nach dem Reset sind wieder volle 3 Versuche frei, also kein 429 beim 3.:
    _login("wechsel@example.test", "falsch-falsch-99")
    _login("wechsel@example.test", "falsch-falsch-99")
    assert _login("wechsel@example.test", PW).status_code == 200


@pytest.mark.django_db
@override_settings(MCN_LOGIN_ACCT_THRESHOLD=3, MCN_LOGIN_IP_THRESHOLD=100, MCN_TRUST_PROXY_IP=True)
def test_device_login_wird_ebenfalls_gedrosselt():
    make_role_user("NUR_LESEN", email="geraet@example.test", password=PW)
    for _ in range(3):
        r = _login("geraet@example.test", "falsch-falsch-99", url=DEVICE_URL,
                   device_name="Handy")
        assert r.status_code == 401
    r = _login("geraet@example.test", PW, url=DEVICE_URL, device_name="Handy")
    assert r.status_code == 429


@pytest.mark.django_db
@override_settings(MCN_LOGIN_ACCT_THRESHOLD=100, MCN_LOGIN_IP_THRESHOLD=4, MCN_TRUST_PROXY_IP=True)
def test_ip_schwelle_ueber_viele_konten():
    # Konto-Schwelle bewusst hoch → nur die IP-Schwelle greift (Spraying über viele
    # Konten von einer Quelle).
    for i in range(4):
        _login(f"spray{i}@example.test", "falsch-falsch-99", ip="7.7.7.7")
    # Ein weiteres (anderes) Konto von derselben IP ist jetzt gesperrt.
    r = _login("noch-eins@example.test", "falsch-falsch-99", ip="7.7.7.7")
    assert r.status_code == 429
    # Von einer anderen IP nicht.
    r2 = _login("noch-eins@example.test", "falsch-falsch-99", ip="8.8.8.8")
    assert r2.status_code == 401


@pytest.mark.django_db
@override_settings(MCN_LOGIN_ACCT_THRESHOLD=3, MCN_LOGIN_IP_THRESHOLD=100)
def test_ohne_proxy_vertrauen_wird_x_real_ip_ignoriert():
    # MCN_TRUST_PROXY_IP ist hier NICHT gesetzt (Default False): ein gefälschter
    # X-Real-IP darf den Schutz NICHT umgehen. Alle Requests fallen auf REMOTE_ADDR
    # zurück und teilen denselben Bucket — trotz wechselnder gefälschter IP wird
    # gesperrt.
    make_role_user("NUR_LESEN", email="spoof@example.test", password=PW)
    for i in range(3):
        assert (
            _login("spoof@example.test", "falsch-falsch-99", ip=f"9.9.9.{i}").status_code
            == 401
        )
    r = _login("spoof@example.test", PW, ip="9.9.9.250")
    assert r.status_code == 429


@pytest.mark.django_db
def test_prune_entfernt_alte_zeilen():
    login_schutz.registriere_fehlversuch("prune@example.test", "3.3.3.3")
    # older_than_seconds negativ ⇒ Schwelle liegt in der Zukunft ⇒ alle (nicht
    # gesperrten) Zeilen sind „alt genug" und werden entfernt.
    entfernt = login_schutz.prune(older_than_seconds=-1)
    assert entfernt >= 1
    assert login_schutz.gesperrt_bis("prune@example.test", "3.3.3.3") is None
