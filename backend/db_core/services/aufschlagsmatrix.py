"""EK→VK-Aufschlagsmatrix: die EINZIGE Stelle, an der aus einem Einkaufspreis ein
Verkaufspreis wird.

Über IDS-Connect und DATANORM liegen die Einkaufspreise im System
(`pricing.article_supplier_reference.last_purchase_price`). Bisher musste aus
ihnen von Hand ein VK gerechnet werden, oder es brauchte je Artikel eine
`article_sale_price`-Zeile (Migration 0033). Für Katalogartikel ist beides
untauglich — dafür ist die Matrix da: eine Regel je **Warengruppe** (optional je
Lieferant), mit Fallback auf eine Standardregel und mit dem Einzelfall (Regel je
Artikel), der die Gruppenregel schlägt.

RANGFOLGE (`vk_vorschlag`) — die Matrix steht UNTER der Artikelkalkulation,
nicht daneben:

1. **Festpreis am Artikel** (`article_sale_price` Standard-Variante mit
   `fixed_price`, `price_origin='MANUELL'`) → gewinnt immer. Handpflege schlägt
   Regel; eine Staffel greift auf einem Festpreis nicht.
2. **VK-Gruppe am Artikel** (Standard-Variante mit `sale_price_group`) → die
   bestehende Formel (`kalkulation._apply_formula`), unverändert.
3. **Matrix** — Regel auflösen (Artikel > Warengruppe+Lieferant > Warengruppe >
   Lieferant > Standardregel), Staffel anwenden, Mindestmarge als Untergrenze.
   Hierher fallen auch Zeilen mit `price_origin='MATRIX'`: ein von der
   Massenpflege geschriebener VK ist eine **Ausfertigung der Regel**, keine
   konkurrierende Wahrheit — er wird live neu gerechnet, damit ein veralteter
   gespeicherter Preis niemals in ein Angebot rutscht.
4. Sonst **unbekannt** (`None`) — NIE 0. (Bestehende Invariante der Auswertungen:
   ein fehlender EK ist „unbekannt", kein Nullpreis.)

RECHNUNG (Decimal, ROUND_HALF_UP, auf 2 Nachkommastellen quantisiert — die Skala
von `article_sale_price.fixed_price` und `quote_line.unit_price`):

    basis  = (EK | Listenpreis) / price_unit        # je Stück (Hero-Preiseinheit)
    aufschlag = Staffel(Menge) ?? regel.markup_percent
    vk     = round2(basis * (1 + aufschlag/100))
    ist min_margin_percent gesetzt UND der EK bekannt:
        untergrenze = round2(ek_je_stueck / (1 - min_margin/100))
        vk = max(vk, untergrenze)

Die Mindestmarge ist die **Handelsspanne auf den VK** ((VK−EK)/VK), nicht der
Aufschlag auf den EK — sie wirkt auch dann, wenn eine Staffel sie unterbieten
wollte, und auch bei Basis LISTENPREIS.

WAS DIE MATRIX NICHT TUT: Sie schreibt von sich aus nichts. Belegpositionen sind
eingefrorene Kopien (HANDOFF-Invariante) — die Matrix liefert beim ANLEGEN einer
Position einen Vorschlag, den der Kalkulator überschreiben darf, und rührt eine
bestehende Position nie an. In `pricing.article` schreibt sie nie; der einzige
Schreibweg ist die ausdrückliche, bestätigungspflichtige **Massenpflege**
(`pricing/AENDERN`), die nur `article_sale_price`-Zeilen mit
`price_origin='MATRIX'` fortschreibt bzw. anlegt.
"""
import uuid
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from django.db.models import F, Q

from db_core.db_context import business_transaction
from db_core.models import (
    Article,
    ArticleSalePrice,
    ArticleSupplierReference,
    MarkupRule,
    MarkupRuleTier,
    Party,
)
from db_core.services._validation import ensure_exists
from db_core.services.kalkulation import _apply_formula, _je_stueck

_CENT = Decimal("0.01")
_HUNDERT = Decimal(100)

CALC_BASES = ("EK", "LISTENPREIS")
STATUS = ("AKTIV", "INAKTIV")

# Quellen eines VK-Vorschlags
QUELLE_FESTPREIS = "ARTIKEL_FESTPREIS"
QUELLE_VK_GRUPPE = "ARTIKEL_VK_GRUPPE"
QUELLE_MATRIX = "MATRIX"
QUELLE_UNBEKANNT = "UNBEKANNT"

# Massenpflege: so viele Artikel werden höchstens in EINEM Vorgang angefasst.
MAX_MASSENPFLEGE = 20_000
# So viele Beispielzeilen liefert die Vorschau zurück (die Zahlen sind vollständig).
MAX_VORSCHAU_ZEILEN = 200
_BATCH = 500


class AufschlagsmatrixFehler(ValueError):
    """Fachlich unzulässig (→ 422)."""


