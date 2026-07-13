"""Service-Tests für Qualifikationen und Zuweisungs-Vorlagen (Migration 0078).

Zwei Invarianten stehen im Mittelpunkt:

1. **Der Katalog ist dynamisch.** `kind` ist ein freier Datenwert — Gewerk,
   Zertifikat und Herstellerschulung liegen in DERSELBEN Tabelle. Eine neue
   Schulungsart kostet keinen Deploy.
2. **Der Abgleich WARNT, er BLOCKIERT NICHT.** Wie die Doppelbelegung: sichtbar
   machen, nicht verbieten. Der Notdienst am Sonntag darf nicht an einem
   gesperrten Board scheitern.
"""
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.db import Error as DbError, connection, transaction

from db_core.models import (
    AppUser,
    EmployeeQualification,
    JobAssignment,
    Qualification,
)
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import mitarbeiter as hr_service
from db_core.services import identity as identity_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service
from db_core.services import qualifikation as q_service

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def monteur(app_user):
    """Ein Mitarbeiter mit Login (die Plantafel-Bahn hängt am app_user)."""
    person = identity_service.create_person(
        app_user.id, first_name="Timo", last_name="Kalinski"
    )
    login = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Timo Kalinski", status="ACTIVE", version=1
    )
    emp = hr_service.create_employee(
        app_user.id, app_user_id=login.id, party_id=person.id,
        hired_on=date(2024, 1, 1),
    )
    return {"employee": emp, "login": login}


def _termin(app_user, *, kategorie=None, assignee=None, start=T0):
    obj = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Heizungswartung"
    )
    return planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=start, scheduled_end=start + timedelta(hours=2),
        appointment_category_id=(kategorie.id if kategorie else None),
        assignee_ids=([assignee.id] if assignee else ()),
    )


# --- Der Katalog ist dynamisch ----------------------------------------------

@pytest.mark.django_db
def test_katalog_traegt_beliebige_arten_ohne_deploy(app_user):
    """Gewerk, Zertifikat und Herstellerschulung liegen in DERSELBEN Tabelle und
    unterscheiden sich nur durch einen freien Datenwert. Eine neue Art (hier:
    „SICHERHEIT") kostet keine Migration."""
    for code, label, kind, expires in (
        ("SHK", "Sanitär/Heizung/Klima", "GEWERK", False),
        ("GASSCHEIN", "Gasschein (TRGI)", "ZERTIFIKAT", True),
        ("VITODENS", "Viessmann Vitodens", "HERSTELLERSCHULUNG", False),
        ("PSAgA", "Absturzsicherung", "SICHERHEIT", True),   # neue Art, kein Deploy
    ):
        q = q_service.create_qualification(
            app_user.id, code=code, label=label, kind=kind, expires=expires
        )
        assert q.kind == kind
        assert q.expires is expires
    assert Qualification.objects.count() == 4


@pytest.mark.django_db
def test_code_ist_eindeutig(app_user):
    q_service.create_qualification(app_user.id, code="SHK", label="Sanitär")
    with pytest.raises(ValueError, match="existiert bereits"):
        q_service.create_qualification(app_user.id, code="shk", label="Nochmal")


# --- Ablaufpflicht ----------------------------------------------------------

@pytest.mark.django_db
def test_ablaufpflichtiger_nachweis_verlangt_ein_gueltig_bis(app_user, monteur):
    q = q_service.create_qualification(
        app_user.id, code="GASSCHEIN", label="Gasschein", expires=True
    )
    with pytest.raises(ValueError, match="ablaufpflichtig"):
        q_service.set_employee_qualification(
            app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id
        )


@pytest.mark.django_db
def test_die_datenbank_haelt_die_ablaufpflicht_auch_ohne_service(app_user, monteur):
    """Die Regel liegt physisch im Trigger — nicht nur im Service."""
    q = q_service.create_qualification(
        app_user.id, code="GASSCHEIN", label="Gasschein", expires=True
    )
    # Der Trigger meldet P0001 (fachliches Tor) - Django macht daraus einen
    # ProgrammingError, keinen IntegrityError. Gepr�ft wird die DB-Sperre selbst.
    with pytest.raises(DbError):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "INSERT INTO hr.employee_qualification "
                "(id, employee_id, qualification_id, created_by) "
                "VALUES (gen_random_uuid(), %s, %s, %s)",
                [str(monteur["employee"].id), str(q.id), str(app_user.id)],
            )


