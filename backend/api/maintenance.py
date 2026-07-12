"""Wartungs- und Fälligkeiten-API (`maintenance.*`).

Drei Fristenarten unter einem Dach — und EINE Ansicht, die sie alle beantwortet:

  * **Wartungsverträge** (`maintenance_contract`, seit 0016) — Vertrag, Intervall,
    Fälligkeit, Auslöse-Historie.
  * **Prüffristen** (`inspection_type` + `inspection`, 0071) — wiederkehrende
    Prüfungen an Liegenschaft/Anlage, OHNE Wartungsvertrag.
  * **Gewährleistung** (`warranty`, 0071) — Fristablauf je Auftrag.
  * **Fälligkeiten** (`due_item`, 0071) — „Was steht an?" über alle drei Arten,
    mit den Aktionen Erledigen (Folgeobjekt erzeugen) und Verwerfen
    (begründungspflichtig, kein DELETE).

**Rechtemodul ist `maintenance`** (Migration 0071) — vorher lief die Wartung auf
`workflow` mit. Die Aktion **STORNIEREN** ist das Tor fürs **Verwerfen** einer
Fälligkeit: eine Frist bewusst verstreichen zu lassen ist eine andere
Entscheidung als sie zu erledigen, und nicht jede Rolle darf sie.

**Keine Rechtsauskunft.** Prüfarten sind vom Betrieb gepflegte Stammdaten (ein
paar Vorschläge sind mitgeliefert, `is_suggestion`); Gewährleistungsfristen sind
je Auftrag einstellbar. Das Produkt leitet aus `basis` (BGB/VOB) keine Frist ab.
"""
from datetime import date, datetime
from uuid import UUID

from django.db.models import F, Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.models import (
    DueItem,
    Inspection,
    InspectionType,
    MaintenanceContract,
    MaintenanceEvent,
    Warranty,
)
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services import gewaehrleistung as gewaehrleistung_service
from db_core.services import pruefung as pruefung_service
from db_core.services import wartung as wartung_service

router = Router()

CONTRACT_STATUSES = ("AKTIV", "INAKTIV", "ARCHIVIERT")


# --- Schemas ---------------------------------------------------------------

class PropertyRefOut(Schema):
    id: UUID
    property_number: str
    name: str
    city: str


class ContractOut(Schema):
    id: UUID
    contract_number: str
    name: str
    status: str
    interval_kind: str
    interval_days: int | None = None
    fixed_date: date | None = None
    due_action: str
    start_date: date
    next_due_date: date | None = None
    lead_time_days: int | None = None
    is_due: bool
    property: PropertyRefOut
    customer: str | None = None
    project_name: str | None = None


class ContractListOut(Schema):
    items: list[ContractOut]
    total: int
    page: int
    page_size: int


class EventOut(Schema):
    occurred_at: datetime
    due_date: date | None = None
    action: str
    result_object_type: str | None = None
    result_object_id: UUID | None = None
    note: str | None = None
    triggered_by: str | None = None


class ContractDetailOut(ContractOut):
    notes: str | None = None
    created_at: datetime
    events: list[EventOut]


class ContractFilter(Schema):
    q: str | None = None
    status: str | None = None
    property_id: UUID | None = None
    due: bool | None = None


class ContractCreateIn(Schema):
    property_id: UUID
    name: str
    start_date: date
    interval_kind: str
    due_action: str
    interval_days: int | None = None
    fixed_date: date | None = None
    party_id: UUID | None = None
    project_id: UUID | None = None
    lead_time_days: int | None = None
    notes: str | None = None


class ContractStatusIn(Schema):
    to_status: str


class ContractTriggerIn(Schema):
    note: str | None = None


# --- Mapper ----------------------------------------------------------------

def _property_ref(contract):
    p = contract.property
    return PropertyRefOut(
        id=p.id, property_number=p.property_number, name=p.name, city=p.address.city
    )


