"""Property-Service: Liegenschaften, Gebäude, Einheiten, Party-Rollen anlegen.

Wie der Identity-Service laufen alle Writes ausschließlich über
db_core.db_context.business_transaction, damit die DB-Trigger den
Benutzerkontext (app.current_user_id) sehen (Audit) und die Schutz-/
Konsistenztrigger greifen. Transaktionen bleiben kurz und fachlich abgeschlossen.

Codelisten werden gegen die in der Migration 0004 beschlossenen Werte geprüft,
damit Fehleingaben schon vor dem DB-CHECK eine klare Meldung bekommen.
"""
import re
import uuid

from django.db import IntegrityError, ProgrammingError
from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.models import Address, Building, Property, PropertyPartyRole, Unit
from db_core.services._validation import ensure_exists, ensure_party_usable

# Beschlossene Codelisten (Migration 0004_property.sql; EINFAMILIENHAUS aus 0048).
PROPERTY_TYPES = (
    "WEG",
    "RENTAL_PROPERTY",
    "COMMERCIAL",
    "MIXED",
    "OTHER",
    "EINFAMILIENHAUS",
)
UNIT_TYPES = (
    "APARTMENT",
    "COMMERCIAL",
    "GARAGE",
    "PARKING",
    "STORAGE",
    "COMMON_AREA",
    "TECHNICAL_ROOM",
    "OTHER",
)
PARTY_ROLES = ("COMMUNITY_OF_OWNERS", "PROPERTY_OWNER", "OPERATOR", "CARETAKER")


def create_property(
    actor_app_user_id,
    *,
    name,
    property_type,
    street,
    postal_code,
    city,
    house_number=None,
    address_addition=None,
    country_code="DE",
    latitude=None,
    longitude=None,
):
    """Legt identity.address + property.property in einer Transaktion an.

    Die Liegenschaftsnummer vergibt die DB-Sequenz (property_number bleibt
    ungesetzt → der Spalten-Default greift). Gibt die neue Property zurück,
    frisch aus der DB geladen, damit die vergebene Nummer gefüllt ist.
    """
    if property_type not in PROPERTY_TYPES:
        raise ValueError(
            f"Ungültiger property_type '{property_type}'. "
            f"Erlaubt: {', '.join(PROPERTY_TYPES)}."
        )
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    # Die DB erzwingt btrim(...) <> '' auf diesen Adressfeldern; vorab prüfen,
    # damit Leereingaben als klarer 422 statt als DB-IntegrityError (500) enden.
    for feldname, wert in (
        ("street", street), ("postal_code", postal_code), ("city", city),
    ):
        if not wert or not wert.strip():
            raise ValueError(f"{feldname} darf nicht leer sein.")
    # Dieselbe Adresstabelle, derselbe CHECK (country_code ~ '^[A-Z]{2}$',
    # 0003) — und bis hierher dieselbe Lücke wie in `identity.add_address`:
    # Ein Kürzel wie „xx" endete als roher IntegrityError, also 500.
    if not re.fullmatch(r"[A-Z]{2}", str(country_code or "")):
        raise ValueError(
            f"Ungültiges Länderkürzel '{country_code}'. "
            "Erwartet werden zwei Großbuchstaben (z. B. DE)."
        )

    with business_transaction(actor_app_user_id):
        address = Address.objects.create(
            id=uuid.uuid4(),
            street=street,
            house_number=house_number,
            address_addition=address_addition,
            postal_code=postal_code,
            city=city,
            country_code=country_code,
            latitude=latitude,
            longitude=longitude,
        )
        prop = Property.objects.create(
            id=uuid.uuid4(),
            name=name.strip(),
            address=address,
            property_type=property_type,
            status="ACTIVE",
            version=1,
        )
        # property_number wird von der DB-Sequenz gesetzt; frisch nachladen.
        prop.refresh_from_db()
    return prop


