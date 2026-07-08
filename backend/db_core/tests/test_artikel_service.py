"""Service-Tests der Artikel-Schicht gegen die echte Test-DB."""
import pytest

from django.db import Error, transaction

from db_core.models import Article, AssemblyComponent
from db_core.services import artikel as artikel_service


@pytest.mark.django_db
def test_create_article(app_user):
    a = artikel_service.create_article(
        app_user.id, article_number="A-1", description="Schraube", unit="Stk",
        list_price="0.20",
    )
    assert a.status == "AKTIV"
    assert a.version == 1
    assert a.line_type == "MATERIAL"


@pytest.mark.django_db
def test_create_article_ungueltiger_line_type(app_user):
    with pytest.raises(ValueError):
        artikel_service.create_article(
            app_user.id, article_number="A-2", description="x", unit="Stk",
            line_type="FALSCH",
        )


@pytest.mark.django_db
def test_create_article_no_delete(app_user):
    a = artikel_service.create_article(
        app_user.id, article_number="A-3", description="x", unit="Stk",
    )
    with pytest.raises(Error):
        with transaction.atomic():
            Article.objects.filter(id=a.id).delete()


@pytest.mark.django_db
def test_create_assembly_mit_komponenten(app_user):
    art = artikel_service.create_article(
        app_user.id, article_number="A-4", description="Ziegel", unit="Stk",
    )
    wg = artikel_service.create_wage_group(
        app_user.id, name="Monteur A", hourly_rate="58.00",
    )
    asm = artikel_service.create_assembly(
        app_user.id, assembly_number="L-1", name="Leistung", unit="m²",
        components=[
            {"article_id": art.id, "quantity": "5.000"},
            {"wage_group_id": wg.id, "minutes": "30.00"},
        ],
    )
    comps = AssemblyComponent.objects.filter(assembly_id=asm.id).order_by("position")
    assert comps.count() == 2
    assert comps[0].article_id == art.id and comps[0].quantity is not None
    assert comps[1].wage_group_id == wg.id and comps[1].minutes is not None


@pytest.mark.django_db
def test_create_assembly_material_ohne_menge(app_user):
    art = artikel_service.create_article(
        app_user.id, article_number="A-6", description="x", unit="Stk",
    )
    with pytest.raises(ValueError):
        artikel_service.create_assembly(
            app_user.id, assembly_number="L-3", name="X", unit="m²",
            components=[{"article_id": art.id}],  # quantity fehlt
        )


@pytest.mark.django_db
def test_create_assembly_komponente_xor(app_user):
    art = artikel_service.create_article(
        app_user.id, article_number="A-5", description="x", unit="Stk",
    )
    wg = artikel_service.create_wage_group(
        app_user.id, name="Monteur B", hourly_rate="58.00",
    )
    with pytest.raises(ValueError):
        artikel_service.create_assembly(
            app_user.id, assembly_number="L-2", name="X", unit="m²",
            components=[{"article_id": art.id, "wage_group_id": wg.id}],
        )
