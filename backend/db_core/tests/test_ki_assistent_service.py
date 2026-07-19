"""KI Slice 5 — die Assistent-Pipeline (`db_core/ai/assistent.py`).

Geprüft wird der ganze Weg Frage → Retrieval → (skriptetes) Modell → Antwort →
persistierte Turns, plus die Sicherheitsgrenzen:

* **Happy Path:** eine echte, gesuchte Liegenschaft wird gefunden, das Modell wählt
  sie (Plan) und zitiert sie (Antwort); der Assistenten-Turn trägt Quelle + Intent +
  `ai_run`-Provenance.
* **Fallback ohne Modell:** fällt das Modell aus, entsteht trotzdem eine (determi-
  nistische) Antwort aus den Treffern — ohne `ai_run`-Link.
* **Zitat-Grenze:** ein Modell, das eine Quelle/Entität außerhalb der Trefferliste
  nennt, kann nichts erfinden — die Indizes werden verworfen.
* **Mehrturn:** Sequenznummern laufen fort, der Titel kommt aus der ERSTEN Frage.
* **CRUD/Eigentum:** fremde Gespräche sind unsichtbar (404-Semantik).

Das Modell ist ein `FakeBackend` mit skripteten Antworten — deterministisch, ohne Netz.
"""
import uuid

import pytest

from db_core.ai import assistent
from db_core.ai.llm import FakeBackend
from db_core.models import ConversationTurn
from db_core.services import property as property_service
from db_core.services.dossier import Sicht as DossierSicht
from db_core.services.suche import Sicht as SucheSicht


# --- Sicht + Backend-Helfer ------------------------------------------------

def _sicht(actor_id, *, alle=True, darf_anlegen=True):
    """Volle Lese-Sicht (row_scope ALLE) für Suche UND Dossier."""
    such = SucheSicht(
        identity=alle, property=alle, workflow=alle, invoicing=alle,
        pricing=alle, hr=alle, actor_id=actor_id,
    )
    doss = DossierSicht(
        identity=alle, property=alle, workflow=alle, invoicing=alle,
        pricing=alle, content=alle, maintenance=alle, actor_id=actor_id,
    )
    return assistent.AssistentSicht(
        such_sicht=such, dossier_sicht=doss,
        workflow_alle=alle, invoicing_alle=alle, darf_anlegen=darf_anlegen,
    )


def _fake(plan, antwort):
    return FakeBackend(responses=[plan, antwort])


@pytest.fixture
def liegenschaft(app_user):
    """Eine gesuchte Liegenschaft mit unverwechselbarem Namen."""
    return property_service.create_property(
        app_user.id, name="Villa Sonnenschein", property_type="EINFAMILIENHAUS",
        street="Ahornweg", house_number="7", postal_code="12345", city="Musterstadt",
    )


@pytest.fixture
def auftrag(app_user, liegenschaft):
    """Ein gesuchter Auftrag mit unverwechselbarem Titel (für den VORSCHLAG-Pfad)."""
    from db_core.services import auftrag as auftrag_service
    from db_core.services import identity as identity_service
    from db_core.services import projekt as projekt_service

    u = app_user.id
    melder = identity_service.create_person(u, first_name="Max", last_name="Muster")
    projekt = projekt_service.create_project(
        u, name="Instandhaltung", property_ids=[liegenschaft.id])
    vorgang = projekt_service.create_service_case(
        u, property_id=liegenschaft.id, subject="Heizung defekt",
        reported_by_party_id=melder.id, project_id=projekt.id)
    # Titel bewusst OHNE Token-Überschneidung mit der Liegenschaft (Villa
    # Sonnenschein / Ahornweg / Musterstadt), damit die Suche NUR den Auftrag liefert.
    return auftrag_service.create_work_order(
        u, property_id=liegenschaft.id, title="Notdienst Heizungsausfall Nord",
        service_case_id=vorgang.id, project_id=projekt.id)


# --- Happy Path ------------------------------------------------------------

