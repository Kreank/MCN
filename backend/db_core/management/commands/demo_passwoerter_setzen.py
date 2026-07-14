"""Setzt auf einer DEMO-Instanz ein bekanntes Passwort für alle Login-Konten.

## Warum es diesen Befehl gibt

`seed_demo` (und der spätere `seed_szenario`) vergeben Login-Passwörter **nur bei
`settings.DEBUG`** (`_ensure_login` → `set_unusable_password()` sonst). Auf einem
Server läuft das System aber mit `MCN_DEBUG=0` (fail-safe, `settings.py`). Ohne
diesen Befehl bekämen alle Seed-Konten ein *unbenutzbares* Passwort — die Demo
stünde vor einem Login, durch das niemand kommt, und im Leitstand gibt es (noch)
keine Benutzeranlage.

Der Befehl ist deshalb **unabhängig vom Seed** und läuft nach ihm im Entrypoint.
Er wirkt auch auf Konten, die der Seed beim zweiten Lauf übersprungen hat
(`_ensure_login` legt nur *neue* Konten an) — er ist idempotent.

## Warum er nicht versehentlich scharf werden kann

Er verlangt **zwei** bewusste Schalter:

* `MCN_DEMO_INSTANZ=1` — die ausdrückliche Erklärung „das hier ist eine
  Demo-/Wegwerf-Instanz". Fehlt sie, bricht der Befehl ab.
* `MCN_DEMO_PASSWORD` (oder `--passwort`) — das zu setzende Passwort.

Ein Produktivsystem setzt keinen der beiden. Damit kann der Befehl dort selbst
dann nichts anrichten, wenn ihn jemand aus Versehen aufruft.

Das Passwort wird **nicht** ausgegeben (es steht in der `.env` des Betreibers) —
protokolliert wird nur, für wie viele Konten es gesetzt wurde.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

MIN_LAENGE = 12  # spiegelt MinimumLengthValidator aus settings.AUTH_PASSWORD_VALIDATORS


class Command(BaseCommand):
    help = (
        "DEMO-Instanz: setzt für alle aktiven Login-Konten dasselbe Passwort "
        "(MCN_DEMO_PASSWORD). Erfordert MCN_DEMO_INSTANZ=1."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--passwort",
            default=None,
            help="Passwort; ersatzweise Umgebungsvariable MCN_DEMO_PASSWORD.",
        )
        parser.add_argument(
            "--nur-ohne-passwort",
            action="store_true",
            help=(
                "Nur Konten anfassen, die kein nutzbares Passwort haben. "
                "Schützt bereits geänderte Passwörter vor dem Zurücksetzen."
            ),
        )

    def handle(self, *args, **options):
        if os.environ.get("MCN_DEMO_INSTANZ") != "1":
            raise CommandError(
                "demo_passwoerter_setzen läuft nur auf einer ausdrücklich als "
                "Demo markierten Instanz (MCN_DEMO_INSTANZ=1). Auf einem "
                "Produktivsystem vergibt man Passwörter einzeln "
                "(manage.py changepassword)."
            )

        passwort = options["passwort"] or os.environ.get("MCN_DEMO_PASSWORD", "")
        if not passwort:
            raise CommandError(
                "Kein Passwort angegeben (MCN_DEMO_PASSWORD oder --passwort)."
            )
        if len(passwort) < MIN_LAENGE:
            raise CommandError(
                f"Das Demo-Passwort muss mindestens {MIN_LAENGE} Zeichen haben "
                f"(wie AUTH_PASSWORD_VALIDATORS es für jeden anderen Weg verlangt)."
            )

        User = get_user_model()
        gesetzt = 0
        uebersprungen = 0
        for user in User.objects.filter(is_active=True).order_by("email"):
            if options["nur_ohne_passwort"] and user.has_usable_password():
                uebersprungen += 1
                continue
            user.set_password(passwort)
            user.save(update_fields=["password"])
            gesetzt += 1
            self.stdout.write(f"Passwort gesetzt: {user.email or user.username}")

        if gesetzt == 0:
            self.stdout.write(
                self.style.WARNING(
                    "Kein Konto angefasst — läuft der Seed? Ohne Login-Konto kommt "
                    "niemand in die Demo."
                )
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Demo-Passwörter: {gesetzt} gesetzt, {uebersprungen} übersprungen."
            )
        )
