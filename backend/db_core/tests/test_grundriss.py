"""Grundriss: wer zeichnet, misst nicht doppelt — und der Raum, der stillgelegt wird.

Zwei Haltungen werden hier scharf geprüft:

**1. Der Umriss ist die Quelle, nicht eine zweite Meinung.**  Hat ein Raum ein
Polygon, rechnet der Server Fläche und Umfang daraus und **verwirft** einen vom
Client mitgeschickten Wert. Sonst stünden zwei Zahlensätze nebeneinander, die
beide die Wahrheit behaupten und im Lauf eines Projekts auseinanderlaufen —
dieselbe Falle, gegen die `planned_quantity` im Baustellenbericht abgesichert ist.

Die Rechnung selbst hat drei Klippen, und jede hat hier ihren Test:
* der **Umlaufsinn** darf keine negative Fläche erzeugen (Trapezformel → Betrag),
* ein **entartetes** Polygon (alle Punkte auf einer Linie) hat keine Fläche,
* ein **überschlagenes** Polygon liefert eine sinnlose Fläche (die Teilflächen
  heben sich auf) — es muss vor der Rechnung abgefangen werden, nicht danach.

**2. Fehlende Lage heißt unbekannt, nicht „bei 0 m".**  Eine Öffnung ohne
`position_m` bleibt gültig und zählt voll in Fläche und Heizlast — sie wird nur
nicht gezeichnet. Sie darf niemals stillschweigend an den Kantenanfang rutschen.
"""
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from db_core.models import RoomOpening, RoomSurface, RoomVertex
from db_core.services import property as property_service
from db_core.services import raum as raum_service

# 5 m × 4 m, im Uhrzeigersinn gezeichnet (Millimeter).
RECHTECK = [
    {"x_mm": 0, "y_mm": 0},
    {"x_mm": 5000, "y_mm": 0},
    {"x_mm": 5000, "y_mm": 4000},
    {"x_mm": 0, "y_mm": 4000},
]
RECHTECK_GEGEN_UZS = list(reversed(RECHTECK))


def _property(actor):
    prop = property_service.create_property(
        actor.id, name="Grundriss-Objekt", property_type="EINFAMILIENHAUS",
        street="Feldweg", house_number="3", postal_code="10115", city="Berlin",
    )
    return raum_service.set_auslegung(
        actor.id, prop.id, {"design_outdoor_temp_c": Decimal("-12.0")}
    )


def _raum(actor, prop, **kwargs):
    daten = {
        "name": "Wohnzimmer",
        "floor_area_m2": Decimal("99.000"),   # bewusst falsch: der Umriss gewinnt
        "room_height_m": Decimal("2.500"),
        "indoor_temp_c": Decimal("20.0"),
        "air_change_rate": Decimal("0.50"),
    }
    daten.update(kwargs)
    return raum_service.create_room(actor.id, prop.id, daten)


# --- Die Rechnung ----------------------------------------------------------

@pytest.mark.django_db
def test_rechteck_flaeche_und_umfang(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)

    room = raum_service.set_grundriss(app_user.id, room.id, RECHTECK)

    assert room.floor_area_m2 == Decimal("20.000")
    assert room.perimeter_m == Decimal("18.000")
    # volume_m3 ist GENERATED und zieht nach: 20 × 2,5.
    assert room.volume_m3 == Decimal("50.000")
    assert [(v.idx, v.x_mm, v.y_mm) for v in room.vertices.all()] == [
        (0, 0, 0), (1, 5000, 0), (2, 5000, 4000), (3, 0, 4000)
    ]


