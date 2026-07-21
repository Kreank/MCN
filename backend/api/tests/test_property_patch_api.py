"""PATCH auf Gebäude und Einheit (AP1, Befunde I1/I7/I12, Migration 0124).

Vor 0124 gab es auf `property.building` und `property.unit` **keinen einzigen
Schreibpfad außer INSERT**: Ein ohne Bezeichnung angelegtes Gebäude blieb
dauerhaft „Gebäude 1", eine vertippte Einheitsnummer war nicht zu retten, und
die Etage einer Wohnung ließ sich überhaupt nicht erfassen.

Die Tests laufen gegen die echte Test-DB mit allen Triggern — insbesondere
gegen die mit 0124 neu gesetzten Audit-/No-Delete-Trigger und gegen den seit
0009 bestehenden `trg_unit_type_conflicts`.
"""
import uuid
from datetime import date

import pytest

from db_core.models import Unit
from db_core.services import identity as identity_service
from db_core.services import property as property_service


@pytest.fixture
def objekt(app_user):
    """Eine WEG mit einem Gebäude OHNE Bezeichnung und zwei Einheiten.

    Das namenlose Gebäude ist der Anlassfall aus Befund I7.
    """
    weg = property_service.create_property(
        app_user.id, name="Wohnanlage Ahornweg", property_type="WEG",
        street="Ahornweg", house_number="7", postal_code="10115", city="Berlin",
    )
    building = property_service.add_building(
        app_user.id, property_id=weg.id, building_number="1",
    )
    unit = property_service.add_unit(
        app_user.id, building_id=building.id, property_id=weg.id,
        unit_type="APARTMENT", unit_number="WE 1",
    )
    return {"app_user": app_user, "weg": weg, "building": building, "unit": unit}


# --- Gebäude ---------------------------------------------------------------