def update_property(actor_app_user_id, property_id, daten, *, adresse=None):
    """Teil-Update (PATCH) einer Liegenschaft — Name, Typ, Anschrift.

    Behebt Befund H5: `api/property.py` hatte kein `PATCH /properties/{id}`.
    Ein Tippfehler im Objektnamen oder eine falsch erfasste Anschrift waren
    endgültig — und die Anschrift ist das, wonach der Monteur fährt.

    **Die Adresse wird ersetzt, nicht geändert** (Befund H1): `identity.address`
    ist append-only (`trg_address_immutable`, 0003). `adresse` ist deshalb ein
    Satz Felder, aus dem serverseitig eine NEUE Zeile entsteht, auf die
    `property.address_id` danach zeigt. Bewusst **keine** client-gelieferte
    `address_id`: Die nähme eine fremde Anschrift an — derselbe Fehler, der in
    AP1 an `building.address_id` aufgefallen ist.

    Die alte Adresszeile bleibt stehen: Gebäude, Kontakte oder Belege können
    auf sie zeigen.
    """
    prop = Property.objects.filter(pk=property_id).first()
    if prop is None:
        raise ValueError(f"Liegenschaft {property_id} existiert nicht")

    daten = daten or {}
    werte = {}
    if "name" in daten:
        wert = daten["name"]
        if not wert or not str(wert).strip():
            raise ValueError("Der Name ist ein Pflichtfeld und darf nicht leer sein.")
        werte["name"] = str(wert).strip()
    if "property_type" in daten:
        if daten["property_type"] not in PROPERTY_TYPES:
            raise ValueError(
                f"Ungültiger property_type '{daten['property_type']}'. "
                f"Erlaubt: {', '.join(PROPERTY_TYPES)}."
            )
        werte["property_type"] = daten["property_type"]

    if not werte and not adresse:
        return prop

    if adresse:
        for feldname, wert in (
            ("Die Straße", adresse.get("street")),
            ("Die PLZ", adresse.get("postal_code")),
            ("Der Ort", adresse.get("city")),
        ):
            if not wert or not str(wert).strip():
                raise ValueError(f"{feldname} darf nicht leer sein.")
        land = adresse.get("country_code") or "DE"
        if not re.fullmatch(r"[A-Z]{2}", str(land)):
            raise ValueError(
                f"Ungültiges Länderkürzel '{land}'. "
                "Erwartet werden zwei Großbuchstaben (z. B. DE)."
            )

    with business_transaction(actor_app_user_id):
        if adresse:
            neu = Address.objects.create(
                id=uuid.uuid4(),
                street=adresse["street"].strip(),
                house_number=adresse.get("house_number"),
                address_addition=adresse.get("address_addition"),
                postal_code=adresse["postal_code"].strip(),
                city=adresse["city"].strip(),
                country_code=adresse.get("country_code") or "DE",
            )
            werte["address_id"] = neu.id
        Property.objects.filter(pk=property_id).update(**werte)
    return Property.objects.select_related("address").get(pk=property_id)


def add_building(
    actor_app_user_id,
    *,
    property_id,
    building_number=None,
    name=None,
    address_id=None,
):
    """Legt ein property.building an einer bestehenden Liegenschaft an.

    `building_number` darf leer bleiben: dann zählt `trg_building_number` den
    Bestand DIESER Liegenschaft hoch (Migration 0149). Eine eingetragene Nummer
    — „Hinterhaus", „A" — bleibt unangetastet.
    """
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_exists(Address, address_id, "Adresse")
    try:
        with business_transaction(actor_app_user_id):
            building = Building.objects.create(
                id=uuid.uuid4(),
                property_id=property_id,
                building_number=(building_number or "").strip(),
                name=name,
                address_id=address_id,
            )
    except IntegrityError as exc:
        if "building_property_id_building_number_key" in str(exc):
            raise ValueError(
                f"An dieser Liegenschaft existiert bereits ein Gebäude mit der "
                f"Nummer '{(building_number or '').strip()}'."
            ) from exc
        raise
    # Die Nummer kann aus dem Trigger stammen — dann steht im Objekt noch ''.
    building.refresh_from_db(fields=["building_number"])
    return building


