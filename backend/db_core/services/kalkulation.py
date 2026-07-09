"""VK-Kalkulations-Service (rein lesend): berechnet den Verkaufspreis eines
Artikels aus der Formel der Kalkulationsgruppe.

Der VK ist im Schema (Migration 0033) KEINE gespeicherte Zahl, sondern eine
Formel: Basis (EK oder Listenpreis) mit prozentualem oder Betrags-Auf-/Abschlag
je sale_price_group; alternativ ein fixed_price je Variante. Der EK stammt aus
dem aktuell gültigen article_supplier_reference (last_purchase_price). Die DB
wertet die Formel nicht selbst aus — das übernimmt dieser Service.

Rein lesend (kein business_transaction). Beträge als String (Decimal, verlustfrei).
"""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import F, Q

from db_core.models import Article, ArticleSalePrice, ArticleSupplierReference

_CENT = Decimal("0.01")


def _round2(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _current_ek(article_id, on=None):
    """Aktuell gültiger EK (last_purchase_price) aus article_supplier_reference.

    Gültig = valid_from <= Stichtag und (valid_until offen oder > Stichtag), Preis
    gesetzt. Bei mehreren Lieferantenreferenzen entscheidet der neueste
    valid_from, dann last_imported_at (das Schema gibt keine andere Priorisierung
    vor)."""
    on = on or date.today()
    ref = (
        ArticleSupplierReference.objects.filter(
            article_id=article_id,
            last_purchase_price__isnull=False,
            valid_from__lte=on,
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=on))
        .order_by("-valid_from", F("last_imported_at").desc(nulls_last=True), "id")
        .first()
    )
    return ref.last_purchase_price if ref else None


def _apply_formula(basis, group):
    """Wendet die Auf-/Abschlagsformel der sale_price_group auf die Basis an."""
    if basis is None:
        return None
    sign = Decimal(1) if group.operator == "AUFSCHLAG" else Decimal(-1)
    if group.percent_change is not None:
        return _round2(basis + sign * basis * group.percent_change / Decimal(100))
    return _round2(basis + sign * group.amount_change)


def article_kalkulation(article_id):
    """VK-Kalkulation eines Artikels: Listenpreis, aktueller EK und alle
    VK-Varianten (Formel oder Festpreis) mit errechnetem Verkaufspreis.

    Gibt None zurück, wenn der Artikel nicht existiert.
    """
    article = Article.objects.filter(id=article_id).first()
    if article is None:
        return None

    ek = _current_ek(article_id)
    variants = []
    for asp in (
        ArticleSalePrice.objects.filter(article_id=article_id)
        .select_related("sale_price_group")
        .order_by("-is_standard", "label")
    ):
        if asp.sale_price_group_id is None:
            variants.append(
                {
                    "label": asp.label,
                    "is_standard": asp.is_standard,
                    "kind": "FESTPREIS",
                    "group_name": None,
                    "basis_kind": None,
                    "basis_amount": None,
                    "operator": None,
                    "percent_change": None,
                    "amount_change": None,
                    "sale_price": str(asp.fixed_price),
                }
            )
            continue
        group = asp.sale_price_group
        basis = article.list_price if group.calc_basis == "LISTENPREIS" else ek
        vk = _apply_formula(basis, group)
        variants.append(
            {
                "label": asp.label,
                "is_standard": asp.is_standard,
                "kind": "FORMEL",
                "group_name": group.name,
                "basis_kind": group.calc_basis,
                "basis_amount": str(basis) if basis is not None else None,
                "operator": group.operator,
                "percent_change": (
                    str(group.percent_change)
                    if group.percent_change is not None
                    else None
                ),
                "amount_change": (
                    str(group.amount_change)
                    if group.amount_change is not None
                    else None
                ),
                "sale_price": str(vk) if vk is not None else None,
            }
        )

    return {
        "article_id": str(article.id),
        "article_number": article.article_number,
        "description": article.description,
        "list_price": (
            str(article.list_price) if article.list_price is not None else None
        ),
        "ek": str(ek) if ek is not None else None,
        "variants": variants,
    }