@pytest.mark.django_db
def test_umstellung_auf_ablaufpflichtig_wird_abgesichert(app_user, monteur):
    """Bestehende Nachweise ohne Gültig-bis wären schlagartig regelwidrig — dann
    stünde der DB-Trigger beim nächsten Speichern eines unbeteiligten Feldes im
    Weg, und niemand verstünde warum."""
    q = q_service.create_qualification(
        app_user.id, code="KAELTE", label="Kälteschein", expires=False
    )
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id
    )
    with pytest.raises(ValueError, match="ohne Gültig-bis"):
        q_service.update_qualification(
            app_user.id, qualification_id=q.id, expires=True
        )


@pytest.mark.django_db
def test_verlaengerung_schreibt_fort_statt_zu_dublizieren(app_user, monteur):
    """Eine Zeile je (Mitarbeiter, Qualifikation) — sonst wäre „gültig?"
    mehrdeutig, und genau das fragt die Plantafel."""
    q = q_service.create_qualification(
        app_user.id, code="GASSCHEIN", label="Gasschein", expires=True
    )
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id,
        valid_until=date(2026, 12, 31),
    )
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id,
        valid_until=date(2029, 12, 31),
    )
    zeilen = EmployeeQualification.objects.filter(
        employee_id=monteur["employee"].id, qualification_id=q.id
    )
    assert zeilen.count() == 1
    assert zeilen.first().valid_until == date(2029, 12, 31)


# --- Der Abgleich: warnt, blockiert nicht -----------------------------------

@pytest.mark.django_db
def test_fehlender_nachweis_warnt_nur(app_user, monteur):
    """DIE Kerninvariante: Die Zuweisung geht durch, die Warnung erscheint."""
    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    q = q_service.create_qualification(
        app_user.id, code="GASSCHEIN", label="Gasschein", expires=True
    )
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    job = _termin(app_user, kategorie=kat, assignee=monteur["login"])

    # Die Zuweisung existiert — sie wurde NICHT verhindert.
    assert JobAssignment.objects.filter(
        service_job_id=job.id, assignee_id=monteur["login"].id
    ).exists()

    warnungen = q_service.qualifikations_warnungen(job.id)
    assert len(warnungen) == 1
    assert warnungen[0]["kind"] == "QUALIFIKATION"
    assert "keinen Nachweis" in warnungen[0]["text"]
    assert "Gasschein" in warnungen[0]["text"]


@pytest.mark.django_db
def test_gueltiger_nachweis_erzeugt_keine_warnung(app_user, monteur):
    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    q = q_service.create_qualification(
        app_user.id, code="GASSCHEIN", label="Gasschein", expires=True
    )
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id,
        valid_until=date(2029, 12, 31),
    )
    job = _termin(app_user, kategorie=kat, assignee=monteur["login"])
    assert q_service.qualifikations_warnungen(job.id) == []


@pytest.mark.django_db
def test_stichtag_ist_der_terminbeginn_nicht_heute(app_user, monteur):
    """Ein Nachweis, der bis März gilt, taugt nicht für einen Termin im Mai —
    auch wenn er HEUTE noch gültig ist."""
    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    q = q_service.create_qualification(
        app_user.id, code="GASSCHEIN", label="Gasschein", expires=True
    )
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    # Heute (2026-07-13 im Testszenario) noch gültig …
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id,
        valid_until=date(2026, 8, 31),
    )
    # … aber der Termin liegt im Oktober.
    job = _termin(
        app_user, kategorie=kat, assignee=monteur["login"],
        start=datetime(2026, 10, 5, 8, 0, tzinfo=dt_timezone.utc),
    )
    warnungen = q_service.qualifikations_warnungen(job.id)
    assert len(warnungen) == 1
    assert "abgelaufen" in warnungen[0]["text"]
    # Das exakte Gültig-bis steht NICHT im Board-Text (Personalakte, siehe
    # test_board_warnung_verraet_das_gueltig_bis_nicht).
    assert "31.08" not in warnungen[0]["text"]


