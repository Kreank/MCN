"""Firmeneinstellungen-API — Firmenprofil (Singleton), Niederlassungen, Gewerke.

Modul `company` in der Rechtematrix (Migration 0024 db_core): LESEN für alle
Rollen (das Firmenprofil steht auf jedem Beleg), Ändern/Anlegen nur
ADMINISTRATION/GESCHAEFTSFUEHRUNG. Schreibende Endpunkte laufen über den
firma-Service (business_transaction); Fachfehler → 422.
"""
from uuid import UUID

from django.http import HttpResponse
from ninja import File as NinjaFile
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile
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
    # DATEV-Export-Konfiguration (0051)
    datev_consultant_number: str | None = None
    datev_client_number: str | None = None
    datev_chart_of_accounts: str | None = None
    datev_account_length: int | None = None
    datev_fiscal_year_start_month: int | None = None
    datev_debtor_account: str | None = None
    datev_revenue_account_full: str | None = None
    datev_revenue_account_reduced: str | None = None
    datev_revenue_account_free: str | None = None
    datev_revenue_account_reverse: str | None = None
    # Abschlags-Kontierung (0063): ERLOES (Teilleistung, Default) | ANZAHLUNG
    datev_advance_mode: str | None = None
    datev_advance_account_full: str | None = None
    datev_advance_account_reduced: str | None = None
    datev_advance_account_free: str | None = None
    datev_advance_account_reverse: str | None = None
    # Additiv: OB ein Firmenlogo hinterlegt ist (die Bytes holt GET /profile/logo).
    has_logo: bool = False
    # Gesetzt, wenn eine Bankdaten-Änderung einen Vier-Augen-Antrag ausgelöst hat
    # (BANKDATEN): die IBAN/BIC-Änderung wurde NICHT geschrieben, sondern wartet
    # auf Genehmigung. Die übrigen Felder sind bereits übernommen.
    pending_bank_approval: UUID | None = None


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
    datev_consultant_number: str | None = None
    datev_client_number: str | None = None
    datev_chart_of_accounts: str | None = None
    datev_account_length: int | None = None
    datev_fiscal_year_start_month: int | None = None
    datev_debtor_account: str | None = None
    datev_revenue_account_full: str | None = None
    datev_revenue_account_reduced: str | None = None
    datev_revenue_account_free: str | None = None
    datev_revenue_account_reverse: str | None = None
    datev_advance_mode: str | None = None
    datev_advance_account_full: str | None = None
    datev_advance_account_reduced: str | None = None
    datev_advance_account_free: str | None = None
    datev_advance_account_reverse: str | None = None


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


class OnboardingOut(Schema):
    firmenprofil: bool
    logo: bool
    bankdaten: bool
    mailkonto: bool
    kontakt: bool
    liegenschaft: bool
    projekt: bool
    beleg: bool


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


class AcquisitionSourceOut(Schema):
    id: UUID
    code: str
    label: str
    active: bool
    sort_order: int


class AcquisitionSourceIn(Schema):
    code: str
    label: str
    sort_order: int = 0


class AcquisitionSourcePatch(Schema):
    label: str | None = None
    active: bool | None = None
    sort_order: int | None = None


# --- Mapper ----------------------------------------------------------------

def _profile_out(p, pending_bank_approval=None):
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
        datev_consultant_number=p.datev_consultant_number,
        datev_client_number=p.datev_client_number,
        datev_chart_of_accounts=p.datev_chart_of_accounts,
        datev_account_length=p.datev_account_length,
        datev_fiscal_year_start_month=p.datev_fiscal_year_start_month,
        datev_debtor_account=p.datev_debtor_account,
        datev_revenue_account_full=p.datev_revenue_account_full,
        datev_revenue_account_reduced=p.datev_revenue_account_reduced,
        datev_revenue_account_free=p.datev_revenue_account_free,
        datev_revenue_account_reverse=p.datev_revenue_account_reverse,
        datev_advance_mode=p.datev_advance_mode,
        datev_advance_account_full=p.datev_advance_account_full,
        datev_advance_account_reduced=p.datev_advance_account_reduced,
        datev_advance_account_free=p.datev_advance_account_free,
        datev_advance_account_reverse=p.datev_advance_account_reverse,
        has_logo=p.logo_file_id is not None,
        pending_bank_approval=pending_bank_approval,
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


@router.get("/onboarding", response=OnboardingOut)
def get_onboarding(request):
    """Erste-Schritte-Fortschritt (LESEN für alle Rollen). Nur Ja/Nein-Flags je
    Meilenstein, keine Zahlen und keine fremden Daten."""
    require(request, "company", "LESEN")
    return OnboardingOut(**firma_service.onboarding_status())


