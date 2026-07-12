"""Attest an einer Abwesenheit — DSGVO Art. 9 (Migration 0072).

Eine Arbeitsunfähigkeitsbescheinigung ist ein **Gesundheitsdatum**. Diese Tests
sind kein Beiwerk, sondern die eigentliche Absicherung des Slices:

* Der Betroffene lädt sein Attest hoch (201) und sieht es (200).
* Ein **fremdes** Attest ist **404** — nicht 403: ein 403 bestätigte die Existenz
  der Abwesenheit und damit die Tatsache einer Krankmeldung.
* Die **Disposition** hat `content/LESEN` + `content/AENDERN` mit Scope ALLE und
  kommt trotzdem nicht heran — das content-Recht trägt keine Gesundheitsdaten.
* Der **Dateiname verrät keine Diagnose** (er wird serverseitig ersetzt).
* Der Download ist an dieselbe Grenze gebunden wie die Liste.

Der Upload braucht MinIO. Ohne erreichbaren Objektspeicher meldet der Service
422 — dann würden diese Tests unecht grün. Deshalb wird der Speicher hier durch
eine In-Memory-Ablage ersetzt: geprüft werden Rechte und Namensgebung, nicht der
Objektspeicher (den deckt `test_dateien_api` ab).
"""
from datetime import date
from decimal import Decimal

import pytest

from api.tests.conftest import logged_in_client, make_app_user
from db_core import storage as storage_module
from db_core.models import File, FileLink
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as ma


class _SpeicherAttrappe:
    """Objektspeicher im RAM — der Test prüft Rechte, nicht MinIO."""

    def __init__(self):
        self.objekte = {}

    def put_object(self, key, inhalt, content_type=None):
        self.objekte[key] = inhalt

    def get_object(self, key):
        if key not in self.objekte:
            raise storage_module.StorageError(f"unbekannt: {key}")
        return self.objekte[key]


@pytest.fixture(autouse=True)
def speicher(monkeypatch):
    ablage = _SpeicherAttrappe()
    monkeypatch.setattr(storage_module, "get_storage", lambda: ablage)
    return ablage


def _app_user_of(client):
    from django.contrib.auth import get_user_model

    uid = client.session["_auth_user_id"]
    return get_user_model().objects.get(pk=uid).app_user_id


def _employee(actor, app_user_id, nachname):
    person = identity_service.create_person(
        actor, first_name="Timo", last_name=nachname
    )
    emp = ma.create_employee(
        actor, app_user_id=app_user_id, party_id=person.id, hired_on=date(2026, 1, 1)
    )
    ma.create_contract(
        actor,
        employee_id=emp.id,
        valid_from=date(2026, 1, 1),
        hours={f"hours_{t}": Decimal("8") for t in
               ("monday", "tuesday", "wednesday", "thursday", "friday")},
        vacation_days_per_year=Decimal("30"),
    )
    return emp


def _krankheit(actor, emp, von=date(2026, 7, 6), bis=date(2026, 7, 10)):
    return ma.create_absence(
        actor,
        employee_id=emp.id,
        absence_type="KRANKHEIT",
        start_date=von,
        end_date=bis,
    )


def _upload(client, absence_id, dateiname="attest.pdf", inhalt=b"%PDF-1.4 fake"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        "/api/content/files",
        data={
            "datei": SimpleUploadedFile(dateiname, inhalt, content_type="application/pdf"),
            "absence_id": str(absence_id),
            "link_category": "DOKUMENT",
        },
    )


@pytest.fixture
def szene(admin_client):
    """Admin (Personalverwaltung) + zwei Monteure mit je einer Krankmeldung."""
    actor = _app_user_of(admin_client)
    a = logged_in_client("MONTEUR")
    b = logged_in_client("MONTEUR")
    emp_a = _employee(actor, _app_user_of(a), "Kalinski")
    emp_b = _employee(actor, _app_user_of(b), "Ostmann")
    abw_a = _krankheit(actor, emp_a)
    abw_b = _krankheit(actor, emp_b, date(2026, 8, 3), date(2026, 8, 5))
    return admin_client, a, b, abw_a, abw_b