@pytest.mark.django_db
def test_noch_nicht_gueltiger_nachweis_warnt_ebenfalls(app_user, monteur):
    """Ein Gasschein, der erst nächste Woche erteilt wird, taugt nicht für morgen."""
    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    q = q_service.create_qualification(app_user.id, code="G", label="Gasschein")
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id,
        valid_from=date(2026, 9, 1),
    )
    job = _termin(app_user, kategorie=kat, assignee=monteur["login"])  # Juli
    warnungen = q_service.qualifikations_warnungen(job.id)
    assert len(warnungen) == 1
    assert "gilt zum Terminzeitpunkt noch nicht" in warnungen[0]["text"]


@pytest.mark.django_db
def test_bedarf_ist_die_vereinigung_aus_kategorie_und_termin(app_user, monteur):
    """Wer am Termin eine Zusatzqualifikation fordert, will die der Kategorie
    nicht abwählen."""
    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    gas = q_service.create_qualification(app_user.id, code="G", label="Gasschein")
    psa = q_service.create_qualification(app_user.id, code="P", label="Absturzsicherung")
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[gas.id]
    )
    job = _termin(app_user, kategorie=kat, assignee=monteur["login"])
    q_service.set_job_qualifications(
        app_user.id, service_job_id=job.id, qualification_ids=[psa.id]
    )

    job.refresh_from_db()
    labels = {q.label for q in q_service.bedarf(job)}
    assert labels == {"Gasschein", "Absturzsicherung"}
    assert len(q_service.qualifikations_warnungen(job.id)) == 2


@pytest.mark.django_db
def test_ohne_zuweisung_keine_warnung(app_user):
    """Ein Termin ohne Mitarbeiter kann niemandem etwas vorwerfen."""
    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    q = q_service.create_qualification(app_user.id, code="G", label="Gasschein")
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    job = _termin(app_user, kategorie=kat)
    assert q_service.qualifikations_warnungen(job.id) == []


@pytest.mark.django_db
def test_board_zeigt_die_qualifikationswarnung_als_weichen_konflikt(app_user, monteur):
    """Die Plantafel behandelt sie wie Doppelbelegung/Abwesenheit."""
    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    q = q_service.create_qualification(app_user.id, code="G", label="Gasschein")
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    job = _termin(app_user, kategorie=kat, assignee=monteur["login"])

    board = planung_service.board_daten(
        date_from=T0.date(), date_to=T0.date() + timedelta(days=1)
    )
    arten = {k["kind"] for k in board.konflikte.get(job.id, [])}
    assert "QUALIFIKATION" in arten

    # Und beim Umplanen (außerhalb des Sichtfensters) ebenso.
    assert any(
        "Gasschein" in w for w in planung_service.belegungs_warnungen(job.id)
    )


# --- Zuweisungs-Vorlagen (lose Gruppen) -------------------------------------

@pytest.mark.django_db
def test_vorlage_ist_ein_vorschlag_und_bindet_nichts(app_user, monteur):
    """Kein Team-Modell: Nach der Übernahme sind es gewöhnliche
    Einzelzuweisungen. Wer abweicht, weicht ab — kein Trigger hält ihn auf."""
    t = q_service.create_template(
        app_user.id, name="Bad-Team",
        members=[
            {"app_user_id": app_user.id, "role": "LEAD"},
            {"app_user_id": monteur["login"].id, "role": "TECHNICIAN"},
        ],
    )
    geladen = q_service.templates()[0]
    assert geladen.name == "Bad-Team"
    rollen = {m.assignee_id: m.role for m in geladen.members.all()}
    assert rollen[app_user.id] == "LEAD"
    assert rollen[monteur["login"].id] == "TECHNICIAN"

    # Ein Termin mit NUR EINEM der beiden ist völlig zulässig.
    job = _termin(app_user, assignee=monteur["login"])
    assert JobAssignment.objects.filter(service_job_id=job.id).count() == 1


