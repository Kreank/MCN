"""API-Tests der Auswertungen-Endpoints (lesend, Recht über die Rechtematrix)."""
import uuid
from datetime import date, datetime, timezone as dt_timezone

import pytest

from db_core.models import AppUser
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service


def _publish_invoice(
    app_user, obj, party, *, unit_price, quantity, project_id=None, description="X"
):
    order = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="A")
    auftrag_service.set_order_evidence(app_user.id, work_order_id=order.id, reference="N")
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=party.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, work_order_id=order.id, project_id=project_id,
        lines=[{"line_type": "MATERIAL", "description": description, "quantity": quantity,
                "unit_price": unit_price, "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=party.id, role=role, is_primary=True
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    return inv


@pytest.mark.django_db
def test_dashboards_liste(admin_client, db):
    r = admin_client.get("/api/auswertungen/dashboards")
    assert r.status_code == 200
    body = r.json()
    umsatz = next(d for d in body if d["key"] == "umsatz-projektuebersicht")
    assert umsatz["available"] is True


@pytest.mark.django_db
def test_umsatz_projektuebersicht(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    projekt_service.create_project(app_user.id, name="P1")
    weg = identity_service.create_person(app_user.id, first_name="W", last_name="EG")
    _publish_invoice(app_user, obj, weg, unit_price="100.00", quantity=2)

    r = admin_client.get("/api/auswertungen/umsatz-projektuebersicht")
    assert r.status_code == 200
    body = r.json()
    assert body["revenue"]["net_total"] == "200.00"
    assert body["revenue"]["invoice_count"] == 1
    assert body["projects"]["total"] == 1
    assert body["projects"]["open"] == 1
    assert len(body["timeline"]) == 1


@pytest.mark.django_db
def test_umsatz_projektuebersicht_leer(admin_client, db):
    r = admin_client.get("/api/auswertungen/umsatz-projektuebersicht")
    assert r.status_code == 200
    body = r.json()
    assert body["revenue"]["net_total"] == "0.00"
    assert body["revenue"]["invoice_count"] == 0
    assert body["timeline"] == []


@pytest.mark.django_db
def test_kunden_dashboard_verfuegbar(admin_client, db):
    body = admin_client.get("/api/auswertungen/dashboards").json()
    kunden = next(d for d in body if d["key"] == "kunden")
    assert kunden["available"] is True


@pytest.mark.django_db
def test_kunden_endpoint(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    anna = identity_service.create_person(app_user.id, first_name="Anna", last_name="A")
    _publish_invoice(app_user, obj, anna, unit_price="100.00", quantity=2)  # net 200

    r = admin_client.get("/api/auswertungen/kunden")
    assert r.status_code == 200
    body = r.json()
    assert body["customer_count"] == 1
    assert body["net_total"] == "200.00"
    assert body["customers"][0]["display_name"] == "Anna A"
    assert body["customers"][0]["net_total"] == "200.00"


# --- Projekte-Dashboard -----------------------------------------------------

@pytest.mark.django_db
def test_projekte_dashboard(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    proj = projekt_service.create_project(app_user.id, name="Sanierung Dach")
    kunde = identity_service.create_person(app_user.id, first_name="K", last_name="K")
    _publish_invoice(
        app_user, obj, kunde, unit_price="100.00", quantity=3, project_id=proj.id
    )  # net 300 auf das Projekt

    r = admin_client.get("/api/auswertungen/projekte")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["open"] == 1
    assert body["closed"] == 0
    offen = next(s for s in body["by_status"] if s["status"] == "OPEN")
    assert offen["net_total"] == "300.00"
    assert body["top_projects"][0]["name"] == "Sanierung Dach"
    assert body["top_projects"][0]["net_total"] == "300.00"


@pytest.mark.django_db
def test_projekte_dashboard_403_ohne_invoicing(client_with_role):
    """DISPOSITION hat kein invoicing-Recht → 403 (Umsatz je Projekt ist tabu)."""
    c = client_with_role("DISPOSITION")
    assert c.get("/api/auswertungen/projekte").status_code == 403


# --- Artikel-Dashboard ------------------------------------------------------

@pytest.mark.django_db
def test_artikel_dashboard(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    kunde = identity_service.create_person(app_user.id, first_name="K", last_name="K")
    _publish_invoice(
        app_user, obj, kunde, unit_price="50.00", quantity=2, description="Dichtung DN20"
    )  # net 100

    r = admin_client.get("/api/auswertungen/artikel")
    assert r.status_code == 200
    body = r.json()
    assert body["net_total"] == "100.00"
    assert body["line_count"] == 1
    top = body["articles"][0]
    assert top["description"] == "Dichtung DN20"
    assert top["net_total"] == "100.00"
    assert top["quantity_total"] == "2.000"
    material = next(t for t in body["by_type"] if t["line_type"] == "MATERIAL")
    assert material["net_total"] == "100.00"


@pytest.mark.django_db
def test_artikel_dashboard_403_ohne_invoicing(client_with_role):
    c = client_with_role("DISPOSITION")
    assert c.get("/api/auswertungen/artikel").status_code == 403


# --- Marge / Deckungsbeitrag (Recht pricing/LESEN) --------------------------

@pytest.mark.django_db
def test_umsatz_marge_sichtbar_mit_pricing(admin_client, app_user):
    """ADMINISTRATION hat pricing/LESEN -> Marge-Block wird ausgeliefert."""
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="W", last_name="EG")
    order = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="A")
    auftrag_service.set_order_evidence(app_user.id, work_order_id=order.id, reference="N")
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=weg.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, work_order_id=order.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": 2,
                "unit_price": "100.00", "unit_cost": "60.00", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id, role=role, is_primary=True
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)

    body = admin_client.get("/api/auswertungen/umsatz-projektuebersicht").json()
    assert body["marge_sichtbar"] is True
    assert body["marge"]["deckungsbeitrag"] == "80.00"
    assert body["marge"]["marge_prozent"] == "40.00"
    assert body["marge"]["ek_vollstaendig"] is True


# --- Mitarbeitenden-Dashboard (hr) ------------------------------------------

VOLLZEIT = {
    "hours_monday": 8, "hours_tuesday": 8, "hours_wednesday": 8,
    "hours_thursday": 8, "hours_friday": 8,
}
_T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)   # Montag
_T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)  # +4 h


def _job_in_progress(app_user, obj):
    """Auftrag → Einsatz bis VOR_ORT (nicht abgeschlossen: keine B-28-Begründung)."""
    principal = identity_service.create_person(
        app_user.id, first_name="E", last_name="Auftraggeber"
    )
    order = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="A")
    auftrag_service.set_order_evidence(app_user.id, work_order_id=order.id, reference="N")
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=_T0, scheduled_end=_T1
    )
    for to in ("GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"):
        einsatz_service.advance_status(app_user.id, service_job_id=job.id, to_status=to)
    return job


