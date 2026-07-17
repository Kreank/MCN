"""Unmanaged Models auf das Fachschema (database-first).

Muster: managed = False, db_table mit Schema-Quoting-Trick
('security"."app_user' ergibt "security"."app_user"). Django rührt diese
Tabellen bei Migrationen nie an; Trigger, Constraints und Statusautomaten
setzt die Datenbank selbst durch.

Neue Fachtabellen: SQL-Migration schreiben (siehe README), dann hier das
Model nachziehen. `python manage.py inspectdb --database default <tabelle>`
liefert einen Startpunkt.
"""
from django.contrib.postgres.fields import ArrayField
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


class EmployeeNumberDefault(Func):
    """DB-seitiger Default für hr.employee.employee_number (Migration 0019).

    Eigene Sequenz statt workflow.next_number(): die Personalnummer ist kein
    Beleg und gehört deshalb in keinen GoBD-Belegkreis.
    """

    function = ""
    template = "'MA-' || lpad(nextval('hr.employee_number_seq')::text, 5, '0')"
    output_field = models.TextField()


class ResourceNumberDefault(Func):
    """DB-seitiger Default für resource.resource.resource_number (Migration 0025).

    Eigene Sequenz statt workflow.next_number(): die Ressourcennummer ist kein
    Beleg und gehört deshalb in keinen GoBD-Belegkreis (Muster hr.employee).
    """

    function = ""
    template = (
        "'RES-' || lpad(nextval('resource.resource_number_seq')::text, 5, '0')"
    )
    output_field = models.TextField()


class ReceiptNumberDefault(Func):
    """DB-seitiger Default für accounting.receipt.receipt_number (Migration 0031).

    Eigene Sequenz statt workflow.next_number(): der Eingangsbeleg ist ein FREMDER
    Beleg; seine Ordnungsnummer (EB-#####) ist eine interne Erfassungs-/Ablage-
    nummer, kein GoBD-Ausgangsbelegkreis (Muster hr.employee). Ohne diesen
    db_default setzte die ORM einen leeren String ein und höbe den Spalten-Default
    aus (CHECK receipt_number ~ '^EB-[0-9]{5,}$' schlüge fehl).
    """

    function = ""
    template = "'EB-' || lpad(nextval('accounting.receipt_number_seq')::text, 5, '0')"
    output_field = models.TextField()


class _NextNumber(Func):
    """DB-seitiger Default über workflow.next_number(prefix) — vergibt fortlaufende
    Fachnummern (Format PREFIX-JJJJ-NNNNNN) in der DB (Migration 0010). Subklassen
    setzen den Prefix; als db_default bleibt die Vergabe atomar in der DB."""

    function = ""
    output_field = models.TextField()


class ProjectNumberDefault(_NextNumber):
    template = "workflow.next_number('P')"


class ServiceCaseNumberDefault(_NextNumber):
    template = "workflow.next_number('V')"


class WorkOrderNumberDefault(_NextNumber):
    template = "workflow.next_number('AU')"


class ServiceJobNumberDefault(_NextNumber):
    template = "workflow.next_number('E')"


class MaintenanceContractNumberDefault(_NextNumber):
    template = "workflow.next_number('W')"


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
    acquisition_source = models.ForeignKey(
        "AcquisitionSource",
        models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="acquisition_source_id",
        related_name="parties",
    )
    note = models.TextField(null=True, blank=True)
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


class PartyAddress(models.Model):
    """identity.party_address — zeitabhängige Zuordnung Adresse↔Party mit Typ.

    Eine Exclusion (excl_party_address_primary) verbietet je Party und Typ zwei
    zeitgleich primäre Adressen (Constraint über den Gültigkeitszeitraum); der
    Service fängt die Verletzung als 422 ab.
    """

    id = models.UUIDField(primary_key=True)
    party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="party_id", related_name="addresses"
    )
    address = models.ForeignKey(
        Address, models.DO_NOTHING, db_column="address_id", related_name="party_links"
    )
    address_type = models.TextField()  # BUSINESS | POSTAL | BILLING | PRIVATE
    is_primary = models.BooleanField()
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    label = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'identity"."party_address'


class ContactPoint(models.Model):
    """identity.contact_point — zeitabhängiger Kommunikationsweg einer Party.

    contact_type ∈ EMAIL|PHONE|MOBILE|FAX|PORTAL. Eine Exclusion
    (excl_contact_point_primary) verbietet je Party und Typ zwei zeitgleich
    primäre Wege; der Service fängt die Verletzung als 422 ab.
    """

    id = models.UUIDField(primary_key=True)
    party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="party_id", related_name="contact_points"
    )
    contact_type = models.TextField()  # EMAIL | PHONE | MOBILE | FAX | PORTAL
    value = models.TextField()
    label = models.TextField(null=True, blank=True)
    is_primary = models.BooleanField()
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'identity"."contact_point'


class PartyRelationship(models.Model):
    """identity.party_relationship — gerichtete Beziehung zweier Parties.

    relationship_type ∈ CONTACT_PERSON_FOR|EMPLOYEE_OF|… . Die Zeile wird nie
    gelöscht (trg_party_relationship_no_delete); beendet wird per valid_until
    (UPDATE, auditiert). Merged Parties sind als Referenz unzulässig (P0001).
    """

    id = models.UUIDField(primary_key=True)
    from_party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="from_party_id",
        related_name="relationships_from",
    )
    to_party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="to_party_id",
        related_name="relationships_to",
    )
    relationship_type = models.TextField()
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'identity"."party_relationship'


class Property(models.Model):
    """property.property — Liegenschaft (Haupt-Entität der Objektwelt)."""

    id = models.UUIDField(primary_key=True)
    # Nummernvergabe bleibt in der DB-Sequenz; siehe PropertyNumberDefault.
    property_number = models.TextField(db_default=PropertyNumberDefault())
    name = models.TextField()
    address = models.ForeignKey(
        Address, models.DO_NOTHING, db_column="address_id", related_name="properties"
    )
    property_type = models.TextField()  # WEG|RENTAL_PROPERTY|COMMERCIAL|MIXED|OTHER|EINFAMILIENHAUS
    status = models.TextField()  # ACTIVE|INACTIVE
    # Auslegungsdaten fürs Raumaufmaß (Migration 0089). Bewusst NICHT vorbelegt:
    # Norm-Außentemperaturen und Gebäudekennwerte sind DIN-Tabellenwerte, die MCN
    # nicht mitliefert. Fehlen sie, ist die Heizlast unbekannt — nicht 0.
    design_outdoor_temp_c = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    heat_load_w_per_m2 = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True
    )
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."property'

    def __str__(self):
        return f"{self.property_number} {self.name}"


class TechnicalAsset(models.Model):
    """property.technical_asset — technische Anlage (Therme, Aufzug, Hebeanlage …).

    Existiert seit db/migrations/0004, war aber bis zum Anlagen-Slice **totes
    Schema**. Migration **0101** macht sie zur Fachentität: echte Spalten mit
    CHECKs statt Freitext-JSON, und der Schutzstandard (Audit/No-Delete/
    No-Truncate) wie bei `property.room`.

    **Kein Löschen** — `status` AKTIV/INAKTIV (der No-Delete-Trigger erzwingt es
    jetzt physisch, nicht mehr nur der Service).

    `supply_type` (ZENTRAL|DEZENTRAL|UNBEKANNT) ist der fachliche Kern: „Mieter
    meldet Heizkörper kalt" heißt bei einer Zentralanlage etwas anderes als bei
    einer Etagentherme. **UNBEKANNT ist ein echter Wert**, kein geratenes
    „dezentral".

    `power_kw = NULL` heißt **unbekannt**, nie 0 kW (CHECK: > 0).

    `building_id`/`unit_id` sind zusammengesetzte FKs (Composite-Ziel) und
    deshalb hier als reine UUIDs geführt. `attributes` bleibt für echte
    Zusatzfakten ohne eigenes Feld.
    """

    id = models.UUIDField(primary_key=True)
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="assets"
    )
    building_id = models.UUIDField(null=True, blank=True)
    unit_id = models.UUIDField(null=True, blank=True)
    name = models.TextField()
    asset_type = models.TextField()  # CHECK-Codeliste, siehe services/anlage.py
    status = models.TextField(db_default="AKTIV")  # AKTIV | INAKTIV
    supply_type = models.TextField(db_default="UNBEKANNT")
    manufacturer = models.TextField(null=True, blank=True)
    model = models.TextField(null=True, blank=True)
    serial_number = models.TextField(null=True, blank=True)
    year_built = models.IntegerField(null=True, blank=True)
    energy_source = models.TextField(null=True, blank=True)
    power_kw = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    location_note = models.TextField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    attributes = models.JSONField(db_default={})
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."technical_asset'

    def __str__(self):
        return self.name


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


class ProjectCategory(models.Model):
    """workflow.project_category — Projektordner/-kategorie (Gliederung/Filter).

    Rein organisatorisch (Name, Farbe, Reihenfolge), kein Statusautomat.
    """

    id = models.UUIDField(primary_key=True)
    name = models.TextField()
    color_hex = models.TextField(null=True, blank=True)
    sort_order = models.IntegerField()
    status = models.TextField()  # AKTIV | INAKTIV
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."project_category'

    def __str__(self):
        return self.name


class Project(models.Model):
    """workflow.project — Projekt (Akte/Cockpit-Klammer, Hero-„Projekt").

    Technischer Minimalstatus OPEN/CLOSED (kein Statusautomat). Append-only:
    trg_project_no_delete verbietet DELETE; Updates werden auditiert.
    """

    id = models.UUIDField(primary_key=True)
    # Nummernvergabe bleibt in der DB (workflow.next_number), siehe Default.
    project_number = models.TextField(db_default=ProjectNumberDefault())
    name = models.TextField()
    status = models.TextField()  # OPEN | CLOSED
    start_date = models.DateField(null=True, blank=True)
    target_end_date = models.DateField(null=True, blank=True)
    responsible_user = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="responsible_user_id",
        null=True,
        blank=True,
        related_name="responsible_projects",
    )
    category = models.ForeignKey(
        ProjectCategory,
        models.DO_NOTHING,
        db_column="category_id",
        null=True,
        blank=True,
        related_name="projects",
    )
    internal_note = models.TextField(null=True, blank=True)
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."project'

    def __str__(self):
        return f"{self.project_number} {self.name}"


class ProjectProperty(models.Model):
    """workflow.project_property — Projekt↔Liegenschaft (M:N).

    Zusammengesetzter PK (project_id, property_id) über Djangos
    CompositePrimaryKey (Django 5.2). Zeilen sind unveränderlich; Löschen nur
    solange das Projekt OPEN ist (DB-Trigger).
    """

    pk = models.CompositePrimaryKey("project_id", "property_id")
    project = models.ForeignKey(
        Project, models.DO_NOTHING, db_column="project_id", related_name="property_links"
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="project_links"
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."project_property'


class ServiceCase(models.Model):
    """workflow.service_case — Vorgang (reicher Statusautomat).

    Statusänderungen laufen über die DB-Trigger (validate_status_change);
    begründungspflichtige Übergänge verlangen app.status_reason. Hier zunächst
    für Anzeige (Liste/Detail) und einfache Anlage (Initialstatus NEU).
    """

    id = models.UUIDField(primary_key=True)
    case_number = models.TextField(db_default=ServiceCaseNumberDefault())
    project = models.ForeignKey(
        Project,
        models.DO_NOTHING,
        db_column="project_id",
        null=True,
        blank=True,
        related_name="service_cases",
    )
    subject = models.TextField()
    description = models.TextField(null=True, blank=True)
    reported_by_party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="reported_by_party_id",
        null=True,
        blank=True,
        related_name="reported_cases",
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="service_cases"
    )
    responsibility_scope = models.TextField()  # UNKNOWN|COMMON_PROPERTY|PRIVATE_UNIT|MIXED
    priority = models.TextField()  # NORMAL|DRINGEND|NOTFALL (FK priority_level.code)
    # NEU|IN_PRUEFUNG|RUECKFRAGE|FREIGABE_AUSSTEHEND|BEAUFTRAGT|ABGESCHLOSSEN|ABGELEHNT
    status = models.TextField()
    received_at = models.DateTimeField(db_default=Now())
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."service_case'

    def __str__(self):
        return f"{self.case_number} {self.subject}"


class ProjectLog(models.Model):
    """workflow.project_log — Projekt-Logbuch (append-only, Migration 0035)."""

    id = models.UUIDField(primary_key=True)
    project = models.ForeignKey(
        Project, models.DO_NOTHING, db_column="project_id", related_name="log_entries"
    )
    category = models.TextField()  # NOTIZ|ANRUF|ABSPRACHE|ENTSCHEIDUNG|SYSTEM
    entry = models.TextField()
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by", related_name="log_entries"
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."project_log'


class Checklist(models.Model):
    """workflow.checklist — Checklisten-Instanz eines Projekts (Migration 0035)."""

    id = models.UUIDField(primary_key=True)
    project = models.ForeignKey(
        Project, models.DO_NOTHING, db_column="project_id", related_name="checklists"
    )
    name = models.TextField()
    template_id = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by", related_name="checklists"
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."checklist'

    def __str__(self):
        return self.name


class ChecklistItem(models.Model):
    """workflow.checklist_item — Checklistenpunkt (Migration 0035).

    Erledigt = done_by UND done_at gesetzt (DB-CHECK erzwingt beide gemeinsam).
    """

    id = models.UUIDField(primary_key=True)
    checklist = models.ForeignKey(
        Checklist, models.DO_NOTHING, db_column="checklist_id", related_name="items"
    )
    position = models.IntegerField()
    label = models.TextField()
    done_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="done_by",
        null=True,
        blank=True,
        related_name="done_checklist_items",
    )
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'workflow"."checklist_item'

    def __str__(self):
        return f"{self.position}. {self.label}"


class StatusChange(models.Model):
    """workflow.status_change — append-only Statusverlauf (Migration 0010).

    Wird ausschließlich von den Statusautomat-Triggern befüllt; hier nur lesend
    (Verlauf einer Entität anzeigen). entity/entity_id verweisen generisch auf
    service_case/work_order/service_job/quote.
    """

    id = models.UUIDField(primary_key=True)
    entity = models.TextField()
    entity_id = models.UUIDField()
    from_status = models.TextField(null=True, blank=True)
    to_status = models.TextField()
    reason = models.TextField(null=True, blank=True)
    changed_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="changed_by_user_id",
        null=True,
        blank=True,
        related_name="status_changes",
    )
    occurred_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."status_change'

    def __str__(self):
        return f"{self.entity} {self.from_status}->{self.to_status}"


class StatusCatalog(models.Model):
    """workflow.status_catalog — Vokabular des Statusautomaten je Entity
    (Migration 0042, Pipeline-Editor): deutsches Label, Reihenfolge und die
    Anfangs-/Final-/Freeze-Marker.

    Read-only Stammdaten. Liefert die Labels für die erlaubten Übergänge; wird
    zur Laufzeit gelesen, damit eine über den Editor geänderte Pipeline sofort
    wirkt. Zusammengesetzter PK (entity, status).
    """

    pk = models.CompositePrimaryKey("entity", "status")
    entity = models.TextField()  # service_case|work_order|service_job|quote
    status = models.TextField()
    label = models.TextField()
    sort_order = models.IntegerField()
    is_initial = models.BooleanField()
    is_final = models.BooleanField()
    is_frozen = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'workflow"."status_catalog'

    def __str__(self):
        return f"{self.entity}:{self.status}"


class StatusTransition(models.Model):
    """workflow.status_transition — erlaubte Statusübergänge je Entity
    (Migration 0010, seit 0042 über den Pipeline-Editor konfigurierbar).

    requires_reason erzwingt eine Begründung (app.status_reason). Hier nur
    lesend: die Übergänge werden ZUR LAUFZEIT ausgewertet (nicht hartkodiert),
    damit eine editierte Pipeline nicht vom UI/Service abweicht. Der DB-Trigger
    validate_status_change bleibt die maßgebliche Instanz. Zusammengesetzter PK
    (entity, from_status, to_status).
    """

    pk = models.CompositePrimaryKey("entity", "from_status", "to_status")
    entity = models.TextField()
    from_status = models.TextField()
    to_status = models.TextField()
    requires_reason = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'workflow"."status_transition'

    def __str__(self):
        return f"{self.entity}:{self.from_status}->{self.to_status}"


class Task(models.Model):
    """workflow.task — Aufgabe (leichtgewichtiges To-do, Migration 0005).

    Optional verknüpft mit Projekt und/oder Kontakt (Party) und einem
    Zuständigen. Erledigen setzt completed_by/completed_at; kein physisches
    Löschen (Schutzstandard) — Status VERWORFEN statt DELETE.
    """

    id = models.UUIDField(primary_key=True)
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.TextField()  # OFFEN | ERLEDIGT | VERWORFEN
    assigned_to = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="assigned_to_user_id",
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    project = models.ForeignKey(
        Project,
        models.DO_NOTHING,
        db_column="project_id",
        null=True,
        blank=True,
        related_name="tasks",
    )
    party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="party_id",
        null=True,
        blank=True,
        related_name="tasks",
    )
    completed_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="completed_by",
        null=True,
        blank=True,
        related_name="completed_tasks",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by", related_name="created_tasks"
    )
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."task'

    def __str__(self):
        return self.title


class TaxCode(models.Model):
    """invoicing.tax_code — Steuercodes (DE_19, DE_7, DE_0, DE_13B; Migration 0016)."""

    code = models.TextField(primary_key=True)
    label = models.TextField()
    rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    mandatory_text = models.TextField(null=True, blank=True)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    stb_confirmed_at = models.DateField(null=True, blank=True)
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."tax_code'

    def __str__(self):
        return f"{self.code} ({self.rate_percent} %)"


class Quote(models.Model):
    """invoicing.quote — Angebot (Migration 0018).

    Belegnummer wird erst beim Versand vergeben (bleibt im ENTWURF NULL). Ab
    VERSENDET ist der Beleg eingefroren (B-30). Versand verlangt Snapshot +
    Inhalts-Hash (per DB-Trigger).

    `work_order` ordnet das Angebot einem **Auftrag** zu (optional; zusammengesetzter
    FK gegen die Liegenschaft, 0018). Diese Zuordnung ist die Aussage „das ist das
    Soll dieser Baustelle": der Soll-Ist-Abgleich am Baustellenbericht (0080) stützt
    sich ausschließlich darauf. Gesetzt wird sie über `beleg.create_quote` /
    `beleg.update_quote` — **in jedem Status, auch nach dem Versand**
    (Migration 0082): `invoicing.freeze_sent_quote` nimmt `work_order_id` aus der
    Einfrierung aus, weil sie ein interner Verweis ist und kein Beleginhalt (der
    reale Ablauf ist „versenden → Kunde nimmt an → *dann* Auftrag anlegen"). Der
    übrige Beleginhalt bleibt ab VERSENDET unveränderlich (B-30).
    """

    id = models.UUIDField(primary_key=True)
    quote_number = models.TextField(null=True, blank=True)
    work_order = models.ForeignKey(
        "WorkOrder",
        models.DO_NOTHING,
        db_column="work_order_id",
        null=True,
        blank=True,
        related_name="quotes",
    )
    # Vorgangsbezug (Migration 0113): der Beleg entsteht am Vorgang und wird bei der
    # Aufstufung Vorgang→Projekt mitgezogen. Zusammengesetzter FK gegen die
    # Liegenschaft (analog work_order). Optional.
    service_case = models.ForeignKey(
        "ServiceCase",
        models.DO_NOTHING,
        db_column="service_case_id",
        null=True,
        blank=True,
        related_name="quotes",
    )
    project = models.ForeignKey(
        Project,
        models.DO_NOTHING,
        db_column="project_id",
        null=True,
        blank=True,
        related_name="quotes",
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="quotes"
    )
    title = models.TextField()
    # ENTWURF|INTERN_GEPRUEFT|FREIGEGEBEN|VERSENDET|ANGENOMMEN|ABGELEHNT|ABGELAUFEN|ERSETZT
    status = models.TextField()
    quote_date = models.DateField(null=True, blank=True)
    valid_until_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, db_default="EUR")
    net_total = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    tax_total = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    gross_total = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    billing_snapshot = models.JSONField(null=True, blank=True)
    content_hash = models.TextField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    replaced_by_quote_id = models.UUIDField(null=True, blank=True)
    cover_letter = models.TextField(null=True, blank=True)
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."quote'

    def __str__(self):
        return f"{self.quote_number or 'ENTWURF'} {self.title}"


