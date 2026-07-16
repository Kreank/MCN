"""Service-Tests der EK→VK-Aufschlagsmatrix (Migration 0069) gegen die Test-DB.

Geprüft werden: Regelauflösung inkl. Fallback und Einzelfall-Vorrang, Staffel,
Mindestmarge (auch gegen eine Staffel), Rundung, Massenpflege (Vorschau ==
Ergebnis), fehlender EK → „unbekannt" (NIE 0), Schutzstandard (No-Delete,
unveränderlicher Geltungsbereich) und die Invariante, dass die Matrix keine
bestehende Belegposition anfasst.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.db import Error, transaction

from db_core.models import (
    Article,
    ArticleSalePrice,
    ArticleSupplierReference,
    MarkupRule,
    QuoteLine,
)
from db_core.services import artikel as artikel_service
from db_core.services import aufschlagsmatrix as matrix
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _article(app_user, *, number, list_price=None, product_group=None, price_unit=1):
    return artikel_service.create_article(
        app_user.id, article_number=number, description=f"Artikel {number}",
        unit="Stk", line_type="MATERIAL", list_price=list_price,
        product_group=product_group, price_unit=price_unit,
    )


def _lieferant(app_user, name=None):
    return identity_service.create_organization(
        app_user.id,
        legal_name=name or f"Lieferant {uuid.uuid4().hex[:6]}",
        organization_type="COMPANY",
    )


def _ek(app_user, article, preis, lieferant=None):
    lief = lieferant or _lieferant(app_user)
    ArticleSupplierReference.objects.create(
        id=uuid.uuid4(), article_id=article.id, supplier_party_id=lief.id,
        source_system="DATANORM", source_namespace="test",
        supplier_article_number=uuid.uuid4().hex[:8],
        last_purchase_price=Decimal(preis) if preis is not None else None,
        currency="EUR" if preis is not None else None,
        valid_from=date(2020, 1, 1),
    )
    return lief


# ---------------------------------------------------------------------------
# Regelauflösung: Fallback und Einzelfall-Vorrang
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_standardregel_greift_als_fallback(app_user):
    art = _article(app_user, number="M-1", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Standard", markup_percent=Decimal("30"),
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["quelle"] == "MATRIX"
    assert res["sale_price"] == "130.00"
    assert res["regel"]["scope"] == "STANDARD"


@pytest.mark.django_db
def test_warengruppenregel_schlaegt_standardregel(app_user):
    art = _article(app_user, number="M-2", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Standard", markup_percent=Decimal("30"),
    )
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "145.00"
    assert res["regel"]["scope"] == "WARENGRUPPE"


@pytest.mark.django_db
def test_warengruppe_case_insensitiv(app_user):
    art = _article(app_user, number="M-2b", product_group="SANITÄR")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="sanitär",
    )
    assert matrix.vk_vorschlag(art.id)["sale_price"] == "145.00"


@pytest.mark.django_db
def test_lieferant_plus_warengruppe_schlaegt_warengruppe(app_user):
    art = _article(app_user, number="M-3", product_group="Sanitär")
    lief = _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    matrix.create_markup_rule(
        app_user.id, name="Sanitär @ Lieferant", markup_percent=Decimal("60"),
        product_group="Sanitär", supplier_party_id=lief.id,
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "160.00"
    assert res["regel"]["scope"] == "WARENGRUPPE_LIEFERANT"


@pytest.mark.django_db
def test_artikelregel_gewinnt_gegen_gruppenregel(app_user):
    art = _article(app_user, number="M-4", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    matrix.create_markup_rule(
        app_user.id, name="Einzelfall", markup_percent=Decimal("10"),
        article_id=art.id,
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "110.00"
    assert res["regel"]["scope"] == "ARTIKEL"


@pytest.mark.django_db
def test_artikelregel_traegt_keinen_weiteren_selektor(app_user):
    art = _article(app_user, number="M-5")
    with pytest.raises(ValueError):
        matrix.create_markup_rule(
            app_user.id, name="Falsch", markup_percent=Decimal("10"),
            article_id=art.id, product_group="Sanitär",
        )


@pytest.mark.django_db
def test_nur_eine_aktive_regel_je_geltungsbereich(app_user):
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    with pytest.raises(ValueError):
        matrix.create_markup_rule(
            app_user.id, name="Sanitär zwei", markup_percent=Decimal("50"),
            product_group="Sanitär",
        )


@pytest.mark.django_db
def test_inaktive_regel_greift_nicht(app_user):
    art = _article(app_user, number="M-6", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    regel = matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    matrix.set_markup_rule_status(app_user.id, rule_id=regel.id, status="INAKTIV")
    res = matrix.vk_vorschlag(art.id)
    assert res["quelle"] == "UNBEKANNT"
    assert res["sale_price"] is None


# ---------------------------------------------------------------------------
# Basis, Preiseinheit, Rundung
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_basis_listenpreis(app_user):
    art = _article(app_user, number="M-7", list_price=Decimal("200.0000"))
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Liste", markup_percent=Decimal("10"),
        calc_basis="LISTENPREIS",
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "220.00"
    assert res["basis_kind"] == "LISTENPREIS"


@pytest.mark.django_db
def test_listenpreis_override_ersetzt_stammlistenpreis_matrix(app_user):
    # IDS-OfferPrice überschreibt den DATANORM-Listenpreis in der Matrix-Basis:
    # Stamm 200, aktuell 250; LISTENPREIS +10 % -> 275 statt 220.
    art = _article(app_user, number="M-7b", list_price=Decimal("200.0000"))
    matrix.create_markup_rule(
        app_user.id, name="Liste", markup_percent=Decimal("10"),
        calc_basis="LISTENPREIS",
    )
    res = matrix.vk_vorschlag(art.id, listenpreis_override=Decimal("250"))
    assert res["sale_price"] == "275.00"
    assert res["basis_kind"] == "LISTENPREIS"
    # Der Kopf meldet die tatsächlich gerechnete Basis (den Override), nicht 200.
    assert Decimal(res["list_price"]) == Decimal("250")


@pytest.mark.django_db
def test_listenpreis_override_je_stueck_mit_price_unit(app_user):
    # Override ist JE STÜCK und wird — exakt wie ek_override — auf die Stamm-Skala
    # (je price_unit) hochgerechnet: 2,50 €/Stück, price_unit 100, LISTENPREIS
    # +10 % -> 2,75 €. Kein doppeltes Teilen.
    art = _article(app_user, number="M-7c", price_unit=100)
    matrix.create_markup_rule(
        app_user.id, name="Liste", markup_percent=Decimal("10"),
        calc_basis="LISTENPREIS",
    )
    res = matrix.vk_vorschlag(art.id, listenpreis_override=Decimal("2.50"))
    assert res["sale_price"] == "2.75"


@pytest.mark.django_db
def test_listenpreis_override_gilt_auch_fuer_zugewiesene_gruppe(app_user):
    # Auch die am Artikel zugewiesene LISTENPREIS-VK-Gruppe (Zweig 2, schlägt die
    # Matrix) rechnet mit dem Override: Stamm 200, aktuell 300; +20 % -> 360.
    art = _article(app_user, number="M-7d", list_price=Decimal("200.0000"))
    grp = artikel_service.create_sale_price_group(
        app_user.id, name="Liste +20", calc_basis="LISTENPREIS",
        operator="AUFSCHLAG", percent_change=Decimal("20"),
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True,
    )
    res = matrix.vk_vorschlag(art.id, listenpreis_override=Decimal("300"))
    assert res["sale_price"] == "360.00"
    assert res["quelle"] == matrix.QUELLE_VK_GRUPPE


@pytest.mark.django_db
def test_ohne_override_bleibt_stammlistenpreis(app_user):
    # Regression: ohne Override zählt weiter der Stammwert (200 +10 % -> 220).
    art = _article(app_user, number="M-7e", list_price=Decimal("200.0000"))
    matrix.create_markup_rule(
        app_user.id, name="Liste", markup_percent=Decimal("10"),
        calc_basis="LISTENPREIS",
    )
    assert matrix.vk_vorschlag(art.id)["sale_price"] == "220.00"


@pytest.mark.django_db
def test_price_unit_wird_beruecksichtigt(app_user):
    # EK je 100 Stück = 7,74 € -> 0,0774 €/Stück; +30 % = 0,10062 -> 0,10 €
    art = _article(app_user, number="M-8", price_unit=100)
    _ek(app_user, art, "7.7400")
    matrix.create_markup_rule(app_user.id, name="Std", markup_percent=Decimal("30"))
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "0.10"


@pytest.mark.django_db
def test_rundung_half_up(app_user):
    # 1,005 € * 1,00 -> Basis 1,005 -> kaufmännisch 1,01 (nicht 1,00)
    art = _article(app_user, number="M-9")
    _ek(app_user, art, "1.0050")
    matrix.create_markup_rule(app_user.id, name="Std", markup_percent=Decimal("0"))
    assert matrix.vk_vorschlag(art.id)["sale_price"] == "1.01"


@pytest.mark.django_db
def test_abschlag_negativer_aufschlag(app_user):
    art = _article(app_user, number="M-10")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(app_user.id, name="Std", markup_percent=Decimal("-10"))
    assert matrix.vk_vorschlag(art.id)["sale_price"] == "90.00"


# ---------------------------------------------------------------------------
# Fehlender EK: „unbekannt", NIE 0
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_fehlender_ek_ist_unbekannt_nie_null(app_user):
    art = _article(app_user, number="M-11", product_group="Sanitär")
    _ek(app_user, art, None)  # Referenz ohne Preis
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] is None
    assert res["quelle"] == "UNBEKANNT"
    assert res["regel"] is not None       # die Regel greift, nur die Basis fehlt
    assert "unbekannt" in res["hinweis"]


@pytest.mark.django_db
def test_fehlender_listenpreis_ist_unbekannt(app_user):
    art = _article(app_user, number="M-12")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Liste", markup_percent=Decimal("10"),
        calc_basis="LISTENPREIS",
    )
    assert matrix.vk_vorschlag(art.id)["sale_price"] is None


# ---------------------------------------------------------------------------
# Rabattstaffel
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_staffel_greift_ab_menge(app_user):
    art = _article(app_user, number="M-13")
    _ek(app_user, art, "100.00")
    regel = matrix.create_markup_rule(
        app_user.id, name="Std", markup_percent=Decimal("50"),
    )
    matrix.set_tiers(
        app_user.id, rule_id=regel.id,
        tiers=[
            {"min_quantity": Decimal("10"), "markup_percent": Decimal("40")},
            {"min_quantity": Decimal("100"), "markup_percent": Decimal("30")},
        ],
    )
    assert matrix.vk_vorschlag(art.id, menge=Decimal("1"))["sale_price"] == "150.00"
    assert matrix.vk_vorschlag(art.id, menge=Decimal("9"))["sale_price"] == "150.00"
    assert matrix.vk_vorschlag(art.id, menge=Decimal("10"))["sale_price"] == "140.00"
    res = matrix.vk_vorschlag(art.id, menge=Decimal("250"))
    assert res["sale_price"] == "130.00"
    assert res["tier_min_quantity"] == "100.000"


@pytest.mark.django_db
def test_staffel_stufe_entfernen_deaktiviert_sie(app_user):
    art = _article(app_user, number="M-14")
    _ek(app_user, art, "100.00")
    regel = matrix.create_markup_rule(
        app_user.id, name="Std", markup_percent=Decimal("50"),
    )
    matrix.set_tiers(
        app_user.id, rule_id=regel.id,
        tiers=[{"min_quantity": Decimal("10"), "markup_percent": Decimal("40")}],
    )
    assert matrix.vk_vorschlag(art.id, menge=Decimal("20"))["sale_price"] == "140.00"
    matrix.set_tiers(app_user.id, rule_id=regel.id, tiers=[])
    assert matrix.vk_vorschlag(art.id, menge=Decimal("20"))["sale_price"] == "150.00"
    assert matrix.list_tiers(regel.id) == []


# ---------------------------------------------------------------------------
# Mindestmarge — auch gegen eine Staffel
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_mindestmarge_hebt_den_preis(app_user):
    # EK 100, Aufschlag 10 % -> 110. Mindestmarge 25 % (auf den VK):
    # VK >= 100 / 0,75 = 133,3333… -> aufgerundet 133,34 (bei 133,33 läge die
    # Marge minimal UNTER 25 % — eine Untergrenze wird nicht abgerundet).
    art = _article(app_user, number="M-15")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Std", markup_percent=Decimal("10"),
        min_margin_percent=Decimal("25"),
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "133.34"
    assert res["min_margin_applied"] is True


@pytest.mark.django_db
def test_mindestmarge_schlaegt_die_staffel(app_user):
    art = _article(app_user, number="M-16")
    _ek(app_user, art, "100.00")
    regel = matrix.create_markup_rule(
        app_user.id, name="Std", markup_percent=Decimal("60"),
        min_margin_percent=Decimal("20"),   # Untergrenze 125,00
    )
    matrix.set_tiers(
        app_user.id, rule_id=regel.id,
        tiers=[{"min_quantity": Decimal("50"), "markup_percent": Decimal("5")}],
    )
    # ohne Untergrenze wären es 105,00 — die Mindestmarge zieht auf 125,00
    res = matrix.vk_vorschlag(art.id, menge=Decimal("100"))
    assert res["sale_price"] == "125.00"
    assert res["min_margin_applied"] is True


@pytest.mark.django_db
def test_mindestmarge_greift_nicht_ohne_ek(app_user):
    art = _article(app_user, number="M-17", list_price=Decimal("100.0000"))
    matrix.create_markup_rule(
        app_user.id, name="Liste", markup_percent=Decimal("10"),
        calc_basis="LISTENPREIS", min_margin_percent=Decimal("50"),
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "110.00"
    assert res["min_margin_applied"] is False


@pytest.mark.django_db
def test_mindestmarge_wird_aufgerundet_nicht_unterschritten(app_user):
    """Review-Befund A-1: eine abgerundete Untergrenze ist keine Untergrenze.

    EK 0,01 €/Stück (bei DATANORM-Kleinteilen der Normalfall), Aufschlag 0 %,
    Mindestmarge 33 % ⇒ exakte Grenze 0,014925. Kaufmännisch gerundet käme 0,01
    heraus — die Marge wäre 0 %. Es muss auf 0,02 aufgerundet werden.
    """
    art = _article(app_user, number="M-15b")
    _ek(app_user, art, "0.0100")
    matrix.create_markup_rule(
        app_user.id, name="Kleinteile", markup_percent=Decimal("0"),
        min_margin_percent=Decimal("33"),
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "0.02"
    assert res["min_margin_applied"] is True
    # …und die Marge ist tatsächlich >= 33 %.
    vk = Decimal(res["sale_price"])
    assert (vk - Decimal("0.01")) / vk >= Decimal("0.33")


@pytest.mark.django_db
def test_mindestmarge_bei_price_unit(app_user):
    """Derselbe Fall über die Preiseinheit: EK 1,00 je 100 Stück = 0,01/Stück."""
    art = _article(app_user, number="M-15c", price_unit=100)
    _ek(app_user, art, "1.0000")
    matrix.create_markup_rule(
        app_user.id, name="Kleinteile", markup_percent=Decimal("0"),
        min_margin_percent=Decimal("33"),
    )
    assert matrix.vk_vorschlag(art.id)["sale_price"] == "0.02"


@pytest.mark.django_db
def test_ek_null_ist_kein_preis(app_user):
    """Review-Befund A-7: EK 0,00 (Importfehler) ist eine Lücke, kein Preis."""
    art = _article(app_user, number="M-15d")
    _ek(app_user, art, "0.0000")
    matrix.create_markup_rule(app_user.id, name="Std", markup_percent=Decimal("45"))
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] is None
    assert res["quelle"] == "UNBEKANNT"

    # …und die Massenpflege speichert erst recht keine 0,00.
    erg = matrix.massenpflege(app_user.id, dry_run=False)
    assert erg["angelegt"] == 0
    assert not ArticleSalePrice.objects.filter(article_id=art.id).exists()


@pytest.mark.django_db
def test_ek_override_je_stueck(app_user):
    """Review-Befund A-3: der IDS-Warenkorb rechnet mit dem EK des Warenkorbs.

    Der Override ist ein Je-Stück-Preis und darf nicht ein zweites Mal durch
    price_unit geteilt werden.
    """
    art = _article(app_user, number="M-15e", price_unit=100)
    _ek(app_user, art, "1000.0000")          # veralteter Stamm-EK: 10,00 €/Stück
    matrix.create_markup_rule(app_user.id, name="Std", markup_percent=Decimal("50"))
    # Warenkorb meldet 2,00 €/Stück
    res = matrix.vk_vorschlag(art.id, ek_override=Decimal("2.00"))
    assert res["sale_price"] == "3.00"
    # ohne Override bliebe es beim alten Stammpreis
    assert matrix.vk_vorschlag(art.id)["sale_price"] == "15.00"


@pytest.mark.django_db
def test_ek_override_ohne_gespeicherten_ek(app_user):
    art = _article(app_user, number="M-15f")
    matrix.create_markup_rule(app_user.id, name="Std", markup_percent=Decimal("50"))
    assert matrix.vk_vorschlag(art.id)["sale_price"] is None
    assert matrix.vk_vorschlag(
        art.id, ek_override=Decimal("10.00")
    )["sale_price"] == "15.00"


@pytest.mark.django_db
def test_mindestmarge_grenzen(app_user):
    with pytest.raises(ValueError):
        matrix.create_markup_rule(
            app_user.id, name="X", markup_percent=Decimal("10"),
            min_margin_percent=Decimal("100"),
        )


# ---------------------------------------------------------------------------
# Rangfolge gegenüber der bestehenden Artikelkalkulation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_handfestpreis_am_artikel_schlaegt_die_matrix(app_user):
    art = _article(app_user, number="M-18")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(app_user.id, name="Std", markup_percent=Decimal("50"))
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, fixed_price=Decimal("99.00"),
        is_standard=True,
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "99.00"
    assert res["quelle"] == "ARTIKEL_FESTPREIS"


@pytest.mark.django_db
def test_vk_gruppe_am_artikel_schlaegt_die_matrix(app_user):
    art = _article(app_user, number="M-19")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(app_user.id, name="Std", markup_percent=Decimal("50"))
    grp = artikel_service.create_sale_price_group(
        app_user.id, name="Auf20", calc_basis="EK", operator="AUFSCHLAG",
        percent_change=Decimal("20"),
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True,
    )
    res = matrix.vk_vorschlag(art.id)
    assert res["sale_price"] == "120.00"
    assert res["quelle"] == "ARTIKEL_VK_GRUPPE"


# ---------------------------------------------------------------------------
# Massenpflege
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_massenpflege_vorschau_schreibt_nicht(app_user):
    art = _article(app_user, number="M-20", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    vor = matrix.massenpflege(
        app_user.id, product_group="Sanitär", dry_run=True
    )
    assert vor["angelegt"] == 1
    assert vor["zeilen"][0]["neu"] == "145.00"
    assert vor["zeilen"][0]["alt"] is None
    assert not ArticleSalePrice.objects.filter(article_id=art.id).exists()


@pytest.mark.django_db
def test_massenpflege_vorschau_gleich_ergebnis(app_user):
    art = _article(app_user, number="M-21", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    vor = matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=True)
    nach = matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    assert [
        (z["article_id"], z["aktion"], z["alt"], z["neu"]) for z in vor["zeilen"]
    ] == [
        (z["article_id"], z["aktion"], z["alt"], z["neu"]) for z in nach["zeilen"]
    ]
    asp = ArticleSalePrice.objects.get(article_id=art.id, is_standard=True)
    assert asp.fixed_price == Decimal("145.00")
    assert asp.price_origin == "MATRIX"
    # Zweiter Lauf ist idempotent.
    nochmal = matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    assert nochmal["unveraendert"] == 1
    assert nochmal["angelegt"] == 0


@pytest.mark.django_db
def test_massenpflege_aktualisiert_nach_ek_aenderung(app_user):
    art = _article(app_user, number="M-22", product_group="Sanitär")
    lief = _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    # DATANORM-Import hebt den EK an
    ref = ArticleSupplierReference.objects.get(
        article_id=art.id, supplier_party_id=lief.id
    )
    ref.last_purchase_price = Decimal("120.0000")
    ref.save(update_fields=["last_purchase_price"])

    vor = matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=True)
    assert vor["aktualisiert"] == 1
    assert vor["zeilen"][0]["alt"] == "145.00"
    assert vor["zeilen"][0]["neu"] == "174.00"
    matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    assert ArticleSalePrice.objects.get(
        article_id=art.id, is_standard=True
    ).fixed_price == Decimal("174.00")


@pytest.mark.django_db
def test_massenpflege_fasst_handpreise_nicht_an(app_user):
    art = _article(app_user, number="M-23", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, fixed_price=Decimal("99.00"), is_standard=True,
    )
    res = matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    assert res["uebersprungen"] == 1
    assert res["zeilen"][0]["grund"] == matrix.GRUND_MANUELL
    assert ArticleSalePrice.objects.get(
        article_id=art.id, is_standard=True
    ).fixed_price == Decimal("99.00")


@pytest.mark.django_db
def test_massenpflege_ueberspringt_unbekannten_ek(app_user):
    art = _article(app_user, number="M-24", product_group="Sanitär")
    _ek(app_user, art, None)
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    res = matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    assert res["uebersprungen"] == 1
    assert res["angelegt"] == 0
    # Kein Preis 0 im Stamm!
    assert not ArticleSalePrice.objects.filter(article_id=art.id).exists()


@pytest.mark.django_db
def test_massenpflege_beruehrt_keine_belegposition(app_user):
    """Kern-Invariante: eine Belegposition ist eine eingefrorene Kopie.

    Eine Massenpflege, die den Stamm-VK vervierfacht, darf ein bereits
    geschriebenes Angebot nicht anfassen — sonst wäre dessen Marge im Nachhinein
    nicht mehr nachvollziehbar.
    """
    art = _article(app_user, number="M-26", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    obj = property_service.create_property(
        app_user.id, name="Matrix-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot vor der Matrix",
        lines=[
            {"line_type": "MATERIAL", "description": "Artikel M-26",
             "quantity": 2, "unit": "Stk", "unit_price": 130,
             "tax_code": "DE_19", "source_article_id": str(art.id)},
        ],
    )
    line = QuoteLine.objects.get(quote_id=quote.id, position_number=1)

    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("400"),
        product_group="Sanitär",
    )
    matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)

    nachher = QuoteLine.objects.get(id=line.id)
    assert nachher.unit_price == line.unit_price == Decimal("130.00")
    assert nachher.net_amount == line.net_amount
    assert nachher.updated_at == line.updated_at
    # …und der Stamm-VK ist trotzdem neu gerechnet worden.
    assert ArticleSalePrice.objects.get(
        article_id=art.id, is_standard=True
    ).fixed_price == Decimal("500.00")


@pytest.mark.django_db
def test_massenpflege_schreibt_nicht_in_pricing_article(app_user):
    art = _article(app_user, number="M-25", product_group="Sanitär",
                   list_price=Decimal("10.0000"))
    _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )
    vorher = Article.objects.get(id=art.id)
    matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    nachher = Article.objects.get(id=art.id)
    assert nachher.list_price == vorher.list_price
    assert nachher.version == vorher.version
    assert nachher.updated_at == vorher.updated_at


@pytest.mark.django_db
def test_massenpflege_wendet_keine_staffel_an(app_user):
    """Review-Befund A-6: die Massenpflege schreibt den STAMMPREIS (Menge 1)."""
    art = _article(app_user, number="M-27", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    regel = matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("50"),
        product_group="Sanitär",
    )
    matrix.set_tiers(
        app_user.id, rule_id=regel.id,
        tiers=[{"min_quantity": Decimal("1"), "markup_percent": Decimal("10")}],
    )
    matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    assert ArticleSalePrice.objects.get(
        article_id=art.id, is_standard=True
    ).fixed_price == Decimal("150.00")   # nicht 110,00


@pytest.mark.django_db
def test_matrixpreis_ist_keine_zweite_wahrheit(app_user):
    """Review-Befund A-2: nach der Massenpflege darf es nicht ZWEI VK geben.

    Wird die Regel geändert, muss die Artikelansicht denselben Preis zeigen wie
    der Angebots-Editor; wird sie deaktiviert, ist der Preis „unbekannt" — und
    nicht der gespeicherte Altwert.
    """
    from db_core.services import kalkulation as kalk

    art = _article(app_user, number="M-28", product_group="Sanitär")
    _ek(app_user, art, "100.00")
    regel = matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("50"),
        product_group="Sanitär",
    )
    matrix.massenpflege(app_user.id, product_group="Sanitär", dry_run=False)
    std = [v for v in kalk.article_kalkulation(art.id)["variants"] if v["is_standard"]][0]
    assert std["sale_price"] == "150.00"

    # Regel ändern: BEIDE Sichten müssen mitgehen.
    matrix.update_markup_rule(
        app_user.id, rule_id=regel.id, markup_percent=Decimal("80")
    )
    std = [v for v in kalk.article_kalkulation(art.id)["variants"] if v["is_standard"]][0]
    assert std["sale_price"] == "180.00"
    assert matrix.vk_vorschlag(art.id)["sale_price"] == "180.00"

    # Regel deaktivieren: kein Preis mehr — nicht der gespeicherte Altwert.
    matrix.set_markup_rule_status(app_user.id, rule_id=regel.id, status="INAKTIV")
    std = [v for v in kalk.article_kalkulation(art.id)["variants"] if v["is_standard"]][0]
    assert std["kind"] == "MATRIX"
    assert std["sale_price"] is None
    assert matrix.vk_vorschlag(art.id)["sale_price"] is None


@pytest.mark.django_db
def test_massenpflege_abschnittweise(app_user, monkeypatch):
    """Review-Befund A-4: große Auswahlen laufen in Abschnitten weiter, statt an
    einer harten Obergrenze zu scheitern."""
    monkeypatch.setattr(matrix, "MAX_MASSENPFLEGE", 2)
    for i in range(5):
        art = _article(app_user, number=f"M-30-{i}", product_group="Sanitär")
        _ek(app_user, art, "100.00")
    matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("45"),
        product_group="Sanitär",
    )

    gesamt = 0
    ab = None
    runden = 0
    while True:
        res = matrix.massenpflege(
            app_user.id, product_group="Sanitär", dry_run=False, ab_artikelnummer=ab
        )
        assert res["artikel_gesamt"] == 5
        gesamt += res["angelegt"]
        runden += 1
        ab = res["weiter"]
        if not ab:
            break
        assert runden < 10
    assert gesamt == 5
    assert runden == 3   # 2 + 2 + 1
    assert ArticleSalePrice.objects.filter(
        fixed_price=Decimal("145.00"), price_origin="MATRIX"
    ).count() == 5


# ---------------------------------------------------------------------------
# Schutzstandard
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regel_kein_loeschen(app_user):
    regel = matrix.create_markup_rule(
        app_user.id, name="Std", markup_percent=Decimal("30"),
    )
    with pytest.raises(Error):
        with transaction.atomic():
            MarkupRule.objects.filter(id=regel.id).delete()


@pytest.mark.django_db
def test_geltungsbereich_ist_unveraenderlich(app_user):
    regel = matrix.create_markup_rule(
        app_user.id, name="Sanitär", markup_percent=Decimal("30"),
        product_group="Sanitär",
    )
    with pytest.raises(Error):
        with transaction.atomic():
            MarkupRule.objects.filter(id=regel.id).update(product_group="Heizung")


@pytest.mark.django_db
def test_reaktivieren_kollidiert_nicht_still(app_user):
    a = matrix.create_markup_rule(
        app_user.id, name="A", markup_percent=Decimal("30"), product_group="Sanitär",
    )
    matrix.set_markup_rule_status(app_user.id, rule_id=a.id, status="INAKTIV")
    matrix.create_markup_rule(
        app_user.id, name="B", markup_percent=Decimal("40"), product_group="Sanitär",
    )
    with pytest.raises(ValueError):
        matrix.set_markup_rule_status(app_user.id, rule_id=a.id, status="AKTIV")
