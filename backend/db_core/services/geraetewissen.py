"""Gerätewissen-Service (rein lesend): Herstellerersatzteile aus dem Artikelstamm.

Der „Gerätewissen"-Reiter ist KEIN eigenes Datensilo, sondern eine gefilterte
Sicht auf `pricing.article`: es werden ausschließlich Artikel gezeigt, die eine
Lieferantenreferenz (`pricing.article_supplier_reference`) in einem der
Hersteller-Namensräume (`vaillant`, `junkers`, …) mit `source_system='DATANORM'`
tragen. Der Großhandels-Namensraum (`bo`, Bär & Ollenroth, ~2 Mio Artikel) fällt
damit von allein heraus — er steht nicht in der Hersteller-Menge.

Erweitern um weitere Hersteller: einen Namensraum zu `GERAETEWISSEN_NAMESPACES`
hinzufügen (oder über die Django-Einstellung `GERAETEWISSEN_NAMESPACES`
überschreiben). Sobald der zugehörige Katalog importiert ist, erscheint er.

Rein lesend (kein business_transaction). Beträge als Decimal (verlustfrei).
"""
from datetime import date

from django.conf import settings
from django.db.models import Count, Exists, F, OuterRef, Q

from db_core.models import (
    Article,
    ArticleSupplierReference,
    SupplierConnection,
)
from db_core.services.artikel import _wildcard_regex

# ---------------------------------------------------------------------------
# Hersteller-Menge (zentral, leicht erweiterbar)
# ---------------------------------------------------------------------------
# Reihenfolge = Anzeigereihenfolge der Filter-Chips im Frontend. Der Großhandels-
# Namensraum `bo` darf hier NIE stehen — sonst kippten die ~2 Mio Katalogartikel
# in die Ersatzteilsicht.
_DEFAULT_NAMESPACES = ("vaillant", "junkers")

#: Nur DATANORM-Herstellerkataloge; IDS-Warenkörbe o. Ä. gehören nicht hierher.
GERAETEWISSEN_SOURCE_SYSTEM = "DATANORM"


def geraetewissen_namespaces():
    """Die konfigurierten Hersteller-Namensräume (Liste, Reihenfolge stabil).

    Standard: `vaillant`, `junkers`. Über die Django-Einstellung
    `GERAETEWISSEN_NAMESPACES` (Liste/Tupel von Strings) überschreibbar, ohne den
    Code anzufassen — leere/whitespace-Einträge werden verworfen.
    """
    roh = getattr(settings, "GERAETEWISSEN_NAMESPACES", None)
    if roh:
        namensraeume = [str(n).strip() for n in roh if str(n).strip()]
        if namensraeume:
            return namensraeume
    return list(_DEFAULT_NAMESPACES)


# Modul-Konstante für Import/Tests; die Funktion bleibt die Laufzeit-Quelle,
# damit ein Settings-Override auch ohne Reimport greift.
GERAETEWISSEN_NAMESPACES = list(_DEFAULT_NAMESPACES)


def _namespaces(namespace=None):
    """Die anzuwendende Namensraum-Menge.

    `namespace` grenzt (falls gesetzt) auf genau einen Hersteller ein — aber nur,
    wenn er auch konfiguriert ist. Ein unbekannter/fremder Namensraum (etwa `bo`)
    ergibt eine leere Menge → leeres Ergebnis, statt die Sicht aufzuweichen.
    """
    erlaubt = geraetewissen_namespaces()
    if namespace is None:
        return erlaubt
    return [namespace] if namespace in erlaubt else []


def _hersteller_ref_exists(namespaces, *, supplier_number_lookup=None):
    """Exists-Subquery: hat der Artikel eine Hersteller-Referenz im Namensraum?

    Exists (statt Join + distinct) hält die Trefferzahl korrekt, auch wenn ein
    Artikel mehrere Referenzen trägt (z. B. `vaillant` UND `bo`). Mit
    `supplier_number_lookup=(lookup, value)` wird zusätzlich auf die
    herstellereigene Artikelnummer der Referenz eingegrenzt (Suche).
    """
    sub = ArticleSupplierReference.objects.filter(
        article_id=OuterRef("pk"),
        source_namespace__in=namespaces,
        source_system=GERAETEWISSEN_SOURCE_SYSTEM,
    )
    if supplier_number_lookup is not None:
        lookup, value = supplier_number_lookup
        sub = sub.filter(**{f"supplier_article_number__{lookup}": value})
    return Exists(sub)


# Durchsuchte Artikelfelder: Nummer, Kurztext, Langtext, Fabrikat/Hersteller,
# Matchcode. Die herstellereigene Nummer (supplier_article_number) kommt über die
# Referenz-Subquery dazu (siehe _such_term_q).
_ARTICLE_SEARCH_FIELDS = (
    "article_number",
    "description",
    "long_description",
    "manufacturer_name",
    "matchcode",
)