@pytest.mark.django_db
def test_umlaufsinn_erzeugt_keine_negative_flaeche(app_user):
    """Gegen den Uhrzeigersinn ist dieselbe Fläche — nicht ihr Negativ.

    Ohne den Betrag in der Trapezformel käme −20 m² heraus; die Spalte hat einen
    CHECK `> 0`, das Ergebnis wäre ein 500er statt einer Zahl.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_grundriss(app_user.id, room.id, RECHTECK_GEGEN_UZS)
    assert room.floor_area_m2 == Decimal("20.000")
    assert room.perimeter_m == Decimal("18.000")


@pytest.mark.django_db
def test_l_form(app_user):
    """L-Form: Schenkel 6×2 m + Schenkel 2×2 m = 16 m²; Umfang 6+2+4+2+2+4 = 20 m."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    l_form = [
        {"x_mm": 0, "y_mm": 0},
        {"x_mm": 6000, "y_mm": 0},
        {"x_mm": 6000, "y_mm": 2000},
        {"x_mm": 2000, "y_mm": 2000},
        {"x_mm": 2000, "y_mm": 4000},
        {"x_mm": 0, "y_mm": 4000},
    ]
    room = raum_service.set_grundriss(app_user.id, room.id, l_form)
    assert room.floor_area_m2 == Decimal("16.000")
    assert room.perimeter_m == Decimal("20.000")


# --- Der Client-Wert wird verworfen ----------------------------------------

@pytest.mark.django_db
def test_client_flaeche_wird_bei_umriss_verworfen(app_user):
    """Wer zeichnet, misst nicht doppelt: PATCH kann die Fläche nicht überschreiben."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)

    room = raum_service.update_room(app_user.id, room.id, {
        "floor_area_m2": Decimal("99.000"),
        "perimeter_m": Decimal("3.000"),
        "name": "Wohnzimmer neu",      # das hier darf sehr wohl durch
    })
    assert room.floor_area_m2 == Decimal("20.000"), "Der Client-Wert hat gewonnen."
    assert room.perimeter_m == Decimal("18.000")
    assert room.name == "Wohnzimmer neu"


@pytest.mark.django_db
def test_ohne_umriss_bleibt_handeingabe(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.update_room(app_user.id, room.id, {
        "floor_area_m2": Decimal("31.500"), "perimeter_m": Decimal("23.000"),
    })
    assert room.floor_area_m2 == Decimal("31.500")
    assert room.perimeter_m == Decimal("23.000")


@pytest.mark.django_db
def test_geometrie_quelle(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    assert raum_service.kennzahlen(room)["geometrie_quelle"] == "EINGEGEBEN"

    room = raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    assert raum_service.kennzahlen(room)["geometrie_quelle"] == "GEZEICHNET"

    room = raum_service.set_grundriss(app_user.id, room.id, [])
    assert raum_service.kennzahlen(room)["geometrie_quelle"] == "EINGEGEBEN"
    # Die zuletzt gerechneten Werte bleiben stehen — sie sind ja gemessen.
    assert room.floor_area_m2 == Decimal("20.000")


# --- Entartete Umrisse → 422, nie 500 --------------------------------------

@pytest.mark.django_db
def test_zu_wenige_punkte(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError, match="mindestens 3 Punkte"):
        raum_service.set_grundriss(app_user.id, room.id, RECHTECK[:2])


@pytest.mark.django_db
def test_dublettenpunkt(app_user):
    """Die DB hat dafür einen UNIQUE — der Service fängt ihn vorher ab."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError, match="aufeinanderliegen"):
        raum_service.set_grundriss(app_user.id, room.id, RECHTECK + [
            {"x_mm": 0, "y_mm": 0},
        ])


@pytest.mark.django_db
def test_kollineare_punkte(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError, match="umschließt keine Fläche"):
        raum_service.set_grundriss(app_user.id, room.id, [
            {"x_mm": 0, "y_mm": 0},
            {"x_mm": 1000, "y_mm": 0},
            {"x_mm": 3000, "y_mm": 0},
        ])


