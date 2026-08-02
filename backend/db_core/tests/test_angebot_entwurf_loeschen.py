"""Ein Angebot darf weg, solange es keine Nummer trägt (Migration 0146).

Sascha am 2026-08-02: *„Entwürfe alle löschbar. Sobald versendet oder bestätigt
fest und nicht mehr änderbar."*

Die Grenze ist bewusst die **Belegnummer**, nicht der Status: Sie entsteht erst
beim Versand (CHECK „P3-01" in 0018), und solange sie fehlt, war das Angebot nie
beim Kunden.
"""
import pytest
from django.db import Error

from db_core.db_context import business_transaction
from db_core.models import Quote
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import property as property_service

LINES = [{"line_type": "MATERIAL", "description": "Fliesen", "quantity": 20,
          "unit": "m2", "unit_price": "34.50", "tax_code": "DE_19"}]


def _objekt(actor):
    return property_service.create_property(
        actor.id, name="Baustelle Löschtest", property_type="WEG",
        street="Prüfweg", house_number="3", postal_code="10115", city="Berlin",
    )


@pytest.mark.django_db
def test_angebotsentwurf_laesst_sich_loeschen(app_user):
    quote = beleg_service.create_quote(
        app_user.id, property_id=_objekt(app_user).id, title="Bad sanieren",
        lines=LINES,
    )

    beleg_service.delete_quote(app_user.id, quote_id=quote.id)

    assert not Quote.objects.filter(id=quote.id).exists()


def _versendetes_angebot(app_user):
    obj = _objekt(app_user)
    auftrag = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag zum Angebot"
    )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Bad sanieren",
        work_order_id=auftrag.id, lines=LINES,
    )
    beleg_service.send_quote(app_user.id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote


@pytest.mark.django_db
def test_versendetes_angebot_bleibt(app_user):
    """Ab der Belegnummer ist es ein Dokument — abgelehnt oder ersetzt, nicht gelöscht."""
    quote = _versendetes_angebot(app_user)
    assert quote.quote_number

    with pytest.raises(ValueError, match="versendet"):
        beleg_service.delete_quote(app_user.id, quote_id=quote.id)


@pytest.mark.django_db
def test_die_datenbank_haelt_das_versendete_angebot_fest(app_user):
    """Nicht der Dienst entscheidet das, sondern der Trigger aus 0146."""
    quote = _versendetes_angebot(app_user)

    with pytest.raises(Error, match="ausgestellt"):
        with business_transaction(app_user.id):
            Quote.objects.filter(id=quote.id).delete()
