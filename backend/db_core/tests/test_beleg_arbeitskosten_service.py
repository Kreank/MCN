"""Service-Tests für den § 35a-Arbeitskostenausweis (Migration 0076).

Fachkern: Der Privatkunde darf 20 % der Arbeitskosten (Lohn-, Maschinen-,
Fahrtkosten, max. 1.200 EUR/Jahr) von seiner Steuerschuld abziehen — aber nur,
wenn die Rechnung den Anteil **gesondert ausweist**. Material ist nicht
begünstigt.

Die zentrale Invariante dieser Suite: **unbestimmt ist nicht null.** Wo der
Anteil nicht ableitbar ist (PAUSCHALE, FREMDLEISTUNG, ZUSCHLAG), bleibt er offen,
und der Beleg weist lieber GAR NICHTS aus, als eine geratene Zahl gegenüber dem
Finanzamt zu behaupten.
"""
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from db_core.models import Invoice, InvoiceLine
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


@pytest.fixture
def szenario(app_user):
    obj = property_service.create_property(
        app_user.id, name="Privathaushalt", property_type="EINFAMILIENHAUS",
        street="Eigenheimweg 4", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Petra", last_name="Privat"
    )
    return {"user": app_user, "obj": obj, "kunde": kunde}


def _auftrag(szenario, *, bis="KAUFMAENNISCH_GEPRUEFT"):
    app_user, obj, kunde = szenario["user"], szenario["obj"], szenario["kunde"]
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Heizung erneuern"
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
    order.refresh_from_db()
    return order


def _rechnung(szenario, lines, *, order=None, typ="RECHNUNG", **kwargs):
    app_user, obj = szenario["user"], szenario["obj"]
    return beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type=typ,
        work_order_id=(order.id if order else None), lines=lines, **kwargs,
    )


def _beteiligte(szenario, invoice):
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            szenario["user"].id, invoice_id=invoice.id,
            party_id=szenario["kunde"].id, role=role, is_primary=True,
        )


def _pos(nr, invoice):
    return InvoiceLine.objects.get(invoice_id=invoice.id, position_number=nr)


# --- Ableitung aus der Positionsart -----------------------------------------

@pytest.mark.django_db
def test_anteil_wird_aus_der_positionsart_abgeleitet(szenario):
    """ARBEITSZEIT/FAHRT sind voll begünstigt, MATERIAL gar nicht — die drei
    gemischten Arten bleiben UNBESTIMMT (None), nicht 0."""
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Monteurstunden",
         "quantity": 10, "unit_price": "60.00", "tax_code": "DE_19"},
        {"line_type": "FAHRT", "description": "Anfahrt",
         "quantity": 1, "unit_price": "45.00", "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Heizkörper",
         "quantity": 2, "unit_price": "300.00", "tax_code": "DE_19"},
        {"line_type": "PAUSCHALE", "description": "Bad komplett",
         "quantity": 1, "unit_price": "2000.00", "tax_code": "DE_19"},
        {"line_type": "FREMDLEISTUNG", "description": "Estrich (Subunternehmer)",
         "quantity": 1, "unit_price": "800.00", "tax_code": "DE_19"},
        {"line_type": "ZUSCHLAG", "description": "Wochenendzuschlag",
         "quantity": 1, "unit_price": "100.00", "tax_code": "DE_19"},
    ])
    assert _pos(1, inv).labour_net_amount == Decimal("600.00")   # voll
    assert _pos(2, inv).labour_net_amount == Decimal("45.00")    # voll
    assert _pos(3, inv).labour_net_amount == Decimal("0.00")     # nichts
    # Nicht ableitbar → unbestimmt. Ein Default auf 0 verschenkte dem Kunden
    # still den Bonus; ein Default auf „voll" wäre Steuerverkürzung.
    assert _pos(4, inv).labour_net_amount is None
    assert _pos(5, inv).labour_net_amount is None
    assert _pos(6, inv).labour_net_amount is None


@pytest.mark.django_db
def test_expliziter_anteil_gewinnt_auch_auf_einer_materialzeile(szenario):
    """Verbrauchsmittel (Dicht-, Schmier-, Reinigungsmittel) sind nach § 35a
    begünstigt, obwohl sie Material sind — der Bediener muss sie ansetzen können."""
    inv = _rechnung(szenario, [
        {"line_type": "MATERIAL", "description": "Dichtmittel", "quantity": 1,
         "unit_price": "40.00", "tax_code": "DE_19", "labour_net_amount": "40.00"},
        {"line_type": "PAUSCHALE", "description": "Wartungspauschale", "quantity": 1,
         "unit_price": "500.00", "tax_code": "DE_19", "labour_net_amount": "350.00"},
    ])
    assert _pos(1, inv).labour_net_amount == Decimal("40.00")
    assert _pos(2, inv).labour_net_amount == Decimal("350.00")

    ausweis = beleg_service.arbeitskosten(inv)
    assert ausweis["bestimmbar"] is True
    assert ausweis["net_amount"] == Decimal("390.00")


