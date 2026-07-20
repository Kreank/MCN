"""Anruf-Durchstich: POST /api/planung/anruf.

Der Kern dieser Suite ist `test_monteur_kann_losfahren`. Alles andere prüft
Einzelteile; dieser eine Test prüft den Zweck: Ein Termin, der sich planen, aber
nicht ausführen lässt, wäre schlimmer als kein Durchstich. Der DB-Trigger
`trg_service_job_execution_gate` ist die Instanz, die das entscheidet — nicht
unsere Annahme darüber.
"""
from zoneinfo import ZoneInfo

import pytest

from db_core.betriebszeit import BETRIEBS_TZ
from db_core.services import einsatz as einsatz_service


def _anruf_payload(**overrides):
    payload = {
        "person": {
            "salutation": "Herr",
            "first_name": "Max",
            "last_name": "Mustermann",
            "phone": "030 123456",
        },
        "property": {
            "property_type": "EINFAMILIENHAUS",
            "street": "Hauptstraße",
            "house_number": "5",
            "postal_code": "10115",
            "city": "Berlin",
        },
        "auftrag": {"title": "Therme heizt nicht", "priority": "DRINGEND"},
        "termin": {"scheduled_start": "2026-08-03T08:00:00+02:00"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_anruf_happy(admin_client, db):
    """Ein Aufruf legt Person, Liegenschaft, freigegebenen Auftrag und geplanten
    Termin an — und KEINEN Vorgang."""
    from db_core.models import (
        ContactPoint,
        Party,
        Property,
        PropertyPartyRole,
        ServiceCase,
        ServiceJob,
        WorkOrder,
        WorkOrderParty,
    )

    faelle_vorher = ServiceCase.objects.count()

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()

    party = Party.objects.get(id=body["party_id"])
    assert party.display_name == "Max Mustermann"
    assert ContactPoint.objects.filter(party_id=party.id, contact_type="PHONE").exists()

    prop = Property.objects.get(id=body["property_id"])
    assert prop.property_type == "EINFAMILIENHAUS"
    assert "Hauptstraße" in prop.name
    assert PropertyPartyRole.objects.filter(
        property_id=prop.id, party_id=party.id, role="PROPERTY_OWNER"
    ).exists()

    order = WorkOrder.objects.get(id=body["work_order_id"])
    assert order.status == "FREIGEGEBEN"
    assert order.property_id == prop.id
    assert order.priority == "DRINGEND"
    # Die drei Tore aus recheck_work_order_gates — erfüllt, nicht umgangen.
    assert order.order_evidence_reference
    assert "Telefonisch beauftragt" in order.order_evidence_reference
    assert "Herr Max Mustermann" in order.order_evidence_reference
    assert order.responsibility_scope == "PRIVATE_UNIT"
    assert order.responsibility_confirmed_at is not None
    assert WorkOrderParty.objects.filter(
        work_order_id=order.id, party_id=party.id, role="PRINCIPAL", is_primary=True
    ).exists()

    job = ServiceJob.objects.get(id=body["service_job_id"])
    assert job.status == "GEPLANT"
    assert job.work_order_id == order.id
    assert job.on_site_contact_party_id == party.id
    assert body["im_rueckstand"] is False
    assert body["order_number"].startswith("AU-")
    assert body["job_number"].startswith("E-")

    # Der Eingangskorb bleibt außen vor: Wer am Telefon terminiert, erzeugt
    # keine Meldung, die noch jemand sichten müsste.
    assert ServiceCase.objects.count() == faelle_vorher
    assert order.service_case_id is None


@pytest.mark.django_db
def test_freigabe_tore_halten_der_db_stand(admin_client, db):
    """Die Freigabe passiert `trg_work_order_gates` wirklich — nicht nur unsere
    Annahme darüber.

    Der Trigger ist DEFERRABLE INITIALLY DEFERRED, feuert also erst beim COMMIT.
    Unter `django_db` committet nichts, die Testtransaktion wird zurückgerollt —
    ein naiver Test würde die Tore nie berühren und trotzdem grün sein.
    `SET CONSTRAINTS ALL IMMEDIATE` holt die aufgeschobene Prüfung in die
    Testtransaktion. Das ist `transaction=True` vorzuziehen: Der echte COMMIT
    scheitert anschließend am No-Delete-Schutz beim Teardown-Flush und
    produziert einen Fehler, der nichts mit dieser Prüfung zu tun hat.
    """
    from django.db import connection

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content

    # Feuert alle aufgeschobenen Constraint-Trigger sofort. Wäre eines der drei
    # Tore (Nachweis, Verantwortung, PRINCIPAL) unerfüllt, knallt es hier.
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.django_db
def test_monteur_kann_losfahren(admin_client, db, app_user):
    """Der Zweck des ganzen Slices: Der Einsatz lässt sich auf UNTERWEGS heben.

    `trg_service_job_execution_gate` erlaubt das nur bei einem Auftrag in
    FREIGEGEBEN/IN_PLANUNG/IN_AUSFUEHRUNG. Bliebe der Auftrag im ENTWURF, stünde
    der Monteur am Termintag vor der Tür und käme nicht weiter — genau der
    Zustand, den dieser Durchstich verhindern soll.
    """
    from db_core.models import ServiceJob

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    job_id = r.json()["service_job_id"]

    einsatz_service.advance_status(
        app_user.id, service_job_id=job_id, to_status="BESTAETIGT"
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job_id, to_status="UNTERWEGS"
    )

    assert ServiceJob.objects.get(id=job_id).status == "UNTERWEGS"


@pytest.mark.django_db
def test_zeit_ohne_offset_gilt_als_betriebszeit(admin_client, db):
    """„08:00" ohne Offset ist 08:00 in Berlin, nicht in UTC.

    `<input type="datetime-local">` liefert keinen Offset, und settings.TIME_ZONE
    ist bewusst UTC — ohne den Typ `Betriebszeitpunkt` läge der Termin zwei
    Stunden daneben (im Winter eine). Genau dieser Versatz war b4e24e8; dieser
    Test verhindert, dass ausgerechnet der neue Endpunkt ihn wieder aufreißt.
    """
    from db_core.models import ServiceJob

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(termin={"scheduled_start": "2026-08-03T08:00:00"}),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content

    job = ServiceJob.objects.get(id=r.json()["service_job_id"])
    # 08:00 Berlin im Sommer (MESZ, UTC+2) == 06:00 UTC. Beide Seiten prüfen:
    # Die Berliner Stunde allein wäre auch dann 8, wenn irgendwo doppelt
    # umgerechnet würde.
    assert job.scheduled_start.astimezone(BETRIEBS_TZ).hour == 8
    assert job.scheduled_start.astimezone(ZoneInfo("UTC")).hour == 6


@pytest.mark.django_db
def test_ohne_zeit_landet_im_rueckstand(admin_client, db):
    """„Nächste Woche irgendwann": Der Termin bleibt UNGEPLANT, der Auftrag wird
    trotzdem freigegeben — die Freigabe hängt nicht am Termin."""
    from db_core.models import ServiceJob, WorkOrder

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(termin=None),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()

    job = ServiceJob.objects.get(id=body["service_job_id"])
    assert job.status == "UNGEPLANT"
    assert job.scheduled_start is None
    assert body["im_rueckstand"] is True
    assert WorkOrder.objects.get(id=body["work_order_id"]).status == "FREIGEGEBEN"


@pytest.mark.django_db
def test_bestehender_kontakt_wird_referenziert(admin_client, db):
    """Dedup: Ein bekannter Anrufer wird referenziert, nicht dupliziert — und
    bekommt am fremden Kontakt keine neuen Kontaktwege verpasst."""
    from db_core.models import ContactPoint, Party

    erst = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(),
        content_type="application/json",
    )
    assert erst.status_code == 201, erst.content
    party_id = erst.json()["party_id"]
    wege_vorher = ContactPoint.objects.filter(party_id=party_id).count()
    parties_vorher = Party.objects.count()

    zweit = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(
            person={"existing_party_id": party_id, "phone": "030 999999"},
            auftrag={"title": "Zweiter Schaden"},
        ),
        content_type="application/json",
    )
    assert zweit.status_code == 201, zweit.content
    assert zweit.json()["party_id"] == party_id
    assert Party.objects.count() == parties_vorher
    assert ContactPoint.objects.filter(party_id=party_id).count() == wege_vorher


