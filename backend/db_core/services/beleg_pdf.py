"""Beleg-PDF: erzeugt on-the-fly ein Rechnungs-/Gutschrift-PDF aus den
(eingefrorenen) Belegdaten und archiviert es beim ersten Abruf GoBD-fest.

Rein lesende Ausgabe — eine veröffentlichte Rechnung ist unveränderlich (B-30),
daher entspricht das aus den Live-Modelldaten gerenderte PDF dem festgeschriebenen
Beleg.

Archivierung (GoBD, eine Ausfertigung je Beleg):
Beim ERSTEN Abruf des PDF einer veröffentlichten Rechnung wird die Ausfertigung
gerendert, in den Objektspeicher (MinIO) gelegt und als ``content.file``
(sha256, size, storage_key, mime application/pdf) registriert sowie per
``content.file_link`` (link_category='BELEG_PDF') an den Beleg gebunden. Jeder
weitere Abruf liefert **dieselbe** archivierte Datei aus dem Speicher, nicht neu
gerendert. Die Einmaligkeit erzwingt der partielle UNIQUE-Index aus Migration
0032 physisch; den Wettlauf zweier paralleler Erstabrufe fängt die API mit
Nachselektion ab (Finding P-1), ohne 500.

Degradation: Ist der Objektspeicher nicht erreichbar/authentifizierbar, bleibt
der Beleg **zugänglich** — es wird on-the-fly ausgeliefert und die Archivierung
mit einer klaren Log-Warnung übersprungen. Ein kaputter Objektspeicher darf den
Beleg nie unzugänglich machen; die Archivierung wird beim nächsten Abruf
nachgeholt, sobald der Speicher wieder da ist.

Nutzt fpdf2 (reines Python). Beträge in deutscher Formatierung.

Schrift: **eingebettete TrueType-Schrift** (DejaVu Sans, freie Lizenz, unter
``db_core/assets/fonts/``) statt des fpdf2-Kernfonts Helvetica. Zwei Gründe:
1. PDF/A-3B (E-Rechnung, services/erechnung.py) verlangt zwingend eingebettete
   Schriften — ein Kernfont ist dort verboten. Beide Ausfertigungen teilen sich
   dasselbe Layout, also gilt die Schrift für beide.
2. Der Kernfont kann nur Latin-1; Umlaute mussten ersetzt werden, das €-Zeichen
   war gar nicht darstellbar. Mit DejaVu ist Unicode-Text unverfälscht.

**Bekannte Sichtbild-Divergenz bei Altbelegen** (bewusst in Kauf genommen):
Belege, deren BELEG_PDF VOR der Font-Umstellung archiviert wurde, behalten ihre
archivierte Ausfertigung (Helvetica, Empfänger nur als Name). Wird für denselben
Beleg später eine E-Rechnung erzeugt, trägt deren Sichtbild die neue Typografie
und den vollständigen Anschriftsblock. **Beträge, Positionen und Summen sind
identisch** — es gibt keinen Datenwiderspruch, nur zwei optisch verschiedene
Ausfertigungen desselben Belegs. Ein Neurendern der archivierten Ausfertigung
wäre die Alternative gewesen und ist ausgeschlossen (GoBD: eine Ausfertigung,
einmal abgelegt, bleibt).
"""
import logging
import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.db import IntegrityError, connection
from fpdf import FPDF

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import CompanyProfile, File, Invoice, Quote
from db_core.services.beleg import (
    FINAL_TYPE,
    anzeige_menge_preis,
    beleg_stammdaten,
    beteiligter,
    issuer_stammdaten,
    leistungssummen,
    zahlungsbedingungen,
)

log = logging.getLogger(__name__)

_BELEG_PDF_CATEGORY = "BELEG_PDF"

# Eingebettete Schrift (freie Lizenz; LICENSE-DejaVu.txt liegt daneben).
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_FAMILY = "DejaVu"
_FONT_FILES = {"": "DejaVuSans.ttf", "B": "DejaVuSans-Bold.ttf"}


def new_beleg_pdf(*, compliance=None):
    """Ein leeres A4-Beleg-PDF mit eingebetteter Schrift und Belegrändern.

    `compliance` reicht fpdf2s `enforce_compliance` durch (z. B. "PDF/A-3B" für
    die E-Rechnung). fpdf2 setzt dann OutputIntent + XMP und verweigert
    nicht-eingebettete Schriften — deshalb registrieren wir die TTF immer,
    nicht nur im PDF/A-Fall.
    """
    pdf = FPDF(format="A4", unit="mm", enforce_compliance=compliance)
    for style, datei in _FONT_FILES.items():
        pdf.add_font(FONT_FAMILY, style, str(FONT_DIR / datei))
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 18, 20)
    pdf.add_page()
    return pdf

