"""GET /api/suche — die Abnahmeliste am Endpunkt, inklusive Rechte.

Die Suchlogik selbst prüft `db_core/tests/test_suche.py` (dort liegt das volle
Szenario). Hier geht es um das, was nur die API entscheiden kann: Anmeldepflicht,
die Übersetzung der Rechtematrix in die `Sicht` — und den Bruchfall, dass ein
Monteur (row_scope EIGENE) über die Suche an fremde Daten kommt.

Der Monteur-Test ist der wichtigste dieser Datei: Er ist der Unterschied zwischen
einer Suche und einem Datenleck.
"""
import uuid
from datetime import date

import pytest
from django.test import Client

from api.tests.conftest import make_role_user
from db_core.db_context import business_transaction
from db_core.models import AppUser, Role, RolePermission
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service
from db_core.services import rechte as rechte_service


@pytest.fixture
def welt(db):
    """Badensche Straße 53: Liegenschaft, WEG, Projekt, Vorgang, Auftrag,
    Einsatz (dem Monteur zugewiesen) und ein Angebot ohne Nummer (Entwurf)."""
    chef = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Chefin", status="ACTIVE", version=1,
    )
    u = chef.id
    obj = property_service.create_property(
        u, name="Wohnanlage Wilmersdorf", property_type="WEG",
        street="Badensche Straße", house_number="53", postal_code="10825",
        city="Berlin",
    )
    weg = identity_service.create_organization(
        u, legal_name="WEG Badensche Straße 53", organization_type="WEG",
    )
    property_service.add_party_role(
        u, property_id=obj.id, party_id=weg.id, role="COMMUNITY_OF_OWNERS",
        valid_from=date(2020, 1, 1),
    )
    melderin = identity_service.create_person(u, first_name="Erika", last_name="Meier")
    identity_service.add_contact_point(
        u, melderin.id, contact_type="EMAIL",
        value="erika.meier@hausverwaltung.test", is_primary=True,
    )
    projekt = projekt_service.create_project(
        u, name="Dachsanierung Etappe 2", property_ids=[obj.id],
    )
    vorgang = projekt_service.create_service_case(
        u, property_id=obj.id, subject="Wasserschaden Dachgeschoss",
        reported_by_party_id=melderin.id, project_id=projekt.id,
    )
    auftrag = auftrag_service.create_work_order(
        u, property_id=obj.id, title="Dachrinne erneuern", service_case_id=vorgang.id,
    )
    auftrag_service.add_work_order_party(
        u, work_order_id=auftrag.id, party_id=weg.id, role="PRINCIPAL",
        is_primary=True,
    )
    angebot = beleg_service.create_quote(
        u, property_id=obj.id, title="Dachrinne Material und Montage",
        lines=[{
            "line_type": "MATERIAL", "description": "Zinkrinne", "quantity": "12",
            "unit": "m", "unit_price": "24.00", "tax_code": "DE_19",
        }],
    )
    angebot = beleg_service.send_quote(u, quote_id=angebot.id)
    entwurf = beleg_service.create_quote(
        u, property_id=obj.id, title="Fassade Badensche — Vorabschätzung",
    )

    # Der Monteur: eigenes Login-Konto mit Rolle MONTEUR (row_scope EIGENE auf
    # workflow), ein zugewiesener und ein fremder Einsatz.
    user, monteur = make_role_user("MONTEUR")
    eigen = einsatz_service.create_service_job(
        u, work_order_id=auftrag.id, title="Gerüst stellen",
    )
    einsatz_service.assign_user(
        u, service_job_id=eigen.id, assignee_user_id=monteur.id,
    )
    fremd = einsatz_service.create_service_job(
        u, work_order_id=auftrag.id, title="Rinne montieren",
    )
    return {
        "chef": chef,
        "obj": obj, "weg": weg, "projekt": projekt, "vorgang": vorgang,
        "auftrag": auftrag, "angebot": angebot, "entwurf": entwurf,
        "monteur_user": user, "monteur": monteur, "eigen": eigen, "fremd": fremd,
    }


def _suche(client, q):
    r = client.get("/api/suche", {"q": q})
    assert r.status_code == 200, r.content
    return r.json()


def _typen(daten):
    return {t["typ"] for t in daten["treffer"]}


