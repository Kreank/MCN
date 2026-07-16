"""Artikel-Service: Artikel, Lohngruppen und Leistungen (Stücklisten) anlegen.

Dieser Slice ist im UI lesend (Liste/Detail); die Anlage-Funktionen dienen dem
Seed und späteren Schreib-Pfaden. Alle Writes laufen über business_transaction.
Nummern (article_number/assembly_number) haben keine DB-Automatik und müssen
gesetzt werden. Kein Löschen — nur status AKTIV/INAKTIV.
"""
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from django.db.models import Max, Q

from db_core.db_context import business_transaction
from db_core.models import (
    Article,
    ArticleSalePrice,
    ArticleSupplierReference,
    Assembly,
    AssemblyComponent,
    CostCenter,
    SalePriceGroup,
    TaxCode,
    WageGroup,
)
from db_core.services._validation import (
    ensure_all_exist,
    ensure_exists,
    ensure_party_usable,
)

SALE_CALC_BASES = ("EK", "LISTENPREIS")
SALE_OPERATORS = ("AUFSCHLAG", "ABSCHLAG")
PRICE_UNITS = (1, 10, 100, 1000)


def _ensure_tax_code(code):
    """tax_code muss in der Belegpositions-Codeliste (invoicing.tax_code) stehen."""
    if code is None:
        return None
    code = str(code).strip()
    if not code:
        return None
    if not TaxCode.objects.filter(code=code).exists():
        raise ValueError(f"Unbekannter Steuercode '{code}'.")
    return code


def _ensure_cost_center(cost_center_id):
    """Kostenstelle muss existieren und aktiv sein (archivierte sind gesperrt)."""
    if cost_center_id is None:
        return None
    cc = CostCenter.objects.filter(id=cost_center_id).values("active").first()
    if cc is None:
        raise ValueError(f"Kostenstelle {cost_center_id} existiert nicht.")
    if not cc["active"]:
        raise ValueError("Die Kostenstelle ist archiviert und nicht mehr wählbar.")
    return cost_center_id


def _positiv(wert, feld):
    """Menge > 0 (Mindestbestellmenge/Mengenstaffel). None bleibt None."""
    if wert is None:
        return None
    d = Decimal(str(wert))
    if d <= 0:
        raise ValueError(f"{feld} muss größer als 0 sein.")
    return d


def _price_unit(wert):
    if wert is None:
        return None
    if int(wert) not in PRICE_UNITS:
        raise ValueError("Preiseinheit muss 1, 10, 100 oder 1000 sein.")
    return int(wert)


def _delivery_time(wert):
    if wert is None:
        return None
    d = int(wert)
    if d < 0:
        raise ValueError("Lieferzeit (Tage) darf nicht negativ sein.")
    return d


# ---------------------------------------------------------------------------
# Artikelsuche mit Hero-Operatoren (`+` UND, `|` ODER, `*` Platzhalter)
# ---------------------------------------------------------------------------
# Durchsucht werden Artikelnummer, Bezeichnung und Matchcode (Hero-Kurzsuche).
# `|` trennt ODER-Gruppen, innerhalb einer Gruppe `+` als UND, `*` als
# Platzhalter INNERHALB eines Terms.
#
# Sicherheit (ReDoS/Injection): ein Term MIT `*` wird NICHT als roher User-Regex
# ausgewertet. Nur `*` ist Sonderzeichen; es wird zum Zerlegen des Terms in
# Literalsegmente benutzt. Jedes Segment maskiert `re.escape` vollständig (alle
# regex-relevanten Zeichen — `.`, `(`, `\`, `[`, `+`, `?` … werden literal), die
# Segmente werden mit `.*` verbunden. Der Nutzer kann so keine eigenen Quantoren/
# Gruppen einschleusen (`(a+)+` o. Ä.); es entsteht nur ein Muster der Form
# `literal.*literal`. Die Postgres-Regex-Engine (Spencer-NFA/DFA, kein reines
# Backtracking) wertet solche Muster ohne katastrophales Backtracking aus.
# `%`/`_` sind hier keine Sonderzeichen (iregex, nicht LIKE) und bleiben literal.

_SEARCH_FIELDS = ("article_number", "description", "matchcode")


def _wildcard_regex(term):
    """Übersetzt einen `*`-Term in ein sicheres, unverankertes iregex-Muster.

    Die Literalsegmente zwischen den `*` werden mit re.escape maskiert und mit
    `.*` verbunden. Kein rohes User-Regex — nur `*` ist Sonderzeichen.
    """
    return ".*".join(re.escape(seg) for seg in term.split("*"))


def _term_q(term):
    """Ein einzelner Suchterm → Q über die drei Suchfelder (Feld-OR).

    Ohne `*`: icontains (nutzt den Trigramm-Index auf description). Mit `*`:
    iregex über das sichere Platzhaltermuster. Leerer Term → None.
    """
    term = term.strip()
    if not term:
        return None
    if "*" in term:
        lookup, value = "iregex", _wildcard_regex(term)
    else:
        lookup, value = "icontains", term
    feld_q = Q()
    for field in _SEARCH_FIELDS:
        feld_q |= Q(**{f"{field}__{lookup}": value})
    return feld_q


