"""Der Bezug eines Belegs — um wessen Wohnung geht es hier eigentlich?

## Warum es diesen Baustein gibt

Ein Beleg trug bis hierher nur „Objekt: «Liegenschaft» · «Ort»". Bei einer WEG
mit 24 Einheiten und 24 verschiedenen Eigentümern (16 davon vermietet) sagt das
nichts: Man sieht nicht, um wessen Wohnung es geht, und die Zuordnung von
Angebot und Rechnung wird zur Ratearbeit. Gebraucht wird die Zeile, die auf
jedem Handwerkerbeleg an eine Verwaltung gehört:

    Eigentümer, Wohneinheit/Mieter — vertreten durch «Verwaltung»

## Die Regel dahinter (Sascha, 2026-08-01, siehe INVARIANTEN.md §2)

**Ab Strang-/Wohnungsabsperrung ist es Sondereigentum — Sache des Eigentümers.
Alles davor (Schacht, Keller, Steigleitung) ist Gemeinschaftseigentum und läuft
über die WEG.** Diese Grenze steht bereits im Schema: `COMMON_AREA` und
`TECHNICAL_ROOM` dürfen weder Eigentumsstand noch Belegung tragen (A-08/F-12) —
dort folgt das Eigentum der Gemeinschaft. Der Auflöser spricht sie nur aus.

## Die Eigentümer-Kaskade

Der Betrieb hat „so ziemlich alles vertreten", und die drei Welten unterscheiden
sich darin, **wo** der Eigentümer steht:

1. **WEG** — jede Wohnung hat einen eigenen Eigentümer: `tenure.ownership_period`
   an der Einheit, mit Anteilen als echte Brüche.
2. **Ein Eigentümer für das ganze Objekt** (Mietshaus) — anteilslos als
   `property.property_party_role` = PROPERTY_OWNER an der Liegenschaft
   (Entscheidung 2026-07-21: bewusst keine Schemaänderung dafür).
3. **Eigenheim** — dieselbe Rolle, nur ohne Verwaltung.

Deshalb wird in genau dieser Reihenfolge gefragt: Eigentumsstand der Einheit →
Eigentümerrolle an der Liegenschaft → Eigentümergemeinschaft. Die erste Antwort
gewinnt; sie ist zugleich die genaueste.

## Was hier NICHT passiert

* **Kein Rechnungsempfänger.** Wer die Post bekommt, entscheidet weiterhin die
  Beteiligtenrolle am Beleg (INVOICE_RECIPIENT/INVOICE_DEBTOR). Beauftragen,
  zahlen und Post bekommen sind drei Fragen — dieser Baustein beantwortet keine
  davon, er beschreibt nur den **Gegenstand**.
* **Kein Raten.** Fehlt die Verwaltung, entfällt die Zeile ersatzlos, statt leer
  dazustehen. Beim Eigenheim ist das der Normalfall, kein Mangel.

## Einfrieren

Das Ergebnis ist bewusst reines JSON (nur str/bool/list/None): Es wandert beim
Veröffentlichen in `billing_snapshot` und muss dort unverändert überdauern.
Live gelesen würde eine zwei Jahre alte Rechnung nach einem Eigentümerwechsel
plötzlich den **neuen** Eigentümer zeigen — ein hübsches Feature und ein
handfester Buchhaltungsfehler (B-30, GoBD).
"""
from datetime import date

from django.db.models import Q

#: Einheitsarten, die per Trigger weder Eigentum noch Belegung tragen (A-08/F-12).
GEMEINSCHAFTS_ARTEN = ("COMMON_AREA", "TECHNICAL_ROOM")

#: Herkunft der Eigentümerangabe — bestimmt die Beschriftung in der Ausgabe.
AUS_EINHEIT = "SONDEREIGENTUM"
AUS_OBJEKT = "OBJEKT"
AUS_GEMEINSCHAFT = "GEMEINSCHAFT"


def _zeitlich_gueltig(qs, stichtag):
    """Zeilen, die am Stichtag gelten — offenes Ende zählt als „läuft noch"."""
    return qs.filter(valid_from__lte=stichtag).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gte=stichtag)
    )


def _name(party):
    return (getattr(party, "display_name", None) or "").strip() or None