def _ids(daten, typ):
    return {t["id"] for t in daten["treffer"] if t["typ"] == typ}


def test_suche_verlangt_anmeldung(anonymous_client, db):
    r = anonymous_client.get("/api/suche", {"q": "Badensche"})
    assert r.status_code == 401


@pytest.mark.django_db
def test_leerer_begriff_ist_kein_fehler(admin_client):
    daten = _suche(admin_client, "")
    assert daten["treffer"] == []
    assert daten["direkttreffer"] is None
    assert daten["kategorien"] == []


@pytest.mark.django_db
def test_badensche_findet_alles_was_an_der_adresse_haengt(admin_client, welt):
    daten = _suche(admin_client, "Badensche")
    assert {"LIEGENSCHAFT", "KONTAKT", "AUFTRAG", "ANGEBOT"} <= _typen(daten)
    assert str(welt["obj"].id) in _ids(daten, "LIEGENSCHAFT")
    assert str(welt["weg"].id) in _ids(daten, "KONTAKT")
    assert str(welt["auftrag"].id) in _ids(daten, "AUFTRAG")
    # Auch der Entwurf ohne Belegnummer (quote_number IS NULL).
    assert str(welt["entwurf"].id) in _ids(daten, "ANGEBOT")


@pytest.mark.django_db
def test_strassenname_mit_hausnummer_findet_das_projekt(admin_client, welt):
    daten = _suche(admin_client, "Badensche Straße 53")
    assert str(welt["projekt"].id) in _ids(daten, "PROJEKT")
    projekt = next(t for t in daten["treffer"] if t["typ"] == "PROJEKT")
    assert projekt["rang"] == 3
    assert projekt["grund"] == "Adresse der Liegenschaft"


@pytest.mark.django_db
def test_mailadresse_findet_den_vorgang(admin_client, welt):
    daten = _suche(admin_client, "erika.meier@hausverwaltung.test")
    assert str(welt["vorgang"].id) in _ids(daten, "VORGANG")


@pytest.mark.django_db
def test_angebotsnummer_ist_direkttreffer_auf_position_eins(admin_client, welt):
    nummer = welt["angebot"].quote_number
    daten = _suche(admin_client, nummer)
    assert daten["direkttreffer"] is not None
    assert daten["direkttreffer"]["id"] == str(welt["angebot"].id)
    assert daten["treffer"][0]["id"] == str(welt["angebot"].id)
    assert daten["treffer"][0]["rang"] == 0
    assert daten["treffer"][0]["ist_direkttreffer"] is True


