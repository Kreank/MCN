"""seed_demo — Demodaten für die Entwicklung (Identity + Liegenschaften).

Legt idempotent einen Demo-Sachbearbeiter (security.app_user), eine Handvoll
realistischer Parties (Personen/Organisationen) sowie ein paar Liegenschaften
mit Adresse, Gebäude/Einheiten und Party-Rollen an. Läuft ausschließlich mit
settings.DEBUG; auf produktiven Umgebungen bricht der Befehl ab. Alle Writes
gehen über die Service-Schicht, also durch business_transaction
(Benutzerkontext, Audit).
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.models import (
    Absence, AppointmentCategory, AppUser, Article, ArticleSalePrice, Assembly,
    Inspection, InspectionType, Warranty,
    DunningNotice, Employee, Invoice, MaintenanceContract, Party, Payment,
    Project, ProjectLog, Property, Quote, Resource, SalePriceGroup,
    ServiceJob, SiteReport, SupplierConnection, Task, TimeCategory, TimeEntry,
    UserRole, WageGroup, WorkDay, WorkOrder,
)
from db_core.services import artikel as artikel_service
from db_core.services import aufgabe as aufgabe_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import einsatz as einsatz_service
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services import gewaehrleistung as gewaehrleistung_service
from db_core.services import pruefung as pruefung_service
from db_core.services import anbindung as anbindung_service
from db_core.services import firma as firma_service
from db_core.services import planung as planung_service
from db_core.services import wartung as wartung_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service
from db_core.services import site_report as site_report_service
from db_core.services import zeiterfassung as zeit_service

# Fester Namespace → wiederholbare Aufrufe treffen denselben Demo-Benutzer.
DEMO_APP_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEMO_APP_USER_NAME = "Demo Sachbearbeiter"

# DATEV-Export-Konfiguration des Demo-Mandanten (frei erfundene, aber
# formgültige Berater-/Mandantennummer; SKR03 wie im Handwerk üblich). Damit
# funktioniert „Buchhaltung → DATEV-Export" out of the box.
_DATEV_DEMO_CONFIG = {
    "datev_consultant_number": "12345",
    "datev_client_number": "1001",
    "datev_chart_of_accounts": "SKR03",
    "datev_account_length": 4,
    "datev_fiscal_year_start_month": 1,
}

DEMO_PERSONS = [
    {"salutation": "Herr", "title": "Dr.", "first_name": "Thomas", "last_name": "Bergmann"},
    {"salutation": "Frau", "first_name": "Sabine", "last_name": "Krüger"},
    {"salutation": "Herr", "first_name": "Michael", "last_name": "Hoffmann"},
    {"salutation": "Frau", "first_name": "Andrea", "last_name": "Wagner"},
]

DEMO_ORGANIZATIONS = [
    {
        "legal_name": "Hausverwaltung Meyer & Partner GmbH",
        "organization_type": "PROPERTY_MANAGEMENT",
        "legal_form": "GmbH",
    },
    {
        "legal_name": "WEG Lindenstraße 12, Musterstadt",
        "organization_type": "WEG",
        "display_name": "WEG Lindenstraße 12",
    },
    {
        "legal_name": "Elektro Schneider GmbH",
        "organization_type": "COMPANY",
        "legal_form": "GmbH",
    },
    {
        "legal_name": "Rheinische Gebäudeversicherung AG",
        "organization_type": "INSURER",
        "legal_form": "AG",
    },
]

# Demo-Liegenschaften. Rollen verweisen über den Anzeigenamen auf die oben
# angelegten Parties (aufgelöst zur Laufzeit); Gebäude/Einheiten hängen daran.
DEMO_PROPERTIES = [
    {
        "name": "Wohnanlage Lindenhöfe",
        "property_type": "WEG",
        "address": {
            "street": "Lindenstraße",
            "house_number": "12",
            "postal_code": "40213",
            "city": "Musterstadt",
        },
        "buildings": [
            {
                "building_number": "A",
                "name": "Vorderhaus",
                "units": [
                    {"unit_type": "APARTMENT", "unit_number": "WE 01"},
                    {"unit_type": "APARTMENT", "unit_number": "WE 02"},
                    {"unit_type": "APARTMENT", "unit_number": "WE 03"},
                ],
            },
            {
                "building_number": "B",
                "name": "Gartenhaus",
                "units": [
                    {"unit_type": "APARTMENT", "unit_number": "WE 04"},
                    {"unit_type": "COMMON_AREA", "unit_number": "Waschküche"},
                ],
            },
        ],
        "roles": [
            {"party": "WEG Lindenstraße 12", "role": "COMMUNITY_OF_OWNERS"},
            {"party": "Michael Hoffmann", "role": "CARETAKER"},
        ],
    },
    {
        "name": "Geschäftshaus Rheinpassage",
        "property_type": "COMMERCIAL",
        "address": {
            "street": "Rheinuferpromenade",
            "house_number": "5",
            "postal_code": "50667",
            "city": "Musterstadt",
        },
        "buildings": [
            {
                "building_number": "1",
                "name": None,
                "units": [
                    {"unit_type": "COMMERCIAL", "unit_number": "EG links"},
                    {"unit_type": "COMMERCIAL", "unit_number": "EG rechts"},
                    {"unit_type": "TECHNICAL_ROOM", "unit_number": "Technik UG"},
                ],
            },
        ],
        "roles": [
            {"party": "Thomas Bergmann", "role": "PROPERTY_OWNER"},
            {"party": "Elektro Schneider GmbH", "role": "OPERATOR"},
        ],
    },
]


# Demo-Projekte: verweisen über den Liegenschaftsnamen auf die oben angelegten
# Objekte; optionale Vorgänge (service_case) starten im Status NEU.
DEMO_PROJECTS = [
    {
        "name": "Dachsanierung Lindenhöfe",
        "property": "Wohnanlage Lindenhöfe",
        "cases": [
            {"subject": "Dachziegel nach Sturm lose", "priority": "DRINGEND"},
            {"subject": "Dachrinne Vorderhaus verstopft", "priority": "NORMAL"},
        ],
    },
    {
        "name": "Fassadeninstandsetzung Rheinpassage",
        "property": "Geschäftshaus Rheinpassage",
        "cases": [
            {"subject": "Risse in der Sockelzone prüfen", "priority": "NORMAL"},
        ],
    },
]


# Demo-Aufgaben: verweisen optional über Projektname bzw. Party-Anzeigename.
DEMO_TASKS = [
    {"title": "Angebot Dachsanierung nachfassen", "project": "Dachsanierung Lindenhöfe"},
    {"title": "Rückruf Hausverwaltung Meyer", "party": "Hausverwaltung Meyer & Partner GmbH"},
    {"title": "Fotos Sockelzone anfordern", "project": "Fassadeninstandsetzung Rheinpassage", "done": True},
]


# Demo-Angebote (Status ENTWURF): verweisen über Liegenschafts-/Projektname.
DEMO_QUOTES = [
    {
        "title": "Angebot Dachsanierung Lindenhöfe",
        "property": "Wohnanlage Lindenhöfe",
        "project": "Dachsanierung Lindenhöfe",
        "lines": [
            {"line_type": "TEXT", "description": "Erneuerung der Dacheindeckung Vorderhaus."},
            {"line_type": "MATERIAL", "description": "Dachziegel Tonziegel rot",
             "quantity": 850, "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
            {"line_type": "ARBEITSZEIT", "description": "Dachdeckerarbeiten",
             "quantity": 64, "unit": "h", "unit_price": "58.00", "tax_code": "DE_19"},
            {"line_type": "FAHRT", "description": "An- und Abfahrt",
             "quantity": 4, "unit": "Fahrt", "unit_price": "35.00", "tax_code": "DE_19"},
        ],
    },
]


# Demo-Artikelstamm: Lohngruppen, Artikel und eine Leistung (Stückliste).
DEMO_WAGE_GROUPS = [
    {"name": "Monteur", "kind": "LOHN", "hourly_rate": "58.00", "cost_rate": "42.00"},
    {"name": "Meister", "kind": "LOHN", "hourly_rate": "72.00", "cost_rate": "55.00"},
]
DEMO_ARTICLES = [
    {"article_number": "MAT-1001", "description": "Dachziegel Tonziegel rot",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "2.40",
     "manufacturer_name": "Braas", "product_group": "Dachbaustoffe"},
    {"article_number": "MAT-1002", "description": "Dachrinne verzinkt 6-teilig",
     "unit": "m", "line_type": "MATERIAL", "list_price": "12.90",
     "product_group": "Dachentwässerung"},
    {"article_number": "MAT-1003", "description": "Edelstahlschraube 4,5x60",
     "unit": "Stk", "line_type": "MATERIAL", "list_price": "0.18",
     "product_group": "Befestigung"},
    {"article_number": "FAH-2001", "description": "An- und Abfahrt Servicefahrzeug",
     "unit": "Fahrt", "line_type": "FAHRT", "list_price": "35.00"},
]
DEMO_ASSEMBLIES = [
    {
        "assembly_number": "LEI-3001",
        "name": "Dacheindeckung je m² erneuern",
        "unit": "m²",
        "description": "Alteindeckung abnehmen, neue Tonziegel verlegen.",
        "components": [
            {"article": "MAT-1001", "quantity": "12.000"},
            {"article": "MAT-1003", "quantity": "24.000"},
            {"wage_group": "Monteur", "minutes": "45.00"},
        ],
    },
]


# Demo-Personal (hr.*). Jeder Mitarbeiter braucht eine eigene identity.person,
# ein eigenes security.app_user (Login-Konto, feste UUID → idempotent) und einen
# Personalsatz. Sollstunden-Raster als Wochentag-Feld → Stunden. Abwesenheiten
# werden zur Laufzeit auf Arbeitstage (Montage) des laufenden Jahres gelegt.
VOLLZEIT_HOURS = {
    "hours_monday": Decimal("8"),
    "hours_tuesday": Decimal("8"),
    "hours_wednesday": Decimal("8"),
    "hours_thursday": Decimal("8"),
    "hours_friday": Decimal("8"),
}
TEILZEIT_HOURS = {
    "hours_monday": Decimal("6"),
    "hours_tuesday": Decimal("6"),
    "hours_wednesday": Decimal("6"),
}

DEMO_EMPLOYEES = [
    {
        "person": {"salutation": "Herr", "first_name": "Jörg", "last_name": "Feldmann"},
        "account_id": uuid.UUID("00000000-0000-4000-8000-000000000101"),
        # Login-Konto (accounts.User): E-Mail rein ASCII (joerg, nicht jörg).
        "login_email": "joerg.feldmann@mitra-sanitaer.de",
        "role": "ADMINISTRATION",
        "hired_on": date(2021, 3, 1),
        "hours": VOLLZEIT_HOURS,
        "vacation_days": Decimal("30"),
        "status": "AKTIV",
        # (Startmonat, Länge in Arbeitstagen Mo–Fr, genehmigen?)
        "absences": [
            {"month": 3, "weekdays": 5, "approve": True},   # genehmigt
            {"month": 4, "weekdays": 3, "approve": False},  # eingereicht (offen)
        ],
    },
    {
        "person": {"salutation": "Frau", "first_name": "Petra", "last_name": "Lindqvist"},
        "account_id": uuid.UUID("00000000-0000-4000-8000-000000000102"),
        "login_email": "petra.lindqvist@mitra-sanitaer.de",
        "role": "DISPOSITION",
        "hired_on": date(2022, 9, 1),
        "hours": TEILZEIT_HOURS,
        "vacation_days": Decimal("18"),
        "status": "AKTIV",
        "absences": [],
    },
    {
        # Der Monteur — Zielperson der Stempeluhr (Rolle MONTEUR, row_scope
        # EIGENE). Er sieht ausschließlich seine eigenen Einsätze, Aufgaben und
        # Zeiten; die Verwaltungssicht der Zeiterfassung bleibt für ihn 403.
        "person": {"salutation": "Herr", "first_name": "Timo", "last_name": "Kalinski"},
        "account_id": uuid.UUID("00000000-0000-4000-8000-000000000104"),
        "login_email": "timo.kalinski@mitra-sanitaer.de",
        "role": "MONTEUR",
        "hired_on": date(2023, 5, 2),
        "hours": VOLLZEIT_HOURS,
        "vacation_days": Decimal("28"),
        "status": "AKTIV",
        "absences": [],
    },
    {
        "person": {"salutation": "Herr", "first_name": "Sven", "last_name": "Ostmann"},
        "account_id": uuid.UUID("00000000-0000-4000-8000-000000000103"),
        "login_email": "sven.ostmann@mitra-sanitaer.de",
        "role": "NUR_LESEN",
        "hired_on": date(2019, 1, 15),
        "hours": VOLLZEIT_HOURS,
        "vacation_days": Decimal("30"),
        "status": "AUSGETRETEN",
        "left_on": date(2024, 6, 30),
        "absences": [],
    },
]

# Administrations-Login, verknüpft mit dem Seed-Akteur (DEMO_APP_USER_ID). Django-
# Superuser (Admin-Backend), fachlich Rolle ADMINISTRATION.
ADMIN_LOGIN_EMAIL = "admin@mitra-sanitaer.de"

# Wegwerf-Dev-Passwort (12+ Zeichen, erfüllt die Passwort-Policy). NIE in eine
# Datei schreiben — nur auf stdout ausgeben. Nur bei settings.DEBUG gesetzt.
DEV_PASSWORD = os.environ.get("MCN_DEV_PASSWORD", "mcn-dev-passwort-2026")


def _first_monday(year, month):
    """Der erste Montag des angegebenen Monats (garantiert ein Werktag)."""
    d = date(year, month, 1)
    return d + timedelta(days=(0 - d.weekday()) % 7)


class Command(BaseCommand):
    help = (
        "Legt Demo-Kontakte, -Liegenschaften, -Projekte (mit Vorgängen), "
        "-Aufgaben, -Angebote, -Artikelstamm und -Personal für die Entwicklung an."
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo läuft nur mit settings.DEBUG = True.")

        actor, _ = self._ensure_demo_user()

        # Firmenprofil (Singleton) — steht auf jedem Beleg (Beleg-PDF-Aussteller).
        if firma_service.get_company_profile() is None:
            firma_service.update_company_profile(
                actor.id,
                company_name="Mitra Sanitär GmbH",
                legal_form="GmbH",
                street="Industriestraße 5",
                postal_code="80331",
                city="München",
                state_code="BY",
                phone="+49 89 1234567",
                email="info@mitra-sanitaer.de",
                web="https://mitra-sanitaer.de",
                tax_number="143/456/78901",
                vat_id="DE123456789",
                commercial_register="HRB 123456, AG München",
                bank_name="Stadtsparkasse München",
                iban="DE12500105170648489890",
                bic="SSKMDEMMXXX",
                managing_director="Jörg Feldmann",
                managing_director_title="Geschäftsführer",
                **_DATEV_DEMO_CONFIG,
            )
            self.stdout.write("Firmenprofil angelegt: Mitra Sanitär GmbH")
        else:
            # DATEV-Export-Konfiguration idempotent nachziehen (ältere Profile,
            # die noch vor Migration 0051 angelegt wurden). Nur setzen, wenn leer.
            profil = firma_service.get_company_profile()
            if not profil.datev_consultant_number:
                firma_service.update_company_profile(actor.id, **_DATEV_DEMO_CONFIG)
                self.stdout.write("Firmenprofil: DATEV-Konfiguration ergänzt.")
            else:
                self.stdout.write("Firmenprofil vorhanden — übersprungen.")

        # Idempotenz je Party (nicht am Benutzer-Flag): heilt auch Läufe,
        # die nach Teilerfolg abgebrochen sind.
        angelegt = uebersprungen = 0
        for person in DEMO_PERSONS:
            expected = f"{person['first_name']} {person['last_name']}"
            if Party.objects.filter(display_name=expected).exists():
                uebersprungen += 1
                continue
            party = identity_service.create_person(actor.id, **person)
            angelegt += 1
            self.stdout.write(f"Person angelegt: {party.display_name} ({party.id})")

        for org in DEMO_ORGANIZATIONS:
            expected = org.get("display_name") or org["legal_name"]
            if Party.objects.filter(display_name=expected).exists():
                uebersprungen += 1
                continue
            party = identity_service.create_organization(actor.id, **org)
            angelegt += 1
            self.stdout.write(f"Organisation angelegt: {party.display_name} ({party.id})")

        # IDS-Connect-Demo-Anbindung: ein Großhändler + eine aktive Anbindung,
        # damit „Artikel → Anbindungen" out of the box Daten zeigt.
        if not SupplierConnection.objects.filter(source_namespace="gut").exists():
            gh = Party.objects.filter(
                display_name="Sanitär-Großhandel G.U.T. GmbH"
            ).first()
            if gh is None:
                gh = identity_service.create_organization(
                    actor.id, legal_name="Sanitär-Großhandel G.U.T. GmbH",
                    organization_type="COMPANY", legal_form="GmbH",
                )
            anbindung_service.create_connection(
                actor.id, supplier_party_id=gh.id, source_namespace="gut",
                label="G.U.T. Großhandel (IDS-Connect)", source_system="IDS_CONNECT",
                connection_kind="GROSSHAENDLER",
                # Platzhalter — die echte Connector-URL ist gutonlineplus.de/ids.aspx
                # (an der produktiven Anbindung gesetzt). G.U.T. liefert NetPrice als
                # Positionssumme; produktiv trägt die Anbindung dafür
                # net_price_semantics='GESAMT' (GC-Quirk, Migration 0111). Der Demo-
                # Platzhalter macht keinen echten Roundtrip → Default EINHEIT genügt.
                shop_url="https://shop.gut-gruppe.example",
            )
            self.stdout.write("IDS-Anbindung angelegt: G.U.T. (gut)")

        # Kontaktmappe-Demodaten: ein Ansprechpartner, eine Adresse und zwei
        # Kommunikationswege an einem Demokontakt, damit die Mappe echte Daten
        # zeigt. Idempotent über die aktiven Listen (heilt Teilerfolge nach).
        meyer = Party.objects.filter(
            display_name="Hausverwaltung Meyer & Partner GmbH"
        ).first()
        sabine = Party.objects.filter(display_name="Sabine Krüger").first()
        if meyer is not None:
            if sabine is not None and not identity_service.list_contact_persons(meyer.id):
                identity_service.add_contact_person(
                    actor.id, meyer.id, person_party_id=sabine.id,
                    valid_from=date(2021, 3, 1),
                )
                self.stdout.write("Ansprechpartner verknuepft: Sabine Krueger an Meyer")
            if not identity_service.list_addresses(meyer.id):
                identity_service.add_address(
                    actor.id, meyer.id, address_type="BUSINESS",
                    street="Verwaltungsweg", house_number="7",
                    postal_code="40213", city="Musterstadt",
                    valid_from=date(2021, 3, 1),
                )
                self.stdout.write("Adresse angelegt: Meyer (BUSINESS)")
            if not identity_service.list_contact_points(meyer.id):
                identity_service.add_contact_point(
                    actor.id, meyer.id, contact_type="EMAIL",
                    value="info@hv-meyer.example", is_primary=True,
                    valid_from=date(2021, 3, 1),
                )
                identity_service.add_contact_point(
                    actor.id, meyer.id, contact_type="PHONE",
                    value="+49 211 5551234", is_primary=True,
                    valid_from=date(2021, 3, 1),
                )
                self.stdout.write("Kommunikationswege angelegt: Meyer")

        # Liegenschaften: idempotent auf Property-Ebene (existiert der Name,
        # wird die ganze Liegenschaft übersprungen). Ein nach Teilerfolg
        # abgebrochener Lauf heilt Gebäude/Einheiten/Rollen NICHT nach — für
        # Demodaten genügt das. Rollen-Parties werden über den Anzeigenamen
        # aufgelöst; fehlt eine Party, wird die Rolle mit Hinweis übersprungen
        # statt den Lauf abzubrechen.
        for prop_data in DEMO_PROPERTIES:
            if Property.objects.filter(name=prop_data["name"]).exists():
                uebersprungen += 1
                continue
            prop = property_service.create_property(
                actor.id,
                name=prop_data["name"],
                property_type=prop_data["property_type"],
                **prop_data["address"],
            )
            angelegt += 1
            self.stdout.write(
                f"Liegenschaft angelegt: {prop.property_number} {prop.name} ({prop.id})"
            )

            for bld in prop_data["buildings"]:
                building = property_service.add_building(
                    actor.id,
                    property_id=prop.id,
                    building_number=bld["building_number"],
                    name=bld["name"],
                )
                for unit in bld["units"]:
                    property_service.add_unit(
                        actor.id,
                        building_id=building.id,
                        property_id=prop.id,
                        unit_type=unit["unit_type"],
                        unit_number=unit["unit_number"],
                    )

            for role in prop_data["roles"]:
                party = Party.objects.filter(display_name=role["party"]).first()
                if party is None:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Rolle {role['role']} übersprungen: Party "
                            f"'{role['party']}' nicht gefunden."
                        )
                    )
                    continue
                property_service.add_party_role(
                    actor.id,
                    property_id=prop.id,
                    party_id=party.id,
                    role=role["role"],
                    valid_from=date(2020, 1, 1),
                )

        # Projekte: idempotent je Name; verknüpfen eine Liegenschaft und legen
        # optionale Vorgänge (service_case) an.
        for proj in DEMO_PROJECTS:
            if Project.objects.filter(name=proj["name"]).exists():
                uebersprungen += 1
                continue
            obj = Property.objects.filter(name=proj["property"]).first()
            if obj is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Projekt '{proj['name']}' übersprungen: Liegenschaft "
                        f"'{proj['property']}' nicht gefunden."
                    )
                )
                continue
            project = projekt_service.create_project(
                actor.id, name=proj["name"], property_ids=[obj.id]
            )
            angelegt += 1
            self.stdout.write(
                f"Projekt angelegt: {project.project_number} {project.name} ({project.id})"
            )
            for case in proj["cases"]:
                projekt_service.create_service_case(
                    actor.id,
                    property_id=obj.id,
                    subject=case["subject"],
                    project_id=project.id,
                    priority=case["priority"],
                )

        # Aufgaben: idempotent je Titel; optionale Projekt-/Party-Verknüpfung.
        for task in DEMO_TASKS:
            if Task.objects.filter(title=task["title"]).exists():
                uebersprungen += 1
                continue
            project_id = None
            if task.get("project"):
                proj = Project.objects.filter(name=task["project"]).first()
                project_id = proj.id if proj else None
            party_id = None
            if task.get("party"):
                party = Party.objects.filter(display_name=task["party"]).first()
                party_id = party.id if party else None
            created = aufgabe_service.create_task(
                actor.id, title=task["title"], project_id=project_id, party_id=party_id
            )
            if task.get("done"):
                aufgabe_service.complete_task(actor.id, created.id)
            angelegt += 1
            self.stdout.write(f"Aufgabe angelegt: {created.title} ({created.id})")

        # Demo-Auftrag am Dachsanierungs-Projekt, durchgeschaltet bis
        # KAUFMAENNISCH_GEPRUEFT (Voraussetzung für eine veröffentlichte
        # Rechnung, B-08). Durchläuft die echten Freigabe-/Prüf-Tore der DB.
        # Idempotent darüber, ob das Projekt bereits einen Auftrag trägt.
        au_proj = Project.objects.filter(name="Dachsanierung Lindenhöfe").first()
        au_obj = Property.objects.filter(name="Wohnanlage Lindenhöfe").first()
        au_weg = Party.objects.filter(display_name="WEG Lindenstraße 12").first()
        if (
            au_proj is not None and au_obj is not None and au_weg is not None
            and not WorkOrder.objects.filter(project_id=au_proj.id).exists()
        ):
            order = auftrag_service.create_work_order(
                actor.id, property_id=au_obj.id,
                title="Dacheindeckung Vorderhaus erneuern",
                project_id=au_proj.id,
                description="Alteindeckung abnehmen, neue Tonziegel verlegen.",
            )
            auftrag_service.set_order_evidence(
                actor.id, work_order_id=order.id,
                reference="Beschluss Eigentümerversammlung vom 12.03.",
            )
            auftrag_service.confirm_responsibility(
                actor.id, work_order_id=order.id, scope="COMMON_PROPERTY"
            )
            auftrag_service.add_work_order_party(
                actor.id, work_order_id=order.id, party_id=au_weg.id,
                role="PRINCIPAL", is_primary=True, source="OWNERSHIP",
            )
            auftrag_service.add_work_order_party(
                actor.id, work_order_id=order.id, party_id=au_weg.id,
                role="INVOICE_DEBTOR", is_primary=True, source="BILLING_INSTRUCTION",
            )
            for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
                              "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
                auftrag_service.advance_status(
                    actor.id, work_order_id=order.id, to_status=to_status
                )
            order.refresh_from_db()
            angelegt += 1
            self.stdout.write(
                f"Auftrag angelegt: {order.order_number} ({order.status})"
            )

        # Veröffentlichte Rechnung am kaufmännisch geprüften Auftrag: erzeugt
        # echten Umsatz (für die Auswertungen). Durchläuft das Veröffentlichungs-
        # tor (Snapshot/Hash, Auftrag B-08, Schuldner/Empfänger A-27/A-28) mit
        # echtem Commit. Idempotent: nur, wenn der Auftrag noch keine
        # veröffentlichte Rechnung trägt.
        pub_order = (
            WorkOrder.objects.filter(
                project_id=au_proj.id, status="KAUFMAENNISCH_GEPRUEFT"
            ).first()
            if au_proj is not None
            else None
        )
        if (
            pub_order is not None and au_obj is not None and au_weg is not None
            and not Invoice.objects.filter(
                work_order_id=pub_order.id, status="VEROEFFENTLICHT"
            ).exists()
        ):
            inv = beleg_service.create_invoice(
                actor.id, property_id=au_obj.id, invoice_type="RECHNUNG",
                project_id=au_proj.id, work_order_id=pub_order.id,
                invoice_date=date(2026, 5, 15), due_date=date(2026, 6, 14),
                lines=[
                    {"line_type": "MATERIAL", "description": "Dachziegel Tonziegel rot",
                     "quantity": 850, "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
                    {"line_type": "ARBEITSZEIT", "description": "Dachdeckerarbeiten",
                     "quantity": 64, "unit": "h", "unit_price": "58.00", "tax_code": "DE_19"},
                    {"line_type": "FAHRT", "description": "An- und Abfahrt",
                     "quantity": 4, "unit": "Fahrt", "unit_price": "35.00", "tax_code": "DE_19"},
                ],
            )
            beleg_service.add_invoice_party(
                actor.id, invoice_id=inv.id, party_id=au_weg.id,
                role="INVOICE_DEBTOR", is_primary=True,
            )
            beleg_service.add_invoice_party(
                actor.id, invoice_id=inv.id, party_id=au_weg.id,
                role="INVOICE_RECIPIENT", is_primary=True,
            )
            beleg_service.publish_invoice(actor.id, invoice_id=inv.id)
            inv.refresh_from_db()
            angelegt += 1
            self.stdout.write(
                f"Rechnung veröffentlicht: {inv.invoice_number} — {inv.gross_total} EUR"
            )

        # Demo-Rechnung (ENTWURF) an der Wohnanlage Lindenhöfe; idempotent
        # darüber, ob die Liegenschaft bereits eine Rechnung trägt.
        re_obj = Property.objects.filter(name="Wohnanlage Lindenhöfe").first()
        re_proj = Project.objects.filter(name="Dachsanierung Lindenhöfe").first()
        if re_obj is not None and not Invoice.objects.filter(property_id=re_obj.id).exists():
            inv = beleg_service.create_invoice(
                actor.id, property_id=re_obj.id, invoice_type="RECHNUNG",
                project_id=re_proj.id if re_proj else None,
                lines=[
                    {"line_type": "MATERIAL", "description": "Dachziegel Tonziegel rot",
                     "quantity": 320, "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
                    {"line_type": "ARBEITSZEIT", "description": "Dachdeckerarbeiten (Teilabschnitt)",
                     "quantity": 24, "unit": "h", "unit_price": "58.00", "tax_code": "DE_19"},
                ],
            )
            angelegt += 1
            self.stdout.write(
                f"Rechnung angelegt: {inv.invoice_type} (Entwurf) — {inv.gross_total} EUR ({inv.id})"
            )

        # Angebote: idempotent je Titel; brauchen eine Liegenschaft (Pflicht).
        for quote in DEMO_QUOTES:
            if Quote.objects.filter(title=quote["title"]).exists():
                uebersprungen += 1
                continue
            obj = Property.objects.filter(name=quote["property"]).first()
            if obj is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Angebot '{quote['title']}' übersprungen: Liegenschaft "
                        f"'{quote['property']}' nicht gefunden."
                    )
                )
                continue
            project_id = None
            if quote.get("project"):
                proj = Project.objects.filter(name=quote["project"]).first()
                project_id = proj.id if proj else None
            created = beleg_service.create_quote(
                actor.id, property_id=obj.id, title=quote["title"],
                project_id=project_id, lines=quote["lines"],
            )
            angelegt += 1
            self.stdout.write(
                f"Angebot angelegt: {created.title} — {created.gross_total} EUR ({created.id})"
            )

        # Demo-Angebot versenden (ENTWURF → VERSENDET): vergibt die AN-Nummer und
        # friert den Beleg ein. Idempotent: nur, solange es noch ENTWURF ist.
        send_q = Quote.objects.filter(
            title="Angebot Dachsanierung Lindenhöfe", status="ENTWURF"
        ).first()
        if send_q is not None:
            beleg_service.send_quote(actor.id, quote_id=send_q.id)
            send_q.refresh_from_db()
            angelegt += 1
            self.stdout.write(
                f"Angebot versendet: {send_q.quote_number} ({send_q.status})"
            )

        # Lohngruppen (idempotent je Name).
        for wg in DEMO_WAGE_GROUPS:
            if WageGroup.objects.filter(name=wg["name"]).exists():
                uebersprungen += 1
                continue
            artikel_service.create_wage_group(actor.id, **wg)
            angelegt += 1

        # Artikel (idempotent je Nummer).
        for art in DEMO_ARTICLES:
            if Article.objects.filter(article_number=art["article_number"]).exists():
                uebersprungen += 1
                continue
            created = artikel_service.create_article(actor.id, **art)
            angelegt += 1
            self.stdout.write(f"Artikel angelegt: {created.article_number} {created.description}")

        # Leistungen mit Stückliste (idempotent je Nummer); Komponenten über
        # Artikelnummer bzw. Lohngruppennamen aufgelöst.
        for asm in DEMO_ASSEMBLIES:
            if Assembly.objects.filter(assembly_number=asm["assembly_number"]).exists():
                uebersprungen += 1
                continue
            components = []
            for comp in asm["components"]:
                if comp.get("article"):
                    a = Article.objects.filter(article_number=comp["article"]).first()
                    if a is None:
                        continue
                    components.append({"article_id": a.id, "quantity": comp["quantity"]})
                elif comp.get("wage_group"):
                    w = WageGroup.objects.filter(name=comp["wage_group"]).first()
                    if w is None:
                        continue
                    components.append({"wage_group_id": w.id, "minutes": comp["minutes"]})
            created = artikel_service.create_assembly(
                actor.id, assembly_number=asm["assembly_number"], name=asm["name"],
                unit=asm["unit"], description=asm.get("description"), components=components,
            )
            angelegt += 1
            self.stdout.write(f"Leistung angelegt: {created.assembly_number} {created.name}")

        # VK-Kalkulation: zwei Kalkulationsgruppen (Listenpreis-Basis, damit der VK
        # ohne EK-Referenz rechenbar ist) und je Artikel mit Listenpreis eine
        # Standard-VK-Variante. Idempotent je Gruppenname bzw. je Artikel.
        vk_groups = {}
        for g in (
            {"name": "Aufschlag 30% (Listenpreis)", "percent_change": Decimal("30.000")},
            {"name": "Aufschlag 45% (Material)", "percent_change": Decimal("45.000")},
        ):
            existing = SalePriceGroup.objects.filter(name=g["name"]).first()
            if existing is None:
                existing = artikel_service.create_sale_price_group(
                    actor.id, name=g["name"], calc_basis="LISTENPREIS",
                    operator="AUFSCHLAG", percent_change=g["percent_change"],
                )
                angelegt += 1
                self.stdout.write(f"VK-Gruppe angelegt: {existing.name}")
            vk_groups[g["name"]] = existing

        # NUR die Demo-Artikel bepreisen — niemals den ganzen Stamm. Die frühere
        # Fassung lief über alle Artikel mit Listenpreis; nach einem DATANORM-Import
        # legte MCN_SEED=1 dadurch für ~215.000 Fremdartikel eine „Standard"-VK-Zeile
        # über die 45%-Demo-Gruppe an (Unfall vom 14.07.2026, bereinigt via
        # bereinige_vk_seed_unfall). Eine solche Artikel-Zuweisung schlägt die
        # Matrix-Standardregel und verfälscht den VK still um +45 %.
        _demo_artikelnummern = {a["article_number"] for a in DEMO_ARTICLES}
        for art in Article.objects.filter(
            status="AKTIV",
            list_price__isnull=False,
            article_number__in=_demo_artikelnummern,
        ):
            if ArticleSalePrice.objects.filter(article_id=art.id).exists():
                continue
            grp = (
                vk_groups["Aufschlag 45% (Material)"]
                if art.line_type == "MATERIAL"
                else vk_groups["Aufschlag 30% (Listenpreis)"]
            )
            artikel_service.set_article_sale_price(
                actor.id, article_id=art.id, label="Standard",
                sale_price_group_id=grp.id, is_standard=True,
            )
            angelegt += 1

        # Projekt-Cockpit: Logbuch + Checkliste am Dachsanierungs-Projekt
        # (idempotent darüber, ob das Projekt bereits Logeinträge hat).
        cockpit_proj = Project.objects.filter(name="Dachsanierung Lindenhöfe").first()
        if cockpit_proj and not ProjectLog.objects.filter(project_id=cockpit_proj.id).exists():
            projekt_service.add_project_log(
                actor.id, project_id=cockpit_proj.id, category="ANRUF",
                entry="Eigentümergemeinschaft über Sturmschaden informiert; Freigabe angefragt.",
            )
            projekt_service.add_project_log(
                actor.id, project_id=cockpit_proj.id, category="ENTSCHEIDUNG",
                entry="Erneuerung Vorderhaus-Eindeckung beschlossen, Gartenhaus separat.",
            )
            projekt_service.create_checklist(
                actor.id, project_id=cockpit_proj.id, name="Baustellenstart",
                items=[
                    "Gerüst bestellt",
                    "Container für Bauschutt bestellt",
                    "Materiallieferung terminiert",
                    "Anwohner informiert",
                ],
            )
            angelegt += 1
            self.stdout.write("Projekt-Cockpit (Logbuch + Checkliste) angelegt.")

        # Planungs-Stammdaten: sechs Standard-Terminkategorien (Hero-Vorbild) und
        # ein paar Betriebsmittel. Idempotent über den Namen; müssen vor der
        # Kategorie-/Ressourcenzuordnung am Einsatz existieren.
        _std_categories = [
            ("Umsetzung", "NAVY"),
            ("Vor-Ort-Termin", "ORANGE"),
            # Typische Kategorie des freien Termins (ohne Auftrag).
            ("Begehung", "PLUM"),
            ("Schlechtwetter", "SLATE"),
            ("Büro", "AMBER"),
            ("Besprechung", "TEAL"),
            ("Schule", "SAGE"),
        ]
        for idx, (kat_name, token) in enumerate(_std_categories):
            if not AppointmentCategory.objects.filter(name=kat_name).exists():
                planung_service.create_category(
                    actor.id, name=kat_name, color_token=token, sort_order=idx
                )
                angelegt += 1
        _std_resources = [
            ("VW Crafter (BN-MC 1234)", "FAHRZEUG"),
            ("Sprinter (BN-MC 5678)", "FAHRZEUG"),
            ("Hubarbeitsbühne 12 m", "GERAET"),
            ("Kernbohrgerät", "GERAET"),
            ("Besprechungsraum Nord", "RAUM"),
        ]
        for res_name, res_type in _std_resources:
            if not Resource.objects.filter(name=res_name).exists():
                planung_service.create_resource(
                    actor.id, name=res_name, resource_type=res_type
                )
                angelegt += 1

        # Demo-Einsatz (workflow.service_job) am Fassaden-Projekt. Braucht einen
        # freigegebenen/in Ausführung befindlichen Auftrag (Ausführungs-Gate ab
        # UNTERWEGS, B-01/A-23) und einen Auftrag, der NICHT kaufmännisch geprüft
        # ist (sonst sperrt B-28 die Zeit-/Materialerfassung). Deshalb ein eigener
        # Auftrag bis IN_AUSFUEHRUNG. Idempotent: nur, wenn das Projekt noch keinen
        # Auftrag trägt.
        ein_proj = Project.objects.filter(name="Fassadeninstandsetzung Rheinpassage").first()
        ein_obj = Property.objects.filter(name="Geschäftshaus Rheinpassage").first()
        ein_principal = Party.objects.filter(display_name="Thomas Bergmann").first()
        if (
            ein_proj is not None and ein_obj is not None and ein_principal is not None
            and not WorkOrder.objects.filter(project_id=ein_proj.id).exists()
        ):
            ein_order = auftrag_service.create_work_order(
                actor.id, property_id=ein_obj.id,
                title="Sockelrisse Rheinpassage instand setzen",
                project_id=ein_proj.id,
                description="Risse in der Sockelzone öffnen, verpressen und schließen.",
            )
            auftrag_service.set_order_evidence(
                actor.id, work_order_id=ein_order.id,
                reference="Auftrag Eigentümer vom 05.06.",
            )
            auftrag_service.confirm_responsibility(
                actor.id, work_order_id=ein_order.id, scope="PRIVATE_UNIT"
            )
            auftrag_service.add_work_order_party(
                actor.id, work_order_id=ein_order.id, party_id=ein_principal.id,
                role="PRINCIPAL", is_primary=True, source="OWNERSHIP",
            )
            auftrag_service.add_work_order_party(
                actor.id, work_order_id=ein_order.id, party_id=ein_principal.id,
                role="INVOICE_DEBTOR", is_primary=True, source="BILLING_INSTRUCTION",
            )
            for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
                auftrag_service.advance_status(
                    actor.id, work_order_id=ein_order.id, to_status=to_status
                )

            # Einsatz 1: läuft gerade vor Ort — mit Zuweisung, Zeiten, Material.
            job = einsatz_service.create_service_job(
                actor.id, work_order_id=ein_order.id,
                scheduled_start=datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc),
                scheduled_end=datetime(2026, 7, 13, 16, 0, tzinfo=dt_timezone.utc),
                on_site_contact_party_id=ein_principal.id,
                access_instructions="Schlüssel beim Hausmeister EG rechts abholen.",
            )
            for to_status in ("GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"):
                einsatz_service.advance_status(
                    actor.id, service_job_id=job.id, to_status=to_status
                )
            einsatz_service.assign_user(
                actor.id, service_job_id=job.id,
                assignee_user_id=actor.id, role="LEAD",
            )
            einsatz_service.log_time(
                actor.id, service_job_id=job.id, user_id=actor.id,
                time_type="FAHRTZEIT",
                started_at=datetime(2026, 7, 13, 7, 30, tzinfo=dt_timezone.utc),
                ended_at=datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc),
            )
            einsatz_service.log_time(
                actor.id, service_job_id=job.id, user_id=actor.id,
                time_type="ARBEITSZEIT",
                started_at=datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc),
                ended_at=datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc),
                note="Sockelrisse geöffnet und verpresst.",
            )
            einsatz_service.log_material(
                actor.id, service_job_id=job.id,
                description="Injektionsharz 2K", quantity=Decimal("3.500"),
                unit="kg", recorded_by=actor.id,
            )
            job.refresh_from_db()
            angelegt += 1
            self.stdout.write(
                f"Einsatz angelegt: {job.job_number} ({job.status})"
            )

            # Einsatz 2: anstehend (nur geplant) — für Listen-Variation.
            job2 = einsatz_service.create_service_job(
                actor.id, work_order_id=ein_order.id,
                scheduled_start=datetime(2026, 7, 20, 9, 0, tzinfo=dt_timezone.utc),
                scheduled_end=datetime(2026, 7, 20, 13, 0, tzinfo=dt_timezone.utc),
                access_instructions="Nachkontrolle der verpressten Risse.",
            )
            einsatz_service.advance_status(
                actor.id, service_job_id=job2.id, to_status="GEPLANT"
            )
            einsatz_service.assign_user(
                actor.id, service_job_id=job2.id,
                assignee_user_id=actor.id, role="TECHNICIAN",
            )
            job2.refresh_from_db()
            angelegt += 1
            self.stdout.write(
                f"Einsatz angelegt: {job2.job_number} ({job2.status})"
            )

            # Kategorie + Ressource dem geplanten Einsatz zuordnen (falls noch
            # nicht gesetzt). Braucht die zuvor angelegten Stammdaten.
            vor_ort = AppointmentCategory.objects.filter(name="Vor-Ort-Termin").first()
            if vor_ort is not None and job2.appointment_category_id is None:
                planung_service.set_job_category(
                    actor.id, service_job_id=job2.id, category_id=vor_ort.id
                )
            crafter = Resource.objects.filter(name="VW Crafter (BN-MC 1234)").first()
            if crafter is not None and not job2.resource_links.exists():
                planung_service.assign_resource(
                    actor.id, service_job_id=job2.id, resource_id=crafter.id
                )

        # Freier Termin ohne Auftrag (Migration 0062): eine Begehung, die noch zu
        # keinem Auftrag gehört — und deren Kontakt bewusst NOCH NICHT gesetzt ist
        # (der Interessent ist noch kein Kontakt; er wird im Einsatz-Detail
        # nachgetragen). Damit zeigt die Plantafel beide Spielarten nebeneinander.
        # Idempotent über den Titel (Einsätze tragen sonst nur eine E-Nummer).
        BEGEHUNG_TITEL = "Begehung Kellerabdichtung (Interessent)"
        frei = ServiceJob.objects.filter(title=BEGEHUNG_TITEL).first()
        if frei is None:
            frei_obj = Property.objects.filter(
                name="Geschäftshaus Rheinpassage"
            ).first()
            frei = einsatz_service.create_service_job(
                actor.id,
                title=BEGEHUNG_TITEL,
                property_id=frei_obj.id if frei_obj is not None else None,
                scheduled_start=datetime(2026, 7, 15, 10, 0, tzinfo=dt_timezone.utc),
                scheduled_end=datetime(2026, 7, 15, 11, 30, tzinfo=dt_timezone.utc),
                access_instructions="Ortstermin mit dem Interessenten am Kellereingang.",
            )
            einsatz_service.advance_status(
                actor.id, service_job_id=frei.id, to_status="GEPLANT"
            )
            einsatz_service.assign_user(
                actor.id, service_job_id=frei.id,
                assignee_user_id=actor.id, role="LEAD",
            )
            begehung = AppointmentCategory.objects.filter(name="Begehung").first()
            if begehung is not None:
                planung_service.set_job_category(
                    actor.id, service_job_id=frei.id, category_id=begehung.id
                )
            frei.refresh_from_db()
            angelegt += 1
            self.stdout.write(
                f"Freier Termin angelegt: {frei.job_number} ({frei.title})"
            )

        # Begehungsprotokoll AM FREIEN TERMIN (Migration 0064): der Bericht hängt
        # allein am Einsatz — ohne Auftrag. Bewusst als ENTWURF: die
        # Kundenunterschrift besiegelt ihn unwiderruflich (und bräuchte den
        # Objektspeicher), der Seed darf keinen unumkehrbaren Zustand erzeugen und
        # nicht von MinIO abhängen. Fotos hängt man im UI an. Idempotent über den
        # Einsatzbezug.
        if not SiteReport.objects.filter(service_job_id=frei.id).exists():
            protokoll = site_report_service.create_report(
                actor.id,
                service_job_id=frei.id,
                report_date=date(2026, 7, 15),
                activity_text=(
                    "Kellergeschoss begangen. Feuchte Stellen an der Nordwand "
                    "(ca. 3 m²), Salzausblühungen unterhalb der Fensterbank. "
                    "Außenabdichtung vermutlich schadhaft."
                ),
                weather="bedeckt, 17 °C",
                hours_worked=Decimal("1.50"),
                remarks=(
                    "Interessent bittet um ein Angebot für die Außenabdichtung. "
                    "Noch kein Auftrag — dieser Bericht dokumentiert den Zustand "
                    "vor der Beauftragung."
                ),
            )
            angelegt += 1
            self.stdout.write(
                f"Begehungsprotokoll angelegt (freier Termin {frei.job_number}): "
                f"{protokoll.report_date}"
            )

        # Buchhaltung: Teilzahlung + erste Mahnstufe auf der veröffentlichten
        # Rechnung. Idempotent je Rechnung. Die Zahlung braucht kein due_date;
        # die Mahnung nur eine fällige Rechnung (issued_at > due_date, per DB-Tor
        # erzwungen). Auf einer Alt-DB ohne due_date entfällt die Mahn-Demo.
        bh_inv = (
            Invoice.objects.filter(
                work_order_id=pub_order.id, status="VEROEFFENTLICHT"
            ).first()
            if pub_order is not None
            else None
        )
        if bh_inv is not None:
            if not Payment.objects.filter(invoice_id=bh_inv.id).exists():
                buchhaltung_service.record_payment(
                    actor.id, invoice_id=bh_inv.id, amount=Decimal("3000.00"),
                    paid_at=date(2026, 6, 20), payment_type="TEILZAHLUNG",
                )
                angelegt += 1
                self.stdout.write(
                    f"Teilzahlung erfasst: {bh_inv.invoice_number} — 3000.00 EUR"
                )
            if (
                bh_inv.due_date is not None
                and not DunningNotice.objects.filter(invoice_id=bh_inv.id).exists()
            ):
                buchhaltung_service.issue_dunning_notice(
                    actor.id, invoice_id=bh_inv.id, level=1,
                    issued_at=date(2026, 6, 21),
                    note="Erste Zahlungserinnerung (Demo).",
                )
                angelegt += 1
                self.stdout.write(f"Mahnstufe 1 erzeugt: {bh_inv.invoice_number}")

            # Rechnungskorrektur (GUTSCHRIFT) der ersten Position — demonstriert
            # den GoBD-Folgebeleg (reference_invoice_id). Idempotent: nur, wenn
            # der Beleg noch keinen Korrektur-/Stornobeleg trägt.
            if not Invoice.objects.filter(reference_invoice_id=bh_inv.id).exists():
                gs = beleg_service.create_correction(
                    actor.id, invoice_id=bh_inv.id, positions=[1]
                )
                angelegt += 1
                self.stdout.write(
                    f"Rechnungskorrektur erzeugt: {gs.invoice_number} "
                    f"({gs.gross_total} EUR)"
                )

        # Wartung: jährlicher Wartungsvertrag mit bereits fälliger erster Wartung;
        # eine Auslösung erzeugt die Folge-Aufgabe und rückt die Fälligkeit vor.
        # Idempotent je Liegenschaft.
        wa_obj = Property.objects.filter(name="Geschäftshaus Rheinpassage").first()
        wa_party = Party.objects.filter(display_name="Thomas Bergmann").first()
        if (
            wa_obj is not None
            and not MaintenanceContract.objects.filter(property_id=wa_obj.id).exists()
        ):
            contract = wartung_service.create_contract(
                actor.id, property_id=wa_obj.id,
                name="Thermenwartung jährlich",
                start_date=date(2026, 6, 1),
                interval_kind="JAEHRLICH",
                due_action="AUFGABE",
                party_id=wa_party.id if wa_party else None,
                lead_time_days=14,
                notes="Jährliche Wartung der Gasthermen inkl. Abgasmessung.",
            )
            wartung_service.trigger_action(
                actor.id, contract_id=contract.id,
                note="Erste Wartung fällig — Folge-Aufgabe erzeugt (Demo).",
            )
            contract.refresh_from_db()
            angelegt += 1
            self.stdout.write(
                f"Wartungsvertrag angelegt: {contract.contract_number} "
                f"(nächste Fälligkeit {contract.next_due_date})"
            )

        angelegt += self._seed_faelligkeiten(actor)

        # Personal (hr.*): Mitarbeiter mit Login-Konto, Personalsatz, Vertrag,
        # Urlaubskonto und Abwesenheiten. Idempotent je Person (existiert bereits
        # ein Personalsatz zur Person, wird der Mitarbeiter übersprungen). Das
        # Login-Konto hat eine feste UUID, damit ein nach Teilerfolg abgebrochener
        # Lauf nicht mehrere Konten anlegt.
        current_year = date.today().year
        for emp in DEMO_EMPLOYEES:
            expected = f"{emp['person']['first_name']} {emp['person']['last_name']}"
            person = Party.objects.filter(display_name=expected).first()
            if person is not None and Employee.objects.filter(party_id=person.id).exists():
                uebersprungen += 1
                continue
            if person is None:
                person = identity_service.create_person(actor.id, **emp["person"])

            account, _ = AppUser.objects.get_or_create(
                id=emp["account_id"],
                defaults={
                    "display_name": f"{expected} (Login)",
                    "status": "ACTIVE",
                    "version": 1,
                },
            )
            employee = mitarbeiter_service.create_employee(
                actor.id,
                app_user_id=account.id,
                party_id=person.id,
                hired_on=emp["hired_on"],
            )
            mitarbeiter_service.create_contract(
                actor.id,
                employee_id=employee.id,
                valid_from=emp["hired_on"],
                hours=emp["hours"],
                vacation_days_per_year=emp["vacation_days"],
            )
            mitarbeiter_service.set_vacation_budget(
                actor.id,
                employee_id=employee.id,
                year=current_year,
                entitlement_days=emp["vacation_days"],
            )
            for ab in emp["absences"]:
                monday = _first_monday(current_year, ab["month"])
                absence = mitarbeiter_service.create_absence(
                    actor.id,
                    employee_id=employee.id,
                    absence_type="URLAUB",
                    start_date=monday,
                    end_date=monday + timedelta(days=ab["weekdays"] - 1),
                )
                mitarbeiter_service.submit_absence(actor.id, absence_id=absence.id)
                if ab["approve"]:
                    mitarbeiter_service.approve_absence(
                        actor.id, absence_id=absence.id, note="Genehmigt (Demo)."
                    )
            # Statuswechsel zuletzt: für einen Ausgetretenen lehnt der Service
            # sonst den Vertrag ab (Reihenfolge!).
            if emp["status"] != "AKTIV":
                mitarbeiter_service.set_employee_status(
                    actor.id,
                    employee_id=employee.id,
                    status=emp["status"],
                    left_on=emp.get("left_on"),
                )
            employee.refresh_from_db()
            angelegt += 1
            self.stdout.write(
                f"Mitarbeiter angelegt: {employee.employee_number} {expected} "
                f"({employee.status})"
            )

        # Zeiterfassung (workflow.time_entry/work_day, Migrationen 0066–0068):
        # zwei gestempelte Arbeitstage des Monteurs — einer bestätigt, einer
        # eingereicht und damit im Freigabekorb der Leitung. Idempotent: liegt
        # schon eine Zeitbuchung des Monteurs vor, wird der Block übersprungen.
        monteur_id = uuid.UUID("00000000-0000-4000-8000-000000000104")
        if (
            AppUser.objects.filter(id=monteur_id).exists()
            and not TimeEntry.objects.filter(user_id=monteur_id).exists()
        ):
            tz = ZoneInfo("Europe/Berlin")
            arbeit = TimeCategory.objects.get(code="ARBEITSZEIT")
            fahrt = TimeCategory.objects.get(code="FAHRTZEIT")

            def _t(tag, hh, mm=0):
                return datetime(tag.year, tag.month, tag.day, hh, mm, tzinfo=tz)

            werktage = []
            d = date.today() - timedelta(days=1)
            while len(werktage) < 2:
                if d.weekday() < 5:
                    werktage.append(d)
                d -= timedelta(days=1)
            werktage.sort()

            for i, tag in enumerate(werktage):
                zeit_service.zeiteintrag_anlegen(
                    monteur_id, user_id=monteur_id, category_id=fahrt.id,
                    started_at=_t(tag, 7, 15), ended_at=_t(tag, 8),
                    note="Anfahrt Baustelle",
                )
                zeit_service.zeiteintrag_anlegen(
                    monteur_id, user_id=monteur_id, category_id=arbeit.id,
                    started_at=_t(tag, 8), ended_at=_t(tag, 12),
                )
                zeit_service.zeiteintrag_anlegen(
                    monteur_id, user_id=monteur_id, category_id=arbeit.id,
                    started_at=_t(tag, 12, 30), ended_at=_t(tag, 16, 30),
                )
                work_day = WorkDay.objects.get(user_id=monteur_id, day=tag)
                # Gesetzliche Pause einrechnen (ArbZG § 4) und einreichen.
                zeit_service.pausen_regel_anwenden(
                    monteur_id, work_day_id=work_day.id
                )
                zeit_service.arbeitstag_einreichen(
                    monteur_id, work_day_id=work_day.id
                )
                if i == 0:
                    # Vier-Augen: der Akteur (Administration) ist nicht der
                    # Monteur — er darf bestätigen.
                    zeit_service.arbeitstag_bestaetigen(
                        actor.id, work_day_id=work_day.id
                    )
                angelegt += 1
                self.stdout.write(
                    f"Arbeitstag angelegt: {tag} "
                    f"({'BESTAETIGT' if i == 0 else 'EINGEREICHT'})"
                )
        else:
            uebersprungen += 1

        # Login-Konten (accounts.User) für die Seed-Mitarbeiter und ein Admin-
        # Konto. Jedes Konto ist über app_user_id mit einem security.app_user
        # verknüpft; die fachliche Rolle kommt über security.user_role. Passwörter
        # NUR bei settings.DEBUG (siehe _ensure_login). Idempotent über die
        # E-Mail-Existenz bzw. eine bereits gültige UserRole-Zeile.
        User = get_user_model()
        if settings.DEBUG:
            self.stdout.write(
                f"Seed-Logins erhalten das Dev-Passwort aus MCN_DEV_PASSWORD "
                f"(aktuell: {DEV_PASSWORD!r}) — nur DEBUG, nicht in Dateien ablegen."
            )
        # (E-Mail, app_user_id, Rolle, Admin-Rechte) — die drei Mitarbeiter plus Admin.
        seed_logins = [
            (emp["login_email"], emp["account_id"], emp["role"], False)
            for emp in DEMO_EMPLOYEES
        ]
        seed_logins.append((ADMIN_LOGIN_EMAIL, DEMO_APP_USER_ID, "ADMINISTRATION", True))

        for email, app_user_id, role_code, is_admin in seed_logins:
            if AppUser.objects.filter(id=app_user_id).first() is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Login '{email}' übersprungen: kein security.app_user "
                        f"{app_user_id} vorhanden."
                    )
                )
                continue
            if self._ensure_login(
                User, email=email, app_user_id=app_user_id,
                is_staff=is_admin, is_superuser=is_admin,
            ):
                angelegt += 1
            else:
                uebersprungen += 1
            if self._ensure_role(actor, app_user_id=app_user_id, role_code=role_code):
                angelegt += 1
            else:
                uebersprungen += 1

        # ===================================================================
        # Plantafel-Demodaten (Disposition)
        # ===================================================================
        # Die Plantafel zeigt standardmäßig die LAUFENDE Woche — feste Datums-
        # angaben (wie im übrigen Seed) lägen dort nie im Bild. Diese Daten sind
        # deshalb RELATIV zu heute und decken genau die drei Dinge ab, die man am
        # Board sehen können muss:
        #   * Rückstand   — ungeplante Einsätze, die man ins Raster zieht,
        #   * Mehrtages-Balken — ein Einsatz über drei Tage,
        #   * Sperrfläche — eine genehmigte Abwesenheit im sichtbaren Zeitraum.
        # Idempotent über die Titel (der Einsatztitel ist im Demo-Datensatz
        # eindeutig).
        angelegt += self._seed_plantafel(actor)

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_demo fertig: {angelegt} angelegt, {uebersprungen} übersprungen."
            )
        )

    def _seed_plantafel(self, actor):
        """Rückstand, Mehrtages-Einsatz und Abwesenheit im Board-Zeitraum."""
        angelegt = 0
        order = (
            WorkOrder.objects.filter(status="IN_AUSFUEHRUNG")
            .order_by("created_at")
            .first()
        )
        if order is None:
            return 0

        # Der Board-Anker ist der Montag DIESER Woche (wie im Frontend).
        heute = date.today()
        montag = heute - timedelta(days=heute.weekday())

        def _utc(tag, stunde, minute=0):
            return datetime(
                tag.year, tag.month, tag.day, stunde, minute, tzinfo=dt_timezone.utc
            )

        # --- Rückstand: ungeplante Einsätze (kein scheduled_start) -----------
        rueckstand = [
            ("Heizungswartung Dachgeschoss (noch zu terminieren)", "Wartung"),
            ("Silikonfugen Bad 3. OG erneuern", None),
            ("Rückstau-Klappe prüfen — Keller", None),
        ]
        for titel, kat_name in rueckstand:
            if ServiceJob.objects.filter(title=titel).exists():
                continue
            kat = (
                AppointmentCategory.objects.filter(name=kat_name, status="AKTIV").first()
                if kat_name
                else None
            )
            job = einsatz_service.create_service_job(
                actor.id,
                work_order_id=order.id,
                title=titel,
                appointment_category_id=kat.id if kat else None,
            )
            angelegt += 1
            self.stdout.write(
                f"Rückstand (ungeplant): {job.job_number} — {titel}"
            )

        # --- Mehrtägiger Einsatz (Mi–Fr dieser Woche) ------------------------
        MEHRTAGES_TITEL = "Gerüst stellen und Fassade reinigen (3 Tage)"
        if not ServiceJob.objects.filter(title=MEHRTAGES_TITEL).exists():
            mittwoch = montag + timedelta(days=2)
            freitag = montag + timedelta(days=4)
            lang = planung_service.create_termin(
                actor.id,
                work_order_id=order.id,
                title=MEHRTAGES_TITEL,
                scheduled_start=_utc(mittwoch, 7),
                scheduled_end=_utc(freitag, 16),
                assignee_ids=[actor.id],
            )
            angelegt += 1
            self.stdout.write(
                f"Mehrtägiger Einsatz: {lang.job_number} "
                f"({mittwoch} 07:00 – {freitag} 16:00)"
            )

        # --- Termin OHNE Ende (zeigt den Konflikt „Kein Ende gepflegt") ------
        OFFEN_TITEL = "Kurzeinsatz ohne Endzeit (Beispiel)"
        if not ServiceJob.objects.filter(title=OFFEN_TITEL).exists():
            offen = planung_service.create_termin(
                actor.id,
                work_order_id=order.id,
                title=OFFEN_TITEL,
                scheduled_start=_utc(montag + timedelta(days=1), 13),
                assignee_ids=[actor.id],
            )
            angelegt += 1
            self.stdout.write(f"Termin ohne Ende: {offen.job_number}")

        # --- Genehmigte Abwesenheit IM Board-Zeitraum ------------------------
        # Ohne sie plant der Disponent auf einen Urlauber — bei Hero steht die
        # Abwesenheit im Board, deshalb muss sie auch hier sichtbar sein.
        # Bevorzugt der Monteur (Timo Kalinski); notfalls der Seed-Akteur selbst,
        # damit die Sperrfläche IMMER in einer Bahn liegt, die auch Termine trägt
        # (sonst sähe man die Sperre, aber nie den Konflikt).
        monteur = Employee.objects.filter(
            app_user_id=uuid.UUID("00000000-0000-4000-8000-000000000104")
        ).first() or Employee.objects.filter(app_user_id=actor.id).first()
        if monteur is not None:
            von = montag + timedelta(days=3)   # Donnerstag
            bis = montag + timedelta(days=4)   # Freitag
            schon_da = Absence.objects.filter(
                employee_id=monteur.id, start_date=von, end_date=bis
            ).exists()
            if not schon_da:
                ab = mitarbeiter_service.create_absence(
                    actor.id,
                    employee_id=monteur.id,
                    absence_type="URLAUB",
                    start_date=von,
                    end_date=bis,
                    reason="Brückentage (Demo).",
                )
                mitarbeiter_service.submit_absence(actor.id, absence_id=ab.id)
                mitarbeiter_service.approve_absence(
                    actor.id, absence_id=ab.id, note="Genehmigt (Demo)."
                )
                angelegt += 1
                self.stdout.write(
                    f"Abwesenheit im Board-Zeitraum: {von} – {bis} (Timo Kalinski)"
                )

                # Und ein Termin GENAU DARAUF — so ist der Konflikt „Termin auf
                # Abwesenheit" im Board sofort sichtbar (er blockiert nichts).
                # Der Titel nennt die Abwesenheits-ART bewusst NICHT: Er steht im
                # Board-Payload, den auch ein Disponent ohne hr-Recht liest — und
                # er wäre der einzige Weg, auf dem die Art (Gesundheitsdatum,
                # DSGVO Art. 9) doch noch dort landet. Was das Board zeigen soll,
                # ist der Konflikt selbst, nicht sein Grund.
                KONFLIKT_TITEL = "Nachkontrolle (kollidiert mit Abwesenheit — Demo)"
                if not ServiceJob.objects.filter(title=KONFLIKT_TITEL).exists():
                    kollision = planung_service.create_termin(
                        actor.id,
                        work_order_id=order.id,
                        title=KONFLIKT_TITEL,
                        scheduled_start=_utc(von, 9),
                        scheduled_end=_utc(von, 12),
                        assignee_ids=[monteur.app_user_id],
                    )
                    angelegt += 1
                    self.stdout.write(
                        f"Konflikt-Demo (Termin auf Abwesenheit): {kollision.job_number}"
                    )
        return angelegt

    def _ensure_demo_user(self):
        """Idempotenter Demo-app_user über feste UUID; kein Trigger verlangt hier
        app.current_user_id, daher einfacher atomarer Insert (Bootstrapping)."""
        with transaction.atomic():
            return AppUser.objects.get_or_create(
                id=DEMO_APP_USER_ID,
                defaults={
                    "display_name": DEMO_APP_USER_NAME,
                    "status": "ACTIVE",
                    "version": 1,
                },
            )

    def _ensure_login(self, User, *, email, app_user_id, is_staff=False,
                      is_superuser=False):
        """Legt ein Login-Konto (accounts.User) idempotent an. Rückgabe: True, wenn
        neu angelegt, sonst False.

        Idempotenz über die E-Mail (case-insensitiv, entspricht dem
        UniqueConstraint auf Lower('email')). Das Passwort wird NUR bei
        settings.DEBUG gesetzt (Wegwerf-Dev-Passwort aus MCN_DEV_PASSWORD) und
        ausschließlich auf stdout ausgegeben, nie in eine Datei geschrieben. Ohne
        DEBUG bleibt das Konto ohne nutzbares Passwort (manage.py changepassword).
        """
        if User.objects.filter(email__iexact=email).exists():
            return False
        # username bleibt technisches Pflichtfeld von AbstractUser; wir nehmen die
        # E-Mail (angemeldet wird sich ohnehin über die E-Mail, EmailBackend).
        user = User(username=email, email=email, app_user_id=app_user_id,
                    is_staff=is_staff, is_superuser=is_superuser)
        if settings.DEBUG:
            user.set_password(DEV_PASSWORD)
        else:
            user.set_unusable_password()
        user.save()
        if settings.DEBUG:
            self.stdout.write(f"Login angelegt: {email} (Passwort gesetzt, s. o.)")
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Login angelegt: {email} — Passwort NICHT gesetzt (kein DEBUG); "
                    f"bitte 'manage.py changepassword {email}' nutzen."
                )
            )
        return True

    def _seed_faelligkeiten(self, actor):
        """Fälligkeiten-Engine (Migration 0071): Prüffristen + Gewährleistung +
        ein Scheduler-Lauf, damit „Was steht an?" nicht leer ist.

        Idempotent: die Prüfungen werden je (Liegenschaft, Prüfart) nur einmal
        angelegt, die Gewährleistung je Auftrag nur einmal (DB-UNIQUE), und der
        Generierungslauf ist von Haus aus idempotent (UNIQUE-Indizes auf
        maintenance.due_item).
        """
        angelegt = 0
        heute = date.today()

        # --- Ein zweiter Wartungsvertrag, dessen Fälligkeit NOCH offen ist ----
        # Der erste (oben) wird im Seed sofort ausgelöst und steht damit ein Jahr
        # in der Zukunft. Damit „Was steht an?" auch eine WARTUNG zeigt, kommt
        # hier ein Vertrag dazu, der in 10 Tagen fällig ist (Vorlauf 30 → jetzt
        # sichtbar) und NICHT ausgelöst wird — der Mensch entscheidet.
        lh_obj = Property.objects.filter(name="Wohnanlage Lindenhöfe").first()
        if lh_obj is not None and not MaintenanceContract.objects.filter(
            property_id=lh_obj.id
        ).exists():
            c2 = wartung_service.create_contract(
                actor.id, property_id=lh_obj.id,
                name="Heizungsanlage Jahreswartung",
                start_date=heute + timedelta(days=10),
                interval_kind="JAEHRLICH",
                due_action="AUFTRAG",
                lead_time_days=30,
                notes="Demo: fällig in 10 Tagen, Vorlauf 30 Tage.",
            )
            angelegt += 1
            self.stdout.write(
                f"Wartungsvertrag angelegt: {c2.contract_number} "
                f"(offene Fälligkeit {c2.next_due_date})"
            )

        # --- Prüffristen an zwei Objekten (OHNE Wartungsvertrag) --------------
        # Bewusst mit den ausgelieferten Prüfart-VORSCHLÄGEN — sie sind ein
        # Startpunkt, kein Normkatalog und keine Rechtsauskunft.
        pruef_plan = [
            ("Wohnanlage Lindenhöfe", "Trinkwasser: Legionellenprüfung", -5),
            ("Wohnanlage Lindenhöfe", "Rauchwarnmelder prüfen", 40),
            ("Geschäftshaus Rheinpassage", "Schornsteinfeger / Feuerstättenschau", 12),
        ]
        for obj_name, art_name, tage in pruef_plan:
            obj = Property.objects.filter(name=obj_name).first()
            art = InspectionType.objects.filter(name=art_name).first()
            if obj is None or art is None:
                continue
            if Inspection.objects.filter(
                property_id=obj.id, inspection_type_id=art.id
            ).exists():
                continue
            p = pruefung_service.create_inspection(
                actor.id,
                inspection_type_id=art.id,
                property_id=obj.id,
                start_date=heute + timedelta(days=tage),
                notes="Demo-Prüffrist. Intervall und Zuständigkeit prüft der "
                      "Betrieb selbst — keine Rechtsauskunft.",
            )
            angelegt += 1
            self.stdout.write(
                f"Prüffrist angelegt: {p.name} @ {obj_name} "
                f"(fällig {p.next_due_date})"
            )

        # --- Gewährleistung am durchgeschalteten Auftrag ----------------------
        order = (
            WorkOrder.objects.filter(
                status__in=gewaehrleistung_service.ABGESCHLOSSEN
            )
            .order_by("created_at")
            .first()
        )
        if order is not None and not Warranty.objects.filter(
            work_order_id=order.id
        ).exists():
            # Frist läuft in 45 Tagen ab, Vorlauf 90 → JETZT im Vorlauf sichtbar.
            # is_machinery=True → der Vertriebshinweis („Anlage ohne
            # Wartungsvertrag") greift, sofern kein aktiver Vertrag am Objekt hängt.
            start = heute - timedelta(days=45) - timedelta(days=365 * 5)
            w = gewaehrleistung_service.create_warranty(
                actor.id,
                work_order_id=order.id,
                start_date=start,
                duration_months=60,
                lead_time_days=90,
                basis="BGB",
                is_machinery=True,
                notes="Demo-Gewährleistung. Die Frist ist je Auftrag einstellbar; "
                      "das Produkt gibt keine Rechtsauskunft.",
            )
            angelegt += 1
            self.stdout.write(
                f"Gewährleistung angelegt: Auftrag {order.order_number}, "
                f"läuft ab {w.end_date}"
            )

        # --- Ein Scheduler-Lauf: Fälligkeiten erzeugen ------------------------
        ergebnis = faelligkeit_service.generiere(actor.id, stichtag=heute)
        neu = sum(len(v) for v in ergebnis.values())
        if neu:
            angelegt += neu
            self.stdout.write(
                "Fälligkeiten erzeugt: "
                + ", ".join(f"{k} {len(v)}" for k, v in ergebnis.items() if v)
            )
        return angelegt

    def _ensure_role(self, actor, *, app_user_id, role_code):
        """Weist einem app_user eine Rolle über security.user_role zu (idempotent).
        Rückgabe: True, wenn neu angelegt, sonst False.

        user_role ist eine Fachtabelle (security-Schema) → Anlage über
        business_transaction (Benutzerkontext/Audit). granted_by ist NOT NULL
        (Migration 0026) und wird auf den Seed-Akteur gesetzt. Idempotenz über
        eine bereits heute gültige Zuordnung derselben Rolle (das EXCLUDE der
        Tabelle verböte eine zeitgleiche Doppelzuordnung ohnehin).
        """
        today = date.today()
        exists = (
            UserRole.objects.filter(user_id=app_user_id, role_id=role_code,
                                    valid_from__lte=today)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=today))
            .exists()
        )
        if exists:
            return False
        with business_transaction(actor.id):
            UserRole.objects.create(
                id=uuid.uuid4(), user_id=app_user_id, role_id=role_code,
                valid_from=today, granted_by_id=actor.id,
            )
        self.stdout.write(f"Rolle zugewiesen: {role_code} -> app_user {app_user_id}")
        return True