def _contract_out(contract, today):
    is_due = bool(
        contract.status == "AKTIV"
        and contract.next_due_date
        and contract.next_due_date <= today
    )
    return ContractOut(
        id=contract.id,
        contract_number=contract.contract_number,
        name=contract.name,
        status=contract.status,
        interval_kind=contract.interval_kind,
        interval_days=contract.interval_days,
        fixed_date=contract.fixed_date,
        due_action=contract.due_action,
        start_date=contract.start_date,
        next_due_date=contract.next_due_date,
        lead_time_days=contract.lead_time_days,
        is_due=is_due,
        property=_property_ref(contract),
        customer=contract.party.display_name if contract.party_id else None,
        project_name=contract.project.name if contract.project_id else None,
    )


# --- Lesende Endpoints (Dev-Phase ohne Auth) -------------------------------

@router.get("/contracts", response=ContractListOut)
def list_contracts(
    request,
    filters: ContractFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Wartungsverträge auflisten: Suche (Name/Nummer), Status-/Objekt-/
    Fälligkeitsfilter. Sortiert nach nächster Fälligkeit (fällige zuerst)."""
    require(request, "maintenance", "LESEN")
    if filters.status and filters.status not in CONTRACT_STATUSES:
        raise HttpError(422, f"Unbekannter Status '{filters.status}'.")

    today = date.today()
    qs = MaintenanceContract.objects.select_related(
        "property__address", "party", "project"
    )
    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(
            Q(name__icontains=needle) | Q(contract_number__icontains=needle)
        )
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.property_id:
        qs = qs.filter(property_id=filters.property_id)
    if filters.due:
        qs = qs.filter(status="AKTIV", next_due_date__lte=today)
    qs = qs.order_by(F("next_due_date").asc(nulls_last=True), "-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_contract_out(c, today) for c in qs[start:start + page_size]]
    return ContractListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/contracts/{contract_id}", response=ContractDetailOut)
def get_contract(request, contract_id: UUID):
    """Detail eines Wartungsvertrags inkl. Auslöse-Historie."""
    require(request, "maintenance", "LESEN")
    today = date.today()
    contract = (
        MaintenanceContract.objects.filter(id=contract_id)
        .select_related("property__address", "party", "project")
        .first()
    )
    if contract is None:
        raise HttpError(404, "Wartungsvertrag nicht gefunden.")

    events = [
        EventOut(
            occurred_at=e.occurred_at,
            due_date=e.due_date,
            action=e.action,
            result_object_type=e.result_object_type,
            result_object_id=e.result_object_id,
            note=e.note,
            triggered_by=e.triggered_by.display_name if e.triggered_by_id else None,
        )
        for e in MaintenanceEvent.objects.filter(contract_id=contract.id)
        .select_related("triggered_by")
        .order_by("-occurred_at")
    ]

    base = _contract_out(contract, today)
    return ContractDetailOut(
        **base.model_dump(),
        notes=contract.notes,
        created_at=contract.created_at,
        events=events,
    )


# --- Schreibende Endpoints (Session-Auth Pflicht) --------------------------

def _reload_contract(contract_id):
    contract = (
        MaintenanceContract.objects.filter(id=contract_id)
        .select_related("property__address", "party", "project")
        .first()
    )
    if contract is None:
        raise HttpError(404, "Wartungsvertrag nicht gefunden.")
    return _contract_out(contract, date.today())


@router.post("/contracts", response={201: ContractOut}, auth=django_auth)
def create_contract(request, payload: ContractCreateIn):
    """Legt einen Wartungsvertrag im Status AKTIV an und berechnet die erste
    Fälligkeit.

    `require` (fail-closed): der Vertrag trägt kein Owner-Feld, das der Erzeuger
    jemandem zuordnet — Wartungsverträge plant die Disposition/Leitung, nicht der
    Monteur (dessen 'EIGENE'-Scope hier zu 403 führt)."""
    actor, _ = require(request, "maintenance", "ANLEGEN")
    try:
        contract = wartung_service.create_contract(
            actor,
            property_id=payload.property_id,
            name=payload.name,
            start_date=payload.start_date,
            interval_kind=payload.interval_kind,
            due_action=payload.due_action,
            interval_days=payload.interval_days,
            fixed_date=payload.fixed_date,
            party_id=payload.party_id,
            project_id=payload.project_id,
            lead_time_days=payload.lead_time_days,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _reload_contract(contract.id))


@router.post("/contracts/{contract_id}/status", response=ContractOut, auth=django_auth)
def set_contract_status(request, contract_id: UUID, payload: ContractStatusIn):
    """Wechselt den Vertragsstatus (AKTIV↔INAKTIV, INAKTIV→ARCHIVIERT).

    Statuswechsel eines bestehenden Vertrags → AENDERN. Unzulässige Übergänge
    kommen als 422 (Service-Vorprüfung + DB-Trigger)."""
    actor, _ = require(request, "maintenance", "AENDERN")
    try:
        wartung_service.set_status(
            actor, contract_id=contract_id, to_status=payload.to_status
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_contract(contract_id)


@router.post("/contracts/{contract_id}/trigger", response=ContractOut, auth=django_auth)
def trigger_contract_action(request, contract_id: UUID, payload: ContractTriggerIn):
    """Löst die Fälligkeits-Aktion des Vertrags manuell aus (protokolliert append-only
    und rückt next_due_date vor).

    Auslösen ist eine Zustandsänderung am Vertrag (Fälligkeit, Historie) → AENDERN.
    Nur aktive Verträge mit offener Fälligkeit → sonst 422."""
    actor, _ = require(request, "maintenance", "AENDERN")
    try:
        wartung_service.trigger_action(
            actor, contract_id=contract_id, note=payload.note
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_contract(contract_id)


# ===========================================================================
# Fälligkeiten-Engine (Migration 0071)
# ===========================================================================

# --- Schemas: Fälligkeiten --------------------------------------------------

class DueItemOut(Schema):
    id: UUID
    kind: str
    title: str
    due_date: date
    lead_time_days: int
    status: str
    ueberfaellig: bool
    tage_bis_faellig: int
    property: PropertyRefOut | None = None
    # Woraus die Fälligkeit stammt (sprechender Bezug, keine rohe UUID).
    quelle: str
    quelle_id: UUID | None = None
    # Werktags-Vorschlag: die Fälligkeit selbst wird NIE verschoben, nur ein
    # daraus erzeugter Termin.
    termin_vorschlag: date
    termin_hinweis: str | None = None
    # Nur bei GEWAEHRLEISTUNG: der Vertriebshinweis (Anlage ohne Wartungsvertrag).
    vertriebshinweis: str | None = None
    result_object_type: str | None = None
    result_object_id: UUID | None = None
    resolution_note: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class DueItemListOut(Schema):
    items: list[DueItemOut]
    total: int
    page: int
    page_size: int
    offen_total: int
    ueberfaellig_total: int


class DueItemFilter(Schema):
    status: str | None = None
    kind: str | None = None
    property_id: UUID | None = None
    von: date | None = None
    bis: date | None = None


class DueItemErledigenIn(Schema):
    folgeaktion: str = "KEINE"
    termin_datum: date | None = None
    notiz: str | None = None


class DueItemVerwerfenIn(Schema):
    begruendung: str


class DueItemErledigtOut(Schema):
    item: DueItemOut
    hinweis: str | None = None


def _quelle(item):
    if item.kind == "WARTUNG" and item.contract_id:
        return f"Wartungsvertrag {item.contract.contract_number}", item.contract_id
    if item.kind == "PRUEFUNG" and item.inspection_id:
        return f"Prüfung {item.inspection.name}", item.inspection_id
    if item.kind == "GEWAEHRLEISTUNG" and item.warranty_id:
        return f"Auftrag {item.warranty.work_order.order_number}", item.warranty_id
    return "-", None


def _due_item_out(item, today, feiertage=None, mit_vertrag=None):
    """Eine Zeile der Fälligkeiten-Ansicht.

    `feiertage` und `mit_vertrag` sind die je Request EINMAL geladenen Kataloge
    (Feiertage, Liegenschaften mit aktivem Wartungsvertrag). Ohne sie kostete
    jede Zeile eigene Queries — bei 200 Zeilen ein Query-Sturm.
    """
    vorschlag, hinweis = faelligkeit_service.termin_vorschlag(
        item.due_date, feiertage
    )
    tipp = None
    if item.kind == "GEWAEHRLEISTUNG" and item.warranty_id:
        tipp = gewaehrleistung_service.vertriebshinweis(item.warranty, mit_vertrag)
    quelle, quelle_id = _quelle(item)
    return DueItemOut(
        id=item.id,
        kind=item.kind,
        title=item.title,
        due_date=item.due_date,
        lead_time_days=item.lead_time_days,
        status=item.status,
        ueberfaellig=(item.status == "OFFEN" and item.due_date < today),
        tage_bis_faellig=(item.due_date - today).days,
        property=_property_ref(item) if item.property_id else None,
        quelle=quelle,
        quelle_id=quelle_id,
        termin_vorschlag=vorschlag,
        termin_hinweis=hinweis,
        vertriebshinweis=tipp,
        result_object_type=item.result_object_type,
        result_object_id=item.result_object_id,
        resolution_note=item.resolution_note,
        resolved_at=item.resolved_at,
        resolved_by=item.resolved_by.display_name if item.resolved_by_id else None,
    )


@router.get("/due-items", response=DueItemListOut)
def list_due_items(
    request,
    filters: DueItemFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """„Was steht an?" — Fälligkeiten über alle drei Fristenarten.

    Filter: Status (Default OFFEN), Art, Liegenschaft, Zeitraum. Sortiert nach
    Fälligkeitsdatum (überfällige zuerst).
    """
    require(request, "maintenance", "LESEN")
    status = filters.status or "OFFEN"
    if status not in faelligkeit_service.STATUSES:
        raise HttpError(422, f"Unbekannter Status '{status}'.")
    if filters.kind and filters.kind not in faelligkeit_service.KINDS:
        raise HttpError(422, f"Unbekannte Art '{filters.kind}'.")

    today = date.today()
    qs = faelligkeit_service.liste(
        status=status,
        kind=filters.kind,
        property_id=filters.property_id,
        von=filters.von,
        bis=filters.bis,
        stichtag=today,
    )
    total = qs.count()
    start = (page - 1) * page_size
    seite = list(qs[start:start + page_size])
    # Kataloge EINMAL je Request laden (nicht je Zeile): Feiertage für den
    # Werktags-Vorschlag, Wartungsverträge für den Vertriebshinweis.
    feiertage = faelligkeit_service.feiertage_fenster(i.due_date for i in seite)
    mit_vertrag = gewaehrleistung_service.objekte_mit_wartungsvertrag(
        i.property_id for i in seite if i.kind == "GEWAEHRLEISTUNG"
    )
    items = [_due_item_out(i, today, feiertage, mit_vertrag) for i in seite]
    return DueItemListOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        offen_total=DueItem.objects.filter(status="OFFEN").count(),
        ueberfaellig_total=faelligkeit_service.ueberfaellig_count(today),
    )


@router.post(
    "/due-items/{due_item_id}/erledigen",
    response=DueItemErledigtOut,
    auth=django_auth,
)
def erledige_faelligkeit(request, due_item_id: UUID, payload: DueItemErledigenIn):
    """Erledigt eine Fälligkeit und erzeugt das gewählte Folgeobjekt.

    Folgeaktionen: TERMIN, AUFTRAG, PROJEKT, AUFGABE, ANGEBOT, KEINE (Vermerk
    pflicht). Jedes Folgeobjekt entsteht über den NORMALEN Service seines Bereichs —
    Statusautomat, Tore und Nummernkreise gelten unverändert, kein Sonderweg.

    TERMIN landet **immer im Plantafel-Rückstand** (Einsatz im Status UNGEPLANT).
    `termin_datum` ist ein **Wunschtermin**: er wird als Vermerk am Einsatz
    hinterlegt (Sonntag/Feiertag → nächster Werktag, `hinweis` erklärt es), aber
    NICHT als Zeitraum gesetzt. Ein Einsatz mit Zeitraum ohne Zuweisung wäre in
    der Plantafel unsichtbar — weder im Rückstand noch als Kachel im Raster; wer
    ihn fährt, entscheidet die Disposition.
    """
    actor, _ = require(request, "maintenance", "AENDERN")
    try:
        item, hinweis = faelligkeit_service.erledigen(
            actor,
            due_item_id=due_item_id,
            folgeaktion=payload.folgeaktion,
            termin_datum=payload.termin_datum,
            notiz=payload.notiz,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    frisch = faelligkeit_service.liste(status=None).filter(id=item.id).first()
    return DueItemErledigtOut(item=_due_item_out(frisch, date.today()), hinweis=hinweis)


@router.post(
    "/due-items/{due_item_id}/verwerfen", response=DueItemOut, auth=django_auth
)
def verwirf_faelligkeit(request, due_item_id: UUID, payload: DueItemVerwerfenIn):
    """Verwirft eine Fälligkeit — begründungspflichtig, kein DELETE (GoBD).

    Recht: **STORNIEREN**. Eine Frist bewusst verstreichen zu lassen ist eine
    andere Entscheidung als sie zu erledigen. Die Quelle (Vertrag/Prüfung) wird
    trotzdem fortgeschrieben, damit sie nicht still verstummt. Der verworfene
    Eintrag taucht nie wieder auf (statusunabhängiger UNIQUE-Index).
    """
    actor, _ = require(request, "maintenance", "STORNIEREN")
    try:
        item = faelligkeit_service.verwerfen(
            actor, due_item_id=due_item_id, begruendung=payload.begruendung
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    frisch = faelligkeit_service.liste(status=None).filter(id=item.id).first()
    return _due_item_out(frisch, date.today())


# --- Prüfarten (Stammdaten) -------------------------------------------------

class InspectionTypeOut(Schema):
    id: UUID
    name: str
    interval_kind: str
    interval_days: int | None = None
    lead_time_days: int
    responsibility: str | None = None
    notes: str | None = None
    is_suggestion: bool
    is_active: bool


class InspectionTypeIn(Schema):
    name: str
    interval_kind: str
    interval_days: int | None = None
    lead_time_days: int = 30
    responsibility: str | None = None
    notes: str | None = None


class InspectionTypePatchIn(Schema):
    name: str | None = None
    interval_kind: str | None = None
    interval_days: int | None = None
    lead_time_days: int | None = None
    responsibility: str | None = None
    notes: str | None = None
    is_active: bool | None = None


def _type_out(t):
    return InspectionTypeOut(
        id=t.id, name=t.name, interval_kind=t.interval_kind,
        interval_days=t.interval_days, lead_time_days=t.lead_time_days,
        responsibility=t.responsibility, notes=t.notes,
        is_suggestion=t.is_suggestion, is_active=t.is_active,
    )


@router.get("/inspection-types", response=list[InspectionTypeOut])
def list_inspection_types(request, nur_aktive: bool = Query(True)):
    """Prüfarten (Stammdaten des Betriebs).

    `is_suggestion=true` markiert die mitgelieferten Vorschläge — sie sind ein
    Startpunkt, KEIN Normkatalog und keine Rechtsauskunft.
    """
    require(request, "maintenance", "LESEN")
    qs = InspectionType.objects.all()
    if nur_aktive:
        qs = qs.filter(is_active=True)
    return [_type_out(t) for t in qs.order_by("name")]


@router.post("/inspection-types", response={201: InspectionTypeOut}, auth=django_auth)
def create_inspection_type(request, payload: InspectionTypeIn):
    """Legt eine Prüfart an (der Betrieb pflegt seine Prüfarten selbst)."""
    actor, _ = require(request, "maintenance", "ANLEGEN")
    try:
        t = pruefung_service.create_inspection_type(
            actor,
            name=payload.name,
            interval_kind=payload.interval_kind,
            interval_days=payload.interval_days,
            lead_time_days=payload.lead_time_days,
            responsibility=payload.responsibility,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _type_out(t))


@router.patch(
    "/inspection-types/{type_id}", response=InspectionTypeOut, auth=django_auth
)
def patch_inspection_type(request, type_id: UUID, payload: InspectionTypePatchIn):
    """Ändert eine Prüfart (auch die ausgelieferten Vorschläge — dafür sind sie da).

    Deaktivieren statt löschen (`is_active=false`); Löschen ist per Trigger gesperrt.
    """
    actor, _ = require(request, "maintenance", "AENDERN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        t = pruefung_service.update_inspection_type(
            actor, inspection_type_id=type_id, **gesetzt
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _type_out(t)


# --- Prüfungen --------------------------------------------------------------

class InspectionOut(Schema):
    id: UUID
    name: str
    inspection_type_id: UUID
    inspection_type_name: str
    status: str
    start_date: date
    interval_kind: str
    interval_days: int | None = None
    lead_time_days: int
    next_due_date: date | None = None
    responsibility: str | None = None
    notes: str | None = None
    property: PropertyRefOut
    asset_id: UUID | None = None
    is_due: bool


class InspectionListOut(Schema):
    items: list[InspectionOut]
    total: int
    page: int
    page_size: int


class InspectionIn(Schema):
    inspection_type_id: UUID
    property_id: UUID
    start_date: date
    name: str | None = None
    asset_id: UUID | None = None
    interval_kind: str | None = None
    interval_days: int | None = None
    lead_time_days: int | None = None
    responsibility: str | None = None
    party_id: UUID | None = None
    notes: str | None = None


class InspectionPatchIn(Schema):
    name: str | None = None
    interval_kind: str | None = None
    interval_days: int | None = None
    lead_time_days: int | None = None
    next_due_date: date | None = None
    responsibility: str | None = None
    notes: str | None = None


def _inspection_out(i, today):
    return InspectionOut(
        id=i.id,
        name=i.name,
        inspection_type_id=i.inspection_type_id,
        inspection_type_name=i.inspection_type.name,
        status=i.status,
        start_date=i.start_date,
        interval_kind=i.interval_kind,
        interval_days=i.interval_days,
        lead_time_days=i.lead_time_days,
        next_due_date=i.next_due_date,
        responsibility=i.responsibility,
        notes=i.notes,
        property=_property_ref(i),
        asset_id=i.asset_id,
        is_due=bool(
            i.status == "AKTIV" and i.next_due_date and i.next_due_date <= today
        ),
    )


@router.get("/inspections", response=InspectionListOut)
def list_inspections(
    request,
    q: str | None = Query(None),
    status: str | None = Query(None),
    property_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Prüffristen — wiederkehrende Prüfungen an Liegenschaft/Anlage."""
    require(request, "maintenance", "LESEN")
    if status and status not in CONTRACT_STATUSES:
        raise HttpError(422, f"Unbekannter Status '{status}'.")
    today = date.today()
    qs = Inspection.objects.select_related("property__address", "inspection_type")
    if q:
        qs = qs.filter(name__icontains=q.strip())
    if status:
        qs = qs.filter(status=status)
    if property_id:
        qs = qs.filter(property_id=property_id)
    qs = qs.order_by(F("next_due_date").asc(nulls_last=True), "name")
    total = qs.count()
    start = (page - 1) * page_size
    return InspectionListOut(
        items=[_inspection_out(i, today) for i in qs[start:start + page_size]],
        total=total, page=page, page_size=page_size,
    )


def _reload_inspection(inspection_id):
    i = (
        Inspection.objects.filter(id=inspection_id)
        .select_related("property__address", "inspection_type")
        .first()
    )
    if i is None:
        raise HttpError(404, "Prüfung nicht gefunden.")
    return _inspection_out(i, date.today())


@router.post("/inspections", response={201: InspectionOut}, auth=django_auth)
def create_inspection(request, payload: InspectionIn):
    """Legt eine wiederkehrende Prüfung an (erste Fälligkeit = Startdatum).

    Intervall/Vorlauf/Zuständigkeit kommen aus der Prüfart, sofern nicht
    überschrieben — und werden dabei KOPIERT: eine spätere Änderung der Prüfart
    verschiebt den Plan dieser Prüfung nicht rückwirkend.
    """
    actor, _ = require(request, "maintenance", "ANLEGEN")
    try:
        i = pruefung_service.create_inspection(
            actor,
            inspection_type_id=payload.inspection_type_id,
            property_id=payload.property_id,
            start_date=payload.start_date,
            name=payload.name,
            asset_id=payload.asset_id,
            interval_kind=payload.interval_kind,
            interval_days=payload.interval_days,
            lead_time_days=payload.lead_time_days,
            responsibility=payload.responsibility,
            party_id=payload.party_id,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _reload_inspection(i.id))


@router.patch("/inspections/{inspection_id}", response=InspectionOut, auth=django_auth)
def patch_inspection(request, inspection_id: UUID, payload: InspectionPatchIn):
    """Ändert eine Prüfung. Prüfart, Liegenschaft und Anlage bleiben fest."""
    actor, _ = require(request, "maintenance", "AENDERN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        pruefung_service.update_inspection(
            actor, inspection_id=inspection_id, **gesetzt
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_inspection(inspection_id)


@router.post(
    "/inspections/{inspection_id}/status", response=InspectionOut, auth=django_auth
)
def set_inspection_status(request, inspection_id: UUID, payload: ContractStatusIn):
    """AKTIV ↔ INAKTIV, INAKTIV → ARCHIVIERT (final). Kein Löschen."""
    actor, _ = require(request, "maintenance", "AENDERN")
    try:
        pruefung_service.set_inspection_status(
            actor, inspection_id=inspection_id, to_status=payload.to_status
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_inspection(inspection_id)


# --- Gewährleistung ---------------------------------------------------------

class WarrantyOut(Schema):
    id: UUID
    work_order_id: UUID
    order_number: str
    order_title: str
    basis: str
    start_date: date
    duration_months: int
    end_date: date
    lead_time_days: int
    is_machinery: bool
    status: str
    notes: str | None = None
    property: PropertyRefOut
    laeuft_ab_in_tagen: int
    abgelaufen: bool
    # Hinweis, KEINE Rechtsbehauptung: wartungsbedürftige Anlage ohne aktiven
    # Wartungsvertrag → Anlass für ein Wartungsangebot.
    vertriebshinweis: str | None = None


class WarrantyListOut(Schema):
    items: list[WarrantyOut]
    total: int
    page: int
    page_size: int
    default_months: int
    default_lead_days: int
    # Anhaltspunkte fürs Formular — Voreinstellungen, keine Rechtsauskunft.
    vorschlaege: dict[str, int]


class WarrantyIn(Schema):
    work_order_id: UUID
    start_date: date | None = None
    duration_months: int | None = None
    lead_time_days: int | None = None
    basis: str = "BGB"
    is_machinery: bool = False
    party_id: UUID | None = None
    notes: str | None = None


class WarrantyPatchIn(Schema):
    start_date: date | None = None
    duration_months: int | None = None
    lead_time_days: int | None = None
    basis: str | None = None
    is_machinery: bool | None = None
    notes: str | None = None
    status: str | None = None


class WarrantyDefaultsIn(Schema):
    months: int | None = None
    lead_days: int | None = None


class WarrantyDefaultsOut(Schema):
    default_months: int
    default_lead_days: int


def _warranty_out(w, today, mit_vertrag=None):
    return WarrantyOut(
        id=w.id,
        work_order_id=w.work_order_id,
        order_number=w.work_order.order_number,
        order_title=w.work_order.title,
        basis=w.basis,
        start_date=w.start_date,
        duration_months=w.duration_months,
        end_date=w.end_date,
        lead_time_days=w.lead_time_days,
        is_machinery=w.is_machinery,
        status=w.status,
        notes=w.notes,
        property=_property_ref(w),
        laeuft_ab_in_tagen=(w.end_date - today).days,
        abgelaufen=w.end_date < today,
        vertriebshinweis=gewaehrleistung_service.vertriebshinweis(w, mit_vertrag),
    )


@router.get("/warranties", response=WarrantyListOut)
def list_warranties(
    request,
    work_order_id: UUID | None = Query(None),
    property_id: UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Gewährleistungen (je Auftrag höchstens eine). Sortiert nach Fristende."""
    require(request, "maintenance", "LESEN")
    today = date.today()
    qs = Warranty.objects.select_related("property__address", "work_order")
    if work_order_id:
        qs = qs.filter(work_order_id=work_order_id)
    if property_id:
        qs = qs.filter(property_id=property_id)
    if status:
        if status not in gewaehrleistung_service.STATUSES:
            raise HttpError(422, f"Unbekannter Status '{status}'.")
        qs = qs.filter(status=status)
    qs = qs.order_by("end_date", "id")
    total = qs.count()
    start = (page - 1) * page_size
    seite = list(qs[start:start + page_size])
    mit_vertrag = gewaehrleistung_service.objekte_mit_wartungsvertrag(
        w.property_id for w in seite
    )
    return WarrantyListOut(
        items=[_warranty_out(w, today, mit_vertrag) for w in seite],
        total=total, page=page, page_size=page_size,
        default_months=gewaehrleistung_service.default_monate(),
        default_lead_days=gewaehrleistung_service.default_vorlauf(),
        vorschlaege=dict(gewaehrleistung_service.VORSCHLAEGE),
    )


def _reload_warranty(warranty_id):
    w = (
        Warranty.objects.filter(id=warranty_id)
        .select_related("property__address", "work_order")
        .first()
    )
    if w is None:
        raise HttpError(404, "Gewährleistung nicht gefunden.")
    return _warranty_out(w, date.today())


@router.post("/warranties", response={201: WarrantyOut}, auth=django_auth)
def create_warranty(request, payload: WarrantyIn):
    """Legt die Gewährleistung eines Auftrags an (Leistung muss erbracht sein).

    Frist und Vorlauf sind je Auftrag einstellbar; ohne Angabe greift der Default
    aus dem Firmenprofil. `basis` ist ein Label — daraus wird KEINE Frist abgeleitet.
    """
    actor, _ = require(request, "maintenance", "ANLEGEN")
    try:
        w = gewaehrleistung_service.create_warranty(
            actor,
            work_order_id=payload.work_order_id,
            start_date=payload.start_date,
            duration_months=payload.duration_months,
            lead_time_days=payload.lead_time_days,
            basis=payload.basis,
            is_machinery=payload.is_machinery,
            party_id=payload.party_id,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _reload_warranty(w.id))


@router.patch("/warranties/{warranty_id}", response=WarrantyOut, auth=django_auth)
def patch_warranty(request, warranty_id: UUID, payload: WarrantyPatchIn):
    """Ändert die Gewährleistung (Frist je Auftrag einstellbar).

    Verschiebt sich das Fristende, wird eine bereits erzeugte, noch OFFENE
    Fälligkeit zum alten Datum begründet VERWORFEN (nicht gelöscht, nicht
    umdatiert) — der Scheduler erzeugt die neue.
    """
    actor, _ = require(request, "maintenance", "AENDERN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        gewaehrleistung_service.update_warranty(
            actor, warranty_id=warranty_id, **gesetzt
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_warranty(warranty_id)


@router.patch("/warranty-defaults", response=WarrantyDefaultsOut, auth=django_auth)
def patch_warranty_defaults(request, payload: WarrantyDefaultsIn):
    """Voreinstellung der Gewährleistungsfrist am Firmenprofil (betrieblich).

    Recht: **company/AENDERN** — hier wird `company.company_profile` geschrieben,
    und dafür gilt dasselbe Tor wie überall sonst am Firmenprofil (api/firma.py).
    `maintenance/AENDERN` reichte NICHT: DISPOSITION und TECHNISCHE_LEITUNG haben
    es, dürfen die Stammdaten der Firma aber nicht ändern — sonst wäre dieser
    Endpunkt die Hintertür ins Firmenprofil.
    """
    actor, _ = require(request, "company", "AENDERN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        profil = gewaehrleistung_service.set_defaults(actor, **gesetzt)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return WarrantyDefaultsOut(
        default_months=profil.warranty_default_months,
        default_lead_days=profil.warranty_default_lead_days,
    )
