"""Baustellenbericht am freien Termin (workflow.site_report, Migration 0064).

Deckt ab:
* Bericht am **freien Termin** (Einsatz ohne Auftrag) anlegen, fotografieren,
  unterschreiben lassen — und die Unveränderlichkeit danach,
* Bericht am **auftragsgebundenen** Einsatz: der Auftrag wird abgeleitet, der
  Bericht erscheint auch in der Auftragsliste (unveränderte Auftragssicht),
* **Anker**: weder Auftrag noch Einsatz → abgelehnt (Service UND DB-CHECK),
* **Konsistenz** Einsatz ↔ Auftrag (Service UND DB-Trigger),
* **Rechte/row_scope**: Der Monteur schreibt und besiegelt am eigenen Einsatz;
  fremder Einsatz/Bericht → 404; die Auftragssicht bleibt ihm verwehrt (403).
"""
import base64
import uuid
from datetime import datetime, timezone as dt_timezone
from hashlib import sha256

import pytest
from django import db as django_db
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import SiteReport
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service

from .conftest import make_app_user, make_role_user

JSON = "application/json"
T0 = datetime(2026, 7, 15, 8, 0, tzinfo=dt_timezone.utc)

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        payload = bytes(data)
        self.objects[key] = payload
        return storage_module.ObjectInfo(
            storage_key=key, sha256=sha256(payload).hexdigest(), size_bytes=len(payload)
        )

    def get_object(self, key):
        if key not in self.objects:
            raise storage_module.StorageError(key)
        return self.objects[key]

    def remove_object(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    return fake


def _client(role="ADMINISTRATION"):
    user, app_user = make_role_user(role)
    client = Client()
    client.force_login(user)
    return client, app_user


def _property(actor_id, name="Begehungsobjekt"):
    return property_service.create_property(
        actor_id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )


def _order(actor_id, obj=None, titel="Sockelrisse setzen"):
    obj = obj or _property(actor_id, name="Auftragshaus")
    return auftrag_service.create_work_order(
        actor_id, property_id=obj.id, title=titel
    )


def _sign_payload(name="Klara Kundin", png=PNG_1x1):
    return {
        "signed_by_name": name,
        "signature_png_base64": base64.b64encode(png).decode("ascii"),
    }


def _bericht(**felder):
    daten = {"report_date": "2026-07-15", "activity_text": "Keller begangen."}
    daten.update(felder)
    return daten


# --- Bericht am freien Termin ----------------------------------------------

@pytest.mark.django_db
def test_bericht_am_freien_termin_anlegen():
    client, actor = _client()
    job = einsatz_service.create_service_job(actor.id, title="Begehung Keller")
    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id), weather="regnerisch"),
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["work_order_id"] is None
    assert body["service_job_id"] == str(job.id)
    assert body["status"] == "ENTWURF"

    liste = client.get(f"/api/workflow/site_reports?service_job_id={job.id}").json()
    assert liste["total"] == 1
    assert liste["items"][0]["id"] == body["id"]


