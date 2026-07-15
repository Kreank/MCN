"""Workflow-Engine — treibt einen resume-baren ai.workflow_run Schritt für Schritt.

Der Anker der KI-Orchestrierung: ein Workflow reiht Werkzeug-Aufrufe (`tool_call`)
ein und geht **WAITING**; die Queue-Runtime (`runtime.tick`) macht die Calls terminal,
dann nimmt `resume_ready` den Workflow wieder auf und fährt den nächsten Schritt. Die
Idempotenz hängt an `(workflow_run, step_key)` — ein Schritt hat genau einen Call.

Ein **Handler** ist eine Funktion `handler(actor_id, wf) -> None`, die anhand von
`wf.current_step` und den Ergebnissen der vorigen Schritte das Nächste tut:
`enqueue_tool(...)` (→ WAITING) oder `finish_workflow(...)` (→ DONE/FAILED). Workflows
registrieren sich in `WORKFLOWS`; für Tests kann ein eigenes Dict übergeben werden.

Statusautomat (Trigger `guard_workflow_run`, 0106): QUEUED→RUNNING→WAITING→RUNNING→…
→DONE/FAILED. Der Handler läuft NIE unter gehaltener Sperre (er darf das LLM rufen);
die Wiederaufnahme fenced per SKIP LOCKED + WAITING→RUNNING gegen einen zweiten Tick.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import Tool, ToolCall, WorkflowRun

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"})

#: Registrierte Workflows: name -> handler(actor_id, wf). v1 trägt sich hier ein.
WORKFLOWS: dict = {}


def enqueue_tool(actor_id, wf, *, step_key, tool_key, input_ref, deadline_seconds=None):
    """Reiht einen Werkzeug-Aufruf für einen Schritt ein und setzt den Workflow WAITING.

    `input_ref` trägt NUR Referenzen/kleine Daten (z. B. `{"file_id": …}`) — kein PII-
    Rohtext, kein Blob (die Runtime lädt die Datei erst zur Dispatch-Zeit). Für ein
    passives Gerät sollte `deadline_seconds` gesetzt sein (sonst pollt die Runtime
    unbegrenzt weiter)."""
    tool = Tool.objects.filter(tool_key=tool_key, status="ACTIVE").first()
    if tool is None:
        raise ValueError(f"Werkzeug '{tool_key}' nicht gefunden oder inaktiv.")
    deadline = timezone.now() + timedelta(seconds=deadline_seconds) if deadline_seconds else None
    with business_transaction(actor_id):
        ToolCall.objects.create(
            id=uuid.uuid4(),
            workflow_run=wf,
            tool=tool,
            capability=tool.capability,
            capability_version=tool.capability_version,
            contract_version=tool.contract_version,
            step_key=step_key,
            input_ref=input_ref,
            deadline_at=deadline,
        )
        wf.current_step = step_key
        wf.status = "WAITING"
        wf.save(update_fields=["current_step", "status"])


def finish_workflow(actor_id, wf, status, *, error=None):
    """Schließt den Workflow ab (DONE/FAILED/CANCELLED)."""
    with business_transaction(actor_id):
        wf.status = status
        wf.finished_at = timezone.now()
        wf.error_message = error
        wf.save(update_fields=["status", "finished_at", "error_message"])


def tool_result(wf, step_key):
    """Der (terminale) tool_call eines Schritts — für den Handler beim Resume."""
    return ToolCall.objects.filter(workflow_run=wf, step_key=step_key).first()


def start_workflow(actor_id, *, workflow_name, workflow_version, triggered_by_user_id,
                   context=None, workflows=None):
    """Legt einen Workflow an und fährt den ersten Schritt."""
    workflows = workflows if workflows is not None else WORKFLOWS
    with business_transaction(actor_id):
        wf = WorkflowRun.objects.create(
            id=uuid.uuid4(),
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            triggered_by_user_id=triggered_by_user_id,
            context=context or {},
        )
    with business_transaction(actor_id):
        wf.status = "RUNNING"
        wf.save(update_fields=["status"])
    try:
        _run_handler(actor_id, wf, workflows)
    except Exception:
        # Der erste Schritt ist gescheitert (z. B. Werkzeug fehlt) → den Lauf
        # terminalisieren statt RUNNING hängen zu lassen; der Fehler geht an den
        # Aufrufer (der Endpoint macht daraus eine 422).
        if wf.status not in ("DONE", "FAILED", "CANCELLED"):
            finish_workflow(actor_id, wf, "FAILED", error="Start fehlgeschlagen.")
        raise
    return wf


def resume_ready(actor_id, *, workflows=None, limit=20):
    """Nimmt WAITING-Workflows wieder auf, deren aktueller Schritt-`tool_call` terminal
    ist. Fenced per SKIP LOCKED + WAITING→RUNNING gegen einen zweiten Tick; der Handler
    läuft danach OHNE Sperre."""
    workflows = workflows if workflows is not None else WORKFLOWS
    ready = []
    with business_transaction(actor_id):
        candidates = list(
            WorkflowRun.objects.select_for_update(skip_locked=True)
            .filter(status="WAITING").order_by("updated_at")[:limit]
        )
        for wf in candidates:
            if wf.workflow_name not in workflows:
                # Handler (noch) nicht registriert → WAITING lassen (recoverable, statt
                # laufende Arbeit zu verlieren). Beim START ist ein fehlender Handler
                # dagegen ein Konfigurationsfehler (_run_handler → FAILED).
                continue
            call = ToolCall.objects.filter(workflow_run=wf, step_key=wf.current_step).first()
            if call is not None and call.status in _TERMINAL:
                wf.status = "RUNNING"
                wf.save(update_fields=["status"])
                ready.append(wf.id)
    resumed = []
    for wf_id in ready:
        wf = WorkflowRun.objects.get(id=wf_id)
        try:
            _run_handler(actor_id, wf, workflows)
            resumed.append(wf_id)
        except Exception:
            # Ein Fehler bei EINEM Workflow darf den Rest nicht abbrechen; der Lauf
            # bleibt RUNNING und fällt einem späteren Reaper zu (noch offen).
            logger.exception("engine: Wiederaufnahme fehlgeschlagen für workflow_run %s", wf_id)
    return resumed


def _run_handler(actor_id, wf, workflows):
    handler = workflows.get(wf.workflow_name)
    if handler is None:
        finish_workflow(actor_id, wf, "FAILED", error=f"Unbekannter Workflow '{wf.workflow_name}'.")
        return
    handler(actor_id, wf)
