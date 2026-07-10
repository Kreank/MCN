"""Artikel werden nie gelöscht, nur deaktiviert.

`pricing.article` trägt `trg_article_no_delete` (GoBD/Historienschutz): Belege
verweisen über `source_article_id` auf den Artikel, ein DELETE zerrisse sie.
Ausrangiertes Material wird deshalb auf INAKTIV gesetzt — und muss dann auch aus
der Artikelsuche verschwinden, sonst landet es wieder im nächsten Angebot.
"""
import uuid

import pytest
from django.db import Error, transaction

from db_core.db_context import business_transaction
from db_core.models import Article
from db_core.services import artikel as artikel_service


def _artikel(app_user, nummer="TEST-1", status="AKTIV"):
    a = artikel_service.create_article(
        app_user.id, article_number=nummer, description="Prüfartikel", unit="Stk",
    )
    if status != "AKTIV":
        artikel_service.set_article_status(app_user.id, article_id=a.id, status=status)
        a.refresh_from_db()
    return a


@pytest.mark.django_db
def test_artikel_kann_nicht_geloescht_werden(app_user):
    """Die DB verbietet DELETE physisch — nicht nur die Anwendung."""
    a = _artikel(app_user, "TEST-DEL")
    with pytest.raises(Error):
        with transaction.atomic():
            with business_transaction(app_user.id):
                Article.objects.filter(id=a.id).delete()
    assert Article.objects.filter(id=a.id).exists()


@pytest.mark.django_db
def test_deaktivieren_und_reaktivieren(app_user):
    a = _artikel(app_user, "TEST-STAT")
    assert a.status == "AKTIV"
    artikel_service.set_article_status(app_user.id, article_id=a.id, status="INAKTIV")
    a.refresh_from_db()
    assert a.status == "INAKTIV"
    artikel_service.set_article_status(app_user.id, article_id=a.id, status="AKTIV")
    a.refresh_from_db()
    assert a.status == "AKTIV"


@pytest.mark.django_db
def test_ungueltiger_status_wird_abgewiesen(app_user):
    a = _artikel(app_user, "TEST-BAD")
    with pytest.raises(ValueError, match="Status"):
        artikel_service.set_article_status(
            app_user.id, article_id=a.id, status="GELOESCHT"
        )


@pytest.mark.django_db
def test_unbekannter_artikel(app_user):
    with pytest.raises(ValueError, match="nicht gefunden"):
        artikel_service.set_article_status(
            app_user.id, article_id=uuid.uuid4(), status="INAKTIV"
        )


@pytest.mark.django_db
def test_status_setzen_ist_idempotent(app_user):
    """Derselbe Status noch einmal setzen schreibt nicht und wirft nicht."""
    a = _artikel(app_user, "TEST-IDEM", status="INAKTIV")
    vorher = a.updated_at
    erneut = artikel_service.set_article_status(
        app_user.id, article_id=a.id, status="INAKTIV"
    )
    assert erneut.status == "INAKTIV"
    assert erneut.updated_at == vorher