def _round2(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _dec(wert, feld):
    try:
        return Decimal(str(wert))
    except Exception as exc:  # noqa: BLE001
        raise AufschlagsmatrixFehler(f"{feld}: keine gültige Zahl.") from exc


# ---------------------------------------------------------------------------
# Regelauflösung
# ---------------------------------------------------------------------------

def _rang(regel):
    """Spezifität einer Regel — je höher, desto stärker (Einzelfall gewinnt)."""
    if regel.article_id is not None:
        return 4
    if regel.product_group is not None and regel.supplier_party_id is not None:
        return 3
    if regel.product_group is not None:
        return 2
    if regel.supplier_party_id is not None:
        return 1
    return 0


def _norm_gruppe(wert):
    """Vergleichsform einer Warengruppe.

    Bewusst `lower()` und NICHT `casefold()`: der Unique-Index in der DB
    (`uq_markup_rule_scope`) benutzt `lower(product_group)`. `casefold()` bildet
    'ß' auf 'ss' ab, `lower()` nicht — mit casefold hier fielen „Straße" und
    „STRASSE" für den Service zusammen, für den Index aber nicht (zwei aktive
    Regeln gleichen Ranges, Auswahl per id) bzw. umgekehrt liesse die
    Kollisionsprüfung einen Fall durch, den der Index mit 500 statt 422 quittiert.
    Service und Index müssen dieselbe Normalform benutzen.
    """
    return wert.strip().lower() if wert is not None else None


def _passt(regel, article_id, product_group, supplier_party_id):
    if regel.article_id is not None:
        return regel.article_id == article_id
    if regel.product_group is not None:
        if product_group is None:
            return False
        if _norm_gruppe(regel.product_group) != _norm_gruppe(product_group):
            return False
    if regel.supplier_party_id is not None:
        return regel.supplier_party_id == supplier_party_id
    return True


def regel_aufloesen(regeln, article_id, product_group, supplier_party_id):
    """Wählt aus AKTIVEN Regeln die spezifischste passende (oder None).

    Kaskade: Artikel > Warengruppe+Lieferant > Warengruppe > Lieferant >
    Standardregel (alle Selektoren NULL). Der partielle Unique-Index
    (`uq_markup_rule_scope`) stellt sicher, dass je Rang höchstens eine aktive
    Regel passt — die Auswahl ist damit eindeutig.
    """
    treffer = [
        r for r in regeln
        if _passt(r, article_id, product_group, supplier_party_id)
    ]
    if not treffer:
        return None
    return max(treffer, key=lambda r: (_rang(r), str(r.id)))


def _aktive_regeln():
    return list(
        MarkupRule.objects.filter(status="AKTIV").select_related("supplier_party")
    )


def lade_regelwerk():
    """Alle aktiven Regeln + ihre Staffeln in ZWEI Queries — als `regelwerk`.

    Wer mehrere Artikel hintereinander bepreist (IDS-Warenkorb, Massenpflege),
    lädt das Regelwerk EINMAL und reicht es durch; sonst zieht jeder Artikel die
    komplette Regeltabelle samt Staffeln erneut (N+1).
    """
    regeln = _aktive_regeln()
    return regeln, _tiers([r.id for r in regeln])


def _tiers(rule_ids):
    """Aktive Staffelstufen je Regel, absteigend nach Mengenschwelle."""
    out = {}
    if not rule_ids:
        return out
    for t in MarkupRuleTier.objects.filter(
        markup_rule_id__in=list(rule_ids), status="AKTIV"
    ).order_by("-min_quantity"):
        out.setdefault(t.markup_rule_id, []).append(t)
    return out


def _staffel_aufschlag(tiers, menge):
    """Höchste Stufe mit min_quantity <= Menge; sonst None (Basisaufschlag)."""
    if not tiers or menge is None:
        return None
    for t in tiers:  # absteigend sortiert
        if menge >= t.min_quantity:
            return t
    return None


# ---------------------------------------------------------------------------
# DIE Rechenstelle
# ---------------------------------------------------------------------------

def berechne(regel, *, ek, list_price, price_unit, menge=Decimal(1), tiers=None):
    """Verkaufspreis aus einer Regel — die einzige Rechenstelle der Matrix.

    Gibt ein dict mit Ergebnis UND Rechenweg zurück (Nachvollziehbarkeit im UI):
    basis/aufschlag/Staffel/Mindestmarge. `sale_price` ist None, wenn die Basis
    fehlt ODER nicht positiv ist — ein EK von 0,00 (Importfehler) ist eine LÜCKE,
    keine Aussage: er darf keinen 0-€-Verkaufspreis erzeugen.
    """
    roh = list_price if regel.calc_basis == "LISTENPREIS" else ek
    basis = _je_stueck(roh, price_unit)
    ek_stueck = _je_stueck(ek, price_unit)

    stufe = _staffel_aufschlag(tiers, menge)
    aufschlag = stufe.markup_percent if stufe is not None else regel.markup_percent

    ergebnis = {
        "basis_kind": regel.calc_basis,
        "basis_amount": basis,
        "markup_percent": aufschlag,
        "tier_min_quantity": stufe.min_quantity if stufe is not None else None,
        "min_margin_percent": regel.min_margin_percent,
        "min_margin_applied": False,
        "sale_price": None,
    }
    # Keine Basis oder Basis <= 0 → „unbekannt", NIE 0 (bestehende Invariante).
    if basis is None or basis <= 0:
        ergebnis["basis_amount"] = basis if (basis is not None and basis > 0) else None
        return ergebnis

    vk = _round2(basis * (_HUNDERT + aufschlag) / _HUNDERT)

    if (
        regel.min_margin_percent is not None
        and ek_stueck is not None
        and ek_stueck > 0
    ):
        # Handelsspanne auf den VK: (VK - EK) / VK >= m/100  <=>  VK >= EK/(1-m/100).
        # AUFRUNDEN (ROUND_CEILING): eine abgerundete Untergrenze ist keine
        # Untergrenze — bei EK 0,01 und Mindestmarge 33 % läge die exakte Grenze bei
        # 0,014925; kaufmännisch gerundet käme 0,01 heraus und die Marge wäre 0 %.
        # Bei DATANORM-Kleinteilen (price_unit 100/1000) ist das der Normalfall.
        untergrenze = (
            ek_stueck * _HUNDERT / (_HUNDERT - regel.min_margin_percent)
        ).quantize(_CENT, rounding=ROUND_CEILING)
        if untergrenze > vk:
            vk = untergrenze
            ergebnis["min_margin_applied"] = True

    ergebnis["sale_price"] = vk
    return ergebnis


def matrix_preis(
    article, *, ek, supplier_party_id, menge=Decimal(1), regelwerk=None,
    list_price_override=None,
):
    """Regel + Rechenergebnis für EINEN Artikel (oder (None, None), wenn keine
    Regel greift). Gemeinsame Auflösung für `vk_vorschlag`, die Massenpflege und
    die Kalkulationsansichten — es gibt nur diese eine Rechenstelle.

    `list_price_override` setzt den gespeicherten Listenpreis für diese Rechnung
    außer Kraft (IDS-Warenkorb: der Händler liefert mit `OfferPrice` die
    tagesaktuelle Listenpreis-Aussage, der Stamm trägt nur den DATANORM-Stand). Der
    Wert liegt auf der Stamm-Skala (je `price_unit`), damit `berechne` ihn gleich
    behandelt wie den gespeicherten Listenpreis; None = Stammwert verwenden."""
    regeln, tiers = regelwerk if regelwerk is not None else lade_regelwerk()
    regel = regel_aufloesen(
        regeln, article.id, article.product_group, supplier_party_id
    )
    if regel is None:
        return None, None
    res = berechne(
        regel,
        ek=ek,
        list_price=(
            article.list_price if list_price_override is None else list_price_override
        ),
        price_unit=article.price_unit,
        menge=menge,
        tiers=tiers.get(regel.id, []),
    )
    return regel, res


# ---------------------------------------------------------------------------
# Bezugsdaten (EK + Lieferant) in einem Rutsch — auch für die Massenpflege
# ---------------------------------------------------------------------------

def _bezug(article_ids, on=None):
    """{article_id: (supplier_party_id, ek)} aus den aktuell gültigen Referenzen.

    Dieselbe Priorisierung wie `kalkulation.primary_supplier_reference`
    (jüngstes valid_from, dann last_imported_at, dann id). Der EK stammt aus der
    primären Referenz MIT Preis (eine Referenz ohne Preis verdeckt keinen EK) —
    deckungsgleich mit `kalkulation._current_ek`.
    """
    ids = [i for i in article_ids if i is not None]
    if not ids:
        return {}
    on = on or date.today()
    refs = (
        ArticleSupplierReference.objects.filter(article_id__in=ids, valid_from__lte=on)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=on))
        .order_by(
            "article_id", "-valid_from",
            F("last_imported_at").desc(nulls_last=True), "id",
        )
    )
    out = {}
    for ref in refs:
        lief, ek = out.get(ref.article_id, (None, None))
        if ref.article_id not in out:
            # Erste Referenz je Artikel ist dank der Sortierung die primäre.
            lief = ref.supplier_party_id
        if ek is None:
            ek = ref.last_purchase_price
        out[ref.article_id] = (lief, ek)
    return out


