"""API-Tests des DATEV-Export-Endpoints (GET /api/buchhaltung/datev-export.csv).

Read-only-Export. Setup baut über die Services (wie der Service-Test) das
Firmenprofil mit DATEV-Konfiguration und eine veröffentlichte Rechnung. Geprüft
werden Auth (401 anonym), das 422 bei fehlender Konfiguration und der eigentliche
Download (Header + Inhalt).
"""
from datetime import date

import pytest
from django.test import Client

# Seeding-Helfer aus dem Service-Test wiederverwenden (bauen Profil + Rechnung).
from db_core.tests.test_datev_service import _config, _published
from .conftest import make_role_user

_URL = "/api/buchhaltung/datev-export.csv"
_HEUTE = date.today()


def _params():
    return {"von": _HEUTE.isoformat(), "bis": _HEUTE.isoformat()}


@pytest.mark.django_db
def test_anonym_401(anonymous_client):
    r = anonymous_client.get(_URL, _params())
    assert r.status_code == 401


@pytest.mark.django_db
def test_recht_vor_parametern_403(db):
    """Ein eingeloggter Nutzer OHNE Recht bekommt 403 — auch ohne von/bis. Beweist,
    dass require() vor der Parameter-Pflichtprüfung greift (sonst käme 422)."""
    user, _ = make_role_user(None)  # app_user, aber keine Rolle
    c = Client()
    c.force_login(user)
    r = c.get(_URL)  # bewusst ohne Parameter
    assert r.status_code == 403


@pytest.mark.django_db
def test_ohne_konfiguration_422(admin_client, app_user):
    r = admin_client.get(_URL, _params())
    assert r.status_code == 422


@pytest.mark.django_db
def test_download_liefert_extf(admin_client, app_user):
    _config(app_user)
    inv = _published(app_user, lines=[
        {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
         "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
    ])
    r = admin_client.get(_URL, _params())
    assert r.status_code == 200
    assert "windows-1252" in r["Content-Type"]
    assert r["Content-Disposition"].startswith("attachment;")
    assert ".csv" in r["Content-Disposition"]
    assert r["X-Content-Type-Options"] == "nosniff"
    text = r.content.decode("cp1252")
    assert text.startswith('"EXTF";700;21;"Buchungsstapel"')
    # Ein Buchungssatz mit Erlöskonto 8400 (SKR03, 19 %) und der Belegnummer.
    assert ";8400;" in text
    assert inv.invoice_number in text


@pytest.mark.django_db
def test_zeitraum_ueber_jahresgrenze_422(admin_client, app_user):
    _config(app_user)
    r = admin_client.get(_URL, {"von": "2025-12-01", "bis": "2026-01-31"})
    assert r.status_code == 422
