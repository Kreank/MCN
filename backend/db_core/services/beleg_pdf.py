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

Layout (seit dem Marken-Redesign 2026-07): DIN 5008 Form B (Anschriftfeld für
Fensterkuverts, Falz-/Lochmarken), Mitra-Markenfarben aus den Frontend-Tokens
(frontend/src/styles/_tokens.scss), Wortmarke im Kopf, blasses Tropfen-Emblem
als Wasserzeichen (Blässe im PNG gebacken — keine PDF-Transparenz-Gruppen),
Giro-Code (EPC-QR) im Zahlungsblock. Vorschau unveröffentlichter Belege über
``render_invoice_preview``/``render_quote_preview`` mit ENTWURF-Aufdruck, ohne
Archivierung.

Schrift: **eingebettete TrueType-Schrift** (Inter, SIL OFL, unter
``db_core/assets/fonts/``) statt des fpdf2-Kernfonts Helvetica. Zwei Gründe:
1. PDF/A-3B (E-Rechnung, services/erechnung.py) verlangt zwingend eingebettete
   Schriften — ein Kernfont ist dort verboten. Beide Ausfertigungen teilen sich
   dasselbe Layout, also gilt die Schrift für beide.
2. Der Kernfont kann nur Latin-1; Umlaute mussten ersetzt werden, das €-Zeichen
   war gar nicht darstellbar. Mit eingebetteter TTF ist Unicode unverfälscht.

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

import segno
from django.db import IntegrityError, connection
from fpdf import FPDF

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import CompanyProfile, File, Invoice, Quote
from db_core.services.beleg import (
    FINAL_TYPE,
    anzeige_menge_preis,
    arbeitskosten,
    beleg_stammdaten,
    beteiligter,
    issuer_stammdaten,
    leistungssummen,
    party_stammdaten,
    zahlungsbedingungen,
)

log = logging.getLogger(__name__)

_BELEG_PDF_CATEGORY = "BELEG_PDF"

# Eingebettete Schrift (SIL OFL; LICENSE-Inter.txt liegt daneben). Vier
# Schnitte: Regular/Bold als Familie "Inter", Medium/SemiBold als eigene
# Familien (fpdf2 kennt je Familie nur ""/"B"/"I"-Stile).
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_FAMILY = "Inter"
_FONT_FILES = {
    ("Inter", ""): "Inter-Regular.ttf",
    ("Inter", "B"): "Inter-Bold.ttf",
    ("InterM", ""): "Inter-Medium.ttf",
    ("InterSB", ""): "Inter-SemiBold.ttf",
}

# Markenzeichen (Repo-SVGs, als PNG gerendert). Das Ghost-Emblem trägt seine
# Blässe im Bild selbst — keine PDF-Transparenz-Gruppen nötig (PDF/A-freundlich).
BRAND_DIR = Path(__file__).resolve().parent.parent / "assets" / "branding"
_BRAND_LOGO = BRAND_DIR / "mitra_logo.png"
_BRAND_GHOST = BRAND_DIR / "mitra_emblem_ghost.png"

# Markenfarben — verbindliche Tokens aus frontend/src/styles/_tokens.scss.
# Orange in Reinform nur auf Navy bzw. als grafischer Akzent (CI-Regel).
_NAVY = (28, 50, 68)        # #1c3244
_ORANGE = (239, 128, 78)    # #ef804e
_PAPER = (247, 246, 243)    # #f7f6f3
_HAIR = (221, 216, 206)     # #ddd8ce
_INK = (28, 50, 68)
_MUTED = (74, 91, 104)      # #4a5b68
_HINT = (92, 107, 120)      # #5c6b78

# DIN-5008-Grundraster (Form B): links 25 mm, rechts 20 mm; Falzmarken bei
# 105/210 mm, Lochmarke bei 148,5 mm; Anschriftfeld ab 45 mm.
_M_L, _M_R, _M_T = 25, 20, 16
_PAGE_W = 210
_USABLE = _PAGE_W - _M_L - _M_R  # 165 mm
_FOOT_H = 34                     # reservierter Fußbereich je Seite


