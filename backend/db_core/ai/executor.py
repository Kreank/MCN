"""Executor — klammert einen KI-Lauf und protokolliert ihn in `ai.ai_run`.

Ein Lauf wird beim Start mit seiner **Provenance** angelegt (Modell, Workflow,
Prompt, auslösender Benutzer, Rechtekontext, Quellen, geplante Werkzeuge) und am
Ende **genau einmal** abgeschlossen (Ausgang: OK/FEHLER, Ablaufzeit, Ressourcen).
Die „genau einmal"-Garantie ist ein DB-Trigger (`guard_ai_run_update`), nicht
Code.

Bewusst getrennte Transaktionen: Start und Abschluss sind je eine kurze
`business_transaction`; die eigentliche Arbeit (LLM-Aufruf, Werkzeuge) läuft
**dazwischen ohne gehaltene DB-Transaktion** — sonst hinge eine Zeile an einem
langsamen Modell-Endpoint.

`sources`/`tools_used` sind nach dem INSERT unveränderlich (Trigger lässt nur die
Ausgangsfelder nachtragen). Das passt zur deterministischen Architektur: Quellen-
und Werkzeugplan stehen vor dem Lauf fest (siehe `docs/ki-orchestrierung.md`).
Was erst währenddessen anfällt (Tokenverbrauch o. Ä.), wandert in
`resource_usage` — das darf beim Abschluss geschrieben werden.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import AiRun

from .llm import LlmBackend, LlmMessage, LlmResponse

_MAX_ERROR = 2000  # error_message knapp halten


class RunHandle:
    """Griff auf den laufenden `ai_run`. Über `generate()` aufgerufene Modell-
    Antworten summieren ihren `usage` automatisch in `resource_usage`."""

    def __init__(self, run_id: uuid.UUID, backend: LlmBackend):
        self.id = run_id
        self.backend = backend
        self.usage: dict = {}

    def generate(self, messages: list[LlmMessage], **kwargs) -> LlmResponse:
        resp = self.backend.generate(messages, **kwargs)
        for schluessel, wert in resp.usage.items():
            if isinstance(wert, (int, float)) and not isinstance(wert, bool):
                self.usage[schluessel] = self.usage.get(schluessel, 0) + wert
        return resp


@contextmanager
def ai_run(
    *,
    actor_id: uuid.UUID,
    backend: LlmBackend,
    workflow_name: str,
    workflow_version: str,
    prompt_version: str = "v1",
    permission_context: dict | None = None,
    sources: list | None = None,
    tools_used: list | None = None,
    workflow_run_id: uuid.UUID | None = None,
):
    """Kontextmanager um einen KI-Lauf.

    Legt den Lauf an, liefert einen `RunHandle`, und schließt ihn beim Verlassen
    ab: normal → OK, bei einer Ausnahme → FEHLER (die Ausnahme wird
    weitergereicht). Modell-Name/-Version kommen aus dem `backend` — so trägt der
    Lauf ehrlich, welches Modell tatsächlich entschieden hat (Grundlage des
    Vergleichs).
    """
    run_id = uuid.uuid4()
    with business_transaction(actor_id):
        AiRun.objects.create(
            id=run_id,
            model_name=backend.model_name,
            model_version=backend.model_version,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            prompt_version=prompt_version,
            triggered_by_user_id=actor_id,
            permission_context=permission_context or {},
            sources=sources or [],
            tools_used=tools_used or [],
            workflow_run_id=workflow_run_id,
        )

    handle = RunHandle(run_id, backend)
    try:
        yield handle
    except BaseException as exc:  # noqa: BLE001 — der Lauf muss IMMER abgeschlossen werden
        _finish(actor_id, handle, status="FEHLER", error=str(exc)[:_MAX_ERROR])
        raise
    else:
        _finish(actor_id, handle, status="OK", error=None)


def _finish(actor_id: uuid.UUID, handle: RunHandle, *, status: str, error: str | None) -> None:
    """Schließt den Lauf ab. Schreibt NUR die Ausgangsfelder (update_fields), damit
    der Trigger nichts anderes als geändert sieht.

    Scheitert der Abschluss selbst (z. B. DB weg), hat der ursprüngliche Fehler
    Vorrang — der Lauf bleibt dann unabgeschlossen und fällt einem späteren Sweep
    zu (ABBRUCH). Wir verschlucken den ursprünglichen Fehler nie."""
    try:
        with business_transaction(actor_id):
            run = AiRun.objects.get(id=handle.id)
            run.finished_at = timezone.now()
            run.result_status = status
            run.error_message = error
            run.resource_usage = handle.usage
            run.save(
                update_fields=["finished_at", "result_status", "error_message", "resource_usage"]
            )
    except Exception:  # noqa: BLE001
        if status == "OK":
            raise  # bei Erfolg ist der Abschlussfehler DER Fehler
        # bei bereits vorliegendem Fehler: nicht überdecken (Sweep räumt auf)
