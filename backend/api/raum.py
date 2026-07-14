"""Raumaufmaß-API — Räume, Hüllflächen, Öffnungen, Heizlast (property.room).

Liegt unter demselben Präfix wie die Liegenschafts-API (`/api/property`) und
hängt am selben Recht: der Raum ist **Objektstammdatum**, kein Vorgangswert
(Modulkopf der Migration 0086).

**row_scope 'EIGENE' (Objektsicht, Migration 0099).** Das Raumaufmaß ist der
Schreibfall, für den der Monteur die Objektsicht überhaupt bekommen hat: Er nimmt
vor Ort auf, was er misst. Die Grenze ist überall dieselbe — **meine Objekte**
(`db_core/services/objektsicht.py`); ein fremdes Objekt bzw. ein Raum daran ist
**404**.

**Der Bauteilkatalog ist die Ausnahme: er ist GLOBAL.** Eine Vorlage
(„Doppelkastenfenster", „Außenwand 24 cm") hängt an keinem Objekt — sie gilt für
alle. Deshalb:

  * **Lesen** (`GET /component-templates`): für 'EIGENE' erlaubt, **ohne** Filter —
    es gibt keinen, und ohne Katalog könnte der Monteur kein Aufmaß erfassen.
  * **Anlegen/Ändern**: fail-closed **403** (`require`). Wer den Katalog ändert,
    ändert ihn für **jedes** Objekt und jede Kollegin — das ist keine
    Baustellenentscheidung.

**Die Auslegungsdaten der Liegenschaft** (`PATCH /properties/{id}/auslegung`) bleiben
ebenfalls fail-closed (403): Norm-Außentemperatur und Heizlast-Kennwert sind eine
Planungsvorgabe, die der Betrieb verantwortet — sie ändern die Heizlast **aller**
Räume des Objekts. Der Monteur liest sie (sie stehen in jeder Kennzahl mit drin).

Die View rechnet nichts: sämtliche Kennzahlen und die Heizlast kommen aus
`db_core.services.raum` (die einzige Rechenstelle). Fachfehler → 422, fehlende
Zeile → 404.

**Die Auslegungsdaten kommen vom Objekt** (Migration 0089): Jeder Endpunkt zieht
`design_outdoor_temp_c` und `heat_load_w_per_m2` aus der Liegenschaft des Raumes
— der Client muss nichts mitschicken, damit eine Heizlast entsteht. Die
Liegenschaft wird dafür je Anfrage **einmal** geladen (kein N+1 über die Räume).
Die Query-Parameter `aussentemperatur_c`/`kennwert_w_m2` übersteuern die
Objektwerte (Was-wäre-wenn); gepflegt werden sie über
`PATCH /properties/{id}/auslegung`. **Sie sind kein Nebeneingang:** Der Service
prüft sie gegen exakt dieselben Grenzen wie den Schreibpfad und den DB-CHECK
(0089) — ein ungültiger Parameter ergibt **422 mit Grund**, nie ein gerechnetes
Ergebnis (`kennwert_w_m2=0` hieße sonst „0,0 kW", nicht „unbekannt").

**Der Bauteilkatalog ist eine Kopierquelle** (Migration 0090): `template_id` an
Wand und Öffnung ist ein **Herkunftsvermerk**; der U-Wert wird beim Erfassen in
die Zeile kopiert. Kein Endpunkt hier liest für eine Heizlast je den Katalog —
eine spätere Katalogkorrektur ändert kein bestehendes Aufmaß.

**Der Grundriss ist die Quelle der Kennzahlen** (Migration 0091): Hat ein Raum
einen Umriss (`PUT /rooms/{id}/grundriss`), rechnet der Server `floor_area_m2`
und `perimeter_m` daraus. `kennzahlen.geometrie_quelle` sagt dem UI, ob es die
beiden Felder als Eingabe (`EINGEGEBEN`) oder als Ergebnis (`GEZEICHNET`) zeigen
muss — im zweiten Fall verwirft der Server einen mitgeschickten Wert.
"""
from decimal import Decimal
from uuid import UUID

