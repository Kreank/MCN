"""Gebuchte Arbeitszeit auf dem Bericht — abgeleitet, nicht gespeichert.

Sascha am 2026-08-02: *„Gebuchte Zeiten auf diesen Termin sollen dann als
Position unten mit angegeben werden, und zwar als Leistung."*

Der entscheidende Zuschnitt steht im letzten Test: Die Zeiten werden **nicht**
als Berichtsposition abgelegt. Die Abrechnung liest dieselben Buchungen bereits
direkt — eine zweite Ablage stünde zweimal in der Rechnung.
"""
import pytest

from db_core.services import site_report as report_service


@pytest.mark.django_db
def test_ohne_termin_keine_zeiten(app_user):
    """Ein Bericht am Auftrag (ohne Termin) kennt keine Buchungen."""
    from db_core.services import auftrag as auftrag_service
    from db_core.services import property as property_service

    obj = property_service.create_property(
        app_user.id, name="Zeitobjekt", property_type="WEG",
        street="Uhrweg", house_number="1", postal_code="10115", city="Berlin",
    )
    auftrag = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag ohne Termin"
    )
    bericht = report_service.create_report(
        app_user.id, work_order_id=auftrag.id, report_date="2026-08-02",
        activity_text="Ohne Termin.",
    )

    assert report_service.gebuchte_zeiten(bericht) == []


@pytest.mark.django_db
def test_none_ist_kein_fehler(app_user):
    """Der PDF-Renderer ruft die Funktion unbesehen — sie muss robust sein."""
    assert report_service.gebuchte_zeiten(None) == []


@pytest.mark.django_db
def test_zeiten_werden_nicht_als_position_gespeichert(app_user):
    """Der Kern: keine zweite Ablage, also keine Doppelzählung.

    Die Abrechnung liest die Buchungen direkt (`abrechnung._zeitbuchungen`).
    Stünden dieselben Stunden zusätzlich als Berichtsposition, zählte die
    Rechnung sie zweimal.
    """
    from db_core.services import auftrag as auftrag_service
    from db_core.services import property as property_service

    obj = property_service.create_property(
        app_user.id, name="Zeitobjekt 2", property_type="WEG",
        street="Uhrweg", house_number="2", postal_code="10115", city="Berlin",
    )
    auftrag = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag"
    )
    bericht = report_service.create_report(
        app_user.id, work_order_id=auftrag.id, report_date="2026-08-02",
        activity_text="Gearbeitet.",
    )

    report_service.gebuchte_zeiten(bericht)

    # Keine Position ist dabei entstanden.
    assert list(report_service.list_report_lines(bericht.id)) == []
