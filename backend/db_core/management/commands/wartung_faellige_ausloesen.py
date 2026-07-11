"""Wartungs-Fälligkeits-Scheduler: löst alle fälligen Wartungsverträge aus.

Das Kernversprechen des Wartungsmoduls: fällige Verträge lösen ihre konfigurierte
Aktion (AUFGABE/PROJEKT/AUFTRAG/BENACHRICHTIGUNG) automatisch aus. Dieses Command
IST der Scheduler — es wird extern per Cron täglich aufgerufen (kein eigener
Worker im Repo). Es findet alle AKTIVEN Verträge mit next_due_date <= Stichtag
und ruft für jeden wartung.trigger_action auf.

Aufruf:

    # Trockenlauf (zeigt nur, schreibt nichts):
    uv run python manage.py wartung_faellige_ausloesen --dry-run

    # Echter Lauf für heute:
    uv run python manage.py wartung_faellige_ausloesen

    # Für einen bestimmten Stichtag (z. B. Nacharbeiten):
    uv run python manage.py wartung_faellige_ausloesen --stichtag 2026-07-11

Cron-Eintrag (täglich 06:00), den ein Admin einträgt — siehe Slice-Report.

Robustheit: Ein Fehler bei einem Vertrag bricht den Lauf NICHT ab; er wird gezählt
und gemeldet, der Rest läuft weiter (jeder Vertrag in eigener Transaktion über
trigger_action). Idempotenz: trigger_action rückt next_due_date über den Stichtag
hinaus vor (catch_up), sodass ein zweiter Lauf am selben Stichtag denselben Vertrag
nicht erneut auslöst. Mehrfach verpasste Intervalle werden EINMAL ausgelöst (kein
Nachhol-Sturm) und der Plan auf den nächsten künftigen Termin gesetzt.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from db_core.models import AppUser, MaintenanceContract
from db_core.services import wartung as wartung_service


class Command(BaseCommand):
    help = "Löst fällige Wartungsverträge aus (Scheduler; täglich per Cron)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--stichtag",
            help="Stichtag YYYY-MM-DD (Default: heute). Verträge mit "
            "next_due_date <= Stichtag gelten als fällig.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur anzeigen, was ausgelöst würde — schreibt nichts.",
        )
        parser.add_argument(
            "--actor",
            help="app_user-UUID als Auslöser (Default: erster aktiver Account). "
            "Der Akteur wird für Audit/created_by der Folgeobjekte gesetzt.",
        )

    def handle(self, *args, **opts):
        stichtag = self._stichtag(opts.get("stichtag"))
        dry_run = opts["dry_run"]

        faellige = list(
            MaintenanceContract.objects.filter(
                status="AKTIV",
                next_due_date__isnull=False,
                next_due_date__lte=stichtag,
            ).order_by("next_due_date", "contract_number")
        )

        modus = "TROCKENLAUF" if dry_run else "LAUF"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Wartungs-Scheduler [{modus}] - Stichtag {stichtag:%d.%m.%Y}"
            )
        )
        self.stdout.write(f"  Fällige aktive Verträge: {len(faellige)}")
        self.stdout.write("")

        if not faellige:
            self.stdout.write(self.style.SUCCESS("  Nichts fällig — nichts zu tun."))
            return

        actor = None
        if not dry_run:
            actor = self._actor(opts.get("actor"))
            self.stdout.write(f"  Auslöser: {actor.display_name} ({actor.id})")
            self.stdout.write("")

        ausgeloest = fehler = 0
        for contract in faellige:
            kopf = (
                f"  {contract.contract_number} | {contract.name} | "
                f"fällig {contract.next_due_date:%d.%m.%Y} | {contract.due_action}"
            )
            if dry_run:
                self.stdout.write(f"{kopf}  -> würde ausgelöst")
                ausgeloest += 1
                continue
            try:
                event, aktualisiert = wartung_service.trigger_action(
                    actor.id,
                    contract_id=contract.id,
                    note=f"Automatische Auslösung durch Scheduler "
                    f"(Stichtag {stichtag.isoformat()}).",
                    catch_up_until=stichtag,
                )
                ausgeloest += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{kopf}  -> {self._folge(event)}; "
                        f"nächste Fälligkeit "
                        f"{self._datum(aktualisiert.next_due_date)}"
                    )
                )
            except Exception as exc:  # ein Vertrag darf den Lauf nicht abbrechen
                fehler += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"{kopf}  -> FEHLER: {exc.__class__.__name__}: {exc}"
                    )
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Summe: {ausgeloest} ausgelöst, {fehler} Fehler "
                f"(von {len(faellige)} fällig)"
            )
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING("  Trockenlauf — es wurde nichts geschrieben.")
            )

    # ------------------------------------------------------------------

    def _stichtag(self, roh):
        if not roh:
            return date.today()
        stichtag = parse_date(roh)
        if stichtag is None:
            raise CommandError(f"--stichtag '{roh}' ist kein gültiges Datum (YYYY-MM-DD).")
        return stichtag

    def _actor(self, roh):
        if roh:
            actor = AppUser.objects.filter(id=roh, status="ACTIVE").first()
            if actor is None:
                raise CommandError(f"Kein aktiver security.app_user mit id '{roh}'.")
            return actor
        actor = AppUser.objects.filter(status="ACTIVE").order_by("created_at").first()
        if actor is None:
            raise CommandError("Kein aktiver security.app_user als Auslöser gefunden.")
        return actor

    def _folge(self, event):
        """Textbaustein, welches Folgeobjekt entstand."""
        if event.result_object_type:
            return f"{event.action} -> {event.result_object_type} {event.result_object_id}"
        return f"{event.action} -> protokolliert (kein Folgeobjekt)"

    def _datum(self, d):
        return d.strftime("%d.%m.%Y") if d else "- (keine weitere)"
