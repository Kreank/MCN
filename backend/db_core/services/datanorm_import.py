"""DATANORM-Import über das Frontend: Datei einlesen, Artikelstamm + Preise gegen
eine bestehende Lieferanten-Anbindung upserten.

Das Parsen erledigt der reine Parser `services/datanorm.py` (Satzarten,
Preissemantik); dieser Service ist die **schreibende Hälfte** — er ist bewusst
eigenständig neben dem CLI-Command (`management/commands/datanorm_import.py`), das
für den Erstimport ganzer Kataloge (mehrere GB) gedacht ist und Re-Importe
ablehnt. Über das Frontend geht es um überschaubare Dateien und vor allem um den
**wiederholten Preis-/Stammdaten-Abgleich**: darum upsertet dieser Service.

Ablauf je Artikel (A-Satz + optional B/D):

* **Verarbeitungskennzeichen L (Löschung):** der Artikel wird auf INAKTIV gesetzt
  und die offene Lieferantenreferenz beendet (`valid_until = heute`). Kein echtes
  Löschen (No-Delete-Trigger).
* **N/A (neu/Änderung):** wiedererkannt wird über die Lieferantenreferenz
  (`source_namespace` + `supplier_article_number`), nicht über die Artikelnummer —
  die ist ein Anzeigename und darf bei Kollisionen zwischen Katalogen ausweichen.
  Ist der Artikel unbekannt, wird er samt Referenz angelegt (unter der NACKTEN
  Lieferantennummer, wie sie auf dessen Rechnung steht); sonst werden Stammfelder
  und der
  Preis der offenen Referenz aktualisiert (EK/Listenpreis/Rabattgruppe/
  Preiseinheit/`last_imported_at`). Die Identitätsfelder der Referenz sind per
  Trigger eingefroren — nur Preis/Gültigkeit sind veränderlich.

Sicherheit: DATANORM-Preisfallen (Ganzzahl-Cent, Preiseinheit, Rabattkennzeichen)
liegen im Parser; hier wird nur geschrieben, was der Parser als `Decimal` je EINER
Mengeneinheit liefert. Unbekannter EK bleibt **NULL, nie 0** (DB-CHECK: kein Preis
⇒ keine Währung). `dry_run` schreibt nichts und liefert dieselbe Auswertung — für
eine Vorschau vor dem Import.
"""
import io
import uuid
import zipfile
from datetime import date, datetime, timezone as dt_timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Article,
    ArticleSupplierReference,
    SupplierConnection,
)
from db_core.services import datanorm, datanorm_katalog

# Obergrenzen für den Frontend-Weg: sehr große Vollkataloge gehören ins CLI-
# Command (Streaming aus Datei), nicht durch einen Upload in den Speicher.
MAX_ENTPACKT = 200 * 1024 * 1024  # entpackte Bytes je Datei (Zip-Bomben-Schutz)
MAX_ZEILEN = 3_000_000          # Schutz vor Zip-Bomben / Riesenkatalogen
MAX_ARTIKEL = 100_000           # Höchstzahl verarbeiteter Artikel je Import
MAX_FEHLER_SAMPLE = 50          # so viele Fehler werden im Ergebnis gemeldet
MAX_ARTIKEL_SAMPLE = 20         # so viele Beispielzeilen werden zurückgegeben
_BATCH = 500                    # Artikel je Schreib-Transaktion


class DatanormImportFehler(ValueError):
    """Der Import ist fachlich/technisch unzulässig (→ 422)."""


def _now():
    return datetime.now(dt_timezone.utc)


