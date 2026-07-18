"""KI-Vorschläge über die API (api/ki.py) — die Freigabe-Kachel end-to-end.

Liste/Detail/Annehmen/Ablehnen/Löschen über den HTTP-Client, samt Rechte-Tor
(Büro darf, Monteur nicht, anonym 401) und der Provenienz-Markierung
(`aus_untrusted_quelle`). Die Annahme materialisiert einen echten Bericht.
"""
import uuid

import pytest

from db_core.ai import engine, registry
from db_core.ai import workflow_sprachmemo as wsm
from db_core.ai.llm import FakeBackend
from db_core.db_context import business_transaction
from db_core.models import AiProposal, ContentItem, File, SiteReport
from db_core.services import auftrag as auftrag_service
from db_core.services import property as property_service


def _auftrag(actor, titel="Bad"):
    obj = property_service.create_property(
        actor.id, name="Obj", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    return auftrag_service.create_work_order(actor.id, property_id=obj.id, title=titel)


def _pending_proposal(actor, work_order_id, payload, monkeypatch):
    key = f"asr-{uuid.uuid4().hex[:8]}"
    registry.register_tool(
        actor.id, tool_key=key, label="ASR", capability="ASR",
        invocation_mode="ASYNC", endpoint_url="https://handy.local/asr",
    )
    with business_transaction(actor.id):
        audio = File.objects.create(
            id=uuid.uuid4(), storage_key=str(uuid.uuid4()), original_filename="memo.m4a",
            mime_type="audio/mp4", size_bytes=10, sha256="0" * 64, uploaded_by=actor,
        )
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
            extracted_text="Heizung entlüftet.", content_hash="h", is_untrusted=True,
            source_tool_call_id=call.id,
        )
        call.status = "SUCCEEDED"
        call.output_ref = {"content_item_id": str(ci.id)}
        call.save()
    fake = FakeBackend(responses=[payload], model_name="qwen2.5-7b", model_version="q4")
    monkeypatch.setattr(wsm, "get_backend", lambda *a, **k: fake)
    engine.resume_ready(actor.id)
    return AiProposal.objects.get(target_id=work_order_id)


_PAYLOAD = {
    "activity_text": "Heizung entlüftet",
    "lines": [{"line_type": "MATERIAL", "description": "Ventil",
               "quantity": 1, "unit": "Stk"}],
}


def test_liste_zeigt_offene_vorschlaege(app_user, admin_client, monkeypatch):
    wo = _auftrag(app_user)
    _pending_proposal(app_user, wo.id, _PAYLOAD, monkeypatch)
    resp = admin_client.get("/api/ai/proposals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    assert item["status"] == "PENDING"
    assert item["aus_untrusted_quelle"] is True        # Transkript = untrusted
    assert item["anzahl_positionen"] == 1
    assert item["auftrag_titel"] == "Bad"
    assert item["titel"] == "Heizung entlüftet"


def test_detail_liefert_entwurf(app_user, admin_client, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(app_user, wo.id, _PAYLOAD, monkeypatch)
    resp = admin_client.get(f"/api/ai/proposals/{prop.id}")
    assert resp.status_code == 200
    assert resp.json()["proposed_payload"]["lines"][0]["description"] == "Ventil"


def test_approve_ueber_api_materialisiert(app_user, admin_client, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(app_user, wo.id, _PAYLOAD, monkeypatch)
    resp = admin_client.post(f"/api/ai/proposals/{prop.id}/approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "APPROVED"
    assert body["result_type"] == "site_report"
    report = SiteReport.objects.get(id=body["result_id"])
    assert report.status == "ENTWURF" and report.work_order_id == wo.id


def test_reject_ueber_api(app_user, admin_client, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(app_user, wo.id, _PAYLOAD, monkeypatch)
    ok = admin_client.post(
        f"/api/ai/proposals/{prop.id}/reject",
        data={"reason": "unbrauchbar"}, content_type="application/json",
    )
    assert ok.status_code == 200 and ok.json()["status"] == "REJECTED"


def test_reject_ohne_grund_422(app_user, admin_client, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(app_user, wo.id, _PAYLOAD, monkeypatch)
    resp = admin_client.post(
        f"/api/ai/proposals/{prop.id}/reject",
        data={"reason": "   "}, content_type="application/json",
    )
    assert resp.status_code == 422


def test_delete_pending_verboten_nach_reject_erlaubt(app_user, admin_client, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(app_user, wo.id, _PAYLOAD, monkeypatch)
    # PENDING löschen weist der DB-Trigger ab (GoBD) → 422.
    verboten = admin_client.delete(f"/api/ai/proposals/{prop.id}")
    assert verboten.status_code == 422
    assert AiProposal.objects.filter(id=prop.id).exists()
    # Nach der Ablehnung ist das Löschen (DSGVO) erlaubt.
    admin_client.post(
        f"/api/ai/proposals/{prop.id}/reject",
        data={"reason": "weg"}, content_type="application/json",
    )
    erlaubt = admin_client.delete(f"/api/ai/proposals/{prop.id}")
    assert erlaubt.status_code == 200
    assert not AiProposal.objects.filter(id=prop.id).exists()


def test_monteur_darf_nicht(app_user, client_with_role, monkeypatch):
    wo = _auftrag(app_user)
    prop = _pending_proposal(app_user, wo.id, _PAYLOAD, monkeypatch)
    c = client_with_role("MONTEUR")
    assert c.get("/api/ai/proposals").status_code == 403
    assert c.post(f"/api/ai/proposals/{prop.id}/approve").status_code == 403


def test_anonym_401(anonymous_client):
    assert anonymous_client.get("/api/ai/proposals").status_code == 401
