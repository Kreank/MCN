"""Service-Tests für den Vier-Augen-Freigabe-Flow (security.approval_request, 0028).

Deckt den kompletten Antrag/Freigabe-Zyklus über `db_core.services.vier_augen` ab
sowie die Verdrahtung der Firmen-Bankdaten (`services.firma.update_company_profile`
→ BANKDATEN-Antrag mit Applier).

Zwei Ebenen werden geprüft:
  * der Service-Guard (klarer ValueError → später 422), und
  * die physische DB-Durchsetzung (CHECK `decided_by <> requested_by`,
    Statusautomat-Trigger) — bewusst am Service vorbei per ORM-`.update()`.

Für das Vier-Augen-Prinzip braucht jeder Test ZWEI verschiedene Akteure
(Antragsteller ≠ Entscheider); dafür der `_actor`-Helfer.
"""
import uuid

import pytest
from django.db import Error as DBError
from django.db.utils import IntegrityError
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import AppUser, ApprovalRequest, CompanyProfile, FourEyesAction
from db_core.services import firma as firma_service
from db_core.services import vier_augen


def _actor(name="Akteur"):
    """Ein fachlicher security.app_user als Antragsteller bzw. Entscheider."""
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=name, status="ACTIVE", version=1,
    )


# --- Antrag stellen --------------------------------------------------------

@pytest.mark.django_db
def test_request_approval_legt_antrag_an(app_user):
    """Ein Antrag beginnt als ANGEFORDERT, trägt Antragsteller, Payload und Ziel."""
    ziel = uuid.uuid4()
    req = vier_augen.request_approval(
        app_user.id, action_code="BANKDATEN",
        payload={"iban": "DE111"},
        target_table="company.company_profile", target_id=ziel,
        reason="Kontowechsel",
    )
    assert req.status == "ANGEFORDERT"
    assert req.requested_by_id == app_user.id
    assert req.payload == {"iban": "DE111"}
    assert req.target_table == "company.company_profile"
    assert req.target_id == ziel
    assert req.decided_by_id is None
    assert req.applied_at is None


@pytest.mark.django_db
def test_request_approval_unbekannte_aktion(app_user):
    """Ein action_code, den es in security.four_eyes_action nicht gibt → ValueError."""
    with pytest.raises(ValueError, match="Unbekannte Vier-Augen-Aktion"):
        vier_augen.request_approval(app_user.id, action_code="GIBTESNICHT")


@pytest.mark.django_db
def test_request_approval_inaktive_aktion(app_user):
    """Eine deaktivierte Aktion darf keinen Antrag mehr erzeugen."""
    FourEyesAction.objects.filter(action_code="BANKDATEN").update(active=False)
    with pytest.raises(ValueError, match="nicht aktiv"):
        vier_augen.request_approval(app_user.id, action_code="BANKDATEN")


@pytest.mark.django_db
def test_request_approval_unvollstaendige_zielreferenz(app_user):
    """Zielreferenz nur als Paar: nur Tabelle ODER nur id ist verboten."""
    with pytest.raises(ValueError, match="Tabelle UND id"):
        vier_augen.request_approval(
            app_user.id, action_code="BANKDATEN",
            target_table="company.company_profile",
        )
    with pytest.raises(ValueError, match="Tabelle UND id"):
        vier_augen.request_approval(
            app_user.id, action_code="BANKDATEN", target_id=uuid.uuid4(),
        )


# --- Selbstgenehmigung: Service-Guard UND DB-CHECK -------------------------

@pytest.mark.django_db
def test_selbstgenehmigung_verboten_service(app_user):
    """Der Antragsteller darf seinen eigenen Antrag nicht genehmigen (Service-Guard)."""
    req = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    with pytest.raises(ValueError, match="nicht selbst"):
        vier_augen.approve(app_user.id, request_id=req.id)
    # Der Antrag bleibt unentschieden.
    req.refresh_from_db()
    assert req.status == "ANGEFORDERT"