def _entpackt(daten: bytes) -> bytes:
    """Liefert den DATANORM-Inhalt aus `daten` (ZIP mit genau einer Datei ODER
    rohe Datei) als Bytes — mit hartem Deckel auf die ENTPACKTE Größe.

    Der Deckel schützt vor Zip-Bomben: eine reine Zeilenzählung würde einen
    Eintrag OHNE Zeilenumbruch nicht bremsen (er entpackt beim ersten Lesen
    komplett). Deshalb wird höchstens `MAX_ENTPACKT` (+1) gelesen und danach
    abgebrochen — auch bei gelogenem Header."""
    if daten[:4] != b"PK\x03\x04":
        return daten
    with zipfile.ZipFile(io.BytesIO(daten)) as z:
        namen = [n for n in z.namelist() if not n.endswith("/")]
        if len(namen) != 1:
            raise DatanormImportFehler(
                f"Das ZIP muss genau eine Datei enthalten (gefunden: {len(namen)})."
            )
        info = z.getinfo(namen[0])
        if info.file_size > MAX_ENTPACKT:
            raise DatanormImportFehler(
                f"Die entpackte Datei ist zu groß (> {MAX_ENTPACKT // 1_048_576} MB). "
                "Für Vollkataloge das CLI-Kommando nutzen."
            )
        with z.open(namen[0]) as roh:
            inhalt = roh.read(MAX_ENTPACKT + 1)
    if len(inhalt) > MAX_ENTPACKT:
        raise DatanormImportFehler(
            f"Die entpackte Datei ist zu groß (> {MAX_ENTPACKT // 1_048_576} MB). "
            "Für Vollkataloge das CLI-Kommando nutzen."
        )
    return inhalt


def _zeilen_aus_bytes(daten: bytes):
    """DATANORM-Zeilen aus `daten` (ZIP mit genau einer Datei ODER rohe Datei),
    dekodiert als CP850. Größe ist über `_entpackt` (Bytes) und `MAX_ZEILEN`
    (Zeilen) begrenzt."""
    text = _entpackt(daten).decode(datanorm.ENCODING, errors="replace")
    zeilen = text.splitlines()
    if len(zeilen) > MAX_ZEILEN:
        raise DatanormImportFehler(
            "Die Datei ist zu groß für den Frontend-Import "
            f"(> {MAX_ZEILEN:,} Zeilen). Für Vollkataloge das CLI-Kommando nutzen."
        )
    yield from zeilen


def _preisindex(daten: bytes):
    """Artikelnummer → bester Preissatz (Netto gewinnt vor Liste). Wie im Command,
    aber aus Bytes."""
    liste, netto = {}, {}
    for zeile in _zeilen_aus_bytes(daten):
        if not zeile.startswith("P;"):
            continue
        for p in datanorm.parse_preise(zeile):
            ziel = netto if p.preiskennzeichen == datanorm.PREISKENNZEICHEN_NETTO else liste
            ziel[p.artikelnummer] = p
    return liste, netto


def _artikel_bloecke(daten: bytes):
    """Streamt (Artikel, Zusatz|None, Langtext) je Artikel aus Bytes."""
    a = b = None
    texte = []

    def fertig():
        return a, b, "\n".join(t for _, t in sorted(texte)) or None

    for zeile in _zeilen_aus_bytes(daten):
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


def _preis_aus_artikel(a):
    """EK/Listenpreis direkt aus dem A-Satz, wenn kein separater Preis-Satz
    vorliegt. Das Preiskennzeichen des A-Satzes bestimmt die Bedeutung des
    Preisfelds: Netto (2) = EK direkt; Liste (1) = Listenpreis, EK ohne Rabatt
    unbekannt; Werk (3)/sonst = kein EK. Gibt `(ek, listenpreis)` zurück."""
    if a.preiskennzeichen == datanorm.PREISKENNZEICHEN_NETTO:
        return a.listenpreis, None
    return None, a.listenpreis


def _stammfelder(a, b, langtext, lp, profil):
    """Gemeinsame Artikel-Stammfelder für Anlegen/Ändern.

    Matchcode und die Hersteller-Angaben kommen aus dem Katalogprofil — welches
    B-Satz-Feld was bedeutet, unterscheidet sich je Absender (siehe
    `datanorm_katalog`). Früher wurde hier für alle Kataloge gleich gemappt; das
    hat 2 Mio Artikeln eine Herstellernummer angedichtet, die es nicht gibt.
    """
    return {
        "description": (a.bezeichnung or a.artikelnummer)[:2000],
        "long_description": langtext,
        "unit": (a.mengeneinheit or "Stk"),
        "list_price": lp,
        "gtin": (b.ean if b else None),
        "product_group": (b.warengruppe if b else None),
        **datanorm_katalog.identitaetsfelder(profil, a, b),
    }