@router.put("/profile", response=CompanyProfileOut, auth=django_auth)
def put_profile(request, payload: CompanyProfileIn):
    """Firmenprofil anlegen/ändern (nur ADMINISTRATION/GESCHAEFTSFUEHRUNG)."""
    actor, _ = require(request, "company", "AENDERN")
    # Nur gesetzte Felder übernehmen (partielles Update; unset bleibt unverändert).
    fields = payload.model_dump(exclude_unset=True)
    try:
        profile, pending = firma_service.update_company_profile(actor, **fields)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _profile_out(profile, pending_bank_approval=pending.id if pending else None)


# --- Firmenlogo ------------------------------------------------------------
# Das Logo erscheint im Kopf der Beleg-PDFs. Upload/Entfernen nur mit
# company/AENDERN, Abruf mit company/LESEN. Der Abruf läuft durch die Anwendung
# (nie als vorsignierte Direkt-URL), analog zum Datei-Download.

@router.post("/profile/logo", response=CompanyProfileOut, auth=django_auth)
def upload_logo(request, datei: UploadedFile = NinjaFile(...)):
    """Firmenlogo hochladen/ersetzen (nur ADMINISTRATION/GESCHAEFTSFUEHRUNG).

    Nur PNG/JPEG und höchstens 2 MB; ungültiger Typ/zu groß → 422. Der Typ wird
    aus dem Inhalt bestimmt, nicht aus dem gemeldeten Content-Type.
    """
    actor, _ = require(request, "company", "AENDERN")
    try:
        profile = firma_service.set_company_logo(
            actor, dateiname=datei.name, inhalt=datei.read()
        )
    except firma_service.LogoFehler as exc:
        raise HttpError(422, str(exc))
    return _profile_out(profile)


@router.delete("/profile/logo", response=CompanyProfileOut, auth=django_auth)
def delete_logo(request):
    """Firmenlogo entfernen (nur ADMINISTRATION/GESCHAEFTSFUEHRUNG).

    Setzt logo_file_id auf NULL; die Datei selbst bleibt im Objektspeicher
    (unveränderlich, GoBD). Idempotent.
    """
    actor, _ = require(request, "company", "AENDERN")
    try:
        profile = firma_service.remove_company_logo(actor)
    except firma_service.LogoFehler as exc:
        raise HttpError(422, str(exc))
    return _profile_out(profile)


@router.get("/profile/logo")
def get_logo(request):
    """Bytes des Firmenlogos (LESEN für alle Rollen) — durch die Anwendung, nie
    als Direkt-URL. 404, wenn kein Logo gesetzt oder gerade nicht abrufbar ist.

    `inline` (Vorschau) mit `nosniff`: da nur PNG/JPEG (inhaltsgeprüft) abgelegt
    werden, kann so kein aktiver Inhalt im Ursprung der Anwendung ausgeführt werden.
    """
    require(request, "company", "LESEN")
    try:
        datei, inhalt = firma_service.company_logo_inhalt()
    except firma_service.LogoFehler as exc:
        raise HttpError(404, str(exc))
    antwort = HttpResponse(inhalt, content_type=datei.mime_type)
    antwort["Content-Disposition"] = 'inline; filename="firmenlogo"'
    antwort["X-Content-Type-Options"] = "nosniff"
    return antwort


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


# --- Akquisekanäle / Quellen -----------------------------------------------

def _source_out(s):
    return AcquisitionSourceOut(
        id=s.id, code=s.code, label=s.label, active=s.active, sort_order=s.sort_order
    )


@router.get("/acquisition-sources", response=list[AcquisitionSourceOut])
def list_acquisition_sources(request, include_inactive: bool = True):
    require(request, "company", "LESEN")
    return [_source_out(s) for s in firma_service.list_acquisition_sources(
        include_inactive=include_inactive)]


@router.post("/acquisition-sources", response={201: AcquisitionSourceOut}, auth=django_auth)
def create_acquisition_source(request, payload: AcquisitionSourceIn):
    actor = require_create(request, "company", "ANLEGEN")
    try:
        source = firma_service.create_acquisition_source(actor, **payload.model_dump())
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _source_out(source))


@router.put("/acquisition-sources/{source_id}", response=AcquisitionSourceOut, auth=django_auth)
def update_acquisition_source(request, source_id: UUID, payload: AcquisitionSourcePatch):
    actor, _ = require(request, "company", "AENDERN")
    try:
        source = firma_service.update_acquisition_source(
            actor, source_id=source_id, **payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _source_out(source)
