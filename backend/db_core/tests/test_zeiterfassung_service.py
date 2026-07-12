"""Service-Tests der Zeiterfassung gegen die echte Test-DB.

Scharf sind: der EXCLUDE-Constraint gegen Überlappung (0066), der partielle
UNIQUE-Index „genau eine laufende Buchung", die Tagesklammer-Trigger und der
Statusautomat des Arbeitstags (0067), der Vier-Augen-Trigger und die zwei
unabhängigen Schlösser (B-28 + Arbeitstag).
"""
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db.utils import IntegrityError, ProgrammingError

from db_core.models import (
    AppUser,
    BreakRule,
    Employee,
    Holiday,
    TimeCategory,
    TimeEntry,
    WorkDay,
)
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import zeiterfassung as zeit

TZ = ZoneInfo("Europe/Berlin")


def _dt(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


@pytest.fixture
def zweiter_user(db):
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name="Chef", status="ACTIVE", version=1
    )


def _kat(code):
    return TimeCategory.objects.get(code=code)


# ---------------------------------------------------------------------------
# Kategorien (0066)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_systemkategorien_geseedet():
    codes = {c.code for c in zeit.kategorien() if c.is_system}
    assert codes == {
        "ARBEITSZEIT", "FAHRTZEIT", "PAUSE", "BEREITSCHAFT", "NACHARBEIT",
        "INTERNE_ZEIT",
    }
    assert _kat("PAUSE").is_work_time is False
    assert _kat("FAHRTZEIT").is_work_time is True
    # Betriebseigene Kategorien sind nicht System — der Betrieb darf sie ändern.
    frei = [c for c in zeit.kategorien() if not c.is_system]
    assert {c.name for c in frei} >= {"Werkstatt", "Buero", "Materialfahrt", "Schulung"}


@pytest.mark.django_db
def test_systemkategorie_nicht_archivierbar(app_user):
    with pytest.raises(ValueError, match="Systemkategorien"):
        zeit.kategorie_archivieren(app_user.id, category_id=_kat("ARBEITSZEIT").id)


@pytest.mark.django_db
def test_pause_is_work_time_nicht_umschaltbar(app_user):
    """Eine Pause, die als Arbeitszeit zählt, wäre die Aufzeichnungspflicht ad
    absurdum geführt — der DB-Trigger verbietet es."""
    with pytest.raises(Exception) as exc:
        zeit.kategorie_aendern(
            app_user.id, category_id=_kat("PAUSE").id, is_work_time=True
        )
    assert "is_work_time" in str(exc.value) or "Pause" in str(exc.value)


@pytest.mark.django_db
def test_eigene_kategorie_anlegen_und_archivieren(app_user):
    cat = zeit.kategorie_anlegen(
        app_user.id, name="Rüstzeit", is_work_time=True, sort_order=200
    )
    assert cat.is_system is False and cat.code is None
    zeit.kategorie_archivieren(app_user.id, category_id=cat.id)
    assert TimeCategory.objects.get(id=cat.id).status == "ARCHIVIERT"
    # Der Name ist nach dem Archivieren wieder frei (partieller Unique-Index).
    zeit.kategorie_anlegen(app_user.id, name="Rüstzeit", is_work_time=True)


@pytest.mark.django_db
def test_archivierte_kategorie_nicht_buchbar(app_user):
    cat = zeit.kategorie_anlegen(app_user.id, name="Alt", is_work_time=True)
    zeit.kategorie_archivieren(app_user.id, category_id=cat.id)
    with pytest.raises(ValueError, match="archiviert"):
        zeit.stempel_start(app_user.id, category_id=cat.id)


# ---------------------------------------------------------------------------
# Stempeluhr — der Zustandsautomat
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_stempel_start_pause_weiter_stopp(app_user):
    t = _dt(2026, 7, 13, 8)
    e1 = zeit.stempel_start(app_user.id, now=t)
    assert e1.ended_at is None
    assert e1.time_type == "ARBEITSZEIT"
    assert zeit.aktuell(app_user.id)["zustand"] == "LAEUFT"

    p = zeit.stempel_pause(app_user.id, now=t + timedelta(hours=4))
    assert p.category.code == "PAUSE"
    assert p.service_job_id is None
    assert TimeEntry.objects.get(id=e1.id).ended_at == t + timedelta(hours=4)
    assert zeit.aktuell(app_user.id)["zustand"] == "PAUSE"

    w = zeit.stempel_weiter(app_user.id, now=t + timedelta(hours=4, minutes=30))
    assert w.category.code == "ARBEITSZEIT"
    assert zeit.aktuell(app_user.id)["zustand"] == "LAEUFT"

    zeit.stempel_stopp(app_user.id, now=t + timedelta(hours=8))
    assert zeit.aktueller_eintrag(app_user.id) is None
    assert zeit.aktuell(app_user.id)["zustand"] == "GESTOPPT"

    tag = WorkDay.objects.get(user_id=app_user.id, day=date(2026, 7, 13))
    summen = zeit.tages_summen(zeit.eintraege_am_tag(tag.id))
    # 4 h + 3,5 h Arbeit, 0,5 h Pause
    assert summen["arbeit_sekunden"] == int(timedelta(hours=7.5).total_seconds())
    assert summen["pause_sekunden"] == 1800
    assert summen["laeuft"] is False