class QuoteLine(models.Model):
    """invoicing.quote_line — Angebotsposition (Migration 0018).

    TEXT/ZWISCHENSUMME tragen keine Beträge; Betragszeilen sind vollständig
    (quantity/unit_price/net_amount/tax_code/tax_rate_percent). net_amount wird
    per CHECK erzwungen (kaufmännische Rundung) — die App muss es korrekt
    vorberechnen.

    `line_type` sagt, WAS die Position ist; `line_kind` (0036), OB sie in die
    Summe zählt (ALTERNATIV/BEDARF zählen nicht). Die Kalkulationsspalten (0033)
    frieren die Herkunft und die Marge zum Zeitpunkt der Belegerstellung ein:
    `unit_cost` = EK-Snapshot, `markup_percent` = Aufschlag-Snapshot (darf negativ
    sein = bewusster Verlust), `source_article`/`source_assembly` = Herkunft.
    """

    id = models.UUIDField(primary_key=True)
    quote = models.ForeignKey(
        Quote, models.DO_NOTHING, db_column="quote_id", related_name="lines"
    )
    rubrik = models.ForeignKey(
        "BelegRubrik", models.DO_NOTHING, db_column="rubrik_id",
        null=True, blank=True, related_name="quote_lines",
    )
    position_number = models.IntegerField()
    # MATERIAL|ARBEITSZEIT|PAUSCHALE|FREMDLEISTUNG|FAHRT|ZUSCHLAG|TEXT|ZWISCHENSUMME
    line_type = models.TextField()
    # NORMAL|ALTERNATIV|BEDARF (0036)
    line_kind = models.TextField(db_default="NORMAL")
    description = models.TextField()
    quantity = models.DecimalField(
        max_digits=15, decimal_places=3, null=True, blank=True
    )
    unit = models.TextField(null=True, blank=True)
    unit_price = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    discount_percent = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True
    )
    tax_code = models.ForeignKey(
        TaxCode,
        models.DO_NOTHING,
        db_column="tax_code",
        null=True,
        blank=True,
        related_name="quote_lines",
    )
    tax_rate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    net_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    # Arbeitskostenanteil nach § 35a EStG (Migration 0076). NULL = UNBESTIMMT,
    # nicht 0,00 — siehe InvoiceLine.labour_net_amount.
    labour_net_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    # Kalkulations-Snapshot (Migration 0033)
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    markup_percent = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True
    )
    sale_price_group = models.ForeignKey(
        "SalePriceGroup", models.DO_NOTHING, db_column="sale_price_group_id",
        null=True, blank=True, related_name="quote_lines",
    )
    source_article = models.ForeignKey(
        "Article", models.DO_NOTHING, db_column="source_article_id",
        null=True, blank=True, related_name="quote_lines",
    )
    source_assembly = models.ForeignKey(
        "Assembly", models.DO_NOTHING, db_column="source_assembly_id",
        null=True, blank=True, related_name="quote_lines",
    )
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."quote_line'

    def __str__(self):
        return f"{self.position_number}. {self.description}"


class Invoice(models.Model):
    """invoicing.invoice — Rechnung/Gutschrift (Migration 0019).

    Kein title (Identität über Typ + Nummer). Nummer erst bei Veröffentlichung
    (bleibt im ENTWURF NULL); ab VEROEFFENTLICHT vollständig eingefroren (B-30).
    Veröffentlichung verlangt Snapshot + Inhalts-Hash, einen kaufmännisch
    geprüften Auftrag (work_order, B-08) und bestätigte Beteiligte (invoice_party,
    A-27) — physisch per DB-Trigger erzwungen.
    """

    id = models.UUIDField(primary_key=True)
    invoice_number = models.TextField(null=True, blank=True)
    # RECHNUNG|ABSCHLAGSRECHNUNG|TEILRECHNUNG|SCHLUSSRECHNUNG|GUTSCHRIFT|STORNO
    invoice_type = models.TextField()
    work_order = models.ForeignKey(
        "WorkOrder",
        models.DO_NOTHING,
        db_column="work_order_id",
        null=True,
        blank=True,
        related_name="invoices",
    )
    # Vorgangsbezug (Migration 0113): siehe Quote.service_case. Optional; wird bei
    # der Aufstufung Vorgang→Projekt mitgezogen.
    service_case = models.ForeignKey(
        "ServiceCase",
        models.DO_NOTHING,
        db_column="service_case_id",
        null=True,
        blank=True,
        related_name="invoices",
    )
    project = models.ForeignKey(
        Project,
        models.DO_NOTHING,
        db_column="project_id",
        null=True,
        blank=True,
        related_name="invoices",
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="invoices"
    )
    reference_invoice_id = models.UUIDField(null=True, blank=True)
    status = models.TextField()  # ENTWURF | VEROEFFENTLICHT
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    # Zahlungsbedingungen je Rechnung (Migration 0058). `due_date` bleibt die
    # maßgebliche Fälligkeit (Mahnwesen/DATEV); payment_term_days leitet sie beim
    # Veröffentlichen ab, wenn sie leer geblieben ist. Skontosatz und -frist gibt
    # es nur gemeinsam; Gutschrift/Storno tragen keine Zahlungsbedingungen (DB-CHECK).
    payment_term_days = models.IntegerField(null=True, blank=True)
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    discount_days = models.IntegerField(null=True, blank=True)
    # Arbeitskosten nach § 35a EStG ausweisen (Migration 0076). Default true: der
    # Privatkunde ist der Regelfall des Betriebs, und ein vergessener Haken kostet
    # ihn 20 % der Arbeitskosten. Auf einer B2B-Rechnung abschaltbar.
    show_labour_costs = models.BooleanField(db_default=models.Value(True))
    currency = models.CharField(max_length=3, db_default="EUR")
    net_total = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    tax_total = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    gross_total = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    billing_snapshot = models.JSONField(null=True, blank=True)
    content_hash = models.TextField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."invoice'

    def __str__(self):
        return f"{self.invoice_number or 'ENTWURF'} ({self.invoice_type})"


class InvoiceParty(models.Model):
    """invoicing.invoice_party — strukturierte Rechnungsbeteiligte (Migration 0019).

    Rollen A-27/A-29: INVOICE_DEBTOR (Schuldner), INVOICE_RECIPIENT (Empfänger),
    REPRESENTATIVE, COST_BEARER. Höchstens ein primärer Beteiligter je Rolle
    (partieller UNIQUE-Index). Nur im Entwurf veränderbar (nach Veröffentlichung
    eingefroren).
    """

    id = models.UUIDField(primary_key=True)
    invoice = models.ForeignKey(
        Invoice, models.DO_NOTHING, db_column="invoice_id", related_name="parties"
    )
    party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="party_id", related_name="invoice_roles"
    )
    # INVOICE_DEBTOR|INVOICE_RECIPIENT|REPRESENTATIVE|COST_BEARER
    role = models.TextField()
    is_primary = models.BooleanField(db_default=models.Value(False))
    allocation_percent = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True
    )
    liability_group = models.TextField(null=True, blank=True)
    liability_basis = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."invoice_party'

    def __str__(self):
        return f"{self.role} @ {self.invoice_id}"


class InvoiceLine(models.Model):
    """invoicing.invoice_line — Rechnungsposition (Migration 0019, wie quote_line)."""

    id = models.UUIDField(primary_key=True)
    invoice = models.ForeignKey(
        Invoice, models.DO_NOTHING, db_column="invoice_id", related_name="lines"
    )
    rubrik = models.ForeignKey(
        "BelegRubrik", models.DO_NOTHING, db_column="rubrik_id",
        null=True, blank=True, related_name="invoice_lines",
    )
    position_number = models.IntegerField()
    line_type = models.TextField()
    # NORMAL|ALTERNATIV|BEDARF (0036)
    line_kind = models.TextField(db_default="NORMAL")
    description = models.TextField()
    quantity = models.DecimalField(
        max_digits=15, decimal_places=3, null=True, blank=True
    )
    unit = models.TextField(null=True, blank=True)
    unit_price = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    discount_percent = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True
    )
    tax_code = models.ForeignKey(
        TaxCode,
        models.DO_NOTHING,
        db_column="tax_code",
        null=True,
        blank=True,
        related_name="invoice_lines",
    )
    tax_rate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    net_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    # Arbeitskostenanteil nach § 35a EStG (Migration 0076): der begünstigte
    # NETTO-Teil dieser Position (Lohn-, Maschinen-, Fahrtkosten).
    # **NULL heißt UNBESTIMMT, nicht 0,00.** Solange eine summenwirksame Position
    # unbestimmt ist, weist der Beleg gar keine Arbeitskosten aus — ein geratener
    # Anteil wäre eine Falschaussage gegenüber dem Finanzamt. Vorzeichen und
    # Betragsgrenze sind per CHECK an net_amount gebunden.
    labour_net_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    # Kalkulations-Snapshot (Migration 0033)
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    markup_percent = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True
    )
    sale_price_group = models.ForeignKey(
        "SalePriceGroup", models.DO_NOTHING, db_column="sale_price_group_id",
        null=True, blank=True, related_name="invoice_lines",
    )
    source_article = models.ForeignKey(
        "Article", models.DO_NOTHING, db_column="source_article_id",
        null=True, blank=True, related_name="invoice_lines",
    )
    source_assembly = models.ForeignKey(
        "Assembly", models.DO_NOTHING, db_column="source_assembly_id",
        null=True, blank=True, related_name="invoice_lines",
    )
    # Anrechnungsposition einer Schlussrechnung (Migration 0060): diese Position
    # rechnet die genannte Abschlags-/Teilrechnung an. Immer ein NEGATIVER Betrag,
    # je Steuersatz eine Position (DB-CHECK invoice_line_advance_is_deduction).
    advance_invoice = models.ForeignKey(
        Invoice, models.DO_NOTHING, db_column="advance_invoice_id",
        null=True, blank=True, related_name="deduction_lines",
    )
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."invoice_line'

    def __str__(self):
        return f"{self.position_number}. {self.description}"


class InvoiceAdvance(models.Model):
    """invoicing.invoice_advance — Schlussrechnung rechnet Abschlagsrechnung an
    (Migration 0060).

    Eine Zeile je (Schlussrechnung, Abschlags-/Teilrechnung, Steuercode) mit dem
    **eingefrorenen** angerechneten Betrag. Die Beträge sind positiv (sie sagen,
    WAS angerechnet wurde); das Vorzeichen des Abzugs trägt die zugehörige
    Anrechnungsposition (`InvoiceLine.advance_invoice`, negativer net_amount).

    Physisch abgesichert (siehe Migration 0060): nur eine Schlussrechnung kann
    anrechnen, nur veröffentlichte und nicht stornierte Abschläge desselben
    Auftrags sind anrechenbar, dieselbe Abschlagsrechnung nie zweimal, und beim
    Veröffentlichen müssen Verkettung und Positionen deckungsgleich sein.
    """

    id = models.UUIDField(primary_key=True)
    final_invoice = models.ForeignKey(
        Invoice, models.DO_NOTHING, db_column="final_invoice_id",
        related_name="advances",
    )
    advance_invoice = models.ForeignKey(
        Invoice, models.DO_NOTHING, db_column="advance_invoice_id",
        related_name="angerechnet_in",
    )
    tax_code = models.ForeignKey(
        TaxCode, models.DO_NOTHING, db_column="tax_code",
        related_name="invoice_advances",
    )
    tax_rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    net_amount = models.DecimalField(max_digits=15, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2)
    gross_amount = models.DecimalField(max_digits=15, decimal_places=2)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."invoice_advance'

    def __str__(self):
        return f"Anrechnung {self.gross_amount} ({self.tax_code_id})"


class BelegRubrik(models.Model):
    """invoicing.beleg_rubrik — Abschnitt/Titel eines Belegs (Migration 0033).

    Gliedert Angebot ODER Rechnung (XOR per CHECK) in Abschnitte. Der Kunde sieht
    im PDF die Zwischensumme je Abschnitt; die interne Kalkulationsübersicht (EK,
    Aufschlag, Deckungsbeitrag je Abschnitt) rechnet der Service aus den
    eingefrorenen Positionswerten — sie wird bewusst NICHT gespeichert, damit sie
    nicht von den Positionen abdriften kann.

    Die Rubriken folgen der Beleg-Einfrierung: ab Versand (Angebot) bzw.
    Veröffentlichung (Rechnung) sind sie per Trigger unveränderlich.
    """

    id = models.UUIDField(primary_key=True)
    quote = models.ForeignKey(
        Quote, models.DO_NOTHING, db_column="quote_id",
        null=True, blank=True, related_name="rubriken",
    )
    invoice = models.ForeignKey(
        Invoice, models.DO_NOTHING, db_column="invoice_id",
        null=True, blank=True, related_name="rubriken",
    )
    position_number = models.IntegerField()
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."beleg_rubrik'

    def __str__(self):
        return f"{self.position_number}. {self.title}"


class Payment(models.Model):
    """invoicing.payment — Zahlungseingang zu einer Rechnung (Migration 0025).

    Append-only (UPDATE/DELETE per Trigger gesperrt); Zahlung nur auf eine
    veröffentlichte Rechnung (B-23). Kein gespeicherter Zahlungsstatus — er wird
    aus der vorzeichenbehafteten Summe der Zahlungen abgeleitet (Konvention im
    Service). Ein „Storno" ist eine Gegenbuchung payment_type='STORNO_BUCHUNG',
    keine physische Löschung. UNIQUE(import_source, external_reference) macht den
    Rückimport idempotent; manuelle Erfassung nutzt import_source='MANUAL' mit
    synthetischer Referenz.
    """

    id = models.UUIDField(primary_key=True)
    invoice = models.ForeignKey(
        Invoice, models.DO_NOTHING, db_column="invoice_id", related_name="payments"
    )
    # ZAHLUNG|TEILZAHLUNG|UEBERZAHLUNG|RUECKERSTATTUNG|STORNO_BUCHUNG
    payment_type = models.TextField()
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, db_default="EUR")
    paid_at = models.DateField()
    import_source = models.TextField()
    external_reference = models.TextField()
    imported_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."payment'

    def __str__(self):
        return f"{self.payment_type} {self.amount} {self.currency}"


class DunningLevel(models.Model):
    """invoicing.dunning_level — Mahnstufen-Stammdaten (Migration 0025).

    Primärschlüssel ist die Stufennummer selbst. Geseedet sind 3 Stufen
    (Zahlungserinnerung, Mahnung 1/2); fee bleibt NULL (STB-Vorbehalt B-22). Der
    Hero-Vollausbau auf 6 Stufen steht noch aus.
    """

    level = models.IntegerField(primary_key=True)
    label = models.TextField()
    days_after_due = models.IntegerField()
    fee = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    interest_note = models.TextField(null=True, blank=True)
    # Stufe aktivierbar/deaktivierbar (Migration 0025 db_core). Deaktivierte
    # Stufen werden im Mahnlauf nicht ausgestellt; die aktiven Stufen müssen
    # einen lückenlosen Präfix {1..k} bilden (Service-Durchsetzung).
    active = models.BooleanField(db_default=True)
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."dunning_level'

    def __str__(self):
        return f"{self.level}. {self.label}"


class DunningNotice(models.Model):
    """invoicing.dunning_notice — erzeugte Mahnung/Zahlungserinnerung (Migration 0025).

    Append-only; je Rechnung ist jede Stufe nur einmal möglich (UNIQUE) und die
    Stufen müssen lückenlos aufsteigen. Die DB erzwingt: veröffentlichte, zum
    issued_at bereits fällige Rechnung; nächste Stufe = max+1. Das Mahndokument
    (content.document) ist optional — eine Mahnung entsteht auch ohne PDF.
    """

    id = models.UUIDField(primary_key=True)
    invoice = models.ForeignKey(
        Invoice,
        models.DO_NOTHING,
        db_column="invoice_id",
        related_name="dunning_notices",
    )
    level = models.ForeignKey(
        DunningLevel,
        models.DO_NOTHING,
        db_column="level",
        related_name="notices",
    )
    issued_at = models.DateField()
    document_id = models.UUIDField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="created_by",
        related_name="dunning_notices",
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."dunning_notice'

    def __str__(self):
        return f"Mahnstufe {self.level_id} @ {self.invoice_id}"


class MaintenanceContract(models.Model):
    """maintenance.maintenance_contract — Wartungsvertrag (Migration 0016).

    Objektzentriert (property Pflicht), Kunde/Projekt optional. Statusautomat
    AKTIV ↔ INAKTIV, INAKTIV → ARCHIVIERT (final, per DB-Trigger erzwungen; kein
    Row-Delete). Vertragsnummer (W-…) vergibt die DB (db_default). Bei Fälligkeit
    (next_due_date) löst die konfigurierte due_action eine Folgeaktion aus.
    """

    id = models.UUIDField(primary_key=True)
    contract_number = models.TextField(db_default=MaintenanceContractNumberDefault())
    name = models.TextField()
    property = models.ForeignKey(
        Property,
        models.DO_NOTHING,
        db_column="property_id",
        related_name="maintenance_contracts",
    )
    party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="party_id",
        null=True,
        blank=True,
        related_name="maintenance_contracts",
    )
    project = models.ForeignKey(
        Project,
        models.DO_NOTHING,
        db_column="project_id",
        null=True,
        blank=True,
        related_name="maintenance_contracts",
    )
    status = models.TextField()  # AKTIV | INAKTIV | ARCHIVIERT
    start_date = models.DateField()
    # JAEHRLICH|MONATLICH|WOECHENTLICH|TAGE|FESTES_DATUM
    interval_kind = models.TextField()
    interval_days = models.IntegerField(null=True, blank=True)
    fixed_date = models.DateField(null=True, blank=True)
    next_due_date = models.DateField(null=True, blank=True)
    # PROJEKT|AUFTRAG|AUFGABE|BENACHRICHTIGUNG
    due_action = models.TextField()
    lead_time_days = models.IntegerField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="created_by",
        related_name="maintenance_contracts",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'maintenance"."maintenance_contract'

    def __str__(self):
        return f"{self.contract_number} {self.name}"


