"""API der technischen Anlagen — `property.technical_asset`.

Liegt unter dem Präfix `/api/property` und hängt am Rechtemodul **`property`**:
Die Anlage ist Objektstammdatum, kein Vorgangswert (wie der Raum, siehe
`api/raum.py`). Die View rechnet und prüft nichts — das tut
`db_core.services.anlage`. Fachfehler → 422, fehlende Zeile → 404.

**Es gibt kein DELETE.** Eine Anlage wird stillgelegt
(`PATCH … {"status": "INAKTIV"}`), nicht gelöscht: Aufträge, Prüfungen und
Berichte zeigen auf sie. Seit Migration 0101 verbietet es zusätzlich der
No-Delete-Trigger — der Schutz hängt nicht mehr allein am fehlenden Pfad.

**row_scope 'EIGENE' (Objektsicht, 0099).** Die Anlage ist genau das, was der
Monteur am Objekt wissen und erfassen muss („zentrale Anlage oder Etagentherme?").
Er sieht und pflegt sie deshalb an **seinen** Objekten. Die Grenze „was ist
meins?" wird hier nicht nachgebaut, sondern kommt aus der einen Definition
(`db_core/services/objektsicht.py`, HTTP-Tor `api/objektgrenze.py`): fremdes
Objekt → **404**, nicht 403.

**Das Detail bündelt drei Module — also tort es drei Module (Review-Fund).**
`GET /assets/{id}` liefert Wartungsverträge, Prüfungen und Fälligkeiten (Modul
**`maintenance`**) sowie Aufträge (Modul **`workflow`**). Nur `property/LESEN` zu
prüfen hieße: Wer die Liegenschaft sehen darf, bekommt hier Module, die ihm ihr
eigener Endpunkt mit 403 verweigert. Dass das heute kein Leck ist, liegt allein
daran, dass zufällig jede Rolle mit `property` auch `maintenance` hat — die
nächste Matrixzeile macht daraus wieder eins. **Ein Endpunkt, dessen Dichtheit von
einer zufälligen Eigenschaft der Rechtematrix abhängt, ist nicht dicht.**

Deshalb das Muster aus `api/dossier.py`: Der **Kern** (die Anlage selbst) ist hart
getort (`require_scoped` → 403/404); jeder **Baustein** prüft sein eigenes Modul
mit dem weichen `check_scoped()`. Fehlt das Recht, fehlt der **Baustein**
(`<modul>_sichtbar = false`), nicht die Antwort — sonst gäbe es kein Anlagendetail
für den, der 90 % davon sehen darf.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status

from api.objektgrenze import guard_objekt
from api.permissions import check_scoped, require_scoped
from db_core.models import Building, Property, Unit
from db_core.services import anlage as anlage_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class AssetIn(Schema):
    """Anlegen. `property_id` steht in der Route und ist **nicht** setzbar."""

    name: str
    asset_type: str
    building_id: UUID | None = None
    unit_id: UUID | None = None
    supply_type: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    year_built: int | None = None
    serial_number: str | None = None
    location_note: str | None = None
    energy_source: str | None = None
    power_kw: Decimal | None = None
    note: str | None = None
    status: str | None = None


class AssetPatch(Schema):
    """PATCH: nur die **gesendeten** Felder werden geändert.

    Alle Felder tragen einen Default, damit ein Teil-Payload gültig ist; welche
    gesetzt wurden, liest der Endpunkt über `dict(exclude_unset=True)` — sonst
    ließe sich eine einmal erfasste Angabe nie wieder leeren („nicht gesendet"
    wäre von „ausdrücklich null" nicht unterscheidbar).
    """

    name: str | None = None
    asset_type: str | None = None
    building_id: UUID | None = None
    unit_id: UUID | None = None
    supply_type: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    year_built: int | None = None
    serial_number: str | None = None
    location_note: str | None = None
    energy_source: str | None = None
    power_kw: Decimal | None = None
    note: str | None = None
    status: str | None = None


class NutzerOut(Schema):
    """Wer in der Einheit sitzt, an der die Anlage hängt — zum Anrufen.

    Kommt aus `tenure.occupancy` (Reiter Belegung), nicht aus einem zweiten
    Feld an der Anlage: Es gibt genau eine Wahrheit darüber, wer wo wohnt.
    """

    party_id: UUID
    display_name: str
    #: MIETER | EIGENTUEMER_BEWOHNT | … (Rolle aus der Belegung)
    rolle: str
    telefon: str | None = None
    email: str | None = None


class AssetOut(Schema):
    id: UUID
    property_id: UUID
    name: str
    asset_type: str
    status: str
    supply_type: str
    building_id: UUID | None = None
    unit_id: UUID | None = None
    # Aufgelöste Bezeichnungen für die Anzeige (der Client soll dafür nicht die
    # Liegenschaftsmappe nachladen müssen).
    building_label: str | None = None
    unit_label: str | None = None
    #: Etage der Einheit (`property.unit.storey`, 0124) — Freitext („2. OG").
    unit_storey: str | None = None
    #: Die Bewohner der Einheit. Leer, wenn die Anlage keiner Einheit zugeordnet
    #: ist oder die Einheit gerade niemand bewohnt.
    nutzer: list[NutzerOut] = []
    #: Ob das Modul `tenure` überhaupt gelesen werden durfte. Ohne dieses Flag
    #: hieße eine leere `nutzer`-Liste „niemand wohnt hier" — und das wäre
    #: gelogen, wenn nur das Recht fehlt.
    belegung_sichtbar: bool = False
    manufacturer: str | None = None
    model: str | None = None
    year_built: int | None = None
    serial_number: str | None = None
    location_note: str | None = None
    energy_source: str | None = None
    # `null` heißt **unbekannt**, nie 0 kW.
    power_kw: Decimal | None = None
    note: str | None = None


class VertragOut(Schema):
    id: UUID
    contract_number: str
    name: str
    status: str
    next_due_date: date | None = None
    #: ANLAGE — der Vertrag nennt diese Anlage ausdrücklich (0135).
    #: LIEGENSCHAFT — der Vertrag nennt gar keine Anlage und gilt fürs Objekt.
    #: Verträge, die nur ANDERE Anlagen abdecken, stehen hier nicht mehr.
    bezug: str


class PruefungOut(Schema):
    id: UUID
    name: str
    status: str
    next_due_date: date | None = None


class AuftragOut(Schema):
    id: UUID
    order_number: str
    title: str
    status: str


class FaelligkeitOut(Schema):
    id: UUID
    kind: str
    title: str
    due_date: date
    status: str


class AssetDetailOut(AssetOut):
    wartungsvertraege: list[VertragOut]
    pruefungen: list[PruefungOut]
    faelligkeiten: list[FaelligkeitOut]
    auftraege: list[AuftragOut]
    # **Warum ein Baustein fehlt, wird ausgesprochen** — eine leere Liste ohne
    # dieses Flag hieße „nichts vorhanden", und das wäre gelogen.
    maintenance_sichtbar: bool
    workflow_sichtbar: bool


# --- Abbildung -------------------------------------------------------------

def standort_labels(assets):
    """Gebäude-/Einheitsangaben für eine Menge Anlagen — zwei Queries.

    Die Einheit liefert Nummer **und Etage** (`storey`, 0124): „Therme in WE 12"
    schickt den Monteur ins Treppenhaus, „WE 12, 3. OG" an die Tür.
    """
    b_ids = {a.building_id for a in assets if a.building_id}
    u_ids = {a.unit_id for a in assets if a.unit_id}
    gebaeude = {
        b.id: (b.name or f"Gebäude {b.building_number}")
        for b in Building.objects.filter(id__in=b_ids)
    }
    einheiten = {
        u.id: (u.unit_number, u.storey) for u in Unit.objects.filter(id__in=u_ids)
    }
    return gebaeude, einheiten


def _nutzer_map(request, actor, assets):
    """`{unit_id: [NutzerOut, …]}` für die Einheiten dieser Anlagen — oder `None`.

    `None` heißt **„durfte nicht nachsehen"** und ist etwas anderes als ein
    leeres Dict („nachgesehen, niemand da"). Der Aufrufer gibt den Unterschied
    als `belegung_sichtbar` weiter.

    Warum überhaupt hier: Wer eine Etagentherme im Auftrag hat, braucht die
    Nummer der Person, die aufmacht. Bisher stand sie im Reiter Belegung —
    also drei Klicks und ein Abgleich „welche Einheit war das noch?" entfernt.

    **Getort am Modul `tenure`, nicht an `property`.** Dieselbe Begründung wie
    in der Kopfzeile (`api/property.py::_sichtbar`): Die Rechtematrix ist eine
    Datentabelle; eine einzige Zeile mit `tenure/LESEN` = NEIN machte die
    Anlagenliste sonst zum Umgehungspfad für Mieterdaten. Und dieselbe
    Objektgrenze: fremdes Objekt → als wäre kein Recht da.

    Zwei Abfragen für die ganze Liste (Belegungen + Kontaktwege), kein N+1.
    """
    from db_core.services import belegung as belegung_service
    from db_core.services import identity as identity_service
    from db_core.services import objektsicht

    # Das Recht wird ZUERST geprüft, auch wenn danach gar keine Einheit übrig
    # bleibt: Sonst meldete die Antwort `belegung_sichtbar = true` für einen
    # Aufrufer, dessen Recht nie geprüft wurde — ein Flag, das nur zufällig
    # stimmt, ist keins.
    #
    # `check_scoped`, NICHT `check`: `check` liefert bei Scope 'EIGENE' `None`
    # (es sitzt auf dem fail-closed `require`) und hätte dem Monteur die Mieter
    # **seines eigenen** Objekts verweigert — die ihm der Belegungs-Endpunkt
    # gleichzeitig liefert. Die Grenze zieht stattdessen `ist_eigenes_objekt`.
    scope = check_scoped(request, "tenure", "LESEN")
    if scope is None:
        return None
    property_ids = {a.property_id for a in assets}
    if scope == "EIGENE" and not all(
        objektsicht.ist_eigenes_objekt(actor, pid) for pid in property_ids
    ):
        return None

    unit_ids = {a.unit_id for a in assets if a.unit_id}
    if not unit_ids:
        return {}

    zeilen = {
        occ.unit_id: belegung_service.aktive_mieter(occ)
        for occ in belegung_service.belegungen_der_einheiten(unit_ids)
    }
    wege = identity_service.contact_points_bulk(
        {z.party_id for liste in zeilen.values() for z in liste}
    )

    ergebnis = {}
    for unit_id, liste in zeilen.items():
        for z in liste:
            kontakte = wege.get(z.party_id, [])
            telefon = next(
                (c.value for c in kontakte if c.contact_type == "MOBILE"), None
            ) or next((c.value for c in kontakte if c.contact_type == "PHONE"), None)
            email = next(
                (c.value for c in kontakte if c.contact_type == "EMAIL"), None
            )
            ergebnis.setdefault(unit_id, []).append(
                NutzerOut(
                    party_id=z.party_id,
                    display_name=z.party.display_name,
                    rolle=z.role,
                    telefon=telefon,
                    email=email,
                )
            )
    return ergebnis


def _asset_out(asset, gebaeude, einheiten, nutzer=None, klasse=AssetOut, **extra):
    einheit = einheiten.get(asset.unit_id) if asset.unit_id else None
    return klasse(
        id=asset.id,
        property_id=asset.property_id,
        name=asset.name,
        asset_type=asset.asset_type,
        status=asset.status,
        supply_type=asset.supply_type,
        building_id=asset.building_id,
        unit_id=asset.unit_id,
        building_label=gebaeude.get(asset.building_id),
        unit_label=einheit[0] if einheit else None,
        unit_storey=einheit[1] if einheit else None,
        nutzer=(nutzer or {}).get(asset.unit_id, []) if nutzer is not None else [],
        belegung_sichtbar=nutzer is not None,
        manufacturer=asset.manufacturer,
        model=asset.model,
        year_built=asset.year_built,
        serial_number=asset.serial_number,
        location_note=asset.location_note,
        energy_source=asset.energy_source,
        power_kw=asset.power_kw,
        note=asset.note,
        **extra,
    )


def _baustein_sichtbar(request, actor, modul, property_id):
    """Darf der Akteur dieses Modul an DIESEM Objekt lesen? (Baustein-Tor)

    Dasselbe Muster wie `api/property.py::_sichtbar`. Wichtig ist `check_scoped`
    statt `check`: `check` sitzt auf dem fail-closed `require` und liefert bei
    Scope `'EIGENE'` `None` — der Baustein fehlte dann ausgerechnet dem
    **Monteur an seinem eigenen Objekt**, obwohl `api/maintenance.py` und
    `api/auftrag.py` ihm dieselben Zeilen sehr wohl liefern (Migration 0100:
    „Wer vor der Zentralanlage steht, muss wissen, ob sie unter Wartungsvertrag
    steht"). Die Grenze zieht stattdessen die Objektsicht — mit dem Scope
    **dieses** Moduls, nie dem von `property`.
    """
    scope = check_scoped(request, modul, "LESEN")
    if scope is None:
        return False
    if scope == "EIGENE":
        from db_core.services import objektsicht

        return objektsicht.ist_eigenes_objekt(actor, property_id)
    return True


def _detail_out(request, actor, asset):
    """Detail bauen — **jeder Baustein an seinem eigenen Modul** (siehe Modulkopf).

    Fehlt ein Modulrecht (oder ist es auf `'EIGENE'` gestellt und das Objekt
    gehört nicht dazu), fehlt der **Baustein** — nicht die Antwort. Das Flag
    `*_sichtbar` spricht den Unterschied aus.
    """
    gebaeude, einheiten = standort_labels([asset])
    b = anlage_service.bezuege(
        asset,
        maintenance=_baustein_sichtbar(
            request, actor, "maintenance", asset.property_id
        ),
        workflow=_baustein_sichtbar(request, actor, "workflow", asset.property_id),
    )
    return _asset_out(
        asset,
        gebaeude,
        einheiten,
        _nutzer_map(request, actor, [asset]),
        klasse=AssetDetailOut,
        wartungsvertraege=[
            VertragOut(
                id=v["contract"].id,
                contract_number=v["contract"].contract_number,
                name=v["contract"].name,
                status=v["contract"].status,
                next_due_date=v["contract"].next_due_date,
                bezug=v["bezug"],
            )
            for v in b["wartungsvertraege"]
        ],
        pruefungen=[
            PruefungOut(
                id=p.id, name=p.name, status=p.status, next_due_date=p.next_due_date
            )
            for p in b["pruefungen"]
        ],
        faelligkeiten=[
            FaelligkeitOut(
                id=f.id, kind=f.kind, title=f.title, due_date=f.due_date,
                status=f.status,
            )
            for f in b["faelligkeiten"]
        ],
        auftraege=[
            AuftragOut(
                id=a.id, order_number=a.order_number, title=a.title, status=a.status
            )
            for a in b["auftraege"]
        ],
        maintenance_sichtbar=b["maintenance_sichtbar"],
        workflow_sichtbar=b["workflow_sichtbar"],
    )


def _asset_or_404_scoped(asset_id, actor, scope):
    """Anlage laden — bei Scope 'EIGENE' nur, wenn sie an meinem Objekt hängt.

    Eine Anlage an einem fremden Objekt ist **404**, nicht 403: Sie soll nicht
    einmal als existent erkennbar sein (Hausregel, `backend/README.md`).
    """
    asset = anlage_service.get_asset(asset_id)
    if asset is None:
        raise HttpError(404, "Anlage nicht gefunden.")
    guard_objekt(scope, actor, asset.property_id, "Anlage nicht gefunden.")
    return asset


def _property_or_404(property_id):
    if not Property.objects.filter(id=property_id).exists():
        raise HttpError(404, "Liegenschaft nicht gefunden.")


# --- Endpunkte -------------------------------------------------------------

@router.get("/properties/{property_id}/assets", response=list[AssetOut])
def list_assets(request, property_id: UUID, mit_inaktiven: bool = False):
    """Technische Anlagen einer Liegenschaft — **standardmäßig nur die aktiven**.

    Stillgelegte Anlagen liefert der Server nur auf ausdrückliche Nachfrage; sie
    bleiben lesbar (die Aufträge von damals zeigen weiter auf sie).

    Reine Objektstammdaten, kein fremdes Modul → nur `property/LESEN`.
    Scope 'EIGENE': fremdes Objekt → 404.
    """
    actor, scope = require_scoped(request, "property", "LESEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    assets = anlage_service.list_assets(property_id, mit_inaktiven=mit_inaktiven)
    gebaeude, einheiten = standort_labels(assets)
    nutzer = _nutzer_map(request, actor, assets)
    return [_asset_out(a, gebaeude, einheiten, nutzer) for a in assets]


@router.post("/properties/{property_id}/assets", response={201: AssetDetailOut})
def create_asset(request, property_id: UUID, payload: AssetIn):
    """Anlage an einer Liegenschaft erfassen (Scope 'EIGENE': nur an meiner).

    Die Liegenschaft kommt aus der **Route**: An einer fremden Liegenschaft lässt
    sich so keine Anlage anlegen, auch nicht mit einem gefälschten Payload — und
    unter Scope 'EIGENE' schon gar nicht (`guard_objekt` → 404).
    """
    actor, scope = require_scoped(request, "property", "ANLEGEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    try:
        asset = anlage_service.create_asset(
            actor, property_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _detail_out(request, actor, asset))


@router.get("/assets/{asset_id}", response=AssetDetailOut)
def get_asset(request, asset_id: UUID):
    """Anlagen-Detail: Stammdaten + Wartung, Prüfungen, Aufträge, Fälligkeiten.

    Der **Kern** hängt an `property/LESEN`; Wartung/Prüfungen/Fälligkeiten hängen
    an `maintenance/LESEN`, die Aufträge an `workflow/LESEN` — je Baustein einzeln
    (siehe Modulkopf). Fehlt ein Recht, fehlt der Baustein, nicht die Antwort.
    """
    actor, scope = require_scoped(request, "property", "LESEN")
    return _detail_out(request, actor, _asset_or_404_scoped(asset_id, actor, scope))


@router.patch("/assets/{asset_id}", response=AssetDetailOut)
def update_asset(request, asset_id: UUID, payload: AssetPatch):
    """Anlage ändern — **und stilllegen** (`status='INAKTIV'`).

    Ein DELETE gibt es nicht: Aufträge, Prüfungen und Berichte zeigen auf die
    Anlage; ihr Verschwinden wäre Geschichtsfälschung (GoBD/Audit).

    Scope 'EIGENE': nur an meinem Objekt (sonst 404).
    """
    actor, scope = require_scoped(request, "property", "AENDERN")
    _asset_or_404_scoped(asset_id, actor, scope)
    try:
        asset = anlage_service.update_asset(
            actor, asset_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        if "existiert nicht" in str(exc):
            raise HttpError(404, "Anlage nicht gefunden.")
        raise HttpError(422, str(exc))
    return _detail_out(request, actor, asset)