def _standard_variante(article_ids):
    """{article_id: ArticleSalePrice} — die Standard-Variante je Artikel."""
    ids = [i for i in article_ids if i is not None]
    if not ids:
        return {}
    return {
        asp.article_id: asp
        for asp in ArticleSalePrice.objects.filter(
            article_id__in=ids, is_standard=True
        ).select_related("sale_price_group")
    }


def _scope_text(regel):
    if regel.article_id is not None:
        return "Artikel"
    teile = []
    if regel.product_group:
        teile.append(f"Warengruppe „{regel.product_group}“")
    if regel.supplier_party_id is not None:
        name = (
            regel.supplier_party.display_name
            if regel.supplier_party_id and regel.supplier_party is not None
            else "Lieferant"
        )
        teile.append(f"Lieferant {name}")
    return " + ".join(teile) if teile else "Standardregel (Fallback)"


def _regel_out(regel, tiers):
    return {
        "id": str(regel.id),
        "name": regel.name,
        "scope": _scope_kind(regel),
        "scope_text": _scope_text(regel),
        "article_id": str(regel.article_id) if regel.article_id else None,
        "product_group": regel.product_group,
        "supplier_party_id": (
            str(regel.supplier_party_id) if regel.supplier_party_id else None
        ),
        "supplier_name": (
            regel.supplier_party.display_name
            if regel.supplier_party_id and regel.supplier_party is not None
            else None
        ),
        "calc_basis": regel.calc_basis,
        "markup_percent": str(regel.markup_percent),
        "min_margin_percent": (
            str(regel.min_margin_percent)
            if regel.min_margin_percent is not None else None
        ),
        "status": regel.status,
        "tiers": [
            {
                "id": str(t.id),
                "min_quantity": str(t.min_quantity),
                "markup_percent": str(t.markup_percent),
                "status": t.status,
            }
            for t in sorted(tiers or [], key=lambda t: t.min_quantity)
        ],
    }