def einheit_bezeichnung(unit):
    """„Vorderhaus · WE 12" — was der Monteur am Klingelschild sucht.

    Das Gebäude steht vorn, weil es bei mehreren Häusern (Vorderhaus,
    Seitenflügel, Hinterhaus) die erste Orientierung ist; die Etage nur, wenn sie
    erfasst ist. Nichts wird ergänzt, was nicht in den Daten steht — eine
    geratene Etage schickt den Monteur in den falschen Stock.
    """
    if unit is None:
        return None
    teile = []
    gebaeude = getattr(unit, "building", None)
    if gebaeude is not None:
        teile.append((gebaeude.name or "").strip() or f"Haus {gebaeude.building_number}")
    nummer = (unit.unit_number or "").strip()
    etage = (unit.storey or "").strip()
    if etage:
        teile.append(etage)
    if nummer:
        # „WE 12" — außer die Nummer sagt das schon selbst („WE 12", „Whg. 3").
        teile.append(nummer if nummer[:1].isalpha() else f"WE {nummer}")
    return " · ".join(teile) or None


def _eigentuemer_der_einheit(unit, stichtag):
    """Eigentümer aus dem Eigentumsstand der Wohnung (WEG-Fall)."""
    periode = (
        _zeitlich_gueltig(unit.ownership_periods, stichtag)
        .order_by("-valid_from")
        .first()
    )
    if periode is None:
        return []
    return [
        n
        for n in (
            _name(i.owner_party)
            for i in periode.interests.select_related("owner_party").all()
        )
        if n
    ]


def _rollen_der_liegenschaft(prop, rolle, stichtag):
    """Beteiligte einer Liegenschaft in einer Rolle (anteilslos)."""
    if prop is None:
        return []
    zeilen = _zeitlich_gueltig(
        prop.party_roles.filter(role=rolle), stichtag
    ).select_related("party")
    return [n for n in (_name(z.party) for z in zeilen) if n]


def _belegung(unit, stichtag):
    """(Mieterliste, selbst_bewohnt) aus der geltenden Belegung der Wohnung."""
    if unit is None:
        return [], False
    belegung = (
        _zeitlich_gueltig(unit.occupancies, stichtag).order_by("-valid_from").first()
    )
    if belegung is None:
        return [], False
    if belegung.occupancy_type == "OWNER_OCCUPIED":
        return [], True
    namen = [
        n
        for n in (
            _name(p.party)
            for p in _zeitlich_gueltig(belegung.parties, stichtag).select_related("party")
        )
        if n
    ]
    return namen, False


def _verwaltung(prop, unit, stichtag):
    """Die verwaltende Partei — oder None, wenn es keine gibt (Eigenheim).

    Ein Teilmandat (SELECTED_UNITS) zählt nur, wenn es **diese** Einheit umfasst.
    Sonst stünde auf dem Beleg für Wohnung 12 die Verwaltung, die ausschließlich
    Wohnung 3 betreut.
    """
    if prop is None:
        return None
    mandate = (
        _zeitlich_gueltig(prop.mandates.exclude(status="ENDED"), stichtag)
        .select_related("management_party")
        .order_by("valid_from")
    )
    for mandat in mandate:
        if mandat.scope_type == "SELECTED_UNITS":
            if unit is None:
                continue
            if not mandat.mandate_units.filter(unit_id=unit.id).exists():
                continue
        name = _name(mandat.management_party)
        if name:
            return name
    return None


