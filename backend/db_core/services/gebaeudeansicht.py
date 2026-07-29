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

## Die Lage steht mit im Etagenfeld — und gehört an die Wohnung

In der Praxis wird nicht „EG" erfasst, sondern **„EG links"**: Das Etagenfeld
ist das einzige, in das die Lage überhaupt hineinpasst. Wer das als ganze
Etagenbezeichnung nimmt, bekommt acht Bänder mit je einer Wohnung statt vier
Etagen mit je zweien — und weil dann nichts mehr deutbar ist, auch noch in
alphabetischer Reihenfolge (EG über dem 3. OG).

Deshalb wird eine **bekannte** Lageangabe am Rand des Textes abgespalten
(`links`/`Mitte`/`rechts` samt der Kürzel `li`/`mi`/`re`): Die Etage bündelt
das Band, die Lage ordnet die Wohnungen darin von links nach rechts und steht
auf der Kachel. Abgespalten wird **nur, wenn der Rest danach eine deutbare
Etage ist** — bei „links hinten" bleibt der Text unangetastet und unten im
Ungedeutet-Band. Wir zerlegen nichts, was wir nicht verstanden haben.

Der erfasste Originaltext geht dabei nicht verloren: Er hängt als `etage_text`
an der Einheit und steht in der Auswahl. Gruppiert wird über die abgeleitete
**Höhe**, nicht mehr über den Text — „2.OG" und „2. OG" landen damit im selben
Band. Dass zwei Schreibweisen existieren, sagt das Band trotzdem (`schreibweisen`),
statt das Haus dafür zu zerreißen.
"""
import re
from collections import namedtuple
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

#: Lage innerhalb der Etage → Rang auf der Achse links → rechts. Nur
#: ausgeschriebene Wörter und die drei im Handwerk üblichen Kürzel; ein
#: einzelnes „l"/„r" wäre von einer Einheitennummer nicht zu unterscheiden.
LAGE_WORTE = (
    (("links", "lks", "li"), 0, "links"),
    (("mitte", "mittig", "mittelwohnung", "mi"), 1, "Mitte"),
    (("rechts", "rts", "re"), 2, "rechts"),
)

#: Ohne erkannte Lage hinter alle erkannten — nicht dazwischen.
LAGE_UNBEKANNT = 9

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


def etagen_ordnung(storey, *, nur_mit_wort=False):
    """Höhe eines Etagentextes als Zahl — oder `None`, wenn nicht deutbar.

    Größer heißt weiter oben. `None` heißt **„nicht geraten"** und ist ein
    ehrliches Ergebnis, kein Fehler.

    `nur_mit_wort=True` verlangt ein echtes Etagenwort und lehnt die nackte
    Zahl ab. Das ist der Modus für die *Einheitennummer*: Im Feld „Etage" heißt
    „3" das dritte Obergeschoss, in der Nummer heißt „3" die **Wohnung 3** —
    dieselbe Ziffer, zwei Bedeutungen, und die falsche kostet eine Anfahrt.
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
    if nur_mit_wort and kompakt.isdigit():
        return None
    treffer = _OG_MUSTER.match(text) or _OG_MUSTER.match(kompakt)
    if treffer:
        return float(treffer.group(1))
    return None


#: Eine erkannte Lage: `rang` sortiert (links vor Mitte vor rechts), `anzeige`
#: steht auf der Kachel.
Lage = namedtuple("Lage", "rang anzeige")


def _lage_wort(token):
    """`Lage` für ein einzelnes Wort — oder `None`."""
    for worte, rang, anzeige in LAGE_WORTE:
        if token in worte:
            return Lage(rang, anzeige)
    return None


def _wort(token):
    """Ein Wort ohne Satzzeichen und Groß/Klein — „(Links)," → „links"."""
    return token.strip(".,;:-()[]/").lower()


def lage_aus_text(text):
    """Erste erkannte Lageangabe in einem Text — `Lage` oder `None`.

    Wird auf die **Einheitennummer** angewandt („Laden links", „WE 3 re"), wenn
    die Etage selbst keine Lage trug. Gesucht wird wortweise: „Remise" ist kein
    „re".
    """
    if not text:
        return None
    for token in re.sub(r"\s+", " ", text.strip()).split():
        gefunden = _lage_wort(_wort(token))
        if gefunden:
            return gefunden
    return None


def _lage_abspalten(text):
    """`(Resttext, lage)` — eine Lageangabe am **Rand** des Textes abtrennen.

    Nur am Anfang oder Ende: In „EG links" ist „links" ein Zusatz, in einem
    Etagennamen mitten im Text wäre es Teil des Namens.
    """
    tokens = text.split()
    if len(tokens) < 2:
        return text, None
    for i in (len(tokens) - 1, 0):
        gefunden = _lage_wort(_wort(tokens[i]))
        if gefunden:
            return " ".join(tokens[:i] + tokens[i + 1 :]), gefunden
    return text, None


def etage_deuten(text, *, nur_mit_wort=False):
    """`(ordnung, label, lage)` für einen Etagen-Freitext.

    `label` ist der Text **ohne** die abgespaltene Lage („EG links" → „EG") —
    aber nur, wenn danach eine deutbare Etage übrig bleibt. Bleibt sie es
    nicht, kommt der Originaltext unangetastet zurück und `lage` ist `None`:
    Was wir nicht verstanden haben, nehmen wir auch nicht auseinander.
    """
    if not text or not text.strip():
        return None, "", None
    roh = re.sub(r"\s+", " ", text.strip())
    rest, lage = _lage_abspalten(roh)
    if lage is not None and rest.strip():
        ordnung = etagen_ordnung(rest, nur_mit_wort=nur_mit_wort)
        if ordnung is not None:
            return ordnung, rest, lage
    return etagen_ordnung(roh, nur_mit_wort=nur_mit_wort), roh, None