def test_happy_path_auskunft(app_user, liegenschaft):
    """Frage → Treffer → Plan wählt [0] → Antwort zitiert [0] → zwei Turns."""
    conv = assistent.starte_gespraech(app_user.id)
    fake = _fake(
        {"intent": "AUSKUNFT", "entitaeten": [0]},
        {"antwort": "Die Villa Sonnenschein steht in Musterstadt.", "quellen": [0]},
    )
    res = assistent.antworte(
        app_user.id, conversation=conv, frage="Villa Sonnenschein",
        sicht=_sicht(app_user.id), backend=fake,
    )
    assert res.frage_turn.role == "USER"
    assert res.frage_turn.seq == 1
    assert res.antwort_turn.role == "ASSISTANT"
    assert res.antwort_turn.seq == 2
    assert res.antwort_turn.intent == "AUSKUNFT"
    assert res.antwort_turn.ai_run_id is not None          # Provenance vorhanden
    assert len(res.antwort_turn.sources) == 1
    assert res.antwort_turn.sources[0]["typ"] == "LIEGENSCHAFT"
    assert res.antwort_turn.sources[0]["id"] == str(liegenschaft.id)
    # Titel aus der ersten Frage.
    assert res.conversation.title == "Villa Sonnenschein"


def test_fallback_ohne_modell(app_user, liegenschaft):
    """Modell aus/kaputt → deterministische Antwort aus den Treffern, kein ai_run."""
    conv = assistent.starte_gespraech(app_user.id)
    res = assistent.antworte(
        app_user.id, conversation=conv, frage="Villa Sonnenschein",
        sicht=_sicht(app_user.id), backend=FakeBackend(responses=[]),  # erschöpft → LlmError
    )
    assert res.antwort_turn.role == "ASSISTANT"
    assert res.antwort_turn.ai_run_id is None              # kein Modell-Lauf
    assert "Villa Sonnenschein" in res.antwort_turn.content


def test_zitat_ausserhalb_der_treffer_wird_verworfen(app_user, liegenschaft):
    """Ein Modell, das eine Quelle außerhalb der Trefferliste nennt, erfindet nichts."""
    conv = assistent.starte_gespraech(app_user.id)
    fake = _fake(
        {"intent": "AUSKUNFT", "entitaeten": [99]},        # out of range → verworfen
        {"antwort": "Erfundene Quelle.", "quellen": [99]},  # out of range → verworfen
    )
    res = assistent.antworte(
        app_user.id, conversation=conv, frage="Villa Sonnenschein",
        sicht=_sicht(app_user.id), backend=fake,
    )
    assert res.antwort_turn.sources == []                  # keine erfundene Quelle


def test_mehrturn_seq_und_titel(app_user, liegenschaft):
    """Zweite Frage: seq 3/4, Titel bleibt aus der ersten Frage."""
    conv = assistent.starte_gespraech(app_user.id)
    assistent.antworte(
        app_user.id, conversation=conv, frage="Erste Frage: Villa Sonnenschein",
        sicht=_sicht(app_user.id),
        backend=_fake({"intent": "AUSKUNFT", "entitaeten": []},
                      {"antwort": "Antwort eins.", "quellen": []}),
    )
    res2 = assistent.antworte(
        app_user.id, conversation=conv, frage="Und die Adresse?",
        sicht=_sicht(app_user.id),
        backend=_fake({"intent": "AUSKUNFT", "entitaeten": []},
                      {"antwort": "Ahornweg 7.", "quellen": []}),
    )
    assert res2.frage_turn.seq == 3 and res2.antwort_turn.seq == 4
    assert res2.conversation.title == "Erste Frage: Villa Sonnenschein"
    assert ConversationTurn.objects.filter(conversation=conv).count() == 4


# --- CRUD / Eigentum -------------------------------------------------------

def test_gespraech_crud_und_eigentum(app_user):
    from db_core.models import AppUser

    conv = assistent.starte_gespraech(app_user.id)
    assert conv in assistent.meine_gespraeche(app_user.id)
    assert assistent.hole_gespraech(app_user.id, conv.id).id == conv.id

    # Fremder Nutzer sieht das Gespräch nicht (404-Semantik, keine Existenzaussage).
    from db_core.db_context import business_transaction
    with business_transaction(app_user.id):
        fremd = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Fremd", status="ACTIVE", version=1)
    with pytest.raises(assistent.GespraechNichtGefunden):
        assistent.hole_gespraech(fremd.id, conv.id)
    assert conv not in assistent.meine_gespraeche(fremd.id)


