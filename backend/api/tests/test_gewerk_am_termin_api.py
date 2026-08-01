"""API-Tests „Das Gewerk am Termin" (workflow.service_job.trade_id, 0120).

Das Gewerk wurde beim Anlegen gesetzt (und vom Auftrag geerbt), war danach aber
**write-only**: kein Ausgabeschema trug es, kein Endpunkt konnte es ändern, das
Board konnte nicht danach filtern. Ein Datenfeld, das niemand sehen kann, ist im
Betrieb keines — genau dasselbe Muster wie bei `quote.work_order_id`.

Geprüft wird:
* Ausgabe an allen vier Stellen: Liste, Detail, Board-Kachel, Rückstandskarte,
* der Gewerkfilter des Boards — **symmetrisch** auf Raster UND Rückstand
  (Treffer und Nicht-Treffer),
* Setzen/Ändern/Entfernen über PATCH /einsaetze/{id} und PATCH /termine/{id},
  inklusive der None-Semantik („weglassen = unverändert, null = entfernen") und
  der Zusage, dass beim Update **nicht erneut** vom Auftrag geerbt wird,
* unbekannte trade_id → 422 (nicht 500),
* Rechte: das Gewerk ist Dispositionsdatum — ein Monteur (Scope EIGENE) kommt
  weder am eigenen freien Termin daran,
* die Qualifikations-Bedienfläche am Einsatz ist fail-closed (Monteur → 403).
"""
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.test import Client

from db_core.models import ServiceJob, Trade
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service

from .conftest import make_app_user, make_role_user

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = T0 + timedelta(hours=2)
TAG = f"{T0:%Y-%m-%d}"
JSON = "application/json"


def _client(role="ADMINISTRATION"):
    user, app_user = make_role_user(role)
    client = Client()
    client.force_login(user)
    return client, app_user


def _trade(code):
    """Gewerk aus dem mitgelieferten Katalog (Migration 0120 seedet SAN/HZG/ELT …).

    Bewusst NICHT neu angelegt: Der Code ist UNIQUE, ein `create_trade("HZG")`
    liefe gegen die Seed-Zeile. Die Tests nehmen also genau die Stammdaten, die
    ein frischer Betrieb auch hat.
    """
    return Trade.objects.get(code=code)


def _property(actor_id, name="Gewerkhaus"):
    return property_service.create_property(
        actor_id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )


