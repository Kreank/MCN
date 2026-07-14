"""Raumaufmaß: Geometrie, die Fenster-passt-in-die-Wand-Invariante, Heizlast.

Der Kern dieses Slices ist eine einzige Haltung, und sie wird hier scharf
geprüft:

    **Unbestimmt ist NICHT null.**

Fehlt ein U-Wert, ein Temperaturkorrekturfaktor, die Innen-/Außentemperatur oder
die Luftwechselrate, ist die Heizlast **unbekannt** — nie 0 W. Ein fehlender
U-Wert als 0 gelesen hieße „diese Wand verliert keine Wärme"; der Heizkörper
fiele zu klein aus und niemand sähe den Fehler. Die Tests `test_heizlast_*`
belegen deshalb nicht nur, dass ein `None` kommt, sondern dass der Rechner
**benennt, welche Fläche ihm fehlt**.

Die Gegenprobe steht daneben: eine Fläche gegen einen **beheizten** Nachbarraum
trägt definitionsgemäß **0 W** bei — ganz ohne U-Wert. Das ist kein „unbekannt",
sondern Physik (kein Temperaturgefälle). Wer beides verwechselt, hat entweder
einen Rechner, der schweigt, wo er rechnen könnte, oder einen, der rechnet, wo
er schweigen müsste.

Die DB-Trigger werden bewusst **am Service vorbei** geprüft (`test_trigger_*`):
`property.enforce_room_opening_fits` muss beide Richtungen sperren — das zu große
Fenster UND die nachträglich geschrumpfte Wand; seit 0089 zusätzlich raumweit
(Σ Öffnungen ≤ Σ Bauteilflächen).

Die zweite Haltung prüfen die `test_auslegung_*`: Die Auslegungsdaten hängen an
der **Liegenschaft** (0089), nicht am Aufruf — sonst rechnet die Anwendung nie.
"""
import uuid
from decimal import Decimal

import pytest
from django.db import Error, connection

from db_core.db_context import business_transaction
from db_core.models import Property, Room, RoomOpening, RoomSurface
from db_core.services import property as property_service
from db_core.services import raum as raum_service


# --- Hilfen ----------------------------------------------------------------

def _property(actor, name="Aufmaß-Objekt"):
    return property_service.create_property(
        actor.id, name=name, property_type="EINFAMILIENHAUS",
        street="Feldweg", house_number="3", postal_code="10115", city="Berlin",
    )


def _raum(actor, prop, **kwargs):
    daten = {
        "name": "Wohnzimmer",
        "floor_area_m2": Decimal("20.000"),
        "room_height_m": Decimal("2.500"),
    }
    daten.update(kwargs)
    return raum_service.create_room(actor.id, prop.id, daten)


def _wand(ref="s1", **kwargs):
    daten = {
        "ref": ref, "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
        "gross_area_m2": Decimal("10.000"), "u_value": Decimal("0.800"),
    }
    daten.update(kwargs)
    return daten


def _fenster(surface_ref="s1", **kwargs):
    daten = {
        "surface_ref": surface_ref, "opening_type": "FENSTER", "quantity": 1,
        "width_m": Decimal("1.000"), "height_m": Decimal("1.000"),
        "u_value": Decimal("1.300"),
    }
    daten.update(kwargs)
    return daten


# --- Generierte Spalten ----------------------------------------------------

@pytest.mark.django_db
def test_volumen_ist_generiert(app_user):
    """volume_m3 = Fläche × Höhe ist eine Definition, keine Ermessensfrage —
    die DB rechnet, kein Client kann ein inkonsistentes Volumen schreiben."""
    prop = _property(app_user)
    room = _raum(app_user, prop, floor_area_m2=Decimal("24.500"),
                 room_height_m=Decimal("2.600"))
    assert room.volume_m3 == Decimal("63.700")

    # Und sie zieht nach, wenn die Höhe sich ändert.
    room = raum_service.update_room(
        app_user.id, room.id, {"room_height_m": Decimal("3.000")}
    )
    assert room.volume_m3 == Decimal("73.500")


@pytest.mark.django_db
def test_oeffnungsflaeche_ist_generiert(app_user):
    """area_m2 = Menge × Breite × Höhe — ebenfalls DB-seitig."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand()],
        [_fenster(quantity=2, width_m=Decimal("1.200"), height_m=Decimal("1.500"))],
    )
    assert room.openings.all()[0].area_m2 == Decimal("3.600")


# --- Trigger: das Fenster passt in seine Wand (beide Richtungen) -----------

@pytest.mark.django_db
def test_trigger_fenster_groesser_als_wand(app_user):
    """Am Service vorbei: der Trigger selbst muss das zu große Fenster sperren."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_aufbau(
        app_user.id, room.id, [_wand(gross_area_m2=Decimal("6.000"))], []
    )
    wand = room.surfaces.all()[0]

    with pytest.raises(Error) as exc:
        with business_transaction(app_user.id):
            with connection.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO property.room_opening
                        (id, room_id, surface_id, opening_type, quantity,
                         width_m, height_m)
                    VALUES (%s, %s, %s, 'FENSTER', 1, 3, 3)
                    """,
                    [str(uuid.uuid4()), str(room.id), str(wand.id)],
                )
    # 9 m² Fenster in 6 m² Wand → negative Nettowandfläche, die Wand GEWÖNNE Wärme.
    assert "größer als die Fläche" in str(exc.value)


@pytest.mark.django_db
def test_trigger_wand_schrumpft_unter_ihre_fenster(app_user):
    """Die Gegenrichtung: sonst schrumpfte man die Wand einfach unter ihr Fenster."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand(gross_area_m2=Decimal("10.000"))],
        [_fenster(width_m=Decimal("2.000"), height_m=Decimal("2.000"))],  # 4 m²
    )
    wand = room.surfaces.all()[0]

    with pytest.raises(Error) as exc:
        with business_transaction(app_user.id):
            RoomSurface.objects.filter(id=wand.id).update(
                gross_area_m2=Decimal("3.000")
            )
    assert "größer als die Fläche" in str(exc.value)

    # 4 m² Restfläche geht — die Wand darf bis auf das Fenster schrumpfen.
    with business_transaction(app_user.id):
        RoomSurface.objects.filter(id=wand.id).update(gross_area_m2=Decimal("4.000"))
    assert RoomSurface.objects.get(id=wand.id).gross_area_m2 == Decimal("4.000")


