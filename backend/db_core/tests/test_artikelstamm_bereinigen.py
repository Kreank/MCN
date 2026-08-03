"""Die einmalige Bestandskorrektur des Artikelstamms.

Stellt den ALTEN, fehlerhaften Zustand künstlich her (präfixierte Nummer,
Matchcode als Herstellername, katalog-interne Nummer als Herstellernummer) und
prüft, dass das Kommando ihn richtig auflöst — inklusive der Kollision zwischen
Leitkatalog und Herstellerkatalog, an der die nackte Nummer sonst scheitert.
"""
import uuid

import pytest
from django.core.management import call_command
from django.db import connection as db_connection

from db_core.models import AppUser, Article, ArticleSupplierReference, Party


def _akteur():
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name="Seed", status="ACTIVE", version=1
    )


def _anbindung(ns, label, kind):
    party = Party.objects.create(
        id=uuid.uuid4(), party_type="ORGANIZATION", display_name=label,
        status="ACTIVE", version=1,
    )
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pricing.supplier_connection
                (id, supplier_party_id, source_system, source_namespace, label,
                 status, connection_kind, version)
            VALUES (gen_random_uuid(), %s, 'DATANORM', %s, %s, 'ACTIVE', %s, 1)
            """,
            [party.id, ns, label, kind],
        )
    return party


def _altbestand(ns, party, liefer_nr, *, matchcode, katalog_nr):
    """Ein Artikel so, wie der alte Importer ihn hinterlassen hat."""
    artikel = Article.objects.create(
        id=uuid.uuid4(), article_number=f"DN-{ns}-{liefer_nr}",
        description=f"Artikel {liefer_nr}", unit="ST", line_type="MATERIAL",
        status="AKTIV", version=1,
        manufacturer_name=matchcode,      # falsch: das ist der Matchcode
        manufacturer_number=katalog_nr,   # falsch: das ist die Katalognummer
    )
    ArticleSupplierReference.objects.create(
        id=uuid.uuid4(), article_id=artikel.id, supplier_party_id=party.id,
        source_system="DATANORM", source_namespace=ns,
        supplier_article_number=liefer_nr, valid_from="2026-01-01",
    )
    return artikel


@pytest.fixture
def bestand():
    _akteur()
    bo = _anbindung("bo", "Bär & Ollenroth KG", "GROSSHAENDLER")
    va = _anbindung("vaillant", "Vaillant Deutschland GmbH", "HERSTELLER")
    return {
        "cus": _altbestand("bo", bo, "CUS15H",
                           matchcode="CUSSH01510", katalog_nr="ZRB2071510"),
        # Dieselbe nackte Nummer in beiden Katalogen — verschiedene Artikel.
        "bo_kollision": _altbestand("bo", bo, "509010",
                                    matchcode="KUPFER", katalog_nr="AAASRT10298106"),
        "va_kollision": _altbestand("vaillant", va, "509010",
                                    matchcode=None, katalog_nr=None),
    }


@pytest.mark.django_db
def test_trockenlauf_schreibt_nichts(bestand):
    call_command("artikelstamm_bereinigen")
    bestand["cus"].refresh_from_db()
    assert bestand["cus"].article_number == "DN-bo-CUS15H"
    assert bestand["cus"].manufacturer_number == "ZRB2071510"


@pytest.mark.django_db
def test_grosshandel_verliert_die_erfundene_herstellernummer(bestand):
    call_command("artikelstamm_bereinigen", "--ja")
    cus = Article.objects.get(id=bestand["cus"].id)
    # Die nackte Bestellnummer — genau das, was auf dem Angebot stehen muss.
    assert cus.article_number == "CUS15H"
    # Keine erfundene Herstellernummer mehr, kein Matchcode als Hersteller.
    assert cus.manufacturer_number is None
    assert cus.manufacturer_name is None
    assert cus.matchcode == "CUSSH01510"
    # Die Katalognummer ist nicht verloren, sondern an der Referenz.
    ref = ArticleSupplierReference.objects.get(article_id=cus.id)
    assert ref.supplier_catalog_id == "ZRB2071510"


@pytest.mark.django_db
def test_leitkatalog_gewinnt_die_kollidierende_nummer(bestand):
    call_command("artikelstamm_bereinigen", "--ja")
    bo = Article.objects.get(id=bestand["bo_kollision"].id)
    va = Article.objects.get(id=bestand["va_kollision"].id)
    # B&O ist der Bestellkatalog und behält die nackte Nummer …
    assert bo.article_number == "509010"
    # … der Herstellerkatalog weicht nachvollziehbar aus.
    assert va.article_number == "509010-vaillant"


@pytest.mark.django_db
def test_herstellerkatalog_bekommt_seine_nummer_als_herstellernummer(bestand):
    call_command("artikelstamm_bereinigen", "--ja")
    va = Article.objects.get(id=bestand["va_kollision"].id)
    assert va.manufacturer_number == "509010"
    assert va.manufacturer_name == "Vaillant Deutschland GmbH"


@pytest.mark.django_db
def test_audit_trigger_ist_hinterher_wieder_aktiv(bestand):
    """Der Lauf schaltet den Audit-Trigger ab — er MUSS ihn zurückgeben."""
    call_command("artikelstamm_bereinigen", "--ja")
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT tgname, tgenabled FROM pg_trigger "
            "WHERE tgname IN ('trg_article_audit', 'trg_supplier_ref_audit')"
        )
        zustand = dict(cur.fetchall())
    assert zustand and all(z != "D" for z in zustand.values()), zustand


@pytest.mark.django_db
def test_trockenlauf_rechnet_mit_der_gewuenschten_katalogart(capsys):
    """Die Vorschau muss zeigen, was der scharfe Lauf TUN WIRD.

    Ohne diese Regel zeigte der Trockenlauf die Herstellernummer als „→ None"
    (alte Art GROSSHAENDLER), während `--ja` sie gesetzt hätte — man liest dann
    etwas gegen, das so nie passiert.
    """
    _akteur()
    party = _anbindung("junkers", "Bosch/Junkers", "GROSSHAENDLER")
    _altbestand("junkers", party, "10000946",
                matchcode="EINLEGEBLENDE", katalog_nr="1-000-094-6")
    call_command("artikelstamm_bereinigen", "--anbindungsart", "junkers=HERSTELLER")
    ausgabe = capsys.readouterr().out
    assert "Hersteller-Nr.: '1-000-094-6' → '10000946'" in ausgabe
    assert "'Bosch/Junkers'" in ausgabe


@pytest.mark.django_db
def test_anbindungsart_wird_vor_der_bereinigung_gesetzt():
    """Erst die Katalogart, dann die Felder — die Art bestimmt die Bedeutung."""
    _akteur()
    party = _anbindung("junkers", "Bosch/Junkers", "GROSSHAENDLER")
    artikel = _altbestand("junkers", party, "10000946",
                          matchcode="EINLEGEBLENDE", katalog_nr="1-000-094-6")
    call_command("artikelstamm_bereinigen", "--ja", "--anbindungsart", "junkers=HERSTELLER")
    artikel.refresh_from_db()
    assert artikel.manufacturer_number == "10000946"
    assert artikel.manufacturer_name == "Bosch/Junkers"
    assert artikel.matchcode == "EINLEGEBLENDE"
