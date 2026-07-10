"""Identity-Service: Parties (Personen/Organisationen) anlegen und die
Kontaktmappe verdrahten — Ansprechpartner (party_relationship), Adressen
(address/party_address) und Kommunikationswege (contact_point).

Alle Writes laufen ausschließlich über db_core.db_context.business_transaction,
damit die DB-Trigger app.current_user_id sehen (Audit) und die
Typkonsistenz-Trigger greifen. Transaktionen bleiben kurz: Party + Subtyp in
einer Transaktion, keine weitere Arbeit darin.

Fremdschlüssel und Codelisten werden VOR dem Schreiben geprüft (→ ValueError,
den die API in 422 übersetzt). Die DB-Constraints (Exclusion über den
Gültigkeitszeitraum, CHECK, no-merged-Trigger) bleiben die letzte Instanz:
ihre Verletzung wird hier eingefangen und ebenfalls als ValueError gemeldet,
niemals als 500. DSGVO: es werden keine personenbezogenen Werte (Adressen,
Kontaktwege) in Fehlermeldungen aufgenommen.
"""
import datetime
import uuid

from django.db import IntegrityError, transaction

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Address,
    ContactPoint,
    Organization,
    Party,
    PartyAddress,
    PartyRelationship,
    Person,
)
from db_core.services._validation import ensure_party_usable

# Beschlossene Codelisten (Migration 0003).
ADDRESS_TYPES = ("BUSINESS", "POSTAL", "BILLING", "PRIVATE")
CONTACT_POINT_TYPES = ("EMAIL", "PHONE", "MOBILE", "FAX", "PORTAL")

# Beschlossene Codeliste der Organisationstypen (A-02, Migration 0002).
ORGANIZATION_TYPES = (
    "PROPERTY_MANAGEMENT",
    "WEG",
    "COMPANY",
    "AUTHORITY",
    "INSURER",
    "OTHER",
)


def _person_display_name(first_name, last_name):
    return f"{first_name.strip()} {last_name.strip()}".strip()


def create_person(
    actor_app_user_id,
    first_name,
    last_name,
    salutation=None,
    title=None,
    birth_date=None,
):
    """Legt identity.party (PERSON) + identity.person in einer Transaktion an.

    Gibt die angelegte Party zurück. Der Anzeigename entsteht aus Vor- und
    Nachname; die Subtyp-Trigger stellen die Typkonsistenz sicher.
    """
    display_name = _person_display_name(first_name, last_name)
    with business_transaction(actor_app_user_id):
        # PK explizit: die Models tragen keinen Model-Default, ein von Django
        # eingesetztes NULL würde den DB-Default gen_random_uuid() aushebeln.
        party = Party.objects.create(
            id=uuid.uuid4(),
            party_type="PERSON",
            display_name=display_name,
            status="ACTIVE",
            version=1,
        )
        Person.objects.create(
            party=party,
            first_name=first_name,
            last_name=last_name,
            salutation=salutation,
            title=title,
            birth_date=birth_date,
        )
    return party


def create_organization(
    actor_app_user_id,
    legal_name,
    organization_type,
    display_name=None,
    legal_form=None,
    registration_number=None,
    tax_number=None,
    vat_id=None,
):
    """Legt identity.party (ORGANIZATION) + identity.organization an.

    organization_type wird gegen die beschlossene Codeliste geprüft; der
    Anzeigename fällt auf den Rechtsnamen zurück, wenn keiner angegeben ist.
    """
    if organization_type not in ORGANIZATION_TYPES:
        raise ValueError(
            f"Ungültiger organization_type '{organization_type}'. "
            f"Erlaubt: {', '.join(ORGANIZATION_TYPES)}."
        )
    name = (display_name or legal_name).strip()
    with business_transaction(actor_app_user_id):
        party = Party.objects.create(
            id=uuid.uuid4(),
            party_type="ORGANIZATION",
            display_name=name,
            status="ACTIVE",
            version=1,
        )
        Organization.objects.create(
            party=party,
            organization_type=organization_type,
            legal_name=legal_name,
            legal_form=legal_form,
            registration_number=registration_number,
            tax_number=tax_number,
            vat_id=vat_id,
        )
    return party


# ---------------------------------------------------------------------------
# Ansprechpartner — party_relationship (CONTACT_PERSON_FOR)
# Konvention: from_party (Person) ist Ansprechpartner FÜR to_party (Organisation).
# ---------------------------------------------------------------------------

