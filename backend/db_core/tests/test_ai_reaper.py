"""workflow_run-Reaper (db_core.ai.engine.reap_stale_workflows).

Schließt die Lücke aus `resume_ready`: ein Workflow, dessen Handler beim Resume
abstürzt (oder dessen Worker gekillt wird), bleibt für immer RUNNING. Der Reaper
setzt ihn nach Ablauf einer Schwelle auf FAILED — und lässt frische RUNNING-Läufe
sowie WAITING-Läufe unangetastet.
"""
import uuid

from db_core.ai import engine
from db_core.db_context import business_transaction
from db_core.models import WorkflowRun


def _wf(actor, status, *, current_step=None):
    """Erzeugt einen Workflow und fährt ihn (über erlaubte Übergänge) in `status`.

    Anlage ist immer QUEUED (Trigger). QUEUED→RUNNING→WAITING sind die legalen
    Wege dorthin."""
    with business_transaction(actor.id):
        wf = WorkflowRun.objects.create(
            id=uuid.uuid4(), workflow_name="sprachmemo_bericht",
            workflow_version="v1", triggered_by_user_id=actor.id, context={},
        )
    if status == "QUEUED":
        return wf
    with business_transaction(actor.id):
        wf.status = "RUNNING"
        wf.save(update_fields=["status"])
    if status == "RUNNING":
        return wf
    if status == "WAITING":
        with business_transaction(actor.id):
            wf.status = "WAITING"
            wf.current_step = current_step or "asr"
            wf.save(update_fields=["status", "current_step"])
        return wf
    raise ValueError(status)


def test_reaper_terminalisiert_haengenden_running(app_user):
    wf = _wf(app_user, "RUNNING")
    # Frisch RUNNING (updated_at = jetzt): eine großzügige Schwelle rührt ihn nicht an.
    assert engine.reap_stale_workflows(app_user.id, older_than_seconds=100000) == []
    wf.refresh_from_db()
    assert wf.status == "RUNNING"
    # Schwelle in die Zukunft ziehen ⇒ „seit jeher hängend" ⇒ abräumen.
    reaped = engine.reap_stale_workflows(app_user.id, older_than_seconds=-5)
    assert wf.id in reaped
    wf.refresh_from_db()
    assert wf.status == "FAILED"
    assert wf.finished_at is not None
    assert "Reaper" in (wf.error_message or "")


def test_reaper_ruehrt_waiting_nicht_an(app_user):
    wf = _wf(app_user, "WAITING")
    assert engine.reap_stale_workflows(app_user.id, older_than_seconds=-5) == []
    wf.refresh_from_db()
    assert wf.status == "WAITING"


def test_reaper_ruehrt_terminale_nicht_an(app_user):
    wf = _wf(app_user, "RUNNING")
    engine.finish_workflow(app_user.id, wf, "DONE")   # setzt finished_at (CHECK)
    assert engine.reap_stale_workflows(app_user.id, older_than_seconds=-5) == []
    wf.refresh_from_db()
    assert wf.status == "DONE"