@pytest.mark.django_db
def test_doppeltes_start_verboten(app_user):
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    with pytest.raises(ValueError, match="läuft bereits eine Buchung"):
        zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 9))


@pytest.mark.django_db
def test_pause_ohne_arbeit_verboten(app_user):
    with pytest.raises(ValueError, match="keine Zeitbuchung"):
        zeit.stempel_pause(app_user.id, now=_dt(2026, 7, 13, 8))
    with pytest.raises(ValueError, match="keine Zeitbuchung"):
        zeit.stempel_stopp(app_user.id, now=_dt(2026, 7, 13, 8))


@pytest.mark.django_db
def test_pause_waehrend_pause_verboten(app_user):
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    zeit.stempel_pause(app_user.id, now=_dt(2026, 7, 13, 12))
    with pytest.raises(ValueError, match="bereits eine Pause"):
        zeit.stempel_pause(app_user.id, now=_dt(2026, 7, 13, 12, 10))


@pytest.mark.django_db
def test_weiter_ohne_pause_verboten(app_user):
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    with pytest.raises(ValueError, match="keine Pause"):
        zeit.stempel_weiter(app_user.id, now=_dt(2026, 7, 13, 9))


@pytest.mark.django_db
def test_weiter_schreibt_kategorie_und_einsatz_fort(app_user):
    fahrt = _kat("FAHRTZEIT")
    zeit.stempel_start(app_user.id, category_id=fahrt.id, now=_dt(2026, 7, 13, 7))
    zeit.stempel_pause(app_user.id, now=_dt(2026, 7, 13, 8))
    w = zeit.stempel_weiter(app_user.id, now=_dt(2026, 7, 13, 8, 15))
    assert w.category.code == "FAHRTZEIT"


@pytest.mark.django_db
def test_vergessenes_stoppen_bleibt_offen_und_blockiert_start(app_user):
    """Die laufende Buchung wird NICHT automatisch beendet — ein erfundenes Ende
    wäre eine Falschaussage. Sie ist als überfällig markiert und blockiert Start,
    bis der Monteur stoppt oder korrigiert."""
    gestern = _dt(2026, 7, 12, 8)
    zeit.stempel_start(app_user.id, now=gestern)

    heute = _dt(2026, 7, 13, 7)
    zustand = zeit.aktuell(app_user.id, now=heute)
    assert zustand["laeuft"] is True
    assert zustand["ueberfaellig"] is True

    with pytest.raises(ValueError, match="läuft bereits"):
        zeit.stempel_start(app_user.id, now=heute)

    # Stoppen bleibt möglich; die Buchung gehört dem ANFANGSTAG (Nachtschicht).
    e = zeit.stempel_stopp(app_user.id, now=heute)
    assert e.work_day.day == date(2026, 7, 12)


@pytest.mark.django_db
def test_nachtschicht_haengt_am_anfangstag(app_user):
    e = zeit.zeiteintrag_anlegen(
        app_user.id,
        user_id=app_user.id,
        category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 22),
        ended_at=_dt(2026, 7, 14, 6),
    )
    assert e.work_day.day == date(2026, 7, 13)
    assert WorkDay.objects.filter(user_id=app_user.id, day=date(2026, 7, 14)).count() == 0


# ---------------------------------------------------------------------------
# Überlappungssperre (0066)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ueberlappung_verboten(app_user):
    """Überlappung ist ein BEDIENfehler → ValueError (422), nicht IntegrityError
    (500). Der Browser-Durchlauf hat den 500 aufgedeckt."""
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 12),
    )
    with pytest.raises(ValueError, match="überschneidet"):
        zeit.zeiteintrag_anlegen(
            app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
            started_at=_dt(2026, 7, 13, 11), ended_at=_dt(2026, 7, 13, 13),
        )


@pytest.mark.django_db
def test_lueckenlos_anschliessende_buchung_erlaubt(app_user):
    """tstzrange ist [von, bis) — Ende == Beginn ist KEINE Überlappung."""
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 12),
    )
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
        started_at=_dt(2026, 7, 13, 12), ended_at=_dt(2026, 7, 13, 13),
    )
    assert TimeEntry.objects.filter(user_id=app_user.id).count() == 2


@pytest.mark.django_db
def test_ueberlappung_anderer_mitarbeiter_erlaubt(app_user, zweiter_user):
    for u in (app_user, zweiter_user):
        zeit.zeiteintrag_anlegen(
            u.id, user_id=u.id, category_id=_kat("ARBEITSZEIT").id,
            started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 12),
        )
    assert TimeEntry.objects.count() == 2


