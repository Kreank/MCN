"""Die Objektsicht des Monteurs (Migration 0099) — die Bruchfälle, namentlich.

## Das fachliche Warum (es entscheidet jeden Zweifelsfall hier)

Der Betrieb sagt: „Mieter meldet Heizkörper kalt. Zwei Tage vorher hat bei einem
anderen Mieter der Heizkörper geleckt und musste getauscht werden. Im Gesamtobjekt
sollte auch stehen, dass es eine Zentralanlage ist. Das sind Infos, die der Monteur
wissen muss. Wenn er dazu nichts findet, ist das scheiße."

Vorher sah die Rolle MONTEUR (`row_scope='EIGENE'`) **nur ihre eigenen Einsätze**.
Liegenschaft, Kontakte, Objekthistorie: 403.

## Die Regeln, die diese Datei festschreibt

1. **Zeitfenster: keines.** Wer je einen Einsatz auf einem Objekt hatte, sieht es
   dauerhaft.
2. **Er darf alles sehen — außer Geld.** Seit Migration **0102** sieht er das
   **Angebot** seines Objekts (versendet/angenommen) — mit **Mengen, ohne Preise**;
   die **Rechnung** bleibt die eine, vollständige Ausnahme. Geprüft wird das nicht an
   einer Feldliste, sondern am **serialisierten Antwortkörper** (`_kein_geld`):
   Betrag als Text UND Geldfeldname. Eine Feldliste vergisst `unit_cost`.
3. **Lesen ist weiter als Schreiben.** Schreiben darf er: Räume/Aufmaß, Gebäude und
   Einheiten **an seinen Objekten**; Dateien an **eigenen Einsätzen/Berichten**. Sonst
   nichts — kein Statuswechsel, keine neue Liegenschaft, kein Bauteilkatalog-Eintrag,
   kein fremder Bericht.
4. **Fremd = 404, nicht 403.** Die Existenz fremder Objekte wird nicht verraten — auch
   nicht über eine exakte Objektnummer in der Suche (Direkttreffer-Pfad!).

## Der Aufbau

**Zwei** Liegenschaften: **A** (der Monteur hat dort einen Einsatz — über einen
Auftrag, dem ein KOLLEGE zugewiesen ist) und **B** (fremd). An beiden hängt dasselbe:
Gebäude, Einheit, Vorgang, Auftrag, Einsatz des Kollegen, Baustellenbericht des
Kollegen, Beteiligte mit Telefonnummer, Datei, Angebot, Rechnung. Was der Monteur an A
sieht, muss er an B genauso wenig sehen — sonst ist der Filter Zufall, nicht Regel.
"""
import uuid
from datetime import date, datetime, timezone as dt_timezone

import pytest
from django.test import Client

from db_core.models import DueItem
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import dateien as dateien_service
from db_core.services import einsatz as einsatz_service
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services import identity as identity_service
from db_core.services import gewaehrleistung as gewaehrleistung_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service
from db_core.services import pruefung as pruefung_service
from db_core.services import site_report as report_service
from db_core.services import wartung as wartung_service

from .conftest import logged_in_client, make_app_user, make_role_user

JSON = "application/json"
T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    """Objektspeicher-Attrappe (Dateien ohne MinIO)."""

    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        return None

    def get_object(self, key):
        return PNG_1x1

    def remove_object(self, key):
        pass


@pytest.fixture
def fake_storage(monkeypatch):
    from db_core import storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage", lambda: FakeStorage())


# ---------------------------------------------------------------------------
# Das Szenario
# ---------------------------------------------------------------------------

def _objekt(chef, *, name, strasse, hausnummer, plz, ort):
    """Eine vollständige Liegenschaft: Gebäude, Einheit, Beteiligte mit Telefon,
    Projekt, Vorgang, Auftrag, Einsatz eines Kollegen, dessen Bericht, Angebot und
    Rechnung. Zweimal aufgebaut (A und B) — identisch, damit die Grenze die einzige
    Variable ist."""
    u = chef.id
    obj = property_service.create_property(
        u, name=name, property_type="WEG",
        street=strasse, house_number=hausnummer, postal_code=plz, city=ort,
    )
    gebaeude = property_service.add_building(
        u, property_id=obj.id, building_number="1", name="Vorderhaus",
    )
    einheit = property_service.add_unit(
        u, building_id=gebaeude.id, property_id=obj.id,
        unit_type="APARTMENT", unit_number="WE1",
    )

    # Beteiligter MIT Telefonnummer — die muss der Monteur anrufen können.
    mieter = identity_service.create_person(
        u, first_name="Mona", last_name=f"Mieterin {name[:3]}"
    )
    identity_service.add_contact_point(
        u, mieter.id, contact_type="PHONE", value=f"030 111{plz}", is_primary=True,
    )
    property_service.add_party_role(
        u, property_id=obj.id, party_id=mieter.id, role="PROPERTY_OWNER",
        valid_from=date(2020, 1, 1),
    )

    projekt = projekt_service.create_project(
        u, name=f"Heizungssanierung {name}", property_ids=[obj.id]
    )
    vorgang = projekt_service.create_service_case(
        u, property_id=obj.id, subject="Heizkörper leckt",
        reported_by_party_id=mieter.id, project_id=projekt.id,
    )
    auftrag = auftrag_service.create_work_order(
        u, property_id=obj.id, title="Heizkörper tauschen",
        service_case_id=vorgang.id, project_id=projekt.id,
    )
    auftrag_service.add_work_order_party(
        u, work_order_id=auftrag.id, party_id=mieter.id, role="PRINCIPAL",
        is_primary=True,
    )

    # Der KOLLEGE — sein Einsatz und sein Bericht sind das, was der Monteur an A
    # sehen MUSS und an B nicht sehen DARF.
    kollege = make_app_user(f"Kollege {name[:3]}")
    kollegen_job = einsatz_service.create_service_job(
        u, work_order_id=auftrag.id, title="Heizkörper demontieren",
        scheduled_start=T0, scheduled_end=T1,
    )
    einsatz_service.assign_user(
        u, service_job_id=kollegen_job.id, assignee_user_id=kollege.id,
    )
    bericht = report_service.create_report(
        kollege.id, service_job_id=kollegen_job.id, work_order_id=auftrag.id,
        report_date=date(2026, 7, 13),
        activity_text=f"Heizkoerper undicht, getauscht. Zentralanlage im Keller ({name}).",
    )

    # --- Das Angebot (0102) -------------------------------------------------
    # MARKANTE Beträge: Sie sind der eigentliche Prüfstein. `unit_cost` (Einkauf)
    # und `markup_percent` (Aufschlag) stehen nicht einmal auf dem Kundenbeleg —
    # sie stehen aber in `QuoteLineOut`, und genau daran scheitert dieser Slice,
    # wenn die Mengensicht als „Feldliste minus unit_price" gebaut wird.
    # Alle Zahlen tragen einen Dezimalpunkt: So kann kein UUID-Hex sie zufällig
    # enthalten, und der Textscan über die Antwort wird nicht flaky.
    angebot = beleg_service.create_quote(
        u, property_id=obj.id, title="Heizkörper Material und Montage",
        work_order_id=auftrag.id, project_id=projekt.id,
        lines=[{
            "line_type": "MATERIAL", "description": "Kupferrohr DN20",
            "quantity": "12", "unit": "m", "unit_price": "1234.56",
            "unit_cost": "999.99", "markup_percent": "37.25",
            "tax_code": "DE_19",
        }],
    )
    # VERSENDET: erst damit ist das Angebot eine Aussage nach außen (und für den
    # Monteur überhaupt sichtbar). Die DB vergibt die Nummer und friert ein.
    angebot = beleg_service.send_quote(u, quote_id=angebot.id)

    # Ein ENTWURF am selben Objekt — er darf dem Monteur NIE erscheinen: Inhalt und
    # Preise ändern sich noch, und was hier steht, hat niemand beauftragt.
    angebot_entwurf = beleg_service.create_quote(
        u, property_id=obj.id, title=f"Fassade {name} — Vorabschätzung",
        lines=[{
            "line_type": "MATERIAL", "description": "Gerüst (geschätzt)",
            "quantity": "1", "unit": "psch", "unit_price": "4321.00",
            "tax_code": "DE_19",
        }],
    )
    rechnung = beleg_service.create_invoice(
        u, property_id=obj.id, work_order_id=auftrag.id,
        lines=[{
            "line_type": "MATERIAL", "description": "Heizkörper Typ 22",
            "quantity": "1", "unit": "Stk", "unit_price": "5678.90",
            "tax_code": "DE_19",
        }],
    )
    beleg_service.add_invoice_party(
        u, invoice_id=rechnung.id, party_id=mieter.id, role="INVOICE_DEBTOR",
        is_primary=True,
    )
    # Der Inhalt MUSS je Objekt verschieden sein: `datei_hochladen` dedupliziert
    # über SHA-256 (dieselbe Rechnung an Projekt und Kontakt ist dieselbe Datei).
    # Zweimal dasselbe PNG ergäbe EINE `content.file` mit zwei Verknüpfungen — die
    # Datei „an B" wäre physisch die Datei „an A", und der Test prüfte nichts.
    _datei, link = dateien_service.datei_hochladen(
        u, dateiname=f"wartungsplan_{plz}.png", inhalt=PNG_1x1 + plz.encode(),
        link_category="DOKUMENT", property_id=obj.id,
    )

    # --- Wartung (0100): Vertrag, Prüffrist, Gewährleistung an der Zentralanlage.
    # Das Schema `maintenance` führt KEINE Geldspalte — deshalb darf der Monteur es
    # an seinem Objekt lesen. „Steht die Anlage unter Vertrag? Wann zuletzt geprüft?
    # Läuft noch Gewährleistung?" ist derselbe Heizkörper-Fall.
    vertrag = wartung_service.create_contract(
        u, name=f"Zentralanlage {name} — Jahreswartung", property_id=obj.id,
        start_date=date(2026, 3, 1), interval_kind="JAEHRLICH",
        due_action="AUFGABE",
    )
    pruefart = pruefung_service.create_inspection_type(
        u, name=f"Trinkwasserbeprobung {plz}", interval_kind="JAEHRLICH",
    )
    pruefung = pruefung_service.create_inspection(
        u, property_id=obj.id, inspection_type_id=pruefart.id,
        name=f"TrinkwV-Beprobung {name}", start_date=date(2026, 4, 1),
    )
    # Eine Gewährleistung entsteht erst mit erbrachter Leistung (Service-Tor) — der
    # Auftrag muss dafür bis TECHNISCH_ABGESCHLOSSEN laufen. Das ist kein
    # Test-Kunstgriff, sondern der reale Zustand, in dem eine Frist zu laufen beginnt.
    auftrag_service.set_order_evidence(
        u, work_order_id=auftrag.id, reference="Auftrag per Mail"
    )
    auftrag_service.confirm_responsibility(
        u, work_order_id=auftrag.id, scope="COMMON_PROPERTY"
    )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN"):
        auftrag_service.advance_status(u, work_order_id=auftrag.id, to_status=to)
    gewaehrleistung = gewaehrleistung_service.create_warranty(
        u, work_order_id=auftrag.id, start_date=date(2026, 5, 1),
    )

    return {
        "obj": obj, "gebaeude": gebaeude, "einheit": einheit, "mieter": mieter,
        "projekt": projekt, "vorgang": vorgang, "auftrag": auftrag,
        "kollege": kollege, "kollegen_job": kollegen_job, "bericht": bericht,
        "angebot": angebot, "angebot_entwurf": angebot_entwurf,
        "rechnung": rechnung,
        "datei_id": link.file_id, "link_id": link.id,
        "vertrag": vertrag, "pruefart": pruefart, "pruefung": pruefung,
        "gewaehrleistung": gewaehrleistung,
    }