def build_article_search_q(needle):
    """Baut aus der Hero-Suchsyntax ein Django-`Q` über Nummer/Bezeichnung/Matchcode.

    `|` trennt ODER-Gruppen, innerhalb einer Gruppe verknüpft `+` mit UND, `*`
    ist Platzhalter innerhalb eines Terms. Leere Terme/Gruppen werden ignoriert.
    Eine leere Suche (oder eine, die nur aus Trennzeichen besteht) ergibt None —
    dann wird nicht gefiltert. Eine Suche ohne Operatoren verhält sich wie ein
    einzelner icontains-Term über die drei Felder (rückwärtskompatibel).
    """
    if not needle:
        return None
    needle = needle.strip()
    if not needle:
        return None

    or_q = None
    for group in needle.split("|"):
        and_q = None
        for term in group.split("+"):
            term_q = _term_q(term)
            if term_q is None:
                continue
            and_q = term_q if and_q is None else (and_q & term_q)
        if and_q is None:
            continue
        or_q = and_q if or_q is None else (or_q | and_q)
    return or_q


ARTICLE_LINE_TYPES = (
    "MATERIAL",
    "ARBEITSZEIT",
    "PAUSCHALE",
    "FREMDLEISTUNG",
    "FAHRT",
    "ZUSCHLAG",
)


def create_wage_group(actor_app_user_id, *, name, hourly_rate, kind="LOHN", cost_rate=None):
    if kind not in ("LOHN", "MASCHINE"):
        raise ValueError("kind muss LOHN oder MASCHINE sein.")
    with business_transaction(actor_app_user_id):
        wg = WageGroup.objects.create(
            id=uuid.uuid4(),
            name=name.strip(),
            kind=kind,
            hourly_rate=hourly_rate,
            cost_rate=cost_rate,
            status="AKTIV",
            version=1,
        )
    return wg


def create_article(
    actor_app_user_id,
    *,
    article_number,
    description,
    unit,
    line_type="MATERIAL",
    list_price=None,
    long_description=None,
    manufacturer_name=None,
    manufacturer_number=None,
    manufacturer_type=None,
    product_group=None,
    matchcode=None,
    min_order_quantity=None,
    quantity_step=None,
    delivery_time_days=None,
    tax_code=None,
    cost_center_id=None,
    price_unit=None,
):
    """Legt einen Artikel (Status AKTIV) an."""
    if line_type not in ARTICLE_LINE_TYPES:
        raise ValueError(
            f"Ungültiger line_type '{line_type}'. "
            f"Erlaubt: {', '.join(ARTICLE_LINE_TYPES)}."
        )
    for feld, wert in (("article_number", article_number), ("description", description), ("unit", unit)):
        if not wert or not str(wert).strip():
            raise ValueError(f"{feld} darf nicht leer sein.")
    # Fremdschlüssel/Wertebereiche vorab prüfen → klarer 422 statt IntegrityError.
    tax_code = _ensure_tax_code(tax_code)
    cost_center_id = _ensure_cost_center(cost_center_id)
    min_order_quantity = _positiv(min_order_quantity, "Mindestbestellmenge")
    quantity_step = _positiv(quantity_step, "Mengenstaffel")
    delivery_time_days = _delivery_time(delivery_time_days)
    price_unit = _price_unit(price_unit)
    with business_transaction(actor_app_user_id):
        article = Article.objects.create(
            id=uuid.uuid4(),
            article_number=article_number.strip(),
            description=description.strip(),
            unit=unit.strip(),
            line_type=line_type,
            list_price=list_price,
            long_description=long_description,
            manufacturer_name=manufacturer_name,
            manufacturer_number=manufacturer_number,
            manufacturer_type=manufacturer_type,
            product_group=product_group,
            matchcode=matchcode,
            min_order_quantity=min_order_quantity,
            quantity_step=quantity_step,
            delivery_time_days=delivery_time_days,
            tax_code_id=tax_code,
            cost_center_id=cost_center_id,
            price_unit=price_unit if price_unit is not None else 1,
            status="AKTIV",
            version=1,
        )
    return article


ARTICLE_STATUS = ("AKTIV", "INAKTIV")

# Felder, die der Artikeldialog ändern darf. `status` läuft über
# set_article_status, die Preise der Lieferanten über den Import — sie sind nach
# der Anlage unveränderlich (trg_supplier_ref_protect).
ARTICLE_UPDATE_FIELDS = (
    "article_number",
    "description",
    "long_description",
    "unit",
    "line_type",
    "list_price",
    "gtin",
    "manufacturer_name",
    "manufacturer_number",
    "manufacturer_type",
    "product_group",
    "matchcode",
    "min_order_quantity",
    "quantity_step",
    "delivery_time_days",
    "tax_code",
    "cost_center_id",
    "price_unit",
)


