"""Einmalige Korrektur des importierten Artikelstamms (Slice 03.08.2026).

**Was schiefstand.** Der DATANORM-Import hat für ALLE Kataloge gleich gemappt:
B-Satz Feld 3 → `manufacturer_name`, Feld 4 → `manufacturer_number`, und die
Artikelnummer bekam ein Präfix `DN-{namensraum}-`. Ergebnis im Bestand:

* Unter „Hersteller-Nr." steht bei 2.043.336 B&O-Artikeln eine B&O-INTERNE
  Katalognummer (`ZRB2071510`, `ARESRT10018217`). Sie existiert außerhalb von
  B&O nirgends — wer damit nachbestellt, sucht ins Leere.
* Unter „Hersteller" steht der Matchcode (`CUSSH01510`) bzw. im Bosch-Katalog
  sogar die Kurzbezeichnung (`6KT-SCHRAUBE`).
* Die Artikelnummer lautet `DN-bo-CUS15H` statt `CUS15H` — also nicht die
  Nummer, die auf der Lieferantenrechnung und im Angebot stehen muss.

**Was dieses Kommando tut** (je Namensraum, in dieser Reihenfolge):

1. `supplier_catalog_id` an der Lieferantenreferenz ← bisherige
   `manufacturer_number` (nur bei Großhandelskatalogen; die Nummer ist der
   Rückkanal zum Lieferanten und geht nicht verloren).
2. `matchcode` ← bisheriger `manufacturer_name`.
3. `manufacturer_name`/`manufacturer_number` nach Katalogart: Großhandel leer,
   Hersteller = Anbindungsname bzw. die Lieferanten-Artikelnummer.
4. `article_number` ohne Präfix.

**Warum der LEITKATALOG zuerst läuft.** Solange die Herstellerkataloge noch ihr
Präfix tragen, ist jede nackte B&O-Nummer garantiert frei — B&O kann in einem
Rutsch umgestellt werden, ohne eine einzige Kollisionsprüfung. Erst danach
werden die kleinen Kataloge umgestellt, und nur dort muss ausgewichen werden
(`509010` ist bei B&O ein Kupferwinkel und bei Vaillant ein Seitenteil).

**Warum der Audit-Trigger dabei ruht.** `audit.audit_row_update()` schreibt je
geänderter Zeile die KOMPLETTE Zeile zweimal als JSONB (vorher/nachher). Bei
2 Mio Artikeln mit Langtexten sind das mehrere Gigabyte Audit-Daten für eine
einmalige technische Feldkorrektur, die inhaltlich nichts entscheidet. Der
Vorgang wird stattdessen als EIN Audit-Eintrag protokolliert. Der Trigger wird in
`finally` wieder eingeschaltet und am Ende geprüft — ein Lauf, der ihn
ausgeschaltet zurückließe, wäre schlimmer als der Fehler, den er behebt.
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection as db_connection, transaction

from db_core.db_context import business_transaction
from db_core.models import AppUser
from db_core.services import datanorm_katalog

CHARGE = 5000

# Die Trigger, die für den Massenlauf ruhen. Sie schreiben Audit-Zeilen, sonst
# nichts — Schutz-, No-Delete- und updated_at-Trigger bleiben aktiv.
AUDIT_TRIGGER = [
    ("pricing.article", "trg_article_audit"),
    ("pricing.article_supplier_reference", "trg_supplier_ref_audit"),
]


def _kataloge():
    """Namensräume mit Artikeln, Leitkatalog zuerst — Reihenfolge ist wesentlich."""
    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.source_namespace, c.connection_kind, c.label, count(r.id)
            FROM pricing.supplier_connection c
            JOIN pricing.article_supplier_reference r
              ON r.source_namespace = c.source_namespace
             AND r.source_system = c.source_system
            WHERE c.source_system = 'DATANORM'
            GROUP BY 1, 2, 3, 4
            """
        )
        zeilen = cur.fetchall()
    return sorted(zeilen, key=lambda z: z[1] != datanorm_katalog.LEITKATALOG)