# ---------------------------------------------------------------------------
# Der Betroffene
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_monteur_laedt_eigenes_attest_hoch_und_sieht_es(szene):
    admin, a, b, abw_a, abw_b = szene

    r = _upload(a, abw_a.id)
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["link_category"] == "ATTEST"

    r = a.get(f"/api/content/files?absence_id={abw_a.id}")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    file_id = body["file_id"]
    r = a.get(f"/api/content/files/{file_id}/download")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 fake"

    # Die Personalverwaltung sieht es ebenfalls.
    assert admin.get(f"/api/content/files?absence_id={abw_a.id}").json()["total"] == 1
    assert admin.get(f"/api/content/files/{file_id}/download").status_code == 200


@pytest.mark.django_db
def test_dateiname_verraet_keine_diagnose(szene):
    """`grippaler_infekt.pdf` darf in keiner Liste auftauchen."""
    admin, a, _, abw_a, _ = szene
    r = _upload(a, abw_a.id, dateiname="grippaler_infekt_dr_mueller.pdf")
    assert r.status_code == 201
    name = r.json()["original_filename"]
    assert "grippal" not in name.lower()
    assert name == "Arbeitsunfaehigkeitsbescheinigung_2026-07-06_bis_2026-07-10.pdf"
    assert File.objects.get(id=r.json()["file_id"]).original_filename == name


# ---------------------------------------------------------------------------
# Fremde und Unbefugte
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_fremdes_attest_ist_404(szene):
    admin, a, b, abw_a, abw_b = szene
    r = _upload(b, abw_b.id)
    assert r.status_code == 201
    fremde_datei = r.json()["file_id"]
    fremder_link = r.json()["link_id"]

    # Kollege A: weder hochladen noch listen noch herunterladen.
    assert _upload(a, abw_b.id).status_code == 404
    assert a.get(f"/api/content/files?absence_id={abw_b.id}").status_code == 404
    assert a.get(f"/api/content/files/{fremde_datei}/download").status_code == 404
    # Auch das Lösen der Verknüpfung geht nicht (er hat ohnehin kein AENDERN).
    assert a.delete(f"/api/content/links/{fremder_link}").status_code in (403, 404)
    assert FileLink.objects.filter(id=fremder_link).exists()


@pytest.mark.django_db
def test_disposition_kommt_trotz_content_recht_nicht_heran(szene):
    """Die Disposition darf content/LESEN + AENDERN mit Scope ALLE — und sieht
    trotzdem kein Attest. Das content-Recht trägt keine Gesundheitsdaten."""
    admin, a, _, abw_a, _ = szene
    r = _upload(a, abw_a.id)
    file_id, link_id = r.json()["file_id"], r.json()["link_id"]

    import uuid

    dispo = logged_in_client("DISPOSITION")
    # POSITIVKONTROLLE: Die Disposition hat content/LESEN mit Scope ALLE
    # (Rechtematrix 0026) — an einem gewöhnlichen Ziel antwortet sie mit 200.
    # Ohne diese Zeile prüfte der Test unten nur, dass eine rechtlose Rolle
    # nichts sieht, und wäre wertlos.
    assert (
        dispo.get(f"/api/content/files?project_id={uuid.uuid4()}").status_code == 200
    )

    # Und trotzdem: kein Attest. Weder listen, noch herunterladen, noch lösen.
    assert dispo.get(f"/api/content/files?absence_id={abw_a.id}").status_code == 404
    assert dispo.get(f"/api/content/files/{file_id}/download").status_code == 404
    assert dispo.delete(f"/api/content/links/{link_id}").status_code == 404
    assert FileLink.objects.filter(id=link_id).exists()


