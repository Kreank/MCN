"""Der Token-Unterbau für anmeldefreie Links (`security.public_link`, 0141) und
der Systemakteur, der die daraus entstehenden Schreibvorgänge trägt.

Bewiesen wird gegen die echte Test-DB (mit allen Triggern):

  * Der Klartext verlässt den Server genau einmal — in der Datenbank steht nur
    ein 64-stelliger Hex-Hash, und der CHECK erzwingt das physisch.
  * Auflösen liefert für **jeden** Ungültigkeitsgrund dasselbe `None`.
  * Einlösen ist einmalig (Replay-Schutz), und der Guard-Trigger lässt weder
    Zielwechsel noch Zurücknehmen von Einlösung/Widerruf zu.
  * Der Systemakteur existiert, ist als technisch gekennzeichnet — und kann sich
    **nicht anmelden**: Ein Login-Konto für ihn weist ein DB-Trigger ab.

Savepoints statt `transaction=True`: Ein Trigger-Verstoß macht die laufende
Transaktion unbrauchbar; `pytest.raises` allein genügte nicht, danach wäre jede
weitere Abfrage ein InternalError. `transaction=True` wäre der andere Weg — er
erzeugt in diesem Repo aber die bekannten Teardown-Artefakte.
"""
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.db import DatabaseError, transaction

from db_core.db_context import business_transaction
from db_core.models import AppUser, PublicLink
from db_core.services import oeffentlicher_link as link_service

User = get_user_model()

ZIEL = link_service.ZIEL_ANGEBOT
ZWECK = link_service.PURPOSE_ANGEBOT_FREIGABE


def _spaeter(tage=14):
    return datetime.now(dt_timezone.utc) + timedelta(days=tage)


def abgelaufener_link(app_user, klartext, *, target_id=None):
    """Eine Link-Zeile, wie sie nach Ablauf der Frist dasteht.

    Direkt per INSERT: `expires_at` ist nach dem Anlegen unveränderlich (der
    Guard-Trigger, den ein eigener Test nachweist), also lässt sich ein Link
    nicht nachträglich „altern". Der INSERT trägt keine Trigger-Sperre — er
    erzeugt genau den Zustand, den die Uhr sonst nach 14 Tagen herstellt.
    """
    return PublicLink.objects.create(
        id=uuid.uuid4(),
        purpose=ZWECK,
        target_type=ZIEL,
        target_id=target_id or uuid.uuid4(),
        token_hash=link_service._hash(klartext),
        expires_at=datetime.now(dt_timezone.utc) - timedelta(minutes=1),
        created_by_id=app_user.id,
        use_count=0,
        version=1,
    )


def _link(app_user, **kwargs):
    daten = dict(
        purpose=ZWECK, target_type=ZIEL, target_id=uuid.uuid4(),
        gueltig_bis=_spaeter(),
    )
    daten.update(kwargs)
    return link_service.link_erzeugen(app_user.id, **daten)


# --- Erzeugen ---------------------------------------------------------------

@pytest.mark.django_db
def test_klartext_steht_nur_in_der_antwort(app_user):
    zeile, klartext = _link(app_user)
    assert len(klartext) >= 40                       # token_urlsafe(32)
    zeile.refresh_from_db()
    assert zeile.token_hash != klartext
    assert len(zeile.token_hash) == 64
    assert set(zeile.token_hash) <= set("0123456789abcdef")
    # Und nirgendwo sonst in der Zeile.
    assert klartext not in str(zeile.__dict__)


@pytest.mark.django_db
def test_zwei_links_haben_verschiedene_token(app_user):
    _, a = _link(app_user)
    _, b = _link(app_user)
    assert a != b


@pytest.mark.django_db
def test_ablauf_muss_in_der_zukunft_liegen(app_user):
    with pytest.raises(ValueError, match="Zukunft"):
        _link(app_user, gueltig_bis=_spaeter(tage=-1))