def list_contact_persons(organization_party_id, *, include_ended=False):
    """Ansprechpartner einer Organisation (aktive Beziehungen zuerst).

    Standardmäßig nur laufende Zuordnungen (valid_until IS NULL); mit
    include_ended auch beendete (für eine spätere Historie).
    """
    qs = (
        PartyRelationship.objects.filter(
            to_party_id=organization_party_id,
            relationship_type="CONTACT_PERSON_FOR",
        )
        .select_related("from_party", "from_party__person")
    )
    if not include_ended:
        qs = qs.filter(valid_until__isnull=True)
    return list(qs.order_by("-valid_until", "from_party__display_name", "id"))


def add_contact_person(
    actor_app_user_id,
    organization_party_id,
    *,
    person_party_id=None,
    new_person=None,
    valid_from=None,
):
    """Ordnet einer Organisation eine Person als Ansprechpartner zu.

    Entweder person_party_id (vorhandene Person) ODER new_person (dict mit
    first_name/last_name/…) — genau eines. Eine neue Person wird im selben
    Vorgang angelegt. Gibt die angelegte party_relationship zurück.
    """
    if (person_party_id is None) == (new_person is None):
        raise ValueError(
            "Genau eine Quelle angeben: bestehende Person ODER neue Person."
        )

    # Ziel muss eine (nicht zusammengeführte) Organisation sein.
    org = (
        Party.objects.filter(pk=organization_party_id)
        .values_list("party_type", "status")
        .first()
    )
    if org is None:
        raise ValueError(f"Kontakt {organization_party_id} existiert nicht")
    if org[1] == "MERGED":
        raise ValueError(
            f"Kontakt {organization_party_id} ist zusammengeführt; bitte die "
            "kanonische Partei verwenden"
        )
    if org[0] != "ORGANIZATION":
        raise ValueError(
            "Ansprechpartner können nur einer Organisation zugeordnet werden."
        )

    valid_from = valid_from or datetime.date.today()

    if person_party_id is not None:
        ensure_party_usable(person_party_id, label="Person")
        ptype = (
            Party.objects.filter(pk=person_party_id)
            .values_list("party_type", flat=True)
            .first()
        )
        if ptype != "PERSON":
            raise ValueError("Als Ansprechpartner ist nur eine Person zulässig.")
        if person_party_id == organization_party_id:
            raise ValueError("Eine Partei kann nicht ihr eigener Ansprechpartner sein.")

    try:
        with as_business_error(), business_transaction(actor_app_user_id):
            if person_party_id is None:
                person_party = create_person(actor_app_user_id, **new_person)
                # create_person öffnet eine eigene, bereits geschlossene
                # Transaktion; hier läuft weiter der äußere Kontext.
                target_person_id = person_party.id
            else:
                target_person_id = person_party_id

            relationship = PartyRelationship.objects.create(
                id=uuid.uuid4(),
                from_party_id=target_person_id,
                to_party_id=organization_party_id,
                relationship_type="CONTACT_PERSON_FOR",
                valid_from=valid_from,
            )
    except IntegrityError as exc:
        if "excl_party_relationship_dup" in str(exc):
            raise ValueError(
                "Diese Person ist im angegebenen Zeitraum bereits als "
                "Ansprechpartner zugeordnet."
            ) from exc
        raise
    return relationship


def remove_contact_person(actor_app_user_id, relationship_id):
    """Beendet eine Ansprechpartner-Zuordnung (valid_until = heute).

    Kein Löschen (trg_party_relationship_no_delete); die Beziehung wird zeitlich
    beendet und der Vorgang auditiert. Wurde sie am selben Tag angelegt, endet
    sie am Folgetag (der CHECK verlangt valid_until > valid_from).
    """
    rel = PartyRelationship.objects.filter(
        pk=relationship_id, relationship_type="CONTACT_PERSON_FOR"
    ).first()
    if rel is None:
        raise ValueError("Ansprechpartner-Zuordnung nicht gefunden.")
    if rel.valid_until is not None:
        raise ValueError("Diese Zuordnung ist bereits beendet.")
    ende = datetime.date.today()
    if ende <= rel.valid_from:
        ende = rel.valid_from + datetime.timedelta(days=1)
    with business_transaction(actor_app_user_id):
        PartyRelationship.objects.filter(pk=rel.id).update(valid_until=ende)
    rel.valid_until = ende
    return rel


# ---------------------------------------------------------------------------
# Adressen — address + party_address
# ---------------------------------------------------------------------------

def list_addresses(party_id, *, include_ended=False):
    """Adresszuordnungen eines Kontakts inkl. der Adressdaten."""
    qs = PartyAddress.objects.filter(party_id=party_id).select_related("address")
    if not include_ended:
        qs = qs.filter(valid_until__isnull=True)
    return list(qs.order_by("-is_primary", "address_type", "-valid_from", "id"))


