"""Rückfrage an der Aufgabe + Benachrichtigungen (Migration 0137).

Geprüfte Zusagen:
  * Der Ersteller ERFÄHRT, dass seine Aufgabe erledigt wurde — der Befund, der
    diesen Slice ausgelöst hat.
  * Eine Rückfrage steht am Datensatz und erreicht die Gegenseite.
  * Niemand benachrichtigt sich selbst (weder Service noch DB lassen es zu).
  * Das Postfach ist persönlich: Es gibt keinen Weg, ein fremdes zu lesen.
  * Der Faden ist append-only — auch der Verfasser kann nichts nachträglich
    umschreiben.
"""
import uuid

import pytest
from django.db import transaction
from django.db.utils import ProgrammingError

from db_core.db_context import business_transaction
from db_core.models import Notification, TaskComment
from db_core.services import aufgabe as aufgabe_service
from db_core.services import benachrichtigung as benachrichtigung_service

from .conftest import make_role_user


def _client(role="ADMINISTRATION"):
    """Eingeloggter Client samt seinem app_user (den braucht fast jeder Test)."""
    from django.test import Client

    user, app_user = make_role_user(role)
    client = Client()
    client.force_login(user)
    return client, app_user


def _postfach(app_user_id):
    return list(
        Notification.objects.filter(recipient_id=app_user_id).order_by("created_at")
    )


# --- Der eigentliche Befund -------------------------------------------------

@pytest.mark.django_db
def test_ersteller_erfaehrt_von_der_erledigung():
    """Büro legt an, Monteur hakt ab — das Büro bekommt eine Meldung."""
    _, buero = _client()
    _, monteur = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Therme prüfen", assigned_to_user_id=monteur.id
    )

    aufgabe_service.complete_task(monteur.id, task.id)

    meldungen = _postfach(buero.id)
    assert [m.kind for m in meldungen] == ["AUFGABE_ERLEDIGT"]
    assert meldungen[0].title == "Therme prüfen"
    assert meldungen[0].target_type == "workflow.task"
    assert meldungen[0].target_id == task.id
    assert meldungen[0].read_at is None
    # Der Erlediger selbst bekommt für seine eigene Tat nichts — in seinem
    # Postfach steht nur die Zuweisung von vorhin.
    assert [m.kind for m in _postfach(monteur.id)] == ["AUFGABE_ZUGEWIESEN"]


@pytest.mark.django_db
def test_zuweisung_meldet_dem_zustaendigen():
    _, buero = _client()
    _, monteur = _client()
    aufgabe_service.create_task(
        buero.id, title="Zähler ablesen", assigned_to_user_id=monteur.id
    )
    assert [m.kind for m in _postfach(monteur.id)] == ["AUFGABE_ZUGEWIESEN"]
    assert _postfach(buero.id) == []


@pytest.mark.django_db
def test_eigene_aufgabe_meldet_nichts():
    """Wer sich selbst etwas notiert und abhakt, bekommt keinen roten Punkt."""
    _, ich = _client()
    task = aufgabe_service.create_task(
        ich.id, title="Selbstnotiz", assigned_to_user_id=ich.id
    )
    aufgabe_service.complete_task(ich.id, task.id)
    assert _postfach(ich.id) == []


@pytest.mark.django_db
def test_umhaengen_meldet_dem_neuen_zustaendigen():
    _, buero = _client()
    _, alt = _client()
    _, neu = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Umhängen", assigned_to_user_id=alt.id
    )

    aufgabe_service.update_task(buero.id, task.id, assigned_to_user_id=neu.id)

    assert [m.kind for m in _postfach(neu.id)] == ["AUFGABE_ZUGEWIESEN"]
    vermerke = [k.body for k in aufgabe_service.kommentare(task.id) if k.kind == "SYSTEM"]
    assert any("Zuständigkeit gewechselt" in v for v in vermerke)