class MaintenanceEvent(models.Model):
    """maintenance.maintenance_event — ausgelöste Fälligkeits-Aktion (Migration 0016).

    Append-only Nachweis, welche Fälligkeit welche Folgeaktion (und welches
    Folgeobjekt) erzeugt hat.
    """

    id = models.UUIDField(primary_key=True)
    contract = models.ForeignKey(
        MaintenanceContract,
        models.DO_NOTHING,
        db_column="contract_id",
        related_name="events",
    )
    occurred_at = models.DateTimeField(db_default=Now())
    due_date = models.DateField(null=True, blank=True)
    # PROJEKT|AUFTRAG|AUFGABE|BENACHRICHTIGUNG
    action = models.TextField()
    result_object_type = models.TextField(null=True, blank=True)
    result_object_id = models.UUIDField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="triggered_by",
        null=True,
        blank=True,
        related_name="maintenance_events",
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'maintenance"."maintenance_event'

    def __str__(self):
        return f"{self.action} @ {self.contract_id}"


# ---------------------------------------------------------------------------
# maintenance.* — Fälligkeiten-Engine (Migration 0071)
#
# Drei Fristenarten, ein Fälligkeitsmodell: WARTUNG (Vertrag, 0016), PRUEFUNG
# (Prüfart + Prüfung an Objekt/Anlage) und GEWAEHRLEISTUNG (je Auftrag).
# ---------------------------------------------------------------------------


class InspectionType(models.Model):
    """maintenance.inspection_type — Prüfart (Stammdaten, vom Betrieb gepflegt).

    `is_suggestion=True` markiert die mitgelieferten Vorschläge. Sie sind
    ausdrücklich KEINE Normtabelle und keine Rechtsauskunft — Intervall und
    Zuständigkeit muss der Betrieb selbst prüfen und anpassen.
    """

    id = models.UUIDField(primary_key=True)
    name = models.TextField()
    interval_kind = models.TextField()  # JAEHRLICH|MONATLICH|WOECHENTLICH|TAGE
    interval_days = models.IntegerField(null=True, blank=True)
    lead_time_days = models.IntegerField(db_default=models.Value(30))
    responsibility = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    is_suggestion = models.BooleanField(db_default=False)
    is_active = models.BooleanField(db_default=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by", null=True, blank=True,
        related_name="inspection_types",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'maintenance"."inspection_type'

    def __str__(self):
        return self.name


class Inspection(models.Model):
    """maintenance.inspection — wiederkehrende Prüfung an Liegenschaft/Anlage.

    Die Intervall-/Vorlauffelder sind bei der Anlage aus der Prüfart KOPIERT
    (nicht referenziert): eine spätere Änderung der Prüfart verschiebt den Plan
    einer laufenden Prüfung nicht rückwirkend. `asset_id` ist ein
    zusammengesetzter FK (asset_id, property_id) auf property.technical_asset —
    hier als reine UUID geführt (Composite-Ziel, kein ORM-FK).
    """

    id = models.UUIDField(primary_key=True)
    inspection_type = models.ForeignKey(
        InspectionType, models.DO_NOTHING, db_column="inspection_type_id",
        related_name="inspections",
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id",
        related_name="inspections",
    )
    asset_id = models.UUIDField(null=True, blank=True)
    name = models.TextField()
    status = models.TextField()  # AKTIV | INAKTIV | ARCHIVIERT
    start_date = models.DateField()
    interval_kind = models.TextField()
    interval_days = models.IntegerField(null=True, blank=True)
    lead_time_days = models.IntegerField(db_default=models.Value(30))
    next_due_date = models.DateField(null=True, blank=True)
    responsibility = models.TextField(null=True, blank=True)
    party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="party_id", null=True, blank=True,
        related_name="inspections",
    )
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="inspections",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'maintenance"."inspection'

    def __str__(self):
        return self.name


class Warranty(models.Model):
    """maintenance.warranty — Gewährleistungsfrist eines Auftrags (genau eine).

    `basis` (BGB|VOB|INDIVIDUELL) ist ein LABEL, keine Rechtsfolge: das Produkt
    leitet daraus keine Frist ab, es merkt sich, was der Betrieb vereinbart hat.
    Maßgeblich ist `duration_months` — je Auftrag einstellbar, Default aus dem
    Firmenprofil. `is_machinery` ist ein reiner Hinweis-Schalter (wartungs-
    bedürftige maschinelle Anlage) und verkürzt NICHTS automatisch.
    """

    id = models.UUIDField(primary_key=True)
    # WorkOrder ist erst weiter unten definiert → Lazy-Referenz als String.
    work_order = models.OneToOneField(
        "WorkOrder", models.DO_NOTHING, db_column="work_order_id",
        related_name="warranty",
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id",
        related_name="warranties",
    )
    party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="party_id", null=True, blank=True,
        related_name="warranties",
    )
    basis = models.TextField(db_default="BGB")
    start_date = models.DateField()
    duration_months = models.IntegerField()
    end_date = models.DateField()
    lead_time_days = models.IntegerField(db_default=models.Value(90))
    is_machinery = models.BooleanField(db_default=False)
    status = models.TextField(db_default="AKTIV")  # AKTIV | ARCHIVIERT
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="warranties",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'maintenance"."warranty'

    def __str__(self):
        return f"Gewährleistung bis {self.end_date}"


class DueItem(models.Model):
    """maintenance.due_item — EINE Fälligkeit, egal welcher Art.

    Genau ein Anker (contract | inspection | warranty), passend zur `kind`
    (DB-CHECK). Statusautomat OFFEN → ERLEDIGT | VERWORFEN, beide final
    (Trigger); Verwerfen ist begründungspflichtig (CHECK). Art, Bezug und
    due_date sind nach dem INSERT unveränderlich (Trigger) — sonst wären die
    Idempotenz-Indizes wertlos.

    IDEMPOTENZ: partielle UNIQUE-Indizes über (anker_id, due_date),
    **statusunabhängig**. Ein zweiter Scheduler-Lauf erzeugt keine Dublette, und
    ein VERWORFENER Eintrag kann nicht wieder auferstehen.
    """

    id = models.UUIDField(primary_key=True)
    kind = models.TextField()  # WARTUNG | PRUEFUNG | GEWAEHRLEISTUNG
    contract = models.ForeignKey(
        MaintenanceContract, models.DO_NOTHING, db_column="contract_id",
        null=True, blank=True, related_name="due_items",
    )
    inspection = models.ForeignKey(
        Inspection, models.DO_NOTHING, db_column="inspection_id",
        null=True, blank=True, related_name="due_items",
    )
    warranty = models.ForeignKey(
        Warranty, models.DO_NOTHING, db_column="warranty_id",
        null=True, blank=True, related_name="due_items",
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id",
        null=True, blank=True, related_name="due_items",
    )
    title = models.TextField()
    due_date = models.DateField()
    lead_time_days = models.IntegerField(db_default=models.Value(0))
    status = models.TextField(db_default="OFFEN")
    result_object_type = models.TextField(null=True, blank=True)
    result_object_id = models.UUIDField(null=True, blank=True)
    resolution_note = models.TextField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="resolved_by", null=True, blank=True,
        related_name="due_items_resolved",
    )
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by", null=True, blank=True,
        related_name="due_items_created",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'maintenance"."due_item'

    def __str__(self):
        return f"{self.kind} {self.due_date}: {self.title}"


class Article(models.Model):
    """pricing.article — Artikel/Material (Migration 0028, list_price aus 0033).

    Keine automatische Nummernvergabe (article_number ist app-/nutzergesetzt).
    Kein Löschen (No-Delete-Trigger) — nur status AKTIV/INAKTIV.
    """

    id = models.UUIDField(primary_key=True)
    article_number = models.TextField()
    description = models.TextField()
    long_description = models.TextField(null=True, blank=True)
    gtin = models.TextField(null=True, blank=True)
    manufacturer_name = models.TextField(null=True, blank=True)
    manufacturer_number = models.TextField(null=True, blank=True)
    unit = models.TextField()
    # MATERIAL|ARBEITSZEIT|PAUSCHALE|FREMDLEISTUNG|FAHRT|ZUSCHLAG
    line_type = models.TextField()
    product_group = models.TextField(null=True, blank=True)
    status = models.TextField()  # AKTIV | INAKTIV
    # Listenpreis je `price_unit` Mengeneinheiten, vier Nachkommastellen
    # (Migration 0039). Kann Basis einer Verkaufspreisgruppe sein
    # (calc_basis = LISTENPREIS), daher dieselbe Genauigkeit wie der EK.
    list_price = models.DecimalField(
        max_digits=15, decimal_places=4, null=True, blank=True
    )
    # Hero-Feldsatz (Migration 0042).
    matchcode = models.TextField(null=True, blank=True)
    manufacturer_type = models.TextField(null=True, blank=True)
    min_order_quantity = models.DecimalField(
        max_digits=15, decimal_places=3, null=True, blank=True
    )
    quantity_step = models.DecimalField(
        max_digits=15, decimal_places=3, null=True, blank=True
    )
    delivery_time_days = models.SmallIntegerField(null=True, blank=True)
    # Steuercode-Vorschlag für neue Belegpositionen (FK invoicing.tax_code.code).
    # attname = tax_code_id, db_column = 'tax_code' (wie bei quote_line).
    tax_code = models.ForeignKey(
        "TaxCode", models.DO_NOTHING, db_column="tax_code",
        null=True, blank=True, related_name="articles",
    )
    cost_center = models.ForeignKey(
        "CostCenter", models.DO_NOTHING, db_column="cost_center_id",
        null=True, blank=True, related_name="articles",
    )
    # Preiseinheit: list_price/EK gelten je `price_unit` Einheiten (1/10/100/1000).
    # Der je-Stück-Preis ergibt sich durch Division (Kalkulations-Service).
    price_unit = models.SmallIntegerField(db_default=models.Value(1))
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."article'

    def __str__(self):
        return f"{self.article_number} {self.description}"


class WageGroup(models.Model):
    """pricing.wage_group — Lohn-/Maschinengruppe (Migration 0033/0034)."""

    id = models.UUIDField(primary_key=True)
    name = models.TextField()
    kind = models.TextField()  # LOHN | MASCHINE
    hourly_rate = models.DecimalField(max_digits=12, decimal_places=2)
    cost_rate = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    status = models.TextField()  # AKTIV | INAKTIV
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."wage_group'

    def __str__(self):
        return self.name


class Assembly(models.Model):
    """pricing.assembly — Leistung (Stückliste aus Material + Lohn; Migration 0033)."""

    id = models.UUIDField(primary_key=True)
    assembly_number = models.TextField()
    name = models.TextField()
    internal_name = models.TextField(null=True, blank=True)
    unit = models.TextField()
    description = models.TextField(null=True, blank=True)
    status = models.TextField()  # AKTIV | INAKTIV
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."assembly'

    def __str__(self):
        return f"{self.assembly_number} {self.name}"


class AssemblyComponent(models.Model):
    """pricing.assembly_component — Position einer Leistung.

    Entweder Material (article_id + quantity) ODER Lohn (wage_group_id +
    minutes) — nie beides (DB-XOR-CHECK).
    """

    id = models.UUIDField(primary_key=True)
    assembly = models.ForeignKey(
        Assembly, models.DO_NOTHING, db_column="assembly_id", related_name="components"
    )
    position = models.IntegerField()
    article = models.ForeignKey(
        Article,
        models.DO_NOTHING,
        db_column="article_id",
        null=True,
        blank=True,
        related_name="assembly_components",
    )
    wage_group = models.ForeignKey(
        WageGroup,
        models.DO_NOTHING,
        db_column="wage_group_id",
        null=True,
        blank=True,
        related_name="assembly_components",
    )
    quantity = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    minutes = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True
    )
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."assembly_component'

    def __str__(self):
        return f"{self.position}. @ {self.assembly_id}"


class WorkOrder(models.Model):
    """workflow.work_order — Auftrag (Migration 0013).

    Reicher Statusautomat ENTWURF → FREIGEGEBEN → IN_PLANUNG → IN_AUSFUEHRUNG →
    TECHNISCH_ABGESCHLOSSEN → KAUFMAENNISCH_GEPRUEFT → ABGERECHNET (jederzeit
    STORNIERT). Übergänge validiert workflow.validate_status_change; die
    Freigabe-/Abrechnungs-Tore (Beauftragungsnachweis, bestätigter
    Verantwortungsbereich, PRINCIPAL, INVOICE_DEBTOR) prüft die DB als deferred
    Constraint-Trigger. Auftragsnummer (AU-…) vergibt die DB (db_default), erst
    veröffentlichte Rechnungen entstehen aus KAUFMAENNISCH_GEPRUEFT-Aufträgen
    (B-08).

    building_id/unit_id/asset_id sind zusammengesetzte FKs auf property; hier als
    reine UUIDs geführt (kein ORM-FK, da Composite-Ziel).
    """

    id = models.UUIDField(primary_key=True)
    order_number = models.TextField(db_default=WorkOrderNumberDefault())
    project = models.ForeignKey(
        Project,
        models.DO_NOTHING,
        db_column="project_id",
        null=True,
        blank=True,
        related_name="work_orders",
    )
    service_case = models.ForeignKey(
        ServiceCase,
        models.DO_NOTHING,
        db_column="service_case_id",
        null=True,
        blank=True,
        related_name="work_orders",
    )
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="work_orders"
    )
    building_id = models.UUIDField(null=True, blank=True)
    unit_id = models.UUIDField(null=True, blank=True)
    asset_id = models.UUIDField(null=True, blank=True)
    # UNKNOWN | COMMON_PROPERTY | PRIVATE_UNIT | MIXED
    responsibility_scope = models.TextField()
    # ENTWURF|FREIGABE_AUSSTEHEND|FREIGEGEBEN|IN_PLANUNG|IN_AUSFUEHRUNG|
    # TECHNISCH_ABGESCHLOSSEN|KAUFMAENNISCH_GEPRUEFT|ABGERECHNET|STORNIERT
    status = models.TextField()
    priority = models.TextField()  # FK priority_level.code (NORMAL|DRINGEND|NOTFALL)
    customer_reference = models.TextField(null=True, blank=True)
    order_evidence_reference = models.TextField(null=True, blank=True)
    authority_id = models.UUIDField(null=True, blank=True)
    is_emergency = models.BooleanField(db_default=models.Value(False))
    responsibility_confirmed_at = models.DateTimeField(null=True, blank=True)
    responsibility_confirmed_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="responsibility_confirmed_by",
        null=True,
        blank=True,
        related_name="confirmed_work_orders",
    )
    follow_up_of_work_order_id = models.UUIDField(null=True, blank=True)
    is_warranty_case = models.BooleanField(db_default=models.Value(False))
    ordered_at = models.DateTimeField(null=True, blank=True)
    desired_date = models.DateField(null=True, blank=True)
    # Abrechnungsart (Migration 0084): PAUSCHAL | REGIE.
    # PAUSCHAL (Default): Die Rechnung ist die ANGEBOTSKOPIE. Zeiten und
    # Berichtspositionen sind Nachweis, kein Rechnungsposten — das Angebot enthält
    # die Leistung bereits; beides zu fakturieren hieße doppelt kassieren. Das
    # Soll-Ist (0080) bleibt die interne Nachkalkulation.
    # REGIE: Die Rechnung entsteht aus Bericht + Zeiten (dem Ist).
    billing_mode = models.TextField(db_default=models.Value("PAUSCHAL"))
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."work_order'

    def __str__(self):
        return f"{self.order_number} {self.title}"


class WorkOrderParty(models.Model):
    """workflow.work_order_party — Beteiligte eines Auftrags (Migration 0013).

    Rollen A-25/A-27/A-29; höchstens ein primärer Beteiligter je Auftrag und
    Rolle (partieller UNIQUE-Index). Für die Freigabe-/Abrechnungs-Tore relevant:
    PRINCIPAL (Auftraggeber) und INVOICE_DEBTOR (Rechnungsschuldner).
    """

    id = models.UUIDField(primary_key=True)
    work_order = models.ForeignKey(
        WorkOrder, models.DO_NOTHING, db_column="work_order_id", related_name="parties"
    )
    party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="party_id", related_name="work_order_roles"
    )
    # PRINCIPAL|REPRESENTATIVE|SERVICE_RECIPIENT|OCCUPANT|COST_BEARER|
    # INVOICE_DEBTOR|INVOICE_RECIPIENT|REPORTER|ON_SITE_CONTACT
    role = models.TextField()
    # MANDATE|OWNERSHIP|OCCUPANCY|BILLING_INSTRUCTION|MANUAL
    source = models.TextField()
    source_reference_id = models.UUIDField(null=True, blank=True)
    resolved_at = models.DateTimeField(db_default=Now())
    is_primary = models.BooleanField(db_default=models.Value(False))
    allocation_percent = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."work_order_party'

    def __str__(self):
        return f"{self.role} @ {self.work_order_id}"


class ServiceJob(models.Model):
    """workflow.service_job — Einsatz/Termin (Migration 0014).

    Statusautomat UNGEPLANT → GEPLANT → BESTAETIGT → UNTERWEGS → VOR_ORT →
    (PAUSIERT ↔ VOR_ORT) → ABGESCHLOSSEN → NACHARBEIT; jederzeit AUSGEFALLEN
    (Sackgasse). Übergänge validiert workflow.validate_status_change; Ausführung
    ab UNTERWEGS setzt einen freigegebenen Auftrag voraus (DB-Gate). Einsatznummer
    (E-…) vergibt die DB über workflow.next_number (db_default). Kein physisches
    Löschen (Schutzstandard 0015); „Storno" = Status AUSGEFALLEN.

    Freier Termin (Migration 0062): work_order ist NULL-fähig — eine Begehung/
    Besichtigung/Beratung findet vor der Beauftragung statt. Dann ist `title`
    Pflicht (DB-CHECK) und `property` optional. Bei einem auftragsgebundenen
    Einsatz ist `title` optional (Fallback: Auftragstitel) und `property` muss,
    falls gesetzt, die Liegenschaft des Auftrags sein (zusammengesetzter FK).
    Der Auftragsbezug ist nach der Anlage unveränderlich (Trigger).
    """

    id = models.UUIDField(primary_key=True)
    job_number = models.TextField(db_default=ServiceJobNumberDefault())
    work_order = models.ForeignKey(
        WorkOrder,
        models.DO_NOTHING,
        db_column="work_order_id",
        null=True,
        blank=True,
        related_name="service_jobs",
    )
    # Pflicht beim freien Termin (ohne Auftrag), sonst optional.
    title = models.TextField(null=True, blank=True)
    property = models.ForeignKey(
        Property,
        models.DO_NOTHING,
        db_column="property_id",
        null=True,
        blank=True,
        related_name="service_jobs",
    )
    # UNGEPLANT|GEPLANT|BESTAETIGT|UNTERWEGS|VOR_ORT|PAUSIERT|ABGESCHLOSSEN|
    # NACHARBEIT|AUSGEFALLEN
    status = models.TextField()
    scheduled_start = models.DateTimeField(null=True, blank=True)
    scheduled_end = models.DateTimeField(null=True, blank=True)
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    on_site_contact_party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="on_site_contact_party_id",
        null=True,
        blank=True,
        related_name="on_site_service_jobs",
    )
    access_instructions = models.TextField(null=True, blank=True)
    completion_notes = models.TextField(null=True, blank=True)
    # Optionale Terminkategorie (Migration 0025) — steuert Kalender-/Plantafel-Farbe.
    appointment_category = models.ForeignKey(
        "AppointmentCategory",
        models.DO_NOTHING,
        db_column="appointment_category_id",
        null=True,
        blank=True,
        related_name="service_jobs",
    )
    # Serientermin (Migration 0077): reine HERKUNFTSKLAMMER — „diese Termine
    # wurden zusammen angelegt". Kein FK, keine Serientabelle: jedes Vorkommen ist
    # ein eigenständiger Einsatz mit eigenem Status, eigenen Zuweisungen und
    # eigenen Zeitbuchungen. Eine nachträglich änderbare Serienregel würde bereits
    # abgearbeitete Termine rückwirkend in Frage stellen.
    series_id = models.UUIDField(null=True, blank=True)
    # Taktgeber der Reihe: Beginn des ERSTEN Vorkommens, wie er beim Anlegen galt.
    # Jeder weitere Takt zählt daraus — deshalb kippt ein verschobenes oder
    # abgesagtes Vorkommen den Takt der Reihe NICHT, und der Monatstag bleibt
    # erhalten (der geklemmte 28.02. weiß nicht mehr, dass „der 31." gemeint war).
    # DB-CHECK: Anker und series_id gibt es nur gemeinsam.
    series_anchor = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."service_job'

    def __str__(self):
        return f"{self.job_number} ({self.status})"