@pytest.mark.django_db
def test_selbstschnitt(app_user):
    """Überschlagenes Polygon: die Trapezformel liefert dafür eine sinnlose Fläche.

    Dieses Viereck hat eine *positive* Fläche (3 m²) — die Prüfung darf sich also
    nicht auf `Fläche > 0` verlassen, sondern muss die Kanten wirklich schneiden.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError, match="überschlägt sich"):
        raum_service.set_grundriss(app_user.id, room.id, [
            {"x_mm": 0, "y_mm": 0},
            {"x_mm": 4000, "y_mm": 0},
            {"x_mm": 1000, "y_mm": 3000},
            {"x_mm": 3000, "y_mm": 3000},
        ])
    assert not RoomVertex.objects.filter(room_id=room.id).exists()


# --- Wand auf der Kante ----------------------------------------------------

@pytest.mark.django_db
def test_wandflaeche_aus_kantenlaenge_mal_hoehe(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)

    room = raum_service.set_aufbau(app_user.id, room.id, [
        # Kante 0 = 5 m → 5 × 2,5 = 12,5 m²
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 0, "u_value": Decimal("0.800")},
        # Kante 1 = 4 m, aber Giebel: der mitgeschickte Wert GEWINNT.
        {"ref": "s2", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 1, "gross_area_m2": Decimal("7.500"),
         "u_value": Decimal("0.800")},
    ], [])

    je_ref = {s.edge_index: s for s in room.surfaces.all()}
    assert je_ref[0].gross_area_m2 == Decimal("12.500")
    assert je_ref[1].gross_area_m2 == Decimal("7.500")


@pytest.mark.django_db
def test_kante_gibt_es_nicht(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    with pytest.raises(ValueError, match="Kante 4 gibt es nicht"):
        raum_service.set_aufbau(app_user.id, room.id, [
            {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
             "edge_index": 4, "u_value": Decimal("0.800")},
        ], [])


@pytest.mark.django_db
def test_zwei_waende_auf_derselben_kante(app_user):
    """Sonst zählte dieselbe Fläche doppelt in die Heizlast (uq_room_surface_edge)."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    with pytest.raises(ValueError, match="mehr als eine Wand"):
        raum_service.set_aufbau(app_user.id, room.id, [
            {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
             "edge_index": 0, "u_value": Decimal("0.800")},
            {"ref": "s2", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
             "edge_index": 0, "u_value": Decimal("0.800")},
        ], [])


# --- Eine Kante trägt nur eine WAND (0094) ---------------------------------
#
# Der Umriss ist die DRAUFSICHT: Seine Kanten sind die senkrechten Bauteile. Ohne
# diese Grenze bekam eine Decke die Fläche „Kantenlänge × Raumhöhe" (5,00 × 2,50 =
# 12,50 m² statt ihrer 20 m²) — und wuchs als abgeleitete Fläche fortan mit der
# Raumhöhe mit. Eine Decke, die größer wird, wenn der Raum höher wird.

@pytest.mark.django_db
@pytest.mark.parametrize("typ", ["DECKE", "BODEN", "DACHSCHRAEGE"])
def test_kante_traegt_keine_decke(app_user, typ):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    with pytest.raises(ValueError, match="kann nicht auf einer Kante"):
        raum_service.set_aufbau(app_user.id, room.id, [
            {"ref": "s1", "surface_type": typ, "adjacent": "UNBEHEIZT",
             "edge_index": 0, "temp_factor": Decimal("0.50")},
        ], [])
    # Auch NICHT, wenn eine Fläche mitkommt: Der `edge_index` selbst ist der
    # Fehler — er markierte die Zeile sonst als „steht auf einer Kante", und der
    # nächste Umriss/Höhenwechsel griffe sie an.
    with pytest.raises(ValueError, match="kann nicht auf einer Kante"):
        raum_service.set_aufbau(app_user.id, room.id, [
            {"ref": "s1", "surface_type": typ, "adjacent": "UNBEHEIZT",
             "edge_index": 0, "gross_area_m2": Decimal("20.000"),
             "temp_factor": Decimal("0.50")},
        ], [])


