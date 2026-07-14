"""API: Qualifikationen (Katalog, Nachweise, Bedarf) und Zuweisungs-Vorlagen.

Rechte-Zuschnitt — bewusst zweigeteilt:

- **Der Katalog und der BEDARF sind Planungsstammdaten** (`workflow`): Der
  Disponent muss wissen und festlegen dürfen, was ein Termintyp verlangt.
- **Der NACHWEIS am Mitarbeiter ist ein Personaldatum** (`hr`): Wer welchen
  Schein hat und wann er abläuft, gehört in die Personalakte. Ein Disponent ohne
  `hr`-Recht sieht auf der Plantafel nur die **Folge** („X hat keinen Nachweis
  für Gasschein") — genau so viel, wie er zum Disponieren braucht, und keinen
  Blick in die Akte. Dieselbe Grenze wie bei der Abwesenheitsart (DSGVO Art. 9).
"""
from datetime import date
from uuid import UUID

from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.betriebszeit import betriebs_datum
from db_core.models import Employee, Qualification
from db_core.services import qualifikation as qualifikation_service

router = Router()


# ===========================================================================
# Katalog (Planungsstammdaten: Modul `workflow`)
# ===========================================================================

class QualificationOut(Schema):
    id: UUID
    code: str
    label: str
    # Freie Gruppierung (GEWERK | ZERTIFIKAT | HERSTELLERSCHULUNG | …). **Kein
    # Enum** — der Betrieb legt seine Arten selbst an (User-Entscheidung:
    # „dynamisch halten, wir müssen sehr flexibel bleiben").
    kind: str | None = None
    description: str | None = None
    # Verlangt die Zuordnung ein Gültig-bis? (Gasschein ja, Gesellenbrief nein.)
    expires: bool = False
    active: bool = True
    sort_order: int = 0


class QualificationCreateIn(Schema):
    code: str
    label: str
    kind: str | None = None
    description: str | None = None
    expires: bool = False
    sort_order: int = 0


class QualificationUpdateIn(Schema):
    label: str | None = None
    kind: str | None = None
    description: str | None = None
    expires: bool | None = None
    active: bool | None = None
    sort_order: int | None = None


def _q_out(q):
    return QualificationOut(
        id=q.id, code=q.code, label=q.label, kind=q.kind,
        description=q.description, expires=q.expires, active=q.active,
        sort_order=q.sort_order,
    )


@router.get("/qualifikationen", response=list[QualificationOut])
def list_qualifikationen(request, include_inactive: bool = Query(False)):
    """Der Qualifikationskatalog. Planungsstammdaten → Modul `workflow`."""
    require(request, "workflow", "LESEN")
    qs = Qualification.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return [_q_out(q) for q in qs.order_by("sort_order", "label", "id")]


@router.post("/qualifikationen", response={201: QualificationOut}, auth=django_auth)
def create_qualifikation(request, payload: QualificationCreateIn):
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        q = qualifikation_service.create_qualification(
            actor,
            code=payload.code,
            label=payload.label,
            kind=payload.kind,
            description=payload.description,
            expires=payload.expires,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _q_out(q))


