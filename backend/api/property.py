"""Property-API — Liegenschaften (Objektwelt: property.property).

Views bleiben dünn und rufen die Service-Schicht; Model-Instanzen verlassen die
API nicht.

**row_scope 'EIGENE' (Monteur) — die Objektsicht (Migration 0099).**
Seit dem Objektsicht-Slice hat der Monteur `property` mit Scope EIGENE. „Eigen"
heißt hier **nicht** „von mir angelegt", sondern: *eine Liegenschaft, an der ich je
einen Einsatz hatte*. Die Definition steht an genau einer Stelle
(`db_core/services/objektsicht.py`) — jeder Endpunkt hier zieht von dort.

  * **Lesen** (Liste, Detail, Beteiligte): nur meine Objekte. Ein fremdes Objekt ist
    **404**, nicht 403 (seine Existenz wird nicht verraten).
  * **Gebäude/Einheiten anlegen**: an meinen Objekten erlaubt — der Monteur nimmt vor
    Ort auf, was er vorfindet.
  * **Eine neue Liegenschaft anlegen**: **verboten** (`require`, fail-closed → 403).
    Wer Objekte erfinden kann, baut sich seine eigene Sichtbarkeit.
  * **Beteiligtenrollen pflegen**: verboten (Dispositionsdatum, `require` → 403).
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.objektgrenze import guard_objekt
from api.permissions import require, require_scoped
from db_core.models import Building, Property, PropertyPartyRole, Unit
from db_core.services import objektsicht
from db_core.services import property as property_service
from db_core.services import property_steckbrief
from db_core.services.textsuche import (
    adresse_annotationen,
    feld_q,
    norm,
    normalisieren,
    normalisieren_strasse,
    strassen_norm,
    tokenisieren,
    tokens_q,
)

router = Router()


# --- Schemas ---------------------------------------------------------------

class PropertyOut(Schema):
    id: UUID
    property_number: str
    name: str
    property_type: str
    status: str
    # Aus der verknüpften identity.address; in den Endpoints explizit gesetzt
    # (kein from_orm-Resolver, damit Liste und Detail denselben Pfad nutzen).
    city: str

    # --- Steckbrief (Dublettenvermeidung) ---------------------------------
    # Alle Felder tragen einen Default. Das ist kein Stilmittel, sondern die
    # Bedingung dafür, dass `PropertyDetailOut(PropertyOut)` unverändert
    # weiterläuft: Die Detailroute füllt den Steckbrief NICHT (sie zeigt
    # Adresse, Gebäude und Rollen ohnehin vollständig) und dürfte sonst gar
    # nicht mehr serialisieren.
    address_line: str | None = None
    eigentuemer: list[str] = []
    verwaltung: str | None = None
    telefon: str | None = None
    telefon_quelle: str | None = None
    einheiten_anzahl: int = 0
    gebaeude_adressen: list[str] = []


class PropertyListOut(Schema):
    items: list[PropertyOut]
    total: int
    page: int
    page_size: int


class AddressOut(Schema):
    street: str
    house_number: str | None = None
    address_addition: str | None = None
    postal_code: str
    city: str
    country_code: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class PartyRoleOut(Schema):
    party_id: UUID
    party_display_name: str
    role: str
    valid_from: date
    valid_until: date | None = None
    is_current: bool


class UnitOut(Schema):
    id: UUID
    unit_type: str
    unit_number: str


class BuildingOut(Schema):
    id: UUID
    building_number: str
    name: str | None = None
    units: list[UnitOut]


class PropertyDetailOut(PropertyOut):
    version: int
    address: AddressOut
    buildings: list[BuildingOut]
    party_roles: list[PartyRoleOut]


class PropertyIn(Schema):
    name: str
    property_type: str
    street: str
    postal_code: str
    city: str
    house_number: str | None = None
    address_addition: str | None = None
    country_code: str = "DE"


class PropertyFilter(Schema):
    q: str | None = None
    property_type: str | None = None
    status: str | None = None


# --- Lesende Endpoints (Dev-Phase ohne Auth, siehe Modul-Docstring) --------

@router.get("/properties", response=PropertyListOut)
def list_properties(
    request,
    filters: PropertyFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Liegenschaften auflisten: Suche **über die Adresse**, Filter, Seiten.

    **Die Suche geht über die Adresse, nicht nur über Name und Nummer.** Das war
    die Dublettenquelle Nummer eins: Ein Mieter ruft an und nennt seine Adresse;
    der Mitarbeiter tippt „Albrechtstraße 30", findet nichts (weil die
    Liegenschaft „WEG Albrechtstr." heißt) und legt sie neu an. Gesucht wird
    deshalb in `property_number`, `name` und allen Feldern der
    Liegenschaftsadresse — **plus** den Adressen der **Gebäude**: Bei einer WEG
    trägt die Liegenschaft eine Hausnummer und das gesuchte Gebäude eine andere.

    Normalisiert und tokenweise (`services/textsuche.py`): **jedes** Token muss
    irgendwo vorkommen (UND), innerhalb eines Tokens zählt jedes Feld (ODER). Nur
    so findet „Albrechtstr 30" die Liegenschaft — „albrechtstr" trifft die Straße,
    „30" die Hausnummer, kein einzelnes Feld enthält beides.

    Jede Zeile trägt ihren **Steckbrief** (Eigentümer, Verwaltung, Telefon,
    Einheitenzahl) — ohne diesen Kontext ist eine Trefferliste gleichnamiger
    Objekte keine Entscheidungshilfe. Er wird gebündelt geladen
    (`services/property_steckbrief.py`), nicht je Zeile.

    Scope 'EIGENE': nur die Objekte, an denen der Akteur je einen Einsatz hatte
    (`objektsicht.begrenzen` auf dem Primärschlüssel).
    """
    actor, scope = require_scoped(request, "property", "LESEN")
    qs = Property.objects.select_related("address")
    qs = objektsicht.begrenzen(qs, scope, actor, "id")

    if filters.q:
        qs = _adresssuche(qs, filters.q)
    if filters.property_type:
        qs = qs.filter(property_type=filters.property_type)
    if filters.status:
        qs = qs.filter(status=filters.status)

    qs = qs.order_by("property_number", "id")

    total = qs.count()
    start = (page - 1) * page_size
    seite = list(qs[start:start + page_size])
    return PropertyListOut(
        items=_property_outs(seite), total=total, page=page, page_size=page_size
    )


