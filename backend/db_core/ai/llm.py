"""Modell-agnostischer LLM-Adapter.

Ein Port (`LlmBackend`), austauschbare Backends dahinter. **Modell, Endpoint und
Parameter sind Konfiguration, kein Code** — genau, damit Modelle im Betrieb
verglichen und getauscht werden können (das Fundament von A/B: gleicher Input,
zwei Profile → `AiRun.model_name`/`model_version` unterscheiden die Läufe).

Betriebsrahmen (siehe `docs/ki-orchestrierung.md`): lokal-only, das größte Modell
läuft als OpenAI-kompatibler Endpoint (llama.cpp/Ollama/vLLM) im eigenen Netz.
Deshalb spricht das reale Backend die OpenAI-Chat-Schnittstelle; die Wahl des
konkreten Modells bleibt bewusst offen.

Doktrin (wie `storage.py`/`mail_crypto.py`): jede Störung wird zu einer eigenen
Exception (`LlmError`), es wird **nie ein Secret geloggt** (kein Endpoint, kein
API-Key in einer Fehlermeldung), und fehlende Konfiguration ist **fail-closed**.

Ohne konfiguriertes Profil liefert `get_backend()` das `FakeBackend` — so laufen
Dev und Tests ohne ein reales Modell.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class LlmError(Exception):
    """Störung im LLM-Adapter (Konfiguration, Transport, Antwortform).

    Trägt bewusst nur eine knappe, secret-freie Meldung — nie Endpoint oder
    API-Key.
    """


@dataclass(frozen=True)
class LlmMessage:
    """Eine Chat-Nachricht. role ∈ {system, user, assistant}."""

    role: str
    content: str


@dataclass(frozen=True)
class LlmResponse:
    """Ergebnis eines Laufs.

    `data` ist die geparste JSON-Antwort, wenn ein `schema` erzwungen wurde
    (Constrained Decoding), sonst None. `usage` (Token o. Ä.) wandert in
    `AiRun.resource_usage`; `model_name`/`model_version` in den Lauf.
    """

    text: str
    data: dict | None
    model_name: str
    model_version: str
    usage: dict = field(default_factory=dict)
    raw: dict | None = None


class LlmBackend(ABC):
    """Der Port. Ein Backend erzeugt aus Nachrichten eine Antwort.

    `schema` (JSON-Schema) verlangt Constrained Decoding: Das Backend zwingt die
    Ausgabe in die Form; `LlmResponse.data` trägt dann das geparste Objekt. Das
    ist der zentrale Kompensationshebel für lokale Modelle (siehe Skizze).
    """

    #: Für Protokoll/Vergleich; von der Konfiguration gesetzt.
    model_name: str = "unbekannt"
    model_version: str = "unbekannt"

    @abstractmethod
    def generate(
        self,
        messages: list[LlmMessage],
        *,
        schema: dict | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> LlmResponse:
        ...


# ---------------------------------------------------------------------------
# Fake-Backend — deterministisch, ohne laufendes Modell (Tests + Dev-Default)
# ---------------------------------------------------------------------------


class FakeBackend(LlmBackend):
    """Deterministisches Backend ohne Netz.

    Ohne `responses` antwortet es als Echo (gibt die letzte Nutzernachricht
    zurück, bei `schema` als `{"echo": ...}`). Mit `responses` gibt es die
    skripteten Antworten der Reihe nach aus — ein Element darf ein `str` (→ Text),
    ein `dict` (→ JSON-`data`, `text` ist die Serialisierung) oder ein fertiges
    `LlmResponse` sein. Ist die Liste erschöpft, ist das ein Fehler (ein Test, der
    mehr Aufrufe macht als erwartet, soll auffallen).
    """

    name = "fake"

    def __init__(
        self,
        *,
        responses: list | None = None,
        model_name: str = "fake",
        model_version: str = "0",
    ):
        self._responses = list(responses) if responses is not None else None
        self._i = 0
        self.model_name = model_name
        self.model_version = model_version

    def generate(
        self,
        messages: list[LlmMessage],
        *,
        schema: dict | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> LlmResponse:
        if not messages:
            raise LlmError("Keine Nachrichten übergeben.")

        if self._responses is None:
            letzter = messages[-1].content
            if schema is None:
                return self._response(text=letzter, data=None)
            return self._response(text=json.dumps({"echo": letzter}), data={"echo": letzter})

        if self._i >= len(self._responses):
            raise LlmError("FakeBackend: keine weitere skriptete Antwort vorhanden.")
        item = self._responses[self._i]
        self._i += 1

        if isinstance(item, LlmResponse):
            return item
        if isinstance(item, dict):
            return self._response(text=json.dumps(item), data=item)
        if isinstance(item, str):
            return self._response(text=item, data=None)
        raise LlmError(f"FakeBackend: unerwarteter Antworttyp {type(item).__name__}.")

    def _response(self, *, text: str, data: dict | None) -> LlmResponse:
        return LlmResponse(
            text=text,
            data=data,
            model_name=self.model_name,
            model_version=self.model_version,
        )


# ---------------------------------------------------------------------------
# OpenAI-kompatibles Backend (llama.cpp / Ollama / vLLM)
# ---------------------------------------------------------------------------


def _default_transport(url: str, body: dict, headers: dict, timeout: float | None) -> dict:
    """POST JSON → JSON, über die stdlib (kein zusätzlicher Dependency).

    Kapselt jede Netz-/Formstörung in `LlmError` OHNE die URL oder Header (die den
    API-Key tragen könnten) in die Meldung zu ziehen.
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise LlmError(f"LLM-Endpoint antwortete mit HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LlmError("LLM-Endpoint nicht erreichbar.") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise LlmError("LLM-Antwort war kein gültiges JSON.") from None


class OpenAICompatBackend(LlmBackend):
    """Spricht die OpenAI-Chat-Schnittstelle (`/chat/completions`).

    `transport` ist injizierbar (Standard: stdlib-HTTP), damit das Request-/
    Response-Mapping ohne laufendes Modell testbar ist. Bei `schema` wird
    `response_format` als `json_schema` gesetzt — Server, die Constrained Decoding
    beherrschen (llama.cpp GBNF, vLLM guided decoding), erzwingen damit die Form.
    """

    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        model_version: str = "unbekannt",
        api_key: str | None = None,
        timeout: float | None = 120.0,
        transport=None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.model_name = model
        self.model_version = model_version
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport or _default_transport

    def generate(
        self,
        messages: list[LlmMessage],
        *,
        schema: dict | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> LlmResponse:
        if not messages:
            raise LlmError("Keine Nachrichten übergeben.")

        payload: dict = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "antwort", "schema": schema, "strict": True},
            }

        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        resp = self._transport(
            f"{self._base_url}/chat/completions", payload, headers, timeout or self._timeout
        )

        try:
            text = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LlmError("LLM-Antwort ohne choices[0].message.content.") from None
        if text is None:
            raise LlmError("LLM-Antwort ohne Inhalt.")

        data = None
        if schema is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                raise LlmError("Erzwungene JSON-Antwort war nicht parsebar.") from None

        usage = resp.get("usage", {}) if isinstance(resp, dict) else {}
        return LlmResponse(
            text=text,
            data=data,
            model_name=self._model,
            model_version=self.model_version,
            usage=usage if isinstance(usage, dict) else {},
            raw=resp if isinstance(resp, dict) else None,
        )