@pytest.mark.django_db
def test_mitarbeitende_dashboard(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    person = identity_service.create_person(app_user.id, first_name="Max", last_name="Muster")
    account = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Max Konto", status="ACTIVE", version=1
    )
    emp = mitarbeiter_service.create_employee(
        app_user.id, app_user_id=account.id, party_id=person.id, hired_on=date(2026, 1, 1)
    )
    mitarbeiter_service.create_contract(
        app_user.id, employee_id=emp.id, valid_from=date(2026, 1, 1),
        hours=VOLLZEIT, vacation_days_per_year=30,
    )
    mitarbeiter_service.set_vacation_budget(
        app_user.id, employee_id=emp.id, year=2026, entitlement_days=30
    )
    # Genehmigter Urlaub Mo+Di (2 Arbeitstage).
    ab = mitarbeiter_service.create_absence(
        app_user.id, employee_id=emp.id, absence_type="URLAUB",
        start_date=date(2026, 7, 13), end_date=date(2026, 7, 14),
    )
    mitarbeiter_service.submit_absence(app_user.id, absence_id=ab.id)
    mitarbeiter_service.approve_absence(app_user.id, absence_id=ab.id)
    # 4 h Ist-Arbeitszeit auf einem laufenden Einsatz.
    job = _job_in_progress(app_user, obj)
    einsatz_service.log_time(
        app_user.id, service_job_id=job.id, user_id=account.id,
        time_type="ARBEITSZEIT", started_at=_T0, ended_at=_T1,
    )

    r = admin_client.get("/api/auswertungen/mitarbeitende?year=2026")
    assert r.status_code == 200
    body = r.json()
    assert body["year"] == 2026
    assert body["employee_count"] == 1
    zeile = body["people"][0]
    assert zeile["display_name"] == "Max Muster"
    assert zeile["worked_hours"] == "4.0"
    assert zeile["vacation_entitlement"] == "30.00"
    assert zeile["vacation_used"] == "2.00"
    assert zeile["vacation_remaining"] == "28.00"
    urlaub = next(a for a in body["absence_by_type"] if a["absence_type"] == "URLAUB")
    assert urlaub["days"] == "2.00"


@pytest.mark.django_db
def test_mitarbeitende_dashboard_403_ohne_hr(client_with_role):
    """DISPOSITION hat kein hr-Recht → 403 (Personaldaten, DSGVO Art. 9)."""
    c = client_with_role("DISPOSITION")
    assert c.get("/api/auswertungen/mitarbeitende").status_code == 403