def _artikel_zu_referenz(namespace, nummer):
    """Der Artikel hinter (Namensraum, Lieferanten-Artikelnummer).

    Die Identität eines importierten Artikels hängt an seiner LIEFERANTEN-
    REFERENZ, nicht an der Artikelnummer. Die Artikelnummer ist ein Anzeigename:
    Sie soll die nackte Nummer des Lieferanten sein (`CUS15H`, wie auf dessen
    Rechnung und im Shop), muss aber ausweichen können, wenn ein zweiter Katalog
    dieselbe Nummer für etwas anderes vergibt. Hinge die Wiedererkennung daran,
    würde ein ausgewichener Artikel beim nächsten Import doppelt angelegt.
    """
    ref = (
        ArticleSupplierReference.objects.filter(
            source_system="DATANORM",
            source_namespace=namespace,
            supplier_article_number=nummer,
        )
        .order_by("-valid_from")
        .first()
    )
    if ref is None:
        return None
    return Article.objects.filter(id=ref.article_id).first()


def _freie_artikelnummer(nummer, namespace, dry_run):
    """Artikelnummer für einen NEU anzulegenden Artikel — kollisionsfrei.

    Der Leitkatalog (B&O) behält die nackte Nummer immer: Dort wird bestellt, und
    eine Bestellnummer, die je nach Importreihenfolge mal so und mal anders
    heißt, ist wertlos. Belegt ein Fremdkatalog die Nummer bereits, weicht DIESER
    aus — sein Artikel wird umbenannt, seine Identität hängt ohnehin an der
    Referenz, nicht am Namen.
    """
    def belegt(kandidat):
        return Article.objects.filter(article_number=kandidat).exists()

    if namespace == datanorm_katalog.LEITKATALOG and belegt(nummer):
        weichender = Article.objects.filter(article_number=nummer).first()
        fremd_ns = (
            ArticleSupplierReference.objects.filter(article_id=weichender.id)
            .values_list("source_namespace", flat=True)
            .first()
            or "fremd"
        )
        if not dry_run:
            Article.objects.filter(id=weichender.id).update(
                article_number=datanorm_katalog.ausweichnummer(
                    nummer, fremd_ns, belegt=belegt
                )
            )
    return datanorm_katalog.artikelnummer(nummer, namespace, belegt=belegt)


def import_datanorm(actor_app_user_id, *, connection_id, stamm_bytes,
                    preise_bytes=None, dry_run=False):
    """Importiert eine DATANORM-Datei gegen die Anbindung `connection_id`.

    `stamm_bytes` ist die Stammdatei (ZIP oder roh) mit A/B/D-Sätzen; `preise_bytes`
    optional die separate Preisdatei (P-Sätze). Fehlt sie, werden Preise — falls
    vorhanden — aus P-Sätzen der Stammdatei gelesen. Gibt eine Auswertung zurück
    (angelegt/aktualisiert/deaktiviert/ohne EK, Fehlerliste, Beispiele). `dry_run`
    schreibt nichts.
    """
    conn = SupplierConnection.objects.filter(id=connection_id).first()
    if conn is None:
        raise DatanormImportFehler("Anbindung nicht gefunden.")
    # Hersteller liefern ihre Ersatzteilkataloge ebenfalls als DATANORM (Bosch,
    # Vaillant, Buderus …) — der Import steht deshalb beiden Anbindungsarten
    # offen. Was sich unterscheidet, ist die FELDBEDEUTUNG, und die kommt aus
    # `connection_kind`: nur wo der Lieferant der Hersteller ist, darf aus der
    # Artikelnummer eine Herstellernummer werden (siehe datanorm_katalog).
    namespace = conn.source_namespace
    supplier_id = conn.supplier_party_id
    profil = datanorm_katalog.profil(
        conn.connection_kind, hersteller_name=conn.label
    )
    if not stamm_bytes:
        raise DatanormImportFehler("Die Datei ist leer.")

    # Vorlauf zur Anzeige (Version/Währung/Stand) — defensiv, nie fatal.
    vorlauf = None
    try:
        erste = next(iter(_zeilen_aus_bytes(stamm_bytes)))
        vorlauf = datanorm.parse_vorlauf(erste)
    except (StopIteration, datanorm.DatanormFehler):
        vorlauf = None

    preis_liste, preis_netto = _preisindex(preise_bytes or stamm_bytes)

    ergebnis = {
        "namespace": namespace,
        "version": vorlauf.version if vorlauf else None,
        "waehrung": vorlauf.waehrung if vorlauf else None,
        "stand": vorlauf.datum if vorlauf else None,
        "angelegt": 0,
        "aktualisiert": 0,
        "deaktiviert": 0,
        "ohne_einkaufspreis": 0,
        "verarbeitet": 0,
        "fehler": [],
        "beispiele": [],
        "dry_run": dry_run,
    }
    heute = date.today()

    puffer = []

    def flush():
        if not puffer:
            return
        # Reads + Writes desselben Stapels in EINER Transaktion (Audit-Kontext).
        with as_business_error():
            with business_transaction(actor_app_user_id):
                for block in puffer:
                    _verarbeite(block, namespace, supplier_id, preis_liste,
                                preis_netto, heute, dry_run, ergebnis, profil)
        puffer.clear()

    for a, b, langtext in _artikel_bloecke(stamm_bytes):
        if ergebnis["verarbeitet"] >= MAX_ARTIKEL:
            ergebnis["fehler"].append(
                f"Abbruch bei {MAX_ARTIKEL:,} Artikeln (Frontend-Obergrenze). "
                "Für größere Kataloge das CLI-Kommando nutzen."
            )
            break
        puffer.append((a, b, langtext))
        if len(puffer) >= _BATCH:
            flush()
    flush()

    if not dry_run and ergebnis["verarbeitet"] > 0:
        with as_business_error():
            with business_transaction(actor_app_user_id):
                SupplierConnection.objects.filter(id=connection_id).update(
                    last_import_at=_now()
                )

    return ergebnis


