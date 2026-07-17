"""Tests der Geräte-Token-Auth (Bearer) für die native App.

Vertrag (MCN-APP/auth/AuthApi.kt):
  POST /api/auth/device/login  {email, password, device_name}
       → {token, display_name, app_user_id, roles}
  POST /api/auth/device/logout (Bearer) → widerruft das präsentierte Token

Geprüft: Login liefert Token + Profil; das Token authentisiert einen sonst
cookie-geschützten GESCHÄFTS-Endpunkt (der reale App-Fluss); falsches Passwort →
401 (unspezifisch); Logout und Widerruf entwerten das Token; das Klartext-Token
wird nirgends gespeichert (nur der SHA-256-Hash liegt in der Tabelle).

Hinweis: `/api/auth/me` ist bewusst NICHT der Nachweis-Endpunkt — er trägt
`auth=None` mit manueller Sitzungsprüfung (die globale Bearer-Auth läuft dort
nicht), ist also session-gebunden. Die App bezieht ihr Profil aus der
device/login-Antwort. Der Bearer-Weg wird deshalb an einem echten Geschäfts-GET
bewiesen (`/api/identity/parties`).
"""
import hashlib

import pytest
from django.test import Client

from db_core.models import DeviceToken
from db_core.services import geraetetoken

from .conftest import make_role_user

WRONG = "E-Mail-Adresse oder Passwort ist falsch."
PW = "geraete-passwort-2026"

# Ein rechtegeschützter Geschäfts-GET mit globaler Auth (Cookie ODER Bearer).
GESCHAEFTS_GET = "/api/identity/parties"


def _login(email, device_name="X"):
    """Meldet die App an und gibt das Klartext-Bearer-Token zurück."""
    return Client().post(
        "/api/auth/device/login",
        data={"email": email, "password": PW, "device_name": device_name},
        content_type="application/json",
    ).json()["token"]


def _bearer(client, token, method="get", path=GESCHAEFTS_GET, **extra):
    fn = getattr(client, method)
    return fn(path, HTTP_AUTHORIZATION=f"Bearer {token}", **extra)


# --- Login -----------------------------------------------------------------


