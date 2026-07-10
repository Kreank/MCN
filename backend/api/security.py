"""Security-API — Rechtematrix-Pflege, Rollenzuordnungen und Vier-Augen-Freigaben.

Rechte-Tore (Modul `security`):
  * Lesen (Rollen, Matrix, Benutzer, Zuordnungen, Anträge): `LESEN`.
  * Matrix-Zelle ändern, Rolle zuweisen/beenden: `AENDERN`.
  * Freigabeantrag entscheiden (genehmigen/ablehnen): `FREIGEBEN`.
  * Freigabeantrag stellen/zurückziehen: `ANLEGEN`.

Startmatrix (Migration 0026): `security`-Schreibrechte (ANLEGEN/AENDERN/FREIGEBEN)
haben ausschließlich ADMINISTRATION und GESCHAEFTSFUEHRUNG — also gibt es
Entscheider für Vier-Augen-Anträge, ohne die Matrix anpassen zu müssen. Die
Lese-Endpunkte laufen über `security/LESEN` (das auch NUR_LESEN hält): die
Konfiguration ist einsehbar, das Ändern bleibt den beiden Rollen vorbehalten.
"""
from datetime import date
from uuid import UUID

from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import check, require, require_create
from db_core.models import AppUser
from db_core.services import rechte as rechte_service
from db_core.services import rechte_pflege
from db_core.services import vier_augen

router = Router()


# --- Schemas: Rollen & Matrix ----------------------------------------------

class RoleOut(Schema):
    code: str
    label: str


class PermissionCellOut(Schema):
    role_code: str
    module: str
    action: str
    allowed: bool
    row_scope: str


class PermissionMatrixOut(Schema):
    modules: list[str]
    actions: list[str]
    roles: list[RoleOut]
    cells: list[PermissionCellOut]


class PermissionSetIn(Schema):
    role_code: str
    module: str
    action: str
    allowed: bool
    row_scope: str = "ALLE"


# --- Schemas: Benutzer & Rollenzuordnungen ---------------------------------

class AppUserOut(Schema):
    id: UUID
    display_name: str
    status: str
    roles: list[str]


class UserRoleOut(Schema):
    id: UUID
    user_id: UUID
    user_name: str
    role_code: str
    role_label: str
    valid_from: date
    valid_until: date | None = None
    is_active: bool


class UserRoleAssignIn(Schema):
    user_id: UUID
    role_code: str
    valid_from: date | None = None


class UserRoleEndIn(Schema):
    valid_until: date | None = None


# --- Schemas: Vier-Augen-Anträge -------------------------------------------

class ApprovalOut(Schema):
    id: UUID
    action_code: str
    action_label: str
    status: str
    # Der Payload trägt die beantragte Änderung im Klartext (z. B. eine neue
    # IBAN). `security/LESEN` hat auch NUR_LESEN — deshalb wird er nur an den
    # Antragsteller und an Entscheider (`security/FREIGEBEN`) ausgeliefert.
    # `payload_verborgen` sagt dem UI, dass es nichts anzuzeigen gibt.
    payload: dict
    payload_verborgen: bool = False
    target_table: str | None = None
    target_id: UUID | None = None
    reason: str | None = None
    requested_by: UUID
    requested_by_name: str | None = None
    requested_at: str
    decided_by: UUID | None = None
    decided_by_name: str | None = None
    decided_at: str | None = None
    decision_note: str | None = None
    applied_at: str | None = None


class ApprovalDecideIn(Schema):
    note: str | None = None


# --- Mapper ----------------------------------------------------------------

def _payload_sichtbar(a, actor, darf_entscheiden):
    """Wer die beantragte Änderung im Klartext sehen darf: der Antragsteller
    (es sind seine Daten) und jeder, der entscheiden darf — er muss prüfen
    können, worüber er entscheidet. Sonst niemand."""
    return darf_entscheiden or str(a.requested_by_id) == str(actor)