@pytest.mark.django_db
def test_dedup_traegt_das_attest_nicht_in_eine_offene_ablage(szene):
    """Review-Befund A2: Der SHA-256-Dedup war das Leck.

    Der realistische Auslöser braucht keinen Angreifer: Der Monteur hängt sein
    Attest-PDF **zusätzlich an seinen eigenen Einsatz** — das darf er
    (`content/ANLEGEN` mit Scope EIGENE). Vorher lieferte der Dedup dieselbe
    `file_id`, und die Krankschreibung lag offen für die ganze Disposition.

    Zwei Riegel, beide hier geprüft:
      1. Ein Attest wird **nie** dedupliziert — es bekommt ein eigenes Objekt,
         und es wird nie auf ein Attest-Objekt dedupliziert.
      2. Der Download-Guard ist **fail-closed**: eine einzige Attest-Verknüpfung
         sperrt die ganze Datei.
    """
    import uuid as _uuid

    from django.core.files.uploadedfile import SimpleUploadedFile

    from db_core.services import einsatz as einsatz_service

    admin, a, _, abw_a, _ = szene
    actor = _app_user_of(admin)
    inhalt = b"%PDF-1.4 attest-bytes"

    r = _upload(a, abw_a.id, inhalt=inhalt)
    assert r.status_code == 201
    attest_file = r.json()["file_id"]

    # Der Monteur hängt DIESELBEN Bytes an seinen eigenen Einsatz.
    job = einsatz_service.create_service_job(actor, title="Begehung")
    einsatz_service.assign_user(
        actor, service_job_id=job.id, assignee_user_id=_app_user_of(a)
    )
    r2 = a.post(
        "/api/content/files",
        data={
            "datei": SimpleUploadedFile("foto.pdf", inhalt, content_type="application/pdf"),
            "service_job_id": str(job.id),
            "link_category": "DOKUMENT",
        },
    )
    assert r2.status_code == 201, r2.content
    einsatz_file = r2.json()["file_id"]

    # 1. KEIN Dedup: das Attest bleibt ein eigenes Speicherobjekt.
    assert einsatz_file != attest_file
    assert FileLink.objects.filter(file_id=attest_file).count() == 1

    # 2. Die Disposition kommt an das Attest-Objekt nicht heran …
    dispo = logged_in_client("DISPOSITION")
    assert dispo.get(f"/api/content/files/{attest_file}/download").status_code == 404
    # … wohl aber an die (harmlose) Einsatzdatei — Positivkontrolle.
    assert dispo.get(f"/api/content/files/{einsatz_file}/download").status_code == 200


@pytest.mark.django_db
def test_guard_ist_fail_closed_bei_zweiter_verknuepfung(szene):
    """Selbst wenn eine Attest-Datei doch eine zweite Verknüpfung bekäme (etwa
    durch einen künftigen Codepfad), bleibt sie gesperrt: **eine** Attest-
    Verknüpfung genügt, um die ganze Datei zu sperren (fail-closed)."""
    import uuid as _uuid

    from db_core.db_context import business_transaction
    from db_core.services import projekt as projekt_service
    from db_core.services import property as property_service

    admin, a, _, abw_a, _ = szene
    actor = _app_user_of(admin)
    r = _upload(a, abw_a.id)
    file_id = r.json()["file_id"]

    obj = property_service.create_property(
        actor, name="Objekt", property_type="WEG", street="Weg",
        postal_code="10115", city="Berlin",
    )
    projekt = projekt_service.create_project(actor, name="Projekt", property_ids=[obj.id])
    with business_transaction(actor):
        FileLink.objects.create(
            id=_uuid.uuid4(),
            file_id=file_id,
            project_id=projekt.id,
            link_category="DOKUMENT",
            created_by_id=actor,
        )

    dispo = logged_in_client("DISPOSITION")
    # Die Datei hängt jetzt auch am Projekt — und bleibt trotzdem gesperrt.
    assert dispo.get(f"/api/content/files/{file_id}/download").status_code == 404
    # Der Betroffene und die Personalverwaltung kommen weiter heran.
    assert a.get(f"/api/content/files/{file_id}/download").status_code == 200
    assert admin.get(f"/api/content/files/{file_id}/download").status_code == 200