@pytest.mark.django_db
def test_gebaeude_nachtraeglich_benennen(admin_client, objekt):
    """Befund I7: genau der Fall, der bisher unmöglich war."""
    bid = objekt["building"].id
    assert objekt["building"].name is None

    r = admin_client.patch(
        f"/api/property/buildings/{bid}",
        data={"name": "Vorderhaus"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["name"] == "Vorderhaus"

    objekt["building"].refresh_from_db()
    assert objekt["building"].name == "Vorderhaus"


@pytest.mark.django_db
def test_gebaeude_teilupdate_laesst_andere_felder_stehen(admin_client, objekt):
    """PATCH heißt Teil-Update: die Nummer darf ein Namens-PATCH nicht anfassen."""
    bid = objekt["building"].id
    r = admin_client.patch(
        f"/api/property/buildings/{bid}",
        data={"name": "Seitenflügel"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["building_number"] == "1"


@pytest.mark.django_db
def test_gebaeudename_laesst_sich_wieder_loeschen(admin_client, objekt):
    """`name` ist NULL-fähig — ausdrückliches null muss löschen, nicht scheitern.

    Genau dafür liest der Endpunkt `exclude_unset`: Ohne das wäre „gelöscht"
    nicht von „nicht gesendet" zu unterscheiden.
    """
    bid = objekt["building"].id
    admin_client.patch(
        f"/api/property/buildings/{bid}",
        data={"name": "Vorderhaus"},
        content_type="application/json",
    )
    r = admin_client.patch(
        f"/api/property/buildings/{bid}",
        data={"name": None},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["name"] is None


@pytest.mark.django_db
def test_leerstring_wird_zu_null_nicht_zu_leerem_namen(admin_client, objekt):
    """Ein Name aus Leerzeichen sähe befüllt aus und wäre leer."""
    bid = objekt["building"].id
    r = admin_client.patch(
        f"/api/property/buildings/{bid}",
        data={"name": "   "},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["name"] is None


@pytest.mark.django_db
def test_gebaeudenummer_darf_nicht_geleert_werden(admin_client, objekt):
    """`building_number` ist NOT NULL — Leeren ist ein Fachfehler, kein 500."""
    bid = objekt["building"].id
    r = admin_client.patch(
        f"/api/property/buildings/{bid}",
        data={"building_number": "  "},
        content_type="application/json",
    )
    assert r.status_code == 422
    # Auf den Klartext prüfen, nicht nur auf „Pflichtfeld": Sonst erfüllte auch
    # die alte Meldung „building_number ist ein Pflichtfeld…" die Zusicherung,
    # und der Spaltenname leckte unbemerkt wieder in die Oberfläche.
    assert "Gebäudenummer" in r.json()["detail"]


@pytest.mark.django_db
def test_einheitsnummer_darf_nicht_geleert_werden(admin_client, objekt):
    """Gegenstück für die Einheit — inklusive Klartext statt Spaltenname."""
    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"unit_number": "   "},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Einheitsnummer" in r.json()["detail"]


@pytest.mark.django_db
def test_doppelte_gebaeudenummer_wird_fachlich_gemeldet(admin_client, objekt):
    """UNIQUE (property_id, building_number) → 422 mit Klartext, nicht 500."""
    property_service.add_building(
        objekt["app_user"].id, property_id=objekt["weg"].id, building_number="2",
    )
    r = admin_client.patch(
        f"/api/property/buildings/{objekt['building'].id}",
        data={"building_number": "2"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "bereits ein Gebäude" in r.json()["detail"]


@pytest.mark.django_db
def test_gebaeude_404(admin_client, objekt):
    r = admin_client.patch(
        f"/api/property/buildings/{uuid.uuid4()}",
        data={"name": "X"},
        content_type="application/json",
    )
    assert r.status_code == 404


# --- Einheit ---------------------------------------------------------------


@pytest.mark.django_db
def test_einheit_etage_setzen(admin_client, objekt):
    """Befund I12: Die Etage hängt jetzt an der Einheit, nicht nur am Raum."""
    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"storey": "3. OG"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["storey"] == "3. OG"

    objekt["unit"].refresh_from_db()
    assert objekt["unit"].storey == "3. OG"


@pytest.mark.django_db
def test_etage_ist_freitext_kein_codelistenwert(admin_client, objekt):
    """Souterrain/Hochparterre sind der Grund gegen eine Codeliste."""
    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"storey": "Hochparterre links"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["storey"] == "Hochparterre links"


@pytest.mark.django_db
def test_etage_leeren_ist_erlaubt(admin_client, objekt):
    """NULL heißt „nicht erfasst" und muss erreichbar bleiben."""
    uid = objekt["unit"].id
    admin_client.patch(
        f"/api/property/units/{uid}",
        data={"storey": "EG"},
        content_type="application/json",
    )
    r = admin_client.patch(
        f"/api/property/units/{uid}",
        data={"storey": None},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["storey"] is None


@pytest.mark.django_db
def test_leere_etage_verletzt_den_check_nicht(admin_client, objekt):
    """Ein Leerstring würde `unit_storey_nicht_leer` verletzen → vorher zu NULL."""
    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"storey": "   "},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["storey"] is None


@pytest.mark.django_db
def test_einheitsnummer_korrigieren(admin_client, objekt):
    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"unit_number": "WE 01"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["unit_number"] == "WE 01"


@pytest.mark.django_db
def test_unbekannter_unit_type_wird_abgelehnt(admin_client, objekt):
    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"unit_type": "PENTHOUSE"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Ungültiger unit_type" in r.json()["detail"]


@pytest.mark.django_db
def test_doppelte_einheitsnummer_wird_fachlich_gemeldet(admin_client, objekt):
    """UNIQUE (property_id, unit_number) — pro Liegenschaft, nicht pro Gebäude."""
    property_service.add_unit(
        objekt["app_user"].id, building_id=objekt["building"].id,
        property_id=objekt["weg"].id, unit_type="APARTMENT", unit_number="WE 2",
    )
    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"unit_number": "WE 2"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "bereits eine Einheit" in r.json()["detail"]


@pytest.mark.django_db
def test_umtypisieren_mit_belegung_wird_uebersetzt(admin_client, objekt):
    """Befund I2b: `trg_unit_type_conflicts` meldet sich per RAISE EXCEPTION.

    Ohne Übersetzung im Service wäre das ein 500. Der Trigger (0009, Beschluss
    F-12) sperrt den Wechsel nach COMMON_AREA/TECHNICAL_ROOM, solange eine
    Belegung an der Einheit hängt.
    """
    from db_core.services import belegung as belegung_service

    belegung_service.create_belegung(
        objekt["app_user"].id,
        unit_id=objekt["unit"].id,
        occupancy_type="RENTED",
        valid_from=date(2024, 1, 1),
    )

    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"unit_type": "COMMON_AREA"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Belegungen" in r.json()["detail"]


@pytest.mark.django_db
def test_umtypisieren_mit_eigentumsstand_wird_uebersetzt(admin_client, objekt):
    """Der ANDERE Zweig derselben Übersetzung (Beschluss A-08).

    Wichtig, weil beide Zweige inzwischen ausdrücklich matchen: Ohne diesen
    Test bliebe unbemerkt, wenn der A-08-Zweig nie greift und stattdessen ein
    500 durchschlüge.

    `tenure.ownership_period` hat weder Service noch Model (Befund J5 — die
    Tabellen sind seit 0005 gebaut, aber nirgends angebunden), deshalb hier
    ein Direkt-INSERT. `source_type` ist codelistengebunden.
    """
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO tenure.ownership_period
                   (unit_id, valid_from, source_type, source_reference)
               VALUES (%s, %s, %s, %s)""",
            [str(objekt["unit"].id), date(2024, 1, 1), "MANUAL", "Teilungserklärung 1"],
        )

    r = admin_client.patch(
        f"/api/property/units/{objekt['unit'].id}",
        data={"unit_type": "TECHNICAL_ROOM"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Eigentumsstände" in r.json()["detail"]


@pytest.mark.django_db
def test_einheit_404(admin_client, objekt):
    r = admin_client.patch(
        f"/api/property/units/{uuid.uuid4()}",
        data={"storey": "EG"},
        content_type="application/json",
    )
    assert r.status_code == 404


# --- Schutzstandard (Migration 0124) ---------------------------------------


@pytest.mark.django_db
def test_aenderung_hinterlaesst_einen_audit_eintrag(admin_client, objekt):
    """Der Kern von I2: Vor 0124 gab es zu keiner Änderung einen Nachweis.

    `audit.audit_entry` ist nicht als Model abgebildet — direkt gegen die
    Tabelle prüfen, das ist hier ohnehin die ehrlichere Quelle.
    """
    from django.db import connection

    bid = objekt["building"].id
    r = admin_client.patch(
        f"/api/property/buildings/{bid}",
        data={"name": "Vorderhaus"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content

    with connection.cursor() as cur:
        cur.execute(
            """SELECT action, before_excerpt->>'name', after_excerpt->>'name',
                      actor_type, actor_user_id
               FROM audit.audit_entry
               WHERE target_type = 'property.building' AND target_id = %s
               ORDER BY occurred_at DESC LIMIT 1""",
            [str(bid)],
        )
        zeile = cur.fetchone()

    assert zeile is not None, "Keine Audit-Zeile — der Trigger aus 0124 greift nicht"
    assert zeile[0] == "ROW_UPDATE"
    assert zeile[1] is None
    assert zeile[2] == "Vorderhaus"
    # Das WER ist der halbe Zweck eines Audits. Ohne diese beiden Zeilen würde
    # der Test auch dann grün bleiben, wenn `app.current_user_id` nie ankommt
    # und jede Änderung als 'SYSTEM' protokolliert würde.
    assert zeile[3] == "USER"
    assert zeile[4] is not None


@pytest.mark.django_db
def test_einheit_mit_etage_anlegen(admin_client, objekt):
    """`storey` gleich beim Anlegen — sonst wäre die Etage ein zweiter Handgriff."""
    r = admin_client.post(
        f"/api/property/buildings/{objekt['building'].id}/units",
        data={"unit_type": "APARTMENT", "unit_number": "WE 9", "storey": "2. OG"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["storey"] == "2. OG"


@pytest.mark.django_db
def test_anlegen_ohne_etage_bleibt_moeglich(admin_client, objekt):
    """Bestandsdaten kennen die Etage oft nicht — NULL muss der Normalfall sein."""
    r = admin_client.post(
        f"/api/property/buildings/{objekt['building'].id}/units",
        data={"unit_type": "APARTMENT", "unit_number": "WE 8"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["storey"] is None


@pytest.mark.django_db
def test_address_id_ist_kein_schreibpfad(admin_client, objekt):
    """`address_id` wird bewusst NICHT angenommen.

    Ohne Objektgrenze ließe sich einem eigenen Gebäude die Anschrift einer
    fremden Liegenschaft geben — und die rendert `api/planung.py` anschließend
    als Einsatzort. Ninja ignoriert unbekannte Felder, entscheidend ist also,
    dass die Adresse danach **unverändert** ist.
    """
    fremde = property_service.create_property(
        objekt["app_user"].id, name="Fremdes Haus", property_type="COMMERCIAL",
        street="Fremdweg", house_number="1", postal_code="20095", city="Hamburg",
    )
    vorher = objekt["building"].address_id

    r = admin_client.patch(
        f"/api/property/buildings/{objekt['building'].id}",
        data={"name": "Vorderhaus", "address_id": str(fremde.address_id)},
        content_type="application/json",
    )
    assert r.status_code == 200

    objekt["building"].refresh_from_db()
    assert objekt["building"].address_id == vorher, (
        "address_id wurde geschrieben — der Schreibpfad ist wieder offen, "
        "ohne dass eine Objektgrenze dafür existiert"
    )


@pytest.mark.django_db
def test_die_datenbank_verbietet_das_loeschen(objekt):
    """No-Delete-Trigger aus 0124 — nicht der fehlende Servicepfad.

    **Bewusst OHNE `transaction=True`.** Der naheliegende Weg wäre es, aber er
    kostet: Tests mit `transaction=True` lassen Django beim Aufräumen `flush`
    laufen, das `TRUNCATE` benutzt — und genau das verbieten die
    No-Truncate-Trigger. Jeder solche Test erzeugt deshalb einen
    Teardown-Fehler und vergrößert die bekannte 19er-Baseline der Suite.

    Der `transaction.atomic()`-Block setzt stattdessen einen Savepoint: Die
    Trigger-Exception rollt nur bis dorthin zurück, die Testtransaktion bleibt
    heil. Gleiche Aussage, kein neuer Fehler in der Suite.

    ProgrammingError, nicht InternalError: `util.forbid_mutation` meldet sich
    per plpgsql-RAISE (P0001), und das bildet Django auf ProgrammingError ab.
    """
    from django.db import ProgrammingError, transaction

    with pytest.raises(ProgrammingError, match="append-only"):
        with transaction.atomic():
            Unit.objects.filter(id=objekt["unit"].id).delete()

    # Die Zeile steht noch — der Trigger hat gesperrt, nicht nur gemeckert.
    assert Unit.objects.filter(id=objekt["unit"].id).exists()


@pytest.mark.django_db
def test_die_datenbank_verbietet_das_loeschen_eines_gebaeudes(objekt):
    """Dasselbe für `building` — 0124 setzt den Trigger auf beide Tabellen."""
    from django.db import ProgrammingError, transaction

    from db_core.models import Building

    with pytest.raises(ProgrammingError, match="append-only"):
        with transaction.atomic():
            Building.objects.filter(id=objekt["building"].id).delete()

    assert Building.objects.filter(id=objekt["building"].id).exists()