# Ein Angebot erhält erst ab dem Versand eine finale Ausfertigung: vorher (ENTWURF/
# INTERN_GEPRUEFT/FREIGEGEBEN) ist es unverbindlicher Entwurf ohne Belegnummer und
# ohne eingefrorenen Snapshot — ein „finales" PDF würde eine Verbindlichkeit
# vortäuschen, die es nicht gibt. Ab VERSENDET ist der Beleg festgeschrieben
# (Snapshot + Hash, B-30); die Folgestatus sind reine Kundenantworten am selben,
# unveränderten Dokument und erhalten daher dieselbe Ausfertigung.
_QUOTE_PDF_STATUSES = frozenset(
    {"VERSENDET", "ANGENOMMEN", "ABGELEHNT", "ABGELAUFEN", "ERSETZT"}
)

# Abgeleiteter Angebotsempfänger (best-effort) über den optionalen Auftrag:
# primärer INVOICE_RECIPIENT gewinnt, ersatzweise PRINCIPAL. Reihenfolge = Priorität.
_QUOTE_RECIPIENT_ROLES = ("INVOICE_RECIPIENT", "PRINCIPAL")

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
    """None-sichere Textausgabe. Seit der Umstellung auf die eingebettete
    DejaVu-Schrift ist keine Latin-1-Ersetzung mehr nötig — Umlaute, €, ² usw.
    werden unverfälscht gesetzt."""
    if value is None:
        return ""
    return str(value)


def _eur(value):
    """Formatiert einen Decimal/None als deutschen Eurobetrag (1.234,56 EUR).

    Bewusst „EUR" statt „€": im Beleg-Kontext gebräuchlich und in jedem Viewer
    eindeutig (die Schrift könnte inzwischen auch €)."""
    if value is None:
        return "-"
    q = Decimal(value).quantize(Decimal("0.01"))
    s = f"{q:,.2f}"  # 1,234.56
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{s} EUR"


def _de_date(d):
    return d.strftime("%d.%m.%Y") if d else "-"


def _de_prozent(value):
    """Prozentsatz in deutscher Schreibweise mit zwei Nachkommastellen (2,00)."""
    return f"{Decimal(value).quantize(Decimal('0.01'))}".replace(".", ",")


def zahlungsbedingungen_text(invoice):
    """Die Zahlungsbedingungs-Zeile des Belegs (oder None).

    Rechnet nicht selbst: der Skontobetrag kommt aus `beleg.zahlungsbedingungen()`,
    damit PDF und Bildschirm nie zwei verschiedene Beträge zeigen.
    """
    zb = zahlungsbedingungen(invoice)
    if zb:
        text = (
            f"Zahlungsbedingungen: {_de_prozent(zb['discount_percent'])} % Skonto "
            f"bei Zahlung bis {_de_date(zb['skonto_bis'])} "
            f"({_eur(zb['skonto_betrag'])})"
        )
        if zb["zahlbar_bis"]:
            return f"{text}, sonst netto bis {_de_date(zb['zahlbar_bis'])}."
        return f"{text}, sonst netto ohne Abzug."
    # Ohne Skonto, aber mit Fälligkeit: die Zahlungsfrist trotzdem ausschreiben.
    # Gutschrift/Storno fordern kein Geld (und tragen laut DB keine Bedingungen).
    if invoice.due_date and invoice.invoice_type not in ("GUTSCHRIFT", "STORNO"):
        return f"Zahlbar ohne Abzug bis {_de_date(invoice.due_date)}."
    return None


def _issuer_lines(issuer):
    """(Name, Unterzeile) des Ausstellers — pure Funktion.

    `issuer` ist der Stammdaten-Snapshot aus `beleg.beleg_stammdaten` (dict) bzw.
    `beleg.issuer_stammdaten` (Angebot: live). None ⇒ Fallback ohne Absturz.
    """
    if not issuer:
        return _FALLBACK_NAME, _FALLBACK_SUBLINE
    addr_bits = [
        issuer.get("street"),
        " ".join(
            b for b in (issuer.get("postal_code"), issuer.get("city")) if b
        ),
    ]
    subline = " · ".join(b for b in addr_bits if b) or (issuer.get("legal_form") or "")
    return issuer.get("company_name"), subline


def _footer_parts(issuer):
    """Liste der Fußzeilen-Angaben (Steuer/Register/Bank) — nur was gepflegt ist."""
    if not issuer:
        return []
    parts = []
    if issuer.get("tax_number"):
        parts.append(f"Steuernr.: {issuer['tax_number']}")
    if issuer.get("vat_id"):
        parts.append(f"USt-IdNr.: {issuer['vat_id']}")
    if issuer.get("commercial_register"):
        parts.append(issuer["commercial_register"])
    if issuer.get("managing_director"):
        title = issuer.get("managing_director_title") or "Geschäftsführung"
        parts.append(f"{title}: {issuer['managing_director']}")
    bank = []
    if issuer.get("bank_name"):
        bank.append(issuer["bank_name"])
    if issuer.get("iban"):
        bank.append(f"IBAN {issuer['iban']}")
    if issuer.get("bic"):
        bank.append(f"BIC {issuer['bic']}")
    if bank:
        parts.append(" · ".join(bank))
    return parts


