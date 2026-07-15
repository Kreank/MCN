"""KI-Werkzeug-Queue-Tick — ein Durchlauf der Tool-Call-Queue.

Wird vom `queue-worker`-Dienst (deploy/) im schnellen Loop aufgerufen: reapt stale
Calls (Worker-Abbrüche), pollt fällige pending-Calls, claimt+dispatcht neue. Die
Idempotenz garantiert die DB (`SELECT … FOR UPDATE SKIP LOCKED` + Lease) — zwei
gleichzeitige Ticks greifen NIE denselben Call, ein Fehler bei einem Call bricht den
Tick nicht ab (die Fehlerklassifikation der Runtime fängt ihn).

    uv run python manage.py ki_tool_queue_tick
    uv run python manage.py ki_tool_queue_tick --actor <uuid> --limit 20
"""
from django.core.management.base import BaseCommand, CommandError

from db_core.ai import runtime
from db_core.models import AppUser


class Command(BaseCommand):
    help = "Ein Durchlauf der KI-Werkzeug-Queue (reap/poll/claim/dispatch)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--actor",
            help="app_user-UUID als Akteur der Runtime-Writes (Default: erster "
            "aktiver Account). Empfohlen: ein eigener KI-Service-Account.",
        )
        parser.add_argument(
            "--limit", type=int, default=10, help="Max. neue Calls pro Tick (Default 10)."
        )

    def handle(self, *args, **opts):
        actor = self._actor(opts.get("actor"))
        summary = runtime.tick(actor.id, claim_limit=opts["limit"])
        if summary["reaped"] or summary["polled"] or summary["dispatched"]:
            self.stdout.write(
                f"Tick: {summary['dispatched']} dispatcht, "
                f"{summary['polled']} gepollt, {summary['reaped']} reaped."
            )

    def _actor(self, roh):
        if roh:
            actor = AppUser.objects.filter(id=roh, status="ACTIVE").first()
            if actor is None:
                raise CommandError(f"Kein aktiver security.app_user mit id '{roh}'.")
            return actor
        actor = AppUser.objects.filter(status="ACTIVE").order_by("created_at").first()
        if actor is None:
            raise CommandError("Kein aktiver security.app_user als Akteur gefunden.")
        return actor
