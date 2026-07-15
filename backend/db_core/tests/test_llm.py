"""LLM-Adapter (db_core.ai.llm) — Backends und Profil-Fabrik.

DB-frei. Das OpenAI-kompatible Backend wird über einen injizierten Transport
geprüft — Request-/Response-Mapping, Constrained-Decoding-Weg und Fehlerformen,
alles ohne laufendes Modell. Die Fabrik beweist, dass Modelle **Konfiguration**
sind (Profilwahl → anderes Backend) und dass fehlende/kaputte Konfiguration
fail-closed ist.
"""
import json
import urllib.error
import urllib.request

import pytest

from db_core.ai.llm import (
    FakeBackend,
    LlmError,
    LlmMessage,
    LlmResponse,
    OpenAICompatBackend,
    _default_transport,
    get_backend,
)


def _msgs(user="Fass den Einsatz zusammen."):
    return [LlmMessage("system", "Du bist ein Assistent."), LlmMessage("user", user)]


def _canned(content, usage=None):
    """Eine OpenAI-kompatible Antwort."""
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": usage or {"total_tokens": 7},
        "model": "server-modell",
    }


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        return self.response


# --- FakeBackend -----------------------------------------------------------

def test_fake_echo_ohne_schema():
    resp = FakeBackend().generate(_msgs("Hallo"))
    assert resp.text == "Hallo"
    assert resp.data is None
    assert resp.model_name == "fake"


def test_fake_echo_mit_schema_liefert_data():
    resp = FakeBackend().generate(_msgs("Hallo"), schema={"type": "object"})
    assert resp.data == {"echo": "Hallo"}
    assert json.loads(resp.text) == {"echo": "Hallo"}


def test_fake_skriptet_str_und_dict():
    fake = FakeBackend(responses=["erste", {"positionen": [1, 2]}], model_name="qwen", model_version="q4")
    r1 = fake.generate(_msgs())
    r2 = fake.generate(_msgs())
    assert r1.text == "erste" and r1.model_name == "qwen"
    assert r2.data == {"positionen": [1, 2]}


def test_fake_erschoepft_ist_fehler():
    fake = FakeBackend(responses=["nur eine"])
    fake.generate(_msgs())
    with pytest.raises(LlmError):
        fake.generate(_msgs())


def test_fake_ohne_nachrichten_ist_fehler():
    with pytest.raises(LlmError):
        FakeBackend().generate([])


# --- OpenAICompatBackend ---------------------------------------------------

def test_openai_request_und_response_mapping():
    transport = RecordingTransport(_canned("Zusammenfassung."))
    backend = OpenAICompatBackend(
        base_url="http://host:8080/v1/", model="mistral-nemo", model_version="q4", transport=transport
    )
    resp = backend.generate(_msgs("X"), temperature=0.1, max_tokens=256)

    call = transport.calls[0]
    assert call["url"] == "http://host:8080/v1/chat/completions"  # rstrip + Pfad
    assert call["body"]["model"] == "mistral-nemo"
    assert call["body"]["messages"][1] == {"role": "user", "content": "X"}
    assert call["body"]["temperature"] == 0.1 and call["body"]["max_tokens"] == 256
    assert "response_format" not in call["body"]  # ohne Schema kein Zwang

    assert resp.text == "Zusammenfassung."
    assert resp.data is None
    assert resp.model_name == "mistral-nemo" and resp.model_version == "q4"
    assert resp.usage == {"total_tokens": 7}


