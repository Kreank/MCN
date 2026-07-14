"""Der gebundene Entwurf: Zeile anhängen geht, gebundene Zeile anfassen nicht.

Migration 0084 sperrte bei *irgendeiner* aktiven Bindung *jede* Positionsänderung
— auch das INSERT. Migration **0088** hat das verengt: Gesperrt sind nur UPDATE
und DELETE einer **gebundenen Zeile**.

Damit gilt für den Beleg aus dem Abrechnungslauf:

* Der **Editor** (`update_invoice`) bleibt zu — er ersetzt den ganzen
  Positionssatz per Delete+Insert und trifft dabei die gebundene Zeile (422).
* **Anhängen** (`add_invoice_line`) geht — Anfahrtspauschale, Rabatt, Zusatztext.
  Die Notbremse `bindungen_loesen` (die alle gebundenen Positionen verwirft)
  bleibt dem verunglückten Lauf vorbehalten.
* **Zurücknehmen** der letzten, ungebundenen Zeile geht; die gebundene nicht.

Geprüft wird gegen die echten Trigger, nicht gegen Service-Logik.
"""
from decimal import Decimal

import pytest

from db_core.models import Invoice, InvoiceLine
from db_core.services import abrechnung as abrechnung_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


@pytest.fixture
def gebundene_rechnung(app_user):
    """Rechnung (ENTWURF) aus einem Angebot — jede Position trägt eine Bindung."""
    obj = property_service.create_property(
        app_user.id, name="Bindungs-Objekt", property_type="WEG",
        street="Baustelle", house_number="2", postal_code="10115", city="Berlin",
    )
    identity_service.create_person(app_user.id, first_name="Karla", last_name="Kundin")
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot",
        lines=[{
            "line_type": "MATERIAL", "description": "Kupferrohr 18",
            "quantity": "10", "unit": "m", "unit_price": "12.50", "tax_code": "DE_19",
        }],
    )
    beleg_service.send_quote(app_user.id, quote_id=quote.id)
    invoice = abrechnung_service.rechnung_aus_angebot(app_user.id, quote_id=quote.id)
    return app_user, invoice


@pytest.mark.django_db
def test_editor_scheitert_am_gebundenen_positionssatz(gebundene_rechnung):
    """`update_invoice` ersetzt alles per Delete+Insert → die DB weist es ab."""
    user, invoice = gebundene_rechnung
    with pytest.raises(ValueError, match="gebunden"):
        beleg_service.update_invoice(
            user.id, invoice_id=invoice.id,
            lines=[{
                "line_type": "PAUSCHALE", "description": "Anfahrt",
                "quantity": "1", "unit": "Stk", "unit_price": "45.00",
                "tax_code": "DE_19",
            }],
        )


@pytest.mark.django_db
def test_neue_zeile_darf_angehaengt_werden(gebundene_rechnung):
    """Der Normalfall: Anfahrtspauschale auf die Regierechnung — ohne Notbremse."""
    user, invoice = gebundene_rechnung
    vorher = Decimal(invoice.net_total)

    beleg_service.add_invoice_line(
        user.id, invoice_id=invoice.id,
        line={
            "line_type": "PAUSCHALE", "description": "Anfahrtspauschale",
            "quantity": "1", "unit": "Stk", "unit_price": "45.00", "tax_code": "DE_19",
        },
    )

    zeilen = list(
        InvoiceLine.objects.filter(invoice_id=invoice.id).order_by("position_number")
    )
    assert [l.description for l in zeilen] == ["Kupferrohr 18", "Anfahrtspauschale"]
    # Die gebundene Zeile ist unangetastet geblieben (Nummer UND Betrag).
    assert zeilen[0].position_number == 1
    assert zeilen[0].net_amount == Decimal("125.00")
    # Der Server hat die Summen aus ALLEN Zeilen neu gerechnet, nicht addiert.
    invoice.refresh_from_db()
    assert Decimal(invoice.net_total) == vorher + Decimal("45.00")
    assert Decimal(invoice.gross_total) == Decimal(invoice.net_total) * Decimal("1.19")


@pytest.mark.django_db
def test_letzte_ungebundene_zeile_laesst_sich_zuruecknehmen(gebundene_rechnung):
    """Der Vertipper in der eben angehängten Zeile ist keine Sackgasse."""
    user, invoice = gebundene_rechnung
    beleg_service.add_invoice_line(
        user.id, invoice_id=invoice.id,
        line={
            "line_type": "PAUSCHALE", "description": "Anfahrt (falsch)",
            "quantity": "1", "unit": "Stk", "unit_price": "450.00", "tax_code": "DE_19",
        },
    )
    beleg_service.remove_last_invoice_line(user.id, invoice_id=invoice.id)

    zeilen = list(InvoiceLine.objects.filter(invoice_id=invoice.id))
    assert [l.description for l in zeilen] == ["Kupferrohr 18"]
    assert Decimal(Invoice.objects.get(id=invoice.id).net_total) == Decimal("125.00")


