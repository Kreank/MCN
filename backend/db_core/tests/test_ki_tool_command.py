"""Management-Command `ki_tool` — Werkzeuge ohne Python-Shell verwalten.

Prüft die Betreiber-Wege: registrieren (inkl. Endpoint-Pflicht, Bearer aus Env),
Bearer setzen/löschen, stilllegen/aktivieren, auflisten OHNE das Secret.
"""
import io

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from db_core.models import Tool

# Ein Test-Schlüssel für die Bearer-at-rest-Verschlüsselung (cred_crypto liest
# settings.MCN_CRED_KEY — wie in test_ai_registry).
KEY = Fernet.generate_key().decode()


def _run(*args, **kwargs):
    out = io.StringIO()
    call_command("ki_tool", *args, stdout=out, stderr=io.StringIO(), **kwargs)
    return out.getvalue()


def test_register_setzt_werkzeug(app_user):
    _run("register", "--key", "asr-x", "--label", "ASR", "--capability", "ASR",
         "--mode", "ASYNC", "--endpoint", "https://handy.local/asr",
         "--actor", str(app_user.id))
    t = Tool.objects.get(tool_key="asr-x")
    assert t.capability == "ASR" and t.invocation_mode == "ASYNC"
    assert t.status == "ACTIVE" and t.bearer_encrypted is None


def test_register_extern_ohne_endpoint_fehler(app_user):
    with pytest.raises(CommandError):
        _run("register", "--key", "asr-y", "--label", "ASR", "--capability", "ASR",
             "--mode", "ASYNC", "--actor", str(app_user.id))
    assert not Tool.objects.filter(tool_key="asr-y").exists()


@override_settings(MCN_CRED_KEY=KEY)
def test_register_mit_bearer_aus_env(app_user, monkeypatch):
    monkeypatch.setenv("MCN_TEST_ASR_BEARER", "geheim-123")
    _run("register", "--key", "asr-z", "--label", "ASR", "--capability", "ASR",
         "--mode", "ASYNC", "--endpoint", "https://h.local/asr",
         "--bearer-env", "MCN_TEST_ASR_BEARER", "--actor", str(app_user.id))
    t = Tool.objects.get(tool_key="asr-z")
    assert t.bearer_encrypted is not None            # Fernet-Chiffre, kein Klartext
    assert b"geheim-123" not in bytes(t.bearer_encrypted)


@override_settings(MCN_CRED_KEY=KEY)
def test_list_zeigt_kein_secret(app_user, monkeypatch):
    monkeypatch.setenv("MCN_TEST_ASR_BEARER", "streng-geheim")
    _run("register", "--key", "asr-l", "--label", "ASR", "--capability", "ASR",
         "--mode", "ASYNC", "--endpoint", "https://h.local/asr",
         "--bearer-env", "MCN_TEST_ASR_BEARER", "--actor", str(app_user.id))
    ausgabe = _run("list")
    assert "asr-l" in ausgabe and "Bearer:ja" in ausgabe
    assert "streng-geheim" not in ausgabe


@override_settings(MCN_CRED_KEY=KEY)
def test_set_bearer_und_clear(app_user, monkeypatch):
    monkeypatch.setenv("MCN_TEST_ASR_BEARER", "tok")
    _run("register", "--key", "asr-b", "--label", "ASR", "--capability", "ASR",
         "--mode", "ASYNC", "--endpoint", "https://h.local/asr",
         "--actor", str(app_user.id))
    _run("set-bearer", "--key", "asr-b", "--bearer-env", "MCN_TEST_ASR_BEARER",
         "--actor", str(app_user.id))
    assert Tool.objects.get(tool_key="asr-b").bearer_encrypted is not None
    _run("set-bearer", "--key", "asr-b", "--clear", "--actor", str(app_user.id))
    assert Tool.objects.get(tool_key="asr-b").bearer_encrypted is None


def test_deactivate(app_user):
    _run("register", "--key", "asr-d", "--label", "ASR", "--capability", "ASR",
         "--mode", "ASYNC", "--endpoint", "https://h.local/asr",
         "--actor", str(app_user.id))
    _run("deactivate", "--key", "asr-d", "--actor", str(app_user.id))
    assert Tool.objects.get(tool_key="asr-d").status == "INACTIVE"


def test_unbekanntes_werkzeug_fehler(app_user):
    with pytest.raises(CommandError):
        _run("set-bearer", "--key", "gibtsnicht", "--clear", "--actor", str(app_user.id))


@override_settings(MCN_CRED_KEY="")
def test_register_mit_bearer_ohne_cred_key_fehler(app_user, monkeypatch):
    # Ohne MCN_CRED_KEY scheitert das Bearer — als klarer CommandError, nicht als
    # roher Traceback. Das Werkzeug wurde davor bereits registriert (committet).
    monkeypatch.setenv("MCN_TEST_ASR_BEARER", "x")
    with pytest.raises(CommandError):
        _run("register", "--key", "asr-nokey", "--label", "ASR", "--capability", "ASR",
             "--mode", "ASYNC", "--endpoint", "https://h.local/asr",
             "--bearer-env", "MCN_TEST_ASR_BEARER", "--actor", str(app_user.id))
    assert Tool.objects.filter(tool_key="asr-nokey").exists()   # angelegt, ohne Bearer
    assert Tool.objects.get(tool_key="asr-nokey").bearer_encrypted is None