def update_article(actor_app_user_id, *, article_id, **felder):
    """Ändert einen Artikel. Nur Felder aus der Whitelist.

    Jede Änderung landet über `trg_article_audit` in `audit.audit_entry` mit
    vollständigem Vorher/Nachher-Zustand — daraus speist sich der Historie-Reiter.

    Die Artikelnummer ist änderbar (wie im Hero-Vorbild), aber eindeutig: ein
    Duplikat ergibt einen klaren 422 statt eines IntegrityError.
    """
    unbekannt = set(felder) - set(ARTICLE_UPDATE_FIELDS)
    if unbekannt:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")

    article = Article.objects.filter(id=article_id).first()
    if article is None:
        raise ValueError("Artikel nicht gefunden.")

    # werte: attname -> Wert (für setattr); spalten: Feldnamen (für update_fields).
    # Für die Fremdschlüssel weichen beide ab: tax_code -> attname tax_code_id,
    # cost_center_id -> Feldname cost_center.
    werte = {}
    spalten = []

    def _merke(attname, feldname, wert):
        werte[attname] = wert
        spalten.append(feldname)

    for feld, wert in felder.items():
        if feld in ("article_number", "description", "unit"):
            # NOT NULL + CHECK btrim(...) <> '' — leer wäre ein 500.
            if wert is None or not str(wert).strip():
                raise ValueError(f"{feld} darf nicht leer sein.")
            _merke(feld, feld, str(wert).strip())
        elif feld == "line_type":
            if wert not in ARTICLE_LINE_TYPES:
                raise ValueError(
                    f"Ungültiger line_type '{wert}'. "
                    f"Erlaubt: {', '.join(ARTICLE_LINE_TYPES)}."
                )
            _merke(feld, feld, wert)
        elif feld == "gtin":
            wert = (wert or "").strip() or None
            if wert is not None and not _gtin_gueltig(wert):
                raise ValueError(
                    "Ungültige GTIN/EAN: erwartet 8, 12, 13 oder 14 Ziffern mit "
                    "korrekter Prüfziffer."
                )
            _merke(feld, feld, wert)
        elif feld == "list_price":
            if wert is not None and Decimal(str(wert)) < 0:
                raise ValueError("list_price darf nicht negativ sein.")
            _merke(feld, feld, wert)
        elif feld == "min_order_quantity":
            _merke(feld, feld, _positiv(wert, "Mindestbestellmenge"))
        elif feld == "quantity_step":
            _merke(feld, feld, _positiv(wert, "Mengenstaffel"))
        elif feld == "delivery_time_days":
            _merke(feld, feld, _delivery_time(wert))
        elif feld == "price_unit":
            pu = _price_unit(wert)
            if pu is None:
                raise ValueError("Preiseinheit darf nicht leer sein.")
            _merke(feld, feld, pu)
        elif feld == "tax_code":
            _merke("tax_code_id", "tax_code", _ensure_tax_code(wert))
        elif feld == "cost_center_id":
            _merke("cost_center_id", "cost_center", _ensure_cost_center(wert))
        else:
            _merke(feld, feld, (str(wert).strip() or None) if wert is not None else None)

    if "article_number" in werte:
        vergeben = (
            Article.objects.filter(article_number=werte["article_number"])
            .exclude(id=article.id)
            .exists()
        )
        if vergeben:
            raise ValueError(
                f"Artikelnummer '{werte['article_number']}' ist bereits vergeben."
            )

    if not werte:
        return article
    for attname, wert in werte.items():
        setattr(article, attname, wert)
    with business_transaction(actor_app_user_id):
        article.save(update_fields=spalten + ["updated_at"])
    article.refresh_from_db()
    return article


def _gtin_gueltig(gtin):
    """GTIN-8/12/13/14 mit Prüfziffer (Modulo-10, Gewichte 3 und 1).

    Der DB-CHECK prüft nur die Länge und dass es Ziffern sind. Eine falsche
    Prüfziffer ist aber ein Tippfehler, kein gültiger Code — und im echten
    DATANORM-Bestand hatten 13.464 von 13.465 GTINs eine korrekte Prüfziffer.
    """
    if not gtin.isdigit() or len(gtin) not in (8, 12, 13, 14):
        return False
    ziffern = [int(z) for z in gtin]
    pruef = ziffern[-1]
    rest = ziffern[:-1][::-1]
    summe = sum(z * (3 if i % 2 == 0 else 1) for i, z in enumerate(rest))
    return (10 - summe % 10) % 10 == pruef


def set_article_status(actor_app_user_id, *, article_id, status):
    """Setzt den Artikelstatus (AKTIV/INAKTIV).

    Es gibt kein Löschen: `trg_article_no_delete` verbietet es physisch, und
    Belegpositionen verweisen über `source_article_id` auf den Artikel — ein
    gelöschter Artikel zerrisse die Historie. Ausrangiertes Material wird deshalb
    auf INAKTIV gesetzt; die Artikelsuche blendet es dann aus, bestehende Belege
    behalten ihren Bezug.
    """
    if status not in ARTICLE_STATUS:
        raise ValueError(
            f"Ungültiger Status '{status}'. Erlaubt: {', '.join(ARTICLE_STATUS)}."
        )
    article = Article.objects.filter(id=article_id).first()
    if article is None:
        raise ValueError("Artikel nicht gefunden.")
    if article.status == status:
        return article
    article.status = status
    with business_transaction(actor_app_user_id):
        article.save(update_fields=["status", "updated_at"])
    article.refresh_from_db()
    return article


