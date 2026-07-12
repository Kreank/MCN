"""API-Tests der Fälligkeiten-Engine (/api/maintenance/due-items, Prüfungen,
Gewährleistung).

Schwerpunkt neben dem Fachlichen: die **Rechte**. Das Modul heißt `maintenance`
(Migration 0071). Verwerfen hängt an **STORNIEREN** — die DISPOSITION darf eine
Fälligkeit erledigen, aber nicht bewusst verstreichen lassen; der MONTEUR hat im
Modul gar nichts zu suchen.
"""
import uuid
from datetime import date, timedelta

import pytest

from db_core.models import ServiceJob
from db_core.services import auftrag as auftrag_service
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services import gewaehrleistung as gewaehrleistung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import pruefung as pruefung_service
from db_core.services import wartung as wartung_service

HEUTE = date.today()


@pytest.fixture
def firmenprofil(app_user):
    """Firmenprofil-Singleton — Träger der Gewährleistungs-Voreinstellung."""
    from db_core.db_context import business_transaction
    from db_core.models import CompanyProfile

    with business_transaction(app_user.id):
        return CompanyProfile.objects.create(
            id=uuid.uuid4(), company_name="Mitra Sanitär GmbH"
        )


@pytest.fixture
def welt(app_user):
    obj = property_service.create_property(
        app_user.id, name="Prüfhaus", property_type="WEG",
        street="Weg", house_number="2", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Karla", last_name="Kundin"
    )
    vertrag = wartung_service.create_contract(
        app_user.id, property_id=obj.id, name="Thermenwartung",
        start_date=HEUTE, interval_kind="JAEHRLICH",
        due_action="BENACHRICHTIGUNG", party_id=kunde.id, lead_time_days=0,
    )
    art = pruefung_service.create_inspection_type(
        app_user.id, name="Eigene Prüfart", interval_kind="JAEHRLICH",
        lead_time_days=0, responsibility="Fachbetrieb",
    )
    pruefung = pruefung_service.create_inspection(
        app_user.id, inspection_type_id=art.id, property_id=obj.id,
        start_date=HEUTE,
    )
    erzeugt = faelligkeit_service.generiere(app_user.id, stichtag=HEUTE)
    return {
        "obj": obj, "kunde": kunde, "vertrag": vertrag, "art": art,
        "pruefung": pruefung,
        "wartung_item": erzeugt["WARTUNG"][0],
        "pruef_item": erzeugt["PRUEFUNG"][0],
    }


# ---------------------------------------------------------------------------
# Liste
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_liste_zeigt_alle_arten(admin_client, welt):
    r = admin_client.get("/api/maintenance/due-items")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    arten = {i["kind"] for i in body["items"]}
    assert arten == {"WARTUNG", "PRUEFUNG"}
    wartung = next(i for i in body["items"] if i["kind"] == "WARTUNG")
    assert wartung["quelle"].startswith("Wartungsvertrag W-")
    assert wartung["property"]["name"] == "Prüfhaus"
    assert wartung["termin_vorschlag"]  # Werktags-Vorschlag ist immer dabei


@pytest.mark.django_db
def test_liste_filter_art(admin_client, welt):
    r = admin_client.get("/api/maintenance/due-items?kind=PRUEFUNG")
    assert r.json()["total"] == 1


@pytest.mark.django_db
def test_liste_filter_zeitraum(admin_client, welt):
    morgen = (HEUTE + timedelta(days=1)).isoformat()
    r = admin_client.get(f"/api/maintenance/due-items?von={morgen}")
    assert r.json()["total"] == 0


@pytest.mark.django_db
def test_liste_unbekannter_status_422(admin_client, welt):
    r = admin_client.get("/api/maintenance/due-items?status=QUATSCH")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Erledigen — Folgeobjekte
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_termin_erzeugen_landet_im_rueckstand(admin_client, welt):
    """Der Browser-Fluss: Fälligkeit → Termin → Plantafel-Rückstand."""
    item_id = welt["wartung_item"].id
    r = admin_client.post(
        f"/api/maintenance/due-items/{item_id}/erledigen",
        data={"folgeaktion": "TERMIN"}, content_type="application/json",
    )
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["status"] == "ERLEDIGT"
    assert item["result_object_type"] == "workflow.service_job"

    job = ServiceJob.objects.get(id=item["result_object_id"])
    assert job.status == "UNGEPLANT"  # Rückstand
    assert job.work_order_id is None

    # Und er taucht wirklich im Plantafel-Rückstand auf.
    von = HEUTE.isoformat()
    bis = (HEUTE + timedelta(days=7)).isoformat()
    board = admin_client.get(f"/api/planung/plantafel?von={von}&bis={bis}").json()
    assert str(job.id) in {b["id"] for b in board["backlog"]}