from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status

from api.objektgrenze import guard_objekt
from api.permissions import require, require_scoped
from db_core.models import Property
from db_core.services import bauteilkatalog as katalog_service
from db_core.services import raum as raum_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class RoomIn(Schema):
    name: str
    storey: str | None = None
    room_type: str | None = None
    building_id: UUID | None = None
    unit_id: UUID | None = None
    floor_area_m2: Decimal
    length_m: Decimal | None = None
    width_m: Decimal | None = None
    room_height_m: Decimal
    perimeter_m: Decimal | None = None
    indoor_temp_c: Decimal | None = None
    air_change_rate: Decimal | None = None
    heat_load_w_per_m2: Decimal | None = None
    riser_distance_m: Decimal | None = None
    status: str | None = None
    note: str | None = None


class RoomPatch(Schema):
    """PATCH: nur die **gesendeten** Felder werden geändert.

    Alle Felder tragen einen Default, damit ein Teil-Payload gültig ist; welche
    Felder tatsächlich gesetzt wurden, liest der Endpunkt über
    `dict(exclude_unset=True)` — sonst ließe sich ein Feld nicht mehr auf `null`
    zurücksetzen (Löschen einer Angabe wäre nicht von „nicht gesendet"
    unterscheidbar).
    """

    name: str | None = None
    storey: str | None = None
    room_type: str | None = None
    building_id: UUID | None = None
    unit_id: UUID | None = None
    floor_area_m2: Decimal | None = None
    length_m: Decimal | None = None
    width_m: Decimal | None = None
    room_height_m: Decimal | None = None
    perimeter_m: Decimal | None = None
    indoor_temp_c: Decimal | None = None
    air_change_rate: Decimal | None = None
    heat_load_w_per_m2: Decimal | None = None
    riser_distance_m: Decimal | None = None
    status: str | None = None
    note: str | None = None


class SurfaceIn(Schema):
    # Freier Schlüssel des Clients ('s1', 's2', …). Die Öffnungen zeigen darüber
    # auf ihre Wand; der Service löst ihn auf die neu erzeugte UUID auf.
    ref: str
    surface_type: str
    adjacent: str
    orientation: str | None = None
    label: str | None = None
    # Optional, SOBALD `edge_index` gesetzt ist: Dann rechnet der Server
    # Kantenlänge × Raumhöhe. Ein mitgeschickter Wert GEWINNT (Giebelwand, Erker).
    # Ohne Kante bleibt er Pflicht (sonst wäre die Wandfläche unbekannt).
    gross_area_m2: Decimal | None = None
    # Ohne eigenen `u_value` kopiert der Server den der Vorlage (0090). Mit
    # eigenem `u_value` gewinnt dieser — ein Messwert schlägt den Katalog.
    template_id: UUID | None = None
    u_value: Decimal | None = None
    temp_factor: Decimal | None = None
    # Kante des Umrisses (0091), auf der die Wand steht: 0 ≤ i < Punktzahl.
    edge_index: int | None = None


class OpeningIn(Schema):
    # None = reiner Mengenabzug ohne Bauteilzuordnung (zählt nicht in die
    # Transmission).
    surface_ref: str | None = None
    opening_type: str
    label: str | None = None
    quantity: int = 1
    width_m: Decimal
    height_m: Decimal
    template_id: UUID | None = None
    u_value: Decimal | None = None
    # Abstand der linken Öffnungskante vom Anfangspunkt der Wandkante (0091).
    # Weglassen ist gültig: die Öffnung zählt dann voll in Fläche und Heizlast,
    # sie wird nur nicht gezeichnet. Sie wird NICHT auf 0 gesetzt — fehlende Lage
    # heißt unbekannt, nicht „bei 0 m".
    position_m: Decimal | None = None