def _order(actor_id, *, trade_id=None, obj=None, title="Therme tauschen"):
    obj = obj or _property(actor_id, name=f"Objekt {uuid.uuid4().hex[:6]}")
    principal = identity_service.create_person(
        actor_id, first_name="Petra", last_name="Prinzipal"
    )
    order = auftrag_service.create_work_order(
        actor_id, property_id=obj.id, title=title, trade_id=trade_id
    )
    auftrag_service.set_order_evidence(
        actor_id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        actor_id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        actor_id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(
            actor_id, work_order_id=order.id, to_status=to_status
        )
    return order


# --- Ausgabe ----------------------------------------------------------------

@pytest.mark.django_db
def test_gewerk_steht_in_liste_und_detail():
    client, actor = _client()
    hzg = _trade("HZG")
    order = _order(actor.id, trade_id=hzg.id)
    job = einsatz_service.create_service_job(
        actor.id, work_order_id=order.id, scheduled_start=T0
    )
    # Erben beim Anlegen (0120) — die Voraussetzung dieses Slice.
    assert job.trade_id == hzg.id

    r = client.get("/api/planung/einsaetze")
    assert r.status_code == 200, r.content
    eintrag = next(i for i in r.json()["items"] if i["id"] == str(job.id))
    assert eintrag["trade"] == {
        "id": str(hzg.id), "code": "HZG", "label": "Heizung",
    }

    r = client.get(f"/api/planung/einsaetze/{job.id}")
    assert r.status_code == 200, r.content
    assert r.json()["trade"]["label"] == "Heizung"


@pytest.mark.django_db
def test_freier_termin_ohne_gewerk_liefert_null():
    """`null` heißt „kein Gewerk gepflegt" — nicht „Sonstiges"."""
    client, actor = _client()
    job = einsatz_service.create_service_job(actor.id, title="Begehung")
    r = client.get(f"/api/planung/einsaetze/{job.id}")
    assert r.status_code == 200, r.content
    assert r.json()["trade"] is None


@pytest.mark.django_db
def test_gewerk_steht_auf_board_kachel_und_rueckstandskarte():
    client, actor = _client()
    san = _trade("SAN")
    order = _order(actor.id, trade_id=san.id)
    geplant = einsatz_service.create_service_job(
        actor.id, work_order_id=order.id, scheduled_start=T0, scheduled_end=T1
    )
    rueckstand = einsatz_service.create_service_job(
        actor.id, work_order_id=order.id
    )

    r = client.get(f"/api/planung/plantafel?date_from={TAG}&date_to={TAG}")
    assert r.status_code == 200, r.content
    body = r.json()
    kachel = next(j for j in body["jobs"] if j["id"] == str(geplant.id))
    assert kachel["trade"]["label"] == "Sanitär"
    karte = next(b for b in body["backlog"] if b["id"] == str(rueckstand.id))
    assert karte["trade"]["code"] == "SAN"


# --- Board-Filter (Raster UND Rückstand) ------------------------------------

@pytest.mark.django_db
def test_board_gewerkfilter_trifft_raster_und_rueckstand():
    """Der Filter greift auf BEIDE Bahnen — sonst zöge der Disponent einen
    fremden Termin ins gefilterte Raster, wo er sofort wieder verschwände."""
    client, actor = _client()
    hzg = _trade("HZG")
    san = _trade("SAN")
    order_h = _order(actor.id, trade_id=hzg.id, title="Therme")
    order_s = _order(actor.id, trade_id=san.id, title="Rohrbruch")

    h_geplant = einsatz_service.create_service_job(
        actor.id, work_order_id=order_h.id, scheduled_start=T0, scheduled_end=T1
    )
    s_geplant = einsatz_service.create_service_job(
        actor.id, work_order_id=order_s.id, scheduled_start=T0, scheduled_end=T1
    )
    h_rueck = einsatz_service.create_service_job(actor.id, work_order_id=order_h.id)
    s_rueck = einsatz_service.create_service_job(actor.id, work_order_id=order_s.id)

    ungefiltert = client.get(
        f"/api/planung/plantafel?date_from={TAG}&date_to={TAG}"
    ).json()
    assert {j["id"] for j in ungefiltert["jobs"]} == {
        str(h_geplant.id), str(s_geplant.id)
    }
    assert ungefiltert["backlog_total"] == 2

    r = client.get(
        f"/api/planung/plantafel?date_from={TAG}&date_to={TAG}&trade_id={hzg.id}"
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert [j["id"] for j in body["jobs"]] == [str(h_geplant.id)]
    assert [b["id"] for b in body["backlog"]] == [str(h_rueck.id)]
    # Der Zähler muss mitfiltern, sonst behauptet die Leiste „N weitere", die es
    # in dieser Sicht gar nicht gibt.
    assert body["backlog_total"] == 1
    assert str(s_geplant.id) not in {j["id"] for j in body["jobs"]}
    assert str(s_rueck.id) not in {b["id"] for b in body["backlog"]}


@pytest.mark.django_db
def test_board_gewerkfilter_ohne_treffer_ist_leer():
    client, actor = _client()
    hzg = _trade("HZG")
    elt = _trade("ELT")
    order = _order(actor.id, trade_id=hzg.id)
    einsatz_service.create_service_job(
        actor.id, work_order_id=order.id, scheduled_start=T0, scheduled_end=T1
    )
    einsatz_service.create_service_job(actor.id, work_order_id=order.id)

    r = client.get(
        f"/api/planung/plantafel?date_from={TAG}&date_to={TAG}&trade_id={elt.id}"
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["jobs"] == []
    assert body["backlog"] == []
    assert body["backlog_total"] == 0
    # Die BAHNEN bleiben: Auf eine leere Bahn muss man weiterhin ziehen können.
    assert body["lanes"] != []


@pytest.mark.django_db
def test_gewerkfilter_blendet_termine_ohne_gewerk_aus():
    """Bewusst festgehalten: Ein Termin OHNE Gewerk ist kein Treffer eines
    Gewerkfilters. Die Oberfläche sagt das dazu (Hinweistext am Board)."""
    client, actor = _client()
    hzg = _trade("HZG")
    einsatz_service.create_service_job(
        actor.id, title="Begehung ohne Gewerk",
        scheduled_start=T0, scheduled_end=T1,
    )
    r = client.get(
        f"/api/planung/plantafel?date_from={TAG}&date_to={TAG}&trade_id={hzg.id}"
    )
    assert r.status_code == 200, r.content
    assert r.json()["jobs"] == []


# --- Setzen / Ändern --------------------------------------------------------

@pytest.mark.django_db
def test_gewerk_beim_anlegen_ueber_die_api_setzbar():
    client, actor = _client()
    san = _trade("SAN")
    r = client.post(
        "/api/planung/einsaetze",
        data={"title": "Begehung Bad", "trade_id": str(san.id)},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    assert r.json()["trade"]["code"] == "SAN"


@pytest.mark.django_db
def test_gewerk_ueber_patch_einsaetze_setzen_und_entfernen():
    client, actor = _client()
    hzg = _trade("HZG")
    job = einsatz_service.create_service_job(actor.id, title="Begehung")

    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"trade_id": str(hzg.id)},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["trade"]["id"] == str(hzg.id)

    # Ausdrückliches null = entfernen.
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"trade_id": None},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["trade"] is None


@pytest.mark.django_db
def test_weggelassenes_gewerk_bleibt_unveraendert():
    """„Nicht mitgeschickt" ist etwas anderes als „auf null" — sonst löschte
    jedes Nachtragen des Zutrittscodes nebenbei das Gewerk."""
    client, actor = _client()
    hzg = _trade("HZG")
    job = einsatz_service.create_service_job(
        actor.id, title="Begehung", trade_id=hzg.id
    )
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"access_instructions": "Schlüssel im Kasten"},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["trade"]["id"] == str(hzg.id)


@pytest.mark.django_db
def test_entferntes_gewerk_wird_nicht_erneut_vom_auftrag_geerbt():
    """Das Erben ist eine Voreinstellung der ANLAGE, keine laufende Bindung.
    Käme es beim nächsten Speichern zurück, wäre es nie zu löschen."""
    client, actor = _client()
    hzg = _trade("HZG")
    order = _order(actor.id, trade_id=hzg.id)
    job = einsatz_service.create_service_job(actor.id, work_order_id=order.id)
    assert job.trade_id == hzg.id

    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"trade_id": None},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["trade"] is None

    # Ein weiteres Update ohne trade_id darf es nicht wiederbeleben.
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"access_instructions": "Code 1234"},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["trade"] is None
    assert ServiceJob.objects.get(id=job.id).trade_id is None


