"""Baustellenbericht-PDF (workflow.site_report) im Mitra-Markenlayout.

Erste PDF-Ausgabe des Berichts überhaupt — rein lesend, on-the-fly, KEINE
Archivierung: der Bericht selbst ist das Original (unterzeichnet = versiegelt,
DB-Trigger `protect_site_report`); das PDF ist seine Druckansicht. Ein Bericht
im ENTWURF trägt den ENTWURF-Aufdruck des Beleg-Layouts.

Inhalt: Kontext (Auftrag/Einsatz/Liegenschaft), Tätigkeit, Material/Bemerkungen,
die Berichtspositionen (bewusst OHNE Preise — siehe Migration 0080: ein
unterschriebener Bericht mit Preisen wäre eine Preisvereinbarung) mit
Soll/Ist-Mengen, Fotos (vorher/nachher, aus dem Objektspeicher, fürs PDF
verkleinert) und die Kundenunterschrift.

Graceful degradation wie beim Beleg-PDF: nicht abrufbare Fotos oder eine nicht
abrufbare Unterschrift lassen das PDF NIE scheitern — der Abschnitt wird mit
Log-Warnung übersprungen (der Bericht bleibt zugänglich).
"""
import logging
from io import BytesIO

from db_core import storage as storage_module
from db_core.models import CompanyProfile, File
from db_core.services import dateien as dateien_service
from db_core.services import site_report as report_service
from db_core.services.beleg import issuer_stammdaten
from db_core.services.beleg_pdf import (
    _HAIR,
    _HINT,
    _INK,
    _M_L,
    _M_R,
    _NAVY,
    _PAGE_W,
    _PAPER,
    _USABLE,
    _FOOT_H,
    _de_date,
    _logo_bytes,
    _num,
    _platz_sichern,
    _render_titel,
    _txt,
    new_beleg_pdf,
)

log = logging.getLogger(__name__)

# Foto-Kategorien in Anzeige-Reihenfolge mit Abschnittstitel.
_FOTO_GRUPPEN = (
    ("FOTO_VORHER", "Fotos — vorher"),
    ("FOTO_NACHHER", "Fotos — nachher"),
)

# Fotos fürs PDF verkleinern: Handy-JPEGs (3–8 MB) würden das PDF aufblähen.
_FOTO_MAX_PX = 1400
_FOTO_JPEG_QUALITAET = 82


def _verkleinert(inhalt):
    """Bild-Bytes fürs PDF verkleinern (max. Kante, JPEG). Bei jedem Fehler
    kommen die Original-Bytes zurück — lieber ein großes PDF als gar keins."""
    try:
        from PIL import Image

        img = Image.open(BytesIO(inhalt))
        img.thumbnail((_FOTO_MAX_PX, _FOTO_MAX_PX))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = BytesIO()
        img.save(out, format="JPEG", quality=_FOTO_JPEG_QUALITAET)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - Verkleinern ist reine Optimierung
        return inhalt


def _abschnitt(pdf, titel, text):
    """Überschrift in Navy + Fließtext; entfällt ohne Text."""
    if not text:
        return
    _platz_sichern(pdf, 18)
    pdf.ln(4)
    pdf.set_font("InterSB", "", 10)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, _txt(titel), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.set_font("Inter", "", 9)
    pdf.multi_cell(_USABLE, 4.8, _txt(text), align="L",
                   new_x="LMARGIN", new_y="NEXT")


_COLS_BERICHT = (12, 91, 22, 22, 18)  # Pos · Beschreibung · Soll · Ist · Einheit


def _positionskopf(pdf):
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("InterSB", "", 8.5)
    for w, h in zip(_COLS_BERICHT, ("Pos", "Beschreibung", "Soll", "Ist", "Einheit")):
        align = "R" if h in ("Soll", "Ist") else "L"
        pdf.cell(w, 8, f" {h}" if align == "L" else f"{h} ", align=align, fill=True)
    pdf.ln(8)
    pdf.set_text_color(*_INK)