class AufbauIn(Schema):
    surfaces: list[SurfaceIn] = []
    openings: list[OpeningIn] = []


class VertexIn(Schema):
    x_mm: int
    y_mm: int


class GrundrissIn(Schema):
    """Umriss des Raumes. Reihenfolge = Umlauf; leeres Array = Umriss entfernen."""

    vertices: list[VertexIn] = []


class VertexOut(Schema):
    idx: int
    x_mm: int
    y_mm: int


class SurfaceOut(Schema):
    id: UUID
    surface_type: str
    adjacent: str
    orientation: str | None = None
    label: str | None = None
    gross_area_m2: Decimal
    u_value: Decimal | None = None
    temp_factor: Decimal | None = None
    net_area_m2: Decimal
    # Herkunft aus dem Katalog — reine ANZEIGE ('aus: Fenster, 3-fach'). Der
    # U-Wert oben ist eine Kopie und bleibt es.
    template_id: UUID | None = None
    edge_index: int | None = None
    # Abgeleitet aus dem Umriss; `null`, wenn die Wand auf keiner Kante steht.
    edge_length_m: Decimal | None = None
    # true  = `gross_area_m2` ist aus Kantenlänge × Raumhöhe GERECHNET und wird bei
    #         jeder Änderung von Umriss oder Raumhöhe neu gerechnet („aus der
    #         Zeichnung berechnet" — das UI zeigt sie als Ergebnis, nicht als Feld).
    # false = Handeingabe (Giebel, Erker, Dachschräge) und wird NIE überschrieben.
    # Kein Feld in `SurfaceIn`: Der Client sagt das nicht — es ergibt sich daraus,
    # OB er eine `gross_area_m2` mitschickt.
    area_is_derived: bool


class OpeningOut(Schema):
    id: UUID
    surface_id: UUID | None = None
    opening_type: str
    label: str | None = None
    quantity: int
    width_m: Decimal
    height_m: Decimal
    u_value: Decimal | None = None
    area_m2: Decimal
    template_id: UUID | None = None
    # `null` = Lage nicht ausgemessen (die Öffnung zählt trotzdem voll mit).
    position_m: Decimal | None = None


class TemplateIn(Schema):
    """Bauteilvorlage (Katalog, Migration 0090).

    `u_value` ist **nicht** Pflicht: Der Katalog wird ohne U-Werte ausgeliefert
    (keine DIN-Tabellen im Produkt). Eine Vorlage ohne Wert ist der Normalzustand;
    sie verhält sich wie ein fehlender U-Wert — Heizlast unbekannt, nicht 0.
    """

    kind: str
    name: str
    default_surface_type: str | None = None
    default_opening_type: str | None = None
    u_value: Decimal | None = None
    note: str | None = None
    status: str | None = None
    sort_index: int | None = None


class TemplatePatch(Schema):
    """PATCH: nur die **gesendeten** Felder werden geändert (`exclude_unset`).

    `kind` lässt sich nicht ändern (die Gattung ist die Identität der Vorlage —
    bestehende Aufmaße zeigen darauf).
    """

    name: str | None = None
    default_surface_type: str | None = None
    default_opening_type: str | None = None
    u_value: Decimal | None = None
    note: str | None = None
    status: str | None = None
    sort_index: int | None = None


class TemplateOut(Schema):
    id: UUID
    kind: str
    name: str
    default_surface_type: str | None = None
    default_opening_type: str | None = None
    u_value: Decimal | None = None
    note: str | None = None
    status: str
    sort_index: int


class TemplateFilter(Schema):
    kind: str | None = None
    # Regelfall: nur wählbare Vorlagen. Stillgelegte bleiben lesbar, damit die
    # Herkunftsangabe eines bestehenden Aufmaßes anzeigbar bleibt.
    nur_aktive: bool = True


