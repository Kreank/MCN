"""KI Slice 5 — Gesprächsspeicher (Migration 0117): Parität + DB-Tore.

Prüft die physischen Invarianten der beiden Tabellen `ai.conversation` /
`ai.conversation_turn` DIREKT gegen die DB (am Service vorbei über das ORM),
denn genau das ist die Doktrin „die DB erzwingt, nicht der Code":

* **Parität:** Gespräch + Nutzerfrage + Assistenten-Antwort werden über alle
  Spalten angelegt und zurückgelesen.
* **Gespräch startet ACTIVE / Eigentümer unveränderlich** (Trigger `guard_conversation`).
* **Turns sind append-only** (Trigger `forbid_mutation` auf UPDATE).
* **Rollen-Kohärenz** (CHECK): eine Nutzerfrage trägt keine Assistenten-Metadaten.
* **seq eindeutig je Gespräch** (UNIQUE).
* **Gespräch ist löschbar, Turns hängen per CASCADE daran** (DSGVO Art. 17) — der
  personenbezogene Rohtext verschwindet, das `ai_run`-Audit bleibt bestehen.

Nicht hier prüfbar: das `REVOKE DELETE` auf `conversation_turn` (verhindert das
Einzel-Löschen eines Turns) ist Defense-in-Depth für die eingeschränkte
App-Rolle in Produktion — die Testverbindung ist Superuser und umgeht REVOKE.
Die harte Immutabilität (append-only) sowie die CASCADE greifen unabhängig davon.
"""
import uuid

import pytest
from django.db import Error
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import AiRun, Conversation, ConversationTurn


# --- Hilfen ----------------------------------------------------------------

def _conversation(actor, *, title="Wie ist der Stand bei Familie Borm?"):
    with business_transaction(actor.id):
        conv = Conversation.objects.create(
            id=uuid.uuid4(), created_by_user=actor, title=title,
        )
    conv.refresh_from_db()
    return conv


def _ai_run(actor):
    with business_transaction(actor.id):
        run = AiRun.objects.create(
            id=uuid.uuid4(), model_name="qwen2.5-7b-instruct", model_version="q4",
            workflow_name="ki_assistent", workflow_version="v1", prompt_version="v1",
            triggered_by_user=actor,
        )
    return run


def _turn(actor, conv, *, seq, role, content, **extra):
    with business_transaction(actor.id):
        turn = ConversationTurn.objects.create(
            id=uuid.uuid4(), conversation=conv, seq=seq, role=role,
            content=content, **extra,
        )
    turn.refresh_from_db()
    return turn


# --- Parität ---------------------------------------------------------------

def test_conversation_und_turn_roundtrip(app_user):
    """Gespräch + Nutzerfrage + Assistenten-Antwort über alle Spalten."""
    conv = _conversation(app_user)
    assert conv.created_by_user_id == app_user.id
    assert conv.status == "ACTIVE"           # db_default
    assert conv.created_at is not None and conv.updated_at is not None

    frage = _turn(app_user, conv, seq=1, role="USER",
                  content="Wie ist der Stand bei Familie Borm?")
    assert frage.sources == [] and frage.intent is None
    assert frage.ai_run_id is None and frage.proposal_id is None
    assert frage.aus_untrusted_quelle is False

    run = _ai_run(app_user)
    antwort = _turn(
        app_user, conv, seq=2, role="ASSISTANT",
        content="Zwei offene Aufträge, letzte Wartung am 3. Juni.",
        intent="AUSKUNFT", ai_run=run,
        sources=[{"typ": "AUFTRAG", "id": str(uuid.uuid4()), "titel": "Heizungswartung"}],
        aus_untrusted_quelle=True,
    )
    assert antwort.role == "ASSISTANT"
    assert antwort.ai_run_id == run.id
    assert antwort.intent == "AUSKUNFT"
    assert antwort.sources[0]["typ"] == "AUFTRAG"
    assert antwort.aus_untrusted_quelle is True


# --- Tore (DB-Trigger / CHECK) ---------------------------------------------

