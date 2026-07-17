"""Flächendeckender Schutz-Nachweis für JEDEN registrierten API-Endpunkt.

Zwei Zusicherungen, die verhindern, dass ein künftiger Endpunkt versehentlich
offen registriert wird (`auth=None` oder vergessenes `require*`):

  * `test_anonymer_zugriff_401` — ein anonymer Client bekommt auf jeder Operation
    (außer der Whitelist) 401. Fällt jemand mit `auth=None` durch, liefert der
    Endpunkt statt 401 etwas anderes und der Test schlägt fehl.
  * `test_ohne_rolle_403_bei_get` — ein eingeloggtes Konto OHNE jede Rolle bekommt
    auf jeder GET-Operation 403 (das Recht fehlt). Das ist der verhaltensbasierte
    Beweis, dass der Endpunkt tatsächlich ein Recht prüft — und dass 403 vor 404
    kommt (Pfadparameter mit beliebiger UUID). POST-Operationen sind hier
    ausgenommen, weil django-ninja den Request-Body VOR dem View validiert (leerer
    Body → 422 vor `require*`); sie deckt der statische Scan ab.
  * `test_jeder_endpunkt_ruft_require` — statischer Quelltext-Scan: die
    View-Funktion jeder Nicht-Whitelist-Operation enthält einen `require*`-Aufruf.
    Deckt auch POST/PUT ab, wo der behavioral-Test nicht greift.

Die Aufzählung geht über die django-ninja-Registry (`api._routers` →
`router.path_operations` → `PathView.operations`); neue Router/Endpunkte werden
automatisch mitgeprüft, ohne dass dieser Test gepflegt werden muss.
"""
import inspect
import re
from functools import lru_cache
from uuid import uuid4

import pytest
from django.test import Client

from api.api import api

from .conftest import make_role_user

# Bewusst offen (auth=None) bzw. selbst 401 liefernd — vor der Sitzung erreichbar.
WHITELIST = {
    "/api/health",
    "/api/auth/csrf",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    # Passwort vergessen: beide vor der Sitzung erreichbar (auth=None), mit
    # eigener CSRF-Prüfung. /request antwortet bewusst immer 200 (Anti-
    # Enumeration), /confirm setzt anhand eines Einmal-Tokens ein neues Passwort.
    "/api/auth/password-reset/request",
    "/api/auth/password-reset/confirm",
    # Geräte-Login der nativen App (auth=None, kein CSRF): muss vor jeder Sitzung
    # erreichbar sein. Gibt bei Erfolg das Bearer-Token zurück; falsche
    # Zugangsdaten → 401 (unspezifisch, keine Enumeration).
    "/api/auth/device/login",
    # IDS-Connect Warenkorb-Rückgabe: der Händler-Shop POSTet hierher (aus dem
    # Browser des Handwerkers, ohne MCN-Sitzung). Autorisierung ist das Einmal-
    # Token in der URL (in der DB nur als Hash); kein Modul-Recht, auth=None.
    "/api/pricing/warenkorb-return/{token}",
}

# Authentifiziert (auth=django_auth), aber bewusst OHNE Modul-Recht: jeder darf
# sein EIGENES Passwort ändern. Der Endpunkt ruft daher kein `require*` auf und
# ist nur vom statischen require-Scan ausgenommen — NICHT von der 401-Prüfung:
# er bleibt anmeldepflichtig (steht deshalb NICHT in WHITELIST, sondern hier).
# Sicherheit liegt nicht in der Rechtematrix, sondern darin, dass der Endpunkt
# ausschließlich auf request.user wirkt und kein Zielkonto entgegennimmt.
NO_REQUIRE_OK = {
    "/api/auth/password",
    # Geräte-Logout der nativen App: Bearer-authentifiziert (anonym → 401, daher
    # NICHT in WHITELIST), aber bewusst OHNE Modul-Recht — er widerruft nur das
    # präsentierte Token, kein Zielkonto (analog /api/auth/password).
    "/api/auth/device/logout",
}

_REQUIRE_CALL = re.compile(r"\brequire(_scoped|_create)?\s*\(")


def _iter_ops():
    """(full_path, method, view_func) für jede registrierte Operation."""
    for prefix, router in api._routers:
        for path, path_view in router.path_operations.items():
            for op in path_view.operations:
                full = "/api" + prefix + path
                for method in op.methods:
                    yield full, method, op.view_func


