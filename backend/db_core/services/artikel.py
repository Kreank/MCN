"""Artikel-Service: Artikel, Lohngruppen und Leistungen (Stücklisten) anlegen.

Dieser Slice ist im UI lesend (Liste/Detail); die Anlage-Funktionen dienen dem
Seed und späteren Schreib-Pfaden. Alle Writes laufen über business_transaction.
Nummern (article_number/assembly_number) haben keine DB-Automatik und müssen
gesetzt werden. Kein Löschen — nur status AKTIV/INAKTIV.
"""
import uuid
from decimal import Decimal

from db_core.db_context import business_transaction
from db_core.models import Article, Assembly, AssemblyComponent, WageGroup

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
        for pos, comp in enumerate(components, start=1):
            is_material = comp.get("article_id") is not None
            is_labour = comp.get("wage_group_id") is not None
            if is_material == is_labour:
                raise ValueError(
                    f"Position {pos}: genau eines von Material (article_id+quantity) "
                    "oder Lohn (wage_group_id+minutes) angeben."
                )
            # Betrag der jeweiligen Seite ist Pflicht und > 0 (sonst DB-CHECK/500).
            if is_material and not (comp.get("quantity") and Decimal(str(comp["quantity"])) > 0):
                raise ValueError(f"Position {pos}: quantity (> 0) Pflicht für Material.")
            if is_labour and not (comp.get("minutes") and Decimal(str(comp["minutes"])) > 0):
                raise ValueError(f"Position {pos}: minutes (> 0) Pflicht für Lohn.")
            AssemblyComponent.objects.create(
                id=uuid.uuid4(),
                assembly_id=assembly.id,
                position=pos,
                article_id=comp.get("article_id"),
                wage_group_id=comp.get("wage_group_id"),
                quantity=comp.get("quantity") if is_material else None,
                minutes=comp.get("minutes") if is_labour else None,
                note=comp.get("note"),
            )
    return assembly
