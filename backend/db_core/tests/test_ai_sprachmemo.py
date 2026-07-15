"""v1-Workflow Sprachmemo → Bericht (db_core.ai.workflow_sprachmemo, Stufe 5b).

Der durchgängige KI-Pfad, gegen echte DB, mit gefaktem LLM + simulierter ASR:
start → WAITING(asr) → (Transkript da) → resume → LLM entwirft → ai_proposal
(preisfrei, PENDING, am Workflow verlinkt). Und der Fehlerfall (ASR gescheitert →
kein Vorschlag).
"""
import uuid

from db_core.ai import engine, registry
from db_core.ai import workflow_sprachmemo as wsm
from db_core.ai.llm import FakeBackend, LlmError
from db_core.db_context import business_transaction
from db_core.models import AiProposal, ContentItem, File


def _file(app_user):
    with business_transaction(app_user.id):
        return File.objects.create(
            id=uuid.uuid4(), storage_key=str(uuid.uuid4()), original_filename="memo.m4a",
            mime_type="audio/mp4", size_bytes=10, sha256="0" * 64, uploaded_by=app_user,
        )


def _asr_success(app_user, call, audio, transkript):
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.save()
    with business_transaction(app_user.id):
        ci = ContentItem.objects.create(
            id=uuid.uuid4(), source_type="PROTOKOLL", file_id=audio.id,
            extracted_text=transkript, content_hash="h", is_untrusted=True,
            source_tool_call_id=call.id,
        )
        call.status = "SUCCEEDED"
        call.output_ref = {"content_item_id": str(ci.id)}
        call.save()


def _start(app_user):
    registry.register_tool(
        app_user.id, tool_key="asr-1", label="ASR", capability="ASR",
        invocation_mode="ASYNC", endpoint_url="https://handy.local/asr",
    )
    audio = _file(app_user)
    work_order_id = uuid.uuid4()
    wf = wsm.start_sprachmemo(
        app_user.id, work_order_id=work_order_id, audio_file_id=audio.id, asr_tool_key="asr-1",
    )
    return wf, audio, work_order_id


def test_sprachmemo_erzeugt_preisfreien_bericht_vorschlag(app_user, monkeypatch):
    wf, audio, work_order_id = _start(app_user)
    wf.refresh_from_db()
    assert wf.status == "WAITING" and wf.current_step == "asr"

    call = engine.tool_result(wf, "asr")
    _asr_success(app_user, call, audio, "Heizung entlüftet, Thermostatventil DN20 getauscht.")

    fake = FakeBackend(responses=[{
        "activity_text": "Heizung entlüftet, Ventil getauscht",
        "lines": [{"line_type": "MATERIAL", "description": "Thermostatventil DN20",
                   "quantity": 1, "unit": "Stk"}],
    }], model_name="qwen2.5-7b", model_version="q4")
    monkeypatch.setattr(wsm, "get_backend", lambda *a, **k: fake)

    resumed = engine.resume_ready(app_user.id)
    assert wf.id in resumed
    wf.refresh_from_db()
    assert wf.status == "DONE"

    prop = AiProposal.objects.get(target_id=work_order_id)
    assert prop.proposal_type == "SITE_REPORT_ENTWURF" and prop.status == "PENDING"
    line = prop.proposed_payload["lines"][0]
    assert line["description"] == "Thermostatventil DN20"
    # Preisfrei — keine Geldfelder im Vorschlag
    assert "unit_price" not in line and "net_amount" not in line
    # Provenance: Lauf am Workflow verlinkt, Modell protokolliert
    assert prop.ai_run.workflow_run_id == wf.id
    assert prop.ai_run.model_name == "qwen2.5-7b"


def test_sprachmemo_bei_asr_fehler_kein_vorschlag(app_user):
    wf, audio, work_order_id = _start(app_user)
    call = engine.tool_result(wf, "asr")
    with business_transaction(app_user.id):
        call.status = "RUNNING"
        call.save()
    with business_transaction(app_user.id):
        call.status = "FAILED"
        call.error_code = "AUTH"
        call.save()

    engine.resume_ready(app_user.id)
    wf.refresh_from_db()
    assert wf.status == "FAILED"
    assert not AiProposal.objects.filter(target_id=work_order_id).exists()


class _BoomBackend:
    model_name = "x"
    model_version = "0"

    def generate(self, *args, **kwargs):
        raise LlmError("Modell nicht erreichbar")


def test_sprachmemo_llm_fehler_terminalisiert_nicht_ewig_running(app_user, monkeypatch):
    # M1: ein Entwurf-/Modell-Fehler beim Resume darf den Lauf nicht ewig RUNNING lassen.
    wf, audio, work_order_id = _start(app_user)
    call = engine.tool_result(wf, "asr")
    _asr_success(app_user, call, audio, "Heizung entlüftet.")
    monkeypatch.setattr(wsm, "get_backend", lambda *a, **k: _BoomBackend())

    engine.resume_ready(app_user.id)
    wf.refresh_from_db()
    assert wf.status == "FAILED"
    assert not AiProposal.objects.filter(target_id=work_order_id).exists()


def test_sprachmemo_ohne_verwertbaren_entwurf_scheitert(app_user, monkeypatch):
    # L4: liefert das LLM kein Schema-JSON (data=None), wird KEIN Müll-Vorschlag abgelegt.
    wf, audio, work_order_id = _start(app_user)
    call = engine.tool_result(wf, "asr")
    _asr_success(app_user, call, audio, "Heizung entlüftet.")
    fake = FakeBackend(responses=["kein gültiges json"], model_name="x", model_version="0")
    monkeypatch.setattr(wsm, "get_backend", lambda *a, **k: fake)

    engine.resume_ready(app_user.id)
    wf.refresh_from_db()
    assert wf.status == "FAILED"
    assert not AiProposal.objects.filter(target_id=work_order_id).exists()
