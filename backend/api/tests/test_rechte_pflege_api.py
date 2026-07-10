"""API-Tests der Rechtematrix-Pflege (api/security.py).

Geprüft wird das Zusammenspiel aus Rechte-Tor (api/permissions.require) und den
Härtungen der Service-Schicht, wie sie durch die HTTP-Schicht sichtbar werden:

  * **Gating je Endpunkt** (aus dem Modul-Docstring von api/security.py):
      GET  /security/{roles,permissions,users,user-roles}  → security/LESEN
      PUT  /security/permissions                           → security/AENDERN
      POST /security/user-roles[, /{id}/end]               → security/AENDERN
  * **401 ohne Login**, **403 mit unzureichender Rolle** (NUR_LESEN darf lesen,
    nicht ändern), **422** bei Selbst-Erweiterung / Selbstzuweisung / letzter
    ADMINISTRATION — die Service-ValueError schlägt als HttpError(422) durch,
    nicht als 500.

Nur ADMINISTRATION und GESCHAEFTSFUEHRUNG halten security/AENDERN. Weil beide
bereits ALLE Rechte tragen, ist die Selbst-Erweiterung über die HTTP-Schicht nur
mit einem MEHRROLLEN-Konto testbar (GESCHAEFTSFUEHRUNG für das Schreibrecht +
MONTEUR als die Rolle, deren Zelle ausgeweitet werden soll).
"""
import json
import uuid
from datetime import date, timedelta

import pytest
from django.test import Client

from db_core.models import UserRole

from .conftest import grant_role, make_app_user, make_role_user

ROLES_URL = "/api/security/roles"
PERM_URL = "/api/security/permissions"
USERS_URL = "/api/security/users"
USER_ROLES_URL = "/api/security/user-roles"


def _mehrrollen_client(*rollen):
    """Eingeloggter Client, dessen app_user MEHRERE Rollen trägt. Gibt
    (client, app_user) zurück."""
    user, app_user = make_role_user(rollen[0])
    for weitere in rollen[1:]:
        grant_role(app_user.id, weitere)
    client = Client()
    client.force_login(user)
    return client, app_user


def _login_mit_app_user(role_code):
    """Eingeloggter Client + zugehöriger app_user (für Selbstbezugs-Tests)."""
    user, app_user = make_role_user(role_code)
    client = Client()
    client.force_login(user)
    return client, app_user


# --- Lesen: Gating security/LESEN ------------------------------------------

@pytest.mark.django_db
def test_roles_lesen_nur_lesen_200(client_with_role):
    """NUR_LESEN hält security/LESEN (ALLE) → darf die Rollen sehen."""
    r = client_with_role("NUR_LESEN").get(ROLES_URL)
    assert r.status_code == 200, r.content
    codes = {row["code"] for row in r.json()}
    assert {"ADMINISTRATION", "MONTEUR", "NUR_LESEN"} <= codes


@pytest.mark.django_db
def test_permissions_matrix_enthaelt_accounting_zellen(client_with_role):
    """Die gelesene Matrix führt accounting-Zellen (Migration 0032)."""
    r = client_with_role("NUR_LESEN").get(PERM_URL)
    assert r.status_code == 200, r.content
    body = r.json()
    accounting = [c for c in body["cells"] if c["module"] == "accounting"]
    assert accounting, "Matrix enthält keine accounting-Zellen."
    # BUCHHALTUNG darf accounting lesen, MONTEUR nicht.
    by_key = {(c["role_code"], c["action"]): c for c in accounting}
    assert by_key[("BUCHHALTUNG", "LESEN")]["allowed"] is True
    assert by_key[("MONTEUR", "LESEN")]["allowed"] is False


@pytest.mark.django_db
def test_users_und_user_roles_lesen_nur_lesen_200(client_with_role):
    c = client_with_role("NUR_LESEN")
    assert c.get(USERS_URL).status_code == 200
    assert c.get(USER_ROLES_URL).status_code == 200


@pytest.mark.django_db
def test_lesen_ohne_login_401(anonymous_client):
    """Die gesamte API ist anmeldepflichtig → 401 statt Daten."""
    assert anonymous_client.get(ROLES_URL).status_code == 401
    assert anonymous_client.get(PERM_URL).status_code == 401
    assert anonymous_client.get(USER_ROLES_URL).status_code == 401


# --- Schreiben: Gating security/AENDERN ------------------------------------