def empfaenger_zeilen(party_snapshot):
    """Anschriftsblock eines Beteiligten (Name + Adresszeilen) als Liste.

    Ohne Adresse bleibt nur der Name — ein Beleg muss auch dann rendern, wenn
    beim Kontakt keine Anschrift gepflegt ist.
    """
    if not party_snapshot:
        return []
    zeilen = [party_snapshot.get("display_name") or "-"]
    addr = party_snapshot.get("address")
    if addr:
        strasse = " ".join(
            b for b in (addr.get("street"), addr.get("house_number")) if b
        )
        if addr.get("address_addition"):
            zeilen.append(addr["address_addition"])
        if strasse:
            zeilen.append(strasse)
        ort = " ".join(b for b in (addr.get("postal_code"), addr.get("city")) if b)
        if ort:
            zeilen.append(ort)
    return zeilen


# Rahmen (mm), in den das Logo oben rechts eingepasst wird (Seitenverhältnis
# gewahrt). Höhe bewusst knapp unter dem Aussteller-Textblock (Name + Unterzeile),
# damit das Logo nicht in den Empfängerbereich darunter ragt.
_LOGO_BOX_W_MM = 50
_LOGO_BOX_H_MM = 20


def _logo_bytes(profile):
    """Bytes des Firmenlogos für den PDF-Kopf, oder None.

    Graceful degradation (wie bei der PDF-Archivierung): kein Logo gesetzt / der
    content.file-Steckbrief fehlt / der Objektspeicher ist nicht erreichbar → None;
    der Kopf wird dann ohne Logo gerendert. Ein Logo darf das PDF nie scheitern
    lassen.
    """
    file_id = getattr(profile, "logo_file_id", None) if profile else None
    if file_id is None:
        return None
    try:
        datei = File.objects.filter(id=file_id).only("id", "storage_key").first()
        if datei is None:
            return None
        return storage_module.get_storage().get_object(datei.storage_key)
    except storage_module.StorageError as exc:
        log.warning(
            "Beleg-PDF: Firmenlogo nicht abrufbar (%s); rendere ohne Logo.", exc
        )
        return None


def _place_logo(pdf, logo_bytes):
    """Bettet das Firmenlogo oben rechts im Kopf ein (Seitenverhältnis gewahrt),
    ohne den Textcursor zu verschieben — der Aussteller-Text rendert unverändert
    links daneben. Lässt sich das Bild nicht einbetten (kaputte/unlesbare Bytes),
    wird es übersprungen: das PDF darf nie wegen des Logos scheitern.
    """
    x0, y0 = pdf.get_x(), pdf.get_y()
    x = pdf.w - pdf.r_margin - _LOGO_BOX_W_MM
    try:
        pdf.image(
            BytesIO(logo_bytes), x=x, y=y0,
            w=_LOGO_BOX_W_MM, h=_LOGO_BOX_H_MM, keep_aspect_ratio=True,
        )
    except Exception as exc:  # noqa: BLE001 - Logo darf das PDF nie scheitern lassen
        log.warning(
            "Beleg-PDF: Firmenlogo nicht einbettbar (%s); rendere ohne Logo.", exc
        )
    finally:
        # Cursor exakt zurücksetzen: der nachfolgende Text-Kopf bleibt bit-genau.
        pdf.set_xy(x0, y0)