class _BelegPDF(FPDF):
    """A4-Beleg mit Marken-Kopf, Wasserzeichen und Fußzeile auf jeder Seite.

    Kopf/Fuß rendern aus `issuer`-Snapshot-Daten (dict) — bei None greift der
    Fallback ohne Absturz. `entwurf=True` legt zusätzlich einen deutlichen
    ENTWURF-Aufdruck unter den Inhalt (Vorschau unveröffentlichter Belege).
    """

    def __init__(self, *, compliance=None, issuer=None, entwurf=False,
                 logo_bytes=None):
        super().__init__(format="A4", unit="mm", enforce_compliance=compliance)
        self.issuer = issuer
        self.entwurf = entwurf
        self.logo_bytes = logo_bytes
        for (fam, style), datei in _FONT_FILES.items():
            self.add_font(fam, style, str(FONT_DIR / datei))
        self.set_margins(_M_L, _M_T, _M_R)
        self.set_auto_page_break(auto=True, margin=_FOOT_H)
        self.alias_nb_pages()

    def header(self):
        # Wasserzeichen zuerst — alles Weitere liegt darüber.
        if _BRAND_GHOST.exists():
            self.image(str(_BRAND_GHOST), x=60, y=120, w=100)
        if self.entwurf:
            self.set_font("Inter", "B", 58)
            self.set_text_color(214, 220, 226)
            with self.rotation(40, x=105, y=160):
                self.text(x=48, y=172, text="ENTWURF")

        # Falz- und Lochmarken (DIN 5008)
        self.set_draw_color(150, 150, 150)
        self.set_line_width(0.2)
        self.line(4, 105, 8, 105)
        self.line(4, 210, 8, 210)
        self.line(3, 148.5, 9, 148.5)

        # Logo rechts: Firmenprofil-Logo, sonst die eingebaute Mitra-Wortmarke.
        lw, lh = 40, 40 * 449.08 / 1065.59
        lx = _PAGE_W - _M_R - lw
        if self.logo_bytes is not None:
            try:
                self.image(BytesIO(self.logo_bytes), x=lx, y=_M_T - 1,
                           w=lw, h=lh, keep_aspect_ratio=True)
            except Exception as exc:  # noqa: BLE001 - Logo darf nie scheitern
                log.warning("Beleg-PDF: Logo nicht einbettbar (%s).", exc)
        elif _BRAND_LOGO.exists():
            self.image(str(_BRAND_LOGO), x=lx, y=_M_T - 1, w=lw)

        # Aussteller links
        name, subline = _issuer_lines(self.issuer)
        self.set_xy(_M_L, _M_T)
        self.set_font("InterSB", "", 13.5)
        self.set_text_color(*_NAVY)
        self.cell(0, 7, _txt(name), new_x="LMARGIN", new_y="NEXT")
        if subline:
            self.set_font("Inter", "", 8.5)
            self.set_text_color(*_HINT)
            self.cell(0, 4.5, _txt(subline), new_x="LMARGIN", new_y="NEXT")

        # Akzentlinie: kurzer Orange-Auftakt, dann Navy
        y = _M_T + lh + 1.5
        self.set_draw_color(*_ORANGE)
        self.set_line_width(0.9)
        self.line(_M_L, y, _M_L + 18, y)
        self.set_draw_color(*_NAVY)
        self.set_line_width(0.5)
        self.line(_M_L + 18, y, _PAGE_W - _M_R, y)
        self.set_text_color(*_INK)
        self.set_y(y + 5)

    def footer(self):
        self.set_y(-(_FOOT_H - 6))
        self.set_draw_color(*_NAVY)
        self.set_line_width(0.4)
        self.line(_M_L, self.get_y(), _PAGE_W - _M_R, self.get_y())
        self.ln(2.5)
        self.set_font("Inter", "", 7)
        self.set_text_color(*_HINT)
        spalten = _footer_spalten(self.issuer)
        y0 = self.get_y()
        col_w = _USABLE / 3
        for i, block in enumerate(spalten[:3]):
            for j, zeile in enumerate(block[:4]):
                self.set_xy(_M_L + i * col_w, y0 + j * 3.4)
                self.cell(col_w, 3.4, _txt(zeile))
        self.set_xy(_M_L, y0 + 15.5)
        self.set_font("InterM", "", 7.5)
        self.cell(_USABLE, 4, f"Seite {self.page_no()} von {{nb}}", align="R")


