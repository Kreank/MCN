"""IDS-Connect Warenkorb-Verfahren (v2.5) — Rückgabe-Warenkorb parsen, Positionen
auf den Artikelstamm mappen und einen Ausgangs-Warenkorb bauen.

IDS-Connect ist der SHK-Branchenstandard (itek): Der Handwerker öffnet aus MCN den
Webshop des Großhändlers (Punchout), stellt dort einen Warenkorb zusammen und der
Shop schickt ihn als XML zurück. Dieses Modul deckt die reine XML-/Mapping-Logik
ab — der HTTP-Roundtrip (Shop-Formular + Rückgabe-Endpunkt) ist ein eigener Slice.

Format (Schema `warenkorb_empfangen_2_5.xsd`, Namespace
`http://www.itek.de/Shop-Anbindung/Warenkorb/`): `<Warenkorb>` → `<Order>` →
mehrere `<OrderItem>` mit **`ArtNo`** (Händler-Artikelnummer), **`Qty`** (Menge),
**`QU`** (Mengeneinheit); optional `Kurztext`, `EAN`, Preise usw. Der zurückgegebene
Warenkorb ist in der Praxis schlank (oft nur ArtNo/Qty/QU) — die Preise stehen im
über DATANORM importierten Stamm.

Robustheit: Reale Warenkörbe kommen mal MIT, mal OHNE deklarierten XML-Namespace
(das mitgelieferte Beispiel deklariert ihn nicht). Deshalb wird **über den lokalen
Tag-Namen** geparst (Namespace abgestreift), nie über den qualifizierten Namen.

Mapping: Eine Position wird über `(source_namespace, supplier_article_number)`
gegen `pricing.article_supplier_reference` (aktuell gültig, `valid_until IS NULL`)
aufgelöst. Bewusst NUR über den Namespace, nicht über `source_system` — der Katalog
wird i. d. R. per DATANORM importiert (`source_system='DATANORM'`), bestellt wird
per IDS-Connect; beide teilen denselben Händler-Namespace und dieselben
Artikelnummern. Nicht auflösbare Positionen werden als solche gemeldet (nie still
verworfen).
"""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

# defusedxml härtet das Parsen von FREMD-XML (der Warenkorb kommt vom Händler-Shop)
# gegen XXE, externe Entities und Entity-Expansion („billion laughs"): interne/
# externe Entities und DTDs werden abgelehnt. Der Ausgangs-Warenkorb wird weiter
# mit der stdlib gebaut (kein Fremd-Input, ElementTree escapet korrekt).
from defusedxml.ElementTree import fromstring as _safe_fromstring
from defusedxml.common import DefusedXmlException

from db_core.models import ArticleSupplierReference

IDS_NAMESPACE = "http://www.itek.de/Shop-Anbindung/Warenkorb/"
IDS_VERSION = "2.5"

# Bekannte Präfix→Namespace-Zuordnungen für die Reparatur nicht deklarierter
# Präfixe (siehe _parse_xml). Das reale IDS-Beispiel trägt `xsi:schemaLocation`,
# ohne `xmlns:xsi` zu deklarieren — streng genommen nicht wohlgeformt, aber in der
# Praxis verbreitet.
_KNOWN_PREFIX_NS = {
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xsd": "http://www.w3.org/2001/XMLSchema",
}


class WarenkorbError(ValueError):
    """Der Warenkorb ist fachlich/technisch nicht verarbeitbar (→ 422)."""


@dataclass(frozen=True)
class CartPosition:
    """Eine Warenkorb-Position, wie sie der Shop zurückgibt."""
    art_no: str
    qty: Decimal
    unit: str | None = None
    short_text: str | None = None
    ean: str | None = None


@dataclass(frozen=True)
class ResolvedPosition:
    """Eine Position samt Auflösung gegen den Artikelstamm."""
    art_no: str
    qty: Decimal
    unit: str | None
    short_text: str | None
    ean: str | None
    article_id: str | None
    article_number: str | None
    article_name: str | None
    matched: bool
    ambiguous: bool


# --- XML-Hilfen (namespace-tolerant) ---------------------------------------

def _local(tag: str) -> str:
    """Lokaler Tag-Name ohne `{namespace}`-Präfix."""
    return tag.rsplit("}", 1)[-1]


def _first(elem, name):
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _all(elem, name):
    return [child for child in elem if _local(child.tag) == name]