@pytest.mark.django_db
def test_gebundene_zeile_laesst_sich_nicht_zuruecknehmen(gebundene_rechnung):
    """Die Bindung ist der Nachweis der Abrechnung — sie fällt nicht nebenbei weg."""
    user, invoice = gebundene_rechnung
    with pytest.raises(ValueError, match="gebunden"):
        beleg_service.remove_last_invoice_line(user.id, invoice_id=invoice.id)
    assert InvoiceLine.objects.filter(invoice_id=invoice.id).count() == 1


def _auftrag_szenario(app_user, name, *, bis="KAUFMAENNISCH_GEPRUEFT"):
    """Objekt + Kunde + Auftrag mit erfüllten Freigabe-Toren (B-08 für die Rechnung)."""
    obj = property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Baustelle", house_number="7", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Sieglinde", last_name="Schuldner"
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Bad-Sanierung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Auftrag per Mail"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
        if to == bis:
            break
    return obj, kunde, order


def _beteiligte(app_user, invoice, kunde):
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=invoice.id, party_id=kunde.id,
            role=role, is_primary=True,
        )


@pytest.fixture
def veroeffentlichte_rechnung(app_user):
    """Eine veröffentlichte Rechnung (B-08: Auftrag kaufmännisch geprüft)."""
    obj, kunde, order = _auftrag_szenario(app_user, "Publizierte-Objekt")
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "PAUSCHALE", "description": "Leistung",
                "quantity": 1, "unit_price": "500.00", "tax_code": "DE_19"}],
    )
    _beteiligte(app_user, inv, kunde)
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return app_user, inv


@pytest.mark.django_db
def test_veroeffentlichte_rechnung_ist_zu(veroeffentlichte_rechnung):
    """Nach der Veröffentlichung ist der Beleg eingefroren — beide Wege → 422."""
    user, invoice = veroeffentlichte_rechnung
    assert invoice.status == "VEROEFFENTLICHT"

    with pytest.raises(ValueError, match="unveränderlich"):
        beleg_service.add_invoice_line(
            user.id, invoice_id=invoice.id,
            line={"line_type": "PAUSCHALE", "description": "Zu spät",
                  "quantity": "1", "unit_price": "10.00", "tax_code": "DE_19"},
        )
    with pytest.raises(ValueError, match="unveränderlich"):
        beleg_service.remove_last_invoice_line(user.id, invoice_id=invoice.id)
    assert InvoiceLine.objects.filter(invoice_id=invoice.id).count() == 1


@pytest.mark.django_db
def test_storno_wird_nicht_ueber_den_anhaenge_pfad_geaendert(veroeffentlichte_rechnung):
    """Gutschrift/Storno sind kein Editor-Gegenstand — und nie ein 500."""
    user, invoice = veroeffentlichte_rechnung
    storno = beleg_service.create_cancellation(user.id, invoice_id=invoice.id)

    with pytest.raises(ValueError) as anhaengen:
        beleg_service.add_invoice_line(
            user.id, invoice_id=storno.id,
            line={"line_type": "PAUSCHALE", "description": "Nein",
                  "quantity": "1", "unit_price": "1.00", "tax_code": "DE_19"},
        )
    with pytest.raises(ValueError) as entfernen:
        beleg_service.remove_last_invoice_line(user.id, invoice_id=storno.id)
    # Der Storno ist veröffentlicht — das Statustor greift zuerst. Entscheidend ist,
    # dass BEIDE Wege einen Fachfehler (422) liefern und keinen 500.
    for exc in (anhaengen, entfernen):
        assert "unveränderlich" in str(exc.value) or "Storno" in str(exc.value)


# ---------------------------------------------------------------------------
# Schlussrechnung: die Anrechnung schließt den Beleg ab
# ---------------------------------------------------------------------------
# Die Anrechnungspositionen (`advance_invoice_id IS NOT NULL`) sind die Projektion
# der Abschlagsverkettung `invoicing.invoice_advance` und stehen per Konstruktion
# HINTEN. Zwei Löcher lagen hier:
#
# * `remove_last_invoice_line` traf sie — die „letzte Zeile" einer SR IST die
#   Anrechnung. Der Abzug verschwand, die Verkettung blieb: Der Entwurf forderte
#   den bereits gezahlten Abschlag ein zweites Mal.
# * `add_invoice_line` hängte HINTER sie an — der nächste `set_invoice_advances`
#   nummerierte die Anrechnung wieder ab `len(user_lines)` und lief in die UNIQUE
#   (invoice_id, position_number): ein 500 auf einem Schreibpfad.