@pytest.mark.django_db
def test_service_uebersetzt_trigger_in_valueerror(app_user):
    """Der Trigger-Fehler ist ein Bedienfehler (→ 422), kein 500."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError) as exc:
        raum_service.set_aufbau(
            app_user.id, room.id,
            [_wand(gross_area_m2=Decimal("2.000"), label="Giebelwand")],
            [_fenster(width_m=Decimal("2.000"), height_m=Decimal("2.000"))],
        )
    meldung = str(exc.value)
    assert "Giebelwand" in meldung
    assert "nie größer als ihre Fläche" in meldung


# --- Schutzstandard --------------------------------------------------------

@pytest.mark.django_db
def test_raum_kann_nicht_geloescht_werden(app_user):
    """No-Delete: ein aufgenommener Raum wird INAKTIV, nicht gelöscht — das
    Aufmaß ist ein Nachweis über den Bestand."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            Room.objects.filter(id=room.id).delete()
    assert Room.objects.filter(id=room.id).exists()

    # Der vorgesehene Weg:
    room = raum_service.update_room(app_user.id, room.id, {"status": "INAKTIV"})
    assert room.status == "INAKTIV"


@pytest.mark.django_db
def test_dublettensperre(app_user):
    """Denselben Raum zweimal aufnehmen ist der häufigste Fehler einer Begehung.

    NULLS NOT DISTINCT: die Sperre greift auch ohne Einheit und ohne Geschoss —
    dem Regelfall im Einfamilienhaus.
    """
    prop = _property(app_user)
    _raum(app_user, prop, name="Bad", storey=None)
    with pytest.raises(ValueError) as exc:
        _raum(app_user, prop, name="Bad", storey=None)
    assert "bereits ein Raum" in str(exc.value)

    # Dasselbe Namensschild in einem anderen Geschoss ist kein Duplikat.
    zweites = _raum(app_user, prop, name="Bad", storey="1. OG")
    assert zweites.storey == "1. OG"


# --- set_aufbau: Satz-Ersetzung und surface_ref-Auflösung ------------------

@pytest.mark.django_db
def test_set_aufbau_loest_surface_ref_auf(app_user):
    """Der Client kennt keine UUIDs — er referenziert seine Wand über 'ref'."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [
            _wand("nord", label="Nordwand", orientation="N"),
            _wand("sued", label="Südwand", orientation="S",
                  gross_area_m2=Decimal("12.000")),
        ],
        [_fenster("sued", label="Südfenster")],
    )
    wände = {s.label: s for s in room.surfaces.all()}
    fenster = room.openings.all()[0]
    assert fenster.surface_id == wände["Südwand"].id
    assert fenster.surface_id != wände["Nordwand"].id


@pytest.mark.django_db
def test_set_aufbau_ersetzt_den_satz_atomar(app_user):
    """Delete+Insert als Satz — der Editor sortiert um und streicht Zeilen; ein
    Teil-Update wäre bei umsortierten Zeilen nicht eindeutig."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1"), _wand("s2", surface_type="INNENWAND", adjacent="BEHEIZT")],
        [_fenster("s1"), _fenster("s1", label="Zweites")],
    )
    assert RoomSurface.objects.filter(room_id=room.id).count() == 2
    assert RoomOpening.objects.filter(room_id=room.id).count() == 2

    room = raum_service.set_aufbau(
        app_user.id, room.id, [_wand("neu", label="Einzige")], []
    )
    assert RoomSurface.objects.filter(room_id=room.id).count() == 1
    assert RoomOpening.objects.filter(room_id=room.id).count() == 0
    assert room.surfaces.all()[0].label == "Einzige"


@pytest.mark.django_db
def test_set_aufbau_unbekannte_surface_ref(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError) as exc:
        raum_service.set_aufbau(
            app_user.id, room.id, [_wand("s1")], [_fenster("tippfehler")]
        )
    assert "unbekannte Hüllfläche" in str(exc.value)


@pytest.mark.django_db
def test_set_aufbau_oeffnung_ohne_wand_erlaubt(app_user):
    """surface_ref = None: reiner Mengenabzug ohne Bauteilzuordnung (Malerarbeiten)."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1")],
        [_fenster(surface_ref=None, u_value=None)],
    )
    assert room.openings.all()[0].surface_id is None


@pytest.mark.django_db
def test_set_aufbau_rollt_bei_fehler_zurueck(app_user):
    """Atomar heißt: schlägt die zweite Öffnung fehl, bleibt der ALTE Satz stehen."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    raum_service.set_aufbau(app_user.id, room.id, [_wand("alt", label="Bestand")], [])

    with pytest.raises(ValueError):
        raum_service.set_aufbau(
            app_user.id, room.id,
            [_wand("s1", gross_area_m2=Decimal("2.000"))],
            [_fenster("s1", width_m=Decimal("3.000"), height_m=Decimal("3.000"))],
        )

    flaechen = list(RoomSurface.objects.filter(room_id=room.id))
    assert len(flaechen) == 1
    assert flaechen[0].label == "Bestand"