def _such_term_q(term, namespaces):
    """Ein Suchterm → Q über Artikelfelder ODER die herstellereigene Nummer.

    Übernimmt die Hero-Operatoren aus der Artikelsuche: `*` als Platzhalter
    innerhalb eines Terms (sicheres iregex-Muster, kein rohes User-Regex), sonst
    icontains. Die Verknüpfung `+` (UND) / `|` (ODER) macht `build_search_q`.
    """
    term = term.strip()
    if not term:
        return None
    if "*" in term:
        lookup, value = "iregex", _wildcard_regex(term)
    else:
        lookup, value = "icontains", term
    q = Q()
    for feld in _ARTICLE_SEARCH_FIELDS:
        q |= Q(**{f"{feld}__{lookup}": value})
    # Herstellereigene Artikelnummer (z. B. Vaillant-Sachnummer) mitdurchsuchen —
    # per Exists, damit kein Join-Duplikat entsteht.
    q |= Q(_hersteller_ref_exists(namespaces, supplier_number_lookup=(lookup, value)))
    return q


def build_search_q(needle, namespaces):
    """Baut aus der Hero-Suchsyntax ein Q. `|` ODER-Gruppen, `+` UND, `*` Platzhalter.

    Leere Suche → None (dann wird nicht gefiltert). Deckungsgleich mit der
    Artikelsuche, nur über den erweiterten Feldsatz inkl. herstellereigener Nummer.
    """
    if not needle or not needle.strip():
        return None
    or_q = None
    for group in needle.split("|"):
        and_q = None
        for term in group.split("+"):
            term_q = _such_term_q(term, namespaces)
            if term_q is None:
                continue
            and_q = term_q if and_q is None else (and_q & term_q)
        if and_q is None:
            continue
        or_q = and_q if or_q is None else (or_q | and_q)
    return or_q


def _primary_namespace_ref(article_id, namespaces, on=None):
    """Die maßgebliche Hersteller-Referenz eines Artikels (jüngste gültige zuerst).

    Priorisierung wie im Kalkulations-Service: aktuell gültig (valid_from ≤ heute,
    valid_until offen/künftig), jüngstes valid_from, dann last_imported_at, dann id.
    Fällt keine gültige an (etwa nur künftige Gültigkeit), wird die neueste
    überhaupt genommen — die Sicht ist ein Katalog, kein Preis-Stichtag.
    """
    on = on or date.today()
    basis = ArticleSupplierReference.objects.filter(
        article_id=article_id,
        source_namespace__in=namespaces,
        source_system=GERAETEWISSEN_SOURCE_SYSTEM,
    )
    gueltig = basis.filter(valid_from__lte=on).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gt=on)
    ).order_by("-valid_from", F("last_imported_at").desc(nulls_last=True), "id")
    return gueltig.first() or basis.order_by(
        "-valid_from", F("last_imported_at").desc(nulls_last=True), "id"
    ).first()


def _namespace_refs(article_ids, namespaces, on=None):
    """Maßgebliche Hersteller-Referenz je Artikel in EINER Query (kein N+1).

    Gibt {article_id: ArticleSupplierReference}. Wählt je Artikel nach derselben
    Priorisierung wie `_primary_namespace_ref` — hier über eine gemeinsame,
    sortierte Query für die ganze Seite, damit der Aufwand an der Seitengröße hängt
    und nicht an der Gesamtzahl der Referenzen.
    """
    ids = [i for i in article_ids if i is not None]
    if not ids:
        return {}
    on = on or date.today()
    refs = (
        ArticleSupplierReference.objects.filter(
            article_id__in=ids,
            source_namespace__in=namespaces,
            source_system=GERAETEWISSEN_SOURCE_SYSTEM,
        )
        # Gültige zuerst, dann jüngstes valid_from etc. `-valid_until` mit
        # nulls_first sortiert offene (NULL) Gültigkeit vor beendete.
        .order_by(
            "article_id",
            F("valid_until").desc(nulls_first=True),
            "-valid_from",
            F("last_imported_at").desc(nulls_last=True),
            "id",
        )
    )
    gewaehlt = {}
    for ref in refs:
        if ref.article_id not in gewaehlt:
            gewaehlt[ref.article_id] = ref
    return gewaehlt


# ---------------------------------------------------------------------------
# Liste / Suche
# ---------------------------------------------------------------------------