@pytest.mark.django_db
def test_mitarbeitende_karte_nur_mit_hr(admin_client, client_with_role):
    """Die Landing zeigt die Mitarbeitenden-Kachel nur mit hr-Recht."""
    admin_keys = [d["key"] for d in admin_client.get("/api/auswertungen/dashboards").json()]
    assert "mitarbeitende" in admin_keys
    # NUR_LESEN darf invoicing lesen (Landing sichtbar), aber nicht hr.
    leser = client_with_role("NUR_LESEN")
    leser_keys = [d["key"] for d in leser.get("/api/auswertungen/dashboards").json()]
    assert "mitarbeitende" not in leser_keys


# ===========================================================================
# CSV-Export der Dashboards
# ===========================================================================

from django.test import Client  # noqa: E402

from api.tests.conftest import make_role_user  # noqa: E402
from db_core.db_context import business_transaction  # noqa: E402
from db_core.models import RolePermission  # noqa: E402


def _csv_text(response):
    """Dekodiert den CSV-Body (utf-8-sig entfernt das BOM) zu Text."""
    return response.content.decode("utf-8-sig")


def _csv_zeilen(response):
    return _csv_text(response).splitlines()


def _buchhaltung_ohne_pricing():
    """Ein eingeloggter Client mit invoicing/LESEN, aber OHNE pricing/LESEN.

    Die Startmatrix kennt keine Rolle mit invoicing, aber ohne pricing (jede
    Umsatz-lesende Rolle liest auch Einkaufspreise); für den Marge-Need-to-know
    wird BUCHHALTUNG das pricing/LESEN gezielt entzogen."""
    user, app_user = make_role_user("BUCHHALTUNG")
    with business_transaction(app_user.id):
        RolePermission.objects.filter(
            role_id="BUCHHALTUNG", module="pricing", action="LESEN"
        ).update(allowed=False)
    client = Client()
    client.force_login(user)
    return client


def _publish_invoice_mit_ek(app_user, obj, party, *, unit_price, unit_cost, quantity,
                            description="Rohr", project_id=None):
    """Wie _publish_invoice, aber mit unit_cost (EK) für die Marge."""
    order = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="A")
    auftrag_service.set_order_evidence(app_user.id, work_order_id=order.id, reference="N")
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=party.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, work_order_id=order.id, project_id=project_id,
        lines=[{"line_type": "MATERIAL", "description": description, "quantity": quantity,
                "unit_price": unit_price, "unit_cost": unit_cost, "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=party.id, role=role, is_primary=True
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    return inv


@pytest.mark.django_db
def test_umsatz_export_grundformat(admin_client, app_user):
    """CSV mit BOM, Semikolon-Trenner, erwarteten Kopfzeilen und der Umsatzzeile."""
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="W", last_name="EG")
    _publish_invoice(app_user, obj, weg, unit_price="100.00", quantity=2)  # net 200

    r = admin_client.get("/api/auswertungen/umsatz-projektuebersicht/export.csv")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/csv")
    assert r["Content-Disposition"].startswith("attachment;")
    assert ".csv" in r["Content-Disposition"]
    # BOM voran (Excel de erkennt sonst keine Umlaute), Semikolon als Trenner.
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert b";" in r.content
    zeilen = _csv_zeilen(r)
    assert "Kennzahl;Wert" in zeilen
    # Deutsches Zahlenformat: Komma-Dezimal, kein Punkt.
    assert "Umsatz netto;200,00" in zeilen


@pytest.mark.django_db
def test_umsatz_export_zeitraumfilter_wirkt(admin_client, app_user):
    """date_to vor dem Belegdatum → der Umsatz fällt aus dem Export (0,00)."""
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="W", last_name="EG")
    _publish_invoice(app_user, obj, weg, unit_price="100.00", quantity=2)

    r = admin_client.get(
        "/api/auswertungen/umsatz-projektuebersicht/export.csv?date_to=2000-01-01"
    )
    assert r.status_code == 200
    zeilen = _csv_zeilen(r)
    assert "Umsatz netto;0,00" in zeilen
    # Der Zeitraum steht im Dateinamen (sprechend).
    assert "bis_2000-01-01" in r["Content-Disposition"]


@pytest.mark.django_db
def test_umsatz_export_marge_mit_pricing(admin_client, app_user):
    """Mit pricing/LESEN enthält der Export die Marge-Spalten/-Block."""
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="W", last_name="EG")
    _publish_invoice_mit_ek(
        app_user, obj, weg, unit_price="100.00", unit_cost="60.00", quantity=2
    )
    r = admin_client.get("/api/auswertungen/umsatz-projektuebersicht/export.csv")
    text = _csv_text(r)
    assert "Deckungsbeitrag/Marge nach Gewerk" in text
    assert "Marge %" in text
    assert "EK vollständig" in text


