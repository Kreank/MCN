"""ai.ai_proposal annehmen/ablehnen/löschen (db_core.ai.proposal, Slice A).

Der Schluss der KI-Kette: Ein PENDING-Vorschlag wird durch einen Menschen
angenommen und **über die Fach-API** zu einem echten `workflow.site_report`
(ENTWURF) materialisiert — durch dieselben Tore wie die manuelle Anlage. Geprüft
wird scharf, dass die Annahme

* einen echten, preisfreien Bericht im ENTWURF erzeugt (kein KI-Sonderweg),
* **idempotent/nebenläufigkeitssicher** ist (kein zweiter Bericht),
* bei Ablauf, Ablehnung oder unbekanntem Typ **nichts** materialisiert (der
  Vorschlag bleibt PENDING, es entsteht kein halber Bericht),
* eine unvollständige Position nicht mit einer erfundenen Menge durchdrückt,
  sondern als Textnotiz erhält (nichts erfinden, nichts verlieren).
"""
import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from db_core.ai import engine, registry
from db_core.ai import proposal as proposal_service
from db_core.ai import workflow_sprachmemo as wsm
from db_core.ai.llm import FakeBackend
from db_core.db_context import business_transaction
from db_core.models import AiProposal, ContentItem, File, SiteReport, SiteReportLine
from db_core.services import auftrag as auftrag_service
from db_core.services import property as property_service