def _schlussrechnung_mit_anrechnung(app_user):
    """SR über 5.000 € netto mit einem angerechneten Abschlag über 1.000 € (ARBEITSZEIT)."""
    obj, kunde, order = _auftrag_szenario(app_user, "SR-Objekt", bis="IN_AUSFUEHRUNG")

    ar = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="ABSCHLAGSRECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "ARBEITSZEIT", "description": "1. Abschlag",
                "quantity": 1, "unit_price": "1000.00", "tax_code": "DE_19"}],
    )
    _beteiligte(app_user, ar, kunde)
    beleg_service.publish_invoice(app_user.id, invoice_id=ar.id)
    ar.refresh_from_db()

    for to in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)

    sr = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="SCHLUSSRECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "ARBEITSZEIT", "description": "Gesamtleistung",
                "quantity": 1, "unit_price": "5000.00", "tax_code": "DE_19"}],
        advance_invoice_ids=[ar.id],
    )
    sr.refresh_from_db()
    return ar, sr


@pytest.fixture
def schlussrechnung(app_user):
    ar, sr = _schlussrechnung_mit_anrechnung(app_user)
    # Ausgangslage: 5.000 − 1.000 = 4.000 netto → 4.760 brutto.
    assert Decimal(sr.gross_total) == Decimal("4760.00")
    return app_user, ar, sr


@pytest.mark.django_db
def test_anrechnungsposition_laesst_sich_nicht_entfernen(schlussrechnung):
    """Der bewiesene Datenfehler: die letzte Zeile einer SR IST die Anrechnung.

    Ohne dieses Tor verschwand der Abzug aus den Summen (4.760 € → 5.950 €),
    während `invoice_advance` stehenblieb: die SR forderte den bereits gezahlten
    Abschlag ein zweites Mal.
    """
    user, _ar, sr = schlussrechnung
    letzte = InvoiceLine.objects.filter(invoice_id=sr.id).order_by("-position_number")[0]
    assert letzte.advance_invoice_id is not None  # die Anrechnung steht hinten

    with pytest.raises(ValueError, match="Anrechnung"):
        beleg_service.remove_last_invoice_line(user.id, invoice_id=sr.id)

    sr.refresh_from_db()
    assert Decimal(sr.net_total) == Decimal("4000.00")
    assert Decimal(sr.gross_total) == Decimal("4760.00")
    assert InvoiceLine.objects.filter(invoice_id=sr.id).count() == 2


@pytest.mark.django_db
def test_neue_zeile_landet_vor_der_anrechnung(schlussrechnung):
    """Angehängt wird ans Ende der LEISTUNG — der Abzug schließt den Beleg ab."""
    user, ar, sr = schlussrechnung

    beleg_service.add_invoice_line(
        user.id, invoice_id=sr.id,
        line={"line_type": "PAUSCHALE", "description": "Anfahrtspauschale",
              "quantity": "1", "unit": "Stk", "unit_price": "100.00",
              "tax_code": "DE_19"},
    )

    zeilen = list(
        InvoiceLine.objects.filter(invoice_id=sr.id).order_by("position_number")
    )
    assert [l.description for l in zeilen][:2] == ["Gesamtleistung", "Anfahrtspauschale"]
    assert [l.position_number for l in zeilen] == [1, 2, 3]
    assert zeilen[2].advance_invoice_id == ar.id  # die Anrechnung blieb hinten
    # § 35a: der negative Arbeitskostenanteil des Abschlags hat das Umnummerieren
    # unbeschadet überstanden.
    assert zeilen[2].net_amount == Decimal("-1000.00")
    assert zeilen[2].labour_net_amount == Decimal("-1000.00")

    sr.refresh_from_db()
    assert Decimal(sr.net_total) == Decimal("4100.00")   # 5000 + 100 − 1000
    assert Decimal(sr.gross_total) == Decimal("4879.00")  # × 1,19

    # Und der eigentliche Bruch: der nächste Lauf der Anrechnung lief in die UNIQUE
    # (invoice_id, position_number) → 500. Jetzt trägt er.
    beleg_service.set_invoice_advances(
        user.id, invoice_id=sr.id, advance_invoice_ids=[ar.id]
    )
    sr.refresh_from_db()
    assert Decimal(sr.net_total) == Decimal("4100.00")
    assert Decimal(sr.gross_total) == Decimal("4879.00")
    danach = list(
        InvoiceLine.objects.filter(invoice_id=sr.id).order_by("position_number")
    )
    assert [l.position_number for l in danach] == [1, 2, 3]
    assert danach[2].advance_invoice_id == ar.id
    assert danach[2].net_amount == Decimal("-1000.00")
