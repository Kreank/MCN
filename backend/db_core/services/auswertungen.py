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
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

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
    QuoteLine,
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

_CENT = Decimal("0.01")


def _round2(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


# ===========================================================================
# Deckungsbeitrag / Marge — eingefrorene EK-Basis (unit_cost) der Belegzeilen
# ===========================================================================
# Rechtewahl: Der Einkaufspreis (`unit_cost`) und die daraus abgeleitete Marge
# sind Kalkulationsdaten des Moduls `pricing` (Einkaufspreise, VK-Kalkulation),
# NICHT der reine Umsatz (`invoicing`). Die Marge wird deshalb nur berechnet und
# ausgeliefert, wenn das Konto ZUSÄTZLICH `pricing/LESEN` hat (`ek_allowed`).
# Fehlt das Recht, bleibt der Umsatz sichtbar, der Marge-Block ist None
# (`marge_sichtbar=False`) — Need-to-know statt 403 auf die ganze Auswertung.
#
# EHRLICHKEIT BEI FEHLENDEM EK (wie der Angebotseditor / beleg._kalkulation):
# Eine Zeile ohne `unit_cost` hat KEINE Marge von 0 und KEINE von 100 % — sie ist
# UNBEKANNT. Der fehlende EK wird NIE als 0 in die Kostenbasis gerechnet.
# Deckungsbeitrag und Marge beziehen sich ausschließlich auf den Netto-Anteil MIT
# hinterlegtem EK (`net_mit_ek`); der Anteil OHNE EK (`net_ohne_ek`) und die Zahl
# der Positionen ohne EK (`positionen_ohne_ek`) werden getrennt ausgewiesen, damit
# das Dashboard die Lücke zeigt statt eine erfundene Zahl.
#
# Nur `line_kind='NORMAL'` ist summenwirksam (ALTERNATIV/BEDARF zählen nicht, wie
# in den Kopfsummen); TEXT/ZWISCHENSUMME tragen keinen Betrag. Korrekturbelege
# (GUTSCHRIFT/STORNO) sind ausgeschlossen: ihre Zeilen tragen keinen EK-Snapshot
# (nur der Umsatz würde negiert, die Kosten nicht) — sie würden die Marge
# verfälschen. Die Marge misst also die Kalkulationsqualität der ausgestellten
# (nicht-stornierenden) Rechnungen.

_SUMMENWIRKSAM = "NORMAL"


def _leerer_marge_block():
    return {
        "net_total": Decimal("0.00"),
        "net_mit_ek": Decimal("0.00"),
        "ek_total": Decimal("0.00"),
        "positionen": 0,
        "positionen_ohne_ek": 0,
    }


def _marge_add(block, row):
    """Verbucht eine Belegzeile (values-dict) in einen Marge-Block."""
    if row["line_type"] in NON_AMOUNT_LINE_TYPES:
        return
    if (row["line_kind"] or _SUMMENWIRKSAM) != _SUMMENWIRKSAM:
        return
    netto = row["net_amount"] or Decimal("0.00")
    block["net_total"] += netto
    block["positionen"] += 1
    if row["unit_cost"] is None:
        block["positionen_ohne_ek"] += 1
    else:
        block["net_mit_ek"] += netto
        block["ek_total"] += _round2(row["unit_cost"] * (row["quantity"] or Decimal(0)))


def _marge_finalize(block):
    """Leitet Deckungsbeitrag und Marge% ab — nur auf dem Anteil MIT bekanntem EK.

    `deckungsbeitrag` = net_mit_ek − ek_total (nur, wenn mindestens eine Position
    einen EK trägt). `marge_prozent` = DB / net_mit_ek × 100 (nur bei positivem
    net_mit_ek). Fehlt jeder EK, bleiben beide None = „unbekannt", nie 0.
    """
    net_total = block["net_total"]
    net_mit_ek = block["net_mit_ek"]
    ek = block["ek_total"]
    positionen = block["positionen"]
    ohne = block["positionen_ohne_ek"]
    mit_ek = positionen - ohne

    deckungsbeitrag = net_mit_ek - ek if mit_ek > 0 else None
    marge = None
    if deckungsbeitrag is not None and net_mit_ek > 0:
        marge = _round2(deckungsbeitrag / net_mit_ek * Decimal(100))

    return {
        "net_total": str(net_total),
        "net_mit_ek": str(net_mit_ek),
        "net_ohne_ek": str(net_total - net_mit_ek),
        "ek_total": str(ek),
        "deckungsbeitrag": None if deckungsbeitrag is None else str(deckungsbeitrag),
        "marge_prozent": None if marge is None else str(marge),
        "positionen": positionen,
        "positionen_ohne_ek": ohne,
        # ek_vollstaendig=True ⇒ jede summenwirksame Position hat einen EK; dann
        # ist die Marge belastbar. False ⇒ es fehlen EKs, die Marge bezieht sich
        # nur auf net_mit_ek (Rest = Marge unbekannt).
        "ek_vollstaendig": positionen > 0 and ohne == 0,
    }


# Spalten, die eine Belegzeile für die Marge-Aggregation braucht.
_MARGE_COLS = ("line_type", "line_kind", "net_amount", "unit_cost", "quantity")


def _non_credit_published_lines(date_from, date_to):
    """Zeilen veröffentlichter, NICHT-korrigierender Rechnungen (mit Datumsfilter)."""
    qs = InvoiceLine.objects.filter(invoice__status="VEROEFFENTLICHT").exclude(
        invoice__invoice_type__in=CREDIT_TYPES
    )
    if date_from:
        qs = qs.filter(invoice__invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice__invoice_date__lte=date_to)
    return qs


def _marge_gesamt(date_from, date_to):
    """Ein Marge-Block über ALLE nicht-korrigierenden veröffentlichten Zeilen."""
    block = _leerer_marge_block()
    for row in _non_credit_published_lines(date_from, date_to).values(*_MARGE_COLS):
        _marge_add(block, row)
    return _marge_finalize(block)


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


def _marge_by_gewerk(date_from, date_to):
    """Marge je Gewerk (angenähert über die Projektkategorie der Rechnung).

    Rechnungen ohne Projekt/Kategorie fallen in „Ohne Gewerk". Sortiert nach
    Nettoumsatz absteigend; nur Gewerke mit summenwirksamen Positionen.
    """
    buckets = {}
    rows = _non_credit_published_lines(date_from, date_to).values(
        "invoice__project__category__name", *_MARGE_COLS
    )
    for row in rows:
        name = row["invoice__project__category__name"] or "Ohne Gewerk"
        _marge_add(buckets.setdefault(name, _leerer_marge_block()), row)
    result = []
    for name, block in buckets.items():
        if block["positionen"] == 0:
            continue
        result.append({"name": name, **_marge_finalize(block)})
    result.sort(key=lambda g: Decimal(g["net_total"]), reverse=True)
    return result


def umsatz_projektuebersicht_summary(*, date_from=None, date_to=None, ek_allowed=False):
    """Kennzahlen für das Umsatz-/Projektübersicht-Dashboard.

    date_from/date_to (date, optional) filtern Rechnungen über das Belegdatum
    und Projekte über das Erstellungsdatum.

    ek_allowed (pricing/LESEN): nur dann werden Deckungsbeitrag und Marge (Gesamt
    und je Gewerk) berechnet; sonst bleibt `marge`/`marge_by_gewerk` leer und
    `marge_sichtbar=False`.
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
        "marge_sichtbar": ek_allowed,
        "marge": _marge_gesamt(date_from, date_to) if ek_allowed else None,
        "marge_by_gewerk": _marge_by_gewerk(date_from, date_to) if ek_allowed else [],
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


# Angebotsstatus, die eine noch gültige Planung tragen (geplante Marge). ENTWURF
# ist noch nicht verbindlich; ABGELEHNT/ABGELAUFEN/ERSETZT sind erledigt. Ersetzte
# Angebote (replaced_by_quote_id gesetzt) werden zusätzlich ausgeschlossen, damit
# Angebotsversionen nicht doppelt zählen.
_AKTIVE_ANGEBOT_STATUS = ("INTERN_GEPRUEFT", "FREIGEGEBEN", "VERSENDET", "ANGENOMMEN")


def _marge_by_project(date_from, date_to):
    """Realisierte Marge je Projekt (aus Rechnungszeilen), keyed nach project_id."""
    buckets = {}
    rows = _non_credit_published_lines(date_from, date_to).values(
        "invoice__project_id", *_MARGE_COLS
    )
    for row in rows:
        pid = row["invoice__project_id"]
        if pid is None:
            continue
        _marge_add(buckets.setdefault(pid, _leerer_marge_block()), row)
    return {pid: _marge_finalize(block) for pid, block in buckets.items()}


def _geplante_marge(date_from, date_to):
    """Geplante Marge aus den Angebotszeilen der noch aktiven, nicht ersetzten
    Angebote (Angebots-Snapshot: quote_line trägt dieselbe eingefrorene EK-Basis).

    Datumsfilter über das Angebotsdatum (quote_date). Ableitbar, weil quote_line
    `unit_cost` beim Einfügen einfriert — dieselbe Logik wie bei den Rechnungen.
    """
    qs = QuoteLine.objects.filter(
        quote__status__in=_AKTIVE_ANGEBOT_STATUS,
        quote__replaced_by_quote_id__isnull=True,
    )
    if date_from:
        qs = qs.filter(quote__quote_date__gte=date_from)
    if date_to:
        qs = qs.filter(quote__quote_date__lte=date_to)
    block = _leerer_marge_block()
    for row in qs.values(*_MARGE_COLS):
        _marge_add(block, row)
    return _marge_finalize(block)


def projekte_summary(*, date_from=None, date_to=None, limit=10, ek_allowed=False):
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
    marge_je_projekt = _marge_by_project(date_from, date_to) if ek_allowed else {}

    def _projekt_marge(project_id):
        m = marge_je_projekt.get(project_id)
        if m is None:
            return {
                "ek_total": None,
                "deckungsbeitrag": None,
                "marge_prozent": None,
                "positionen_ohne_ek": None,
                "ek_vollstaendig": None,
            }
        return {
            "ek_total": m["ek_total"],
            "deckungsbeitrag": m["deckungsbeitrag"],
            "marge_prozent": m["marge_prozent"],
            "positionen_ohne_ek": m["positionen_ohne_ek"],
            "ek_vollstaendig": m["ek_vollstaendig"],
        }

    top_projects = [
        {
            "project_id": str(r["project_id"]),
            "project_number": r["project__project_number"],
            "name": r["project__name"],
            "net_total": str(r["net"]),
            **_projekt_marge(r["project_id"]),
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
        "marge_sichtbar": ek_allowed,
        # Realisierte Marge (aus veröffentlichten Rechnungen) und geplante Marge
        # (aus aktiven Angeboten) nebeneinander — Plan/Ist der Kalkulation.
        "marge": _marge_gesamt(date_from, date_to) if ek_allowed else None,
        "geplante_marge": _geplante_marge(date_from, date_to) if ek_allowed else None,
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


def _marge_by_description(date_from, date_to):
    """Marge-Block je Positionstext (description) — Datenquelle wie artikel_summary."""
    buckets = {}
    rows = _non_credit_published_lines(date_from, date_to).values(
        "description", *_MARGE_COLS
    )
    for row in rows:
        _marge_add(buckets.setdefault(row["description"], _leerer_marge_block()), row)
    return {desc: _marge_finalize(block) for desc, block in buckets.items()}


def artikel_summary(*, date_from=None, date_to=None, limit=15, ek_allowed=False):
    """Meistverwendete Positionen und Umsatz je Artikel/Leistung.

    Aggregiert die Zeilen veröffentlichter, NICHT-korrigierender Rechnungen
    (Positionsarten mit Betrag, also ohne TEXT/ZWISCHENSUMME):
    - je Positionstext: Anzahl Vorkommen, Gesamtmenge, Netto-Summe (Top-N).
    - je Positionsart (line_type): Anzahl und Netto-Summe (Materialaufteilung).

    ek_allowed (pricing/LESEN): dann trägt jede Positionszeile zusätzlich EK,
    Deckungsbeitrag, Marge% und die Zahl der Vorkommen ohne EK; sonst bleiben die
    Marge-Felder None und `marge_sichtbar=False`. Der Marge-Anteil bezieht sich
    auf die summenwirksamen (NORMAL-)Vorkommen mit hinterlegtem EK.

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

    marge_je_desc = _marge_by_description(date_from, date_to) if ek_allowed else {}

    def _marge_felder(description):
        m = marge_je_desc.get(description)
        if m is None:
            return {
                "ek_total": None,
                "deckungsbeitrag": None,
                "marge_prozent": None,
                "positionen_ohne_ek": None,
                "ek_vollstaendig": None,
            }
        return {
            "ek_total": m["ek_total"],
            "deckungsbeitrag": m["deckungsbeitrag"],
            "marge_prozent": m["marge_prozent"],
            "positionen_ohne_ek": m["positionen_ohne_ek"],
            "ek_vollstaendig": m["ek_vollstaendig"],
        }

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
            **_marge_felder(r["description"]),
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
        "marge_sichtbar": ek_allowed,
        "marge": _marge_gesamt(date_from, date_to) if ek_allowed else None,
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


# ===========================================================================
# CSV-Export der Dashboards — Ausleitung der Tabellen (Aggregation NICHT ändern)
# ===========================================================================
# Diese Helfer KONSUMIEREN die vorhandenen *_summary()-Aggregate und übersetzen
# sie in Blöcke aus typisierten Zeilen/Spalten. Die eigentliche CSV-Serialisierung
# (Semikolon, UTF-8-BOM, de-DE-Zahlen, Quoting) macht die API-Schicht
# (api/auswertungen.py::render_csv). Hier fällt bewusst KEINE Formatierung an:
# Geld/Prozente bleiben verlustfreie Decimal-Strings, damit erst am äußersten Rand
# (im CSV) auf Komma-Dezimal umgestellt wird — Geld reist als String durch die Kette.
#
# Need-to-know der Marge: Deckungsbeitrag/Marge stützen sich auf die Einkaufspreise
# (Modul pricing). Die Marge-SPALTEN werden nur aufgenommen, wenn der Aufrufer
# `ek_allowed` (pricing/LESEN) übergibt — sonst fehlen sie ganz (kein 403), exakt
# wie die Dashboards es halten. Ein fehlender EK bleibt „unbekannt" (None), nie 0.

# Formatierungsarten der Spalten (Auswertung in api/auswertungen.py::_fmt_zelle):
#   text · geld · prozent · menge · stunden · dezimal · ganzzahl · datum · bool


class Zelle(tuple):
    """Zelle mit EIGENER Formatierungsart (überschreibt die Spaltenart).

    Für Kennzahlen-Blöcke, in denen dieselbe Spalte je Zeile unterschiedliche
    Werttypen trägt (mal Geld, mal Ganzzahl)."""

    __slots__ = ()

    def __new__(cls, wert, kind):
        return super().__new__(cls, (wert, kind))


@dataclass(frozen=True)
class CsvSpalte:
    header: str
    kind: str = "text"


@dataclass
class CsvBlock:
    """Ein Tabellenblock: optionaler Titel, Kopfzeile, Datenzeilen."""

    titel: str | None
    spalten: list
    zeilen: list


@dataclass
class CsvTabelle:
    """Das ausleitbare Ganze: sprechender Dateiname (ohne .csv) + Blöcke."""

    dateiname: str
    bloecke: list


# Deutsche Beschriftungen (Spiegel der Frontend-Labels), damit die CSV liest wie
# die Tabelle auf dem Schirm.
_STATUS_LABEL = {"OPEN": "Offen", "CLOSED": "Abgeschlossen"}
_ABSENCE_LABEL = {
    "URLAUB": "Urlaub",
    "KRANKHEIT": "Krankheit",
    "ELTERNZEIT": "Elternzeit",
    "SONDERURLAUB": "Sonderurlaub",
    "UNBEZAHLT": "Unbezahlt",
    "FORTBILDUNG": "Fortbildung",
}
_LINE_TYPE_LABEL = {
    "MATERIAL": "Material",
    "ARBEITSZEIT": "Arbeitszeit",
    "PAUSCHALE": "Pauschale",
    "FREMDLEISTUNG": "Fremdleistung",
    "FAHRT": "Fahrt",
    "ZUSCHLAG": "Zuschlag",
    "TEXT": "Text",
    "ZWISCHENSUMME": "Zwischensumme",
}

# Die fünf Marge-Spalten (nur mit ek_allowed). „ohne EK"-Header wird je Dashboard
# überschrieben (Positionen vs. Vorkommen); die Werte kommen aus _marge_zellen.
_MARGE_SPALTEN = [
    CsvSpalte("EK gesamt", "geld"),
    CsvSpalte("Deckungsbeitrag", "geld"),
    CsvSpalte("Marge %", "prozent"),
    CsvSpalte("Positionen ohne EK", "ganzzahl"),
    CsvSpalte("EK vollständig", "bool"),
]


def _marge_zellen(m):
    """Fünf Marge-Werte aus einem Marge-dict bzw. einer Top-Zeile (raw, ungeformt).

    None (Deckungsbeitrag/Marge unbekannt, weil kein EK hinterlegt) bleibt None
    und wird im CSV als „unbekannt" ausgewiesen — nie als erfundene 0."""
    return [
        m.get("ek_total"),
        m.get("deckungsbeitrag"),
        m.get("marge_prozent"),
        m.get("positionen_ohne_ek"),
        m.get("ek_vollstaendig"),
    ]


def _export_name(stub, date_from, date_to):
    """Sprechender Dateistamm inkl. Zeitraum (ohne Erweiterung, ASCII)."""
    if date_from and date_to:
        zeitraum = f"{date_from.isoformat()}_bis_{date_to.isoformat()}"
    elif date_from:
        zeitraum = f"ab_{date_from.isoformat()}"
    elif date_to:
        zeitraum = f"bis_{date_to.isoformat()}"
    else:
        zeitraum = "gesamt"
    return f"{stub}_{zeitraum}"


def _kennzahlen_block(titel, zeilen):
    """Zwei-Spalten-Block (Kennzahl | Wert) mit je Zeile getippten Zellen."""
    return CsvBlock(titel, [CsvSpalte("Kennzahl"), CsvSpalte("Wert")], zeilen)


def build_umsatz_export(*, date_from=None, date_to=None, ek_allowed=False):
    """Umsatz-/Projektübersicht als CSV-Tabelle (konsumiert das Dashboard-Aggregat).

    Blöcke: Kennzahlen (Umsatz/Projekte) · Umsatzverlauf je Monat · Projekte nach
    Gewerk · (nur mit pricing/LESEN) Deckungsbeitrag/Marge nach Gewerk."""
    data = umsatz_projektuebersicht_summary(
        date_from=date_from, date_to=date_to, ek_allowed=ek_allowed
    )
    rev, proj = data["revenue"], data["projects"]
    bloecke = [
        _kennzahlen_block(
            "Kennzahlen",
            [
                ["Umsatz netto", Zelle(rev["net_total"], "geld")],
                ["Umsatz brutto", Zelle(rev["gross_total"], "geld")],
                ["Rechnungen (Anzahl)", Zelle(rev["invoice_count"], "ganzzahl")],
                ["Korrekturbelege (Anzahl)", Zelle(rev["credit_count"], "ganzzahl")],
                ["Projekte gesamt", Zelle(proj["total"], "ganzzahl")],
                ["Projekte offen", Zelle(proj["open"], "ganzzahl")],
                ["Projekte abgeschlossen", Zelle(proj["closed"], "ganzzahl")],
                ["Projekte im Zeitraum erstellt", Zelle(proj["created_in_range"], "ganzzahl")],
            ],
        ),
        CsvBlock(
            "Umsatzverlauf (netto je Monat)",
            [CsvSpalte("Monat"), CsvSpalte("Umsatz netto", "geld")],
            [[p["month"], p["net"]] for p in data["timeline"]],
        ),
        CsvBlock(
            "Erstellte Projekte nach Gewerk",
            [CsvSpalte("Gewerk"), CsvSpalte("Anzahl", "ganzzahl")],
            [[g["name"], g["count"]] for g in proj["by_gewerk"]],
        ),
    ]
    if ek_allowed:
        spalten = [
            CsvSpalte("Gewerk"),
            CsvSpalte("Umsatz netto", "geld"),
            CsvSpalte("Netto mit EK", "geld"),
            CsvSpalte("Netto ohne EK", "geld"),
            *_MARGE_SPALTEN,
        ]
        zeilen = [
            [g["name"], g["net_total"], g["net_mit_ek"], g["net_ohne_ek"], *_marge_zellen(g)]
            for g in data["marge_by_gewerk"]
        ]
        bloecke.append(CsvBlock("Deckungsbeitrag/Marge nach Gewerk", spalten, zeilen))
    return CsvTabelle(_export_name("Umsatz-Projektuebersicht", date_from, date_to), bloecke)


def build_kunden_export(*, date_from=None, date_to=None):
    """Kunden-Dashboard als CSV: Kennzahlen + Umsatz/Anteil je Kunde.

    Der Umsatzanteil wird hier (Ausleitung, keine Aggregatsänderung) aus dem
    Netto je Kunde und der Gesamtsumme abgeleitet."""
    data = kunden_summary(date_from=date_from, date_to=date_to)
    total = Decimal(data["net_total"])
    zeilen = []
    for c in data["customers"]:
        anteil = None
        if total != 0:
            anteil = str(_round2(Decimal(c["net_total"]) / total * Decimal(100)))
        zeilen.append(
            [
                c["display_name"],
                c["net_total"],
                c["gross_total"],
                anteil,
                c["invoice_count"],
                c["credit_count"],
            ]
        )
    bloecke = [
        _kennzahlen_block(
            "Kennzahlen",
            [
                ["Kunden (Anzahl)", Zelle(data["customer_count"], "ganzzahl")],
                ["Umsatz netto gesamt", Zelle(data["net_total"], "geld")],
            ],
        ),
        CsvBlock(
            "Umsatz je Kunde (Top nach Netto)",
            [
                CsvSpalte("Kunde"),
                CsvSpalte("Umsatz netto", "geld"),
                CsvSpalte("Umsatz brutto", "geld"),
                CsvSpalte("Anteil %", "prozent"),
                CsvSpalte("Rechnungen", "ganzzahl"),
                CsvSpalte("Korrekturen", "ganzzahl"),
            ],
            zeilen,
        ),
    ]
    return CsvTabelle(_export_name("Kunden", date_from, date_to), bloecke)


def build_projekte_export(*, date_from=None, date_to=None, ek_allowed=False):
    """Projekte-Dashboard als CSV: Kennzahlen/Durchlaufzeit · Volumen nach Status ·
    Top-Projekte (mit Marge nur bei pricing/LESEN) · realisierte vs. geplante Marge."""
    data = projekte_summary(date_from=date_from, date_to=date_to, ek_allowed=ek_allowed)
    tp = data["throughput"]
    bloecke = [
        _kennzahlen_block(
            "Kennzahlen",
            [
                ["Projekte gesamt", Zelle(data["total"], "ganzzahl")],
                ["Projekte offen", Zelle(data["open"], "ganzzahl")],
                ["Projekte abgeschlossen", Zelle(data["closed"], "ganzzahl")],
                ["Ø Alter offener Projekte (Tage)", Zelle(tp["avg_open_age_days"], "dezimal")],
                [
                    "Ø Dauer abgeschlossener Projekte (Tage)",
                    Zelle(tp["avg_closed_duration_days"], "dezimal"),
                ],
            ],
        ),
        CsvBlock(
            "Volumen nach Status",
            [CsvSpalte("Status"), CsvSpalte("Anzahl", "ganzzahl"), CsvSpalte("Umsatz netto", "geld")],
            [
                [_STATUS_LABEL.get(s["status"], s["status"]), s["count"], s["net_total"]]
                for s in data["by_status"]
            ],
        ),
    ]
    top_spalten = [CsvSpalte("Projektnummer"), CsvSpalte("Projekt"), CsvSpalte("Umsatz netto", "geld")]
    if ek_allowed:
        top_spalten += _MARGE_SPALTEN
    top_zeilen = []
    for p in data["top_projects"]:
        zeile = [p["project_number"], p["name"], p["net_total"]]
        if ek_allowed:
            zeile += _marge_zellen(p)
        top_zeilen.append(zeile)
    bloecke.append(CsvBlock("Top-Projekte nach Umsatz", top_spalten, top_zeilen))

    if ek_allowed and data["marge"]:
        m = data["marge"]
        gp = data["geplante_marge"] or {}
        bloecke.append(
            CsvBlock(
                "Deckungsbeitrag/Marge (Ist realisiert / Plan aus Angeboten)",
                [CsvSpalte("Kennzahl"), CsvSpalte("Realisiert"), CsvSpalte("Geplant")],
                [
                    ["Netto mit EK", Zelle(m["net_mit_ek"], "geld"), Zelle(gp.get("net_mit_ek"), "geld")],
                    ["EK gesamt", Zelle(m["ek_total"], "geld"), Zelle(gp.get("ek_total"), "geld")],
                    [
                        "Deckungsbeitrag",
                        Zelle(m["deckungsbeitrag"], "geld"),
                        Zelle(gp.get("deckungsbeitrag"), "geld"),
                    ],
                    ["Marge %", Zelle(m["marge_prozent"], "prozent"), Zelle(gp.get("marge_prozent"), "prozent")],
                    [
                        "EK vollständig",
                        Zelle(m["ek_vollstaendig"], "bool"),
                        Zelle(gp.get("ek_vollstaendig"), "bool"),
                    ],
                ],
            )
        )
    return CsvTabelle(_export_name("Projekte", date_from, date_to), bloecke)


def build_artikel_export(*, date_from=None, date_to=None, ek_allowed=False):
    """Artikel-/Leistungs-Dashboard als CSV: Kennzahlen · nach Positionsart ·
    Positionen (mit Ø-Marge nur bei pricing/LESEN)."""
    data = artikel_summary(date_from=date_from, date_to=date_to, ek_allowed=ek_allowed)
    art_spalten = [
        CsvSpalte("Bezeichnung"),
        CsvSpalte("Vorkommen", "ganzzahl"),
        CsvSpalte("Menge gesamt", "menge"),
        CsvSpalte("Umsatz netto", "geld"),
    ]
    if ek_allowed:
        art_spalten += [
            CsvSpalte("EK gesamt", "geld"),
            CsvSpalte("Deckungsbeitrag", "geld"),
            CsvSpalte("Ø-Marge %", "prozent"),
            CsvSpalte("Vorkommen ohne EK", "ganzzahl"),
            CsvSpalte("EK vollständig", "bool"),
        ]
    art_zeilen = []
    for a in data["articles"]:
        zeile = [a["description"], a["count"], a["quantity_total"], a["net_total"]]
        if ek_allowed:
            zeile += _marge_zellen(a)
        art_zeilen.append(zeile)
    bloecke = [
        _kennzahlen_block(
            "Kennzahlen",
            [
                ["Positionszeilen (Anzahl)", Zelle(data["line_count"], "ganzzahl")],
                ["Umsatz netto gesamt", Zelle(data["net_total"], "geld")],
            ],
        ),
        CsvBlock(
            "Umsatz nach Positionsart",
            [CsvSpalte("Positionsart"), CsvSpalte("Anzahl", "ganzzahl"), CsvSpalte("Umsatz netto", "geld")],
            [
                [_LINE_TYPE_LABEL.get(t["line_type"], t["line_type"]), t["count"], t["net_total"]]
                for t in data["by_type"]
            ],
        ),
        CsvBlock("Artikel & Leistungen (Top nach Umsatz)", art_spalten, art_zeilen),
    ]
    return CsvTabelle(_export_name("Artikel-Leistungen", date_from, date_to), bloecke)


def build_mitarbeitende_export(*, year):
    """Mitarbeitenden-Dashboard als CSV (Personaldaten → Recht hr/LESEN).

    Blöcke: Kennzahlen · Auslastung/Urlaub je Mitarbeiter · Abwesenheiten nach Art
    (unternehmensweit — nie je Person, DSGVO wie im Dashboard)."""
    data = mitarbeitende_summary(year=year)
    bloecke = [
        _kennzahlen_block(
            "Kennzahlen",
            [
                ["Jahr", Zelle(data["year"], "ganzzahl")],
                ["Mitarbeitende (Anzahl)", Zelle(data["employee_count"], "ganzzahl")],
                ["Ist-Stunden gesamt", Zelle(data["total_worked_hours"], "stunden")],
                ["Abwesenheitstage gesamt", Zelle(data["total_absence_days"], "dezimal")],
            ],
        ),
        CsvBlock(
            "Auslastung & Urlaub je Mitarbeiter",
            [
                CsvSpalte("Personalnummer"),
                CsvSpalte("Name"),
                CsvSpalte("Ist-Stunden", "stunden"),
                CsvSpalte("Urlaubsanspruch (Tage)", "dezimal"),
                CsvSpalte("Urlaub genommen (Tage)", "dezimal"),
                CsvSpalte("Urlaub Rest (Tage)", "dezimal"),
            ],
            [
                [
                    p["employee_number"],
                    p["display_name"],
                    p["worked_hours"],
                    p["vacation_entitlement"],
                    p["vacation_used"],
                    p["vacation_remaining"],
                ]
                for p in data["people"]
            ],
        ),
        CsvBlock(
            "Abwesenheiten nach Art (unternehmensweit aggregiert)",
            [CsvSpalte("Abwesenheitsart"), CsvSpalte("Tage", "dezimal"), CsvSpalte("Vorgänge", "ganzzahl")],
            [
                [_ABSENCE_LABEL.get(a["absence_type"], a["absence_type"]), a["days"], a["count"]]
                for a in data["absence_by_type"]
            ],
        ),
    ]
    return CsvTabelle(f"Mitarbeitende_{year}", bloecke)