@router.patch(
    "/qualifikationen/{qualification_id}", response=QualificationOut, auth=django_auth
)
def update_qualifikation(
    request, qualification_id: UUID, payload: QualificationUpdateIn
):
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        q = qualifikation_service.update_qualification(
            actor,
            qualification_id=qualification_id,
            label=payload.label,
            kind=payload.kind,
            description=payload.description,
            expires=payload.expires,
            active=payload.active,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _q_out(q)


# ===========================================================================
# Bedarf (Terminkategorie / einzelner Termin) — Modul `workflow`
# ===========================================================================

class BedarfIn(Schema):
    qualification_ids: list[UUID] = []


@router.get(
    "/kategorien/{category_id}/qualifikationen", response=list[QualificationOut]
)
def kategorie_bedarf(request, category_id: UUID):
    require(request, "workflow", "LESEN")
    return [
        _q_out(q) for q in qualifikation_service.category_qualifications(category_id)
    ]


@router.put(
    "/kategorien/{category_id}/qualifikationen",
    response=list[QualificationOut],
    auth=django_auth,
)
def set_kategorie_bedarf(request, category_id: UUID, payload: BedarfIn):
    """Was dieser Termintyp IMMER verlangt (Vollersetzung)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        qs = qualifikation_service.set_category_qualifications(
            actor, category_id=category_id,
            qualification_ids=payload.qualification_ids,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return [_q_out(q) for q in qs]


@router.get("/einsaetze/{job_id}/qualifikationen", response=list[QualificationOut])
def einsatz_bedarf(request, job_id: UUID):
    require(request, "workflow", "LESEN")
    return [_q_out(q) for q in qualifikation_service.job_qualifications(job_id)]


@router.put(
    "/einsaetze/{job_id}/qualifikationen",
    response=list[QualificationOut],
    auth=django_auth,
)
def set_einsatz_bedarf(request, job_id: UUID, payload: BedarfIn):
    """Was DIESER Termin zusätzlich verlangt (Vollersetzung).

    Der Bedarf der Kategorie bleibt unberührt — wirksam ist die **Vereinigung**.
    """
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        qs = qualifikation_service.set_job_qualifications(
            actor, service_job_id=job_id,
            qualification_ids=payload.qualification_ids,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return [_q_out(q) for q in qs]


# ===========================================================================
# Nachweise am Mitarbeiter — Modul `hr` (Personaldatum!)
# ===========================================================================

class EmployeeQualificationOut(Schema):
    qualification: QualificationOut
    valid_from: date | None = None
    valid_until: date | None = None
    evidence_note: str | None = None
    # Abgeleitet: gilt der Nachweis HEUTE? (Der Terminabgleich nutzt den
    # Terminbeginn als Stichtag, nicht diesen.)
    gueltig_heute: bool = True


class EmployeeQualificationIn(Schema):
    qualification_id: UUID
    valid_from: date | None = None
    valid_until: date | None = None
    evidence_note: str | None = None


def _eq_out(eq):
    # `betriebs_datum()`, nicht `localdate()` und nicht `date.today()`:
    # `settings.TIME_ZONE` ist UTC, `localdate()` liefert also das UTC-Datum —
    # zwischen 00:00 und 02:00 MESZ stünde das Häkchen einen Tag daneben. Der
    # Service (`services/qualifikation.py`) rechnet längst in BETRIEBS_TZ; die
    # API zog hier eine zweite, abweichende Wahrheit.
    heute = betriebs_datum()
    gueltig = (eq.valid_until is None or eq.valid_until >= heute) and (
        eq.valid_from is None or eq.valid_from <= heute
    )
    return EmployeeQualificationOut(
        qualification=_q_out(eq.qualification),
        valid_from=eq.valid_from,
        valid_until=eq.valid_until,
        evidence_note=eq.evidence_note,
        gueltig_heute=gueltig,
    )


@router.get(
    "/mitarbeiter/{employee_id}/qualifikationen",
    response=list[EmployeeQualificationOut],
)
def mitarbeiter_qualifikationen(request, employee_id: UUID):
    """Die Nachweise eines Mitarbeiters. **`hr`-Recht** — Personalakte.

    Der Disponent braucht das nicht: Er sieht auf der Plantafel die FOLGE
    („X hat keinen Nachweis für Gasschein"), nicht die Akte.
    """
    require(request, "hr", "LESEN")
    if not Employee.objects.filter(id=employee_id).exists():
        raise HttpError(404, "Mitarbeiter nicht gefunden.")
    return [
        _eq_out(eq)
        for eq in qualifikation_service.employee_qualifications(employee_id)
    ]


@router.put(
    "/mitarbeiter/{employee_id}/qualifikationen",
    response=EmployeeQualificationOut,
    auth=django_auth,
)
def set_mitarbeiter_qualifikation(
    request, employee_id: UUID, payload: EmployeeQualificationIn
):
    """Trägt einen Nachweis ein oder schreibt ihn fort (Verlängerung)."""
    actor, _ = require(request, "hr", "AENDERN")
    if not Employee.objects.filter(id=employee_id).exists():
        raise HttpError(404, "Mitarbeiter nicht gefunden.")
    try:
        eq = qualifikation_service.set_employee_qualification(
            actor,
            employee_id=employee_id,
            qualification_id=payload.qualification_id,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            evidence_note=payload.evidence_note,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _eq_out(eq)


@router.delete(
    "/mitarbeiter/{employee_id}/qualifikationen/{qualification_id}",
    response={204: None},
    auth=django_auth,
)
def remove_mitarbeiter_qualifikation(
    request, employee_id: UUID, qualification_id: UUID
):
    """Entfernt einen Nachweis (Fehleintrag).

    Bewusst löschbar: Ein falscher Haken ist kein Geschäftsvorfall, und eine
    Karteileiche „hat den Gasschein, eigentlich aber nicht" wäre gefährlicher als
    die Löschung. Die Änderung steht im Audit-Log.
    """
    actor, _ = require(request, "hr", "AENDERN")
    # Wie GET und PUT daneben: eine Phantom-ID ist ein 404, kein stilles 204.
    if not Employee.objects.filter(id=employee_id).exists():
        raise HttpError(404, "Mitarbeiter nicht gefunden.")
    qualifikation_service.remove_employee_qualification(
        actor, employee_id=employee_id, qualification_id=qualification_id
    )
    return 204, None


# ===========================================================================
# Zuweisungs-Vorlagen (lose Gruppen) — Modul `workflow`
# ===========================================================================

class TemplateMemberOut(Schema):
    app_user_id: UUID
    display_name: str
    role: str


class TemplateOut(Schema):
    id: UUID
    name: str
    description: str | None = None
    active: bool = True
    sort_order: int = 0
    members: list[TemplateMemberOut] = []


class TemplateMemberIn(Schema):
    app_user_id: UUID
    role: str = "TECHNICIAN"


class TemplateCreateIn(Schema):
    name: str
    description: str | None = None
    sort_order: int = 0
    members: list[TemplateMemberIn] = []


class TemplateUpdateIn(Schema):
    name: str | None = None
    description: str | None = None
    active: bool | None = None
    sort_order: int | None = None
    # None = Mitglieder nicht anfassen; [] = alle entfernen.
    members: list[TemplateMemberIn] | None = None


def _t_out(t):
    return TemplateOut(
        id=t.id, name=t.name, description=t.description, active=t.active,
        sort_order=t.sort_order,
        members=[
            TemplateMemberOut(
                app_user_id=m.assignee_id,
                display_name=m.assignee.display_name,
                role=m.role,
            )
            for m in t.members.all()
        ],
    )


@router.get("/vorlagen", response=list[TemplateOut])
def list_vorlagen(request, include_inactive: bool = Query(False)):
    """Zuweisungs-Vorlagen: benannte Personengruppen als **Vorschlag**.

    Kein Team-Modell (User-Entscheidung „lose Gruppen, wechselnd") — die Vorlage
    bindet nichts, sie füllt nur den Dialog vor.
    """
    require(request, "workflow", "LESEN")
    return [
        _t_out(t)
        for t in qualifikation_service.templates(include_inactive=include_inactive)
    ]


@router.post("/vorlagen", response={201: TemplateOut}, auth=django_auth)
def create_vorlage(request, payload: TemplateCreateIn):
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        t = qualifikation_service.create_template(
            actor,
            name=payload.name,
            description=payload.description,
            sort_order=payload.sort_order,
            members=[m.dict() for m in payload.members],
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    # Über die ID nachladen, NICHT über den Namen: Der Unique-Index gilt nur für
    # AKTIVE Vorlagen — ein stillgelegter Namensvetter darf existieren, und eine
    # Namenssuche lieferte dann die falsche (alte) Zeile zurück. (Review-Fund.)
    alle = qualifikation_service.templates(include_inactive=True)
    return Status(201, _t_out(next(x for x in alle if x.id == t.id)))


@router.patch("/vorlagen/{template_id}", response=TemplateOut, auth=django_auth)
def update_vorlage(request, template_id: UUID, payload: TemplateUpdateIn):
    actor, _ = require(request, "workflow", "AENDERN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        qualifikation_service.update_template(
            actor,
            template_id=template_id,
            name=payload.name,
            description=payload.description,
            active=payload.active,
            sort_order=payload.sort_order,
            members=(
                [m.dict() for m in (payload.members or [])]
                if "members" in gesetzt
                else None
            ),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    alle = qualifikation_service.templates(include_inactive=True)
    return _t_out(next(t for t in alle if t.id == template_id))
