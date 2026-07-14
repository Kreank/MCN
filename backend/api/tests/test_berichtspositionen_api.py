"""API der Berichtspositionen und des Soll-Ist-Abgleichs (Migration 0080).

* `PUT  /workflow/site_reports/{id}/positionen` — Positionssatz ersetzen (ENTWURF)
* `POST /workflow/site_reports/{id}/vorbelegen` — Soll aus einem Angebot übernehmen
* `GET  /workflow/site_reports/{id}` — liefert die Positionen mit
* `GET  /workflow/work_orders/{id}/soll-ist` — der Abgleich (Dispositionssicht)

Die Rechte-Tore spiegeln exakt das Bestandsmuster der Datei `api/site_report.py`:
der Monteur (row_scope EIGENE) arbeitet am **eigenen** Bericht; ein fremder Bericht
antwortet mit **404** (nicht 403 — sonst verriete die Antwort seine Existenz). Die
Auftragssicht (und damit der Soll-Ist) ist nicht auf eigene Zeilen begrenzbar:
Scope EIGENE → **403**, fail-closed.
"""
import uuid
from decimal import Decimal

import pytest
from django.test import Client

from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import einsatz as einsatz_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service

from .conftest import make_app_user, make_role_user

JSON = "application/json"


def _client(role="ADMINISTRATION"):
    user, app_user = make_role_user(role)
    c = Client()
    c.force_login(user)
    return c, app_user


def _property(actor_id, name="Baustelle"):
    return property_service.create_property(
        actor_id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )


def _auftrag(actor_id, titel="Bad sanieren"):
    obj = _property(actor_id, name=titel)
    return auftrag_service.create_work_order(
        actor_id, property_id=obj.id, title=titel
    )


def _bericht(actor_id, auftrag):
    return report_service.create_report(
        actor_id, work_order_id=auftrag.id, report_date="2026-07-13",
        activity_text="Fliesen verlegt.",
    )


def _mat(desc, qty, *, unit="m", kind="NORMAL"):
    return {
        "line_type": "MATERIAL", "description": desc, "quantity": qty,
        "unit": unit, "unit_price": "10.00", "tax_code": "DE_19",
        "line_kind": kind,
    }


def _angebot(actor_id, auftrag, lines, *, versenden=True):
    """Angebot, dem Auftrag zugeordnet — über den Produktweg (`work_order_id`).

    Versendet wird per Default: ein Angebot im ENTWURF ist keine Vereinbarung und
    bildet kein Soll.
    """
    quote = beleg_service.create_quote(
        actor_id, property_id=auftrag.property_id, title="Angebot",
        work_order_id=auftrag.id, lines=lines,
    )
    if versenden:
        beleg_service.send_quote(actor_id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote


# --- Positionen setzen ------------------------------------------------------

@pytest.mark.django_db
def test_positionen_setzen_und_im_detail_wiederfinden():
    c, actor = _client()
    auftrag = _auftrag(actor.id)
    bericht = _bericht(actor.id, auftrag)
    artikel = artikel_service.create_article(
        actor.id, article_number="A-1", description="Rohr DN20", unit="m"
    )

    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [
            {"line_type": "MATERIAL", "source_article_id": str(artikel.id),
             "quantity": "12.5"},
            {"line_type": "TEXT", "description": "Kunde war anwesend."},
        ]},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["total"] == 2
    assert body["items"][0]["description"] == "Rohr DN20"   # Kopie aus dem Stamm
    assert body["items"][0]["unit"] == "m"
    assert body["items"][0]["quantity"] == "12.500"
    # INVARIANTE: keine Preisfelder im Ausgabeschema.
    assert not (set(body["items"][0]) & {"unit_price", "net_amount", "tax_code"})

    detail = c.get(f"/api/workflow/site_reports/{bericht.id}").json()
    assert len(detail["lines"]) == 2
    assert detail["lines"][1]["line_type"] == "TEXT"


@pytest.mark.django_db
def test_positionen_ersetzen_komplett():
    c, actor = _client()
    bericht = _bericht(actor.id, _auftrag(actor.id))
    c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [_pos("Alt", "1"), _pos("Alt 2", "2")]},
        content_type=JSON,
    )
    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [_pos("Neu", "5")]},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["position_number"] == 1