class JobAssignment(models.Model):
    """workflow.job_assignment — Zuordnung Mitarbeiter ↔ Einsatz (Migration 0014).

    Höchstens ein Eintrag je (Einsatz, Mitarbeiter) (UNIQUE). Rolle TECHNICIAN
    (Standard) oder LEAD. Der Zugewiesene ist ein security.app_user (interne
    Person), nicht eine identity.party.
    """

    id = models.UUIDField(primary_key=True)
    service_job = models.ForeignKey(
        ServiceJob,
        models.DO_NOTHING,
        db_column="service_job_id",
        related_name="assignments",
    )
    assignee = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="assignee_user_id",
        related_name="job_assignments",
    )
    role = models.TextField(db_default=models.Value("TECHNICIAN"))  # TECHNICIAN|LEAD
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."job_assignment'

    def __str__(self):
        return f"{self.role} {self.assignee_id} @ {self.service_job_id}"


class TimeCategory(models.Model):
    """hr.time_category — Zeitkategorie (Migration 0066).

    Loest das harte `time_type`-Enum aus 0017 ab. `is_work_time` ist das einzige
    fachlich harte Attribut (ArbZG/MiLoG); alles andere ist Betriebssache.
    Systemkategorien (`is_system`, mit `code`) sind nicht archivierbar, und
    `PAUSE.is_work_time` ist nicht umschaltbar (Trigger).
    """

    id = models.UUIDField(primary_key=True)
    code = models.TextField(null=True, blank=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    is_work_time = models.BooleanField()
    is_system = models.BooleanField(db_default=False)
    status = models.TextField(db_default=models.Value("AKTIV"))  # AKTIV|ARCHIVIERT
    sort_order = models.IntegerField(db_default=models.Value(100))
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."time_category'

    def __str__(self):
        return self.name


class WorkDay(models.Model):
    """workflow.work_day — Tagesklammer der Zeiterfassung (Migration 0067).

    Der gesetzliche Kern: § 17 MiLoG verlangt Beginn, Ende und Dauer der
    TAEGLICHEN Arbeitszeit. Der Arbeitstag haengt an `security.app_user` (nicht
    an `hr.employee`) — Begruendung im Migrationskopf. Ein Zeiteintrag wird dem
    lokalen Kalendertag seines BEGINNS zugeordnet (Nachtschicht → Anfangstag).

    Statusautomat ENTWURF → EINGEREICHT → BESTAETIGT|ABGELEHNT; eine Aenderung
    an einem BESTAETIGTen Tag verlangt eine Begruendung und wirft ihn auf
    ENTWURF zurueck. Vier-Augen: `decided_by <> user_id` (Trigger).
    """

    id = models.UUIDField(primary_key=True)
    user = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="user_id", related_name="work_days"
    )
    day = models.DateField()
    status = models.TextField(db_default=models.Value("ENTWURF"))
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="decided_by",
        null=True,
        blank=True,
        related_name="decided_work_days",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."work_day'

    def __str__(self):
        return f"{self.day} ({self.status})"


class TimeEntry(models.Model):
    """workflow.time_entry — Zeiterfassung (Migration 0017, umgebaut in 0066/0067).

    * Kategorie statt Enum (0066): `category` → hr.time_category. `time_type`
      ist gedroppt; die gleichnamige **Property** liefert weiter den Code der
      Systemkategorie, damit bestehende Ausgabepfade unveraendert lesen koennen.
    * Einsatzbezug ist fuer JEDE Kategorie optional (Werkstatt-/Buero-/Fahrtzeit
      haengt an keinem Termin).
    * `ended_at IS NULL` = **laeuft gerade** (Stempeluhr). Genau eine laufende
      Buchung je Mitarbeiter (partieller UNIQUE-Index). Der EXCLUDE gegen
      Ueberlappung greift nur unter den ABGESCHLOSSENEN Buchungen
      (`WHERE ended_at IS NOT NULL`) — eine laufende Buchung hat noch kein Ende
      und darf keine spaeter geplante Zeit blockieren (0066).
    * `work_day` (0067) setzt ein DB-Trigger selbst — jeder Eintrag haengt am
      Arbeitstag seines Beginndatums.
    * Zwei unabhaengige Schloesser: B-28 (kaufmaennisch, 0017) und das
      Arbeitstag-Schloss (arbeitsrechtlich, 0067).
    """

    id = models.UUIDField(primary_key=True)
    service_job = models.ForeignKey(
        ServiceJob,
        models.DO_NOTHING,
        db_column="service_job_id",
        null=True,
        blank=True,
        related_name="time_entries",
    )
    user = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="user_id", related_name="time_entries"
    )
    category = models.ForeignKey(
        TimeCategory,
        models.DO_NOTHING,
        db_column="category_id",
        related_name="time_entries",
    )
    work_day = models.ForeignKey(
        WorkDay,
        models.DO_NOTHING,
        db_column="work_day_id",
        related_name="time_entries",
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    auto_generated = models.BooleanField(db_default=False)
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."time_entry'

    @property
    def time_type(self):
        """Rueckwaertskompatible Anzeige: Code der Systemkategorie, sonst Name.

        Bestandsausgaben (Einsatz-Mappe) sprechen weiter von „Zeitart"; die
        Property haelt sie am Leben, ohne eine zweite Klassifikation in der DB
        zu fuehren. **Kein Filterfeld** — ORM-Filter laufen ueber `category`.
        """
        cat = self.category
        return (cat.code or cat.name) if cat else None

    def __str__(self):
        return f"{self.time_type} {self.started_at:%Y-%m-%d}"


class MaterialEntry(models.Model):
    """workflow.material_entry — Materialverbrauch am Einsatz (Migration 0017).

    Reine Verbrauchserfassung (B-26: keine Bestandsführung). service_job ist
    Pflicht. Korrekturfenster B-28 setzt die DB durch.
    """

    id = models.UUIDField(primary_key=True)
    service_job = models.ForeignKey(
        ServiceJob,
        models.DO_NOTHING,
        db_column="service_job_id",
        related_name="material_entries",
    )
    description = models.TextField()
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    unit = models.TextField()
    note = models.TextField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="recorded_by",
        related_name="material_entries",
    )
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."material_entry'

    def __str__(self):
        return f"{self.description} {self.quantity} {self.unit}"


class AppointmentCategory(models.Model):
    """workflow.appointment_category — Terminkategorie (Migration 0025).

    Schlanke Codeliste am Einsatz: Name, optionale Beschreibung, Farbe als
    geschlossener Token (kein freier Hex; das UI mappt ihn WCAG-sicher) und
    Sortierung. Archivieren statt Löschen (Statusautomat AKTIV -> ARCHIVIERT,
    final). Schutzstandard (No-Delete/Audit/No-Truncate).
    """

    id = models.UUIDField(primary_key=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    # NAVY|ORANGE|SAGE|AMBER|TEAL|PLUM|ROSE|SLATE
    color_token = models.TextField(db_default=models.Value("NAVY"))
    status = models.TextField(db_default=models.Value("AKTIV"))  # AKTIV|ARCHIVIERT
    sort_order = models.IntegerField(db_default=models.Value(0))
    # Übliche Dauer dieses Termintyps in Minuten (Migration 0077). **Nur ein
    # VORSCHLAG** für den Termin-Dialog — der Server leitet daraus nie ein
    # `scheduled_end` ab, sonst überschriebe er die Entscheidung des Disponenten.
    default_duration_minutes = models.IntegerField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="created_by",
        related_name="created_appointment_categories",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."appointment_category'

    def __str__(self):
        return self.name


class Resource(models.Model):
    """resource.resource — planbares Betriebsmittel (Migration 0025).

    Fahrzeug/Gerät/Raum als eigenständige Stammdaten (neues Schema `resource`).
    Nummer RES-##### aus eigener Sequenz (kein Beleg). Statusautomat
    AKTIV<->INAKTIV->ARCHIVIERT (final). Schutzstandard.
    """

    id = models.UUIDField(primary_key=True)
    resource_number = models.TextField(db_default=ResourceNumberDefault())
    name = models.TextField()
    # FAHRZEUG|GERAET|RAUM|SONSTIGE
    resource_type = models.TextField()
    status = models.TextField(db_default=models.Value("AKTIV"))  # AKTIV|INAKTIV|ARCHIVIERT
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="created_by",
        related_name="created_resources",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'resource"."resource'

    def __str__(self):
        return f"{self.resource_number} {self.name}"


class JobResource(models.Model):
    """resource.job_resource — n:m Einsatz <-> Ressource (Migration 0025).

    Höchstens ein Eintrag je (Einsatz, Ressource) (UNIQUE). KEIN EXCLUDE gegen
    zeitliche Doppelbelegung (offene Invariante — der service_job-Zeitraum ist
    nullable und liegt in einer anderen Tabelle; siehe Migration 0025). Zeilen
    sind unveränderlich; Entfernen nur vor Einsatzabschluss.
    """

    id = models.UUIDField(primary_key=True)
    service_job = models.ForeignKey(
        ServiceJob,
        models.DO_NOTHING,
        db_column="service_job_id",
        related_name="resource_links",
    )
    resource = models.ForeignKey(
        Resource,
        models.DO_NOTHING,
        db_column="resource_id",
        related_name="job_links",
    )
    created_by = models.ForeignKey(
        AppUser,
        models.DO_NOTHING,
        db_column="created_by",
        related_name="created_job_resources",
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'resource"."job_resource'

    def __str__(self):
        return f"{self.resource_id} @ {self.service_job_id}"


class SalePriceGroup(models.Model):
    """pricing.sale_price_group — VK-Kalkulationsgruppe (Migration 0033).

    Der Verkaufspreis ist eine Formel: Basis (EK oder LISTENPREIS) mit Auf-/
    Abschlag, entweder prozentual (percent_change) ODER als Betrag (amount_change)
    — genau eines ist gesetzt (DB-CHECK). Kein Löschen (Schutzstandard).
    """

    id = models.UUIDField(primary_key=True)
    name = models.TextField()
    calc_basis = models.TextField()  # EK | LISTENPREIS
    operator = models.TextField()  # AUFSCHLAG | ABSCHLAG
    percent_change = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True
    )
    amount_change = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    status = models.TextField()  # AKTIV | INAKTIV
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."sale_price_group'

    def __str__(self):
        return self.name


class ArticleSalePrice(models.Model):
    """pricing.article_sale_price — VK-Variante eines Artikels (Migration 0033).

    Verweist auf eine sale_price_group (Formel) ODER trägt einen fixed_price
    (genau eines, DB-CHECK). Genau eine Variante je Artikel ist Standard
    (partieller Unique-Index).
    """

    id = models.UUIDField(primary_key=True)
    article = models.ForeignKey(
        Article, models.DO_NOTHING, db_column="article_id", related_name="sale_prices"
    )
    label = models.TextField()
    sale_price_group = models.ForeignKey(
        SalePriceGroup,
        models.DO_NOTHING,
        db_column="sale_price_group_id",
        null=True,
        blank=True,
        related_name="article_sale_prices",
    )
    fixed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    is_standard = models.BooleanField(db_default=models.Value(False))
    # MANUELL | MATRIX (Migration 0069): Herkunft eines gespeicherten fixed_price.
    # Die Massenpflege der Aufschlagsmatrix schreibt nur MATRIX-Zeilen fort und
    # fasst von Hand gesetzte Preise nie an.
    price_origin = models.TextField(db_default=models.Value("MANUELL"))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."article_sale_price'

    def __str__(self):
        return f"{self.label} @ {self.article_id}"


class MarkupRule(models.Model):
    """pricing.markup_rule — EK→VK-Aufschlagsmatrix (Migration 0069).

    Regel-Ebene UNTER der Artikelkalkulation: greift, wo ein Artikel keine eigene
    `article_sale_price`-Zeile hat. Geltungsbereich als Kaskade über nullbare
    Selektoren (Artikel > Warengruppe+Lieferant > Warengruppe > Lieferant >
    Standardregel = alles NULL); je Bereich höchstens eine AKTIVE Regel
    (partieller Unique-Index, NULLS NOT DISTINCT). Der Geltungsbereich ist nach
    dem INSERT unveränderlich (Trigger).
    """

    id = models.UUIDField(primary_key=True)
    name = models.TextField()
    article = models.ForeignKey(
        Article, models.DO_NOTHING, db_column="article_id",
        null=True, blank=True, related_name="markup_rules",
    )
    product_group = models.TextField(null=True, blank=True)
    supplier_party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="supplier_party_id",
        null=True, blank=True, related_name="markup_rules",
    )
    calc_basis = models.TextField()  # EK | LISTENPREIS
    # Vorzeichenbehaftet: negativ = Abschlag (> -100).
    markup_percent = models.DecimalField(max_digits=9, decimal_places=3)
    # Handelsspanne auf den VK: (VK-EK)/VK >= min_margin_percent/100.
    min_margin_percent = models.DecimalField(
        max_digits=9, decimal_places=3, null=True, blank=True
    )
    status = models.TextField()  # AKTIV | INAKTIV
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."markup_rule'

    def __str__(self):
        return self.name


class MarkupRuleTier(models.Model):
    """pricing.markup_rule_tier — Rabattstaffel einer Aufschlagsregel (0069).

    Ab `min_quantity` gilt `markup_percent`. Es zählt die höchste AKTIVE Stufe mit
    `min_quantity <= Menge`. Die Mindestmarge der Regel bleibt auch für Staffeln
    die Untergrenze. Kein Löschen — Stufen werden auf INAKTIV gesetzt.
    """

    id = models.UUIDField(primary_key=True)
    markup_rule = models.ForeignKey(
        MarkupRule, models.DO_NOTHING, db_column="markup_rule_id",
        related_name="tiers",
    )
    min_quantity = models.DecimalField(max_digits=15, decimal_places=3)
    markup_percent = models.DecimalField(max_digits=9, decimal_places=3)
    status = models.TextField()  # AKTIV | INAKTIV
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."markup_rule_tier'

    def __str__(self):
        return f"ab {self.min_quantity}: {self.markup_percent}%"


class ArticleSupplierReference(models.Model):
    """pricing.article_supplier_reference — Lieferantenbezug/EK eines Artikels
    (Migration 0028, price_unit_code aus 0039).

    Historisiert den letzten Einkaufspreis (last_purchase_price) je Lieferant/
    Quellsystem mit Gültigkeitszeitraum. Für die VK-Kalkulation auf EK-Basis ist
    der aktuell gültige Datensatz maßgeblich. Kein Löschen — Referenzen werden
    über valid_until beendet.
    """

    id = models.UUIDField(primary_key=True)
    article = models.ForeignKey(
        Article,
        models.DO_NOTHING,
        db_column="article_id",
        related_name="supplier_references",
    )
    supplier_party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="supplier_party_id",
        related_name="supplied_articles",
    )
    source_system = models.TextField()
    source_namespace = models.TextField()
    supplier_article_number = models.TextField()
    # Vier Nachkommastellen (Migration 0038): bei DATANORM-Preiseinheit 100/1000
    # liegen echte Stückpreise unter einem Cent (Stahlhaften: 0,0774 €/Stück).
    # NULL heißt „unbekannt", nie 0.
    last_purchase_price = models.DecimalField(
        max_digits=15, decimal_places=4, null=True, blank=True
    )
    # Händler-Listenpreis je EINER Mengeneinheit (DATANORM-Preiskennzeichen 1).
    list_price = models.DecimalField(
        max_digits=15, decimal_places=4, null=True, blank=True
    )
    currency = models.CharField(max_length=3, null=True, blank=True)
    discount_group = models.TextField(null=True, blank=True)
    last_imported_at = models.DateTimeField(null=True, blank=True)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    # DATANORM-Preiseinheit: 0 = je 1, 1 = je 10, 2 = je 100, 3 = je 1000.
    # Der gespeicherte Preis gilt IMMER je einer Mengeneinheit; dieses Feld hält
    # nur fest, wie er aus der Quelldatei hergeleitet wurde.
    price_unit_code = models.SmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."article_supplier_reference'

    def __str__(self):
        return f"{self.supplier_article_number} @ {self.article_id}"


# ---------------------------------------------------------------------------
# hr.* — Personalstamm, Arbeitsvertrag, Abwesenheit, Urlaubskonto (0019)
# ---------------------------------------------------------------------------


class Employee(models.Model):
    """hr.employee — Beschäftigungsverhältnis zu einer natürlichen Person.

    Trägt selbst keine Personendaten: Name/Adresse hängen an identity.person,
    der Login an security.app_user. Status AUSGETRETEN ist final (Trigger).
    """

    id = models.UUIDField(primary_key=True)
    employee_number = models.TextField(db_default=EmployeeNumberDefault())
    app_user = models.OneToOneField(
        "AppUser",
        models.DO_NOTHING,
        db_column="app_user_id",
        related_name="employee",
    )
    party = models.OneToOneField(
        Person,
        models.DO_NOTHING,
        db_column="party_id",
        related_name="employee",
    )
    wage_group = models.ForeignKey(
        "WageGroup",
        models.DO_NOTHING,
        db_column="wage_group_id",
        null=True,
        blank=True,
        related_name="employees",
    )
    status = models.TextField()  # AKTIV | INAKTIV | AUSGETRETEN
    hired_on = models.DateField()
    left_on = models.DateField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        "AppUser",
        models.DO_NOTHING,
        db_column="created_by",
        related_name="created_employees",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."employee'

    def __str__(self):
        return self.employee_number