# Normalisierte Adressfelder der Liegenschaft (Aliasse der Annotationen).
_ADRESSFELDER = ("n_street", "n_hn", "n_plz", "n_city")
# Dieselben Felder am Gebäude (eigene Aliasse, damit die Subquery nicht mit den
# gleichnamigen Annotationen der äußeren Query kollidiert).
_GEBAEUDEFELDER = ("b_street", "b_hn", "b_plz", "b_city")


def _adresssuche(qs, begriff):
    """Tokensuche über Nummer, Name, Liegenschafts- UND Gebäudeadresse.

    **Die Straße wird zusätzlich in ihrer Suffixform verglichen** (`strassen_norm`
    gegen `normalisieren_strasse(token)`). Ohne das ist die Suche
    richtungsabhängig, und zwar im Hauptfall: Gespeichert ist „Albrechtstr.", der
    Anrufer sagt „Albrechtstraße 30", der Mitarbeiter tippt die ausgeschriebene
    Form — und `albrechtstrasse` steckt nicht in `albrechtstr`. Die Trefferliste
    bleibt leer, und er legt genau die Dublette an, die dieser Slice verhindern
    soll. Beide Formen normalisieren sich auf `albrechtstr` und treffen damit in
    **beide** Richtungen.

    Die allgemeine Normalform bleibt zusätzlich stehen: Sie trägt die Teilstring-
    suche über Hausnummer, PLZ, Ort, Name und Nummer, und sie findet „albrecht"
    als Wortanfang, was die Suffixform allein nicht leistet.

    Beides sind Annotationen und ein korreliertes EXISTS — die Zahl der Abfragen
    ändert sich dadurch nicht.
    """
    tokens = tokenisieren(begriff)
    if not tokens:
        return qs
    qs = qs.annotate(
        n_nummer=norm("property_number"),
        n_name=norm("name"),
        **adresse_annotationen("address__", "n"),
        s_street=strassen_norm("address__street"),
    )
    felder = ("n_nummer", "n_name", *_ADRESSFELDER)

    def zusatzzweige(token):
        """Straßenform der Liegenschaft + die Gebäudeadresse (beides ODER)."""
        s_token = normalisieren_strasse(token)
        sub = (
            Building.objects.filter(property_id=OuterRef("pk"))
            .annotate(
                **adresse_annotationen("address__", "b"),
                b_sstreet=strassen_norm("address__street"),
            )
            .filter(
                feld_q(_GEBAEUDEFELDER, token)
                | Q(b_sstreet__contains=s_token)
            )
        )
        # „Albrechtstr. 22" muss die WEG finden, die dieses Gebäude trägt.
        return [Q(s_street__contains=s_token), Exists(sub)]

    return qs.filter(tokens_q(felder, tokens, zusatzzweige))


