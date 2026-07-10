"""API-Tests der Vier-Augen-Endpoints (security/approvals) über den
Django-Test-Client.

Geprüft wird das Rechte-Gating (LESEN für die Liste, FREIGEBEN für
Genehmigen/Ablehnen, ANLEGEN für Zurückziehen), 401 ohne Login, 403 mit
unzureichender Rolle, 422 bei Selbstgenehmigung/fehlender Begründung sowie der
Listenfilter nach Status.

Vier-Augen braucht ZWEI Akteure: `_client` baut ein Login-Konto samt
security.app_user und Rolle und gibt (Client, app_user) zurück, damit der
Antragsteller (per Service angelegt) und der eingeloggte Entscheider gezielt
gesteuert werden können.
"""
import json
import uuid

import pytest
from django.test import Client

from db_core.services import vier_augen

from .conftest import make_role_user

APPROVALS = "/api/security/approvals"


def _client(role="ADMINISTRATION"):
    """(eingeloggter Client, app_user) mit der gegebenen Rolle."""
    user, app_user = make_role_user(role)
    c = Client()
    c.force_login(user)
    return c, app_user


def _antrag(steller_app_user_id, *, action_code="BANKDATEN", target_id=None):
    return vier_augen.request_approval(
        steller_app_user_id, action_code=action_code,
        target_table="company.company_profile",
        target_id=target_id or uuid.uuid4(),
    )


# --- Authentifizierung / Rechte-Gating -------------------------------------

@pytest.mark.django_db
def test_liste_ohne_login_401(anonymous_client):
    """Die gesamte API ist anmeldepflichtig — ohne Login 401."""
    r = anonymous_client.get(APPROVALS)
    assert r.status_code == 401


@pytest.mark.django_db
def test_liste_ohne_leserecht_403():
    """DISPOSITION hat kein security/LESEN → 403 auf die Liste."""
    c, _ = _client("DISPOSITION")
    r = c.get(APPROVALS)
    assert r.status_code == 403


@pytest.mark.django_db
def test_liste_mit_nur_lesen_erlaubt(app_user):
    """NUR_LESEN hält security/LESEN und darf die Liste sehen."""
    c, _ = _client("NUR_LESEN")
    _antrag(app_user.id)
    r = c.get(APPROVALS)
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.django_db
def test_genehmigen_ohne_freigaberecht_403(app_user):
    """NUR_LESEN darf lesen, aber NICHT genehmigen (FREIGEBEN fehlt) → 403.

    Zeigt die Trennung LESEN vs. FREIGEBEN: das Leserecht allein genügt nicht.
    """
    c, _ = _client("NUR_LESEN")
    req = _antrag(app_user.id)
    r = c.post(f"{APPROVALS}/{req.id}/approve", content_type="application/json")
    assert r.status_code == 403
    req.refresh_from_db()
    assert req.status == "ANGEFORDERT"


@pytest.mark.django_db
def test_zurueckziehen_ohne_anlegerecht_403(app_user):
    """withdraw fordert security/ANLEGEN; NUR_LESEN hat es nicht → 403."""
    c, _ = _client("NUR_LESEN")
    req = _antrag(app_user.id)
    r = c.post(f"{APPROVALS}/{req.id}/withdraw", content_type="application/json")
    assert r.status_code == 403


# --- Genehmigen / Ablehnen (positive Fälle) --------------------------------