@pytest.mark.django_db
def test_laufende_buchung_blockiert_keine_zukunft(app_user):
    """Eine laufende Buchung hat noch KEIN Ende — sie darf keine spätere Zeit
    blockieren (z. B. eine vorausgeplante Zeit am Termin von morgen).

    Der erste Entwurf modellierte sie als [start, ∞) und verbot genau das; der
    Browser-Durchlauf ist darüber gestolpert (500 beim Start, weil eine
    Demo-Zeit von morgen im Weg lag)."""
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
        started_at=_dt(2026, 7, 14, 14), ended_at=_dt(2026, 7, 14, 15),
    )
    assert TimeEntry.objects.filter(user_id=app_user.id).count() == 2


@pytest.mark.django_db
def test_stoppen_ueber_eine_bestehende_buchung_hinweg_scheitert(app_user):
    """Die Kollision entsteht beim STOPPEN — dann trägt die Buchung ein Ende."""
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
        started_at=_dt(2026, 7, 13, 10), ended_at=_dt(2026, 7, 13, 11),
    )
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    with pytest.raises(ValueError, match="überschneidet"):
        zeit.stempel_stopp(app_user.id, now=_dt(2026, 7, 13, 12))
    # Vor der Kollision stoppen geht.
    e = zeit.stempel_stopp(app_user.id, now=_dt(2026, 7, 13, 9, 30))
    assert e.ended_at == _dt(2026, 7, 13, 9, 30)


@pytest.mark.django_db
def test_start_in_eine_erfasste_zeit_hinein_verboten(app_user):
    """Review-Befund S3: der EXCLUDE greift erst beim Stoppen. Startete die Uhr
    MITTEN in eine erfasste Zeit, ließe sie sich nie mehr stoppen — Sackgasse.
    Also schon beim Start 422."""
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 16),
    )
    with pytest.raises(ValueError, match="bereits eine Zeit erfasst"):
        zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 10))
    assert TimeEntry.objects.filter(user_id=app_user.id, ended_at__isnull=True).count() == 0
    # Nach dem Ende der erfassten Zeit geht es.
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 16))


@pytest.mark.django_db
def test_stopp_fehler_nennt_den_schuldigen_eintrag(app_user):
    """Die Meldung muss sagen, WELCHER Eintrag im Weg liegt — sonst weiß der
    Monteur nicht, was er korrigieren soll."""
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
        started_at=_dt(2026, 7, 13, 10), ended_at=_dt(2026, 7, 13, 11),
    )
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    with pytest.raises(ValueError) as exc:
        zeit.stempel_stopp(app_user.id, now=_dt(2026, 7, 13, 12))
    text = str(exc.value)
    assert "überschneidet" in text
    assert "Fahrtzeit" in text and "10:00" in text and "11:00" in text
    # Und der Ausweg steht dabei.
    assert "löschen" in text


@pytest.mark.django_db
def test_zweite_laufende_buchung_physisch_verboten(app_user):
    """Auch am Service vorbei: der partielle UNIQUE-Index lässt nur eine zu."""
    from db_core.db_context import business_transaction

    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    with pytest.raises(IntegrityError):
        with business_transaction(app_user.id):
            TimeEntry.objects.create(
                id=uuid.uuid4(),
                user_id=app_user.id,
                category_id=_kat("FAHRTZEIT").id,
                started_at=_dt(2026, 7, 14, 8),
                ended_at=None,
            )


# ---------------------------------------------------------------------------
# Tagesklammer + Freigabe (0067)
# ---------------------------------------------------------------------------

def _tag_mit_zeiten(user, tag=date(2026, 7, 13)):
    zeit.zeiteintrag_anlegen(
        user.id, user_id=user.id, category_id=_kat("ARBEITSZEIT").id,
        started_at=datetime(tag.year, tag.month, tag.day, 8, tzinfo=TZ),
        ended_at=datetime(tag.year, tag.month, tag.day, 16, tzinfo=TZ),
    )
    return WorkDay.objects.get(user_id=user.id, day=tag)


@pytest.mark.django_db
def test_arbeitstag_wird_automatisch_angelegt(app_user):
    tag = _tag_mit_zeiten(app_user)
    assert tag.status == "ENTWURF"
    assert TimeEntry.objects.filter(work_day_id=tag.id).count() == 1


@pytest.mark.django_db
def test_einreichen_bestaetigen(app_user, zweiter_user):
    tag = _tag_mit_zeiten(app_user)
    tag = zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)
    assert tag.status == "EINGEREICHT" and tag.submitted_at is not None

    tag = zeit.arbeitstag_bestaetigen(zweiter_user.id, work_day_id=tag.id)
    assert tag.status == "BESTAETIGT"
    assert tag.decided_by_id == zweiter_user.id