class EmploymentContract(models.Model):
    """hr.employment_contract — versionierter Arbeitsvertrag.

    Beginn, Sollstunden-Raster, Urlaubsanspruch und Lohngruppe sind nach dem
    INSERT unveränderlich (Trigger hr.enforce_contract_immutable); eine
    Änderung erzeugt einen Folgevertrag. Verträge eines Mitarbeiters dürfen
    sich zeitlich nicht überlappen (EXCLUDE-Constraint).
    """

    id = models.UUIDField(primary_key=True)
    employee = models.ForeignKey(
        Employee,
        models.DO_NOTHING,
        db_column="employee_id",
        related_name="contracts",
    )
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    hours_monday = models.DecimalField(max_digits=4, decimal_places=2)
    hours_tuesday = models.DecimalField(max_digits=4, decimal_places=2)
    hours_wednesday = models.DecimalField(max_digits=4, decimal_places=2)
    hours_thursday = models.DecimalField(max_digits=4, decimal_places=2)
    hours_friday = models.DecimalField(max_digits=4, decimal_places=2)
    hours_saturday = models.DecimalField(max_digits=4, decimal_places=2)
    hours_sunday = models.DecimalField(max_digits=4, decimal_places=2)
    vacation_days_per_year = models.DecimalField(max_digits=5, decimal_places=2)
    wage_group = models.ForeignKey(
        "WageGroup",
        models.DO_NOTHING,
        db_column="wage_group_id",
        null=True,
        blank=True,
        related_name="contracts",
    )
    status = models.TextField()  # AKTIV | GEKUENDIGT
    termination_reason = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        "AppUser",
        models.DO_NOTHING,
        db_column="created_by",
        related_name="created_contracts",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."employment_contract'

    def __str__(self):
        return f"Vertrag ab {self.valid_from}"


