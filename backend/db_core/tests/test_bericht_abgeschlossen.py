"""Der dritte Berichtszustand — ABGESCHLOSSEN (Migration 0144).

Sascha am 2026-08-02:

    „Tatsache unterschreiben eher wenige Kunden … ca. 80 % unserer Berichte sind
    ohne Unterschrift. Die Vorgabe/Regel ist also eher kontraproduktiv für uns.“

Bis dahin kannte ein Bericht nur ENTWURF und UNTERZEICHNET, und die Abrechnung
zog allein aus unterzeichneten. Der Normalfall dieses Betriebs — fertig, aber
niemand hat unterschrieben — hatte damit keinen eigenen Zustand und blieb als
Entwurf liegen, wo die Abrechnung ihn nicht erreichte.

Diese Tests halten beide Hälften fest: Was jetzt **geht** (abgeschlossene
Berichte tragen eine Rechnung) und was weiterhin **nicht** geht (an einem
Entwurf tippt der Monteur womöglich noch; ein abgeschlossener Bericht wird nicht
nachträglich umgeschrieben).
"""
from decimal import Decimal

import pytest

from db_core.services import abrechnung as abrechnung_service
from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service
from db_core.services.site_report import SiteReportError


def _auftrag(actor):
    obj = property_service.create_property(
        actor.id, name="Baustelle Wartenberg", property_type="WEG",
        street="Wartenbergstraße", house_number="24",
        postal_code="10365", city="Berlin",
    )
    return auftrag_service.create_work_order(
        actor.id, property_id=obj.id, title="Bad sanieren"
    )


def _bericht(actor, auftrag, text="Waschtisch montiert."):
    return report_service.create_report(
        actor.id, work_order_id=auftrag.id, report_date="2026-08-01",
        activity_text=text,
    )


# --- Der Statusautomat ------------------------------------------------------

@pytest.mark.django_db
def test_entwurf_laesst_sich_abschliessen(app_user):
    bericht = _bericht(app_user, _auftrag(app_user))
    assert bericht.status == "ENTWURF"

    bericht = report_service.abschliessen(app_user.id, report_id=bericht.id)

    assert bericht.status == "ABGESCHLOSSEN"


@pytest.mark.django_db
def test_abschliessen_friert_den_briefkopf_ein(app_user):
    """Wie beim Unterzeichnen (Befund B9): Der Bericht ist ab hier
    Abrechnungsgrundlage — zieht der Kunde später um, darf das den Bericht nicht
    rückwirkend umschreiben."""
    bericht = _bericht(app_user, _auftrag(app_user))
    assert not bericht.header_snapshot

    bericht = report_service.abschliessen(app_user.id, report_id=bericht.id)

    assert bericht.header_snapshot


@pytest.mark.django_db
def test_zweimal_abschliessen_wird_abgelehnt(app_user):
    bericht = _bericht(app_user, _auftrag(app_user))
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    with pytest.raises(SiteReportError, match="bereits abgeschlossen"):
        report_service.abschliessen(app_user.id, report_id=bericht.id)


@pytest.mark.django_db
def test_die_datenbank_verbietet_das_wieder_oeffnen(app_user):
    """Nicht der Service entscheidet das, sondern der Trigger.

    Ein abgeschlossener Bericht trägt womöglich schon eine Rechnung. Ließe er
    sich zurück in den Entwurf holen, wäre die Grundlage einer gestellten
    Forderung nachträglich veränderbar.
    """
    from django.db import Error

    from db_core.db_context import business_transaction
    from db_core.models import SiteReport

    bericht = _bericht(app_user, _auftrag(app_user))
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    with pytest.raises(Error, match="nicht wieder"):
        with business_transaction(app_user.id):
            SiteReport.objects.filter(id=bericht.id).update(status="ENTWURF")


@pytest.mark.django_db
def test_die_datenbank_verbietet_inhaltliche_aenderung(app_user):
    from django.db import Error

    from db_core.db_context import business_transaction
    from db_core.models import SiteReport

    bericht = _bericht(app_user, _auftrag(app_user))
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    with pytest.raises(Error, match="unveränderlich"):
        with business_transaction(app_user.id):
            SiteReport.objects.filter(id=bericht.id).update(
                activity_text="Doch etwas ganz anderes gemacht."
            )


@pytest.mark.django_db
def test_positionen_sind_nach_dem_abschluss_gesperrt(app_user):
    """Das Loch, das der Kopf-Trigger allein offen gelassen hätte.

    `0080` sperrte Positionen nur bei UNTERZEICHNET. Ohne die Erweiterung in
    `0144` ließe sich ein Bericht abschließen, abrechnen — und anschließend die
    Menge umschreiben, auf der die Rechnung fußt.
    """
    auftrag = _auftrag(app_user)
    artikel = artikel_service.create_article(
        app_user.id, article_number="WT-4713", description="Waschtisch groß",
        unit="Stk", line_type="MATERIAL", list_price=Decimal("299.00"),
    )
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{
            "line_type": "MATERIAL", "article_id": artikel.id,
            "description": "Waschtisch groß", "quantity": "1", "unit": "Stk",
        }],
    )
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    with pytest.raises(Exception, match="abgeschlossen"):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{
                "line_type": "MATERIAL", "article_id": artikel.id,
                "description": "Waschtisch groß", "quantity": "9", "unit": "Stk",
            }],
        )


# --- Die Unterschrift bleibt möglich ----------------------------------------

