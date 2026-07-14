"""Der Ausgang eines Angebots: ANGENOMMEN | ABGELEHNT | ABGELAUFEN.

Die Übergänge lagen seit Migration 0016 in `workflow.status_transition` — **gesetzt
hat sie nie ein Produktpfad**. Ein Angebot blieb für immer „versendet", auch wenn
der Kunde längst zugesagt hatte. Klassisches „DB fertig, Wiring fehlt".

Die zwei Dinge, die dabei kaputtgehen können, stehen hier:

* **B-30**: Der Snapshot und der Inhalts-Hash eines versendeten Angebots bleiben
  Zeichen für Zeichen dieselben. Ein Statuswechsel ist kein Inhaltswechsel.
* **Das Soll**: Ein ABGELEHNTES Angebot bildet kein Soll mehr
  (`SOLL_AUSGESCHLOSSENE_STATUS`) — der Soll-Ist-Abgleich rechnet sich neu. Das ist
  die eigentliche Wirkung von „abgelehnt", nicht bloß ein Etikett.
"""
import pytest

from db_core.models import Quote
from db_core.services import beleg as beleg_service
from db_core.services import objektsicht
from db_core.services import site_report as report_service
from db_core.tests.test_abrechnung_service import (  # noqa: F401
    _angebot,
    _auftrag,
    _artikel,
    fake_storage,
    szenario,
)


def _pos(desc, qty, preis):
    return {
        "line_type": "MATERIAL", "line_kind": "NORMAL", "description": desc,
        "quantity": qty, "unit": "stk", "unit_price": preis, "tax_code": "DE_19",
    }


@pytest.mark.django_db
def test_versendetes_angebot_wird_angenommen(szenario):
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    quote = _angebot(szenario, order, [_pos("Thermostatventil", "18", "24.00")])
    assert quote.status == "VERSENDET"
    vorher = (quote.billing_snapshot, quote.content_hash, quote.quote_number)

    quote = beleg_service.set_quote_status(
        szenario["user"].id, quote_id=quote.id, to_status="ANGENOMMEN"
    )
    assert quote.status == "ANGENOMMEN"
    # B-30: der Inhalt des versendeten Angebots ist unangetastet.
    assert (quote.billing_snapshot, quote.content_hash, quote.quote_number) == vorher


@pytest.mark.django_db
def test_abgelehntes_angebot_bildet_kein_soll_mehr(szenario, fake_storage):
    """Die eigentliche Wirkung von „abgelehnt" — nicht bloß ein Etikett."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    artikel = _artikel(szenario, "TH-R", vk="24.00", beschreibung="Thermostatventil")
    quote = _angebot(szenario, order, [{
        "line_type": "MATERIAL", "line_kind": "NORMAL",
        "description": "Thermostatventil", "quantity": "18", "unit": "stk",
        "unit_price": "24.00", "tax_code": "DE_19",
        "source_article_id": str(artikel.id),
    }])
    assert report_service.soll_ist(order.id)["positionen"][0]["soll"] != 0

    beleg_service.set_quote_status(
        szenario["user"].id, quote_id=quote.id, to_status="ABGELEHNT"
    )
    abgleich = report_service.soll_ist(order.id)
    assert abgleich["positionen"] == []
    assert abgleich["angebote"] == []


@pytest.mark.django_db
def test_abgelaufen_ist_erlaubt_und_bleibt_ein_soll(szenario):
    """ABGELAUFEN kennt der Statusautomat — und das Angebot bleibt ein Soll
    (`SOLL_AUSGESCHLOSSENE_STATUS` nennt es nicht): Was abgelaufen ist, wurde
    trotzdem so angeboten."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    quote = _angebot(szenario, order, [_pos("Ventil", "1", "24.00")])
    quote = beleg_service.set_quote_status(
        szenario["user"].id, quote_id=quote.id, to_status="ABGELAUFEN"
    )
    assert quote.status == "ABGELAUFEN"
    assert "ABGELAUFEN" not in report_service.SOLL_AUSGESCHLOSSENE_STATUS


@pytest.mark.django_db
def test_entwurf_kann_nicht_angenommen_werden(szenario):
    """Ein Angebot, das nie hinausgegangen ist, kann niemand angenommen haben."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    quote = _angebot(
        szenario, order, [_pos("Ventil", "1", "24.00")], versenden=False
    )
    with pytest.raises(ValueError, match="nicht erlaubt"):
        beleg_service.set_quote_status(
            szenario["user"].id, quote_id=quote.id, to_status="ANGENOMMEN"
        )


@pytest.mark.django_db
def test_zweiter_ausgang_ist_gesperrt(szenario):
    """Angenommen bleibt angenommen — ein zweiter Ausgang ist kein Übergang der DB."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    quote = _angebot(szenario, order, [_pos("Ventil", "1", "24.00")])
    beleg_service.set_quote_status(
        szenario["user"].id, quote_id=quote.id, to_status="ANGENOMMEN"
    )
    with pytest.raises(ValueError, match="nicht erlaubt"):
        beleg_service.set_quote_status(
            szenario["user"].id, quote_id=quote.id, to_status="ABGELEHNT"
        )
    assert Quote.objects.get(id=quote.id).status == "ANGENOMMEN"


@pytest.mark.django_db
def test_ersetzt_ist_kein_statuswechsel(szenario):
    """ERSETZT verlangt ein **Nachfolgeangebot** (DB-CHECK, 0018).

    Es hier als nackten Statuswechsel anzubieten hieße, einen Knopf zu geben, der
    zuverlässig am CHECK scheitert. Der Fehler sagt stattdessen, was fehlt.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    quote = _angebot(szenario, order, [_pos("Ventil", "1", "24.00")])
    with pytest.raises(ValueError, match="Nachfolgeangebot"):
        beleg_service.set_quote_status(
            szenario["user"].id, quote_id=quote.id, to_status="ERSETZT"
        )
    assert Quote.objects.get(id=quote.id).status == "VERSENDET"


def test_monteurssicht_traegt_den_normalfall():
    """Nebenwirkung: Angebote gehen künftig wirklich auf ANGENOMMEN.

    Die preisfreie Objektsicht des Monteurs zeigt VERSENDET **und** ANGENOMMEN —
    sonst verschwände das Angebot aus seiner Sicht in genau dem Moment, in dem es
    verbindlich wird (und die Vorbelegung des Berichts liefe ins Leere).
    """
    assert "ANGENOMMEN" in objektsicht.ANGEBOT_STATUS_EIGENE