def _render_issuer(pdf, issuer):
    """Kopfzeile des Ausstellers aus dem Stammdaten-Snapshot (oder Fallback).

    Ist ein Firmenlogo gepflegt und abrufbar, wird es oben rechts eingebettet;
    fehlt es oder ist der Objektspeicher weg, bleibt der Text-Kopf unverändert
    (graceful degradation). Das Logo bleibt bewusst LIVE (es ist Dekoration, kein
    belegrelevanter Inhalt, und wird als Dateiverweis geführt).
    """
    logo = _logo_bytes(CompanyProfile.objects.first())
    if logo is not None:
        _place_logo(pdf, logo)
    name, subline = _issuer_lines(issuer)
    pdf.set_font(FONT_FAMILY, "B", 14)
    pdf.cell(0, 8, _txt(name), new_x="LMARGIN", new_y="NEXT")
    if subline:
        pdf.set_font(FONT_FAMILY, "", 9)
        pdf.set_text_color(110, 110, 110)
        pdf.cell(0, 5, _txt(subline), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(6)


def _render_footer(pdf, issuer):
    """Fußzeile mit Steuer-/Register-/Bankangaben (nur was gepflegt ist)."""
    parts = _footer_parts(issuer)
    if not parts:
        return
    pdf.ln(6)
    pdf.set_draw_color(210, 210, 210)
    pdf.set_font(FONT_FAMILY, "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4, _txt(" · ".join(parts)), border="T")
    pdf.set_text_color(0, 0, 0)


def _render_lines(pdf, lines):
    """Positionstabelle (Kopf + Zeilen). Gemeinsam für Rechnung und Angebot —
    Angebots- und Rechnungsposition tragen dieselben Felder (position_number,
    line_type, quantity, unit, unit_price, net_amount, description).

    Menge und Einzelpreis kommen aus `beleg.anzeige_menge_preis` — derselben
    Funktion, die auch das ZUGFeRD-XML nutzt. Bei Kreditbelegen (Gutschrift/
    Storno) liegt das Vorzeichen dadurch in BEIDEN Darstellungen auf der Menge
    („−100 × 2,40 EUR"). Ohne diese gemeinsame Quelle zeigte das Sichtbild
    „100 × −2,40" und das eingebettete XML „−100 × 2,40" — dasselbe Ergebnis,
    aber ein in sich widersprüchliches Hybrid-Dokument."""
    widths = (12, 82, 20, 16, 28, 32)  # Summe 190 mm (usable width)
    headers = ("Pos", "Beschreibung", "Menge", "Einheit", "Einzelpreis", "Betrag")
    pdf.set_font(FONT_FAMILY, "B", 9)
    pdf.set_fill_color(238, 238, 238)
    for w, h in zip(widths, headers):
        align = "R" if h in ("Menge", "Einzelpreis", "Betrag") else "L"
        pdf.cell(w, 7, h, border="B", align=align, fill=True)
    pdf.ln(7)

    pdf.set_font(FONT_FAMILY, "", 9)
    for line in sorted(lines, key=lambda x: x.position_number):
        is_text = line.line_type in ("TEXT", "ZWISCHENSUMME")
        anz_menge, anz_preis = anzeige_menge_preis(line)
        menge = "" if is_text or anz_menge is None else _num(anz_menge)
        einheit = "" if is_text else _txt(line.unit or "")
        ep = "" if is_text or anz_preis is None else _eur(anz_preis)
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


def _render_totals(pdf, net_total, tax_total, gross_total):
    """Summenblock (Netto/USt/Gesamt) — gemeinsam für Rechnung und Angebot."""
    pdf.ln(3)
    pdf.set_draw_color(180, 180, 180)
    label_w, val_w = 150, 40
    for label, value, bold in (
        ("Nettobetrag", net_total, False),
        ("Umsatzsteuer", tax_total, False),
        ("Gesamtbetrag", gross_total, True),
    ):
        pdf.set_font(FONT_FAMILY, "B" if bold else "", 10 if bold else 9)
        pdf.cell(label_w, 7, label, align="R")
        pdf.cell(val_w, 7, _eur(value), align="R",
                 border="T" if bold else 0, new_x="LMARGIN", new_y="NEXT")


def _render_anrechnung(pdf, invoice):
    """Anrechnungsspiegel der Schlussrechnung (Leistung − Abschläge = Zahlbetrag).

    Die Anrechnung steht bereits als negative Position in der Tabelle — der
    Summenblock zeigt deshalb schon den Zahlbetrag. Dieser Block macht die
    Rechnung dennoch **explizit**, wie es die Praxis (und § 14 Abs. 5 UStG)
    verlangt: volle Leistung, jede angerechnete Abschlagsrechnung mit **Nummer und
    Datum**, verbleibender Zahlbetrag.

    Rechnet nicht selbst: die Zahlen kommen aus `beleg.leistungssummen` — derselben
    Quelle, die auch API und E-Rechnung nutzen.
    """
    if invoice.invoice_type != FINAL_TYPE:
        return
    spiegel = leistungssummen(invoice)
    if not spiegel:
        return

    pdf.ln(4)
    pdf.set_font(FONT_FAMILY, "B", 10)
    pdf.cell(0, 6, "Anrechnung der Abschlagsrechnungen", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT_FAMILY, "", 9)
    label_w, val_w = 150, 40

    def zeile(label, betrag, *, bold=False, border=0):
        pdf.set_font(FONT_FAMILY, "B" if bold else "", 10 if bold else 9)
        pdf.multi_cell(label_w, 6, _txt(label), align="R", new_x="RIGHT", new_y="TOP",
                       max_line_height=6)
        pdf.cell(val_w, 6, _eur(betrag), align="R", border=border,
                 new_x="LMARGIN", new_y="NEXT")

    zeile("Gesamtleistung (netto)", spiegel["leistung_net"])
    zeile("Umsatzsteuer auf die Gesamtleistung", spiegel["leistung_tax"])
    zeile("Gesamtleistung (brutto)", spiegel["leistung_gross"])
    for posten in spiegel["posten"]:
        titel = (
            "Abschlagsrechnung"
            if posten["invoice_type"] == "ABSCHLAGSRECHNUNG"
            else "Teilrechnung"
        )
        zeile(
            f"abzüglich {titel} {posten['invoice_number']} "
            f"vom {_de_date(posten['invoice_date'])} (brutto)",
            -posten["gross_amount"],
        )
    zeile("Verbleibender Zahlbetrag", spiegel["zahlbetrag"], bold=True, border="T")


def load_invoice_for_render(invoice_id):
    """Lädt eine Rechnung mit allem, was Layout und ZUGFeRD-XML brauchen.

    None, wenn sie nicht existiert oder nicht veröffentlicht ist — nur
    festgeschriebene Belege erhalten eine Ausfertigung.
    """
    invoice = (
        Invoice.objects.filter(id=invoice_id)
        .select_related("property__address")
        .prefetch_related("lines", "parties__party")
        .first()
    )
    if invoice is None or invoice.status != "VEROEFFENTLICHT":
        return None
    return invoice


def render_invoice_document(invoice, *, compliance=None):
    """Rendert das Beleg-PDF einer veröffentlichten Rechnung (Bytes).

    Gemeinsame Layout-Quelle für die normale Ausfertigung und die
    ZUGFeRD-Ausfertigung (`compliance="PDF/A-3B"`, services/erechnung.py) — das
    Sichtbild ist in beiden Fällen dasselbe, nur der PDF-Standard unterscheidet
    sich. Stammdaten (Aussteller/Empfänger) kommen aus dem eingefrorenen
    Snapshot (Live-Fallback nur für Altbelege, siehe beleg.beleg_stammdaten).
    """
    title = _TYPE_TITLES.get(invoice.invoice_type, invoice.invoice_type)
    stamm = beleg_stammdaten(invoice)
    debtor = beteiligter(stamm, "INVOICE_DEBTOR")
    recipient = beteiligter(stamm, "INVOICE_RECIPIENT") or debtor
    debtor_name = (debtor or {}).get("snapshot", {}).get("display_name")
    recipient_snapshot = (recipient or {}).get("snapshot")

    pdf = new_beleg_pdf(compliance=compliance)
    _render_issuer(pdf, stamm["issuer"])

    # Empfänger (Name + Anschrift, soweit gepflegt)
    pdf.set_font(FONT_FAMILY, "", 11)
    zeilen = empfaenger_zeilen(recipient_snapshot) or ["-"]
    for zeile in zeilen:
        pdf.cell(0, 6, _txt(zeile), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Belegkopf
    pdf.set_font(FONT_FAMILY, "B", 16)
    pdf.cell(0, 9, f"{title} {invoice.invoice_number or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT_FAMILY, "", 10)
    pdf.cell(0, 6, _txt(f"Belegdatum: {_de_date(invoice.invoice_date)}"
                        f"    Fällig: {_de_date(invoice.due_date)}"),
             new_x="LMARGIN", new_y="NEXT")
    zb = zahlungsbedingungen_text(invoice)
    if zb:
        pdf.set_font(FONT_FAMILY, "", 9)
        pdf.multi_cell(0, 5, _txt(zb), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(FONT_FAMILY, "", 10)
    if debtor_name and debtor is not recipient:
        pdf.cell(0, 6, _txt(f"Rechnungsschuldner: {debtor_name}"),
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Positionstabelle + Summen (gemeinsame Bausteine)
    _render_lines(pdf, invoice.lines.all())
    _render_totals(pdf, invoice.net_total, invoice.tax_total, invoice.gross_total)
    _render_anrechnung(pdf, invoice)

    if invoice.invoice_type in ("GUTSCHRIFT", "STORNO") and invoice.reference_invoice_id:
        ref = Invoice.objects.filter(id=invoice.reference_invoice_id).first()
        if ref:
            pdf.ln(4)
            pdf.set_font(FONT_FAMILY, "", 9)
            pdf.set_text_color(110, 110, 110)
            pdf.multi_cell(0, 5, _txt(f"Bezieht sich auf Ursprungsbeleg "
                                      f"{ref.invoice_number or ref.invoice_type}."))
            pdf.set_text_color(0, 0, 0)

    _render_footer(pdf, stamm["issuer"])

    out = pdf.output()
    return bytes(out)


def render_invoice_pdf(invoice_id):
    """Rendert das PDF einer veröffentlichten Rechnung und gibt die Bytes zurück.

    Gibt None zurück, wenn die Rechnung nicht existiert oder nicht veröffentlicht
    ist (nur festgeschriebene Belege erhalten eine Ausfertigung).
    """
    invoice = load_invoice_for_render(invoice_id)
    if invoice is None:
        return None
    return render_invoice_document(invoice)


def _num(value):
    q = Decimal(value)
    s = f"{q.normalize():f}" if q == q.to_integral() else f"{q}"
    return s.replace(".", ",")


# --- Angebot (invoicing.quote) ---------------------------------------------
# Ein Angebot hat KEINE eigenen Beteiligten. Der Empfänger wird — falls möglich —
# über den optionalen Auftrag abgeleitet; ohne Auftrag/Adresse rendert das
# Angebot trotzdem (ein Angebot muss keinen formalen Adressaten haben).

def quote_recipient_party(quote):
    """Abgeleitete Empfängerpartei eines Angebots (best-effort) oder None.

    Über den (optionalen) Auftrag: primärer INVOICE_RECIPIENT, ersatzweise
    PRINCIPAL. Erwartet ein Quote mit vorgeladenem ``work_order__parties__party``.
    """
    if quote.work_order_id is None:
        return None
    for role in _QUOTE_RECIPIENT_ROLES:
        chosen = None
        for p in quote.work_order.parties.all():
            if p.role == role:
                chosen = p
                if p.is_primary:
                    break
        if chosen is not None:
            return chosen.party
    return None


def render_quote_pdf(quote_id):
    """Rendert das PDF eines versendeten Angebots und gibt die Bytes zurück.

    Gibt None zurück, wenn das Angebot nicht existiert oder noch nicht versendet
    ist (ein Entwurf erhält keine finale Ausfertigung — siehe _QUOTE_PDF_STATUSES).
    """
    quote = (
        Quote.objects.filter(id=quote_id)
        .select_related("property__address", "work_order")
        .prefetch_related("lines", "work_order__parties__party")
        .first()
    )
    if quote is None or quote.status not in _QUOTE_PDF_STATUSES:
        return None

    recipient_party = quote_recipient_party(quote)
    recipient = recipient_party.display_name if recipient_party else None

    pdf = new_beleg_pdf()

    # Ein Angebot friert keine Stammdaten ein (es ist kein GoBD-Beleg im Sinne
    # der Rechnung) — der Aussteller kommt hier bewusst live aus dem Firmenprofil.
    profile = issuer_stammdaten()
    _render_issuer(pdf, profile)

    # Empfänger, falls ableitbar; sonst die Liegenschaft als Bezug (ein Angebot
    # darf ohne formalen Adressaten rendern).
    pdf.set_font(FONT_FAMILY, "", 11)
    if recipient:
        pdf.cell(0, 6, _txt(recipient), new_x="LMARGIN", new_y="NEXT")
    else:
        prop = quote.property
        ort = " · ".join(b for b in (prop.name, prop.address.city) if b)
        pdf.set_text_color(110, 110, 110)
        pdf.cell(0, 6, _txt(f"Liegenschaft: {ort}") if ort else "-",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # Belegkopf
    pdf.set_font(FONT_FAMILY, "B", 16)
    pdf.cell(0, 9, f"Angebot {quote.quote_number or ''}".strip(),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT_FAMILY, "", 12)
    pdf.set_text_color(70, 70, 70)
    pdf.cell(0, 7, _txt(quote.title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(FONT_FAMILY, "", 10)
    pdf.cell(0, 6, f"Angebotsdatum: {_de_date(quote.quote_date)}"
                   f"    Gültig bis: {_de_date(quote.valid_until_date)}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Positionstabelle + Summen (gemeinsame Bausteine)
    _render_lines(pdf, quote.lines.all())
    _render_totals(pdf, quote.net_total, quote.tax_total, quote.gross_total)

    _render_footer(pdf, profile)

    out = pdf.output()
    return bytes(out)


# --- GoBD-Archivierung (content.file + content.file_link) -------------------
# Kein ORM-Model auf content.* (das Schema existiert database-first, ein neues
# managed=False-Model verlangte eine State-only-Migration). Registrierung daher
# als schlankes Hand-SQL innerhalb der business_transaction — dieselben Tore
# (Trigger, Constraints, Audit-Kontext) wie jeder andere fachliche Write.

def archived_key_for(link_column, ident, category=_BELEG_PDF_CATEGORY):
    """storage_key der bereits archivierten Ausfertigung dieser Kategorie, sonst None.

    Reine Leseabfrage (Autocommit). Ein partieller UNIQUE-Index je Kategorie
    (Migration 0032 für BELEG_PDF, 0059 für E_RECHNUNG) garantiert höchstens eine
    solche Zeile je Beleg. `link_column` ist ein kontrolliertes internes Literal
    ('invoice_id'|'quote_id'), keine Nutzereingabe.
    """
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT f.storage_key
              FROM content.file_link fl
              JOIN content.file f ON f.id = fl.file_id
             WHERE fl.{link_column} = %s AND fl.link_category = %s
             LIMIT 1
            """,
            [str(ident), category],
        )
        row = cur.fetchone()
    return row[0] if row else None


def _archived_storage_key(invoice_id):
    """storage_key der archivierten Rechnungs-Ausfertigung (Migration 0032)."""
    return archived_key_for("invoice_id", invoice_id)


def _archived_quote_storage_key(quote_id):
    """storage_key der archivierten Angebots-Ausfertigung (Migration 0032)."""
    return archived_key_for("quote_id", quote_id)


def insert_file_and_link(actor_app_user_id, link_column, ident, *, storage_key,
                         original_filename, sha256, size_bytes,
                         category=_BELEG_PDF_CATEGORY, mime_type="application/pdf"):
    """Registriert content.file + content.file_link in EINER Transaktion.

    Wirft IntegrityError, wenn parallel bereits eine Ausfertigung derselben
    Kategorie verlinkt wurde (partieller UNIQUE-Index) — der Aufrufer behandelt
    den Wettlauf mit Nachselektion. Bei diesem Fehler rollt die atomic-Transaktion
    beide Inserts zurück (kein verwaister content.file-Steckbrief). `link_column`
    ist ein kontrolliertes internes Literal, keine Nutzereingabe.
    """
    with business_transaction(actor_app_user_id):
        with connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO content.file
                    (id, storage_key, original_filename, mime_type,
                     size_bytes, sha256, uploaded_by)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                [storage_key, original_filename, mime_type, size_bytes, sha256,
                 str(actor_app_user_id)],
            )
            file_id = cur.fetchone()[0]
            cur.execute(
                f"""
                INSERT INTO content.file_link
                    (id, file_id, {link_column}, link_category, created_by)
                VALUES (gen_random_uuid(), %s, %s, %s, %s)
                """,
                [str(file_id), str(ident), category, str(actor_app_user_id)],
            )
    return file_id


def _register_beleg_file(actor_app_user_id, invoice_id, *, storage_key,
                         original_filename, sha256, size_bytes):
    """Registriert die Rechnungs-Ausfertigung (content.file + file_link)."""
    return insert_file_and_link(
        actor_app_user_id, "invoice_id", invoice_id, storage_key=storage_key,
        original_filename=original_filename, sha256=sha256, size_bytes=size_bytes,
    )


def _register_quote_file(actor_app_user_id, quote_id, *, storage_key,
                         original_filename, sha256, size_bytes):
    """Registriert die Angebots-Ausfertigung (content.file + file_link)."""
    return insert_file_and_link(
        actor_app_user_id, "quote_id", quote_id, storage_key=storage_key,
        original_filename=original_filename, sha256=sha256, size_bytes=size_bytes,
    )


def _safe_number_filename(number, fallback_id):
    raw = number or str(fallback_id)
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    return f"{safe or 'beleg'}.pdf"


def _safe_filename(invoice):
    """Dateiname der Rechnungs-Ausfertigung (aus der Belegnummer)."""
    return _safe_number_filename(invoice.invoice_number, invoice.id)


def _safe_quote_filename(quote):
    """Dateiname der Angebots-Ausfertigung (aus der Angebotsnummer)."""
    return _safe_number_filename(quote.quote_number, quote.id)


def _invoice_filename(invoice_id):
    inv = Invoice.objects.filter(id=invoice_id).only("id", "invoice_number").first()
    return _safe_filename(inv) if inv else "beleg.pdf"


def _quote_filename(quote_id):
    q = Quote.objects.filter(id=quote_id).only("id", "quote_number").first()
    return _safe_quote_filename(q) if q else "angebot.pdf"


def get_or_archive_pdf(actor_app_user_id, ident, *, storage_prefix, render_fn,
                       key_lookup, register_fn, filename_fn):
    """Gemeinsamer Ablauf für die GoBD-Archivierung von Beleg-PDFs.

    Ablauf (Rechnung wie Angebot):
      1. Ist bereits eine BELEG_PDF-Ausfertigung archiviert → deren Bytes aus dem
         Objektspeicher ausliefern (nicht neu rendern; GoBD: eine Ausfertigung).
      2. Sonst on-the-fly rendern, in MinIO ablegen und als content.file +
         content.file_link registrieren.
      3. Wettlauf (Finding P-1): Verliert der zweite Erstabruf den UNIQUE-Index
         (IntegrityError), wird die vom Gewinner archivierte Datei nachselektiert
         und ausgeliefert — kein 500.

    Gibt None zurück, wenn der Beleg nicht existiert oder (noch) keine Ausfertigung
    erhält (render_fn liefert None → 404 in der API). Bei nicht erreichbarem
    Objektspeicher degradiert die Funktion bewusst: sie liefert die on-the-fly
    gerenderten Bytes und überspringt die Archivierung mit einer Log-Warnung.

    `key_lookup`/`register_fn` werden als Callables übergeben, damit der Aufrufer
    die belegtypspezifische Spalte (invoice_id|quote_id) bestimmt.
    """
    # 1) Bereits archiviert? Dann exakt diese Datei ausliefern.
    existing_key = key_lookup(ident)
    if existing_key is not None:
        served = serve_archived(existing_key)
        if served is not None:
            return served
        # Objektspeicher gerade nicht erreichbar: Steckbrief existiert, Objekt
        # (noch) nicht abrufbar → on-the-fly ausliefern (Degradation), nicht neu
        # archivieren (der Link ist bereits vergeben).
        pdf = render_fn(ident)
        if pdf is not None:
            log.warning(
                "Beleg-PDF %s ist archiviert, der Objektspeicher aber nicht "
                "erreichbar; liefere on-the-fly aus.", ident,
            )
        return pdf

    # 2) Rendern (None ⇒ keine Ausfertigung/unbekannt ⇒ 404 in der API).
    pdf = render_fn(ident)
    if pdf is None:
        return None

    # 3) Archivieren. Jeder Speicher-/DB-Fehler degradiert auf on-the-fly.
    try:
        storage = storage_module.get_storage()
        # Pro Versuch ein eigener, kollisionsfreier storage_key (uuid): so haben
        # zwei parallele Erstabrufe garantiert VERSCHIEDENE Objekte — der einzige
        # Wettlauf-Punkt bleibt der partielle UNIQUE-Index auf file_link, und der
        # Verlierer räumt beim Cleanup nur SEIN eigenes Objekt ab, nie das des
        # Gewinners. Der sha256 der Bytes wird davon unabhängig als
        # content.file.sha256 registriert (put_object berechnet ihn aus den Bytes).
        storage_key = f"{storage_prefix}/{ident}/{uuid.uuid4()}.pdf"
        info = storage.put_object(storage_key, pdf, content_type="application/pdf")
    except storage_module.StorageError as exc:
        log.warning(
            "Beleg-PDF %s: Objektspeicher nicht verfügbar (%s); liefere "
            "on-the-fly aus, Archivierung wird beim nächsten Abruf nachgeholt.",
            ident, exc,
        )
        return pdf

    try:
        register_fn(
            actor_app_user_id, ident,
            storage_key=info.storage_key,
            original_filename=filename_fn(ident),
            sha256=info.sha256,
            size_bytes=info.size_bytes,
        )
    except IntegrityError:
        # Wettlauf verloren: ein paralleler Erstabruf hat die Ausfertigung schon
        # verlinkt. Eigenes (verwaistes) Objekt best-effort entfernen, die vom
        # Gewinner archivierte Datei nachselektieren und ausliefern.
        best_effort_remove(storage, info.storage_key)
        winner_key = key_lookup(ident)
        if winner_key is not None:
            served = serve_archived(winner_key)
            if served is not None:
                return served
        log.warning(
            "Beleg-PDF %s: Wettlauf verloren, Gewinner-Objekt nicht abrufbar; "
            "liefere on-the-fly aus.", ident,
        )
        return pdf

    # Frisch archiviert: die soeben abgelegten Bytes ausliefern (== im Speicher).
    return pdf


def get_or_archive_invoice_pdf(actor_app_user_id, invoice_id):
    """Liefert die (archivierte) PDF-Ausfertigung einer veröffentlichten Rechnung.

    Gibt None zurück, wenn die Rechnung nicht existiert oder nicht veröffentlicht
    ist (Endpunkt → 404). Siehe get_or_archive_pdf für den Ablauf (Archivierung
    beim Erstabruf, Wettlauf-Nachselektion, Degradation ohne Objektspeicher).
    """
    return get_or_archive_pdf(
        actor_app_user_id, invoice_id,
        storage_prefix="belege/rechnung",
        render_fn=render_invoice_pdf,
        key_lookup=_archived_storage_key,
        register_fn=_register_beleg_file,
        filename_fn=_invoice_filename,
    )


def get_or_archive_quote_pdf(actor_app_user_id, quote_id):
    """Liefert die (archivierte) PDF-Ausfertigung eines versendeten Angebots.

    Gibt None zurück, wenn das Angebot nicht existiert oder noch nicht versendet
    ist (Endpunkt → 404). Ablauf identisch zur Rechnung (siehe
    get_or_archive_pdf); der partielle UNIQUE-Index auf file_link.quote_id
    (Migration 0032) sichert die eine Ausfertigung je Angebot.
    """
    return get_or_archive_pdf(
        actor_app_user_id, quote_id,
        storage_prefix="belege/angebot",
        render_fn=render_quote_pdf,
        key_lookup=_archived_quote_storage_key,
        register_fn=_register_quote_file,
        filename_fn=_quote_filename,
    )


def serve_archived(storage_key):
    """Lädt die archivierten Bytes; None, wenn der Objektspeicher gerade fehlt."""
    try:
        return storage_module.get_storage().get_object(storage_key)
    except storage_module.StorageError as exc:
        log.warning(
            "Beleg-PDF: archiviertes Objekt %s nicht abrufbar (%s).",
            storage_key, exc,
        )
        return None


def best_effort_remove(storage, storage_key):
    try:
        storage.remove_object(storage_key)
    except storage_module.StorageError as exc:  # pragma: no cover - Best-Effort
        log.warning(
            "Beleg-PDF: verwaistes Objekt %s konnte nicht entfernt werden (%s).",
            storage_key, exc,
        )