@pytest.mark.django_db
def test_gueltigkeit_ist_gedeckelt(app_user):
    with pytest.raises(ValueError, match="höchstens"):
        _link(app_user, gueltig_bis=_spaeter(tage=400))


@pytest.mark.django_db
def test_klartext_laesst_sich_nicht_in_die_spalte_schreiben(app_user):
    """Der CHECK auf `token_hash` macht „nur Hashes" physisch, nicht bloß
    konventionell — der Weg an der Service-Schicht vorbei ist versperrt."""
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            with business_transaction(app_user.id):
                PublicLink.objects.create(
                    id=uuid.uuid4(), purpose=ZWECK, target_type=ZIEL,
                    target_id=uuid.uuid4(), token_hash="klartext-token",
                    expires_at=_spaeter(), created_by_id=app_user.id, version=1,
                )


# --- Auflösen: EINE Antwort für jeden Ungültigkeitsgrund ---------------------

@pytest.mark.django_db
def test_aufloesen_findet_gueltigen_link(app_user):
    zeile, klartext = _link(app_user)
    gefunden = link_service.link_aufloesen(klartext, purpose=ZWECK)
    assert gefunden is not None and gefunden.id == zeile.id


@pytest.mark.django_db
@pytest.mark.parametrize("kaputt", ["", "   ", "x", "a" * 400, "../../etc/passwd",
                                    "' OR 1=1 --"])
def test_aufloesen_unsinn_ist_none(app_user, kaputt):
    assert link_service.link_aufloesen(kaputt, purpose=ZWECK) is None


@pytest.mark.django_db
def test_aufloesen_unbekannt_abgelaufen_widerrufen(app_user):
    """Drei Gründe, ein Ergebnis. Wer sie unterscheiden könnte, hätte ein Orakel.

    **Bereits eingelöst gehört bewusst NICHT dazu** — siehe den Test darunter.
    """
    # (1) unbekannt
    assert link_service.link_aufloesen("A" * 43, purpose=ZWECK) is None

    # (2) abgelaufen. `expires_at` ist nach dem Anlegen eingefroren (Guard-
    #     Trigger, eigener Test) — die abgelaufene Zeile entsteht deshalb direkt
    #     per INSERT, wie sie nach 14 Tagen Liegezeit aussähe.
    klartext_alt = "B" * 43
    abgelaufen = abgelaufener_link(app_user, klartext_alt)
    assert abgelaufen.expires_at < datetime.now(dt_timezone.utc)
    assert link_service.link_aufloesen(klartext_alt, purpose=ZWECK) is None

    # (3) widerrufen
    zeile2, klartext2 = _link(app_user)
    link_service.link_widerrufen(app_user.id, zeile2.id)
    assert link_service.link_aufloesen(klartext2, purpose=ZWECK) is None


@pytest.mark.django_db
def test_eingeloester_link_bleibt_bis_zum_ablauf_lesbar(app_user):
    """Wer eingelöst hat, hat den Besitz nachgewiesen — er darf den Ausgang sehen.

    Ihn wie ein unbekanntes Token zu behandeln verrät niemandem etwas; es lässt
    nur den Kunden glauben, seine Zusage sei fehlgeschlagen. Einlösbar ist er
    trotzdem nicht mehr (nächster Test).
    """
    zeile, klartext = _link(app_user)
    with business_transaction(app_user.id):
        link_service.einloesen(zeile.id)

    gefunden = link_service.link_aufloesen(klartext, purpose=ZWECK)
    assert gefunden is not None
    assert gefunden.id == zeile.id
    assert gefunden.used_at is not None
    # „offen" ist er dennoch nicht — er trägt nichts mehr aus.
    assert link_service.ist_offen(gefunden) is False


@pytest.mark.django_db
def test_eingeloester_link_wird_mit_dem_ablauf_doch_unsichtbar(app_user):
    """Die Lesbarkeit endet an `expires_at`, nicht an der Einlösung."""
    zeile = abgelaufener_link(app_user, "C" * 43)
    with business_transaction(app_user.id):
        PublicLink.objects.filter(id=zeile.id).update(
            used_at=datetime.now(dt_timezone.utc) - timedelta(minutes=2),
            use_count=1,
        )
    assert link_service.link_aufloesen("C" * 43, purpose=ZWECK) is None


