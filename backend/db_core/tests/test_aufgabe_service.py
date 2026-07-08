"""Service-Tests der Aufgaben-Schicht gegen die echte Test-DB.

workflow.task-Trigger sind scharf: No-Delete, No-Merged, Erledigt-CHECKs.
"""
import pytest

from django.db import Error, transaction

from db_core.models import Party, Task
from db_core.db_context import business_transaction
from db_core.services import aufgabe as aufgabe_service
from db_core.services import identity as identity_service
from db_core.services import projekt as projekt_service


@pytest.mark.django_db
def test_create_task_basis(app_user):
    t = aufgabe_service.create_task(app_user.id, title="Angebot nachfassen")
    assert t.title == "Angebot nachfassen"
    assert t.status == "OFFEN"
    assert t.created_by_id == app_user.id
    assert t.completed_at is None


@pytest.mark.django_db
def test_create_task_leerer_titel(app_user):
    with pytest.raises(ValueError):
        aufgabe_service.create_task(app_user.id, title="   ")


@pytest.mark.django_db
def test_create_task_mit_projekt_und_party(app_user):
    project = projekt_service.create_project(app_user.id, name="P")
    party = identity_service.create_person(app_user.id, first_name="A", last_name="B")
    t = aufgabe_service.create_task(
        app_user.id, title="Rückruf", project_id=project.id, party_id=party.id
    )
    reloaded = Task.objects.get(id=t.id)
    assert reloaded.project_id == project.id
    assert reloaded.party_id == party.id


@pytest.mark.django_db
def test_complete_task(app_user):
    t = aufgabe_service.create_task(app_user.id, title="Erledige mich")
    done = aufgabe_service.complete_task(app_user.id, t.id)
    assert done.status == "ERLEDIGT"
    assert done.completed_by_id == app_user.id
    assert done.completed_at is not None


@pytest.mark.django_db
def test_discard_und_reopen(app_user):
    t = aufgabe_service.create_task(app_user.id, title="Verwerfe mich")
    verworfen = aufgabe_service.discard_task(app_user.id, t.id)
    assert verworfen.status == "VERWORFEN"
    assert verworfen.completed_at is None
    wieder = aufgabe_service.reopen_task(app_user.id, t.id)
    assert wieder.status == "OFFEN"


@pytest.mark.django_db
def test_task_no_delete(app_user):
    """Der Trigger trg_task_no_delete verbietet physisches Löschen."""
    t = aufgabe_service.create_task(app_user.id, title="Unlöschbar")
    with pytest.raises(Error):
        with transaction.atomic():
            Task.objects.filter(id=t.id).delete()


@pytest.mark.django_db
def test_task_merged_party_abgelehnt(app_user):
    """trg_task_no_merged verbietet Referenz auf zusammengeführte Party."""
    ziel = identity_service.create_person(app_user.id, first_name="Z", last_name="P")
    dub = identity_service.create_person(app_user.id, first_name="Alt", last_name="Dub")
    with business_transaction(app_user.id):
        Party.objects.filter(id=dub.id).update(
            status="MERGED", merged_into_party_id=ziel.id
        )
    with pytest.raises(Error):
        aufgabe_service.create_task(app_user.id, title="X", party_id=dub.id)
