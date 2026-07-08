"""seed_demo — Demodaten für die Entwicklung (Identity + Liegenschaften).

Legt idempotent einen Demo-Sachbearbeiter (security.app_user), eine Handvoll
realistischer Parties (Personen/Organisationen) sowie ein paar Liegenschaften
mit Adresse, Gebäude/Einheiten und Party-Rollen an. Läuft ausschließlich mit
settings.DEBUG; auf produktiven Umgebungen bricht der Befehl ab. Alle Writes
gehen über die Service-Schicht, also durch business_transaction
(Benutzerkontext, Audit).
"""
import uuid
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from db_core.models import (
    AppUser, Article, Assembly, Invoice, Party, Project, ProjectLog, Property,
    Quote, Task, WageGroup, WorkOrder,
)
from db_core.services import artikel as artikel_service
from db_core.services import aufgabe as aufgabe_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
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


class Command(BaseCommand):
    help = (
        "Legt Demo-Kontakte, -Liegenschaften, -Projekte (mit Vorgängen), "
        "-Aufgaben, -Angebote und -Artikelstamm für die Entwicklung an."
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
