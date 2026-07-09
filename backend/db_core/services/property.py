"""Property-Service: Liegenschaften, Gebäude, Einheiten, Party-Rollen anlegen.

Wie der Identity-Service laufen alle Writes ausschließlich über
db_core.db_context.business_transaction, damit die DB-Trigger den
Benutzerkontext (app.current_user_id) sehen (Audit) und die Schutz-/
Konsistenztrigger greifen. Transaktionen bleiben kurz und fachlich abgeschlossen.

Codelisten werden gegen die in der Migration 0004 beschlossenen Werte geprüft,
damit Fehleingaben schon vor dem DB-CHECK eine klare Meldung bekommen.
"""
import uuid

from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.models import Address, Building, Property, PropertyPartyRole, Unit
from db_core.services._validation import ensure_exists, ensure_party_usable

# Beschlossene Codelisten (Migration 0004_property.sql).
PROPERTY_TYPES = ("WEG", "RENTAL_PROPERTY", "COMMERCIAL", "MIXED", "OTHER")
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


def add_building(
    actor_app_user_id,
    *,
    property_id,
    building_number,
    name=None,
    address_id=None,
):
    """Legt ein property.building an einer bestehenden Liegenschaft an."""
    if not building_number or not building_number.strip():
        raise ValueError("building_number darf nicht leer sein.")
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_exists(Address, address_id, "Adresse")
    with business_transaction(actor_app_user_id):
        building = Building.objects.create(
            id=uuid.uuid4(),
            property_id=property_id,
            building_number=building_number.strip(),
            name=name,
            address_id=address_id,
        )
    return building


def add_unit(
    actor_app_user_id,
    *,
    building_id,
    property_id,
    unit_type,
    unit_number,
):
    """Legt eine property.unit in einem Gebäude an.

    property_id muss zum Gebäude passen (DB-seitig über den zusammengesetzten
    FK erzwungen); die Codeliste unit_type wird vorab geprüft.
    """
    if unit_type not in UNIT_TYPES:
        raise ValueError(
            f"Ungültiger unit_type '{unit_type}'. "
            f"Erlaubt: {', '.join(UNIT_TYPES)}."
        )
    if not unit_number or not unit_number.strip():
        raise ValueError("unit_number darf nicht leer sein.")
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
            unit_number=unit_number.strip(),
        )
    return unit


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
    with business_transaction(actor_app_user_id):
        entry = PropertyPartyRole.objects.create(
            id=uuid.uuid4(),
            property_id=property_id,
            party_id=party_id,
            role=role,
            valid_from=valid_from,
            valid_until=valid_until,
        )
    return entry