class Absence(models.Model):
    """hr.absence — Abwesenheitsantrag mit Statusautomat.

    days_count sind die angerechneten Arbeitstage; sie werden vom Service aus
    dem Sollstunden-Raster des gültigen Vertrags berechnet, nicht vom Client
    geliefert.
    """

    id = models.UUIDField(primary_key=True)
    employee = models.ForeignKey(
        Employee,
        models.DO_NOTHING,
        db_column="employee_id",
        related_name="absences",
    )
    # URLAUB | KRANKHEIT | ELTERNZEIT | SONDERURLAUB | UNBEZAHLT | FORTBILDUNG
    absence_type = models.TextField()
    start_date = models.DateField()
    end_date = models.DateField()
    half_day_start = models.BooleanField(db_default=models.Value(False))
    half_day_end = models.BooleanField(db_default=models.Value(False))
    days_count = models.DecimalField(max_digits=5, decimal_places=2)
    # ENTWURF | EINGEREICHT | GENEHMIGT | ABGELEHNT | ZURUECKGEZOGEN
    status = models.TextField()
    reason = models.TextField(null=True, blank=True)
    decided_by = models.ForeignKey(
        "AppUser",
        models.DO_NOTHING,
        db_column="decided_by",
        null=True,
        blank=True,
        related_name="decided_absences",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        "AppUser",
        models.DO_NOTHING,
        db_column="created_by",
        related_name="created_absences",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."absence'

    def __str__(self):
        return f"{self.absence_type} {self.start_date}–{self.end_date}"


class VacationBudget(models.Model):
    """hr.vacation_budget — Urlaubskonto je Mitarbeiter und Jahr.

    Der Verbrauch ist bewusst NICHT gespeichert, sondern wird aus genehmigten
    URLAUB-Abwesenheiten des Jahres abgeleitet (gleiche Konvention wie der
    offene Betrag in der Buchhaltung).
    """

    id = models.UUIDField(primary_key=True)
    employee = models.ForeignKey(
        Employee,
        models.DO_NOTHING,
        db_column="employee_id",
        related_name="vacation_budgets",
    )
    year = models.IntegerField()
    entitlement_days = models.DecimalField(max_digits=5, decimal_places=2)
    carryover_days = models.DecimalField(
        max_digits=5, decimal_places=2, db_default=models.Value(0)
    )
    adjustment_days = models.DecimalField(
        max_digits=5, decimal_places=2, db_default=models.Value(0)
    )
    adjustment_reason = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        "AppUser",
        models.DO_NOTHING,
        db_column="created_by",
        related_name="created_vacation_budgets",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."vacation_budget'

    def __str__(self):
        return f"Urlaubskonto {self.year} ({self.employee_id})"


class TimeAdjustment(models.Model):
    """hr.time_adjustment — Ausgleichsbuchung auf dem Arbeitszeitkonto (0072).

    Der Saldo bleibt **abgeleitet**: `Saldo = Ist − Soll + Σ Ausgleich`. Diese
    Zeile ist die dritte Größe der Formel, nicht ein gespeicherter Saldo.

    `minutes` ist vorzeichenbehaftet und in **Minuten** (exakt; 20 min sind in
    einer Stunden-Dezimalspalte nicht darstellbar): positiv = Gutschrift aufs
    Konto, negativ = Belastung. Append-only: eine Fehlbuchung wird **storniert**
    (Storno-Zeile mit `reversal_of` + negierten Minuten, Ursprung → STORNIERT),
    nie gelöscht oder umgeschrieben (Trigger).

    In die Summe gehen nur Zeilen mit `status='GEBUCHT' AND reversal_of IS NULL`.
    """

    id = models.UUIDField(primary_key=True)
    employee = models.ForeignKey(
        Employee,
        models.DO_NOTHING,
        db_column="employee_id",
        related_name="time_adjustments",
    )
    # EINBEHALT | AUSZAHLUNG | FREIZEITAUSGLEICH | KORREKTUR
    adjustment_type = models.TextField()
    effective_on = models.DateField()
    minutes = models.IntegerField()
    reason = models.TextField()
    status = models.TextField(db_default=models.Value("GEBUCHT"))  # GEBUCHT|STORNIERT
    reversal_of = models.ForeignKey(
        "self",
        models.DO_NOTHING,
        db_column="reversal_of_id",
        null=True,
        blank=True,
        related_name="reversals",
    )
    created_by = models.ForeignKey(
        "AppUser",
        models.DO_NOTHING,
        db_column="created_by",
        related_name="created_time_adjustments",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."time_adjustment'

    def __str__(self):
        return f"{self.adjustment_type} {self.minutes} min ({self.effective_on})"


class BreakRule(models.Model):
    """hr.break_rule — Pausenregel des Betriebs, Singleton (Migration 0068).

    KEINE | GESETZLICH (ArbZG § 4: >6 h → 30 min, >9 h → 45 min) | FESTE_ZEITEN
    (Fenster in `fixed_breaks`: [{"von": "12:00", "bis": "12:30"}, …]).
    """

    id = models.UUIDField(primary_key=True)
    is_singleton = models.BooleanField(db_default=True)
    mode = models.TextField(db_default=models.Value("GESETZLICH"))
    fixed_breaks = models.JSONField(db_default=models.Value("[]"))
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."break_rule'

    def __str__(self):
        return f"Pausenregel {self.mode}"


class Holiday(models.Model):
    """hr.holiday — Feiertag (Migration 0068). `region` NULL = bundesweit."""

    id = models.UUIDField(primary_key=True)
    day = models.DateField()
    name = models.TextField()
    region = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."holiday'

    def __str__(self):
        return f"{self.day} {self.name}"


# ---------------------------------------------------------------------------
# security.* — Rollen und Rechtematrix (db/migrations/0026, hr-Modul 0021)
# ---------------------------------------------------------------------------


class Role(models.Model):
    """security.role — Codeliste der Rollen (PK ist der Code, nicht eine UUID)."""

    code = models.TextField(primary_key=True)
    label = models.TextField()
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'security"."role'

    def __str__(self):
        return self.code


class UserRole(models.Model):
    """security.user_role — zeitabhängige Rollenzuordnung.

    Zuordnungen werden beendet (`valid_until`), nicht gelöscht. Ein EXCLUDE
    verhindert zeitgleiche Doppelzuordnung derselben Rolle.
    """

    id = models.UUIDField(primary_key=True)
    user = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="user_id", related_name="user_roles"
    )
    role = models.ForeignKey(
        Role, models.DO_NOTHING, db_column="role_code", related_name="user_roles"
    )
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    granted_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="granted_by", related_name="granted_roles"
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'security"."user_role'

    def __str__(self):
        return f"{self.user_id} → {self.role_id}"


class RolePermission(models.Model):
    """security.role_permission — Rechtematrix (Rolle × Modul × Aktion).

    `row_scope` ('ALLE'|'EIGENE') ist ein Kennzeichen; die Auswertung erfolgt
    ausdrücklich in der App-Schicht (die Anwendung verbindet sich als
    technischer DB-Benutzer).
    """

    id = models.UUIDField(primary_key=True)
    role = models.ForeignKey(
        Role, models.DO_NOTHING, db_column="role_code", related_name="permissions"
    )
    module = models.TextField()
    action = models.TextField()
    allowed = models.BooleanField()
    row_scope = models.TextField()  # ALLE | EIGENE
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'security"."role_permission'

    def __str__(self):
        return f"{self.role_id}/{self.module}/{self.action}={self.allowed}"


class FourEyesAction(models.Model):
    """security.four_eyes_action — Vier-Augen-pflichtige Aktionen (Migration 0026 db/).

    Codeliste (BANKDATEN, RECHNUNGSKORREKTUR, …); PK ist der Code. Nur-Lese-Nutzung
    im Backend (Auswahl gültiger Aktionen); Pflege per Migration.
    """

    action_code = models.TextField(primary_key=True)
    label = models.TextField()
    active = models.BooleanField(db_default=True)
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'security"."four_eyes_action'

    def __str__(self):
        return self.action_code


class ApprovalRequest(models.Model):
    """security.approval_request — Vier-Augen-Antrag/Freigabe (Migration 0028 db_core).

    Statusautomat ANGEFORDERT -> GENEHMIGT | ABGELEHNT | ZURUECKGEZOGEN (Trigger).
    Kernregel physisch: `decided_by <> requested_by` (CHECK). `applied_at` markiert
    den Einmal-Verbrauch einer erteilten Genehmigung.
    """

    id = models.UUIDField(primary_key=True)
    action = models.ForeignKey(
        FourEyesAction,
        models.DO_NOTHING,
        db_column="action_code",
        related_name="approval_requests",
    )
    status = models.TextField()  # ANGEFORDERT | GENEHMIGT | ABGELEHNT | ZURUECKGEZOGEN
    payload = models.JSONField(db_default={})
    target_table = models.TextField(null=True, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    requested_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="requested_by",
        related_name="approval_requests_made",
    )
    requested_at = models.DateTimeField(db_default=Now())
    decided_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="decided_by", null=True, blank=True,
        related_name="approval_requests_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'security"."approval_request'

    def __str__(self):
        return f"{self.action_id}/{self.status}"


class CompanyProfile(models.Model):
    """company.company_profile — Firmenprofil (Singleton, Migration 0023 db_core).

    Genau eine Zeile (Singleton-Garantie über `is_singleton`: UNIQUE +
    CHECK(is_singleton)). Trägt die Stammdaten des ausstellenden Unternehmens
    (Identität, Anschrift, Kontakt, Steuer/Register, Firmen-Bankverbindung,
    Geschäftsführung). Ersetzt den Aussteller-Platzhalter im Beleg-PDF. Änderung
    auditiert (Trigger); kein Löschen.
    """

    id = models.UUIDField(primary_key=True)
    is_singleton = models.BooleanField(db_default=True)
    company_name = models.TextField()
    legal_form = models.TextField(null=True, blank=True)
    street = models.TextField(null=True, blank=True)
    postal_code = models.TextField(null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    country = models.CharField(max_length=2, db_default="DE")
    state_code = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    web = models.TextField(null=True, blank=True)
    tax_number = models.TextField(null=True, blank=True)
    vat_id = models.TextField(null=True, blank=True)
    commercial_register = models.TextField(null=True, blank=True)
    bank_name = models.TextField(null=True, blank=True)
    iban = models.TextField(null=True, blank=True)
    bic = models.TextField(null=True, blank=True)
    managing_director = models.TextField(null=True, blank=True)
    managing_director_title = models.TextField(null=True, blank=True)
    default_language = models.CharField(max_length=2, db_default="de")
    logo_file_id = models.UUIDField(null=True, blank=True)
    # DATEV-Export-Konfiguration (Migration 0051): Mandanten-Identität beim
    # Steuerberater + optionale Konto-Overrides (NULL = SKR-Standard aus dem Service).
    datev_consultant_number = models.TextField(null=True, blank=True)
    datev_client_number = models.TextField(null=True, blank=True)
    datev_chart_of_accounts = models.TextField(null=True, blank=True)
    datev_account_length = models.SmallIntegerField(null=True, blank=True)
    datev_fiscal_year_start_month = models.SmallIntegerField(null=True, blank=True)
    datev_debtor_account = models.TextField(null=True, blank=True)
    datev_revenue_account_full = models.TextField(null=True, blank=True)
    datev_revenue_account_reduced = models.TextField(null=True, blank=True)
    datev_revenue_account_free = models.TextField(null=True, blank=True)
    datev_revenue_account_reverse = models.TextField(null=True, blank=True)
    # Abschlags-Kontierung (Migration 0063): 'ERLOES' (Teilleistung, Default und
    # Bestandsverhalten) oder 'ANZAHLUNG' (Vorauszahlung → Verbindlichkeitskonto
    # „Erhaltene, versteuerte Anzahlungen", von der Schlussrechnung aufgelöst).
    datev_advance_mode = models.TextField(db_default="ERLOES")
    datev_advance_account_full = models.TextField(null=True, blank=True)
    datev_advance_account_reduced = models.TextField(null=True, blank=True)
    datev_advance_account_free = models.TextField(null=True, blank=True)
    datev_advance_account_reverse = models.TextField(null=True, blank=True)
    # Gewährleistung (Migration 0071): Voreinstellung für neue Gewährleistungen.
    # Je Auftrag überschreibbar — eine betriebliche Einstellung, keine Rechtsauskunft.
    warranty_default_months = models.IntegerField(db_default=models.Value(60))
    warranty_default_lead_days = models.IntegerField(db_default=models.Value(90))
    # Verfall des Resturlaubs-Übertrags im Folgejahr (Migration 0072).
    # NULL/NULL = KEIN Verfall (Default). Nur was der Betrieb ausdrücklich
    # einstellt, wird weggerechnet — § 7 Abs. 3 BUrlG ist eine Möglichkeit,
    # keine Automatik, und BAG/EuGH knüpfen den Verfall an die Hinweis- und
    # Aufforderungsobliegenheit des Arbeitgebers.
    vacation_carryover_expiry_month = models.SmallIntegerField(null=True, blank=True)
    vacation_carryover_expiry_day = models.SmallIntegerField(null=True, blank=True)
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'company"."company_profile'

    def __str__(self):
        return self.company_name


class Branch(models.Model):
    """company.branch — Niederlassung (Migration 0023 db_core).

    Deaktivieren statt Löschen (`active`); Änderung auditiert, kein Löschen.
    """

    id = models.UUIDField(primary_key=True)
    name = models.TextField()
    street = models.TextField(null=True, blank=True)
    postal_code = models.TextField(null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    country = models.CharField(max_length=2, db_default="DE")
    phone = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    active = models.BooleanField(db_default=True)
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'company"."branch'

    def __str__(self):
        return self.name


class Trade(models.Model):
    """company.trade — Gewerk-Katalog (erste echte Gewerk-Wahrheit, 0023 db_core).

    Bewusst NICHT an workflow.project_category (Projektkategorie) gekoppelt —
    ein Gewerk ist fachlich etwas anderes als ein Projekttyp. Deaktivieren statt
    Löschen; Änderung auditiert.
    """

    id = models.UUIDField(primary_key=True)
    code = models.TextField(unique=True)
    label = models.TextField()
    active = models.BooleanField(db_default=True)
    sort_order = models.IntegerField(db_default=models.Value(0))
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'company"."trade'

    def __str__(self):
        return f"{self.code} — {self.label}"


class AcquisitionSource(models.Model):
    """company.acquisition_source — Akquisekanal/Quelle (0049 db_core).

    „Wie ist der Kunde auf uns gekommen?" (Empfehlung/Website/Messe …). Wird von
    identity.party referenziert. Deaktivieren statt Löschen; Änderung auditiert.
    """

    id = models.UUIDField(primary_key=True)
    code = models.TextField(unique=True)
    label = models.TextField()
    active = models.BooleanField(db_default=True)
    sort_order = models.IntegerField(db_default=models.Value(0))
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'company"."acquisition_source'

    def __str__(self):
        return f"{self.code} — {self.label}"


class MailAccount(models.Model):
    """company.mail_account — firmenweites SMTP-Absenderkonto (0046 db_core).

    Trägt die Zugangsdaten für den Mailversand. Das Passwort liegt ausschließlich
    als Fernet-Chiffre in `password_encrypted` (bytea); der Klartext wird NIE
    gespeichert, NIE über die API zurückgegeben und NIE geloggt (siehe
    services/mail.py, mail_crypto.py). Höchstens ein aktives Konto (partieller
    Unique-Index `(active) WHERE active`). Deaktivieren statt Löschen; Änderung
    auditiert.
    """

    id = models.UUIDField(primary_key=True)
    label = models.TextField()
    host = models.TextField()
    port = models.IntegerField()
    security = models.TextField()
    username = models.TextField(null=True, blank=True)
    password_encrypted = models.BinaryField(null=True, blank=True)
    from_address = models.TextField()
    from_name = models.TextField(null=True, blank=True)
    active = models.BooleanField(db_default=True)
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'company"."mail_account'

    def __str__(self):
        return f"{self.label} ({self.from_address})"


class Communication(models.Model):
    """content.communication — protokollierte Kommunikation (Migration 0023 db/).

    Audit-Senke u. a. für gesendete Mails: `channel='EMAIL'`,
    `direction='AUSGEHEND'` (die DB-CHECK-Werte sind deutsch — EINGEHEND/
    AUSGEHEND/INTERN, NICHT OUTBOUND/INBOUND). Gesendete Mails landen zunächst im
    Klärungskorb (`assignment_status='KLAERUNGSKORB'`, default) ohne Verknüpfung —
    das ist der einzige Zustand, den der DB-Trigger ohne communication_link
    zulässt. Die Beleg-/Vorgangs-Zuordnung ist ein späterer Slice.
    """

    id = models.UUIDField(primary_key=True)
    channel = models.TextField()
    direction = models.TextField()
    subject = models.TextField(null=True, blank=True)
    body = models.TextField(null=True, blank=True)
    counterpart_party_id = models.UUIDField(null=True, blank=True)
    counterpart_raw = models.TextField(null=True, blank=True)
    occurred_at = models.DateTimeField(db_default=Now())
    recorded_by = models.UUIDField()
    is_internal = models.BooleanField(db_default=False)
    is_commercial = models.BooleanField(db_default=False)
    assignment_status = models.TextField(db_default=models.Value("KLAERUNGSKORB"))
    assignment_source = models.TextField(null=True, blank=True)
    assignment_confirmed_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'content"."communication'


# ---------------------------------------------------------------------------
# accounting.* — Buchungskonten, Kostenstellen und Eingangsbelege (0030/0031)
# ---------------------------------------------------------------------------

class LedgerAccount(models.Model):
    """accounting.ledger_account — Buchungskonto (Migration 0030).

    Stammdaten zur Kontierung von Eingangsbelegen. Kein Kontenrahmen geseedet
    (die Roadmap nennt SKR03/SKR04 nur als wählbaren Rahmen, ohne konkreten
    Kontenplan). `account_type` ist die buchhalterische Grundklassifikation
    (AKTIV/PASSIV/AUFWAND/ERTRAG). Archivieren über `active`, kein Löschen.
    """

    id = models.UUIDField(primary_key=True)
    account_number = models.TextField()
    label = models.TextField()
    account_type = models.TextField()  # AKTIV | PASSIV | AUFWAND | ERTRAG
    chart_of_accounts = models.TextField(null=True, blank=True)  # SKR03 | SKR04
    active = models.BooleanField(db_default=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_ledger_accounts",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'accounting"."ledger_account'

    def __str__(self):
        return f"{self.account_number} — {self.label}"


class CostCenter(models.Model):
    """accounting.cost_center — Kostenstelle (Migration 0030).

    Freistehende Stammdaten; die Zuordnung erfolgt je Beleg-Position
    (receipt_line.cost_center). Archivieren über `active`, kein Löschen.
    """

    id = models.UUIDField(primary_key=True)
    code = models.TextField(unique=True)
    label = models.TextField()
    active = models.BooleanField(db_default=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_cost_centers",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'accounting"."cost_center'

    def __str__(self):
        return f"{self.code} — {self.label}"


class Receipt(models.Model):
    """accounting.receipt — Eingangsbeleg/Eingangsrechnung (Migration 0031).

    Eigene Tabelle (nicht invoicing.invoice): Lieferant (Pflicht), eigene
    Erfassungsnummer (EB-…, eigene Sequenz, KEIN Ausgangsbelegkreis), eigener
    Statusautomat ERFASST→GEPRUEFT→FREIGEGEBEN→GEBUCHT (+ABGELEHNT). Beträge
    werden serverseitig aus den Positionen gerechnet. Kein Löschen (GoBD).
    """

    id = models.UUIDField(primary_key=True)
    # Nummernvergabe bleibt in der DB-Sequenz; siehe ReceiptNumberDefault.
    receipt_number = models.TextField(db_default=ReceiptNumberDefault())
    supplier_party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="supplier_party_id",
        related_name="supplier_receipts",
    )
    supplier_invoice_number = models.TextField(null=True, blank=True)
    receipt_date = models.DateField()
    received_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, db_default="EUR")
    net_total = models.DecimalField(max_digits=15, decimal_places=2, db_default=0)
    tax_total = models.DecimalField(max_digits=15, decimal_places=2, db_default=0)
    gross_total = models.DecimalField(max_digits=15, decimal_places=2, db_default=0)
    # ERFASST | GEPRUEFT | FREIGEGEBEN | GEBUCHT | ABGELEHNT
    status = models.TextField()
    rejection_reason = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_receipts",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'accounting"."receipt'

    def __str__(self):
        return f"{self.receipt_number} ({self.status})"


class ReceiptLine(models.Model):
    """accounting.receipt_line — Eingangsbeleg-Position (Migration 0031).

    Steuersatz per FK auf die bestehende invoicing.tax_code (keine zweite Liste).
    Kontierung: Buchungskonto und Kostenstelle je Position. net_amount =
    round(quantity * unit_price, 2) (DB-CHECK). Nur in ERFASST/GEPRUEFT
    veränderbar (Trigger).
    """

    id = models.UUIDField(primary_key=True)
    receipt = models.ForeignKey(
        Receipt, models.DO_NOTHING, db_column="receipt_id", related_name="lines"
    )
    position_number = models.IntegerField()
    description = models.TextField()
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    unit = models.TextField(null=True, blank=True)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)
    tax_code = models.ForeignKey(
        TaxCode, models.DO_NOTHING, db_column="tax_code",
        related_name="receipt_lines",
    )
    tax_rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    net_amount = models.DecimalField(max_digits=15, decimal_places=2)
    ledger_account = models.ForeignKey(
        LedgerAccount, models.DO_NOTHING, db_column="ledger_account_id",
        null=True, blank=True, related_name="receipt_lines",
    )
    cost_center = models.ForeignKey(
        CostCenter, models.DO_NOTHING, db_column="cost_center_id",
        null=True, blank=True, related_name="receipt_lines",
    )
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'accounting"."receipt_line'

    def __str__(self):
        return f"{self.position_number}. {self.description}"


# ---------------------------------------------------------------------------
# content.* — Dateien und ihre Verknüpfungen (Migration 0021, 0035)
# ---------------------------------------------------------------------------

class File(models.Model):
    """content.file — eine hochgeladene Datei (Migration 0021).

    Physisch unveränderlich: `trg_file_immutable` verbietet UPDATE und DELETE.
    Eine Korrektur ist eine neue Datei, kein Überschreiben. Der Inhalt liegt im
    Objektspeicher unter `storage_key`; die Zeile hier ist nur der Nachweis.

    `sha256` erlaubt es, denselben Inhalt wiederzuerkennen, ohne ihn erneut zu
    übertragen.
    """

    id = models.UUIDField(primary_key=True)
    storage_key = models.TextField(unique=True)
    original_filename = models.TextField()
    mime_type = models.TextField()
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    media_metadata = models.JSONField(default=dict)
    uploaded_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="uploaded_by", related_name="files"
    )
    uploaded_at = models.DateTimeField(db_default=Now())
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'content"."file'

    def __str__(self):
        return self.original_filename


class FileLink(models.Model):
    """content.file_link — hängt eine Datei an GENAU EIN Zielobjekt.

    Der DB-CHECK `num_nonnulls(...) = 1` erzwingt das: eine Datei kann an einem
    Projekt ODER einer Liegenschaft ODER einem Kontakt hängen, nie an zweien.
    Wer sie an mehreren Orten braucht, legt mehrere Verknüpfungen auf dieselbe
    `file_id` an — der Inhalt existiert dann trotzdem nur einmal.

    `asset_id` und `communication_id` haben in der DB einen Fremdschlüssel, aber
    hier kein Model (technical_asset/communication sind nicht abgebildet). Sie
    bleiben rohe UUID-Felder; die DB prüft trotzdem.
    """

    id = models.UUIDField(primary_key=True)
    file = models.ForeignKey(
        File, models.DO_NOTHING, db_column="file_id", related_name="links"
    )
    project = models.ForeignKey(
        Project, models.DO_NOTHING, db_column="project_id",
        null=True, blank=True, related_name="file_links",
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id",
        null=True, blank=True, related_name="file_links",
    )
    unit = models.ForeignKey(
        Unit, models.DO_NOTHING, db_column="unit_id",
        null=True, blank=True, related_name="file_links",
    )
    party = models.ForeignKey(
        "Party", models.DO_NOTHING, db_column="party_id",
        null=True, blank=True, related_name="file_links",
    )
    service_case = models.ForeignKey(
        ServiceCase, models.DO_NOTHING, db_column="service_case_id",
        null=True, blank=True, related_name="file_links",
    )
    work_order = models.ForeignKey(
        "WorkOrder", models.DO_NOTHING, db_column="work_order_id",
        null=True, blank=True, related_name="file_links",
    )
    service_job = models.ForeignKey(
        "ServiceJob", models.DO_NOTHING, db_column="service_job_id",
        null=True, blank=True, related_name="file_links",
    )
    quote = models.ForeignKey(
        "Quote", models.DO_NOTHING, db_column="quote_id",
        null=True, blank=True, related_name="file_links",
    )
    invoice = models.ForeignKey(
        "Invoice", models.DO_NOTHING, db_column="invoice_id",
        null=True, blank=True, related_name="file_links",
    )
    article = models.ForeignKey(
        "Article", models.DO_NOTHING, db_column="article_id",
        null=True, blank=True, related_name="file_links",
    )
    site_report = models.ForeignKey(
        "SiteReport", models.DO_NOTHING, db_column="site_report_id",
        null=True, blank=True, related_name="file_links",
    )
    # Attest (Arbeitsunfähigkeitsbescheinigung) an einer Abwesenheit, Migration
    # 0072. Gesundheitsdatum — besondere Kategorie nach DSGVO Art. 9. Der Zugriff
    # hängt NICHT am content-Recht allein: siehe Ziel-Guard in api/dateien.py.
    absence = models.ForeignKey(
        "Absence", models.DO_NOTHING, db_column="absence_id",
        null=True, blank=True, related_name="file_links",
    )
    asset_id = models.UUIDField(null=True, blank=True)
    communication_id = models.UUIDField(null=True, blank=True)
    link_category = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by", related_name="file_links"
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'content"."file_link'

    def __str__(self):
        return f"{self.file_id} -> {self.link_category or 'ohne Kategorie'}"


class SiteReport(models.Model):
    """workflow.site_report — Baustellenbericht (Migration 0054/0064).

    Tätigkeitsnachweis vor Ort. Fotos hängen als content.file_link
    (site_report_id) daran; die Kundenunterschrift (`signature_file_id`, PNG im
    Objektspeicher) besiegelt den Bericht (ENTWURF → UNTERZEICHNET). Ein
    unterzeichneter Bericht ist unveränderlich (Trigger `protect_site_report`);
    kein Löschen (GoBD-Schutzstandard).

    Anker (Migration 0064): Auftrag ODER Einsatz — mindestens eins von beiden
    (DB-CHECK). `work_order` ist NULL beim **Begehungsprotokoll am freien
    Termin** (Einsatz ohne Auftrag, Migration 0062). Ist ein Einsatz gesetzt,
    muss der Auftrag des Berichts der Auftrag dieses Einsatzes sein — auch, wenn
    das „kein Auftrag" bedeutet (Trigger `check_site_report_anchor`). Die
    Liegenschaft trägt der Bericht bewusst nicht selbst; sie kommt vom Anker.
    """

    id = models.UUIDField(primary_key=True)
    work_order = models.ForeignKey(
        "WorkOrder", models.DO_NOTHING, db_column="work_order_id",
        null=True, blank=True, related_name="site_reports",
    )
    service_job = models.ForeignKey(
        "ServiceJob", models.DO_NOTHING, db_column="service_job_id",
        null=True, blank=True, related_name="site_reports",
    )
    report_date = models.DateField()
    author = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="author_id",
        related_name="site_reports",
    )
    weather = models.TextField(null=True, blank=True)
    activity_text = models.TextField()
    hours_worked = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    materials_note = models.TextField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    status = models.TextField(db_default="ENTWURF")  # ENTWURF | UNTERZEICHNET
    signed_by_name = models.TextField(null=True, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    signature_file_id = models.UUIDField(null=True, blank=True)
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."site_report'

    def __str__(self):
        return f"Baustellenbericht {self.report_date} ({self.status})"


class SiteReportLine(models.Model):
    """workflow.site_report_line — Berichtsposition (Migration 0080).

    Was vor Ort tatsächlich verbraucht/geleistet wurde: Artikel/Leistung, Menge,
    Einheit. Grundlage des Soll-Ist-Abgleichs gegen das Angebot.

    **INVARIANTE: Die Berichtsposition führt KEINE PREISE.** Kein `unit_price`,
    kein `net_amount`, kein Steuercode — auch nicht „für später". Der Bericht wird
    vom Kunden unterschrieben und danach versiegelt; ein unterschriebener Bericht
    mit Preisen wäre eine **Preisvereinbarung**. Der Preis entsteht erst in der
    Rechnung (Artikelstamm/`aufschlagsmatrix.vk_vorschlag`). Der Bericht liefert
    die Menge, das Belegwesen den Preis.

    Aus demselben Grund kennt `line_type` **kein ZWISCHENSUMME** (der Bericht
    summiert nichts).

    Wie die Belegposition ist auch diese Position eine **Kopie, kein Verweis**:
    `description`/`unit` werden beim Anlegen aus dem Stamm kopiert und eingefroren.
    `planned_quantity` ist die beim Vorbelegen eingefrorene **Sollmenge** aus dem
    Angebot (`source_quote_line_id` = Herkunft); NULL = war nicht angeboten
    (Zusatzleistung).

    Änderbar **nur im ENTWURF** des Berichts — der Trigger
    `workflow.protect_site_report_lines` sperrt INSERT/UPDATE/DELETE, sobald der
    Bericht UNTERZEICHNET ist. Kein No-Delete-Trigger (dokumentierte Ausnahme wie
    bei `invoicing.quote_line`: der Editor ersetzt den ganzen Positionssatz).
    """

    id = models.UUIDField(primary_key=True)
    site_report = models.ForeignKey(
        SiteReport, models.DO_NOTHING, db_column="site_report_id",
        related_name="lines",
    )
    position_number = models.IntegerField()
    # MATERIAL|ARBEITSZEIT|PAUSCHALE|FREMDLEISTUNG|FAHRT|ZUSCHLAG|TEXT
    line_type = models.TextField()
    description = models.TextField()
    quantity = models.DecimalField(
        max_digits=15, decimal_places=3, null=True, blank=True
    )
    unit = models.TextField(null=True, blank=True)
    source_article = models.ForeignKey(
        "Article", models.DO_NOTHING, db_column="source_article_id",
        null=True, blank=True, related_name="site_report_lines",
    )
    source_assembly = models.ForeignKey(
        "Assembly", models.DO_NOTHING, db_column="source_assembly_id",
        null=True, blank=True, related_name="site_report_lines",
    )
    planned_quantity = models.DecimalField(
        max_digits=15, decimal_places=3, null=True, blank=True
    )
    source_quote_line = models.ForeignKey(
        "QuoteLine", models.DO_NOTHING, db_column="source_quote_line_id",
        null=True, blank=True, related_name="site_report_lines",
    )
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."site_report_line'
        ordering = ["position_number"]

    def __str__(self):
        return f"{self.position_number}. {self.description}"


class SupplierConnection(models.Model):
    """pricing.supplier_connection — Anbindung eines Lieferanten (DATANORM, IDS).

    `connection_kind` trennt den Bestellkatalog des Großhändlers von den
    Herstellerkatalogen (Migration 0040 der Baseline): Das Gerätewissen durchsucht
    nur HERSTELLER-Daten (Ersatzteile zu einer Typenbezeichnung), die
    Artikelsuche im Angebot nur GROSSHAENDLER — ein Vaillant-Mikroschalter ist
    beim Großhändler nicht bestellbar.

    `status` ist hier englisch (ACTIVE/INACTIVE), anders als bei den Fachtabellen
    mit deutschem Statusautomaten.
    """

    id = models.UUIDField(primary_key=True)
    supplier_party = models.ForeignKey(
        "Party", models.DO_NOTHING, db_column="supplier_party_id",
        related_name="supplier_connections",
    )
    source_system = models.TextField()          # DATANORM | IDS_CONNECT
    source_namespace = models.TextField()
    label = models.TextField()                  # NOT NULL in der DB
    shop_url = models.TextField(null=True, blank=True)
    credential_reference = models.TextField(null=True, blank=True)
    status = models.TextField()                 # ACTIVE | INACTIVE
    connection_kind = models.TextField()        # GROSSHAENDLER | HERSTELLER
    # Interpretation von OrderItem/NetPrice im IDS-Rückgabe-Warenkorb (GC-Quirk,
    # Migration 0111): EINHEIT = je Einheit (itek-Standard), GESAMT = Positionssumme
    # (NetPrice zusätzlich durch die Menge teilen). Default EINHEIT.
    net_price_semantics = models.TextField()    # EINHEIT | GESAMT
    last_import_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(db_default=1)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."supplier_connection'

    def __str__(self):
        return f"{self.source_namespace} ({self.connection_kind})"


class SupplierCredential(models.Model):
    """pricing.supplier_credential — IDS-Connect-Zugangsdaten einer Anbindung
    (Migration 0052, 1:1 zu supplier_connection).

    Das Passwort liegt Fernet-verschlüsselt in `password_encrypted` (bytea); der
    Klartext wird nie gespeichert/zurückgegeben/geloggt (Muster wie
    company.mail_account). Benutzername/Kundennummer sind keine Geheimnisse.
    """

    id = models.UUIDField(primary_key=True)
    connection = models.OneToOneField(
        SupplierConnection, models.DO_NOTHING, db_column="connection_id",
        related_name="credential",
    )
    username = models.TextField(null=True, blank=True)
    customer_number = models.TextField(null=True, blank=True)
    password_encrypted = models.BinaryField(null=True, blank=True)
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."supplier_credential'

    def __str__(self):
        return f"Zugangsdaten {self.connection_id}"


class PunchoutSession(models.Model):
    """pricing.punchout_session — kurzlebige IDS-Connect-Punchout-Session
    (Migration 0056).

    Ordnet den unauthentifizierten Shop-Rückruf (hookurl) der auslösenden Aktion
    zu. In der DB liegt nur der SHA-256-Hash des Einmal-Tokens (`token_hash`); der
    Klartext lebt nur in der an den Shop übergebenen hookurl. Statusautomat
    OFFEN → EINGELOEST; abgelaufene Sessions (`expires_at`) wehrt der Service ab.
    """

    id = models.UUIDField(primary_key=True)
    connection = models.ForeignKey(
        SupplierConnection, models.DO_NOTHING, db_column="connection_id",
        related_name="punchout_sessions",
    )
    quote = models.ForeignKey(
        "Quote", models.DO_NOTHING, db_column="quote_id",
        null=True, blank=True, related_name="punchout_sessions",
    )
    token_hash = models.TextField()
    action = models.TextField()  # WKE | WKS
    status = models.TextField(db_default="OFFEN")  # OFFEN | EINGELOEST
    returned_cart_xml = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="punchout_sessions",
    )
    expires_at = models.DateTimeField()
    redeemed_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."punchout_session'

    def __str__(self):
        return f"Punchout {self.action} ({self.status})"


# ---------------------------------------------------------------------------
# Qualifikationen und Zuweisungs-Vorlagen (Migration 0078)
# ---------------------------------------------------------------------------

class Qualification(models.Model):
    """hr.qualification — frei pflegbarer Qualifikationskatalog (Migration 0078).

    **`kind` ist eine Gruppierung als DATENWERT, kein Enum im Code** (bewusst ohne
    CHECK): Der Betrieb legt „GEWERK", „ZERTIFIKAT", „HERSTELLERSCHULUNG" oder was
    immer er braucht selbst an. Ein fest verdrahtetes Enum verlangte für jede neue
    Schulungsart eine Migration — der User hat ausdrücklich um Flexibilität gebeten.

    `expires` sagt, ob die Zuordnung ein Gültig-bis verlangt (Gasschein ja,
    Gesellenbrief nein). Der DB-Trigger setzt das durch.
    """

    id = models.UUIDField(primary_key=True)
    code = models.TextField()
    label = models.TextField()
    kind = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    expires = models.BooleanField(db_default=models.Value(False))
    active = models.BooleanField(db_default=models.Value(True))
    sort_order = models.IntegerField(db_default=models.Value(0))
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_qualifications",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."qualification'

    def __str__(self):
        return f"{self.code} {self.label}"


