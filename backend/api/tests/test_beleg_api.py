"""API-Tests der Beleg-Endpoints (Angebote) über den Django-Test-Client."""
import uuid

import pytest

from django.contrib.auth import get_user_model

from db_core.models import AppUser, Quote
from db_core.services import abrechnung as abrechnung_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service

User = get_user_model()


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Angebotsobjekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Dachreparatur",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
             "unit_price": 3, "tax_code": "DE_19"},
            {"line_type": "ARBEITSZEIT", "description": "Arbeit", "quantity": 2,
             "unit_price": 45, "tax_code": "DE_19"},
        ],
    )
    return {"app_user": app_user, "obj": obj, "quote": q}


def _logged_in_client(client, *, with_app_user=True):
    user = User.objects.create_user(username=f"u{uuid.uuid4().hex[:8]}", password="x")
    if with_app_user:
        from .conftest import grant_role
        au = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Login", status="ACTIVE", version=1
        )
        user.app_user_id = au.id
        user.save()
        grant_role(au.id, "ADMINISTRATION")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_liste(admin_client, seeded):
    r = admin_client.get("/api/invoicing/quotes")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["title"] == "Dachreparatur"
    assert item["status"] == "ENTWURF"
    assert item["quote_number"] is None
    assert item["property"]["city"] == "Berlin"


@pytest.mark.django_db
def test_liste_property_filter(admin_client, seeded):
    r = admin_client.get(f"/api/invoicing/quotes?property_id={seeded['obj'].id}")
    assert r.json()["total"] == 1
    r2 = admin_client.get(f"/api/invoicing/quotes?property_id={uuid.uuid4()}")
    assert r2.json()["total"] == 0


