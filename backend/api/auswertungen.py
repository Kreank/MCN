"""Auswertungen-API — rein lesende Aggregations-Endpoints (Dashboards).

Ausschließlich GET; keine Schreibpfade. In der Dev-Phase ohne Auth (wie die
übrigen Leseendpunkte); das spätere Zugriffsrecht („kennzahlengated") kommt mit
dem Rechte-/Auth-Slice. Beträge sind Strings (Decimal, verlustfrei).
"""
from datetime import date

from ninja import Query, Router, Schema

from api.permissions import check, require
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


class CustomerRevenueOut(Schema):
    party_id: str
    display_name: str
    net_total: str
    gross_total: str
    invoice_count: int
    credit_count: int


class KundenOut(Schema):
    filters: FiltersOut
    customer_count: int
    net_total: str
    customers: list[CustomerRevenueOut]


class ProjektStatusOut(Schema):
    status: str
    count: int
    net_total: str


class ThroughputOut(Schema):
    avg_open_age_days: float | None = None
    avg_closed_duration_days: float | None = None


class TopProjektOut(Schema):
    project_id: str
    project_number: str
    name: str
    net_total: str


class ProjekteOut(Schema):
    filters: FiltersOut
    total: int
    open: int
    closed: int
    by_status: list[ProjektStatusOut]
    throughput: ThroughputOut
    top_projects: list[TopProjektOut]


class ArtikelPositionOut(Schema):
    description: str
    count: int
    quantity_total: str
    net_total: str


class ArtikelTypOut(Schema):
    line_type: str
    count: int
    net_total: str


class ArtikelOut(Schema):
    filters: FiltersOut
    line_count: int
    net_total: str
    by_type: list[ArtikelTypOut]
    articles: list[ArtikelPositionOut]


class MitarbeiterZeileOut(Schema):
    employee_id: str
    employee_number: str
    display_name: str
    worked_hours: str
    vacation_entitlement: str
    vacation_used: str
    vacation_remaining: str


class AbsenceTypOut(Schema):
    absence_type: str
    days: str
    count: int


class MitarbeitendeOut(Schema):
    year: int
    employee_count: int
    total_worked_hours: str
    total_absence_days: str
    people: list[MitarbeiterZeileOut]
    absence_by_type: list[AbsenceTypOut]


class DashboardFilter(Schema):
    date_from: date | None = None
    date_to: date | None = None


class YearFilter(Schema):
    year: int | None = None


@router.get("/dashboards", response=list[DashboardOut])
def list_dashboards(request):
    """Verfügbare Auswertungs-Dashboards (Landing).

    Das Mitarbeitenden-Dashboard erscheint nur, wenn das Konto das hr-Recht hat
    (Personaldaten) — sonst böte die Landing eine Kachel an, die beim Öffnen mit
    403 abgewiesen würde."""
    require(request, "invoicing", "LESEN")
    hr_allowed = check(request, "hr", "LESEN") is not None
    return auswertungen_service.list_dashboards(hr_allowed=hr_allowed)


@router.get("/umsatz-projektuebersicht", response=UmsatzProjektOut)
def umsatz_projektuebersicht(request, filters: DashboardFilter = Query(...)):
    """Umsatzkennzahlen + Umsatzverlauf + Projektzahlen nach Gewerk.

    Optionale Filter date_from/date_to (Belegdatum für Umsatz,
    Erstellungsdatum für Projekte)."""
    require(request, "invoicing", "LESEN")
    return auswertungen_service.umsatz_projektuebersicht_summary(
        date_from=filters.date_from, date_to=filters.date_to
    )


@router.get("/kunden", response=KundenOut)
def kunden(request, filters: DashboardFilter = Query(...)):
    """Umsatz und Rechnungsanzahl je Kunde (primärer Rechnungsschuldner),
    Top-N nach Netto-Umsatz. Optionale Filter date_from/date_to (Belegdatum)."""
    require(request, "invoicing", "LESEN")
    return auswertungen_service.kunden_summary(
        date_from=filters.date_from, date_to=filters.date_to
    )


@router.get("/projekte", response=ProjekteOut)
def projekte(request, filters: DashboardFilter = Query(...)):
    """Projekte: Anzahl/Volumen nach Status, angenäherte Durchlaufzeit und
    Top-Projekte nach Netto-Umsatz.

    Recht `invoicing/LESEN` wie die übrigen Umsatz-Dashboards — der Kern ist der
    Umsatz je Projekt (invoicing-sensibel). Optionale Filter date_from/date_to
    (Belegdatum) wirken auf die Umsatzzahlen."""
    require(request, "invoicing", "LESEN")
    return auswertungen_service.projekte_summary(
        date_from=filters.date_from, date_to=filters.date_to
    )


@router.get("/artikel", response=ArtikelOut)
def artikel(request, filters: DashboardFilter = Query(...)):
    """Meistverwendete Positionen und Umsatz je Artikel/Leistung aus den
    Belegzeilen veröffentlichter Rechnungen.

    Recht `invoicing/LESEN` (zeigt Umsätze). Korrekturbelege sind ausgeschlossen
    (nicht positionsweise zurechenbar). Optionale Filter date_from/date_to
    (Belegdatum)."""
    require(request, "invoicing", "LESEN")
    return auswertungen_service.artikel_summary(
        date_from=filters.date_from, date_to=filters.date_to
    )


@router.get("/mitarbeitende", response=MitarbeitendeOut)
def mitarbeitende(request, filters: YearFilter = Query(...)):
    """Mitarbeitenden-Auswertung für ein Kalenderjahr: Auslastung (Ist-Zeiten),
    Urlaubsverbrauch je Person und Abwesenheiten NACH ART (unternehmensweit
    aggregiert, Krankheit nie je Person).

    Recht `hr/LESEN` — Personaldaten (DSGVO Art. 9). Nur ADMINISTRATION/
    GESCHAEFTSFUEHRUNG haben das hr-Recht; NUR_LESEN und DISPOSITION bekommen
    403. Default-Jahr ist das laufende Kalenderjahr."""
    require(request, "hr", "LESEN")
    year = filters.year or date.today().year
    return auswertungen_service.mitarbeitende_summary(year=year)