def _scope_kind(regel):
    if regel.article_id is not None:
        return "ARTIKEL"
    if regel.product_group is not None and regel.supplier_party_id is not None:
        return "WARENGRUPPE_LIEFERANT"
    if regel.product_group is not None:
        return "WARENGRUPPE"
    if regel.supplier_party_id is not None:
        return "LIEFERANT"
    return "STANDARD"


# ---------------------------------------------------------------------------
# Öffentliche Auflösung: Artikel → Regel → Verkaufspreis
# ---------------------------------------------------------------------------

def vk_vorschlag(
    article_id, menge=None, *, ek_override=None, listenpreis_override=None,
    regelwerk=None,
):
    """VK-Vorschlag für EINEN Artikel (Artikel-Detail, Angebots-Editor, IDS,
    DATANORM). Gibt None zurück, wenn der Artikel nicht existiert.

    `ek_override` ist ein Einkaufspreis **JE STÜCK** und setzt den gespeicherten
    EK für diese Rechnung außer Kraft — für den IDS-Warenkorb: dort ist der EK des
    Händlers die AKTUELLE Aussage (er steht in derselben Warenkorbzeile, bereits
    durch die PriceBasis geteilt), der gespeicherte `last_purchase_price` womöglich
    veraltet oder gar nicht vorhanden. Ohne den Override trüge die Position einen
    EK aus dem Warenkorb und einen VK aus dem alten Stamm-EK — eine still falsche,
    womöglich negative Marge. Der Override wird NICHT in den Stamm geschrieben
    (dafür gibt es den DATANORM-Import).

    `listenpreis_override` ist analog der **Listenpreis JE STÜCK** aus derselben
    Warenkorbzeile (`OfferPrice`) — die tagesaktuelle Händler-Aussage. Er setzt den
    gespeicherten `list_price` (DATANORM-Stand) außer Kraft, damit die VK-Basis bei
    LISTENPREIS-Formeln (auch die Catch-all-Standardregel) auf dem aktuellen
    Listenpreis steht statt auf dem womöglich veralteten Stammwert. Wie `ek_override`
    wird er NICHT in den Stamm geschrieben.

    `regelwerk` (aus `lade_regelwerk()`) vermeidet N+1, wenn viele Artikel
    hintereinander bepreist werden.

    `sale_price` ist ein String (Decimal, verlustfrei) oder None = unbekannt.
    """
    article = Article.objects.filter(id=article_id).first()
    if article is None:
        return None
    menge = _dec(menge, "menge") if menge is not None else Decimal(1)
    if menge <= 0:
        menge = Decimal(1)

    lief, ek = _bezug([article.id]).get(article.id, (None, None))
    if ek_override is not None:
        # Intern rechnet die Matrix mit EK-Werten JE `price_unit` (so liegen sie im
        # Stamm) und teilt erst in `berechne`. Ein Je-Stück-Override wird daher
        # hochgerechnet, damit die Division ihn nicht ein zweites Mal teilt —
        # price_unit ist stets eine Zehnerpotenz, das ist exakt.
        ek = _dec(ek_override, "Einkaufspreis") * Decimal(article.price_unit or 1)

    # Listenpreis für diese Rechnung: Override (IDS-OfferPrice) je Stück auf die
    # Stamm-Skala (je price_unit) hochgerechnet, exakt wie der EK-Override — sonst
    # der gespeicherte Stammwert.
    list_price = article.list_price
    if listenpreis_override is not None:
        list_price = _dec(listenpreis_override, "Listenpreis") * Decimal(
            article.price_unit or 1
        )
    asp = _standard_variante([article.id]).get(article.id)

    kopf = {
        "article_id": str(article.id),
        "article_number": article.article_number,
        "description": article.description,
        "unit": article.unit,
        "price_unit": article.price_unit,
        "product_group": article.product_group,
        "menge": str(menge),
        "ek": str(ek) if ek is not None else None,
        # Der effektiv verwendete Listenpreis (mit Override der aktuelle OfferPrice,
        # sonst der Stammwert) — nicht der rohe Stammwert, damit das UI die wirklich
        # gerechnete Basis sieht.
        "list_price": (str(list_price) if list_price is not None else None),
        "regel": None,
        "basis_kind": None,
        "basis_amount": None,
        "markup_percent": None,
        "tier_min_quantity": None,
        "min_margin_percent": None,
        "min_margin_applied": False,
        "sale_price": None,
        "quelle": QUELLE_UNBEKANNT,
        "hinweis": "Kein Verkaufspreis ermittelbar (Einkaufspreis unbekannt "
                   "und keine Regel).",
    }

    # 1) Von Hand gesetzter Festpreis am Artikel schlägt jede Regel.
    if asp is not None and asp.fixed_price is not None and asp.price_origin == "MANUELL":
        kopf["sale_price"] = str(asp.fixed_price)
        kopf["quelle"] = QUELLE_FESTPREIS
        kopf["hinweis"] = (
            f"Festpreis am Artikel („{asp.label}“) — von Hand gesetzt, "
            "die Matrix greift hier nicht."
        )
        return kopf

    # 2) Zugewiesene VK-Gruppe am Artikel (bestehende Formel, unverändert).
    if asp is not None and asp.sale_price_group_id is not None and asp.fixed_price is None:
        gruppe = asp.sale_price_group
        roh = list_price if gruppe.calc_basis == "LISTENPREIS" else ek
        basis = _je_stueck(roh, article.price_unit)
        vk = _apply_formula(basis, gruppe)
        kopf["basis_kind"] = gruppe.calc_basis
        kopf["basis_amount"] = str(basis) if basis is not None else None
        kopf["sale_price"] = str(vk) if vk is not None else None
        kopf["quelle"] = QUELLE_VK_GRUPPE if vk is not None else QUELLE_UNBEKANNT
        kopf["hinweis"] = (
            f"VK-Gruppe „{gruppe.name}“ am Artikel zugewiesen."
            if vk is not None
            else f"VK-Gruppe „{gruppe.name}“ am Artikel — Basis unbekannt, "
                 "kein Verkaufspreis."
        )
        return kopf

    # 3) Matrix (auch für Zeilen mit price_origin='MATRIX': ein gespeicherter
    #    Matrixpreis ist eine Ausfertigung der Regel, keine eigene Wahrheit — er
    #    wird live neu gerechnet, damit ein veralteter Wert nie in ein Angebot
    #    rutscht).
    regelwerk = regelwerk if regelwerk is not None else lade_regelwerk()
    regel, res = matrix_preis(
        article, ek=ek, supplier_party_id=lief, menge=menge, regelwerk=regelwerk,
        list_price_override=(
            list_price if listenpreis_override is not None else None
        ),
    )
    if regel is None:
        kopf["hinweis"] = (
            "Keine Aufschlagsregel greift für diesen Artikel "
            "(auch keine Standardregel)."
        )
        return kopf

    kopf["regel"] = _regel_out(regel, regelwerk[1].get(regel.id, []))
    kopf["basis_kind"] = res["basis_kind"]
    kopf["basis_amount"] = (
        str(res["basis_amount"]) if res["basis_amount"] is not None else None
    )
    kopf["markup_percent"] = str(res["markup_percent"])
    kopf["tier_min_quantity"] = (
        str(res["tier_min_quantity"]) if res["tier_min_quantity"] is not None else None
    )
    kopf["min_margin_percent"] = (
        str(res["min_margin_percent"])
        if res["min_margin_percent"] is not None else None
    )
    kopf["min_margin_applied"] = res["min_margin_applied"]
    if res["sale_price"] is None:
        kopf["quelle"] = QUELLE_UNBEKANNT
        basis_name = "Einkaufspreis" if regel.calc_basis == "EK" else "Listenpreis"
        kopf["hinweis"] = (
            f"Regel „{regel.name}“ greift, aber der {basis_name} ist unbekannt "
            "oder 0 — kein Verkaufspreis (nicht 0)."
        )
        return kopf

    kopf["sale_price"] = str(res["sale_price"])
    kopf["quelle"] = QUELLE_MATRIX
    teile = [f"Regel „{regel.name}“ ({_scope_text(regel)})"]
    if res["tier_min_quantity"] is not None:
        teile.append(f"Staffel ab {res['tier_min_quantity']}")
    if res["min_margin_applied"]:
        teile.append("Mindestmarge greift")
    kopf["hinweis"] = ", ".join(teile) + "."
    return kopf