def _verarbeite(block, namespace, supplier_id, preis_liste, preis_netto,
                heute, dry_run, ergebnis, profil):
    a, b, langtext = block
    ergebnis["verarbeitet"] += 1
    try:
        # Preiseinheit gegen den DB-CHECK (0..3) absichern, BEVOR geschrieben wird —
        # ein ungültiger Wert (bei fehlendem Preis vom Parser nicht abgefangen)
        # würde sonst als 23514 die ganze Batch-Transaktion abbrechen (500).
        if a.preiseinheit not in datanorm.PREISEINHEIT_DIVISOR:
            raise datanorm.DatanormFehler(
                f"ungültige Preiseinheit {a.preiseinheit!r} (erlaubt: 0–3)."
            )
        # Löschung: Artikel deaktivieren, offene Referenz beenden.
        if a.vkz == datanorm.VKZ_LOESCHUNG:
            artikel = _artikel_zu_referenz(namespace, a.artikelnummer)
            if artikel is not None:
                if not dry_run:
                    # INAKTIV ist das eigentliche Löschsignal (kein echtes Löschen).
                    Article.objects.filter(id=artikel.id).update(status="INAKTIV")
                    # Offene Referenz beenden — aber nur, wenn sie nicht HEUTE
                    # beginnt: `valid_until > valid_from` ist ein DB-CHECK, ein am
                    # selben Tag angelegter Datensatz ließe sich sonst nicht am
                    # selben Tag schließen (in der Praxis liegen Import und
                    # Löschung Tage auseinander). Die EK-Historie bleibt erhalten.
                    ArticleSupplierReference.objects.filter(
                        article_id=artikel.id, source_system="DATANORM",
                        source_namespace=namespace, valid_until__isnull=True,
                        valid_from__lt=heute,
                    ).update(valid_until=heute)
                ergebnis["deaktiviert"] += 1
                _beispiel(ergebnis, a, "deaktiviert", None)
            return

        # Preis bestimmen: bevorzugt aus dem Preis-Satz (Netto gewinnt vor Liste);
        # fehlt einer, direkt aus dem A-Satz (dessen Preiskennzeichen bestimmt, ob
        # der Preis Netto/Liste/Werk ist). Listenpreis als Rückfall für list_price.
        p = preis_netto.get(a.artikelnummer) or preis_liste.get(a.artikelnummer)
        if p is not None:
            ek, lp = datanorm.einkaufspreis(p, a.preiseinheit)
        else:
            ek, lp = _preis_aus_artikel(a)
        if lp is None:
            lp = a.listenpreis
        if ek is None:
            ergebnis["ohne_einkaufspreis"] += 1
        waehrung = "EUR" if ek is not None else None

        artikel = _artikel_zu_referenz(namespace, a.artikelnummer)
        felder = _stammfelder(a, b, langtext, lp, profil)
        katalog_id = datanorm_katalog.katalog_id(profil, b)

        if artikel is None:
            nummer = _freie_artikelnummer(a.artikelnummer, namespace, dry_run)
            _anlegen(namespace, supplier_id, a, nummer, felder, ek, lp,
                     waehrung, heute, dry_run, katalog_id)
            ergebnis["angelegt"] += 1
            _beispiel(ergebnis, a, "angelegt", ek)
        else:
            _aendern(artikel, namespace, supplier_id, a, felder, ek, lp,
                     waehrung, heute, dry_run, katalog_id)
            ergebnis["aktualisiert"] += 1
            _beispiel(ergebnis, a, "aktualisiert", ek)
    except datanorm.DatanormFehler as exc:
        if len(ergebnis["fehler"]) < MAX_FEHLER_SAMPLE:
            ergebnis["fehler"].append(f"Artikel {a.artikelnummer}: {exc}")