@pytest.mark.django_db
def test_bericht_am_freien_termin_mit_foto_und_unterschrift(fake_storage):
    client, actor = _client()
    obj = _property(actor.id)
    job = einsatz_service.create_service_job(
        actor.id, title="Begehung Kellerabdichtung", property_id=obj.id,
        scheduled_start=T0,
    )
    rid = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    ).json()["id"]

    foto = client.post(
        "/api/content/files",
        data={
            "datei": SimpleUploadedFile("keller.png", PNG_1x1, content_type="image/png"),
            "site_report_id": rid,
            "link_category": "FOTO_VORHER",
        },
    )
    assert foto.status_code == 201, foto.content

    r = client.post(
        f"/api/workflow/site_reports/{rid}/sign",
        data=_sign_payload(),
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "UNTERZEICHNET"
    assert body["signature_file_id"] is not None
    assert body["work_order_id"] is None

    # Unterzeichnet ⇒ eingefroren, auch am freien Termin.
    r2 = client.put(
        f"/api/workflow/site_reports/{rid}",
        data={"remarks": "nachträglich"},
        content_type=JSON,
    )
    assert r2.status_code == 422, r2.content


@pytest.mark.django_db
def test_einsatzbezug_des_freien_berichts_ist_unveraenderlich():
    client, actor = _client()
    job = einsatz_service.create_service_job(actor.id, title="Begehung")
    anderer = einsatz_service.create_service_job(actor.id, title="Andere Begehung")
    rid = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    ).json()["id"]

    r = client.put(
        f"/api/workflow/site_reports/{rid}",
        data={"service_job_id": str(anderer.id)},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content
    # Leeren ebenso (der Anker risse sonst auf).
    r = client.put(
        f"/api/workflow/site_reports/{rid}",
        data={"service_job_id": None},
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


# --- Auftragsgebundener Einsatz: Auftrag wird abgeleitet --------------------

@pytest.mark.django_db
def test_bericht_am_auftragseinsatz_erbt_den_auftrag():
    client, actor = _client()
    order = _order(actor.id)
    job = einsatz_service.create_service_job(actor.id, work_order_id=order.id)
    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    assert r.json()["work_order_id"] == str(order.id)

    # Er erscheint in BEIDEN Sichten — Auftrag und Einsatz.
    per_auftrag = client.get(
        f"/api/workflow/site_reports?work_order_id={order.id}"
    ).json()
    per_einsatz = client.get(
        f"/api/workflow/site_reports?service_job_id={job.id}"
    ).json()
    assert per_auftrag["total"] == 1
    assert per_einsatz["total"] == 1


@pytest.mark.django_db
def test_reiner_auftragsbericht_unveraendert():
    """Der Bestandsfall: Bericht nur am Auftrag, ohne Einsatz."""
    client, actor = _client()
    order = _order(actor.id)
    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(work_order_id=str(order.id)),
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    assert r.json()["service_job_id"] is None
    assert r.json()["work_order_id"] == str(order.id)


# --- Anker und Konsistenz ---------------------------------------------------

@pytest.mark.django_db
def test_ohne_anker_422(db):
    client, _ = _client()
    r = client.post("/api/workflow/site_reports", data=_bericht(), content_type=JSON)
    assert r.status_code == 422, r.content
    assert "Bezug" in r.json()["detail"]


@pytest.mark.django_db
def test_anker_check_greift_auch_ohne_service(app_user):
    """Der Service ist nicht die letzte Instanz: der DB-CHECK lehnt einen Bericht
    ohne jeden Bezug ebenfalls ab."""
    with pytest.raises(django_db.Error):
        with business_transaction(app_user.id):
            SiteReport.objects.create(
                id=uuid.uuid4(), work_order_id=None, service_job_id=None,
                report_date="2026-07-15", author_id=app_user.id,
                activity_text="Ohne Anker", status="ENTWURF", version=1,
            )


@pytest.mark.django_db
def test_einsatz_eines_fremden_auftrags_422():
    client, actor = _client()
    order_a = _order(actor.id, titel="Auftrag A")
    order_b = _order(actor.id, titel="Auftrag B")
    job_b = einsatz_service.create_service_job(actor.id, work_order_id=order_b.id)
    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(work_order_id=str(order_a.id), service_job_id=str(job_b.id)),
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_freier_termin_mit_auftrag_kombiniert_422():
    """Ein freier Termin trägt keinen Auftrag — ein Bericht daran auch nicht."""
    client, actor = _client()
    order = _order(actor.id)
    job = einsatz_service.create_service_job(actor.id, title="Begehung")
    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(work_order_id=str(order.id), service_job_id=str(job.id)),
        content_type=JSON,
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_konsistenz_trigger_greift_auch_ohne_service(app_user):
    """DB-Trigger `check_site_report_anchor`: ein Bericht darf den Auftrag seines
    Einsatzes nicht verschweigen (er wäre sonst über die Auftragsliste unsichtbar,
    obwohl er zur Baustelle gehört)."""
    order = _order(app_user.id)
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    with pytest.raises(django_db.Error):
        with business_transaction(app_user.id):
            SiteReport.objects.create(
                id=uuid.uuid4(), work_order_id=None, service_job_id=job.id,
                report_date="2026-07-15", author_id=app_user.id,
                activity_text="Auftrag verschwiegen", status="ENTWURF", version=1,
            )


@pytest.mark.django_db
def test_liste_braucht_genau_einen_filter():
    client, actor = _client()
    order = _order(actor.id)
    job = einsatz_service.create_service_job(actor.id, work_order_id=order.id)
    assert client.get("/api/workflow/site_reports").status_code == 422
    r = client.get(
        f"/api/workflow/site_reports?work_order_id={order.id}&service_job_id={job.id}"
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_liste_unbekannter_einsatz_404(db):
    client, _ = _client()
    r = client.get(f"/api/workflow/site_reports?service_job_id={uuid.uuid4()}")
    assert r.status_code == 404, r.content


# --- Rechte / row_scope EIGENE (Monteur) ------------------------------------

def _monteur_am_termin(dispo, *, titel="Meine Begehung", work_order_id=None):
    """Freier (oder auftragsgebundener) Termin mit zugewiesenem Monteur."""
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    job = einsatz_service.create_service_job(
        dispo.id,
        title=None if work_order_id else titel,
        work_order_id=work_order_id,
        scheduled_start=T0,
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    return client, monteur, job


@pytest.mark.django_db
def test_monteur_schreibt_und_besiegelt_am_eigenen_freien_termin(fake_storage):
    dispo = make_app_user("Dispo")
    client, _monteur, job = _monteur_am_termin(dispo)

    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    rid = r.json()["id"]

    # Ändern im Entwurf …
    r = client.put(
        f"/api/workflow/site_reports/{rid}",
        data={"remarks": "Feuchte Wand hinter dem Regal."},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content

    # … und die Abnahme vor Ort.
    r = client.post(
        f"/api/workflow/site_reports/{rid}/sign",
        data=_sign_payload(),
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "UNTERZEICHNET"

    # Eigene Liste + Detail sind für ihn sichtbar.
    assert (
        client.get(f"/api/workflow/site_reports?service_job_id={job.id}").json()["total"]
        == 1
    )
    assert client.get(f"/api/workflow/site_reports/{rid}").status_code == 200


@pytest.mark.django_db
def test_monteur_sieht_fremden_bericht_nicht():
    dispo = make_app_user("Dispo")
    client, _monteur, _job = _monteur_am_termin(dispo)

    fremder_job = einsatz_service.create_service_job(dispo.id, title="Fremde Begehung")
    fremder = report_service.create_report(
        dispo.id, service_job_id=fremder_job.id, report_date="2026-07-15",
        activity_text="Fremd",
    )

    # Liste am fremden Einsatz, Detail des fremden Berichts: 404, nicht 403.
    assert (
        client.get(
            f"/api/workflow/site_reports?service_job_id={fremder_job.id}"
        ).status_code
        == 404
    )
    assert client.get(f"/api/workflow/site_reports/{fremder.id}").status_code == 404
    # Schreiben ebenso.
    assert (
        client.put(
            f"/api/workflow/site_reports/{fremder.id}",
            data={"remarks": "x"},
            content_type=JSON,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/workflow/site_reports/{fremder.id}/sign",
            data=_sign_payload(),
            content_type=JSON,
        ).status_code
        == 404
    )
    # Und anlegen am fremden Einsatz auch nicht.
    assert (
        client.post(
            "/api/workflow/site_reports",
            data=_bericht(service_job_id=str(fremder_job.id)),
            content_type=JSON,
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_monteur_ohne_einsatzbezug_darf_nicht_anlegen():
    """Ein Bericht allein am Auftrag ist Dispositionssache — der Monteur hat dort
    keine Zuweisung, an der seine Sicht hängen könnte (fail-closed → 403)."""
    dispo = make_app_user("Dispo")
    client, _monteur, _job = _monteur_am_termin(dispo)
    order = _order(dispo.id)
    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(work_order_id=str(order.id)),
        content_type=JSON,
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_monteur_darf_auftragssicht_eines_fremden_objekts_nicht_lesen():
    """Objektsicht (0099): Die Auftragssicht ist für 'EIGENE' nicht mehr pauschal
    gesperrt — sie ist auf **meine Objekte** begrenzt. Ein Auftrag an einem fremden
    Objekt ist **404** (die Existenz wird nicht verraten), nicht mehr 403.

    Der Monteur hier hängt an einem freien Termin ohne Liegenschaft; der Auftrag
    gehört zu einem Objekt, an dem er nie war."""
    dispo = make_app_user("Dispo")
    client, _monteur, _job = _monteur_am_termin(dispo)
    order = _order(dispo.id)
    r = client.get(f"/api/workflow/site_reports?work_order_id={order.id}")
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_monteur_sieht_reinen_auftragsbericht_an_seinem_objekt():
    """**Der Kern der Objektsicht (0099)** — und die Umkehr der früheren Regel.

    Vorher: „Ein Bericht, der nur am Auftrag hängt (ohne Einsatz), bleibt ihm
    verborgen — seine Sicht hängt allein an der Zuweisung." Das war genau der Fehler:
    Der Monteur fuhr zur Meldung „Heizkörper kalt" und fand den Bericht von
    vorgestern nicht, in dem stand, dass am Nachbar-Heizkörper ein Leck war.

    Jetzt: Der Monteur ist einem Einsatz DES AUFTRAGS zugewiesen → dessen
    Liegenschaft ist **sein Objekt** → er liest jeden Bericht daran, auch den ohne
    Einsatzbezug. **Ändern** darf er ihn weiterhin nicht (dafür bräuchte er die
    Einsatzzuweisung)."""
    dispo = make_app_user("Dispo")
    order = _order(dispo.id)
    client, _monteur, _job = _monteur_am_termin(dispo, work_order_id=order.id)
    nur_auftrag = report_service.create_report(
        dispo.id, work_order_id=order.id, report_date="2026-07-15",
        activity_text="Nur am Auftrag",
    )
    r = client.get(f"/api/workflow/site_reports/{nur_auftrag.id}")
    assert r.status_code == 200, r.content
    assert r.json()["activity_text"] == "Nur am Auftrag"

    # Lesen ja — schreiben nein. Die Schreibgrenze bleibt die Einsatzzuweisung.
    r = client.put(
        f"/api/workflow/site_reports/{nur_auftrag.id}",
        data={"remarks": "Heimlich geändert"},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_monteur_darf_bericht_nicht_umhaengen():
    dispo = make_app_user("Dispo")
    order = _order(dispo.id)
    client, _monteur, job = _monteur_am_termin(dispo, work_order_id=order.id)
    rid = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    ).json()["id"]
    fremder_job = einsatz_service.create_service_job(dispo.id, work_order_id=order.id)
    r = client.put(
        f"/api/workflow/site_reports/{rid}",
        data={"service_job_id": str(fremder_job.id)},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    # Den UNVERÄNDERTEN Wert mitzuschicken ist dagegen erlaubt (Formulare senden
    # ihre Felder vollständig).
    r = client.put(
        f"/api/workflow/site_reports/{rid}",
        data={"service_job_id": str(job.id), "remarks": "ok"},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["remarks"] == "ok"


@pytest.mark.django_db
def test_nur_lesen_darf_keinen_bericht_anlegen():
    client, actor = _client("NUR_LESEN")
    dispo = make_app_user("Dispo")
    job = einsatz_service.create_service_job(dispo.id, title="Begehung")
    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_ohne_anmeldung_401():
    _, actor = _client()
    job = einsatz_service.create_service_job(actor.id, title="Begehung")
    anonym = Client()
    r = anonym.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    )
    assert r.status_code in (401, 403)
    r = anonym.get(f"/api/workflow/site_reports?service_job_id={job.id}")
    assert r.status_code in (401, 403)


# --- Fotos: Zeilenbegrenzung der Datei-API (Review B1/B3) -------------------

def _foto(client, *, name="foto.png", **ziel):
    daten = {"datei": SimpleUploadedFile(name, PNG_1x1, content_type="image/png")}
    daten.update({k: str(v) for k, v in ziel.items()})
    daten["link_category"] = "FOTO_VORHER"
    return client.post("/api/content/files", data=daten)


@pytest.mark.django_db
def test_monteur_foto_am_eigenen_bericht_hochladen_und_wiedersehen(fake_storage):
    """Der ganze Zweck des Slices: Foto vom Zustand vor Ort — und der Monteur
    sieht es danach auch wieder (Liste + Download)."""
    dispo = make_app_user("Dispo")
    client, _monteur, job = _monteur_am_termin(dispo)
    rid = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    ).json()["id"]

    r = _foto(client, site_report_id=rid)
    assert r.status_code == 201, r.content
    fid = r.json()["file_id"]

    liste = client.get(f"/api/content/files?site_report_id={rid}")
    assert liste.status_code == 200, liste.content
    assert liste.json()["total"] == 1
    assert client.get(f"/api/content/files/{fid}/download").status_code == 200
    # Am eigenen Einsatz ebenso.
    assert _foto(client, name="einsatz.png", service_job_id=job.id).status_code == 201
    assert client.get(f"/api/content/files?service_job_id={job.id}").status_code == 200


@pytest.mark.django_db
def test_monteur_darf_kein_foto_an_fremden_bericht_haengen(fake_storage):
    """Review B1: `require_create` wertete den row_scope nicht aus — der Monteur
    konnte Bildmaterial in den Nachweis einer fremden Baustelle einschleusen."""
    dispo = make_app_user("Dispo")
    client, _monteur, _job = _monteur_am_termin(dispo)

    fremder_job = einsatz_service.create_service_job(dispo.id, title="Fremde Begehung")
    fremder = report_service.create_report(
        dispo.id, service_job_id=fremder_job.id, report_date="2026-07-15",
        activity_text="Fremd",
    )
    order = _order(dispo.id)

    assert _foto(client, site_report_id=fremder.id).status_code == 404
    assert _foto(client, service_job_id=fremder_job.id).status_code == 404
    # HOCHLADEN an einen Auftrag ist für 'EIGENE' ganz zu — auch am eigenen Objekt
    # (die Objektsicht ist eine LESE-Sicht): 403, fail-closed.
    assert _foto(client, work_order_id=order.id).status_code == 403
    assert (
        client.get(f"/api/content/files?site_report_id={fremder.id}").status_code == 404
    )
    # LESEN am Auftrag ist seit der Objektsicht (0099) grundsätzlich möglich — hier
    # aber an einem FREMDEN Objekt, also **404** (nicht mehr 403: die Zielart ist
    # zulässig, das Objekt ist es nicht — die Existenz wird nicht verraten).
    assert client.get(f"/api/content/files?work_order_id={order.id}").status_code == 404


@pytest.mark.django_db
def test_monteur_kann_fremde_datei_nicht_herunterladen(fake_storage):
    dispo = make_app_user("Dispo")
    client, _monteur, _job = _monteur_am_termin(dispo)
    dispo_client, _ = _client()
    order = _order(dispo.id)
    fid = _foto(dispo_client, work_order_id=order.id).json()["file_id"]
    assert client.get(f"/api/content/files/{fid}/download").status_code == 404


# --- Versiegelung des Beweismittelbündels (Review B2, Migration 0065) -------

@pytest.mark.django_db
def test_unterzeichneter_bericht_nimmt_keine_fotos_mehr_auf(fake_storage):
    client, actor = _client()
    job = einsatz_service.create_service_job(actor.id, title="Begehung")
    rid = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    ).json()["id"]
    link_id = _foto(client, site_report_id=rid).json()["link_id"]

    assert (
        client.post(
            f"/api/workflow/site_reports/{rid}/sign",
            data=_sign_payload(),
            content_type=JSON,
        ).status_code
        == 200
    )

    # Nachschieben: gesperrt (422, nicht 201).
    r = _foto(client, name="nachtrag.png", site_report_id=rid)
    assert r.status_code == 422, r.content
    # Entfernen: ebenso gesperrt (422, nicht 204).
    r = client.delete(f"/api/content/links/{link_id}")
    assert r.status_code == 422, r.content
    # Das Foto ist noch da.
    assert client.get(f"/api/content/files?site_report_id={rid}").json()["total"] == 1


@pytest.mark.django_db
def test_versiegelung_greift_auch_ohne_service(app_user, fake_storage):
    """DB-Trigger direkt: auch am Service vorbei kein Anhang an einem
    unterzeichneten Bericht."""
    from db_core.models import File, FileLink

    job = einsatz_service.create_service_job(app_user.id, title="Begehung")
    report = report_service.create_report(
        app_user.id, service_job_id=job.id, report_date="2026-07-15",
        activity_text="Begangen",
    )
    report_service.sign_report(
        app_user.id, report_id=report.id, signed_by_name="Klara",
        signature_png=PNG_1x1,
    )
    datei = File.objects.filter(mime_type="image/png").first()
    with pytest.raises(django_db.Error):
        with business_transaction(app_user.id):
            FileLink.objects.create(
                id=uuid.uuid4(), file_id=datei.id, site_report_id=report.id,
                link_category="FOTO_NACHHER", created_by_id=app_user.id,
            )


# --- Kontakt: der Bericht trägt keine eigene Liegenschaft --------------------

@pytest.mark.django_db
def test_liegenschaft_kommt_vom_anker_nicht_vom_bericht():
    """Bewusste Entscheidung (0064): Der Bericht führt kein eigenes property_id.
    Die Liegenschaft steht am Einsatz (freier Termin) bzw. am Auftrag."""
    client, actor = _client()
    obj = _property(actor.id)
    job = einsatz_service.create_service_job(
        actor.id, title="Begehung", property_id=obj.id
    )
    rid = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    ).json()["id"]
    assert "property_id" not in client.get(
        f"/api/workflow/site_reports/{rid}"
    ).json()
    detail = client.get(f"/api/planung/einsaetze/{job.id}").json()
    assert detail["property"]["id"] == str(obj.id)


@pytest.mark.django_db
def test_kontakt_am_freien_termin_bleibt_optional():
    """Ein Interessent ist noch kein Kontakt — die Begehung (und ihr Protokoll)
    muss ohne Ansprechpartner funktionieren."""
    client, actor = _client()
    job = einsatz_service.create_service_job(actor.id, title="Begehung")
    assert job.on_site_contact_party_id is None
    r = client.post(
        "/api/workflow/site_reports",
        data=_bericht(service_job_id=str(job.id)),
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    # Kontakt später nachtragen ändert am Bericht nichts.
    kontakt = identity_service.create_person(
        actor.id, first_name="Nora", last_name="Nachbar"
    )
    assert (
        client.patch(
            f"/api/planung/einsaetze/{job.id}",
            data={"on_site_contact_party_id": str(kontakt.id)},
            content_type=JSON,
        ).status_code
        == 200
    )
