"""Raumaufmaß: Räume, Hüllflächen, Öffnungen — und die raumweise Heizlast.

Fachlicher Hintergrund und die DB-Invarianten stehen im Modulkopf von
``db_core/migrations/0086_raumaufmass.py``. Diese Datei ist die **einzige
Rechenstelle** für Kennzahlen und Heizlast; die API rechnet nicht.

Zwei Dinge prägen den Code:

**1. Unbestimmt ist NICHT null.**  Fehlt ein U-Wert, ein Temperaturfaktor, die
Innen-/Außentemperatur oder die Luftwechselrate, ist die Heizlast **unbekannt**
(``None``) — nie 0. Ein fehlender U-Wert als 0 gelesen hieße „diese Wand
verliert keine Wärme"; das Ergebnis wäre still falsch und der Heizkörper zu
klein. Dieselbe Haltung wie beim fehlenden Einkaufspreis
(``aufschlagsmatrix.vk_vorschlag`` → ``sale_price = None``) und beim § 35a-Ausweis
(``beleg.arbeitskosten`` → gar kein Ausweis). Der Rechner sagt zusätzlich, **welche
Fläche** ihm fehlt (``unbekannt_grund``) — sonst ist das ``None`` unbrauchbar.

Genau eine Ausnahme ist KEIN „unbekannt": eine Fläche mit ``adjacent='BEHEIZT'``
trägt **definitionsgemäß 0 W** bei (kein Temperaturgefälle zum Nachbarn). Sie
braucht weder U-Wert noch Faktor.

**2. Keine Normtabellen im Produkt.**  U-Werte, Innentemperaturen,
Temperaturkorrekturfaktoren, Luftwechselraten und Klimadaten sind **Eingaben des
Betriebs**, keine mitgelieferten Konstanten (Normrechtslage, siehe
``docs/HANDOFF.md``, Welle 2/Punkt 11). Die einzige Zahl im Code ist
``0,34 Wh/(m³·K)`` — die volumenbezogene Wärmekapazität der Luft. Das ist eine
physikalische Stoffgröße, keine DIN-Tabelle.

Gerechnet wird ausschließlich mit ``Decimal``.

**3. Die Auslegungsdaten kommen vom OBJEKT (Migration 0089).**  Die
Auslegungs-Außentemperatur (``property.property.design_outdoor_temp_c``) und der
Gebäudekennwert (``property.property.heat_load_w_per_m2``) sind Eigenschaften der
Liegenschaft, keine Frage an den Aufruf: Sie folgen aus Standort und Bauweise.
Jeder Raum-Endpunkt rechnet damit ohne Zutun des Clients. Rangfolge des
Kennwerts: ``room.heat_load_w_per_m2`` → ``property.heat_load_w_per_m2`` → sonst
``None`` (nie 0, nie erfunden). Ein Aufrufer darf die Objektwerte übersteuern
(Was-wäre-wenn); ``auslegung()`` ist die eine Stelle, an der diese Rangfolge
entschieden wird — sie wird je Anfrage **einmal** gebildet und in die
Kennzahlen gereicht (kein N+1 über die Räume).

**4. Skala und Wertebereich sind Bedienfehler, keine 500er.**  ``NUMERIK_*``
bildet die ``numeric(p, s)``-Definitionen der DB an **einer** Stelle ab. Ohne das
werden zwei harmlose Fehleingaben zum Serverfehler: ``u_value = 0,0001`` rundet
in ``numeric(5,3)`` auf ``0,000`` und verletzt den CHECK ``> 0``
(IntegrityError), ``floor_area_m2 = 99999999`` sprengt ``numeric(10,3)``
(DataError „numeric field overflow"). Beides endet hier als ``ValueError`` → 422,
und die Meldung nennt Feld und Grenze.

**5. Der Bauteilkatalog ist eine KOPIERQUELLE, kein Verweis (Migration 0090).**
``set_aufbau`` kopiert den ``u_value`` der gewählten Vorlage in die Zeile;
``template_id`` ist nur ein **Herkunftsvermerk**. Der Rechner unten liest deshalb
ausschließlich ``room_surface.u_value`` / ``room_opening.u_value`` — **nie** den
Katalog. Eine spätere Katalogkorrektur darf die Heizlast eines Objekts, das der
Betrieb dem Kunden längst vorgerechnet hat, nicht rückwirkend verschieben (genau
wie bei der Belegposition). Schickt der Client einen eigenen ``u_value`` mit,
gewinnt der: ein abweichender Messwert schlägt die Vorlage.

**6. Wer zeichnet, misst nicht doppelt (Migration 0091).**  Hat ein Raum einen
Umriss (``property.room_vertex``), rechnet **der Server** ``floor_area_m2``
(Gauß'sche Trapezformel, **Betrag**) und ``perimeter_m`` (Σ Kantenlängen) aus dem
Polygon und schreibt sie. Ein vom Client mitgeschickter Wert für diese beiden
Felder wird dann **verworfen** — dieselbe Haltung wie bei
``planned_quantity`` in ``services/site_report``: Der Service **leitet ab**,
statt dem Client zu glauben. Sonst stünden zwei Sätze Zahlen nebeneinander, die
auseinanderlaufen. Ohne Umriss bleibt alles Handeingabe wie bisher.

**Schreibweg.**  ``property.room.volume_m3`` und ``property.room_opening.area_m2``
sind GENERATED-Spalten. Djangos ``objects.create()`` nimmt jedes konkrete Feld in
das INSERT auf und läuft deshalb in „cannot insert a non-DEFAULT value into
column" — die Inserts laufen hier über **explizite Spaltenlisten** per Cursor
(genau das meint der Model-Docstring). Gelesen und aktualisiert wird über das ORM;
``QuerySet.update()`` schreibt nur die genannten Spalten und ist damit unkritisch.
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import NamedTuple

from django.db import DataError, IntegrityError, connection

from db_core.db_context import business_transaction
from db_core.models import (
    Building,
    ComponentTemplate,
    Property,
    Room,
    RoomOpening,
    RoomSurface,
    RoomVertex,
    Unit,
)
from db_core.services._validation import ensure_exists, ensure_standort

# --- Codelisten (Migration 0086) -------------------------------------------

ROOM_TYPES = (
    "WOHNEN", "SCHLAFEN", "KUECHE", "BAD", "WC", "FLUR", "TREPPENHAUS",
    "KELLER", "DACHBODEN", "TECHNIK", "BUERO", "LAGER", "GEWERBE", "SONSTIGES",
)
ROOM_STATUS = ("AKTIV", "INAKTIV")
SURFACE_TYPES = ("AUSSENWAND", "INNENWAND", "DACHSCHRAEGE", "DECKE", "BODEN")
# Ein Polygon ist die DRAUFSICHT: Seine Kanten sind die **senkrechten** Bauteile
# (Migration 0094, CHECK `room_surface_kante_nur_an_der_wand`). Decke und Boden
# liegen über bzw. unter der Fläche, die Dachschräge steht schief darüber — für sie
# alle ist die Grundfläche die Bezugsgröße, nicht eine Kantenlänge. Ohne diese
# Grenze bekäme eine Decke die Fläche „Kantenlänge × Raumhöhe" und wüchse fortan
# mit der Raumhöhe mit.
KANTEN_BAUTEILE = ("AUSSENWAND", "INNENWAND")
ADJACENT = ("AUSSENLUFT", "ERDREICH", "UNBEHEIZT", "BEHEIZT")
ORIENTATIONS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")
OPENING_TYPES = ("FENSTER", "DACHFENSTER", "TUER_AUSSEN", "TUER_INNEN", "SONSTIGES")

# Volumenbezogene Wärmekapazität der Luft. Physikalische Stoffgröße, KEINE
# Normtabelle — deshalb darf sie hier stehen.
C_LUFT_WH_PRO_M3K = Decimal("0.34")

# Felder, die ein Client an einem Raum setzen darf (property_id gehört NICHT
# dazu: ein Raum wandert nicht in eine andere Liegenschaft).
ROOM_FIELDS = (
    "name", "storey", "room_type", "building_id", "unit_id",
    "floor_area_m2", "length_m", "width_m", "room_height_m", "perimeter_m",
    "indoor_temp_c", "air_change_rate", "heat_load_w_per_m2",
    "riser_distance_m", "status", "note",
)
ROOM_PFLICHT = ("name", "floor_area_m2", "room_height_m")
# Felder, die der Server aus dem Umriss ABLEITET, sobald es einen gibt (0091).
# Ein Client-Wert dafür wird dann verworfen — nicht etwa gegen den gezeichneten
# gestellt.
GEZEICHNETE_FELDER = ("floor_area_m2", "perimeter_m")

# Auslegungsdaten an der Liegenschaft (Migration 0089).
PROPERTY_AUSLEGUNG_FIELDS = ("design_outdoor_temp_c", "heat_load_w_per_m2")

_W = Decimal("0.1")       # Heizlast in Watt, eine Nachkommastelle
_M2 = Decimal("0.001")    # Flächen/Volumen/Längen wie in der DB

# --- Skala und Wertebereich: die numeric(p, s) der Datenbank ---------------
#
# EINE Stelle für alle Dezimalfelder des Slices (Migrationen 0086/0089), sonst
# stehen die Grenzen verstreut im Code und laufen auseinander.
#   (max_digits, decimal_places, muss_positiv)
# `muss_positiv` spiegelt ein DB-CHECK `> 0`: Ein Wert unterhalb der halben
# letzten Stelle rundete dort auf 0 und schlüge als IntegrityError durch (500).
NUMERIK_RAUM = {
    "floor_area_m2":      (10, 3, True),
    "length_m":           (8, 3, True),
    "width_m":            (8, 3, True),
    "room_height_m":      (8, 3, True),
    "perimeter_m":        (10, 3, True),
    "indoor_temp_c":      (4, 1, False),
    "air_change_rate":    (4, 2, False),
    "heat_load_w_per_m2": (6, 1, True),
    "riser_distance_m":   (8, 2, False),
}
NUMERIK_FLAECHE = {
    "gross_area_m2": (10, 3, True),
    "u_value":       (5, 3, True),
    "temp_factor":   (4, 2, False),
}
NUMERIK_OEFFNUNG = {
    "width_m":    (6, 3, True),
    "height_m":   (6, 3, True),
    "u_value":    (5, 3, True),
    # Lage in der Kante (0091). `muss_positiv` ist FALSCH: 0,0 m ist ein gültiger
    # Wert (die Öffnung sitzt am Anfangspunkt der Kante).
    "position_m": (6, 3, False),
}
NUMERIK_LIEGENSCHAFT = {
    "design_outdoor_temp_c": (4, 1, False),
    "heat_load_w_per_m2":    (6, 1, True),
}
# Wertebereich der Auslegungsdaten — Spiegel der DB-CHECKs aus Migration 0089:
#   design_outdoor_temp_c BETWEEN -40 AND 30      heat_load_w_per_m2 > 0
# `NUMERIK_LIEGENSCHAFT` deckt nur Skala und Spaltenbreite ab; diese beiden
# Grenzen sind fachlich. Sie stehen HIER genau einmal und werden von JEDEM Pfad
# benutzt — auch vom Was-wäre-wenn (Query-Parameter). Ein Kennwert 0 wäre sonst
# kein „unbekannt", sondern die Aussage „0 kW Heizlast".
AUSSENTEMP_MIN = Decimal("-40")
AUSSENTEMP_MAX = Decimal("30")
# GENERATED: property.room_opening.area_m2 = round(quantity * width * height, 3).
# Die Faktoren passen je einzeln in ihre Spalte, ihr PRODUKT kann trotzdem
# überlaufen — deshalb wird auch das Ergebnis geprüft.
NUMERIK_OEFFNUNG_FLAECHE = (12, 3, True)
# integer in der DB (CHECK > 0).
MAX_QUANTITY = 2_147_483_647
# property.room_vertex.x_mm/y_mm sind `integer` (Millimeter, siehe 0091).
MAX_KOORDINATE_MM = 2_147_483_647
MIN_KOORDINATE_MM = -2_147_483_648


# Deutsche Klarnamen für die Fehlermeldungen. Eine 422-Meldung geht an einen
# Monteur auf der Baustelle, nicht an einen Entwickler: „floor_area_m2" sagt ihm
# nichts, „Grundfläche" sagt ihm alles. Die Spaltennamen bleiben im Code, die
# Meldung spricht Deutsch.
FELD_LABEL = {
    "floor_area_m2":         "Grundfläche",
    "length_m":              "Länge",
    "width_m":               "Breite",
    "height_m":              "Höhe",
    "room_height_m":         "Raumhöhe",
    "perimeter_m":           "Umfang",
    "indoor_temp_c":         "Innentemperatur",
    "air_change_rate":       "Luftwechselrate",
    "heat_load_w_per_m2":    "Kennwert (W/m²)",
    "riser_distance_m":      "Abstand zur Steigleitung",
    "gross_area_m2":         "Bruttofläche",
    "u_value":               "U-Wert",
    "temp_factor":           "Temperaturfaktor",
    "design_outdoor_temp_c": "Auslegungs-Außentemperatur",
    "quantity":              "Anzahl",
    "area_m2":               "Öffnungsfläche",
    "position_m":            "Lage in der Wand",
}


# --- Hilfen ----------------------------------------------------------------

def _lesbar(feld):
    """Ersetzt Spaltennamen durch deutsche Klarnamen (auch innerhalb eines Präfixes).

    Die Feldbezeichner werden an den Aufrufstellen als Text zusammengesetzt
    (z. B. „Hüllfläche 'w1': gross_area_m2"). Statt jede dieser Stellen anzufassen,
    übersetzt genau eine Funktion die enthaltenen Spaltennamen — von den längsten
    zuerst, sonst verschluckte `width_m` das `room_height_m` nicht, wohl aber
    `u_value` das `u_value` in einem längeren Namen.
    """
    for spalte in sorted(FELD_LABEL, key=len, reverse=True):
        feld = feld.replace(spalte, FELD_LABEL[spalte])
    return feld


def _zahl_de(wert):
    """Formatiert eine Dezimalzahl deutsch (Tausenderpunkt, Dezimalkomma).

    Die Meldung geht an einen deutschsprachigen Bediener; „9999999.999" liest
    sich dort falsch. Python formatiert englisch, also werden Gruppen- und
    Dezimaltrenner getrennt zusammengesetzt — NICHT über ein wechselseitiges
    `replace`, das sich selbst überschreibt.
    """
    ganz, _, bruch = f"{wert:,f}".partition(".")
    ganz = ganz.replace(",", ".")      # Tausendertrenner: englisch , -> deutsch .
    bruch = bruch.rstrip("0")
    return f"{ganz},{bruch}" if bruch else ganz


def _dec(wert, feld):
    if wert is None:
        return None
    if isinstance(wert, Decimal):
        return wert
    try:
        return Decimal(str(wert))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{_lesbar(feld)}: '{wert}' ist keine Zahl.") from exc


def _q(wert, schritt):
    return None if wert is None else wert.quantize(schritt, rounding=ROUND_HALF_UP)


def _numerik(wert, feld, spec):
    """Prüft Skala und Wertebereich gegen `numeric(p, s)` und rundet auf die Skala.

    Gerundet wird, weil die DB es ohnehin täte — nur ist die Rundung hier
    sichtbar und ihre beiden Klippen sind benannt:
      * zu fein  (rundet auf 0, obwohl die Spalte `> 0` verlangt) → ValueError,
      * zu groß  (numeric field overflow)                          → ValueError.
    Beides ist ein **Bedienfehler** → 422, kein 500.
    """
    if wert is None:
        return None
    stellen, nachkomma, muss_positiv = spec
    schritt = Decimal(1).scaleb(-nachkomma)
    grenze = Decimal(10) ** (stellen - nachkomma) - schritt  # größter darstellbarer Betrag

    if not wert.is_finite():
        raise ValueError(f"{_lesbar(feld)}: '{wert}' ist keine endliche Zahl.")
    try:
        gerundet = wert.quantize(schritt, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        gerundet = None  # Betrag jenseits der Rechenpräzision — dieselbe Grenze
    if gerundet is None or abs(gerundet) > grenze:
        raise ValueError(
            f"{_lesbar(feld)}: Der Wert liegt außerhalb des zulässigen Bereichs "
            f"(Betrag höchstens {_zahl_de(grenze)}, {nachkomma} Nachkommastellen)."
        )
    if muss_positiv and gerundet == 0 and wert > 0:
        raise ValueError(
            f"{_lesbar(feld)}: Der Wert ist zu klein — es werden {nachkomma} "
            f"Nachkommastellen gespeichert, der kleinste zulässige Wert ist "
            f"{_zahl_de(schritt)}."
        )
    return gerundet


def _numerik_felder(werte, spec, prefix=""):
    """Wendet `_numerik` auf alle in `werte` vorhandenen Felder von `spec` an."""
    for feld, s in spec.items():
        if feld in werte:
            werte[feld] = _numerik(_dec(werte[feld], f"{prefix}{feld}"),
                                   f"{prefix}{feld}", s)
    return werte


def _constraint(exc):
    """Constraint-Name eines DB-Fehlers (psycopg-Diagnose), sonst None.

    Der Trigger `property.enforce_room_opening_fits` setzt den Namen über
    `USING CONSTRAINT = …`; im String der Exception steht er nicht.
    """
    cause = getattr(exc, "__cause__", None)
    return getattr(getattr(cause, "diag", None), "constraint_name", None)


def _db_fehler(exc):
    """DataError / unerwarteter IntegrityError → Bedienfehler (422), nie 500.

    Die Vorabprüfung (`_numerik`, Codelisten, Zuordnung) fängt die bekannten
    Fälle. Was trotzdem an einem CHECK oder an der Spaltenbreite scheitert, ist
    immer noch eine Eingabe des Bedieners — sie darf nicht als Serverfehler
    enden. Die DB-Meldung wird mitgegeben, damit der Fehler benennbar bleibt.
    """
    meldung = str(getattr(exc, "__cause__", exc)).splitlines()[0].strip()
    return ValueError(f"Die Datenbank hat die Eingabe abgewiesen: {meldung}")


def _insert(table, werte):
    """INSERT mit **expliziter** Spaltenliste (GENERATED-Spalten bleiben außen vor)."""
    spalten = list(werte)
    platzhalter = ", ".join(["%s"] * len(spalten))
    liste = ", ".join(f'"{s}"' for s in spalten)
    with connection.cursor() as cur:
        cur.execute(
            f'INSERT INTO {table} ({liste}) VALUES ({platzhalter})',
            [werte[s] for s in spalten],
        )


# --- Grundriss: Polygonrechnung (Migration 0091) ----------------------------
#
# Die Punkte sind ganzzahlige Millimeter — die ganze Rechnung läuft deshalb in
# **exakter Ganzzahlarithmetik** und wird erst am Schluss in Meter überführt. Das
# ist kein Detail: Fläche und Umfang gehen als Menge in ein Angebot, und ein
# Gleitkommafehler an dieser Stelle wäre nirgends mehr sichtbar.

_MM_PRO_M = Decimal(1000)
_MM2_PRO_M2 = Decimal(1_000_000)


def _kante(punkte, i):
    """Kante i = (Punkt i → Punkt i+1), zyklisch (Modulkopf 0091)."""
    return punkte[i], punkte[(i + 1) % len(punkte)]


def _polygon_flaeche_m2(punkte):
    """Gauß'sche Trapezformel — als **Betrag**.

    Der Umlaufsinn (im/gegen den Uhrzeigersinn) ist eine Zeichenkonvention, keine
    Aussage über die Fläche: Ein gegen den Uhrzeigersinn gezeichneter Raum hat
    dieselbe Fläche wie derselbe Raum andersherum. Ohne `abs` käme hier eine
    negative Fläche heraus — und die verletzte den CHECK `floor_area_m2 > 0`
    (500 statt Ergebnis).
    """
    doppelt = 0
    for i in range(len(punkte)):
        (x1, y1), (x2, y2) = _kante(punkte, i)
        doppelt += x1 * y2 - x2 * y1
    # mm² → m². Erst hier wird aus Ganzzahl eine Dezimalzahl.
    return _q(Decimal(abs(doppelt)) / 2 / _MM2_PRO_M2, _M2)


def _kantenlaengen_m(punkte):
    """Länge jeder Kante in Metern (auf die Skala der DB gerundet).

    Gerundet wird **je Kante**, und der Umfang ist die Summe dieser gerundeten
    Längen: Die ausgewiesene Summe muss die Summe der ausgewiesenen Teile sein
    (dieselbe Regel wie bei der Heizlast — sonst findet, wer nachrechnet, eine
    Differenz, die es nicht gibt).
    """
    laengen = []
    for i in range(len(punkte)):
        (x1, y1), (x2, y2) = _kante(punkte, i)
        dx, dy = x2 - x1, y2 - y1
        # Ganzzahlige Quadratsumme → Decimal.sqrt (exakt für ganze Vielfache).
        laengen.append(_q(Decimal(dx * dx + dy * dy).sqrt() / _MM_PRO_M, _M2))
    return laengen


def _orientierung(a, b, c):
    """Kreuzprodukt (b−a) × (c−a): >0 links, <0 rechts, 0 kollinear."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _liegt_dazwischen(a, b, p):
    """p ist kollinear zu (a, b) — liegt es auch im Streckenabschnitt?"""
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _strecken_schneiden(a, b, c, d):
    """Standard-Segmentschnitt (echte Kreuzung ODER Berührung/Überlappung)."""
    o1 = _orientierung(a, b, c)
    o2 = _orientierung(a, b, d)
    o3 = _orientierung(c, d, a)
    o4 = _orientierung(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True  # echte Kreuzung
    # Kollineare Berührung: ein Endpunkt liegt auf der anderen Strecke.
    if o1 == 0 and _liegt_dazwischen(a, b, c):
        return True
    if o2 == 0 and _liegt_dazwischen(a, b, d):
        return True
    if o3 == 0 and _liegt_dazwischen(c, d, a):
        return True
    if o4 == 0 and _liegt_dazwischen(c, d, b):
        return True
    return False


def _selbstschnitt(punkte):
    """Überschlägt sich der Umriss? → (i, j) der schneidenden Kanten, sonst None.

    Für ein überschlagenes Polygon liefert die Trapezformel eine **sinnlose**
    Fläche (Teilflächen heben sich gegenseitig auf) — sie muss deshalb vor der
    Rechnung abgefangen werden, nicht danach.

    **Benachbarte Kanten teilen sich per Definition einen Punkt** und werden
    übersprungen; ihr entarteter Fall (zurückknickende, überlappende Kanten) hat
    seine eigene Prüfung, siehe `_knickt_zurueck`.
    """
    n = len(punkte)
    for i in range(n):
        a, b = _kante(punkte, i)
        for j in range(i + 1, n):
            if j == (i + 1) % n or i == (j + 1) % n:
                continue  # benachbart
            c, d = _kante(punkte, j)
            if _strecken_schneiden(a, b, c, d):
                return i, j
    return None


def _knickt_zurueck(punkte):
    """Zwei benachbarte Kanten liegen aufeinander (Dorn) → Index, sonst None.

    (0,0) → (10,0) → (4,0) → … : die zweite Kante läuft auf der ersten zurück.
    Der Selbstschnitt-Test sieht das nicht (die beiden sind benachbart), und die
    Fläche kann trotzdem positiv bleiben — der Umriss ist aber entartet.
    """
    n = len(punkte)
    for i in range(n):
        a, b = _kante(punkte, i)
        c = punkte[(i + 2) % n]
        d1 = (b[0] - a[0], b[1] - a[1])
        d2 = (c[0] - b[0], c[1] - b[1])
        kreuz = d1[0] * d2[1] - d1[1] * d2[0]
        skalar = d1[0] * d2[0] + d1[1] * d2[1]
        if kreuz == 0 and skalar < 0:
            return i
    return None


def _pruefe_umriss(vertices):
    """Validiert die Punktliste → Liste von (x_mm, y_mm) oder [] (Umriss entfernen).

    Alle Verstöße sind **Bedienfehler** (422 mit benennender Meldung), keine 500er:
    zu wenige Punkte, Dublettenpunkte (die DB hat dafür einen UNIQUE), entartete
    (kollineare) und überschlagene Polygone.
    """
    roh = list(vertices or [])
    if not roh:
        return []

    punkte = []
    for i, v in enumerate(roh):
        for feld in ("x_mm", "y_mm"):
            if v.get(feld) is None:
                raise ValueError(f"Punkt {i + 1}: {feld} fehlt.")
        try:
            x, y = int(v["x_mm"]), int(v["y_mm"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Punkt {i + 1}: die Koordinaten sind ganze Millimeter."
            ) from exc
        for name, wert in (("x_mm", x), ("y_mm", y)):
            if not (MIN_KOORDINATE_MM <= wert <= MAX_KOORDINATE_MM):
                raise ValueError(
                    f"Punkt {i + 1}: {name} liegt außerhalb des zulässigen Bereichs "
                    f"({MIN_KOORDINATE_MM} bis {MAX_KOORDINATE_MM} mm)."
                )
        punkte.append((x, y))

    if len(punkte) < 3:
        raise ValueError(
            "Ein Umriss braucht mindestens 3 Punkte — mit zwei Punkten wird keine "
            "Fläche umschlossen."
        )

    gesehen = {}
    for i, p in enumerate(punkte):
        if p in gesehen:
            raise ValueError(
                f"Punkt {i + 1} liegt auf Punkt {gesehen[p] + 1} "
                f"({p[0]} / {p[1]} mm) — zwei Punkte des Umrisses dürfen nicht "
                "aufeinanderliegen."
            )
        gesehen[p] = i

    # Reihenfolge der Prüfungen ist bewusst: Erst die entartete Fläche (dann liest
    # sich das kollineare Dreieck als „keine Fläche", nicht als „Dorn"), dann der
    # Überschlag, dann der Dorn.
    if _polygon_flaeche_m2(punkte) <= 0:
        raise ValueError(
            "Der Umriss umschließt keine Fläche — die Punkte liegen auf einer "
            "Linie (oder die Fläche ist kleiner als 0,001 m²)."
        )
    schnitt = _selbstschnitt(punkte)
    if schnitt is not None:
        i, j = schnitt
        raise ValueError(
            f"Der Umriss überschlägt sich: Kante {i} (Punkt {i + 1} → "
            f"{(i + 1) % len(punkte) + 1}) und Kante {j} (Punkt {j + 1} → "
            f"{(j + 1) % len(punkte) + 1}) schneiden sich."
        )
    dorn = _knickt_zurueck(punkte)
    if dorn is not None:
        raise ValueError(
            f"Der Umriss knickt bei Punkt {(dorn + 1) % len(punkte) + 1} in sich "
            "zurück — die beiden Kanten liegen aufeinander."
        )
    return punkte


def _punkte(room):
    """Umriss eines Raumes als Liste von (x_mm, y_mm), in Umlaufreihenfolge."""
    return [(v.x_mm, v.y_mm) for v in room.vertices.all()]


def _sperre_raum(room_id):
    """Sperrt die Raumzeile (`FOR UPDATE`) und liefert die **aktuelle** Raumhöhe.

    Der Serialisierungspunkt des ganzen Aufmaßes ist die **Raumzeile** — dieselbe,
    die `property.enforce_room_opening_fits` seit 0089 nimmt. Bisher entstand die
    Sperre hier nur als **Nebenwirkung** dieses Triggers, und der feuert nicht
    immer: Ein `set_aufbau`, das ausschließlich Flächen schreibt (keine Öffnungen,
    kein `UPDATE OF gross_area_m2`), fasste die Raumzeile nie an.

    Die Lücke, die dadurch offenstand (vom Review durchgespielt):

        T2 (set_aufbau)   liest den 5-Punkt-Umriss, validiert edge_index = 4
        T1 (set_grundriss) verkleinert auf 3 Punkte, committet
                           (sein UPDATE ... WHERE edge_index >= 3 sieht T2s
                            ungeschriebene Zeile nicht)
        T2                 fügt die Wand mit edge_index = 4 ein

    Ergebnis: eine Wand auf einer Kante, die es nicht gibt — `area_is_derived =
    true`, aber `_rechne_abgeleitete_flaechen` **überspringt sie still**
    (`continue`). Ihre Fläche erstarrt und folgt der Raumhöhe nie wieder. Kein
    CHECK ist verletzt, also **meldet es niemand**. Genau die Fehlerklasse, gegen
    die dieser Slice gebaut ist.

    Deshalb nehmen `set_aufbau`, `set_grundriss` und das Höhen-Nachrechnen in
    `update_room` die Sperre **ausdrücklich und als Erstes** — und lesen Umriss und
    Raumhöhe erst **danach**. Ein Lesen vor der Sperre wäre wertlos: Es ist genau
    der veraltete Stand, um den es geht.

    Immer dieselbe Zeile, immer als Erstes: einheitliche Sperrreihenfolge, kein
    Deadlock-Risiko gegen den Trigger.
    """
    with connection.cursor() as cur:
        cur.execute(
            'SELECT room_height_m FROM property."room" WHERE id = %s FOR UPDATE',
            [room_id],
        )
        zeile = cur.fetchone()
    if zeile is None:
        raise ValueError(f"Raum {room_id} existiert nicht")
    return zeile[0]


def _rechne_abgeleitete_flaechen(room_id, room_height_m, laengen):
    """Rechnet JEDE abgeleitete Wandfläche des Raumes neu: Kantenlänge × Raumhöhe.

    Das ist die Einlösung von `area_is_derived` (0093). Eine abgeleitete Fläche ist
    **kein Datum, sondern ein Ergebnis** — sie muss jeder Änderung ihrer beiden
    Eingangsgrößen (Umriss, Raumhöhe) folgen. Täte sie das nicht, erstarrte sie
    still auf dem alten Stand, und die Heizlast wäre falsch, ohne dass irgendwo
    etwas ungewöhnlich aussähe.

    Handeingaben (`area_is_derived = false`) bleiben **unangetastet** — Giebel,
    Erker und Dachschräge sind keine Rechtecke, und wer dort eine Fläche einträgt,
    will sie behalten.

    Läuft ausschließlich **innerhalb** einer `business_transaction` (der Aufrufer
    hält sie), und der Aufrufer fängt den Trigger `room_opening_passt_in_flaeche`:
    Eine schrumpfende Wand kann ihre Fenster verlieren.
    """
    for s in RoomSurface.objects.filter(room_id=room_id, area_is_derived=True):
        # Kann nach dem Auflösen verwaister Kanten nicht mehr vorkommen — aber der
        # Zugriff auf laengen[i] darf unter keinen Umständen ins Leere greifen.
        if s.edge_index is None or s.edge_index >= len(laengen):
            continue
        neu = _numerik(
            _q(laengen[s.edge_index] * room_height_m, _M2),
            "gross_area_m2", NUMERIK_FLAECHE["gross_area_m2"],
        )
        if neu != s.gross_area_m2:
            RoomSurface.objects.filter(id=s.id).update(gross_area_m2=neu)


def _flaeche_zu_klein(exc):
    """Der Trigger hat eine neu gerechnete Wand unter ihre Fenster schrumpfen sehen.

    Er benennt Wand und Quadratmeter bereits klar und deutsch (0086/0089) — hier
    kommt dazu, WARUM sie gerade kleiner geworden ist. Das ist ein Bedienfehler
    (422), kein Serverfehler.
    """
    meldung = str(getattr(exc, "__cause__", exc)).splitlines()[0].strip()
    return ValueError(
        f"{meldung} Die Wandfläche wurde aus Umriss und Raumhöhe NEU gerechnet und "
        "ist dadurch kleiner geworden — die Öffnungen passen nicht mehr hinein. "
        "Entweder die Öffnungen anpassen oder die Wandfläche von Hand eintragen "
        "(dann wird sie nicht mehr nachgerechnet)."
    )


def kantenlaengen(room):
    """Kantenlängen des Umrisses in Metern (leer, wenn der Raum nicht gezeichnet ist).

    Öffentlich, weil die API sie ausweist (`SurfaceOut.edge_length_m`) — auch das
    ist eine **abgeleitete** Zahl und kommt deshalb aus dieser Rechenstelle, nicht
    aus der View.
    """
    punkte = _punkte(room)
    return _kantenlaengen_m(punkte) if punkte else []


# --- Lesen -----------------------------------------------------------------

def _basis_qs():
    # `vertices` mit vorladen: sonst zieht `kennzahlen()` (geometrie_quelle) und
    # die Kantenlänge in der Ausgabe je Raum eine eigene Query (N+1 in der Liste).
    return Room.objects.prefetch_related("surfaces", "openings", "vertices")


def list_rooms(property_id, mit_inaktiven=False):
    """Räume einer Liegenschaft inkl. Hüllflächen, Öffnungen und Umriss.

    **Standard: nur AKTIVE.** Ein umgebauter oder weggefallener Raum wird nie
    gelöscht (No-Delete), sondern auf INAKTIV gesetzt — er soll die Arbeitsliste
    aber nicht mehr belasten. `mit_inaktiven=True` holt ihn zurück ins Bild.
    """
    qs = _basis_qs().filter(property_id=property_id)
    if not mit_inaktiven:
        qs = qs.filter(status="AKTIV")
    return list(qs.order_by("storey", "name", "id"))


def get_room(room_id):
    """Ein Raum inkl. Hüllflächen, Öffnungen und Umriss, sonst None.

    Kein Statusfilter: Ein stillgelegter Raum bleibt **einzeln** abrufbar (sonst
    wäre er nicht mehr reaktivierbar und sein Aufmaß nicht mehr einsehbar).
    """
    return _basis_qs().filter(id=room_id).first()


# --- Validierung -----------------------------------------------------------

def _pruefe_zuordnung(property_id, building_id, unit_id):
    """Gebäude/Einheit müssen zur Liegenschaft passen (sonst FK-Fehler → 500).

    Die Regel liegt in `_validation.ensure_standort` — sie gilt gleichlautend für
    die technische Anlage (0004) und den Raum (0086). Hier bleibt nur der
    hausinterne Name stehen.

    Gibt `(building_id, unit_id)` durch: Kam nur die Einheit, ist das Gebäude
    darin abgeleitet (Befund I11).
    """
    return ensure_standort(property_id, building_id, unit_id)


def _pruefe_raum(daten):
    """Vorabvalidierung eines (Teil-)Raum-Payloads → normalisierte Werte.

    Nur die im Payload **vorhandenen** Schlüssel werden geprüft (PATCH-Semantik);
    ROOM_PFLICHT prüft der Aufrufer beim Anlegen.
    """
    unbekannt = set(daten) - set(ROOM_FIELDS)
    if unbekannt:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")

    werte = dict(daten)

    if "name" in werte:
        name = (werte["name"] or "").strip()
        if not name:
            raise ValueError("name darf nicht leer sein.")
        werte["name"] = name
    if "storey" in werte and werte["storey"] is not None:
        storey = werte["storey"].strip()
        # Die DB verbietet den leeren String; ein leeres Feld aus dem Formular
        # ist fachlich „kein Geschoss" → NULL.
        werte["storey"] = storey or None
    if werte.get("room_type") is not None and werte["room_type"] not in ROOM_TYPES:
        raise ValueError(
            f"Ungültiger room_type '{werte['room_type']}'. "
            f"Erlaubt: {', '.join(ROOM_TYPES)}."
        )
    if werte.get("status") is not None and werte["status"] not in ROOM_STATUS:
        raise ValueError(
            f"Ungültiger status '{werte['status']}'. Erlaubt: {', '.join(ROOM_STATUS)}."
        )

    # Skala/Wertebereich zuerst: sonst schlägt eine zu feine oder zu große Zahl
    # erst in der DB auf (IntegrityError/DataError → 500).
    _numerik_felder(werte, NUMERIK_RAUM)

    for feld in ("floor_area_m2", "length_m", "width_m", "room_height_m",
                 "perimeter_m", "heat_load_w_per_m2"):
        wert = werte.get(feld)
        if wert is not None and wert <= 0:
            raise ValueError(f"{feld} muss größer als 0 sein.")
    if werte.get("room_height_m") is not None and werte["room_height_m"] > 20:
        raise ValueError("room_height_m darf höchstens 20 m betragen.")
    if werte.get("air_change_rate") is not None and werte["air_change_rate"] < 0:
        raise ValueError("air_change_rate darf nicht negativ sein.")
    if werte.get("riser_distance_m") is not None and werte["riser_distance_m"] < 0:
        raise ValueError("riser_distance_m darf nicht negativ sein.")
    temp = werte.get("indoor_temp_c")
    if temp is not None and not (Decimal("-30") <= temp <= Decimal("60")):
        raise ValueError("indoor_temp_c muss zwischen -30 und 60 °C liegen.")

    return werte


# --- Schreiben: Raum -------------------------------------------------------

def _dublette_text(unit_id, storey, name):
    """Nennt den Bereich, in dem der Name schon vergeben ist — den echten.

    `room_dublette` ist UNIQUE **NULLS NOT DISTINCT** über
    `(property_id, unit_id, storey, name)` — das **Gebäude steht nicht darin**.
    Ein Raum ohne Einheit und ohne Etage teilt sich seinen Namensraum deshalb
    mit allen anderen Räumen ohne Einheit und ohne Etage DERSELBEN
    LIEGENSCHAFT, gebäudeübergreifend.

    Die alte Meldung sprach pauschal von „dieser Einheit/diesem Geschoss". Wer
    im Hinterhaus einen zweiten „Heizungskeller" anlegte, las also einen Grund,
    den er im Baum nicht wiederfand — der kollidierende Raum steht im
    Vorderhaus. Vorder- und Hinterhaus sind im Handwerk kein Randfall.
    """
    if unit_id is None and storey is None:
        return (
            f"An dieser Liegenschaft existiert bereits ein Raum ohne Einheit "
            f"mit dem Namen '{name}'. Räume ohne Einheit und ohne Etage teilen "
            f"sich den Namen über alle Gebäude hinweg — hängen Sie das Gebäude "
            f"an ('{name} Vorderhaus')."
        )
    if unit_id is None:
        # Auch hier fehlt das Gebäude im Schlüssel: Zwei Häuser mit je einem
        # „Treppenhaus" im EG kollidieren. „In dieser Etage" allein läse sich
        # als „im EG DIESES Hauses" — und im anderen Haus sucht dann niemand.
        return (
            f"Auf der Etage '{storey}' existiert an dieser Liegenschaft bereits "
            f"ein Raum ohne Einheit mit dem Namen '{name}'. Ohne Einheit gilt "
            f"der Name gebäudeübergreifend — hängen Sie das Gebäude an "
            f"('{name} Vorderhaus')."
        )
    if storey is not None:
        # Die Etage steht MIT im Schlüssel, grenzt hier also wirklich ein: In
        # einer Maisonette sind „Bad" im EG und „Bad" im OG erlaubt. Das zu
        # verschweigen behauptete eine engere Grenze als die echte.
        return (
            f"In dieser Einheit existiert auf der Etage '{storey}' bereits ein "
            f"Raum mit dem Namen '{name}'."
        )
    # „ohne Etagenangabe" ist keine Wortklauberei: Ein „Bad" auf 'OG' darf in
    # derselben Einheit zulässig daneben stehen — es kollidiert nur der Raum,
    # der wie dieser gar keine Etage trägt.
    return (
        f"In dieser Einheit existiert bereits ein Raum ohne Etagenangabe mit "
        f"dem Namen '{name}'."
    )


def create_room(actor_app_user_id, property_id, daten):
    """Legt einen Raum an einer Liegenschaft an."""
    ensure_exists(Property, property_id, "Liegenschaft")
    werte = _pruefe_raum(daten or {})
    fehlend = [f for f in ROOM_PFLICHT if werte.get(f) is None]
    if fehlend:
        raise ValueError(f"Pflichtfelder fehlen: {', '.join(fehlend)}.")
    # Rückgabewert übernehmen: Kam nur die Einheit, ist ihr Gebäude jetzt
    # abgeleitet (Befund I11) — die DB verlangt es im zusammengesetzten FK.
    b, u = _pruefe_zuordnung(
        property_id, werte.get("building_id"), werte.get("unit_id")
    )
    if u is not None:
        werte["building_id"] = b

    zeile = {"id": uuid.uuid4(), "property_id": property_id}
    for feld in ROOM_FIELDS:
        if feld in werte:
            zeile[feld] = werte[feld]
    # status ist NOT NULL; ein explizit gesendetes null ist kein „lösche das Feld".
    if zeile.get("status") is None:
        zeile["status"] = "AKTIV"

    try:
        with business_transaction(actor_app_user_id):
            _insert('property."room"', zeile)
    except IntegrityError as exc:
        if _constraint(exc) == "room_dublette":
            raise ValueError(_dublette_text(zeile.get("unit_id"), zeile.get("storey"), zeile["name"])) from exc
        raise _db_fehler(exc) from exc
    except DataError as exc:
        raise _db_fehler(exc) from exc
    return get_room(zeile["id"])


def update_room(actor_app_user_id, room_id, daten):
    """Teil-Update (PATCH) eines Raumes. Nur übergebene Felder werden gesetzt.

    **Wer zeichnet, misst nicht doppelt (0091):** Hat der Raum einen Umriss, sind
    `floor_area_m2` und `perimeter_m` aus dem Polygon abgeleitet — ein vom Client
    mitgeschickter Wert wird hier **verworfen**, nicht geschrieben. Sonst stünde
    neben der gezeichneten Fläche eine zweite, getippte, und beide behaupteten die
    Wahrheit. (Vorbild: `planned_quantity` in `services/site_report`.)

    **Und die Raumhöhe zieht die abgeleiteten Wandflächen mit (0093):** Wer 2,50 m
    auf 2,80 m korrigiert, korrigiert damit jede Wand, die auf einer Kante steht
    und ihre Fläche daraus rechnet. Handeingaben (Giebel, Erker) bleiben stehen.
    """
    room = Room.objects.filter(id=room_id).first()
    if room is None:
        raise ValueError(f"Raum {room_id} existiert nicht")
    werte = _pruefe_raum(daten or {})
    if not werte:
        return get_room(room_id)

    # Zuordnung immer gegen den ZIELZUSTAND prüfen: wer nur unit_id setzt, muss
    # sich an dem building_id messen lassen, das der Raum schon trägt.
    ziel_building = werte.get("building_id", room.building_id)
    ziel_unit = werte.get("unit_id", room.unit_id)
    ziel_building, _ = _pruefe_zuordnung(room.property_id, ziel_building, ziel_unit)
    if ziel_unit is not None and "building_id" not in werte:
        werte["building_id"] = ziel_building

    try:
        with business_transaction(actor_app_user_id):
            # Sperre zuerst (siehe `_sperre_raum`): Umriss und ALTE Raumhöhe werden
            # darunter gelesen. Beides entscheidet, WAS geschrieben wird — ein
            # ungesperrter Blick darauf wäre ein Blick auf einen Stand, der beim
            # Schreiben schon nicht mehr gilt.
            alte_hoehe = _sperre_raum(room_id)
            laengen = kantenlaengen(room)

            # Wer zeichnet, misst nicht doppelt (0091): Bei vorhandenem Umriss sind
            # Fläche und Umfang ABGELEITET — ein Client-Wert wird verworfen.
            if laengen:
                for feld in GEZEICHNETE_FELDER:
                    werte.pop(feld, None)
            if not werte:
                return get_room(room_id)

            # NOT NULL in der DB: ein explizites null wäre ein 500, kein Löschen.
            for feld in ROOM_PFLICHT + ("status",):
                if feld in werte and werte[feld] is None:
                    raise ValueError(
                        f"{feld} ist ein Pflichtfeld und darf nicht leer sein."
                    )

            # QuerySet.update() schreibt nur die genannten Spalten — die
            # GENERATED-Spalte volume_m3 bleibt außen vor (die DB zieht sie nach).
            Room.objects.filter(id=room_id).update(**werte)

            # Ändert sich die Raumhöhe, ändert sich JEDE abgeleitete Wandfläche mit
            # — sie ist Kantenlänge × Raumhöhe. Der Kern von 0093: Ohne diese Zeilen
            # rechnete die Heizlast nach einer Korrektur von 2,50 m auf 2,80 m
            # weiter mit den Wandflächen von 2,50 m, und es sähe völlig normal aus.
            neue_hoehe = werte.get("room_height_m")
            if laengen and neue_hoehe is not None and neue_hoehe != alte_hoehe:
                _rechne_abgeleitete_flaechen(room_id, neue_hoehe, laengen)
    except IntegrityError as exc:
        if _constraint(exc) == "room_dublette":
            # `ziel_unit` steht oben schon fest; Etage und Name kommen aus dem
            # PATCH, unveränderte Felder aus dem Bestand.
            ziel_storey = werte.get("storey", room.storey)
            ziel_name = werte.get("name", room.name)
            raise ValueError(_dublette_text(ziel_unit, ziel_storey, ziel_name)) from exc
        if _constraint(exc) == "room_opening_passt_in_flaeche":
            raise _flaeche_zu_klein(exc) from exc
        raise _db_fehler(exc) from exc
    except DataError as exc:
        raise _db_fehler(exc) from exc
    return get_room(room_id)


# --- Schreiben: Grundriss (Umriss als Satz) --------------------------------

def set_grundriss(actor_app_user_id, room_id, vertices):
    """Ersetzt den Umriss eines Raumes **als Satz** und leitet Fläche/Umfang ab.

    Leere Liste = Umriss entfernen. Danach sind `floor_area_m2` und `perimeter_m`
    wieder **Handeingabe** (die zuletzt gerechneten Werte bleiben stehen — sie sind
    ja gemessen, nur nicht mehr gezeichnet).

    Zwei Aufräumarbeiten fallen dabei an, und beide setzen auf **NULL statt 422**:

    * `room_surface.edge_index`, der auf eine **nicht mehr vorhandene** Kante zeigt
      (Umriss verkleinert oder entfernt),
    * `room_opening.position_m`, dessen Kante weggefallen ist **oder** so kurz
      wurde, dass die Öffnung nicht mehr hineinpasst.

    Ein 422 wäre hier die falsche Härte: Der Bediener käme aus der Lage nicht mehr
    heraus — er müsste erst jede Wand von Hand entkoppeln, bevor er den Raum neu
    zeichnen darf. Und NULL ist keine Erfindung, sondern die Wahrheit: Die Wand
    steht auf **keiner bekannten** Kante mehr, die Lage der Öffnung ist
    **unbekannt**. Beides ist folgenlos für die Heizlast — Bruttofläche und
    Öffnungsfläche bleiben unangetastet, die Wand zählt weiter voll mit; sie wird
    nur nicht mehr gezeichnet. Das ist dieselbe Hausregel wie überall:
    **fehlende Lage heißt unbekannt, nicht „bei 0 m"**.
    """
    room = Room.objects.filter(id=room_id).first()
    if room is None:
        raise ValueError(f"Raum {room_id} existiert nicht")

    punkte = _pruefe_umriss(vertices)
    n = len(punkte)

    flaeche = umfang = None
    laengen = []
    if punkte:
        laengen = _kantenlaengen_m(punkte)
        # Skala/Wertebereich der Zielspalten: ein absurd großes Polygon ist ein
        # Bedienfehler (422), kein „numeric field overflow" (500).
        flaeche = _numerik(_polygon_flaeche_m2(punkte), "floor_area_m2",
                           NUMERIK_RAUM["floor_area_m2"])
        umfang = _numerik(sum(laengen, Decimal(0)), "perimeter_m",
                          NUMERIK_RAUM["perimeter_m"])

    try:
        with business_transaction(actor_app_user_id):
            # Serialisierungspunkt (siehe `_sperre_raum`): Ohne diese Sperre könnte
            # ein nebenläufiges `set_aufbau` gerade eine Wand auf eine Kante setzen,
            # die dieser Aufruf im selben Moment abschafft. Die Raumhöhe kommt aus
            # derselben, gesperrten Zeile — nicht aus dem vorher geladenen Objekt.
            room_height_m = _sperre_raum(room_id)

            RoomVertex.objects.filter(room_id=room_id).delete()
            for i, (x, y) in enumerate(punkte):
                _insert(
                    'property."room_vertex"',
                    {"id": uuid.uuid4(), "room_id": room_id, "idx": i,
                     "x_mm": x, "y_mm": y},
                )

            # Kanten, die es nicht mehr gibt (n = 0 beim Entfernen → alle).
            # `area_is_derived` MUSS mitfallen: Ohne Kante gibt es nichts, woraus
            # sich etwas ableiten ließe — der CHECK `room_surface_abgeleitet_nur_
            # auf_kante` (0093) verlangt genau das. Die Fläche selbst bleibt stehen
            # (sie war gemessen, nur nicht mehr gezeichnet) und wird ab jetzt wie
            # eine Handeingabe behandelt.
            RoomSurface.objects.filter(
                room_id=room_id, edge_index__gte=n
            ).update(edge_index=None, area_is_derived=False)

            # Der Kern von 0093: Die abgeleiteten Wände folgen dem neuen Umriss.
            # Ohne das trüge eine Wand auf einer jetzt 4,37 m langen Kante weiter
            # die Fläche der alten 4,00-m-Kante — still falsch.
            if laengen:
                _rechne_abgeleitete_flaechen(room_id, room_height_m, laengen)

            # Öffnungen, deren Lage ihren Bezugspunkt verloren hat oder nicht mehr
            # in ihre (verkürzte) Kante passt.
            kante_je_wand = {
                s.id: laengen[s.edge_index]
                for s in RoomSurface.objects.filter(room_id=room_id)
                if s.edge_index is not None
            }
            for o in RoomOpening.objects.filter(
                room_id=room_id, position_m__isnull=False
            ):
                laenge = kante_je_wand.get(o.surface_id)
                if laenge is None or o.position_m + o.width_m > laenge:
                    RoomOpening.objects.filter(id=o.id).update(position_m=None)

            if punkte:
                Room.objects.filter(id=room_id).update(
                    floor_area_m2=flaeche, perimeter_m=umfang
                )
    except IntegrityError as exc:
        if _constraint(exc) == "room_opening_passt_in_flaeche":
            raise _flaeche_zu_klein(exc) from exc
        raise _db_fehler(exc) from exc
    except DataError as exc:
        raise _db_fehler(exc) from exc
    return get_room(room_id)


# --- Schreiben: Aufbau (Hüllflächen + Öffnungen als Satz) -------------------

def _ref(wert):
    """Normalisiert einen Client-Schlüssel (`ref` / `surface_ref`).

    Beide Seiten MÜSSEN gleich normalisiert werden: `refs` wurde mit `.strip()`
    gebaut, `surface_ref` ungestrippt verglichen — ein in sich konsistenter
    Payload (`ref: ' s1'`, `surface_ref: ' s1'`) flog mit „unbekannte Hüllfläche"
    heraus.
    """
    return None if wert is None else str(wert).strip()


def _templates(eintraege):
    """Lädt die gewählten Vorlagen in EINER Query (kein N+1 über die Zeilen)."""
    ids = {e.get("template_id") for e in eintraege if e.get("template_id") is not None}
    if not ids:
        return {}
    return {t.id: t for t in ComponentTemplate.objects.filter(id__in=ids)}


def _vorlage(eintrag, templates, erwartet, bezeichnung):
    """Löst `template_id` auf — die Vorlage ist eine KOPIERQUELLE (0090).

    Unbekannte ID → 422. **INAKTIV ist erlaubt**, und das ist eine bewusste
    Entscheidung: `set_aufbau` ersetzt den ganzen Satz, also schickt der Editor bei
    JEDER Änderung auch die Zeilen mit, die schon länger stehen. Würde eine
    stillgelegte Vorlage hier abgewiesen, ließe sich ein Raum, dessen Wand auf eine
    inzwischen stillgelegte Vorlage zeigt, überhaupt nicht mehr bearbeiten — das
    Stilllegen einer Vorlage würde fremde Aufmaße lahmlegen. `status` steuert die
    **Auswahl** (`list_templates(nur_aktive=True)`), nicht den Bestand.
    """
    t_id = eintrag.get("template_id")
    if t_id is None:
        return None
    t = templates.get(t_id)
    if t is None:
        raise ValueError(f"{bezeichnung}: unbekannte Bauteilvorlage {t_id}.")
    if t.kind != erwartet:
        gattung = "Flächenvorlage" if erwartet == "FLAECHE" else "Öffnungsvorlage"
        raise ValueError(
            f"{bezeichnung}: '{t.name}' ist keine {gattung} "
            f"(kind='{t.kind}', erwartet '{erwartet}')."
        )
    return t


def _pruefe_flaeche(eintrag, index, room_height_m, laengen, templates):
    ref = _ref(eintrag.get("ref"))
    if not ref:
        raise ValueError(f"Hüllfläche {index + 1}: 'ref' darf nicht leer sein.")
    typ = eintrag.get("surface_type")
    if typ not in SURFACE_TYPES:
        raise ValueError(
            f"Hüllfläche '{ref}': ungültiger surface_type '{typ}'. "
            f"Erlaubt: {', '.join(SURFACE_TYPES)}."
        )
    adj = eintrag.get("adjacent")
    if adj not in ADJACENT:
        raise ValueError(
            f"Hüllfläche '{ref}': ungültiges adjacent '{adj}'. "
            f"Erlaubt: {', '.join(ADJACENT)}."
        )
    orient = eintrag.get("orientation")
    if orient is not None and orient not in ORIENTATIONS:
        raise ValueError(
            f"Hüllfläche '{ref}': ungültige orientation '{orient}'. "
            f"Erlaubt: {', '.join(ORIENTATIONS)}."
        )
    prefix = f"Hüllfläche '{ref}': "
    vorlage = _vorlage(eintrag, templates, "FLAECHE", f"Hüllfläche '{ref}'")

    # --- Kante des Umrisses (0091) ----------------------------------------
    kante = eintrag.get("edge_index")
    if kante is not None:
        try:
            kante = int(kante)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}edge_index ist keine ganze Zahl.") from exc
        if not laengen:
            raise ValueError(
                f"Hüllfläche '{ref}': edge_index ist gesetzt, aber der Raum hat "
                "keinen Umriss — erst zeichnen, dann Wände auf Kanten setzen."
            )
        if not (0 <= kante < len(laengen)):
            raise ValueError(
                f"Hüllfläche '{ref}': Kante {kante} gibt es nicht — der Umriss hat "
                f"{len(laengen)} Kanten (0 bis {len(laengen) - 1})."
            )
        # 0094: Eine Kante trägt nur ein SENKRECHTES Bauteil. Sonst bekäme die
        # Decke die Fläche „Kantenlänge × Raumhöhe" (5,00 × 2,50 = 12,50 m² statt
        # ihrer 20 m²) — und wüchse als abgeleitete Fläche fortan mit der Raumhöhe
        # mit. Eine Decke, die größer wird, wenn der Raum höher wird.
        if typ not in KANTEN_BAUTEILE:
            raise ValueError(
                f"Hüllfläche '{ref}': '{typ}' kann nicht auf einer Kante des "
                "Umrisses stehen — der Umriss ist die Draufsicht, seine Kanten sind "
                f"die senkrechten Bauteile ({', '.join(KANTEN_BAUTEILE)}). Decke, "
                "Boden und Dachschräge beziehen sich auf die Grundfläche: bitte "
                "edge_index weglassen und gross_area_m2 eintragen."
            )

    brutto = _numerik(
        _dec(eintrag.get("gross_area_m2"), f"{prefix}gross_area_m2"),
        f"{prefix}gross_area_m2", NUMERIK_FLAECHE["gross_area_m2"],
    )
    # `area_is_derived` (0093): Die Zeile merkt sich, WOHER ihr Wert stammt. Ohne
    # das erstarrt eine gerechnete Fläche bei der nächsten Höhen- oder
    # Umrissänderung still auf dem alten Stand — die Heizlast rechnete weiter mit
    # 2,50 m, obwohl der Raum 2,80 m hoch ist, und es sähe völlig normal aus.
    abgeleitet = False
    if brutto is None and kante is not None:
        # Wer zeichnet, misst nicht doppelt: Kantenlänge × Raumhöhe. Ein vom
        # Client mitgeschickter Wert GEWINNT allerdings — die Giebelwand und der
        # Erker sind keine Rechtecke, und der Bediener weiß das besser als die
        # Formel. Genau deshalb reicht „immer nachrechnen" nicht, und genau
        # deshalb gibt es die Spalte.
        brutto = _numerik(
            _q(laengen[kante] * room_height_m, _M2),
            f"{prefix}gross_area_m2", NUMERIK_FLAECHE["gross_area_m2"],
        )
        abgeleitet = True
    if brutto is None or brutto <= 0:
        raise ValueError(f"Hüllfläche '{ref}': gross_area_m2 muss größer als 0 sein.")

    u = _numerik(
        _dec(eintrag.get("u_value"), f"{prefix}u_value"),
        f"{prefix}u_value", NUMERIK_FLAECHE["u_value"],
    )
    if u is not None and u <= 0:
        raise ValueError(f"Hüllfläche '{ref}': u_value muss größer als 0 sein.")
    if u is None and vorlage is not None:
        # DIE Stelle des Katalogs: der Wert wird KOPIERT, nicht verlinkt (0090).
        # Ein eigener Messwert schlägt die Vorlage (oben). Hat die Vorlage keinen
        # Wert (Auslieferungszustand), bleibt der U-Wert leer — unbekannt, nicht 0.
        u = vorlage.u_value
    f = _numerik(
        _dec(eintrag.get("temp_factor"), f"{prefix}temp_factor"),
        f"{prefix}temp_factor", NUMERIK_FLAECHE["temp_factor"],
    )
    if f is not None and not (Decimal(0) <= f <= Decimal(1)):
        raise ValueError(f"Hüllfläche '{ref}': temp_factor muss zwischen 0 und 1 liegen.")
    if adj == "AUSSENLUFT" and f is not None and f != Decimal(1):
        # room_surface_aussenluft_faktor: gegen Außenluft ist der Faktor per
        # Definition 1,0 (volle Temperaturdifferenz) — keine Normtabelle.
        raise ValueError(
            f"Hüllfläche '{ref}': gegen AUSSENLUFT ist temp_factor per Definition "
            "1,0 (oder leer)."
        )
    return {
        "surface_type": typ,
        "adjacent": adj,
        "orientation": orient,
        "label": eintrag.get("label"),
        "gross_area_m2": brutto,
        "u_value": u,
        "temp_factor": f,
        # Herkunftsvermerk (0090) — der WERT oben ist bereits kopiert.
        "template_id": vorlage.id if vorlage is not None else None,
        "edge_index": kante,
        # true = gerechnet, wird bei jeder Höhen-/Umrissänderung NEU gerechnet.
        # false = Handeingabe, wird NIE überschrieben (0093).
        "area_is_derived": abgeleitet,
    }


def _pruefe_oeffnung(eintrag, index, refs, flaeche_je_ref, laengen, templates):
    ref = _ref(eintrag.get("surface_ref"))
    if ref is not None and ref not in refs:
        raise ValueError(
            f"Öffnung {index + 1}: unbekannte Hüllfläche '{ref}'. "
            f"Bekannt: {', '.join(sorted(refs)) or '—'}."
        )
    typ = eintrag.get("opening_type")
    if typ not in OPENING_TYPES:
        raise ValueError(
            f"Öffnung {index + 1}: ungültiger opening_type '{typ}'. "
            f"Erlaubt: {', '.join(OPENING_TYPES)}."
        )
    prefix = f"Öffnung {index + 1}: "
    menge = eintrag.get("quantity")
    try:
        menge = None if menge is None else int(menge)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{prefix}quantity ist keine ganze Zahl.") from exc
    if menge is None or menge <= 0:
        raise ValueError(f"{prefix}quantity muss größer als 0 sein.")
    if menge > MAX_QUANTITY:
        raise ValueError(f"{prefix}quantity darf höchstens {MAX_QUANTITY} sein.")
    breite = _numerik(
        _dec(eintrag.get("width_m"), f"{prefix}width_m"),
        f"{prefix}width_m", NUMERIK_OEFFNUNG["width_m"],
    )
    hoehe = _numerik(
        _dec(eintrag.get("height_m"), f"{prefix}height_m"),
        f"{prefix}height_m", NUMERIK_OEFFNUNG["height_m"],
    )
    if breite is None or breite <= 0:
        raise ValueError(f"Öffnung {index + 1}: width_m muss größer als 0 sein.")
    if hoehe is None or hoehe <= 0:
        raise ValueError(f"Öffnung {index + 1}: height_m muss größer als 0 sein.")
    vorlage = _vorlage(eintrag, templates, "OEFFNUNG", f"Öffnung {index + 1}")
    u = _numerik(
        _dec(eintrag.get("u_value"), f"{prefix}u_value"),
        f"{prefix}u_value", NUMERIK_OEFFNUNG["u_value"],
    )
    if u is not None and u <= 0:
        raise ValueError(f"Öffnung {index + 1}: u_value muss größer als 0 sein.")
    if u is None and vorlage is not None:
        # KOPIE, kein Verweis (0090) — siehe `_pruefe_flaeche`.
        u = vorlage.u_value
    # area_m2 ist GENERATED: die Faktoren passen je einzeln, ihr Produkt kann die
    # Spalte trotzdem sprengen (DataError → 500).
    _numerik(Decimal(menge) * breite * hoehe, f"{prefix}Fläche (Menge × Breite × Höhe)",
             NUMERIK_OEFFNUNG_FLAECHE)

    # --- Lage in der Kante (0091) -----------------------------------------
    # Eine Öffnung OHNE position_m bleibt gültig: sie zählt ganz normal in Fläche
    # und Heizlast, sie wird nur nicht gezeichnet. Sie wird hier NICHT auf 0
    # gesetzt — fehlende Lage heißt **unbekannt**, nicht „bei 0 m".
    pos = _numerik(
        _dec(eintrag.get("position_m"), f"{prefix}position_m"),
        f"{prefix}position_m", NUMERIK_OEFFNUNG["position_m"],
    )
    if pos is not None:
        if pos < 0:
            raise ValueError(f"Öffnung {index + 1}: position_m darf nicht negativ sein.")
        if ref is None:
            raise ValueError(
                f"Öffnung {index + 1}: position_m ohne Wand (surface_ref) hat keinen "
                "Bezugspunkt — die Lage zählt vom Anfangspunkt der Wandkante."
            )
        kante = flaeche_je_ref[ref]["edge_index"]
        if kante is None:
            raise ValueError(
                f"Öffnung {index + 1}: die Wand '{ref}' steht auf keiner Kante des "
                "Umrisses (edge_index fehlt) — ohne Kante hat position_m keinen "
                "Bezugspunkt."
            )
        if menge > 1:
            # Drei Fenster können nicht an EINER Stelle sitzen. Wer sie zeichnen
            # will, legt drei Zeilen mit je eigener Lage an; wer nur die Menge
            # braucht, lässt position_m weg (die Öffnung zählt trotzdem voll mit).
            raise ValueError(
                f"Öffnung {index + 1}: eine Lage (position_m) lässt sich nur für "
                "EINE Öffnung angeben (quantity = 1) — für mehrere Fenster je "
                "Fenster eine Zeile."
            )
        laenge = laengen[kante]
        if pos + breite > laenge:
            raise ValueError(
                f"Öffnung {index + 1}: sie passt nicht in ihre Kante — Lage "
                f"{_zahl_de(pos)} m + Breite {_zahl_de(breite)} m = "
                f"{_zahl_de(pos + breite)} m, die Kante {kante} der Wand '{ref}' ist "
                f"aber nur {_zahl_de(laenge)} m lang."
            )

    return {
        "surface_ref": ref,
        "opening_type": typ,
        "label": eintrag.get("label"),
        "quantity": int(menge),
        "width_m": breite,
        "height_m": hoehe,
        "u_value": u,
        "template_id": vorlage.id if vorlage is not None else None,
        "position_m": pos,
    }


def set_aufbau(actor_app_user_id, room_id, surfaces, openings):
    """Ersetzt Hüllflächen UND Öffnungen eines Raumes **atomar als Satz**.

    Delete+Insert statt Teil-Update: dieselbe dokumentierte Ausnahme wie bei
    `invoicing.quote_line` und `workflow.site_report_line` — ein Editor, der Zeilen
    umsortiert und streicht, hat keine stabile Identität je Zeile.

    Die Öffnungen des Clients zeigen über den freien Schlüssel `surface_ref` auf
    ihre Wand ('s1', 's2', …); der Service löst ihn auf die frisch erzeugte UUID
    auf. `surface_ref = None` ist erlaubt: reiner Mengenabzug ohne
    Bauteilzuordnung (Malerarbeiten) — eine solche Öffnung zählt NICHT in die
    Transmission.

    Zwei Dinge kommen aus 0090/0091 dazu:

    * `template_id` — Herkunft aus dem Bauteilkatalog. Der U-Wert der Vorlage wird
      **kopiert**, nicht verlinkt; ein eigener `u_value` gewinnt.
    * `edge_index` / `position_m` — die Wand steht auf einer Polygonkante, die
      Öffnung an einer Stelle dieser Kante. Ohne `gross_area_m2` rechnet der
      Server die Wandfläche als Kantenlänge × Raumhöhe.

    **Umriss und Raumhöhe werden erst NACH der Sperre gelesen** (siehe
    `_sperre_raum`): Ein `edge_index`, gegen einen ungesperrt gelesenen Umriss
    geprüft, kann bis zum INSERT längst ins Leere zeigen.
    """
    room = Room.objects.filter(id=room_id).first()
    if room is None:
        raise ValueError(f"Raum {room_id} existiert nicht")

    surfaces = list(surfaces or [])
    openings = list(openings or [])
    # Der Katalog ist Stammdatum und hängt nicht am Raum — er darf vor der Sperre
    # gelesen werden (der U-Wert wird ohnehin kopiert, nicht verlinkt).
    templates = _templates(surfaces + openings)

    ref_to_id = {}
    try:
        with business_transaction(actor_app_user_id):
            # ERST sperren, DANN den Umriss lesen und dagegen validieren — sonst
            # prüft man gegen einen Stand, den ein nebenläufiges `set_grundriss`
            # bis zum INSERT längst überholt hat.
            room_height_m = _sperre_raum(room_id)
            punkte = _punkte(room)
            laengen = _kantenlaengen_m(punkte) if punkte else []

            geprueft_f = [
                _pruefe_flaeche(s, i, room_height_m, laengen, templates)
                for i, s in enumerate(surfaces)
            ]
            # Beide Seiten des Schlüssels gleich normalisieren — siehe `_ref`.
            refs = [_ref(s.get("ref")) for s in surfaces]
            doppelt = {r for r in refs if refs.count(r) > 1}
            if doppelt:
                raise ValueError(
                    f"Doppelte Hüllflächen-Schlüssel (ref): "
                    f"{', '.join(sorted(doppelt))}."
                )
            # uq_room_surface_edge (0091): Zwei Wände auf derselben Kante zählten
            # dieselbe Fläche doppelt in die Heizlast. Die DB verbietet es — hier
            # steht die Meldung.
            kanten = [
                d["edge_index"] for d in geprueft_f if d["edge_index"] is not None
            ]
            mehrfach = {k for k in kanten if kanten.count(k) > 1}
            if mehrfach:
                raise ValueError(
                    "Auf derselben Kante des Umrisses steht mehr als eine Wand "
                    f"(Kante {', '.join(str(k) for k in sorted(mehrfach))}) — je "
                    "Kante genau eine Wand, sonst zählt dieselbe Fläche doppelt."
                )
            flaeche_je_ref = dict(zip(refs, geprueft_f))
            geprueft_o = [
                _pruefe_oeffnung(o, i, set(refs), flaeche_je_ref, laengen, templates)
                for i, o in enumerate(openings)
            ]

            ref_to_id = {r: uuid.uuid4() for r in refs}

            # Reihenfolge ist Pflicht: die Öffnung hängt per FK an ihrer Wand.
            RoomOpening.objects.filter(room_id=room_id).delete()
            RoomSurface.objects.filter(room_id=room_id).delete()
            for ref, daten in zip(refs, geprueft_f):
                _insert(
                    'property."room_surface"',
                    {"id": ref_to_id[ref], "room_id": room_id, **daten},
                )
            for daten in geprueft_o:
                ref = daten.pop("surface_ref")
                _insert(
                    'property."room_opening"',
                    {
                        "id": uuid.uuid4(),
                        "room_id": room_id,
                        "surface_id": ref_to_id[ref] if ref is not None else None,
                        **daten,
                    },
                )
    except IntegrityError as exc:
        if _constraint(exc) == "room_opening_passt_in_flaeche":
            # Der Trigger nennt Fläche und Quadratmeter bereits klar und deutsch
            # — je Wand (a) wie raumweit (b), siehe Migration 0089.
            meldung = str(getattr(exc, "__cause__", exc)).splitlines()[0].strip()
            raise ValueError(
                f"{meldung} Eine Öffnung ist nie größer als ihre Fläche — sonst "
                "wäre die Nettowandfläche negativ."
            ) from exc
        raise _db_fehler(exc) from exc
    except DataError as exc:
        raise _db_fehler(exc) from exc
    return get_room(room_id)


# --- Auslegungsdaten: sie kommen vom OBJEKT (Migration 0089) ---------------

class Auslegung(NamedTuple):
    """Die beiden Auslegungswerte, die eine Anfrage tatsächlich verwendet.

    Regelfall: die Werte der Liegenschaft. Ein Aufrufer darf sie übersteuern
    (Was-wäre-wenn am Bildschirm) — deshalb sind das hier die **wirksamen**
    Werte, nicht zwingend die gespeicherten.
    """

    aussentemperatur_c: Decimal | None
    kennwert_w_m2: Decimal | None


def _pruefe_auslegungswerte(temp, kennwert):
    """Die EINE Prüfstelle für die Wertebereiche der Auslegungsdaten.

    Sie gilt für den Schreibpfad (`set_auslegung`) **und** für die Übersteuerung
    am Aufruf (`auslegung`). Sonst entstünde eine dritte Wahrheit: Die DB nähme
    einen Kennwert von 0 nie an — er erreichte sie über den Was-wäre-wenn-Pfad
    aber auch nie, und das Ergebnis wären **0,0 kW** statt „unbekannt".

    Dass der Angular-Client die Parameter nicht sendet, ist dabei kein Argument:
    Nach der Vision geht die KI durch denselben Service wie ein Mensch.
    """
    if temp is not None and not (AUSSENTEMP_MIN <= temp <= AUSSENTEMP_MAX):
        raise ValueError(
            f"{FELD_LABEL['design_outdoor_temp_c']}: Der Wert muss zwischen "
            f"{_zahl_de(AUSSENTEMP_MIN)} und {_zahl_de(AUSSENTEMP_MAX)} °C liegen "
            "(design_outdoor_temp_c)."
        )
    if kennwert is not None and kennwert <= 0:
        raise ValueError(
            f"{FELD_LABEL['heat_load_w_per_m2']}: Der Wert muss größer als 0 sein "
            "— 0 ist kein Kennwert, sondern eine unbekannte Größe "
            "(heat_load_w_per_m2)."
        )


def auslegung(prop, aussentemperatur_c=None, kennwert_w_m2=None):
    """Wirksame Auslegungsdaten: Objektwerte, optional übersteuert.

    `prop` ist die Liegenschaft des Raumes (oder None). Fehlt ein Wert an beiden
    Stellen, bleibt er `None` — es wird **nichts erfunden** (keine
    DIN-Klimatabelle, kein erfundener Gebäudekennwert).

    Die Übersteuerung durchläuft **dieselben Grenzen** wie der Schreibpfad
    (`_pruefe_auslegungswerte`): ein ungültiger Parameter liefert nie ein
    gerechnetes Ergebnis, sondern einen Fehler mit benanntem Grund (→ 422).
    """
    aussen = _numerik(
        _dec(aussentemperatur_c, "aussentemperatur_c"), "aussentemperatur_c",
        NUMERIK_LIEGENSCHAFT["design_outdoor_temp_c"],
    )
    kennwert = _numerik(
        _dec(kennwert_w_m2, "kennwert_w_m2"), "kennwert_w_m2",
        NUMERIK_LIEGENSCHAFT["heat_load_w_per_m2"],
    )
    _pruefe_auslegungswerte(aussen, kennwert)
    if prop is not None:
        if aussen is None:
            aussen = prop.design_outdoor_temp_c
        if kennwert is None:
            kennwert = prop.heat_load_w_per_m2
    return Auslegung(aussen, kennwert)


def auslegung_fuer(property_id, aussentemperatur_c=None, kennwert_w_m2=None):
    """Wie `auslegung`, lädt die Liegenschaft aber selbst (EINE Query).

    Für Listen die Liegenschaft **einmal** laden und `auslegung()` verwenden —
    sonst entsteht ein N+1 über die Räume.
    """
    return auslegung(
        Property.objects.filter(pk=property_id).first(),
        aussentemperatur_c,
        kennwert_w_m2,
    )


def set_auslegung(actor_app_user_id, property_id, daten):
    """Setzt die Auslegungsdaten einer Liegenschaft (Migration 0089).

    PATCH-Semantik: nur die **übergebenen** Schlüssel werden geschrieben; ein
    ausdrückliches `None` setzt das Feld zurück (beide Spalten sind NULL-fähig —
    ohne Wert ist die Heizlast unbekannt, nicht 0).
    """
    ensure_exists(Property, property_id, "Liegenschaft")
    unbekannt = set(daten or {}) - set(PROPERTY_AUSLEGUNG_FIELDS)
    if unbekannt:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")

    werte = _numerik_felder(dict(daten or {}), NUMERIK_LIEGENSCHAFT)
    # Dieselbe Prüfstelle wie beim Was-wäre-wenn-Pfad (`auslegung`) — sonst laufen
    # Schreibweg und Lese-Übersteuerung auseinander.
    _pruefe_auslegungswerte(
        werte.get("design_outdoor_temp_c"), werte.get("heat_load_w_per_m2")
    )

    if werte:
        try:
            with business_transaction(actor_app_user_id):
                Property.objects.filter(pk=property_id).update(**werte)
        except (DataError, IntegrityError) as exc:
            raise _db_fehler(exc) from exc
    return Property.objects.get(pk=property_id)


# --- Rechnen: die einzige Rechenstelle -------------------------------------

def _oeffnungsflaeche(oeffnung):
    """Fläche einer Öffnung. `area_m2` ist GENERATED; frisch gebaute Objekte
    (noch nicht nachgeladen) tragen sie nicht — dann selbst rechnen."""
    if oeffnung.area_m2 is not None:
        return oeffnung.area_m2
    return _q(
        Decimal(oeffnung.quantity) * oeffnung.width_m * oeffnung.height_m, _M2
    )


def kennzahlen(room, aussentemperatur_c=None, kennwert_w_m2=None, *, vorgabe=None):
    """Kennzahlen und Heizlast EINES Raumes. Die einzige Rechenstelle.

    Die Auslegungsdaten kommen aus der **Liegenschaft** des Raumes
    (`design_outdoor_temp_c`, `heat_load_w_per_m2`; Migration 0089) — der Client
    muss nichts mitschicken. `aussentemperatur_c`/`kennwert_w_m2` übersteuern die
    Objektwerte (Was-wäre-wenn).

    `vorgabe` ist die bereits gebildete `Auslegung` (inkl. Übersteuerung). Wer sie
    übergibt, spart die Liegenschafts-Query je Raum — für Listen ist das Pflicht
    (N+1). Ist sie gesetzt, sind die beiden Einzelparameter bereits eingerechnet
    und werden nicht noch einmal ausgewertet.

    Rangfolge des Kennwerts: Raum → Liegenschaft → `None`. Fehlt ein Wert, bleibt
    das Ergebnis **unbekannt (None), nie 0** — mit Grund.
    """
    if vorgabe is None:
        vorgabe = auslegung_fuer(room.property_id, aussentemperatur_c, kennwert_w_m2)
    aussen = vorgabe.aussentemperatur_c
    gebaeude_kennwert = vorgabe.kennwert_w_m2

    flaechen = list(room.surfaces.all())
    oeffnungen = list(room.openings.all())

    hinweise = []
    gruende = []

    # --- Geometrie ---------------------------------------------------------
    volumen = room.volume_m3
    if volumen is None:
        volumen = _q(room.floor_area_m2 * room.room_height_m, _M2)

    je_wand = {}
    for o in oeffnungen:
        if o.surface_id is not None:
            je_wand[o.surface_id] = je_wand.get(o.surface_id, Decimal(0)) + _oeffnungsflaeche(o)

    # Ohne aufgenommene Hüllfläche ist die Wandfläche UNBEKANNT, nicht 0 — dieselbe
    # Haltung wie bei der Heizlast (und dieselbe Stelle, an der die DB-Grenze (b)
    # aus 0089 nicht greifen kann). Eine 0 hier liefe als Mengengrundlage fürs
    # Verputzen/Streichen in ein Angebot.
    brutto = sum((f.gross_area_m2 for f in flaechen), Decimal(0)) if flaechen else None
    oeffnung_gesamt = sum((_oeffnungsflaeche(o) for o in oeffnungen), Decimal(0))
    netto = None if brutto is None else brutto - oeffnung_gesamt

    # --- Kennwertverfahren -------------------------------------------------
    # Der Raumkennwert schlägt den Gebäudekennwert. Explizit auf None prüfen, nicht
    # `or`: ein Kennwert 0 wäre eine Aussage („braucht keine Wärme"), kein „fehlt".
    kennwert = (
        room.heat_load_w_per_m2
        if room.heat_load_w_per_m2 is not None
        else gebaeude_kennwert
    )
    if kennwert is None:
        heizlast_kennwert = None
        hinweise.append(
            "Kennwertverfahren: weder der Raum noch die Liegenschaft trägt einen "
            "Kennwert (W/m²) — die Heizlast ist unbekannt."
        )
    else:
        heizlast_kennwert = _q(room.floor_area_m2 * kennwert, _W)

    # --- Hüllflächenverfahren ---------------------------------------------
    if not flaechen:
        # Ohne aufgenommene Hüllflächen wäre die Transmission rechnerisch 0 W —
        # also „dieser Raum verliert keine Wärme". Das ist der Fehler, gegen den
        # dieser ganze Rechner gebaut ist: unbestimmt ist nicht null.
        gruende.append("Für diesen Raum ist keine Hüllfläche aufgenommen.")
    if room.indoor_temp_c is None:
        gruende.append("Die Innentemperatur des Raumes fehlt.")
    if aussen is None:
        gruende.append(
            "Die Auslegungs-Außentemperatur ist an der Liegenschaft nicht "
            "hinterlegt (design_outdoor_temp_c)."
        )

    delta_t = None
    if room.indoor_temp_c is not None and aussen is not None:
        delta_t = room.indoor_temp_c - aussen

    # Öffnungen ohne Wandzuordnung sind reiner Mengenabzug — sie tragen NICHT zur
    # Transmission bei. Ein U-Wert an einer solchen Öffnung ist ein Widerspruch.
    for o in oeffnungen:
        if o.surface_id is None and o.u_value is not None:
            hinweise.append(
                f"Öffnung '{o.label or o.opening_type}' trägt einen U-Wert, ist "
                "aber keiner Fläche zugeordnet — sie zählt nur als Mengenabzug, "
                "nicht in die Transmission."
            )

    transmission = Decimal(0) if delta_t is not None else None
    for f in flaechen:
        bezeichnung = f.label or f.surface_type
        if f.adjacent == "BEHEIZT":
            # Kein Temperaturgefälle zum beheizten Nachbarn → definitionsgemäß 0 W.
            # Das ist KEIN „unbekannt": die Fläche braucht weder U-Wert noch Faktor.
            continue

        if f.adjacent == "AUSSENLUFT":
            # Volle Temperaturdifferenz — das ist die Bedeutung von „gegen
            # Außenluft", keine Normtabelle.
            faktor = f.temp_factor if f.temp_factor is not None else Decimal(1)
        else:
            faktor = f.temp_factor
            if faktor is None:
                gruende.append(
                    f"Der Temperaturkorrekturfaktor der Fläche '{bezeichnung}' "
                    f"(grenzt an {f.adjacent}) fehlt."
                )

        if f.u_value is None:
            gruende.append(f"Der U-Wert der Fläche '{bezeichnung}' fehlt.")

        for o in oeffnungen:
            if o.surface_id == f.id and o.u_value is None:
                gruende.append(
                    f"Der U-Wert der Öffnung '{o.label or o.opening_type}' in der "
                    f"Fläche '{bezeichnung}' fehlt."
                )

        if transmission is None or faktor is None or f.u_value is None:
            continue

        netto_wand = f.gross_area_m2 - je_wand.get(f.id, Decimal(0))
        transmission += f.u_value * netto_wand * faktor * delta_t
        for o in oeffnungen:
            if o.surface_id == f.id and o.u_value is not None:
                transmission += o.u_value * _oeffnungsflaeche(o) * faktor * delta_t

    if gruende:
        transmission = None

    # --- Lüftung -----------------------------------------------------------
    lueftung_gruende = []
    if room.air_change_rate is None:
        lueftung_gruende.append("Die Luftwechselrate des Raumes fehlt.")
    if delta_t is None:
        lueftung = None
    elif room.air_change_rate is None:
        lueftung = None
    else:
        lueftung = _q(
            C_LUFT_WH_PRO_M3K * room.air_change_rate * volumen * delta_t, _W
        )

    # Die Luftwechselrate ist auch dann ein Grund, wenn ΔT bereits fehlt — der
    # Betrieb soll alle Lücken auf einmal sehen, nicht eine nach der anderen.
    gruende_gesamt = gruende + lueftung_gruende

    # Die ausgewiesene Summe MUSS die Summe der ausgewiesenen Teile sein: erst
    # quantisieren, dann addieren. Andersherum wich `heizlast_huellflaeche_w` um
    # bis zu 0,1 W von `transmission_w + lueftung_w` ab — und wer nachrechnet,
    # findet eine Differenz, die es nicht gibt.
    transmission_w = _q(transmission, _W)
    gesamt = None
    if transmission_w is not None and lueftung is not None:
        gesamt = transmission_w + lueftung

    return {
        "floor_area_m2": room.floor_area_m2,
        "volume_m3": volumen,
        "perimeter_m": room.perimeter_m,
        # Woher kommen Fläche und Umfang? Das UI muss die beiden Felder sperren,
        # sobald der Raum gezeichnet ist (der Server verwirft dort Client-Werte) —
        # und es muss dem Bediener sagen können, warum.
        "geometrie_quelle": "GEZEICHNET" if room.vertices.all() else "EINGEGEBEN",
        "wall_area_gross_m2": _q(brutto, _M2),
        "opening_area_m2": _q(oeffnung_gesamt, _M2),
        "wall_area_net_m2": _q(netto, _M2),
        "heizlast_kennwert_w": heizlast_kennwert,
        "transmission_w": transmission_w,
        "lueftung_w": lueftung,
        "heizlast_huellflaeche_w": gesamt,
        "unbekannt_grund": " ".join(gruende_gesamt) if gruende_gesamt else None,
        "hinweise": hinweise,
    }


# --- Rechnen: Gebäudesummen ------------------------------------------------

def aufmass_property(property_id, aussentemperatur_c=None, kennwert_w_m2=None):
    """Aufmaß-Summen einer Liegenschaft über alle **aktiven** Räume.

    Die Auslegungsdaten kommen aus der Liegenschaft (0089) und werden hier
    **einmal** geladen (kein N+1 über die Räume); die beiden Parameter
    übersteuern sie. Die wirksamen Werte stehen im Ergebnis
    (`design_outdoor_temp_c`, `heat_load_w_per_m2`) — das Panel zeigt sie an und
    füllt damit sein Formular vor.

    Eine Heizlastsumme ist `None`, sobald **ein** Raum unbekannt ist — sonst wäre
    die Gebäudeheizlast still zu klein (die fehlenden Räume zählten als 0 W). Die
    betroffenen Räume werden benannt. Ist **gar kein** Raum aufgenommen, ist die
    Heizlast ebenfalls `None` (mit Grund) — eine leere Mengensumme ist 0, eine
    leere Heizlast ist unbekannt.

    `leitungslaenge_schaetzung_m` ist eine **Schätzung**: 2 × Σ Weg zur
    Steigleitung (Vor- und Rücklauf). Ohne Formstücke, ohne Steigstrang, ohne
    Zuschlagsfaktor — einen solchen Faktor erfindet MCN nicht. Trägt KEIN Raum
    einen Weg zur Steigleitung, ist sie `None`: eine „0,0 m" liefe als Menge in
    ein Angebot. Dasselbe gilt für `umfang_m` — beide Felder sind in der DB
    NULL-fähig, Fläche und Höhe sind es nicht (deshalb bleiben `flaeche_m2` und
    `volumen_m3` bei 0 Räumen eine leere Summe = 0).
    """
    vorgabe = auslegung_fuer(property_id, aussentemperatur_c, kennwert_w_m2)
    # `list_rooms` liefert von sich aus nur AKTIVE: Ein stillgelegter Raum (umgebaut,
    # zusammengelegt, weggefallen) darf die Gebäudesumme nicht mehr aufblähen — er
    # steht ja nicht mehr da. Gelöscht wird er nie (No-Delete).
    raeume = list_rooms(property_id)

    flaeche = sum((r.floor_area_m2 for r in raeume), Decimal(0))
    volumen = sum(
        (r.volume_m3 if r.volume_m3 is not None else r.floor_area_m2 * r.room_height_m
         for r in raeume),
        Decimal(0),
    )
    # Umfang und Weg zur Steigleitung sind in der DB NULL-fähig (0086) — im
    # Gegensatz zu Fläche und Höhe. Eine Summe über ein Feld, das NIEMAND gefüllt
    # hat, ist deshalb keine 0, sondern eine Nichtaussage: sie bleibt `None` mit
    # Grund. Hat ein TEIL der Räume den Wert, ist die Summe eine ehrliche
    # Teilsumme — mit Hinweis, wie viele fehlen.
    mit_umfang = [r.perimeter_m for r in raeume if r.perimeter_m is not None]
    ohne_umfang = len(raeume) - len(mit_umfang)
    umfang = sum(mit_umfang, Decimal(0)) if mit_umfang else None

    hinweise = []
    unbekannt = []

    summe_kennwert = Decimal(0)
    summe_huelle = Decimal(0)
    # Ohne EINEN aufgenommenen Raum ist die Heizlast des Objekts unbekannt — nicht
    # 0 W. Dieselbe Fehlerklasse, gegen die `kennzahlen()` gebaut ist (dort: keine
    # Hüllfläche → None mit Grund). Sonst meldete eine frisch angelegte
    # Liegenschaft „0,0 kW" — der billigste Weg zu einer kleinen Anlage wäre, gar
    # nichts zu messen.
    #
    # Die Mengensummen (flaeche_m2, volumen_m3, umfang_m) bleiben dagegen 0: eine
    # leere Summe IST 0 (es gibt keine Fläche, die nicht gezählt würde), während
    # eine unbekannte Heizlast keine Aussage ist. `umfang_m` trägt seine
    # Unvollständigkeit ohnehin über `ohne_umfang` im Hinweis.
    kennwert_unbekannt = not raeume
    huelle_unbekannt = not raeume
    if not raeume:
        hinweise.append(
            "Für diese Liegenschaft ist noch kein Raum aufgenommen — die Heizlast "
            "ist unbekannt, nicht 0 W."
        )

    for r in raeume:
        k = kennzahlen(r, vorgabe=vorgabe)
        if k["heizlast_kennwert_w"] is None:
            kennwert_unbekannt = True
            if r.name not in unbekannt:
                unbekannt.append(r.name)
        else:
            summe_kennwert += k["heizlast_kennwert_w"]
        if k["heizlast_huellflaeche_w"] is None:
            huelle_unbekannt = True
            if r.name not in unbekannt:
                unbekannt.append(r.name)
            if k["unbekannt_grund"]:
                hinweise.append(f"{r.name}: {k['unbekannt_grund']}")
        else:
            summe_huelle += k["heizlast_huellflaeche_w"]

    # Die beiden Verfahrenshinweise gelten nur, wenn es überhaupt Räume gibt —
    # sonst steht der Grund schon oben („noch kein Raum aufgenommen").
    if kennwert_unbekannt and raeume:
        hinweise.append(
            "Die Heizlast nach dem Kennwertverfahren ist unbekannt, solange nicht "
            "jeder Raum einen Kennwert (W/m²) trägt oder die Liegenschaft einen "
            "Gebäudekennwert führt."
        )
    if huelle_unbekannt and raeume:
        hinweise.append(
            "Die Heizlast nach dem Hüllflächenverfahren ist unbekannt, solange "
            "Eingaben fehlen (siehe oben). Sie wird NICHT als 0 W ausgewiesen."
        )
    if umfang is None:
        hinweise.append(
            "Kein Raum trägt einen gemessenen Umfang — die Umfangssumme ist "
            "unbekannt, nicht 0 m."
        )
    elif ohne_umfang:
        hinweise.append(
            f"{ohne_umfang} Raum/Räume ohne gemessenen Umfang — die Umfangssumme "
            "ist unvollständig."
        )

    mit_steig = [r.riser_distance_m for r in raeume if r.riser_distance_m is not None]
    ohne_steig = len(raeume) - len(mit_steig)
    # Die gefährlichste Stelle des Slices: eine Leitungslänge von „0,0 m" ginge
    # als MENGE in ein Angebot. Trägt kein Raum einen Weg zur Steigleitung, gibt
    # es nichts zu schätzen — dann `None`, nicht 0.
    leitung = (
        _q(Decimal(2) * sum(mit_steig, Decimal(0)), _M2) if mit_steig else None
    )
    if leitung is None:
        hinweise.append(
            "Kein Raum trägt einen Weg zur Steigleitung — die Leitungslänge ist "
            "unbekannt, nicht 0 m."
        )
    else:
        hinweise.append(
            "Leitungslänge ist eine SCHÄTZUNG: 2 × Weg zur Steigleitung (Vor- und "
            "Rücklauf). Ohne Formstücke, ohne Steigstrang."
        )
        if ohne_steig:
            hinweise.append(
                f"{ohne_steig} Raum/Räume ohne Weg zur Steigleitung — die Schätzung "
                "deckt sie nicht ab."
            )

    return {
        "raeume_anzahl": len(raeume),
        # Die WIRKSAMEN Auslegungsdaten (Objektwerte, ggf. übersteuert) — damit
        # das Panel sie anzeigen und sein Formular vorbelegen kann.
        "design_outdoor_temp_c": vorgabe.aussentemperatur_c,
        "heat_load_w_per_m2": vorgabe.kennwert_w_m2,
        "flaeche_m2": _q(flaeche, _M2),
        "volumen_m3": _q(volumen, _M2),
        # `None` = unbekannt (kein Raum trägt den Wert), nicht 0.
        "umfang_m": _q(umfang, _M2),
        "heizlast_kennwert_w": None if kennwert_unbekannt else _q(summe_kennwert, _W),
        "heizlast_huellflaeche_w": None if huelle_unbekannt else _q(summe_huelle, _W),
        "unbekannt_raeume": unbekannt,
        "leitungslaenge_schaetzung_m": leitung,
        "raeume_ohne_steigleitung": ohne_steig,
        "hinweise": hinweise,
    }
