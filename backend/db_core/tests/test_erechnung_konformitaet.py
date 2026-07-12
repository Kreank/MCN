"""Externe Konformitätsprüfung der E-Rechnung (veraPDF + Mustang/ZUGFeRD).

Die übrigen E-Rechnungs-Tests prüfen unsere eigene Logik (Mapping, Summen, XSD).
Dieser Test prüft das ERGEBNIS mit **fremden Werkzeugen** — den Referenz-
Validatoren, die auch ein Rechnungsempfänger einsetzt:

* **veraPDF** (verapdf.org, Referenzimplementierung der PDF Association):
  PDF/A-3B-Konformität des Hybrid-PDF.
* **Mustang** (github.com/ZUGFeRD/mustangproject, ``--action validate``):
  ZUGFeRD/Factur-X-Konformität — XSD **und** EN16931-Schematron (BR-*/BR-CO-*),
  dazu ein eigener PDF/A-Lauf über die veraPDF-Bibliothek.

Beides braucht eine JRE und die Validator-Artefakte; die liegen bewusst NICHT im
Repo. Fehlt eines davon, wird sauber übersprungen (Muster: test_storage_minio_e2e).
Installation und Aufruf: ``docs/erechnung-validierung.md``.

    MCN_VERAPDF=…/verapdf.bat  MCN_MUSTANG_JAR=…/Mustang-CLI-2.24.0.jar

Geprüft werden die sechs fachlich verschiedenen Belegformen (Skonto, kein Skonto,
zwei Steuersätze, Schlussrechnung mit negativen Anrechnungspositionen, Kreditbeleg
mit negativen Summen, Logo mit Alphakanal) — die Fälle also, an denen die
Konformität realistisch bricht.
"""
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zlib
from datetime import date
from decimal import Decimal
from struct import pack

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf
from db_core.services import erechnung as erechnung_service
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

JAVA = shutil.which("java")
VERAPDF = os.environ.get("MCN_VERAPDF")
MUSTANG_JAR = os.environ.get("MCN_MUSTANG_JAR")

_verapdf_ok = bool(VERAPDF) and os.path.exists(VERAPDF)
_mustang_ok = bool(JAVA) and bool(MUSTANG_JAR) and os.path.exists(MUSTANG_JAR or "")

verapdf_only = pytest.mark.skipif(
    not _verapdf_ok,
    reason="veraPDF nicht installiert (MCN_VERAPDF; siehe docs/erechnung-validierung.md)",
)
mustang_only = pytest.mark.skipif(
    not _mustang_ok,
    reason="Mustang/JRE nicht verfügbar (MCN_MUSTANG_JAR; siehe docs/erechnung-validierung.md)",
)

_TIMEOUT = 300


# --- Belegformen ------------------------------------------------------------

def _kunde(app_user):
    org = identity_service.create_organization(
        app_user.id, legal_name="Hausverwaltung Nord GmbH",
        organization_type="PROPERTY_MANAGEMENT", vat_id="DE987654321",
    )
    identity_service.add_address(
        app_user.id, org.id, address_type="BILLING",
        street="Elbchaussee", house_number="5",
        postal_code="22765", city="Hamburg", valid_from=date(2020, 1, 1),
    )
    return org


def _auftrag(app_user, obj, kunde, *, bis="KAUFMAENNISCH_GEPRUEFT"):
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
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
        if to == bis:
            break
    return order


def _beteiligte(app_user, invoice, kunde):
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=invoice.id, party_id=kunde.id,
            role=role, is_primary=True,
        )


def _publish(app_user, invoice):
    beleg_service.publish_invoice(app_user.id, invoice_id=invoice.id)
    invoice.refresh_from_db()
    return invoice


def _rechnung(app_user, obj, kunde, order, *, lines, typ="RECHNUNG", **kw):
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type=typ,
        work_order_id=order.id, invoice_date=date(2026, 7, 1),
        lines=lines, **kw,
    )
    _beteiligte(app_user, inv, kunde)
    return _publish(app_user, inv)