class KennzahlenOut(Schema):
    floor_area_m2: Decimal
    volume_m3: Decimal
    perimeter_m: Decimal | None = None
    # GEZEICHNET = Fläche und Umfang kommen aus dem Umriss (der Server rechnet sie
    # und verwirft Client-Werte). EINGEGEBEN = Handeingabe wie bisher.
    geometrie_quelle: str
    # `None` heißt UNBEKANNT, nie 0 — auch hier: ist für den Raum KEINE Hüllfläche
    # aufgenommen, ist seine Wandfläche unbekannt. Eine 0 liefe als Mengengrundlage
    # (Verputzen, Streichen) in ein Angebot.
    wall_area_gross_m2: Decimal | None = None
    opening_area_m2: Decimal
    wall_area_net_m2: Decimal | None = None
    # `None` heißt UNBEKANNT, nie 0 — siehe services/raum.py.
    heizlast_kennwert_w: Decimal | None = None
    transmission_w: Decimal | None = None
    lueftung_w: Decimal | None = None
    heizlast_huellflaeche_w: Decimal | None = None
    unbekannt_grund: str | None = None
    hinweise: list[str] = []


class RoomOut(Schema):
    id: UUID
    property_id: UUID
    building_id: UUID | None = None
    unit_id: UUID | None = None
    storey: str | None = None
    name: str
    room_type: str | None = None
    floor_area_m2: Decimal
    length_m: Decimal | None = None
    width_m: Decimal | None = None
    room_height_m: Decimal
    perimeter_m: Decimal | None = None
    volume_m3: Decimal
    indoor_temp_c: Decimal | None = None
    air_change_rate: Decimal | None = None
    heat_load_w_per_m2: Decimal | None = None
    riser_distance_m: Decimal | None = None
    status: str
    note: str | None = None
    surfaces: list[SurfaceOut] = []
    openings: list[OpeningOut] = []
    # Umriss in Umlaufreihenfolge (leer = nicht gezeichnet). Kante i = (idx i → i+1),
    # zyklisch.
    vertices: list[VertexOut] = []
    kennzahlen: KennzahlenOut


class AuslegungIn(Schema):
    """Auslegungsdaten der Liegenschaft (PATCH).

    Beide Felder sind optional: **nicht gesendet = unverändert**, ausdrücklich
    `null` = zurückgesetzt. Der Endpunkt unterscheidet das über
    `dict(exclude_unset=True)` — ohne diese Trennung ließe sich ein einmal
    gesetzter Wert nie wieder löschen.
    """

    design_outdoor_temp_c: Decimal | None = None
    heat_load_w_per_m2: Decimal | None = None


class AuslegungOut(Schema):
    design_outdoor_temp_c: Decimal | None = None
    heat_load_w_per_m2: Decimal | None = None


class AufmassOut(Schema):
    raeume_anzahl: int
    # Die WIRKSAMEN Auslegungsdaten dieser Anfrage: die Werte der Liegenschaft,
    # sofern kein Query-Parameter sie übersteuert. Das Panel zeigt sie an und
    # belegt damit sein Formular vor — kein zweiter Endpunkt nötig.
    design_outdoor_temp_c: Decimal | None = None
    heat_load_w_per_m2: Decimal | None = None
    # Fläche und Höhe sind in der DB NOT NULL — die leere Summe über 0 Räume ist
    # hier ehrlich 0.
    flaeche_m2: Decimal
    volumen_m3: Decimal
    # Umfang und Weg zur Steigleitung sind NULL-fähig: eine Summe über ein Feld,
    # das KEIN Raum trägt, ist keine 0, sondern unbekannt (`null` mit Grund in
    # `hinweise`). Eine Leitungslänge „0,0 m" liefe sonst als Menge in ein Angebot.
    umfang_m: Decimal | None = None
    heizlast_kennwert_w: Decimal | None = None
    heizlast_huellflaeche_w: Decimal | None = None
    unbekannt_raeume: list[str] = []
    leitungslaenge_schaetzung_m: Decimal | None = None
    raeume_ohne_steigleitung: int
    hinweise: list[str] = []


