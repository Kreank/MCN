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
from django.db.models.functions import Now


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
