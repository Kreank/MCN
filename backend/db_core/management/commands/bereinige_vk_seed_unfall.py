"""Bereinigt die Unfall-VK-Zeilen des seed-Vorfalls vom 14.07.2026.

## Was war der Unfall?

Die VK-Standard-Schleife in `seed_demo` lief nicht über die Demo-Artikel, sondern
über **jeden** Artikel mit Listenpreis (`Article.objects.filter(status="AKTIV",
list_price__isnull=False)`). Nach einem DATANORM-Import mit ~215.000 Artikeln legte
`MCN_SEED=1` damit für den gesamten Stamm eine „Standard"-VK-Variante über die
Demo-Gruppe „Aufschlag 45 % (Material)" an — 214.923 Zeilen.

Diese Zeilen sind fachlich fatal: eine Artikel-Zuweisung schlägt in der VK-Ableitung
(`aufschlagsmatrix.vk_vorschlag`, Zweig 2) jede Matrix-Regel — auch die neue
Catch-all-Standardregel „Händler-Listenpreis (Standard)". Bei ~10 % des Stamms kommt
dadurch still **+45 %** auf den Listenpreis heraus, statt des gewünschten reinen
Listenpreises (Beweis: DN-bo-ARN507543KWE → VK 7.344,25 statt 5.065,00).

## Fingerabdruck

Die Unfall-Zeilen sind exakt identifizierbar über:

    label = 'Standard'
    price_origin = 'MANUELL'          (Formel-Zeilen tragen keine Matrix-Herkunft)
    fixed_price IS NULL               (Gruppe, kein Festpreis)
    sale_price_group_id = <Unfall-Gruppe>

Die Unfall-Gruppe ist standardmäßig die Demo-Gruppe „Aufschlag 45 % (Material)"
(UUID unten). Der Wert ist über `--sale-price-group-id` überschreibbar, falls die
Gruppen-ID auf einer anderen Instanz abweicht.

Die zwei Demo-Gruppen selbst bleiben bestehen (Schutzstandard: kein Löschen von
`sale_price_group`); nach der Bereinigung hängt an ihnen nur noch die eine echte
Demo-Zuweisung.

## Aufruf

    uv run python manage.py bereinige_vk_seed_unfall --dry-run
    uv run python manage.py bereinige_vk_seed_unfall

Die Löschung läuft in **einer** `business_transaction` (Benutzerkontext für den
Delete-Audit-Trigger `trg_article_sale_price_delete_audit`). Der Count wird vorher
und nachher ausgegeben; ein zweiter Lauf findet nichts mehr (idempotent).
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from db_core.db_context import business_transaction
from db_core.models import AppUser

# Demo-Gruppe „Aufschlag 45 % (Material)" auf der Demo-Instanz — der Anker der
# 214.923 Unfall-Zeilen. Überschreibbar via --sale-price-group-id.
UNFALL_GRUPPE = "1b1e9b93-7ef2-4eed-aa3e-06a915ca8d67"

# Der Fingerabdruck als reines WHERE (ein einziger gebundener Parameter: die
# Gruppen-UUID). Zähl- und Löschabfrage nutzen dieselbe Bedingung.
_WHERE = (
    "label = 'Standard' AND price_origin = 'MANUELL' "
    "AND fixed_price IS NULL AND sale_price_group_id = %s"
)


class Command(BaseCommand):
    help = (
        "Löscht die Unfall-VK-Zeilen des seed-Vorfalls (Standard/MANUELL/Gruppe) "
        "über business_transaction; --dry-run zeigt nur den Count."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur zählen und anzeigen — löscht nichts.",
        )
        parser.add_argument(
            "--sale-price-group-id",
            dest="group_id",
            default=UNFALL_GRUPPE,
            help="UUID der Unfall-Gruppe (Default: die Demo-Gruppe "
            f"Aufschlag 45 Prozent Material, {UNFALL_GRUPPE}).",
        )
        parser.add_argument(
            "--actor",
            help="app_user-UUID als Auslöser für den Delete-Audit (Default: "
            "erster aktiver Account). Nur beim echten Lauf nötig.",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        group_id = opts["group_id"]

        modus = "TROCKENLAUF" if dry_run else "LAUF"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"VK-Unfall-Bereinigung [{modus}] — Gruppe {group_id}"
            )
        )

        vorher = self._count(group_id)
        self.stdout.write(f"  Betroffene VK-Zeilen (Fingerabdruck): {vorher}")

        if vorher == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "  Nichts zu tun — kein Treffer für diesen Fingerabdruck."
                )
            )
            return

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"  Trockenlauf — es würden {vorher} Zeile(n) gelöscht, "
                    "geschrieben wird nichts."
                )
            )
            return

        actor = self._actor(opts.get("actor"))
        self.stdout.write(f"  Auslöser (Audit): {actor.display_name} ({actor.id})")

        with business_transaction(actor.id):
            with connection.cursor() as cur:
                cur.execute(
                    f"DELETE FROM pricing.article_sale_price WHERE {_WHERE}",
                    [group_id],
                )
                geloescht = cur.rowcount

        nachher = self._count(group_id)
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Summe: {geloescht} Zeile(n) gelöscht "
                f"(vorher {vorher}, nachher {nachher})."
            )
        )
        if nachher != 0:
            self.stderr.write(
                self.style.ERROR(
                    f"  WARNUNG: es verbleiben {nachher} Treffer — bitte prüfen."
                )
            )

    def _count(self, group_id):
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM pricing.article_sale_price WHERE {_WHERE}",
                [group_id],
            )
            return cur.fetchone()[0]

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
