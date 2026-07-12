"""Service-Tests für Zahlungsbedingungen und Skonto je Rechnung (Migration 0058).

Fixiert die Regeln, die sonst nur die DB kennt (und die dort als 500 endeten):
Wertebereiche, Paarigkeit von Skontosatz/-frist, Frist <= Zahlungsziel, keine
Zahlungsbedingungen auf Kreditbelegen. Dazu die beiden abgeleiteten Größen:
die Fälligkeit beim Veröffentlichen (due_date aus dem Zahlungsziel) und der
Skontobetrag (kaufmännisch gerundet).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import Error, transaction

from db_core.models import Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _property(app_user):
    return property_service.create_property(
        app_user.id, name="Skonto-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _gepruefter_auftrag(app_user, obj, debtor):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag zur Rechnung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=debtor.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    return order


@pytest.fixture
def szenario(app_user):
    """Liegenschaft + Partei + kaufmännisch geprüfter Auftrag (publish-fähig)."""
    obj = _property(app_user)
    weg = identity_service.create_person(
        app_user.id, first_name="Wanda", last_name="WEG"
    )
    order = _gepruefter_auftrag(app_user, obj, weg)
    return obj, weg, order


def _rechnung(app_user, obj, order, **kwargs):
    """Rechnung über 1.000,00 netto / 1.190,00 brutto (DE_19)."""
    kwargs.setdefault(
        "lines",
        [{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
          "unit": "Stk", "unit_price": "10.00", "tax_code": "DE_19"}],
    )
    return beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id, **kwargs,
    )


def _beteiligte(app_user, inv, weg):
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id,
            role=role, is_primary=True,
        )


# --- Anlegen ---------------------------------------------------------------

@pytest.mark.django_db
def test_create_invoice_speichert_zahlungsbedingungen(app_user, szenario):
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order,
        payment_term_days=30, discount_percent="2.5", discount_days=10,
    )
    assert inv.payment_term_days == 30
    assert inv.discount_percent == Decimal("2.50")
    assert inv.discount_days == 10


@pytest.mark.django_db
def test_create_invoice_ohne_zahlungsbedingungen_bleibt_leer(app_user, szenario):
    obj, _weg, order = szenario
    inv = _rechnung(app_user, obj, order)
    assert inv.payment_term_days is None
    assert inv.discount_percent is None
    assert inv.discount_days is None


@pytest.mark.django_db
def test_skontosatz_wird_auf_zwei_stellen_quantisiert(app_user, szenario):
    """numeric(5,2): 2,345 % wird kaufmännisch auf 2,35 % gerundet — nicht von der
    DB abgeschnitten, sondern vom Service bewusst gerundet."""
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order,
        payment_term_days=30, discount_percent="2.345", discount_days=10,
    )
    assert inv.discount_percent == Decimal("2.35")


# --- Validierung (422 statt 500) -------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize(
    "kwargs",
    [
        # Wertebereiche
        {"payment_term_days": -1},
        {"payment_term_days": 366},
        {"discount_percent": "0", "discount_days": 5},
        {"discount_percent": "100", "discount_days": 5},
        {"discount_percent": "-2", "discount_days": 5},
        {"discount_percent": "2", "discount_days": -1},
        {"discount_percent": "2", "discount_days": 400},
        # Paarigkeit
        {"discount_percent": "2"},
        {"discount_days": 10},
        # Frist nach Ziel
        {"payment_term_days": 10, "discount_percent": "2", "discount_days": 14},
    ],
)
def test_create_invoice_weist_ungueltige_bedingungen_ab(app_user, szenario, kwargs):
    obj, _weg, order = szenario
    with pytest.raises(ValueError):
        _rechnung(app_user, obj, order, **kwargs)


@pytest.mark.django_db
def test_frist_gleich_ziel_ist_erlaubt(app_user, szenario):
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order,
        payment_term_days=14, discount_percent="2", discount_days=14,
    )
    assert inv.discount_days == 14


@pytest.mark.django_db
def test_skonto_ohne_zahlungsziel_ist_erlaubt(app_user, szenario):
    """Ein Skonto ohne Zahlungsziel bleibt zulässig — dann trägt due_date die
    Fälligkeit (oder gar nichts)."""
    obj, _weg, order = szenario
    inv = _rechnung(app_user, obj, order, discount_percent="3", discount_days=7)
    assert inv.payment_term_days is None and inv.discount_days == 7


# --- Ändern (Sentinel-Muster) ----------------------------------------------

@pytest.mark.django_db
def test_update_invoice_setzt_und_leert_bedingungen(app_user, szenario):
    obj, _weg, order = szenario
    inv = _rechnung(app_user, obj, order)

    beleg_service.update_invoice(
        app_user.id, invoice_id=inv.id,
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    inv.refresh_from_db()
    assert (inv.payment_term_days, inv.discount_percent, inv.discount_days) == (
        30, Decimal("2.00"), 10,
    )

    # Nicht übergebene Felder bleiben unverändert (Sentinel `...`).
    beleg_service.update_invoice(app_user.id, invoice_id=inv.id, invoice_date=None)
    inv.refresh_from_db()
    assert inv.discount_percent == Decimal("2.00")

    # Bewusstes Leeren (None) entfernt das Skonto.
    beleg_service.update_invoice(
        app_user.id, invoice_id=inv.id, discount_percent=None, discount_days=None,
    )
    inv.refresh_from_db()
    assert inv.discount_percent is None and inv.discount_days is None
    assert inv.payment_term_days == 30


@pytest.mark.django_db
def test_update_invoice_prueft_den_ergebniszustand_nicht_nur_den_payload(
    app_user, szenario
):
    """Nur die Frist zu leeren, während der Satz stehen bleibt, bricht die
    Paarigkeit — der Service prüft das Ergebnis, nicht den Payload."""
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order,
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    with pytest.raises(ValueError, match="gemeinsam"):
        beleg_service.update_invoice(
            app_user.id, invoice_id=inv.id, discount_days=None
        )
    inv.refresh_from_db()
    assert inv.discount_days == 10  # unverändert


@pytest.mark.django_db
def test_update_invoice_erlaubt_teilaenderung_gegen_bestand(app_user, szenario):
    """Nur den Satz ändern, während die Frist am Beleg steht: gültig."""
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order,
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    beleg_service.update_invoice(
        app_user.id, invoice_id=inv.id, discount_percent="3"
    )
    inv.refresh_from_db()
    assert inv.discount_percent == Decimal("3.00") and inv.discount_days == 10


@pytest.mark.django_db
def test_update_invoice_neue_frist_nach_ziel_wird_abgewiesen(app_user, szenario):
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order,
        payment_term_days=14, discount_percent="2", discount_days=7,
    )
    with pytest.raises(ValueError, match="Skontofrist"):
        beleg_service.update_invoice(
            app_user.id, invoice_id=inv.id, discount_days=20
        )


# --- Kreditbelege ----------------------------------------------------------

@pytest.mark.django_db
def test_kreditbeleg_uebernimmt_keine_zahlungsbedingungen(app_user, szenario):
    """Storno einer Rechnung mit Skonto: der Folgebeleg trägt keine Bedingungen —
    eine Gutschrift fordert kein Geld, es gibt nichts zu skontieren."""
    obj, weg, order = szenario
    inv = _rechnung(
        app_user, obj, order,
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    _beteiligte(app_user, inv, weg)
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)

    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    assert storno.invoice_type == "STORNO"
    assert storno.payment_term_days is None
    assert storno.discount_percent is None
    assert storno.discount_days is None


@pytest.mark.django_db
def test_db_verbietet_zahlungsbedingungen_auf_kreditbeleg(app_user, szenario):
    """Die Regel steht physisch in der DB, nicht nur im Service."""
    obj, weg, order = szenario
    inv = _rechnung(app_user, obj, order)
    _beteiligte(app_user, inv, weg)
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    with pytest.raises(Error):
        with transaction.atomic():
            Invoice.objects.filter(id=storno.id).update(payment_term_days=30)


# --- Fälligkeit ableiten (Veröffentlichung) --------------------------------

@pytest.mark.django_db
def test_publish_leitet_due_date_aus_zahlungsziel_ab(app_user, szenario):
    obj, weg, order = szenario
    belegdatum = date(2026, 7, 1)
    inv = _rechnung(
        app_user, obj, order, invoice_date=belegdatum, payment_term_days=30,
    )
    _beteiligte(app_user, inv, weg)
    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    assert published.due_date == belegdatum + timedelta(days=30)
    # Der Snapshot friert die abgeleitete Fälligkeit mit ein.
    assert published.billing_snapshot["header"]["due_date"] == "2026-07-31"


@pytest.mark.django_db
def test_publish_ohne_belegdatum_nutzt_heute_als_basis(app_user, szenario):
    """Ohne Belegdatum setzt der DB-Trigger das heutige Datum — der Service muss
    dieselbe Basis benutzen, sonst verletzt die Fälligkeit den CHECK
    (due_date >= invoice_date) oder driftet davon ab."""
    obj, weg, order = szenario
    inv = _rechnung(app_user, obj, order, payment_term_days=14)
    _beteiligte(app_user, inv, weg)
    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    assert published.invoice_date is not None
    assert published.due_date == published.invoice_date + timedelta(days=14)


@pytest.mark.django_db
def test_publish_laesst_gesetztes_due_date_unangetastet(app_user, szenario):
    obj, weg, order = szenario
    inv = _rechnung(
        app_user, obj, order,
        invoice_date=date(2026, 7, 1), due_date=date(2026, 7, 10),
        payment_term_days=30,
    )
    _beteiligte(app_user, inv, weg)
    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    assert published.due_date == date(2026, 7, 10)


@pytest.mark.django_db
def test_publish_ohne_zahlungsziel_laesst_due_date_leer(app_user, szenario):
    """Ohne Zahlungsziel bleibt die Fälligkeit leer — sie wird nicht geraten."""
    obj, weg, order = szenario
    inv = _rechnung(app_user, obj, order, invoice_date=date(2026, 7, 1))
    _beteiligte(app_user, inv, weg)
    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    assert published.due_date is None


@pytest.mark.django_db
def test_snapshot_enthaelt_die_zahlungsbedingungen(app_user, szenario):
    """GoBD: die Bedingungen stehen auf dem Beleg, also gehören sie in den
    gehashten Snapshot."""
    obj, weg, order = szenario
    inv = _rechnung(
        app_user, obj, order, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    _beteiligte(app_user, inv, weg)
    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    header = published.billing_snapshot["header"]
    assert header["payment_term_days"] == 30
    assert header["discount_percent"] == "2.00"
    assert header["discount_days"] == 10
    assert published.content_hash and len(published.content_hash) == 64


# --- Abgeleitete Skonto-Werte ----------------------------------------------

@pytest.mark.django_db
def test_zahlungsbedingungen_rechnet_skonto_kaufmaennisch(app_user, szenario):
    """1.190,00 brutto, 2,5 % Skonto → 29,75 EUR; Zahlbetrag 1.160,25 EUR."""
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2.5", discount_days=10,
    )
    zb = beleg_service.zahlungsbedingungen(inv)
    assert zb["skonto_bis"] == date(2026, 7, 11)
    assert zb["skonto_betrag"] == Decimal("29.75")
    assert zb["skonto_zahlbetrag"] == Decimal("1160.25")
    assert zb["zahlbar_bis"] is None  # noch nicht veröffentlicht → kein due_date


@pytest.mark.django_db
def test_zahlungsbedingungen_rundet_halbe_cent_auf(app_user, szenario):
    """119,00 brutto, 3,33 % → 3,9627 EUR → 3,96 EUR; und 1,5 % von 119,00 =
    1,785 EUR → 1,79 EUR (ROUND_HALF_UP, nicht bankers rounding)."""
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order, invoice_date=date(2026, 7, 1),
        discount_percent="1.5", discount_days=7,
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
                "unit": "Stk", "unit_price": "10.00", "tax_code": "DE_19"}],
    )
    assert inv.gross_total == Decimal("119.00")
    zb = beleg_service.zahlungsbedingungen(inv)
    assert zb["skonto_betrag"] == Decimal("1.79")
    assert zb["skonto_zahlbetrag"] == Decimal("117.21")


@pytest.mark.django_db
def test_zahlungsbedingungen_ohne_skonto_ist_none(app_user, szenario):
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order, invoice_date=date(2026, 7, 1), payment_term_days=30
    )
    assert beleg_service.zahlungsbedingungen(inv) is None


@pytest.mark.django_db
def test_zahlungsbedingungen_ohne_belegdatum_ist_none(app_user, szenario):
    """Ohne Belegdatum gibt es kein Fristende — lieber nichts als ein geratenes."""
    obj, _weg, order = szenario
    inv = _rechnung(app_user, obj, order, discount_percent="2", discount_days=10)
    assert beleg_service.zahlungsbedingungen(inv) is None


# --- Skontofrist gegen die tatsächliche Fälligkeit (Review-Befund 1) --------

@pytest.mark.django_db
def test_frist_nach_manueller_faelligkeit_wird_abgewiesen(app_user, szenario):
    """`payment_term_days` ist nicht die maßgebliche Schranke: `due_date` kann von
    Hand früher gesetzt sein. Dann darf die Skontofrist nicht dahinter enden —
    sonst druckte der Beleg „Skonto bis 11.07., sonst netto bis 05.07."."""
    obj, _weg, order = szenario
    with pytest.raises(ValueError, match="nach der Fälligkeit"):
        _rechnung(
            app_user, obj, order,
            invoice_date=date(2026, 7, 1), due_date=date(2026, 7, 5),
            payment_term_days=30, discount_percent="2", discount_days=10,
        )


