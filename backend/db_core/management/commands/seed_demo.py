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

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.models import (
    AppUser, Article, ArticleSalePrice, Assembly, DunningNotice, Employee,
    Invoice, MaintenanceContract, Party, Payment, Project, ProjectLog, Property,
    Quote, SalePriceGroup, Task, UserRole, WageGroup, WorkOrder,
)
from db_core.services import artikel as artikel_service
from db_core.services import aufgabe as aufgabe_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import einsatz as einsatz_service
from db_core.services import wartung as wartung_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service

# Fester Namespace → wiederholbare Aufrufe treffen denselben Demo-Benutzer.
DEMO_APP_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEMO_APP_USER_NAME = "Demo Sachbearbeiter"

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

        for art in Article.objects.filter(status="AKTIV", list_price__isnull=False):
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

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_demo fertig: {angelegt} angelegt, {uebersprungen} übersprungen."
            )
        )

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