@pytest.mark.django_db
def test_decke_ohne_kante_bleibt_gueltig(app_user):
    """Die Gegenprobe: Decke, Boden und Dachschräge sind ganz normale Bauteile —
    sie beziehen sich nur auf die Grundfläche, nicht auf eine Kante."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    room = raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 0, "u_value": Decimal("0.800")},
        {"ref": "s2", "surface_type": "DECKE", "adjacent": "UNBEHEIZT",
         "gross_area_m2": Decimal("20.000"), "u_value": Decimal("0.300"),
         "temp_factor": Decimal("0.50")},
    ], [])
    decke = [s for s in room.surfaces.all() if s.surface_type == "DECKE"][0]
    assert decke.edge_index is None
    assert decke.area_is_derived is False
    assert decke.gross_area_m2 == Decimal("20.000")   # die volle Grundfläche


@pytest.mark.django_db
def test_kantenwand_nachtraeglich_zur_decke_umwidmen(app_user):
    """Eine Wand auf einer Kante später zur DECKE machen (und die Kante behalten)
    ist derselbe Fehler — der Service muss ihn ebenso abweisen."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 0, "u_value": Decimal("0.800")},
    ], [])
    with pytest.raises(ValueError, match="kann nicht auf einer Kante"):
        raum_service.set_aufbau(app_user.id, room.id, [
            {"ref": "s1", "surface_type": "DECKE", "adjacent": "UNBEHEIZT",
             "edge_index": 0, "u_value": Decimal("0.300"),
             "temp_factor": Decimal("0.50")},
        ], [])
    # Der alte Stand steht unverändert (die Transaktion ist zurückgerollt).
    wand = RoomSurface.objects.get(room_id=room.id)
    assert wand.surface_type == "AUSSENWAND"
    assert wand.edge_index == 0


@pytest.mark.django_db
def test_innenwand_darf_auf_der_kante_stehen(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    room = raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "INNENWAND", "adjacent": "UNBEHEIZT",
         "edge_index": 2, "u_value": Decimal("1.200"),
         "temp_factor": Decimal("0.50")},
    ], [])
    wand = RoomSurface.objects.get(room_id=room.id)
    assert wand.edge_index == 2
    assert wand.area_is_derived is True
    assert wand.gross_area_m2 == Decimal("12.500")    # Kante 2 = 5 m × 2,5 m


# --- Öffnung in der Kante --------------------------------------------------

def _wand_mit_fenster(pos):
    return (
        [{"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
          "edge_index": 0, "u_value": Decimal("0.800")}],
        [{"surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
          "width_m": Decimal("1.500"), "height_m": Decimal("1.200"),
          "u_value": Decimal("1.300"), "position_m": pos}],
    )


@pytest.mark.django_db
def test_oeffnung_passt_in_die_kante(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)

    flaechen, oeffnungen = _wand_mit_fenster(Decimal("3.500"))  # 3,5 + 1,5 = 5,0 m
    room = raum_service.set_aufbau(app_user.id, room.id, flaechen, oeffnungen)
    assert list(room.openings.all())[0].position_m == Decimal("3.500")


@pytest.mark.django_db
def test_oeffnung_passt_nicht_in_die_kante(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)

    flaechen, oeffnungen = _wand_mit_fenster(Decimal("4.000"))  # 4,0 + 1,5 > 5,0 m
    with pytest.raises(ValueError, match="passt nicht in ihre Kante"):
        raum_service.set_aufbau(app_user.id, room.id, flaechen, oeffnungen)