@pytest.mark.django_db
def test_monteur_findet_sein_objekt_aber_niemals_einen_beleg(welt):
    """DER Bruchfall — Objektsicht (0099). Alles darüber hinaus wäre ein Datenleck.

    Der Monteur ist einem Einsatz am Objekt „Badensche Straße 53" zugewiesen. Damit
    ist es **sein Objekt**, und er findet:

      * die **Liegenschaft** — inkl. **Straße** im Untertitel (genau das war der
        Auslöser: er fand seinen Einsatz, aber nicht die Adresse, zu der er fährt),
      * **Projekt**, **Vorgang** und **Auftrag** daran — auch die der Kollegen,
      * den **Kontakt** (die WEG, der Melder) — er muss anrufen können,
      * seinen **eigenen** Einsatz (der fremde bleibt weg: die Einsatzsicht hängt
        weiterhin an der ZUWEISUNG, nicht am Objekt — sonst würde ein freier Termin
        öffentlich).

    Seit **Migration 0102** findet er zusätzlich das **versendete Angebot** seines
    Objekts — er muss wissen, was beauftragt ist. **Nicht** dagegen: den ENTWURF
    (Bürokram, Inhalt noch änderbar) und **nie** eine RECHNUNG. Und in **keinem**
    Untertitel steht ein Betrag.
    """
    c = Client()
    c.force_login(welt["monteur_user"])

    daten = _suche(c, "Badensche")
    typen = _typen(daten)
    assert "LIEGENSCHAFT" in typen, daten["treffer"]
    assert "RECHNUNG" not in typen, daten["treffer"]
    # Das versendete Angebot: ja. Der Entwurf: nein.
    assert _ids(daten, "ANGEBOT") == {str(welt["angebot"].id)}, daten["treffer"]
    assert str(welt["entwurf"].id) not in _ids(daten, "ANGEBOT")
    # Kein Betrag im Untertitel — sonst wäre die Suche das Preisleck an der
    # preisfreien Beleg-API vorbei (24,00 €/m, 12 m → 288,00 €).
    angebot = next(t for t in daten["treffer"] if t["typ"] == "ANGEBOT")
    for betrag in ("24,00", "24.00", "288", "€"):
        assert betrag not in angebot["untertitel"], angebot
        assert betrag not in angebot["grund"], angebot
    assert _ids(daten, "LIEGENSCHAFT") == {str(welt["obj"].id)}
    # Die Straße steht im Untertitel — ohne sie fährt er nirgendwohin.
    liegenschaft = next(
        t for t in daten["treffer"] if t["typ"] == "LIEGENSCHAFT"
    )
    assert "Badensche Straße" in liegenschaft["untertitel"]

    # Objekthistorie: Vorgang, Auftrag, Projekt — auch die der Kollegen.
    assert _ids(daten, "VORGANG") == {str(welt["vorgang"].id)}
    assert _ids(daten, "AUFTRAG") == {str(welt["auftrag"].id)}
    assert _ids(daten, "PROJEKT") == {str(welt["projekt"].id)}
    # Kontakt: die WEG hängt als Beteiligte an seinem Objekt.
    assert str(welt["weg"].id) in _ids(daten, "KONTAKT")
    # Einsatz: weiterhin NUR der eigene (Zuweisung, nicht Objekt).
    assert _ids(daten, "EINSATZ") == {str(welt["eigen"].id)}
    assert str(welt["fremd"].id) not in _ids(daten, "EINSATZ")

    # Der Direkttreffer auf die Objektnummer öffnet ihm SEIN Objekt …
    daten = _suche(c, welt["obj"].property_number)
    assert daten["direkttreffer"] is not None
    assert daten["direkttreffer"]["id"] == str(welt["obj"].id)

    # … und die exakte Angebotsnummer öffnet ihm SEIN Angebot (0102) — der
    # Direkttreffer zieht aus derselben begrenzten Grundmenge wie die Liste.
    daten = _suche(c, welt["angebot"].quote_number)
    assert daten["direkttreffer"] is not None
    assert daten["direkttreffer"]["id"] == str(welt["angebot"].id)
    assert "RECHNUNG" not in _typen(daten), daten["treffer"]

    # Und die Kennung SEINES Einsatzes findet ihn sehr wohl.
    daten = _suche(c, welt["eigen"].job_number)
    assert daten["direkttreffer"] is not None
    assert daten["direkttreffer"]["id"] == str(welt["eigen"].id)


@pytest.mark.django_db
def test_monteur_findet_ein_fremdes_objekt_auch_ueber_die_objektnummer_nicht(welt):
    """Der Direkttreffer-Pfad ist der klassische Nebeneingang — hier ist er zu.

    Ein zweites Objekt, an dem der Monteur nie war: weder über den Namen noch über
    die **exakte Objektnummer** (Rang 0, eigener Query-Pfad!) taucht es auf. Beide
    Pfade ziehen aus derselben rechtegefilterten Grundmenge (`_basis_qs`).
    """
    fremd = property_service.create_property(
        welt["chef"].id, name="Kantstraße 42 Ladenlokal", property_type="COMMERCIAL",
        street="Kantstraße", house_number="42", postal_code="10625", city="Berlin",
    )
    c = Client()
    c.force_login(welt["monteur_user"])

    daten = _suche(c, "Kantstraße")
    assert str(fremd.id) not in _ids(daten, "LIEGENSCHAFT"), daten["treffer"]

    daten = _suche(c, fremd.property_number)
    assert daten["direkttreffer"] is None, daten["treffer"]
    assert str(fremd.id) not in _ids(daten, "LIEGENSCHAFT"), daten["treffer"]


