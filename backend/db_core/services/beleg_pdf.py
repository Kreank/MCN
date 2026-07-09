"""Beleg-PDF: erzeugt on-the-fly ein Rechnungs-/Gutschrift-PDF aus den
(eingefrorenen) Belegdaten.

Rein lesende Ausgabe — eine veröffentlichte Rechnung ist unveränderlich (B-30),
daher entspricht das aus den Live-Modelldaten gerenderte PDF dem festgeschriebenen
Beleg. Die persistente GoBD-Archivierung (content.document + file_link,
Einmaligkeits-Index) über MinIO ist ein späterer Schritt und NICHT Voraussetzung
der Veröffentlichung.

Nutzt fpdf2 (reines Python). Beträge in deutscher Formatierung.
"""
from decimal import Decimal

from fpdf import FPDF

from db_core.models import CompanyProfile, Invoice

# Fallback-Aussteller, solange kein Firmenprofil gepflegt ist (kein Absturz).
_FALLBACK_NAME = "MCN Gebäudeservice"
_FALLBACK_SUBLINE = "Firmenprofil noch nicht gepflegt · Einstellungen › Firmenprofil"

_TYPE_TITLES = {
    "RECHNUNG": "Rechnung",
    "ABSCHLAGSRECHNUNG": "Abschlagsrechnung",
    "TEILRECHNUNG": "Teilrechnung",
    "SCHLUSSRECHNUNG": "Schlussrechnung",
    "GUTSCHRIFT": "Gutschrift",
    "STORNO": "Stornorechnung",
}


def _txt(value):
    """Latin-1-sichere Textausgabe für den fpdf2-Kernfont (ersetzt nicht
    darstellbare Zeichen, damit exotische Altdaten kein 500 auslösen)."""
    if value is None:
        return ""
    return str(value).encode("latin-1", "replace").decode("latin-1")


def _eur(value):
    """Formatiert einen Decimal/None als deutschen Eurobetrag (1.234,56 EUR).

    Das €-Zeichen liegt außerhalb von Latin-1 (fpdf2-Kernfont) — daher „EUR"."""
    if value is None:
        return "-"
    q = Decimal(value).quantize(Decimal("0.01"))
    s = f"{q:,.2f}"  # 1,234.56
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{s} EUR"


def _de_date(d):
    return d.strftime("%d.%m.%Y") if d else "-"


def _issuer_lines(profile):
    """(Name, Unterzeile) des Ausstellers — pure Funktion (ohne Profil: Fallback)."""
    if profile is None:
        return _FALLBACK_NAME, _FALLBACK_SUBLINE
    name = profile.company_name
    addr_bits = [
        profile.street,
        " ".join(b for b in (profile.postal_code, profile.city) if b),
    ]
    subline = " · ".join(b for b in addr_bits if b) or (profile.legal_form or "")
    return name, subline


def _footer_parts(profile):
    """Liste der Fußzeilen-Angaben (Steuer/Register/Bank) — nur was gepflegt ist."""
    if profile is None:
        return []
    parts = []
    if profile.tax_number:
        parts.append(f"Steuernr.: {profile.tax_number}")
    if profile.vat_id:
        parts.append(f"USt-IdNr.: {profile.vat_id}")
    if profile.commercial_register:
        parts.append(profile.commercial_register)
    if profile.managing_director:
        title = profile.managing_director_title or "Geschäftsführung"
        parts.append(f"{title}: {profile.managing_director}")
    bank = []
    if profile.bank_name:
        bank.append(profile.bank_name)
    if profile.iban:
        bank.append(f"IBAN {profile.iban}")
    if profile.bic:
        bank.append(f"BIC {profile.bic}")
    if bank:
        parts.append(" · ".join(bank))
    return parts


def _render_issuer(pdf, profile):
    """Kopfzeile des Ausstellers aus dem Firmenprofil (oder Fallback)."""
    name, subline = _issuer_lines(profile)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _txt(name), new_x="LMARGIN", new_y="NEXT")
    if subline:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(110, 110, 110)
        pdf.cell(0, 5, _txt(subline), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(6)


def _render_footer(pdf, profile):
    """Fußzeile mit Steuer-/Register-/Bankangaben (nur was gepflegt ist)."""
    parts = _footer_parts(profile)
    if not parts:
        return
    pdf.ln(6)
    pdf.set_draw_color(210, 210, 210)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4, _txt(" · ".join(parts)), border="T")
    pdf.set_text_color(0, 0, 0)


