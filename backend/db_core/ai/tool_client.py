"""Tool-Client — spricht EIN (passives) Gerät im Poll-Modell an.

MCN initiiert alle Verbindungen: `dispatch` (POST) schickt den Auftrag inkl. Eingabe
ans Gerät; ist die Antwort `pending`, pollt MCN später den Job-Status (`poll`, GET).
Der Client ist die reine „rede mit EINEM Gerät"-Schicht — die Queue/State-Machine
liegt darüber (Dispatcher/Runtime, spätere Stufe).

Transport injizierbar (Default stdlib-HTTP) → testbar ohne echtes Gerät, wie llm.py.

Doktrin (Rev 2 des Vertrags):
- **Nie Endpoint/Bearer/Klartext** in einer Fehlermeldung (die `ToolError`-Meldungen
  nennen nur den HTTP-Status/Grund, nie die URL oder das Token).
- Das Gerät ist **untrusted**: seine `metrics` werden auf eine Whitelist reduziert,
  seine **Freitext-Fehlermeldung wird NIE übernommen** (nur ein knapper Code-Hinweis),
  und eine vom Gerät behauptete Vertrauensstufe existiert hier gar nicht — die leitet
  der Aufrufer aus der Capability ab.
- Fehler sind **klassifiziert** (transient vs. permanent), damit die Retry-Logik der
  Runtime nicht raten muss.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Enges Muster für eine Geräte-Job-Kennung (aus untrusted Antwort) — verhindert,
# dass ein "../admin" in den Poll-Pfad interpoliert wird.
_JOB_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Fehler-Taxonomie: transient → Retry sinnvoll, permanent → Schritt scheitert.
TRANSIENT = frozenset({"UNREACHABLE", "TIMEOUT", "TOOL_ERROR"})
PERMANENT = frozenset({"AUTH", "BAD_INPUT", "CONTRACT_VERSION", "UNSUPPORTED"})

_METRIC_KEYS = (
    "duration_ms", "tokens", "prompt_tokens", "completion_tokens", "total_tokens", "model",
)
_MAX_CODE = 64


class ToolError(Exception):
    """Ein Werkzeug-Aufruf ist gescheitert. `code` ist aus der Taxonomie; die Meldung
    ist secret-frei (nie URL/Bearer)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    @property
    def transient(self) -> bool:
        return self.code in TRANSIENT


@dataclass(frozen=True)
class ToolResult:
    """Ausgang eines Dispatch/Poll. `status` ∈ {ok, pending, error}. Kein
    `is_untrusted` — die Vertrauensstufe ist keine Geräte-Aussage."""

    status: str
    output: dict | None = None
    job_id: str | None = None
    error_code: str | None = None       # knapper Geräte-Hinweis, nie Freitext
    metrics: dict = field(default_factory=dict)
    content_hash: str | None = None


def _clean_metrics(raw) -> dict:
    """Nur numerische/kurze Whitelist-Keys übernehmen (Geräte-Input ist untrusted)."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in _METRIC_KEYS:
        val = raw.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            out[key] = val
        elif isinstance(val, str):
            out[key] = val[:128]
    return out


def _default_transport(method: str, url: str, body, headers: dict, timeout):
    """HTTP → JSON über die stdlib. Jede Störung wird zu einer klassifizierten
    `ToolError` OHNE URL/Header (die den Bearer tragen) in der Meldung."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ToolError("AUTH", f"Gerät lehnte die Authentifizierung ab (HTTP {exc.code}).") from None
        if 400 <= exc.code < 500:
            raise ToolError("BAD_INPUT", f"Gerät wies die Anfrage ab (HTTP {exc.code}).") from None
        raise ToolError("TOOL_ERROR", f"Gerät meldete einen Fehler (HTTP {exc.code}).") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ToolError("TIMEOUT", "Gerät antwortete nicht rechtzeitig.") from None
        raise ToolError("UNREACHABLE", "Gerät nicht erreichbar.") from None
    except (TimeoutError, socket.timeout):
        raise ToolError("TIMEOUT", "Gerät antwortete nicht rechtzeitig.") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ToolError("TOOL_ERROR", "Gerät lieferte kein gültiges JSON.") from None


class ToolClient:
    """Ruft ein Werkzeug auf. `tool` liefert `endpoint_url`, `timeout_seconds`,
    `contract_version`."""

    def __init__(self, *, transport=None):
        self._transport = transport or _default_transport

    def dispatch(self, tool, envelope: dict, *, bearer: str | None = None, timeout=None) -> ToolResult:
        resp = self._call("POST", tool.endpoint_url, envelope, bearer, timeout, tool)
        return self._interpret(resp, tool)

    def poll(self, tool, job_id: str, *, bearer: str | None = None, timeout=None) -> ToolResult:
        if not isinstance(job_id, str) or not _JOB_ID.match(job_id):
            raise ToolError("BAD_INPUT", "Ungültige Job-Kennung.")
        base = (tool.endpoint_url or "").rstrip("/")
        resp = self._call("GET", f"{base}/jobs/{job_id}", None, bearer, timeout, tool)
        return self._interpret(resp, tool)

    def _call(self, method, url, body, bearer, timeout, tool):
        if not url:
            raise ToolError("BAD_INPUT", "Werkzeug hat keinen Endpoint (internes Werkzeug?).")
        headers = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        return self._transport(method, url, body, headers, timeout or tool.timeout_seconds)

    def _interpret(self, resp, tool) -> ToolResult:
        if not isinstance(resp, dict):
            raise ToolError("TOOL_ERROR", "Geräteantwort hatte kein Objekt-Format.")
        # Downgrade-Schutz: die Vertragsversion muss vorhanden und >= der erwarteten
        # sein — NUMERISCH verglichen (ein String-Vergleich wäre bei "10" vs "2"
        # falsch). Fehlt/unparsebar → ebenfalls ablehnen (fail-closed, das Envelope
        # verlangt das Feld).
        try:
            cv_num = int(resp.get("contract_version"))
            erwartet = int(tool.contract_version)
        except (TypeError, ValueError):
            raise ToolError("CONTRACT_VERSION", "Geräteantwort ohne gültige Vertragsversion.")
        if cv_num < erwartet:
            raise ToolError("CONTRACT_VERSION", "Gerät antwortete mit veralteter Vertragsversion.")
        status = resp.get("status")
        if status not in ("ok", "pending", "error"):
            raise ToolError("TOOL_ERROR", "Geräteantwort ohne gültigen Status.")

        error = resp.get("error")
        error_code = None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            error_code = error["code"][:_MAX_CODE]   # knapper Hinweis; nie die Freitextmeldung

        output = resp.get("output")
        return ToolResult(
            status=status,
            output=output if isinstance(output, dict) else None,
            job_id=resp.get("job_id") if isinstance(resp.get("job_id"), str) else None,
            error_code=error_code,
            metrics=_clean_metrics(resp.get("metrics")),
            content_hash=resp.get("content_hash") if isinstance(resp.get("content_hash"), str) else None,
        )
