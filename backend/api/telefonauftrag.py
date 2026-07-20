"""Anruf-Durchstich — ein Endpunkt für den häufigsten Vorgang im Betrieb.

Das Telefon klingelt, der Kunde schildert sein Problem, man vereinbart einen
Termin. Bisher brauchte das vier bis fünf Bildschirme (Kontakt suchen/anlegen →
Liegenschaft → Auftrag → freigeben → Termin). Hier ist es ein Aufruf.

Fachliche Begründung und die Reihenfolge der Freigabe-Tore stehen im Service
(`db_core/services/telefonauftrag.py`). Die View bleibt dünn: Rechte prüfen,
durchreichen, ValueError → 422.
"""
from datetime import datetime
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.betriebszeit import Betriebszeitpunkt
from db_core.services import telefonauftrag as telefonauftrag_service

router = Router()


class AnrufPersonIn(Schema):
    """Der Anrufer. Mit `existing_party_id` wird ein bestehender Kontakt
    referenziert und NICHT neu angelegt — dann sind die Namensfelder unnötig."""

    existing_party_id: UUID | None = None
    salutation: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None


class AnrufPropertyIn(Schema):
    """Der Ort. Mit `existing_property_id` wird eine bestehende Liegenschaft
    referenziert und NICHT neu angelegt (kein Duplikat, keine zweite Adresse)."""

    existing_property_id: UUID | None = None
    # Bewusst OHNE Default: Ausgerechnet EINFAMILIENHAUS ist der Wert, der die
    # Ableitung des Verantwortungsbereichs auslöst. Als Default würde ein Client,
    # der das Feld weglässt, still PRIVATE_UNIT bekommen — und damit genau den
    # Schaden anrichten, vor dem EINDEUTIGER_SCOPE schützen soll. Bei
    # existing_property_id bleibt das Feld ungenutzt.
    property_type: str | None = None
    name: str | None = None
    street: str | None = None
    house_number: str | None = None
    postal_code: str | None = None
    city: str | None = None


class AnrufAuftragIn(Schema):
    """Was zu tun ist.

    `responsibility_scope` darf beim Einfamilienhaus entfallen (dort gibt es kein
    Gemeinschaftseigentum); bei allen anderen Typen ist er für die Freigabe
    Pflicht. `order_evidence_reference` wird ohne Angabe aus dem Telefonat
    formuliert — Textform genügt (A-26).
    """

    title: str
    description: str | None = None
    priority: str = "NORMAL"
    is_emergency: bool = False
    responsibility_scope: str | None = None
    order_evidence_reference: str | None = None
    # Gewerk (0120). Optional — am Telefon ist oft noch unklar, ob es SHK oder
    # Elektro wird. Ist es bekannt, bekommt der Auftrag eine sprechende Nummer
    # (AU-SHK-26-0001 statt AU-26-0001), und der Einsatz erbt das Gewerk.
    trade_id: UUID | None = None


class AnrufTerminIn(Schema):
    """Wann und wer. Ohne `scheduled_start` landet der Termin im **Rückstand**
    (Status UNGEPLANT) — das ist der zweite legitime Weg, kein Fehler."""

    # `Betriebszeitpunkt`, nicht `datetime`: Ein Zeitstempel ohne Offset (den
    # liefert jedes `<input type="datetime-local">`) gilt als Europe/Berlin statt
    # als UTC. settings.TIME_ZONE ist bewusst UTC, also läge „08:00" sonst zwei
    # Stunden daneben — genau der Versatz, den b4e24e8 für die übrigen
    # Planungs-Endpunkte geschlossen hat. Ausgabe bleibt `datetime` (aus der DB
    # kommt immer aware).
    scheduled_start: Betriebszeitpunkt | None = None
    scheduled_end: Betriebszeitpunkt | None = None
    building_id: UUID | None = None
    unit_id: UUID | None = None
    assignee_ids: list[UUID] = []
    resource_ids: list[UUID] = []
    access_instructions: str | None = None
    appointment_category_id: UUID | None = None


class AnrufIn(Schema):
    person: AnrufPersonIn
    property: AnrufPropertyIn
    auftrag: AnrufAuftragIn
    termin: AnrufTerminIn | None = None


class AnrufOut(Schema):
    party_id: UUID
    property_id: UUID
    work_order_id: UUID
    order_number: str
    order_status: str
    service_job_id: UUID
    job_number: str
    job_status: str
    scheduled_start: datetime | None
    # Ohne Beginn liegt der Termin im Rückstand. Das Feld macht der Oberfläche
    # explizit, was sonst nur aus dem Status zu erschließen wäre — sie muss die
    # Statuscodes nicht kennen, um den richtigen Hinweis anzuzeigen.
    im_rueckstand: bool