@pytest.fixture
def welt(db, fake_storage):
    """A = mein Objekt (ich habe dort einen eigenen Einsatz). B = fremd."""
    chef = make_app_user("Chefin")
    a = _objekt(chef, name="Alpha-Hof", strasse="Badensche Straße",
                hausnummer="53", plz="10825", ort="Berlin")
    b = _objekt(chef, name="Beta-Hof", strasse="Kantstraße",
                hausnummer="42", plz="10625", ort="Berlin")

    # Der Monteur bekommt EINEN eigenen Einsatz an A — mehr braucht es nicht, damit
    # A für immer „sein Objekt" ist.
    user, monteur = make_role_user("MONTEUR")
    mein_job = einsatz_service.create_service_job(
        chef.id, work_order_id=a["auftrag"].id, title="Neuen Heizkörper anschließen",
        scheduled_start=T0, scheduled_end=T1,
    )
    einsatz_service.assign_user(
        chef.id, service_job_id=mein_job.id, assignee_user_id=monteur.id,
    )

    client = Client()
    client.force_login(user)
    return {
        "chef": chef, "monteur": monteur, "monteur_user": user, "client": client,
        "mein_job": mein_job, "A": a, "B": b,
    }


# ===========================================================================
# ER MUSS: das Objekt A sehen — Straße, Einheiten, Beteiligte mit Telefonnummer
# ===========================================================================

@pytest.mark.django_db
def test_monteur_sieht_liegenschaft_a_mit_strasse_und_einheiten(welt):
    c, a, b = welt["client"], welt["A"], welt["B"]

    liste = c.get("/api/property/properties")
    assert liste.status_code == 200, liste.content
    ids = {i["id"] for i in liste.json()["items"]}
    assert ids == {str(a["obj"].id)}, "nur MEIN Objekt, nie das fremde"

    r = c.get(f"/api/property/properties/{a['obj'].id}")
    assert r.status_code == 200, r.content
    d = r.json()
    # Die Straße — ohne sie fährt er nirgendwohin. Genau das fehlte ihm.
    assert d["address"]["street"] == "Badensche Straße"
    assert d["address"]["house_number"] == "53"
    assert [u["unit_number"] for g in d["buildings"] for u in g["units"]] == ["WE1"]
    assert {p["party_id"] for p in d["party_roles"]} == {str(a["mieter"].id)}


@pytest.mark.django_db
def test_monteur_erreicht_den_mieter_von_a_telefonisch(welt):
    """Der Grund, aus dem er `identity/LESEN` überhaupt bekommen hat."""
    c, a, b = welt["client"], welt["A"], welt["B"]

    r = c.get(f"/api/identity/parties/{a['mieter'].id}/contact-points")
    assert r.status_code == 200, r.content
    assert [w["value"] for w in r.json()] == ["030 11110825"]

    # Die Kontaktliste zeigt NUR die Parties an seinen Objekten.
    liste = c.get("/api/identity/parties")
    assert liste.status_code == 200
    ids = {i["id"] for i in liste.json()["items"]}
    assert str(a["mieter"].id) in ids
    assert str(b["mieter"].id) not in ids


@pytest.mark.django_db
def test_monteur_sieht_vorgang_auftrag_und_einsatz_des_kollegen_an_a(welt):
    """**DER Heizkörper-Fall.** Der Vorgang „Heizkörper leckt" und der Auftrag dazu
    stammen von einem Kollegen — der Monteur muss sie trotzdem finden."""
    c, a = welt["client"], welt["A"]

    r = c.get(f"/api/workflow/service_cases/{a['vorgang'].id}")
    assert r.status_code == 200, r.content
    assert r.json()["subject"] == "Heizkörper leckt"

    r = c.get(f"/api/workflow/work_orders/{a['auftrag'].id}")
    assert r.status_code == 200, r.content
    assert r.json()["title"] == "Heizkörper tauschen"

    r = c.get(f"/api/workflow/projects/{a['projekt'].id}")
    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_monteur_liest_den_baustellenbericht_des_kollegen_an_a(welt):
    """Der Bericht von vorgestern — die Information, ohne die er zweimal fährt."""
    c, a = welt["client"], welt["A"]

    r = c.get(f"/api/workflow/site_reports/{a['bericht'].id}")
    assert r.status_code == 200, r.content
    assert "Zentralanlage" in r.json()["activity_text"]

    # Auch die Auftragssicht über ALLE Berichte der Baustelle (vorher: 403).
    r = c.get(f"/api/workflow/site_reports?work_order_id={a['auftrag'].id}")
    assert r.status_code == 200, r.content
    assert str(a["bericht"].id) in {i["id"] for i in r.json()["items"]}


