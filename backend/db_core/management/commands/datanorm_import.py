"""DATANORM-Import: Artikelstamm und Preise eines Großhändlers einlesen.

Aufruf (Trockenlauf, schreibt NICHTS):

    uv run python manage.py datanorm_import \
        --stamm "D:/Mitra/MCN/DATANORM/3STAMM.ZIP" \
        --preise "D:/Mitra/MCN/DATANORM/DATANORM (1).ZIP" \
        --namespace bo --lieferant "BÄR & OLLENROTH KG" \
        --dry-run --limit 10

Der Trockenlauf zeigt je Artikel den Rohsatz, die geparsten Felder und den
errechneten Preis — zum Gegenlesen, bevor irgendetwas geschrieben wird. Ein
Importer, der Millionen Artikel mit falscher Preiseinheit anlegt, ist schlimmer
als keiner: `pricing.article_supplier_reference` ist nach der Anlage per Trigger
unveränderlich.

Die Dateien sind zu groß für den Speicher (Stammdatei entpackt 1,6 GB), deshalb
wird zeilenweise gestreamt. Die Preisdatei wird vorab in ein Dictionary geladen
(rund 1,4 Mio Einträge) — sie ist mit ~68 MB klein genug.
"""
import io
import uuid
import zipfile
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection as db_connection

from db_core.db_context import business_transaction
from db_core.models import AppUser, Article, ArticleSupplierReference, Party
from db_core.services import datanorm


def _zeilen(zip_pfad):
    """Streamt die (einzige) Datei im ZIP zeilenweise, dekodiert als CP850."""
    with zipfile.ZipFile(zip_pfad) as z:
        namen = z.namelist()
        if len(namen) != 1:
            raise CommandError(f"{zip_pfad}: erwartet genau eine Datei, gefunden {namen}")
        with z.open(namen[0]) as roh:
            for zeile in io.TextIOWrapper(roh, encoding=datanorm.ENCODING, newline=""):
                yield zeile.rstrip("\r\n")


def _preisindex(zip_pfad, stdout=None):
    """Artikelnummer -> bester Preissatz.

    Ein Artikel kann mehrfach vorkommen: einmal mit Listenpreis + Rabatt
    (Kennzeichen 1) und einmal als Nettopreis (Kennzeichen 2). Der Nettopreis ist
    die ausdrückliche Aussage des Händlers und gewinnt; der Listensatz liefert
    zusätzlich den Listenpreis.
    """
    liste, netto = {}, {}
    n = 0
    for zeile in _zeilen(zip_pfad):
        if not zeile.startswith("P;"):
            continue
        for p in datanorm.parse_preise(zeile):
            n += 1
            ziel = netto if p.preiskennzeichen == datanorm.PREISKENNZEICHEN_NETTO else liste
            ziel[p.artikelnummer] = p
    if stdout:
        stdout(f"  Preisdatei: {n} Blöcke, {len(liste)} Listen-, {len(netto)} Nettopreise")
    return liste, netto


def _euro(wert):
    if wert is None:
        return "—"
    return f"{wert:>12,.4f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _artikel_bloecke(zip_pfad):
    """Streamt (Artikel, Zusatz|None, Langtext) je Artikel.

    Die Datei ist je Artikel gruppiert: erst der A-Satz, dann sein B-Satz, dann
    seine D-Sätze. Deshalb genügt ein Puffer für genau einen Artikel — 2 Mio
    Artikel mit 14,5 Mio Langtextzeilen passen sonst nicht in den Speicher.
    """
    a = b = None
    texte = []

    def fertig():
        return a, b, "\n".join(t for _, t in sorted(texte)) or None

    for zeile in _zeilen(zip_pfad):
        art = zeile[:1]
        if art == "A":
            if a is not None:
                yield fertig()
            a, b, texte = datanorm.parse_artikel(zeile), None, []
        elif art == "B" and a is not None:
            z = datanorm.parse_zusatz(zeile)
            if z.artikelnummer == a.artikelnummer:
                b = z
        elif art == "D" and a is not None:
            nummer, zeilen = datanorm.parse_langtext(zeile)
            if nummer == a.artikelnummer:
                texte.extend(zeilen)
    if a is not None:
        yield fertig()


def _lieferant_und_anbindung(actor_id, *, name, namespace):
    """Legt Lieferant (identity.party) und Anbindung an, oder findet sie.

    `connection_kind = GROSSHAENDLER` trennt den Bestellkatalog von den
    Herstellerdaten des Gerätefinders (Migration 0040). Wer hier HERSTELLER
    einträgt, mischt Ersatzteile in die Artikelsuche des Angebots.
    """
    party = Party.objects.filter(display_name=name, status="ACTIVE").first()
    if party is None:
        with business_transaction(actor_id):
            party = Party.objects.create(
                id=uuid.uuid4(), party_type="ORGANIZATION",
                display_name=name, status="ACTIVE", version=1,
            )
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM pricing.supplier_connection "
            "WHERE source_namespace = %s AND source_system = 'DATANORM'",
            [namespace],
        )
        row = cur.fetchone()
        if row is None:
            with business_transaction(actor_id):
                cur.execute(
                    """
                    -- status ist hier englisch (ACTIVE/INACTIVE), anders als bei
                    -- den Fachtabellen mit deutschem Statusautomaten.
                    INSERT INTO pricing.supplier_connection
                        (id, supplier_party_id, source_system, source_namespace,
                         label, status, connection_kind, version)
                    VALUES (gen_random_uuid(), %s, 'DATANORM', %s, %s, 'ACTIVE',
                            'GROSSHAENDLER', 1)
                    """,
                    [party.id, namespace, name],
                )
    return party