# ---------------------------------------------------------------------------
# Pflege der Regeln
# ---------------------------------------------------------------------------

def _pruefe_scope(article_id, product_group, supplier_party_id):
    if article_id is not None and (product_group or supplier_party_id):
        raise AufschlagsmatrixFehler(
            "Eine Artikelregel ist der Einzelfall und trägt weder Warengruppe "
            "noch Lieferant."
        )
    if article_id is not None:
        ensure_exists(Article, article_id, "Artikel")
    if supplier_party_id is not None:
        ensure_exists(Party, supplier_party_id, "Lieferant")


def _pruefe_werte(calc_basis, markup_percent, min_margin_percent):
    if calc_basis not in CALC_BASES:
        raise AufschlagsmatrixFehler(f"Ungültige Basis '{calc_basis}'.")
    auf = _dec(markup_percent, "Aufschlag")
    if auf <= Decimal(-100):
        raise AufschlagsmatrixFehler("Der Aufschlag muss größer als -100 % sein.")
    marge = None
    if min_margin_percent is not None:
        marge = _dec(min_margin_percent, "Mindestmarge")
        if marge < 0 or marge >= _HUNDERT:
            raise AufschlagsmatrixFehler(
                "Die Mindestmarge muss zwischen 0 und unter 100 % liegen."
            )
    return auf, marge


def _kollision(article_id, product_group, supplier_party_id, ausser=None):
    qs = MarkupRule.objects.filter(status="AKTIV")
    qs = (
        qs.filter(article_id=article_id) if article_id is not None
        else qs.filter(article_id__isnull=True)
    )
    if product_group:
        qs = qs.filter(product_group__iexact=product_group.strip())
    else:
        qs = qs.filter(product_group__isnull=True)
    qs = (
        qs.filter(supplier_party_id=supplier_party_id)
        if supplier_party_id is not None
        else qs.filter(supplier_party_id__isnull=True)
    )
    if ausser is not None:
        qs = qs.exclude(id=ausser)
    return qs.exists()