@pytest.mark.django_db
def test_selbstablehnung_verboten_service(app_user):
    """Auch die Ablehnung darf nicht durch den Antragsteller erfolgen."""
    req = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    with pytest.raises(ValueError, match="nicht selbst"):
        vier_augen.reject(app_user.id, request_id=req.id, note="passt nicht")
    req.refresh_from_db()
    assert req.status == "ANGEFORDERT"


@pytest.mark.django_db
def test_db_check_decided_by_ungleich_requested_by(app_user):
    """Scharfe Prüfung des CHECK `approval_four_eyes`: der Service wird umgangen
    und direkt per ORM-`.update()` decided_by = requested_by gesetzt — die DB
    muss das physisch abweisen (IntegrityError)."""
    req = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    with pytest.raises(IntegrityError):
        with business_transaction(app_user.id):
            ApprovalRequest.objects.filter(id=req.id).update(
                status="GENEHMIGT",
                decided_by_id=app_user.id,   # == requested_by → CHECK-Verletzung
                decided_at=timezone.now(),
            )


# --- Ablehnung -------------------------------------------------------------

@pytest.mark.django_db
def test_reject_ohne_begruendung_verboten(app_user):
    """Ablehnung ist begründungspflichtig — leere/weiße Begründung → ValueError."""
    decider = _actor("Entscheider")
    req = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    with pytest.raises(ValueError, match="begründungspflichtig"):
        vier_augen.reject(decider.id, request_id=req.id, note="")
    with pytest.raises(ValueError, match="begründungspflichtig"):
        vier_augen.reject(decider.id, request_id=req.id, note="   ")
    with pytest.raises(ValueError, match="begründungspflichtig"):
        vier_augen.reject(decider.id, request_id=req.id, note=None)


@pytest.mark.django_db
def test_reject_mit_begruendung(app_user):
    """Mit Begründung: Status ABGELEHNT, Entscheider und Notiz gesetzt (getrimmt)."""
    decider = _actor("Entscheider")
    req = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    out = vier_augen.reject(decider.id, request_id=req.id, note="  IBAN unplausibel ")
    assert out.status == "ABGELEHNT"
    assert out.decided_by_id == decider.id
    assert out.decision_note == "IBAN unplausibel"
    assert out.decided_at is not None


# --- Zurückziehen ----------------------------------------------------------

@pytest.mark.django_db
def test_withdraw_nur_antragsteller(app_user):
    """Nur der Antragsteller darf zurückziehen; ein Fremder → ValueError."""
    fremder = _actor("Fremder")
    req = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    with pytest.raises(ValueError, match="Nur der Antragsteller"):
        vier_augen.withdraw(fremder.id, request_id=req.id)
    req.refresh_from_db()
    assert req.status == "ANGEFORDERT"
    # Der Antragsteller selbst kann zurückziehen.
    out = vier_augen.withdraw(app_user.id, request_id=req.id)
    assert out.status == "ZURUECKGEZOGEN"


# --- Statusautomat: keine erneute Entscheidung -----------------------------

@pytest.mark.django_db
def test_bereits_entschieden_nicht_erneut(app_user):
    """Ein entschiedener Antrag lässt sich nicht erneut entscheiden (Service-Guard)."""
    decider = _actor("Entscheider")
    req = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    vier_augen.approve(decider.id, request_id=req.id)
    with pytest.raises(ValueError, match="bereits entschieden"):
        vier_augen.approve(decider.id, request_id=req.id)
    with pytest.raises(ValueError, match="bereits entschieden"):
        vier_augen.reject(decider.id, request_id=req.id, note="zu spät")
    with pytest.raises(ValueError, match="bereits entschieden"):
        vier_augen.withdraw(app_user.id, request_id=req.id)


@pytest.mark.django_db
def test_statusautomat_terminal_ist_final_db(app_user):
    """DB-Trigger `enforce_approval_status`: aus einem Endzustand führt kein Weg
    zurück. Am Service vorbei per ORM-`.update()` geprüft."""
    decider = _actor("Entscheider")
    req = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    vier_augen.approve(decider.id, request_id=req.id)
    with pytest.raises(DBError):
        with business_transaction(decider.id):
            ApprovalRequest.objects.filter(id=req.id).update(status="ZURUECKGEZOGEN")


