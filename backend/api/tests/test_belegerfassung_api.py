"""API-Tests der Belegerfassung (/api/accounting) über den Django-Test-Client.

Deckt ab: Buchungskonten/Kostenstellen (CRUD, Eindeutigkeit → 422), Eingangsbelege
(Liste mit Filter/Paginierung, Detail mit Positionen + Status-Historie, Anlegen,
Ändern, Statuswechsel), das Freigabe-Tor (Kontierung → 422), unbekannte UUID → 404,
unbekannter Fremdschlüssel im Payload → 422 (nicht 500) und die Rechte-Torung:
401 ohne Login, 403 für eine Rolle ohne accounting-Recht, und der eigene
FREIGEBEN-Bedarf für FREIGEGEBEN/GEBUCHT.

Setup läuft über die Services (mit der `app_user`-Fixture als Akteur); die Clients
lesen/schreiben dann über die anmeldepflichtige API. Modul-Recht: `accounting`.
"""
import uuid
from datetime import date

import pytest

from db_core.services import belegerfassung as service
from db_core.services import identity as identity_service

pytestmark = pytest.mark.django_db

BASE = "/api/accounting"


# ---------------------------------------------------------------------------
# Setup-Helfer (über die Services, mit der app_user-Fixture)
# ---------------------------------------------------------------------------

def _supplier(app_user):
    return identity_service.create_person(
        app_user.id, first_name="Liefer", last_name="Ant"
    )


def _ledger(app_user, number="5400"):
    return service.create_ledger_account(
        app_user.id, account_number=number, label="Wareneingang",
        account_type="AUFWAND",
    )


def _receipt(app_user, *, kontiert=True, lines=None):
    supplier = _supplier(app_user)
    if lines is None:
        # Eindeutige Kontonummer je Aufruf (mehrere Belege in EINEM Test möglich).
        ledger_id = _ledger(app_user, number=uuid.uuid4().hex[:8]).id if kontiert else None
        lines = [{
            "description": "Rohre", "quantity": 10, "unit_price": 3,
            "tax_code": "DE_19", "unit": "Stk",
            "ledger_account_id": ledger_id, "cost_center_id": None,
        }]
    return service.create_receipt(
        app_user.id, supplier_party_id=supplier.id, receipt_date="2026-07-01",
        lines=lines,
    )


@pytest.fixture
def seeded(app_user):
    """Ein kontierter Eingangsbeleg (ERFASST) + ein Buchungskonto + Lieferant."""
    supplier = _supplier(app_user)
    ledger = _ledger(app_user, number="5401")
    r = service.create_receipt(
        app_user.id, supplier_party_id=supplier.id, receipt_date="2026-07-01",
        supplier_invoice_number="LR-7",
        lines=[{
            "description": "Rohre", "quantity": 10, "unit_price": 3,
            "tax_code": "DE_19", "unit": "Stk",
            "ledger_account_id": ledger.id, "cost_center_id": None,
        }],
    )
    return {"receipt": r, "ledger": ledger, "supplier": supplier}


# ---------------------------------------------------------------------------
# Buchungskonten / Kostenstellen
# ---------------------------------------------------------------------------