def bezug_aufloesen(work_order, prop, stichtag=None):
    """Löst den Belegbezug auf — Einheit, Eigentümer, Mieter, Verwaltung.

    `work_order` darf None sein (Beleg ohne Auftrag): Dann gibt es keine Einheit,
    und der Bezug beschreibt die Liegenschaft als Ganzes.

    Rückgabe ist reines JSON (siehe Modulkopf) oder None, wenn nichts Sinnvolles
    ableitbar war — dann bleibt der Beleg, wie er vorher aussah.
    """
    stichtag = stichtag or date.today()
    unit = getattr(work_order, "unit", None) if work_order is not None else None
    prop = prop or (getattr(work_order, "property", None) if work_order else None)

    gemeinschaft = unit is None or unit.unit_type in GEMEINSCHAFTS_ARTEN

    eigentuemer, herkunft = [], None
    if not gemeinschaft:
        eigentuemer = _eigentuemer_der_einheit(unit, stichtag)
        if eigentuemer:
            herkunft = AUS_EINHEIT
    if not eigentuemer:
        eigentuemer = _rollen_der_liegenschaft(prop, "PROPERTY_OWNER", stichtag)
        if eigentuemer:
            herkunft = AUS_OBJEKT
    if not eigentuemer:
        eigentuemer = _rollen_der_liegenschaft(prop, "COMMUNITY_OF_OWNERS", stichtag)
        if eigentuemer:
            herkunft = AUS_GEMEINSCHAFT

    mieter, selbst_bewohnt = ([], False) if gemeinschaft else _belegung(unit, stichtag)

    bezug = {
        "einheit": None if gemeinschaft else einheit_bezeichnung(unit),
        "gemeinschaftseigentum": bool(gemeinschaft),
        "eigentuemer": eigentuemer,
        "eigentuemer_herkunft": herkunft,
        "mieter": mieter,
        "selbst_bewohnt": bool(selbst_bewohnt),
        "verwaltung": _verwaltung(prop, unit, stichtag),
    }
    if not any((bezug["einheit"], eigentuemer, mieter, bezug["verwaltung"])):
        return None
    return bezug


def bezug_zeilen(bezug):
    """Beschriftete Zeilen für die Ausgabe — `[(Label, Wert), …]`.

    Eine Stelle für alle drei Ausgaben (Angebots-PDF, Rechnungs-PDF,
    Bildschirm). Liefen sie auseinander, entstünde genau der Fehler, der im Juli
    schon einmal passiert ist: Das Angebot zeigte eine nackte Namenszeile,
    während die Rechnung den vollen Anschriftsblock trug.

    Leere Angaben erzeugen **keine** Zeile — ein Beleg ohne Verwaltung soll nicht
    „Vertreten durch: —" tragen.
    """
    if not bezug:
        return []
    zeilen = []
    if bezug.get("einheit"):
        zeilen.append(("Wohneinheit", bezug["einheit"]))

    eigentuemer = bezug.get("eigentuemer") or []
    if eigentuemer:
        label = (
            "Eigentümergemeinschaft"
            if bezug.get("eigentuemer_herkunft") == AUS_GEMEINSCHAFT
            else ("Eigentümer" if len(eigentuemer) == 1 else "Eigentümer")
        )
        wert = ", ".join(eigentuemer)
        if bezug.get("selbst_bewohnt"):
            wert += " (bewohnt selbst)"
        zeilen.append((label, wert))

    mieter = bezug.get("mieter") or []
    if mieter:
        zeilen.append(("Mieter" if len(mieter) == 1 else "Mieter", ", ".join(mieter)))

    # „Gemeinschaftseigentum" gibt es nur, wo es eine Gemeinschaft GIBT. Das
    # Flag allein genügt dafür nicht: Es sagt bloß „keine Wohnung betroffen" und
    # ist auch beim Eigenheim gesetzt, das gar keine Einheiten führt. Dort stand
    # in der ersten Fassung „Bereich: Gemeinschaftseigentum" auf dem Blatt eines
    # Einfamilienhauses — aufgefallen erst am gerenderten PDF, nicht im Test.
    # Maßgeblich ist deshalb die Herkunft des Eigentümers.
    if (
        bezug.get("gemeinschaftseigentum")
        and not bezug.get("einheit")
        and bezug.get("eigentuemer_herkunft") == AUS_GEMEINSCHAFT
    ):
        zeilen.append(("Bereich", "Gemeinschaftseigentum"))

    if bezug.get("verwaltung"):
        zeilen.append(("Vertreten durch", bezug["verwaltung"]))
    return zeilen


def bezug_felder(bezug):
    """Dieselben Zeilen als `{"label": …, "wert": …}` — für die API.

    Das PDF arbeitet mit Tupeln (kurz und positionsbasiert), ein JSON-Schema
    braucht benannte Felder. Die Umwandlung steht hier und nicht im Endpunkt,
    damit beide Ausgaben nachweislich dieselbe Liste zeigen.
    """
    return [{"label": label, "wert": wert} for label, wert in bezug_zeilen(bezug)]
