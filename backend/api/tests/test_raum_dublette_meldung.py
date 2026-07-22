"""Die Dubletten-Meldung muss den Bereich nennen, in dem der Name wirklich kollidiert.

`room_dublette` ist UNIQUE **NULLS NOT DISTINCT** über
`(property_id, unit_id, storey, name)`. Zwei Eigenheiten dieses Schlüssels
machen jede pauschale Meldung falsch:

* **Das Gebäude steht nicht darin.** Räume ohne Einheit teilen sich ihren
  Namensraum über alle Gebäude einer Liegenschaft hinweg.
* **Die Etage steht darin.** Innerhalb einer Einheit sind gleiche Raumnamen auf
  verschiedenen Etagen erlaubt (Maisonette: „Bad" im EG und im OG).

Die frühere Meldung sprach pauschal von „dieser Einheit/diesem Geschoss" und lag
damit in beide Richtungen daneben. Seit der Strukturbaum das Anlegen von Räumen
ohne Einheit **je Gebäude** anbietet (Befund I13), führt das regelmäßig zu einer
Begründung, die im Baum nicht auffindbar ist: Der kollidierende
„Heizungskeller" steht im anderen Haus.

Geprüft wird über die **API**, nicht am Service vorbei: Das Versprechen dieses
Fixes ist, dass der richtige Text als 422 beim Client ankommt.
"""
import pytest

from db_core.services import property as property_service


@pytest.fixture
def dubl_objekt(app_user):
    # WEG, nicht „MEHRFAMILIENHAUS" — den Wert kennt die Codeliste nicht, und
    # `create_property` prüft fail-closed. Ein erfundener Typ lässt die Fixture
    # scheitern; die Tests wären ERROR statt FAIL und gingen in der Suite unter.
    return property_service.create_property(
        app_user.id, name="Zwei-Häuser-Hof", property_type="WEG",
        street="Hofweg", house_number="7", postal_code="10115", city="Berlin",
    )


@pytest.fixture
def dubl_haeuser(app_user, dubl_objekt):
    """Vorderhaus und Hinterhaus — der Fall, um den es geht."""
    vorne = property_service.add_building(
        app_user.id, property_id=dubl_objekt.id, building_number="1", name="Vorderhaus",
    )
    hinten = property_service.add_building(
        app_user.id, property_id=dubl_objekt.id, building_number="2", name="Hinterhaus",
    )
    return vorne, hinten


def _post_raum(client, objekt, **kwargs):
    daten = {"name": "Heizungskeller", "floor_area_m2": "12.000", "room_height_m": "2.200"}
    daten.update(kwargs)
    return client.post(
        f"/api/property/properties/{objekt.id}/rooms",
        data=daten, content_type="application/json",
    )


def _fehlertext(antwort):
    assert antwort.status_code == 422, antwort.content
    return antwort.json()["detail"]


# --- Ohne Einheit: der Name gilt gebäudeübergreifend ------------------------

@pytest.mark.django_db
def test_gleicher_raumname_in_zwei_gebaeuden_nennt_die_liegenschaft(
    admin_client, dubl_objekt, dubl_haeuser
):
    """Der zweite „Heizungskeller" scheitert — die Meldung sagt WARUM richtig."""
    vorne, hinten = dubl_haeuser
    erst = _post_raum(admin_client, dubl_objekt, building_id=str(vorne.id))
    assert erst.status_code == 201, erst.content

    text = _fehlertext(_post_raum(admin_client, dubl_objekt, building_id=str(hinten.id)))

    # Der echte Geltungsbereich …
    assert "Liegenschaft" in text
    assert "gebäudeübergreifend" in text or "über alle Gebäude hinweg" in text
    # … und KEINE Rede von einer Einheit: Der Nutzer hat keine gewählt, und im
    # Baum gibt es keine, in der er nachsehen könnte.
    assert "In dieser Einheit" not in text


@pytest.mark.django_db
def test_ohne_einheit_mit_etage_nennt_ebenfalls_die_liegenschaft(
    admin_client, dubl_objekt, dubl_haeuser
):
    """Auch mit Etage fehlt das Gebäude im Schlüssel.

    Zwei Häuser, beide mit einem „Treppenhaus" im EG: Das kollidiert. „In dieser
    Etage" allein läse sich als „im EG DIESES Hauses" — und im anderen Haus
    sucht dann niemand.
    """
    vorne, hinten = dubl_haeuser
    erst = _post_raum(
        admin_client, dubl_objekt, name="Treppenhaus",
        building_id=str(vorne.id), storey="EG",
    )
    assert erst.status_code == 201, erst.content

    text = _fehlertext(_post_raum(
        admin_client, dubl_objekt, name="Treppenhaus",
        building_id=str(hinten.id), storey="EG",
    ))

    assert "EG" in text
    assert "Liegenschaft" in text
    assert "gebäudeübergreifend" in text


# --- Mit Einheit: die Etage grenzt wirklich ein ------------------------------