def create_markup_rule(
    actor_app_user_id,
    *,
    name,
    calc_basis="EK",
    markup_percent,
    min_margin_percent=None,
    article_id=None,
    product_group=None,
    supplier_party_id=None,
):
    """Legt eine Aufschlagsregel an. Je Geltungsbereich nur EINE aktive Regel."""
    if not name or not name.strip():
        raise AufschlagsmatrixFehler("Der Name darf nicht leer sein.")
    product_group = (product_group or "").strip() or None
    _pruefe_scope(article_id, product_group, supplier_party_id)
    auf, marge = _pruefe_werte(calc_basis, markup_percent, min_margin_percent)
    if _kollision(article_id, product_group, supplier_party_id):
        raise AufschlagsmatrixFehler(
            "Für diesen Geltungsbereich besteht bereits eine aktive Regel. "
            "Bestehende Regel ändern oder deaktivieren."
        )
    with business_transaction(actor_app_user_id):
        return MarkupRule.objects.create(
            id=uuid.uuid4(),
            name=name.strip(),
            article_id=article_id,
            product_group=product_group,
            supplier_party_id=supplier_party_id,
            calc_basis=calc_basis,
            markup_percent=auf,
            min_margin_percent=marge,
            status="AKTIV",
            version=1,
        )


def update_markup_rule(
    actor_app_user_id, *, rule_id, name=None, calc_basis=None,
    markup_percent=None, min_margin_percent=...,
):
    """Ändert Name/Basis/Aufschlag/Mindestmarge. Der Geltungsbereich ist
    unveränderlich (DB-Trigger) — Umzielen = neue Regel."""
    regel = MarkupRule.objects.filter(id=rule_id).first()
    if regel is None:
        raise AufschlagsmatrixFehler("Regel nicht gefunden.")
    if name is not None:
        if not name.strip():
            raise AufschlagsmatrixFehler("Der Name darf nicht leer sein.")
        regel.name = name.strip()
    basis = calc_basis if calc_basis is not None else regel.calc_basis
    auf = markup_percent if markup_percent is not None else regel.markup_percent
    marge = (
        regel.min_margin_percent if min_margin_percent is ... else min_margin_percent
    )
    auf, marge = _pruefe_werte(basis, auf, marge)
    regel.calc_basis = basis
    regel.markup_percent = auf
    regel.min_margin_percent = marge
    with business_transaction(actor_app_user_id):
        regel.save(update_fields=[
            "name", "calc_basis", "markup_percent", "min_margin_percent",
            "updated_at",
        ])
    return regel


def set_markup_rule_status(actor_app_user_id, *, rule_id, status):
    """AKTIV ↔ INAKTIV. Reaktivieren scheitert, wenn der Geltungsbereich
    inzwischen von einer anderen aktiven Regel belegt ist."""
    if status not in STATUS:
        raise AufschlagsmatrixFehler(f"Ungültiger Status '{status}'.")
    regel = MarkupRule.objects.filter(id=rule_id).first()
    if regel is None:
        raise AufschlagsmatrixFehler("Regel nicht gefunden.")
    if regel.status == status:
        return regel
    if status == "AKTIV" and _kollision(
        regel.article_id, regel.product_group, regel.supplier_party_id, ausser=regel.id
    ):
        raise AufschlagsmatrixFehler(
            "Für diesen Geltungsbereich ist bereits eine andere Regel aktiv."
        )
    regel.status = status
    with business_transaction(actor_app_user_id):
        regel.save(update_fields=["status", "updated_at"])
    return regel


def set_tiers(actor_app_user_id, *, rule_id, tiers):
    """Setzt die Rabattstaffel einer Regel (ganze Liste auf einmal).

    `tiers`: [{min_quantity, markup_percent}, …]. Nicht mehr genannte Stufen
    werden auf INAKTIV gesetzt (kein Löschen — Schutzstandard).
    """
    regel = MarkupRule.objects.filter(id=rule_id).first()
    if regel is None:
        raise AufschlagsmatrixFehler("Regel nicht gefunden.")

    normiert = {}
    for t in tiers or []:
        menge = _dec(t.get("min_quantity"), "Mengenschwelle").quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if menge <= 0:
            raise AufschlagsmatrixFehler("Eine Mengenschwelle muss größer 0 sein.")
        auf = _dec(t.get("markup_percent"), "Aufschlag").quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if auf <= Decimal(-100):
            raise AufschlagsmatrixFehler("Der Aufschlag muss größer als -100 % sein.")
        if menge in normiert:
            raise AufschlagsmatrixFehler(
                f"Die Mengenschwelle {menge} kommt doppelt vor."
            )
        normiert[menge] = auf

    bestehend = {
        t.min_quantity: t
        for t in MarkupRuleTier.objects.filter(markup_rule_id=rule_id)
    }
    with business_transaction(actor_app_user_id):
        for menge, auf in normiert.items():
            vorhanden = bestehend.get(menge)
            if vorhanden is None:
                MarkupRuleTier.objects.create(
                    id=uuid.uuid4(),
                    markup_rule_id=rule_id,
                    min_quantity=menge,
                    markup_percent=auf,
                    status="AKTIV",
                )
            else:
                vorhanden.markup_percent = auf
                vorhanden.status = "AKTIV"
                vorhanden.save(
                    update_fields=["markup_percent", "status", "updated_at"]
                )
        for menge, vorhanden in bestehend.items():
            if menge not in normiert and vorhanden.status == "AKTIV":
                vorhanden.status = "INAKTIV"
                vorhanden.save(update_fields=["status", "updated_at"])
    return list_tiers(rule_id)