def _approval_out(a, *, payload_sichtbar=True):
    return ApprovalOut(
        id=a.id,
        action_code=a.action_id,
        action_label=a.action.label if a.action_id else a.action_id,
        status=a.status,
        payload=(a.payload or {}) if payload_sichtbar else {},
        payload_verborgen=not payload_sichtbar,
        target_table=a.target_table,
        target_id=a.target_id,
        reason=a.reason,
        requested_by=a.requested_by_id,
        requested_by_name=a.requested_by.display_name if a.requested_by_id else None,
        requested_at=a.requested_at.isoformat() if a.requested_at else None,
        decided_by=a.decided_by_id,
        decided_by_name=a.decided_by.display_name if a.decided_by_id else None,
        decided_at=a.decided_at.isoformat() if a.decided_at else None,
        decision_note=a.decision_note,
        applied_at=a.applied_at.isoformat() if a.applied_at else None,
    )


# --- Rollen & Matrix -------------------------------------------------------

@router.get("/roles", response=list[RoleOut])
def list_roles(request):
    require(request, "security", "LESEN")
    return [RoleOut(code=r.code, label=r.label) for r in rechte_pflege.list_roles()]


@router.get("/permissions", response=PermissionMatrixOut)
def get_permissions(request):
    """Vollständige Rechtematrix (Rolle × Modul × Aktion)."""
    require(request, "security", "LESEN")
    roles = [RoleOut(code=r.code, label=r.label) for r in rechte_pflege.list_roles()]
    cells = [
        PermissionCellOut(
            role_code=p.role_id, module=p.module, action=p.action,
            allowed=p.allowed, row_scope=p.row_scope,
        )
        for p in rechte_pflege.permission_rows()
    ]
    return PermissionMatrixOut(
        modules=list(rechte_service.MODULES),
        actions=list(rechte_service.ACTIONS),
        roles=roles,
        cells=cells,
    )