@pytest.mark.django_db
def test_bisheriger_zustaendiger_erfaehrt_vom_entzug():
    """Sonst verschwände die Aufgabe signallos aus seiner Liste."""
    _, buero = _client()
    _, alt = _client()
    _, neu = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Wandert weiter", assigned_to_user_id=alt.id
    )

    aufgabe_service.update_task(buero.id, task.id, assigned_to_user_id=neu.id)

    arten = [m.kind for m in _postfach(alt.id)]
    assert arten == ["AUFGABE_ZUGEWIESEN", "AUFGABE_ENTZOGEN"]
    entzug = _postfach(alt.id)[-1]
    # Nichts Neues im Text: nur wer jetzt zuständig ist, kein Auszug aus dem
    # Faden — den darf er nach dem Entzug nicht mehr lesen.
    assert "übertragen" in entzug.body


@pytest.mark.django_db
def test_entzug_traegt_den_alten_titel():
    """Ein PATCH kann Titel UND Zuständigkeit ändern. Der Entzogene darf dann
    nicht den neuen Titel zu sehen bekommen — den kannte er nie und kann ihn
    nach dem Entzug auch nicht mehr nachschlagen."""
    _, buero = _client()
    _, alt = _client()
    _, neu = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Alter Titel", assigned_to_user_id=alt.id
    )

    aufgabe_service.update_task(
        buero.id, task.id, title="Mahnung Müller 12.000 €", assigned_to_user_id=neu.id
    )

    entzug = [m for m in _postfach(alt.id) if m.kind == "AUFGABE_ENTZOGEN"][0]
    assert entzug.title == "Alter Titel"
    # Der neue Zuständige sieht dagegen den aktuellen Titel.
    zuweisung = [m for m in _postfach(neu.id) if m.kind == "AUFGABE_ZUGEWIESEN"][0]
    assert zuweisung.title == "Mahnung Müller 12.000 €"