def test_openai_schema_setzt_response_format_und_parst():
    transport = RecordingTransport(_canned('{"positionen": [{"menge": 2}]}'))
    backend = OpenAICompatBackend(base_url="http://h/v1", model="m", transport=transport)
    schema = {"type": "object", "properties": {"positionen": {"type": "array"}}}
    resp = backend.generate(_msgs(), schema=schema)

    rf = transport.calls[0]["body"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == schema and rf["json_schema"]["strict"] is True
    assert resp.data == {"positionen": [{"menge": 2}]}


def test_openai_api_key_als_bearer_header():
    transport = RecordingTransport(_canned("ok"))
    backend = OpenAICompatBackend(
        base_url="http://h/v1", model="m", api_key="geheim", transport=transport
    )
    backend.generate(_msgs())
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer geheim"


def test_openai_transportfehler_wird_llmerror():
    def raising(*_a, **_k):
        raise LlmError("Endpoint nicht erreichbar.")

    backend = OpenAICompatBackend(base_url="http://h/v1", model="m", transport=raising)
    with pytest.raises(LlmError):
        backend.generate(_msgs())


def test_openai_antwort_ohne_content_ist_fehler():
    backend = OpenAICompatBackend(
        base_url="http://h/v1", model="m", transport=RecordingTransport({"choices": []})
    )
    with pytest.raises(LlmError):
        backend.generate(_msgs())


def test_openai_schema_aber_kein_json_ist_fehler():
    transport = RecordingTransport(_canned("kein json"))
    backend = OpenAICompatBackend(base_url="http://h/v1", model="m", transport=transport)
    with pytest.raises(LlmError):
        backend.generate(_msgs(), schema={"type": "object"})


# --- Fabrik: Profile machen Modelle austauschbar ---------------------------

def test_fabrik_ohne_konfiguration_liefert_fake(monkeypatch):
    monkeypatch.delenv("MCN_AI_PROFILES", raising=False)
    monkeypatch.delenv("MCN_AI_DEFAULT_PROFILE", raising=False)
    assert isinstance(get_backend(), FakeBackend)


def test_fabrik_unbekanntes_profil_ist_fehler(monkeypatch):
    monkeypatch.delenv("MCN_AI_PROFILES", raising=False)
    with pytest.raises(LlmError):
        get_backend("gibtsnicht")


def test_fabrik_baut_openai_backend_aus_profil(monkeypatch):
    profiles = {
        "mistral": {
            "backend": "openai_compat",
            "base_url": "http://server:8080/v1",
            "model": "mistral-nemo",
            "model_version": "q4",
        }
    }
    monkeypatch.setenv("MCN_AI_PROFILES", json.dumps(profiles))
    monkeypatch.setenv("MCN_AI_DEFAULT_PROFILE", "mistral")
    transport = RecordingTransport(_canned("ok"))
    backend = get_backend(transport=transport)
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.model_name == "mistral-nemo" and backend.model_version == "q4"
    # Zwei Profile nebeneinander → A/B: dasselbe get_backend, anderer Name, anderes Modell.
    backend.generate(_msgs())
    assert transport.calls[0]["body"]["model"] == "mistral-nemo"


def test_fabrik_fehlender_api_key_ist_fehler(monkeypatch):
    profiles = {
        "p": {
            "backend": "openai_compat",
            "base_url": "http://h/v1",
            "model": "m",
            "api_key_env": "MCN_AI_KEY",
        }
    }
    monkeypatch.setenv("MCN_AI_PROFILES", json.dumps(profiles))
    monkeypatch.delenv("MCN_AI_KEY", raising=False)
    with pytest.raises(LlmError):
        get_backend("p")


def test_fabrik_kaputtes_json_ist_fehler(monkeypatch):
    monkeypatch.setenv("MCN_AI_PROFILES", "{kaputt")
    with pytest.raises(LlmError):
        get_backend("egal")


def test_fabrik_fake_profil(monkeypatch):
    profiles = {"f": {"backend": "fake", "model": "testmodell", "model_version": "1"}}
    monkeypatch.setenv("MCN_AI_PROFILES", json.dumps(profiles))
    backend = get_backend("f")
    assert isinstance(backend, FakeBackend)
    assert backend.generate(_msgs()).model_name == "testmodell"


def test_fabrik_default_gesetzt_aber_unbekannt_ist_fehler(monkeypatch):
    """Fail-closed: ein GESETZTER, aber nicht auflösbarer Default weicht nicht still
    aufs Fake aus (sonst liefe Produktion mit Echo-Modell)."""
    monkeypatch.setenv("MCN_AI_DEFAULT_PROFILE", "mistral")
    monkeypatch.delenv("MCN_AI_PROFILES", raising=False)  # Tippfehler/vergessen
    with pytest.raises(LlmError):
        get_backend()


def test_openai_content_none_ist_fehler():
    transport = RecordingTransport(_canned(None))
    backend = OpenAICompatBackend(base_url="http://h/v1", model="m", transport=transport)
    with pytest.raises(LlmError):
        backend.generate(_msgs())


# --- _default_transport: der reale HTTP-Pfad + Secret-Hygiene ---------------

class _FakeHttpResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


_URL = "http://geheim-host.intern/v1/chat/completions"
_HEADERS = {"Authorization": "Bearer supergeheim"}


def _assert_kein_leak(msg):
    assert "geheim-host.intern" not in msg
    assert "supergeheim" not in msg


def test_transport_happy_path(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeHttpResponse(b'{"ok": 1}')
    )
    assert _default_transport(_URL, {"x": 1}, _HEADERS, 5) == {"ok": 1}


def test_transport_httperror_ohne_leak(monkeypatch):
    def boom(*_a, **_k):
        raise urllib.error.HTTPError(_URL, 503, "unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(LlmError) as ei:
        _default_transport(_URL, {}, _HEADERS, 5)
    assert "503" in str(ei.value)
    _assert_kein_leak(str(ei.value))


def test_transport_urlerror_ohne_leak(monkeypatch):
    def boom(*_a, **_k):
        raise urllib.error.URLError("Name or service not known: geheim-host.intern")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(LlmError) as ei:
        _default_transport(_URL, {}, _HEADERS, 5)
    _assert_kein_leak(str(ei.value))
    assert ei.value.__cause__ is None  # `from None` — keine Kette, die den Host trägt


def test_transport_kaputtes_json_ohne_leak(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeHttpResponse(b"kein json")
    )
    with pytest.raises(LlmError) as ei:
        _default_transport(_URL, {}, _HEADERS, 5)
    assert "JSON" in str(ei.value)
    _assert_kein_leak(str(ei.value))