@router.put("/permissions", response=PermissionCellOut, auth=django_auth)
def set_permission(request, payload: PermissionSetIn):
    """Setzt eine Matrix-Zelle (allowed + row_scope). Keine Selbst-Erweiterung."""
    actor, _ = require(request, "security", "AENDERN")
    try:
        row = rechte_pflege.set_permission(
            actor, role_code=payload.role_code, module=payload.module,
            action=payload.action, allowed=payload.allowed,
            row_scope=payload.row_scope,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return PermissionCellOut(
        role_code=row.role_id, module=row.module, action=row.action,
        allowed=row.allowed, row_scope=row.row_scope,
    )


# --- Benutzer & Rollenzuordnungen ------------------------------------------

@router.get("/users", response=list[AppUserOut])
def list_users(request):
    """Alle fachlichen Benutzer (app_user) samt aktiver Rollen — für die
    Rollenzuweisung."""
    require(request, "security", "LESEN")
    today = date.today()
    active = {}
    for ur in rechte_pflege.list_user_roles(active_only=True, on=today):
        active.setdefault(ur.user_id, []).append(ur.role_id)
    return [
        AppUserOut(
            id=u.id, display_name=u.display_name, status=u.status,
            roles=sorted(active.get(u.id, [])),
        )
        for u in AppUser.objects.order_by("display_name")
    ]


@router.get("/user-roles", response=list[UserRoleOut])
def list_user_roles(request, active_only: bool = Query(False)):
    require(request, "security", "LESEN")
    today = date.today()
    out = []
    for ur in rechte_pflege.list_user_roles(active_only=active_only, on=today):
        is_active = ur.valid_from <= today and (
            ur.valid_until is None or ur.valid_until > today
        )
        out.append(
            UserRoleOut(
                id=ur.id, user_id=ur.user_id, user_name=ur.user.display_name,
                role_code=ur.role_id, role_label=ur.role.label,
                valid_from=ur.valid_from, valid_until=ur.valid_until,
                is_active=is_active,
            )
        )
    return out


@router.post("/user-roles", response={201: UserRoleOut}, auth=django_auth)
def assign_role(request, payload: UserRoleAssignIn):
    """Weist einem Benutzer eine Rolle zu. Keine Selbstzuweisung."""
    actor, _ = require(request, "security", "AENDERN")
    try:
        ur = rechte_pflege.assign_role(
            actor, user_id=payload.user_id, role_code=payload.role_code,
            valid_from=payload.valid_from,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    ur = rechte_pflege.list_user_roles().get(id=ur.id)
    today = date.today()
    is_active = ur.valid_from <= today and (
        ur.valid_until is None or ur.valid_until > today
    )
    return Status(
        201,
        UserRoleOut(
            id=ur.id, user_id=ur.user_id, user_name=ur.user.display_name,
            role_code=ur.role_id, role_label=ur.role.label,
            valid_from=ur.valid_from, valid_until=ur.valid_until,
            is_active=is_active,
        ),
    )


@router.post("/user-roles/{user_role_id}/end", response=UserRoleOut, auth=django_auth)
def end_user_role(request, user_role_id: UUID, payload: UserRoleEndIn):
    """Beendet eine Rollenzuordnung (valid_until; kein Löschen). Die letzte
    ADMINISTRATION-Zuordnung lässt sich nicht beenden."""
    actor, _ = require(request, "security", "AENDERN")
    try:
        ur = rechte_pflege.end_user_role(
            actor, user_role_id=user_role_id, valid_until=payload.valid_until
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    ur = rechte_pflege.list_user_roles().get(id=ur.id)
    today = date.today()
    is_active = ur.valid_from <= today and (
        ur.valid_until is None or ur.valid_until > today
    )
    return UserRoleOut(
        id=ur.id, user_id=ur.user_id, user_name=ur.user.display_name,
        role_code=ur.role_id, role_label=ur.role.label,
        valid_from=ur.valid_from, valid_until=ur.valid_until, is_active=is_active,
    )


# --- Vier-Augen-Anträge ----------------------------------------------------

@router.get("/approvals", response=list[ApprovalOut])
def list_approvals(request, status: str | None = Query(None)):
    """Freigabeanträge, optional nach Status gefiltert (z. B. ANGEFORDERT).

    Der Payload wird nur für eigene Anträge und für Entscheider ausgeliefert —
    `security/LESEN` allein (das auch NUR_LESEN hält) genügt dafür nicht.
    """
    actor, _ = require(request, "security", "LESEN")
    darf_entscheiden = check(request, "security", "FREIGEBEN") is not None
    return [
        _approval_out(
            a, payload_sichtbar=_payload_sichtbar(a, actor, darf_entscheiden)
        )
        for a in vier_augen.list_requests(status=status)
    ]


@router.post("/approvals/{request_id}/approve", response=ApprovalOut, auth=django_auth)
def approve_request(request, request_id: UUID):
    """Genehmigt einen Antrag. Der Entscheider muss ein anderer sein als der
    Antragsteller (Vier-Augen-Prinzip → 422)."""
    actor, _ = require(request, "security", "FREIGEBEN")
    try:
        req = vier_augen.approve(actor, request_id=request_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    from db_core.models import ApprovalRequest

    req = ApprovalRequest.objects.select_related(
        "action", "requested_by", "decided_by"
    ).get(id=req.id)
    return _approval_out(req)


@router.post("/approvals/{request_id}/reject", response=ApprovalOut, auth=django_auth)
def reject_request(request, request_id: UUID, payload: ApprovalDecideIn):
    """Lehnt einen Antrag ab — begründungspflichtig (422 ohne Begründung)."""
    actor, _ = require(request, "security", "FREIGEBEN")
    try:
        req = vier_augen.reject(actor, request_id=request_id, note=payload.note)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    from db_core.models import ApprovalRequest

    req = ApprovalRequest.objects.select_related(
        "action", "requested_by", "decided_by"
    ).get(id=req.id)
    return _approval_out(req)


@router.post("/approvals/{request_id}/withdraw", response=ApprovalOut, auth=django_auth)
def withdraw_request(request, request_id: UUID):
    """Zieht den eigenen Antrag zurück (nur der Antragsteller)."""
    actor = require_create(request, "security", "ANLEGEN")
    try:
        req = vier_augen.withdraw(actor, request_id=request_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    from db_core.models import ApprovalRequest

    req = ApprovalRequest.objects.select_related(
        "action", "requested_by", "decided_by"
    ).get(id=req.id)
    return _approval_out(req)
