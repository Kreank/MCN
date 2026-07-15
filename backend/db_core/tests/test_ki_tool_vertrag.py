"""KI-Tool-Vertrag (Migration 0106) — Parität + Statusautomaten in der DB.

Beweist gegen die echte DB, dass die vier Entitäten deckungsgleich sind UND die
Tore als Trigger halten (nicht nur im Service):

* `tool_call`: nur erlaubte Übergänge, Terminalzustände final, Retry
  (RUNNING→QUEUED), attempt monoton, Identität/Eingabe eingefroren, Idempotenz
  UNIQUE(workflow_run, step_key).
* `workflow_run`: erlaubte Übergänge inkl. WAITING/Resume, Identität eingefroren,
  Kohärenz „Terminalzustand ⇔ finished_at".
* `tool`: Endpoint-Kohärenz (extern braucht Endpoint), tool_key/capability fix.
* `content_item`: data_class-Default, source_tool_call_id UNIQUE (kein zweites
  Transkript bei doppeltem Ergebnis).
"""
import uuid

import pytest
from django.db import Error, IntegrityError
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import AiRun, File, Tool, ToolCall, WorkflowRun


# --- Hilfen ----------------------------------------------------------------

def _wf(actor, *, name="sprachmemo_bericht"):
    with business_transaction(actor.id):
        wf = WorkflowRun.objects.create(
            id=uuid.uuid4(), workflow_name=name, workflow_version="v1",
            triggered_by_user=actor,
        )
    wf.refresh_from_db()
    return wf


def _tool(actor, *, key="asr-handy-1", capability="ASR", mode="ASYNC",
          endpoint="https://handy.local/asr"):
    with business_transaction(actor.id):
        t = Tool.objects.create(
            id=uuid.uuid4(), tool_key=key, label="ASR Handy", capability=capability,
            invocation_mode=mode, endpoint_url=endpoint,
        )
    t.refresh_from_db()
    return t


def _call(actor, wf, tool, *, step="asr"):
    with business_transaction(actor.id):
        c = ToolCall.objects.create(
            id=uuid.uuid4(), workflow_run=wf, tool=tool,
            capability=tool.capability, step_key=step,
        )
    c.refresh_from_db()
    return c


# --- Parität ---------------------------------------------------------------

def test_workflow_run_roundtrip(app_user):
    wf = _wf(app_user)
    assert wf.status == "QUEUED"          # db_default
    assert wf.context == {} and wf.current_step is None
    assert wf.finished_at is None
    assert wf.triggered_by_user_id == app_user.id


def test_tool_und_tool_call_roundtrip(app_user):
    wf = _wf(app_user)
    tool = _tool(app_user)
    call = _call(app_user, wf, tool)
    assert tool.data_boundary == "LOCAL_ONLY" and tool.status == "ACTIVE"
    assert tool.timeout_seconds == 120 and tool.max_attempts == 3
    assert call.status == "QUEUED" and call.attempt == 0
    assert call.is_untrusted is True      # server-abgeleitet, Default true
    assert call.capability == "ASR"
    assert call.input_ref == {} and call.metrics == {}


# --- tool_call: Statusautomat ----------------------------------------------

def test_tool_call_gueltiger_lebenszyklus(app_user):
    call = _call(app_user, _wf(app_user), _tool(app_user))
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.attempt = 1
        call.leased_until = timezone.now()
        call.save()
    with business_transaction(app_user.id):
        call.status = "SUCCEEDED"
        call.save()
    call.refresh_from_db()
    assert call.status == "SUCCEEDED"


def test_tool_call_terminal_ist_final(app_user):
    call = _call(app_user, _wf(app_user), _tool(app_user))
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.save()
    with business_transaction(app_user.id):
        call.status = "SUCCEEDED"
        call.save()
    call.refresh_from_db()
    with pytest.raises(Error):             # SUCCEEDED → irgendwas ist gesperrt
        with business_transaction(app_user.id):
            call.status = "QUEUED"
            call.save()


