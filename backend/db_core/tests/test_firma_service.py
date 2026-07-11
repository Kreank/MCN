"""Service-Tests für die Firmeneinstellungen (company.*) und die
Mahnstufen-Pflege (invoicing.dunning_level).

Deckt ab: Singleton-Constraint des Firmenprofils, Schutzstandard (DELETE per
Trigger verboten), Niederlassungs-/Gewerk-Pflege inkl. Deaktivieren, sowie die
bewusste Mahnstufen-Lücken-Entscheidung (aktive Stufen = lückenloser Präfix).
"""
import uuid
from hashlib import sha256

import pytest
from django.db import transaction
from django.db.utils import IntegrityError, ProgrammingError

from db_core import storage as storage_module
from db_core.models import Branch, CompanyProfile, DunningLevel, File, Trade
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import firma as firma_service


# Kleinstes gültiges PNG (1x1) bzw. JPEG-Magic für die Logo-Tests.
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 32


class FakeStorage:
    """In-memory-Objektspeicher (dieselbe Schnittstelle wie ObjectStorage)."""

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


# --- Firmenprofil (Singleton) ----------------------------------------------

@pytest.mark.django_db
def test_profile_upsert_legt_einmalig_an_und_aktualisiert(app_user):
    # Erstanlage schreibt die Bankdaten direkt (kein Bestand, den es zu schützen gäbe).
    p1, pending = firma_service.update_company_profile(
        app_user.id, company_name="Mitra Sanitär GmbH", city="Musterstadt",
        iban="DE12500105170648489890",
    )
    assert p1.company_name == "Mitra Sanitär GmbH"
    assert pending is None
    assert p1.iban == "DE12500105170648489890"
    # Zweiter Aufruf aktualisiert dieselbe (einzige) Zeile, legt keine neue an.
    p2, _ = firma_service.update_company_profile(app_user.id, city="Neustadt")
    assert CompanyProfile.objects.count() == 1
    assert p2.id == p1.id
    assert p2.city == "Neustadt"
    assert p2.company_name == "Mitra Sanitär GmbH"  # unverändert


@pytest.mark.django_db
def test_profile_anlegen_ohne_namen_scheitert(app_user):
    with pytest.raises(ValueError, match="Firmenname"):
        firma_service.update_company_profile(app_user.id, city="Musterstadt")


@pytest.mark.django_db
def test_profile_leeres_land_faellt_auf_default(app_user):
    """Ein geleertes NOT-NULL-Feld (country) darf nie NULL werden (kein 500)."""
    p, _ = firma_service.update_company_profile(
        app_user.id, company_name="Ohne Land GmbH", country="", default_language=""
    )
    assert p.country == "DE"  # DB-Default statt NULL
    assert p.default_language == "de"


@pytest.mark.django_db
def test_profile_ungueltiges_land_422(app_user):
    firma_service.update_company_profile(app_user.id, company_name="X GmbH")
    with pytest.raises(ValueError, match="ISO-Kürzel"):
        firma_service.update_company_profile(app_user.id, country="Deutschland")


@pytest.mark.django_db
def test_profile_singleton_zweite_zeile_verboten(app_user):
    firma_service.update_company_profile(app_user.id, company_name="Erste GmbH")
    # Direkter Insert einer zweiten Zeile scheitert am Singleton-Constraint.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CompanyProfile.objects.create(
                id=uuid.uuid4(), is_singleton=True, company_name="Zweite GmbH"
            )


@pytest.mark.django_db
def test_profile_delete_verboten(app_user):
    firma_service.update_company_profile(app_user.id, company_name="Bleibt GmbH")
    with pytest.raises(ProgrammingError, match="append-only"):
        with transaction.atomic():
            CompanyProfile.objects.all().delete()


# --- Firmenlogo -------------------------------------------------------------

@pytest.mark.django_db
def test_logo_hochladen_setzt_logo_file_id_und_speichert(app_user, fake_storage):
    firma_service.update_company_profile(app_user.id, company_name="Logo GmbH")
    profile = firma_service.set_company_logo(
        app_user.id, dateiname="logo.png", inhalt=PNG_1x1
    )
    assert profile.logo_file_id is not None
    # content.file trägt Prüfsumme/Größe/MIME der abgelegten Bytes.
    datei = File.objects.get(id=profile.logo_file_id)
    assert datei.mime_type == "image/png"
    assert datei.size_bytes == len(PNG_1x1)
    assert datei.sha256 == sha256(PNG_1x1).hexdigest()
    # und genau diese Bytes liegen im Objektspeicher.
    assert fake_storage.objects[datei.storage_key] == PNG_1x1


@pytest.mark.django_db
def test_logo_jpeg_erlaubt(app_user, fake_storage):
    firma_service.update_company_profile(app_user.id, company_name="JPEG GmbH")
    profile = firma_service.set_company_logo(
        app_user.id, dateiname="logo.jpg", inhalt=JPEG_MAGIC
    )
    assert File.objects.get(id=profile.logo_file_id).mime_type == "image/jpeg"