def list_tiers(rule_id):
    return list(
        MarkupRuleTier.objects.filter(markup_rule_id=rule_id, status="AKTIV")
        .order_by("min_quantity")
    )


def markup_rule_out(rule_id):
    """EINE Regel (mit Staffeln) als API-Dict — oder None.

    Nach einem Schreibvorgang wird genau die geänderte Regel nachgeladen; die
    ganze Regeltabelle dafür durchzuziehen wäre Verschwendung.
    """
    regel = (
        MarkupRule.objects.filter(id=rule_id)
        .select_related("supplier_party", "article")
        .first()
    )
    if regel is None:
        return None
    eintrag = _regel_out(regel, _tiers([regel.id]).get(regel.id, []))
    eintrag["article_number"] = (
        regel.article.article_number if regel.article_id else None
    )
    return eintrag


def list_markup_rules(*, status=None):
    """Alle Regeln (mit Staffeln), spezifischste zuerst."""
    qs = MarkupRule.objects.all().select_related("supplier_party", "article")
    if status:
        qs = qs.filter(status=status)
    regeln = list(qs)
    tiers = _tiers([r.id for r in regeln])
    regeln.sort(key=lambda r: (-_rang(r), r.name.casefold()))
    out = []
    for r in regeln:
        eintrag = _regel_out(r, tiers.get(r.id, []))
        eintrag["article_number"] = r.article.article_number if r.article_id else None
        out.append(eintrag)
    return out


def warengruppen():
    """Vorhandene Warengruppen (aus dem Artikelstamm) mit Artikelzahl — die
    Auswahlliste der Regelpflege. Quelle ist `pricing.article.product_group`,
    das der DATANORM-Import aus dem B-Satz (Warengruppe) füllt."""
    from django.db.models import Count

    return [
        {"product_group": row["product_group"], "anzahl": row["anzahl"]}
        for row in Article.objects.exclude(product_group__isnull=True)
        .exclude(product_group="")
        .values("product_group")
        .annotate(anzahl=Count("id"))
        .order_by("product_group")
    ]


# ---------------------------------------------------------------------------
# Massenpflege: VK der betroffenen Artikel neu rechnen (Vorschau → Anwenden)
# ---------------------------------------------------------------------------
# Ausdrücklich KEIN stiller Automatismus. Der Vorgang schreibt ausschließlich
# `article_sale_price`-Zeilen mit price_origin='MATRIX' fort (bzw. legt sie an)
# und lässt von Hand gesetzte Preise sowie am Artikel zugewiesene VK-Gruppen
# unangetastet. Er schreibt NIE in `pricing.article` und NIE in eine
# Belegposition.
#
# `dry_run=True` (Vorschau) und `dry_run=False` (Anwenden) durchlaufen denselben
# Code — die Vorschau kann also nicht von dem abweichen, was danach passiert.

GRUND_MANUELL = "Von Hand gesetzter Festpreis — bleibt unverändert."
GRUND_VK_GRUPPE = "Eigene VK-Gruppe am Artikel — rechnet bereits selbst."
GRUND_KEINE_REGEL = "Keine Aufschlagsregel greift."
GRUND_UNBEKANNT = "Einkaufspreis/Listenpreis unbekannt — kein Preis (nicht 0)."


def _artikel_auswahl(product_group=None, supplier_party_id=None, nur_aktive=True):
    qs = Article.objects.all()
    if nur_aktive:
        qs = qs.filter(status="AKTIV")
    if product_group:
        qs = qs.filter(product_group__iexact=product_group.strip())
    if supplier_party_id:
        heute = date.today()
        refs = (
            ArticleSupplierReference.objects.filter(
                supplier_party_id=supplier_party_id, valid_from__lte=heute
            )
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=heute))
            .values("article_id")
        )
        qs = qs.filter(id__in=refs)
    return qs.order_by("article_number")