def _png_mit_alpha(breite=120, hoehe=48):
    """Ein echtes PNG mit Alphakanal (RGBA) — ohne Pillow, von Hand gebaut.

    Der Alphakanal ist der Grund für diesen Fall: fpdf2 legt daraus eine SMask an,
    und Transparenz ist die klassische PDF/A-Falle.
    """
    raw = bytearray()
    for y in range(hoehe):
        raw.append(0)  # Filter „None" je Zeile
        for x in range(breite):
            alpha = 0 if (x + y) % 7 == 0 else 255  # echte Transparenzlöcher
            raw += bytes((0x1C, 0x32, 0x44, alpha))

    def chunk(typ, daten):
        c = typ + daten
        return pack(">I", len(daten)) + c + pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = pack(">IIBBBBB", breite, hoehe, 8, 6, 0, 0, 0)  # 8 bit, RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def firmenprofil(app_user):
    profile, _ = firma_service.update_company_profile(
        app_user.id,
        company_name="Mitra Sanitär GmbH",
        street="Industriestraße 5",
        postal_code="12345",
        city="Musterstadt",
        tax_number="12/345/67890",
        vat_id="DE123456789",
        iban="DE02120300000000202051",
        bic="BYLADEM1001",
        bank_name="Musterbank",
        # Telefon/E-Mail werden bewusst gepflegt: nur dann entsteht BG-6
        # (DefinedTradeContact, BT-41/42/43) — sonst bliebe der Pfad ungeprüft.
        phone="+49 30 1234567",
        email="rechnung@mitra-sanitaer.example",
    )
    return profile


@pytest.fixture
def belege(app_user, firmenprofil, fake_storage_for_logo):
    """Die sechs Belegformen, an denen die Konformität realistisch bricht.

    ``fake_storage_for_logo`` MUSS hier hängen (nicht erst an ``pdfs``): das Logo
    wird beim Anlegen abgelegt und beim Rendern gelesen — beides muss denselben
    Speicher sehen.
    """
    kunde = _kunde(app_user)
    obj = property_service.create_property(
        app_user.id, name="Wohnanlage Nord", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    formen = {}

    order = _auftrag(app_user, obj, kunde)
    formen["mit_skonto"] = _rechnung(
        app_user, obj, kunde, order,
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
        due_date=date(2026, 7, 31), discount_days=10, discount_percent="2.00",
    )

    order2 = _auftrag(app_user, obj, kunde)
    formen["ohne_skonto"] = _rechnung(
        app_user, obj, kunde, order2,
        lines=[{"line_type": "ARBEITSZEIT", "description": "Montage", "quantity": "7.5",
                "unit": "Std", "unit_price": "58.00", "tax_code": "DE_19"}],
        due_date=date(2026, 7, 31),
    )

    order3 = _auftrag(app_user, obj, kunde)
    formen["zwei_steuersaetze"] = _rechnung(
        app_user, obj, kunde, order3,
        lines=[
            {"line_type": "MATERIAL", "description": "Rohr (19 %)", "quantity": 12,
             "unit": "m", "unit_price": "13.37", "tax_code": "DE_19"},
            {"line_type": "MATERIAL", "description": "Broschüre (7 %)", "quantity": 3,
             "unit": "Stk", "unit_price": "9.90", "tax_code": "DE_7"},
        ],
        due_date=date(2026, 8, 15),
    )

    # Schlussrechnung mit Anrechnung eines veröffentlichten Abschlags →
    # NEGATIVE Anrechnungsposition (Vorzeichen auf der Menge im XML).
    order4 = _auftrag(app_user, obj, kunde, bis="IN_AUSFUEHRUNG")
    abschlag = _rechnung(
        app_user, obj, kunde, order4,
        typ="ABSCHLAGSRECHNUNG",
        lines=[{"line_type": "PAUSCHALE", "description": "1. Abschlag", "quantity": 1,
                "unit_price": "1000.00", "tax_code": "DE_19"}],
    )
    for to in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order4.id, to_status=to
        )
    formen["schlussrechnung"] = _rechnung(
        app_user, obj, kunde, order4,
        typ="SCHLUSSRECHNUNG",
        lines=[{"line_type": "PAUSCHALE", "description": "Gesamtleistung",
                "quantity": 1, "unit_price": "5000.00", "tax_code": "DE_19"}],
        advance_invoice_ids=[abschlag.id],
    )

    # Kreditbeleg: Vollstorno einer veröffentlichten Rechnung (negative Summen).
    order5 = _auftrag(app_user, obj, kunde)
    ursprung = _rechnung(
        app_user, obj, kunde, order5,
        lines=[{"line_type": "MATERIAL", "description": "Fehllieferung", "quantity": 4,
                "unit": "Stk", "unit_price": "125.00", "tax_code": "DE_19"}],
    )
    formen["storno"] = beleg_service.create_cancellation(
        app_user.id, invoice_id=ursprung.id
    )

    # Logo mit Alphakanal (SMask) — erst ab hier trägt jedes weitere PDF das Logo,
    # deshalb steht dieser Beleg am Ende.
    firma_service.set_company_logo(
        app_user.id, dateiname="logo.png", inhalt=_png_mit_alpha()
    )
    order6 = _auftrag(app_user, obj, kunde)
    formen["mit_logo"] = _rechnung(
        app_user, obj, kunde, order6,
        lines=[{"line_type": "FREMDLEISTUNG", "description": "Beratung", "quantity": 2,
                "unit": "Std", "unit_price": "95.00", "tax_code": "DE_19"}],
        due_date=date(2026, 7, 20),
    )
    return formen


