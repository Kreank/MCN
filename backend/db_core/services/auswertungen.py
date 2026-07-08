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
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth

from db_core.models import Invoice, Project

# Korrekturbelege mindern den Umsatz (kein eigener Status "storniert" im Schema).
CREDIT_TYPES = ("GUTSCHRIFT", "STORNO")

# Verfügbare Dashboards (Landing). available=False = noch nicht umgesetzt.
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
        "description": "Marge nach Gewerk, Nachkalkulation, offene Umsätze.",
        "available": False,
    },
    {
        "key": "kunden",
        "title": "Kunden",
        "description": "Umsatz und Rechnungen je Kunde.",
        "available": False,
    },
    {
        "key": "artikel",
        "title": "Artikel & Leistungen",
        "description": "Top-Positionen nach Marge und Menge, Verkaufsverlauf.",
        "available": False,
    },
]

_DEC = DecimalField(max_digits=15, decimal_places=2)
_ZERO = Value(Decimal("0.00"), output_field=_DEC)


def list_dashboards():
    """Liste der verfügbaren Auswertungs-Dashboards (Landing)."""
    return [dict(d) for d in DASHBOARDS]


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
    agg = published.aggregate(
        net_pos=Coalesce(Sum("net_total", filter=not_credit), _ZERO),
        gross_pos=Coalesce(Sum("gross_total", filter=not_credit), _ZERO),
        net_credit=Coalesce(Sum("net_total", filter=is_credit), _ZERO),
        gross_credit=Coalesce(Sum("gross_total", filter=is_credit), _ZERO),
        invoice_count=Count("id", filter=not_credit),
        credit_count=Count("id", filter=is_credit),
    )
    net_revenue = agg["net_pos"] - agg["net_credit"]
    gross_revenue = agg["gross_pos"] - agg["gross_credit"]

    # Umsatzverlauf je Monat (Belegdatum), Korrekturen mindernd.
    rows = (
        published.annotate(month=TruncMonth("invoice_date"))
        .values("month")
        .annotate(
            net_pos=Coalesce(Sum("net_total", filter=not_credit), _ZERO),
            net_credit=Coalesce(Sum("net_total", filter=is_credit), _ZERO),
        )
        .order_by("month")
    )
    timeline = [
        {
            "month": r["month"].strftime("%Y-%m"),
            "net": str(r["net_pos"] - r["net_credit"]),
        }
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
