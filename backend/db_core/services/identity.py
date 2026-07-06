"""Identity-Service: Parties (Personen/Organisationen) anlegen.

Alle Writes laufen ausschließlich über db_core.db_context.business_transaction,
damit die DB-Trigger app.current_user_id sehen (Audit) und die
Typkonsistenz-Trigger greifen. Transaktionen bleiben kurz: Party + Subtyp in
einer Transaktion, keine weitere Arbeit darin.
"""
import uuid

from db_core.db_context import business_transaction
from db_core.models import Organization, Party, Person

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
