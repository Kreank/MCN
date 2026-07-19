"""Gebäude und Einheit am Einsatz/Termin (workflow.service_job, Migration 0119).

Gegen die echte Test-DB — also gegen die scharfen zusammengesetzten FKs und die
CHECKs:

* Anlage eines (freien) Termins mit Gebäude/Einheit,
* die Hierarchie-Regeln (Einheit⇒Gebäude, Gebäude⇒Liegenschaft) als Service-422
  UND physisch in der DB (inkl. des CHECKs, der die MATCH-SIMPLE-Lücke schließt),
* Konsistenz Gebäude↔Liegenschaft bzw. Einheit↔Gebäude,
* Zielzustands-Prüfung beim Teil-Update (Liegenschaft wechseln, ohne das Gebäude
  zu räumen, muss scheitern),
* die Ortsauflösung der Anzeige (api/planung): Gebäudeadresse schlägt die
  Liegenschaftsadresse, Einheit erscheint als Zusatz, und der auftragsgebundene
  Termin ERBT den Ort vom Auftrag.
"""
import uuid

import pytest
from django.db import connection

from api import planung as planung_api
from db_core.db_context import business_transaction
from db_core.models import Address, ServiceJob, WorkOrder
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


# --- Fixtures ---------------------------------------------------------------

def _property(app_user, name="Ortobjekt", street="Weg", city="Berlin"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street=street, postal_code="10115", city=city,
    )


def _adresse(app_user, *, street, house_number="1", city="Berlin"):
    """Eine eigenständige identity.address (für die eigene Gebäudeadresse)."""
    with business_transaction(app_user.id):
        return Address.objects.create(
            id=uuid.uuid4(), street=street, house_number=house_number,
            postal_code="10115", city=city, country_code="DE",
        )


def _building(app_user, prop, *, number="1", name=None, address_id=None):
    return property_service.add_building(
        app_user.id, property_id=prop.id, building_number=number,
        name=name, address_id=address_id,
    )


def _unit(app_user, prop, building, *, number="3. OG rechts", unit_type="APARTMENT"):
    return property_service.add_unit(
        app_user.id, building_id=building.id, property_id=prop.id,
        unit_type=unit_type, unit_number=number,
    )


def _order(app_user, obj):
    return auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag mit Ort"
    )


def _lade(job_id):
    """Wie die API: mit den Ortsketten für die Auflösung (kein N+1)."""
    return (
        ServiceJob.objects.select_related(
            "property__address", "building__address", "unit",
            "work_order__property__address",
            "work_order__building__address", "work_order__unit",
        )
        .get(id=job_id)
    )


# --- Anlage / Hierarchie (Service) -----------------------------------------

@pytest.mark.django_db
def test_freier_termin_mit_gebaeude_und_einheit(app_user):
    prop = _property(app_user)
    b = _building(app_user, prop, number="A", name="Haus A")
    u = _unit(app_user, prop, b)
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=prop.id,
        building_id=b.id, unit_id=u.id,
    )
    assert job.property_id == prop.id
    assert job.building_id == b.id
    assert job.unit_id == u.id


@pytest.mark.django_db
def test_einheit_ohne_gebaeude_scheitert(app_user):
    prop = _property(app_user)
    b = _building(app_user, prop)
    u = _unit(app_user, prop, b)
    with pytest.raises(ValueError, match="Einheit setzt ein Gebäude"):
        einsatz_service.create_service_job(
            app_user.id, title="Begehung", property_id=prop.id, unit_id=u.id
        )


@pytest.mark.django_db
def test_gebaeude_ohne_liegenschaft_scheitert(app_user):
    prop = _property(app_user)
    b = _building(app_user, prop)
    with pytest.raises(ValueError, match="setzt eine Liegenschaft"):
        einsatz_service.create_service_job(
            app_user.id, title="Begehung", building_id=b.id
        )


@pytest.mark.django_db
def test_gebaeude_fremder_liegenschaft_scheitert(app_user):
    prop = _property(app_user)
    fremd = _property(app_user, name="Fremd")
    b_fremd = _building(app_user, fremd)
    with pytest.raises(ValueError, match="nicht zur angegebenen Liegenschaft"):
        einsatz_service.create_service_job(
            app_user.id, title="Begehung", property_id=prop.id, building_id=b_fremd.id
        )


@pytest.mark.django_db
def test_einheit_fremden_gebaeudes_scheitert(app_user):
    prop = _property(app_user)
    b1 = _building(app_user, prop, number="A")
    b2 = _building(app_user, prop, number="B")
    u2 = _unit(app_user, prop, b2)
    with pytest.raises(ValueError, match="nicht zum angegebenen Gebäude"):
        einsatz_service.create_service_job(
            app_user.id, title="Begehung", property_id=prop.id,
            building_id=b1.id, unit_id=u2.id,
        )