class Command(BaseCommand):
    help = "Importiert einen DATANORM-4-Artikelstamm (Trockenlauf mit --dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--stamm", required=True, help="ZIP mit datanorm.001")
        parser.add_argument("--preise", help="ZIP mit datpreis.001")
        parser.add_argument("--namespace", required=True, help="z. B. 'bo'")
        parser.add_argument("--lieferant", help="Anzeigename des Lieferanten")
        parser.add_argument("--dry-run", action="store_true", help="nichts schreiben")
        parser.add_argument("--limit", type=int, default=10, help="Artikel im Trockenlauf")
        parser.add_argument(
            "--nummer", action="append", default=[],
            help="Nur diese Artikelnummer(n) zeigen (mehrfach angebbar).",
        )
        parser.add_argument(
            "--batch", type=int, default=2000,
            help="Artikel je Schreib-Transaktion (Standard 2000).",
        )
        parser.add_argument(
            "--limit-import", type=int, default=0,
            help="Nur die ersten N Artikel schreiben (0 = alle). Für Probeläufe.",
        )

    def handle(self, *args, **opts):
        if not opts["dry_run"]:
            return self._importieren(**opts)
        self.stdout.write(self.style.MIGRATE_HEADING("DATANORM-Trockenlauf"))

        # Vorlaufsatz
        erste = next(iter(_zeilen(opts["stamm"])))
        v = datanorm.parse_vorlauf(erste)
        self.stdout.write(f"  Datei     : {opts['stamm']}")
        self.stdout.write(f"  Version   : {v.version}   Währung: {v.waehrung}   Stand: {v.datum}")
        self.stdout.write(f"  Absender  : {v.info[:70]}")
        self.stdout.write("")

        preis_liste, preis_netto = {}, {}
        if opts.get("preise"):
            self.stdout.write("  Lese Preisdatei …")
            preis_liste, preis_netto = _preisindex(
                opts["preise"], stdout=self.stdout.write
            )
            self.stdout.write("")

        # Zusatzsätze (Fabrikat) für die gesuchten Artikel einsammeln
        limit = opts["limit"]
        gesucht = set(opts["nummer"])
        artikel, zusatz = [], {}
        gefunden = set()
        for zeile in _zeilen(opts["stamm"]):
            if zeile.startswith("A;"):
                a = datanorm.parse_artikel(zeile)
                if gesucht:
                    if a.artikelnummer in gesucht:
                        artikel.append((zeile, a))
                        gefunden.add(a.artikelnummer)
                elif len(artikel) < limit:
                    artikel.append((zeile, a))
            elif zeile.startswith("B;"):
                b = datanorm.parse_zusatz(zeile)
                if any(a.artikelnummer == b.artikelnummer for _, a in artikel):
                    zusatz[b.artikelnummer] = b
            fertig = (
                gefunden >= gesucht if gesucht else len(artikel) >= limit
            )
            if fertig and len(zusatz) >= len(artikel):
                break

        self.stdout.write(self.style.MIGRATE_HEADING(f"Erste {len(artikel)} Artikel"))
        for roh, a in artikel:
            b = zusatz.get(a.artikelnummer)
            p = preis_netto.get(a.artikelnummer) or preis_liste.get(a.artikelnummer)
            ek = lp = None
            herkunft = "kein Preissatz"
            if p is not None:
                ek, lp = datanorm.einkaufspreis(p, a.preiseinheit)
                if p.preiskennzeichen == datanorm.PREISKENNZEICHEN_NETTO:
                    herkunft = "Nettopreis (PKZ 2)"
                elif p.rabatt_kennzeichen == datanorm.RABATT_PROZENT:
                    herkunft = f"Liste - {Decimal(p.rabatt_wert)/100:.2f} % Rabatt"
                elif p.rabatt_kennzeichen == datanorm.RABATT_GRUPPE:
                    herkunft = f"Rabattgruppe {a.rabattgruppe} (nicht auflösbar)"
                else:
                    herkunft = f"Rabattkennzeichen {p.rabatt_kennzeichen}"

            self.stdout.write("")
            self.stdout.write(self.style.SQL_KEYWORD(f"  {a.artikelnummer}"))
            self.stdout.write(f"    roh          : {roh[:96]}")
            self.stdout.write(f"    Bezeichnung  : {a.bezeichnung[:70]}")
            self.stdout.write(
                f"    Einheit      : {a.mengeneinheit}    "
                f"Preiseinheit: {a.preiseinheit} (je {datanorm.PREISEINHEIT_DIVISOR[a.preiseinheit]})"
            )
            self.stdout.write(f"    Fabrikat     : {(b.matchcode if b else None) or '—'}")
            self.stdout.write(f"    Rabattgruppe : {a.rabattgruppe or '—'}")
            self.stdout.write(
                f"    Stammpreis   : {a.listenpreis_cent} Cent "
                f"-> je Einheit {_euro(a.listenpreis)} €"
            )
            self.stdout.write(f"    Listenpreis  : {_euro(lp)} €")
            self.stdout.write(
                f"    Einkaufspreis: {_euro(ek)} €   ({herkunft})"
            )
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("  Trockenlauf — es wurde nichts geschrieben."))

    # ------------------------------------------------------------------
    # Schreibender Import
    # ------------------------------------------------------------------

    def _importieren(self, **opts):
        namespace = opts["namespace"]
        name = opts["lieferant"] or namespace
        if not opts.get("preise"):
            raise CommandError("--preise ist für den Import erforderlich (EK-Quelle).")

        actor = AppUser.objects.filter(status="ACTIVE").order_by("created_at").first()
        if actor is None:
            raise CommandError("Kein aktiver security.app_user als Akteur gefunden.")

        self.stdout.write(self.style.MIGRATE_HEADING("DATANORM-Import"))
        v = datanorm.parse_vorlauf(next(iter(_zeilen(opts["stamm"]))))
        self.stdout.write(f"  Version {v.version}, Währung {v.waehrung}, Stand {v.datum}")

        if Article.objects.filter(article_number__startswith=f"DN-{namespace}-").exists():
            raise CommandError(
                f"Es existieren bereits Artikel im Namensraum '{namespace}'. "
                "Der Erstimport bricht ab, statt Dubletten anzulegen."
            )

        self.stdout.write("  Lese Preisdatei …")
        preis_liste, preis_netto = _preisindex(opts["preise"], stdout=self.stdout.write)

        party = _lieferant_und_anbindung(actor.id, name=name, namespace=namespace)
        self.stdout.write(f"  Lieferant : {party.display_name} ({party.id})")
        self.stdout.write(f"  Namensraum: {namespace}  (GROSSHAENDLER)")
        self.stdout.write("")

        heute = date.today()
        batch = opts["batch"]
        artikel_puffer, ref_puffer = [], []
        n = ohne_preis = 0
        limit = opts["limit_import"]

        def flush():
            if not artikel_puffer:
                return
            with business_transaction(actor.id):
                Article.objects.bulk_create(artikel_puffer, batch_size=1000)
                ArticleSupplierReference.objects.bulk_create(ref_puffer, batch_size=1000)
            artikel_puffer.clear()
            ref_puffer.clear()

        for a, b, langtext in _artikel_bloecke(opts["stamm"]):
            if a.vkz == datanorm.VKZ_LOESCHUNG:
                continue
            p = preis_netto.get(a.artikelnummer) or preis_liste.get(a.artikelnummer)
            ek = lp = None
            if p is not None:
                ek, lp = datanorm.einkaufspreis(p, a.preiseinheit)
            if lp is None:
                lp = a.listenpreis          # Stammpreis als Rückfall
            if ek is None:
                ohne_preis += 1

            artikel_id = uuid.uuid4()
            artikel_puffer.append(
                Article(
                    id=artikel_id,
                    article_number=f"DN-{namespace}-{a.artikelnummer}",
                    description=(a.bezeichnung or a.artikelnummer)[:2000],
                    long_description=langtext,
                    unit=(a.mengeneinheit or "Stk"),
                    line_type="MATERIAL",
                    list_price=lp,
                    gtin=(b.ean if b else None),
                    manufacturer_name=(b.matchcode if b else None),
                    manufacturer_number=(b.alt_artikelnummer if b else None),
                    product_group=(b.warengruppe if b else None),
                    status="AKTIV",
                    version=1,
                )
            )
            ref_puffer.append(
                ArticleSupplierReference(
                    id=uuid.uuid4(),
                    article_id=artikel_id,
                    supplier_party_id=party.id,
                    source_system="DATANORM",
                    source_namespace=namespace,
                    supplier_article_number=a.artikelnummer,
                    last_purchase_price=ek,
                    list_price=lp,
                    # DB-CHECK: kein Preis, keine Währung.
                    currency="EUR" if ek is not None else None,
                    discount_group=a.rabattgruppe,
                    price_unit_code=a.preiseinheit,
                    valid_from=heute,
                )
            )
            n += 1
            if len(artikel_puffer) >= batch:
                flush()
                self.stdout.write(f"    … {n} Artikel", ending="\r")
                self.stdout.flush()
            if limit and n >= limit:
                break
        flush()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"  {n} Artikel importiert."))
        self.stdout.write(
            f"  davon ohne bestimmbaren Einkaufspreis: {ohne_preis} "
            "(unbekannt = NULL, nicht 0)"
        )
