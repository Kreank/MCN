"""KI-Werkzeuge verwalten — für den Betreiber, ohne Python-Shell.

Ein Werkzeug ist die Anbindung eines (meist passiven) Geräts der Wahrnehmungsflotte
— das ASR-Handy, die Vision-Box, der OCR-Dienst — oder eines in-process-Dienstes.
MCN spricht das Gerät über seine `endpoint_url` an und authentifiziert sich mit einem
**Bearer-Token**, das Fernet-verschlüsselt at rest liegt (MCN_CRED_KEY). Das LLM
selbst braucht KEIN Werkzeug: es wird über MCN_AI_PROFILES angebunden (siehe
deploy/.env.example) und in-process gerufen.

    # Das ASR-Handy anbinden (Token liegt in einer Env-Var, nicht in der History):
    MCN_ASR_BEARER=geheim uv run python manage.py ki_tool register \
        --key asr-handy --label "ASR (altes Handy)" --capability ASR --mode ASYNC \
        --endpoint https://handy.local/asr --bearer-env MCN_ASR_BEARER

    uv run python manage.py ki_tool list
    uv run python manage.py ki_tool set-bearer --key asr-handy --bearer-env MCN_ASR_BEARER
    uv run python manage.py ki_tool set-bearer --key asr-handy --clear
    uv run python manage.py ki_tool deactivate --key asr-handy

Das Bearer-Token wird NIE ausgegeben oder geloggt. `--bearer-env` ist der empfohlene
Weg (das Secret steht dann nicht in argv/Shell-History); `--bearer <wert>` geht auch,
warnt aber.
"""
import os

from django.core.management.base import BaseCommand, CommandError

from db_core.ai import registry
from db_core.models import AppUser, Tool

_CAPABILITIES = ("ASR", "VISION", "OCR", "LLM", "DOMAIN_QUERY")
_MODES = ("SYNC", "ASYNC", "INTERNAL")
# Externe Modi sprechen ein Gerät über HTTP an — ohne Endpoint nicht dispatchbar.
_EXTERN = ("SYNC", "ASYNC")