@pytest.mark.django_db
def test_auftragsgebunden_mit_gebaeude_und_einheit(app_user):
    """Am gebundenen Termin: property = Auftragsliegenschaft, dann Gebäude/Einheit."""
    prop = _property(app_user)
    order = _order(app_user, prop)
    b = _building(app_user, prop)
    u = _unit(app_user, prop, b)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, property_id=prop.id,
        building_id=b.id, unit_id=u.id,
    )
    assert job.building_id == b.id
    assert job.unit_id == u.id


# --- Hierarchie physisch in der DB -----------------------------------------

@pytest.mark.django_db
def test_db_check_blockt_gebaeude_ohne_liegenschaft(app_user):
    """service_job_building_needs_property schließt die MATCH-SIMPLE-Lücke: ein
    Gebäude ohne property_id ist physisch unmöglich (sonst wäre der (building_id,
    property_id)-FK stumm und ein fremdes Gebäude anhängbar)."""
    prop = _property(app_user)
    b = _building(app_user, prop)
    with pytest.raises(Exception):  # CheckViolation
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO workflow.service_job (id, title, building_id) "
                "VALUES (%s, %s, %s)",
                [str(uuid.uuid4()), "Begehung", str(b.id)],
            )


@pytest.mark.django_db
def test_db_fk_blockt_fremdes_gebaeude(app_user):
    """Der zusammengesetzte FK (building_id, property_id) ist physisch."""
    prop = _property(app_user)
    fremd = _property(app_user, name="Fremd")
    b_fremd = _building(app_user, fremd)
    with pytest.raises(Exception):  # ForeignKeyViolation
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO workflow.service_job "
                "(id, title, property_id, building_id) VALUES (%s, %s, %s, %s)",
                [str(uuid.uuid4()), "Begehung", str(prop.id), str(b_fremd.id)],
            )


# --- Teil-Update: Zielzustand ----------------------------------------------

@pytest.mark.django_db
def test_ort_nachtragen(app_user):
    prop = _property(app_user)
    b = _building(app_user, prop)
    u = _unit(app_user, prop, b)
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    job = einsatz_service.update_service_job(
        app_user.id, service_job_id=job.id,
        property_id=prop.id, building_id=b.id, unit_id=u.id,
    )
    assert (job.property_id, job.building_id, job.unit_id) == (prop.id, b.id, u.id)


@pytest.mark.django_db
def test_liegenschaft_wechseln_ohne_gebaeude_zu_raeumen_scheitert(app_user):
    """Nur die Liegenschaft ändern, das (alte) Gebäude stehen lassen → der
    Zielzustand ist inkonsistent und scheitert als klarer 422."""
    prop = _property(app_user)
    b = _building(app_user, prop)
    andere = _property(app_user, name="Andere")
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=prop.id, building_id=b.id
    )
    with pytest.raises(ValueError, match="nicht zur angegebenen Liegenschaft"):
        einsatz_service.update_service_job(
            app_user.id, service_job_id=job.id, property_id=andere.id
        )


@pytest.mark.django_db
def test_nur_einheit_aendern_gegen_bestandsgebaeude(app_user):
    """Nur die Einheit setzen — sie wird gegen das bereits gespeicherte Gebäude
    geprüft (Zielzustand), nicht gegen ein leeres Payload-Gebäude."""
    prop = _property(app_user)
    b = _building(app_user, prop)
    u = _unit(app_user, prop, b)
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=prop.id, building_id=b.id
    )
    job = einsatz_service.update_service_job(
        app_user.id, service_job_id=job.id, unit_id=u.id
    )
    assert job.unit_id == u.id
    assert job.building_id == b.id


@pytest.mark.django_db
def test_gebaeude_wechseln_alte_einheit_stehenlassen_scheitert(app_user):
    """Gebäude wechseln, die (zum alten Gebäude gehörende) Einheit stehen lassen →
    der Zielzustand ist inkonsistent und endet als 422, nicht als DB-500."""
    prop = _property(app_user)
    b1 = _building(app_user, prop, number="A")
    u1 = _unit(app_user, prop, b1)
    b2 = _building(app_user, prop, number="B")
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=prop.id,
        building_id=b1.id, unit_id=u1.id,
    )
    with pytest.raises(ValueError, match="nicht zum angegebenen Gebäude"):
        einsatz_service.update_service_job(
            app_user.id, service_job_id=job.id, building_id=b2.id
        )