@pytest.mark.django_db
def test_vier_augen_eigener_tag_nicht_bestaetigbar(app_user):
    tag = _tag_mit_zeiten(app_user)
    zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)
    with pytest.raises(ValueError, match="Vier-Augen"):
        zeit.arbeitstag_bestaetigen(app_user.id, work_day_id=tag.id)
    with pytest.raises(ValueError, match="Vier-Augen"):
        zeit.arbeitstag_ablehnen(app_user.id, work_day_id=tag.id, note="passt schon")


@pytest.mark.django_db
def test_vier_augen_physisch_im_trigger(app_user):
    """Auch am Service vorbei: der DB-Trigger lässt decided_by = user_id nicht zu."""
    from db_core.db_context import business_transaction

    tag = _tag_mit_zeiten(app_user)
    zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)
    tag.refresh_from_db()
    tag.status = "BESTAETIGT"
    tag.decided_by_id = app_user.id
    tag.decided_at = datetime.now(tz=TZ)
    with pytest.raises(ProgrammingError, match="Vier-Augen"):
        with business_transaction(app_user.id):
            tag.save(update_fields=["status", "decided_by", "decided_at"])


@pytest.mark.django_db
def test_ablehnen_verlangt_begruendung(app_user, zweiter_user):
    tag = _tag_mit_zeiten(app_user)
    zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)
    with pytest.raises(ValueError, match="begründungspflichtig"):
        zeit.arbeitstag_ablehnen(zweiter_user.id, work_day_id=tag.id, note="  ")
    tag = zeit.arbeitstag_ablehnen(
        zweiter_user.id, work_day_id=tag.id, note="Fahrtzeit fehlt"
    )
    assert tag.status == "ABGELEHNT" and tag.decision_note == "Fahrtzeit fehlt"
    # Nach Korrektur erneut einreichbar.
    tag = zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)
    assert tag.status == "EINGEREICHT"


@pytest.mark.django_db
def test_einreichen_mit_laufender_buchung_verboten(app_user):
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    tag = WorkDay.objects.get(user_id=app_user.id, day=date(2026, 7, 13))
    with pytest.raises(ValueError, match="läuft noch eine Buchung"):
        zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)


@pytest.mark.django_db
def test_leerer_tag_nicht_einreichbar(app_user, zweiter_user):
    tag = _tag_mit_zeiten(app_user)
    eintrag = TimeEntry.objects.get(work_day_id=tag.id)
    zeit.zeiteintrag_loeschen(app_user.id, entry_id=eintrag.id)
    with pytest.raises(ValueError, match="keine Zeitbuchung"):
        zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)


@pytest.mark.django_db
def test_bearbeiten_eines_eingereichten_tages_faellt_zurueck(app_user):
    """Ein EINGEREICHTer Tag, der noch bearbeitet wird, fällt auf ENTWURF —
    sonst wäre die Einreichung eine Lüge."""
    tag = _tag_mit_zeiten(app_user)
    zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
        started_at=_dt(2026, 7, 13, 17), ended_at=_dt(2026, 7, 13, 18),
    )
    tag.refresh_from_db()
    assert tag.status == "ENTWURF" and tag.submitted_at is None


# ---------------------------------------------------------------------------
# Das Arbeitstag-Schloss (Korrektur eines bestätigten Tages)
# ---------------------------------------------------------------------------

def _bestaetigter_tag(app_user, chef):
    tag = _tag_mit_zeiten(app_user)
    zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)
    return zeit.arbeitstag_bestaetigen(chef.id, work_day_id=tag.id)


@pytest.mark.django_db
def test_bestaetigter_tag_ohne_begruendung_gesperrt(app_user, zweiter_user):
    _bestaetigter_tag(app_user, zweiter_user)
    with pytest.raises(Exception) as exc:
        zeit.zeiteintrag_anlegen(
            app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
            started_at=_dt(2026, 7, 13, 17), ended_at=_dt(2026, 7, 13, 18),
        )
    assert "bestätigt" in str(exc.value) or "bestaetigt" in str(exc.value)


@pytest.mark.django_db
def test_bestaetigter_tag_mit_begruendung_faellt_auf_entwurf(app_user, zweiter_user):
    tag = _bestaetigter_tag(app_user, zweiter_user)
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
        started_at=_dt(2026, 7, 13, 17), ended_at=_dt(2026, 7, 13, 18),
        correction_reason="Rückfahrt nachgetragen",
    )
    tag.refresh_from_db()
    assert tag.status == "ENTWURF"
    assert tag.decided_by_id is None and tag.decided_at is None
    assert tag.submitted_at is None

    # Der Rückfall steht im Statusprotokoll — mit der Begründung.
    from db_core.models import StatusChange

    log = StatusChange.objects.filter(entity="work_day", entity_id=tag.id).order_by(
        "occurred_at"
    )
    letzter = list(log)[-1]
    assert letzter.from_status == "BESTAETIGT" and letzter.to_status == "ENTWURF"
    assert letzter.reason == "Rückfahrt nachgetragen"