@pytest.mark.django_db
@pytest.mark.parametrize("anteil", ["600.01", "-1.00"])
def test_anteil_muss_ein_teil_des_positionsbetrags_sein(szenario, anteil):
    """Größer als die Position oder mit falschem Vorzeichen → 422, nicht 500."""
    with pytest.raises(ValueError, match="Teil des Positionsbetrags"):
        _rechnung(szenario, [
            {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 10,
             "unit_price": "60.00", "tax_code": "DE_19", "labour_net_amount": anteil},
        ])


@pytest.mark.django_db
def test_textzeile_traegt_keinen_anteil(szenario):
    with pytest.raises(ValueError, match="keinen Arbeitskostenanteil"):
        _rechnung(szenario, [
            {"line_type": "TEXT", "description": "Hinweis",
             "labour_net_amount": "10.00"},
        ])


@pytest.mark.django_db
def test_die_datenbank_haelt_die_grenze_auch_ohne_service(szenario):
    """Die Regel liegt physisch im CHECK — nicht nur im Service.

    Ohne den DB-CHECK stünde die Aussage „Arbeitskosten sind ein Teil dieser
    Position" nur im Python-Code und wäre über jeden anderen Schreibweg (KI,
    Skript, psql) zu umgehen.
    """
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 1,
         "unit_price": "100.00", "tax_code": "DE_19"},
    ])
    zeile = _pos(1, inv)
    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE invoicing.invoice_line SET labour_net_amount = 100.01 "
                "WHERE id = %s",
                [str(zeile.id)],
            )


# --- Der Ausweis ------------------------------------------------------------

@pytest.mark.django_db
def test_ausweis_rundet_die_steuer_je_steuergruppe(szenario):
    """Zwei Steuersätze: die Steuer auf die Arbeitskosten wird je Gruppe gerundet —
    dieselbe Regel wie bei der Kopfsteuer."""
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden 19 %",
         "quantity": 3, "unit_price": "33.33", "tax_code": "DE_19"},   # 99,99
        {"line_type": "ARBEITSZEIT", "description": "Stunden 7 %",
         "quantity": 3, "unit_price": "33.33", "tax_code": "DE_7"},    # 99,99
        {"line_type": "MATERIAL", "description": "Rohr",
         "quantity": 1, "unit_price": "500.00", "tax_code": "DE_19"},
    ])
    ausweis = beleg_service.arbeitskosten(inv)
    assert ausweis["bestimmbar"] is True
    assert ausweis["net_amount"] == Decimal("199.98")
    # 99,99 × 19 % = 18,9981 → 19,00 ; 99,99 × 7 % = 6,9993 → 7,00
    assert ausweis["tax_amount"] == Decimal("26.00")
    assert ausweis["gross_amount"] == Decimal("225.98")


@pytest.mark.django_db
def test_eine_unbestimmte_position_verhindert_den_ausweis(szenario):
    """Lieber kein Ausweis als ein falscher — und das UI erfährt, WO es klemmt."""
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 10,
         "unit_price": "60.00", "tax_code": "DE_19"},
        {"line_type": "PAUSCHALE", "description": "Bad komplett", "quantity": 1,
         "unit_price": "2000.00", "tax_code": "DE_19"},
    ])
    ausweis = beleg_service.arbeitskosten(inv)
    assert ausweis["bestimmbar"] is False
    assert ausweis["offen"] == [2]
    # Unbekannt ist NICHT 0: sonst stünden 600,00 EUR auf dem Beleg, obwohl in der
    # Pauschale womöglich weitere 1.200 EUR Lohn stecken.
    assert ausweis["net_amount"] is None
    assert ausweis["gross_amount"] is None


@pytest.mark.django_db
def test_reine_lohnrechnung_weist_genau_die_kopfsteuer_aus(szenario):
    """Sind ALLE Positionen Arbeitskosten, muss der ausgewiesene Steuerbetrag exakt
    die Kopfsteuer sein — sonst stünden zwei verschiedene Steuerbeträge auf einem
    Beleg."""
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 7,
         "unit_price": "58.33", "tax_code": "DE_19"},
        {"line_type": "FAHRT", "description": "Anfahrt", "quantity": 1,
         "unit_price": "37.77", "tax_code": "DE_19"},
    ])
    ausweis = beleg_service.arbeitskosten(inv)
    assert ausweis["net_amount"] == inv.net_total
    assert ausweis["tax_amount"] == inv.tax_total
    assert ausweis["gross_amount"] == inv.gross_total


