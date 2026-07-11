"""Tests der IDS-Connect-Warenkorb-Logik (Parsen, Mapping, Ausgangs-Warenkorb).

Die XML-Fixtures sind den mitgelieferten IDS-Beispieldateien (v2.5)
nachgebildet — einmal OHNE deklarierten Namespace (wie das reale Beispiel) und
einmal MIT, um die Namespace-Toleranz zu sichern.
"""
from decimal import Decimal

import pytest

from db_core.db_context import business_transaction
from db_core.models import ArticleSupplierReference
from db_core.services import artikel as artikel_service
from db_core.services import identity as identity_service
from db_core.services import ids_warenkorb as ids

# Rückgabe-Warenkorb OHNE Namespace-Deklaration (wie das gelieferte Beispiel).
CART_OHNE_NS = """<Warenkorb xsi:schemaLocation="http://www.itek.de/Shop-Anbindung/Warenkorb/ warenkorb_empfangen_2_5.xsd">
<WarenkorbInfo><Date>2009-10-29</Date><Time>17:00:29</Time>
<RueckgabeKZ>Warenkorbrückgabe</RueckgabeKZ><Version>2.5</Version></WarenkorbInfo>
<Order>
<OrderItem><ArtNo>4711</ArtNo><Qty>50.00</Qty><QU>MTR</QU></OrderItem>
<OrderItem><ArtNo>4712</ArtNo><Qty>3.00</Qty><QU>PCE</QU><Kurztext>Winkel</Kurztext></OrderItem>
</Order></Warenkorb>"""

# Derselbe Warenkorb MIT deklariertem IDS-Namespace.
CART_MIT_NS = """<Warenkorb xmlns="http://www.itek.de/Shop-Anbindung/Warenkorb/">
<WarenkorbInfo><Date>2009-10-29</Date><Time>17:00:29</Time><Version>2.5</Version></WarenkorbInfo>
<Order><OrderItem><ArtNo>4711</ArtNo><Qty>50.00</Qty><QU>MTR</QU></OrderItem></Order>
</Warenkorb>"""


# --- Parsen -----------------------------------------------------------------

def test_parse_ohne_namespace():
    pos = ids.parse_returned_cart(CART_OHNE_NS)
    assert [p.art_no for p in pos] == ["4711", "4712"]
    assert pos[0].qty == Decimal("50.00")
    assert pos[0].unit == "MTR"
    assert pos[1].short_text == "Winkel"


def test_parse_mit_namespace():
    pos = ids.parse_returned_cart(CART_MIT_NS)
    assert len(pos) == 1
    assert pos[0].art_no == "4711"
    assert pos[0].qty == Decimal("50.00")


def test_parse_bytes_und_str_gleich():
    assert ids.parse_returned_cart(CART_MIT_NS.encode("utf-8"))[0].art_no == "4711"


def test_parse_leerer_warenkorb():
    xml = '<Warenkorb><Order></Order></Warenkorb>'
    assert ids.parse_returned_cart(xml) == []


def test_parse_position_ohne_artno_uebersprungen():
    xml = ("<Warenkorb><Order>"
           "<OrderItem><Qty>1.00</Qty><QU>PCE</QU></OrderItem>"
           "<OrderItem><ArtNo>9000</ArtNo><Qty>2.00</Qty></OrderItem>"
           "</Order></Warenkorb>")
    pos = ids.parse_returned_cart(xml)
    assert [p.art_no for p in pos] == ["9000"]


def test_parse_ungueltiges_xml_scheitert():
    with pytest.raises(ids.WarenkorbError):
        ids.parse_returned_cart("<Warenkorb><Order>")


def test_parse_falsches_wurzelelement_scheitert():
    with pytest.raises(ids.WarenkorbError):
        ids.parse_returned_cart("<Etwas></Etwas>")


def test_parse_ungueltige_menge_scheitert():
    xml = "<Warenkorb><Order><OrderItem><ArtNo>1</ArtNo><Qty>viel</Qty></OrderItem></Order></Warenkorb>"
    with pytest.raises(ids.WarenkorbError):
        ids.parse_returned_cart(xml)


def test_parse_entity_expansion_abgelehnt():
    """„Billion Laughs": interne Entity-Expansion muss abgelehnt werden (defusedxml),
    nicht expandiert werden — sonst DoS über einen bösartigen Shop-Warenkorb."""
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE Warenkorb [<!ENTITY a "AAAAAAAAAA">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<Warenkorb><Order><OrderItem><ArtNo>&b;</ArtNo><Qty>1</Qty></OrderItem></Order></Warenkorb>'
    )
    with pytest.raises(ids.WarenkorbError):
        ids.parse_returned_cart(bomb)


def test_parse_negative_menge_scheitert():
    xml = "<Warenkorb><Order><OrderItem><ArtNo>1</ArtNo><Qty>-5.00</Qty></OrderItem></Order></Warenkorb>"
    with pytest.raises(ids.WarenkorbError):
        ids.parse_returned_cart(xml)


