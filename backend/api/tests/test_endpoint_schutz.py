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
    Body → 422 vor `require*`); sie deckt der statische Scan ab. Die wenigen
    persönlichen Ressourcen ohne Modul-Recht (`NO_ROLE_GET_OK`) führen stattdessen
    den inhaltlichen Nachweis in `test_ohne_rolle_sieht_leeres_postfach`.
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
from urllib.parse import urlencode
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
    # Kunden-Freigabelink zum Angebot: Der Kunde hat kein MCN-Konto und wird
    # auch keins bekommen — er klickt einen Link aus einer E-Mail. Autorisierung
    # ist das Einmal-Token in der URL (in der DB nur als SHA-256-Hash,
    # security.public_link/0141); kein Modul-Recht, auth=None. Abgesichert
    # stattdessen durch: Drosselung je IP (dieselbe DB-Mechanik wie der Login),
    # CSRF am POST (_require_csrf), EINE einheitliche Antwort für unbekannt/
    # abgelaufen/widerrufen/verbraucht, eine Antwort als Positivliste ohne
    # EK/Marge, und einen Schreibweg über den regulären Service mit dem
    # Systemakteur. Nachgewiesen in api/tests/test_oeffentlicher_link_api.py.
    "/api/oeffentlich/angebot/{token}",
    "/api/oeffentlich/angebot/{token}/entscheidung",
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
    # Das persönliche Postfach (Migration 0137). Dieselbe Begründung wie beim
    # eigenen Passwort: Der Endpunkt wirkt AUSSCHLIESSLICH auf `request.user`;
    # es gibt keinen Parameter, mit dem sich ein fremdes Postfach adressieren
    # ließe (`benachrichtigung_service` filtert ausnahmslos auf den Akteur).
    #
    # Warum kein Modul-Recht: Das Postfach ist bereichsübergreifend. Hinge es an
    # `workflow/LESEN`, bekäme ein reines Buchhaltungskonto seine Freigabe- und
    # Rechnungsmeldungen nie zu sehen — ein Recht aus dem falschen Bereich als
    # Tor für alle anderen. Der Inhalt selbst ist getort, nur an anderer Stelle:
    # Eine Benachrichtigung darf nur enthalten, was ihr Empfänger am Ziel ohnehin
    # sehen darf (`docs/INVARIANTEN.md`, Abschnitt 5) — geprüft in
    # `test_aufgabe_dialog_api.py`.
    "/api/benachrichtigungen",
    "/api/benachrichtigungen/zaehler",
    "/api/benachrichtigungen/{notification_id}/gelesen",
    "/api/benachrichtigungen/alle-gelesen",
}

# Teilmenge von NO_REQUIRE_OK mit GET-Operationen: Sie können den
# 403-ohne-Rolle-Nachweis nicht führen, weil sie gar kein Modul-Recht prüfen.
# Bewusst als EIGENE, kleine Liste geführt statt NO_REQUIRE_OK pauschal auch
# hier auszuwerten — wer einen Endpunkt vom statischen Scan befreit, soll nicht
# nebenbei und unbemerkt auch den verhaltensbasierten Nachweis verlieren.
#
# Was an ihrer Stelle nachgewiesen wird: dass ein rollenloses Konto zwar 200
# bekommt, darin aber NICHTS steht, was ihm nicht gehört (siehe
# `test_ohne_rolle_sieht_leeres_postfach` unten).
NO_ROLE_GET_OK = {
    "/api/benachrichtigungen",
    "/api/benachrichtigungen/zaehler",
}

# Die „Teilmenge" oben ist eine Zusage — hier wird sie haltbar gemacht. Sonst
# könnte jemand einen Pfad nur hier eintragen und damit den verhaltensbasierten
# Nachweis abschalten, ohne dass der statische Scan ihn je gesehen hätte.
assert NO_ROLE_GET_OK <= NO_REQUIRE_OK, (
    "NO_ROLE_GET_OK muss Teilmenge von NO_REQUIRE_OK sein: "
    f"{NO_ROLE_GET_OK - NO_REQUIRE_OK}"
)

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