# --- Heizlast: unbestimmt ist NICHT null -----------------------------------

@pytest.mark.django_db
def test_heizlast_huellflaeche_rechnet(app_user):
    """Der Referenzfall, gegen den die Null-Fälle sich messen lassen.

    Wand 10 m² brutto, U 0,8; Fenster 2 m², U 1,3; ΔT = 20 − (−10) = 30 K.
      Transmission = 0,8 × 8 × 1,0 × 30  +  1,3 × 2 × 1,0 × 30  =  192 + 78 = 270 W
      Lüftung      = 0,34 × 0,5 × 50 m³ × 30                    =  255 W
    """
    prop = _property(app_user)
    room = _raum(
        app_user, prop, floor_area_m2=Decimal("20.000"),
        room_height_m=Decimal("2.500"), indoor_temp_c=Decimal("20.0"),
        air_change_rate=Decimal("0.50"),
    )
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1", gross_area_m2=Decimal("10.000"), u_value=Decimal("0.800"))],
        [_fenster("s1", width_m=Decimal("2.000"), height_m=Decimal("1.000"),
                  u_value=Decimal("1.300"))],
    )
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["volume_m3"] == Decimal("50.000")
    assert k["wall_area_net_m2"] == Decimal("8.000")
    assert k["transmission_w"] == Decimal("270.0")
    assert k["lueftung_w"] == Decimal("255.0")
    assert k["heizlast_huellflaeche_w"] == Decimal("525.0")
    assert k["unbekannt_grund"] is None


@pytest.mark.django_db
def test_heizlast_fehlender_u_wert_ist_unbekannt_nicht_null(app_user):
    """INVARIANTE: fehlender U-Wert → None, und der Grund BENENNT die Fläche."""
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1", label="Giebelwand", u_value=None)],
        [],
    )
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["transmission_w"] is None
    assert k["heizlast_huellflaeche_w"] is None
    # NICHT 0 — das ist der ganze Punkt.
    assert k["transmission_w"] != Decimal(0)
    assert "Giebelwand" in k["unbekannt_grund"]
    assert "U-Wert" in k["unbekannt_grund"]
    # Die Lüftung ist davon unberührt: sie ist bekannt.
    assert k["lueftung_w"] is not None


@pytest.mark.django_db
def test_heizlast_fehlender_u_wert_am_fenster_ist_unbekannt(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1", label="Südwand")],
        [_fenster("s1", label="Terrassentür", u_value=None)],
    )
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["transmission_w"] is None
    assert "Terrassentür" in k["unbekannt_grund"]


@pytest.mark.django_db
def test_heizlast_fehlender_temp_factor_ist_unbekannt(app_user):
    """ERDREICH/UNBEHEIZT: der Temperaturkorrekturfaktor ist eine PFLICHTeingabe.

    MCN liefert keine f-Faktoren mit (Normrecht) — also muss der Rechner fragen,
    statt einen Wert zu erfinden.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1", surface_type="BODEN", adjacent="ERDREICH",
               label="Kellerboden", u_value=Decimal("0.400"), temp_factor=None)],
        [],
    )
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["transmission_w"] is None
    assert "Kellerboden" in k["unbekannt_grund"]
    assert "Temperaturkorrekturfaktor" in k["unbekannt_grund"]

    # Mit Faktor rechnet er: 0,4 × 10 × 0,5 × 30 = 60 W.
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1", surface_type="BODEN", adjacent="ERDREICH",
               label="Kellerboden", u_value=Decimal("0.400"),
               temp_factor=Decimal("0.50"))],
        [],
    )
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["transmission_w"] == Decimal("60.0")


@pytest.mark.django_db
def test_heizlast_fehlende_luftwechselrate_ist_unbekannt(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=None)
    room = raum_service.set_aufbau(app_user.id, room.id, [_wand("s1")], [])
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["lueftung_w"] is None
    assert k["lueftung_w"] != Decimal(0)
    assert "Luftwechselrate" in k["unbekannt_grund"]
    # Die Transmission ist bekannt — aber das GESAMTergebnis ist es nicht.
    assert k["transmission_w"] == Decimal("240.0")
    assert k["heizlast_huellflaeche_w"] is None


@pytest.mark.django_db
def test_heizlast_ohne_aussentemperatur_ist_unbekannt(app_user):
    """MCN liefert keine Klimadaten mit — die Auslegungstemperatur ist Eingabe."""
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(app_user.id, room.id, [_wand("s1")], [])
    k = raum_service.kennzahlen(room, None)
    assert k["transmission_w"] is None
    assert k["lueftung_w"] is None
    assert k["heizlast_huellflaeche_w"] is None
    assert "Außentemperatur" in k["unbekannt_grund"]


@pytest.mark.django_db
def test_heizlast_ohne_innentemperatur_ist_unbekannt(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=None,
                 air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(app_user.id, room.id, [_wand("s1")], [])
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["heizlast_huellflaeche_w"] is None
    assert "Innentemperatur" in k["unbekannt_grund"]


@pytest.mark.django_db
def test_heizlast_ohne_huellflaechen_ist_unbekannt(app_user):
    """Ein Raum, für den nichts aufgenommen wurde, verliert nicht 0 W — er ist
    unbekannt. Sonst wäre der billigste Weg zu einer kleinen Heizlast, gar nichts
    zu messen."""
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["transmission_w"] is None
    assert "keine Hüllfläche" in k["unbekannt_grund"]


@pytest.mark.django_db
def test_beheizte_flaeche_traegt_null_watt_ohne_u_wert(app_user):
    """Die Gegenprobe: BEHEIZT ist 0 W — und das ist KEIN „unbekannt".

    Eine Innenwand zum beheizten Nachbarraum hat kein Temperaturgefälle. Sie
    braucht weder U-Wert noch Faktor, und ihr Fehlen darf die Heizlast NICHT
    unbekannt machen.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [
            _wand("aussen", gross_area_m2=Decimal("10.000"),
                  u_value=Decimal("0.800")),
            # Weder u_value noch temp_factor — und trotzdem kein „unbekannt".
            _wand("innen", surface_type="INNENWAND", adjacent="BEHEIZT",
                  label="Wand zum Flur", gross_area_m2=Decimal("8.000"),
                  u_value=None, temp_factor=None),
        ],
        [_fenster("innen", opening_type="TUER_INNEN", label="Zimmertür",
                  width_m=Decimal("1.000"), height_m=Decimal("2.000"),
                  u_value=None)],
    )
    k = raum_service.kennzahlen(room, Decimal("-10"))
    assert k["unbekannt_grund"] is None
    # Nur die Außenwand trägt: 0,8 × 10 × 1,0 × 30 = 240 W.
    assert k["transmission_w"] == Decimal("240.0")