class EmployeeQualification(models.Model):
    """hr.employee_qualification — wer kann was, bis wann (Migration 0078).

    Genau EINE Zeile je (Mitarbeiter, Qualifikation): Eine Verlängerung schreibt
    `valid_until` fort, sie legt keine zweite Zeile an — sonst wäre „gültig?"
    mehrdeutig. `valid_until = NULL` heißt „läuft nie ab" (nur erlaubt, wenn der
    Katalog `expires = false` sagt).
    """

    id = models.UUIDField(primary_key=True)
    employee = models.ForeignKey(
        "Employee", models.DO_NOTHING, db_column="employee_id",
        related_name="qualifications",
    )
    qualification = models.ForeignKey(
        Qualification, models.DO_NOTHING, db_column="qualification_id",
        related_name="employee_links",
    )
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    evidence_note = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_employee_qualifications",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'hr"."employee_qualification'

    def __str__(self):
        return f"{self.employee_id} · {self.qualification_id}"


class AppointmentCategoryQualification(models.Model):
    """workflow.appointment_category_qualification — was ein Termintyp IMMER braucht."""

    id = models.UUIDField(primary_key=True)
    appointment_category = models.ForeignKey(
        AppointmentCategory, models.DO_NOTHING, db_column="appointment_category_id",
        related_name="qualification_links",
    )
    qualification = models.ForeignKey(
        Qualification, models.DO_NOTHING, db_column="qualification_id",
        related_name="category_links",
    )
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_category_qualifications",
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."appointment_category_qualification'


class ServiceJobQualification(models.Model):
    """workflow.service_job_qualification — was DIESER eine Termin zusätzlich braucht.

    Der wirksame Bedarf ist die VEREINIGUNG aus Kategoriebedarf und Terminbedarf.
    """

    id = models.UUIDField(primary_key=True)
    service_job = models.ForeignKey(
        ServiceJob, models.DO_NOTHING, db_column="service_job_id",
        related_name="qualification_links",
    )
    qualification = models.ForeignKey(
        Qualification, models.DO_NOTHING, db_column="qualification_id",
        related_name="job_links",
    )
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_job_qualifications",
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."service_job_qualification'


class AssignmentTemplate(models.Model):
    """workflow.assignment_template — benannte Personengruppe als VORSCHLAG (0078).

    Der Betrieb fährt in „losen Gruppen, wechselnd" (User-Entscheidung) — deshalb
    KEIN Team-Modell mit eigenen Board-Bahnen, sondern eine Vorlage, die der
    Termin-Dialog auf Knopfdruck übernimmt. Sie **bindet nichts**: Danach sind es
    gewöhnliche Einzelzuweisungen, und wer abweicht, weicht ab.
    """

    id = models.UUIDField(primary_key=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    active = models.BooleanField(db_default=models.Value(True))
    sort_order = models.IntegerField(db_default=models.Value(0))
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_assignment_templates",
    )
    version = models.IntegerField(db_default=models.Value(1))
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."assignment_template'

    def __str__(self):
        return self.name


class AssignmentTemplateMember(models.Model):
    """workflow.assignment_template_member — Mitglied einer Zuweisungs-Vorlage."""

    id = models.UUIDField(primary_key=True)
    template = models.ForeignKey(
        AssignmentTemplate, models.DO_NOTHING, db_column="template_id",
        related_name="members",
    )
    assignee = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="assignee_user_id",
        related_name="assignment_template_memberships",
    )
    role = models.TextField(db_default="TECHNICIAN")  # TECHNICIAN | LEAD
    created_by = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="created_by",
        related_name="created_assignment_template_members",
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."assignment_template_member'


class BillingLink(models.Model):
    """invoicing.billing_link — die Abrechnungsbindung (Migration 0084).

    **Kein Beleg**, sondern eine interne Verknüpfung mit genau einer Aussage:
    „Diese Berichtsposition / diese Zeitbuchung / diese Angebotsposition ist in
    DIESER Rechnungsposition abgerechnet."

    **INVARIANTE: Dieselbe Leistung kann physisch nicht zweimal abgerechnet
    werden.** Drei partielle UNIQUE-Indizes (je Quellspalte,
    `WHERE released_at IS NULL`) garantieren das in der **Datenbank** — nicht im
    Service. Zwei parallele Rechnungsläufe können dieselbe Zeitbuchung nicht
    beide greifen.

    **Der Storno löst die Bindung** (`released_at`, Trigger
    `invoicing.release_billing_links_on_cancel`) — und nur er. Eine GUTSCHRIFT
    ist eine Teilkorrektur: die Ursprungsrechnung besteht weiter und fordert
    weiterhin Geld, ihre Leistung ist also weiterhin abgerechnet. Dieselbe Grenze
    zieht das Modul schon bei den Abschlägen (`advance_blocking_final`).

    Genau EINE Quellspalte ist gesetzt, passend zu `source_kind` (CHECK).
    `invoice_line_id` ist bei einer **aktiven** Bindung immer gesetzt (CHECK);
    NULL kann sie nur bei einer gelösten sein, deren Entwurfsposition entfernt
    wurde (`abrechnung.bindungen_loesen`).

    Kein DELETE (Trigger): Ein gelöschter Link machte die Sperre spurlos
    rückgängig. Aufgehoben wird ausschließlich über `released_at` — mit Grund,
    mit Zeitstempel, im Audit.
    """

    id = models.UUIDField(primary_key=True)
    invoice = models.ForeignKey(
        Invoice, models.DO_NOTHING, db_column="invoice_id",
        related_name="billing_links",
    )
    invoice_line = models.ForeignKey(
        InvoiceLine, models.DO_NOTHING, db_column="invoice_line_id",
        null=True, blank=True, related_name="billing_links",
    )
    # BERICHTSPOSITION | ZEITBUCHUNG | ANGEBOTSPOSITION
    source_kind = models.TextField()
    site_report_line = models.ForeignKey(
        SiteReportLine, models.DO_NOTHING, db_column="site_report_line_id",
        null=True, blank=True, related_name="billing_links",
    )
    time_entry = models.ForeignKey(
        TimeEntry, models.DO_NOTHING, db_column="time_entry_id",
        null=True, blank=True, related_name="billing_links",
    )
    quote_line = models.ForeignKey(
        QuoteLine, models.DO_NOTHING, db_column="quote_line_id",
        null=True, blank=True, related_name="billing_links",
    )
    released_at = models.DateTimeField(null=True, blank=True)
    released_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."billing_link'

    def __str__(self):
        return f"{self.source_kind} -> {self.invoice_id}"


class Room(models.Model):
    """property.room — Raum als Objektstammdatum (Aufmaß, Migration 0086).

    `volume_m3` ist eine GENERATED-Spalte der Datenbank und deshalb hier
    nicht schreibbar (`db_default` genügt nicht — Django dürfte sie nie in ein
    INSERT/UPDATE aufnehmen; das verhindert `Meta.managed = False` in Verbindung
    damit, dass der Service ausschließlich über explizite Feldlisten schreibt).
    `building_id`/`unit_id` sind zusammengesetzte FKs (Composite-Ziel) und
    werden wie bei TechnicalAsset als reine UUIDs geführt.
    """

    id = models.UUIDField(primary_key=True)
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="rooms"
    )
    building_id = models.UUIDField(null=True, blank=True)
    unit_id = models.UUIDField(null=True, blank=True)
    storey = models.TextField(null=True, blank=True)
    name = models.TextField()
    # WOHNEN|SCHLAFEN|KUECHE|BAD|WC|FLUR|TREPPENHAUS|KELLER|DACHBODEN|TECHNIK|
    # BUERO|LAGER|GEWERBE|SONSTIGES
    room_type = models.TextField(null=True, blank=True)
    floor_area_m2 = models.DecimalField(max_digits=10, decimal_places=3)
    length_m = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    width_m = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    room_height_m = models.DecimalField(max_digits=8, decimal_places=3)
    perimeter_m = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    volume_m3 = models.DecimalField(
        max_digits=13, decimal_places=3, null=True, blank=True, editable=False
    )
    indoor_temp_c = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    air_change_rate = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True
    )
    heat_load_w_per_m2 = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True
    )
    riser_distance_m = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    status = models.TextField(db_default="AKTIV")  # AKTIV|INAKTIV
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."room'

    def __str__(self):
        return self.name


class RoomSurface(models.Model):
    """property.room_surface — Hüllfläche (Bauteil) eines Raumes.

    `adjacent` — nicht `surface_type` — entscheidet, ob Wärme verloren geht.
    Fehlt `u_value` oder `temp_factor`, ist die Heizlast UNBEKANNT, nicht 0.
    """

    id = models.UUIDField(primary_key=True)
    room = models.ForeignKey(
        Room, models.DO_NOTHING, db_column="room_id", related_name="surfaces"
    )
    # AUSSENWAND|INNENWAND|DACHSCHRAEGE|DECKE|BODEN
    surface_type = models.TextField()
    # AUSSENLUFT|ERDREICH|UNBEHEIZT|BEHEIZT
    adjacent = models.TextField()
    orientation = models.TextField(null=True, blank=True)  # N|NO|O|SO|S|SW|W|NW
    label = models.TextField(null=True, blank=True)
    gross_area_m2 = models.DecimalField(max_digits=10, decimal_places=3)
    u_value = models.DecimalField(max_digits=5, decimal_places=3, null=True, blank=True)
    temp_factor = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True
    )
    # Herkunft aus dem Bauteilkatalog (0090). Der U-Wert oben ist eine KOPIE —
    # eine spätere Katalogkorrektur ändert dieses Aufmaß nicht.
    template = models.ForeignKey(
        "ComponentTemplate",
        models.DO_NOTHING,
        db_column="template_id",
        null=True,
        blank=True,
        related_name="surfaces",
    )
    # Polygonkante, auf der diese Wand steht (0091). NULL bei Decke/Boden/
    # Dachschräge oder ohne Zeichnung.
    edge_index = models.IntegerField(null=True, blank=True)
    # 0093: Weiß die Fläche, dass sie gerechnet ist? true → der Server rechnet sie
    # bei jeder Änderung von Umriss oder Raumhöhe NEU (Kantenlänge × Raumhöhe).
    # false → Handeingabe (Giebel, Erker) und wird NIE überschrieben.
    area_is_derived = models.BooleanField(db_default=False)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."room_surface'

    def __str__(self):
        return self.label or self.surface_type


class RoomOpening(models.Model):
    """property.room_opening — Fenster/Tür in einer Hüllfläche.

    `area_m2` ist eine GENERATED-Spalte (quantity × width × height). Der Trigger
    `property.enforce_room_opening_fits` garantiert: die Öffnung ist nie größer
    als ihre Wand.
    """

    id = models.UUIDField(primary_key=True)
    room = models.ForeignKey(
        Room, models.DO_NOTHING, db_column="room_id", related_name="openings"
    )
    surface = models.ForeignKey(
        RoomSurface,
        models.DO_NOTHING,
        db_column="surface_id",
        null=True,
        blank=True,
        related_name="openings",
    )
    # FENSTER|DACHFENSTER|TUER_AUSSEN|TUER_INNEN|SONSTIGES
    opening_type = models.TextField()
    label = models.TextField(null=True, blank=True)
    quantity = models.IntegerField(db_default=1)
    width_m = models.DecimalField(max_digits=6, decimal_places=3)
    height_m = models.DecimalField(max_digits=6, decimal_places=3)
    u_value = models.DecimalField(max_digits=5, decimal_places=3, null=True, blank=True)
    area_m2 = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, editable=False
    )
    # Herkunft aus dem Bauteilkatalog (0090) — der U-Wert oben ist eine KOPIE.
    template = models.ForeignKey(
        "ComponentTemplate",
        models.DO_NOTHING,
        db_column="template_id",
        null=True,
        blank=True,
        related_name="openings",
    )
    # Abstand vom Anfangspunkt der Kante (0091). NULL = Lage nicht ausgemessen:
    # die Öffnung zählt in Fläche und Heizlast, sie wird nur nicht gezeichnet.
    position_m = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True
    )
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."room_opening'

    def __str__(self):
        return self.label or self.opening_type


class ComponentTemplate(models.Model):
    """property.component_template — Bauteilkatalog (Migration 0090).

    Vorauswahl statt Zahlentipperei: „Doppelkastenfenster" statt „2,7".

    INVARIANTE: Die Vorlage ist eine KOPIERQUELLE, kein Verweis. Der U-Wert wird
    beim Erfassen in `room_surface`/`room_opening` **kopiert**; der Heizlast-
    Rechner liest **nie** den Katalog. Eine spätere Katalogkorrektur ändert damit
    kein Aufmaß rückwirkend (dieselbe Regel wie bei der Belegposition).

    `u_value` wird OHNE Wert ausgeliefert — keine DIN-Tabellen im Produkt; der
    Betrieb trägt ihn einmal ein.
    """

    id = models.UUIDField(primary_key=True)
    kind = models.TextField()  # FLAECHE|OEFFNUNG
    name = models.TextField()
    # AUSSENWAND|INNENWAND|DACHSCHRAEGE|DECKE|BODEN
    default_surface_type = models.TextField(null=True, blank=True)
    # FENSTER|DACHFENSTER|TUER_AUSSEN|TUER_INNEN|SONSTIGES
    default_opening_type = models.TextField(null=True, blank=True)
    u_value = models.DecimalField(max_digits=5, decimal_places=3, null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    status = models.TextField(db_default="AKTIV")  # AKTIV|INAKTIV
    sort_index = models.IntegerField(db_default=0)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."component_template'

    def __str__(self):
        return self.name


class RoomVertex(models.Model):
    """property.room_vertex — Umriss des Raumes als Polygon (Migration 0091).

    Koordinaten in **Millimetern** (integer), im System des GESCHOSSES — nicht je
    Raum. Zwei Räume derselben Etage liegen damit im selben Raster, und die
    Etagenübersicht entsteht ohne weitere Daten.

    Kante `i` ist das Paar (vertex i → vertex i+1), zyklisch. Sie ist keine Zeile;
    `room_surface.edge_index` verweist auf sie über den Index.

    Hat ein Raum Punkte, sind `room.floor_area_m2` und `room.perimeter_m` **daraus
    gerechnet** (Gauß'sche Trapezformel bzw. Summe der Kantenlängen) — wer
    zeichnet, misst nicht doppelt.
    """

    id = models.UUIDField(primary_key=True)
    room = models.ForeignKey(
        Room, models.DO_NOTHING, db_column="room_id", related_name="vertices"
    )
    idx = models.IntegerField()
    x_mm = models.IntegerField()
    y_mm = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'property"."room_vertex'
        ordering = ["idx"]

    def __str__(self):
        return f"{self.idx}: ({self.x_mm}, {self.y_mm})"


# ---------------------------------------------------------------------------
# tenure — Belegung (wer wohnt/nutzt hier?), Migration 0005, Schutz 0009/0103
# ---------------------------------------------------------------------------


class Occupancy(models.Model):
    """tenure.occupancy — die Belegung einer Einheit (A-17 bis A-20).

    Trägt die **Nutzungsart**, nicht den Mieter: Der hängt als `OccupancyParty`
    daran (A-03/A-19). Genau deshalb gibt es hier **keine** `party_id` — die
    Begründung steht im Modulkopf von Migration 0103.

    * **Überlappungsfrei je Einheit** (`excl_occupancy`, A-18): Eine Einheit hat
      zu jedem Zeitpunkt höchstens **eine** primäre Belegung. Der Constraint ist
      ein EXCLUDE über `daterange(valid_from, valid_until)` — der Service prüft
      vor, die DB entscheidet.
    * **COMMON_AREA und TECHNICAL_ROOM tragen keine Belegung** (Beschluss F-12,
      Trigger `forbid_common_area_occupancy`). Ein tatsächlich vermieteter
      Kellerraum ist als eigene Einheit vom Typ STORAGE zu führen.
    * **Kein Löschen** (0009): Eine Belegung wird **beendet** (`valid_until`),
      nicht gelöscht — sie ist die Historie, auf die Aufträge und Berichte zeigen.
    * `contract_reference` ist eine **Vertragsreferenz**, kein Mietername und
      erst recht kein Mietbetrag (A-17: keine Mietbeträge in diesem System).
    """

    id = models.UUIDField(primary_key=True)
    unit = models.ForeignKey(
        Unit, models.DO_NOTHING, db_column="unit_id", related_name="occupancies"
    )
    # RENTED|OWNER_OCCUPIED|VACANT|COMMERCIAL_USE|OTHER|UNKNOWN
    occupancy_type = models.TextField()
    contract_reference = models.TextField(null=True, blank=True)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'tenure"."occupancy'
        ordering = ["-valid_from"]

    def __str__(self):
        return f"{self.occupancy_type} ab {self.valid_from}"


class OccupancyParty(models.Model):
    """tenure.occupancy_party — **der Mieter** an der Belegung (A-03, A-19).

    Die Heimat des Mieternamens. Ein Beteiligter ist eine ganz normale
    `identity.party` — mit Adressen und Kommunikationswegen, auffindbar und
    verknüpfbar. Der Monteur ruft ihn an, bevor er losfährt.

    * **Mehrere Beteiligte je Belegung sind der Normalfall**, nicht die Ausnahme
      (Ehepaar: zweimal CONTRACTUAL_TENANT; Mitbewohner: OCCUPANT). Eine einzelne
      Spalte an der Belegung könnte das nicht abbilden.
    * **Leerstand** = eine Belegung ohne jeden Beteiligten (Typ `VACANT`).
    * Der Beteiligtenzeitraum muss **innerhalb** des Belegungszeitraums liegen
      (deferred Constraint-Trigger `check_occupancy_party_range`) — ein Mieter
      kann nicht länger wohnen, als die Belegung gilt.
    * MERGED-Parties sind verboten (0009), Löschen ebenfalls (0009): Ein
      ausgezogener Mieter bekommt ein `valid_until`.
    """

    id = models.UUIDField(primary_key=True)
    occupancy = models.ForeignKey(
        Occupancy, models.DO_NOTHING, db_column="occupancy_id", related_name="parties"
    )
    party = models.ForeignKey(
        Party, models.DO_NOTHING, db_column="party_id", related_name="occupancy_roles"
    )
    # CONTRACTUAL_TENANT|CO_TENANT|OCCUPANT|OWNER_OCCUPANT|COMMERCIAL_USER
    role = models.TextField()
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'tenure"."occupancy_party'
        ordering = ["valid_from"]

    def __str__(self):
        return f"{self.role} {self.party_id}"


# ---------------------------------------------------------------------------
# management — das Verwaltungsmandat, Migration 0006, Schutz 0009/0103
# ---------------------------------------------------------------------------


class ManagementMandate(models.Model):
    """management.management_mandate — **die Verwaltung** (A-10 bis A-12).

    **Die Verwaltung ist KEINE Beteiligtenrolle an der Liegenschaft.**
    `property.property_party_role` kennt nur COMMUNITY_OF_OWNERS, PROPERTY_OWNER,
    OPERATOR und CARETAKER; der Kommentar in `0004_property.sql` sagt es wörtlich:
    „Die Verwaltung wird ausschließlich über ein Mandat verbunden." Der
    Unterschied wird bei der Rechnung scharf — **wer beauftragt** (die WEG), **wer
    verwaltet** (Stegos) und **wer den Beleg bekommt** sind drei Fragen.

    Das Mandat verbindet `management_party` (Stegos) mit `principal_party`
    (die WEG) an einer `property`:

    * `mandate_type`: WEG_MANAGEMENT | RENTAL_MANAGEMENT |
      SPECIAL_PROPERTY_MANAGEMENT | SPECIAL_MANDATE
    * `scope_type`: ENTIRE_PROPERTY (**ohne** Mandatseinheiten) | SELECTED_UNITS
      (**mit mindestens einer**). Beides erzwingt ein deferred Constraint-Trigger
      (`assert_mandate_valid`) — nicht der Service.
    * `default_contact_party_id` ist **Pflicht** (A-10, NOT NULL): Ein Mandat ohne
      benannten Ansprechpartner ist eine Telefonnummer, die niemand hat.
    * Vollmandate desselben Typs überlappen nie am selben Objekt
      (`excl_mandate_entire`); Teilmandate kollidieren nie auf derselben Einheit.
    * **Beenden statt löschen** (0009): `status='ENDED'` + `valid_until`
      (CHECK: ein beendetes Mandat hat immer ein Enddatum).
    """

    id = models.UUIDField(primary_key=True)
    management_party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="management_party_id",
        related_name="mandates_as_manager",
    )
    principal_party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="principal_party_id",
        related_name="mandates_as_principal",
    )
    property = models.ForeignKey(
        Property, models.DO_NOTHING, db_column="property_id", related_name="mandates"
    )
    # WEG_MANAGEMENT|RENTAL_MANAGEMENT|SPECIAL_PROPERTY_MANAGEMENT|SPECIAL_MANDATE
    mandate_type = models.TextField()
    # ENTIRE_PROPERTY|SELECTED_UNITS
    scope_type = models.TextField()
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    status = models.TextField()  # ACTIVE | ENDED
    contract_reference = models.TextField(null=True, blank=True)
    default_contact_party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="default_contact_party_id",
        related_name="mandates_as_contact",
    )
    version = models.IntegerField(db_default=1)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'management"."management_mandate'
        ordering = ["-valid_from"]

    def __str__(self):
        return f"{self.mandate_type} ({self.status})"