def test_tool_call_kein_sprung_ohne_running(app_user):
    call = _call(app_user, _wf(app_user), _tool(app_user))
    with pytest.raises(Error):             # QUEUED → SUCCEEDED ohne RUNNING
        with business_transaction(app_user.id):
            call.status = "SUCCEEDED"
            call.save()


def test_tool_call_retry_running_zu_queued(app_user):
    call = _call(app_user, _wf(app_user), _tool(app_user))
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.save()
    with business_transaction(app_user.id):   # transienter Fehler → erneut einreihen
        call.status = "QUEUED"
        call.attempt = 1
        call.save()
    call.refresh_from_db()
    assert call.status == "QUEUED" and call.attempt == 1


def test_tool_call_identitaet_eingefroren(app_user):
    call = _call(app_user, _wf(app_user), _tool(app_user))
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            call.step_key = "anders"
            call.save()


def test_tool_call_attempt_monoton(app_user):
    call = _call(app_user, _wf(app_user), _tool(app_user))
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.attempt = 5
        call.save()
    call.refresh_from_db()
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            call.attempt = 3
            call.save()


def test_tool_call_idempotenz_unique(app_user):
    wf = _wf(app_user)
    tool = _tool(app_user)
    _call(app_user, wf, tool, step="asr")
    with pytest.raises(IntegrityError):    # (workflow_run, step_key) ist eindeutig
        with business_transaction(app_user.id):
            ToolCall.objects.create(
                id=uuid.uuid4(), workflow_run=wf, tool=tool,
                capability="ASR", step_key="asr",
            )


# --- workflow_run: Statusautomat -------------------------------------------

def test_workflow_run_lebenszyklus_mit_warten(app_user):
    wf = _wf(app_user)
    for neu in ["RUNNING", "WAITING", "RUNNING"]:
        with business_transaction(app_user.id):
            wf.status = neu
            wf.save()
    with business_transaction(app_user.id):
        wf.status = "DONE"
        wf.finished_at = timezone.now()
        wf.save()
    wf.refresh_from_db()
    assert wf.status == "DONE" and wf.finished_at is not None
    with pytest.raises(Error):             # DONE ist terminal
        with business_transaction(app_user.id):
            wf.status = "RUNNING"
            wf.save()


def test_workflow_run_terminal_braucht_finished_at(app_user):
    wf = _wf(app_user)
    with business_transaction(app_user.id):
        wf.status = "RUNNING"
        wf.save()
    with pytest.raises(Error):             # DONE ohne finished_at verletzt die Kohärenz
        with business_transaction(app_user.id):
            wf.status = "DONE"
            wf.save()


def test_workflow_run_identitaet_eingefroren(app_user):
    wf = _wf(app_user)
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            wf.workflow_name = "anders"
            wf.save()


# --- tool / content_item ---------------------------------------------------

def test_tool_extern_braucht_endpoint(app_user):
    with pytest.raises(Error):             # SYNC ohne Endpoint verletzt die Kohärenz
        with business_transaction(app_user.id):
            Tool.objects.create(
                id=uuid.uuid4(), tool_key="ocr-x", label="OCR", capability="OCR",
                invocation_mode="SYNC", endpoint_url=None,
            )


def test_tool_intern_ohne_endpoint_ok(app_user):
    t = _tool(app_user, key="llm-lokal", capability="LLM", mode="INTERNAL",
              endpoint=None)
    assert t.endpoint_url is None and t.invocation_mode == "INTERNAL"


def test_tool_identitaet_eingefroren(app_user):
    tool = _tool(app_user)
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            tool.tool_key = "anders"
            tool.save()
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            tool.capability = "OCR"
            tool.save()


# --- CANCELLED, Startintegrität (INSERT), Verknüpfung ----------------------

def test_tool_call_cancelled_ist_erlaubt_und_terminal(app_user):
    call = _call(app_user, _wf(app_user), _tool(app_user))
    with business_transaction(app_user.id):
        call.status = "CANCELLED"          # QUEUED → CANCELLED erlaubt
        call.save()
    call.refresh_from_db()
    assert call.status == "CANCELLED"
    with pytest.raises(Error):             # CANCELLED ist terminal
        with business_transaction(app_user.id):
            call.status = "QUEUED"
            call.save()