@router.post("/anruf", response={201: AnrufOut}, auth=django_auth)
def anruf_durchstich(request, payload: AnrufIn):
    """Kunde + Ort + Auftrag + Termin aus einem Telefonat — atomar.

    Der Auftrag entsteht direkt als FREIGEGEBEN: Das Telefonat wird als
    Beauftragungsnachweis hinterlegt, der Anrufer als PRINCIPAL eingetragen, der
    Verantwortungsbereich bestätigt. Ohne diese drei würde der DB-Trigger
    `trg_service_job_execution_gate` den Monteur am Termintag blockieren — der
    Termin wäre planbar, aber nicht ausführbar.

    Es entsteht **kein Vorgang**: Der Eingangskorb ist die optionale Vorstufe für
    Meldungen ohne Termin. Wer am Telefon schon terminiert, braucht ihn nicht.

    Fail-closed Tore, VOR der Transaktion geprüft und am tatsächlich
    Geschriebenen ausgerichtet (least privilege): identity.ANLEGEN für einen
    neuen Kontakt, sonst identity.LESEN; property.ANLEGEN+AENDERN für eine neue
    Liegenschaft, sonst property.LESEN; immer workflow.ANLEGEN (Auftrag und
    Einsatz verbrauchen je eine GoBD-Belegnummer) und workflow.AENDERN (Freigabe,
    Beteiligte, Zuweisungen). Termine plant die Disposition — Monteur-Scope
    'EIGENE' läuft hier in ein 403.
    """
    if payload.person.existing_party_id is None:
        actor, _ = require(request, "identity", "ANLEGEN")
    else:
        actor, _ = require(request, "identity", "LESEN")

    if payload.property.existing_property_id is None:
        require(request, "property", "ANLEGEN")
        require(request, "property", "AENDERN")
    else:
        require(request, "property", "LESEN")

    require(request, "workflow", "ANLEGEN")
    require(request, "workflow", "AENDERN")
    # FREIGEBEN ist ein eigenes Recht, kein Sonderfall von AENDERN — der reguläre
    # Pfad (api/auftrag.py, Statuswechsel) trennt das ausdrücklich. Ohne diese
    # Zeile wäre der Durchstich eine Rechte-Umgehung: Wer nur AENDERN hat, könnte
    # über /anruf freigeben, was ihm über /status verwehrt bleibt. Betrifft real
    # die Rolle DISPOSITION (0026), die AENDERN hat, FREIGEBEN aber nicht.
    require(request, "workflow", "FREIGEBEN")

    termin = payload.termin or AnrufTerminIn()

    try:
        party_id, property_id, order, job = telefonauftrag_service.anruf_durchstich(
            actor,
            existing_party_id=payload.person.existing_party_id,
            salutation=payload.person.salutation,
            first_name=payload.person.first_name,
            last_name=payload.person.last_name,
            phone=payload.person.phone,
            email=payload.person.email,
            existing_property_id=payload.property.existing_property_id,
            property_type=payload.property.property_type,
            property_name=payload.property.name,
            street=payload.property.street,
            house_number=payload.property.house_number,
            postal_code=payload.property.postal_code,
            city=payload.property.city,
            title=payload.auftrag.title,
            description=payload.auftrag.description,
            priority=payload.auftrag.priority,
            is_emergency=payload.auftrag.is_emergency,
            responsibility_scope=payload.auftrag.responsibility_scope,
            order_evidence_reference=payload.auftrag.order_evidence_reference,
            trade_id=payload.auftrag.trade_id,
            scheduled_start=termin.scheduled_start,
            scheduled_end=termin.scheduled_end,
            building_id=termin.building_id,
            unit_id=termin.unit_id,
            assignee_ids=termin.assignee_ids,
            resource_ids=termin.resource_ids,
            access_instructions=termin.access_instructions,
            appointment_category_id=termin.appointment_category_id,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))

    return Status(
        201,
        AnrufOut(
            party_id=party_id,
            property_id=property_id,
            work_order_id=order.id,
            order_number=order.order_number,
            order_status=order.status,
            service_job_id=job.id,
            job_number=job.job_number,
            job_status=job.status,
            scheduled_start=job.scheduled_start,
            im_rueckstand=job.scheduled_start is None,
        ),
    )