@pytest.mark.django_db
def test_permissions_schreiben_nur_lesen_403(client_with_role):
    """NUR_LESEN darf lesen, aber NICHT schreiben → 403 am Recht-Tor."""
    payload = {"role_code": "MONTEUR", "module": "property", "action": "AENDERN",
               "allowed": True, "row_scope": "ALLE"}
    r = client_with_role("NUR_LESEN").put(
        PERM_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_permissions_schreiben_ohne_login_401(anonymous_client):
    payload = {"role_code": "MONTEUR", "module": "property", "action": "AENDERN",
               "allowed": True, "row_scope": "ALLE"}
    r = anonymous_client.put(
        PERM_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 401, r.content


@pytest.mark.django_db
def test_permissions_schreiben_admin_ok(admin_client):
    """ADMINISTRATION (trägt security/AENDERN, hält MONTEUR NICHT) darf eine fremde
    Rollenzelle setzen."""
    payload = {"role_code": "MONTEUR", "module": "property", "action": "AENDERN",
               "allowed": True, "row_scope": "ALLE"}
    r = admin_client.put(
        PERM_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["role_code"] == "MONTEUR" and body["allowed"] is True


@pytest.mark.django_db
def test_permissions_unbekanntes_modul_422_nicht_500(admin_client):
    """Ein unbekanntes Modul im Payload → 422 (ValueError → HttpError), kein 500."""
    payload = {"role_code": "MONTEUR", "module": "quatsch", "action": "LESEN",
               "allowed": True, "row_scope": "ALLE"}
    r = admin_client.put(
        PERM_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 422, r.content
    assert "Modul" in r.json()["detail"]


@pytest.mark.django_db
def test_selbst_erweiterung_ueber_api_422():
    """Selbst-Erweiterung über HTTP: ein Konto mit GESCHAEFTSFUEHRUNG (security/
    AENDERN) UND MONTEUR versucht, die eigene MONTEUR-Zelle workflow/LESEN von
    EIGENE auf ALLE zu heben → 422 (nicht durchgelassen)."""
    client, _ = _mehrrollen_client("GESCHAEFTSFUEHRUNG", "MONTEUR")
    payload = {"role_code": "MONTEUR", "module": "workflow", "action": "LESEN",
               "allowed": True, "row_scope": "ALLE"}
    r = client.put(
        PERM_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 422, r.content
    assert "erweitern" in r.json()["detail"]


@pytest.mark.django_db
def test_selbst_reduktion_ueber_api_erlaubt():
    """Grenze scharf: dasselbe Mehrrollen-Konto DARF die eigene MONTEUR-Zelle
    reduzieren (allowed=false) — nur Ausweiten ist gesperrt."""
    client, _ = _mehrrollen_client("GESCHAEFTSFUEHRUNG", "MONTEUR")
    payload = {"role_code": "MONTEUR", "module": "workflow", "action": "LESEN",
               "allowed": False, "row_scope": "EIGENE"}
    r = client.put(
        PERM_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 200, r.content
    assert r.json()["allowed"] is False


# --- Rollenzuordnung: assign / end -----------------------------------------

@pytest.mark.django_db
def test_assign_role_admin_201(admin_client):
    """ADMINISTRATION weist einem ANDEREN Benutzer eine Rolle zu → 201, aktiv."""
    ziel = make_app_user("Ziel-Benutzer")
    payload = {"user_id": str(ziel.id), "role_code": "MONTEUR"}
    r = admin_client.post(
        USER_ROLES_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["user_id"] == str(ziel.id)
    assert body["role_code"] == "MONTEUR"
    assert body["is_active"] is True


@pytest.mark.django_db
def test_assign_role_selbstzuweisung_422():
    """Selbstzuweisung über HTTP → 422. Der Admin versucht, sich SELBST eine
    weitere Rolle zu geben."""
    client, app_user = _login_mit_app_user("ADMINISTRATION")
    payload = {"user_id": str(app_user.id), "role_code": "MONTEUR"}
    r = client.post(
        USER_ROLES_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 422, r.content
    assert "selbst" in r.json()["detail"]


@pytest.mark.django_db
def test_assign_role_nur_lesen_403(client_with_role):
    """NUR_LESEN darf keine Rollen zuweisen → 403 (Recht-Tor vor dem Service)."""
    ziel = make_app_user("Ziel-Benutzer")
    payload = {"user_id": str(ziel.id), "role_code": "MONTEUR"}
    r = client_with_role("NUR_LESEN").post(
        USER_ROLES_URL, data=json.dumps(payload), content_type="application/json"
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_end_user_role_admin_ok(admin_client):
    """Eine (nicht-Admin-)Zuordnung beenden → 200, danach inaktiv."""
    ziel = make_app_user("Ziel-Benutzer")
    ur = grant_role(ziel.id, "MONTEUR", valid_from=date.today() - timedelta(days=10))
    r = admin_client.post(
        f"{USER_ROLES_URL}/{ur.id}/end",
        data=json.dumps({}), content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["valid_until"] == date.today().isoformat()
    assert body["is_active"] is False


@pytest.mark.django_db
def test_end_user_role_nur_lesen_403(client_with_role):
    """NUR_LESEN darf nicht beenden → 403 am Recht-Tor (vor dem Service)."""
    r = client_with_role("NUR_LESEN").post(
        f"{USER_ROLES_URL}/{uuid.uuid4()}/end",
        data=json.dumps({}), content_type="application/json",
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_letzte_admin_zuordnung_ueber_api_422():
    """Die letzte ADMINISTRATION-Zuordnung über HTTP zu beenden → 422. Das
    admin-Konto ist der einzige Admin und versucht, seine eigene Zuordnung zu
    beenden."""
    client, app_user = _login_mit_app_user("ADMINISTRATION")
    ur = UserRole.objects.get(user_id=app_user.id, role_id="ADMINISTRATION")
    r = client.post(
        f"{USER_ROLES_URL}/{ur.id}/end",
        data=json.dumps({}), content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "ADMINISTRATION" in r.json()["detail"]