def massenpflege(
    actor_app_user_id,
    *,
    product_group=None,
    supplier_party_id=None,
    dry_run=True,
    ab_artikelnummer=None,
):
    """Rechnet die Verkaufspreise der gewählten Artikel neu — in Abschnitten.

    Ein Aufruf verarbeitet höchstens MAX_MASSENPFLEGE Artikel (nach
    `article_number` sortiert). Sind es mehr, kommt `weiter` (die letzte
    verarbeitete Artikelnummer) zurück; der nächste Aufruf setzt mit
    `ab_artikelnummer=weiter` genau dort fort. So ist auch eine Warengruppe mit
    100.000 Katalogartikeln pflegbar — der Fall, für den die Matrix gebaut ist.
    Ein harter 422 („zu viele Artikel") wäre für genau diesen Fall eine Sackgasse.

    Rückgabe: Zahlen (angelegt/aktualisiert/unverändert/übersprungen) für DIESEN
    Abschnitt, bis zu MAX_VORSCHAU_ZEILEN Beispielzeilen („Preis von … auf …"),
    `artikel_gesamt` (die ganze Auswahl) und `weiter`.

    Die Staffel greift hier NICHT: die Massenpflege schreibt den Basispreis des
    Stamms (Menge 1). Eine Mengenstaffel ist eine Aussage über eine konkrete
    Belegposition, nicht über den Stammpreis.
    """
    if product_group is not None:
        product_group = product_group.strip() or None
    if supplier_party_id is not None:
        ensure_exists(Party, supplier_party_id, "Lieferant")

    qs = _artikel_auswahl(product_group, supplier_party_id)
    gesamt = qs.count()
    if ab_artikelnummer:
        qs = qs.filter(article_number__gt=ab_artikelnummer)

    regelwerk = lade_regelwerk()
    regeln, _tier_index = regelwerk

    ergebnis = {
        "product_group": product_group,
        "supplier_party_id": str(supplier_party_id) if supplier_party_id else None,
        "dry_run": dry_run,
        "artikel_gesamt": gesamt,
        "verarbeitet": 0,
        "angelegt": 0,
        "aktualisiert": 0,
        "unveraendert": 0,
        "uebersprungen": 0,
        "zeilen": [],
        "weiter": None,
    }

    # Ein Artikel mehr laden, als verarbeitet wird: so ist ohne zweite Query
    # bekannt, ob noch etwas folgt (Fortsetzungspunkt).
    block_gesamt = list(qs[: MAX_MASSENPFLEGE + 1])
    mehr = len(block_gesamt) > MAX_MASSENPFLEGE
    block_gesamt = block_gesamt[:MAX_MASSENPFLEGE]

    def _schreiben(neu, aenderungen):
        """Ein Stapel, zwei Statements: bulk_create + bulk_update."""
        if not neu and not aenderungen:
            return
        with business_transaction(actor_app_user_id):
            if neu:
                ArticleSalePrice.objects.bulk_create(neu)
            if aenderungen:
                ArticleSalePrice.objects.bulk_update(
                    aenderungen, ["fixed_price", "price_origin"]
                )

    for block in _chunks(block_gesamt, _BATCH):
        ids = [a.id for a in block]
        bezug = _bezug(ids)
        standard = _standard_variante(ids)
        neu, aenderungen = [], []

        for artikel in block:
            ergebnis["verarbeitet"] += 1
            lief, ek = bezug.get(artikel.id, (None, None))
            asp = standard.get(artikel.id)

            if asp is not None and asp.fixed_price is not None \
                    and asp.price_origin == "MANUELL":
                _zeile(ergebnis, artikel, None, None, "UEBERSPRUNGEN", GRUND_MANUELL)
                continue
            if asp is not None and asp.sale_price_group_id is not None \
                    and asp.fixed_price is None:
                _zeile(ergebnis, artikel, None, None, "UEBERSPRUNGEN",
                       GRUND_VK_GRUPPE)
                continue

            regel, res = matrix_preis(
                artikel,
                ek=ek,
                supplier_party_id=lief,
                menge=Decimal(1),  # Stammpreis: keine Mengenstaffel
                regelwerk=(regeln, {}),  # ohne Staffeln
            )
            if regel is None:
                _zeile(ergebnis, artikel, None, None, "UEBERSPRUNGEN",
                       GRUND_KEINE_REGEL)
                continue

            vk = res["sale_price"]
            if vk is None:
                _zeile(ergebnis, artikel, None, None, "UEBERSPRUNGEN",
                       GRUND_UNBEKANNT)
                continue

            alt = asp.fixed_price if asp is not None else None
            if asp is None:
                aktion = "ANLEGEN"
            elif alt == vk:
                aktion = "UNVERAENDERT"
            else:
                aktion = "AKTUALISIEREN"

            _zeile(ergebnis, artikel, alt, vk, aktion, regel_name=regel.name)
            if aktion == "UNVERAENDERT" or dry_run:
                continue

            if asp is None:
                neu.append(
                    ArticleSalePrice(
                        id=uuid.uuid4(),
                        article_id=artikel.id,
                        label="Aufschlagsmatrix",
                        sale_price_group_id=None,
                        fixed_price=vk,
                        is_standard=True,
                        price_origin="MATRIX",
                    )
                )
            else:
                # Die Zeile ist bereits geladen — kein erneutes SELECT je Artikel.
                asp.fixed_price = vk
                asp.price_origin = "MATRIX"
                aenderungen.append(asp)

        if not dry_run:
            _schreiben(neu, aenderungen)

    if mehr and block_gesamt:
        ergebnis["weiter"] = block_gesamt[-1].article_number
    return ergebnis


def _chunks(iterable, groesse):
    block = []
    for item in iterable:
        block.append(item)
        if len(block) >= groesse:
            yield block
            block = []
    if block:
        yield block


_AKTION_ZAEHLER = {
    "ANLEGEN": "angelegt",
    "AKTUALISIEREN": "aktualisiert",
    "UNVERAENDERT": "unveraendert",
    "UEBERSPRUNGEN": "uebersprungen",
}


def _zeile(ergebnis, artikel, alt, neu, aktion, grund=None, regel_name=None):
    """Zählt die Aktion und legt (bis zur Kappungsgrenze) eine Beispielzeile ab.

    `grund` steht nur bei UEBERSPRUNGEN (warum nichts passiert), `regel_name` nur
    bei den gerechneten Zeilen (welche Regel den Preis macht).
    """
    ergebnis[_AKTION_ZAEHLER[aktion]] += 1
    if len(ergebnis["zeilen"]) < MAX_VORSCHAU_ZEILEN:
        ergebnis["zeilen"].append(
            {
                "article_id": str(artikel.id),
                "article_number": artikel.article_number,
                "description": artikel.description,
                "product_group": artikel.product_group,
                "alt": str(alt) if alt is not None else None,
                "neu": str(neu) if neu is not None else None,
                "aktion": aktion,
                "grund": grund,
                "regel_name": regel_name,
            }
        )