@pytest.mark.django_db
def test_antwort_deckt_sich_mit_der_rechtematrix(welt):
    """Belege stehen genau dann in der Antwort, wenn `invoicing/LESEN` = ALLE gilt.

    Bewusst gegen die Matrix geprüft statt gegen eine fest verdrahtete
    Rollenannahme: Ändert jemand die Rechte der DISPOSITION, muss dieser Test die
    Suche mitziehen sehen — nicht rot werden.
    """
    user, app_user = make_role_user("DISPOSITION")
    client = Client()
    client.force_login(user)
    daten = _suche(client, "Badensche")

    rechte = rechte_service.effective_permissions(app_user.id)
    darf_belege = rechte.get(("invoicing", "LESEN")) == "ALLE"
    hat_belege = bool(_typen(daten) & {"ANGEBOT", "RECHNUNG"})
    assert hat_belege == darf_belege


def _rolle_nur_workflow(chef_id):
    """Eine Rolle, die AUSSCHLIESSLICH `workflow/LESEN` (Scope ALLE) darf.

    Die Seed-Matrix hat keine solche Rolle — die Grenze, um die es geht (Auftrag
    sehen dürfen, Adresse und Kontaktdaten NICHT), lässt sich mit den vorhandenen
    Rollen also gar nicht prüfen. Deshalb hier eine eigene: Der Test prüft die
    Regel, nicht die zufällige Belegung der Seed-Daten.
    """
    with business_transaction(chef_id):
        rolle, _ = Role.objects.get_or_create(
            code="NUR_WORKFLOW", defaults={"label": "Nur Workflow (Test)"},
        )
        RolePermission.objects.get_or_create(
            role_id=rolle.code, module="workflow", action="LESEN",
            defaults={"id": uuid.uuid4(), "allowed": True, "row_scope": "ALLE"},
        )
    return rolle.code


@pytest.mark.django_db
def test_ohne_property_und_identity_recht_keine_adresse_und_keine_mailsuche(welt):
    """Der Bruchfall aus Runde 2: Die Suche darf keine Grenze neu ziehen.

    Ein Konto, das nur `workflow` lesen darf, sieht den Auftrag — aber die übrige
    API gibt ihm zu diesem Auftrag nie Straße und Hausnummer (nur Objekt + Ort)
    und nie die Kontaktdaten der Beteiligten. Die Suche hält sich daran:
    kein Straßenname im Untertitel, kein Treffer über eine Mailadresse.
    """
    rolle = _rolle_nur_workflow(welt["chef"].id)
    user, _app_user = make_role_user(rolle)
    c = Client()
    c.force_login(user)

    daten = _suche(c, "Dachrinne")
    auftrag = next(t for t in daten["treffer"] if t["typ"] == "AUFTRAG")
    assert "Badensche" not in auftrag["untertitel"]
    assert "Berlin" in auftrag["untertitel"]

    # Kein Auskunftsdienst „E-Mail rein, Vorgang/Name raus".
    daten = _suche(c, "erika.meier@hausverwaltung.test")
    assert daten["treffer"] == []

    # Zum Vergleich: Die Administration (property + identity) sieht beides.
    admin = Client()
    admin.force_login(make_role_user("ADMINISTRATION")[0])
    auftrag = next(
        t for t in _suche(admin, "Dachrinne")["treffer"] if t["typ"] == "AUFTRAG")
    assert "Badensche Straße 53, 10825 Berlin" in auftrag["untertitel"]
    assert _suche(admin, "erika.meier@hausverwaltung.test")["treffer"]


@pytest.mark.django_db
def test_konto_ohne_app_user_bekommt_403(client_with_role, welt):
    client = client_with_role("MONTEUR", with_app_user=False)
    r = client.get("/api/suche", {"q": "Badensche"})
    assert r.status_code == 403


@pytest.mark.django_db
def test_konto_ohne_jede_rolle_bekommt_403_statt_leerer_liste(welt):
    """Wer nirgends lesen darf, darf auch nicht suchen — 403, keine leere 200.

    Die Regel „kein 403, nur weil eine Kategorie fehlt" gilt hier nicht: Es fehlt
    nicht eine Kategorie, sondern jede. Eine leere 200 wäre die falsche Auskunft
    („nichts gefunden") auf eine Rechtefrage.
    """
    user, _app_user = make_role_user(None)
    client = Client()
    client.force_login(user)
    r = client.get("/api/suche", {"q": "Badensche"})
    assert r.status_code == 403