def _anlegen(namespace, supplier_id, a, nummer, felder, ek, lp, waehrung,
             heute, dry_run, katalog_id=None):
    if dry_run:
        return
    artikel_id = uuid.uuid4()
    Article.objects.create(
        id=artikel_id, article_number=nummer, line_type="MATERIAL",
        status="AKTIV", version=1, **felder,
    )
    ArticleSupplierReference.objects.create(
        id=uuid.uuid4(), article_id=artikel_id, supplier_party_id=supplier_id,
        source_system="DATANORM", source_namespace=namespace,
        supplier_article_number=a.artikelnummer,
        last_purchase_price=ek, list_price=lp, currency=waehrung,
        discount_group=a.rabattgruppe, price_unit_code=a.preiseinheit,
        supplier_catalog_id=katalog_id,
        last_imported_at=_now(), valid_from=heute,
    )


def _aendern(artikel, namespace, supplier_id, a, felder, ek, lp, waehrung,
             heute, dry_run, katalog_id=None):
    if dry_run:
        return
    Article.objects.filter(id=artikel.id).update(status="AKTIV", **felder)
    open_ref = ArticleSupplierReference.objects.filter(
        article_id=artikel.id, source_system="DATANORM",
        source_namespace=namespace, valid_until__isnull=True,
    ).first()
    if open_ref is not None:
        # Nur Preis-/Gültigkeitsfelder ändern — Identität ist per Trigger fix.
        # Die Katalog-ID gehört nicht zur Identität: Ein neuer Katalogstand darf
        # sie korrigieren, ohne die Referenz beenden zu müssen.
        ArticleSupplierReference.objects.filter(id=open_ref.id).update(
            last_purchase_price=ek, list_price=lp, currency=waehrung,
            discount_group=a.rabattgruppe, price_unit_code=a.preiseinheit,
            supplier_catalog_id=katalog_id,
            last_imported_at=_now(),
        )
    else:
        ArticleSupplierReference.objects.create(
            id=uuid.uuid4(), article_id=artikel.id, supplier_party_id=supplier_id,
            source_system="DATANORM", source_namespace=namespace,
            supplier_article_number=a.artikelnummer,
            last_purchase_price=ek, list_price=lp, currency=waehrung,
            discount_group=a.rabattgruppe, price_unit_code=a.preiseinheit,
            supplier_catalog_id=katalog_id,
            last_imported_at=_now(), valid_from=heute,
        )


def _beispiel(ergebnis, a, aktion, ek):
    if len(ergebnis["beispiele"]) < MAX_ARTIKEL_SAMPLE:
        ergebnis["beispiele"].append({
            "artikelnummer": a.artikelnummer,
            "bezeichnung": (a.bezeichnung or "")[:80],
            "aktion": aktion,
            "einkaufspreis": str(ek) if ek is not None else None,
        })
