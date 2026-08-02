"""Rechnungsentwurf verwerfen (Migration 0147).

Sascha am 2026-08-02: *„Ja wir nehmen das zweite. Aber es soll auch nur mit
Entwürfen gehen. Erstellte Rechnungen können nur wie gehabt über Storno
berichtigt werden."*

Der Kern steht unten: Beim Verwerfen werden die Abrechnungsbindungen **gelöst**.
Blieben sie aktiv, wären Stunden und Material für immer als abgerechnet markiert
— nie wieder fakturierbar, und niemand fände den Grund.
"""
import pytest
from django.db import Error

from db_core.db_context import business_transaction
from db_core.models import Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import property as property_service

LINES = [{"line_type": "MATERIAL", "description": "Fliesen", "quantity": 5,
          "unit": "m2", "unit_price": "30.00", "tax_code": "DE_19"}]


def _entwurf(app_user):
    obj = property_service.create_property(
        app_user.id, name="Baustelle Verwerfen", property_type="WEG",
        street="Prüfweg", house_number="5", postal_code="10115", city="Berlin",
    )
    auftrag = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag"
    )
    return beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=auftrag.id, lines=LINES,
    )


@pytest.mark.django_db
def test_entwurf_laesst_sich_verwerfen(app_user):
    invoice = _entwurf(app_user)

    invoice = beleg_service.verwirf_rechnung(app_user.id, invoice_id=invoice.id)

    assert invoice.status == "VERWORFEN"


@pytest.mark.django_db
def test_zweimal_verwerfen_wird_abgelehnt(app_user):
    invoice = _entwurf(app_user)
    beleg_service.verwirf_rechnung(app_user.id, invoice_id=invoice.id)

    with pytest.raises(ValueError, match="bereits verworfen"):
        beleg_service.verwirf_rechnung(app_user.id, invoice_id=invoice.id)


@pytest.mark.django_db
def test_der_beleg_bleibt_vollstaendig_lesbar(app_user):
    """Verwerfen ist kein Löschen — die Positionen bleiben stehen."""
    invoice = _entwurf(app_user)
    beleg_service.verwirf_rechnung(app_user.id, invoice_id=invoice.id)

    invoice.refresh_from_db()
    assert invoice.lines.count() == 1


@pytest.mark.django_db
def test_die_datenbank_verbietet_das_wiederbeleben(app_user):
    """Ein wiederbelebter Entwurf könnte Quellen binden, die inzwischen
    anderswo abgerechnet sind — der Trigger aus 0147 hält das dicht."""
    invoice = _entwurf(app_user)
    beleg_service.verwirf_rechnung(app_user.id, invoice_id=invoice.id)

    with pytest.raises(Error, match="nicht wiederbelebt"):
        with business_transaction(app_user.id):
            Invoice.objects.filter(id=invoice.id).update(status="ENTWURF")


# Hinweis: „Veröffentlichter Beleg lässt sich nicht verwerfen" ist hier NICHT
# als Test abgebildet — eine Rechnung lässt sich nicht künstlich auf
# VEROEFFENTLICHT setzen, das verlangt Snapshot und Inhalts-Hash (B-21/B-30,
# Trigger `prepare_invoice_publish`). Die Grenze deckt der Trigger aus 0147 ab,
# der jeden Weg nach VERWORFEN aus einem anderen Zustand als ENTWURF abweist;
# geprüft wird er oben über das Wiederbeleben.