# ---------------------------------------------------------------------------
# Konfiguration / Fabrik — Profile machen die Modelle austauschbar
# ---------------------------------------------------------------------------
#
# MCN_AI_PROFILES: JSON-Objekt {profilname: {...}}. Ein Profil:
#   {"backend": "openai_compat", "base_url": "http://host:8080/v1",
#    "model": "mistral-nemo", "model_version": "q4", "api_key_env": "MCN_AI_KEY"}
#   oder {"backend": "fake", "model": "…", "model_version": "…"}
# MCN_AI_DEFAULT_PROFILE: welches Profil ohne Angabe genommen wird.
#
# Der API-Key steht NIE im Profil, nur ein Verweis (`api_key_env`) auf die
# Umgebungsvariable — dieselbe Doktrin wie `credential_reference` bei den
# Lieferanten-Anbindungen (nie das Secret in Fachdaten).


def _load_profiles() -> dict:
    raw = os.environ.get("MCN_AI_PROFILES", "").strip()
    if not raw:
        return {}
    try:
        profiles = json.loads(raw)
    except json.JSONDecodeError:
        raise LlmError("MCN_AI_PROFILES ist kein gültiges JSON.") from None
    if not isinstance(profiles, dict):
        raise LlmError("MCN_AI_PROFILES muss ein JSON-Objekt sein.")
    return profiles


def get_backend(profile: str | None = None, *, transport=None) -> LlmBackend:
    """Liefert das Backend für ein Profil (oder das Default-Profil).

    Nur wenn WEDER ein Profil verlangt NOCH ein Default gesetzt ist, kommt das
    `FakeBackend` (Dev/Test laufen ohne Modell). Sobald ein Profil verlangt oder als
    `MCN_AI_DEFAULT_PROFILE` gesetzt ist, es aber nicht konfiguriert ist, ist das
    fail-closed ein `LlmError` — **nie ein stilles Ausweichen aufs Fake** (sonst liefe
    die Produktion mit einem gesetzten, aber vertippten Default gegen ein Echo-Modell).
    """
    profiles = _load_profiles()

    if profile is not None:
        name = profile
    elif os.environ.get("MCN_AI_DEFAULT_PROFILE"):
        name = os.environ["MCN_AI_DEFAULT_PROFILE"]
    else:
        return FakeBackend()

    if name not in profiles:
        raise LlmError(f"KI-Profil '{name}' ist nicht konfiguriert.")

    cfg = profiles[name]
    kind = cfg.get("backend")

    if kind == "fake":
        return FakeBackend(
            model_name=cfg.get("model", "fake"),
            model_version=cfg.get("model_version", "0"),
        )
    if kind == "openai_compat":
        try:
            base_url = cfg["base_url"]
            model = cfg["model"]
        except KeyError as exc:
            raise LlmError(f"KI-Profil '{name}': Pflichtfeld {exc} fehlt.") from None
        api_key = None
        key_env = cfg.get("api_key_env")
        if key_env:
            api_key = os.environ.get(key_env)
            if not api_key:
                raise LlmError(f"KI-Profil '{name}': Umgebungsvariable {key_env} nicht gesetzt.")
        return OpenAICompatBackend(
            base_url=base_url,
            model=model,
            model_version=cfg.get("model_version", "unbekannt"),
            api_key=api_key,
            transport=transport,
        )

    raise LlmError(f"KI-Profil '{name}': unbekannter backend-Typ '{kind}'.")
