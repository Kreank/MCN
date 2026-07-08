"""Auswertungen-API — rein lesende Aggregations-Endpoints (Dashboards).

Ausschließlich GET; keine Schreibpfade. In der Dev-Phase ohne Auth (wie die
übrigen Leseendpunkte); das spätere Zugriffsrecht („kennzahlengated") kommt mit
dem Rechte-/Auth-Slice. Beträge sind Strings (Decimal, verlustfrei).
"""
from datetime import date

from ninja import Query, Router, Schema

from db_core.services import auswertungen as auswertungen_service

router = Router()


class DashboardOut(Schema):
    key: str
    title: str
    description: str
    available: bool


class FiltersOut(Schema):
    date_from: date | None = None
    date_to: date | None = None


class RevenueOut(Schema):
    net_total: str
    gross_total: str
    invoice_count: int
    credit_count: int


class GewerkCountOut(Schema):
    name: str
    count: int


class ProjectsOut(Schema):
    total: int
    open: int
    closed: int
    created_in_range: int
    by_gewerk: list[GewerkCountOut]


class TimelinePointOut(Schema):
    month: str
    net: str


class UmsatzProjektOut(Schema):
    filters: FiltersOut
    revenue: RevenueOut
    projects: ProjectsOut
    timeline: list[TimelinePointOut]


class DashboardFilter(Schema):
    date_from: date | None = None
    date_to: date | None = None


@router.get("/dashboards", response=list[DashboardOut])
def list_dashboards(request):
    """Verfügbare Auswertungs-Dashboards (Landing)."""
    return auswertungen_service.list_dashboards()


@router.get("/umsatz-projektuebersicht", response=UmsatzProjektOut)
def umsatz_projektuebersicht(request, filters: DashboardFilter = Query(...)):
    """Umsatzkennzahlen + Umsatzverlauf + Projektzahlen nach Gewerk.

    Optionale Filter date_from/date_to (Belegdatum für Umsatz,
    Erstellungsdatum für Projekte)."""
    return auswertungen_service.umsatz_projektuebersicht_summary(
        date_from=filters.date_from, date_to=filters.date_to
    )