# Pflicht-Query-Parameter je Pfad.
#
# django-ninja validiert die Query VOR dem View — fehlt ein Pflichtparameter,
# antwortet der Endpunkt mit 422, bevor `require*` überhaupt läuft. Der
# 403-Nachweis liefe dann ins Leere, und zwar STILL: Der Test würde nicht etwa
# ein Loch melden, sondern gar nichts prüfen.
#
# Deshalb wird hier nachgereicht statt ausgenommen. Wer einen Endpunkt mit
# Pflicht-Query anlegt, sieht den Test rotwerden und trägt ihn hier ein — die
# Alternative (Pfad auf eine Ausnahmeliste) hätte denselben Endpunkt dauerhaft
# ungeprüft gelassen.
PFLICHT_QUERY = {
    "/api/management/properties/{property_id}/darf-beauftragen": {
        "party_id": str(uuid4()),
    },
}


def _concrete(path):
    """Pfadparameter ({uuid}) durch eine beliebige UUID ersetzen, damit die
    Django-URL-Auflösung greift (alle Parameter sind UUIDs)."""
    return re.sub(r"\{[^}]+\}", str(uuid4()), path)


def _query(path):
    """Query-String für Pfade mit Pflichtparametern, sonst leer."""
    p = PFLICHT_QUERY.get(path)
    return "?" + urlencode(p) if p else ""


def _op_id(val):
    path, method = val[0], val[1]
    return f"{method} {path}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path,method,_vf", PROTECTED, ids=[_op_id(v) for v in PROTECTED]
)
def test_anonymer_zugriff_401(path, method, _vf):
    """Jede Nicht-Whitelist-Operation lehnt anonyme Zugriffe mit 401 ab."""
    r = Client().generic(method, _concrete(path) + _query(path))
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
    if path in NO_ROLE_GET_OK:
        pytest.skip(f"{path}: persönliche Ressource ohne Modul-Recht.")
    user, _app_user = make_role_user(None)  # app_user vorhanden, keine Rolle
    client = Client()
    client.force_login(user)
    r = client.get(_concrete(path) + _query(path))
    assert r.status_code == 403, (
        f"GET {path} -> {r.status_code} (erwartet 403 ohne Rolle). "
        "Fehlt hier der require*-Aufruf?"
    )


@pytest.mark.django_db
def test_ohne_rolle_sieht_leeres_postfach():
    """Ersatznachweis für die Postfach-GETs (`NO_ROLE_GET_OK`).

    Sie antworten einem rollenlosen Konto mit 200 statt 403 — das ist richtig
    (jeder hat ein eigenes Postfach) und darf trotzdem nichts preisgeben. Der
    Beweis ist inhaltlich statt statuscodebasiert.

    Wichtig: Es liegt eine **fremde** Benachrichtigung in der Tabelle. Gegen eine
    leere Tabelle zu prüfen bewiese gar nichts — der Test bestünde auch dann,
    wenn der Service überhaupt nicht auf den Empfänger filterte.
    """
    from db_core.services import aufgabe as aufgabe_service

    besitzer, besitzer_app_user = make_role_user("ADMINISTRATION")
    empfaenger, _ = make_role_user("ADMINISTRATION")
    task = aufgabe_service.create_task(
        besitzer_app_user.id,
        title="Fremde Aufgabe",
        assigned_to_user_id=empfaenger.app_user_id,
    )
    aufgabe_service.complete_task(empfaenger.app_user_id, task.id)

    user, _app_user = make_role_user(None)
    client = Client()
    client.force_login(user)

    r = client.get("/api/benachrichtigungen")
    assert r.status_code == 200
    assert r.json()["items"] == [], "Fremde Benachrichtigung sichtbar!"
    assert r.json()["total"] == 0
    assert client.get("/api/benachrichtigungen/zaehler").json()["ungelesen"] == 0


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
