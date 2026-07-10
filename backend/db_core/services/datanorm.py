"""DATANORM-4-Parser: reine Funktionen, kein Datenbankzugriff.

Quelle der Wahrheit für die Preissemantik ist `db/migrations/0037_datanorm_grundlagen.sql`
(nach ausdrücklicher Warnung des Users am 05.07.2026) — hier noch einmal in Code:

    Preiskennzeichen 1 = LISTENPREIS (brutto)  → EK = Liste × (1 − Rabatt)
    Preiskennzeichen 2 = NETTOPREIS            → EK direkt
    Preiskennzeichen 3 = Werkspreis            → EK unbekannt (nicht raten)

Zwei Fallen, die Geld kosten:

1. **Preise sind Ganzzahlen in Cent.** `2380` sind 23,80 €, nicht 2380 €.
2. **Darauf wirkt die Preiseinheit** (Feld 8 des A-Satzes): 0 = je 1, 1 = je 10,
   2 = je 100, 3 = je 1000 Mengeneinheiten. Ein Listenpreis `1290` mit
   Preiseinheit 2 ist 12,90 € für 100 Stück, also 0,129 € je Stück. Wer die
   Preiseinheit ignoriert, legt den Artikel zum Hundertfachen an.

Der Rabatt im P-Satz trägt ein eigenes **Kennzeichen** (Feld 4), das bestimmt, wie
Feld 5 zu lesen ist:

    0 = Verweis auf eine Rabattgruppe (Wert ist KEIN Prozentsatz)
    1 = Rabattsatz in Hundertstel-Prozent (6800 → 68,00 %)
    2 = Multiplikator, drei Nachkommastellen (850 → ×0,850)
    3 = Aufschlag in Cent, wird addiert

`6800` blind als Prozent zu lesen wäre falsch, sobald das Kennzeichen 2 oder 3 ist.

Genauigkeit: Alles läuft über `Decimal`, nie über `float`. Der Preis je Einheit
wird auf vier Nachkommastellen quantisiert — bei Preiseinheit 100 ist
0,0774 €/Stück ein realer Wert (Stahlhaften), und auf zwei Stellen gerundet
entstünde ein Fehler von über drei Prozent.

Kodierung der Dateien: CP850 (DOS-Latin-1), nicht ISO-8859-1 und nicht UTF-8.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ENCODING = "cp850"

# Preis je EINER Mengeneinheit: vier Nachkommastellen. Kleinteile mit
# Preiseinheit 100/1000 wären auf zwei Stellen nicht mehr korrekt abbildbar.
_Q_EINHEITSPREIS = Decimal("0.0001")
_CENT = Decimal("100")

# Divisor je Preiseinheit-Kennzeichen (Feld 8 im A-Satz).
PREISEINHEIT_DIVISOR = {
    0: Decimal(1),
    1: Decimal(10),
    2: Decimal(100),
    3: Decimal(1000),
}

# Verarbeitungskennzeichen (Feld 2)
VKZ_NEU, VKZ_AENDERUNG, VKZ_LOESCHUNG = "N", "A", "L"

PREISKENNZEICHEN_LISTE = 1     # Bruttopreis, Rabatt anwenden
PREISKENNZEICHEN_NETTO = 2     # Nettopreis, kein Rabatt
PREISKENNZEICHEN_WERK = 3      # Werkspreis, EK unbekannt

RABATT_GRUPPE = 0              # Verweis auf Rabattgruppe, kein Prozentsatz
RABATT_PROZENT = 1             # Hundertstel-Prozent
RABATT_MULTIPLIKATOR = 2       # Tausendstel
RABATT_AUFSCHLAG = 3           # Cent, additiv


class DatanormFehler(ValueError):
    """Ein Satz lässt sich nicht sinnvoll lesen."""


def _int(wert, feld):
    wert = (wert or "").strip()
    if not wert:
        return None
    try:
        return int(wert)
    except ValueError:
        raise DatanormFehler(f"{feld}: '{wert}' ist keine Ganzzahl.")


def _txt(wert):
    wert = (wert or "").strip()
    return wert or None


def cent_zu_euro(cent):
    """Ganzzahl in Cent → Decimal-Euro. `2380` → 23.80."""
    if cent is None:
        return None
    return (Decimal(cent) / _CENT).quantize(Decimal("0.01"))


def preis_je_einheit(cent, preiseinheit):
    """Preis für EINE Mengeneinheit, unter Anwendung der Preiseinheit.

    `1290` mit Preiseinheit 2 (je 100) → 0.1290 € je Stück.
    Nicht auf zwei Stellen runden: bei Kleinteilen ist die dritte und vierte
    Nachkommastelle der Unterschied zwischen richtig und falsch.
    """
    if cent is None:
        return None
    divisor = PREISEINHEIT_DIVISOR.get(preiseinheit)
    if divisor is None:
        raise DatanormFehler(
            f"Unbekannte Preiseinheit {preiseinheit!r} (erlaubt: 0, 1, 2, 3)."
        )
    euro = Decimal(cent) / _CENT / divisor
    return euro.quantize(_Q_EINHEITSPREIS, rounding=ROUND_HALF_UP)


def rabatt_anwenden(listenpreis, kennzeichen, wert):
    """Wendet den Rabatt eines P-Satz-Blocks auf den Listenpreis an.

    Gibt None zurück, wenn der Einkaufspreis daraus NICHT bestimmbar ist — etwa
    bei einem Verweis auf eine Rabattgruppe, deren Tabelle (.RAB-Datei) uns nicht
    vorliegt. Dann ist der EK unbekannt, und unbekannt heißt None, nicht 0.
    """
    if listenpreis is None:
        return None
    if kennzeichen is None:
        return listenpreis
    if kennzeichen == RABATT_GRUPPE:
        return None          # ohne Rabatttabelle nicht auflösbar
    if wert is None:
        return listenpreis
    if kennzeichen == RABATT_PROZENT:
        prozent = Decimal(wert) / Decimal(10000)      # 6800 → 0.68
        if not (Decimal(0) <= prozent <= Decimal(1)):
            raise DatanormFehler(f"Rabattsatz {wert} liegt außerhalb 0–100 %.")
        ergebnis = listenpreis * (Decimal(1) - prozent)
    elif kennzeichen == RABATT_MULTIPLIKATOR:
        ergebnis = listenpreis * (Decimal(wert) / Decimal(1000))
    elif kennzeichen == RABATT_AUFSCHLAG:
        ergebnis = listenpreis + (Decimal(wert) / _CENT)
    else:
        raise DatanormFehler(f"Unbekanntes Rabattkennzeichen {kennzeichen!r}.")
    return ergebnis.quantize(_Q_EINHEITSPREIS, rounding=ROUND_HALF_UP)


# --- Satzarten --------------------------------------------------------------

@dataclass(frozen=True)
class Vorlauf:
    datum: str          # TTMMJJ, wie in der Datei
    version: str        # "04"
    waehrung: str       # "EUR"
    info: str


@dataclass(frozen=True)
class Artikel:
    vkz: str
    artikelnummer: str
    textkennzeichen: str | None
    kurztext1: str | None
    kurztext2: str | None
    preiskennzeichen: int | None
    preiseinheit: int
    mengeneinheit: str | None
    listenpreis_cent: int | None
    rabattgruppe: str | None
    warengruppe: str | None
    langtextschluessel: str | None

    @property
    def bezeichnung(self):
        """Kurztext 1 und 2 ergeben zusammen die Bezeichnung."""
        teile = [t for t in (self.kurztext1, self.kurztext2) if t]
        return " ".join(teile)

    @property
    def listenpreis(self):
        """Listenpreis je EINER Mengeneinheit (Preiseinheit angewandt)."""
        return preis_je_einheit(self.listenpreis_cent, self.preiseinheit)


@dataclass(frozen=True)
class Zusatz:
    """B-Satz. Der Matchcode trägt bei diesem Händler das Fabrikat."""
    vkz: str
    artikelnummer: str
    matchcode: str | None
    alt_artikelnummer: str | None
    ean: str | None
    warengruppe: str | None


@dataclass(frozen=True)
class Preis:
    artikelnummer: str
    preiskennzeichen: int
    preis_cent: int
    rabatt_kennzeichen: int | None
    rabatt_wert: int | None


def parse_vorlauf(zeile):
    """V-Satz: als EINZIGE Satzart positionsbasiert, nicht semikolongetrennt."""
    if not zeile.startswith("V"):
        raise DatanormFehler("Kein Vorlaufsatz.")
    datum = zeile[2:8]
    rest = zeile[8:]
    waehrung = rest[-3:].strip()
    version = rest[-5:-3].strip()
    return Vorlauf(
        datum=datum, version=version, waehrung=waehrung, info=rest[:-5].strip()
    )


def parse_artikel(zeile):
    f = zeile.rstrip("\r\n").split(";")
    if len(f) < 11 or f[0] != "A":
        raise DatanormFehler(f"Kein A-Satz: {zeile[:40]!r}")
    preiseinheit = _int(f[7], "Preiseinheit")
    return Artikel(
        vkz=f[1].strip(),
        artikelnummer=f[2].strip(),
        textkennzeichen=_txt(f[3]),
        kurztext1=_txt(f[4]),
        kurztext2=_txt(f[5]),
        preiskennzeichen=_int(f[6], "Preiskennzeichen"),
        preiseinheit=0 if preiseinheit is None else preiseinheit,
        mengeneinheit=_txt(f[8]),
        listenpreis_cent=_int(f[9], "Preis"),
        rabattgruppe=_txt(f[10]),
        warengruppe=_txt(f[11]) if len(f) > 11 else None,
        langtextschluessel=_txt(f[12]) if len(f) > 12 else None,
    )


def parse_zusatz(zeile):
    f = zeile.rstrip("\r\n").split(";")
    if len(f) < 6 or f[0] != "B":
        raise DatanormFehler(f"Kein B-Satz: {zeile[:40]!r}")
    ean = _txt(f[9]) if len(f) > 9 else None
    # Nur echte GTIN übernehmen; der Händler lässt das Feld oft leer oder auf "0".
    if ean is not None and (not ean.isdigit() or len(ean) < 8):
        ean = None
    return Zusatz(
        vkz=f[1].strip(),
        artikelnummer=f[2].strip(),
        matchcode=_txt(f[3]),
        alt_artikelnummer=_txt(f[4]),
        ean=ean,
        warengruppe=_txt(f[11]) if len(f) > 11 else None,
    )


def parse_langtext(zeile):
    """D-Satz → (artikelnummer, [(zeilennummer, text), ...]).

    Blöcke à vier Feldern: Zeilennummer, Formatkennzeichen, (frei), Text.
    Defensiv gelesen: die Quellenlage zum Formatkennzeichen ist unsicher, deshalb
    wird je Block das letzte nicht-leere Feld als Text genommen.
    """
    f = zeile.rstrip("\r\n").split(";")
    if len(f) < 4 or f[0] != "D":
        raise DatanormFehler(f"Kein D-Satz: {zeile[:40]!r}")
    artikelnummer = f[2].strip()
    zeilen = []
    rest = f[3:]
    for i in range(0, len(rest) - 3, 4):
        nummer = _int(rest[i], "Zeilennummer")
        text = _txt(rest[i + 3])
        if nummer is not None and text:
            zeilen.append((nummer, text))
    return artikelnummer, zeilen


def parse_preise(zeile):
    """P-Satz → Liste von `Preis`. Eine Zeile trägt mehrere Artikel-Blöcke à 9 Feldern."""
    f = zeile.rstrip("\r\n").split(";")
    if len(f) < 3 or f[0] != "P":
        raise DatanormFehler(f"Kein P-Satz: {zeile[:40]!r}")
    rest = f[2:]
    preise = []
    # Ein Block hat NEUN Datenfelder, nicht zehn:
    #   Artikelnr, PKZ, Preis, RabKzA, RabWertA, RabKzB, RabWertB, RabKzC, RabWertC
    # Die scheinbar zehnte Spalte beim naiven Split ist der Trenner zum nächsten
    # Block. Mit Schrittweite 10 verrutscht ab dem zweiten Artikel jedes Feld.
    for i in range(0, len(rest) - 8, 9):
        nummer = rest[i].strip()
        if not nummer:
            continue
        pkz = _int(rest[i + 1], "Preiskennzeichen")
        preis = _int(rest[i + 2], "Preis")
        if pkz is None or preis is None:
            continue
        preise.append(
            Preis(
                artikelnummer=nummer,
                preiskennzeichen=pkz,
                preis_cent=preis,
                rabatt_kennzeichen=_int(rest[i + 3], "Rabattkennzeichen"),
                rabatt_wert=_int(rest[i + 4], "Rabattwert"),
            )
        )
    return preise


def einkaufspreis(preis: Preis, preiseinheit: int):
    """Einkaufspreis je EINER Mengeneinheit aus einem P-Satz-Block.

    Gibt `(ek, listenpreis)` zurück; jeweils None, wenn nicht bestimmbar. Der
    Werkspreis (Kennzeichen 3) liefert bewusst keinen EK — er ist ein
    Herstellerabgabepreis, kein Einkaufspreis, und geraten wird nicht.
    """
    if preis.preiskennzeichen == PREISKENNZEICHEN_NETTO:
        return preis_je_einheit(preis.preis_cent, preiseinheit), None
    if preis.preiskennzeichen == PREISKENNZEICHEN_LISTE:
        liste = preis_je_einheit(preis.preis_cent, preiseinheit)
        ek = rabatt_anwenden(liste, preis.rabatt_kennzeichen, preis.rabatt_wert)
        return ek, liste
    if preis.preiskennzeichen == PREISKENNZEICHEN_WERK:
        return None, preis_je_einheit(preis.preis_cent, preiseinheit)
    raise DatanormFehler(f"Unbekanntes Preiskennzeichen {preis.preiskennzeichen!r}.")
