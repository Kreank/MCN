"""API-Tests des IDS-Connect Warenkorb-Roundtrips (Punchout-Session + Rückgabe).

Deckt den vollen HTTP-Roundtrip ab: Session starten (WKE/WKS), token-gesicherter
unauthentifizierter Shop-Rückruf, Vorschau der aufgelösten Positionen inkl. Preise,
sowie die Fehlerpfade (unbekanntes/abgelaufenes/doppelt eingelöstes Token, fehlende
Zugangsdaten, unbekannte Session).
"""
import uuid
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.test import Client, override_settings

from db_core.db_context import business_transaction
from db_core.models import (
    AppUser,
    ArticleSupplierReference,
    PunchoutSession,
)
from db_core.services import anbindung as anbindung_service
from db_core.services import artikel as artikel_service
from db_core.services import identity as identity_service

_URL = "/api/pricing/supplier-connections"
_MAIL_KEY = Fernet.generate_key().decode()

_CART = (
    '<Warenkorb xsi:schemaLocation="http://www.itek.de/Shop-Anbindung/Warenkorb/ x.xsd">'
    "<WarenkorbInfo><Version>2.5</Version></WarenkorbInfo><Order>"
    "<OrderItem><ArtNo>4711</ArtNo><Qty>50.00</Qty><QU>MTR</QU>"
    "<NetPrice>522</NetPrice><PriceBasis>1000</PriceBasis><VAT>19.00</VAT>"
    "<Kurztext>Kabelring</Kurztext></OrderItem>"
    "<OrderItem><ArtNo>9999</ArtNo><Qty>1.00</Qty><QU>PCE</QU></OrderItem>"
    "</Order></Warenkorb>"
)


def _seed_actor():
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name="Seed", status="ACTIVE", version=1
    )


def _connection_mit_artikel_und_login():
    """IDS-Anbindung mit Shop-URL, Stammartikel (Ref 4711→A-4711) und Zugangsdaten."""
    actor = _seed_actor()
    supplier = identity_service.create_person(actor.id, first_name="Gross", last_name="Handel")
    conn = anbindung_service.create_connection(
        actor.id, supplier_party_id=supplier.id, source_namespace="gut",
        label="G.U.T.", source_system="IDS_CONNECT", shop_url="https://gut.example/ids",
    )
    art = artikel_service.create_article(
        actor.id, article_number="A-4711", description="Kabelring", unit="m",
    )
    with business_transaction(actor.id):
        ArticleSupplierReference.objects.create(
            id=uuid.uuid4(), article_id=art.id, supplier_party_id=supplier.id,
            source_system="DATANORM", source_namespace="gut",
            supplier_article_number="4711", valid_from="2020-01-01",
        )
    anbindung_service.set_credentials(
        actor.id, connection_id=conn.id, username="hw1",
        customer_number="4711", password="geheim",
    )
    return conn, art


def _token_aus_hookurl(hookurl: str) -> str:
    return hookurl.rstrip("/").rsplit("/", 1)[-1]


# --- Start + voller Roundtrip ----------------------------------------------