@pytest.mark.django_db
def test_logo_ungueltiger_typ_422(app_user, fake_storage):
    """SVG/PDF/andere Formate werden über die Magic Bytes abgelehnt."""
    firma_service.update_company_profile(app_user.id, company_name="SVG GmbH")
    with pytest.raises(firma_service.LogoFehler, match="PNG- oder JPEG"):
        firma_service.set_company_logo(
            app_user.id, dateiname="logo.svg", inhalt=b"<svg>evil</svg>"
        )


@pytest.mark.django_db
def test_logo_zu_gross_422(app_user, fake_storage):
    firma_service.update_company_profile(app_user.id, company_name="Groß GmbH")
    riesig = PNG_1x1 + b"\x00" * firma_service.LOGO_MAX_BYTES
    with pytest.raises(firma_service.LogoFehler, match="zu groß"):
        firma_service.set_company_logo(
            app_user.id, dateiname="logo.png", inhalt=riesig
        )


@pytest.mark.django_db
def test_logo_ohne_profil_422(app_user, fake_storage):
    with pytest.raises(firma_service.LogoFehler, match="kein Firmenprofil"):
        firma_service.set_company_logo(
            app_user.id, dateiname="logo.png", inhalt=PNG_1x1
        )


@pytest.mark.django_db
def test_logo_ersetzen_zeigt_auf_neue_datei(app_user, fake_storage):
    firma_service.update_company_profile(app_user.id, company_name="Ersatz GmbH")
    p1 = firma_service.set_company_logo(app_user.id, dateiname="a.png", inhalt=PNG_1x1)
    erstes = p1.logo_file_id
    p2 = firma_service.set_company_logo(app_user.id, dateiname="b.jpg", inhalt=JPEG_MAGIC)
    assert p2.logo_file_id != erstes
    # Die alte Datei bleibt bestehen (unveränderlich, GoBD).
    assert File.objects.filter(id=erstes).exists()


@pytest.mark.django_db
def test_logo_entfernen_setzt_null(app_user, fake_storage):
    firma_service.update_company_profile(app_user.id, company_name="Weg GmbH")
    p = firma_service.set_company_logo(app_user.id, dateiname="a.png", inhalt=PNG_1x1)
    datei_id = p.logo_file_id
    p2 = firma_service.remove_company_logo(app_user.id)
    assert p2.logo_file_id is None
    # Die Datei selbst bleibt (nur die Referenz ist weg).
    assert File.objects.filter(id=datei_id).exists()
    # Idempotent: erneutes Entfernen ist unschädlich.
    assert firma_service.remove_company_logo(app_user.id).logo_file_id is None


@pytest.mark.django_db
def test_logo_inhalt_liefert_bytes(app_user, fake_storage):
    firma_service.update_company_profile(app_user.id, company_name="Abruf GmbH")
    firma_service.set_company_logo(app_user.id, dateiname="a.png", inhalt=PNG_1x1)
    datei, inhalt = firma_service.company_logo_inhalt()
    assert inhalt == PNG_1x1
    assert datei.mime_type == "image/png"


@pytest.mark.django_db
def test_logo_inhalt_ohne_logo_fehler(app_user, fake_storage):
    firma_service.update_company_profile(app_user.id, company_name="Leer GmbH")
    with pytest.raises(firma_service.LogoFehler, match="kein Firmenlogo"):
        firma_service.company_logo_inhalt()


@pytest.mark.django_db
def test_logo_dedup_bei_gleichem_inhalt(app_user, fake_storage):
    """Derselbe Inhalt (SHA-256) wird nicht doppelt abgelegt."""
    firma_service.update_company_profile(app_user.id, company_name="Dedup GmbH")
    p1 = firma_service.set_company_logo(app_user.id, dateiname="a.png", inhalt=PNG_1x1)
    firma_service.remove_company_logo(app_user.id)
    p2 = firma_service.set_company_logo(app_user.id, dateiname="a2.png", inhalt=PNG_1x1)
    # Gleicher Inhalt → gleiche content.file, nur ein Objekt im Speicher.
    assert p2.logo_file_id == p1.logo_file_id
    assert len(fake_storage.objects) == 1


# --- Niederlassungen --------------------------------------------------------

@pytest.mark.django_db
def test_branch_anlegen_und_deaktivieren(app_user):
    b = firma_service.create_branch(app_user.id, name="Nord", city="Hamburg")
    assert b.active is True
    b2 = firma_service.update_branch(app_user.id, branch_id=b.id, active=False)
    assert b2.active is False
    # Deaktivierte tauchen ohne include_inactive nicht mehr auf.
    aktive = list(firma_service.list_branches(include_inactive=False))
    assert b.id not in [x.id for x in aktive]


