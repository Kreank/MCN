"""seed_demo — Demodaten für die Entwicklung (Identity).

Legt idempotent einen Demo-Sachbearbeiter (security.app_user) und eine
Handvoll realistischer Parties (Personen/Organisationen) an. Läuft
ausschließlich mit settings.DEBUG; auf produktiven Umgebungen bricht der
Befehl ab. Alle Party-Writes gehen über die Service-Schicht, also durch
business_transaction (Benutzerkontext, Audit).
"""
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from db_core.models import AppUser, Party
from db_core.services import identity as identity_service

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


class Command(BaseCommand):
    help = "Legt Demo-Kontakte (Personen/Organisationen) für die Entwicklung an."

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