@pytest.mark.django_db
def test_detail_mit_positionen(admin_client, seeded):
    r = admin_client.get(f"/api/invoicing/quotes/{seeded['quote'].id}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["lines"]) == 2
    assert body["lines"][0]["position_number"] == 1
    assert body["lines"][0]["description"] == "Ziegel"
    # 10*3=30, 2*45=90 -> net 120
    assert body["net_total"] == "120.00"


@pytest.mark.django_db
def test_detail_404(admin_client, seeded):
    r = admin_client.get(f"/api/invoicing/quotes/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_create_eingeloggt(client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/quotes",
        data={
            "property_id": str(obj.id),
            "title": "Neues Angebot",
            "lines": [
                {"line_type": "MATERIAL", "description": "Pos", "quantity": 1,
                 "unit_price": 100, "tax_code": "DE_19"}
            ],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["title"] == "Neues Angebot"
    assert body["status"] == "ENTWURF"
    assert body["net_total"] == "100.00"
    assert Quote.objects.filter(id=body["id"]).exists()


@pytest.mark.django_db
def test_create_ungueltige_position_422(client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/quotes",
        data={
            "property_id": str(obj.id), "title": "X",
            "lines": [{"line_type": "MATERIAL", "description": "kein Preis"}],
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_ohne_login_abgelehnt(anonymous_client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    r = anonymous_client.post(
        "/api/invoicing/quotes",
        data={"property_id": str(obj.id), "title": "Anon", "lines": []},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_kopie_dupliziert_kopf_und_positionen(client, db, app_user, seeded):
    """POST /quotes/{id}/kopie erzeugt einen NEUEN Entwurf mit „(Kopie)"-Titel und
    wertgleich kopierten Positionen — die Quelle bleibt unberührt."""
    c = _logged_in_client(client, with_app_user=True)
    quelle = seeded["quote"]
    r = c.post(
        f"/api/invoicing/quotes/{quelle.id}/kopie",
        data={}, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["id"] != str(quelle.id)
    assert body["title"] == "Dachreparatur (Kopie)"
    assert body["status"] == "ENTWURF"
    assert body["quote_number"] is None
    assert len(body["lines"]) == 2
    assert body["net_total"] == "120.00"
    # Quelle unverändert (kein zweiter Titel, kein Statuswechsel).
    quelle.refresh_from_db()
    assert quelle.title == "Dachreparatur"
    assert Quote.objects.count() == 2


@pytest.mark.django_db
def test_kopie_in_anderes_projekt(client, db, app_user, seeded):
    """Zielprojekt der Kopie ist wählbar (Default sonst = Quelle)."""
    c = _logged_in_client(client, with_app_user=True)
    projekt = projekt_service.create_project(
        app_user.id, name="Zielprojekt", property_ids=[seeded["obj"].id]
    )
    r = c.post(
        f"/api/invoicing/quotes/{seeded['quote'].id}/kopie",
        data={"project_id": str(projekt.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["project"]["id"] == str(projekt.id)


@pytest.mark.django_db
def test_kopie_404_unbekannt(client, db, app_user):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        f"/api/invoicing/quotes/{uuid.uuid4()}/kopie",
        data={}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_verschieben_setzt_projekt(client, db, app_user, seeded):
    """PUT /quotes/{id} mit project_id verschiebt einen Entwurf in ein Projekt."""
    c = _logged_in_client(client, with_app_user=True)
    projekt = projekt_service.create_project(
        app_user.id, name="Neues Projekt", property_ids=[seeded["obj"].id]
    )
    r = c.put(
        f"/api/invoicing/quotes/{seeded['quote'].id}",
        data={"project_id": str(projekt.id)},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["project"]["id"] == str(projekt.id)
    seeded["quote"].refresh_from_db()
    assert str(seeded["quote"].project_id) == str(projekt.id)


@pytest.mark.django_db
def test_rechnung_liste_und_detail(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="RE-Objekt", property_type="WEG",
        street="W", house_number="2", postal_code="10115", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Pos", "quantity": 2,
                "unit_price": 50, "tax_code": "DE_19"}],
    )
    r = admin_client.get("/api/invoicing/invoices")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["invoice_type"] == "RECHNUNG"
    assert r.json()["items"][0]["invoice_number"] is None

    d = admin_client.get(f"/api/invoicing/invoices/{inv.id}")
    assert d.status_code == 200
    body = d.json()
    assert len(body["lines"]) == 1
    assert body["net_total"] == "100.00"


@pytest.mark.django_db
def test_rechnung_detail_404(admin_client, db):
    r = admin_client.get(f"/api/invoicing/invoices/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_rechnung_create_eingeloggt(client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/invoices",
        data={
            "property_id": str(obj.id), "invoice_type": "RECHNUNG",
            "lines": [{"line_type": "MATERIAL", "description": "P", "quantity": 1,
                       "unit_price": 100, "tax_code": "DE_19"}],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["gross_total"] == "119.00"


@pytest.mark.django_db
def test_rechnung_create_ohne_login(anonymous_client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    r = anonymous_client.post(
        "/api/invoicing/invoices",
        data={"property_id": str(obj.id), "invoice_type": "RECHNUNG", "lines": []},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


# --- Anschreiben-Freitext am Angebot (Hero-Angleichung Dokumente-9) ---------

@pytest.mark.django_db
def test_cover_letter_setzen_auslesen_und_freeze(client, db, app_user, seeded):
    """Anschreiben im Entwurf setzbar/auslesbar, nach Versand eingefroren (B-30)."""
    c = _logged_in_client(client, with_app_user=True)
    qid = seeded["quote"].id

    # Entwurf: noch kein Anschreiben.
    assert c.get(f"/api/invoicing/quotes/{qid}").json()["cover_letter"] is None

    # Setzen (PUT-Kopf, wird getrimmt) — Positionen unangetastet.
    r = c.put(
        f"/api/invoicing/quotes/{qid}",
        data={"cover_letter": "  Sehr geehrte Damen und Herren, anbei unser Angebot.  "},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["cover_letter"] == "Sehr geehrte Damen und Herren, anbei unser Angebot."
    # Persistent im Detail.
    assert (
        c.get(f"/api/invoicing/quotes/{qid}").json()["cover_letter"]
        == "Sehr geehrte Damen und Herren, anbei unser Angebot."
    )

    # Versenden — ab jetzt friert die DB den Beleginhalt ein.
    rs = c.post(f"/api/invoicing/quotes/{qid}/send", content_type="application/json")
    assert rs.status_code == 200, rs.content
    assert rs.json()["status"] == "VERSENDET"

    # Anschreiben am versendeten Angebot ändern → 422 (Beleginhalt eingefroren).
    r2 = c.put(
        f"/api/invoicing/quotes/{qid}",
        data={"cover_letter": "Nachtraeglich geaendert"},
        content_type="application/json",
    )
    assert r2.status_code == 422
    # Der ursprüngliche Text bleibt erhalten.
    assert (
        c.get(f"/api/invoicing/quotes/{qid}").json()["cover_letter"]
        == "Sehr geehrte Damen und Herren, anbei unser Angebot."
    )


# --- Vorgangsbezug am Beleg (Migration 0113) --------------------------------

def _obj(app_user, *, name="Vorgangsobjekt", city="Berlin"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city=city,
    )


_LINE = {"line_type": "MATERIAL", "description": "Pos", "quantity": 1,
         "unit_price": 100, "tax_code": "DE_19"}


@pytest.mark.django_db
def test_create_quote_mit_vorgang(client, db, app_user):
    """Angebot mit service_case_id: der Vorgang steht in der Antwort."""
    obj = _obj(app_user)
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Heizung defekt",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/quotes",
        data={"property_id": str(obj.id), "title": "A", "service_case_id": str(case.id),
              "lines": [_LINE]},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["service_case_id"] == str(case.id)


@pytest.mark.django_db
def test_create_quote_vorgang_fremde_liegenschaft_422(client, db, app_user):
    """Vorgang einer ANDEREN Liegenschaft ist kein zulässiger Bezug (422)."""
    obj1 = _obj(app_user, name="Obj1")
    obj2 = _obj(app_user, name="Obj2")
    case2 = projekt_service.create_service_case(
        app_user.id, property_id=obj2.id, subject="X",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/quotes",
        data={"property_id": str(obj1.id), "title": "A",
              "service_case_id": str(case2.id), "lines": [_LINE]},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Liegenschaft" in r.json()["detail"]


@pytest.mark.django_db
def test_create_quote_erbt_vorgang_vom_auftrag(app_user):
    """Ohne service_case_id, aber mit Auftrag an einem Vorgang: Vorgang wird geerbt."""
    obj = _obj(app_user)
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Y",
    )
    wo = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag", service_case_id=case.id,
    )
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="A", work_order_id=wo.id, lines=[_LINE],
    )
    assert str(q.service_case_id) == str(case.id)


@pytest.mark.django_db
def test_create_quote_auftrag_vorgang_mismatch_422(app_user):
    """Auftrag und übergebener Vorgang passen nicht zusammen → Fachfehler."""
    obj = _obj(app_user)
    case1 = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="C1",
    )
    case2 = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="C2",
    )
    wo = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag", service_case_id=case1.id,
    )
    with pytest.raises(ValueError, match="anderen Vorgang"):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="A",
            work_order_id=wo.id, service_case_id=case2.id, lines=[_LINE],
        )


@pytest.mark.django_db
def test_create_quote_erbt_projekt_vom_vorgang(app_user):
    """Hat der Vorgang ein Projekt und der Aufruf keins, wird es geerbt."""
    obj = _obj(app_user)
    projekt = projekt_service.create_project(
        app_user.id, name="P", property_ids=[obj.id],
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Z", project_id=projekt.id,
    )
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="A", service_case_id=case.id, lines=[_LINE],
    )
    assert str(q.project_id) == str(projekt.id)


@pytest.mark.django_db
def test_create_quote_abweichendes_projekt_422(app_user):
    """Vorgang trägt Projekt A, Aufruf übergibt Projekt B → Fachfehler."""
    obj = _obj(app_user)
    projekt_a = projekt_service.create_project(
        app_user.id, name="A", property_ids=[obj.id],
    )
    projekt_b = projekt_service.create_project(
        app_user.id, name="B", property_ids=[obj.id],
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Z", project_id=projekt_a.id,
    )
    with pytest.raises(ValueError, match="anderes Projekt"):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="A",
            service_case_id=case.id, project_id=projekt_b.id, lines=[_LINE],
        )


@pytest.mark.django_db
def test_filter_quotes_nach_vorgang_direkt_und_via_auftrag(admin_client, app_user):
    """?service_case_id findet Belege DIREKT am Vorgang UND an dessen Aufträgen."""
    obj = _obj(app_user)
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Filter",
    )
    wo = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag", service_case_id=case.id,
    )
    # direkt am Vorgang
    q_direkt = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="direkt",
        service_case_id=case.id, lines=[_LINE],
    )
    # über den Auftrag (erbt den Vorgang)
    q_via_auftrag = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="via", work_order_id=wo.id, lines=[_LINE],
    )
    # unbeteiligtes Angebot
    beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="fremd", lines=[_LINE],
    )
    r = admin_client.get(f"/api/invoicing/quotes?service_case_id={case.id}")
    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {str(q_direkt.id), str(q_via_auftrag.id)}


@pytest.mark.django_db
def test_folgebeleg_erbt_vorgang(app_user):
    """rechnung_aus_angebot übernimmt den Vorgang vom Quell-Angebot."""
    obj = _obj(app_user)
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Folgebeleg",
    )
    wo = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag", service_case_id=case.id,
    )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="A", work_order_id=wo.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": 2,
                "unit_price": 10, "tax_code": "DE_19"}],
    )
    assert str(quote.service_case_id) == str(case.id)
    beleg_service.send_quote(app_user.id, quote_id=quote.id)
    invoice = abrechnung_service.rechnung_aus_angebot(app_user.id, quote_id=quote.id)
    assert str(invoice.service_case_id) == str(case.id)


@pytest.mark.django_db
def test_create_invoice_mit_vorgang(client, db, app_user):
    """Rechnung mit service_case_id: der Vorgang steht in der Antwort."""
    obj = _obj(app_user)
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Rechnung",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/invoices",
        data={"property_id": str(obj.id), "invoice_type": "RECHNUNG",
              "service_case_id": str(case.id), "lines": [_LINE]},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["service_case_id"] == str(case.id)


@pytest.mark.django_db
def test_filter_invoices_nach_vorgang(admin_client, app_user):
    """?service_case_id filtert Rechnungen direkt am Vorgang UND via Auftrag."""
    obj = _obj(app_user)
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Filter-RE",
    )
    wo = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag", service_case_id=case.id,
    )
    inv_direkt = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, service_case_id=case.id, lines=[_LINE],
    )
    inv_via = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, work_order_id=wo.id, lines=[_LINE],
    )
    beleg_service.create_invoice(app_user.id, property_id=obj.id, lines=[_LINE])
    r = admin_client.get(f"/api/invoicing/invoices?service_case_id={case.id}")
    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {str(inv_direkt.id), str(inv_via.id)}
