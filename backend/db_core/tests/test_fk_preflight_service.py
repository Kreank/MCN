"""Vorab-Existenzprüfung von Payload-Fremdschlüsseln (→ ValueError statt 500).

Jede Service-Funktion, die eine ID aus dem Payload in ein objects.create/update
schreibt, muss einen unbekannten/ungültigen FK als klaren ValueError (→422)
melden, statt ihn erst als DB-IntegrityError (→500) durchschlagen zu lassen
(Projektregel „Fachfehler = 422, nie 500"). Deckt zusätzlich MERGED-Party,
überlappende Doppelrolle und Doppelzuweisung ab.

Die Test-DB baut die volle Migrationskette; app_user aus conftest.
"""
import uuid
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

import pytest

from db_core.services import artikel as artikel_service
from db_core.services import aufgabe as aufgabe_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service
from db_core.services import wartung as wartung_service

UNKNOWN = uuid.uuid4  # kurze, sprechende Erzeugung unbekannter IDs

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)


# --- gemeinsame Bausteine --------------------------------------------------

def _property(app_user, name="Objekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _person(app_user, first="Erika", last="Muster"):
    return identity_service.create_person(app_user.id, first_name=first, last_name=last)


def _account(display_name="Konto"):
    from db_core.models import AppUser
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=display_name, status="ACTIVE", version=1
    )


def _driven_order(app_user, *, target="IN_AUSFUEHRUNG"):
    """Ein vollständig getorter Auftrag, geschaltet bis target."""
    obj = _property(app_user)
    principal = _person(app_user)
    order = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="A")
    auftrag_service.set_order_evidence(app_user.id, work_order_id=order.id, reference="N")
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to_status)
        if to_status == target:
            break
    return order


def _job(app_user):
    order = _driven_order(app_user)
    return einsatz_service.create_service_job(app_user.id, work_order_id=order.id)


def _draft_invoice(app_user):
    obj = _property(app_user)
    return beleg_service.create_invoice(
        app_user.id, property_id=obj.id,
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 1,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )


# --- projekt ---------------------------------------------------------------

@pytest.mark.django_db
def test_create_service_case_unbekannte_liegenschaft(app_user):
    with pytest.raises(ValueError, match="Liegenschaft"):
        projekt_service.create_service_case(
            app_user.id, property_id=UNKNOWN(), subject="X"
        )


@pytest.mark.django_db
def test_create_service_case_unbekannter_melder(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="Melder"):
        projekt_service.create_service_case(
            app_user.id, property_id=obj.id, subject="X",
            reported_by_party_id=UNKNOWN(),
        )


@pytest.mark.django_db
def test_create_service_case_unbekanntes_projekt(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="Projekt"):
        projekt_service.create_service_case(
            app_user.id, property_id=obj.id, subject="X", project_id=UNKNOWN()
        )


@pytest.mark.django_db
def test_create_project_unbekannte_liegenschaft(app_user):
    with pytest.raises(ValueError, match="Liegenschaft"):
        projekt_service.create_project(
            app_user.id, name="P", property_ids=[UNKNOWN()]
        )


@pytest.mark.django_db
def test_create_project_unbekannte_kategorie(app_user):
    with pytest.raises(ValueError, match="Kategorie"):
        projekt_service.create_project(app_user.id, name="P", category_id=UNKNOWN())


@pytest.mark.django_db
def test_create_project_unbekannter_verantwortlicher(app_user):
    with pytest.raises(ValueError, match="Benutzer"):
        projekt_service.create_project(
            app_user.id, name="P", responsible_user_id=UNKNOWN()
        )


@pytest.mark.django_db
def test_add_project_log_unbekanntes_projekt(app_user):
    with pytest.raises(ValueError, match="Projekt"):
        projekt_service.add_project_log(
            app_user.id, project_id=UNKNOWN(), entry="Notiz"
        )


@pytest.mark.django_db
def test_create_checklist_unbekanntes_projekt(app_user):
    with pytest.raises(ValueError, match="Projekt"):
        projekt_service.create_checklist(
            app_user.id, project_id=UNKNOWN(), name="Liste"
        )


# --- aufgabe ---------------------------------------------------------------

