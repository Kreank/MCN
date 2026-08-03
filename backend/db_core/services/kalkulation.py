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
    Assembly,
    AssemblyComponent,
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


# ---------------------------------------------------------------------------
# Leistungen (Stücklisten): Material + Lohn ergeben Preis
# ---------------------------------------------------------------------------
def _stunden(minuten):
    """Minuten → Stunden als exakter Decimal-Quotient (keine float-Näherung)."""
    return Decimal(minuten) / Decimal(60)


def assembly_kalkulation(assembly_id):
    """Kalkuliert eine Leistung aus ihrer Stückliste — je EINER Leistungseinheit.

    Bis hierher war eine Leistung eine Stückliste ohne Preis: Der Angebots-Editor
    übernahm sie als Pauschalposition mit leerem Einzelpreis und der Bitte, den
    Preis von Hand zu ergänzen. Genau das ist der Zweck einer Stückliste — sie
    soll den Preis ERGEBEN, nicht ihn offenlassen.

    Die Mengen der Stückliste gelten je EINER Leistungseinheit — das Ergebnis
    ist deshalb ein Einzelpreis. Skaliert wird nicht hier, sondern über die
    Menge der Belegposition, die diese Leistung übernimmt.

    * **Material** — Verkaufspreis über `vk_vorschlag`, also über exakt denselben
      Weg, den der Angebots-Editor für einen einzelnen Artikel geht. Sonst
      bekäme dieselbe Ware zwei Preise, je nachdem ob sie einzeln oder als Teil
      einer Leistung ins Angebot kommt. Die hinterlegte Menge geht dabei als
      Staffelmenge mit ein: Wer zwölf Ziegel verbaut, kauft zwölf Ziegel.
      Einkaufsseitig zählt der EK des primären Lieferantenbezugs — er kommt aus
      derselben Abfrage wie der VK, damit beide Seiten denselben Bezug sehen.
    * **Lohn** — `hourly_rate` der Lohngruppe für den Verkauf, `cost_rate` für
      die Kosten. Ist `cost_rate` NULL (Kosten unbekannt, Migration 0034), wird
      **konservativ mit dem Verrechnungssatz** gerechnet: Lieber eine zu kleine
      als eine erfundene Marge.

    **Unbekannte Preise werden nicht als 0 gerechnet.** Fehlt einem Material der
    VK, fließt die Position nicht in die Summe ein und die Leistung ist als
    `vollstaendig=False` markiert. Eine Summe, die eine fehlende Position
    stillschweigend als kostenlos führt, wäre schlimmer als gar keine Summe —
    sie sähe aus wie ein Preis.

    `lohnanteil_vk` ist der Lohnanteil am Verkaufspreis (§ 35a EStG): Er wird
    hier mitgeliefert, damit eine ins Angebot übernommene Leistung ihren
    steuerlich absetzbaren Anteil kennt, statt ihn aus der Positionsart zu raten.

    Rein lesend. Gibt None zurück, wenn die Leistung nicht existiert.
    """
    # Lokaler Import: aufschlagsmatrix importiert kalkulation nicht — der Zyklus
    # entstünde erst durch einen Modul-Import auf dieser Ebene.
    from db_core.services import aufschlagsmatrix as matrix_service

    assembly = Assembly.objects.filter(id=assembly_id).first()
    if assembly is None:
        return None

    komponenten = list(
        AssemblyComponent.objects.filter(assembly_id=assembly_id)
        .select_related("article", "wage_group")
        .order_by("position")
    )
    # Das Regelwerk EINMAL laden und durchreichen: sonst zieht jede
    # Materialposition die komplette Regeltabelle samt Staffeln erneut (N+1).
    regelwerk = (
        matrix_service.lade_regelwerk()
        if any(k.article_id is not None for k in komponenten)
        else None
    )

    positionen = []
    material_ek = material_vk = Decimal(0)
    lohn_ek = lohn_vk = Decimal(0)
    minuten_gesamt = Decimal(0)
    vollstaendig = True
    # Getrennt gefuehrt: Ein fehlender EINKAUFSpreis laesst den Verkaufspreis
    # unberuehrt, macht aber die Marge zu schoen — eine Position, deren Kosten
    # unbekannt sind, saehe in der Summe wie eine kostenlose aus.
    kosten_vollstaendig = True

    for k in komponenten:
        if k.article_id is not None:
            artikel = k.article
            vorschlag = matrix_service.vk_vorschlag(
                k.article_id, menge=k.quantity, regelwerk=regelwerk
            )
            # Den EK aus derselben Antwort nehmen statt ihn erneut zu holen:
            # `vk_vorschlag` hat den primaeren Lieferantenbezug ohnehin geladen,
            # und beide Seiten muessen denselben Bezug sehen. Der Wert gilt je
            # `price_unit` — wie im Stamm; die Umrechnung passiert hier.
            ek_je = _je_stueck(
                Decimal(vorschlag["ek"]) if vorschlag and vorschlag["ek"] else None,
                artikel.price_unit,
            )
            vk_je = (
                Decimal(vorschlag["sale_price"])
                if vorschlag and vorschlag["sale_price"] is not None
                else None
            )
            ek_summe = _round2(ek_je * k.quantity) if ek_je is not None else None
            vk_summe = _round2(vk_je * k.quantity) if vk_je is not None else None
            if ek_summe is not None:
                material_ek += ek_summe
            else:
                kosten_vollstaendig = False
            if vk_summe is not None:
                material_vk += vk_summe
            else:
                vollstaendig = False
            positionen.append(
                {
                    "position": k.position,
                    "kind": "MATERIAL",
                    "description": artikel.description,
                    "reference": artikel.article_number,
                    "quantity": str(k.quantity),
                    "unit": artikel.unit,
                    "minutes": None,
                    "ek_je_einheit": str(ek_je) if ek_je is not None else None,
                    "vk_je_einheit": str(vk_je) if vk_je is not None else None,
                    "ek_summe": str(ek_summe) if ek_summe is not None else None,
                    "vk_summe": str(vk_summe) if vk_summe is not None else None,
                    "hinweis": (vorschlag or {}).get("hinweis"),
                }
            )
            continue

        gruppe = k.wage_group
        stunden = _stunden(k.minutes)
        minuten_gesamt += k.minutes
        vk_je = gruppe.hourly_rate
        # cost_rate NULL = Kosten unbekannt: konservativ mit dem Verrechnungssatz
        # rechnen (Migration 0034), statt eine zu schoene Marge auszuweisen.
        kosten_unbekannt = gruppe.cost_rate is None
        if kosten_unbekannt:
            kosten_vollstaendig = False
        ek_je = gruppe.hourly_rate if kosten_unbekannt else gruppe.cost_rate
        ek_summe = _round2(ek_je * stunden)
        vk_summe = _round2(vk_je * stunden)
        lohn_ek += ek_summe
        lohn_vk += vk_summe
        positionen.append(
            {
                "position": k.position,
                "kind": "LOHN",
                "description": gruppe.name,
                "reference": None,
                "quantity": None,
                "unit": "h",
                "minutes": str(k.minutes),
                "ek_je_einheit": str(ek_je),
                "vk_je_einheit": str(vk_je),
                "ek_summe": str(ek_summe),
                "vk_summe": str(vk_summe),
                "hinweis": (
                    "Kostensatz der Lohngruppe unbekannt — es wird konservativ "
                    "mit dem Verrechnungssatz gerechnet."
                    if kosten_unbekannt
                    else None
                ),
            }
        )

    ek_gesamt = _round2(material_ek + lohn_ek)
    vk_gesamt = _round2(material_vk + lohn_vk)
    # Die Marge braucht BEIDE Seiten vollstaendig. Fehlt auch nur ein
    # Einkaufspreis, waere sie zu hoch — und zwar genau in der Zahl, auf die
    # jemand schaut, um zu entscheiden, ob sich der Auftrag lohnt.
    marge = None
    if vollstaendig and kosten_vollstaendig and vk_gesamt > 0:
        marge = _round2((vk_gesamt - ek_gesamt) / vk_gesamt * Decimal(100))

    return {
        "assembly_id": str(assembly.id),
        "assembly_number": assembly.assembly_number,
        "name": assembly.name,
        "unit": assembly.unit,
        "positionen": positionen,
        "material_ek": str(_round2(material_ek)),
        "material_vk": str(_round2(material_vk)),
        "lohn_ek": str(_round2(lohn_ek)),
        "lohn_vk": str(_round2(lohn_vk)),
        "minuten_gesamt": str(minuten_gesamt),
        "ek_gesamt": str(ek_gesamt),
        "vk_gesamt": str(vk_gesamt),
        # § 35a: der Lohnanteil am Verkaufspreis EINER Leistungseinheit.
        "lohnanteil_vk": str(_round2(lohn_vk)),
        "marge_prozent": str(marge) if marge is not None else None,
        "vollstaendig": vollstaendig,
        "kosten_vollstaendig": kosten_vollstaendig,
    }