@pytest.mark.django_db
def test_ersteller_mit_scope_eigene_behaelt_zugriff():
    """Wer eine Aufgabe stellt, darf sie sehen — auch nach dem Umhängen.

    Sonst bekäme er Meldungen (samt Auszug aus einer Rückfrage) zu einer
    Aufgabe, die ihm die API mit 404 verweigert.
    """
    from django.test import Client

    monteur_user, monteur = make_role_user("MONTEUR")
    monteur_client = Client()
    monteur_client.force_login(monteur_user)
    _, kollege = _client()

    # Der Monteur legt selbst an (der Server erzwingt Selbstzuweisung) …
    r = monteur_client.post(
        "/api/workflow/tasks",
        {"title": "Ersatzteil bestellen"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    task_id = r.json()["id"]

    # … das Büro hängt sie um.
    _, buero = _client()
    aufgabe_service.update_task(buero.id, task_id, assigned_to_user_id=kollege.id)

    # Er sieht sie weiter — in der Liste und im Detail.
    assert monteur_client.get(f"/api/workflow/tasks/{task_id}").status_code == 200
    assert monteur_client.get(f"/api/workflow/tasks/{task_id}/comments").status_code == 200
    titel = {i["title"] for i in monteur_client.get("/api/workflow/tasks").json()["items"]}
    assert "Ersatzteil bestellen" in titel


@pytest.mark.django_db
def test_fremde_aufgabe_bleibt_fuer_scope_eigene_verschlossen():
    from django.test import Client

    monteur_user, _ = make_role_user("MONTEUR")
    monteur_client = Client()
    monteur_client.force_login(monteur_user)

    _, buero = _client()
    _, dritter = _client()
    fremd = aufgabe_service.create_task(
        buero.id, title="Geht ihn nichts an", assigned_to_user_id=dritter.id
    )

    assert monteur_client.get(f"/api/workflow/tasks/{fremd.id}").status_code == 404
    assert monteur_client.get(f"/api/workflow/tasks/{fremd.id}/comments").status_code == 404
    r = monteur_client.post(
        f"/api/workflow/tasks/{fremd.id}/comments",
        {"body": "Mitlesen?"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_gleiche_zuweisung_meldet_nicht_erneut():
    _, buero = _client()
    _, monteur = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Nur Titel ändern", assigned_to_user_id=monteur.id
    )
    aufgabe_service.update_task(
        buero.id, task.id, title="Neuer Titel", assigned_to_user_id=monteur.id
    )
    assert len(_postfach(monteur.id)) == 1


# --- Rückfragen -------------------------------------------------------------

@pytest.mark.django_db
def test_rueckfrage_erreicht_die_gegenseite():
    client_monteur, monteur = _client()
    _, buero = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Ventil tauschen", assigned_to_user_id=monteur.id
    )

    r = client_monteur.post(
        f"/api/workflow/tasks/{task.id}/comments",
        {"body": "Welches Fabrikat soll rein?"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["kind"] == "KOMMENTAR"
    assert r.json()["created_by"]["id"] == str(monteur.id)

    meldungen = [m for m in _postfach(buero.id) if m.kind == "AUFGABE_KOMMENTAR"]
    assert len(meldungen) == 1
    assert "Welches Fabrikat" in meldungen[0].body


@pytest.mark.django_db
def test_leerer_kommentar_ist_422():
    client, app_user = _client()
    task = aufgabe_service.create_task(app_user.id, title="X")
    r = client.post(
        f"/api/workflow/tasks/{task.id}/comments",
        {"body": "   "},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_zu_langer_kommentar_ist_422():
    """Append-only: Ein Riesentext bliebe für immer stehen."""
    client, app_user = _client()
    task = aufgabe_service.create_task(app_user.id, title="Grenze")
    r = client.post(
        f"/api/workflow/tasks/{task.id}/comments",
        {"body": "x" * (aufgabe_service.MAX_KOMMENTAR_ZEICHEN + 1)},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_zwei_eintraege_derselben_transaktion_sind_geordnet():
    """clock_timestamp statt now(): sonst wären beide Zeitstempel bitgleich und
    die Reihenfolge des Fadens fiele auf eine zufällige UUID zurück."""
    _, app_user = _client()
    task = aufgabe_service.create_task(app_user.id, title="Ordnung")
    with business_transaction(app_user.id):
        for text in ("erst dies", "dann das"):
            TaskComment.objects.create(
                id=uuid.uuid4(),
                task_id=task.id,
                kind="KOMMENTAR",
                body=text,
                created_by_id=app_user.id,
            )
    eintraege = aufgabe_service.kommentare(task.id)
    assert [k.body for k in eintraege] == ["erst dies", "dann das"]
    assert eintraege[0].created_at < eintraege[1].created_at


@pytest.mark.django_db
def test_faden_enthaelt_systemvermerk_der_erledigung():
    client, app_user = _client()
    task = aufgabe_service.create_task(app_user.id, title="Mit Verlauf")
    aufgabe_service.complete_task(app_user.id, task.id)

    r = client.get(f"/api/workflow/tasks/{task.id}/comments")
    assert r.status_code == 200
    eintraege = r.json()
    assert [e["kind"] for e in eintraege] == ["SYSTEM"]
    assert eintraege[0]["body"] == "Als erledigt markiert."


@pytest.mark.django_db
def test_kommentar_ist_unveraenderlich():
    """Append-only: Der DB-Trigger verbietet auch dem Verfasser das Umschreiben."""
    _, app_user = _client()
    task = aufgabe_service.create_task(app_user.id, title="Fest")
    kommentar = aufgabe_service.kommentieren(app_user.id, task.id, "So war es.")

    with pytest.raises(ProgrammingError):
        # Savepoint: Der Trigger bricht die Transaktion ab; ohne eigenen
        # Savepoint risse er die ganze Testtransaktion mit.
        with transaction.atomic():
            with business_transaction(app_user.id):
                TaskComment.objects.filter(id=kommentar.id).update(body="Doch nicht.")


@pytest.mark.django_db
def test_detailabruf_liefert_ersteller():
    client, app_user = _client()
    task = aufgabe_service.create_task(app_user.id, title="Mit Ersteller")
    r = client.get(f"/api/workflow/tasks/{task.id}")
    assert r.status_code == 200
    assert r.json()["created_by"]["id"] == str(app_user.id)


@pytest.mark.django_db
def test_detailabruf_unbekannt_ist_404():
    client, _ = _client()
    r = client.get(f"/api/workflow/tasks/{uuid.uuid4()}")
    assert r.status_code == 404


# --- Das Postfach -----------------------------------------------------------

@pytest.mark.django_db
def test_postfach_zeigt_nur_eigene_meldungen():
    client_buero, buero = _client()
    client_monteur, monteur = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Nur meins", assigned_to_user_id=monteur.id
    )
    aufgabe_service.complete_task(monteur.id, task.id)

    r = client_buero.get("/api/benachrichtigungen")
    assert r.status_code == 200
    body = r.json()
    assert body["ungelesen"] == 1
    assert [i["kind"] for i in body["items"]] == ["AUFGABE_ERLEDIGT"]

    # Der Monteur sieht in seinem Postfach die Zuweisung — und nur sie.
    r2 = client_monteur.get("/api/benachrichtigungen")
    assert [i["kind"] for i in r2.json()["items"]] == ["AUFGABE_ZUGEWIESEN"]

    # Ein Unbeteiligter sieht gar nichts.
    fremder, _ = _client()
    assert fremder.get("/api/benachrichtigungen").json()["items"] == []


@pytest.mark.django_db
def test_gelesen_senkt_den_zaehler():
    client_buero, buero = _client()
    _, monteur = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Lesen", assigned_to_user_id=monteur.id
    )
    aufgabe_service.complete_task(monteur.id, task.id)
    meldung = _postfach(buero.id)[0]

    r = client_buero.post(f"/api/benachrichtigungen/{meldung.id}/gelesen")
    assert r.status_code == 200
    assert r.json()["ungelesen"] == 0
    # Idempotent: der zweite Klick ist kein Fehler.
    assert client_buero.post(f"/api/benachrichtigungen/{meldung.id}/gelesen").status_code == 200


@pytest.mark.django_db
def test_fremde_meldung_laesst_sich_nicht_lesen():
    """Wirkungslos statt 404 — ein 404 verriete, dass es die Zeile gibt."""
    _, buero = _client()
    _, monteur = _client()
    fremder_client, _ = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Fremd", assigned_to_user_id=monteur.id
    )
    aufgabe_service.complete_task(monteur.id, task.id)
    meldung = _postfach(buero.id)[0]

    r = fremder_client.post(f"/api/benachrichtigungen/{meldung.id}/gelesen")
    assert r.status_code == 200
    meldung.refresh_from_db()
    assert meldung.read_at is None


@pytest.mark.django_db
def test_alle_gelesen():
    client_buero, buero = _client()
    _, monteur = _client()
    for titel in ("A", "B"):
        t = aufgabe_service.create_task(
            buero.id, title=titel, assigned_to_user_id=monteur.id
        )
        aufgabe_service.complete_task(monteur.id, t.id)

    assert client_buero.get("/api/benachrichtigungen/zaehler").json()["ungelesen"] == 2
    r = client_buero.post("/api/benachrichtigungen/alle-gelesen")
    assert r.json()["ungelesen"] == 0


@pytest.mark.django_db
def test_db_verbietet_selbstbenachrichtigung():
    """Der Riegel liegt in der DB, nicht nur im Service."""
    _, app_user = _client()
    with pytest.raises(Exception):
        with transaction.atomic():
            with business_transaction(app_user.id):
                Notification.objects.create(
                    id=uuid.uuid4(),
                    recipient_id=app_user.id,
                    kind="AUFGABE_ERLEDIGT",
                    title="Ich mir selbst",
                    target_type="workflow.task",
                    target_id=uuid.uuid4(),
                    triggered_by_id=app_user.id,
                    version=1,
                )


@pytest.mark.django_db
def test_nur_lesestatus_ist_aenderbar():
    _, buero = _client()
    _, monteur = _client()
    task = aufgabe_service.create_task(
        buero.id, title="Fest verdrahtet", assigned_to_user_id=monteur.id
    )
    aufgabe_service.complete_task(monteur.id, task.id)
    meldung = _postfach(buero.id)[0]

    with pytest.raises(ProgrammingError):
        with transaction.atomic():
            with business_transaction(buero.id):
                Notification.objects.filter(id=meldung.id).update(title="Umgeschrieben")

    # Der Lesestatus dagegen darf sich ändern.
    benachrichtigung_service.als_gelesen(buero.id, meldung.id)
    meldung.refresh_from_db()
    assert meldung.read_at is not None
