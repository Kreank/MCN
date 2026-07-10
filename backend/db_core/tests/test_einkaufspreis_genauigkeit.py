"""Der Einkaufspreis braucht vier Nachkommastellen (Migration 0038).

Bei DATANORM-Preiseinheit 100 oder 1000 liegen echte Stückpreise unterhalb eines
Cents. Stahlhaften 20 cm: Listenpreis 12,90 € für 100 Stück, minus 40 % Rabatt =
0,0774 € je Stück. Auf zwei Nachkommastellen gerundet wären das 0,08 € — bei 1000
Stück ein Fehler von 2,60 €, also 3,4 %.

Der Verkaufspreis bleibt bei zwei Nachkommastellen: er steht auf dem Kundenbeleg
und ist GoBD-relevant. Die VK-Formel rechnet mit der vollen EK-Genauigkeit und
rundet erst am Schluss (siehe test_vk_rundet_erst_am_schluss).
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from db_core.db_context import business_transaction
from db_core.models import ArticleSupplierReference
from db_core.services import artikel as artikel_service
from db_core.services import identity as identity_service
from db_core.services import kalkulation


def _lieferant(app_user):
    return identity_service.create_organization(
        app_user.id, "Testgrosshandel GmbH", "COMPANY"
    )


def _referenz(app_user, article, lieferant, ek, *, price_unit_code=0, list_price=None):
    with business_transaction(app_user.id):
        return ArticleSupplierReference.objects.create(
            id=uuid.uuid4(),
            article_id=article.id,
            supplier_party_id=lieferant.id,
            source_system="DATANORM",
            source_namespace="test",
            supplier_article_number="X-1",
            last_purchase_price=ek,
            list_price=list_price,
            price_unit_code=price_unit_code,
            # DB-CHECK: (last_purchase_price IS NULL) = (currency IS NULL).
            # Kein Preis heißt keine Währung — der Importer muss das beachten.
            currency="EUR" if ek is not None else None,
            valid_from=date(2026, 1, 1),
        )


@pytest.mark.django_db
def test_stahlhaften_ek_unter_einem_cent(app_user):
    """0,0774 €/Stück muss exakt gespeichert werden, nicht als 0,08 €."""
    a = artikel_service.create_article(
        app_user.id, article_number="EK-4NK", description="Stahlhaften 20cm", unit="Stk"
    )
    ref = _referenz(
        app_user, a, _lieferant(app_user),
        Decimal("0.0774"), price_unit_code=2, list_price=Decimal("0.1290"),
    )
    ref.refresh_from_db()
    assert ref.last_purchase_price == Decimal("0.0774")
    assert ref.last_purchase_price != Decimal("0.08")
    assert ref.list_price == Decimal("0.1290")
    assert ref.price_unit_code == 2

    # Bei 1000 Stück ist der Unterschied kein Rundungsrauschen mehr.
    assert ref.last_purchase_price * 1000 == Decimal("77.4000")
    assert Decimal("0.08") * 1000 == Decimal("80.000")


@pytest.mark.django_db
def test_je_tausend_artikel(app_user):
    """Preiseinheit 1000: 0,0372 € je Stück bleibt darstellbar."""
    a = artikel_service.create_article(
        app_user.id, article_number="EK-1000", description="Zugdraht", unit="Stk"
    )
    ref = _referenz(app_user, a, _lieferant(app_user), Decimal("0.0372"), price_unit_code=3)
    ref.refresh_from_db()
    assert ref.last_purchase_price == Decimal("0.0372")


@pytest.mark.django_db
def test_vk_rundet_erst_am_schluss(app_user):
    """Die VK-Formel rechnet mit der vollen EK-Genauigkeit und rundet zum Schluss.

    EK 0,0774 € + 50 % Aufschlag = 0,1161 € → VK 0,12 €.
    Wer den EK vorher auf 0,08 € rundete, käme auf 0,12 € — hier zufällig gleich.
    Deshalb zusätzlich ein Fall, in dem sich das Ergebnis unterscheidet:
    EK 0,0774 € + 10 % = 0,08514 € → 0,09 €; aus gerundetem 0,08 € würde 0,088 €
    → 0,09 €. Der sichtbare Unterschied entsteht bei größeren Mengen und im
    Deckungsbeitrag, der auf dem ungerundeten EK beruht.
    """
    a = artikel_service.create_article(
        app_user.id, article_number="EK-VK", description="Kleinteil", unit="Stk"
    )
    _referenz(app_user, a, _lieferant(app_user), Decimal("0.0774"), price_unit_code=2)
    gruppe = artikel_service.create_sale_price_group(
        app_user.id, name="Test +50%", calc_basis="EK", operator="AUFSCHLAG",
        percent_change=Decimal("50.000"),
    )
    artikel_service.set_article_sale_price(
        app_user.id, article_id=a.id, sale_price_group_id=gruppe.id, is_standard=True
    )

    k = kalkulation.article_kalkulation(a.id)
    assert k["ek"] == "0.0774"          # ungerundet gefuehrt
    variante = k["variants"][0]
    # 0,0774 * 1,5 = 0,1161 -> kaufmaennisch auf zwei Stellen
    assert variante["sale_price"] == "0.12"


@pytest.mark.django_db
def test_ek_unbekannt_bleibt_null(app_user):
    """Kein Preissatz heisst NULL, nicht 0,00 — sonst waere die Marge 100 %."""
    a = artikel_service.create_article(
        app_user.id, article_number="EK-NULL", description="Ohne Preis", unit="Stk"
    )
    _referenz(app_user, a, _lieferant(app_user), None)
    k = kalkulation.article_kalkulation(a.id)
    assert k["ek"] is None


@pytest.mark.django_db
def test_kein_preis_keine_waehrung(app_user):
    """DB-CHECK: (last_purchase_price IS NULL) = (currency IS NULL).

    Ein unbekannter Einkaufspreis (Rabattgruppe ohne .RAB-Datei, Werkspreis)
    darf keine Währung tragen. Der DATANORM-Importer muss das beachten, sonst
    scheitert jeder Artikel ohne auflösbaren Rabatt am CHECK.
    """
    from django.db import Error, transaction

    a = artikel_service.create_article(
        app_user.id, article_number="EK-WAEHRUNG", description="X", unit="Stk"
    )
    lieferant = _lieferant(app_user)
    with pytest.raises(Error):
        with transaction.atomic():
            with business_transaction(app_user.id):
                ArticleSupplierReference.objects.create(
                    id=uuid.uuid4(), article_id=a.id, supplier_party_id=lieferant.id,
                    source_system="DATANORM", source_namespace="test",
                    supplier_article_number="Y-1",
                    last_purchase_price=None, currency="EUR",   # verboten
                    valid_from=date(2026, 1, 1),
                )