def _property_outs(properties):
    """`PropertyOut` je Zeile MIT Steckbrief — gebündelt geladen, kein N+1."""
    briefe = property_steckbrief.steckbriefe([p.id for p in properties])
    leer = property_steckbrief.Steckbrief()
    ergebnis = []
    for p in properties:
        s = briefe.get(p.id, leer)
        ergebnis.append(PropertyOut(
            id=p.id,
            property_number=p.property_number,
            name=p.name,
            property_type=p.property_type,
            status=p.status,
            city=p.address.city,
            address_line=s.address_line,
            eigentuemer=s.eigentuemer,
            verwaltung=s.verwaltung,
            telefon=s.telefon,
            telefon_quelle=s.telefon_quelle,
            einheiten_anzahl=s.einheiten_anzahl,
            gebaeude_adressen=s.gebaeude_adressen,
        ))
    return ergebnis


# --- Adress-Dublettenabgleich ----------------------------------------------
# Vor der {property_id}-Detailroute registriert: Diese Reihenfolge ist die
# Absicherung dagegen, dass ein späterer Konverterwechsel den literalen Pfad
# `/properties/adress-dubletten` an die Detailroute verfüttert.

class AdressTreffer(Schema):
    art: str    # 'EXAKT' | 'GEBAEUDE' | 'STRASSE'
    grund: str  # menschenlesbar, deutsch
    property: PropertyOut


class AdressDublettenOut(Schema):
    treffer: list[AdressTreffer]


#: Reihenfolge der Trefferarten — je näher an der eingegebenen Adresse, desto weiter oben.
_ART_RANG = {"EXAKT": 0, "GEBAEUDE": 1, "STRASSE": 2}

#: Zeilen, die je Zweig aus der DB geholt werden, bevor in Python bewertet und auf
#: `limit` gekürzt wird (Muster: `services/suche.py::_FENSTER`). Eine Straße mit
#: PLZ hat in der Praxis eine Handvoll Liegenschaften; ohne Ortsangabe könnte
#: „Hauptstraße" aber jede Zeile des Hauses ziehen — und der Abgleich läuft bei
#: jedem Tastendruck im Erfassungsformular.
#:
#: Ein `EXAKT`-Treffer kann dabei **nicht** aus dem Fenster fallen: Die Abfrage
#: sortiert Zeilen mit passender Hausnummer ausdrücklich nach vorn.
_FENSTER = 200