def suche(*, q=None, namespace=None, page=1, page_size=25):
    """Ersatzteile suchen/auflisten (rein lesend, seitenweise).

    - `q`: Volltext über Nummer/Kurztext/Langtext/Fabrikat + herstellereigene Nummer
      (Hero-Operatoren `+`/`|`/`*`).
    - `namespace`: auf genau einen Hersteller eingrenzen (Filter-Chip). Ein nicht
      konfigurierter Namensraum liefert leer (die Sicht wird nie aufgeweicht).

    Gibt (items, total). Jedes Item ist ein dict mit den Anzeigefeldern; die
    herstellereigene Nummer/der Namensraum kommen gebündelt aus `_namespace_refs`.
    Nur AKTIVE Artikel (INAKTIV = ausrangiert, gehört nicht in die Ersatzteilsuche).
    """
    namespaces = _namespaces(namespace)
    if not namespaces:
        return [], 0

    qs = Article.objects.filter(
        _hersteller_ref_exists(namespaces), status="AKTIV"
    )
    such_q = build_search_q(q, namespaces)
    if such_q is not None:
        qs = qs.filter(such_q)
    qs = qs.order_by("description", "article_number", "id")

    total = qs.count()
    if page < 1:
        page = 1
    start = (page - 1) * page_size
    artikel = list(qs[start:start + page_size])
    refs = _namespace_refs([a.id for a in artikel], namespaces)

    items = [_treffer(a, refs.get(a.id)) for a in artikel]
    return items, total


def _treffer(article, ref):
    """Ein Listen-Treffer: Anzeigefelder aus Artikel + Hersteller-Referenz."""
    return {
        "article_id": article.id,
        # Interne MCN-Nummer (DN-… nach DATANORM-Import) — als Referenz.
        "article_number": article.article_number,
        # Herstellereigene Sachnummer (das, was der Monteur am Gerät sucht).
        "supplier_article_number": ref.supplier_article_number if ref else None,
        "description": article.description,
        # Fabrikat/Hersteller: bevorzugt das Artikelfeld, sonst der Namensraum.
        "manufacturer_name": article.manufacturer_name,
        "namespace": ref.source_namespace if ref else None,
        "unit": article.unit,
        "list_price": article.list_price,
    }


def detail(article_id):
    """Voll-Detail eines Ersatzteils (read-only) oder None, wenn es nicht in die
    Gerätewissen-Sicht fällt (kein Hersteller-Namensraum / inaktiv).

    Ein Artikel, der nur über den Großhandel (`bo`) geführt wird, ist hier bewusst
    NICHT auffindbar — die Detailsicht spiegelt exakt die Liste.
    """
    namespaces = geraetewissen_namespaces()
    if not namespaces:
        return None
    article = (
        Article.objects.filter(id=article_id, status="AKTIV")
        .filter(_hersteller_ref_exists(namespaces))
        .first()
    )
    if article is None:
        return None
    ref = _primary_namespace_ref(article.id, namespaces)
    return {
        "article_id": article.id,
        "article_number": article.article_number,
        "supplier_article_number": ref.supplier_article_number if ref else None,
        "description": article.description,
        "long_description": article.long_description,
        "manufacturer_name": article.manufacturer_name,
        "manufacturer_number": article.manufacturer_number,
        "manufacturer_type": article.manufacturer_type,
        "product_group": article.product_group,
        "matchcode": article.matchcode,
        "namespace": ref.source_namespace if ref else None,
        "unit": article.unit,
        "list_price": article.list_price,
        # Aussage des Händlers/Herstellers aus der Referenz (falls vorhanden).
        "supplier_list_price": ref.list_price if ref else None,
        "last_purchase_price": ref.last_purchase_price if ref else None,
        "currency": ref.currency if ref else None,
    }


# ---------------------------------------------------------------------------
# Hersteller-Facetten (Filter-Chips)
# ---------------------------------------------------------------------------

def hersteller():
    """Die konfigurierten Hersteller mit Ersatzteilzahl (Filter-Chips).

    Liefert JEDEN konfigurierten Namensraum — auch mit `anzahl=0`, solange noch
    kein Katalog importiert ist (das Frontend zeigt dann den erklärenden
    Leerzustand). Das Label kommt aus der HERSTELLER-Anbindung
    (`supplier_connection.label`), sonst dient der Namensraum selbst als Anzeige.
    """
    namespaces = geraetewissen_namespaces()
    if not namespaces:
        return []

    # Artikelzahl je Namensraum (nur AKTIVE Artikel, distinct).
    zaehlung = dict(
        ArticleSupplierReference.objects.filter(
            source_namespace__in=namespaces,
            source_system=GERAETEWISSEN_SOURCE_SYSTEM,
            article__status="AKTIV",
        )
        .values_list("source_namespace")
        .annotate(anzahl=Count("article_id", distinct=True))
    )

    # Labels aus den HERSTELLER-Anbindungen (falls angelegt).
    labels = dict(
        SupplierConnection.objects.filter(
            source_namespace__in=namespaces, connection_kind="HERSTELLER"
        ).values_list("source_namespace", "label")
    )

    return [
        {
            "namespace": ns,
            "label": labels.get(ns) or ns.capitalize(),
            "anzahl": int(zaehlung.get(ns, 0)),
        }
        for ns in namespaces
    ]