def add_unit(
    actor_app_user_id,
    *,
    building_id,
    property_id,
    unit_type,
    unit_number=None,
    storey=None,
):
    """Legt eine property.unit in einem Gebäude an.

    property_id muss zum Gebäude passen (DB-seitig über den zusammengesetzten
    FK erzwungen); die Codeliste unit_type wird vorab geprüft.

    `unit_number` darf leer bleiben: dann zählt `trg_unit_number` den Bestand
    der Liegenschaft hoch (Migration 0149). Gezählt wird je Liegenschaft, nicht
    je Gebäude — so verlangt es A-09 (`UNIQUE (property_id, unit_number)`).
    """
    if unit_type not in UNIT_TYPES:
        raise ValueError(
            f"Ungültiger unit_type '{unit_type}'. "
            f"Erlaubt: {', '.join(UNIT_TYPES)}."
        )
    ensure_exists(Property, property_id, "Liegenschaft")
    # Der zusammengesetzte FK (building_id, property_id) → building verlangt, dass
    # das Gebäude zur angegebenen Liegenschaft gehört; sonst IntegrityError (500).
    building_property_id = (
        Building.objects.filter(pk=building_id)
        .values_list("property_id", flat=True)
        .first()
    )
    if building_property_id is None:
        raise ValueError(f"Gebäude {building_id} existiert nicht")
    if building_property_id != property_id:
        raise ValueError("Das Gebäude gehört nicht zur angegebenen Liegenschaft")
    with business_transaction(actor_app_user_id):
        unit = Unit.objects.create(
            id=uuid.uuid4(),
            building_id=building_id,
            property_id=property_id,
            unit_type=unit_type,
            unit_number=(unit_number or "").strip(),
            # Leerstring waere ein CHECK-Verstoss (unit_storey_nicht_leer) —
            # „nicht erfasst" ist NULL. Gleiche Normalisierung wie im PATCH.
            storey=_text_oder_none(storey),
        )
    # Die Nummer kann aus dem Trigger stammen — dann steht im Objekt noch ''.
    unit.refresh_from_db(fields=["unit_number"])
    return unit


# ---------------------------------------------------------------------------
# Korrigieren (AP1 / Befunde I1, I7, I12)
#
# Bis Migration 0124 gab es auf building und unit ausser INSERT keinen einzigen
# Schreibpfad: Ein ohne Bezeichnung angelegtes Gebaeude blieb dauerhaft
# „Gebaeude 1", eine vertippte Einheitsnummer war nicht mehr zu retten. Die
# Tore dahinter setzt weiterhin die DB (zusammengesetzte FKs, UNIQUE,
# trg_unit_type_conflicts) — hier steht nur die Uebersetzung in Fachfehler,
# damit aus einem Tippfehler ein 422 mit Klartext wird und kein 500.
# ---------------------------------------------------------------------------


def _text_oder_none(wert):
    """Leerstring wie „nicht gesetzt" behandeln.

    `building.name` und `unit.storey` sind NULL-faehig. Ein Leerstring waere
    zwar speicherbar (kein CHECK auf `name`), erzeugte aber einen Datensatz,
    der befuellt AUSSIEHT und leer IST — und bei `storey` verletzte er den
    CHECK. Beides wird deshalb auf NULL normalisiert: ausdrueckliches Leeren
    ist erlaubt, ein leerer Wert wird es nie.
    """
    if wert is None:
        return None
    text = str(wert).strip()
    return text or None


def _pflichttext(daten, feld, label):
    """Pflichtfeld aus einem PATCH lesen. NULL/leer ist Loeschen, nicht Setzen.

    `label` ist die Bezeichnung, die der NUTZER liest — deshalb deutsch und
    nicht der Spaltenname. „building_number ist ein Pflichtfeld" ist keine
    Meldung, sondern ein Leck aus dem Schema.
    """
    wert = daten[feld]
    if wert is None or not str(wert).strip():
        raise ValueError(f"{label} ist ein Pflichtfeld und darf nicht leer sein.")
    return str(wert).strip()


