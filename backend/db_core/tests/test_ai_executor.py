"""Executor (db_core.ai.executor) — Lauf-Protokollierung gegen echte DB.

Beweist: Ein Lauf wird angelegt und genau einmal abgeschlossen; Modell-Name/
-Version kommen aus dem tatsächlich benutzten Backend (Grundlage des Vergleichs);
`sources`/`tools_used` stehen beim Start fest; `resource_usage` summiert den
Verbrauch der Modellaufrufe; eine Ausnahme schließt den Lauf als FEHLER ab und
wird weitergereicht.
"""
import pytest

from db_core.ai.executor import ai_run
from db_core.ai.llm import FakeBackend, LlmMessage, LlmResponse
from db_core.models import AiRun


def _msgs():
    return [LlmMessage("user", "Fass zusammen.")]


def test_erfolgreicher_lauf_wird_protokolliert(app_user):
    resp1 = LlmResponse("a", None, "qwen2.5-7b", "q4", usage={"total_tokens": 10})
    resp2 = LlmResponse("b", None, "qwen2.5-7b", "q4", usage={"total_tokens": 5})
    backend = FakeBackend(responses=[resp1, resp2], model_name="qwen2.5-7b", model_version="q4")

    with ai_run(
        actor_id=app_user.id,
        backend=backend,
        workflow_name="sprachmemo_bericht",
        workflow_version="v1",
        sources=[{"type": "content_item", "n": 1}],
        tools_used=["asr_phone"],
    ) as handle:
        handle.generate(_msgs())
        handle.generate(_msgs())

    run = AiRun.objects.get(id=handle.id)
    assert run.result_status == "OK"
    assert run.finished_at is not None
    # Provenance: welches Modell hat entschieden?
    assert run.model_name == "qwen2.5-7b" and run.model_version == "q4"
    assert run.workflow_name == "sprachmemo_bericht"
    assert run.sources == [{"type": "content_item", "n": 1}]
    assert run.tools_used == ["asr_phone"]
    # Verbrauch über beide Aufrufe summiert
    assert run.resource_usage == {"total_tokens": 15}


def test_ausnahme_schliesst_lauf_als_fehler_und_reicht_weiter(app_user):
    with pytest.raises(ValueError):
        with ai_run(
            actor_id=app_user.id,
            backend=FakeBackend(),
            workflow_name="sprachmemo_bericht",
            workflow_version="v1",
        ) as handle:
            raise ValueError("Transkript leer")

    run = AiRun.objects.get(id=handle.id)
    assert run.result_status == "FEHLER"
    assert "Transkript leer" in run.error_message
    assert run.finished_at is not None


def test_lauf_ohne_quellen_und_werkzeuge_ist_erlaubt(app_user):
    with ai_run(
        actor_id=app_user.id,
        backend=FakeBackend(),
        workflow_name="wf",
        workflow_version="v1",
    ) as handle:
        pass

    run = AiRun.objects.get(id=handle.id)
    assert run.result_status == "OK"
    assert run.sources == [] and run.tools_used == []
    assert run.resource_usage == {}