@pytest.mark.django_db
def test_branch_leeres_land_default(app_user):
    b = firma_service.create_branch(app_user.id, name="West", country="")
    assert b.country == "DE"


@pytest.mark.django_db
def test_branch_delete_verboten(app_user):
    b = firma_service.create_branch(app_user.id, name="Süd")
    with pytest.raises(ProgrammingError, match="append-only"):
        with transaction.atomic():
            Branch.objects.filter(id=b.id).delete()


# --- Gewerk-Katalog ---------------------------------------------------------

@pytest.mark.django_db
def test_trade_katalog_geseedet():
    # Migration 0023 seedet die branchenüblichen Gewerke.
    assert Trade.objects.filter(code="SHK").exists()
    assert Trade.objects.filter(code="ELEKTRO").exists()


@pytest.mark.django_db
def test_trade_code_eindeutig(app_user):
    firma_service.create_trade(app_user.id, code="SONDER", label="Sonderbau")
    with pytest.raises(ValueError, match="bereits vergeben"):
        firma_service.create_trade(app_user.id, code="SONDER", label="Doppelt")


@pytest.mark.django_db
def test_trade_delete_verboten(app_user):
    t = Trade.objects.filter(code="SHK").first()
    with pytest.raises(ProgrammingError, match="append-only"):
        with transaction.atomic():
            Trade.objects.filter(id=t.id).delete()


# --- Mahnstufen -------------------------------------------------------------

@pytest.mark.django_db
def test_mahnstufen_sechs_stufen_geseedet():
    levels = list(firma_service.list_dunning_levels())
    assert [lv.level for lv in levels] == [1, 2, 3, 4, 5, 6]
    assert all(lv.active for lv in levels)
    # fee/interest_note bleiben NULL (STB-Vorbehalt B-22) für die neuen Stufen.
    for lv in levels:
        if lv.level >= 4:
            assert lv.fee is None


@pytest.mark.django_db
def test_mahnstufe_label_und_frist_pflegbar(app_user):
    lv = firma_service.update_dunning_level(
        app_user.id, level=2, label="Freundliche Erinnerung", days_after_due=10
    )
    assert lv.label == "Freundliche Erinnerung"
    assert lv.days_after_due == 10


@pytest.mark.django_db
def test_mahnstufe_gebuehr_bleibt_unangetastet(app_user):
    """update_dunning_level rührt fee nie an (STB-Vorbehalt B-22)."""
    vorher = DunningLevel.objects.get(level=6).fee
    firma_service.update_dunning_level(app_user.id, level=6, label="Letzte Mahnung")
    assert DunningLevel.objects.get(level=6).fee == vorher  # weiterhin NULL


@pytest.mark.django_db
def test_mahnstufe_hoechste_deaktivieren_erlaubt(app_user):
    lv = firma_service.update_dunning_level(app_user.id, level=6, active=False)
    assert lv.active is False
    # Präfix {1..5} weiterhin lückenlos aktiv.
    aktive = [l.level for l in firma_service.list_dunning_levels() if l.active]
    assert aktive == [1, 2, 3, 4, 5]


@pytest.mark.django_db
def test_mahnstufe_mittlere_deaktivieren_verboten(app_user):
    """Lücken-Entscheidung: eine mittlere Stufe zu deaktivieren, während eine
    höhere aktiv bleibt, ist verboten (sonst wäre die Eskalation nicht
    ausführbar)."""
    with pytest.raises(ValueError, match="lückenlos ab Stufe 1"):
        firma_service.update_dunning_level(app_user.id, level=3, active=False)
    # Nichts wurde verändert.
    assert DunningLevel.objects.get(level=3).active is True


@pytest.mark.django_db
def test_mahnstufe_reaktivieren_in_reihenfolge(app_user):
    # Erst die höchste deaktivieren, dann die nächsthöchste — immer Präfix.
    firma_service.update_dunning_level(app_user.id, level=6, active=False)
    firma_service.update_dunning_level(app_user.id, level=5, active=False)
    aktive = [l.level for l in firma_service.list_dunning_levels() if l.active]
    assert aktive == [1, 2, 3, 4]
    # Wieder aktivieren (Präfix bleibt gewahrt).
    firma_service.update_dunning_level(app_user.id, level=5, active=True)
    aktive = [l.level for l in firma_service.list_dunning_levels() if l.active]
    assert aktive == [1, 2, 3, 4, 5]


@pytest.mark.django_db
def test_deaktivierte_stufe_nicht_ausstellbar(app_user):
    """issue_dunning_notice lehnt eine deaktivierte Stufe ab (ohne dass die
    Rechnungsvorbedingungen überhaupt geprüft werden müssen)."""
    firma_service.update_dunning_level(app_user.id, level=6, active=False)
    with pytest.raises(ValueError, match="deaktiviert"):
        buchhaltung_service.issue_dunning_notice(
            app_user.id, invoice_id=uuid.uuid4(), level=6, issued_at="2026-01-01"
        )