@pytest.mark.django_db
def test_vorlage_nimmt_keinen_inaktiven_mitarbeiter(app_user):
    tot = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Ehemalig", status="DISABLED", version=1
    )
    with pytest.raises(ValueError, match="nicht aktiv"):
        q_service.create_template(
            app_user.id, name="Alt", members=[{"app_user_id": tot.id}]
        )


@pytest.mark.django_db
def test_vorlagenmitglieder_werden_vollstaendig_ersetzt(app_user, monteur):
    t = q_service.create_template(
        app_user.id, name="Team", members=[{"app_user_id": app_user.id}]
    )
    q_service.update_template(
        app_user.id, template_id=t.id,
        members=[{"app_user_id": monteur["login"].id, "role": "LEAD"}],
    )
    geladen = next(x for x in q_service.templates() if x.id == t.id)
    mitglieder = list(geladen.members.all())
    assert len(mitglieder) == 1
    assert mitglieder[0].assignee_id == monteur["login"].id
    assert mitglieder[0].role == "LEAD"


@pytest.mark.django_db
def test_stillgelegte_qualifikation_wird_nicht_mehr_gefordert(app_user):
    kat = planung_service.create_category(app_user.id, name="Wartung")
    q = q_service.create_qualification(app_user.id, code="ALT", label="Veraltet")
    q_service.update_qualification(app_user.id, qualification_id=q.id, active=False)
    with pytest.raises(ValueError, match="stillgelegt"):
        q_service.set_category_qualifications(
            app_user.id, category_id=kat.id, qualification_ids=[q.id]
        )


# --- Regressionen aus dem Review --------------------------------------------

@pytest.mark.django_db
def test_stichtag_ist_der_berliner_kalendertag(app_user, monteur):
    """Ein Handwerkstermin ist eine Uhrzeit auf der WANDUHR.

    Review-Fund, reproduziert: Der Stichtag war der UTC-Tag. Ein Notdiensttermin
    am 01.05. um 01:00 Ortszeit ist in UTC noch der 30.04. — ein Nachweis, der am
    30.04. abläuft, galt damit fälschlich noch, und der Monteur führe mit
    abgelaufenem Gasschein zur Gastherme.
    """
    kat = planung_service.create_category(app_user.id, name="Notdienst")
    q = q_service.create_qualification(
        app_user.id, code="G", label="Gasschein", expires=True
    )
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id,
        valid_until=date(2026, 4, 30),
    )
    # 01.05.2026, 01:00 Berlin  ==  30.04.2026, 23:00 UTC
    start = datetime(2026, 4, 30, 23, 0, tzinfo=dt_timezone.utc)
    job = _termin(app_user, kategorie=kat, assignee=monteur["login"], start=start)

    warnungen = q_service.qualifikations_warnungen(job.id)
    assert len(warnungen) == 1
    assert "abgelaufen" in warnungen[0]["text"]


@pytest.mark.django_db
def test_board_warnung_verraet_das_gueltig_bis_nicht(app_user, monteur):
    """DSGVO-Grenze: Das Board hängt an `workflow`. Der Disponent OHNE hr-Recht
    darf die FOLGE erfahren („kein Nachweis"), nicht den Akteninhalt (das exakte
    Gültig-bis) — genau den Feldwert verweigert ihm die Akte mit 403."""
    kat = planung_service.create_category(app_user.id, name="Wartung")
    q = q_service.create_qualification(
        app_user.id, code="G", label="Gasschein", expires=True
    )
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id,
        valid_until=date(2026, 3, 12),
    )
    job = _termin(app_user, kategorie=kat, assignee=monteur["login"])  # Juli 2026

    warnungen = q_service.qualifikations_warnungen(job.id)
    assert len(warnungen) == 1
    text = warnungen[0]["text"]
    assert "abgelaufen" in text
    assert "12.03" not in text and "2026" not in text


