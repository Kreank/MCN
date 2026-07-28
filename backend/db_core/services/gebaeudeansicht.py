"""Gebäudeansicht — die Liegenschaft als **Haus**, nicht als Liste.

Sascha im Praxistest: *„Wenn man das quasi modular wie ein Haus aufbauen
könnte … man hat ja in den Reitern entsprechende Informationen. Etage,
bewohnt-Status, Einheitennummer. Gibt ja auch Gebäude, wo 3 oder 4 Wohnungen
auf der Etage sind."* Genau das baut dieser Service zusammen: Aus Gebäuden,
Einheiten (mit Etage), Belegung und technischen Anlagen entsteht **ein**
Datenbild, aus dem das Frontend einen Gebäudeschnitt zeichnen kann — mehrere
Häuser nebeneinander (Vorderhaus, Seitenflügel, Hinterhaus).

**Es entsteht keine neue Wahrheit.** Jede Angabe kommt aus der Tabelle, in der
sie ohnehin gepflegt wird: Einheit und Etage aus `property.unit` (0124), die
Bewohner aus `tenure.occupancy` (Reiter Belegung), die Technik aus
`property.technical_asset`. Dieser Service fügt zusammen und sortiert — er
speichert nichts und rechnet nichts hinzu.

## Die Etage ist Freitext — sortiert wird trotzdem

`unit.storey` ist bewusst Freitext („2. OG", „Hochparterre", „Souterrain"): Wer
Etagen in eine Codeliste presst, verliert das Haus mit dem Zwischengeschoss.
Für ein *Bild* eines Hauses braucht es aber eine Reihenfolge — Dach oben,
Keller unten. Der Schlüssel dafür wird hier **abgeleitet**, nicht gespeichert:

* Der angezeigte Text bleibt **wortwörtlich der eingegebene** — es wird nichts
  umgeschrieben und nichts vereinheitlicht.
* Die Ableitung entscheidet nur, in welcher Höhe das Stockwerk hängt.
* Was sich nicht deuten lässt (`ordnung = None`), landet **unten in einem
  eigenen Band** mit seinem Originaltext. Falsch einsortieren wäre schlimmer
  als sichtbar unsortiert lassen — ein Monteur, der wegen einer geratenen
  Etage im falschen Stockwerk klingelt, ist der eigentliche Schaden.
"""
import re
from datetime import date

from django.db.models import Prefetch, Q

from db_core.models import (
    Building,
    Occupancy,
    OccupancyParty,
    TechnicalAsset,
    Unit,
)

#: Einheitsarten, die keine Belegung tragen (Spiegel von `belegung.py`; die DB
#: erzwingt es über `tenure.forbid_common_area_occupancy`).
UNIT_TYPES_OHNE_BELEGUNG = ("COMMON_AREA", "TECHNICAL_ROOM")

#: Bekannte Etagenbezeichnungen → Höhe. Halbe Werte für die Zwischenlagen, die
#: es wirklich gibt (Hochparterre liegt zwischen EG und 1. OG).
ETAGEN_WORTE = (
    (("spitzboden", "spitzbogen"), 900.0),
    (("dachgeschoss", "dachgeschoß", "dg", "dach"), 800.0),
    (("staffelgeschoss", "staffelgeschoß"), 700.0),
    (("hochparterre",), 0.5),
    (("zwischengeschoss", "zwischengeschoß", "zg", "mezzanin"), 0.5),
    (("erdgeschoss", "erdgeschoß", "eg", "parterre", "hochebene"), 0.0),
    (("souterrain", "sou", "hochkeller"), -0.5),
    (("untergeschoss", "untergeschoß", "ug", "keller", "kg", "kellergeschoss"), -1.0),
    (("tiefgarage", "tg"), -2.0),
)

#: „3. OG", „3.OG", „3 Obergeschoss", „OG 3", „3. Etage", „3. Stock", nur „3".
_OG_MUSTER = re.compile(
    r"^(?:og\s*)?(\d{1,2})\s*\.?\s*"
    r"(?:og|obergeschoss|obergeschoß|etage|stock|stockwerk|geschoss|geschoß)?$"
)
#: „2. UG", „UG 2", „2. Kellergeschoss"
_UG_MUSTER = re.compile(
    r"^(?:ug\s*)?(\d{1,2})\s*\.?\s*"
    r"(?:ug|untergeschoss|untergeschoß|keller|kellergeschoss)$"
)


def etagen_ordnung(storey):
    """Höhe eines Etagentextes als Zahl — oder `None`, wenn nicht deutbar.

    Größer heißt weiter oben. `None` heißt **„nicht geraten"** und ist ein
    ehrliches Ergebnis, kein Fehler.
    """
    if not storey:
        return None
    text = storey.strip().lower().replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None

    kompakt = text.replace(".", "").replace(" ", "")
    for worte, hoehe in ETAGEN_WORTE:
        if kompakt in worte:
            return hoehe

    treffer = _UG_MUSTER.match(text) or _UG_MUSTER.match(kompakt)
    if treffer:
        return -float(treffer.group(1))
    treffer = _OG_MUSTER.match(text) or _OG_MUSTER.match(kompakt)
    if treffer:
        return float(treffer.group(1))
    return None