def _render_positionen(pdf, lines):
    """Berichtspositionen mit Soll/Ist-Mengen — ohne Preise (Invariante 0080)."""
    lines = list(lines)
    if not lines:
        return
    _platz_sichern(pdf, 26)
    pdf.ln(4)
    pdf.set_font("InterSB", "", 10)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, "Positionen (Mengen, ohne Preise)",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    _positionskopf(pdf)
    for line in lines:
        ist_text = line.line_type == "TEXT"
        text = _txt(line.description)
        if line.note:
            text = f"{text}\n{line.note}"
        pdf.set_font("InterM", "", 9)
        n_zeilen = max(1, len(pdf.multi_cell(
            _COLS_BERICHT[1] - 2, 4.6, text, align="L",
            dry_run=True, output="LINES")))
        h = 4.6 * n_zeilen + 3.6
        if pdf.get_y() + h > 297 - _FOOT_H:
            pdf.add_page()
            _positionskopf(pdf)
        y0 = pdf.get_y()
        pdf.set_font("Inter", "", 9)
        pdf.set_xy(_M_L, y0 + 1.8)
        pdf.cell(_COLS_BERICHT[0], 4.6,
                 "" if ist_text else f" {line.position_number}")
        if ist_text:
            pdf.set_text_color(*_HINT)
        pdf.set_font("InterM" if not ist_text else "Inter", "", 9)
        pdf.set_xy(_M_L + _COLS_BERICHT[0], y0 + 1.8)
        pdf.multi_cell(_COLS_BERICHT[1] - 2, 4.6, text, align="L")
        pdf.set_text_color(*_INK)
        pdf.set_font("Inter", "", 9)
        pdf.set_xy(_M_L + _COLS_BERICHT[0] + _COLS_BERICHT[1], y0 + 1.8)
        soll = "" if ist_text or line.planned_quantity is None else _num(line.planned_quantity)
        ist = "" if ist_text or line.quantity is None else _num(line.quantity)
        pdf.cell(_COLS_BERICHT[2], 4.6, soll, align="R")
        pdf.cell(_COLS_BERICHT[3], 4.6, ist, align="R")
        pdf.cell(_COLS_BERICHT[4], 4.6, f" {_txt(line.unit or '')}")
        pdf.set_draw_color(*_HAIR)
        pdf.set_line_width(0.2)
        pdf.line(_M_L, y0 + h, _M_L + _USABLE, y0 + h)
        pdf.set_y(y0 + h)


def _bild_links(report_id):
    """Bild-Verknüpfungen des Berichts, gruppiert nach Kategorie."""
    links = dateien_service.dateien_am_ziel(site_report_id=report_id)
    gruppen = {}
    for link in links:
        if not (link.file.mime_type or "").startswith("image/"):
            continue
        gruppen.setdefault(link.link_category, []).append(link.file)
    return gruppen


def _render_fotos(pdf, report_id):
    """Fotoraster (2 je Zeile), vorher/nachher getrennt. Nicht abrufbare Fotos
    werden mit Log-Warnung übersprungen — das PDF scheitert nie an einem Foto."""
    gruppen = _bild_links(report_id)
    if not gruppen:
        return
    reihenfolge = [g for g in _FOTO_GRUPPEN if g[0] in gruppen]
    uebrige = [k for k in gruppen if k not in {g[0] for g in _FOTO_GRUPPEN}]
    if uebrige:
        reihenfolge.append((None, "Fotos"))
    zell_w = (_USABLE - 6) / 2
    zell_h = 58
    for kategorie, titel in reihenfolge:
        dateien = (
            gruppen.get(kategorie, [])
            if kategorie is not None
            else [f for k in uebrige for f in gruppen[k]]
        )
        if not dateien:
            continue
        _platz_sichern(pdf, 14 + zell_h)
        pdf.ln(4)
        pdf.set_font("InterSB", "", 10)
        pdf.set_text_color(*_NAVY)
        pdf.cell(0, 6, titel, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_INK)
        pdf.ln(1)
        spalte = 0
        for datei in dateien:
            try:
                inhalt = storage_module.get_storage().get_object(datei.storage_key)
            except storage_module.StorageError as exc:
                log.warning(
                    "Bericht-PDF %s: Foto %s nicht abrufbar (%s); übersprungen.",
                    report_id, datei.id, exc,
                )
                continue
            if spalte == 0:
                _platz_sichern(pdf, zell_h + 4)
                zeilen_y = pdf.get_y()
            x = _M_L + spalte * (zell_w + 6)
            try:
                pdf.image(BytesIO(_verkleinert(inhalt)), x=x, y=zeilen_y,
                          w=zell_w, h=zell_h, keep_aspect_ratio=True)
            except Exception as exc:  # noqa: BLE001 - Foto darf nie scheitern
                log.warning(
                    "Bericht-PDF %s: Foto %s nicht einbettbar (%s); übersprungen.",
                    report_id, datei.id, exc,
                )
                continue
            spalte += 1
            if spalte == 2:
                spalte = 0
                pdf.set_y(zeilen_y + zell_h + 4)
        if spalte:
            pdf.set_y(zeilen_y + zell_h + 4)


