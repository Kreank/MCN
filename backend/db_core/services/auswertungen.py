"""Auswertungen-Service: rein lesende Aggregationen (Umsatz, Projekte).

Ausschließlich lesend — Auswertungen aggregieren, sie schreiben nicht; kein
business_transaction nötig. Die Berechnungsdefinitionen folgen der Roadmap
(docs/roadmap/10-auswertungen.md):

- Umsatz = Rechnungsvolumen veröffentlichter Rechnungen (status VEROEFFENTLICHT),
  Korrekturbelege (GUTSCHRIFT/STORNO) mindern den Umsatz; Entwürfe zählen nicht.
- "Gewerk" wird über die Projektkategorie (workflow.project_category) angenähert
  — ein echtes Gewerk-Feld gibt es im Schema nicht.
- Projektabschluss = status CLOSED (das Schema führt kein eigenes Abschlussdatum;
  der Zeitraumfilter der Projekt-Kennzahlen bezieht sich auf die Erstellung).

Beträge werden als String (Decimal, verlustfrei) zurückgegeben.
"""
from datetime import date
from decimal import Decimal

from django.db.models import (
    Avg,
    Count,
    DecimalField,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Now, TruncMonth

from db_core.models import (
    Absence,
    Employee,
    EmploymentContract,
    Invoice,
    InvoiceLine,
    InvoiceParty,
    Project,
    TimeEntry,
    VacationBudget,
)

# Korrekturbelege mindern den Umsatz (kein eigener Status "storniert" im Schema).
CREDIT_TYPES = ("GUTSCHRIFT", "STORNO")

# Positionsarten ohne Betrag (reine Gliederung) — für die Artikelauswertung
# irrelevant (kein net_amount, keine Menge).
NON_AMOUNT_LINE_TYPES = ("TEXT", "ZWISCHENSUMME")

# Verfügbare Dashboards (Landing). available=False = noch nicht umgesetzt.
# Das Mitarbeitenden-Dashboard erscheint nur, wenn das Konto das hr-Recht hat
# (Personaldaten, DSGVO) — es wird in list_dashboards bedingt eingeblendet.
DASHBOARDS = [
    {
        "key": "umsatz-projektuebersicht",
        "title": "Umsatz- und Projektübersicht",
        "description": "Umsatzkennzahlen, Umsatzverlauf und Projektzahlen nach Gewerk.",
        "available": True,
    },
    {
        "key": "projekte",
        "title": "Projekte",
        "description": "Volumen und Anzahl nach Status, Durchlaufzeit, "
        "Top-Projekte nach Umsatz.",
        "available": True,
    },
    {
        "key": "kunden",
        "title": "Kunden",
        "description": "Umsatz und Rechnungen je Kunde.",
        "available": True,
    },
    {
        "key": "artikel",
        "title": "Artikel & Leistungen",
        "description": "Meistverwendete Positionen und Umsatz je Artikel/Leistung "
        "aus den Belegzeilen.",
        "available": True,
    },
]

# Nur mit hr-Recht sichtbar (Personaldaten). Separat gehalten, damit die Landing
# ihn selektiv einblenden kann, ohne die Reihenfolge der offenen Dashboards zu
# stören.
HR_DASHBOARD = {
    "key": "mitarbeitende",
    "title": "Mitarbeitende",
    "description": "Auslastung (Ist-Zeiten), Abwesenheiten und Urlaubsverbrauch.",
    "available": True,
}

_DEC = DecimalField(max_digits=15, decimal_places=2)
_ZERO = Value(Decimal("0.00"), output_field=_DEC)


def list_dashboards(*, hr_allowed=False):
    """Liste der verfügbaren Auswertungs-Dashboards (Landing).

    Das Mitarbeitenden-Dashboard (Personaldaten) wird nur aufgenommen, wenn das
    Konto das hr-Recht besitzt (hr_allowed) — sonst würde die Landing eine
    Kachel anbieten, die beim Öffnen ohnehin mit 403 abgewiesen wird.
    """
    dashboards = [dict(d) for d in DASHBOARDS]
    if hr_allowed:
        dashboards.append(dict(HR_DASHBOARD))
    return dashboards


def _apply_invoice_dates(qs, date_from, date_to):
    if date_from:
        qs = qs.filter(invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice_date__lte=date_to)
    return qs


def umsatz_projektuebersicht_summary(*, date_from=None, date_to=None):
    """Kennzahlen für das Umsatz-/Projektübersicht-Dashboard.

    date_from/date_to (date, optional) filtern Rechnungen über das Belegdatum
    und Projekte über das Erstellungsdatum.
    """
    published = _apply_invoice_dates(
        Invoice.objects.filter(status="VEROEFFENTLICHT"), date_from, date_to
    )

    not_credit = ~Q(invoice_type__in=CREDIT_TYPES)
    is_credit = Q(invoice_type__in=CREDIT_TYPES)
    # Korrekturbelege (GUTSCHRIFT/STORNO) tragen bereits NEGATIVE Summen — daher
    # über ALLE veröffentlichten Belege summieren (sie mindern automatisch); die
    # Splittung dient nur den Belegzählern.
    agg = published.aggregate(
        net_all=Coalesce(Sum("net_total"), _ZERO),
        gross_all=Coalesce(Sum("gross_total"), _ZERO),
        invoice_count=Count("id", filter=not_credit),
        credit_count=Count("id", filter=is_credit),
    )
    net_revenue = agg["net_all"]
    gross_revenue = agg["gross_all"]

    # Umsatzverlauf je Monat (Belegdatum); Korrekturen mindern über ihr Vorzeichen.
    rows = (
        published.annotate(month=TruncMonth("invoice_date"))
        .values("month")
        .annotate(net=Coalesce(Sum("net_total"), _ZERO))
        .order_by("month")
    )
    timeline = [
        {"month": r["month"].strftime("%Y-%m"), "net": str(r["net"])}
        for r in rows
        if r["month"] is not None
    ]

    # Projekte: Zeitraum über Erstellungsdatum.
    created = Project.objects.all()
    if date_from:
        created = created.filter(created_at__date__gte=date_from)
    if date_to:
        created = created.filter(created_at__date__lte=date_to)

    all_projects = Project.objects.all()
    by_gewerk = [
        {"name": r["category__name"] or "Ohne Kategorie", "count": r["c"]}
        for r in created.values("category__name")
        .annotate(c=Count("id"))
        .order_by("-c", "category__name")
    ]

    return {
        "filters": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
        "revenue": {
            "net_total": str(net_revenue),
            "gross_total": str(gross_revenue),
            "invoice_count": agg["invoice_count"],
            "credit_count": agg["credit_count"],
        },
        "projects": {
            "total": all_projects.count(),
            "open": all_projects.filter(status="OPEN").count(),
            "closed": all_projects.filter(status="CLOSED").count(),
            "created_in_range": created.count(),
            "by_gewerk": by_gewerk,
        },
        "timeline": timeline,
    }


def kunden_summary(*, date_from=None, date_to=None, limit=20):
    """Umsatz und Rechnungsanzahl je Kunde (primärer Rechnungsschuldner).

    Aggregiert veröffentlichte Rechnungen über den **primären** INVOICE_DEBTOR
    (is_primary); Korrekturbelege (GUTSCHRIFT/STORNO) mindern den Umsatz.
    date_from/date_to filtern über das Belegdatum. Sortiert nach Netto-Umsatz
    absteigend, Top-N.

    Attributionsregel: Der partielle Unique-Index lässt höchstens einen primären
    Schuldner je Rechnung zu → keine Doppelzählung. Rechnungen OHNE primären
    Schuldner (alle aktuellen Anlage-Pfade setzen is_primary=True) werden hier
    keinem Kunden zugeordnet; die kundenübergreifende Gesamtsumme (Umsatz-
    Dashboard) bleibt die maßgebliche Umsatzzahl.
    """
    not_credit = ~Q(invoice__invoice_type__in=CREDIT_TYPES)
    is_credit = Q(invoice__invoice_type__in=CREDIT_TYPES)

    qs = InvoiceParty.objects.filter(
        role="INVOICE_DEBTOR", is_primary=True, invoice__status="VEROEFFENTLICHT"
    )
    if date_from:
        qs = qs.filter(invoice__invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice__invoice_date__lte=date_to)

    # Korrekturbelege tragen negative Summen und mindern so über ihr Vorzeichen.
    rows = (
        qs.values("party_id", "party__display_name")
        .annotate(
            net=Coalesce(Sum("invoice__net_total"), _ZERO),
            gross=Coalesce(Sum("invoice__gross_total"), _ZERO),
            invoice_count=Count("invoice_id", filter=not_credit),
            credit_count=Count("invoice_id", filter=is_credit),
        )
    )
    customers = [
        {
            "party_id": str(r["party_id"]),
            "display_name": r["party__display_name"],
            "net_total": str(r["net"]),
            "gross_total": str(r["gross"]),
            "invoice_count": r["invoice_count"],
            "credit_count": r["credit_count"],
        }
        for r in rows
    ]
    # Nach Netto-Umsatz absteigend sortieren (String → Decimal für den Vergleich).
    customers.sort(key=lambda c: Decimal(c["net_total"]), reverse=True)

    total_net = sum((Decimal(c["net_total"]) for c in customers), Decimal("0.00"))
    return {
        "filters": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
        "customer_count": len(customers),
        "net_total": str(total_net),
        "customers": customers[:limit],
    }


def _filter_kv(date_from, date_to):
    return {
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
    }


# ===========================================================================
# Projekte-Dashboard
# ===========================================================================
# Rechtewahl: `invoicing/LESEN` wie die übrigen Dashboards — der Kern dieser
# Auswertung ist der **Umsatz** je Projekt/Status (invoicing-sensibel). Die
# Disposition (nur `workflow`) sieht Umsatzzahlen bewusst nicht.


def projekte_summary(*, date_from=None, date_to=None, limit=10):
    """Kennzahlen für das Projekte-Dashboard.

    - Anzahl offen/abgeschlossen (Project.status OPEN/CLOSED — das Schema kennt
      keinen reicheren Projektstatus).
    - Volumen (Netto-Umsatz veröffentlichter Rechnungen) je Projektstatus; die
      Rechnung wird über invoice.project_id zugeordnet. Korrekturbelege mindern
      über ihr Vorzeichen (Summe über alle veröffentlichten Belege).
    - Durchlaufzeit (angenähert): Das Schema führt **kein** Abschlussdatum, daher
      wird die Dauer abgeschlossener Projekte als updated_at − created_at
      genähert (CLOSED setzt updated_at) und das Alter offener Projekte als
      now − created_at. Beides in Tagen, als Durchschnitt.
    - Top-Projekte nach Netto-Umsatz (Top-N).

    date_from/date_to filtern Rechnungen über das Belegdatum; die Projektzähler
    sind Bestandsgrößen (nicht zeitraumgefiltert).
    """
    projects = Project.objects.all()
    total = projects.count()
    open_count = projects.filter(status="OPEN").count()
    closed_count = projects.filter(status="CLOSED").count()

    published = _apply_invoice_dates(
        Invoice.objects.filter(status="VEROEFFENTLICHT", project__isnull=False),
        date_from,
        date_to,
    )

    # Volumen je Projektstatus (Umsatz der Rechnungen, gruppiert über den Status
    # ihres Projekts). Korrekturen mindern über ihr Vorzeichen.
    status_rows = {
        r["project__status"]: r["net"]
        for r in published.values("project__status").annotate(
            net=Coalesce(Sum("net_total"), _ZERO)
        )
    }
    by_status = [
        {
            "status": status,
            "count": count,
            "net_total": str(status_rows.get(status, Decimal("0.00"))),
        }
        for status, count in (("OPEN", open_count), ("CLOSED", closed_count))
    ]

    # Durchlaufzeit / Alter als Durchschnitt in Tagen.
    closed_dur = Project.objects.filter(status="CLOSED").aggregate(
        avg=Avg(
            ExpressionWrapper(
                F("updated_at") - F("created_at"), output_field=DurationField()
            )
        )
    )["avg"]
    open_age = Project.objects.filter(status="OPEN").aggregate(
        avg=Avg(
            ExpressionWrapper(Now() - F("created_at"), output_field=DurationField())
        )
    )["avg"]

    def _days(delta):
        if delta is None:
            return None
        return round(delta.total_seconds() / 86400, 1)

    # Top-Projekte nach Netto-Umsatz.
    top_rows = (
        published.values("project_id", "project__project_number", "project__name")
        .annotate(net=Coalesce(Sum("net_total"), _ZERO))
        .order_by("-net")[:limit]
    )
    top_projects = [
        {
            "project_id": str(r["project_id"]),
            "project_number": r["project__project_number"],
            "name": r["project__name"],
            "net_total": str(r["net"]),
        }
        for r in top_rows
    ]

    return {
        "filters": _filter_kv(date_from, date_to),
        "total": total,
        "open": open_count,
        "closed": closed_count,
        "by_status": by_status,
        "throughput": {
            "avg_open_age_days": _days(open_age),
            "avg_closed_duration_days": _days(closed_dur),
        },
        "top_projects": top_projects,
    }


# ===========================================================================
# Artikel-/Leistungs-Dashboard
# ===========================================================================
# Rechtewahl: `invoicing/LESEN` (die Auswertung zeigt Umsätze). Datenquelle sind
# die **Belegzeilen** veröffentlichter Rechnungen.
#
# WICHTIG (bewusste Abgrenzung): invoice_line trägt KEINEN Artikelbezug (keine
# article_id) — der „Artikel" ist der Positionstext (description). Positionen
# werden über den normalisierten Text gruppiert. Korrekturbelege (GUTSCHRIFT/
# STORNO) werden AUSGESCHLOSSEN: sie stornieren ganze Belege, ihre Zeilen sind
# einer einzelnen Artikelposition nicht sinnvoll zurechenbar (und die Menge ist
# per CHECK stets > 0, würde die „meistverwendet"-Zählung also fälschlich
# erhöhen). Die maßgebliche Umsatzzahl bleibt das Umsatz-Dashboard.


def artikel_summary(*, date_from=None, date_to=None, limit=15):
    """Meistverwendete Positionen und Umsatz je Artikel/Leistung.

    Aggregiert die Zeilen veröffentlichter, NICHT-korrigierender Rechnungen
    (Positionsarten mit Betrag, also ohne TEXT/ZWISCHENSUMME):
    - je Positionstext: Anzahl Vorkommen, Gesamtmenge, Netto-Summe (Top-N).
    - je Positionsart (line_type): Anzahl und Netto-Summe (Materialaufteilung).

    date_from/date_to filtern über das Belegdatum.
    """
    lines = InvoiceLine.objects.filter(
        invoice__status="VEROEFFENTLICHT",
    ).exclude(
        invoice__invoice_type__in=CREDIT_TYPES
    ).exclude(
        line_type__in=NON_AMOUNT_LINE_TYPES
    )
    if date_from:
        lines = lines.filter(invoice__invoice_date__gte=date_from)
    if date_to:
        lines = lines.filter(invoice__invoice_date__lte=date_to)

    _QTY = DecimalField(max_digits=15, decimal_places=3)
    art_rows = (
        lines.values("description")
        .annotate(
            count=Count("id"),
            quantity_total=Coalesce(
                Sum("quantity"), Value(Decimal("0.000"), output_field=_QTY)
            ),
            net_total=Coalesce(Sum("net_amount"), _ZERO),
        )
        .order_by("-net_total", "-count")
    )
    articles = [
        {
            "description": r["description"],
            "count": r["count"],
            "quantity_total": str(r["quantity_total"]),
            "net_total": str(r["net_total"]),
        }
        for r in art_rows[:limit]
    ]

    type_rows = (
        lines.values("line_type")
        .annotate(count=Count("id"), net_total=Coalesce(Sum("net_amount"), _ZERO))
        .order_by("-net_total")
    )
    by_type = [
        {
            "line_type": r["line_type"],
            "count": r["count"],
            "net_total": str(r["net_total"]),
        }
        for r in type_rows
    ]

    totals = lines.aggregate(
        line_count=Count("id"), net_total=Coalesce(Sum("net_amount"), _ZERO)
    )
    return {
        "filters": _filter_kv(date_from, date_to),
        "line_count": totals["line_count"],
        "net_total": str(totals["net_total"]),
        "by_type": by_type,
        "articles": articles,
    }


# ===========================================================================
# Mitarbeitenden-Dashboard (Personaldaten → Modul hr)
# ===========================================================================
# Rechtewahl: `hr/LESEN`. Abwesenheiten enthalten Krankheitszeiten (DSGVO
# Art. 9, besondere Kategorie). Nur ADMINISTRATION/GESCHAEFTSFUEHRUNG haben das
# hr-Recht; NUR_LESEN und DISPOSITION bekommen 403.
#
# DSGVO-Datenminimierung: Auslastung (Ist-Arbeitszeit) und Urlaubsverbrauch sind
# gewöhnliche Beschäftigtendaten und werden je Person ausgewiesen. Die
# Abwesenheiten NACH ART (insbesondere KRANKHEIT) werden dagegen NUR
# unternehmensweit aggregiert zurückgegeben — niemals je Person —, damit aus dem
# Dashboard keine personenbezogene Krankheitsauswertung wird.


def _year_bounds(year):
    return date(year, 1, 1), date(year, 12, 31)


def mitarbeitende_summary(*, year):
    """Auslastung, Abwesenheiten und Urlaub für ein Kalenderjahr.

    - Auslastung je Mitarbeiter: Summe der Ist-ARBEITSZEIT (workflow.time_entry)
      im Jahr, in Stunden. (Ein Soll-/Ist-Vergleich bräuchte die tagesgenaue
      Vertragsauflösung und ist hier bewusst nicht enthalten.)
    - Urlaub je Mitarbeiter: Anspruch (Konto + Übertrag + Anpassung) und
      abgeleiteter Verbrauch (genehmigte URLAUB-Abwesenheiten des Jahres).
    - Abwesenheiten NACH ART: unternehmensweit aggregierte Tagessummen (inkl.
      KRANKHEIT als reine Gesamtzahl — NICHT je Person, siehe DSGVO-Hinweis).

    Alle Personenlisten umfassen ausschließlich aktive/inaktive, nicht
    ausgetretene Mitarbeiter.
    """
    start, end = _year_bounds(year)

    # Aktive Personalstämme mit Personennamen (1 Query).
    employees = list(
        Employee.objects.exclude(status="AUSGETRETEN").select_related("party")
    )
    by_app_user = {e.app_user_id: e for e in employees}
    emp_ids = [e.id for e in employees]

    # Ist-Arbeitszeit je Benutzer (1 Query). Dauer = ended_at − started_at.
    dur = ExpressionWrapper(
        F("ended_at") - F("started_at"), output_field=DurationField()
    )
    time_rows = (
        TimeEntry.objects.filter(
            time_type="ARBEITSZEIT",
            user_id__in=list(by_app_user.keys()),
            started_at__date__gte=start,
            started_at__date__lte=end,
        )
        .values("user_id")
        .annotate(total=Sum(dur))
    )
    hours_by_user = {
        r["user_id"]: round((r["total"].total_seconds() / 3600), 2)
        for r in time_rows
        if r["total"] is not None
    }

    # Urlaubsanspruch je Mitarbeiter für das Jahr (1 Query).
    budget_by_emp = {
        b.employee_id: (b.entitlement_days + b.carryover_days + b.adjustment_days)
        for b in VacationBudget.objects.filter(employee_id__in=emp_ids, year=year)
    }
    # Verbrauchter Urlaub (genehmigte URLAUB-Abwesenheiten, die im Jahr beginnen).
    used_by_emp = {
        r["employee_id"]: r["used"]
        for r in Absence.objects.filter(
            employee_id__in=emp_ids,
            absence_type="URLAUB",
            status="GENEHMIGT",
            start_date__year=year,
        )
        .values("employee_id")
        .annotate(used=Coalesce(Sum("days_count"), Value(Decimal("0.00"))))
    }

    people = []
    for e in employees:
        person = e.party
        name = f"{person.first_name} {person.last_name}"
        entitlement = budget_by_emp.get(e.id, Decimal("0"))
        used = used_by_emp.get(e.id, Decimal("0"))
        people.append(
            {
                "employee_id": str(e.id),
                "employee_number": e.employee_number,
                "display_name": name,
                "worked_hours": str(hours_by_user.get(e.app_user_id, 0)),
                "vacation_entitlement": str(entitlement),
                "vacation_used": str(used),
                "vacation_remaining": str(entitlement - used),
            }
        )
    people.sort(key=lambda p: float(p["worked_hours"]), reverse=True)

    # Abwesenheiten nach Art — unternehmensweit aggregiert (KEINE Personenbindung).
    absence_rows = (
        Absence.objects.filter(
            employee_id__in=emp_ids,
            status="GENEHMIGT",
            start_date__year=year,
        )
        .values("absence_type")
        .annotate(
            days=Coalesce(Sum("days_count"), Value(Decimal("0.00"))),
            count=Count("id"),
        )
        .order_by("-days")
    )
    absence_by_type = [
        {
            "absence_type": r["absence_type"],
            "days": str(r["days"]),
            "count": r["count"],
        }
        for r in absence_rows
    ]
    total_absence_days = sum(
        (Decimal(a["days"]) for a in absence_by_type), Decimal("0.00")
    )

    return {
        "year": year,
        "employee_count": len(people),
        "total_worked_hours": str(
            round(sum(float(p["worked_hours"]) for p in people), 2)
        ),
        "total_absence_days": str(total_absence_days),
        "people": people,
        "absence_by_type": absence_by_type,
    }
