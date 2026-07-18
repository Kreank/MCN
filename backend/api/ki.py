"""KI-API — Sprachmemo hochladen und den Bericht-Workflow starten.

v1: Der Monteur lädt ein Sprachmemo (Audio) zu einem Auftrag hoch; MCN legt es im
Objektspeicher ab und startet den Workflow (ASR → LLM → Bericht-Entwurf als
`ai_proposal`). Der Entwurf hat KEINE fachliche Wirkung — ein Mensch nimmt ihn über
die Freigabe ab, die App-Schicht materialisiert den Bericht über die Fach-API.
"""
import uuid
from datetime import datetime

from ninja import File as NinjaFile
from ninja import Form, Query, Router, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import check, require, require_scoped
from db_core.ai import leitstand_briefing, workflow_sprachmemo
from db_core.ai import proposal as proposal_service
from db_core.gate_errors import as_business_error
from db_core.models import AiProposal, ContentItem, Tool, WorkOrder
from db_core.services.dateien import datei_hochladen

router = Router()


class WorkflowGestartet(Schema):
    workflow_run_id: uuid.UUID
    status: str


class BriefingPunktOut(Schema):
    text: str
    bereich: str          # aufgaben | vorgaenge | wartung | angebote (→ Route im UI)
    dringlichkeit: str    # info | bald | ueberfaellig


class BriefingOut(Schema):
    schlagzeile: str
    punkte: list[BriefingPunktOut]
    stand: datetime
    ki_generiert: bool    # false = deterministisches Fallback (kein Modell)
    modell: str | None = None


@router.post("/sprachmemo", response={201: WorkflowGestartet}, auth=django_auth)
def sprachmemo_hochladen(
    request,
    datei: UploadedFile = NinjaFile(...),
    work_order_id: uuid.UUID = Form(...),
    asr_tool_key: str = Form(...),
):
    """Lädt ein Sprachmemo zu einem Auftrag hoch und startet den Bericht-Workflow.

    Der Dateityp wird aus der Endung gegen die Whitelist geprüft (Audio ergänzt in
    services/dateien.py). Ergebnis ist eine `workflow_run`-Kennung; der Fortschritt
    läuft asynchron über den queue-worker, das Ende ist ein PENDING-`ai_proposal`.
    """
    actor_id, _scope = require_scoped(request, "workflow", "ANLEGEN")
    if not WorkOrder.objects.filter(id=work_order_id).exists():
        raise HttpError(404, "Auftrag nicht gefunden.")
    if not Tool.objects.filter(tool_key=asr_tool_key, status="ACTIVE").exists():
        raise HttpError(422, f"ASR-Werkzeug '{asr_tool_key}' nicht gefunden oder inaktiv.")
    try:
        datei_obj, _link = datei_hochladen(
            actor_id, dateiname=datei.name, inhalt=datei.read(),
            work_order_id=work_order_id, link_category="DOKUMENT",
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))

    wf = workflow_sprachmemo.start_sprachmemo(
        actor_id, work_order_id=work_order_id, audio_file_id=datei_obj.id,
        asr_tool_key=asr_tool_key, triggered_by_user_id=actor_id,
    )
    return Status(201, WorkflowGestartet(workflow_run_id=wf.id, status=wf.status))


@router.get("/briefing", response=BriefingOut, auth=django_auth)
def leitstand_briefing_lesen(request, refresh: bool = Query(False)):
    """Tagesbriefing für die Leitstand-KI-Kachel (Lese-Zusammenfassung, kein Vorschlag).

    Rechte: `workflow/LESEN` über `require` (fail-closed) — wer nur EIGENE sehen
    darf (Monteur), bekommt 403; die Kachel gehört ohnehin nur zur Büro-Sicht.
    Angebote fließen nur ein, wenn zusätzlich `invoicing/LESEN` (ALLE) vorliegt —
    dieselbe Grenze wie bei der Angebots-Kachel der Übersicht.

    Das Ergebnis ist serverseitig gecacht; `refresh=1` erzwingt eine
    Neuberechnung (der „Aktualisieren"-Knopf).
    """
    actor, _scope = require(request, "workflow", "LESEN")
    mit_angebote = check(request, "invoicing", "LESEN") is not None
    return leitstand_briefing.hole_briefing(actor, mit_angebote=mit_angebote, refresh=refresh)


# ---------------------------------------------------------------------------
# KI-Vorschläge (ai_proposal) — die Freigabe-Kachel: ansehen, annehmen, ablehnen
# ---------------------------------------------------------------------------
#
# Ein Vorschlag hat KEINE fachliche Wirkung, bis ein Mensch ihn annimmt. Das
# Annehmen materialisiert ihn über die Fach-API (dieselben Tore wie beim Menschen)
# und ist deshalb an DIESELBEN Rechte gebunden wie die manuelle Anlage
# (workflow/ANLEGEN für den Bericht + workflow/AENDERN für seine Positionen).
# Ansehen liegt hinter workflow/LESEN (fail-closed, Büro-Sicht wie das Briefing).

_PROPOSAL_STATUS = ("PENDING", "APPROVED", "REJECTED", "EXPIRED")


class ProposalOut(Schema):
    id: uuid.UUID
    proposal_type: str
    target_type: str
    target_id: uuid.UUID
    status: str
    created_at: datetime
    expires_at: datetime
    # Provenienz: stammt der Entwurf aus einer untrusted Quelle (ASR/Vision/OCR)?
    # Server-abgeleitet aus content_item.is_untrusted — die einzige Bremse gegen
    # Content-Poisoning: die Injektion kann nichts schreiben, aber den Entwurfs-
    # INHALT vergiften. Die Kachel markiert das sichtbar.
    aus_untrusted_quelle: bool
    titel: str                       # Auszug der Tätigkeitsbeschreibung
    anzahl_positionen: int
    auftrag_titel: str | None = None
    modell: str | None = None
    workflow: str | None = None


