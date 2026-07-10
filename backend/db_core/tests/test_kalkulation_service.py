"""Service-Tests der VK-Kalkulation (rein lesend) gegen die Test-DB.

Der VK ist eine Formel (Basis EK/Listenpreis × Auf-/Abschlag) bzw. ein Festpreis;
die Berechnung liegt in der App, nicht in der DB.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from db_core.models import ArticleSupplierReference
from db_core.services import artikel as artikel_service
from db_core.services import identity as identity_service
from db_core.services import kalkulation as kalkulation_service


def _article(app_user, *, number, list_price=None, line_type="MATERIAL"):
    return artikel_service.create_article(
        app_user.id, article_number=number, description="Artikel",
        unit="Stk", line_type=line_type, list_price=list_price,
    )


def _supplier_ref(app_user, article, *, price, valid_from=None, valid_until=None):
    supplier = identity_service.create_organization(
        app_user.id, legal_name=f"Lieferant {uuid.uuid4().hex[:6]}",
        organization_type="COMPANY",
    )
    return ArticleSupplierReference.objects.create(
        id=uuid.uuid4(), article_id=article.id, supplier_party_id=supplier.id,
        source_system="MANUELL", source_namespace="test",
        supplier_article_number=uuid.uuid4().hex[:8],
        last_purchase_price=Decimal(price), currency="EUR",
        valid_from=valid_from or date(2026, 1, 1), valid_until=valid_until,
    )


@pytest.mark.django_db
def test_listenpreis_aufschlag(app_user):
    art = _article(app_user, number="A-1", list_price=Decimal("100.00"))
    grp = artikel_service.create_sale_price_group(
        app_user.id, name="Auf30", calc_basis="LISTENPREIS",
        operator="AUFSCHLAG", percent_change=Decimal("30.000"),
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True
    )
    data = kalkulation_service.article_kalkulation(art.id)
    assert Decimal(data["list_price"]) == Decimal("100.00")   # 4 NK seit Migration 0039
    assert data["ek"] is None
    v = data["variants"][0]
    assert v["kind"] == "FORMEL"
    assert v["basis_kind"] == "LISTENPREIS"
    assert v["sale_price"] == "130.00"


@pytest.mark.django_db
def test_ek_abschlag_betrag(app_user):
    art = _article(app_user, number="A-2", list_price=Decimal("100.00"))
    _supplier_ref(app_user, art, price="80.00")
    grp = artikel_service.create_sale_price_group(
        app_user.id, name="EKminus5", calc_basis="EK",
        operator="ABSCHLAG", amount_change=Decimal("5.00"),
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True
    )
    data = kalkulation_service.article_kalkulation(art.id)
    assert Decimal(data["ek"]) == Decimal("80.00")   # 4 NK seit Migration 0038
    # EK 80 − 5 = 75.
    assert data["variants"][0]["sale_price"] == "75.00"


@pytest.mark.django_db
def test_festpreis(app_user):
    art = _article(app_user, number="A-3", list_price=Decimal("100.00"))
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, fixed_price=Decimal("42.50"), is_standard=True
    )
    v = kalkulation_service.article_kalkulation(art.id)["variants"][0]
    assert v["kind"] == "FESTPREIS"
    assert v["sale_price"] == "42.50"


@pytest.mark.django_db
def test_ek_basis_ohne_referenz_liefert_keinen_vk(app_user):
    art = _article(app_user, number="A-4")  # kein list_price, kein EK
    grp = artikel_service.create_sale_price_group(
        app_user.id, name="EK20", calc_basis="EK",
        operator="AUFSCHLAG", percent_change=Decimal("20.000"),
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True
    )
    v = kalkulation_service.article_kalkulation(art.id)["variants"][0]
    assert v["basis_amount"] is None
    assert v["sale_price"] is None


@pytest.mark.django_db
def test_abgelaufene_ek_referenz_ignoriert(app_user):
    art = _article(app_user, number="A-5")
    # Nur eine bereits abgelaufene Referenz → kein aktueller EK.
    _supplier_ref(
        app_user, art, price="80.00",
        valid_from=date(2020, 1, 1), valid_until=date(2020, 12, 31),
    )
    data = kalkulation_service.article_kalkulation(art.id)
    assert data["ek"] is None


@pytest.mark.django_db
def test_rundung_half_up(app_user):
    # 0,18 + 45 % = 0,261 → kaufmännisch gerundet 0,26.
    art = _article(app_user, number="A-R", list_price=Decimal("0.18"))
    grp = artikel_service.create_sale_price_group(
        app_user.id, name="Auf45R", calc_basis="LISTENPREIS",
        operator="AUFSCHLAG", percent_change=Decimal("45.000"),
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, sale_price_group_id=grp.id, is_standard=True
    )
    v = kalkulation_service.article_kalkulation(art.id)["variants"][0]
    assert v["sale_price"] == "0.26"


@pytest.mark.django_db
def test_standard_variante_zuerst(app_user):
    art = _article(app_user, number="A-V", list_price=Decimal("100.00"))
    grp = artikel_service.create_sale_price_group(
        app_user.id, name="Auf10V", calc_basis="LISTENPREIS",
        operator="AUFSCHLAG", percent_change=Decimal("10.000"),
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, label="Aktion",
        fixed_price=Decimal("90.00"), is_standard=False,
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=art.id, label="Standard",
        sale_price_group_id=grp.id, is_standard=True,
    )
    variants = kalkulation_service.article_kalkulation(art.id)["variants"]
    assert variants[0]["is_standard"] is True
    assert variants[0]["label"] == "Standard"


@pytest.mark.django_db
def test_unbekannter_artikel(app_user):
    assert kalkulation_service.article_kalkulation(uuid.uuid4()) is None


@pytest.mark.django_db
def test_ek_neueste_referenz_gewinnt(app_user):
    art = _article(app_user, number="A-6")
    _supplier_ref(app_user, art, price="70.00", valid_from=date(2026, 1, 1))
    _supplier_ref(app_user, art, price="90.00", valid_from=date(2026, 6, 1))
    data = kalkulation_service.article_kalkulation(art.id)
    # Neuerer valid_from (Juni) gewinnt.
    assert Decimal(data["ek"]) == Decimal("90.00")   # 4 NK seit Migration 0038
