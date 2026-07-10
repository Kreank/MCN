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
import zipfile
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

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

    def handle(self, *args, **opts):
        if not opts["dry_run"]:
            raise CommandError(
                "Der schreibende Import ist noch nicht freigegeben. Bitte zuerst "
                "--dry-run gegenlesen."
            )
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