class Command(BaseCommand):
    help = (
        "Korrigiert den importierten Artikelstamm: Präfix aus der Artikelnummer, "
        "Matchcode und Herstellerfelder je Katalogart (Trockenlauf ohne --ja)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--ja", action="store_true",
            help="Wirklich schreiben. Ohne dieses Flag wird nur berichtet.",
        )
        parser.add_argument(
            "--anbindungsart", action="append", default=[], metavar="NS=ART",
            help="Katalogart korrigieren, z. B. --anbindungsart vaillant=HERSTELLER. "
            "Mehrfach angebbar. Wirkt VOR der Bereinigung, weil sie die "
            "Feldbedeutung bestimmt.",
        )

    def handle(self, *args, **opts):
        schreiben = opts["ja"]
        actor = AppUser.objects.filter(status="ACTIVE").order_by("created_at").first()
        if actor is None:
            raise CommandError("Kein aktiver security.app_user als Akteur gefunden.")

        self.stdout.write(self.style.MIGRATE_HEADING("Artikelstamm-Bereinigung"))
        if not schreiben:
            self.stdout.write(self.style.WARNING("  TROCKENLAUF — es wird nichts geschrieben.\n"))

        self._anbindungsarten_setzen(actor, opts["anbindungsart"], schreiben)

        kataloge = _kataloge()
        if not kataloge:
            self.stdout.write("  Keine DATANORM-Kataloge gefunden — nichts zu tun.")
            return

        for _id, ns, kind, _label, anzahl in kataloge:
            leit = " (Leitkatalog)" if ns == datanorm_katalog.LEITKATALOG else ""
            self.stdout.write(f"  {ns}{leit}: {anzahl:,} Artikel, Art {kind}".replace(",", "."))

        if not schreiben:
            self._vorschau(kataloge)
            self.stdout.write(self.style.WARNING("\n  Zum Ausführen: --ja"))
            return

        try:
            self._audit_trigger(aktiv=False)
            for _id, ns, kind, label, _anzahl in kataloge:
                self._bereinige(ns, kind, label, actor)
        finally:
            self._audit_trigger(aktiv=True)
            self._trigger_pruefen()

        self._protokollieren(actor, kataloge)
        self.stdout.write(self.style.SUCCESS("\n  Fertig."))
        self._nachschau()

    # -- Schritte ------------------------------------------------------------

    def _anbindungsarten_setzen(self, actor, angaben, schreiben):
        for angabe in angaben:
            if "=" not in angabe:
                raise CommandError(f"--anbindungsart erwartet NS=ART, bekam {angabe!r}")
            ns, art = (t.strip() for t in angabe.split("=", 1))
            if art not in ("GROSSHAENDLER", "HERSTELLER"):
                raise CommandError(f"Unbekannte Katalogart {art!r}")
            self.stdout.write(f"  Anbindung {ns} → {art}")
            if schreiben:
                with business_transaction(actor.id), db_connection.cursor() as cur:
                    cur.execute(
                        "UPDATE pricing.supplier_connection SET connection_kind = %s "
                        "WHERE source_namespace = %s AND source_system = 'DATANORM'",
                        [art, ns],
                    )

    def _vorschau(self, kataloge):
        """Zeigt je Katalog drei Artikel im Vorher/Nachher."""
        with db_connection.cursor() as cur:
            for _id, ns, kind, label, _n in kataloge:
                profil = datanorm_katalog.profil(kind, hersteller_name=label)
                cur.execute(
                    """
                    SELECT a.article_number, a.manufacturer_name, a.manufacturer_number,
                           r.supplier_article_number
                    FROM pricing.article a
                    JOIN pricing.article_supplier_reference r ON r.article_id = a.id
                    WHERE r.source_system = 'DATANORM' AND r.source_namespace = %s
                    LIMIT 3
                    """,
                    [ns],
                )
                self.stdout.write(f"\n  — {ns} —")
                for nummer, m_name, m_nr, liefer_nr in cur.fetchall():
                    neu_nr = (
                        liefer_nr if profil.hersteller_nummer == datanorm_katalog.HN_ARTIKELNUMMER
                        else None
                    )
                    self.stdout.write(f"    {nummer!r} → {liefer_nr!r}")
                    self.stdout.write(
                        f"      Hersteller    : {m_name!r} → {profil.hersteller_name!r}"
                    )
                    self.stdout.write(f"      Hersteller-Nr.: {m_nr!r} → {neu_nr!r}")
                    self.stdout.write(f"      Matchcode     : NULL → {m_name!r}")

    def _bereinige(self, ns, kind, label, actor):
        profil = datanorm_katalog.profil(kind, hersteller_name=label)
        self.stdout.write(f"\n  Bereinige {ns} …")

        # 1. Katalognummer retten, BEVOR manufacturer_number geleert wird.
        if profil.katalog_id_aus_feld4:
            n = self._sql(
                """
                UPDATE pricing.article_supplier_reference r
                SET supplier_catalog_id = a.manufacturer_number
                FROM pricing.article a
                WHERE a.id = r.article_id
                  AND r.source_system = 'DATANORM' AND r.source_namespace = %s
                  AND a.manufacturer_number IS NOT NULL
                  AND r.supplier_catalog_id IS NULL
                """,
                [ns], actor,
            )
            self.stdout.write(f"    Katalognummer gesichert : {n:,}".replace(",", "."))

        # 2./3. Matchcode aus dem falsch belegten Herstellernamen, Herstellerfelder
        #       nach Profil. `manufacturer_name` trug bisher IMMER den Matchcode.
        hersteller_nr_sql = (
            "r.supplier_article_number"
            if profil.hersteller_nummer == datanorm_katalog.HN_ARTIKELNUMMER
            else "NULL"
        )
        n = self._sql(
            f"""
            UPDATE pricing.article a
            SET matchcode = COALESCE(a.matchcode, a.manufacturer_name),
                manufacturer_name = %s,
                manufacturer_number = {hersteller_nr_sql}
            FROM pricing.article_supplier_reference r
            WHERE r.article_id = a.id
              AND r.source_system = 'DATANORM' AND r.source_namespace = %s
            """,
            [profil.hersteller_name, ns], actor,
        )
        self.stdout.write(f"    Hersteller/Matchcode    : {n:,}".replace(",", "."))

        # 4. Präfix entfernen. Der Leitkatalog läuft zuerst und trifft dabei auf
        #    keine Kollision, weil alle anderen Kataloge noch präfixiert sind.
        praefix = f"DN-{ns}-"
        if ns == datanorm_katalog.LEITKATALOG:
            n = self._sql(
                """
                UPDATE pricing.article a
                SET article_number = r.supplier_article_number
                FROM pricing.article_supplier_reference r
                WHERE r.article_id = a.id
                  AND r.source_system = 'DATANORM' AND r.source_namespace = %s
                  AND a.article_number = %s || r.supplier_article_number
                """,
                [ns, praefix], actor,
            )
            self.stdout.write(f"    Nummer entpräfixiert    : {n:,}".replace(",", "."))
            return

        # Fremdkataloge: einzeln, weil die nackte Nummer belegt sein kann.
        umbenannt = ausgewichen = 0
        while True:
            with db_connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT a.id, r.supplier_article_number
                    FROM pricing.article a
                    JOIN pricing.article_supplier_reference r ON r.article_id = a.id
                    WHERE r.source_system = 'DATANORM' AND r.source_namespace = %s
                      AND a.article_number = %s || r.supplier_article_number
                    LIMIT %s
                    """,
                    [ns, praefix, CHARGE],
                )
                zeilen = cur.fetchall()
            if not zeilen:
                break
            with business_transaction(actor.id), db_connection.cursor() as cur:
                for artikel_id, liefer_nr in zeilen:
                    cur.execute(
                        "SELECT 1 FROM pricing.article WHERE article_number = %s",
                        [liefer_nr],
                    )
                    if cur.fetchone() is None:
                        neu = liefer_nr
                    else:
                        neu = datanorm_katalog.ausweichnummer(
                            liefer_nr, ns, belegt=lambda k: self._belegt(cur, k)
                        )
                        ausgewichen += 1
                    cur.execute(
                        "UPDATE pricing.article SET article_number = %s WHERE id = %s",
                        [neu, artikel_id],
                    )
                    umbenannt += 1
        self.stdout.write(f"    Nummer entpräfixiert    : {umbenannt:,}".replace(",", "."))
        if ausgewichen:
            self.stdout.write(
                self.style.WARNING(
                    f"    davon ausgewichen (Kollision mit dem Leitkatalog): {ausgewichen}"
                )
            )

    # -- Werkzeug ------------------------------------------------------------

    @staticmethod
    def _belegt(cur, kandidat):
        cur.execute("SELECT 1 FROM pricing.article WHERE article_number = %s", [kandidat])
        return cur.fetchone() is not None

    @staticmethod
    def _sql(sql, params, actor):
        with business_transaction(actor.id), db_connection.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def _audit_trigger(self, *, aktiv):
        wort = "ENABLE" if aktiv else "DISABLE"
        with transaction.atomic(), db_connection.cursor() as cur:
            for tabelle, trigger in AUDIT_TRIGGER:
                cur.execute(f"ALTER TABLE {tabelle} {wort} TRIGGER {trigger}")
        self.stdout.write(f"  Audit-Trigger: {'an' if aktiv else 'aus'}")

    def _trigger_pruefen(self):
        """Ein Lauf, der die Audit-Trigger ausgeschaltet zurücklässt, ist ein Loch
        in der Nachvollziehbarkeit — lauter als jeder Importfehler melden."""
        with db_connection.cursor() as cur:
            cur.execute(
                "SELECT tgname FROM pg_trigger WHERE tgname = ANY(%s) AND tgenabled = 'D'",
                [[t for _, t in AUDIT_TRIGGER]],
            )
            tot = [z[0] for z in cur.fetchall()]
        if tot:
            raise CommandError(
                f"Audit-Trigger sind noch deaktiviert: {tot}. "
                "Sofort von Hand einschalten: ALTER TABLE … ENABLE TRIGGER …"
            )

    def _protokollieren(self, actor, kataloge):
        """Ein Audit-Eintrag JE KATALOG statt Millionen Zeilenkopien.

        Angehängt wird er an die Anbindung: Sie ist der Gegenstand, dessen
        Auslegung sich geändert hat, und `audit_entry.target_id` ist NOT NULL —
        ein tabellenweiter Eintrag ohne Ziel wäre nicht speicherbar.
        """


        with business_transaction(actor.id), db_connection.cursor() as cur:
            for conn_id, ns, kind, _label, anzahl in kataloge:
                cur.execute(
                    """
                    INSERT INTO audit.audit_entry
                        (actor_type, actor_user_id, action, target_type, target_id,
                         before_excerpt, after_excerpt)
                    VALUES ('USER', %s, 'ARTIKELSTAMM_BEREINIGT',
                            'pricing.supplier_connection', %s, %s::jsonb, %s::jsonb)
                    """,
                    [
                        actor.id,
                        conn_id,
                        json.dumps({
                            "regel": "Feld 3 -> manufacturer_name, "
                                     "Feld 4 -> manufacturer_number, DN-Praefix",
                            "namensraum": ns,
                        }),
                        json.dumps({
                            "regel": "Feld 3 -> matchcode, Feld 4 -> supplier_catalog_id, "
                                     "Herstellerfelder je Katalogart, nackte Nummer",
                            "namensraum": ns,
                            "katalogart": kind,
                            "artikel": anzahl,
                        }),
                    ],
                )

    def _nachschau(self):
        with db_connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM pricing.article WHERE article_number LIKE 'DN-%'")
            rest = cur.fetchone()[0]
            cur.execute(
                """
                SELECT count(*) FROM pricing.article a
                JOIN pricing.article_supplier_reference r ON r.article_id = a.id
                WHERE r.source_namespace = %s AND a.manufacturer_number IS NOT NULL
                """,
                [datanorm_katalog.LEITKATALOG],
            )
            erfunden = cur.fetchone()[0]
        self.stdout.write(f"  Rest mit DN-Präfix                : {rest}")
        self.stdout.write(f"  Leitkatalog mit Hersteller-Nr.    : {erfunden}")
        if rest or erfunden:
            self.stdout.write(self.style.WARNING("  → Beides sollte 0 sein."))