def test_gespraech_loeschen(app_user, liegenschaft):
    """Eigentümer löscht sein Gespräch; die Turns gehen per CASCADE mit."""
    from db_core.models import Conversation

    conv = assistent.starte_gespraech(app_user.id)
    assistent.antworte(
        app_user.id, conversation=conv, frage="Villa Sonnenschein",
        sicht=_sicht(app_user.id),
        backend=_fake({"intent": "AUSKUNFT", "entitaeten": [0]},
                      {"antwort": "Da.", "quellen": [0]}),
    )
    assistent.loesche_gespraech(app_user.id, conv.id)
    assert not Conversation.objects.filter(id=conv.id).exists()
    assert ConversationTurn.objects.filter(conversation_id=conv.id).count() == 0


# --- VORSCHLAG: Chat → Berichtsentwurf (ai_proposal) -----------------------

def test_vorschlag_legt_bericht_an_und_ist_materialisierbar(app_user, auftrag):
    """VORSCHLAG mit Ziel-Auftrag + Recht → ai_proposal, das `approve` materialisiert."""
    from db_core.ai import proposal as proposal_service
    from db_core.models import AiProposal

    conv = assistent.starte_gespraech(app_user.id)
    fake = _fake(
        {"intent": "VORSCHLAG", "entitaeten": [0]},
        {"activity_text": "Heizung entlüftet und Ventil getauscht.",
         "lines": [{"line_type": "ARBEITSZEIT", "description": "Entlüften",
                    "quantity": 1, "unit": "h"}]},
    )
    res = assistent.antworte(
        app_user.id, conversation=conv,
        frage="Notdienst Heizungsausfall Nord",
        sicht=_sicht(app_user.id, darf_anlegen=True), backend=fake,
    )
    assert res.antwort_turn.intent == "VORSCHLAG"
    assert res.antwort_turn.proposal_id is not None

    prop = AiProposal.objects.get(id=res.antwort_turn.proposal_id)
    assert prop.proposal_type == "SITE_REPORT_ENTWURF"
    assert prop.target_type == "work_order"
    assert prop.target_id == auftrag.id
    assert prop.status == "PENDING"

    # Dieselbe Fach-API/dieselben Tore wie beim Menschen → echter Bericht (ENTWURF).
    _p, result = proposal_service.approve(app_user.id, proposal_id=prop.id)
    assert result["result_type"] == "site_report"
    assert result["work_order_id"] == auftrag.id


def test_vorschlag_ohne_recht_kein_proposal(app_user, auftrag):
    """Fehlt workflow/ANLEGEN, entsteht KEIN Vorschlag — nur eine ehrliche Absage."""
    conv = assistent.starte_gespraech(app_user.id)
    # Nur der Plan-Aufruf fällt an (die Absage kommt vor der Entwurfs-Generierung).
    fake = FakeBackend(responses=[{"intent": "VORSCHLAG", "entitaeten": [0]}])
    res = assistent.antworte(
        app_user.id, conversation=conv, frage="Notdienst Heizungsausfall Nord",
        sicht=_sicht(app_user.id, darf_anlegen=False), backend=fake,
    )
    assert res.antwort_turn.intent == "VORSCHLAG"
    assert res.antwort_turn.proposal_id is None
    assert "Recht" in res.antwort_turn.content


def test_vorschlag_ohne_auftrag_kein_proposal(app_user, liegenschaft):
    """VORSCHLAG ohne Ziel-Auftrag in den Treffern → Nachfrage, kein Vorschlag."""
    conv = assistent.starte_gespraech(app_user.id)
    fake = FakeBackend(responses=[{"intent": "VORSCHLAG", "entitaeten": [0]}])
    res = assistent.antworte(
        app_user.id, conversation=conv, frage="Villa Sonnenschein",  # nur Liegenschaft
        sicht=_sicht(app_user.id, darf_anlegen=True), backend=fake,
    )
    assert res.antwort_turn.proposal_id is None
    assert "Auftrag" in res.antwort_turn.content
