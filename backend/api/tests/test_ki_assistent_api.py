"""KI Slice 5 — die Assistent-API (`/api/ai/conversations…`).

Prüft Wiring, Auth, Eigentum und Schema-Form end-to-end über den Django-Test-Client:

* Frage ohne `conversation_id` beginnt ein Gespräch und liefert Frage+Antwort-Turn.
* Fortsetzung mit `conversation_id` zählt die Sequenz weiter.
* GET listet/liest nur EIGENE Gespräche; fremde → 404 (keine Existenzaussage).
* DELETE entfernt das eigene Gespräch; fremdes → 404.
* Anonym → 401/403.
* Happy Path mit gepatchtem Modell: die Antwort trägt die zitierte Quelle.

Ohne konfiguriertes Profil liefert `get_backend()` ein Echo-FakeBackend → der
Endpunkt läuft dann über den deterministischen Fallback (valider End-to-End-Pfad).
"""
import json
import uuid

import pytest
from django.test import Client

from api.tests.conftest import make_role_user
from db_core.ai.llm import FakeBackend
from db_core.services import property as property_service

FRAGE_URL = "/api/ai/conversations/frage"


def _admin():
    """Eingeloggter ADMINISTRATION-Client + zugehöriger app_user."""
    user, app_user = make_role_user("ADMINISTRATION")
    client = Client()
    client.force_login(user)
    return client, app_user


def _post_frage(client, frage, conversation_id=None):
    body = {"frage": frage}
    if conversation_id is not None:
        body["conversation_id"] = str(conversation_id)
    return client.post(FRAGE_URL, data=json.dumps(body),
                       content_type="application/json")


@pytest.fixture
def liegenschaft(db):
    _c, app_user = _admin()  # nur um einen Akteur zu haben
    prop = property_service.create_property(
        app_user.id, name="Villa Sonnenschein", property_type="EINFAMILIENHAUS",
        street="Ahornweg", house_number="7", postal_code="12345", city="Musterstadt",
    )
    return prop


# --- Frage / Fortsetzung ---------------------------------------------------

def test_frage_beginnt_gespraech(db, liegenschaft):
    client, _ = _admin()
    r = _post_frage(client, "Villa Sonnenschein")
    assert r.status_code == 200, r.content
    daten = r.json()
    assert daten["frage"]["role"] == "USER"
    assert daten["frage"]["seq"] == 1
    assert daten["antwort"]["role"] == "ASSISTANT"
    assert daten["antwort"]["seq"] == 2
    assert daten["conversation_id"]


def test_frage_fortsetzung_zaehlt_weiter(db, liegenschaft):
    client, _ = _admin()
    erste = _post_frage(client, "Villa Sonnenschein").json()
    cid = erste["conversation_id"]
    zweite = _post_frage(client, "Und die Adresse?", conversation_id=cid).json()
    assert zweite["conversation_id"] == cid
    assert zweite["frage"]["seq"] == 3
    assert zweite["antwort"]["seq"] == 4


def test_frage_in_fremdem_gespraech_404(db):
    a_client, _ = _admin()
    cid = _post_frage(a_client, "Meins").json()["conversation_id"]
    b_client, _ = _admin()
    r = _post_frage(b_client, "Fremd", conversation_id=cid)
    assert r.status_code == 404


# --- Lesen / Löschen / Eigentum --------------------------------------------

def test_liste_und_detail_nur_eigene(db):
    a_client, _ = _admin()
    cid = _post_frage(a_client, "Meine Frage").json()["conversation_id"]

    liste = a_client.get("/api/ai/conversations").json()
    assert any(g["id"] == cid for g in liste)

    detail = a_client.get(f"/api/ai/conversations/{cid}").json()
    assert detail["id"] == cid
    assert len(detail["turns"]) == 2

    b_client, _ = _admin()
    assert b_client.get(f"/api/ai/conversations/{cid}").status_code == 404
    assert cid not in [g["id"] for g in b_client.get("/api/ai/conversations").json()]


def test_loeschen_eigenes_und_fremdes(db):
    a_client, _ = _admin()
    cid = _post_frage(a_client, "Löschbar").json()["conversation_id"]

    b_client, _ = _admin()
    assert b_client.delete(f"/api/ai/conversations/{cid}").status_code == 404  # fremd

    assert a_client.delete(f"/api/ai/conversations/{cid}").status_code == 200
    assert a_client.get(f"/api/ai/conversations/{cid}").status_code == 404     # weg


def test_anonym_abgewiesen(db):
    r = Client().post(FRAGE_URL, data=json.dumps({"frage": "hallo"}),
                      content_type="application/json")
    assert r.status_code in (401, 403)


# --- Happy Path mit gepatchtem Modell --------------------------------------

def test_happy_path_mit_quelle(db, liegenschaft, monkeypatch):
    """Modell gepatcht: die Antwort zitiert die gefundene Liegenschaft als Quelle."""
    fake = FakeBackend(responses=[
        {"intent": "AUSKUNFT", "entitaeten": [0]},
        {"antwort": "Die Villa steht in Musterstadt.", "quellen": [0]},
    ])
    monkeypatch.setattr("db_core.ai.assistent.get_backend", lambda *a, **k: fake)

    client, _ = _admin()
    daten = _post_frage(client, "Villa Sonnenschein").json()
    quellen = daten["antwort"]["sources"]
    assert len(quellen) == 1
    assert quellen[0]["typ"] == "LIEGENSCHAFT"
    assert quellen[0]["id"] == str(liegenschaft.id)
    assert daten["antwort"]["ai_run_id"] is not None