@pytest.mark.django_db
def test_create_task_unbekannter_benutzer(app_user):
    with pytest.raises(ValueError, match="Benutzer"):
        aufgabe_service.create_task(
            app_user.id, title="X", assigned_to_user_id=UNKNOWN()
        )


@pytest.mark.django_db
def test_create_task_unbekanntes_projekt(app_user):
    with pytest.raises(ValueError, match="Projekt"):
        aufgabe_service.create_task(app_user.id, title="X", project_id=UNKNOWN())


@pytest.mark.django_db
def test_create_task_unbekannter_kontakt(app_user):
    with pytest.raises(ValueError, match="Kontakt"):
        aufgabe_service.create_task(app_user.id, title="X", party_id=UNKNOWN())


# --- property --------------------------------------------------------------

@pytest.mark.django_db
def test_add_building_unbekannte_liegenschaft(app_user):
    with pytest.raises(ValueError, match="Liegenschaft"):
        property_service.add_building(
            app_user.id, property_id=UNKNOWN(), building_number="A"
        )


@pytest.mark.django_db
def test_add_building_unbekannte_adresse(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="Adresse"):
        property_service.add_building(
            app_user.id, property_id=obj.id, building_number="A", address_id=UNKNOWN()
        )


@pytest.mark.django_db
def test_add_unit_unbekanntes_gebaeude(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="Gebäude"):
        property_service.add_unit(
            app_user.id, building_id=UNKNOWN(), property_id=obj.id,
            unit_type="APARTMENT", unit_number="1",
        )


@pytest.mark.django_db
def test_add_unit_gebaeude_fremde_liegenschaft(app_user):
    obj_a = _property(app_user, "A")
    obj_b = _property(app_user, "B")
    building = property_service.add_building(
        app_user.id, property_id=obj_a.id, building_number="A"
    )
    with pytest.raises(ValueError, match="gehört nicht"):
        property_service.add_unit(
            app_user.id, building_id=building.id, property_id=obj_b.id,
            unit_type="APARTMENT", unit_number="1",
        )


@pytest.mark.django_db
def test_add_party_role_unbekannte_partei(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="existiert nicht"):
        property_service.add_party_role(
            app_user.id, property_id=obj.id, party_id=UNKNOWN(),
            role="OPERATOR", valid_from=date(2020, 1, 1),
        )


@pytest.mark.django_db
def test_add_party_role_ueberlappende_doppelrolle(app_user):
    obj = _property(app_user)
    person = _person(app_user)
    property_service.add_party_role(
        app_user.id, property_id=obj.id, party_id=person.id,
        role="OPERATOR", valid_from=date(2020, 1, 1),
    )
    with pytest.raises(ValueError, match="bereits dieselbe Rolle"):
        property_service.add_party_role(
            app_user.id, property_id=obj.id, party_id=person.id,
            role="OPERATOR", valid_from=date(2021, 1, 1),
        )


@pytest.mark.django_db
def test_add_party_role_anschliessend_erlaubt(app_user):
    """Direkt anschließende (nicht überlappende) Zeiträume bleiben erlaubt."""
    obj = _property(app_user)
    person = _person(app_user)
    property_service.add_party_role(
        app_user.id, property_id=obj.id, party_id=person.id,
        role="OPERATOR", valid_from=date(2020, 1, 1), valid_until=date(2021, 1, 1),
    )
    # daterange '[)': [2021-01-01, …) grenzt an, überlappt aber nicht.
    property_service.add_party_role(
        app_user.id, property_id=obj.id, party_id=person.id,
        role="OPERATOR", valid_from=date(2021, 1, 1),
    )


# --- einsatz ---------------------------------------------------------------

@pytest.mark.django_db
def test_create_service_job_unbekannter_auftrag(app_user):
    with pytest.raises(ValueError, match="Auftrag"):
        einsatz_service.create_service_job(app_user.id, work_order_id=UNKNOWN())


@pytest.mark.django_db
def test_assign_user_unbekannter_einsatz(app_user):
    with pytest.raises(ValueError, match="Einsatz"):
        einsatz_service.assign_user(
            app_user.id, service_job_id=UNKNOWN(), assignee_user_id=app_user.id
        )


