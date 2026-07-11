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

from db_core.models import Invoice
from db_core.services.beleg import SUMMENWIRKSAM, TEXT_TYPES
from db_core.services.firma import get_company_profile

_ROUND2 = Decimal("0.01")


class DatevExportError(ValueError):
    """Der DATEV-Export ist fachlich nicht möglich (fehlende Konfiguration,
    ungültiger Zeitraum, nicht abbildbarer Steuercode) → 422."""


# SKR-Standardkonten (Sammeldebitor + Erlöskonten je Steuersatz). Nur genutzt,
# wenn im Firmenprofil kein Override gepflegt ist.
_SKR_DEFAULTS = {
    "SKR03": {
        "debtor": "1400",   # Forderungen aus Lieferungen und Leistungen
        "full": "8400",     # Erlöse 19 % USt (Automatik)
        "reduced": "8300",  # Erlöse 7 % USt (Automatik)
        "free": "8200",     # Erlöse (steuerfrei)
        "reverse": "8337",  # Erlöse §13b (Leistungsempfänger schuldet USt)
    },
    "SKR04": {
        "debtor": "1200",   # Forderungen aus Lieferungen und Leistungen
        "full": "4400",     # Erlöse 19 % USt (Automatik)
        "reduced": "4300",  # Erlöse 7 % USt (Automatik)
        "free": "4200",     # Erlöse (steuerfrei)
        "reverse": "4337",  # Erlöse §13b
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
    gegenkonto: str        # Erlöskonto (Automatik)
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


def _invoice_bookings(invoice, accounts):
    """Buchungssätze einer Rechnung (je Steuergruppe einer). Leere/Null-Gruppen
    werden übersprungen."""
    # Netto je (tax_code, Satz) summieren — exakt wie die Kopf-Summenlogik.
    group_net = defaultdict(lambda: Decimal("0.00"))
    for line in invoice.lines.all():
        if line.line_type in TEXT_TYPES or line.line_kind != SUMMENWIRKSAM:
            continue
        if line.net_amount is None:
            continue
        group_net[(line.tax_code_id, line.tax_rate_percent)] += line.net_amount

    text = (_debtor_name(invoice) or invoice.invoice_type)[:60]
    bookings = []
    for (tax_code, rate), net in group_net.items():
        role = _TAX_CODE_ROLE.get(tax_code)
        if role is None:
            raise DatevExportError(
                f"Rechnung {invoice.invoice_number}: Steuercode "
                f"'{tax_code}' ist keinem DATEV-Erlöskonto zugeordnet."
            )
        tax = (net * (rate or Decimal(0)) / Decimal(100)).quantize(
            _ROUND2, rounding=ROUND_HALF_UP
        )
        gross = net + tax
        if gross == 0:
            continue
        bookings.append(
            _Booking(
                umsatz=abs(gross),
                # Normaler Verkauf: Forderung im Soll (S). Gutschrift/Storno
                # (negative Gruppe): Umkehr auf Haben (H).
                soll_haben="S" if gross > 0 else "H",
                konto=accounts["debtor"],
                gegenkonto=accounts[role],
                belegdatum=invoice.invoice_date,
                belegfeld1=invoice.invoice_number or "",
                buchungstext=text,
            )
        )
    return bookings


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
        "",                          # 12 Belegfeld 2
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

    invoices = (
        Invoice.objects.filter(
            status="VEROEFFENTLICHT",
            invoice_date__gte=von,
            invoice_date__lte=bis,
        )
        .prefetch_related("lines", "parties__party")
        .order_by("invoice_date", "invoice_number")
    )

    zeilen = [_header_row(profile, von, bis, erzeugt_am=erzeugt_am),
              ";".join(_q(c) for c in _COLUMNS)]
    for invoice in invoices:
        for booking in _invoice_bookings(invoice, accounts):
            zeilen.append(_booking_row(booking))

    # Zeilenende CRLF (DATEV), abschließendes CRLF inklusive.
    text = "\r\n".join(zeilen) + "\r\n"
    inhalt = text.encode("cp1252", errors="replace")
    dateiname = f"EXTF_Buchungsstapel_{von:%Y%m%d}_{bis:%Y%m%d}.csv"
    return dateiname, inhalt