@pytest.mark.django_db
def test_falscher_zweck_loest_nicht_auf(app_user):
    """Ein Link für Zweck A darf für Zweck B nicht gelten — sonst wäre der Zweck
    ein Etikett und keine Grenze."""
    _, klartext = _link(app_user)
    assert link_service.link_aufloesen(klartext, purpose="ETWAS_ANDERES") is None


# --- Einlösen: einmal und nur einmal ----------------------------------------

@pytest.mark.django_db
def test_einloesen_ist_einmalig(app_user):
    zeile, _ = _link(app_user)
    with business_transaction(app_user.id):
        eingeloest = link_service.einloesen(zeile.id)
    assert eingeloest.used_at is not None
    assert eingeloest.use_count == 1

    with pytest.raises(link_service.LinkError, match="bereits verwendet"):
        with business_transaction(app_user.id):
            link_service.einloesen(zeile.id)


@pytest.mark.django_db
def test_widerrufener_link_ist_nicht_einloesbar(app_user):
    zeile, _ = _link(app_user)
    link_service.link_widerrufen(app_user.id, zeile.id)
    with pytest.raises(link_service.LinkError):
        with business_transaction(app_user.id):
            link_service.einloesen(zeile.id)


@pytest.mark.django_db
def test_widerruf_ist_idempotent(app_user):
    zeile, _ = _link(app_user)
    assert link_service.link_widerrufen(app_user.id, zeile.id) is True
    assert link_service.link_widerrufen(app_user.id, zeile.id) is True
    assert link_service.link_widerrufen(app_user.id, uuid.uuid4()) is False


@pytest.mark.django_db
def test_widerruf_geht_nicht_ueber_die_zweckgrenze(app_user):
    """Wer Links EINES Zwecks widerrufen darf, darf nicht die aller Zwecke.

    Die Tabelle trägt die Links aller Bereiche; die Endpunkte hängen je an einem
    Modulrecht. Ohne diese Grenze legte das Angebotsrecht künftige
    Terminbuchungs-Links still.
    """
    zeile, _ = _link(app_user)
    assert link_service.link_widerrufen(
        app_user.id, zeile.id, purpose="EIN_ANDERER_ZWECK"
    ) is False
    zeile.refresh_from_db()
    assert zeile.revoked_at is None

    assert link_service.link_widerrufen(
        app_user.id, zeile.id, purpose=ZWECK
    ) is True
    zeile.refresh_from_db()
    assert zeile.revoked_at is not None


# --- Einmalig / mehrfach ----------------------------------------------------

@pytest.mark.django_db
def test_angebotslink_ist_einmalig(app_user):
    """Der Zweck bestimmt die Einmaligkeit — nicht der Aufrufer."""
    zeile, _ = _link(app_user)
    assert zeile.single_use is True


@pytest.mark.django_db
def test_unbekannter_zweck_ist_fail_closed_einmalig(app_user, monkeypatch):
    """Ein Zweck, den `_EINMALIG_JE_ZWECK` nicht kennt, wird einmalig — nicht
    versehentlich mehrfach nutzbar."""
    monkeypatch.setattr(link_service, "_EINMALIG_JE_ZWECK", {})
    zeile, _ = _link(app_user)
    assert zeile.single_use is True