@pytest.mark.django_db
def test_assign_user_unbekannter_mitarbeiter(app_user):
    job = _job(app_user)
    with pytest.raises(ValueError, match="Mitarbeiter"):
        einsatz_service.assign_user(
            app_user.id, service_job_id=job.id, assignee_user_id=UNKNOWN()
        )


@pytest.mark.django_db
def test_assign_user_doppelzuweisung(app_user):
    job = _job(app_user)
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=app_user.id
    )
    with pytest.raises(ValueError, match="bereits zugewiesen"):
        einsatz_service.assign_user(
            app_user.id, service_job_id=job.id, assignee_user_id=app_user.id
        )


@pytest.mark.django_db
def test_log_time_unbekannter_einsatz(app_user):
    with pytest.raises(ValueError, match="Einsatz"):
        einsatz_service.log_time(
            app_user.id, service_job_id=UNKNOWN(), user_id=app_user.id,
            time_type="ARBEITSZEIT", started_at=T0, ended_at=T1,
        )


@pytest.mark.django_db
def test_log_material_unbekannter_erfasser(app_user):
    job = _job(app_user)
    with pytest.raises(ValueError, match="Erfasser"):
        einsatz_service.log_material(
            app_user.id, service_job_id=job.id, description="Harz",
            quantity=Decimal("1.0"), unit="kg", recorded_by=UNKNOWN(),
        )


# --- buchhaltung -----------------------------------------------------------

@pytest.mark.django_db
def test_record_payment_unbekannte_rechnung(app_user):
    with pytest.raises(ValueError, match="Rechnung"):
        buchhaltung_service.record_payment(
            app_user.id, invoice_id=UNKNOWN(), amount=Decimal("10.00"),
            paid_at=date(2026, 1, 1),
        )


@pytest.mark.django_db
def test_issue_dunning_unbekannte_rechnung(app_user):
    with pytest.raises(ValueError, match="Rechnung"):
        buchhaltung_service.issue_dunning_notice(
            app_user.id, invoice_id=UNKNOWN(), level=1, issued_at=date(2026, 1, 1)
        )


# --- artikel ---------------------------------------------------------------

@pytest.mark.django_db
def test_create_assembly_unbekannter_artikel(app_user):
    with pytest.raises(ValueError, match="Artikel"):
        artikel_service.create_assembly(
            app_user.id, assembly_number="L-1", name="Leistung", unit="m²",
            components=[{"article_id": UNKNOWN(), "quantity": "1.000"}],
        )


@pytest.mark.django_db
def test_create_assembly_unbekannte_lohngruppe(app_user):
    with pytest.raises(ValueError, match="Lohngruppe"):
        artikel_service.create_assembly(
            app_user.id, assembly_number="L-2", name="Leistung", unit="m²",
            components=[{"wage_group_id": UNKNOWN(), "minutes": "30.00"}],
        )


@pytest.mark.django_db
def test_set_article_sale_price_unbekannter_artikel(app_user):
    with pytest.raises(ValueError, match="Artikel"):
        artikel_service.set_article_sale_price(
            app_user.id, article_id=UNKNOWN(), fixed_price="10.00"
        )


@pytest.mark.django_db
def test_set_article_sale_price_unbekannte_gruppe(app_user):
    article = artikel_service.create_article(
        app_user.id, article_number="A-9", description="x", unit="Stk"
    )
    with pytest.raises(ValueError, match="Kalkulationsgruppe"):
        artikel_service.set_article_sale_price(
            app_user.id, article_id=article.id, sale_price_group_id=UNKNOWN()
        )


# --- auftrag ---------------------------------------------------------------

@pytest.mark.django_db
def test_create_work_order_unbekannte_liegenschaft(app_user):
    with pytest.raises(ValueError, match="Liegenschaft"):
        auftrag_service.create_work_order(
            app_user.id, property_id=UNKNOWN(), title="A"
        )


@pytest.mark.django_db
def test_add_work_order_party_unbekannter_auftrag(app_user):
    person = _person(app_user)
    with pytest.raises(ValueError, match="Auftrag"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=UNKNOWN(), party_id=person.id, role="PRINCIPAL"
        )