@pytest.mark.django_db
def test_position_ohne_wand(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    with pytest.raises(ValueError, match="ohne Wand"):
        raum_service.set_aufbau(app_user.id, room.id, [], [{
            "surface_ref": None, "opening_type": "FENSTER", "quantity": 1,
            "width_m": Decimal("1.000"), "height_m": Decimal("1.000"),
            "position_m": Decimal("0.500"),
        }])


@pytest.mark.django_db
def test_oeffnung_ohne_position_bleibt_gueltig_und_zaehlt(app_user):
    """Fehlende Lage heißt unbekannt — NICHT „bei 0 m", und schon gar nicht „zählt nicht".

    Die Öffnung geht voll in Fläche und Heizlast ein; sie wird nur nicht gezeichnet.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)

    room = raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 0, "u_value": Decimal("0.800")},
    ], [
        {"surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
         "width_m": Decimal("2.000"), "height_m": Decimal("1.000"),
         "u_value": Decimal("1.300")},
    ])

    o = list(room.openings.all())[0]
    assert o.position_m is None, "Die Lage wurde auf 0 gesetzt — sie ist UNBEKANNT."

    k = raum_service.kennzahlen(room)
    # Wand 12,5 m² brutto − 2,0 m² Fenster = 10,5 m² netto.
    # 0,8 × 10,5 × 1,0 × 32 = 268,8 W  +  1,3 × 2,0 × 1,0 × 32 = 83,2 W  → 352,0 W
    assert k["opening_area_m2"] == Decimal("2.000")
    assert k["transmission_w"] == Decimal("352.0")


# --- Die abgeleitete Fläche weiß, dass sie abgeleitet ist (0093) ------------
#
# Der stille Fehler, gegen den 0093 gebaut ist: Eine gerechnete Wandfläche erstarrt
# auf dem alten Stand, wenn sich Raumhöhe oder Umriss ändern. Die Heizlast rechnet
# weiter mit 2,50 m, obwohl der Raum 2,80 m hoch ist — und es sieht völlig normal
# aus. Die Gegenprobe steht jeweils daneben: die Handeingabe (Giebel) darf sich
# NICHT bewegen.

def _kanten_und_giebel(actor, room):
    """Kante 0 (5 m) abgeleitet, Kante 1 (4 m) als Giebel von Hand eingetragen."""
    return raum_service.set_aufbau(actor.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 0, "u_value": Decimal("0.800")},
        {"ref": "s2", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 1, "gross_area_m2": Decimal("7.500"),
         "u_value": Decimal("0.800")},
    ], [])


@pytest.mark.django_db
def test_abgeleitet_wird_markiert(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    room = _kanten_und_giebel(app_user, room)

    je_kante = {s.edge_index: s for s in room.surfaces.all()}
    assert je_kante[0].area_is_derived is True     # gerechnet: 5,0 × 2,5
    assert je_kante[0].gross_area_m2 == Decimal("12.500")
    assert je_kante[1].area_is_derived is False    # Handeingabe: Giebel
    assert je_kante[1].gross_area_m2 == Decimal("7.500")


@pytest.mark.django_db
def test_raumhoehe_zieht_die_abgeleitete_wandflaeche_mit(app_user):
    """DER Fund: 2,50 → 2,80 m, und die Heizlast folgt.

    Ohne das Nachrechnen bliebe die Wand bei 12,5 m² (2,50 m) stehen, die Heizlast
    sähe völlig normal aus — und wäre falsch.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    room = _kanten_und_giebel(app_user, room)

    # Vorher: (12,5 + 7,5) m² × 0,8 × 1,0 × 32 K = 512,0 W
    vorher = raum_service.kennzahlen(room)
    assert vorher["transmission_w"] == Decimal("512.0")

    room = raum_service.update_room(
        app_user.id, room.id, {"room_height_m": Decimal("2.800")}
    )

    je_kante = {s.edge_index: s for s in room.surfaces.all()}
    # 5,0 m × 2,8 m = 14,0 m² — die abgeleitete Wand ist mitgewachsen.
    assert je_kante[0].gross_area_m2 == Decimal("14.000")
    # Der Giebel bleibt, wie er eingetragen wurde. Nie überschreiben.
    assert je_kante[1].gross_area_m2 == Decimal("7.500")

    # Und die Heizlast folgt: (14,0 + 7,5) × 0,8 × 32 = 550,4 W
    nachher = raum_service.kennzahlen(raum_service.get_room(room.id))
    assert nachher["transmission_w"] == Decimal("550.4")
    assert nachher["wall_area_gross_m2"] == Decimal("21.500")


@pytest.mark.django_db
def test_neuer_umriss_zieht_die_abgeleitete_wandflaeche_mit(app_user):
    """Kante 1: 4,00 m → 4,37 m. Die abgeleitete Fläche folgt, die Handeingabe nicht."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    # Beide Wände abgeleitet — hier interessiert die zweite (Kante 1 = 4 m).
    room = raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 1, "u_value": Decimal("0.800")},
        {"ref": "s2", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 2, "gross_area_m2": Decimal("7.500"),
         "u_value": Decimal("0.800")},
    ], [])
    je_kante = {s.edge_index: s for s in room.surfaces.all()}
    assert je_kante[1].gross_area_m2 == Decimal("10.000")   # 4,0 × 2,5

    # Nachgezeichnet: die rechte Kante ist jetzt 4,37 m statt 4,00 m.
    room = raum_service.set_grundriss(app_user.id, room.id, [
        {"x_mm": 0, "y_mm": 0},
        {"x_mm": 5000, "y_mm": 0},
        {"x_mm": 5000, "y_mm": 4370},
        {"x_mm": 0, "y_mm": 4370},
    ])

    je_kante = {s.edge_index: s for s in room.surfaces.all()}
    # 4,37 m × 2,5 m = 10,925 m²
    assert je_kante[1].gross_area_m2 == Decimal("10.925")
    assert je_kante[1].area_is_derived is True
    # Die Handeingabe auf Kante 2 bleibt unberührt (sie ist die Kante, die sich
    # gar nicht geändert hat — aber sie dürfte sich auch dann nicht bewegen).
    assert je_kante[2].gross_area_m2 == Decimal("7.500")
    assert je_kante[2].area_is_derived is False


@pytest.mark.django_db
def test_neuberechnung_unter_die_fenster_ist_422_kein_500(app_user):
    """Schrumpft die neu gerechnete Wand unter ihre Fenster, greift der Trigger —
    und der Bediener bekommt eine Meldung, die die Wand benennt."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    # Kante 0 = 5 m × 2,5 m = 12,5 m², darin ein Fenster von 12,0 m².
    raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 0, "label": "Südwand", "u_value": Decimal("0.800")},
    ], [
        {"surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
         "width_m": Decimal("4.800"), "height_m": Decimal("2.500"),
         "u_value": Decimal("1.300")},
    ])

    # Raumhöhe auf 2,0 m → die Wand wäre nur noch 10,0 m² groß. Das Fenster passt
    # nicht mehr hinein.
    with pytest.raises(ValueError) as exc:
        raum_service.update_room(
            app_user.id, room.id, {"room_height_m": Decimal("2.000")}
        )
    assert "Südwand" in str(exc.value)
    assert "passen nicht mehr hinein" in str(exc.value)

    # Nichts ist geschrieben worden — die Transaktion ist zurückgerollt.
    room = raum_service.get_room(room.id)
    assert room.room_height_m == Decimal("2.500")
    assert RoomSurface.objects.get(room_id=room.id).gross_area_m2 == Decimal("12.500")


