"""Freier Termin ohne Auftrag (workflow.service_job, Migration 0062).

Geprüft wird gegen die echte Test-DB — also gegen die scharfen Trigger:

* Anlage ohne Auftrag (mit/ohne Titel, mit/ohne Liegenschaft),
* Statusautomat bis ABGESCHLOSSEN OHNE Auftrag (das Ausführungstor darf beim
  freien Termin nicht greifen) — und dass es beim auftragsgebundenen Einsatz
  weiterhin scharf ist,
* Konsistenz der Liegenschaft mit dem Auftrag (zusammengesetzter FK),
* Unveränderlichkeit des Auftragsbezugs (WF-01),
* Zeit-/Materialbuchung auf einem freien Termin inklusive Korrekturfenster B-28
  (der Trigger musste für den NULL-Fall auf LEFT JOIN umgestellt werden, sonst
  griffe das Fenster beim freien Termin gar nicht),
* Nachtragen des Kontakts (Begehung: Kunde noch nicht angelegt).
"""
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import pytest
from django.db import connection

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import ServiceJob
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)


def _property(app_user, name="Begehungsobjekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user, first="Erika", last="Interessentin"):
    return identity_service.create_person(app_user.id, first_name=first, last_name=last)


def _order(app_user, *, target="IN_AUSFUEHRUNG", obj=None):
    obj = obj or _property(app_user)
    principal = _party(app_user, first="Thea", last="Auftraggeberin")
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag mit Termin"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status=to_status
        )
        if to_status == target:
            break
    order.refresh_from_db()
    return order


def _bis(app_user, job, *, target):
    chain = ["GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT", "ABGESCHLOSSEN"]
    for to_status in chain:
        einsatz_service.advance_status(
            app_user.id, service_job_id=job.id, to_status=to_status
        )
        if to_status == target:
            break
    job.refresh_from_db()
    return job


# --- Anlage ----------------------------------------------------------------

@pytest.mark.django_db
def test_freier_termin_ohne_titel_scheitert(app_user):
    """Ohne Auftrag UND ohne Titel hätte der Termin keinen fachlichen Anker."""
    with pytest.raises(ValueError, match="Titel"):
        einsatz_service.create_service_job(app_user.id)


@pytest.mark.django_db
def test_freier_termin_mit_leerem_titel_scheitert(app_user):
    """Ein Titel aus Leerzeichen zählt nicht (DB-CHECK btrim(title) <> '')."""
    with pytest.raises(ValueError, match="Titel"):
        einsatz_service.create_service_job(app_user.id, title="   ")


@pytest.mark.django_db
def test_freier_termin_mit_titel(app_user):
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung Dachgeschoss", scheduled_start=T0, scheduled_end=T1
    )
    assert job.work_order_id is None
    assert job.title == "Begehung Dachgeschoss"
    assert job.property_id is None
    assert job.on_site_contact_party_id is None
    assert job.status == "UNGEPLANT"
    assert job.job_number.startswith("E-")


@pytest.mark.django_db
def test_freier_termin_an_liegenschaft(app_user):
    obj = _property(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, title="Besichtigung", property_id=obj.id
    )
    assert job.property_id == obj.id
    assert job.work_order_id is None


@pytest.mark.django_db
def test_freier_termin_mit_unbekannter_liegenschaft_scheitert(app_user):
    import uuid as _uuid

    with pytest.raises(ValueError, match="Liegenschaft"):
        einsatz_service.create_service_job(
            app_user.id, title="Begehung", property_id=_uuid.uuid4()
        )


@pytest.mark.django_db
def test_auftragsgebunden_ohne_titel_bleibt_erlaubt(app_user):
    """Bestandsverhalten: der Titel kommt vom Auftrag."""
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    assert job.title is None
    assert job.work_order_id == order.id


# --- Liegenschafts-Konsistenz (zusammengesetzter FK) -----------------------

@pytest.mark.django_db
def test_property_muss_zum_auftrag_passen(app_user):
    order = _order(app_user)
    fremd = _property(app_user, name="Fremdes Objekt")
    with pytest.raises(ValueError, match="Liegenschaft des Auftrags"):
        einsatz_service.create_service_job(
            app_user.id, work_order_id=order.id, property_id=fremd.id
        )


@pytest.mark.django_db
def test_property_gleich_der_auftragsliegenschaft_ist_ok(app_user):
    obj = _property(app_user)
    order = _order(app_user, obj=obj)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, property_id=obj.id
    )
    assert job.property_id == obj.id


@pytest.mark.django_db
def test_db_fk_blockt_fremde_liegenschaft_auch_am_service_vorbei(app_user):
    """Der zusammengesetzte FK ist physisch — nicht nur eine Service-Prüfung."""
    import uuid as _uuid

    order = _order(app_user)
    fremd = _property(app_user, name="Fremdes Objekt")
    with pytest.raises(Exception):  # ForeignKeyViolation
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO workflow.service_job (id, work_order_id, property_id) "
                "VALUES (%s, %s, %s)",
                [str(_uuid.uuid4()), str(order.id), str(fremd.id)],
            )


# --- Statusautomat ohne Auftrag --------------------------------------------

@pytest.mark.django_db
def test_freier_termin_laeuft_bis_abgeschlossen(app_user):
    """Ohne Auftrag gibt es keine Freigabe, auf die das Ausführungstor warten
    könnte — eine Begehung findet gerade VOR der Beauftragung statt."""
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", scheduled_start=T0
    )
    _bis(app_user, job, target="ABGESCHLOSSEN")
    assert job.status == "ABGESCHLOSSEN"


