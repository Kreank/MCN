"""Auswertungen-API — rein lesende Aggregations-Endpoints (Dashboards).

Ausschließlich GET; keine Schreibpfade. In der Dev-Phase ohne Auth (wie die
übrigen Leseendpunkte); das spätere Zugriffsrecht („kennzahlengated") kommt mit
dem Rechte-/Auth-Slice. Beträge sind Strings (Decimal, verlustfrei).
"""
import csv
import io
from datetime import date

from django.http import HttpResponse
from ninja import Query, Router, Schema

from api.permissions import check, require
from db_core.services import auswertungen as auswertungen_service

router = Router()

# ---------------------------------------------------------------------------
# CSV-Serialisierung (deutsches Excel-Format)
# ---------------------------------------------------------------------------
# Trenner Semikolon, UTF-8 MIT BOM (damit Excel de Umlaute erkennt), Zahlen im
# deutschen Format (Komma-Dezimal, keine Tausendertrenner — die stören den
# Excel-Import). Werte mit ;/"/Zeilenumbruch werden über das csv-Modul
# (QUOTE_MINIMAL) korrekt gequotet. Die Zeilen/Spalten liefert der Service
# (auswertungen_service.build_*_export) — hier wird nur formatiert und geschrieben.

# Spaltenarten, die als deutsche Dezimalzahl (Komma) ausgegeben werden.
_NUMERISCH = {"geld", "prozent", "menge", "stunden", "dezimal"}


def _fmt_zahl(wert):
    """Decimal-/Zahl-String → deutsches Format (Punkt→Komma). None → „unbekannt".

    None steht für „unbekannt" (fehlender EK bei der Marge, kein Anteil bei
    Nullsumme) — bewusst nicht 0 (Ehrlichkeit wie im Dashboard). Der Wert kommt
    als verlustfreier Decimal-String; das Komma entsteht erst hier."""
    if wert is None:
        return "unbekannt"
    return str(wert).replace(".", ",")


def _fmt_zelle(wert, kind):
    if kind in _NUMERISCH:
        return _fmt_zahl(wert)
    if kind == "ganzzahl":
        return "" if wert is None else str(wert)
    if kind == "datum":
        return "" if wert is None else wert.strftime("%d.%m.%Y")
    if kind == "bool":
        if wert is None:
            return "unbekannt"
        return "Ja" if wert else "Nein"
    return "" if wert is None else str(wert)