@pytest.mark.django_db
def test_handeingabe_wird_von_der_hoehe_nicht_angefasst(app_user):
    """Eine Wand ohne Kante (Decke, oder von Hand angelegt) bleibt, wie sie ist."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "DECKE", "adjacent": "UNBEHEIZT",
         "gross_area_m2": Decimal("20.000"), "u_value": Decimal("0.300"),
         "temp_factor": Decimal("0.50")},
    ], [])
    assert list(room.surfaces.all())[0].area_is_derived is False

    room = raum_service.update_room(
        app_user.id, room.id, {"room_height_m": Decimal("2.800")}
    )
    assert list(room.surfaces.all())[0].gross_area_m2 == Decimal("20.000")


# --- Umriss verkleinern/entfernen ------------------------------------------

@pytest.mark.django_db
def test_verkleinerter_umriss_loest_verwaiste_kanten(app_user):
    """Ein edge_index, den es nicht mehr gibt, wird NULL — kein 422.

    Ein 422 sperrte den Bediener aus: Er müsste erst jede Wand entkoppeln, bevor
    er neu zeichnen darf. NULL ist die Wahrheit (die Wand steht auf keiner
    bekannten Kante mehr) und folgenlos für die Heizlast — die Bruttofläche bleibt.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 3, "u_value": Decimal("0.800")},
    ], [
        {"surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
         "width_m": Decimal("1.000"), "height_m": Decimal("1.000"),
         "u_value": Decimal("1.300"), "position_m": Decimal("1.000")},
    ])

    # Dreieck: es gibt nur noch die Kanten 0..2 — Kante 3 fällt weg.
    raum_service.set_grundriss(app_user.id, room.id, [
        {"x_mm": 0, "y_mm": 0},
        {"x_mm": 5000, "y_mm": 0},
        {"x_mm": 0, "y_mm": 4000},
    ])

    wand = RoomSurface.objects.get(room_id=room.id)
    assert wand.edge_index is None
    # Ohne Kante gibt es nichts abzuleiten — das Flag MUSS mitfallen (CHECK aus
    # 0093). Die Fläche bleibt stehen und gilt ab jetzt als Handeingabe.
    assert wand.area_is_derived is False
    assert wand.gross_area_m2 == Decimal("10.000")   # 4 m × 2,5 m, unverändert
    fenster = RoomOpening.objects.get(room_id=room.id)
    assert fenster.position_m is None                # zeigte ins Leere
    assert fenster.area_m2 == Decimal("1.000")       # zählt weiter voll mit