def test_parse_fehlende_menge_scheitert():
    xml = "<Warenkorb><Order><OrderItem><ArtNo>1</ArtNo><QU>PCE</QU></OrderItem></Order></Warenkorb>"
    with pytest.raises(ids.WarenkorbError):
        ids.parse_returned_cart(xml)


def test_parse_latin1_umlaut_mit_unbound_prefix():
    """latin-1-Kurztext (deklariert) UND unbound prefix (xsi): der Reparaturpfad
    muss die Bytes verlustfrei erhalten — der Umlaut darf nicht verstümmelt werden
    (Regression gegen utf-8/„replace")."""
    xml = (
        '<?xml version="1.0" encoding="iso-8859-1"?>'
        '<Warenkorb xsi:schemaLocation="http://x y.xsd"><Order>'
        '<OrderItem><ArtNo>1</ArtNo><Qty>1.00</Qty><Kurztext>Gr\xf6\xdfe</Kurztext></OrderItem>'
        '</Order></Warenkorb>'
    ).encode("latin-1")
    pos = ids.parse_returned_cart(xml)
    assert pos[0].short_text == "Größe"


def test_parse_dtd_abgelehnt():
    """Ein Warenkorb mit DTD wird abgelehnt (forbid_dtd)."""
    xml = ('<?xml version="1.0"?><!DOCTYPE Warenkorb SYSTEM "http://evil.example/x.dtd">'
           '<Warenkorb><Order></Order></Warenkorb>')
    with pytest.raises(ids.WarenkorbError):
        ids.parse_returned_cart(xml)


# --- Ausgangs-Warenkorb bauen + Round-Trip ----------------------------------

def test_build_cart_roundtrip():
    positions = [ids.CartPosition(art_no="4711", qty=Decimal("50"), unit="MTR")]
    xml = ids.build_cart_xml(positions)
    assert isinstance(xml, bytes)
    zurueck = ids.parse_returned_cart(xml)
    assert zurueck[0].art_no == "4711"
    assert zurueck[0].qty == Decimal("50.00")
    assert zurueck[0].unit == "MTR"


# --- Mapping auf den Artikelstamm -------------------------------------------

def _artikel_mit_ref(app_user, *, article_number, namespace, art_no,
                     source_system="DATANORM"):
    art = artikel_service.create_article(
        app_user.id, article_number=article_number,
        description=f"Artikel {article_number}", unit="Stk",
    )
    supplier = identity_service.create_person(
        app_user.id, first_name="Liefer", last_name=article_number
    )
    with business_transaction(app_user.id):
        import uuid
        ArticleSupplierReference.objects.create(
            id=uuid.uuid4(), article_id=art.id, supplier_party_id=supplier.id,
            source_system=source_system, source_namespace=namespace,
            supplier_article_number=art_no, valid_from="2020-01-01",
        )
    return art


@pytest.mark.django_db
def test_resolve_treffer_und_fehltreffer(app_user):
    _artikel_mit_ref(app_user, article_number="A-100", namespace="gut", art_no="4711")
    positions = [
        ids.CartPosition(art_no="4711", qty=Decimal("50"), unit="MTR"),
        ids.CartPosition(art_no="9999", qty=Decimal("1"), unit="PCE"),
    ]
    res = ids.resolve_positions("gut", positions)
    assert res[0].matched and not res[0].ambiguous
    assert res[0].article_number == "A-100"
    assert res[0].qty == Decimal("50")
    assert not res[1].matched and not res[1].ambiguous
    assert res[1].article_id is None


@pytest.mark.django_db
def test_resolve_namespace_trennt(app_user):
    """Dieselbe ArtNo unter einem anderen Namespace darf NICHT matchen."""
    _artikel_mit_ref(app_user, article_number="A-200", namespace="reisser", art_no="4711")
    res = ids.resolve_positions("gut", [ids.CartPosition(art_no="4711", qty=Decimal("1"))])
    assert not res[0].matched


@pytest.mark.django_db
def test_resolve_mehrdeutig(app_user):
    """Dieselbe (Namespace, ArtNo) unter ZWEI Quellsystemen (DATANORM + IDS_CONNECT)
    auf verschiedene Artikel → ambiguous. Die DB-EXCLUDE-Schranke erlaubt das, weil
    sie source_system einschließt; das Mapping ignoriert source_system bewusst."""
    _artikel_mit_ref(app_user, article_number="A-300", namespace="gut",
                     art_no="5000", source_system="DATANORM")
    _artikel_mit_ref(app_user, article_number="A-301", namespace="gut",
                     art_no="5000", source_system="IDS_CONNECT")
    res = ids.resolve_positions("gut", [ids.CartPosition(art_no="5000", qty=Decimal("1"))])
    assert res[0].ambiguous and not res[0].matched
    assert res[0].article_id is None
