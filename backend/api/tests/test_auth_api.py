"""Tests der Auth-API (/api/auth/*): Login, Session, /me, Logout, CSRF.

Die vier Auth-Endpunkte tragen `auth=None` (sie müssen vor der Sitzung erreichbar
sein); geprüft werden hier die Login-Semantik (inkl. Nicht-Enumerierbarkeit und
Session-Fixation-Schutz) sowie die Rollen-/Rechteausgabe von /me.
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from .conftest import make_role_user

User = get_user_model()

WRONG = "E-Mail-Adresse oder Passwort ist falsch."


@pytest.mark.django_db
def test_login_korrekt_liefert_rollen_und_rechte():
    make_role_user(
        "ADMINISTRATION", email="chef@example.test", password="login-passwort-2026"
    )
    r = Client().post(
        "/api/auth/login",
        data={"email": "chef@example.test", "password": "login-passwort-2026"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["email"] == "chef@example.test"
    assert "ADMINISTRATION" in body["roles"]
    # ADMINISTRATION darf alles → die Rechteliste ist nicht leer.
    assert body["permissions"]
    assert all({"module", "action", "row_scope"} <= set(p) for p in body["permissions"])


@pytest.mark.django_db
def test_login_case_insensitiv():
    make_role_user(
        "NUR_LESEN", email="case.test@example.test", password="login-passwort-2026"
    )
    r = Client().post(
        "/api/auth/login",
        # Großschreibung: dieselbe Person (UniqueConstraint auf Lower('email')).
        data={"email": "CASE.TEST@EXAMPLE.TEST", "password": "login-passwort-2026"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["email"] == "case.test@example.test"


@pytest.mark.django_db
def test_falsches_passwort_und_unbekannte_email_gleiche_meldung():
    make_role_user(
        "NUR_LESEN", email="bekannt@example.test", password="login-passwort-2026"
    )
    falsch_pw = Client().post(
        "/api/auth/login",
        data={"email": "bekannt@example.test", "password": "falsch-falsch-2026"},
        content_type="application/json",
    )
    unbekannt = Client().post(
        "/api/auth/login",
        data={"email": "gibtsnicht@example.test", "password": "egal-egal-2026"},
        content_type="application/json",
    )
    assert falsch_pw.status_code == 401
    assert unbekannt.status_code == 401
    # Keine User-Enumeration: identische Fehlermeldung.
    assert falsch_pw.json()["detail"] == WRONG
    assert unbekannt.json()["detail"] == WRONG


@pytest.mark.django_db
def test_deaktiviertes_konto():
    """ModelBackend.user_can_authenticate lehnt inaktive Konten ab → authenticate
    liefert None. login_view kommt daher zum None-Zweig (401), nicht zum
    is_active-Zweig (403). Wir testen den tatsächlichen Wert: 401."""
    user, _ = make_role_user(
        "NUR_LESEN", email="inaktiv@example.test", password="login-passwort-2026"
    )
    user.is_active = False
    user.save()
    r = Client().post(
        "/api/auth/login",
        data={"email": "inaktiv@example.test", "password": "login-passwort-2026"},
        content_type="application/json",
    )
    assert r.status_code == 401
    assert r.json()["detail"] == WRONG


@pytest.mark.django_db
def test_me_anonym_401():
    r = Client().get("/api/auth/me")
    assert r.status_code == 401


@pytest.mark.django_db
def test_me_eingeloggt_liefert_rollen():
    user, _ = make_role_user(
        "DISPOSITION", email="dispo@example.test", password="login-passwort-2026"
    )
    client = Client()
    client.force_login(user)
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "dispo@example.test"
    assert body["roles"] == ["DISPOSITION"]


@pytest.mark.django_db
def test_logout_beendet_sitzung():
    user, _ = make_role_user(
        "NUR_LESEN", email="tschuess@example.test", password="login-passwort-2026"
    )
    client = Client()
    client.force_login(user)
    assert client.get("/api/auth/me").status_code == 200
    out = client.post("/api/auth/logout", content_type="application/json")
    assert out.status_code == 200
    # Nach dem Logout wieder anonym → 401.
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.django_db
def test_csrf_setzt_cookie():
    r = Client().get("/api/auth/csrf")
    assert r.status_code == 200
    assert "csrftoken" in r.cookies
    assert r.cookies["csrftoken"].value
    assert r.json()["csrftoken"]


@pytest.mark.django_db
def test_login_rotiert_session_id():
    """Session Fixation: eine vor dem Login bestehende Session-ID darf nach dem
    Login nicht dieselbe sein (Django ruft cycle_key)."""
    make_role_user(
        "NUR_LESEN", email="fixation@example.test", password="login-passwort-2026"
    )
    client = Client()
    # Eine anonyme Session erzeugen und ihren Schlüssel festhalten.
    session = client.session
    session["angelegt"] = True
    session.save()
    old_key = session.session_key
    client.cookies["sessionid"] = old_key

    r = client.post(
        "/api/auth/login",
        data={"email": "fixation@example.test", "password": "login-passwort-2026"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    new_key = client.session.session_key
    assert old_key is not None
    assert new_key is not None
    assert new_key != old_key


# --- CSRF-Schutz der auth=None-Endpunkte ----------------------------------
# Regression: django-ninja prüft CSRF nur bei Cookie-Auth, `auth=None`-Endpunkte
# sind befreit. Login und Logout holen die Prüfung selbst nach (Login-CSRF).


@pytest.mark.django_db
def test_login_ohne_csrf_token_wird_abgelehnt():
    make_role_user(
        "ADMINISTRATION", email="csrf.login@example.test", password="login-passwort-2026"
    )
    strict = Client(enforce_csrf_checks=True)
    r = strict.post(
        "/api/auth/login",
        data={"email": "csrf.login@example.test", "password": "login-passwort-2026"},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content
    assert "CSRF" in r.json()["detail"]


@pytest.mark.django_db
def test_login_mit_csrf_token_gelingt():
    make_role_user(
        "ADMINISTRATION", email="csrf.ok@example.test", password="login-passwort-2026"
    )
    strict = Client(enforce_csrf_checks=True)
    strict.get("/api/auth/csrf")  # setzt das csrftoken-Cookie
    token = strict.cookies["csrftoken"].value
    r = strict.post(
        "/api/auth/login",
        data={"email": "csrf.ok@example.test", "password": "login-passwort-2026"},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_logout_ohne_csrf_token_wird_abgelehnt():
    strict = Client(enforce_csrf_checks=True)
    r = strict.post("/api/auth/logout")
    assert r.status_code == 403, r.content


# --- Passwort ändern (POST /api/auth/password) -----------------------------

ALT = "altes-passwort-2026"
NEU = "ganz-neues-passwort-2027"


def _login_client(email, password):
    user, _ = make_role_user("NUR_LESEN", email=email, password=password)
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_passwort_wechsel_erfolgreich_und_session_bleibt_gueltig():
    client = _login_client("pw.ok@example.test", ALT)
    r = client.post(
        "/api/auth/password",
        data={"old_password": ALT, "new_password": NEU},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    # update_session_auth_hash: die laufende Sitzung bleibt gültig.
    assert client.get("/api/auth/me").status_code == 200


@pytest.mark.django_db
def test_passwort_wechsel_neues_passwort_wirkt_altes_nicht_mehr():
    _login_client("pw.login@example.test", ALT).post(
        "/api/auth/password",
        data={"old_password": ALT, "new_password": NEU},
        content_type="application/json",
    )
    # Frischer Login mit dem NEUEN Passwort gelingt …
    ok = Client().post(
        "/api/auth/login",
        data={"email": "pw.login@example.test", "password": NEU},
        content_type="application/json",
    )
    assert ok.status_code == 200, ok.content
    # … mit dem ALTEN Passwort nicht mehr.
    alt = Client().post(
        "/api/auth/login",
        data={"email": "pw.login@example.test", "password": ALT},
        content_type="application/json",
    )
    assert alt.status_code == 401


@pytest.mark.django_db
def test_passwort_wechsel_falsches_altes_passwort():
    client = _login_client("pw.falsch@example.test", ALT)
    r = client.post(
        "/api/auth/password",
        data={"old_password": "stimmt-nicht-2026", "new_password": NEU},
        content_type="application/json",
    )
    assert r.status_code == 400
    # Unspezifische Meldung; das neue Passwort wurde NICHT gesetzt.
    assert Client().post(
        "/api/auth/login",
        data={"email": "pw.falsch@example.test", "password": ALT},
        content_type="application/json",
    ).status_code == 200


@pytest.mark.django_db
def test_passwort_wechsel_zu_kurzes_neues_passwort_422():
    client = _login_client("pw.kurz@example.test", ALT)
    r = client.post(
        "/api/auth/password",
        data={"old_password": ALT, "new_password": "kurz1"},
        content_type="application/json",
    )
    assert r.status_code == 422
    # Deutsche Validator-Meldung (LANGUAGE_CODE=de-de, MinimumLengthValidator).
    assert "12 Zeichen" in r.json()["detail"]


@pytest.mark.django_db
def test_passwort_wechsel_anonym_401():
    r = Client().post(
        "/api/auth/password",
        data={"old_password": ALT, "new_password": NEU},
        content_type="application/json",
    )
    assert r.status_code == 401
