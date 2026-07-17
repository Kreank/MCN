"""Gerätewissen-API — durchsuchbare Sicht auf Hersteller-Ersatzteile.

Read-only. Zeigt AUSSCHLIESSLICH Artikel, die eine Lieferantenreferenz in einem
der Hersteller-Namensräume (`vaillant`, `junkers`, …) mit `source_system='DATANORM'`
tragen — eine gefilterte Sicht auf `pricing.article`, kein eigenes Datensilo. Der
Großhandels-Namensraum `bo` (~2 Mio Artikel) erscheint hier NIE.

Recht `pricing/LESEN`, Torfunktion `require` (fail-closed): `pricing` kennt keine
'EIGENE'-Rolle, ein Scope-Konflikt kann hier also nicht auftreten. Die
Objektsicht/Scope-Absicherung ist deckungsgleich mit `api/artikel.py`.
"""
from decimal import Decimal
from uuid import UUID

from ninja import Query, Router, Schema
from ninja.errors import HttpError

from api.permissions import require
from db_core.services import geraetewissen as geraetewissen_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class ErsatzteilOut(Schema):
    article_id: UUID
    #: interne MCN-Nummer (DN-… nach DATANORM-Import)
    article_number: str
    #: herstellereigene Sachnummer (die am Gerät gesuchte Nummer)
    supplier_article_number: str | None = None
    description: str
    manufacturer_name: str | None = None
    #: Hersteller-Namensraum (vaillant | junkers | …)
    namespace: str | None = None
    unit: str
    list_price: Decimal | None = None


class ErsatzteilListeOut(Schema):
    items: list[ErsatzteilOut]
    total: int
    page: int
    page_size: int


class ErsatzteilDetailOut(ErsatzteilOut):
    long_description: str | None = None
    manufacturer_number: str | None = None
    manufacturer_type: str | None = None
    product_group: str | None = None
    matchcode: str | None = None
    #: Hersteller-/Händler-Listenpreis aus der Referenz (falls vorhanden)
    supplier_list_price: Decimal | None = None
    last_purchase_price: Decimal | None = None
    currency: str | None = None


class HerstellerOut(Schema):
    namespace: str
    label: str
    anzahl: int


class ErsatzteilFilter(Schema):
    q: str | None = None
    #: auf genau einen Hersteller-Namensraum eingrenzen (Filter-Chip)
    namespace: str | None = None


# --- Endpoints -------------------------------------------------------------

@router.get("/hersteller", response=list[HerstellerOut])
def list_hersteller(request):
    """Die konfigurierten Hersteller mit Ersatzteilzahl (Filter-Chips).

    Liefert jeden konfigurierten Namensraum, auch mit `anzahl=0` (Katalog noch
    nicht importiert) — das Frontend zeigt dann den erklärenden Leerzustand.
    """
    require(request, "pricing", "LESEN")
    return geraetewissen_service.hersteller()


@router.get("/ersatzteile", response=ErsatzteilListeOut)
def list_ersatzteile(
    request,
    filters: ErsatzteilFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Ersatzteile suchen/auflisten. Volltext über Nummer/Kurz-/Langtext/Fabrikat
    und herstellereigene Nummer (Hero-Operatoren `+`/`|`/`*`), Filter nach
    Hersteller-Namensraum, Paginierung. Nur AKTIVE Artikel.

    Ein leeres Ergebnis (noch kein Katalog importiert) ist ein gültiger Zustand —
    kein Fehler.
    """
    require(request, "pricing", "LESEN")
    items, total = geraetewissen_service.suche(
        q=filters.q,
        namespace=filters.namespace,
        page=page,
        page_size=page_size,
    )
    return ErsatzteilListeOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/ersatzteile/{article_id}", response=ErsatzteilDetailOut)
def get_ersatzteil(request, article_id: UUID):
    """Voll-Detail eines Ersatzteils (read-only). 404, wenn der Artikel nicht in
    die Gerätewissen-Sicht fällt (kein Hersteller-Namensraum / inaktiv) — die
    Detailsicht spiegelt exakt die Liste, ein reiner Großhandelsartikel ist hier
    nicht auffindbar."""
    require(request, "pricing", "LESEN")
    data = geraetewissen_service.detail(article_id)
    if data is None:
        raise HttpError(404, "Ersatzteil nicht gefunden.")
    return data