def _auftrag(actor, titel="Bad"):
    obj = property_service.create_property(
        actor.id, name="Obj", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    return auftrag_service.create_work_order(actor.id, property_id=obj.id, title=titel)


def _file(actor):
    with business_transaction(actor.id):
        return File.objects.create(
            id=uuid.uuid4(), storage_key=str(uuid.uuid4()), original_filename="memo.m4a",
            mime_type="audio/mp4", size_bytes=10, sha256="0" * 64, uploaded_by=actor,
        )


def _pending_proposal(actor, work_order_id, payload, monkeypatch,
                      transkript="Heizung entlüftet."):
    """Erzeugt einen echten PENDING-Vorschlag am Auftrag über den v1-Workflow.

    Registriert ein ASR-Werkzeug, simuliert dessen Transkript (untrusted) und lässt
    das gefakte LLM `payload` als Entwurf liefern — derselbe Pfad wie im Betrieb,
    nur ohne echte Geräte."""
    key = f"asr-{uuid.uuid4().hex[:8]}"
    registry.register_tool(
        actor.id, tool_key=key, label="ASR", capability="ASR",
        invocation_mode="ASYNC", endpoint_url="https://handy.local/asr",
    )
    audio = _file(actor)
    wf = wsm.start_sprachmemo(
        actor.id, work_order_id=work_order_id, audio_file_id=audio.id, asr_tool_key=key,
    )
    call = engine.tool_result(wf, "asr")
    with business_transaction(actor.id):
        call.status = "RUNNING"
        call.save()
    with business_transaction(actor.id):
        ci = ContentItem.objects.create(
            id=uuid.uuid4(), source_type="PROTOKOLL", file_id=audio.id,
            extracted_text=transkript, content_hash="h", is_untrusted=True,
            source_tool_call_id=call.id,
        )
        call.status = "SUCCEEDED"
        call.output_ref = {"content_item_id": str(ci.id)}
        call.save()
    fake = FakeBackend(responses=[payload], model_name="qwen2.5-7b", model_version="q4")
    monkeypatch.setattr(wsm, "get_backend", lambda *a, **k: fake)
    engine.resume_ready(actor.id)
    return AiProposal.objects.get(target_id=work_order_id)


def test_approve_materialisiert_preisfreien_bericht(app_user, monkeypatch):
    wo = _auftrag(app_user)
    payload = {
        "activity_text": "Heizung entlüftet, Ventil getauscht",
        "lines": [
            {"line_type": "MATERIAL", "description": "Thermostatventil DN20",
             "quantity": 1, "unit": "Stk"},
            {"line_type": "ARBEITSZEIT", "description": "Montage",
             "quantity": 1.5, "unit": "h"},
        ],
    }
    prop = _pending_proposal(app_user, wo.id, payload, monkeypatch)
    assert prop.status == "PENDING"

    prop2, result = proposal_service.approve(app_user.id, proposal_id=prop.id)
    assert prop2.status == "APPROVED"
    assert prop2.approved_by_user_id == app_user.id
    assert prop2.approved_at is not None            # vom DB-Trigger gesetzt
    assert result["result_type"] == "site_report"

    report = SiteReport.objects.get(id=result["result_id"])
    assert report.status == "ENTWURF"               # der Mensch korrigiert + signiert
    assert report.work_order_id == wo.id
    assert report.activity_text == "Heizung entlüftet, Ventil getauscht"

    lines = list(
        SiteReportLine.objects.filter(site_report_id=report.id).order_by("position_number")
    )
    assert [l.description for l in lines] == ["Thermostatventil DN20", "Montage"]
    assert lines[0].quantity == Decimal("1.000") and lines[0].unit == "Stk"
    assert lines[1].quantity == Decimal("1.500") and lines[1].unit == "h"


def test_approve_ist_idempotent_und_serialisiert(app_user, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(
        app_user, wo.id, {"activity_text": "x", "lines": []}, monkeypatch
    )
    proposal_service.approve(app_user.id, proposal_id=prop.id)
    # Zweite Annahme desselben (nun APPROVED) Vorschlags: Fachfehler, kein
    # zweiter Bericht.
    with pytest.raises(ValueError):
        proposal_service.approve(app_user.id, proposal_id=prop.id)
    assert SiteReport.objects.filter(work_order_id=wo.id).count() == 1


def test_approve_abgelaufen_materialisiert_nichts(app_user, monkeypatch):
    monkeypatch.setattr(wsm, "PROPOSAL_TTL_HOURS", -1)   # sofort abgelaufen
    wo = _auftrag(app_user)
    prop = _pending_proposal(
        app_user, wo.id, {"activity_text": "x", "lines": []}, monkeypatch
    )
    assert prop.expires_at < timezone.now()
    with pytest.raises(ValueError):
        proposal_service.approve(app_user.id, proposal_id=prop.id)
    prop.refresh_from_db()
    assert prop.status == "PENDING"
    assert not SiteReport.objects.filter(work_order_id=wo.id).exists()


def test_reject_dann_approve_scheitert(app_user, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(
        app_user, wo.id, {"activity_text": "x", "lines": []}, monkeypatch
    )
    proposal_service.reject(app_user.id, proposal_id=prop.id, reason="unbrauchbar")
    with pytest.raises(ValueError):
        proposal_service.approve(app_user.id, proposal_id=prop.id)
    assert not SiteReport.objects.filter(work_order_id=wo.id).exists()


def test_unvollstaendige_position_wird_textnotiz(app_user, monkeypatch):
    # Eine MATERIAL-Zeile ohne Menge/Einheit darf NICHT mit erfundener Menge
    # durchgedrückt werden — sie wird zur TEXT-Notiz, das Teilwissen bleibt erhalten.
    wo = _auftrag(app_user)
    payload = {
        "activity_text": "Dichtung erneuert",
        "lines": [{"line_type": "MATERIAL", "description": "Dichtung",
                   "quantity": None, "unit": None}],
    }
    prop = _pending_proposal(app_user, wo.id, payload, monkeypatch)
    _, result = proposal_service.approve(app_user.id, proposal_id=prop.id)

    lines = list(SiteReportLine.objects.filter(site_report_id=result["result_id"]))
    assert len(lines) == 1
    assert lines[0].line_type == "TEXT"
    assert lines[0].description == "Dichtung"
    assert lines[0].quantity is None
    assert lines[0].note                            # Teilwissen (Art …) gerettet


def test_approve_unbekannter_typ_laesst_pending(app_user, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(
        app_user, wo.id, {"activity_text": "x", "lines": []}, monkeypatch
    )
    monkeypatch.setattr(proposal_service, "MATERIALISIERER", {})
    with pytest.raises(ValueError):
        proposal_service.approve(app_user.id, proposal_id=prop.id)
    prop.refresh_from_db()
    assert prop.status == "PENDING"
    assert not SiteReport.objects.filter(work_order_id=wo.id).exists()


def test_reject_braucht_grund(app_user, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(
        app_user, wo.id, {"activity_text": "x", "lines": []}, monkeypatch
    )
    with pytest.raises(ValueError):
        proposal_service.reject(app_user.id, proposal_id=prop.id, reason="  ")
    prop.refresh_from_db()
    assert prop.status == "PENDING"


def test_expire_setzt_abgelaufene_pending_auf_expired(app_user, monkeypatch):
    monkeypatch.setattr(wsm, "PROPOSAL_TTL_HOURS", -1)          # sofort abgelaufen
    wo = _auftrag(app_user)
    prop = _pending_proposal(
        app_user, wo.id, {"activity_text": "x", "lines": []}, monkeypatch
    )
    assert prop.status == "PENDING" and prop.expires_at < timezone.now()
    expired = proposal_service.expire_stale_proposals(app_user.id)
    assert prop.id in expired
    prop.refresh_from_db()
    assert prop.status == "EXPIRED"


def test_expire_laesst_gueltige_pending_in_ruhe(app_user, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(                                    # TTL 72 h (Default)
        app_user, wo.id, {"activity_text": "x", "lines": []}, monkeypatch
    )
    assert proposal_service.expire_stale_proposals(app_user.id) == []
    prop.refresh_from_db()
    assert prop.status == "PENDING"
