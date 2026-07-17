"""DATANORM-Import: Artikelstamm und Preise eines Großhändlers/Herstellers einlesen.

Zwei DATANORM-4-Varianten werden unterstützt:

1. GROSSHÄNDLER (B&O): Preise stehen in einer SEPARATEN Preisdatei (P-Sätze),
   die per --preise übergeben wird. Rabatt steckt im P-Satz.

       uv run python manage.py datanorm_import \
           --stamm "3STAMM.ZIP" --preise "DATANORM (1).ZIP" \
           --namespace bo --lieferant "BÄR & OLLENROTH KG" --dry-run --limit 10

2. HERSTELLER (Vaillant, Bosch/Junkers): Preis steht IM A-Satz (Feld nach der
   Einheit, in Cent; Preiskennzeichen 1 = Bruttopreis/Liste). Der Rabatt liegt
   NICHT am Artikel, sondern verweist per Rabattgruppe (RC/RA/PP21/…) auf eine
   separate .RAB-Datei, die per --rabatt übergeben wird (oder im Stamm-ZIP
   mitgeliefert ist, wie bei Bosch). Ohne .RAB bleibt der EK unbekannt (NULL).

       uv run python manage.py datanorm_import \
           --stamm "Preisliste_ET_DE_Update_01.07.2026.zip" \
           --namespace vaillant --dry-run --limit 5
       uv run python manage.py datanorm_import \
           --stamm "DATANORM_PPT01042026_Handelsware-neu.zip" \
           --rabatt "datanorm-rab-ppt01062023-2669958.zip" \
           --namespace vaillant --dry-run --limit 5

Die Wahl der Preisquelle geschieht je Artikel automatisch: liegt ein P-Satz vor,
gewinnt er (B&O); sonst wird der A-Satz-Preis verwendet (Hersteller). Der
bestehende B&O-Weg bleibt damit unverändert.

Die Dateinamen im ZIP sind tolerant (datanorm.001/.002, Groß/Klein; Mehr-Member-
ZIPs mit .RAB/.WRG oder einer Ordnerebene werden korrekt aufgelöst).

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


def _ist_rab(name):
    return name.rsplit("/", 1)[-1].lower().endswith(".rab")


def _ist_beiwerk(name):
    """.RAB (Rabatte) und .WRG (Warengruppen) sind Beiwerk, nicht der Artikelstamm."""
    return name.rsplit("/", 1)[-1].lower().endswith((".rab", ".wrg"))


def _artikel_member(namen):
    """Wählt die Artikel-Hauptdatei aus einem ZIP.

    Toleriert alle DATANORM-Varianten: `datanorm.001`, `.002`, Groß/Klein, sowie
    Mehr-Member-ZIPs (Hersteller bündeln DATANORM.001 + .RAB + .WRG) und
    Verzeichnis-Einträge (die Grundausstattung packt eine Ordnerebene mit ein).
    Der bisherige B&O-Weg (genau eine Datei) fällt als Sonderfall darunter.
    """
    dateien = [n for n in namen if not n.endswith("/")]      # keine Verzeichnisse
    haupt = [n for n in dateien if not _ist_beiwerk(n)]
    if len(haupt) == 1:
        return haupt[0]
    if len(dateien) == 1:
        return dateien[0]
    raise CommandError(
        f"ZIP: Artikel-Hauptdatei nicht eindeutig bestimmbar, gefunden {namen}"
    )


def _zeilen(zip_pfad, member=None):
    """Streamt die Artikel-Hauptdatei im ZIP zeilenweise, dekodiert als CP850.

    Ohne `member` wird die Hauptdatei automatisch gewählt (siehe `_artikel_member`);
    mit `member` wird gezielt dieser Eintrag gelesen (z. B. eine .RAB-Datei).
    """
    with zipfile.ZipFile(zip_pfad) as z:
        name = member or _artikel_member(z.namelist())
        with z.open(name) as roh:
            for zeile in io.TextIOWrapper(roh, encoding=datanorm.ENCODING, newline=""):
                yield zeile.rstrip("\r\n")


def _rabattindex(zip_pfade, stdout=None):
    """Rabattgruppe -> `Rabattgruppe`, gelesen aus allen .RAB-Membern der ZIPs.

    Sammelt die Rabatttabelle aus einer separaten .RAB-ZIP (--rabatt) UND aus
    einer im Artikelstamm-ZIP mitgelieferten .RAB-Datei (Bosch/Junkers bündeln
    beides). Ohne .RAB-Datei bleibt die Tabelle leer → EK unbekannt.
    """
    tabelle = {}
    for zip_pfad in zip_pfade:
        if not zip_pfad:
            continue
        with zipfile.ZipFile(zip_pfad) as z:
            rab_member = [n for n in z.namelist() if _ist_rab(n)]
            for m in rab_member:
                for zeile in _zeilen(zip_pfad, member=m):
                    if zeile.startswith("R;"):
                        r = datanorm.parse_rabattgruppe(zeile)
                        if r.gruppe:
                            tabelle[r.gruppe] = r
    if stdout and tabelle:
        stdout(f"  Rabatttabelle: {len(tabelle)} Rabattgruppen")
    return tabelle


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
        parser.add_argument(
            "--stamm", required=True,
            help="ZIP mit dem Artikelstamm (datanorm.001/.002, Groß/Klein egal)",
        )
        parser.add_argument(
            "--preise",
            help="ZIP mit datpreis.001 (B&O-Weg: separate P-Sätze). Fehlt sie, "
            "wird der Preis aus dem A-Satz gelesen (Herstellerkataloge).",
        )
        parser.add_argument(
            "--rabatt",
            help="ZIP mit einer .RAB-Rabatttabelle (Herstellerkataloge). Löst die "
            "Rabattgruppe am A-Satz zum EK auf. Fehlt sie, bleibt der EK unbekannt.",
        )
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

        # Rabatttabelle: aus --rabatt UND aus einer im Stamm-ZIP mitgelieferten .RAB.
        rabatt_tabelle = _rabattindex(
            [opts.get("rabatt"), opts["stamm"]], stdout=self.stdout.write
        )
        if rabatt_tabelle:
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
            rg = rabatt_tabelle.get(a.rabattgruppe) if a.rabattgruppe else None
            ek = lp = None
            herkunft = "kein Preissatz"
            if p is not None:
                # B&O-Weg: Preis aus separatem P-Satz.
                ek, lp = datanorm.einkaufspreis(p, a.preiseinheit)
                if p.preiskennzeichen == datanorm.PREISKENNZEICHEN_NETTO:
                    herkunft = "Nettopreis (PKZ 2)"
                elif p.rabatt_kennzeichen == datanorm.RABATT_PROZENT:
                    herkunft = f"Liste - {Decimal(p.rabatt_wert)/100:.2f} % Rabatt"
                elif p.rabatt_kennzeichen == datanorm.RABATT_GRUPPE:
                    herkunft = f"Rabattgruppe {a.rabattgruppe} (nicht auflösbar)"
                else:
                    herkunft = f"Rabattkennzeichen {p.rabatt_kennzeichen}"
            elif a.listenpreis is not None:
                # Herstellerkatalog: Preis steckt im A-Satz.
                ek, lp = datanorm.preis_aus_artikel(a, rabatt=rg)
                if a.preiskennzeichen == datanorm.PREISKENNZEICHEN_NETTO:
                    herkunft = "A-Satz Nettopreis (PKZ 2)"
                elif rg is not None and ek is not None:
                    herkunft = f"A-Satz Liste - Rabattgruppe {a.rabattgruppe}"
                elif a.rabattgruppe:
                    herkunft = f"A-Satz Liste, Rabattgruppe {a.rabattgruppe} (keine .RAB)"
                else:
                    herkunft = "A-Satz Liste (ohne Rabattgruppe)"

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
        # Der B&O-Weg braucht die Preisdatei als EK-Quelle. Herstellerkataloge
        # tragen den Preis im A-Satz (EK aus der Rabattgruppe via --rabatt) — dort
        # ist --preise nicht erforderlich.
        if not opts.get("preise") and not opts.get("rabatt"):
            self.stdout.write(self.style.WARNING(
                "  Weder --preise noch --rabatt: Listenpreise aus dem A-Satz, "
                "Einkaufspreise bleiben unbekannt (EK = NULL)."
            ))

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

        preis_liste, preis_netto = {}, {}
        if opts.get("preise"):
            self.stdout.write("  Lese Preisdatei …")
            preis_liste, preis_netto = _preisindex(opts["preise"], stdout=self.stdout.write)
        rabatt_tabelle = _rabattindex(
            [opts.get("rabatt"), opts["stamm"]], stdout=self.stdout.write
        )

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
                # B&O-Weg: Preis aus separatem P-Satz.
                ek, lp = datanorm.einkaufspreis(p, a.preiseinheit)
            else:
                # Herstellerkatalog: Preis aus dem A-Satz, EK über die Rabattgruppe.
                rg = rabatt_tabelle.get(a.rabattgruppe) if a.rabattgruppe else None
                ek, lp = datanorm.preis_aus_artikel(a, rabatt=rg)
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
