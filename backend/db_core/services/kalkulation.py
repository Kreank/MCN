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

from db_core.models import (
    Article,
    ArticleSalePrice,
    ArticleSupplierReference,
    SalePriceGroup,
)

_CENT = Decimal("0.01")


def _round2(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def primary_supplier_reference(article_id, on=None, *, require_price=False):
    """Primärer (aktuell gültiger) Lieferantenbezug eines Artikels.

    „Primär" = aktuell gültig (valid_from <= Stichtag, valid_until offen oder
    > Stichtag) mit dem JÜNGSTEN valid_from; bei Gleichstand entscheidet
    last_imported_at, dann id (das Schema gibt keine andere Priorisierung vor).
    Mit require_price=True werden nur Referenzen mit gesetztem Einkaufspreis
    betrachtet (für die VK-Basis EK).
    """
    on = on or date.today()
    qs = ArticleSupplierReference.objects.filter(article_id=article_id, valid_from__lte=on)
    if require_price:
        qs = qs.filter(last_purchase_price__isnull=False)
    return (
        qs.filter(Q(valid_until__isnull=True) | Q(valid_until__gt=on))
        .order_by("-valid_from", F("last_imported_at").desc(nulls_last=True), "id")
        .first()
    )


def primary_supplier_names(article_ids, on=None):
    """Namen der primären Lieferanten für viele Artikel in EINER Query.

    Vermeidet N+1 in der Artikelliste: statt je Zeile
    `primary_supplier_reference` aufzurufen, werden alle aktuell gültigen
    Referenzen der Seite gemeinsam geladen (mit Join auf die Partei über
    select_related) und in Python je Artikel der primäre gewählt — dieselbe
    Priorisierung wie `primary_supplier_reference` (jüngstes valid_from, dann
    last_imported_at, dann id). Aufwand hängt an der Seitengröße (den übergebenen
    IDs), nicht an der Gesamtzahl der 2,3-Mio-Artikel.

    Gibt {article_id: display_name} zurück (nur Artikel mit gültigem Bezug).
    """
    ids = [i for i in article_ids if i is not None]
    if not ids:
        return {}
    on = on or date.today()
    refs = (
        ArticleSupplierReference.objects.filter(article_id__in=ids, valid_from__lte=on)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=on))
        .select_related("supplier_party")
        .order_by(
            "article_id", "-valid_from",
            F("last_imported_at").desc(nulls_last=True), "id",
        )
    )
    namen = {}
    for ref in refs:
        # Erste Referenz je Artikel ist dank der Sortierung die primäre.
        if ref.article_id not in namen:
            namen[ref.article_id] = ref.supplier_party.display_name
    return namen


def _current_ek(article_id, on=None):
    """Einkaufspreis (last_purchase_price) des primären Lieferantenbezugs."""
    ref = primary_supplier_reference(article_id, on, require_price=True)
    return ref.last_purchase_price if ref else None


def _je_stueck(betrag, price_unit):
    """Rechnet einen je-`price_unit`-Preis auf den je-Stück-Preis um.

    list_price und Einkaufspreis gelten je `price_unit` Einheiten (Hero
    „Preiseinheit", Migration 0042). price_unit ist stets 1/10/100/1000 —
    die Division ist exakt (Zehnerpotenz), es entsteht kein Rundungsfehler.
    """
    if betrag is None:
        return None
    return betrag / Decimal(price_unit or 1)


def _apply_formula(basis, group):
    """Wendet die Auf-/Abschlagsformel der sale_price_group auf die Basis an.

    `basis` ist bereits der je-Stück-Preis (durch price_unit geteilt); das
    Ergebnis wird kaufmännisch auf zwei Nachkommastellen gerundet.
    """
    if basis is None:
        return None
    sign = Decimal(1) if group.operator == "AUFSCHLAG" else Decimal(-1)
    if group.percent_change is not None:
        return _round2(basis + sign * basis * group.percent_change / Decimal(100))
    return _round2(basis + sign * group.amount_change)