@pytest.mark.django_db
def test_dublette_innerhalb_einer_einheit_nennt_die_einheit(
    app_user, admin_client, dubl_objekt, dubl_haeuser
):
    """Gegenprobe: Mit Einheit ist die engere Aussage die richtige."""
    vorne, _ = dubl_haeuser
    einheit = property_service.add_unit(
        app_user.id, building_id=vorne.id, property_id=dubl_objekt.id,
        unit_number="WE 1", unit_type="APARTMENT",
    )
    bad = {"name": "Bad", "floor_area_m2": "6.000", "room_height_m": "2.500",
           "building_id": str(vorne.id), "unit_id": str(einheit.id)}

    erst = _post_raum(admin_client, dubl_objekt, **bad)
    assert erst.status_code == 201, erst.content

    text = _fehlertext(_post_raum(admin_client, dubl_objekt, **bad))

    assert "In dieser Einheit" in text
    assert "Bad" in text
    # Der liegenschaftsweite Hinweis wäre hier falsch — die Einheit grenzt ein.
    assert "gebäudeübergreifend" not in text
    # Und nicht die alte Pauschale: Ohne Etage grenzt kein Geschoss ein, ein
    # „/diesem Geschoss" wäre eine erfundene zweite Grenze. Ohne diese Zeile
    # bliebe der Test auch bei zurückgedrehtem Fix grün (Teilstring-Treffer).
    assert "Geschoss" not in text


@pytest.mark.django_db
def test_maisonette_gleicher_name_auf_zwei_etagen_ist_erlaubt(
    app_user, admin_client, dubl_objekt, dubl_haeuser
):
    """Die Etage steht IM Schlüssel: „Bad" im EG und im OG ist zulässig.

    Genau deshalb darf die Meldung bei gesetzter Etage nicht behaupten, der Name
    sei „in dieser Einheit" vergeben — er ist es nur auf DIESER Etage.
    """
    vorne, _ = dubl_haeuser
    einheit = property_service.add_unit(
        app_user.id, building_id=vorne.id, property_id=dubl_objekt.id,
        unit_number="WE 2", unit_type="APARTMENT",
    )
    gemeinsam = {"name": "Bad", "floor_area_m2": "6.000", "room_height_m": "2.500",
                 "building_id": str(vorne.id), "unit_id": str(einheit.id)}

    eg = _post_raum(admin_client, dubl_objekt, storey="EG", **gemeinsam)
    assert eg.status_code == 201, eg.content
    og = _post_raum(admin_client, dubl_objekt, storey="OG", **gemeinsam)
    assert og.status_code == 201, og.content

    # Erst die WIEDERHOLUNG derselben Etage kollidiert — und die Meldung nennt sie.
    text = _fehlertext(_post_raum(admin_client, dubl_objekt, storey="OG", **gemeinsam))
    assert "OG" in text
    assert "Einheit" in text


# --- Umbenennen (PATCH) -----------------------------------------------------

@pytest.mark.django_db
def test_umbenennen_auf_belegten_namen_nennt_den_richtigen_bereich(
    admin_client, dubl_objekt, dubl_haeuser
):
    """Der PATCH-Pfad baut den Zielzustand selbst zusammen — auch der muss stimmen.

    `update_room` liest `unit_id`/`storey`/`name` aus dem PATCH mit Rückfall auf
    den Bestand. Genau diese Konstruktion war die unsicherste Stelle des Fixes,
    und sie war bis hierher nur analytisch abgesichert. Das Umbenennen ist
    zugleich die Funktion, die mit dem Strukturbaum neu ins UI gekommen ist.
    """
    vorne, hinten = dubl_haeuser
    belegt = _post_raum(admin_client, dubl_objekt, building_id=str(vorne.id))
    assert belegt.status_code == 201, belegt.content
    anderer = _post_raum(
        admin_client, dubl_objekt, name="Waschküche", building_id=str(hinten.id),
    )
    assert anderer.status_code == 201, anderer.content

    # „Waschküche" (Hinterhaus) auf den im Vorderhaus belegten Namen umbenennen.
    r = admin_client.patch(
        f"/api/property/rooms/{anderer.json()['id']}",
        data={"name": "Heizungskeller"}, content_type="application/json",
    )

    text = _fehlertext(r)
    assert "Liegenschaft" in text
    assert "über alle Gebäude hinweg" in text
    # Der Name im Text muss der ZIELname sein, nicht der bisherige.
    assert "Heizungskeller" in text
    assert "Waschküche" not in text


# --- Kontrollprobe ----------------------------------------------------------

@pytest.mark.django_db
def test_verschiedene_namen_kollidieren_nicht(admin_client, dubl_objekt, dubl_haeuser):
    """Ohne Constraint-Treffer kein Fehler — sonst prüften die Tests oben nichts.

    Ohne diese Probe könnten sie auch dann „grün" sein, wenn das Anlegen aus
    einem ganz anderen Grund scheitert (fehlendes Recht, Pflichtfeld, Tippfehler
    im Pfad).
    """
    vorne, hinten = dubl_haeuser
    a = _post_raum(admin_client, dubl_objekt, building_id=str(vorne.id))
    assert a.status_code == 201, a.content
    b = _post_raum(
        admin_client, dubl_objekt, name="Heizungskeller Hinterhaus",
        building_id=str(hinten.id),
    )
    assert b.status_code == 201, b.content
    assert a.json()["id"] != b.json()["id"]