@pytest.mark.django_db
def test_freie_oeffnung_zaehlt_nicht_in_die_transmission(app_user):
    """Eine Öffnung ohne Wandzuordnung ist reiner Mengenabzug. Trägt sie trotzdem
    einen U-Wert, ist das ein Widerspruch → Hinweis, aber kein Fehler."""
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1", gross_area_m2=Decimal("10.000"), u_value=Decimal("0.800"))],
        [_fenster(surface_ref=None, label="Loser Abzug",
                  width_m=Decimal("1.000"), height_m=Decimal("1.000"),
                  u_value=Decimal("1.300"))],
    )
    k = raum_service.kennzahlen(room, Decimal("-10"))
    # Die Wand bleibt voll: 0,8 × 10 × 1,0 × 30 = 240 W (das Fenster hängt in
    # keiner Wand, es schneidet nichts aus).
    assert k["transmission_w"] == Decimal("240.0")
    # Aber es zählt gegen die Nettowandfläche des Raumes.
    assert k["opening_area_m2"] == Decimal("1.000")
    assert k["wall_area_net_m2"] == Decimal("9.000")
    assert any("Mengenabzug" in h for h in k["hinweise"])


# --- Kennwertverfahren -----------------------------------------------------

@pytest.mark.django_db
def test_kennwert_raum_schlaegt_gebaeude(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop, floor_area_m2=Decimal("20.000"),
                 heat_load_w_per_m2=Decimal("80.0"))
    k = raum_service.kennzahlen(room, None, Decimal("60"))
    assert k["heizlast_kennwert_w"] == Decimal("1600.0")


@pytest.mark.django_db
def test_kennwert_faellt_auf_gebaeudekennwert_zurueck(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop, floor_area_m2=Decimal("20.000"),
                 heat_load_w_per_m2=None)
    k = raum_service.kennzahlen(room, None, Decimal("60"))
    assert k["heizlast_kennwert_w"] == Decimal("1200.0")


@pytest.mark.django_db
def test_kennwert_ohne_jede_angabe_ist_unbekannt(app_user):
    """Kein Raumkennwert, kein Gebäudekennwert → None, nicht 0."""
    prop = _property(app_user)
    room = _raum(app_user, prop, heat_load_w_per_m2=None)
    k = raum_service.kennzahlen(room, None, None)
    assert k["heizlast_kennwert_w"] is None
    assert any("Kennwert" in h for h in k["hinweise"])


# --- Gebäudesummen ---------------------------------------------------------

@pytest.mark.django_db
def test_aufmass_summen_und_leitungsschaetzung(app_user):
    prop = _property(app_user)
    for name, flaeche, steig in (
        ("Wohnen", "20.000", "5.00"),
        ("Bad", "10.000", "2.50"),
    ):
        _raum(app_user, prop, name=name, floor_area_m2=Decimal(flaeche),
              room_height_m=Decimal("2.500"), perimeter_m=Decimal("18.000"),
              riser_distance_m=Decimal(steig), heat_load_w_per_m2=Decimal("70.0"))

    a = raum_service.aufmass_property(prop.id, None, None)
    assert a["raeume_anzahl"] == 2
    assert a["flaeche_m2"] == Decimal("30.000")
    assert a["volumen_m3"] == Decimal("75.000")
    assert a["umfang_m"] == Decimal("36.000")
    assert a["heizlast_kennwert_w"] == Decimal("2100.0")
    # SCHÄTZUNG: 2 × (5,00 + 2,50) = 15 m — Vor- und Rücklauf, ohne Formstücke.
    assert a["leitungslaenge_schaetzung_m"] == Decimal("15.000")
    assert a["raeume_ohne_steigleitung"] == 0
    assert any("SCHÄTZUNG" in h for h in a["hinweise"])


