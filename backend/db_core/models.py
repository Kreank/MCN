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
    Inhalts-Hash (per DB-Trigger). work_order-Bezug bleibt hier ungenutzt
    (optional in der DB), aber modelliert.
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
    """

    id = models.UUIDField(primary_key=True)
    quote = models.ForeignKey(
        Quote, models.DO_NOTHING, db_column="quote_id", related_name="lines"
    )
    position_number = models.IntegerField()
    # MATERIAL|ARBEITSZEIT|PAUSCHALE|FREMDLEISTUNG|FAHRT|ZUSCHLAG|TEXT|ZWISCHENSUMME
    line_type = models.TextField()
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
    position_number = models.IntegerField()
    line_type = models.TextField()
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
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."invoice_line'

    def __str__(self):
        return f"{self.position_number}. {self.description}"


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
    list_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
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
    (E-…) vergibt die DB über workflow.next_number (db_default). Kein Titel-Feld —
    der Titel kommt vom zugehörigen work_order. Kein physisches Löschen
    (Schutzstandard 0015); „Storno" = Status AUSGEFALLEN.
    """

    id = models.UUIDField(primary_key=True)
    job_number = models.TextField(db_default=ServiceJobNumberDefault())
    work_order = models.ForeignKey(
        WorkOrder,
        models.DO_NOTHING,
        db_column="work_order_id",
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


class TimeEntry(models.Model):
    """workflow.time_entry — Zeiterfassung am Einsatz (Migration 0017).

    Zeitarten B-27; INTERNE_ZEIT darf ohne Einsatzbezug erfasst werden, sonst ist
    service_job Pflicht. Korrekturfenster B-28 setzt die DB durch.
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
    # ARBEITSZEIT|FAHRTZEIT|PAUSE|BEREITSCHAFT|NACHARBEIT|INTERNE_ZEIT
    time_type = models.TextField()
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'workflow"."time_entry'

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
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'pricing"."article_sale_price'

    def __str__(self):
        return f"{self.label} @ {self.article_id}"


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
    last_purchase_price = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, null=True, blank=True)
    discount_group = models.TextField(null=True, blank=True)
    last_imported_at = models.DateTimeField(null=True, blank=True)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
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
