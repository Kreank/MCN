"""Tests für das Beleg-PDF (services/beleg_pdf.py) mit Firmenprofil-Anschluss.

Kernpunkte: Das PDF zieht den Aussteller aus dem Firmenprofil; ohne gepflegtes
Profil greift ein neutraler Fallback (kein Absturz). Für eine unveröffentlichte
oder fehlende Rechnung gibt es kein PDF (None).
"""
import io
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from pypdf import PdfReader

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import Quote
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


# Kleinstes gültiges 1x1-PNG — von fpdf2/Pillow einbettbar.
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    """In-memory-Objektspeicher (Schnittstelle wie ObjectStorage)."""

    def __init__(self):
        self.objects = {}

    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        payload = bytes(data)
        self.objects[key] = payload
        return storage_module.ObjectInfo(
            storage_key=key, sha256=sha256(payload).hexdigest(), size_bytes=len(payload)
        )

    def get_object(self, key):
        if key not in self.objects:
            raise storage_module.StorageError(f"unbekannt {key}")
        return self.objects[key]

    def remove_object(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    return fake


def _pdf_text(data):
    """Sichtbarer Text eines PDF.

    Seit der Umstellung auf die eingebettete DejaVu-Schrift (zwingend für
    PDF/A-3B, siehe services/erechnung.py) stehen im Inhaltsstrom keine
    Latin-1-Bytes mehr, sondern Glyph-IDs — lesbar nur über die
    ToUnicode-Tabelle. Das erledigt pypdf (liegt ohnehin als
    factur-x-Abhängigkeit vor).

    Whitespace wird zusammengezogen: wo eine Zeile im Layout umbricht, ist der
    Umbruch für den Test bedeutungslos (DejaVu setzt breiter als der frühere
    Kernfont, also brechen längere Zeilen an anderer Stelle um).
    """
    reader = PdfReader(io.BytesIO(data))
    roh = "\n".join(seite.extract_text() for seite in reader.pages)
    return " ".join(roh.split())


def _published_invoice(app_user, *, lines=None, publish=True, **invoice_kwargs):
    obj = property_service.create_property(
        app_user.id, name="PDF-Objekt", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="Wanda", last_name="WEG")
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag zur Rechnung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=weg.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        lines=lines or [
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
        **invoice_kwargs,
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id, role=role, is_primary=True
        )
    if publish:
        beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


@pytest.mark.django_db
def test_pdf_ohne_profil_faellt_sauber_zurueck(app_user):
    """Ohne Firmenprofil erzeugt das PDF trotzdem gültige Bytes (kein Absturz)."""
    inv = _published_invoice(app_user)
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None
    assert data[:4] == b"%PDF"


def test_aussteller_fallback_ohne_profil():
    """Ohne Profil liefert die Aussteller-Zeile den neutralen Fallback."""
    name, subline = beleg_pdf._issuer_lines(None)
    assert name == beleg_pdf._FALLBACK_NAME
    assert beleg_pdf._footer_parts(None) == []


@pytest.mark.django_db
def test_pdf_nutzt_firmenprofil_als_aussteller(app_user):
    firma_service.update_company_profile(
        app_user.id, company_name="Mitra Sanitär GmbH",
        street="Industriestraße 5", postal_code="12345", city="Musterstadt",
        tax_number="12/345/67890", iban="DE12500105170648489890",
    )
    # Erst NACH dem gepflegten Profil veröffentlichen: der Aussteller wandert bei
    # der Veröffentlichung in den Snapshot (SNAPSHOT_VERSION 2) und wird von dort
    # gerendert.
    inv = _published_invoice(app_user)
    issuer = beleg_service.beleg_stammdaten(
        beleg_pdf.load_invoice_for_render(inv.id)
    )["issuer"]
    name, subline = beleg_pdf._issuer_lines(issuer)
    assert name == "Mitra Sanitär GmbH"
    assert "Musterstadt" in subline
    parts = beleg_pdf._footer_parts(issuer)
    assert any("12/345/67890" in p for p in parts)
    assert any("IBAN DE12500105170648489890" in p for p in parts)
    # Und das vollständige PDF rendert weiterhin zu gültigen Bytes.
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    assert "Mitra Sanitär GmbH" in _pdf_text(data)


# --- Firmenlogo im PDF-Kopf ------------------------------------------------

@pytest.mark.django_db
def test_pdf_bettet_logo_ein(app_user, fake_storage):
    """Ist ein Logo gepflegt, enthält das gerenderte PDF das eingebettete Bild."""
    inv = _published_invoice(app_user)
    firma_service.update_company_profile(app_user.id, company_name="Logo GmbH")
    firma_service.set_company_logo(app_user.id, dateiname="logo.png", inhalt=PNG_1x1)

    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    # fpdf2 legt eingebettete Bilder als Image-XObject ab.
    assert b"/Subtype /Image" in data


@pytest.mark.django_db
def test_pdf_ohne_logo_nutzt_markenfallback(app_user, fake_storage):
    """Ohne gepflegtes Firmenlogo rendert das PDF mit den eingebauten
    Markenzeichen (Wortmarke + Wasserzeichen) — Bilder sind seit dem
    Marken-Layout immer enthalten, ein Absturz bleibt ausgeschlossen."""
    inv = _published_invoice(app_user)
    firma_service.update_company_profile(app_user.id, company_name="Ohne Logo GmbH")
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    assert b"/Subtype /Image" in data


@pytest.mark.django_db
def test_pdf_ohne_profil_rendert_ohne_storagezugriff(app_user):
    """Ohne Firmenprofil-Logo rendert dieselbe Rechnung ohne jeden
    Storage-Zugriff und ohne Absturz (Markenzeichen kommen aus dem Repo,
    nicht aus MinIO)."""
    inv = _published_invoice(app_user)
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"


@pytest.mark.django_db
def test_pdf_logo_gesetzt_aber_storage_weg_rendert_ohne_logo(app_user, monkeypatch):
    """Logo ist gesetzt, der Objektspeicher aber nicht erreichbar → das PDF
    rendert trotzdem (mit Marken-Fallback statt Profillogo), kein Absturz."""
    # Logo mit funktionierendem Speicher setzen …
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    inv = _published_invoice(app_user)
    firma_service.update_company_profile(app_user.id, company_name="Degrad GmbH")
    firma_service.set_company_logo(app_user.id, dateiname="logo.png", inhalt=PNG_1x1)

    # … dann den Speicher „ausschalten".
    def boom():
        raise storage_module.StorageError("MinIO weg")

    monkeypatch.setattr(storage_module, "get_storage", boom)
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"


@pytest.mark.django_db
def test_pdf_kaputtes_logo_rendert_ohne_absturz(app_user, monkeypatch):
    """Sind die Logo-Bytes kein gültiges Bild, wird das Logo übersprungen — das
    PDF darf nie an einem Logo scheitern."""
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    inv = _published_invoice(app_user)
    firma_service.update_company_profile(app_user.id, company_name="Kaputt GmbH")
    # Magic-Bytes gaukeln PNG vor, der Rest ist Müll → fpdf2/Pillow scheitert am
    # Einbetten, _place_logo fängt das ab.
    firma_service.set_company_logo(
        app_user.id, dateiname="logo.png", inhalt=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    )
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"


@pytest.mark.django_db
def test_pdf_nur_fuer_veroeffentlichte(app_user):
    obj = property_service.create_property(
        app_user.id, name="Entwurf-Objekt", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Z", "quantity": 1,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    assert beleg_pdf.render_invoice_pdf(inv.id) is None


# --- Angebots-PDF (invoicing.quote) ----------------------------------------

def _entwurf_quote(app_user, *, with_order=True, recipient_role="INVOICE_RECIPIENT",
                   with_email=True):
    """Angebots-Entwurf (ENTWURF) inkl. optionalem Auftrag mit Empfänger-Beteiligtem."""
    obj = property_service.create_property(
        app_user.id, name="Angebots-Objekt", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    weg = identity_service.create_person(
        app_user.id, first_name="Quintus", last_name="Quote"
    )
    if with_email:
        identity_service.add_contact_point(
            app_user.id, weg.id, contact_type="EMAIL",
            value="angebot-kunde@example.test", is_primary=True,
        )
    order = None
    if with_order:
        order = auftrag_service.create_work_order(
            app_user.id, property_id=obj.id, title="Auftrag zum Angebot"
        )
        if recipient_role is not None:
            auftrag_service.add_work_order_party(
                app_user.id, work_order_id=order.id, party_id=weg.id,
                role=recipient_role, is_primary=True,
            )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Bad sanieren",
        lines=[{"line_type": "MATERIAL", "description": "Fliesen", "quantity": 20,
                "unit": "m2", "unit_price": "34.50", "tax_code": "DE_19"}],
    )
    if order is not None:
        with business_transaction(app_user.id):
            Quote.objects.filter(id=quote.id).update(work_order_id=order.id)
    quote.refresh_from_db()
    return quote, weg


def _sent_quote(app_user, **kw):
    quote, weg = _entwurf_quote(app_user, **kw)
    beleg_service.send_quote(app_user.id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote, weg


@pytest.mark.django_db
def test_quote_pdf_ab_versendet(app_user):
    """Ein versendetes Angebot rendert zu gültigen PDF-Bytes."""
    quote, _ = _sent_quote(app_user)
    assert quote.status == "VERSENDET"
    data = beleg_pdf.render_quote_pdf(quote.id)
    assert data is not None and data[:4] == b"%PDF"


@pytest.mark.django_db
def test_quote_pdf_entwurf_liefert_kein_finales_pdf(app_user):
    """Ein Entwurf täuscht kein finales PDF vor (None → 404 in der API)."""
    quote, _ = _entwurf_quote(app_user)
    assert quote.status == "ENTWURF"
    assert beleg_pdf.render_quote_pdf(quote.id) is None


@pytest.mark.django_db
def test_quote_pdf_unbekannt_none(app_user):
    import uuid as _uuid
    assert beleg_pdf.render_quote_pdf(_uuid.uuid4()) is None


@pytest.mark.django_db
def test_quote_empfaenger_ableitung_invoice_recipient(app_user):
    """Empfänger wird über work_order_party INVOICE_RECIPIENT abgeleitet."""
    quote, weg = _sent_quote(app_user, recipient_role="INVOICE_RECIPIENT")
    q = (
        Quote.objects.filter(id=quote.id)
        .select_related("work_order")
        .prefetch_related("work_order__parties__party")
        .first()
    )
    party = beleg_pdf.quote_recipient_party(q)
    assert party is not None and party.id == weg.id


@pytest.mark.django_db
def test_quote_empfaenger_fallback_principal(app_user):
    """Ohne INVOICE_RECIPIENT greift PRINCIPAL als Ersatz."""
    quote, weg = _sent_quote(app_user, recipient_role="PRINCIPAL")
    q = (
        Quote.objects.filter(id=quote.id)
        .select_related("work_order")
        .prefetch_related("work_order__parties__party")
        .first()
    )
    party = beleg_pdf.quote_recipient_party(q)
    assert party is not None and party.id == weg.id


@pytest.mark.django_db
def test_quote_pdf_ohne_auftrag_rendert_ohne_empfaenger(app_user):
    """Ein Angebot OHNE Auftrag/Empfänger rendert trotzdem (kein formaler Adressat)."""
    quote, _ = _sent_quote(app_user, with_order=False, with_email=False)
    q = Quote.objects.filter(id=quote.id).select_related("work_order").first()
    assert beleg_pdf.quote_recipient_party(q) is None
    data = beleg_pdf.render_quote_pdf(quote.id)
    assert data is not None and data[:4] == b"%PDF"


# --- Zahlungsbedingungen / Skonto (Migration 0058) --------------------------

@pytest.mark.django_db
def test_pdf_zeigt_skonto_hinweis(app_user):
    """Der Beleg trägt die Zahlungsbedingung im Klartext — nicht nur als Feld
    irgendwo im UI: der Kunde muss sie auf dem PDF lesen können."""
    inv = _published_invoice(
        app_user, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    # Brutto: 100 x 2,40 = 240,00 netto + 19 % = 285,60 → 2 % Skonto = 5,71 EUR.
    assert inv.gross_total == Decimal("285.60")
    assert inv.due_date == date(2026, 7, 31)

    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    text = _pdf_text(data)
    assert "Zahlungsbedingungen" in text
    assert "Skonto" in text
    assert "5,71 EUR" in text          # Skontobetrag
    assert "11.07.2026" in text        # Skontofrist (Belegdatum + 10 Tage)
    assert "31.07.2026" in text        # Zahlungsziel


@pytest.mark.django_db
def test_pdf_ohne_skonto_zeigt_nettofrist(app_user):
    inv = _published_invoice(
        app_user, invoice_date=date(2026, 7, 1), payment_term_days=14
    )
    data = beleg_pdf.render_invoice_pdf(inv.id)
    text = _pdf_text(data)
    assert "Zahlbar ohne Abzug bis 15.07.2026." in text
    assert "Skonto" not in text


@pytest.mark.django_db
def test_pdf_ohne_faelligkeit_zeigt_keine_zahlungsbedingung(app_user):
    inv = _published_invoice(app_user, invoice_date=date(2026, 7, 1))
    text = _pdf_text(beleg_pdf.render_invoice_pdf(inv.id))
    assert "Zahlbar ohne Abzug" not in text
    assert "Zahlungsbedingungen" not in text


@pytest.mark.django_db
def test_pdf_skontobetrag_kommt_aus_dem_service(app_user):
    """Keine zweite Rechenstelle: der Text nutzt beleg.zahlungsbedingungen()."""
    inv = _published_invoice(
        app_user, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="3", discount_days=8,
    )
    zb = beleg_service.zahlungsbedingungen(inv)
    text = beleg_pdf.zahlungsbedingungen_text(inv)
    assert beleg_pdf._eur(zb["skonto_betrag"]) in text
    assert beleg_pdf._de_date(zb["skonto_bis"]) in text
    assert zb["skonto_bis"] == inv.invoice_date + timedelta(days=8)


@pytest.mark.django_db
def test_pdf_storno_hat_keine_zahlungsbedingung(app_user):
    """Eine Stornorechnung fordert kein Geld — kein „zahlbar bis"."""
    inv = _published_invoice(
        app_user, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    text = _pdf_text(beleg_pdf.render_invoice_pdf(storno.id))
    assert "Zahlungsbedingungen" not in text
    assert "Zahlbar ohne Abzug" not in text


# --- § 35a-Ausweis (Migration 0076) -----------------------------------------

_LOHN_UND_MATERIAL = [
    {"line_type": "ARBEITSZEIT", "description": "Monteurstunden", "quantity": 10,
     "unit": "h", "unit_price": "60.00", "tax_code": "DE_19"},
    {"line_type": "FAHRT", "description": "Anfahrt", "quantity": 1,
     "unit_price": "40.00", "tax_code": "DE_19"},
    {"line_type": "MATERIAL", "description": "Heizkörper", "quantity": 2,
     "unit": "Stk", "unit_price": "300.00", "tax_code": "DE_19"},
]


@pytest.mark.django_db
def test_pdf_weist_die_arbeitskosten_nach_35a_aus(app_user):
    """Ohne diesen Block verliert der Privatkunde 20 % der Arbeitskosten."""
    inv = _published_invoice(app_user, lines=_LOHN_UND_MATERIAL)
    text = _pdf_text(beleg_pdf.render_invoice_pdf(inv.id))

    assert "Arbeitskosten nach § 35a EStG" in text
    # 600 Lohn + 40 Fahrt = 640,00 netto; USt 121,60; brutto 761,60.
    assert beleg_pdf._eur(Decimal("640.00")) in text
    assert beleg_pdf._eur(Decimal("121.60")) in text
    assert beleg_pdf._eur(Decimal("761.60")) in text
    # Der Materialanteil (600,00) darf NICHT in den Arbeitskosten stecken.
    ausweis = beleg_service.arbeitskosten(inv)
    assert ausweis["net_amount"] == Decimal("640.00")


@pytest.mark.django_db
def test_pdf_ohne_ausweis_wenn_abgeschaltet(app_user):
    """B2B-Rechnung: der Block lässt sich je Beleg abschalten."""
    inv = _published_invoice(
        app_user, lines=_LOHN_UND_MATERIAL, show_labour_costs=False
    )
    text = _pdf_text(beleg_pdf.render_invoice_pdf(inv.id))
    assert "35a" not in text


@pytest.mark.django_db
def test_pdf_ohne_ausweis_wenn_eine_position_unbestimmt_ist(app_user):
    """Lieber kein Ausweis als ein falscher: eine Pauschale ohne bestimmten Anteil
    macht den ganzen Ausweis unmöglich — es wird nichts geschätzt."""
    inv = _published_invoice(app_user, lines=_LOHN_UND_MATERIAL + [
        {"line_type": "PAUSCHALE", "description": "Bad komplett", "quantity": 1,
         "unit_price": "2000.00", "tax_code": "DE_19"},
    ])
    text = _pdf_text(beleg_pdf.render_invoice_pdf(inv.id))
    assert "35a" not in text


@pytest.mark.django_db
def test_pdf_ohne_ausweis_bei_reiner_materiallieferung(app_user):
    """Keine Arbeitskosten enthalten → kein leerer 0,00-EUR-Block."""
    inv = _published_invoice(app_user)  # Default: nur MATERIAL
    text = _pdf_text(beleg_pdf.render_invoice_pdf(inv.id))
    assert "35a" not in text


# --- Vorschau (render_invoice_preview / render_quote_preview) ----------------
# Die Vorschau rendert JEDEN Status on-the-fly und archiviert nichts. Ein
# unveröffentlichter Beleg trägt den ENTWURF-Aufdruck, die veröffentlichte
# Rechnung zeigt ihr normales Sichtbild.

@pytest.mark.django_db
def test_vorschau_entwurf_traegt_entwurfsaufdruck(app_user):
    inv = _published_invoice(app_user, publish=False)
    data = beleg_pdf.render_invoice_preview(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    text = _pdf_text(data)
    assert "ENTWURF" in text
    assert "Entwurf" in text  # Titelzusatz „— Entwurf"


@pytest.mark.django_db
def test_vorschau_veroeffentlicht_ohne_entwurfsaufdruck(app_user):
    inv = _published_invoice(app_user)
    data = beleg_pdf.render_invoice_preview(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    assert "ENTWURF" not in _pdf_text(data)


@pytest.mark.django_db
def test_vorschau_unbekannte_rechnung_ist_none(app_user):
    import uuid as _uuid
    assert beleg_pdf.render_invoice_preview(_uuid.uuid4()) is None
    assert beleg_pdf.render_quote_preview(_uuid.uuid4()) is None


@pytest.mark.django_db
def test_vorschau_entwurf_archiviert_nichts(app_user):
    inv = _published_invoice(app_user, publish=False)
    beleg_pdf.render_invoice_preview(inv.id)
    assert beleg_pdf.archived_key_for("invoice_id", inv.id) is None


# --- Giro-Code (EPC-QR) ------------------------------------------------------

@pytest.mark.django_db
def test_girocode_bei_gepflegter_iban(app_user):
    firma_service.update_company_profile(
        app_user.id, company_name="QR GmbH",
        iban="DE89370400440532013000", bic="COBADEFFXXX",
        bank_name="Musterbank",
    )
    inv = _published_invoice(app_user)
    text = _pdf_text(beleg_pdf.render_invoice_pdf(inv.id))
    assert "Giro-Code" in text


@pytest.mark.django_db
def test_kein_girocode_ohne_iban(app_user):
    firma_service.update_company_profile(app_user.id, company_name="Ohne IBAN GmbH")
    inv = _published_invoice(app_user)
    text = _pdf_text(beleg_pdf.render_invoice_pdf(inv.id))
    assert "Giro-Code" not in text


def test_epc_qr_daten_None_sicher():
    """Ohne IBAN/Name/positiven Betrag gibt es keinen QR (statt eines kaputten)."""
    assert beleg_pdf._epc_qr_png(None, Decimal("10"), "x") is None
    assert beleg_pdf._epc_qr_png({"company_name": "A"}, Decimal("10"), "x") is None
    assert beleg_pdf._epc_qr_png(
        {"company_name": "A", "iban": "DE0"}, Decimal("0"), "x"
    ) is None
    assert beleg_pdf._epc_qr_png(
        {"company_name": "A", "iban": "DE89 3704 0044 0532 0130 00"},
        Decimal("12.34"), "Rechnung X",
    )[:8] == b"\x89PNG\r\n\x1a\n"