@pytest.mark.django_db
def test_aufmass_summe_ist_null_sobald_ein_raum_unbekannt_ist(app_user):
    """Die zentrale Invariante auf Gebäudeebene: ein unbekannter Raum darf nicht
    als 0 W in die Summe fallen — sonst ist die Anlage still unterdimensioniert."""
    prop = _property(app_user)
    gut = _raum(app_user, prop, name="Wohnen", indoor_temp_c=Decimal("20.0"),
                air_change_rate=Decimal("0.50"), heat_load_w_per_m2=Decimal("70.0"))
    raum_service.set_aufbau(app_user.id, gut.id, [_wand("s1")], [])

    luecke = _raum(app_user, prop, name="Bad", indoor_temp_c=Decimal("24.0"),
                   air_change_rate=Decimal("0.50"),
                   heat_load_w_per_m2=Decimal("100.0"))
    raum_service.set_aufbau(
        app_user.id, luecke.id, [_wand("s1", label="Badwand", u_value=None)], []
    )

    a = raum_service.aufmass_property(prop.id, Decimal("-10"), None)
    assert a["heizlast_huellflaeche_w"] is None
    assert a["unbekannt_raeume"] == ["Bad"]
    # Der Kennwertweg ist für beide Räume vollständig — der bleibt eine Zahl.
    assert a["heizlast_kennwert_w"] is not None
    assert any("Badwand" in h for h in a["hinweise"])


@pytest.mark.django_db
def test_aufmass_ohne_raeume_ist_unbekannt_nicht_null(app_user):
    """Eine Liegenschaft ohne aufgenommenen Raum hat keine Heizlast von 0 W — sie
    ist UNBEKANNT. Sonst meldete das Panel „0,0 kW", und der billigste Weg zu
    einer kleinen Anlage wäre, gar nichts zu messen.

    Die Mengensummen bleiben dagegen 0: eine leere Summe IST 0 (es fehlt keine
    Fläche, die gezählt werden müsste) — eine leere Heizlast ist keine Aussage.
    """
    prop = _property(app_user)
    raum_service.set_auslegung(
        app_user.id, prop.id,
        {"design_outdoor_temp_c": Decimal("-10.0"),
         "heat_load_w_per_m2": Decimal("70.0")},
    )
    a = raum_service.aufmass_property(prop.id)
    assert a["raeume_anzahl"] == 0
    # Auch mit vollständigen Auslegungsdaten: ohne Raum keine Heizlast.
    assert a["heizlast_kennwert_w"] is None
    assert a["heizlast_huellflaeche_w"] is None
    assert any("kein Raum aufgenommen" in h for h in a["hinweise"])
    # Pflichtfelder (NOT NULL): die leere Summe IST 0.
    assert a["flaeche_m2"] == Decimal("0.000")
    assert a["volumen_m3"] == Decimal("0.000")
    # NULL-fähige Felder: eine Summe über etwas, das niemand gefüllt hat, ist
    # keine 0, sondern eine Nichtaussage.
    assert a["umfang_m"] is None
    assert a["leitungslaenge_schaetzung_m"] is None


@pytest.mark.django_db
def test_aufmass_ohne_umfang_und_ohne_steigleitung_ist_unbekannt(app_user):
    """Trägt KEIN Raum einen Umfang bzw. Weg zur Steigleitung, sind Umfangssumme
    und Leitungslängen-Schätzung `null` — nicht 0.

    Die gefährlichere der beiden: eine Leitungslänge „0,0 m" ginge als MENGE in
    ein Angebot. Der Hinweis daneben entschuldigt eine falsche Zahl nicht.
    """
    prop = _property(app_user)
    _raum(app_user, prop, name="Wohnen", perimeter_m=None, riser_distance_m=None)
    _raum(app_user, prop, name="Bad", perimeter_m=None, riser_distance_m=None)

    a = raum_service.aufmass_property(prop.id)
    assert a["umfang_m"] is None
    assert a["leitungslaenge_schaetzung_m"] is None
    assert any("Umfangssumme ist unbekannt" in h for h in a["hinweise"])
    assert any("Leitungslänge ist unbekannt" in h for h in a["hinweise"])

    # Trägt EIN Raum den Wert, ist die Summe eine ehrliche Teilsumme mit Hinweis.
    raum_service.update_room(
        app_user.id,
        Room.objects.get(property_id=prop.id, name="Wohnen").id,
        {"perimeter_m": Decimal("18.000"), "riser_distance_m": Decimal("4.00")},
    )
    a = raum_service.aufmass_property(prop.id)
    assert a["umfang_m"] == Decimal("18.000")
    assert a["leitungslaenge_schaetzung_m"] == Decimal("8.000")   # 2 × 4 m
    assert a["raeume_ohne_steigleitung"] == 1
    assert any("ohne gemessenen Umfang" in h for h in a["hinweise"])
    assert any("ohne Weg zur Steigleitung" in h for h in a["hinweise"])


@pytest.mark.django_db
def test_aufmass_zaehlt_nur_aktive_raeume(app_user):
    prop = _property(app_user)
    _raum(app_user, prop, name="Wohnen", floor_area_m2=Decimal("20.000"))
    alt = _raum(app_user, prop, name="Abstellkammer", floor_area_m2=Decimal("5.000"))
    raum_service.update_room(app_user.id, alt.id, {"status": "INAKTIV"})

    a = raum_service.aufmass_property(prop.id, None, None)
    assert a["raeume_anzahl"] == 1
    assert a["flaeche_m2"] == Decimal("20.000")