@override_settings(MCN_MAIL_KEY=_MAIL_KEY)
@pytest.mark.django_db
def test_roundtrip_wke_bis_uebernahme(admin_client, anonymous_client):
    conn, _art = _connection_mit_artikel_und_login()

    # 1. Session starten (WKE)
    r = admin_client.post(
        f"{_URL}/{conn.id}/punchout-session",
        data={"action": "WKE"}, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    start = r.json()
    session_id = start["session_id"]
    fields = start["punchout"]["fields"]
    assert fields["action"] == "WKE"
    assert fields["name_kunde"] == "hw1" and fields["pw_kunde"] == "geheim"
    hookurl = fields["hookurl"]
    assert "/api/pricing/warenkorb-return/" in hookurl
    token = _token_aus_hookurl(hookurl)

    # 2. Vor Rückgabe: Session ist OFFEN, keine Positionen
    vor = admin_client.get(f"/api/pricing/punchout-sessions/{session_id}").json()
    assert vor["status"] == "OFFEN" and vor["total"] == 0

    # 3. Shop meldet den Warenkorb zurück (UNAUTHENTIFIZIERT, Token in der URL)
    ret = anonymous_client.post(
        f"/api/pricing/warenkorb-return/{token}",
        data=_CART, content_type="application/xml",
    )
    assert ret.status_code == 200, ret.content
    assert "Warenkorb empfangen" in ret.content.decode("utf-8")

    # 4. Vorschau: eingelöst, Positionen aufgelöst inkl. Preis
    nach = admin_client.get(f"/api/pricing/punchout-sessions/{session_id}").json()
    assert nach["status"] == "EINGELOEST"
    assert nach["total"] == 2 and nach["matched"] == 1
    treffer = next(p for p in nach["positions"] if p["art_no"] == "4711")
    assert treffer["matched"] and treffer["article_number"] == "A-4711"
    assert treffer["net_price"] == "0.5220"      # 522 / PriceBasis 1000
    assert treffer["vat"] == "19.00"
    fehl = next(p for p in nach["positions"] if p["art_no"] == "9999")
    assert not fehl["matched"]


# Echtes G.U.T.-OrderItem (GC-Quirk): NetPrice 35,30 ist die Positionssumme für
# 5 m, nicht der Preis je Meter. Unter GESAMT-Semantik ergibt sich 35,30/5 = 7,06.
_CART_GESAMT = (
    "<Warenkorb><Order>"
    "<OrderItem><ArtNo>4711</ArtNo><Qty>5.000</Qty><QU>MTR</QU>"
    "<OfferPrice>12.83</OfferPrice><NetPrice>35.30</NetPrice><PriceBasis>1.0</PriceBasis>"
    "<VAT>19.00</VAT><Kurztext>Kupferrohr</Kurztext></OrderItem>"
    "</Order></Warenkorb>"
)


@override_settings(MCN_MAIL_KEY=_MAIL_KEY)
@pytest.mark.django_db
def test_roundtrip_gesamt_semantik_teilt_durch_menge(admin_client, anonymous_client):
    """GC-Quirk end-to-end: eine GESAMT-Anbindung teilt den NetPrice durch die Menge,
    der EK je Einheit (und der daraus abgeleitete VK) sind NICHT mehr ×Menge zu hoch."""
    conn, _art = _connection_mit_artikel_und_login()
    anbindung_service.update_connection(
        _seed_actor().id, connection_id=conn.id, net_price_semantics="GESAMT",
    )
    r = admin_client.post(
        f"{_URL}/{conn.id}/punchout-session",
        data={"action": "WKE"}, content_type="application/json",
    )
    token = _token_aus_hookurl(r.json()["punchout"]["fields"]["hookurl"])
    session_id = r.json()["session_id"]
    anonymous_client.post(
        f"/api/pricing/warenkorb-return/{token}",
        data=_CART_GESAMT, content_type="application/xml",
    )
    nach = admin_client.get(f"/api/pricing/punchout-sessions/{session_id}").json()
    treffer = next(p for p in nach["positions"] if p["art_no"] == "4711")
    assert treffer["net_price"] == "7.0600"      # 35,30 / 5, NICHT 35,30
    assert treffer["preis_hinweis"] is None       # Summensemantik gewählt → kein Hinweis


@override_settings(MCN_MAIL_KEY=_MAIL_KEY)
@pytest.mark.django_db
def test_roundtrip_einheit_default_warnt_bei_summensemantik(admin_client, anonymous_client):
    """Default EINHEIT bei einem GC-Warenkorb: EK bleibt (bewusst) ×Menge, aber die
    Vorschau warnt sichtbar an der Position, dass der EK wie eine Positionssumme wirkt."""
    conn, _art = _connection_mit_artikel_und_login()  # Default EINHEIT
    r = admin_client.post(
        f"{_URL}/{conn.id}/punchout-session",
        data={"action": "WKE"}, content_type="application/json",
    )
    token = _token_aus_hookurl(r.json()["punchout"]["fields"]["hookurl"])
    session_id = r.json()["session_id"]
    anonymous_client.post(
        f"/api/pricing/warenkorb-return/{token}",
        data=_CART_GESAMT, content_type="application/xml",
    )
    nach = admin_client.get(f"/api/pricing/punchout-sessions/{session_id}").json()
    treffer = next(p for p in nach["positions"] if p["art_no"] == "4711")
    assert treffer["net_price"] == "35.3000"
    assert treffer["preis_hinweis"] and "Positionssumme" in treffer["preis_hinweis"]


@override_settings(MCN_MAIL_KEY=_MAIL_KEY)
@pytest.mark.django_db
def test_start_wks_uebergibt_warenkorb(admin_client):
    conn, _art = _connection_mit_artikel_und_login()
    r = admin_client.post(
        f"{_URL}/{conn.id}/punchout-session",
        data={"action": "WKS", "positions": [
            {"art_no": "4711", "qty": "5", "unit": "MTR"},
        ]},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    fields = r.json()["punchout"]["fields"]
    assert fields["action"] == "WKS"
    assert "warenkorb" in fields
    assert "<ArtNo>4711</ArtNo>" in fields["warenkorb"]


@override_settings(MCN_MAIL_KEY=_MAIL_KEY)
@pytest.mark.django_db
def test_rueckgabe_per_formularfeld(admin_client, anonymous_client):
    conn, _art = _connection_mit_artikel_und_login()
    r = admin_client.post(
        f"{_URL}/{conn.id}/punchout-session",
        data={"action": "WKE"}, content_type="application/json",
    )
    token = _token_aus_hookurl(r.json()["punchout"]["fields"]["hookurl"])
    # Multipart-Formularfeld statt Roh-Body (wie manche Shops senden).
    ret = anonymous_client.post(
        f"/api/pricing/warenkorb-return/{token}", data={"warenkorb": _CART},
    )
    assert ret.status_code == 200, ret.content
    session_id = r.json()["session_id"]
    assert admin_client.get(
        f"/api/pricing/punchout-sessions/{session_id}"
    ).json()["status"] == "EINGELOEST"


# --- Fehlerpfade -----------------------------------------------------------

@pytest.mark.django_db
def test_rueckgabe_unbekanntes_token_422(anonymous_client):
    ret = anonymous_client.post(
        "/api/pricing/warenkorb-return/gibtsnicht", data=_CART,
        content_type="application/xml",
    )
    assert ret.status_code == 422


@override_settings(MCN_MAIL_KEY=_MAIL_KEY)
@pytest.mark.django_db
def test_rueckgabe_doppelt_422(admin_client, anonymous_client):
    conn, _art = _connection_mit_artikel_und_login()
    r = admin_client.post(
        f"{_URL}/{conn.id}/punchout-session",
        data={"action": "WKE"}, content_type="application/json",
    )
    token = _token_aus_hookurl(r.json()["punchout"]["fields"]["hookurl"])
    assert anonymous_client.post(
        f"/api/pricing/warenkorb-return/{token}", data=_CART,
        content_type="application/xml",
    ).status_code == 200
    # Zweite Einlösung wird abgewiesen (Replay-Schutz).
    assert anonymous_client.post(
        f"/api/pricing/warenkorb-return/{token}", data=_CART,
        content_type="application/xml",
    ).status_code == 422


@pytest.mark.django_db
def test_rueckgabe_abgelaufen_422(anonymous_client):
    # Eine bereits abgelaufene Session direkt einfügen (INSERT ist trigger-frei; die
    # expires_at-Freeze im Trigger greift nur bei UPDATE). Der Rückruf muss sie mit
    # 422 abweisen.
    import hashlib

    from db_core.services import punchout_session as ps

    actor = _seed_actor()
    supplier = identity_service.create_person(actor.id, first_name="Ab", last_name="Gelaufen")
    conn = anbindung_service.create_connection(
        actor.id, supplier_party_id=supplier.id, source_namespace="abl",
        label="Abl", source_system="IDS_CONNECT", shop_url="https://x/ids",
    )
    token = "abgelaufen-xyz"
    with business_transaction(actor.id):
        PunchoutSession.objects.create(
            id=uuid.uuid4(), connection_id=conn.id, quote_id=None,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            action="WKE", status="OFFEN", created_by_id=actor.id,
            expires_at=ps._now() - timedelta(minutes=1), version=1,
        )
    assert anonymous_client.post(
        f"/api/pricing/warenkorb-return/{token}", data=_CART,
        content_type="application/xml",
    ).status_code == 422


@override_settings(MCN_MAIL_KEY=_MAIL_KEY)
@pytest.mark.django_db
def test_start_ohne_zugangsdaten_422(admin_client):
    actor = _seed_actor()
    supplier = identity_service.create_person(actor.id, first_name="Ohne", last_name="Login")
    conn = anbindung_service.create_connection(
        actor.id, supplier_party_id=supplier.id, source_namespace="ohne",
        label="Ohne", source_system="IDS_CONNECT", shop_url="https://x/ids",
    )
    r = admin_client.post(
        f"{_URL}/{conn.id}/punchout-session",
        data={"action": "WKE"}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_unbekannte_session_404(admin_client):
    assert admin_client.get(
        f"/api/pricing/punchout-sessions/{uuid.uuid4()}"
    ).status_code == 404


@pytest.mark.django_db
def test_start_ohne_recht_403(db):
    from .conftest import make_role_user
    user, _ = make_role_user(None)
    c = Client()
    c.force_login(user)
    r = c.post(
        f"{_URL}/{uuid.uuid4()}/punchout-session",
        data={"action": "WKE"}, content_type="application/json",
    )
    assert r.status_code == 403