@pytest.mark.django_db
def test_serie_kopiert_gebaeude_und_einheit(app_user):
    """Ein freier Serientermin an einem Gebäude/einer Einheit darf seinen präzisen
    Ort nicht verlieren — jeder Folgetermin trägt Gebäude UND Einheit."""
    from datetime import datetime, timezone as dt_timezone
    from db_core.services import planung as planung_service

    prop = _property(app_user)
    b = _building(app_user, prop, name="Haus A")
    u = _unit(app_user, prop, b)
    start = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
    job = einsatz_service.create_service_job(
        app_user.id, title="Wöchentliche Begehung", property_id=prop.id,
        building_id=b.id, unit_id=u.id, scheduled_start=start,
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="GEPLANT"
    )
    ergebnis = planung_service.serie_anlegen(
        app_user.id, service_job_id=job.id, intervall="WOECHENTLICH", anzahl=2
    )
    for neu in ergebnis["erzeugt"]:
        neu.refresh_from_db()
        assert neu.building_id == b.id
        assert neu.unit_id == u.id


@pytest.mark.django_db
def test_ort_entfernen(app_user):
    prop = _property(app_user)
    b = _building(app_user, prop)
    u = _unit(app_user, prop, b)
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=prop.id,
        building_id=b.id, unit_id=u.id,
    )
    job = einsatz_service.update_service_job(
        app_user.id, service_job_id=job.id, unit_id=None, building_id=None
    )
    assert job.building_id is None
    assert job.unit_id is None


# --- Ortsauflösung der Anzeige (api/planung) -------------------------------

@pytest.mark.django_db
def test_aufloesung_bevorzugt_gebaeudeadresse(app_user):
    """Hat das Gebäude eine eigene Anschrift, zeigt die Karte DIESE (nicht die der
    Liegenschaft) — plus die Einheit als Zusatz."""
    prop = _property(app_user, street="Albrechtstraße", city="Berlin")
    haus_adr = _adresse(app_user, street="Steglitzer Damm", house_number="12")
    b = _building(app_user, prop, name="Haus Steglitz", address_id=haus_adr.id)
    u = _unit(app_user, prop, b)
    job_id = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=prop.id,
        building_id=b.id, unit_id=u.id,
    ).id
    job = _lade(job_id)

    ref = planung_api._property_ref(job)
    assert ref.street == "Steglitzer Damm"      # aus der Gebäudeadresse
    assert ref.building == "Haus Steglitz"
    assert ref.unit == "3. OG rechts"

    prop_r, b_r, u_r = planung_api._job_ort(job)
    kurz = planung_api._ort_adresse_kurz(prop_r, b_r, u_r)
    assert "Steglitzer Damm 12" in kurz
    assert kurz.endswith("3. OG rechts")


@pytest.mark.django_db
def test_aufloesung_faellt_auf_liegenschaft_zurueck(app_user):
    """Ohne eigene Gebäudeadresse steht die Liegenschaftsadresse; das Gebäude wird
    dann als Zusatz genannt (sonst wäre es unsichtbar)."""
    prop = _property(app_user, street="Albrechtstraße", city="Berlin")
    b = _building(app_user, prop, number="B", name="Haus B")  # keine eigene Adresse
    u = _unit(app_user, prop, b)
    job = _lade(
        einsatz_service.create_service_job(
            app_user.id, title="Begehung", property_id=prop.id,
            building_id=b.id, unit_id=u.id,
        ).id
    )
    ref = planung_api._property_ref(job)
    assert ref.street == "Albrechtstraße"       # Fallback Liegenschaft
    assert ref.building == "Haus B"
    prop_r, b_r, u_r = planung_api._job_ort(job)
    kurz = planung_api._ort_adresse_kurz(prop_r, b_r, u_r)
    assert "Haus B" in kurz and kurz.endswith("3. OG rechts")


@pytest.mark.django_db
def test_gebundener_termin_erbt_ort_vom_auftrag(app_user):
    """Trägt der Einsatz selbst kein Gebäude, erbt die Anzeige es vom Auftrag."""
    prop = _property(app_user)
    order = _order(app_user, prop)
    b = _building(app_user, prop, name="Auftragshaus")
    u = _unit(app_user, prop, b)
    # Auftrag bekommt Gebäude/Einheit (create_work_order nimmt sie nicht entgegen).
    with business_transaction(app_user.id):
        WorkOrder.objects.filter(id=order.id).update(building_id=b.id, unit_id=u.id)
    job = _lade(
        einsatz_service.create_service_job(
            app_user.id, work_order_id=order.id
        ).id
    )
    assert job.building_id is None               # der Einsatz selbst trägt nichts
    prop_r, b_r, u_r = planung_api._job_ort(job)
    assert b_r is not None and b_r.id == b.id     # geerbt
    assert u_r.id == u.id
    ref = planung_api._property_ref(job)
    assert ref.building == "Auftragshaus"
    assert ref.unit == "3. OG rechts"