@pytest.mark.django_db
def test_aufmass_ohne_steigleitung_wird_benannt(app_user):
    prop = _property(app_user)
    _raum(app_user, prop, name="Wohnen", riser_distance_m=Decimal("4.00"))
    _raum(app_user, prop, name="Bad", riser_distance_m=None)

    a = raum_service.aufmass_property(prop.id, None, None)
    assert a["leitungslaenge_schaetzung_m"] == Decimal("8.000")
    assert a["raeume_ohne_steigleitung"] == 1
    assert any("ohne Weg zur Steigleitung" in h for h in a["hinweise"])


# --- Validierung -----------------------------------------------------------

@pytest.mark.django_db
def test_aussenluft_faktor_muss_eins_sein(app_user):
    """Gegen Außenluft ist f per Definition 1,0 — das ist keine Normtabelle,
    sondern die Bedeutung von „volle Temperaturdifferenz" (CHECK in der DB)."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError) as exc:
        raum_service.set_aufbau(
            app_user.id, room.id,
            [_wand("s1", adjacent="AUSSENLUFT", temp_factor=Decimal("0.60"))],
            [],
        )
    assert "1,0" in str(exc.value)


@pytest.mark.django_db
def test_einheit_muss_zur_liegenschaft_passen(app_user):
    """Der zusammengesetzte FK verhindert es ohnehin — der Service meldet es als
    Fachfehler statt als 500."""
    fremd = _property(app_user, name="Fremdes Objekt")
    eigen = _property(app_user, name="Eigenes Objekt")
    gebaeude = property_service.add_building(
        app_user.id, property_id=fremd.id, building_number="A"
    )
    with pytest.raises(ValueError) as exc:
        raum_service.create_room(
            app_user.id, eigen.id,
            {"name": "Untergeschoben", "floor_area_m2": Decimal("10"),
             "room_height_m": Decimal("2.5"), "building_id": gebaeude.id},
        )
    assert "gehört nicht zur angegebenen Liegenschaft" in str(exc.value)


# --- Auslegungsdaten kommen vom OBJEKT (Migration 0089) --------------------

@pytest.mark.django_db
def test_auslegung_vom_objekt_rechnet_die_heizlast(app_user):
    """Der Kern des Nachbesserns: Die Anwendung rechnet, ohne dass der Client
    etwas mitschickt — die Auslegungs-Außentemperatur steht an der Liegenschaft.

    Gegenprobe zu `test_heizlast_huellflaeche_rechnet`, das dieselbe Zahl noch
    über einen Parameter erzwingen musste.
    """
    prop = _property(app_user)
    raum_service.set_auslegung(
        app_user.id, prop.id, {"design_outdoor_temp_c": Decimal("-10.0")}
    )
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1", gross_area_m2=Decimal("10.000"), u_value=Decimal("0.800"))],
        [_fenster("s1", width_m=Decimal("2.000"), height_m=Decimal("1.000"),
                  u_value=Decimal("1.300"))],
    )
    # KEIN Parameter — die Zahlen kommen trotzdem.
    k = raum_service.kennzahlen(room)
    assert k["transmission_w"] == Decimal("270.0")   # 0,8×8×30 + 1,3×2×30
    assert k["lueftung_w"] == Decimal("255.0")       # 0,34×0,5×50×30
    assert k["heizlast_huellflaeche_w"] == Decimal("525.0")
    assert k["unbekannt_grund"] is None


@pytest.mark.django_db
def test_auslegung_fehlt_am_objekt_ist_unbekannt_mit_grund(app_user):
    """Ohne Auslegungs-Außentemperatur bleibt sie unbekannt — nicht 0, und der
    Grund benennt das Feld, das zu pflegen ist."""
    prop = _property(app_user)
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.50"))
    raum_service.set_aufbau(app_user.id, room.id, [_wand("s1")], [])
    k = raum_service.kennzahlen(raum_service.get_room(room.id))
    assert k["heizlast_huellflaeche_w"] is None
    assert k["transmission_w"] is None
    assert "design_outdoor_temp_c" in k["unbekannt_grund"]


@pytest.mark.django_db
def test_auslegung_raumkennwert_schlaegt_objektkennwert(app_user):
    """Rangfolge: Raum → Liegenschaft → unbekannt. Nie 0, nie erfunden."""
    prop = _property(app_user)
    raum_service.set_auslegung(
        app_user.id, prop.id, {"heat_load_w_per_m2": Decimal("60.0")}
    )
    objekt = _raum(app_user, prop, name="Objektkennwert",
                   floor_area_m2=Decimal("20.000"))
    eigener = _raum(app_user, prop, name="Eigener Kennwert",
                    floor_area_m2=Decimal("20.000"),
                    heat_load_w_per_m2=Decimal("80.0"))
    assert raum_service.kennzahlen(objekt)["heizlast_kennwert_w"] == Decimal("1200.0")
    assert raum_service.kennzahlen(eigener)["heizlast_kennwert_w"] == Decimal("1600.0")

    # Ohne beides: unbekannt.
    raum_service.set_auslegung(app_user.id, prop.id, {"heat_load_w_per_m2": None})
    ohne = raum_service.kennzahlen(raum_service.get_room(objekt.id))
    assert ohne["heizlast_kennwert_w"] is None


@pytest.mark.django_db
def test_auslegung_parameter_uebersteuert_das_objekt(app_user):
    """Was-wäre-wenn: der Aufrufer darf die Objektwerte übersteuern."""
    prop = _property(app_user)
    raum_service.set_auslegung(
        app_user.id, prop.id,
        {"design_outdoor_temp_c": Decimal("-10.0"),
         "heat_load_w_per_m2": Decimal("60.0")},
    )
    room = _raum(app_user, prop, floor_area_m2=Decimal("20.000"),
                 indoor_temp_c=Decimal("20.0"), air_change_rate=Decimal("0.50"))
    room = raum_service.set_aufbau(app_user.id, room.id, [_wand("s1")], [])

    # Objektwerte: ΔT = 30 → 0,8×10×30 = 240 W; Kennwert 20×60 = 1200 W.
    k = raum_service.kennzahlen(room)
    assert k["transmission_w"] == Decimal("240.0")
    assert k["heizlast_kennwert_w"] == Decimal("1200.0")

    # Übersteuert: ΔT = 20 → 160 W; Kennwert 20×100 = 2000 W.
    k = raum_service.kennzahlen(room, Decimal("0.0"), Decimal("100.0"))
    assert k["transmission_w"] == Decimal("160.0")
    assert k["heizlast_kennwert_w"] == Decimal("2000.0")


@pytest.mark.django_db
def test_auslegung_setzen_und_zuruecksetzen(app_user):
    prop = _property(app_user)
    raum_service.set_auslegung(
        app_user.id, prop.id,
        {"design_outdoor_temp_c": Decimal("-12.0"),
         "heat_load_w_per_m2": Decimal("55.0")},
    )
    prop = Property.objects.get(pk=prop.id)
    assert prop.design_outdoor_temp_c == Decimal("-12.0")
    assert prop.heat_load_w_per_m2 == Decimal("55.0")

    # Teil-Update: nur das eine Feld — das andere bleibt stehen.
    raum_service.set_auslegung(
        app_user.id, prop.id, {"design_outdoor_temp_c": Decimal("-14.0")}
    )
    prop = Property.objects.get(pk=prop.id)
    assert prop.design_outdoor_temp_c == Decimal("-14.0")
    assert prop.heat_load_w_per_m2 == Decimal("55.0")

    # Ausdrückliches null setzt zurück (beide Spalten sind NULL-fähig).
    raum_service.set_auslegung(
        app_user.id, prop.id,
        {"design_outdoor_temp_c": None, "heat_load_w_per_m2": None},
    )
    prop = Property.objects.get(pk=prop.id)
    assert prop.design_outdoor_temp_c is None
    assert prop.heat_load_w_per_m2 is None


@pytest.mark.django_db
def test_auslegung_wertebereich(app_user):
    prop = _property(app_user)
    with pytest.raises(ValueError) as exc:
        raum_service.set_auslegung(
            app_user.id, prop.id, {"design_outdoor_temp_c": Decimal("-60.0")}
        )
    assert "design_outdoor_temp_c" in str(exc.value)
    # numeric(6,1): 99999,9 ist die Obergrenze der Spalte.
    with pytest.raises(ValueError) as exc:
        raum_service.set_auslegung(
            app_user.id, prop.id, {"heat_load_w_per_m2": Decimal("100000")}
        )
    assert "Kennwert" in str(exc.value)


# --- Wandfläche ohne Hüllfläche: unbekannt, nicht 0 ------------------------

@pytest.mark.django_db
def test_wandflaeche_ohne_huellflaeche_ist_unbekannt_nicht_null(app_user):
    """Dieselbe Haltung wie bei der Heizlast: 0 m² Wand wäre eine AUSSAGE — und
    liefe als Mengengrundlage fürs Verputzen/Streichen in ein Angebot."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    k = raum_service.kennzahlen(room)
    assert k["wall_area_gross_m2"] is None
    assert k["wall_area_net_m2"] is None
    assert k["opening_area_m2"] == Decimal("0.000")


