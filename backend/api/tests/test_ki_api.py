"""KI-API (api/ki.py) — Sprachmemo-Upload startet den Bericht-Workflow.

Prüft den Endpunkt end-to-end über den HTTP-Client: Audio hochladen → workflow_run
(WAITING) + eingereihter ASR-tool_call; plus die Fehlerfälle (unbekannter Auftrag,
unbekanntes Werkzeug).
"""
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from db_core.ai import registry
from db_core.models import ToolCall, WorkflowRun
from db_core.services import auftrag as auftrag_service
from db_core.services import property as property_service


class _FakeStorage:
    """Der Objektspeicher ist für diesen Test Nebensache — nur das Ablegen zählt."""

    def put_object(self, key, data, content_type="application/octet-stream"):
        return None

    def get_object(self, key):
        raise KeyError(key)

    def remove_object(self, key):
        pass

    def ensure_bucket(self):
        pass


@pytest.fixture
def fake_storage(monkeypatch):
    from db_core import storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage", lambda: _FakeStorage())


def _obj_und_auftrag(app_user, name):
    prop = property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    return auftrag_service.create_work_order(app_user.id, property_id=prop.id, title="Auftrag")


def _post(client, work_order_id, tool_key):
    return client.post(
        "/api/ai/sprachmemo",
        data={
            "datei": SimpleUploadedFile("memo.m4a", b"fake-audio", content_type="audio/mp4"),
            "work_order_id": str(work_order_id),
            "asr_tool_key": tool_key,
        },
    )


def test_sprachmemo_upload_startet_workflow(app_user, admin_client, fake_storage):
    wo = _obj_und_auftrag(app_user, "Obj")
    registry.register_tool(
        app_user.id, tool_key="asr-1", label="ASR", capability="ASR",
        invocation_mode="ASYNC", endpoint_url="https://handy.local/asr",
    )
    resp = _post(admin_client, wo.id, "asr-1")
    assert resp.status_code == 201
    wf = WorkflowRun.objects.get(id=resp.json()["workflow_run_id"])
    assert wf.status == "WAITING"
    call = ToolCall.objects.get(workflow_run=wf, step_key="asr")
    assert call.capability == "ASR" and call.status == "QUEUED"
    assert call.input_ref.get("file_id")          # Audio referenziert (nicht inline)


def test_sprachmemo_upload_unbekanntes_tool_422(app_user, admin_client):
    wo = _obj_und_auftrag(app_user, "Obj2")
    resp = _post(admin_client, wo.id, "gibtsnicht")
    assert resp.status_code == 422


def test_sprachmemo_upload_unbekannter_auftrag_404(admin_client):
    resp = _post(admin_client, uuid.uuid4(), "asr-1")
    assert resp.status_code == 404
