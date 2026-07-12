"""Service-Tests für Abschlags-/Teil-/Schlussrechnung (Migration 0060).

Geprüft werden die **Tore scharf** — die Veröffentlichungsprüfung ist ein
DEFERRED Constraint-Trigger und feuert unter der pytest-Transaktion nicht von
selbst; `_force_deferred_checks()` (SET CONSTRAINTS ALL IMMEDIATE) wertet sie
innerhalb des Tests aus (Muster aus test_auftrag_service.py).

Fachkern:
- AR/TR dürfen bei einem **laufenden** Auftrag veröffentlicht werden (ab
  FREIGEGEBEN), die SR verlangt weiterhin den kaufmännisch geprüften Auftrag.
- Die SR rechnet die AR/TR **je Steuersatz** an (negative Positionen); der
  Zahlbetrag ist die Differenz, der offene Posten hängt daran.
- Dieselbe AR nie zweimal; keine stornierte AR; kein Storno einer angerechneten AR.
"""
import uuid
from decimal import Decimal

import pytest
from django.db import connection

from db_core.models import Invoice, InvoiceAdvance, InvoiceLine
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _force_deferred_checks():
    """Wertet die DEFERRED Constraint-Trigger sofort aus (Tore werden scharf)."""
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _auftrag(app_user, obj, debtor, *, bis="IN_AUSFUEHRUNG"):
    """Auftrag mit erfüllten Freigabe-Toren, hochgefahren bis `bis`."""
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
            app_user.id, work_order_id=order.id, party_id=debtor.id,
            role=role, is_primary=True,
        )
    kette = ["FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
             "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"]
    for to in kette:
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
        if to == bis:
            break
    order.refresh_from_db()
    return order


@pytest.fixture
def szenario(app_user):
    obj = property_service.create_property(
        app_user.id, name="Schlussrechnungs-Objekt", property_type="WEG",
        street="Baustelle 1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Sieglinde", last_name="Schuldner"
    )
    return {"obj": obj, "kunde": kunde, "user": app_user}


def _beteiligte(app_user, invoice, kunde):
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=invoice.id, party_id=kunde.id,
            role=role, is_primary=True,
        )


def _abschlag(szenario, order, *, betrag="1000.00", typ="ABSCHLAGSRECHNUNG",
              tax="DE_19", publish=True):
    app_user, obj, kunde = szenario["user"], szenario["obj"], szenario["kunde"]
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type=typ, work_order_id=order.id,
        lines=[{"line_type": "PAUSCHALE", "description": "1. Abschlag",
                "quantity": 1, "unit_price": betrag, "tax_code": tax}],
    )
    _beteiligte(app_user, inv, kunde)
    if publish:
        beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
        inv.refresh_from_db()
    return inv


def _schlussrechnung(szenario, order, advances, *, leistung="5000.00", tax="DE_19"):
    app_user, obj, kunde = szenario["user"], szenario["obj"], szenario["kunde"]
    sr = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="SCHLUSSRECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "PAUSCHALE", "description": "Gesamtleistung",
                "quantity": 1, "unit_price": leistung, "tax_code": tax}],
        advance_invoice_ids=[a.id for a in advances],
    )
    _beteiligte(app_user, sr, kunde)
    return sr


# --- Tor: Auftragsstatus ----------------------------------------------------