def test_conversation_muss_active_starten(app_user):
    """Ein Gespräch entsteht immer ACTIVE (kein Direkt-INSERT als ARCHIVED)."""
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            Conversation.objects.create(
                id=uuid.uuid4(), created_by_user=app_user, status="ARCHIVED",
            )


def test_conversation_eigentuemer_unveraenderlich(app_user):
    """created_by_user ist nach Anlage unveränderlich (Identität)."""
    conv = _conversation(app_user)
    with business_transaction(app_user.id):
        fremd = type(app_user).objects.create(
            id=uuid.uuid4(), display_name="Anderer", status="ACTIVE", version=1,
        )
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            conv.created_by_user = fremd
            conv.save()


def test_conversation_titel_und_status_aenderbar(app_user):
    """Titel/Status sind pflegbar (Archivieren); updated_at zieht nach."""
    conv = _conversation(app_user, title="")
    with business_transaction(app_user.id):
        conv.title = "Stand Familie Borm"
        conv.status = "ARCHIVED"
        conv.save()
    conv.refresh_from_db()
    assert conv.title == "Stand Familie Borm"
    assert conv.status == "ARCHIVED"
    assert conv.updated_at >= conv.created_at


def test_turn_ist_append_only(app_user):
    """Eine gesprochene Nachricht ändert sich nie (UPDATE-Trigger)."""
    conv = _conversation(app_user)
    turn = _turn(app_user, conv, seq=1, role="USER", content="Originalfrage")
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            turn.content = "nachträglich verändert"
            turn.save()


def test_turn_seq_eindeutig_je_gespraech(app_user):
    """Zwei Turns mit derselben Nummer im selben Gespräch sind unmöglich."""
    conv = _conversation(app_user)
    _turn(app_user, conv, seq=1, role="USER", content="erste")
    with pytest.raises(Error):
        _turn(app_user, conv, seq=1, role="ASSISTANT", content="zweite")


@pytest.mark.parametrize("feld,wert", [
    ("intent", "AUSKUNFT"),
    ("sources", [{"typ": "AUFTRAG", "id": "x"}]),
    ("aus_untrusted_quelle", True),
    ("proposal_id", uuid.uuid4()),
])
def test_user_turn_ohne_assistenten_metadaten(app_user, feld, wert):
    """Rollen-Kohärenz (alle fünf Konjunkte): eine Nutzerfrage trägt KEINE
    Assistenten-Metadaten — nicht intent, nicht sources, nicht das Untrusted-Flag,
    nicht proposal_id (und nicht ai_run_id, s. eigener Fall unten)."""
    conv = _conversation(app_user)
    with pytest.raises(Error):
        _turn(app_user, conv, seq=1, role="USER", content="frage", **{feld: wert})


def test_user_turn_ohne_ai_run(app_user):
    """Der fünfte Konjunkt: ein USER-Turn darf keinen ai_run tragen."""
    conv = _conversation(app_user)
    run = _ai_run(app_user)
    with pytest.raises(Error):
        _turn(app_user, conv, seq=1, role="USER", content="frage", ai_run=run)


# --- Löschbarkeit (DSGVO Art. 17) ------------------------------------------

def test_gespraech_loeschbar_mit_kaskade(app_user):
    """Der Eigentümer löscht sein Gespräch; die Turns (Rohtext) gehen mit.

    Das `ai_run`-Audit der Antwort bleibt bestehen — Nachweis ohne Rohtext.
    """
    conv = _conversation(app_user)
    _turn(app_user, conv, seq=1, role="USER", content="Personenbezogene Frage über X")
    run = _ai_run(app_user)
    _turn(app_user, conv, seq=2, role="ASSISTANT", content="Antwort mit Namen", ai_run=run)

    with business_transaction(app_user.id):
        conv.delete()

    assert not Conversation.objects.filter(id=conv.id).exists()
    assert ConversationTurn.objects.filter(conversation_id=conv.id).count() == 0
    # Das unveränderliche Audit überlebt die DSGVO-Löschung des Rohtexts.
    assert AiRun.objects.filter(id=run.id).exists()
