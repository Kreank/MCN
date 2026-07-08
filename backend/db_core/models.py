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
    VERSENDET ist der Beleg eingefroren (B-30). Dieser Slice deckt Anlage bis
    ENTWURF sowie Liste/Detail ab — der Versand-Workflow folgt separat. Die
    Versand-/Snapshot-Spalten (billing_snapshot, content_hash, sent_at,
    replaced_by_quote_id, work_order_id) sind hier bewusst nicht modelliert.
    """

    id = models.UUIDField(primary_key=True)
    quote_number = models.TextField(null=True, blank=True)
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
    Dieser Slice: Anlage bis ENTWURF sowie Liste/Detail. Versand-/Snapshot-/
    Auftrags-Gate-Spalten (billing_snapshot, content_hash, published_at,
    work_order_id) sind hier bewusst nicht modelliert.
    """

    id = models.UUIDField(primary_key=True)
    invoice_number = models.TextField(null=True, blank=True)
    # RECHNUNG|ABSCHLAGSRECHNUNG|TEILRECHNUNG|SCHLUSSRECHNUNG|GUTSCHRIFT|STORNO
    invoice_type = models.TextField()
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
    version = models.IntegerField()
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = 'invoicing"."invoice'

    def __str__(self):
        return f"{self.invoice_number or 'ENTWURF'} ({self.invoice_type})"


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