def _weg_property():
    """Eine WEG hat Gemeinschafts- UND Sondereigentum — der Verantwortungsbereich
    ist dort nie aus dem Objekttyp allein ableitbar."""
    return {
        "property_type": "WEG",
        "street": "Ringstraße",
        "house_number": "12",
        "postal_code": "10115",
        "city": "Berlin",
    }


@pytest.mark.django_db
def test_weg_ohne_scope_422(admin_client, db):
    """Nur beim EFH ist der Verantwortungsbereich eindeutig. Sonst wird er
    verlangt statt geraten — eine falsche Annahme landet später beim falschen
    Kostenträger auf der Rechnung."""
    from db_core.models import Party

    parties_vorher = Party.objects.count()

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(property=_weg_property()),
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Verantwortungsbereich" in r.json()["detail"]
    assert Party.objects.count() == parties_vorher


@pytest.mark.django_db
def test_weg_mit_scope_geht_durch(admin_client, db):
    from db_core.models import WorkOrder

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(
            property=_weg_property(),
            auftrag={
                "title": "Dachrinne verstopft",
                "responsibility_scope": "COMMON_PROPERTY",
            },
        ),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    order = WorkOrder.objects.get(id=r.json()["work_order_id"])
    assert order.responsibility_scope == "COMMON_PROPERTY"
    assert order.status == "FREIGEGEBEN"