def test_workflow_run_cancelled_braucht_finished_at(app_user):
    wf = _wf(app_user)
    with pytest.raises(Error):             # CANCELLED (terminal) ohne finished_at
        with business_transaction(app_user.id):
            wf.status = "CANCELLED"
            wf.save()
    wf.refresh_from_db()
    with business_transaction(app_user.id):
        wf.status = "CANCELLED"
        wf.finished_at = timezone.now()
        wf.save()
    wf.refresh_from_db()
    assert wf.status == "CANCELLED"


def test_workflow_run_ungueltiger_sprung(app_user):
    wf = _wf(app_user)
    with pytest.raises(Error):             # QUEUED → WAITING ist nicht erlaubt
        with business_transaction(app_user.id):
            wf.status = "WAITING"
            wf.save()


def test_ai_run_haengt_am_workflow_run(app_user):
    wf = _wf(app_user)
    with business_transaction(app_user.id):
        run = AiRun.objects.create(
            id=uuid.uuid4(), model_name="qwen2.5-7b", model_version="q4",
            workflow_name="sprachmemo_bericht", workflow_version="v1",
            prompt_version="v1", triggered_by_user=app_user, workflow_run=wf,
        )
    run.refresh_from_db()
    assert run.workflow_run_id == wf.id


def test_tool_call_insert_muss_queued_sein(app_user):
    wf = _wf(app_user)
    tool = _tool(app_user)
    with pytest.raises(Error):             # Direkt-INSERT in einen Terminalzustand
        with business_transaction(app_user.id):
            ToolCall.objects.create(
                id=uuid.uuid4(), workflow_run=wf, tool=tool,
                capability="ASR", step_key="asr", status="SUCCEEDED",
            )
    with pytest.raises(Error):             # gefälschter Versuchszähler beim INSERT
        with business_transaction(app_user.id):
            ToolCall.objects.create(
                id=uuid.uuid4(), workflow_run=wf, tool=tool,
                capability="ASR", step_key="asr2", attempt=5,
            )


def test_workflow_run_insert_muss_queued_sein(app_user):
    with pytest.raises(Error):             # Direkt-INSERT in DONE an der Maschine vorbei
        with business_transaction(app_user.id):
            WorkflowRun.objects.create(
                id=uuid.uuid4(), workflow_name="wf", workflow_version="v1",
                triggered_by_user=app_user, status="DONE",
                finished_at=timezone.now(),
            )


def test_content_item_data_class_und_source_unique(app_user):
    from db_core.models import ContentItem

    wf = _wf(app_user)
    tool = _tool(app_user)
    call = _call(app_user, wf, tool)
    with business_transaction(app_user.id):
        datei = File.objects.create(
            id=uuid.uuid4(), storage_key=str(uuid.uuid4()),
            original_filename="memo.m4a", mime_type="audio/mp4",
            size_bytes=42, sha256="0" * 64, uploaded_by=app_user,
        )
        item = ContentItem.objects.create(
            id=uuid.uuid4(), source_type="PROTOKOLL", file=datei,
            extracted_text="Heizung entlüftet.", content_hash="h1",
            source_tool_call=call,
        )
    item.refresh_from_db()
    assert item.data_class == "LOCAL_ONLY"           # Default
    assert item.source_tool_call_id == call.id

    with pytest.raises(IntegrityError):              # kein zweites Ergebnis am selben Call
        with business_transaction(app_user.id):
            zweite = File.objects.create(
                id=uuid.uuid4(), storage_key=str(uuid.uuid4()),
                original_filename="memo2.m4a", mime_type="audio/mp4",
                size_bytes=7, sha256="1" * 64, uploaded_by=app_user,
            )
            ContentItem.objects.create(
                id=uuid.uuid4(), source_type="PROTOKOLL", file=zweite,
                extracted_text="Doppelt.", content_hash="h2",
                source_tool_call=call,
            )