@pytest.mark.django_db
def test_ausfuehrungstor_bleibt_fuer_auftragsgebundene_scharf(app_user):
    """Auftrag im ENTWURF → der Einsatz darf NICHT nach UNTERWEGS."""
    obj = _property(app_user)
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Noch nicht freigegeben"
    )
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="GEPLANT"
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="BESTAETIGT"
    )
    with pytest.raises(ValueError, match="freigegebenen Auftrag"):
        einsatz_service.advance_status(
            app_user.id, service_job_id=job.id, to_status="UNTERWEGS"
        )


@pytest.mark.django_db
def test_freier_termin_kann_ausfallen(app_user):
    job = einsatz_service.create_service_job(
        app_user.id, title="Beratung", scheduled_start=T0
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="GEPLANT"
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="AUSGEFALLEN",
        reason="Kunde abgesagt",
    )
    job.refresh_from_db()
    assert job.status == "AUSGEFALLEN"


# --- Auftragsbezug ist unveränderlich (WF-01) ------------------------------

@pytest.mark.django_db
def test_auftragsbezug_ist_unveraenderlich(app_user):
    """Ein freier Termin lässt sich nicht nachträglich an einen Auftrag hängen —
    sonst wären das INSERT-Tor (B-03/B-06) und das Ausführungstor umgehbar."""
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    with pytest.raises(ValueError, match="unveränderlich"):
        with as_business_error():
            with business_transaction(app_user.id):
                ServiceJob.objects.filter(id=job.id).update(work_order_id=order.id)


@pytest.mark.django_db
def test_auftragsbezug_kann_nicht_geloest_werden(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    with pytest.raises(ValueError, match="unveränderlich"):
        with as_business_error():
            with business_transaction(app_user.id):
                ServiceJob.objects.filter(id=job.id).update(work_order_id=None)


# --- Zeit/Material auf einem freien Termin (Korrekturfenster B-28) ---------

@pytest.mark.django_db
def test_zeit_und_material_auf_freiem_termin(app_user):
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung mit Aufmaß", scheduled_start=T0
    )
    _bis(app_user, job, target="VOR_ORT")
    eintrag = einsatz_service.log_time(
        app_user.id, service_job_id=job.id, user_id=app_user.id,
        time_type="ARBEITSZEIT", started_at=T0, ended_at=T1,
    )
    assert eintrag.service_job_id == job.id
    material = einsatz_service.log_material(
        app_user.id, service_job_id=job.id, description="Messprotokoll-Set",
        quantity=Decimal("1.000"), unit="Stk", recorded_by=app_user.id,
    )
    assert material.service_job_id == job.id


@pytest.mark.django_db
def test_b28_greift_auch_auf_freiem_termin(app_user):
    """Das Korrekturfenster muss auch OHNE Auftrag greifen.

    Der Trigger las Einsatz- und Auftragsstatus per INNER JOIN auf work_order —
    beim freien Termin liefert das keine Zeile, beide Variablen blieben NULL und
    das Fenster fiele aus. Migration 0062 stellt deshalb auf LEFT JOIN um; dieser
    Test hält das fest: nach ABGESCHLOSSEN ist eine Buchung ohne Begründung
    unzulässig."""
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", scheduled_start=T0
    )
    _bis(app_user, job, target="ABGESCHLOSSEN")
    with pytest.raises(ValueError, match="Begründung"):
        einsatz_service.log_time(
            app_user.id, service_job_id=job.id, user_id=app_user.id,
            time_type="ARBEITSZEIT", started_at=T0, ended_at=T1,
        )


# --- Kontakt/Angaben nachtragen --------------------------------------------

@pytest.mark.django_db
def test_kontakt_nachtragen(app_user):
    """Begehung: der Kontakt entsteht erst nach dem Termin."""
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    assert job.on_site_contact_party_id is None
    kontakt = _party(app_user, first="Neu", last="Kunde")
    job = einsatz_service.update_service_job(
        app_user.id, service_job_id=job.id, on_site_contact_party_id=kontakt.id
    )
    assert job.on_site_contact_party_id == kontakt.id


@pytest.mark.django_db
def test_kontakt_entfernen(app_user):
    kontakt = _party(app_user)
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", on_site_contact_party_id=kontakt.id
    )
    job = einsatz_service.update_service_job(
        app_user.id, service_job_id=job.id, on_site_contact_party_id=None
    )
    assert job.on_site_contact_party_id is None


@pytest.mark.django_db
def test_liegenschaft_nachtragen(app_user):
    obj = _property(app_user)
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    job = einsatz_service.update_service_job(
        app_user.id, service_job_id=job.id, property_id=obj.id
    )
    assert job.property_id == obj.id


@pytest.mark.django_db
def test_nachtragen_erzwingt_konsistente_liegenschaft(app_user):
    order = _order(app_user)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    fremd = _property(app_user, name="Fremdes Objekt")
    with pytest.raises(ValueError, match="Liegenschaft des Auftrags"):
        einsatz_service.update_service_job(
            app_user.id, service_job_id=job.id, property_id=fremd.id
        )


@pytest.mark.django_db
def test_freier_termin_darf_titel_nicht_verlieren(app_user):
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    with pytest.raises(ValueError, match="Titel"):
        einsatz_service.update_service_job(
            app_user.id, service_job_id=job.id, title=""
        )


@pytest.mark.django_db
def test_update_ohne_felder_ist_no_op(app_user):
    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    unveraendert = einsatz_service.update_service_job(
        app_user.id, service_job_id=job.id
    )
    assert unveraendert.title == "Begehung"


@pytest.mark.django_db
def test_update_unbekannter_einsatz(app_user):
    import uuid as _uuid

    with pytest.raises(ValueError, match="nicht gefunden"):
        einsatz_service.update_service_job(
            app_user.id, service_job_id=_uuid.uuid4(), title="X"
        )
