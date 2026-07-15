"""KI-Werkzeug-Registry (db_core.ai.registry) — Anlegen + Bearer at rest.

Beweist gegen echte DB: Werkzeug anlegen (eindeutig), Bearer Fernet-verschlüsselt
speichern (nie Klartext in der Spalte), zurückholen, löschen, und fail-closed ohne
Schlüssel.
"""
import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core.ai import registry
from db_core.models import Tool

KEY = Fernet.generate_key().decode()


def _reg(app_user, key):
    return registry.register_tool(
        app_user.id, tool_key=key, label="ASR Handy", capability="ASR",
        invocation_mode="ASYNC", endpoint_url="https://handy.local/asr",
    )


def test_register_tool_eindeutig(app_user):
    _reg(app_user, "asr-1")
    assert Tool.objects.filter(tool_key="asr-1").exists()
    with pytest.raises(ValueError):
        _reg(app_user, "asr-1")


@override_settings(MCN_CRED_KEY=KEY)
def test_bearer_roundtrip_durch_db(app_user):
    tool = _reg(app_user, "asr-2")
    status = registry.set_bearer(app_user.id, tool_id=tool.id, bearer="geheim-bearer")
    assert status["has_bearer"] is True
    tool.refresh_from_db()
    assert tool.bearer_encrypted is not None
    assert b"geheim-bearer" not in bytes(tool.bearer_encrypted)   # nie Klartext
    assert registry.get_bearer(tool.id) == "geheim-bearer"


@override_settings(MCN_CRED_KEY=KEY)
def test_bearer_loeschen(app_user):
    tool = _reg(app_user, "asr-3")
    registry.set_bearer(app_user.id, tool_id=tool.id, bearer="x")
    registry.set_bearer(app_user.id, tool_id=tool.id, bearer="")
    assert registry.get_bearer(tool.id) is None


def test_get_bearer_ohne_schluessel_fail_closed(app_user):
    with override_settings(MCN_CRED_KEY=KEY):
        tool = _reg(app_user, "asr-4")
        registry.set_bearer(app_user.id, tool_id=tool.id, bearer="x")
    with override_settings(MCN_CRED_KEY=""):
        with pytest.raises(ValueError):
            registry.get_bearer(tool.id)


def test_get_bearer_falscher_schluessel(app_user):
    with override_settings(MCN_CRED_KEY=KEY):
        tool = _reg(app_user, "asr-5")
        registry.set_bearer(app_user.id, tool_id=tool.id, bearer="x")
    anderer = Fernet.generate_key().decode()
    with override_settings(MCN_CRED_KEY=anderer):
        with pytest.raises(ValueError):        # Chiffre passt nicht zum Schlüssel
            registry.get_bearer(tool.id)


@override_settings(MCN_CRED_KEY="")
def test_set_bearer_ohne_schluessel_fail_closed(app_user):
    with override_settings(MCN_CRED_KEY=KEY):
        tool = _reg(app_user, "asr-6")
    with pytest.raises(ValueError):            # Verschlüsseln ohne Schlüssel
        registry.set_bearer(app_user.id, tool_id=tool.id, bearer="x")