@pytest.mark.django_db
def test_umsatz_export_marge_fehlt_ohne_pricing(app_user):
    """Ohne pricing/LESEN fehlen die Marge-Spalten — kein 403 (Need-to-know)."""
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="W", last_name="EG")
    _publish_invoice_mit_ek(
        app_user, obj, weg, unit_price="100.00", unit_cost="60.00", quantity=2
    )
    client = _buchhaltung_ohne_pricing()
    r = client.get("/api/auswertungen/umsatz-projektuebersicht/export.csv")
    assert r.status_code == 200
    text = _csv_text(r)
    # Der Umsatz bleibt sichtbar, die Marge-Spalten sind weg.
    assert "Umsatz netto;200,00" in _csv_zeilen(r)
    assert "Deckungsbeitrag" not in text
    assert "Marge %" not in text


@pytest.mark.django_db
def test_projekte_export_marge_spalten_mit_pricing(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    proj = projekt_service.create_project(app_user.id, name="Sanierung Dach")
    kunde = identity_service.create_person(app_user.id, first_name="K", last_name="K")
    _publish_invoice_mit_ek(
        app_user, obj, kunde, unit_price="100.00", unit_cost="60.00", quantity=3,
        project_id=proj.id,
    )
    r = admin_client.get("/api/auswertungen/projekte/export.csv")
    assert r.status_code == 200
    text = _csv_text(r)
    assert "Top-Projekte nach Umsatz" in text
    assert "Sanierung Dach" in text
    assert "Deckungsbeitrag" in text  # Marge-Spalten vorhanden


@pytest.mark.django_db
def test_artikel_export_sonderzeichen_gequotet(admin_client, app_user):
    """Ein Positionstext mit ; und \" wird korrekt gequotet (csv-Modul)."""
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    kunde = identity_service.create_person(app_user.id, first_name="K", last_name="K")
    _publish_invoice(
        app_user, obj, kunde, unit_price="50.00", quantity=2,
        description='Rohr; "DN20"',
    )
    r = admin_client.get("/api/auswertungen/artikel/export.csv")
    assert r.status_code == 200
    text = _csv_text(r)
    # Feld mit Trenner+Anführungszeichen: umschlossen, innere " verdoppelt.
    assert '"Rohr; ""DN20"""' in text


@pytest.mark.django_db
def test_kunden_export(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    anna = identity_service.create_person(app_user.id, first_name="Anna", last_name="A")
    _publish_invoice(app_user, obj, anna, unit_price="100.00", quantity=2)  # net 200
    r = admin_client.get("/api/auswertungen/kunden/export.csv")
    assert r.status_code == 200
    text = _csv_text(r)
    assert "Anna A" in text
    # Anteil bei einzigem Kunden = 100 % (Komma-Dezimal).
    assert "100,00" in text


@pytest.mark.django_db
def test_mitarbeitende_export_hr(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    person = identity_service.create_person(app_user.id, first_name="Max", last_name="Muster")
    account = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Max Konto", status="ACTIVE", version=1
    )
    emp = mitarbeiter_service.create_employee(
        app_user.id, app_user_id=account.id, party_id=person.id, hired_on=date(2026, 1, 1)
    )
    mitarbeiter_service.create_contract(
        app_user.id, employee_id=emp.id, valid_from=date(2026, 1, 1),
        hours=VOLLZEIT, vacation_days_per_year=30,
    )
    mitarbeiter_service.set_vacation_budget(
        app_user.id, employee_id=emp.id, year=2026, entitlement_days=30
    )
    r = admin_client.get("/api/auswertungen/mitarbeitende/export.csv?year=2026")
    assert r.status_code == 200
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "Mitarbeitende_2026.csv" in r["Content-Disposition"]
    text = _csv_text(r)
    assert "Max Muster" in text
    assert "Auslastung & Urlaub je Mitarbeiter" in text


@pytest.mark.django_db
def test_export_rechte_gate(client_with_role):
    """Ohne das jeweilige Dashboard-Recht → 403 auf dem Export."""
    dispo = client_with_role("DISPOSITION")  # kein invoicing, kein hr
    for pfad in ("umsatz-projektuebersicht", "kunden", "projekte", "artikel"):
        assert dispo.get(f"/api/auswertungen/{pfad}/export.csv").status_code == 403
    assert dispo.get("/api/auswertungen/mitarbeitende/export.csv").status_code == 403
    # NUR_LESEN darf invoicing lesen, aber nicht hr → nur der hr-Export ist tabu.
    leser = client_with_role("NUR_LESEN")
    assert leser.get("/api/auswertungen/umsatz-projektuebersicht/export.csv").status_code == 200
    assert leser.get("/api/auswertungen/mitarbeitende/export.csv").status_code == 403
