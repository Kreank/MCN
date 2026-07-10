"""Service-Tests für die Firmeneinstellungen (company.*) und die
Mahnstufen-Pflege (invoicing.dunning_level).

Deckt ab: Singleton-Constraint des Firmenprofils, Schutzstandard (DELETE per
Trigger verboten), Niederlassungs-/Gewerk-Pflege inkl. Deaktivieren, sowie die
bewusste Mahnstufen-Lücken-Entscheidung (aktive Stufen = lückenloser Präfix).
"""
import uuid

import pytest
from django.db import transaction
from django.db.utils import IntegrityError, ProgrammingError

from db_core.models import Branch, CompanyProfile, DunningLevel, Trade
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import firma as firma_service


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