@pytest.mark.django_db
def test_frist_ohne_zahlungsziel_gegen_faelligkeit_geprueft(app_user, szenario):
    """Auch ohne Zahlungsziel greift die Prüfung — dort gab es vorher gar keine."""
    obj, _weg, order = szenario
    with pytest.raises(ValueError, match="nach der Fälligkeit"):
        _rechnung(
            app_user, obj, order,
            invoice_date=date(2026, 7, 1), due_date=date(2026, 7, 5),
            discount_percent="2", discount_days=10,
        )


@pytest.mark.django_db
def test_update_darf_faelligkeit_nicht_vor_die_skontofrist_ziehen(app_user, szenario):
    """Ein reiner Datumswechsel kann die Frist hinter die Fälligkeit schieben."""
    obj, _weg, order = szenario
    inv = _rechnung(
        app_user, obj, order, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    with pytest.raises(ValueError, match="nach der Fälligkeit"):
        beleg_service.update_invoice(
            app_user.id, invoice_id=inv.id, due_date=date(2026, 7, 5)
        )
    inv.refresh_from_db()
    assert inv.due_date is None


@pytest.mark.django_db
def test_publish_weist_frist_nach_faelligkeit_ab(app_user, szenario):
    """Letzte Instanz: erst beim Veröffentlichen stehen Belegdatum und Fälligkeit
    endgültig fest. Der Beleg entsteht hier über einen direkten Spaltenschreib-
    vorgang (am Service vorbei), um genau diese Schutzschicht zu prüfen."""
    obj, weg, order = szenario
    inv = _rechnung(
        app_user, obj, order, invoice_date=date(2026, 7, 1),
        payment_term_days=30, discount_percent="2", discount_days=10,
    )
    _beteiligte(app_user, inv, weg)
    # due_date nachträglich vorziehen, ohne den Service (simuliert Altdaten/Import).
    Invoice.objects.filter(id=inv.id).update(due_date=date(2026, 7, 5))
    with pytest.raises(ValueError, match="nach der Fälligkeit"):
        beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    assert inv.status == "ENTWURF"  # nicht halb veröffentlicht


@pytest.mark.django_db
def test_frist_genau_auf_der_faelligkeit_ist_erlaubt(app_user, szenario):
    obj, weg, order = szenario
    inv = _rechnung(
        app_user, obj, order, invoice_date=date(2026, 7, 1),
        due_date=date(2026, 7, 11), discount_percent="2", discount_days=10,
    )
    _beteiligte(app_user, inv, weg)
    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    assert published.status == "VEROEFFENTLICHT"


# --- Belegdatum immer im Snapshot (Review-Befund 2) ------------------------

@pytest.mark.django_db
def test_publish_schreibt_belegdatum_immer_in_den_snapshot(app_user, szenario):
    """Ohne Belegdatum UND ohne Zahlungsziel setzte bisher nur der DB-Trigger das
    Datum — der gehashte Snapshot trug `null`, während Zeile und PDF (und die
    daraus abgeleitete Skontofrist) das heutige Datum zeigten. Der Snapshot muss
    den ausgelieferten Beleg rekonstruieren (B-21/B-30)."""
    obj, weg, order = szenario
    inv = _rechnung(app_user, obj, order)  # kein invoice_date, kein Zahlungsziel
    _beteiligte(app_user, inv, weg)
    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    assert published.invoice_date is not None
    assert (
        published.billing_snapshot["header"]["invoice_date"]
        == published.invoice_date.isoformat()
    )


@pytest.mark.django_db
def test_publish_mit_skonto_ohne_belegdatum_ist_snapshot_konsistent(app_user, szenario):
    """Dasselbe mit Skonto: die im PDF gedruckte Frist leitet sich aus genau dem
    Datum ab, das im Snapshot steht."""
    obj, weg, order = szenario
    inv = _rechnung(
        app_user, obj, order, discount_percent="2", discount_days=10,
    )
    _beteiligte(app_user, inv, weg)
    published = beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    header = published.billing_snapshot["header"]
    assert header["invoice_date"] == published.invoice_date.isoformat()
    zb = beleg_service.zahlungsbedingungen(published)
    assert zb["skonto_bis"] == published.invoice_date + timedelta(days=10)
