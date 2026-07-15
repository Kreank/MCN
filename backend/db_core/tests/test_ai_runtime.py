"""Runtime 4a (db_core.ai.runtime) — Ausführung eines tool_call gegen echte DB.

Beweist: Erfolg schreibt den Text ins löschbare content_item (mit der Eingabe-Datei
als Quelle, is_untrusted server-abgeleitet) und tool_call hält nur einen Verweis —
KEIN Text in tool_call. Dazu pending/RUNNING, transienter Retry mit Backoff-Fence,
permanenter FAILED, erschöpfte Versuche, Deadline→EXPIRED.
"""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from db_core.ai import runtime
from db_core.ai.tool_client import ToolError, ToolResult
from db_core.db_context import business_transaction
from db_core.models import ContentItem, File, Tool, ToolCall, WorkflowRun
from db_core.storage import StorageError


class FakeStorage:
    def __init__(self, data=b"audio-bytes"):
        self._data = data

    def get_object(self, key):
        return self._data


class FailingStorage:
    def get_object(self, key):
        raise StorageError("Objektspeicher weg")


class FakeClient:
    def __init__(self, *, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = []

    def dispatch(self, tool, envelope, *, bearer=None, timeout=None):
        self.calls.append(envelope)
        if self._error:
            raise self._error
        return self._result


def _file(app_user):
    with business_transaction(app_user.id):
        return File.objects.create(
            id=uuid.uuid4(), storage_key=str(uuid.uuid4()), original_filename="a.m4a",
            mime_type="audio/mp4", size_bytes=10, sha256="0" * 64, uploaded_by=app_user,
        )


def _running_call(app_user, *, capability="ASR", file_id=None, deadline=None, attempt=0):
    with business_transaction(app_user.id):
        wf = WorkflowRun.objects.create(
            id=uuid.uuid4(), workflow_name="w", workflow_version="1", triggered_by_user=app_user,
        )
        tool = Tool.objects.create(
            id=uuid.uuid4(), tool_key=f"asr-{uuid.uuid4()}", label="ASR", capability=capability,
            invocation_mode="ASYNC", endpoint_url="https://handy.local/asr",
            max_attempts=3, backoff_seconds=1,
        )
        call = ToolCall.objects.create(
            id=uuid.uuid4(), workflow_run=wf, tool=tool, capability=capability, step_key="asr",
            input_ref={"file_id": str(file_id)} if file_id else {},
        )
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.attempt = attempt
        call.deadline_at = deadline
        call.save()
    call.refresh_from_db()
    return call, tool


def test_is_untrusted_ableitung():
    assert runtime.is_untrusted_for("ASR") is True
    assert runtime.is_untrusted_for("LLM") is False


def test_erfolg_schreibt_content_item_kein_pii_im_tool_call(app_user):
    f = _file(app_user)
    call, _ = _running_call(app_user, file_id=f.id)
    client = FakeClient(result=ToolResult(
        status="ok", output={"text": "Heizung entlüftet."},
        metrics={"duration_ms": 100}, content_hash="h1",
    ))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())

    assert status == "SUCCEEDED"
    call.refresh_from_db()
    assert call.status == "SUCCEEDED" and call.output_hash == "h1"
    ci = ContentItem.objects.get(source_tool_call_id=call.id)
    assert ci.extracted_text == "Heizung entlüftet."
    assert ci.is_untrusted is True and ci.source_type == "PROTOKOLL"
    assert ci.file_id == f.id
    assert call.output_ref == {"content_item_id": str(ci.id)}
    assert "Heizung" not in str(call.output_ref)          # kein Text im unlöschbaren tool_call
    assert "file_b64" in client.calls[0]["input"]         # Eingabe-Bytes gepusht (passives Gerät)


def test_pending_bleibt_running(app_user):
    call, _ = _running_call(app_user, file_id=_file(app_user).id)
    client = FakeClient(result=ToolResult(status="pending", job_id="j-1"))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())
    assert status == "RUNNING"
    call.refresh_from_db()
    assert call.status == "RUNNING" and call.output_ref == {"job_id": "j-1"}
    assert call.leased_until is not None


def test_transienter_fehler_retry_mit_backoff(app_user):
    call, _ = _running_call(app_user, file_id=_file(app_user).id)
    client = FakeClient(error=ToolError("TIMEOUT", "Gerät antwortete nicht rechtzeitig."))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())
    assert status == "QUEUED"
    call.refresh_from_db()
    assert call.status == "QUEUED" and call.attempt == 1
    assert call.leased_until > timezone.now()             # Backoff-Fence
    assert call.error_code == "TIMEOUT"


def test_permanenter_fehler_failed(app_user):
    call, _ = _running_call(app_user, file_id=_file(app_user).id)
    client = FakeClient(error=ToolError("AUTH", "Gerät lehnte die Authentifizierung ab."))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())
    assert status == "FAILED"
    call.refresh_from_db()
    assert call.status == "FAILED" and call.error_code == "AUTH"


def test_versuche_erschoepft_failed(app_user):
    # attempt=2, max_attempts=3 → nächster Versuch (3) ist nicht < 3 → FAILED
    call, _ = _running_call(app_user, file_id=_file(app_user).id, attempt=2)
    client = FakeClient(error=ToolError("TIMEOUT", "x"))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())
    assert status == "FAILED"


def test_deadline_ueberschritten_expired_ohne_dispatch(app_user):
    call, _ = _running_call(
        app_user, file_id=_file(app_user).id, deadline=timezone.now() - timedelta(minutes=1)
    )
    client = FakeClient(error=ToolError("TIMEOUT", "x"))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())
    assert status == "EXPIRED"
    assert client.calls == []                             # gar nicht erst dispatcht
    call.refresh_from_db()
    assert call.status == "EXPIRED"


def test_fehlende_quelldatei_ist_permanent_failed(app_user):
    # input_ref zeigt auf eine nicht existierende File-Zeile → File.DoesNotExist
    call, _ = _running_call(app_user, file_id=uuid.uuid4())
    client = FakeClient(result=ToolResult(status="ok", output={"text": "x"}))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())
    assert status == "FAILED"                             # permanent, kein Retry
    call.refresh_from_db()
    assert call.error_code == "BAD_INPUT"
    assert client.calls == []                             # Aufbau scheitert vor dem Dispatch


def test_speicher_weg_ist_transient_retry(app_user):
    # Objektspeicher kurz nicht erreichbar → transient → erneut einreihen, NICHT failen
    call, _ = _running_call(app_user, file_id=_file(app_user).id)
    client = FakeClient(result=ToolResult(status="ok", output={"text": "x"}))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FailingStorage())
    assert status == "QUEUED"
    call.refresh_from_db()
    assert call.attempt == 1 and call.error_code == "UNREACHABLE"


def test_ok_ohne_text_failed_kein_leeres_content_item(app_user):
    call, _ = _running_call(app_user, file_id=_file(app_user).id)
    client = FakeClient(result=ToolResult(status="ok", output={}))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())
    assert status == "FAILED"
    assert not ContentItem.objects.filter(source_tool_call_id=call.id).exists()


def test_pending_ohne_job_id_wird_transient(app_user):
    call, _ = _running_call(app_user, file_id=_file(app_user).id)
    client = FakeClient(result=ToolResult(status="pending", job_id=None))
    status = runtime.run_tool_call(call.id, actor_id=app_user.id, client=client, storage=FakeStorage())
    assert status == "QUEUED"                             # kein hängendes RUNNING ohne job_id
    call.refresh_from_db()
    assert call.attempt == 1