@router.get("/properties/adress-dubletten", response=AdressDublettenOut)
def adress_dubletten(
    request,
    street: str | None = Query(None),
    house_number: str | None = Query(None),
    postal_code: str | None = Query(None),
    city: str | None = Query(None),
    limit: int = Query(10, ge=1, le=25),
):
    """Gibt es diese Adresse schon? — der Abgleich VOR dem Anlegen.

    **Warum das mehr können muss als „gleiche Adresse suchen" (der WEG-Fall):**
    Eine Wohnungseigentümergemeinschaft ist *eine* Liegenschaft, die sich über
    *mehrere Hausnummern* erstreckt. Die WEG steht als „Albrechtstraße 30" im
    System; ihre Gebäude liegen in der 22, 24, 26 und 30. Ruft der Mieter aus der
    **22** an und sucht der Mitarbeiter nach „Albrechtstraße 22", findet ein
    Gleichheitsabgleich **nichts** — und er legt eine zweite Liegenschaft an
    derselben WEG an. Genau diese Dublette ist danach nicht mehr sauber
    auflösbar: An ihr hängen Vorgänge, Aufträge und Belege.

    Deshalb antwortet dieser Endpunkt in **drei Stufen**:

    | Art | Bedeutung |
    |---|---|
    | `EXAKT` | Die Liegenschaftsadresse selbst stimmt (Hausnummer gleich oder beide leer). |
    | `GEBAEUDE` | Ein **Gebäude** dieser Liegenschaft trägt genau diese Straße + Hausnummer. |
    | `STRASSE` | Gleiche Straße, **andere** Hausnummer — der WEG-Fall. Er ist kein Rauschen, er ist der Zweck. |

    Verglichen wird **normalisiert** (`services/textsuche.py`): Kleinschreibung,
    Umlaute entfaltet, Nicht-Alphanumerisches raus — und das Straßen-Suffix
    vereinheitlicht. „Albrechtstr." und „Albrechtstraße" sind damit **gleich**,
    nicht bloß ähnlich.

    Eingrenzung auf den Ort: Ist eine PLZ angegeben, muss sie stimmen; sonst
    entscheidet der Ort, falls angegeben. Fehlt beides, zählt nur die Straße —
    dann ist die Antwort bewusst weit, denn eine Straße ohne Ort ist keine Adresse.

    Rechte wie die Liste: `property/LESEN`, Scope 'EIGENE' begrenzt auf die
    eigenen Objekte. Der Abgleich ist damit **kein Nebeneingang**: Wer eine
    Liegenschaft nicht sehen darf, erfährt hier auch nicht, dass es sie gibt.

    `street` ist **fachlich Pflicht**, im Schema aber optional: Die Prüfung steht
    bewusst **hinter** `require_scoped`, damit ein Konto ohne Recht 403 bekommt
    und nicht 422 — sonst verriete die Fehlermeldung, dass hier eine Straße
    erwartet wird, an jemanden, der den Endpunkt gar nicht aufrufen darf.

    Das gilt **nur für `street`**, nicht für die Parameter allgemein: `limit`
    trägt seine Grenzen im Schema, und ninja validiert sie, bevor die View
    überhaupt läuft — `?limit=99` antwortet deshalb mit 422, auch ohne Recht.
    Wer die Reihenfolge über alle Felder ziehen wollte, müsste sämtliche
    Validierung von Hand in die View holen; der Gewinn wäre die Information
    „es gibt hier ein limit", der Preis die halbe Schemaprüfung.
    """
    actor, scope = require_scoped(request, "property", "LESEN")

    q_street = normalisieren_strasse(street)
    if not q_street:
        raise HttpError(422, "Für den Adressabgleich wird eine Straße benötigt.")
    q_hn = normalisieren(house_number)
    q_plz = normalisieren(postal_code)
    q_city = normalisieren(city)

    basis = objektsicht.begrenzen(
        Property.objects.select_related("address"), scope, actor, "id"
    )

    def ort_eingrenzen(qs, plz_feld, ort_feld):
        """PLZ schlägt Ort; ohne beides bleibt es bei der Straße."""
        if q_plz:
            return qs.filter(**{plz_feld: q_plz})
        if q_city:
            return qs.filter(**{ort_feld: q_city})
        return qs

    # (1) Liegenschaften, deren EIGENE Adresse in dieser Straße liegt. Zeilen mit
    #     passender Hausnummer zuerst — damit der EXAKT-Treffer das Fenster nie
    #     verlässt, auch nicht in einer Straße mit hunderten Objekten.
    an_der_strasse = ort_eingrenzen(
        basis.annotate(
            s_street=strassen_norm("address__street"),
            s_hn=norm("address__house_number"),
            s_plz=norm("address__postal_code"),
            s_city=norm("address__city"),
        ).filter(s_street=q_street),
        "s_plz", "s_city",
    ).annotate(
        hn_rang=Case(When(s_hn=q_hn, then=Value(0)), default=Value(1),
                     output_field=IntegerField())
    ).order_by("hn_rang", "property_number", "id")[:_FENSTER]

    # (2) Gebäude mit genau dieser Straße + Hausnummer — auch an Liegenschaften,
    #     deren eigene Adresse in einer anderen Straße liegt.
    gebaeude_qs = ort_eingrenzen(
        Building.objects.filter(
            property_id__in=basis.values("id"), address__isnull=False
        ).annotate(
            s_street=strassen_norm("address__street"),
            s_hn=norm("address__house_number"),
            s_plz=norm("address__postal_code"),
            s_city=norm("address__city"),
        ).filter(s_street=q_street, s_hn=q_hn),
        "s_plz", "s_city",
    ).select_related("address", "property__address").order_by(
        "property__property_number", "building_number", "id"
    )[:_FENSTER]

    gebaeude_je_objekt = {}
    for b in gebaeude_qs:
        gebaeude_je_objekt.setdefault(b.property_id, b)

    # Kandidaten zusammenführen: Straßentreffer + Objekte mit passendem Gebäude.
    kandidaten = {}
    for p in an_der_strasse:
        kandidaten[p.id] = p
    for b in gebaeude_je_objekt.values():
        kandidaten.setdefault(b.property_id, b.property)

    bewertet = []
    for p in kandidaten.values():
        gebaeude = gebaeude_je_objekt.get(p.id)
        eigene_strasse = normalisieren_strasse(p.address.street) == q_street
        if eigene_strasse and normalisieren(p.address.house_number) == q_hn:
            art = "EXAKT"
            grund = (
                "Diese Adresse ist bereits erfasst "
                f"({property_steckbrief.adresszeile(p.address)})."
            )
        elif gebaeude is not None:
            art = "GEBAEUDE"
            grund = (
                f"Gebäude {gebaeude.building_number} dieser Liegenschaft liegt an "
                f"{property_steckbrief.adresszeile(gebaeude.address)}."
            )
        else:
            art = "STRASSE"
            nummer = (p.address.house_number or "").strip()
            grund = (
                f"Gleiche Straße, andere Hausnummer (Nr. {nummer})."
                if nummer else "Gleiche Straße, keine Hausnummer erfasst."
            )
        bewertet.append((art, grund, p))

    bewertet.sort(key=lambda t: (_ART_RANG[t[0]], t[2].property_number))
    bewertet = bewertet[:limit]

    treffer_objekte = [p for _, _, p in bewertet]
    outs = dict(zip(
        (p.id for p in treffer_objekte), _property_outs(treffer_objekte)
    ))
    return AdressDublettenOut(treffer=[
        AdressTreffer(art=art, grund=grund, property=outs[p.id])
        for art, grund, p in bewertet
    ])