@pytest.mark.django_db
def test_monteur_findet_a_ueber_die_globale_suche(welt):
    c, a, b = welt["client"], welt["A"], welt["B"]

    r = c.get("/api/suche", {"q": "Badensche"})
    assert r.status_code == 200, r.content
    treffer = r.json()["treffer"]
    typen = {t["typ"] for t in treffer}
    assert "LIEGENSCHAFT" in typen
    ids = {t["id"] for t in treffer}
    assert str(a["obj"].id) in ids
    assert str(a["auftrag"].id) in ids
    assert str(a["vorgang"].id) in ids
    # Und die Straße steht im Untertitel (sie hing vorher an `sicht.property`).
    liegenschaft = next(t for t in treffer if t["typ"] == "LIEGENSCHAFT")
    assert "Badensche Straße" in liegenschaft["untertitel"]


@pytest.mark.django_db
def test_monteur_bekommt_das_liegenschafts_dossier_von_a_ohne_geld(welt):
    """Die Gesamtsicht — **ohne** die Geld-Bausteine."""
    c, a = welt["client"], welt["A"]

    r = c.get(f"/api/dossier/liegenschaft/{a['obj'].id}")
    assert r.status_code == 200, r.content
    d = r.json()

    # Die Objektakte ist da: Struktur, Beteiligte, Historie.
    assert d["liegenschaft"]["street"] == "Badensche Straße"
    assert {b["party_id"] for b in d["beteiligte"]} == {str(a["mieter"].id)}
    assert d["vorgaenge_sichtbar"] is True
    assert {v["id"] for v in d["vorgaenge"]} == {str(a["vorgang"].id)}
    assert {o["id"] for o in d["auftraege"]} == {str(a["auftrag"].id)}
    # Auch der Einsatz des Kollegen (Objekthistorie, nicht Terminliste).
    assert str(a["kollegen_job"].id) in {e["id"] for e in d["einsaetze"]}

    # Und KEIN Geld: kein offener Posten, kein Baustein, kein Betrag.
    assert d["offene_posten_sichtbar"] is False
    assert d["offene_posten"] is None
    # Wartung dagegen IST sichtbar (Migration 0100) — sie führt kein Geld, und ohne
    # sie stünde der Monteur ahnungslos vor der Zentralanlage. Details im eigenen
    # Test `test_monteur_sieht_wartung_pruefung_und_gewaehrleistung_an_a`.
    assert d["wartung_sichtbar"] is True


@pytest.mark.django_db
def test_monteur_laedt_eine_datei_an_a_herunter(welt):
    c, a = welt["client"], welt["A"]

    liste = c.get(f"/api/content/files?property_id={a['obj'].id}")
    assert liste.status_code == 200, liste.content
    assert liste.json()["total"] == 1

    r = c.get(f"/api/content/files/{a['datei_id']}/download")
    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_die_objektsicht_gilt_dauerhaft(welt):
    """Entscheidung des Users: **kein Zeitfenster.** Auch wenn der Einsatz längst
    abgeschlossen und der Auftrag abgerechnet ist, bleibt A sein Objekt.

    Geprüft am **AUSGEFALLEN**en Einsatz (Sackgassen-Status): Selbst wenn die
    Zuweisung nie zu Arbeit geführt hat, bleibt die Sicht — sie hängt an der
    **Zuweisung**, an keinem Status und an keinem Datum."""
    c, a = welt["client"], welt["A"]
    for to_status, reason in (("GEPLANT", None), ("AUSGEFALLEN", "Kunde nicht da")):
        einsatz_service.advance_status(
            welt["chef"].id, service_job_id=welt["mein_job"].id,
            to_status=to_status, reason=reason,
        )
    welt["mein_job"].refresh_from_db()
    assert welt["mein_job"].status == "AUSGEFALLEN"

    assert c.get(f"/api/property/properties/{a['obj'].id}").status_code == 200
    assert c.get(f"/api/dossier/liegenschaft/{a['obj'].id}").status_code == 200
    assert c.get(f"/api/workflow/site_reports/{a['bericht'].id}").status_code == 200


# ===========================================================================
# WARTUNG (Migration 0100) — ein Vertrag ist keine Rechnung
# ===========================================================================

@pytest.mark.django_db
def test_monteur_sieht_wartung_pruefung_und_gewaehrleistung_an_a(welt):
    """Er steht vor der Zentralanlage und muss wissen: Steht sie unter Vertrag? Wann
    ist die nächste Fälligkeit? Läuft noch Gewährleistung?

    Das Schema `maintenance` führt **keine einzige Geldspalte** — hier ist nichts zu
    verbergen. Deshalb ist das die eine Ausweitung gegenüber 0099."""
    c, a = welt["client"], welt["A"]

    r = c.get("/api/maintenance/contracts")
    assert r.status_code == 200, r.content
    assert {v["id"] for v in r.json()["items"]} == {str(a["vertrag"].id)}

    r = c.get(f"/api/maintenance/contracts/{a['vertrag'].id}")
    assert r.status_code == 200, r.content
    # Die nächste Fälligkeit ist der eigentliche Nutzwert.
    assert r.json()["next_due_date"] is not None

    r = c.get("/api/maintenance/inspections")
    assert r.status_code == 200, r.content
    assert {p["id"] for p in r.json()["items"]} == {str(a["pruefung"].id)}

    r = c.get("/api/maintenance/warranties")
    assert r.status_code == 200, r.content
    assert {g["id"] for g in r.json()["items"]} == {str(a["gewaehrleistung"].id)}

    # Prüfarten sind globales Stammdatum (kein property_id) — lesbar, sonst wäre die
    # Prüffrist am eigenen Objekt eine namenlose Zeile.
    assert c.get("/api/maintenance/inspection-types").status_code == 200

    # Und im Dossier erscheint der Baustein jetzt (vorher: wartung_sichtbar=False).
    d = c.get(f"/api/dossier/liegenschaft/{a['obj'].id}").json()
    assert d["wartung_sichtbar"] is True
    assert {v["id"] for v in d["wartungsvertraege"]} == {str(a["vertrag"].id)}
    # Geld bleibt weg — die Ausweitung hat die eine Ausnahme nicht aufgeweicht.
    assert d["offene_posten_sichtbar"] is False


@pytest.mark.django_db
def test_monteur_sieht_die_wartung_von_b_nicht(welt):
    """Auch nicht über die exakte Vertragsnummer. **Befund:** Die Suche kennt
    Wartungsverträge gar nicht als Kategorie (`suche.TYPEN` führt keine, und
    `_KENNUNG_JAHR` kein Präfix „W") — der Direkttreffer-Pfad kann sie deshalb
    strukturell nicht ausliefern. Der Test hält das fest, damit eine spätere
    WARTUNG-Kategorie nicht ungefiltert hinzukommt."""
    c, b = welt["client"], welt["B"]

    assert c.get(f"/api/maintenance/contracts/{b['vertrag'].id}").status_code == 404

    # Keine Zeile von B in irgendeiner der drei Listen.
    for pfad, key in (
        ("/api/maintenance/contracts", "vertrag"),
        ("/api/maintenance/inspections", "pruefung"),
        ("/api/maintenance/warranties", "gewaehrleistung"),
        ("/api/maintenance/due-items", None),
    ):
        r = c.get(pfad)
        assert r.status_code == 200, r.content
        if key:
            assert str(b[key].id) not in {i["id"] for i in r.json()["items"]}
    assert "Beta-Hof" not in c.get("/api/maintenance/contracts").content.decode()

    # Die Vertragsnummer öffnet in der Suche keine Tür.
    r = c.get("/api/suche", {"q": b["vertrag"].contract_number})
    assert r.status_code == 200
    assert r.json()["direkttreffer"] is None
    assert str(b["vertrag"].id) not in {t["id"] for t in r.json()["treffer"]}


