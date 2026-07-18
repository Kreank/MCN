"""Räumt alte Zeilen aus security.login_throttle weg (Prune).

Der Login-Drosselzähler (Migration 0116) ist transienter Zustand. Ohne Prune
wüchse die Tabelle mit jedem je gesehenen (Konto,IP)-Paar. Dieser Lauf entfernt
Zeilen, die länger als `--older-than-seconds` unberührt und nicht (mehr) gesperrt
sind. Für den täglichen Scheduler gedacht:

    uv run python manage.py login_throttle_aufraeumen
    uv run python manage.py login_throttle_aufraeumen --older-than-seconds 3600
"""
from django.core.management.base import BaseCommand

from db_core.services import login_schutz


class Command(BaseCommand):
    help = "Entfernt alte, nicht gesperrte Zeilen aus security.login_throttle."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-seconds", type=int, default=86400,
            help="Zeilen, die länger als so viele Sekunden unberührt sind, "
                 "werden entfernt (Default 86400 = 1 Tag).",
        )

    def handle(self, *args, **opts):
        entfernt = login_schutz.prune(opts["older_than_seconds"])
        if entfernt:
            self.stdout.write(f"{entfernt} Zeile(n) aus login_throttle entfernt.")
