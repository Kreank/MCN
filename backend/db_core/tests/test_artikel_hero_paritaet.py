"""Service-Tests zur Hero-Parität des Artikelstamms (Migration 0042).

Deckt ab: price_unit-Umrechnung in beide Richtungen, VK-Gruppen-Übersicht,
Genau-eine-Standard-Regel, cost_center-/tax_code-Validierung, Lieferantenbezug,
sowie die file_link-Invarianten (Artikelbild-Einmaligkeit, „genau ein Ziel").
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from db_core.models import (
    Article,
    ArticleSalePrice,
    ArticleSupplierReference,
    CostCenter,
    File,
    FileLink,
    SalePriceGroup,
)
from db_core.services import artikel as artikel_service
from db_core.services import identity as identity_service
from db_core.services import kalkulation as kalkulation_service


# --- Helfer ----------------------------------------------------------------

def _article(app_user, *, number=None, list_price=None, price_unit=None):
    return artikel_service.create_article(
        app_user.id,
        article_number=number or f"A-{uuid.uuid4().hex[:8]}",
        description="Artikel", unit="Stk", list_price=list_price,
        price_unit=price_unit,
    )


def _cost_center(app_user, *, active=True):
    return CostCenter.objects.create(
        id=uuid.uuid4(), code=f"KST-{uuid.uuid4().hex[:6]}", label="Prüfstelle",
        active=active, created_by_id=app_user.id, version=1,
    )


def _group(app_user, *, basis="LISTENPREIS", op="AUFSCHLAG", pct=None, amt=None):
    return artikel_service.create_sale_price_group(
        app_user.id, name=f"G-{uuid.uuid4().hex[:6]}", calc_basis=basis,
        operator=op, percent_change=pct, amount_change=amt,
    )


def _supplier(app_user):
    return identity_service.create_organization(
        app_user.id, legal_name=f"Lief {uuid.uuid4().hex[:6]}",
        organization_type="COMPANY",
    )


# --- price_unit: Umrechnung je Stück (Hinrichtung) -------------------------

@pytest.mark.django_db
def test_price_unit_teilt_listenpreis_basis(app_user):
    # Listenpreis 250 für Preiseinheit 100 -> je Stück 2,50; +0 % = 2,50.
    art = _article(app_user, list_price=Decimal("250.00"), price_unit=100)
    grp = _group(app_user, basis="LISTENPREIS", pct=Decimal("0.000"))
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True
    )
    v = kalkulation_service.article_kalkulation(art.id)["variants"][0]
    assert Decimal(v["basis_amount"]) == Decimal("2.50")
    assert v["sale_price"] == "2.50"


@pytest.mark.django_db
def test_price_unit_teilt_ek_basis(app_user):
    # EK 200 für Preiseinheit 100 -> je Stück 2,00; +50 % = 3,00.
    art = _article(app_user, price_unit=100)
    supplier = _supplier(app_user)
    ArticleSupplierReference.objects.create(
        id=uuid.uuid4(), article_id=art.id, supplier_party_id=supplier.id,
        source_system="MANUELL", source_namespace="test",
        supplier_article_number=uuid.uuid4().hex[:8],
        last_purchase_price=Decimal("200.00"), currency="EUR",
        valid_from=date(2026, 1, 1),
    )
    grp = _group(app_user, basis="EK", pct=Decimal("50.000"))
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True
    )
    v = kalkulation_service.article_kalkulation(art.id)["variants"][0]
    assert Decimal(v["basis_amount"]) == Decimal("2.00")
    assert v["sale_price"] == "3.00"


@pytest.mark.django_db
def test_price_unit_default_1_unveraendert(app_user):
    # Ohne gesetzte Preiseinheit (Default 1) bleibt die Basis unverändert.
    art = _article(app_user, list_price=Decimal("100.00"))
    assert art.price_unit == 1
    grp = _group(app_user, basis="LISTENPREIS", pct=Decimal("30.000"))
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True
    )
    assert kalkulation_service.article_kalkulation(art.id)["variants"][0]["sale_price"] == "130.00"


# --- price_unit: Gegenrichtung (Beleg -> Stamm) ----------------------------

@pytest.mark.django_db
def test_price_unit_festpreis_uebernahme_ohne_umrechnung(app_user):
    """positionswerte_in_stammdaten rechnet NICHT um: der Belegpreis je Stück
    landet unverändert als fixed_price (je Stück), auch bei price_unit != 1."""
    art = _article(app_user, list_price=Decimal("250.00"), price_unit=100)
    artikel_service.positionswerte_in_stammdaten(
        app_user.id, article_id=art.id, verkaufspreis=Decimal("5.00"),
    )
    asp = ArticleSalePrice.objects.get(article_id=art.id, is_standard=True)
    assert asp.fixed_price == Decimal("5.00")   # NICHT 500 und NICHT 0.05
    v = [x for x in kalkulation_service.article_kalkulation(art.id)["variants"]
         if x["kind"] == "FESTPREIS"][0]
    assert v["sale_price"] == "5.00"


# --- VK-Gruppen-Übersicht --------------------------------------------------

@pytest.mark.django_db
def test_verkaufspreise_uebersicht_berechnet_und_ueberschreibt(app_user):
    art = _article(app_user, list_price=Decimal("100.00"))
    grp = _group(app_user, basis="LISTENPREIS", pct=Decimal("30.000"))
    # Erst nur die Formel -> errechnet 130, keine Überschreibung.
    artikel_service.set_verkaufspreise(
        app_user.id, article_id=art.id,
        entries=[{"sale_price_group_id": grp.id, "fixed_price": None, "is_standard": True}],
    )
    data = kalkulation_service.verkaufspreise_uebersicht(art.id)
    row = data["groups"][0]
    assert row["computed_sale_price"] == "130.00"
    assert row["override_price"] is None
    assert row["effective_sale_price"] == "130.00"
    assert row["is_standard"] is True

    # Jetzt überschreiben -> effektiv 140, errechnet bleibt 130.
    artikel_service.set_verkaufspreise(
        app_user.id, article_id=art.id,
        entries=[{"sale_price_group_id": grp.id, "fixed_price": Decimal("140.00"),
                  "is_standard": True}],
    )
    row = kalkulation_service.verkaufspreise_uebersicht(art.id)["groups"][0]
    assert row["computed_sale_price"] == "130.00"
    assert row["override_price"] == "140.00"
    assert row["effective_sale_price"] == "140.00"

    # Der ALTE Kalkulations-Endpunkt darf demselben Artikel keinen anderen VK
    # ausweisen: Überschreibung gewinnt gegen den Formelwert (sonst widersprechen
    # sich zwei GET-Endpunkte beim effektiven VK).
    variante = next(
        v for v in kalkulation_service.article_kalkulation(art.id)["variants"]
        if v["kind"] == "FORMEL"
    )
    assert variante["sale_price"] == "140.00"
    assert variante["basis_amount"] == "100.0000"  # Formelbasis bleibt sichtbar


# --- Genau-eine-Standard-Regel ---------------------------------------------

@pytest.mark.django_db
def test_verkaufspreise_genau_eine_standard(app_user):
    art = _article(app_user, list_price=Decimal("100.00"))
    g1 = _group(app_user, pct=Decimal("10.000"))
    g2 = _group(app_user, pct=Decimal("20.000"))
    # keine Standard-Markierung -> Fehler
    with pytest.raises(ValueError):
        artikel_service.set_verkaufspreise(
            app_user.id, article_id=art.id,
            entries=[{"sale_price_group_id": g1.id, "is_standard": False},
                     {"sale_price_group_id": g2.id, "is_standard": False}],
        )
    # zwei Standard -> Fehler
    with pytest.raises(ValueError):
        artikel_service.set_verkaufspreise(
            app_user.id, article_id=art.id,
            entries=[{"sale_price_group_id": g1.id, "is_standard": True},
                     {"sale_price_group_id": g2.id, "is_standard": True}],
        )
    # genau eine -> OK, und DB hat genau eine Standard-Zeile
    artikel_service.set_verkaufspreise(
        app_user.id, article_id=art.id,
        entries=[{"sale_price_group_id": g1.id, "is_standard": True},
                 {"sale_price_group_id": g2.id, "is_standard": False}],
    )
    standards = ArticleSalePrice.objects.filter(article_id=art.id, is_standard=True)
    assert standards.count() == 1
    assert standards.first().sale_price_group_id == g1.id


@pytest.mark.django_db
def test_verkaufspreise_gruppe_doppelt_abgelehnt(app_user):
    art = _article(app_user, list_price=Decimal("100.00"))
    g1 = _group(app_user, pct=Decimal("10.000"))
    with pytest.raises(ValueError):
        artikel_service.set_verkaufspreise(
            app_user.id, article_id=art.id,
            entries=[{"sale_price_group_id": g1.id, "is_standard": True},
                     {"sale_price_group_id": g1.id, "is_standard": False}],
        )


@pytest.mark.django_db
def test_verkaufspreise_wechsel_des_standards(app_user):
    """Ein zuvor freistehender Festpreis-Standard verliert den Standard an die
    Tabelle (partieller Unique-Index bleibt konsistent)."""
    art = _article(app_user, list_price=Decimal("100.00"))
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, label="Aktion",
        fixed_price=Decimal("90.00"), is_standard=True,
    )
    g1 = _group(app_user, pct=Decimal("10.000"))
    artikel_service.set_verkaufspreise(
        app_user.id, article_id=art.id,
        entries=[{"sale_price_group_id": g1.id, "is_standard": True}],
    )
    assert ArticleSalePrice.objects.filter(article_id=art.id, is_standard=True).count() == 1
    # Der Festpreis existiert weiter, nur nicht mehr als Standard.
    assert ArticleSalePrice.objects.filter(
        article_id=art.id, label="Aktion", is_standard=False
    ).exists()


# --- cost_center-Validierung -----------------------------------------------

@pytest.mark.django_db
def test_cost_center_unbekannt(app_user):
    with pytest.raises(ValueError):
        _article_mit(app_user, cost_center_id=uuid.uuid4())


@pytest.mark.django_db
def test_cost_center_archiviert(app_user):
    cc = _cost_center(app_user, active=False)
    with pytest.raises(ValueError):
        _article_mit(app_user, cost_center_id=cc.id)


@pytest.mark.django_db
def test_cost_center_aktiv_ok(app_user):
    cc = _cost_center(app_user, active=True)
    art = _article_mit(app_user, cost_center_id=cc.id)
    assert art.cost_center_id == cc.id


def _article_mit(app_user, **kw):
    return artikel_service.create_article(
        app_user.id, article_number=f"A-{uuid.uuid4().hex[:8]}",
        description="x", unit="Stk", **kw,
    )


# --- tax_code-Validierung --------------------------------------------------

@pytest.mark.django_db
def test_tax_code_unbekannt(app_user):
    with pytest.raises(ValueError):
        _article_mit(app_user, tax_code="FALSCH")


@pytest.mark.django_db
def test_tax_code_gueltig(app_user):
    art = _article_mit(app_user, tax_code="DE_19")
    assert art.tax_code_id == "DE_19"


@pytest.mark.django_db
def test_price_unit_ungueltig(app_user):
    with pytest.raises(ValueError):
        _article_mit(app_user, price_unit=7)


@pytest.mark.django_db
def test_min_order_quantity_muss_positiv(app_user):
    with pytest.raises(ValueError):
        _article_mit(app_user, min_order_quantity=Decimal("0"))


# --- Lieferantenbezug ------------------------------------------------------

@pytest.mark.django_db
def test_set_primary_supplier_und_update(app_user):
    art = _article(app_user)
    s1 = _supplier(app_user)
    artikel_service.set_primary_supplier(
        app_user.id, article_id=art.id, supplier_party_id=s1.id,
        supplier_article_number="ART-1", last_purchase_price=Decimal("12.3400"),
    )
    ref = kalkulation_service.primary_supplier_reference(art.id)
    assert ref.supplier_party_id == s1.id
    assert ref.last_purchase_price == Decimal("12.3400")
    # Erneut mit gleichem Lieferanten+Nummer -> nur EK aktualisiert (kein Duplikat).
    artikel_service.set_primary_supplier(
        app_user.id, article_id=art.id, supplier_party_id=s1.id,
        supplier_article_number="ART-1", last_purchase_price=Decimal("9.9900"),
    )
    refs = ArticleSupplierReference.objects.filter(
        article_id=art.id, source_system="MANUELL"
    )
    assert refs.count() == 1
    assert refs.first().last_purchase_price == Decimal("9.9900")


@pytest.mark.django_db
def test_set_primary_supplier_wechsel(app_user):
    art = _article(app_user)
    s1, s2 = _supplier(app_user), _supplier(app_user)
    artikel_service.set_primary_supplier(
        app_user.id, article_id=art.id, supplier_party_id=s1.id,
        supplier_article_number="A", last_purchase_price=Decimal("5.0000"),
    )
    artikel_service.set_primary_supplier(
        app_user.id, article_id=art.id, supplier_party_id=s2.id,
        supplier_article_number="B", last_purchase_price=Decimal("6.0000"),
    )
    # Der zuletzt gesetzte (jüngstes last_imported_at) ist primär.
    assert kalkulation_service.primary_supplier_reference(art.id).supplier_party_id == s2.id


@pytest.mark.django_db
def test_set_primary_supplier_leere_nummer(app_user):
    art = _article(app_user)
    s1 = _supplier(app_user)
    with pytest.raises(ValueError):
        artikel_service.set_primary_supplier(
            app_user.id, article_id=art.id, supplier_party_id=s1.id,
            supplier_article_number="   ",
        )


# --- file_link: Artikelbild-Einmaligkeit + genau ein Ziel ------------------

def _file(app_user):
    return File.objects.create(
        id=uuid.uuid4(), storage_key=f"upload/{uuid.uuid4()}",
        original_filename="bild.png", mime_type="image/png", size_bytes=10,
        sha256="a" * 64, media_metadata={}, uploaded_by_id=app_user.id,
    )


@pytest.mark.django_db
def test_artikelbild_hoechstens_eines(app_user):
    art = _article(app_user)
    FileLink.objects.create(
        id=uuid.uuid4(), file_id=_file(app_user).id, article_id=art.id,
        link_category="ARTIKELBILD", created_by_id=app_user.id,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            FileLink.objects.create(
                id=uuid.uuid4(), file_id=_file(app_user).id, article_id=art.id,
                link_category="ARTIKELBILD", created_by_id=app_user.id,
            )


@pytest.mark.django_db
def test_artikelbild_mehrere_andere_kategorien_erlaubt(app_user):
    art = _article(app_user)
    for kat in ("ARTIKELBILD", "DOKUMENT", "SONSTIGES"):
        FileLink.objects.create(
            id=uuid.uuid4(), file_id=_file(app_user).id, article_id=art.id,
            link_category=kat, created_by_id=app_user.id,
        )
    assert FileLink.objects.filter(article_id=art.id).count() == 3


@pytest.mark.django_db
def test_file_link_genau_ein_ziel(app_user):
    art = _article(app_user)
    # article_id allein -> ok
    FileLink.objects.create(
        id=uuid.uuid4(), file_id=_file(app_user).id, article_id=art.id,
        link_category="DOKUMENT", created_by_id=app_user.id,
    )
    # article_id UND party_id -> „genau ein Ziel" verletzt
    party = _supplier(app_user)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            FileLink.objects.create(
                id=uuid.uuid4(), file_id=_file(app_user).id, article_id=art.id,
                party_id=party.id, link_category="DOKUMENT",
                created_by_id=app_user.id,
            )
    # gar kein Ziel -> ebenfalls verletzt
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            FileLink.objects.create(
                id=uuid.uuid4(), file_id=_file(app_user).id,
                link_category="DOKUMENT", created_by_id=app_user.id,
            )
