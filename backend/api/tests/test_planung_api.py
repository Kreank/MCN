"""API-Tests der Planungs-Endpoints (Einsätze) über den Django-Test-Client.

Read-only: Liste, Filter, Detail, 404. Setup baut über die Services einen bis
IN_AUSFUEHRUNG geschalteten Auftrag und darauf zwei Einsätze (einer vor Ort mit
Zuweisung/Zeit/Material, einer nur geplant).
"""
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from django.test import Client

from db_core.models import AppUser, JobAssignment, TimeEntry
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

from .conftest import make_role_user

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Einsatzhaus", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    principal = identity_service.create_person(
        app_user.id, first_name="Petra", last_name="Prinzipal"
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Sockelrisse setzen"
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

    # Einsatz 1: vor Ort, mit Zuweisung, Zeit und Material.
    j1 = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=T0, scheduled_end=T1,
        on_site_contact_party_id=principal.id,
        access_instructions="Schlüssel Hausmeister.",
    )
    for to_status in ("GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"):
        einsatz_service.advance_status(
            app_user.id, service_job_id=j1.id, to_status=to_status
        )
    einsatz_service.assign_user(
        app_user.id, service_job_id=j1.id, assignee_user_id=app_user.id, role="LEAD"
    )
    einsatz_service.log_time(
        app_user.id, service_job_id=j1.id, user_id=app_user.id,
        time_type="ARBEITSZEIT", started_at=T0, ended_at=T1,
    )
    einsatz_service.log_material(
        app_user.id, service_job_id=j1.id,
        description="Injektionsharz", quantity=Decimal("3.5"), unit="kg",
        recorded_by=app_user.id,
    )

    # Einsatz 2: nur geplant.
    j2 = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0, scheduled_end=T1,
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=j2.id, to_status="GEPLANT"
    )
    return {"order": order, "j1": j1, "j2": j2}