def _text(elem, name):
    child = _first(elem, name)
    if child is None or child.text is None:
        return None
    wert = child.text.strip()
    return wert or None


def _declare_missing_prefixes(xml_str: str) -> str:
    """Deklariert am Wurzelelement jedes verwendete, aber nicht deklarierte
    XML-Präfix (bekannte wie `xsi` mit ihrem echten Namespace, unbekannte mit
    einem Platzhalter). Nötig, weil reale IDS-Warenkörbe `xsi:schemaLocation`
    tragen, ohne `xmlns:xsi` zu deklarieren (expat lehnt das sonst ab)."""
    used = set(re.findall(r"</?\s*([A-Za-z_][\w.-]*):", xml_str))
    used |= set(re.findall(r"[\s\"'<]([A-Za-z_][\w.-]*):[A-Za-z_][\w.-]*\s*=", xml_str))
    used.discard("xmlns")
    declared = set(re.findall(r"xmlns:([A-Za-z_][\w.-]*)\s*=", xml_str))
    missing = used - declared
    if not missing:
        return xml_str
    decls = "".join(
        f' xmlns:{p}="{_KNOWN_PREFIX_NS.get(p, f"urn:mcn:ids:unknown:{p}")}"'
        for p in sorted(missing)
    )
    # Deklarationen direkt hinter den Namen des Wurzelelements einschieben.
    return re.sub(r"(<[A-Za-z_][\w.:-]*)", lambda m: m.group(1) + decls, xml_str, count=1)


def _fromstring(raw: bytes):
    """defusedxml-Parse: verbietet DTD, interne und externe Entities (härtet gegen
    XXE/Entity-Expansion). Der Warenkorb braucht keins davon."""
    return _safe_fromstring(
        raw, forbid_dtd=True, forbid_entities=True, forbid_external=True
    )


def _parse_xml(raw: bytes):
    """Parst XML-Bytes robust und gehärtet. Auf „unbound prefix" wird einmal mit
    ergänzten Präfix-Deklarationen erneut versucht (siehe _declare_missing_prefixes)."""
    try:
        return _fromstring(raw)
    except ET.ParseError as exc:
        if "unbound prefix" in str(exc):
            # latin-1 ist bijektiv (Byte↔Zeichen 1:1): der Roundtrip erhält die
            # Originalbytes samt Kodierung verlustfrei — nur die (rein ASCII-)
            # xmlns-Deklarationen kommen hinzu. utf-8/"replace" würde latin-1-
            # Umlaute (z. B. in Kurztext) zu U+FFFD verstümmeln.
            repariert = _declare_missing_prefixes(raw.decode("latin-1"))
            try:
                return _fromstring(repariert.encode("latin-1"))
            except (ET.ParseError, DefusedXmlException) as exc2:
                raise WarenkorbError(f"Ungültiges Warenkorb-XML: {exc2}")
        raise WarenkorbError(f"Ungültiges Warenkorb-XML: {exc}")
    except DefusedXmlException:
        raise WarenkorbError(
            "Warenkorb-XML mit unzulässiger DTD oder Entities abgelehnt."
        )


# --- Rückgabe-Warenkorb parsen ----------------------------------------------

def parse_returned_cart(xml) -> list[CartPosition]:
    """Parst einen empfangenen IDS-Warenkorb in seine Positionen.

    `xml` sind Bytes oder ein String. Wirft `WarenkorbError` bei ungültigem XML,
    fehlendem `<Warenkorb>`-Wurzelelement oder ungültiger Menge. Positionen ohne
    `ArtNo` werden übersprungen (eine leere Rückgabe ist zulässig — z. B. wenn der
    Handwerker im Shop abbricht).
    """
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    root = _parse_xml(raw)
    if _local(root.tag) != "Warenkorb":
        raise WarenkorbError("Kein <Warenkorb>-Wurzelelement.")

    order = _first(root, "Order")
    if order is None:
        return []

    positions: list[CartPosition] = []
    for item in _all(order, "OrderItem"):
        art_no = _text(item, "ArtNo")
        if not art_no:
            continue
        qty_raw = _text(item, "Qty")
        try:
            qty = Decimal(qty_raw) if qty_raw else Decimal("0")
        except InvalidOperation:
            raise WarenkorbError(
                f"Position {art_no}: ungültige Menge '{qty_raw}'."
            )
        if not qty.is_finite():
            raise WarenkorbError(f"Position {art_no}: ungültige Menge.")
        # Fehlende (→ 0) oder negative Mengen sind fachlich unzulässig — eine
        # Position muss eine positive Menge tragen (sie wird später bestellt).
        if qty <= 0:
            raise WarenkorbError(
                f"Position {art_no}: Menge muss größer als 0 sein (war '{qty_raw}')."
            )
        positions.append(
            CartPosition(
                art_no=art_no,
                qty=qty,
                unit=_text(item, "QU"),
                short_text=_text(item, "Kurztext"),
                ean=_text(item, "EAN"),
            )
        )
    return positions