def _property_detail(property_id):
    """Detail-Schema einer Liegenschaft inkl. Adresse, Gebäude/Einheiten und
    Party-Rollen; 404 wenn nicht vorhanden."""
    prop = (
        Property.objects.filter(id=property_id)
        .select_related("address")
        .prefetch_related("buildings__units", "party_roles__party")
        .first()
    )
    if prop is None:
        raise HttpError(404, "Liegenschaft nicht gefunden.")

    today = date.today()

    def _is_current(r):
        # daterange(valid_from, valid_until) ist [) — obere Grenze exklusiv:
        # eine Rolle mit valid_until = heute gilt heute nicht mehr.
        return r.valid_until is None or r.valid_until > today

    party_roles = [
        PartyRoleOut(
            party_id=r.party_id,
            party_display_name=r.party.display_name,
            role=r.role,
            valid_from=r.valid_from,
            valid_until=r.valid_until,
            is_current=_is_current(r),
        )
        # Aktuelle Rollen zuerst, innerhalb der Gruppe neueste zuerst.
        for r in sorted(
            prop.party_roles.all(),
            key=lambda r: (_is_current(r), r.valid_from),
            reverse=True,
        )
    ]
    buildings = [
        BuildingOut(
            id=b.id,
            building_number=b.building_number,
            name=b.name,
            units=[
                UnitOut(id=u.id, unit_type=u.unit_type, unit_number=u.unit_number)
                for u in sorted(b.units.all(), key=lambda u: u.unit_number)
            ],
        )
        for b in sorted(prop.buildings.all(), key=lambda b: b.building_number)
    ]

    return PropertyDetailOut(
        id=prop.id,
        property_number=prop.property_number,
        name=prop.name,
        property_type=prop.property_type,
        status=prop.status,
        city=prop.address.city,
        version=prop.version,
        address=AddressOut.from_orm(prop.address),
        buildings=buildings,
        party_roles=party_roles,
    )