class AufmassFilter(Schema):
    """Optionale Übersteuerung der Auslegungsdaten des Objekts (Was-wäre-wenn).

    Die Werte kommen im Regelfall aus der Liegenschaft (0089); diese Parameter
    sind **nicht** die Quelle, sondern nur die Ausnahme („was, wenn wir mit −14 °C
    rechnen?"). MCN liefert keine Klimatabellen mit (Normrechtslage) — fehlt der
    Wert an beiden Stellen, bleibt die Heizlast unbekannt, nie 0.
    """

    aussentemperatur_c: Decimal | None = None
    kennwert_w_m2: Decimal | None = None


# --- Abbildung -------------------------------------------------------------

def _room_out(room, vorgabe):
    """RoomOut inkl. Kennzahlen.

    `vorgabe` ist die **einmal** gebildete `Auslegung` der Liegenschaft (siehe
    Modulkopf) — sie wird durchgereicht, damit die Liste nicht je Raum eine
    Liegenschafts-Query zieht. Fehlt ein Wert an Objekt und Aufruf, bleiben die
    Werte des Hüllflächenverfahrens `None` — mit Grund, nie 0.
    """
    oeffnungen = list(room.openings.all())
    belegt = {}
    for o in oeffnungen:
        if o.surface_id is not None:
            belegt[o.surface_id] = belegt.get(o.surface_id, Decimal(0)) + o.area_m2

    # Kantenlängen kommen aus derselben Rechenstelle wie Fläche und Umfang — die
    # API rechnet nicht selbst (Modulkopf).
    laengen = raum_service.kantenlaengen(room)

    def _kantenlaenge(s):
        if s.edge_index is None or s.edge_index >= len(laengen):
            return None
        return laengen[s.edge_index]

    surfaces = [
        SurfaceOut(
            id=s.id,
            surface_type=s.surface_type,
            adjacent=s.adjacent,
            orientation=s.orientation,
            label=s.label,
            gross_area_m2=s.gross_area_m2,
            u_value=s.u_value,
            temp_factor=s.temp_factor,
            net_area_m2=s.gross_area_m2 - belegt.get(s.id, Decimal(0)),
            template_id=s.template_id,
            edge_index=s.edge_index,
            edge_length_m=_kantenlaenge(s),
            area_is_derived=s.area_is_derived,
        )
        for s in sorted(room.surfaces.all(), key=lambda s: (s.surface_type, str(s.id)))
    ]
    openings = [
        OpeningOut(
            id=o.id,
            surface_id=o.surface_id,
            opening_type=o.opening_type,
            label=o.label,
            quantity=o.quantity,
            width_m=o.width_m,
            height_m=o.height_m,
            u_value=o.u_value,
            area_m2=o.area_m2,
            template_id=o.template_id,
            position_m=o.position_m,
        )
        for o in sorted(oeffnungen, key=lambda o: (o.opening_type, str(o.id)))
    ]
    vertices = [
        VertexOut(idx=v.idx, x_mm=v.x_mm, y_mm=v.y_mm) for v in room.vertices.all()
    ]
    return RoomOut(
        id=room.id,
        property_id=room.property_id,
        building_id=room.building_id,
        unit_id=room.unit_id,
        storey=room.storey,
        name=room.name,
        room_type=room.room_type,
        floor_area_m2=room.floor_area_m2,
        length_m=room.length_m,
        width_m=room.width_m,
        room_height_m=room.room_height_m,
        perimeter_m=room.perimeter_m,
        volume_m3=room.volume_m3,
        indoor_temp_c=room.indoor_temp_c,
        air_change_rate=room.air_change_rate,
        heat_load_w_per_m2=room.heat_load_w_per_m2,
        riser_distance_m=room.riser_distance_m,
        status=room.status,
        note=room.note,
        surfaces=surfaces,
        openings=openings,
        vertices=vertices,
        kennzahlen=KennzahlenOut(**raum_service.kennzahlen(room, vorgabe=vorgabe)),
    )