def render_csv(tabelle):
    """Serialisiert eine CsvTabelle in eine Excel-taugliche CSV-Antwort (de-DE).

    Mehrere Blöcke werden durch eine Leerzeile getrennt; jeder Block schreibt
    optional seinen Titel, dann die Kopfzeile, dann die Datenzeilen. Eine Zelle
    kann als `auswertungen_service.Zelle(wert, kind)` ihre eigene Formatierungsart
    tragen (Kennzahlen-Blöcke); sonst gilt die Spaltenart."""
    puffer = io.StringIO()
    writer = csv.writer(puffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    for i, block in enumerate(tabelle.bloecke):
        if i > 0:
            writer.writerow([])
        if block.titel:
            writer.writerow([block.titel])
        writer.writerow([s.header for s in block.spalten])
        for zeile in block.zeilen:
            ausgabe = []
            for spalte, zelle in zip(block.spalten, zeile):
                if isinstance(zelle, auswertungen_service.Zelle):
                    wert, kind = zelle
                else:
                    wert, kind = zelle, spalte.kind
                ausgabe.append(_fmt_zelle(wert, kind))
            writer.writerow(ausgabe)
    # utf-8-sig schreibt das BOM voran (Excel de erkennt sonst keine Umlaute).
    inhalt = puffer.getvalue().encode("utf-8-sig")
    antwort = HttpResponse(inhalt, content_type="text/csv; charset=utf-8")
    antwort["Content-Disposition"] = f'attachment; filename="{tabelle.dateiname}.csv"'
    antwort["X-Content-Type-Options"] = "nosniff"
    return antwort


class DashboardOut(Schema):
    key: str
    title: str
    description: str
    available: bool


class MargeOut(Schema):
    """Deckungsbeitrag/Marge-Block. Alle Beträge Strings (Decimal).

    `deckungsbeitrag`/`marge_prozent` sind None, wenn keine Position einen EK
    trägt (= unbekannt, NICHT 0). Sie beziehen sich stets nur auf `net_mit_ek`;
    `net_ohne_ek`/`positionen_ohne_ek` weisen die Lücke aus. `ek_vollstaendig`
    sagt, ob jede summenwirksame Position einen EK hat (dann ist die Marge
    belastbar)."""

    net_total: str
    net_mit_ek: str
    net_ohne_ek: str
    ek_total: str
    deckungsbeitrag: str | None = None
    marge_prozent: str | None = None
    positionen: int
    positionen_ohne_ek: int
    ek_vollstaendig: bool


class MargeGewerkOut(MargeOut):
    name: str


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
    marge_sichtbar: bool
    marge: MargeOut | None = None
    marge_by_gewerk: list[MargeGewerkOut] = []


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
    # Realisierte Marge (nur mit pricing/LESEN; sonst None).
    ek_total: str | None = None
    deckungsbeitrag: str | None = None
    marge_prozent: str | None = None
    positionen_ohne_ek: int | None = None
    ek_vollstaendig: bool | None = None


class ProjekteOut(Schema):
    filters: FiltersOut
    total: int
    open: int
    closed: int
    by_status: list[ProjektStatusOut]
    throughput: ThroughputOut
    top_projects: list[TopProjektOut]
    marge_sichtbar: bool
    marge: MargeOut | None = None
    geplante_marge: MargeOut | None = None


class ArtikelPositionOut(Schema):
    description: str
    count: int
    quantity_total: str
    net_total: str
    # Marge je Position (nur mit pricing/LESEN; sonst None).
    ek_total: str | None = None
    deckungsbeitrag: str | None = None
    marge_prozent: str | None = None
    positionen_ohne_ek: int | None = None
    ek_vollstaendig: bool | None = None


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
    marge_sichtbar: bool
    marge: MargeOut | None = None


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

    Deckungsbeitrag und Marge (Gesamt und je Gewerk) nur mit `pricing/LESEN`
    (EK-Daten); sonst bleibt der Umsatz sichtbar und `marge_sichtbar=False`.
    Optionale Filter date_from/date_to (Belegdatum für Umsatz,
    Erstellungsdatum für Projekte)."""
    require(request, "invoicing", "LESEN")
    ek_allowed = check(request, "pricing", "LESEN") is not None
    return auswertungen_service.umsatz_projektuebersicht_summary(
        date_from=filters.date_from, date_to=filters.date_to, ek_allowed=ek_allowed
    )


@router.get("/umsatz-projektuebersicht/export.csv")
def umsatz_projektuebersicht_export(request, filters: DashboardFilter = Query(...)):
    """CSV-Export der Umsatz-/Projektübersicht (gleiche Filter + gleiches Recht).

    Marge-Spalten (nach Gewerk) nur mit `pricing/LESEN` — sonst weggelassen,
    kein 403."""
    require(request, "invoicing", "LESEN")
    ek_allowed = check(request, "pricing", "LESEN") is not None
    tabelle = auswertungen_service.build_umsatz_export(
        date_from=filters.date_from, date_to=filters.date_to, ek_allowed=ek_allowed
    )
    return render_csv(tabelle)


@router.get("/kunden", response=KundenOut)
def kunden(request, filters: DashboardFilter = Query(...)):
    """Umsatz und Rechnungsanzahl je Kunde (primärer Rechnungsschuldner),
    Top-N nach Netto-Umsatz. Optionale Filter date_from/date_to (Belegdatum)."""
    require(request, "invoicing", "LESEN")
    return auswertungen_service.kunden_summary(
        date_from=filters.date_from, date_to=filters.date_to
    )


@router.get("/kunden/export.csv")
def kunden_export(request, filters: DashboardFilter = Query(...)):
    """CSV-Export des Kunden-Dashboards (Umsatz/Anteil je Kunde). Recht wie oben."""
    require(request, "invoicing", "LESEN")
    tabelle = auswertungen_service.build_kunden_export(
        date_from=filters.date_from, date_to=filters.date_to
    )
    return render_csv(tabelle)


@router.get("/projekte", response=ProjekteOut)
def projekte(request, filters: DashboardFilter = Query(...)):
    """Projekte: Anzahl/Volumen nach Status, angenäherte Durchlaufzeit und
    Top-Projekte nach Netto-Umsatz.

    Recht `invoicing/LESEN` wie die übrigen Umsatz-Dashboards — der Kern ist der
    Umsatz je Projekt (invoicing-sensibel). Optionale Filter date_from/date_to
    (Belegdatum) wirken auf die Umsatzzahlen."""
    require(request, "invoicing", "LESEN")
    ek_allowed = check(request, "pricing", "LESEN") is not None
    return auswertungen_service.projekte_summary(
        date_from=filters.date_from, date_to=filters.date_to, ek_allowed=ek_allowed
    )


@router.get("/projekte/export.csv")
def projekte_export(request, filters: DashboardFilter = Query(...)):
    """CSV-Export des Projekte-Dashboards. Marge-Spalten nur mit `pricing/LESEN`."""
    require(request, "invoicing", "LESEN")
    ek_allowed = check(request, "pricing", "LESEN") is not None
    tabelle = auswertungen_service.build_projekte_export(
        date_from=filters.date_from, date_to=filters.date_to, ek_allowed=ek_allowed
    )
    return render_csv(tabelle)


@router.get("/artikel", response=ArtikelOut)
def artikel(request, filters: DashboardFilter = Query(...)):
    """Meistverwendete Positionen und Umsatz je Artikel/Leistung aus den
    Belegzeilen veröffentlichter Rechnungen.

    Recht `invoicing/LESEN` (zeigt Umsätze). Korrekturbelege sind ausgeschlossen
    (nicht positionsweise zurechenbar). Optionale Filter date_from/date_to
    (Belegdatum)."""
    require(request, "invoicing", "LESEN")
    ek_allowed = check(request, "pricing", "LESEN") is not None
    return auswertungen_service.artikel_summary(
        date_from=filters.date_from, date_to=filters.date_to, ek_allowed=ek_allowed
    )


@router.get("/artikel/export.csv")
def artikel_export(request, filters: DashboardFilter = Query(...)):
    """CSV-Export des Artikel-/Leistungs-Dashboards. Ø-Marge nur mit `pricing/LESEN`."""
    require(request, "invoicing", "LESEN")
    ek_allowed = check(request, "pricing", "LESEN") is not None
    tabelle = auswertungen_service.build_artikel_export(
        date_from=filters.date_from, date_to=filters.date_to, ek_allowed=ek_allowed
    )
    return render_csv(tabelle)


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


@router.get("/mitarbeitende/export.csv")
def mitarbeitende_export(request, filters: YearFilter = Query(...)):
    """CSV-Export der Mitarbeitenden-Auswertung eines Jahres. Recht `hr/LESEN`
    (Personaldaten). Abwesenheiten nach Art bleiben unternehmensweit aggregiert."""
    require(request, "hr", "LESEN")
    year = filters.year or date.today().year
    tabelle = auswertungen_service.build_mitarbeitende_export(year=year)
    return render_csv(tabelle)