def _natuerlich(text):
    """Sortierschlüssel, in dem „WE 2" vor „WE 10" steht.

    Reine Textsortierung stellt „WE 10" vor „WE 2" — im Bild eines Hauses ist
    das genauso falsch wie eine vertauschte Etage, nur unauffälliger.
    """
    teile = re.split(r"(\d+)", (text or "").casefold())
    return tuple(
        (1, int(t), "") if t.isdigit() else (0, 0, t) for t in teile if t != ""
    )


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


def _einheit_verorten(u):
    """Wo hängt diese Einheit? `(ordnung, label, lage, aus_nummer)`.

    Erste Quelle ist das Etagenfeld. Ist es **leer**, darf die Einheitennummer
    aushelfen — aber nur mit einem echten Etagenwort („EG links"), nie mit einer
    nackten Zahl. Dass die Etage dann abgeleitet ist, wird ausgewiesen
    (`aus_nummer`) statt so getan, als stünde sie im dafür vorgesehenen Feld.
    """
    ordnung, label, lage = etage_deuten(u.storey)
    aus_nummer = False
    if ordnung is None and not (u.storey or "").strip():
        o2, l2, lage2 = etage_deuten(u.unit_number, nur_mit_wort=True)
        if o2 is not None:
            ordnung, label, lage, aus_nummer = o2, l2, lage2, True
    # Die Lage darf auch in der Nummer stehen („EG" + „Laden links").
    if lage is None:
        lage = lage_aus_text(u.unit_number)
    return ordnung, label, lage, aus_nummer


def _etagen_bauen(einheiten, anlagen_je_einheit, bewohner_je_einheit):
    """Einheiten eines Gebäudes zu Etagenbändern bündeln — oben nach unten.

    Gruppiert wird über die **abgeleitete Höhe**: „EG links", „EG rechts" und
    „Erdgeschoss" ergeben ein Band mit drei Wohnungen nebeneinander, nicht drei
    Bänder mit je einer. Nur was sich nicht deuten lässt, bündelt weiter über
    seinen Originaltext und bleibt unten stehen.

    Dass verschiedene Schreibweisen im Umlauf sind, verschwindet dabei nicht —
    das Band führt sie in `schreibweisen`. Sichtbar machen ja; dafür das Haus
    zerreißen nein.
    """
    baender = {}
    for u in einheiten:
        ordnung, label, lage, aus_nummer = _einheit_verorten(u)
        if ordnung is not None:
            schluessel = ("hoehe", ordnung)
        elif label:
            schluessel = ("text", label.casefold())
        else:
            schluessel = ("ohne",)
        band = baender.setdefault(
            schluessel,
            {
                "label": label or "Ohne Etagenangabe",
                "ordnung": ordnung,
                "gedeutet": ordnung is not None,
                # Wahr, sobald **eine** Einheit ihre Etage nur der Nummer
                # verdankt: Das Band steht dann auf einer Ableitung, und das
                # gehört ins Bild.
                "abgeleitet": aus_nummer,
                "schreibweisen": {},
                "einheiten": [],
            },
        )
        if label:
            band["schreibweisen"][label] = band["schreibweisen"].get(label, 0) + 1
        band["abgeleitet"] = band["abgeleitet"] or aus_nummer
        band["einheiten"].append(
            {
                "einheit": u,
                "etage_text": (u.storey or "").strip() or None,
                "lage": lage.anzeige if lage else None,
                "lage_rang": lage.rang if lage else LAGE_UNBEKANNT,
                "anlagen": anlagen_je_einheit.get(u.id, []),
                "bewohner": bewohner_je_einheit.get(u.id, []),
                "belegbar": u.unit_type not in UNIT_TYPES_OHNE_BELEGUNG,
            }
        )

    for band in baender.values():
        # Häufigste Schreibweise beschriftet das Band (bei Gleichstand die
        # zuerst erfasste); die seltene bleibt sichtbar, statt die Etage zu
        # spalten. `dict` hält die Einfügereihenfolge — `max` nimmt bei
        # Gleichstand den ersten Treffer.
        band["schreibweisen"] = list(band["schreibweisen"].items())
        if band["schreibweisen"]:
            band["label"] = max(band["schreibweisen"], key=lambda p: p[1])[0]
        band["schreibweisen"] = [s for s, _ in band["schreibweisen"]]
        # Innerhalb der Etage: links, Mitte, rechts — und danach die Nummer
        # natürlich sortiert („WE 2" vor „WE 10").
        band["einheiten"].sort(
            key=lambda e: (e["lage_rang"], _natuerlich(e["einheit"].unit_number))
        )

    # Gedeutete Etagen von oben nach unten; alles Ungedeutete darunter,
    # alphabetisch — es wird nicht dazwischengemogelt.
    def sortierung(band):
        if band["ordnung"] is None:
            return (1, 0.0, band["label"].casefold())
        return (0, -band["ordnung"], "")

    return sorted(baender.values(), key=sortierung)