def _matrix_variante(article, asp, ek):
    """VK-Variante, die aus der Aufschlagsmatrix stammt — live gerechnet.

    Der gespeicherte `fixed_price` wird bewusst NICHT ausgewiesen: er ist nur die
    zuletzt geschriebene Ausfertigung der Regel und kann veraltet sein (Regel
    geändert oder deaktiviert). Maßgeblich ist immer die Regel.
    """
    # Lokaler Import: `aufschlagsmatrix` setzt auf diesem Modul auf.
    from db_core.services import aufschlagsmatrix as matrix

    lief, _ek_ref = matrix._bezug([article.id]).get(article.id, (None, None))
    regel, res = matrix.matrix_preis(
        article, ek=ek, supplier_party_id=lief, menge=Decimal(1)
    )
    if regel is None:
        return {
            "label": asp.label,
            "is_standard": asp.is_standard,
            "kind": "MATRIX",
            "group_name": None,
            "basis_kind": None,
            "basis_amount": None,
            "operator": None,
            "percent_change": None,
            "amount_change": None,
            "sale_price": None,   # keine Regel mehr → unbekannt, nicht der Altwert
        }
    return {
        "label": asp.label,
        "is_standard": asp.is_standard,
        "kind": "MATRIX",
        "group_name": regel.name,
        "basis_kind": res["basis_kind"],
        "basis_amount": (
            str(res["basis_amount"]) if res["basis_amount"] is not None else None
        ),
        "operator": "AUFSCHLAG" if res["markup_percent"] >= 0 else "ABSCHLAG",
        "percent_change": str(abs(res["markup_percent"])),
        "amount_change": None,
        "sale_price": (
            str(res["sale_price"]) if res["sale_price"] is not None else None
        ),
    }


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
            # Eine von der Aufschlagsmatrix geschriebene Zeile (price_origin
            # MATRIX) ist eine AUSFERTIGUNG der Regel, keine eigene Wahrheit:
            # Sie wird live neu gerechnet. Sonst zeigte die Artikelansicht den
            # gespeicherten Preis, während der Angebots-Editor (der immer live
            # rechnet) einen anderen einsetzt — zwei Verkaufspreise für denselben
            # Artikel. Greift keine Regel mehr, ist der Preis „unbekannt".
            if getattr(asp, "price_origin", "MANUELL") == "MATRIX":
                variants.append(_matrix_variante(article, asp, ek))
                continue
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
        roh = article.list_price if group.calc_basis == "LISTENPREIS" else ek
        # Basis je Stück: durch price_unit teilen (Hero-Preiseinheit).
        basis = _je_stueck(roh, article.price_unit)
        formel_vk = _apply_formula(basis, group)
        # Manuelle Überschreibung gewinnt gegen den Formelwert (Hero-Modell:
        # Formel + Überschreibung je Gruppe). Die Formelfelder bleiben zur
        # Nachvollziehbarkeit gefüllt; ausgewiesen wird der überschriebene VK.
        vk = asp.fixed_price if asp.fixed_price is not None else formel_vk
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


def verkaufspreise_uebersicht(article_id):
    """Hero-Reiter „Verkaufspreise": ALLE aktiven VK-Gruppen mit errechnetem VK.

    Für jede aktive `sale_price_group` wird der VK je Stück aus der Formel
    berechnet (Basis EK aus dem primären Lieferantenbezug bzw. list_price,
    geteilt durch price_unit). Trägt der Artikel für diese Gruppe eine manuelle
    Überschreibung (`article_sale_price.fixed_price`), wird sie mitgeliefert; der
    „effektive" VK ist die Überschreibung, sonst der errechnete Wert. Genau eine
    Gruppe ist als Standard markiert.

    Sowohl der errechnete VK als auch die Überschreibung sind je Stück (die
    Hero-Spalte heisst „VK/Einheit"); nur die BASIS wird durch price_unit
    geteilt, die Überschreibung selbst nicht.

    Gibt None zurück, wenn der Artikel nicht existiert.
    """
    article = Article.objects.filter(id=article_id).first()
    if article is None:
        return None

    ek = _current_ek(article_id)
    # Überschreibungen je Gruppe (article_sale_price mit gesetzter Gruppe).
    per_group = {
        asp.sale_price_group_id: asp
        for asp in ArticleSalePrice.objects.filter(
            article_id=article_id, sale_price_group_id__isnull=False
        )
    }

    gruppen = []
    for group in SalePriceGroup.objects.filter(status="AKTIV").order_by("name", "id"):
        roh = article.list_price if group.calc_basis == "LISTENPREIS" else ek
        basis = _je_stueck(roh, article.price_unit)
        computed = _apply_formula(basis, group)
        asp = per_group.get(group.id)
        override = asp.fixed_price if (asp and asp.fixed_price is not None) else None
        is_standard = bool(asp and asp.is_standard)
        effective = override if override is not None else computed
        gruppen.append(
            {
                "sale_price_group_id": str(group.id),
                "name": group.name,
                "calc_basis": group.calc_basis,
                "operator": group.operator,
                "percent_change": (
                    str(group.percent_change)
                    if group.percent_change is not None else None
                ),
                "amount_change": (
                    str(group.amount_change)
                    if group.amount_change is not None else None
                ),
                "basis_amount": str(basis) if basis is not None else None,
                "computed_sale_price": str(computed) if computed is not None else None,
                "override_price": str(override) if override is not None else None,
                "effective_sale_price": (
                    str(effective) if effective is not None else None
                ),
                "is_standard": is_standard,
            }
        )

    return {
        "article_id": str(article.id),
        "article_number": article.article_number,
        "description": article.description,
        "unit": article.unit,
        "price_unit": article.price_unit,
        "list_price": (
            str(article.list_price) if article.list_price is not None else None
        ),
        "ek": str(ek) if ek is not None else None,
        "groups": gruppen,
    }