def _pos(desc, qty, unit="Stk", **extra):
    return {"line_type": "MATERIAL", "description": desc, "quantity": qty,
            "unit": unit, **extra}


@pytest.mark.django_db
def test_positionen_am_unterzeichneten_bericht_422(monkeypatch):
    from db_core import storage as storage_module

    class Fake:
        def put_object(self, key, data, content_type=None):
            return None

    monkeypatch.setattr(storage_module, "get_storage", lambda: Fake())
    c, actor = _client()
    bericht = _bericht(actor.id, _auftrag(actor.id))
    report_service.sign_report(
        actor.id, report_id=bericht.id, signed_by_name="Klara",
        signature_png=(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        ),
    )
    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [_pos("Nachtrag", "1")]},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_unbekannter_bericht_404():
    c, _actor = _client()
    r = c.put(
        f"/api/workflow/site_reports/{uuid.uuid4()}/positionen",
        data={"lines": []},
        content_type=JSON,
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_ungueltige_positionsart_422():
    c, actor = _client()
    bericht = _bericht(actor.id, _auftrag(actor.id))
    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [{"line_type": "ZWISCHENSUMME", "description": "Summe"}]},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


# --- Vorbelegung ------------------------------------------------------------

@pytest.mark.django_db
def test_vorbelegen_aus_angebot():
    c, actor = _client()
    auftrag = _auftrag(actor.id)
    bericht = _bericht(actor.id, auftrag)
    angebot = _angebot(actor.id, auftrag, [
        _mat("Rohr DN20", "12"),
        _mat("Ausweichvariante", "5", kind="ALTERNATIV"),
    ])

    r = c.post(
        f"/api/workflow/site_reports/{bericht.id}/vorbelegen",
        data={"quote_id": str(angebot.id)},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    items = r.json()["items"]
    assert len(items) == 1                       # ALTERNATIV ist kein Soll
    assert items[0]["quantity"] == "12.000"
    assert items[0]["planned_quantity"] == "12.000"
    assert items[0]["source_quote_line_id"] is not None

    # Zweite Vorbelegung auf den nun befüllten Bericht → 422.
    r2 = c.post(
        f"/api/workflow/site_reports/{bericht.id}/vorbelegen",
        data={"quote_id": str(angebot.id)},
        content_type=JSON,
    )
    assert r2.status_code == 422, r2.content


@pytest.mark.django_db
def test_vorbelegbare_angebote_nur_die_des_auftrags():
    """Die Auswahlliste des UI zeigt genau die Angebote, aus denen die Vorbelegung
    auch tatsächlich zulässig ist (dieselbe Definition, kein zweiter Weg)."""
    c, actor = _client()
    auftrag = _auftrag(actor.id, titel="Unsere Baustelle")
    fremder = _auftrag(actor.id, titel="Fremde Baustelle")
    bericht = _bericht(actor.id, auftrag)
    unseres = _angebot(actor.id, auftrag, [_mat("Rohr DN20", "12")])
    _angebot(actor.id, fremder, [_mat("Rohr", "12")])

    r = c.get(f"/api/workflow/site_reports/{bericht.id}/vorbelegen-angebote")
    assert r.status_code == 200, r.content
    ids = [a["id"] for a in r.json()]
    assert ids == [str(unseres.id)]
    # Keine Beträge in der Auswahlliste.
    assert "net_total" not in r.json()[0]


@pytest.mark.django_db
def test_vorbelegen_aus_fremdem_angebot_422():
    c, actor = _client()
    auftrag = _auftrag(actor.id, titel="Unsere Baustelle")
    fremder = _auftrag(actor.id, titel="Fremde Baustelle")
    bericht = _bericht(actor.id, auftrag)
    fremdes = _angebot(actor.id, fremder, [_mat("Rohr", "12")])
    r = c.post(
        f"/api/workflow/site_reports/{bericht.id}/vorbelegen",
        data={"quote_id": str(fremdes.id)},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


# --- Soll-Ist ---------------------------------------------------------------

@pytest.mark.django_db
def test_soll_ist_endpunkt():
    c, actor = _client()
    auftrag = _auftrag(actor.id)
    _angebot(actor.id, auftrag, [_mat("Rohr DN20", "12"), _mat("Hahn", "2",
                                                               unit="Stk")])
    bericht = _bericht(actor.id, auftrag)
    c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [
            {"line_type": "MATERIAL", "description": "Rohr DN20",
             "quantity": "15", "unit": "m"},
            _pos("Notdichtung", "1"),
        ]},
        content_type=JSON,
    )

    r = c.get(f"/api/workflow/work_orders/{auftrag.id}/soll-ist")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["enthaelt_entwuerfe"] is True
    nach_art = {p["bezeichnung"]: p for p in body["positionen"]}
    assert nach_art["Rohr DN20"]["art"] == "MEHRVERBRAUCH"
    assert nach_art["Rohr DN20"]["differenz"] == "3.000"
    assert nach_art["Hahn"]["art"] == "ENTFALLEN"
    assert nach_art["Notdichtung"]["art"] == "ZUSATZ"
    # Keine Geldbeträge im Ergebnis.
    assert not (set(nach_art["Rohr DN20"]) & {"preis", "betrag", "netto"})


