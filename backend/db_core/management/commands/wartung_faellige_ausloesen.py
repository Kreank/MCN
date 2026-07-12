"""Fälligkeiten-Scheduler — erzeugt Fälligkeiten und löst fällige Wartungen aus.

Das Command hieß ursprünglich nur „Wartungs-Fälligkeits-Scheduler" und löste
fällige Wartungsverträge aus. Es ist jetzt der Scheduler der gesamten
**Fälligkeiten-Engine** (Migration 0071) und läuft in zwei Phasen. Der Name
bleibt, damit bestehende Cron-Einträge weiter greifen.

## Phase 1 — Fälligkeiten erzeugen (alle drei Arten)

Legt `maintenance.due_item`-Einträge **im Voraus** an, sobald der Vorlauf
beginnt (`heute >= due_date - lead_time_days`):

  WARTUNG          aus aktiven Wartungsverträgen
  PRUEFUNG         aus aktiven Prüfungen (Prüffristen an Objekt/Anlage)
  GEWAEHRLEISTUNG  aus laufenden Gewährleistungen (Fristablauf)

**Idempotent.** Zweimal laufen erzeugt nichts doppelt — nicht weil dieser Code
aufpasst, sondern weil drei partielle UNIQUE-Indizes über (Anker, Fälligkeits-
datum) das physisch verbieten. Die Indizes sind statusunabhängig, deshalb kann
auch ein **verworfener** Eintrag nicht wieder auferstehen.

## Phase 2 — Vollautomatik für Wartungsverträge (Bestandsverhalten)

Löst wie bisher jeden AKTIVEN Vertrag mit `next_due_date <= Stichtag` aus:
`wartung.trigger_action` erzeugt das konfigurierte Folgeobjekt (AUFGABE →
workflow.task, PROJEKT → workflow.project, AUFTRAG → workflow.work_order),
protokolliert append-only, rückt die Fälligkeit vor **und schließt die zugehörige
due_item mit** — es gibt nur eine Wahrheit.

Prüfungen und Gewährleistungen werden **bewusst NICHT automatisch ausgelöst**:
ob aus einer ablaufenden Gewährleistung ein Termin, ein Angebot oder gar nichts
wird, ist eine Entscheidung, keine Mechanik. Sie erscheinen in der Fälligkeiten-
Ansicht („Was steht an?"), und ein Mensch entscheidet dort.

Mit `--nur-erzeugen` bleibt auch die Wartung beim Menschen.

## Aufruf

    uv run python manage.py wartung_faellige_ausloesen --dry-run
    uv run python manage.py wartung_faellige_ausloesen
    uv run python manage.py wartung_faellige_ausloesen --stichtag 2026-07-11
    uv run python manage.py wartung_faellige_ausloesen --nur-erzeugen

Robustheit: Ein Fehler bei einem Vertrag bricht den Lauf NICHT ab; er wird
gezählt und gemeldet, der Rest läuft weiter.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from db_core.models import AppUser, MaintenanceContract
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services import wartung as wartung_service

ART_LABELS = {
    "WARTUNG": "Wartung",
    "PRUEFUNG": "Prüfung",
    "GEWAEHRLEISTUNG": "Gewährleistung",
}


class Command(BaseCommand):
    help = (
        "Fälligkeiten-Scheduler: erzeugt Fälligkeiten (Wartung/Prüfung/"
        "Gewährleistung) im Vorlauf und löst fällige Wartungsverträge aus."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--stichtag",
            help="Stichtag YYYY-MM-DD (Default: heute). Verträge mit "
            "next_due_date <= Stichtag gelten als fällig; Fälligkeiten entstehen, "
            "sobald ihr Vorlauf am Stichtag begonnen hat.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur anzeigen, was geschähe — schreibt nichts.",
        )
        parser.add_argument(
            "--nur-erzeugen",
            action="store_true",
            dest="nur_erzeugen",
            help="Phase 2 überspringen: Fälligkeiten nur erzeugen, keine "
            "Wartungsverträge automatisch auslösen (der Mensch entscheidet in "
            "der Fälligkeiten-Ansicht).",
        )
        parser.add_argument(
            "--actor",
            help="app_user-UUID als Auslöser (Default: erster aktiver Account). "
            "Der Akteur wird für Audit/created_by der Folgeobjekte gesetzt.",
        )

    def handle(self, *args, **opts):
        stichtag = self._stichtag(opts.get("stichtag"))
        dry_run = opts["dry_run"]
        nur_erzeugen = opts.get("nur_erzeugen", False)

        modus = "TROCKENLAUF" if dry_run else "LAUF"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fälligkeiten-Scheduler [{modus}] - Stichtag {stichtag:%d.%m.%Y}"
            )
        )

        actor = None
        if not dry_run:
            actor = self._actor(opts.get("actor"))
            self.stdout.write(f"  Auslöser: {actor.display_name} ({actor.id})")
        self.stdout.write("")

        erzeugt = self._phase_erzeugen(actor, stichtag, dry_run)
        ausgeloest = fehler = faellig = 0
        if not nur_erzeugen:
            ausgeloest, fehler, faellig = self._phase_ausloesen(
                actor, stichtag, dry_run
            )

        self.stdout.write("")
        teile = [f"{erzeugt} Fälligkeit(en) erzeugt"]
        if nur_erzeugen:
            teile.append("Auslösung übersprungen (--nur-erzeugen)")
        else:
            teile.append(
                f"{ausgeloest} Wartung(en) ausgelöst, {fehler} Fehler "
                f"(von {faellig} fällig)"
            )
        self.stdout.write(self.style.MIGRATE_HEADING("Summe: " + "; ".join(teile)))
        if dry_run:
            self.stdout.write(
                self.style.WARNING("  Trockenlauf — es wurde nichts geschrieben.")
            )

    # ------------------------------------------------------------------
    # Phase 1 — erzeugen
    # ------------------------------------------------------------------

    def _phase_erzeugen(self, actor, stichtag, dry_run):
        self.stdout.write(
            self.style.MIGRATE_LABEL("  Phase 1: Fälligkeiten erzeugen (Vorlauf)")
        )
        if dry_run:
            self.stdout.write(
                "    (Trockenlauf: es wird nichts angelegt. Der Lauf ist "
                "idempotent — ein zweiter Lauf erzeugt keine Dubletten.)"
            )
            self.stdout.write("")
            return 0

        ergebnis = faelligkeit_service.generiere(actor.id, stichtag=stichtag)
        gesamt = 0
        for art, items in ergebnis.items():
            gesamt += len(items)
            self.stdout.write(f"    {ART_LABELS.get(art, art):16s} {len(items):3d} neu")
            for item in items:
                self.stdout.write(
                    f"      + {item.due_date:%d.%m.%Y}  {item.title}"
                )
        if gesamt == 0:
            self.stdout.write(
                "    Nichts Neues (bereits erzeugt oder noch außerhalb des Vorlaufs)."
            )
        self.stdout.write("")
        return gesamt

    # ------------------------------------------------------------------
    # Phase 2 — Wartungsverträge auslösen (Bestandsverhalten)
    # ------------------------------------------------------------------

    def _phase_ausloesen(self, actor, stichtag, dry_run):
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                "  Phase 2: fällige Wartungsverträge auslösen"
            )
        )
        faellige = list(
            MaintenanceContract.objects.filter(
                status="AKTIV",
                next_due_date__isnull=False,
                next_due_date__lte=stichtag,
            ).order_by("next_due_date", "contract_number")
        )
        self.stdout.write(f"    Fällige aktive Verträge: {len(faellige)}")
        if not faellige:
            self.stdout.write(self.style.SUCCESS("    Nichts fällig — nichts zu tun."))
            return 0, 0, 0

        ausgeloest = fehler = 0
        for contract in faellige:
            kopf = (
                f"    {contract.contract_number} | {contract.name} | "
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
        return ausgeloest, fehler, len(faellige)

    # ------------------------------------------------------------------

    def _stichtag(self, roh):
        if not roh:
            return date.today()
        stichtag = parse_date(roh)
        if stichtag is None:
            raise CommandError(
                f"--stichtag '{roh}' ist kein gültiges Datum (YYYY-MM-DD)."
            )
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
            return (
                f"{event.action} -> {event.result_object_type} "
                f"{event.result_object_id}"
            )
        return f"{event.action} -> protokolliert (kein Folgeobjekt)"

    def _datum(self, d):
        return d.strftime("%d.%m.%Y") if d else "- (keine weitere)"