@pytest.mark.django_db
def test_abschlag_bei_laufendem_auftrag_veroeffentlichbar(szenario):
    """Der Kern: eine Abschlagsrechnung entsteht WÄHREND der Ausführung."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"], bis="IN_AUSFUEHRUNG")
    ar = _abschlag(szenario, order, publish=False)
    beleg_service.publish_invoice(app_user.id, invoice_id=ar.id)
    _force_deferred_checks()  # Tore scharf auswerten
    ar.refresh_from_db()
    assert ar.status == "VEROEFFENTLICHT"
    assert ar.invoice_number.startswith("RE-")


@pytest.mark.django_db
def test_abschlag_ohne_freigegebenen_auftrag_scheitert(szenario):
    """Ohne Freigabe gibt es keine beauftragte Leistung — also keinen Abschlag."""
    app_user = szenario["user"]
    order = auftrag_service.create_work_order(
        app_user.id, property_id=szenario["obj"].id, title="Unfreigegeben"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=szenario["kunde"].id,
        role="INVOICE_DEBTOR", is_primary=True,
    )
    ar = _abschlag(szenario, order, publish=False)
    beleg_service.publish_invoice(app_user.id, invoice_id=ar.id)
    with pytest.raises(Exception, match="freigegebenen Auftrag"):
        _force_deferred_checks()


@pytest.mark.django_db
def test_schlussrechnung_verlangt_gepruefen_auftrag(szenario):
    """Die Schlussrechnung bleibt am strengen B-08-Tor."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"], bis="IN_AUSFUEHRUNG")
    sr = _schlussrechnung(szenario, order, [])
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    with pytest.raises(Exception, match="kaufmännisch geprüft"):
        _force_deferred_checks()


# --- Anrechnung -------------------------------------------------------------

@pytest.mark.django_db
def test_schlussrechnung_rechnet_abschlaege_an(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"], bis="IN_AUSFUEHRUNG")
    ar1 = _abschlag(szenario, order, betrag="1000.00")
    ar2 = _abschlag(szenario, order, betrag="1500.00", typ="TEILRECHNUNG")
    auftrag_service.advance_status(
        app_user.id, work_order_id=order.id, to_status="TECHNISCH_ABGESCHLOSSEN"
    )
    auftrag_service.advance_status(
        app_user.id, work_order_id=order.id, to_status="KAUFMAENNISCH_GEPRUEFT"
    )

    sr = _schlussrechnung(szenario, order, [ar1, ar2], leistung="5000.00")

    # Zahlbetrag = Leistung − Abschläge, brutto wie netto.
    assert sr.net_total == Decimal("2500.00")
    assert sr.tax_total == Decimal("475.00")
    assert sr.gross_total == Decimal("2975.00")

    # Verkettung eingefroren, je Steuersatz.
    links = list(InvoiceAdvance.objects.filter(final_invoice_id=sr.id))
    assert len(links) == 2
    assert {l.advance_invoice_id for l in links} == {ar1.id, ar2.id}
    assert sum(l.gross_amount for l in links) == Decimal("2975.00")

    # Anrechnungspositionen: negativ, hinten, mit Verweis auf den Abschlag.
    abzuege = InvoiceLine.objects.filter(
        invoice_id=sr.id, advance_invoice__isnull=False
    ).order_by("position_number")
    assert [l.net_amount for l in abzuege] == [Decimal("-1000.00"), Decimal("-1500.00")]
    assert all(l.position_number > 1 for l in abzuege)
    assert ar1.invoice_number in abzuege[0].description

    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()
    sr.refresh_from_db()
    assert sr.status == "VEROEFFENTLICHT"
    # Der offene Posten hängt an gross_total (= Zahlbetrag), nicht an der
    # Gesamtleistung — genau das trägt Weg (a) ohne Umbau des Zahlungsspiegels.
    assert sr.gross_total == Decimal("2975.00")


@pytest.mark.django_db
def test_anrechnung_je_steuersatz_cent_genau(szenario):
    """Zwei Steuersätze: der Abzug wird je Satz getrennt geführt (§14 Abs. 5 UStG)."""
    app_user, obj, kunde = szenario["user"], szenario["obj"], szenario["kunde"]
    order = _auftrag(app_user, obj, kunde, bis="IN_AUSFUEHRUNG")
    ar = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="ABSCHLAGSRECHNUNG",
        work_order_id=order.id,
        lines=[
            {"line_type": "MATERIAL", "description": "Material 19 %", "quantity": 3,
             "unit_price": "33.33", "tax_code": "DE_19"},
            {"line_type": "PAUSCHALE", "description": "Ermäßigt 7 %", "quantity": 1,
             "unit_price": "99.99", "tax_code": "DE_7"},
        ],
    )
    _beteiligte(app_user, ar, kunde)
    beleg_service.publish_invoice(app_user.id, invoice_id=ar.id)
    ar.refresh_from_db()
    assert ar.net_total == Decimal("199.98")  # 99.99 + 99.99
    assert ar.tax_total == Decimal("26.00")   # 19.00 (19 %) + 7.00 (7 %)

    for to in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)

    sr = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="SCHLUSSRECHNUNG",
        work_order_id=order.id,
        lines=[
            {"line_type": "MATERIAL", "description": "Material gesamt", "quantity": 1,
             "unit_price": "500.00", "tax_code": "DE_19"},
            {"line_type": "PAUSCHALE", "description": "Ermäßigt gesamt", "quantity": 1,
             "unit_price": "200.00", "tax_code": "DE_7"},
        ],
        advance_invoice_ids=[ar.id],
    )
    # Je Steuersatz eine Anrechnungszeile.
    links = list(
        InvoiceAdvance.objects.filter(final_invoice_id=sr.id).order_by("tax_code")
    )
    assert [(l.tax_code_id, l.net_amount, l.tax_amount) for l in links] == [
        ("DE_19", Decimal("99.99"), Decimal("19.00")),
        ("DE_7", Decimal("99.99"), Decimal("7.00")),
    ]
    # Kopfsummen: je Gruppe gerundet — exakt wie die DB-Summenprüfung.
    assert sr.net_total == Decimal("500.02")   # 400.01 + 100.01
    assert sr.tax_total == Decimal("83.00")    # 76.00 + 7.00
    assert sr.gross_total == Decimal("583.02")
    # Und die Summenprüfung der DB besteht (sonst scheitert die Veröffentlichung).
    _beteiligte(app_user, sr, kunde)
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()