@pytest.mark.django_db
def test_termin_MIT_wunschdatum_landet_ebenfalls_im_rueckstand(admin_client, welt):
    """Der Normalfall des Dialogs — die Dispo nennt ein Datum.

    Auch dann muss der Einsatz im Rückstand SICHTBAR sein. Ein Einsatz mit
    Zeitraum, aber ohne Zuweisung wäre in der Plantafel nirgends: nicht im
    Rückstand (der zeigt nur Einsätze ohne Beginn) und nicht im Raster (Kacheln
    hängen an Monteur/Ressource). Das Datum ist deshalb ein Wunschtermin.
    """
    item_id = welt["wartung_item"].id
    wunsch = (HEUTE + timedelta(days=1)).isoformat()
    r = admin_client.post(
        f"/api/maintenance/due-items/{item_id}/erledigen",
        data={"folgeaktion": "TERMIN", "termin_datum": wunsch},
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.json()
    assert "Rückstand" in body["hinweis"]

    job = ServiceJob.objects.get(id=body["item"]["result_object_id"])
    assert job.status == "UNGEPLANT"
    assert job.scheduled_start is None
    assert "Wunschtermin" in job.access_instructions

    von = HEUTE.isoformat()
    bis = (HEUTE + timedelta(days=7)).isoformat()
    board = admin_client.get(f"/api/planung/plantafel?von={von}&bis={bis}").json()
    assert str(job.id) in {b["id"] for b in board["backlog"]}


@pytest.mark.django_db
def test_erledigte_faelligkeit_verschwindet_aus_der_offenen_liste(admin_client, welt):
    item_id = welt["pruef_item"].id
    admin_client.post(
        f"/api/maintenance/due-items/{item_id}/erledigen",
        data={"folgeaktion": "AUFGABE"}, content_type="application/json",
    )
    offen = admin_client.get("/api/maintenance/due-items?status=OFFEN").json()
    assert {i["id"] for i in offen["items"]} == {str(welt["wartung_item"].id)}
    erledigt = admin_client.get("/api/maintenance/due-items?status=ERLEDIGT").json()
    assert erledigt["total"] == 1


@pytest.mark.django_db
def test_angebot_aus_faelligkeit(admin_client, welt):
    item_id = welt["wartung_item"].id
    r = admin_client.post(
        f"/api/maintenance/due-items/{item_id}/erledigen",
        data={"folgeaktion": "ANGEBOT"}, content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["item"]["result_object_type"] == "invoicing.quote"


@pytest.mark.django_db
def test_unbekannte_folgeaktion_422(admin_client, welt):
    r = admin_client.post(
        f"/api/maintenance/due-items/{welt['wartung_item'].id}/erledigen",
        data={"folgeaktion": "ZAUBERN"}, content_type="application/json",
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Verwerfen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_verwerfen_mit_begruendung(admin_client, welt):
    item_id = welt["wartung_item"].id
    r = admin_client.post(
        f"/api/maintenance/due-items/{item_id}/verwerfen",
        data={"begruendung": "Kunde hat den Vertrag mündlich gekündigt."},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["status"] == "VERWORFEN"
    assert r.json()["resolution_note"].startswith("Kunde hat")


@pytest.mark.django_db
def test_verwerfen_ohne_begruendung_422(admin_client, welt):
    r = admin_client.post(
        f"/api/maintenance/due-items/{welt['wartung_item'].id}/verwerfen",
        data={"begruendung": "  "}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_verworfene_faelligkeit_kommt_nicht_zurueck(admin_client, welt, app_user):
    item_id = welt["wartung_item"].id
    admin_client.post(
        f"/api/maintenance/due-items/{item_id}/verwerfen",
        data={"begruendung": "Diesmal nicht."}, content_type="application/json",
    )
    for _ in range(3):
        faelligkeit_service.generiere(app_user.id, stichtag=HEUTE)
    offen = admin_client.get("/api/maintenance/due-items?kind=WARTUNG").json()
    assert offen["total"] == 0


# ---------------------------------------------------------------------------
# Rechte
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_monteur_sieht_keine_faelligkeiten(client_with_role, welt):
    """MONTEUR hat im Modul `maintenance` kein Recht — fail-closed 403."""
    c = client_with_role("MONTEUR")
    assert c.get("/api/maintenance/due-items").status_code == 403


@pytest.mark.django_db
def test_disposition_darf_erledigen_aber_nicht_verwerfen(client_with_role, welt):
    """Eine Frist bewusst verstreichen zu lassen (STORNIEREN) ist keine
    Dispo-Entscheidung — erledigen (AENDERN) sehr wohl."""
    c = client_with_role("DISPOSITION")
    item_id = welt["pruef_item"].id

    verwerfen = c.post(
        f"/api/maintenance/due-items/{item_id}/verwerfen",
        data={"begruendung": "Egal."}, content_type="application/json",
    )
    assert verwerfen.status_code == 403

    erledigen = c.post(
        f"/api/maintenance/due-items/{item_id}/erledigen",
        data={"folgeaktion": "AUFGABE"}, content_type="application/json",
    )
    assert erledigen.status_code == 200


@pytest.mark.django_db
def test_nur_lesen_darf_nur_lesen(client_with_role, welt):
    c = client_with_role("NUR_LESEN")
    assert c.get("/api/maintenance/due-items").status_code == 200
    r = c.post(
        f"/api/maintenance/due-items/{welt['wartung_item'].id}/erledigen",
        data={"folgeaktion": "AUFGABE"}, content_type="application/json",
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Prüfarten / Prüfungen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_pruefarten_liefern_vorschlaege_gekennzeichnet(admin_client, welt):
    r = admin_client.get("/api/maintenance/inspection-types")
    assert r.status_code == 200
    typen = r.json()
    vorschlaege = [t for t in typen if t["is_suggestion"]]
    assert vorschlaege, "Die ausgelieferten Prüfart-Vorschläge fehlen."
    # Sie sind als Vorschlag gekennzeichnet und sagen es auch im Text.
    for t in vorschlaege:
        assert "Rechtsauskunft" in (t["notes"] or "")
    # Die selbst angelegte ist KEIN Vorschlag.
    eigene = next(t for t in typen if t["name"] == "Eigene Prüfart")
    assert eigene["is_suggestion"] is False


@pytest.mark.django_db
def test_pruefart_anlegen_und_deaktivieren(admin_client, welt):
    r = admin_client.post(
        "/api/maintenance/inspection-types",
        data={"name": "Aufzugsprüfung", "interval_kind": "TAGE",
              "interval_days": 180, "lead_time_days": 21,
              "responsibility": "ZÜS"},
        content_type="application/json",
    )
    assert r.status_code == 201
    tid = r.json()["id"]

    d = admin_client.patch(
        f"/api/maintenance/inspection-types/{tid}",
        data={"is_active": False}, content_type="application/json",
    )
    assert d.status_code == 200
    assert d.json()["is_active"] is False
    # Deaktivierte Prüfarten sind nicht mehr zuweisbar.
    neu = admin_client.post(
        "/api/maintenance/inspections",
        data={"inspection_type_id": tid, "property_id": str(welt["obj"].id),
              "start_date": HEUTE.isoformat()},
        content_type="application/json",
    )
    assert neu.status_code == 422


@pytest.mark.django_db
def test_pruefung_anlegen(admin_client, welt):
    r = admin_client.post(
        "/api/maintenance/inspections",
        data={"inspection_type_id": str(welt["art"].id),
              "property_id": str(welt["obj"].id),
              "start_date": (HEUTE + timedelta(days=10)).isoformat(),
              "name": "Legionellen Haus A"},
        content_type="application/json",
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Legionellen Haus A"
    assert body["next_due_date"] == (HEUTE + timedelta(days=10)).isoformat()
    assert body["responsibility"] == "Fachbetrieb"  # aus der Prüfart kopiert


@pytest.mark.django_db
def test_pruefung_status(admin_client, welt):
    pid = welt["pruefung"].id
    r = admin_client.post(
        f"/api/maintenance/inspections/{pid}/status",
        data={"to_status": "INAKTIV"}, content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["status"] == "INAKTIV"
    # AKTIV → ARCHIVIERT direkt ist nicht zulässig (nur aus INAKTIV).
    r2 = admin_client.post(
        f"/api/maintenance/inspections/{pid}/status",
        data={"to_status": "ARCHIVIERT"}, content_type="application/json",
    )
    assert r2.status_code == 200  # aus INAKTIV heraus erlaubt
    r3 = admin_client.post(
        f"/api/maintenance/inspections/{pid}/status",
        data={"to_status": "AKTIV"}, content_type="application/json",
    )
    assert r3.status_code == 422  # ARCHIVIERT ist final


# ---------------------------------------------------------------------------
# Gewährleistung
# ---------------------------------------------------------------------------

@pytest.fixture
def auftrag(app_user, welt):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=welt["obj"].id, title="Heizungstausch"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=welt["kunde"].id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN"):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status=to
        )
    order.refresh_from_db()
    return order


@pytest.mark.django_db
def test_gewaehrleistung_anlegen_mit_default(admin_client, auftrag):
    r = admin_client.post(
        "/api/maintenance/warranties",
        data={"work_order_id": str(auftrag.id)}, content_type="application/json",
    )
    assert r.status_code == 201
    body = r.json()
    assert body["duration_months"] == 60      # Default aus dem Firmenprofil
    assert body["lead_time_days"] == 90
    assert body["basis"] == "BGB"
    assert body["end_date"] > body["start_date"]


@pytest.mark.django_db
def test_gewaehrleistung_frist_je_auftrag_einstellbar(admin_client, auftrag):
    r = admin_client.post(
        "/api/maintenance/warranties",
        data={"work_order_id": str(auftrag.id), "duration_months": 24,
              "basis": "INDIVIDUELL", "is_machinery": True},
        content_type="application/json",
    )
    assert r.status_code == 201
    wid = r.json()["id"]

    p = admin_client.patch(
        f"/api/maintenance/warranties/{wid}",
        data={"duration_months": 48, "basis": "VOB"},
        content_type="application/json",
    )
    assert p.status_code == 200
    assert p.json()["duration_months"] == 48
    assert p.json()["basis"] == "VOB"


@pytest.mark.django_db
def test_vertriebshinweis_im_api(admin_client, auftrag, app_user, welt):
    """Der Hinweis ist da — aber die Frist bleibt, was eingetragen wurde."""
    # Der Wartungsvertrag aus `welt` hängt an derselben Liegenschaft → erst
    # inaktiv setzen, sonst greift der Hinweis (zu Recht) nicht.
    wartung_service.set_status(
        app_user.id, contract_id=welt["vertrag"].id, to_status="INAKTIV"
    )
    r = admin_client.post(
        "/api/maintenance/warranties",
        data={"work_order_id": str(auftrag.id), "is_machinery": True,
              "duration_months": 24},
        content_type="application/json",
    )
    assert r.status_code == 201
    body = r.json()
    assert body["vertriebshinweis"] is not None
    assert "Wartungsvertrag" in body["vertriebshinweis"]
    assert "keine Rechtsauskunft" in body["vertriebshinweis"]
    assert body["duration_months"] == 24  # unangetastet


@pytest.mark.django_db
def test_gewaehrleistung_liste_liefert_defaults_und_vorschlaege(admin_client, auftrag):
    r = admin_client.get("/api/maintenance/warranties")
    assert r.status_code == 200
    body = r.json()
    assert body["default_months"] == 60
    assert set(body["vorschlaege"]) == {"BGB", "VOB", "INDIVIDUELL"}


@pytest.mark.django_db
def test_gewaehrleistungs_default_aenderbar(admin_client, auftrag, firmenprofil):
    r = admin_client.patch(
        "/api/maintenance/warranty-defaults",
        data={"months": 48}, content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["default_months"] == 48
    assert gewaehrleistung_service.default_monate() == 48


@pytest.mark.django_db
def test_gewaehrleistungs_default_braucht_company_recht(
    client_with_role, auftrag, firmenprofil
):
    """Der Endpunkt schreibt das FIRMENPROFIL. Dafür gilt company/AENDERN — sonst
    wäre er die Hintertür, durch die Dispo/Technische Leitung Firmenstammdaten
    ändern (maintenance/AENDERN haben sie, company/AENDERN nicht)."""
    for rolle in ("DISPOSITION", "TECHNISCHE_LEITUNG"):
        c = client_with_role(rolle)
        r = c.patch(
            "/api/maintenance/warranty-defaults",
            data={"months": 12}, content_type="application/json",
        )
        assert r.status_code == 403, rolle
    assert gewaehrleistung_service.default_monate() == 60  # unverändert


@pytest.mark.django_db
def test_gewaehrleistungs_default_grenzen(admin_client, auftrag, firmenprofil):
    r = admin_client.patch(
        "/api/maintenance/warranty-defaults",
        data={"months": 999}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_default_ohne_firmenprofil_faellt_zurueck(admin_client, auftrag):
    """Ohne Firmenprofil greift der ausgelieferte Default (60/90) — der Betrieb
    soll nicht ohne Gewährleistungsfrist dastehen, nur weil die Stammdaten fehlen."""
    assert gewaehrleistung_service.default_monate() == 60
    r = admin_client.patch(
        "/api/maintenance/warranty-defaults",
        data={"months": 48}, content_type="application/json",
    )
    assert r.status_code == 422  # …ändern lässt er sich aber erst mit Profil


@pytest.mark.django_db
def test_gewaehrleistung_erscheint_als_faelligkeit(admin_client, auftrag, app_user):
    """Vorlauf: die Frist läuft in 30 Tagen ab, Vorlauf 90 → jetzt sichtbar."""
    gewaehrleistung_service.create_warranty(
        app_user.id, work_order_id=auftrag.id,
        start_date=HEUTE - timedelta(days=335),
        duration_months=12, lead_time_days=90,
    )
    faelligkeit_service.generiere(app_user.id, stichtag=HEUTE)
    r = admin_client.get("/api/maintenance/due-items?kind=GEWAEHRLEISTUNG")
    assert r.json()["total"] == 1
    item = r.json()["items"][0]
    assert item["quelle"].startswith("Auftrag AU-")
    assert item["tage_bis_faellig"] > 0  # rechtzeitig VOR dem Ablauf
