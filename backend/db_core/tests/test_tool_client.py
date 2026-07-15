"""Tool-Client (db_core.ai.tool_client) — Poll-Modell, Fehler-Taxonomie, Hygiene.

DB-frei. Dispatch/Poll-Mapping über einen injizierten Transport; die reale
HTTP-Fehler-Klassifikation über einen urllib-Monkeypatch. Geprüft wird besonders:
Geräte-Freitext landet NIE im Ergebnis, metrics werden whitelisted, und keine
Fehlermeldung nennt URL oder Bearer.
"""
import urllib.error
import urllib.request

import pytest

from db_core.ai.tool_client import (
    ToolClient,
    ToolError,
    ToolResult,
    _default_transport,
)


class _FakeTool:
    endpoint_url = "https://geheim-handy.intern/asr"
    timeout_seconds = 30
    contract_version = "1"


class Recording:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append(
            {"method": method, "url": url, "body": body, "headers": headers, "timeout": timeout}
        )
        return self.response


ENV = {"contract_version": "1", "capability": "ASR", "input": {"ref": "x"}}


# --- Dispatch / Poll -------------------------------------------------------

def test_dispatch_ok_mapping_und_metrics_whitelist():
    t = Recording({
        "contract_version": "1",
        "status": "ok",
        "output": {"transcript": "Heizung entlüftet."},
        "metrics": {"duration_ms": 1200, "model": "whisper", "geheim": "leak", "flag": True},
        "content_hash": "h1",
    })
    res = ToolClient(transport=t).dispatch(_FakeTool(), ENV, bearer="supergeheim")

    call = t.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://geheim-handy.intern/asr"
    assert call["body"] == ENV
    assert call["headers"]["Authorization"] == "Bearer supergeheim"
    assert res.status == "ok"
    assert res.output == {"transcript": "Heizung entlüftet."}
    assert res.metrics == {"duration_ms": 1200, "model": "whisper"}   # geheim/flag raus
    assert res.content_hash == "h1"


def test_dispatch_pending_liefert_job_id():
    res = ToolClient(transport=Recording(
        {"contract_version": "1", "status": "pending", "job_id": "j-1"}
    )).dispatch(_FakeTool(), ENV)
    assert res.status == "pending" and res.job_id == "j-1"


def test_poll_baut_job_url():
    t = Recording({"contract_version": "1", "status": "ok", "output": {}})
    ToolClient(transport=t).poll(_FakeTool(), "j-1", bearer="b")
    assert t.calls[0]["method"] == "GET"
    assert t.calls[0]["url"] == "https://geheim-handy.intern/asr/jobs/j-1"


def test_geraete_freitext_landet_nie_im_ergebnis():
    res = ToolClient(transport=Recording({
        "contract_version": "1",
        "status": "error",
        "error": {"code": "ASR_FAIL", "message": "personenbezogenes Transkript im Fehler"},
    })).dispatch(_FakeTool(), ENV)
    assert res.status == "error"
    assert res.error_code == "ASR_FAIL"                # knapper Code als Hinweis
    assert "personenbezogenes" not in str(res)         # aber NIE der Freitext


def test_ungueltiger_status_ist_toolerror():
    with pytest.raises(ToolError) as ei:
        ToolClient(transport=Recording(
            {"contract_version": "1", "status": "quatsch"}
        )).dispatch(_FakeTool(), ENV)
    assert ei.value.code == "TOOL_ERROR"


def test_output_nicht_dict_wird_none():
    res = ToolClient(transport=Recording(
        {"contract_version": "1", "status": "ok", "output": [1, 2, 3]}
    )).dispatch(_FakeTool(), ENV)
    assert res.status == "ok" and res.output is None


def test_fehlende_contract_version_abgelehnt():
    with pytest.raises(ToolError) as ei:
        ToolClient(transport=Recording({"status": "ok"})).dispatch(_FakeTool(), ENV)
    assert ei.value.code == "CONTRACT_VERSION"


class _FakeTool2(_FakeTool):
    contract_version = "2"