@pytest.mark.django_db
def test_alternativposition_zaehlt_nicht_in_den_ausweis(szenario):
    """Eine Alternativposition wurde nie berechnet — ihre Arbeitskosten auch nicht."""
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 10,
         "unit_price": "60.00", "tax_code": "DE_19"},
        {"line_type": "ARBEITSZEIT", "description": "Variante Premium",
         "line_kind": "ALTERNATIV", "quantity": 20, "unit_price": "60.00",
         "tax_code": "DE_19"},
    ])
    ausweis = beleg_service.arbeitskosten(inv)
    assert ausweis["bestimmbar"] is True
    assert ausweis["net_amount"] == Decimal("600.00")


# --- Veröffentlichung, Snapshot, Storno -------------------------------------

@pytest.mark.django_db
def test_snapshot_friert_den_anteil_ein(szenario):
    """Der Ausweis steht auf dem Kundenbeleg — er muss aus dem Snapshot
    rekonstruierbar sein (B-21/B-30)."""
    order = _auftrag(szenario)
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 10,
         "unit_price": "60.00", "tax_code": "DE_19"},
    ], order=order)
    _beteiligte(szenario, inv)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=inv.id)
    inv.refresh_from_db()

    zeile = inv.billing_snapshot["lines"][0]
    assert zeile["labour_net_amount"] == "600.00"
    assert inv.billing_snapshot["header"]["snapshot_version"] == 3


@pytest.mark.django_db
def test_storno_kehrt_die_arbeitskosten_um(szenario):
    """Die Gutschrift nimmt genau die Arbeitskosten zurück, die die Rechnung
    ausgewiesen hat — sonst bliebe der Steuerbonus stehen, obwohl die Leistung
    storniert ist."""
    order = _auftrag(szenario)
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 10,
         "unit_price": "60.00", "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Rohr", "quantity": 1,
         "unit_price": "400.00", "tax_code": "DE_19"},
    ], order=order)
    _beteiligte(szenario, inv)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=inv.id)

    storno = beleg_service.create_cancellation(szenario["user"].id, invoice_id=inv.id)
    assert _pos(1, storno).labour_net_amount == Decimal("-600.00")
    assert _pos(2, storno).labour_net_amount == Decimal("0.00")

    ausweis = beleg_service.arbeitskosten(storno)
    assert ausweis["bestimmbar"] is True
    assert ausweis["net_amount"] == Decimal("-600.00")
    assert ausweis["gross_amount"] == Decimal("-714.00")


@pytest.mark.django_db
def test_storno_erbt_den_ausweis_schalter(szenario):
    """Das Storno einer B2B-Rechnung (Ausweis aus) trägt keinen § 35a-Block."""
    order = _auftrag(szenario)
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 1,
         "unit_price": "60.00", "tax_code": "DE_19"},
    ], order=order, show_labour_costs=False)
    _beteiligte(szenario, inv)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=inv.id)

    storno = beleg_service.create_cancellation(szenario["user"].id, invoice_id=inv.id)
    assert storno.show_labour_costs is False


# --- Abschlag → Schlussrechnung ---------------------------------------------

def _abschlag(szenario, order, *, lines, publish=True):
    inv = _rechnung(szenario, lines, order=order, typ="ABSCHLAGSRECHNUNG")
    _beteiligte(szenario, inv)
    if publish:
        beleg_service.publish_invoice(szenario["user"].id, invoice_id=inv.id)
        inv.refresh_from_db()
    return inv


