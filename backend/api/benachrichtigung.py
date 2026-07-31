"""Benachrichtigungs-API — das persönliche Postfach (`notify.notification`).

**Kein Modulrecht.** Anders als jede andere Fachliste hängt dieser Endpunkt an
keinem Eintrag der Rechtematrix: Das Postfach ist eine persönliche Ressource,
genau wie die Gespräche des KI-Assistenten (`api/ki.py`). Ein Recht
„benachrichtigung/LESEN" wäre keine Sicherheit, sondern ein zusätzlicher Weg,
jemandem versehentlich seine eigenen Meldungen wegzunehmen. Die Grenze ist
stattdessen fest verdrahtet: **gefiltert wird immer auf den Akteur**, es gibt
keinen Parameter, mit dem man ein fremdes Postfach adressieren könnte.

Der Inhalt einer Benachrichtigung ist absichtlich dünn (Titel, ein Satz, Ziel) —
er wiederholt nur, was der Empfänger am Ziel ohnehin sehen darf. Die
Rechteprüfung sitzt am Ziel, nicht hier: Ein Klick auf eine Meldung zu einer
Aufgabe, die man nicht (mehr) sehen darf, endet regulär im 404 der Aufgaben-API.
"""
from datetime import datetime
from uuid import UUID

from ninja import Query, Router, Schema
from ninja.security import django_auth

from api.permissions import actor_id
from db_core.services import benachrichtigung as benachrichtigung_service

router = Router()


class AusloeserOut(Schema):
    id: UUID
    display_name: str


class BenachrichtigungOut(Schema):
    id: UUID
    kind: str
    title: str
    body: str | None = None
    #: Ziel als weiche Referenz: 'workflow.task' + id. Die Route baut das
    #: Frontend daraus — in der DB steht bewusst keine URL (Migration 0137).
    target_type: str
    target_id: UUID
    triggered_by: AusloeserOut | None = None
    read_at: datetime | None = None
    created_at: datetime


class BenachrichtigungListOut(Schema):
    items: list[BenachrichtigungOut]
    total: int
    #: Der Zähler der Glocke. Steht bewusst in JEDER Antwort: Das Frontend
    #: bekommt ihn damit ohne zweiten Request, auch nach dem Lesen einer Zeile.
    ungelesen: int
    page: int
    page_size: int


class BenachrichtigungFilter(Schema):
    nur_ungelesen: bool = False


def _out(n):
    return BenachrichtigungOut(
        id=n.id,
        kind=n.kind,
        title=n.title,
        body=n.body,
        target_type=n.target_type,
        target_id=n.target_id,
        triggered_by=(
            AusloeserOut(
                id=n.triggered_by.id, display_name=n.triggered_by.display_name
            )
            if n.triggered_by_id
            else None
        ),
        read_at=n.read_at,
        created_at=n.created_at,
    )


@router.get("", response=BenachrichtigungListOut, auth=django_auth)
def list_benachrichtigungen(
    request,
    filters: BenachrichtigungFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    """Das eigene Postfach, neueste zuerst."""
    actor = actor_id(request)
    items, total = benachrichtigung_service.liste(
        actor,
        nur_ungelesen=filters.nur_ungelesen,
        page=page,
        page_size=page_size,
    )
    return BenachrichtigungListOut(
        items=[_out(n) for n in items],
        total=total,
        ungelesen=benachrichtigung_service.ungelesen_zaehlen(actor),
        page=page,
        page_size=page_size,
    )


class ZaehlerOut(Schema):
    ungelesen: int


@router.get("/zaehler", response=ZaehlerOut, auth=django_auth)
def zaehler(request):
    """Nur der Zähler — das, was die Glocke im Hintergrund abfragt.

    Eigener Endpunkt statt der vollen Liste: Der Ruf läuft alle 60 Sekunden in
    jedem geöffneten Leitstand. Er darf nichts kosten außer einem Index-Zugriff
    auf den Teilindex `idx_notification_ungelesen`.
    """
    return ZaehlerOut(ungelesen=benachrichtigung_service.ungelesen_zaehlen(actor_id(request)))


@router.post("/{notification_id}/gelesen", response=ZaehlerOut, auth=django_auth)
def gelesen(request, notification_id: UUID):
    """Markiert eine Benachrichtigung als gelesen. Idempotent.

    Eine fremde oder unbekannte Zeile ist kein Fehler, sondern wirkungslos: Der
    Filter im Service trifft sie nicht. 404 wäre hier sogar schädlich — es
    verriete, dass es die Zeile gibt.
    """
    actor = actor_id(request)
    benachrichtigung_service.als_gelesen(actor, notification_id)
    return ZaehlerOut(ungelesen=benachrichtigung_service.ungelesen_zaehlen(actor))


@router.post("/alle-gelesen", response=ZaehlerOut, auth=django_auth)
def alle_gelesen(request):
    """Markiert alle eigenen Benachrichtigungen als gelesen."""
    actor = actor_id(request)
    benachrichtigung_service.alle_gelesen(actor)
    return ZaehlerOut(ungelesen=benachrichtigung_service.ungelesen_zaehlen(actor))