@pytest.mark.django_db
def test_bestaetigter_tag_loeschen_mit_begruendung(app_user, zweiter_user):
    tag = _bestaetigter_tag(app_user, zweiter_user)
    eintrag = TimeEntry.objects.get(work_day_id=tag.id)
    with pytest.raises(Exception):
        zeit.zeiteintrag_loeschen(app_user.id, entry_id=eintrag.id)
    zeit.zeiteintrag_loeschen(
        app_user.id, entry_id=eintrag.id, correction_reason="doppelt erfasst"
    )
    tag.refresh_from_db()
    assert tag.status == "ENTWURF"


@pytest.mark.django_db
def test_b28_bleibt_scharf(app_user):
    """Zwei unabhängige Schlösser: nach kaufmännischer Auftragsprüfung ist die
    Zeitbuchung gesperrt — auch mit Arbeitstag-Begründung."""
    from db_core.tests.test_einsatz_service import _order

    order = _order(app_user, target="KAUFMAENNISCH_GEPRUEFT")
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    with pytest.raises(Exception) as exc:
        zeit.zeiteintrag_anlegen(
            app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
            started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 12),
            service_job_id=job.id, correction_reason="Nachtrag",
        )
    assert "kaufmännisch" in str(exc.value).lower() or "B-28" in str(exc.value)


# ---------------------------------------------------------------------------
# Pausen-Engine (0068)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_pausen_gesetzlich_30_minuten(app_user):
    """8 h Arbeit ohne Pause → ArbZG § 4 verlangt 30 min; sie werden vom ENDE
    der Arbeitszeit abgeschnitten und als Pause umgewidmet."""
    tag = _tag_mit_zeiten(app_user)  # 08:00–16:00
    neu = zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id)
    assert len(neu) == 1
    p = neu[0]
    assert p.auto_generated is True
    assert p.started_at == _dt(2026, 7, 13, 15, 30)
    assert p.ended_at == _dt(2026, 7, 13, 16)

    summen = zeit.tages_summen(zeit.eintraege_am_tag(tag.id))
    assert summen["arbeit_sekunden"] == int(timedelta(hours=7.5).total_seconds())
    assert summen["pause_sekunden"] == 1800


@pytest.mark.django_db
def test_pausen_gesetzlich_45_minuten_ueber_9h(app_user):
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 7), ended_at=_dt(2026, 7, 13, 17),  # 10 h
    )
    tag = WorkDay.objects.get(user_id=app_user.id, day=date(2026, 7, 13))
    zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id)
    summen = zeit.tages_summen(zeit.eintraege_am_tag(tag.id))
    assert summen["pause_sekunden"] == 45 * 60


@pytest.mark.django_db
def test_pausen_gesetzlich_idempotent(app_user):
    tag = _tag_mit_zeiten(app_user)
    zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id)
    zweiter_lauf = zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id)
    assert zweiter_lauf == []
    summen = zeit.tages_summen(zeit.eintraege_am_tag(tag.id))
    assert summen["pause_sekunden"] == 1800


@pytest.mark.django_db
def test_pausen_gesetzlich_rechnet_gestempelte_pause_an(app_user):
    """Wer schon 30 min gestempelt hat, bekommt bei 8 h nichts dazu."""
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    zeit.stempel_pause(app_user.id, now=_dt(2026, 7, 13, 12))
    zeit.stempel_weiter(app_user.id, now=_dt(2026, 7, 13, 12, 30))
    zeit.stempel_stopp(app_user.id, now=_dt(2026, 7, 13, 16, 30))
    tag = WorkDay.objects.get(user_id=app_user.id, day=date(2026, 7, 13))
    assert zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id) == []


@pytest.mark.django_db
def test_pausen_modus_keine(app_user):
    zeit.pausenregel_setzen(app_user.id, mode="KEINE")
    tag = _tag_mit_zeiten(app_user)
    assert zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id) == []
    assert zeit.tages_summen(zeit.eintraege_am_tag(tag.id))["pause_sekunden"] == 0


@pytest.mark.django_db
def test_pausen_feste_zeiten_schneiden_aus(app_user):
    zeit.pausenregel_setzen(
        app_user.id, mode="FESTE_ZEITEN",
        fixed_breaks=[{"von": "12:00", "bis": "12:30"}],
    )
    tag = _tag_mit_zeiten(app_user)  # 08:00–16:00
    neu = zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id)
    assert len(neu) == 1
    assert neu[0].started_at == _dt(2026, 7, 13, 12)
    assert neu[0].ended_at == _dt(2026, 7, 13, 12, 30)

    eintraege = zeit.eintraege_am_tag(tag.id)
    # Der Arbeitsblock ist gesplittet: 08–12, Pause 12–12:30, 12:30–16.
    assert len(eintraege) == 3
    summen = zeit.tages_summen(eintraege)
    assert summen["arbeit_sekunden"] == int(timedelta(hours=7.5).total_seconds())