@pytest.mark.django_db
def test_device_login_liefert_token_und_profil():
    make_role_user("DISPOSITION", email="app@example.test", password=PW)
    r = Client().post(
        "/api/auth/device/login",
        data={"email": "app@example.test", "password": PW, "device_name": "Pixel 8"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["token"]
    assert body["display_name"]
    assert body["roles"] == ["DISPOSITION"]
    assert body["app_user_id"]
    # Genau EIN Token angelegt, mit dem übergebenen Gerätenamen.
    assert DeviceToken.objects.count() == 1
    assert DeviceToken.objects.get().device_name == "Pixel 8"


@pytest.mark.django_db
def test_device_login_ohne_csrf_token_gelingt():
    """Anders als der Session-Login braucht der Geräte-Login KEIN CSRF (kein
    Cookie, keine Angriffsfläche) — auch der strikt prüfende Client kommt durch."""
    make_role_user("NUR_LESEN", email="nocsrf@example.test", password=PW)
    strict = Client(enforce_csrf_checks=True)
    r = strict.post(
        "/api/auth/device/login",
        data={"email": "nocsrf@example.test", "password": PW, "device_name": "S21"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_device_login_falsches_passwort_401_unspezifisch():
    make_role_user("NUR_LESEN", email="bekannt@example.test", password=PW)
    falsch = Client().post(
        "/api/auth/device/login",
        data={"email": "bekannt@example.test", "password": "falsch-falsch-2026",
              "device_name": "X"},
        content_type="application/json",
    )
    unbekannt = Client().post(
        "/api/auth/device/login",
        data={"email": "gibtsnicht@example.test", "password": "egal-egal-2026",
              "device_name": "X"},
        content_type="application/json",
    )
    assert falsch.status_code == 401
    assert unbekannt.status_code == 401
    assert falsch.json()["detail"] == WRONG
    assert unbekannt.json()["detail"] == WRONG
    # Kein Token bei Fehlschlag.
    assert DeviceToken.objects.count() == 0


@pytest.mark.django_db
def test_device_login_deaktiviertes_konto_401():
    user, _ = make_role_user("NUR_LESEN", email="inaktiv@example.test", password=PW)
    user.is_active = False
    user.save()
    r = Client().post(
        "/api/auth/device/login",
        data={"email": "inaktiv@example.test", "password": PW, "device_name": "X"},
        content_type="application/json",
    )
    assert r.status_code == 401
    assert r.json()["detail"] == WRONG


# --- Token authentisiert geschützte Endpunkte ------------------------------


@pytest.mark.django_db
def test_token_authentisiert_geschaeftsendpunkt():
    """Das Bearer-Token öffnet einen sonst cookie-geschützten Geschäfts-GET.
    Die Rechteprüfung liest request.user.app_user_id — von DeviceTokenAuth
    gesetzt. ADMINISTRATION darf alles."""
    make_role_user("ADMINISTRATION", email="biz@example.test", password=PW)
    token = _login("biz@example.test")
    # Ohne Token: anonym → 401.
    assert Client().get(GESCHAEFTS_GET).status_code == 401
    # Mit Token: 200.
    assert _bearer(Client(), token).status_code == 200


@pytest.mark.django_db
def test_me_bleibt_session_only():
    """/api/auth/me ist bewusst session-gebunden (auth=None + manuelle
    Sitzungsprüfung; die globale Bearer-Auth läuft dort NICHT). Die App holt ihr
    Profil aus der device/login-Antwort, nicht über /me. Ein gültiges Bearer-
    Token liefert an /me daher 401 — dokumentierte Grenze, kein Regressionsleck."""
    make_role_user("NUR_LESEN", email="meso@example.test", password=PW)
    token = _login("meso@example.test")
    assert _bearer(Client(), token, path="/api/auth/me").status_code == 401


@pytest.mark.django_db
def test_falsches_token_401():
    """Erfundenes Bearer-Token an einem bearer-authentifizierten Geschäfts-GET
    → 401 (DeviceTokenAuth löst nichts auf, django_auth greift ohne Cookie auch
    nicht)."""
    make_role_user("ADMINISTRATION", email="ft@example.test", password=PW)
    assert _bearer(Client(), "voellig-erfundenes-token").status_code == 401


# --- Logout / Widerruf -----------------------------------------------------


@pytest.mark.django_db
def test_logout_entwertet_token():
    make_role_user("ADMINISTRATION", email="lo@example.test", password=PW)
    token = _login("lo@example.test")

    assert _bearer(Client(), token).status_code == 200  # gültig
    out = _bearer(Client(), token, method="post", path="/api/auth/device/logout")
    assert out.status_code == 200
    # Nach dem Logout ist das Token entwertet → 401.
    assert _bearer(Client(), token).status_code == 401


@pytest.mark.django_db
def test_logout_ist_idempotent():
    """Ein zweiter Logout mit demselben (bereits widerrufenen) Token → sauberes
    Verhalten: Das Token ist ungültig, ein Bearer-Aufruf gibt 401 (kein 500)."""
    make_role_user("NUR_LESEN", email="idem@example.test", password=PW)
    token = _login("idem@example.test")
    assert _bearer(Client(), token, method="post",
                   path="/api/auth/device/logout").status_code == 200
    # Zweiter Logout: Token schon ungültig → Bearer-Auth scheitert (401).
    assert _bearer(Client(), token, method="post",
                   path="/api/auth/device/logout").status_code == 401


@pytest.mark.django_db
def test_logout_ohne_token_401():
    assert Client().post("/api/auth/device/logout").status_code == 401


# --- Nur der Hash liegt in der DB ------------------------------------------


@pytest.mark.django_db
def test_klartext_token_wird_nicht_gespeichert():
    make_role_user("NUR_LESEN", email="hash@example.test", password=PW)
    token = _login("hash@example.test")

    row = DeviceToken.objects.get()
    # Der gespeicherte Wert ist NICHT das Klartext-Token, sondern dessen SHA-256.
    assert row.token_hash != token
    assert row.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
    # Kein Feld der Zeile enthält das Klartext-Token.
    assert token not in (row.device_name or "")


# --- Service-Ebene ----------------------------------------------------------


@pytest.mark.django_db
def test_service_aufloesen_und_widerrufen():
    user, _ = make_role_user("NUR_LESEN", email="svc@example.test", password=PW)
    klartext = geraetetoken.token_ausstellen(user, "Werkstatt-Tablet")
    aufgeloest = geraetetoken.token_aufloesen(klartext)
    assert aufgeloest is not None
    assert aufgeloest.user_id == user.pk
    assert aufgeloest.app_user_id == user.app_user_id

    geraetetoken.token_widerrufen(aufgeloest)
    # Widerrufenes Token wird nicht mehr aufgelöst.
    assert geraetetoken.token_aufloesen(klartext) is None
    # Idempotent: erneuter Widerruf ändert nichts und wirft nicht.
    geraetetoken.token_widerrufen(aufgeloest)


@pytest.mark.django_db
def test_service_konto_ohne_app_user_kann_token_ausstellen():
    """Bootstrapping-Pfad: ein Login-Konto ohne app_user_id kann ein Token
    ausstellen und wieder widerrufen (einfacher atomarer Insert/Update, kein
    business_transaction)."""
    user, _ = make_role_user(None, with_app_user=False,
                             email="ohne@example.test", password=PW)
    assert user.app_user_id is None
    klartext = geraetetoken.token_ausstellen(user, "Kiosk")
    row = geraetetoken.token_aufloesen(klartext)
    assert row is not None
    assert row.app_user_id is None
    geraetetoken.token_widerrufen(row)
    assert geraetetoken.token_aufloesen(klartext) is None