@pytest.mark.django_db
def test_notfall_ohne_scope_geht_durch(admin_client, db):
    """A-23 Gefahrenabwehr: Beim Wasserrohrbruch um 23 Uhr wird nicht erst
    geklärt, ob das Rohr Gemeinschafts- oder Sondereigentum ist. Die DB lässt die
    Freigabe im Notfall ohne bestätigte Verantwortung zu — der Durchstich nutzt
    das, statt den dringendsten Fall an einer Formalie scheitern zu lassen."""
    from db_core.models import WorkOrder

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(
            property=_weg_property(),
            auftrag={"title": "Wasserrohrbruch Keller", "is_emergency": True},
        ),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    order = WorkOrder.objects.get(id=r.json()["work_order_id"])
    assert order.status == "FREIGEGEBEN"
    assert order.is_emergency is True
    assert order.responsibility_scope == "UNKNOWN"
    assert order.responsibility_confirmed_at is None


@pytest.mark.django_db
def test_disposition_darf_den_durchstich(client_with_role, db):
    """Die Rolle, die am Telefon sitzt, muss den Endpunkt auch benutzen können.

    Der Endpunkt verlangt ausdrücklich `workflow.FREIGEBEN` — sonst wäre er ein
    Bypass an `/status` vorbei. Die Startmatrix 0026 gab DISPOSITION dieses Recht
    nicht; Migration 0120 holt es nach (User-Entscheidung 2026-07-20). Dieser
    Test hält beides zusammen: Fällt die Migration weg, schlägt er fehl — und
    zwar hier, nicht erst beim ersten echten Anruf.
    """
    from db_core.models import WorkOrder

    c = client_with_role("DISPOSITION")
    r = c.post(
        "/api/planung/anruf",
        data=_anruf_payload(),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert WorkOrder.objects.get(id=r.json()["work_order_id"]).status == "FREIGEGEBEN"


@pytest.mark.django_db
def test_ohne_freigabe_recht_403(client_with_role, db):
    """Gegenprobe: Genau das Fehlen von FREIGEBEN muss den Durchstich stoppen.

    Eine bestehende Rolle als Negativfall zu nehmen trägt nicht: MONTEUR etwa
    scheitert schon an `identity.ANLEGEN` und zusätzlich an `row_scope EIGENE` —
    der Test wäre grün, ohne die FREIGEBEN-Zeile je zu berühren. Man könnte sie
    aus dem Endpunkt löschen und nichts würde rot. Deshalb hier DISPOSITION
    (hat alles, was der Endpunkt sonst braucht) und **nur** FREIGEBEN entzogen:
    So bleibt genau eine Variable übrig.
    """
    from django.db import connection

    c = client_with_role("DISPOSITION")
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE security.role_permission
               SET allowed = false
             WHERE role_code = 'DISPOSITION'
               AND module    = 'workflow'
               AND action    = 'FREIGEBEN'
            """
        )

    r = c.post(
        "/api/planung/anruf",
        data=_anruf_payload(),
        content_type="application/json",
    )
    assert r.status_code == 403, r.content
    detail = r.json()["detail"]
    assert "FREIGEBEN" in detail and "workflow" in detail, detail


@pytest.mark.django_db
def test_rollback_bei_fehler_im_termin(admin_client, db):
    """Atomarität: Scheitert der letzte Schritt, bleiben keine Waisen zurück.

    Aufträge und Einsätze verbrauchen GoBD-Belegnummern; ein halb angelegter
    Durchstich ließe sich wegen des No-Delete-Schutzes nicht mehr aufräumen.
    """
    from db_core.models import Party, Property, WorkOrder

    parties_vorher = Party.objects.count()
    props_vorher = Property.objects.count()
    orders_vorher = WorkOrder.objects.count()

    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(
            # Ende vor Beginn → _pruefe_zeitraum wirft im letzten Schritt.
            termin={
                "scheduled_start": "2026-08-03T10:00:00+02:00",
                "scheduled_end": "2026-08-03T08:00:00+02:00",
            }
        ),
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert Party.objects.count() == parties_vorher
    assert Property.objects.count() == props_vorher
    assert WorkOrder.objects.count() == orders_vorher


@pytest.mark.django_db
def test_leerer_titel_422(admin_client, db):
    r = admin_client.post(
        "/api/planung/anruf",
        data=_anruf_payload(auftrag={"title": "   "}),
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_ohne_recht_403(client_with_role, db):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/planung/anruf",
        data=_anruf_payload(),
        content_type="application/json",
    )
    assert r.status_code == 403, r.content