@pytest.mark.django_db
def test_pausen_feste_zeiten_schneiden_gestempelte_pause_nicht_erneut(app_user):
    """Review-Befund S2: Fenster 12:00–12:30, der Mitarbeiter hat 12:15–12:45
    SELBST gestempelt. Der blinde Schnitt widmete zusätzlich 12:00–12:15 zu Pause
    um → 45 min Pause statt 30, 15 min Arbeitszeit vernichtet. Das ist eine
    Falschaussage in einer nach § 17 MiLoG aufzeichnungspflichtigen Erfassung."""
    zeit.pausenregel_setzen(
        app_user.id, mode="FESTE_ZEITEN",
        fixed_breaks=[{"von": "12:00", "bis": "12:30"}],
    )
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    zeit.stempel_pause(app_user.id, now=_dt(2026, 7, 13, 12, 15))
    zeit.stempel_weiter(app_user.id, now=_dt(2026, 7, 13, 12, 45))
    zeit.stempel_stopp(app_user.id, now=_dt(2026, 7, 13, 16))
    tag = WorkDay.objects.get(user_id=app_user.id, day=date(2026, 7, 13))

    assert zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id) == []
    summen = zeit.tages_summen(zeit.eintraege_am_tag(tag.id))
    assert summen["pause_sekunden"] == 30 * 60
    assert summen["arbeit_sekunden"] == int(timedelta(hours=7.5).total_seconds())


@pytest.mark.django_db
def test_pausen_feste_zeiten_rechnen_zu_kurze_pause_auf(app_user):
    """Nur der FEHLBETRAG wird geschnitten: 10 min selbst gestempelt (12:20–12:30),
    Fenster 30 min → 20 min kommen dazu, nicht 30."""
    zeit.pausenregel_setzen(
        app_user.id, mode="FESTE_ZEITEN",
        fixed_breaks=[{"von": "12:00", "bis": "12:30"}],
    )
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    zeit.stempel_pause(app_user.id, now=_dt(2026, 7, 13, 12, 20))
    zeit.stempel_weiter(app_user.id, now=_dt(2026, 7, 13, 12, 30))
    zeit.stempel_stopp(app_user.id, now=_dt(2026, 7, 13, 16))
    tag = WorkDay.objects.get(user_id=app_user.id, day=date(2026, 7, 13))

    neu = zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id)
    assert len(neu) == 1
    assert neu[0].started_at == _dt(2026, 7, 13, 12)
    assert neu[0].ended_at == _dt(2026, 7, 13, 12, 20)
    summen = zeit.tages_summen(zeit.eintraege_am_tag(tag.id))
    assert summen["pause_sekunden"] == 30 * 60


@pytest.mark.django_db
def test_pausen_feste_zeiten_idempotent(app_user):
    zeit.pausenregel_setzen(
        app_user.id, mode="FESTE_ZEITEN",
        fixed_breaks=[{"von": "12:00", "bis": "12:30"}],
    )
    tag = _tag_mit_zeiten(app_user)  # 08:00–16:00
    zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id)
    assert zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id) == []
    assert zeit.tages_summen(zeit.eintraege_am_tag(tag.id))["pause_sekunden"] == 30 * 60


@pytest.mark.django_db
def test_pausen_gesetzlich_volle_stufe_keine_kappung(app_user):
    """Entscheidung zum Review-Befund S8: bei 6 h 01 brutto werden die VOLLEN
    30 min abgezogen, nicht auf `min(30 min, arbeit − 6 h)` = 1 min gekappt.

    ArbZG § 4 Satz 2 kennt Ruhepausen nur in Abschnitten von mindestens
    15 Minuten — eine 1-Minuten-„Pause" wäre keine Ruhepause, sondern eine
    erfundene Zahl. Die Kappung wäre zudem zirkulär (sie drückte die Arbeitszeit
    auf exakt 6 h und ließe die Pausenpflicht rechnerisch entfallen)."""
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 14, 1),  # 6 h 01
    )
    tag = WorkDay.objects.get(user_id=app_user.id, day=date(2026, 7, 13))
    neu = zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id)
    assert len(neu) == 1
    summen = zeit.tages_summen(zeit.eintraege_am_tag(tag.id))
    assert summen["pause_sekunden"] == 30 * 60
    assert summen["arbeit_sekunden"] == int(timedelta(hours=5, minutes=31).total_seconds())


@pytest.mark.django_db
def test_pausen_feste_zeiten_ohne_arbeit_im_fenster(app_user):
    zeit.pausenregel_setzen(
        app_user.id, mode="FESTE_ZEITEN",
        fixed_breaks=[{"von": "12:00", "bis": "12:30"}],
    )
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 14), ended_at=_dt(2026, 7, 13, 17),
    )
    tag = WorkDay.objects.get(user_id=app_user.id, day=date(2026, 7, 13))
    assert zeit.pausen_regel_anwenden(app_user.id, work_day_id=tag.id) == []