# --- Positionen auf den Artikelstamm mappen ---------------------------------

def resolve_positions(source_namespace: str, positions) -> list[ResolvedPosition]:
    """Löst jede Position über `(source_namespace, ArtNo)` gegen die aktuell
    gültigen Lieferantenreferenzen auf.

    `matched` = genau ein Artikel gefunden; `ambiguous` = mehrere verschiedene
    Artikel unter derselben Händler-Artikelnummer (dann `article_*` leer, der
    Anwender muss entscheiden). Nicht gefunden = beide false.
    """
    # Alle Referenzen der vorkommenden Artikelnummern in EINER Query laden und je
    # Nummer gruppieren (kein N+1 über die Positionen — wichtig für große Körbe).
    art_nos = {p.art_no for p in positions}
    nach_nummer: dict[str, list] = {}
    if art_nos:
        for ref in ArticleSupplierReference.objects.filter(
            source_namespace=source_namespace,
            supplier_article_number__in=art_nos,
            valid_until__isnull=True,
        ).select_related("article"):
            nach_nummer.setdefault(ref.supplier_article_number, []).append(ref)

    resolved: list[ResolvedPosition] = []
    for p in positions:
        refs = nach_nummer.get(p.art_no, [])
        artikel_ids = {r.article_id for r in refs}
        matched = len(artikel_ids) == 1
        ambiguous = len(artikel_ids) > 1
        art = refs[0].article if matched else None
        resolved.append(
            ResolvedPosition(
                art_no=p.art_no,
                qty=p.qty,
                unit=p.unit,
                short_text=p.short_text,
                ean=p.ean,
                article_id=str(art.id) if art else None,
                article_number=art.article_number if art else None,
                article_name=art.description if art else None,
                matched=matched,
                ambiguous=ambiguous,
            )
        )
    return resolved


# --- Ausgangs-Warenkorb bauen (Handover an den Shop) ------------------------

def build_cart_xml(positions=None, *, jetzt=None, currency="EUR") -> bytes:
    """Baut einen minimalen IDS-Ausgangs-Warenkorb (`warenkorb_senden`) zum
    Handover an den Shop.

    Nur die Pflicht-/Kernfelder: `WarenkorbInfo` (Datum/Zeit/Version) und je
    Position `ArtNo`/`Qty`/`QU`. `positions` ist eine Liste von `CartPosition`
    (oder leer, um mit leerem Warenkorb in den Shop zu springen). Rückgabe sind
    UTF-8-Bytes mit deklariertem IDS-Namespace.
    """
    jetzt = jetzt or datetime.now()
    ET.register_namespace("", IDS_NAMESPACE)
    root = ET.Element(f"{{{IDS_NAMESPACE}}}Warenkorb")
    info = ET.SubElement(root, f"{{{IDS_NAMESPACE}}}WarenkorbInfo")
    ET.SubElement(info, f"{{{IDS_NAMESPACE}}}Date").text = jetzt.strftime("%Y-%m-%d")
    ET.SubElement(info, f"{{{IDS_NAMESPACE}}}Time").text = jetzt.strftime("%H:%M:%S")
    ET.SubElement(info, f"{{{IDS_NAMESPACE}}}Version").text = IDS_VERSION
    order = ET.SubElement(root, f"{{{IDS_NAMESPACE}}}Order")
    for p in positions or []:
        item = ET.SubElement(order, f"{{{IDS_NAMESPACE}}}OrderItem")
        ET.SubElement(item, f"{{{IDS_NAMESPACE}}}ArtNo").text = p.art_no
        ET.SubElement(item, f"{{{IDS_NAMESPACE}}}Qty").text = f"{p.qty:.2f}"
        if p.unit:
            ET.SubElement(item, f"{{{IDS_NAMESPACE}}}QU").text = p.unit
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
