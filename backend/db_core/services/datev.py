"""DATEV-Export: EXTF-Buchungsstapel aus veröffentlichten Ausgangsrechnungen.

Erzeugt eine DATEV-Import-Datei im Format „EXTF Buchungsstapel", die der
Steuerberater in DATEV Kanzlei-Rechnungswesen / Unternehmen online importiert.
Rein lesend — keine Writes, kein `business_transaction`.

Was gebucht wird
----------------
Je veröffentlichter Rechnung (Status VEROEFFENTLICHT) mit Belegdatum im Zeitraum
entsteht **je Steuergruppe ein Buchungssatz**: Debitor an Erlöskonto. Die
Erlöskonten sind DATEV-**Automatikkonten** (die Umsatzsteuer wird von DATEV aus
dem Konto abgeleitet), deshalb trägt der Umsatz den **Bruttobetrag** und es wird
**kein** BU-Schlüssel gesetzt. Die Steuergruppierung spiegelt exakt die
Kopf-Summenlogik der Rechnung (`services/beleg._prepare_lines`): Summe je
(tax_code, Satz), Steuer je Gruppe gerundet — dadurch stimmt die Summe der
Buchungssätze cent-genau mit `gross_total` überein.

Vorzeichen: Ein normaler Verkauf bucht die Forderung im Soll (S). Gutschriften
und Stornos tragen laut Invariante negative Summen; deren Gruppen kehren das
Soll/Haben-Kennzeichen um (H) und buchen den Betragsbetrag (ohne Vorzeichen —
DATEV trägt das Vorzeichen ausschließlich über S/H).

Kontenrahmen
------------
Erlös-/Debitorenkonten kommen aus dem gewählten SKR (03/04) mit den üblichen
Standardnummern; abweichende Nummern trägt der Anwender im Firmenprofil als
Override ein (`datev_*_account`). Für die Zuordnung Steuercode → Erlöskonto siehe
`_TAX_CODE_ROLE`.

Abschlagsrechnungen: Erlös oder Anzahlungskonto (Migration 0063)
----------------------------------------------------------------
`company_profile.datev_advance_mode` entscheidet, wie ABSCHLAGSRECHNUNG und
TEILRECHNUNG kontiert werden. **Default ist `ERLOES`** — das bisherige Verhalten.

**Modus ERLOES (Teilleistung).** Der Abschlag ist ein endgültig abgerechneter,
abgrenzbarer Leistungsteil (§ 13 Abs. 1 Nr. 1 Buchst. a Satz 2 UStG): er bucht wie
jede Rechnung Debitor an **Erlös**. Die Schlussrechnung bucht ihre Steuergruppen
saldiert (Leistung minus Anrechnung) ebenfalls gegen Erlös — der zuvor gebuchte
Abschlagserlös wird dadurch rechnerisch wieder herausgenommen. Nichts ändert sich.

**Modus ANZAHLUNG (Vorauszahlung).** Vor der Leistung vereinnahmtes Geld ist kein
Ertrag, sondern eine **Verbindlichkeit** (§ 13 Abs. 1 Nr. 1 Buchst. a Satz 4 UStG:
die Umsatzsteuer entsteht trotzdem sofort). Gebucht wird gegen
„Erhaltene, versteuerte Anzahlungen":

    (1) Abschlagsrechnung   Debitor        an  Anzahlungskonto   (brutto)
                            [S]                [Automatikkonto: zieht die USt]

    (2) Schlussrechnung, Leistungsteil
                            Debitor        an  Erlöskonto        (brutto)
                            [S]
        Schlussrechnung, Anrechnungsteil (die negativen Anrechnungspositionen)
                            Anzahlungskonto an Debitor           (brutto)
                            [Konto=Debitor mit H → Gegenkonto im Soll]

Das Anzahlungskonto wird durch (2) **exakt in Höhe von (1) wieder ausgeglichen**
und steht danach wieder auf null; der Ertrag entsteht in voller Höhe erst mit der
Schlussrechnung.

*Warum die Steuer nicht doppelt entsteht:* beide Konten sind DATEV-Automatik-
konten desselben Steuersatzes und tragen den Bruttobetrag. Bei (1) meldet das
Anzahlungskonto die USt an; die Soll-Buchung auf dasselbe Konto in (2) **storniert
genau diese USt wieder**, während der volle Leistungsbetrag auf dem Erlöskonto die
USt neu (und diesmal endgültig) auslöst. Über beide Belege bleibt die
Umsatzsteuer damit **exakt die des Gesamtauftrags** — dieselbe Summe wie im Modus
ERLOES, nur anders und periodenrichtig verteilt.

*Cent-Genauigkeit (die Falle).* Die Steuer wird je Steuergruppe **gerundet**;
`round(Leistung·r) − round(Anrechnung·r)` kann um einen Cent von
`round((Leistung−Anrechnung)·r)` abweichen. Verbindlich ist aber `gross_total` der
Schlussrechnung. Der Leistungsteil wird deshalb **nicht neu gerechnet**, sondern
als **Rest** ermittelt: `Leistungsbuchung = Gruppensumme (wie bisher) − Anrechnung`.
Die Anrechnung kommt dabei aus den **eingefrorenen** Beträgen der Verkettung
`invoicing.invoice_advance` — exakt jenen, die beim Abschlag gebucht wurden. So
gleicht (2) das Anzahlungskonto auf den Cent aus, UND die Summe aller Buchungen
eines Belegs bleibt gleich seinem `gross_total`. (Dasselbe Verfahren nutzt
`beleg.leistungssummen()` für das Sichtbild.)

*Kreditbelege.* Storno/Gutschrift **einer Abschlagsrechnung** buchen ebenfalls
gegen das Anzahlungskonto (sonst bliebe dort ein Rest stehen). Das Storno einer
**Schlussrechnung** trägt keine Anrechnungspositionen mehr (die DB lässt
`advance_invoice_id` auf einem Kreditbeleg nicht zu) — die Aufteilung wird deshalb
aus der Verkettung der stornierten Schlussrechnung rekonstruiert und mit
umgekehrtem Vorzeichen gebucht: das Anzahlungskonto lebt wieder auf, der Abschlag
ist wieder frei. Kreditbelege zu normalen Rechnungen bleiben unverändert.

*Grenze (Modus-Wechsel):* Der Modus wirkt zum Zeitpunkt des **Exports**, nicht des
Belegs. Würde umgestellt, während Abschläge offen sind (gebucht, aber noch nicht
schlussgerechnet), löste die spätere Schlussrechnung eine Anzahlung auf, die nie
als solche gebucht wurde (bzw. umgekehrt) — auf dem Anzahlungskonto bliebe ein
Saldo stehen. Deshalb **lehnt `firma.update_company_profile` den Moduswechsel ab**,
solange `beleg.offene_abschlaege_gesamt()` etwas liefert (→ 422). Ob ERLOES oder
ANZAHLUNG fachlich richtig ist, entscheidet der Steuerberater.

Bewusste Grenzen (v1 — vor produktivem Einsatz mit dem Steuerberater abstimmen)
------------------------------------------------------------------------------
- **Sammeldebitor statt Personenkonten.** Alle Forderungen laufen gegen EIN
  Debitorenkonto (Forderungen aLuL). Eine echte OPOS-Debitorenverwaltung
  (Personenkonto je Kunde) gibt es im Schema nicht und ist ein Folge-Slice. Der
  Kundenname steht als Buchungstext an jedem Satz.
- **Automatik = aktuelle Regelsteuersätze.** Weil die Erlöskonten die Steuer per
  Automatik ableiten, passen sie zu den derzeit geltenden Sätzen (19/7/0 %,
  §13b). Ein historischer Sondersatz (z. B. 16 %) über ein 19-%-Automatikkonto
  wäre falsch — dann braucht es ein eigenes Konto + Steuercode.
- **Belegdatum als TTMM.** Der Zeitraum muss innerhalb EINES Kalenderjahres
  liegen; das Jahr ergibt sich aus dem Stapelkopf. Der EXTF-Kopf deklariert
  Formatversion 13; die Datei füllt die 14 führenden Standardspalten (DATEV
  ordnet Spalten über die Kopfzeile den Namen zu). Ein echter DATEV-Import beim
  Steuerberater ist die abschließende Abnahme.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from db_core.models import Invoice, InvoiceAdvance
from db_core.services.beleg import CREDIT_TYPES, SUMMENWIRKSAM, TEXT_TYPES
from db_core.services.firma import get_company_profile

_ROUND2 = Decimal("0.01")
_NULL = Decimal("0.00")

# Belegarten, die im Modus ANZAHLUNG gegen das Anzahlungskonto buchen.
_ADVANCE_TYPES = ("ABSCHLAGSRECHNUNG", "TEILRECHNUNG")
_FINAL_TYPE = "SCHLUSSRECHNUNG"
# Kreditbelege — EINE Liste im Repo (sie wohnt im Belegmodul). Hier stand eine
# inhaltsgleiche Kopie; zwei Listen, die dasselbe bedeuten, driften irgendwann
# auseinander, und dann bucht der Export eine Belegart falsch herum.
# `test_kreditbelegliste_kommt_aus_dem_belegmodul` beweist byte-identischen Export.
_CREDIT_TYPES = CREDIT_TYPES

MODE_REVENUE = "ERLOES"
MODE_ADVANCE = "ANZAHLUNG"
ADVANCE_MODES = (MODE_REVENUE, MODE_ADVANCE)


class DatevExportError(ValueError):
    """Der DATEV-Export ist fachlich nicht möglich (fehlende Konfiguration,
    ungültiger Zeitraum, nicht abbildbarer Steuercode) → 422."""


# SKR-Standardkonten (Sammeldebitor + Erlöskonten je Steuersatz). Nur genutzt,
# wenn im Firmenprofil kein Override gepflegt ist.
#
# Die Anzahlungskonten (`advance_*`, Migration 0063) sind die DATEV-Standard-
# konten „Erhaltene, versteuerte Anzahlungen" je Steuersatz. Belegt am
# DATEV-Kontenrahmen bzw. Haufe „Anzahlungen, erhaltene — So kontieren Sie
# richtig!":
#   19 % USt  → SKR03 1718 / SKR04 3272   (Automatikkonten, Verbindlichkeiten)
#    7 % USt  → SKR03 1711 / SKR04 3260   (Automatikkonten, Verbindlichkeiten)
# ACHTUNG: SKR04 **3270** ist „Erhaltene, versteuerte Anzahlungen 16 % USt"
# (Corona-Sondersatz 2020) — NICHT 19 %. Das richtige 19-%-Konto ist 3272.
#
# Für steuerfreie Umsätze und §13b gibt es kein Automatik-Anzahlungskonto: dort
# entsteht beim Aussteller keine Umsatzsteuer. Verwendet wird das neutrale Konto
# „Erhaltene Anzahlungen auf Bestellungen" (SKR03 1710 / SKR04 3250). Wer die
# beiden Fälle getrennt sehen will (z. B. ein eigenes §13b-Anzahlungskonto),
# trägt im Firmenprofil einen Override ein — **das ist mit dem Steuerberater
# abzustimmen**.
_SKR_DEFAULTS = {
    "SKR03": {
        "debtor": "1400",   # Forderungen aus Lieferungen und Leistungen
        "full": "8400",     # Erlöse 19 % USt (Automatik)
        "reduced": "8300",  # Erlöse 7 % USt (Automatik)
        "free": "8200",     # Erlöse (steuerfrei)
        "reverse": "8337",  # Erlöse §13b (Leistungsempfänger schuldet USt)
        "advance_full": "1718",     # Erhaltene, versteuerte Anzahlungen 19 % USt
        "advance_reduced": "1711",  # Erhaltene, versteuerte Anzahlungen 7 % USt
        "advance_free": "1710",     # Erhaltene Anzahlungen auf Bestellungen
        "advance_reverse": "1710",  # dito (kein Standard-§13b-Anzahlungskonto)
    },
    "SKR04": {
        "debtor": "1200",   # Forderungen aus Lieferungen und Leistungen
        "full": "4400",     # Erlöse 19 % USt (Automatik)
        "reduced": "4300",  # Erlöse 7 % USt (Automatik)
        "free": "4200",     # Erlöse (steuerfrei)
        "reverse": "4337",  # Erlöse §13b
        "advance_full": "3272",     # Erhaltene, versteuerte Anzahlungen 19 % USt
        "advance_reduced": "3260",  # Erhaltene, versteuerte Anzahlungen 7 % USt
        "advance_free": "3250",     # Erhaltene Anzahlungen auf Bestellungen
        "advance_reverse": "3250",  # dito
    },
}

# Steuercode → Konto-Rolle. §13b (Reverse-Charge) ist ebenfalls 0 %, verlangt aber
# ein anderes Erlöskonto als der schlichte steuerfreie Umsatz — deshalb wird nach
# dem Code unterschieden, nicht nach dem Satz. Ein unbekannter Code ist ein Fehler
# (kein stilles Fehlbuchen).
_TAX_CODE_ROLE = {
    "DE_19": "full",
    "DE_7": "reduced",
    "DE_0": "free",
    "DE_13B": "reverse",
}


def _fiscal_year_start(d, fy_month):
    """Beginn des Wirtschaftsjahres, das den Tag `d` enthält.

    Bei Kalenderjahr (fy_month=1) ist das der 1. Januar von `d.year`. Bei
    abweichendem WJ-Beginn gehört ein Tag VOR dem Startmonat noch zum
    Wirtschaftsjahr, das im Vorjahr begann (z. B. Februar bei April-WJ → 1. April
    des Vorjahres)."""
    year = d.year if d.month >= fy_month else d.year - 1
    return date(year, fy_month, 1)


@dataclass(frozen=True)
class _Booking:
    umsatz: Decimal        # Bruttobetrag der Steuergruppe, ohne Vorzeichen (>0)
    soll_haben: str        # "S" | "H"
    konto: str             # Debitor (Sammeldebitor)
    gegenkonto: str        # Erlös- bzw. Anzahlungskonto (Automatik)
    belegdatum: date       # Rechnungsdatum
    belegfeld1: str        # Rechnungsnummer
    buchungstext: str      # Kundenname (Buchungstext, ≤ 60)


# --- Konfiguration ----------------------------------------------------------

def _resolve_accounts(profile):
    """Kontonummern je Rolle: Override aus dem Profil, sonst SKR-Standard."""
    defaults = _SKR_DEFAULTS[profile.datev_chart_of_accounts]
    return {
        "debtor": profile.datev_debtor_account or defaults["debtor"],
        "full": profile.datev_revenue_account_full or defaults["full"],
        "reduced": profile.datev_revenue_account_reduced or defaults["reduced"],
        "free": profile.datev_revenue_account_free or defaults["free"],
        "reverse": profile.datev_revenue_account_reverse or defaults["reverse"],
        # Anzahlungskonten (nur im Modus ANZAHLUNG genutzt, Migration 0063)
        "advance_full": (
            profile.datev_advance_account_full or defaults["advance_full"]
        ),
        "advance_reduced": (
            profile.datev_advance_account_reduced or defaults["advance_reduced"]
        ),
        "advance_free": (
            profile.datev_advance_account_free or defaults["advance_free"]
        ),
        "advance_reverse": (
            profile.datev_advance_account_reverse or defaults["advance_reverse"]
        ),
    }


def _require_config(profile):
    """Prüft die Export-Vorbedingungen und liefert (profile, accounts)."""
    if profile is None:
        raise DatevExportError(
            "Es ist noch kein Firmenprofil gepflegt. Bitte zuerst die "
            "Firmendaten und die DATEV-Einstellungen hinterlegen."
        )
    fehlend = []
    if not profile.datev_consultant_number:
        fehlend.append("Beraternummer")
    if not profile.datev_client_number:
        fehlend.append("Mandantennummer")
    if not profile.datev_chart_of_accounts:
        fehlend.append("Kontenrahmen (SKR03/SKR04)")
    if fehlend:
        raise DatevExportError(
            "Für den DATEV-Export fehlt im Firmenprofil: "
            + ", ".join(fehlend) + "."
        )
    # Der DB-CHECK lässt nur ERLOES/ANZAHLUNG zu; NULL kann es nicht geben (NOT
    # NULL DEFAULT). Die Prüfung ist der Riegel gegen ein stilles Fehlbuchen,
    # falls je ein dritter Modus hinzukäme, ohne dass dieser Service ihn kennt.
    if (profile.datev_advance_mode or MODE_REVENUE) not in ADVANCE_MODES:
        raise DatevExportError(
            f"Unbekannter Abschlags-Buchungsmodus "
            f"'{profile.datev_advance_mode}'."
        )
    return profile, _resolve_accounts(profile)


# --- Buchungssätze ----------------------------------------------------------

def _debtor_name(invoice):
    """Name des primären Rechnungsschuldners (INVOICE_DEBTOR) oder None."""
    debtor = None
    for p in invoice.parties.all():
        if p.role == "INVOICE_DEBTOR":
            debtor = p
            if p.is_primary:
                break
    return debtor.party.display_name if debtor else None


def _role(invoice, tax_code):
    """Konto-Rolle des Steuercodes. Ein unbekannter Code ist ein Fehler (kein
    stilles Fehlbuchen)."""
    role = _TAX_CODE_ROLE.get(tax_code)
    if role is None:
        raise DatevExportError(
            f"Rechnung {invoice.invoice_number}: Steuercode "
            f"'{tax_code}' ist keinem DATEV-Erlöskonto zugeordnet."
        )
    return role


def _group_gross(invoice):
    """Bruttosumme je Steuergruppe (tax_code, Satz) — exakt die Kopf-Summenlogik
    aus `services/beleg._prepare_lines`: Netto je Gruppe summieren, Steuer je
    Gruppe runden. Die Summe über alle Gruppen ist damit `gross_total`."""
    group_net = defaultdict(lambda: _NULL)
    for line in invoice.lines.all():
        if line.line_type in TEXT_TYPES or line.line_kind != SUMMENWIRKSAM:
            continue
        if line.net_amount is None:
            continue
        group_net[(line.tax_code_id, line.tax_rate_percent)] += line.net_amount

    gross = {}
    for key, net in group_net.items():
        rate = key[1] or Decimal(0)
        tax = (net * rate / Decimal(100)).quantize(_ROUND2, rounding=ROUND_HALF_UP)
        gross[key] = net + tax
    return gross


def _bucht_auf_anzahlung(invoice, ref_type, mode):
    """Bucht dieser Beleg als GANZES gegen das Anzahlungskonto?

    Das trifft im Modus ANZAHLUNG auf Abschlags-/Teilrechnungen zu — und auf
    deren Storno/Gutschrift: würde der Kreditbeleg gegen Erlös laufen, bliebe der
    Betrag auf dem Anzahlungskonto stehen und der Erlös wäre um denselben Betrag
    zu niedrig.
    """
    if mode != MODE_ADVANCE:
        return False
    if invoice.invoice_type in _ADVANCE_TYPES:
        return True
    return invoice.invoice_type in _CREDIT_TYPES and ref_type in _ADVANCE_TYPES


def _invoice_bookings(invoice, accounts, *, mode=MODE_REVENUE, ref_type=None,
                      anrechnung=()):
    """Buchungssätze einer Rechnung. Leere/Null-Gruppen werden übersprungen.

    `anrechnung` ist im Modus ANZAHLUNG die (vorzeichenbehaftete) Anrechnung
    dieses Belegs: Tupel `(tax_code, rate, brutto, abschlagsnummer)` mit
    **negativem** Brutto auf der Schlussrechnung (Anzahlungskonto im Soll) und
    **positivem** Brutto auf deren Storno (die Anzahlung lebt wieder auf). Der
    Erlösteil einer Steuergruppe ist der REST zur Gruppensumme — nie neu gerechnet
    (Rundung, siehe Modul-Docstring).
    """
    gross = _group_gross(invoice)
    text = (_debtor_name(invoice) or invoice.invoice_type)[:60]
    ganz_auf_anzahlung = _bucht_auf_anzahlung(invoice, ref_type, mode)

    # Anrechnung je Steuergruppe aufsummieren (für den Rest), Einzelposten für die
    # Buchungssätze behalten. Eine Gruppe kann in `gross` fehlen, wenn Leistung und
    # Anrechnung sich exakt aufheben — deshalb die Schlüssel vereinigen.
    anr_summe = defaultdict(lambda: _NULL)
    for tax_code, rate, brutto, _nr in anrechnung:
        anr_summe[(tax_code, rate)] += brutto

    bookings = []
    for tax_code, rate, brutto, nummer in anrechnung:
        if brutto == 0:
            continue
        role = _role(invoice, tax_code)
        bookings.append(
            _Booking(
                umsatz=abs(brutto),
                # Negativ (Abzug auf der Schlussrechnung) → Debitor im Haben,
                # Anzahlungskonto im Soll: die Anzahlung wird aufgelöst.
                soll_haben="S" if brutto > 0 else "H",
                konto=accounts["debtor"],
                gegenkonto=accounts["advance_" + role],
                belegdatum=invoice.invoice_date,
                belegfeld1=invoice.invoice_number or "",
                # Der Rückverweis auf den Abschlag steht im Buchungstext (60
                # Zeichen) — Belegfeld 2 ist dafür zu kurz und anders belegt.
                buchungstext=f"{text} / Anrechnung {nummer or ''}".strip()[:60],
            )
        )

    # Reihenfolge = Einfügereihenfolge der Positionen (wie bisher); Gruppen, die es
    # NUR in der Anrechnung gibt (Leistung und Abzug heben sich exakt auf), hinten
    # anhängen. So bleibt der Modus ERLOES zeichengleich zum bisherigen Export.
    keys = list(gross) + [k for k in anr_summe if k not in gross]
    for key in keys:
        tax_code, _rate = key
        rest = gross.get(key, _NULL) - anr_summe.get(key, _NULL)
        if rest == 0:
            continue
        role = _role(invoice, tax_code)
        konto_rolle = ("advance_" + role) if ganz_auf_anzahlung else role
        bookings.append(
            _Booking(
                umsatz=abs(rest),
                # Normaler Verkauf: Forderung im Soll (S). Gutschrift/Storno
                # (negative Gruppe): Umkehr auf Haben (H).
                soll_haben="S" if rest > 0 else "H",
                konto=accounts["debtor"],
                gegenkonto=accounts[konto_rolle],
                belegdatum=invoice.invoice_date,
                belegfeld1=invoice.invoice_number or "",
                buchungstext=text,
            )
        )
    return bookings


def _anzahlungs_kontext(invoices, mode):
    """(ref_types, anrechnungen) für den Modus ANZAHLUNG.

    - `ref_types`: {reference_invoice_id: invoice_type} für alle Kreditbelege der
      Auswahl. Ein Storno „erbt" seine Kontierung vom Ursprungsbeleg.
    - `anrechnungen`: {invoice_id: [(tax_code, rate, brutto, abschlagsnummer), …]}
      mit **vorzeichenbehaftetem** Brutto:
        * Schlussrechnung  → negativ (Anzahlung wird aufgelöst),
        * Storno/Gutschrift einer Schlussrechnung → positiv (Anzahlung lebt auf).
      Die Beträge kommen aus der eingefrorenen Verkettung `invoicing.invoice_advance`
      — dieselben Zahlen, die beim Abschlag gebucht wurden. Nur so gleicht die
      Auflösung das Anzahlungskonto auf den Cent aus.

    Im Modus ERLOES ist beides leer: der Export bleibt Zeichen für Zeichen der
    bisherige.
    """
    if mode != MODE_ADVANCE:
        return {}, {}

    ref_ids = {
        inv.reference_invoice_id
        for inv in invoices
        if inv.invoice_type in _CREDIT_TYPES and inv.reference_invoice_id
    }
    ref_types = dict(
        Invoice.objects.filter(id__in=ref_ids).values_list("id", "invoice_type")
    ) if ref_ids else {}

    # Verkettung laden: für Schlussrechnungen der Auswahl UND für die
    # Schlussrechnungen, die von einem Kreditbeleg der Auswahl storniert werden
    # (deren Storno trägt selbst keine Anrechnungspositionen mehr).
    final_ids = {inv.id for inv in invoices if inv.invoice_type == _FINAL_TYPE}
    storno_von = {
        inv.id: inv.reference_invoice_id
        for inv in invoices
        if inv.invoice_type in _CREDIT_TYPES
        and ref_types.get(inv.reference_invoice_id) == _FINAL_TYPE
    }
    alle_finals = final_ids | set(storno_von.values())
    if not alle_finals:
        return ref_types, {}

    rows = defaultdict(list)
    for row in (
        InvoiceAdvance.objects.filter(final_invoice_id__in=alle_finals)
        .select_related("advance_invoice")
        .order_by("advance_invoice__invoice_number", "tax_code")
    ):
        rows[row.final_invoice_id].append(
            (
                row.tax_code_id,
                row.tax_rate_percent,
                row.gross_amount,           # eingefroren, POSITIV
                row.advance_invoice.invoice_number,
            )
        )

    anrechnungen = {}
    for final_id in final_ids:
        # Auf der Schlussrechnung ist die Anrechnung ein ABZUG (negativ).
        anrechnungen[final_id] = [
            (code, rate, -brutto, nr) for code, rate, brutto, nr in rows[final_id]
        ]
    for credit_id, final_id in storno_von.items():
        # Der Kreditbeleg dreht sie um: die Anzahlung lebt wieder auf (positiv).
        anrechnungen[credit_id] = list(rows[final_id])
    return ref_types, anrechnungen


# --- EXTF-Serialisierung ----------------------------------------------------
# DATEV EXTF: Semikolon-getrennt, Textfelder in doppelten Anführungszeichen,
# Zahlen/Datumsfelder unquotiert, Zeichensatz Windows-1252 (cp1252). Wir bauen die
# Felder typisiert zusammen, weil Header- und Datenzeilen quotierte Text- und
# rohe Zahlenfelder mischen (ein einheitliches csv-Quoting passt nicht).

# Die 14 führenden Standardspalten des Buchungsstapels (Reihenfolge fix).
_COLUMNS = [
    "Umsatz (ohne Soll/Haben-Kz)",
    "Soll/Haben-Kennzeichen",
    "WKZ Umsatz",
    "Kurs",
    "Basis-Umsatz",
    "WKZ Basis-Umsatz",
    "Konto",
    "Gegenkonto (ohne BU-Schlüssel)",
    "BU-Schlüssel",
    "Belegdatum",
    "Belegfeld 1",
    "Belegfeld 2",
    "Skonto",
    "Buchungstext",
]


def _q(text):
    """Quotiertes Textfeld (interne Anführungszeichen verdoppelt, Zeilenumbrüche
    entfernt). Leer → leeres, unquotiertes Feld."""
    if text is None or text == "":
        return ""
    s = str(text).replace('"', '""').replace("\r", " ").replace("\n", " ")
    return f'"{s}"'


def _amount(value):
    """Decimal → DATEV-Betrag (deutsches Komma, zwei Nachkommastellen, ohne
    Tausendertrenner, ohne Vorzeichen — das Vorzeichen trägt S/H)."""
    return f"{value:.2f}".replace(".", ",")


def _booking_row(b):
    return ";".join([
        _amount(b.umsatz),           # 1 Umsatz
        _q(b.soll_haben),            # 2 Soll/Haben-Kennzeichen
        "",                          # 3 WKZ Umsatz (leer = Basiswährung EUR)
        "",                          # 4 Kurs
        "",                          # 5 Basis-Umsatz
        "",                          # 6 WKZ Basis-Umsatz
        b.konto,                     # 7 Konto (Debitor)
        b.gegenkonto,                # 8 Gegenkonto (Erlöskonto, Automatik)
        "",                          # 9 BU-Schlüssel (leer: Automatik aus Konto)
        b.belegdatum.strftime("%d%m"),  # 10 Belegdatum TTMM
        _q(b.belegfeld1),            # 11 Belegfeld 1 (Rechnungsnummer)
        # 12 Belegfeld 2 bleibt LEER. Es ist auf 12 Zeichen begrenzt (eine
        # Belegnummer RE-2026-000001 hat 14) und trägt konventionell das
        # Fälligkeitsdatum (TTMMJJ) — die angerechnete Abschlagsrechnung gehört
        # deshalb NICHT hierher, sondern in den Buchungstext (60 Zeichen).
        "",
        "",                          # 13 Skonto
        _q(b.buchungstext),          # 14 Buchungstext (Kundenname)
    ])


def _header_row(profile, von, bis, *, erzeugt_am):
    """Kopfzeile 1 des Buchungsstapels (Metadaten). 31 Felder, gemischt quotiert."""
    length = profile.datev_account_length or 4
    fy_month = profile.datev_fiscal_year_start_month or 1
    wj_beginn = _fiscal_year_start(von, fy_month)
    return ";".join([
        _q("EXTF"),                              # 1 Kennzeichen
        "700",                                   # 2 Versionsnummer
        "21",                                    # 3 Datenkategorie (Buchungsstapel)
        _q("Buchungsstapel"),                    # 4 Formatname
        "13",                                    # 5 Formatversion
        erzeugt_am.strftime("%Y%m%d%H%M%S") + "000",  # 6 erzeugt am (17 Stellen)
        "",                                      # 7 importiert
        _q("RE"),                                # 8 Herkunft
        "",                                      # 9 exportiert von
        "",                                      # 10 importiert von
        str(profile.datev_consultant_number),    # 11 Beraternummer
        str(profile.datev_client_number),        # 12 Mandantennummer
        wj_beginn.strftime("%Y%m%d"),            # 13 Wirtschaftsjahresbeginn
        str(length),                             # 14 Sachkontenlänge
        von.strftime("%Y%m%d"),                  # 15 Datum von
        bis.strftime("%Y%m%d"),                  # 16 Datum bis
        "",                                      # 17 Bezeichnung
        "",                                      # 18 Diktatkürzel
        "1",                                     # 19 Buchungstyp (Finanzbuchführung)
        "0",                                     # 20 Rechnungslegungszweck
        "0",                                     # 21 Festschreibung (0 = nicht festgeschrieben)
        _q("EUR"),                               # 22 WKZ
        "", "", "", "",                          # 23–26 reserviert
        "", "",                                  # 27–28 reserviert
        "", "", "",                              # 29–31 reserviert
    ])


# --- Öffentliche API --------------------------------------------------------

def build_datev_export(von, bis, *, erzeugt_am=None):
    """Baut den EXTF-Buchungsstapel für den Zeitraum [von, bis].

    Gibt `(dateiname, inhalt_bytes)` zurück; `inhalt_bytes` ist cp1252-kodiert
    (DATEV-Vorgabe). Wirft `DatevExportError` (→ 422) bei fehlender Konfiguration,
    ungültigem Zeitraum oder nicht abbildbarem Steuercode.

    Der Zeitraum muss innerhalb eines Kalenderjahres liegen (Belegdatum wird als
    TTMM geschrieben; das Jahr steht im Stapelkopf).
    """
    if von > bis:
        raise DatevExportError("Das Von-Datum liegt nach dem Bis-Datum.")
    # Belegdatum wird als TTMM geschrieben (Jahr aus dem Stapelkopf) → der
    # Zeitraum muss in EINEM Kalenderjahr liegen, sonst wäre das Jahr mehrdeutig.
    if von.year != bis.year:
        raise DatevExportError(
            "Der Zeitraum muss innerhalb eines Kalenderjahres liegen."
        )
    profile, accounts = _require_config(get_company_profile())
    # …und in EINEM Wirtschaftsjahr: bei abweichendem WJ-Beginn (z. B. April)
    # trennt die WJ-Grenze zwei Buchungsjahre, die DATEV nicht in einem Stapel
    # importiert. Bei Kalenderjahr-WJ (Default) fällt diese Prüfung mit der
    # Kalenderjahr-Schranke zusammen.
    fy_month = profile.datev_fiscal_year_start_month or 1
    if _fiscal_year_start(von, fy_month) != _fiscal_year_start(bis, fy_month):
        raise DatevExportError(
            "Der Zeitraum muss innerhalb eines Wirtschaftsjahres liegen."
        )
    erzeugt_am = erzeugt_am or datetime.now()

    invoices = list(
        Invoice.objects.filter(
            status="VEROEFFENTLICHT",
            invoice_date__gte=von,
            invoice_date__lte=bis,
        )
        .prefetch_related("lines", "parties__party")
        .order_by("invoice_date", "invoice_number")
    )
    mode = profile.datev_advance_mode or MODE_REVENUE
    ref_types, anrechnungen = _anzahlungs_kontext(invoices, mode)

    zeilen = [_header_row(profile, von, bis, erzeugt_am=erzeugt_am),
              ";".join(_q(c) for c in _COLUMNS)]
    for invoice in invoices:
        bookings = _invoice_bookings(
            invoice, accounts, mode=mode,
            ref_type=ref_types.get(invoice.reference_invoice_id),
            anrechnung=anrechnungen.get(invoice.id, ()),
        )
        for booking in bookings:
            zeilen.append(_booking_row(booking))

    # Zeilenende CRLF (DATEV), abschließendes CRLF inklusive.
    text = "\r\n".join(zeilen) + "\r\n"
    inhalt = text.encode("cp1252", errors="replace")
    dateiname = f"EXTF_Buchungsstapel_{von:%Y%m%d}_{bis:%Y%m%d}.csv"
    return dateiname, inhalt