def render_invoice_pdf(invoice_id):
    """Rendert das PDF einer veröffentlichten Rechnung und gibt die Bytes zurück.

    Gibt None zurück, wenn die Rechnung nicht existiert oder nicht veröffentlicht
    ist (nur festgeschriebene Belege erhalten eine Ausfertigung).
    """
    invoice = (
        Invoice.objects.filter(id=invoice_id)
        .select_related("property__address")
        .prefetch_related("lines", "parties__party")
        .first()
    )
    if invoice is None or invoice.status != "VEROEFFENTLICHT":
        return None

    title = _TYPE_TITLES.get(invoice.invoice_type, invoice.invoice_type)
    debtor = _party(invoice, "INVOICE_DEBTOR")
    recipient = _party(invoice, "INVOICE_RECIPIENT") or debtor

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 18, 20)
    pdf.add_page()

    # Aussteller aus dem Firmenprofil (Singleton). Ohne gepflegtes Profil ein
    # neutraler Fallback statt Absturz.
    profile = CompanyProfile.objects.first()
    _render_issuer(pdf, profile)

    # Empfänger
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, _txt(recipient) or "-", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Belegkopf
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, f"{title} {invoice.invoice_number or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Belegdatum: {_de_date(invoice.invoice_date)}"
                   f"    Fällig: {_de_date(invoice.due_date)}", new_x="LMARGIN", new_y="NEXT")
    if debtor and debtor != recipient:
        pdf.cell(0, 6, _txt(f"Rechnungsschuldner: {debtor}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Positionstabelle
    widths = (12, 82, 20, 16, 28, 32)  # Summe 190 mm (usable width)
    headers = ("Pos", "Beschreibung", "Menge", "Einheit", "Einzelpreis", "Betrag")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(238, 238, 238)
    for w, h in zip(widths, headers):
        align = "R" if h in ("Menge", "Einzelpreis", "Betrag") else "L"
        pdf.cell(w, 7, h, border="B", align=align, fill=True)
    pdf.ln(7)

    pdf.set_font("Helvetica", "", 9)
    for line in sorted(invoice.lines.all(), key=lambda x: x.position_number):
        is_text = line.line_type in ("TEXT", "ZWISCHENSUMME")
        menge = "" if is_text or line.quantity is None else _num(line.quantity)
        einheit = "" if is_text else _txt(line.unit or "")
        ep = "" if is_text or line.unit_price is None else _eur(line.unit_price)
        betrag = "" if is_text or line.net_amount is None else _eur(line.net_amount)
        y0 = pdf.get_y()
        pdf.multi_cell(widths[0], 6, str(line.position_number), align="L",
                       new_x="RIGHT", new_y="TOP", max_line_height=6)
        pdf.set_xy(20 + widths[0], y0)
        pdf.multi_cell(widths[1], 6, _txt(line.description), align="L",
                       new_x="RIGHT", new_y="TOP", max_line_height=6)
        y1 = pdf.get_y()
        pdf.set_xy(20 + widths[0] + widths[1], y0)
        pdf.cell(widths[2], 6, menge, align="R")
        pdf.cell(widths[3], 6, einheit, align="L")
        pdf.cell(widths[4], 6, ep, align="R")
        pdf.cell(widths[5], 6, betrag, align="R")
        pdf.set_y(max(y0 + 6, y1))

    pdf.ln(3)
    # Summen
    pdf.set_draw_color(180, 180, 180)
    label_w, val_w = 150, 40
    for label, value, bold in (
        ("Nettobetrag", invoice.net_total, False),
        ("Umsatzsteuer", invoice.tax_total, False),
        ("Gesamtbetrag", invoice.gross_total, True),
    ):
        pdf.set_font("Helvetica", "B" if bold else "", 10 if bold else 9)
        pdf.cell(label_w, 7, label, align="R")
        pdf.cell(val_w, 7, _eur(value), align="R", border="T" if bold else 0, new_x="LMARGIN", new_y="NEXT")

    if invoice.invoice_type in ("GUTSCHRIFT", "STORNO") and invoice.reference_invoice_id:
        ref = Invoice.objects.filter(id=invoice.reference_invoice_id).first()
        if ref:
            pdf.ln(4)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(110, 110, 110)
            pdf.multi_cell(0, 5, _txt(f"Bezieht sich auf Ursprungsbeleg "
                                      f"{ref.invoice_number or ref.invoice_type}."))
            pdf.set_text_color(0, 0, 0)

    _render_footer(pdf, profile)

    out = pdf.output()
    return bytes(out)


def _party(invoice, role):
    """Anzeigename des primären Beteiligten einer Rolle (sonst irgendeiner)."""
    chosen = None
    for p in invoice.parties.all():
        if p.role == role:
            chosen = p
            if p.is_primary:
                break
    return chosen.party.display_name if chosen else None


def _num(value):
    q = Decimal(value)
    s = f"{q.normalize():f}" if q == q.to_integral() else f"{q}"
    return s.replace(".", ",")
