"""Service-Tests der Planungs-Stammdaten (workflow.appointment_category,
resource.resource, resource.job_resource) gegen die echte Test-DB.

Prüft Anlage/Validierung/Statusautomaten, den Schutzstandard (physisches DELETE
scheitert am Trigger), die Kategorie-Zuordnung am Einsatz und die
Doppelbelegungs-Warnung (KEIN EXCLUDE — offene Invariante, siehe Migration 0025).
"""
from datetime import datetime, timezone as dt_timezone

import pytest
from django.db import Error, transaction

from db_core.models import AppointmentCategory, JobResource, Resource
from db_core.services import einsatz as einsatz_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service
from db_core.services import identity as identity_service
from db_core.services import auftrag as auftrag_service

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)
T2 = datetime(2026, 7, 13, 10, 0, tzinfo=dt_timezone.utc)  # überlappt [T0, T1)
T3 = datetime(2026, 7, 13, 14, 0, tzinfo=dt_timezone.utc)


def _order(app_user, title="Auftrag"):
    obj = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    return auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title=title
    )


def _job(app_user, order, *, start=None, end=None):
    return einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=start, scheduled_end=end,
    )


# ===========================================================================
# Terminkategorie
# ===========================================================================

@pytest.mark.django_db
def test_create_category_defaults(app_user):
    c = planung_service.create_category(app_user.id, name="Vor-Ort-Termin")
    assert c.status == "AKTIV"
    assert c.color_token == "NAVY"
    assert c.sort_order == 0


@pytest.mark.django_db
def test_create_category_farbe(app_user):
    c = planung_service.create_category(
        app_user.id, name="Büro", color_token="AMBER", sort_order=3
    )
    assert c.color_token == "AMBER"
    assert c.sort_order == 3


@pytest.mark.django_db
def test_create_category_ungueltige_farbe(app_user):
    with pytest.raises(ValueError):
        planung_service.create_category(
            app_user.id, name="X", color_token="KNALLROT"
        )


@pytest.mark.django_db
def test_create_category_leerer_name(app_user):
    with pytest.raises(ValueError):
        planung_service.create_category(app_user.id, name="   ")


@pytest.mark.django_db
def test_category_aktiver_name_eindeutig(app_user):
    planung_service.create_category(app_user.id, name="Umsetzung")
    # Gleicher Name (case-insensitiv) als AKTIV → Unique-Index (→ Trigger/DB → 422).
    with pytest.raises(ValueError):
        planung_service.create_category(app_user.id, name="umsetzung")


@pytest.mark.django_db
def test_archive_category(app_user):
    c = planung_service.create_category(app_user.id, name="Schule")
    planung_service.archive_category(app_user.id, category_id=c.id)
    c.refresh_from_db()
    assert c.status == "ARCHIVIERT"
    # Name nach Archivieren wieder frei vergebbar.
    c2 = planung_service.create_category(app_user.id, name="Schule")
    assert c2.status == "AKTIV"


@pytest.mark.django_db
def test_archive_category_doppelt(app_user):
    c = planung_service.create_category(app_user.id, name="Schule")
    planung_service.archive_category(app_user.id, category_id=c.id)
    with pytest.raises(ValueError):
        planung_service.archive_category(app_user.id, category_id=c.id)


@pytest.mark.django_db
def test_category_delete_verboten(app_user):
    """Schutzstandard: physisches DELETE scheitert am No-Delete-Trigger."""
    c = planung_service.create_category(app_user.id, name="Nix löschen")
    with pytest.raises(Error):
        with transaction.atomic():
            AppointmentCategory.objects.filter(id=c.id).delete()


# ===========================================================================
# Ressource
# ===========================================================================

@pytest.mark.django_db
def test_create_resource(app_user):
    r = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    assert r.status == "AKTIV"
    assert r.resource_number.startswith("RES-")
    assert r.resource_type == "FAHRZEUG"


@pytest.mark.django_db
def test_create_resource_ungueltiger_typ(app_user):
    with pytest.raises(ValueError):
        planung_service.create_resource(
            app_user.id, name="X", resource_type="RAKETE"
        )


@pytest.mark.django_db
def test_resource_status_automat(app_user):
    r = planung_service.create_resource(
        app_user.id, name="Bohrhammer", resource_type="GERAET"
    )
    planung_service.set_resource_status(
        app_user.id, resource_id=r.id, to_status="INAKTIV"
    )
    r.refresh_from_db()
    assert r.status == "INAKTIV"
    planung_service.set_resource_status(
        app_user.id, resource_id=r.id, to_status="ARCHIVIERT"
    )
    r.refresh_from_db()
    assert r.status == "ARCHIVIERT"


@pytest.mark.django_db
def test_resource_status_unzulaessig(app_user):
    r = planung_service.create_resource(
        app_user.id, name="Bohrhammer", resource_type="GERAET"
    )
    # AKTIV -> ARCHIVIERT direkt ist nicht erlaubt (nur über INAKTIV).
    with pytest.raises(ValueError):
        planung_service.set_resource_status(
            app_user.id, resource_id=r.id, to_status="ARCHIVIERT"
        )