class Command(BaseCommand):
    help = "KI-Werkzeuge (Geräteflotte) registrieren, auflisten und pflegen."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)

        sub.add_parser("list", help="Alle registrierten Werkzeuge anzeigen.")

        reg = sub.add_parser("register", help="Ein neues Werkzeug registrieren.")
        reg.add_argument("--key", required=True, help="Eindeutiger tool_key.")
        reg.add_argument("--label", required=True, help="Anzeigename.")
        reg.add_argument("--capability", required=True, choices=_CAPABILITIES)
        reg.add_argument("--mode", required=True, choices=_MODES,
                         help="SYNC/ASYNC = externes Gerät (MCN pollt bei ASYNC), "
                              "INTERNAL = in-process.")
        reg.add_argument("--endpoint", help="HTTP-Endpoint des Geräts "
                                            "(Pflicht bei SYNC/ASYNC).")
        reg.add_argument("--timeout", type=int, default=120,
                         help="Timeout je Aufruf in Sekunden (Default 120).")
        reg.add_argument("--attempts", type=int, default=3,
                         help="Max. Versuche je Aufruf (Default 3).")
        self._add_bearer_args(reg)
        self._add_actor(reg)

        sb = sub.add_parser("set-bearer", help="Bearer-Token setzen/löschen.")
        sb.add_argument("--key", required=True)
        self._add_bearer_args(sb, mit_clear=True)
        self._add_actor(sb)

        for name, hilfe in (("activate", "aktivieren"), ("deactivate", "stilllegen")):
            p = sub.add_parser(name, help=f"Ein Werkzeug {hilfe}.")
            p.add_argument("--key", required=True)
            self._add_actor(p)

    def _add_actor(self, parser):
        parser.add_argument(
            "--actor",
            help="app_user-UUID als Akteur der Writes (Default: erster aktiver "
                 "Account). Empfohlen: ein eigener KI-Service-Account.",
        )

    def _add_bearer_args(self, parser, *, mit_clear=False):
        parser.add_argument("--bearer", help="Token direkt (landet ggf. in der "
                                             "Shell-History — --bearer-env bevorzugen).")
        parser.add_argument("--bearer-env", help="Name der Env-Var, die das Token "
                                                 "trägt (empfohlen).")
        if mit_clear:
            parser.add_argument("--clear", action="store_true",
                                help="Hinterlegtes Token entfernen.")

    # --- Aktionen ----------------------------------------------------------

    def handle(self, *args, **opts):
        action = opts["action"]
        if action == "list":
            return self._list()
        actor = self._actor(opts.get("actor"))
        if action == "register":
            return self._register(actor, opts)
        if action == "set-bearer":
            return self._set_bearer(actor, opts)
        if action in ("activate", "deactivate"):
            return self._set_status(actor, opts, action)

    def _list(self):
        tools = Tool.objects.order_by("capability", "tool_key")
        if not tools:
            self.stdout.write("Keine Werkzeuge registriert.")
            return
        for t in tools:
            bearer = "Bearer:ja" if t.bearer_encrypted is not None else "Bearer:nein"
            endpoint = t.endpoint_url or "—"
            gesehen = t.last_seen_at.isoformat() if t.last_seen_at else "nie"
            self.stdout.write(
                f"{t.tool_key:<24} {t.capability:<12} {t.invocation_mode:<9} "
                f"{t.status:<9} {bearer:<11} {endpoint}  (zuletzt gesehen: {gesehen})"
            )

    def _register(self, actor, opts):
        mode = opts["mode"]
        endpoint = opts.get("endpoint")
        if mode in _EXTERN and not endpoint:
            raise CommandError(
                f"Modus {mode} spricht ein Gerät über HTTP an — --endpoint ist Pflicht."
            )
        if mode == "INTERNAL" and endpoint:
            raise CommandError("Modus INTERNAL hat keinen Endpoint (in-process).")
        bearer = self._bearer_wert(opts)
        try:
            tool = registry.register_tool(
                actor.id, tool_key=opts["key"], label=opts["label"],
                capability=opts["capability"], invocation_mode=mode,
                endpoint_url=endpoint, timeout_seconds=opts["timeout"],
                max_attempts=opts["attempts"],
            )
        except ValueError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Werkzeug '{tool.tool_key}' registriert."))
        if bearer:
            # register_tool hat bereits committet — scheitert das Bearer (z. B.
            # MCN_CRED_KEY fehlt), steht das Werkzeug ohne Bearer da. Kein roher
            # Traceback: klare Meldung inkl. Nachhol-Weg.
            try:
                registry.set_bearer(actor.id, tool_id=tool.id, bearer=bearer)
            except ValueError as exc:
                raise CommandError(
                    f"Werkzeug '{tool.tool_key}' wurde registriert, aber das Bearer "
                    f"ließ sich nicht hinterlegen: {exc} Nachholen mit "
                    f"`ki_tool set-bearer --key {tool.tool_key} …`."
                )
            self.stdout.write("Bearer hinterlegt.")

    def _set_bearer(self, actor, opts):
        tool = self._tool(opts["key"])
        if opts.get("clear"):
            registry.set_bearer(actor.id, tool_id=tool.id, bearer=None)
            self.stdout.write(self.style.SUCCESS("Bearer entfernt."))
            return
        bearer = self._bearer_wert(opts)
        if not bearer:
            raise CommandError("Kein Token: --bearer, --bearer-env oder --clear angeben.")
        try:
            registry.set_bearer(actor.id, tool_id=tool.id, bearer=bearer)
        except ValueError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS("Bearer hinterlegt."))

    def _set_status(self, actor, opts, action):
        tool = self._tool(opts["key"])
        status = "ACTIVE" if action == "activate" else "INACTIVE"
        registry.set_status(actor.id, tool_id=tool.id, status=status)
        self.stdout.write(self.style.SUCCESS(f"Werkzeug '{tool.tool_key}' ist {status}."))

    # --- Helfer ------------------------------------------------------------

    def _bearer_wert(self, opts):
        """Das Bearer-Token aus --bearer-env (bevorzugt) oder --bearer, sonst None."""
        env = opts.get("bearer_env")
        direkt = opts.get("bearer")
        if env and direkt:
            raise CommandError("--bearer und --bearer-env schließen sich aus.")
        if env:
            wert = os.environ.get(env)
            if not wert:
                raise CommandError(f"Umgebungsvariable {env} ist nicht gesetzt.")
            return wert
        if direkt:
            self.stderr.write(self.style.WARNING(
                "Hinweis: --bearer kann in der Shell-History landen; "
                "--bearer-env ist sicherer."
            ))
            return direkt
        return None

    def _tool(self, key):
        tool = Tool.objects.filter(tool_key=key).first()
        if tool is None:
            raise CommandError(f"Kein Werkzeug mit tool_key '{key}'.")
        return tool

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