@pytest.mark.django_db
def test_mehrfach_nutzbarer_link_traegt_mehrere_einloesungen(app_user):
    """Der Unterbau muss Absage/Umbuchung tragen können (Terminbuchung).

    Erzeugt wird er über denselben Weg — nur mit einem Zweck, der in
    `_EINMALIG_JE_ZWECK` auf `False` steht. Der Angebotsfall bleibt davon
    unberührt (Test darüber).
    """
    zeile = PublicLink.objects.create(
        id=uuid.uuid4(), purpose=ZWECK, target_type=ZIEL,
        target_id=uuid.uuid4(), token_hash=link_service._hash("D" * 43),
        expires_at=_spaeter(), single_use=False,
        created_by_id=app_user.id, use_count=0, version=1,
    )
    for erwartet in (1, 2, 3):
        with business_transaction(app_user.id):
            aktuell = link_service.einloesen(zeile.id)
        assert aktuell.use_count == erwartet
        assert aktuell.used_at is not None
    # Und er bleibt „offen": mit ihm geht noch etwas.
    assert link_service.ist_offen(aktuell) is True
    assert link_service.link_aufloesen("D" * 43, purpose=ZWECK) is not None


@pytest.mark.django_db
def test_einmaligkeit_haelt_auch_ohne_den_service(app_user):
    """`single_use` ist ein DB-CHECK, kein Service-Versprechen — der Weg an der
    Service-Schicht vorbei ist versperrt."""
    zeile, _ = _link(app_user)
    with business_transaction(app_user.id):
        link_service.einloesen(zeile.id)
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(
        id=zeile.id).update(use_count=2))


@pytest.mark.django_db
def test_einmaligkeit_ist_unveraenderlich(app_user):
    """Ein Link, der sich nachträglich auf „mehrfach" stellen ließe, wäre kein
    Einmal-Token."""
    zeile, _ = _link(app_user)
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(
        id=zeile.id).update(single_use=False))


# --- Der Guard-Trigger ------------------------------------------------------

def _erwarte_trigger(app_user, aktion):
    """Führt `aktion` aus und erwartet einen DB-Fehler — im eigenen Savepoint,
    damit die umgebende Testtransaktion danach weiterlebt."""
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            with business_transaction(app_user.id):
                aktion()


@pytest.mark.django_db
def test_ziel_und_token_sind_unveraenderlich(app_user):
    zeile, _ = _link(app_user)
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(
        id=zeile.id).update(target_id=uuid.uuid4()))
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(
        id=zeile.id).update(token_hash="f" * 64))
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(
        id=zeile.id).update(expires_at=_spaeter(tage=80)))


@pytest.mark.django_db
def test_einloesung_und_widerruf_sind_einbahnstrassen(app_user):
    zeile, _ = _link(app_user)
    with business_transaction(app_user.id):
        link_service.einloesen(zeile.id)
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(
        id=zeile.id).update(used_at=None))

    zeile2, _ = _link(app_user)
    link_service.link_widerrufen(app_user.id, zeile2.id)
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(
        id=zeile2.id).update(revoked_at=None))


@pytest.mark.django_db
def test_nutzungszaehlung_laeuft_nicht_rueckwaerts(app_user):
    zeile, _ = _link(app_user)
    with business_transaction(app_user.id):
        link_service.einloesen(zeile.id)
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(
        id=zeile.id).update(use_count=0))


@pytest.mark.django_db
def test_link_ist_nicht_loeschbar(app_user):
    zeile, _ = _link(app_user)
    _erwarte_trigger(app_user, lambda: PublicLink.objects.filter(id=zeile.id).delete())
    assert PublicLink.objects.filter(id=zeile.id).exists()


# --- Der Systemakteur -------------------------------------------------------

@pytest.mark.django_db
def test_systemakteur_existiert_und_ist_technisch():
    actor = link_service.systemakteur()
    assert actor.id == link_service.SYSTEMAKTEUR_ID
    assert actor.is_system is True
    assert actor.status == "ACTIVE"
    assert actor.display_name == "Online-Selbstbedienung"