# --- Trigger raumweit (Migration 0089) -------------------------------------

@pytest.mark.django_db
def test_freie_oeffnung_groesser_als_alle_bauteile_wird_gesperrt(app_user):
    """Grenze (b) aus 0089: eine Öffnung OHNE Wandzuordnung umging bisher jede
    Prüfung — die Nettowandfläche wurde negativ (reproduziert). Bedienfehler → 422."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError) as exc:
        raum_service.set_aufbau(
            app_user.id, room.id,
            [_wand("s1", gross_area_m2=Decimal("10.000"))],
            [_fenster(surface_ref=None, width_m=Decimal("5.000"),
                      height_m=Decimal("5.000"))],  # 25 m² frei gegen 10 m² Wand
        )
    assert "Nettowandfläche" in str(exc.value)
    assert not RoomOpening.objects.filter(room_id=room.id).exists()


@pytest.mark.django_db
def test_freie_oeffnung_ohne_jede_huellflaeche_ist_erlaubt(app_user):
    """Hat der Raum noch KEINE Hüllfläche, ist die Wandfläche unbekannt (nicht 0)
    — dann gibt es keine Grenze, gegen die man prüfen könnte: der Monteur darf
    die Fenster vor den Wänden erfassen."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_aufbau(
        app_user.id, room.id, [],
        [_fenster(surface_ref=None, width_m=Decimal("5.000"),
                  height_m=Decimal("5.000"))],
    )
    assert room.openings.all()[0].area_m2 == Decimal("25.000")
    k = raum_service.kennzahlen(room)
    assert k["wall_area_net_m2"] is None   # NICHT −25 m²


