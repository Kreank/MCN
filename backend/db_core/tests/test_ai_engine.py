"""Workflow-Engine (db_core.ai.engine, Stufe 5a) — einreihen/warten/wiederaufnehmen.

Beweist den generischen Kern mit einem trivialen In-Test-Workflow, unabhängig von
der realen Queue/LLM: start → WAITING mit tool_call → (Call terminal simuliert) →
resume_ready → nächster Schritt / Abschluss. Idempotenz und Statusübergänge gegen die
echten DB-Trigger.
"""
import uuid

from db_core.ai import engine
from db_core.ai import registry
from db_core.db_context import business_transaction
from db_core.models import ToolCall


def _echo_handler(actor_id, wf):
    """Trivial: Schritt 'asr' einreihen; bei Erfolg DONE, sonst FAILED."""
    if wf.current_step is None:
        engine.enqueue_tool(
            actor_id, wf, step_key="asr", tool_key=wf.context["tool_key"],
            input_ref={"file_id": wf.context["file_id"]}, deadline_seconds=3600,
        )
    elif wf.current_step == "asr":
        call = engine.tool_result(wf, "asr")
        if call.status == "SUCCEEDED":
            engine.finish_workflow(actor_id, wf, "DONE")
        else:
            engine.finish_workflow(actor_id, wf, "FAILED", error=f"asr {call.status}")


def _terminal(app_user, call, status):
    """Simuliert die Queue: den Call auf einen Terminalzustand bringen."""
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.save()
    with business_transaction(app_user.id):
        call.status = status
        call.save()


def _start(app_user):
    registry.register_tool(
        app_user.id, tool_key="asr-x", label="ASR", capability="ASR",
        invocation_mode="ASYNC", endpoint_url="https://handy.local/asr",
    )
    wf = engine.start_workflow(
        app_user.id, workflow_name="echo", workflow_version="v1",
        triggered_by_user_id=app_user.id,
        context={"tool_key": "asr-x", "file_id": str(uuid.uuid4())},
        workflows={"echo": _echo_handler},
    )
    return wf


def test_start_reiht_ein_und_wartet(app_user):
    wf = _start(app_user)
    wf.refresh_from_db()
    assert wf.status == "WAITING" and wf.current_step == "asr"
    call = engine.tool_result(wf, "asr")
    assert call is not None and call.status == "QUEUED"
    assert call.deadline_at is not None            # ASYNC → Deadline gesetzt


def test_resume_bei_erfolg_fuehrt_weiter(app_user):
    wf = _start(app_user)
    call = engine.tool_result(wf, "asr")
    _terminal(app_user, call, "SUCCEEDED")
    resumed = engine.resume_ready(app_user.id, workflows={"echo": _echo_handler})
    assert wf.id in resumed
    wf.refresh_from_db()
    assert wf.status == "DONE" and wf.finished_at is not None


def test_resume_wartet_solange_call_offen(app_user):
    wf = _start(app_user)
    # Call bleibt QUEUED → nicht wiederaufnahmefähig
    resumed = engine.resume_ready(app_user.id, workflows={"echo": _echo_handler})
    assert wf.id not in resumed
    wf.refresh_from_db()
    assert wf.status == "WAITING"


def test_resume_bei_tool_fehler_scheitert(app_user):
    wf = _start(app_user)
    call = engine.tool_result(wf, "asr")
    _terminal(app_user, call, "FAILED")
    engine.resume_ready(app_user.id, workflows={"echo": _echo_handler})
    wf.refresh_from_db()
    assert wf.status == "FAILED" and "asr" in (wf.error_message or "")


def test_unbekannter_workflow_scheitert(app_user):
    wf = engine.start_workflow(
        app_user.id, workflow_name="gibtsnicht", workflow_version="v1",
        triggered_by_user_id=app_user.id, workflows={},
    )
    wf.refresh_from_db()
    assert wf.status == "FAILED"


def test_resume_ueberspringt_unregistrierten_workflow(app_user):
    # L5: Handler beim Resume nicht registriert → WAITING lassen (recoverable),
    # NICHT als FAILED zerstören (sonst ginge laufende Arbeit bei vergessenem Import
    # verloren).
    wf = _start(app_user)
    call = engine.tool_result(wf, "asr")
    _terminal(app_user, call, "SUCCEEDED")
    resumed = engine.resume_ready(app_user.id, workflows={})   # leeres Register
    assert wf.id not in resumed
    wf.refresh_from_db()
    assert wf.status == "WAITING"