def _footer_spalten(issuer):
    """Dreispaltige Fußzeile (Kontakt · Steuer/Register · Bank) — nur Gepflegtes."""
    if not issuer:
        return [[_FALLBACK_NAME, _FALLBACK_SUBLINE]]
    kontakt = [issuer.get("company_name")]
    strasse = issuer.get("street")
    ort = " ".join(b for b in (issuer.get("postal_code"), issuer.get("city")) if b)
    if strasse or ort:
        kontakt.append(" · ".join(b for b in (strasse, ort) if b))
    if issuer.get("phone"):
        kontakt.append(issuer["phone"])
    if issuer.get("email"):
        kontakt.append(issuer["email"])
    steuer = []
    if issuer.get("tax_number"):
        steuer.append(f"Steuernr. {issuer['tax_number']}")
    if issuer.get("vat_id"):
        steuer.append(f"USt-IdNr. {issuer['vat_id']}")
    if issuer.get("commercial_register"):
        steuer.append(issuer["commercial_register"])
    if issuer.get("managing_director"):
        title = issuer.get("managing_director_title") or "Geschäftsführung"
        steuer.append(f"{title}: {issuer['managing_director']}")
    bank = []
    if issuer.get("bank_name"):
        bank.append(issuer["bank_name"])
    if issuer.get("iban"):
        bank.append(f"IBAN {issuer['iban']}")
    if issuer.get("bic"):
        bank.append(f"BIC {issuer['bic']}")
    return [[z for z in kontakt if z], steuer, bank]


def new_beleg_pdf(*, compliance=None, issuer=None, entwurf=False,
                  logo_bytes=None):
    """Ein leeres A4-Beleg-PDF im Markenlayout, erste Seite angelegt.

    `compliance` reicht fpdf2s `enforce_compliance` durch (z. B. "PDF/A-3B" für
    die E-Rechnung). fpdf2 setzt dann OutputIntent + XMP und verweigert
    nicht-eingebettete Schriften — die Inter-TTFs sind immer eingebettet.
    """
    pdf = _BelegPDF(compliance=compliance, issuer=issuer, entwurf=entwurf,
                    logo_bytes=logo_bytes)
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


def _zeilenzahl(pdf, breite, text, font, size):
    """Echte Zeilenzahl eines Textes nach Umbruch (dry_run), min. 1."""
    pdf.set_font(font, "", size)
    lines = pdf.multi_cell(breite, 4.6, _txt(text), align="L",
                           dry_run=True, output="LINES")
    return max(1, len(lines))


def _tabellenkopf(pdf):
    """Navy-Tabellenkopf der Positionstabelle (auch nach Seitenumbruch)."""
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("InterSB", "", 8.5)
    heads = ("Pos", "Beschreibung", "Menge", "Einheit", "Einzelpreis", "Betrag")
    for w, h in zip(_COLS, heads):
        align = "R" if h in ("Menge", "Einzelpreis", "Betrag") else "L"
        pdf.cell(w, 8, f" {h}" if align == "L" else f"{h} ",
                 align=align, fill=True)
    pdf.ln(8)
    pdf.set_text_color(*_INK)