@pytest.mark.django_db
def test_nur_lesen_rolle_sieht_kein_attest(szene):
    admin, a, _, abw_a, _ = szene
    r = _upload(a, abw_a.id)
    leser = logged_in_client("NUR_LESEN")
    assert leser.get(f"/api/content/files?absence_id={abw_a.id}").status_code == 404
    assert (
        leser.get(f"/api/content/files/{r.json()['file_id']}/download").status_code == 404
    )


@pytest.mark.django_db
def test_attest_erscheint_in_keiner_anderen_dateiliste(szene):
    """Eine Verknüpfung hängt an genau einem Objekt — ein Attest kann in keiner
    Projekt-/Auftragsliste auftauchen. Und die Kategorie ATTEST ist an einem
    anderen Ziel gar nicht erst zulässig."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    admin, a, _, abw_a, _ = szene
    _upload(a, abw_a.id)
    assert FileLink.objects.filter(absence__isnull=False).count() == 1
    assert FileLink.objects.filter(absence__isnull=True).count() == 0

    # ATTEST an einem anderen Ziel: 422 (fail-closed).
    r = admin.post(
        "/api/content/files",
        data={
            "datei": SimpleUploadedFile("x.pdf", b"%PDF-1.4 x", content_type="application/pdf"),
            "party_id": str(make_app_user("x").id),
            "link_category": "ATTEST",
        },
    )
    assert r.status_code == 422
    assert "ATTEST" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Fachliche Tore
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_attest_an_genehmigter_abwesenheit_bleibt_moeglich(szene):
    """Bewusste Entscheidung: Die Erstbescheinigung trifft NACH der Genehmigung
    ein (§ 5 Abs. 1 EFZG). Wer den Upload mit der Genehmigung sperrt, macht den
    Nachweis unmöglich, den er verlangt."""
    admin, a, _, abw_a, _ = szene
    actor = _app_user_of(admin)
    ma.submit_absence(actor, absence_id=abw_a.id)
    ma.approve_absence(actor, absence_id=abw_a.id)

    assert _upload(a, abw_a.id).status_code == 201
    assert _upload(a, abw_a.id, inhalt=b"%PDF-1.4 folge").status_code == 201  # Folge-AU
    assert FileLink.objects.filter(absence_id=abw_a.id).count() == 2


@pytest.mark.django_db
def test_kein_attest_an_verworfener_abwesenheit(szene):
    """ABGELEHNT/ZURUECKGEZOGEN: der Antrag ist gegenstandslos — ein
    Gesundheitsdatum daran wäre eine Verarbeitung ohne Zweck (Art. 5). Der
    DB-Trigger verbietet es; die API meldet 422."""
    admin, a, _, abw_a, _ = szene
    actor = _app_user_of(admin)
    ma.withdraw_absence(actor, absence_id=abw_a.id)

    r = _upload(a, abw_a.id)
    assert r.status_code == 422, r.content
    assert "gegenstandslos" in r.json()["detail"]
    assert FileLink.objects.filter(absence_id=abw_a.id).count() == 0


@pytest.mark.django_db
def test_personalverwaltung_kann_die_verknuepfung_loesen(szene):
    """Aufbewahrungsfrist: das Lösen ist Bürotätigkeit der Personalverwaltung.
    Die Datei selbst bleibt (content.file ist unveränderlich)."""
    admin, a, _, abw_a, _ = szene
    r = _upload(a, abw_a.id)
    link_id, file_id = r.json()["link_id"], r.json()["file_id"]

    assert admin.delete(f"/api/content/links/{link_id}").status_code == 204
    assert not FileLink.objects.filter(id=link_id).exists()
    assert File.objects.filter(id=file_id).exists()
