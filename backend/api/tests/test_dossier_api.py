"""Entitäts-Dossiers (Slice 3): vier rechtegefilterte Read-Endpunkte.

Die Lehre aus Welle 5 steht über dieser Datei: **„Grün" hieß bisher zuverlässig
„der Normalfall stimmt"** — die Fehler wohnten in den Sonderfällen, die Geld
bewegen. Deshalb prüft diese Suite nicht das Dossier „im Großen und Ganzen",
sondern namentlich die Fälle, an denen es brechen würde:

  1. `test_teilsicht_ohne_invoicing_*`   — Recht fehlt → Baustein null, KEIN 403,
                                            kein Leak (Rolle DISPOSITION).
  2. `test_ohne_pricing_keine_marge`     — Marge null + marge_sichtbar=False,
                                            Rest des Dossiers vollständig.
  3. `test_monteur_ohne_objekt_bekommt_kein_dossier` — row_scope EIGENE ohne Objekt
                                            → 404 und KEINE Daten (Objektsicht 0099;
                                            der positive Fall steht in
                                            `test_monteur_objektsicht.py`).
  4. `test_unbekannte_*_404`             — fremde/unbekannte UUID → 404, nicht 403.
  5. `test_zahlungsverhalten_ohne_zahlung_ist_null` — null, NICHT „0 Tage Verzug".
  6. `test_stornierte_rechnung_*` / `test_gutschrift_ist_keine_verspaetete_zahlung`
  7. `test_marge_ohne_ek_ist_unbekannt`  — null + ek_vollstaendig=False, nie 0/100 %.
  8. `test_projekt_marge_gleich_dashboard` — dieselbe Zahl, kein Zweit-Rechenweg.
  9. `test_auftrag_dossier_gleich_einzelendpunkte` — soll_ist/offene_abrechnung
                                            identisch zu ihren Einzel-Endpunkten.
 10. `test_attest_taucht_in_keinem_dossier_auf` — auch nicht für content/LESEN ALLE.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from db_core.models import AppUser, RolePermission
from db_core.services import abrechnung as abrechnung_service
from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import dateien as dateien_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service
from db_core.services import rechte_pflege
from db_core.services import site_report as report_service

from .conftest import logged_in_client, make_app_user

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    """Objektspeicher-Attrappe (Unterschrift/Datei-Upload ohne MinIO)."""

    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        return None

    def get_object(self, key):
        return b""

    def remove_object(self, key):
        pass


@pytest.fixture
def fake_storage(monkeypatch):
    from db_core import storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage", lambda: FakeStorage())


# ---------------------------------------------------------------------------
# Szenario: ein Projekt mit Liegenschaft, Auftrag, Bericht, Zeiten und Rechnungen
# ---------------------------------------------------------------------------

def _gepruefter_auftrag(actor, obj, kunde, projekt=None, *, bis="KAUFMAENNISCH_GEPRUEFT"):
    order = auftrag_service.create_work_order(
        actor, property_id=obj.id, title="Heizung erneuern",
        project_id=projekt.id if projekt else None,
    )
    auftrag_service.set_order_evidence(
        actor, work_order_id=order.id, reference="Auftrag per Mail"
    )
    auftrag_service.confirm_responsibility(
        actor, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            actor, work_order_id=order.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(actor, work_order_id=order.id, to_status=to)
        if to == bis:
            break
    order.refresh_from_db()
    return order


def _rechnung(actor, obj, kunde, order, *, projekt=None, lines, faellig_vor_tagen=30,
              publish=True):
    inv = beleg_service.create_invoice(
        actor, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id, project_id=projekt.id if projekt else None,
        invoice_date=date.today() - timedelta(days=faellig_vor_tagen + 14),
        due_date=date.today() - timedelta(days=faellig_vor_tagen),
        lines=lines,
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            actor, invoice_id=inv.id, party_id=kunde.id, role=role, is_primary=True
        )
    if publish:
        beleg_service.publish_invoice(actor, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


@pytest.fixture
def szenario(app_user, fake_storage):
    """Ein vollständiges Szenario: Kontakt · Liegenschaft · Projekt · Auftrag.

    Enthält absichtlich beide Marge-Fälle: eine Position MIT EK-Snapshot und eine
    OHNE — damit `ek_vollstaendig=False` und die Marge sich nur auf den bekannten
    Anteil bezieht (nie 0 %, nie 100 %).
    """
    actor = app_user.id
    obj = property_service.create_property(
        actor, name="Lindenstraße 14", property_type="WEG",
        street="Lindenstraße", house_number="14", postal_code="34117", city="Kassel",
    )
    kunde = identity_service.create_person(
        actor, first_name="Karla", last_name="Kundin"
    )
    projekt = projekt_service.create_project(
        actor, name="Heizungssanierung", property_ids=[obj.id]
    )
    order = _gepruefter_auftrag(actor, obj, kunde, projekt)

    inv = _rechnung(
        actor, obj, kunde, order, projekt=projekt,
        lines=[
            # MIT EK → geht in die Marge ein.
            {"line_type": "MATERIAL", "description": "Kupferrohr", "quantity": 10,
             "unit": "m", "unit_price": "20.00", "unit_cost": "12.00",
             "tax_code": "DE_19"},
            # OHNE EK → Marge unbekannt für diesen Anteil (ek_vollstaendig=False).
            {"line_type": "ARBEITSZEIT", "description": "Montage", "quantity": 4,
             "unit": "h", "unit_price": "50.00", "tax_code": "DE_19"},
        ],
    )
    return {
        "actor": actor, "obj": obj, "kunde": kunde, "projekt": projekt,
        "order": order, "inv": inv,
    }


@pytest.fixture
def dispo_client(db):
    """DISPOSITION: identity/property/workflow/content — aber KEIN invoicing,
    KEIN pricing (Migration 0026). Genau die Teilsicht, um die es in Bruchfall 1
    geht."""
    return logged_in_client("DISPOSITION")


@pytest.fixture
def monteur_client(db):
    """MONTEUR mit row_scope EIGENE — und **ohne jeden Einsatz**, also ohne Objekt.

    Genau das ist Bruchfall 3: Die Objektsicht (0099) gibt ihm nicht „irgendein"
    Dossier, sondern das seiner Objekte. Hat er keins, bekommt er keins.
    """
    return logged_in_client("MONTEUR")


@pytest.fixture
def buchhaltung_ohne_pricing(db):
    """BUCHHALTUNG, aber `pricing/LESEN` entzogen (Bruchfall 2).

    Keine Standardrolle trägt invoicing/LESEN OHNE pricing/LESEN — die Matrix wird
    deshalb gezielt eingeschränkt. Das ist kein Kunstgriff, sondern ein realer
    Betriebsfall: Wer Rechnungen sehen soll, muss nicht die Einkaufspreise sehen.
    """
    pfleger = make_app_user("Rechte-Pflege")  # keine eigene Rolle → keine Selbst-Erweiterung
    rechte_pflege.set_permission(
        pfleger.id, role_code="BUCHHALTUNG", module="pricing", action="LESEN",
        allowed=False, row_scope="ALLE",
    )
    assert not RolePermission.objects.get(
        role_id="BUCHHALTUNG", module="pricing", action="LESEN"
    ).allowed
    return logged_in_client("BUCHHALTUNG")


# ===========================================================================
# Normalfall (damit die Bruchfälle etwas haben, wogegen sie sich abheben)
# ===========================================================================

@pytest.mark.django_db
def test_kontakt_dossier_vollstaendig(admin_client, szenario):
    r = admin_client.get(f"/api/dossier/kontakt/{szenario['kunde'].id}")
    assert r.status_code == 200
    b = r.json()
    assert b["kontakt"]["display_name"] == "Karla Kundin"
    assert b["offene_posten_sichtbar"] is True
    assert b["offene_posten"]["anzahl"] == 1
    assert b["offene_posten"]["posten"][0]["is_overdue"] is True
    # Auftrag des Kunden ist noch nicht abgerechnet → offen.
    assert [a["order_number"] for a in b["auftraege"]] == [
        szenario["order"].order_number
    ]


@pytest.mark.django_db
def test_liegenschaft_dossier_zutrittshinweis_mit_herkunft(admin_client, szenario):
    """Zutrittshinweise gibt es NUR am Einsatz — sie werden mit ihrer Herkunft
    geliefert (welcher Einsatz), kein erfundenes Objektfeld."""
    job = einsatz_service.create_service_job(
        szenario["actor"], work_order_id=szenario["order"].id,
        access_instructions="Schlüssel bei Hausmeister Meier, Klingel 3",
    )
    b = admin_client.get(f"/api/dossier/liegenschaft/{szenario['obj'].id}").json()
    assert b["liegenschaft"]["property_number"] == szenario["obj"].property_number
    hinweise = b["zutrittshinweise"]
    assert len(hinweise) == 1
    assert hinweise[0]["hinweis"].startswith("Schlüssel bei Hausmeister")
    # Die Herkunft ist benannt: welcher Einsatz, welcher Auftrag.
    assert hinweise[0]["service_job_id"] == str(job.id)
    assert hinweise[0]["work_order_number"] == szenario["order"].order_number


@pytest.mark.django_db
def test_auftrag_dossier_moegliche_uebergaenge_aus_der_tabelle(admin_client, szenario):
    """Die möglichen Übergänge kommen aus WORK_ORDER_TRANSITIONS — der Service
    gibt sie aus, er erfindet sie nicht."""
    b = admin_client.get(f"/api/dossier/auftrag/{szenario['order'].id}").json()
    assert b["auftrag"]["status"] == "KAUFMAENNISCH_GEPRUEFT"
    erwartet = auftrag_service.WORK_ORDER_TRANSITIONS["KAUFMAENNISCH_GEPRUEFT"]
    geliefert = {u["to_status"]: u["begruendung_pflicht"] for u in b["moegliche_uebergaenge"]}
    assert geliefert == erwartet
    # STORNIERT ist begründungspflichtig, ABGERECHNET nicht — 1:1 aus der Tabelle.
    assert geliefert["STORNIERT"] is True
    assert geliefert["ABGERECHNET"] is False


# ===========================================================================
# BRUCHFALL 1 — Rechte-Teilsicht: Inhalt ja, Geld nein. Kein 403, kein Leak.
# ===========================================================================

@pytest.mark.django_db
def test_teilsicht_ohne_invoicing_projekt(dispo_client, szenario):
    """DISPOSITION (workflow/LESEN, kein invoicing/LESEN) bekommt das
    Projekt-Dossier MIT Inhalt — aber offene Posten/Belege/Marge sind `null` und
    als unsichtbar gekennzeichnet. **Kein 403 auf die ganze Antwort.**"""
    r = dispo_client.get(f"/api/dossier/projekt/{szenario['projekt'].id}")
    assert r.status_code == 200
    b = r.json()
    # Der Kern ist da:
    assert b["projekt"]["name"] == "Heizungssanierung"
    assert len(b["auftraege"]) == 1
    assert len(b["liegenschaften"]) == 1
    # Das Geld ist es nicht — und das Flag sagt warum:
    assert b["offene_posten_sichtbar"] is False
    assert b["offene_posten"] is None
    assert b["belege_sichtbar"] is False
    assert b["rechnungen"] is None
    assert b["angebote"] is None
    assert b["anrechenbare_abschlaege"] is None
    assert b["marge_sichtbar"] is False
    assert b["marge"] is None
    # Kein Leak über die Serialisierung: nirgends ein Betrag im Rohtext.
    assert "20.00" not in r.content.decode()


@pytest.mark.django_db
def test_teilsicht_ohne_invoicing_kontakt(dispo_client, szenario):
    """Gleiches Bild am Kontakt: Stammdaten ja, Zahlungsverhalten nein."""
    r = dispo_client.get(f"/api/dossier/kontakt/{szenario['kunde'].id}")
    assert r.status_code == 200
    b = r.json()
    assert b["kontakt"]["display_name"] == "Karla Kundin"
    assert b["vorgaenge_sichtbar"] is True
    assert b["offene_posten_sichtbar"] is False
    assert b["offene_posten"] is None
    assert b["zahlungsverhalten_sichtbar"] is False
    assert b["zahlungsverhalten"] is None


@pytest.mark.django_db
def test_teilsicht_ohne_invoicing_auftrag(dispo_client, szenario):
    """Auftrags-Dossier ohne invoicing: Soll-Ist und Zeiten ja (keine Preise),
    Abrechnungsstand nein (er führt Einzelpreise)."""
    b = dispo_client.get(f"/api/dossier/auftrag/{szenario['order'].id}").json()
    assert b["soll_ist"] is not None          # Kern: workflow, ohne Geldbeträge
    assert b["abrechnung_sichtbar"] is False
    assert b["abrechnung"] is None
    assert b["offene_posten"] is None


# ===========================================================================
# BRUCHFALL 2 — pricing fehlt: Marge null, Rest vollständig
# ===========================================================================

@pytest.mark.django_db
def test_ohne_pricing_keine_marge(buchhaltung_ohne_pricing, szenario):
    """invoicing/LESEN ja, pricing/LESEN nein → Umsatz sichtbar, Marge `null`.

    Der EK ist ein Kalkulationsdatum (`pricing`). Ohne das Recht gibt es keine
    Marge — aber sehr wohl das übrige Dossier (kein 403)."""
    r = buchhaltung_ohne_pricing.get(f"/api/dossier/projekt/{szenario['projekt'].id}")
    assert r.status_code == 200
    b = r.json()
    assert b["marge_sichtbar"] is False
    assert b["marge"] is None
    assert b["geplante_marge"] is None
    # …und der Rest ist vollständig da (inkl. Geld):
    assert b["belege_sichtbar"] is True
    assert len(b["rechnungen"]) == 1
    assert b["offene_posten"]["anzahl"] == 1
    assert len(b["auftraege"]) == 1


# ===========================================================================
# BRUCHFALL 3 — MONTEUR (row_scope EIGENE): nur SEINE Objekte, nie fremde
# ===========================================================================

@pytest.mark.django_db
@pytest.mark.parametrize("pfad", ["kontakt", "liegenschaft", "projekt", "auftrag"])
def test_monteur_ohne_objekt_bekommt_kein_dossier(monteur_client, szenario, pfad):
    """Objektsicht (0099): Der Monteur bekommt das Dossier **seiner Objekte** — der
    Liegenschaften, an denen er je einen Einsatz hatte. Dieser Monteur hat **keinen**
    Einsatz, also kein Objekt: **404** auf alle vier Dossiers (nicht mehr 403 — die
    Existenz der Entität wird nicht verraten).

    Entscheidend ist die zweite Zusicherung: **keine Daten in der Antwort**. Ein
    `require_scoped` ohne Objekt-Guard wäre hier ein 200 mit dem vollen Dossier —
    genau der stille Datenleak, den dieser Test unmöglich machen soll.

    Der positive Fall (der Monteur MIT Einsatz sieht sein Objekt inkl. der Historie
    der Kollegen, aber ohne Geld) steht in `test_monteur_objektsicht.py`.
    """
    ziel = {
        "kontakt": szenario["kunde"].id,
        "liegenschaft": szenario["obj"].id,
        "projekt": szenario["projekt"].id,
        "auftrag": szenario["order"].id,
    }[pfad]
    r = monteur_client.get(f"/api/dossier/{pfad}/{ziel}")
    assert r.status_code == 404, (
        f"{pfad}: Monteur ohne Einsatz hat kein Objekt — weder Daten noch 200."
    )
    assert "Karla" not in r.content.decode()
    assert "Lindenstraße" not in r.content.decode()


# ===========================================================================
# BRUCHFALL 4 — unbekannte/fremde UUID → 404 (nicht 403)
# ===========================================================================

@pytest.mark.django_db
@pytest.mark.parametrize("pfad", ["kontakt", "liegenschaft", "projekt", "auftrag"])
def test_unbekannte_uuid_404(admin_client, szenario, pfad):
    r = admin_client.get(f"/api/dossier/{pfad}/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_falsche_entitaet_ist_404_nicht_200(admin_client, szenario):
    """Eine gültige UUID der FALSCHEN Art ist kein Treffer (kein Objekt-Mixup)."""
    r = admin_client.get(f"/api/dossier/projekt/{szenario['obj'].id}")
    assert r.status_code == 404


# ===========================================================================
# BRUCHFALL 5 — Zahlungsverhalten ohne jede Zahlung: null, NICHT 0 Tage Verzug
# ===========================================================================

@pytest.mark.django_db
def test_zahlungsverhalten_ohne_zahlung_ist_null(admin_client, szenario):
    """Nie gezahlt → Verzögerung **unbekannt** (null), nicht „0 Tage".

    Eine 0 hieße „zahlt pünktlich" — eine Behauptung über einen Kunden, von dem
    wir nichts wissen. Das ist die Hausinvariante (fehlender EK → VK unbekannt,
    nie 0), hier auf Tage angewandt.
    """
    b = admin_client.get(f"/api/dossier/kontakt/{szenario['kunde'].id}").json()
    zv = b["zahlungsverhalten"]
    assert zv["rechnungen_gesamt"] == 1
    assert zv["bezahlt_anzahl"] == 0
    assert zv["ueberfaellig_anzahl"] == 1
    assert zv["durchschnittliche_verzoegerung_tage"] is None
    assert zv["groesste_verzoegerung_tage"] is None
    assert zv["bewertete_rechnungen"] == 0


@pytest.mark.django_db
def test_zahlungsverhalten_rechnet_verzug_aus_der_letzten_zahlung(
    admin_client, szenario, app_user
):
    """Vollständig bezahlt → Verzug = letzte Zahlung − Fälligkeit (hier +5 Tage)."""
    inv = szenario["inv"]
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=inv.gross_total,
        paid_at=inv.due_date + timedelta(days=5),
    )
    zv = admin_client.get(
        f"/api/dossier/kontakt/{szenario['kunde'].id}"
    ).json()["zahlungsverhalten"]
    assert zv["bezahlt_anzahl"] == 1
    assert zv["offen_anzahl"] == 0
    assert zv["durchschnittliche_verzoegerung_tage"] == 5.0
    assert zv["bewertete_rechnungen"] == 1


# ===========================================================================
# BRUCHFALL 6 — Storno und Gutschrift
# ===========================================================================

@pytest.mark.django_db
def test_stornierte_rechnung_ist_keine_ueberfaellige_forderung(
    admin_client, szenario, app_user
):
    """Eine stornierte Rechnung darf weder als offener Posten noch im
    Zahlungsverhalten als überfällige Forderung auftauchen.

    Sonst mahnte jemand Geld an, das niemand mehr schuldet — und die Statistik
    machte aus einem stornierten Beleg einen säumigen Kunden. Und: Der Stornobeleg
    selbst (negativer Betrag) ist ebenfalls keine Forderung gegen den Kunden.
    """
    beleg_service.create_cancellation(app_user.id, invoice_id=szenario["inv"].id)

    b = admin_client.get(f"/api/dossier/kontakt/{szenario['kunde'].id}").json()
    assert b["offene_posten"]["anzahl"] == 0
    assert b["offene_posten"]["summe_offen"] == "0.00"
    assert b["offene_posten"]["anzahl_ueberfaellig"] == 0
    zv = b["zahlungsverhalten"]
    assert zv["rechnungen_gesamt"] == 0
    assert zv["ueberfaellig_anzahl"] == 0
    assert zv["summe_ueberfaellig"] == "0.00"

    # Auch an der Liegenschaft und am Projekt (dieselbe Rechenstelle):
    for pfad, ziel in (
        ("liegenschaft", szenario["obj"].id),
        ("projekt", szenario["projekt"].id),
    ):
        op = admin_client.get(f"/api/dossier/{pfad}/{ziel}").json()["offene_posten"]
        assert op["anzahl"] == 0, pfad


@pytest.mark.django_db
def test_gutschrift_ist_keine_verspaetete_zahlung(admin_client, szenario, app_user):
    """Eine Gutschrift ist kein „zu spät bezahlte Rechnung".

    Der Kreditbeleg wird NICHT als Forderung gezählt (er fordert nichts) und darf
    das Zahlungsverhalten nicht verfälschen. Die **Teil**gutschrift lässt die
    Ursprungsrechnung stehen — sie fordert weiterhin Geld (dieselbe Grenze wie im
    Belegmodul: *Storno löst, Gutschrift nicht*).
    """
    credit = beleg_service.create_correction(
        app_user.id, invoice_id=szenario["inv"].id, positions=[2]
    )
    assert credit.invoice_type == "GUTSCHRIFT"

    b = admin_client.get(f"/api/dossier/kontakt/{szenario['kunde'].id}").json()
    zv = b["zahlungsverhalten"]
    # Genau EINE Forderung (die Rechnung) — die Gutschrift zählt nicht mit.
    assert zv["rechnungen_gesamt"] == 1
    assert zv["bezahlt_anzahl"] == 0
    assert zv["durchschnittliche_verzoegerung_tage"] is None
    nummern = [p["invoice_type"] for p in b["offene_posten"]["posten"]]
    assert nummern == ["RECHNUNG"]
    assert "GUTSCHRIFT" not in nummern


# ===========================================================================
# BRUCHFALL 7 + 8 — Marge: unbekannt statt 0/100 %, und identisch zum Dashboard
# ===========================================================================

@pytest.mark.django_db
def test_marge_ohne_ek_ist_unbekannt(admin_client, szenario):
    """Eine Position ohne EK macht die Marge NICHT zu 100 % (und nicht zu 0 %).

    Sie bezieht sich ausschließlich auf den Anteil MIT bekanntem EK; die Lücke wird
    ausgewiesen (`positionen_ohne_ek`, `ek_vollstaendig=False`).
    """
    b = admin_client.get(f"/api/dossier/projekt/{szenario['projekt'].id}").json()
    assert b["marge_sichtbar"] is True
    m = b["marge"]
    assert m["ek_vollstaendig"] is False
    assert m["positionen_ohne_ek"] == 1
    # 10 m × 20 € = 200 € mit EK (10 × 12 = 120) → DB 80 €, Marge 40 %.
    assert Decimal(m["net_mit_ek"]) == Decimal("200.00")
    assert Decimal(m["net_ohne_ek"]) == Decimal("200.00")   # Montage 4 h × 50 €
    assert Decimal(m["deckungsbeitrag"]) == Decimal("80.00")
    assert Decimal(m["marge_prozent"]) == Decimal("40.00")
    # …und ausdrücklich NICHT die naive Rechnung über den vollen Umsatz:
    assert Decimal(m["marge_prozent"]) not in (Decimal("0.00"), Decimal("100.00"))


@pytest.mark.django_db
def test_marge_ganz_ohne_ek_ist_null(admin_client, app_user, fake_storage):
    """Trägt KEINE Position einen EK, ist die Marge `null` — nie 0, nie 100 %."""
    actor = app_user.id
    obj = property_service.create_property(
        actor, name="EK-frei", property_type="WEG", street="A", house_number="1",
        postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(actor, first_name="Ohne", last_name="EK")
    projekt = projekt_service.create_project(actor, name="Ohne EK", property_ids=[obj.id])
    order = _gepruefter_auftrag(actor, obj, kunde, projekt)
    _rechnung(
        actor, obj, kunde, order, projekt=projekt,
        lines=[{"line_type": "MATERIAL", "description": "Ohne EK", "quantity": 1,
                "unit": "Stk", "unit_price": "100.00", "tax_code": "DE_19"}],
    )
    m = admin_client.get(f"/api/dossier/projekt/{projekt.id}").json()["marge"]
    assert m["deckungsbeitrag"] is None
    assert m["marge_prozent"] is None
    assert m["ek_vollstaendig"] is False


@pytest.mark.django_db
def test_projekt_marge_gleich_dashboard(admin_client, szenario):
    """Die Marge im Dossier IST die Marge des Auswertungs-Dashboards.

    Kein zweiter Rechenweg: `_marge_by_project` wird nur auf das Projekt
    vorgefiltert (sonst skalierte ein Einzel-Dossier mit der Firmengröße) — die
    Zahlen müssen deckungsgleich bleiben.
    """
    dossier = admin_client.get(
        f"/api/dossier/projekt/{szenario['projekt'].id}"
    ).json()["marge"]
    dashboard = admin_client.get("/api/auswertungen/projekte").json()
    zeile = next(
        p for p in dashboard["top_projects"]
        if p["project_id"] == str(szenario["projekt"].id)
    )
    assert dossier["ek_total"] == zeile["ek_total"]
    assert dossier["deckungsbeitrag"] == zeile["deckungsbeitrag"]
    assert dossier["marge_prozent"] == zeile["marge_prozent"]
    assert dossier["positionen_ohne_ek"] == zeile["positionen_ohne_ek"]
    assert dossier["ek_vollstaendig"] == zeile["ek_vollstaendig"]


@pytest.mark.django_db
def test_dashboard_marge_unveraendert_trotz_refactor(admin_client, szenario):
    """Regression: Der Vorfilter darf die BESTEHENDEN Aufrufer nicht verändern.

    Das Dashboard rechnet weiter über alle Projekte (ohne project_id) — die
    Gesamtmarge muss die Summe über die Belegzeilen bleiben.
    """
    d = admin_client.get("/api/auswertungen/projekte").json()
    assert d["marge_sichtbar"] is True
    assert Decimal(d["marge"]["net_mit_ek"]) == Decimal("200.00")
    assert Decimal(d["marge"]["deckungsbeitrag"]) == Decimal("80.00")
    assert d["marge"]["ek_vollstaendig"] is False


# ===========================================================================
# BRUCHFALL 9 — Auftrags-Dossier == die bestehenden Einzel-Endpunkte
# ===========================================================================

@pytest.mark.django_db
def test_auftrag_dossier_gleich_einzelendpunkte(admin_client, app_user, fake_storage):
    """`soll_ist` und `offene_abrechnung` im Dossier liefern **dieselben Werte**
    wie ihre bestehenden Endpunkte — Byte für Byte dieselbe Struktur.

    Wären es zwei Rechenwege, driftete einer irgendwann ab; das Dossier zeigte dann
    etwas anderes als die Auftragsmappe, und niemand wüsste, welcher Zahl zu trauen
    ist.
    """
    actor = app_user.id
    obj = property_service.create_property(
        actor, name="Regie", property_type="WEG", street="B", house_number="2",
        postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(actor, first_name="Rolf", last_name="Regie")
    order = _gepruefter_auftrag(actor, obj, kunde, bis="IN_AUSFUEHRUNG")
    abrechnung_service.set_billing_mode(
        actor, work_order_id=order.id, billing_mode="REGIE"
    )
    art = artikel_service.create_article(
        actor, article_number="A-1", description="Kupferrohr 18", unit="m",
        line_type="MATERIAL",
    )
    artikel_service.set_article_sale_price(
        actor, article_id=art.id, fixed_price=Decimal("25.00"), is_standard=True
    )
    bericht = report_service.create_report(
        actor, work_order_id=order.id, report_date=date.today(),
        activity_text="Rohre verlegt.",
    )
    report_service.set_report_lines(
        actor, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "source_article_id": str(art.id),
                "quantity": "7"}],
    )
    report_service.sign_report(
        actor, report_id=bericht.id, signed_by_name="Rolf Regie",
        signature_png=PNG_1x1,
    )

    dossier = admin_client.get(f"/api/dossier/auftrag/{order.id}").json()
    soll_ist = admin_client.get(
        f"/api/workflow/work_orders/{order.id}/soll-ist"
    ).json()
    offene = admin_client.get(
        f"/api/workflow/work_orders/{order.id}/offene-abrechnung"
    ).json()

    assert dossier["soll_ist"] == soll_ist
    assert dossier["abrechnung"] == offene
    # …und die Substanz stimmt auch: 7 m ohne Angebot = ZUSATZ, Preis bekannt.
    assert soll_ist["positionen"][0]["art"] == "ZUSATZ"
    assert offene["berichtspositionen"][0]["preis_status"] == "BEKANNT"
    assert dossier["berichte"][0]["status"] == "UNTERZEICHNET"


# ===========================================================================
# BRUCHFALL 10 — DSGVO: kein Attest in irgendeinem Dossier
# ===========================================================================

@pytest.mark.django_db
def test_attest_taucht_in_keinem_dossier_auf(admin_client, szenario, app_user):
    """Ein Attest (Gesundheitsdatum, DSGVO Art. 9) darf in KEINEM Dossier stehen —
    auch nicht für ein Konto mit `content/LESEN` und Scope ALLE.

    Zwei Riegel: Die Verknüpfung hängt per DB-CHECK an genau EINEM Objekt (einer
    Abwesenheit), und der Dossier-Service filtert Attest-Verknüpfungen zusätzlich
    aus. Der Test hängt bewusst **denselben Dateiinhalt** zusätzlich an das Projekt:
    Deduplizierte Bytes dürfen die Grenze nicht aufweichen.
    """
    actor = app_user.id
    person = identity_service.create_person(actor, first_name="Timo", last_name="Krank")
    mitarbeiter_app_user = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Timo Krank", status="ACTIVE", version=1
    )
    employee = mitarbeiter_service.create_employee(
        actor, app_user_id=mitarbeiter_app_user.id, party_id=person.id,
        hired_on=date(2026, 1, 1),
    )
    # Ohne gültigen Vertrag zählt die Abwesenheit keine Arbeitstage (Service-Tor).
    mitarbeiter_service.create_contract(
        actor, employee_id=employee.id, valid_from=date(2026, 1, 1),
        hours={f"hours_{d}": 8 for d in ("monday", "tuesday", "wednesday",
                                         "thursday", "friday")},
        vacation_days_per_year=30,
    )
    # Ein Zeitraum, der sicher Arbeitstage enthält (Mo–Do einer festen Woche).
    absence = mitarbeiter_service.create_absence(
        actor, employee_id=employee.id, absence_type="KRANKHEIT",
        start_date=date(2026, 7, 6), end_date=date(2026, 7, 9),
    )
    dateien_service.datei_hochladen(
        actor, dateiname="grippaler_infekt.pdf", inhalt=b"%PDF-1.4 attest",
        link_category="ATTEST", absence_id=absence.id,
    )
    # Dieselben Bytes zusätzlich am Projekt — der Dedup darf keine Brücke bauen.
    dateien_service.datei_hochladen(
        actor, dateiname="projektplan.pdf", inhalt=b"%PDF-1.4 attest",
        link_category="DOKUMENT", project_id=szenario["projekt"].id,
    )

    for pfad, ziel in (
        ("kontakt", szenario["kunde"].id),
        ("kontakt", person.id),
        ("liegenschaft", szenario["obj"].id),
        ("projekt", szenario["projekt"].id),
        ("auftrag", szenario["order"].id),
    ):
        r = admin_client.get(f"/api/dossier/{pfad}/{ziel}")
        assert r.status_code == 200
        roh = r.content.decode()
        assert "ATTEST" not in roh, f"{pfad}: Attest-Kategorie im Dossier!"
        assert "Arbeitsunfaehigkeit" not in roh, f"{pfad}: Attest im Dossier!"
        assert "grippal" not in roh.lower()
        for d in (r.json().get("dokumente") or []):
            assert d["link_category"] != "ATTEST"