@pytest.mark.django_db
def test_schlussrechnung_ohne_abschlaege_bleibt_normale_rechnung(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    sr = _schlussrechnung(szenario, order, [], leistung="1000.00")
    assert sr.gross_total == Decimal("1190.00")
    assert not InvoiceAdvance.objects.filter(final_invoice_id=sr.id).exists()
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()
    sr.refresh_from_db()
    assert sr.status == "VEROEFFENTLICHT"


@pytest.mark.django_db
def test_anrechnung_uebersteigt_leistung_wird_abgelehnt(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"], bis="IN_AUSFUEHRUNG")
    ar = _abschlag(szenario, order, betrag="5000.00")
    for to in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    with pytest.raises(ValueError, match="übersteigt die abgerechnete Leistung"):
        _schlussrechnung(szenario, order, [ar], leistung="1000.00")


# --- Doppelanrechnung / stornierte Abschläge --------------------------------

@pytest.mark.django_db
def test_dieselbe_ar_zweimal_in_derselben_sr_ist_eine_anrechnung(szenario):
    """Doppelt übergeben = einmal angerechnet (die DB verböte es ohnehin)."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = beleg_service.create_invoice(
        app_user.id, property_id=szenario["obj"].id, invoice_type="SCHLUSSRECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "PAUSCHALE", "description": "Leistung", "quantity": 1,
                "unit_price": "5000.00", "tax_code": "DE_19"}],
        advance_invoice_ids=[ar.id, ar.id],
    )
    assert InvoiceAdvance.objects.filter(final_invoice_id=sr.id).count() == 1
    assert sr.net_total == Decimal("4000.00")


@pytest.mark.django_db
def test_ar_in_zweiter_schlussrechnung_wird_abgelehnt(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr1 = _schlussrechnung(szenario, order, [ar], leistung="5000.00")
    beleg_service.publish_invoice(app_user.id, invoice_id=sr1.id)
    _force_deferred_checks()

    # Zweite Schlussrechnung mit derselben Abschlagsrechnung: schon der Service
    # lehnt ab (die DB-Trigger stünden dahinter).
    with pytest.raises(ValueError, match="bereits in einer veröffentlichten"):
        _schlussrechnung(szenario, order, [ar], leistung="2000.00")


@pytest.mark.django_db
def test_zweiter_sr_entwurf_scheitert_spaetestens_am_db_tor(szenario):
    """Zwei ENTWÜRFE dürfen dieselbe AR vormerken — nur einer darf sie einlösen.

    Der zweite Entwurf entsteht, BEVOR der erste veröffentlicht ist (kein
    Service-Vorwissen). Beim Veröffentlichen schlägt die DB zu.
    """
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr1 = _schlussrechnung(szenario, order, [ar], leistung="5000.00")
    sr2 = _schlussrechnung(szenario, order, [ar], leistung="5000.00")

    beleg_service.publish_invoice(app_user.id, invoice_id=sr1.id)
    # Ab hier sind die Constraint-Trigger IMMEDIATE — das Tor feuert direkt in
    # der Veröffentlichung (und wird zu 422 übersetzt).
    _force_deferred_checks()

    with pytest.raises(ValueError, match="bereits in einer anderen veröffentlichten"):
        beleg_service.publish_invoice(app_user.id, invoice_id=sr2.id)


@pytest.mark.django_db
def test_stornierte_ar_ist_nicht_anrechenbar(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    beleg_service.create_cancellation(app_user.id, invoice_id=ar.id)
    with pytest.raises(ValueError, match="storniert oder gutgeschrieben"):
        _schlussrechnung(szenario, order, [ar], leistung="5000.00")
    assert beleg_service.anrechenbare_abschlaege(order.id) == []


@pytest.mark.django_db
def test_storno_einer_angerechneten_ar_ist_gesperrt(szenario):
    """Der angerechnete Abschlag ist gebunden — Korrektur läuft über die SR."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [ar], leistung="5000.00")
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()
    sr.refresh_from_db()

    with pytest.raises(ValueError, match="angerechnet"):
        beleg_service.create_cancellation(app_user.id, invoice_id=ar.id)
    with pytest.raises(ValueError, match="angerechnet"):
        beleg_service.create_correction(
            app_user.id, invoice_id=ar.id, positions=[1]
        )

    # Nach dem Storno der Schlussrechnung ist der Abschlag wieder frei.
    beleg_service.create_cancellation(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()
    assert [a["id"] for a in beleg_service.anrechenbare_abschlaege(order.id)] == [ar.id]


@pytest.mark.django_db
def test_storno_der_schlussrechnung_dreht_die_anrechnung_um(szenario):
    """Das STORNO der SR trägt die Anrechnung invertiert (positiv) mit."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [ar], leistung="5000.00")
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()

    storno = beleg_service.create_cancellation(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()
    assert storno.gross_total == -Decimal("4760.00")  # (5000 − 1000) × 1,19
    # Der Stornobeleg trägt KEINE Anrechnungspositionen (er ist keine SR).
    assert not InvoiceLine.objects.filter(
        invoice_id=storno.id, advance_invoice__isnull=False
    ).exists()
    assert not InvoiceAdvance.objects.filter(final_invoice_id=storno.id).exists()


# --- Gutschrift-Pfad auf der Schlussrechnung (Review-Befunde) ---------------

@pytest.mark.django_db
def test_teilgutschrift_der_anrechnungsposition_ist_gesperrt(szenario):
    """DER Fehler, den der Review gefunden hat.

    Würde man die (negative) Anrechnungsposition „korrigieren", drehte
    `_negated_lines` ihr Vorzeichen um: es entstünde eine veröffentlichte
    GUTSCHRIFT mit POSITIVEM Betrag — der Abschlag stünde erneut als offener Posten
    und würde gemahnt. Die Position ist deshalb gar nicht korrigierbar, und eine
    Schlussrechnung mit Anrechnung ist überhaupt nicht teilgutschriftfähig.
    """
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [ar], leistung="5000.00")
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()

    anrechnung_pos = InvoiceLine.objects.get(
        invoice_id=sr.id, advance_invoice__isnull=False
    ).position_number
    with pytest.raises(ValueError, match="nicht teilweise gutschreiben"):
        beleg_service.create_correction(
            app_user.id, invoice_id=sr.id, positions=[anrechnung_pos]
        )
    # Auch die reine Leistungsposition ist gesperrt (die Anrechnung bliebe stehen).
    with pytest.raises(ValueError, match="nicht teilweise gutschreiben"):
        beleg_service.create_correction(app_user.id, invoice_id=sr.id, positions=[1])
    assert not Invoice.objects.filter(
        reference_invoice_id=sr.id, invoice_type="GUTSCHRIFT"
    ).exists()


@pytest.mark.django_db
def test_kreditbeleg_mit_positivem_betrag_wird_von_der_db_abgewiesen(szenario):
    """Physischer Riegel: eine Gutschrift ist nie eine Forderung.

    Umgeht den Service-Schutz (Direktzugriff wie ein fehlerhafter Pfad) und
    veröffentlicht eine GUTSCHRIFT mit positivem Betrag — die DB muss zuschlagen.
    """
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")

    gutschrift = Invoice.objects.create(
        id=uuid.uuid4(), property_id=szenario["obj"].id,
        invoice_type="GUTSCHRIFT", reference_invoice_id=ar.id, status="ENTWURF",
        invoice_date=ar.invoice_date,
        net_total=Decimal("1000.00"), tax_total=Decimal("190.00"),
        gross_total=Decimal("1190.00"), version=1,
    )
    InvoiceLine.objects.create(
        id=uuid.uuid4(), invoice_id=gutschrift.id, position_number=1,
        line_type="PAUSCHALE", line_kind="NORMAL", description="Rückdrehung",
        quantity=Decimal("1.000"), unit_price=Decimal("1000.00"),
        tax_code_id="DE_19", tax_rate_percent=Decimal("19.00"),
        net_amount=Decimal("1000.00"),
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=gutschrift.id, party_id=szenario["kunde"].id,
            role=role, is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=gutschrift.id)
    with pytest.raises(Exception, match="keinen positiven Betrag"):
        _force_deferred_checks()


@pytest.mark.django_db
def test_teilgutschrift_der_leistung_ohne_anrechnung_bleibt_moeglich(szenario):
    """Eine Schlussrechnung OHNE Anrechnung bleibt eine gewöhnliche Rechnung."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    sr = _schlussrechnung(szenario, order, [], leistung="5000.00")
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()
    gutschrift = beleg_service.create_correction(
        app_user.id, invoice_id=sr.id, positions=[1]
    )
    assert gutschrift.gross_total == -Decimal("5950.00")


# --- Vergessene Anrechnung --------------------------------------------------

@pytest.mark.django_db
def test_schlussrechnung_darf_keinen_abschlag_uebergehen(szenario):
    """Der teuerste Bedienfehler: Abschläge abwählen und voll abrechnen."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [], leistung="5000.00")  # nichts angerechnet

    with pytest.raises(ValueError, match="übergeht 1 anrechenbare"):
        beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)

    # Nach dem Anrechnen geht es (und die DB bestätigt es).
    beleg_service.set_invoice_advances(
        app_user.id, invoice_id=sr.id, advance_invoice_ids=[ar.id]
    )
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()
    sr.refresh_from_db()
    assert sr.gross_total == Decimal("4760.00")


@pytest.mark.django_db
def test_null_euro_abschlag_blockiert_die_schlussrechnung_nicht(szenario):
    """Randfall: ein veröffentlichter Abschlag über 0,00 EUR.

    Er trägt nichts zum Anrechnen bei (jede Anrechnungszeile braucht einen
    positiven Betrag). Also darf er auch nicht blockieren — sonst wäre die
    Schlussrechnung in der Sackgasse: nicht anrechenbar UND nicht übergehbar.
    """
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    leer = _abschlag(szenario, order, betrag="0.00")
    assert leer.gross_total == Decimal("0.00")

    # Weder in der Auswahlliste …
    assert beleg_service.anrechenbare_abschlaege(order.id) == []
    # … noch als ausdrückliche Anrechnung (klare Meldung statt Sackgasse).
    with pytest.raises(ValueError, match="keinen anrechenbaren Betrag"):
        _schlussrechnung(szenario, order, [leer], leistung="5000.00")

    # … und er blockiert die Veröffentlichung nicht (Service UND DB-Tor).
    sr = _schlussrechnung(szenario, order, [], leistung="5000.00")
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()
    sr.refresh_from_db()
    assert sr.status == "VEROEFFENTLICHT"
    assert sr.gross_total == Decimal("5950.00")


@pytest.mark.django_db
def test_uebergangener_abschlag_scheitert_auch_am_db_tor(szenario):
    """Dasselbe Tor physisch — der Service-Schutz ist nicht die einzige Instanz."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [], leistung="5000.00")
    # Am Service vorbei veröffentlichen (wie ein fehlerhafter Pfad es täte).
    snapshot = {"header": {}, "lines": [], "parties": []}
    Invoice.objects.filter(id=sr.id).update(
        billing_snapshot=snapshot, content_hash="x" * 64, status="VEROEFFENTLICHT"
    )
    with pytest.raises(Exception, match="nicht angerechnet"):
        _force_deferred_checks()


# --- Editor / Auswahl -------------------------------------------------------

@pytest.mark.django_db
def test_anrechenbare_abschlaege_liste(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"], bis="IN_AUSFUEHRUNG")
    ar1 = _abschlag(szenario, order, betrag="1000.00")
    _abschlag(szenario, order, betrag="500.00", publish=False)  # Entwurf → nicht dabei
    liste = beleg_service.anrechenbare_abschlaege(order.id)
    assert [a["id"] for a in liste] == [ar1.id]
    assert liste[0]["gross_total"] == Decimal("1190.00")
    assert liste[0]["vorgemerkt"] is False

    for to in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    sr = _schlussrechnung(szenario, order, [ar1])
    # Für DIESE Schlussrechnung: angehakt. Für eine andere: nur vorgemerkt.
    eigen = beleg_service.anrechenbare_abschlaege(order.id, final_invoice_id=sr.id)
    assert eigen[0]["angerechnet"] is True
    fremd = beleg_service.anrechenbare_abschlaege(order.id)
    assert fremd[0]["vorgemerkt"] is True and fremd[0]["angerechnet"] is False


@pytest.mark.django_db
def test_editor_kann_die_anrechnung_nicht_verlieren(szenario):
    """Ersetzt der Editor alle Positionen, wird die Anrechnung neu angehängt."""
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [ar], leistung="5000.00")

    beleg_service.update_invoice(
        app_user.id, invoice_id=sr.id,
        lines=[{"line_type": "PAUSCHALE", "description": "Leistung neu", "quantity": 1,
                "unit_price": "6000.00", "tax_code": "DE_19"}],
    )
    sr.refresh_from_db()
    assert sr.net_total == Decimal("5000.00")  # 6000 − 1000
    assert sr.gross_total == Decimal("5950.00")
    assert InvoiceLine.objects.filter(
        invoice_id=sr.id, advance_invoice__isnull=False
    ).count() == 1
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()


@pytest.mark.django_db
def test_set_invoice_advances_ersetzt_die_auswahl(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar1 = _abschlag(szenario, order, betrag="1000.00")
    ar2 = _abschlag(szenario, order, betrag="2000.00")
    sr = _schlussrechnung(szenario, order, [ar1], leistung="5000.00")
    assert sr.net_total == Decimal("4000.00")

    beleg_service.set_invoice_advances(
        app_user.id, invoice_id=sr.id, advance_invoice_ids=[ar1.id, ar2.id]
    )
    sr.refresh_from_db()
    assert sr.net_total == Decimal("2000.00")
    assert InvoiceAdvance.objects.filter(final_invoice_id=sr.id).count() == 2

    beleg_service.set_invoice_advances(
        app_user.id, invoice_id=sr.id, advance_invoice_ids=[]
    )
    sr.refresh_from_db()
    assert sr.net_total == Decimal("5000.00")
    assert not InvoiceAdvance.objects.filter(final_invoice_id=sr.id).exists()
    assert not InvoiceLine.objects.filter(
        invoice_id=sr.id, advance_invoice__isnull=False
    ).exists()


@pytest.mark.django_db
def test_anrechnung_nach_veroeffentlichung_unveraenderlich(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [ar], leistung="5000.00")
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    _force_deferred_checks()

    with pytest.raises(ValueError, match="unveränderlich"):
        beleg_service.set_invoice_advances(
            app_user.id, invoice_id=sr.id, advance_invoice_ids=[]
        )


@pytest.mark.django_db
def test_nur_schlussrechnung_kann_anrechnen(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    with pytest.raises(ValueError, match="nur eine Schlussrechnung"):
        beleg_service.create_invoice(
            app_user.id, property_id=szenario["obj"].id, invoice_type="RECHNUNG",
            work_order_id=order.id,
            lines=[{"line_type": "PAUSCHALE", "description": "Leistung", "quantity": 1,
                    "unit_price": "5000.00", "tax_code": "DE_19"}],
            advance_invoice_ids=[ar.id],
        )


@pytest.mark.django_db
def test_abschlag_eines_fremden_auftrags_nicht_anrechenbar(szenario):
    app_user = szenario["user"]
    order_a = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                       bis="KAUFMAENNISCH_GEPRUEFT")
    order_b = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                       bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order_a, betrag="1000.00")
    with pytest.raises(ValueError, match="anderen Auftrag"):
        _schlussrechnung(szenario, order_b, [ar], leistung="5000.00")


@pytest.mark.django_db
def test_fehlende_anrechnungsposition_blockiert_die_veroeffentlichung(szenario):
    """Verkettung und Positionen müssen deckungsgleich sein.

    Simuliert den Ernstfall: die Anrechnungsposition verschwindet (fehlerhafter
    Schreibpfad, Direktzugriff), die Verkettung bleibt. Ohne dieses Tor ginge eine
    Schlussrechnung raus, die den Abschlag NICHT abzieht — der Kunde zahlte doppelt.
    """
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [ar], leistung="5000.00")

    # Anrechnungsposition entfernen und die Kopfsummen „glattziehen" (so, als hätte
    # ein fehlerhafter Pfad die volle Leistung stehen lassen).
    InvoiceLine.objects.filter(invoice_id=sr.id, advance_invoice__isnull=False).delete()
    Invoice.objects.filter(id=sr.id).update(
        net_total=Decimal("5000.00"), tax_total=Decimal("950.00"),
        gross_total=Decimal("5950.00"),
    )
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    with pytest.raises(Exception, match="stimmen nicht mit der Anrechnung überein"):
        _force_deferred_checks()


@pytest.mark.django_db
def test_leistungssummen_gehen_auf(szenario):
    app_user = szenario["user"]
    order = _auftrag(app_user, szenario["obj"], szenario["kunde"],
                     bis="KAUFMAENNISCH_GEPRUEFT")
    ar = _abschlag(szenario, order, betrag="1000.00")
    sr = _schlussrechnung(szenario, order, [ar], leistung="5000.00")
    spiegel = beleg_service.leistungssummen(sr)
    assert spiegel["leistung_net"] == Decimal("5000.00")
    assert spiegel["leistung_gross"] == Decimal("5950.00")
    assert spiegel["anrechnung_gross"] == Decimal("1190.00")
    assert (
        spiegel["leistung_gross"] - spiegel["anrechnung_gross"] == spiegel["zahlbetrag"]
    )