def update_building(actor_app_user_id, building_id, daten):
    """Teil-Update (PATCH) eines Gebaeudes. Nur uebergebene Felder werden gesetzt.

    Behebt I7: Ein Gebaeude ohne Bezeichnung war bisher nie wieder benennbar.
    `name` ist NULL-faehig — ein ausdrueckliches null (oder ein Leerstring)
    loescht die Bezeichnung, die Liste faellt dann auf „Gebaeude <Nummer>"
    zurueck. `building_number` ist NOT NULL und laesst sich nur ersetzen,
    nicht leeren.
    """
    building = Building.objects.filter(pk=building_id).first()
    if building is None:
        raise ValueError(f"Gebäude {building_id} existiert nicht")

    daten = daten or {}
    werte = {}
    if "building_number" in daten:
        werte["building_number"] = _pflichttext(daten, "building_number", "Die Gebäudenummer")
    if "name" in daten:
        werte["name"] = _text_oder_none(daten["name"])
    # `address_id` wird hier NICHT verarbeitet, auch wenn die Spalte es
    # zuliesse: Eine Adresse gehoert zu einer Liegenschaft, und ohne diese
    # Pruefung liesse sich einem Gebaeude die Anschrift einer fremden
    # Liegenschaft geben (die `api/planung.py` dann als Einsatzort ausgibt).
    # Siehe die Begruendung an `BuildingPatch` in `api/property.py`.

    if not werte:
        return building

    try:
        with business_transaction(actor_app_user_id):
            Building.objects.filter(pk=building_id).update(**werte)
    except IntegrityError as exc:
        if "building_property_id_building_number_key" in str(exc):
            raise ValueError(
                f"An dieser Liegenschaft existiert bereits ein Gebäude mit der "
                f"Nummer '{werte['building_number']}'."
            ) from exc
        raise
    return Building.objects.get(pk=building_id)


def update_unit(actor_app_user_id, unit_id, daten):
    """Teil-Update (PATCH) einer Einheit. Nur uebergebene Felder werden gesetzt.

    `storey` (Migration 0124) ist NULL-faehig — ausdrueckliches Leeren ist
    erlaubt und heisst „nicht erfasst". `unit_number` und `unit_type` sind NOT
    NULL und lassen sich nur ersetzen.

    Das Gebaeude wird bewusst NICHT umgehaengt: Eine Einheit in ein anderes
    Gebaeude zu verschieben zoege Raeume, Belegungen und Eigentumsstaende mit
    und ist keine Korrektur, sondern ein Umzug — dafuer braucht es eine eigene
    fachliche Entscheidung, nicht ein Feld im Bearbeiten-Formular.
    """
    unit = Unit.objects.filter(pk=unit_id).first()
    if unit is None:
        raise ValueError(f"Einheit {unit_id} existiert nicht")

    daten = daten or {}
    werte = {}
    if "unit_type" in daten:
        unit_type = daten["unit_type"]
        if unit_type not in UNIT_TYPES:
            raise ValueError(
                f"Ungültiger unit_type '{unit_type}'. "
                f"Erlaubt: {', '.join(UNIT_TYPES)}."
            )
        werte["unit_type"] = unit_type
    if "unit_number" in daten:
        werte["unit_number"] = _pflichttext(daten, "unit_number", "Die Einheitsnummer")
    if "storey" in daten:
        werte["storey"] = _text_oder_none(daten["storey"])

    if not werte:
        return unit

    try:
        with business_transaction(actor_app_user_id):
            Unit.objects.filter(pk=unit_id).update(**werte)
    except IntegrityError as exc:
        if "unit_property_id_unit_number_key" in str(exc):
            raise ValueError(
                f"An dieser Liegenschaft existiert bereits eine Einheit mit der "
                f"Nummer '{werte['unit_number']}'."
            ) from exc
        raise
    except ProgrammingError as exc:
        # trg_unit_type_conflicts (0009): Der Typwechsel nach COMMON_AREA/
        # TECHNICAL_ROOM ist gesperrt, solange Eigentumsstaende (A-08) oder
        # Belegungen (F-12) an der Einheit haengen.
        #
        # ProgrammingError, NICHT InternalError: plpgsql `RAISE EXCEPTION` ohne
        # eigenen SQLSTATE liefert P0001, psycopg macht daraus RaiseException,
        # und Django bildet das auf ProgrammingError ab. Ein Test hat das
        # gefunden — mit InternalError griff der Handler nie und der Typwechsel
        # endete als 500 statt als Meldung.
        #
        # Deshalb wird hier auf die Meldung geprueft und alles andere weiter-
        # gereicht: ProgrammingError umfasst auch echte SQL-Fehler, und die
        # duerfen nicht als Fachfehler getarnt werden.
        #
        # Beide Zweige sind AUSDRUECKLICH — kein stiller Fallback: Wuerde der
        # zweite Zweig alles auffangen, was „Typwechsel" enthaelt, erschiene
        # nach einer Umformulierung des Triggers klaglos die falsche
        # Begruendung. Ein durchgereichter 500 ist ehrlicher als eine Meldung,
        # die den falschen Grund nennt.
        #
        # Anker sind die Beschluss-Kennungen A-08 und F-12: Sie stehen in den
        # Trigger-Meldungen (0009:21-28), sind reines ASCII (keine Umlautfrage)
        # und identifizieren die REGEL, statt sie zu beschreiben — sie
        # ueberleben also auch eine Umformulierung des Meldungstextes.
        text = str(exc)
        if "A-08" in text:
            raise ValueError(
                "Diese Einheit hat Eigentumsstände und kann deshalb nicht zur "
                "Gemeinschaftsfläche oder zum Technikraum werden (Beschluss A-08)."
            ) from exc
        if "F-12" in text:
            raise ValueError(
                "Diese Einheit hat Belegungen und kann deshalb nicht zur "
                "Gemeinschaftsfläche oder zum Technikraum werden (Beschluss F-12)."
            ) from exc
        raise
    return Unit.objects.get(pk=unit_id)