@pytest.mark.django_db
def test_resource_delete_verboten(app_user):
    """Schutzstandard: physisches DELETE der Ressource scheitert am Trigger."""
    r = planung_service.create_resource(
        app_user.id, name="Anhänger", resource_type="FAHRZEUG"
    )
    with pytest.raises(Error):
        with transaction.atomic():
            Resource.objects.filter(id=r.id).delete()


# ===========================================================================
# Kategorie-Zuordnung am Einsatz
# ===========================================================================

@pytest.mark.django_db
def test_kategorie_am_einsatz(app_user):
    order = _order(app_user)
    cat = planung_service.create_category(app_user.id, name="Vor-Ort-Termin")
    job = _job(app_user, order)
    planung_service.set_job_category(
        app_user.id, service_job_id=job.id, category_id=cat.id
    )
    job.refresh_from_db()
    assert job.appointment_category_id == cat.id
    # Entfernen (None).
    planung_service.set_job_category(
        app_user.id, service_job_id=job.id, category_id=None
    )
    job.refresh_from_db()
    assert job.appointment_category_id is None


@pytest.mark.django_db
def test_kategorie_am_einsatz_beim_anlegen(app_user):
    order = _order(app_user)
    cat = planung_service.create_category(app_user.id, name="Büro")
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, appointment_category_id=cat.id
    )
    assert job.appointment_category_id == cat.id


@pytest.mark.django_db
def test_kategorie_archiviert_nicht_zuweisbar(app_user):
    order = _order(app_user)
    cat = planung_service.create_category(app_user.id, name="Alt")
    planung_service.archive_category(app_user.id, category_id=cat.id)
    job = _job(app_user, order)
    with pytest.raises(ValueError):
        planung_service.set_job_category(
            app_user.id, service_job_id=job.id, category_id=cat.id
        )


# ===========================================================================
# Ressourcen-Zuordnung am Einsatz + Doppelbelegung (weiche Warnung)
# ===========================================================================

@pytest.mark.django_db
def test_ressource_zuordnen(app_user):
    order = _order(app_user)
    job = _job(app_user, order, start=T0, end=T1)
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    link, warnings = planung_service.assign_resource(
        app_user.id, service_job_id=job.id, resource_id=res.id
    )
    assert link.resource_id == res.id
    assert warnings == []
    assert JobResource.objects.filter(
        service_job_id=job.id, resource_id=res.id
    ).exists()


@pytest.mark.django_db
def test_ressource_doppelzuordnung_verboten(app_user):
    order = _order(app_user)
    job = _job(app_user, order)
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    planung_service.assign_resource(
        app_user.id, service_job_id=job.id, resource_id=res.id
    )
    # Dieselbe Ressource ein zweites Mal an denselben Einsatz → UNIQUE.
    with pytest.raises(ValueError):
        planung_service.assign_resource(
            app_user.id, service_job_id=job.id, resource_id=res.id
        )


@pytest.mark.django_db
def test_ressource_doppelbelegung_warnt_aber_blockt_nicht(app_user):
    """Offene Invariante (kein EXCLUDE): überlappende Zeitfenster derselben
    Ressource lösen einen nicht-blockierenden Warnhinweis aus — die Zuordnung
    wird trotzdem angelegt (Hero-Parität)."""
    order = _order(app_user)
    job_a = _job(app_user, order, start=T0, end=T1)  # 08–12
    job_b = _job(app_user, order, start=T2, end=T3)  # 10–14 (überlappt)
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    planung_service.assign_resource(
        app_user.id, service_job_id=job_a.id, resource_id=res.id
    )
    link, warnings = planung_service.assign_resource(
        app_user.id, service_job_id=job_b.id, resource_id=res.id
    )
    # Trotz Warnung angelegt.
    assert link.resource_id == res.id
    assert len(warnings) == 1
    assert "Doppelbelegung" in warnings[0]


@pytest.mark.django_db
def test_ressource_keine_warnung_ohne_zeitfenster(app_user):
    """Bei unvollständigem/nullable Zeitraum wird bewusst NICHT gewarnt."""
    order = _order(app_user)
    job_a = _job(app_user, order)  # keine Zeit
    job_b = _job(app_user, order)  # keine Zeit
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    planung_service.assign_resource(
        app_user.id, service_job_id=job_a.id, resource_id=res.id
    )
    _link, warnings = planung_service.assign_resource(
        app_user.id, service_job_id=job_b.id, resource_id=res.id
    )
    assert warnings == []


@pytest.mark.django_db
def test_ressource_entfernen(app_user):
    order = _order(app_user)
    job = _job(app_user, order)
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    planung_service.assign_resource(
        app_user.id, service_job_id=job.id, resource_id=res.id
    )
    planung_service.unassign_resource(
        app_user.id, service_job_id=job.id, resource_id=res.id
    )
    assert not JobResource.objects.filter(
        service_job_id=job.id, resource_id=res.id
    ).exists()


@pytest.mark.django_db
def test_archivierte_ressource_nicht_zuordenbar(app_user):
    order = _order(app_user)
    job = _job(app_user, order)
    res = planung_service.create_resource(
        app_user.id, name="Alt", resource_type="GERAET"
    )
    planung_service.set_resource_status(
        app_user.id, resource_id=res.id, to_status="INAKTIV"
    )
    with pytest.raises(ValueError):
        planung_service.assign_resource(
            app_user.id, service_job_id=job.id, resource_id=res.id
        )
