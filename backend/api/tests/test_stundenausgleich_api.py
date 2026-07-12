"""Stundenausgleich (hr.time_adjustment, Migration 0072).

Der Saldo bleibt **abgeleitet**: Ist − Soll + Σ Ausgleich. Diese Tests sichern
das Vorzeichen, die Pflichtbegründung, das Selbst-Ausgleich-Verbot und das
Storno-Muster (append-only, kein stilles Löschen).
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from api.tests.conftest import logged_in_client, make_app_user
from db_core.models import TimeAdjustment, TimeCategory
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import zeiterfassung as zeit

TZ = ZoneInfo("Europe/Berlin")

# Ein Montag (2026-07-13) — der Vertrag unten weist Mo–Fr 8 h aus.
MONTAG = date(2026, 7, 13)


def _app_user_of(client):
    from django.contrib.auth import get_user_model

    uid = client.session["_auth_user_id"]
    return get_user_model().objects.get(pk=uid).app_user_id


def _dt(tag, hh, mm=0):
    return datetime(tag.year, tag.month, tag.day, hh, mm, tzinfo=TZ)


def _mitarbeiter(actor, app_user_id, vorname="Timo", nachname="Kalinski"):
    person = identity_service.create_person(
        actor, first_name=vorname, last_name=nachname
    )
    emp = mitarbeiter_service.create_employee(
        actor, app_user_id=app_user_id, party_id=person.id, hired_on=date(2026, 1, 1)
    )
    mitarbeiter_service.create_contract(
        actor,
        employee_id=emp.id,
        valid_from=date(2026, 1, 1),
        hours={
            "hours_monday": Decimal("8"),
            "hours_tuesday": Decimal("8"),
            "hours_wednesday": Decimal("8"),
            "hours_thursday": Decimal("8"),
            "hours_friday": Decimal("8"),
        },
        vacation_days_per_year=Decimal("30"),
    )
    return emp


@pytest.fixture
def szene(admin_client):
    """Admin (Personalverwaltung) + ein Monteur mit Personalsatz und Vertrag."""
    actor = _app_user_of(admin_client)
    monteur = logged_in_client("MONTEUR")
    emp = _mitarbeiter(actor, _app_user_of(monteur))
    return admin_client, monteur, emp, actor


# ---------------------------------------------------------------------------
# Saldo mit Ausgleichsbuchung (Vorzeichen!)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_saldo_beruecksichtigt_ausgleich_mit_vorzeichen(szene):
    admin, monteur, emp, actor = szene
    # 4 h gearbeitet an einem Montag mit 8 h Soll → Saldo −4,00.
    zeit.zeiteintrag_anlegen(
        _app_user_of(monteur),
        user_id=_app_user_of(monteur),
        category_id=TimeCategory.objects.get(code="ARBEITSZEIT").id,
        started_at=_dt(MONTAG, 8),
        ended_at=_dt(MONTAG, 12),
    )
    von, bis = MONTAG, MONTAG
    r = admin.get(
        f"/api/zeiterfassung/stundenkonto?employee_id={emp.id}"
        f"&von={von}&bis={bis}"
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["soll"] == "8.00" and body["ist"] == "4.00"
    assert body["ausgleich"] == "0.00"
    assert body["saldo"] == "-4.00"

    # Einbehalt: +4 h auf das Konto → Saldo 0.
    r = admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "EINBEHALT",
            "effective_on": MONTAG.isoformat(),
            "minutes": 240,
            "reason": "Minusstunden werden einbehalten (Vereinbarung vom 10.07.)",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["stunden"] == "4.00"

    body = admin.get(
        f"/api/zeiterfassung/stundenkonto?employee_id={emp.id}&von={von}&bis={bis}"
    ).json()
    assert body["ausgleich"] == "4.00"
    assert body["saldo"] == "0.00"

    # Auszahlung: NEGATIVES Vorzeichen belastet das Konto.
    admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "AUSZAHLUNG",
            "effective_on": MONTAG.isoformat(),
            "minutes": -90,
            "reason": "1,5 h ausgezahlt mit der Juli-Abrechnung",
        },
        content_type="application/json",
    )
    body = admin.get(
        f"/api/zeiterfassung/stundenkonto?employee_id={emp.id}&von={von}&bis={bis}"
    ).json()
    assert body["ausgleich"] == "2.50"
    assert body["saldo"] == "-1.50"


@pytest.mark.django_db
def test_ausgleich_ausserhalb_des_zeitraums_zaehlt_nicht(szene):
    admin, _, emp, _ = szene
    admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "KORREKTUR",
            "effective_on": "2026-06-30",
            "minutes": 600,
            "reason": "Korrektur Juni",
        },
        content_type="application/json",
    )
    body = admin.get(
        f"/api/zeiterfassung/stundenkonto?employee_id={emp.id}"
        f"&von=2026-07-01&bis=2026-07-31"
    ).json()
    assert body["ausgleich"] == "0.00"


@pytest.mark.django_db
def test_minuten_bleiben_exakt(szene):
    """20 Minuten sind keine 0,33 h — die Wahrheit steht in Minuten."""
    admin, _, emp, _ = szene
    r = admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "FREIZEITAUSGLEICH",
            "effective_on": MONTAG.isoformat(),
            "minutes": 20,
            "reason": "20 Minuten Freizeitausgleich",
        },
        content_type="application/json",
    )
    assert r.status_code == 201
    assert TimeAdjustment.objects.get(id=r.json()["id"]).minutes == 20


# ---------------------------------------------------------------------------
# Tore
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ausgleich_ohne_begruendung_422(szene):
    admin, _, emp, _ = szene
    r = admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "EINBEHALT",
            "effective_on": MONTAG.isoformat(),
            "minutes": 60,
            "reason": "   ",
        },
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "begründungspflichtig" in r.json()["detail"]
    assert TimeAdjustment.objects.count() == 0


@pytest.mark.django_db
def test_ausgleich_null_minuten_und_unbekannte_art_422(szene):
    admin, _, emp, _ = szene
    basis = {
        "employee_id": str(emp.id),
        "effective_on": MONTAG.isoformat(),
        "reason": "x",
    }
    r = admin.post(
        "/api/zeiterfassung/ausgleich",
        data={**basis, "adjustment_type": "EINBEHALT", "minutes": 0},
        content_type="application/json",
    )
    assert r.status_code == 422
    r = admin.post(
        "/api/zeiterfassung/ausgleich",
        data={**basis, "adjustment_type": "GESCHENK", "minutes": 60},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_monteur_darf_sich_nicht_selbst_ausgleichen(szene):
    """Zwei Sperren: das Recht (403) und — für den, der ALLE darf — das eigene Konto."""
    admin, monteur, emp, actor = szene

    # 1. Der Monteur hat hr/AENDERN nur mit row_scope EIGENE → `require` = 403.
    r = monteur.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "EINBEHALT",
            "effective_on": MONTAG.isoformat(),
            "minutes": 480,
            "reason": "Ich schenke mir einen Tag",
        },
        content_type="application/json",
    )
    assert r.status_code == 403
    assert TimeAdjustment.objects.count() == 0

    # 2. Auch wer ALLE darf, gleicht sein EIGENES Konto nicht aus (Führungsaufgabe).
    eigener = _mitarbeiter(actor, actor, vorname="Ada", nachname="Admin")
    r = admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(eigener.id),
            "adjustment_type": "AUSZAHLUNG",
            "effective_on": MONTAG.isoformat(),
            "minutes": -480,
            "reason": "Selbstbedienung",
        },
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Vier-Augen" in r.json()["detail"]
    assert TimeAdjustment.objects.count() == 0


@pytest.mark.django_db
def test_monteur_sieht_eigene_ausgleiche_aber_keine_fremden(szene):
    admin, monteur, emp, actor = szene
    admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "EINBEHALT",
            "effective_on": MONTAG.isoformat(),
            "minutes": 60,
            "reason": "Einbehalt",
        },
        content_type="application/json",
    )
    fremd_user = make_app_user("Fremder Kollege")
    fremd = _mitarbeiter(actor, fremd_user.id, vorname="Ida", nachname="Fremd")
    admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(fremd.id),
            "adjustment_type": "AUSZAHLUNG",
            "effective_on": MONTAG.isoformat(),
            "minutes": -120,
            "reason": "Auszahlung",
        },
        content_type="application/json",
    )

    r = monteur.get("/api/zeiterfassung/ausgleich")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1 and body[0]["employee_id"] == str(emp.id)

    # Der fremde Personalsatz ist 404 (Existenz wird nicht verraten).
    assert (
        monteur.get(f"/api/zeiterfassung/ausgleich?employee_id={fremd.id}").status_code
        == 404
    )
    # Die Verwaltung sieht beide.
    assert len(admin.get("/api/zeiterfassung/ausgleich").json()) == 2


# ---------------------------------------------------------------------------
# Storno (append-only)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_storno_hebt_die_buchung_auf_und_loescht_nichts(szene):
    admin, _, emp, _ = szene
    r = admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "AUSZAHLUNG",
            "effective_on": MONTAG.isoformat(),
            "minutes": -300,
            "reason": "5 h ausgezahlt",
        },
        content_type="application/json",
    )
    buchung_id = r.json()["id"]
    konto = f"/api/zeiterfassung/stundenkonto?employee_id={emp.id}&von={MONTAG}&bis={MONTAG}"
    assert admin.get(konto).json()["ausgleich"] == "-5.00"

    r = admin.post(
        f"/api/zeiterfassung/ausgleich/{buchung_id}/stornieren",
        data={"reason": "Auszahlung wurde doppelt erfasst"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    storno = r.json()
    assert storno["minutes"] == 300 and storno["ist_storno"] is True

    # Beide Zeilen stehen noch, beide zählen nicht mehr.
    assert TimeAdjustment.objects.count() == 2
    assert TimeAdjustment.objects.get(id=buchung_id).status == "STORNIERT"
    assert admin.get(konto).json()["ausgleich"] == "0.00"

    # Zweites Storno: fachlich zu (weder doppelt noch ein Storno des Stornos).
    r = admin.post(
        f"/api/zeiterfassung/ausgleich/{buchung_id}/stornieren",
        data={"reason": "nochmal"},
        content_type="application/json",
    )
    assert r.status_code == 422
    r = admin.post(
        f"/api/zeiterfassung/ausgleich/{storno['id']}/stornieren",
        data={"reason": "nochmal"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_storno_auf_dem_eigenen_konto_ist_gesperrt(szene):
    """Review-Befund A1: Ein Storno IST eine Ausgleichsbuchung.

    Der direkte Weg war zu (422) — der Storno-Weg stand offen: Der
    Geschäftsführer ließ eine Belastung auf seinem Konto stornieren und schrieb
    sich damit die Stunden wieder gut. Beide Wege sind jetzt zu, im Service UND
    im DB-Trigger.
    """
    admin, _, _, actor = szene
    chef = _mitarbeiter(actor, actor, vorname="Ada", nachname="Admin")

    # Die Buchung auf dem eigenen Konto legt ein DRITTER an (das ist erlaubt).
    fremder_client = logged_in_client("ADMINISTRATION")
    r = fremder_client.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(chef.id),
            "adjustment_type": "AUSZAHLUNG",
            "effective_on": MONTAG.isoformat(),
            "minutes": -1800,
            "reason": "30 h ausgezahlt",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    buchung_id = r.json()["id"]

    konto = f"/api/zeiterfassung/stundenkonto?employee_id={chef.id}&von={MONTAG}&bis={MONTAG}"
    assert admin.get(konto).json()["ausgleich"] == "-30.00"

    # Der Betroffene selbst darf sie NICHT stornieren (das wäre +30 h für ihn).
    r = admin.post(
        f"/api/zeiterfassung/ausgleich/{buchung_id}/stornieren",
        data={"reason": "Ich hätte die Stunden lieber auf dem Konto"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Vier-Augen" in r.json()["detail"]
    assert admin.get(konto).json()["ausgleich"] == "-30.00"
    assert TimeAdjustment.objects.count() == 1

    # Ein Dritter darf stornieren — der Vorgang selbst ist ja zulässig.
    r = fremder_client.post(
        f"/api/zeiterfassung/ausgleich/{buchung_id}/stornieren",
        data={"reason": "Auszahlung wurde doppelt erfasst"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert admin.get(konto).json()["ausgleich"] == "0.00"


@pytest.mark.django_db
def test_db_trigger_sperrt_das_eigene_konto_auch_am_service_vorbei(szene):
    """Die Vier-Augen-Regel liegt physisch in der DB (Migration 0075) — ein
    zweiter Schreibpfad (KI-Agent, Skript) kann sie nicht umgehen."""
    from db_core.db_context import business_transaction

    admin, _, _, actor = szene
    chef = _mitarbeiter(actor, actor, vorname="Ada", nachname="Admin")

    with pytest.raises(Exception) as exc:
        with business_transaction(actor):
            TimeAdjustment.objects.create(
                id=__import__("uuid").uuid4(),
                employee_id=chef.id,
                adjustment_type="EINBEHALT",
                effective_on=MONTAG,
                minutes=480,
                reason="am Service vorbei",
                status="GEBUCHT",
                reversal_of_id=None,
                created_by_id=actor,
            )
    assert "Vier-Augen" in str(exc.value)
    assert TimeAdjustment.objects.count() == 0


@pytest.mark.django_db
def test_storno_ohne_begruendung_422_und_unbekannt_404(szene):
    admin, _, emp, _ = szene
    r = admin.post(
        "/api/zeiterfassung/ausgleich",
        data={
            "employee_id": str(emp.id),
            "adjustment_type": "KORREKTUR",
            "effective_on": MONTAG.isoformat(),
            "minutes": 60,
            "reason": "Korrektur",
        },
        content_type="application/json",
    )
    buchung_id = r.json()["id"]
    assert (
        admin.post(
            f"/api/zeiterfassung/ausgleich/{buchung_id}/stornieren",
            data={"reason": " "},
            content_type="application/json",
        ).status_code
        == 422
    )
    import uuid as _uuid

    assert (
        admin.post(
            f"/api/zeiterfassung/ausgleich/{_uuid.uuid4()}/stornieren",
            data={"reason": "x"},
            content_type="application/json",
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_buchung_ist_physisch_unveraenderlich(szene):
    """Der DB-Trigger lässt kein Umschreiben zu — eine Fehlbuchung wird storniert."""
    from db_core.db_context import business_transaction

    admin, _, emp, actor = szene
    eintrag = zeit.ausgleich_buchen(
        actor,
        employee_id=emp.id,
        adjustment_type="EINBEHALT",
        effective_on=MONTAG,
        minutes=120,
        reason="Einbehalt",
    )
    with pytest.raises(Exception) as exc:
        with business_transaction(actor):
            TimeAdjustment.objects.filter(id=eintrag.id).update(minutes=9999)
    assert "unveraenderlich" in str(exc.value).lower() or "unveränderlich" in str(
        exc.value
    ).lower()

    with pytest.raises(Exception):
        with business_transaction(actor):
            TimeAdjustment.objects.filter(id=eintrag.id).delete()