ALL_OPS = list(_iter_ops())
# Absicherung, dass die Registry-Aufzählung überhaupt etwas findet.
assert len(ALL_OPS) > 30, f"Nur {len(ALL_OPS)} Operationen gefunden — Registry kaputt?"

PROTECTED = [(p, m, vf) for (p, m, vf) in ALL_OPS if p not in WHITELIST]
PROTECTED_GET = [(p, vf) for (p, m, vf) in PROTECTED if m == "GET"]


def _concrete(path):
    """Pfadparameter ({uuid}) durch eine beliebige UUID ersetzen, damit die
    Django-URL-Auflösung greift (alle Parameter sind UUIDs)."""
    return re.sub(r"\{[^}]+\}", str(uuid4()), path)


def _op_id(val):
    path, method = val[0], val[1]
    return f"{method} {path}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path,method,_vf", PROTECTED, ids=[_op_id(v) for v in PROTECTED]
)
def test_anonymer_zugriff_401(path, method, _vf):
    """Jede Nicht-Whitelist-Operation lehnt anonyme Zugriffe mit 401 ab."""
    r = Client().generic(method, _concrete(path))
    assert r.status_code == 401, (
        f"{method} {path} -> {r.status_code} (erwartet 401 für anonym). "
        "Wurde hier versehentlich auth=None gesetzt?"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path,_vf", PROTECTED_GET, ids=[f"GET {p}" for (p, _v) in PROTECTED_GET]
)
def test_ohne_rolle_403_bei_get(path, _vf):
    """Eingeloggt, aber ohne jede Rolle → 403 auf jeder GET-Operation (das Recht
    fehlt). Beweist die tatsächliche Rechteprüfung; 403 kommt vor 404."""
    user, _app_user = make_role_user(None)  # app_user vorhanden, keine Rolle
    client = Client()
    client.force_login(user)
    r = client.get(_concrete(path))
    assert r.status_code == 403, (
        f"GET {path} -> {r.status_code} (erwartet 403 ohne Rolle). "
        "Fehlt hier der require*-Aufruf?"
    )


@lru_cache(maxsize=None)
def _guard_helpers(module):
    """Namen der Modul-Funktionen, deren Quelltext selbst einen require*-Aufruf
    enthält (z. B. der Helfer `_absence_action`). Ein View gilt auch dann als
    geschützt, wenn er statt direkt zu prüfen an einen solchen Helfer delegiert."""
    names = set()
    for name, fn in inspect.getmembers(module, inspect.isfunction):
        try:
            if _REQUIRE_CALL.search(inspect.getsource(fn)):
                names.add(name)
        except (OSError, TypeError):
            continue
    return frozenset(names)


def _is_guarded(vf):
    src = inspect.getsource(vf)
    if _REQUIRE_CALL.search(src):
        return True
    module = inspect.getmodule(vf)
    for name in _guard_helpers(module):
        if name == vf.__name__:
            continue  # der eigene def-Kopf zählt nicht als Aufruf
        if re.search(r"\b" + re.escape(name) + r"\s*\(", src):
            return True
    return False


@pytest.mark.parametrize(
    "path,method,vf", PROTECTED, ids=[_op_id(v) for v in PROTECTED]
)
def test_jeder_endpunkt_ruft_require(path, method, vf):
    """Statischer Nachweis: die View-Funktion prüft ein Recht — direkt über einen
    require*-Aufruf oder durch Delegation an einen prüfenden Modul-Helfer.

    Fängt POST/PUT-Endpunkte, die der behavioral-Test (nur GET) nicht abdeckt.

    Ausnahme: NO_REQUIRE_OK-Endpunkte prüfen bewusst KEIN Modul-Recht (z. B.
    „eigenes Passwort ändern") und sind vom Scan befreit — sie bleiben aber über
    `test_anonymer_zugriff_401` anmeldepflichtig abgesichert."""
    if path in NO_REQUIRE_OK:
        pytest.skip(f"{path}: bewusst ohne Modul-Recht (Self-Service).")
    assert _is_guarded(vf), (
        f"{method} {path}: View {vf.__name__} prüft kein Recht (kein require*/"
        "require_scoped/require_create, auch nicht via Helfer) — ungeschützt."
    )