@pytest.mark.django_db
def test_genehmigen_durch_zweiten_akteur(app_user):
    """Ein anderer Akteur mit FREIGEBEN genehmigt den Antrag → 200, GENEHMIGT."""
    decider_client, _ = _client("ADMINISTRATION")
    req = _antrag(app_user.id)
    r = decider_client.post(
        f"{APPROVALS}/{req.id}/approve", content_type="application/json"
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "GENEHMIGT"
    assert body["decided_by_name"] is not None


@pytest.mark.django_db
def test_ablehnen_mit_begruendung(app_user):
    """Ablehnung mit Begründung → 200, ABGELEHNT, Notiz gesetzt."""
    decider_client, _ = _client("ADMINISTRATION")
    req = _antrag(app_user.id)
    r = decider_client.post(
        f"{APPROVALS}/{req.id}/reject",
        data=json.dumps({"note": "IBAN unplausibel"}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "ABGELEHNT"
    assert body["decision_note"] == "IBAN unplausibel"


@pytest.mark.django_db
def test_zurueckziehen_durch_antragsteller(app_user):
    """Der eingeloggte Antragsteller zieht seinen eigenen Antrag zurück → 200."""
    c, steller = _client("ADMINISTRATION")
    req = _antrag(steller.id)   # Antragsteller = eingeloggter Nutzer
    r = c.post(f"{APPROVALS}/{req.id}/withdraw", content_type="application/json")
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "ZURUECKGEZOGEN"


# --- Fachliche Tore (422) ---------------------------------------------------

@pytest.mark.django_db
def test_selbstgenehmigung_422():
    """Selbstgenehmigung: Antragsteller = eingeloggter Entscheider → 422 (nicht 403).

    Das Recht FREIGEBEN liegt vor; erst das fachliche Vier-Augen-Tor greift.
    """
    c, steller = _client("ADMINISTRATION")
    req = _antrag(steller.id)   # derselbe Nutzer stellt den Antrag
    r = c.post(f"{APPROVALS}/{req.id}/approve", content_type="application/json")
    assert r.status_code == 422
    req.refresh_from_db()
    assert req.status == "ANGEFORDERT"


@pytest.mark.django_db
def test_selbstablehnung_422():
    """Auch die Ablehnung des eigenen Antrags ist ein 422."""
    c, steller = _client("ADMINISTRATION")
    req = _antrag(steller.id)
    r = c.post(
        f"{APPROVALS}/{req.id}/reject",
        data=json.dumps({"note": "doch nicht"}),
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_ablehnen_ohne_begruendung_422(app_user):
    """Ablehnung ohne Begründung → 422 (begründungspflichtig)."""
    decider_client, _ = _client("ADMINISTRATION")
    req = _antrag(app_user.id)
    r = decider_client.post(
        f"{APPROVALS}/{req.id}/reject",
        data=json.dumps({"note": ""}),
        content_type="application/json",
    )
    assert r.status_code == 422
    # Auch ganz ohne Body (note fehlt) → 422.
    r2 = decider_client.post(
        f"{APPROVALS}/{req.id}/reject", data=json.dumps({}),
        content_type="application/json",
    )
    assert r2.status_code == 422


@pytest.mark.django_db
def test_fremd_zurueckziehen_422(app_user):
    """Ein Fremder (mit ANLEGEN-Recht) kann den Antrag nicht zurückziehen → 422."""
    c, _ = _client("ADMINISTRATION")   # nicht der Antragsteller
    req = _antrag(app_user.id)          # von jemand anderem gestellt
    r = c.post(f"{APPROVALS}/{req.id}/withdraw", content_type="application/json")
    assert r.status_code == 422
    req.refresh_from_db()
    assert req.status == "ANGEFORDERT"


@pytest.mark.django_db
def test_bereits_entschieden_422(app_user):
    """Ein bereits genehmigter Antrag kann nicht erneut entschieden werden → 422."""
    decider_client, _ = _client("ADMINISTRATION")
    req = _antrag(app_user.id)
    assert decider_client.post(
        f"{APPROVALS}/{req.id}/approve", content_type="application/json"
    ).status_code == 200
    r = decider_client.post(
        f"{APPROVALS}/{req.id}/approve", content_type="application/json"
    )
    assert r.status_code == 422


# --- Listenfilter -----------------------------------------------------------

@pytest.mark.django_db
def test_liste_filter_nach_status(app_user):
    """?status=ANGEFORDERT liefert nur offene Anträge."""
    decider_client, _ = _client("ADMINISTRATION")
    offen = _antrag(app_user.id)
    zu_genehmigen = _antrag(app_user.id)
    # Genehmigen über den API-Nutzer (anderer Akteur als der Antragsteller).
    decider_client.post(
        f"{APPROVALS}/{zu_genehmigen.id}/approve", content_type="application/json"
    )

    r = decider_client.get(f"{APPROVALS}?status=ANGEFORDERT")
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()}
    assert str(offen.id) in ids
    assert str(zu_genehmigen.id) not in ids

    r2 = decider_client.get(f"{APPROVALS}?status=GENEHMIGT")
    ids2 = {item["id"] for item in r2.json()}
    assert str(zu_genehmigen.id) in ids2
    assert str(offen.id) not in ids2


# --- Payload-Schutz ---------------------------------------------------------

def _antrag_mit_iban(steller_app_user_id):
    """Ein BANKDATEN-Antrag, dessen Payload die neue IBAN im Klartext trägt."""
    return vier_augen.request_approval(
        steller_app_user_id, action_code="BANKDATEN",
        payload={"iban": "DE12500105170648489890", "bank_name": "Testbank"},
        target_table="company.company_profile", target_id=uuid.uuid4(),
    )


@pytest.mark.django_db
def test_payload_verborgen_ohne_freigaberecht(app_user):
    """NUR_LESEN hält `security/LESEN` (Startmatrix 0026) und darf die Anträge
    sehen — aber NICHT die beantragten Daten im Klartext.

    Sonst läse jede Nur-Lese-Rolle die beantragte IBAN mit; sobald Personal-
    Bankdaten über denselben Flow laufen, wäre das ein DSGVO-Leck an einer Rolle
    ohne `hr`-Recht.
    """
    _antrag_mit_iban(app_user.id)
    leser, _ = _client("NUR_LESEN")
    eintrag = leser.get(APPROVALS).json()[0]
    assert eintrag["payload"] == {}
    assert eintrag["payload_verborgen"] is True
    assert "DE12500105170648489890" not in leser.get(APPROVALS).content.decode()


@pytest.mark.django_db
def test_payload_sichtbar_fuer_entscheider(app_user):
    """Wer entscheiden darf (security/FREIGEBEN), muss sehen, worüber."""
    _antrag_mit_iban(app_user.id)
    entscheider, _ = _client("ADMINISTRATION")
    eintrag = entscheider.get(APPROVALS).json()[0]
    assert eintrag["payload"]["iban"] == "DE12500105170648489890"
    assert eintrag["payload_verborgen"] is False


@pytest.mark.django_db
def test_payload_sichtbar_fuer_eigenen_antrag():
    """Der Antragsteller sieht seinen eigenen Payload — es sind seine Daten."""
    steller, steller_au = _client("NUR_LESEN")
    _antrag_mit_iban(steller_au.id)
    eintrag = steller.get(APPROVALS).json()[0]
    assert eintrag["payload"]["iban"] == "DE12500105170648489890"
    assert eintrag["payload_verborgen"] is False