def _prepare_component(comp, pos):
    """Validiert eine Stücklisten-Position und liefert die DB-Spaltenwerte.

    Genau eine Seite je Position: Material (article_id + quantity > 0) ODER Lohn
    (wage_group_id + minutes > 0), nie beides (spiegelt den DB-XOR-CHECK). Der
    Pflicht-/Positiv-Check hier verhindert, dass ein leerer/0-Betrag erst als
    DB-CHECK (500) statt als sauberer 422 auffällt. Fehlermeldungen nennen die
    fachliche Positionsnummer, keine Tabellen-/Spaltennamen.
    """
    is_material = comp.get("article_id") is not None
    is_labour = comp.get("wage_group_id") is not None
    if is_material == is_labour:
        raise ValueError(
            f"Position {pos}: genau eines von Material (article_id+quantity) "
            "oder Lohn (wage_group_id+minutes) angeben."
        )
    if is_material and not (comp.get("quantity") and Decimal(str(comp["quantity"])) > 0):
        raise ValueError(f"Position {pos}: quantity (> 0) Pflicht für Material.")
    if is_labour and not (comp.get("minutes") and Decimal(str(comp["minutes"])) > 0):
        raise ValueError(f"Position {pos}: minutes (> 0) Pflicht für Lohn.")
    return {
        "article_id": comp.get("article_id"),
        "wage_group_id": comp.get("wage_group_id"),
        "quantity": comp.get("quantity") if is_material else None,
        "minutes": comp.get("minutes") if is_labour else None,
        "note": comp.get("note"),
    }


def create_assembly(
    actor_app_user_id,
    *,
    assembly_number,
    name,
    unit,
    description=None,
    components=None,
):
    """Legt eine Leistung mit Stückliste an.

    components: Liste von dicts, je entweder {'article_id', 'quantity'} (Material)
    oder {'wage_group_id', 'minutes'} (Lohn) — nie beides (DB-XOR-CHECK).
    """
    for feld, wert in (("assembly_number", assembly_number), ("name", name), ("unit", unit)):
        if not wert or not str(wert).strip():
            raise ValueError(f"{feld} darf nicht leer sein.")
    components = components or []
    # Artikel-/Lohngruppen-FKs je Komponente vorab prüfen — mit je EINER Query
    # (kein N+1), damit ein unbekannter FK ein klarer 422 statt IntegrityError wird.
    ensure_all_exist(
        Article, [c.get("article_id") for c in components], "Artikel"
    )
    ensure_all_exist(
        WageGroup, [c.get("wage_group_id") for c in components], "Lohngruppe"
    )
    prepared = [_prepare_component(comp, pos) for pos, comp in enumerate(components, start=1)]
    with business_transaction(actor_app_user_id):
        assembly = Assembly.objects.create(
            id=uuid.uuid4(),
            assembly_number=assembly_number.strip(),
            name=name.strip(),
            unit=unit.strip(),
            description=description,
            status="AKTIV",
            version=1,
        )
        for pos, cols in enumerate(prepared, start=1):
            AssemblyComponent.objects.create(
                id=uuid.uuid4(), assembly_id=assembly.id, position=pos, **cols
            )
    return assembly


def add_assembly_components(actor_app_user_id, *, assembly_id, components):
    """Hängt einer bestehenden Leistung weitere Positionen an.

    Die Stückliste ist nach der Anlage änderbar — pricing.assembly_component trägt
    keinen Unveränderlichkeits-/Einfrier-Trigger (Migration 0033 hat nur
    updated_at/Audit/kein-TRUNCATE, aber keine INSERT-Sperre). Neue Positionen
    werden hinter der höchsten bestehenden Positionsnummer eingereiht; bestehende
    bleiben unberührt. FKs werden vorab je EINER Query geprüft (kein N+1, klarer
    422 statt IntegrityError).
    """
    ensure_exists(Assembly, assembly_id, "Leistung")
    if not components:
        raise ValueError("Mindestens eine Position ist anzugeben.")
    ensure_all_exist(Article, [c.get("article_id") for c in components], "Artikel")
    ensure_all_exist(WageGroup, [c.get("wage_group_id") for c in components], "Lohngruppe")
    current_max = (
        AssemblyComponent.objects.filter(assembly_id=assembly_id).aggregate(m=Max("position"))["m"]
        or 0
    )
    prepared = [
        _prepare_component(comp, current_max + offset)
        for offset, comp in enumerate(components, start=1)
    ]
    created = []
    with business_transaction(actor_app_user_id):
        for offset, cols in enumerate(prepared, start=1):
            created.append(
                AssemblyComponent.objects.create(
                    id=uuid.uuid4(),
                    assembly_id=assembly_id,
                    position=current_max + offset,
                    **cols,
                )
            )
    return created


def create_sale_price_group(
    actor_app_user_id,
    *,
    name,
    calc_basis="EK",
    operator="AUFSCHLAG",
    percent_change=None,
    amount_change=None,
):
    """Legt eine VK-Kalkulationsgruppe an (Migration 0033).

    Genau eines von percent_change ODER amount_change ist zu setzen (DB-XOR-CHECK);
    beide müssen >= 0 sein.
    """
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    if calc_basis not in SALE_CALC_BASES:
        raise ValueError(f"Ungültige calc_basis '{calc_basis}'.")
    if operator not in SALE_OPERATORS:
        raise ValueError(f"Ungültiger operator '{operator}'.")
    if (percent_change is None) == (amount_change is None):
        raise ValueError(
            "Genau eines von percent_change oder amount_change ist zu setzen."
        )
    with business_transaction(actor_app_user_id):
        group = SalePriceGroup.objects.create(
            id=uuid.uuid4(),
            name=name.strip(),
            calc_basis=calc_basis,
            operator=operator,
            percent_change=percent_change,
            amount_change=amount_change,
            status="AKTIV",
            version=1,
        )
    return group