def _aktive_belegung(unit_ids, stichtag):
    """`{unit_id: [OccupancyParty, …]}` — die am Stichtag geltenden Bewohner.

    Eine Abfrage über alle Einheiten des Objekts; je Einheit zu fragen wäre bei
    dreißig Wohnungen ein N+1 mitten in der Ansicht, die schnell sein soll.
    """
    if not unit_ids:
        return {}
    aktiv = Q(valid_from__lte=stichtag) & (
        Q(valid_until__isnull=True) | Q(valid_until__gt=stichtag)
    )
    belegungen = (
        Occupancy.objects.filter(unit_id__in=unit_ids)
        .filter(aktiv)
        .prefetch_related(
            Prefetch(
                "parties",
                queryset=OccupancyParty.objects.select_related("party").order_by(
                    "role", "valid_from"
                ),
            )
        )
    )
    ergebnis = {}
    for occ in belegungen:
        bewohner = [
            p
            for p in occ.parties.all()
            if p.valid_from <= stichtag
            and (p.valid_until is None or p.valid_until > stichtag)
        ]
        ergebnis.setdefault(occ.unit_id, []).extend(bewohner)
    return ergebnis


def _anlagen_gruppieren(assets):
    """Anlagen nach Einheit bzw. Gebäude aufteilen.

    Drei Töpfe, weil es fachlich drei Orte gibt: an der Einheit (Etagentherme),
    am Gebäude ohne Einheit (Zentralheizung im Keller) und ganz ohne Zuordnung
    (erfasst, aber noch nicht verortet — die wird sonst niemand je wiederfinden).
    """
    je_einheit, je_gebaeude, ohne = {}, {}, []
    for a in assets:
        if a.unit_id:
            je_einheit.setdefault(a.unit_id, []).append(a)
        elif a.building_id:
            je_gebaeude.setdefault(a.building_id, []).append(a)
        else:
            ohne.append(a)
    return je_einheit, je_gebaeude, ohne


def ansicht(property_id, *, mit_belegung=True, stichtag=None):
    """Das ganze Objekt als Gebäude → Etagen → Einheiten (+ Technik, + Bewohner).

    `mit_belegung=False` (fehlendes Recht `tenure/LESEN`) liefert dieselbe
    Struktur **ohne** Bewohner. Der Aufrufer spricht das aus; hier wird nichts
    stillschweigend weggelassen.

    Rückgabe ist bewusst reines Python (dicts/Modelle), kein Schema: Die API
    formt daraus ihre Antwort, die Tests prüfen die Sortierung direkt.
    """
    stichtag = stichtag or date.today()

    gebaeude = list(
        Building.objects.filter(property_id=property_id).order_by("building_number", "id")
    )
    einheiten = list(
        Unit.objects.filter(property_id=property_id).order_by("unit_number", "id")
    )
    assets = list(
        TechnicalAsset.objects.filter(property_id=property_id, status="AKTIV").order_by(
            "asset_type", "name", "id"
        )
    )

    je_einheit, je_gebaeude, anlagen_ohne_gebaeude = _anlagen_gruppieren(assets)
    bewohner = (
        _aktive_belegung([u.id for u in einheiten], stichtag) if mit_belegung else {}
    )

    einheiten_je_gebaeude = {}
    for u in einheiten:
        einheiten_je_gebaeude.setdefault(u.building_id, []).append(u)

    haeuser = []
    for b in gebaeude:
        haeuser.append(
            {
                "gebaeude": b,
                "etagen": _etagen_bauen(
                    einheiten_je_gebaeude.get(b.id, []), je_einheit, bewohner
                ),
                # Technik ohne Einheit: die Zentralanlage im Keller. Sie gehört
                # ins Bild, sonst fehlt im Haus genau das Stück, das den
                # Unterschied zwischen Objekt- und Wohnungsproblem ausmacht.
                "technik": je_gebaeude.get(b.id, []),
            }
        )

    # Einheiten ohne Gebäude kann es nicht geben (`unit.building_id` ist NOT NULL
    # und Teil des zusammengesetzten FK) — deshalb gibt es dafür auch keinen
    # Rückgabewert. Ein Schlüssel, der immer leer ist, ist kein Sicherheitsnetz,
    # sondern eine Behauptung über einen Fall, den es nicht gibt.
    return {
        "haeuser": haeuser,
        "anlagen_ohne_gebaeude": anlagen_ohne_gebaeude,
    }


def _etagen_bauen(einheiten, anlagen_je_einheit, bewohner_je_einheit):
    """Einheiten eines Gebäudes zu Etagenbändern bündeln — oben nach unten.

    Gruppiert wird über den **Originaltext** der Etage (nach `strip`): Zwei
    Einheiten mit „2. OG" landen im selben Band, „2.OG" und „2. OG" hingegen in
    zwei — und das ist richtig so. Die Ansicht macht damit sichtbar, dass die
    Erfassung uneinheitlich ist, statt sie zu kaschieren; wer es zusammenhaben
    will, korrigiert die Etage an der Einheit (eine Stelle, eine Wahrheit).
    """
    baender = {}
    for u in einheiten:
        label = (u.storey or "").strip()
        schluessel = label or "\x00ohne"
        band = baender.setdefault(
            schluessel,
            {
                "label": label or "Ohne Etagenangabe",
                "ordnung": etagen_ordnung(label),
                "gedeutet": etagen_ordnung(label) is not None,
                "einheiten": [],
            },
        )
        band["einheiten"].append(
            {
                "einheit": u,
                "anlagen": anlagen_je_einheit.get(u.id, []),
                "bewohner": bewohner_je_einheit.get(u.id, []),
                "belegbar": u.unit_type not in UNIT_TYPES_OHNE_BELEGUNG,
            }
        )

    # Gedeutete Etagen von oben nach unten; alles Ungedeutete darunter,
    # alphabetisch — es wird nicht dazwischengemogelt.
    def sortierung(band):
        if band["ordnung"] is None:
            return (1, 0.0, band["label"].lower())
        return (0, -band["ordnung"], "")

    return sorted(baender.values(), key=sortierung)