@pytest.mark.django_db
def test_soll_ist_unbekannter_auftrag_404():
    c, _actor = _client()
    assert c.get(
        f"/api/workflow/work_orders/{uuid.uuid4()}/soll-ist"
    ).status_code == 404


# --- Der Auftragsbezug des Angebots über die API ----------------------------

@pytest.mark.django_db
def test_angebot_wird_ueber_die_api_dem_auftrag_zugeordnet():
    """Der ganze Weg über Produkt-Endpunkte — kein Datenbank-Handgriff:
    Angebot anlegen (ohne Bezug) → zuordnen → versenden → Soll-Ist steht."""
    c, actor = _client()
    auftrag = _auftrag(actor.id)

    r = c.post(
        "/api/invoicing/quotes",
        data={"property_id": str(auftrag.property_id), "title": "Angebot",
              "lines": [_mat("Rohr DN20", "12")]},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    quote_id = r.json()["id"]
    assert r.json()["work_order"] is None

    r = c.put(
        f"/api/invoicing/quotes/{quote_id}",
        data={"work_order_id": str(auftrag.id)},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["work_order"]["id"] == str(auftrag.id)
    assert r.json()["work_order"]["order_number"]

    # Erst der Versand macht das Angebot zur Vereinbarung — dann trägt es das Soll.
    beleg_service.send_quote(actor.id, quote_id=uuid.UUID(quote_id))

    bericht = _bericht(actor.id, auftrag)
    r = c.post(
        f"/api/workflow/site_reports/{bericht.id}/vorbelegen",
        data={"quote_id": quote_id},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["items"][0]["planned_quantity"] == "12.000"

    body = c.get(f"/api/workflow/work_orders/{auftrag.id}/soll-ist").json()
    assert [a["id"] for a in body["angebote"]] == [quote_id]
    assert body["positionen"][0]["art"] == "UNVERAENDERT"


@pytest.mark.django_db
def test_zuordnung_nach_dem_versand_ueber_die_api():
    """Der reale Ablauf: Angebot versenden → Kunde nimmt an → dann Auftrag anlegen
    → **dann** zuordnen. Genau dieser Weg muss über die API laufen (0082).

    Und der Gegenbeweis in derselben Prüfung: eine Inhaltsänderung am versendeten
    Angebot bleibt gesperrt (422) — B-30 ist intakt.
    """
    c, actor = _client()
    auftrag = _auftrag(actor.id)

    r = c.post(
        "/api/invoicing/quotes",
        data={"property_id": str(auftrag.property_id), "title": "Angebot",
              "lines": [_mat("Rohr DN20", "12")]},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    quote_id = r.json()["id"]
    beleg_service.send_quote(actor.id, quote_id=uuid.UUID(quote_id))

    r = c.put(
        f"/api/invoicing/quotes/{quote_id}",
        data={"work_order_id": str(auftrag.id)},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["work_order"]["id"] == str(auftrag.id)

    # Das Soll steht — genau darum ging es.
    body = c.get(f"/api/workflow/work_orders/{auftrag.id}/soll-ist").json()
    assert body["positionen"][0]["soll"] == "12.000"

    # B-30: der Beleginhalt bleibt eingefroren.
    r = c.put(
        f"/api/invoicing/quotes/{quote_id}",
        data={"title": "Umgeschrieben"},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content
    r = c.put(
        f"/api/invoicing/quotes/{quote_id}",
        data={"lines": [_mat("Rohr DN20", "99")]},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content

    # Lösen geht ebenfalls in jedem Status.
    r = c.put(
        f"/api/invoicing/quotes/{quote_id}",
        data={"work_order_id": None},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["work_order"] is None


@pytest.mark.django_db
def test_angebot_an_fremdem_auftrag_422():
    c, actor = _client()
    auftrag = _auftrag(actor.id, titel="Unsere Baustelle")
    fremder = _auftrag(actor.id, titel="Fremde Baustelle")
    r = c.post(
        "/api/invoicing/quotes",
        data={"property_id": str(auftrag.property_id), "title": "Angebot",
              "work_order_id": str(fremder.id), "lines": [_mat("Rohr", "12")]},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_versendetes_angebot_an_fremdem_auftrag_422():
    """Die Freigabe von `work_order_id` weicht den zusammengesetzten FK nicht auf."""
    c, actor = _client()
    auftrag = _auftrag(actor.id, titel="Unsere Baustelle")
    fremder = _auftrag(actor.id, titel="Fremde Baustelle")
    r = c.post(
        "/api/invoicing/quotes",
        data={"property_id": str(auftrag.property_id), "title": "Angebot",
              "lines": [_mat("Rohr", "12")]},
        content_type=JSON,
    )
    quote_id = r.json()["id"]
    beleg_service.send_quote(actor.id, quote_id=uuid.UUID(quote_id))
    r = c.put(
        f"/api/invoicing/quotes/{quote_id}",
        data={"work_order_id": str(fremder.id)},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_gefaelschte_sollmenge_ueber_die_api():
    """Ohne Herkunft: 422. Mit Herkunft: der Client-Wert wird verworfen."""
    c, actor = _client()
    auftrag = _auftrag(actor.id)
    bericht = _bericht(actor.id, auftrag)
    angebot = _angebot(actor.id, auftrag, [_mat("Rohr DN20", "12")])

    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [_pos("Frei erfunden", "1", planned_quantity="99")]},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content

    ql_id = str(angebot.lines.first().id)
    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [_pos("Rohr DN20", "12", unit="m",
                             source_quote_line_id=ql_id,
                             planned_quantity="99")]},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["items"][0]["planned_quantity"] == "12.000"   # nicht 99


@pytest.mark.django_db
def test_fremde_herkunft_ohne_mitgeschickte_felder_ueber_die_api():
    """**Der Befund der Abnahme, über die API.** Der Client lässt `source_article_id`
    und `unit` einfach WEG und hängt die KESSEL-Zeile als Herkunft an eine
    Rohr-Position. Vorher: 200 OK — gespeichert wurde „Rohr DN20 · 5 Stk · angeboten
    500". Jetzt: 422, weil auch die Bezeichnung aus der Herkunft kommt."""
    c, actor = _client()
    auftrag = _auftrag(actor.id)
    bericht = _bericht(actor.id, auftrag)
    angebot = _angebot(actor.id, auftrag, [
        _mat("Rohr DN20", "12"),
        _mat("Kessel", "500", unit="Stk"),
    ])
    kessel = str(angebot.lines.get(description="Kessel").id)

    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [{"line_type": "MATERIAL", "description": "Rohr DN20",
                         "quantity": "5", "source_quote_line_id": kessel}]},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content
    assert "Bezeichnung" in r.json()["detail"]

    # Der Weg, den es stattdessen gibt: Bezeichnung fest, Ergänzung in der Notiz.
    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [{"line_type": "MATERIAL", "quantity": "5",
                         "note": "Steigstrang, 2. OG",
                         "source_quote_line_id": kessel}]},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    zeile = r.json()["items"][0]
    assert zeile["description"] == "Kessel"
    assert zeile["unit"] == "Stk"
    assert zeile["planned_quantity"] == "500.000"
    assert zeile["note"] == "Steigstrang, 2. OG"


@pytest.mark.django_db
def test_zuordnung_loesen_ueber_die_api_422():
    """Ein-Klick-Button „Zuordnung lösen" an einem Angebot, dessen Positionen ein
    Bericht bereits als Soll führt: 422 statt Sackgasse."""
    c, actor = _client()
    auftrag = _auftrag(actor.id)
    bericht = _bericht(actor.id, auftrag)
    angebot = _angebot(actor.id, auftrag, [_mat("Rohr DN20", "12")])
    report_service.vorbelegen_aus_angebot(
        actor.id, report_id=bericht.id, quote_id=angebot.id
    )

    r = c.put(
        f"/api/invoicing/quotes/{angebot.id}",
        data={"work_order_id": None},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content
    assert "Zuordnung" in r.json()["detail"]
    angebot.refresh_from_db()
    assert angebot.work_order_id == auftrag.id


# --- Rechte -----------------------------------------------------------------

def _monteur_am_auftragstermin(dispo, auftrag):
    user, monteur = make_role_user("MONTEUR")
    c = Client()
    c.force_login(user)
    job = einsatz_service.create_service_job(
        dispo.id, work_order_id=auftrag.id
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    return c, monteur, job


@pytest.mark.django_db
def test_monteur_setzt_positionen_am_eigenen_bericht():
    dispo = make_app_user("Dispo")
    auftrag = _auftrag(dispo.id)
    c, monteur, job = _monteur_am_auftragstermin(dispo, auftrag)
    bericht = report_service.create_report(
        monteur.id, service_job_id=job.id, report_date="2026-07-13",
        activity_text="Vor Ort gearbeitet.",
    )
    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [_pos("Dichtung", "3")]},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["total"] == 1


@pytest.mark.django_db
def test_monteur_am_fremden_bericht_404():
    dispo = make_app_user("Dispo")
    auftrag = _auftrag(dispo.id)
    c, _monteur, _job = _monteur_am_auftragstermin(dispo, auftrag)

    fremder_job = einsatz_service.create_service_job(dispo.id, title="Fremd")
    fremder = report_service.create_report(
        dispo.id, service_job_id=fremder_job.id, report_date="2026-07-13",
        activity_text="Fremd",
    )
    assert c.put(
        f"/api/workflow/site_reports/{fremder.id}/positionen",
        data={"lines": [_pos("Eingeschleust", "1")]},
        content_type=JSON,
    ).status_code == 404
    assert c.post(
        f"/api/workflow/site_reports/{fremder.id}/vorbelegen",
        data={"quote_id": str(uuid.uuid4())},
        content_type=JSON,
    ).status_code == 404
    assert c.get(
        f"/api/workflow/site_reports/{fremder.id}"
    ).status_code == 404


@pytest.mark.django_db
def test_monteur_liest_soll_ist_am_eigenen_objekt_aber_nicht_am_fremden():
    """Objektsicht (0099): Der Soll-Ist führt **keine Geldbeträge** — er ist für den
    Monteur an SEINEM Objekt lesbar (er soll wissen, was geplant war und was drin
    steckt). An einem fremden Objekt: 404, nicht 403 — die Zielart ist zulässig, das
    Objekt ist es nicht."""
    dispo = make_app_user("Dispo")
    auftrag = _auftrag(dispo.id)
    c, _monteur, _job = _monteur_am_auftragstermin(dispo, auftrag)

    r = c.get(f"/api/workflow/work_orders/{auftrag.id}/soll-ist")
    assert r.status_code == 200, r.content
    assert r.json()["work_order_id"] == str(auftrag.id)

    fremd = _auftrag(dispo.id)
    r = c.get(f"/api/workflow/work_orders/{fremd.id}/soll-ist")
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_nur_lesen_darf_keine_positionen_setzen():
    c, _actor = _client("NUR_LESEN")
    dispo = make_app_user("Dispo")
    bericht = _bericht(dispo.id, _auftrag(dispo.id))
    r = c.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": [_pos("X", "1")]},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    # Lesen darf er (inkl. Soll-Ist).
    assert c.get(f"/api/workflow/site_reports/{bericht.id}").status_code == 200


@pytest.mark.django_db
def test_ohne_anmeldung_abgelehnt():
    _c, actor = _client()
    bericht = _bericht(actor.id, _auftrag(actor.id))
    anonym = Client()
    assert anonym.put(
        f"/api/workflow/site_reports/{bericht.id}/positionen",
        data={"lines": []}, content_type=JSON,
    ).status_code in (401, 403)
    assert anonym.get(
        f"/api/workflow/work_orders/{bericht.work_order_id}/soll-ist"
    ).status_code in (401, 403)