def test_ledger_account_crud(admin_client):
    r = admin_client.post(
        f"{BASE}/ledger-accounts",
        data={"account_number": "6000", "label": "Fremdleistung",
              "account_type": "AUFWAND"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    acc_id = r.json()["id"]
    assert r.json()["active"] is True

    lst = admin_client.get(f"{BASE}/ledger-accounts").json()
    assert any(a["id"] == acc_id for a in lst)

    upd = admin_client.put(
        f"{BASE}/ledger-accounts/{acc_id}",
        data={"label": "Neu", "active": False},
        content_type="application/json",
    )
    assert upd.status_code == 200
    assert upd.json()["label"] == "Neu"
    assert upd.json()["active"] is False

    # include_inactive=false blendet das archivierte Konto aus.
    aktive = admin_client.get(f"{BASE}/ledger-accounts?include_inactive=false").json()
    assert all(a["id"] != acc_id for a in aktive)


def test_ledger_account_duplikat_422(admin_client):
    admin_client.post(
        f"{BASE}/ledger-accounts",
        data={"account_number": "6001", "label": "A", "account_type": "AKTIV"},
        content_type="application/json",
    )
    r = admin_client.post(
        f"{BASE}/ledger-accounts",
        data={"account_number": "6001", "label": "B", "account_type": "AKTIV"},
        content_type="application/json",
    )
    assert r.status_code == 422


def test_ledger_account_update_unbekannt_422(admin_client):
    r = admin_client.put(
        f"{BASE}/ledger-accounts/{uuid.uuid4()}",
        data={"label": "X"}, content_type="application/json",
    )
    assert r.status_code == 422


def test_cost_center_crud(admin_client):
    r = admin_client.post(
        f"{BASE}/cost-centers",
        data={"code": "K900", "label": "Zentrale"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    cc_id = r.json()["id"]
    upd = admin_client.put(
        f"{BASE}/cost-centers/{cc_id}",
        data={"active": False}, content_type="application/json",
    )
    assert upd.status_code == 200
    assert upd.json()["active"] is False


def test_cost_center_duplikat_422(admin_client):
    admin_client.post(
        f"{BASE}/cost-centers", data={"code": "K901", "label": "A"},
        content_type="application/json",
    )
    r = admin_client.post(
        f"{BASE}/cost-centers", data={"code": "K901", "label": "B"},
        content_type="application/json",
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Eingangsbeleg: Anlegen / Detail / Liste
# ---------------------------------------------------------------------------

def test_create_receipt_api(admin_client, app_user):
    ledger = _ledger(app_user, number="5500")
    supplier = _supplier(app_user)
    r = admin_client.post(
        f"{BASE}/receipts",
        data={
            "supplier_party_id": str(supplier.id),
            "receipt_date": "2026-07-01",
            "supplier_invoice_number": "LR-99",
            "lines": [{
                "description": "Kabel", "quantity": "5", "unit_price": "4",
                "tax_code": "DE_19", "ledger_account_id": str(ledger.id),
            }],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["receipt_number"].startswith("EB-")
    assert body["status"] == "ERFASST"
    assert body["net_total"] == "20.00"
    assert body["gross_total"] == "23.80"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["ledger_account_id"] == str(ledger.id)
    # Status-Historie enthält die Anlage (ERFASST).
    assert any(ev["to_status"] == "ERFASST" for ev in body["history"])


def test_detail_liefert_positionen_und_historie(admin_client, app_user, seeded):
    rid = seeded["receipt"].id
    # Einen echten Statuswechsel erzeugen, damit die Historie mehr als die Anlage hat.
    service.advance_status(app_user.id, receipt_id=rid, to_status="GEPRUEFT")
    body = admin_client.get(f"{BASE}/receipts/{rid}").json()
    assert body["supplier_invoice_number"] == "LR-7"
    assert body["lines"][0]["description"] == "Rohre"
    stati = [ev["to_status"] for ev in body["history"]]
    assert "ERFASST" in stati and "GEPRUEFT" in stati


def test_liste_paginierung_und_filter(admin_client, app_user):
    # Zwei Belege anlegen, einen davon prüfen (GEPRUEFT).
    r1 = _receipt(app_user)
    r2 = _receipt(app_user)
    service.advance_status(app_user.id, receipt_id=r2.id, to_status="GEPRUEFT")

    full = admin_client.get(f"{BASE}/receipts").json()
    assert full["total"] >= 2

    # Statusfilter.
    geprueft = admin_client.get(f"{BASE}/receipts?status=GEPRUEFT").json()
    ids = {it["id"] for it in geprueft["items"]}
    assert str(r2.id) in ids and str(r1.id) not in ids

    # Paginierung: page_size 1 liefert genau eine Zeile.
    seite = admin_client.get(f"{BASE}/receipts?page=1&page_size=1").json()
    assert len(seite["items"]) == 1
    assert seite["page_size"] == 1


def test_liste_unbekannter_status_422(admin_client, seeded):
    r = admin_client.get(f"{BASE}/receipts?status=QUATSCH")
    assert r.status_code == 422


def test_detail_unbekannt_404(admin_client, db):
    r = admin_client.get(f"{BASE}/receipts/{uuid.uuid4()}")
    assert r.status_code == 404


def test_create_unbekannter_lieferant_422(admin_client, app_user):
    """Unbekannter Fremdschlüssel (Lieferant) → 422, nicht 500."""
    ledger = _ledger(app_user, number="5600")
    r = admin_client.post(
        f"{BASE}/receipts",
        data={
            "supplier_party_id": str(uuid.uuid4()),
            "receipt_date": "2026-07-01",
            "lines": [{"description": "x", "quantity": "1", "unit_price": "1",
                       "tax_code": "DE_19", "ledger_account_id": str(ledger.id)}],
        },
        content_type="application/json",
    )
    assert r.status_code == 422


def test_create_unbekanntes_buchungskonto_422(admin_client, app_user):
    """Unbekannte Kontierungs-Referenz → 422, nicht 500."""
    supplier = _supplier(app_user)
    r = admin_client.post(
        f"{BASE}/receipts",
        data={
            "supplier_party_id": str(supplier.id),
            "receipt_date": "2026-07-01",
            "lines": [{"description": "x", "quantity": "1", "unit_price": "1",
                       "tax_code": "DE_19", "ledger_account_id": str(uuid.uuid4())}],
        },
        content_type="application/json",
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Ändern
# ---------------------------------------------------------------------------

def test_update_receipt_api(admin_client, seeded):
    rid = seeded["receipt"].id
    r = admin_client.put(
        f"{BASE}/receipts/{rid}",
        data={"notes": "Korrektur", "lines": [
            {"description": "Neu", "quantity": "1", "unit_price": "50",
             "tax_code": "DE_19",
             "ledger_account_id": str(seeded["ledger"].id)},
        ]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["notes"] == "Korrektur"
    assert body["net_total"] == "50.00"
    assert len(body["lines"]) == 1


def test_update_nach_freigabe_422(admin_client, app_user, seeded):
    rid = seeded["receipt"].id
    for to in ("GEPRUEFT", "FREIGEGEBEN"):
        service.advance_status(app_user.id, receipt_id=rid, to_status=to)
    r = admin_client.put(
        f"{BASE}/receipts/{rid}",
        data={"notes": "zu spät"}, content_type="application/json",
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Statuswechsel über die API
# ---------------------------------------------------------------------------

def test_status_flow_api(admin_client, seeded):
    rid = seeded["receipt"].id
    for to in ("GEPRUEFT", "FREIGEGEBEN", "GEBUCHT"):
        r = admin_client.post(
            f"{BASE}/receipts/{rid}/status",
            data={"to_status": to}, content_type="application/json",
        )
        assert r.status_code == 200, (to, r.content)
        assert r.json()["status"] == to


def test_status_ungueltiger_uebergang_422(admin_client, seeded):
    rid = seeded["receipt"].id
    # ERFASST → FREIGEGEBEN direkt ist unzulässig.
    r = admin_client.post(
        f"{BASE}/receipts/{rid}/status",
        data={"to_status": "FREIGEGEBEN"}, content_type="application/json",
    )
    assert r.status_code == 422


def test_freigabe_ohne_kontierung_422(admin_client, app_user):
    """Freigabe-Tor über die API: unkontierte Position → 422."""
    r = _receipt(app_user, kontiert=False)
    service.advance_status(app_user.id, receipt_id=r.id, to_status="GEPRUEFT")
    resp = admin_client.post(
        f"{BASE}/receipts/{r.id}/status",
        data={"to_status": "FREIGEGEBEN"}, content_type="application/json",
    )
    assert resp.status_code == 422


def test_ablehnung_ohne_begruendung_422(admin_client, seeded):
    rid = seeded["receipt"].id
    r = admin_client.post(
        f"{BASE}/receipts/{rid}/status",
        data={"to_status": "ABGELEHNT"}, content_type="application/json",
    )
    assert r.status_code == 422


def test_ablehnung_mit_begruendung_ok(admin_client, seeded):
    rid = seeded["receipt"].id
    r = admin_client.post(
        f"{BASE}/receipts/{rid}/status",
        data={"to_status": "ABGELEHNT", "reason": "Doppelt"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ABGELEHNT"
    assert r.json()["rejection_reason"] == "Doppelt"


# ---------------------------------------------------------------------------
# Rechte-Torung
# ---------------------------------------------------------------------------

def test_liste_ohne_login_401(anonymous_client, seeded):
    r = anonymous_client.get(f"{BASE}/receipts")
    assert r.status_code == 401


def test_liste_ohne_accounting_recht_403(client_with_role, seeded):
    """MONTEUR hat keinerlei accounting-Recht → 403."""
    c = client_with_role("MONTEUR")
    r = c.get(f"{BASE}/receipts")
    assert r.status_code == 403


def test_lesen_erlaubt_schreiben_verboten(client_with_role, app_user, seeded):
    """NUR_LESEN darf LESEN, aber nicht ANLEGEN/AENDERN/FREIGEBEN."""
    c = client_with_role("NUR_LESEN")
    rid = seeded["receipt"].id
    # Lesen: erlaubt.
    assert c.get(f"{BASE}/receipts/{rid}").status_code == 200
    # Anlegen: 403 (schemakonformer Payload, damit die Rechteprüfung greift).
    ledger = _ledger(app_user, number="5700")
    supplier = _supplier(app_user)
    anlegen = c.post(
        f"{BASE}/receipts",
        data={"supplier_party_id": str(supplier.id), "receipt_date": "2026-07-01",
              "lines": [{"description": "x", "quantity": "1", "unit_price": "1",
                         "tax_code": "DE_19", "ledger_account_id": str(ledger.id)}]},
        content_type="application/json",
    )
    assert anlegen.status_code == 403


def test_status_gepruefen_ohne_aendern_recht_403(client_with_role, seeded):
    """NUR_LESEN (nur LESEN) darf keinen Statuswechsel (AENDERN) → 403."""
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"{BASE}/receipts/{seeded['receipt'].id}/status",
        data={"to_status": "GEPRUEFT"}, content_type="application/json",
    )
    assert r.status_code == 403


def test_freigeben_ohne_freigeben_recht_403(client_with_role, seeded):
    """FREIGEGEBEN verlangt das eigene Recht FREIGEBEN; NUR_LESEN hat es nicht → 403."""
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"{BASE}/receipts/{seeded['receipt'].id}/status",
        data={"to_status": "FREIGEGEBEN"}, content_type="application/json",
    )
    assert r.status_code == 403


def test_buchhaltung_darf_kompletten_flow(client_with_role, app_user, seeded):
    """BUCHHALTUNG (nicht Admin) trägt ANLEGEN/AENDERN/FREIGEBEN: voller Durchlauf.

    Belegt, dass das FREIGEBEN-Tor für die BUCHHALTUNG offen ist (im Unterschied
    zu NUR_LESEN, die schon an GEPRUEFT scheitert).
    """
    c = client_with_role("BUCHHALTUNG")
    rid = seeded["receipt"].id
    for to in ("GEPRUEFT", "FREIGEGEBEN", "GEBUCHT"):
        r = c.post(
            f"{BASE}/receipts/{rid}/status",
            data={"to_status": to}, content_type="application/json",
        )
        assert r.status_code == 200, (to, r.content)
    assert c.get(f"{BASE}/receipts/{rid}").json()["status"] == "GEBUCHT"


@pytest.mark.django_db
def test_create_faelligkeit_vor_belegdatum_422(admin_client, app_user):
    """`due_date < receipt_date` ist ein harter DB-CHECK (kein Fach-Tor). Der
    Service fängt ihn vorab ab, damit die API 422 statt 500 liefert."""
    supplier = _supplier(app_user)
    r = admin_client.post(
        f"{BASE}/receipts",
        data={
            "supplier_party_id": str(supplier.id),
            "receipt_date": "2026-07-01",
            "due_date": "2026-06-01",
            "lines": [{"description": "x", "quantity": "1", "unit_price": "1",
                       "tax_code": "DE_19"}],
        },
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Fälligkeitsdatum" in r.json()["detail"]