@pytest.mark.django_db
def test_add_work_order_party_unbekannte_partei(app_user):
    obj = _property(app_user)
    order = auftrag_service.create_work_order(app_user.id, property_id=obj.id, title="A")
    with pytest.raises(ValueError, match="existiert nicht"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=UNKNOWN(), role="PRINCIPAL"
        )


# --- beleg -----------------------------------------------------------------

@pytest.mark.django_db
def test_create_quote_unbekannte_liegenschaft(app_user):
    with pytest.raises(ValueError, match="Liegenschaft"):
        beleg_service.create_quote(app_user.id, property_id=UNKNOWN(), title="AN")


@pytest.mark.django_db
def test_create_invoice_unbekannte_liegenschaft(app_user):
    with pytest.raises(ValueError, match="Liegenschaft"):
        beleg_service.create_invoice(app_user.id, property_id=UNKNOWN())


@pytest.mark.django_db
def test_create_invoice_unbekannter_auftrag(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="Auftrag"):
        beleg_service.create_invoice(
            app_user.id, property_id=obj.id, work_order_id=UNKNOWN(),
            lines=[{"line_type": "MATERIAL", "description": "Z", "quantity": 1,
                    "unit": "Stk", "unit_price": "1.00", "tax_code": "DE_19"}],
        )


@pytest.mark.django_db
def test_add_invoice_party_unbekannte_rechnung(app_user):
    person = _person(app_user)
    with pytest.raises(ValueError, match="Rechnung"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=UNKNOWN(), party_id=person.id, role="INVOICE_DEBTOR"
        )


@pytest.mark.django_db
def test_add_invoice_party_unbekannte_partei(app_user):
    inv = _draft_invoice(app_user)
    with pytest.raises(ValueError, match="existiert nicht"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=UNKNOWN(), role="INVOICE_DEBTOR"
        )


# --- wartung ---------------------------------------------------------------

@pytest.mark.django_db
def test_wartung_create_contract_unbekannte_liegenschaft(app_user):
    with pytest.raises(ValueError, match="Liegenschaft"):
        wartung_service.create_contract(
            app_user.id, property_id=UNKNOWN(), name="Wartung",
            start_date=date(2026, 1, 1), interval_kind="JAEHRLICH", due_action="AUFGABE",
        )


@pytest.mark.django_db
def test_wartung_create_contract_unbekannter_kunde(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="Kunde"):
        wartung_service.create_contract(
            app_user.id, property_id=obj.id, name="Wartung",
            start_date=date(2026, 1, 1), interval_kind="JAEHRLICH", due_action="AUFGABE",
            party_id=UNKNOWN(),
        )


# --- mitarbeiter -----------------------------------------------------------

@pytest.mark.django_db
def test_create_employee_unbekanntes_konto(app_user):
    person = _person(app_user)
    with pytest.raises(ValueError, match="Benutzerkonto"):
        mitarbeiter_service.create_employee(
            app_user.id, app_user_id=UNKNOWN(), party_id=person.id,
            hired_on=date(2024, 1, 1),
        )


@pytest.mark.django_db
def test_create_employee_unbekannte_person(app_user):
    with pytest.raises(ValueError, match="Person"):
        mitarbeiter_service.create_employee(
            app_user.id, app_user_id=_account().id, party_id=UNKNOWN(),
            hired_on=date(2024, 1, 1),
        )


@pytest.mark.django_db
def test_create_employee_unbekannte_lohngruppe(app_user):
    person = _person(app_user)
    with pytest.raises(ValueError, match="Lohngruppe"):
        mitarbeiter_service.create_employee(
            app_user.id, app_user_id=_account().id, party_id=person.id,
            hired_on=date(2024, 1, 1), wage_group_id=UNKNOWN(),
        )


@pytest.mark.django_db
def test_create_contract_unbekannte_lohngruppe(app_user):
    person = _person(app_user)
    employee = mitarbeiter_service.create_employee(
        app_user.id, app_user_id=_account().id, party_id=person.id,
        hired_on=date(2024, 1, 1),
    )
    with pytest.raises(ValueError, match="Lohngruppe"):
        mitarbeiter_service.create_contract(
            app_user.id, employee_id=employee.id, valid_from=date(2024, 1, 1),
            hours={"hours_monday": 8}, vacation_days_per_year=30,
            wage_group_id=UNKNOWN(),
        )