def update_sale_price_group(
    actor_app_user_id,
    *,
    group_id,
    name=None,
    calc_basis=None,
    operator=None,
    percent_change=...,
    amount_change=...,
    status=None,
):
    """Ändert eine bestehende VK-Kalkulationsgruppe (Name/Formel/Status).

    Nicht mitgegebene Felder bleiben unverändert. `percent_change`/`amount_change`
    tragen den Sentinel `...` als „nicht mitgeschickt": nur so lässt sich die
    Formel von prozentual auf Betrag (oder umgekehrt) umstellen — dazu MÜSSEN beide
    Werte explizit gesetzt werden (der neue Wert und `None` für den anderen), sonst
    verletzt der Doppelwert den DB-XOR-CHECK und die Änderung wird als 422 abgelehnt.
    """
    group = SalePriceGroup.objects.filter(id=group_id).first()
    if group is None:
        raise ValueError("Kalkulationsgruppe nicht gefunden.")

    if name is not None:
        if not name.strip():
            raise ValueError("name darf nicht leer sein.")
        group.name = name.strip()
    if calc_basis is not None:
        if calc_basis not in SALE_CALC_BASES:
            raise ValueError(f"Ungültige calc_basis '{calc_basis}'.")
        group.calc_basis = calc_basis
    if operator is not None:
        if operator not in SALE_OPERATORS:
            raise ValueError(f"Ungültiger operator '{operator}'.")
        group.operator = operator

    pc = group.percent_change if percent_change is ... else percent_change
    ac = group.amount_change if amount_change is ... else amount_change
    if (pc is None) == (ac is None):
        raise ValueError(
            "Genau eines von percent_change oder amount_change ist zu setzen."
        )
    group.percent_change = pc
    group.amount_change = ac

    if status is not None:
        if status not in ("AKTIV", "INAKTIV"):
            raise ValueError(f"Ungültiger Status '{status}'.")
        group.status = status

    with business_transaction(actor_app_user_id):
        group.save(update_fields=[
            "name", "calc_basis", "operator", "percent_change",
            "amount_change", "status", "updated_at",
        ])
    return group


def set_article_sale_price(
    actor_app_user_id,
    *,
    article_id,
    label="Standard",
    sale_price_group_id=None,
    fixed_price=None,
    is_standard=False,
):
    """Legt eine VK-Variante für einen Artikel an (Formel-Gruppe ODER Festpreis).

    Genau eines von sale_price_group_id oder fixed_price ist zu setzen (DB-XOR);
    höchstens eine Standard-Variante je Artikel (partieller Unique-Index).
    """
    if (sale_price_group_id is None) == (fixed_price is None):
        raise ValueError(
            "Genau eines von sale_price_group_id oder fixed_price ist zu setzen."
        )
    ensure_exists(Article, article_id, "Artikel")
    ensure_exists(SalePriceGroup, sale_price_group_id, "Kalkulationsgruppe")
    # Höchstens eine Zeile je (Artikel, VK-Gruppe) — seit Migration 0042 als
    # partieller Unique-Index (uq_article_sale_price_group). Ohne Vorabprüfung
    # schlüge ein Doppel-Anlegen als IntegrityError (500) durch statt als 422;
    # die ganze VK-Tabelle wird über set_verkaufspreise (Upsert je Gruppe) gepflegt.
    if sale_price_group_id is not None and ArticleSalePrice.objects.filter(
        article_id=article_id, sale_price_group_id=sale_price_group_id
    ).exists():
        raise ValueError(
            "Für diese VK-Gruppe besteht bereits ein Eintrag an diesem Artikel."
        )
    with business_transaction(actor_app_user_id):
        asp = ArticleSalePrice.objects.create(
            id=uuid.uuid4(),
            article_id=article_id,
            label=label,
            sale_price_group_id=sale_price_group_id,
            fixed_price=fixed_price,
            is_standard=is_standard,
        )
    return asp


# ---------------------------------------------------------------------------
# Historie eines Artikels (Reiter „Historie" im Artikeldialog)
# ---------------------------------------------------------------------------
# Quelle ist `audit.audit_entry`, gefüllt vom Trigger `trg_article_audit`. Der
# Eintrag trägt den vollständigen Vorher- und Nachher-Zustand als JSON; wir
# bilden daraus den Feld-Diff. Technische Felder (Zeitstempel, Version) werden
# ausgeblendet — sie ändern sich bei jedem Speichern und sagen nichts aus.

_HISTORIE_IGNORIEREN = {"updated_at", "created_at", "version", "id"}


