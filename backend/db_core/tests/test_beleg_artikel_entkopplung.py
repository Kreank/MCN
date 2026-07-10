"""Eine Belegposition ist eine KOPIE, kein Verweis auf den Artikelstamm.

Wer im Angebot den Preis, die Bezeichnung oder den Einkaufspreis einer Position
ändert, ändert **nur die Position**. Der Artikel in `pricing.article` bleibt
unberührt — sonst schriebe ein einzelnes Angebot den Stammdatensatz um, den alle
anderen Angebote ebenfalls verwenden.

Umgekehrt gilt dasselbe: ändert sich später der Artikelstamm (neuer EK aus dem
DATANORM-Import), verändert das kein bereits geschriebenes Angebot. Die
Positionswerte sind zum Zeitpunkt der Übernahme eingefroren — das ist die
Grundlage dafür, dass die Marge eines gestellten Angebots nachvollziehbar bleibt.

`source_article_id` ist ein reiner Herkunftsvermerk („dieser Posten kam aus
Artikel X"), aus dem nichts gelesen und in den nichts geschrieben wird.
"""
from decimal import Decimal

import pytest

from db_core.models import Article, QuoteLine
from db_core.services import artikel as artikel_service
from db_core.services import beleg as beleg_service
from db_core.services import property as property_service


@pytest.fixture
def artikel(app_user):
    return artikel_service.create_article(
        app_user.id,
        article_number="ENTK-1",
        description="Kupferrohr 18 mm",
        unit="m",
        list_price=Decimal("12.5000"),
    )


@pytest.fixture
def objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Entkopplung", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _position(artikel, **abweichung):
    basis = {
        "line_type": "MATERIAL",
        "description": artikel.description,
        "quantity": "10",
        "unit": artikel.unit,
        "unit_price": "18.00",
        "tax_code": "DE_19",
        "unit_cost": "12.50",
        "source_article_id": str(artikel.id),
    }
    basis.update(abweichung)
    return basis


@pytest.mark.django_db
def test_position_aendern_laesst_artikelstamm_unberuehrt(app_user, artikel, objekt):
    """Preis, Bezeichnung und EK in der Position ändern — der Artikel bleibt gleich."""
    vorher = {
        "description": artikel.description,
        "list_price": artikel.list_price,
        "unit": artikel.unit,
    }

    q = beleg_service.create_quote(
        app_user.id, property_id=objekt.id, title="Entkopplung",
        lines=[_position(artikel)],
    )
    # Jetzt im Angebot alles umschreiben, was der Editor umschreiben kann.
    beleg_service.update_quote(
        app_user.id, quote_id=q.id,
        lines=[
            _position(
                artikel,
                description="Kupferrohr 18 mm, SONDERPREIS Baustelle Nord",
                unit_price="9.90",
                unit_cost="4.00",
                discount_percent="15",
                unit="lfm",
            )
        ],
    )

    artikel.refresh_from_db()
    assert artikel.description == vorher["description"]
    assert artikel.list_price == vorher["list_price"]
    assert artikel.unit == vorher["unit"]

    zeile = QuoteLine.objects.get(quote_id=q.id, position_number=1)
    assert zeile.description.endswith("SONDERPREIS Baustelle Nord")
    assert zeile.unit_price == Decimal("9.90")
    assert zeile.unit_cost == Decimal("4.00")
    assert zeile.unit == "lfm"
    # Die Herkunft bleibt vermerkt, obwohl alles abweicht.
    assert zeile.source_article_id == artikel.id


@pytest.mark.django_db
def test_artikelstamm_aendern_laesst_bestehendes_angebot_unberuehrt(
    app_user, artikel, objekt
):
    """Der umgekehrte Weg: ein neuer EK im Stamm verfälscht keine alte Kalkulation."""
    q = beleg_service.create_quote(
        app_user.id, property_id=objekt.id, title="Eingefroren",
        lines=[_position(artikel)],
    )
    artikel_service.update_article(
        app_user.id, article_id=artikel.id,
        description="Kupferrohr 18 mm (neue Charge)",
        list_price=Decimal("19.9000"),
    )

    zeile = QuoteLine.objects.get(quote_id=q.id, position_number=1)
    assert zeile.description == "Kupferrohr 18 mm"
    assert zeile.unit_price == Decimal("18.00")
    assert zeile.unit_cost == Decimal("12.50")

    q.refresh_from_db()
    assert q.net_total == Decimal("180.00")