@pytest.mark.django_db
def test_systemakteur_kann_sich_nicht_anmelden():
    """Angemeldet wird sich über `accounts.User` — für den Systemakteur gibt es
    keinen, und es kann auch keiner entstehen.

    Drei Nachweise: (1) es existiert kein Login-Konto zu seiner app_user_id,
    (2) der Versuch, eines anzulegen, prallt am DB-Trigger ab (nicht an einer
    Service-Prüfung, die man umgehen könnte), (3) `authenticate` findet ihn auch
    unter seinem Anzeigenamen nicht.
    """
    assert not User.objects.filter(
        app_user_id=link_service.SYSTEMAKTEUR_ID
    ).exists()

    with pytest.raises(DatabaseError, match="technischer Akteur"):
        with transaction.atomic():
            u = User(username="systemakteur@example.test",
                     email="systemakteur@example.test")
            u.set_password("irgendein-passwort-2026")
            u.app_user_id = link_service.SYSTEMAKTEUR_ID
            u.save()

    assert authenticate(
        None, email="systemakteur@example.test", password="irgendein-passwort-2026"
    ) is None
    assert authenticate(
        None, email="Online-Selbstbedienung", password="irgendein-passwort-2026"
    ) is None


@pytest.mark.django_db
def test_bestehendes_konto_laesst_sich_nicht_auf_den_systemakteur_umhaengen():
    """Der Trigger greift auch beim UPDATE — sonst wäre die Sperre über den
    Umweg „erst anlegen, dann umhängen" wirkungslos."""
    u = User(username="mensch@example.test", email="mensch@example.test")
    u.set_password("mensch-passwort-2026")
    u.save()
    with pytest.raises(DatabaseError, match="technischer Akteur"):
        with transaction.atomic():
            u.app_user_id = link_service.SYSTEMAKTEUR_ID
            u.save(update_fields=["app_user_id"])


@pytest.mark.django_db
def test_normaler_akteur_darf_weiterhin_ein_konto_haben(app_user):
    """Gegenprobe — der Trigger sperrt NUR Systemakteure. Ohne diese Prüfung
    könnte er alles sperren und der Test oben bestünde trotzdem."""
    u = User(username="normal@example.test", email="normal@example.test")
    u.set_password("normal-passwort-2026")
    u.app_user_id = app_user.id
    u.save()
    assert User.objects.filter(app_user_id=app_user.id).exists()


@pytest.mark.django_db
def test_systemakteur_traegt_genau_ein_recht():
    from db_core.services import rechte as rechte_service

    rechte = rechte_service.effective_permissions(link_service.SYSTEMAKTEUR_ID)
    assert rechte == {("invoicing", "AENDERN"): "ALLE"}


@pytest.mark.django_db
def test_systemakteur_ist_kein_benachrichtigungsempfaenger():
    """Er hat kein Postfach, das jemand liest — `empfaenger_mit_recht` lässt ihn
    deshalb aus, auch wenn er das Recht trüge."""
    from db_core.services import rechte as rechte_service

    empfaenger = rechte_service.empfaenger_mit_recht("invoicing", "AENDERN")
    assert link_service.SYSTEMAKTEUR_ID not in empfaenger


@pytest.mark.django_db
def test_scheduler_faellt_auf_den_systemakteur_zurueck():
    """Ohne `--actor` nahmen die Automaten „den ältesten aktiven Account" — und
    schrieben einem zufälligen Menschen ihre Taten zu.

    Der ältere Mensch existiert hier bewusst: Gegen eine leere Tabelle bewiese
    der Test nichts, er bestünde auch mit dem alten Verhalten.
    """
    from db_core.management.commands.wartung_faellige_ausloesen import (
        Command as WartungCommand,
    )
    from db_core.management.commands.ki_tool_queue_tick import Command as KiCommand

    aeltester = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Ein Mensch", status="ACTIVE", version=1,
    )
    AppUser.objects.filter(id=aeltester.id).update(
        created_at=datetime(2000, 1, 1, tzinfo=dt_timezone.utc)
    )

    assert WartungCommand()._actor(None).id == link_service.SYSTEMAKTEUR_ID
    assert KiCommand()._actor(None).id == link_service.SYSTEMAKTEUR_ID
    # --actor bleibt Übersteuerung.
    assert WartungCommand()._actor(str(aeltester.id)).id == aeltester.id