# --- BANKDATEN-Applier über das Firmenprofil -------------------------------

@pytest.mark.django_db
def test_bankdaten_erstanlage_schreibt_direkt(app_user):
    """Erstanlage eines Profils schreibt Bankdaten sofort (kein Antrag nötig)."""
    profile, pending = firma_service.update_company_profile(
        app_user.id, company_name="Erst GmbH", iban="DE00111111111111111111",
    )
    assert pending is None
    assert profile.iban == "DE00111111111111111111"
    # Es entstand kein Freigabeantrag.
    assert not ApprovalRequest.objects.filter(action_id="BANKDATEN").exists()


@pytest.mark.django_db
def test_bankdaten_aenderung_erst_nach_genehmigung_wirksam(app_user):
    """IBAN-Änderung an einem BESTEHENDEN Profil: erst ein Antrag, das Profil
    bleibt zunächst unverändert; nach approve() ist die IBAN geschrieben und die
    Genehmigung als verbraucht markiert (applied_at)."""
    decider = _actor("Entscheider")
    profile, _ = firma_service.update_company_profile(
        app_user.id, company_name="Best GmbH", iban="DE00111111111111111111",
    )
    alt = profile.iban

    profile2, pending = firma_service.update_company_profile(
        app_user.id, iban="DE99222222222222222222", bank_name="Neue Bank",
    )
    assert pending is not None
    assert pending.action_id == "BANKDATEN"
    assert pending.target_table == "company.company_profile"
    assert pending.target_id == profile.id
    # Bankdaten noch NICHT geschrieben.
    profile2.refresh_from_db()
    assert profile2.iban == alt
    assert profile2.bank_name is None

    granted = vier_augen.approve(decider.id, request_id=pending.id)
    assert granted.status == "GENEHMIGT"
    assert granted.applied_at is not None   # Applier hat verbraucht

    profile.refresh_from_db()
    assert profile.iban == "DE99222222222222222222"
    assert profile.bank_name == "Neue Bank"
    # Eine verbrauchte Genehmigung wird von der Torfunktion nicht mehr gefunden.
    assert vier_augen.find_grant(
        "BANKDATEN", target_table="company.company_profile", target_id=profile.id
    ) is None


@pytest.mark.django_db
def test_bankdaten_unveraenderte_werte_loesen_keinen_antrag(app_user):
    """Wird die IBAN auf denselben Wert 'geändert', entsteht kein Antrag."""
    profile, _ = firma_service.update_company_profile(
        app_user.id, company_name="Gleich GmbH", iban="DE00111111111111111111",
    )
    _, pending = firma_service.update_company_profile(
        app_user.id, iban="DE00111111111111111111", city="Musterstadt",
    )
    assert pending is None
    profile.refresh_from_db()
    assert profile.city == "Musterstadt"   # Nicht-Bankfeld direkt übernommen


# --- Einmaligkeit: consume/find_grant (Tor-Aktion ohne Applier) ------------

@pytest.mark.django_db
def test_consume_wirkt_genau_einmal(app_user):
    """Eine Tor-Genehmigung (RECHNUNGSKORREKTUR, kein Applier) wird durch approve()
    NICHT verbraucht; consume() wirkt genau einmal, ein zweites Mal → ValueError."""
    decider = _actor("Entscheider")
    ziel = uuid.uuid4()
    req = vier_augen.request_approval(
        app_user.id, action_code="RECHNUNGSKORREKTUR",
        target_table="invoicing.invoice", target_id=ziel,
    )
    granted = vier_augen.approve(decider.id, request_id=req.id)
    # Ohne Applier bleibt applied_at nach dem Genehmigen offen (Tor-Muster).
    assert granted.applied_at is None

    grant = vier_augen.assert_approved(
        "RECHNUNGSKORREKTUR", target_table="invoicing.invoice", target_id=ziel
    )
    assert grant.id == req.id

    vier_augen.consume(decider.id, request_id=req.id)
    with pytest.raises(ValueError, match="nicht \\(mehr\\) verfügbar"):
        vier_augen.consume(decider.id, request_id=req.id)