@pytest.mark.django_db
def test_zwei_angebote_teilen_denselben_artikel_ohne_sich_zu_stoeren(
    app_user, artikel, objekt
):
    q1 = beleg_service.create_quote(
        app_user.id, property_id=objekt.id, title="Angebot A",
        lines=[_position(artikel, unit_price="18.00")],
    )
    q2 = beleg_service.create_quote(
        app_user.id, property_id=objekt.id, title="Angebot B",
        lines=[_position(artikel, unit_price="25.00", description="Mit Zuschlag")],
    )
    l1 = QuoteLine.objects.get(quote_id=q1.id, position_number=1)
    l2 = QuoteLine.objects.get(quote_id=q2.id, position_number=1)

    assert l1.unit_price == Decimal("18.00")
    assert l2.unit_price == Decimal("25.00")
    assert l1.source_article_id == l2.source_article_id == artikel.id
    artikel.refresh_from_db()
    assert artikel.description == "Kupferrohr 18 mm"


def test_beleg_service_schreibt_nie_in_den_artikelstamm():
    """Statische Absicherung gegen den Fehler, bevor er entsteht.

    `ensure_exists(Article, ...)` prüft nur, dass es den Artikel gibt — lesend.
    Ein `Article.objects.create/update` oder ein `article.save()` in beleg.py wäre
    dagegen genau der Fehler, den die Tests oben verhindern: ein einzelnes
    Angebot schriebe den Stammdatensatz um, den alle anderen mitbenutzen.
    """
    import inspect

    from db_core.services import beleg

    quelle = inspect.getsource(beleg)
    for verboten in ("Article.objects.create", "Article.objects.update", "article.save"):
        assert verboten not in quelle, (
            f"beleg.py schreibt in den Artikelstamm ('{verboten}'). "
            "Eine Belegposition ist eine Kopie, kein Verweis."
        )


# ---------------------------------------------------------------------------
# Das Häkchen: „Änderungen an Stammdaten übernehmen"
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_uebernahme_nur_auf_ausdrueckliche_anforderung(app_user, artikel, objekt):
    """Ohne Übernahme bleibt der Stamm unberührt, mit Übernahme zieht er nach."""
    beleg_service.create_quote(
        app_user.id, property_id=objekt.id, title="Q",
        lines=[_position(artikel, description="Kupferrohr 18 mm, entgratet")],
    )
    artikel.refresh_from_db()
    assert artikel.description == "Kupferrohr 18 mm"      # unberührt

    artikel_service.positionswerte_in_stammdaten(
        app_user.id, article_id=artikel.id,
        description="Kupferrohr 18 mm, entgratet",
        verkaufspreis=Decimal("18.00"),
    )
    artikel.refresh_from_db()
    assert artikel.description == "Kupferrohr 18 mm, entgratet"


@pytest.mark.django_db
def test_uebernahme_setzt_standard_verkaufspreis(app_user, artikel):
    from db_core.models import ArticleSalePrice

    gruppe = artikel_service.create_sale_price_group(
        app_user.id, name="Formel +20%", percent_change=Decimal("20.000")
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=artikel.id, sale_price_group_id=gruppe.id,
        is_standard=True, label="Formel",
    )
    artikel_service.positionswerte_in_stammdaten(
        app_user.id, article_id=artikel.id, verkaufspreis=Decimal("21.50")
    )

    varianten = ArticleSalePrice.objects.filter(article_id=artikel.id)
    standard = varianten.get(is_standard=True)
    assert standard.fixed_price == Decimal("21.50")
    # Die Formel-Gruppe bleibt erhalten, nur ohne Standard-Status.
    alt = varianten.get(label="Formel")
    assert alt.is_standard is False
    assert alt.sale_price_group_id == gruppe.id


@pytest.mark.django_db
def test_einkaufspreis_wird_nicht_uebernommen(app_user, artikel):
    """Der EK ist die Aussage des Händlers, nicht die des Angebotsschreibers."""
    with pytest.raises(ValueError, match="nicht in den Stamm übernehmen"):
        artikel_service.positionswerte_in_stammdaten(
            app_user.id, article_id=artikel.id, unit_cost=Decimal("4.00")
        )


@pytest.mark.django_db
def test_uebernahme_ohne_angaben(app_user, artikel):
    with pytest.raises(ValueError, match="nichts zum Übernehmen"):
        artikel_service.positionswerte_in_stammdaten(
            app_user.id, article_id=artikel.id
        )
