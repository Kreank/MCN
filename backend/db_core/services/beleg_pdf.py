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

from db_core.models import Invoice

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

    # Aussteller (Platzhalter — Firmenprofil folgt mit den Einstellungen).
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "MCN Gebäudeservice", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 5, "Handwerk & Gebäudeservice · Musterstadt", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

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
