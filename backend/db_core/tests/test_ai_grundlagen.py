"""KI-Grundlagen (Schema `ai`, Migration 0027) — Model-DB-Parität + DB-Tore.

Erstes Stück des KI-Fundaments: Django-Models (managed=False) auf die vier
`ai.*`-Tabellen, die seit 0027 physisch in der DB liegen, aber bisher von keinem
Backend-Code benutzt wurden.

Was hier scharf geprüft wird — nicht nur „lädt", sondern gegen die echte DB:

* **Parität:** Jedes Model wird real angelegt und zurückgelesen. Ein INSERT über
  alle Spalten beweist, dass Model und physische Tabelle deckungsgleich sind
  (fehlt/verrutscht eine Spalte, schlägt es hier fehl, nicht erst im Betrieb).
* **Die Tore sind DB-TRIGGER, nicht Service-Regeln.** Die `test_trigger_*`-Fälle
  gehen bewusst am (noch nicht existierenden) Service vorbei und schreiben direkt
  über das ORM:
  - ein abgeschlossener `ai_run` ist unveränderlich (`guard_ai_run_update`),
  - der Inhalt eines `ai_proposal` (Payload/Hash/Ziel) ist nach Anlage
    unveränderlich (`guard_ai_proposal`),
  - eine Freigabe nach Ablaufzeit ist unzulässig, und die Freigabezeit setzt die
    **Serverzeit** (nicht fälschbar).
  Das ist der physische Kern der Vision „die KI geht durch dieselben Tore wie ein
  Mensch": ohne diese Trigger wäre ein Vorschlag kein Vorschlag.
"""
import uuid
from datetime import timedelta

import pytest
from django.db import Error
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import AiProposal, AiRun, ContentItem, Embedding, File


# --- Hilfen ----------------------------------------------------------------

def _ai_run(actor):
    """Ein frisch gestarteter (nicht abgeschlossener) KI-Lauf."""
    with business_transaction(actor.id):
        run = AiRun.objects.create(
            id=uuid.uuid4(),
            model_name="qwen2.5-7b-instruct",
            model_version="q4_k_m",
            workflow_name="sprachmemo_bericht",
            workflow_version="v1",
            prompt_version="v1",
            triggered_by_user=actor,
        )
    run.refresh_from_db()
    return run


def _proposal(actor, run, *, expires_in=timedelta(hours=1)):
    with business_transaction(actor.id):
        prop = AiProposal.objects.create(
            id=uuid.uuid4(),
            ai_run=run,
            proposal_type="BERICHT_ENTWURF",
            target_type="site_report",
            target_id=uuid.uuid4(),
            proposed_payload={"lines": [{"description": "Heizung entlüftet"}]},
            payload_hash="hash-a",
            expires_at=timezone.now() + expires_in,
        )
    prop.refresh_from_db()
    return prop


# --- Parität ---------------------------------------------------------------

def test_ai_run_roundtrip(app_user):
    """ai.ai_run: Anlegen und Zurücklesen über alle Spalten (Parität + Defaults)."""
    run = _ai_run(app_user)
    assert run.workflow_name == "sprachmemo_bericht"
    assert run.model_name == "qwen2.5-7b-instruct"
    assert run.triggered_by_user_id == app_user.id
    assert run.permission_context == {}      # JSON-Default
    assert run.sources == [] and run.tools_used == []
    assert run.started_at is not None         # db_default Now()
    assert run.finished_at is None and run.result_status is None


def test_content_item_und_embedding_paritaet(app_user):
    """ai.content_item (Quelle = Datei) + ai.embedding (vector real[]) roundtrip."""
    with business_transaction(app_user.id):
        datei = File.objects.create(
            id=uuid.uuid4(),
            storage_key=str(uuid.uuid4()),
            original_filename="memo.m4a",
            mime_type="audio/mp4",
            size_bytes=4711,
            sha256="0" * 64,
            uploaded_by=app_user,
        )
        item = ContentItem.objects.create(
            id=uuid.uuid4(),
            source_type="PROTOKOLL",
            file=datei,
            extracted_text="Heizung entlüftet, Thermostatventil getauscht.",
            content_hash="h1",
        )
        emb = Embedding.objects.create(
            id=uuid.uuid4(),
            content_item=item,
            chunk_index=0,
            chunk_text="Heizung entlüftet",
            embedding_model="bge-m3",
            embedding_version="v1",
            vector=[0.1, 0.2, 0.3],
            content_hash="h1",
        )

    item.refresh_from_db()
    emb.refresh_from_db()
    assert item.source_type == "PROTOKOLL"
    assert item.file_id == datei.id
    assert item.is_untrusted is True          # db_default true — Quelle ist DATEN
    # real[] (float4): mit Toleranz vergleichen, nicht exakt
    assert emb.vector == pytest.approx([0.1, 0.2, 0.3], rel=1e-3)
    assert emb.content_item_id == item.id