@pytest.mark.django_db
def test_die_datenbank_verhindert_die_umstellung_auf_ablaufpflichtig(app_user, monteur):
    """Review-Fund: Der Service war der EINZIGE Wächter — jedes UPDATE aus psql
    oder künftigem Code hinterließe regelwidrige Zeilen."""
    q = q_service.create_qualification(
        app_user.id, code="KAELTE", label="Kälteschein", expires=False
    )
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id
    )
    with pytest.raises(DbError):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE hr.qualification SET expires = true WHERE id = %s",
                [str(q.id)],
            )


@pytest.mark.django_db
def test_geloeschter_nachweis_steht_im_audit_log(app_user, monteur):
    """Das DELETE bleibt erlaubt (ein falscher Haken ist kein Geschäftsvorfall) —
    aber es verschwindet eine Zeile der PERSONALAKTE. Ohne Lösch-Audit geschähe
    das spurlos (Review-Fund)."""
    q = q_service.create_qualification(app_user.id, code="G", label="Gasschein")
    q_service.set_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id
    )
    q_service.remove_employee_qualification(
        app_user.id, employee_id=monteur["employee"].id, qualification_id=q.id
    )
    with connection.cursor() as cur:
        cur.execute(
            "SELECT before_excerpt FROM audit.audit_entry "
            "WHERE action = 'ROW_DELETE' "
            "  AND target_type = 'hr.employee_qualification'"
        )
        zeilen = cur.fetchall()
    assert len(zeilen) == 1
    # Das Vorher-Bild ist mitgeschrieben — sonst wüsste niemand, WAS verschwand.
    assert str(q.id) in str(zeilen[0][0])


@pytest.mark.django_db
def test_vorlage_mit_stillgelegtem_namensvetter(app_user, monteur):
    """Der Unique-Index gilt nur für AKTIVE Vorlagen — ein stillgelegter
    Namensvetter darf existieren. (Der API-Endpunkt lud die neue Vorlage vorher
    über den NAMEN nach und lieferte die alte zurück; Review-Fund.)"""
    alt = q_service.create_template(app_user.id, name="Bad-Team")
    q_service.update_template(app_user.id, template_id=alt.id, active=False)

    neu = q_service.create_template(
        app_user.id, name="Bad-Team",
        members=[{"app_user_id": monteur["login"].id, "role": "LEAD"}],
    )
    assert neu.id != alt.id
    aktive = q_service.templates()
    assert len(aktive) == 1
    assert aktive[0].id == neu.id
    assert len(list(aktive[0].members.all())) == 1


@pytest.mark.django_db
def test_board_prueft_qualifikationen_ohne_n_plus_1(app_user, monteur):
    """Die Zusage „drei Abfragen fürs ganze Board" muss abgesichert sein.

    Sie hängt daran, dass `board_daten` die Zuweisungen MIT `assignee` prefetcht.
    Fällt das bei einem künftigen Umbau weg, wird aus jeder Kachel ein Query —
    im heißesten Lesepfad des Produkts, still.
    """
    from django.test.utils import CaptureQueriesContext
    from django.db import connection as conn

    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    q = q_service.create_qualification(app_user.id, code="G", label="Gasschein")
    q_service.set_category_qualifications(
        app_user.id, category_id=kat.id, qualification_ids=[q.id]
    )
    # Zehn Termine mit demselben (nicht qualifizierten) Monteur.
    for i in range(10):
        _termin(
            app_user, kategorie=kat, assignee=monteur["login"],
            start=T0 + timedelta(hours=3 * i),
        )

    with CaptureQueriesContext(conn) as ctx:
        board = planung_service.board_daten(
            date_from=T0.date(), date_to=T0.date() + timedelta(days=2)
        )
    # Zehn Kacheln, jede mit Qualifikationswarnung …
    assert sum(
        1 for ks in board.konflikte.values()
        for k in ks if k["kind"] == "QUALIFIKATION"
    ) == 10
    # … und das Board bleibt bei einer klar begrenzten Zahl von Abfragen. Ohne
    # Bündelung wären es 10 × (Bedarf + Nachweise) mehr.
    assert len(ctx.captured_queries) < 20