def article_historie(article_id, *, limit=50):
    """Änderungen an einem Artikel, neueste zuerst.

    Gibt je Eintrag den Zeitpunkt, den Akteur und die geänderten Felder mit
    Vorher-/Nachher-Wert zurück. Einträge ohne fachliche Änderung (nur
    Zeitstempel) werden weggelassen — sonst rauscht die Historie zu.
    """
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT a.occurred_at, a.action, u.display_name,
                   a.before_excerpt, a.after_excerpt
            FROM audit.audit_entry a
            LEFT JOIN security.app_user u ON u.id = a.actor_user_id
            WHERE a.target_type = 'pricing.article' AND a.target_id = %s
            ORDER BY a.occurred_at DESC
            LIMIT %s
            """,
            [str(article_id), limit],
        )
        zeilen = cur.fetchall()

    def _als_dict(wert):
        """Ueber einen rohen Cursor liefert psycopg `jsonb` als Zeichenkette --
        den JSON-Loader registriert Django nur fuer das ORM."""
        if wert is None:
            return {}
        if isinstance(wert, str):
            return json.loads(wert)
        return wert

    eintraege = []
    for occurred_at, action, akteur, vorher, nachher in zeilen:
        vorher = _als_dict(vorher)
        nachher = _als_dict(nachher)
        felder = []
        for feld in sorted(set(vorher) | set(nachher)):
            if feld in _HISTORIE_IGNORIEREN:
                continue
            alt, neu = vorher.get(feld), nachher.get(feld)
            if alt != neu:
                felder.append({"feld": feld, "vorher": alt, "nachher": neu})
        if not felder:
            continue
        eintraege.append(
            {
                "occurred_at": occurred_at,
                "action": action,
                "akteur": akteur,
                "felder": felder,
            }
        )
    return eintraege


# ---------------------------------------------------------------------------
# Stammdaten aus einer Belegposition übernehmen (das „Häkchen")
# ---------------------------------------------------------------------------
# Standardfall: Wer im Angebot, in der Rechnung oder im Baustellenbericht eine
# Position ändert, ändert NUR diese Position. Der Artikelstamm ist davon
# unberührt (siehe test_beleg_artikel_entkopplung.py).
#
# Nur wenn ausdrücklich „Änderungen an Stammdaten übernehmen" gewählt wird, läuft
# dieser Vorgang — und er verlangt ein eigenes Recht (`pricing/AENDERN`). Wer ein
# Angebot schreiben darf, darf damit nicht automatisch den Stamm umschreiben, den
# alle anderen Angebote mitbenutzen.
#
# Der EINKAUFSPREIS wird bewusst NICHT übernommen. Er steht auf der
# Lieferantenreferenz und ist die Aussage des Händlers (DATANORM-Import), keine
# Meinung des Angebotsschreibers. Ein abweichender EK in einer Position ist eine
# Kalkulationsentscheidung für genau dieses Angebot.

STAMMDATEN_UEBERNAHME_FELDER = ("description", "long_description", "unit")


def positionswerte_in_stammdaten(
    actor_app_user_id, *, article_id, verkaufspreis=None, **felder
):
    """Überträgt Werte einer Belegposition in den Artikelstamm.

    `felder` sind Bezeichnung, Langtext und Einheit. `verkaufspreis` wird als
    Standard-Festpreis hinterlegt: er ersetzt die bisherige Standard-Variante,
    ohne die Formel-Gruppen des Artikels anzutasten.

    Gibt den aktualisierten Artikel zurück.
    """
    unbekannt = set(felder) - set(STAMMDATEN_UEBERNAHME_FELDER)
    if unbekannt:
        raise ValueError(
            f"Diese Felder lassen sich nicht in den Stamm übernehmen: "
            f"{', '.join(sorted(unbekannt))}. "
            f"Erlaubt: {', '.join(STAMMDATEN_UEBERNAHME_FELDER)} und verkaufspreis."
        )
    gesetzt = {k: v for k, v in felder.items() if v is not None}
    if not gesetzt and verkaufspreis is None:
        raise ValueError("Es wurde nichts zum Übernehmen angegeben.")

    article = update_article(actor_app_user_id, article_id=article_id, **gesetzt)

    if verkaufspreis is not None:
        # BEWUSST KEINE price_unit-Umrechnung: `verkaufspreis` ist der
        # Belegpositions-Preis JE STÜCK, und `fixed_price` (die VK-Überschreibung)
        # ist ebenfalls je Stück (die VK-Übersicht teilt nur die BASIS durch
        # price_unit, nicht die Überschreibung). Ein Teilen/Multiplizieren hier
        # verfälschte den Rundlauf Beleg <-> Stamm.
        preis = Decimal(str(verkaufspreis))
        if preis < 0:
            raise ValueError("Der Verkaufspreis darf nicht negativ sein.")
        with business_transaction(actor_app_user_id):
            # Höchstens eine Standard-Variante je Artikel (partieller Unique-Index):
            # die bisherige verliert ihren Standard-Status, statt zu verschwinden —
            # sie kann eine Formel-Gruppe sein, die weiter gebraucht wird.
            ArticleSalePrice.objects.filter(
                article_id=article.id, is_standard=True
            ).update(is_standard=False)
            ArticleSalePrice.objects.create(
                id=uuid.uuid4(),
                article_id=article.id,
                label="Aus Beleg übernommen",
                fixed_price=preis,
                is_standard=True,
            )
    article.refresh_from_db()
    return article


# ---------------------------------------------------------------------------
# Lieferantenbezug setzen (Hero-Reiter „Informationen": Lieferant + EK)
# ---------------------------------------------------------------------------

def set_primary_supplier(
    actor_app_user_id,
    *,
    article_id,
    supplier_party_id,
    supplier_article_number,
    last_purchase_price=None,
    currency="EUR",
):
    """Setzt den primären (manuellen) Lieferantenbezug eines Artikels.

    Ein Artikel kann mehrere Lieferantenbezüge tragen; für den Dialog zählt der
    PRIMÄRE — aktuell gültig mit dem jüngsten valid_from (siehe
    kalkulation.primary_supplier_reference). Diese Funktion pflegt den manuell
    gesetzten Bezug (`source_system='MANUELL'`):

    * Besteht bereits ein offener Bezug für denselben Lieferanten und dieselbe
      Lieferanten-Artikelnummer, wird nur der Einkaufspreis aktualisiert (der
      Wechsel wird über `trg_supplier_ref_audit` historisiert). Lieferant,
      Quellsystem, Namespace und Nummer sind laut Schema unveränderlich
      (trg_supplier_ref_protect).
    * Andernfalls entsteht ein neuer Bezug ab heute; er wird durch das jüngste
      valid_from/last_imported_at zum primären.

    `last_purchase_price` wird UNVERÄNDERT gespeichert (je `price_unit` Einheiten;
    die Umrechnung auf je Stück macht der Kalkulations-Service). NULL heisst
    „Einkaufspreis unbekannt" — dann verlangt der DB-CHECK auch keine Währung.
    """
    ensure_exists(Article, article_id, "Artikel")
    ensure_party_usable(supplier_party_id, "Lieferant")
    san = (supplier_article_number or "").strip()
    if not san:
        raise ValueError("Die Lieferanten-Artikelnummer darf nicht leer sein.")
    if last_purchase_price is not None:
        ek = Decimal(str(last_purchase_price))
        if ek < 0:
            raise ValueError("Der Einkaufspreis darf nicht negativ sein.")
        cur = (currency or "EUR").strip().upper() or "EUR"
    else:
        ek = None
        cur = None  # CHECK ((last_purchase_price IS NULL) = (currency IS NULL))

    heute = date.today()
    with business_transaction(actor_app_user_id):
        vorhanden = (
            ArticleSupplierReference.objects.filter(
                article_id=article_id,
                source_system="MANUELL",
                supplier_party_id=supplier_party_id,
                supplier_article_number=san,
                valid_until__isnull=True,
            )
            .order_by("-valid_from")
            .first()
        )
        if vorhanden is not None:
            vorhanden.last_purchase_price = ek
            vorhanden.currency = cur
            vorhanden.last_imported_at = datetime.now(timezone.utc)
            vorhanden.save(
                update_fields=[
                    "last_purchase_price", "currency", "last_imported_at", "updated_at"
                ]
            )
            ref = vorhanden
        else:
            ref = ArticleSupplierReference.objects.create(
                id=uuid.uuid4(),
                article_id=article_id,
                supplier_party_id=supplier_party_id,
                source_system="MANUELL",
                # Namespace je Artikel+Lieferant: die Kollisionsschranke (EXCLUDE)
                # greift dann nur bei genau diesem Bezug, nicht über Artikel hinweg.
                source_namespace=f"{article_id}:{supplier_party_id}",
                supplier_article_number=san,
                last_purchase_price=ek,
                currency=cur,
                valid_from=heute,
                last_imported_at=datetime.now(timezone.utc),
            )
    return ref


# ---------------------------------------------------------------------------
# VK-Tabelle „auf einmal" speichern (Hero-Reiter „Kalkulation" rechts)
# ---------------------------------------------------------------------------

def set_verkaufspreise(actor_app_user_id, *, article_id, entries):
    """Setzt die komplette VK-Gruppen-Tabelle eines Artikels in EINEM Vorgang.

    `entries`: Liste von dicts {sale_price_group_id, fixed_price|None, is_standard}.
    Je Gruppe entsteht/aktualisiert sich eine `article_sale_price`-Zeile:
    fixed_price gesetzt = manuelle Überschreibung des Formel-VK dieser Gruppe,
    None = Formel gilt. Genau eine Gruppe ist Standard.

    Der Hero-Dialog speichert die ganze Tabelle auf einmal; darum ein einziger
    business_transaction-Block. Bestehende freistehende Festpreise (Gruppe NULL,
    z. B. „aus Beleg übernommen") verlieren dabei ihren Standard-Status — den
    vergibt die Tabelle.
    """
    ensure_exists(Article, article_id, "Artikel")
    entries = list(entries or [])
    if not entries:
        raise ValueError("Es wurde keine VK-Gruppe übergeben.")

    gesehen = set()
    standard = []
    normiert = []
    for e in entries:
        gid = e.get("sale_price_group_id")
        if gid is None:
            raise ValueError("Jede Zeile braucht eine VK-Gruppe.")
        if gid in gesehen:
            raise ValueError("Eine VK-Gruppe darf nur einmal vorkommen.")
        gesehen.add(gid)
        fixed = e.get("fixed_price")
        if fixed is not None:
            fixed = Decimal(str(fixed))
            if fixed < 0:
                raise ValueError("Ein überschriebener VK darf nicht negativ sein.")
        is_std = bool(e.get("is_standard"))
        if is_std:
            standard.append(gid)
        normiert.append({"gid": gid, "fixed": fixed, "is_std": is_std})

    if len(standard) != 1:
        raise ValueError("Genau eine VK-Gruppe muss als Standard markiert sein.")

    # Alle genannten Gruppen müssen existieren UND aktiv sein.
    gruppen = {
        g.id: g
        for g in SalePriceGroup.objects.filter(id__in=list(gesehen))
    }
    fehlend = gesehen - set(gruppen)
    if fehlend:
        raise ValueError(
            "Unbekannte VK-Gruppe(n): "
            + ", ".join(str(m) for m in sorted(fehlend, key=str))
        )
    inaktiv = [str(gid) for gid, g in gruppen.items() if g.status != "AKTIV"]
    if inaktiv:
        raise ValueError("Inaktive VK-Gruppe(n): " + ", ".join(sorted(inaktiv)))

    bestehend = {
        asp.sale_price_group_id: asp
        for asp in ArticleSalePrice.objects.filter(
            article_id=article_id, sale_price_group_id__in=list(gesehen)
        )
    }

    with business_transaction(actor_app_user_id):
        # Erst alle Standard-Marker löschen (auch freistehende Festpreise), damit
        # der partielle Unique-Index nie zwei Standards gleichzeitig sieht.
        ArticleSalePrice.objects.filter(
            article_id=article_id, is_standard=True
        ).update(is_standard=False)

        std_gid = standard[0]
        for row in normiert:
            asp = bestehend.get(row["gid"])
            if asp is not None:
                asp.fixed_price = row["fixed"]
                # Was hier von Hand gesetzt wird, ist MANUELL — die Massenpflege
                # der Aufschlagsmatrix (0069) fasst solche Preise nicht mehr an.
                asp.price_origin = "MANUELL"
                asp.save(
                    update_fields=["fixed_price", "price_origin", "updated_at"]
                )
            else:
                ArticleSalePrice.objects.create(
                    id=uuid.uuid4(),
                    article_id=article_id,
                    sale_price_group_id=row["gid"],
                    label=gruppen[row["gid"]].name,
                    fixed_price=row["fixed"],
                    is_standard=False,
                )
        # Genau eine Zeile als Standard markieren.
        ArticleSalePrice.objects.filter(
            article_id=article_id, sale_price_group_id=std_gid
        ).update(is_standard=True)


# ---------------------------------------------------------------------------
# Artikel kopieren (Hero „Kopieren")
# ---------------------------------------------------------------------------
# Stammfelder, die beim Kopieren 1:1 übernommen werden. NICHT dabei:
# * id/version/status/created_at/updated_at — für die Kopie neu gesetzt
#   (Status AKTIV, version 1).
# * article_number — die Kopie bekommt eine NEUE, freie Nummer.
# * gtin — `uq_article_gtin` ist (partiell) eindeutig: eine GTIN/EAN
#   identifiziert genau EIN physisches Produkt und darf nicht an zwei Artikeln
#   hängen. Die Kopie startet ohne GTIN (sonst IntegrityError/500).
_ARTICLE_COPY_FIELDS = (
    "description",
    "long_description",
    "manufacturer_name",
    "manufacturer_number",
    "manufacturer_type",
    "unit",
    "line_type",
    "product_group",
    "list_price",
    "matchcode",
    "min_order_quantity",
    "quantity_step",
    "delivery_time_days",
    "tax_code_id",
    "cost_center_id",
    "price_unit",
)


def copy_article(actor_app_user_id, *, source_article_id, article_number):
    """Dupliziert einen Artikel unter neuer Nummer (Hero „Kopieren").

    In EINER Transaktion:
    * alle Stammfelder des Quellartikels (inkl. price_unit, tax_code,
      cost_center_id, matchcode, Hersteller-Felder, long_description,
      list_price) — mit der neuen `article_number` und Status AKTIV. GTIN wird
      bewusst nicht kopiert (eindeutiger Produktcode, siehe _ARTICLE_COPY_FIELDS).
    * alle VK-Varianten (`article_sale_price`: Gruppen-Überschreibungen und die
      Standard-Markierung).
    * der primäre Lieferantenbezug, falls vorhanden — als `source_system='MANUELL'`
      am Zielartikel (über set_primary_supplier).

    GoBD: die Kopie ist ein neuer, eigenständiger Artikel mit eigener Nummer,
    kein Verweis auf die Quelle. Gibt den neuen Artikel zurück.
    """
    # Lokaler Import: kein Modul-Zyklus (kalkulation importiert artikel nicht).
    from db_core.services import kalkulation as kalkulation_service

    nummer = (article_number or "").strip()
    if not nummer:
        raise ValueError("Die neue Artikelnummer darf nicht leer sein.")
    source = Article.objects.filter(id=source_article_id).first()
    if source is None:
        raise ValueError("Quellartikel nicht gefunden.")
    if Article.objects.filter(article_number=nummer).exists():
        raise ValueError(f"Artikelnummer '{nummer}' ist bereits vergeben.")

    werte = {feld: getattr(source, feld) for feld in _ARTICLE_COPY_FIELDS}
    sale_prices = list(ArticleSalePrice.objects.filter(article_id=source_article_id))
    primary_ref = kalkulation_service.primary_supplier_reference(source_article_id)

    new_id = uuid.uuid4()
    with business_transaction(actor_app_user_id):
        Article.objects.create(
            id=new_id,
            article_number=nummer,
            gtin=None,
            status="AKTIV",
            version=1,
            **werte,
        )
        for asp in sale_prices:
            ArticleSalePrice.objects.create(
                id=uuid.uuid4(),
                article_id=new_id,
                label=asp.label,
                sale_price_group_id=asp.sale_price_group_id,
                fixed_price=asp.fixed_price,
                is_standard=asp.is_standard,
            )
        if primary_ref is not None:
            # set_primary_supplier öffnet eine geschachtelte business_transaction
            # (Savepoint innerhalb dieser Transaktion) und legt den Bezug als
            # MANUELL am Zielartikel an — die Kopie bleibt atomar.
            set_primary_supplier(
                actor_app_user_id,
                article_id=new_id,
                supplier_party_id=primary_ref.supplier_party_id,
                supplier_article_number=primary_ref.supplier_article_number,
                last_purchase_price=primary_ref.last_purchase_price,
                currency=primary_ref.currency,
            )
    return Article.objects.get(id=new_id)