@pytest.mark.django_db
def test_schlussrechnung_rechnet_die_arbeitskosten_des_abschlags_heraus(szenario):
    """Die Schlussrechnung weist genau die Arbeitskosten aus, die MIT IHR bezahlt
    werden. Die des Abschlags standen auf dem Abschlagsbeleg und wurden dort schon
    geltend gemacht — ohne den Abzug zählte der Kunde sie zweimal.
    """
    order = _auftrag(szenario)
    abschlag = _abschlag(szenario, order, lines=[
        {"line_type": "ARBEITSZEIT", "description": "Abschlag Lohn", "quantity": 1,
         "unit_price": "1000.00", "tax_code": "DE_19"},
    ])
    sr = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Lohn gesamt", "quantity": 1,
         "unit_price": "3000.00", "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Material gesamt", "quantity": 1,
         "unit_price": "2000.00", "tax_code": "DE_19"},
    ], order=order, typ="SCHLUSSRECHNUNG", advance_invoice_ids=[abschlag.id])

    # Die Anrechnungsposition trägt den NEGATIVEN Arbeitskostenanteil des Abschlags.
    anrechnung = InvoiceLine.objects.get(invoice_id=sr.id, advance_invoice_id=abschlag.id)
    assert anrechnung.net_amount == Decimal("-1000.00")
    assert anrechnung.labour_net_amount == Decimal("-1000.00")

    ausweis = beleg_service.arbeitskosten(sr)
    assert ausweis["bestimmbar"] is True
    # 3.000 Lohn − 1.000 bereits im Abschlag ausgewiesen = 2.000
    assert ausweis["net_amount"] == Decimal("2000.00")
    # …und der Zahlbetrag der SR beträgt 4.000 netto (5.000 − 1.000).
    assert sr.net_total == Decimal("4000.00")


@pytest.mark.django_db
def test_negativer_ausweis_wird_nicht_behauptet(szenario):
    """Trug der Abschlag mehr Lohn, als überhaupt Lohnleistung abgerechnet wird,
    wäre der Ausweis der Schlussrechnung NEGATIV.

    Das entsteht aus einem Erfassungsfehler im (inzwischen unveränderlichen)
    Abschlag — hier ein als ARBEITSZEIT gebuchter Abschlag über 10.000 € bei nur
    5.000 € Lohnleistung. „Arbeitskosten: −5.000 €" darf auf keinem Beleg stehen:
    kein Ausweis, aber ein benannter Grund. Veröffentlichen bleibt möglich (die
    BETRÄGE der Rechnung stimmen ja) — sonst wäre die Schlussrechnung dauerhaft
    unstellbar.
    """
    order = _auftrag(szenario)
    abschlag = _abschlag(szenario, order, lines=[
        {"line_type": "ARBEITSZEIT", "description": "Abschlag (falsch erfasst)",
         "quantity": 1, "unit_price": "10000.00", "tax_code": "DE_19"},
    ])
    sr = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Lohn gesamt", "quantity": 1,
         "unit_price": "5000.00", "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Material gesamt", "quantity": 1,
         "unit_price": "15000.00", "tax_code": "DE_19"},
    ], order=order, typ="SCHLUSSRECHNUNG", advance_invoice_ids=[abschlag.id])

    ausweis = beleg_service.arbeitskosten(sr)
    assert ausweis["bestimmbar"] is False
    assert ausweis["grund"] == beleg_service.LOHN_UNSTIMMIG
    assert ausweis["net_amount"] is None


@pytest.mark.django_db
def test_ausweis_kann_den_rechnungsbetrag_nicht_uebersteigen(szenario):
    """Ein Material-Abschlag zieht keine Arbeitskosten ab — der Lohnanteil kann
    damit größer werden als der Zahlbetrag der Schlussrechnung. „Darin enthalten"
    wäre dann eine falsche Aussage."""
    order = _auftrag(szenario)
    abschlag = _abschlag(szenario, order, lines=[
        {"line_type": "MATERIAL", "description": "Materialvorschuss", "quantity": 1,
         "unit_price": "5000.00", "tax_code": "DE_19"},
    ])
    sr = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Lohn", "quantity": 1,
         "unit_price": "6000.00", "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Material", "quantity": 1,
         "unit_price": "4000.00", "tax_code": "DE_19"},
    ], order=order, typ="SCHLUSSRECHNUNG", advance_invoice_ids=[abschlag.id])

    assert sr.net_total == Decimal("5000.00")   # 10.000 − 5.000
    ausweis = beleg_service.arbeitskosten(sr)
    # 6.000 Lohn > 5.000 Zahlbetrag → nicht ausweisbar.
    assert ausweis["bestimmbar"] is False
    assert ausweis["grund"] == beleg_service.LOHN_UNSTIMMIG


@pytest.mark.django_db
def test_kreditbeleg_darf_negative_arbeitskosten_ausweisen(szenario):
    """Gegenprobe zur Unstimmigkeits-Regel: Auf einer Gutschrift ist der negative
    Ausweis RICHTIG (sie nimmt die Arbeitskosten zurück) — Vorzeichen und Betrag
    passen zum negativen Rechnungsbetrag. Die Regel darf ihn nicht wegfiltern."""
    order = _auftrag(szenario)
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 10,
         "unit_price": "60.00", "tax_code": "DE_19"},
    ], order=order)
    _beteiligte(szenario, inv)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=inv.id)

    storno = beleg_service.create_cancellation(szenario["user"].id, invoice_id=inv.id)
    ausweis = beleg_service.arbeitskosten(storno)
    assert ausweis["bestimmbar"] is True
    assert ausweis["net_amount"] == Decimal("-600.00")


