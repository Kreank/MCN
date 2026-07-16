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
from db_core.models import Tool, WorkOrder
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
