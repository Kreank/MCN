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

from db_core.models import Invoice, InvoiceParty, Project

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
        "available": True,
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
