"""Artikel-Service: Artikel, Lohngruppen und Leistungen (Stücklisten) anlegen.

Dieser Slice ist im UI lesend (Liste/Detail); die Anlage-Funktionen dienen dem
Seed und späteren Schreib-Pfaden. Alle Writes laufen über business_transaction.
Nummern (article_number/assembly_number) haben keine DB-Automatik und müssen
gesetzt werden. Kein Löschen — nur status AKTIV/INAKTIV.
"""
import uuid
from decimal import Decimal

from django.db.models import Max

from db_core.db_context import business_transaction
from db_core.models import (
    Article,
    ArticleSalePrice,
    Assembly,
    AssemblyComponent,
    SalePriceGroup,
    WageGroup,
)
from db_core.services._validation import ensure_all_exist, ensure_exists

SALE_CALC_BASES = ("EK", "LISTENPREIS")
SALE_OPERATORS = ("AUFSCHLAG", "ABSCHLAG")

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
    product_group=None,
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
            product_group=product_group,
            status="AKTIV",
            version=1,
        )
    return article


ARTICLE_STATUS = ("AKTIV", "INAKTIV")


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