@pytest.mark.django_db
def test_umriss_entfernen(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 0, "u_value": Decimal("0.800")},
    ], [])

    room = raum_service.set_grundriss(app_user.id, room.id, [])
    assert not RoomVertex.objects.filter(room_id=room.id).exists()
    wand = RoomSurface.objects.get(room_id=room.id)
    assert wand.edge_index is None
    assert wand.area_is_derived is False

    # Und die Fläche ist wieder Handeingabe.
    room = raum_service.update_room(
        app_user.id, room.id, {"floor_area_m2": Decimal("22.000")}
    )
    assert room.floor_area_m2 == Decimal("22.000")


# --- Serialisierung: die Raumzeile ist der Sperrpunkt -----------------------

@pytest.mark.django_db
def test_schreibwege_nehmen_die_raumsperre(app_user):
    """Jeder Schreibweg am Aufmaß sperrt die Raumzeile — ausdrücklich, nicht zufällig.

    Bisher entstand die Sperre nur als **Nebenwirkung** von
    `enforce_room_opening_fits`, und der Trigger feuert nicht immer: Ein
    `set_aufbau` ohne Öffnungen fasste die Raumzeile nie an. Damit konnte ein
    `set_aufbau` eine Wand auf eine Kante setzen, die ein gleichzeitiges
    `set_grundriss` gerade abschaffte — eine Wand auf einer Kante, die es nicht
    gibt, mit `area_is_derived = true`, die danach **still** nie wieder
    nachgerechnet wird. Kein CHECK verletzt, also meldet es niemand.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop)

    def _sperrt(fn):
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return any(
            "FOR UPDATE" in q["sql"] and "room" in q["sql"] for q in ctx.captured_queries
        )

    # 1. set_grundriss
    assert _sperrt(
        lambda: raum_service.set_grundriss(app_user.id, room.id, RECHTECK)
    )
    # 2. set_aufbau — der Fall OHNE Öffnungen, der den Trigger gerade NICHT auslöst.
    assert _sperrt(lambda: raum_service.set_aufbau(app_user.id, room.id, [
        {"ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
         "edge_index": 0, "u_value": Decimal("0.800")},
    ], []))
    # 3. update_room (Höhenänderung rechnet abgeleitete Flächen nach).
    assert _sperrt(lambda: raum_service.update_room(
        app_user.id, room.id, {"room_height_m": Decimal("2.700")}
    ))


# --- Raum stilllegen -------------------------------------------------------

@pytest.mark.django_db
def test_stillgelegter_raum_faellt_aus_liste_und_summen(app_user):
    """Gelöscht wird nie (No-Delete) — aber ein weggefallener Raum darf die
    Gebäudesumme nicht mehr aufblähen."""
    prop = _property(app_user)
    a = _raum(app_user, prop, name="Bleibt", floor_area_m2=Decimal("20.000"))
    b = _raum(app_user, prop, name="Weggefallen", floor_area_m2=Decimal("10.000"))

    summe = raum_service.aufmass_property(prop.id)
    assert summe["raeume_anzahl"] == 2
    assert summe["flaeche_m2"] == Decimal("30.000")

    raum_service.update_room(app_user.id, b.id, {"status": "INAKTIV"})

    assert [r.id for r in raum_service.list_rooms(prop.id)] == [a.id]
    assert {r.id for r in raum_service.list_rooms(prop.id, mit_inaktiven=True)} == {
        a.id, b.id
    }
    # Einzeln bleibt er abrufbar (sonst wäre er nicht reaktivierbar).
    assert raum_service.get_room(b.id).status == "INAKTIV"

    summe = raum_service.aufmass_property(prop.id)
    assert summe["raeume_anzahl"] == 1
    assert summe["flaeche_m2"] == Decimal("20.000")