@pytest.mark.django_db
def test_monteur_faelligkeitszaehler_zaehlen_nur_seine_objekte(welt):
    """Ein Scope, der die Liste filtert und die Summe darunter nicht, ist ein halber
    Scope: `offen_total` lief vorher über den **ganzen** Bestand („der Betrieb hat 240
    offene Fälligkeiten"). Eine Zahl ist keine Zeile — aber sie ist eine Auskunft über
    fremde Zeilen."""
    c = welt["client"]
    admin = logged_in_client("ADMINISTRATION")

    faelligkeit_service.generiere(welt["chef"].id, stichtag=date(2026, 12, 31))

    meine = c.get("/api/maintenance/due-items").json()
    alle = admin.get("/api/maintenance/due-items").json()

    assert alle["total"] > meine["total"] > 0, (meine["total"], alle["total"])
    assert meine["offen_total"] == meine["total"]
    assert alle["offen_total"] > meine["offen_total"]
    # Und keine einzige Zeile von B.
    assert "Beta-Hof" not in c.get("/api/maintenance/due-items").content.decode()


@pytest.mark.django_db
def test_monteur_verwaltet_keine_wartung(welt):
    """Er liest — er schließt keinen Vertrag, verschiebt keine Frist, verwirft keine
    Fälligkeit. Alle Schreibpfade bleiben fail-closed 403."""
    c, a = welt["client"], welt["A"]

    r = c.post(
        "/api/maintenance/contracts",
        data={"name": "Eigener Vertrag", "property_id": str(a["obj"].id),
              "start_date": "2026-01-01", "interval_kind": "JAEHRLICH",
              "due_action": "AUFGABE"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    r = c.post(
        f"/api/maintenance/contracts/{a['vertrag'].id}/status",
        data={"to_status": "INAKTIV"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    r = c.post(
        f"/api/maintenance/contracts/{a['vertrag'].id}/trigger",
        data={"action": "AUFGABE"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    r = c.post(
        "/api/maintenance/inspection-types",
        data={"name": "Eigene Prüfart", "interval_kind": "JAEHRLICH"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    r = c.post(
        "/api/maintenance/inspections",
        data={"inspection_type_id": str(a["pruefart"].id),
              "property_id": str(a["obj"].id), "start_date": "2026-01-01"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    r = c.patch(
        f"/api/maintenance/warranties/{a['gewaehrleistung'].id}",
        data={"duration_months": 120},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    # Fälligkeit erledigen/verwerfen: beides zu.
    faelligkeit_service.generiere(welt["chef"].id, stichtag=date(2026, 12, 31))
    item = DueItem.objects.filter(contract_id=a["vertrag"].id).first()
    assert item is not None
    r = c.post(
        f"/api/maintenance/due-items/{item.id}/erledigen",
        data={"action": "BENACHRICHTIGUNG"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    r = c.post(
        f"/api/maintenance/due-items/{item.id}/verwerfen",
        data={"begruendung": "Nicht nötig"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content


# ===========================================================================
# ER DARF NICHT: das fremde Objekt B — auf keinem Weg
# ===========================================================================

@pytest.mark.django_db
def test_monteur_sieht_liegenschaft_b_auf_keinem_weg(welt):
    """Jeder Weg einzeln — Liste, Detail, Dossier, Räume, Aufmaß, Datei."""
    c, b = welt["client"], welt["B"]

    assert c.get(f"/api/property/properties/{b['obj'].id}").status_code == 404
    assert c.get(f"/api/dossier/liegenschaft/{b['obj'].id}").status_code == 404
    assert c.get(f"/api/property/properties/{b['obj'].id}/rooms").status_code == 404
    assert c.get(f"/api/property/properties/{b['obj'].id}/aufmass").status_code == 404
    assert (
        c.get(f"/api/content/files?property_id={b['obj'].id}").status_code == 404
    )
    assert c.get(f"/api/content/files/{b['datei_id']}/download").status_code == 404


@pytest.mark.django_db
def test_monteur_findet_b_auch_ueber_die_exakte_objektnummer_nicht(welt):
    """**Der Direkttreffer-Pfad** ist der klassische Nebeneingang: eine eigene Query,
    an der Rechtefilter gern vergessen werden. Hier zieht er aus derselben
    rechtegefilterten Grundmenge."""
    c, b = welt["client"], welt["B"]

    r = c.get("/api/suche", {"q": b["obj"].property_number})
    assert r.status_code == 200, r.content
    assert r.json()["direkttreffer"] is None
    assert str(b["obj"].id) not in {t["id"] for t in r.json()["treffer"]}

    # Auch über die Straße nicht.
    #
    # **Zwei Fallen in der Rohtext-Probe, beide hier vermieden:**
    # 1. Django escapt Nicht-ASCII im JSON („Kantstraße" → „Kantstra\\u00dfe") — ein
    #    `"Kantstraße" not in content` wäre trivial wahr und prüfte nichts.
    # 2. Die Antwort echot den **Suchbegriff** in `begriff` zurück; auf „Kantstra" zu
    #    prüfen schlüge deshalb selbst dann fehl, wenn kein einziger Treffer käme.
    # Also: auf die TREFFER prüfen, und als Rohtext-Probe den Objektnamen „Beta-Hof"
    # (ASCII, kommt im Suchbegriff nicht vor).
    r = c.get("/api/suche", {"q": "Kantstraße"})
    assert r.json()["treffer"] == [], r.json()["treffer"]
    assert "Beta-Hof" not in r.content.decode()


@pytest.mark.django_db
def test_monteur_sieht_vorgang_auftrag_bericht_an_b_nicht(welt):
    c, b = welt["client"], welt["B"]

    assert c.get(f"/api/workflow/service_cases/{b['vorgang'].id}").status_code == 404
    assert c.get(f"/api/workflow/work_orders/{b['auftrag'].id}").status_code == 404
    assert c.get(f"/api/workflow/projects/{b['projekt'].id}").status_code == 404
    assert c.get(f"/api/workflow/site_reports/{b['bericht'].id}").status_code == 404
    assert (
        c.get(f"/api/workflow/site_reports?work_order_id={b['auftrag'].id}").status_code
        == 404
    )
    assert (
        c.get(f"/api/workflow/work_orders/{b['auftrag'].id}/soll-ist").status_code == 404
    )
    assert c.get(f"/api/dossier/auftrag/{b['auftrag'].id}").status_code == 404
    assert c.get(f"/api/dossier/projekt/{b['projekt'].id}").status_code == 404


@pytest.mark.django_db
def test_monteur_sieht_kontakte_ohne_objektbezug_nicht(welt):
    """Der Mieter von B hängt an keinem seiner Objekte — 404, nicht 403."""
    c, b = welt["client"], welt["B"]
    fremder = identity_service.create_person(
        welt["chef"].id, first_name="Lieferant", last_name="Ohne Objekt"
    )

    assert c.get(f"/api/identity/parties/{b['mieter'].id}").status_code == 404
    assert (
        c.get(f"/api/identity/parties/{b['mieter'].id}/contact-points").status_code
        == 404
    )
    assert c.get(f"/api/identity/parties/{fremder.id}").status_code == 404
    assert c.get(f"/api/dossier/kontakt/{fremder.id}").status_code == 404


# ===========================================================================
# DAS ANGEBOT (Migration 0102): Mengen ja — Geld nie
# ===========================================================================
#
# Jeder Betrag der Fixture trägt einen Dezimalpunkt, damit der Textscan über die
# **serialisierte** Antwort nicht zufällig in einem UUID-Hex anschlägt.
#
#   1234.56  Einzelpreis (VK)          999.99  EINKAUFSPREIS (unit_cost)
#   37.25    Aufschlag (markup)      14814.72  Positions-/Nettobetrag (12 × 1234.56)
#   17629.52 Bruttobetrag             4321.00  Preis im ENTWURF (nie sichtbar)
#   5678.90  Rechnungsbetrag
GELDSPUREN = (
    "1234.56", "999.99", "37.25", "14814.72", "17629.52", "4321.00", "5678.90",
)
# Die Feldnamen selbst — der zweite Gürtel. Ein leerer/nuller Betrag würde beim
# Zahlenscan durchrutschen; das FELD verrät die Absicht trotzdem.
GELDFELDER = (
    '"unit_price"', '"unit_cost"', '"markup_percent"', '"net_amount"',
    '"net_total"', '"gross_total"', '"tax_total"', '"discount_percent"',
    '"tax_rate_percent"', '"labour_net_amount"',
)


def _kein_geld(response, *, wo):
    """Die serialisierte Antwort enthält KEINEN Betrag und KEIN Geldfeld.

    Bewusst über den **Rohkörper**, nicht über eine Feldliste: Ein Test, der prüft,
    ob `unit_price` fehlt, übersieht `unit_cost` — genau der Fehler, den dieser
    Slice vermeiden soll. Was hier durchrutscht, rutscht auch zum Monteur durch.
    """
    text = response.content.decode()
    for betrag in GELDSPUREN:
        assert betrag not in text, f"{wo}: Betrag {betrag} ist durchgerutscht."
    for feld in GELDFELDER:
        assert feld not in text, f"{wo}: Geldfeld {feld} ist durchgerutscht."


@pytest.mark.django_db
def test_monteur_sieht_das_angebot_seines_objekts_mit_mengen(welt):
    """**Der Kern dieses Slices.** „12 m Kupferrohr DN20" — sonst baut er das Falsche.

    Er sieht die Position, die Menge und die Einheit. Er sieht **keinen** Preis,
    **keinen** Einkaufspreis und **keinen** Aufschlag — nicht einmal als Feldnamen.
    """
    c, a = welt["client"], welt["A"]

    liste = c.get("/api/invoicing/quotes/mengen")
    assert liste.status_code == 200, liste.content
    items = liste.json()["items"]
    assert {i["id"] for i in items} == {str(a["angebot"].id)}, (
        "nur das versendete Angebot MEINES Objekts"
    )
    assert items[0]["preise_ausgeblendet"] is True
    _kein_geld(liste, wo="Angebotsliste (Mengen)")

    detail = c.get(f"/api/invoicing/quotes/{a['angebot'].id}/mengen")
    assert detail.status_code == 200, detail.content
    d = detail.json()
    assert d["quote_number"] == a["angebot"].quote_number
    assert d["work_order"]["id"] == str(a["auftrag"].id)
    (pos,) = d["lines"]
    assert pos["description"] == "Kupferrohr DN20"
    assert pos["quantity"] == "12.000" and pos["unit"] == "m"
    assert pos["line_kind"] == "NORMAL"
    _kein_geld(detail, wo="Angebotsdetail (Mengen)")


@pytest.mark.django_db
def test_monteur_sieht_weder_entwurf_noch_fremdes_angebot(welt):
    """Zwei Grenzen an einem Endpunkt: der **Status** und das **Objekt**.

    Der ENTWURF an seinem eigenen Objekt A ist Bürokram (Inhalt noch änderbar, nichts
    beauftragt) → 404. Das versendete Angebot am fremden Objekt B → 404 (nie 403: die
    Existenz wird nicht verraten) — auch nicht über die exakte Belegnummer im
    Direkttreffer-Pfad der Suche.
    """
    c, a, b = welt["client"], welt["A"], welt["B"]

    assert (
        c.get(f"/api/invoicing/quotes/{a['angebot_entwurf'].id}/mengen").status_code
        == 404
    )
    assert c.get(f"/api/invoicing/quotes/{b['angebot'].id}/mengen").status_code == 404

    # Die Liste zeigt beide nicht (sie ist dieselbe Regel, nicht eine zweite).
    ids = {i["id"] for i in c.get("/api/invoicing/quotes/mengen").json()["items"]}
    assert str(a["angebot_entwurf"].id) not in ids
    assert str(b["angebot"].id) not in ids

    # Der Direkttreffer-Pfad der Suche zieht aus derselben Grundmenge.
    b["angebot"].refresh_from_db()
    r = c.get("/api/suche", {"q": b["angebot"].quote_number})
    assert r.status_code == 200
    assert r.json()["direkttreffer"] is None
    assert str(b["angebot"].id) not in {t["id"] for t in r.json()["treffer"]}


@pytest.mark.django_db
def test_projekt_ueber_zwei_objekte_zeigt_nur_mein_angebot(welt):
    """**Der Nebeneingang, der zu bleiben hat.** Ein Projekt gilt schon als „meins",
    wenn EINE seiner Liegenschaften meine ist — seine Angebote am **fremden** Objekt
    dürfen darin trotzdem nicht auftauchen.

    Genau dieser Fall fehlt der Fixture (dort hat jedes Objekt sein eigenes Projekt),
    und genau er wäre das Leck: Das Projekt-Dossier filtert die Angebote deshalb noch
    einmal über `objektsicht.angebote_begrenzen`, nicht bloß über `project_id`.
    """
    c, chef, a, b = welt["client"], welt["chef"], welt["A"], welt["B"]

    # EIN Projekt über BEIDE Objekte — A ist meins, B nicht.
    gemeinsam = projekt_service.create_project(
        chef.id, name="Quartierssanierung Alpha+Beta",
        property_ids=[a["obj"].id, b["obj"].id],
    )
    # Je ein versendetes Angebot in diesem Projekt: eines an A, eines an B.
    meins = beleg_service.create_quote(
        chef.id, property_id=a["obj"].id, project_id=gemeinsam.id,
        title="Steigleitung Alpha",
        lines=[{"line_type": "MATERIAL", "description": "Steigleitung",
                "quantity": "8", "unit": "m", "unit_price": "1234.56",
                "tax_code": "DE_19"}],
    )
    meins = beleg_service.send_quote(chef.id, quote_id=meins.id)
    fremdes = beleg_service.create_quote(
        chef.id, property_id=b["obj"].id, project_id=gemeinsam.id,
        title="Steigleitung Beta",
        lines=[{"line_type": "MATERIAL", "description": "Steigleitung",
                "quantity": "8", "unit": "m", "unit_price": "5678.90",
                "tax_code": "DE_19"}],
    )
    fremdes = beleg_service.send_quote(chef.id, quote_id=fremdes.id)

    r = c.get(f"/api/dossier/projekt/{gemeinsam.id}")
    assert r.status_code == 200, r.content
    d = r.json()
    ids = {z["id"] for z in d["angebote_mengen"]}
    assert ids == {str(meins.id)}, "das Angebot am FREMDEN Objekt ist durchgerutscht"
    # Und die Liegenschaftsliste des Projekts zeigt B ebenfalls nicht.
    assert {l["property_id"] for l in d["liegenschaften"]} == {str(a["obj"].id)}
    _kein_geld(r, wo="Projekt-Dossier über zwei Objekte")

    # Dasselbe an der Beleg-API: das fremde Angebot ist 404, meines 200.
    assert c.get(f"/api/invoicing/quotes/{fremdes.id}/mengen").status_code == 404
    assert c.get(f"/api/invoicing/quotes/{meins.id}/mengen").status_code == 200


@pytest.mark.django_db
def test_monteur_findet_sein_angebot_in_der_suche_ohne_betrag(welt):
    """Die Suche ist der Endpunkt, den jeder aufruft — und damit das bequemste Leck."""
    c, a = welt["client"], welt["A"]
    a["angebot"].refresh_from_db()

    r = c.get("/api/suche", {"q": a["angebot"].quote_number})
    assert r.status_code == 200
    assert r.json()["direkttreffer"]["id"] == str(a["angebot"].id)
    _kein_geld(r, wo="Suche (Direkttreffer Angebot)")

    r = c.get("/api/suche", {"q": "Heizkörper"})
    assert r.status_code == 200
    typen = {t["typ"] for t in r.json()["treffer"]}
    assert "RECHNUNG" not in typen, "Die Rechnung bleibt die eine Ausnahme."
    _kein_geld(r, wo="Suche (Volltext)")


@pytest.mark.django_db
def test_monteur_sieht_das_angebot_im_dossier_preisfrei(welt):
    """Auftrags- und Projekt-Dossier: `angebote_mengen` ja, alles mit Geld nein."""
    c, a, b = welt["client"], welt["A"], welt["B"]

    r = c.get(f"/api/dossier/auftrag/{a['auftrag'].id}")
    assert r.status_code == 200, r.content
    d = r.json()
    assert d["angebote_mengen_sichtbar"] is True
    assert [z["id"] for z in d["angebote_mengen"]] == [str(a["angebot"].id)]
    # Die preisführenden Bausteine bleiben zu — an DEMSELBEN Dossier.
    assert d["belege_sichtbar"] is False
    assert d["angebote"] is None and d["rechnungen"] is None
    assert d["abrechnung_sichtbar"] is False and d["abrechnung"] is None
    assert d["offene_posten_sichtbar"] is False and d["offene_posten"] is None
    _kein_geld(r, wo="Auftrags-Dossier")

    r = c.get(f"/api/dossier/projekt/{a['projekt'].id}")
    assert r.status_code == 200, r.content
    d = r.json()
    assert d["angebote_mengen_sichtbar"] is True
    assert [z["id"] for z in d["angebote_mengen"]] == [str(a["angebot"].id)]
    assert d["marge_sichtbar"] is False and d["marge"] is None
    assert d["belege_sichtbar"] is False
    _kein_geld(r, wo="Projekt-Dossier")

    # Liegenschafts- und Kontakt-Dossier: unverändert ohne Geld-Baustein.
    for pfad in (
        f"/api/dossier/liegenschaft/{a['obj'].id}",
        f"/api/dossier/kontakt/{a['mieter'].id}",
    ):
        r = c.get(pfad)
        assert r.status_code == 200, r.content
        assert r.json()["offene_posten"] is None
        _kein_geld(r, wo=pfad)

    # Der Soll-Ist-Abgleich liest die ANGEBOTSZEILEN (das Soll) — er ist damit der
    # zweite Weg, auf dem ein Einzelpreis in die Objektsicht geraten könnte.
    # Er darf Mengen führen und sonst nichts.
    r = c.get(f"/api/workflow/work_orders/{a['auftrag'].id}/soll-ist")
    assert r.status_code == 200, r.content
    assert r.json()["angebote"], "das Soll stützt sich auf das Angebot"
    _kein_geld(r, wo="Soll-Ist")

    r = c.get(f"/api/workflow/work_orders/{a['auftrag'].id}")
    assert r.status_code == 200, r.content
    _kein_geld(r, wo="Auftragsdetail")

    # Das fremde Objekt bleibt fremd — auch mit invoicing/LESEN in der Tasche.
    assert c.get(f"/api/dossier/auftrag/{b['auftrag'].id}").status_code == 404


@pytest.mark.django_db
def test_monteur_sieht_NIEMALS_eine_rechnung(welt):
    """**Die eine Ausnahme, wörtlich vom User.** Jeder Lesepfad der Rechnung, einzeln.

    Seit 0102 schützt die Rechnung nicht mehr die Abwesenheit des Rechts, sondern
    `permissions.require` (403 bei row_scope EIGENE). Diese Liste ist der Beweis,
    dass kein Rechnungs-Endpunkt versehentlich auf `require_scoped` steht.
    """
    c, a, b = welt["client"], welt["A"], welt["B"]

    for beleg in (a, b):
        rid = beleg["rechnung"].id
        for pfad in (
            f"/api/invoicing/invoices/{rid}",
            f"/api/invoicing/invoices/{rid}/pdf",
            f"/api/invoicing/invoices/{rid}/zugferd.pdf",
            f"/api/invoicing/invoices/{rid}/zugferd.xml",
            f"/api/invoicing/invoices/{rid}/kalkulation",
            f"/api/buchhaltung/invoices/{rid}",
        ):
            assert c.get(pfad).status_code == 403, f"{pfad} ist offen!"

    for pfad in (
        "/api/invoicing/invoices",
        "/api/invoicing/invoices/anrechenbare-abschlaege"
        f"?work_order_id={a['auftrag'].id}",
        "/api/buchhaltung/invoices",
        "/api/buchhaltung/dunning",
        "/api/buchhaltung/mahnlauf/vorschau",
        "/api/buchhaltung/datev-export.csv",
        "/api/auswertungen/dashboards",
        "/api/auswertungen/kunden",
        "/api/auswertungen/projekte",
        f"/api/workflow/work_orders/{a['auftrag'].id}/offene-abrechnung",
    ):
        assert c.get(pfad).status_code == 403, f"{pfad} ist offen!"

    # Die exakte RECHNUNGSNUMMER im Direkttreffer-Pfad der Suche: nichts. (Die
    # Rechnung muss dafür veröffentlicht sein — vorher hat sie keine Nummer. Das
    # Freigabetor A-28 verlangt genau einen primären Empfänger.)
    beleg_service.add_invoice_party(
        welt["chef"].id, invoice_id=a["rechnung"].id, party_id=a["mieter"].id,
        role="INVOICE_RECIPIENT", is_primary=True,
    )
    # A-27: Der Schuldner der Rechnung muss am AUFTRAG als Rechnungsschuldner
    # bestätigt sein (sonst stellt der Betrieb jemandem etwas in Rechnung, den
    # niemand beauftragt hat).
    auftrag_service.add_work_order_party(
        welt["chef"].id, work_order_id=a["auftrag"].id, party_id=a["mieter"].id,
        role="INVOICE_DEBTOR", is_primary=True,
    )
    # B-08: veröffentlicht wird nur auf einem kaufmännisch geprüften Auftrag.
    auftrag_service.advance_status(
        welt["chef"].id, work_order_id=a["auftrag"].id,
        to_status="KAUFMAENNISCH_GEPRUEFT",
    )
    rechnung = beleg_service.publish_invoice(
        welt["chef"].id, invoice_id=a["rechnung"].id
    )
    assert rechnung.invoice_number, "Voraussetzung: die DB hat eine Nummer vergeben"
    r = c.get("/api/suche", {"q": rechnung.invoice_number})
    assert r.status_code == 200
    assert r.json()["direkttreffer"] is None
    assert "RECHNUNG" not in {t["typ"] for t in r.json()["treffer"]}
    _kein_geld(r, wo="Suche (exakte Rechnungsnummer)")

    # Und das preisführende Angebot (inkl. PDF und Kalkulation) bleibt ebenfalls zu.
    qid = a["angebot"].id
    for pfad in (
        "/api/invoicing/quotes",
        f"/api/invoicing/quotes/{qid}",
        f"/api/invoicing/quotes/{qid}/pdf",
        f"/api/invoicing/quotes/{qid}/kalkulation",
    ):
        assert c.get(pfad).status_code == 403, f"{pfad} ist offen!"


@pytest.mark.django_db
def test_monteur_kommt_nicht_ueber_die_datei_api_an_den_beleg(welt):
    """Der Umweg, den man vergisst: **das archivierte Beleg-PDF**.

    Das Angebots-PDF trägt Preise, die ZUGFeRD-Datei sogar maschinenlesbar. Beide
    werden beim ersten Abruf als `content.file` archiviert und hängen an `quote_id`
    bzw. `invoice_id`. Die Datei-API (`api/dateien.py::_ziel_guard`) kennt diese
    Zielarten für row_scope EIGENE **nicht** — sie sind fail-closed 403, und der
    Download-Guard führt keinen Beleg-Zweig. Dieser Test hält das fest: Es ist die
    Stelle, an der ein „kleiner Komfort-Zweig" den ganzen Slice aufhebeln würde.
    """
    c, a = welt["client"], welt["A"]

    for query in (
        f"quote_id={a['angebot'].id}",
        f"invoice_id={a['rechnung'].id}",
    ):
        r = c.get(f"/api/content/files?{query}")
        assert r.status_code == 403, f"{query}: {r.status_code}"


@pytest.mark.django_db
def test_monteur_schreibt_kein_angebot(welt):
    """Lesen ist nicht Schreiben: kein Anlegen, kein Ändern, kein Versenden."""
    c, a = welt["client"], welt["A"]

    r = c.post(
        "/api/invoicing/quotes",
        data={"property_id": str(a["obj"].id), "title": "Eigenes Angebot"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    r = c.put(
        f"/api/invoicing/quotes/{a['angebot_entwurf'].id}",
        data={"title": "Umbenannt"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    r = c.post(
        f"/api/invoicing/quotes/{a['angebot_entwurf'].id}/send", data={},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    r = c.post(
        "/api/invoicing/invoices/aus-angebot",
        data={"quote_id": str(a["angebot"].id)},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_scope_alle_sieht_unveraendert_alles(welt):
    """**Regression.** Die Objektsicht darf niemandem etwas wegnehmen.

    Ein Konto mit row_scope ALLE (ADMINISTRATION) liest Angebot und Rechnung
    weiterhin mit **allen** Beträgen — inklusive Einkaufspreis und Aufschlag.
    """
    a = welt["A"]
    c = logged_in_client("ADMINISTRATION")

    r = c.get(f"/api/invoicing/quotes/{a['angebot'].id}")
    assert r.status_code == 200, r.content
    (pos,) = r.json()["lines"]
    assert pos["unit_price"] == "1234.56"
    assert pos["unit_cost"] == "999.99"
    # numeric(x,3) → „37.250". Der Textscan über die Mengensicht sucht „37.25" und
    # trifft damit auch diese Schreibweise (Teilstring) — Absicht.
    assert pos["markup_percent"] == "37.250"
    assert r.json()["net_total"] == "14814.72"

    assert c.get(f"/api/invoicing/invoices/{a['rechnung'].id}").status_code == 200
    assert c.get("/api/invoicing/quotes").json()["total"] >= 4

    # Und die Mengensicht gibt es auch für ihn — als Arbeitsansicht, ohne Beschnitt-
    # Behauptung: `preise_ausgeblendet` ist false, und sie zeigt ALLE Angebote.
    r = c.get("/api/invoicing/quotes/mengen")
    assert r.status_code == 200, r.content
    daten = r.json()
    assert daten["total"] >= 4, "Scope ALLE sieht auch die Entwürfe."
    assert all(i["preise_ausgeblendet"] is False for i in daten["items"])

    # Dossier: alle Geld-Bausteine da, die Mengenliste bleibt weg (kein EIGENE).
    d = c.get(f"/api/dossier/auftrag/{a['auftrag'].id}").json()
    assert d["belege_sichtbar"] is True and d["angebote"] is not None
    assert d["angebote_mengen_sichtbar"] is False and d["angebote_mengen"] is None


# ===========================================================================
# ER DARF NICHT SCHREIBEN — außer Räume an A (und Dateien wie bisher)
# ===========================================================================

@pytest.mark.django_db
def test_monteur_erfasst_raeume_und_struktur_an_a(welt):
    """Der EINE Schreibfall, für den die Objektsicht gebaut wurde."""
    c, a = welt["client"], welt["A"]

    r = c.post(
        f"/api/property/properties/{a['obj'].id}/rooms",
        data={"name": "Heizraum", "floor_area_m2": "12.5", "room_height_m": "2.5"},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    raum_id = r.json()["id"]

    r = c.patch(
        f"/api/property/rooms/{raum_id}",
        data={"note": "Zentralanlage steht hier."},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content

    # Gebäude/Einheiten an MEINEM Objekt: erlaubt.
    r = c.post(
        f"/api/property/properties/{a['obj'].id}/buildings",
        data={"building_number": "2", "name": "Hinterhaus"},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    gebaeude_id = r.json()["id"]
    r = c.post(
        f"/api/property/buildings/{gebaeude_id}/units",
        data={"unit_type": "APARTMENT", "unit_number": "WE9"},
        content_type=JSON,
    )
    assert r.status_code == 201, r.content
    einheit_id = r.json()["id"]

    # Und sie KORRIGIEREN (Migration 0124, Befunde I1/I7/I12). Genau dafür
    # gaten die Bearbeiten-Knöpfe im Leitstand auf `darf`, nicht `darfAlle`:
    # Das namenlose Gebäude fällt dem auf, der davorsteht — dem Monteur mit
    # Objektsicht. Ohne diesen Test bliebe der positive Fall unbelegt.
    r = c.patch(
        f"/api/property/buildings/{gebaeude_id}",
        data={"name": "Hinterhaus West"},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["name"] == "Hinterhaus West"

    r = c.patch(
        f"/api/property/units/{einheit_id}",
        data={"storey": "2. OG"},
        content_type=JSON,
    )
    assert r.status_code == 200, r.content
    assert r.json()["storey"] == "2. OG"


@pytest.mark.django_db
def test_monteur_erfasst_keinen_raum_an_b(welt):
    c, b = welt["client"], welt["B"]
    r = c.post(
        f"/api/property/properties/{b['obj'].id}/rooms",
        data={"name": "Fremdraum", "floor_area_m2": "10", "room_height_m": "2.5"},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content
    r = c.post(
        f"/api/property/properties/{b['obj'].id}/buildings",
        data={"building_number": "9"},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content

    # Auch nicht KORRIGIEREN (0124). 404, nicht 403 — eine 403 verriete, dass
    # es das fremde Gebäude gibt. Gebäude und Einheit kommen direkt aus der
    # Fixture (`_objekt` legt beide für A UND B an), damit der Test nicht
    # still leerlaufen kann.
    r = c.patch(
        f"/api/property/buildings/{b['gebaeude'].id}",
        data={"name": "Übernommen"},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content

    r = c.patch(
        f"/api/property/units/{b['einheit'].id}",
        data={"storey": "EG"},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_monteur_schreibt_sonst_nichts(welt):
    """Die Verbotsliste, Zeile für Zeile — **auch an seinem eigenen Objekt A**."""
    c, a = welt["client"], welt["A"]

    # Keine neue Liegenschaft (er darf sich keine Sichtbarkeit erfinden).
    r = c.post(
        "/api/property/properties",
        data={"name": "Erfundenes Haus", "property_type": "WEG", "street": "X",
              "postal_code": "10000", "city": "Berlin"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    # Kein Bauteilkatalog-Eintrag (er ist GLOBAL — er gälte für alle Objekte).
    r = c.post(
        "/api/property/component-templates",
        data={"kind": "FLAECHE", "name": "Meine Wand"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    # …aber LESEN darf er ihn (ohne Katalog kein Aufmaß).
    assert c.get("/api/property/component-templates").status_code == 200

    # Keine Auslegungsdaten (Planungsvorgabe des Betriebs, wirkt auf ALLE Räume).
    r = c.patch(
        f"/api/property/properties/{a['obj'].id}/auslegung",
        data={"heat_load_w_per_m2": "100"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    # Kein Statuswechsel am Auftrag, keine Beteiligten, keine Abrechnungsart.
    r = c.post(
        f"/api/workflow/work_orders/{a['auftrag'].id}/status",
        data={"to_status": "FREIGEGEBEN"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    r = c.post(
        f"/api/workflow/work_orders/{a['auftrag'].id}/parties",
        data={"party_id": str(a["mieter"].id), "role": "OCCUPANT"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    # Keine Beteiligtenrolle an der Liegenschaft (Stammdatenpflege der Verwaltung).
    r = c.post(
        f"/api/property/properties/{a['obj'].id}/parties",
        data={"party_id": str(a["mieter"].id), "role": "CARETAKER",
              "valid_from": "2026-01-01"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    # Kein Kontakt anlegen, kein Kontaktweg ändern.
    r = c.post(
        "/api/identity/parties/person",
        data={"first_name": "Neu", "last_name": "Erfunden"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content

    # Kein Vorgangs-Statuswechsel, kein Projekt-Logbuch.
    r = c.post(
        f"/api/workflow/service_cases/{a['vorgang'].id}/status",
        data={"to_status": "IN_PRUEFUNG"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content
    r = c.post(
        f"/api/workflow/projects/{a['projekt'].id}/log",
        data={"category": "NOTIZ", "entry": "Heimlich"},
        content_type=JSON,
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_projektlogbuch_und_checklisten_gehen_nicht_an_die_objektsicht(welt):
    """**Review-Befund.** Die einzige Stelle des Slices, an der Projektinhalte **ohne
    Objektbezug** an die Objektsicht gingen.

    Ein Projekt gilt schon als „meins", wenn EINE seiner Liegenschaften meine ist —
    ein Logbucheintrag ist aber **Freitext**, der ein fremdes Objekt beim Namen nennen
    kann („Abstimmung mit der Verwaltung wegen Kantstraße 42"). Keine Spalte sagt mir
    das vorher, also lässt sich das nicht begrenzen: **fail-closed 403**, und im
    Dossier `projektsteuerung_sichtbar: false` (kein stilles Weglassen).

    Das ist auch fachlich richtig: Logbuch und Checkliste sind Bürokommunikation und
    Projektsteuerung, kein Baustellenwissen. Was der Monteur braucht, bekommt er
    objektgenau über das Liegenschafts-Dossier.
    """
    c, a = welt["client"], welt["A"]
    chef = welt["chef"].id

    # Ein Eintrag im Logbuch SEINES Projekts, der das FREMDE Objekt beim Namen nennt.
    # Der Marker ist bewusst **ASCII**: Django escapt Nicht-ASCII im JSON
    # („Kantstraße" → „Kantstra\\u00dfe"), ein `"Kantstraße" not in content` wäre
    # also trivial wahr — eine Leak-Probe, die nichts prüft.
    verraeter = "Abstimmung mit Verwaltung wegen Beta-Hof, Kantstrasse 42"
    projekt_service.add_project_log(
        chef, project_id=a["projekt"].id, category="NOTIZ", entry=verraeter,
    )

    # Das Projekt selbst ist für ihn sichtbar …
    assert c.get(f"/api/workflow/projects/{a['projekt'].id}").status_code == 200

    # … Logbuch und Checklisten aber nicht (403, mit Grund — keine leere Liste).
    r = c.get(f"/api/workflow/projects/{a['projekt'].id}/log")
    assert r.status_code == 403, r.content
    assert verraeter not in r.content.decode()
    r = c.get(f"/api/workflow/projects/{a['projekt'].id}/checklists")
    assert r.status_code == 403, r.content

    # Und im Dossier: Flag false, Bausteine null, kein Freitext in der Antwort.
    r = c.get(f"/api/dossier/projekt/{a['projekt'].id}")
    assert r.status_code == 200, r.content
    d = r.json()
    assert d["projektsteuerung_sichtbar"] is False
    assert d["logbuch"] is None and d["checklisten"] is None
    assert verraeter not in r.content.decode()
    assert "Beta-Hof" not in r.content.decode()

    # Gegenprobe: Für Scope ALLE ist beides unverändert da.
    admin = logged_in_client("ADMINISTRATION")
    assert (
        admin.get(f"/api/workflow/projects/{a['projekt'].id}/log").status_code == 200
    )
    ra = admin.get(f"/api/dossier/projekt/{a['projekt'].id}")
    assert ra.json()["projektsteuerung_sichtbar"] is True
    assert verraeter in ra.content.decode()


@pytest.mark.django_db
def test_monteur_aendert_den_bericht_des_kollegen_nicht(welt):
    """Lesen ja, schreiben nein. Ein Baustellenbericht wird unterschrieben und
    versiegelt — wer ihn schreibt, behauptet, dort gewesen zu sein."""
    c, a = welt["client"], welt["A"]

    # Er liest ihn (siehe oben) …
    assert c.get(f"/api/workflow/site_reports/{a['bericht'].id}").status_code == 200

    # … aber ändert, besiegelt und bepositioniert ihn nicht.
    r = c.put(
        f"/api/workflow/site_reports/{a['bericht'].id}",
        data={"remarks": "Heimlich geändert"},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content
    r = c.put(
        f"/api/workflow/site_reports/{a['bericht'].id}/positionen",
        data={"lines": []},
        content_type=JSON,
    )
    assert r.status_code == 404, r.content
    a["bericht"].refresh_from_db()
    assert a["bericht"].remarks is None


@pytest.mark.django_db
def test_monteur_laedt_keine_datei_in_die_objektakte(welt, fake_storage):
    """Die Objektsicht ist eine **LESE**-Sicht: Er lädt die Objektakte herunter,
    aber er schreibt nicht hinein (Upload nur am eigenen Einsatz/Bericht)."""
    c, a = welt["client"], welt["A"]

    def _upload(**ziel):
        return c.post(
            "/api/content/files",
            data={"datei": _png(), **{k: str(v) for k, v in ziel.items()}},
        )

    assert _upload(property_id=a["obj"].id).status_code == 403
    assert _upload(work_order_id=a["auftrag"].id).status_code == 403
    assert _upload(unit_id=a["einheit"].id).status_code == 403
    assert _upload(service_case_id=a["vorgang"].id).status_code == 403
    # Am EIGENEN Einsatz aber sehr wohl (unverändert wie vorher).
    assert _upload(service_job_id=welt["mein_job"].id).status_code == 201


def _png():
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("foto.png", PNG_1x1, content_type="image/png")


# ===========================================================================
# REGRESSION: Scope ALLE bleibt unverändert
# ===========================================================================

@pytest.mark.django_db
def test_administration_sieht_weiterhin_beide_objekte(welt):
    """Gegenprobe: Ein Konto mit row_scope ALLE verhält sich **unverändert** — die
    Objektsicht darf niemandem etwas wegnehmen."""
    admin = logged_in_client("ADMINISTRATION")
    a, b = welt["A"], welt["B"]

    ids = {i["id"] for i in admin.get("/api/property/properties").json()["items"]}
    assert {str(a["obj"].id), str(b["obj"].id)} <= ids

    for objekt in (a, b):
        assert (
            admin.get(f"/api/dossier/liegenschaft/{objekt['obj'].id}").status_code == 200
        )
        assert (
            admin.get(f"/api/workflow/site_reports/{objekt['bericht'].id}").status_code
            == 200
        )
        assert (
            admin.get(f"/api/identity/parties/{objekt['mieter'].id}").status_code == 200
        )
    # Wartung: beide Objekte, unbegrenzt.
    vertraege = {v["id"] for v in admin.get("/api/maintenance/contracts").json()["items"]}
    assert {str(a["vertrag"].id), str(b["vertrag"].id)} <= vertraege
    assert (
        admin.get(f"/api/maintenance/contracts/{b['vertrag'].id}").status_code == 200
    )

    # Und die Geld-Bausteine sind für ihn da.
    d = admin.get(f"/api/dossier/liegenschaft/{a['obj'].id}").json()
    assert d["offene_posten_sichtbar"] is True
    assert d["wartung_sichtbar"] is True