@pytest.mark.django_db
def test_teilgutschrift_traegt_den_anteil_der_gutgeschriebenen_position(szenario):
    """Teilkorrektur: nur die gutgeschriebene Position zählt — und nur deren Anteil."""
    order = _auftrag(szenario)
    inv = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Stunden", "quantity": 10,
         "unit_price": "60.00", "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Rohr", "quantity": 1,
         "unit_price": "400.00", "tax_code": "DE_19"},
    ], order=order)
    _beteiligte(szenario, inv)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=inv.id)

    gutschrift = beleg_service.create_correction(
        szenario["user"].id, invoice_id=inv.id, positions=[1]
    )
    ausweis = beleg_service.arbeitskosten(gutschrift)
    assert ausweis["bestimmbar"] is True
    assert ausweis["net_amount"] == Decimal("-600.00")
    assert gutschrift.net_total == Decimal("-600.00")


@pytest.mark.django_db
def test_anrechnung_traegt_den_anteil_auch_nach_dem_neuaufbau(szenario):
    """`set_invoice_advances` und `update_invoice` bauen die Anrechnungspositionen
    NEU auf — dabei darf der § 35a-Anteil nicht verloren gehen."""
    order = _auftrag(szenario)
    abschlag = _abschlag(szenario, order, lines=[
        {"line_type": "ARBEITSZEIT", "description": "Abschlag Lohn", "quantity": 1,
         "unit_price": "1000.00", "tax_code": "DE_19"},
    ])
    sr = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Lohn gesamt", "quantity": 1,
         "unit_price": "3000.00", "tax_code": "DE_19"},
    ], order=order, typ="SCHLUSSRECHNUNG")

    # (a) Anrechnung nachträglich setzen.
    beleg_service.set_invoice_advances(
        szenario["user"].id, invoice_id=sr.id, advance_invoice_ids=[abschlag.id]
    )
    sr.refresh_from_db()
    anrechnung = InvoiceLine.objects.get(invoice_id=sr.id, advance_invoice_id=abschlag.id)
    assert anrechnung.labour_net_amount == Decimal("-1000.00")
    assert beleg_service.arbeitskosten(sr)["net_amount"] == Decimal("2000.00")

    # (b) Positionen im Editor ersetzen — die Anrechnung wird neu erzeugt.
    beleg_service.update_invoice(
        szenario["user"].id, invoice_id=sr.id,
        lines=[{"line_type": "ARBEITSZEIT", "description": "Lohn gesamt",
                "quantity": 1, "unit_price": "4000.00", "tax_code": "DE_19"}],
    )
    sr.refresh_from_db()
    anrechnung = InvoiceLine.objects.get(invoice_id=sr.id, advance_invoice_id=abschlag.id)
    assert anrechnung.labour_net_amount == Decimal("-1000.00")
    assert beleg_service.arbeitskosten(sr)["net_amount"] == Decimal("3000.00")


@pytest.mark.django_db
def test_unbestimmter_abschlag_macht_auch_die_schlussrechnung_unbestimmt(szenario):
    """Die Unbestimmtheit propagiert: wer eine Pauschal-Abschlagsrechnung ohne
    bestimmten Anteil anrechnet, kann auf der Schlussrechnung keinen ehrlichen
    Ausweis machen."""
    order = _auftrag(szenario)
    abschlag = _abschlag(szenario, order, lines=[
        {"line_type": "PAUSCHALE", "description": "1. Abschlag", "quantity": 1,
         "unit_price": "1000.00", "tax_code": "DE_19"},
    ])
    sr = _rechnung(szenario, [
        {"line_type": "ARBEITSZEIT", "description": "Lohn gesamt", "quantity": 1,
         "unit_price": "3000.00", "tax_code": "DE_19"},
    ], order=order, typ="SCHLUSSRECHNUNG", advance_invoice_ids=[abschlag.id])

    anrechnung = InvoiceLine.objects.get(invoice_id=sr.id, advance_invoice_id=abschlag.id)
    assert anrechnung.labour_net_amount is None

    ausweis = beleg_service.arbeitskosten(sr)
    assert ausweis["bestimmbar"] is False
    assert ausweis["offen"] == [anrechnung.position_number]
