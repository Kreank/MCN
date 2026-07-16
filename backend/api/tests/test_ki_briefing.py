"""API: GET /api/ai/briefing — Leitstand-Tagesbriefing.

Prüft das Rechte-Tor (Büro darf, Monteur 403, anonym 401) und die Antwortform.
Der Modell-Aufruf ist in Dev/Test ohne Profil ein Echo → deterministisches
Fallback; das reicht, um Endpunkt, Serialisierung und Cache-Umgehung (refresh)
zu prüfen, ohne ein laufendes LLM.
"""
import pytest

from db_core.ai import leitstand_briefing as lb


@pytest.fixture(autouse=True)
def _cache_leeren():
    lb.cache_leeren()
    yield
    lb.cache_leeren()


def test_briefing_buero_liefert_briefing(admin_client):
    r = admin_client.get("/api/ai/briefing")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"schlagzeile", "punkte", "stand", "ki_generiert", "modell"}
    assert isinstance(body["punkte"], list)
    # Leere Test-DB → nichts steht an → deterministisches Fallback.
    assert body["ki_generiert"] is False
    assert "Ruhiger Tag" in body["schlagzeile"]


def test_briefing_refresh_umgeht_cache(admin_client):
    erst = admin_client.get("/api/ai/briefing").json()
    gecacht = admin_client.get("/api/ai/briefing").json()
    assert gecacht["stand"] == erst["stand"]                 # zweiter Aufruf aus dem Cache
    frisch = admin_client.get("/api/ai/briefing?refresh=1").json()
    assert frisch["stand"] >= erst["stand"]                  # refresh erzeugt neu


def test_briefing_monteur_verboten(client_with_role):
    # Monteur trägt workflow-Scope EIGENE (oder kein Recht) → require() = 403.
    c = client_with_role("MONTEUR")
    r = c.get("/api/ai/briefing")
    assert r.status_code == 403


def test_briefing_anonym_nicht_erlaubt(anonymous_client):
    r = anonymous_client.get("/api/ai/briefing")
    assert r.status_code in (401, 403)