def add_address(
    actor_app_user_id,
    party_id,
    *,
    address_type,
    street,
    postal_code,
    city,
    house_number=None,
    address_addition=None,
    country_code="DE",
    is_primary=True,
    valid_from=None,
):
    """Legt eine Adresse an und ordnet sie dem Kontakt mit Typ zu.

    Die zeitliche Exklusivität je (Party, Typ) für primäre Adressen erzwingt der
    DB-Constraint excl_party_address_primary; seine Verletzung wird als
    ValueError (→ 422) gemeldet. Gibt die angelegte party_address zurück.
    """
    if address_type not in ADDRESS_TYPES:
        raise ValueError(
            f"Ungültiger Adresstyp '{address_type}'. "
            f"Erlaubt: {', '.join(ADDRESS_TYPES)}."
        )
    ensure_party_usable(party_id, label="Kontakt")
    valid_from = valid_from or datetime.date.today()

    try:
        with business_transaction(actor_app_user_id):
            address = Address.objects.create(
                id=uuid.uuid4(),
                street=street,
                house_number=house_number,
                address_addition=address_addition,
                postal_code=postal_code,
                city=city,
                country_code=country_code,
            )
            link = PartyAddress.objects.create(
                id=uuid.uuid4(),
                party_id=party_id,
                address_id=address.id,
                address_type=address_type,
                is_primary=is_primary,
                valid_from=valid_from,
            )
    except IntegrityError as exc:
        if "excl_party_address_primary" in str(exc):
            raise ValueError(
                "Für diesen Kontakt existiert im angegebenen Zeitraum bereits "
                "eine primäre Adresse dieses Typs."
            ) from exc
        raise
    return link


# ---------------------------------------------------------------------------
# Kommunikationswege — contact_point
# ---------------------------------------------------------------------------

def list_contact_points(party_id, *, include_ended=False):
    """Kommunikationswege eines Kontakts (primäre zuerst)."""
    qs = ContactPoint.objects.filter(party_id=party_id)
    if not include_ended:
        qs = qs.filter(valid_until__isnull=True)
    return list(qs.order_by("-is_primary", "contact_type", "-valid_from", "id"))


def add_contact_point(
    actor_app_user_id,
    party_id,
    *,
    contact_type,
    value,
    label=None,
    is_primary=False,
    valid_from=None,
):
    """Legt einen Kommunikationsweg (Tel/Mobil/E-Mail/Fax/Portal) an.

    Die Exklusivität je (Party, Typ) für primäre Wege erzwingt
    excl_contact_point_primary; Verletzung → ValueError (422).
    """
    if contact_type not in CONTACT_POINT_TYPES:
        raise ValueError(
            f"Ungültiger Kontakttyp '{contact_type}'. "
            f"Erlaubt: {', '.join(CONTACT_POINT_TYPES)}."
        )
    if not value or not value.strip():
        raise ValueError("Der Wert des Kommunikationswegs darf nicht leer sein.")
    ensure_party_usable(party_id, label="Kontakt")
    valid_from = valid_from or datetime.date.today()

    try:
        with business_transaction(actor_app_user_id):
            point = ContactPoint.objects.create(
                id=uuid.uuid4(),
                party_id=party_id,
                contact_type=contact_type,
                value=value.strip(),
                label=(label.strip() if label and label.strip() else None),
                is_primary=is_primary,
                valid_from=valid_from,
            )
    except IntegrityError as exc:
        if "excl_contact_point_primary" in str(exc):
            raise ValueError(
                "Für diesen Kontakt existiert im angegebenen Zeitraum bereits "
                "ein primärer Kommunikationsweg dieses Typs."
            ) from exc
        raise
    return point


def deactivate_contact_point(actor_app_user_id, contact_point_id):
    """Beendet einen Kommunikationsweg zeitlich (valid_until = heute).

    contact_point trägt kein no-delete-Verbot, wird aber wie die übrigen
    zeitabhängigen Zuordnungen beendet statt gelöscht (Historie).
    """
    point = ContactPoint.objects.filter(pk=contact_point_id).first()
    if point is None:
        raise ValueError("Kommunikationsweg nicht gefunden.")
    if point.valid_until is not None:
        raise ValueError("Dieser Kommunikationsweg ist bereits beendet.")
    ende = datetime.date.today()
    if ende <= point.valid_from:
        ende = point.valid_from + datetime.timedelta(days=1)
    with business_transaction(actor_app_user_id):
        ContactPoint.objects.filter(pk=point.id).update(valid_until=ende)
    point.valid_until = ende
    return point