def test_downgrade_zweistellig_korrekt():
    # "10" ist NEUER als "2" — numerisch, nicht lexikografisch: darf NICHT ablehnen.
    res = ToolClient(transport=Recording(
        {"contract_version": "10", "status": "ok", "output": {}}
    )).dispatch(_FakeTool2(), ENV)
    assert res.status == "ok"
    # "1" ist aelter als "2" → ablehnen.
    with pytest.raises(ToolError) as ei:
        ToolClient(transport=Recording(
            {"contract_version": "1", "status": "ok"}
        )).dispatch(_FakeTool2(), ENV)
    assert ei.value.code == "CONTRACT_VERSION"


def test_poll_pending_und_error():
    p = ToolClient(transport=Recording(
        {"contract_version": "1", "status": "pending", "job_id": "j-9"}
    )).poll(_FakeTool(), "j-9")
    assert p.status == "pending"
    e = ToolClient(transport=Recording(
        {"contract_version": "1", "status": "error", "error": {"code": "X"}}
    )).poll(_FakeTool(), "j-9")
    assert e.status == "error" and e.error_code == "X"


def test_poll_ungueltige_job_id():
    with pytest.raises(ToolError) as ei:
        ToolClient(transport=Recording({"contract_version": "1", "status": "ok"})).poll(
            _FakeTool(), "../admin"
        )
    assert ei.value.code == "BAD_INPUT"


def test_downgrade_schutz():
    with pytest.raises(ToolError) as ei:
        ToolClient(transport=Recording({"status": "ok", "contract_version": "0"})).dispatch(
            _FakeTool(), ENV
        )
    assert ei.value.code == "CONTRACT_VERSION" and ei.value.transient is False


def test_ohne_endpoint_bad_input():
    class _Kein(_FakeTool):
        endpoint_url = None

    with pytest.raises(ToolError) as ei:
        ToolClient(transport=Recording({"status": "ok"})).dispatch(_Kein(), ENV)
    assert ei.value.code == "BAD_INPUT"


def test_transient_klassifikation():
    assert ToolError("TIMEOUT", "x").transient is True
    assert ToolError("UNREACHABLE", "x").transient is True
    assert ToolError("AUTH", "x").transient is False


# --- Realer Transport: HTTP-Fehler-Mapping + Secret-Hygiene ----------------

_URL = "https://geheim-handy.intern/asr"
_HEADERS = {"Authorization": "Bearer supergeheim"}


def _assert_kein_leak(msg):
    assert "geheim-handy.intern" not in msg and "supergeheim" not in msg


@pytest.mark.parametrize("code,erwartet", [(401, "AUTH"), (403, "AUTH"), (404, "BAD_INPUT"),
                                           (400, "BAD_INPUT"), (500, "TOOL_ERROR"), (503, "TOOL_ERROR")])
def test_transport_httperror_mapping(monkeypatch, code, erwartet):
    def boom(*_a, **_k):
        raise urllib.error.HTTPError(_URL, code, "err", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ToolError) as ei:
        _default_transport("POST", _URL, {"x": 1}, _HEADERS, 5)
    assert ei.value.code == erwartet
    _assert_kein_leak(str(ei.value))


def test_transport_unerreichbar(monkeypatch):
    def boom(*_a, **_k):
        raise urllib.error.URLError("connection refused: geheim-handy.intern")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ToolError) as ei:
        _default_transport("POST", _URL, {}, _HEADERS, 5)
    assert ei.value.code == "UNREACHABLE"
    _assert_kein_leak(str(ei.value))


def test_transport_timeout(monkeypatch):
    def boom(*_a, **_k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ToolError) as ei:
        _default_transport("POST", _URL, {}, _HEADERS, 5)
    assert ei.value.code == "TIMEOUT"


def test_transport_kaputtes_json(monkeypatch):
    class _Resp:
        def read(self):
            return b"kein json"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(ToolError) as ei:
        _default_transport("POST", _URL, {}, _HEADERS, 5)
    assert ei.value.code == "TOOL_ERROR"
