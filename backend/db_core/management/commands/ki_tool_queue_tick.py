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

from db_core.ai import engine, proposal, runtime
from db_core.ai import workflow_sprachmemo  # noqa: F401 — registriert den v1-Workflow
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
        parser.add_argument(
            "--wf-stale-seconds", type=int, default=900,
            help="Ab welcher RUNNING-Dauer ein Workflow als hängend gilt und auf "
                 "FAILED gesetzt wird (Default 900). MUSS über der längsten "
                 "Handler-Laufzeit — einem LLM-Aufruf — liegen.",
        )

    def handle(self, *args, **opts):
        actor = self._actor(opts.get("actor"))
        # Ein Tick treibt vier Dinge: die Werkzeug-Queue, die Wiederaufnahme
        # wartender Workflows, den Reaper hängender RUNNING-Workflows und den
        # Ablauf-Sweep offener Vorschläge.
        summary = runtime.tick(actor.id, claim_limit=opts["limit"])
        resumed = engine.resume_ready(actor.id)
        reaped_wf = engine.reap_stale_workflows(
            actor.id, older_than_seconds=opts["wf_stale_seconds"]
        )
        expired = proposal.expire_stale_proposals(actor.id)
        if (summary["reaped"] or summary["polled"] or summary["dispatched"]
                or resumed or reaped_wf or expired):
            self.stdout.write(
                f"Tick: {summary['dispatched']} dispatcht, {summary['polled']} gepollt, "
                f"{summary['reaped']} reaped, {len(resumed)} Workflow(s) fortgesetzt, "
                f"{len(reaped_wf)} Workflow(s) abgeräumt, {len(expired)} Vorschlag/"
                "Vorschläge abgelaufen."
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