def _template_out(t):
    return TemplateOut(
        id=t.id,
        kind=t.kind,
        name=t.name,
        default_surface_type=t.default_surface_type,
        default_opening_type=t.default_opening_type,
        u_value=t.u_value,
        note=t.note,
        status=t.status,
        sort_index=t.sort_index,
    )


def _get_room_or_404(room_id):
    room = raum_service.get_room(room_id)
    if room is None:
        raise HttpError(404, "Raum nicht gefunden.")
    return room


def _property_or_404(property_id):
    prop = Property.objects.filter(id=property_id).first()
    if prop is None:
        raise HttpError(404, "Liegenschaft nicht gefunden.")
    return prop


def _vorgabe_fuer_raum(room, filters=None):
    """Auslegung der Liegenschaft dieses Raumes (eine Query), optional übersteuert."""
    return raum_service.auslegung_fuer(
        room.property_id,
        filters.aussentemperatur_c if filters else None,
        filters.kennwert_w_m2 if filters else None,
    )


def _room_or_404_scoped(room_id, actor, scope):
    """Raum laden — und bei Scope 'EIGENE' prüfen, dass er an meinem Objekt hängt.

    Ein Raum an einem fremden Objekt ist **404**, nicht 403: Er soll nicht einmal
    als existent erkennbar sein.
    """
    room = _get_room_or_404(room_id)
    guard_objekt(scope, actor, room.property_id, "Raum nicht gefunden.")
    return room


# --- Endpunkte -------------------------------------------------------------

@router.get("/component-templates", response=list[TemplateOut])
def list_component_templates(request, filters: TemplateFilter = Query(...)):
    """Bauteilkatalog: Wandaufbauten (`FLAECHE`) und Fenster-/Türarten (`OEFFNUNG`).

    Der Katalog macht aus „U-Wert 2,7 eintippen" ein „Doppelkastenfenster
    auswählen". Er wird **ohne U-Werte** ausgeliefert (Normrecht) — eine Vorlage
    ohne Wert ist der Normalzustand, kein Fehler.

    **Global, deshalb ungefiltert** — und deshalb `require_scoped` OHNE anschließende
    Begrenzung: Eine Vorlage hängt an keinem Objekt, es gibt nichts zu begrenzen. Das
    ist die **einzige** Stelle dieses Slices, an der `require_scoped` ohne Filter
    steht; sie ist hier ausdrücklich begründet, damit sie nicht als Vorbild für die
    objektgebundenen Endpunkte missverstanden wird.
    """
    require_scoped(request, "property", "LESEN")
    try:
        templates = katalog_service.list_templates(
            kind=filters.kind, nur_aktive=filters.nur_aktive
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return [_template_out(t) for t in templates]


@router.post("/component-templates", response={201: TemplateOut})
def create_component_template(request, payload: TemplateIn):
    """Eigene Bauteilvorlage anlegen (der Betrieb pflegt seinen Katalog selbst).

    `require` (fail-closed → 403 bei Scope 'EIGENE'): Der Katalog ist **global**. Wer
    hier schreibt, schreibt in jedes Objekt und für jede Kollegin — es gibt keine
    „eigene" Vorlage, auf die sich die Zeilenbegrenzung stützen könnte.
    """
    actor, _ = require(request, "property", "ANLEGEN")
    try:
        t = katalog_service.create_template(actor, payload.dict(exclude_unset=True))
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _template_out(t))