# --- Schreibender Endpoint (Django-Session-Auth Pflicht) -------------------
# Reihenfolge hier unkritisch (POST /properties vs. GET /properties/{id}
# unterscheiden sich in Methode und Segmentzahl); der Aufbau folgt der
# Identity-API der Lesbarkeit halber: Liste, dann Write, dann Detail.

@router.post("/properties", response={201: PropertyDetailOut}, auth=django_auth)
def create_property(request, payload: PropertyIn):
    """Neue Liegenschaft anlegen (identity.address + property.property).

    `require` (fail-closed): Scope 'EIGENE' → **403**. Ein Konto mit Objektsicht darf
    keine Liegenschaft **erfinden** — es hätte sich damit selbst ein Objekt in sein
    Sichtfeld geschrieben. Anlegen darf es nur **an** seinen Objekten (Gebäude,
    Einheiten, Räume, technische Anlagen).
    """
    actor, _ = require(request, "property", "ANLEGEN")
    try:
        prop = property_service.create_property(
            actor,
            name=payload.name,
            property_type=payload.property_type,
            street=payload.street,
            postal_code=payload.postal_code,
            city=payload.city,
            house_number=payload.house_number,
            address_addition=payload.address_addition,
            country_code=payload.country_code,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _property_detail(prop.id))


@router.get("/properties/{property_id}", response=PropertyDetailOut)
def get_property(request, property_id: UUID):
    """Detail einer Liegenschaft inkl. Adresse, Gebäude/Einheiten und Rollen.

    Scope 'EIGENE': fremdes Objekt → 404 (die Existenz wird nicht verraten).
    """
    actor, scope = require_scoped(request, "property", "LESEN")
    guard_objekt(scope, actor, property_id)
    return _property_detail(property_id)


# --- Schreibende Unterstruktur-Endpoints (Session-Auth Pflicht) ------------
# row_scope 'EIGENE' (Objektsicht): Gebäude und Einheiten darf der Monteur an
# **seinen** Objekten anlegen (er nimmt vor Ort auf, was er vorfindet) — an fremden
# nicht (404). Deshalb `require_scoped` + `guard_objekt`, NICHT `require_create`:
# Die erzeugte Zeile trägt ihr Elternobjekt im Payload, und genau dafür ist
# `require_create` laut eigenem Docstring nicht gedacht.
#
# Die Party-Rollen bleiben Dispositionsdatum (`require`, fail-closed → 403).

class BuildingIn(Schema):
    building_number: str
    name: str | None = None