class ProposalDetailOut(ProposalOut):
    proposed_payload: dict


class ApproveOut(Schema):
    proposal_id: uuid.UUID
    status: str
    result_type: str
    result_id: uuid.UUID
    work_order_id: uuid.UUID | None = None


class RejectIn(Schema):
    reason: str


def _aus_untrusted_quelle(prop) -> bool:
    """Ob der Entwurf aus einer untrusted Quelle stammt (server-abgeleitet).

    Konservativ: Lässt sich die Herkunft nicht auflösen (keine content_item-Quelle
    am Lauf), gilt der Vorschlag als untrusted — die Warnung fällt lieber einmal zu
    oft als einmal zu wenig."""
    sources = prop.ai_run.sources or []
    ids = [
        s.get("id") for s in sources
        if isinstance(s, dict) and s.get("type") == "content_item" and s.get("id")
    ]
    if not ids:
        return True
    return ContentItem.objects.filter(id__in=ids, is_untrusted=True).exists()


def _proposal_basis(prop) -> dict:
    payload = prop.proposed_payload if isinstance(prop.proposed_payload, dict) else {}
    titel = (payload.get("activity_text") or "").strip() or "(ohne Beschreibung)"
    lines = payload.get("lines")
    anzahl = len(lines) if isinstance(lines, list) else 0
    auftrag_titel = None
    if prop.target_type == "work_order":
        auftrag_titel = (
            WorkOrder.objects.filter(id=prop.target_id)
            .values_list("title", flat=True)
            .first()
        )
    run = prop.ai_run
    return {
        "id": prop.id,
        "proposal_type": prop.proposal_type,
        "target_type": prop.target_type,
        "target_id": prop.target_id,
        "status": prop.status,
        "created_at": prop.created_at,
        "expires_at": prop.expires_at,
        "aus_untrusted_quelle": _aus_untrusted_quelle(prop),
        "titel": titel,
        "anzahl_positionen": anzahl,
        "auftrag_titel": auftrag_titel,
        "modell": getattr(run, "model_name", None),
        "workflow": getattr(run, "workflow_name", None),
    }


@router.get("/proposals", response=list[ProposalOut], auth=django_auth)
def vorschlaege_liste(request, status: str = Query("PENDING")):
    """Die KI-Vorschläge eines Status (Default: die offenen). Büro-Sicht."""
    require(request, "workflow", "LESEN")
    status = (status or "PENDING").upper()
    if status not in _PROPOSAL_STATUS:
        raise HttpError(422, f"Unbekannter Status '{status}'.")
    qs = (
        AiProposal.objects.select_related("ai_run")
        .filter(status=status)
        .order_by("-created_at")
    )
    return [ProposalOut(**_proposal_basis(p)) for p in qs]


@router.get("/proposals/{proposal_id}", response=ProposalDetailOut, auth=django_auth)
def vorschlag_detail(request, proposal_id: uuid.UUID):
    """Ein Vorschlag samt vollständigem Entwurf (`proposed_payload`)."""
    require(request, "workflow", "LESEN")
    prop = (
        AiProposal.objects.select_related("ai_run").filter(id=proposal_id).first()
    )
    if prop is None:
        raise HttpError(404, "Vorschlag nicht gefunden.")
    basis = _proposal_basis(prop)
    basis["proposed_payload"] = (
        prop.proposed_payload if isinstance(prop.proposed_payload, dict) else {}
    )
    return ProposalDetailOut(**basis)


@router.post("/proposals/{proposal_id}/approve", response=ApproveOut, auth=django_auth)
def vorschlag_annehmen(request, proposal_id: uuid.UUID):
    """Nimmt einen Vorschlag an und materialisiert ihn über die Fach-API.

    Verlangt DIESELBEN Rechte wie die manuelle Anlage des Zielobjekts
    (workflow/ANLEGEN + workflow/AENDERN) — die KI geht durch kein anderes Tor.
    """
    actor, _ = require(request, "workflow", "ANLEGEN")
    require(request, "workflow", "AENDERN")
    try:
        prop, result = proposal_service.approve(actor, proposal_id=proposal_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return ApproveOut(proposal_id=prop.id, status=prop.status, **result)


@router.post("/proposals/{proposal_id}/reject", response=ProposalOut, auth=django_auth)
def vorschlag_ablehnen(request, proposal_id: uuid.UUID, payload: RejectIn):
    """Lehnt einen offenen Vorschlag ab (mit Pflicht-Begründung)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        prop = proposal_service.reject(
            actor, proposal_id=proposal_id, reason=payload.reason
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return ProposalOut(**_proposal_basis(prop))


@router.delete("/proposals/{proposal_id}", auth=django_auth)
def vorschlag_loeschen(request, proposal_id: uuid.UUID):
    """Löscht einen abgelehnten/abgelaufenen Vorschlag (DSGVO Art. 17)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        # Das Löschtor (guard_ai_proposal_delete) wirft für PENDING/APPROVED P0001;
        # as_business_error übersetzt das in eine klare 422 (der Service reicht den
        # rohen DB-Fehler durch).
        with as_business_error():
            proposal_service.delete_proposal(actor, proposal_id=proposal_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return {"detail": "gelöscht"}