# --- Skala und Wertebereich: Bedienfehler, kein 500 ------------------------

@pytest.mark.django_db
def test_zu_feiner_u_wert_ist_bedienfehler(app_user):
    """numeric(5,3) rundete 0,0001 auf 0,000 → CHECK (u_value > 0) → 500.
    Jetzt: ValueError (422), und die Meldung nennt Feld und Grenze."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError) as exc:
        raum_service.set_aufbau(
            app_user.id, room.id, [_wand("s1", u_value=Decimal("0.0001"))], []
        )
    meldung = str(exc.value)
    assert "U-Wert" in meldung
    assert "u_value" not in meldung   # Klarname, kein Spaltenname
    assert "0,001" in meldung         # Grenze deutsch formatiert


@pytest.mark.django_db
def test_zu_feine_fenstermasse_sind_bedienfehler(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    for feld, klarname in (("width_m", "Breite"), ("height_m", "Höhe")):
        with pytest.raises(ValueError) as exc:
            raum_service.set_aufbau(
                app_user.id, room.id, [_wand("s1")],
                [_fenster("s1", **{feld: Decimal("0.0004")})],
            )
        # Die Meldung geht an einen Monteur: deutscher Klarname, kein Spaltenname.
        assert klarname in str(exc.value)
        assert feld not in str(exc.value)

    with pytest.raises(ValueError) as exc:
        raum_service.set_aufbau(
            app_user.id, room.id,
            [_wand("s1", gross_area_m2=Decimal("0.0004"))], [],
        )
    assert "Bruttofläche" in str(exc.value)


@pytest.mark.django_db
def test_zu_grosse_werte_sind_bedienfehler(app_user):
    """numeric(10,3)/numeric(4,2)/numeric(6,1) laufen sonst als DataError
    („numeric field overflow") in einen 500."""
    prop = _property(app_user)
    for feld, wert, grenze, klarname in (
        ("floor_area_m2", Decimal("99999999"), "9.999.999,999", "Grundfläche"),
        ("air_change_rate", Decimal("100"), "99,99", "Luftwechselrate"),
        ("heat_load_w_per_m2", Decimal("100000"), "99.999,9", "Kennwert"),
    ):
        with pytest.raises(ValueError) as exc:
            _raum(app_user, prop, name=f"Zu groß {feld}", **{feld: wert})
        meldung = str(exc.value)
        # Klarname statt Spaltenname, Grenze deutsch formatiert.
        assert klarname in meldung
        assert feld not in meldung
        assert grenze in meldung          # die Grenze wird BENANNT
    assert Room.objects.filter(property_id=prop.id).count() == 0


@pytest.mark.django_db
def test_zu_grosser_wert_im_update_ist_bedienfehler(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError) as exc:
        raum_service.update_room(
            app_user.id, room.id, {"floor_area_m2": Decimal("99999999")}
        )
    assert "Grundfläche" in str(exc.value)
    # Der alte Wert steht unverändert.
    assert Room.objects.get(pk=room.id).floor_area_m2 == Decimal("20.000")


# --- Normalisierung der Client-Schlüssel -----------------------------------

@pytest.mark.django_db
def test_ref_und_surface_ref_werden_gleich_normalisiert(app_user):
    """`refs` wurde gestrippt gebaut, `surface_ref` ungestrippt verglichen — ein
    in sich konsistenter Payload flog mit „unbekannte Hüllfläche" heraus."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    room = raum_service.set_aufbau(
        app_user.id, room.id, [_wand(" s1 ")], [_fenster(" s1 ")]
    )
    assert room.openings.all()[0].surface_id == room.surfaces.all()[0].id


# --- Rundung ---------------------------------------------------------------

@pytest.mark.django_db
def test_summe_ist_die_summe_der_ausgewiesenen_teile(app_user):
    """Wer nachrechnet, darf keine Differenz finden: die ausgewiesene Heizlast IST
    Transmission + Lüftung — beide so, wie sie ausgewiesen werden."""
    prop = _property(app_user)
    raum_service.set_auslegung(
        app_user.id, prop.id, {"design_outdoor_temp_c": Decimal("-10.0")}
    )
    room = _raum(app_user, prop, indoor_temp_c=Decimal("20.0"),
                 air_change_rate=Decimal("0.55"))
    room = raum_service.set_aufbau(
        app_user.id, room.id,
        [_wand("s1", gross_area_m2=Decimal("7.777"), u_value=Decimal("0.833"))],
        [],
    )
    k = raum_service.kennzahlen(room)
    # 0,833 × 7,777 × 30 = 194,34723 → 194,3
    assert k["transmission_w"] == Decimal("194.3")
    assert k["lueftung_w"] == Decimal("280.5")   # 0,34 × 0,55 × 50 × 30
    assert k["heizlast_huellflaeche_w"] == k["transmission_w"] + k["lueftung_w"]


@pytest.mark.django_db
def test_update_ist_teilupdate(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop, note="Erstaufnahme",
                 indoor_temp_c=Decimal("20.0"))
    room = raum_service.update_room(
        app_user.id, room.id, {"indoor_temp_c": Decimal("22.0")}
    )
    assert room.indoor_temp_c == Decimal("22.0")
    # Nicht gesendete Felder bleiben unangetastet.
    assert room.note == "Erstaufnahme"
    assert room.name == "Wohnzimmer"