_COLS = (10, 75, 16, 12, 26, 26)  # Summe 165 mm = _USABLE


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
    _tabellenkopf(pdf)
    for line in sorted(lines, key=lambda x: x.position_number):
        is_text = line.line_type in ("TEXT", "ZWISCHENSUMME")
        anz_menge, anz_preis = anzeige_menge_preis(line)
        menge = "" if is_text or anz_menge is None else _num(anz_menge)
        einheit = "" if is_text else _txt(line.unit or "")
        ep = "" if is_text or anz_preis is None else _eur(anz_preis)
        betrag = "" if is_text or line.net_amount is None else _eur(line.net_amount)

        # Beschreibung: erste Zeile betont, Folgezeilen als graues Detail.
        # Zeilenhöhen VOR dem Zeichnen bestimmen (echter Umbruch, dry_run),
        # damit der Seitenumbruch je Position als Ganzes fällt.
        teile = _txt(line.description).split("\n")
        haupt, detail = teile[0], "\n".join(teile[1:])
        desc_w = _COLS[1] - 2
        n_haupt = _zeilenzahl(pdf, desc_w, haupt, "InterM" if not is_text else "Inter", 9)
        n_detail = _zeilenzahl(pdf, desc_w, detail, "Inter", 8) if detail else 0
        h = 4.6 * n_haupt + 4.2 * n_detail + 3.6
        if pdf.get_y() + h > 297 - _FOOT_H:
            pdf.add_page()
            _tabellenkopf(pdf)

        y0 = pdf.get_y()
        pdf.set_font("Inter", "", 9)
        pdf.set_xy(_M_L, y0 + 1.8)
        pdf.cell(_COLS[0], 4.6, "" if is_text else f" {line.position_number}")
        if is_text:
            pdf.set_text_color(*_MUTED)
        pdf.set_font("InterM" if not is_text else "Inter", "", 9)
        pdf.set_xy(_M_L + _COLS[0], y0 + 1.8)
        pdf.multi_cell(desc_w, 4.6, haupt, align="L")
        if detail:
            pdf.set_font("Inter", "", 8)
            pdf.set_text_color(*_HINT)
            pdf.set_xy(_M_L + _COLS[0], y0 + 1.8 + 4.6 * n_haupt)
            pdf.multi_cell(desc_w, 4.2, detail, align="L")
        pdf.set_text_color(*_INK)
        pdf.set_font("Inter", "", 9)
        pdf.set_xy(_M_L + _COLS[0] + _COLS[1], y0 + 1.8)
        pdf.cell(_COLS[2], 4.6, menge, align="R")
        pdf.cell(_COLS[3], 4.6, f" {einheit}")
        pdf.cell(_COLS[4], 4.6, f"{ep} " if ep else "", align="R")
        pdf.set_font("InterM", "", 9)
        pdf.cell(_COLS[5], 4.6, f"{betrag} " if betrag else "", align="R")
        # feine Trennlinie statt Zebra — lässt das Wasserzeichen ungestört
        pdf.set_draw_color(*_HAIR)
        pdf.set_line_width(0.2)
        pdf.line(_M_L, y0 + h, _M_L + _USABLE, y0 + h)
        pdf.set_y(y0 + h)


def _platz_sichern(pdf, hoehe):
    """Seitenumbruch, wenn `hoehe` mm nicht mehr auf die Seite passen."""
    if pdf.get_y() + hoehe > 297 - _FOOT_H:
        pdf.add_page()


