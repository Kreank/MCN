"""Wie ein KONKRETER Katalog die DATANORM-Nebensatzfelder belegt.

Die Norm legt die Satzarten fest, nicht verlässlich die Bedeutung jedes Feldes.
Der B-Satz (Artikelnebensatz) hat nominell Matchcode (Feld 3) und „alternative
Artikelnummer" (Feld 4) — was dort tatsächlich steht, entscheidet der Absender.
Genau daran ist der Artikelstamm einmal entgleist:

    Bis 03.08.2026 hat der Import für ALLE Kataloge gleich gemappt —
    Feld 3 → manufacturer_name, Feld 4 → manufacturer_number.
    Ergebnis bei 2.043.336 B&O-Artikeln: unter „Hersteller" stand der
    Matchcode (`CUSSH01510`, bei Bosch sogar die Kurzbezeichnung
    `6KT-SCHRAUBE`), unter „Hersteller-Nr." eine B&O-INTERNE Nummer.
    Diese Nummer existiert außerhalb von B&O nirgends — sie ließ sich weder
    beim Hersteller noch beim Großhändler nachschlagen.

Empirischer Befund aus den echten Dateien (nachgeprüft gegen dieselben Artikel
im Alt-System HERO, das die Hersteller-Nr. bei B&O-Ware korrekt LEER lässt):

    Großhandel (B&O)
        B;N;CUS15H;CUSSH01510;ZRB2071510;1G00003;0;0;0; ; ; ;0;0; ; ;
        Feld 3 = Matchcode (mal Fabrikat „GEBERIT", mal reiner Suchcode)
        Feld 4 = B&O-intern. 55,8 % der Sätze tragen das Muster
                 XXXSRT<laufende Zahl>; über alle 2.043.336 Artikel ist JEDER
                 Wert genau einmal vergeben. Eine Herstellernummer kann das
                 nicht sein — Geberit HyTouch steht als TZZ459CH/WE/WG drin
                 (B&O-Farbcode), echte Geberit-Nummern sind 116.xxx.xx.x.
        Feld 9 = EAN: in 0 von 2.043.336 Sätzen belegt.

    Hersteller (Bosch)
        B;N;10000946;EINLEGEBLENDE;1-000-094-6; ;0;0;0;4047416574165; ...
        Feld 3 = Kurzbezeichnung, KEIN Hersteller.
        Feld 4 = die TTNR mit Bindestrichen, also die Artikelnummer selbst.

    Hersteller (Vaillant)
        B;N;0010027751; ; ; ;0;0;0;4024074922828; ; ;0;0; ; ;
        Feld 3/4 leer. Artikelnummer = Vaillant-Nummer.

Daraus die beiden Leitregeln:

1. **Die Katalogart entscheidet, nicht der Namensraum.** Ob eine
   Herstellernummer abgeleitet werden darf, hängt daran, ob der Lieferant der
   Hersteller IST — das steht bereits an der Anbindung
   (`pricing.supplier_connection.connection_kind`). Ein neuer Herstellerkatalog
   (Buderus, Viessmann …) bekommt damit automatisch das richtige Mapping, ohne
   dass hier eine Liste gepflegt werden muss.
2. **Im Zweifel leer statt falsch.** Ein leeres Herstellerfeld ist ehrlich; eine
   erfundene Nummer kostet bei der Nachbestellung Zeit und Vertrauen. Der
   Herstellername wird deshalb NIE aus dem Matchcode abgeleitet — der trägt je
   nach Katalog Fabrikat, Kurzbezeichnung oder einen reinen Suchcode.
"""
from dataclasses import dataclass

#: Werte von pricing.supplier_connection.connection_kind
KIND_GROSSHAENDLER = "GROSSHAENDLER"
KIND_HERSTELLER = "HERSTELLER"

#: Woher die Herstellernummer eines Artikels stammt.
HN_KEINE = "keine"                  # Katalog liefert keine — Feld bleibt leer
HN_ARTIKELNUMMER = "artikelnummer"  # Herstellerkatalog: Artikelnummer IST die Nummer