def _render_unterschrift(pdf, report):
    """Unterschriftsblock des unterzeichneten Berichts (Name, Zeitpunkt, Bild)."""
    if report.status != "UNTERZEICHNET":
        return
    _platz_sichern(pdf, 46)
    pdf.ln(5)
    y0 = pdf.get_y()
    pdf.set_fill_color(*_PAPER)
    pdf.set_draw_color(*_NAVY)
    pdf.set_line_width(0.3)
    pdf.rect(_M_L, y0, _USABLE, 40, style="DF",
             round_corners=True, corner_radius=2.5)
    pdf.set_xy(_M_L + 5, y0 + 4)
    pdf.set_font("InterSB", "", 10)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 5.5, "Abnahme durch den Kunden", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.set_font("Inter", "", 9)
    signiert = _txt(report.signed_by_name)
    if report.signed_at:
        signiert += f" · {report.signed_at.strftime('%d.%m.%Y %H:%M')} Uhr"
    pdf.set_xy(_M_L + 5, y0 + 11)
    pdf.cell(0, 5, signiert, new_x="LMARGIN", new_y="NEXT")
    file_id = getattr(report, "signature_file_id", None)
    if file_id:
        try:
            datei = File.objects.filter(id=file_id).only("storage_key").first()
            if datei is not None:
                inhalt = storage_module.get_storage().get_object(datei.storage_key)
                pdf.image(BytesIO(inhalt), x=_M_L + 5, y=y0 + 18,
                          w=60, h=19, keep_aspect_ratio=True)
        except Exception as exc:  # noqa: BLE001 - Unterschrift darf nie scheitern
            log.warning(
                "Bericht-PDF %s: Unterschrift nicht abrufbar/einbettbar (%s).",
                report.id, exc,
            )
    pdf.set_y(y0 + 40)


def render_site_report_pdf(report_id):
    """Rendert das Bericht-PDF (Bytes) oder None, wenn es den Bericht nicht gibt.

    ENTWURF → ENTWURF-Aufdruck; UNTERZEICHNET → normales Sichtbild mit
    Unterschriftsblock. Der Aussteller kommt live aus dem Firmenprofil (der
    Bericht ist kein GoBD-Beleg mit eingefrorenem Stammdaten-Snapshot).
    """
    report = report_service.get_report(report_id)
    if report is None:
        return None

    issuer = issuer_stammdaten()
    logo = _logo_bytes(CompanyProfile.objects.first())
    pdf = new_beleg_pdf(issuer=issuer, logo_bytes=logo,
                        entwurf=report.status != "UNTERZEICHNET")

    # Kontextzeilen: Auftrag/Einsatz + Liegenschaft (vom Anker, Migration 0064)
    kontext = []
    wo = report.work_order
    if wo is not None:
        kontext.append(("Auftrag", wo.title))
        prop = getattr(wo, "property", None)
        if prop is not None:
            ort = " · ".join(b for b in (
                prop.name, getattr(getattr(prop, "address", None), "city", None)
            ) if b)
            if ort:
                kontext.append(("Objekt", ort))
    sj = report.service_job
    if sj is not None and sj.title:
        kontext.append(("Einsatz", sj.title))

    meta = [
        ("Berichtsdatum", _de_date(report.report_date)),
        ("Monteur", report.author.display_name if report.author else None),
        ("Arbeitsstunden", _num(report.hours_worked) if report.hours_worked else None),
        ("Wetter", report.weather),
        ("Status", "Unterzeichnet" if report.status == "UNTERZEICHNET" else "Entwurf"),
    ]

    # Kontext links (statt Anschriftfeld — der Bericht ist kein Brief),
    # Infoblock rechts wie beim Beleg.
    pdf.set_y(47)
    pdf.set_font("Inter", "", 10.5)
    for label, wert in kontext:
        pdf.set_font("Inter", "", 7.5)
        pdf.set_text_color(*_HINT)
        pdf.cell(18, 5.6, label)
        pdf.set_font("InterM", "", 10)
        pdf.set_text_color(*_INK)
        pdf.multi_cell(80, 5.6, _txt(wert), align="L",
                       new_x="LMARGIN", new_y="NEXT")
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
    pdf.set_y(88)

    titel = f"Baustellenbericht vom {_de_date(report.report_date)}"
    untertitel = " · ".join(_txt(w) for _, w in kontext[:2]) or None
    _render_titel(pdf, titel, untertitel)

    _abschnitt(pdf, "Ausgeführte Arbeiten", report.activity_text)
    _abschnitt(pdf, "Material", report.materials_note)
    _abschnitt(pdf, "Bemerkungen", report.remarks)
    _render_positionen(pdf, report_service.list_report_lines(report.id))
    _render_fotos(pdf, report.id)
    _render_unterschrift(pdf, report)

    return bytes(pdf.output())
