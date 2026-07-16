"""Test des Cleanup-Commands `bereinige_vk_seed_unfall` gegen die Test-DB.

Geprüft wird der exakte Fingerabdruck: gelöscht werden NUR die Formel-Zeilen
(label='Standard', price_origin='MANUELL', fixed_price IS NULL) der angegebenen
Unfall-Gruppe. Festpreis-Zeilen und Zeilen anderer Gruppen bleiben; --dry-run
schreibt nichts; ein zweiter Lauf findet nichts mehr (idempotent).
"""
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from db_core.models import Article, ArticleSalePrice
from db_core.services import artikel as artikel_service


def _article(app_user, number):
    return artikel_service.create_article(
        app_user.id, article_number=number, description=f"Artikel {number}",
        unit="Stk", line_type="MATERIAL", list_price=Decimal("100.0000"),
    )


def _grp(app_user, name):
    return artikel_service.create_sale_price_group(
        app_user.id, name=name, calc_basis="LISTENPREIS",
        operator="AUFSCHLAG", percent_change=Decimal("45.000"),
    )


def _lauf(**kwargs):
    out = StringIO()
    call_command("bereinige_vk_seed_unfall", stdout=out, stderr=out, **kwargs)
    return out.getvalue()


@pytest.mark.django_db
def test_loescht_nur_den_fingerabdruck(app_user):
    unfall = _grp(app_user, "Aufschlag 45% (Material)")
    andere = _grp(app_user, "Andere Gruppe")

    # Treffer: Standard/MANUELL/ohne Festpreis über die Unfall-Gruppe.
    art_treffer = _article(app_user, "DN-bo-1")
    treffer = artikel_service.set_article_sale_price(
        app_user.id, article_id=art_treffer.id,
        sale_price_group_id=unfall.id, is_standard=True,
    )
    # Kein Treffer 1: Festpreis (fixed_price IS NOT NULL).
    art_fest = _article(app_user, "DN-bo-2")
    fest = artikel_service.set_article_sale_price(
        app_user.id, article_id=art_fest.id, fixed_price=Decimal("9.99"),
        is_standard=True,
    )
    # Kein Treffer 2: Formel-Zeile, aber ANDERE Gruppe.
    art_andere = _article(app_user, "DN-bo-3")
    behalten = artikel_service.set_article_sale_price(
        app_user.id, article_id=art_andere.id,
        sale_price_group_id=andere.id, is_standard=True,
    )

    # --dry-run: meldet den einen Treffer, löscht aber nichts.
    ausgabe = _lauf(sale_price_group_id=str(unfall.id), dry_run=True)
    assert "1" in ausgabe
    assert ArticleSalePrice.objects.count() == 3

    # Echter Lauf: genau die Unfall-Zeile verschwindet.
    _lauf(sale_price_group_id=str(unfall.id))
    verbliebene = set(ArticleSalePrice.objects.values_list("id", flat=True))
    assert treffer.id not in verbliebene
    assert fest.id in verbliebene
    assert behalten.id in verbliebene

    # Idempotent: zweiter Lauf findet nichts mehr.
    ausgabe2 = _lauf(sale_price_group_id=str(unfall.id))
    assert "Nichts zu tun" in ausgabe2