@pytest.mark.django_db
def test_liste_und_pagination(admin_client, seeded):
    r = admin_client.get("/api/planung/einsaetze?page=1&page_size=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    # Jeder Eintrag trägt den Auftragstitel und die Objekt-Referenz.
    it = body["items"][0]
    assert it["work_order"]["title"] == "Sockelrisse setzen"
    assert it["property"]["name"] == "Einsatzhaus"


@pytest.mark.django_db
def test_statusfilter(admin_client, seeded):
    r = admin_client.get("/api/planung/einsaetze?status=VOR_ORT")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "VOR_ORT"
    assert body["items"][0]["assignee_count"] == 1


@pytest.mark.django_db
def test_unbekannter_status_422(admin_client, seeded):
    r = admin_client.get("/api/planung/einsaetze?status=QUATSCH")
    assert r.status_code == 422


@pytest.mark.django_db
def test_auftragsfilter(admin_client, seeded):
    r = admin_client.get(f"/api/planung/einsaetze?work_order_id={seeded['order'].id}")
    assert r.json()["total"] == 2


@pytest.mark.django_db
def test_detail_mit_zuweisung_zeit_material(admin_client, seeded):
    r = admin_client.get(f"/api/planung/einsaetze/{seeded['j1'].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "VOR_ORT"
    assert body["job_number"].startswith("E-")
    assert body["on_site_contact"] == "Petra Prinzipal"
    assert body["access_instructions"] == "Schlüssel Hausmeister."
    assert [a["role"] for a in body["assignments"]] == ["LEAD"]
    assert body["assignments"][0]["display_name"] == "Test Sachbearbeiter"
    assert {t["time_type"] for t in body["time_entries"]} == {"ARBEITSZEIT"}
    assert body["material_entries"][0]["description"] == "Injektionsharz"
    # Vollständiger Statusverlauf UNGEPLANT→…→VOR_ORT. Die Reihenfolge lässt sich
    # hier nicht prüfen: alle Wechsel laufen in EINER pytest-Transaktion, daher
    # liefert now() (Transaktionsstartzeit) für jede Zeile denselben occurred_at
    # → Gleichstand. In der echten App (separate Transaktionen) ist die Sortierung
    # eindeutig absteigend.
    assert {h["to_status"] for h in body["history"]} == {
        "UNGEPLANT", "GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"
    }


@pytest.mark.django_db
def test_detail_404(admin_client, db):
    r = admin_client.get(f"/api/planung/einsaetze/{uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_plantafel(admin_client, seeded):
    # Beide Einsätze sind auf 2026-07-13 geplant; j1 hat eine Zuweisung, j2 nicht.
    r = admin_client.get("/api/planung/plantafel?date_from=2026-07-13&date_to=2026-07-13")
    assert r.status_code == 200
    body = r.json()
    assert len(body["jobs"]) == 2
    assert [res["display_name"] for res in body["resources"]] == ["Test Sachbearbeiter"]
    assert body["unassigned_count"] == 1
    vor_ort = next(j for j in body["jobs"] if j["status"] == "VOR_ORT")
    assert len(vor_ort["assignee_ids"]) == 1
    assert vor_ort["title"] == "Sockelrisse setzen"


@pytest.mark.django_db
def test_plantafel_range_invalid(admin_client, db):
    r = admin_client.get("/api/planung/plantafel?date_from=2026-07-20&date_to=2026-07-10")
    assert r.status_code == 422


@pytest.mark.django_db
def test_plantafel_range_zu_gross(admin_client, db):
    r = admin_client.get("/api/planung/plantafel?date_from=2026-01-01&date_to=2026-12-31")
    assert r.status_code == 422


# --- Schreibende Endpoints -------------------------------------------------

@pytest.mark.django_db
def test_einsatz_anlegen(admin_client, seeded):
    """create_service_job (201): Initialstatus UNGEPLANT, E-Nummer von der DB."""
    r = admin_client.post(
        "/api/planung/einsaetze",
        data={"work_order_id": str(seeded["order"].id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "UNGEPLANT"
    assert body["job_number"].startswith("E-")


@pytest.mark.django_db
def test_schedule_setzen(admin_client, seeded):
    """set_schedule (200): Planungszeitraum ohne Statuswechsel."""
    r = admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/schedule",
        data={"scheduled_start": "2026-08-01T08:00:00Z",
              "scheduled_end": "2026-08-01T12:00:00Z"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["scheduled_start"].startswith("2026-08-01")


@pytest.mark.django_db
def test_schedule_ende_vor_start_422(admin_client, seeded):
    r = admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/schedule",
        data={"scheduled_start": "2026-08-01T12:00:00Z",
              "scheduled_end": "2026-08-01T08:00:00Z"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_schedule_unbekannter_einsatz_404(admin_client, db):
    r = admin_client.post(
        f"/api/planung/einsaetze/{uuid4()}/schedule",
        data={"scheduled_start": "2026-08-01T08:00:00Z"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_status_vorschieben(admin_client, seeded):
    """advance_status (200): GEPLANT → BESTAETIGT (nicht begründungspflichtig)."""
    r = admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/status",
        data={"to_status": "BESTAETIGT"}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "BESTAETIGT"


@pytest.mark.django_db
def test_status_unzulaessig_422(admin_client, seeded):
    """GEPLANT → VOR_ORT ist kein erlaubter Übergang → 422."""
    r = admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/status",
        data={"to_status": "VOR_ORT"}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_zuweisung(admin_client, seeded, app_user):
    """assign_user (201): Mitarbeiter dem Einsatz zuweisen."""
    r = admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/assignments",
        data={"assignee_user_id": str(app_user.id), "role": "TECHNICIAN"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["role"] == "TECHNICIAN"
    assert body["assignee_id"] == str(app_user.id)


# --- Umplanen/Bahnwechsel (Plantafel Drag & Drop) ---------------------------
# Doppelbelegung ist eine bewusst WEICHE Invariante: der Server warnt, blockiert
# aber nicht. Das UI muss die Warnung zeigen — verschluckt es sie, entsteht eine
# stille Fehlplanung.

@pytest.mark.django_db
def test_umplanen_meldet_doppelbelegung_blockt_aber_nicht(admin_client, seeded, app_user):
    """j1 (08–12) hat app_user zugewiesen. Wird app_user auch j2 zugewiesen und j2
    ins selbe Fenster geplant, kommt eine nicht-blockierende Warnung zurück."""
    admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/assignments",
        data={"assignee_user_id": str(app_user.id)},
        content_type="application/json",
    )
    r = admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/schedule",
        data={"scheduled_start": "2026-07-13T09:00:00Z",
              "scheduled_end": "2026-07-13T11:00:00Z"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    # Die Umplanung ist geschrieben …
    assert body["scheduled_start"].startswith("2026-07-13T09:00")
    # … und die Doppelbelegung wird gemeldet, nicht verhindert.
    assert len(body["warnings"]) == 1
    assert "Doppelbelegung" in body["warnings"][0]
    assert seeded["j1"].job_number in body["warnings"][0]


@pytest.mark.django_db
def test_umplanen_ohne_kollision_ohne_warnung(admin_client, seeded, app_user):
    admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/assignments",
        data={"assignee_user_id": str(app_user.id)},
        content_type="application/json",
    )
    r = admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/schedule",
        data={"scheduled_start": "2026-08-01T08:00:00Z",
              "scheduled_end": "2026-08-01T12:00:00Z"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["warnings"] == []


@pytest.mark.django_db
def test_zuweisung_meldet_doppelbelegung(admin_client, seeded, app_user):
    """Auch die Zuweisung selbst (Bahnwechsel) warnt — j1 und j2 liegen im selben
    Fenster, app_user ist bereits auf j1."""
    r = admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/assignments",
        data={"assignee_user_id": str(app_user.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert any("Doppelbelegung" in w for w in r.json()["warnings"])


@pytest.mark.django_db
def test_zuweisung_aufheben(admin_client, seeded, app_user):
    """DELETE /assignments/{user} (200): die Zuweisung ist weg (alte Bahn räumen)."""
    # Nicht zugewiesen → fachlicher 422 (kein stiller Erfolg).
    r = admin_client.delete(
        f"/api/planung/einsaetze/{seeded['j2'].id}/assignments/{app_user.id}"
    )
    assert r.status_code == 422
    admin_client.post(
        f"/api/planung/einsaetze/{seeded['j2'].id}/assignments",
        data={"assignee_user_id": str(app_user.id)},
        content_type="application/json",
    )
    r = admin_client.delete(
        f"/api/planung/einsaetze/{seeded['j2'].id}/assignments/{app_user.id}"
    )
    assert r.status_code == 200, r.content
    assert not JobAssignment.objects.filter(
        service_job_id=seeded["j2"].id, assignee_id=app_user.id
    ).exists()


@pytest.mark.django_db
def test_zuweisung_aufheben_nach_abschluss_422(admin_client, seeded, app_user):
    """Historienschutz F-02: nach Einsatzabschluss lässt der DB-Trigger das Lösen
    nicht mehr zu → 422 (kein 500)."""
    einsatz_service.advance_status(
        app_user.id, service_job_id=seeded["j1"].id, to_status="ABGESCHLOSSEN"
    )
    r = admin_client.delete(
        f"/api/planung/einsaetze/{seeded['j1'].id}/assignments/{app_user.id}"
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_zuweisung_aufheben_unbekannter_einsatz_404(admin_client, app_user, db):
    r = admin_client.delete(
        f"/api/planung/einsaetze/{uuid4()}/assignments/{app_user.id}"
    )
    assert r.status_code == 404


# --- MONTEUR (row_scope EIGENE) --------------------------------------------

def _monteur_client(seeded, app_user, *, assigned=True):
    """Ein eingeloggter MONTEUR; optional dem Einsatz j1 zugewiesen."""
    user, monteur = make_role_user("MONTEUR")
    if assigned:
        einsatz_service.assign_user(
            app_user.id, service_job_id=seeded["j1"].id, assignee_user_id=monteur.id
        )
    c = Client()
    c.force_login(user)
    return c, monteur


@pytest.mark.django_db
def test_monteur_bucht_zeit_auf_eigenem_einsatz(seeded, app_user):
    """require_scoped: der zugewiesene Monteur darf Zeit buchen (201); user_id wird
    auf den Akteur gezwungen."""
    c, monteur = _monteur_client(seeded, app_user)
    r = c.post(
        f"/api/planung/einsaetze/{seeded['j1'].id}/times",
        data={"time_type": "ARBEITSZEIT", "started_at": "2026-07-13T13:00:00Z",
              "ended_at": "2026-07-13T15:00:00Z"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert TimeEntry.objects.filter(
        service_job_id=seeded["j1"].id, user_id=monteur.id
    ).exists()


@pytest.mark.django_db
def test_monteur_fremde_user_id_403(seeded, app_user):
    """Bei Scope EIGENE ist das Buchen einer fremden user_id verboten → 403."""
    c, _monteur = _monteur_client(seeded, app_user)
    r = c.post(
        f"/api/planung/einsaetze/{seeded['j1'].id}/times",
        data={"time_type": "ARBEITSZEIT", "started_at": "2026-07-13T13:00:00Z",
              "ended_at": "2026-07-13T15:00:00Z", "user_id": str(app_user.id)},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_monteur_fremder_einsatz_404(seeded, app_user):
    """Nicht zugewiesener Einsatz → 404 (Existenz wird nicht verraten)."""
    c, _monteur = _monteur_client(seeded, app_user, assigned=False)
    r = c.post(
        f"/api/planung/einsaetze/{seeded['j1'].id}/times",
        data={"time_type": "ARBEITSZEIT", "started_at": "2026-07-13T13:00:00Z",
              "ended_at": "2026-07-13T15:00:00Z"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_monteur_bucht_material_auf_eigenem_einsatz(seeded, app_user):
    c, _monteur = _monteur_client(seeded, app_user)
    r = c.post(
        f"/api/planung/einsaetze/{seeded['j1'].id}/materials",
        data={"description": "Dichtung", "quantity": "2", "unit": "Stk"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["description"] == "Dichtung"


@pytest.mark.django_db
def test_monteur_material_fremder_einsatz_404(seeded, app_user):
    c, _monteur = _monteur_client(seeded, app_user, assigned=False)
    r = c.post(
        f"/api/planung/einsaetze/{seeded['j1'].id}/materials",
        data={"description": "Dichtung", "quantity": "2", "unit": "Stk"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_monteur_darf_nicht_anlegen_403(seeded, app_user):
    """create_service_job nutzt `require` (fail-closed) → Monteur 403."""
    c, _monteur = _monteur_client(seeded, app_user, assigned=False)
    r = c.post(
        "/api/planung/einsaetze",
        data={"work_order_id": str(seeded["order"].id)},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_monteur_darf_nicht_zuweisen_403(seeded, app_user):
    """assign_user nutzt `require` (fail-closed) → Monteur 403, auch auf eigenem
    Einsatz."""
    c, monteur = _monteur_client(seeded, app_user)
    r = c.post(
        f"/api/planung/einsaetze/{seeded['j1'].id}/assignments",
        data={"assignee_user_id": str(monteur.id)},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_monteur_darf_sich_nicht_abmelden_403(seeded, app_user):
    """unassign_user nutzt `require` (fail-closed) → der Monteur kann sich nicht
    selbst von einem Einsatz abmelden; Umplanen ist Dispositionssache."""
    c, monteur = _monteur_client(seeded, app_user)
    r = c.delete(f"/api/planung/einsaetze/{seeded['j1'].id}/assignments/{monteur.id}")
    assert r.status_code == 403
    assert JobAssignment.objects.filter(
        service_job_id=seeded["j1"].id, assignee_id=monteur.id
    ).exists()


# --- Benutzer-Auswahlliste (Zuweisung) -------------------------------------

def _make_app_user(display_name, status="ACTIVE"):
    return AppUser.objects.create(
        id=uuid4(), display_name=display_name, status=status, version=1
    )


@pytest.mark.django_db
def test_users_liste_nur_id_und_name(admin_client, db):
    """Happy Path + Datenminimierung: die Auswahlliste liefert ausschließlich
    id + display_name (keine E-Mail, kein Status, keine Personendaten)."""
    _make_app_user("Anna Anker")
    r = admin_client.get("/api/planung/users")
    assert r.status_code == 200
    body = r.json()
    assert any(u["display_name"] == "Anna Anker" for u in body)
    # Jedes Element trägt exakt die zwei erlaubten Felder.
    for u in body:
        assert set(u.keys()) == {"id", "display_name"}


@pytest.mark.django_db
def test_users_suche(admin_client, db):
    _make_app_user("Bernd Bohrer")
    _make_app_user("Carla Klemme")
    r = admin_client.get("/api/planung/users?q=bohrer")
    namen = {u["display_name"] for u in r.json()}
    assert "Bernd Bohrer" in namen
    assert "Carla Klemme" not in namen


@pytest.mark.django_db
def test_users_inaktive_ausgeblendet(admin_client, db):
    inaktiv = _make_app_user("Detlef Disabled", status="DISABLED")
    r = admin_client.get("/api/planung/users")
    ids = {u["id"] for u in r.json()}
    assert str(inaktiv.id) not in ids


@pytest.mark.django_db
def test_users_monteur_403(seeded, app_user):
    """Fail-closed: MONTEUR hat workflow.LESEN nur als Scope EIGENE → 403 (er darf
    ohnehin nur sich selbst zuweisen)."""
    c, _monteur = _monteur_client(seeded, app_user, assigned=False)
    r = c.get("/api/planung/users")
    assert r.status_code == 403
