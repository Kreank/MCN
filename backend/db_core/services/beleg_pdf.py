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
"""
import logging
import uuid
from decimal import Decimal

from django.db import IntegrityError, connection
from fpdf import FPDF

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import CompanyProfile, Invoice, Quote

log = logging.getLogger(__name__)

_BELEG_PDF_CATEGORY = "BELEG_PDF"

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


def _render_lines(pdf, lines):
    """Positionstabelle (Kopf + Zeilen). Gemeinsam für Rechnung und Angebot —
    Angebots- und Rechnungsposition tragen dieselben Felder (position_number,
    line_type, quantity, unit, unit_price, net_amount, description)."""
    widths = (12, 82, 20, 16, 28, 32)  # Summe 190 mm (usable width)
    headers = ("Pos", "Beschreibung", "Menge", "Einheit", "Einzelpreis", "Betrag")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(238, 238, 238)
    for w, h in zip(widths, headers):
        align = "R" if h in ("Menge", "Einzelpreis", "Betrag") else "L"
        pdf.cell(w, 7, h, border="B", align=align, fill=True)
    pdf.ln(7)

    pdf.set_font("Helvetica", "", 9)
    for line in sorted(lines, key=lambda x: x.position_number):
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
        pdf.set_font("Helvetica", "B" if bold else "", 10 if bold else 9)
        pdf.cell(label_w, 7, label, align="R")
        pdf.cell(val_w, 7, _eur(value), align="R",
                 border="T" if bold else 0, new_x="LMARGIN", new_y="NEXT")


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

    # Positionstabelle + Summen (gemeinsame Bausteine)
    _render_lines(pdf, invoice.lines.all())
    _render_totals(pdf, invoice.net_total, invoice.tax_total, invoice.gross_total)

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

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 18, 20)
    pdf.add_page()

    profile = CompanyProfile.objects.first()
    _render_issuer(pdf, profile)

    # Empfänger, falls ableitbar; sonst die Liegenschaft als Bezug (ein Angebot
    # darf ohne formalen Adressaten rendern).
    pdf.set_font("Helvetica", "", 11)
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
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, f"Angebot {quote.quote_number or ''}".strip(),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(70, 70, 70)
    pdf.cell(0, 7, _txt(quote.title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
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

def _archived_key_for(link_column, ident):
    """storage_key der bereits archivierten BELEG_PDF-Ausfertigung, sonst None.

    Reine Leseabfrage (Autocommit). Der partielle UNIQUE-Index (Migration 0032)
    garantiert höchstens eine solche Zeile je Beleg. `link_column` ist ein
    kontrolliertes internes Literal ('invoice_id'|'quote_id'), keine Nutzereingabe.
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
            [str(ident), _BELEG_PDF_CATEGORY],
        )
        row = cur.fetchone()
    return row[0] if row else None


def _archived_storage_key(invoice_id):
    """storage_key der archivierten Rechnungs-Ausfertigung (Migration 0032)."""
    return _archived_key_for("invoice_id", invoice_id)


def _archived_quote_storage_key(quote_id):
    """storage_key der archivierten Angebots-Ausfertigung (Migration 0032)."""
    return _archived_key_for("quote_id", quote_id)


def _insert_file_and_link(actor_app_user_id, link_column, ident, *, storage_key,
                          original_filename, sha256, size_bytes):
    """Registriert content.file + content.file_link in EINER Transaktion.

    Wirft IntegrityError, wenn parallel bereits eine BELEG_PDF-Ausfertigung
    verlinkt wurde (partieller UNIQUE-Index) — der Aufrufer behandelt den
    Wettlauf mit Nachselektion. Bei diesem Fehler rollt die atomic-Transaktion
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
                VALUES (gen_random_uuid(), %s, %s, 'application/pdf', %s, %s, %s)
                RETURNING id
                """,
                [storage_key, original_filename, size_bytes, sha256,
                 str(actor_app_user_id)],
            )
            file_id = cur.fetchone()[0]
            cur.execute(
                f"""
                INSERT INTO content.file_link
                    (id, file_id, {link_column}, link_category, created_by)
                VALUES (gen_random_uuid(), %s, %s, %s, %s)
                """,
                [str(file_id), str(ident), _BELEG_PDF_CATEGORY,
                 str(actor_app_user_id)],
            )
    return file_id


def _register_beleg_file(actor_app_user_id, invoice_id, *, storage_key,
                         original_filename, sha256, size_bytes):
    """Registriert die Rechnungs-Ausfertigung (content.file + file_link)."""
    return _insert_file_and_link(
        actor_app_user_id, "invoice_id", invoice_id, storage_key=storage_key,
        original_filename=original_filename, sha256=sha256, size_bytes=size_bytes,
    )


def _register_quote_file(actor_app_user_id, quote_id, *, storage_key,
                         original_filename, sha256, size_bytes):
    """Registriert die Angebots-Ausfertigung (content.file + file_link)."""
    return _insert_file_and_link(
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


def _get_or_archive_pdf(actor_app_user_id, ident, *, storage_prefix, render_fn,
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
        served = _serve_archived(existing_key)
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
        _best_effort_remove(storage, info.storage_key)
        winner_key = key_lookup(ident)
        if winner_key is not None:
            served = _serve_archived(winner_key)
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
    ist (Endpunkt → 404). Siehe _get_or_archive_pdf für den Ablauf (Archivierung
    beim Erstabruf, Wettlauf-Nachselektion, Degradation ohne Objektspeicher).
    """
    return _get_or_archive_pdf(
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
    _get_or_archive_pdf); der partielle UNIQUE-Index auf file_link.quote_id
    (Migration 0032) sichert die eine Ausfertigung je Angebot.
    """
    return _get_or_archive_pdf(
        actor_app_user_id, quote_id,
        storage_prefix="belege/angebot",
        render_fn=render_quote_pdf,
        key_lookup=_archived_quote_storage_key,
        register_fn=_register_quote_file,
        filename_fn=_quote_filename,
    )


def _serve_archived(storage_key):
    """Lädt die archivierten Bytes; None, wenn der Objektspeicher gerade fehlt."""
    try:
        return storage_module.get_storage().get_object(storage_key)
    except storage_module.StorageError as exc:
        log.warning(
            "Beleg-PDF: archiviertes Objekt %s nicht abrufbar (%s).",
            storage_key, exc,
        )
        return None


def _best_effort_remove(storage, storage_key):
    try:
        storage.remove_object(storage_key)
    except storage_module.StorageError as exc:  # pragma: no cover - Best-Effort
        log.warning(
            "Beleg-PDF: verwaistes Objekt %s konnte nicht entfernt werden (%s).",
            storage_key, exc,
        )
