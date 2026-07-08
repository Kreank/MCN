"""Unmanaged Models auf das Fachschema (database-first).

Muster: managed = False, db_table mit Schema-Quoting-Trick
('security"."app_user' ergibt "security"."app_user"). Django rührt diese
Tabellen bei Migrationen nie an; Trigger, Constraints und Statusautomaten
setzt die Datenbank selbst durch.

Neue Fachtabellen: SQL-Migration schreiben (siehe README), dann hier das
Model nachziehen. `python manage.py inspectdb --database default <tabelle>`
liefert einen Startpunkt.
"""
from django.db import models
from django.db.models import Func
from django.db.models.functions import Now


class PropertyNumberDefault(Func):
    """DB-seitiger Default für property.property_number.

    Spiegelt die Migration 0004 wörtlich: 'OBJ-' + fünfstellige, per Sequenz
    gezogene Nummer. Als db_default kompiliert Django diesen Ausdruck direkt in
    das INSERT, sodass die Nummernvergabe in der Datenbank (Sequenz) bleibt und
    die ORM keinen NULL-Wert einsetzt, der den Spalten-Default aushebeln würde.
    """

    function = ""
    template = (
        "'OBJ-' || lpad(nextval('property.property_number_seq')::text, 5, '0')"
    )
    output_field = models.TextField()


class AppUser(models.Model):
    """security.app_user — fachliches Referenzziel für Audit/Trigger."""

    id = models.UUIDField(primary_key=True)
    display_name = models.TextField()
    principal_party_id = models.UUIDField(null=True, blank=True)
    status = models.TextField()  # ACTIVE | DISABLED
    version = models.IntegerField()
    # db_default: die DB füllt die Zeitstempel selbst (DEFAULT now()); ohne das
    # würde die ORM ein explizites NULL einsetzen und den DB-Default aushebeln.
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'security"."app_user'

    def __str__(self):
        return self.display_name


class Party(models.Model):
    """identity.party — Personen und Organisationen (Merge-Kanonik in der DB)."""

    id = models.UUIDField(primary_key=True)
    party_type = models.TextField()  # PERSON | ORGANIZATION
    display_name = models.TextField()
    status = models.TextField()  # ACTIVE | INACTIVE | MERGED
    merged_into_party = models.ForeignKey(
        "self",
        models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="merged_into_party_id",
        related_name="merged_parties",
    )
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'identity"."party'

    def __str__(self):
        return f"{self.display_name} ({self.party_type})"


class Person(models.Model):
    """identity.person — Subtyp einer Party mit party_type = 'PERSON'."""

    party = models.OneToOneField(
        Party,
        models.DO_NOTHING,
        primary_key=True,
        db_column="party_id",
        related_name="person",
    )
    salutation = models.TextField(null=True, blank=True)
    title = models.TextField(null=True, blank=True)
    first_name = models.TextField()
    last_name = models.TextField()
    birth_date = models.DateField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'identity"."person'

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Organization(models.Model):
    """identity.organization — Subtyp einer Party mit party_type = 'ORGANIZATION'."""

    party = models.OneToOneField(
        Party,
        models.DO_NOTHING,
        primary_key=True,
        db_column="party_id",
        related_name="organization",
    )
    # Beschlossene Codeliste (A-02); genau ein Haupttyp je Organisation.
    organization_type = models.TextField()
    legal_name = models.TextField()
    legal_form = models.TextField(null=True, blank=True)
    registration_number = models.TextField(null=True, blank=True)
    tax_number = models.TextField(null=True, blank=True)
    vat_id = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'identity"."organization'

    def __str__(self):
        return self.legal_name


class Address(models.Model):
    """identity.address — unveränderliche Adresse (Korrektur = neue Zeile).

    Der Trigger trg_address_immutable verbietet UPDATE/DELETE physisch; eine
    Adresse wird nur angelegt und danach referenziert.
    """

    id = models.UUIDField(primary_key=True)
    street = models.TextField()
    house_number = models.TextField(null=True, blank=True)
    address_addition = models.TextField(null=True, blank=True)
    postal_code = models.TextField()
    city = models.TextField()
    country_code = models.CharField(max_length=2, db_default="DE")
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'identity"."address'

    def __str__(self):
        return f"{self.street}, {self.postal_code} {self.city}"


class Property(models.Model):
    """property.property — Liegenschaft (Haupt-Entität der Objektwelt)."""

    id = models.UUIDField(primary_key=True)
    # Nummernvergabe bleibt in der DB-Sequenz; siehe PropertyNumberDefault.
    property_number = models.TextField(db_default=PropertyNumberDefault())
    name = models.TextField()
    address = models.ForeignKey(
        Address, models.DO_NOTHING, db_column="address_id", related_name="properties"
    )
    property_type = models.TextField()  # WEG|RENTAL_PROPERTY|COMMERCIAL|MIXED|OTHER
    status = models.TextField()  # ACTIVE|INACTIVE
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."property'

    def __str__(self):
        return f"{self.property_number} {self.name}"


class Building(models.Model):
    """property.building — Gebäude einer Liegenschaft."""

    id = models.UUIDField(primary_key=True)
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="buildings"
    )
    building_number = models.TextField()
    name = models.TextField(null=True, blank=True)
    address = models.ForeignKey(
        Address,
        models.DO_NOTHING,
        db_column="address_id",
        null=True,
        blank=True,
        related_name="buildings",
    )
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."building'

    def __str__(self):
        return self.name or self.building_number


class Unit(models.Model):
    """property.unit — Einheit in einem Gebäude.

    Die DB kennt einen zusammengesetzten FK (building_id, property_id) →
    building; Django modelliert beide Spalten als eigene FKs. property_id ist
    redundant, aber von der DB konsistenzgesichert und muss beim Anlegen zum
    Gebäude passen.
    """

    id = models.UUIDField(primary_key=True)
    building = models.ForeignKey(
        Building, models.DO_NOTHING, db_column="building_id", related_name="units"
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="units"
    )
    # APARTMENT|COMMERCIAL|GARAGE|PARKING|STORAGE|COMMON_AREA|TECHNICAL_ROOM|OTHER
    unit_type = models.TextField()
    unit_number = models.TextField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."unit'

    def __str__(self):
        return self.unit_number


class PropertyPartyRole(models.Model):
    """property.property_party_role — zeitlich gültige Party-Rolle an einer
    Liegenschaft (Eigentümergemeinschaft/Eigentümer/Betreiber/Hausmeister).

    Append-only: der Trigger trg_property_party_role_no_delete verbietet DELETE;
    Referenzen auf MERGED-Parties lehnt trg_property_role_no_merged ab.
    """

    id = models.UUIDField(primary_key=True)
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="party_roles"
    )
    party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="party_id", related_name="property_roles"
    )
    # COMMUNITY_OF_OWNERS|PROPERTY_OWNER|OPERATOR|CARETAKER
    role = models.TextField()
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."property_party_role'

    def __str__(self):
        return f"{self.role} @ {self.property_id}"