class ManagementMandateUnit(models.Model):
    """management.management_mandate_unit — Teilmandat auf einzelne Einheiten (A-11).

    **Unveränderlich** (0009, `trg_mandate_unit_immutable`: UPDATE *und* DELETE
    verboten). Der Umfang eines laufenden Mandats wird nicht umgeschrieben; eine
    Korrektur läuft über ein **Nachfolgemandat** (das alte beenden, ein neues mit
    dem richtigen Umfang anlegen). Der Service bietet deshalb bewusst keinen Weg,
    Einheiten nachträglich hinzuzufügen oder zu entfernen — er könnte ihn gar
    nicht anbieten.

    Zusammengesetzter Primärschlüssel (mandate_id, unit_id) und zwei
    zusammengesetzte Fremdschlüssel, die erzwingen, dass Mandat und Einheit zur
    **selben Liegenschaft** gehören; `property_id` ist deshalb redundant, aber
    Pflicht und DB-seitig konsistenzgesichert.
    """

    pk = models.CompositePrimaryKey("mandate_id", "unit_id")
    mandate = models.ForeignKey(
        ManagementMandate,
        models.DO_NOTHING,
        db_column="mandate_id",
        related_name="mandate_units",
    )
    property = models.ForeignKey(
        Property,
        models.DO_NOTHING,
        db_column="property_id",
        related_name="mandate_units",
    )
    unit = models.ForeignKey(
        Unit, models.DO_NOTHING, db_column="unit_id", related_name="mandate_units"
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'management"."management_mandate_unit'

    def __str__(self):
        return f"{self.mandate_id} -> {self.unit_id}"


class ManagementResponsibility(models.Model):
    """management.management_responsibility — weitere Kontakte am Mandat (A-13).

    Der **Standardkontakt** steht am Mandat selbst (Pflicht). Hier stehen die
    **zusätzlichen** Zuständigkeiten: technischer Kontakt, kaufmännischer Kontakt,
    Buchhaltung, **Notfallkontakt**, Freigabeberechtigter — mit `priority` als
    Eskalationsreihenfolge (kleiner = früher).

    Kein Löschen (0009); eine Zuständigkeit endet über `valid_until`.
    """

    id = models.UUIDField(primary_key=True)
    mandate = models.ForeignKey(
        ManagementMandate,
        models.DO_NOTHING,
        db_column="mandate_id",
        related_name="responsibilities",
    )
    # TECHNICAL_CONTACT|COMMERCIAL_CONTACT|ACCOUNTING_CONTACT|EMERGENCY_CONTACT|APPROVER
    responsibility_type = models.TextField()
    responsible_party = models.ForeignKey(
        Party,
        models.DO_NOTHING,
        db_column="responsible_party_id",
        related_name="mandate_responsibilities",
    )
    priority = models.IntegerField(db_default=100)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'management"."management_responsibility'
        ordering = ["priority", "valid_from"]

    def __str__(self):
        return f"{self.responsibility_type} ({self.priority})"


# ---------------------------------------------------------------------------
# Schema ai — KI-Grundlagen (Migration 0027): abgeleitete Inhalte, Embeddings,
# Läufe, Vorschläge. Die KI schreibt NIE direkt: sie erzeugt einen ai_proposal
# ohne fachliche Wirkung, ausgeführt wird der ausschließlich über die Fach-API
# (dieselben Tore wie beim Menschen). Alle hier gespeicherten Inhalte sind DATEN,
# niemals Anweisungen (Prompt-Injection); is_untrusted kennzeichnet externe Quellen.
# ---------------------------------------------------------------------------


class ContentItem(models.Model):
    """ai.content_item — extrahierter Text als abgeleitete Kopie (Migration 0027).

    Die Fachwahrheit bleibt im Quellmodul; hier liegt nur eine für Retrieval/KI
    aufbereitete Kopie. Genau EINE Quelle ist gesetzt (DB-CHECK
    num_nonnulls(communication_id, document_id, file_id) = 1). `content.document`
    ist nicht als Model abgebildet — `document_id` bleibt ein rohes UUID-Feld, die
    DB prüft den Fremdschlüssel trotzdem.

    `is_untrusted` (Default true) kennzeichnet Inhalte aus externen Quellen (E-Mail,
    fremdes PDF, Foto-/OCR-Text): Sie sind DATEN, nie Anweisung — der Prompt-
    Injection-Schutz der ganzen Kette hängt daran.
    """

    id = models.UUIDField(primary_key=True)
    # EMAIL | PDF | EINSATZBERICHT | FOTO_BESCHREIBUNG | PROTOKOLL | SONSTIGES
    source_type = models.TextField()
    communication = models.ForeignKey(
        Communication, models.DO_NOTHING, db_column="communication_id",
        null=True, blank=True, related_name="content_items",
    )
    document_id = models.UUIDField(null=True, blank=True)
    file = models.ForeignKey(
        File, models.DO_NOTHING, db_column="file_id",
        null=True, blank=True, related_name="content_items",
    )
    extracted_text = models.TextField()
    language = models.TextField(null=True, blank=True)
    content_hash = models.TextField()
    is_untrusted = models.BooleanField(db_default=models.Value(True))
    # Datenklasse für das Dispatcher-Tor (Migration 0106); vorerst einwertig.
    data_class = models.TextField(db_default="LOCAL_ONLY")
    # Der Werkzeug-Aufruf, der diesen Text erzeugt hat (UNIQUE in der DB → kein
    # zweites Transkript bei doppeltem Ergebnis).
    source_tool_call = models.ForeignKey(
        "ToolCall", models.DO_NOTHING, db_column="source_tool_call_id",
        null=True, blank=True, related_name="produced_content",
    )
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'ai"."content_item'

    def __str__(self):
        return f"{self.source_type} ({self.id})"


class Embedding(models.Model):
    """ai.embedding — Vektor eines Textabschnitts, abgeleitete Daten (Migration 0027).

    Modellagnostisch als `real[]` gespeichert; die pgvector-Einführung mit fester
    Dimension folgt erst nach der Modellauswahl per eigener Migration. Als abgeleitete
    Daten lösch- und neu aufbaubar — hängt per ON DELETE CASCADE am content_item
    (die DB räumt auf, kein Löschverbot). Eindeutig je (content_item, chunk_index,
    embedding_model, embedding_version): dasselbe Modell erzeugt denselben Abschnitt
    nur einmal, verschiedene Modelle koexistieren.
    """

    id = models.UUIDField(primary_key=True)
    content_item = models.ForeignKey(
        ContentItem, models.DO_NOTHING, db_column="content_item_id",
        related_name="embeddings",
    )
    chunk_index = models.IntegerField()
    chunk_text = models.TextField()
    embedding_model = models.TextField()
    embedding_version = models.TextField()
    vector = ArrayField(models.FloatField())
    content_hash = models.TextField()
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'ai"."embedding'
        ordering = ["chunk_index"]

    def __str__(self):
        return f"{self.embedding_model}/{self.embedding_version} #{self.chunk_index}"


class AiRun(models.Model):
    """ai.ai_run — Protokoll eines KI-Laufs (Migration 0027). Append-only.

    Hält fest, WELCHES Modell in welcher Version über welchen Workflow/Prompt
    entschieden hat, wer den Lauf auslöste, mit welchem Rechtekontext, aus welchen
    Quellen und mit welchen Werkzeugen. Zugleich die Grundlage des Modellvergleichs
    (gleicher Input, zwei Modelle → `model_name`/`model_version` unterscheiden die Läufe).

    Ein Lauf wird genau EINMAL abgeschlossen (Trigger `guard_ai_run_update`); danach
    unveränderlich, kein Löschen/Truncate (Schutzstandard).
    """

    id = models.UUIDField(primary_key=True)
    model_name = models.TextField()
    model_version = models.TextField()
    workflow_name = models.TextField()
    workflow_version = models.TextField()
    prompt_version = models.TextField()
    triggered_by_user = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="triggered_by_user_id",
        related_name="ai_runs",
    )
    permission_context = models.JSONField(default=dict)
    sources = models.JSONField(default=list)
    tools_used = models.JSONField(default=list)
    started_at = models.DateTimeField(db_default=Now())
    finished_at = models.DateTimeField(null=True, blank=True)
    # OK | FEHLER | ABBRUCH — gesetzt genau beim Abschluss (zusammen mit finished_at)
    result_status = models.TextField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    resource_usage = models.JSONField(default=dict)
    # Ein Lauf ist der Protokolleintrag EINES Modell-Aufrufs; er hängt (nullbar für
    # Alt-Läufe/Einzelaufrufe ohne Workflow) an einem durablen workflow_run (0106).
    workflow_run = models.ForeignKey(
        "WorkflowRun", models.DO_NOTHING, db_column="workflow_run_id",
        null=True, blank=True, related_name="ai_runs",
    )

    class Meta:
        managed = False
        db_table = 'ai"."ai_run'

    def __str__(self):
        return f"{self.workflow_name} [{self.model_name}] {self.result_status or 'läuft'}"


class AiProposal(models.Model):
    """ai.ai_proposal — KI-Vorschlag OHNE fachliche Wirkung (Migration 0027).

    Der Kern der Vision: Die KI schreibt nie selbst. Sie legt einen Vorschlag ab;
    ausgeführt wird er ausschließlich durch die App-Schicht über die Fach-API — durch
    dieselben Statusautomaten, Freigaben und DB-Trigger wie beim Menschen.

    Die Freigabe ist an `payload_hash`, `target_type`/`target_id`, `target_version`,
    den freigebenden Benutzer und `expires_at` gebunden (Trigger `guard_ai_proposal`;
    Freigabe nach Ablauf ist unzulässig, die Freigabezeit setzt die Serverzeit — nicht
    fälschbar). Inhalt (Payload, Hash, Ziel) ist nach Anlage unveränderlich; Status nur
    PENDING → APPROVED/REJECTED/EXPIRED. Kein Löschen/Truncate.
    """

    id = models.UUIDField(primary_key=True)
    ai_run = models.ForeignKey(
        AiRun, models.DO_NOTHING, db_column="ai_run_id", related_name="proposals",
    )
    proposal_type = models.TextField()
    target_type = models.TextField()
    target_id = models.UUIDField()
    target_version = models.IntegerField(null=True, blank=True)
    proposed_payload = models.JSONField(default=dict)
    payload_hash = models.TextField()
    # PENDING | APPROVED | REJECTED | EXPIRED
    status = models.TextField(db_default="PENDING")
    expires_at = models.DateTimeField()
    approved_by_user = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="approved_by_user_id",
        null=True, blank=True, related_name="approved_ai_proposals",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'ai"."ai_proposal'
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.proposal_type} → {self.target_type} ({self.status})"


class WorkflowRun(models.Model):
    """ai.workflow_run — durabler, wiederaufnehmbarer Workflow-Lauf (Migration 0106).

    Der Anker eines KI-Workflows über Zeit: mehrere Modell-Aufrufe (`ai_run`) und
    Werkzeug-Aufrufe (`tool_call`) hängen daran, und er darf **warten** (WAITING),
    während ein passives Gerät gepollt wird. `context` trägt nur Referenzen/IDs,
    **nie personenbezogenen Rohtext** (der lebt im löschbaren `content_item`).
    Statusübergänge erzwingt der Trigger `guard_workflow_run`.
    """

    id = models.UUIDField(primary_key=True)
    workflow_name = models.TextField()
    workflow_version = models.TextField()
    triggered_by_user = models.ForeignKey(
        AppUser, models.DO_NOTHING, db_column="triggered_by_user_id",
        related_name="workflow_runs",
    )
    # QUEUED | RUNNING | WAITING | DONE | FAILED | CANCELLED
    status = models.TextField(db_default="QUEUED")
    current_step = models.TextField(null=True, blank=True)
    context = models.JSONField(default=dict)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'ai"."workflow_run'

    def __str__(self):
        return f"{self.workflow_name} ({self.status})"


class Tool(models.Model):
    """ai.tool — Registry eines Werkzeugs (Migration 0106). Konfiguration, nicht Code.

    Ein Werkzeug hat genau EINE Capability. `invocation_mode` unterscheidet externes
    Gerät (SYNC/ASYNC — bei ASYNC pollt MCN) von in-process (INTERNAL: LLM über den
    Adapter, DOMAIN_QUERY über die Lese-Services). Das Bearer-Secret liegt nie hier,
    nur ein `credential_reference` (Fernet unter MCN_CRED_KEY). `tool_key`/`capability`
    sind die unveränderliche Identität (Trigger `guard_tool`).
    """

    id = models.UUIDField(primary_key=True)
    tool_key = models.TextField(unique=True)
    label = models.TextField()
    capability = models.TextField()          # ASR|VISION|OCR|LLM|DOMAIN_QUERY
    invocation_mode = models.TextField()     # SYNC|ASYNC|INTERNAL
    endpoint_url = models.TextField(null=True, blank=True)
    credential_reference = models.TextField(null=True, blank=True)
    # Das Geräte-Bearer, Fernet-verschlüsselt (cred_crypto/MCN_CRED_KEY, Migration
    # 0108). Nie Klartext; der Registry-Service ver-/entschlüsselt.
    bearer_encrypted = models.BinaryField(null=True, blank=True)
    data_boundary = models.TextField(db_default="LOCAL_ONLY")
    timeout_seconds = models.IntegerField(db_default=models.Value(120))
    max_attempts = models.IntegerField(db_default=models.Value(3))
    backoff_seconds = models.DecimalField(
        max_digits=8, decimal_places=2, db_default=models.Value(5)
    )
    capability_version = models.TextField(db_default="1")
    contract_version = models.TextField(db_default="1")
    status = models.TextField(db_default="ACTIVE")   # ACTIVE|INACTIVE
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_health = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'ai"."tool'

    def __str__(self):
        return f"{self.tool_key} [{self.capability}]"


class ToolCall(models.Model):
    """ai.tool_call — ein tatsächlicher Werkzeug-Aufruf (Migration 0106).

    State-Machine + Queue-Zeile in einem. `(workflow_run, step_key)` ist der
    Idempotenzschlüssel (UNIQUE): ein Schritt hat GENAU EINE Zeile, Retries
    wiederholen sie (attempt++, Status zurück auf QUEUED). Trägt **nur Hashes/Refs**,
    nie personenbezogenen Rohtext; der erzeugte Text landet im `content_item`
    (`source_tool_call_id`). `capability`/`capability_version` sind bei Dispatch
    eingefroren. Übergänge und Unveränderlichkeit erzwingt `guard_tool_call`.
    """

    id = models.UUIDField(primary_key=True)
    workflow_run = models.ForeignKey(
        WorkflowRun, models.DO_NOTHING, db_column="workflow_run_id",
        related_name="tool_calls",
    )
    tool = models.ForeignKey(
        Tool, models.DO_NOTHING, db_column="tool_id", related_name="calls"
    )
    capability = models.TextField()
    capability_version = models.TextField(db_default="1")
    contract_version = models.TextField(db_default="1")
    step_key = models.TextField()
    # QUEUED | RUNNING | SUCCEEDED | FAILED | EXPIRED | CANCELLED
    status = models.TextField(db_default="QUEUED")
    attempt = models.IntegerField(db_default=models.Value(0))
    leased_until = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    request_hash = models.TextField(null=True, blank=True)
    input_ref = models.JSONField(default=dict)
    output_ref = models.JSONField(null=True, blank=True)
    output_hash = models.TextField(null=True, blank=True)
    is_untrusted = models.BooleanField(db_default=models.Value(True))
    error_code = models.TextField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    metrics = models.JSONField(default=dict)
    cost_units = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    cost_currency = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'ai"."tool_call'

    def __str__(self):
        return f"{self.capability}/{self.step_key} ({self.status})"


class DeviceToken(models.Model):
    """security.device_token — Bearer-Token je Gerät für die native App.

    Neben der Session-Cookie-Auth des Web-Cockpits meldet sich die Android-App
    mit einem Bearer-Token an. Gespeichert wird AUSSCHLIESSLICH der SHA-256-Hex-
    Hash des Tokens; das Klartext-Token verlässt den Server nur einmalig in der
    Login-Antwort. Widerruf über `revoked_at` (stilllegen statt löschen — die
    Tabelle trägt den No-Delete-Schutz). `user` ist das Login-Konto
    (accounts.User); `app_user_id` spiegelt die fachliche Identität für die
    schnelle Rechteauflösung (kann NULL sein: Konto ohne app_user_id).
    """

    id = models.UUIDField(primary_key=True)
    user = models.ForeignKey(
        "accounts.User", models.DO_NOTHING, db_column="user_id",
        related_name="device_tokens",
    )
    app_user_id = models.UUIDField(null=True, blank=True)
    token_hash = models.TextField(unique=True)
    device_name = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'security"."device_token'

    def __str__(self):
        return f"device_token({self.device_name or '—'})"