# --- Tore (DB-Trigger) -----------------------------------------------------

def test_ai_run_wird_genau_einmal_abgeschlossen(app_user):
    """Abschluss ist erlaubt; ein zweiter Schreibvorgang danach ist gesperrt."""
    run = _ai_run(app_user)
    with business_transaction(app_user.id):
        run.finished_at = timezone.now()
        run.result_status = "OK"
        run.save()

    run.refresh_from_db()
    assert run.result_status == "OK" and run.finished_at is not None

    # Der abgeschlossene Lauf ist unveränderlich (guard_ai_run_update).
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            run.error_message = "nachträglich"
            run.save()


def test_ai_proposal_inhalt_ist_unveraenderlich(app_user):
    """Payload/Hash/Ziel eines Vorschlags sind nach Anlage eingefroren."""
    run = _ai_run(app_user)
    prop = _proposal(app_user, run)
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            prop.payload_hash = "hash-manipuliert"
            prop.save()


def test_ai_proposal_freigabe_setzt_serverzeit(app_user):
    """Freigabe innerhalb der Frist: approved_at wird vom Trigger gesetzt."""
    run = _ai_run(app_user)
    prop = _proposal(app_user, run)
    assert prop.status == "PENDING"            # db_default
    with business_transaction(app_user.id):
        prop.status = "APPROVED"
        prop.approved_by_user = app_user
        prop.save()

    prop.refresh_from_db()
    assert prop.status == "APPROVED"
    assert prop.approved_at is not None        # serverseitig gesetzt, nicht vom Client


def test_ai_proposal_keine_freigabe_nach_ablauf(app_user):
    """Ein abgelaufener Vorschlag lässt sich nicht mehr freigeben."""
    run = _ai_run(app_user)
    prop = _proposal(app_user, run, expires_in=timedelta(hours=-1))
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            prop.status = "APPROVED"
            prop.approved_by_user = app_user
            prop.save()


# --- Löschbarkeit (DSGVO Art. 17, Migration 0110) --------------------------

def test_ai_proposal_pending_nicht_loeschbar(app_user):
    from db_core.ai import proposal as proposal_service

    prop = _proposal(app_user, _ai_run(app_user))
    with pytest.raises(Error):          # PENDING trägt PII, aber wartet auf Entscheidung
        proposal_service.delete_proposal(app_user.id, proposal_id=prop.id)


def test_ai_proposal_genehmigt_nicht_loeschbar(app_user):
    from db_core.ai import proposal as proposal_service

    prop = _proposal(app_user, _ai_run(app_user))
    with business_transaction(app_user.id):
        prop.status = "APPROVED"
        prop.approved_by_user = app_user
        prop.save()
    with pytest.raises(Error):          # genehmigt → Belegvorstufe, GoBD-Aufbewahrung
        proposal_service.delete_proposal(app_user.id, proposal_id=prop.id)


def test_ai_proposal_abgelehnt_loeschbar(app_user):
    from db_core.ai import proposal as proposal_service
    from db_core.models import AiProposal

    prop = _proposal(app_user, _ai_run(app_user))
    proposal_service.reject(app_user.id, proposal_id=prop.id, reason="unbrauchbar")
    proposal_service.delete_proposal(app_user.id, proposal_id=prop.id)
    assert not AiProposal.objects.filter(id=prop.id).exists()


def test_ai_proposal_abgelaufen_loeschbar(app_user):
    from db_core.ai import proposal as proposal_service
    from db_core.models import AiProposal

    prop = _proposal(app_user, _ai_run(app_user))
    with business_transaction(app_user.id):
        prop.status = "EXPIRED"
        prop.save()
    proposal_service.delete_proposal(app_user.id, proposal_id=prop.id)
    assert not AiProposal.objects.filter(id=prop.id).exists()