@router.patch("/component-templates/{template_id}", response=TemplateOut)
def update_component_template(request, template_id: UUID, payload: TemplatePatch):
    """Vorlage ändern — z. B. den U-Wert **einmal** hinterlegen.

    Es gibt **kein DELETE** (No-Delete-Trigger): Eine Vorlage, die schon in einem
    Aufmaß steckt, würde ihre Herkunftsangabe ins Leere zeigen lassen. Stillgelegt
    wird über `status = 'INAKTIV'` — sie ist dann nicht mehr wählbar, bestehende
    Aufmaße bleiben unberührt (ihr U-Wert ist eine Kopie).

    `require` (fail-closed → 403 bei 'EIGENE'): globaler Katalog, siehe
    `create_component_template`.
    """
    actor, _ = require(request, "property", "AENDERN")
    try:
        t = katalog_service.update_template(
            actor, template_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        if "existiert nicht" in str(exc):
            raise HttpError(404, "Bauteilvorlage nicht gefunden.")
        raise HttpError(422, str(exc))
    return _template_out(t)


@router.get("/properties/{property_id}/rooms", response=list[RoomOut])
def list_rooms(
    request,
    property_id: UUID,
    filters: AufmassFilter = Query(...),
    mit_inaktiven: bool = False,
):
    """Räume einer Liegenschaft inkl. Hüllflächen, Öffnungen und Kennzahlen.

    **Standard: nur AKTIVE Räume.** Ein stillgelegter Raum (umgebaut, weggefallen)
    verschwindet aus der Arbeitsliste, wird aber nie gelöscht — `mit_inaktiven=true`
    holt ihn zurück ins Bild.

    Die Auslegungsdaten der Liegenschaft werden **einmal** gelesen und in jeden
    Raum gereicht (kein N+1) — die Heizlast steht damit auch in der Liste, ohne
    dass der Client etwas mitschickt.

    Scope 'EIGENE': fremdes Objekt → 404.
    """
    actor, scope = require_scoped(request, "property", "LESEN")
    prop = _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    try:
        vorgabe = raum_service.auslegung(
            prop, filters.aussentemperatur_c, filters.kennwert_w_m2
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return [
        _room_out(r, vorgabe)
        for r in raum_service.list_rooms(property_id, mit_inaktiven=mit_inaktiven)
    ]


@router.post("/properties/{property_id}/rooms", response={201: RoomOut})
def create_room(request, property_id: UUID, payload: RoomIn):
    """Raum an einer Liegenschaft aufnehmen (Scope 'EIGENE': nur an meiner).

    `require_scoped` + `guard_objekt` statt `require_create`: Die erzeugte Zeile trägt
    ihr Elternobjekt (die Liegenschaft) im Pfad — genau der Fall, für den
    `require_create` laut eigenem Docstring NICHT gedacht ist. Sonst legte ein Monteur
    Räume an Objekten an, die er nie betreten hat.
    """
    actor, scope = require_scoped(request, "property", "ANLEGEN")
    prop = _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    try:
        room = raum_service.create_room(
            actor, property_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _room_out(room, raum_service.auslegung(prop)))


@router.get("/rooms/{room_id}", response=RoomOut)
def get_room(request, room_id: UUID, filters: AufmassFilter = Query(...)):
    actor, scope = require_scoped(request, "property", "LESEN")
    room = _room_or_404_scoped(room_id, actor, scope)
    try:
        return _room_out(room, _vorgabe_fuer_raum(room, filters))
    except ValueError as exc:
        raise HttpError(422, str(exc))


@router.patch("/rooms/{room_id}", response=RoomOut)
def update_room(request, room_id: UUID, payload: RoomPatch):
    """Teil-Update: nur gesendete Felder werden geändert (Scope 'EIGENE': nur an meinem Objekt)."""
    actor, scope = require_scoped(request, "property", "AENDERN")
    _room_or_404_scoped(room_id, actor, scope)
    try:
        room = raum_service.update_room(
            actor, room_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _room_out(room, _vorgabe_fuer_raum(room))


@router.put("/rooms/{room_id}/aufbau", response=RoomOut)
def set_aufbau(request, room_id: UUID, payload: AufbauIn):
    """Hüllflächen UND Öffnungen als Satz ersetzen (atomar).

    Die Öffnungen zeigen über `surface_ref` auf die Wand desselben Payloads; der
    Service löst den Schlüssel auf die neu erzeugte UUID auf.

    Scope 'EIGENE': nur an meinem Objekt (sonst 404).
    """
    actor, scope = require_scoped(request, "property", "AENDERN")
    _room_or_404_scoped(room_id, actor, scope)
    try:
        room = raum_service.set_aufbau(
            actor,
            room_id,
            [s.dict() for s in payload.surfaces],
            [o.dict() for o in payload.openings],
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _room_out(room, _vorgabe_fuer_raum(room))


@router.put("/rooms/{room_id}/grundriss", response=RoomOut)
def set_grundriss(request, room_id: UUID, payload: GrundrissIn):
    """Umriss des Raumes setzen (Polygon in Millimetern) — **als Satz**.

    Der Server rechnet daraus `floor_area_m2` (Gauß'sche Trapezformel, Betrag —
    der Umlaufsinn erzeugt keine negative Fläche) und `perimeter_m` (Σ
    Kantenlängen) und **schreibt sie**. Ab dann sind die beiden Felder kein
    Eingabefeld mehr: `PATCH /rooms/{id}` verwirft einen mitgeschickten Wert.

    Leeres Array = Umriss entfernen (Fläche/Umfang sind danach wieder Handeingabe;
    `edge_index` und `position_m` fallen dabei auf `null` — sie zeigten ins Leere).

    Scope 'EIGENE': nur an meinem Objekt (sonst 404).
    """
    actor, scope = require_scoped(request, "property", "AENDERN")
    _room_or_404_scoped(room_id, actor, scope)
    try:
        room = raum_service.set_grundriss(
            actor, room_id, [v.dict() for v in payload.vertices]
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _room_out(room, _vorgabe_fuer_raum(room))


@router.patch("/properties/{property_id}/auslegung", response=AuslegungOut)
def set_auslegung(request, property_id: UUID, payload: AuslegungIn):
    """Auslegungsdaten der Liegenschaft pflegen (Migration 0089).

    Ohne sie ist die Heizlast in der ganzen Anwendung „unbekannt" — sie sind die
    Voraussetzung dafür, dass das Hüllflächenverfahren überhaupt rechnet. MCN
    liefert dafür KEINE DIN-Klimatabelle mit: der Betrieb trägt die Werte ein,
    die er verantwortet.

    Nicht gesendet = unverändert; ausdrücklich `null` = zurückgesetzt.

    `require` (fail-closed → 403 bei Scope 'EIGENE'): Diese beiden Werte sind eine
    **Planungsvorgabe des Betriebs**, keine Baustellenmessung — sie ändern die
    Heizlast **jedes** Raumes dieses Objekts. Der Monteur liest sie (sie stecken in
    jeder Kennzahl), er setzt sie nicht.
    """
    actor, _ = require(request, "property", "AENDERN")
    _property_or_404(property_id)
    try:
        prop = raum_service.set_auslegung(
            actor, property_id, payload.dict(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return AuslegungOut(
        design_outdoor_temp_c=prop.design_outdoor_temp_c,
        heat_load_w_per_m2=prop.heat_load_w_per_m2,
    )


@router.get("/properties/{property_id}/aufmass", response=AufmassOut)
def aufmass(request, property_id: UUID, filters: AufmassFilter = Query(...)):
    """Gebäudesummen über alle **aktiven** Räume der Liegenschaft.

    Eine Heizlastsumme ist `None`, sobald ein Raum unbekannt ist — die fehlenden
    Räume als 0 W mitzusummieren machte die Auslegung still zu klein. Die
    wirksamen Auslegungsdaten stehen im Ergebnis.

    Scope 'EIGENE': fremdes Objekt → 404.
    """
    actor, scope = require_scoped(request, "property", "LESEN")
    _property_or_404(property_id)
    guard_objekt(scope, actor, property_id)
    try:
        return AufmassOut(
            **raum_service.aufmass_property(
                property_id, filters.aussentemperatur_c, filters.kennwert_w_m2
            )
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
