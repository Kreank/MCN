"""Firmeneinstellungen-API — Firmenprofil (Singleton), Niederlassungen, Gewerke.

Modul `company` in der Rechtematrix (Migration 0024 db_core): LESEN für alle
Rollen (das Firmenprofil steht auf jedem Beleg), Ändern/Anlegen nur
ADMINISTRATION/GESCHAEFTSFUEHRUNG. Schreibende Endpunkte laufen über den
firma-Service (business_transaction); Fachfehler → 422.
"""
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_create
from db_core.services import firma as firma_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class CompanyProfileOut(Schema):
    exists: bool
    company_name: str | None = None
    legal_form: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    state_code: str | None = None
    phone: str | None = None
    email: str | None = None
    web: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None
    commercial_register: str | None = None
    bank_name: str | None = None
    iban: str | None = None
    bic: str | None = None
    managing_director: str | None = None
    managing_director_title: str | None = None
    default_language: str | None = None
    logo_file_id: UUID | None = None


class CompanyProfileIn(Schema):
    company_name: str | None = None
    legal_form: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    state_code: str | None = None
    phone: str | None = None
    email: str | None = None
    web: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None
    commercial_register: str | None = None
    bank_name: str | None = None
    iban: str | None = None
    bic: str | None = None
    managing_director: str | None = None
    managing_director_title: str | None = None
    default_language: str | None = None


class BranchOut(Schema):
    id: UUID
    name: str
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str
    phone: str | None = None
    email: str | None = None
    active: bool


class BranchIn(Schema):
    name: str
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str = "DE"
    phone: str | None = None
    email: str | None = None


class BranchPatch(Schema):
    name: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str | None = None
    active: bool | None = None


class TradeOut(Schema):
    id: UUID
    code: str
    label: str
    active: bool
    sort_order: int


class TradeIn(Schema):
    code: str
    label: str
    sort_order: int = 0


class TradePatch(Schema):
    label: str | None = None
    active: bool | None = None
    sort_order: int | None = None


# --- Mapper ----------------------------------------------------------------

def _profile_out(p):
    if p is None:
        return CompanyProfileOut(exists=False)
    return CompanyProfileOut(
        exists=True,
        company_name=p.company_name, legal_form=p.legal_form, street=p.street,
        postal_code=p.postal_code, city=p.city, country=p.country,
        state_code=p.state_code, phone=p.phone, email=p.email, web=p.web,
        tax_number=p.tax_number, vat_id=p.vat_id,
        commercial_register=p.commercial_register, bank_name=p.bank_name,
        iban=p.iban, bic=p.bic, managing_director=p.managing_director,
        managing_director_title=p.managing_director_title,
        default_language=p.default_language, logo_file_id=p.logo_file_id,
    )


def _branch_out(b):
    return BranchOut(
        id=b.id, name=b.name, street=b.street, postal_code=b.postal_code,
        city=b.city, country=b.country, phone=b.phone, email=b.email,
        active=b.active,
    )


def _trade_out(t):
    return TradeOut(id=t.id, code=t.code, label=t.label, active=t.active,
                    sort_order=t.sort_order)


# --- Firmenprofil ----------------------------------------------------------

@router.get("/profile", response=CompanyProfileOut)
def get_profile(request):
    """Firmenprofil lesen (LESEN für alle Rollen)."""
    require(request, "company", "LESEN")
    return _profile_out(firma_service.get_company_profile())


@router.put("/profile", response=CompanyProfileOut, auth=django_auth)
def put_profile(request, payload: CompanyProfileIn):
    """Firmenprofil anlegen/ändern (nur ADMINISTRATION/GESCHAEFTSFUEHRUNG)."""
    actor, _ = require(request, "company", "AENDERN")
    # Nur gesetzte Felder übernehmen (partielles Update; unset bleibt unverändert).
    fields = payload.model_dump(exclude_unset=True)
    try:
        profile = firma_service.update_company_profile(actor, **fields)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _profile_out(profile)


# --- Niederlassungen -------------------------------------------------------

@router.get("/branches", response=list[BranchOut])
def list_branches(request, include_inactive: bool = True):
    require(request, "company", "LESEN")
    return [_branch_out(b) for b in firma_service.list_branches(
        include_inactive=include_inactive)]


@router.post("/branches", response={201: BranchOut}, auth=django_auth)
def create_branch(request, payload: BranchIn):
    actor = require_create(request, "company", "ANLEGEN")
    try:
        branch = firma_service.create_branch(actor, **payload.model_dump())
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _branch_out(branch))


@router.put("/branches/{branch_id}", response=BranchOut, auth=django_auth)
def update_branch(request, branch_id: UUID, payload: BranchPatch):
    actor, _ = require(request, "company", "AENDERN")
    try:
        branch = firma_service.update_branch(
            actor, branch_id=branch_id, **payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _branch_out(branch)


# --- Gewerke ---------------------------------------------------------------

@router.get("/trades", response=list[TradeOut])
def list_trades(request, include_inactive: bool = True):
    require(request, "company", "LESEN")
    return [_trade_out(t) for t in firma_service.list_trades(
        include_inactive=include_inactive)]


@router.post("/trades", response={201: TradeOut}, auth=django_auth)
def create_trade(request, payload: TradeIn):
    actor = require_create(request, "company", "ANLEGEN")
    try:
        trade = firma_service.create_trade(actor, **payload.model_dump())
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _trade_out(trade))


@router.put("/trades/{trade_id}", response=TradeOut, auth=django_auth)
def update_trade(request, trade_id: UUID, payload: TradePatch):
    actor, _ = require(request, "company", "AENDERN")
    try:
        trade = firma_service.update_trade(
            actor, trade_id=trade_id, **payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _trade_out(trade)