@pytest.mark.django_db
def test_find_grant_nach_verbrauch_leer(app_user):
    """Nach consume() findet die Torfunktion die Genehmigung nicht mehr → NotApproved."""
    decider = _actor("Entscheider")
    ziel = uuid.uuid4()
    req = vier_augen.request_approval(
        app_user.id, action_code="RECHNUNGSKORREKTUR",
        target_table="invoicing.invoice", target_id=ziel,
    )
    vier_augen.approve(decider.id, request_id=req.id)
    vier_augen.consume(decider.id, request_id=req.id)

    assert vier_augen.find_grant(
        "RECHNUNGSKORREKTUR", target_table="invoicing.invoice", target_id=ziel
    ) is None
    with pytest.raises(vier_augen.NotApproved):
        vier_augen.assert_approved(
            "RECHNUNGSKORREKTUR", target_table="invoicing.invoice", target_id=ziel
        )


@pytest.mark.django_db
def test_assert_approved_ohne_genehmigung_wirft(app_user):
    """Ohne erteilte Genehmigung wirft die Torfunktion NotApproved."""
    with pytest.raises(vier_augen.NotApproved):
        vier_augen.assert_approved(
            "RECHNUNGSKORREKTUR", target_table="invoicing.invoice",
            target_id=uuid.uuid4(),
        )


@pytest.mark.django_db
def test_abgelehnter_antrag_ist_keine_genehmigung(app_user):
    """Ein abgelehnter Antrag erteilt keine Genehmigung (find_grant leer)."""
    decider = _actor("Entscheider")
    ziel = uuid.uuid4()
    req = vier_augen.request_approval(
        app_user.id, action_code="RECHNUNGSKORREKTUR",
        target_table="invoicing.invoice", target_id=ziel,
    )
    vier_augen.reject(decider.id, request_id=req.id, note="nicht nachvollziehbar")
    assert vier_augen.find_grant(
        "RECHNUNGSKORREKTUR", target_table="invoicing.invoice", target_id=ziel
    ) is None


# --- Liste / Filter --------------------------------------------------------

@pytest.mark.django_db
def test_list_requests_filter_nach_status(app_user):
    """list_requests filtert nach Status; ohne Filter kommen alle."""
    decider = _actor("Entscheider")
    offen = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    zu_genehmigen = vier_augen.request_approval(app_user.id, action_code="BANKDATEN")
    vier_augen.approve(decider.id, request_id=zu_genehmigen.id)

    alle_ids = {r.id for r in vier_augen.list_requests()}
    assert {offen.id, zu_genehmigen.id} <= alle_ids

    offene = list(vier_augen.list_requests(status="ANGEFORDERT"))
    assert [r.id for r in offene] == [offen.id]

    genehmigte = list(vier_augen.list_requests(status="GENEHMIGT"))
    assert [r.id for r in genehmigte] == [zu_genehmigen.id]


@pytest.mark.django_db
def test_find_pending_dedupe(app_user):
    """find_pending findet den offenen Antrag zu Aktion + Ziel (Dedupe-Helfer)."""
    ziel = uuid.uuid4()
    req = vier_augen.request_approval(
        app_user.id, action_code="RECHNUNGSKORREKTUR",
        target_table="invoicing.invoice", target_id=ziel,
    )
    found = vier_augen.find_pending(
        "RECHNUNGSKORREKTUR", target_table="invoicing.invoice", target_id=ziel
    )
    assert found is not None and found.id == req.id
    # Nach dem Zurückziehen ist nichts mehr offen.
    vier_augen.withdraw(app_user.id, request_id=req.id)
    assert vier_augen.find_pending(
        "RECHNUNGSKORREKTUR", target_table="invoicing.invoice", target_id=ziel
    ) is None