@pytest.mark.django_db
def test_pausenregel_validierung(app_user):
    with pytest.raises(ValueError, match="Modus"):
        zeit.pausenregel_setzen(app_user.id, mode="IRGENDWAS")
    with pytest.raises(ValueError, match="mindestens ein Pausenfenster"):
        zeit.pausenregel_setzen(app_user.id, mode="FESTE_ZEITEN", fixed_breaks=[])
    with pytest.raises(ValueError, match="nach 'von'"):
        zeit.pausenregel_setzen(
            app_user.id, mode="FESTE_ZEITEN",
            fixed_breaks=[{"von": "13:00", "bis": "12:00"}],
        )
    with pytest.raises(ValueError, match="überlappen"):
        zeit.pausenregel_setzen(
            app_user.id, mode="FESTE_ZEITEN",
            fixed_breaks=[
                {"von": "12:00", "bis": "13:00"},
                {"von": "12:30", "bis": "13:30"},
            ],
        )


# ---------------------------------------------------------------------------
# Feiertage + Stundenkonto
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_feiertage_bundesweit_ohne_firmenprofil():
    tage = zeit.feiertage(date(2026, 1, 1), date(2026, 12, 31))
    assert tage[date(2026, 1, 1)] == "Neujahr"
    assert tage[date(2026, 10, 3)] == "Tag der Deutschen Einheit"
    # Ostern 2026 = 5. April → Karfreitag 3.4., Ostermontag 6.4.
    assert tage[date(2026, 4, 3)] == "Karfreitag"
    assert tage[date(2026, 4, 6)] == "Ostermontag"
    # Ohne Bundesland im Firmenprofil: nur bundesweite Tage.
    assert date(2026, 11, 1) not in tage  # Allerheiligen (regional)


@pytest.mark.django_db
def test_feiertage_mit_bundesland(app_user):
    from db_core.services import firma as firma_service

    firma_service.update_company_profile(
        app_user.id, company_name="Mitra", state_code="BY"
    )
    tage = zeit.feiertage(date(2026, 1, 1), date(2026, 12, 31))
    assert tage[date(2026, 11, 1)] == "Allerheiligen"      # BY
    assert tage[date(2026, 1, 6)] == "Heilige Drei Könige"  # BY
    assert date(2026, 10, 31) not in tage                   # Reformationstag: nicht BY


def _employee(app_user, chef):
    person = identity_service.create_person(
        chef.id, first_name="Max", last_name="Monteur"
    )
    emp = mitarbeiter_service.create_employee(
        chef.id, app_user_id=app_user.id, party_id=person.id,
        hired_on=date(2026, 1, 1),
    )
    mitarbeiter_service.create_contract(
        chef.id, employee_id=emp.id, valid_from=date(2026, 1, 1),
        hours={
            "hours_monday": Decimal("8"), "hours_tuesday": Decimal("8"),
            "hours_wednesday": Decimal("8"), "hours_thursday": Decimal("8"),
            "hours_friday": Decimal("8"),
        },
        vacation_days_per_year=Decimal("30"),
    )
    return emp


@pytest.mark.django_db
def test_stundenkonto_soll_ist_saldo(app_user, zweiter_user):
    emp = _employee(app_user, zweiter_user)
    # Mo 13.07. bis Fr 17.07.2026 = 5 Arbeitstage à 8 h = 40 h Soll.
    for tag, (von, bis) in {
        date(2026, 7, 13): (8, 16),
        date(2026, 7, 14): (8, 16),
        date(2026, 7, 15): (8, 16),
        date(2026, 7, 16): (8, 16),
        date(2026, 7, 17): (8, 17),  # 1 h Mehrarbeit
    }.items():
        zeit.zeiteintrag_anlegen(
            app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
            started_at=datetime(tag.year, tag.month, tag.day, von, tzinfo=TZ),
            ended_at=datetime(tag.year, tag.month, tag.day, bis, tzinfo=TZ),
        )
    konto = zeit.stundenkonto(emp.id, date(2026, 7, 13), date(2026, 7, 19))
    assert konto["soll"] == Decimal("40.00")
    assert konto["ist"] == Decimal("41.00")
    assert konto["saldo"] == Decimal("1.00")
    assert konto["tage_gesamt"] == 5


@pytest.mark.django_db
def test_stundenkonto_zaehlt_fahrt_und_bereitschaft(app_user, zweiter_user):
    """Die alte Auswertung zählte nur ARBEITSZEIT — Fahrt/Bereitschaft fielen
    unter den Tisch. Maßgeblich ist `is_work_time`."""
    emp = _employee(app_user, zweiter_user)
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("FAHRTZEIT").id,
        started_at=_dt(2026, 7, 13, 7), ended_at=_dt(2026, 7, 13, 8),
    )
    zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 12),
    )
    konto = zeit.stundenkonto(emp.id, date(2026, 7, 13), date(2026, 7, 13))
    assert konto["ist"] == Decimal("5.00")   # 1 h Fahrt + 4 h Arbeit
    assert konto["soll"] == Decimal("8.00")
    assert konto["saldo"] == Decimal("-3.00")


