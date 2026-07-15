"""Queue-Mechanik (db_core.ai.runtime, Stufe 4b) — Claim/Reap/Poll/Tick.

Die reine Logik gegen echte DB (Übergänge, Deadline, Backoff-Fence,
Reap-überspringt-pending, Poll, Tick end-to-end). Die eigentliche Nebenläufigkeit
(SKIP LOCKED gegen Doppel-Claim) beweist der Zwei-Sitzungs-Test
`db/tests/nebenlaeufigkeitstest_tool_queue.sh` (Djangos transaction=True scheitert
hier am No-Truncate-Schutz der Tabellen).
"""
import uuid
from datetime import timedelta

from django.utils import timezone

from db_core.ai import runtime
from db_core.ai.tool_client import ToolError, ToolResult
from db_core.db_context import business_transaction
from db_core.models import ContentItem, File, Tool, ToolCall, WorkflowRun


class FakeStorage:
    def get_object(self, key):
        return b"audio-bytes"


class FakeClient:
    def __init__(self, *, result=None, error=None):
        self._result = result
        self._error = error

    def dispatch(self, tool, envelope, *, bearer=None, timeout=None):
        if self._error:
            raise self._error
        return self._result

    def poll(self, tool, job_id, *, bearer=None, timeout=None):
        if self._error:
            raise self._error
        return self._result


def _file(app_user):
    with business_transaction(app_user.id):
        return File.objects.create(
            id=uuid.uuid4(), storage_key=str(uuid.uuid4()), original_filename="a.m4a",
            mime_type="audio/mp4", size_bytes=10, sha256="0" * 64, uploaded_by=app_user,
        )


def _queued_call(app_user, *, file_id=None, deadline=None, leased_until=None):
    with business_transaction(app_user.id):
        wf = WorkflowRun.objects.create(
            id=uuid.uuid4(), workflow_name="w", workflow_version="1", triggered_by_user=app_user,
        )
        tool = Tool.objects.create(
            id=uuid.uuid4(), tool_key=f"asr-{uuid.uuid4()}", label="ASR", capability="ASR",
            invocation_mode="ASYNC", endpoint_url="https://handy.local/asr",
            max_attempts=3, backoff_seconds=1,
        )
        call = ToolCall.objects.create(
            id=uuid.uuid4(), workflow_run=wf, tool=tool, capability="ASR", step_key="asr",
            input_ref={"file_id": str(file_id)} if file_id else {},
        )
    if deadline or leased_until:
        with business_transaction(app_user.id):
            call.deadline_at = deadline
            call.leased_until = leased_until
            call.save()
    call.refresh_from_db()
    return call, tool


def _make_running(app_user, call, *, leased_until, job_id=None):
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.leased_until = leased_until
        if job_id:
            call.output_ref = {"job_id": job_id}
        call.save()
    call.refresh_from_db()
    return call


# --- Claim -----------------------------------------------------------------

def test_claim_batch_claimt_und_least(app_user):
    c1, _ = _queued_call(app_user, file_id=_file(app_user).id)
    c2, _ = _queued_call(app_user, file_id=_file(app_user).id)
    claimed = set(runtime.claim_batch(app_user.id, limit=10))
    assert {c1.id, c2.id} <= claimed
    c1.refresh_from_db()
    assert c1.status == "RUNNING" and c1.leased_until is not None


def test_claim_ueberfaellige_deadline_expired(app_user):
    c, _ = _queued_call(app_user, file_id=_file(app_user).id, deadline=timezone.now() - timedelta(minutes=1))
    claimed = runtime.claim_batch(app_user.id, limit=10)
    assert c.id not in claimed
    c.refresh_from_db()
    assert c.status == "EXPIRED"


def test_claim_ueberspringt_backoff_fence(app_user):
    c, _ = _queued_call(app_user, file_id=_file(app_user).id, leased_until=timezone.now() + timedelta(minutes=5))
    claimed = runtime.claim_batch(app_user.id, limit=10)
    assert c.id not in claimed
    c.refresh_from_db()
    assert c.status == "QUEUED"


# --- Reap ------------------------------------------------------------------

def test_reap_stale_ohne_job_retry(app_user):
    c, _ = _queued_call(app_user, file_id=_file(app_user).id)
    _make_running(app_user, c, leased_until=timezone.now() - timedelta(minutes=1))
    reaped = runtime.reap_stale(app_user.id)
    assert c.id in reaped
    c.refresh_from_db()
    assert c.status == "QUEUED" and c.attempt == 1


def test_reap_ueberspringt_pending(app_user):
    c, _ = _queued_call(app_user, file_id=_file(app_user).id)
    _make_running(app_user, c, leased_until=timezone.now() - timedelta(minutes=1), job_id="j-1")
    reaped = runtime.reap_stale(app_user.id)
    assert c.id not in reaped
    c.refresh_from_db()
    assert c.status == "RUNNING"           # pending wird gepollt, nicht gereapt


# --- Poll ------------------------------------------------------------------

def test_poll_pending_erfolg(app_user):
    f = _file(app_user)
    c, _ = _queued_call(app_user, file_id=f.id)
    _make_running(app_user, c, leased_until=timezone.now() - timedelta(seconds=1), job_id="j-1")
    client = FakeClient(result=ToolResult(status="ok", output={"text": "fertig"}, content_hash="h"))
    polled = runtime.poll_pending(app_user.id, client=client, storage=FakeStorage())
    assert c.id in polled
    c.refresh_from_db()
    assert c.status == "SUCCEEDED"
    assert ContentItem.objects.filter(source_tool_call_id=c.id).exists()


def test_poll_noch_pending_bleibt_running(app_user):
    c, _ = _queued_call(app_user, file_id=_file(app_user).id)
    _make_running(app_user, c, leased_until=timezone.now() - timedelta(seconds=1), job_id="j-1")
    client = FakeClient(result=ToolResult(status="pending", job_id="j-1"))
    runtime.poll_pending(app_user.id, client=client)
    c.refresh_from_db()
    assert c.status == "RUNNING"


def test_poll_ueberlebt_einzelfehler(app_user):
    # Ein unerwarteter Fehler bei EINEM Poll darf den Durchlauf nicht abbrechen (F2).
    c, _ = _queued_call(app_user, file_id=_file(app_user).id)
    _make_running(app_user, c, leased_until=timezone.now() - timedelta(seconds=1), job_id="j-1")

    class Boom:
        def poll(self, tool, job_id, *, bearer=None, timeout=None):
            raise RuntimeError("kaputt")

    runtime.poll_pending(app_user.id, client=Boom())   # darf NICHT propagieren
    c.refresh_from_db()
    assert c.status == "RUNNING"


# --- Tick (end-to-end) -----------------------------------------------------

def test_tick_dispatcht_und_verbucht(app_user):
    c, _ = _queued_call(app_user, file_id=_file(app_user).id)
    client = FakeClient(result=ToolResult(status="ok", output={"text": "Transkript"}, content_hash="h"))
    summary = runtime.tick(app_user.id, client=client, storage=FakeStorage(), claim_limit=10)
    assert summary["dispatched"] == 1
    c.refresh_from_db()
    assert c.status == "SUCCEEDED"
    assert ContentItem.objects.filter(source_tool_call_id=c.id).exists()