@pytest.mark.django_db
def test_gewerk_ueber_termin_patch_aenderbar():
    """Der Board-Dialog schreibt über PATCH /termine/{id} — auch dort muss das
    Gewerk ankommen, sonst bliebe es aus dem Board heraus unveränderlich."""
    client, actor = _client()
    hzg = _trade("HZG")
    san = _trade("SAN")
    job = planung_service.create_termin(
        actor.id, title="Begehung", scheduled_start=T0, scheduled_end=T1,
        trade_id=hzg.id,
    )
    r = client.patch(
        f"/api/planung/termine/{job.id}",
        data={"trade_id": str(san.id)},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["trade"]["code"] == "SAN"

    r = client.patch(
        f"/api/planung/termine/{job.id}", data={"trade_id": None},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["trade"] is None


# --- Serie ------------------------------------------------------------------

@pytest.mark.django_db
def test_serie_uebernimmt_das_gewerk_des_ausgangstermins():
    """Sonst verlöre eine Serie freier Termine ihr Gewerk — und verschwände
    genau aus der Board-Sicht, in der sie geplant wurde (Review-Fund)."""
    client, actor = _client()
    hzg = _trade("HZG")
    job = planung_service.create_termin(
        actor.id, title="Wartungsrunde", scheduled_start=T0, scheduled_end=T1,
        trade_id=hzg.id,
    )
    r = client.post(
        f"/api/planung/termine/{job.id}/serie",
        data={"intervall": "WOECHENTLICH", "anzahl": 2},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    ids = [t["id"] for t in r.json()["erzeugt"]]
    assert list(
        ServiceJob.objects.filter(id__in=ids).values_list("trade_id", flat=True)
    ) == [hzg.id, hzg.id]


@pytest.mark.django_db
def test_serie_erbt_nicht_erneut_vom_auftrag():
    """Ein bewusst ABWEICHENDES Gewerk (Sanitärtermin auf Heizungsauftrag) muss
    die Serie überleben — sonst kippte jeder Folgetermin auf das Auftragsgewerk."""
    client, actor = _client()
    hzg = _trade("HZG")
    san = _trade("SAN")
    order = _order(actor.id, trade_id=hzg.id)
    job = planung_service.create_termin(
        actor.id, work_order_id=order.id, scheduled_start=T0, scheduled_end=T1,
        trade_id=san.id,
    )
    r = client.post(
        f"/api/planung/termine/{job.id}/serie",
        data={"intervall": "WOECHENTLICH", "anzahl": 1},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    ids = [t["id"] for t in r.json()["erzeugt"]]
    assert list(
        ServiceJob.objects.filter(id__in=ids).values_list("trade_id", flat=True)
    ) == [san.id]


# --- Fremdschlüssel-Vorprüfung ---------------------------------------------

@pytest.mark.django_db
def test_unbekanntes_gewerk_ist_422_nicht_500():
    client, actor = _client()
    job = einsatz_service.create_service_job(actor.id, title="Begehung")
    fremd = uuid.uuid4()

    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"trade_id": str(fremd)},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content
    assert "gewerk" in r.json()["detail"].lower()

    r = client.post(
        "/api/planung/einsaetze",
        data={"title": "Begehung", "trade_id": str(fremd)},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content

    r = client.patch(
        f"/api/planung/termine/{job.id}",
        data={"trade_id": str(fremd)},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


# --- Rechte -----------------------------------------------------------------

@pytest.mark.django_db
def test_monteur_darf_das_gewerk_nicht_setzen():
    """Das Gewerk ist Dispositionsdatum — es steckt in der Einsatznummer und
    entscheidet, in wessen Board-Sicht der Termin auftaucht."""
    dispo = make_app_user("Dispo")
    hzg = _trade("HZG")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    job = einsatz_service.create_service_job(dispo.id, title="Begehung")
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    r = client.patch(
        f"/api/planung/einsaetze/{job.id}",
        data={"trade_id": str(hzg.id)},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    assert ServiceJob.objects.get(id=job.id).trade_id is None


# --- Qualifikations-Bedienfläche am Einsatz (fail-closed) -------------------

@pytest.mark.django_db
def test_einsatz_bedarf_lesen_und_setzen():
    client, actor = _client()
    job = einsatz_service.create_service_job(actor.id, title="Begehung")
    gas = client.post(
        "/api/planung/qualifikationen",
        {"code": "GAS", "label": "Gasschein", "expires": True},
        content_type=JSON,
    ).json()

    r = client.get(f"/api/planung/einsaetze/{job.id}/qualifikationen")
    assert r.status_code == 200, r.content
    assert r.json() == []

    r = client.put(
        f"/api/planung/einsaetze/{job.id}/qualifikationen",
        {"qualification_ids": [gas["id"]]},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert [q["code"] for q in r.json()] == ["GAS"]

    # Vollersetzung: leere Liste räumt den Bedarf wieder ab.
    r = client.put(
        f"/api/planung/einsaetze/{job.id}/qualifikationen",
        {"qualification_ids": []},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json() == []


@pytest.mark.django_db
def test_monteur_kommt_an_den_bedarf_nicht_heran():
    """`require` ist fail-closed: Scope EIGENE ist kein 'ALLE'. Deshalb blendet
    die Mappe die Bedienfläche für den Monteur gar nicht erst ein — ein Knopf,
    der garantiert 403 liefert, ist keine Funktion."""
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    job = einsatz_service.create_service_job(dispo.id, title="Begehung")
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )

    r = client.get(f"/api/planung/einsaetze/{job.id}/qualifikationen")
    assert r.status_code == 403, r.content
    r = client.put(
        f"/api/planung/einsaetze/{job.id}/qualifikationen",
        {"qualification_ids": []},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