@pytest.mark.django_db
def test_unterschrift_laesst_sich_nachreichen(app_user, monkeypatch):
    """Der eine erlaubte Ausgang aus ABGESCHLOSSEN.

    Kommt die Verwaltung eine Woche später doch noch mit einer Unterschrift,
    darf sie nachgetragen werden — ohne den Inhalt anzurühren.
    """
    from db_core import storage as storage_module

    class FakeStorage:
        def ensure_bucket(self):
            pass

        def put_object(self, key, data, content_type="application/octet-stream"):
            return storage_module.ObjectInfo(
                storage_key=key, sha256="a" * 64, size_bytes=len(bytes(data))
            )

    monkeypatch.setattr(storage_module, "get_storage", lambda: FakeStorage())
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    bericht = _bericht(app_user, _auftrag(app_user))
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    bericht = report_service.sign_report(
        app_user.id, report_id=bericht.id,
        signed_by_name="Frau Stegos", signature_png=png,
    )

    assert bericht.status == "UNTERZEICHNET"
    assert bericht.signed_by_name == "Frau Stegos"


# --- Und der eigentliche Zweck: die Abrechnung ------------------------------

@pytest.mark.django_db
def test_abgeschlossener_bericht_traegt_die_abrechnung(app_user):
    """Der Kern des Slice: ohne Unterschrift, trotzdem abrechenbar."""
    auftrag = _auftrag(app_user)
    artikel = artikel_service.create_article(
        app_user.id, article_number="WT-4711", description="Waschtisch",
        unit="Stk", line_type="MATERIAL", list_price=Decimal("249.00"),
    )
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{
            "line_type": "MATERIAL", "article_id": artikel.id,
            "description": "Waschtisch", "quantity": "1", "unit": "Stk",
        }],
    )
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    positionen = abrechnung_service._berichtspositionen(auftrag.id)

    assert [p.description for p in positionen] == ["Waschtisch"]


@pytest.mark.django_db
def test_entwurf_traegt_die_abrechnung_weiterhin_nicht(app_user):
    """Die Vorsicht, die bleibt: An einem Entwurf tippt der Monteur womöglich noch."""
    auftrag = _auftrag(app_user)
    artikel = artikel_service.create_article(
        app_user.id, article_number="WT-4712", description="Waschtisch klein",
        unit="Stk", line_type="MATERIAL", list_price=Decimal("199.00"),
    )
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{
            "line_type": "MATERIAL", "article_id": artikel.id,
            "description": "Waschtisch klein", "quantity": "1", "unit": "Stk",
        }],
    )

    assert abrechnung_service._berichtspositionen(auftrag.id) == []
    # …und er wird nicht verschwiegen, sondern benannt.
    assert [b.id for b in abrechnung_service._entwurfsberichte(auftrag.id)] == [bericht.id]


@pytest.mark.django_db
def test_abgeschlossener_bericht_gilt_nicht_mehr_als_entwurf(app_user):
    """Sonst meldete die Vorschau eine Lücke, die keine ist — und jemand suchte
    nach Geld, das längst in der Rechnung steht."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    assert abrechnung_service._entwurfsberichte(auftrag.id) == []


# --- Entwürfe dürfen weg, Fertiges nicht -----------------------------------

@pytest.mark.django_db
def test_entwurf_laesst_sich_loeschen(app_user):
    """Sascha: „Entwürfe alle löschbar … das müllt das System zu."""
    from db_core.models import SiteReport

    bericht = _bericht(app_user, _auftrag(app_user))

    report_service.delete_report(app_user.id, report_id=bericht.id)

    assert not SiteReport.objects.filter(id=bericht.id).exists()


@pytest.mark.django_db
def test_abgeschlossener_bericht_laesst_sich_nicht_loeschen(app_user):
    """Ab hier ist er Abrechnungsgrundlage — er bleibt."""
    bericht = _bericht(app_user, _auftrag(app_user))
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    with pytest.raises(SiteReportError, match="nicht mehr löschen"):
        report_service.delete_report(app_user.id, report_id=bericht.id)


@pytest.mark.django_db
def test_die_datenbank_verbietet_das_loeschen_am_dienst_vorbei(app_user):
    """Nicht der Dienst hält das dicht, sondern der Trigger aus 0145."""
    from django.db import Error

    from db_core.db_context import business_transaction
    from db_core.models import SiteReport

    bericht = _bericht(app_user, _auftrag(app_user))
    report_service.abschliessen(app_user.id, report_id=bericht.id)

    with pytest.raises(Error, match="nur ein Entwurf"):
        with business_transaction(app_user.id):
            SiteReport.objects.filter(id=bericht.id).delete()


@pytest.mark.django_db
def test_loeschen_nimmt_die_positionen_mit(app_user):
    """Eine zurückbleibende Position verwiese auf einen Bericht, den es nicht
    mehr gibt."""
    from db_core.models import SiteReportLine

    auftrag = _auftrag(app_user)
    artikel = artikel_service.create_article(
        app_user.id, article_number="WT-4714", description="Eckventil",
        unit="Stk", line_type="MATERIAL", list_price=Decimal("9.90"),
    )
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{
            "line_type": "MATERIAL", "article_id": artikel.id,
            "description": "Eckventil", "quantity": "2", "unit": "Stk",
        }],
    )
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 1

    report_service.delete_report(app_user.id, report_id=bericht.id)

    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 0