def add_party_role(
    actor_app_user_id,
    *,
    property_id,
    party_id,
    role,
    valid_from,
    valid_until=None,
):
    """Ordnet einer Liegenschaft eine Party-Rolle mit Gültigkeit zu.

    role wird gegen die Codeliste geprüft. Die DB verbietet Referenzen auf
    MERGED-Parties und zeitlich überlappende Doppelrollen (Exclusion-Constraint)
    — solche Fälle schlagen als IntegrityError durch.
    """
    if role not in PARTY_ROLES:
        raise ValueError(
            f"Ungültige role '{role}'. Erlaubt: {', '.join(PARTY_ROLES)}."
        )
    # Ende vor (oder gleich) Beginn verletzt sonst property_party_role_check
    # (IntegrityError → 500); vorab als klaren 422 abweisen.
    if valid_until is not None and valid_until <= valid_from:
        raise ValueError(
            "Das Gültig-bis-Datum der Rolle muss nach dem Gültig-ab-Datum liegen."
        )
    ensure_exists(Property, property_id, "Liegenschaft")
    # party_id muss existieren und darf nicht MERGED sein (trg_property_role_no_merged).
    ensure_party_usable(party_id, "Partei")
    # Zeitlich überlappende Doppelrolle verletzt sonst excl_property_party_role_dup
    # (IntegrityError → 500). Overlap zweier daterange('[)') = ef < nu UND nf < eu.
    overlap = PropertyPartyRole.objects.filter(
        property_id=property_id, party_id=party_id, role=role
    ).filter(Q(valid_until__isnull=True) | Q(valid_until__gt=valid_from))
    if valid_until is not None:
        overlap = overlap.filter(valid_from__lt=valid_until)
    if overlap.exists():
        raise ValueError(
            "Für diese Partei besteht in diesem Zeitraum bereits dieselbe Rolle "
            "an der Liegenschaft"
        )
    try:
        with business_transaction(actor_app_user_id):
            entry = PropertyPartyRole.objects.create(
                id=uuid.uuid4(),
                property_id=property_id,
                party_id=party_id,
                role=role,
                valid_from=valid_from,
                valid_until=valid_until,
            )
    except IntegrityError as exc:
        # Fällt nur bei einem nebenläufigen Insert an, das die Vorabprüfung
        # oben nicht sehen konnte.
        if "excl_property_party_role_dup" in str(exc):
            raise ValueError(
                "Für diese Partei besteht in diesem Zeitraum bereits dieselbe "
                "Rolle an der Liegenschaft"
            ) from exc
        raise
    return entry