def _render_totals(pdf, net_total, tax_total, gross_total):
    """Summenblock (Netto/USt/Gesamt) — gemeinsam für Rechnung und Angebot."""
    _platz_sichern(pdf, 32)
    pdf.ln(3)
    sum_w, val_w = 70, 32
    sx = _PAGE_W - _M_R - sum_w
    for label, value in (("Nettobetrag", net_total),
                         ("Umsatzsteuer", tax_total)):
        pdf.set_xy(sx, pdf.get_y())
        pdf.set_font("Inter", "", 9)
        pdf.cell(sum_w - val_w, 6.4, label, align="R")
        pdf.cell(val_w, 6.4, f"{_eur(value)} ", align="R",
                 new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(sx, pdf.get_y() + 0.8)
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("InterSB", "", 10.5)
    pdf.cell(sum_w - val_w, 8.6, "Gesamtbetrag  ", align="R", fill=True)
    pdf.cell(val_w, 8.6, f"{_eur(gross_total)} ", align="R", fill=True,
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)


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

    _platz_sichern(pdf, 30 + 6 * len(spiegel["posten"]))
    pdf.ln(5)
    pdf.set_font("InterSB", "", 10)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, "Anrechnung der Abschlagsrechnungen", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.set_font(FONT_FAMILY, "", 9)
    label_w, val_w = 130, 35

    def zeile(label, betrag, *, bold=False, border=0):
        pdf.set_font("InterSB" if bold else "Inter", "", 10 if bold else 9)
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


def _render_arbeitskosten(pdf, invoice):
    """Ausweis der Arbeitskosten nach § 35a EStG (Lohn-, Maschinen-, Fahrtkosten).

    Ohne diesen Ausweis verliert ein Privatkunde 20 % der Arbeitskosten (max.
    1.200 EUR/Jahr) an Steuerermäßigung — der Materialanteil ist nicht begünstigt,
    und eine eigene Schätzung des Kunden erkennt das Finanzamt nicht an.

    Der Block bleibt aus, wenn
    - der Beleg ihn abgeschaltet hat (`show_labour_costs`, B2B),
    - auch nur EINE Position ihren Anteil nicht bestimmt (lieber kein Ausweis als
      ein falscher — das UI warnt vor dem Veröffentlichen), oder
    - keine Arbeitskosten enthalten sind (reine Materiallieferung).

    Rechnet nicht selbst: die Zahlen kommen aus `beleg.arbeitskosten` — derselben
    Quelle, die auch die API und der Editor nutzen.
    """
    if not invoice.show_labour_costs:
        return
    ausweis = arbeitskosten(invoice)
    if not ausweis["bestimmbar"] or not ausweis["gross_amount"]:
        return

    _platz_sichern(pdf, 42)
    pdf.ln(5)
    y0 = pdf.get_y()
    pdf.set_fill_color(*_NAVY)
    pdf.rect(_M_L, y0, 1.2, 24, style="F")
    pdf.set_xy(_M_L + 5, y0)
    pdf.set_font("InterSB", "", 10)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, _txt("Arbeitskosten nach § 35a EStG"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    label_w, val_w = 130, 35
    for label, betrag, bold in (
        ("in der Rechnung enthaltene Lohn-, Maschinen- und Fahrtkosten (netto)",
         ausweis["net_amount"], False),
        ("darauf entfallende Umsatzsteuer", ausweis["tax_amount"], False),
        ("Arbeitskosten (brutto)", ausweis["gross_amount"], True),
    ):
        pdf.set_font("InterSB" if bold else "Inter", "", 10 if bold else 9)
        pdf.multi_cell(label_w, 6, _txt(label), align="R", new_x="RIGHT", new_y="TOP",
                       max_line_height=6)
        pdf.cell(val_w, 6, _eur(betrag), align="R", border="T" if bold else 0,
                 new_x="LMARGIN", new_y="NEXT")

    pdf.set_font(FONT_FAMILY, "", 8)
    pdf.set_text_color(*_HINT)
    pdf.set_x(_M_L + 5)
    pdf.multi_cell(
        _USABLE - 8, 4,
        _txt("Für Handwerkerleistungen in einem Privathaushalt können 20 % der "
             "Arbeitskosten (höchstens 1.200 EUR im Jahr) von der Steuerschuld "
             "abgezogen werden (§ 35a Abs. 3 EStG). Voraussetzung ist die "
             "unbare Zahlung auf das oben genannte Konto; Materialkosten sind "
             "nicht begünstigt."),
        align="L", new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*_INK)


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


def _render_anschrift_und_infoblock(pdf, zeilen, meta):
    """DIN-5008-Anschriftfeld (Fensterkuvert) + Infoblock rechts daneben.

    `zeilen` ist der Empfänger-Anschriftblock, `meta` eine Liste
    (Label, Wert)-Paare für den rechten Infoblock. Setzt den Cursor
    anschließend auf die Titelposition (96 mm).
    """
    issuer = pdf.issuer
    pdf.set_y(45)
    if issuer:
        ort = " ".join(b for b in (issuer.get("postal_code"),
                                   issuer.get("city")) if b)
        ruecksende = " · ".join(
            b for b in (issuer.get("company_name"), issuer.get("street"), ort) if b
        )
        if ruecksende:
            pdf.set_font("Inter", "", 6.8)
            pdf.set_text_color(*_HINT)
            pdf.cell(85, 3.5, _txt(ruecksende), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)
    pdf.set_font("Inter", "", 10.5)
    pdf.set_text_color(*_INK)
    for zeile in zeilen:
        pdf.cell(85, 5.6, _txt(zeile), new_x="LMARGIN", new_y="NEXT")

    ix = 125
    pdf.set_y(47)
    for label, wert in meta:
        if not wert:
            continue
        pdf.set_x(ix)
        pdf.set_font("Inter", "", 7.5)
        pdf.set_text_color(*_HINT)
        pdf.cell(26, 4.8, _txt(label))
        pdf.set_font("InterM", "", 8.5)
        pdf.set_text_color(*_INK)
        pdf.cell(0, 4.8, _txt(wert), new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(96)


def _render_titel(pdf, titel, untertitel=None):
    """Belegtitel in Navy + optionale graue Unterzeile."""
    pdf.set_font("Inter", "B", 16)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 9, _txt(titel), new_x="LMARGIN", new_y="NEXT")
    if untertitel:
        pdf.set_font("Inter", "", 9.5)
        pdf.set_text_color(*_HINT)
        pdf.cell(0, 5.5, _txt(untertitel), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.ln(3)


def _epc_qr_png(issuer, betrag, zweck):
    """PNG-Bytes eines Giro-Codes (EPC-QR, Version 002) oder None.

    Nur wenn IBAN und Empfängername gepflegt sind und ein positiver Betrag
    gefordert wird — sonst kein QR (ein leerer/falscher Code wäre schlimmer
    als keiner).
    """
    if not issuer or not issuer.get("iban") or not issuer.get("company_name"):
        return None
    if betrag is None or Decimal(betrag) <= 0:
        return None
    data = "\n".join([
        "BCD", "002", "1", "SCT",
        _txt(issuer.get("bic")),
        _txt(issuer["company_name"])[:70],
        issuer["iban"].replace(" ", ""),
        f"EUR{Decimal(betrag).quantize(Decimal('0.01'))}",
        "", "",
        _txt(zweck)[:140],
    ])
    buf = BytesIO()
    segno.make(data, error="m").save(buf, kind="png", scale=8, border=0)
    return buf.getvalue()


def _render_zahlung(pdf, invoice, issuer, *, mit_qr=True):
    """Zahlungsblock: Zahlungsbedingungen + Giro-Code in einer Markenbox.

    Der Bedingungstext kommt unverändert aus `zahlungsbedingungen_text` —
    PDF und Bildschirm zeigen denselben Wortlaut. Ohne Bedingungen UND ohne
    QR entfällt der Block ersatzlos.
    """
    zb = zahlungsbedingungen_text(invoice)
    qr = None
    if mit_qr and invoice.invoice_type not in ("GUTSCHRIFT", "STORNO"):
        title = _TYPE_TITLES.get(invoice.invoice_type, invoice.invoice_type)
        zweck = f"{title} {invoice.invoice_number or ''}".strip()
        qr = _epc_qr_png(issuer, invoice.gross_total, zweck)
    if not zb and qr is None:
        return

    bank_bits = []
    if issuer and issuer.get("bank_name"):
        bank_bits.append(issuer["bank_name"])
    if issuer and issuer.get("iban"):
        bank_bits.append(f"IBAN {issuer['iban']}")
    if issuer and issuer.get("bic"):
        bank_bits.append(f"BIC {issuer['bic']}")
    zeilen = [z for z in (zb, " · ".join(bank_bits) or None) if z]

    box_h = 38 if qr else 10 + 5 * len(zeilen)
    _platz_sichern(pdf, box_h + 6)
    pdf.ln(5)
    y0 = pdf.get_y()
    pdf.set_fill_color(*_PAPER)
    pdf.set_draw_color(*_NAVY)
    pdf.set_line_width(0.3)
    pdf.rect(_M_L, y0, _USABLE, box_h, style="DF",
             round_corners=True, corner_radius=2.5)
    tx = _M_L + 5
    if qr is not None:
        pdf.image(BytesIO(qr), x=_M_L + 5, y=y0 + 5, w=28, h=28)
        tx = _M_L + 40
    pdf.set_xy(tx, y0 + 5)
    pdf.set_font("InterSB", "", 10)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 5.5, "Bequem bezahlen mit Giro-Code" if qr else "Zahlung",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.set_font("Inter", "", 8.8)
    pdf.set_xy(tx, y0 + 11.5)
    if qr:
        zeilen.insert(0, "QR-Code mit Ihrer Banking-App scannen — Empfänger, "
                         "Betrag und Verwendungszweck sind bereits ausgefüllt.")
    pdf.multi_cell(_USABLE - (tx - _M_L) - 5, 4.6, _txt("\n".join(zeilen)),
                   align="L")
    pdf.set_y(y0 + box_h)


def render_invoice_document(invoice, *, compliance=None, entwurf=False):
    """Rendert das Beleg-PDF einer Rechnung (Bytes).

    Gemeinsame Layout-Quelle für die normale Ausfertigung und die
    ZUGFeRD-Ausfertigung (`compliance="PDF/A-3B"`, services/erechnung.py) — das
    Sichtbild ist in beiden Fällen dasselbe, nur der PDF-Standard unterscheidet
    sich. Stammdaten (Aussteller/Empfänger) kommen aus dem eingefrorenen
    Snapshot (Live-Fallback nur für Altbelege, siehe beleg.beleg_stammdaten).

    `entwurf=True` (Vorschau unveröffentlichter Belege) legt einen deutlichen
    ENTWURF-Aufdruck unter den Inhalt und lässt den Giro-Code weg — ein
    Entwurf fordert keine Zahlung.
    """
    title = _TYPE_TITLES.get(invoice.invoice_type, invoice.invoice_type)
    stamm = beleg_stammdaten(invoice)
    debtor = beteiligter(stamm, "INVOICE_DEBTOR")
    recipient = beteiligter(stamm, "INVOICE_RECIPIENT") or debtor
    debtor_name = (debtor or {}).get("snapshot", {}).get("display_name")
    recipient_snapshot = (recipient or {}).get("snapshot")

    logo = _logo_bytes(CompanyProfile.objects.first())
    pdf = new_beleg_pdf(compliance=compliance, issuer=stamm["issuer"],
                        entwurf=entwurf, logo_bytes=logo)

    objekt = None
    prop = getattr(invoice, "property", None)
    if prop is not None:
        objekt = " · ".join(b for b in (
            prop.name, getattr(getattr(prop, "address", None), "city", None)
        ) if b)

    meta = [
        ("Beleg-Nr.", invoice.invoice_number or ("Entwurf" if entwurf else None)),
        ("Belegdatum", _de_date(invoice.invoice_date)),
        ("Fällig bis", _de_date(invoice.due_date)),
        ("Objekt", objekt),
    ]
    _render_anschrift_und_infoblock(
        pdf, empfaenger_zeilen(recipient_snapshot) or ["-"], meta
    )

    titel_text = f"{title} {invoice.invoice_number or ''}".strip()
    if entwurf:
        titel_text += " — Entwurf"
    _render_titel(pdf, titel_text, objekt and f"Objekt: {objekt}")

    if debtor_name and debtor is not recipient:
        pdf.set_font(FONT_FAMILY, "", 10)
        pdf.cell(0, 6, _txt(f"Rechnungsschuldner: {debtor_name}"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Positionstabelle + Summen (gemeinsame Bausteine)
    _render_lines(pdf, invoice.lines.all())
    _render_totals(pdf, invoice.net_total, invoice.tax_total, invoice.gross_total)
    _render_anrechnung(pdf, invoice)
    _render_zahlung(pdf, invoice, stamm["issuer"], mit_qr=not entwurf)
    _render_arbeitskosten(pdf, invoice)

    if invoice.invoice_type in ("GUTSCHRIFT", "STORNO") and invoice.reference_invoice_id:
        ref = Invoice.objects.filter(id=invoice.reference_invoice_id).first()
        if ref:
            pdf.ln(4)
            pdf.set_font(FONT_FAMILY, "", 9)
            pdf.set_text_color(*_HINT)
            pdf.multi_cell(0, 5, _txt(f"Bezieht sich auf Ursprungsbeleg "
                                      f"{ref.invoice_number or ref.invoice_type}."))
            pdf.set_text_color(*_INK)

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


def render_invoice_preview(invoice_id):
    """Vorschau-PDF einer Rechnung in JEDEM Status (Bytes) oder None.

    Unveröffentlichte Belege erhalten den ENTWURF-Aufdruck und keinen
    Giro-Code; eine bereits veröffentlichte Rechnung zeigt ihr normales
    Sichtbild. Es wird NICHTS archiviert — die GoBD-Ausfertigung entsteht
    weiterhin ausschließlich über get_or_archive_invoice_pdf.
    """
    invoice = (
        Invoice.objects.filter(id=invoice_id)
        .select_related("property__address")
        .prefetch_related("lines", "parties__party")
        .first()
    )
    if invoice is None:
        return None
    return render_invoice_document(
        invoice, entwurf=invoice.status != "VEROEFFENTLICHT"
    )


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


def _load_quote(quote_id):
    return (
        Quote.objects.filter(id=quote_id)
        .select_related("property__address", "work_order")
        .prefetch_related("lines", "work_order__parties__party")
        .first()
    )


def render_quote_document(quote, *, entwurf=False):
    """Rendert das Angebots-PDF (Bytes) im Markenlayout.

    Ein Angebot friert keine Stammdaten ein (es ist kein GoBD-Beleg im Sinne
    der Rechnung) — der Aussteller kommt bewusst live aus dem Firmenprofil.
    """
    recipient_party = quote_recipient_party(quote)
    profile = issuer_stammdaten()
    logo = _logo_bytes(CompanyProfile.objects.first())
    pdf = new_beleg_pdf(issuer=profile, entwurf=entwurf, logo_bytes=logo)

    prop = quote.property
    ort = " · ".join(b for b in (prop.name, prop.address.city) if b)

    # Empfänger mit **vollständiger Anschrift**, falls ableitbar; sonst die
    # Liegenschaft als Bezug (ein Angebot darf ohne formalen Adressaten rendern).
    #
    # Bis Juli 2026 stand hier nur `recipient_party.display_name` — eine nackte
    # Namenszeile. Das Anschriftfeld ist aber nach DIN 5008 Form B für ein
    # Fensterkuvert gebaut: Ohne Straße und Ort ließ sich das ausgedruckte
    # Angebot schlicht nicht eintüten. Jetzt dieselbe Funktion wie beim
    # Rechnungs-PDF und bei der Bildschirmansicht (`beleg.dokumentkopf`), damit
    # alle drei denselben Block zeigen.
    #
    # Sichtbild-Divergenz bei Altbelegen: Ein vor dieser Änderung archiviertes
    # Angebots-PDF behält seine Ausfertigung mit der Namenszeile. Beträge und
    # Positionen sind identisch — es gibt keinen Datenwiderspruch (vgl. den
    # gleichgelagerten Hinweis zur Font-Umstellung im Modul-Docstring).
    empfaenger = empfaenger_zeilen(party_stammdaten(recipient_party)) if recipient_party else []
    zeilen = empfaenger or ([f"Liegenschaft: {ort}"] if ort else ["-"])
    meta = [
        ("Angebots-Nr.", quote.quote_number or ("Entwurf" if entwurf else None)),
        ("Angebotsdatum", _de_date(quote.quote_date)),
        ("Gültig bis", _de_date(quote.valid_until_date)),
        ("Objekt", ort or None),
    ]
    _render_anschrift_und_infoblock(pdf, zeilen, meta)

    titel_text = f"Angebot {quote.quote_number or ''}".strip()
    if entwurf:
        titel_text += " — Entwurf"
    _render_titel(pdf, titel_text, quote.title)

    # Positionstabelle + Summen (gemeinsame Bausteine)
    _render_lines(pdf, quote.lines.all())
    _render_totals(pdf, quote.net_total, quote.tax_total, quote.gross_total)

    out = pdf.output()
    return bytes(out)


def render_quote_pdf(quote_id):
    """Rendert das PDF eines versendeten Angebots und gibt die Bytes zurück.

    Gibt None zurück, wenn das Angebot nicht existiert oder noch nicht versendet
    ist (ein Entwurf erhält keine finale Ausfertigung — siehe _QUOTE_PDF_STATUSES).
    """
    quote = _load_quote(quote_id)
    if quote is None or quote.status not in _QUOTE_PDF_STATUSES:
        return None
    return render_quote_document(quote)


def render_quote_preview(quote_id):
    """Vorschau-PDF eines Angebots in JEDEM Status (Bytes) oder None.

    Noch nicht versendete Angebote erhalten den ENTWURF-Aufdruck. Es wird
    nichts archiviert.
    """
    quote = _load_quote(quote_id)
    if quote is None:
        return None
    return render_quote_document(
        quote, entwurf=quote.status not in _QUOTE_PDF_STATUSES
    )


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