@pytest.mark.django_db
def test_stundenkonto_feiertag_mindert_soll(app_user, zweiter_user):
    from db_core.services import firma as firma_service

    firma_service.update_company_profile(
        zweiter_user.id, company_name="Mitra", state_code="BE"
    )
    emp = _employee(app_user, zweiter_user)
    # 1.5.2026 (Tag der Arbeit) ist ein Freitag → Soll fällt weg.
    konto = zeit.stundenkonto(emp.id, date(2026, 5, 1), date(2026, 5, 1))
    assert konto["soll"] == Decimal("0.00")
    assert konto["saldo"] == Decimal("0.00")


@pytest.mark.django_db
def test_stundenkonto_abwesenheit_zaehlt_als_erfuellt(app_user, zweiter_user):
    emp = _employee(app_user, zweiter_user)
    absence = mitarbeiter_service.create_absence(
        zweiter_user.id, employee_id=emp.id, absence_type="URLAUB",
        start_date=date(2026, 7, 13), end_date=date(2026, 7, 14),
    )
    mitarbeiter_service.submit_absence(zweiter_user.id, absence_id=absence.id)
    mitarbeiter_service.approve_absence(zweiter_user.id, absence_id=absence.id)

    konto = zeit.stundenkonto(emp.id, date(2026, 7, 13), date(2026, 7, 14))
    assert konto["soll"] == Decimal("16.00")
    assert konto["abwesend"] == Decimal("16.00")
    assert konto["ist"] == Decimal("0.00")
    assert konto["saldo"] == Decimal("0.00")


@pytest.mark.django_db
def test_stundenkonto_laufende_buchung_zaehlt_nicht(app_user, zweiter_user):
    emp = _employee(app_user, zweiter_user)
    zeit.stempel_start(app_user.id, now=_dt(2026, 7, 13, 8))
    konto = zeit.stundenkonto(emp.id, date(2026, 7, 13), date(2026, 7, 13))
    assert konto["ist"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_stundenliste_enthaelt_beginn_ende_dauer(app_user, zweiter_user):
    tag = _tag_mit_zeiten(app_user)
    zeit.arbeitstag_einreichen(app_user.id, work_day_id=tag.id)
    zeit.arbeitstag_bestaetigen(zweiter_user.id, work_day_id=tag.id)

    zeilen = zeit.stundenliste(date(2026, 7, 13), date(2026, 7, 13))
    assert len(zeilen) == 1
    z = zeilen[0]
    assert z["tag"] == date(2026, 7, 13)
    assert z["beginn"].hour == 8 and z["ende"].hour == 16
    assert z["dauer_stunden"] == Decimal("8.0")
    assert z["arbeitszeit"] is True
    assert z["tagesstatus"] == "BESTAETIGT"
    assert z["bestaetigt_von"] == "Chef"


# ---------------------------------------------------------------------------
# Bestandspfad: Zeitbuchung am Einsatz
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_log_time_am_einsatz_mit_time_type(app_user):
    from db_core.tests.test_einsatz_service import _order

    order = _order(app_user, target="IN_AUSFUEHRUNG")
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    entry = einsatz_service.log_time(
        app_user.id, service_job_id=job.id, user_id=app_user.id,
        time_type="ARBEITSZEIT",
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 12),
    )
    assert entry.time_type == "ARBEITSZEIT"           # Property, keine Spalte
    assert entry.category.code == "ARBEITSZEIT"
    assert entry.work_day_id is not None              # Tagesklammer per Trigger


@pytest.mark.django_db
def test_log_time_am_einsatz_mit_kategorie(app_user):
    from db_core.tests.test_einsatz_service import _order

    order = _order(app_user, target="IN_AUSFUEHRUNG")
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    werkstatt = TimeCategory.objects.get(name="Werkstatt")
    entry = einsatz_service.log_time(
        app_user.id, service_job_id=job.id, user_id=app_user.id,
        category_id=werkstatt.id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 12),
    )
    assert entry.category.name == "Werkstatt"
    assert entry.time_type == "Werkstatt"


@pytest.mark.django_db
def test_zeit_ohne_einsatz_ist_erstklassig(app_user):
    """Der alte CHECK (nur INTERNE_ZEIT ohne Einsatz) ist weg — Werkstatt-,
    Büro- und Materialfahrtzeit hängen an keinem Termin."""
    werkstatt = TimeCategory.objects.get(name="Werkstatt")
    e = zeit.zeiteintrag_anlegen(
        app_user.id, user_id=app_user.id, category_id=werkstatt.id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 12),
    )
    assert e.service_job_id is None
    assert e.category.is_work_time is True