class UnitIn(Schema):
    unit_type: str
    unit_number: str


class PartyRoleIn(Schema):
    party_id: UUID
    role: str
    valid_from: date
    valid_until: date | None = None


def _building_out(building):
    return BuildingOut(
        id=building.id,
        building_number=building.building_number,
        name=building.name,
        units=[
            UnitOut(id=u.id, unit_type=u.unit_type, unit_number=u.unit_number)
            for u in sorted(building.units.all(), key=lambda u: u.unit_number)
        ],
    )


@router.post(
    "/properties/{property_id}/buildings",
    response={201: BuildingOut},
    auth=django_auth,
)
def add_building(request, property_id: UUID, payload: BuildingIn):
    """Gebäude an einer bestehenden Liegenschaft anlegen (Scope 'EIGENE': nur an meiner)."""
    actor, scope = require_scoped(request, "property", "ANLEGEN")
    if not Property.objects.filter(id=property_id).exists():
        raise HttpError(404, "Liegenschaft nicht gefunden.")
    guard_objekt(scope, actor, property_id)
    try:
        building = property_service.add_building(
            actor,
            property_id=property_id,
            building_number=payload.building_number,
            name=payload.name,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    building = Building.objects.prefetch_related("units").get(id=building.id)
    return Status(201, _building_out(building))


@router.post(
    "/buildings/{building_id}/units", response={201: UnitOut}, auth=django_auth
)
def add_unit(request, building_id: UUID, payload: UnitIn):
    """Einheit in einem Gebäude anlegen.

    property_id ist von der DB an das Gebäude gebunden (zusammengesetzter FK) und
    wird deshalb hier aus dem Gebäude abgeleitet, nicht aus dem Payload
    übernommen — so kann keine Einheit einer fremden Liegenschaft untergeschoben
    werden.

    Scope 'EIGENE': Das Gebäude muss an einem meiner Objekte hängen, sonst 404.
    """
    actor, scope = require_scoped(request, "property", "ANLEGEN")
    building = Building.objects.filter(id=building_id).first()
    if building is None:
        raise HttpError(404, "Gebäude nicht gefunden.")
    guard_objekt(scope, actor, building.property_id, "Gebäude nicht gefunden.")
    try:
        unit = property_service.add_unit(
            actor,
            building_id=building_id,
            property_id=building.property_id,
            unit_type=payload.unit_type,
            unit_number=payload.unit_number,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(
        201, UnitOut(id=unit.id, unit_type=unit.unit_type, unit_number=unit.unit_number)
    )


@router.post(
    "/properties/{property_id}/parties",
    response={201: PartyRoleOut},
    auth=django_auth,
)
def add_party_role(request, property_id: UUID, payload: PartyRoleIn):
    """Einer Liegenschaft eine Party-Rolle mit Gültigkeit zuordnen.

    Torfunktion `require` (AENDERN), **fail-closed → 403 bei Scope 'EIGENE'**: Wer
    ist Eigentümer, Betreiber, Hausmeister — das ist Stammdatenpflege der Verwaltung,
    kein Baustellenbefund. Der Monteur **liest** die Beteiligten seines Objekts (er
    muss den Mieter anrufen können); er schreibt sie nicht.
    """
    actor, _ = require(request, "property", "AENDERN")
    if not Property.objects.filter(id=property_id).exists():
        raise HttpError(404, "Liegenschaft nicht gefunden.")
    try:
        role = property_service.add_party_role(
            actor,
            property_id=property_id,
            party_id=payload.party_id,
            role=payload.role,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    role = PropertyPartyRole.objects.select_related("party").get(id=role.id)
    today = date.today()
    is_current = role.valid_until is None or role.valid_until > today
    return Status(
        201,
        PartyRoleOut(
            party_id=role.party_id,
            party_display_name=role.party.display_name,
            role=role.role,
            valid_from=role.valid_from,
            valid_until=role.valid_until,
            is_current=is_current,
        ),
    )