@pytest.fixture
def pdfs(belege, tmp_path):
    """Jede Belegform als Hybrid-PDF auf der Platte (Pfad je Form)."""
    # MCN_ERECHNUNG_DUMP=<dir> legt die Belege zusätzlich dauerhaft ab — für den
    # manuellen Lauf der Validatoren (siehe docs/erechnung-validierung.md).
    dump = os.environ.get("MCN_ERECHNUNG_DUMP")
    out = {}
    for name, inv in belege.items():
        geladen = beleg_pdf.load_invoice_for_render(inv.id)
        pdf = erechnung_service.render_zugferd_pdf(geladen)
        ziel = tmp_path / f"{name}.pdf"
        ziel.write_bytes(pdf)
        out[name] = ziel
        if dump:
            os.makedirs(dump, exist_ok=True)
            with open(os.path.join(dump, f"{name}.pdf"), "wb") as fh:
                fh.write(pdf)
            with open(os.path.join(dump, f"{name}.xml"), "wb") as fh:
                fh.write(erechnung_service.build_cii_xml(geladen))
    return out


@pytest.fixture
def fake_storage_for_logo(monkeypatch):
    """Objektspeicher im Speicher — das Logo muss ohne MinIO abrufbar sein."""
    from hashlib import sha256

    from db_core import storage as storage_module

    class FakeStorage:
        def __init__(self):
            self.objects = {}

        def put_object(self, key, data, content_type="application/octet-stream"):
            self.objects[key] = bytes(data)
            return storage_module.ObjectInfo(
                storage_key=key, sha256=sha256(data).hexdigest(), size_bytes=len(data)
            )

        def get_object(self, key):
            if key not in self.objects:
                raise storage_module.StorageError(f"unbekannt {key}")
            return self.objects[key]

        def remove_object(self, key):
            self.objects.pop(key, None)

    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    return fake


# --- Validator-Aufrufe ------------------------------------------------------

def _verapdf(pdf_pfad):
    """(ok, meldungen) — veraPDF gegen Flavour 3b."""
    proc = subprocess.run(
        [VERAPDF, "--flavour", "3b", "--format", "xml", str(pdf_pfad)],
        capture_output=True, text=True, timeout=_TIMEOUT,
    )
    baum = ET.fromstring(proc.stdout)
    ergebnis = baum.find(".//validationReport")
    if ergebnis is None:
        return False, [f"veraPDF lieferte keinen Bericht: {proc.stdout[:400]}"]
    ok = ergebnis.get("isCompliant") == "true"
    verstoesse = []
    for rule in ergebnis.iter("rule"):
        if rule.get("status") == "failed":
            spec = f"{rule.get('specification')} {rule.get('clause')}-{rule.get('testNumber')}"
            beschreibung = (rule.findtext("description") or "").strip()
            verstoesse.append(f"{spec}: {beschreibung}")
    return ok, verstoesse