@dataclass(frozen=True)
class KatalogProfil:
    """Feldbelegung eines Katalogs."""

    #: Feld 4 als katalog-interne Nummer sichern (Rückkanal zum Lieferanten).
    katalog_id_aus_feld4: bool = True
    #: Fester Herstellername, wenn der Lieferant zugleich der Hersteller ist.
    hersteller_name: str | None = None
    #: Woher die Herstellernummer kommt (HN_*).
    hersteller_nummer: str = HN_KEINE


#: Großhandel: Matchcode ja, Herstellernummer nein, Feld 4 als Katalog-ID sichern.
#: Zugleich das konservative Standardprofil für alles Ungeklärte.
GROSSHANDEL = KatalogProfil()


def profil(connection_kind: str | None, *, hersteller_name: str | None = None) -> KatalogProfil:
    """Profil aus der Art der Lieferanten-Anbindung.

    `hersteller_name` ist der Anzeigename der Anbindung (Label bzw. Partei) und
    wird nur bei `KIND_HERSTELLER` verwendet. Alles andere — auch ein unbekannter
    oder fehlender Wert — bekommt das Großhandelsprofil und behauptet damit keine
    Herstellernummer.
    """
    if (connection_kind or "").strip().upper() == KIND_HERSTELLER:
        name = (hersteller_name or "").strip() or None
        return KatalogProfil(
            katalog_id_aus_feld4=False,
            hersteller_name=name,
            hersteller_nummer=HN_ARTIKELNUMMER,
        )
    return GROSSHANDEL


#: Dieser Namensraum behält bei Nummernkollisionen die nackte Artikelnummer.
#: B&O ist der Beschaffungskatalog des Betriebs — dort wird bestellt, also muss
#: dessen Nummer die sein, die überall unverfälscht auftaucht. Herstellerkataloge
#: sind Nachschlagewerke und weichen aus.
LEITKATALOG = "bo"


def identitaetsfelder(p: KatalogProfil, a, b) -> dict:
    """Matchcode/Hersteller-Felder für einen Artikel nach Katalogprofil.

    `a` ist der A-Satz, `b` der B-Satz (oder None). Liefert genau die Schlüssel,
    die auf `pricing.article` gesetzt werden.
    """
    hersteller_nummer = (
        (a.artikelnummer or None) if p.hersteller_nummer == HN_ARTIKELNUMMER else None
    )
    return {
        "matchcode": (b.matchcode if b else None),
        "manufacturer_name": p.hersteller_name,
        "manufacturer_number": hersteller_nummer,
    }


def katalog_id(p: KatalogProfil, b) -> str | None:
    """Katalog-interne Nummer (B-Satz Feld 4), sofern das Profil sie führt."""
    if not p.katalog_id_aus_feld4 or b is None:
        return None
    return b.alt_artikelnummer or None


def artikelnummer(nummer: str, namespace: str, *, belegt) -> str:
    """Die Artikelnummer, unter der ein NEUER Artikel angelegt wird.

    Grundfall ist die nackte Lieferantennummer — genau die Nummer, die auf dem
    Angebot, der Rechnung und im Shop des Lieferanten steht. Nur wenn ein anderer
    Katalog dieselbe Nummer bereits belegt, wird ausgewichen; die Kollision ist
    real und selten: `509010` ist bei B&O ein Kupferwinkel und bei Vaillant ein
    Seitenteil (9 Fälle bei 2,07 Mio Artikeln).

    `belegt(kandidat)` meldet, ob eine Nummer schon vergeben ist. Der Leitkatalog
    weicht NICHT aus — für ihn ist die nackte Nummer garantiert; kollidierende
    Fremdartikel werden vom Aufrufer umbenannt.
    """
    if namespace == LEITKATALOG or not belegt(nummer):
        return nummer
    return ausweichnummer(nummer, namespace, belegt=belegt)


def ausweichnummer(nummer: str, namespace: str, *, belegt) -> str:
    """Nummer, die garantiert ausweicht — auch für den Leitkatalog.

    Gebraucht beim Verdrängen: Wenn B&O die nackte Nummer beansprucht, muss der
    bisherige Inhaber zwingend einen anderen Namen bekommen, sonst kollidiert der
    UNIQUE-Index.
    """
    kandidat = f"{nummer}-{namespace}"
    if not belegt(kandidat):
        return kandidat
    lauf = 2
    while belegt(f"{kandidat}-{lauf}"):
        lauf += 1
    return f"{kandidat}-{lauf}"