def _mustang(pdf_pfad):
    """(ok, meldungen) — Mustang: XSD + EN16931-Schematron (+ eigener PDF/A-Lauf).

    Der Bericht trägt DREI ``<summary>``-Elemente: eines unter ``<pdf>``, eines
    unter ``<xml>`` und das Gesamturteil als **direktes Kind** von
    ``<validation>``. Ausgewertet wird ausdrücklich nur das Gesamturteil — ein
    ``.//summary`` fände das PDF-Summary zuerst und meldete „valid", während das
    XML durchgefallen wäre.

    ``<notice>`` ist KEIN Verstoß: Mustang feuert zusätzlich die
    XRechnung/PEPPOL-CIUS-Regeln (BR-DE-*, PEPPOL-EN16931-R*) und stuft sie für
    ein EN16931-Dokument als Hinweis ein. Sie werden mitgeliefert (der Aufrufer
    sieht sie im Fehlerfall), aber nur ``<error>`` entscheidet.
    """
    proc = subprocess.run(
        [JAVA, "-jar", MUSTANG_JAR, "--action", "validate", "--source", str(pdf_pfad)],
        capture_output=True, text=True, timeout=_TIMEOUT,
    )
    start = proc.stdout.find("<validation")
    if start < 0:
        return False, [f"Mustang lieferte keinen Bericht: {proc.stdout[-800:]}"]
    baum = ET.fromstring(proc.stdout[start:])
    gesamt = baum.find("summary")  # direktes Kind = Gesamturteil
    ok = gesamt is not None and gesamt.get("status") == "valid"
    fehler = [
        f"[{teil}] {(m.text or '').strip()}"
        for teil in ("pdf", "xml", ".")
        for m in baum.iterfind(f"{teil}/messages/error")
    ]
    return ok, fehler or [f"kein <error> im Bericht:\n{proc.stdout[start:]}"]


@pytest.mark.django_db
@verapdf_only
@pytest.mark.parametrize(
    "form",
    ["mit_skonto", "ohne_skonto", "zwei_steuersaetze", "schlussrechnung",
     "storno", "mit_logo"],
)
def test_pdfa_3b_konform(pdfs, form):
    """Jede Belegform ist laut veraPDF PDF/A-3B-konform."""
    ok, verstoesse = _verapdf(pdfs[form])
    assert ok, "veraPDF-Verstöße (" + form + "):\n" + "\n".join(verstoesse)


@pytest.mark.django_db
@mustang_only
@pytest.mark.parametrize(
    "form",
    ["mit_skonto", "ohne_skonto", "zwei_steuersaetze", "schlussrechnung",
     "storno", "mit_logo"],
)
def test_en16931_schematron_konform(pdfs, form):
    """Jede Belegform besteht die EN16931-Schematron-Regeln (BR-*/BR-CO-*)."""
    ok, meldungen = _mustang(pdfs[form])
    assert ok, "Mustang-Verstöße (" + form + "):\n" + "\n".join(meldungen)


@pytest.mark.django_db
def test_skonto_konvention_endet_mit_umbruch(belege):
    """BR-DE-18: hinter dem letzten ``#…#``-Block MUSS ein Zeilenumbruch stehen.

    Läuft ohne Validator — der Befund kam aus Mustang (die Zeile ohne ``\\n`` wurde
    verworfen), und diese eine Zusicherung hält ihn fest, damit die Regression
    auch auf einem Rechner ohne Java auffliegt.
    """
    xml = erechnung_service.build_cii_xml(
        beleg_pdf.load_invoice_for_render(belege["mit_skonto"].id)
    ).decode()
    assert "#SKONTO#TAGE=10#PROZENT=2.00#BASISBETRAG=285.60#\n" in xml


# Ein Ankerwert, der ohne Validator läuft: die Steuergruppen müssen cent-genau zu
# den Kopfsummen passen — die Bedingung hinter BR-CO-13/15.
@pytest.mark.django_db
def test_summen_cent_genau(belege):
    for name, inv in belege.items():
        assert Decimal(inv.net_total) + Decimal(inv.tax_total) == Decimal(
            inv.gross_total
        ), name
